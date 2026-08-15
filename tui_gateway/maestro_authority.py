"""Fail-closed consumer for Maestro's immutable Hermes authority bundle.

This module is an execution-side consumer, not an authority source.  It sends
one sanitized operational context and the actual Hermes runtime identity to an
explicitly installed Maestro decision transport.  It never constructs an
allow receipt, retries an authority decision, or falls back to another
execution harness.
"""

from __future__ import annotations

import _socket
import hashlib
import math
import os
import re
import select
import stat
import sys
import threading
import time
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final


HERMES_MAESTRO_AUTHORITY_BUNDLE_ID: Final = "HERMES_MAESTRO_AUTHORITY_BUNDLE_V3"
HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION: Final = "hermes-maestro-authority-bundle.v3"
HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST: Final = (
    "7d6bc36e50938f74ad2728ed3d87f272620086de7bfd928616c84bbdfd09412e"
)
HERMES_TELEMETRY_SCHEMA_VERSION: Final = "hermes-operational-telemetry-schema.v2"
HERMES_TELEMETRY_SCHEMA_DIGEST: Final = (
    "7c391860dd39fb01b9a466e3826d74261d30fafd1c609869a6d55a275dcb8748"
)
HERMES_ROLLBACK_ADMISSION_VERSION: Final = "hermes-version-rollback-admission.v2"
HERMES_ROLLBACK_ADMISSION_DIGEST: Final = (
    "240ad49b1b822e1a48929294b4999c1c94864e205370734992603a73372dfefd"
)
HERMES_AUTHORITY_OWNER: Final = "maestro-kernel"
HERMES_AUTHORITY_CONSUMER: Final = "hermes_operational_harness"
HERMES_OPERATIONAL_TARGET: Final = "hermes"
HERMES_OPERATIONAL_METHOD: Final = "prompt.submit"
HERMES_OPERATIONAL_CONTEXT_VERSION: Final = "hermes-orch-operational-context.v1"
HERMES_OPERATIONAL_GOAL: Final = "hermes_exclusive_harness_complete_migration"
HERMES_OPERATIONAL_REVISION: Final = 1
HERMES_CONTEXT_MAX_TTL_SECONDS: Final = 300.0
HERMES_SESSION_TOKEN_REQUEST_TTL_SECONDS: Final = 60.0
HERMES_SESSION_TOKEN_PROMPT_CONTRACT_VERSION: Final = "orch_prompt.v1"
HERMES_SESSION_TOKEN_PROMPT_CONTRACT_DIGEST: Final = (
    "9a7f77b1dfa79c28b6d4532d11f73a99c26e6fe868eace7050edaf143ad3e8c2"
)
HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY: Final = "maestro-kernel"
HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE: Final = "orchnext-hermes-authority-v3"
HERMES_TERMINAL_AUTHORITY_CONTRACT_ID: Final = "INC191_PRE_IDLE_SUCCESSOR_ADMISSION_V1"
HERMES_TERMINAL_AUTHORITY_CONTRACT_VERSION: Final = "1.1.0"
HERMES_TERMINAL_AUTHORITY_SOURCE: Final = "scripts/ops/mk_whole_goal_control.py"
HERMES_TERMINAL_AUTHORITY_SOURCE_SHA256: Final = (
    "35ac9d266faf9841ada668efe10768ce383e5601ff362ad9b12cc670dd171942"
)
HERMES_TERMINAL_PROFILE_SHA256: Final = (
    "a57c57fc6cbe65c5657324ebbc737a370c7ef24ea6ae5cc2f0305ec94607c0be"
)

# This committed file contains only the public verifier trust anchor.  The
# corresponding private signing key remains outside the repository and outside
# the Hermes process.  Pinning the exact bytes here prevents callers, runtime
# environment variables, or service responses from selecting their own signer.
HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256: Final = (
    "218086ae46c18210e169300f346f6a596a133fdb3d9be5923e19662588b6874f"
)
_PROTECTED_AUTHORITY_ALLOWED_SIGNERS = Path(__file__).with_name(
    "maestro_authority_allowed_signers"
)
_PROTECTED_AUTHORITY_SOCKET_LEAF = "maestro-authority-v3.sock"
_PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES = 128 * 1024
_PROTECTED_AUTHORITY_CONNECT_TIMEOUT_SECONDS = 5.0
_PROTECTED_AUTHORITY_VERIFY_TIMEOUT_SECONDS = 5.0
_PROTECTED_AUTHORITY_MAX_ALLOWED_SIGNERS_BYTES = 64 * 1024
_PROTECTED_AUTHORITY_SSH_KEYGEN = Path("/usr/bin/ssh-keygen")
_PROTECTED_TRANSPORT_GENERATION = object()
_EXPECTED_OS_WNOHANG = 1
_EXPECTED_SOCKET_AF_UNIX = 1
_EXPECTED_SOCKET_SOCK_STREAM = 1
_NATIVE_OS_KILL = os.kill
_NATIVE_OS_URANDOM = os.urandom
_NATIVE_OS_WNOHANG = os.WNOHANG
_NATIVE_OS_WAITSTATUS_TO_EXITCODE = os.waitstatus_to_exitcode
_NATIVE_SHA256 = hashlib.sha256
_NATIVE_SELECT = select.select
_NATIVE_SOCKET_CLASS = _socket.socket
_NATIVE_SOCKET_AF_UNIX = _socket.AF_UNIX
_NATIVE_SOCKET_SOCK_STREAM = _socket.SOCK_STREAM
_NATIVE_SOCKET_SETTIMEOUT = _socket.socket.settimeout
_NATIVE_SOCKET_CONNECT = _socket.socket.connect
_NATIVE_SOCKET_SENDALL = _socket.socket.sendall
_NATIVE_SOCKET_RECV = _socket.socket.recv
_NATIVE_SOCKET_CLOSE = _socket.socket.close
_NATIVE_TIME_MONOTONIC = time.monotonic
_NATIVE_TIME_TIME = time.time


def _trusted_runtime_boundary() -> bool:
    """Admit only the hardened CPython stdlib/native verifier boundary.

    Hermes hardens ``sys.path`` before importing the gateway.  This additional
    check rejects ordinary pre-import ``sys.modules`` substitution of the
    stdlib primitives that carry the signature-verification decision.  Native
    code execution or mutation of the CPython runtime itself is outside an
    in-process Python consumer's enforceable boundary and remains protected at
    the downstream Maestro authority transition.
    """
    try:
        stdlib_root = (
            Path(sys.base_prefix)
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
        ).resolve(strict=True)

        def admitted_module(module: object, name: str) -> bool:
            spec = getattr(module, "__spec__", None)
            if spec is None or spec.name != name or not isinstance(spec.origin, str):
                return False
            if spec.origin in {"built-in", "frozen"}:
                return True
            return Path(spec.origin).resolve(strict=True).is_relative_to(stdlib_root)

        native_type = type(len)
        return (
            admitted_module(hashlib, "hashlib")
            and admitted_module(_socket, "_socket")
            and admitted_module(os, "os")
            and admitted_module(select, "select")
            and admitted_module(time, "time")
            and type(_NATIVE_SHA256) is native_type
            and _NATIVE_SHA256.__module__ == "_hashlib"
            and all(
                type(function) is native_type and function.__module__ == "posix"
                for function in (
                    os.fstat,
                    os.close,
                    os.getuid,
                    os.lstat,
                    os.open,
                    os.pipe,
                    os.posix_spawn,
                    os.read,
                    os.set_blocking,
                    os.urandom,
                    os.waitpid,
                    os.write,
                )
            )
            and type(_NATIVE_OS_KILL) is native_type
            and _NATIVE_OS_KILL.__module__ == "posix"
            and type(_NATIVE_OS_WNOHANG) is int
            and _NATIVE_OS_WNOHANG == _EXPECTED_OS_WNOHANG
            and type(_NATIVE_OS_WAITSTATUS_TO_EXITCODE) is native_type
            and _NATIVE_OS_WAITSTATUS_TO_EXITCODE.__module__ == "posix"
            and type(_NATIVE_TIME_MONOTONIC) is native_type
            and _NATIVE_TIME_MONOTONIC.__module__ == "time"
            and type(_NATIVE_TIME_TIME) is native_type
            and _NATIVE_TIME_TIME.__module__ == "time"
            and type(_NATIVE_SELECT) is native_type
            and _NATIVE_SELECT.__module__ == "select"
            and type(_NATIVE_SOCKET_CLASS) is type
            and _NATIVE_SOCKET_CLASS.__module__ == "_socket"
            and _NATIVE_SOCKET_CLASS.__name__ == "socket"
            and _socket.socket is _NATIVE_SOCKET_CLASS
            and type(_NATIVE_SOCKET_AF_UNIX) is int
            and _NATIVE_SOCKET_AF_UNIX == _EXPECTED_SOCKET_AF_UNIX
            and type(_NATIVE_SOCKET_SOCK_STREAM) is int
            and _NATIVE_SOCKET_SOCK_STREAM == _EXPECTED_SOCKET_SOCK_STREAM
            and all(
                type(descriptor) is type(list.append)
                and descriptor.__objclass__ is _NATIVE_SOCKET_CLASS
                for descriptor in (
                    _NATIVE_SOCKET_SETTIMEOUT,
                    _NATIVE_SOCKET_CONNECT,
                    _NATIVE_SOCKET_SENDALL,
                    _NATIVE_SOCKET_RECV,
                    _NATIVE_SOCKET_CLOSE,
                )
            )
        )
    except Exception:
        return False


