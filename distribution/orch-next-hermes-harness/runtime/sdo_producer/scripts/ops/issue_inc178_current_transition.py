#!/usr/bin/env python3
"""Issue a current INC-178 transition and separate live context from current facts."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mk_whole_goal_control import (
    ACTION_CLASS_BY_OPERATION_TYPE,
    CMD_EPOCH_ACTOR_FIELDS,
    CMD_PENDING_RETURN_FIELDS,
    CMD_RELEASE_STATES,
    CMD_RETURN_PATHS,
    CONTRACT_VERSION,
    EXEMPT_WORK_CLASSES,
    LIVE_CONTEXT_VERSION,
    NONFIRE_REASONS,
    REQUIRED_NON_CLAIMS,
    TRANSITIONS,
    _derived_action_class,
    _parse_time,
    _threshold_reasons,
    apply_cmd_epoch_request,
    check_contract,
    normalize_cmd_epoch_state,
    validate_cmd_epoch_actor_fields,
    validate_cmd_epoch_request,
)
import mk733j_decision_os as decision_os


INPUT_VERSION = "inc178_current_observation.v1"
INPUT_FIELDS = {
    "schema_version", "observation_source", "work_class", "transition", "binding",
    "whole_goal", "progress_deltas", "time_accounting", "counters", "skill_firing",
    "audit_integration", "gate_burden", "recent_activity", "candidates",
    "before_selected_action_id", "terminal_continuation",
}
OPTIONAL_INPUT_FIELDS = {
    "cmd_epoch_request", "return_event", "protected_transition_request", "dispatch_request", "sdo_intake",
    "sdo_downstream_result", "sdo_decision_receipt", "sdo_route_receipt",
}
SDO_INTAKE_FIELDS = {
    "project_id", "brain", "pms", "odg", "fable5", "accepted_knowledge",
    "brain_decisions", "brain_identities", "fable5_records",
}
SDO_LAYER_NAMES = ("brain", "pms", "odg", "fable5")
SDO_LAYER_FIELDS = {"status"}
SDO_LAYER_STATUSES = {
    "unavailable", "available", "current", "admitted", "stale", "unaccepted",
}
SDO_KNOWLEDGE_FIELDS = {
    "knowledge_id", "project_id", "action_id", "status", "source_ref",
}
SDO_KNOWLEDGE_STATUSES = {"accepted", "stale", "unaccepted"}
SDO_BRAIN_DECISION_FIELDS = {
    "decision_id", "project_id", "action_id", "status", "source_ref", "expires_at",
}
SDO_BRAIN_DECISION_STATUSES = {"current", "stale", "rejected", "deferred"}
SDO_BRAIN_IDENTITY_FIELDS = {
    "provider", "brain_id", "project_id", "repo_id", "canonical_file", "brain_root", "source_ref",
}
SDO_BRAIN_PROVIDER = "mindmux"
SDO_BRAIN_REPO_ID = "maestro-kernel"
SDO_BRAIN_CANONICAL_FILE = "BRAIN.md"
SDO_BRAIN_ROOT = "brain"
SDO_BRAIN_SOURCE_REF = "brain-cli:brain/pages/sdo-synaptic-decision-os.md#sdo-synaptic-decision-os"
SDO_FABLE_RECORD_FIELDS = {
    "record_id", "project_id", "action_id", "disposition", "source_ref", "applicability",
    "selected_alternative_summary", "rejected_alternative_summary", "user_capability_delta",
    "blocker_delta", "threshold_provenance", "expires_at", "recheck_at", "rollback",
    "retirement", "operation_outcome",
}
SDO_FABLE_DISPOSITIONS = {"admitted", "rejected", "deferred"}
SDO_ACCEPTED_ACTION_BONUS = 2
SDO_DISPLAY_IDENTITY = "ORCH Synaptic Decision OS (SDO)"
SDO_HISTORICAL_ALIASES = ("Fable5-OS", "Decision OS", "ODS")
SDO_RECEIPT_VERSION = "sdo_decision_receipt.v1"
SDO_CLAIM_TTL_SECONDS = 300
SDO_NO_CAPABILITY_DELTA_REPLAN_MS = 5_400_000
SDO_RECEIPT_ENVELOPE_BINDING_FIELDS = {
    "receipt_id", "receipt_digest", "receipt_consumed", "consumed_by",
}
SDO_RECEIPT_CONTENT_FIELDS = {
    "schema_version", "display_identity", "historical_aliases", "consulted_refs",
    "repo_facts", "project_id", "repo_candidate_set", "layer_status",
    "base_selected_action_id", "selected_action_id", "selected_alternatives",
    "rejected_alternatives", "capability_delta", "blocker_delta", "model_route",
    "cost_telemetry", "cumulative_work", "decision", "safe_local_continuation",
    "replan_reason", "thresholds", "protected_transition", "expiry", "rollback",
    "nonclaims", "owner", "source_consumer", "selected_action_operation_type",
    "next_operation", "evaluated_at", "receipt_expiry_is_authority",
    "support_work_progress_credit",
}
SDO_RECEIPT_ENVELOPE_FIELDS = (
    SDO_RECEIPT_CONTENT_FIELDS | SDO_RECEIPT_ENVELOPE_BINDING_FIELDS
)
SDO_RECEIPT_ENVELOPE_SCHEMA_BLOCKER = (
    "BLOCKED_FOR_INC178_SDO_RECEIPT_ENVELOPE_SCHEMA_INVALID"
)
SDO_RECEIPT_CONSUMPTION_MISMATCH = (
    "BLOCKED_FOR_INC178_SDO_RECEIPT_CONSUMPTION_MISMATCH"
)
SDO_RECEIPT_DIGEST_MISMATCH = "BLOCKED_FOR_INC178_SDO_RECEIPT_DIGEST_MISMATCH"
SDO_RECEIPT_PENDING_RETURN_MISSING = (
    "BLOCKED_FOR_INC178_SDO_RECEIPT_PENDING_RETURN_MISSING"
)
SDO_RECEIPT_PENDING_RETURN_INVALID = (
    "BLOCKED_FOR_INC178_SDO_RECEIPT_PENDING_RETURN_INVALID"
)
SDO_RECEIPTLESS_ACTION_CHANGE = "BLOCKED_FOR_INC178_RECEIPTLESS_ACTION_CHANGE"
CMD_STATE_LEGACY_SDO_UPGRADE = "CMD_STATE_LEGACY_SDO_UPGRADE"
CMD_STATE_TRUSTED_DEBTS = {
    "", "CMD_STATE_INITIALIZED", "CMD_STATE_LINEAR_HEAD_ADVANCE",
    CMD_STATE_LEGACY_SDO_UPGRADE,
}
CMD_STATE_BOOTSTRAP_PENDING = "legacy_upgrade_pending_first_consumed_receipt"
CMD_STATE_BOOTSTRAP_PRESERVED = "legacy_upgrade_preserved_next_operation"
CMD_STATE_BOOTSTRAP_BOUND = "receipt_bound"
CMD_STATE_CHAIN_JOURNAL_MISSING = "BLOCKED_FOR_INC178_CMD_STATE_CHAIN_JOURNAL_MISSING"
CMD_STATE_CHAIN_INVALID = "BLOCKED_FOR_INC178_CMD_STATE_CHAIN_INVALID"
CMD_STATE_CHAIN_JOURNAL_MISMATCH = (
    "BLOCKED_FOR_INC178_CMD_STATE_CHAIN_JOURNAL_MISMATCH"
)
SDO_DOWNSTREAM_RESULT_FIELDS = {
    "consumer", "consumed", "project_id", "action_changed", "decision",
    "model_route_changed", "receipt_digest",
}
SDO_RECEIPT_UNCONSUMED_ACTION_CHANGE = (
    "BLOCKED_FOR_INC178_UNCONSUMED_RECEIPT_ACTION_CHANGE"
)
SDO_ROUTE_RECEIPT_VERSION = "sdo_route_decision_receipt.v1"
SDO_ROUTE_RECEIPT_FIELDS = {
    "schema_version", "action_id", "route_result_digest",
    "protected_classification", "authority_result", "target_binding",
}
SDO_ROUTE_CLASSIFICATION_FIELDS = {"action_id", "protected"}
SDO_ROUTE_RECEIPT_SCHEMA_BLOCKER = (
    "BLOCKED_FOR_INC178_SDO_ROUTE_RECEIPT_SCHEMA_INVALID"
)
SDO_PROTECTED_ACTION_WITHOUT_ROUTE_AUTHORITY = (
    "BLOCKED_FOR_INC178_PROTECTED_ACTION_WITHOUT_ROUTE_AUTHORITY"
)
SDO_ROUTE_RECEIPT_BINDING_INVALID = (
    "BLOCKED_FOR_INC178_SDO_ROUTE_RECEIPT_BINDING_INVALID"
)
PROTECTED_TRANSITION_FIELDS = {
    "repository_id", "revision", "operation", "target", "protected_asset", "hazard",
    "owner", "rollback", "expires_at",
}
DISPATCH_REQUEST_FIELDS = {
    "audit_requested", "audit_max_findings", "audit_stop_after_first_p1",
    "cause_changing_audit_corrections", "broad_suite_requested", "suite_input_digest",
    "prior_suite_input_digest", "signed_fixture_or_routing_rebind_requested", "source_revision",
    "implementation_model", "reasoning_effort", "worktree", "owner",
    "selected_action_binding", "write_set_kind", "precision_required", "deterministic_mechanical",
    "bounded_single_repo", "cost_telemetry", "durable_holder", "support_lanes_active",
    "primary_state", "goal_incomplete", "safe_disjoint_work", "merge_order_conflict",
    "disjoint_branch", "claim_check_failure", "unavailable_skill", "protected_transition_requested",
    "telemetry_state", "correction_count", "natural_cohort_complete", "worker_pace",
}
WORKER_PACE_FIELDS = {
    "profile", "grounding_review_ms", "material_delta_review_ms",
    "no_delta_replan_review_ms", "expected_completion_review_ms",
    "elapsed_only_stop_allowed", "dispatch_response_checkpoint_used_as_worker_deadline",
}
COST_TELEMETRY_FIELDS = {
    "raw_input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
    "raw_token_volume", "discount_eligibility", "billing_telemetry_authoritative",
    "effective_billed_cost", "elapsed_ms", "first_pass_result", "rework_count",
    "scope_deviation_count", "normal_user_capability_delta",
}
DISPATCH_REQUEST_OPTIONAL_FIELDS = {"cost_telemetry", "worker_pace"}
DISPATCH_REQUEST_REQUIRED_FIELDS = DISPATCH_REQUEST_FIELDS - DISPATCH_REQUEST_OPTIONAL_FIELDS
INC191_WRITE_SET_KIND = "abstract_action_binding_not_file_authority"
CMD_STATE_VERSION = "inc178_cmd_runtime_state.v3"
CMD_STATE_CORE_FIELDS = {
    "schema_version", "sequence", "predecessor_state_digest",
    "cause_changing_correction_count", "total_cause_changing_correction_count",
    "lineage_correction_counts", "last_capability_delta_at", "last_event_id",
    "last_event_sequence", "last_observation_digest", "total_attempt_count",
    "seen_capability_transition_ids", "natural_transition_count", "natural_task_classes",
    "same_class_user_correction_count", "repository_head", "state_digest",
}
LEGACY_EPOCH_STATE_FIELDS = {
    "cmd_epoch_id", "previous_epoch_id", "cmd_release_state", "checkpoint_sha256", "pending_returns",
}
CMD_EPOCH_STATE_FIELDS = LEGACY_EPOCH_STATE_FIELDS | CMD_EPOCH_ACTOR_FIELDS
LEGACY_SDO_STATE_FIELDS = {
    "next_operation", "sdo_decision", "receipt_digest", "receipt_consumed",
}
CMD_SDO_STATE_FIELDS = LEGACY_SDO_STATE_FIELDS | {
    "bootstrap_state", "predecessor_chain_digest", "chain_digest",
    "transition_journal_path", "transition_journal_digest",
}
CMD_STATE_FIELDS = CMD_STATE_CORE_FIELDS | CMD_EPOCH_STATE_FIELDS | CMD_SDO_STATE_FIELDS
LEGACY_CMD_STATE_FIELDS = CMD_STATE_CORE_FIELDS
LEGACY_SDO_CMD_STATE_FIELDS = CMD_STATE_CORE_FIELDS | LEGACY_SDO_STATE_FIELDS
LEGACY_EPOCH_CMD_STATE_FIELDS = CMD_STATE_CORE_FIELDS | LEGACY_EPOCH_STATE_FIELDS
LEGACY_FULL_CMD_STATE_FIELDS = (
    CMD_STATE_CORE_FIELDS | LEGACY_EPOCH_STATE_FIELDS | CMD_SDO_STATE_FIELDS
)
CMD_STATE_FIELD_SETS = (
    LEGACY_CMD_STATE_FIELDS,
    LEGACY_SDO_CMD_STATE_FIELDS,
    LEGACY_EPOCH_CMD_STATE_FIELDS,
    LEGACY_FULL_CMD_STATE_FIELDS,
    CMD_STATE_FIELDS,
)
SDO_PENDING_RETURN_FIELDS = CMD_PENDING_RETURN_FIELDS | {"receipt_digest"}
CMD_REPLAN_AFTER_CORRECTIONS = 2
CMD_REPLAN_AFTER_NO_DELTA_MINUTES = 90
CMD_MAX_TASK_LINEAGES = 64
CMD_MAX_CAPABILITY_TRANSITIONS = 256
CMD_EPOCH_OUTSIDE_LOCKED_STATE = "BLOCKED_FOR_INC178_EPOCH_OUTSIDE_LOCKED_STATE"
PRODUCER_SOURCE_ROOT = Path(__file__).resolve().parents[2]
TERMINAL_CONTINUATION_FIELDS = {
    "terminal_result_consumed", "protected_adoption_held", "bounded_local_repair_dispatchable",
    "primary_state", "quiet_closeout_requested", "current_transition_checker_invoked",
    "selected_action_result_consumed", "control_dispatch_sent", "control_dispatch_mode",
    "dispatch_target_thread_id", "target_readback_received", "target_readback_marker",
    "progress_blocker",
}
TERMINAL_PROGRESS_BLOCKER_FIELDS = {
    "present", "blocker_id", "summary", "owner", "unblock_condition",
}
THRESHOLDS = {
    "elapsed_zero_delta_ms": 3_600_000,
    "max_chained_implementation_blocks": 3,
    "zero_delta_slice_limit": 3,
    "distinct_causal_blocker_limit": 3,
    "protected_mutation_without_milestone_limit": 2,
    "estimate_error_ratio_limit": 2.0,
    "same_warning_replan_count": 2,
    "return_decide_dispatch_checkpoint_ms": 180_000,
}

INC191_IMPLEMENTATION_MODEL = "gpt-5.6-luna"
INC191_REASONING_EFFORT = "high"
INC191_MAX_REASONING_EFFORT = "max"
INC191_PRIMARY_ACTIVE_STATES = {"active", "waiting_dependency", "protected_blocked"}
INC191_LUNA_STANDARD_SERVICE_TIER = "standard"
INC191_LUNA_FAST_SERVICE_TIER = "fast"
INC191_FAST_MODE_SPEED_MULTIPLIER = 1.5
INC191_FAST_MODE_CREDIT_MULTIPLIER = 2.5
INC191_HISTORICAL_LUNA_REDUCTION_FACTOR = 0.20
INC191_CURRENT_LUNA_STANDARD_CREDIT_RATE = {
    "input_per_1m": 5.0,
    "cached_input_per_1m": 0.5,
    "output_per_1m": 30.0,
}
INC191_LUNA_RATE_CARD_SOURCE = "https://learn.chatgpt.com/docs/pricing"
INC191_LUNA_SPEED_SOURCE = "https://learn.chatgpt.com/docs/agent-configuration/speed"
INC191_LUNA_RATE_CARD_CHECKED_AT = "2026-08-11"
CAUSE_CHANGING_CORRECTION_CLASSES = {
    "continuity_error_idle_dispatch",
    "best_action_model_gate_evidence_time",
    "ux_iphone_outcome",
    "wrong_target_owner_routing",
    "strategy_or_hypothesis_change",
    "target_or_write_set_change",
    "authority_boundary_change",
}
NON_CAUSE_CORRECTION_CLASSES = {
    "clerical_patch_format",
    "test_syntax",
    "path_resolution",
    "receipt_metadata",
    "grounding_no_diff",
}


class IssueError(ValueError):
    """Fail closed before an issuer writes a transition."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_int(value: Any, *, positive: bool = False) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= (1 if positive else 0)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonical_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _hex_digest(value: Any, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_cmd_epoch_state_fields(value: Any) -> bool:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("cmd_epoch_id"), str)
        or not isinstance(value.get("previous_epoch_id"), str)
        or value.get("cmd_release_state") not in CMD_RELEASE_STATES
        or not _hex_digest(value.get("checkpoint_sha256"))
        or not isinstance(value.get("pending_returns"), list)
    ):
        return False
    return_entries: set[str] = set()
    for entry in value["pending_returns"]:
        if (
            not isinstance(entry, dict)
            or set(entry) not in (CMD_PENDING_RETURN_FIELDS, SDO_PENDING_RETURN_FIELDS)
            or not _nonempty(entry.get("return_id"))
            or entry["return_id"] in return_entries
            or not _hex_digest(entry.get("source_head"), length=40)
            or not isinstance(entry.get("consumed"), bool)
            or not isinstance(entry.get("consumed_by"), str)
            or (entry["consumed"] and not _nonempty(entry["consumed_by"]))
            or (not entry["consumed"] and entry["consumed_by"] != "")
            or entry.get("return_path") not in CMD_RETURN_PATHS
            or (
                set(entry) == SDO_PENDING_RETURN_FIELDS
                and not _hex_digest(entry.get("receipt_digest"))
            )
        ):
            return False
        return_entries.add(entry["return_id"])
    actor_fields_present = set(value) & CMD_EPOCH_ACTOR_FIELDS
    if actor_fields_present and (
        actor_fields_present != CMD_EPOCH_ACTOR_FIELDS
        or not validate_cmd_epoch_actor_fields({
            field: value.get(field) for field in CMD_EPOCH_ACTOR_FIELDS
        })
    ):
        return False
    return True


def _selected_action_binding(selected_action: dict[str, Any]) -> str:
    """Bind a bounded receipt to the selected observation action, not a file list."""
    return f"selected_action:{selected_action['action_id']}"


def _sdo_receipt_next_operation(receipt: dict[str, Any]) -> str:
    """Read the route engine's explicitly bound operation."""
    value = receipt.get("next_operation")
    return value.strip() if _nonempty(value) else ""


def _sdo_receipt_decision(receipt: dict[str, Any], next_operation: str) -> str:
    decision = receipt.get("decision")
    return decision.strip() if _nonempty(decision) else next_operation


def _sdo_receipt_content(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key not in SDO_RECEIPT_ENVELOPE_BINDING_FIELDS
    }


def _sdo_receipt_digest(receipt: dict[str, Any]) -> str:
    """Recompute the digest from receipt content, excluding binding metadata."""
    return _canonical_digest(_sdo_receipt_content(receipt))


def _seal_sdo_receipt(
    receipt: dict[str, Any],
    *,
    receipt_consumed: bool,
    consumed_by: str,
) -> dict[str, Any]:
    sealed = {
        **receipt,
        "receipt_id": "",
        "receipt_digest": "",
        "receipt_consumed": receipt_consumed,
        "consumed_by": consumed_by if receipt_consumed else "",
    }
    computed = _sdo_receipt_digest(sealed)
    sealed["receipt_id"] = computed
    sealed["receipt_digest"] = computed
    return sealed


def _validate_sdo_decision_receipt(value: Any) -> None:
    """Accept only the closed route receipt envelope owned by the consumer."""
    if (
        not isinstance(value, dict)
        or set(value) != SDO_RECEIPT_ENVELOPE_FIELDS
        or value.get("schema_version") != SDO_RECEIPT_VERSION
        or not _nonempty(value.get("decision"))
        or not _sdo_receipt_next_operation(value)
        or not _hex_digest(value.get("receipt_id"))
        or not _hex_digest(value.get("receipt_digest"))
        or value.get("receipt_id") != value.get("receipt_digest")
        or not isinstance(value.get("receipt_consumed"), bool)
        or not isinstance(value.get("consumed_by"), str)
        or (
            value.get("receipt_consumed") is True
            and not _nonempty(value.get("consumed_by"))
        )
        or (
            value.get("receipt_consumed") is False
            and value.get("consumed_by") != ""
        )
    ):
        raise IssueError(SDO_RECEIPT_ENVELOPE_SCHEMA_BLOCKER)
    if _sdo_receipt_digest(value) != value["receipt_digest"]:
        raise IssueError(SDO_RECEIPT_DIGEST_MISMATCH)


def _validate_sdo_route_receipt(value: Any) -> None:
    """Accept only the closed route-engine authority receipt envelope."""
    classification = value.get("protected_classification") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != SDO_ROUTE_RECEIPT_FIELDS
        or value.get("schema_version") != SDO_ROUTE_RECEIPT_VERSION
        or not _nonempty(value.get("action_id"))
        or not _hex_digest(value.get("route_result_digest"))
        or not isinstance(classification, dict)
        or set(classification) != SDO_ROUTE_CLASSIFICATION_FIELDS
        or classification.get("action_id") != value.get("action_id")
        or not isinstance(classification.get("protected"), bool)
        or not isinstance(value.get("authority_result"), dict)
        or not isinstance(value.get("target_binding"), dict)
    ):
        raise IssueError(SDO_ROUTE_RECEIPT_SCHEMA_BLOCKER)


