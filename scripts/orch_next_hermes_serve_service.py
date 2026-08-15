#!/usr/bin/env python3
"""Credential-free launchd packaging for the ORCH-Next Hermes serve runtime.

This module manages only the macOS service definition.  The launched process is
the existing ``hermes serve`` entrypoint; no alternate server or runtime is
introduced.  Lifecycle helpers accept an injected subprocess runner so behavior
can be verified without touching launchd.

The operational CLI is admitted only through the exact sibling launcher, which
starts this direct file under a fresh isolated, no-site interpreter. Bare
``python`` and ``python -m`` execution are typed unavailable surfaces.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import importlib
import importlib.machinery
import json
import os
import plistlib
import re
import secrets
import select
import signal
import stat
import subprocess
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator, Sequence


_ADMITTED_VENV_SITE_VERSION = "python3.11"


def _bind_admitted_checkout_import_root() -> Path:
    """Bind isolated dependency loading to this checkout and its exact venv."""

    source_root = Path(__file__).resolve(strict=True).parents[1]
    if not (sys.flags.isolated == 1 and sys.flags.no_site == 1):
        return source_root
    base_root = Path(sys.base_prefix).resolve(strict=True)
    venv_site = (
        source_root / ".venv" / "lib" / _ADMITTED_VENV_SITE_VERSION / "site-packages"
    ).resolve(strict=True)
    retained: list[str] = []
    for raw in sys.path:
        if not raw or not Path(raw).is_absolute():
            continue
        try:
            resolved = Path(raw).resolve()
        except OSError:
            continue
        if resolved == source_root:
            continue
        if resolved.is_relative_to(base_root):
            retained.append(str(resolved))
    sys.path[:] = [str(source_root), *retained, str(venv_site)]
    return source_root


_ADMITTED_CHECKOUT_ROOT = _bind_admitted_checkout_import_root()
_ISOLATED_LAUNCHER_RUNTIME = sys.flags.isolated == 1 and sys.flags.no_site == 1
_BARE_MODULE_ENTRYPOINT = __name__ == "__main__" and __spec__ is not None
_LIFECYCLE_SOURCE_LOCK_ENV = "ORCH_LIFECYCLE_SOURCE_LOCK_FD"
_LIFECYCLE_CONTROLLER_PATH = "scripts/orch_next_hermes_mcp_launcher.py"
_LIFECYCLE_CONTROLLER_SHA256 = (
    "98085796d88a677b6e430a8395621924c109193fc437e2e05b1128e35250cd74"
)


DEFAULT_LABEL = "com.orchnext.hermes.serve"
ORCH_SIDECAR_LABEL = "com.orchnext.hermes.serve.orch"
DEFAULT_HOST = "127.0.0.1"
ORCH_SIDECAR_HOST = DEFAULT_HOST
DEFAULT_PORT = 3517
ORCH_SIDECAR_PORT = 3518
ORCH_SIDECAR_ENV = "HERMES_ORCH_SIDECAR"
SERVICE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ENV_PATH = "/usr/bin/env"
LAUNCHCTL_PATH = "/bin/launchctl"
QUALIFIED_LAUNCHCTL_SHA256 = (
    "b1f2b90f349938cc4c3c9234f11cefd05545f7b4bfe9b1751ac01f1cb27d3714"
)
QUALIFIED_LAUNCHCTL_OS = "macOS 26.5.2 build 25F84"
# This unprivileged user service can contain concurrent filesystem mutation and
# refuse a false LOADED result after launchd registers mismatched execution
# fields. It cannot isolate the account from a deliberately malicious process
# already running as the same UID; that requires a privileged/system-owned
# consumption boundary and remains outside this credential-free helper.
SAME_UID_EXECUTION_ISOLATION = "requires_privileged_system_owned_boundary"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
BOOTSTRAP_STAGE_MODE = 0o400
LAUNCHD_TIMEOUT_SECONDS = 30
STOP_CONFIRM_ATTEMPTS = 51
STOP_CONFIRM_INTERVAL_SECONDS = 0.1
_NATIVE_SELECT = select.select
# launchctl uses 3/113 when the requested job is already absent.  Broader
# errors (including permission/domain failures) must preserve the plist.
_BOOTOUT_ACCEPTABLE_RETURN_CODES = frozenset({0, 3, 113})
# Extracted from hermes_cli.gateway._LAUNCHD_JOB_UNLOADED_EXIT_CODES.  Code 125
# is safe for start/restart/status re-bootstrap classification, but not for a
# destructive uninstall because it can also mean an unsupported domain.
_RETRY_NOT_LOADED_RETURN_CODES = frozenset({3, 113, 125})
_STATUS_NOT_LOADED_RETURN_CODES = frozenset({3, 113})
_BOOTSTRAP_STALE_REGISTRATION_RETURN_CODE = 5
# sysexits.EX_NOINPUT: launchctl could not open the descriptor-backed plist.
# This is a typed availability boundary; never retry through the canonical path.
_BOOTSTRAP_DESCRIPTOR_UNAVAILABLE_RETURN_CODES = frozenset({66})
_PID_RE = re.compile(r"^\tpid\s*=\s*(\d+)\s*$", re.MULTILINE)
_LAUNCHD_DISABLED_MAX_CHARS = 65_536
_LAUNCHD_DISABLED_HEADER = "disabled services = {"
_LAUNCHD_DISABLED_ENTRY_RE = re.compile(
    r'^\s*"(?P<label>[^"\r\n]{1,255})"\s*=>\s*'
    r"(?P<value>enabled|disabled)\s*;?\s*$"
)
_LIFECYCLE_LOCK_NAME = "lifecycle.lock"
_DARWIN_MAX_SIGNAL_NUMBER = 31

# ``launchctl print`` is a human-oriented grammar.  These keys are the bounded
# diagnostic/configuration projection observed for the pinned launchctl binary
# and classified as non-triggering.  Unknown top-level keys fail closed: a new
# OS grammar must be reviewed and re-pinned before it can authorize LOADED.
_LAUNCHD_PRINT_ALLOWED_SCALARS = frozenset({
    "active count",
    "asid",
    "checked allocations",
    "checked allocations flags",
    "checked allocations reason",
    "cpumon",
    "domain",
    "execs",
    "exit timeout",
    "forks",
    "immediate reason",
    "initialized",
    "jetsam memory limit (active)",
    "jetsam memory limit (inactive)",
    "jetsam priority",
    "jetsam thread limit",
    "jetsamproperties category",
    "last exit code",
    "last terminating signal",
    "minimum runtime",
    "path",
    "pid",
    "program",
    "properties",
    "proxy started suspended",
    "runs",
    "spawn type",
    "started suspended",
    "state",
    "stderr path",
    "stdout path",
    "trampolined",
    "type",
    "umask",
    "working directory",
})
_LAUNCHD_PRINT_ALLOWED_SECTIONS = frozenset({
    "arguments",
    "default environment",
    "environment",
    "inherited environment",
    "jetsam coalition",
    "resource coalition",
})
_LAUNCHD_PRINT_REQUIRED_PROPERTIES = frozenset({
    "has LWCR",
    "inferred program",
    "keepalive",
    "managed LWCR",
    "runatload",
})
# Explicit unprivileged ``launchctl bootstrap`` does not receive launchd's
# managed LWCR spawn constraint or an ownership marker in the printed property
# set.  This exact set is admitted only when the separately printed source path
# is the unique private bootstrap stage owned by this helper, and only inside
# SAME_UID_EXECUTION_ISOLATION's documented boundary.
_LAUNCHD_PRINT_CLI_PROPERTIES = frozenset({
    "inferred program",
    "keepalive",
    "runatload",
})
_LAUNCHD_PRINT_ALLOWED_PROPERTY_SETS = frozenset({
    _LAUNCHD_PRINT_REQUIRED_PROPERTIES,
    _LAUNCHD_PRINT_CLI_PROPERTIES,
})


class ConfigurationError(ValueError):
    """Raised when a service identity is not pinned to safe absolute paths."""


class LifecycleBusyError(RuntimeError):
    """Raised when another process owns the service lifecycle mutation lock."""


class AtomicWriteError(OSError):
    """Carries admitted identities after an interrupted atomic write."""

    def __init__(
        self,
        candidate: _PlistSnapshot | None = None,
        recovery_record: RecoveryRecord | None = None,
    ) -> None:
        super().__init__("atomic plist write failed")
        self.candidate = candidate
        self.recovery_record = recovery_record


def _admit_inherited_source_lock() -> tuple[int, Path] | None:
    """Retain the controller-owned distribution writer exclusion through exit."""

    raw = os.environ.get(_LIFECYCLE_SOURCE_LOCK_ENV)
    if raw is None or re.fullmatch(r"[0-9]+", raw) is None:
        return None
    descriptor = int(raw)
    if descriptor < 3:
        return None
    bundle = _ADMITTED_CHECKOUT_ROOT / "distribution" / "orch-next-hermes-harness"
    lock_path = bundle.parent / f".{bundle.name}.distribution.lock"
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = lock_path.lstat()
        os.lseek(descriptor, 0, os.SEEK_SET)
        marker = os.read(descriptor, 64)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        return None
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or descriptor_stat.st_uid != os.getuid()
        or descriptor_stat.st_nlink != 1
        or stat.S_IMODE(descriptor_stat.st_mode) != 0o600
        or descriptor_stat.st_dev != path_stat.st_dev
        or descriptor_stat.st_ino != path_stat.st_ino
        or marker != f"pid={os.getpid()}\n".encode("ascii")
    ):
        return None
    return descriptor, lock_path


def _release_inherited_source_lock(admission: tuple[int, Path]) -> None:
    descriptor, lock_path = admission
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = lock_path.lstat()
        if (
            descriptor_stat.st_dev == path_stat.st_dev
            and descriptor_stat.st_ino == path_stat.st_ino
        ):
            lock_path.unlink()
    except OSError:
        pass
    try:
        os.close(descriptor)
    except OSError:
        pass


def _load_lifecycle_controller_snapshot() -> types.ModuleType:
    """Execute one service-pinned controller snapshot without a path reopen."""

    path = _ADMITTED_CHECKOUT_ROOT / _LIFECYCLE_CONTROLLER_PATH
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("lifecycle authority controller unavailable") from exc
    try:
        before = os.fstat(descriptor)
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    source = b"".join(chunks)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or hashlib.sha256(source).hexdigest() != _LIFECYCLE_CONTROLLER_SHA256
    ):
        raise RuntimeError("lifecycle authority controller unavailable")
    module = types.ModuleType("_orch_lifecycle_authority_controller")
    module.__file__ = str(path)
    module.__package__ = None
    module.__spec__ = None
    code = compile(source, str(path), "exec", dont_inherit=True)
    exec(code, module.__dict__)
    return module


def _consume_lifecycle_runtime_authority(authority_home: Path) -> bool:
    """Consume a fresh signed current-tuple decision inside the service process."""

    try:
        controller = _load_lifecycle_controller_snapshot()
        controller._consume_runtime_provenance_authority(
            str(
                (
                    _ADMITTED_CHECKOUT_ROOT
                    / "distribution"
                    / controller.PLUGIN_ID
                    / controller.SOURCE_MANIFEST_NAME
                ).resolve(strict=True)
            ),
            source_root=_ADMITTED_CHECKOUT_ROOT,
            authority_home=authority_home,
        )
    except BaseException:
        return False
    finally:
        sys.modules.pop("tui_gateway.maestro_authority", None)
    return True


def _passwd_account_home() -> Path:
    import pwd

    return Path(str(pwd.getpwuid(os.getuid()).pw_dir))


def _admit_stable_account_home(path: Path, *, expected_uid: int) -> os.stat_result:
    home_info = os.lstat(path)
    if stat.S_ISLNK(home_info.st_mode) or not stat.S_ISDIR(home_info.st_mode):
        raise ConfigurationError("passwd account home must be a real directory")
    if home_info.st_uid != expected_uid:
        raise ConfigurationError("passwd account home must be owned by current account")
    parent_info = os.lstat(path.parent)
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise ConfigurationError("passwd account home parent must be a real directory")
    parent_mode = stat.S_IMODE(parent_info.st_mode)
    if (
        parent_mode & 0o022
        or (parent_info.st_uid == expected_uid and parent_mode & 0o200)
        or os.access(path.parent, os.W_OK)
    ):
        raise ConfigurationError("passwd account home parent is account-replaceable")
    return home_info


def _require_owned_private_directory(
    path: Path, *, expected_uid: int
) -> os.stat_result:
    try:
        info = os.lstat(path)
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"{path.name or 'directory'} must already exist"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ConfigurationError(f"{path.name or 'directory'} must be a real directory")
    if info.st_uid != expected_uid:
        raise ConfigurationError(
            f"{path.name or 'directory'} must be owned by current account"
        )
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigurationError(f"{path.name or 'directory'} must be owner-private")
    return info


class ServiceState(str, Enum):
    """Stable machine-readable lifecycle states."""

    PLANNED = "planned"
    INSTALLED = "installed"
    REMOVED = "removed"
    REMOVED_QUARANTINED = "removed_quarantined"
    RUNNING = "running"
    LOADED = "loaded"
    STOPPED = "stopped"
    NOT_INSTALLED = "not_installed"
    RECOVERED = "recovered"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ServiceRole(str, Enum):
    """The only admitted Hermes serve roles."""

    PRIMARY = "primary"
    ORCH_SIDECAR = "orch-sidecar"


class RecoveryArtifactKind(str, Enum):
    """The only two recovery artifact classes exposed to callers."""

    RESTORABLE_PLIST = "restorable_plist"
    PARTIAL_ATOMIC_TEMP = "partial_atomic_temp"


@dataclass(frozen=True)
class RecoveryRecord:
    leaf: str
    device: int
    inode: int
    sha256: str
    mode: int
    expected_label: str
    artifact_kind: RecoveryArtifactKind
    label_validated: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "leaf": self.leaf,
            "device": self.device,
            "inode": self.inode,
            "sha256": self.sha256,
            "mode": self.mode,
            "expected_label": self.expected_label,
            "artifact_kind": self.artifact_kind.value,
            "label_validated": self.label_validated,
        }


@dataclass(frozen=True)
class ServiceConfig:
    """Pinned paths and non-secret settings for one Hermes serve service."""

    worktree: Path
    runtime: Path
    python: Path
    hermes_home: Path
    port: int = DEFAULT_PORT
    label: str = DEFAULT_LABEL
    role: ServiceRole | str = ServiceRole.PRIMARY
    host: str = DEFAULT_HOST
    _hermes_home_device: int = field(init=False, repr=False)
    _hermes_home_inode: int = field(init=False, repr=False)
    _account_home: Path = field(init=False, repr=False)
    _account_home_device: int = field(init=False, repr=False)
    _account_home_inode: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            role = ServiceRole(self.role)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("service role is not admitted") from exc
        object.__setattr__(self, "role", role)

        for field_name in ("worktree", "runtime", "python", "hermes_home"):
            value = getattr(self, field_name)
            if not isinstance(value, Path):
                raise ConfigurationError(f"{field_name} must be a pathlib.Path")
            if not value.is_absolute():
                raise ConfigurationError(f"{field_name} must be an absolute path")

        if not self.worktree.is_dir():
            raise ConfigurationError("worktree must be an existing directory")
        if not self.runtime.is_file():
            raise ConfigurationError("runtime must be an existing file")
        if not self.python.is_file():
            raise ConfigurationError("python must be an existing file")
        if not os.access(self.python, os.X_OK):
            raise ConfigurationError("python must be executable")
        home_info = _require_owned_private_directory(
            self.hermes_home, expected_uid=os.getuid()
        )
        object.__setattr__(self, "_hermes_home_device", home_info.st_dev)
        object.__setattr__(self, "_hermes_home_inode", home_info.st_ino)
        account_home = _passwd_account_home()
        account_info = _admit_stable_account_home(
            account_home, expected_uid=os.getuid()
        )
        try:
            resolved_home = self.hermes_home.resolve(strict=True)
        except OSError as exc:
            raise ConfigurationError("hermes_home must be canonical") from exc
        if resolved_home != self.hermes_home:
            raise ConfigurationError("hermes_home must be canonical")
        object.__setattr__(self, "_account_home", account_home)
        object.__setattr__(self, "_account_home_device", account_info.st_dev)
        object.__setattr__(self, "_account_home_inode", account_info.st_ino)
        try:
            self.runtime.resolve(strict=True).relative_to(
                self.worktree.resolve(strict=True)
            )
        except ValueError as exc:
            raise ConfigurationError("runtime must be contained by worktree") from exc
        expected_runtime = self.worktree / ".venv" / "bin" / "hermes"
        if self.runtime != expected_runtime:
            raise ConfigurationError(
                "runtime must be the worktree Hermes console entrypoint"
            )
        expected_python = self.worktree / ".venv" / "bin" / "python"
        if self.python != expected_python:
            raise ConfigurationError(
                "python must be the worktree virtualenv interpreter"
            )
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("port must be between 1 and 65535")
        if self.host != DEFAULT_HOST:
            raise ConfigurationError("service host is fixed to the loopback address")
        expected_label = (
            ORCH_SIDECAR_LABEL
            if role is ServiceRole.ORCH_SIDECAR
            else DEFAULT_LABEL
        )
        if self.label != expected_label:
            raise ConfigurationError(
                "service label is fixed to the ORCH-Next namespace"
            )
        if role is ServiceRole.ORCH_SIDECAR and self.port != ORCH_SIDECAR_PORT:
            raise ConfigurationError("orch sidecar port is fixed to 3518")

    @property
    def services_dir(self) -> Path:
        return self.hermes_home / "services"

    @property
    def service_root(self) -> Path:
        root_name = (
            "orch-next-serve-orch"
            if self.role is ServiceRole.ORCH_SIDECAR
            else "orch-next-serve"
        )
        return self.services_dir / root_name

    @property
    def state_dir(self) -> Path:
        return self.service_root / "state"

    @property
    def program_arguments(self) -> list[str]:
        # --isolated is required when HERMES_HOME belongs to a named profile;
        # without it cmd_dashboard may intentionally route to the machine home.
        # launchd domains can carry ambient default/inherited variables.  Run
        # through the root-owned system env boundary so PYTHONHOME, PYTHONPATH,
        # and every other caller-controlled value are cleared before Python.
        environment = [f"HERMES_HOME={self.hermes_home}"]
        if self.role is ServiceRole.ORCH_SIDECAR:
            environment.append(f"{ORCH_SIDECAR_ENV}=1")
        environment.append(f"PATH={SERVICE_PATH}")
        arguments = [
            ENV_PATH,
            "-i",
            *environment,
            str(self.python),
            str(self.runtime),
            "serve",
            "--isolated",
        ]
        if self.role is ServiceRole.ORCH_SIDECAR:
            arguments.append("--orch-sidecar")
        arguments.extend(
            [
                "--host",
                self.host,
                "--port",
                str(self.port),
            ]
        )
        return arguments


@dataclass(frozen=True)
class ServiceResult:
    """Sanitized lifecycle result; never includes launchctl output."""

    action: str
    state: ServiceState
    label: str
    installed: bool
    loaded: bool = False
    pid: int | None = None
    detail: str | None = None
    recovery_records: tuple[RecoveryRecord, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "state": self.state.value,
            "label": self.label,
            "installed": self.installed,
            "loaded": self.loaded,
            "pid": self.pid,
            "detail": self.detail,
            "recovery_records": [record.as_dict() for record in self.recovery_records],
        }


@dataclass(frozen=True)
class ConfigArtifactExpectation:
    """Caller-bound metadata for one protected config generation."""

    file_type: str
    uid: int
    mode: int
    device: int
    inode: int


@dataclass(frozen=True)
class ConfigRecoveryRequest:
    recovery_identity: ConfigArtifactExpectation | None
    recovery_disposition: str | None
    retired_identity: ConfigArtifactExpectation | None
    retired_disposition: str | None
    active_identity: ConfigArtifactExpectation | None = None


@dataclass(frozen=True)
class _SessionTokenPathIdentity:
    """Metadata-only identity for the fixed session-token inode."""

    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _module_has_admitted_checkout_origin(
    module: object,
    module_name: str,
    relative_file: str,
) -> bool:
    """Inspect import machinery and origin without caller-defined hooks."""

    if type(module) is not types.ModuleType:
        return False
    module_values = module.__dict__
    spec = module_values.get("__spec__")
    loader = module_values.get("__loader__")
    if (
        type(spec) is not importlib.machinery.ModuleSpec
        or type(loader) is not importlib.machinery.SourceFileLoader
        or spec.loader is not loader
        or spec.name != module_name
        or loader.name != module_name
        or module_values.get("__name__") != module_name
        or set(loader.__dict__) != {"name", "path"}
    ):
        return False
    try:
        expected = (_ADMITTED_CHECKOUT_ROOT / relative_file).resolve(strict=True)
        return all(
            Path(value).resolve(strict=True) == expected
            for value in (
                module_values.get("__file__"),
                spec.origin,
                loader.path,
            )
            if type(value) is str
        ) and all(
            type(value) is str
            for value in (
                module_values.get("__file__"),
                spec.origin,
                loader.path,
            )
        )
    except OSError:
        return False


def _admitted_checkout_module(
    config: ServiceConfig,
    module_name: str,
    relative_file: str,
):
    """Import one runtime module only from this exact lifecycle checkout."""

    if config.worktree.resolve(strict=True) != _ADMITTED_CHECKOUT_ROOT:
        raise ImportError("lifecycle checkout mismatch")
    module = importlib.import_module(module_name)
    if not _module_has_admitted_checkout_origin(
        module,
        module_name,
        relative_file,
    ):
        raise ImportError("lifecycle module origin mismatch")
    return module


def _checkout_import_preflight(config: ServiceConfig) -> bool:
    """Resolve the lifecycle-only import closure without provider execution."""

    if not _ISOLATED_LAUNCHER_RUNTIME:
        return False

    modules = (
        ("scripts", "scripts/__init__.py"),
        (
            "scripts.orch_next_hermes_mcp_launcher",
            "scripts/orch_next_hermes_mcp_launcher.py",
        ),
        (
            "scripts.orch_next_hermes_session_token_source",
            "scripts/orch_next_hermes_session_token_source.py",
        ),
        ("tui_gateway", "tui_gateway/__init__.py"),
        ("tui_gateway.maestro_authority", "tui_gateway/maestro_authority.py"),
        ("agent", "agent/__init__.py"),
        ("agent.jiter_preload", "agent/jiter_preload.py"),
        ("agent.secret_sources", "agent/secret_sources/__init__.py"),
        ("agent.secret_sources.base", "agent/secret_sources/base.py"),
        ("agent.secret_sources._cache", "agent/secret_sources/_cache.py"),
        # Protected session-token readiness uses CommandSource directly. Do
        # not import unrelated provider implementations (and their optional
        # native dependencies) into the fixed system-Python controller.
        ("agent.secret_sources.command", "agent/secret_sources/command.py"),
    )
    try:
        for module_name, relative_file in modules:
            if module_name in sys.modules:
                return False
        for module_name, relative_file in modules:
            _admitted_checkout_module(config, module_name, relative_file)
    except (ImportError, OSError, SystemExit):
        return False
    return True


def _session_token_logical_id(config: ServiceConfig) -> str:
    """Bind one authority decision to this service/profile target only."""

    target = f"{config.label}\0{config.hermes_home}".encode("utf-8")
    return "orch-next-session-token-" + hashlib.sha256(target).hexdigest()


def _session_token_runtime_revision(config: ServiceConfig) -> str | None:
    """Return the exact clean checkout revision used by an authority boundary."""

    try:
        root = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(config.worktree),
                "rev-parse",
                "--show-toplevel",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=5,
        )
        head = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(config.worktree),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=5,
        )
        # Keep the NUL-safe full-tree check last so it is the immediate Git
        # observation at each request/consume/writer boundary.
        status = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(config.worktree),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--",
                ".",
                ":(exclude)distribution/.orch-next-hermes-harness.distribution.lock",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if root.returncode != 0 or status.returncode != 0 or head.returncode != 0:
        return None
    try:
        observed_root = Path((root.stdout or b"").decode("utf-8").strip()).resolve(
            strict=True
        )
        expected_root = config.worktree.resolve(strict=True)
        revision = (head.stdout or b"").decode("ascii").strip()
    except (OSError, UnicodeError):
        return None
    if (
        observed_root != expected_root
        or bool(status.stdout)
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
    ):
        return None
    return revision


def _session_token_runtime_identity(
    config: ServiceConfig,
) -> tuple[str, dict[str, str], str] | None:
    """Reverify clean Git state and the admitted portable runtime closure."""

    try:
        launcher = _admitted_checkout_module(
            config,
            "scripts.orch_next_hermes_mcp_launcher",
            "scripts/orch_next_hermes_mcp_launcher.py",
        )

        before_revision = _session_token_runtime_revision(config)
        if before_revision is None:
            return None
        provenance, provenance_digest = launcher.verified_lifecycle_runtime_provenance(
            config.worktree / "distribution" / launcher.PLUGIN_ID,
            expected_source_root=config.worktree,
        )
        after_revision = _session_token_runtime_revision(config)
    except (Exception, SystemExit):
        return None
    if (
        before_revision != after_revision
        or provenance.get("runtimeCommit") != before_revision
    ):
        return None
    return before_revision, provenance, provenance_digest


def _session_token_install_authority_context(config: ServiceConfig) -> object:
    """Build one install-only request from the admitted service identity."""

    identity = _session_token_runtime_identity(config)
    if identity is None:
        return None
    revision, _provenance, _provenance_digest = identity
    try:
        authority = _admitted_checkout_module(
            config,
            "tui_gateway.maestro_authority",
            "tui_gateway/maestro_authority.py",
        )
        return authority.build_session_token_install_authority_request(
            logical_session_id=_session_token_logical_id(config),
            runtime_revision=revision,
        )
    except Exception:
        return None


def _consume_session_token_authority(
    config: ServiceConfig,
    authority_context: object,
    *,
    rotate: bool,
) -> bool:
    """Consume exactly one existing Maestro decision for a source mutation."""

    if authority_context is None:
        return False
    identity = _session_token_runtime_identity(config)
    if identity is None:
        return False
    revision, provenance, provenance_digest = identity
    actual = {
        "logical_session_id": _session_token_logical_id(config),
        "ui_session_id": (
            "orch-next-session-token-rotate"
            if rotate
            else "orch-next-session-token-create"
        ),
        "method": "prompt.submit",
        "target": "hermes",
        "runtime_revision": revision,
    }
    binding = (
        authority_context.get("decision_binding")
        if type(authority_context) is dict
        else None
    )
    expected_decision_id = binding.get("decision_id") if type(binding) is dict else None
    if type(expected_decision_id) is not str:
        return False
    try:
        authority = _admitted_checkout_module(
            config,
            "tui_gateway.maestro_authority",
            "tui_gateway/maestro_authority.py",
        )
        result = authority.consume_maestro_authority_decision(
            authority_context,
            actual,
        )
    except Exception:
        return False
    expected_result_keys = {
        "outcome",
        "decision_id",
        "consumed_once",
        "runtime_provenance_manifest",
        "runtime_provenance_manifest_digest",
    }
    if (
        type(result) is dict
        and set(result) == expected_result_keys
        and result.get("outcome") == "allow"
        and result.get("decision_id") == expected_decision_id
        and result.get("consumed_once") is True
    ):
        return (
            result.get("runtime_provenance_manifest") == provenance
            and result.get("runtime_provenance_manifest_digest") == provenance_digest
        )
    return False


def _session_token_source_ready(config: ServiceConfig) -> bool:
    """Use the registered CommandSource, never a direct token-file reader."""

    try:
        command = _admitted_checkout_module(
            config,
            "agent.secret_sources.command",
            "agent/secret_sources/command.py",
        )
        base = _admitted_checkout_module(
            config,
            "agent.secret_sources.base",
            "agent/secret_sources/base.py",
        )
        source = _admitted_checkout_module(
            config,
            "scripts.orch_next_hermes_session_token_source",
            "scripts/orch_next_hermes_session_token_source.py",
        )

        command_cfg = source.protected_command_config(config.python)
        source_env = dict(os.environ)
        source_env["HERMES_HOME"] = str(config.hermes_home)
        # The fixed helper reads only its admitted file descriptor route. A
        # stale/raw process or dotenv value must not even be inherited by its
        # command-source subprocess.
        source_env.pop(source.SESSION_TOKEN_ENV, None)
        environment_token = base.set_source_environment(source_env)
        try:
            result = command.CommandSource().fetch(command_cfg, config.hermes_home)
        finally:
            base.reset_source_environment(environment_token)
        token = result.secrets.get(source.SESSION_TOKEN_ENV)
        return type(token) is str and re.fullmatch(r"[0-9a-f]{64}", token) is not None
    except Exception:
        return False


def _prepare_session_token_command_config(config: ServiceConfig) -> bool:
    """Prepare only the credential-free command-source configuration."""

    try:
        source = _admitted_checkout_module(
            config,
            "scripts.orch_next_hermes_session_token_source",
            "scripts/orch_next_hermes_session_token_source.py",
        )

        command_cfg = source.protected_command_config(config.python)
        return source.prepare_protected_command_config(
            config.hermes_home,
            command_cfg,
            runtime=config.python,
        ) and source.command_source_is_admitted(
            config.hermes_home,
            command_cfg,
            runtime=config.python,
        )
    except Exception:
        return False


def _session_token_path_identity(config: ServiceConfig) -> _SessionTokenPathIdentity | None:
    """Observe token identity without opening or returning its contents."""

    try:
        source = _admitted_checkout_module(
            config,
            "scripts.orch_next_hermes_session_token_source",
            "scripts/orch_next_hermes_session_token_source.py",
        )
        token_path = config.hermes_home / source.TOKEN_RELATIVE_PATH
        info = os.lstat(token_path)
    except (Exception, OSError):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return _SessionTokenPathIdentity(
        info.st_dev,
        info.st_ino,
        stat.S_IMODE(info.st_mode),
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _running_refresh_binding(
    status: ServiceResult,
    config: ServiceConfig,
) -> bool:
    """Require the fixed job definition and its same running process."""

    return (
        status.state is ServiceState.RUNNING
        and status.label == config.label == DEFAULT_LABEL
        and status.installed is True
        and status.loaded is True
        and type(status.pid) is int
        and status.pid > 0
    )


def _rollback_session_token_command_config(
    config: ServiceConfig,
    source: object,
    prior: object,
    committed: object,
) -> bool:
    """CAS-restore the old generation without clobbering a racing writer.

    The admitted source owns the descriptor-relative atomic exchange protocol.
    This wrapper only supplies the pre/post generations captured by this action;
    it never returns protected config contents.
    """

    try:
        with source._open_absolute_directory(
            config.hermes_home,
            exact_mode=source._DIRECTORY_MODE,
        ) as home_fd:
            current = source._read_optional_config_snapshot(home_fd)
            if current != committed:
                return False
            installed = source._ConfigTemp(
                source._CONFIG_LEAF,
                committed.device,
                committed.inode,
            )
            if prior is None:
                return source._remove_installed_config(
                    home_fd,
                    installed,
                    committed.content,
                )
            rollback = source._write_config_temp(home_fd, prior.content)
            desired = source._read_config_snapshot_at(home_fd, rollback.name)
            return source._exchange_back_without_clobber(
                home_fd,
                rollback.name,
                desired,
                installed,
                committed.content,
            )
    except Exception:
        return False


def _clear_refresh_config_recovery_slots(
    config: ServiceConfig,
    source: object,
) -> str | None:
    """Quarantine exact prior refresh generations before the next replacement.

    The protected source refuses a new replacement while its bounded recovery
    slots are occupied.  This action owns only generations it observes under
    the lifecycle lock and asks the source to move them to its bounded
    quarantine family.  The source's metadata checks and exclusive renames
    make a raced replacement fail closed rather than being deleted or clobbered.
    """

    try:
        with source._open_absolute_directory(
            config.hermes_home,
            exact_mode=source._DIRECTORY_MODE,
        ) as home_fd:
            recovery_identity = source._config_artifact_identity_at(
                home_fd,
                source._CONFIG_RECOVERY_LEAF,
            )
            retired = [
                (name, source._config_artifact_identity_at(home_fd, name))
                for name in source._CONFIG_RETIRED_LEAVES
            ]
        retired_identities = [identity for _name, identity in retired if identity]
        if len(retired_identities) > 1:
            return None
        if recovery_identity is None and not retired_identities:
            return "session_token_config_recovery_not_required"
        outcome = source.recover_protected_command_config(
            config.hermes_home,
            recovery_identity=recovery_identity,
            recovery_disposition=("quarantine" if recovery_identity else None),
            retired_identity=(retired_identities[0] if retired_identities else None),
            retired_disposition=("quarantine" if retired_identities else None),
        )
        return outcome.detail if outcome.recovered is True else None
    except Exception:
        return None


def _prepare_session_token_source(
    config: ServiceConfig,
    *,
    authority_context: object = None,
    rotate: bool = False,
    prepare_config: bool = False,
) -> bool:
    """Prepare config only for install; create token only after authority."""

    if not rotate and _session_token_source_ready(config):
        return True
    try:
        source = _admitted_checkout_module(
            config,
            "scripts.orch_next_hermes_session_token_source",
            "scripts/orch_next_hermes_session_token_source.py",
        )

        command_cfg = source.protected_command_config(config.python)
        if prepare_config and not source.prepare_protected_command_config(
            config.hermes_home,
            command_cfg,
            runtime=config.python,
        ):
            return False
        if not source.command_source_is_admitted(
            config.hermes_home,
            command_cfg,
            runtime=config.python,
        ):
            return False
        if not _consume_session_token_authority(
            config,
            authority_context,
            rotate=rotate,
        ):
            return False
        # Import the writer module before this final check. A disk replacement
        # after the boundary therefore cannot select a different module for
        # the one admitted call below.
        final_identity = _session_token_runtime_identity(config)
        binding = (
            authority_context.get("decision_binding")
            if type(authority_context) is dict
            else None
        )
        bound_revision = (
            binding.get("runtime_revision") if type(binding) is dict else None
        )
        if (
            final_identity is None
            or type(bound_revision) is not str
            or final_identity[0] != bound_revision
        ):
            return False
        source.create_or_rotate_token(config.hermes_home, rotate=rotate)
    except Exception:
        return False
    return _session_token_source_ready(config)


def _session_token_source_unavailable(
    action: str,
    config: ServiceConfig,
    directory: _PlistDirectory,
    *,
    runner: Runner,
    domain: str,
) -> ServiceResult:
    """Retain a durable disabled hold when the source cannot be consumed."""

    hold_error = _establish_durable_hold(config, runner=runner, domain=domain)
    detail = "session_token_source_unavailable"
    if hold_error is not None:
        detail = f"{detail}_hold_{hold_error}"
    return ServiceResult(
        action,
        ServiceState.UNAVAILABLE,
        config.label,
        _plist_installed_at(directory),
        detail=detail,
    )


def render_launchd_plist(config: ServiceConfig) -> str:
    """Render a deterministic, loopback-only launchd plist."""

    payload = {
        "ExitTimeOut": 25,
        "KeepAlive": True,
        "Label": config.label,
        "ProcessType": "Background",
        "ProgramArguments": config.program_arguments,
        "RunAtLoad": True,
        # Hermes server output can contain prompts or private runtime data.
        # launchd must not persist that stream; operator output is limited to
        # the sanitized ServiceResult projection below.
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 30,
        # launchd reads this as a numeric mode.  63 decimal is 077 octal.
        "Umask": 0o077,
        "WorkingDirectory": str(config.worktree),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


def ensure_private_directories(config: ServiceConfig) -> None:
    """Create only the service-owned state directory with owner-only mode."""

    expected_uid = os.getuid()
    _require_owned_private_directory(config.hermes_home, expected_uid=expected_uid)
    for directory in (config.services_dir, config.service_root, config.state_dir):
        try:
            directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            pass
        _require_owned_private_directory(directory, expected_uid=expected_uid)


def default_plist_path(
    label: str = DEFAULT_LABEL,
    *,
    home: Path | None = None,
    uid: int | None = None,
    passwd_lookup: Callable[[int], object] | None = None,
) -> Path:
    """Locate LaunchAgents under the real account home, never profile HOME.

    ``home`` is an explicit test/embedding override.  The default path mirrors
    ``hermes_cli.gateway._launchd_user_home`` and resolves the macOS account via
    ``pwd.getpwuid`` even when a profile-scoped process changed HOME.
    """

    if label not in {DEFAULT_LABEL, ORCH_SIDECAR_LABEL}:
        raise ConfigurationError("service label is fixed to the ORCH-Next namespace")
    if home is not None:
        base = home
    else:
        if passwd_lookup is None:
            import pwd

            passwd_lookup = pwd.getpwuid
        account = passwd_lookup(os.getuid() if uid is None else uid)
        base = Path(str(getattr(account, "pw_dir")))
    return base / "Library" / "LaunchAgents" / f"{label}.plist"


@dataclass(frozen=True)
class _PlistDirectory:
    fd: int
    path: Path
    name: str
    device: int
    inode: int


@dataclass(frozen=True)
class _PlistSnapshot:
    content: bytes
    mode: int
    device: int
    inode: int


@dataclass(frozen=True)
class _BootstrapStage:
    fd: int
    leaf: str
    directory: _PlistDirectory
    snapshot: _PlistSnapshot
    content: bytes
    expected_label: str
    recovery_record: RecoveryRecord


def _validate_owned_directory_info(
    info: os.stat_result, *, expected_uid: int, label: str
) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ConfigurationError(f"{label} must be a real directory")
    if info.st_uid != expected_uid:
        raise ConfigurationError(f"{label} must be owned by current account")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ConfigurationError(f"{label} must not be group/world writable")


@contextmanager
def _open_plist_directory(path: Path) -> Iterator[_PlistDirectory]:
    """Admit LaunchAgents once and retain its identity via a directory fd."""

    expected_uid = os.getuid()
    before = os.lstat(path.parent)
    _validate_owned_directory_info(
        before, expected_uid=expected_uid, label="LaunchAgents directory"
    )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(path.parent, flags)
    try:
        opened = os.fstat(fd)
        _validate_owned_directory_info(
            opened, expected_uid=expected_uid, label="LaunchAgents directory"
        )
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            raise ConfigurationError("LaunchAgents directory changed during admission")
        yield _PlistDirectory(fd, path.parent, path.name, opened.st_dev, opened.st_ino)
    finally:
        os.close(fd)


def _parent_path_matches(directory: _PlistDirectory) -> bool:
    try:
        current = os.lstat(directory.path)
        _validate_owned_directory_info(
            current,
            expected_uid=os.getuid(),
            label="LaunchAgents directory",
        )
    except (ConfigurationError, OSError):
        return False
    return current.st_dev == directory.device and current.st_ino == directory.inode


def _validate_plist_at(directory: _PlistDirectory) -> os.stat_result | None:
    try:
        info = os.stat(directory.name, dir_fd=directory.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigurationError("plist target must be a regular non-symlink file")
    if info.st_uid != os.getuid():
        raise ConfigurationError("plist target must be owned by current account")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ConfigurationError("plist target must not be group/world writable")
    return info


def _same_identity(
    info: os.stat_result | None, expected: _PlistSnapshot | None
) -> bool:
    if info is None or expected is None:
        return info is None and expected is None
    return info.st_dev == expected.device and info.st_ino == expected.inode


def _same_snapshot_identity(
    observed: _PlistSnapshot | None, expected: _PlistSnapshot | None
) -> bool:
    if observed is None or expected is None:
        return observed is None and expected is None
    return observed.device == expected.device and observed.inode == expected.inode


def _atomic_write_at(
    directory: _PlistDirectory,
    content: bytes,
    mode: int,
    *,
    expected: _PlistSnapshot | None,
    expected_label: str | None = None,
) -> _PlistSnapshot:
    current = _validate_plist_at(directory)
    if not _same_identity(current, expected):
        raise ConfigurationError("plist changed before atomic replacement")

    temp_name = f".{directory.name}.{secrets.token_hex(12)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(temp_name, flags, mode, dir_fd=directory.fd)
    temp_exists = True
    candidate: _PlistSnapshot | None = None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid():
            raise OSError("temporary plist file identity rejected")
        os.fchmod(fd, mode)
        candidate = _PlistSnapshot(
            content,
            mode,
            opened.st_dev,
            opened.st_ino,
        )
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                raise OSError("short plist write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        current = _validate_plist_at(directory)
        if not _same_identity(current, expected):
            raise ConfigurationError("plist changed before atomic replacement")
        os.replace(
            temp_name,
            directory.name,
            src_dir_fd=directory.fd,
            dst_dir_fd=directory.fd,
        )
        temp_exists = False
        os.fsync(directory.fd)
        installed = _snapshot_plist_at(directory)
        if installed is None:
            raise OSError("atomic plist replacement was not observable")
        if (
            not _same_snapshot_identity(installed, candidate)
            or installed.content != content
        ):
            raise ConfigurationError("atomic plist candidate identity changed")
        if expected_label is not None:
            try:
                parsed = plistlib.loads(installed.content)
            except Exception as exc:
                raise ConfigurationError(
                    "atomic plist candidate is not a plist"
                ) from exc
            if not isinstance(parsed, dict) or parsed.get("Label") != expected_label:
                raise ConfigurationError("atomic plist candidate label changed")
        return installed
    except Exception as exc:
        admitted: _PlistSnapshot | None = None
        try:
            observed = _snapshot_plist_at(directory)
        except (ConfigurationError, OSError):
            observed = None
        if observed is not None:
            if candidate is not None and _same_snapshot_identity(observed, candidate):
                admitted = candidate
            elif expected is not None and _same_snapshot_identity(observed, expected):
                admitted = expected
        recovery_record = None
        if temp_exists:
            temp_directory = _PlistDirectory(
                directory.fd,
                directory.path,
                temp_name,
                directory.device,
                directory.inode,
            )
            try:
                temp_snapshot = _snapshot_plist_at(temp_directory)
            except (ConfigurationError, OSError):
                temp_snapshot = None
            artifact_kind = RecoveryArtifactKind.PARTIAL_ATOMIC_TEMP
            if temp_snapshot is not None and temp_snapshot.content == content:
                artifact_kind = RecoveryArtifactKind.RESTORABLE_PLIST
            recovery_record = _recovery_record(
                directory,
                temp_name,
                expected_label=expected_label or DEFAULT_LABEL,
                artifact_kind=artifact_kind,
            )
        raise AtomicWriteError(admitted, recovery_record) from exc
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_exists:
            # Retain an interrupted unique temp leaf for separately authorized
            # cleanup.  A same-UID process could replace even this random name
            # between identity proof and unlink, so this migration never
            # performs a pathname deletion.
            os.fsync(directory.fd)


@contextmanager
def _lifecycle_lock(config: ServiceConfig) -> Iterator[None]:
    """Hold the exactly-one-writer advisory lock for a lifecycle mutation."""

    expected_uid = os.getuid()
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    account_before = _admit_stable_account_home(
        config._account_home, expected_uid=expected_uid
    )
    if (
        account_before.st_dev != config._account_home_device
        or account_before.st_ino != config._account_home_inode
    ):
        raise ConfigurationError("passwd account home lock anchor identity changed")
    account_fd = os.open(config._account_home, directory_flags)
    account_locked = False
    home_fd = -1
    home_locked = False
    state_fd = -1
    lock_fd = -1
    try:
        account_opened = os.fstat(account_fd)
        if (
            account_opened.st_dev != config._account_home_device
            or account_opened.st_ino != config._account_home_inode
        ):
            raise ConfigurationError(
                "passwd account home lock anchor changed during admission"
            )
        try:
            fcntl.flock(account_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LifecycleBusyError("lifecycle mutation already active") from exc
        account_locked = True

        home_before = os.lstat(config.hermes_home)
        _validate_owned_directory_info(
            home_before, expected_uid=expected_uid, label="HERMES_HOME lock anchor"
        )
        if stat.S_IMODE(home_before.st_mode) & 0o077:
            raise ConfigurationError("HERMES_HOME lock anchor must be owner-private")
        if (
            home_before.st_dev != config._hermes_home_device
            or home_before.st_ino != config._hermes_home_inode
        ):
            raise ConfigurationError("HERMES_HOME lock anchor identity changed")
        home_fd = os.open(config.hermes_home, directory_flags)
        home_opened = os.fstat(home_fd)
        if (
            home_opened.st_dev != config._hermes_home_device
            or home_opened.st_ino != config._hermes_home_inode
        ):
            raise ConfigurationError("HERMES_HOME lock anchor changed during admission")
        try:
            fcntl.flock(home_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LifecycleBusyError("lifecycle mutation already active") from exc
        home_locked = True

        # The canonical passwd-home inode is locked before any replaceable
        # HERMES_HOME, service directory, or lock leaf is admitted.
        ensure_private_directories(config)
        state_before = os.lstat(config.state_dir)
        _validate_owned_directory_info(
            state_before, expected_uid=expected_uid, label="service state directory"
        )
        state_fd = os.open(config.state_dir, directory_flags)
        state_opened = os.fstat(state_fd)
        if (
            state_opened.st_dev != state_before.st_dev
            or state_opened.st_ino != state_before.st_ino
        ):
            raise ConfigurationError("service state directory changed during admission")
        lock_flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        lock_fd = os.open(
            _LIFECYCLE_LOCK_NAME,
            lock_flags,
            PRIVATE_FILE_MODE,
            dir_fd=state_fd,
        )
        lock_info = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != expected_uid
            or stat.S_IMODE(lock_info.st_mode) & 0o077
        ):
            raise ConfigurationError("lifecycle lock file identity rejected")
        os.fsync(state_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LifecycleBusyError("lifecycle mutation already active") from exc
        yield
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        if state_fd >= 0:
            os.close(state_fd)
        if home_locked and home_fd >= 0:
            try:
                fcntl.flock(home_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        if home_fd >= 0:
            os.close(home_fd)
        if account_locked:
            try:
                fcntl.flock(account_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(account_fd)


def _target(config: ServiceConfig, domain: str) -> str:
    return f"{domain}/{config.label}"


def _run_launchctl(
    runner: Runner,
    arguments: Sequence[str],
    *,
    timeout: int = LAUNCHD_TIMEOUT_SECONDS,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    options: dict[str, object] = {
        "check": False,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if pass_fds:
        options["pass_fds"] = pass_fds
    return runner([LAUNCHCTL_PATH, *arguments], **options)


def _launchd_disabled_override(
    config: ServiceConfig,
    runner: Runner,
    domain: str,
) -> tuple[bool | None, str | None]:
    """Read the exact launchd disabled override without exposing raw output.

    ``launchctl print-disabled`` omits labels that have no disabled override;
    omission therefore means enabled. Duplicate or unrecognised values for our
    exact label are ambiguous and fail closed.
    """

    try:
        completed = _run_launchctl(runner, ["print-disabled", domain])
    except (OSError, subprocess.TimeoutExpired):
        return None, "launchctl_disabled_state_unavailable"
    if completed.returncode != 0:
        return None, "launchctl_disabled_state_error"
    output = completed.stdout or ""
    if len(output) > _LAUNCHD_DISABLED_MAX_CHARS:
        return None, "launchctl_disabled_state_oversize"
    lines = output.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if (
        len(lines) < 2
        or lines[0].strip() != _LAUNCHD_DISABLED_HEADER
        or lines[-1].strip() != "}"
    ):
        return None, "launchctl_disabled_state_malformed"
    entries: dict[str, bool] = {}
    for line in lines[1:-1]:
        if not line.strip():
            continue
        match = _LAUNCHD_DISABLED_ENTRY_RE.fullmatch(line)
        if match is None:
            return None, "launchctl_disabled_state_malformed"
        label = match.group("label")
        if label in entries:
            return None, "launchctl_disabled_state_ambiguous"
        entries[label] = match.group("value") == "disabled"
    if config.label not in entries:
        return False, None
    return entries[config.label], None


def _set_launchd_disabled(
    config: ServiceConfig,
    runner: Runner,
    domain: str,
    *,
    disabled: bool,
) -> str | None:
    """Set and independently verify the exact label's disabled override."""

    verb = "disable" if disabled else "enable"
    try:
        completed = _run_launchctl(
            runner,
            [verb, _target(config, domain)],
        )
    except (OSError, subprocess.TimeoutExpired):
        return f"launchctl_{verb}_unavailable"
    if completed.returncode != 0:
        return f"launchctl_{verb}_error"
    observed, detail = _launchd_disabled_override(config, runner, domain)
    if detail is not None:
        return detail
    if observed is not disabled:
        return "launchctl_disabled_state_mismatch"
    return None


