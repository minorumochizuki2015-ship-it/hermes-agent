#!/usr/bin/env python3
"""Pure contract decisions for the Hermes mobile-harness skill.

This module deliberately contains no device, process, network, credential, or
clock I/O.  Callers provide observations and monotonic values; the functions
return sanitized decisions and safe checkpoint data.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any


SOURCE_IDENTITIES = {
    "droidrun_mobile_harness": "ace2e483a954431f84c9004991f4704d4609d25f",
    "droidrun_ios_portal": "621e3e9bf680d3ff5e1294eef0ad5e4536dce0b3",
}
LOCAL_PACKAGES = {
    "mobilerun-core": "1.5.0",
    "mobilerun-core-local": "0.6.0",
    "mobilerun-sdk": "5.1.0",
}

SERVICE_DURATIONS = (15, 30, 60)
DEFAULT_SERVICE_DURATION = 30
OPERATION_CLASSES = {
    "physical_unlocked_required",
    "mirroring_locked_required",
    "either",
}
SAFE_OPERATIONS = {
    "observe",
    "install",
    "launch",
    "current_operation",
    "continue",
}
FORBIDDEN_OPERATIONS = {
    "credential",
    "paid_provider",
    "destructive",
    "public_deploy",
    "public_release",
    "app_store",
    "testflight",
    "account_deletion",
    "acceptance",
}
REVOKE_CAUSES = (
    "will_resign_active",
    "background",
    "termination",
    "manual_lock",
    "protected_data_unavailable",
    "reboot",
    "disconnect",
    "trust_loss",
    "account_signout",
    "task_supersession",
    "deadline",
    "manual_stop",
)
ZERO_USAGE = {
    "model_turns": 0,
    "totalTokens": 0,
    "toolCalls": 0,
    "device_polls": 0,
    "retries": 0,
    "repeated_notifications": 0,
}

_REQUIRED_WINDOW_FLAGS = (
    "app_foreground_active",
    "protected_data_available",
    "task_valid",
    "session_valid",
    "account_valid",
    "generation_valid",
    "transport_ready",
    "paired",
    "tunnel_ready",
    "ddi_ready",
)
_SECRET_FIELD_NAMES = {
    "passcode",
    "password",
    "face_id",
    "touch_id",
    "apple_password",
    "otp",
    "token",
    "device_identifier",
    "raw_payload",
    "screenshot",
    "accessibility_tree",
    "dom",
}
_CHECKPOINT_FIELDS = (
    "status",
    "task_id",
    "session_id",
    "generation",
    "next_operation",
    "source",
    "package",
    "runtime_generation",
    "expires_at_monotonic",
    "private_generation",
    "counters",
    "request_count",
    "last_event_sequence",
)
_LEARNING_STATE_FIELDS = (
    "submitted",
    "visible",
    "consumed",
    "decision_changed",
    "implementation_adopted",
    "runtime_exercised",
    "user_accepted",
)

_IOS_SELECTION_ERRORS = {
    "IOS_DEVICE_SELECTION_MISSING",
    "IOS_DEVICE_SELECTION_MALFORMED",
    "IOS_DEVICE_SELECTION_DUPLICATE",
    "IOS_DEVICE_SELECTION_AMBIGUOUS",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_label(value: Any) -> bool:
    if not _nonempty_text(value):
        return False
    lowered = value.lower()
    return not any(term in lowered for term in ("passcode", "password", "token", "raw_payload"))


class IOSDeviceSelectionError(ValueError):
    """Value-free, non-retryable error for private iOS device selection."""

    def __init__(self, code: str):
        if code not in _IOS_SELECTION_ERRORS:
            code = "IOS_DEVICE_SELECTION_MALFORMED"
        self.code = code
        self.retry_count = 0
        super().__init__(code)

    def as_result(self) -> dict[str, Any]:
        return {
            "valid": False,
            "decision": "ios_device_selection_denied",
            "error": self.code,
            "retry_count": 0,
        }


class _PrivateIOSSelection(dict[str, Any]):
    """Safe receipt with IDs held privately for explicit consumer accessors."""

    def __init__(self, *, devicectl_identifier: str, iproxy_udid: str):
        # The dict is intentionally the only JSON-visible state.  The two IDs
        # live on private attributes and can only be handed to their named
        # consumer accessor.
        super().__init__(valid=True, decision="ios_device_selected", retry_count=0)
        self._devicectl_identifier = devicectl_identifier
        self._iproxy_udid = iproxy_udid


def _private_ios_id(value: Any) -> bool:
    return _nonempty_text(value) and value == value.strip()


def _ios_devices(result: Mapping[str, Any]) -> Sequence[Any]:
    result = _mapping(result)
    has_direct = "devices" in result
    direct = result.get("devices")
    nested_result = result.get("result")
    has_nested = isinstance(nested_result, Mapping) and "devices" in nested_result
    nested = nested_result.get("devices") if has_nested else None
    if has_direct and has_nested:
        raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_AMBIGUOUS")
    if not has_direct and "result" not in result:
        raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MISSING")
    if not has_direct and not has_nested:
        raise IOSDeviceSelectionError(
            "IOS_DEVICE_SELECTION_MALFORMED" if not isinstance(nested_result, Mapping) else "IOS_DEVICE_SELECTION_MISSING"
        )
    devices = direct if has_direct else nested
    if not isinstance(devices, Sequence) or isinstance(devices, (str, bytes)):
        raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MALFORMED")
    if not devices:
        raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MISSING")
    return devices


def select_private_ios_device(
    result: Mapping[str, Any], *, logical_identifier: str | None = None
) -> _PrivateIOSSelection:
    """Select one iOS device without crossing logical and hardware ID domains.

    ``identifier`` is reserved for the ``devicectl`` consumer.  The only
    value accepted for ``iproxy`` is ``hardwareProperties.udid``; there is no
    fallback when that field is absent or malformed.
    """

    if logical_identifier is not None and not _private_ios_id(logical_identifier):
        raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MALFORMED")

    devices = _ios_devices(result)
    parsed: list[tuple[str, str]] = []
    logical_ids: set[str] = set()
    hardware_ids: set[str] = set()
    for device in devices:
        if not isinstance(device, Mapping):
            raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MALFORMED")
        logical_id = device.get("identifier")
        hardware_properties = device.get("hardwareProperties")
        if not _private_ios_id(logical_id) or not isinstance(hardware_properties, Mapping):
            raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MALFORMED")
        hardware_id = hardware_properties.get("udid")
        if not _private_ios_id(hardware_id):
            raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MALFORMED")
        if logical_id in logical_ids or hardware_id in hardware_ids:
            raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_DUPLICATE")
        logical_ids.add(logical_id)
        hardware_ids.add(hardware_id)
        parsed.append((logical_id, hardware_id))

    if logical_identifier is None:
        if len(parsed) != 1:
            raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_AMBIGUOUS")
        selected = parsed[0]
    else:
        matches = [pair for pair in parsed if pair[0] == logical_identifier]
        if not matches:
            raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MISSING")
        if len(matches) != 1:
            raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_AMBIGUOUS")
        selected = matches[0]

    return _PrivateIOSSelection(devicectl_identifier=selected[0], iproxy_udid=selected[1])


def get_devicectl_identifier(selection: _PrivateIOSSelection) -> str:
    """Return only the logical ID required by ``devicectl``."""

    if not isinstance(selection, _PrivateIOSSelection):
        raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MALFORMED")
    return selection._devicectl_identifier


def get_iproxy_udid(selection: _PrivateIOSSelection) -> str:
    """Return only the hardware UDID required by ``iproxy``."""

    if not isinstance(selection, _PrivateIOSSelection):
        raise IOSDeviceSelectionError("IOS_DEVICE_SELECTION_MALFORMED")
    return selection._iproxy_udid


def ios_selection_decision(
    result: Mapping[str, Any], *, logical_identifier: str | None = None
) -> dict[str, Any]:
    """Return a sanitized selection receipt; never return either private ID."""

    try:
        return dict(select_private_ios_device(result, logical_identifier=logical_identifier))
    except IOSDeviceSelectionError as error:
        return error.as_result()


def _safe_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only fields that the checkpoint contract permits to cross a boundary."""

    result: dict[str, Any] = {}
    for field in _CHECKPOINT_FIELDS:
        if field not in checkpoint:
            continue
        value = checkpoint[field]
        if field == "counters":
            result[field] = dict(ZERO_USAGE)
        elif field in {"status", "next_operation", "source", "package", "runtime_generation"}:
            result[field] = value if _safe_label(value) else "invalid"
        elif field in {"task_id", "session_id"}:
            result[field] = value if _nonempty_text(value) else "invalid"
        elif field in {"generation", "request_count", "last_event_sequence"}:
            result[field] = value if _nonnegative_int(value) else 0
        elif field == "expires_at_monotonic":
            result[field] = value if _finite_number(value) else 0
        elif field == "private_generation":
            result[field] = value if _safe_label(value) else "invalid"
    return result