def _startup_process_hermes_home() -> Path:
    """Bind the protected socket route once, before request handling begins."""
    routed = globals().get("_ORCH_PROTECTED_AUTHORITY_HOME_ROUTE")
    if type(routed) is type(Path()):
        return routed
    configured = os.environ.get("HERMES_HOME", "").strip()
    home = Path(configured) if configured else Path.home() / ".hermes"
    return Path(os.path.abspath(home))


_PROTECTED_AUTHORITY_HOME = _startup_process_hermes_home()

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-/]{0,159}\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_TASK_CLASSES = frozenset({
    "mechanical",
    "low_risk_mechanical",
    "implementation",
    "audit",
    "operations",
    "product",
    "research",
    "migration",
})
_CONTEXT_KEYS = frozenset({
    "contract_version",
    "authority_bundle",
    "threshold_policy",
    "decision_binding",
    "goal",
    "operation",
    "target",
    "revision",
    "issued_at",
    "expires_at",
    "operation_id",
    "task_declaration",
})
_ACTUAL_KEYS = frozenset({
    "logical_session_id",
    "ui_session_id",
    "method",
    "target",
    "runtime_revision",
})
_RECEIPT_KEYS = frozenset({
    "outcome",
    "code",
    "decision_id",
    "authority_owner",
    "authority_bundle_version",
    "authority_bundle_digest",
    "authority_consumer",
    "telemetry_schema_version",
    "telemetry_schema_digest",
    "rollback_admission_version",
    "rollback_admission_digest",
    "account_id",
    "project_id",
    "logical_session_id",
    "ui_session_id",
    "method",
    "target",
    "runtime_revision",
    "runtime_provenance_manifest",
    "runtime_provenance_manifest_digest",
    "issued_at",
    "expires_at",
    "final_decision_state",
    "final_execution_permitted",
    "consumed_once",
})
_RUNTIME_PROVENANCE_MANIFEST_KEYS = frozenset({
    "upstreamReleaseTag",
    "upstreamPackageVersion",
    "upstreamCommit",
    "runtimeCommit",
    "runtimeContentDigest",
})
_TERMINAL_ACTUAL_KEYS = frozenset({
    "logical_session_id",
    "ui_session_id",
    "runtime_revision",
    "requested_transition",
    "controller_owner_id",
    "owner_epoch",
})
_TERMINAL_RECEIPT_KEYS = frozenset({
    "contract_id",
    "contract_version",
    "authority_source_sha256",
    "profile_sha256",
    "decision_id",
    "consumer_decision",
    "admitted",
    "blocking_findings",
    "logical_session_id",
    "ui_session_id",
    "runtime_revision",
    "requested_transition",
    "controller_owner_id",
    "owner_epoch",
    "issued_at",
    "expires_at",
    "consumed_once",
})
_TERMINAL_TRANSITIONS = frozenset({"idle", "final", "protected_wait"})
_TERMINAL_DECISIONS = frozenset({
    "ALLOW_FINAL_IDLE",
    "ALLOW_NARROW_PROTECTED_WAIT",
    "ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR",
    "REJECT_IDLE_DISJOINT_WORK_UNASSIGNED",
    "CONTINUE_CURRENT_CONTROLLER",
})

AuthorityDecisionTransport = Callable[[dict[str, Any], dict[str, str]], object]
AuthorityReceiptOriginVerifier = Callable[
    [object, dict[str, Any], dict[str, str]], bool
]


@dataclass(frozen=True, **({"slots": True} if sys.version_info >= (3, 10) else {}))
class AuthorityTransportInstallation:
    """Opaque handle used to reset only the transport that was installed."""

    _generation: object


def _load_protected_receipt_origin_verifier(
    _transport: AuthorityDecisionTransport,
) -> AuthorityReceiptOriginVerifier | None:
    """Fail closed until Hermes owns a fixed cryptographic receipt verifier.

    A verifier supplied by an importable provider module is not an authority
    boundary: ordinary ``sys.path`` or ``sys.modules`` substitution can make a
    forged provider return an always-true callable.  The protected transition
    service may remain Maestro-owned and out of process, but receipt-origin
    verification must be fixed consumer code bound to a pinned trust anchor.
    No such trust anchor is provisioned in this source revision, so transport
    installation is deliberately unavailable instead of accepting an
    in-process verifier or falling back to shape validation.
    """

    return None


class _AuthorityJsonError(ValueError):
    """Internal sentinel for the bounded protected-envelope codec."""


def _canonical_json_string(value: str) -> bytes:
    encoded = bytearray(b'"')
    escapes = {
        0x08: b"\\b",
        0x09: b"\\t",
        0x0A: b"\\n",
        0x0C: b"\\f",
        0x0D: b"\\r",
        0x22: b'\\"',
        0x5C: b"\\\\",
    }
    for character in value:
        codepoint = ord(character)
        escaped = escapes.get(codepoint)
        if escaped is not None:
            encoded.extend(escaped)
        elif 0x20 <= codepoint <= 0x7E:
            encoded.append(codepoint)
        elif codepoint <= 0xFFFF:
            encoded.extend(f"\\u{codepoint:04x}".encode("ascii"))
        elif codepoint <= 0x10FFFF:
            adjusted = codepoint - 0x10000
            high = 0xD800 + (adjusted >> 10)
            low = 0xDC00 + (adjusted & 0x3FF)
            encoded.extend(f"\\u{high:04x}\\u{low:04x}".encode("ascii"))
        else:  # pragma: no cover - CPython str cannot contain larger values
            raise _AuthorityJsonError("invalid codepoint")
    encoded.append(0x22)
    return bytes(encoded)


def _canonical_json_bytes(value: object, *, path: tuple[str, ...] = ()) -> bytes:
    value_type = type(value)
    if value is None:
        return b"null"
    if value_type is bool:
        return b"true" if value else b"false"
    if value_type is str:
        return _canonical_json_string(value)
    if value_type is int:
        return str(value).encode("ascii")
    if value_type is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise _AuthorityJsonError("non-finite number")
        return repr(value).encode("ascii")
    if value_type is list:
        return (
            b"["
            + b",".join(
                _canonical_json_bytes(item, path=(*path, str(index)))
                for index, item in enumerate(value)
            )
            + b"]"
        )
    if value_type is dict:
        keys = tuple(dict.keys(value))
        if any(type(key) is not str for key in keys):
            raise _AuthorityJsonError("non-string key")
        parts = []
        for key in sorted(keys):
            parts.append(
                _canonical_json_string(key)
                + b":"
                + _canonical_json_bytes(dict.__getitem__(value, key), path=(*path, key))
            )
        return b"{" + b",".join(parts) + b"}"
    raise _AuthorityJsonError("unsupported value")


