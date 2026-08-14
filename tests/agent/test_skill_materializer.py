"""Behavior tests for deterministic selected-skill materialization."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent import skill_materializer
from agent.skill_materializer import (
    MAX_REFERENCE_BYTES,
    SkillMaterializationError,
    compile_attempt_profile,
    materialize_skill,
    validate_transport_transition,
    verify_expected_profile,
)


def _skill(
    tmp_path: Path,
    *,
    reference_sha: str | None = None,
    reference_path: str = "references/a.md",
) -> tuple[Path, bytes]:
    root = tmp_path / "sample"
    (root / "references").mkdir(parents=True)
    reference = b"required reference\n"
    (root / "references" / "a.md").write_bytes(reference)
    sha = reference_sha or hashlib.sha256(reference).hexdigest()
    (root / "SKILL.md").write_text(
        "---\n"
        "name: sample\n"
        "version: '1.2.3'\n"
        "description: Sample skill.\n"
        "metadata:\n"
        "  hermes:\n"
        "    required_references:\n"
        f"      - path: {reference_path}\n"
        f"        sha256: {sha}\n"
        "---\n\n"
        "# Sample\n\nApply the exact contract.\n",
        encoding="utf-8",
    )
    return root / "SKILL.md", reference


def _materialize(
    skill_md: Path,
    *,
    mode: str = "exec_compiled_materialization",
    source_path: str = "skills/sample/SKILL.md",
):
    return materialize_skill(
        skill_md,
        qualified_skill_id="example-plugin:sample",
        trigger_mode="task_specific",
        source_trigger="task_match",
        source_binding={
            "source_kind": "canonical_source",
            "source_manifest_identity": "example.v1",
            "source_head": "a" * 40,
            "path": source_path,
        },
        mode=mode,
        plugin_binding=None,
    )


def test_materializes_full_exact_bytes_and_only_declared_references(
    tmp_path: Path,
) -> None:
    skill_md, reference = _skill(tmp_path)
    undeclared = skill_md.parent / "references" / "unused.md"
    undeclared.write_text("must not be materialized", encoding="utf-8")

    first = _materialize(skill_md)
    second = _materialize(skill_md)

    assert first.skill.content == skill_md.read_bytes()
    assert [item.path for item in first.required_references] == ["references/a.md"]
    assert first.required_references[0].content == reference
    assert first.prompt_bytes() == second.prompt_bytes()
    assert first.receipt == second.receipt
    assert first.receipt["coverage"] == {
        "full_skill_read": True,
        "required_reference_count": 1,
        "total_bytes": len(skill_md.read_bytes()) + len(reference),
        "undeclared_references_materialized": False,
    }
    serialized = str(first.receipt)
    assert "Apply the exact contract" not in serialized
    assert "must not be materialized" not in serialized


@pytest.mark.parametrize("failure", ["escape", "digest", "missing", "symlink"])
def test_required_reference_safety_fails_closed(tmp_path: Path, failure: str) -> None:
    if failure == "escape":
        skill_md, _ = _skill(tmp_path, reference_path="../outside.md")
    elif failure == "digest":
        skill_md, _ = _skill(tmp_path, reference_sha="0" * 64)
    else:
        skill_md, _ = _skill(tmp_path)
        reference = skill_md.parent / "references" / "a.md"
        if failure == "missing":
            reference.unlink()
        else:
            target = tmp_path / "outside.md"
            target.write_text("outside", encoding="utf-8")
            reference.unlink()
            reference.symlink_to(target)

    with pytest.raises(SkillMaterializationError):
        _materialize(skill_md)


def test_reference_size_limit_is_enforced_before_materialization(tmp_path: Path) -> None:
    skill_md, _ = _skill(tmp_path)
    reference = skill_md.parent / "references" / "a.md"
    reference.write_bytes(b"x" * (MAX_REFERENCE_BYTES + 1))
    content = skill_md.read_text(encoding="utf-8")
    content = content.replace(
        hashlib.sha256(b"required reference\n").hexdigest(),
        hashlib.sha256(reference.read_bytes()).hexdigest(),
    )
    skill_md.write_text(content, encoding="utf-8")

    with pytest.raises(SkillMaterializationError, match="byte limit"):
        _materialize(skill_md)


@pytest.mark.parametrize("source_path", ["../sample/SKILL.md", "skills/foreign/SKILL.md"])
def test_source_binding_path_must_match_actual_selected_skill(
    tmp_path: Path, source_path: str
) -> None:
    skill_md, _ = _skill(tmp_path)

    with pytest.raises(SkillMaterializationError, match="source binding path|source path drift"):
        _materialize(skill_md, source_path=source_path)


@pytest.mark.parametrize("swap_kind", ["same_size_file", "symlink"])
def test_descriptor_read_rejects_deterministic_path_swap(
    tmp_path: Path, monkeypatch, swap_kind: str
) -> None:
    skill_md, _ = _skill(tmp_path)
    original = skill_md.read_bytes()
    original_read = skill_materializer.os.read
    swapped = False

    def swapping_read(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        if not swapped:
            swapped = True
            replacement = skill_md.with_name("replacement")
            if swap_kind == "same_size_file":
                replacement.write_bytes(b"x" * len(original))
            else:
                outside = tmp_path / "outside.md"
                outside.write_bytes(original)
                replacement.symlink_to(outside)
            replacement.replace(skill_md)
        return original_read(descriptor, size)

    monkeypatch.setattr(skill_materializer.os, "read", swapping_read)

    with pytest.raises(SkillMaterializationError, match="changed while being read"):
        _materialize(skill_md)


def test_same_qualified_name_with_different_content_fails_expected_profile(
    tmp_path: Path,
) -> None:
    skill_md, _ = _skill(tmp_path)
    expected = _materialize(skill_md).receipt
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\nChanged operation.\n",
        encoding="utf-8",
    )
    changed = _materialize(skill_md)

    with pytest.raises(
        SkillMaterializationError, match="same qualified skill has different"
    ):
        verify_expected_profile(changed, expected)


def test_attempt_profile_records_conditional_omission_without_selecting_it(
    tmp_path: Path,
) -> None:
    skill_md, _ = _skill(tmp_path)
    selected = _materialize(skill_md)
    profile = compile_attempt_profile(
        [selected],
        task_skill_chain_reason="The task has one fixed implementation route.",
        omitted_conditionals=[
            {
                "qualified_skill_id": "example-plugin:best-evaluate",
                "reason": "No unresolved comparison exists.",
            }
        ],
        mode="exec_compiled_materialization",
        prompt=b"bounded prompt",
    )

    assert profile["selected_skill_ids"] == ["example-plugin:sample"]
    assert profile["omitted_conditionals"][0]["qualified_skill_id"].endswith(
        ":best-evaluate"
    )
    assert profile["prompt_sha256"] == hashlib.sha256(b"bounded prompt").hexdigest()


def test_transport_switch_requires_terminal_attempt_and_new_owner_epoch() -> None:
    previous = {
        "stable_task_lineage_id": "lineage-1",
        "attempt_id": "attempt-1",
        "owner_epoch": 1,
        "transport": "plugin",
        "terminal": True,
    }
    current = {
        "stable_task_lineage_id": "lineage-1",
        "attempt_id": "attempt-2",
        "owner_epoch": 2,
        "transport": "exec",
        "owner_transfer_readback": True,
        "simultaneous_writer_count": 1,
    }
    validate_transport_transition(previous, current)

    for field, value in (
        ("attempt_id", "attempt-1"),
        ("owner_epoch", 1),
        ("owner_transfer_readback", False),
        ("simultaneous_writer_count", 2),
    ):
        invalid = {**current, field: value}
        with pytest.raises(SkillMaterializationError):
            validate_transport_transition(previous, invalid)

    with pytest.raises(SkillMaterializationError, match="terminal prior attempt"):
        validate_transport_transition({**previous, "terminal": False}, current)