def select_launchd_domain(
    config: ServiceConfig,
    *,
    runner: Runner = subprocess.run,
    uid: int | None = None,
) -> str:
    """Select gui/user launchd domain using Hermes gateway probe semantics.

    This is the injected-runner form of ``hermes_cli.gateway._launchd_domain``:
    probe the loaded label in GUI then user domains, consult managername when
    neither contains it, and conservatively default to the user domain.
    """

    selected_uid = os.getuid() if uid is None else uid
    gui_domain = f"gui/{selected_uid}"
    user_domain = f"user/{selected_uid}"
    for candidate in (gui_domain, user_domain):
        try:
            completed = _run_launchctl(
                runner,
                ["print", _target(config, candidate)],
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0:
            return candidate

    try:
        manager = _run_launchctl(runner, ["managername"], timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return user_domain
    if manager.returncode == 0 and "Aqua" in (manager.stdout or ""):
        return gui_domain
    return user_domain


def _selected_domain(
    config: ServiceConfig,
    runner: Runner,
    uid: int | None,
    domain: str | None,
) -> str:
    return domain or select_launchd_domain(config, runner=runner, uid=uid)


def _snapshot_open_fd(fd: int) -> _PlistSnapshot:
    """Read an already admitted descriptor without consulting its pathname."""

    opened = os.fstat(fd)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) & 0o022
    ):
        raise ConfigurationError("bootstrap stage descriptor identity rejected")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return _PlistSnapshot(
        b"".join(chunks),
        stat.S_IMODE(opened.st_mode),
        opened.st_dev,
        opened.st_ino,
    )


