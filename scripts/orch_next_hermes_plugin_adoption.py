#!/usr/bin/env python3
"""Execute one authorized, crash-safe Codex/Claude plugin adoption.

The authority wire contains only fixed identities, revisions, digests, and
state labels.  Host paths and commands are fixed inside the two adapters.  The
script does not accept a path, command, plugin, marketplace, host, or version
from its CLI.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass, replace
import errno
import fcntl
import hashlib
import json
import os
import pwd
import re
import select
import secrets
import signal
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable, Final, Iterator, Protocol, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import orch_next_hermes_distribution as distribution  # noqa: E402
from tui_gateway import maestro_plugin_adoption_authority as authority  # noqa: E402


JOURNAL_SCHEMA: Final = "orch-next-hermes-plugin-adoption-journal.v1"
ROLLBACK_SCHEMA: Final = "orch-next-hermes-plugin-adoption-rollback.v1"
TERMINAL_BRIDGE_SCHEMA: Final = (
    "orch-next-hermes-terminal-observation-ordinary-apply-bridge.v1"
)
ARCHIVE_SCHEMA: Final = "orch-next-hermes-plugin-adoption-archive.v1"
PHASES: Final = (
    "AUTHORIZED",
    "PREPARED",
    "CODEX_APPLIED",
    "CLAUDE_APPLIED",
    "VERIFIED",
    "COMMITTED",
    "ROLLING_BACK",
    "ROLLED_BACK",
)
FORWARD_PHASES: Final = PHASES[:6]
_JOURNAL_EDGES: Final = frozenset({
    ("AUTHORIZED", "PREPARED"),
    ("AUTHORIZED", "ROLLING_BACK"),
    ("PREPARED", "CODEX_APPLIED"),
    ("PREPARED", "ROLLING_BACK"),
    ("CODEX_APPLIED", "CLAUDE_APPLIED"),
    ("CODEX_APPLIED", "ROLLING_BACK"),
    ("CLAUDE_APPLIED", "VERIFIED"),
    ("CLAUDE_APPLIED", "ROLLING_BACK"),
    ("VERIFIED", "COMMITTED"),
    ("VERIFIED", "ROLLING_BACK"),
    ("ROLLING_BACK", "ROLLED_BACK"),
})
_JOURNAL_EDGE_LEAVES: Final = frozenset(
    ".ordinary-journal-edge-"
    f"{current.lower()}-to-{successor.lower()}"
    for current, successor in _JOURNAL_EDGES
)
HOST_ORDER: Final = ("codex", "claude")
PREDECESSOR_VERSION: Final = "0.1.13"
PREDECESSOR_REVISION: Final = "f7a8102745270394cbacab64199346354a2abff1"
PREDECESSOR_BRANCH: Final = "refs/heads/codex/hermes-ic204-skill-lifecycle-v1"
PREDECESSOR_WORKTREE_LEAF: Final = "hermes-ic204-skill-lifecycle-v1"
PREDECESSOR_BUNDLE_DIGEST: Final = (
    "55d7d210250a8fe81a1b3521fd966c09d6150aa83999e2fafb63761020f11e4e"
)
CLAUDE_RESIDUAL_PREDECESSOR_DIGEST: Final = (
    "6d60abeb44604b2f4c58ec4c54e45451664d7631a0088fb3298a084d8fdcff8d"
)
CLAUDE_RESIDUE_VERSION: Final = "0.1.15"
# Exact live inactive residue identity for quarantine/rollback only. It is not
# admitted source provenance and must never authorize execution or installation.
CLAUDE_RESIDUE_OPAQUE_DIGEST: Final = (
    "7aeec22ebd07df360afab3ca35a37560607e893108df41ffceb44ad8a3466687"
)
TARGET_CACHE_VERSIONS: Final = (
    PREDECESSOR_VERSION,
    CLAUDE_RESIDUE_VERSION,
    "0.1.17",
    "0.1.18",
    "0.1.19",
    "0.1.20",
    "0.1.21",
    "0.1.22",
    "0.1.23",
    "0.1.24",
    "0.1.25",
    "0.1.26",
    "0.1.27",
    "0.1.28",
    "0.1.29",
    "0.1.30",
    "0.1.31",
    "0.1.32",
    "0.1.33",
    "0.1.34",
    "0.1.35",
    "0.1.36",
    "0.1.37",
    "0.1.38",
    "0.1.39",
    "0.1.40",
    "0.1.41",
    "0.1.42",
    "0.1.43",
    "0.1.44",
    "0.1.45",
    "0.1.46",
    "0.1.47",
    authority.TERMINAL_PLUGIN_VERSION,
    authority.ORDINARY_APPLY_PLUGIN_VERSION,
)
TARGET_CACHE_HANDLES: Final = {
    PREDECESSOR_VERSION: "target-cache-v013",
    CLAUDE_RESIDUE_VERSION: "target-cache-v015",
    "0.1.17": "target-cache-v017",
    "0.1.18": "target-cache-v018",
    "0.1.19": "target-cache-v019",
    "0.1.20": "target-cache-v020",
    "0.1.21": "target-cache-v021",
    "0.1.22": "target-cache-v022",
    "0.1.23": "target-cache-v023",
    "0.1.24": "target-cache-v024",
    "0.1.25": "target-cache-v025",
    "0.1.26": "target-cache-v026",
    "0.1.27": "target-cache-v027",
    "0.1.28": "target-cache-v028",
    "0.1.29": "target-cache-v029",
    "0.1.30": "target-cache-v030",
    "0.1.31": "target-cache-v031",
    "0.1.32": "target-cache-v032",
    "0.1.33": "target-cache-v033",
    "0.1.34": "target-cache-v034",
    "0.1.35": "target-cache-v035",
    "0.1.36": "target-cache-v036",
    "0.1.37": "target-cache-v037",
    "0.1.38": "target-cache-v038",
    "0.1.39": "target-cache-v039",
    "0.1.40": "target-cache-v040",
    "0.1.41": "target-cache-v041",
    "0.1.42": "target-cache-v042",
    "0.1.43": "target-cache-v043",
    "0.1.44": "target-cache-v044",
    "0.1.45": "target-cache-v045",
    "0.1.46": "target-cache-v046",
    "0.1.47": "target-cache-v047",
    authority.TERMINAL_PLUGIN_VERSION: "target-cache-v048",
    authority.ORDINARY_APPLY_PLUGIN_VERSION: "target-cache-v049",
}
PREDECESSOR_SOURCE_MANIFEST_DIGEST: Final = (
    "b91ad2dbd7143f40311d7d0e073ab830f0ea1052ced3b98f4ca133b1215cf4e9"
)
PREDECESSOR_MARKETPLACE_MANIFEST_DIGEST: Final = (
    "c56fc03e84a2fe3f9c4661dd8fb71e57b309149fa38fabbf74f6ec0fa5c8b7a9"
)
PREDECESSOR_BUNDLE_TREE_OID: Final = "db5b533bc39f79c91a38a88577c2560d87756ce4"
PREDECESSOR_MARKETPLACE_MANIFEST_BLOB_OID: Final = (
    "4c9703a9a4fe2d83565d6208afb18a1a55253308"
)
PREDECESSOR_SOURCE_MANIFEST_BLOB_OID: Final = (
    "a8f0af61b75988e1a268dbbb63b7c1cdb27c4042"
)
SOURCE_BINDING_SCHEMA: Final = "orch-next-hermes-marketplace-source-binding.v1"
PREDECESSOR_MARKETPLACE_IDENTITY_SCHEMA: Final = (
    "orch-next-hermes-predecessor-marketplace-identity.v1"
)
_CODEX_BIN = Path("/opt/homebrew/bin/codex")
_CLAUDE_BIN = Path("/opt/homebrew/bin/claude")
_HOMEBREW_ROOT = Path("/opt/homebrew")
_HOMEBREW_OWNER_UID = 501
_HOMEBREW_GROUP_GID = 80
_MAX_COMMAND_OUTPUT = 4 * 1024 * 1024
_MAX_VERSION_OUTPUT = 128
_CHILD_TIMEOUT_SECONDS = 60.0
_CHILD_TERM_GRACE_SECONDS = 1.0
_MAX_ROLLBACK_SNAPSHOT_BYTES: Final = 512 * 1024 * 1024
_MAX_ROLLBACK_SNAPSHOT_ENTRIES: Final = 20_000
_MAX_ROLLBACK_SNAPSHOT_DEPTH: Final = 32
_PROTECTED_PIN_SIGNALS = frozenset({
    signal.SIGHUP,
    signal.SIGINT,
    signal.SIGQUIT,
    signal.SIGTERM,
})
_CHILD_DEFAULT_SIGNALS = _PROTECTED_PIN_SIGNALS | frozenset({signal.SIGPIPE})
_PLUGIN_VERSION_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,23})?"
)
_JOURNAL_KEYS = frozenset({
    "schema",
    "transaction_id",
    "decision_id",
    "phase",
    "plan_digest",
    "before_state_digest",
    "after_state_digest",
    "rollback_manifest_digest",
    "request_digest",
    "envelope_digest",
    "request_b64",
    "envelope_b64",
    "before_states",
    "after_states",
})
_TERMINAL_JOURNAL_KEYS = _JOURNAL_KEYS | frozenset({
    "operation",
    "predecessor_identity_digest",
    "current_identity_digest",
    "canonical_identity_digest",
    "canonical_recovery",
    "host_mutation_count",
})
_TERMINAL_PREPARED_KEYS = _TERMINAL_JOURNAL_KEYS - frozenset({
    "envelope_digest",
    "envelope_b64",
})
_TERMINAL_PREPARED_TEMP_LEAF: Final = ".terminal-journal.prepared.tmp"
_TERMINAL_PREPARED_BACKUP_LEAF: Final = ".terminal-journal.prepared"
_TERMINAL_STAGE_LEAF: Final = ".terminal-journal.stage"
_TERMINAL_STAGE_TEMP_LEAF: Final = ".terminal-journal.stage.tmp"
_TERMINAL_OPERATIONAL_STATES = frozenset({"qualification_pending", "orphaned"})
_TERMINAL_REGISTRY_STATES = frozenset({"active", "installed", "inactive", "orphaned"})
_TERMINAL_ANCHOR = "fp1-canonical-recovery"
_TERMINAL_BRIDGE_MANIFEST_LEAF: Final = "terminal-rollback-manifest.json"
_ORDINARY_PREPARED_LEAF: Final = ".ordinary-request.prepared"
_ORDINARY_CONSUMED_LEAF: Final = ".ordinary-request.consumed"
_ORDINARY_PREPARED_KEYS: Final = frozenset({
    "after_states",
    "before_states",
    "decision_id",
    "manifest_digest",
    "phase",
    "request_b64",
    "request_digest",
    "schema",
    "transaction_id",
})
_TERMINAL_BRIDGE_CAPTURE_KEYS: Final = frozenset({
    "before_state_digest",
    "cache_digest",
    "cache_source_digest",
    "host",
    "install_projection_digest",
    "marketplace_digest",
    "orphan_marker_content_digest",
    "source_digest",
    "source_version",
})
_TERMINAL_BRIDGE_MANIFEST_KEYS: Final = frozenset({
    "host_order",
    "ordinary_plugin_version",
    "policy",
    "schema",
    "sources",
    "states",
    "terminal_canonical_identity_digest",
    "terminal_current_identity_digest",
    "terminal_envelope_digest",
    "terminal_journal_b64",
    "terminal_journal_digest",
    "terminal_plugin_version",
    "terminal_request_digest",
    "terminal_source_bundle_digest",
    "terminal_source_revision",
    "terminal_transaction_id",
})


class AdoptionError(RuntimeError):
    """A stable, sanitized adoption failure."""


def _require_ordinary_apply_plugin_version_alignment() -> None:
    ordinary_version = getattr(
        authority,
        "ORDINARY_APPLY_PLUGIN_VERSION",
        None,
    )
    if (
        type(ordinary_version) is not str
        or authority.PLUGIN_VERSION != ordinary_version
        or distribution.PLUGIN_VERSION != ordinary_version
    ):
        raise AdoptionError("ordinary_apply_plugin_version_mismatch")


class InjectedCrash(BaseException):
    """Test-only process-crash boundary; intentionally bypasses rollback."""


@dataclass(frozen=True, slots=True)
class _FixedCliNode:
    path: Path
    kind: str
    uid: int
    gid: int
    mode: int
    size: int | None = None
    link_target: str | None = None


@dataclass(frozen=True, slots=True)
class _FixedCliSpec:
    name: str
    link: Path
    target: Path
    link_target: str
    version_output: bytes
    nodes: tuple[_FixedCliNode, ...]


@dataclass(frozen=True, slots=True)
class _FixedCliIdentity:
    path: Path
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    size: int
    mtime_ns: int
    nlink: int
    link_target: str | None


def _homebrew_directory(path: str, mode: int = 0o755) -> _FixedCliNode:
    return _FixedCliNode(
        Path(path), "directory", _HOMEBREW_OWNER_UID, _HOMEBREW_GROUP_GID, mode
    )


_CODEX_CLI = _FixedCliSpec(
    name="codex",
    link=_CODEX_BIN,
    target=Path("/opt/homebrew/Caskroom/codex/0.147.0/bin/codex"),
    link_target="/opt/homebrew/Caskroom/codex/0.147.0/bin/codex",
    version_output=b"codex-cli 0.147.0\n",
    nodes=(
        _FixedCliNode(Path("/opt"), "directory", 0, 0, 0o755),
        _homebrew_directory("/opt/homebrew"),
        _homebrew_directory("/opt/homebrew/bin", 0o775),
        _FixedCliNode(
            _CODEX_BIN,
            "symlink",
            _HOMEBREW_OWNER_UID,
            _HOMEBREW_GROUP_GID,
            0o755,
            link_target="/opt/homebrew/Caskroom/codex/0.147.0/bin/codex",
        ),
        _homebrew_directory("/opt/homebrew/Caskroom", 0o775),
        _homebrew_directory("/opt/homebrew/Caskroom/codex"),
        _homebrew_directory("/opt/homebrew/Caskroom/codex/0.147.0"),
        _homebrew_directory("/opt/homebrew/Caskroom/codex/0.147.0/bin"),
        _FixedCliNode(
            Path("/opt/homebrew/Caskroom/codex/0.147.0/bin/codex"),
            "regular",
            _HOMEBREW_OWNER_UID,
            _HOMEBREW_GROUP_GID,
            0o755,
            size=219997536,
        ),
    ),
)

_CLAUDE_CLI = _FixedCliSpec(
    name="claude",
    link=_CLAUDE_BIN,
    target=Path(
        "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    ),
    link_target="../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
    version_output=b"2.1.222 (Claude Code)\n",
    nodes=(
        _FixedCliNode(Path("/opt"), "directory", 0, 0, 0o755),
        _homebrew_directory("/opt/homebrew"),
        _homebrew_directory("/opt/homebrew/bin", 0o775),
        _FixedCliNode(
            _CLAUDE_BIN,
            "symlink",
            _HOMEBREW_OWNER_UID,
            _HOMEBREW_GROUP_GID,
            0o755,
            link_target="../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
        ),
        _homebrew_directory("/opt/homebrew/lib", 0o775),
        _homebrew_directory("/opt/homebrew/lib/node_modules"),
        _homebrew_directory("/opt/homebrew/lib/node_modules/@anthropic-ai"),
        _homebrew_directory(
            "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code"
        ),
        _homebrew_directory(
            "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin"
        ),
        _FixedCliNode(
            Path(
                "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            ),
            "regular",
            _HOMEBREW_OWNER_UID,
            _HOMEBREW_GROUP_GID,
            0o755,
            size=271289792,
        ),
    ),
)


def _lexical_link_target(link: Path, target: str) -> Path:
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = link.parent / candidate
    return Path(os.path.normpath(candidate))


def _capture_fixed_cli(spec: _FixedCliSpec) -> tuple[_FixedCliIdentity, ...]:
    if (
        not spec.link.is_absolute()
        or not spec.target.is_absolute()
        or _lexical_link_target(spec.link, spec.link_target) != spec.target
        or os.path.commonpath((str(_HOMEBREW_ROOT), str(spec.target)))
        != str(_HOMEBREW_ROOT)
    ):
        raise AdoptionError("host_cli_drift")
    identities: list[_FixedCliIdentity] = []
    for expected in spec.nodes:
        try:
            observed = expected.path.lstat()
            observed_link = os.readlink(expected.path) if stat.S_ISLNK(observed.st_mode) else None
        except OSError as exc:
            raise AdoptionError("host_cli_unavailable") from exc
        kind_matches = {
            "directory": stat.S_ISDIR(observed.st_mode),
            "regular": stat.S_ISREG(observed.st_mode),
            "symlink": stat.S_ISLNK(observed.st_mode),
        }.get(expected.kind, False)
        if (
            not kind_matches
            or observed.st_uid != expected.uid
            or observed.st_gid != expected.gid
            or stat.S_IMODE(observed.st_mode) != expected.mode
            or (expected.size is not None and observed.st_size != expected.size)
            or observed_link != expected.link_target
        ):
            raise AdoptionError("host_cli_drift")
        identities.append(
            _FixedCliIdentity(
                path=expected.path,
                device=observed.st_dev,
                inode=observed.st_ino,
                mode=observed.st_mode,
                uid=observed.st_uid,
                gid=observed.st_gid,
                size=observed.st_size,
                mtime_ns=observed.st_mtime_ns,
                nlink=observed.st_nlink,
                link_target=observed_link,
            )
        )
    if spec.nodes[-1].path != spec.target or spec.nodes[-1].kind != "regular":
        raise AdoptionError("host_cli_drift")
    return tuple(identities)


def _fixed_cli_environment() -> dict[str, str]:
    return {
        "HOME": str(_fixed_user_home()),
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "LC_ALL": "C",
    }


def _same_fixed_cli_inode(
    observed: os.stat_result,
    expected: _FixedCliIdentity,
    *,
    nlink: int,
) -> bool:
    return (
        observed.st_dev == expected.device
        and observed.st_ino == expected.inode
        and observed.st_mode == expected.mode
        and observed.st_uid == expected.uid
        and observed.st_gid == expected.gid
        and observed.st_size == expected.size
        and observed.st_mtime_ns == expected.mtime_ns
        and observed.st_nlink == nlink
    )


def _fd_sha256(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


@contextmanager
def _pinned_cli_executable(
    spec: _FixedCliSpec,
    expected_final: _FixedCliIdentity,
    transaction_root: Path,
) -> Iterator[Path]:
    _lstat_admitted_directory(transaction_root, create=True)
    execution_parent = transaction_root / "cli-exec"
    _lstat_admitted_directory(execution_parent, create=True)
    execution_root = execution_parent / spec.name
    try:
        execution_root.mkdir(mode=0o700)
    except OSError as exc:
        raise AdoptionError("host_cli_pin_drift") from exc
    root_info = execution_root.lstat()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.getuid()
        or stat.S_IMODE(root_info.st_mode) != 0o700
    ):
        raise AdoptionError("host_cli_pin_drift")
    root_identity = (root_info.st_dev, root_info.st_ino)
    pin = execution_root / "executable"
    source_fd = -1
    pin_cleanup_identity: tuple[int, int] | None = None
    pin_identity: tuple[int, int, int, int, int, int, int] | None = None
    try:
        source_fd = os.open(
            spec.target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        source_before = os.fstat(source_fd)
        if not _same_fixed_cli_inode(
            source_before, expected_final, nlink=expected_final.nlink
        ):
            raise AdoptionError("host_cli_drift")
        copied = False
        try:
            os.link(spec.target, pin, follow_symlinks=False)
            linked_info = pin.lstat()
            pin_cleanup_identity = (linked_info.st_dev, linked_info.st_ino)
        except OSError as exc:
            if exc.errno not in {
                errno.EACCES,
                errno.EMLINK,
                errno.EPERM,
                errno.EXDEV,
            }:
                raise AdoptionError("host_cli_pin_drift") from exc
            copied = True
            destination = os.open(
                pin,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o500,
            )
            try:
                destination_info = os.fstat(destination)
                pin_cleanup_identity = (
                    destination_info.st_dev,
                    destination_info.st_ino,
                )
                os.fchmod(destination, 0o500)
                os.lseek(source_fd, 0, os.SEEK_SET)
                source_digest = hashlib.sha256()
                while True:
                    chunk = os.read(source_fd, 1024 * 1024)
                    if not chunk:
                        break
                    source_digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination, view)
                        if written <= 0:
                            raise AdoptionError("host_cli_pin_drift")
                        view = view[written:]
                os.fsync(destination)
            finally:
                os.close(destination)
            copied_fd = os.open(pin, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                if _fd_sha256(copied_fd) != source_digest.hexdigest():
                    raise AdoptionError("host_cli_pin_drift")
            finally:
                os.close(copied_fd)
        pin_info = pin.lstat()
        source_after_pin = os.fstat(source_fd)
        if copied:
            if (
                not stat.S_ISREG(pin_info.st_mode)
                or pin_info.st_uid != os.getuid()
                or stat.S_IMODE(pin_info.st_mode) != 0o500
                or pin_info.st_nlink != 1
                or pin_info.st_size != expected_final.size
                or not _same_fixed_cli_inode(
                    source_after_pin, expected_final, nlink=expected_final.nlink
                )
            ):
                raise AdoptionError("host_cli_pin_drift")
        elif (
            not _same_fixed_cli_inode(
                pin_info, expected_final, nlink=expected_final.nlink + 1
            )
            or not _same_fixed_cli_inode(
                source_after_pin, expected_final, nlink=expected_final.nlink + 1
            )
        ):
            raise AdoptionError("host_cli_pin_drift")
        pin_identity = (
            pin_info.st_dev,
            pin_info.st_ino,
            pin_info.st_mode,
            pin_info.st_uid,
            pin_info.st_gid,
            pin_info.st_size,
            pin_info.st_nlink,
        )
        yield pin
        final_source_nlink = expected_final.nlink if copied else expected_final.nlink + 1
        if not _same_fixed_cli_inode(
            os.fstat(source_fd), expected_final, nlink=final_source_nlink
        ):
            raise AdoptionError("host_cli_drift")
    finally:
        cleanup_error = False
        if pin_cleanup_identity is not None:
            try:
                observed_pin = pin.lstat()
                observed_cleanup_identity = (
                    observed_pin.st_dev,
                    observed_pin.st_ino,
                )
                observed_identity = (
                    observed_pin.st_dev,
                    observed_pin.st_ino,
                    observed_pin.st_mode,
                    observed_pin.st_uid,
                    observed_pin.st_gid,
                    observed_pin.st_size,
                    observed_pin.st_nlink,
                )
                if observed_cleanup_identity != pin_cleanup_identity or (
                    pin_identity is not None and observed_identity != pin_identity
                ):
                    cleanup_error = True
                else:
                    pin.unlink()
            except OSError:
                cleanup_error = True
        if source_fd >= 0:
            os.close(source_fd)
        try:
            current_root = execution_root.lstat()
            if (current_root.st_dev, current_root.st_ino) != root_identity:
                cleanup_error = True
            elif not any(execution_root.iterdir()):
                execution_root.rmdir()
            else:
                cleanup_error = True
        except OSError:
            cleanup_error = True
        if cleanup_error:
            raise AdoptionError("host_cli_pin_cleanup_failed")


def _invoke_fixed_cli(
    spec: _FixedCliSpec,
    args: tuple[str, ...],
    *,
    expected_final: _FixedCliIdentity,
    transaction_root: Path,
) -> subprocess.CompletedProcess[bytes]:
    previous_signal_mask = signal.pthread_sigmask(
        signal.SIG_BLOCK, _PROTECTED_PIN_SIGNALS
    )
    try:
        with _pinned_cli_executable(
            spec, expected_final, transaction_root
        ) as executable:
            return _run_pinned_child(
                executable,
                args,
                previous_signal_mask=previous_signal_mask,
            )
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_signal_mask)


def _run_pinned_child(
    executable: Path,
    args: tuple[str, ...],
    *,
    previous_signal_mask: set[signal.Signals],
) -> subprocess.CompletedProcess[bytes]:
    command = (str(executable), *args)
    child_signal_mask = set(previous_signal_mask) - _CHILD_DEFAULT_SIGNALS
    descriptors: list[int] = []
    pid: int | None = None
    try:
        stdin_descriptor = os.open(
            "/dev/null", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        )
        stderr_descriptor = os.open(
            "/dev/null", os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
        )
        read_descriptor, write_descriptor = os.pipe()
        os.set_blocking(read_descriptor, False)
        os.set_inheritable(read_descriptor, False)
        os.set_inheritable(write_descriptor, False)
        descriptors.extend(
            (stdin_descriptor, stderr_descriptor, read_descriptor, write_descriptor)
        )
        file_actions = (
            (os.POSIX_SPAWN_DUP2, stdin_descriptor, 0),
            (os.POSIX_SPAWN_DUP2, write_descriptor, 1),
            (os.POSIX_SPAWN_DUP2, stderr_descriptor, 2),
            (os.POSIX_SPAWN_CLOSE, read_descriptor),
            (os.POSIX_SPAWN_CLOSE, write_descriptor),
            (os.POSIX_SPAWN_CLOSE, stdin_descriptor),
            (os.POSIX_SPAWN_CLOSE, stderr_descriptor),
        )
        original_cwd = os.open(
            ".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        fixed_cwd = -1
        try:
            fixed_cwd = os.open(
                _REPO_ROOT,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            os.fchdir(fixed_cwd)
            pid = os.posix_spawn(
                str(executable),
                command,
                _fixed_cli_environment(),
                file_actions=file_actions,
                setpgroup=0,
                setsigmask=child_signal_mask,
                setsigdef=_CHILD_DEFAULT_SIGNALS,
            )
        finally:
            os.fchdir(original_cwd)
            if fixed_cwd >= 0:
                os.close(fixed_cwd)
            os.close(original_cwd)
        os.close(write_descriptor)
        descriptors.remove(write_descriptor)
        status, stdout, failed = _drain_spawned_child(
            pid,
            read_descriptor,
            deadline=time.monotonic() + _CHILD_TIMEOUT_SECONDS,
        )
        if failed:
            raise AdoptionError("host_cli_operation_failed")
        return subprocess.CompletedProcess(
            command, os.waitstatus_to_exitcode(status), stdout, b""
        )
    except OSError as exc:
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        raise AdoptionError("host_cli_operation_failed") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _drain_spawned_child(
    pid: int,
    descriptor: int,
    *,
    deadline: float,
) -> tuple[int, bytes, bool]:
    output = bytearray()
    status: int | None = None
    eof = False
    failed = False
    terminal_drain_deadline: float | None = None
    while status is None or not eof:
        while not eof:
            now = time.monotonic()
            if (
                terminal_drain_deadline is not None
                and now >= terminal_drain_deadline
            ):
                return status, bytes(output), True
            if terminal_drain_deadline is None and now >= deadline:
                failed = True
                terminal_drain_deadline = now + _CHILD_TERM_GRACE_SECONDS
                status = _terminate_spawned_group(pid, status)
                continue
            try:
                chunk = os.read(descriptor, 64 * 1024)
            except BlockingIOError:
                break
            if not chunk:
                eof = True
                break
            if (
                terminal_drain_deadline is not None
                and time.monotonic() >= terminal_drain_deadline
            ):
                return status, bytes(output), True
            remaining = _MAX_COMMAND_OUTPUT - len(output)
            if len(chunk) > remaining:
                output.extend(chunk[:remaining])
                failed = True
                if terminal_drain_deadline is None:
                    terminal_drain_deadline = (
                        time.monotonic() + _CHILD_TERM_GRACE_SECONDS
                    )
                    status = _terminate_spawned_group(pid, status)
            elif not failed:
                output.extend(chunk)
        if status is None:
            waited_pid, observed_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = observed_status
        now = time.monotonic()
        if (
            terminal_drain_deadline is None
            and now >= deadline
            and (status is None or not eof)
        ):
            failed = True
            terminal_drain_deadline = now + _CHILD_TERM_GRACE_SECONDS
            status = _terminate_spawned_group(pid, status)
        if (
            terminal_drain_deadline is not None
            and time.monotonic() >= terminal_drain_deadline
            and not eof
        ):
            return status, bytes(output), True
        if status is not None and eof:
            break
        select.select((descriptor,), (), (), 0.01)
    return status, bytes(output), failed


def _terminate_spawned_group(pid: int, status: int | None) -> int:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    if status is None:
        status = _waitpid_until(pid, time.monotonic() + _CHILD_TERM_GRACE_SECONDS)
    group_deadline = time.monotonic() + _CHILD_TERM_GRACE_SECONDS
    group_alive = True
    while group_alive and time.monotonic() < group_deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            group_alive = False
            break
        except PermissionError:
            break
        time.sleep(0.01)
    if status is None or group_alive:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if status is None:
            _waited_pid, status = os.waitpid(pid, 0)
    return status


def _waitpid_until(pid: int, deadline: float) -> int | None:
    while True:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return status
        if time.monotonic() >= deadline:
            return None
        try:
            time.sleep(0.01)
        except InterruptedError:
            continue


def _admit_fixed_cli(
    spec: _FixedCliSpec, transaction_root: Path
) -> tuple[_FixedCliIdentity, ...]:
    before = _capture_fixed_cli(spec)
    completed = _invoke_fixed_cli(
        spec,
        ("--version",),
        expected_final=before[-1],
        transaction_root=transaction_root,
    )
    if (
        completed.returncode != 0
        or len(completed.stdout) > _MAX_VERSION_OUTPUT
        or completed.stdout != spec.version_output
        or _capture_fixed_cli(spec) != before
    ):
        raise AdoptionError("host_cli_drift")
    return before


def _safe_plugin_version(value: object) -> str | None:
    if type(value) is not str or _PLUGIN_VERSION_RE.fullmatch(value) is None:
        return None
    if value in {".", ".."} or "/" in value or "\\" in value:
        return None
    return value


def _generated_candidate_quarantine_handle() -> str:
    version = authority.PLUGIN_VERSION.split(".")
    if len(version) != 3 or any(not part.isdigit() for part in version):
        raise AdoptionError("candidate_version_invalid")
    return f"generated-cache-v{int(version[2]):03d}"


def _safe_sha256(value: object) -> str | None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        return None
    return value


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    handle: str
    version: str
    cache_digest: str
    full_digest: str
    identity_digest: str
    in_use_present: bool

    def projection(self) -> dict[str, object]:
        return {
            "cache_digest": self.cache_digest,
            "full_digest": self.full_digest,
            "handle": self.handle,
            "identity_digest": self.identity_digest,
            "in_use_present": self.in_use_present,
            "version": self.version,
        }

    @classmethod
    def from_projection(cls, value: object) -> "QuarantineEntry":
        if type(value) is not dict or set(value) != {
            "cache_digest",
            "full_digest",
            "handle",
            "identity_digest",
            "in_use_present",
            "version",
        }:
            raise AdoptionError("journal_state_projection_invalid")
        version = value["version"]
        if (
            version not in TARGET_CACHE_VERSIONS
            or value["handle"] != TARGET_CACHE_HANDLES[version]
            or _safe_sha256(value["cache_digest"]) is None
            or _safe_sha256(value["full_digest"]) is None
            or _safe_sha256(value["identity_digest"]) is None
            or type(value["in_use_present"]) is not bool
        ):
            raise AdoptionError("journal_state_projection_invalid")
        return cls(
            handle=value["handle"],
            version=version,
            cache_digest=value["cache_digest"],
            full_digest=value["full_digest"],
            identity_digest=value["identity_digest"],
            in_use_present=value["in_use_present"],
        )


@dataclass(frozen=True, slots=True)
class HostState:
    host: str
    marketplace_present: bool
    marketplace_digest: str | None
    marketplace_binding_digest: str | None
    plugin_present: bool
    plugin_version: str | None
    active: bool
    cache_digest: str | None
    quarantine_entries: tuple[QuarantineEntry, ...] = ()
    foreign_cache_leaf_count: int = 0
    invalid_cache_leaf_count: int = 0
    ambiguous_cache_leaf_count: int = 0

    def projection(self) -> dict[str, object]:
        return {
            "active": self.active,
            "cache_digest": self.cache_digest,
            "host": self.host,
            "marketplace_digest": self.marketplace_digest,
            "marketplace_binding_digest": self.marketplace_binding_digest,
            "marketplace_present": self.marketplace_present,
            "plugin_present": self.plugin_present,
            "plugin_version": self.plugin_version,
            "quarantine_entries": [
                entry.projection() for entry in self.quarantine_entries
            ],
            "foreign_cache_leaf_count": self.foreign_cache_leaf_count,
            "invalid_cache_leaf_count": self.invalid_cache_leaf_count,
            "ambiguous_cache_leaf_count": self.ambiguous_cache_leaf_count,
        }

    @classmethod
    def from_projection(cls, value: object, *, expected_host: str) -> "HostState":
        if type(value) is not dict or set(value) != {
            "active",
            "cache_digest",
            "host",
            "marketplace_digest",
            "marketplace_binding_digest",
            "marketplace_present",
            "plugin_present",
            "plugin_version",
            "quarantine_entries",
            "foreign_cache_leaf_count",
            "invalid_cache_leaf_count",
            "ambiguous_cache_leaf_count",
        }:
            raise AdoptionError("journal_state_projection_invalid")
        cache_digest = value["cache_digest"]
        marketplace_digest = value["marketplace_digest"]
        marketplace_binding_digest = value["marketplace_binding_digest"]
        plugin_version = value["plugin_version"]
        quarantine_value = value["quarantine_entries"]
        if type(quarantine_value) is not list:
            raise AdoptionError("journal_state_projection_invalid")
        quarantine_entries = tuple(
            QuarantineEntry.from_projection(item) for item in quarantine_value
        )
        if (
            value["host"] != expected_host
            or type(value["active"]) is not bool
            or type(value["marketplace_present"]) is not bool
            or type(value["plugin_present"]) is not bool
            or (plugin_version is not None and _safe_plugin_version(plugin_version) is None)
            or (
                cache_digest is not None
                and (
                    type(cache_digest) is not str
                    or len(cache_digest) != 64
                    or any(character not in "0123456789abcdef" for character in cache_digest)
                )
            )
            or (
                marketplace_digest is not None
                and (
                    type(marketplace_digest) is not str
                    or len(marketplace_digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in marketplace_digest
                    )
                )
            )
            or (
                marketplace_binding_digest is not None
                and (
                    type(marketplace_binding_digest) is not str
                    or len(marketplace_binding_digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in marketplace_binding_digest
                    )
                )
            )
            or value["marketplace_present"] is not (
                marketplace_digest is not None
                and marketplace_binding_digest is not None
            )
            or value["plugin_present"] is not (
                plugin_version is not None and cache_digest is not None
            )
            or (value["active"] and not value["plugin_present"])
            or any(
                type(value[key]) is not int or value[key] < 0 or value[key] > 4096
                for key in (
                    "foreign_cache_leaf_count",
                    "invalid_cache_leaf_count",
                    "ambiguous_cache_leaf_count",
                )
            )
            or len({entry.version for entry in quarantine_entries})
            != len(quarantine_entries)
            or tuple(entry.version for entry in quarantine_entries)
            != tuple(
                version
                for version in TARGET_CACHE_VERSIONS
                if version in {entry.version for entry in quarantine_entries}
            )
        ):
            raise AdoptionError("journal_state_projection_invalid")
        return cls(
            host=expected_host,
            marketplace_present=value["marketplace_present"],
            marketplace_digest=marketplace_digest,
            marketplace_binding_digest=marketplace_binding_digest,
            plugin_present=value["plugin_present"],
            plugin_version=plugin_version,
            active=value["active"],
            cache_digest=cache_digest,
            quarantine_entries=quarantine_entries,
            foreign_cache_leaf_count=value["foreign_cache_leaf_count"],
            invalid_cache_leaf_count=value["invalid_cache_leaf_count"],
            ambiguous_cache_leaf_count=value["ambiguous_cache_leaf_count"],
        )


@dataclass(frozen=True, slots=True)
class TerminalHostState:
    """Sanitized read-only identity of one installed 0.1.48 host state."""

    host: str
    plugin_version: str
    operational_adoption: str
    registry_state: str
    cache_digest: str
    cache_identity_digest: str
    marketplace_digest: str
    marketplace_identity_digest: str
    orphan_marker_digest: str | None

    def projection(self) -> dict[str, object]:
        return {
            "cache_digest": self.cache_digest,
            "cache_identity_digest": self.cache_identity_digest,
            "host": self.host,
            "marketplace_digest": self.marketplace_digest,
            "marketplace_identity_digest": self.marketplace_identity_digest,
            "operational_adoption": self.operational_adoption,
            "orphan_marker_digest": self.orphan_marker_digest,
            "plugin_version": self.plugin_version,
            "registry_state": self.registry_state,
        }

    @classmethod
    def from_projection(
        cls,
        value: object,
        *,
        expected_host: str,
    ) -> "TerminalHostState":
        keys = {
            "cache_digest",
            "cache_identity_digest",
            "host",
            "marketplace_digest",
            "marketplace_identity_digest",
            "operational_adoption",
            "orphan_marker_digest",
            "plugin_version",
            "registry_state",
        }
        if type(value) is not dict or set(value) != keys:
            raise AdoptionError("terminal_state_projection_invalid")
        orphan_digest = value["orphan_marker_digest"]
        if (
            value["host"] != expected_host
            or value["plugin_version"] != authority.TERMINAL_PLUGIN_VERSION
            or value["operational_adoption"] not in _TERMINAL_OPERATIONAL_STATES
            or value["registry_state"] not in _TERMINAL_REGISTRY_STATES
            or any(
                _safe_sha256(value[key]) is None
                for key in (
                    "cache_digest",
                    "cache_identity_digest",
                    "marketplace_digest",
                    "marketplace_identity_digest",
                )
            )
            or (orphan_digest is not None and _safe_sha256(orphan_digest) is None)
            or (
                value["operational_adoption"] == "orphaned"
                and orphan_digest is None
            )
            or (
                value["operational_adoption"] != "orphaned"
                and orphan_digest is not None
            )
        ):
            raise AdoptionError("terminal_state_projection_invalid")
        return cls(**value)

    def identity_digest(self) -> str:
        checked = self.from_projection(self.projection(), expected_host=self.host)
        return _canonical_digest(checked.projection())


@dataclass(frozen=True, slots=True)
class CanonicalRecoveryState:
    """Path-free identity of the clean executable FP1 recovery anchor."""

    anchor: str
    source_revision: str
    source_bundle_digest: str
    source_tree_digest: str
    interpreter_digest: str
    clean: bool
    interpreter_executable: bool

    def projection(self) -> dict[str, object]:
        return {
            "anchor": self.anchor,
            "clean": self.clean,
            "interpreter_digest": self.interpreter_digest,
            "interpreter_executable": self.interpreter_executable,
            "source_bundle_digest": self.source_bundle_digest,
            "source_revision": self.source_revision,
            "source_tree_digest": self.source_tree_digest,
        }

    @classmethod
    def from_projection(cls, value: object) -> "CanonicalRecoveryState":
        keys = {
            "anchor",
            "clean",
            "interpreter_digest",
            "interpreter_executable",
            "source_bundle_digest",
            "source_revision",
            "source_tree_digest",
        }
        if type(value) is not dict or set(value) != keys:
            raise AdoptionError("canonical_recovery_projection_invalid")
        if (
            value["anchor"] != _TERMINAL_ANCHOR
            or value["source_revision"] != authority.TERMINAL_SOURCE_REVISION
            or any(
                _safe_sha256(value[key]) is None
                for key in (
                    "source_bundle_digest",
                    "source_tree_digest",
                    "interpreter_digest",
                )
            )
            or type(value["clean"]) is not bool
            or type(value["interpreter_executable"]) is not bool
        ):
            raise AdoptionError("canonical_recovery_projection_invalid")
        return cls(**value)

    def identity_digest(self) -> str:
        checked = self.from_projection(self.projection())
        return _canonical_digest(checked.projection())


class TerminalHostObserver(Protocol):
    name: str

    def observe(self) -> TerminalHostState: ...


class CanonicalRecoveryObserver(Protocol):
    def observe(self) -> CanonicalRecoveryState: ...


class AdoptionHostAdapter(Protocol):
    name: str

    def observe(self) -> HostState: ...

    def prepare(self, transaction_id: str, expected_after: HostState) -> None: ...

    def apply(
        self,
        transaction_id: str,
        expected_before: HostState,
        expected_after: HostState,
    ) -> HostState: ...

    def verify(self, transaction_id: str, expected_after: HostState) -> HostState: ...

    def rollback(self, transaction_id: str, expected_before: HostState) -> HostState: ...


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("ascii")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(authority.canonical_bytes(value)).hexdigest()


def _git_text(cwd: Path, args: tuple[str, ...], *, timeout: int = 10) -> str:
    """Run the fixed local Git binary without inheriting caller state."""

    try:
        completed = subprocess.run(
            ("/usr/bin/git", *args),
            cwd=cwd,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            timeout=timeout,
        )
        output = completed.stdout.decode("utf-8", "strict")
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        raise AdoptionError("predecessor_source_unavailable") from exc
    if completed.returncode != 0 or len(completed.stdout) > _MAX_COMMAND_OUTPUT:
        raise AdoptionError("predecessor_source_unavailable")
    return output


def _predecessor_binding_descriptor() -> dict[str, object]:
    """Return the path-free identity admitted for the one boot transition."""

    return {
        "branch": PREDECESSOR_BRANCH,
        "bundle_tree_oid": PREDECESSOR_BUNDLE_TREE_OID,
        "marketplace_id": authority.MARKETPLACE_ID,
        "marketplace_manifest_blob_oid": PREDECESSOR_MARKETPLACE_MANIFEST_BLOB_OID,
        "marketplace_manifest_digest": PREDECESSOR_MARKETPLACE_MANIFEST_DIGEST,
        "plugin_bundle_digest": PREDECESSOR_BUNDLE_DIGEST,
        "plugin_id": authority.PLUGIN_ID,
        "plugin_version": PREDECESSOR_VERSION,
        "schema": SOURCE_BINDING_SCHEMA,
        "source_manifest_digest": PREDECESSOR_SOURCE_MANIFEST_DIGEST,
        "source_manifest_blob_oid": PREDECESSOR_SOURCE_MANIFEST_BLOB_OID,
        "source_revision": PREDECESSOR_REVISION,
    }


def _predecessor_binding_digest() -> str:
    return _canonical_digest(_predecessor_binding_descriptor())


def _predecessor_marketplace_digest() -> str:
    """Return a path-free digest of the verified direct-source marketplace."""

    descriptor = _predecessor_binding_descriptor()
    return _canonical_digest({
        "binding_digest": _predecessor_binding_digest(),
        "bundle_tree_oid": descriptor["bundle_tree_oid"],
        "marketplace_manifest_blob_oid": descriptor[
            "marketplace_manifest_blob_oid"
        ],
        "marketplace_manifest_digest": descriptor["marketplace_manifest_digest"],
        "plugin_bundle_digest": descriptor["plugin_bundle_digest"],
        "schema": PREDECESSOR_MARKETPLACE_IDENTITY_SCHEMA,
        "source_manifest_blob_oid": descriptor["source_manifest_blob_oid"],
        "source_manifest_digest": descriptor["source_manifest_digest"],
        "source_revision": descriptor["source_revision"],
    })


def _candidate_binding_digest(
    *, source_revision: str, source_bundle_digest: str, marketplace_digest: str
) -> str:
    return _canonical_digest({
        "marketplace_digest": marketplace_digest,
        "marketplace_id": authority.MARKETPLACE_ID,
        "plugin_id": authority.PLUGIN_ID,
        "plugin_version": authority.PLUGIN_VERSION,
        "schema": SOURCE_BINDING_SCHEMA,
        "source_bundle_digest": source_bundle_digest,
        "source_revision": source_revision,
    })


def _source_file_digest(root: Path, relative: Path) -> str:
    descriptors: list[int] = []
    try:
        descriptor = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptors.append(descriptor)
        root_info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or root_info.st_mode & 0o022
        ):
            raise AdoptionError("predecessor_source_drift")
        for component in relative.parts[:-1]:
            if component in {"", ".", ".."} or "/" in component or "\\" in component:
                raise AdoptionError("predecessor_source_drift")
            descriptor = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
            ):
                raise AdoptionError("predecessor_source_drift")
        leaf = relative.parts[-1]
        if leaf in {"", ".", ".."} or "/" in leaf or "\\" in leaf:
            raise AdoptionError("predecessor_source_drift")
        file_descriptor = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AdoptionError("predecessor_source_drift")
        hasher = hashlib.sha256()
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
        after = os.fstat(file_descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise AdoptionError("predecessor_source_drift")
        return hasher.hexdigest()
    except OSError as exc:
        raise AdoptionError("predecessor_source_drift") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _resolve_predecessor_source() -> Path:
    """Resolve one exact clean sibling worktree, independent of caller cwd."""

    family_root = _REPO_ROOT.parent.resolve(strict=True)
    if family_root.name != "hermes-agent":
        raise AdoptionError("predecessor_source_containment_failed")
    raw = _git_text(_REPO_ROOT, ("worktree", "list", "--porcelain", "-z"))
    matches: list[Path] = []
    for record in raw.split("\0\0"):
        fields = record.strip("\0").split("\0")
        values = {
            field.split(" ", 1)[0]: field.split(" ", 1)[1]
            for field in fields
            if " " in field
        }
        if (
            values.get("HEAD") == PREDECESSOR_REVISION
            and values.get("branch") == PREDECESSOR_BRANCH
            and type(values.get("worktree")) is str
        ):
            matches.append(Path(values["worktree"]))
    if len(matches) != 1:
        raise AdoptionError("predecessor_source_ambiguous")
    lexical = matches[0]
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise AdoptionError("predecessor_source_unavailable") from exc
    if (
        not lexical.is_absolute()
        or resolved.parent != family_root
        or resolved.name != PREDECESSOR_WORKTREE_LEAF
        or resolved == _REPO_ROOT.resolve(strict=True)
    ):
        raise AdoptionError("predecessor_source_containment_failed")
    root_before = resolved.lstat()
    family_before = family_root.lstat()
    if (
        stat.S_ISLNK(root_before.st_mode)
        or not stat.S_ISDIR(root_before.st_mode)
        or root_before.st_uid != os.getuid()
        or root_before.st_mode & 0o022
        or stat.S_ISLNK(family_before.st_mode)
        or not stat.S_ISDIR(family_before.st_mode)
        or family_before.st_uid != os.getuid()
        or family_before.st_mode & 0o022
    ):
        raise AdoptionError("predecessor_source_containment_failed")
    expected = {
        "branch": PREDECESSOR_BRANCH,
        "common": str(
            Path(
                _git_text(
                    _REPO_ROOT,
                    ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                ).strip()
            ).resolve(strict=True)
        ),
        "head": PREDECESSOR_REVISION,
        "root": str(resolved),
        "status": "",
    }
    observed = {
        "branch": _git_text(resolved, ("symbolic-ref", "-q", "HEAD")).strip(),
        "common": str(
            Path(
                _git_text(
                    resolved,
                    ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                ).strip()
            ).resolve(strict=True)
        ),
        "head": _git_text(resolved, ("rev-parse", "--verify", "HEAD^{commit}")).strip(),
        "root": str(
            Path(_git_text(resolved, ("rev-parse", "--show-toplevel")).strip()).resolve(
                strict=True
            )
        ),
        "status": _git_text(
            resolved, ("status", "--porcelain=v1", "--untracked-files=normal")
        ),
    }
    if observed != expected:
        raise AdoptionError("predecessor_source_drift")
    object_identity = {
        "bundle_tree": _git_text(
            resolved,
            ("rev-parse", f"{PREDECESSOR_REVISION}:distribution/{authority.PLUGIN_ID}"),
        ).strip(),
        "marketplace_manifest": _git_text(
            resolved,
            ("rev-parse", f"{PREDECESSOR_REVISION}:.claude-plugin/marketplace.json"),
        ).strip(),
        "source_manifest": _git_text(
            resolved,
            (
                "rev-parse",
                f"{PREDECESSOR_REVISION}:distribution/{authority.PLUGIN_ID}/SOURCE_MANIFEST.json",
            ),
        ).strip(),
    }
    if object_identity != {
        "bundle_tree": PREDECESSOR_BUNDLE_TREE_OID,
        "marketplace_manifest": PREDECESSOR_MARKETPLACE_MANIFEST_BLOB_OID,
        "source_manifest": PREDECESSOR_SOURCE_MANIFEST_BLOB_OID,
    }:
        raise AdoptionError("predecessor_source_drift")
    bundle = resolved / "distribution" / authority.PLUGIN_ID
    if (
        _source_file_digest(
            resolved, Path(".claude-plugin/marketplace.json")
        )
        != PREDECESSOR_MARKETPLACE_MANIFEST_DIGEST
        or _source_file_digest(
            resolved,
            Path("distribution") / authority.PLUGIN_ID / "SOURCE_MANIFEST.json",
        )
        != PREDECESSOR_SOURCE_MANIFEST_DIGEST
        or _tree_digest(bundle, ignored=frozenset({".in_use"}))
        != PREDECESSOR_BUNDLE_DIGEST
    ):
        raise AdoptionError("predecessor_source_drift")
    root_after = resolved.lstat()
    family_after = family_root.lstat()
    if (
        (root_before.st_dev, root_before.st_ino, root_before.st_mode)
        != (root_after.st_dev, root_after.st_ino, root_after.st_mode)
        or (family_before.st_dev, family_before.st_ino, family_before.st_mode)
        != (family_after.st_dev, family_after.st_ino, family_after.st_mode)
    ):
        raise AdoptionError("predecessor_source_drift")
    return resolved


def _states_digest(states: Sequence[HostState]) -> str:
    if tuple(state.host for state in states) != HOST_ORDER:
        raise AdoptionError("host_order_mismatch")
    return _canonical_digest([state.projection() for state in states])


def _terminal_states_digest(states: Sequence[TerminalHostState]) -> str:
    if tuple(state.host for state in states) != HOST_ORDER:
        raise AdoptionError("host_order_mismatch")
    checked = [
        TerminalHostState.from_projection(
            state.projection(),
            expected_host=host,
        )
        for state, host in zip(states, HOST_ORDER, strict=True)
    ]
    if checked[0].identity_digest() == checked[1].identity_digest():
        raise AdoptionError("terminal_host_identity_collision")
    return _canonical_digest([state.projection() for state in checked])


def _terminal_current_identity_digest(
    states: Sequence[TerminalHostState],
) -> str:
    if tuple(state.host for state in states) != HOST_ORDER:
        raise AdoptionError("host_order_mismatch")
    return _canonical_digest({
        "claude": states[1].identity_digest(),
        "codex": states[0].identity_digest(),
        "target_set": list(HOST_ORDER),
    })


def _admit_canonical_recovery(state: CanonicalRecoveryState) -> CanonicalRecoveryState:
    checked = CanonicalRecoveryState.from_projection(state.projection())
    if not checked.interpreter_executable:
        raise AdoptionError("canonical_recovery_interpreter_unavailable")
    if not checked.clean:
        raise AdoptionError("canonical_recovery_source_dirty")
    return checked


def _safe_transaction_id(value: object) -> str:
    checked = authority._safe_id(value)
    if checked is None:
        raise AdoptionError("transaction_identity_invalid")
    return checked


def _fixed_user_home() -> Path:
    home = Path(str(pwd.getpwuid(os.getuid()).pw_dir)).absolute()
    try:
        info = home.lstat()
    except OSError as exc:
        raise AdoptionError("host_home_unavailable") from exc
    if (
        home.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
    ):
        raise AdoptionError("host_home_unavailable")
    return home


def _fixed_state_root() -> Path:
    version_handle = TARGET_CACHE_HANDLES.get(authority.PLUGIN_VERSION)
    if version_handle is None or not version_handle.startswith("target-cache-"):
        raise AdoptionError("state_root_version_unavailable")
    return (
        _fixed_user_home()
        / ".hermes"
        / "profiles"
        / "orch"
        / f"plugin-adoption-{version_handle.removeprefix('target-cache-')}"
    )


def _fixed_terminal_state_root() -> Path:
    """Return the dedicated fresh 0.1.48 terminal transaction root."""

    return (
        _fixed_user_home()
        / ".hermes"
        / "profiles"
        / "orch"
        / "plugin-adoption-terminal-v048"
    )


def _fixed_terminal_source_root() -> Path:
    """Return the immutable ordinary 0.1.48 marketplace transaction root."""

    return (
        _fixed_user_home()
        / ".hermes"
        / "profiles"
        / "orch"
        / "plugin-adoption-v048"
    )


def _fixed_canonical_recovery_root() -> Path:
    """Return the one fixed FP1 recovery checkout admitted by terminalize."""

    return (
        _fixed_user_home()
        / "ORCH-Next"
        / "worktrees"
        / "hermes-agent"
        / "hermes-fp1-state-schema-20260814"
    )


def _previous_state_root() -> Path:
    base = _fixed_user_home() / ".hermes" / "profiles" / "orch"
    if authority.PREVIOUS_TERMINAL_PLUGIN_VERSION == "0.1.22":
        return base / "plugin-adoption"
    version_handle = TARGET_CACHE_HANDLES.get(
        authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
    )
    if version_handle is None or not version_handle.startswith("target-cache-"):
        raise AdoptionError("previous_state_root_version_unavailable")
    return base / f"plugin-adoption-{version_handle.removeprefix('target-cache-')}"


def _secure_user_directory_chain(path: Path, *, create: bool) -> bool:
    """Validate/create a user-home-relative directory chain without symlinks."""

    home = _fixed_user_home()
    try:
        relative = path.absolute().relative_to(home)
    except ValueError:
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(home, flags)
    try:
        parts = relative.parts
        for index, part in enumerate(parts):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                successor = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise AdoptionError("protected_state_unavailable") from exc
            info = os.fstat(successor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or (
                    index == len(parts) - 1
                    and stat.S_IMODE(info.st_mode) != 0o700
                )
            ):
                os.close(successor)
                raise AdoptionError("protected_state_drift")
            os.close(descriptor)
            descriptor = successor
    finally:
        os.close(descriptor)
    return True


def _validate_fixed_host_chain(path: Path, *, allow_missing: bool) -> None:
    home = _fixed_user_home()
    try:
        relative = path.absolute().relative_to(home)
    except ValueError as exc:
        raise AdoptionError("host_root_drift") from exc
    current = home
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return
            raise AdoptionError("host_root_drift")
        except OSError as exc:
            raise AdoptionError("host_root_drift") from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
        ):
            raise AdoptionError("host_root_drift")


def _lstat_admitted_directory(path: Path, *, create: bool = False) -> None:
    if _secure_user_directory_chain(path, create=create):
        return
    if create:
        missing: list[Path] = []
        current = path.absolute()
        while True:
            try:
                info = current.lstat()
            except FileNotFoundError:
                missing.append(current)
                parent = current.parent
                if parent == current:
                    raise AdoptionError("protected_state_unavailable")
                current = parent
                continue
            except OSError as exc:
                raise AdoptionError("protected_state_unavailable") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise AdoptionError("protected_state_unavailable")
            break
        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise AdoptionError("protected_state_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AdoptionError("protected_state_drift")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_directory_between_exclusive(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> None:
    """Atomically publish a sibling directory without replacing a destination."""

    if any(
        not name
        or name in {".", ".."}
        or "/" in name
        or "\0" in name
        for name in (source_name, destination_name)
    ):
        raise AdoptionError("rollback_capture_drift")
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = library.renameatx_np
            flags = 0x00000004  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            rename = library.renameat2
            flags = 0x00000001  # RENAME_NOREPLACE
        else:
            raise AdoptionError("rollback_capture_unsupported")
    except AttributeError as exc:
        raise AdoptionError("rollback_capture_unsupported") from exc
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    result = rename(
        source_parent_descriptor,
        os.fsencode(source_name),
        destination_parent_descriptor,
        os.fsencode(destination_name),
        flags,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, "exclusive rollback publish failed")


def _rename_bound_directory_between_exclusive(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
    *,
    failure: str,
    expected_identity_digest: str | None = None,
) -> None:
    """Rename one held directory inode and evacuate a swapped destination."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    child_descriptor = -1
    try:
        child_descriptor = os.open(
            source_name,
            flags,
            dir_fd=source_parent_descriptor,
        )
        bound_identity = _archive_stat_identity(os.fstat(child_descriptor))
        if expected_identity_digest is not None and (
            _safe_sha256(expected_identity_digest) is None
            or _canonical_digest({
                "device": bound_identity[0],
                "group": bound_identity[4],
                "inode": bound_identity[1],
                "mode": stat.S_IMODE(bound_identity[2]),
                "owner": bound_identity[3],
            })
            != expected_identity_digest
        ):
            raise AdoptionError(failure)
        path_identity = _archive_stat_identity(
            os.stat(
                source_name,
                dir_fd=source_parent_descriptor,
                follow_symlinks=False,
            )
        )
        if not _terminal_rename_identity_matches(
            bound_identity, path_identity
        ):
            raise AdoptionError(failure)
        _rename_directory_between_exclusive(
            source_parent_descriptor,
            source_name,
            destination_parent_descriptor,
            destination_name,
        )
        destination_identity = _archive_stat_identity(
            os.stat(
                destination_name,
                dir_fd=destination_parent_descriptor,
                follow_symlinks=False,
            )
        )
        held_identity = _archive_stat_identity(os.fstat(child_descriptor))
        if _terminal_rename_identity_matches(
            bound_identity, destination_identity
        ) and _terminal_rename_identity_matches(
            bound_identity, held_identity
        ):
            return

        # Keep a substituted inode out of the live destination before
        # reporting the typed CAS failure.  Prefer the original source leaf;
        # if it was repopulated, retain the displaced inode under a bounded
        # content-derived residue rather than overwriting either entry.
        try:
            os.stat(
                source_name,
                dir_fd=source_parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            recovery_name = source_name
        else:
            recovery_name = (
                ".rejected-"
                + hashlib.sha256(
                    (source_name + "\0" + destination_name).encode("utf-8")
                    + repr(destination_identity).encode("ascii")
                ).hexdigest()[:24]
            )
        try:
            _rename_directory_between_exclusive(
                destination_parent_descriptor,
                destination_name,
                source_parent_descriptor,
                recovery_name,
            )
            recovered_identity = _archive_stat_identity(
                os.stat(
                    recovery_name,
                    dir_fd=source_parent_descriptor,
                    follow_symlinks=False,
                )
            )
        except (OSError, AdoptionError) as exc:
            raise AdoptionError(f"{failure}_ambiguous") from exc
        if not _terminal_rename_identity_matches(
            destination_identity, recovered_identity
        ):
            raise AdoptionError(f"{failure}_ambiguous")
        raise AdoptionError(failure)
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError(failure) from exc
    finally:
        if child_descriptor >= 0:
            os.close(child_descriptor)


def _rename_directory_exclusive(
    parent_descriptor: int,
    source_name: str,
    destination_name: str,
) -> None:
    _rename_directory_between_exclusive(
        parent_descriptor,
        source_name,
        parent_descriptor,
        destination_name,
    )


def _exchange_directory_entries(
    parent_descriptor: int,
    first_name: str,
    second_name: str,
) -> None:
    """Atomically exchange two sibling entries without discarding either."""

    if any(
        not name
        or name in {".", ".."}
        or "/" in name
        or "\0" in name
        for name in (first_name, second_name)
    ):
        raise AdoptionError("terminal_final_exchange_unsupported")
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = library.renameatx_np
        elif sys.platform.startswith("linux"):
            rename = library.renameat2
        else:
            raise AdoptionError("terminal_final_exchange_unsupported")
    except AttributeError as exc:
        raise AdoptionError("terminal_final_exchange_unsupported") from exc
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    result = rename(
        parent_descriptor,
        os.fsencode(first_name),
        parent_descriptor,
        os.fsencode(second_name),
        0x00000002,  # RENAME_SWAP (Darwin) / RENAME_EXCHANGE (Linux)
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, "terminal final exchange failed")


def _atomic_private_write(path: Path, content: bytes) -> None:
    _lstat_admitted_directory(path.parent, create=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _private_file_bytes(path: Path, *, maximum: int = 512 * 1024) -> bytes:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = os.open(path.parent, directory_flags)
    descriptor = -1
    try:
        parent_before = os.fstat(parent_descriptor)
        parent_path_before = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent_before.st_mode)
            or parent_before.st_uid != os.getuid()
            or stat.S_IMODE(parent_before.st_mode) != 0o700
            or (parent_before.st_dev, parent_before.st_ino, parent_before.st_mode)
            != (
                parent_path_before.st_dev,
                parent_path_before.st_ino,
                parent_path_before.st_mode,
            )
        ):
            raise AdoptionError("journal_drift")
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        path_before = os.stat(
            path.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > maximum
            or (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_uid,
                before.st_gid,
                before.st_nlink,
                before.st_size,
            )
            != (
                path_before.st_dev,
                path_before.st_ino,
                path_before.st_mode,
                path_before.st_uid,
                path_before.st_gid,
                path_before.st_nlink,
                path_before.st_size,
            )
        ):
            raise AdoptionError("journal_drift")
        content = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        path_after = os.stat(
            path.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        parent_after = os.fstat(parent_descriptor)
        parent_path_after = path.parent.lstat()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
    if len(content) > maximum or (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_uid,
        before.st_gid,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_uid,
        after.st_gid,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_uid,
        after.st_gid,
        after.st_nlink,
        after.st_size,
    ) != (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_mode,
        path_after.st_uid,
        path_after.st_gid,
        path_after.st_nlink,
        path_after.st_size,
    ) or (
        parent_before.st_dev,
        parent_before.st_ino,
        parent_before.st_mode,
    ) != (
        parent_after.st_dev,
        parent_after.st_ino,
        parent_after.st_mode,
    ) or (
        parent_after.st_dev,
        parent_after.st_ino,
        parent_after.st_mode,
    ) != (
        parent_path_after.st_dev,
        parent_path_after.st_ino,
        parent_path_after.st_mode,
    ):
        raise AdoptionError("journal_drift")
    return content


def _write_private_file_exclusive(
    path: Path,
    content: bytes,
    *,
    failure_code: str,
) -> None:
    _lstat_admitted_directory(path.parent, create=True)
    parent_descriptor = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary = f".{path.name}.{secrets.token_hex(16)}"
    descriptor = -1
    published = False
    written_identity: tuple[int, ...] | None = None
    try:
        parent_before = os.fstat(parent_descriptor)
        parent_path_before = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent_before.st_mode)
            or parent_before.st_uid != os.getuid()
            or stat.S_IMODE(parent_before.st_mode) != 0o700
            or (parent_before.st_dev, parent_before.st_ino, parent_before.st_mode)
            != (
                parent_path_before.st_dev,
                parent_path_before.st_ino,
                parent_path_before.st_mode,
            )
        ):
            raise AdoptionError(failure_code)
        try:
            os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError(failure_code)
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "private file write failed")
            view = view[written:]
        os.fsync(descriptor)
        written_info = os.fstat(descriptor)
        written_identity = _archive_stat_identity(written_info)
        if (
            not stat.S_ISREG(written_info.st_mode)
            or written_info.st_uid != os.getuid()
            or stat.S_IMODE(written_info.st_mode) != 0o600
            or written_info.st_nlink != 1
            or written_info.st_size != len(content)
        ):
            raise AdoptionError(failure_code)
        _rename_directory_exclusive(
            parent_descriptor, temporary, path.name
        )
        published_info = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not _terminal_rename_identity_matches(
                written_identity,
                _archive_stat_identity(published_info),
            )
            or not _terminal_rename_identity_matches(
                written_identity,
                _archive_stat_identity(os.fstat(descriptor)),
            )
        ):
            raise AdoptionError(failure_code)
        published = True
        os.fsync(parent_descriptor)
        parent_after = os.fstat(parent_descriptor)
        parent_path_after = path.parent.lstat()
        if (
            parent_before.st_dev,
            parent_before.st_ino,
            parent_before.st_mode,
        ) != (
            parent_after.st_dev,
            parent_after.st_ino,
            parent_after.st_mode,
        ) or (
            parent_after.st_dev,
            parent_after.st_ino,
            parent_after.st_mode,
        ) != (
            parent_path_after.st_dev,
            parent_path_after.st_ino,
            parent_path_after.st_mode,
        ):
            raise AdoptionError(failure_code)
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError(failure_code) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            # A same-UID peer may have exchanged the random leaf after its
            # inode was bound.  Retain any ambiguous residue rather than
            # unlinking a pathname that no longer names our inode.
            pass
        os.close(parent_descriptor)
    if _private_file_bytes(path) != content:
        raise AdoptionError(failure_code)