class _AuthorityJsonParser:
    """Strict recursive-descent parser for the existing canonical JSON envelope."""

    __slots__ = ("data", "index")

    def __init__(self, data: bytes) -> None:
        if (
            type(data) is not bytes
            or not data
            or len(data) > _PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES
        ):
            raise _AuthorityJsonError("invalid input")
        if any(byte > 0x7F for byte in data):
            raise _AuthorityJsonError("non-ascii input")
        self.data = data
        self.index = 0

    def parse(self) -> object:
        value = self._value()
        if self.index != len(self.data):
            raise _AuthorityJsonError("trailing data")
        return value

    def _take(self, expected: int) -> None:
        if self.index >= len(self.data) or self.data[self.index] != expected:
            raise _AuthorityJsonError("unexpected token")
        self.index += 1

    def _value(self) -> object:
        if self.index >= len(self.data):
            raise _AuthorityJsonError("missing value")
        token = self.data[self.index]
        if token == 0x7B:
            return self._object()
        if token == 0x5B:
            return self._array()
        if token == 0x22:
            return self._string()
        for literal, value in ((b"true", True), (b"false", False), (b"null", None)):
            if self.data.startswith(literal, self.index):
                self.index += len(literal)
                return value
        if token == 0x2D or 0x30 <= token <= 0x39:
            return self._number()
        raise _AuthorityJsonError("invalid value")

    def _object(self) -> dict[str, object]:
        self._take(0x7B)
        result: dict[str, object] = {}
        if self.index < len(self.data) and self.data[self.index] == 0x7D:
            self.index += 1
            return result
        while True:
            key = self._string()
            if key in result:
                raise _AuthorityJsonError("duplicate key")
            self._take(0x3A)
            result[key] = self._value()
            if self.index >= len(self.data):
                raise _AuthorityJsonError("unterminated object")
            delimiter = self.data[self.index]
            self.index += 1
            if delimiter == 0x7D:
                return result
            if delimiter != 0x2C:
                raise _AuthorityJsonError("invalid object delimiter")

    def _array(self) -> list[object]:
        self._take(0x5B)
        result: list[object] = []
        if self.index < len(self.data) and self.data[self.index] == 0x5D:
            self.index += 1
            return result
        while True:
            result.append(self._value())
            if self.index >= len(self.data):
                raise _AuthorityJsonError("unterminated array")
            delimiter = self.data[self.index]
            self.index += 1
            if delimiter == 0x5D:
                return result
            if delimiter != 0x2C:
                raise _AuthorityJsonError("invalid array delimiter")

    def _string(self) -> str:
        self._take(0x22)
        characters: list[str] = []
        while self.index < len(self.data):
            token = self.data[self.index]
            self.index += 1
            if token == 0x22:
                return "".join(characters)
            if token == 0x5C:
                if self.index >= len(self.data):
                    raise _AuthorityJsonError("unterminated escape")
                escape = self.data[self.index]
                self.index += 1
                simple = {
                    0x22: '"',
                    0x2F: "/",
                    0x5C: "\\",
                    0x62: "\b",
                    0x66: "\f",
                    0x6E: "\n",
                    0x72: "\r",
                    0x74: "\t",
                }.get(escape)
                if simple is not None:
                    characters.append(simple)
                    continue
                if escape != 0x75:
                    raise _AuthorityJsonError("invalid escape")
                codepoint = self._hex4()
                if 0xD800 <= codepoint <= 0xDBFF:
                    if self.data[self.index : self.index + 2] != b"\\u":
                        raise _AuthorityJsonError("missing low surrogate")
                    self.index += 2
                    low = self._hex4()
                    if not 0xDC00 <= low <= 0xDFFF:
                        raise _AuthorityJsonError("invalid low surrogate")
                    codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)
                elif 0xDC00 <= codepoint <= 0xDFFF:
                    raise _AuthorityJsonError("unpaired low surrogate")
                characters.append(chr(codepoint))
                continue
            if token < 0x20:
                raise _AuthorityJsonError("control character")
            characters.append(chr(token))
        raise _AuthorityJsonError("unterminated string")

    def _hex4(self) -> int:
        if self.index + 4 > len(self.data):
            raise _AuthorityJsonError("short unicode escape")
        value = 0
        for token in self.data[self.index : self.index + 4]:
            if 0x30 <= token <= 0x39:
                digit = token - 0x30
            elif 0x61 <= token <= 0x66:
                digit = token - 0x61 + 10
            else:
                raise _AuthorityJsonError("invalid unicode escape")
            value = value * 16 + digit
        self.index += 4
        return value

    def _number(self) -> int | float:
        start = self.index
        if self.data[self.index] == 0x2D:
            self.index += 1
            if self.index >= len(self.data):
                raise _AuthorityJsonError("invalid number")
        if self.data[self.index] == 0x30:
            self.index += 1
            if self.index < len(self.data) and 0x30 <= self.data[self.index] <= 0x39:
                raise _AuthorityJsonError("leading zero")
        elif 0x31 <= self.data[self.index] <= 0x39:
            while self.index < len(self.data) and 0x30 <= self.data[self.index] <= 0x39:
                self.index += 1
        else:
            raise _AuthorityJsonError("invalid integer")
        floating = False
        if self.index < len(self.data) and self.data[self.index] == 0x2E:
            floating = True
            self.index += 1
            fraction = self.index
            while self.index < len(self.data) and 0x30 <= self.data[self.index] <= 0x39:
                self.index += 1
            if self.index == fraction:
                raise _AuthorityJsonError("missing fraction")
        if self.index < len(self.data) and self.data[self.index] in {0x45, 0x65}:
            floating = True
            self.index += 1
            if self.index < len(self.data) and self.data[self.index] in {0x2B, 0x2D}:
                self.index += 1
            exponent = self.index
            while self.index < len(self.data) and 0x30 <= self.data[self.index] <= 0x39:
                self.index += 1
            if self.index == exponent:
                raise _AuthorityJsonError("missing exponent")
        token = self.data[start : self.index].decode("ascii")
        try:
            value = float(token) if floating else int(token)
        except (OverflowError, ValueError) as exc:
            raise _AuthorityJsonError("invalid number") from exc
        if type(value) is float and (
            value != value or value in {float("inf"), float("-inf")}
        ):
            raise _AuthorityJsonError("non-finite number")
        return value


def _canonical_authority_payload(value: object) -> bytes | None:
    try:
        encoded = _canonical_json_bytes(value)
    except (_AuthorityJsonError, OverflowError, UnicodeError):
        return None
    return encoded if len(encoded) <= _PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES else None


def _parse_canonical_authority_payload(value: bytes) -> object | None:
    try:
        parsed = _AuthorityJsonParser(value).parse()
        return parsed if _canonical_json_bytes(parsed) == value else None
    except (_AuthorityJsonError, OverflowError, UnicodeError):
        return None


def _fixed_allowed_signers_content() -> bytes | None:
    expected = HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        return None
    path = _PROTECTED_AUTHORITY_ALLOWED_SIGNERS
    fd = -1
    try:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        nonblocking = getattr(os, "O_NONBLOCK", None)
        if no_follow is None or nonblocking is None:
            return None
        fd = os.open(
            path,
            os.O_RDONLY | no_follow | nonblocking,
        )
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None
        if before.st_uid != os.getuid() or before.st_mode & 0o022:
            return None
        chunks = bytearray()
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > _PROTECTED_AUTHORITY_MAX_ALLOWED_SIGNERS_BYTES:
                return None
        after = os.fstat(fd)
    except Exception:
        return None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_mode != after.st_mode
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        return None
    content = bytes(chunks)
    if _NATIVE_SHA256(content).hexdigest() != expected:
        return None
    return content