def _bootstrap_stage_matches(stage: _BootstrapStage) -> bool:
    """Validate the linked stage, descriptor and recovery locator."""

    try:
        descriptor_info = os.fstat(stage.fd)
        descriptor_flags = fcntl.fcntl(stage.fd, fcntl.F_GETFL)
        linked_info = os.stat(
            stage.leaf,
            dir_fd=stage.directory.fd,
            follow_symlinks=False,
        )
        observed = _snapshot_open_fd(stage.fd)
        parsed = plistlib.loads(observed.content)
    except (ConfigurationError, OSError, ValueError, TypeError):
        return False
    return (
        descriptor_info.st_nlink == 1
        and stat.S_ISREG(linked_info.st_mode)
        and linked_info.st_uid == os.getuid()
        and stat.S_IMODE(linked_info.st_mode) == BOOTSTRAP_STAGE_MODE
        and linked_info.st_dev == descriptor_info.st_dev
        and linked_info.st_ino == descriptor_info.st_ino
        and descriptor_flags & os.O_ACCMODE == os.O_RDONLY
        and _parent_path_matches(stage.directory)
        and _same_snapshot_identity(observed, stage.snapshot)
        and observed.mode == stage.snapshot.mode
        and observed.content == stage.content
        and isinstance(parsed, dict)
        and parsed.get("Label") == stage.expected_label
        and _recovery_record_matches(
            stage.directory,
            stage.recovery_record,
            expected_label=stage.expected_label,
            require_canonical_absent=False,
        )
    )


