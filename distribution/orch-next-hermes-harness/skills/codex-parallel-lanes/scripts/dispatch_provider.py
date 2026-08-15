#!/usr/bin/env python3
"""Dispatch one explicitly selected Hermes execution-plane provider.

This is the small operational seam for the existing ``codex-parallel-lanes``
skill.  It does not decide protected authority, install providers, or use a
provider as a fallback.  Hermes-native and Claude bridge selections return a
typed handoff; only an explicit ``codex_luna`` selection can invoke headless
Codex directly.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import select
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


LUNA_MODEL = "gpt-5.6-luna"
LUNA_EFFORT = "max"
SERVICE_TIER_PREFERENCE = "fast"
SANDBOX = "danger-full-access"
APPROVAL_POLICY = "never"
RECEIPT_TYPE = "codex_parallel_lane_dispatch_receipt.v1"
IDENTITY_RECEIPT_TYPE = "codex_parallel_lane_child_identity_readback.v1"
MAX_TERMINAL_OUTPUT_BYTES = 4096
MAX_ORDINARY_WRITERS = 4
WRITER_LOCK_ROOT_NAME = "hermes-codex-parallel-lanes"
WRITER_LEASE_SOURCE = "hermes_owner_private_durable_lease"
CODEX_PROVENANCE_SOURCE = "hermes_local_codex"
APP_SERVER_IDENTITY_SOURCE = "codex_app_server_thread_resume"
MAX_APP_SERVER_READBACK_BYTES = 65536
MAX_APP_SERVER_READ_TIMEOUT_SECONDS = 5.0
MAX_APP_SERVER_WAIT_TIMEOUT_SECONDS = 5.0

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,159}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EXPECTED_SURFACE = {
    "hermes_native": "delegate_task",
    "codex_luna": "direct_codex_exec",
    "codex_plugin_cc": "codex_plugin_cc",
}
_HANDOFF_CODE = {
    "hermes_native": "hermes_delegate_task_handoff",
    "codex_plugin_cc": "codex_plugin_cc_bridge_handoff",
}
ROUTINE_OPERATION_CLASSES = frozenset({
    "build",
    "claim_checks",
    "job_lifecycle",
    "local_patch",
    "nonprotected_validation",
    "normal_model_routing",
    "ordinary_branch_or_pr_work",
    "read_only",
    "test",
})
PROTECTED_OPERATION_CLASSES = frozenset({
    "credential_oauth_or_secret_mutation",
    "paid_provider_use",
    "public_deploy_or_release",
    "destructive_action",
    "protected_integration",
    "authority_transfer",
    "shared_security_or_runtime_mutation",
    "rollback_promotion",
    "final_acceptance",
})
OPERATION_CLASS_ALLOWLIST = (
    ROUTINE_OPERATION_CLASSES | PROTECTED_OPERATION_CLASSES
)
LUNA_EFFORT_BY_WORK_CLASS = {
    "deterministic_mechanical": "high",
    "precision_difficult": "max",
}
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|auth[_ -]?token|password|secret|token)"
    r"(\s*[:=]\s*)[^\s\r\n]+"
)
_CHILD_FAILURE_PATTERNS = (
    (
        re.compile(
            r"(?i)\b(?:unexpected|unknown|unrecognized|unrecognised|invalid)"
            r"\s+(?:argument|option|flag)\b|\b(?:argument|option|flag)\s+"
            r"(?:is\s+)?(?:unexpected|unknown|unrecognized|unrecognised|invalid)\b"
        ),
        "cli_argument_rejected",
    ),
    (
        re.compile(r"(?i)\b(?:auth|authentication|login|credential|unauthorized|forbidden)\b"),
        "authentication_rejected",
    ),
    (
        re.compile(r"(?i)\b(?:rate\s+limit|rate-limited|too\s+many\s+requests|quota|\b429\b)"),
        "rate_limited",
    ),
    (
        re.compile(r"(?i)\b(?:config|configuration|invalid\s+value|unsupported\s+value)\b"),
        "configuration_rejected",
    ),
)


class DispatchError(ValueError):
    """A sanitized, typed dispatch rejection."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(code if not message else f"{code}: {message}")


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None


def _unknown(value: Any = "UNKNOWN") -> str:
    return value if isinstance(value, str) and value else "UNKNOWN"


def _request_value(request: Mapping[str, Any], name: str, default: Any = "UNKNOWN") -> Any:
    value = request.get(name, default)
    return value


def _operation_binding(request: Mapping[str, Any]) -> tuple[str, str]:
    operation_class = request.get("operation_class")
    if not isinstance(operation_class, str) or not operation_class:
        raise DispatchError("operation_class_required")
    if operation_class not in OPERATION_CLASS_ALLOWLIST:
        raise DispatchError("operation_class_invalid")
    if operation_class in PROTECTED_OPERATION_CLASSES:
        return operation_class, "REQUIRED"
    return operation_class, "INAPPLICABLE"


def _work_class_and_effort(request: Mapping[str, Any]) -> tuple[str, str]:
    work_class = request.get("work_class", request.get("task_class"))
    if not isinstance(work_class, str) or work_class not in LUNA_EFFORT_BY_WORK_CLASS:
        raise DispatchError("luna_work_class_invalid")
    effort = LUNA_EFFORT_BY_WORK_CLASS[work_class]
    requested_effort = request.get("requested_effort", effort)
    if requested_effort != effort:
        raise DispatchError("luna_effort_work_class_mismatch")
    return work_class, effort


