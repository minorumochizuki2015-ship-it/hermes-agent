"""Pure FP-2 SDO decision consumer.

Producer execution, source discovery, signatures, trust roots, distribution,
and adoption belong to later lifecycle slices and are intentionally absent.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping
from typing import Any, Final


NATURAL_SELECTION_REASON: Final = "natural_delegated_nontrivial"
NATURAL_PROVIDER: Final = "openai-codex"
NATURAL_MODEL: Final = "gpt-5.6-luna"
NATURAL_EFFORT: Final = "max"
NATURAL_TIER: Final = "fast"

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
    if isinstance(context, Mapping):
        if safe_binding["operation_id"] != context.get("operation_id"):
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
    action_changed = raw_decision.get(
        "action_changed",
        selected_action is not None
        and base_action is not None
        and selected_action != base_action,
    )
    replan_required = raw_decision.get("replan_required", decision == "REPLAN_NOW")
    if type(action_changed) is not bool or type(replan_required) is not bool:
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
    "NATURAL_EFFORT",
    "NATURAL_MODEL",
    "NATURAL_PROVIDER",
    "NATURAL_SELECTION_REASON",
    "NATURAL_TIER",
    "apply_sdo_decision_to_session",
    "consume_sdo_decision",
    "public_decision_projection",
    "safe_local_continuation",
    "unavailable_decision",
]