@contextmanager
def _bootstrap_stage(
    directory: _PlistDirectory,
    content: bytes,
    *,
    expected_label: str,
) -> Iterator[_BootstrapStage]:
    """Separate retained recovery evidence from launchd-readable staging."""

    recovery_name = f".{directory.name}.bootstrap-recovery-{secrets.token_hex(16)}"
    # launchctl classifies path inputs by their plist suffix before admitting
    # the file.  A real, readable staging inode without ``.plist`` was rejected
    # by macOS 26.5.2 with status 66.  Keep the suffix while retaining the
    # unique, owner-private, identity-checked stage.
    consume_name = f".{directory.name}.bootstrap-consume-{secrets.token_hex(16)}.plist"
    create_flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    read_flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    recovery_fd = os.open(
        recovery_name,
        create_flags,
        PRIVATE_FILE_MODE,
        dir_fd=directory.fd,
    )
    consume_write_fd = -1
    consume_fd = -1
    consume_snapshot = None
    recovery_record = None
    stage_admitted = False
    try:
        offset = 0
        while offset < len(content):
            written = os.write(recovery_fd, content[offset:])
            if written <= 0:
                raise OSError("short bootstrap recovery write")
            offset += written
        os.fchmod(recovery_fd, BOOTSTRAP_STAGE_MODE)
        os.fsync(recovery_fd)
        recovery_record = _recovery_record(
            directory,
            recovery_name,
            expected_label=expected_label,
            artifact_kind=RecoveryArtifactKind.RESTORABLE_PLIST,
        )
        if recovery_record is None:
            raise ConfigurationError("bootstrap recovery label rejected")

        consume_write_fd = os.open(
            consume_name,
            create_flags,
            PRIVATE_FILE_MODE,
            dir_fd=directory.fd,
        )
        offset = 0
        while offset < len(content):
            written = os.write(consume_write_fd, content[offset:])
            if written <= 0:
                raise OSError("short bootstrap consumption write")
            offset += written
        os.fchmod(consume_write_fd, BOOTSTRAP_STAGE_MODE)
        os.fsync(consume_write_fd)
        written_snapshot = _snapshot_open_fd(consume_write_fd)
        consume_fd = os.open(
            consume_name,
            read_flags,
            dir_fd=directory.fd,
        )
        snapshot = _snapshot_open_fd(consume_fd)
        consume_snapshot = snapshot
        if (
            not _same_snapshot_identity(snapshot, written_snapshot)
            or snapshot.content != content
            or snapshot.mode != BOOTSTRAP_STAGE_MODE
        ):
            raise ConfigurationError("bootstrap consumption bytes changed")
        os.close(consume_write_fd)
        consume_write_fd = -1
        stage = _BootstrapStage(
            consume_fd,
            consume_name,
            directory,
            snapshot,
            content,
            expected_label,
            recovery_record,
        )
        if not _bootstrap_stage_matches(stage):
            raise ConfigurationError("bootstrap stage identity changed")
        stage_admitted = True
        yield stage
    except Exception as exc:
        if stage_admitted:
            raise
        if recovery_record is None:
            recovery_record = _recovery_record(
                directory,
                recovery_name,
                expected_label=expected_label,
                artifact_kind=RecoveryArtifactKind.PARTIAL_ATOMIC_TEMP,
            )
        if isinstance(exc, AtomicWriteError):
            raise
        raise AtomicWriteError(None, recovery_record) from exc
    finally:
        if consume_snapshot is not None:
            try:
                linked = os.stat(
                    consume_name,
                    dir_fd=directory.fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                linked = None
            if (
                linked is not None
                and linked.st_dev == consume_snapshot.device
                and linked.st_ino == consume_snapshot.inode
            ):
                os.unlink(consume_name, dir_fd=directory.fd)
        for fd in (consume_fd, consume_write_fd, recovery_fd):
            if fd < 0:
                continue
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
        # Only the separate recovery leaf is retained.  A changed/foreign
        # stage pathname is never unlinked by this owner.
        os.fsync(directory.fd)


def _wait_for_launchd_label_absence(
    config: ServiceConfig,
    *,
    runner: Runner,
    domain: str,
) -> subprocess.CompletedProcess[str]:
    """Wait for bootout to reach the same terminal absence used by stop."""

    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(STOP_CONFIRM_ATTEMPTS):
        completed = _run_launchctl(
            runner,
            ["print", _target(config, domain)],
        )
        if completed.returncode in _STATUS_NOT_LOADED_RETURN_CODES:
            return completed
        if completed.returncode != 0:
            return completed
        if attempt + 1 < STOP_CONFIRM_ATTEMPTS:
            _NATIVE_SELECT([], [], [], STOP_CONFIRM_INTERVAL_SECONDS)
    assert completed is not None
    return completed


def _bootstrap_launchd_job(
    config: ServiceConfig,
    stage: _BootstrapStage,
    *,
    runner: Runner,
    domain: str,
) -> subprocess.CompletedProcess[str]:
    """Bootstrap from one private, identity-bound staging pathname.

    macOS launchd resolves service paths in the manager process and requires a
    plist-classified path.  A caller-inherited ``/dev/fd/N`` is not readable
    there, while a real staging path without the ``.plist`` suffix is rejected
    before service admission.  Keep a unique owner-only ``.plist`` link alive
    for the bounded call, validate its descriptor/path identity immediately
    before and after, then unlink only that exact inode in
    ``_bootstrap_stage``.
    """

    if not _bootstrap_stage_matches(stage):
        raise ConfigurationError("bootstrap stage identity changed")
    stage_path = str(stage.directory.path / stage.leaf)
    completed = _run_launchctl(
        runner,
        ["bootstrap", domain, stage_path],
    )
    if completed.returncode in _BOOTSTRAP_DESCRIPTOR_UNAVAILABLE_RETURN_CODES:
        raise FileNotFoundError("path-backed bootstrap unavailable")
    if not _bootstrap_stage_matches(stage):
        try:
            os.fstat(stage.fd)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                raise FileNotFoundError("bootstrap stage descriptor closed") from exc
        raise ConfigurationError("bootstrap stage identity changed")
    if completed.returncode != _BOOTSTRAP_STALE_REGISTRATION_RETURN_CODE:
        return completed
    bootout = _run_launchctl(runner, ["bootout", _target(config, domain)])
    if bootout.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
        return bootout
    absent = _wait_for_launchd_label_absence(
        config,
        runner=runner,
        domain=domain,
    )
    if absent.returncode not in _STATUS_NOT_LOADED_RETURN_CODES:
        if absent.returncode == 0:
            return subprocess.CompletedProcess(
                absent.args,
                _BOOTSTRAP_STALE_REGISTRATION_RETURN_CODE,
                stdout="",
                stderr="",
            )
        return absent
    if not _bootstrap_stage_matches(stage):
        raise ConfigurationError("bootstrap stage identity changed")
    completed = _run_launchctl(
        runner,
        ["bootstrap", domain, stage_path],
    )
    if completed.returncode in _BOOTSTRAP_DESCRIPTOR_UNAVAILABLE_RETURN_CODES:
        raise FileNotFoundError("path-backed bootstrap unavailable")
    if not _bootstrap_stage_matches(stage):
        try:
            os.fstat(stage.fd)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                raise FileNotFoundError("bootstrap stage descriptor closed") from exc
        raise ConfigurationError("bootstrap stage identity changed")
    if completed.returncode in (
        _BOOTSTRAP_DESCRIPTOR_UNAVAILABLE_RETURN_CODES
        | {_BOOTSTRAP_STALE_REGISTRATION_RETURN_CODE}
    ):
        raise FileNotFoundError("path-backed bootstrap unavailable")
    return completed


def _confirm_launchd_label(
    config: ServiceConfig, *, runner: Runner, domain: str
) -> subprocess.CompletedProcess[str]:
    """Confirm launchd registered the exact executable definition.

    ``bootstrap`` accepts only a pathname, so pre/post inode checks cannot by
    themselves prove which bytes launchd consumed during the call. Bind every
    successful lifecycle result to launchd's own registered execution fields.
    Raw launchctl output is never returned through ``ServiceResult``.
    """

    if not _launchctl_binary_qualified():
        raise FileNotFoundError("launchctl print contract unqualified")
    completed = _run_launchctl(runner, ["print", _target(config, domain)])
    if completed.returncode != 0:
        return completed
    if not _launchd_definition_matches(config, domain, completed.stdout or ""):
        return subprocess.CompletedProcess(
            completed.args,
            78,
            stdout="",
            stderr="",
        )
    return completed


def _launchd_definition_mismatch_code(
    config: ServiceConfig, domain: str, output: str
) -> str | None:
    """Return one sanitized field class for a rejected print projection."""

    if not output or "\x00" in output or len(output.encode("utf-8")) > 262_144:
        return "output_invalid"
    lines = output.splitlines()
    if not lines or lines[0] != f"{_target(config, domain)} = {{":
        return "header"

    scalars: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    index = 1
    closed = False
    while index < len(lines):
        line = lines[index]
        if line == "}":
            closed = True
            index += 1
            break
        if not line:
            index += 1
            continue
        if not line.startswith("\t") or line.startswith("\t\t"):
            return "grammar"
        body = line[1:]
        if " = " not in body:
            return "grammar"
        key, value = body.split(" = ", 1)
        if not key or key in scalars or key in sections:
            return "grammar"
        if value != "{":
            scalars[key] = value
            index += 1
            continue
        entries: list[str] = []
        index += 1
        while index < len(lines) and lines[index] != "\t}":
            nested = lines[index]
            if not nested.startswith("\t\t"):
                return "grammar"
            entries.append(nested[2:])
            index += 1
        if index >= len(lines):
            return "grammar"
        sections[key] = entries
        index += 1

    if not closed or any(line for line in lines[index:]):
        return "grammar"

    environment: dict[str, str] = {}
    for entry in sections.get("environment", []):
        if " => " not in entry:
            return "environment_grammar"
        key, value = entry.split(" => ", 1)
        if not key or key in environment:
            return "environment_grammar"
        environment[key] = value

    properties = {
        item.strip() for item in scalars.get("properties", "").split("|") if item
    }
    domain_projection = scalars.get("domain")
    asid_projection = scalars.get("asid")
    session_match = (
        re.fullmatch(rf"{re.escape(domain)} \[([1-9][0-9]*)\]", domain_projection)
        if domain_projection is not None
        else None
    )
    session_projection_matches = (
        domain_projection is None and asid_projection is None
    ) or (
        session_match is not None
        and asid_projection is not None
        and asid_projection == session_match.group(1)
    )
    if not set(scalars).issubset(_LAUNCHD_PRINT_ALLOWED_SCALARS):
        return "unknown_scalar"
    if not set(sections).issubset(_LAUNCHD_PRINT_ALLOWED_SECTIONS):
        return "unknown_section"
    terminating_signal = scalars.get("last terminating signal")
    if terminating_signal is not None and (
        re.fullmatch(r"[1-9][0-9]?", terminating_signal) is None
        or int(terminating_signal) > _DARWIN_MAX_SIGNAL_NUMBER
    ):
        return "last_terminating_signal"
    if properties == _LAUNCHD_PRINT_CLI_PROPERTIES:
        plist_path = default_plist_path(
            label=config.label, home=config._account_home
        )
        stage_prefix = str(plist_path.parent / f".{plist_path.name}.bootstrap-consume-")
        stage_path = scalars.get("path")
        if (
            stage_path is None
            or re.fullmatch(
                rf"{re.escape(stage_prefix)}[0-9a-f]{{32}}\.plist", stage_path
            )
            is None
        ):
            return "cli_stage_path"
    if properties not in _LAUNCHD_PRINT_ALLOWED_PROPERTY_SETS:
        for required, code in (
            ("keepalive", "properties_missing_keepalive"),
            ("runatload", "properties_missing_runatload"),
            ("inferred program", "properties_missing_inferred_program"),
        ):
            if required not in properties:
                return code
        if "managed LWCR" in properties and "has LWCR" not in properties:
            return "properties_missing_has_lwcr"
        return "properties_extra"
    checks = (
        ("type", scalars.get("type") == "LaunchAgent"),
        ("session", session_projection_matches),
        ("program", scalars.get("program") == ENV_PATH),
        ("arguments", sections.get("arguments") == config.program_arguments),
        ("working_directory", scalars.get("working directory") == str(config.worktree)),
        ("stdout_path", scalars.get("stdout path") == "/dev/null"),
        ("stderr_path", scalars.get("stderr path") == "/dev/null"),
        ("spawn_type", scalars.get("spawn type") == "background (5)"),
        ("minimum_runtime", scalars.get("minimum runtime") == "30"),
        ("exit_timeout", scalars.get("exit timeout") == "25"),
        ("umask", scalars.get("umask") == "77"),
        ("cpumon", scalars.get("cpumon") == "default"),
        ("jetsam_priority", scalars.get("jetsam priority") == "40"),
        (
            "jetsam_active_limit",
            scalars.get("jetsam memory limit (active)") == "(unlimited)",
        ),
        (
            "jetsam_inactive_limit",
            scalars.get("jetsam memory limit (inactive)") == "(unlimited)",
        ),
        ("jetsam_thread_limit", scalars.get("jetsam thread limit") == "32"),
        (
            "jetsam_category",
            scalars.get("jetsamproperties category") == "daemon",
        ),
        (
            "environment",
            environment
            == {
                "OSLogRateLimit": "64",
                "XPC_SERVICE_NAME": config.label,
            },
        ),
    )
    for code, matches in checks:
        if not matches:
            return code
    return None


def _launchd_definition_matches(
    config: ServiceConfig, domain: str, output: str
) -> bool:
    """Parse the bounded launchctl-print fields that authorize execution."""

    return _launchd_definition_mismatch_code(config, domain, output) is None


def _launchctl_binary_qualified() -> bool:
    """Pin the human-only print grammar to one reviewed system binary."""

    descriptor = -1
    try:
        descriptor = os.open(LAUNCHCTL_PATH, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            return False
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest() == QUALIFIED_LAUNCHCTL_SHA256
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _launchctl_unqualified(
    action: str, config: ServiceConfig, *, installed: bool
) -> ServiceResult:
    """Return the typed pre-mutation boundary for an unknown launchctl build."""

    return ServiceResult(
        action,
        ServiceState.UNAVAILABLE,
        config.label,
        installed,
        detail="launchctl_binary_unqualified",
    )


def _unavailable(
    action: str, config: ServiceConfig, plist_path: Path, detail: str
) -> ServiceResult:
    return ServiceResult(
        action=action,
        state=ServiceState.UNAVAILABLE,
        label=config.label,
        installed=_plist_installed(plist_path),
        detail=detail,
    )


def _plist_installed(plist_path: Path) -> bool:
    """Report installation only after the same no-follow target admission."""

    try:
        with _open_plist_directory(plist_path) as directory:
            return _plist_installed_at(directory)
    except (ConfigurationError, OSError):
        return False


def _plist_installed_at(directory: _PlistDirectory) -> bool:
    try:
        return _validate_plist_at(directory) is not None
    except (ConfigurationError, OSError):
        return False


def _planned(action: str, config: ServiceConfig, plist_path: Path) -> ServiceResult:
    return ServiceResult(
        action=action,
        state=ServiceState.PLANNED,
        label=config.label,
        installed=_plist_installed(plist_path),
        detail=f"{action}_dry_run",
    )


def _target_rejected(action: str, config: ServiceConfig) -> ServiceResult:
    return ServiceResult(
        action,
        ServiceState.ERROR,
        config.label,
        False,
        detail="plist_target_rejected",
    )


def _sidecar_primary_plist_collision(
    config: ServiceConfig, plist_path: Path
) -> bool:
    if config.role is not ServiceRole.ORCH_SIDECAR:
        return False
    primary_path = default_plist_path(
        label=DEFAULT_LABEL, home=config._account_home
    )
    return Path(plist_path) == primary_path


def _lifecycle_error(
    action: str, config: ServiceConfig, plist_path: Path, detail: str
) -> ServiceResult:
    return ServiceResult(
        action,
        ServiceState.ERROR,
        config.label,
        _plist_installed(plist_path),
        detail=detail,
    )


def _with_recovery_records(
    result: ServiceResult, *records: RecoveryRecord | None
) -> ServiceResult:
    admitted = tuple(record for record in records if record is not None)
    if not admitted:
        return result
    return replace(
        result,
        recovery_records=(*result.recovery_records, *admitted),
    )


def _establish_durable_hold(
    config: ServiceConfig,
    *,
    runner: Runner,
    domain: str,
) -> str | None:
    """Disable, boot out, and prove the retained LaunchAgent cannot respawn."""

    disabled_error = _set_launchd_disabled(
        config,
        runner,
        domain,
        disabled=True,
    )
    if disabled_error is not None:
        return disabled_error
    try:
        removed = _run_launchctl(runner, ["bootout", _target(config, domain)])
    except (OSError, subprocess.TimeoutExpired):
        return "launchctl_bootout_unavailable_disabled"
    if removed.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
        return "launchctl_bootout_error_disabled"
    try:
        absent = _wait_for_launchd_label_absence(
            config,
            runner=runner,
            domain=domain,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "stop_confirmation_unavailable_disabled"
    if absent.returncode == 0:
        return "stop_confirmation_still_loaded_disabled"
    if absent.returncode not in _STATUS_NOT_LOADED_RETURN_CODES:
        return "stop_confirmation_error_disabled"
    observed, detail = _launchd_disabled_override(config, runner, domain)
    if detail is not None:
        return detail
    if observed is not True:
        return "launchctl_disabled_state_mismatch"
    return None


def _prepare_activation(
    action: str,
    config: ServiceConfig,
    directory: _PlistDirectory,
    *,
    runner: Runner,
    domain: str,
) -> tuple[bool | None, ServiceResult | None]:
    """Open one admitted activation transition and remember its prior hold."""

    prior_disabled, read_error = _launchd_disabled_override(config, runner, domain)
    if read_error is not None:
        return None, ServiceResult(
            action,
            ServiceState.UNAVAILABLE,
            config.label,
            _plist_installed_at(directory),
            detail=read_error,
        )
    enable_error = _set_launchd_disabled(
        config,
        runner,
        domain,
        disabled=False,
    )
    if enable_error is not None:
        if prior_disabled:
            restore_error = _establish_durable_hold(
                config,
                runner=runner,
                domain=domain,
            )
            if restore_error is not None:
                enable_error = f"{enable_error}_hold_restore_{restore_error}"
        return prior_disabled, ServiceResult(
            action,
            (
                ServiceState.UNAVAILABLE
                if enable_error.endswith("_unavailable")
                else ServiceState.ERROR
            ),
            config.label,
            _plist_installed_at(directory),
            detail=enable_error,
        )
    return prior_disabled, None


def _finish_activation(
    result: ServiceResult,
    prior_disabled: bool,
    config: ServiceConfig,
    *,
    runner: Runner,
    domain: str,
) -> ServiceResult:
    """Restore a pre-existing disabled hold after a failed activation."""

    if (
        result.state
        in {
            ServiceState.INSTALLED,
            ServiceState.LOADED,
            ServiceState.RUNNING,
        }
        or not prior_disabled
    ):
        return result
    hold_error = _establish_durable_hold(config, runner=runner, domain=domain)
    if hold_error is None:
        return result
    return replace(
        result,
        state=(
            ServiceState.UNAVAILABLE
            if "unavailable" in hold_error
            else ServiceState.ERROR
        ),
        detail=f"{result.detail or 'activation_error'}_hold_restore_{hold_error}",
    )


def _contain_fallback_failure(
    action: str,
    config: ServiceConfig,
    directory: _PlistDirectory,
    *,
    runner: Runner,
    domain: str,
    detail: str,
    recovery_records: tuple[RecoveryRecord, ...] = (),
    failure_state: ServiceState = ServiceState.ERROR,
    restore_disabled_hold: bool = False,
) -> ServiceResult:
    try:
        removed = _run_launchctl(runner, ["bootout", _target(config, domain)])
    except (OSError, subprocess.TimeoutExpired):
        unavailable_detail = f"{detail}_containment_unavailable"
        if restore_disabled_hold:
            hold_error = _establish_durable_hold(
                config,
                runner=runner,
                domain=domain,
            )
            if hold_error is not None:
                unavailable_detail = f"{unavailable_detail}_hold_{hold_error}"
        return _with_recovery_records(
            ServiceResult(
                action,
                ServiceState.UNAVAILABLE,
                config.label,
                _plist_installed_at(directory),
                detail=unavailable_detail,
            ),
            *recovery_records,
        )
    if removed.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
        detail = f"{detail}_containment_bootout_error"
    if restore_disabled_hold:
        hold_error = _establish_durable_hold(config, runner=runner, domain=domain)
        if hold_error is not None:
            detail = f"{detail}_hold_{hold_error}"
    return _with_recovery_records(
        ServiceResult(
            action,
            failure_state,
            config.label,
            _plist_installed_at(directory),
            detail=detail,
        ),
        *recovery_records,
    )


def _snapshot_plist_at(directory: _PlistDirectory) -> _PlistSnapshot | None:
    expected_uid = os.getuid()
    before = _validate_plist_at(directory)
    if before is None:
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(directory.name, flags, dir_fd=directory.fd)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != expected_uid
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise ConfigurationError("plist changed during no-follow admission")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return _PlistSnapshot(
            b"".join(chunks),
            stat.S_IMODE(opened.st_mode),
            opened.st_dev,
            opened.st_ino,
        )
    finally:
        os.close(fd)


def _candidate_matches(
    directory: _PlistDirectory,
    *,
    candidate: _PlistSnapshot,
    rendered: bytes,
    expected_label: str,
) -> bool:
    """Verify exact candidate inode, bytes, and fixed launchd label."""

    try:
        observed = _snapshot_plist_at(directory)
        if not _same_snapshot_identity(observed, candidate):
            return False
        if (
            observed is None
            or observed.mode != candidate.mode
            or observed.content != rendered
        ):
            return False
        parsed = plistlib.loads(observed.content)
    except (ConfigurationError, OSError, ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed.get("Label") == expected_label


def _snapshot_label_matches(snapshot: _PlistSnapshot, *, expected_label: str) -> bool:
    try:
        parsed = plistlib.loads(snapshot.content)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed.get("Label") == expected_label


def _definition_matches(
    directory: _PlistDirectory,
    *,
    candidate: _PlistSnapshot,
    rendered: bytes,
    expected_label: str,
) -> bool:
    """Validate parent, inode, mode, exact bytes, and fixed launchd label."""

    return _parent_path_matches(directory) and _candidate_matches(
        directory,
        candidate=candidate,
        rendered=rendered,
        expected_label=expected_label,
    )


def _recovery_record(
    directory: _PlistDirectory,
    leaf: str,
    *,
    expected_label: str,
    artifact_kind: RecoveryArtifactKind,
) -> RecoveryRecord | None:
    """Snapshot a relative leaf into an identity record.

    ``leaf`` is only a sanitized locator.  The device, inode, digest, and mode
    are the identity proof that every future consumer must revalidate.
    """

    recovery = _PlistDirectory(
        directory.fd,
        directory.path,
        leaf,
        directory.device,
        directory.inode,
    )
    try:
        observed = _snapshot_plist_at(recovery)
    except (ConfigurationError, OSError):
        return None
    if observed is None:
        return None
    label_validated = False
    try:
        parsed = plistlib.loads(observed.content)
        label_validated = (
            isinstance(parsed, dict) and parsed.get("Label") == expected_label
        )
    except Exception:
        pass
    if artifact_kind is RecoveryArtifactKind.RESTORABLE_PLIST and not label_validated:
        return None
    if artifact_kind is RecoveryArtifactKind.PARTIAL_ATOMIC_TEMP:
        # A partial/interrupted write is evidence for later cleanup only.  It
        # is never promoted into a service definition, even if its prefix
        # happens to parse as a plist.
        label_validated = False
    return RecoveryRecord(
        leaf=leaf,
        device=observed.device,
        inode=observed.inode,
        sha256=hashlib.sha256(observed.content).hexdigest(),
        mode=observed.mode,
        expected_label=expected_label,
        artifact_kind=artifact_kind,
        label_validated=label_validated,
    )


def _recovery_record_matches(
    directory: _PlistDirectory,
    record: RecoveryRecord,
    *,
    expected_label: str,
    require_canonical_absent: bool,
) -> bool:
    """Reopen a record's locator and revalidate every identity field."""

    if record.expected_label != expected_label:
        return False
    observed = _recovery_record(
        directory,
        record.leaf,
        expected_label=expected_label,
        artifact_kind=record.artifact_kind,
    )
    if observed != record:
        return False
    if not require_canonical_absent:
        return True
    try:
        return _validate_plist_at(directory) is None
    except (ConfigurationError, OSError):
        return False


def _quarantine_delete_admitted(
    directory: _PlistDirectory,
    admitted: _PlistSnapshot,
    *,
    expected_label: str,
) -> RecoveryRecord | None:
    """Move an admitted inode and return a revalidatable identity record."""

    quarantine_name = f".{directory.name}.remove-{secrets.token_hex(16)}"
    os.rename(
        directory.name,
        quarantine_name,
        src_dir_fd=directory.fd,
        dst_dir_fd=directory.fd,
    )
    os.fsync(directory.fd)
    quarantine = _PlistDirectory(
        directory.fd,
        directory.path,
        quarantine_name,
        directory.device,
        directory.inode,
    )
    try:
        moved = _snapshot_plist_at(quarantine)
    except (ConfigurationError, OSError):
        # The moved entry is intentionally preserved under the unpredictable
        # quarantine name when its identity cannot be proven.
        return None
    if not _same_snapshot_identity(moved, admitted):
        # Never delete a replacement moved during the final source-name race.
        try:
            os.link(
                quarantine_name,
                directory.name,
                src_dir_fd=directory.fd,
                dst_dir_fd=directory.fd,
                follow_symlinks=False,
            )
            os.fsync(directory.fd)
        except OSError:
            # Either another replacement already occupies the service name or
            # the moved object cannot be hard-linked.  The quarantine remains.
            pass
        return None
    final = _snapshot_plist_at(quarantine)
    if not _same_snapshot_identity(final, admitted):
        return None
    record = _recovery_record(
        directory,
        quarantine_name,
        expected_label=expected_label,
        artifact_kind=RecoveryArtifactKind.RESTORABLE_PLIST,
    )
    if record is None:
        return None
    if (
        record.device != admitted.device
        or record.inode != admitted.inode
        or record.sha256 != hashlib.sha256(admitted.content).hexdigest()
        or record.mode != admitted.mode
        or record.expected_label != expected_label
    ):
        return None
    if not _recovery_record_matches(
        directory,
        record,
        expected_label=expected_label,
        require_canonical_absent=True,
    ):
        return None
    # Do not unlink the quarantine locator: under a same-UID threat model no
    # pathname operation remains identity-bound after its final check.  The
    # returned record, rather than the pathname, is the durable identity.
    os.fsync(directory.fd)
    return record


def _rollback_failed_install(
    config: ServiceConfig,
    plist_path: Path,
    directory: _PlistDirectory,
    previous: _PlistSnapshot | None,
    candidate: _PlistSnapshot | None,
    *,
    runner: Runner,
    domain: str,
    failure_state: ServiceState,
    failure_detail: str,
) -> ServiceResult:
    """Restore and re-register the prior plist, or remove a first candidate."""

    # A timed-out/failed bootstrap may still have registered the candidate.
    # Never replace or remove its plist until launchctl confirms it is gone.
    try:
        candidate_bootout = _run_launchctl(runner, ["bootout", _target(config, domain)])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ServiceResult(
            "install",
            ServiceState.ERROR,
            config.label,
            _plist_installed_at(directory),
            detail=f"{failure_detail}_rollback_unavailable",
        )
    if candidate_bootout.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
        return ServiceResult(
            "install",
            ServiceState.ERROR,
            config.label,
            _plist_installed_at(directory),
            detail=f"{failure_detail}_rollback_bootout_error",
        )

    if previous is None:
        recovery_record = None
        try:
            current = _validate_plist_at(directory)
            if current is not None:
                if candidate is None or not _same_identity(current, candidate):
                    raise ConfigurationError("rollback candidate identity changed")
                recovery_record = _quarantine_delete_admitted(
                    directory,
                    candidate,
                    expected_label=config.label,
                )
                if recovery_record is None:
                    raise ConfigurationError("rollback candidate identity changed")
        except (ConfigurationError, OSError):
            return ServiceResult(
                "install",
                ServiceState.ERROR,
                config.label,
                _plist_installed_at(directory),
                detail=f"{failure_detail}_rollback_quarantine_error",
            )
        if current is not None and (
            recovery_record is None
            or candidate is None
            or not _recovery_record_matches(
                directory,
                recovery_record,
                expected_label=config.label,
                require_canonical_absent=True,
            )
        ):
            return ServiceResult(
                "install",
                ServiceState.ERROR,
                config.label,
                _plist_installed_at(directory),
                detail=f"{failure_detail}_rollback_quarantine_changed",
                recovery_records=(recovery_record,) if recovery_record else (),
            )
        return ServiceResult(
            "install",
            failure_state,
            config.label,
            False,
            detail=f"{failure_detail}_candidate_quarantined",
            recovery_records=(
                (recovery_record,)
                if current is not None and recovery_record is not None
                else ()
            ),
        )

    try:
        if candidate is None:
            raise ConfigurationError("rollback candidate identity unavailable")
        restored_snapshot = _atomic_write_at(
            directory,
            previous.content,
            previous.mode,
            expected=candidate,
            expected_label=config.label,
        )
    except AtomicWriteError as exc:
        return ServiceResult(
            "install",
            ServiceState.ERROR,
            config.label,
            _plist_installed_at(directory),
            detail=f"{failure_detail}_rollback_write_error",
            recovery_records=(exc.recovery_record,) if exc.recovery_record else (),
        )
    except Exception:
        return ServiceResult(
            "install",
            ServiceState.ERROR,
            config.label,
            _plist_installed_at(directory),
            detail=f"{failure_detail}_rollback_write_error",
        )
    if not _parent_path_matches(directory) or not _candidate_matches(
        directory,
        candidate=restored_snapshot,
        rendered=previous.content,
        expected_label=config.label,
    ):
        return ServiceResult(
            "install",
            ServiceState.ERROR,
            config.label,
            _plist_installed_at(directory),
            detail=f"{failure_detail}_rollback_restored_identity_error",
        )
    bootstrap_record = None

    def contain_restored_registration(
        detail: str, state: ServiceState
    ) -> ServiceResult:
        """Boot out an unverified rollback registration before returning."""

        try:
            removed = _run_launchctl(runner, ["bootout", _target(config, domain)])
        except (OSError, subprocess.TimeoutExpired):
            return _with_recovery_records(
                ServiceResult(
                    "install",
                    ServiceState.ERROR,
                    config.label,
                    _plist_installed_at(directory),
                    detail=f"{detail}_bootout_unavailable",
                ),
                bootstrap_record,
            )
        suffix = "contained"
        result_state = state
        if removed.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
            suffix = "bootout_error"
            result_state = ServiceState.ERROR
        return _with_recovery_records(
            ServiceResult(
                "install",
                result_state,
                config.label,
                _plist_installed_at(directory),
                detail=f"{detail}_{suffix}",
            ),
            bootstrap_record,
        )

    try:
        with _bootstrap_stage(
            directory,
            previous.content,
            expected_label=config.label,
        ) as stage:
            bootstrap_record = stage.recovery_record
            restored = _bootstrap_launchd_job(
                config, stage, runner=runner, domain=domain
            )
    except AtomicWriteError as exc:
        return ServiceResult(
            "install",
            ServiceState.ERROR,
            config.label,
            _plist_installed_at(directory),
            detail=f"{failure_detail}_rollback_bootstrap_stage_unavailable",
            recovery_records=(exc.recovery_record,) if exc.recovery_record else (),
        )
    except ConfigurationError:
        return contain_restored_registration(
            f"{failure_detail}_rollback_bootstrap_stage_changed",
            ServiceState.ERROR,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return contain_restored_registration(
            f"{failure_detail}_rollback_unavailable",
            ServiceState.UNAVAILABLE,
        )
    if restored.returncode != 0:
        return contain_restored_registration(
            f"{failure_detail}_rollback_error",
            ServiceState.ERROR,
        )
    try:
        confirmed = _confirm_launchd_label(config, runner=runner, domain=domain)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return contain_restored_registration(
            f"{failure_detail}_rollback_confirmation_unavailable",
            ServiceState.UNAVAILABLE,
        )
    if confirmed.returncode != 0:
        return contain_restored_registration(
            f"{failure_detail}_rollback_confirmation_error",
            ServiceState.ERROR,
        )
    if not _parent_path_matches(directory) or not _candidate_matches(
        directory,
        candidate=restored_snapshot,
        rendered=previous.content,
        expected_label=config.label,
    ):
        try:
            removed = _run_launchctl(runner, ["bootout", _target(config, domain)])
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return _with_recovery_records(
                ServiceResult(
                    "install",
                    ServiceState.ERROR,
                    config.label,
                    _plist_installed_at(directory),
                    detail=(f"{failure_detail}_rollback_postconfirm_unavailable"),
                ),
                bootstrap_record,
            )
        suffix = "identity_error"
        if removed.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
            suffix = "bootout_error"
        return _with_recovery_records(
            ServiceResult(
                "install",
                ServiceState.ERROR,
                config.label,
                _plist_installed_at(directory),
                detail=f"{failure_detail}_rollback_postconfirm_{suffix}",
            ),
            bootstrap_record,
        )
    return _with_recovery_records(
        ServiceResult(
            "install",
            failure_state,
            config.label,
            True,
            True,
            detail=f"{failure_detail}_rolled_back",
        ),
        bootstrap_record,
    )


def install_service(
    config: ServiceConfig,
    plist_path: Path,
    *,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    uid: int | None = None,
    domain: str | None = None,
    session_token_authority_context: object = None,
    rotate_session_token: bool = False,
    command_config_prepared: bool | None = None,
) -> ServiceResult:
    if _sidecar_primary_plist_collision(config, plist_path):
        return _target_rejected("install", config)
    if dry_run:
        return _planned("install", config, plist_path)
    try:
        with _lifecycle_lock(config):
            try:
                with _open_plist_directory(plist_path) as directory:
                    previous = _snapshot_plist_at(directory)
                    if not _launchctl_binary_qualified():
                        return _launchctl_unqualified(
                            "install", config, installed=previous is not None
                        )
                    selected_domain = _selected_domain(config, runner, uid, domain)
                    if config.role is ServiceRole.ORCH_SIDECAR and (
                        rotate_session_token
                        or session_token_authority_context is not None
                    ):
                        return _session_token_source_unavailable(
                            "install",
                            config,
                            directory,
                            runner=runner,
                            domain=selected_domain,
                        )
                    if command_config_prepared is False:
                        return _session_token_source_unavailable(
                            "install",
                            config,
                            directory,
                            runner=runner,
                            domain=selected_domain,
                        )
                    if not _prepare_session_token_source(
                        config,
                        authority_context=session_token_authority_context,
                        rotate=rotate_session_token,
                        prepare_config=(
                            command_config_prepared is None
                            and config.role is not ServiceRole.ORCH_SIDECAR
                        ),
                    ):
                        return _session_token_source_unavailable(
                            "install",
                            config,
                            directory,
                            runner=runner,
                            domain=selected_domain,
                        )
                    try:
                        # A stale registration must not retain an older pinned worktree.
                        bootout = _run_launchctl(
                            runner, ["bootout", _target(config, selected_domain)]
                        )
                    except FileNotFoundError:
                        return ServiceResult(
                            "install",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_not_found",
                        )
                    except subprocess.TimeoutExpired:
                        return ServiceResult(
                            "install",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_timeout",
                        )
                    if bootout.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
                        return ServiceResult(
                            "install",
                            ServiceState.ERROR,
                            config.label,
                            previous is not None,
                            detail="launchctl_bootout_error",
                        )

                    rendered: bytes | None = None
                    candidate = previous
                    try:
                        rendered = render_launchd_plist(config).encode("utf-8")
                        candidate = _atomic_write_at(
                            directory,
                            rendered,
                            PRIVATE_FILE_MODE,
                            expected=previous,
                            expected_label=config.label,
                        )
                    except AtomicWriteError as exc:
                        candidate = exc.candidate
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.ERROR,
                            failure_detail="filesystem_error",
                        )
                        return _with_recovery_records(
                            rollback_result, exc.recovery_record
                        )
                    except Exception:
                        candidate = previous
                        return _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.ERROR,
                            failure_detail="filesystem_error",
                        )
                    if not _parent_path_matches(directory):
                        return _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.ERROR,
                            failure_detail="plist_parent_changed",
                        )
                    if (
                        rendered is None
                        or candidate is None
                        or not _candidate_matches(
                            directory,
                            candidate=candidate,
                            rendered=rendered,
                            expected_label=config.label,
                        )
                    ):
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.ERROR,
                            failure_detail="plist_candidate_changed",
                        )
                        return rollback_result
                    prior_disabled, activation_error = _prepare_activation(
                        "install",
                        config,
                        directory,
                        runner=runner,
                        domain=selected_domain,
                    )
                    restore_disabled = bool(prior_disabled)

                    def finish_install(result: ServiceResult) -> ServiceResult:
                        return _finish_activation(
                            result,
                            restore_disabled,
                            config,
                            runner=runner,
                            domain=selected_domain,
                        )

                    if activation_error is not None:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=activation_error.state,
                            failure_detail=activation_error.detail
                            or "launchctl_enable_error",
                        )
                        return finish_install(rollback_result)
                    bootstrap_record = None
                    try:
                        with _bootstrap_stage(
                            directory,
                            rendered,
                            expected_label=config.label,
                        ) as stage:
                            bootstrap_record = stage.recovery_record
                            completed = _bootstrap_launchd_job(
                                config,
                                stage,
                                runner=runner,
                                domain=selected_domain,
                            )
                    except AtomicWriteError as exc:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.UNAVAILABLE,
                            failure_detail="bootstrap_stage_unavailable",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, exc.recovery_record)
                        )
                    except ConfigurationError:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.ERROR,
                            failure_detail="bootstrap_stage_changed",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, bootstrap_record)
                        )
                    except FileNotFoundError:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.UNAVAILABLE,
                            failure_detail="launchctl_not_found",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, bootstrap_record)
                        )
                    except subprocess.TimeoutExpired:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.UNAVAILABLE,
                            failure_detail="launchctl_timeout",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, bootstrap_record)
                        )
                    if completed.returncode != 0:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.ERROR,
                            failure_detail="launchctl_bootstrap_error",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, bootstrap_record)
                        )
                    try:
                        confirmed = _confirm_launchd_label(
                            config, runner=runner, domain=selected_domain
                        )
                    except FileNotFoundError:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.UNAVAILABLE,
                            failure_detail="launchctl_confirmation_not_found",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, bootstrap_record)
                        )
                    except subprocess.TimeoutExpired:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.UNAVAILABLE,
                            failure_detail="launchctl_confirmation_timeout",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, bootstrap_record)
                        )
                    if confirmed.returncode != 0:
                        rollback_result = _rollback_failed_install(
                            config,
                            plist_path,
                            directory,
                            previous,
                            candidate,
                            runner=runner,
                            domain=selected_domain,
                            failure_state=ServiceState.ERROR,
                            failure_detail="launchctl_confirmation_error",
                        )
                        return finish_install(
                            _with_recovery_records(rollback_result, bootstrap_record)
                        )
                    if (
                        not _parent_path_matches(directory)
                        or rendered is None
                        or candidate is None
                        or not _candidate_matches(
                            directory,
                            candidate=candidate,
                            rendered=rendered,
                            expected_label=config.label,
                        )
                    ):
                        return finish_install(
                            _rollback_failed_install(
                                config,
                                plist_path,
                                directory,
                                previous,
                                candidate,
                                runner=runner,
                                domain=selected_domain,
                                failure_state=ServiceState.ERROR,
                                failure_detail="launchctl_postconfirm_identity_error",
                            )
                        )
                    return _with_recovery_records(
                        ServiceResult(
                            "install",
                            ServiceState.INSTALLED,
                            config.label,
                            True,
                            True,
                        ),
                        bootstrap_record,
                    )
            except (ConfigurationError, OSError):
                return _target_rejected("install", config)
    except LifecycleBusyError:
        return _lifecycle_error("install", config, plist_path, "lifecycle_busy")
    except (ConfigurationError, OSError):
        return _lifecycle_error("install", config, plist_path, "lifecycle_lock_error")


