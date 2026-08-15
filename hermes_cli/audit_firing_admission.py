"""Exclusive Hermes consumer for the protected INC-191 host operation.

Maestro owns the immutable classifier and signed terminal ledger. Hermes owns
only the source-side operational call boundary. The public operation accepts
one exact sanitized C0 request; transport, verifier, signing identity, runtime
generation, revision, key path, socket and namespace are fixed or loaded from
one protected local runtime configuration file.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tui_gateway import maestro_authority as _authority


INC191_AUTHORITY_CONTRACT: Final = "INC191AuditFiringAdmissionC0.v1"
INC191_AUTHORITY_SOURCE_COMMIT: Final = "0c7ba204f210687c1d294592965929ab45294f8b"
INC191_AUTHORITY_SOURCE_PATH: Final = "scripts/ops/inc191_audit_firing_admission.py"
INC191_AUTHORITY_SOURCE_SHA256: Final = (
    "829bb45928b02a5cdafa042ec6098020f8d5611d9aa7a4bf3af3435e77d94289"
)
INC191_AUDIT_READBACK_SCHEMA: Final = "audit_readback.v1#inc191-firing-admission.v1"
INC191_HOST_OPERATION: Final = "verify_consume_inc191_audit_firing"
INC191_HOST_CONSUMER: Final = "hermes_host_runtime"
INC191_HOST_PROVENANCE: Final = "protected_maestro_host_runtime"
INC191_RUNTIME_SIGNATURE_NAMESPACE: Final = "orchnext-hermes-inc191-runtime-v1"

_RUNTIME_CONFIG_LEAF = "inc191-runtime.json"
_SSH_KEYGEN = Path("/usr/bin/ssh-keygen")
_SIGN_TIMEOUT_SECONDS = 5.0
_MAX_CONFIG_BYTES = 4 * 1024
_MAX_PRIVATE_KEY_BYTES = 64 * 1024
_MAX_SIGNATURE_BYTES = 32 * 1024
_GENERATION_RE = re.compile(r"inc191-g([1-9][0-9]{0,8})\Z")
_RUNTIME_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_FIRING_SUFFIX_RE = re.compile(r"[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,159}\Z")
_AWS_ACCESS_KEY_RE = re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}\Z", re.IGNORECASE)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?:^|[-_.:])(?:prompt|log|secret|credential|password|passphrase|token|auth|cookie|private|reasoning)(?:[-_.:]|$)",
    re.IGNORECASE,
)
_LONG_OPAQUE_RE = re.compile(r"[A-Za-z0-9+_=-]{32,}\Z")

_DECISION_KEYS = frozenset({"action"})
_DECISION_ACTIONS = frozenset({"keep", "demote"})

_REQUEST_KEYS = frozenset({
    "firing_id",
    "target_ref",
    "target_cursor_before",
    "target_cursor_after",
    "target_digest_before",
    "target_digest_after",
    "target_read_result",
    "independent_surfaces_checked",
    "owner_claim_refs",
    "independent_evidence_refs",
    "trigger_set",
    "panoramic_surfaces_checked",
    "decision_before",
    "decision_after",
    "decision_delta",
    "notification_decision",
    "visibility_debt",
    "model_invocation",
    "bounded_usage_delta",
})
_CALLER_REQUEST_KEYS = _REQUEST_KEYS - {"firing_id"}
_TARGET_READ_KEYS = frozenset({"status", "receipt_ref"})
_MODEL_INVOCATION_KEYS = frozenset({
    "occurred_before_admission",
    "requested_if_admitted",
})
_USAGE_KEYS = frozenset({"status", "value"})
_AUTHORITY_SOURCE_KEYS = frozenset({
    "repository",
    "commit",
    "contract",
    "path",
    "sha256",
})
_WIRE_REQUEST_KEYS = frozenset({
    "operation",
    "authority_source",
    "consumer_id",
    "generation",
    "runtime_revision",
    "request",
    "request_sha256",
    "operation_nonce",
    "caller_proof",
})
_UNSIGNED_REQUEST_KEYS = _WIRE_REQUEST_KEYS - {"caller_proof"}
_BINDING_KEYS = frozenset({
    "authority_source",
    "consumer_id",
    "firing_id",
    "request_sha256",
    "operation_nonce",
    "consumed_once",
    "provenance",
})
_HOST_RECEIPT_KEYS = frozenset({"binding", "audit_readback"})
_AUDIT_RESULT_KEYS = frozenset({
    "schema_version",
    "firing_id",
    "receipt_result",
    "audit_verdict_allowed",
    "model_invocation_allowed",
    "notification_allowed",
    "visibility_debt",
    "no_delta",
    "reasons",
    "bounded_usage_delta",
    "firing_admission",
    "provider_authority",
    "model_invocations",
})
_FIRING_ADMISSION_KEYS = frozenset({
    "target_ref",
    "target_cursor_before",
    "target_cursor_after",
    "target_digest_before",
    "target_digest_after",
    "target_read_result",
    "independent_surfaces_checked",
    "owner_claim_refs",
    "independent_evidence_refs",
    "trigger_set",
    "panoramic_surfaces_checked",
    "decision_before",
    "decision_after",
    "decision_delta",
    "notification_decision",
    "visibility_debt",
    "model_invocation",
    "bounded_usage_delta",
    "receipt_result",
})
_PINNED_RECEIPT_RESULT = "MALFORMED_FIRING_REJECTED"
_ALLOWED_REASON_CODES = frozenset({
    "THREAT_BOUNDARY_UNIMPLEMENTED_WITHOUT_HOST_INTEGRATION",
    "caller_authentication_unavailable",
    "caller_authentication_failed",
    "runtime_revision_mismatch",
    "firing_generation_mismatch",
    "firing_ledger_capacity_reached",
    "firing_id_already_terminal",
    "authority_classification_invalid",
    "authority_classification_unavailable",
})


@dataclass(frozen=True)
class _RuntimeConfig:
    generation: str
    runtime_revision: str
    signing_key_path: Path


class _HostTransportFailure(Exception):
    def __init__(self, *, sent: bool) -> None:
        super().__init__("protected host unavailable")
        self.sent = sent


_state_lock = threading.Lock()
_pending_firing_ids: set[str] = set()


def _typed_deny(code: str) -> dict[str, str]:
    return {"outcome": "deny", "code": code}


def _exact_dict(value: object, keys: frozenset[str]) -> dict[str, Any] | None:
    if type(value) is not dict:
        return None
    actual = tuple(dict.keys(value))
    if any(type(key) is not str for key in actual) or frozenset(actual) != keys:
        return None
    return {key: dict.__getitem__(value, key) for key in keys}


def _same_typed_value(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if frozenset(dict.keys(left)) != frozenset(dict.keys(right)):  # type: ignore[arg-type]
            return False
        return all(
            _same_typed_value(dict.__getitem__(left, key), dict.__getitem__(right, key))  # type: ignore[arg-type]
            for key in dict.keys(left)
        )
    if type(left) is list:
        return len(left) == len(right) and all(  # type: ignore[arg-type]
            _same_typed_value(item, right[index])  # type: ignore[index]
            for index, item in enumerate(left)
        )
    return left == right


def _authority_source() -> dict[str, str]:
    return {
        "repository": "maestro-kernel",
        "commit": INC191_AUTHORITY_SOURCE_COMMIT,
        "contract": INC191_AUTHORITY_CONTRACT,
        "path": INC191_AUTHORITY_SOURCE_PATH,
        "sha256": INC191_AUTHORITY_SOURCE_SHA256,
    }


def _runtime_config_path() -> Path:
    return _authority._PROTECTED_AUTHORITY_HOME / "authority" / _RUNTIME_CONFIG_LEAF


def _secure_regular_snapshot(path: Path, *, max_bytes: int) -> bytes | None:
    fd = -1
    try:
        if not path.is_absolute() or not _authority._path_has_no_symlink_components(
            path
        ):
            return None
        no_follow = getattr(os, "O_NOFOLLOW", None)
        nonblocking = getattr(os, "O_NONBLOCK", None)
        if no_follow is None or nonblocking is None:
            return None
        fd = os.open(path, os.O_RDONLY | no_follow | nonblocking)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            return None
        chunks = bytearray()
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                return None
        after = os.fstat(fd)
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
            return None
        return bytes(chunks)
    except Exception:
        return None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def _secure_private_key(path: Path) -> bool:
    try:
        if not path.is_absolute() or not _authority._path_has_no_symlink_components(
            path
        ):
            return False
        parent = os.lstat(path.parent)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            return False
    except Exception:
        return False
    return _secure_regular_snapshot(path, max_bytes=_MAX_PRIVATE_KEY_BYTES) is not None


def _load_runtime_config() -> _RuntimeConfig | None:
    path = _runtime_config_path()
    raw = _secure_regular_snapshot(path, max_bytes=_MAX_CONFIG_BYTES)
    if raw is None:
        return None
    if not raw.isascii():
        return None
    parsed = _authority._parse_canonical_authority_payload(raw)
    value = _exact_dict(
        parsed, frozenset({"generation", "runtime_revision", "signing_key_path"})
    )
    if value is None:
        return None
    generation = value["generation"]
    revision = value["runtime_revision"]
    key_value = value["signing_key_path"]
    if (
        type(generation) is not str
        or _GENERATION_RE.fullmatch(generation) is None
        or type(revision) is not str
        or _RUNTIME_REVISION_RE.fullmatch(revision) is None
        or type(key_value) is not str
        or not key_value
    ):
        return None
    key_path = Path(key_value)
    if key_path == path or not _secure_private_key(key_path):
        return None
    return _RuntimeConfig(generation, revision, key_path)


def _looks_sensitive(value: str) -> bool:
    lowered = value.lower()
    return (
        "://" in value
        or "/" in value
        or "\\" in value
        or "localhost" in lowered
        or ".local" in lowered
        or ".internal" in lowered
        or "private" in lowered
        or lowered.startswith(("sk-", "ghp_", "gho_", "xox"))
        or value.startswith(("AIza", "eyJ"))
        or _AWS_ACCESS_KEY_RE.fullmatch(value) is not None
        or _SENSITIVE_VALUE_RE.search(value) is not None
        or re.fullmatch(r"[0-9a-f]{40,}", value, re.IGNORECASE) is not None
        or (
            _LONG_OPAQUE_RE.fullmatch(value) is not None
            and any(character.isalpha() for character in value)
            and any(character.isdigit() for character in value)
        )
    )


def _safe_ref(value: object) -> bool:
    return (
        type(value) is str
        and _SAFE_REF_RE.fullmatch(value) is not None
        and not _looks_sensitive(value)
    )


def _string_list(value: object) -> list[str] | None:
    if type(value) is not list or len(value) > 64:
        return None
    if any(not _safe_ref(item) for item in value) or len(value) != len(set(value)):
        return None
    return list(value)


def _safe_decision(value: object) -> dict[str, Any] | None:
    decision = _exact_dict(value, _DECISION_KEYS)
    if decision is None:
        return None
    action = decision["action"]
    if type(action) is not str or action not in _DECISION_ACTIONS:
        return None
    return {"action": action}


def _valid_firing_id(value: object, generation: str) -> bool:
    if type(value) is not str or value.count(":") != 1:
        return False
    actual_generation, suffix = value.split(":", 1)
    return (
        actual_generation == generation
        and _FIRING_SUFFIX_RE.fullmatch(suffix) is not None
    )


def _new_firing_id(generation: str) -> str:
    if _GENERATION_RE.fullmatch(generation) is None:
        raise ValueError("invalid INC-191 generation")
    return f"{generation}:{os.urandom(16).hex()}"


def _snapshot_request(
    value: object, *, generation: str, firing_id: str
) -> dict[str, Any] | None:
    caller_request = _exact_dict(value, _CALLER_REQUEST_KEYS)
    if caller_request is None or not _valid_firing_id(firing_id, generation):
        return None
    request = {**caller_request, "firing_id": firing_id}
    if not _safe_ref(request["target_ref"]):
        return None
    for field in ("target_cursor_before", "target_cursor_after"):
        if request[field] is not None and not _safe_ref(request[field]):
            return None
    for field in ("target_digest_before", "target_digest_after"):
        if request[field] is not None and (
            type(request[field]) is not str
            or _SHA256_RE.fullmatch(request[field]) is None
        ):
            return None
    for field in (
        "independent_surfaces_checked",
        "owner_claim_refs",
        "independent_evidence_refs",
        "trigger_set",
        "panoramic_surfaces_checked",
    ):
        normalized = _string_list(request[field])
        if normalized is None:
            return None
        request[field] = normalized
    before = _safe_decision(request["decision_before"])
    after = _safe_decision(request["decision_after"])
    if before is None or after is None:
        return None
    request["decision_before"] = before
    request["decision_after"] = after
    if (
        type(request["decision_delta"]) is not bool
        or type(request["visibility_debt"]) is not bool
    ):
        return None
    if request["notification_decision"] not in {"NOTIFY", "DONT_NOTIFY"}:
        return None
    target_read = _exact_dict(request["target_read_result"], _TARGET_READ_KEYS)
    if (
        target_read is None
        or target_read["status"] not in {"success", "timeout", "unread", "error"}
        or not _safe_ref(target_read["receipt_ref"])
    ):
        return None
    request["target_read_result"] = target_read
    model = _exact_dict(request["model_invocation"], _MODEL_INVOCATION_KEYS)
    if model is None or any(type(model[key]) is not bool for key in model):
        return None
    request["model_invocation"] = model
    usage = _exact_dict(request["bounded_usage_delta"], _USAGE_KEYS)
    if usage is None:
        return None
    if usage["status"] == "known":
        if (
            type(usage["value"]) is not int
            or not 0 <= usage["value"] <= 1_000_000_000_000
        ):
            return None
    elif usage["status"] == "UNKNOWN":
        if usage["value"] is not None:
            return None
    else:
        return None
    request["bounded_usage_delta"] = usage
    return request


def _validated_ssh_keygen() -> bool:
    try:
        info = os.lstat(_SSH_KEYGEN)
    except Exception:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == 0
        and not info.st_mode & 0o022
        and os.access(_SSH_KEYGEN, os.X_OK)
    )


def _sign_unsigned_request(payload: bytes, config: _RuntimeConfig) -> str | None:
    if not _validated_ssh_keygen() or not _secure_private_key(config.signing_key_path):
        return None
    directory: str | None = None
    payload_path: Path | None = None
    signature_path: Path | None = None
    try:
        directory = tempfile.mkdtemp(
            prefix=".inc191-sign-", dir=str(_runtime_config_path().parent)
        )
        os.chmod(directory, 0o700)
        payload_path = Path(directory) / "request.json"
        signature_path = Path(f"{payload_path}.sig")
        fd = os.open(
            payload_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    return None
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        completed = subprocess.run(
            [
                str(_SSH_KEYGEN),
                "-Y",
                "sign",
                "-f",
                str(config.signing_key_path),
                "-n",
                INC191_RUNTIME_SIGNATURE_NAMESPACE,
                str(payload_path),
            ],
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_SIGN_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0 or not _secure_private_key(
            config.signing_key_path
        ):
            return None
        os.chmod(signature_path, 0o600, follow_symlinks=False)
        signature = _secure_regular_snapshot(
            signature_path, max_bytes=_MAX_SIGNATURE_BYTES
        )
        if signature is None:
            return None
        text = signature.decode("ascii")
        if not text.startswith("-----BEGIN SSH SIGNATURE-----\n") or not text.endswith(
            "-----END SSH SIGNATURE-----\n"
        ):
            return None
        return text
    except Exception:
        return None
    finally:
        for path in (signature_path, payload_path):
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        if directory is not None:
            try:
                os.rmdir(directory)
            except OSError:
                pass


def _request_fixed_host(request: dict[str, Any]) -> object:
    sent = False
    try:
        if not _authority._trusted_runtime_boundary():
            raise RuntimeError
        allowed_signers = _authority._fixed_allowed_signers_content()
        socket_path = _authority._fixed_authority_socket_path()
        if allowed_signers is None or socket_path is None:
            raise RuntimeError
        request_bytes = _authority._canonical_authority_payload(request)
        if request_bytes is None:
            raise RuntimeError
        deadline = (
            _authority._NATIVE_TIME_MONOTONIC()
            + _authority._PROTECTED_AUTHORITY_CONNECT_TIMEOUT_SECONDS
        )

        def arm(client: object) -> None:
            remaining = deadline - _authority._NATIVE_TIME_MONOTONIC()
            if remaining <= 0:
                raise TimeoutError
            _authority._NATIVE_SOCKET_SETTIMEOUT(client, remaining)

        client = _authority._NATIVE_SOCKET_CLASS(
            _authority._NATIVE_SOCKET_AF_UNIX, _authority._NATIVE_SOCKET_SOCK_STREAM
        )
        try:
            arm(client)
            _authority._NATIVE_SOCKET_CONNECT(client, str(socket_path))
            arm(client)
            sent = True
            _authority._NATIVE_SOCKET_SENDALL(client, request_bytes + b"\n")
            chunks = bytearray()
            while b"\n" not in chunks:
                arm(client)
                chunk = _authority._NATIVE_SOCKET_RECV(client, 8192)
                if not chunk:
                    raise RuntimeError
                chunks.extend(chunk)
                if len(chunks) > _authority._PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES:
                    raise RuntimeError
        finally:
            _authority._NATIVE_SOCKET_CLOSE(client)
        line, separator, remainder = bytes(chunks).partition(b"\n")
        if separator != b"\n" or remainder:
            raise RuntimeError
        envelope = _exact_dict(
            _authority._parse_canonical_authority_payload(line),
            frozenset({"receipt", "signature"}),
        )
        if envelope is None:
            raise RuntimeError
        signed_payload = _authority._canonical_authority_payload({
            "request": request,
            "receipt": envelope["receipt"],
        })
        if signed_payload is None or not _authority._verify_sshsig_with_allowed_signers(
            signed_payload, envelope["signature"], allowed_signers
        ):
            raise RuntimeError
        return envelope["receipt"]
    except Exception:
        raise _HostTransportFailure(sent=sent) from None


def _validate_firing_admission(
    value: object, *, request: dict[str, Any], result: dict[str, Any]
) -> bool:
    admission = _exact_dict(value, _FIRING_ADMISSION_KEYS)
    if admission is None:
        return False
    for field in _FIRING_ADMISSION_KEYS - {
        "receipt_result",
        "bounded_usage_delta",
        "visibility_debt",
    }:
        expected = request[field]
        if not _same_typed_value(admission[field], expected):
            return False
    return (
        type(admission["visibility_debt"]) is bool
        and admission["visibility_debt"] is False
        and type(admission["bounded_usage_delta"]) is str
        and admission["bounded_usage_delta"] == "UNKNOWN"
        and type(admission["receipt_result"]) is str
        and admission["receipt_result"] == _PINNED_RECEIPT_RESULT
        and result["receipt_result"] == _PINNED_RECEIPT_RESULT
        and result["visibility_debt"] is False
    )


def _validate_reasons(value: object) -> bool:
    return (
        type(value) is list
        and len(value) == 1
        and type(value[0]) is str
        and value[0] in _ALLOWED_REASON_CODES
    )


def _validate_host_receipt(
    value: object,
    *,
    request: dict[str, Any],
    operation_nonce: str,
    request_sha256: str,
) -> dict[str, Any] | None:
    host_receipt = _exact_dict(value, _HOST_RECEIPT_KEYS)
    if host_receipt is None:
        return None
    binding = _exact_dict(host_receipt["binding"], _BINDING_KEYS)
    source = (
        _exact_dict(binding["authority_source"], _AUTHORITY_SOURCE_KEYS)
        if binding is not None
        else None
    )
    if binding is None or source is None:
        return None
    expected_binding = {
        "authority_source": _authority_source(),
        "consumer_id": INC191_HOST_CONSUMER,
        "firing_id": request["firing_id"],
        "request_sha256": request_sha256,
        "operation_nonce": operation_nonce,
        "consumed_once": True,
        "provenance": INC191_HOST_PROVENANCE,
    }
    for key, expected in expected_binding.items():
        if type(binding[key]) is not type(expected) or binding[key] != expected:
            return None
    result = _exact_dict(host_receipt["audit_readback"], _AUDIT_RESULT_KEYS)
    if result is None:
        return None
    if (
        type(result["schema_version"]) is not str
        or result["schema_version"] != INC191_AUDIT_READBACK_SCHEMA
        or type(result["firing_id"]) is not str
        or result["firing_id"] != request["firing_id"]
        or type(result["receipt_result"]) is not str
        or result["receipt_result"] != _PINNED_RECEIPT_RESULT
        or any(
            type(result[field]) is not bool
            for field in (
                "audit_verdict_allowed",
                "model_invocation_allowed",
                "notification_allowed",
                "visibility_debt",
                "no_delta",
                "provider_authority",
            )
        )
        or result["provider_authority"] is not False
        or type(result["model_invocations"]) is not int
        or result["model_invocations"] != 0
        or not _validate_reasons(result["reasons"])
        or not _validate_firing_admission(
            result["firing_admission"], request=request, result=result
        )
    ):
        return None
    if (
        result["audit_verdict_allowed"] is not False
        or result["model_invocation_allowed"] is not False
        or result["notification_allowed"] is not False
        or result["visibility_debt"] is not False
        or result["no_delta"] is not False
        or type(result["bounded_usage_delta"]) is not str
        or result["bounded_usage_delta"] != "UNKNOWN"
    ):
        return None
    encoded = _authority._canonical_authority_payload(result)
    parsed = (
        _authority._parse_canonical_authority_payload(encoded)
        if encoded is not None
        else None
    )
    return parsed if type(parsed) is dict else None


def consume_inc191_audit_firing(request: object) -> dict[str, Any]:
    """Sign, submit and consume one exact generation-bound C0 request."""

    config = _load_runtime_config()
    if config is None:
        return _typed_deny("inc191_runtime_configuration_unavailable")
    firing_id = _new_firing_id(config.generation)
    snapshot = _snapshot_request(
        request, generation=config.generation, firing_id=firing_id
    )
    if snapshot is None:
        return _typed_deny("inc191_audit_request_invalid")
    with _state_lock:
        if firing_id in _pending_firing_ids:
            return _typed_deny("inc191_audit_operation_in_flight")
        _pending_firing_ids.add(firing_id)

    sent = False
    try:
        request_bytes = _authority._canonical_authority_payload(snapshot)
        if request_bytes is None:
            return _typed_deny("inc191_host_verifier_unavailable")
        request_sha256 = _authority._NATIVE_SHA256(request_bytes).hexdigest()
        operation_nonce = os.urandom(32).hex()
        unsigned = {
            "operation": INC191_HOST_OPERATION,
            "authority_source": _authority_source(),
            "consumer_id": INC191_HOST_CONSUMER,
            "generation": config.generation,
            "runtime_revision": config.runtime_revision,
            "request": snapshot,
            "request_sha256": request_sha256,
            "operation_nonce": operation_nonce,
        }
        if frozenset(unsigned) != _UNSIGNED_REQUEST_KEYS:
            return _typed_deny("inc191_host_verifier_unavailable")
        unsigned_bytes = _authority._canonical_authority_payload(unsigned)
        if unsigned_bytes is None:
            return _typed_deny("inc191_host_verifier_unavailable")
        proof = _sign_unsigned_request(unsigned_bytes, config)
        if proof is None:
            return _typed_deny("inc191_caller_authentication_unavailable")
        host_request = {**unsigned, "caller_proof": proof}
        if frozenset(host_request) != _WIRE_REQUEST_KEYS:
            return _typed_deny("inc191_host_verifier_unavailable")
        try:
            receipt = _request_fixed_host(host_request)
            sent = True
        except _HostTransportFailure as failure:
            sent = failure.sent
            return _typed_deny(
                "inc191_host_response_unavailable"
                if sent
                else "inc191_host_verifier_unavailable"
            )
        except Exception:
            sent = True
            return _typed_deny("inc191_host_response_unavailable")
        verified = _validate_host_receipt(
            receipt,
            request=snapshot,
            operation_nonce=operation_nonce,
            request_sha256=request_sha256,
        )
        if verified is None:
            return _typed_deny("inc191_host_receipt_invalid")
        return verified
    finally:
        with _state_lock:
            _pending_firing_ids.discard(firing_id)


__all__ = ["consume_inc191_audit_firing"]
