"""Pure SDO result validation, projection, and session-route consumption.

Producer execution and source discovery stay in the gateway.  This module
only validates the fixed producer's JSON result, projects a compact bounded
decision, and consumes that decision before lazy agent construction.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Final


NATURAL_SELECTION_REASON: Final = "natural_delegated_nontrivial"
NATURAL_PROVIDER: Final = "openai-codex"
NATURAL_MODEL: Final = "gpt-5.6-luna"
NATURAL_EFFORT: Final = "max"
NATURAL_TIER: Final = "fast"
NATURAL_TRANSITION: Final = "natural-transition"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_DIGEST_RE = re.compile(r"^opaque:sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@:\-]{0,127}$")
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_SECRET_RE = re.compile(
    r"(?i)(?:sk-|gh[opurs]_|xox[baprs]-|AIza|eyJ[A-Za-z0-9_-]*\.|"
    r"(?:^|[._-])(secret|token|password|api[_-]?key)(?:$|[=:._-]))"
)
_BINDING_FIELDS = frozenset(
    {
        "project_id",
        "repo_id",
        "worktree_id",
        "goal_ref",
        "request_ref",
        "transition",
        "logical_session_id",
        "operation_id",
    }
)
_RESULT_STATUSES = frozenset(
    {"complete", "error", "interrupted", "blocked", "failed"}
)
_DECISION_FIELDS = frozenset(
    {
        "selection_reason",
        "provider",
        "model",
        "effort",
        "tier",
        "receipt_digest",
        "expires_at",
        "binding",
        "selected_action_id",
        "base_selected_action_id",
        "decision",
        "dispatch_mode",
        "action_changed",
        "replan_required",
    }
)
NATURAL_DECISION_FIELDS: Final = _DECISION_FIELDS
_RECEIPT_BINDING_FIELDS = frozenset(
    {"receipt_id", "receipt_digest", "receipt_consumed", "consumed_by"}
)
_CURRENT_PRODUCER_STATUS = "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY"
_INVALID = object()


def safe_local_continuation(reason: str, *, status: str = "unavailable") -> dict[str, Any]:
    """Return a value-free, non-routing result for one withheld claim."""

    safe_reason = reason if _SAFE_ID_RE.fullmatch(reason) else "sdo_unavailable"
    return {
        "claim_status": "withheld",
        "claim_withheld_reason": safe_reason,
        "safe_local_continuation": True,
        "selection_reason": None,
        "provider": None,
        "model": None,
        "effort": None,
        "tier": None,
        "selected_action_id": None,
        "base_selected_action_id": None,
        "decision": "CONTINUE_LOCAL",
        "dispatch_mode": "safe_local_baseline",
        "replan_required": False,
        "model_route": None,
        "receipt_digest": None,
        "terminal_status": status if status in _RESULT_STATUSES else "unavailable",
    }


def unavailable_decision(_context: Mapping[str, Any]) -> None:
    """Default callable: no authority/runtime decision is available."""

    return None


def _safe_identifier(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or "/" in value
        or "\\" in value
        or _SECRET_RE.search(value) is not None
    ):
        return None
    if _CANONICAL_DIGEST_RE.fullmatch(value):
        return value
    if _URI_RE.match(value) is not None or _SAFE_ID_RE.fullmatch(value) is None:
        return None
    return value


def _optional_identifier(value: Any) -> str | None | object:
    if value is None:
        return None
    checked = _safe_identifier(value)
    return checked if checked is not None else _INVALID


def _future_timestamp(value: Any, now: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        checked = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(checked) or checked <= now:
        return None
    return checked


def _binding_digest(binding: Mapping[str, str]) -> str:
    serialized = json.dumps(dict(binding), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _producer_receipt_digest(receipt: Mapping[str, Any]) -> str | None:
    content = {
        key: value
        for key, value in receipt.items()
        if key not in _RECEIPT_BINDING_FIELDS
    }
    try:
        serialized = json.dumps(content, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _iso_timestamp(value: Any, now: float) -> float | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return _future_timestamp(parsed.astimezone(timezone.utc).timestamp(), now)


def project_natural_producer_result(
    raw_result: Any,
    *,
    context: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    now: float | None = None,
) -> dict[str, Any] | None:
    """Project one authenticated current-producer result into a route claim.

    The projection deliberately rebuilds the binding from the already
    authenticated prompt context and the gateway-observed Git identity.  It
    never forwards a producer authority field or a caller-supplied binding.
    """

    checked_now = time.time() if now is None else now
    try:
        checked_now = float(checked_now)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(checked_now):
        return None

    # Keep the operational-context validator as the single authority for the
    # current goal/operation/target/revision and freshness contract.  Import
    # locally so this pure adapter stays usable by hermes_state at startup.
    try:
        import hermes_state

        context_status, authenticated = hermes_state.validate_orch_operational_context(
            dict(context), now=checked_now
        )
    except (ImportError, TypeError, ValueError, OverflowError):
        return None
    if context_status != "context_accepted" or not isinstance(authenticated, Mapping):
        return None

    if not isinstance(source_identity, Mapping) or set(source_identity) != {
        "head",
        "repo_id",
        "worktree_id",
    }:
        return None
    decision_binding = authenticated.get("decision_binding")
    declaration = authenticated.get("task_declaration")
    if not isinstance(decision_binding, Mapping) or not isinstance(declaration, Mapping):
        return None
    head = source_identity.get("head")
    repo_id = source_identity.get("repo_id")
    worktree_id = source_identity.get("worktree_id")
    if (
        not isinstance(head, str)
        or re.fullmatch(r"[0-9a-f]{40}", head) is None
        or head != decision_binding.get("runtime_revision")
        or not isinstance(repo_id, str)
        or _CANONICAL_DIGEST_RE.fullmatch(repo_id) is None
        or not isinstance(worktree_id, str)
        or _CANONICAL_DIGEST_RE.fullmatch(worktree_id) is None
    ):
        return None

    if not isinstance(raw_result, Mapping):
        return None
    if (
        raw_result.get("status") != _CURRENT_PRODUCER_STATUS
        or raw_result.get("support_work_progress_credit", 0) != 0
    ):
        return None
    receipt = raw_result.get("sdo_decision_receipt")
    if not isinstance(receipt, Mapping):
        return None
    receipt_id = receipt.get("receipt_id")
    receipt_digest = receipt.get("receipt_digest")
    if (
        receipt.get("schema_version") != "sdo_decision_receipt.v1"
        or not isinstance(receipt_id, str)
        or _DIGEST_RE.fullmatch(receipt_id) is None
        or receipt_id != receipt_digest
        or receipt.get("receipt_consumed") is not False
        or receipt.get("consumed_by") != ""
        or _producer_receipt_digest(receipt) != receipt_digest
        or receipt.get("receipt_expiry_is_authority") is not False
        or receipt.get("support_work_progress_credit") != 0
    ):
        return None

    repo_facts = receipt.get("repo_facts")
    if not isinstance(repo_facts, Mapping) or (
        receipt.get("project_id") != decision_binding.get("project_id")
        or repo_facts.get("head_ref") != head
        or repo_facts.get("goal_ref") != authenticated.get("goal")
        or repo_facts.get("phase_ref") != declaration.get("task_class")
    ):
        return None

    selected_action = _optional_identifier(receipt.get("selected_action_id"))
    base_action = _optional_identifier(receipt.get("base_selected_action_id"))
    decision = receipt.get("decision")
    capability_delta = receipt.get("capability_delta")
    if (
        selected_action is _INVALID
        or base_action is _INVALID
        or selected_action is None
        or base_action is None
        or decision not in {"CONTINUE_LOCAL", "REPLAN_NOW"}
        or not isinstance(capability_delta, Mapping)
        or type(capability_delta.get("action_changed")) is not bool
        or capability_delta.get("action_changed") != (selected_action != base_action)
        or raw_result.get("decision") != decision
        or raw_result.get("selected_action_id") != selected_action
    ):
        return None

    route = receipt.get("model_route")
    fast_mode = route.get("fast_mode") if isinstance(route, Mapping) else None
    result_route = raw_result.get("model_routing")
    if (
        not isinstance(route, Mapping)
        or route.get("provider") != NATURAL_PROVIDER
        or route.get("model") != NATURAL_MODEL
        or route.get("reasoning_effort") != NATURAL_EFFORT
        or route.get("selection_reason") != NATURAL_SELECTION_REASON
        or route.get("runtime_identity_verified") is not False
        or not isinstance(fast_mode, Mapping)
        or fast_mode.get("selected") is not True
        or fast_mode.get("service_tier_preference") != NATURAL_TIER
        or fast_mode.get("claim_withheld") is not True
        or fast_mode.get("runtime_verified") is not False
        or not isinstance(result_route, Mapping)
        or any(
            result_route.get(key) != route.get(key)
            for key in (
                "provider",
                "model",
                "reasoning_effort",
                "selection_reason",
                "runtime_identity_verified",
            )
        )
    ):
        return None

    protected = receipt.get("protected_transition")
    top_authority = raw_result.get("authority_transition")
    if (
        not isinstance(protected, Mapping)
        or protected.get("requested") is not False
        or protected.get("allowed") is not False
        or protected.get("execution_authorized") is not False
        or not isinstance(top_authority, Mapping)
        or top_authority.get("requested") is not False
        or top_authority.get("allowed") is not False
        or top_authority.get("execution_authorized") is not False
    ):
        return None

    expiry = receipt.get("expiry")
    if (
        not isinstance(expiry, Mapping)
        or expiry.get("scope") != "current_transition"
        or expiry.get("claim_ttl_seconds") != 300
    ):
        return None
    expires_at = _iso_timestamp(expiry.get("expires_at"), checked_now)
    context_expires_at = _future_timestamp(authenticated.get("expires_at"), checked_now)
    if expires_at is None or context_expires_at is None:
        return None

    dispatch_mode = "replan_local" if decision == "REPLAN_NOW" else "continue_local"
    replan_required = decision == "REPLAN_NOW"
    return {
        "selection_reason": NATURAL_SELECTION_REASON,
        "provider": NATURAL_PROVIDER,
        "model": NATURAL_MODEL,
        "effort": NATURAL_EFFORT,
        "tier": NATURAL_TIER,
        "receipt_digest": receipt_digest,
        "expires_at": min(expires_at, context_expires_at),
        "binding": {
            "project_id": decision_binding["project_id"],
            "repo_id": repo_id,
            "worktree_id": worktree_id,
            "goal_ref": authenticated["goal"],
            "request_ref": decision_binding["decision_id"],
            "transition": NATURAL_TRANSITION,
            "logical_session_id": decision_binding["logical_session_id"],
            "operation_id": authenticated["operation_id"],
        },
        "selected_action_id": selected_action,
        "base_selected_action_id": base_action,
        "decision": decision,
        "dispatch_mode": dispatch_mode,
        "action_changed": capability_delta["action_changed"],
        "replan_required": replan_required,
    }


def consume_sdo_decision(
    raw_decision: Any,
    *,
    context: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Validate one already-supplied bounded decision.

    The injected callable supplies route fields, a one-use receipt digest and
    expiry, and an opaque binding. This function never invokes a producer,
    opens a path, starts a process, or verifies a trust root.
    """

    if not isinstance(raw_decision, Mapping):
        return safe_local_continuation("sdo_decision_unavailable")
    # Preserve the more useful typed provider-malformed result for the common
    # missing-provider case while rejecting every other missing/extra field.
    if "provider" not in raw_decision:
        return safe_local_continuation("sdo_provider_malformed")
    if set(raw_decision) != _DECISION_FIELDS:
        return safe_local_continuation("sdo_decision_malformed")
    selection_reason = raw_decision.get("selection_reason")
    if not isinstance(selection_reason, str):
        return safe_local_continuation("sdo_selection_malformed")
    if selection_reason != NATURAL_SELECTION_REASON:
        return safe_local_continuation("sdo_route_unsupported")

    provider = raw_decision.get("provider")
    if not isinstance(provider, str) or not provider:
        return safe_local_continuation("sdo_provider_malformed")
    if provider != NATURAL_PROVIDER:
        return safe_local_continuation("sdo_provider_unsupported")
    if raw_decision.get("model") != NATURAL_MODEL:
        return safe_local_continuation("sdo_model_unsupported")
    if raw_decision.get("effort") != NATURAL_EFFORT:
        return safe_local_continuation("sdo_effort_unsupported")
    if raw_decision.get("tier") != NATURAL_TIER:
        return safe_local_continuation("sdo_tier_unsupported")

    receipt_digest = raw_decision.get("receipt_digest")
    if not isinstance(receipt_digest, str) or _DIGEST_RE.fullmatch(receipt_digest) is None:
        return safe_local_continuation("sdo_receipt_malformed")
    checked_now = time.time() if now is None else now
    try:
        checked_now = float(checked_now)
    except (TypeError, ValueError, OverflowError):
        return safe_local_continuation("sdo_clock_invalid")
    if not math.isfinite(checked_now):
        return safe_local_continuation("sdo_clock_invalid")
    expires_at = _future_timestamp(raw_decision.get("expires_at"), checked_now)
    if expires_at is None:
        return safe_local_continuation("sdo_receipt_stale")

    binding = raw_decision.get("binding")
    if not isinstance(binding, Mapping) or set(binding) != _BINDING_FIELDS:
        return safe_local_continuation("sdo_binding_malformed")
    safe_binding: dict[str, str] = {}
    for key in sorted(_BINDING_FIELDS):
        checked = _safe_identifier(binding.get(key))
        if checked is None:
            return safe_local_continuation("sdo_binding_malformed")
        safe_binding[key] = checked
    if safe_binding["transition"] != NATURAL_TRANSITION:
        return safe_local_continuation("sdo_binding_mismatch")
    if isinstance(context, Mapping):
        expected_binding: dict[str, Any] = {}
        operation_id = context.get("operation_id")
        if operation_id is not None:
            expected_binding["operation_id"] = operation_id
        decision_binding = context.get("decision_binding")
        if isinstance(decision_binding, Mapping):
            for key in (
                "project_id",
                "repo_id",
                "worktree_id",
                "goal_ref",
                "request_ref",
                "logical_session_id",
            ):
                if key in decision_binding:
                    expected_binding[key] = decision_binding.get(key)
        for key in ("repo_id", "worktree_id", "goal_ref", "request_ref"):
            if key in context:
                expected_binding[key] = context.get(key)
        if "transition" in context:
            expected_binding["transition"] = context.get("transition")
        if any(safe_binding.get(key) != value for key, value in expected_binding.items()):
            return safe_local_continuation("sdo_binding_mismatch")

    selected_action = _optional_identifier(raw_decision.get("selected_action_id"))
    base_action = _optional_identifier(raw_decision.get("base_selected_action_id"))
    if selected_action is _INVALID or base_action is _INVALID:
        return safe_local_continuation("sdo_action_malformed")
    decision = raw_decision.get("decision", "CONTINUE_LOCAL")
    dispatch_mode = raw_decision.get("dispatch_mode", "continue_local")
    if decision not in {"CONTINUE_LOCAL", "REPLAN_NOW"}:
        return safe_local_continuation("sdo_decision_malformed")
    if dispatch_mode not in {"continue_local", "replan_local"}:
        return safe_local_continuation("sdo_dispatch_malformed")
    action_changed = raw_decision.get("action_changed")
    replan_required = raw_decision.get("replan_required")
    if type(action_changed) is not bool or type(replan_required) is not bool:
        return safe_local_continuation("sdo_outcome_malformed")
    if action_changed != (selected_action != base_action):
        return safe_local_continuation("sdo_outcome_malformed")
    if decision == "REPLAN_NOW":
        if dispatch_mode != "replan_local" or replan_required is not True:
            return safe_local_continuation("sdo_outcome_malformed")
    elif dispatch_mode != "continue_local" or replan_required is not False:
        return safe_local_continuation("sdo_outcome_malformed")

    outcome = {
        "selected_action_id": selected_action,
        "base_selected_action_id": base_action,
        "decision": decision,
        "dispatch_mode": dispatch_mode,
        "action_changed": action_changed,
        "replan_required": replan_required,
        "model": NATURAL_MODEL,
        "reasoning_effort": NATURAL_EFFORT,
        "service_tier_preference": NATURAL_TIER,
    }
    return {
        "claim_status": "admitted",
        "claim_withheld_reason": None,
        "safe_local_continuation": False,
        "selection_reason": NATURAL_SELECTION_REASON,
        "provider": NATURAL_PROVIDER,
        "model": NATURAL_MODEL,
        "effort": NATURAL_EFFORT,
        "tier": NATURAL_TIER,
        "selected_action_id": selected_action,
        "base_selected_action_id": base_action,
        "decision": decision,
        "dispatch_mode": dispatch_mode,
        "replan_required": replan_required,
        "model_route": {
            "provider": NATURAL_PROVIDER,
            "model": NATURAL_MODEL,
            "reasoning_effort": NATURAL_EFFORT,
            "service_tier_preference": NATURAL_TIER,
        },
        "receipt_digest": receipt_digest,
        "binding_digest": _binding_digest(safe_binding),
        "claim": {
            "receipt_digest": receipt_digest,
            "expires_at": expires_at,
            "binding": safe_binding,
            "outcome": outcome,
        },
        "outcome": outcome,
    }