def _sdo_route_candidate(
    next_operation: str, selected_action: dict[str, Any]
) -> dict[str, Any]:
    candidate = {"action_id": next_operation}
    if next_operation == selected_action.get("action_id"):
        for key in ("repository_class", "task_class", "operation_class"):
            if key in selected_action:
                candidate[key] = selected_action[key]
    return candidate


def _sdo_route_authority_binding(
    base_dir: Path,
    doc: dict[str, Any],
    *,
    next_operation: str,
    selected_action: dict[str, Any],
) -> dict[str, Any]:
    """Require an authority-bound route result only for trusted protected work."""
    if not _nonempty(next_operation):
        return {
            "required": False,
            "protected": False,
            "verified": False,
            "action_id": "",
            "route_result_digest": "",
        }

    candidate = _sdo_route_candidate(next_operation, selected_action)
    trusted_state, trusted_blocks = decision_os._sdo_trusted_repository_state({
        "base_dir": str(base_dir.resolve()),
    })
    protected = decision_os._sdo_trusted_action_protected(
        candidate, next_operation, trusted_state
    )
    if trusted_blocks or protected is None:
        raise IssueError(SDO_PROTECTED_ACTION_WITHOUT_ROUTE_AUTHORITY)
    if protected is False:
        return {
            "required": False,
            "protected": False,
            "verified": False,
            "action_id": next_operation,
            "route_result_digest": "",
        }

    route_receipt = doc.get("sdo_route_receipt")
    if route_receipt is None:
        raise IssueError(SDO_PROTECTED_ACTION_WITHOUT_ROUTE_AUTHORITY)
    _validate_sdo_route_receipt(route_receipt)
    if (
        route_receipt["action_id"] != next_operation
        or route_receipt["protected_classification"]
        != {"action_id": next_operation, "protected": True}
    ):
        raise IssueError(SDO_ROUTE_RECEIPT_BINDING_INVALID)

    route_payload = {
        "target_binding": route_receipt["target_binding"],
    }
    if not decision_os._sdo_authority_result_valid(
        route_receipt["authority_result"],
        next_operation,
        route_payload,
        trusted_state.get("base_dir") or decision_os.REPO,
    ):
        raise IssueError(SDO_ROUTE_RECEIPT_BINDING_INVALID)

    route_result = decision_os.sdo_route({
        "sdo_route": {
            "base_dir": str(base_dir.resolve()),
            "repository_candidates": [candidate],
            "facts": {
                "candidate_actions": [{
                    "action_id": next_operation,
                    "protected": True,
                }],
            },
            "target_binding": route_receipt["target_binding"],
            "authority_results": [route_receipt["authority_result"]],
        },
    })
    action_route = route_result.get("action_routes", {}).get(next_operation)
    if (
        route_result.get("route") != "allow"
        or route_result.get("selected_action") != next_operation
        or not isinstance(action_route, dict)
        or action_route.get("route") != "allow"
        or action_route.get("protected") is not True
        or action_route.get("blockers")
        or route_result.get("blockers")
        or decision_os.digest(route_result) != route_receipt["route_result_digest"]
    ):
        raise IssueError(SDO_ROUTE_RECEIPT_BINDING_INVALID)
    return {
        "required": True,
        "protected": True,
        "verified": True,
        "action_id": next_operation,
        "route_result_digest": route_receipt["route_result_digest"],
    }