def uninstall_service(
    config: ServiceConfig,
    plist_path: Path,
    *,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    uid: int | None = None,
    domain: str | None = None,
) -> ServiceResult:
    if _sidecar_primary_plist_collision(config, plist_path):
        return _target_rejected("uninstall", config)
    if dry_run:
        return _planned("uninstall", config, plist_path)
    try:
        with _lifecycle_lock(config):
            try:
                with _open_plist_directory(plist_path) as directory:
                    admitted = _snapshot_plist_at(directory)
                    if admitted is None:
                        return ServiceResult(
                            "uninstall",
                            ServiceState.NOT_INSTALLED,
                            config.label,
                            False,
                        )
                    if not _snapshot_label_matches(
                        admitted, expected_label=config.label
                    ):
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="plist_label_mismatch",
                        )
                    if not _launchctl_binary_qualified():
                        return _launchctl_unqualified(
                            "uninstall", config, installed=True
                        )
                    selected_domain = _selected_domain(config, runner, uid, domain)
                    try:
                        completed = _run_launchctl(
                            runner, ["bootout", _target(config, selected_domain)]
                        )
                    except FileNotFoundError:
                        return ServiceResult(
                            "uninstall",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_not_found",
                        )
                    except subprocess.TimeoutExpired:
                        return ServiceResult(
                            "uninstall",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_timeout",
                        )
                    if completed.returncode not in _BOOTOUT_ACCEPTABLE_RETURN_CODES:
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="launchctl_bootout_error",
                        )
                    if not _parent_path_matches(directory):
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="plist_parent_changed",
                        )
                    try:
                        current = _validate_plist_at(directory)
                    except (ConfigurationError, OSError):
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="plist_replaced_after_bootout",
                        )
                    if not _same_identity(current, admitted):
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="plist_replaced_after_bootout",
                        )
                    try:
                        recovery_record = _quarantine_delete_admitted(
                            directory,
                            admitted,
                            expected_label=config.label,
                        )
                    except OSError:
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="plist_quarantine_error",
                        )
                    if recovery_record is None:
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="plist_replaced_after_bootout",
                        )
                    if not _recovery_record_matches(
                        directory,
                        recovery_record,
                        expected_label=config.label,
                        require_canonical_absent=True,
                    ):
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="quarantine_recovery_changed",
                            recovery_records=(recovery_record,),
                        )
                    try:
                        remaining = _validate_plist_at(directory)
                    except (ConfigurationError, OSError):
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="plist_replaced_during_removal",
                        )
                    if remaining is not None:
                        return ServiceResult(
                            "uninstall",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="plist_replaced_during_removal",
                        )
                    return ServiceResult(
                        "uninstall",
                        ServiceState.REMOVED_QUARANTINED,
                        config.label,
                        False,
                        detail="plist_quarantined",
                        recovery_records=(recovery_record,),
                    )
            except (ConfigurationError, OSError):
                return _target_rejected("uninstall", config)
    except LifecycleBusyError:
        return _lifecycle_error("uninstall", config, plist_path, "lifecycle_busy")
    except (ConfigurationError, OSError):
        return _lifecycle_error("uninstall", config, plist_path, "lifecycle_lock_error")