def _path_has_no_symlink_components(path: Path) -> bool:
    if not path.is_absolute():
        return False
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return False
    except OSError:
        return False
    return True


def _fixed_authority_socket_path() -> Path | None:
    try:
        home = _PROTECTED_AUTHORITY_HOME
        if not _path_has_no_symlink_components(home):
            return None
        authority_dir = home / "authority"
        if not _path_has_no_symlink_components(authority_dir):
            return None
        home_info = os.lstat(home)
        directory_info = os.lstat(authority_dir)
        socket_path = authority_dir / _PROTECTED_AUTHORITY_SOCKET_LEAF
        socket_info = os.lstat(socket_path)
    except Exception:
        return None
    uid = os.getuid()
    if (
        stat.S_ISLNK(home_info.st_mode)
        or not stat.S_ISDIR(home_info.st_mode)
        or home_info.st_uid != uid
        or home_info.st_mode & 0o077
        or stat.S_ISLNK(directory_info.st_mode)
        or not stat.S_ISDIR(directory_info.st_mode)
        or directory_info.st_uid != uid
        or directory_info.st_mode & 0o077
        or stat.S_ISLNK(socket_info.st_mode)
        or not stat.S_ISSOCK(socket_info.st_mode)
        or socket_info.st_uid != uid
    ):
        return None
    return socket_path


def _write_all_until(fd: int, content: bytes, deadline: float) -> bool:
    offset = 0
    while offset < len(content):
        remaining = deadline - _NATIVE_TIME_MONOTONIC()
        if remaining <= 0:
            return False
        try:
            _readable, writable, _exceptional = _NATIVE_SELECT([], [fd], [], remaining)
            if not writable:
                return False
            written = os.write(fd, content[offset:])
            if written <= 0:
                return False
            offset += written
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            return False
    return True


