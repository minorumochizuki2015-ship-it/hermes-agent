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


class AdoptionError(RuntimeError):
    """A stable, sanitized adoption failure."""


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
    target=Path("/opt/homebrew/Caskroom/codex/0.146.0/bin/codex"),
    link_target="/opt/homebrew/Caskroom/codex/0.146.0/bin/codex",
    version_output=b"codex-cli 0.146.0\n",
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
            link_target="/opt/homebrew/Caskroom/codex/0.146.0/bin/codex",
        ),
        _homebrew_directory("/opt/homebrew/Caskroom", 0o775),
        _homebrew_directory("/opt/homebrew/Caskroom/codex"),
        _homebrew_directory("/opt/homebrew/Caskroom/codex/0.146.0"),
        _homebrew_directory("/opt/homebrew/Caskroom/codex/0.146.0/bin"),
        _FixedCliNode(
            Path("/opt/homebrew/Caskroom/codex/0.146.0/bin/codex"),
            "regular",
            _HOMEBREW_OWNER_UID,
            _HOMEBREW_GROUP_GID,
            0o755,
            size=271056976,
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
                os.O_WRONLY
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
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > maximum
        ):
            raise AdoptionError("journal_drift")
        content = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(content) > maximum or (
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
        raise AdoptionError("journal_drift")
    return content


def _journal_path(root: Path) -> Path:
    return root / "journal.json"


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


def _read_journal(root: Path) -> dict[str, object]:
    try:
        value = json.loads(_private_file_bytes(_journal_path(root)).decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError("journal_unavailable") from exc
    if type(value) is not dict:
        raise AdoptionError("journal_contract_mismatch")
    _journal_bytes(value)
    return value


def _write_journal(root: Path, record: dict[str, object]) -> None:
    _atomic_private_write(_journal_path(root), _journal_bytes(record))


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
    published = False
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_descriptor)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _rename_directory_exclusive(parent_descriptor, temporary, name)
        published = True
        os.fsync(parent_descriptor)
    except OSError as exc:
        raise AdoptionError("terminal_archive_drift") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass


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
    })
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
    allowed_edges = {
        "AUTHORIZED": {"PREPARED", "ROLLING_BACK"},
        "PREPARED": {"CODEX_APPLIED", "ROLLING_BACK"},
        "CODEX_APPLIED": {"CLAUDE_APPLIED", "ROLLING_BACK"},
        "CLAUDE_APPLIED": {"VERIFIED", "ROLLING_BACK"},
        "VERIFIED": {"COMMITTED", "ROLLING_BACK"},
        "ROLLING_BACK": {"ROLLED_BACK"},
        "COMMITTED": set(),
        "ROLLED_BACK": set(),
    }
    if phase not in allowed_edges[current]:
        raise AdoptionError("journal_transition_invalid")
    successor = {**record, "phase": phase}
    _write_journal(root, successor)
    crash_hook(phase)
    return successor


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
) -> tuple[str, ...]:
    if not ignored_sets:
        raise AdoptionError("generated_candidate_drift")
    uid = os.getuid()
    rows: list[tuple[str, str]] = []
    held_directories: list[tuple[int, str, int, tuple[int, ...]]] = []
    held_files: list[tuple[int, str, int, tuple[int, ...]]] = []
    root_before = os.fstat(descriptor)
    validators = file_validators or {}

    def path_is_ignored(relative: str, ignored: frozenset[str]) -> bool:
        return any(
            relative == candidate or relative.startswith(f"{candidate}/")
            for candidate in ignored
        )

    def ignored_by_every_projection(relative: str) -> bool:
        return all(path_is_ignored(relative, ignored) for ignored in ignored_sets)

    def walk(current: int, prefix: str = "") -> None:
        current_before = os.fstat(current)
        if (
            not stat.S_ISDIR(current_before.st_mode)
            or current_before.st_uid != uid
            or current_before.st_mode & 0o022
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
                if file_identity != _archive_stat_identity(info):
                    os.close(file_descriptor)
                    raise AdoptionError("generated_candidate_drift")
                held_files.append(
                    (current, name, file_descriptor, file_identity)
                )
                hasher = hashlib.sha256()
                captured = bytearray() if relative in validators else None
                while chunk := os.read(file_descriptor, 1024 * 1024):
                    hasher.update(chunk)
                    if captured is not None:
                        captured.extend(chunk)
                        if len(captured) > 64:
                            raise AdoptionError("generated_candidate_drift")
                if captured is not None:
                    validators[relative](before, bytes(captured))
                rows.append(
                    (
                        relative,
                        f"f {stat.S_IMODE(info.st_mode):04o} {info.st_size} "
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
        cache_digest = _tree_digest(cache, ignored=frozenset({".in_use"}))
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
        selector = f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}"
        if self.name == "codex":
            self._run(("plugin", "marketplace", "add", str(marketplace_root), "--json"))
            self._run(("plugin", "add", selector, "--json"))
        else:
            self._run(("plugin", "marketplace", "add", str(marketplace_root), "--scope", "user"))
            self._run(("plugin", "install", selector, "--scope", "user"))
            plugins = self._run(("plugin", "list", "--json"), json_output=True)
            present, version, active = _row_projection(_find_plugin_row(plugins))
            if marketplace_root == self._stage_marketplace_root():
                expected_version = authority.PLUGIN_VERSION
            elif (
                self._previous_transaction_root is not None
                and marketplace_root == self._previous_stage_marketplace_root()
            ):
                expected_version = authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
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

        if before != getattr(self, "_previous_state", None):
            return
        source = self._cache.with_name(authority.PREVIOUS_TERMINAL_PLUGIN_VERSION)
        handle = "previous-active-v" + authority.PREVIOUS_TERMINAL_PLUGIN_VERSION.replace(
            ".", ""
        )
        destination = self._quarantine_root() / handle
        source_present = source.exists() or source.is_symlink()
        destination_present = destination.exists() or destination.is_symlink()
        if not source_present:
            if destination_present:
                self._entry_for_path(
                    destination,
                    version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
                    handle=handle,
                )
            return
        entry = self._entry_for_path(
            source,
            version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
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

    def _restore_quarantine(self, before: HostState) -> None:
        if not before.quarantine_entries:
            return
        quarantine = self._quarantine_root()
        _lstat_admitted_directory(quarantine)
        cache_parent = self._cache.parent
        _validate_fixed_host_chain(cache_parent, allow_missing=False)
        if not self._registry_is_exact_predecessor():
            plugin_present, marketplace_present = self._cleanup_presence()
            if plugin_present:
                self._remove_plugin()
            if marketplace_present:
                self._remove_marketplace()
            self._install_from(_resolve_predecessor_source())
        self._quarantine_failed_candidate()
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
                    _rename_directory_between_exclusive(
                        destination_descriptor,
                        entry.version,
                        source_descriptor,
                        generated_handle,
                    )
                    os.fsync(destination_descriptor)
                    os.fsync(source_descriptor)
                if not quarantine_present or self._entry_for_path(
                    quarantined,
                    version=entry.version,
                    handle=entry.handle,
                ) != entry:
                    raise AdoptionError("quarantine_entry_drift")
                _rename_directory_between_exclusive(
                    source_descriptor,
                    entry.handle,
                    destination_descriptor,
                    entry.version,
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

    @staticmethod
    def _rollback_marker(
        before: HostState,
        *,
        previous_state: HostState | None = None,
    ) -> dict[str, object]:
        if before == previous_state:
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
            self._exact_directory_names(rollback, frozenset({"predecessor.json"}))
            actual = _private_file_bytes(rollback / "predecessor.json")
        except (OSError, AdoptionError) as exc:
            raise AdoptionError("rollback_source_drift") from exc
        if actual != _json_bytes(
            self._rollback_marker(
                before,
                previous_state=getattr(self, "_previous_state", None),
            )
        ):
            raise AdoptionError("rollback_source_drift")

    def _capture_rollback_marketplace(self, before: HostState) -> None:
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
        if current == expected_before:
            return expected_before
        if not rollback.is_dir() or rollback.is_symlink():
            raise AdoptionError("rollback_source_unavailable")
        try:
            _require_reversible_before_states(
                (expected_before,),
                admitted_previous=(expected_before,)
                if expected_before == getattr(self, "_previous_state", None)
                else None,
            )
        except AdoptionError as exc:
            raise AdoptionError("rollback_source_unavailable") from exc
        self._exact_directory_names(rollback, frozenset({"predecessor.json"}))
        self._verify_rollback_marker(rollback, expected_before)
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


def _require_reversible_before_states(
    states: Sequence[HostState],
    *,
    admitted_previous: Sequence[HostState] | None = None,
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
def _host_locks(root: Path) -> Iterator[None]:
    """Acquire the canonical host lock order: Codex, then Claude."""

    lock_root = _lock_root(root)
    _lstat_admitted_directory(lock_root, create=True)
    descriptors: list[int] = []
    try:
        for host in HOST_ORDER:
            path = lock_root / f"{host}.lock"
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise AdoptionError("host_lock_drift")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            descriptors.append(descriptor)
        yield
    finally:
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


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
    ):
        if tuple(adapter.name for adapter in adapters) != HOST_ORDER:
            raise AdoptionError("host_order_mismatch")
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

    def _fresh_authorization(self) -> tuple[dict[str, object], list[HostState], list[HostState]]:
        _lstat_admitted_directory(self.root, create=True)
        if _journal_path(self.root).exists() or _journal_path(self.root).is_symlink():
            raise AdoptionError("active_transaction_exists")
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
        rollback_digest = _canonical_digest(_rollback_manifest(before))
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
        issued_at = float(self.clock())
        request = authority.build_plugin_adoption_request(
            decision_id=decision_id,
            transaction_id=transaction_id,
            source_runtime_revision=source_revision,
            issued_at=issued_at,
            expires_at=issued_at + 120.0,
            plan=plan,
        )
        verified = self.authority_request(request, now=issued_at + 0.001)
        if not verified.allowed:
            raise AdoptionError("plugin_adoption_denied")
        record = _record_from_verified(verified, before=before, after=after)
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
        )
        if _states_digest(after) != verified.request["plan"]["after_state_digest"]:
            raise AdoptionError("after_state_plan_mismatch")
        return before, after

    def run(self) -> dict[str, object]:
        _lstat_admitted_directory(self.root, create=True)
        archived_transaction_id: str | None = None
        with _host_locks(self.root):
            if _journal_path(self.root).exists():
                record = _read_journal(self.root)
                verified = _reverify_journal(record)
                before, after = self._states_from_verified(verified, record)
            else:
                record, before, after = self._fresh_authorization()
                verified = _reverify_journal(record)
            phase = str(record["phase"])
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
                if not self.archive_rolled_back:
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
    parser.add_argument("action", choices=("apply", "resume", "status"))
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
    root = _fixed_state_root()
    try:
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
        if args.action == "resume" and not _journal_path(root).is_file():
            raise AdoptionError("journal_unavailable")
        previous = (
            _previous_committed_context()
            if _previous_context_required(args.action, root)
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
            )
            for index, host in enumerate(HOST_ORDER)
        )
        executor = PluginAdoptionExecutor(
            state_root=root,
            adapters=adapters,
            authority_request=authority.request_plugin_adoption_decision,
            archive_rolled_back=args.action == "apply",
            admitted_previous_states=previous_states,
        )
        result = executor.run()
    except (AdoptionError, authority.PluginAdoptionAuthorityError) as exc:
        print(json.dumps({"code": str(exc), "status": "failed"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
