#!/usr/bin/env python3
"""Admitted local source for the persistent ORCH dashboard session token.

The token is deliberately not an ``.env`` value.  The generic Hermes command
secret source is the sole process-startup consumer; this module only admits a
fixed command-source configuration and emits the one existing token to that
source's captured stdout.  Lifecycle code is the sole writer and must consume
an external Maestro decision before calling :func:`create_or_rotate_token`.

No caller supplied path, command, token, or diagnostic is reflected from this
module.  Failure is represented by a small stable code so a caller can stop
before any network operation without revealing which filesystem object failed.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import os
import re
import secrets
import shlex
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import yaml


SESSION_TOKEN_ENV = "HERMES_DASHBOARD_SESSION_TOKEN"
TOKEN_LEAF = "dashboard-session-token"
LOCK_LEAF = "dashboard-session-token.lock"
TOKEN_RELATIVE_PATH = "services/orch-next-serve/state/dashboard-session-token"
_STATE_COMPONENTS = ("services", "orch-next-serve", "state")
_CONFIG_LEAF = "config.yaml"
# One bounded, lifecycle-owner-managed slot for a captured generation that
# cannot safely be restored. It is never automatically overwritten or deleted.
_CONFIG_RECOVERY_LEAF = ".config.yaml.orch-recovery"
# One bounded slot for a generation retired from an operation-owned temp name.
# Pathname unlink is deliberately avoided because POSIX unlink has no inode CAS.
_CONFIG_RETIRED_LEAF = ".config.yaml.orch-retired"
_CONFIG_RETIRED_SECONDARY_LEAF = ".config.yaml.orch-retired-2"
_CONFIG_RETIRED_LEAVES = (
    _CONFIG_RETIRED_LEAF,
    _CONFIG_RETIRED_SECONDARY_LEAF,
)
_CONFIG_QUARANTINED_RECOVERY_LEAF = ".config.yaml.orch-quarantined-recovery"
_CONFIG_QUARANTINED_RETIRED_LEAF = ".config.yaml.orch-quarantined-retired"
_CONFIG_QUARANTINED_ACTIVE_LEAF = ".config.yaml.orch-quarantined-active"
# At one material runtime update every 2-3 days, 256 generations provide an
# explicit 1.4-2.1 year retention horizon. Once full, this source returns the
# typed ``session_token_config_recovery_destination_occupied`` result
# and leaves every generation intact; reclamation needs a separate exact
# cleanup authority because neither unlink nor overwrite has inode CAS.
_CONFIG_QUARANTINE_GENERATION_LIMIT = 256
_CONFIG_QUARANTINE_LEAVES = (
    _CONFIG_QUARANTINED_RECOVERY_LEAF,
    _CONFIG_QUARANTINED_RETIRED_LEAF,
    _CONFIG_QUARANTINED_ACTIVE_LEAF,
)
_CONFIG_MODE = 0o600
_DIRECTORY_MODE = 0o700
_TOKEN_MODE = 0o600
_HELPER_MODE = 0o644
_MAX_CONFIG_BYTES = 512 * 1024
_TOKEN_BYTES = 64
_TOKEN_PATTERN = re.compile(rb"[0-9a-f]{64}\Z")
_SOURCE_VERSION = 1
_RENAME_SWAP = 0x00000002
_RENAME_EXCL = 0x00000004
_RENAME_NOFOLLOW_ANY = 0x00000010
_RENAME_RESOLVE_BENEATH = 0x00000020


class SessionTokenUnavailable(RuntimeError):
    """A stable, value-free refusal to use the persistent token source."""

    def __init__(self, code: str = "session_token_unavailable") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TokenMaterial:
    """One descriptor-verified value bound to its content generation."""

    value: str
    generation: str


@dataclass(frozen=True)
class _SecretTemp:
    name: str
    device: int
    inode: int


@dataclass(frozen=True)
class _ConfigSnapshot:
    content: bytes
    device: int
    inode: int
    size: int
    ctime_ns: int
    mtime_ns: int


@dataclass(frozen=True)
class _ConfigTemp:
    name: str
    device: int
    inode: int


@dataclass(frozen=True)
class ConfigArtifactIdentity:
    """Credential-free identity for one protected config generation."""

    file_type: str
    uid: int
    mode: int
    device: int
    inode: int
    links: int


@dataclass(frozen=True)
class ConfigRecoveryResult:
    """Sanitized recovery outcome; never contains config content or hashes."""

    recovered: bool
    detail: str


@dataclass(frozen=True)
class _ConfigMove:
    source: str
    destination: str
    identity: ConfigArtifactIdentity


class _StrictConfigLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""

    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict:
        if not isinstance(node, yaml.MappingNode):
            _raise("session_token_config_rejected")
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError:
                _raise("session_token_config_rejected")
            if duplicate:
                _raise("session_token_config_rejected")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _raise(code: str) -> None:
    raise SessionTokenUnavailable(code)


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _file_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _require_absolute_path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        _raise("session_token_path_rejected")


def _require_directory(info: os.stat_result, *, exact_mode: int) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != exact_mode
    ):
        _raise("session_token_path_rejected")


def _require_regular(info: os.stat_result, *, exact_mode: int) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != exact_mode
        or info.st_nlink != 1
    ):
        _raise("session_token_source_rejected")


@contextmanager
def _open_absolute_directory(path: Path, *, exact_mode: int) -> Iterator[int]:
    """Open an absolute directory one no-follow component at a time."""

    _require_absolute_path(path)
    fd = os.open("/", _directory_flags())
    try:
        for component in path.parts[1:]:
            try:
                before = os.stat(component, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                    _raise("session_token_path_rejected")
                child = os.open(component, _directory_flags(), dir_fd=fd)
            except (FileNotFoundError, NotADirectoryError, OSError) as exc:
                if isinstance(exc, SessionTokenUnavailable):
                    raise
                _raise("session_token_path_rejected")
            opened = os.fstat(child)
            if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
                os.close(child)
                _raise("session_token_path_rejected")
            os.close(fd)
            fd = child
        _require_directory(os.fstat(fd), exact_mode=exact_mode)
        yield fd
    finally:
        os.close(fd)


def _open_child_directory(parent_fd: int, name: str) -> int:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            _raise("session_token_path_rejected")
        _require_directory(before, exact_mode=_DIRECTORY_MODE)
        child = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except SessionTokenUnavailable:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError):
        _raise("session_token_path_rejected")
    opened = os.fstat(child)
    try:
        _require_directory(opened, exact_mode=_DIRECTORY_MODE)
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            _raise("session_token_path_rejected")
        return child
    except Exception:
        os.close(child)
        raise


@contextmanager
def _open_state_directory_from_home(home_fd: int) -> Iterator[int]:
    """Open the state components from one already-admitted profile fd."""

    parent = home_fd
    opened: list[int] = []
    try:
        for component in _STATE_COMPONENTS:
            parent = _open_child_directory(parent, component)
            opened.append(parent)
        yield parent
    finally:
        for fd in reversed(opened):
            os.close(fd)


@contextmanager
def _open_state_directory(hermes_home: Path) -> Iterator[tuple[int, int, int]]:
    """Hold no-follow profile and state directory descriptors until completion."""

    with _open_absolute_directory(hermes_home, exact_mode=_DIRECTORY_MODE) as home_fd:
        home_info = os.fstat(home_fd)
        with _open_state_directory_from_home(home_fd) as state_fd:
            yield state_fd, home_info.st_dev, home_info.st_ino


def _path_still_matches(hermes_home: Path, *, device: int, inode: int) -> bool:
    try:
        _require_absolute_path(hermes_home)
        current = os.lstat(hermes_home)
        _require_directory(current, exact_mode=_DIRECTORY_MODE)
        return current.st_dev == device and current.st_ino == inode
    except (OSError, SessionTokenUnavailable):
        return False


def _read_regular_at(
    directory_fd: int,
    name: str,
    *,
    exact_mode: int,
    maximum_bytes: int,
    missing_code: str,
) -> tuple[bytes, os.stat_result]:
    """Read one owned no-follow regular file with before/after FD identity."""

    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        _raise(missing_code)
    except OSError:
        _raise("session_token_source_rejected")
    if stat.S_ISLNK(before.st_mode):
        _raise("session_token_source_rejected")
    _require_regular(before, exact_mode=exact_mode)
    if before.st_size > maximum_bytes:
        _raise("session_token_source_rejected")
    try:
        fd = os.open(name, _file_flags(), dir_fd=directory_fd)
    except OSError:
        _raise("session_token_source_rejected")
    try:
        opened = os.fstat(fd)
        _require_regular(opened, exact_mode=exact_mode)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_ctime_ns != before.st_ctime_ns
            or opened.st_mtime_ns != before.st_mtime_ns
        ):
            _raise("session_token_source_rejected")
        data = bytearray()
        while len(data) <= maximum_bytes:
            chunk = os.read(fd, min(65_536, maximum_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        _require_regular(after, exact_mode=exact_mode)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_ctime_ns != opened.st_ctime_ns
            or after.st_mtime_ns != opened.st_mtime_ns
            or len(data) != opened.st_size
        ):
            _raise("session_token_source_rejected")
        return bytes(data), after
    finally:
        os.close(fd)


def _helper_path() -> Path:
    return Path(__file__).absolute()


def _digest_regular_path(path: Path, *, expected_mode: int | None = None) -> str:
    """Digest a no-follow regular file without accepting path substitution."""

    _require_absolute_path(path)
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _raise("session_token_source_rejected")
        if before.st_uid != os.getuid() or (
            expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode
        ):
            _raise("session_token_source_rejected")
        fd = os.open(path, _file_flags())
    except SessionTokenUnavailable:
        raise
    except OSError:
        _raise("session_token_source_rejected")
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or (expected_mode is not None and stat.S_IMODE(opened.st_mode) != expected_mode)
        ):
            _raise("session_token_source_rejected")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
        if after.st_dev != opened.st_dev or after.st_ino != opened.st_ino:
            _raise("session_token_source_rejected")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _runtime_digest(runtime: Path) -> str:
    """Pin the actual executable bytes while allowing normal venv symlinks."""

    try:
        resolved = runtime.resolve(strict=True)
    except OSError:
        _raise("session_token_source_rejected")
    return _digest_regular_path(resolved)


def expected_command(runtime: Path | None = None) -> str:
    """The one command value an ORCH command source is allowed to execute."""

    executable = Path(runtime) if runtime is not None else Path(sys.executable)
    _require_absolute_path(executable)
    return f"{shlex.quote(str(executable))} {shlex.quote(str(_helper_path()))}"


def protected_command_config(runtime: Path | None = None) -> dict[str, object]:
    """Return the fixed command-source configuration shape for one runtime."""

    executable = Path(runtime) if runtime is not None else Path(sys.executable)
    return {
        "enabled": True,
        "command": expected_command(executable),
        "helper_timeout_seconds": 3.0,
        "override_existing": True,
        "orch_next_session_token": {
            "version": _SOURCE_VERSION,
            "helper_sha256": _digest_regular_path(
                _helper_path(), expected_mode=_HELPER_MODE
            ),
            "runtime_path": str(executable),
            "runtime_sha256": _runtime_digest(executable),
            "token_relative_path": TOKEN_RELATIVE_PATH,
        },
    }


def _parse_protected_config(raw: bytes) -> dict[str, object]:
    """Parse one mapping without aliases, anchors, or duplicate keys."""

    try:
        for event in yaml.parse(raw):
            if isinstance(event, yaml.events.AliasEvent) or getattr(
                event, "anchor", None
            ) is not None:
                _raise("session_token_config_rejected")
        parsed = yaml.load(raw, Loader=_StrictConfigLoader)
    except SessionTokenUnavailable:
        raise
    except Exception:
        _raise("session_token_config_rejected")
    if type(parsed) is not dict:
        _raise("session_token_config_rejected")
    return parsed


def _config_snapshot(content: bytes, info: os.stat_result) -> _ConfigSnapshot:
    return _ConfigSnapshot(
        content=content,
        device=info.st_dev,
        inode=info.st_ino,
        size=info.st_size,
        ctime_ns=info.st_ctime_ns,
        mtime_ns=info.st_mtime_ns,
    )


def _read_config_snapshot_at(home_fd: int, name: str) -> _ConfigSnapshot:
    raw, config_info = _read_regular_at(
        home_fd,
        name,
        exact_mode=_CONFIG_MODE,
        maximum_bytes=_MAX_CONFIG_BYTES,
        missing_code="session_token_config_missing",
    )
    return _config_snapshot(raw, config_info)


def _read_config_snapshot(home_fd: int) -> _ConfigSnapshot:
    return _read_config_snapshot_at(home_fd, _CONFIG_LEAF)


def _read_optional_config_snapshot(home_fd: int) -> _ConfigSnapshot | None:
    try:
        return _read_config_snapshot(home_fd)
    except SessionTokenUnavailable as exc:
        if exc.code == "session_token_config_missing":
            return None
        raise


def _require_config_recovery_slot_empty(home_fd: int) -> None:
    for name in (_CONFIG_RECOVERY_LEAF, *_CONFIG_RETIRED_LEAVES):
        try:
            os.stat(name, dir_fd=home_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            _raise("session_token_config_recovery_required")
        _raise("session_token_config_recovery_required")


def _read_protected_config(home_fd: int) -> dict[str, object]:
    return _parse_protected_config(_read_config_snapshot(home_fd).content)


def _config_snapshot_matches(
    home_fd: int,
    expected: _ConfigSnapshot | None,
) -> bool:
    """CAS predicate for the exact config generation observed before merge."""

    if expected is None:
        try:
            os.stat(_CONFIG_LEAF, dir_fd=home_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False
    try:
        return _read_config_snapshot(home_fd) == expected
    except (OSError, SessionTokenUnavailable):
        return False


def _same_config_generation(
    observed: _ConfigSnapshot,
    expected: _ConfigSnapshot,
) -> bool:
    """Compare one inode generation across a rename that updates ctime."""

    return (
        observed.content == expected.content
        and observed.device == expected.device
        and observed.inode == expected.inode
        and observed.size == expected.size
        and observed.mtime_ns == expected.mtime_ns
    )


def _config_generation_matches(home_fd: int, expected: _ConfigSnapshot) -> bool:
    try:
        return _same_config_generation(_read_config_snapshot(home_fd), expected)
    except (OSError, SessionTokenUnavailable):
        return False


def _write_config_temp(home_fd: int, content: bytes) -> _ConfigTemp:
    """Create one owner-private, fsynced config generation."""

    if not content or len(content) > _MAX_CONFIG_BYTES:
        _raise("session_token_config_rejected")
    name = f".{_CONFIG_LEAF}.{secrets.token_hex(16)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    temporary: _ConfigTemp | None = None
    complete = False
    try:
        fd = os.open(name, flags, _CONFIG_MODE, dir_fd=home_fd)
        os.fchmod(fd, _CONFIG_MODE)
        opened = os.fstat(fd)
        _require_regular(opened, exact_mode=_CONFIG_MODE)
        temporary = _ConfigTemp(name, opened.st_dev, opened.st_ino)
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                _raise("session_token_config_write_failed")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.fsync(home_fd)
        complete = True
    except SessionTokenUnavailable:
        raise
    except OSError:
        _raise("session_token_config_write_failed")
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None and not complete:
            _remove_config_temp(home_fd, temporary)
    if temporary is None:
        _raise("session_token_config_write_failed")
    return temporary


def _config_temp_matches(home_fd: int, temporary: _ConfigTemp) -> bool:
    try:
        observed = os.stat(
            temporary.name,
            dir_fd=home_fd,
            follow_symlinks=False,
        )
        _require_regular(observed, exact_mode=_CONFIG_MODE)
        return (
            observed.st_dev == temporary.device
            and observed.st_ino == temporary.inode
        )
    except (OSError, SessionTokenUnavailable):
        return False


def _remove_config_temp(home_fd: int, temporary: _ConfigTemp) -> bool:
    """Retire an operation temp atomically; never unlink a raced replacement."""

    if not _config_temp_matches(home_fd, temporary):
        return False
    for retired_name in _CONFIG_RETIRED_LEAVES:
        try:
            _atomic_config_rename(
                home_fd,
                temporary.name,
                retired_name,
                _RENAME_EXCL,
            )
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                continue
            return False
        except SessionTokenUnavailable:
            return False
        try:
            os.fsync(home_fd)
        except OSError:
            return False
        return _config_temp_matches(
            home_fd,
            _ConfigTemp(retired_name, temporary.device, temporary.inode),
        )
    return False


def _atomic_config_rename(
    home_fd: int,
    source: str,
    destination: str,
    operation: int,
) -> None:
    """Perform one descriptor-relative, no-follow Darwin rename operation."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx = libc.renameatx_np
        renameatx.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx.restype = ctypes.c_int
        flags = operation | _RENAME_NOFOLLOW_ANY | _RENAME_RESOLVE_BENEATH
        result = renameatx(
            home_fd,
            os.fsencode(source),
            home_fd,
            os.fsencode(destination),
            flags,
        )
    except (AttributeError, OSError):
        _raise("session_token_config_write_failed")
    if result != 0:
        failure = ctypes.get_errno()
        raise OSError(failure, os.strerror(failure))