def _journal_path(root: Path) -> Path:
    return root / "journal.json"


def _terminal_stage_path(root: Path) -> Path:
    return root / _TERMINAL_STAGE_LEAF


def _history_root(root: Path) -> Path:
    return root.parent / "plugin-adoption-history"


def _lock_root(root: Path) -> Path:
    return root.parent / ".plugin-adoption-locks"


def _journal_bytes(record: dict[str, object]) -> bytes:
    if set(record) != _JOURNAL_KEYS:
        raise AdoptionError("journal_contract_mismatch")
    if record["schema"] != JOURNAL_SCHEMA or record["phase"] not in PHASES:
        raise AdoptionError("journal_contract_mismatch")
    for key in (
        "plan_digest",
        "before_state_digest",
        "after_state_digest",
        "rollback_manifest_digest",
        "request_digest",
        "envelope_digest",
    ):
        value = record[key]
        if type(value) is not str or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise AdoptionError("journal_contract_mismatch")
    for key in ("transaction_id", "decision_id"):
        _safe_transaction_id(record[key])
    for key in ("request_b64", "envelope_b64"):
        value = record[key]
        if type(value) is not str or len(value) > 256 * 1024:
            raise AdoptionError("journal_contract_mismatch")
        try:
            base64.b64decode(value, validate=True)
        except Exception as exc:
            raise AdoptionError("journal_contract_mismatch") from exc
    for key in ("before_states", "after_states"):
        value = record[key]
        if type(value) is not list or len(value) != len(HOST_ORDER):
            raise AdoptionError("journal_contract_mismatch")
        states = [
            HostState.from_projection(item, expected_host=host)
            for item, host in zip(value, HOST_ORDER, strict=True)
        ]
        expected_digest = (
            record["before_state_digest"] if key == "before_states" else record["after_state_digest"]
        )
        if _states_digest(states) != expected_digest:
            raise AdoptionError("journal_contract_mismatch")
    # The only opaque fields are the two canonical authority byte strings.
    # No raw config, command, path, prompt, log, or credential field exists.
    return _json_bytes(record)


def _terminal_record_bytes(
    record: dict[str, object],
    *,
    phase: str,
    keys: frozenset[str],
) -> bytes:
    if set(record) != keys:
        raise AdoptionError("terminal_journal_contract_mismatch")
    if (
        record["schema"] != JOURNAL_SCHEMA
        or record["phase"] != phase
        or record["operation"] != authority.TERMINAL_OPERATION
        or record["host_mutation_count"] != 0
        or record["before_state_digest"] != record["after_state_digest"]
        or record["before_states"] != record["after_states"]
    ):
        raise AdoptionError("terminal_journal_contract_mismatch")
    for key in (
        "plan_digest",
        "before_state_digest",
        "after_state_digest",
        "rollback_manifest_digest",
        "request_digest",
        "predecessor_identity_digest",
        "current_identity_digest",
        "canonical_identity_digest",
    ):
        if _safe_sha256(record[key]) is None:
            raise AdoptionError("terminal_journal_contract_mismatch")
    if "envelope_digest" in keys and _safe_sha256(record["envelope_digest"]) is None:
        raise AdoptionError("terminal_journal_contract_mismatch")
    for key in ("transaction_id", "decision_id"):
        _safe_transaction_id(record[key])
    encoded_keys = ["request_b64"]
    if "envelope_b64" in keys:
        encoded_keys.append("envelope_b64")
    decoded: dict[str, bytes] = {}
    for key in encoded_keys:
        value = record[key]
        if type(value) is not str or len(value) > 256 * 1024:
            raise AdoptionError("terminal_journal_contract_mismatch")
        try:
            decoded[key] = base64.b64decode(value, validate=True)
        except Exception as exc:
            raise AdoptionError("terminal_journal_contract_mismatch") from exc
    request_bytes = decoded["request_b64"]
    if hashlib.sha256(request_bytes).hexdigest() != record["request_digest"]:
        raise AdoptionError("terminal_journal_contract_mismatch")
    if "envelope_b64" in decoded and (
        hashlib.sha256(decoded["envelope_b64"]).hexdigest()
        != record["envelope_digest"]
    ):
        raise AdoptionError("terminal_journal_contract_mismatch")
    try:
        request_value = authority._base._parse_canonical_authority_payload(
            request_bytes
        )
        request = authority.validate_terminal_request(request_value)
        if authority.canonical_bytes(request) != request_bytes:
            raise AdoptionError("terminal_journal_contract_mismatch")
    except authority.PluginAdoptionAuthorityError as exc:
        raise AdoptionError("terminal_journal_contract_mismatch") from exc
    states = []
    for key in ("before_states", "after_states"):
        value = record[key]
        if type(value) is not list or len(value) != len(HOST_ORDER):
            raise AdoptionError("terminal_journal_contract_mismatch")
        parsed = [
            TerminalHostState.from_projection(item, expected_host=host)
            for item, host in zip(value, HOST_ORDER, strict=True)
        ]
        if _terminal_states_digest(parsed) != record[f"{key.removesuffix('s')}_digest"]:
            raise AdoptionError("terminal_journal_contract_mismatch")
        states.append(parsed)
    if states[0][0].identity_digest() == states[0][1].identity_digest():
        raise AdoptionError("terminal_host_identity_collision")
    if _terminal_current_identity_digest(states[0]) != record["current_identity_digest"]:
        raise AdoptionError("terminal_journal_contract_mismatch")
    recovery = CanonicalRecoveryState.from_projection(record["canonical_recovery"])
    if recovery.identity_digest() != record["canonical_identity_digest"]:
        raise AdoptionError("terminal_journal_contract_mismatch")
    plan = request["plan"]
    actual = request["actual"]
    rollback_manifest_digest = _canonical_digest({
        "host_order": list(HOST_ORDER),
        "policy": "observation_only_no_host_mutation.v1",
        "states": [state.projection() for state in states[0]],
    })
    predecessor_identity_digest = _canonical_digest({
        "host_state_digests": [
            state.identity_digest() for state in states[0]
        ],
        "operation": authority.TERMINAL_OPERATION,
        "plugin_id": authority.PLUGIN_ID,
        "plugin_version": authority.TERMINAL_PLUGIN_VERSION,
    })
    if (
        actual["transaction_id"] != record["transaction_id"]
        or actual["decision_id"] != record["decision_id"]
        or plan["plan_digest"] != record["plan_digest"]
        or plan["before_state_digest"] != record["before_state_digest"]
        or plan["after_state_digest"] != record["after_state_digest"]
        or plan["rollback_manifest_digest"]
        != record["rollback_manifest_digest"]
        or plan["rollback_manifest_digest"] != rollback_manifest_digest
        or plan["predecessor_identity_digest"]
        != record["predecessor_identity_digest"]
        or plan["predecessor_identity_digest"]
        != predecessor_identity_digest
        or plan["current_identity_digest"] != record["current_identity_digest"]
        or plan["codex_current_state_digest"]
        != states[0][0].identity_digest()
        or plan["claude_current_state_digest"]
        != states[0][1].identity_digest()
        or plan["canonical_identity_digest"]
        != record["canonical_identity_digest"]
        or plan["source_bundle_digest"] != recovery.source_bundle_digest
    ):
        raise AdoptionError("terminal_journal_contract_mismatch")
    return _json_bytes(record)


def _terminal_prepared_bytes(record: dict[str, object]) -> bytes:
    return _terminal_record_bytes(
        record,
        phase="REQUEST_PREPARED",
        keys=_TERMINAL_PREPARED_KEYS,
    )


def _terminal_journal_bytes(record: dict[str, object]) -> bytes:
    return _terminal_record_bytes(
        record,
        phase="COMMITTED",
        keys=_TERMINAL_JOURNAL_KEYS,
    )


