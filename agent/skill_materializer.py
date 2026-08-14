"""Deterministically materialize one selected Hermes skill.

This module is intentionally a library, not a model tool or provider adapter.
Callers resolve the selected skill through their existing catalog/collision
rules, then pass the exact ``SKILL.md`` path here.  The returned in-memory
payload carries the full selected bytes and only frontmatter-declared required
references; the receipt contains hashes and identities, never prompt or
provider payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence

from agent.skill_utils import parse_frontmatter


SCHEMA = "hermes-skill-materialization.v1"
COMPILER_IDENTITY = "hermes-agent.skill-materializer"
COMPILER_VERSION = "1"
MATERIALIZATION_MODES = frozenset(
    {"plugin_namespaced_resolve", "exec_compiled_materialization"}
)
TRIGGER_MODES = frozenset(
    {
        "common_preflight",
        "unresolved_comparison",
        "repeated_miss_nonfire",
        "task_specific",
    }
)
MAX_SKILL_BYTES = 1_048_576
MAX_REFERENCE_BYTES = 1_048_576
MAX_MATERIALIZED_BYTES = 4_194_304
_QUALIFIED_SKILL_RE = re.compile(
    r"^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SkillMaterializationError(RuntimeError):
    """The selected skill cannot be materialized without ambiguity or drift."""


@dataclass(frozen=True)
class MaterializedFile:
    path: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class MaterializedSkill:
    """Exact execution bytes plus a sanitized deterministic receipt."""

    qualified_skill_id: str
    skill: MaterializedFile
    required_references: tuple[MaterializedFile, ...]
    receipt: dict[str, Any]

    def prompt_bytes(self) -> bytes:
        """Return one unambiguous length-prefixed full-content packet."""

        return _materialization_stream((self.skill, *self.required_references))


@dataclass(frozen=True)
class _SecureRead:
    file: MaterializedFile
    identity: tuple[int, int, int, int, int, int]


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def compiler_digest() -> str:
    """Bind receipts to the exact compiler source bytes."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def compiler_profile() -> dict[str, str]:
    record = {
        "identity": COMPILER_IDENTITY,
        "version": COMPILER_VERSION,
        "digest": compiler_digest(),
    }
    record["profile_digest"] = _digest_json(record)
    return record


def _validate_relative_path(value: object, *, label: str) -> PurePosixPath:
    if type(value) is not str or not value:
        raise SkillMaterializationError(f"{label} must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or "\\" in value
    ):
        raise SkillMaterializationError(f"{label} escapes the selected skill root")
    return path