def validate_service_window(request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a Remote Ops-owned, in-memory service-window contract."""

    request = _mapping(request)
    duration = request.get("duration_minutes", DEFAULT_SERVICE_DURATION)
    reason = "ok"
    valid = True

    if type(duration) is not int or duration not in SERVICE_DURATIONS:
        valid, reason = False, "duration_not_allowed"
    if not _nonempty_text(request.get("task_id")) or not _nonempty_text(request.get("session_id")):
        valid, reason = False, "task_or_session_invalid"
    if not _nonnegative_int(request.get("generation")):
        valid, reason = False, "generation_invalid"
    operation_class = request.get("operation_class")
    if operation_class not in OPERATION_CLASSES:
        valid, reason = False, "operation_class_invalid"
    allowed = request.get("allowed_operations")
    if not isinstance(allowed, Sequence) or isinstance(allowed, (str, bytes)) or not allowed:
        valid, reason = False, "operation_set_invalid"
    elif len(set(allowed)) != len(allowed) or not set(allowed) <= SAFE_OPERATIONS:
        valid, reason = False, "operation_set_outside_envelope"
    if not _finite_number(request.get("now_monotonic")):
        valid, reason = False, "monotonic_time_invalid"
    for flag in _REQUIRED_WINDOW_FLAGS:
        if request.get(flag) is not True:
            valid, reason = False, f"{flag}_unavailable"
    if request.get("lease_owner") != "remote-ops":
        valid, reason = False, "lease_owner_invalid"
    if request.get("lease_storage") != "nonexportable_in_memory":
        valid, reason = False, "lease_storage_invalid"
    if request.get("consumer") != "uikit":
        valid, reason = False, "consumer_binding_invalid"

    control_state = request.get("control_state")
    if operation_class == "physical_unlocked_required" and control_state != "unlocked":
        valid, reason = False, "physical_unlock_required"
    if operation_class == "mirroring_locked_required" and control_state != "locked":
        valid, reason = False, "mirroring_lock_required"
    if operation_class == "either" and control_state not in {"locked", "unlocked"}:
        valid, reason = False, "control_state_invalid"

    result: dict[str, Any] = {
        "valid": valid,
        "decision": "allow_service_window" if valid else "deny_service_window",
        "reason": reason,
        "duration_minutes": duration if type(duration) is int else DEFAULT_SERVICE_DURATION,
        "recommended_duration_minutes": DEFAULT_SERVICE_DURATION,
        "lease_persisted": False,
        "idle_timer_disabled": valid,
        "cancel_pending_physical_mutation": not valid,
    }
    if _nonempty_text(request.get("task_id")):
        result["task_id"] = request["task_id"]
    if _nonempty_text(request.get("session_id")):
        result["session_id"] = request["session_id"]
    if _nonnegative_int(request.get("generation")):
        result["generation"] = request["generation"]
    if operation_class in OPERATION_CLASSES:
        result["operation_class"] = operation_class
    if valid:
        result.update(
            {
                "allowed_operations": list(allowed),
                "control_state": control_state,
                "expires_at_monotonic": request["now_monotonic"] + duration * 60,
                "operation_counter": 0,
                "continuation_used": False,
            }
        )
    return result


def _window_context_valid(window: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[bool, str]:
    if not window.get("valid"):
        return False, "window_invalid"
    now = context.get("now_monotonic")
    if not _finite_number(now) or now > window.get("expires_at_monotonic", -1):
        return False, "window_expired"
    if context.get("app_foreground_active", True) is not True:
        return False, "app_not_active"
    if context.get("protected_data_available", True) is not True:
        return False, "protected_data_unavailable"
    if any(context.get(flag, True) is not True for flag in ("task_valid", "session_valid", "account_valid", "generation_valid")):
        return False, "identity_invalid"
    control_state = context.get("control_state", window.get("control_state", "unlocked"))
    if window.get("operation_class") == "physical_unlocked_required" and control_state != "unlocked":
        return False, "manual_lock"
    if window.get("operation_class") == "mirroring_locked_required" and control_state != "locked":
        return False, "mirroring_lock_required"
    if window.get("operation_class") == "either" and control_state not in {"locked", "unlocked"}:
        return False, "control_state_invalid"
    if any(context.get(flag, True) is not True for flag in ("transport_ready", "paired", "tunnel_ready", "ddi_ready")):
        return False, "transport_unavailable"
    return True, "ok"


def decide_operation(window: Mapping[str, Any], operation: str, context: Mapping[str, Any]) -> dict[str, Any]:
    """Authorize one visible operation without performing it."""

    context = _mapping(context)
    valid, reason = _window_context_valid(window, context)
    allowed = valid and operation in set(window.get("allowed_operations", ()))
    if operation in FORBIDDEN_OPERATIONS:
        allowed, reason = False, "operation_outside_authority"
    if valid and operation == "install" and not (context.get("changed") and context.get("needed")):
        allowed, reason = False, "install_not_changed_and_needed"
    if valid and operation == "launch" and not context.get("needed"):
        allowed, reason = False, "launch_not_needed"
    if valid and operation == "continue" and (not context.get("cause_changed") or context.get("continuation_used")):
        allowed, reason = False, "continuation_not_cause_changed"
    if operation == "observe" and not valid:
        allowed = False
    if reason == "ok" and not allowed:
        reason = "operation_not_allowed"
    if reason == "ok" and context.get("control_state") == "locked" and window.get("operation_class") == "physical_unlocked_required":
        allowed, reason = False, "manual_lock"
    counter = window.get("operation_counter", 0) + (1 if allowed else 0)
    return {
        "allowed": allowed,
        "decision": "allow_operation" if allowed else "deny_operation",
        "reason": reason,
        "operation_counter": counter,
        "idle_timer_disabled": bool(valid),
        "cancel_pending_physical_mutation": not allowed,
        "models_suspended": bool(context.get("idle_gap", False)),
        "usage": dict(ZERO_USAGE) if context.get("idle_gap", False) else dict(ZERO_USAGE),
    }


def revoke_service_window(window: Mapping[str, Any], cause: str) -> dict[str, Any]:
    """Return the immediate fail-closed decision for every revocation cause."""

    valid_cause = cause in REVOKE_CAUSES
    return {
        "decision": "revoke_service_window",
        "cause": cause if valid_cause else "unknown_revoke_cause",
        "idle_timer_disabled": False,
        "cancel_pending_physical_mutation": True,
        "lease_action": "revoke",
        "lease_persisted": False,
    }


def authority_envelope_contains(
    window: Mapping[str, Any], *, task_id: str, session_id: str, generation: int, operation: str
) -> bool:
    """Check exact identity and operation containment without expanding authority."""

    return bool(
        window.get("valid")
        and window.get("task_id") == task_id
        and window.get("session_id") == session_id
        and window.get("generation") == generation
        and operation in set(window.get("allowed_operations", ()))
        and operation in SAFE_OPERATIONS
        and operation not in FORBIDDEN_OPERATIONS
    )


def build_waiting_checkpoint(
    *,
    task_id: str,
    session_id: str,
    generation: int,
    next_operation: str,
    source: str,
    package: str,
    runtime_generation: str,
    expires_at_monotonic: float,
    private_generation: str,
    **_ignored_secret_fields: Any,
) -> dict[str, Any]:
    """Build the only safe checkpoint shape for a locked physical fallback."""

    if not all((_nonempty_text(task_id), _nonempty_text(session_id), _nonnegative_int(generation))):
        raise ValueError("invalid_task_identity")
    if next_operation not in SAFE_OPERATIONS:
        raise ValueError("invalid_next_operation")
    if not all(_safe_label(value) for value in (source, package, runtime_generation, private_generation)):
        raise ValueError("unsafe_checkpoint_label")
    if not _finite_number(expires_at_monotonic):
        raise ValueError("invalid_expiry")
    return {
        "status": "WAITING_PHYSICAL_UNLOCK",
        "task_id": task_id,
        "session_id": session_id,
        "generation": generation,
        "next_operation": next_operation,
        "source": source,
        "package": package,
        "runtime_generation": runtime_generation,
        "expires_at_monotonic": expires_at_monotonic,
        "private_generation": private_generation,
        "counters": dict(ZERO_USAGE),
        "request_count": 0,
        "last_event_sequence": 0,
    }


def waiting_usage(checkpoint: Mapping[str, Any], elapsed_seconds: float) -> dict[str, int]:
    """Waiting is event-driven and has no model/device usage, including after wake."""

    _ = checkpoint, elapsed_seconds
    return dict(ZERO_USAGE)


def _event_result(
    checkpoint: Mapping[str, Any],
    *,
    decision: str,
    reason: str,
    model_wake: bool = False,
    mutation_allowed: bool = False,
    resume_exact_task: bool = False,
    subscription_action: str = "retain",
    next_action: str | None = None,
    request_count: int | None = None,
    checkpoint_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "decision": decision,
        "reason": reason,
        "model_wake": model_wake,
        "mutation_allowed": mutation_allowed,
        "resume_exact_task": resume_exact_task,
        "polling": False,
        "subscription_action": subscription_action,
        "usage": dict(ZERO_USAGE),
        "request_count": request_count if request_count is not None else checkpoint.get("request_count", 0),
        "checkpoint": _safe_checkpoint(checkpoint_override or checkpoint),
    }
    if next_action is not None:
        result["next_action"] = next_action
    return result


def evaluate_waiting_event(checkpoint: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one transition event; never poll and never wake a model."""

    checkpoint = _mapping(checkpoint)
    event = _mapping(event)
    if event.get("source_qualified") is not True:
        return _event_result(
            checkpoint,
            decision="LOCK_STATE_EVENT_SOURCE_UNAVAILABLE",
            reason="consumer_binding_unqualified",
            next_action="normal_surface_resume_affordance",
        )
    if checkpoint.get("status") != "WAITING_PHYSICAL_UNLOCK":
        return _event_result(checkpoint, decision="terminal", reason="checkpoint_not_waiting", subscription_action="remove")
    if event.get("cancelled") is True:
        return _event_result(checkpoint, decision="terminal", reason="cancelled", subscription_action="remove")
    if event.get("disconnected") is True:
        return _event_result(checkpoint, decision="terminal", reason="disconnected", subscription_action="remove")
    now = event.get("now_monotonic")
    if not _finite_number(now) or now > checkpoint.get("expires_at_monotonic", -1):
        return _event_result(checkpoint, decision="terminal", reason="expired", subscription_action="remove")
    sequence = event.get("event_sequence")
    if not _nonnegative_int(sequence) or sequence <= checkpoint.get("last_event_sequence", 0):
        return _event_result(checkpoint, decision="stay_waiting", reason="stale_or_duplicate_event")
    if any(event.get(field) != checkpoint.get(field) for field in ("task_id", "session_id", "generation")):
        return _event_result(checkpoint, decision="stay_waiting", reason="wrong_task_generation")
    if event.get("transition") != "physical_unlock":
        return _event_result(checkpoint, decision="stay_waiting", reason="irrelevant_transition")
    if event.get("control_state") != "unlocked" or event.get("transport_ready") is not True:
        return _event_result(checkpoint, decision="stay_waiting", reason="physical_or_transport_unready")
    if event.get("operation_class") != "physical_unlocked_required":
        return _event_result(checkpoint, decision="stay_waiting", reason="operation_class_mismatch")

    resumed_checkpoint = dict(checkpoint)
    resumed_checkpoint.update(
        {"status": "RESUMED", "request_count": 1, "last_event_sequence": sequence, "counters": dict(ZERO_USAGE)}
    )
    return _event_result(
        checkpoint,
        decision="resume",
        reason="qualified_unlock_transition",
        mutation_allowed=True,
        resume_exact_task=True,
        subscription_action="remove",
        request_count=1,
        checkpoint_override=resumed_checkpoint,
    )


def evaluate_host_wake(checkpoint: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate at most one already-delivered event after host wake."""

    if not events:
        result = _event_result(checkpoint, decision="stay_waiting", reason="no_transition_event")
        result["evaluated_events"] = 0
        return result
    result = evaluate_waiting_event(checkpoint, events[0])
    result["evaluated_events"] = 1
    return result


def classify_control_context(*, locked: bool, mirroring: bool) -> str:
    if locked is True and mirroring is True:
        return "mirroring"
    if locked is False and mirroring is False:
        return "physical"
    return "unsupported"


def project_learning_state(observation: Mapping[str, Any]) -> dict[str, bool]:
    """Project independent evidence-layer flags without inferring progression."""

    observation = _mapping(observation)
    return {field: observation.get(field) is True for field in _LEARNING_STATE_FIELDS}


def decide_one_shot_control_transition(
    *,
    previous_control: str,
    current_control: str,
    cause_changed: bool,
    transition_used: bool,
    local_mirroring_route_callable: bool,
) -> dict[str, Any]:
    """Permit one cause-changed physical-to-Mirroring transition only."""

    result = {
        "decision": "control_transition_waiting",
        "reason": "control_transition_unavailable",
        "transition_allowed": False,
        "device_action_allowed": False,
        "waiting_status": (
            "WAITING_PHYSICAL_LOCK"
            if current_control == "mirroring"
            else "WAITING_PHYSICAL_UNLOCK"
        ),
        "portal_restart_allowed": False,
        "polling": False,
        "model_wake": False,
        "usage": dict(ZERO_USAGE),
    }
    if previous_control != "physical" or current_control != "mirroring":
        result["reason"] = "control_transition_mismatch"
    elif not cause_changed:
        result["reason"] = "transition_not_cause_changed"
    elif transition_used:
        result["reason"] = "transition_already_used"
    elif not local_mirroring_route_callable:
        result["reason"] = "local_mirroring_route_unavailable"
        result["waiting_status"] = "WAITING_PHYSICAL_LOCK"
    else:
        result.update(
            {
                "decision": "control_transition_allowed",
                "reason": "cause_changed_transition_once",
                "transition_allowed": True,
                "device_action_allowed": True,
                "next_action": "mirroring_transition_once",
            }
        )
    return result


def validate_foreground_binding(
    observation: Mapping[str, Any],
    *,
    expected_bundle_id: str,
    expected_app: str,
    allowlisted_markers: Sequence[str] | set[str] | frozenset[str],
) -> dict[str, Any]:
    """Require the expected app identity before making protected claims.

    The caller supplies observations and the expected app binding.  The
    result deliberately contains only typed claims; it never reflects a raw
    bundle ID, app label, marker, tree, screenshot, device ID, or secret.
    """

    observation = _mapping(observation)
    if not _nonempty_text(expected_bundle_id) or not _nonempty_text(expected_app):
        return {
            "decision": "foreground_binding_required",
            "foreground_binding_verified": False,
            "protected_screen": False,
            "credential_claim": False,
            "trust_claim": False,
            "ui_claim": False,
            "device_action_allowed": False,
        }

    exact_binding = (
        observation.get("foreground_bundle_id") == expected_bundle_id
        and observation.get("foreground_app") == expected_app
    )
    if not exact_binding:
        return {
            "decision": "foreground_app_mismatch",
            "foreground_binding_verified": False,
            "protected_screen": False,
            "credential_claim": False,
            "trust_claim": False,
            "ui_claim": False,
            "device_action_allowed": False,
        }

    markers = set(allowlisted_markers) if isinstance(allowlisted_markers, (Sequence, set, frozenset)) else set()
    protected_screen = observation.get("marker") in markers
    return {
        "decision": "foreground_binding_verified",
        "foreground_binding_verified": True,
        "protected_screen": protected_screen,
        "credential_claim": protected_screen and observation.get("credential_surface_present") is True,
        "trust_claim": protected_screen and observation.get("trust_surface_present") is True,
        "ui_claim": protected_screen,
        "device_action_allowed": not protected_screen,
    }


def validate_portal_live(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Require XCTest Runner/process presence, exact target, and loopback HTTP."""

    evidence = _mapping(evidence)
    live = (
        evidence.get("xctest_runner_present") is True
        and evidence.get("runner_target") == "Droidrun Server"
        and evidence.get("http_status") == 200
        and evidence.get("http_path") == "/device/date"
    )
    return {
        "live": live,
        "decision": "portal_live" if live else "portal_not_live",
        "reason": "runner_and_loopback_http" if live else "runner_http_target_required",
    }


def decide_portal_observe_launch_reobserve(
    *,
    initial: Mapping[str, Any],
    launch: Mapping[str, Any],
    reobserve: Mapping[str, Any],
    target_process_alive: bool,
    mirroring_route_callable: bool,
    failover_used: bool = False,
    control_state: str = "locked",
) -> dict[str, Any]:
    """Require a Portal observe/launch/reobserve sequence and one failover.

    Portal reachability alone is never control acceptance.  If launching the
    target invalidates the Portal runner, an alive target may use one admitted
    local Mirroring failover.  Otherwise the caller receives a typed physical
    wait with zero model/tool/device usage and no Portal restart/start-app
    retry.
    """

    initial_live = validate_portal_live(initial).get("live") is True
    launch_observed = _mapping(launch).get("target_launch_observed") is True
    reobserve_live = validate_portal_live(reobserve).get("live") is True
    runner_invalidated = _mapping(launch).get("runner_present_after_launch") is False or not reobserve_live
    sequence = ["observe", "launch", "reobserve"]
    base = {
        "sequence": sequence if initial_live else ["observe"],
        "control_acceptance": False,
        "device_action_allowed": False,
        "model_wake": False,
        "tool_polling": False,
        "usage": dict(ZERO_USAGE),
        "portal_restart_allowed": False,
        "start_app_allowed": False,
        "failover_allowed": False,
    }
    if not initial_live:
        return {**base, "decision": "portal_not_live", "reason": "observe_required_before_launch"}
    if not launch_observed:
        return {**base, "decision": "portal_launch_unobserved", "reason": "launch_observation_required"}
    if not runner_invalidated:
        return {
            **base,
            "decision": "portal_control_accepted",
            "reason": "observe_launch_reobserve_verified",
            "control_acceptance": True,
            "device_action_allowed": True,
        }

    if target_process_alive is True and mirroring_route_callable is True and failover_used is not True:
        return {
            **base,
            "decision": "portal_session_invalidated",
            "reason": "target_launch_invalidated_runner",
            "next_action": "mirroring_failover_once",
            "failover_allowed": True,
        }

    waiting_status = "WAITING_PHYSICAL_LOCK" if control_state == "locked" else "WAITING_PHYSICAL_UNLOCK"
    return {
        **base,
        "decision": "portal_session_invalidated",
        "reason": "target_launch_invalidated_runner",
        "next_action": "wait_for_physical_state",
        "waiting_status": waiting_status,
    }


def validate_iproxy_binding(
    *, host: str, port: int, authorized_hardware_binding: str, proxy_hardware_binding: str
) -> dict[str, Any]:
    """Allow only loopback and an exact opaque binding; never return identifiers."""

    valid = (
        host in {"127.0.0.1", "::1"}
        and type(port) is int
        and 1 <= port <= 65535
        and _nonempty_text(authorized_hardware_binding)
        and authorized_hardware_binding == proxy_hardware_binding
    )
    return {
        "valid": valid,
        "decision": "iproxy_allowed" if valid else "iproxy_denied",
        "reason": "loopback_exact_binding" if valid else "loopback_and_binding_required",
    }


def decide_observe_action_reobserve(
    *,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    requested_action: str,
    changed: bool,
    needed: bool,
) -> dict[str, Any]:
    """Enforce observe -> one action -> reobserve with monotonic observation IDs."""

    previous_counter = previous.get("observation_counter")
    current_counter = current.get("observation_counter")
    monotonic = _nonnegative_int(previous_counter) and _nonnegative_int(current_counter) and current_counter > previous_counter
    allowed = monotonic and requested_action in {"install", "launch"} and changed and needed
    reason = "ok" if allowed else "unchanged_observation" if not monotonic else "action_not_changed_and_needed"
    return {
        "allowed": allowed,
        "reason": reason,
        "sequence": ["observe", "action", "reobserve"] if allowed else ["observe"],
        "observation_counter": current_counter if _nonnegative_int(current_counter) else 0,
        "retry_count": 0,
    }


def _cli_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    operation = payload.get("operation")
    if operation == "select_private_ios_device":
        result = payload.get("result", payload)
        return ios_selection_decision(result, logical_identifier=payload.get("logical_identifier"))
    if operation == "classify_control_context":
        return {
            "control_context": classify_control_context(
                locked=payload.get("locked") is True,
                mirroring=payload.get("mirroring") is True,
            ),
            "decision": "ok",
        }
    if operation == "validate_portal_live":
        return validate_portal_live(payload)
    if operation == "validate_foreground_binding":
        return validate_foreground_binding(
            payload,
            expected_bundle_id=payload.get("expected_bundle_id", ""),
            expected_app=payload.get("expected_app", ""),
            allowlisted_markers=payload.get("allowlisted_markers", ()),
        )
    if operation == "project_learning_state":
        return project_learning_state(payload)
    if operation == "decide_one_shot_control_transition":
        return decide_one_shot_control_transition(
            previous_control=payload.get("previous_control", ""),
            current_control=payload.get("current_control", ""),
            cause_changed=payload.get("cause_changed") is True,
            transition_used=payload.get("transition_used") is True,
            local_mirroring_route_callable=payload.get("local_mirroring_route_callable") is True,
        )
    if operation == "decide_portal_observe_launch_reobserve":
        return decide_portal_observe_launch_reobserve(
            initial=payload.get("initial", {}),
            launch=payload.get("launch", {}),
            reobserve=payload.get("reobserve", {}),
            target_process_alive=payload.get("target_process_alive") is True,
            mirroring_route_callable=payload.get("mirroring_route_callable") is True,
            failover_used=payload.get("failover_used") is True,
            control_state=payload.get("control_state", "locked"),
        )
    if operation == "validate_iproxy_binding":
        return validate_iproxy_binding(
            host=payload.get("host", ""),
            port=payload.get("port", 0),
            authorized_hardware_binding=payload.get("authorized_hardware_binding", ""),
            proxy_hardware_binding=payload.get("proxy_hardware_binding", ""),
        )
    return {"decision": "unsupported_operation", "reason": "pure_contract_only"}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = _cli_result(payload if isinstance(payload, Mapping) else {})
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        result = {"decision": "invalid_input", "reason": "json_object_required"}
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