def _config_file_type(info: os.stat_result) -> str:
    if stat.S_ISREG(info.st_mode):
        return "regular"
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    return "other"


def _config_artifact_identity_at(
    home_fd: int,
    name: str,
) -> ConfigArtifactIdentity | None:
    """Read metadata only; protected config bytes are never opened here."""

    try:
        info = os.stat(name, dir_fd=home_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        _raise("session_token_config_recovery_unavailable")
    return ConfigArtifactIdentity(
        file_type=_config_file_type(info),
        uid=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
        device=info.st_dev,
        inode=info.st_ino,
        links=info.st_nlink,
    )


def _admit_config_artifact_identity(
    identity: ConfigArtifactIdentity,
    *,
    home_device: int,
) -> None:
    if (
        type(identity) is not ConfigArtifactIdentity
        or identity.file_type != "regular"
        or type(identity.uid) is not int
        or identity.uid < 0
        or identity.uid != os.getuid()
        or type(identity.mode) is not int
        or identity.mode != _CONFIG_MODE
        or type(identity.device) is not int
        or identity.device <= 0
        or identity.device != home_device
        or type(identity.inode) is not int
        or identity.inode <= 0
        or type(identity.links) is not int
        or identity.links != 1
    ):
        _raise("session_token_config_recovery_identity_rejected")


def _require_config_artifact_identity(
    home_fd: int,
    name: str,
    expected: ConfigArtifactIdentity,
    *,
    home_device: int,
) -> ConfigArtifactIdentity:
    _admit_config_artifact_identity(expected, home_device=home_device)
    observed = _config_artifact_identity_at(home_fd, name)
    if observed is None:
        _raise("session_token_config_recovery_generation_missing")
    _admit_config_artifact_identity(observed, home_device=home_device)
    if observed != expected:
        _raise("session_token_config_recovery_identity_changed")
    return observed


def _require_config_artifact_absent(home_fd: int, name: str) -> None:
    if _config_artifact_identity_at(home_fd, name) is not None:
        _raise("session_token_config_recovery_destination_occupied")


def _config_quarantine_leaf(base: str, generation: int) -> str:
    """Return one fixed-family leaf without introducing an unbounded name."""

    if base not in _CONFIG_QUARANTINE_LEAVES or not (
        1 <= generation <= _CONFIG_QUARANTINE_GENERATION_LIMIT
    ):
        _raise("session_token_config_recovery_destination_rejected")
    return base if generation == 1 else f"{base}-{generation}"


def _select_config_quarantine_generation(home_fd: int) -> dict[str, str]:
    """Select one wholly empty bounded generation for a recovery transaction.

    All artifact classes share the generation number. Existing generations are
    metadata-only observations and are never opened, replaced, or removed.
    The later exclusive rename remains the mutation-time absence check.
    """

    for generation in range(1, _CONFIG_QUARANTINE_GENERATION_LIMIT + 1):
        destinations = {
            base: _config_quarantine_leaf(base, generation)
            for base in _CONFIG_QUARANTINE_LEAVES
        }
        if all(
            _config_artifact_identity_at(home_fd, destination) is None
            for destination in destinations.values()
        ):
            return destinations
    _raise("session_token_config_recovery_destination_occupied")


def _move_config_artifact(
    home_fd: int,
    source: str,
    destination: str,
    expected: ConfigArtifactIdentity,
    *,
    home_device: int,
    moves: list[_ConfigMove],
) -> None:
    _require_config_artifact_identity(
        home_fd,
        source,
        expected,
        home_device=home_device,
    )
    _require_config_artifact_absent(home_fd, destination)
    try:
        _atomic_config_rename(
            home_fd,
            source,
            destination,
            _RENAME_EXCL,
        )
    except (OSError, SessionTokenUnavailable):
        try:
            remaining = _config_artifact_identity_at(home_fd, source)
        except SessionTokenUnavailable:
            # The rename outcome is unknown. Journal only the expected identity
            # so rollback may restore it but can never move a replacement.
            moves.append(_ConfigMove(source, destination, expected))
            _raise("session_token_config_recovery_move_failed")
        if remaining != expected:
            moves.append(_ConfigMove(source, destination, expected))
        _raise("session_token_config_recovery_move_failed")

    # A successful rename call is journaled before any further observation.
    # Rollback itself insists that the destination still has this exact
    # identity, so a raced replacement is never moved into the protected slot.
    moves.append(_ConfigMove(source, destination, expected))
    moved = _config_artifact_identity_at(home_fd, destination)
    if moved != expected:
        _raise("session_token_config_recovery_move_failed")
    try:
        os.fsync(home_fd)
    except OSError:
        _raise("session_token_config_recovery_move_failed")
    if (
        _config_artifact_identity_at(home_fd, destination) != expected
        or _config_artifact_identity_at(home_fd, source) is not None
    ):
        _raise("session_token_config_recovery_identity_changed")


def _rollback_config_artifact_moves(
    home_fd: int,
    moves: list[_ConfigMove],
) -> bool:
    """Reverse only the exact generations moved by this operation."""

    for move in reversed(moves):
        try:
            if (
                _config_artifact_identity_at(home_fd, move.destination)
                != move.identity
                or _config_artifact_identity_at(home_fd, move.source) is not None
            ):
                return False
            _atomic_config_rename(
                home_fd,
                move.destination,
                move.source,
                _RENAME_EXCL,
            )
            os.fsync(home_fd)
            if (
                _config_artifact_identity_at(home_fd, move.source)
                != move.identity
                or _config_artifact_identity_at(home_fd, move.destination) is not None
            ):
                return False
        except (OSError, SessionTokenUnavailable):
            return False
    return True


def recover_protected_command_config(
    hermes_home: Path,
    *,
    recovery_identity: ConfigArtifactIdentity | None,
    recovery_disposition: str | None,
    retired_identity: ConfigArtifactIdentity | None,
    retired_disposition: str | None,
    active_identity: ConfigArtifactIdentity | None = None,
) -> ConfigRecoveryResult:
    """Resolve bounded recovery slots without reading protected config bytes.

    The lifecycle caller owns the exactly-one-writer lock. This function adds
    descriptor-relative generation checks, exclusive same-directory moves,
    and identity-bound rollback without logging, hashing, or returning values.
    """

    moves: list[_ConfigMove] = []
    try:
        if (recovery_identity is None) != (recovery_disposition is None):
            _raise("session_token_config_recovery_request_incomplete")
        if (retired_identity is None) != (retired_disposition is None):
            _raise("session_token_config_recovery_request_incomplete")
        if recovery_disposition not in {None, "preserve", "restore", "quarantine"}:
            _raise("session_token_config_recovery_disposition_rejected")
        if retired_disposition not in {None, "preserve", "quarantine"}:
            _raise("session_token_config_recovery_disposition_rejected")
        if recovery_disposition == "restore":
            if active_identity is None:
                _raise("session_token_config_recovery_active_identity_required")
        elif active_identity is not None:
            _raise("session_token_config_recovery_active_identity_unexpected")

        with _open_absolute_directory(
            hermes_home,
            exact_mode=_DIRECTORY_MODE,
        ) as home_fd:
            home_info = os.fstat(home_fd)
            recovery_observed = _config_artifact_identity_at(
                home_fd,
                _CONFIG_RECOVERY_LEAF,
            )
            retired_present = [
                name
                for name in _CONFIG_RETIRED_LEAVES
                if _config_artifact_identity_at(home_fd, name) is not None
            ]
            if len(retired_present) > 1:
                _raise("session_token_config_recovery_retired_ambiguous")
            retired_leaf = retired_present[0] if retired_present else None
            if recovery_observed is None and retired_leaf is None:
                _raise("session_token_config_recovery_generation_missing")
            if (recovery_observed is None) != (recovery_identity is None):
                _raise("session_token_config_recovery_occupancy_ambiguous")
            if (retired_leaf is None) != (retired_identity is None):
                _raise("session_token_config_recovery_occupancy_ambiguous")

            if recovery_identity is not None:
                _require_config_artifact_identity(
                    home_fd,
                    _CONFIG_RECOVERY_LEAF,
                    recovery_identity,
                    home_device=home_info.st_dev,
                )
            if retired_identity is not None and retired_leaf is not None:
                _require_config_artifact_identity(
                    home_fd,
                    retired_leaf,
                    retired_identity,
                    home_device=home_info.st_dev,
                )
            if active_identity is not None:
                _require_config_artifact_identity(
                    home_fd,
                    _CONFIG_LEAF,
                    active_identity,
                    home_device=home_info.st_dev,
                )

            quarantine_destinations: dict[str, str] = {}
            if "quarantine" in {recovery_disposition, retired_disposition} or (
                recovery_disposition == "restore"
            ):
                quarantine_destinations = _select_config_quarantine_generation(
                    home_fd
                )

            try:
                if (
                    retired_disposition == "quarantine"
                    and retired_identity is not None
                    and retired_leaf is not None
                ):
                    _move_config_artifact(
                        home_fd,
                        retired_leaf,
                        quarantine_destinations[_CONFIG_QUARANTINED_RETIRED_LEAF],
                        retired_identity,
                        home_device=home_info.st_dev,
                        moves=moves,
                    )
                if (
                    recovery_disposition == "quarantine"
                    and recovery_identity is not None
                ):
                    _move_config_artifact(
                        home_fd,
                        _CONFIG_RECOVERY_LEAF,
                        quarantine_destinations[_CONFIG_QUARANTINED_RECOVERY_LEAF],
                        recovery_identity,
                        home_device=home_info.st_dev,
                        moves=moves,
                    )
                elif (
                    recovery_disposition == "restore"
                    and recovery_identity is not None
                    and active_identity is not None
                ):
                    _move_config_artifact(
                        home_fd,
                        _CONFIG_LEAF,
                        quarantine_destinations[_CONFIG_QUARANTINED_ACTIVE_LEAF],
                        active_identity,
                        home_device=home_info.st_dev,
                        moves=moves,
                    )
                    _move_config_artifact(
                        home_fd,
                        _CONFIG_RECOVERY_LEAF,
                        _CONFIG_LEAF,
                        recovery_identity,
                        home_device=home_info.st_dev,
                        moves=moves,
                    )
                if not _path_still_matches(
                    hermes_home,
                    device=home_info.st_dev,
                    inode=home_info.st_ino,
                ):
                    _raise("session_token_config_recovery_path_changed")
                final_move_identities: dict[str, ConfigArtifactIdentity | None] = {}
                for move in moves:
                    final_move_identities[move.source] = None
                    final_move_identities[move.destination] = move.identity
                for name, expected_identity in final_move_identities.items():
                    if _config_artifact_identity_at(home_fd, name) != expected_identity:
                        _raise("session_token_config_recovery_identity_changed")
                if recovery_disposition == "preserve" and recovery_identity is not None:
                    _require_config_artifact_identity(
                        home_fd,
                        _CONFIG_RECOVERY_LEAF,
                        recovery_identity,
                        home_device=home_info.st_dev,
                    )
                if (
                    retired_disposition == "preserve"
                    and retired_identity is not None
                    and retired_leaf is not None
                ):
                    _require_config_artifact_identity(
                        home_fd,
                        retired_leaf,
                        retired_identity,
                        home_device=home_info.st_dev,
                    )
            except BaseException as exc:
                if not _rollback_config_artifact_moves(home_fd, moves):
                    return ConfigRecoveryResult(
                        False,
                        "session_token_config_recovery_rollback_failed",
                    )
                if not isinstance(exc, (OSError, SessionTokenUnavailable)):
                    raise
                detail = (
                    exc.code
                    if isinstance(exc, SessionTokenUnavailable)
                    else "session_token_config_recovery_move_failed"
                )
                return ConfigRecoveryResult(False, detail)

            if recovery_disposition == "restore":
                detail = "session_token_config_recovery_restored"
            elif "quarantine" in {recovery_disposition, retired_disposition}:
                detail = "session_token_config_recovery_quarantined"
            else:
                detail = "session_token_config_recovery_preserved"
            return ConfigRecoveryResult(True, detail)
    except (OSError, SessionTokenUnavailable) as exc:
        detail = (
            exc.code
            if isinstance(exc, SessionTokenUnavailable)
            else "session_token_config_recovery_unavailable"
        )
        return ConfigRecoveryResult(False, detail)


def _rename_config_exclusive(home_fd: int, temporary: _ConfigTemp) -> bool:
    """Install a missing config without ever replacing a competing writer."""

    try:
        _atomic_config_rename(
            home_fd,
            temporary.name,
            _CONFIG_LEAF,
            _RENAME_EXCL,
        )
        return True
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        _raise("session_token_config_write_failed")


def _exchange_config(home_fd: int, temporary_name: str) -> None:
    _atomic_config_rename(
        home_fd,
        temporary_name,
        _CONFIG_LEAF,
        _RENAME_SWAP,
    )


def _snapshot_at_matches(
    home_fd: int,
    name: str,
    expected: _ConfigSnapshot,
) -> bool:
    try:
        return _same_config_generation(
            _read_config_snapshot_at(home_fd, name),
            expected,
        )
    except (OSError, SessionTokenUnavailable):
        return False


def _preserve_snapshot_at(
    home_fd: int,
    name: str,
    expected: _ConfigSnapshot,
) -> bool:
    """Move a captured non-owned generation to the bounded recovery slot."""

    if not _snapshot_at_matches(home_fd, name, expected):
        return False
    try:
        _atomic_config_rename(
            home_fd,
            name,
            _CONFIG_RECOVERY_LEAF,
            _RENAME_EXCL,
        )
        os.fsync(home_fd)
        return _snapshot_at_matches(home_fd, _CONFIG_RECOVERY_LEAF, expected)
    except (OSError, SessionTokenUnavailable):
        return False


def _exchange_back_without_clobber(
    home_fd: int,
    temporary_name: str,
    desired: _ConfigSnapshot,
    installed: _ConfigTemp,
    installed_content: bytes,
) -> bool:
    """Restore a displaced generation while retaining any later writer.

    The exchange captures rather than deletes the current config.  If a writer
    wins the narrow pre-exchange window, a second exchange puts that captured
    newer generation back instead of overwriting it.
    """

    if not _snapshot_at_matches(home_fd, temporary_name, desired):
        return False
    if not _installed_config_matches(home_fd, installed, installed_content):
        return False
    try:
        _exchange_config(home_fd, temporary_name)
        displaced = _read_config_snapshot_at(home_fd, temporary_name)
        if (
            displaced.device == installed.device
            and displaced.inode == installed.inode
            and displaced.content == installed_content
        ):
            restored = _config_generation_matches(home_fd, desired)
            _remove_config_temp(
                home_fd,
                _ConfigTemp(
                    temporary_name,
                    displaced.device,
                    displaced.inode,
                ),
            )
            return restored

        # A newer writer landed after the pre-exchange identity check.  It is
        # now captured by the exchange; put it back without discarding it.
        if _config_generation_matches(home_fd, desired):
            _exchange_config(home_fd, temporary_name)
        else:
            _preserve_snapshot_at(home_fd, temporary_name, displaced)
        return False
    except (OSError, SessionTokenUnavailable):
        return False


def _installed_config_matches(
    home_fd: int,
    installed: _ConfigTemp,
    expected_content: bytes,
) -> bool:
    try:
        observed = _read_config_snapshot(home_fd)
        return (
            observed.device == installed.device
            and observed.inode == installed.inode
            and observed.content == expected_content
        )
    except (OSError, SessionTokenUnavailable):
        return False


def _remove_installed_config(
    home_fd: int,
    installed: _ConfigTemp,
    expected_content: bytes,
) -> bool:
    quarantine_name = f".{_CONFIG_LEAF}.{secrets.token_hex(16)}.tmp"
    try:
        _atomic_config_rename(
            home_fd,
            _CONFIG_LEAF,
            quarantine_name,
            _RENAME_EXCL,
        )
        moved = _read_config_snapshot_at(home_fd, quarantine_name)
        if (
            moved.device == installed.device
            and moved.inode == installed.inode
            and moved.content == expected_content
        ):
            return _remove_config_temp(
                home_fd,
                _ConfigTemp(quarantine_name, moved.device, moved.inode),
            )

        # A competing writer won before the move.  Restore it only if the
        # destination is still absent; never overwrite a still newer writer.
        try:
            _atomic_config_rename(
                home_fd,
                quarantine_name,
                _CONFIG_LEAF,
                _RENAME_EXCL,
            )
            return False
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                _preserve_snapshot_at(home_fd, quarantine_name, moved)
                return False
            raise
    except (OSError, SessionTokenUnavailable):
        return False


def _serialized_config(config: dict[str, object]) -> bytes:
    try:
        content = yaml.safe_dump(
            config,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).encode("utf-8")
    except Exception:
        _raise("session_token_config_rejected")
    if not content or len(content) > _MAX_CONFIG_BYTES:
        _raise("session_token_config_rejected")
    return content


def _matches_protected_command_config(
    command_cfg: object,
    *,
    runtime: Path,
) -> bool:
    if type(command_cfg) is not dict:
        return False
    expected = protected_command_config(runtime)
    marker = command_cfg.get("orch_next_session_token")
    if type(marker) is not dict or set(marker) != set(expected["orch_next_session_token"]):
        return False
    for key, value in expected.items():
        if command_cfg.get(key) != value:
            return False
    return True


def prepare_protected_command_config(
    hermes_home: Path,
    command_cfg: object,
    *,
    runtime: Path,
) -> bool:
    """Create or exactly merge the credential-free command-source config.

    Only ``secrets.command`` is replaced. Every unrelated parsed key/value is
    retained, and the public result is a stable boolean with no config or
    credential material returned to the caller.
    """

    try:
        executable = Path(runtime)
        if not _matches_protected_command_config(command_cfg, runtime=executable):
            return False
        with _open_absolute_directory(
            hermes_home,
            exact_mode=_DIRECTORY_MODE,
        ) as home_fd:
            home_info = os.fstat(home_fd)
            prior = _read_optional_config_snapshot(home_fd)
            parsed = {} if prior is None else _parse_protected_config(prior.content)
            if "secrets" in parsed:
                secrets_cfg = parsed["secrets"]
                if type(secrets_cfg) is not dict:
                    _raise("session_token_config_rejected")
                merged_secrets = dict(secrets_cfg)
            else:
                merged_secrets = {}
            persisted = merged_secrets.get("command")
            if persisted == command_cfg and _matches_protected_command_config(
                persisted,
                runtime=executable,
            ):
                return _path_still_matches(
                    hermes_home,
                    device=home_info.st_dev,
                    inode=home_info.st_ino,
                )
            _require_config_recovery_slot_empty(home_fd)

            merged = dict(parsed)
            merged_secrets["command"] = command_cfg
            merged["secrets"] = merged_secrets
            content = _serialized_config(merged)
            candidate: _ConfigTemp | None = None
            rollback: _ConfigTemp | None = None
            recovery: _ConfigSnapshot | None = None
            installed: _ConfigTemp | None = None
            try:
                if prior is not None:
                    rollback = _write_config_temp(home_fd, prior.content)
                candidate = _write_config_temp(home_fd, content)
                if (
                    not _path_still_matches(
                        hermes_home,
                        device=home_info.st_dev,
                        inode=home_info.st_ino,
                    )
                    or not _config_snapshot_matches(home_fd, prior)
                    or not _config_temp_matches(home_fd, candidate)
                ):
                    _raise("session_token_config_changed")
                installed = candidate
                if prior is None:
                    if not _rename_config_exclusive(home_fd, candidate):
                        _raise("session_token_config_changed")
                    candidate = None
                else:
                    _exchange_config(home_fd, candidate.name)
                    displaced = _read_config_snapshot_at(home_fd, candidate.name)
                    if not _same_config_generation(displaced, prior):
                        restored = _exchange_back_without_clobber(
                            home_fd,
                            candidate.name,
                            displaced,
                            installed,
                            content,
                        )
                        if not restored:
                            _preserve_snapshot_at(
                                home_fd,
                                candidate.name,
                                displaced,
                            )
                        _raise("session_token_config_changed")
                    if not _preserve_snapshot_at(home_fd, candidate.name, displaced):
                        _raise("session_token_config_write_failed")
                    recovery = displaced
                    candidate = None
                os.fsync(home_fd)
                committed = _read_config_snapshot(home_fd)
                if committed.content != content:
                    _raise("session_token_config_write_failed")
                _validate_admitted_command_config(
                    home_fd,
                    command_cfg,
                    runtime=executable,
                )
                if not _path_still_matches(
                    hermes_home,
                    device=home_info.st_dev,
                    inode=home_info.st_ino,
                ):
                    _raise("session_token_path_rejected")
                if rollback is not None:
                    if not _remove_config_temp(home_fd, rollback):
                        _raise("session_token_config_write_failed")
                    rollback = None
                return True
            except (OSError, SessionTokenUnavailable):
                if installed is not None:
                    if prior is not None and recovery is not None:
                        if _exchange_back_without_clobber(
                            home_fd,
                            _CONFIG_RECOVERY_LEAF,
                            recovery,
                            installed,
                            content,
                        ):
                            recovery = None
                    elif prior is not None and rollback is not None:
                        try:
                            desired = _read_config_snapshot_at(
                                home_fd,
                                rollback.name,
                            )
                        except (OSError, SessionTokenUnavailable):
                            desired = None
                        if desired is not None and desired.content == prior.content:
                            if _exchange_back_without_clobber(
                                home_fd,
                                rollback.name,
                                desired,
                                installed,
                                content,
                            ):
                                rollback = None
                    elif prior is None:
                        _remove_installed_config(home_fd, installed, content)
                if candidate is not None:
                    _remove_config_temp(home_fd, candidate)
                if rollback is not None:
                    _remove_config_temp(home_fd, rollback)
                return False
    except (OSError, SessionTokenUnavailable):
        return False


def _runtime_from_marker(command_cfg: object) -> Path:
    if type(command_cfg) is not dict:
        _raise("session_token_source_rejected")
    marker = command_cfg.get("orch_next_session_token")
    if type(marker) is not dict:
        _raise("session_token_source_rejected")
    raw_runtime = marker.get("runtime_path")
    if type(raw_runtime) is not str:
        _raise("session_token_source_rejected")
    runtime = Path(raw_runtime)
    _require_absolute_path(runtime)
    return runtime


def _validate_admitted_command_config(
    home_fd: int,
    command_cfg: object,
    *,
    runtime: Path | None,
) -> None:
    executable = Path(runtime) if runtime is not None else _runtime_from_marker(command_cfg)
    parsed = _read_protected_config(home_fd)
    secrets_cfg = parsed.get("secrets")
    if type(secrets_cfg) is not dict:
        _raise("session_token_config_rejected")
    persisted = secrets_cfg.get("command")
    if persisted != command_cfg or not _matches_protected_command_config(
        persisted,
        runtime=executable,
    ):
        _raise("session_token_config_rejected")


def command_source_is_admitted(
    hermes_home: Path,
    command_cfg: object,
    *,
    runtime: Path | None = None,
) -> bool:
    """True only for the fixed ORCH command source before a shell is spawned."""

    try:
        with _open_absolute_directory(hermes_home, exact_mode=_DIRECTORY_MODE) as home_fd:
            home_info = os.fstat(home_fd)
            _validate_admitted_command_config(
                home_fd,
                command_cfg,
                runtime=runtime,
            )
            return _path_still_matches(
                hermes_home, device=home_info.st_dev, inode=home_info.st_ino
            )
    except (OSError, SessionTokenUnavailable):
        return False


def configured_command_source_is_admitted(
    hermes_home: Path,
    *,
    runtime: Path | None = None,
) -> bool:
    """Re-admit the persisted command source for a non-shell consumer."""

    try:
        with _open_absolute_directory(hermes_home, exact_mode=_DIRECTORY_MODE) as home_fd:
            parsed = _read_protected_config(home_fd)
        secrets_cfg = parsed.get("secrets")
        command_cfg = secrets_cfg.get("command") if type(secrets_cfg) is dict else None
        return command_source_is_admitted(
            hermes_home,
            command_cfg,
            runtime=runtime,
        )
    except (OSError, SessionTokenUnavailable):
        return False


def _consume_admitted_material_from_home_fd(
    hermes_home: Path,
    home_fd: int,
    home_info: os.stat_result,
    command_cfg: object,
    *,
    runtime: Path | None,
) -> TokenMaterial:
    """Read one token after one config admission, without a path re-open."""

    _validate_admitted_command_config(home_fd, command_cfg, runtime=runtime)
    with _open_state_directory_from_home(home_fd) as state_fd:
        lock_fd = _open_source_lock(state_fd)
        try:
            material = _read_token_at(state_fd)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    if not _path_still_matches(
        hermes_home,
        device=home_info.st_dev,
        inode=home_info.st_ino,
    ):
        _raise("session_token_path_rejected")
    return material


def consume_admitted_session_token(
    hermes_home: Path,
    command_cfg: object,
    *,
    runtime: Path | None = None,
) -> TokenMaterial:
    """In-process protected-source fetch; it never executes the configured shell."""

    with _open_absolute_directory(hermes_home, exact_mode=_DIRECTORY_MODE) as home_fd:
        return _consume_admitted_material_from_home_fd(
            hermes_home,
            home_fd,
            os.fstat(home_fd),
            command_cfg,
            runtime=runtime,
        )


def consume_configured_session_token(hermes_home: Path) -> TokenMaterial:
    """Consume the persisted protected source and return its value/generation."""

    with _open_absolute_directory(hermes_home, exact_mode=_DIRECTORY_MODE) as home_fd:
        parsed = _read_protected_config(home_fd)
        secrets_cfg = parsed.get("secrets")
        command_cfg = secrets_cfg.get("command") if type(secrets_cfg) is dict else None
        return _consume_admitted_material_from_home_fd(
            hermes_home,
            home_fd,
            os.fstat(home_fd),
            command_cfg,
            runtime=None,
        )


def _open_source_lock(state_fd: int) -> int:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(LOCK_LEAF, flags, _TOKEN_MODE, dir_fd=state_fd)
        os.fchmod(fd, _TOKEN_MODE)
        _require_regular(os.fstat(fd), exact_mode=_TOKEN_MODE)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except SessionTokenUnavailable:
        raise
    except OSError:
        _raise("session_token_source_rejected")


@contextmanager
def _locked_state(hermes_home: Path) -> Iterator[tuple[int, int, int]]:
    with _open_state_directory(hermes_home) as (state_fd, home_device, home_inode):
        lock_fd = _open_source_lock(state_fd)
        try:
            yield state_fd, home_device, home_inode
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


def _read_token_at(state_fd: int) -> TokenMaterial:
    raw, _token_info = _read_regular_at(
        state_fd,
        TOKEN_LEAF,
        exact_mode=_TOKEN_MODE,
        maximum_bytes=_TOKEN_BYTES,
        missing_code="session_token_missing",
    )
    if _TOKEN_PATTERN.fullmatch(raw) is None:
        _raise("session_token_malformed")
    return TokenMaterial(
        value=raw.decode("ascii"),
        generation=hashlib.sha256(raw).hexdigest(),
    )


def load_session_token(hermes_home: Path) -> str:
    """Load an already-created token; never creates, rotates, or logs it."""

    with _locked_state(hermes_home) as (state_fd, home_device, home_inode):
        material = _read_token_at(state_fd)
        if not _path_still_matches(hermes_home, device=home_device, inode=home_inode):
            _raise("session_token_path_rejected")
        return material.value


def _write_all(fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            _raise("session_token_write_failed")
        offset += written


def _write_secret_temp(state_fd: int, content: bytes) -> _SecretTemp:
    """Create one fsynced private temp whose later cleanup is identity-bound."""

    temporary = f".{TOKEN_LEAF}.{secrets.token_hex(16)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    admitted: _SecretTemp | None = None
    completed = False
    try:
        fd = os.open(temporary, flags, _TOKEN_MODE, dir_fd=state_fd)
        opened = os.fstat(fd)
        _require_regular(opened, exact_mode=_TOKEN_MODE)
        admitted = _SecretTemp(temporary, opened.st_dev, opened.st_ino)
        os.fchmod(fd, _TOKEN_MODE)
        _write_all(fd, content)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.fsync(state_fd)
        completed = True
    except SessionTokenUnavailable:
        raise
    except OSError:
        _raise("session_token_write_failed")
    finally:
        if fd >= 0:
            os.close(fd)
        if admitted is not None and not completed:
            _remove_secret_temp(state_fd, admitted)
    if admitted is None:
        _raise("session_token_write_failed")
    return admitted


def _remove_secret_temp(state_fd: int, temporary: _SecretTemp) -> bool:
    """Unlink one exact secret temp; once unlinked its commit is conclusive.

    A directory ``fsync`` is still attempted to persist the unlink. If that
    final persistence step fails after a successful unlink, the live process
    has no backup left to restore and must regard the replacement as committed
    rather than report a failed rotation with a new token still installed.
    """

    try:
        observed = os.stat(temporary.name, dir_fd=state_fd, follow_symlinks=False)
        _require_regular(observed, exact_mode=_TOKEN_MODE)
        if (
            observed.st_dev != temporary.device
            or observed.st_ino != temporary.inode
        ):
            return False
        os.unlink(temporary.name, dir_fd=state_fd)
    except (OSError, SessionTokenUnavailable):
        return False
    try:
        os.fsync(state_fd)
    except OSError:
        # The unlink already removed the private old-generation file. Do not
        # turn that irreversible cleanup into a false failed rotation.
        pass
    return True


def _remove_token_generation(state_fd: int, material: TokenMaterial) -> bool:
    """Remove a just-created generation only after re-reading it by FD."""

    try:
        observed = _read_token_at(state_fd)
        if observed != material:
            return False
        os.unlink(TOKEN_LEAF, dir_fd=state_fd)
        os.fsync(state_fd)
        return True
    except (OSError, SessionTokenUnavailable):
        return False


def _restore_previous_token(
    state_fd: int,
    backup: _SecretTemp,
    previous: TokenMaterial,
) -> bool:
    """Restore the previous content generation and durably consume its backup."""

    try:
        os.rename(backup.name, TOKEN_LEAF, src_dir_fd=state_fd, dst_dir_fd=state_fd)
        os.fsync(state_fd)
        return _read_token_at(state_fd) == previous
    except (OSError, SessionTokenUnavailable):
        return False


def _atomic_replace_token(
    state_fd: int,
    content: bytes,
    *,
    previous: TokenMaterial | None,
    post_commit_check: Callable[[], bool] | None = None,
) -> TokenMaterial:
    """Commit one generation or restore/remove it after every later failure.

    ``post_commit_check`` covers root/path identity which depends on the
    caller's retained profile descriptor. It runs before the old generation
    backup is removed, so a failed check can still restore the prior token.
    """

    candidate: _SecretTemp | None = None
    backup: _SecretTemp | None = None
    replacement: TokenMaterial | None = None
    expected = TokenMaterial(
        value=content.decode("ascii"),
        generation=hashlib.sha256(content).hexdigest(),
    )
    replaced = False
    try:
        if previous is not None:
            backup = _write_secret_temp(state_fd, previous.value.encode("ascii"))
        candidate = _write_secret_temp(state_fd, content)
        os.rename(candidate.name, TOKEN_LEAF, src_dir_fd=state_fd, dst_dir_fd=state_fd)
        candidate = None
        replaced = True
        os.fsync(state_fd)
        replacement = _read_token_at(state_fd)
        if replacement != expected:
            _raise("session_token_write_failed")
        if post_commit_check is not None and not post_commit_check():
            _raise("session_token_path_rejected")
        if backup is not None:
            if not _remove_secret_temp(state_fd, backup):
                _raise("session_token_write_failed")
            backup = None
        return replacement
    except (OSError, SessionTokenUnavailable):
        if replaced:
            if previous is not None and backup is not None:
                if _restore_previous_token(state_fd, backup, previous):
                    backup = None
            elif previous is None:
                _remove_token_generation(state_fd, expected)
        if candidate is not None:
            _remove_secret_temp(state_fd, candidate)
        if backup is not None:
            _remove_secret_temp(state_fd, backup)
        _raise("session_token_write_failed")


def create_or_rotate_token(hermes_home: Path, *, rotate: bool) -> str:
    """Mutate the source only after the lifecycle caller consumed authority."""

    with _locked_state(hermes_home) as (state_fd, home_device, home_inode):
        try:
            existing = _read_token_at(state_fd)
        except SessionTokenUnavailable as exc:
            if exc.code != "session_token_missing":
                raise
            existing = None
        if existing is not None and not rotate:
            return existing.value
        generated = secrets.token_hex(32).encode("ascii")
        token = _atomic_replace_token(
            state_fd,
            generated,
            previous=existing,
            post_commit_check=lambda: _path_still_matches(
                hermes_home,
                device=home_device,
                inode=home_inode,
            ),
        )
        return token.value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.parse_args(argv)
    try:
        raw_home = os.environ.get("HERMES_HOME", "")
        home = Path(raw_home)
        material = consume_configured_session_token(home)
    except (OSError, SessionTokenUnavailable):
        return 1
    sys.stdout.write(f"{SESSION_TOKEN_ENV}={material.value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