def _stat_identity(observed: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        observed.st_dev,
        observed.st_ino,
        stat.S_IFMT(observed.st_mode),
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _same_open_identity(
    expected: tuple[int, int, int, int, int, int], observed: os.stat_result
) -> bool:
    return expected == _stat_identity(observed)


def _open_absolute_directory_nofollow(path: Path, *, label: str) -> int:
    """Open every absolute path component without following symbolic links."""

    lexical = path.expanduser().absolute()
    descriptor = os.open(os.sep, _DIRECTORY_OPEN_FLAGS)
    try:
        for part in lexical.parts[1:]:
            if part in {".", ".."}:
                raise SkillMaterializationError(f"{label} path is not canonical")
            next_descriptor = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        observed = os.fstat(descriptor)
        if not stat.S_ISDIR(observed.st_mode):
            raise SkillMaterializationError(f"{label} is not a directory")
        return descriptor
    except (OSError, SkillMaterializationError) as exc:
        os.close(descriptor)
        if isinstance(exc, SkillMaterializationError):
            raise
        raise SkillMaterializationError(
            f"{label} is unavailable or contains a symlink"
        ) from exc


def _canonical_skill_root(skill_md: Path) -> tuple[Path, int]:
    lexical = skill_md.expanduser().absolute()
    if lexical.name != "SKILL.md":
        raise SkillMaterializationError("selected skill path must name SKILL.md")
    root = lexical.parent
    try:
        root_descriptor = _open_absolute_directory_nofollow(
            root, label="selected skill root"
        )
        observed = os.stat(
            "SKILL.md", dir_fd=root_descriptor, follow_symlinks=False
        )
    except OSError as exc:
        try:
            os.close(root_descriptor)
        except UnboundLocalError:
            pass
        raise SkillMaterializationError("selected SKILL.md is unavailable") from exc
    if not stat.S_ISREG(observed.st_mode):
        os.close(root_descriptor)
        raise SkillMaterializationError("selected SKILL.md is not a regular file")
    return root, root_descriptor


def _open_relative_parent(
    root_descriptor: int,
    relative: PurePosixPath,
    *,
    label: str,
) -> tuple[int, list[tuple[int, str, int, tuple[int, int, int, int, int, int]]]]:
    """Open the parent chain and retain descriptors for post-read identity checks."""

    current = os.dup(root_descriptor)
    chain: list[tuple[int, str, int, tuple[int, int, int, int, int, int]]] = []
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=current)
            observed = os.fstat(child)
            if not stat.S_ISDIR(observed.st_mode):
                os.close(child)
                raise SkillMaterializationError(
                    f"{label} parent is not a directory: {relative}"
                )
            chain.append((current, part, child, _stat_identity(observed)))
            current = child
        return current, chain
    except (OSError, SkillMaterializationError) as exc:
        os.close(current)
        for parent, _, _, _ in reversed(chain):
            os.close(parent)
        if isinstance(exc, SkillMaterializationError):
            raise
        raise SkillMaterializationError(
            f"{label} is unavailable or contains a symlink: {relative}"
        ) from exc


def _close_parent_chain(
    current: int,
    chain: Sequence[tuple[int, str, int, tuple[int, int, int, int, int, int]]],
) -> None:
    os.close(current)
    for parent, _, _, _ in reversed(chain):
        os.close(parent)


def _verify_parent_chain(
    chain: Sequence[tuple[int, str, int, tuple[int, int, int, int, int, int]]],
    *,
    relative: PurePosixPath,
    label: str,
) -> None:
    for parent, part, child, identity in reversed(chain):
        descriptor_observed = os.fstat(child)
        entry_observed = os.stat(part, dir_fd=parent, follow_symlinks=False)
        if not _same_open_identity(identity, descriptor_observed) or not _same_open_identity(
            identity, entry_observed
        ):
            raise SkillMaterializationError(
                f"{label} changed while being read: {relative}"
            )