def public_decision_projection(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Strip the private claim binding while retaining route/result facts."""

    admitted = decision.get("claim_status") == "admitted"
    selected_action = (
        _optional_identifier(decision.get("selected_action_id"))
        if admitted
        else None
    )
    base_action = (
        _optional_identifier(decision.get("base_selected_action_id"))
        if admitted
        else None
    )
    if selected_action is _INVALID:
        selected_action = None
    if base_action is _INVALID:
        base_action = None
    claim_reason = decision.get("claim_withheld_reason")
    if not isinstance(claim_reason, str) or _SAFE_ID_RE.fullmatch(claim_reason) is None:
        claim_reason = "sdo_unavailable" if not admitted else None
    digest = decision.get("receipt_digest")
    binding_digest = decision.get("binding_digest")
    return {
        "claim_status": "admitted" if admitted else "withheld",
        "claim_withheld_reason": claim_reason,
        "safe_local_continuation": not admitted,
        "selection_reason": NATURAL_SELECTION_REASON if admitted else None,
        "provider": NATURAL_PROVIDER if admitted else None,
        "model": NATURAL_MODEL if admitted else None,
        "effort": NATURAL_EFFORT if admitted else None,
        "tier": NATURAL_TIER if admitted else None,
        "selected_action_id": selected_action,
        "base_selected_action_id": base_action,
        "decision": (
            decision.get("decision")
            if admitted and decision.get("decision") in {"CONTINUE_LOCAL", "REPLAN_NOW"}
            else "CONTINUE_LOCAL"
        ),
        "dispatch_mode": (
            decision.get("dispatch_mode")
            if admitted and decision.get("dispatch_mode") in {"continue_local", "replan_local"}
            else "safe_local_baseline"
        ),
        "replan_required": admitted and decision.get("replan_required") is True,
        "model_route_consumed": admitted,
        "receipt_digest": (
            digest
            if admitted and isinstance(digest, str) and _DIGEST_RE.fullmatch(digest)
            else None
        ),
        "binding_digest": (
            binding_digest
            if admitted
            and isinstance(binding_digest, str)
            and _DIGEST_RE.fullmatch(binding_digest)
            else None
        ),
    }


def apply_sdo_decision_to_session(
    session: dict[str, Any], decision: Mapping[str, Any]
) -> None:
    """Bind an admitted route before lazy agent construction."""

    session["_orch_sdo_decision"] = public_decision_projection(decision)
    if decision.get("claim_status") != "admitted":
        return
    session["_orch_model_route"] = dict(decision["model_route"])
    session["model_override"] = {
        "model": NATURAL_MODEL,
        "provider": NATURAL_PROVIDER,
    }
    session["create_reasoning_override"] = {
        "enabled": True,
        "effort": NATURAL_EFFORT,
    }
    # The native agent calls the priority service tier "priority"; the
    # external SDO projection remains the truthful "fast".
    session["create_service_tier_override"] = "priority"


__all__ = [
    "NATURAL_DECISION_FIELDS",
    "NATURAL_EFFORT",
    "NATURAL_MODEL",
    "NATURAL_PROVIDER",
    "NATURAL_SELECTION_REASON",
    "NATURAL_TIER",
    "NATURAL_TRANSITION",
    "apply_sdo_decision_to_session",
    "consume_sdo_decision",
    "public_decision_projection",
    "project_natural_producer_result",
    "safe_local_continuation",
    "unavailable_decision",
]