def start_service(
    config: ServiceConfig,
    plist_path: Path,
    *,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    uid: int | None = None,
    domain: str | None = None,
) -> ServiceResult:
    if _sidecar_primary_plist_collision(config, plist_path):
        return _target_rejected("start", config)
    if dry_run:
        return _planned("start", config, plist_path)
    try:
        with _lifecycle_lock(config):
            try:
                with _open_plist_directory(plist_path) as directory:
                    admitted = _snapshot_plist_at(directory)
                    if admitted is None:
                        return ServiceResult(
                            "start",
                            ServiceState.NOT_INSTALLED,
                            config.label,
                            False,
                            detail="plist_missing",
                        )
                    if not _launchctl_binary_qualified():
                        return _launchctl_unqualified("start", config, installed=True)
                    selected_domain = _selected_domain(config, runner, uid, domain)
                    if not _prepare_session_token_source(config):
                        return _session_token_source_unavailable(
                            "start",
                            config,
                            directory,
                            runner=runner,
                            domain=selected_domain,
                        )
                    expected_render = render_launchd_plist(config).encode("utf-8")
                    if not _definition_matches(
                        directory,
                        candidate=admitted,
                        rendered=expected_render,
                        expected_label=config.label,
                    ):
                        return ServiceResult(
                            "start",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="plist_definition_mismatch",
                        )
                    try:
                        registered = _confirm_launchd_label(
                            config, runner=runner, domain=selected_domain
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        return ServiceResult(
                            "start",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            True,
                            detail="registered_definition_unavailable",
                        )
                    if registered.returncode == 78:
                        return ServiceResult(
                            "start",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="registered_definition_mismatch",
                        )
                    if (
                        registered.returncode != 0
                        and registered.returncode not in _STATUS_NOT_LOADED_RETURN_CODES
                    ):
                        return ServiceResult(
                            "start",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="registered_definition_status_error",
                        )
                    prior_disabled, activation_error = _prepare_activation(
                        "start",
                        config,
                        directory,
                        runner=runner,
                        domain=selected_domain,
                    )
                    if activation_error is not None:
                        return activation_error
                    restore_disabled_hold = bool(prior_disabled)
                    # Never label-kick a separately observed registration: a
                    # same-UID replacement can win between print and kickstart.
                    # Re-activate only by booting out the admitted label and
                    # bootstrapping our privately staged RunAtLoad definition.
                    fallback_bootstrap = True
                    bootstrap_records: tuple[RecoveryRecord, ...] = ()
                    try:
                        completed = registered
                        if registered.returncode == 0:
                            completed = _run_launchctl(
                                runner,
                                ["bootout", _target(config, selected_domain)],
                            )
                            if (
                                completed.returncode
                                not in _BOOTOUT_ACCEPTABLE_RETURN_CODES
                            ):
                                return _contain_fallback_failure(
                                    "start",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="prebootstrap_bootout_error",
                                )
                            completed = _wait_for_launchd_label_absence(
                                config,
                                runner=runner,
                                domain=selected_domain,
                            )
                            if completed.returncode == 0:
                                return _contain_fallback_failure(
                                    "start",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="prebootstrap_still_loaded",
                                )
                            if completed.returncode not in (
                                _STATUS_NOT_LOADED_RETURN_CODES
                            ):
                                return _contain_fallback_failure(
                                    "start",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="prebootstrap_status_error",
                                )
                        if (
                            fallback_bootstrap
                            or completed.returncode in _RETRY_NOT_LOADED_RETURN_CODES
                        ):
                            fallback_bootstrap = True
                            try:
                                with _bootstrap_stage(
                                    directory,
                                    expected_render,
                                    expected_label=config.label,
                                ) as stage:
                                    bootstrap_records = (stage.recovery_record,)
                                    completed = _bootstrap_launchd_job(
                                        config,
                                        stage,
                                        runner=runner,
                                        domain=selected_domain,
                                    )
                            except AtomicWriteError as exc:
                                records = (
                                    (exc.recovery_record,)
                                    if exc.recovery_record
                                    else ()
                                )
                                return _contain_fallback_failure(
                                    "start",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="bootstrap_stage_unavailable",
                                    recovery_records=records,
                                    failure_state=ServiceState.UNAVAILABLE,
                                )
                            except ConfigurationError:
                                return _contain_fallback_failure(
                                    "start",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="bootstrap_stage_changed",
                                    recovery_records=bootstrap_records,
                                )
                            if completed.returncode == 0:
                                completed = _confirm_launchd_label(
                                    config, runner=runner, domain=selected_domain
                                )
                                if completed.returncode == 0:
                                    if not _definition_matches(
                                        directory,
                                        candidate=admitted,
                                        rendered=expected_render,
                                        expected_label=config.label,
                                    ):
                                        return _contain_fallback_failure(
                                            "start",
                                            config,
                                            directory,
                                            runner=runner,
                                            domain=selected_domain,
                                            restore_disabled_hold=restore_disabled_hold,
                                            detail="postbootstrap_identity_error",
                                            recovery_records=bootstrap_records,
                                        )
                                    # RunAtLoad + KeepAlive start the exact
                                    # staged definition. A later label-only
                                    # kick would reopen the substitution race.
                    except OSError:
                        if fallback_bootstrap:
                            return _contain_fallback_failure(
                                "start",
                                config,
                                directory,
                                runner=runner,
                                domain=selected_domain,
                                restore_disabled_hold=restore_disabled_hold,
                                detail="fallback_launchctl_unavailable",
                                recovery_records=bootstrap_records,
                                failure_state=ServiceState.UNAVAILABLE,
                            )
                        return ServiceResult(
                            "start",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_not_found",
                        )
                    except subprocess.TimeoutExpired:
                        if fallback_bootstrap:
                            return _contain_fallback_failure(
                                "start",
                                config,
                                directory,
                                runner=runner,
                                domain=selected_domain,
                                restore_disabled_hold=restore_disabled_hold,
                                detail="fallback_launchctl_timeout",
                                recovery_records=bootstrap_records,
                            )
                        return ServiceResult(
                            "start",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_timeout",
                        )
                    if completed.returncode != 0:
                        if fallback_bootstrap:
                            return _contain_fallback_failure(
                                "start",
                                config,
                                directory,
                                runner=runner,
                                domain=selected_domain,
                                restore_disabled_hold=restore_disabled_hold,
                                detail="fallback_launchctl_error",
                                recovery_records=bootstrap_records,
                            )
                        return ServiceResult(
                            "start",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="launchctl_error",
                        )
                    if not _definition_matches(
                        directory,
                        candidate=admitted,
                        rendered=expected_render,
                        expected_label=config.label,
                    ):
                        return _contain_fallback_failure(
                            "start",
                            config,
                            directory,
                            runner=runner,
                            domain=selected_domain,
                            restore_disabled_hold=restore_disabled_hold,
                            detail="postbootstrap_identity_error",
                            recovery_records=bootstrap_records,
                        )
                    # Bootstrap + exact-label print prove the definition is
                    # loaded, not server usability.
                    return _with_recovery_records(
                        ServiceResult(
                            "start", ServiceState.LOADED, config.label, True, True
                        ),
                        *bootstrap_records,
                    )
            except (ConfigurationError, OSError):
                return _target_rejected("start", config)
    except LifecycleBusyError:
        return _lifecycle_error("start", config, plist_path, "lifecycle_busy")
    except (ConfigurationError, OSError):
        return _lifecycle_error("start", config, plist_path, "lifecycle_lock_error")


def stop_service(
    config: ServiceConfig,
    plist_path: Path,
    *,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    uid: int | None = None,
    domain: str | None = None,
) -> ServiceResult:
    if _sidecar_primary_plist_collision(config, plist_path):
        return _target_rejected("stop", config)
    if dry_run:
        return _planned("stop", config, plist_path)
    try:
        with _lifecycle_lock(config):
            try:
                with _open_plist_directory(plist_path) as directory:
                    if _validate_plist_at(directory) is None:
                        return ServiceResult(
                            "stop",
                            ServiceState.NOT_INSTALLED,
                            config.label,
                            False,
                        )
                    if not _launchctl_binary_qualified():
                        return _launchctl_unqualified("stop", config, installed=True)
                    selected_domain = _selected_domain(config, runner, uid, domain)
                    hold_error = _establish_durable_hold(
                        config,
                        runner=runner,
                        domain=selected_domain,
                    )
                    if hold_error is not None:
                        return ServiceResult(
                            "stop",
                            (
                                ServiceState.UNAVAILABLE
                                if "unavailable" in hold_error
                                else ServiceState.ERROR
                            ),
                            config.label,
                            True,
                            loaded=(
                                "bootout" in hold_error or "still_loaded" in hold_error
                            ),
                            detail=hold_error,
                        )
                    return ServiceResult(
                        "stop",
                        ServiceState.STOPPED,
                        config.label,
                        True,
                        detail="service_disabled",
                    )
            except (ConfigurationError, OSError):
                return _target_rejected("stop", config)
    except LifecycleBusyError:
        return _lifecycle_error("stop", config, plist_path, "lifecycle_busy")
    except (ConfigurationError, OSError):
        return _lifecycle_error("stop", config, plist_path, "lifecycle_lock_error")


def restart_service(
    config: ServiceConfig,
    plist_path: Path,
    *,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    uid: int | None = None,
    domain: str | None = None,
) -> ServiceResult:
    if _sidecar_primary_plist_collision(config, plist_path):
        return _target_rejected("restart", config)
    if dry_run:
        return _planned("restart", config, plist_path)
    try:
        with _lifecycle_lock(config):
            try:
                with _open_plist_directory(plist_path) as directory:
                    admitted = _snapshot_plist_at(directory)
                    if admitted is None:
                        return ServiceResult(
                            "restart",
                            ServiceState.NOT_INSTALLED,
                            config.label,
                            False,
                        )
                    if not _launchctl_binary_qualified():
                        return _launchctl_unqualified("restart", config, installed=True)
                    selected_domain = _selected_domain(config, runner, uid, domain)
                    if not _prepare_session_token_source(config):
                        return _session_token_source_unavailable(
                            "restart",
                            config,
                            directory,
                            runner=runner,
                            domain=selected_domain,
                        )
                    expected_render = render_launchd_plist(config).encode("utf-8")
                    if not _definition_matches(
                        directory,
                        candidate=admitted,
                        rendered=expected_render,
                        expected_label=config.label,
                    ):
                        return ServiceResult(
                            "restart",
                            ServiceState.ERROR,
                            config.label,
                            _plist_installed_at(directory),
                            detail="plist_definition_mismatch",
                        )
                    try:
                        registered = _confirm_launchd_label(
                            config, runner=runner, domain=selected_domain
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        return ServiceResult(
                            "restart",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            True,
                            detail="registered_definition_unavailable",
                        )
                    if registered.returncode == 78:
                        return ServiceResult(
                            "restart",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="registered_definition_mismatch",
                        )
                    if (
                        registered.returncode != 0
                        and registered.returncode not in _STATUS_NOT_LOADED_RETURN_CODES
                    ):
                        return ServiceResult(
                            "restart",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="registered_definition_status_error",
                        )
                    prior_disabled, activation_error = _prepare_activation(
                        "restart",
                        config,
                        directory,
                        runner=runner,
                        domain=selected_domain,
                    )
                    if activation_error is not None:
                        return activation_error
                    restore_disabled_hold = bool(prior_disabled)
                    # Restart uses the same substitution-safe activation as
                    # start: remove an admitted registration, then bootstrap
                    # the private RunAtLoad definition without a label kick.
                    fallback_bootstrap = True
                    bootstrap_records: tuple[RecoveryRecord, ...] = ()
                    try:
                        completed = registered
                        if registered.returncode == 0:
                            completed = _run_launchctl(
                                runner,
                                ["bootout", _target(config, selected_domain)],
                            )
                            if (
                                completed.returncode
                                not in _BOOTOUT_ACCEPTABLE_RETURN_CODES
                            ):
                                return _contain_fallback_failure(
                                    "restart",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="prebootstrap_bootout_error",
                                )
                            completed = _wait_for_launchd_label_absence(
                                config,
                                runner=runner,
                                domain=selected_domain,
                            )
                            if completed.returncode == 0:
                                return _contain_fallback_failure(
                                    "restart",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="prebootstrap_still_loaded",
                                )
                            if completed.returncode not in (
                                _STATUS_NOT_LOADED_RETURN_CODES
                            ):
                                return _contain_fallback_failure(
                                    "restart",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="prebootstrap_status_error",
                                )
                        if (
                            fallback_bootstrap
                            or completed.returncode in _RETRY_NOT_LOADED_RETURN_CODES
                        ):
                            fallback_bootstrap = True
                            try:
                                with _bootstrap_stage(
                                    directory,
                                    expected_render,
                                    expected_label=config.label,
                                ) as stage:
                                    bootstrap_records = (stage.recovery_record,)
                                    completed = _bootstrap_launchd_job(
                                        config,
                                        stage,
                                        runner=runner,
                                        domain=selected_domain,
                                    )
                            except AtomicWriteError as exc:
                                records = (
                                    (exc.recovery_record,)
                                    if exc.recovery_record
                                    else ()
                                )
                                return _contain_fallback_failure(
                                    "restart",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="bootstrap_stage_unavailable",
                                    recovery_records=records,
                                    failure_state=ServiceState.UNAVAILABLE,
                                )
                            except ConfigurationError:
                                return _contain_fallback_failure(
                                    "restart",
                                    config,
                                    directory,
                                    runner=runner,
                                    domain=selected_domain,
                                    restore_disabled_hold=restore_disabled_hold,
                                    detail="bootstrap_stage_changed",
                                    recovery_records=bootstrap_records,
                                )
                            if completed.returncode == 0:
                                completed = _confirm_launchd_label(
                                    config, runner=runner, domain=selected_domain
                                )
                                if completed.returncode == 0:
                                    if not _definition_matches(
                                        directory,
                                        candidate=admitted,
                                        rendered=expected_render,
                                        expected_label=config.label,
                                    ):
                                        return _contain_fallback_failure(
                                            "restart",
                                            config,
                                            directory,
                                            runner=runner,
                                            domain=selected_domain,
                                            restore_disabled_hold=restore_disabled_hold,
                                            detail="postbootstrap_identity_error",
                                            recovery_records=bootstrap_records,
                                        )
                                    # The bootstrap's RunAtLoad/KeepAlive is
                                    # the activation. Do not execute by label.
                    except OSError:
                        if fallback_bootstrap:
                            return _contain_fallback_failure(
                                "restart",
                                config,
                                directory,
                                runner=runner,
                                domain=selected_domain,
                                restore_disabled_hold=restore_disabled_hold,
                                detail="fallback_launchctl_unavailable",
                                recovery_records=bootstrap_records,
                                failure_state=ServiceState.UNAVAILABLE,
                            )
                        return ServiceResult(
                            "restart",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_not_found",
                        )
                    except subprocess.TimeoutExpired:
                        if fallback_bootstrap:
                            return _contain_fallback_failure(
                                "restart",
                                config,
                                directory,
                                runner=runner,
                                domain=selected_domain,
                                restore_disabled_hold=restore_disabled_hold,
                                detail="fallback_launchctl_timeout",
                                recovery_records=bootstrap_records,
                            )
                        return ServiceResult(
                            "restart",
                            ServiceState.UNAVAILABLE,
                            config.label,
                            _plist_installed_at(directory),
                            detail="launchctl_timeout",
                        )
                    if completed.returncode != 0:
                        if fallback_bootstrap:
                            return _contain_fallback_failure(
                                "restart",
                                config,
                                directory,
                                runner=runner,
                                domain=selected_domain,
                                restore_disabled_hold=restore_disabled_hold,
                                detail="fallback_launchctl_error",
                                recovery_records=bootstrap_records,
                            )
                        return ServiceResult(
                            "restart",
                            ServiceState.ERROR,
                            config.label,
                            True,
                            detail="launchctl_error",
                        )
                    if not _definition_matches(
                        directory,
                        candidate=admitted,
                        rendered=expected_render,
                        expected_label=config.label,
                    ):
                        return _contain_fallback_failure(
                            "restart",
                            config,
                            directory,
                            runner=runner,
                            domain=selected_domain,
                            restore_disabled_hold=restore_disabled_hold,
                            detail="postbootstrap_identity_error",
                            recovery_records=bootstrap_records,
                        )
                    return _with_recovery_records(
                        ServiceResult(
                            "restart",
                            ServiceState.LOADED,
                            config.label,
                            True,
                            True,
                        ),
                        *bootstrap_records,
                    )
            except (ConfigurationError, OSError):
                return _target_rejected("restart", config)
    except LifecycleBusyError:
        return _lifecycle_error("restart", config, plist_path, "lifecycle_busy")
    except (ConfigurationError, OSError):
        return _lifecycle_error("restart", config, plist_path, "lifecycle_lock_error")


def recover_config_service(
    config: ServiceConfig,
    plist_path: Path,
    *,
    request: ConfigRecoveryRequest,
    dry_run: bool = False,
) -> ServiceResult:
    """Resolve protected config recovery generations under the lifecycle lock."""

    action = "recover-config"
    if config.role is ServiceRole.ORCH_SIDECAR:
        return _target_rejected(action, config)
    if dry_run:
        return _planned(action, config, plist_path)
    try:
        with _lifecycle_lock(config):
            try:
                source = _admitted_checkout_module(
                    config,
                    "scripts.orch_next_hermes_session_token_source",
                    "scripts/orch_next_hermes_session_token_source.py",
                )

                def convert(
                    expectation: ConfigArtifactExpectation | None,
                ) -> object:
                    if expectation is None:
                        return None
                    return source.ConfigArtifactIdentity(
                        file_type=expectation.file_type,
                        uid=expectation.uid,
                        mode=expectation.mode,
                        device=expectation.device,
                        inode=expectation.inode,
                        links=1,
                    )

                protected_signals = {
                    signal.SIGHUP,
                    signal.SIGINT,
                    signal.SIGQUIT,
                    signal.SIGTERM,
                }
                previous_signal_mask = signal.pthread_sigmask(
                    signal.SIG_BLOCK,
                    protected_signals,
                )
                try:
                    outcome = source.recover_protected_command_config(
                        config.hermes_home,
                        recovery_identity=convert(request.recovery_identity),
                        recovery_disposition=request.recovery_disposition,
                        retired_identity=convert(request.retired_identity),
                        retired_disposition=request.retired_disposition,
                        active_identity=convert(request.active_identity),
                    )
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_signal_mask)
            except Exception:
                return _lifecycle_error(
                    action,
                    config,
                    plist_path,
                    "config_recovery_source_unavailable",
                )
            if outcome.recovered is True:
                return ServiceResult(
                    action,
                    ServiceState.RECOVERED,
                    config.label,
                    _plist_installed(plist_path),
                    detail=outcome.detail,
                )
            return ServiceResult(
                action,
                (
                    ServiceState.ERROR
                    if outcome.detail
                    == "session_token_config_recovery_rollback_failed"
                    else ServiceState.UNAVAILABLE
                ),
                config.label,
                _plist_installed(plist_path),
                detail=outcome.detail,
            )
    except LifecycleBusyError:
        return _lifecycle_error(action, config, plist_path, "lifecycle_busy")
    except (ConfigurationError, OSError):
        return _lifecycle_error(action, config, plist_path, "lifecycle_lock_error")


def refresh_session_token_command_config(
    config: ServiceConfig,
    plist_path: Path,
    *,
    current_config: ServiceConfig,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    uid: int | None = None,
    domain: str | None = None,
) -> ServiceResult:
    """Refresh command-source config while preserving the running serve process.

    This action deliberately has no launchd lifecycle operation.  It requires
    the fixed service label and a stable running PID before and after the
    protected config replacement, and rolls the config generation back when
    the new CommandSource is not ready or that runtime binding changes.
    """

    action = "refresh-session-token-command-config"
    if config.role is ServiceRole.ORCH_SIDECAR:
        return _target_rejected(action, config)
    if dry_run:
        return _planned(action, config, plist_path)
    try:
        with _lifecycle_lock(config):
            if (
                current_config.label != DEFAULT_LABEL
                or current_config.hermes_home != config.hermes_home
                or current_config._hermes_home_device != config._hermes_home_device
                or current_config._hermes_home_inode != config._hermes_home_inode
            ):
                return ServiceResult(
                    action,
                    ServiceState.UNAVAILABLE,
                    config.label,
                    False,
                    detail="current_service_identity_rejected",
                )
            initial = service_status(
                current_config,
                plist_path,
                runner=runner,
                uid=uid,
                domain=domain,
            )
            if not _running_refresh_binding(initial, current_config):
                return ServiceResult(
                    action,
                    ServiceState.UNAVAILABLE,
                    config.label,
                    initial.installed,
                    initial.loaded,
                    initial.pid,
                    detail="service_runtime_not_bound",
                )
            token_before = _session_token_path_identity(config)
            if token_before is None:
                return ServiceResult(
                    action,
                    ServiceState.UNAVAILABLE,
                    config.label,
                    initial.installed,
                    initial.loaded,
                    initial.pid,
                    detail="session_token_identity_unavailable",
                )
            try:
                source = _admitted_checkout_module(
                    config,
                    "scripts.orch_next_hermes_session_token_source",
                    "scripts/orch_next_hermes_session_token_source.py",
                )
                recovery_detail = _clear_refresh_config_recovery_slots(config, source)
                if recovery_detail is None:
                    return ServiceResult(
                        action,
                        ServiceState.UNAVAILABLE,
                        config.label,
                        initial.installed,
                        initial.loaded,
                        initial.pid,
                        detail="session_token_command_config_recovery_unavailable",
                    )
                with source._open_absolute_directory(
                    config.hermes_home,
                    exact_mode=source._DIRECTORY_MODE,
                ) as home_fd:
                    prior = source._read_optional_config_snapshot(home_fd)
            except Exception:
                return ServiceResult(
                    action,
                    ServiceState.UNAVAILABLE,
                    config.label,
                    initial.installed,
                    initial.loaded,
                    initial.pid,
                    detail="session_token_command_config_snapshot_unavailable",
                )
            if not _prepare_session_token_command_config(config):
                return ServiceResult(
                    action,
                    ServiceState.UNAVAILABLE,
                    config.label,
                    initial.installed,
                    initial.loaded,
                    initial.pid,
                    detail="session_token_command_config_unavailable",
                )
            try:
                with source._open_absolute_directory(
                    config.hermes_home,
                    exact_mode=source._DIRECTORY_MODE,
                ) as home_fd:
                    committed = source._read_optional_config_snapshot(home_fd)
            except Exception:
                committed = None
            if committed is None:
                return ServiceResult(
                    action,
                    ServiceState.ERROR,
                    config.label,
                    initial.installed,
                    initial.loaded,
                    initial.pid,
                    detail="session_token_command_config_write_unavailable",
                )

            readiness = _session_token_source_ready(config)
            final = service_status(
                current_config,
                plist_path,
                runner=runner,
                uid=uid,
                domain=domain,
            )
            token_after = _session_token_path_identity(config)
            if (
                readiness
                and token_after == token_before
                and _running_refresh_binding(final, current_config)
                and final.pid == initial.pid
            ):
                return ServiceResult(
                    action,
                    ServiceState.RUNNING,
                    config.label,
                    final.installed,
                    True,
                    final.pid,
                    detail=(
                        "session_token_command_config_refreshed"
                        if recovery_detail == "session_token_config_recovery_not_required"
                        else "session_token_command_config_refreshed_recovery_quarantined"
                    ),
                )

            rolled_back = _rollback_session_token_command_config(
                config,
                source,
                prior,
                committed,
            )
            if not rolled_back:
                return ServiceResult(
                    action,
                    ServiceState.ERROR,
                    config.label,
                    final.installed,
                    final.loaded,
                    final.pid,
                    detail="session_token_command_config_rollback_failed",
                )
            if not readiness:
                detail = "session_token_command_config_not_ready"
            elif token_after != token_before:
                detail = "session_token_identity_changed"
            else:
                detail = "service_runtime_binding_changed"
            return ServiceResult(
                action,
                ServiceState.UNAVAILABLE,
                config.label,
                final.installed,
                final.loaded,
                final.pid,
                detail=detail,
            )
    except LifecycleBusyError:
        return _lifecycle_error(action, config, plist_path, "lifecycle_busy")
    except (ConfigurationError, OSError):
        return _lifecycle_error(action, config, plist_path, "lifecycle_lock_error")


def service_status(
    config: ServiceConfig,
    plist_path: Path,
    *,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    uid: int | None = None,
    domain: str | None = None,
) -> ServiceResult:
    if _sidecar_primary_plist_collision(config, plist_path):
        return _target_rejected("status", config)
    if dry_run:
        return _planned("status", config, plist_path)
    try:
        with _open_plist_directory(plist_path) as directory:
            admitted = _snapshot_plist_at(directory)
            if admitted is None:
                return ServiceResult(
                    "status", ServiceState.NOT_INSTALLED, config.label, False
                )
            expected_render = render_launchd_plist(config).encode("utf-8")
            if not _definition_matches(
                directory,
                candidate=admitted,
                rendered=expected_render,
                expected_label=config.label,
            ):
                return ServiceResult(
                    "status",
                    ServiceState.ERROR,
                    config.label,
                    _plist_installed_at(directory),
                    detail="plist_definition_mismatch",
                )
            if not _launchctl_binary_qualified():
                return _launchctl_unqualified("status", config, installed=True)
            selected_domain = _selected_domain(config, runner, uid, domain)
            try:
                completed = _confirm_launchd_label(
                    config, runner=runner, domain=selected_domain
                )
            except FileNotFoundError:
                return ServiceResult(
                    "status",
                    ServiceState.UNAVAILABLE,
                    config.label,
                    True,
                    detail="launchctl_not_found",
                )
            except subprocess.TimeoutExpired:
                return ServiceResult(
                    "status",
                    ServiceState.UNAVAILABLE,
                    config.label,
                    True,
                    detail="launchctl_timeout",
                )
            if not _definition_matches(
                directory,
                candidate=admitted,
                rendered=expected_render,
                expected_label=config.label,
            ):
                return ServiceResult(
                    "status",
                    ServiceState.ERROR,
                    config.label,
                    _plist_installed_at(directory),
                    detail="plist_definition_changed",
                )
            if completed.returncode in _STATUS_NOT_LOADED_RETURN_CODES:
                disabled, disabled_error = _launchd_disabled_override(
                    config,
                    runner,
                    selected_domain,
                )
                if disabled_error is not None:
                    return ServiceResult(
                        "status",
                        ServiceState.UNAVAILABLE,
                        config.label,
                        True,
                        detail=disabled_error,
                    )
                return ServiceResult(
                    "status",
                    ServiceState.STOPPED,
                    config.label,
                    True,
                    detail=("service_disabled" if disabled else "service_not_loaded"),
                )
            if completed.returncode != 0:
                return ServiceResult(
                    "status",
                    ServiceState.ERROR,
                    config.label,
                    True,
                    detail=(
                        "launchctl_definition_mismatch"
                        if completed.returncode == 78
                        else "launchctl_status_error"
                    ),
                )
            match = _PID_RE.search(completed.stdout or "")
            parsed_pid = int(match.group(1)) if match else None
            pid = parsed_pid if parsed_pid is not None and parsed_pid > 0 else None
            state = ServiceState.RUNNING if pid is not None else ServiceState.LOADED
            return ServiceResult("status", state, config.label, True, True, pid)
    except (ConfigurationError, OSError):
        return _target_rejected("status", config)


_ACTIONS = {
    "install": install_service,
    "uninstall": uninstall_service,
    "start": start_service,
    "stop": stop_service,
    "restart": restart_service,
    "recover-config": recover_config_service,
    "refresh-session-token-command-config": refresh_session_token_command_config,
    "status": service_status,
}
_ORCH_SIDECAR_ALLOWED_ACTIONS = frozenset(
    {
        "render",
        "preflight",
        "install",
        "uninstall",
        "start",
        "stop",
        "restart",
        "status",
    }
)
_IMPORT_PREFLIGHT_ACTIONS = frozenset(
    {
        "install",
        "status",
        "start",
        "restart",
        "recover-config",
        "refresh-session-token-command-config",
    }
)


def _absolute_path(raw: str, name: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute path")
    return path


def _config_artifact_expectation(raw: str) -> ConfigArtifactExpectation:
    """Parse regular:uid:0600:device:inode without reflecting the input."""

    fields = raw.split(":")
    if len(fields) != 5 or fields[0] != "regular":
        raise argparse.ArgumentTypeError(
            "config identity must be regular:uid:0600:device:inode"
        )
    try:
        uid = int(fields[1], 10)
        mode = int(fields[2], 8)
        device = int(fields[3], 10)
        inode = int(fields[4], 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "config identity must be regular:uid:0600:device:inode"
        ) from exc
    if uid < 0 or mode != PRIVATE_FILE_MODE or device <= 0 or inode <= 0:
        raise argparse.ArgumentTypeError(
            "config identity must be regular:uid:0600:device:inode"
        )
    return ConfigArtifactExpectation("regular", uid, mode, device, inode)


def _config_recovery_request_from_args(
    args: argparse.Namespace,
) -> ConfigRecoveryRequest:
    recovery_identity = args.recovery_identity
    retired_identity = args.retired_identity
    active_identity = args.active_identity
    if (recovery_identity is None) != (args.recovery_disposition is None):
        raise ConfigurationError("recovery identity and disposition must be paired")
    if (retired_identity is None) != (args.retired_disposition is None):
        raise ConfigurationError("retired identity and disposition must be paired")
    if recovery_identity is None and retired_identity is None:
        raise ConfigurationError("at least one recovery generation is required")
    if args.recovery_disposition == "restore":
        if active_identity is None:
            raise ConfigurationError("restore requires the active config identity")
    elif active_identity is not None:
        raise ConfigurationError("active config identity is valid only for restore")
    return ConfigRecoveryRequest(
        recovery_identity,
        args.recovery_disposition,
        retired_identity,
        args.retired_disposition,
        active_identity,
    )


def _config_from_args(args: argparse.Namespace) -> ServiceConfig:
    role = ServiceRole(getattr(args, "role", ServiceRole.PRIMARY))
    return ServiceConfig(
        worktree=_absolute_path(args.worktree, "worktree"),
        runtime=_absolute_path(args.runtime, "runtime"),
        python=_absolute_path(args.python, "python"),
        hermes_home=_absolute_path(args.hermes_home, "hermes_home"),
        port=args.port,
        role=role,
        label=(
            ORCH_SIDECAR_LABEL
            if role is ServiceRole.ORCH_SIDECAR
            else DEFAULT_LABEL
        ),
    )


def _current_service_config_from_args(args: argparse.Namespace) -> ServiceConfig:
    """Build the exact currently registered service identity for refresh."""

    return ServiceConfig(
        worktree=_absolute_path(args.current_worktree, "current_worktree"),
        runtime=_absolute_path(args.current_runtime, "current_runtime"),
        python=_absolute_path(args.current_python, "current_python"),
        hermes_home=_absolute_path(args.hermes_home, "hermes_home"),
        port=args.current_port,
        role=ServiceRole.PRIMARY,
    )


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--worktree", required=True, help="Absolute pinned Hermes worktree"
    )
    parser.add_argument(
        "--runtime", required=True, help="Absolute Hermes launcher inside worktree"
    )
    parser.add_argument(
        "--python", required=True, help="Absolute pinned Python interpreter"
    )
    parser.add_argument(
        "--hermes-home", required=True, help="Absolute profile-aware HERMES_HOME"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--role",
        choices=tuple(role.value for role in ServiceRole),
        default=ServiceRole.PRIMARY.value,
        help="Admitted serve role (default: primary)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the ORCH-Next macOS service for the existing hermes serve runtime"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    render_parser = subparsers.add_parser(
        "render", help="Render the deterministic plist"
    )
    _add_identity_arguments(render_parser)
    preflight_parser = subparsers.add_parser(
        "preflight", help="Verify the admitted lifecycle import closure"
    )
    _add_identity_arguments(preflight_parser)
    for action in _ACTIONS:
        action_parser = subparsers.add_parser(action)
        _add_identity_arguments(action_parser)
        action_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the planned action without effects",
        )
        if action == "recover-config":
            action_parser.add_argument(
                "--recovery-identity",
                type=_config_artifact_expectation,
                help="Exact regular:uid:0600:device:inode recovery identity",
            )
            action_parser.add_argument(
                "--recovery-disposition",
                choices=("preserve", "restore", "quarantine"),
            )
            action_parser.add_argument(
                "--retired-identity",
                type=_config_artifact_expectation,
                help="Exact regular:uid:0600:device:inode retired identity",
            )
            action_parser.add_argument(
                "--retired-disposition",
                choices=("preserve", "quarantine"),
            )
            action_parser.add_argument(
                "--active-identity",
                type=_config_artifact_expectation,
                help="Exact active identity required only for restore",
            )
        elif action == "refresh-session-token-command-config":
            action_parser.add_argument(
                "--current-worktree",
                required=True,
                help="Absolute worktree bound to the currently running service",
            )
            action_parser.add_argument(
                "--current-runtime",
                required=True,
                help="Absolute Hermes runtime bound to the currently running service",
            )
            action_parser.add_argument(
                "--current-python",
                required=True,
                help="Absolute Python bound to the currently running service",
            )
            action_parser.add_argument(
                "--current-port",
                required=True,
                type=int,
                help="Exact listener port bound to the currently running service",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source_lock = _admit_inherited_source_lock() if __name__ == "__main__" else None
    if _BARE_MODULE_ENTRYPOINT or (
        __name__ == "__main__"
        and (not _ISOLATED_LAUNCHER_RUNTIME or source_lock is None)
    ):
        if source_lock is not None:
            _release_inherited_source_lock(source_lock)
        result = ServiceResult(
            args.action,
            ServiceState.UNAVAILABLE,
            DEFAULT_LABEL,
            False,
            detail=(
                "module_entrypoint_unavailable"
                if _BARE_MODULE_ENTRYPOINT
                else "isolated_launcher_required"
            ),
        )
        sys.stdout.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")
        return 1
    try:
        config = _config_from_args(args)
        if (
            config.role is ServiceRole.ORCH_SIDECAR
            and args.action not in _ORCH_SIDECAR_ALLOWED_ACTIONS
        ):
            raise ConfigurationError(
                f"orch sidecar does not support action: {args.action}"
            )
        if __name__ == "__main__" and not _consume_lifecycle_runtime_authority(
            config.hermes_home
        ):
            result = ServiceResult(
                args.action,
                ServiceState.UNAVAILABLE,
                config.label,
                False,
                detail="runtime_provenance_authority_unavailable",
            )
            sys.stdout.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")
            return 1
        if args.action == "render":
            sys.stdout.write(render_launchd_plist(config))
            return 0
        if args.action == "preflight":
            ready = _checkout_import_preflight(config)
            result = ServiceResult(
                "preflight",
                ServiceState.PLANNED if ready else ServiceState.UNAVAILABLE,
                config.label,
                False,
                detail=(
                    "checkout_imports_admitted"
                    if ready
                    else "checkout_imports_unavailable"
                ),
            )
            sys.stdout.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")
            return 0 if ready else 1
        if args.action in _IMPORT_PREFLIGHT_ACTIONS and not _checkout_import_preflight(
            config
        ):
            result = ServiceResult(
                args.action,
                ServiceState.UNAVAILABLE,
                config.label,
                False,
                detail="checkout_imports_unavailable",
            )
            sys.stdout.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")
            return 1
        plist_path = (
            default_plist_path()
            if config.role is ServiceRole.PRIMARY
            else default_plist_path(label=config.label)
        )
        action_kwargs: dict[str, object] = {"dry_run": args.dry_run}
        if args.action == "install" and not args.dry_run:
            if config.role is ServiceRole.ORCH_SIDECAR:
                # The sidecar consumes the primary profile's already-admitted
                # token source.  It never prepares, rotates, or writes that
                # source as part of its own lifecycle.
                action_kwargs["command_config_prepared"] = True
            else:
                command_config_prepared = _prepare_session_token_command_config(
                    config
                )
                action_kwargs["command_config_prepared"] = command_config_prepared
                if command_config_prepared:
                    action_kwargs["session_token_authority_context"] = (
                        _session_token_install_authority_context(config)
                    )
        elif args.action == "recover-config":
            action_kwargs["request"] = _config_recovery_request_from_args(args)
        elif args.action == "refresh-session-token-command-config":
            action_kwargs["current_config"] = _current_service_config_from_args(args)
        result = _ACTIONS[args.action](config, plist_path, **action_kwargs)
    except ConfigurationError as exc:
        parser.error(str(exc))
    finally:
        if source_lock is not None:
            _release_inherited_source_lock(source_lock)
    sys.stdout.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")
    return (
        0 if result.state not in {ServiceState.ERROR, ServiceState.UNAVAILABLE} else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