def _read_bounded_file(
    root_descriptor: int,
    relative: PurePosixPath,
    *,
    max_bytes: int,
    label: str,
) -> _SecureRead:
    parent, chain = _open_relative_parent(
        root_descriptor, relative, label=label
    )
    descriptor = -1
    try:
        descriptor = os.open(relative.name, _FILE_OPEN_FLAGS, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SkillMaterializationError(
                f"{label} is not a regular file: {relative}"
            )
        if before.st_size > max_bytes:
            raise SkillMaterializationError(
                f"{label} exceeds the byte limit: {relative}"
            )
        identity = _stat_identity(before)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise SkillMaterializationError(
                    f"{label} exceeds the byte limit: {relative}"
                )
        after = os.fstat(descriptor)
        entry_after = os.stat(
            relative.name, dir_fd=parent, follow_symlinks=False
        )
        _verify_parent_chain(chain, relative=relative, label=label)
        if (
            not _same_open_identity(identity, after)
            or not _same_open_identity(identity, entry_after)
            or total != before.st_size
        ):
            raise SkillMaterializationError(
                f"{label} changed while being read: {relative}"
            )
        data = b"".join(chunks)
        return _SecureRead(
            file=MaterializedFile(
                path=relative.as_posix(),
                content=data,
                sha256=hashlib.sha256(data).hexdigest(),
            ),
            identity=identity,
        )
    except OSError as exc:
        raise SkillMaterializationError(
            f"{label} is unavailable or contains a symlink: {relative}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_parent_chain(parent, chain)


def _verify_path_identity(
    root_descriptor: int,
    relative: PurePosixPath,
    expected: tuple[int, int, int, int, int, int],
    *,
    label: str,
) -> None:
    parent, chain = _open_relative_parent(root_descriptor, relative, label=label)
    descriptor = -1
    try:
        descriptor = os.open(relative.name, _FILE_OPEN_FLAGS, dir_fd=parent)
        observed = os.fstat(descriptor)
        _verify_parent_chain(chain, relative=relative, label=label)
        if not _same_open_identity(expected, observed):
            raise SkillMaterializationError(
                f"{label} changed after being read: {relative}"
            )
    except OSError as exc:
        raise SkillMaterializationError(
            f"{label} changed after being read: {relative}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_parent_chain(parent, chain)


def _enumerate_regular_tree(
    directory_descriptor: int,
    *,
    prefix: PurePosixPath | None = None,
) -> list[PurePosixPath]:
    """Enumerate a descriptor-bound tree while rejecting every non-regular leaf."""

    base = prefix or PurePosixPath()
    directory_identity = _stat_identity(os.fstat(directory_descriptor))
    paths: list[PurePosixPath] = []
    try:
        names = sorted(os.listdir(directory_descriptor))
    except OSError as exc:
        raise SkillMaterializationError("skill tree cannot be enumerated") from exc
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise SkillMaterializationError("skill tree contains an invalid entry")
        relative = base / name
        try:
            entry_before = os.stat(
                name, dir_fd=directory_descriptor, follow_symlinks=False
            )
        except OSError as exc:
            raise SkillMaterializationError(
                f"skill tree entry is unavailable: {relative}"
            ) from exc
        if stat.S_ISDIR(entry_before.st_mode):
            try:
                child = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=directory_descriptor)
            except OSError as exc:
                raise SkillMaterializationError(
                    f"skill tree directory contains a symlink: {relative}"
                ) from exc
            try:
                if not _same_open_identity(
                    _stat_identity(entry_before), os.fstat(child)
                ):
                    raise SkillMaterializationError(
                        f"skill tree directory changed while opening: {relative}"
                    )
                paths.extend(_enumerate_regular_tree(child, prefix=relative))
                entry_after = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
                if not _same_open_identity(
                    _stat_identity(entry_before), entry_after
                ):
                    raise SkillMaterializationError(
                        f"skill tree directory changed while enumerating: {relative}"
                    )
            finally:
                os.close(child)
        elif stat.S_ISREG(entry_before.st_mode):
            paths.append(relative)
        else:
            raise SkillMaterializationError(
                f"skill tree contains a symlink or non-regular file: {relative}"
            )
    if not _same_open_identity(directory_identity, os.fstat(directory_descriptor)):
        raise SkillMaterializationError("skill tree changed while being enumerated")
    return paths


def secure_regular_file(
    root: Path,
    relative: str | PurePosixPath,
    *,
    max_bytes: int = MAX_REFERENCE_BYTES,
    label: str = "bound file",
) -> MaterializedFile:
    """Read one root-bound file and verify its path and root identities."""

    normalized = _validate_relative_path(
        relative if isinstance(relative, str) else relative.as_posix(),
        label=label,
    )
    root_descriptor = _open_absolute_directory_nofollow(root, label=f"{label} root")
    root_identity = _stat_identity(os.fstat(root_descriptor))
    try:
        secure_read = _read_bounded_file(
            root_descriptor,
            normalized,
            max_bytes=max_bytes,
            label=label,
        )
        _verify_path_identity(
            root_descriptor,
            normalized,
            secure_read.identity,
            label=label,
        )
        reopened_root = _open_absolute_directory_nofollow(
            root, label=f"{label} root"
        )
        try:
            if not _same_open_identity(root_identity, os.fstat(reopened_root)):
                raise SkillMaterializationError(
                    f"{label} root identity changed while being read"
                )
        finally:
            os.close(reopened_root)
        return secure_read.file
    finally:
        os.close(root_descriptor)


def secure_regular_tree(
    root: Path,
    *,
    max_file_bytes: int = MAX_REFERENCE_BYTES,
    max_total_bytes: int = 67_108_864,
    max_files: int = 4_096,
) -> tuple[MaterializedFile, ...]:
    """Read one exact regular-file tree through descriptor-relative nofollow I/O."""

    root_descriptor = _open_absolute_directory_nofollow(root, label="skill tree root")
    root_identity = _stat_identity(os.fstat(root_descriptor))
    try:
        paths = sorted(
            _enumerate_regular_tree(root_descriptor), key=lambda item: item.as_posix()
        )
        if len(paths) > max_files:
            raise SkillMaterializationError("skill tree exceeds the file-count limit")
        reads: list[tuple[PurePosixPath, _SecureRead]] = []
        total = 0
        for relative in paths:
            secure_read = _read_bounded_file(
                root_descriptor,
                relative,
                max_bytes=max_file_bytes,
                label="skill tree file",
            )
            total += len(secure_read.file.content)
            if total > max_total_bytes:
                raise SkillMaterializationError("skill tree exceeds the total byte limit")
            reads.append((relative, secure_read))
        for relative, secure_read in reads:
            _verify_path_identity(
                root_descriptor,
                relative,
                secure_read.identity,
                label="skill tree file",
            )
        final_paths = sorted(
            _enumerate_regular_tree(root_descriptor), key=lambda item: item.as_posix()
        )
        if paths != final_paths:
            raise SkillMaterializationError("skill tree membership changed while being read")
        reopened = _open_absolute_directory_nofollow(root, label="skill tree root")
        try:
            if not _same_open_identity(root_identity, os.fstat(reopened)):
                raise SkillMaterializationError("skill tree root identity changed")
        finally:
            os.close(reopened)
        return tuple(item.file for _, item in reads)
    finally:
        os.close(root_descriptor)


def _declared_required_references(frontmatter: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = frontmatter.get("metadata")
    hermes = metadata.get("hermes") if isinstance(metadata, Mapping) else None
    if not isinstance(hermes, Mapping):
        return []
    sources: list[object] = [hermes.get("required_references")]
    canonical = hermes.get("canonical_binding")
    if isinstance(canonical, Mapping):
        sources.append(canonical.get("required_references"))

    declared: dict[str, dict[str, Any]] = {}
    for source in sources:
        if source is None:
            continue
        if not isinstance(source, list):
            raise SkillMaterializationError("required_references must be a list")
        for raw in source:
            if isinstance(raw, str):
                row: dict[str, Any] = {"path": raw}
            elif isinstance(raw, Mapping):
                row = dict(raw)
            else:
                raise SkillMaterializationError(
                    "required reference entries must be paths or objects"
                )
            relative = _validate_relative_path(
                row.get("path"), label="required reference"
            ).as_posix()
            expected = row.get("sha256")
            if expected is not None and (
                type(expected) is not str or not _SHA256_RE.fullmatch(expected)
            ):
                raise SkillMaterializationError(
                    f"required reference sha256 is invalid: {relative}"
                )
            normalized = {"path": relative, "sha256": expected}
            prior = declared.get(relative)
            if prior is not None and prior != normalized:
                raise SkillMaterializationError(
                    f"required reference has conflicting declarations: {relative}"
                )
            declared[relative] = normalized
    return [declared[path] for path in sorted(declared)]


def _materialization_stream(files: Sequence[MaterializedFile]) -> bytes:
    stream = bytearray(b"HERMES_SKILL_MATERIALIZATION_V1\x00")
    for item in files:
        path = item.path.encode("utf-8")
        stream.extend(len(path).to_bytes(4, "big"))
        stream.extend(path)
        stream.extend(len(item.content).to_bytes(8, "big"))
        stream.extend(item.content)
    return bytes(stream)


def _validated_source_binding(
    source_binding: Mapping[str, Any],
    *,
    skill_path: str,
    skill_sha256: str,
) -> dict[str, Any]:
    required = ("source_kind", "source_manifest_identity")
    if any(type(source_binding.get(field)) is not str or not source_binding[field] for field in required):
        raise SkillMaterializationError("source binding identity is incomplete")
    expected_sha = source_binding.get("skill_sha256")
    if expected_sha is not None and expected_sha != skill_sha256:
        raise SkillMaterializationError("selected skill source digest drift")
    expected_path = source_binding.get("path")
    normalized_expected_path = _validate_relative_path(
        expected_path, label="source binding path"
    ).as_posix()
    if normalized_expected_path != skill_path:
        raise SkillMaterializationError("selected skill source path drift")
    source_head = source_binding.get("source_head")
    if source_head is not None and (
        type(source_head) is not str or not source_head.strip()
    ):
        raise SkillMaterializationError("source_head must be null or a non-empty identity")
    return {
        "source_kind": source_binding["source_kind"],
        "source_manifest_identity": source_binding["source_manifest_identity"],
        "source_head": source_head,
        "path": skill_path,
        "skill_sha256": skill_sha256,
    }


def _validated_plugin_binding(
    plugin_binding: Mapping[str, Any] | None,
    *,
    qualified_skill_id: str,
) -> dict[str, str] | None:
    if plugin_binding is None:
        return None
    required = (
        "namespaced_skill_id",
        "package_identity",
        "package_version",
        "manifest_digest",
        "content_digest",
    )
    if any(type(plugin_binding.get(field)) is not str or not plugin_binding[field] for field in required):
        raise SkillMaterializationError("plugin binding identity is incomplete")
    if plugin_binding["namespaced_skill_id"] != qualified_skill_id:
        raise SkillMaterializationError("plugin namespaced skill identity drift")
    for field in ("manifest_digest", "content_digest"):
        if not _SHA256_RE.fullmatch(plugin_binding[field]):
            raise SkillMaterializationError(f"plugin {field} is invalid")
    return {field: plugin_binding[field] for field in required}


def materialize_skill(
    skill_md: Path,
    *,
    qualified_skill_id: str,
    trigger_mode: str,
    source_binding: Mapping[str, Any],
    mode: str,
    plugin_binding: Mapping[str, Any] | None = None,
    source_trigger: str | None = None,
) -> MaterializedSkill:
    """Full-read a selected skill and produce an exact coverage receipt."""

    if not _QUALIFIED_SKILL_RE.fullmatch(qualified_skill_id):
        raise SkillMaterializationError("qualified_skill_id is invalid")
    if trigger_mode not in TRIGGER_MODES:
        raise SkillMaterializationError("trigger_mode is invalid")
    if mode not in MATERIALIZATION_MODES:
        raise SkillMaterializationError("materialization mode is invalid")
    if source_trigger is not None and (
        type(source_trigger) is not str or not source_trigger.strip()
    ):
        raise SkillMaterializationError("source_trigger must be non-empty when present")

    root, root_descriptor = _canonical_skill_root(skill_md)
    root_identity = _stat_identity(os.fstat(root_descriptor))
    secure_reads: list[tuple[PurePosixPath, _SecureRead, str]] = []
    try:
        skill_read = _read_bounded_file(
            root_descriptor,
            PurePosixPath("SKILL.md"),
            max_bytes=MAX_SKILL_BYTES,
            label="selected skill",
        )
        secure_reads.append((PurePosixPath("SKILL.md"), skill_read, "selected skill"))
        skill = skill_read.file
        try:
            text = skill.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillMaterializationError("selected SKILL.md is not UTF-8") from exc
        frontmatter, _ = parse_frontmatter(text)
        declared_refs = _declared_required_references(frontmatter)
        references: list[MaterializedFile] = []
        for declaration in declared_refs:
            relative = _validate_relative_path(
                declaration["path"], label="required reference"
            )
            reference_read = _read_bounded_file(
                root_descriptor,
                relative,
                max_bytes=MAX_REFERENCE_BYTES,
                label="required reference",
            )
            secure_reads.append((relative, reference_read, "required reference"))
            reference = reference_read.file
            if (
                declaration["sha256"] is not None
                and declaration["sha256"] != reference.sha256
            ):
                raise SkillMaterializationError(
                    f"required reference digest drift: {reference.path}"
                )
            references.append(reference)
        for relative, secure_read, label in secure_reads:
            _verify_path_identity(
                root_descriptor,
                relative,
                secure_read.identity,
                label=label,
            )
        reopened_root = _open_absolute_directory_nofollow(
            root, label="selected skill root"
        )
        try:
            if not _same_open_identity(root_identity, os.fstat(reopened_root)):
                raise SkillMaterializationError(
                    "selected skill root identity changed while being read"
                )
        finally:
            os.close(reopened_root)
    finally:
        os.close(root_descriptor)
    total_bytes = len(skill.content) + sum(len(item.content) for item in references)
    if total_bytes > MAX_MATERIALIZED_BYTES:
        raise SkillMaterializationError("selected skill materialization exceeds total byte limit")

    skill_path = f"skills/{root.name}/SKILL.md"
    normalized_source = _validated_source_binding(
        source_binding,
        skill_path=skill_path,
        skill_sha256=skill.sha256,
    )
    normalized_plugin = _validated_plugin_binding(
        plugin_binding, qualified_skill_id=qualified_skill_id
    )
    reference_rows = [
        {"path": item.path, "sha256": item.sha256} for item in references
    ]
    coverage_record = {
        "qualified_skill_id": qualified_skill_id,
        "skill_sha256": skill.sha256,
        "required_references": reference_rows,
        "stream_format": "length_prefixed_bytes_v1",
    }
    coverage_digest = _digest_json(coverage_record)
    semantic_digest = hashlib.sha256(
        _materialization_stream((skill, *references))
    ).hexdigest()
    compiler = compiler_profile()
    materialization_binding = {
        "mode": mode,
        "coverage_profile_digest": coverage_digest,
        "compiler_identity": compiler["identity"],
        "compiler_digest": compiler["digest"],
        "profile_digest": "",
        "semantic_operational_digest": semantic_digest,
    }
    profile_record = {
        "qualified_skill_id": qualified_skill_id,
        "trigger_mode": trigger_mode,
        "source_trigger": source_trigger,
        "source_binding": normalized_source,
        "required_references": reference_rows,
        "materialization_binding": {
            key: value
            for key, value in materialization_binding.items()
            if key != "profile_digest"
        },
        "plugin_binding": normalized_plugin,
    }
    materialization_binding["profile_digest"] = _digest_json(profile_record)
    receipt = {
        "schema": SCHEMA,
        **profile_record,
        "materialization_binding": materialization_binding,
        "coverage": {
            "full_skill_read": True,
            "required_reference_count": len(references),
            "total_bytes": total_bytes,
            "undeclared_references_materialized": False,
        },
    }
    return MaterializedSkill(
        qualified_skill_id=qualified_skill_id,
        skill=skill,
        required_references=tuple(references),
        receipt=receipt,
    )


def verify_expected_profile(
    materialized: MaterializedSkill, expected: Mapping[str, Any]
) -> None:
    """Fail closed on same-name content/profile drift."""

    if expected.get("qualified_skill_id") != materialized.qualified_skill_id:
        raise SkillMaterializationError("qualified skill profile identity drift")
    observed = materialized.receipt["materialization_binding"]
    wanted = expected.get("materialization_binding")
    if not isinstance(wanted, Mapping):
        raise SkillMaterializationError("expected materialization binding missing")
    for field in (
        "coverage_profile_digest",
        "compiler_identity",
        "compiler_digest",
        "profile_digest",
        "semantic_operational_digest",
    ):
        if wanted.get(field) != observed[field]:
            raise SkillMaterializationError(
                f"same qualified skill has different {field}"
            )


def compile_attempt_profile(
    materialized: Sequence[MaterializedSkill],
    *,
    task_skill_chain_reason: str,
    omitted_conditionals: Sequence[Mapping[str, str]],
    mode: str,
    prompt: bytes,
) -> dict[str, Any]:
    """Bind one attempt to selected and intentionally omitted skill profiles."""

    if mode not in MATERIALIZATION_MODES:
        raise SkillMaterializationError("attempt materialization mode is invalid")
    if not task_skill_chain_reason.strip():
        raise SkillMaterializationError("task skill chain reason is required")
    selected = sorted(materialized, key=lambda item: item.qualified_skill_id)
    selected_ids = [item.qualified_skill_id for item in selected]
    if len(selected_ids) != len(set(selected_ids)):
        raise SkillMaterializationError("attempt selected skill IDs must be unique")
    omitted: list[dict[str, str]] = []
    for row in omitted_conditionals:
        skill_id = row.get("qualified_skill_id")
        reason = row.get("reason")
        if (
            type(skill_id) is not str
            or not _QUALIFIED_SKILL_RE.fullmatch(skill_id)
            or type(reason) is not str
            or not reason.strip()
        ):
            raise SkillMaterializationError("omitted conditional requires skill ID and reason")
        if skill_id in selected_ids:
            raise SkillMaterializationError("selected skill cannot also be omitted")
        omitted.append({"qualified_skill_id": skill_id, "reason": reason})
    omitted.sort(key=lambda row: row["qualified_skill_id"])
    coverage = _digest_json(
        [
            item.receipt["materialization_binding"]["coverage_profile_digest"]
            for item in selected
        ]
    )
    compiler = _digest_json(
        [
            item.receipt["materialization_binding"]["profile_digest"]
            for item in selected
        ]
    )
    semantic = _digest_json(
        [
            item.receipt["materialization_binding"]["semantic_operational_digest"]
            for item in selected
        ]
    )
    return {
        "task_skill_chain_reason": task_skill_chain_reason,
        "selected_skill_ids": selected_ids,
        "omitted_conditionals": omitted,
        "mode": mode,
        "coverage_profile_digest": coverage,
        "compiler_profile_digest": compiler,
        "semantic_operational_digest": semantic,
        "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
    }


def validate_transport_transition(
    previous_attempt: Mapping[str, Any] | None,
    current_attempt: Mapping[str, Any],
) -> None:
    """Reject silent mid-attempt plugin/exec transport fallback."""

    if previous_attempt is None:
        return
    if previous_attempt.get("transport") == current_attempt.get("transport"):
        return
    if previous_attempt.get("terminal") is not True:
        raise SkillMaterializationError("transport switch requires a terminal prior attempt")
    if previous_attempt.get("stable_task_lineage_id") != current_attempt.get(
        "stable_task_lineage_id"
    ):
        raise SkillMaterializationError("transport switch changed stable task lineage")
    if previous_attempt.get("attempt_id") == current_attempt.get("attempt_id"):
        raise SkillMaterializationError("transport switch requires a new attempt")
    previous_epoch = previous_attempt.get("owner_epoch")
    current_epoch = current_attempt.get("owner_epoch")
    if type(previous_epoch) is not int or type(current_epoch) is not int or current_epoch <= previous_epoch:
        raise SkillMaterializationError("transport switch requires a newer owner epoch")
    if current_attempt.get("owner_transfer_readback") is not True:
        raise SkillMaterializationError("transport switch requires owner-transfer readback")
    if current_attempt.get("simultaneous_writer_count") != 1:
        raise SkillMaterializationError("transport switch requires exactly one writer")
