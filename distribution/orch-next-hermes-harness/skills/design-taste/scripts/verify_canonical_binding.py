#!/usr/bin/env python3
"""Verify the compact design-taste package against its admitted rich source."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


EXPECTED_CONTRACT = "design-taste-canonical-binding.v1"
EXPECTED_NAMESPACE = "orch-next-hermes-harness:design-taste"
EXPECTED_COLLISION_POLICY = "fail_closed"
EXPECTED_SOURCE_IDENTITY = "claude:design-taste"
EXPECTED_SOURCE_VERSION = "0.4.0"
EXPECTED_SOURCE_SKILL_SHA256 = (
    "c00e3e0af5c907b2016ae854afa31c35f7324d93e62f9e51e568850bd983bfaf"
)
EXPECTED_REFERENCES = [
    {
        "path": "references/anti-generic-rules.md",
        "sha256": "c6e11d852a86ca474a7ec4658bf01b6a6cdc22ab0a153387024e03d9145abe34",
    },
    {
        "path": "references/japanese-typography.md",
        "sha256": "8a3d8e169d4641b71db380600febec9b5c81aeb488461dd1372c00182a0edf1b",
    },
    {
        "path": "references/reference-site-teardowns.md",
        "sha256": "4273dea046f3804a020189023959656fa5094429b0c7b45de3b7d0d4389164e6",
    },
    {
        "path": "references/llmo-aio-evidence.md",
        "sha256": "cea8642ab722d123e20d0ecf7b41ef13de942a2f1da5dfb2160a2cbe9e330d56",
    },
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return {}
    parsed = yaml.safe_load(text[4:closing])
    return parsed if isinstance(parsed, dict) else {}


def _is_contained_regular_file(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return path.is_file() and not path.is_symlink()


def verify_binding(
    skill_root: Path,
    *,
    selected_namespace: str,
    canonical_root: Path | None = None,
) -> dict[str, Any]:
    """Return a sanitized admission result; no source content is emitted."""
    violations: list[str] = []
    try:
        metadata = _frontmatter(skill_root / "SKILL.md")["metadata"]["hermes"]
        binding = metadata["canonical_binding"]
    except (KeyError, OSError, TypeError, UnicodeError, yaml.YAMLError):
        binding = {}
        violations.append("binding_metadata_missing_or_invalid")

    expected_pairs = {
        "contract": EXPECTED_CONTRACT,
        "selection_namespace": EXPECTED_NAMESPACE,
        "collision_policy": EXPECTED_COLLISION_POLICY,
        "source_identity": EXPECTED_SOURCE_IDENTITY,
        "source_version": EXPECTED_SOURCE_VERSION,
        "source_skill_sha256": EXPECTED_SOURCE_SKILL_SHA256,
    }
    for field, expected in expected_pairs.items():
        if binding.get(field) != expected:
            violations.append(f"{field}_mismatch")
    if selected_namespace != binding.get("selection_namespace"):
        violations.append("selected_namespace_mismatch")

    reference_rows = binding.get("required_references", [])
    if reference_rows != EXPECTED_REFERENCES:
        violations.append("required_reference_set_mismatch")

    for row in EXPECTED_REFERENCES:
        relative_path = row["path"]
        expected_digest = row["sha256"]
        path = skill_root / relative_path
        if not _is_contained_regular_file(skill_root, path):
            violations.append(f"required_reference_missing:{relative_path}")
        elif _sha256(path) != expected_digest:
            violations.append(f"required_reference_digest_mismatch:{relative_path}")

    canonical_compared = canonical_root is not None
    if canonical_root is not None:
        canonical_skill = canonical_root / "SKILL.md"
        if not _is_contained_regular_file(canonical_root, canonical_skill):
            violations.append("canonical_skill_missing")
        elif _sha256(canonical_skill) != EXPECTED_SOURCE_SKILL_SHA256:
            violations.append("canonical_skill_digest_mismatch")
        for row in EXPECTED_REFERENCES:
            canonical_reference = canonical_root / row["path"]
            if not _is_contained_regular_file(canonical_root, canonical_reference):
                violations.append(f"canonical_reference_missing:{row['path']}")
            elif _sha256(canonical_reference) != row["sha256"]:
                violations.append(f"canonical_reference_digest_mismatch:{row['path']}")

    admitted = not violations
    return {
        "admitted": admitted,
        "code": "canonical_binding_verified"
        if admitted
        else "canonical_binding_mismatch",
        "launch": admitted,
        "selected_namespace": selected_namespace,
        "canonical_compared": canonical_compared,
        "reference_count": len(EXPECTED_REFERENCES),
        "violations": sorted(set(violations)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected-namespace", required=True)
    parser.add_argument("--canonical-root", type=Path)
    args = parser.parse_args()
    skill_root = Path(__file__).resolve().parents[1]
    canonical_root = args.canonical_root
    if canonical_root is None:
        candidate = Path.home() / ".claude" / "skills" / "design-taste"
        if candidate.is_dir():
            canonical_root = candidate
    result = verify_binding(
        skill_root,
        selected_namespace=args.selected_namespace,
        canonical_root=canonical_root,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