def _read_journal(root: Path) -> dict[str, object]:
    try:
        value = json.loads(_private_file_bytes(_journal_path(root)).decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("journal_unavailable") from exc
    if type(value) is not dict:
        raise AdoptionError("journal_contract_mismatch")
    _journal_bytes(value)
    return value


def _read_terminal_journal(root: Path) -> dict[str, object]:
    try:
        value = json.loads(_private_file_bytes(_journal_path(root)).decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("terminal_journal_unavailable") from exc
    if type(value) is not dict:
        raise AdoptionError("terminal_journal_contract_mismatch")
    _terminal_journal_bytes(value)
    return value


def _read_terminal_prepared(root: Path) -> dict[str, object]:
    try:
        value = json.loads(_private_file_bytes(_journal_path(root)).decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("terminal_prepared_unavailable") from exc
    if type(value) is not dict:
        raise AdoptionError("terminal_journal_contract_mismatch")
    _terminal_prepared_bytes(value)
    return value


def _read_terminal_stage(root: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _private_file_bytes(_terminal_stage_path(root)).decode("ascii")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("terminal_stage_unavailable") from exc
    if type(value) is not dict:
        raise AdoptionError("terminal_journal_contract_mismatch")
    _terminal_journal_bytes(value)
    return value


def _read_terminal_record(root: Path) -> dict[str, object]:
    try:
        value = json.loads(_private_file_bytes(_journal_path(root)).decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("terminal_journal_unavailable") from exc
    if type(value) is not dict:
        raise AdoptionError("terminal_journal_contract_mismatch")
    if value.get("phase") == "REQUEST_PREPARED":
        _terminal_prepared_bytes(value)
    elif value.get("phase") == "COMMITTED":
        _terminal_journal_bytes(value)
    else:
        raise AdoptionError("terminal_journal_contract_mismatch")
    return value


def _write_journal(root: Path, record: dict[str, object]) -> None:
    _atomic_private_write(_journal_path(root), _journal_bytes(record))


def _write_journal_exclusive(root: Path, record: dict[str, object]) -> None:
    content = _journal_bytes(record)
    path = _journal_path(root)
    if path.exists() or path.is_symlink():
        raise AdoptionError("ordinary_journal_collision")
    _write_private_file_exclusive(
        path,
        content,
        failure_code="ordinary_journal_collision",
    )


def _terminal_file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _terminal_rename_identity_matches(
    before: tuple[int, ...],
    after: tuple[int, ...],
) -> bool:
    """Admit only the ctime transition caused by renaming a bound file."""

    return before[:-1] == after[:-1] and after[-1] >= before[-1]


def _open_terminal_root(root: Path, *, failure: str) -> int:
    _lstat_admitted_directory(root)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(root, flags)
        info = os.fstat(descriptor)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise AdoptionError(failure) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise AdoptionError(failure)
    return descriptor


def _terminal_temp_snapshot(
    root: Path,
    leaf: str,
) -> tuple[bytes, tuple[int, ...]]:
    """Read one bounded deterministic residue through its admitted root fd."""

    directory = _open_terminal_root(root, failure="terminal_temp_drift")
    descriptor = -1
    try:
        info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > 512 * 1024
        ):
            raise AdoptionError("terminal_temp_drift")
        descriptor = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        identity = _terminal_file_identity(before)
        if identity != _terminal_file_identity(info):
            raise AdoptionError("terminal_temp_drift")
        content = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            content.extend(chunk)
            if len(content) > 512 * 1024:
                raise AdoptionError("terminal_temp_drift")
        after = os.fstat(descriptor)
        path_after = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        if (
            identity != _terminal_file_identity(after)
            or identity != _terminal_file_identity(path_after)
        ):
            raise AdoptionError("terminal_temp_drift")
        return bytes(content), identity
    except OSError as exc:
        raise AdoptionError("terminal_temp_drift") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def _terminal_leaf_record(
    root: Path,
    leaf: str,
    *,
    phase: str,
    failure: str,
) -> tuple[dict[str, object], bytes, tuple[int, ...]]:
    try:
        content, identity = _terminal_temp_snapshot(root, leaf)
        value = json.loads(content.decode("ascii"))
    except (AdoptionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError(failure) from exc
    if type(value) is not dict:
        raise AdoptionError(failure)
    try:
        canonical = (
            _terminal_prepared_bytes(value)
            if phase == "REQUEST_PREPARED"
            else _terminal_journal_bytes(value)
        )
    except AdoptionError as exc:
        raise AdoptionError(failure) from exc
    if canonical != content:
        raise AdoptionError(failure)
    return value, content, identity


def _remove_terminal_temp(
    root: Path,
    leaf: str,
    identity: tuple[int, ...],
) -> None:
    directory = _open_terminal_root(root, failure="terminal_temp_drift")
    try:
        current = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        if _terminal_file_identity(current) != identity:
            raise AdoptionError("terminal_temp_drift")
        os.unlink(leaf, dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        raise AdoptionError("terminal_temp_drift") from exc
    finally:
        os.close(directory)


def _promote_terminal_temp_exclusive(
    root: Path,
    *,
    temp_leaf: str,
    visible_leaf: str,
    content: bytes,
    identity: tuple[int, ...],
    failure: str,
) -> None:
    directory = _open_terminal_root(root, failure=failure)
    try:
        current = os.stat(temp_leaf, dir_fd=directory, follow_symlinks=False)
        if _terminal_file_identity(current) != identity:
            raise AdoptionError("terminal_temp_drift")
        try:
            os.stat(visible_leaf, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError(failure)
        _rename_directory_exclusive(directory, temp_leaf, visible_leaf)
        os.fsync(directory)
    except OSError as exc:
        raise AdoptionError(failure) from exc
    finally:
        os.close(directory)
    visible = root / visible_leaf
    if _private_file_bytes(visible) != content:
        raise AdoptionError(failure)


def _atomic_terminal_publish_exclusive(
    root: Path,
    *,
    temp_leaf: str,
    visible_leaf: str,
    content: bytes,
    failure: str,
    crash_hook: Callable[[str], None],
    temp_ready_phase: str,
) -> None:
    """Write and fsync private bytes before exclusive atomic publication."""

    directory = _open_terminal_root(root, failure=failure)
    descriptor = -1
    temp_created = False
    preserve_temp = False
    initial_identity: tuple[int, ...] | None = None
    try:
        try:
            os.stat(visible_leaf, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError(failure)
        descriptor = os.open(
            temp_leaf,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        temp_created = True
        initial = os.fstat(descriptor)
        initial_identity = _terminal_file_identity(initial)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != os.getuid()
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_nlink != 1
            or initial.st_size != 0
        ):
            raise AdoptionError(failure)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise AdoptionError(failure)
            offset += written
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            after.st_dev != initial.st_dev
            or after.st_ino != initial.st_ino
            or after.st_mode != initial.st_mode
            or after.st_uid != initial.st_uid
            or after.st_nlink != 1
            or after.st_size != len(content)
        ):
            raise AdoptionError(failure)
        os.close(descriptor)
        descriptor = -1
        crash_hook(temp_ready_phase)
        current = os.stat(temp_leaf, dir_fd=directory, follow_symlinks=False)
        if (
            current.st_dev != initial.st_dev
            or current.st_ino != initial.st_ino
            or current.st_mode != initial.st_mode
            or current.st_uid != initial.st_uid
            or current.st_nlink != 1
            or current.st_size != len(content)
        ):
            raise AdoptionError("terminal_temp_drift")
        try:
            os.stat(visible_leaf, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError(failure)
        _rename_directory_exclusive(directory, temp_leaf, visible_leaf)
        temp_created = False
        os.fsync(directory)
    except BaseException as exc:
        preserve_temp = not isinstance(exc, Exception)
        if isinstance(exc, OSError):
            raise AdoptionError(failure) from exc
        raise
    finally:
        try:
            if descriptor >= 0:
                os.close(descriptor)
            if temp_created and not preserve_temp:
                try:
                    current = os.stat(
                        temp_leaf,
                        dir_fd=directory,
                        follow_symlinks=False,
                    )
                    if (
                        initial_identity is None
                        or current.st_dev != initial_identity[0]
                        or current.st_ino != initial_identity[1]
                        or current.st_mode != initial_identity[2]
                        or current.st_uid != initial_identity[3]
                        or current.st_gid != initial_identity[4]
                        or current.st_nlink != 1
                        or current.st_size > len(content)
                    ):
                        raise AdoptionError("terminal_temp_drift")
                    os.unlink(temp_leaf, dir_fd=directory)
                    os.fsync(directory)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise AdoptionError("terminal_temp_drift") from exc
        finally:
            os.close(directory)
    if _private_file_bytes(root / visible_leaf) != content:
        raise AdoptionError(failure)


def _write_terminal_prepared_exclusive(
    root: Path,
    record: dict[str, object],
    *,
    crash_hook: Callable[[str], None],
) -> None:
    """Durably publish the immutable exact request before authority transport."""

    _lstat_admitted_directory(root, create=True)
    _atomic_terminal_publish_exclusive(
        root,
        temp_leaf=_TERMINAL_PREPARED_TEMP_LEAF,
        visible_leaf="journal.json",
        content=_terminal_prepared_bytes(record),
        failure="terminal_prepared_publish_failed",
        crash_hook=crash_hook,
        temp_ready_phase="TERMINAL_PREPARED_TEMP_READY",
    )


def _require_terminal_stage_matches_prepared(
    prepared: dict[str, object],
    committed: dict[str, object],
) -> None:
    _terminal_prepared_bytes(prepared)
    _terminal_journal_bytes(committed)
    if any(
        key != "phase" and committed[key] != prepared[key]
        for key in _TERMINAL_PREPARED_KEYS
    ):
        raise AdoptionError("terminal_stage_drift")


def _replace_terminal_prepared_with_journal(
    root: Path,
    prepared: dict[str, object],
    committed: dict[str, object],
    *,
    crash_hook: Callable[[str], None],
) -> None:
    """CAS-replace one exact prepared request with its committed envelope."""

    _lstat_admitted_directory(root)
    expected = _terminal_prepared_bytes(prepared)
    content = _terminal_journal_bytes(committed)
    try:
        prepared_bytes, _prepared_identity = _terminal_temp_snapshot(
            root,
            "journal.json",
        )
    except AdoptionError as exc:
        raise AdoptionError("terminal_prepared_drift") from exc
    if prepared_bytes != expected:
        raise AdoptionError("terminal_prepared_drift")
    _atomic_terminal_publish_exclusive(
        root,
        temp_leaf=_TERMINAL_STAGE_TEMP_LEAF,
        visible_leaf=_TERMINAL_STAGE_LEAF,
        content=content,
        failure="terminal_journal_publish_failed",
        crash_hook=crash_hook,
        temp_ready_phase="TERMINAL_STAGE_TEMP_READY",
    )
    crash_hook("TERMINAL_FINAL_READY")
    _replace_existing_terminal_stage(
        root,
        prepared,
        committed,
        crash_hook=crash_hook,
    )


def _restore_terminal_prepared_backup(
    root: Path,
    *,
    expected: bytes,
    backup_identity: tuple[int, ...],
) -> None:
    """Restore a prepared backup without discarding a substituted final node."""

    directory = _open_terminal_root(
        root,
        failure="terminal_prepared_backup_drift",
    )
    try:
        backup = os.stat(
            _TERMINAL_PREPARED_BACKUP_LEAF,
            dir_fd=directory,
            follow_symlinks=False,
        )
        if (
            _terminal_file_identity(backup) != backup_identity
            or _private_file_bytes(
                root / _TERMINAL_PREPARED_BACKUP_LEAF
            )
            != expected
        ):
            raise AdoptionError("terminal_prepared_backup_drift")
        _exchange_directory_entries(
            directory,
            _TERMINAL_PREPARED_BACKUP_LEAF,
            "journal.json",
        )
        os.fsync(directory)
        restored = os.stat(
            "journal.json",
            dir_fd=directory,
            follow_symlinks=False,
        )
        if not _terminal_rename_identity_matches(
            backup_identity,
            _terminal_file_identity(restored),
        ):
            raise AdoptionError("terminal_prepared_backup_drift")
    except OSError as exc:
        raise AdoptionError("terminal_prepared_backup_drift") from exc
    finally:
        os.close(directory)
    if _private_file_bytes(_journal_path(root)) != expected:
        raise AdoptionError("terminal_prepared_backup_drift")
    try:
        _collision, collision_identity = _terminal_temp_snapshot(
            root,
            _TERMINAL_PREPARED_BACKUP_LEAF,
        )
        _remove_terminal_temp(
            root,
            _TERMINAL_PREPARED_BACKUP_LEAF,
            collision_identity,
        )
    except AdoptionError as exc:
        raise AdoptionError("terminal_prepared_backup_drift") from exc


def _publish_terminal_stage_from_backup(
    root: Path,
    prepared: dict[str, object],
    committed: dict[str, object],
    *,
    backup_identity: tuple[int, ...],
    stage_identity: tuple[int, ...],
    crash_hook: Callable[[str], None],
) -> None:
    """No-clobber publish a signed stage while retaining REQUEST_PREPARED."""

    _require_terminal_stage_matches_prepared(prepared, committed)
    expected = _terminal_prepared_bytes(prepared)
    content = _terminal_journal_bytes(committed)
    directory = _open_terminal_root(
        root,
        failure="terminal_journal_publish_failed",
    )
    rollback_required = False
    try:
        try:
            os.stat("journal.json", dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError("terminal_journal_publish_failed")
        backup = os.stat(
            _TERMINAL_PREPARED_BACKUP_LEAF,
            dir_fd=directory,
            follow_symlinks=False,
        )
        staged = os.stat(
            _TERMINAL_STAGE_LEAF,
            dir_fd=directory,
            follow_symlinks=False,
        )
        if (
            _terminal_file_identity(backup) != backup_identity
            or _terminal_file_identity(staged) != stage_identity
            or _private_file_bytes(
                root / _TERMINAL_PREPARED_BACKUP_LEAF
            )
            != expected
            or _private_file_bytes(_terminal_stage_path(root)) != content
        ):
            raise AdoptionError("terminal_stage_drift")
        _rename_directory_exclusive(
            directory,
            _TERMINAL_STAGE_LEAF,
            "journal.json",
        )
        os.fsync(directory)
        try:
            published = os.stat(
                "journal.json",
                dir_fd=directory,
                follow_symlinks=False,
            )
            retained = os.stat(
                _TERMINAL_PREPARED_BACKUP_LEAF,
                dir_fd=directory,
                follow_symlinks=False,
            )
            rollback_required = (
                not _terminal_rename_identity_matches(
                    stage_identity,
                    _terminal_file_identity(published),
                )
                or _terminal_file_identity(retained) != backup_identity
                or _private_file_bytes(_journal_path(root)) != content
                or _private_file_bytes(
                    root / _TERMINAL_PREPARED_BACKUP_LEAF
                )
                != expected
            )
        except (OSError, AdoptionError):
            rollback_required = True
        if not rollback_required:
            crash_hook("TERMINAL_FINAL_BOUND")
    except OSError as exc:
        raise AdoptionError("terminal_journal_publish_failed") from exc
    finally:
        os.close(directory)
    if rollback_required:
        _restore_terminal_prepared_backup(
            root,
            expected=expected,
            backup_identity=backup_identity,
        )
        raise AdoptionError("terminal_stage_substitution")
    _remove_terminal_temp(
        root,
        _TERMINAL_PREPARED_BACKUP_LEAF,
        backup_identity,
    )
    crash_hook("TERMINAL_FINAL_REPLACED")
    if _private_file_bytes(_journal_path(root)) != content:
        raise AdoptionError("terminal_journal_publish_failed")


def _replace_existing_terminal_stage(
    root: Path,
    prepared: dict[str, object],
    committed: dict[str, object],
    *,
    crash_hook: Callable[[str], None],
) -> None:
    """Complete an already durable signed stage without authority replay."""

    _lstat_admitted_directory(root)
    _require_terminal_stage_matches_prepared(prepared, committed)
    expected = _terminal_prepared_bytes(prepared)
    content = _terminal_journal_bytes(committed)
    if (
        _private_file_bytes(_journal_path(root)) != expected
        or _private_file_bytes(_terminal_stage_path(root)) != content
    ):
        raise AdoptionError("terminal_stage_drift")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory = os.open(root, directory_flags)
    backup_identity: tuple[int, ...] | None = None
    stage_identity: tuple[int, ...] | None = None
    try:
        root_info = os.fstat(directory)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            raise AdoptionError("terminal_stage_drift")
        identities: list[tuple[int, ...]] = []
        for leaf, size in (
            ("journal.json", len(expected)),
            (_TERMINAL_STAGE_LEAF, len(content)),
        ):
            info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or info.st_size != size
            ):
                raise AdoptionError("terminal_stage_drift")
            identities.append((
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ))
        if (
            _private_file_bytes(_journal_path(root)) != expected
            or _private_file_bytes(_terminal_stage_path(root)) != content
        ):
            raise AdoptionError("terminal_stage_drift")
        for (leaf, _size), identity in zip(
            (
                ("journal.json", len(expected)),
                (_TERMINAL_STAGE_LEAF, len(content)),
            ),
            identities,
            strict=True,
        ):
            info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
            if (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ) != identity:
                raise AdoptionError("terminal_stage_drift")
        prepared_identity, stage_identity = identities
        _rename_directory_exclusive(
            directory,
            "journal.json",
            _TERMINAL_PREPARED_BACKUP_LEAF,
        )
        os.fsync(directory)
        backup = os.stat(
            _TERMINAL_PREPARED_BACKUP_LEAF,
            dir_fd=directory,
            follow_symlinks=False,
        )
        backup_identity = _terminal_file_identity(backup)
        if (
            not _terminal_rename_identity_matches(
                prepared_identity,
                backup_identity,
            )
            or _private_file_bytes(
                root / _TERMINAL_PREPARED_BACKUP_LEAF
            )
            != expected
        ):
            raise AdoptionError("terminal_prepared_backup_drift")
        try:
            os.stat("journal.json", dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError("terminal_prepared_backup_drift")
    except OSError as exc:
        raise AdoptionError("terminal_journal_publish_failed") from exc
    finally:
        os.close(directory)
    if backup_identity is None or stage_identity is None:
        raise AdoptionError("terminal_prepared_backup_drift")
    crash_hook("TERMINAL_PREPARED_BACKED_UP")
    _publish_terminal_stage_from_backup(
        root,
        prepared,
        committed,
        backup_identity=backup_identity,
        stage_identity=stage_identity,
        crash_hook=crash_hook,
    )


def _archive_stat_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _archive_root_rename_identity_matches(
    before: tuple[int, ...],
    after: tuple[int, ...],
) -> bool:
    """Admit only the ctime transition caused by renaming the bound root."""

    return before[:-1] == after[:-1] and after[-1] >= before[-1]


def _archive_private_file(
    parent_descriptor: int,
    name: str,
    *,
    maximum: int = 512 * 1024,
    exact_private_mode: bool = True,
) -> tuple[bytes, tuple[int, ...]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise AdoptionError("terminal_archive_drift") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o022
            or (
                exact_private_mode
                and stat.S_IMODE(before.st_mode) != 0o600
            )
            or before.st_size > maximum
        ):
            raise AdoptionError("terminal_archive_drift")
        content = bytearray()
        while chunk := os.read(descriptor, min(1024 * 1024, maximum + 1)):
            content.extend(chunk)
            if len(content) > maximum:
                raise AdoptionError("terminal_archive_drift")
        after = os.fstat(descriptor)
        path_info = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        identity = _archive_stat_identity(before)
        if (
            identity != _archive_stat_identity(after)
            or identity != _archive_stat_identity(path_info)
        ):
            raise AdoptionError("terminal_archive_drift")
        return bytes(content), identity
    except OSError as exc:
        raise AdoptionError("terminal_archive_drift") from exc
    finally:
        os.close(descriptor)


def _archive_directory_digest(
    descriptor: int,
    *,
    prefix: str = "",
) -> tuple[str, tuple[int, ...]]:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_mode & 0o022
    ):
        raise AdoptionError("terminal_archive_drift")
    try:
        names = sorted(os.listdir(descriptor))
    except OSError as exc:
        raise AdoptionError("terminal_archive_drift") from exc
    rows: list[str] = []
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\0" in name:
            raise AdoptionError("terminal_archive_drift")
        relative = f"{prefix}/{name}" if prefix else name
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise AdoptionError("terminal_archive_drift") from exc
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise AdoptionError("terminal_archive_drift")
        if stat.S_ISDIR(info.st_mode):
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                child = os.open(name, flags, dir_fd=descriptor)
            except OSError as exc:
                raise AdoptionError("terminal_archive_drift") from exc
            try:
                child_before = os.fstat(child)
                digest, child_identity = _archive_directory_digest(
                    child,
                    prefix=relative,
                )
                child_after = os.fstat(child)
            finally:
                os.close(child)
            if (
                _archive_stat_identity(info) != child_identity
                or child_identity != _archive_stat_identity(child_before)
                or child_identity != _archive_stat_identity(child_after)
            ):
                raise AdoptionError("terminal_archive_drift")
            rows.append(
                f"d {stat.S_IMODE(info.st_mode):04o} {relative} {digest}\n"
            )
        elif stat.S_ISREG(info.st_mode):
            content, identity = _archive_private_file(
                descriptor,
                name,
                maximum=4 * 1024 * 1024,
                exact_private_mode=False,
            )
            if _archive_stat_identity(info) != identity:
                raise AdoptionError("terminal_archive_drift")
            rows.append(
                f"f {stat.S_IMODE(info.st_mode):04o} {len(content)} "
                f"{hashlib.sha256(content).hexdigest()} {relative}\n"
            )
        else:
            raise AdoptionError("terminal_archive_drift")
    after = os.fstat(descriptor)
    identity = _archive_stat_identity(before)
    if identity != _archive_stat_identity(after):
        raise AdoptionError("terminal_archive_drift")
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest(), identity


def _archive_snapshot(
    root_descriptor: int,
) -> tuple[dict[str, tuple[str, tuple[int, ...], str]], tuple[int, ...]]:
    root_before = os.fstat(root_descriptor)
    if (
        not stat.S_ISDIR(root_before.st_mode)
        or root_before.st_uid != os.getuid()
        or stat.S_IMODE(root_before.st_mode) != 0o700
    ):
        raise AdoptionError("terminal_archive_drift")
    try:
        names = sorted(os.listdir(root_descriptor))
    except OSError as exc:
        raise AdoptionError("terminal_archive_drift") from exc
    snapshot: dict[str, tuple[str, tuple[int, ...], str]] = {}
    for name in names:
        try:
            info = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise AdoptionError("terminal_archive_drift") from exc
        if stat.S_ISDIR(info.st_mode):
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(name, flags, dir_fd=root_descriptor)
            except OSError as exc:
                raise AdoptionError("terminal_archive_drift") from exc
            try:
                digest, identity = _archive_directory_digest(descriptor)
            finally:
                os.close(descriptor)
            kind = "directory"
        elif stat.S_ISREG(info.st_mode):
            content, identity = _archive_private_file(root_descriptor, name)
            digest = hashlib.sha256(content).hexdigest()
            kind = "file"
        else:
            raise AdoptionError("terminal_archive_drift")
        if _archive_stat_identity(info) != identity:
            raise AdoptionError("terminal_archive_drift")
        snapshot[name] = (kind, identity, digest)
    root_after = os.fstat(root_descriptor)
    root_identity = _archive_stat_identity(root_before)
    if root_identity != _archive_stat_identity(root_after):
        raise AdoptionError("terminal_archive_drift")
    return snapshot, root_identity


def _atomic_private_write_at(
    parent_descriptor: int,
    name: str,
    content: bytes,
) -> None:
    temporary = f".{name}.{secrets.token_hex(12)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_descriptor)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "archive marker write failed")
            offset += written
        os.fsync(descriptor)
        written_info = os.fstat(descriptor)
        written_identity = _archive_stat_identity(written_info)
        path_identity = _archive_stat_identity(
            os.stat(
                temporary,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        if (
            not stat.S_ISREG(written_info.st_mode)
            or written_info.st_uid != os.getuid()
            or stat.S_IMODE(written_info.st_mode) != 0o600
            or written_info.st_nlink != 1
            or written_info.st_size != len(content)
            or written_identity != path_identity
        ):
            raise AdoptionError("terminal_archive_drift")
        _rename_directory_exclusive(parent_descriptor, temporary, name)
        published_identity = _archive_stat_identity(
            os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        held_identity = _archive_stat_identity(os.fstat(descriptor))
        if (
            not _terminal_rename_identity_matches(
                written_identity,
                published_identity,
            )
            or not _terminal_rename_identity_matches(
                written_identity,
                held_identity,
            )
        ):
            raise AdoptionError("terminal_archive_drift")
        os.fsync(parent_descriptor)
        published_content, observed_identity = _archive_private_file(
            parent_descriptor,
            name,
        )
        if (
            published_content != content
            or not _terminal_rename_identity_matches(
                written_identity,
                observed_identity,
            )
        ):
            raise AdoptionError("terminal_archive_drift")
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError("terminal_archive_drift") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        # The random leaf may have been exchanged by a same-UID peer.  Never
        # unlink it by pathname: failed publications retain every ambiguous
        # residue, while a successful rename leaves no temporary entry.


def _archive_terminal_transaction(
    root: Path,
    record: dict[str, object],
    *,
    crash_hook: Callable[[str], None] = lambda _phase: None,
) -> str:
    """Atomically retain a fully verified rolled-back transaction as history."""

    if record.get("phase") != "ROLLED_BACK":
        raise AdoptionError("terminal_archive_not_admitted")
    transaction_id = _safe_transaction_id(record.get("transaction_id"))
    _lstat_admitted_directory(root)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise AdoptionError("terminal_archive_drift") from exc
    initial_root = os.fstat(root_descriptor)
    path_root = root.lstat()
    if (
        _archive_stat_identity(initial_root) != _archive_stat_identity(path_root)
        or not stat.S_ISDIR(initial_root.st_mode)
        or initial_root.st_uid != os.getuid()
        or stat.S_IMODE(initial_root.st_mode) != 0o700
    ):
        os.close(root_descriptor)
        raise AdoptionError("terminal_archive_drift")
    allowed = frozenset({
        "archive.json",
        "claude.lock",
        "cli-exec",
        "codex.lock",
        "journal.json",
        "quarantine",
        "rollback",
        "stage",
        _TERMINAL_BRIDGE_MANIFEST_LEAF,
        _ORDINARY_CONSUMED_LEAF,
    }) | _JOURNAL_EDGE_LEAVES
    try:
        initial_snapshot, _ = _archive_snapshot(root_descriptor)
    except Exception:
        os.close(root_descriptor)
        raise
    names = frozenset(initial_snapshot)
    if "journal.json" not in names or names - allowed:
        os.close(root_descriptor)
        raise AdoptionError("terminal_archive_drift")
    expected_journal = _journal_bytes(record)
    journal_bytes, _journal_identity = _archive_private_file(
        root_descriptor,
        "journal.json",
    )
    if journal_bytes != expected_journal:
        os.close(root_descriptor)
        raise AdoptionError("terminal_archive_journal_mismatch")
    initial_artifact_snapshot = {
        name: identity
        for name, identity in initial_snapshot.items()
        if name != "archive.json"
    }
    artifacts = {
        name: identity[2]
        for name, identity in initial_artifact_snapshot.items()
    }
    marker = {
        "artifacts": artifacts,
        "journal_digest": hashlib.sha256(expected_journal).hexdigest(),
        "phase": "ROLLED_BACK",
        "schema": ARCHIVE_SCHEMA,
        "transaction_id": transaction_id,
    }
    marker_bytes = _json_bytes(marker)
    crash_hook("TERMINAL_ARCHIVE_BEFORE_MARKER")
    if "archive.json" in initial_snapshot:
        observed_marker, _marker_identity = _archive_private_file(
            root_descriptor,
            "archive.json",
        )
        if observed_marker != marker_bytes:
            os.close(root_descriptor)
            raise AdoptionError("terminal_archive_drift")
    else:
        _atomic_private_write_at(root_descriptor, "archive.json", marker_bytes)
    expected_snapshot, expected_root_identity = _archive_snapshot(root_descriptor)
    expected_artifact_snapshot = {
        name: identity
        for name, identity in expected_snapshot.items()
        if name != "archive.json"
    }
    journal_after_marker, _journal_identity = _archive_private_file(
        root_descriptor,
        "journal.json",
    )
    if (
        expected_artifact_snapshot != initial_artifact_snapshot
        or journal_after_marker != expected_journal
    ):
        os.close(root_descriptor)
        raise AdoptionError("terminal_archive_drift")
    crash_hook("TERMINAL_ARCHIVE_READY")

    history = _history_root(root)
    _lstat_admitted_directory(history, create=True)
    destination = history / transaction_id
    if destination.exists() or destination.is_symlink():
        os.close(root_descriptor)
        raise AdoptionError("terminal_archive_exists")
    source_parent_descriptor = os.open(root.parent, directory_flags)
    history_descriptor = os.open(history, directory_flags)
    try:
        before_publish_snapshot, before = _archive_snapshot(root_descriptor)
        path_before = root.lstat()
        journal_before, _journal_identity = _archive_private_file(
            root_descriptor,
            "journal.json",
        )
        if (
            before_publish_snapshot != expected_snapshot
            or before != expected_root_identity
            or before != _archive_stat_identity(path_before)
            or journal_before != expected_journal
        ):
            raise AdoptionError("terminal_archive_drift")
        try:
            _rename_directory_between_exclusive(
                source_parent_descriptor,
                root.name,
                history_descriptor,
                transaction_id,
            )
        except OSError as exc:
            raise AdoptionError("terminal_archive_publish_failed") from exc
        after_publish_snapshot, after = _archive_snapshot(root_descriptor)
        published = destination.lstat()
        journal_after, _journal_identity = _archive_private_file(
            root_descriptor,
            "journal.json",
        )
        if (
            after_publish_snapshot != expected_snapshot
            or not _archive_root_rename_identity_matches(
                expected_root_identity,
                after,
            )
            or after != _archive_stat_identity(published)
            or journal_after != expected_journal
        ):
            raise AdoptionError("terminal_archive_publish_failed")
        _fsync_directory(history)
        _fsync_directory(root.parent)
    except OSError as exc:
        raise AdoptionError("terminal_archive_parent_fsync_failed") from exc
    finally:
        os.close(root_descriptor)
        os.close(history_descriptor)
        os.close(source_parent_descriptor)
    crash_hook("TERMINAL_ARCHIVED")
    return transaction_id


def _advance_journal(
    root: Path,
    record: dict[str, object],
    phase: str,
    *,
    crash_hook: Callable[[str], None],
) -> dict[str, object]:
    current = str(record["phase"])
    if (current, phase) not in _JOURNAL_EDGES:
        raise AdoptionError("journal_transition_invalid")
    successor = {**record, "phase": phase}
    _replace_journal_cas(root, record, successor)
    crash_hook(phase)
    return successor


def _replace_legacy_journal_cas(
    root: Path,
    expected: dict[str, object],
    successor: dict[str, object],
) -> None:
    """Advance legacy journals without pathname-based residue deletion."""

    if _private_file_bytes(_journal_path(root)) != _journal_bytes(expected):
        raise AdoptionError("journal_transition_cas_mismatch")
    _replace_bridge_journal_cas(root, expected, successor)


def _replace_bridge_journal_cas(
    root: Path,
    expected: dict[str, object],
    successor: dict[str, object],
) -> None:
    """Advance a journal while retaining every displaced bound inode."""

    expected_bytes = _journal_bytes(expected)
    successor_bytes = _journal_bytes(successor)
    phase = str(expected["phase"])
    successor_phase = str(successor["phase"])
    if (
        re.fullmatch(r"[A-Z_]+", phase) is None
        or re.fullmatch(r"[A-Z_]+", successor_phase) is None
    ):
        raise AdoptionError("journal_transition_cas_mismatch")
    prior_leaf = (
        ".ordinary-journal-edge-"
        f"{phase.lower()}-to-{successor_phase.lower()}"
    )
    directory = _open_terminal_root(
        root,
        failure="journal_transition_cas_mismatch",
    )
    current_descriptor = successor_descriptor = -1
    exchanged = False
    try:
        root_identity = _archive_stat_identity(os.fstat(directory))
        if root_identity != _archive_stat_identity(root.lstat()):
            raise AdoptionError("journal_transition_cas_mismatch")
        current_descriptor = os.open(
            "journal.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        current_info = os.fstat(current_descriptor)
        current_identity = _archive_stat_identity(current_info)
        if (
            not stat.S_ISREG(current_info.st_mode)
            or current_info.st_uid != os.getuid()
            or stat.S_IMODE(current_info.st_mode) != 0o600
            or current_info.st_nlink != 1
            or current_info.st_size != len(expected_bytes)
            or current_identity
            != _archive_stat_identity(
                os.stat(
                    "journal.json",
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            )
        ):
            raise AdoptionError("journal_transition_cas_mismatch")
        current_content = bytearray()
        while len(current_content) <= len(expected_bytes):
            chunk = os.read(
                current_descriptor,
                len(expected_bytes) + 1 - len(current_content),
            )
            if not chunk:
                break
            current_content.extend(chunk)
        if (
            bytes(current_content) != expected_bytes
            or current_identity
            != _archive_stat_identity(os.fstat(current_descriptor))
        ):
            raise AdoptionError("journal_transition_cas_mismatch")

        try:
            successor_descriptor = os.open(
                prior_leaf,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory,
            )
            os.fchmod(successor_descriptor, 0o600)
            view = memoryview(successor_bytes)
            while view:
                written = os.write(successor_descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "bridge journal write failed")
                view = view[written:]
            os.fsync(successor_descriptor)
            os.lseek(successor_descriptor, 0, os.SEEK_SET)
        except FileExistsError:
            successor_descriptor = os.open(
                prior_leaf,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory,
            )
        successor_info = os.fstat(successor_descriptor)
        successor_identity = _archive_stat_identity(successor_info)
        successor_content = bytearray()
        while len(successor_content) <= len(successor_bytes):
            chunk = os.read(
                successor_descriptor,
                len(successor_bytes) + 1 - len(successor_content),
            )
            if not chunk:
                break
            successor_content.extend(chunk)
        if (
            not stat.S_ISREG(successor_info.st_mode)
            or successor_info.st_uid != os.getuid()
            or stat.S_IMODE(successor_info.st_mode) != 0o600
            or successor_info.st_nlink != 1
            or successor_info.st_size != len(successor_bytes)
            or bytes(successor_content) != successor_bytes
            or successor_identity
            != _archive_stat_identity(os.fstat(successor_descriptor))
            or successor_identity
            != _archive_stat_identity(
                os.stat(
                    prior_leaf,
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            )
            or current_identity
            != _archive_stat_identity(os.fstat(current_descriptor))
            or current_identity
            != _archive_stat_identity(
                os.stat(
                    "journal.json",
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            )
        ):
            raise AdoptionError("journal_transition_cas_mismatch")

        _exchange_directory_entries(directory, prior_leaf, "journal.json")
        exchanged = True
        displaced = _archive_stat_identity(
            os.stat(
                prior_leaf,
                dir_fd=directory,
                follow_symlinks=False,
            )
        )
        published = _archive_stat_identity(
            os.stat(
                "journal.json",
                dir_fd=directory,
                follow_symlinks=False,
            )
        )
        if (
            not _terminal_rename_identity_matches(current_identity, displaced)
            or not _terminal_rename_identity_matches(
                successor_identity,
                published,
            )
            or not _terminal_rename_identity_matches(
                current_identity,
                _archive_stat_identity(os.fstat(current_descriptor)),
            )
            or not _terminal_rename_identity_matches(
                successor_identity,
                _archive_stat_identity(os.fstat(successor_descriptor)),
            )
        ):
            raise AdoptionError("journal_transition_cas_mismatch")
        os.fsync(directory)
        if root_identity[:5] != _archive_stat_identity(root.lstat())[:5]:
            raise AdoptionError("journal_transition_cas_mismatch")
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError("journal_transition_publish_failed") from exc
    finally:
        if successor_descriptor >= 0:
            os.close(successor_descriptor)
        if current_descriptor >= 0:
            os.close(current_descriptor)
        # Never unlink the retained predecessor or an ambiguous pre-exchange
        # leaf: pathname cleanup cannot be made inode-CAS-safe against a
        # same-UID exchange.  Exact per-edge names bound growth to the finite
        # journal state graph while allowing a crash-reconciled rollback edge
        # to coexist with an earlier prepared normal-successor edge.
        os.close(directory)
    if (
        not exchanged
        or _private_file_bytes(_journal_path(root)) != successor_bytes
        or _private_file_bytes(root / prior_leaf) != expected_bytes
    ):
        raise AdoptionError("journal_transition_publish_failed")


def _replace_journal_cas(
    root: Path,
    expected: dict[str, object],
    successor: dict[str, object],
) -> None:
    try:
        before_states = tuple(
            HostState.from_projection(value, expected_host=host)
            for value, host in zip(
                expected["before_states"], HOST_ORDER, strict=True
            )
        )
    except (KeyError, TypeError, ValueError, AdoptionError):
        before_states = ()
    if len(before_states) == len(HOST_ORDER) and all(
        state.plugin_version == authority.TERMINAL_PLUGIN_VERSION
        for state in before_states
    ):
        _replace_bridge_journal_cas(root, expected, successor)
        return
    _replace_legacy_journal_cas(root, expected, successor)


def _has_unsupported_snapshot_metadata(
    descriptor: int,
    info: os.stat_result,
) -> bool:
    if getattr(info, "st_flags", 0):
        return True
    try:
        return bool(os.listxattr(descriptor))
    except AttributeError:
        return False
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
            return False
        raise


def _tree_digest(root: Path, *, ignored: frozenset[str] = frozenset()) -> str | None:
    if not root.exists() and not root.is_symlink():
        return None
    root_info = root.lstat()
    if (
        stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.getuid()
        or root_info.st_mode & 0o022
    ):
        raise AdoptionError("cache_root_drift")
    rows: list[str] = []
    uid = os.getuid()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if any(
            relative == ignored_path
            or relative.startswith(f"{ignored_path}/")
            for ignored_path in ignored
        ):
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise AdoptionError("cache_metadata_drift")
        if path.is_dir():
            rows.append(f"d {stat.S_IMODE(info.st_mode):04o} {relative}\n")
        elif stat.S_ISREG(info.st_mode):
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                before = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != uid
                    or before.st_mode & 0o022
                    or before.st_nlink != 1
                    or _has_unsupported_snapshot_metadata(descriptor, before)
                    or (before.st_dev, before.st_ino, before.st_mode, before.st_size)
                    != (info.st_dev, info.st_ino, info.st_mode, info.st_size)
                ):
                    raise AdoptionError("cache_metadata_drift")
                hasher = hashlib.sha256()
                while chunk := os.read(descriptor, 1024 * 1024):
                    hasher.update(chunk)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise AdoptionError("cache_metadata_drift")
            digest = hasher.hexdigest()
            rows.append(
                f"f {stat.S_IMODE(info.st_mode):04o} {info.st_size} {digest} {relative}\n"
            )
        else:
            raise AdoptionError("cache_metadata_drift")
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def _copy_private_tree_exclusive(
    source: Path,
    destination: Path,
    *,
    expected_digest: str,
    crash_hook: Callable[[str], None] = lambda _phase: None,
) -> None:
    if _safe_sha256(expected_digest) is None:
        raise AdoptionError("terminal_rollback_capture_digest_invalid")
    _lstat_admitted_directory(destination.parent, create=True)
    if destination.exists() or destination.is_symlink():
        try:
            destination_digest = _bounded_private_tree_digest(destination)
        except AdoptionError as exc:
            if str(exc) == "terminal_rollback_capture_limit_exceeded":
                raise
            raise AdoptionError(
                "terminal_rollback_capture_no_clobber"
            ) from exc
        if destination_digest != expected_digest:
            raise AdoptionError("terminal_rollback_capture_no_clobber")
        return
    stage = destination.parent / f".{destination.name}.snapshot"
    if stage.exists() or stage.is_symlink():
        try:
            _publish_private_snapshot_stage(
                stage,
                destination,
                expected_digest=expected_digest,
                digest_mismatch_code=(
                    "terminal_rollback_capture_no_clobber"
                ),
            )
        except AdoptionError as exc:
            if str(exc) in {
                "terminal_rollback_capture_limit_exceeded",
                "terminal_rollback_capture_drift",
                "terminal_rollback_capture_no_clobber",
            }:
                raise
            raise AdoptionError(
                "terminal_rollback_capture_no_clobber"
            ) from exc
        return
    try:
        root_before = source.lstat()
    except OSError as exc:
        raise AdoptionError("terminal_rollback_source_unavailable") from exc
    if (
        stat.S_ISLNK(root_before.st_mode)
        or not stat.S_ISDIR(root_before.st_mode)
        or root_before.st_uid != os.getuid()
        or root_before.st_mode & 0o022
    ):
        raise AdoptionError("terminal_rollback_source_metadata_invalid")
    source_descriptor = os.open(
        source,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if _has_unsupported_snapshot_metadata(
            source_descriptor, os.fstat(source_descriptor)
        ):
            raise AdoptionError("terminal_rollback_source_metadata_invalid")
    finally:
        os.close(source_descriptor)
    if _bounded_private_tree_digest(source) != expected_digest:
        raise AdoptionError("terminal_rollback_source_digest_mismatch")
    stage.mkdir(mode=stat.S_IMODE(root_before.st_mode))
    stage.chmod(stat.S_IMODE(root_before.st_mode))
    entry_count = 0
    total_bytes = 0
    try:
        for source_path in sorted(
            source.rglob("*"),
            key=lambda item: item.relative_to(source).as_posix(),
        ):
            relative = source_path.relative_to(source)
            if (
                len(relative.parts) > _MAX_ROLLBACK_SNAPSHOT_DEPTH
                or any(
                    part in {"", ".", ".."}
                    or "/" in part
                    or "\\" in part
                    for part in relative.parts
                )
            ):
                raise AdoptionError("terminal_rollback_capture_path_invalid")
            entry_count += 1
            if entry_count > _MAX_ROLLBACK_SNAPSHOT_ENTRIES:
                raise AdoptionError("terminal_rollback_capture_limit_exceeded")
            source_info = source_path.lstat()
            destination_path = stage / relative
            if (
                stat.S_ISLNK(source_info.st_mode)
                or source_info.st_uid != os.getuid()
                or source_info.st_mode & 0o022
            ):
                raise AdoptionError("terminal_rollback_source_metadata_invalid")
            if stat.S_ISDIR(source_info.st_mode):
                descriptor = os.open(
                    source_path,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    if (
                        _archive_stat_identity(os.fstat(descriptor))
                        != _archive_stat_identity(source_info)
                        or _has_unsupported_snapshot_metadata(
                            descriptor, source_info
                        )
                    ):
                        raise AdoptionError(
                            "terminal_rollback_source_metadata_invalid"
                        )
                finally:
                    os.close(descriptor)
                destination_path.mkdir(
                    mode=stat.S_IMODE(source_info.st_mode)
                )
                destination_path.chmod(stat.S_IMODE(source_info.st_mode))
                continue
            if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
                raise AdoptionError("terminal_rollback_source_metadata_invalid")
            total_bytes += source_info.st_size
            if total_bytes > _MAX_ROLLBACK_SNAPSHOT_BYTES:
                raise AdoptionError("terminal_rollback_capture_limit_exceeded")
            source_file = os.open(
                source_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            destination_file = -1
            try:
                before = os.fstat(source_file)
                if (
                    _archive_stat_identity(before)
                    != _archive_stat_identity(source_info)
                    or before.st_nlink != 1
                    or _has_unsupported_snapshot_metadata(source_file, before)
                ):
                    raise AdoptionError(
                        "terminal_rollback_source_metadata_invalid"
                    )
                destination_file = os.open(
                    destination_path,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    stat.S_IMODE(before.st_mode),
                )
                os.fchmod(destination_file, stat.S_IMODE(before.st_mode))
                os.fchown(destination_file, -1, before.st_gid)
                while chunk := os.read(source_file, 1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_file, view)
                        if written <= 0:
                            raise OSError(errno.EIO, "snapshot write failed")
                        view = view[written:]
                os.fsync(destination_file)
                source_after = os.fstat(source_file)
                destination_info = os.fstat(destination_file)
                path_after = source_path.lstat()
                if (
                    _archive_stat_identity(before)
                    != _archive_stat_identity(source_after)
                    or _archive_stat_identity(source_after)
                    != _archive_stat_identity(path_after)
                    or not stat.S_ISREG(destination_info.st_mode)
                    or destination_info.st_uid != os.getuid()
                    or destination_info.st_gid != before.st_gid
                    or destination_info.st_nlink != 1
                    or stat.S_IMODE(destination_info.st_mode)
                    != stat.S_IMODE(before.st_mode)
                    or destination_info.st_size != before.st_size
                    or _has_unsupported_snapshot_metadata(
                        destination_file, destination_info
                    )
                ):
                    raise AdoptionError("terminal_rollback_capture_drift")
            finally:
                if destination_file >= 0:
                    os.close(destination_file)
                os.close(source_file)
        for directory in sorted(
            (path for path in stage.rglob("*") if path.is_dir()),
            key=lambda item: len(item.relative_to(stage).parts),
            reverse=True,
        ):
            _fsync_directory(directory)
        _fsync_directory(stage)
        root_after = source.lstat()
        if (
            _archive_stat_identity(root_before)
            != _archive_stat_identity(root_after)
            or _bounded_private_tree_digest(source) != expected_digest
            or _bounded_private_tree_digest(stage) != expected_digest
        ):
            raise AdoptionError("terminal_rollback_capture_drift")
        crash_hook("TERMINAL_ROLLBACK_SNAPSHOT_READY")
        if (
            _archive_stat_identity(root_before)
            != _archive_stat_identity(source.lstat())
            or _bounded_private_tree_digest(source) != expected_digest
            or _bounded_private_tree_digest(stage) != expected_digest
        ):
            raise AdoptionError("terminal_rollback_capture_drift")
        _publish_private_snapshot_stage(
            stage,
            destination,
            expected_digest=expected_digest,
        )
        crash_hook("TERMINAL_ROLLBACK_SNAPSHOT_PUBLISHED")
    except InjectedCrash:
        raise
    except AdoptionError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise AdoptionError("terminal_rollback_capture_enospc") from exc
        if exc.errno == errno.EXDEV:
            raise AdoptionError("terminal_rollback_capture_cross_device") from exc
        raise AdoptionError("terminal_rollback_capture_failed") from exc
    if _bounded_private_tree_digest(destination) != expected_digest:
        raise AdoptionError("terminal_rollback_capture_drift")


def _open_bound_directory(
    path: Path,
) -> tuple[int, int, tuple[int, ...], tuple[int, ...]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = os.open(path.parent, flags)
    descriptor = -1
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        parent_identity = _archive_stat_identity(os.fstat(parent_descriptor))
        identity = _archive_stat_identity(os.fstat(descriptor))
        if (
            parent_identity != _archive_stat_identity(path.parent.lstat())
            or identity
            != _archive_stat_identity(
                os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
            )
        ):
            raise AdoptionError("generated_candidate_parent_drift")
        return parent_descriptor, descriptor, parent_identity, identity
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
        raise


def _recheck_bound_directory(
    path: Path,
    parent_descriptor: int,
    descriptor: int,
    parent_identity: tuple[int, ...],
    identity: tuple[int, ...],
) -> None:
    try:
        if (
            parent_identity != _archive_stat_identity(os.fstat(parent_descriptor))
            or parent_identity != _archive_stat_identity(path.parent.lstat())
            or identity != _archive_stat_identity(os.fstat(descriptor))
            or identity
            != _archive_stat_identity(
                os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
            )
        ):
            raise AdoptionError("generated_candidate_parent_drift")
    except OSError as exc:
        raise AdoptionError("generated_candidate_parent_drift") from exc


def _tree_digests_from_descriptor(
    descriptor: int,
    *,
    ignored_sets: tuple[frozenset[str], ...],
    file_validators: dict[str, Callable[[os.stat_result, bytes], None]] | None = None,
    file_projectors: dict[str, Callable[[bytes], bytes]] | None = None,
    maximum_depth: int | None = None,
    maximum_entries: int | None = None,
    maximum_bytes: int | None = None,
) -> tuple[str, ...]:
    if not ignored_sets:
        raise AdoptionError("generated_candidate_drift")
    uid = os.getuid()
    rows: list[tuple[str, str]] = []
    held_directories: list[tuple[int, str, int, tuple[int, ...]]] = []
    held_files: list[tuple[int, str, int, tuple[int, ...]]] = []
    root_before = os.fstat(descriptor)
    validators = file_validators or {}
    projectors = file_projectors or {}
    entry_count = 0
    total_bytes = 0

    def path_is_ignored(relative: str, ignored: frozenset[str]) -> bool:
        return any(
            relative == candidate or relative.startswith(f"{candidate}/")
            for candidate in ignored
        )

    def ignored_by_every_projection(relative: str) -> bool:
        return all(path_is_ignored(relative, ignored) for ignored in ignored_sets)

    def walk(current: int, prefix: str = "") -> None:
        nonlocal entry_count, total_bytes
        current_before = os.fstat(current)
        if (
            not stat.S_ISDIR(current_before.st_mode)
            or current_before.st_uid != uid
            or current_before.st_mode & 0o022
            or _has_unsupported_snapshot_metadata(current, current_before)
        ):
            raise AdoptionError("generated_candidate_drift")
        try:
            names = sorted(os.listdir(current))
        except OSError as exc:
            raise AdoptionError("generated_candidate_drift") from exc
        for name in names:
            if not name or name in {".", ".."} or "/" in name or "\0" in name:
                raise AdoptionError("generated_candidate_drift")
            relative = f"{prefix}/{name}" if prefix else name
            if ignored_by_every_projection(relative):
                continue
            entry_count += 1
            if (
                (maximum_entries is not None and entry_count > maximum_entries)
                or (
                    maximum_depth is not None
                    and len(Path(relative).parts) > maximum_depth
                )
            ):
                raise AdoptionError(
                    "terminal_rollback_capture_limit_exceeded"
                )
            try:
                info = os.stat(name, dir_fd=current, follow_symlinks=False)
            except OSError as exc:
                raise AdoptionError("generated_candidate_drift") from exc
            if stat.S_ISLNK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
                raise AdoptionError("generated_candidate_drift")
            if stat.S_ISDIR(info.st_mode):
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                child = os.open(name, flags, dir_fd=current)
                child_identity = _archive_stat_identity(os.fstat(child))
                if child_identity != _archive_stat_identity(info):
                    os.close(child)
                    raise AdoptionError("generated_candidate_drift")
                held_directories.append((current, name, child, child_identity))
                rows.append(
                    (
                        relative,
                        f"d {stat.S_IMODE(info.st_mode):04o} {relative}\n",
                    )
                )
                walk(child, relative)
            elif stat.S_ISREG(info.st_mode):
                file_descriptor = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current,
                )
                before = os.fstat(file_descriptor)
                file_identity = _archive_stat_identity(before)
                if (
                    file_identity != _archive_stat_identity(info)
                    or before.st_nlink != 1
                    or _has_unsupported_snapshot_metadata(
                        file_descriptor, before
                    )
                ):
                    os.close(file_descriptor)
                    raise AdoptionError("generated_candidate_drift")
                total_bytes += before.st_size
                if maximum_bytes is not None and total_bytes > maximum_bytes:
                    os.close(file_descriptor)
                    raise AdoptionError(
                        "terminal_rollback_capture_limit_exceeded"
                    )
                held_files.append(
                    (current, name, file_descriptor, file_identity)
                )
                hasher = hashlib.sha256()
                captured = (
                    bytearray()
                    if relative in validators or relative in projectors
                    else None
                )
                while chunk := os.read(file_descriptor, 1024 * 1024):
                    hasher.update(chunk)
                    if captured is not None:
                        captured.extend(chunk)
                        if relative in validators and len(captured) > 64:
                            raise AdoptionError("generated_candidate_drift")
                if captured is not None:
                    if relative in validators:
                        validators[relative](before, bytes(captured))
                    if relative in projectors:
                        projected = projectors[relative](bytes(captured))
                        if type(projected) is not bytes:
                            raise AdoptionError("generated_candidate_drift")
                        hasher = hashlib.sha256(projected)
                        projected_size = len(projected)
                    else:
                        projected_size = info.st_size
                else:
                    projected_size = info.st_size
                rows.append(
                    (
                        relative,
                        f"f {stat.S_IMODE(info.st_mode):04o} {projected_size} "
                        f"{hasher.hexdigest()} {relative}\n",
                    )
                )
            else:
                raise AdoptionError("generated_candidate_drift")
        if _archive_stat_identity(current_before) != _archive_stat_identity(
            os.fstat(current)
        ):
            raise AdoptionError("generated_candidate_drift")

    try:
        walk(descriptor)
        for parent, name, file_descriptor, identity in held_files:
            if (
                identity != _archive_stat_identity(os.fstat(file_descriptor))
                or identity
                != _archive_stat_identity(
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                )
            ):
                raise AdoptionError("generated_candidate_drift")
        for parent, name, child, identity in reversed(held_directories):
            if (
                identity != _archive_stat_identity(os.fstat(child))
                or identity
                != _archive_stat_identity(
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                )
            ):
                raise AdoptionError("generated_candidate_drift")
        if _archive_stat_identity(root_before) != _archive_stat_identity(
            os.fstat(descriptor)
        ):
            raise AdoptionError("generated_candidate_drift")
        return tuple(
            hashlib.sha256(
                "".join(
                    row
                    for relative, row in rows
                    if not path_is_ignored(relative, ignored)
                ).encode("utf-8")
            ).hexdigest()
            for ignored in ignored_sets
        )
    except OSError as exc:
        raise AdoptionError("generated_candidate_drift") from exc
    finally:
        for _parent, _name, file_descriptor, _identity in held_files:
            os.close(file_descriptor)
        for _parent, _name, child, _identity in reversed(held_directories):
            os.close(child)


def _tree_digest_from_descriptor(
    descriptor: int,
    *,
    ignored: frozenset[str] = frozenset(),
) -> str:
    return _tree_digests_from_descriptor(
        descriptor,
        ignored_sets=(ignored,),
    )[0]


def _bounded_private_tree_digest(
    path: Path,
    *,
    ignored: frozenset[str] = frozenset(),
    file_projectors: dict[str, Callable[[bytes], bytes]] | None = None,
) -> str | None:
    if not path.exists() and not path.is_symlink():
        return None
    parent_descriptor = descriptor = -1
    try:
        parent_descriptor, descriptor, parent_identity, identity = (
            _open_bound_directory(path)
        )
        digest = _tree_digests_from_descriptor(
            descriptor,
            ignored_sets=(ignored,),
            file_projectors=file_projectors,
            maximum_depth=_MAX_ROLLBACK_SNAPSHOT_DEPTH,
            maximum_entries=_MAX_ROLLBACK_SNAPSHOT_ENTRIES,
            maximum_bytes=_MAX_ROLLBACK_SNAPSHOT_BYTES,
        )[0]
        _recheck_bound_directory(
            path,
            parent_descriptor,
            descriptor,
            parent_identity,
            identity,
        )
        return digest
    except AdoptionError as exc:
        if str(exc) == "terminal_rollback_capture_limit_exceeded":
            raise
        raise AdoptionError(
            "terminal_rollback_source_metadata_invalid"
        ) from exc
    except OSError as exc:
        raise AdoptionError(
            "terminal_rollback_source_metadata_invalid"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _publish_private_snapshot_stage(
    stage: Path,
    destination: Path,
    *,
    expected_digest: str,
    digest_mismatch_code: str = "terminal_rollback_capture_drift",
) -> None:
    parent_descriptor = descriptor = -1
    try:
        parent_descriptor, descriptor, parent_identity, identity = (
            _open_bound_directory(stage)
        )
        digest = _tree_digests_from_descriptor(
            descriptor,
            ignored_sets=(frozenset(),),
            maximum_depth=_MAX_ROLLBACK_SNAPSHOT_DEPTH,
            maximum_entries=_MAX_ROLLBACK_SNAPSHOT_ENTRIES,
            maximum_bytes=_MAX_ROLLBACK_SNAPSHOT_BYTES,
        )[0]
        _recheck_bound_directory(
            stage,
            parent_descriptor,
            descriptor,
            parent_identity,
            identity,
        )
        if digest != expected_digest:
            raise AdoptionError(digest_mismatch_code)
        _rename_directory_exclusive(
            parent_descriptor,
            stage.name,
            destination.name,
        )
        published = _archive_stat_identity(
            os.stat(
                destination.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        held = _archive_stat_identity(os.fstat(descriptor))
        if (
            not _archive_root_rename_identity_matches(identity, published)
            or not _archive_root_rename_identity_matches(identity, held)
        ):
            try:
                _rename_directory_exclusive(
                    parent_descriptor,
                    destination.name,
                    stage.name,
                )
                os.fsync(parent_descriptor)
            except OSError:
                pass
            raise AdoptionError("terminal_rollback_capture_drift")
        os.fsync(parent_descriptor)
        parent_after = _archive_stat_identity(
            os.fstat(parent_descriptor)
        )
        path_parent_after = _archive_stat_identity(
            destination.parent.lstat()
        )
        if (
            parent_identity[:6] != parent_after[:6]
            or parent_after[:6] != path_parent_after[:6]
        ):
            raise AdoptionError("terminal_rollback_capture_drift")
    except AdoptionError:
        raise
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise AdoptionError(
                "terminal_rollback_capture_cross_device"
            ) from exc
        raise AdoptionError("terminal_rollback_capture_drift") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if _bounded_private_tree_digest(destination) != expected_digest:
        raise AdoptionError("terminal_rollback_capture_drift")


def _project_terminal_runtime_binding(content: bytes) -> bytes:
    try:
        value = json.loads(content.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("terminal_install_projection_invalid") from exc
    if type(value) is not dict or type(value.get("source_root")) is not str:
        raise AdoptionError("terminal_install_projection_invalid")
    projected = dict(value)
    projected["source_root"] = "<materialized-source-root>"
    return _json_bytes(projected)


def _project_terminal_source_manifest(content: bytes) -> bytes:
    try:
        value = json.loads(content.decode("ascii"))
        mcp = value["mcp"]
        locator = mcp["locator"]
        source_binding = value["operational_source_binding"]
        self_binding = source_binding["self_content_binding"]
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as exc:
        raise AdoptionError("terminal_install_projection_invalid") from exc
    if (
        type(value) is not dict
        or type(mcp) is not dict
        or type(locator) is not dict
        or _safe_sha256(locator.get("binding_sha256")) is None
        or type(source_binding) is not dict
        or type(self_binding) is not dict
        or _safe_sha256(self_binding.get("digest")) is None
    ):
        raise AdoptionError("terminal_install_projection_invalid")
    projected = json.loads(json.dumps(value))
    projected["mcp"]["locator"]["binding_sha256"] = (
        "<materialized-binding-digest>"
    )
    projected["operational_source_binding"]["self_content_binding"][
        "digest"
    ] = "<materialized-self-digest>"
    return _json_bytes(projected)


def _terminal_install_projection_digest(root: Path) -> str:
    digest = _bounded_private_tree_digest(
        root,
        ignored=frozenset({
            ".in_use",
            distribution.ORPHANED_INSTALLED_MARKER,
        }),
        file_projectors={
            "SOURCE_MANIFEST.json": _project_terminal_source_manifest,
            "runtime/RUNTIME_BINDING.json": _project_terminal_runtime_binding,
        },
    )
    if digest is None:
        raise AdoptionError("terminal_install_projection_invalid")
    return digest


def _validate_generated_orphan_marker(
    info: os.stat_result,
    content: bytes,
) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_gid != os.getgid()
        or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_nlink != 1
        or info.st_size != 13
        or len(content) != 13
        or not content.isdigit()
    ):
        raise AdoptionError("generated_candidate_drift")


def _find_plugin_row(value: object) -> dict[str, object] | None:
    """Project host JSON onto the one fixed plugin without retaining raw data."""

    matches: list[dict[str, object]] = []

    def visit(node: object) -> None:
        if type(node) is dict:
            strings = {
                str(item) for item in node.values() if type(item) is str
            }
            if (
                authority.PLUGIN_ID in strings
                or f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}" in strings
            ):
                matches.append(node)
            for child in node.values():
                visit(child)
        elif type(node) is list:
            for child in node:
                visit(child)

    visit(value)
    if len(matches) > 1:
        raise AdoptionError("host_registry_ambiguous")
    return matches[0] if matches else None


def _find_marketplace_row(value: object) -> dict[str, object] | None:
    matches: list[dict[str, object]] = []

    def visit(node: object) -> None:
        if type(node) is dict:
            if authority.MARKETPLACE_ID in {
                item for item in node.values() if type(item) is str
            }:
                matches.append(node)
                return
            for child in node.values():
                visit(child)
        elif type(node) is list:
            for child in node:
                visit(child)

    visit(value)
    if len(matches) > 1:
        raise AdoptionError("host_registry_ambiguous")
    return matches[0] if matches else None


def _marketplace_source(row: dict[str, object] | None) -> Path | None:
    """Project an admitted local source path without retaining the raw row."""

    if row is None:
        return None
    candidates: set[str] = set()

    def visit(value: object, key: str = "") -> None:
        if type(value) is dict:
            for child_key, child in value.items():
                if type(child_key) is str:
                    visit(child, child_key)
        elif type(value) is list:
            for child in value:
                visit(child, key)
        elif type(value) is str and key in {
            "location",
            "path",
            "source",
            "sourcePath",
        }:
            candidate = Path(value)
            if candidate.is_absolute():
                candidates.add(value)

    visit(row)
    if len(candidates) != 1:
        raise AdoptionError("marketplace_binding_unavailable")
    source = Path(candidates.pop())
    try:
        return source.resolve(strict=True)
    except OSError as exc:
        raise AdoptionError("marketplace_binding_unavailable") from exc


def _row_projection(row: dict[str, object] | None) -> tuple[bool, str | None, bool]:
    if row is None:
        return False, None, False
    version = row.get("version")
    if type(version) is not str:
        version = row.get("pluginVersion")
    if _safe_plugin_version(version) is None:
        raise AdoptionError("host_registry_invalid")
    enabled = row.get("enabled")
    if type(enabled) is not bool:
        state = row.get("state") or row.get("status")
        if state not in {"enabled", "disabled", "installed", "active"}:
            raise AdoptionError("host_registry_invalid")
        enabled = state in {"enabled", "installed", "active"}
    return True, version, enabled


class FixedHostAdapter:
    """A fixed Codex or Claude adapter; no caller-selected paths/commands."""

    def __init__(
        self,
        name: str,
        transaction_root: Path,
        *,
        previous_transaction_root: Path | None = None,
        previous_state: HostState | None = None,
        terminal_source_transaction_root: Path | None = None,
    ):
        if name not in HOST_ORDER:
            raise AdoptionError("host_not_admitted")
        self.name = name
        self._transaction_root = transaction_root
        if (previous_transaction_root is None) is not (previous_state is None):
            raise AdoptionError("previous_committed_state_unavailable")
        if previous_state is not None and (
            previous_state.host != name
            or previous_state.plugin_version
            != authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
        ):
            raise AdoptionError("previous_committed_state_unavailable")
        self._previous_transaction_root = previous_transaction_root
        self._previous_state = previous_state
        self._terminal_source_transaction_root = terminal_source_transaction_root
        self._terminal_bridge_state: TerminalHostState | None = None
        self._terminal_bridge_binding_digest: str | None = None
        self._terminal_bridge_capture: dict[str, object] | None = None
        home = _fixed_user_home()
        if name == "codex":
            self._cli = _CODEX_CLI
            self._host_home = home / ".codex"
            self._cache = (
                self._host_home
                / "plugins"
                / "cache"
                / authority.MARKETPLACE_ID
                / authority.PLUGIN_ID
                / authority.PLUGIN_VERSION
            )
        else:
            self._cli = _CLAUDE_CLI
            self._host_home = home / ".claude"
            self._cache = (
                self._host_home
                / "plugins"
                / "cache"
                / authority.MARKETPLACE_ID
                / authority.PLUGIN_ID
                / authority.PLUGIN_VERSION
            )
        self._marketplace_cache = (
            self._host_home
            / "plugins"
            / "marketplaces"
            / authority.MARKETPLACE_ID
        )

    def configure_terminal_bridge(
        self,
        terminal_state: TerminalHostState,
        binding_digest: str,
    ) -> None:
        if (
            self._terminal_source_transaction_root is None
            or self._previous_state is not None
            or terminal_state.host != self.name
            or terminal_state.plugin_version
            != authority.TERMINAL_PLUGIN_VERSION
            or _safe_sha256(binding_digest) is None
        ):
            raise AdoptionError("terminal_bridge_adapter_unavailable")
        self._terminal_bridge_state = terminal_state
        self._terminal_bridge_binding_digest = binding_digest

    def _run(self, args: tuple[str, ...], *, json_output: bool = False) -> object:
        identity = _admit_fixed_cli(self._cli, self._transaction_root)
        completed = _invoke_fixed_cli(
            self._cli,
            args,
            expected_final=identity[-1],
            transaction_root=self._transaction_root,
        )
        if _capture_fixed_cli(self._cli) != identity:
            raise AdoptionError("host_cli_drift")
        if completed.returncode != 0 or len(completed.stdout) > _MAX_COMMAND_OUTPUT:
            raise AdoptionError("host_cli_operation_failed")
        if not json_output:
            return None
        try:
            return json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdoptionError("host_registry_invalid") from exc

    def _marketplace_row(self) -> dict[str, object] | None:
        value = self._run(
            ("plugin", "marketplace", "list", "--json"),
            json_output=True,
        )
        return _find_marketplace_row(value)

    def _binding_digest(
        self,
        *,
        source: Path,
        version: str | None,
        marketplace_digest: str | None,
    ) -> str:
        def matches_root(root: Path) -> bool:
            return source in {
                root,
                root / ".claude-plugin" / "marketplace.json",
                root / ".agents" / "plugins" / "marketplace.json",
            }

        if version == PREDECESSOR_VERSION:
            predecessor = _resolve_predecessor_source()
            if not matches_root(predecessor):
                raise AdoptionError("marketplace_binding_unadmitted")
            return _predecessor_binding_digest()
        if (
            version == authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
            and self._previous_state is not None
        ):
            expected = self._previous_stage_marketplace_root().resolve(strict=True)
            if (
                not matches_root(expected)
                or marketplace_digest != self._previous_state.marketplace_digest
                or self._previous_state.marketplace_binding_digest is None
            ):
                raise AdoptionError("marketplace_binding_unadmitted")
            return self._previous_state.marketplace_binding_digest
        if (
            version == authority.TERMINAL_PLUGIN_VERSION
            and self._terminal_bridge_state is not None
            and self._terminal_bridge_binding_digest is not None
        ):
            expected = self._terminal_admitted_marketplace_root(source)
            if (
                not matches_root(expected)
                or marketplace_digest
                != self._terminal_bridge_state.marketplace_digest
            ):
                raise AdoptionError("marketplace_binding_unadmitted")
            return self._terminal_bridge_binding_digest
        if version == authority.PLUGIN_VERSION:
            try:
                expected = self._stage_marketplace_root().resolve(strict=True)
            except OSError as exc:
                raise AdoptionError("marketplace_binding_unavailable") from exc
            if not matches_root(expected) or marketplace_digest is None:
                raise AdoptionError("marketplace_binding_unadmitted")
            return _candidate_binding_digest(
                source_revision=_source_revision(),
                source_bundle_digest=_source_bundle_digest(),
                marketplace_digest=marketplace_digest,
            )
        raise AdoptionError("marketplace_binding_unadmitted")

    def _candidate_stage_marketplace_digest(
        self,
        *,
        source: Path,
        cache_digest: str | None,
    ) -> str:
        """Verify and project the exact private stage used by a local registry."""

        if cache_digest is None:
            raise AdoptionError("marketplace_binding_unadmitted")
        stage = self._stage_marketplace_root()
        try:
            expected = stage.resolve(strict=True)
        except OSError as exc:
            raise AdoptionError("marketplace_binding_unavailable") from exc
        if source not in {
            expected,
            expected / ".claude-plugin" / "marketplace.json",
            expected / ".agents" / "plugins" / "marketplace.json",
        }:
            raise AdoptionError("marketplace_binding_unadmitted")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(expected, flags)
        except OSError as exc:
            raise AdoptionError("marketplace_stage_drift") from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o700
            ):
                raise AdoptionError("marketplace_stage_drift")
            marketplace_digest = _tree_digest(expected)
            if marketplace_digest is None:
                raise AdoptionError("marketplace_stage_drift")
            self._verify_marketplace_root(
                expected,
                version=authority.PLUGIN_VERSION,
                expected_bundle_digest=cache_digest,
                expected_marketplace_digest=marketplace_digest,
                verify_current_bundle=True,
            )
            after = os.fstat(descriptor)
            path_after = expected.lstat()
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_uid,
                before.st_gid,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_gid,
            ) != (
                path_after.st_dev,
                path_after.st_ino,
                path_after.st_mode,
                path_after.st_uid,
                path_after.st_gid,
            ) or source.resolve(strict=True) not in {
                expected,
                expected / ".claude-plugin" / "marketplace.json",
                expected / ".agents" / "plugins" / "marketplace.json",
            }:
                raise AdoptionError("marketplace_stage_drift")
        except OSError as exc:
            raise AdoptionError("marketplace_stage_drift") from exc
        finally:
            os.close(descriptor)
        return marketplace_digest

    def _previous_stage_marketplace_digest(
        self,
        *,
        source: Path,
        cache_digest: str | None,
    ) -> str:
        if self._previous_state is None or cache_digest is None:
            raise AdoptionError("previous_committed_state_unavailable")
        expected = self._previous_stage_marketplace_root()
        try:
            resolved = expected.resolve(strict=True)
        except OSError as exc:
            raise AdoptionError("previous_committed_source_unavailable") from exc
        if source.resolve(strict=True) not in {
            resolved,
            resolved / ".claude-plugin" / "marketplace.json",
            resolved / ".agents" / "plugins" / "marketplace.json",
        }:
            raise AdoptionError("marketplace_binding_unadmitted")
        marketplace_digest = _tree_digest(resolved)
        if marketplace_digest != self._previous_state.marketplace_digest:
            raise AdoptionError("previous_committed_state_drift")
        self._verify_marketplace_root(
            resolved,
            version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
            expected_bundle_digest=cache_digest,
            expected_marketplace_digest=marketplace_digest,
            verify_current_bundle=True,
        )
        return marketplace_digest

    @staticmethod
    def _cache_leaf_identity(info: os.stat_result) -> str:
        return _canonical_digest({
            "device": info.st_dev,
            "group": info.st_gid,
            "inode": info.st_ino,
            "mode": stat.S_IMODE(info.st_mode),
            "owner": info.st_uid,
        })

    def _cache_quarantine_projection(
        self,
        *,
        active_version: str | None,
    ) -> tuple[tuple[QuarantineEntry, ...], int, int, int]:
        cache_parent = self._cache.parent
        _validate_fixed_host_chain(cache_parent, allow_missing=True)
        if not cache_parent.exists():
            return (), 0, 0, 0
        root_info = self._transaction_root.lstat()
        if (
            stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            raise AdoptionError("quarantine_root_unavailable")
        entries: list[QuarantineEntry] = []
        foreign_count = 0
        invalid_count = 0
        ambiguous_count = 0
        seen: set[str] = set()
        for leaf in cache_parent.iterdir():
            try:
                info = leaf.lstat()
            except OSError as exc:
                raise AdoptionError("plugin_cache_leaf_drift") from exc
            version = _safe_plugin_version(leaf.name)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or version is None
            ):
                invalid_count += 1
                continue
            if version in seen:
                ambiguous_count += 1
                continue
            seen.add(version)
            if version not in TARGET_CACHE_VERSIONS:
                foreign_count += 1
                continue
            requires_quarantine = version != active_version or (
                self.name == "claude" and version == PREDECESSOR_VERSION
            )
            if not requires_quarantine:
                continue
            if info.st_dev != root_info.st_dev:
                raise AdoptionError("quarantine_cross_filesystem")
            cache_digest = _tree_digest(leaf, ignored=frozenset({".in_use"}))
            full_digest = _tree_digest(leaf)
            if cache_digest is None or full_digest is None:
                raise AdoptionError("plugin_cache_leaf_drift")
            marker = leaf / ".in_use"
            entries.append(QuarantineEntry(
                handle=TARGET_CACHE_HANDLES[version],
                version=version,
                cache_digest=cache_digest,
                full_digest=full_digest,
                identity_digest=self._cache_leaf_identity(info),
                in_use_present=marker.exists() or marker.is_symlink(),
            ))
        order = {version: index for index, version in enumerate(TARGET_CACHE_VERSIONS)}
        entries.sort(key=lambda entry: order[entry.version])
        return tuple(entries), foreign_count, invalid_count, ambiguous_count

    def observe(self) -> HostState:
        _validate_fixed_host_chain(self._host_home, allow_missing=False)
        _validate_fixed_host_chain(
            self._marketplace_cache, allow_missing=True
        )
        if self.name == "codex":
            listing = self._run(("plugin", "list", "--json"), json_output=True)
        else:
            listing = self._run(("plugin", "list", "--json"), json_output=True)
        present, version, active = _row_projection(_find_plugin_row(listing))
        cache = self._cache if version == authority.PLUGIN_VERSION else self._cache.with_name(version or "absent")
        _validate_fixed_host_chain(cache, allow_missing=True)
        _validate_fixed_host_chain(self._cache, allow_missing=True)
        cache_ignored = {".in_use"}
        if (
            version == authority.TERMINAL_PLUGIN_VERSION
            and self._terminal_bridge_state is not None
            and self._terminal_bridge_state.orphan_marker_digest is not None
        ):
            cache_ignored.add(distribution.ORPHANED_INSTALLED_MARKER)
        cache_digest = _tree_digest(cache, ignored=frozenset(cache_ignored))
        if present is not (cache_digest is not None):
            raise AdoptionError("plugin_registry_cache_mismatch")
        (
            quarantine_entries,
            foreign_cache_leaf_count,
            invalid_cache_leaf_count,
            ambiguous_cache_leaf_count,
        ) = self._cache_quarantine_projection(active_version=version)
        marketplace_row = self._marketplace_row()
        marketplace_present = marketplace_row is not None
        physical_marketplace_digest = _tree_digest(self._marketplace_cache)
        marketplace_digest = physical_marketplace_digest
        marketplace_binding_digest = None
        if marketplace_present:
            source = _marketplace_source(marketplace_row)
            if version == authority.PLUGIN_VERSION:
                if physical_marketplace_digest is not None:
                    raise AdoptionError("marketplace_registry_cache_mismatch")
                marketplace_digest = self._candidate_stage_marketplace_digest(
                    source=source,
                    cache_digest=cache_digest,
                )
            elif (
                version == authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
                and self._previous_state is not None
            ):
                if physical_marketplace_digest is not None:
                    raise AdoptionError("marketplace_registry_cache_mismatch")
                marketplace_digest = self._previous_stage_marketplace_digest(
                    source=source,
                    cache_digest=cache_digest,
                )
            elif (
                version == authority.TERMINAL_PLUGIN_VERSION
                and self._terminal_bridge_state is not None
            ):
                if physical_marketplace_digest is not None:
                    raise AdoptionError("marketplace_registry_cache_mismatch")
                source_root = self._terminal_admitted_marketplace_root(
                    source
                )
                marketplace_digest = _tree_digest(source_root)
                if (
                    marketplace_digest
                    != self._terminal_bridge_state.marketplace_digest
                    or cache_digest
                    != self._terminal_bridge_state.cache_digest
                ):
                    raise AdoptionError("terminal_current_state_drift")
            marketplace_binding_digest = self._binding_digest(
                source=source,
                version=version,
                marketplace_digest=marketplace_digest,
            )
            exact_predecessor = (
                present
                and version == PREDECESSOR_VERSION
                and active
                and cache_digest
                in {
                    PREDECESSOR_BUNDLE_DIGEST,
                    CLAUDE_RESIDUAL_PREDECESSOR_DIGEST,
                }
                and marketplace_binding_digest == _predecessor_binding_digest()
            )
            if exact_predecessor:
                if physical_marketplace_digest is not None:
                    raise AdoptionError("marketplace_registry_cache_mismatch")
                marketplace_digest = _predecessor_marketplace_digest()
            elif version not in {
                authority.PLUGIN_VERSION,
                authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
                authority.TERMINAL_PLUGIN_VERSION,
            }:
                raise AdoptionError("marketplace_registry_cache_mismatch")
        elif present or physical_marketplace_digest is not None:
            raise AdoptionError("marketplace_registry_cache_mismatch")
        observed_state = HostState(
            host=self.name,
            marketplace_present=marketplace_present,
            marketplace_digest=marketplace_digest,
            marketplace_binding_digest=marketplace_binding_digest,
            plugin_present=present,
            plugin_version=version,
            active=active,
            cache_digest=cache_digest,
            quarantine_entries=quarantine_entries,
            foreign_cache_leaf_count=foreign_cache_leaf_count,
            invalid_cache_leaf_count=invalid_cache_leaf_count,
            ambiguous_cache_leaf_count=ambiguous_cache_leaf_count,
        )
        if (
            version == authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
            and self._previous_state is not None
            and observed_state != self._previous_state
        ):
            raise AdoptionError("previous_committed_state_drift")
        if (
            version == authority.TERMINAL_PLUGIN_VERSION
            and self._terminal_bridge_state is not None
            and (
                observed_state.marketplace_digest
                != self._terminal_bridge_state.marketplace_digest
                or observed_state.cache_digest
                != self._terminal_bridge_state.cache_digest
                or observed_state.active
                is not (
                    self._terminal_bridge_state.registry_state
                    in {"active", "installed"}
                )
            )
        ):
            raise AdoptionError("terminal_current_state_drift")
        return observed_state

    def _stage_marketplace_root(self) -> Path:
        return (
            self._transaction_root
            / "stage"
            / self.name
        )

    def _previous_stage_marketplace_root(self) -> Path:
        if self._previous_transaction_root is None:
            raise AdoptionError("previous_committed_source_unavailable")
        return self._previous_transaction_root / "stage" / self.name

    def _terminal_stage_marketplace_root(self) -> Path:
        if self._terminal_source_transaction_root is None:
            raise AdoptionError("terminal_rollback_source_unavailable")
        return self._terminal_source_transaction_root / "stage" / self.name

    def _terminal_admitted_marketplace_root(self, source: Path) -> Path:
        """Resolve only the terminal source the host registry actually names."""

        candidates = (
            self._rollback_root() / "marketplace",
            self._terminal_stage_marketplace_root(),
        )
        for candidate in candidates:
            try:
                info = candidate.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o022
                ):
                    continue
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if source in {
                resolved,
                resolved / ".claude-plugin" / "marketplace.json",
                resolved / ".agents" / "plugins" / "marketplace.json",
            }:
                return resolved
        raise AdoptionError("marketplace_binding_unadmitted")

    @staticmethod
    def _terminal_marketplace_bundle_root(marketplace_root: Path) -> Path:
        return (
            marketplace_root
            / "distribution"
            / authority.MARKETPLACE_ID
            / authority.PLUGIN_ID
            / authority.TERMINAL_PLUGIN_VERSION
        )

    def _stage_root(self) -> Path:
        return (
            self._stage_marketplace_root()
            / "distribution"
            / authority.MARKETPLACE_ID
            / authority.PLUGIN_ID
            / authority.PLUGIN_VERSION
        )

    @staticmethod
    def _marketplace_manifest(version: str) -> dict[str, object]:
        return {
            "description": "Bounded protected plugin-adoption marketplace.",
            "name": authority.MARKETPLACE_ID,
            "owner": {"name": "ORCH-Next Hermes"},
            "plugins": [{
                "description": "Protected ORCH-Next Hermes harness.",
                "name": authority.PLUGIN_ID,
                "source": (
                    f"./distribution/{authority.MARKETPLACE_ID}/"
                    f"{authority.PLUGIN_ID}/{version}"
                ),
                "version": version,
            }],
        }

    @staticmethod
    def _exact_directory_names(path: Path, expected: frozenset[str]) -> None:
        try:
            info = path.lstat()
            names = frozenset(child.name for child in path.iterdir())
        except OSError as exc:
            raise AdoptionError("marketplace_stage_drift") from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or names != expected
        ):
            raise AdoptionError("marketplace_stage_drift")

    def _verify_marketplace_root(
        self,
        marketplace_root: Path,
        *,
        version: str,
        expected_bundle_digest: str,
        expected_marketplace_digest: str,
        verify_current_bundle: bool,
    ) -> None:
        """Bind the exact private marketplace bytes consumed by the host CLI."""

        self._exact_directory_names(
            marketplace_root,
            frozenset({".agents", ".claude-plugin", "distribution"}),
        )
        self._exact_directory_names(
            marketplace_root / ".claude-plugin",
            frozenset({"marketplace.json"}),
        )
        self._exact_directory_names(
            marketplace_root / ".agents",
            frozenset({"plugins"}),
        )
        self._exact_directory_names(
            marketplace_root / ".agents" / "plugins",
            frozenset({"marketplace.json"}),
        )
        self._exact_directory_names(
            marketplace_root / "distribution",
            frozenset({authority.MARKETPLACE_ID}),
        )
        self._exact_directory_names(
            marketplace_root / "distribution" / authority.MARKETPLACE_ID,
            frozenset({authority.PLUGIN_ID}),
        )
        self._exact_directory_names(
            marketplace_root
            / "distribution"
            / authority.MARKETPLACE_ID
            / authority.PLUGIN_ID,
            frozenset({version}),
        )
        expected_manifest = _json_bytes(self._marketplace_manifest(version))
        try:
            actual_manifests = (
                _private_file_bytes(
                    marketplace_root / ".claude-plugin" / "marketplace.json"
                ),
                _private_file_bytes(
                    marketplace_root
                    / ".agents"
                    / "plugins"
                    / "marketplace.json"
                ),
            )
        except (OSError, AdoptionError) as exc:
            raise AdoptionError("marketplace_stage_drift") from exc
        if actual_manifests != (expected_manifest, expected_manifest):
            raise AdoptionError("marketplace_stage_drift")
        bundle = (
            marketplace_root
            / "distribution"
            / authority.MARKETPLACE_ID
            / authority.PLUGIN_ID
            / version
        )
        if verify_current_bundle:
            try:
                if version == authority.PLUGIN_VERSION:
                    distribution.verify_installed_bundle(bundle)
                elif version != authority.PREVIOUS_TERMINAL_PLUGIN_VERSION:
                    raise AdoptionError("prepared_cache_drift")
            except AdoptionError:
                raise
            except Exception as exc:
                raise AdoptionError("prepared_cache_drift") from exc
        if (
            _tree_digest(bundle, ignored=frozenset({".in_use"}))
            != expected_bundle_digest
        ):
            raise AdoptionError("prepared_cache_drift")
        if _tree_digest(marketplace_root) != expected_marketplace_digest:
            raise AdoptionError("marketplace_stage_drift")

    def _stage_candidate_root(self) -> Path:
        return (
            self._transaction_root
            / "stage"
            / f".{self.name}-bundle-candidate"
            / "distribution"
            / authority.MARKETPLACE_ID
            / authority.PLUGIN_ID
            / authority.PLUGIN_VERSION
        )

    def _verify_partial_marketplace_root(self, marketplace_root: Path) -> None:
        """Admit only resumable private stage prefixes, never arbitrary content."""

        try:
            info = marketplace_root.lstat()
            names = frozenset(child.name for child in marketplace_root.iterdir())
        except OSError as exc:
            raise AdoptionError("marketplace_stage_drift") from exc
        allowed = frozenset({".agents", ".claude-plugin", "distribution"})
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or not names <= allowed
        ):
            raise AdoptionError("marketplace_stage_drift")

        for name in (".agents", ".claude-plugin", "distribution"):
            child = marketplace_root / name
            if not child.exists() and not child.is_symlink():
                continue
            try:
                child_info = child.lstat()
                child_names = frozenset(item.name for item in child.iterdir())
            except OSError as exc:
                raise AdoptionError("marketplace_stage_drift") from exc
            if (
                stat.S_ISLNK(child_info.st_mode)
                or not stat.S_ISDIR(child_info.st_mode)
                or child_info.st_uid != os.getuid()
                or (
                    name != "distribution"
                    and stat.S_IMODE(child_info.st_mode) != 0o700
                )
                or (name == "distribution" and child_info.st_mode & 0o022)
            ):
                raise AdoptionError("marketplace_stage_drift")
            if name == ".claude-plugin" and not child_names <= frozenset(
                {"marketplace.json"}
            ):
                raise AdoptionError("marketplace_stage_drift")
            if name == ".agents" and not child_names <= frozenset({"plugins"}):
                raise AdoptionError("marketplace_stage_drift")
            if name == "distribution" and not child_names <= frozenset(
                {authority.MARKETPLACE_ID}
            ):
                raise AdoptionError("marketplace_stage_drift")

        expected_manifest = _json_bytes(
            self._marketplace_manifest(authority.PLUGIN_VERSION)
        )
        for manifest in (
            marketplace_root / ".claude-plugin" / "marketplace.json",
            marketplace_root / ".agents" / "plugins" / "marketplace.json",
        ):
            if not manifest.exists() and not manifest.is_symlink():
                continue
            try:
                if _private_file_bytes(manifest) != expected_manifest:
                    raise AdoptionError("marketplace_stage_drift")
            except OSError as exc:
                raise AdoptionError("marketplace_stage_drift") from exc

    @staticmethod
    def _verify_stage_bundle(path: Path, expected_digest: str) -> None:
        try:
            distribution.verify_installed_bundle(path)
        except Exception as exc:
            raise AdoptionError("marketplace_stage_conflict") from exc
        if _tree_digest(path, ignored=frozenset({".in_use"})) != expected_digest:
            raise AdoptionError("marketplace_stage_conflict")

    @staticmethod
    def _ensure_stage_directory_chain(base: Path, target: Path) -> None:
        try:
            relative = target.relative_to(base)
        except ValueError as exc:
            raise AdoptionError("marketplace_stage_conflict") from exc
        current = base
        for part in relative.parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                try:
                    current.mkdir(mode=0o755)
                    info = current.lstat()
                except OSError as exc:
                    raise AdoptionError("marketplace_stage_publish_failed") from exc
            except OSError as exc:
                raise AdoptionError("marketplace_stage_conflict") from exc
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
            ):
                raise AdoptionError("marketplace_stage_conflict")

    def _discard_stage_candidate(self, candidate: Path) -> None:
        candidate_container = (
            self._transaction_root
            / "stage"
            / f".{self.name}-bundle-candidate"
        )
        if candidate.exists() or candidate.is_symlink():
            try:
                info = candidate.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700
                ):
                    raise AdoptionError("marketplace_stage_conflict")
                distribution._remove_tree(candidate)
            except OSError as exc:
                raise AdoptionError("marketplace_stage_conflict") from exc
        if not candidate_container.exists() and not candidate_container.is_symlink():
            return
        try:
            candidate_container_info = candidate_container.lstat()
            if (
                stat.S_ISLNK(candidate_container_info.st_mode)
                or not stat.S_ISDIR(candidate_container_info.st_mode)
                or candidate_container_info.st_uid != os.getuid()
                or candidate_container_info.st_mode & 0o022
            ):
                raise AdoptionError("marketplace_stage_conflict")
            chain = [
                candidate.parent,
                candidate.parent.parent,
                candidate.parent.parent.parent,
                candidate_container,
            ]
            for directory in chain:
                if not directory.exists() and not directory.is_symlink():
                    continue
                info = directory.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o022
                    or frozenset(item.name for item in directory.iterdir())
                ):
                    raise AdoptionError("marketplace_stage_conflict")
                directory.rmdir()
                _fsync_directory(directory.parent)
        except OSError as exc:
            raise AdoptionError("marketplace_stage_conflict") from exc

    def _ensure_stage_bundle(self, expected_digest: str) -> None:
        stage = self._stage_root()
        candidate = self._stage_candidate_root()
        _lstat_admitted_directory(self._transaction_root / "stage", create=True)
        _lstat_admitted_directory(self._stage_marketplace_root(), create=True)
        self._ensure_stage_directory_chain(
            self._transaction_root / "stage",
            candidate.parent,
        )

        if candidate.exists() or candidate.is_symlink():
            self._verify_stage_bundle(candidate, expected_digest)
        else:
            try:
                distribution.transactional_install(
                    _REPO_ROOT / "skills" / "orch-next",
                    candidate,
                )
            except Exception as exc:
                raise AdoptionError("marketplace_stage_publish_failed") from exc
            self._verify_stage_bundle(candidate, expected_digest)
            _fsync_directory(candidate)

        self._ensure_stage_directory_chain(
            self._stage_marketplace_root(),
            stage.parent,
        )
        if stage.exists() or stage.is_symlink():
            self._verify_stage_bundle(stage, expected_digest)
            self._discard_stage_candidate(candidate)
            return

        source_parent_descriptor = destination_parent_descriptor = -1
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            source_parent_descriptor = os.open(candidate.parent, flags)
            destination_parent_descriptor = os.open(stage.parent, flags)
            def stable_directory_identity(info: os.stat_result) -> tuple[int, ...]:
                return (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_uid,
                    info.st_gid,
                )

            source_parent_identity = stable_directory_identity(
                os.fstat(source_parent_descriptor)
            )
            destination_parent_identity = stable_directory_identity(
                os.fstat(destination_parent_descriptor)
            )
            if (
                source_parent_identity
                != stable_directory_identity(candidate.parent.lstat())
                or destination_parent_identity
                != stable_directory_identity(stage.parent.lstat())
            ):
                raise AdoptionError("marketplace_stage_conflict")
            try:
                _rename_directory_between_exclusive(
                    source_parent_descriptor,
                    candidate.name,
                    destination_parent_descriptor,
                    stage.name,
                )
            except FileExistsError:
                self._verify_stage_bundle(stage, expected_digest)
                self._discard_stage_candidate(candidate)
                return
            if (
                source_parent_identity
                != stable_directory_identity(os.fstat(source_parent_descriptor))
                or destination_parent_identity
                != stable_directory_identity(
                    os.fstat(destination_parent_descriptor)
                )
            ):
                raise AdoptionError("marketplace_stage_conflict")
            os.fsync(source_parent_descriptor)
            os.fsync(destination_parent_descriptor)
            for directory in (
                stage.parent,
                stage.parent.parent,
                stage.parent.parent.parent,
                self._stage_marketplace_root(),
            ):
                _fsync_directory(directory)
        except OSError as exc:
            raise AdoptionError("marketplace_stage_publish_failed") from exc
        finally:
            if destination_parent_descriptor >= 0:
                os.close(destination_parent_descriptor)
            if source_parent_descriptor >= 0:
                os.close(source_parent_descriptor)
        self._verify_stage_bundle(stage, expected_digest)
        self._discard_stage_candidate(candidate)

    def _ensure_stage_manifest(self, path: Path, content: bytes) -> None:
        if path.exists() or path.is_symlink():
            try:
                if _private_file_bytes(path) != content:
                    raise AdoptionError("marketplace_stage_drift")
            except OSError as exc:
                raise AdoptionError("marketplace_stage_drift") from exc
            return
        _atomic_private_write(path, content)

    def prepare(self, transaction_id: str, expected_after: HostState) -> None:
        _safe_transaction_id(transaction_id)
        stage = self._stage_root()
        marketplace_root = self._stage_marketplace_root()
        if marketplace_root.exists() or marketplace_root.is_symlink():
            try:
                self._verify_marketplace_root(
                    marketplace_root,
                    version=authority.PLUGIN_VERSION,
                    expected_bundle_digest=str(expected_after.cache_digest),
                    expected_marketplace_digest=str(expected_after.marketplace_digest),
                    verify_current_bundle=True,
                )
                self._discard_stage_candidate(self._stage_candidate_root())
                return
            except AdoptionError:
                self._verify_partial_marketplace_root(marketplace_root)
        else:
            _lstat_admitted_directory(self._transaction_root / "stage", create=True)
            _lstat_admitted_directory(marketplace_root, create=True)

        self._ensure_stage_bundle(str(expected_after.cache_digest))
        manifest = self._marketplace_manifest(authority.PLUGIN_VERSION)
        manifest_bytes = _json_bytes(manifest)
        self._ensure_stage_manifest(
            marketplace_root / ".claude-plugin" / "marketplace.json",
            manifest_bytes,
        )
        self._ensure_stage_manifest(
            marketplace_root / ".agents" / "plugins" / "marketplace.json",
            manifest_bytes,
        )
        _fsync_directory(marketplace_root)
        self._verify_marketplace_root(
            marketplace_root,
            version=authority.PLUGIN_VERSION,
            expected_bundle_digest=str(expected_after.cache_digest),
            expected_marketplace_digest=str(expected_after.marketplace_digest),
            verify_current_bundle=True,
        )

    def _remove_plugin(self) -> None:
        selector = f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}"
        if self.name == "codex":
            self._run(("plugin", "remove", selector, "--json"))
        else:
            self._run(("plugin", "uninstall", selector, "--scope", "user", "--yes"))

    def _remove_marketplace(self) -> None:
        if self.name == "codex":
            self._run(("plugin", "marketplace", "remove", authority.MARKETPLACE_ID, "--json"))
        else:
            self._run(("plugin", "marketplace", "remove", authority.MARKETPLACE_ID, "--scope", "user"))

    def _cleanup_presence(self) -> tuple[bool, bool]:
        """Read only the two fixed registry identities for rollback cleanup."""

        plugins = self._run(("plugin", "list", "--json"), json_output=True)
        marketplaces = self._run(
            ("plugin", "marketplace", "list", "--json"), json_output=True
        )
        return _find_plugin_row(plugins) is not None, _find_marketplace_row(
            marketplaces
        ) is not None

    def _install_from(self, marketplace_root: Path) -> None:
        terminal_capture = (
            getattr(self, "_terminal_bridge_capture", None)
            if marketplace_root == self._rollback_root() / "marketplace"
            else None
        )

        def reverify_terminal_source() -> None:
            if terminal_capture is None:
                return
            if (
                _bounded_private_tree_digest(marketplace_root)
                != terminal_capture["source_digest"]
                or _terminal_install_projection_digest(
                    self._terminal_marketplace_bundle_root(marketplace_root)
                )
                != terminal_capture["install_projection_digest"]
            ):
                raise AdoptionError("terminal_rollback_source_drift")

        def verify_terminal_install_effect() -> None:
            if terminal_capture is None:
                return
            terminal = getattr(self, "_terminal_bridge_state", None)
            if terminal is None:
                raise AdoptionError("terminal_rollback_effect_mismatch")
            installed = self._cache.with_name(
                authority.TERMINAL_PLUGIN_VERSION
            )
            ignored = {".in_use"}
            if terminal.orphan_marker_digest is not None:
                ignored.add(distribution.ORPHANED_INSTALLED_MARKER)
            if (
                _bounded_private_tree_digest(
                    installed,
                    ignored=frozenset(ignored),
                )
                != terminal_capture["cache_digest"]
                or _terminal_install_projection_digest(installed)
                != terminal_capture["install_projection_digest"]
            ):
                raise AdoptionError("terminal_rollback_effect_mismatch")

        selector = f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}"
        reverify_terminal_source()
        if self.name == "codex":
            self._run(("plugin", "marketplace", "add", str(marketplace_root), "--json"))
            reverify_terminal_source()
            self._run(("plugin", "add", selector, "--json"))
            verify_terminal_install_effect()
            reverify_terminal_source()
        else:
            self._run(("plugin", "marketplace", "add", str(marketplace_root), "--scope", "user"))
            reverify_terminal_source()
            self._run(("plugin", "install", selector, "--scope", "user"))
            verify_terminal_install_effect()
            reverify_terminal_source()
            plugins = self._run(("plugin", "list", "--json"), json_output=True)
            present, version, active = _row_projection(_find_plugin_row(plugins))
            if marketplace_root == self._stage_marketplace_root():
                expected_version = authority.PLUGIN_VERSION
            elif (
                self._previous_transaction_root is not None
                and marketplace_root == self._previous_stage_marketplace_root()
            ):
                expected_version = authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
            elif (
                getattr(self, "_terminal_bridge_capture", None) is not None
                and marketplace_root
                == self._rollback_root() / "marketplace"
            ):
                expected_version = authority.TERMINAL_PLUGIN_VERSION
            else:
                expected_version = PREDECESSOR_VERSION
            if not present or version != expected_version:
                raise AdoptionError("host_registry_invalid")
            if not active:
                self._run(("plugin", "enable", selector, "--scope", "user"))

    def _rollback_root(self) -> Path:
        return self._transaction_root / "rollback" / self.name

    def _quarantine_root(self) -> Path:
        return self._transaction_root / "quarantine" / self.name

    def _entry_for_path(
        self,
        path: Path,
        *,
        version: str,
        handle: str,
    ) -> QuarantineEntry:
        try:
            info = path.lstat()
        except OSError as exc:
            raise AdoptionError("quarantine_entry_unavailable") from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
        ):
            raise AdoptionError("quarantine_entry_drift")
        cache_digest = _tree_digest(path, ignored=frozenset({".in_use"}))
        full_digest = _tree_digest(path)
        if cache_digest is None or full_digest is None:
            raise AdoptionError("quarantine_entry_drift")
        marker = path / ".in_use"
        return QuarantineEntry(
            handle=handle,
            version=version,
            cache_digest=cache_digest,
            full_digest=full_digest,
            identity_digest=self._cache_leaf_identity(info),
            in_use_present=marker.exists() or marker.is_symlink(),
        )

    def _quarantine_before(self, before: HostState) -> None:
        if not before.quarantine_entries:
            return
        quarantine = self._quarantine_root()
        _lstat_admitted_directory(quarantine, create=True)
        cache_parent = self._cache.parent
        _validate_fixed_host_chain(cache_parent, allow_missing=False)
        source_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        source_descriptor = os.open(cache_parent, source_flags)
        destination_descriptor = os.open(quarantine, source_flags)
        try:
            source_info = os.fstat(source_descriptor)
            destination_info = os.fstat(destination_descriptor)
            if source_info.st_dev != destination_info.st_dev:
                raise AdoptionError("quarantine_cross_filesystem")
            for entry in before.quarantine_entries:
                source = cache_parent / entry.version
                destination = quarantine / entry.handle
                source_present = source.exists() or source.is_symlink()
                destination_present = destination.exists() or destination.is_symlink()
                if source_present and destination_present:
                    raise AdoptionError("quarantine_entry_ambiguous")
                if destination_present:
                    if self._entry_for_path(
                        destination,
                        version=entry.version,
                        handle=entry.handle,
                    ) != entry:
                        raise AdoptionError("quarantine_entry_drift")
                    continue
                if not source_present or self._entry_for_path(
                    source,
                    version=entry.version,
                    handle=entry.handle,
                ) != entry:
                    raise AdoptionError("before_state_cas_mismatch")
                _rename_directory_between_exclusive(
                    source_descriptor,
                    entry.version,
                    destination_descriptor,
                    entry.handle,
                )
                if self._entry_for_path(
                    destination,
                    version=entry.version,
                    handle=entry.handle,
                ) != entry:
                    raise AdoptionError("quarantine_entry_drift")
                os.fsync(source_descriptor)
                os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
            os.close(source_descriptor)

    def _quarantine_previous_active_cache(self, before: HostState) -> None:
        """Remove a CLI-retained predecessor cache before candidate install.

        Claude can unregister a plugin without removing its cache directory.
        The signed predecessor stage remains the sole rollback source; this
        private same-filesystem quarantine is retained as non-authoritative
        evidence and is never used to reinstall or authorize execution.
        """

        terminal_active = (
            before.plugin_version == authority.TERMINAL_PLUGIN_VERSION
            and getattr(self, "_terminal_bridge_capture", None) is not None
        )
        if before != getattr(self, "_previous_state", None) and not terminal_active:
            return
        active_version = (
            authority.TERMINAL_PLUGIN_VERSION
            if terminal_active
            else authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
        )
        source = self._cache.with_name(active_version)
        handle = (
            "terminal-active-v048"
            if terminal_active
            else "previous-active-v" + active_version.replace(".", "")
        )
        destination = self._quarantine_root() / handle
        source_present = source.exists() or source.is_symlink()
        destination_present = destination.exists() or destination.is_symlink()
        if not source_present:
            if destination_present:
                self._entry_for_path(
                    destination,
                    version=active_version,
                    handle=handle,
                )
            return
        entry = self._entry_for_path(
            source,
            version=active_version,
            handle=handle,
        )
        self._quarantine_before(replace(before, quarantine_entries=(entry,)))

    def _registry_is_exact_predecessor(self) -> bool:
        plugins = self._run(("plugin", "list", "--json"), json_output=True)
        marketplaces = self._run(
            ("plugin", "marketplace", "list", "--json"), json_output=True
        )
        plugin_row = _find_plugin_row(plugins)
        marketplace_row = _find_marketplace_row(marketplaces)
        present, version, active = _row_projection(plugin_row)
        if not present or version != PREDECESSOR_VERSION or not active:
            return False
        if marketplace_row is None:
            return False
        source = _marketplace_source(marketplace_row)
        return (
            self._binding_digest(
                source=source,
                version=version,
                marketplace_digest=None,
            )
            == _predecessor_binding_digest()
        )

    def _failed_candidate_entry(
        self,
        path: Path,
    ) -> QuarantineEntry:
        parent_descriptor = descriptor = -1
        try:
            (
                parent_descriptor,
                descriptor,
                parent_identity,
                identity,
            ) = _open_bound_directory(path.parent)
            entry = self._failed_candidate_entry_at(descriptor, path.name)
            _recheck_bound_directory(
                path.parent,
                parent_descriptor,
                descriptor,
                parent_identity,
                identity,
            )
            return entry
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)

    def _failed_candidate_entry_at(
        self,
        parent_descriptor: int,
        name: str,
    ) -> QuarantineEntry:
        handle = _generated_candidate_quarantine_handle()
        marker_name = distribution.ORPHANED_INSTALLED_MARKER
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        stage_parent = stage_descriptor = -1
        try:
            root_before = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(root_before.st_mode)
                or root_before.st_uid != os.getuid()
                or root_before.st_mode & 0o022
                or _archive_stat_identity(root_before)
                != _archive_stat_identity(
                    os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                )
            ):
                raise AdoptionError("generated_candidate_drift")
            try:
                os.stat(".in_use", dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise AdoptionError("generated_candidate_drift")
            candidate_digest, cache_digest, full_digest = (
                _tree_digests_from_descriptor(
                    descriptor,
                    ignored_sets=(
                        frozenset({".in_use", marker_name}),
                        frozenset({".in_use"}),
                        frozenset(),
                    ),
                    file_validators={
                        marker_name: _validate_generated_orphan_marker,
                    },
                )
            )
            (
                stage_parent,
                stage_descriptor,
                stage_parent_identity,
                stage_identity,
            ) = _open_bound_directory(self._stage_root())
            stage_digest = _tree_digest_from_descriptor(
                stage_descriptor,
                ignored=frozenset({".in_use"}),
            )
            _recheck_bound_directory(
                self._stage_root(),
                stage_parent,
                stage_descriptor,
                stage_parent_identity,
                stage_identity,
            )
            root_after = os.fstat(descriptor)
            path_after = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                candidate_digest != stage_digest
                or _archive_stat_identity(root_before)
                != _archive_stat_identity(root_after)
                or _archive_stat_identity(root_before)
                != _archive_stat_identity(path_after)
            ):
                raise AdoptionError("generated_candidate_drift")
            return QuarantineEntry(
                handle=handle,
                version=authority.PLUGIN_VERSION,
                cache_digest=cache_digest,
                full_digest=full_digest,
                identity_digest=self._cache_leaf_identity(root_before),
                in_use_present=False,
            )
        except OSError as exc:
            raise AdoptionError("generated_candidate_drift") from exc
        finally:
            if stage_descriptor >= 0:
                os.close(stage_descriptor)
            if stage_parent >= 0:
                os.close(stage_parent)
            os.close(descriptor)

    def _quarantine_failed_candidate(self) -> None:
        source = self._cache
        quarantine = self._quarantine_root()
        _lstat_admitted_directory(quarantine, create=True)
        handle = _generated_candidate_quarantine_handle()
        source_parent = destination_parent = -1
        source_descriptor = destination_descriptor = -1
        try:
            try:
                (
                    source_parent,
                    source_descriptor,
                    source_parent_identity,
                    source_identity,
                ) = _open_bound_directory(self._cache.parent)
            except FileNotFoundError:
                if self._cache.parent.exists() or self._cache.parent.is_symlink():
                    raise AdoptionError("generated_candidate_parent_drift")
                return
            (
                destination_parent,
                destination_descriptor,
                destination_parent_identity,
                destination_identity,
            ) = _open_bound_directory(quarantine)
            if os.fstat(source_descriptor).st_dev != os.fstat(destination_descriptor).st_dev:
                raise AdoptionError("quarantine_cross_filesystem")
            try:
                os.stat(
                    authority.PLUGIN_VERSION,
                    dir_fd=source_descriptor,
                    follow_symlinks=False,
                )
                source_present = True
            except FileNotFoundError:
                source_present = False
            try:
                os.stat(handle, dir_fd=destination_descriptor, follow_symlinks=False)
                destination_present = True
            except FileNotFoundError:
                destination_present = False
            if source_present and destination_present:
                raise AdoptionError("generated_candidate_ambiguous")
            if destination_present:
                expected_recovered = self._failed_candidate_entry_at(
                    destination_descriptor,
                    handle,
                )
                os.fsync(source_descriptor)
                os.fsync(destination_descriptor)
                if self._failed_candidate_entry_at(
                    destination_descriptor,
                    handle,
                ) != expected_recovered:
                    raise AdoptionError("generated_candidate_drift")
                try:
                    os.stat(
                        authority.PLUGIN_VERSION,
                        dir_fd=source_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                else:
                    raise AdoptionError("generated_candidate_ambiguous")
                _recheck_bound_directory(
                    self._cache.parent,
                    source_parent,
                    source_descriptor,
                    source_parent_identity,
                    source_identity,
                )
                _recheck_bound_directory(
                    quarantine,
                    destination_parent,
                    destination_descriptor,
                    destination_parent_identity,
                    destination_identity,
                )
                return
            if not source_present:
                return
            expected = self._failed_candidate_entry_at(
                source_descriptor,
                authority.PLUGIN_VERSION,
            )
            _rename_directory_between_exclusive(
                source_descriptor,
                authority.PLUGIN_VERSION,
                destination_descriptor,
                handle,
            )
            if self._failed_candidate_entry_at(destination_descriptor, handle) != expected:
                raise AdoptionError("generated_candidate_drift")
            os.fsync(source_descriptor)
            os.fsync(destination_descriptor)
            if self._failed_candidate_entry_at(
                destination_descriptor,
                handle,
            ) != expected:
                raise AdoptionError("generated_candidate_drift")
            try:
                os.stat(
                    authority.PLUGIN_VERSION,
                    dir_fd=source_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise AdoptionError("generated_candidate_ambiguous")
            source_identity_after = _archive_stat_identity(
                os.fstat(source_descriptor)
            )
            destination_identity_after = _archive_stat_identity(
                os.fstat(destination_descriptor)
            )
            if (
                source_identity[:5] != source_identity_after[:5]
                or destination_identity[:5] != destination_identity_after[:5]
            ):
                raise AdoptionError("generated_candidate_parent_drift")
            _recheck_bound_directory(
                self._cache.parent,
                source_parent,
                source_descriptor,
                source_parent_identity,
                source_identity_after,
            )
            _recheck_bound_directory(
                quarantine,
                destination_parent,
                destination_descriptor,
                destination_parent_identity,
                destination_identity_after,
            )
        finally:
            for descriptor in (
                destination_descriptor,
                destination_parent,
                source_descriptor,
                source_parent,
            ):
                if descriptor >= 0:
                    os.close(descriptor)

    def _restore_quarantine_entries(self, before: HostState) -> None:
        if not before.quarantine_entries:
            return
        quarantine = self._quarantine_root()
        _lstat_admitted_directory(quarantine)
        cache_parent = self._cache.parent
        _validate_fixed_host_chain(cache_parent, allow_missing=False)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        source_descriptor = os.open(quarantine, flags)
        destination_descriptor = os.open(cache_parent, flags)
        try:
            if os.fstat(source_descriptor).st_dev != os.fstat(destination_descriptor).st_dev:
                raise AdoptionError("quarantine_cross_filesystem")
            for entry in before.quarantine_entries:
                quarantined = quarantine / entry.handle
                restored = cache_parent / entry.version
                quarantine_present = quarantined.exists() or quarantined.is_symlink()
                restored_present = restored.exists() or restored.is_symlink()
                if restored_present:
                    try:
                        restored_entry = self._entry_for_path(
                            restored,
                            version=entry.version,
                            handle=entry.handle,
                        )
                    except AdoptionError:
                        restored_entry = None
                    if restored_entry == entry:
                        if quarantine_present:
                            raise AdoptionError("quarantine_entry_ambiguous")
                        continue
                    if entry.version != PREDECESSOR_VERSION or not quarantine_present:
                        raise AdoptionError("quarantine_restore_conflict")
                    generated_handle = "generated-cache-v013"
                    generated = quarantine / generated_handle
                    if generated.exists() or generated.is_symlink():
                        raise AdoptionError("quarantine_restore_conflict")
                    _rename_bound_directory_between_exclusive(
                        destination_descriptor,
                        entry.version,
                        source_descriptor,
                        generated_handle,
                        failure="quarantine_restore_cas_mismatch",
                    )
                    os.fsync(destination_descriptor)
                    os.fsync(source_descriptor)
                if not quarantine_present or self._entry_for_path(
                    quarantined,
                    version=entry.version,
                    handle=entry.handle,
                ) != entry:
                    raise AdoptionError("quarantine_entry_drift")
                _rename_bound_directory_between_exclusive(
                    source_descriptor,
                    entry.handle,
                    destination_descriptor,
                    entry.version,
                    failure="quarantine_restore_cas_mismatch",
                    expected_identity_digest=entry.identity_digest,
                )
                if self._entry_for_path(
                    restored,
                    version=entry.version,
                    handle=entry.handle,
                ) != entry:
                    raise AdoptionError("quarantine_restore_failed")
                os.fsync(source_descriptor)
                os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
            os.close(source_descriptor)

    def _restore_quarantine(self, before: HostState) -> None:
        if not before.quarantine_entries:
            return
        if not self._registry_is_exact_predecessor():
            plugin_present, marketplace_present = self._cleanup_presence()
            if plugin_present:
                self._remove_plugin()
            if marketplace_present:
                self._remove_marketplace()
            self._install_from(_resolve_predecessor_source())
        self._quarantine_failed_candidate()
        self._restore_quarantine_entries(before)

    @staticmethod
    def _rollback_marker(
        before: HostState,
        *,
        previous_state: HostState | None = None,
        terminal_capture: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if terminal_capture is not None:
            binding = {
                "cache_source_digest": terminal_capture[
                    "cache_source_digest"
                ],
                "install_projection_digest": terminal_capture[
                    "install_projection_digest"
                ],
                "kind": "terminal_observation_private_snapshot",
                "marketplace_source_digest": terminal_capture[
                    "source_digest"
                ],
                "orphan_marker_content_digest": terminal_capture[
                    "orphan_marker_content_digest"
                ],
                "plugin_version": authority.TERMINAL_PLUGIN_VERSION,
                "source_state_digest": _canonical_digest(
                    before.projection()
                ),
            }
            binding_digest = str(before.marketplace_binding_digest)
        elif before == previous_state:
            binding: dict[str, object] = {
                "kind": "previous_committed",
                "plugin_version": authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
                "source_state_digest": _canonical_digest(before.projection()),
            }
            binding_digest = str(before.marketplace_binding_digest)
        else:
            binding = _predecessor_binding_descriptor()
            binding_digest = _predecessor_binding_digest()
        return {
            "before_state_digest": _canonical_digest(before.projection()),
            "binding": binding,
            "binding_digest": binding_digest,
            "schema": ROLLBACK_SCHEMA,
        }

    def _verify_rollback_marker(self, rollback: Path, before: HostState) -> None:
        try:
            _lstat_admitted_directory(rollback)
            expected_names = (
                frozenset({"cache", "marketplace", "predecessor.json"})
                if getattr(self, "_terminal_bridge_capture", None) is not None
                else frozenset({"predecessor.json"})
            )
            self._exact_directory_names(rollback, expected_names)
            actual = _private_file_bytes(rollback / "predecessor.json")
        except (OSError, AdoptionError) as exc:
            raise AdoptionError("rollback_source_drift") from exc
        if actual != _json_bytes(
            self._rollback_marker(
                before,
                previous_state=getattr(self, "_previous_state", None),
                terminal_capture=getattr(
                    self, "_terminal_bridge_capture", None
                ),
            )
        ):
            raise AdoptionError("rollback_source_drift")

    def capture_terminal_rollback(
        self,
        expected_before: HostState,
    ) -> dict[str, object]:
        terminal = getattr(self, "_terminal_bridge_state", None)
        if (
            terminal is None
            or expected_before.host != self.name
            or expected_before.plugin_version
            != authority.TERMINAL_PLUGIN_VERSION
            or expected_before.cache_digest != terminal.cache_digest
            or expected_before.marketplace_digest
            != terminal.marketplace_digest
        ):
            raise AdoptionError("terminal_rollback_source_unavailable")
        rollback = self._rollback_root()
        _lstat_admitted_directory(rollback, create=True)
        marketplace_source = self._terminal_stage_marketplace_root()
        cache_source = self._cache.with_name(
            authority.TERMINAL_PLUGIN_VERSION
        )
        marker_path = cache_source / distribution.ORPHANED_INSTALLED_MARKER
        marker_content: bytes | None = None
        observed_marker_digest = None
        if marker_path.exists() or marker_path.is_symlink():
            marker_content, marker_info = _terminal_regular_file_record(
                marker_path,
                maximum=64,
            )
            _validate_generated_orphan_marker(marker_info, marker_content)
            observed_marker_digest = _canonical_digest({
                "content_sha256": hashlib.sha256(marker_content).hexdigest(),
                "device": marker_info.st_dev,
                "group": marker_info.st_gid,
                "inode": marker_info.st_ino,
                "mode": stat.S_IMODE(marker_info.st_mode),
                "owner": marker_info.st_uid,
            })
        marketplace_digest = _bounded_private_tree_digest(marketplace_source)
        cache_source_digest = _bounded_private_tree_digest(cache_source)
        marketplace_install_projection = (
            _terminal_install_projection_digest(
                self._terminal_marketplace_bundle_root(
                    marketplace_source
                )
            )
        )
        cache_install_projection = _terminal_install_projection_digest(
            cache_source
        )
        cache_ignored = {".in_use"}
        if terminal.orphan_marker_digest is not None:
            cache_ignored.add(distribution.ORPHANED_INSTALLED_MARKER)
        if (
            marketplace_digest != terminal.marketplace_digest
            or observed_marker_digest != terminal.orphan_marker_digest
            or cache_source_digest is None
            or _bounded_private_tree_digest(
                cache_source, ignored=frozenset(cache_ignored)
            )
            != terminal.cache_digest
            or marketplace_install_projection != cache_install_projection
        ):
            raise AdoptionError("terminal_rollback_source_digest_mismatch")
        _copy_private_tree_exclusive(
            marketplace_source,
            rollback / "marketplace",
            expected_digest=marketplace_digest,
        )
        _copy_private_tree_exclusive(
            cache_source,
            rollback / "cache",
            expected_digest=cache_source_digest,
        )
        captured_marker = (
            rollback
            / "cache"
            / distribution.ORPHANED_INSTALLED_MARKER
        )
        if marker_content is None:
            if (
                marker_path.exists()
                or marker_path.is_symlink()
                or captured_marker.exists()
                or captured_marker.is_symlink()
            ):
                raise AdoptionError(
                    "terminal_rollback_source_digest_mismatch"
                )
        else:
            source_marker_content, source_marker_info = (
                _terminal_regular_file_record(marker_path, maximum=64)
            )
            captured_marker_content, captured_marker_info = (
                _terminal_regular_file_record(captured_marker, maximum=64)
            )
            _validate_generated_orphan_marker(
                source_marker_info,
                source_marker_content,
            )
            _validate_generated_orphan_marker(
                captured_marker_info,
                captured_marker_content,
            )
            source_marker_digest = _canonical_digest({
                "content_sha256": hashlib.sha256(
                    source_marker_content
                ).hexdigest(),
                "device": source_marker_info.st_dev,
                "group": source_marker_info.st_gid,
                "inode": source_marker_info.st_ino,
                "mode": stat.S_IMODE(source_marker_info.st_mode),
                "owner": source_marker_info.st_uid,
            })
            if (
                source_marker_content != marker_content
                or captured_marker_content != marker_content
                or source_marker_digest != observed_marker_digest
            ):
                raise AdoptionError(
                    "terminal_rollback_source_digest_mismatch"
                )
        capture = {
            "before_state_digest": _canonical_digest(
                expected_before.projection()
            ),
            "cache_digest": expected_before.cache_digest,
            "cache_source_digest": cache_source_digest,
            "host": self.name,
            "install_projection_digest": cache_install_projection,
            "marketplace_digest": expected_before.marketplace_digest,
            "orphan_marker_content_digest": (
                hashlib.sha256(marker_content).hexdigest()
                if marker_content is not None
                else None
            ),
            "source_digest": marketplace_digest,
            "source_version": authority.TERMINAL_PLUGIN_VERSION,
        }
        self._terminal_bridge_capture = capture
        marker = self._rollback_marker(
            expected_before,
            previous_state=self._previous_state,
            terminal_capture=capture,
        )
        marker_path = rollback / "predecessor.json"
        marker_bytes = _json_bytes(marker)
        if marker_path.exists() or marker_path.is_symlink():
            if _private_file_bytes(marker_path) != marker_bytes:
                raise AdoptionError("terminal_rollback_capture_no_clobber")
        else:
            _write_private_file_exclusive(
                marker_path,
                marker_bytes,
                failure_code="terminal_rollback_capture_no_clobber",
            )
        self.verify_terminal_rollback(expected_before, capture)
        return capture

    def verify_terminal_rollback(
        self,
        expected_before: HostState,
        capture: dict[str, object],
    ) -> None:
        terminal = getattr(self, "_terminal_bridge_state", None)
        if (
            terminal is None
            or type(capture) is not dict
            or set(capture) != _TERMINAL_BRIDGE_CAPTURE_KEYS
            or capture["host"] != self.name
            or capture["source_version"]
            != authority.TERMINAL_PLUGIN_VERSION
            or capture["before_state_digest"]
            != _canonical_digest(expected_before.projection())
            or capture["cache_digest"] != expected_before.cache_digest
            or capture["marketplace_digest"]
            != expected_before.marketplace_digest
            or capture["source_digest"]
            != expected_before.marketplace_digest
            or _safe_sha256(capture["cache_source_digest"]) is None
            or _safe_sha256(capture["install_projection_digest"]) is None
            or (
                capture["orphan_marker_content_digest"] is not None
                and _safe_sha256(
                    capture["orphan_marker_content_digest"]
                )
                is None
            )
            or (
                terminal.orphan_marker_digest is None
            )
            is not (
                capture["orphan_marker_content_digest"] is None
            )
        ):
            raise AdoptionError("terminal_rollback_capture_mismatch")
        self._terminal_bridge_capture = capture
        rollback = self._rollback_root()
        if (
            _bounded_private_tree_digest(rollback / "marketplace")
            != capture["source_digest"]
            or _bounded_private_tree_digest(rollback / "cache")
            != capture["cache_source_digest"]
            or _terminal_install_projection_digest(
                self._terminal_marketplace_bundle_root(
                    rollback / "marketplace"
                )
            )
            != capture["install_projection_digest"]
            or _terminal_install_projection_digest(
                rollback / "cache"
            )
            != capture["install_projection_digest"]
        ):
            raise AdoptionError("terminal_rollback_source_drift")
        self._verify_rollback_marker(rollback, expected_before)

    def _restore_terminal_orphan_marker(self) -> None:
        terminal = getattr(self, "_terminal_bridge_state", None)
        capture = getattr(self, "_terminal_bridge_capture", None)
        if terminal is None or capture is None:
            raise AdoptionError("terminal_rollback_source_unavailable")
        rollback_cache = self._rollback_root() / "cache"

        def require_bound_cache() -> None:
            if (
                _bounded_private_tree_digest(rollback_cache)
                != capture["cache_source_digest"]
            ):
                raise AdoptionError("terminal_rollback_marker_drift")

        require_bound_cache()
        rollback_marker = (
            rollback_cache
            / distribution.ORPHANED_INSTALLED_MARKER
        )
        installed_marker = (
            self._cache.with_name(authority.TERMINAL_PLUGIN_VERSION)
            / distribution.ORPHANED_INSTALLED_MARKER
        )
        if terminal.orphan_marker_digest is None:
            if (
                rollback_marker.exists()
                or rollback_marker.is_symlink()
                or installed_marker.exists()
                or installed_marker.is_symlink()
            ):
                raise AdoptionError("terminal_rollback_marker_drift")
            return
        if not rollback_marker.is_file() or rollback_marker.is_symlink():
            raise AdoptionError("terminal_rollback_marker_drift")
        marker_content, rollback_marker_info = _terminal_regular_file_record(
            rollback_marker,
            maximum=64,
        )
        require_bound_cache()
        if (
            not stat.S_ISREG(rollback_marker_info.st_mode)
            or rollback_marker_info.st_uid != os.getuid()
            or stat.S_IMODE(rollback_marker_info.st_mode) != 0o644
            or rollback_marker_info.st_nlink != 1
            or rollback_marker_info.st_size != 13
            or len(marker_content) != 13
            or not marker_content.isdigit()
            or hashlib.sha256(marker_content).hexdigest()
            != capture["orphan_marker_content_digest"]
        ):
            raise AdoptionError("terminal_rollback_marker_drift")
        _write_terminal_orphan_marker_exclusive(
            installed_marker,
            marker_content,
        )

    def _capture_rollback_marketplace(self, before: HostState) -> None:
        if (
            before.plugin_version == authority.TERMINAL_PLUGIN_VERSION
            and getattr(self, "_terminal_bridge_capture", None) is not None
        ):
            self.verify_terminal_rollback(
                before, self._terminal_bridge_capture
            )
            return
        rollback = self._rollback_root()
        previous_committed = before == getattr(self, "_previous_state", None)
        predecessor = (
            before.host != self.name
            or not before.marketplace_present
            or not before.plugin_present
            or not before.active
        )
        if predecessor:
            raise AdoptionError("rollback_source_unavailable")
        if previous_committed:
            previous_source = self._previous_stage_marketplace_root()
            self._verify_marketplace_root(
                previous_source,
                version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
                expected_bundle_digest=str(before.cache_digest),
                expected_marketplace_digest=str(before.marketplace_digest),
                verify_current_bundle=False,
            )
        else:
            if (
                before.marketplace_digest != _predecessor_marketplace_digest()
                or before.marketplace_binding_digest != _predecessor_binding_digest()
                or before.plugin_version != PREDECESSOR_VERSION
            ):
                raise AdoptionError("rollback_source_unavailable")
            _resolve_predecessor_source()
        current_cache = self._cache.with_name(before.plugin_version)
        observed = _tree_digest(current_cache, ignored=frozenset({".in_use"}))
        if observed != before.cache_digest:
            raise AdoptionError("before_state_cas_mismatch")
        if _tree_digest(self._marketplace_cache) is not None:
            raise AdoptionError("before_state_cas_mismatch")
        if rollback.exists() or rollback.is_symlink():
            self._verify_rollback_marker(rollback, before)
            return

        rollback_parent = rollback.parent
        _lstat_admitted_directory(rollback_parent, create=True)
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        parent_descriptor = os.open(rollback_parent, directory_flags)
        stage_descriptor = -1
        marker_descriptor = -1
        stage_name = f".{self.name}.{secrets.token_hex(16)}"
        stage_identity: tuple[int, int, int, int] | None = None
        marker_identity: tuple[int, int, int, int] | None = None
        published = False
        failure: Exception | None = None

        def identity(info: os.stat_result) -> tuple[int, int, int, int]:
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid)

        def require_directory(info: os.stat_result) -> None:
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise AdoptionError("rollback_capture_drift")

        def cleanup_stage() -> None:
            if stage_descriptor < 0 or stage_identity is None:
                return
            staged = os.stat(
                stage_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if identity(staged) != stage_identity:
                raise AdoptionError("rollback_capture_cleanup_failed")
            names = frozenset(os.listdir(stage_descriptor))
            if marker_identity is None:
                if names:
                    raise AdoptionError("rollback_capture_cleanup_failed")
            else:
                if names != frozenset({"predecessor.json"}):
                    raise AdoptionError("rollback_capture_cleanup_failed")
                marker_info = os.stat(
                    "predecessor.json",
                    dir_fd=stage_descriptor,
                    follow_symlinks=False,
                )
                if identity(marker_info) != marker_identity:
                    raise AdoptionError("rollback_capture_cleanup_failed")
                os.unlink("predecessor.json", dir_fd=stage_descriptor)
            if os.listdir(stage_descriptor):
                raise AdoptionError("rollback_capture_cleanup_failed")
            os.rmdir(stage_name, dir_fd=parent_descriptor)
            try:
                os.stat(
                    stage_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise AdoptionError("rollback_capture_cleanup_failed")
            os.fsync(parent_descriptor)

        try:
            require_directory(os.fstat(parent_descriptor))
            try:
                os.stat(self.name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise AdoptionError("rollback_capture_drift")
            os.mkdir(stage_name, mode=0o700, dir_fd=parent_descriptor)
            stage_descriptor = os.open(
                stage_name,
                directory_flags,
                dir_fd=parent_descriptor,
            )
            stage_info = os.fstat(stage_descriptor)
            require_directory(stage_info)
            stage_identity = identity(stage_info)

            marker_descriptor = os.open(
                "predecessor.json",
                os.O_CREAT
                | os.O_EXCL
                | os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=stage_descriptor,
            )
            marker_info = os.fstat(marker_descriptor)
            if (
                not stat.S_ISREG(marker_info.st_mode)
                or marker_info.st_uid != os.getuid()
                or stat.S_IMODE(marker_info.st_mode) != 0o600
                or marker_info.st_size != 0
            ):
                raise AdoptionError("rollback_capture_drift")
            marker_identity = identity(marker_info)
            content = _json_bytes(
                self._rollback_marker(
                    before,
                    previous_state=getattr(self, "_previous_state", None),
                )
            )
            view = memoryview(content)
            while view:
                written = os.write(marker_descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "rollback marker write failed")
                view = view[written:]
            os.fsync(marker_descriptor)
            after_write = os.fstat(marker_descriptor)
            if identity(after_write) != marker_identity or after_write.st_size != len(content):
                raise AdoptionError("rollback_capture_drift")
            os.lseek(marker_descriptor, 0, os.SEEK_SET)
            if os.read(marker_descriptor, len(content) + 1) != content:
                raise AdoptionError("rollback_capture_drift")
            os.close(marker_descriptor)
            marker_descriptor = -1

            os.fsync(stage_descriptor)
            staged = os.stat(
                stage_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if identity(staged) != stage_identity:
                raise AdoptionError("rollback_capture_drift")
            if frozenset(os.listdir(stage_descriptor)) != frozenset(
                {"predecessor.json"}
            ):
                raise AdoptionError("rollback_capture_drift")
            try:
                os.stat(self.name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise AdoptionError("rollback_capture_drift")
            _rename_directory_exclusive(
                parent_descriptor,
                stage_name,
                self.name,
            )
            published = True
            final_info = os.stat(
                self.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if identity(final_info) != stage_identity:
                raise AdoptionError("rollback_capture_drift")
            os.fsync(parent_descriptor)
        except Exception as exc:
            failure = exc
        finally:
            if marker_descriptor >= 0:
                os.close(marker_descriptor)
            if not published:
                try:
                    cleanup_stage()
                except Exception as cleanup_error:
                    failure = AdoptionError("rollback_capture_cleanup_failed")
                    failure.__cause__ = cleanup_error
            if stage_descriptor >= 0:
                os.close(stage_descriptor)
            os.close(parent_descriptor)
        if failure is not None:
            if isinstance(failure, AdoptionError):
                raise failure
            raise AdoptionError("rollback_capture_failed") from failure

    def apply(
        self,
        transaction_id: str,
        expected_before: HostState,
        expected_after: HostState,
    ) -> HostState:
        _safe_transaction_id(transaction_id)
        if self.observe() != expected_before:
            raise AdoptionError("before_state_cas_mismatch")
        self._verify_marketplace_root(
            self._stage_marketplace_root(),
            version=authority.PLUGIN_VERSION,
            expected_bundle_digest=str(expected_after.cache_digest),
            expected_marketplace_digest=str(expected_after.marketplace_digest),
            verify_current_bundle=True,
        )
        self._capture_rollback_marketplace(expected_before)
        self._quarantine_before(expected_before)
        if expected_before.plugin_present:
            self._remove_plugin()
        if expected_before.marketplace_present:
            self._remove_marketplace()
        self._quarantine_previous_active_cache(expected_before)
        self._install_from(self._stage_marketplace_root())
        return self.observe()

    def verify(self, transaction_id: str, expected_after: HostState) -> HostState:
        _safe_transaction_id(transaction_id)
        distribution.verify_installed_bundle(self._cache)
        observed = self.observe()
        if observed != expected_after:
            raise AdoptionError("after_state_mismatch")
        return observed

    def rollback(self, transaction_id: str, expected_before: HostState) -> HostState:
        _safe_transaction_id(transaction_id)
        rollback = self._rollback_root()
        try:
            current = self.observe()
        except AdoptionError:
            current = None
        terminal_capture = getattr(self, "_terminal_bridge_capture", None)
        terminal_rollback = (
            expected_before.plugin_version
            == authority.TERMINAL_PLUGIN_VERSION
            and terminal_capture is not None
        )
        if current == expected_before and not terminal_rollback:
            return expected_before
        if not rollback.is_dir() or rollback.is_symlink():
            raise AdoptionError("rollback_source_unavailable")
        try:
            _require_reversible_before_states(
                (expected_before,),
                admitted_previous=(expected_before,)
                if expected_before == getattr(self, "_previous_state", None)
                else None,
                admitted_terminal=(expected_before,)
                if terminal_rollback
                else None,
            )
        except AdoptionError as exc:
            raise AdoptionError("rollback_source_unavailable") from exc
        self._verify_rollback_marker(rollback, expected_before)
        if terminal_rollback:
            self.verify_terminal_rollback(
                expected_before, terminal_capture
            )
            if current == expected_before:
                # HostState intentionally excludes the terminal-only orphan
                # marker, so verify/restore that separately bound state before
                # accepting an otherwise exact idempotent rollback effect.
                self._restore_terminal_orphan_marker()
                return expected_before
            if current is not None and replace(
                current,
                quarantine_entries=expected_before.quarantine_entries,
            ) == expected_before:
                # The private .48 install effect is already exact and only
                # cache-quarantine reconciliation remains.  Do not repeat the
                # host CLI removal/install after a crash at that boundary.
                self._restore_terminal_orphan_marker()
                self._quarantine_failed_candidate()
                self._restore_quarantine_entries(expected_before)
                observed = self.observe()
                if observed != expected_before:
                    raise AdoptionError("rollback_verification_failed")
                return observed
            plugin_present, marketplace_present = self._cleanup_presence()
            if plugin_present:
                self._remove_plugin()
            if marketplace_present:
                self._remove_marketplace()
            self._install_from(rollback / "marketplace")
            self._restore_terminal_orphan_marker()
            self._quarantine_failed_candidate()
            self._restore_quarantine_entries(expected_before)
            observed = self.observe()
            if observed != expected_before:
                raise AdoptionError("rollback_verification_failed")
            return observed
        if expected_before == getattr(self, "_previous_state", None):
            plugin_present, marketplace_present = self._cleanup_presence()
            if plugin_present:
                self._remove_plugin()
            if marketplace_present:
                self._remove_marketplace()
            self._install_from(self._previous_stage_marketplace_root())
            self._quarantine_failed_candidate()
            observed = self.observe()
            if observed != expected_before:
                raise AdoptionError("rollback_verification_failed")
            return observed
        if expected_before.marketplace_binding_digest != _predecessor_binding_digest():
            raise AdoptionError("rollback_source_unavailable")
        if expected_before.quarantine_entries:
            self._restore_quarantine(expected_before)
            observed = self.observe()
            if observed != expected_before:
                raise AdoptionError("rollback_verification_failed")
            return observed
        predecessor = _resolve_predecessor_source()
        plugin_present, marketplace_present = self._cleanup_presence()
        if plugin_present:
            self._remove_plugin()
        if marketplace_present:
            self._remove_marketplace()
        self._install_from(predecessor)
        self._quarantine_failed_candidate()
        observed = self.observe()
        if observed != expected_before:
            raise AdoptionError("rollback_verification_failed")
        return observed


def _terminal_regular_file_record(
    path: Path,
    *,
    maximum: int = 16 * 1024 * 1024,
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise AdoptionError("terminal_observation_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o022
            or before.st_nlink != 1
            or before.st_size > maximum
        ):
            raise AdoptionError("terminal_observation_drift")
        chunks = bytearray()
        while chunk := os.read(descriptor, min(1024 * 1024, maximum + 1)):
            chunks.extend(chunk)
            if len(chunks) > maximum:
                raise AdoptionError("terminal_observation_drift")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = path.lstat()
    except OSError as exc:
        raise AdoptionError("terminal_observation_drift") from exc
    if (
        _archive_stat_identity(before) != _archive_stat_identity(after)
        or _archive_stat_identity(after)
        != _archive_stat_identity(path_after)
    ):
        raise AdoptionError("terminal_observation_drift")
    return bytes(chunks), after


def _terminal_regular_file_bytes(
    path: Path,
    *,
    maximum: int = 16 * 1024 * 1024,
) -> bytes:
    return _terminal_regular_file_record(path, maximum=maximum)[0]


def _write_terminal_orphan_marker_exclusive(
    path: Path,
    content: bytes,
) -> None:
    if len(content) != 13 or not content.isdigit():
        raise AdoptionError("terminal_rollback_marker_drift")
    if path.exists() or path.is_symlink():
        actual = _terminal_regular_file_bytes(path, maximum=64)
        _validate_generated_orphan_marker(path.lstat(), actual)
        if actual != content:
            raise AdoptionError("terminal_rollback_marker_drift")
        return
    parent_descriptor = descriptor = -1
    temporary = f".{path.name}.{secrets.token_hex(16)}"
    published = False
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        parent = os.fstat(parent_descriptor)
        path_parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o022
            or _archive_stat_identity(parent)[:5]
            != _archive_stat_identity(path_parent)[:5]
        ):
            raise AdoptionError("terminal_rollback_marker_drift")
        try:
            os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError("terminal_rollback_marker_drift")
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o644)
        os.fchown(descriptor, -1, os.getgid())
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "orphan marker write failed")
            offset += written
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        _validate_generated_orphan_marker(info, content)
        os.close(descriptor)
        descriptor = -1
        _rename_directory_exclusive(
            parent_descriptor,
            temporary,
            path.name,
        )
        published = True
        os.fsync(parent_descriptor)
        final = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        _validate_generated_orphan_marker(final, content)
        if (
            _archive_stat_identity(parent)[:5]
            != _archive_stat_identity(path.parent.lstat())[:5]
        ):
            raise AdoptionError("terminal_rollback_marker_drift")
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError("terminal_rollback_marker_drift") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            if not published:
                # A same-UID peer may have swapped the random leaf.  Retain any
                # ambiguous residue instead of unlinking an unbound inode.
                pass
            os.close(parent_descriptor)
    actual = _terminal_regular_file_bytes(path, maximum=64)
    _validate_generated_orphan_marker(path.lstat(), actual)
    if actual != content:
        raise AdoptionError("terminal_rollback_marker_drift")


def _terminal_directory_identity(path: Path) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AdoptionError("terminal_observation_unavailable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise AdoptionError("terminal_observation_drift")
    return _canonical_digest({
        "device": info.st_dev,
        "group": info.st_gid,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "owner": info.st_uid,
    })


def _terminal_registry_projection(
    row: dict[str, object] | None,
) -> tuple[bool, str | None, str]:
    if row is None:
        return False, None, "inactive"
    version = row.get("version")
    if _safe_plugin_version(version) is None:
        raise AdoptionError("terminal_host_registry_invalid")
    enabled = row.get("enabled")
    if type(enabled) is bool:
        return True, version, "active" if enabled else "inactive"
    state = row.get("state") or row.get("status")
    mapping = {
        "active": "active",
        "enabled": "active",
        "installed": "installed",
        "disabled": "inactive",
        "inactive": "inactive",
        "orphaned": "orphaned",
    }
    if state not in mapping:
        raise AdoptionError("terminal_host_registry_invalid")
    return True, version, mapping[state]


class FixedTerminalHostObserver:
    """Read only the fixed installed 0.1.48 cache and registry identities."""

    def __init__(
        self,
        name: str,
        transaction_root: Path,
        marketplace_transaction_root: Path,
    ):
        if name not in HOST_ORDER:
            raise AdoptionError("host_not_admitted")
        self.name = name
        self._transaction_root = transaction_root
        home = _fixed_user_home()
        if name == "codex":
            self._cli = _CODEX_CLI
            self._host_home = home / ".codex"
        else:
            self._cli = _CLAUDE_CLI
            self._host_home = home / ".claude"
        self._cache = (
            self._host_home
            / "plugins"
            / "cache"
            / authority.MARKETPLACE_ID
            / authority.PLUGIN_ID
            / authority.TERMINAL_PLUGIN_VERSION
        )
        self._marketplace_source_root = (
            marketplace_transaction_root / "stage" / name
        )

    def _run_json(self, args: tuple[str, ...]) -> object:
        identity = _admit_fixed_cli(self._cli, self._transaction_root)
        completed = _invoke_fixed_cli(
            self._cli,
            args,
            expected_final=identity[-1],
            transaction_root=self._transaction_root,
        )
        if (
            completed.returncode != 0
            or len(completed.stdout) > _MAX_COMMAND_OUTPUT
            or _capture_fixed_cli(self._cli) != identity
        ):
            raise AdoptionError("terminal_host_registry_unavailable")
        try:
            return json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdoptionError("terminal_host_registry_invalid") from exc

    def observe(self) -> TerminalHostState:
        _validate_fixed_host_chain(self._host_home, allow_missing=False)
        _validate_fixed_host_chain(self._cache, allow_missing=False)
        _lstat_admitted_directory(self._marketplace_source_root)
        plugin_row = _find_plugin_row(
            self._run_json(("plugin", "list", "--json"))
        )
        present, version, registry_state = _terminal_registry_projection(plugin_row)
        if not present or version != authority.TERMINAL_PLUGIN_VERSION:
            raise AdoptionError("terminal_plugin_identity_mismatch")
        marketplace_row = _find_marketplace_row(
            self._run_json(("plugin", "marketplace", "list", "--json"))
        )
        if marketplace_row is None:
            raise AdoptionError("terminal_marketplace_identity_mismatch")
        source = _marketplace_source(marketplace_row)
        if source not in {
            self._marketplace_source_root,
            self._marketplace_source_root
            / ".claude-plugin"
            / "marketplace.json",
            self._marketplace_source_root
            / ".agents"
            / "plugins"
            / "marketplace.json",
        }:
            raise AdoptionError("terminal_marketplace_identity_mismatch")
        marker = self._cache / distribution.ORPHANED_INSTALLED_MARKER
        marker_digest = None
        ignored = {".in_use"}
        if marker.exists() or marker.is_symlink():
            marker_content = _terminal_regular_file_bytes(marker, maximum=64)
            marker_info = marker.lstat()
            _validate_generated_orphan_marker(marker_info, marker_content)
            marker_digest = _canonical_digest({
                "content_sha256": hashlib.sha256(marker_content).hexdigest(),
                "device": marker_info.st_dev,
                "group": marker_info.st_gid,
                "inode": marker_info.st_ino,
                "mode": stat.S_IMODE(marker_info.st_mode),
                "owner": marker_info.st_uid,
            })
            ignored.add(distribution.ORPHANED_INSTALLED_MARKER)
        cache_digest = _tree_digest(self._cache, ignored=frozenset(ignored))
        marketplace_digest = _tree_digest(self._marketplace_source_root)
        if cache_digest is None or marketplace_digest is None:
            raise AdoptionError("terminal_observation_unavailable")
        try:
            manifest = json.loads(
                _terminal_regular_file_bytes(
                    self._cache / "SOURCE_MANIFEST.json"
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdoptionError("terminal_source_manifest_invalid") from exc
        if type(manifest) is not dict:
            raise AdoptionError("terminal_source_manifest_invalid")
        operational_adoption = (
            "orphaned" if marker_digest is not None else manifest.get("operational_adoption")
        )
        if operational_adoption not in _TERMINAL_OPERATIONAL_STATES:
            raise AdoptionError("terminal_source_manifest_invalid")
        return TerminalHostState(
            host=self.name,
            plugin_version=authority.TERMINAL_PLUGIN_VERSION,
            operational_adoption=operational_adoption,
            registry_state=registry_state,
            cache_digest=cache_digest,
            cache_identity_digest=_terminal_directory_identity(self._cache),
            marketplace_digest=marketplace_digest,
            marketplace_identity_digest=_terminal_directory_identity(
                self._marketplace_source_root
            ),
            orphan_marker_digest=marker_digest,
        )


class FixedCanonicalRecoveryObserver:
    """Read only one clean exact-head FP1 source and its local interpreter."""

    def __init__(self, source_root: Path):
        self._root = source_root

    def observe(self) -> CanonicalRecoveryState:
        if self._root.name != "hermes-fp1-state-schema-20260814":
            raise AdoptionError("canonical_recovery_anchor_unavailable")
        _terminal_directory_identity(self._root)
        source_revision = _git_text(self._root, ("rev-parse", "HEAD")).strip()
        if source_revision != authority.TERMINAL_SOURCE_REVISION:
            raise AdoptionError("canonical_recovery_source_revision_drift")
        tree_oid = _git_text(
            self._root,
            ("rev-parse", "HEAD^{tree}"),
        ).strip()
        if re.fullmatch(r"[0-9a-f]{40}", tree_oid) is None:
            raise AdoptionError("canonical_recovery_source_tree_drift")
        clean = not bool(
            _git_text(
                self._root,
                ("status", "--porcelain", "--untracked-files=normal"),
            )
        )
        bundle_bytes = _terminal_regular_file_bytes(
            self._root
            / "distribution"
            / authority.PLUGIN_ID
            / "SOURCE_MANIFEST.json"
        )
        interpreter = self._root / ".venv" / "bin" / "python"
        try:
            lexical_info = interpreter.lstat()
            resolved_interpreter = interpreter.resolve(strict=True)
            interpreter_info = resolved_interpreter.stat()
        except OSError as exc:
            raise AdoptionError(
                "canonical_recovery_interpreter_unavailable"
            ) from exc
        if (
            not (
                stat.S_ISLNK(lexical_info.st_mode)
                or stat.S_ISREG(lexical_info.st_mode)
            )
            or lexical_info.st_uid != os.getuid()
            or lexical_info.st_mode & 0o022
            or not stat.S_ISREG(interpreter_info.st_mode)
            or interpreter_info.st_uid != os.getuid()
            or interpreter_info.st_mode & 0o022
            or not interpreter_info.st_mode & 0o111
            or interpreter_info.st_size <= 0
        ):
            raise AdoptionError("canonical_recovery_interpreter_unavailable")
        try:
            interpreter_bytes = _terminal_regular_file_bytes(resolved_interpreter)
        except AdoptionError as exc:
            raise AdoptionError(
                "canonical_recovery_interpreter_unavailable"
            ) from exc
        lexical_link_digest = None
        if stat.S_ISLNK(lexical_info.st_mode):
            try:
                lexical_link_digest = hashlib.sha256(
                    os.fsencode(os.readlink(interpreter))
                ).hexdigest()
            except OSError as exc:
                raise AdoptionError(
                    "canonical_recovery_interpreter_unavailable"
                ) from exc
        return CanonicalRecoveryState(
            anchor=_TERMINAL_ANCHOR,
            source_revision=source_revision,
            source_bundle_digest=hashlib.sha256(bundle_bytes).hexdigest(),
            source_tree_digest=_canonical_digest({"git_tree_oid": tree_oid}),
            interpreter_digest=_canonical_digest({
                "content_sha256": hashlib.sha256(interpreter_bytes).hexdigest(),
                "device": interpreter_info.st_dev,
                "group": interpreter_info.st_gid,
                "inode": interpreter_info.st_ino,
                "lexical_link_digest": lexical_link_digest,
                "mode": stat.S_IMODE(interpreter_info.st_mode),
                "owner": interpreter_info.st_uid,
                "size": interpreter_info.st_size,
            }),
            clean=clean,
            interpreter_executable=True,
        )


def _source_revision() -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=_REPO_ROOT,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    revision = completed.stdout.decode("ascii", "strict").strip()
    if completed.returncode != 0 or len(revision) != 40 or any(
        character not in "0123456789abcdef" for character in revision
    ):
        raise AdoptionError("source_revision_unavailable")
    clean = subprocess.run(
        ("git", "status", "--porcelain", "--untracked-files=normal"),
        cwd=_REPO_ROOT,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    if clean.returncode != 0 or clean.stdout:
        raise AdoptionError("source_worktree_dirty")
    return revision


def _source_bundle_digest() -> str:
    distribution.verify_bundle(distribution.default_bundle_target())
    path = distribution.default_bundle_target() / "SOURCE_MANIFEST.json"
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o022
            or before.st_size > 16 * 1024 * 1024
        ):
            raise AdoptionError("source_bundle_unavailable")
        hasher = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            hasher.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise AdoptionError("source_bundle_unavailable")
    return hasher.hexdigest()


def _expected_after_state(
    host: str,
    installed_digest: str,
    marketplace_digest: str,
    source_revision: str,
    source_bundle_digest: str,
    before: HostState,
) -> HostState:
    return HostState(
        host=host,
        marketplace_present=True,
        marketplace_digest=marketplace_digest,
        marketplace_binding_digest=_candidate_binding_digest(
            source_revision=source_revision,
            source_bundle_digest=source_bundle_digest,
            marketplace_digest=marketplace_digest,
        ),
        plugin_present=True,
        plugin_version=authority.PLUGIN_VERSION,
        active=True,
        cache_digest=installed_digest,
        quarantine_entries=(),
        foreign_cache_leaf_count=before.foreign_cache_leaf_count,
        invalid_cache_leaf_count=0,
        ambiguous_cache_leaf_count=0,
    )


def _candidate_installed_digests(scratch_root: Path) -> tuple[str, str]:
    """Compute future plugin and marketplace digests in a private stage."""

    _require_ordinary_apply_plugin_version_alignment()
    _lstat_admitted_directory(scratch_root)
    with tempfile.TemporaryDirectory(
        prefix="candidate-digest-",
        dir=scratch_root,
    ) as temp:
        marketplace_root = Path(temp) / "marketplace"
        marketplace_root.mkdir(mode=0o700)
        target = (
            marketplace_root
            / "distribution"
            / authority.MARKETPLACE_ID
            / authority.PLUGIN_ID
            / authority.PLUGIN_VERSION
        )
        distribution.transactional_install(
            _REPO_ROOT / "skills" / "orch-next",
            target,
        )
        distribution.verify_installed_bundle(target)
        manifest = FixedHostAdapter._marketplace_manifest(authority.PLUGIN_VERSION)
        _atomic_private_write(
            marketplace_root / ".claude-plugin" / "marketplace.json",
            _json_bytes(manifest),
        )
        _atomic_private_write(
            marketplace_root / ".agents" / "plugins" / "marketplace.json",
            _json_bytes(manifest),
        )
        installed_digest = _tree_digest(target, ignored=frozenset({".in_use"}))
        marketplace_digest = _tree_digest(marketplace_root)
    if installed_digest is None or marketplace_digest is None:
        raise AdoptionError("prepared_cache_unavailable")
    return installed_digest, marketplace_digest


def _rollback_manifest(states: Sequence[HostState]) -> dict[str, object]:
    return {
        "host_order": list(HOST_ORDER),
        "policy": "exact_inverse.v1",
        "schema": ROLLBACK_SCHEMA,
        "states": [state.projection() for state in states],
    }


def _terminal_bridge_manifest_path(root: Path) -> Path:
    return root / _TERMINAL_BRIDGE_MANIFEST_LEAF


def _ordinary_prepared_path(root: Path) -> Path:
    return root / _ORDINARY_PREPARED_LEAF


def _ordinary_consumed_path(root: Path) -> Path:
    return root / _ORDINARY_CONSUMED_LEAF


def _ordinary_prepared_bytes(value: object) -> bytes:
    if type(value) is not dict or set(value) != _ORDINARY_PREPARED_KEYS:
        raise AdoptionError("ordinary_prepared_contract_mismatch")
    try:
        request_bytes = base64.b64decode(
            str(value["request_b64"]), validate=True
        )
        request_value = authority._base._parse_canonical_authority_payload(
            request_bytes
        )
        request = authority.validate_request(request_value, now=None)
    except Exception as exc:
        raise AdoptionError("ordinary_prepared_contract_mismatch") from exc
    before = tuple(
        HostState.from_projection(item, expected_host=host)
        for item, host in zip(value["before_states"], HOST_ORDER, strict=True)
    )
    after = tuple(
        HostState.from_projection(item, expected_host=host)
        for item, host in zip(value["after_states"], HOST_ORDER, strict=True)
    )
    actual = request["actual"]
    plan = request["plan"]
    if (
        value["schema"] != JOURNAL_SCHEMA
        or value["phase"] != "REQUEST_PREPARED"
        or value["transaction_id"] != actual["transaction_id"]
        or value["decision_id"] != actual["decision_id"]
        or value["request_digest"]
        != hashlib.sha256(request_bytes).hexdigest()
        or value["manifest_digest"] != plan["rollback_manifest_digest"]
        or _states_digest(before) != plan["before_state_digest"]
        or _states_digest(after) != plan["after_state_digest"]
    ):
        raise AdoptionError("ordinary_prepared_contract_mismatch")
    return _json_bytes(value)


def _ordinary_prepared_from_request(
    request: dict[str, object],
    *,
    before: Sequence[HostState],
    after: Sequence[HostState],
) -> dict[str, object]:
    request_bytes = authority.canonical_bytes(request)
    actual = request["actual"]
    plan = request["plan"]
    prepared = {
        "after_states": [state.projection() for state in after],
        "before_states": [state.projection() for state in before],
        "decision_id": actual["decision_id"],
        "manifest_digest": plan["rollback_manifest_digest"],
        "phase": "REQUEST_PREPARED",
        "request_b64": base64.b64encode(request_bytes).decode("ascii"),
        "request_digest": hashlib.sha256(request_bytes).hexdigest(),
        "schema": JOURNAL_SCHEMA,
        "transaction_id": actual["transaction_id"],
    }
    _ordinary_prepared_bytes(prepared)
    return prepared


def _read_ordinary_prepared(root: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _private_file_bytes(_ordinary_prepared_path(root)).decode("ascii")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("ordinary_prepared_unavailable") from exc
    _ordinary_prepared_bytes(value)
    return value


def _write_ordinary_prepared(
    root: Path,
    prepared: dict[str, object],
) -> None:
    content = _ordinary_prepared_bytes(prepared)
    path = _ordinary_prepared_path(root)
    if path.exists() or path.is_symlink():
        if _private_file_bytes(path) != content:
            raise AdoptionError("ordinary_prepared_collision")
        return
    _write_private_file_exclusive(
        path,
        content,
        failure_code="ordinary_prepared_collision",
    )


def _remove_ordinary_prepared(
    root: Path,
    prepared: dict[str, object],
) -> None:
    path = _ordinary_prepared_path(root)
    consumed = _ordinary_consumed_path(root)
    expected = _ordinary_prepared_bytes(prepared)
    if consumed.exists() or consumed.is_symlink():
        if path.exists() or path.is_symlink():
            raise AdoptionError("ordinary_prepared_drift")
        if _private_file_bytes(consumed) != expected:
            raise AdoptionError("ordinary_prepared_drift")
        return
    if _private_file_bytes(path) != expected:
        raise AdoptionError("ordinary_prepared_drift")
    directory = os.open(
        root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptor = -1
    renamed = False
    try:
        root_identity = _archive_stat_identity(os.fstat(directory))
        if root_identity != _archive_stat_identity(root.lstat()):
            raise AdoptionError("ordinary_prepared_drift")
        try:
            os.stat(
                _ORDINARY_CONSUMED_LEAF,
                dir_fd=directory,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise AdoptionError("ordinary_prepared_drift")
        descriptor = os.open(
            _ORDINARY_PREPARED_LEAF,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        identity = _archive_stat_identity(before)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size != len(expected)
            or identity
            != _archive_stat_identity(
                os.stat(
                    _ORDINARY_PREPARED_LEAF,
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            )
        ):
            raise AdoptionError("ordinary_prepared_drift")
        content = bytearray()
        while len(content) <= len(expected):
            chunk = os.read(
                descriptor,
                len(expected) + 1 - len(content),
            )
            if not chunk:
                break
            content.extend(chunk)
        if (
            bytes(content) != expected
            or identity != _archive_stat_identity(os.fstat(descriptor))
            or identity
            != _archive_stat_identity(
                os.stat(
                    _ORDINARY_PREPARED_LEAF,
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            )
        ):
            raise AdoptionError("ordinary_prepared_drift")
        _rename_directory_exclusive(
            directory,
            _ORDINARY_PREPARED_LEAF,
            _ORDINARY_CONSUMED_LEAF,
        )
        renamed = True
        moved = _archive_stat_identity(
            os.stat(
                _ORDINARY_CONSUMED_LEAF,
                dir_fd=directory,
                follow_symlinks=False,
            )
        )
        if (
            not _terminal_rename_identity_matches(identity, moved)
            or not _terminal_rename_identity_matches(
                identity,
                _archive_stat_identity(os.fstat(descriptor)),
            )
        ):
            try:
                _rename_directory_exclusive(
                    directory,
                    _ORDINARY_CONSUMED_LEAF,
                    _ORDINARY_PREPARED_LEAF,
                )
                renamed = False
                os.fsync(directory)
            except OSError:
                pass
            raise AdoptionError("ordinary_prepared_drift")
        os.fsync(directory)
        root_after = _archive_stat_identity(root.lstat())
        if root_identity[:6] != root_after[:6]:
            raise AdoptionError("ordinary_prepared_drift")
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError("ordinary_prepared_drift") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)
    if not renamed or _private_file_bytes(consumed) != expected:
        raise AdoptionError("ordinary_prepared_drift")


def _terminal_bridge_binding_digest(
    terminal_state: TerminalHostState,
    *,
    terminal_record: dict[str, object],
) -> str:
    return _canonical_digest({
        "host": terminal_state.host,
        "host_identity_digest": terminal_state.identity_digest(),
        "schema": TERMINAL_BRIDGE_SCHEMA,
        "terminal_canonical_identity_digest": terminal_record[
            "canonical_identity_digest"
        ],
        "terminal_current_identity_digest": terminal_record[
            "current_identity_digest"
        ],
        "terminal_envelope_digest": terminal_record["envelope_digest"],
        "terminal_request_digest": terminal_record["request_digest"],
    })


def _terminal_bridge_manifest_bytes(value: object) -> bytes:
    if type(value) is not dict or set(value) != _TERMINAL_BRIDGE_MANIFEST_KEYS:
        raise AdoptionError("terminal_bridge_manifest_mismatch")
    try:
        terminal_journal_bytes = base64.b64decode(
            str(value["terminal_journal_b64"]),
            validate=True,
        )
        terminal_journal_value = json.loads(
            terminal_journal_bytes.decode("ascii")
        )
        if type(terminal_journal_value) is not dict:
            raise ValueError
        canonical_terminal_journal = _terminal_journal_bytes(
            terminal_journal_value
        )
        terminal_verified = _reverify_terminal_journal(
            terminal_journal_value
        )
    except Exception as exc:
        raise AdoptionError("terminal_bridge_manifest_mismatch") from exc
    sources = value["sources"]
    states_value = value["states"]
    terminal_plan = terminal_verified.request["plan"]
    if (
        canonical_terminal_journal != terminal_journal_bytes
        or terminal_journal_value["phase"] != "COMMITTED"
        or terminal_journal_value["operation"]
        != authority.TERMINAL_OPERATION
        or hashlib.sha256(terminal_journal_bytes).hexdigest()
        != value["terminal_journal_digest"]
        or terminal_journal_value["transaction_id"]
        != value["terminal_transaction_id"]
        or terminal_journal_value["request_digest"]
        != value["terminal_request_digest"]
        or terminal_journal_value["envelope_digest"]
        != value["terminal_envelope_digest"]
        or terminal_journal_value["current_identity_digest"]
        != value["terminal_current_identity_digest"]
        or terminal_journal_value["canonical_identity_digest"]
        != value["terminal_canonical_identity_digest"]
        or terminal_plan["source_revision"]
        != value["terminal_source_revision"]
        or terminal_plan["source_bundle_digest"]
        != value["terminal_source_bundle_digest"]
        or value["schema"] != TERMINAL_BRIDGE_SCHEMA
        or value["policy"] != "terminal_observation_exact_inverse.v1"
        or value["host_order"] != list(HOST_ORDER)
        or value["ordinary_plugin_version"] != authority.PLUGIN_VERSION
        or value["terminal_plugin_version"]
        != authority.TERMINAL_PLUGIN_VERSION
        or value["terminal_source_revision"]
        != authority.TERMINAL_SOURCE_REVISION
        or _safe_transaction_id(value["terminal_transaction_id"])
        != value["terminal_transaction_id"]
        or any(
            _safe_sha256(value[key]) is None
            for key in (
                "terminal_canonical_identity_digest",
                "terminal_current_identity_digest",
                "terminal_envelope_digest",
                "terminal_journal_digest",
                "terminal_request_digest",
                "terminal_source_bundle_digest",
            )
        )
        or type(sources) is not list
        or len(sources) != len(HOST_ORDER)
        or type(states_value) is not list
        or len(states_value) != len(HOST_ORDER)
    ):
        raise AdoptionError("terminal_bridge_manifest_mismatch")
    states = tuple(
        HostState.from_projection(item, expected_host=host)
        for item, host in zip(states_value, HOST_ORDER, strict=True)
    )
    for state in states:
        if (
            state.plugin_version != authority.TERMINAL_PLUGIN_VERSION
            or not state.plugin_present
            or not state.marketplace_present
            or not state.active
            or state.invalid_cache_leaf_count != 0
            or state.ambiguous_cache_leaf_count != 0
        ):
            raise AdoptionError("terminal_bridge_manifest_mismatch")
    for source, host, state in zip(sources, HOST_ORDER, states, strict=True):
        if (
            type(source) is not dict
            or set(source) != _TERMINAL_BRIDGE_CAPTURE_KEYS
            or source["host"] != host
            or source["source_version"] != authority.TERMINAL_PLUGIN_VERSION
            or source["cache_digest"] != state.cache_digest
            or _safe_sha256(source["cache_source_digest"]) is None
            or _safe_sha256(source["install_projection_digest"]) is None
            or (
                source["orphan_marker_content_digest"] is not None
                and _safe_sha256(
                    source["orphan_marker_content_digest"]
                )
                is None
            )
            or source["marketplace_digest"] != state.marketplace_digest
            or source["source_digest"] != state.marketplace_digest
            or source["before_state_digest"]
            != _canonical_digest(state.projection())
        ):
            raise AdoptionError("terminal_bridge_manifest_mismatch")
    return _json_bytes(value)


def _read_terminal_bridge_manifest(root: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _private_file_bytes(
                _terminal_bridge_manifest_path(root)
            ).decode("ascii")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("terminal_bridge_manifest_unavailable") from exc
    _terminal_bridge_manifest_bytes(value)
    return value


def _write_terminal_bridge_manifest(
    root: Path,
    manifest: dict[str, object],
) -> None:
    content = _terminal_bridge_manifest_bytes(manifest)
    path = _terminal_bridge_manifest_path(root)
    if path.exists() or path.is_symlink():
        try:
            if _private_file_bytes(path) != content:
                raise AdoptionError("terminal_bridge_manifest_collision")
        except OSError as exc:
            raise AdoptionError("terminal_bridge_manifest_collision") from exc
        return
    _write_private_file_exclusive(
        path,
        content,
        failure_code="terminal_bridge_manifest_collision",
    )


def _require_reversible_before_states(
    states: Sequence[HostState],
    *,
    admitted_previous: Sequence[HostState] | None = None,
    admitted_terminal: Sequence[HostState] | None = None,
) -> None:
    """Admit exact Codex predecessor and one fixed Claude residual predecessor."""

    if admitted_previous is not None and tuple(states) == tuple(admitted_previous):
        if any(
            state.plugin_version != authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
            or not state.plugin_present
            or not state.marketplace_present
            or not state.active
            or state.invalid_cache_leaf_count != 0
            or state.ambiguous_cache_leaf_count != 0
            for state in states
        ):
            raise AdoptionError("before_state_not_exactly_reversible")
        return

    if admitted_terminal is not None and tuple(states) == tuple(admitted_terminal):
        if any(
            state.plugin_version != authority.TERMINAL_PLUGIN_VERSION
            or not state.plugin_present
            or not state.marketplace_present
            or not state.active
            or state.invalid_cache_leaf_count != 0
            or state.ambiguous_cache_leaf_count != 0
            for state in states
        ):
            raise AdoptionError("before_state_not_exactly_reversible")
        return

    for state in states:
        common = (
            state.marketplace_present
            and state.marketplace_digest == _predecessor_marketplace_digest()
            and state.marketplace_binding_digest == _predecessor_binding_digest()
            and state.plugin_present
            and state.plugin_version == PREDECESSOR_VERSION
            and state.active
            and state.invalid_cache_leaf_count == 0
            and state.ambiguous_cache_leaf_count == 0
        )
        codex_predecessor = (
            state.host == "codex"
            and state.cache_digest == PREDECESSOR_BUNDLE_DIGEST
            and state.quarantine_entries == ()
        )
        claude_entries = state.quarantine_entries
        claude_predecessor = (
            state.host == "claude"
            and state.cache_digest == CLAUDE_RESIDUAL_PREDECESSOR_DIGEST
            and len(claude_entries) == 2
            and tuple(entry.version for entry in claude_entries)
            == (PREDECESSOR_VERSION, CLAUDE_RESIDUE_VERSION)
            and claude_entries[0].cache_digest
            == CLAUDE_RESIDUAL_PREDECESSOR_DIGEST
            and claude_entries[0].in_use_present
            and claude_entries[1].cache_digest == CLAUDE_RESIDUE_OPAQUE_DIGEST
            and not claude_entries[1].in_use_present
        )
        if not common or not (codex_predecessor or claude_predecessor):
            raise AdoptionError("before_state_not_exactly_reversible")


def _record_from_verified(
    verified: authority.VerifiedPluginAdoptionEnvelope,
    *,
    before: Sequence[HostState],
    after: Sequence[HostState],
) -> dict[str, object]:
    request = verified.request
    actual = request["actual"]
    plan = request["plan"]
    return {
        "schema": JOURNAL_SCHEMA,
        "transaction_id": actual["transaction_id"],
        "decision_id": actual["decision_id"],
        "phase": "AUTHORIZED",
        "plan_digest": plan["plan_digest"],
        "before_state_digest": plan["before_state_digest"],
        "after_state_digest": plan["after_state_digest"],
        "rollback_manifest_digest": plan["rollback_manifest_digest"],
        "request_digest": verified.request_digest,
        "envelope_digest": verified.envelope_digest,
        "request_b64": base64.b64encode(verified.request_bytes).decode("ascii"),
        "envelope_b64": base64.b64encode(verified.envelope_bytes).decode("ascii"),
        "before_states": [state.projection() for state in before],
        "after_states": [state.projection() for state in after],
    }


def _terminal_prepared_from_request(
    request: dict[str, object],
    *,
    states: Sequence[TerminalHostState],
    recovery: CanonicalRecoveryState,
) -> dict[str, object]:
    actual = request["actual"]
    plan = request["plan"]
    if type(actual) is not dict or type(plan) is not dict:
        raise AdoptionError("terminal_prepared_contract_mismatch")
    projections = [state.projection() for state in states]
    request_bytes = authority.canonical_bytes(request)
    record = {
        "schema": JOURNAL_SCHEMA,
        "operation": authority.TERMINAL_OPERATION,
        "transaction_id": actual["transaction_id"],
        "decision_id": actual["decision_id"],
        "phase": "REQUEST_PREPARED",
        "plan_digest": plan["plan_digest"],
        "before_state_digest": plan["before_state_digest"],
        "after_state_digest": plan["after_state_digest"],
        "rollback_manifest_digest": plan["rollback_manifest_digest"],
        "request_digest": hashlib.sha256(request_bytes).hexdigest(),
        "request_b64": base64.b64encode(request_bytes).decode("ascii"),
        "before_states": projections,
        "after_states": projections,
        "predecessor_identity_digest": plan["predecessor_identity_digest"],
        "current_identity_digest": plan["current_identity_digest"],
        "canonical_identity_digest": plan["canonical_identity_digest"],
        "canonical_recovery": recovery.projection(),
        "host_mutation_count": 0,
    }
    _terminal_prepared_bytes(record)
    return record


def _terminal_record_from_prepared(
    verified: authority.VerifiedPluginAdoptionEnvelope,
    prepared: dict[str, object],
) -> dict[str, object]:
    _terminal_prepared_bytes(prepared)
    request_bytes = base64.b64decode(
        str(prepared["request_b64"]),
        validate=True,
    )
    if (
        verified.request_bytes != request_bytes
        or verified.request_digest != prepared["request_digest"]
        or verified.request != authority._base._parse_canonical_authority_payload(
            request_bytes
        )
    ):
        raise AdoptionError("terminal_authority_replay_mismatch")
    record = {
        **prepared,
        "phase": "COMMITTED",
        "envelope_digest": verified.envelope_digest,
        "envelope_b64": base64.b64encode(verified.envelope_bytes).decode("ascii"),
    }
    _terminal_journal_bytes(record)
    return record


def _reverify_terminal_journal(
    record: dict[str, object],
) -> authority.VerifiedPluginAdoptionEnvelope:
    _terminal_journal_bytes(record)
    try:
        request_bytes = base64.b64decode(str(record["request_b64"]), validate=True)
        envelope_bytes = base64.b64decode(str(record["envelope_b64"]), validate=True)
    except Exception as exc:
        raise AdoptionError("terminal_journal_authority_bytes_invalid") from exc
    request = authority._base._parse_canonical_authority_payload(request_bytes)
    if type(request) is not dict or type(request.get("actual")) is not dict:
        raise AdoptionError("terminal_journal_authority_bytes_invalid")
    verification_time = float(request["actual"].get("issued_at", 0.0)) + 0.001
    try:
        verified = authority.verify_plugin_adoption_terminal_envelope(
            request_bytes=request_bytes,
            envelope_bytes=envelope_bytes,
            now=verification_time,
        )
    except authority.PluginAdoptionAuthorityError as exc:
        raise AdoptionError("terminal_journal_authority_verification_failed") from exc
    plan = verified.request["plan"]
    states = [
        TerminalHostState.from_projection(value, expected_host=host)
        for value, host in zip(record["before_states"], HOST_ORDER, strict=True)
    ]
    recovery = CanonicalRecoveryState.from_projection(record["canonical_recovery"])
    rollback_manifest_digest = _canonical_digest({
        "host_order": list(HOST_ORDER),
        "policy": "observation_only_no_host_mutation.v1",
        "states": [state.projection() for state in states],
    })
    predecessor_identity_digest = _canonical_digest({
        "host_state_digests": [state.identity_digest() for state in states],
        "operation": authority.TERMINAL_OPERATION,
        "plugin_id": authority.PLUGIN_ID,
        "plugin_version": authority.TERMINAL_PLUGIN_VERSION,
    })
    if (
        not verified.allowed
        or verified.request_digest != record["request_digest"]
        or verified.envelope_digest != record["envelope_digest"]
        or plan["plan_digest"] != record["plan_digest"]
        or plan["predecessor_identity_digest"]
        != record["predecessor_identity_digest"]
        or plan["predecessor_identity_digest"]
        != predecessor_identity_digest
        or plan["current_identity_digest"] != record["current_identity_digest"]
        or plan["canonical_identity_digest"]
        != record["canonical_identity_digest"]
        or plan["codex_current_state_digest"] != states[0].identity_digest()
        or plan["claude_current_state_digest"] != states[1].identity_digest()
        or plan["before_state_digest"] != record["before_state_digest"]
        or plan["after_state_digest"] != record["after_state_digest"]
        or plan["rollback_manifest_digest"] != rollback_manifest_digest
        or plan["rollback_manifest_digest"]
        != record["rollback_manifest_digest"]
        or plan["source_bundle_digest"] != recovery.source_bundle_digest
        or recovery.identity_digest() != record["canonical_identity_digest"]
    ):
        raise AdoptionError("terminal_journal_authority_verification_failed")
    return verified


def _reverify_journal(record: dict[str, object]) -> authority.VerifiedPluginAdoptionEnvelope:
    try:
        request_bytes = base64.b64decode(str(record["request_b64"]), validate=True)
        envelope_bytes = base64.b64decode(str(record["envelope_b64"]), validate=True)
    except Exception as exc:
        raise AdoptionError("journal_authority_bytes_invalid") from exc
    # A consumed signed result remains the durable authority for crash recovery
    # after its request TTL.  Re-evaluate at issuance after checking the signed
    # request itself carried a bounded interval.
    request = authority._base._parse_canonical_authority_payload(request_bytes)
    if type(request) is not dict or type(request.get("actual")) is not dict:
        raise AdoptionError("journal_authority_bytes_invalid")
    verification_time = float(request["actual"].get("issued_at", 0.0)) + 0.001
    try:
        verified = authority.verify_plugin_adoption_envelope(
            request_bytes=request_bytes,
            envelope_bytes=envelope_bytes,
            now=verification_time,
        )
    except authority.PluginAdoptionAuthorityError as exc:
        if record.get("phase") not in {"ROLLED_BACK", "COMMITTED"}:
            raise AdoptionError("journal_authority_verification_failed") from exc
        try:
            verified = authority.verify_previous_terminal_plugin_adoption_envelope(
                request_bytes=request_bytes,
                envelope_bytes=envelope_bytes,
                now=verification_time,
            )
        except authority.PluginAdoptionAuthorityError as previous_exc:
            raise AdoptionError(
                "journal_authority_verification_failed"
            ) from previous_exc
    if (
        not verified.allowed
        or verified.request_digest != record["request_digest"]
        or verified.envelope_digest != record["envelope_digest"]
        or verified.request["plan"]["plan_digest"] != record["plan_digest"]
    ):
        raise AdoptionError("journal_authority_verification_failed")
    return verified


@contextmanager
def _host_locks(root: Path, *, typed_failure: bool = False) -> Iterator[None]:
    """Acquire the canonical host lock order: Codex, then Claude."""

    lock_root = _lock_root(root)
    _lstat_admitted_directory(lock_root, create=True)
    parent_descriptor = os.open(
        lock_root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    parent_identity = _archive_stat_identity(os.fstat(parent_descriptor))
    descriptors: list[tuple[int, str, tuple[int, ...]]] = []
    try:
        for host in HOST_ORDER:
            leaf = f"{host}.lock"
            descriptor = os.open(
                leaf,
                os.O_CREAT
                | os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            info = os.fstat(descriptor)
            parent_identity = _archive_stat_identity(
                os.fstat(parent_descriptor)
            )
            identity = _archive_stat_identity(info)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or identity
                != _archive_stat_identity(
                    os.stat(
                        leaf,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                )
            ):
                os.close(descriptor)
                raise AdoptionError("host_lock_drift")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as exc:
                os.close(descriptor)
                if not typed_failure:
                    raise
                raise AdoptionError("host_lock_unavailable") from exc
            if (
                identity != _archive_stat_identity(os.fstat(descriptor))
                or identity
                != _archive_stat_identity(
                    os.stat(
                        leaf,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                )
            ):
                os.close(descriptor)
                raise AdoptionError("host_lock_drift")
            descriptors.append((descriptor, leaf, identity))
        parent_identity = _archive_stat_identity(os.fstat(parent_descriptor))
        if parent_identity != _archive_stat_identity(lock_root.lstat()):
            raise AdoptionError("host_lock_drift")
        yield
    finally:
        drift = (
            parent_identity
            != _archive_stat_identity(os.fstat(parent_descriptor))
            or parent_identity
            != _archive_stat_identity(lock_root.lstat())
        )
        for descriptor, leaf, identity in reversed(descriptors):
            try:
                try:
                    drift = drift or (
                        identity
                        != _archive_stat_identity(os.fstat(descriptor))
                        or identity
                        != _archive_stat_identity(
                            os.stat(
                                leaf,
                                dir_fd=parent_descriptor,
                                follow_symlinks=False,
                            )
                        )
                    )
                except OSError:
                    drift = True
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        os.close(parent_descriptor)
        if drift:
            raise AdoptionError("host_lock_drift")


class PluginAdoptionExecutor:
    def __init__(
        self,
        *,
        state_root: Path,
        adapters: Sequence[AdoptionHostAdapter],
        authority_request: Callable[..., authority.VerifiedPluginAdoptionEnvelope],
        clock: Callable[[], float] = time.time,
        crash_hook: Callable[[str], None] = lambda _phase: None,
        archive_rolled_back: bool = False,
        admitted_previous_states: Sequence[HostState] | None = None,
        action: str = "apply",
        terminal_observers: Sequence[TerminalHostObserver] | None = None,
        canonical_recovery_observer: CanonicalRecoveryObserver | None = None,
        terminal_authority_request: Callable[..., authority.VerifiedPluginAdoptionEnvelope]
        | None = None,
        terminal_bridge_root: Path | None = None,
    ):
        if action not in {"apply", "terminalize"}:
            raise AdoptionError("plugin_adoption_action_invalid")
        adapter_names = tuple(adapter.name for adapter in adapters)
        if (
            action == "apply" and adapter_names != HOST_ORDER
        ) or (
            action == "terminalize"
            and adapter_names not in {(), HOST_ORDER}
        ):
            raise AdoptionError("host_order_mismatch")
        if action == "terminalize" and (
            terminal_observers is None
            or tuple(observer.name for observer in terminal_observers) != HOST_ORDER
            or canonical_recovery_observer is None
            or terminal_authority_request is None
        ):
            raise AdoptionError("terminal_observer_unavailable")
        if terminal_bridge_root is not None and (
            action != "apply"
            or terminal_observers is None
            or tuple(observer.name for observer in terminal_observers) != HOST_ORDER
            or canonical_recovery_observer is None
        ):
            raise AdoptionError("terminal_bridge_observer_unavailable")
        self.root = state_root
        self.adapters = tuple(adapters)
        self.authority_request = authority_request
        self.clock = clock
        self.crash_hook = crash_hook
        self.archive_rolled_back = archive_rolled_back
        self.admitted_previous_states = (
            tuple(admitted_previous_states)
            if admitted_previous_states is not None
            else None
        )
        self.action = action
        self.terminal_observers = (
            tuple(terminal_observers) if terminal_observers is not None else ()
        )
        self.canonical_recovery_observer = canonical_recovery_observer
        self.terminal_authority_request = terminal_authority_request
        self.terminal_bridge_root = terminal_bridge_root
        self._bridge_recovery_required = False

    def _observe_terminal_states(self) -> list[TerminalHostState]:
        states = [observer.observe() for observer in self.terminal_observers]
        _terminal_states_digest(states)
        return states

    def _observe_canonical_recovery(self) -> CanonicalRecoveryState:
        if self.canonical_recovery_observer is None:
            raise AdoptionError("terminal_observer_unavailable")
        return _admit_canonical_recovery(
            self.canonical_recovery_observer.observe()
        )

    def _load_terminal_bridge_record(
        self,
    ) -> tuple[
        dict[str, object],
        authority.VerifiedPluginAdoptionEnvelope,
        tuple[TerminalHostState, ...],
        CanonicalRecoveryState,
    ]:
        if self.terminal_bridge_root is None:
            raise AdoptionError("terminal_bridge_unavailable")
        record = _read_terminal_journal(self.terminal_bridge_root)
        verified = _reverify_terminal_journal(record)
        states = tuple(
            TerminalHostState.from_projection(value, expected_host=host)
            for value, host in zip(
                record["before_states"], HOST_ORDER, strict=True
            )
        )
        recovery = CanonicalRecoveryState.from_projection(
            record["canonical_recovery"]
        )
        plan = verified.request["plan"]
        if (
            record["phase"] != "COMMITTED"
            or record["operation"] != authority.TERMINAL_OPERATION
            or plan["plugin_version"] != authority.TERMINAL_PLUGIN_VERSION
            or plan["source_revision"] != authority.TERMINAL_SOURCE_REVISION
            or recovery.source_revision != authority.TERMINAL_SOURCE_REVISION
            or plan["source_bundle_digest"] != recovery.source_bundle_digest
        ):
            raise AdoptionError("terminal_bridge_contract_mismatch")
        if self._observe_terminal_states() != list(states):
            raise AdoptionError("terminal_current_state_drift")
        if self._observe_canonical_recovery() != recovery:
            raise AdoptionError("canonical_recovery_drift")
        return record, verified, states, recovery

    def _configure_terminal_bridge_adapters(
        self,
        terminal_record: dict[str, object],
        terminal_states: Sequence[TerminalHostState],
        *,
        observe_before: bool = True,
    ) -> list[HostState]:
        for adapter, state in zip(
            self.adapters, terminal_states, strict=True
        ):
            configure = getattr(adapter, "configure_terminal_bridge", None)
            if not callable(configure):
                raise AdoptionError("terminal_bridge_adapter_unavailable")
            configure(
                state,
                _terminal_bridge_binding_digest(
                    state, terminal_record=terminal_record
                ),
            )
        if not observe_before:
            return []
        before = [adapter.observe() for adapter in self.adapters]
        _require_reversible_before_states(
            before,
            admitted_terminal=before,
        )
        return before

    def _fresh_terminal_bridge_manifest(
        self,
    ) -> tuple[list[HostState], dict[str, object]]:
        record, verified, terminal_states, recovery = (
            self._load_terminal_bridge_record()
        )
        before = self._configure_terminal_bridge_adapters(
            record, terminal_states
        )
        captures: list[dict[str, object]] = []
        for adapter, state in zip(self.adapters, before, strict=True):
            capture = getattr(adapter, "capture_terminal_rollback", None)
            if not callable(capture):
                raise AdoptionError("terminal_bridge_adapter_unavailable")
            value = capture(state)
            if type(value) is not dict:
                raise AdoptionError("terminal_rollback_capture_mismatch")
            captures.append(value)
        if self._observe_terminal_states() != list(terminal_states):
            raise AdoptionError("terminal_current_state_drift")
        if self._observe_canonical_recovery() != recovery:
            raise AdoptionError("canonical_recovery_drift")
        if [adapter.observe() for adapter in self.adapters] != before:
            raise AdoptionError("before_state_cas_mismatch")
        terminal_plan = verified.request["plan"]
        manifest = {
            "host_order": list(HOST_ORDER),
            "ordinary_plugin_version": authority.PLUGIN_VERSION,
            "policy": "terminal_observation_exact_inverse.v1",
            "schema": TERMINAL_BRIDGE_SCHEMA,
            "sources": captures,
            "states": [state.projection() for state in before],
            "terminal_canonical_identity_digest": record[
                "canonical_identity_digest"
            ],
            "terminal_current_identity_digest": record[
                "current_identity_digest"
            ],
            "terminal_envelope_digest": record["envelope_digest"],
            "terminal_journal_b64": base64.b64encode(
                _terminal_journal_bytes(record)
            ).decode("ascii"),
            "terminal_journal_digest": hashlib.sha256(
                _terminal_journal_bytes(record)
            ).hexdigest(),
            "terminal_plugin_version": authority.TERMINAL_PLUGIN_VERSION,
            "terminal_request_digest": record["request_digest"],
            "terminal_source_bundle_digest": terminal_plan[
                "source_bundle_digest"
            ],
            "terminal_source_revision": terminal_plan["source_revision"],
            "terminal_transaction_id": record["transaction_id"],
        }
        _terminal_bridge_manifest_bytes(manifest)
        _write_terminal_bridge_manifest(self.root, manifest)
        return before, manifest

    def _resume_private_terminal_bridge_manifest(
        self,
    ) -> tuple[list[HostState], dict[str, object]]:
        """Load only the durable private bridge closure after request prepare."""

        manifest = _read_terminal_bridge_manifest(self.root)
        try:
            terminal_record = json.loads(
                base64.b64decode(
                    str(manifest["terminal_journal_b64"]),
                    validate=True,
                ).decode("ascii")
            )
        except Exception as exc:
            raise AdoptionError("terminal_bridge_binding_mismatch") from exc
        terminal_verified = _reverify_terminal_journal(terminal_record)
        terminal_states = tuple(
            TerminalHostState.from_projection(value, expected_host=host)
            for value, host in zip(
                terminal_record["before_states"], HOST_ORDER, strict=True
            )
        )
        recovery = CanonicalRecoveryState.from_projection(
            terminal_record["canonical_recovery"]
        )
        terminal_plan = terminal_verified.request["plan"]
        if (
            terminal_record["phase"] != "COMMITTED"
            or terminal_record["operation"]
            != authority.TERMINAL_OPERATION
            or terminal_plan["plugin_version"]
            != authority.TERMINAL_PLUGIN_VERSION
            or terminal_plan["source_revision"]
            != authority.TERMINAL_SOURCE_REVISION
            or recovery.source_revision
            != authority.TERMINAL_SOURCE_REVISION
            or terminal_plan["source_bundle_digest"]
            != recovery.source_bundle_digest
            or manifest["terminal_journal_digest"]
            != hashlib.sha256(
                _terminal_journal_bytes(terminal_record)
            ).hexdigest()
            or manifest["terminal_request_digest"]
            != terminal_record["request_digest"]
            or manifest["terminal_envelope_digest"]
            != terminal_record["envelope_digest"]
            or manifest["terminal_current_identity_digest"]
            != terminal_record["current_identity_digest"]
            or manifest["terminal_canonical_identity_digest"]
            != terminal_record["canonical_identity_digest"]
            or manifest["terminal_source_revision"]
            != terminal_plan["source_revision"]
            or manifest["terminal_source_bundle_digest"]
            != terminal_plan["source_bundle_digest"]
            or manifest["terminal_transaction_id"]
            != terminal_record["transaction_id"]
        ):
            raise AdoptionError("terminal_bridge_binding_mismatch")
        self._configure_terminal_bridge_adapters(
            terminal_record,
            terminal_states,
            observe_before=False,
        )
        before = [
            HostState.from_projection(value, expected_host=host)
            for value, host in zip(
                manifest["states"], HOST_ORDER, strict=True
            )
        ]
        _require_reversible_before_states(
            before,
            admitted_terminal=before,
        )
        for adapter, state, capture in zip(
            self.adapters,
            before,
            manifest["sources"],
            strict=True,
        ):
            verify_capture = getattr(
                adapter, "verify_terminal_rollback", None
            )
            if not callable(verify_capture):
                raise AdoptionError("terminal_bridge_adapter_unavailable")
            verify_capture(state, capture)
        return before, manifest

    def _reverify_terminal_bridge_manifest(
        self,
        record: dict[str, object],
        verified: authority.VerifiedPluginAdoptionEnvelope,
    ) -> dict[str, object]:
        if self.terminal_bridge_root is None:
            raise AdoptionError("terminal_bridge_unavailable")
        self._bridge_recovery_required = False
        manifest = _read_terminal_bridge_manifest(self.root)
        manifest_bytes = _terminal_bridge_manifest_bytes(manifest)
        try:
            terminal_record = json.loads(
                base64.b64decode(
                    str(manifest["terminal_journal_b64"]),
                    validate=True,
                ).decode("ascii")
            )
        except Exception as exc:
            raise AdoptionError("terminal_bridge_binding_mismatch") from exc
        terminal_verified = _reverify_terminal_journal(terminal_record)
        terminal_states = tuple(
            TerminalHostState.from_projection(value, expected_host=host)
            for value, host in zip(
                terminal_record["before_states"], HOST_ORDER, strict=True
            )
        )
        recovery = CanonicalRecoveryState.from_projection(
            terminal_record["canonical_recovery"]
        )
        terminal_plan = terminal_verified.request["plan"]
        if (
            manifest["terminal_journal_digest"]
            != hashlib.sha256(
                _terminal_journal_bytes(terminal_record)
            ).hexdigest()
            or manifest["terminal_request_digest"]
            != terminal_record["request_digest"]
            or manifest["terminal_envelope_digest"]
            != terminal_record["envelope_digest"]
            or manifest["terminal_current_identity_digest"]
            != terminal_record["current_identity_digest"]
            or manifest["terminal_canonical_identity_digest"]
            != terminal_record["canonical_identity_digest"]
            or manifest["terminal_source_revision"]
            != terminal_plan["source_revision"]
            or manifest["terminal_source_bundle_digest"]
            != terminal_plan["source_bundle_digest"]
            or manifest["terminal_transaction_id"]
            != terminal_record["transaction_id"]
            or verified.request["plan"]["rollback_manifest_digest"]
            != hashlib.sha256(manifest_bytes).hexdigest()
            or record["rollback_manifest_digest"]
            != verified.request["plan"]["rollback_manifest_digest"]
            or manifest["states"] != record["before_states"]
        ):
            raise AdoptionError("terminal_bridge_binding_mismatch")
        self._configure_terminal_bridge_adapters(
            terminal_record,
            terminal_states,
            observe_before=False,
        )
        before = [
            HostState.from_projection(value, expected_host=host)
            for value, host in zip(
                record["before_states"], HOST_ORDER, strict=True
            )
        ]
        after = [
            HostState.from_projection(value, expected_host=host)
            for value, host in zip(
                record["after_states"], HOST_ORDER, strict=True
            )
        ]
        for adapter, state, capture in zip(
            self.adapters,
            before,
            manifest["sources"],
            strict=True,
        ):
            verify_capture = getattr(
                adapter, "verify_terminal_rollback", None
            )
            if not callable(verify_capture):
                raise AdoptionError("terminal_bridge_adapter_unavailable")
            verify_capture(state, capture)
        phase = str(record["phase"])
        allowed_states: tuple[frozenset[HostState], ...]
        if phase == "AUTHORIZED":
            allowed_states = (
                frozenset({before[0]}),
                frozenset({before[1]}),
            )
        elif phase == "PREPARED":
            allowed_states = (
                frozenset({before[0], after[0]}),
                frozenset({before[1]}),
            )
        elif phase == "CODEX_APPLIED":
            allowed_states = (
                frozenset({after[0]}),
                frozenset({before[1], after[1]}),
            )
        elif phase in {"CLAUDE_APPLIED", "VERIFIED", "COMMITTED"}:
            allowed_states = (
                frozenset({after[0]}),
                frozenset({after[1]}),
            )
        elif phase == "ROLLED_BACK":
            allowed_states = (
                frozenset({before[0]}),
                frozenset({before[1]}),
            )
        else:
            allowed_states = (
                frozenset({before[0], after[0]}),
                frozenset({before[1], after[1]}),
            )
        observed_states: list[HostState | None] = []
        recoverable_intermediate_phases = {
            "AUTHORIZED",
            "PREPARED",
            "CODEX_APPLIED",
            "CLAUDE_APPLIED",
            "VERIFIED",
        }
        for adapter, expected_before, allowed in zip(
            self.adapters,
            before,
            allowed_states,
            strict=True,
        ):
            try:
                observed = adapter.observe()
            except AdoptionError:
                if phase not in recoverable_intermediate_phases | {
                    "ROLLING_BACK"
                }:
                    raise
                if phase != "ROLLING_BACK":
                    self._bridge_recovery_required = True
                observed_states.append(None)
                continue
            if observed not in allowed:
                if phase == "ROLLING_BACK":
                    observed_states.append(observed)
                    continue
                if phase not in recoverable_intermediate_phases:
                    raise AdoptionError("ordinary_phase_state_drift")
                self._bridge_recovery_required = True
            observed_states.append(observed)
        host_effect_started = phase in {
            "CODEX_APPLIED",
            "CLAUDE_APPLIED",
            "VERIFIED",
            "COMMITTED",
            "ROLLING_BACK",
            "ROLLED_BACK",
        } or any(
            observed is None or observed != expected_before
            for observed, expected_before in zip(
                observed_states,
                before,
                strict=True,
            )
        )
        if not host_effect_started:
            try:
                external_terminal_record = _read_terminal_journal(
                    self.terminal_bridge_root
                )
                if (
                    _terminal_journal_bytes(external_terminal_record)
                    != _terminal_journal_bytes(terminal_record)
                    or [
                        observer.observe()
                        for observer in self.terminal_observers
                    ]
                    != list(terminal_states)
                ):
                    raise AdoptionError("terminal_current_state_drift")
                if self._observe_canonical_recovery() != recovery:
                    raise AdoptionError("canonical_recovery_drift")
            except AdoptionError:
                if phase not in recoverable_intermediate_phases:
                    raise
                self._bridge_recovery_required = True
        return manifest

    def _require_terminal_record_bindings(
        self,
        record: dict[str, object],
    ) -> None:
        states = [
            TerminalHostState.from_projection(item, expected_host=host)
            for item, host in zip(
                record["before_states"],
                HOST_ORDER,
                strict=True,
            )
        ]
        recovery = CanonicalRecoveryState.from_projection(
            record["canonical_recovery"]
        )
        if self._observe_terminal_states() != states:
            raise AdoptionError("terminal_current_state_drift")
        if self._observe_canonical_recovery() != recovery:
            raise AdoptionError("canonical_recovery_drift")

    def _reconcile_terminal_prepared_backup(
        self,
        names: frozenset[str],
    ) -> None:
        backup = _TERMINAL_PREPARED_BACKUP_LEAF
        if names == {backup, _TERMINAL_STAGE_LEAF}:
            prepared, _prepared_bytes, backup_identity = _terminal_leaf_record(
                self.root,
                backup,
                phase="REQUEST_PREPARED",
                failure="terminal_prepared_backup_drift",
            )
            staged, _staged_bytes, stage_identity = _terminal_leaf_record(
                self.root,
                _TERMINAL_STAGE_LEAF,
                phase="COMMITTED",
                failure="terminal_stage_drift",
            )
            _require_terminal_stage_matches_prepared(prepared, staged)
            verified = _reverify_terminal_journal(staged)
            if (
                not verified.allowed
                or verified.request_digest != prepared["request_digest"]
            ):
                raise AdoptionError("terminal_stage_drift")
            self._require_terminal_record_bindings(prepared)
            _publish_terminal_stage_from_backup(
                self.root,
                prepared,
                staged,
                backup_identity=backup_identity,
                stage_identity=stage_identity,
                crash_hook=self.crash_hook,
            )
            return

        if names != {backup, "journal.json"}:
            raise AdoptionError("terminal_prepared_backup_drift")
        try:
            prepared, _prepared_bytes, _prepared_identity = (
                _terminal_leaf_record(
                    self.root,
                    "journal.json",
                    phase="REQUEST_PREPARED",
                    failure="terminal_prepared_backup_drift",
                )
            )
        except AdoptionError:
            prepared = None
        if prepared is not None:
            self._require_terminal_record_bindings(prepared)
            _collision, collision_identity = _terminal_temp_snapshot(
                self.root,
                backup,
            )
            _remove_terminal_temp(self.root, backup, collision_identity)
            return

        prepared, prepared_bytes, backup_identity = _terminal_leaf_record(
            self.root,
            backup,
            phase="REQUEST_PREPARED",
            failure="terminal_prepared_backup_drift",
        )
        self._require_terminal_record_bindings(prepared)
        try:
            committed, _committed_bytes, _committed_identity = (
                _terminal_leaf_record(
                    self.root,
                    "journal.json",
                    phase="COMMITTED",
                    failure="terminal_journal_publish_failed",
                )
            )
        except AdoptionError:
            _restore_terminal_prepared_backup(
                self.root,
                expected=prepared_bytes,
                backup_identity=backup_identity,
            )
            return
        _require_terminal_stage_matches_prepared(prepared, committed)
        verified = _reverify_terminal_journal(committed)
        if (
            not verified.allowed
            or verified.request_digest != prepared["request_digest"]
        ):
            raise AdoptionError("terminal_journal_authority_verification_failed")
        _remove_terminal_temp(self.root, backup, backup_identity)

    def _reconcile_terminal_publication_residue(self) -> None:
        """Promote complete deterministic temps or remove only partial residue."""

        try:
            names = frozenset(path.name for path in self.root.iterdir())
        except OSError as exc:
            raise AdoptionError("terminal_temp_drift") from exc
        if _TERMINAL_PREPARED_BACKUP_LEAF in names:
            self._reconcile_terminal_prepared_backup(names)
            return
        temp_names = names & {
            _TERMINAL_PREPARED_TEMP_LEAF,
            _TERMINAL_STAGE_TEMP_LEAF,
        }
        if not temp_names:
            return
        if len(temp_names) != 1:
            raise AdoptionError("terminal_temp_drift")
        temp_leaf = next(iter(temp_names))
        if temp_leaf == _TERMINAL_PREPARED_TEMP_LEAF:
            if names != {_TERMINAL_PREPARED_TEMP_LEAF}:
                raise AdoptionError("terminal_temp_drift")
            content, identity = _terminal_temp_snapshot(self.root, temp_leaf)
            try:
                value = json.loads(content.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _remove_terminal_temp(self.root, temp_leaf, identity)
                return
            if type(value) is not dict:
                raise AdoptionError("terminal_temp_drift")
            try:
                canonical = _terminal_prepared_bytes(value)
            except AdoptionError as exc:
                raise AdoptionError("terminal_temp_drift") from exc
            if canonical != content:
                raise AdoptionError("terminal_temp_drift")
            states = [
                TerminalHostState.from_projection(item, expected_host=host)
                for item, host in zip(
                    value["before_states"],
                    HOST_ORDER,
                    strict=True,
                )
            ]
            recovery = CanonicalRecoveryState.from_projection(
                value["canonical_recovery"]
            )
            if self._observe_terminal_states() != states:
                raise AdoptionError("terminal_current_state_drift")
            if self._observe_canonical_recovery() != recovery:
                raise AdoptionError("canonical_recovery_drift")
            _promote_terminal_temp_exclusive(
                self.root,
                temp_leaf=temp_leaf,
                visible_leaf="journal.json",
                content=content,
                identity=identity,
                failure="terminal_prepared_publish_failed",
            )
            return

        if names != {"journal.json", _TERMINAL_STAGE_TEMP_LEAF}:
            raise AdoptionError("terminal_temp_drift")
        prepared = _read_terminal_prepared(self.root)
        content, identity = _terminal_temp_snapshot(self.root, temp_leaf)
        try:
            value = json.loads(content.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _remove_terminal_temp(self.root, temp_leaf, identity)
            return
        if type(value) is not dict:
            raise AdoptionError("terminal_temp_drift")
        try:
            canonical = _terminal_journal_bytes(value)
        except AdoptionError as exc:
            raise AdoptionError("terminal_temp_drift") from exc
        if canonical != content:
            raise AdoptionError("terminal_temp_drift")
        _require_terminal_stage_matches_prepared(prepared, value)
        verified = _reverify_terminal_journal(value)
        if (
            not verified.allowed
            or verified.request_digest != prepared["request_digest"]
        ):
            raise AdoptionError("terminal_temp_drift")
        states = [
            TerminalHostState.from_projection(item, expected_host=host)
            for item, host in zip(
                prepared["before_states"],
                HOST_ORDER,
                strict=True,
            )
        ]
        recovery = CanonicalRecoveryState.from_projection(
            prepared["canonical_recovery"]
        )
        if self._observe_terminal_states() != states:
            raise AdoptionError("terminal_current_state_drift")
        if self._observe_canonical_recovery() != recovery:
            raise AdoptionError("canonical_recovery_drift")
        _promote_terminal_temp_exclusive(
            self.root,
            temp_leaf=temp_leaf,
            visible_leaf=_TERMINAL_STAGE_LEAF,
            content=content,
            identity=identity,
            failure="terminal_journal_publish_failed",
        )

    def _run_terminalize_under_locks(self) -> dict[str, object]:
        self._reconcile_terminal_publication_residue()
        journal = _journal_path(self.root)
        prepared_replay = False
        if journal.exists() or journal.is_symlink():
            try:
                root_names = frozenset(path.name for path in self.root.iterdir())
                if root_names not in (
                    frozenset({"journal.json"}),
                    frozenset({"journal.json", _TERMINAL_STAGE_LEAF}),
                ):
                    raise AdoptionError("terminal_state_root_not_fresh")
            except OSError as exc:
                raise AdoptionError("terminal_state_root_not_fresh") from exc
            stage_present = _TERMINAL_STAGE_LEAF in root_names
            record = _read_terminal_record(self.root)
            states = [
                TerminalHostState.from_projection(value, expected_host=host)
                for value, host in zip(
                    record["before_states"], HOST_ORDER, strict=True
                )
            ]
            recovery = CanonicalRecoveryState.from_projection(
                record["canonical_recovery"]
            )
            if self._observe_terminal_states() != states:
                raise AdoptionError("terminal_current_state_drift")
            if self._observe_canonical_recovery() != recovery:
                raise AdoptionError("canonical_recovery_drift")
            if record["phase"] == "COMMITTED":
                if stage_present:
                    raise AdoptionError("terminal_state_root_not_fresh")
                verified = _reverify_terminal_journal(record)
                if (
                    verified.request["plan"]["before_state_digest"]
                    != _terminal_states_digest(states)
                ):
                    raise AdoptionError(
                        "terminal_journal_authority_verification_failed"
                    )
                _fsync_directory(self.root)
                return {
                    "status": "terminalized",
                    "transaction_id": record["transaction_id"],
                }
            prepared = record
            if stage_present:
                staged = _read_terminal_stage(self.root)
                _require_terminal_stage_matches_prepared(prepared, staged)
                verified = _reverify_terminal_journal(staged)
                if (
                    not verified.allowed
                    or verified.request_digest != prepared["request_digest"]
                ):
                    raise AdoptionError("terminal_stage_drift")
                if self._observe_terminal_states() != states:
                    raise AdoptionError("terminal_current_state_drift")
                if self._observe_canonical_recovery() != recovery:
                    raise AdoptionError("canonical_recovery_drift")
                _replace_existing_terminal_stage(
                    self.root,
                    prepared,
                    staged,
                    crash_hook=self.crash_hook,
                )
                self.crash_hook("COMMITTED")
                return {
                    "status": "terminalized",
                    "transaction_id": staged["transaction_id"],
                }
            prepared_replay = True
        else:
            try:
                if any(self.root.iterdir()):
                    raise AdoptionError("terminal_state_root_not_fresh")
            except OSError as exc:
                raise AdoptionError("terminal_state_root_not_fresh") from exc

            states = self._observe_terminal_states()
            recovery = self._observe_canonical_recovery()
            state_digest = _terminal_states_digest(states)
            current_identity_digest = _terminal_current_identity_digest(states)
            predecessor_identity_digest = _canonical_digest({
                "host_state_digests": [
                    state.identity_digest() for state in states
                ],
                "operation": authority.TERMINAL_OPERATION,
                "plugin_id": authority.PLUGIN_ID,
                "plugin_version": authority.TERMINAL_PLUGIN_VERSION,
            })
            canonical_identity_digest = recovery.identity_digest()
            rollback_manifest_digest = _canonical_digest({
                "host_order": list(HOST_ORDER),
                "policy": "observation_only_no_host_mutation.v1",
                "states": [state.projection() for state in states],
            })
            transaction_seed = authority.canonical_bytes({
                "canonical_identity_digest": canonical_identity_digest,
                "current_identity_digest": current_identity_digest,
                "operation": authority.TERMINAL_OPERATION,
                "predecessor_identity_digest": predecessor_identity_digest,
            })
            transaction_id = "plugin-adoption-terminal-" + hashlib.sha256(
                transaction_seed
            ).hexdigest()[:32]
            decision_id = "plugin-adoption-terminal-decision-" + hashlib.sha256(
                b"decision\0" + transaction_seed
            ).hexdigest()[:32]
            plan_without_digest = {
                "marketplace_id": authority.MARKETPLACE_ID,
                "plugin_id": authority.PLUGIN_ID,
                "plugin_version": authority.TERMINAL_PLUGIN_VERSION,
                "source_revision": authority.TERMINAL_SOURCE_REVISION,
                "source_bundle_digest": recovery.source_bundle_digest,
                "target_set": list(authority.TARGET_SET),
                "transition_set": list(authority.TERMINAL_TRANSITION_SET),
                "predecessor_identity_digest": predecessor_identity_digest,
                "current_identity_digest": current_identity_digest,
                "canonical_identity_digest": canonical_identity_digest,
                "codex_current_state_digest": states[0].identity_digest(),
                "claude_current_state_digest": states[1].identity_digest(),
                "before_state_digest": state_digest,
                "after_state_digest": state_digest,
                "rollback_manifest_digest": rollback_manifest_digest,
            }
            plan = {
                **plan_without_digest,
                "plan_digest": authority.compute_terminal_plan_digest(
                    plan_without_digest
                ),
            }
            issued_at = float(self.clock())
            request = authority.build_plugin_adoption_terminal_request(
                decision_id=decision_id,
                transaction_id=transaction_id,
                source_runtime_revision=authority.TERMINAL_SOURCE_REVISION,
                issued_at=issued_at,
                expires_at=issued_at + 120.0,
                plan=plan,
            )
            prepared = _terminal_prepared_from_request(
                request,
                states=states,
                recovery=recovery,
            )
            _write_terminal_prepared_exclusive(
                self.root,
                prepared,
                crash_hook=self.crash_hook,
            )
            self.crash_hook("TERMINAL_REQUEST_PREPARED")

        request_bytes = base64.b64decode(
            str(prepared["request_b64"]),
            validate=True,
        )
        request_value = authority._base._parse_canonical_authority_payload(
            request_bytes
        )
        request = authority.validate_terminal_request(request_value)
        issued_at = float(request["actual"]["issued_at"])
        transport_now = (
            float(self.clock()) if prepared_replay else issued_at + 0.001
        )
        if self.terminal_authority_request is None:
            raise AdoptionError("terminal_authority_unavailable")
        if self._observe_terminal_states() != states:
            raise AdoptionError("terminal_current_state_drift")
        if self._observe_canonical_recovery() != recovery:
            raise AdoptionError("canonical_recovery_drift")
        verified = self.terminal_authority_request(
            request,
            now=transport_now,
            prepared_replay=prepared_replay,
        )
        if not verified.allowed:
            raise AdoptionError("plugin_adoption_terminalize_denied")
        if self._observe_terminal_states() != states:
            raise AdoptionError("terminal_current_state_drift")
        if self._observe_canonical_recovery() != recovery:
            raise AdoptionError("canonical_recovery_drift")
        verified = authority.verify_plugin_adoption_terminal_envelope(
            request_bytes=verified.request_bytes,
            envelope_bytes=verified.envelope_bytes,
            now=issued_at + 0.001 if prepared_replay else transport_now,
        )
        if (
            verified.request != request
            or verified.request_bytes != request_bytes
            or verified.request_digest != prepared["request_digest"]
            or not verified.allowed
        ):
            raise AdoptionError("terminal_authority_verification_failed")
        self.crash_hook("TERMINAL_AUTHORIZED")
        record = _terminal_record_from_prepared(
            verified,
            prepared,
        )
        _replace_terminal_prepared_with_journal(
            self.root,
            prepared,
            record,
            crash_hook=self.crash_hook,
        )
        self.crash_hook("COMMITTED")
        return {
            "status": "terminalized",
            "transaction_id": record["transaction_id"],
        }

    def _fresh_authorization(self) -> tuple[dict[str, object], list[HostState], list[HostState]]:
        _lstat_admitted_directory(self.root, create=True)
        if _journal_path(self.root).exists() or _journal_path(self.root).is_symlink():
            raise AdoptionError("active_transaction_exists")
        if (
            _ordinary_consumed_path(self.root).exists()
            or _ordinary_consumed_path(self.root).is_symlink()
        ):
            raise AdoptionError("ordinary_consumed_without_journal")
        bridge_manifest: dict[str, object] | None = None
        if self.terminal_bridge_root is not None:
            if (
                _ordinary_prepared_path(self.root).exists()
                or _ordinary_prepared_path(self.root).is_symlink()
            ):
                before, bridge_manifest = (
                    self._resume_private_terminal_bridge_manifest()
                )
            else:
                before, bridge_manifest = self._fresh_terminal_bridge_manifest()
        else:
            before = [adapter.observe() for adapter in self.adapters]
            _require_reversible_before_states(
                before,
                admitted_previous=self.admitted_previous_states,
            )
        source_revision = _source_revision()
        source_bundle_digest = _source_bundle_digest()
        transaction_seed = authority.canonical_bytes({
            "before": [state.projection() for state in before],
            "source_revision": source_revision,
            "version": authority.PLUGIN_VERSION,
        })
        transaction_id = "plugin-adoption-" + hashlib.sha256(transaction_seed).hexdigest()[:32]
        decision_id = "plugin-adoption-decision-" + hashlib.sha256(
            b"decision\0" + transaction_seed
        ).hexdigest()[:32]
        installed_digest, marketplace_digest = _candidate_installed_digests(
            self.root
        )
        if (
            _source_revision() != source_revision
            or _source_bundle_digest() != source_bundle_digest
        ):
            raise AdoptionError("source_bundle_cas_mismatch")
        after = [
            _expected_after_state(
                host,
                installed_digest,
                marketplace_digest,
                source_revision,
                source_bundle_digest,
                before_state,
            )
            for host, before_state in zip(HOST_ORDER, before, strict=True)
        ]
        rollback_digest = (
            hashlib.sha256(
                _terminal_bridge_manifest_bytes(bridge_manifest)
            ).hexdigest()
            if bridge_manifest is not None
            else _canonical_digest(_rollback_manifest(before))
        )
        plan_without_digest = {
            "marketplace_id": authority.MARKETPLACE_ID,
            "plugin_id": authority.PLUGIN_ID,
            "plugin_version": authority.PLUGIN_VERSION,
            "source_revision": source_revision,
            "source_bundle_digest": source_bundle_digest,
            "target_set": list(authority.TARGET_SET),
            "transition_set": list(authority.TRANSITION_SET),
            "before_state_digest": _states_digest(before),
            "after_state_digest": _states_digest(after),
            "rollback_manifest_digest": rollback_digest,
        }
        plan = {
            **plan_without_digest,
            "plan_digest": authority.compute_plan_digest(plan_without_digest),
        }
        prepared: dict[str, object] | None = None
        prepared_replay = False
        if (
            bridge_manifest is not None
            and (
                _ordinary_prepared_path(self.root).exists()
                or _ordinary_prepared_path(self.root).is_symlink()
            )
        ):
            prepared = _read_ordinary_prepared(self.root)
            request_bytes = base64.b64decode(
                str(prepared["request_b64"]), validate=True
            )
            request_value = authority._base._parse_canonical_authority_payload(
                request_bytes
            )
            request = authority.validate_request(request_value, now=None)
            if (
                request["plan"] != plan
                or request["actual"]["transaction_id"] != transaction_id
                or request["actual"]["decision_id"] != decision_id
                or prepared["before_states"]
                != [state.projection() for state in before]
                or prepared["after_states"]
                != [state.projection() for state in after]
                or prepared["manifest_digest"] != rollback_digest
            ):
                raise AdoptionError("ordinary_prepared_request_mismatch")
            issued_at = float(request["actual"]["issued_at"])
            prepared_replay = True
        else:
            issued_at = float(self.clock())
            request = authority.build_plugin_adoption_request(
                decision_id=decision_id,
                transaction_id=transaction_id,
                source_runtime_revision=source_revision,
                issued_at=issued_at,
                expires_at=issued_at + 120.0,
                plan=plan,
            )
            if bridge_manifest is not None:
                prepared = _ordinary_prepared_from_request(
                    request, before=before, after=after
                )
                _write_ordinary_prepared(self.root, prepared)
                self.crash_hook("ORDINARY_REQUEST_PREPARED")
        if bridge_manifest is not None:
            transport_now = (
                float(self.clock()) if prepared_replay else issued_at + 0.001
            )
            verified = self.authority_request(
                request,
                now=transport_now,
                prepared_replay=prepared_replay,
            )
            self.crash_hook("ORDINARY_AUTHORIZED")
            verified = authority.verify_plugin_adoption_envelope(
                request_bytes=verified.request_bytes,
                envelope_bytes=verified.envelope_bytes,
                now=issued_at + 0.001 if prepared_replay else transport_now,
            )
            if (
                verified.request != request
                or verified.request_bytes != authority.canonical_bytes(request)
                or not verified.allowed
            ):
                raise AdoptionError("ordinary_authority_replay_mismatch")
        else:
            verified = self.authority_request(request, now=issued_at + 0.001)
        if not verified.allowed:
            raise AdoptionError("plugin_adoption_denied")
        record = _record_from_verified(verified, before=before, after=after)
        if bridge_manifest is not None:
            if prepared is None:
                raise AdoptionError("ordinary_prepared_unavailable")
            _write_journal_exclusive(self.root, record)
            self.crash_hook("ORDINARY_JOURNAL_PUBLISHED")
            _remove_ordinary_prepared(self.root, prepared)
        else:
            _write_journal(self.root, record)
        self.crash_hook("AUTHORIZED")
        return record, before, after

    def _states_from_verified(
        self,
        verified: authority.VerifiedPluginAdoptionEnvelope,
        record: dict[str, object],
    ) -> tuple[list[HostState], list[HostState]]:
        before = [
            HostState.from_projection(value, expected_host=host)
            for value, host in zip(record["before_states"], HOST_ORDER, strict=True)
        ]
        after = [
            HostState.from_projection(value, expected_host=host)
            for value, host in zip(record["after_states"], HOST_ORDER, strict=True)
        ]
        if _states_digest(before) != verified.request["plan"]["before_state_digest"]:
            raise AdoptionError("before_state_plan_mismatch")
        _require_reversible_before_states(
            before,
            admitted_previous=self.admitted_previous_states,
            admitted_terminal=(
                before if self.terminal_bridge_root is not None else None
            ),
        )
        if _states_digest(after) != verified.request["plan"]["after_state_digest"]:
            raise AdoptionError("after_state_plan_mismatch")
        return before, after

    def _reconcile_ordinary_prepared_with_journal(
        self,
        record: dict[str, object],
    ) -> None:
        path = _ordinary_prepared_path(self.root)
        consumed = _ordinary_consumed_path(self.root)
        expected = {
            "after_states": record["after_states"],
            "before_states": record["before_states"],
            "decision_id": record["decision_id"],
            "manifest_digest": record["rollback_manifest_digest"],
            "phase": "REQUEST_PREPARED",
            "request_b64": record["request_b64"],
            "request_digest": record["request_digest"],
            "schema": JOURNAL_SCHEMA,
            "transaction_id": record["transaction_id"],
        }
        if consumed.exists() or consumed.is_symlink():
            if path.exists() or path.is_symlink():
                raise AdoptionError("ordinary_prepared_journal_mismatch")
            if _private_file_bytes(consumed) != _ordinary_prepared_bytes(expected):
                raise AdoptionError("ordinary_prepared_journal_mismatch")
            return
        if not (path.exists() or path.is_symlink()):
            raise AdoptionError("ordinary_prepared_unavailable")
        prepared = _read_ordinary_prepared(self.root)
        if (
            prepared["request_digest"] != record["request_digest"]
            or prepared["transaction_id"] != record["transaction_id"]
            or prepared["decision_id"] != record["decision_id"]
            or prepared["before_states"] != record["before_states"]
            or prepared["after_states"] != record["after_states"]
            or prepared["manifest_digest"]
            != record["rollback_manifest_digest"]
            or prepared["request_b64"] != record["request_b64"]
        ):
            raise AdoptionError("ordinary_prepared_journal_mismatch")
        _remove_ordinary_prepared(self.root, prepared)

    def run(self) -> dict[str, object]:
        if self.action != "terminalize":
            _require_ordinary_apply_plugin_version_alignment()
        _lstat_admitted_directory(self.root, create=True)
        archived_transaction_id: str | None = None
        with _host_locks(
            self.root,
            typed_failure=self.action == "terminalize",
        ):
            if self.action == "terminalize":
                return self._run_terminalize_under_locks()
            if _journal_path(self.root).exists():
                record = _read_journal(self.root)
                verified = _reverify_journal(record)
                before, after = self._states_from_verified(verified, record)
                if self.terminal_bridge_root is not None:
                    self._reverify_terminal_bridge_manifest(record, verified)
                    self._reconcile_ordinary_prepared_with_journal(record)
            else:
                record, before, after = self._fresh_authorization()
                verified = _reverify_journal(record)
                if self.terminal_bridge_root is not None:
                    self._reverify_terminal_bridge_manifest(record, verified)
            phase = str(record["phase"])
            if self._bridge_recovery_required:
                record = _advance_journal(
                    self.root,
                    record,
                    "ROLLING_BACK",
                    crash_hook=self.crash_hook,
                )
                phase = "ROLLING_BACK"
            if phase == "COMMITTED":
                observed = [
                    adapter.verify(str(record["transaction_id"]), expected)
                    for adapter, expected in zip(self.adapters, after, strict=True)
                ]
                if _states_digest(observed) != record["after_state_digest"]:
                    raise AdoptionError("committed_state_drift")
                result: dict[str, object] = {
                    "status": "committed",
                    "transaction_id": record["transaction_id"],
                }
                if archived_transaction_id is not None:
                    result["archived_transaction_id"] = archived_transaction_id
                return result
            if phase == "ROLLED_BACK":
                observed = [adapter.observe() for adapter in self.adapters]
                if _states_digest(observed) != record["before_state_digest"]:
                    raise AdoptionError("rolled_back_state_drift")
                if (
                    not self.archive_rolled_back
                    or self.terminal_bridge_root is not None
                ):
                    return {
                        "status": "rolled_back",
                        "transaction_id": record["transaction_id"],
                    }
                archived_transaction_id = _archive_terminal_transaction(
                    self.root,
                    record,
                    crash_hook=self.crash_hook,
                )
                _lstat_admitted_directory(self.root, create=True)
                record, before, after = self._fresh_authorization()
                verified = _reverify_journal(record)
                phase = str(record["phase"])
            if phase == "ROLLING_BACK":
                for adapter, expected in reversed(
                    tuple(zip(self.adapters, before, strict=True))
                ):
                    adapter.rollback(str(record["transaction_id"]), expected)
                observed = [adapter.observe() for adapter in self.adapters]
                if _states_digest(observed) != record["before_state_digest"]:
                    raise AdoptionError("rollback_verification_failed")
                record = _advance_journal(
                    self.root, record, "ROLLED_BACK", crash_hook=self.crash_hook
                )
                return {
                    "status": "rolled_back",
                    "transaction_id": record["transaction_id"],
                }
            try:
                if phase == "AUTHORIZED":
                    signed_plan = verified.request["plan"]
                    signed_actual = verified.request["actual"]
                    if (
                        _source_revision() != signed_plan["source_revision"]
                        or _source_revision() != signed_actual["source_runtime_revision"]
                        or _source_bundle_digest() != signed_plan["source_bundle_digest"]
                    ):
                        raise AdoptionError("source_bundle_cas_mismatch")
                    # Build and verify adapter-owned private marketplace stages.
                    # No host marketplace/config/cache surface is touched here.
                    for adapter, expected in zip(self.adapters, after, strict=True):
                        adapter.prepare(str(record["transaction_id"]), expected)
                    record = _advance_journal(
                        self.root, record, "PREPARED", crash_hook=self.crash_hook
                    )
                    phase = "PREPARED"
                if phase == "PREPARED":
                    observed = self.adapters[0].observe()
                    if observed != after[0]:
                        self.adapters[0].apply(
                            str(record["transaction_id"]), before[0], after[0]
                        )
                    self.adapters[0].verify(str(record["transaction_id"]), after[0])
                    record = _advance_journal(
                        self.root, record, "CODEX_APPLIED", crash_hook=self.crash_hook
                    )
                    phase = "CODEX_APPLIED"
                if phase == "CODEX_APPLIED":
                    observed = self.adapters[1].observe()
                    if observed != after[1]:
                        self.adapters[1].apply(
                            str(record["transaction_id"]), before[1], after[1]
                        )
                    self.adapters[1].verify(str(record["transaction_id"]), after[1])
                    record = _advance_journal(
                        self.root, record, "CLAUDE_APPLIED", crash_hook=self.crash_hook
                    )
                    phase = "CLAUDE_APPLIED"
                if phase == "CLAUDE_APPLIED":
                    observed = [
                        adapter.verify(str(record["transaction_id"]), expected)
                        for adapter, expected in zip(self.adapters, after, strict=True)
                    ]
                    if _states_digest(observed) != record["after_state_digest"]:
                        raise AdoptionError("after_state_mismatch")
                    record = _advance_journal(
                        self.root, record, "VERIFIED", crash_hook=self.crash_hook
                    )
                    phase = "VERIFIED"
                if phase == "VERIFIED":
                    observed = [
                        adapter.verify(str(record["transaction_id"]), expected)
                        for adapter, expected in zip(self.adapters, after, strict=True)
                    ]
                    if _states_digest(observed) != record["after_state_digest"]:
                        raise AdoptionError("after_state_mismatch")
                    record = _advance_journal(
                        self.root, record, "COMMITTED", crash_hook=self.crash_hook
                    )
                result = {
                    "status": "committed",
                    "transaction_id": record["transaction_id"],
                }
                if archived_transaction_id is not None:
                    result["archived_transaction_id"] = archived_transaction_id
                return result
            except InjectedCrash:
                raise
            except Exception as exc:
                failure_code = (
                    str(exc)
                    if isinstance(exc, AdoptionError)
                    and re.fullmatch(r"[a-z0-9_]+", str(exc)) is not None
                    else "internal_failure"
                )
                record = _advance_journal(
                    self.root, record, "ROLLING_BACK", crash_hook=self.crash_hook
                )
                rollback_error: Exception | None = None
                for adapter, expected in reversed(
                    tuple(zip(self.adapters, before, strict=True))
                ):
                    try:
                        adapter.rollback(str(record["transaction_id"]), expected)
                    except Exception as caught:
                        rollback_error = caught
                        break
                if rollback_error is not None:
                    raise AdoptionError("rollback_incomplete") from rollback_error
                observed = [adapter.observe() for adapter in self.adapters]
                if _states_digest(observed) != record["before_state_digest"]:
                    raise AdoptionError("rollback_verification_failed")
                _advance_journal(
                    self.root, record, "ROLLED_BACK", crash_hook=self.crash_hook
                )
                raise AdoptionError(
                    f"plugin_adoption_rolled_back:{failure_code}"
                ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("apply", "resume", "status", "terminalize"),
    )
    return parser


def _previous_committed_context() -> tuple[Path, tuple[HostState, ...]] | None:
    previous_root = _previous_state_root()
    if not _journal_path(previous_root).is_file():
        return None
    record = _read_journal(previous_root)
    if record.get("phase") != "COMMITTED":
        raise AdoptionError("previous_terminal_not_committed")
    verified = _reverify_journal(record)
    after = tuple(
        HostState.from_projection(value, expected_host=host)
        for value, host in zip(record["after_states"], HOST_ORDER, strict=True)
    )
    if (
        verified.request["plan"]["plugin_version"]
        != authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
        or _states_digest(after) != record["after_state_digest"]
        or _states_digest(after)
        != verified.request["plan"]["after_state_digest"]
    ):
        raise AdoptionError("previous_committed_state_unavailable")
    _require_reversible_before_states(after, admitted_previous=after)
    return previous_root, after


def _previous_context_required(action: str, root: Path) -> bool:
    """Keep the fixed signed predecessor across apply, archive, and resume."""

    del root
    return action in {"apply", "resume"}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = (
        _fixed_terminal_state_root()
        if args.action == "terminalize"
        else _fixed_state_root()
    )
    try:
        if args.action == "terminalize":
            terminal_source_root = _fixed_terminal_source_root()
            terminal_observers = tuple(
                FixedTerminalHostObserver(
                    host,
                    root,
                    terminal_source_root,
                )
                for host in HOST_ORDER
            )
            result = PluginAdoptionExecutor(
                state_root=root,
                adapters=(),
                authority_request=authority.request_plugin_adoption_decision,
                action="terminalize",
                terminal_observers=terminal_observers,
                canonical_recovery_observer=FixedCanonicalRecoveryObserver(
                    _fixed_canonical_recovery_root()
                ),
                terminal_authority_request=(
                    authority.request_plugin_adoption_terminal_decision
                ),
            ).run()
            print(json.dumps(result, sort_keys=True))
            return 0
        _require_ordinary_apply_plugin_version_alignment()
        if args.action == "status":
            status_root = root
            if not _journal_path(status_root).is_file():
                status_root = _previous_state_root()
            record = _read_journal(status_root)
            _reverify_journal(record)
            print(json.dumps({
                "phase": record["phase"],
                "status": "signed_journal_verified",
                "transaction_id": record["transaction_id"],
            }, sort_keys=True))
            return 0
        if (
            args.action == "resume"
            and not _journal_path(root).is_file()
            and not _ordinary_prepared_path(root).is_file()
            and not _terminal_bridge_manifest_path(root).is_file()
        ):
            raise AdoptionError("journal_unavailable")
        terminal_root = _fixed_terminal_state_root()
        terminal_source_root = _fixed_terminal_source_root()
        terminal_bridge = (
            _terminal_bridge_manifest_path(root).exists()
            or _terminal_bridge_manifest_path(root).is_symlink()
            or _ordinary_prepared_path(root).exists()
            or _ordinary_prepared_path(root).is_symlink()
            or _journal_path(terminal_root).exists()
            or _journal_path(terminal_root).is_symlink()
        )
        previous = (
            _previous_committed_context()
            if not terminal_bridge
            and _previous_context_required(args.action, root)
            else None
        )
        previous_root = previous[0] if previous is not None else None
        previous_states = previous[1] if previous is not None else None
        adapters = tuple(
            FixedHostAdapter(
                host,
                root,
                previous_transaction_root=previous_root,
                previous_state=previous_states[index]
                if previous_states is not None
                else None,
                terminal_source_transaction_root=(
                    terminal_source_root if terminal_bridge else None
                ),
            )
            for index, host in enumerate(HOST_ORDER)
        )
        executor = PluginAdoptionExecutor(
            state_root=root,
            adapters=adapters,
            authority_request=authority.request_plugin_adoption_decision,
            archive_rolled_back=args.action == "apply",
            admitted_previous_states=previous_states,
            terminal_bridge_root=(
                terminal_root if terminal_bridge else None
            ),
            terminal_observers=(
                tuple(
                    FixedTerminalHostObserver(
                        host,
                        terminal_root,
                        terminal_source_root,
                    )
                    for host in HOST_ORDER
                )
                if terminal_bridge
                else None
            ),
            canonical_recovery_observer=(
                FixedCanonicalRecoveryObserver(
                    _fixed_canonical_recovery_root()
                )
                if terminal_bridge
                else None
            ),
        )
        result = executor.run()
    except (AdoptionError, authority.PluginAdoptionAuthorityError) as exc:
        print(json.dumps({"code": str(exc), "status": "failed"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