def _spawn_sshsig_verify(
    payload: bytes,
    signature: bytes,
    allowed_signers: bytes,
) -> bool:
    """Run root-owned ssh-keygen with every mutable input descriptor-pinned.

    This deliberately avoids Python's importable ``subprocess`` module and
    never gives ssh-keygen a mutable signer/signature pathname.  The child
    reads the already-hashed signer snapshot and exact signature through
    inherited descriptors, while the signed payload is its standard input.
    """
    pipes: list[tuple[int, int]] = []
    pid: int | None = None
    waited = False
    deadline = _NATIVE_TIME_MONOTONIC() + _PROTECTED_AUTHORITY_VERIFY_TIMEOUT_SECONDS
    try:
        allowed_read, allowed_write = os.pipe()
        signature_read, signature_write = os.pipe()
        payload_read, payload_write = os.pipe()
        pipes = [
            (allowed_read, allowed_write),
            (signature_read, signature_write),
            (payload_read, payload_write),
        ]
        allowed_fd = 100
        signature_fd = 101
        argv = [
            str(_PROTECTED_AUTHORITY_SSH_KEYGEN),
            "-Y",
            "verify",
            "-f",
            f"/dev/fd/{allowed_fd}",
            "-I",
            HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY,
            "-n",
            HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE,
            "-s",
            f"/dev/fd/{signature_fd}",
        ]
        file_actions = [
            (os.POSIX_SPAWN_DUP2, allowed_read, allowed_fd),
            (os.POSIX_SPAWN_DUP2, signature_read, signature_fd),
            (os.POSIX_SPAWN_DUP2, payload_read, 0),
            (os.POSIX_SPAWN_OPEN, 1, "/dev/null", os.O_WRONLY, 0),
            (os.POSIX_SPAWN_OPEN, 2, "/dev/null", os.O_WRONLY, 0),
        ]
        pid = os.posix_spawn(
            str(_PROTECTED_AUTHORITY_SSH_KEYGEN),
            argv,
            {"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            file_actions=file_actions,
        )
        for index, (read_fd, write_fd) in enumerate(pipes):
            os.close(read_fd)
            os.set_blocking(write_fd, False)
            pipes[index] = (-1, write_fd)

        # ssh-keygen reads these inputs in order.  Close each writer as soon
        # as its immutable snapshot is complete so the child sees EOF before
        # advancing to the next descriptor.  Keeping all three writers open
        # until the end can deadlock when the canonical payload exceeds the
        # platform pipe buffer.
        writes_ok = True
        for index, content in enumerate((allowed_signers, signature, payload)):
            write_fd = pipes[index][1]
            if not _write_all_until(write_fd, content, deadline):
                writes_ok = False
            os.close(write_fd)
            pipes[index] = (-1, -1)
            if not writes_ok:
                break
        if not writes_ok:
            return False

        while _NATIVE_TIME_MONOTONIC() < deadline:
            waited_pid, status = os.waitpid(pid, _NATIVE_OS_WNOHANG)
            if waited_pid == pid:
                waited = True
                return _NATIVE_OS_WAITSTATUS_TO_EXITCODE(status) == 0
            remaining = deadline - _NATIVE_TIME_MONOTONIC()
            if remaining > 0:
                _NATIVE_SELECT([], [], [], min(0.01, remaining))
        try:
            _NATIVE_OS_KILL(pid, 9)
        except OSError:
            pass
        _waited_pid, _status = os.waitpid(pid, 0)
        waited = True
        return False
    except Exception:
        return False
    finally:
        for read_fd, write_fd in pipes:
            for fd in (read_fd, write_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        if pid is not None and not waited:
            try:
                waited_pid, _status = os.waitpid(pid, _NATIVE_OS_WNOHANG)
                if waited_pid == 0:
                    _NATIVE_OS_KILL(pid, 9)
                    os.waitpid(pid, 0)
            except OSError:
                pass


def _verify_sshsig_with_allowed_signers(
    payload: bytes,
    signature: str,
    allowed_signers: bytes,
) -> bool:
    if (
        type(signature) is not str
        or not signature.startswith("-----BEGIN SSH SIGNATURE-----\n")
        or not signature.endswith("-----END SSH SIGNATURE-----\n")
        or len(signature) > 32 * 1024
    ):
        return False
    try:
        binary = os.lstat(_PROTECTED_AUTHORITY_SSH_KEYGEN)
    except Exception:
        return False
    if (
        stat.S_ISLNK(binary.st_mode)
        or not stat.S_ISREG(binary.st_mode)
        or binary.st_uid != 0
        or binary.st_mode & 0o022
    ):
        return False
    return _spawn_sshsig_verify(
        payload,
        signature.encode("ascii"),
        allowed_signers,
    )


def _verify_sshsig(payload: bytes, signature: str) -> bool:
    if not _trusted_runtime_boundary():
        return False
    allowed_signers = _fixed_allowed_signers_content()
    if allowed_signers is None:
        return False
    return _verify_sshsig_with_allowed_signers(payload, signature, allowed_signers)


_protected_origin_attestations: dict[int, str] = {}


def _fixed_protected_authority_transport(
    context: dict[str, Any], actual: dict[str, str]
) -> object:
    """Call the fixed Maestro Unix service and verify its signed envelope."""
    if not _trusted_runtime_boundary():
        raise RuntimeError("protected authority runtime boundary unavailable")
    # Fail before connecting when the protected trust anchor is not pinned.
    # The exact bytes are retained for this decision so a later pathname swap
    # cannot change which signer ssh-keygen verifies.
    allowed_signers = _fixed_allowed_signers_content()
    if allowed_signers is None:
        raise RuntimeError("protected authority trust anchor unavailable")
    socket_path = _fixed_authority_socket_path()
    request = {
        "context": context,
        "actual": actual,
        "challenge": os.urandom(32).hex(),
    }
    request_bytes = _canonical_authority_payload(request)
    if socket_path is None or request_bytes is None:
        raise RuntimeError("protected authority transport unavailable")
    try:
        deadline = (
            _NATIVE_TIME_MONOTONIC() + _PROTECTED_AUTHORITY_CONNECT_TIMEOUT_SECONDS
        )

        def arm_remaining_deadline(client: _socket.socket) -> None:
            remaining = deadline - _NATIVE_TIME_MONOTONIC()
            if remaining <= 0:
                raise TimeoutError("protected authority deadline exceeded")
            _NATIVE_SOCKET_SETTIMEOUT(client, remaining)

        client = _NATIVE_SOCKET_CLASS(
            _NATIVE_SOCKET_AF_UNIX,
            _NATIVE_SOCKET_SOCK_STREAM,
        )
        try:
            arm_remaining_deadline(client)
            _NATIVE_SOCKET_CONNECT(client, str(socket_path))
            arm_remaining_deadline(client)
            _NATIVE_SOCKET_SENDALL(client, request_bytes + b"\n")
            chunks = bytearray()
            while b"\n" not in chunks:
                arm_remaining_deadline(client)
                chunk = _NATIVE_SOCKET_RECV(client, 8192)
                if not chunk:
                    raise RuntimeError("protected authority response unavailable")
                chunks.extend(chunk)
                if len(chunks) > _PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES:
                    raise RuntimeError("protected authority response unavailable")
        finally:
            _NATIVE_SOCKET_CLOSE(client)
        line, separator, remainder = bytes(chunks).partition(b"\n")
        if separator != b"\n" or remainder:
            raise RuntimeError("protected authority response unavailable")
        envelope = _parse_canonical_authority_payload(line)
    except Exception as exc:
        raise RuntimeError("protected authority transport unavailable") from exc
    if type(envelope) is not dict or set(envelope) != {"receipt", "signature"}:
        raise RuntimeError("protected authority response unavailable")
    receipt = envelope["receipt"]
    signature = envelope["signature"]
    signed_payload = _canonical_authority_payload({
        "request": request,
        "receipt": receipt,
    })
    if signed_payload is None or not _verify_sshsig_with_allowed_signers(
        signed_payload,
        signature,
        allowed_signers,
    ):
        raise RuntimeError("protected authority signature unavailable")
    receipt_snapshot = _exact_dict(receipt, _RECEIPT_KEYS)
    if receipt_snapshot is None:
        raise RuntimeError("protected authority receipt unavailable")
    attestation = _canonical_authority_payload({
        "context": context,
        "actual": actual,
        "receipt": receipt_snapshot,
    })
    if attestation is None:
        raise RuntimeError("protected authority receipt unavailable")
    with _state_lock:
        _protected_origin_attestations[id(receipt_snapshot)] = _NATIVE_SHA256(
            attestation
        ).hexdigest()
    return receipt_snapshot


def _verify_fixed_protected_receipt_origin(
    receipt: object,
    context: dict[str, Any],
    actual: dict[str, str],
) -> bool:
    if type(receipt) is not dict:
        return False
    attestation = _canonical_authority_payload({
        "context": context,
        "actual": actual,
        "receipt": receipt,
    })
    if attestation is None:
        return False
    with _state_lock:
        expected = _protected_origin_attestations.pop(id(receipt), None)
    return expected == _NATIVE_SHA256(attestation).hexdigest()


def _fixed_protected_terminal_transport(actual: dict[str, Any]) -> object:
    """Obtain one signed terminal-continuation result from the same authority host.

    The host, not Hermes or caller prose, resolves the Grand Goal snapshot and
    evaluates the pinned Maestro contract. Hermes sends only its actual runtime
    identity and requested transition, then validates the signed atomic result.
    """
    if not _trusted_runtime_boundary():
        raise RuntimeError("protected authority runtime boundary unavailable")
    allowed_signers = _fixed_allowed_signers_content()
    if allowed_signers is None:
        raise RuntimeError("protected authority trust anchor unavailable")
    socket_path = _fixed_authority_socket_path()
    request = {
        "contract": {
            "id": HERMES_TERMINAL_AUTHORITY_CONTRACT_ID,
            "version": HERMES_TERMINAL_AUTHORITY_CONTRACT_VERSION,
            "authority_source": HERMES_TERMINAL_AUTHORITY_SOURCE,
            "authority_source_sha256": HERMES_TERMINAL_AUTHORITY_SOURCE_SHA256,
            "profile_sha256": HERMES_TERMINAL_PROFILE_SHA256,
        },
        "actual": actual,
        "challenge": os.urandom(32).hex(),
    }
    request_bytes = _canonical_authority_payload(request)
    if socket_path is None or request_bytes is None:
        raise RuntimeError("protected authority transport unavailable")
    try:
        deadline = (
            _NATIVE_TIME_MONOTONIC() + _PROTECTED_AUTHORITY_CONNECT_TIMEOUT_SECONDS
        )

        def arm_remaining_deadline(client: _socket.socket) -> None:
            remaining = deadline - _NATIVE_TIME_MONOTONIC()
            if remaining <= 0:
                raise TimeoutError("protected authority deadline exceeded")
            _NATIVE_SOCKET_SETTIMEOUT(client, remaining)

        client = _NATIVE_SOCKET_CLASS(
            _NATIVE_SOCKET_AF_UNIX,
            _NATIVE_SOCKET_SOCK_STREAM,
        )
        try:
            arm_remaining_deadline(client)
            _NATIVE_SOCKET_CONNECT(client, str(socket_path))
            arm_remaining_deadline(client)
            _NATIVE_SOCKET_SENDALL(client, request_bytes + b"\n")
            chunks = bytearray()
            while b"\n" not in chunks:
                arm_remaining_deadline(client)
                chunk = _NATIVE_SOCKET_RECV(client, 8192)
                if not chunk:
                    raise RuntimeError("protected authority response unavailable")
                chunks.extend(chunk)
                if len(chunks) > _PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES:
                    raise RuntimeError("protected authority response unavailable")
        finally:
            _NATIVE_SOCKET_CLOSE(client)
        line, separator, remainder = bytes(chunks).partition(b"\n")
        if separator != b"\n" or remainder:
            raise RuntimeError("protected authority response unavailable")
        envelope = _parse_canonical_authority_payload(line)
    except Exception as exc:
        raise RuntimeError("protected authority transport unavailable") from exc
    if type(envelope) is not dict or set(envelope) != {"receipt", "signature"}:
        raise RuntimeError("protected authority response unavailable")
    receipt = envelope["receipt"]
    signature = envelope["signature"]
    signed_payload = _canonical_authority_payload({
        "request": request,
        "receipt": receipt,
    })
    if signed_payload is None or not _verify_sshsig_with_allowed_signers(
        signed_payload,
        signature,
        allowed_signers,
    ):
        raise RuntimeError("protected authority signature unavailable")
    return receipt


_state_lock = threading.Lock()
_authority_transport: AuthorityDecisionTransport | None = None
_authority_receipt_origin_verifier: AuthorityReceiptOriginVerifier | None = None
_transport_generation: object | None = None
_pending_decision_ids: set[str] = set()
_consumed_decision_ids: set[str] = set()
_terminal_call_lock = threading.Lock()
_consumed_terminal_decision_ids: set[str] = set()


def _typed_deny(code: str) -> dict[str, Any]:
    return {"outcome": "deny", "code": code}


def _typed_terminal_deny(code: str) -> dict[str, Any]:
    return {
        "consumer_decision": "CONTINUE_CURRENT_CONTROLLER",
        "admitted": False,
        "blocking_findings": [code],
        "code": code,
    }


def install_maestro_authority_transport(
    transport: AuthorityDecisionTransport,
) -> AuthorityTransportInstallation:
    """Install the protected Maestro decision transport for this process.

    Installation is runtime wiring only.  The callable remains solely
    responsible for obtaining an actual decision from Maestro; this consumer
    cannot mint a decision or accept a caller-supplied receipt.
    """

    if not callable(transport):
        raise TypeError("authority transport must be callable")
    receipt_origin_verifier = _load_protected_receipt_origin_verifier(transport)
    if receipt_origin_verifier is None:
        raise RuntimeError("protected Maestro bootstrap unavailable")
    generation = object()
    with _state_lock:
        global _authority_transport, _authority_receipt_origin_verifier
        global _transport_generation
        if _authority_transport is not None or _transport_generation is not None:
            raise RuntimeError("Maestro authority transport already installed")
        _authority_transport = transport
        _authority_receipt_origin_verifier = receipt_origin_verifier
        _transport_generation = generation
    return AuthorityTransportInstallation(generation)


def reset_maestro_authority_transport(
    installation: AuthorityTransportInstallation,
) -> bool:
    """Remove an exact installed transport without clearing replay history."""

    if type(installation) is not AuthorityTransportInstallation:
        return False
    with _state_lock:
        global _authority_transport, _authority_receipt_origin_verifier
        global _transport_generation
        if installation._generation is not _transport_generation:
            return False
        if _pending_decision_ids:
            return False
        _authority_transport = None
        _authority_receipt_origin_verifier = None
        _transport_generation = None
        return True


def _exact_dict(value: object, keys: frozenset[str]) -> dict[str, Any] | None:
    if type(value) is not dict:
        return None
    try:
        actual_keys = tuple(dict.keys(value))
    except Exception:
        return None
    if any(type(key) is not str for key in actual_keys):
        return None
    if frozenset(actual_keys) != keys:
        return None
    # Exact builtin dicts have no accessor/proxy hooks.  Copy once so every
    # subsequent validation and the transport observe the same inert values.
    return {key: dict.__getitem__(value, key) for key in keys}


def _safe_id(value: object, *, allow_empty: bool = False) -> str | None:
    if type(value) is not str:
        return None
    if allow_empty and value == "":
        return value
    if not _ID_RE.fullmatch(value):
        return None
    lowered = value.lower()
    if (
        "://" in value
        or value.startswith(("/", "~", "./", "../"))
        or lowered.startswith(("sk-", "ghp_", "gho_", "ghs_", "ghr_", "xox"))
        or value.startswith(("AIza", "eyJ"))
    ):
        return None
    return value


def _safe_time(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _wall_clock_now() -> float | None:
    try:
        value = float(_NATIVE_TIME_TIME())
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def build_session_token_install_authority_request(
    *,
    logical_session_id: object,
    runtime_revision: object,
) -> dict[str, Any] | None:
    """Build one bounded install request without caller-selected policy.

    This is request material only. Maestro remains the decision authority, and
    :func:`consume_maestro_authority_decision` still requires the exact signed
    one-use receipt before the lifecycle caller may create a token.
    """

    checked_session_id = _safe_id(logical_session_id)
    if (
        checked_session_id is None
        or type(runtime_revision) is not str
        or _GIT_SHA_RE.fullmatch(runtime_revision) is None
    ):
        return None
    now = _wall_clock_now()
    if now is None:
        return None
    try:
        nonce = _NATIVE_OS_URANDOM(16)
    except Exception:
        return None
    if type(nonce) is not bytes or len(nonce) != 16:
        return None
    decision_id = f"hermes-session-token-install-{nonce.hex()}"
    if _safe_id(decision_id) is None:
        return None
    return {
        "contract_version": HERMES_OPERATIONAL_CONTEXT_VERSION,
        "authority_bundle": {
            "identity": HERMES_MAESTRO_AUTHORITY_BUNDLE_ID,
            "version": HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
            "digest": HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST,
        },
        "threshold_policy": {
            "version": HERMES_TELEMETRY_SCHEMA_VERSION,
            "digest": HERMES_TELEMETRY_SCHEMA_DIGEST,
        },
        "decision_binding": {
            "decision_id": decision_id,
            "requester": HERMES_AUTHORITY_CONSUMER,
            "account_id": "orch-next-runtime",
            "project_id": "hermes-exclusive-harness",
            "logical_session_id": checked_session_id,
            "method": HERMES_OPERATIONAL_METHOD,
            "target": HERMES_OPERATIONAL_TARGET,
            "runtime_revision": runtime_revision,
        },
        "goal": HERMES_OPERATIONAL_GOAL,
        "operation": HERMES_OPERATIONAL_METHOD,
        "target": HERMES_OPERATIONAL_TARGET,
        "revision": HERMES_OPERATIONAL_REVISION,
        "issued_at": now,
        "expires_at": now + HERMES_SESSION_TOKEN_REQUEST_TTL_SECONDS,
        "operation_id": decision_id,
        "task_declaration": {
            "task_class": "operations",
            "prompt_contract_version": HERMES_SESSION_TOKEN_PROMPT_CONTRACT_VERSION,
            "prompt_contract_digest": HERMES_SESSION_TOKEN_PROMPT_CONTRACT_DIGEST,
        },
    }


def _snapshot_context(
    value: object, *, now: float
) -> tuple[str, dict[str, Any] | None]:
    context = _exact_dict(value, _CONTEXT_KEYS)
    if context is None:
        return "authority_contract_unavailable", None
    authority = _exact_dict(
        context["authority_bundle"], frozenset({"identity", "version", "digest"})
    )
    threshold = _exact_dict(
        context["threshold_policy"], frozenset({"version", "digest"})
    )
    binding = _exact_dict(
        context["decision_binding"],
        frozenset({
            "decision_id",
            "requester",
            "account_id",
            "project_id",
            "logical_session_id",
            "method",
            "target",
            "runtime_revision",
        }),
    )
    declaration = _exact_dict(
        context["task_declaration"],
        frozenset({"task_class", "prompt_contract_version", "prompt_contract_digest"}),
    )
    if any(item is None for item in (authority, threshold, binding, declaration)):
        return "authority_contract_unavailable", None
    assert authority is not None
    assert threshold is not None
    assert binding is not None
    assert declaration is not None

    for value_to_check in (
        authority["identity"],
        authority["version"],
        authority["digest"],
        binding["requester"],
        binding["method"],
        binding["target"],
        context["contract_version"],
        context["goal"],
        context["operation"],
        context["target"],
    ):
        if type(value_to_check) is not str:
            return "authority_contract_unavailable", None
    if (
        authority["identity"] != HERMES_MAESTRO_AUTHORITY_BUNDLE_ID
        or authority["version"] != HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION
        or authority["digest"] != HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST
        or binding["requester"] != HERMES_AUTHORITY_CONSUMER
        or binding["method"] != HERMES_OPERATIONAL_METHOD
        or binding["target"] != HERMES_OPERATIONAL_TARGET
    ):
        return "authority_mismatch", None
    if (
        context["contract_version"] != HERMES_OPERATIONAL_CONTEXT_VERSION
        or context["goal"] != HERMES_OPERATIONAL_GOAL
        or context["operation"] != HERMES_OPERATIONAL_METHOD
        or context["target"] != HERMES_OPERATIONAL_TARGET
        or type(context["revision"]) is not int
        or context["revision"] != HERMES_OPERATIONAL_REVISION
    ):
        return "authority_mismatch", None

    safe_binding: dict[str, str] = {}
    for key in (
        "decision_id",
        "account_id",
        "project_id",
        "logical_session_id",
    ):
        checked = _safe_id(binding[key])
        if checked is None:
            return "authority_contract_unavailable", None
        safe_binding[key] = checked
    runtime_revision = binding["runtime_revision"]
    if (
        type(runtime_revision) is not str
        or _GIT_SHA_RE.fullmatch(runtime_revision) is None
    ):
        return "authority_contract_unavailable", None
    safe_binding["runtime_revision"] = runtime_revision
    operation_id = _safe_id(context["operation_id"])
    threshold_version = (
        threshold["version"]
        if type(threshold["version"]) is str
        and _VERSION_RE.fullmatch(threshold["version"])
        else None
    )
    if (
        operation_id is None
        or threshold_version is None
        or type(threshold["digest"]) is not str
        or not _SHA256_RE.fullmatch(threshold["digest"])
        or type(declaration["task_class"]) is not str
        or declaration["task_class"] not in _TASK_CLASSES
        or type(declaration["prompt_contract_version"]) is not str
        or not _VERSION_RE.fullmatch(declaration["prompt_contract_version"])
        or type(declaration["prompt_contract_digest"]) is not str
        or not _SHA256_RE.fullmatch(declaration["prompt_contract_digest"])
    ):
        return "authority_contract_unavailable", None

    issued_at = _safe_time(context["issued_at"])
    expires_at = _safe_time(context["expires_at"])
    if issued_at is None or expires_at is None:
        return "authority_contract_unavailable", None
    if (
        issued_at > now + 5.0
        or expires_at <= now
        or expires_at <= issued_at
        or expires_at - issued_at > HERMES_CONTEXT_MAX_TTL_SECONDS
    ):
        return "authority_stale", None

    return (
        "context_accepted",
        {
            "contract_version": HERMES_OPERATIONAL_CONTEXT_VERSION,
            "authority_bundle": {
                "identity": HERMES_MAESTRO_AUTHORITY_BUNDLE_ID,
                "version": HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
                "digest": HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST,
            },
            "threshold_policy": {
                "version": threshold_version,
                "digest": threshold["digest"],
            },
            "decision_binding": {
                **safe_binding,
                "requester": HERMES_AUTHORITY_CONSUMER,
                "method": HERMES_OPERATIONAL_METHOD,
                "target": HERMES_OPERATIONAL_TARGET,
            },
            "goal": HERMES_OPERATIONAL_GOAL,
            "operation": HERMES_OPERATIONAL_METHOD,
            "target": HERMES_OPERATIONAL_TARGET,
            "revision": HERMES_OPERATIONAL_REVISION,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "operation_id": operation_id,
            "task_declaration": {
                "task_class": declaration["task_class"],
                "prompt_contract_version": declaration["prompt_contract_version"],
                "prompt_contract_digest": declaration["prompt_contract_digest"],
            },
        },
    )


def _snapshot_actual(value: object) -> dict[str, str] | None:
    actual = _exact_dict(value, _ACTUAL_KEYS)
    if actual is None:
        return None
    logical_session_id = _safe_id(actual["logical_session_id"])
    ui_session_id = _safe_id(actual["ui_session_id"], allow_empty=True)
    runtime_revision = actual["runtime_revision"]
    if (
        logical_session_id is None
        or ui_session_id is None
        or type(runtime_revision) is not str
        or _GIT_SHA_RE.fullmatch(runtime_revision) is None
        or actual["method"] != HERMES_OPERATIONAL_METHOD
        or actual["target"] != HERMES_OPERATIONAL_TARGET
    ):
        return None
    return {
        "logical_session_id": logical_session_id,
        "ui_session_id": ui_session_id,
        "method": HERMES_OPERATIONAL_METHOD,
        "target": HERMES_OPERATIONAL_TARGET,
        "runtime_revision": runtime_revision,
    }


def _validate_receipt(
    value: object,
    *,
    context: dict[str, Any],
    actual: dict[str, str],
    now: float,
) -> tuple[str, bool, dict[str, Any] | None]:
    receipt = _exact_dict(value, _RECEIPT_KEYS)
    if receipt is None:
        return "authority_contract_unavailable", False, None
    binding = context["decision_binding"]
    expected = {
        "decision_id": binding["decision_id"],
        "authority_owner": HERMES_AUTHORITY_OWNER,
        "authority_bundle_version": HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": HERMES_AUTHORITY_CONSUMER,
        "telemetry_schema_version": HERMES_TELEMETRY_SCHEMA_VERSION,
        "telemetry_schema_digest": HERMES_TELEMETRY_SCHEMA_DIGEST,
        "rollback_admission_version": HERMES_ROLLBACK_ADMISSION_VERSION,
        "rollback_admission_digest": HERMES_ROLLBACK_ADMISSION_DIGEST,
        "account_id": binding["account_id"],
        "project_id": binding["project_id"],
        "logical_session_id": actual["logical_session_id"],
        "ui_session_id": actual["ui_session_id"],
        "method": actual["method"],
        "target": actual["target"],
        "runtime_revision": actual["runtime_revision"],
        "issued_at": context["issued_at"],
        "expires_at": context["expires_at"],
        "consumed_once": True,
    }
    if any(
        type(receipt[key]) is not type(expected_value)
        for key, expected_value in expected.items()
    ):
        return "authority_mismatch", False, None
    if any(receipt[key] != expected_value for key, expected_value in expected.items()):
        return "authority_mismatch", False, None
    if receipt["expires_at"] <= now:
        return "authority_stale", False, None

    provenance = _exact_dict(
        receipt["runtime_provenance_manifest"],
        _RUNTIME_PROVENANCE_MANIFEST_KEYS,
    )
    provenance_digest = receipt["runtime_provenance_manifest_digest"]
    if (
        provenance is None
        or type(provenance_digest) is not str
        or _SHA256_RE.fullmatch(provenance_digest) is None
        or type(provenance["upstreamReleaseTag"]) is not str
        or _VERSION_RE.fullmatch(provenance["upstreamReleaseTag"]) is None
        or type(provenance["upstreamPackageVersion"]) is not str
        or _VERSION_RE.fullmatch(provenance["upstreamPackageVersion"]) is None
        or type(provenance["upstreamCommit"]) is not str
        or _GIT_SHA_RE.fullmatch(provenance["upstreamCommit"]) is None
        or type(provenance["runtimeCommit"]) is not str
        or _GIT_SHA_RE.fullmatch(provenance["runtimeCommit"]) is None
        or type(provenance["runtimeContentDigest"]) is not str
        or _SHA256_RE.fullmatch(provenance["runtimeContentDigest"]) is None
        or _NATIVE_SHA256(_canonical_json_bytes(provenance)).hexdigest()
        != provenance_digest
    ):
        return "authority_mismatch", False, None

    if (
        type(receipt["outcome"]) is not str
        or type(receipt["code"]) is not str
        or type(receipt["final_decision_state"]) is not str
        or type(receipt["final_execution_permitted"]) is not bool
    ):
        return "authority_mismatch", False, None

    if receipt["outcome"] == "allow":
        if (
            receipt["code"] != "authority_allowed"
            or receipt["final_decision_state"] != "final_allowed_once"
            or receipt["final_execution_permitted"] is not True
        ):
            return "authority_mismatch", False, None
        if provenance["runtimeCommit"] != actual["runtime_revision"]:
            return "authority_mismatch", False, None
        decision_export = {
            "runtime_provenance_manifest": provenance,
            "runtime_provenance_manifest_digest": provenance_digest,
        }
        return "authority_allowed", True, decision_export
    if receipt["outcome"] == "deny":
        if (
            receipt["code"] != "authority_denied"
            or receipt["final_decision_state"] != "final_denied"
            or receipt["final_execution_permitted"] is not False
        ):
            return "authority_mismatch", False, None
        return "authority_denied", False, None
    return "authority_contract_unavailable", False, None


def consume_maestro_authority_decision(
    operational_context: object,
    actual_identity: object,
) -> dict[str, Any]:
    """Consume one external Maestro authority decision with no fallback.

    Returned shapes intentionally match ``tui_gateway.server``'s existing
    ``_orch_authority_validator`` seam.  All failures are value-free stable
    codes; transport exceptions and provider data are never reflected.
    """

    checked_now = _wall_clock_now()
    if checked_now is None:
        return _typed_deny("authority_contract_unavailable")
    context_code, context = _snapshot_context(operational_context, now=checked_now)
    if context is None:
        return _typed_deny(context_code)
    actual = _snapshot_actual(actual_identity)
    if actual is None:
        return _typed_deny("authority_mismatch")
    binding = context["decision_binding"]
    if binding["logical_session_id"] != actual["logical_session_id"]:
        return _typed_deny("authority_mismatch")
    if binding["runtime_revision"] != actual["runtime_revision"]:
        return _typed_deny("authority_mismatch")

    decision_id = binding["decision_id"]
    with _state_lock:
        if (
            decision_id in _pending_decision_ids
            or decision_id in _consumed_decision_ids
        ):
            return _typed_deny("authority_replay")
        transport = _authority_transport
        receipt_origin_verifier = _authority_receipt_origin_verifier
        generation = _transport_generation
        if transport is None and receipt_origin_verifier is None and generation is None:
            transport = _fixed_protected_authority_transport
            receipt_origin_verifier = _verify_fixed_protected_receipt_origin
            generation = _PROTECTED_TRANSPORT_GENERATION
        _pending_decision_ids.add(decision_id)

    try:
        if transport is None or receipt_origin_verifier is None or generation is None:
            return _typed_deny("authority_contract_unavailable")
        # The transport cannot rewrite the snapshots against which its receipt
        # is checked.  This also prevents a buggy in-process adapter from
        # smuggling additional fields into the authority request.
        receipt = transport(deepcopy(context), dict(actual))
    except Exception:
        result = _typed_deny("authority_contract_unavailable")
    else:
        with _state_lock:
            generation_current = (
                generation is _PROTECTED_TRANSPORT_GENERATION
                and _authority_transport is None
                and _authority_receipt_origin_verifier is None
                and _transport_generation is None
            ) or generation is _transport_generation
        if not generation_current:
            result = _typed_deny("authority_contract_unavailable")
        else:
            try:
                provenance_verified = receipt_origin_verifier(
                    receipt, deepcopy(context), dict(actual)
                )
            except Exception:
                provenance_verified = False
            if provenance_verified is not True:
                result = _typed_deny("authority_contract_unavailable")
            else:
                receipt_now = _wall_clock_now()
                if receipt_now is None:
                    result = _typed_deny("authority_contract_unavailable")
                else:
                    receipt_code, allowed, decision_export = _validate_receipt(
                        receipt, context=context, actual=actual, now=receipt_now
                    )
                    if allowed and decision_export is not None:
                        result = {
                            "outcome": "allow",
                            "decision_id": decision_id,
                            "consumed_once": True,
                            **decision_export,
                        }
                    else:
                        result = _typed_deny(receipt_code)
    finally:
        with _state_lock:
            _pending_decision_ids.discard(decision_id)
            _consumed_decision_ids.add(decision_id)
    return result


def _snapshot_terminal_actual(value: object) -> dict[str, Any] | None:
    actual = _exact_dict(value, _TERMINAL_ACTUAL_KEYS)
    if actual is None:
        return None
    logical_session_id = _safe_id(actual["logical_session_id"])
    ui_session_id = _safe_id(actual["ui_session_id"], allow_empty=True)
    runtime_revision = actual["runtime_revision"]
    requested_transition = actual["requested_transition"]
    controller_owner_id = _safe_id(actual["controller_owner_id"])
    owner_epoch = actual["owner_epoch"]
    if (
        logical_session_id is None
        or ui_session_id is None
        or type(runtime_revision) is not str
        or _GIT_SHA_RE.fullmatch(runtime_revision) is None
        or type(requested_transition) is not str
        or requested_transition not in _TERMINAL_TRANSITIONS
        or controller_owner_id is None
        or type(owner_epoch) is not int
        or isinstance(owner_epoch, bool)
        or owner_epoch <= 0
    ):
        return None
    return {
        "logical_session_id": logical_session_id,
        "ui_session_id": ui_session_id,
        "runtime_revision": runtime_revision,
        "requested_transition": requested_transition,
        "controller_owner_id": controller_owner_id,
        "owner_epoch": owner_epoch,
    }


def _validate_terminal_receipt(
    value: object,
    *,
    actual: dict[str, Any],
    now: float,
) -> dict[str, Any] | None:
    receipt = _exact_dict(value, _TERMINAL_RECEIPT_KEYS)
    if receipt is None:
        return None
    decision_id = _safe_id(receipt["decision_id"])
    controller_owner_id = _safe_id(receipt["controller_owner_id"])
    issued_at = _safe_time(receipt["issued_at"])
    expires_at = _safe_time(receipt["expires_at"])
    findings = receipt["blocking_findings"]
    if (
        receipt["contract_id"] != HERMES_TERMINAL_AUTHORITY_CONTRACT_ID
        or receipt["contract_version"] != HERMES_TERMINAL_AUTHORITY_CONTRACT_VERSION
        or receipt["authority_source_sha256"] != HERMES_TERMINAL_AUTHORITY_SOURCE_SHA256
        or receipt["profile_sha256"] != HERMES_TERMINAL_PROFILE_SHA256
        or decision_id is None
        or controller_owner_id is None
        or type(receipt["owner_epoch"]) is not int
        or isinstance(receipt["owner_epoch"], bool)
        or receipt["owner_epoch"] < 0
        or receipt["consumer_decision"] not in _TERMINAL_DECISIONS
        or type(receipt["admitted"]) is not bool
        or type(findings) is not list
        or len(findings) > 64
        or any(
            type(item) is not str or _VERSION_RE.fullmatch(item) is None
            for item in findings
        )
        or len(set(findings)) != len(findings)
        or receipt["consumed_once"] is not True
        or issued_at is None
        or expires_at is None
        or issued_at > now + 5.0
        or expires_at <= now
        or expires_at <= issued_at
        or expires_at - issued_at > HERMES_CONTEXT_MAX_TTL_SECONDS
        or any(receipt[key] != actual[key] for key in _TERMINAL_ACTUAL_KEYS)
    ):
        return None
    decision = receipt["consumer_decision"]
    expected_admitted = decision.startswith("ALLOW_") and not findings
    if receipt["admitted"] is not expected_admitted:
        return None
    return {
        "consumer_decision": decision,
        "admitted": expected_admitted,
        "blocking_findings": list(findings),
        "decision_id": decision_id,
        "controller_owner_id": controller_owner_id,
        "owner_epoch": receipt["owner_epoch"],
        "consumed_once": True,
    }


def consume_maestro_terminal_decision(actual_identity: object) -> dict[str, Any]:
    """Consume one signed atomic pre-idle decision from Maestro.

    Hermes supplies no Grand Goal facts and evaluates no authority predicates.
    The protected Maestro host owns that snapshot and decision. Missing host
    wiring, an invalid signature, stale binding, replay, or malformed response
    keeps the current controller active.
    """
    actual = _snapshot_terminal_actual(actual_identity)
    if actual is None:
        return _typed_terminal_deny("terminal_authority_binding_invalid")
    with _terminal_call_lock:
        try:
            receipt = _fixed_protected_terminal_transport(dict(actual))
        except Exception:
            return _typed_terminal_deny("terminal_authority_unavailable")
        now = _wall_clock_now()
        if now is None:
            return _typed_terminal_deny("terminal_authority_unavailable")
        checked = _validate_terminal_receipt(receipt, actual=actual, now=now)
        if checked is None:
            return _typed_terminal_deny("terminal_authority_mismatch")
        decision_id = checked["decision_id"]
        with _state_lock:
            if decision_id in _consumed_terminal_decision_ids:
                return _typed_terminal_deny("terminal_authority_replay")
            _consumed_terminal_decision_ids.add(decision_id)
        return checked


__all__ = [
    "AuthorityTransportInstallation",
    "build_session_token_install_authority_request",
    "consume_maestro_authority_decision",
    "consume_maestro_terminal_decision",
    "install_maestro_authority_transport",
    "reset_maestro_authority_transport",
]