def _sdo_pending_return_entry(
    state: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    pending_returns = state.get("pending_returns")
    if not isinstance(pending_returns, list):
        raise IssueError(SDO_RECEIPT_PENDING_RETURN_INVALID)
    entry = next(
        (
            item for item in pending_returns
            if isinstance(item, dict)
            and item.get("return_id") == receipt.get("receipt_id")
        ),
        None,
    )
    if entry is None:
        raise IssueError(SDO_RECEIPT_PENDING_RETURN_MISSING)
    if (
        set(entry) != SDO_PENDING_RETURN_FIELDS
        or not _hex_digest(entry.get("receipt_digest"))
        or entry.get("receipt_digest") != receipt.get("receipt_digest")
    ):
        raise IssueError(SDO_RECEIPT_PENDING_RETURN_INVALID)
    return entry


def _sdo_receipt_binding(
    state: dict[str, Any],
    doc: dict[str, Any],
    selected_action: dict[str, Any],
) -> tuple[str, str, str, bool, dict[str, Any]]:
    """Bind the persisted next operation only at the receipt-consumption seam."""
    current = state.get("next_operation")
    current = current.strip() if _nonempty(current) else ""
    persisted_digest = state.get("receipt_digest", "")
    persisted_digest = persisted_digest.strip() if _nonempty(persisted_digest) else ""
    persisted_consumed = state.get("receipt_consumed") is True
    persisted_decision = state.get("sdo_decision")
    persisted_decision = (
        persisted_decision.strip() if _nonempty(persisted_decision) else ""
    )
    receipt = doc.get("sdo_decision_receipt")
    if receipt is None:
        if current and selected_action["action_id"] != current:
            raise IssueError(SDO_RECEIPTLESS_ACTION_CHANGE)
        return (
            current,
            persisted_decision,
            persisted_digest,
            persisted_consumed,
            {
                "receipt_present": False,
                "receipt_consumed": False,
                "receipt_digest": "",
                "next_operation_before": current,
                "next_operation_after": current,
                "action_changed": False,
                "diagnostic": "SDO_RECEIPT_NOT_PRESENT_PERSISTED_ACTION_RETAINED",
                "bootstrap_state": state.get("bootstrap_state", ""),
            },
        )
    _validate_sdo_decision_receipt(receipt)
    receipt_operation = _sdo_receipt_next_operation(receipt)
    receipt_digest = _sdo_receipt_digest(receipt)
    receipt_decision = _sdo_receipt_decision(receipt, receipt_operation)
    pending_entry = _sdo_pending_return_entry(state, receipt)
    verified_consumed = (
        pending_entry.get("consumed") is True
        and _nonempty(pending_entry.get("consumed_by"))
        and pending_entry.get("consumed_by") == receipt.get("consumed_by")
    )
    if (receipt.get("receipt_consumed") is True) != verified_consumed:
        raise IssueError(SDO_RECEIPT_CONSUMPTION_MISMATCH)
    if not verified_consumed:
        if receipt_operation != current:
            raise IssueError(SDO_RECEIPT_UNCONSUMED_ACTION_CHANGE)
        return (
            current,
            persisted_decision,
            persisted_digest,
            persisted_consumed,
            {
                "receipt_present": True,
                "receipt_consumed": False,
                "receipt_digest": receipt_digest,
                "next_operation_before": current,
                "next_operation_after": current,
                "action_changed": False,
                "diagnostic": "SDO_RECEIPT_VISIBLE_UNCONSUMED_ACTION_UNCHANGED",
                "receipt_id": receipt["receipt_id"],
                "consumed_by": "",
                "verified_consumed": False,
            },
        )
    return (
        receipt_operation,
        receipt_decision,
        receipt_digest,
        True,
        {
            "receipt_present": True,
            "receipt_consumed": True,
            "receipt_digest": receipt_digest,
            "receipt_id": receipt["receipt_id"],
            "consumed_by": pending_entry["consumed_by"],
            "verified_consumed": True,
            "next_operation_before": current,
            "next_operation_after": receipt_operation,
            "action_changed": receipt_operation != current,
            "diagnostic": (
                "SDO_RECEIPT_CONSUMED_ACTION_CHANGED"
                if receipt_operation != current
                else "SDO_RECEIPT_CONSUMED_ACTION_UNCHANGED"
            ),
        },
    )


def _valid_cost_telemetry(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != COST_TELEMETRY_FIELDS:
        return False
    if any(
        not _is_int(value.get(field))
        for field in (
            "raw_input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
            "elapsed_ms", "rework_count", "scope_deviation_count",
        )
    ):
        return False
    if value.get("raw_token_volume") not in {"low", "medium", "high", "unknown"}:
        return False
    if not isinstance(value.get("discount_eligibility"), bool):
        return False
    authoritative = value.get("billing_telemetry_authoritative")
    if not isinstance(authoritative, bool):
        return False
    if not authoritative and value.get("effective_billed_cost") != "UNKNOWN":
        return False
    if authoritative and value.get("effective_billed_cost") in {None, ""}:
        return False
    if value.get("first_pass_result") not in {"pass", "fail", "unknown"}:
        return False
    return value.get("normal_user_capability_delta") is not None


def _normalize_cost_telemetry(value: Any) -> tuple[dict[str, Any], bool]:
    """Retain caller cost fields without granting caller billing authority."""
    reported = value if isinstance(value, dict) else {}
    integer_fields = (
        "raw_input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
        "elapsed_ms", "rework_count", "scope_deviation_count",
    )
    normalized = {
        field: reported.get(field) if _is_int(reported.get(field)) else None
        for field in integer_fields
    }
    normalized.update({
        "raw_token_volume": (
            reported.get("raw_token_volume")
            if reported.get("raw_token_volume") in {"low", "medium", "high", "unknown"}
            else "unknown"
        ),
        "discount_eligibility": (
            reported.get("discount_eligibility")
            if isinstance(reported.get("discount_eligibility"), bool)
            else None
        ),
        "billing_telemetry_authoritative": False,
        "effective_billed_cost": "UNKNOWN",
        "first_pass_result": (
            reported.get("first_pass_result")
            if reported.get("first_pass_result") in {"pass", "fail", "unknown"}
            else "unknown"
        ),
        "normal_user_capability_delta": reported.get("normal_user_capability_delta"),
    })
    return normalized, True


def _luna_fast_mode_policy(
    discount_eligibility: Any,
    selection_reason: str,
) -> dict[str, Any]:
    """Select Luna speed adaptively and keep cost baselines unambiguous.

    Fast is a latency preference for the bounded Luna-Max precision route. Luna
    High remains Standard so independent/mechanical work can use the shared
    allowance efficiently. A service-tier receipt is still required before
    claiming the native transport applied the preference.

    Current-Luna and historical pre-reduction comparisons are intentionally
    separate. An 80 percent reduction leaves a 0.20 factor; it is never encoded
    as 0.80. Neither comparison promotes caller telemetry into a billing claim.
    """
    fast_selected = selection_reason in {
        "precision_required_single_repo",
        "natural_delegated_nontrivial",
    }
    tier_multiplier = INC191_FAST_MODE_CREDIT_MULTIPLIER if fast_selected else 1.0
    service_tier = (
        INC191_LUNA_FAST_SERVICE_TIER
        if fast_selected
        else INC191_LUNA_STANDARD_SERVICE_TIER
    )
    selection_explanation = (
        "luna_max_precision_latency_priority"
        if fast_selected
        else "luna_high_credit_efficient_parallel_default"
    )
    projected_fast_rate = {
        field: round(value * INC191_FAST_MODE_CREDIT_MULTIPLIER, 3)
        for field, value in INC191_CURRENT_LUNA_STANDARD_CREDIT_RATE.items()
    }
    selected_rate = (
        projected_fast_rate
        if fast_selected
        else dict(INC191_CURRENT_LUNA_STANDARD_CREDIT_RATE)
    )

    if discount_eligibility is True:
        historical_factor: float | str = INC191_HISTORICAL_LUNA_REDUCTION_FACTOR
        historical_selected_multiplier: float | str = round(
            INC191_HISTORICAL_LUNA_REDUCTION_FACTOR * tier_multiplier,
            2,
        )
        historical_formula = (
            "pre_reduction_luna_standard*0.20*2.50"
            if fast_selected
            else "pre_reduction_luna_standard*0.20*1.00"
        )
        estimate_source = "official_current_rate_card_plus_user_historical_comparison"
    else:
        historical_factor = "UNKNOWN"
        historical_selected_multiplier = "UNKNOWN"
        historical_formula = "UNKNOWN"
        estimate_source = "official_current_rate_card_historical_comparison_unavailable"

    current_formula = (
        "current_luna_standard*2.50"
        if fast_selected
        else "current_luna_standard*1.00"
    )
    return {
        "service_tier_preference": service_tier,
        "service_tier_runtime_verified": False,
        "fast_mode_claim_withheld": True,
        "fast_mode_selected": fast_selected,
        "fast_mode_selection_reason": selection_explanation,
        "fast_mode_speed_multiplier": INC191_FAST_MODE_SPEED_MULTIPLIER,
        "fast_mode_credit_multiplier": INC191_FAST_MODE_CREDIT_MULTIPLIER,
        "fast_mode_credit_efficiency_vs_standard": (
            round(
                INC191_FAST_MODE_SPEED_MULTIPLIER / INC191_FAST_MODE_CREDIT_MULTIPLIER,
                2,
            )
            if fast_selected
            else 1.0
        ),
        "fast_mode_end_to_end_speed_claim_withheld": True,
        "planned_cost_baseline": "current_luna_standard",
        "current_luna_standard_credit_rate": dict(INC191_CURRENT_LUNA_STANDARD_CREDIT_RATE),
        "fast_mode_projected_credit_rate": projected_fast_rate,
        "selected_service_tier_credit_rate": selected_rate,
        "historical_luna_standard_factor_vs_pre_reduction": historical_factor,
        "planned_relative_cost_multiplier_vs_current_luna_standard": tier_multiplier,
        "planned_relative_cost_multiplier_vs_pre_reduction_luna_standard": (
            historical_selected_multiplier
        ),
        "planned_cost_formula": current_formula,
        "planned_pre_reduction_cost_formula": historical_formula,
        "planned_cost_estimate_source": estimate_source,
        "planned_cost_estimate_authoritative": False,
        "pricing_rate_card_source": INC191_LUNA_RATE_CARD_SOURCE,
        "speed_rate_source": INC191_LUNA_SPEED_SOURCE,
        "pricing_rate_card_checked_at": INC191_LUNA_RATE_CARD_CHECKED_AT,
        "effective_billed_cost": "UNKNOWN",
        "service_tier_receipt_required_for_runtime_claim": True,
    }


def _select_model_routing(
    request: Any,
    selected_action: dict[str, Any],
    base_dir: Path,
) -> dict[str, str | bool]:
    precision_required = isinstance(request, dict) and request.get("precision_required") is True
    deterministic_mechanical = isinstance(request, dict) and request.get("deterministic_mechanical") is True
    bounded_single_repo = False
    if isinstance(request, dict) and request.get("bounded_single_repo") is True:
        try:
            bounded_single_repo = (
                Path(request.get("worktree", "")).resolve() == base_dir.resolve()
                and selected_action["operation_type"] in {"product_operation", "cause_repair"}
            )
        except (OSError, TypeError, ValueError):
            bounded_single_repo = False
    if precision_required and bounded_single_repo and not deterministic_mechanical:
        return {
            "model": INC191_IMPLEMENTATION_MODEL,
            "reasoning_effort": INC191_MAX_REASONING_EFFORT,
            "selection_reason": "precision_required_single_repo",
            "precision_required": True,
            "deterministic_mechanical": False,
        }
    return {
        "model": INC191_IMPLEMENTATION_MODEL,
        "reasoning_effort": INC191_REASONING_EFFORT,
        "selection_reason": "deterministic_mechanical" if deterministic_mechanical else "default_high",
        "precision_required": precision_required,
        "deterministic_mechanical": deterministic_mechanical,
    }


def _worker_pace_readback(request: Any, selection_reason: str) -> dict[str, Any]:
    """Validate worker pacing without turning a review clock into a stop clock."""
    pace = request.get("worker_pace") if isinstance(request, dict) else None
    expected_profile = (
        "luna_max_precision"
        if selection_reason == "precision_required_single_repo"
        else "luna_high_mechanical"
    )
    default_pace = (
        {
            "profile": "luna_max_precision",
            "grounding_review_ms": 600_000,
            "material_delta_review_ms": 900_000,
            "no_delta_replan_review_ms": 1_800_000,
            "expected_completion_review_ms": 3_600_000,
            "elapsed_only_stop_allowed": False,
            "dispatch_response_checkpoint_used_as_worker_deadline": False,
        }
        if expected_profile == "luna_max_precision"
        else {
            "profile": "luna_high_mechanical",
            "grounding_review_ms": 300_000,
            "material_delta_review_ms": 600_000,
            "no_delta_replan_review_ms": 1_200_000,
            "expected_completion_review_ms": 2_400_000,
            "elapsed_only_stop_allowed": False,
            "dispatch_response_checkpoint_used_as_worker_deadline": False,
        }
    )
    if pace is None:
        return {
            **default_pace,
            "valid": True,
            "source": "consumer_default",
            "review_semantics": "checkpoint_not_hard_stop",
            "reasons": [],
        }
    if not isinstance(pace, dict) or set(pace) != WORKER_PACE_FIELDS:
        return {
            "valid": False,
            "profile": expected_profile,
            "source": "request",
            "review_semantics": "checkpoint_not_hard_stop",
            "reasons": ["worker_pace_malformed"],
        }

    reasons: list[str] = []
    if pace.get("profile") != expected_profile:
        reasons.append("worker_pace_profile_mismatch")
    numeric_fields = (
        "grounding_review_ms", "material_delta_review_ms",
        "no_delta_replan_review_ms", "expected_completion_review_ms",
    )
    if any(not _is_int(pace.get(field), positive=True) for field in numeric_fields):
        reasons.append("worker_pace_budget_invalid")
    else:
        grounding = pace["grounding_review_ms"]
        material = pace["material_delta_review_ms"]
        no_delta = pace["no_delta_replan_review_ms"]
        completion = pace["expected_completion_review_ms"]
        if not grounding <= material <= no_delta <= completion:
            reasons.append("worker_pace_order_invalid")
        if expected_profile == "luna_max_precision":
            if grounding < 600_000:
                reasons.append("worker_pace_precision_grounding_too_short")
            if material < 900_000:
                reasons.append("worker_pace_precision_material_delta_too_short")
            if no_delta < 1_800_000:
                reasons.append("worker_pace_precision_no_delta_replan_too_short")
        else:
            if grounding < 300_000:
                reasons.append("worker_pace_mechanical_grounding_too_short")
            if material < 600_000:
                reasons.append("worker_pace_mechanical_material_delta_too_short")
            if no_delta < 1_200_000:
                reasons.append("worker_pace_mechanical_no_delta_replan_too_short")
    if pace.get("elapsed_only_stop_allowed") is not False:
        reasons.append("worker_pace_elapsed_only_stop_forbidden")
    if pace.get("dispatch_response_checkpoint_used_as_worker_deadline") is not False:
        reasons.append("worker_pace_dispatch_response_deadline_reuse_forbidden")
    return {
        **pace,
        "valid": not reasons,
        "source": "request",
        "review_semantics": "checkpoint_not_hard_stop",
        "reasons": sorted(set(reasons)),
    }


def _control_dispatch_readback_bound(terminal: dict[str, Any]) -> bool:
    target = terminal.get("dispatch_target_thread_id")
    marker = terminal.get("target_readback_marker")
    return bool(
        terminal.get("control_dispatch_sent") is True
        and terminal.get("control_dispatch_mode") == "control_dispatch"
        and _nonempty(target)
        and terminal.get("target_readback_received") is True
        and _nonempty(marker)
        and marker == f"{target}:readback"
    )


def _default_cmd_state_path(base_dir: Path) -> Path:
    try:
        value = subprocess.run(
            [
                "git", "-C", str(base_dir), "rev-parse", "--path-format=absolute",
                "--git-path", "orch-next/inc178-cmd-runtime-state.json",
            ],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise IssueError("BLOCKED_FOR_INC178_CMD_STATE_PATH_UNAVAILABLE") from exc
    path = Path(value)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError as exc:
        raise IssueError("BLOCKED_FOR_INC178_CMD_STATE_PATH_UNAVAILABLE") from exc
    return path


@contextmanager
def _cmd_state_lock(state_path: Path):
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise IssueError("BLOCKED_FOR_INC178_CMD_STATE_LOCK_INVALID")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise IssueError("BLOCKED_FOR_INC178_CMD_STATE_LOCK_UNAVAILABLE") from exc
    except IssueError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    try:
        yield {"held": True, "authorizes": True, "lock_path": str(lock_path.resolve())}
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _cmd_state_digest_at_path(state_path: Path) -> str | None:
    if not state_path.exists():
        return None
    descriptor = -1
    try:
        descriptor = os.open(state_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 65_536
        ):
            return "INVALID"
        value = json.loads(os.read(descriptor, metadata.st_size).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "INVALID"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    digest = value.get("state_digest") if isinstance(value, dict) else None
    return digest if _hex_digest(digest) else "INVALID"


def _cmd_state_digest_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {"state_digest", "chain_digest"}
    }


def _cmd_state_chain_digest(value: dict[str, Any]) -> str:
    predecessor = value.get("predecessor_chain_digest")
    serialized = json.dumps(
        {key: value[key] for key in value if key != "chain_digest"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(str(predecessor).encode("ascii") + serialized).hexdigest()


def _read_journal_json(path: Path) -> Any | None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 65_536
        ):
            return None
        return json.loads(os.read(descriptor, metadata.st_size).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _cmd_state_chain_journal_status(
    state_path: Path, state: dict[str, Any]
) -> str | None:
    journal_path_value = state.get("transition_journal_path")
    if not _nonempty(journal_path_value):
        return CMD_STATE_CHAIN_JOURNAL_MISSING
    journal_path = Path(journal_path_value)
    if journal_path.name != "transition.json":
        return CMD_STATE_CHAIN_JOURNAL_MISMATCH
    candidate_dirs: list[Path] = []
    if journal_path.exists():
        candidate_dirs.append(journal_path.parent)
    local_transition = state_path.parent / "transition.json"
    if local_transition.exists():
        candidate_dirs.append(state_path.parent)
    staged = _staged_output_path(state_path, state)
    if (staged / "transition.json").exists():
        candidate_dirs.append(staged)
    seen: set[Path] = set()
    for directory in candidate_dirs:
        directory = directory.resolve()
        if directory in seen:
            continue
        seen.add(directory)
        transition_path = directory / "transition.json"
        journal_state_path = directory / "cmd-state.json"
        transition = _read_journal_json(transition_path)
        journal_state = _read_journal_json(journal_state_path)
        if (
            not isinstance(transition, dict)
            or not isinstance(journal_state, dict)
            or journal_state != state
            or state.get("transition_journal_digest") != _canonical_digest(transition)
        ):
            continue
        if state.get("chain_digest") != journal_state.get("chain_digest"):
            continue
        return None
    return CMD_STATE_CHAIN_JOURNAL_MISMATCH


def _is_linear_head_advance(base_dir: Path, previous_head: str, current_head: str) -> bool:
    if previous_head == current_head:
        return True
    try:
        return subprocess.run(
            ["git", "-C", str(base_dir), "merge-base", "--is-ancestor", previous_head, current_head],
            capture_output=True,
            check=False,
            text=True,
        ).returncode == 0
    except OSError:
        return False


def pre_dispatch_admission(
    request: Any,
    *,
    base_dir: Path,
    expected_owner: str,
    expected_write_set: list[str] | tuple[str, ...],
    holder_path: Path,
    holder_lock_held: bool,
    expected_reasoning_effort: str = INC191_REASONING_EFFORT,
    model_selection_reason: str = "default_high",
    derived_correction_count: int | None = None,
    derived_replan_reasons: list[str] | None = None,
    derived_primary: dict[str, Any] | None = None,
    derived_telemetry_state: str = "valid",
    derived_protected_transition_requested: bool = False,
    derived_protected_wait: bool = False,
) -> dict[str, Any]:
    """Admit the existing bounded issuer before it writes any output."""
    result: dict[str, Any] = {
        "mutation_allowed": True,
        "safe_local_work_continues": True,
        "claim_withheld": False,
        "protected_transition_held": False,
        "merge_held": False,
        "continuation": "same_worktree",
        "decision": "CONTINUE_LOCAL",
        "same_strategy_allowed": True,
        "observed_effective": False,
        "model": INC191_IMPLEMENTATION_MODEL,
        "reasoning_effort": expected_reasoning_effort,
        "selection_reason": model_selection_reason,
        "runtime_identity_verified": False,
        "native_app_interception": "ABSENT",
        "source_eligible": "denied",
        "external_dispatch": False,
        "cost_telemetry_source": "caller_reported_non_authoritative",
        "discount_eligibility_source": "caller_reported",
        "cost_claim_withheld": True,
        "billing_telemetry_authoritative": False,
        "effective_billed_cost": "UNKNOWN",
        "worker_pace": {
            "valid": False,
            "review_semantics": "checkpoint_not_hard_stop",
            "reasons": ["worker_pace_missing_or_malformed"],
        },
        "reasons": [],
    }

    def deny(reason: str) -> None:
        result["mutation_allowed"] = False
        result["reasons"].append(reason)

    if not isinstance(request, dict):
        deny("model_identity_unknown")
        result["claim_withheld"] = True
        return result

    model = request.get("implementation_model")
    if model != INC191_IMPLEMENTATION_MODEL:
        deny("model_identity_unknown" if not model else "model_identity_not_luna")
        result["claim_withheld"] = True
    if request.get("reasoning_effort") != expected_reasoning_effort:
        deny("reasoning_effort_selection_mismatch")
        result["claim_withheld"] = True

    worker_pace = _worker_pace_readback(request, model_selection_reason)
    result["worker_pace"] = worker_pace
    for reason in worker_pace["reasons"]:
        deny(reason)

    normalized_cost, cost_claim_withheld = _normalize_cost_telemetry(
        request.get("cost_telemetry")
    )
    result["cost_telemetry"] = normalized_cost
    result["cost_claim_withheld"] = cost_claim_withheld
    result["cost_telemetry_shape_valid"] = _valid_cost_telemetry(request.get("cost_telemetry"))
    result.update(_luna_fast_mode_policy(
        normalized_cost["discount_eligibility"],
        model_selection_reason,
    ))

    try:
        actual_worktree = str(base_dir.resolve())
        expected_worktree = str(Path(request.get("worktree", "")).resolve())
    except (OSError, TypeError, ValueError):
        actual_worktree = ""
        expected_worktree = ""
    if expected_worktree != actual_worktree:
        deny("worktree_mismatch")
    if request.get("owner") != expected_owner:
        deny("owner_mismatch")
    expected_binding = expected_write_set[0] if expected_write_set else ""
    if request.get("selected_action_binding") != expected_binding:
        deny("selected_action_binding_mismatch")
    if request.get("write_set_kind") != INC191_WRITE_SET_KIND:
        deny("write_set_kind_not_abstract_action_binding")

    holder = request.get("durable_holder")
    expected_holder_path = str(holder_path.resolve())
    if (
        not isinstance(holder, dict)
        or holder.get("holder_id") != expected_owner
        or holder.get("status") != "held"
        or holder.get("authorizes") is not True
        or holder.get("lock_path") != expected_holder_path
        or holder_lock_held is not True
    ):
        deny("durable_holder_invalid")
        result["continuation"] = "fresh_worktree"

    if request.get("merge_order_conflict") is True:
        result["merge_held"] = True
        if request.get("disjoint_branch") is not True:
            deny("merge_order_conflict_not_disjoint")
        else:
            result["reasons"].append("merge_order_conflict_held")

    if request.get("protected_transition_requested") is not derived_protected_transition_requested:
        deny("protected_transition_claim_untrusted")
    if derived_protected_transition_requested:
        deny("protected_transition_held")
        result["protected_transition_held"] = True

    primary = derived_primary or {}
    primary_fields = ("support_lanes_active", "primary_state", "goal_incomplete", "safe_disjoint_work")
    if any(request.get(field) != primary.get(field) for field in primary_fields):
        deny("primary_lane_claim_untrusted")
    if (
        primary.get("support_lanes_active") is True
        and primary.get("goal_incomplete") is True
        and primary.get("safe_disjoint_work") is True
        and primary.get("primary_state") not in INC191_PRIMARY_ACTIVE_STATES
    ):
        deny("dispatch_primary_before_closeout")
    if primary.get("primary_state") == "protected_blocked" and not derived_protected_wait:
        deny("dispatch_primary_before_closeout")

    if not isinstance(derived_correction_count, int) or isinstance(derived_correction_count, bool) or derived_correction_count < 0:
        deny("persisted_correction_state_unavailable")
    else:
        result["correction_count"] = derived_correction_count
        result["correction_count_source"] = "persisted_cmd_state"
    if derived_replan_reasons is None:
        deny("persisted_replan_state_unavailable")
    elif derived_replan_reasons:
        result["decision"] = "REPLAN_NOW"
        result["same_strategy_allowed"] = False
        result["reasons"].extend(derived_replan_reasons)

    if request.get("telemetry_state") != derived_telemetry_state:
        result["claim_withheld"] = True
        result["reasons"].append("telemetry_claim_untrusted")
    if derived_telemetry_state != "valid":
        result["claim_withheld"] = True
        result["observed_effective"] = False
        result["reasons"].append("telemetry_claim_withheld")
    if request.get("claim_check_failure") is True or request.get("unavailable_skill") is True:
        result["claim_withheld"] = True
        result["reasons"].append("support_claim_withheld")

    result["observed_effective"] = False
    result["source_eligible"] = "eligible" if result["mutation_allowed"] else "denied"
    result["reasons"] = sorted(set(result["reasons"]))
    return result


def _load_cmd_state(
    state_path: Path,
    *,
    base_dir: Path,
    repository_head: str,
    now: datetime,
) -> tuple[dict[str, Any], bool, str]:
    fallback = {
        "schema_version": CMD_STATE_VERSION,
        "sequence": 0,
        "predecessor_state_digest": "0" * 64,
        "cause_changing_correction_count": 0,
        "total_cause_changing_correction_count": 0,
        "lineage_correction_counts": {},
        "last_capability_delta_at": _stamp(now),
        "last_event_id": "",
        "last_event_sequence": 0,
        "last_observation_digest": "0" * 64,
        "total_attempt_count": 0,
        "seen_capability_transition_ids": [],
        "natural_transition_count": 0,
        "natural_task_classes": [],
        "same_class_user_correction_count": 0,
        "repository_head": repository_head,
        "state_digest": "0" * 64,
        "cmd_epoch_id": "",
        "previous_epoch_id": "",
        "cmd_release_state": "released",
        "checkpoint_sha256": "0" * 64,
        "pending_returns": [],
        "decision_owner": {"role": "", "surface": "", "epoch_id": ""},
        "transport_actor": {"role": "", "surface": ""},
        "next_operation": "",
        "sdo_decision": "",
        "receipt_digest": "",
        "receipt_consumed": False,
        "bootstrap_state": "",
        "predecessor_chain_digest": "0" * 64,
        "chain_digest": "0" * 64,
        "transition_journal_path": "",
        "transition_journal_digest": "",
    }
    if not state_path.exists():
        return fallback, True, "CMD_STATE_INITIALIZED"
    descriptor = -1
    try:
        descriptor = os.open(state_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65_536:
            return fallback, False, "CMD_STATE_MALFORMED"
        value = json.loads(os.read(descriptor, metadata.st_size).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return fallback, False, "CMD_STATE_MALFORMED"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    actual_state_fields = set(value) if isinstance(value, dict) else set()
    legacy_state = actual_state_fields in (
        LEGACY_CMD_STATE_FIELDS, LEGACY_SDO_CMD_STATE_FIELDS,
        LEGACY_EPOCH_CMD_STATE_FIELDS,
    )
    full_v3_compat_state = actual_state_fields == LEGACY_FULL_CMD_STATE_FIELDS
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not isinstance(value, dict)
        or actual_state_fields not in CMD_STATE_FIELD_SETS
        or value.get("schema_version") != CMD_STATE_VERSION
        or not _is_int(value.get("sequence"), positive=True)
        or not _hex_digest(value.get("predecessor_state_digest"))
        or not _is_int(value.get("cause_changing_correction_count"))
        or not _is_int(value.get("total_cause_changing_correction_count"))
        or not isinstance(value.get("lineage_correction_counts"), dict)
        or len(value.get("lineage_correction_counts", {})) > CMD_MAX_TASK_LINEAGES
        or any(not _nonempty(key) or not _is_int(count) for key, count in value.get("lineage_correction_counts", {}).items())
        or _parse_time(value.get("last_capability_delta_at")) is None
        or _parse_time(value.get("last_capability_delta_at")) > now
        or not isinstance(value.get("last_event_id"), str)
        or not _is_int(value.get("last_event_sequence"))
        or not _hex_digest(value.get("last_observation_digest"))
        or not _is_int(value.get("total_attempt_count"))
        or not isinstance(value.get("seen_capability_transition_ids"), list)
        or len(value.get("seen_capability_transition_ids", [])) > CMD_MAX_CAPABILITY_TRANSITIONS
        or any(not _nonempty(item) for item in value.get("seen_capability_transition_ids", []))
        or len(set(value.get("seen_capability_transition_ids", []))) != len(value.get("seen_capability_transition_ids", []))
        or not _is_int(value.get("natural_transition_count"))
        or not isinstance(value.get("natural_task_classes"), list)
        or any(not _nonempty(item) for item in value.get("natural_task_classes", []))
        or len(set(value.get("natural_task_classes", []))) != len(value.get("natural_task_classes", []))
        or not _is_int(value.get("same_class_user_correction_count"))
        or not _hex_digest(value.get("repository_head"), length=40)
        or not _hex_digest(value.get("state_digest"))
    ):
        return fallback, False, "CMD_STATE_MALFORMED"
    if actual_state_fields in (
        LEGACY_SDO_CMD_STATE_FIELDS, LEGACY_FULL_CMD_STATE_FIELDS, CMD_STATE_FIELDS
    ) and (
        not isinstance(value.get("next_operation"), str)
        or not isinstance(value.get("sdo_decision"), str)
        or not isinstance(value.get("receipt_digest"), str)
        or not isinstance(value.get("receipt_consumed"), bool)
        or (value.get("receipt_consumed") is True and not _nonempty(value.get("receipt_digest")))
    ):
        return fallback, False, "CMD_STATE_MALFORMED"
    if actual_state_fields in (
        LEGACY_EPOCH_CMD_STATE_FIELDS, LEGACY_FULL_CMD_STATE_FIELDS, CMD_STATE_FIELDS
    ) and (
        not _valid_cmd_epoch_state_fields(value)
    ):
        return fallback, False, "CMD_STATE_MALFORMED"
    if legacy_state:
        unsigned = {key: value[key] for key in actual_state_fields - {"state_digest"}}
        if _canonical_digest(unsigned) != value["state_digest"]:
            return fallback, False, "CMD_STATE_MALFORMED"
        previous_next_operation = value.get("next_operation", "")
        value = normalize_cmd_epoch_state(value)
        if not isinstance(value.get("next_operation"), str):
            value["next_operation"] = ""
        if not isinstance(value.get("sdo_decision"), str):
            value["sdo_decision"] = ""
        if not isinstance(value.get("receipt_digest"), str):
            value["receipt_digest"] = ""
        if not isinstance(value.get("receipt_consumed"), bool):
            value["receipt_consumed"] = False
        value.update({
            "bootstrap_state": (
                CMD_STATE_BOOTSTRAP_PRESERVED
                if _nonempty(previous_next_operation)
                else CMD_STATE_BOOTSTRAP_PENDING
            ),
            "predecessor_chain_digest": "0" * 64,
            "chain_digest": "",
            "transition_journal_path": "",
            "transition_journal_digest": "",
        })
        value["state_digest"] = _canonical_digest(
            _cmd_state_digest_payload(value)
        )
    else:
        if (
            not isinstance(value.get("bootstrap_state"), str)
            or value.get("bootstrap_state") not in {
                "", CMD_STATE_BOOTSTRAP_PENDING,
                CMD_STATE_BOOTSTRAP_PRESERVED, CMD_STATE_BOOTSTRAP_BOUND,
            }
            or not _hex_digest(value.get("predecessor_chain_digest"))
            or not _hex_digest(value.get("chain_digest"))
            or not _nonempty(value.get("transition_journal_path"))
            or not _hex_digest(value.get("transition_journal_digest"))
            or _canonical_digest(_cmd_state_digest_payload(value)) != value["state_digest"]
            or _cmd_state_chain_digest(value) != value["chain_digest"]
        ):
            return fallback, False, CMD_STATE_CHAIN_INVALID
        journal_status = _cmd_state_chain_journal_status(state_path, value)
        if journal_status:
            return fallback, False, journal_status
        if full_v3_compat_state:
            value = normalize_cmd_epoch_state(value)
            value["state_digest"] = _canonical_digest(
                _cmd_state_digest_payload(value)
            )
            value["chain_digest"] = _cmd_state_chain_digest(value)
    if not _is_linear_head_advance(base_dir, value["repository_head"], repository_head):
        return fallback, False, "CMD_STATE_REPOSITORY_LINEAGE_INVALID"
    if legacy_state or full_v3_compat_state:
        return value, True, CMD_STATE_LEGACY_SDO_UPGRADE
    return value, True, "" if value["repository_head"] == repository_head else "CMD_STATE_LINEAR_HEAD_ADVANCE"


def _observation_identity(doc: dict[str, Any]) -> tuple[str, str]:
    lineage = "::".join((doc["binding"]["goal_ref"], doc["binding"]["phase_ref"], doc["work_class"]))
    observed = {
        key: doc[key]
        for key in (
            "schema_version", "observation_source", "work_class", "transition", "binding",
            "whole_goal", "progress_deltas", "time_accounting", "counters", "candidates",
            "before_selected_action_id", "terminal_continuation",
        )
    }
    for key in ("dispatch_request", "protected_transition_request", "cmd_epoch_request", "return_event"):
        if key in doc:
            observed[key] = doc[key]
    if "sdo_downstream_result" in doc:
        observed["sdo_downstream_result"] = doc["sdo_downstream_result"]
    if "sdo_decision_receipt" in doc:
        observed["sdo_decision_receipt"] = doc["sdo_decision_receipt"]
    if "sdo_route_receipt" in doc:
        observed["sdo_route_receipt"] = doc["sdo_route_receipt"]
    return lineage, _canonical_digest(observed)


def _sdo_effective_downstream_result(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Return a typed Hermes-consumption result that can earn natural credit."""
    result = doc.get("sdo_downstream_result")
    intake = doc.get("sdo_intake")
    terminal = doc.get("terminal_continuation", {})
    if (
        not isinstance(result, dict)
        or result.get("consumer") != "hermes_sdo_adapter"
        or result.get("consumed") is not True
        or not isinstance(intake, dict)
        or result.get("project_id") != intake.get("project_id")
        or terminal.get("terminal_result_consumed") is not True
        or terminal.get("selected_action_result_consumed") is not True
        or (
            result.get("action_changed") is not True
            and result.get("decision") != "REPLAN_NOW"
            and result.get("model_route_changed") is not True
        )
    ):
        return None
    return result


def _correction_delta_classification(doc: dict[str, Any], observed_delta: int) -> dict[str, Any]:
    """Classify only the latest validated user correction as strategy-changing.

    Clerical/test/path/receipt corrections advance the raw observation baseline
    so they cannot be recounted later, but they never consume the replan budget.
    """
    if observed_delta <= 0:
        return {
            "cause_changing_delta": 0,
            "non_cause_delta": 0,
            "latest_warning_classes": [],
            "classification": "none",
        }
    warning_events = doc["counters"]["user_warning_events"]
    if not warning_events:
        return {
            "cause_changing_delta": 0,
            "non_cause_delta": observed_delta,
            "latest_warning_classes": [],
            "classification": "unknown_nonblocking",
        }
    latest_observed = max(_parse_time(row["last_observed_at"]) for row in warning_events)
    latest_classes = sorted({
        row["warning_class"]
        for row in warning_events
        if _parse_time(row["last_observed_at"]) == latest_observed
    })
    cause_changing = bool(set(latest_classes) & CAUSE_CHANGING_CORRECTION_CLASSES)
    explicitly_non_cause = bool(set(latest_classes) & NON_CAUSE_CORRECTION_CLASSES)
    if cause_changing:
        return {
            "cause_changing_delta": observed_delta,
            "non_cause_delta": 0,
            "latest_warning_classes": latest_classes,
            "classification": "cause_changing",
        }
    return {
        "cause_changing_delta": 0,
        "non_cause_delta": observed_delta,
        "latest_warning_classes": latest_classes,
        "classification": (
            "clerical_nonblocking" if explicitly_non_cause else "unclassified_nonblocking"
        ),
    }


def _cmd_epoch_request(doc: dict[str, Any]) -> dict[str, Any] | None:
    request = doc.get("cmd_epoch_request")
    if request is not None:
        return request
    event = doc.get("return_event")
    if isinstance(event, dict):
        if "operation" in event:
            return event
        return {"operation": "return", **event}
    return None


def _has_cmd_state_semantics(value: Any) -> bool:
    """Identify inputs that must never be issued by the legacy path."""
    return isinstance(value, dict) and (
        "cmd_epoch_request" in value
        or "return_event" in value
        or "sdo_decision_receipt" in value
        or "sdo_route_receipt" in value
    )


def _advance_cmd_state(
    state: dict[str, Any],
    *,
    base_dir: Path,
    state_supported: bool,
    state_debt: str,
    doc: dict[str, Any],
    selected_action: dict[str, Any],
    repository_head: str,
    now: datetime,
    journal_path: Path | None = None,
    journal_digest: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    (
        next_operation,
        sdo_decision,
        receipt_digest,
        receipt_consumed,
        sdo_receipt_consumption,
    ) = _sdo_receipt_binding(state, doc, selected_action)
    sdo_receipt_consumption["route_authority_binding"] = _sdo_route_authority_binding(
        base_dir,
        doc,
        next_operation=next_operation,
        selected_action=selected_action,
    )
    epoch_request = _cmd_epoch_request(doc)
    epoch_state = normalize_cmd_epoch_state(state)
    epoch_blocker = None
    if state_supported and epoch_request is not None:
        epoch_state, epoch_blocker = apply_cmd_epoch_request(
            epoch_state,
            epoch_request,
            repository_head=repository_head,
        )
    corrections = state["cause_changing_correction_count"] if state_supported else 0
    last_capability_delta = _parse_time(state["last_capability_delta_at"]) if state_supported else now
    last_event_id = state["last_event_id"] if state_supported else ""
    last_event_sequence = state["last_event_sequence"] if state_supported else 0
    total_attempts = state["total_attempt_count"] if state_supported else 0
    total_corrections = state["total_cause_changing_correction_count"] if state_supported else 0
    natural_transition_count = state["natural_transition_count"] if state_supported else 0
    natural_task_classes = list(state["natural_task_classes"]) if state_supported else []
    lineage_correction_counts = dict(state["lineage_correction_counts"]) if state_supported else {}
    seen_capability_transition_ids = list(state["seen_capability_transition_ids"]) if state_supported else []
    same_class_user_corrections = state["same_class_user_correction_count"] if state_supported else 0
    task_lineage, observation_digest = _observation_identity(doc)
    previous_observation_digest = state["last_observation_digest"] if state_supported else "0" * 64
    current_user_corrections = doc["counters"]["user_correction_count"]
    previous_user_corrections = lineage_correction_counts.get(task_lineage, 0)
    rejection_reason = ""
    if state_supported and observation_digest == previous_observation_digest:
        rejection_reason = "CMD_OBSERVATION_REPLAY"
    elif state_supported and current_user_corrections < previous_user_corrections:
        rejection_reason = "CMD_OBSERVATION_COUNTER_REGRESSION"
    observed_correction_delta = max(0, current_user_corrections - previous_user_corrections)
    if state_supported and not rejection_reason and observed_correction_delta > 1:
        rejection_reason = "CMD_OBSERVATION_CORRECTION_JUMP"
    correction_classification = _correction_delta_classification(doc, observed_correction_delta)
    cause_changing_correction_delta = correction_classification["cause_changing_delta"]

    capability = doc["progress_deltas"]["user_visible_capability_delta"]
    capability_transition_id = capability.get("transition_id", "")
    duplicate_capability_transition = bool(
        capability_transition_id and capability_transition_id in seen_capability_transition_ids
    )
    downstream_result = _sdo_effective_downstream_result(doc)
    event_type: str | None = None
    event_accepted = False
    event_id = ""
    if state_supported and not rejection_reason:
        total_attempts += 1
        if (
            capability["classification"] == "positive"
            and not duplicate_capability_transition
            and downstream_result is not None
        ):
            event_type = "user_capability_delta"
        elif cause_changing_correction_delta and selected_action["cause_changing_repair"]:
            event_type = "cause_changing_correction"
        elif capability["classification"] == "zero":
            event_type = "no_capability_delta_checkpoint"
        if event_type is not None:
            event_accepted = True
            last_event_sequence += 1
            event_id = _canonical_digest({
                "event_sequence": last_event_sequence,
                "event_type": event_type,
                "observation_digest": observation_digest,
                "task_lineage": task_lineage,
            })
            last_event_id = event_id
        if observed_correction_delta:
            lineage_correction_counts[task_lineage] = current_user_corrections
        if cause_changing_correction_delta and selected_action["cause_changing_repair"]:
            total_corrections += cause_changing_correction_delta
            if event_type == "cause_changing_correction":
                corrections += cause_changing_correction_delta
                project_lineage = downstream_result["project_id"] if downstream_result else ""
                if project_lineage in natural_task_classes:
                    same_class_user_corrections += cause_changing_correction_delta
        if event_type == "user_capability_delta":
            seen_capability_transition_ids.append(capability_transition_id)
            corrections = 0
            last_capability_delta = now
            natural_transition_count += 1
            project_lineage = downstream_result["project_id"]
            if project_lineage not in natural_task_classes:
                natural_task_classes.append(project_lineage)

    no_delta_minutes = max(0, int((now - last_capability_delta).total_seconds() // 60))
    claim_withheld_reason = rejection_reason or (
        "CMD_CAPABILITY_DELTA_UNKNOWN" if capability["classification"] == "unknown" else ""
    )
    receipt_bound = sdo_receipt_consumption.get("receipt_consumed") is True
    bootstrap_state = state.get("bootstrap_state", "") if state_supported else ""
    if receipt_bound:
        bootstrap_state = CMD_STATE_BOOTSTRAP_BOUND
    elif not isinstance(bootstrap_state, str):
        bootstrap_state = ""
    predecessor_chain_digest = (
        state.get("chain_digest", "")
        if state_supported and state_debt != CMD_STATE_LEGACY_SDO_UPGRADE
        else "0" * 64
    )
    if not _hex_digest(predecessor_chain_digest):
        predecessor_chain_digest = "0" * 64
    trigger_reasons: list[str] = []
    if state_supported and corrections >= CMD_REPLAN_AFTER_CORRECTIONS:
        trigger_reasons.append("two_cause_changing_corrections")
    if state_supported and no_delta_minutes >= CMD_REPLAN_AFTER_NO_DELTA_MINUTES:
        trigger_reasons.append("ninety_minutes_without_capability_delta")

    successor_unsigned = {
        "schema_version": CMD_STATE_VERSION,
        "sequence": state["sequence"] + 1 if state_supported else 1,
        "predecessor_state_digest": state["state_digest"] if state_supported else "0" * 64,
        "cause_changing_correction_count": corrections,
        "total_cause_changing_correction_count": total_corrections,
        "lineage_correction_counts": lineage_correction_counts,
        "last_capability_delta_at": _stamp(last_capability_delta),
        "last_event_id": last_event_id,
        "last_event_sequence": last_event_sequence,
        "last_observation_digest": observation_digest if not rejection_reason else previous_observation_digest,
        "total_attempt_count": total_attempts,
        "seen_capability_transition_ids": seen_capability_transition_ids,
        "natural_transition_count": natural_transition_count,
        "natural_task_classes": sorted(natural_task_classes),
        "same_class_user_correction_count": same_class_user_corrections,
        "repository_head": repository_head,
        "cmd_epoch_id": epoch_state["cmd_epoch_id"],
        "previous_epoch_id": epoch_state["previous_epoch_id"],
        "cmd_release_state": epoch_state["cmd_release_state"],
        "checkpoint_sha256": epoch_state["checkpoint_sha256"],
        "pending_returns": epoch_state["pending_returns"],
        "decision_owner": epoch_state["decision_owner"],
        "transport_actor": epoch_state["transport_actor"],
        "next_operation": next_operation,
        "sdo_decision": sdo_decision,
        "receipt_digest": receipt_digest,
        "receipt_consumed": receipt_consumed,
        "bootstrap_state": bootstrap_state,
        "predecessor_chain_digest": predecessor_chain_digest,
        "transition_journal_path": (
            str(journal_path.resolve()) if journal_path is not None else ""
        ),
        "transition_journal_digest": journal_digest,
    }
    successor = {
        **successor_unsigned,
        "state_digest": _canonical_digest(successor_unsigned),
    }
    successor["chain_digest"] = _cmd_state_chain_digest(successor)
    readback = {
        "prevention_claim_supported": state_supported and not claim_withheld_reason,
        "state_debt": claim_withheld_reason or state_debt,
        "predecessor_sequence": state["sequence"] if state_supported else None,
        "successor_sequence": successor["sequence"],
        "cause_changing_correction_count": corrections,
        "total_cause_changing_correction_count": total_corrections,
        "observed_user_correction_delta": observed_correction_delta,
        "cause_changing_correction_delta": cause_changing_correction_delta,
        "non_cause_correction_delta": correction_classification["non_cause_delta"],
        "correction_classification": correction_classification["classification"],
        "latest_correction_warning_classes": correction_classification["latest_warning_classes"],
        "total_attempt_count": total_attempts,
        "no_capability_delta_minutes": no_delta_minutes if state_supported else None,
        "derived_event_accepted": event_accepted,
        "caller_return_event_ignored": doc.get("return_event") is not None and epoch_request is None,
        "cmd_epoch_request_applied": epoch_request is not None and epoch_blocker is None,
        "cmd_epoch_blocker": epoch_blocker,
        "next_operation": successor["next_operation"],
        "receipt_digest": sdo_receipt_consumption["receipt_digest"],
        "receipt_consumed": sdo_receipt_consumption["receipt_consumed"],
        "receipt_id": sdo_receipt_consumption.get("receipt_id", ""),
        "consumed_by": sdo_receipt_consumption.get("consumed_by", ""),
        "bootstrap_state": successor["bootstrap_state"],
        "predecessor_chain_digest": successor["predecessor_chain_digest"],
        "chain_digest": successor["chain_digest"],
        "transition_journal_path": successor["transition_journal_path"],
        "sdo_receipt_consumption": sdo_receipt_consumption,
        "cmd_epoch_id": successor["cmd_epoch_id"],
        "previous_epoch_id": successor["previous_epoch_id"],
        "cmd_release_state": successor["cmd_release_state"],
        "checkpoint_sha256": successor["checkpoint_sha256"],
        "pending_return_count": len(successor["pending_returns"]),
        "pending_return_consumed_count": sum(
            1 for entry in successor["pending_returns"] if entry.get("consumed") is True
        ),
        "decision_owner": successor["decision_owner"],
        "transport_actor": successor["transport_actor"],
        "derived_event_id": event_id,
        "derived_event_sequence": last_event_sequence if event_accepted else None,
        "derived_event_type": event_type,
        "observation_rejected": bool(rejection_reason),
        "duplicate_capability_transition": duplicate_capability_transition,
        "unknown_capability_preserved": capability["classification"] == "unknown",
        "state_update_allowed": state_supported and not rejection_reason,
        "trigger_reasons": trigger_reasons,
        "natural_transition_count": natural_transition_count,
        "natural_task_classes": sorted(natural_task_classes),
        "same_class_user_correction_count": same_class_user_corrections,
        "natural_effective_delta": downstream_result is not None,
        "natural_effectiveness_credit": bool(
            state_supported and downstream_result is not None
        ),
        "natural_credit_basis": (
            "post_hermes_consumption_effective_delta"
            if downstream_result is not None
            else "positive_caller_delta_is_not_acceptance_credit"
        ),
        "cohort_readiness": bool(
            state_supported
            and not claim_withheld_reason
            and natural_transition_count >= 3
            and len(natural_task_classes) >= 2
            and same_class_user_corrections == 0
        ),
        "cohort_readiness_authoritative": False,
        "observed_effective": False,
    }
    return successor, readback


def _git_head(base_dir: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(base_dir), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_HEAD_UNAVAILABLE") from exc


def _git_repo_root(task_root: Path) -> Path:
    try:
        repo_root = subprocess.run(
            ["git", "-C", str(task_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise IssueError("BLOCKED_FOR_INC178_NATURAL_TASK_ROOT_NOT_GIT") from exc
    if not repo_root:
        raise IssueError("BLOCKED_FOR_INC178_NATURAL_TASK_ROOT_NOT_GIT")
    return Path(repo_root).resolve()


def _validate_natural_task_root(task_root: Path) -> Path:
    resolved = task_root.resolve()
    if not resolved.is_dir() or _git_repo_root(resolved) != resolved:
        raise IssueError("BLOCKED_FOR_INC178_NATURAL_TASK_ROOT_NOT_REPO_ROOT")
    return resolved


def _git_status_category(base_dir: Path) -> str:
    """Read only the current clean/dirty category, never the status paths."""
    try:
        status = subprocess.run(
            ["git", "-C", str(base_dir), "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_STATUS_UNAVAILABLE") from exc
    return "dirty" if status else "clean"


def _natural_identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise IssueError(f"BLOCKED_FOR_INC178_NATURAL_ARGUMENT_INVALID:{label}")
    return value


def _natural_observed_at(observed_at: Any) -> datetime:
    current = _utc_now()
    if observed_at is None:
        return current
    if isinstance(observed_at, datetime):
        if observed_at.tzinfo is None:
            raise IssueError("BLOCKED_FOR_INC178_NATURAL_ARGUMENT_INVALID:observed_at")
        value = observed_at.astimezone(timezone.utc).replace(microsecond=0)
    else:
        value = _parse_time(observed_at)
        if value is None:
            raise IssueError("BLOCKED_FOR_INC178_NATURAL_ARGUMENT_INVALID:observed_at")
    if value > current:
        raise IssueError("BLOCKED_FOR_INC178_NATURAL_ARGUMENT_INVALID:observed_at")
    return value


def build_natural_prompt_observation(
    task_root: Path,
    *,
    project_id: str,
    goal_ref: str,
    phase_ref: str,
    operation_id: str,
    observed_at: Any = None,
) -> dict[str, Any]:
    """Build the complete neutral INC-178 input from current repository facts."""
    project_id = _natural_identifier(project_id, "project_id")
    goal_ref = _natural_identifier(goal_ref, "goal_ref")
    phase_ref = _natural_identifier(phase_ref, "phase_ref")
    operation_id = _natural_identifier(operation_id, "operation_id")
    observed = _natural_observed_at(observed_at)
    task_root = _validate_natural_task_root(task_root)
    head_ref = _git_head(task_root)
    status_category = _git_status_category(task_root)
    stamp = _stamp(observed)
    operation_binding = f"natural-operation:{operation_id}:working-tree:{status_category}"

    def candidate(
        action_id: str,
        operation_type: str,
        next_action: str,
        rejected_reason: str,
    ) -> dict[str, Any]:
        return {
            "action_id": action_id,
            "operation_type": operation_type,
            "user_capability_delta_score": 0,
            "blocker_delta_score": 0,
            "estimated_cost_ms": 0,
            "gate_burden_ms": 0,
            "next_action": next_action,
            "rejected_reason": rejected_reason,
            "product_path_simplified_or_unnecessary_gate_removed": False,
            "cause_changing_repair": False,
            "classified_as_unchanged_retry": False,
            "dependency_map_reviewed": False,
            "pin_or_provenance_only_fast_path_eligible": False,
            "fast_path_used": False,
        }

    return {
        "schema_version": INPUT_VERSION,
        "observation_source": "hermes:natural-prompt-current-repo-facts",
        "work_class": "delegated_nontrivial",
        "transition": "return_decide_dispatch",
        "binding": {
            "goal_ref": goal_ref,
            "phase_ref": phase_ref,
            "head_ref": head_ref,
            "blocker_fingerprint": operation_binding,
        },
        "whole_goal": {
            "started_at": stamp,
            "estimate_range_ms": {"min_ms": 0, "max_ms": 1},
            "expected_completion_max_ms": 1,
            "active_elapsed_source": "measured",
            "local_blocker_delta": "zero",
            "current_biggest_blocker": f"No blocker observed; working-tree category is {status_category}.",
        },
        "progress_deltas": {
            "blocker_knowledge_delta": {"classification": "zero", "summary": "No blocker knowledge delta observed."},
            "runtime_milestone_delta": {"classification": "zero", "summary": "No runtime milestone observed."},
            "user_visible_capability_delta": {
                "classification": "zero",
                "summary": "No positive natural user transition observed.",
                "normal_user_operation_observed": False,
            },
        },
        "time_accounting": {
            "support_work_elapsed_ms": 0,
            "authority_gate_wait_ms": 0,
            "claim_check_support_ms": 0,
        },
        "counters": {
            "chained_implementation_blocks": 0,
            "consecutive_zero_visible_delta_slices": 0,
            "distinct_causal_blocker_count": 0,
            "protected_mutation_or_pair_count": 0,
            "evidence_only_slice_count": 0,
            "user_correction_count": 0,
            "user_warning_events": [],
            "warning_count_source": "current natural observation",
            "user_relay_count": 0,
            "idle_after_partial": False,
            "return_decide_dispatch_elapsed_ms": 0,
            "false_block_count": 0,
            "missed_block_count": 0,
            "avoidable_model_cost_count": 0,
            "product_decision_changed_count": 0,
        },
        "skill_firing": {
            "expected_skills": ["hermes-natural-prompt"],
            "invocation_records": [],
            "nonfire_reason": "result_not_integrated",
            "skill_surface_state": {
                "canonical_source_state": "unavailable",
                "plugin_distribution_state": "unavailable",
                "plugin_cache_diagnostic_state": "unavailable",
                "unprefixed_skill_root_state": "unavailable",
                "active_resolution_root_state": "unavailable",
            },
        },
        "audit_integration": {
            "implementation_owner_thread_id": "UNKNOWN",
            "subagent_lanes_exist": False,
            "fourth_oversight_present": False,
            "fourth_oversight_self_demoted": False,
            "audit_records": [],
        },
        "gate_burden": {"budget_ms": 1, "avoidable_model_cost_ms": 0, "inventory": []},
        "recent_activity": {"source": False, "ci": False, "audit": False},
        "candidates": [
            candidate(
                "natural-safe-local-primary",
                "product_operation",
                "Continue safe local work only if separately authorized.",
                "Neutral repo-ranked candidate.",
            ),
            candidate(
                "natural-safe-local-secondary",
                "product_operation",
                "Retain the second neutral safe-local option.",
                "Lower deterministic tie-break position.",
            ),
            candidate(
                "natural-safe-local-cause-repair",
                "cause_repair",
                "Keep cause repair unselected until current facts support it.",
                "Lower deterministic action-class tie-break position.",
            ),
        ],
        "before_selected_action_id": "natural-safe-local-secondary",
        "terminal_continuation": {
            "terminal_result_consumed": False,
            "protected_adoption_held": False,
            "bounded_local_repair_dispatchable": False,
            "primary_state": "active",
            "quiet_closeout_requested": False,
            "current_transition_checker_invoked": False,
            "selected_action_result_consumed": False,
            "control_dispatch_sent": False,
            "control_dispatch_mode": "",
            "dispatch_target_thread_id": "",
            "target_readback_received": False,
            "target_readback_marker": "",
            "progress_blocker": {
                "present": False,
                "blocker_id": "",
                "summary": "",
                "owner": "",
                "unblock_condition": "",
            },
        },
        "sdo_intake": {
            "project_id": project_id,
            "brain": {"status": "unavailable"},
            "pms": {"status": "unavailable"},
            "odg": {"status": "unavailable"},
            "fable5": {"status": "unavailable"},
            "accepted_knowledge": [],
            "brain_decisions": [],
            "brain_identities": [],
            "fable5_records": [],
        },
    }


def _is_natural_observation(doc: dict[str, Any]) -> bool:
    return doc.get("observation_source") == "hermes:natural-prompt-current-repo-facts"


def _natural_model_routing() -> dict[str, str | bool]:
    return {
        "provider": "openai-codex",
        "model": INC191_IMPLEMENTATION_MODEL,
        "reasoning_effort": INC191_MAX_REASONING_EFFORT,
        "selection_reason": "natural_delegated_nontrivial",
        "precision_required": True,
        "deterministic_mechanical": False,
        "runtime_identity_verified": False,
    }


def _reject_sample_or_path(value: Any, *, field: str = "input") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_sample_or_path(item, field=str(key))
    elif isinstance(value, list):
        for item in value:
            _reject_sample_or_path(item, field=field)
    elif isinstance(value, str):
        lowered = value.lower()
        if field.endswith("_ref") and (Path(value).is_absolute() or ".." in Path(value).parts):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_REF_INVALID")
        if field == "observation_source" and any(marker in lowered for marker in ("fixture", "template", "example")):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_TEMPLATE_REJECTED")


def _require_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise IssueError(f"BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:{label}")
    return value


def _require_time(value: Any, label: str, latest: datetime) -> datetime:
    parsed = _parse_time(value)
    if parsed is None or parsed > latest:
        raise IssueError(f"BLOCKED_FOR_INC178_CURRENT_INPUT_TIME_INVALID:{label}")
    return parsed


def _candidate_score(row: dict[str, Any]) -> float:
    return round(
        row["user_capability_delta_score"]
        + row["blocker_delta_score"]
        - ((row["estimated_cost_ms"] + row["gate_burden_ms"]) / 3_600_000) * 0.25,
        6,
    )


def _validate_sdo_intake(value: Any, now: datetime) -> None:
    """Validate the bounded advisory SDO input without making it an authority."""
    if not isinstance(value, dict) or "project_id" not in value:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_intake")
    if not set(value).issubset(SDO_INTAKE_FIELDS) or not _nonempty(value["project_id"]):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_intake")
    for layer in SDO_LAYER_NAMES:
        if layer not in value:
            continue
        row = value[layer]
        if not isinstance(row, dict) or set(row) != SDO_LAYER_FIELDS:
            raise IssueError(f"BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_{layer}")
        if row["status"] not in SDO_LAYER_STATUSES:
            raise IssueError(f"BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_{layer}")
    knowledge = value.get("accepted_knowledge", [])
    if not isinstance(knowledge, list):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_accepted_knowledge")
    for item in knowledge:
        if not isinstance(item, dict) or set(item) != SDO_KNOWLEDGE_FIELDS:
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_knowledge_item")
        if (
            any(not _nonempty(item[key]) for key in ("knowledge_id", "project_id", "action_id", "source_ref"))
            or item["status"] not in SDO_KNOWLEDGE_STATUSES
        ):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_knowledge_item")

    brain_decisions = value.get("brain_decisions", [])
    if not isinstance(brain_decisions, list):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_brain_decisions")
    for item in brain_decisions:
        if not isinstance(item, dict) or set(item) != SDO_BRAIN_DECISION_FIELDS:
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_brain_decision")
        if (
            any(not _nonempty(item[key]) for key in SDO_BRAIN_DECISION_FIELDS)
            or item["status"] not in SDO_BRAIN_DECISION_STATUSES
            or _parse_time(item["expires_at"]) is None
        ):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_brain_decision")

    brain_identities = value.get("brain_identities", [])
    if not isinstance(brain_identities, list):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_brain_identities")
    for item in brain_identities:
        if not isinstance(item, dict) or set(item) != SDO_BRAIN_IDENTITY_FIELDS:
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_brain_identity")
        if any(not _nonempty(item[key]) for key in SDO_BRAIN_IDENTITY_FIELDS):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_brain_identity")

    fable5_records = value.get("fable5_records", [])
    if not isinstance(fable5_records, list):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_fable5_records")
    for item in fable5_records:
        if not isinstance(item, dict) or set(item) != SDO_FABLE_RECORD_FIELDS:
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_fable5_record")
        if (
            any(not _nonempty(item[key]) for key in SDO_FABLE_RECORD_FIELDS - {"rejected_alternative_summary"})
            or not _nonempty(item["rejected_alternative_summary"])
            or item["disposition"] not in SDO_FABLE_DISPOSITIONS
            or _parse_time(item["expires_at"]) is None
            or _parse_time(item["recheck_at"]) is None
        ):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_fable5_record")


def _validate_sdo_downstream_result(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != SDO_DOWNSTREAM_RESULT_FIELDS:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_downstream_result")
    if (
        value["consumer"] != "hermes_sdo_adapter"
        or value["consumed"] is not True
        or not _nonempty(value["project_id"])
        or not isinstance(value["action_changed"], bool)
        or value["decision"] not in {"CONTINUE_LOCAL", "REPLAN_NOW"}
        or not isinstance(value["model_route_changed"], bool)
        or not _hex_digest(value["receipt_digest"])
    ):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:sdo_downstream_result")


def _sdo_brain_identity_reason(intake: dict[str, Any], project_id: str) -> str:
    if not isinstance(intake, dict):
        return ""
    identities = intake.get("brain_identities", [])
    brain_status = intake.get("brain", {}).get("status", "unavailable")
    if not intake.get("brain_decisions") and brain_status != "current":
        return ""
    if len(identities) != 1:
        return "multiple_or_missing_brain_identity"
    identity = identities[0]
    expected = {
        "provider": SDO_BRAIN_PROVIDER,
        "brain_id": f"{SDO_BRAIN_PROVIDER}:{SDO_BRAIN_REPO_ID}:{project_id}",
        "project_id": project_id,
        "repo_id": SDO_BRAIN_REPO_ID,
        "canonical_file": SDO_BRAIN_CANONICAL_FILE,
        "brain_root": SDO_BRAIN_ROOT,
        "source_ref": SDO_BRAIN_SOURCE_REF,
    }
    if identity != expected:
        return "unknown_or_spoofed_brain_identity"
    return ""


def _sdo_selection(doc: dict[str, Any], evaluated_at: datetime) -> dict[str, Any]:
    """Return only bounded, project-scoped SDO influence over repo candidates."""
    intake = doc.get("sdo_intake")
    project_id = intake.get("project_id") if isinstance(intake, dict) else "UNBOUND"
    pms_status = (
        intake.get("pms", {}).get("status", "unavailable")
        if isinstance(intake, dict) and isinstance(intake.get("pms", {}), dict)
        else "unavailable"
    )
    candidate_by_id = {row["action_id"]: row for row in doc["candidates"]}
    accepted_action_ids: list[str] = []
    brain_action_ids: list[str] = []
    fable5_action_ids: list[str] = []
    rejected: list[dict[str, str]] = []
    seen_knowledge_ids: set[str] = set()
    seen_action_ids: set[str] = set()
    knowledge = intake.get("accepted_knowledge", []) if isinstance(intake, dict) else []
    for item in knowledge:
        knowledge_id = item["knowledge_id"]
        action_id = item["action_id"]
        if knowledge_id in seen_knowledge_ids:
            rejected.append({"knowledge_id": knowledge_id, "action_id": action_id, "reason": "duplicate_knowledge_id"})
            continue
        if action_id in seen_action_ids:
            rejected.append({"knowledge_id": knowledge_id, "action_id": action_id, "reason": "duplicate_action_id"})
            seen_knowledge_ids.add(knowledge_id)
            continue
        seen_knowledge_ids.add(knowledge_id)
        seen_action_ids.add(action_id)
        reason = ""
        if item["status"] != "accepted":
            reason = "knowledge_not_accepted"
        elif pms_status != "available":
            reason = "pms_unavailable_or_not_accepted"
        elif not item["source_ref"].startswith("pms:"):
            reason = "source_not_pms_accepted"
        elif item["project_id"] != project_id:
            reason = "cross_project"
        elif action_id not in candidate_by_id:
            reason = "repo_truth_candidate_missing"
        elif candidate_by_id[action_id]["operation_type"] not in {"product_operation", "cause_repair"}:
            reason = "non_local_candidate"
        if reason:
            rejected.append({"knowledge_id": knowledge_id, "action_id": action_id, "reason": reason})
        else:
            accepted_action_ids.append(action_id)
    brain_decisions = intake.get("brain_decisions", []) if isinstance(intake, dict) else []
    brain_status = (
        intake.get("brain", {}).get("status", "unavailable")
        if isinstance(intake, dict) and isinstance(intake.get("brain", {}), dict)
        else "unavailable"
    )
    brain_identity_reason = _sdo_brain_identity_reason(intake, project_id)
    seen_brain_ids: set[str] = set()
    seen_brain_action_ids: set[str] = set()
    for item in brain_decisions:
        decision_id = item["decision_id"]
        action_id = item["action_id"]
        if brain_identity_reason:
            rejected.append({"knowledge_id": decision_id, "action_id": action_id, "reason": "brain_identity_invalid"})
            continue
        if decision_id in seen_brain_ids:
            rejected.append({"knowledge_id": decision_id, "action_id": action_id, "reason": "duplicate_brain_decision_id"})
            continue
        if action_id in seen_brain_action_ids:
            rejected.append({"knowledge_id": decision_id, "action_id": action_id, "reason": "duplicate_brain_action_id"})
            seen_brain_ids.add(decision_id)
            continue
        seen_brain_ids.add(decision_id)
        seen_brain_action_ids.add(action_id)
        reason = ""
        if item["status"] != "current":
            reason = "brain_decision_not_current"
        elif brain_status != "current":
            reason = "brain_unavailable_or_not_current"
        elif item["source_ref"] != SDO_BRAIN_SOURCE_REF:
            reason = "source_not_canonical_brain"
        elif item["project_id"] != project_id:
            reason = "cross_project_brain_decision"
        elif _parse_time(item["expires_at"]) is None or _parse_time(item["expires_at"]) <= evaluated_at:
            reason = "expired_brain_decision"
        elif action_id not in candidate_by_id:
            reason = "repo_truth_candidate_missing"
        elif candidate_by_id[action_id]["operation_type"] not in {"product_operation", "cause_repair"}:
            reason = "non_local_candidate"
        if reason:
            rejected.append({"knowledge_id": decision_id, "action_id": action_id, "reason": reason})
        else:
            brain_action_ids.append(action_id)
    if len(set(brain_action_ids)) > 1:
        for action_id in sorted(set(brain_action_ids)):
            rejected.append({
                "knowledge_id": action_id,
                "action_id": action_id,
                "reason": "brain_identity_invalid",
            })
        brain_action_ids = []
        brain_identity_reason = "conflicting_brain_records"

    fable5_records = intake.get("fable5_records", []) if isinstance(intake, dict) else []
    fable5_status = (
        intake.get("fable5", {}).get("status", "unavailable")
        if isinstance(intake, dict) and isinstance(intake.get("fable5", {}), dict)
        else "unavailable"
    )
    seen_fable_ids: set[str] = set()
    seen_fable_action_ids: set[str] = set()
    for item in fable5_records:
        record_id = item["record_id"]
        action_id = item["action_id"]
        if record_id in seen_fable_ids:
            rejected.append({"knowledge_id": record_id, "action_id": action_id, "reason": "duplicate_fable_record_id"})
            continue
        if action_id in seen_fable_action_ids:
            rejected.append({"knowledge_id": record_id, "action_id": action_id, "reason": "duplicate_fable_action_id"})
            seen_fable_ids.add(record_id)
            continue
        seen_fable_ids.add(record_id)
        seen_fable_action_ids.add(action_id)
        reason = ""
        if item["disposition"] != "admitted":
            reason = "fable_record_not_admitted"
        elif fable5_status != "admitted":
            reason = "fable5_unavailable_or_not_admitted"
        elif not item["source_ref"].startswith("fable5:"):
            reason = "source_not_fable5_ssot"
        elif item["project_id"] != project_id:
            reason = "cross_project_fable_record"
        elif _parse_time(item["expires_at"]) is None or _parse_time(item["expires_at"]) <= evaluated_at:
            reason = "expired_fable_record"
        elif _parse_time(item["recheck_at"]) is None or _parse_time(item["recheck_at"]) <= evaluated_at:
            reason = "fable_record_recheck_due"
        elif action_id not in candidate_by_id:
            reason = "repo_truth_candidate_missing"
        elif candidate_by_id[action_id]["operation_type"] not in {"product_operation", "cause_repair"}:
            reason = "non_local_candidate"
        if reason:
            rejected.append({"knowledge_id": record_id, "action_id": action_id, "reason": reason})
        else:
            fable5_action_ids.append(action_id)
    pms_action_ids = sorted(set(accepted_action_ids))
    brain_action_ids = sorted(set(brain_action_ids))
    fable5_action_ids = sorted(set(fable5_action_ids))
    if brain_action_ids:
        selection_layer = "brain"
        precedence_action_ids = brain_action_ids
    elif pms_action_ids:
        selection_layer = "pms"
        precedence_action_ids = pms_action_ids
    elif fable5_action_ids:
        selection_layer = "fable5"
        precedence_action_ids = fable5_action_ids
    else:
        selection_layer = "repo_truth"
        precedence_action_ids = []
    precedence_set = set(precedence_action_ids)
    for layer_name, action_ids in (
        ("brain", brain_action_ids),
        ("pms", pms_action_ids),
        ("fable5", fable5_action_ids),
    ):
        if layer_name == selection_layer:
            continue
        for action_id in sorted(set(action_ids) - precedence_set):
            rejected.append({
                "knowledge_id": action_id,
                "action_id": action_id,
                "reason": f"lower_precedence_than_{selection_layer}",
            })
    brain_identity_status = "canonical_single"
    if not brain_decisions and brain_status != "current":
        brain_identity_status = "unavailable"
    elif brain_identity_reason:
        brain_identity_status = "zero_influence"
    return {
        "project_id": project_id,
        "pms_status": pms_status,
        "brain_status": brain_status,
        "fable5_status": fable5_status,
        "accepted_action_ids": sorted(set(accepted_action_ids + brain_action_ids + fable5_action_ids)),
        "precedence_action_ids": precedence_action_ids,
        "selection_layer": selection_layer,
        "brain_action_ids": brain_action_ids,
        "fable5_action_ids": fable5_action_ids,
        "brain_identity_status": brain_identity_status,
        "brain_identity_reason": brain_identity_reason,
        "rejected": sorted(rejected, key=lambda row: (row["knowledge_id"], row["action_id"], row["reason"])),
    }


def _candidate_rows(doc: dict[str, Any], influenced_action_ids: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    candidates = []
    for source in doc["candidates"]:
        row = {key: source[key] for key in (
            "action_id", "operation_type", "user_capability_delta_score", "blocker_delta_score", "estimated_cost_ms", "gate_burden_ms",
        )}
        if row["action_id"] in influenced_action_ids:
            row["user_capability_delta_score"] += SDO_ACCEPTED_ACTION_BONUS
        row["action_class"] = _derived_action_class(row)
        for key in ("repository_class", "task_class", "operation_class"):
            if key in source:
                row[key] = source[key]
        candidates.append(row)
    return candidates


def _sdo_readback(doc: dict[str, Any], selected_action_id: str, evaluated_at: datetime) -> dict[str, Any]:
    intake = doc.get("sdo_intake") if isinstance(doc.get("sdo_intake"), dict) else {}
    selection = _sdo_selection(doc, evaluated_at)
    base_candidates = _candidate_rows(doc)
    base_scored = sorted(
        ((_candidate_score(row), row["action_id"]) for row in base_candidates),
        key=lambda item: (-item[0], item[1]),
    )
    base_selected_action_id = base_scored[0][1]
    selected_sources = []
    if selected_action_id in selection["precedence_action_ids"]:
        selected_sources.append({
            "brain": "current_brain_decision",
            "pms": "accepted_pms_knowledge",
            "fable5": "admitted_fable5_record",
        }[selection["selection_layer"]])
    selected_by = "project_bound_" + "+".join(selected_sources) if selected_sources else "repo_truth_rank"
    layers: dict[str, dict[str, Any]] = {}
    consulted_refs: dict[str, list[str]] = {
        "repo_truth": ["binding.head_ref", "candidates", "current_transition"],
        "accepted_knowledge": [],
    }
    for layer in SDO_LAYER_NAMES:
        status = (
            intake.get(layer, {}).get("status", "unavailable")
            if isinstance(intake.get(layer, {}), dict)
            else "unavailable"
        )
        available = status != "unavailable"
        layers[layer] = {
            "status": status,
            "blocking": False,
            "consulted_ref": f"typed_input:sdo_intake.{layer}" if available else "UNAVAILABLE",
            "adapter": "advisory_read_only" if available else "typed_unavailable_nonblocking",
        }
        consulted_refs[layer] = [layers[layer]["consulted_ref"]]
    if intake.get("accepted_knowledge"):
        consulted_refs["accepted_knowledge"] = ["typed_input:sdo_intake.accepted_knowledge"]
    if intake.get("brain_decisions"):
        consulted_refs["brain_decisions"] = ["typed_input:sdo_intake.brain_decisions"]
    if intake.get("brain_identities"):
        consulted_refs["brain_identities"] = ["typed_input:sdo_intake.brain_identities"]
    if intake.get("fable5_records"):
        consulted_refs["fable5_records"] = ["typed_input:sdo_intake.fable5_records"]
    action_changed = selected_action_id != base_selected_action_id
    return {
        "schema_version": "sdo_intake_readback.v1",
        "project_id": selection["project_id"],
        "consulted_refs": consulted_refs,
        "layer_status": layers,
        "base_selected_action_id": base_selected_action_id,
        "selected_action_id": selected_action_id,
        "selected_by": selected_by,
        "selection_layer": selection["selection_layer"],
        "precedence_action_ids": selection["precedence_action_ids"],
        "accepted_influence_count": len(selection["precedence_action_ids"]),
        "accepted_action_ids": selection["accepted_action_ids"],
        "brain_influence_count": len(selection["brain_action_ids"]),
        "fable5_influence_count": len(selection["fable5_action_ids"]),
        "brain_action_ids": selection["brain_action_ids"],
        "fable5_action_ids": selection["fable5_action_ids"],
        "brain_identity_status": selection["brain_identity_status"],
        "brain_identity_reason": selection["brain_identity_reason"],
        "rejected_alternatives": selection["rejected"],
        "capability_delta": "selected_action_changed" if action_changed else "no_action_change",
        "safe_local_continuation": True,
        "protected_transition_gate": "exact_existing_authority_gate",
        "expiry": "current_transition",
        "rollback": "discard_sdo_influence_and_reissue_from_repo_truth",
        "non_claims": [
            "no_sdo_authority_over_repo_truth",
            "no_pms_or_odg_runtime_claim",
            "no_brain_runtime_adapter_or_fresh_session_claim",
            "no_fable5_runtime_or_user_acceptance_claim",
        ],
    }


def _sdo_replan_reasons(
    transition: dict[str, Any],
    cmd_prevention: dict[str, Any] | None = None,
) -> list[str]:
    allowed = {
        "two_cause_changing_corrections",
        "ninety_minutes_without_capability_delta",
        "wrong_lane",
        "write_set_change",
        "blocker_change",
        "authority_boundary_change",
    }
    source = (
        cmd_prevention.get("trigger_reasons", [])
        if isinstance(cmd_prevention, dict)
        else transition.get("replan", {}).get("trigger_reasons", [])
    )
    aliases = {
        "target_or_write_set_change": "write_set_change",
        "wrong_lane_or_write_set": "write_set_change",
        "wrong_target_owner_routing": "wrong_lane",
    }
    reasons = {
        aliases.get(reason, reason)
        for reason in source
        if aliases.get(reason, reason) in allowed
    }
    for warning in transition.get("counters", {}).get("user_warning_events", []):
        if not isinstance(warning, dict):
            continue
        mapped = aliases.get(warning.get("warning_class", ""), warning.get("warning_class", ""))
        if mapped in {"wrong_lane", "write_set_change", "blocker_change", "authority_boundary_change"}:
            reasons.add(mapped)
    visible_zero = (
        transition.get("progress_deltas", {})
        .get("user_visible_capability_delta", {})
        .get("classification") == "zero"
    )
    elapsed = transition.get("whole_goal", {}).get("whole_goal_elapsed_ms", -1)
    if visible_zero and isinstance(elapsed, int) and elapsed >= SDO_NO_CAPABILITY_DELTA_REPLAN_MS:
        reasons.add("ninety_minutes_without_capability_delta")
    return sorted(reasons)


def _sdo_decision_receipt(
    doc: dict[str, Any],
    transition: dict[str, Any],
    *,
    base_dir: Path,
    model_routing: dict[str, Any],
    authority_transition: dict[str, Any],
    cmd_prevention: dict[str, Any] | None = None,
    pre_dispatch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_action_id = transition["action_selection"]["selected_action_id"]
    sdo = _sdo_readback(
        doc,
        selected_action_id,
        _parse_time(transition["whole_goal"]["observed_at"]) or _utc_now(),
    )
    selected_action = next(
        candidate for candidate in doc["candidates"] if candidate["action_id"] == selected_action_id
    )
    cost = (
        pre_dispatch.get("cost_telemetry", {})
        if isinstance(pre_dispatch, dict)
        else {}
    )
    fast_mode = _luna_fast_mode_policy(
        cost.get("discount_eligibility"),
        str(model_routing["selection_reason"]),
    )
    replan_reason = _sdo_replan_reasons(transition, cmd_prevention)
    decision = "REPLAN_NOW" if replan_reason else "CONTINUE_LOCAL"
    observed_at = transition["whole_goal"]["observed_at"]
    candidate_actions = _candidate_rows(
        doc,
        set(_sdo_selection(doc, _parse_time(observed_at) or _utc_now())["precedence_action_ids"]),
    )
    rejected_by_action = {
        row["action_id"]: row["reason"]
        for row in transition["action_selection"]["rejected_actions"]
    }
    selected_alternatives = [
        {
            "action_id": row["action_id"],
            "action_class": row["action_class"],
            "status": "selected" if row["action_id"] == selected_action_id else "rejected",
            "reason": "selected" if row["action_id"] == selected_action_id else rejected_by_action.get(row["action_id"], "lower_repo_rank"),
        }
        for row in candidate_actions
    ]
    user_delta = doc["progress_deltas"]["user_visible_capability_delta"]
    blocker_delta = doc["progress_deltas"]["blocker_knowledge_delta"]
    correction_count = (
        cmd_prevention.get("cause_changing_correction_count")
        if isinstance(cmd_prevention, dict)
        else doc["counters"]["user_correction_count"]
    )
    if not isinstance(correction_count, int):
        correction_count = doc["counters"]["user_correction_count"]
    def known_cost(field: str) -> int | str:
        return cost[field] if isinstance(cost.get(field), int) else "UNKNOWN"

    receipt = {
        "schema_version": SDO_RECEIPT_VERSION,
        "display_identity": SDO_DISPLAY_IDENTITY,
        "historical_aliases": list(SDO_HISTORICAL_ALIASES),
        "consulted_refs": {
            **sdo["consulted_refs"],
            "repo_control": ["scripts/ops/mk_whole_goal_control.py"],
            "routing_policy": ["controls/routing-table.json"],
        },
        "repo_facts": {
            "goal_ref": doc["binding"]["goal_ref"],
            "phase_ref": doc["binding"]["phase_ref"],
            "head_ref": doc["binding"]["head_ref"],
            "blocker_fingerprint": doc["binding"]["blocker_fingerprint"],
            "work_class": doc["work_class"],
            "transition": doc["transition"],
            "selected_action_source": "repo_candidates",
        },
        "project_id": sdo["project_id"],
        "repo_candidate_set": {
            "action_ids": sorted(row["action_id"] for row in doc["candidates"]),
            "digest": _canonical_digest({
                "action_ids": sorted(row["action_id"] for row in doc["candidates"]),
            }),
        },
        "layer_status": sdo["layer_status"],
        "base_selected_action_id": sdo["base_selected_action_id"],
        "selected_action_id": selected_action_id,
        "selected_alternatives": selected_alternatives,
        "rejected_alternatives": [
            *sdo["rejected_alternatives"],
            *transition["action_selection"]["rejected_actions"],
        ],
        "capability_delta": {
            "classification": user_delta["classification"],
            "summary": user_delta["summary"],
            "action_changed": selected_action_id != sdo["base_selected_action_id"],
            "selection_reason": sdo["selected_by"],
        },
        "blocker_delta": {
            "classification": doc["whole_goal"]["local_blocker_delta"],
            "summary": doc["whole_goal"]["current_biggest_blocker"],
            "knowledge_classification": blocker_delta["classification"],
            "knowledge_summary": blocker_delta["summary"],
        },
        "model_route": {
            "provider": model_routing.get("provider", "UNKNOWN"),
            "model": model_routing["model"],
            "reasoning_effort": model_routing["reasoning_effort"],
            "selection_reason": model_routing["selection_reason"],
            "runtime_identity_verified": model_routing.get("runtime_identity_verified", False),
            "fast_mode": {
                "selected": fast_mode["fast_mode_selected"],
                "selection_reason": fast_mode["fast_mode_selection_reason"],
                "service_tier_preference": fast_mode["service_tier_preference"],
                "runtime_verified": fast_mode["service_tier_runtime_verified"],
                "claim_withheld": fast_mode["fast_mode_claim_withheld"],
            },
        },
        "cost_telemetry": {
            "raw_input_tokens": known_cost("raw_input_tokens"),
            "cached_input_tokens": known_cost("cached_input_tokens"),
            "output_tokens": known_cost("output_tokens"),
            "reasoning_tokens": known_cost("reasoning_tokens"),
            "elapsed_ms": known_cost("elapsed_ms"),
            "first_pass_result": cost.get("first_pass_result", "UNKNOWN"),
            "rework_count": known_cost("rework_count"),
            "scope_deviation_count": known_cost("scope_deviation_count"),
            "discount_eligibility": cost.get("discount_eligibility", "UNKNOWN"),
            "billing_telemetry_authoritative": cost.get("billing_telemetry_authoritative", False),
            "effective_billed_cost": cost.get("effective_billed_cost", "UNKNOWN"),
        },
        "cumulative_work": {
            "elapsed_ms": transition["whole_goal"]["whole_goal_elapsed_ms"],
            "support_work_elapsed_ms": doc["time_accounting"]["support_work_elapsed_ms"],
            "rework_count": known_cost("rework_count"),
            "correction_count": correction_count,
            "cause_changing_correction_count": correction_count,
            "scope_deviation_count": known_cost("scope_deviation_count"),
        },
        "decision": decision,
        "safe_local_continuation": True,
        "replan_reason": replan_reason,
        "thresholds": {
            "no_capability_delta_replan_ms": SDO_NO_CAPABILITY_DELTA_REPLAN_MS,
            "cause_changing_corrections": 2,
            "elapsed_only_stop_allowed": False,
        },
        "protected_transition": {
            "requested": authority_transition.get("requested", False),
            "allowed": authority_transition.get("allowed", False),
            "execution_authorized": authority_transition.get("execution_authorized", False),
            "reason": authority_transition.get("reason", "PROTECTED_TRANSITION_NOT_REQUESTED"),
        },
        "expiry": {
            "scope": "current_transition",
            "expires_at": (
                doc.get("protected_transition_request", {}).get("expires_at")
                if isinstance(doc.get("protected_transition_request"), dict)
                else _stamp(
                    (_parse_time(observed_at) or _utc_now())
                    + timedelta(seconds=SDO_CLAIM_TTL_SECONDS)
                )
            ),
            "claim_ttl_seconds": SDO_CLAIM_TTL_SECONDS,
        },
        "rollback": {
            "action": "reissue_from_repo_truth",
            "sdo_influence": "discard_current_advisory_influence",
            "protected_transition": "retain_exact_existing_authority_gate",
        },
        "nonclaims": sorted(set(REQUIRED_NON_CLAIMS) | {
            "no_brain_runtime_adapter_or_fresh_session_claim",
            "no_pms_or_odg_runtime_claim",
            "no_fable5_runtime_or_user_acceptance_claim",
            "no_authoritative_billing_without_billing_telemetry",
        }),
        "owner": doc["audit_integration"]["implementation_owner_thread_id"],
        "source_consumer": "scripts/ops/issue_inc178_current_transition.py",
        "selected_action_operation_type": selected_action["operation_type"],
        "next_operation": (
            cmd_prevention.get("next_operation") or selected_action_id
            if isinstance(cmd_prevention, dict)
            else selected_action_id
        ),
        "receipt_digest": (
            cmd_prevention.get("receipt_digest", "")
            if isinstance(cmd_prevention, dict)
            else ""
        ),
        "receipt_consumed": (
            cmd_prevention.get("receipt_consumed") is True
            if isinstance(cmd_prevention, dict)
            else False
        ),
        "evaluated_at": observed_at,
        "receipt_expiry_is_authority": False,
        "support_work_progress_credit": 0,
    }
    return _seal_sdo_receipt(
        receipt,
        receipt_consumed=(
            cmd_prevention.get("receipt_consumed") is True
            if isinstance(cmd_prevention, dict)
            else False
        ),
        consumed_by=(
            cmd_prevention.get("consumed_by", "")
            if isinstance(cmd_prevention, dict)
            else ""
        ),
    )


def _validate_input(
    value: Any,
    base_dir: Path,
    now: datetime,
    *,
    observed_task_root: Path | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not INPUT_FIELDS.issubset(value)
        or not set(value).issubset(INPUT_FIELDS | OPTIONAL_INPUT_FIELDS)
    ):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:root")
    doc = value
    _reject_sample_or_path(doc)
    if "sdo_intake" in doc:
        _validate_sdo_intake(doc["sdo_intake"], now)
    if "sdo_downstream_result" in doc:
        _validate_sdo_downstream_result(doc["sdo_downstream_result"])
    if "sdo_decision_receipt" in doc:
        _validate_sdo_decision_receipt(doc["sdo_decision_receipt"])
    if "sdo_route_receipt" in doc:
        _validate_sdo_route_receipt(doc["sdo_route_receipt"])
    if "cmd_epoch_request" in doc and not validate_cmd_epoch_request(doc["cmd_epoch_request"]):
        raise IssueError("BLOCKED_FOR_INC178_CMD_EPOCH_SCHEMA_INVALID")
    if "return_event" in doc:
        event = doc["return_event"]
        request = (
            event
            if isinstance(event, dict) and "operation" in event
            else ({"operation": "return", **event} if isinstance(event, dict) else None)
        )
        if not validate_cmd_epoch_request(request):
            raise IssueError("BLOCKED_FOR_INC178_CMD_EPOCH_SCHEMA_INVALID")
    if doc["schema_version"] != INPUT_VERSION or not _nonempty(doc["observation_source"]):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:identity")
    if doc["work_class"] in EXEMPT_WORK_CLASSES:
        raise IssueError("BLOCKED_FOR_INC178_NORMAL_SUPERVISED_SCOPE_NOT_REQUIRED")
    if doc["work_class"] not in {"delegated_nontrivial", "cross_repo_phase", "external_wait"}:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCOPE_INVALID")
    if doc["transition"] not in TRANSITIONS:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:transition")

    binding = _require_fields(doc["binding"], {"goal_ref", "phase_ref", "head_ref", "blocker_fingerprint"}, "binding")
    if any(not _nonempty(binding[key]) for key in binding):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:binding")
    observed_root = observed_task_root if _is_natural_observation(doc) and observed_task_root else base_dir
    if binding["head_ref"] != _git_head(observed_root):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_HEAD_MISMATCH")
    whole = _require_fields(doc["whole_goal"], {
        "started_at", "estimate_range_ms", "expected_completion_max_ms", "active_elapsed_source",
        "local_blocker_delta", "current_biggest_blocker",
    }, "whole_goal")
    started = _require_time(whole["started_at"], "started_at", now)
    estimate_range = _require_fields(whole["estimate_range_ms"], {"min_ms", "max_ms"}, "estimate_range_ms")
    if (
        not _is_int(estimate_range["min_ms"])
        or not _is_int(estimate_range["max_ms"])
        or estimate_range["min_ms"] > estimate_range["max_ms"]
        or not _is_int(whole["expected_completion_max_ms"], positive=True)
        or whole["active_elapsed_source"] not in {"measured", "bounded_estimate", "user_corrected_estimate"}
        or whole["local_blocker_delta"] not in {"positive", "zero", "unknown"}
        or not _nonempty(whole["current_biggest_blocker"])
    ):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:whole_goal")

    progress = _require_fields(doc["progress_deltas"], {
        "blocker_knowledge_delta", "runtime_milestone_delta", "user_visible_capability_delta",
    }, "progress_deltas")
    for key in ("blocker_knowledge_delta", "runtime_milestone_delta"):
        row = _require_fields(progress[key], {"classification", "summary"}, key)
        if row["classification"] not in {"positive", "zero", "unknown"} or not _nonempty(row["summary"]):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:progress")
    user_delta = progress["user_visible_capability_delta"]
    user_delta_fields = {"classification", "summary", "normal_user_operation_observed"}
    if (
        not isinstance(user_delta, dict)
        or not user_delta_fields.issubset(user_delta)
        or not set(user_delta).issubset(user_delta_fields | {"transition_id"})
    ):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:progress")
    if (
        user_delta["classification"] not in {"positive", "zero", "unknown"}
        or not _nonempty(user_delta["summary"])
        or not isinstance(user_delta["normal_user_operation_observed"], bool)
        or (user_delta["classification"] == "positive" and not user_delta["normal_user_operation_observed"])
        or ("transition_id" in user_delta and not _nonempty(user_delta["transition_id"]))
        or (
            user_delta["classification"] == "positive"
            and user_delta["normal_user_operation_observed"]
            and not _nonempty(user_delta.get("transition_id"))
        )
    ):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:progress")

    time_accounting = _require_fields(doc["time_accounting"], {
        "support_work_elapsed_ms", "authority_gate_wait_ms", "claim_check_support_ms",
    }, "time_accounting")
    if any(not _is_int(time_accounting[key]) for key in time_accounting):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:time_accounting")

    counters = _require_fields(doc["counters"], {
        "chained_implementation_blocks", "consecutive_zero_visible_delta_slices",
        "distinct_causal_blocker_count", "protected_mutation_or_pair_count",
        "evidence_only_slice_count", "user_correction_count", "user_warning_events",
        "warning_count_source", "user_relay_count", "idle_after_partial",
        "return_decide_dispatch_elapsed_ms", "false_block_count", "missed_block_count",
        "avoidable_model_cost_count", "product_decision_changed_count",
    }, "counters")
    integer_counters = set(counters) - {"user_warning_events", "warning_count_source", "idle_after_partial"}
    if any(not _is_int(counters[key]) for key in integer_counters) or not _nonempty(counters["warning_count_source"]) or not isinstance(counters["idle_after_partial"], bool):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:counters")
    if not isinstance(counters["user_warning_events"], list):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:warning_events")
    for event in counters["user_warning_events"]:
        row = _require_fields(event, {"warning_class", "count", "first_observed_at", "last_observed_at", "source_ref"}, "warning_event")
        first = _require_time(row["first_observed_at"], "warning_first", now)
        last = _require_time(row["last_observed_at"], "warning_last", now)
        if not _nonempty(row["warning_class"]) or not _is_int(row["count"], positive=True) or not _nonempty(row["source_ref"]) or last < first:
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:warning_event")

    skills = _require_fields(doc["skill_firing"], {"expected_skills", "invocation_records", "nonfire_reason", "skill_surface_state"}, "skill_firing")
    if not isinstance(skills["expected_skills"], list) or not skills["expected_skills"] or len(set(skills["expected_skills"])) != len(skills["expected_skills"]) or any(not _nonempty(skill) for skill in skills["expected_skills"]):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:expected_skills")
    if skills["nonfire_reason"] not in NONFIRE_REASONS or not isinstance(skills["invocation_records"], list):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:skill_firing")
    state = _require_fields(skills["skill_surface_state"], {
        "canonical_source_state", "plugin_distribution_state", "plugin_cache_diagnostic_state",
        "unprefixed_skill_root_state", "active_resolution_root_state",
    }, "skill_surface_state")
    if any(not _nonempty(item) for item in state.values()):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:skill_surface_state")
    if skills["invocation_records"]:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_INVOCATION_PROVENANCE_UNSUPPORTED")

    candidates = doc["candidates"]
    if not isinstance(candidates, list) or len(candidates) < 3:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:candidates")
    candidate_ids: list[str] = []
    for candidate in candidates:
        candidate_fields = {
            "action_id", "operation_type", "user_capability_delta_score", "blocker_delta_score",
            "estimated_cost_ms", "gate_burden_ms", "next_action", "rejected_reason",
            "product_path_simplified_or_unnecessary_gate_removed", "cause_changing_repair",
            "classified_as_unchanged_retry", "dependency_map_reviewed",
            "pin_or_provenance_only_fast_path_eligible", "fast_path_used",
        }
        candidate_optional_fields = {"repository_class", "task_class", "operation_class"}
        if (
            not isinstance(candidate, dict)
            or not candidate_fields <= set(candidate)
            or not set(candidate) <= candidate_fields | candidate_optional_fields
        ):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:candidate")
        row = candidate
        if (
            not _nonempty(row["action_id"]) or row["operation_type"] not in ACTION_CLASS_BY_OPERATION_TYPE
            or not isinstance(row["user_capability_delta_score"], int) or not isinstance(row["blocker_delta_score"], int)
            or not _is_int(row["estimated_cost_ms"]) or not _is_int(row["gate_burden_ms"])
            or not _nonempty(row["next_action"]) or not _nonempty(row["rejected_reason"])
            or any(not isinstance(row[key], bool) for key in (
                "product_path_simplified_or_unnecessary_gate_removed", "cause_changing_repair",
                "classified_as_unchanged_retry", "dependency_map_reviewed",
                "pin_or_provenance_only_fast_path_eligible", "fast_path_used",
            ))
            or any(
                key in row and not _nonempty(row[key])
                for key in candidate_optional_fields
            )
        ):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:candidate")
        if row["cause_changing_repair"] and row["classified_as_unchanged_retry"]:
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:candidate")
        candidate_ids.append(row["action_id"])
    if len(set(candidate_ids)) != len(candidate_ids) or doc["before_selected_action_id"] not in candidate_ids:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_ACTION_BINDING_INVALID")

    audit = _require_fields(doc["audit_integration"], {
        "implementation_owner_thread_id", "subagent_lanes_exist", "fourth_oversight_present",
        "fourth_oversight_self_demoted", "audit_records",
    }, "audit_integration")
    if not _nonempty(audit["implementation_owner_thread_id"]) or not isinstance(audit["audit_records"], list) or any(not isinstance(audit[key], bool) for key in ("subagent_lanes_exist", "fourth_oversight_present", "fourth_oversight_self_demoted")):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:audit")
    if audit["audit_records"]:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_AUDIT_PROVENANCE_UNSUPPORTED")

    gate = _require_fields(doc["gate_burden"], {"budget_ms", "avoidable_model_cost_ms", "inventory"}, "gate_burden")
    if not _is_int(gate["budget_ms"], positive=True) or not _is_int(gate["avoidable_model_cost_ms"]) or not isinstance(gate["inventory"], list):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:gate_burden")
    for row in gate["inventory"]:
        fields = {"control_id", "gate_class", "protected_asset", "hazard", "owner", "trigger", "scope", "metric", "expiry", "elapsed_ms", "changed_action"}
        _require_fields(row, fields, "gate_inventory")
        if (
            row["gate_class"] not in {"Authority Gate", "Claim Check", "support prerequisite"}
            or any(not _nonempty(row[key]) for key in ("control_id", "owner", "trigger", "scope", "metric", "expiry"))
            or not _is_int(row["elapsed_ms"]) or not isinstance(row["changed_action"], bool)
            or (row["gate_class"] == "Authority Gate" and (not _nonempty(row["protected_asset"]) or not _nonempty(row["hazard"])))
        ):
            raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:gate_inventory")
    if _require_fields(doc["recent_activity"], {"source", "ci", "audit"}, "recent_activity") and any(not isinstance(item, bool) for item in doc["recent_activity"].values()):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:recent_activity")
    terminal = _require_fields(doc["terminal_continuation"], TERMINAL_CONTINUATION_FIELDS, "terminal_continuation")
    blocker = _require_fields(terminal["progress_blocker"], TERMINAL_PROGRESS_BLOCKER_FIELDS, "terminal_progress_blocker")
    if (
        any(not isinstance(terminal[key], bool) for key in (
            "terminal_result_consumed", "protected_adoption_held",
            "bounded_local_repair_dispatchable", "quiet_closeout_requested",
            "current_transition_checker_invoked", "selected_action_result_consumed",
            "control_dispatch_sent", "target_readback_received",
        ))
        or terminal["primary_state"] not in {"active", "inProgress", "idle", "notLoaded"}
        or terminal["control_dispatch_mode"] not in {"", "control_dispatch"}
        or any(not isinstance(terminal[key], str) for key in ("dispatch_target_thread_id", "target_readback_marker"))
        or not isinstance(blocker["present"], bool)
        or any(not isinstance(blocker[key], str) for key in ("blocker_id", "summary", "owner", "unblock_condition"))
    ):
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_SCHEMA_INVALID:terminal_continuation")
    return doc


def build_transition(
    doc: dict[str, Any],
    base_dir: Path,
    now: datetime,
    *,
    additional_replan_reasons: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    whole_input = doc["whole_goal"]
    binding_input = doc["binding"]
    elapsed = int((now - _parse_time(whole_input["started_at"])).total_seconds() * 1000)
    estimate_range = whole_input["estimate_range_ms"]
    active_estimate = min(max(elapsed, estimate_range["min_ms"]), estimate_range["max_ms"])
    expected_max = whole_input["expected_completion_max_ms"]
    ratio = round(elapsed / expected_max, 6)
    sdo_selection = _sdo_selection(doc, now)
    candidates = _candidate_rows(doc, set(sdo_selection["precedence_action_ids"]))
    scored = sorted((( _candidate_score(row), row["action_id"], row) for row in candidates), key=lambda item: (-item[0], item[1]))
    selected = scored[0][2]
    if selected["action_class"] == "evidence_only":
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_EVIDENCE_ACTION_SELECTED")
    if doc["before_selected_action_id"] == selected["action_id"]:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_ACTION_BINDING_INVALID")
    source_by_id = {row["action_id"]: row for row in doc["candidates"]}
    selected_source = source_by_id[selected["action_id"]]
    observed = _stamp(now)
    entrypoint_ref = "scripts/ops/mk_whole_goal_control.py"
    entrypoint_digest = _sha(base_dir / entrypoint_ref)
    fired_records: list[dict[str, Any]] = []
    fired_skills: list[str] = []
    nonfires = [{"skill": skill, "reason": doc["skill_firing"]["nonfire_reason"]} for skill in doc["skill_firing"]["expected_skills"]]
    warning_counts: dict[str, int] = {}
    for event in doc["counters"]["user_warning_events"]:
        warning_counts[event["warning_class"]] = warning_counts.get(event["warning_class"], 0) + event["count"]
    transition_progress_deltas = {
        **doc["progress_deltas"],
        "user_visible_capability_delta": {
            key: value
            for key, value in doc["progress_deltas"]["user_visible_capability_delta"].items()
            if key != "transition_id"
        },
    }
    transition = {
        "contract_version": CONTRACT_VERSION,
        "work_class": doc["work_class"],
        "transition": doc["transition"],
        "decision_binding": {
            **binding_input,
            "selected_action_id": selected["action_id"],
            "evaluated_at": observed,
            "source_ref": doc["observation_source"],
            "live_state_matches": True,
        },
        "whole_goal": {
            "goal_ref": binding_input["goal_ref"], "started_at": whole_input["started_at"], "observed_at": observed,
            "whole_goal_elapsed_ms": elapsed, "active_elapsed_estimate_ms": active_estimate,
            "active_elapsed_source": whole_input["active_elapsed_source"], "estimate_range_ms": estimate_range,
            "expected_completion_max_ms": expected_max, "estimate_error_ratio": ratio,
            "phase_estimate_status": "unknown",
            "whole_goal_estimate_status": "overdue" if ratio > 1 else "green",
            "local_blocker_delta": whole_input["local_blocker_delta"], "current_biggest_blocker": whole_input["current_biggest_blocker"],
        },
        "progress_deltas": transition_progress_deltas,
        "thresholds": THRESHOLDS,
        "time_accounting": {
            **doc["time_accounting"],
            "support_work_ratio": round(doc["time_accounting"]["support_work_elapsed_ms"] / elapsed, 6) if elapsed else 0,
        },
        "counters": {
            **doc["counters"],
            "warning_category_counts_overlap": True,
            "repeated_warning_classes": [
                {"warning_class": key, "count": count} for key, count in sorted(warning_counts.items()) if count >= 2
            ],
        },
        "skill_firing": {
            "expected_skills": doc["skill_firing"]["expected_skills"], "fired_skills": fired_skills,
            "non_fires": nonfires, "invocation_records": fired_records,
            "skill_ecosystem_repair_required": bool(nonfires),
            "skill_surface_state": {**doc["skill_firing"]["skill_surface_state"], "presence_is_invocation_evidence": False, "invocation_is_result_consumption": False},
        },
        "action_selection": {
            "candidate_actions": candidates, "selected_action_id": selected["action_id"], "selected_action_class": selected["action_class"],
            "rejected_actions": [{"action_id": row["action_id"], "reason": source_by_id[row["action_id"]]["rejected_reason"]} for row in candidates if row["action_id"] != selected["action_id"]],
            "quantified_best_action_rationale": {"selected_score": scored[0][0], "next_best_score": scored[1][0], "user_value_weight": 1.0, "cost_weight": 0.25},
            "support_work_progress_credit": 0, "next_action": selected_source["next_action"], "audit_pass_selected_as_sufficient": False,
            **{key: selected_source[key] for key in (
                "product_path_simplified_or_unnecessary_gate_removed", "cause_changing_repair", "classified_as_unchanged_retry",
                "dependency_map_reviewed", "pin_or_provenance_only_fast_path_eligible", "fast_path_used",
            )},
        },
        "heartbeat_self_health": {
            "automation_status": "not_applicable", "local_activity_present": any(doc["recent_activity"].values()),
            "whole_goal_stagnation_evaluated": True, "activity_class": "support", "decision": "NOTIFY_REPLAN_REQUIRED",
            "prompt_updated_manually": False, "prompt_coverage": {
                "current_goal": True, "latest_incident": True, "terminal_marker": True, "whole_goal_cost": True,
                "zero_delta_streak": True, "correction_count": True, "required_skill_firings": True,
                "current_blocker": True, "audit_method": True, "stale": False,
            }, "prompt_coverage_validator_ref": entrypoint_ref,
        },
        "audit_integration": {},
        "gate_burden": {},
        "closeout": {"status": "open_replanned", "report_only": False, "validator_only": False,
                      "product_loop_simplified": selected_source["product_path_simplified_or_unnecessary_gate_removed"],
                      "unnecessary_gate_reduced": selected_source["product_path_simplified_or_unnecessary_gate_removed"], "observed_effective_claimed": False},
        "terminal_continuation": doc["terminal_continuation"],
        "fable5_dependency_banned": True,
        "external_dependencies": {"fable5_required": False, "odg_required": False, "telemetry_required_to_continue_supervised_work": False},
        "non_claims": sorted(REQUIRED_NON_CLAIMS),
        "long_lived_heartbeat": {}, "fresh_session_recheck": {},
    }
    actual_gate_ms = sum(row["elapsed_ms"] for row in doc["gate_burden"]["inventory"])
    transition["gate_burden"] = {
        "budget_ms": doc["gate_burden"]["budget_ms"], "actual_ms": actual_gate_ms,
        "avoidable_model_cost_ms": doc["gate_burden"]["avoidable_model_cost_ms"],
        "gate_burden_breached": actual_gate_ms > doc["gate_burden"]["budget_ms"],
        "inventory": doc["gate_burden"]["inventory"],
        "demote_or_retire_candidates": ["support control exceeded its current decision budget"] if actual_gate_ms > doc["gate_burden"]["budget_ms"] else [],
    }
    reasons = sorted(set(_threshold_reasons(transition)) | set(additional_replan_reasons or []))
    if not reasons:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_REPLAN_NOT_REQUIRED")
    decision = {"product_capability_path": "simplify_product_path", "cause_changing_prerequisite": "pivot", "exact_authority_blocker": "stop_exact_authority_blocker"}[selected["action_class"]]
    transition["replan"] = {
        "required": True, "trigger_reasons": reasons, "decision": decision, "action_changed": True,
        "selected_action_id": selected["action_id"], "exact_blocker": binding_input["blocker_fingerprint"] if selected["action_class"] == "exact_authority_blocker" else "",
        "next_protected_mutation_paused": True, "local_read_only_cause_repair_allowed": True,
        "read_only_and_supervised_local_work_allowed": True,
    }
    audit_input = doc["audit_integration"]
    audit_records: list[dict[str, Any]] = []
    systems: list[dict[str, Any]] = []
    exact: list[dict[str, Any]] = []
    audit_triggered = "repeated_user_warning" in reasons or transition["counters"]["evidence_only_slice_count"] >= 3
    systems_changed = False
    transition["audit_integration"] = {
        "implementation_owner_thread_id": audit_input["implementation_owner_thread_id"],
        "systems_audit_required": audit_triggered, "systems_audit_dispatched": bool(systems),
        "systems_audit_readback_received": bool(systems and all(record["readback_received"] for record in systems)),
        "systems_audit_result_integrated": bool(systems and all(record["integrated_into_action_selection"] for record in systems)),
        "systems_audit_changed_action": systems_changed, "exact_head_audit_performed": bool(exact),
        "exact_head_audit_substituted_for_systems_audit": False,
        "duplicate_same_head_claim_audit": len({(record["audit_type"], record["head_ref"], record["claim_scope"]) for record in audit_records}) != len(audit_records),
        "subagent_lanes_exist": audit_input["subagent_lanes_exist"], "fourth_oversight_present": audit_input["fourth_oversight_present"],
        "fourth_oversight_self_demoted": audit_input["fourth_oversight_self_demoted"], "audit_records": audit_records,
    }
    def session(mode: str) -> dict[str, Any]:
        return {
            "session_kind": "long_lived_control_session_pending_actual_invocation" if mode == "long_lived_heartbeat" else "fresh_session_pending_actual_invocation",
            "session_started_at": None, "source_merge_head": binding_input["head_ref"], "source_merge_observed_at": None,
            "recheck_at": None, "entrypoint_ref": entrypoint_ref, "entrypoint_digest": entrypoint_digest,
            "invocation_command": f'python3 scripts/ops/mk_whole_goal_control.py --record "$INC178_WHOLE_GOAL_TRANSITION" --live-context "$INC178_WHOLE_GOAL_LIVE_CONTEXT" --{"long-lived-heartbeat" if mode == "long_lived_heartbeat" else "fresh-session"} --consume-next-action --json',
            "prompt_mentions_inc178": False, "checker_invoked": False, "result_produced": False,
            "result_status": "PENDING_ACTUAL_INVOCATION", "before_selected_action_id": doc["before_selected_action_id"],
            "after_selected_action_id": None, "verified_non_application": False,
            "protected_next_action_id": selected["action_id"], "automatic_session_start_interception": "unproven",
        }
    transition["long_lived_heartbeat"] = {**session("long_lived_heartbeat"), "recent_activity": doc["recent_activity"], "fresh_session_binding_state": "separate_unproven_not_used_for_long_lived_recheck"}
    transition["fresh_session_recheck"] = {**session("fresh_session"), "long_lived_binding_state": "separate_unproven_not_used_for_fresh_session_recheck"}
    live_context = {"schema_version": LIVE_CONTEXT_VERSION, **binding_input, "selected_action_id": selected["action_id"], "observed_at": observed}
    return transition, live_context


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _authority_transition_readback(
    request: Any,
    *,
    repository_head: str,
    operation: str,
    target: str,
    now: datetime,
) -> dict[str, Any]:
    if request is None:
        return {
            "requested": False,
            "allowed": False,
            "execution_authorized": False,
            "reason": "PROTECTED_TRANSITION_NOT_REQUESTED",
        }
    expiry = _parse_time(request.get("expires_at")) if isinstance(request, dict) else None
    exact = (
        isinstance(request, dict)
        and set(request) == PROTECTED_TRANSITION_FIELDS
        and request.get("repository_id") == "maestro-kernel"
        and request.get("revision") == repository_head
        and request.get("operation") == operation
        and request.get("target") == target
        and all(_nonempty(request.get(field)) for field in ("protected_asset", "hazard", "owner", "rollback"))
        and expiry is not None
        and now < expiry <= now.replace(microsecond=0) + timedelta(hours=1)
    )
    return {
        "requested": True,
        "allowed": exact,
        "execution_authorized": False,
        "reason": "EXACT_PROTECTED_TRANSITION_IDENTITY_ADMITTED" if exact else "PROTECTED_TRANSITION_IDENTITY_MISMATCH",
        "repository_id": request.get("repository_id") if isinstance(request, dict) else None,
        "revision": request.get("revision") if isinstance(request, dict) else None,
        "operation": request.get("operation") if isinstance(request, dict) else None,
        "target": request.get("target") if isinstance(request, dict) else None,
    }


def _dispatch_control_readback(request: Any, *, repository_head: str) -> dict[str, Any]:
    signed_rebind_requested = (
        isinstance(request, dict)
        and request.get("signed_fixture_or_routing_rebind_requested") is True
    )
    source_revision_matches = bool(
        isinstance(request, dict)
        and request.get("source_revision") == repository_head
    )
    valid = (
        isinstance(request, dict)
        and set(request).issubset(DISPATCH_REQUEST_FIELDS)
        and source_revision_matches
    )
    return {
        "request_present": request is not None,
        "request_shape_valid": valid,
        "source_revision_matches": source_revision_matches,
        "signed_rebind_source_frozen": (not signed_rebind_requested) or source_revision_matches,
        "changed_focused_checks_allowed": True,
        "broad_suite_dispatch_allowed": False,
        "external_dispatch": False,
    }


def _read_input(input_path: Path) -> Any:
    try:
        return json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_INPUT_UNREADABLE") from exc


def _staged_output_path(state_path: Path, successor_state: dict[str, Any]) -> Path:
    return state_path.with_name(
        f"{state_path.name}.stage.{successor_state['sequence']}.{successor_state['state_digest']}"
    )


def _prepare_staged_output(
    stage_path: Path,
    *,
    transition: dict[str, Any],
    live_context: dict[str, Any],
    successor_state: dict[str, Any],
) -> Path:
    if stage_path.exists():
        raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_ALREADY_EXISTS")
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_stage = Path(tempfile.mkdtemp(prefix=f".{stage_path.name}.", dir=stage_path.parent))
    try:
        _atomic_write(temporary_stage / "transition.json", transition)
        _atomic_write(temporary_stage / "live-context.json", live_context)
        _atomic_write(temporary_stage / "cmd-state.json", successor_state)
        _fsync_directory(temporary_stage)
        os.replace(temporary_stage, stage_path)
        _fsync_directory(stage_path.parent)
    except BaseException:
        shutil.rmtree(temporary_stage, ignore_errors=True)
        raise
    return stage_path


def _recover_staged_output(
    state_path: Path,
    output_dir: Path,
    *,
    state: dict[str, Any],
    observation_digest: str,
) -> dict[str, Any] | None:
    stage_path = _staged_output_path(state_path, state)
    if not stage_path.exists():
        return None
    if (
        stage_path.is_symlink()
        or not stage_path.is_dir()
        or state.get("last_observation_digest") != observation_digest
    ):
        raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_IDENTITY_MISMATCH")
    expected_names = {"transition.json", "live-context.json", "cmd-state.json"}
    if {item.name for item in stage_path.iterdir()} != expected_names:
        raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_CONTENT_INVALID")
    try:
        staged_state = json.loads((stage_path / "cmd-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_CONTENT_INVALID") from exc
    if staged_state != state:
        raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_STATE_MISMATCH")

    if output_dir.exists():
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_OUTPUT_MISMATCH")
        if {item.name for item in output_dir.iterdir()} != expected_names:
            raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_OUTPUT_MISMATCH")
        for name in expected_names:
            if _sha(stage_path / name) != _sha(output_dir / name):
                raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_OUTPUT_MISMATCH")
        shutil.rmtree(stage_path)
        _fsync_directory(output_dir.parent)
        publication = "already_published"
    else:
        try:
            os.replace(stage_path, output_dir)
            _fsync_directory(output_dir.parent)
        except OSError as exc:
            raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_REPUBLISH_FAILED") from exc
        publication = "republished"
    return {
        "stage_path": str(stage_path.resolve()),
        "publication": publication,
        "sequence": state["sequence"],
        "state_digest": state["state_digest"],
    }


def _remove_precommit_stage(stage_path: Path) -> None:
    """Remove one orphaned predecessor-bound stage without following links."""
    try:
        if stage_path.is_symlink() or not stage_path.is_dir():
            stage_path.unlink()
        else:
            shutil.rmtree(stage_path)
        _fsync_directory(stage_path.parent)
    except OSError as exc:
        raise IssueError("BLOCKED_FOR_INC191_PRECOMMIT_STAGE_CLEANUP_FAILED") from exc


def _precommit_stage_candidate(
    state_path: Path,
    stage_path: Path,
    *,
    predecessor_state: dict[str, Any],
    state_supported: bool,
) -> bool:
    """Match only the next sequence's stage name to the current predecessor."""
    prefix = f"{state_path.name}.stage."
    if not stage_path.name.startswith(prefix):
        return False
    suffix = stage_path.name[len(prefix):].split(".")
    if len(suffix) != 2 or not suffix[0].isdigit() or not _hex_digest(suffix[1]):
        return False
    expected_sequence = predecessor_state["sequence"] + 1 if state_supported else 1
    return int(suffix[0]) == expected_sequence


def _read_precommit_stage_state(
    stage_path: Path,
    *,
    base_dir: Path,
    repository_head: str,
    now: datetime,
    predecessor_state: dict[str, Any],
    state_supported: bool,
    observation_digest: str,
) -> dict[str, Any] | None:
    """Return a complete successor stage only when it belongs to this retry."""
    expected_names = {"transition.json", "live-context.json", "cmd-state.json"}
    if stage_path.is_symlink() or not stage_path.is_dir():
        return None
    try:
        if {item.name for item in stage_path.iterdir()} != expected_names:
            return None
        values: dict[str, Any] = {}
        for name in expected_names:
            item = stage_path / name
            if item.is_symlink() or not item.is_file():
                return None
            values[name] = json.loads(item.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    staged_state = values.get("cmd-state.json")
    if not isinstance(values.get("transition.json"), dict) or not isinstance(values.get("live-context.json"), dict):
        return None
    if (
        not isinstance(staged_state, dict)
        or set(staged_state) != CMD_STATE_FIELDS
        or staged_state.get("predecessor_state_digest")
        != (predecessor_state["state_digest"] if state_supported else "0" * 64)
        or staged_state.get("sequence")
        != (predecessor_state["sequence"] + 1 if state_supported else 1)
        or staged_state.get("repository_head") != repository_head
        or staged_state.get("last_observation_digest") != observation_digest
        or not _valid_cmd_epoch_state_fields(staged_state)
    ):
        return None
    if _canonical_digest(_cmd_state_digest_payload(staged_state)) != staged_state.get("state_digest"):
        return None
    if _cmd_state_chain_digest(staged_state) != staged_state.get("chain_digest"):
        return None
    stage_digest = stage_path.name.rsplit(".", 1)[-1]
    if stage_digest != staged_state.get("state_digest"):
        return None
    loaded_state, loaded_supported, _ = _load_cmd_state(
        stage_path / "cmd-state.json",
        base_dir=base_dir,
        repository_head=repository_head,
        now=now,
    )
    if not loaded_supported or loaded_state != staged_state:
        return None
    return staged_state


def _recover_precommit_stage(
    state_path: Path,
    output_dir: Path,
    *,
    state: dict[str, Any],
    state_supported: bool,
    state_debt: str,
    base_dir: Path,
    repository_head: str,
    now: datetime,
    observation_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Recover or remove a stage left between preparation and state replace."""
    if not state_supported or state_debt not in CMD_STATE_TRUSTED_DEBTS:
        return None
    candidates = sorted(state_path.parent.glob(f"{state_path.name}.stage.*"))
    predecessor_candidates = [
        path
        for path in candidates
        if _precommit_stage_candidate(
            state_path,
            path,
            predecessor_state=state,
            state_supported=state_supported,
        )
    ]
    if not predecessor_candidates:
        return None
    valid_matches: list[tuple[Path, dict[str, Any]]] = []
    for stage_path in predecessor_candidates:
        staged_state = _read_precommit_stage_state(
            stage_path,
            base_dir=base_dir,
            repository_head=repository_head,
            now=now,
            predecessor_state=state,
            state_supported=state_supported,
            observation_digest=observation_digest,
        )
        if staged_state is None:
            _remove_precommit_stage(stage_path)
        else:
            valid_matches.append((stage_path, staged_state))
    if len(valid_matches) > 1:
        raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_IDENTITY_MISMATCH")
    if not valid_matches:
        return None
    stage_path, staged_state = valid_matches[0]
    expected_digest = None if state_debt == "CMD_STATE_INITIALIZED" else state["state_digest"]
    if _cmd_state_digest_at_path(state_path) != expected_digest:
        raise IssueError("BLOCKED_FOR_INC191_CMD_STATE_CAS_MISMATCH")
    try:
        _atomic_write(state_path, staged_state)
    except OSError as exc:
        raise IssueError("BLOCKED_FOR_INC191_CMD_STATE_PERSIST_FAILED") from exc
    recovery = _recover_staged_output(
        state_path,
        output_dir,
        state=staged_state,
        observation_digest=observation_digest,
    )
    if recovery is None:
        raise IssueError("BLOCKED_FOR_INC191_PENDING_STAGE_IDENTITY_MISMATCH")
    return staged_state, recovery


def _recovered_dispatch_readback(
    base_dir: Path,
    input_path: Path,
    output_dir: Path,
    state_path: Path,
    lock_readback: dict[str, Any],
    doc: dict[str, Any],
    state: dict[str, Any],
    transition: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    selected_action_id = transition["action_selection"]["selected_action_id"]
    selected_action = next(
        candidate for candidate in doc["candidates"] if candidate["action_id"] == selected_action_id
    )
    model_routing = _select_model_routing(doc.get("dispatch_request"), selected_action, base_dir)
    worker_pace = _worker_pace_readback(
        doc.get("dispatch_request"), str(model_routing["selection_reason"])
    )
    if worker_pace["valid"] is not True:
        raise IssueError(
            "BLOCKED_FOR_INC191_PRE_DISPATCH_ADMISSION:" + ",".join(worker_pace["reasons"])
        )
    selected_action_binding = _selected_action_binding(selected_action)
    threshold_replan = state["cause_changing_correction_count"] >= CMD_REPLAN_AFTER_CORRECTIONS
    transition_path = output_dir / "transition.json"
    context_path = output_dir / "live-context.json"
    cmd_state_output_path = output_dir / "cmd-state.json"
    authority_transition = _authority_transition_readback(
        doc.get("protected_transition_request"),
        repository_head=doc["binding"]["head_ref"],
        operation=transition["transition"],
        target=selected_action_id,
        now=_parse_time(transition["whole_goal"]["observed_at"]) or _utc_now(),
    )
    sdo_receipt = _sdo_decision_receipt(
        doc,
        transition,
        base_dir=base_dir,
        model_routing=model_routing,
        authority_transition=authority_transition,
        cmd_prevention={
            "trigger_reasons": ["two_cause_changing_corrections"] if threshold_replan else [],
            "cause_changing_correction_count": state["cause_changing_correction_count"],
            "next_operation": state.get("next_operation", ""),
            "receipt_digest": state.get("receipt_digest", ""),
            "receipt_consumed": state.get("receipt_consumed") is True,
        },
    )
    decision = sdo_receipt["decision"]
    return {
        "tool": "issue_inc178_current_transition",
        "status": "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY",
        "recovered_staged_output": True,
        "staged_output_identity": recovery,
        "decision": decision,
        "same_strategy_allowed": decision != "REPLAN_NOW",
        "safe_local_work_continues": True,
        "selected_action_id": selected_action_id,
        "sdo_intake": _sdo_readback(
            doc,
            selected_action_id,
            _parse_time(transition["whole_goal"]["observed_at"]) or _utc_now(),
        ),
        "sdo_decision_receipt": sdo_receipt,
        "support_work_progress_credit": transition["action_selection"]["support_work_progress_credit"],
        "durable_lock": lock_readback,
        "dispatch_intent": {
            "status": "recovered",
            "external_dispatch": False,
            "tool_name": "issue_inc178_current_transition",
            "operation_class": "repo_local_bounded_write",
            "model": model_routing["model"],
            "reasoning_effort": model_routing["reasoning_effort"],
            "selection_reason": model_routing["selection_reason"],
            "source_policy_model": model_routing["model"],
            "source_policy_reasoning_effort": model_routing["reasoning_effort"],
            "worker_pace": worker_pace,
            **_luna_fast_mode_policy(
                _normalize_cost_telemetry(
                    (doc.get("dispatch_request") or {}).get("cost_telemetry")
                )[0]["discount_eligibility"],
                str(model_routing["selection_reason"]),
            ),
            "decision": decision,
            "same_strategy_allowed": decision != "REPLAN_NOW",
            "trigger_reasons": ["two_cause_changing_corrections"] if threshold_replan else [],
            "target": selected_action_id,
            "owner": doc["audit_integration"]["implementation_owner_thread_id"],
            "worktree": str(base_dir.resolve()),
            "selected_action_binding": selected_action_binding,
            "selected_action_binding_digest": _canonical_digest({"selected_action_binding": selected_action_binding}),
            "write_set_kind": INC191_WRITE_SET_KIND,
            "state_sequence": state["sequence"],
            "state_digest": state["state_digest"],
            "holder_lock_path": lock_readback["lock_path"],
            "billing_telemetry_authoritative": False,
            "effective_billed_cost": "UNKNOWN",
            "cost_telemetry_source": "caller_reported_non_authoritative",
            "discount_eligibility_source": "caller_reported",
            "cost_claim_withheld": True,
            "runtime_identity_verified": False,
            "native_app_interception": "ABSENT",
            "source_eligible": "eligible",
            "observed_effective": False,
            "local_output_paths": [
                str(transition_path.resolve()),
                str(context_path.resolve()),
                str(cmd_state_output_path.resolve()),
            ],
            "local_state_path": str(state_path.resolve()),
        },
        "transition_file": transition_path.name,
        "transition_sha256": _sha(transition_path),
        "live_context_file": context_path.name,
        "live_context_sha256": _sha(context_path),
        "cmd_state_file": cmd_state_output_path.name,
        "cmd_state_sha256": _sha(cmd_state_output_path),
        "cmd_state_persisted": True,
        "input_sha256": _sha(input_path),
        "session_receipts": "pending_actual_cli_invocation",
        "non_claims": sorted(REQUIRED_NON_CLAIMS),
    }


def _issue_legacy(
    base_dir: Path,
    input_path: Path,
    output_dir: Path,
    *,
    now: datetime,
    raw: Any,
    observed_task_root: Path | None = None,
) -> dict[str, Any]:
    """Preserve ordinary issuance without resolving or acquiring CMD state."""
    if _has_cmd_state_semantics(raw):
        raise IssueError(CMD_EPOCH_OUTSIDE_LOCKED_STATE)
    doc = _validate_input(raw, base_dir, now, observed_task_root=observed_task_root)
    if doc.get("dispatch_request") is not None:
        raise IssueError("BLOCKED_FOR_INC191_DISPATCH_PRESENCE_CHANGED")
    transition, live_context = build_transition(doc, base_dir, now)
    selected_action_id = transition["action_selection"]["selected_action_id"]
    selected_action = next(
        candidate for candidate in doc["candidates"] if candidate["action_id"] == selected_action_id
    )
    model_routing = (
        _natural_model_routing()
        if _is_natural_observation(doc)
        else _select_model_routing(None, selected_action, base_dir)
    )
    sdo_readback = _sdo_readback(doc, selected_action_id, now)
    authority_transition = _authority_transition_readback(
        doc.get("protected_transition_request"),
        repository_head=doc["binding"]["head_ref"],
        operation=transition["transition"],
        target=selected_action_id,
        now=now,
    )
    sdo_receipt = _sdo_decision_receipt(
        doc,
        transition,
        base_dir=base_dir,
        model_routing=model_routing,
        authority_transition=authority_transition,
    )
    blocks = check_contract(transition, base_dir, session_mode="preparation")
    if blocks:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_ISSUANCE_CONTRACT_INVALID:" + ",".join(blocks))
    if output_dir.exists():
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_OUTPUT_PATH_EXISTS")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        transition_path = temporary_dir / "transition.json"
        context_path = temporary_dir / "live-context.json"
        _atomic_write(transition_path, transition)
        _atomic_write(context_path, live_context)
        os.replace(temporary_dir, output_dir)
    except OSError as exc:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_OUTPUT_WRITE_FAILED") from exc
    transition_path = output_dir / "transition.json"
    context_path = output_dir / "live-context.json"
    return {
        "tool": "issue_inc178_current_transition", "status": "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY",
        "decision": sdo_receipt["decision"],
        "same_strategy_allowed": sdo_receipt["decision"] != "REPLAN_NOW",
        "safe_local_work_continues": True,
        "selected_action_id": selected_action_id,
        "sdo_intake": sdo_readback,
        "sdo_decision_receipt": sdo_receipt,
        "model_routing": model_routing,
        "authority_transition": authority_transition,
        "transition_file": transition_path.name, "transition_sha256": _sha(transition_path),
        "live_context_file": context_path.name, "live_context_sha256": _sha(context_path),
        "input_sha256": _sha(input_path), "session_receipts": "pending_actual_cli_invocation",
        "non_claims": sorted(REQUIRED_NON_CLAIMS),
    }


def _state_only_admission_readback(
    model_routing: dict[str, Any],
) -> dict[str, Any]:
    """Describe a locked CMD-state mutation without claiming dispatch admission."""
    cost_telemetry, cost_claim_withheld = _normalize_cost_telemetry(None)
    return {
        "mutation_allowed": True,
        "safe_local_work_continues": True,
        "claim_withheld": True,
        "protected_transition_held": False,
        "merge_held": False,
        "continuation": "same_worktree",
        "decision": "CONTINUE_LOCAL",
        "same_strategy_allowed": True,
        "observed_effective": False,
        "model": model_routing["model"],
        "reasoning_effort": model_routing["reasoning_effort"],
        "selection_reason": model_routing["selection_reason"],
        "runtime_identity_verified": False,
        "native_app_interception": "ABSENT",
        "source_eligible": "eligible",
        "external_dispatch": False,
        "cost_telemetry_source": "not_applicable_to_locked_cmd_state",
        "discount_eligibility_source": "not_applicable_to_locked_cmd_state",
        "cost_claim_withheld": cost_claim_withheld,
        "billing_telemetry_authoritative": False,
        "effective_billed_cost": "UNKNOWN",
        "worker_pace": _worker_pace_readback(None, str(model_routing["selection_reason"])),
        "cost_telemetry": cost_telemetry,
        "reasons": [],
        "state_only": True,
    }


def _issue_locked(
    base_dir: Path,
    input_path: Path,
    output_dir: Path,
    *,
    now: datetime | None,
    cmd_state_path: Path,
    lock_readback: dict[str, Any],
    fault_inject_final_publish: bool,
    fault_inject_cmd_state_replace: bool,
    observed_task_root: Path | None = None,
) -> dict[str, Any]:
    effective_now = now or _utc_now()
    raw = _read_input(input_path)
    doc = _validate_input(
        raw,
        base_dir,
        effective_now,
        observed_task_root=observed_task_root,
    )
    dispatch_request = doc.get("dispatch_request")
    state_only_epoch = dispatch_request is None and _has_cmd_state_semantics(doc)
    transition, live_context = build_transition(doc, base_dir, effective_now)

    if dispatch_request is None and not state_only_epoch:
        raise IssueError("BLOCKED_FOR_INC191_DISPATCH_PRESENCE_CHANGED")

    repository_head = doc["binding"]["head_ref"]
    if (
        isinstance(dispatch_request, dict)
        and dispatch_request.get("signed_fixture_or_routing_rebind_requested") is True
        and dispatch_request.get("source_revision") != repository_head
    ):
        raise IssueError("BLOCKED_FOR_INC178_SOURCE_FREEZE_REQUIRED_BEFORE_SIGNED_REBIND")
    selected_action_id = transition["action_selection"]["selected_action_id"]
    selected_action = next(
        candidate for candidate in doc["candidates"] if candidate["action_id"] == selected_action_id
    )
    selected_action_binding = _selected_action_binding(selected_action)
    model_routing = _select_model_routing(dispatch_request, selected_action, base_dir)
    cmd_state, state_supported, state_debt = _load_cmd_state(
        cmd_state_path,
        base_dir=base_dir,
        repository_head=repository_head,
        now=effective_now,
    )
    observation_digest = _observation_identity(doc)[1]
    if state_supported:
        precommit_recovery = _recover_precommit_stage(
            cmd_state_path,
            output_dir,
            state=cmd_state,
            state_supported=state_supported,
            state_debt=state_debt,
            base_dir=base_dir,
            repository_head=repository_head,
            now=effective_now,
            observation_digest=observation_digest,
        )
        if precommit_recovery is not None:
            recovered_state, recovery = precommit_recovery
            return _recovered_dispatch_readback(
                base_dir,
                input_path,
                output_dir,
                cmd_state_path,
                lock_readback,
                doc,
                recovered_state,
                transition,
                recovery,
            )
        recovery = _recover_staged_output(
            cmd_state_path,
            output_dir,
            state=cmd_state,
            observation_digest=observation_digest,
        )
        if recovery is not None:
            return _recovered_dispatch_readback(
                base_dir,
                input_path,
                output_dir,
                cmd_state_path,
                lock_readback,
                doc,
                cmd_state,
                transition,
                recovery,
            )
    successor_state, cmd_prevention = _advance_cmd_state(
        cmd_state,
        state_supported=state_supported,
        base_dir=base_dir,
        state_debt=state_debt,
        doc=doc,
        selected_action=selected_action,
        repository_head=repository_head,
        now=effective_now,
        journal_path=output_dir / "transition.json",
        journal_digest=_canonical_digest(transition),
    )
    if cmd_prevention.get("cmd_epoch_blocker"):
        raise IssueError(str(cmd_prevention["cmd_epoch_blocker"]))
    if cmd_prevention["observation_rejected"]:
        raise IssueError("BLOCKED_FOR_INC191_CMD_STATE:" + str(cmd_prevention["state_debt"]))
    if state_debt not in CMD_STATE_TRUSTED_DEBTS:
        raise IssueError("BLOCKED_FOR_INC191_CMD_STATE_UNTRUSTED:" + state_debt)
    if cmd_prevention["trigger_reasons"]:
        transition, live_context = build_transition(
            doc,
            base_dir,
            effective_now,
            additional_replan_reasons=cmd_prevention["trigger_reasons"],
        )
        successor_state["transition_journal_digest"] = _canonical_digest(transition)
        successor_state["state_digest"] = _canonical_digest(
            _cmd_state_digest_payload(successor_state)
        )
        successor_state["chain_digest"] = _cmd_state_chain_digest(successor_state)
        cmd_prevention["chain_digest"] = successor_state["chain_digest"]

    authority_transition = _authority_transition_readback(
        doc.get("protected_transition_request"),
        repository_head=repository_head,
        operation=transition["transition"],
        target=selected_action_id,
        now=effective_now,
    )
    terminal = doc["terminal_continuation"]
    progress_blocker = terminal["progress_blocker"]
    goal_complete = all(
        terminal[field] is True
        for field in ("terminal_result_consumed", "selected_action_result_consumed", "quiet_closeout_requested")
    )
    dependency_wait = bool(
        progress_blocker["present"]
        and all(_nonempty(progress_blocker[field]) for field in ("blocker_id", "summary", "owner", "unblock_condition"))
        and _control_dispatch_readback_bound(terminal)
    )
    protected_wait = bool(
        terminal["protected_adoption_held"]
        and not terminal["bounded_local_repair_dispatchable"]
        and authority_transition["allowed"]
    )
    raw_primary_state = terminal["primary_state"]
    if raw_primary_state in {"active", "inProgress"}:
        derived_primary_state = "active"
    elif dependency_wait:
        derived_primary_state = "waiting_dependency"
    elif protected_wait or terminal["protected_adoption_held"]:
        derived_primary_state = "protected_blocked"
    else:
        derived_primary_state = raw_primary_state
    support_lanes_active = bool(
        terminal["control_dispatch_sent"]
        and terminal["control_dispatch_mode"] == "control_dispatch"
        and _nonempty(terminal["dispatch_target_thread_id"])
        and terminal["target_readback_received"]
    )
    derived_primary = {
        "support_lanes_active": support_lanes_active,
        "primary_state": derived_primary_state,
        "goal_incomplete": not goal_complete,
        "safe_disjoint_work": selected_action["operation_type"] not in {"systems_audit", "evidence_only"},
    }
    derived_telemetry_state = (
        "valid"
        if state_debt in CMD_STATE_TRUSTED_DEBTS
        else "invalid"
    )
    pre_dispatch = (
        _state_only_admission_readback(model_routing)
        if state_only_epoch
        else pre_dispatch_admission(
            dispatch_request,
            base_dir=base_dir,
            expected_owner=doc["audit_integration"]["implementation_owner_thread_id"],
            expected_write_set=[selected_action_binding],
            holder_path=Path(lock_readback["lock_path"]),
            holder_lock_held=lock_readback.get("held") is True,
            expected_reasoning_effort=str(model_routing["reasoning_effort"]),
            model_selection_reason=str(model_routing["selection_reason"]),
            derived_correction_count=cmd_prevention["cause_changing_correction_count"],
            derived_replan_reasons=cmd_prevention["trigger_reasons"],
            derived_primary=derived_primary,
            derived_telemetry_state=derived_telemetry_state,
            derived_protected_transition_requested=(
                doc.get("protected_transition_request") is not None and not protected_wait
            ),
            derived_protected_wait=protected_wait,
        )
    )
    if pre_dispatch["mutation_allowed"] is not True:
        raise IssueError("BLOCKED_FOR_INC191_PRE_DISPATCH_ADMISSION:" + ",".join(pre_dispatch["reasons"]))
    blocks = check_contract(transition, base_dir, session_mode="preparation")
    if blocks:
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_ISSUANCE_CONTRACT_INVALID:" + ",".join(blocks))
    if output_dir.exists():
        raise IssueError("BLOCKED_FOR_INC178_CURRENT_OUTPUT_PATH_EXISTS")

    # The lock is still held: verify the CAS point, fully stage and fsync the
    # output, commit the successor state, then publish the staged directory.
    expected_digest = None if state_debt == "CMD_STATE_INITIALIZED" else cmd_state["state_digest"]
    if _cmd_state_digest_at_path(cmd_state_path) != expected_digest:
        raise IssueError("BLOCKED_FOR_INC191_CMD_STATE_CAS_MISMATCH")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_path = _staged_output_path(cmd_state_path, successor_state)
    try:
        _prepare_staged_output(
            stage_path,
            transition=transition,
            live_context=live_context,
            successor_state=successor_state,
        )
    except OSError as exc:
        raise IssueError("BLOCKED_FOR_INC191_CURRENT_OUTPUT_STAGE_FAILED") from exc

    if fault_inject_cmd_state_replace:
        raise IssueError("BLOCKED_FOR_INC191_CMD_STATE_REPLACE_INJECTED")

    try:
        _atomic_write(cmd_state_path, successor_state)
    except OSError as exc:
        shutil.rmtree(stage_path, ignore_errors=True)
        try:
            _fsync_directory(stage_path.parent)
        except OSError:
            pass
        raise IssueError("BLOCKED_FOR_INC191_CMD_STATE_PERSIST_FAILED") from exc

    if fault_inject_final_publish:
        raise IssueError("BLOCKED_FOR_INC191_FINAL_PUBLISH_INJECTED")
    try:
        os.replace(stage_path, output_dir)
        _fsync_directory(output_dir.parent)
    except OSError as exc:
        raise IssueError("BLOCKED_FOR_INC191_FINAL_PUBLISH_PENDING_RECOVERY") from exc

    transition_path = output_dir / "transition.json"
    context_path = output_dir / "live-context.json"
    cmd_state_output_path = output_dir / "cmd-state.json"
    threshold_replan = bool(cmd_prevention["trigger_reasons"])
    sdo_readback = _sdo_readback(
        doc,
        selected_action_id,
        _parse_time(transition["whole_goal"]["observed_at"]) or effective_now,
    )
    sdo_receipt = _sdo_decision_receipt(
        doc,
        transition,
        base_dir=base_dir,
        model_routing=model_routing,
        authority_transition=authority_transition,
        cmd_prevention=cmd_prevention,
        pre_dispatch=pre_dispatch,
    )
    decision = sdo_receipt["decision"]
    return {
        "tool": "issue_inc178_current_transition",
        "status": "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY",
        "decision": decision,
        "same_strategy_allowed": decision != "REPLAN_NOW",
        "safe_local_work_continues": True,
        "exact_protected_transition_held": bool(
            authority_transition["allowed"]
            or (threshold_replan and transition["replan"]["next_protected_mutation_paused"])
        ),
        "selected_action_id": selected_action_id,
        "sdo_intake": sdo_readback,
        "sdo_decision_receipt": sdo_receipt,
        "support_work_progress_credit": transition["action_selection"]["support_work_progress_credit"],
        "cmd_prevention": cmd_prevention,
        "authority_transition": authority_transition,
        "dispatch_control": _dispatch_control_readback(dispatch_request, repository_head=repository_head),
        "pre_dispatch": pre_dispatch,
        "model_routing": model_routing,
        "durable_lock": lock_readback,
        "dispatch_intent": {
            "status": "state_admitted" if state_only_epoch else "admitted",
            "external_dispatch": False,
            "tool_name": "issue_inc178_current_transition",
            "operation_class": "repo_local_bounded_write",
            "model": model_routing["model"],
            "reasoning_effort": model_routing["reasoning_effort"],
            "selection_reason": model_routing["selection_reason"],
            "source_policy_model": model_routing["model"],
            "source_policy_reasoning_effort": model_routing["reasoning_effort"],
            "worker_pace": pre_dispatch["worker_pace"],
            **_luna_fast_mode_policy(
                pre_dispatch["cost_telemetry"]["discount_eligibility"],
                str(model_routing["selection_reason"]),
            ),
            "decision": decision,
            "same_strategy_allowed": decision != "REPLAN_NOW",
            "trigger_reasons": list(cmd_prevention["trigger_reasons"]),
            "target": selected_action_id,
            "owner": doc["audit_integration"]["implementation_owner_thread_id"],
            "worktree": str(base_dir.resolve()),
            "selected_action_binding": selected_action_binding,
            "selected_action_binding_digest": _canonical_digest({"selected_action_binding": selected_action_binding}),
            "write_set_kind": INC191_WRITE_SET_KIND,
            "state_sequence": successor_state["sequence"],
            "state_digest": successor_state["state_digest"],
            "holder_lock_path": lock_readback["lock_path"],
            "cost_telemetry": pre_dispatch["cost_telemetry"],
            "billing_telemetry_authoritative": False,
            "effective_billed_cost": "UNKNOWN",
            "cost_telemetry_source": "caller_reported_non_authoritative",
            "discount_eligibility_source": "caller_reported",
            "cost_claim_withheld": pre_dispatch["cost_claim_withheld"],
            "runtime_identity_verified": False,
            "native_app_interception": "ABSENT",
            "source_eligible": pre_dispatch["source_eligible"],
            "observed_effective": False,
            "local_output_paths": [
                str(transition_path.resolve()),
                str(context_path.resolve()),
                str(cmd_state_output_path.resolve()),
            ],
            "local_state_path": str(cmd_state_path.resolve()),
        },
        "transition_file": transition_path.name,
        "transition_sha256": _sha(transition_path),
        "live_context_file": context_path.name,
        "live_context_sha256": _sha(context_path),
        "cmd_state_file": cmd_state_output_path.name,
        "cmd_state_sha256": _sha(cmd_state_output_path),
        "cmd_state_persisted": True,
        "staged_output_identity": {
            "stage_path": str(stage_path.resolve()),
            "publication": "published",
            "sequence": successor_state["sequence"],
            "state_digest": successor_state["state_digest"],
        },
        "input_sha256": _sha(input_path),
        "session_receipts": "pending_actual_cli_invocation",
        "non_claims": sorted(REQUIRED_NON_CLAIMS),
    }


def issue(
    base_dir: Path,
    input_path: Path,
    output_dir: Path,
    *,
    now: datetime | None = None,
    cmd_state_path: Path | None = None,
    fault_inject_final_publish: bool = False,
    fault_inject_cmd_state_replace: bool = False,
    observed_task_root: Path | None = None,
) -> dict[str, Any]:
    """Own the durable lock across load, admission, output, and state persist."""
    effective_now = now or _utc_now()
    raw = _read_input(input_path)
    state_semantics = _has_cmd_state_semantics(raw)
    if not state_semantics and (not isinstance(raw, dict) or raw.get("dispatch_request") is None):
        return _issue_legacy(
            base_dir,
            input_path,
            output_dir,
            now=effective_now,
            raw=raw,
            observed_task_root=observed_task_root,
        )
    effective_state_path = cmd_state_path or _default_cmd_state_path(base_dir)
    with _cmd_state_lock(effective_state_path) as lock_readback:
        return _issue_locked(
            base_dir,
            input_path,
            output_dir,
            now=effective_now,
            cmd_state_path=effective_state_path,
            lock_readback=lock_readback,
            fault_inject_final_publish=fault_inject_final_publish,
            fault_inject_cmd_state_replace=fault_inject_cmd_state_replace,
            observed_task_root=observed_task_root,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--input")
    parser.add_argument("--natural-project-id")
    parser.add_argument("--natural-goal-ref")
    parser.add_argument("--natural-phase-ref")
    parser.add_argument("--natural-operation-id")
    parser.add_argument("--natural-task-root")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cmd-state-file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        base_dir = Path(args.base_dir).resolve()
        natural_values = (
            args.natural_project_id,
            args.natural_goal_ref,
            args.natural_phase_ref,
            args.natural_operation_id,
            args.natural_task_root,
        )
        natural_any = any(value is not None for value in natural_values)
        natural_complete = all(value is not None for value in natural_values)
        if args.input is not None and natural_any:
            raise IssueError("BLOCKED_FOR_INC178_NATURAL_INPUT_SIMULTANEOUS_WITH_INPUT")
        if args.input is None and not natural_complete:
            raise IssueError("BLOCKED_FOR_INC178_NATURAL_INPUT_ARGUMENTS_INCOMPLETE")
        cmd_state_path = Path(args.cmd_state_file) if args.cmd_state_file else None
        output_dir = Path(args.output_dir).resolve()
        if args.input is not None:
            result = issue(
                base_dir,
                Path(args.input).resolve(),
                output_dir,
                cmd_state_path=cmd_state_path,
            )
        else:
            task_root = _validate_natural_task_root(Path(args.natural_task_root))
            natural_input = build_natural_prompt_observation(
                task_root,
                project_id=args.natural_project_id,
                goal_ref=args.natural_goal_ref,
                phase_ref=args.natural_phase_ref,
                operation_id=args.natural_operation_id,
            )
            with tempfile.TemporaryDirectory(prefix="inc178-natural-input-") as temporary_root:
                natural_input_path = Path(temporary_root) / "input.json"
                natural_input_path.write_text(
                    json.dumps(natural_input, sort_keys=True),
                    encoding="utf-8",
                )
                result = issue(
                    PRODUCER_SOURCE_ROOT,
                    natural_input_path,
                    output_dir,
                    cmd_state_path=cmd_state_path,
                    observed_task_root=task_root,
                )
    except (IssueError, OSError) as exc:
        result = {
            "tool": "issue_inc178_current_transition",
            "status": "FAIL_WHOLE_GOAL_REPLAN_REQUIRED",
            "blocks": [str(exc)],
            "dispatch_intent": {
                "status": "denied",
                "source_eligible": "denied",
                "external_dispatch": False,
                "tool_name": None,
                "operation_class": None,
                "decision": "CONTINUE_LOCAL",
                "same_strategy_allowed": False,
                "trigger_reasons": [],
                "source_policy_model": None,
                "source_policy_reasoning_effort": None,
                "runtime_identity_verified": False,
                "native_app_interception": "ABSENT",
                "billing_telemetry_authoritative": False,
                "effective_billed_cost": "UNKNOWN",
                "cost_telemetry_source": "caller_reported_non_authoritative",
                "discount_eligibility_source": "caller_reported",
                "cost_claim_withheld": True,
            },
            "non_claims": sorted(REQUIRED_NON_CLAIMS),
        }
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else result["status"])
        return 1
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