def _sanitize_terminal_output(output: str) -> str:
    sanitized = _ANSI_ESCAPE.sub("", output)
    sanitized = _SECRET_VALUE.sub(r"\1\2<redacted>", sanitized)
    sanitized = "".join(
        character
        if character in "\n\r\t" or ord(character) >= 0x20
        else "?"
        for character in sanitized
    )
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= MAX_TERMINAL_OUTPUT_BYTES:
        return sanitized
    return encoded[:MAX_TERMINAL_OUTPUT_BYTES].decode("utf-8", "ignore")


def _child_failure_category(stderr: Any) -> str:
    """Return one bounded category for a nonzero child exit.

    The child diagnostic is intentionally used only for fixed-category
    matching.  It is never returned in the dispatch receipt.
    """

    if isinstance(stderr, bytes):
        diagnostic = stderr.decode("utf-8", "replace")
    elif isinstance(stderr, str):
        diagnostic = stderr
    else:
        diagnostic = ""
    diagnostic = _sanitize_terminal_output(diagnostic).casefold()
    if not diagnostic.strip():
        return "no_error_detail"
    for pattern, category in _CHILD_FAILURE_PATTERNS:
        if pattern.search(diagnostic):
            return category
    return "unknown_child_failure"


def _path_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _writer_entries(request: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    supplied = request.get("writers")
    if supplied is None:
        worktree = request.get("worktree", request.get("cwd"))
        supplied = [
            {
                "owner_id": request.get("owner_id", request.get("job_id")),
                "worktree": worktree,
                "write_set": request.get("write_set") or [worktree],
                "shared_runtime": request.get("shared_runtime", ""),
            }
        ]
    if not isinstance(supplied, list) or not supplied:
        raise DispatchError("writer_admission_invalid")
    if len(supplied) > MAX_ORDINARY_WRITERS:
        raise DispatchError("writer_cohort_too_large")
    if any(not isinstance(item, Mapping) for item in supplied):
        raise DispatchError("writer_admission_invalid")
    return supplied


def admit_writer_cohort(request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a bounded ordinary writer cohort without process signaling."""

    supplied = request.get("writers")
    entries = _writer_entries(request)
    normalized: list[dict[str, Any]] = []
    owner_ids: set[str] = set()
    for entry in entries:
        owner_id = entry.get("owner_id")
        worktree = entry.get("worktree")
        write_set = entry.get("write_set")
        shared_runtime = entry.get("shared_runtime", "")
        if (
            not _safe_id(owner_id)
            or not isinstance(worktree, str)
            or not worktree
            or not isinstance(write_set, list)
            or not write_set
            or not all(isinstance(path, str) and path for path in write_set)
            or not isinstance(shared_runtime, str)
        ):
            raise DispatchError("writer_admission_invalid")
        if owner_id in owner_ids:
            raise DispatchError("operation_scope_conflict")
        owner_ids.add(owner_id)
        normalized.append(
            {
                "owner_id": owner_id,
                "worktree": Path(worktree).resolve(strict=False),
                "write_set": tuple(
                    Path(path).resolve(strict=False) for path in write_set
                ),
                "shared_runtime": shared_runtime,
            }
        )

    for index, left in enumerate(normalized):
        for right in normalized[index + 1 :]:
            if left["worktree"] == right["worktree"]:
                raise DispatchError("operation_scope_conflict")
            if any(
                _path_overlap(left_path, right_path)
                for left_path in left["write_set"]
                for right_path in right["write_set"]
            ):
                raise DispatchError("operation_scope_conflict")
            if (
                left["shared_runtime"]
                and left["shared_runtime"] == right["shared_runtime"]
            ):
                raise DispatchError("operation_scope_conflict")

    if supplied is not None:
        current_owner = request.get("owner_id", request.get("job_id"))
        current_worktree = request.get("worktree", request.get("cwd"))
        current_write_set = request.get("write_set")
        current_runtime = request.get("shared_runtime", "")
        if (
            not _safe_id(current_owner)
            or not isinstance(current_worktree, str)
            or not isinstance(current_write_set, list)
            or not current_write_set
            or not all(isinstance(path, str) and path for path in current_write_set)
            or not isinstance(current_runtime, str)
        ):
            raise DispatchError("writer_admission_invalid")
        current_worktree_path = Path(current_worktree).resolve(strict=False)
        current_write_paths = frozenset(
            Path(path).resolve(strict=False) for path in current_write_set
        )
        if not any(
            entry["owner_id"] == current_owner
            and entry["worktree"] == current_worktree_path
            and frozenset(entry["write_set"]) == current_write_paths
            and entry["shared_runtime"] == current_runtime
            for entry in normalized
        ):
            raise DispatchError("writer_admission_invalid")

    return {
        "status": "admitted",
        "count": len(normalized),
        "max": MAX_ORDINARY_WRITERS,
        "ordinary": True,
        "lease_source": WRITER_LEASE_SOURCE,
        "lock_family": "fcntl_file_locks",
        "owners": [entry["owner_id"] for entry in normalized],
    }


def _writer_scope_keys(
    request: Mapping[str, Any], *, owner_id: str | None = None
) -> list[str]:
    entries = _writer_entries(request)
    keys: set[str] = set()
    for entry in entries:
        if owner_id is not None and entry.get("owner_id") != owner_id:
            continue
        worktree = Path(str(entry["worktree"])).resolve(strict=False)
        keys.add(f"worktree:{worktree}")
        for path in entry["write_set"]:
            keys.add(f"write_set:{Path(str(path)).resolve(strict=False)}")
        shared_runtime = entry.get("shared_runtime", "")
        if shared_runtime:
            keys.add(f"shared_runtime:{shared_runtime}")
    return sorted(keys)


@contextmanager
def _writer_lease(request: Mapping[str, Any]) -> Iterator[None]:
    """Hold the complete owner-private lease and lock family for one launch."""

    root = Path(tempfile.gettempdir()) / WRITER_LOCK_ROOT_NAME
    lock_root = root / "locks"
    lease_root = root / "leases"
    for path in (root, lock_root, lease_root):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    owners = _writer_entries(request)
    owner_id = str(request.get("owner_id", request.get("job_id", owners[0]["owner_id"])))
    lease_name = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
    lease_path = lease_root / f"{lease_name}.json"
    token = uuid.uuid4().hex
    descriptors: list[tuple[int, Path]] = []
    try:
        for scope in _writer_scope_keys(request, owner_id=owner_id):
            lock_name = hashlib.sha256(scope.encode("utf-8")).hexdigest() + ".lock"
            lock_path = lock_root / lock_name
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                os.close(descriptor)
                if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {
                    11,
                    35,
                }:
                    raise DispatchError("operation_scope_conflict") from exc
                raise DispatchError("writer_lock_unavailable") from exc
            descriptors.append((descriptor, lock_path))

        lease_payload = {
            "source": WRITER_LEASE_SOURCE,
            "token": token,
            "owner_id": owner_id,
            "lock_family": "fcntl_file_locks",
            "scopes": len(descriptors),
        }
        temporary = lease_path.with_name(f".{lease_path.name}.{token}.tmp")
        temporary.write_text(
            json.dumps(lease_payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, lease_path)
        yield
    finally:
        try:
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
            if lease.get("token") == token:
                lease_path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
        for descriptor, _lock_path in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _resume_binding(request: Mapping[str, Any]) -> tuple[str, str]:
    resume = request.get("resume")
    if not isinstance(resume, dict):
        raise DispatchError("exact_session_resume_required")
    mode = resume.get("mode")
    session_id = resume.get("session_id", "")
    if resume.get("latest_allowed") is True or mode == "latest":
        raise DispatchError("latest_session_resume_forbidden")
    if mode == "new_session":
        if session_id:
            raise DispatchError("new_session_id_must_be_empty")
        return mode, ""
    if mode == "exact_session_id" and _safe_id(session_id):
        return mode, session_id
    raise DispatchError("exact_session_resume_required")


def _base_receipt(
    request: Mapping[str, Any],
    *,
    status: str,
    code: str,
    launch: bool,
    terminal_class: str,
    result_consumed: bool = False,
    actual: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
    continuation_allowed: bool = True,
    operation_class: str | None = None,
    authority_disposition: str | None = None,
    writer_admission: Mapping[str, Any] | None = None,
    executable_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider = _request_value(request, "provider")
    surface = _request_value(request, "surface")
    resume = request.get("resume") if isinstance(request.get("resume"), dict) else {}
    mode = resume.get("mode", "UNKNOWN")
    session_id = resume.get("session_id", "")
    advertised = request.get("advertised_models")
    if not isinstance(advertised, list):
        advertised = []
    advertised_efforts = request.get("advertised_efforts")
    if not isinstance(advertised_efforts, list):
        advertised_efforts = []
    worktree = _unknown(_request_value(request, "worktree"))
    cwd = _unknown(_request_value(request, "cwd"))
    bound_operation_class = operation_class
    if bound_operation_class is None:
        candidate = request.get("operation_class")
        bound_operation_class = (
            candidate if candidate in OPERATION_CLASS_ALLOWLIST else "UNKNOWN"
        )
    bound_authority = authority_disposition
    if bound_authority is None:
        if bound_operation_class in PROTECTED_OPERATION_CLASSES:
            bound_authority = "REQUIRED"
        elif bound_operation_class in ROUTINE_OPERATION_CLASSES:
            bound_authority = "INAPPLICABLE"
        else:
            bound_authority = "UNKNOWN"
    work_class = request.get("work_class", request.get("task_class"))
    derived_effort = (
        LUNA_EFFORT_BY_WORK_CLASS.get(work_class)
        if isinstance(work_class, str)
        else None
    )
    requested_effort = derived_effort or _unknown(
        _request_value(request, "requested_effort")
    )
    requested = {
        "model": _unknown(_request_value(request, "requested_model")),
        "effort": requested_effort,
        "service_tier": (
            SERVICE_TIER_PREFERENCE
            if requested_effort in {"high", LUNA_EFFORT}
            else "UNKNOWN"
        ),
    }
    actual_identity = {
        "record_type": IDENTITY_RECEIPT_TYPE,
        "requested_model": requested["model"],
        "actual_model": "UNKNOWN",
        "requested_reasoning_effort": requested["effort"],
        "actual_reasoning_effort": "UNKNOWN",
        "actual_service_tier": "UNKNOWN",
        "cwd": cwd,
        "worktree": worktree,
        "approval_policy": APPROVAL_POLICY,
        "sandbox": SANDBOX,
        "job_id": _unknown(_request_value(request, "job_id")),
        "thread_id": _unknown(_request_value(request, "thread_id")),
        "session_id": _unknown(session_id or ("new_session" if mode == "new_session" else "")),
        "requested_session_id": session_id if mode == "exact_session_id" else "",
        "actual_session_id": "UNKNOWN",
        "session_identity_match": False,
        "resume_mode": mode,
        "binding_source": "UNKNOWN",
        "verified": False,
    }
    if actual:
        actual_identity.update(actual)
    provenance = dict(executable_provenance or {
        "source": "UNKNOWN",
        "path": "UNKNOWN",
        "sha256": "UNKNOWN",
        "verified": False,
    })
    return {
        "record_type": RECEIPT_TYPE,
        "provider": provider,
        "surface": surface,
        "status": status,
        "code": code,
        "launch": launch,
        "operation_class": bound_operation_class,
        "authority_disposition": bound_authority,
        "fallback_used": False,
        "continuation_allowed": continuation_allowed,
        "requested": requested,
        "advertised": {
            "models": list(advertised),
            "efforts": list(advertised_efforts),
            "surface": surface,
        },
        "runtime_identity_receipt": actual_identity,
        "executable_provenance": provenance,
        "writer_admission": dict(writer_admission or {
            "status": "UNKNOWN",
            "count": 0,
            "max": MAX_ORDINARY_WRITERS,
            "ordinary": False,
            "lease_source": "UNKNOWN",
            "lock_family": "UNKNOWN",
            "owners": [],
        }),
        "service_tier_runtime_verified": (
            actual_identity.get("actual_service_tier") not in {None, "UNKNOWN"}
            and actual_identity.get("binding_source")
            in {"native_codex_receipt", APP_SERVER_IDENTITY_SOURCE}
        ),
        "terminal_class": terminal_class,
        "result_consumed": result_consumed,
        "result": dict(result or {"state": "UNKNOWN"}),
        "app_visibility": {
            "surface": "codex_app_thread",
            "enabled": bool(request.get("app_visible", False)),
            "required_for_scheduling": False,
            "manual_takeover_only": True,
        },
        "stop_auto_review_enabled": False,
        "stop_auto_review_runtime_bound": False,
        "stop_auto_review_binding": "not_bound_without_executable_runtime_contract",
        "nonclaims": [
            "not_installed_or_qualified_provider",
            "not_codex_app_scheduler",
            "not_maestro_execution",
            "not_user_approval_modal",
        ],
    }


def _typed_rejection(request: Mapping[str, Any], error: DispatchError) -> dict[str, Any]:
    return _base_receipt(
        request,
        status="blocked",
        code=error.code,
        launch=False,
        terminal_class="blocked",
        continuation_allowed=error.code not in {
            "maestro_authority_required",
            "provider_surface_mismatch",
            "operation_scope_conflict",
            "writer_cohort_too_large",
        },
    )


def _validate_common(
    request: Mapping[str, Any],
) -> tuple[str, str, str, str, str, str, dict[str, Any] | None]:
    provider = request.get("provider")
    surface = request.get("surface")
    if provider not in _EXPECTED_SURFACE:
        raise DispatchError("provider_unavailable")
    if surface != _EXPECTED_SURFACE[provider]:
        raise DispatchError("provider_surface_mismatch")
    if request.get("stop_auto_review", False) is not False:
        raise DispatchError("stop_auto_review_must_be_false")
    if request.get("approval_policy", APPROVAL_POLICY) != APPROVAL_POLICY:
        raise DispatchError("approval_policy_must_be_never")
    if request.get("sandbox", SANDBOX) != SANDBOX:
        raise DispatchError("sandbox_must_be_danger_full_access")
    operation_class, authority_disposition = _operation_binding(request)
    job_id = request.get("job_id")
    thread_id = request.get("thread_id")
    if not _safe_id(job_id) or not _safe_id(thread_id):
        raise DispatchError("runtime_identity_binding_invalid")
    mode, session_id = _resume_binding(request)
    if authority_disposition == "REQUIRED":
        return (
            provider,
            surface,
            mode,
            session_id,
            operation_class,
            authority_disposition,
            None,
        )
    if provider == "codex_luna":
        _work_class_and_effort(request)
    writer_admission = admit_writer_cohort(request)
    return (
        provider,
        surface,
        mode,
        session_id,
        operation_class,
        authority_disposition,
        writer_admission,
    )


def _resolved_worktree(request: Mapping[str, Any]) -> Path:
    cwd = request.get("cwd")
    worktree = request.get("worktree")
    if not isinstance(cwd, str) or not isinstance(worktree, str) or cwd != worktree:
        raise DispatchError("cwd_worktree_binding_invalid")
    try:
        resolved = Path(worktree).resolve(strict=True)
    except OSError as exc:
        raise DispatchError("worktree_unavailable") from exc
    if not resolved.is_dir() or str(resolved) != cwd:
        raise DispatchError("cwd_worktree_binding_invalid")
    return resolved


def _result_observation(output: str) -> dict[str, Any]:
    sanitized = _sanitize_terminal_output(output)
    encoded = sanitized.encode("utf-8")
    source_size = len(_ANSI_ESCAPE.sub("", output).encode("utf-8"))
    return {
        "state": "known_nonempty",
        "byte_count": len(encoded),
        "terminal_output": sanitized,
        "truncated": source_size > len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _consumer_acknowledged(
    request: Mapping[str, Any], result: Mapping[str, Any]
) -> bool:
    acknowledgement = request.get("consumer_acknowledgment")
    if not isinstance(acknowledgement, Mapping):
        acknowledgement = request.get("result_acknowledgment")
    if not isinstance(acknowledgement, Mapping):
        return False
    result_digest = acknowledgement.get(
        "sha256", acknowledgement.get("result_sha256")
    )
    return (
        acknowledgement.get("acknowledged") is True
        and result_digest == result.get("sha256")
        and acknowledgement.get("job_id") == request.get("job_id")
        and acknowledgement.get("thread_id") == request.get("thread_id")
    )


def _resolve_admitted_codex(
    request: Mapping[str, Any],
    *,
    test_runner: Callable[[Sequence[str], Path], Any] | None,
) -> tuple[Path, dict[str, Any]]:
    provenance = request.get("admitted_codex_provenance")
    if not isinstance(provenance, Mapping):
        raise DispatchError("codex_executable_provenance_required")
    if provenance.get("source") != CODEX_PROVENANCE_SOURCE:
        raise DispatchError("codex_executable_provenance_invalid")
    path_value = provenance.get("path")
    expected_sha = provenance.get("sha256")
    expected_size = provenance.get("size")
    expected_mode = provenance.get("mode")
    if (
        not isinstance(path_value, str)
        or not path_value
        or not isinstance(expected_sha, str)
        or _SHA256.fullmatch(expected_sha) is None
        or type(expected_size) is not int
        or type(expected_mode) is not int
    ):
        raise DispatchError("codex_executable_provenance_invalid")
    admitted_path = Path(path_value)
    try:
        admitted_resolved = admitted_path.resolve(strict=True)
        if (
            admitted_path.is_symlink()
            or not admitted_resolved.is_file()
            or not os.access(admitted_resolved, os.X_OK)
        ):
            raise DispatchError("codex_executable_provenance_invalid")
        observed_size = admitted_resolved.stat().st_size
        observed_mode = stat.S_IMODE(admitted_resolved.stat().st_mode)
        observed_sha = hashlib.sha256(admitted_resolved.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise DispatchError("codex_executable_provenance_invalid") from exc
    if (
        observed_size != expected_size
        or observed_mode != expected_mode
        or observed_sha != expected_sha
    ):
        raise DispatchError("codex_executable_provenance_mismatch")

    if test_runner is not None:
        # The runner injection is a hermetic test seam; real invocations must
        # resolve the admitted binary from the local Codex PATH entry.
        resolved_from_path = str(admitted_resolved)
    else:
        resolved_from_path = shutil.which("codex")
    if not isinstance(resolved_from_path, str) or not resolved_from_path:
        raise DispatchError("codex_executable_unavailable")
    try:
        path_candidate = Path(resolved_from_path)
        path_resolved = path_candidate.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise DispatchError("codex_executable_unavailable") from exc
    if path_resolved != admitted_resolved:
        raise DispatchError("codex_executable_provenance_mismatch")
    return admitted_resolved, {
        "source": CODEX_PROVENANCE_SOURCE,
        "path": str(admitted_resolved),
        "sha256": observed_sha,
        "size": observed_size,
        "mode": observed_mode,
        "verified": True,
    }


def _valid_native_session_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.casefold() not in {"unknown", "new_session"}
        and _safe_id(value)
    )


def _exec_jsonl_session_id(stdout: Any) -> str:
    if isinstance(stdout, bytes):
        try:
            stdout = stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DispatchError("child_identity_unavailable") from exc
    if not isinstance(stdout, str) or not stdout.strip():
        raise DispatchError("child_identity_unavailable")

    session_id: str | None = None
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DispatchError("child_identity_unavailable") from exc
        if not isinstance(event, Mapping):
            raise DispatchError("child_identity_unavailable")
        if event.get("type") != "thread.started":
            continue
        candidate = event.get("thread_id")
        if not _valid_native_session_id(candidate):
            raise DispatchError("child_identity_unavailable")
        if session_id is not None and session_id != candidate:
            raise DispatchError("session_identity_mismatch")
        session_id = candidate

    if session_id is None:
        raise DispatchError("child_identity_unavailable")
    return session_id


def _app_server_wire(session_id: str, *, experimental_api: bool = True) -> str:
    messages = [
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "hermes",
                    "title": "Hermes Agent",
                    "version": "0.1",
                },
                "capabilities": (
                    {"experimentalApi": True} if experimental_api else {}
                ),
            },
        },
        {"method": "notifications/initialized", "params": {}},
        {
            "id": 2,
            "method": "thread/resume",
            "params": {"threadId": session_id, "excludeTurns": True},
        },
    ]
    return "".join(
        json.dumps(message, separators=(",", ":")) + "\n" for message in messages
    )


def _app_server_thread_resume(
    executable: Path, worktree: Path, session_id: str
) -> Mapping[str, Any]:
    """Read one exact thread through the official app-server JSON-RPC path."""

    process: Any | None = None
    stdin: Any | None = None
    stdin_closed = False
    wait_completed = False
    wait_timed_out = False

    def close_stdin_and_wait() -> None:
        nonlocal stdin_closed, wait_completed, wait_timed_out
        if process is None:
            return
        if not stdin_closed and stdin is not None:
            try:
                stdin.close()
            except (OSError, ValueError):
                pass
            stdin_closed = True
        if wait_completed or wait_timed_out:
            return
        try:
            process.wait(timeout=MAX_APP_SERVER_WAIT_TIMEOUT_SECONDS)
            wait_completed = True
        except subprocess.TimeoutExpired as exc:
            wait_timed_out = True
            raise DispatchError("child_cleanup_timeout") from exc
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    def read_response(expected_id: int, readback_bytes: int) -> tuple[Mapping[str, Any], int]:
        stdout = getattr(process, "stdout", None)
        if stdout is None:
            raise DispatchError("child_identity_unavailable")
        while True:
            try:
                readable, _writeable, _exceptional = select.select(
                    [stdout], [], [], MAX_APP_SERVER_READ_TIMEOUT_SECONDS
                )
            except (OSError, ValueError, select.error) as exc:
                raise DispatchError("child_identity_unavailable") from exc
            if not readable:
                raise DispatchError("child_identity_unavailable")
            try:
                raw_line = stdout.readline()
            except (OSError, ValueError) as exc:
                raise DispatchError("child_identity_unavailable") from exc
            if raw_line in ("", b""):
                raise DispatchError("child_identity_unavailable")
            if isinstance(raw_line, bytes):
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise DispatchError("child_identity_unavailable") from exc
                line_bytes = len(raw_line)
            elif isinstance(raw_line, str):
                line = raw_line
                line_bytes = len(raw_line.encode("utf-8"))
            else:
                raise DispatchError("child_identity_unavailable")
            readback_bytes += line_bytes
            if readback_bytes > MAX_APP_SERVER_READBACK_BYTES:
                raise DispatchError("child_identity_unavailable")
            line = line.strip()
            if not line:
                raise DispatchError("child_identity_unavailable")
            try:
                message = json.loads(line)
            except (TypeError, json.JSONDecodeError) as exc:
                raise DispatchError("child_identity_unavailable") from exc
            if not isinstance(message, Mapping):
                raise DispatchError("child_identity_unavailable")
            if "id" not in message and "method" in message:
                continue
            message_id = message.get("id")
            if type(message_id) is not int or message_id != expected_id:
                raise DispatchError("child_identity_unavailable")
            if "error" in message:
                raise DispatchError("child_identity_unavailable")
            result = message.get("result")
            if not isinstance(result, Mapping):
                raise DispatchError("child_identity_unavailable")
            return result, readback_bytes

    try:
        process = subprocess.Popen(
            [str(executable), "app-server"],
            cwd=worktree,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        stdin = getattr(process, "stdin", None)
        if stdin is None:
            raise DispatchError("child_identity_unavailable")
        wire_lines = _app_server_wire(session_id).splitlines(keepends=True)
        if len(wire_lines) != 3:
            raise DispatchError("child_identity_unavailable")
        stdin.write(wire_lines[0])
        stdin.flush()
        _initialize, readback_bytes = read_response(1, 0)
        stdin.write("".join(wire_lines[1:]))
        stdin.flush()
        response, _readback_bytes = read_response(2, readback_bytes)
        stdin.close()
        stdin_closed = True
        process.wait(timeout=MAX_APP_SERVER_WAIT_TIMEOUT_SECONDS)
        wait_completed = True
        if getattr(process, "returncode", 1) != 0:
            raise DispatchError("child_identity_unavailable")
        return response
    except subprocess.TimeoutExpired as exc:
        wait_timed_out = True
        close_stdin_and_wait()
        raise DispatchError("child_cleanup_timeout") from exc
    except DispatchError:
        close_stdin_and_wait()
        raise
    except (BrokenPipeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        close_stdin_and_wait()
        raise DispatchError("child_identity_unavailable") from exc


def _validated_app_server_identity(
    response: Mapping[str, Any], *, worktree: Path, session_id: str, effort: str
) -> dict[str, str | bool | None]:
    expected_service_tier = "fast" if effort == LUNA_EFFORT else "default"
    if (
        response.get("model") != LUNA_MODEL
        or response.get("reasoningEffort") != effort
        or "serviceTier" not in response
        or response.get("serviceTier") != expected_service_tier
        or response.get("approvalPolicy") != APPROVAL_POLICY
        or response.get("cwd") != str(worktree)
    ):
        raise DispatchError("child_identity_unavailable")

    sandbox = response.get("sandbox")
    if not isinstance(sandbox, Mapping) or sandbox.get("type") != "dangerFullAccess":
        raise DispatchError("child_identity_unavailable")

    thread = response.get("thread")
    if not isinstance(thread, Mapping):
        raise DispatchError("child_identity_unavailable")
    actual_session_id = thread.get("id")
    if not _valid_native_session_id(actual_session_id):
        raise DispatchError("child_identity_unavailable")
    if actual_session_id != session_id:
        raise DispatchError("session_identity_mismatch")

    return {
        "actual_model": LUNA_MODEL,
        "actual_reasoning_effort": effort,
        "actual_service_tier": response["serviceTier"],
        "sandbox": SANDBOX,
        "approval_policy": APPROVAL_POLICY,
        "session_id": actual_session_id,
        "actual_session_id": actual_session_id,
        "session_identity_match": True,
        "binding_source": APP_SERVER_IDENTITY_SOURCE,
    }


def _native_identity(completed: Any) -> dict[str, str] | None:
    receipt = getattr(completed, "native_receipt", None)
    if not isinstance(receipt, Mapping):
        return None
    if (
        receipt.get("record_type") != IDENTITY_RECEIPT_TYPE
        or receipt.get("source") != "codex_native"
    ):
        return None
    fields = {
        "actual_model": receipt.get("model"),
        "actual_reasoning_effort": receipt.get("reasoning_effort"),
        "actual_service_tier": receipt.get("service_tier", "UNKNOWN"),
        "sandbox": receipt.get("sandbox"),
        "approval_policy": receipt.get("approval_policy"),
        "session_id": receipt.get("session_id"),
    }
    if any(not isinstance(value, str) or not value for value in fields.values()):
        return None
    return fields


def _invoke_codex(
    request: Mapping[str, Any],
    *,
    worktree: Path,
    resume_mode: str,
    session_id: str,
    runner: Callable[[Sequence[str], Path], Any] | None,
    writer_admission: Mapping[str, Any],
) -> dict[str, Any]:
    model = request.get("requested_model")
    _work_class, effort = _work_class_and_effort(request)
    advertised = request.get("advertised_models")
    if model != LUNA_MODEL or effort not in {"high", LUNA_EFFORT}:
        return _typed_rejection(
            request,
            DispatchError("luna_request_binding_invalid"),
        )
    if (
        not isinstance(advertised, list)
        or any(not isinstance(item, str) for item in advertised)
        or LUNA_MODEL not in advertised
    ):
        receipt = _base_receipt(
            request,
            status="unavailable",
            code="codex_luna_unavailable",
            launch=False,
            terminal_class="blocked",
        )
        receipt["result"] = {"state": "typed_failure", "reason": "advertised_model_missing"}
        return receipt
    advertised_efforts = request.get("advertised_efforts")
    if (
        not isinstance(advertised_efforts, list)
        or any(not isinstance(item, str) for item in advertised_efforts)
        or effort not in advertised_efforts
    ):
        receipt = _base_receipt(
            request,
            status="unavailable",
            code="codex_luna_effort_unavailable",
            launch=False,
            terminal_class="blocked",
        )
        receipt["result"] = {
            "state": "typed_failure",
            "reason": "advertised_effort_missing",
        }
        return receipt
    try:
        executable, executable_provenance = _resolve_admitted_codex(
            request,
            test_runner=runner,
        )
    except DispatchError as error:
        return _typed_rejection(request, error)
    worktree_text = str(worktree)

    with tempfile.TemporaryDirectory(prefix="hermes-codex-luna-") as temporary:
        result_path = Path(temporary) / "result.txt"
        command: list[str] = [
            str(executable),
            "exec",
            "-m",
            LUNA_MODEL,
            "-C",
            worktree_text,
            "-s",
            SANDBOX,
            "-c",
            f"model_reasoning_effort={effort}",
            "-c",
            "approval_policy=never",
            "--skip-git-repo-check",
            "--json",
            "-o",
            str(result_path),
        ]
        if effort in {"high", LUNA_EFFORT}:
            command.extend(["-c", "service_tier=fast"])
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return _typed_rejection(request, DispatchError("prompt_required"))
        if resume_mode == "exact_session_id":
            command.extend(["resume", session_id, prompt])
        else:
            command.append(prompt)
        try:
            completed = (
                runner(command, worktree)
                if runner is not None
                else subprocess.run(
                    command,
                    cwd=worktree,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
            )
        except OSError:
            return _typed_rejection(request, DispatchError("codex_exec_unavailable"))
        stdout = getattr(completed, "stdout", "") or ""
        native = _native_identity(completed)
        actual = {
            "actual_model": "UNKNOWN",
            "actual_reasoning_effort": "UNKNOWN",
            "actual_service_tier": "UNKNOWN",
            "sandbox": "UNKNOWN",
            "approval_policy": "UNKNOWN",
            "session_id": "UNKNOWN",
            "actual_session_id": "UNKNOWN",
            "requested_session_id": session_id
            if resume_mode == "exact_session_id"
            else "",
            "session_identity_match": False,
            "binding_source": "UNKNOWN",
        }
        if native is not None:
            actual.update(native)
            actual["actual_session_id"] = native["session_id"]
            actual["session_id"] = native["session_id"]
            actual["binding_source"] = "native_codex_receipt"
            actual["session_identity_match"] = (
                native["session_id"] != "UNKNOWN"
                and (
                    (
                        resume_mode == "new_session"
                        and native["session_id"] != "new_session"
                    )
                    or (
                        resume_mode == "exact_session_id"
                        and native["session_id"] == session_id
                    )
                )
            )
        identity_ok = False
        if native is not None:
            service_tier_ok = actual["actual_service_tier"] in {
                "UNKNOWN",
                "fast" if effort == LUNA_EFFORT else "default",
            }
            identity_ok = (
                actual["actual_model"] == LUNA_MODEL
                and actual["actual_reasoning_effort"] == effort
                and actual["sandbox"] == SANDBOX
                and actual["approval_policy"] == APPROVAL_POLICY
                and actual["session_identity_match"] is True
                and service_tier_ok
            )
        if getattr(completed, "returncode", 1) != 0:
            actual["verified"] = False
            receipt = _base_receipt(
                request,
                status="blocked",
                code="codex_exec_failed",
                launch=True,
                terminal_class="crash",
                actual=actual,
                writer_admission=writer_admission,
                executable_provenance=executable_provenance,
            )
            receipt["result"] = {
                "state": "typed_failure",
                "reason": "child_exit_nonzero",
                "failure_category": _child_failure_category(
                    getattr(completed, "stderr", "")
                ),
            }
            return receipt
        if native is None:
            try:
                event_session_id = _exec_jsonl_session_id(stdout)
                actual.update(
                    {
                        "actual_session_id": event_session_id,
                        "session_id": event_session_id,
                        "binding_source": "codex_exec_jsonl",
                    }
                )
                if (
                    resume_mode == "exact_session_id"
                    and event_session_id != session_id
                ):
                    raise DispatchError("session_identity_mismatch")
                readback = _app_server_thread_resume(
                    executable, worktree, event_session_id
                )
                thread = readback.get("thread")
                if isinstance(thread, Mapping):
                    readback_session_id = thread.get("id")
                    if _valid_native_session_id(readback_session_id):
                        actual["actual_session_id"] = readback_session_id
                        actual["session_id"] = readback_session_id
                actual.update(
                    _validated_app_server_identity(
                        readback,
                        worktree=worktree,
                        session_id=event_session_id,
                        effort=effort,
                    )
                )
                identity_ok = True
            except DispatchError as error:
                actual["verified"] = False
                receipt = _base_receipt(
                    request,
                    status="blocked",
                    code=error.code,
                    launch=True,
                    terminal_class="blocked",
                    actual=actual,
                    writer_admission=writer_admission,
                    executable_provenance=executable_provenance,
                )
                receipt["result"] = {
                    "state": "typed_failure",
                    "reason": error.code,
                }
                return receipt
        actual["verified"] = identity_ok
        if native is not None and not actual["session_identity_match"]:
            receipt = _base_receipt(
                request,
                status="blocked",
                code="session_identity_mismatch",
                launch=True,
                terminal_class="blocked",
                actual=actual,
                writer_admission=writer_admission,
                executable_provenance=executable_provenance,
            )
            receipt["result"] = {
                "state": "typed_failure",
                "reason": "requested_and_actual_session_identity_differ",
            }
            return receipt
        if not identity_ok:
            receipt = _base_receipt(
                request,
                status="blocked",
                code="child_identity_unavailable",
                launch=True,
                terminal_class="blocked",
                actual=actual,
                writer_admission=writer_admission,
                executable_provenance=executable_provenance,
            )
            receipt["result"] = {
                "state": "typed_failure",
                "reason": "native_codex_identity_receipt_required",
            }
            return receipt
        try:
            output = result_path.read_text(encoding="utf-8")
        except OSError:
            output = ""
        if not output.strip() or output.strip().casefold() == "unknown":
            receipt = _base_receipt(
                request,
                status="blocked",
                code="empty_or_unknown_output",
                launch=True,
                terminal_class="blocked",
                actual=actual,
                writer_admission=writer_admission,
                executable_provenance=executable_provenance,
            )
            receipt["result"] = {
                "state": "typed_failure",
                "reason": "empty_or_unknown_output",
            }
            return receipt
        observed_result = _result_observation(output)
        result_consumed = _consumer_acknowledged(request, observed_result)
        observed_result["acknowledged"] = result_consumed
        observed_result["consumption"] = (
            "acknowledged"
            if result_consumed
            else "awaiting_consumer_acknowledgment"
        )
        return _base_receipt(
            request,
            status="completed",
            code="codex_luna_completed",
            launch=True,
            terminal_class="completed",
            result_consumed=result_consumed,
            actual=actual,
            result=observed_result,
            writer_admission=writer_admission,
            executable_provenance=executable_provenance,
        )


def _maestro_handoff(
    request: Mapping[str, Any],
    *,
    operation_class: str,
) -> dict[str, Any]:
    receipt = _base_receipt(
        request,
        status="handoff",
        code="maestro_authority_handoff",
        launch=False,
        terminal_class="handoff",
        continuation_allowed=False,
        operation_class=operation_class,
        authority_disposition="REQUIRED",
    )
    receipt["handoff"] = {
        "execution": "maestro_authority",
        "operation_class": operation_class,
        "authority_disposition": "REQUIRED",
        "launch": False,
        "typed": True,
    }
    return receipt


def dispatch(
    request: Mapping[str, Any],
    *,
    runner: Callable[[Sequence[str], Path], Any] | None = None,
) -> dict[str, Any]:
    """Return a typed receipt for one exact provider/surface selection."""

    if not isinstance(request, Mapping):
        return _typed_rejection({}, DispatchError("request_invalid"))
    try:
        (
            provider,
            surface,
            resume_mode,
            session_id,
            operation_class,
            authority_disposition,
            writer_admission,
        ) = _validate_common(request)
    except DispatchError as error:
        return _typed_rejection(request, error)
    if authority_disposition == "REQUIRED":
        return _maestro_handoff(request, operation_class=operation_class)
    if provider in _HANDOFF_CODE:
        receipt = _base_receipt(
            request,
            status="handoff",
            code=_HANDOFF_CODE[provider],
            launch=False,
            terminal_class="handoff",
            operation_class=operation_class,
            authority_disposition=authority_disposition,
            writer_admission=writer_admission,
        )
        receipt["handoff"] = {
            "provider": provider,
            "surface": surface,
            "execution": "delegate_task" if provider == "hermes_native" else "codex_plugin_cc_bridge",
            "installed": False,
            "qualified": False,
        }
        return receipt
    try:
        worktree = _resolved_worktree(request)
    except DispatchError as error:
        return _typed_rejection(request, error)
    try:
        with _writer_lease(request):
            return _invoke_codex(
                request,
                worktree=worktree,
                resume_mode=resume_mode,
                session_id=session_id,
                runner=runner,
                writer_admission=writer_admission or {},
            )
    except DispatchError as error:
        return _typed_rejection(request, error)


def _exit_code(receipt: Mapping[str, Any]) -> int:
    return 0 if receipt.get("status") in {"completed", "handoff"} else 69


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="JSON request file")
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        receipt = _typed_rejection({}, DispatchError("request_unreadable"))
    else:
        receipt = dispatch(request)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return _exit_code(receipt)


if __name__ == "__main__":
    raise SystemExit(main())
