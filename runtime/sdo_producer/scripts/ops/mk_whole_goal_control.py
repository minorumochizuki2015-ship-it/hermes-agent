#!/usr/bin/env python3
"""Validate the INC-178 whole-goal work-selection loop-stop contract."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resolve_mk94_priority_action_queue import resolve_value_first


CONTRACT_VERSION = "inc178_whole_goal_work_selection.v1"
LIVE_CONTEXT_VERSION = "inc178_whole_goal_live_context.v1"
LIVE_CONTEXT_MAX_AGE_SECONDS = 300
LIVE_CONTEXT_CLOCK_SKEW_SECONDS = 5
LIVE_TRANSITION_DECISION_VERSION = "inc186_live_transition_decision.v1"
LIVE_TRANSITION_SUPPORT_ONLY_PREFIXES = (
    "audit ",
    "capture screenshot",
    "check status",
    "collect evidence",
    "compute hash",
    "generate report",
    "record receipt",
    "run audit",
    "run validator",
    "status ",
    "validate ",
    "verify ",
    "write report",
)
OBSERVABILITY_THRESHOLD_IDS = tuple(
    f"TH-OBS-{index}" for index in range(1, 11)
)
PMS_LEARNED_LOW_RISK_QUALIFICATION_PROFILE = (
    "pms_learned_low_risk_n60_ge4w_min15_per_enabled_class_"
    "wilson95_lcb_gt_0_95.v1"
)
EXEMPT_WORK_CLASSES = {
    "read_only_exploration",
    "small_reversible_local",
    "normal_local_bounded_supervised",
    "local_read_only_cause_repair",
}
PACED_WORK_CLASSES = {"delegated_nontrivial", "cross_repo_phase", "external_wait"}
TRANSITIONS = {"work_admission", "return_decide_dispatch", "heartbeat", "closeout"}
NONFIRE_REASONS = {
    "canonical_not_selected",
    "plugin_distribution_mismatch",
    "installed_cache_stale",
    "prompt_omitted_trigger",
    "validator_not_wired_to_transition",
    "result_not_integrated",
}
ACTION_CLASSES = {
    "product_capability_path",
    "cause_changing_prerequisite",
    "exact_authority_blocker",
    "evidence_only",
}
ACTION_CLASS_BY_OPERATION_TYPE = {
    "product_operation": "product_capability_path",
    "cause_repair": "cause_changing_prerequisite",
    "authority_wait": "exact_authority_blocker",
    "exact_head_audit": "evidence_only",
    "systems_audit": "evidence_only",
    "report": "evidence_only",
    "hash": "evidence_only",
    "status": "evidence_only",
}
EVIDENCE_ACTION_ID_MARKERS = (
    "audit",
    "report",
    "evidence",
    "hash",
    "status",
    "exact-scope-check",
)
PLANNING_PHASE_REFS = {"fable5-ultra-planning"}
PLANNING_ORDER_FIELDS = {
    "contract_version", "current_state", "mode_transition", "primary_work_id",
    "primary_work_class", "primary_path_action_id", "critical_path",
    "support_work_items", "topology", "gate_policy", "terminal_policy",
    "diagnosis",
}
PLANNING_DIAGNOSIS_FIELDS = {"state", "run_count"}
PLANNING_SUPPORT_ITEM_FIELDS = {
    "work_id", "work_class", "on_critical_path", "may_block_primary",
}
PLANNING_TOPOLOGY_FIELDS = {
    "architect_role", "optimization_owner", "pre_fixed",
    "optimization_required_before_execution",
}
PLANNING_GATE_POLICY_FIELDS = {
    "read_only_planning_allowed", "execution_grade_gates_deferred",
    "authority_gate_required_before_read_only_planning",
}
PLANNING_TERMINAL_POLICY_FIELDS = {
    "plan_lock_required_before_execution", "execution_requires_plan_lock",
    "control_or_evidence_only_can_close",
}
PLANNING_ORDER_VERSION = "planning_order_control.v1"
REPLAN_DECISIONS = {
    "parallelize_diagnosis",
    "pivot",
    "simplify_product_path",
    "stop_exact_authority_blocker",
    "retire_or_demote_gate",
}
REQUIRED_NON_CLAIMS = {
    "no_product_progress_from_control_implementation",
    "no_runtime_readiness",
    "no_automatic_firing_claim",
    "no_observed_effective_prevention",
    "no_product_user_or_final_acceptance",
    "no_fable5_authority_or_runtime_dependency",
    "no_cryptographic_receipt_source_integrity",
}
CMD_EPOCH_CONTROL_FIELDS = {
    "cmd_epoch_id",
    "previous_epoch_id",
    "cmd_release_state",
    "checkpoint_sha256",
    "pending_returns",
}
DECISION_OWNER_FIELDS = {"role", "surface", "epoch_id"}
TRANSPORT_ACTOR_FIELDS = {"role", "surface"}
CMD_EPOCH_ACTOR_FIELDS = {"decision_owner", "transport_actor"}
EMPTY_DECISION_OWNER = {"role": "", "surface": "", "epoch_id": ""}
EMPTY_TRANSPORT_ACTOR = {"role": "", "surface": ""}
WHOLE_GOAL_DECISION_OWNER_OVERRIDE = (
    "BLOCKED_FOR_WHOLE_GOAL_DECISION_OWNER_OVERRIDE"
)
WHOLE_GOAL_DECISION_OWNER_REQUIRED = (
    "BLOCKED_FOR_WHOLE_GOAL_DECISION_OWNER_REQUIRED"
)
WHOLE_GOAL_ORIGIN_CHANNEL_REQUIRED = (
    "BLOCKED_FOR_WHOLE_GOAL_ORIGIN_CHANNEL_REQUIRED"
)
WHOLE_GOAL_ORIGIN_CHANNEL_INVALID = (
    "BLOCKED_FOR_WHOLE_GOAL_ORIGIN_CHANNEL_INVALID"
)
WHOLE_GOAL_PEER_PACKET_PROVENANCE_REQUIRED = (
    "BLOCKED_FOR_WHOLE_GOAL_PEER_PACKET_PROVENANCE_REQUIRED"
)
WHOLE_GOAL_ORIGIN_PROVENANCE_MISMATCH = (
    "BLOCKED_FOR_WHOLE_GOAL_ORIGIN_PROVENANCE_MISMATCH"
)
ORIGIN_CHANNELS = frozenset({
    "direct_user_turn_on_primary_cmd_session",
    "peer_cmd_transport_readback",
    "tool_result",
    "relayed_pasted_packet_body",
    "notification",
})
SOVEREIGN_ORIGIN_CHANNEL = "direct_user_turn_on_primary_cmd_session"
PEER_PACKET_PROVENANCE_FIELDS = frozenset({
    "packet_id", "source_cmd_epoch_id", "source_surface", "origin_channel",
    "payload_digest",
})
PEER_CMD_DIRECTIVE_BINDING = "BLOCKED_FOR_HEARTBEAT_PEER_CMD_DIRECTIVE_BINDING"
CMD_PENDING_RETURN_FIELDS = {
    "return_id",
    "source_head",
    "consumed",
    "consumed_by",
    "return_path",
}
CMD_RELEASE_STATES = {"active", "released"}
CMD_RETURN_PATHS = {"direct", "user_relayed"}
CMD_EPOCH_CONFLICT = "CMD_EPOCH_CONFLICT"
CMD_RETURN_ALREADY_CONSUMED = "BLOCKED_FOR_INC178_RETURN_ALREADY_CONSUMED"
CMD_RETURN_NOT_CONSUMED = "BLOCKED_FOR_INC178_RETURN_NOT_CONSUMED"
CMD_USER_RELAYED_RETURN = "BLOCKED_FOR_INC178_USER_RELAYED_RETURN"
CMD_EPOCH_SCHEMA_INVALID = "BLOCKED_FOR_INC178_CMD_EPOCH_SCHEMA_INVALID"
CMD_RETURN_NOT_FOUND = "BLOCKED_FOR_INC178_RETURN_NOT_FOUND"
CMD_EMPTY_CHECKPOINT_SHA256 = "0" * 64
CMD_EPOCH_CONTINUATION_BLOCKERS = {
    CMD_EPOCH_CONFLICT,
    CMD_RETURN_ALREADY_CONSUMED,
    CMD_RETURN_NOT_CONSUMED,
    CMD_USER_RELAYED_RETURN,
    WHOLE_GOAL_DECISION_OWNER_REQUIRED,
    WHOLE_GOAL_ORIGIN_CHANNEL_REQUIRED,
    WHOLE_GOAL_ORIGIN_CHANNEL_INVALID,
}
SKILL_SURFACE_STATE_FIELDS = {
    "canonical_source_state", "plugin_distribution_state", "plugin_cache_diagnostic_state",
    "unprefixed_skill_root_state", "active_resolution_root_state",
    "presence_is_invocation_evidence", "invocation_is_result_consumption",
}
LONG_LIVED_HEARTBEAT_FIELDS = {
    "session_kind", "session_started_at", "source_merge_head", "source_merge_observed_at",
    "recheck_at", "entrypoint_ref", "entrypoint_digest", "invocation_command",
    "prompt_mentions_inc178", "checker_invoked", "result_produced", "result_status",
    "before_selected_action_id", "after_selected_action_id", "verified_non_application",
    "protected_next_action_id", "recent_activity",
    "automatic_session_start_interception", "fresh_session_binding_state",
}
FRESH_SESSION_RECHECK_FIELDS = {
    "session_kind", "session_started_at", "source_merge_head", "source_merge_observed_at",
    "recheck_at", "entrypoint_ref", "entrypoint_digest", "invocation_command",
    "prompt_mentions_inc178", "checker_invoked", "result_produced", "result_status",
    "before_selected_action_id", "after_selected_action_id", "verified_non_application",
    "protected_next_action_id", "automatic_session_start_interception", "long_lived_binding_state",
}
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
TERMINAL_PRIMARY_STATES = {"active", "inProgress", "idle", "notLoaded"}
LONG_LIVED_BINDING_BLOCKS = {
    "BLOCKED_FOR_INC178_LONG_LIVED_HEARTBEAT_BINDING_INVALID",
    "BLOCKED_FOR_INC178_LONG_LIVED_SESSION_PREMERGE_RECHECK_REQUIRED",
    "BLOCKED_FOR_INC178_LONG_LIVED_HEARTBEAT_CHECKER_NOT_INVOKED",
    "BLOCKED_FOR_INC178_LONG_LIVED_RESULT_NOT_CONSUMED",
    "BLOCKED_FOR_INC178_LONG_LIVED_RECENT_ACTIVITY_MASKING_ZERO_VISIBLE_DELTA",
}
FRESH_SESSION_BINDING_BLOCKS = {
    "BLOCKED_FOR_INC178_FRESH_SESSION_RECHECK_BINDING_INVALID",
    "BLOCKED_FOR_INC178_FRESH_SESSION_RESULT_NOT_CONSUMED",
}
PENDING_SESSION_RESULT = "PENDING_ACTUAL_INVOCATION"
PENDING_SESSION_KINDS = {
    "long_lived_heartbeat": "long_lived_control_session_pending_actual_invocation",
    "fresh_session": "fresh_session_pending_actual_invocation",
}

LIVE_CONTEXT_FIELDS = {
    "schema_version",
    "goal_ref",
    "phase_ref",
    "head_ref",
    "blocker_fingerprint",
    "selected_action_id",
    "observed_at",
}


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value: Any) -> bool:
    return _nonnegative_int(value) and value > 0


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _valid_actor_shape(value: Any, fields: set[str], *, allow_empty: bool) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == fields
        and all(isinstance(value.get(field), str) for field in fields)
        and (
            allow_empty
            or all(bool(value.get(field, "").strip()) for field in fields)
        )
    )


def validate_cmd_epoch_actor_fields(value: Any) -> bool:
    """Validate the actor records carried by a persisted epoch state."""
    return (
        isinstance(value, dict)
        and set(value) == CMD_EPOCH_ACTOR_FIELDS
        and _valid_actor_shape(
            value.get("decision_owner"), DECISION_OWNER_FIELDS, allow_empty=True
        )
        and _valid_actor_shape(
            value.get("transport_actor"), TRANSPORT_ACTOR_FIELDS, allow_empty=True
        )
    )


def cmd_checkpoint_sha256(content: Any) -> str | None:
    """Hash checkpoint content without persisting the content itself."""
    if isinstance(content, str):
        encoded = content.encode("utf-8")
    elif isinstance(content, bytes):
        encoded = content
    elif isinstance(content, (dict, list, int, float, bool)) or content is None:
        encoded = json.dumps(content, separators=(",", ":"), sort_keys=True).encode("utf-8")
    else:
        return None
    return hashlib.sha256(encoded).hexdigest()


def normalize_cmd_epoch_state(value: Any) -> dict[str, Any]:
    """Add T4 fields to an existing cmd-state value without changing old fields."""
    state = copy.deepcopy(value) if isinstance(value, dict) else {}
    state.setdefault("cmd_epoch_id", "")
    state.setdefault("previous_epoch_id", "")
    state.setdefault("cmd_release_state", "released")
    state.setdefault("checkpoint_sha256", CMD_EMPTY_CHECKPOINT_SHA256)
    state.setdefault("decision_owner", copy.deepcopy(EMPTY_DECISION_OWNER))
    state.setdefault("transport_actor", copy.deepcopy(EMPTY_TRANSPORT_ACTOR))
    pending = state.setdefault("pending_returns", [])
    if isinstance(pending, list):
        normalized: list[dict[str, Any]] = []
        for entry in pending:
            if not isinstance(entry, dict):
                normalized.append(entry)
                continue
            item = copy.deepcopy(entry)
            item.setdefault("consumed_by", "")
            item.setdefault("return_path", "direct")
            normalized.append(item)
        state["pending_returns"] = normalized
    return state


def validate_cmd_epoch_control(value: Any) -> bool:
    """Validate the public whole-goal projection of the persisted CMD epoch."""
    if not isinstance(value, dict) or set(value) != CMD_EPOCH_CONTROL_FIELDS:
        return False
    if (
        not isinstance(value.get("cmd_epoch_id"), str)
        or not isinstance(value.get("previous_epoch_id"), str)
        or value.get("cmd_release_state") not in CMD_RELEASE_STATES
        or not _sha256(value.get("checkpoint_sha256"))
        or not isinstance(value.get("pending_returns"), list)
    ):
        return False
    seen: set[str] = set()
    for entry in value["pending_returns"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != CMD_PENDING_RETURN_FIELDS
            or not isinstance(entry.get("return_id"), str)
            or not entry["return_id"].strip()
            or entry["return_id"] in seen
            or not isinstance(entry.get("source_head"), str)
            or len(entry["source_head"]) != 40
            or any(char not in "0123456789abcdef" for char in entry["source_head"])
            or not isinstance(entry.get("consumed"), bool)
            or not isinstance(entry.get("consumed_by"), str)
            or (entry["consumed"] and not entry["consumed_by"].strip())
            or (not entry["consumed"] and entry["consumed_by"] != "")
            or entry.get("return_path") not in CMD_RETURN_PATHS
        ):
            return False
        seen.add(entry["return_id"])
    return True


def validate_cmd_epoch_request(value: Any) -> bool:
    """Validate request shape; state-dependent conflicts are handled separately."""
    if not isinstance(value, dict) or value.get("operation") not in {"release", "acquire", "return", "consume", "continue"}:
        return False
    operation = value["operation"]
    if operation in {"release", "acquire"}:
        if not isinstance(value.get("cmd_epoch_id"), str) or not value["cmd_epoch_id"].strip():
            return False
        if operation == "acquire" and not isinstance(value.get("previous_epoch_id"), str):
            return False
        if cmd_checkpoint_sha256(value.get("checkpoint_content")) is None:
            return False
        if not _sha256(value.get("checkpoint_sha256")):
            return False
    if operation in {"consume", "continue"}:
        if not isinstance(value.get("cmd_epoch_id"), str) or not value["cmd_epoch_id"].strip():
            return False
        if not isinstance(value.get("return_id"), str) or not value["return_id"].strip():
            return False
    if operation == "return":
        payload = value.get("return") if isinstance(value.get("return"), dict) else value
        if not isinstance(payload.get("return_id"), str) or not payload["return_id"].strip():
            return False
        if payload.get("return_path", "direct") not in CMD_RETURN_PATHS:
            return False
    for field, fields in (
        ("decision_owner", DECISION_OWNER_FIELDS),
        ("transport_actor", TRANSPORT_ACTOR_FIELDS),
    ):
        if field in value and not _valid_actor_shape(
            value.get(field), fields, allow_empty=False
        ):
            return False
    if "decision_owner" in value and operation in {"release", "acquire"}:
        if value["decision_owner"]["epoch_id"] != value.get("cmd_epoch_id"):
            return False
    return True


def _checkpoint_request_digest(request: dict[str, Any]) -> str | None:
    content_digest = cmd_checkpoint_sha256(request.get("checkpoint_content"))
    if content_digest is None or request.get("checkpoint_sha256") != content_digest:
        return None
    return content_digest


def apply_cmd_epoch_request(
    value: Any,
    request: Any,
    *,
    repository_head: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Apply one bounded epoch/return operation, returning a new state or a typed blocker."""
    state = normalize_cmd_epoch_state(value)
    if not validate_cmd_epoch_request(request):
        return state, CMD_EPOCH_SCHEMA_INVALID
    operation = request["operation"]
    if operation == "release":
        digest = _checkpoint_request_digest(request)
        if digest is None or (
            state["cmd_epoch_id"]
            and request["cmd_epoch_id"] != state["cmd_epoch_id"]
        ):
            return state, CMD_EPOCH_CONFLICT
        successor = copy.deepcopy(state)
        successor.update({
            "cmd_epoch_id": request["cmd_epoch_id"],
            "previous_epoch_id": state["previous_epoch_id"] if state["cmd_epoch_id"] else "",
            "cmd_release_state": "released",
            "checkpoint_sha256": digest,
        })
        for field in CMD_EPOCH_ACTOR_FIELDS:
            if field in request:
                successor[field] = copy.deepcopy(request[field])
        return successor, None

    if operation == "acquire":
        if state["cmd_release_state"] != "released":
            return state, CMD_EPOCH_CONFLICT
        if request.get("previous_epoch_id") != state["cmd_epoch_id"]:
            return state, CMD_EPOCH_CONFLICT
        digest = _checkpoint_request_digest(request)
        if digest is None or digest != state["checkpoint_sha256"]:
            return state, CMD_EPOCH_CONFLICT
        successor = copy.deepcopy(state)
        successor.update({
            "previous_epoch_id": state["cmd_epoch_id"],
            "cmd_epoch_id": request["cmd_epoch_id"],
            "cmd_release_state": "active",
        })
        for field in CMD_EPOCH_ACTOR_FIELDS:
            if field in request:
                successor[field] = copy.deepcopy(request[field])
        return successor, None

    if operation == "return":
        payload = request.get("return") if isinstance(request.get("return"), dict) else request
        return_path = payload.get("return_path", "direct")
        if return_path == "user_relayed":
            return state, CMD_USER_RELAYED_RETURN
        if state["cmd_release_state"] != "active":
            return state, CMD_EPOCH_CONFLICT
        source_head = payload.get("source_head") or repository_head
        if not isinstance(source_head, str) or len(source_head) != 40 or any(char not in "0123456789abcdef" for char in source_head):
            return state, CMD_EPOCH_CONFLICT
        if repository_head and source_head != repository_head:
            return state, CMD_EPOCH_CONFLICT
        return_id = payload["return_id"]
        existing = next((entry for entry in state["pending_returns"] if isinstance(entry, dict) and entry.get("return_id") == return_id), None)
        if existing is not None:
            return state, CMD_RETURN_ALREADY_CONSUMED if existing.get("consumed") is True else CMD_EPOCH_CONFLICT
        successor = copy.deepcopy(state)
        successor["pending_returns"].append({
            "return_id": return_id,
            "source_head": source_head,
            "consumed": False,
            "consumed_by": "",
            "return_path": "direct",
        })
        return successor, None

    if state["cmd_release_state"] != "active" or request["cmd_epoch_id"] != state["cmd_epoch_id"]:
        return state, CMD_EPOCH_CONFLICT
    entry = next((item for item in state["pending_returns"] if isinstance(item, dict) and item.get("return_id") == request["return_id"]), None)
    if entry is None:
        return state, CMD_RETURN_NOT_FOUND
    if entry.get("return_path") == "user_relayed":
        return state, CMD_USER_RELAYED_RETURN
    if operation == "continue":
        return state, CMD_RETURN_NOT_CONSUMED if entry.get("consumed") is not True else None
    if entry.get("consumed") is True:
        return state, CMD_RETURN_ALREADY_CONSUMED
    if request.get("source_head") is not None and request.get("source_head") != entry.get("source_head"):
        return state, CMD_EPOCH_CONFLICT
    if repository_head and entry.get("source_head") != repository_head:
        return state, CMD_EPOCH_CONFLICT
    successor = copy.deepcopy(state)
    for item in successor["pending_returns"]:
        if item.get("return_id") == request["return_id"]:
            item["consumed"] = True
            item["consumed_by"] = request["cmd_epoch_id"]
            break
    return successor, None


def cmd_epoch_case_blocks(case: Any, *, repository_head: str | None = None) -> list[str]:
    if not isinstance(case, dict) or not isinstance(case.get("state"), dict) or not isinstance(case.get("request"), dict):
        return [CMD_EPOCH_SCHEMA_INVALID]
    request = copy.deepcopy(case["request"])
    operation = case.get("operation")
    if operation not in {"release", "acquire", "return", "consume", "continue"}:
        return [CMD_EPOCH_SCHEMA_INVALID]
    request["operation"] = operation
    _, blocker = apply_cmd_epoch_request(case["state"], request, repository_head=repository_head)
    return [blocker] if blocker else []


def cmd_epoch_continuation_blocks(value: Any) -> list[str]:
    control = value.get("cmd_epoch_control") if isinstance(value, dict) else None
    if control is None:
        return []
    if not validate_cmd_epoch_control(control):
        return [CMD_EPOCH_SCHEMA_INVALID]
    request = value.get("cmd_epoch_request") if isinstance(value, dict) else None
    if request is not None:
        _, blocker = apply_cmd_epoch_request(control, request)
        if blocker:
            return [blocker]
    for entry in control["pending_returns"]:
        if entry["return_path"] == "user_relayed":
            return [CMD_USER_RELAYED_RETURN]
        if entry["consumed"] is not True:
            return [CMD_RETURN_NOT_CONSUMED]
    return []


def _repo_ref_digest_matches(base_dir: Path, ref: Any, expected_digest: Any) -> bool:
    if not isinstance(ref, str) or not ref or Path(ref).is_absolute() or ".." in Path(ref).parts:
        return False
    root = base_dir.resolve()
    path = (root / ref).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    if not path.is_file() or not _sha256(expected_digest):
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_session_invocation_command(command: Any, mode: str) -> bool:
    if not isinstance(command, str) or not command:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if tokens[:2] != ["python3", "scripts/ops/mk_whole_goal_control.py"]:
        return False

    value_flags: dict[str, str] = {}
    switches: set[str] = set()
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token in {"--record", "--live-context"}:
            if token in value_flags or index + 1 >= len(tokens) or not tokens[index + 1]:
                return False
            value_flags[token] = tokens[index + 1]
            index += 2
            continue
        if token in {"--long-lived-heartbeat", "--fresh-session", "--consume-next-action", "--json"}:
            if token in switches:
                return False
            switches.add(token)
            index += 1
            continue
        return False

    expected_mode_flag = (
        "--long-lived-heartbeat" if mode == "long_lived_heartbeat" else "--fresh-session"
    )
    return (
        set(value_flags) == {"--record", "--live-context"}
        and switches in (
            {expected_mode_flag, "--json"},
            {expected_mode_flag, "--consume-next-action", "--json"},
        )
        and value_flags["--record"] != value_flags["--live-context"]
    )


def _check_live_context(
    value: Any,
    mode: str,
    live_context: Any,
    evaluation_time: datetime,
) -> list[str]:
    if (
        not isinstance(live_context, dict)
        or set(live_context) != LIVE_CONTEXT_FIELDS
        or live_context.get("schema_version") != LIVE_CONTEXT_VERSION
    ):
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_REQUIRED"]

    observed_at = _parse_time(live_context.get("observed_at"))
    if observed_at is None:
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_REQUIRED"]
    age_seconds = (evaluation_time - observed_at).total_seconds()
    if (
        age_seconds < -LIVE_CONTEXT_CLOCK_SKEW_SECONDS
        or age_seconds > LIVE_CONTEXT_MAX_AGE_SECONDS
    ):
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_STALE"]

    if not isinstance(value, dict):
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_MISMATCH"]
    binding = value.get("decision_binding")
    whole = value.get("whole_goal")
    binding_key = "long_lived_heartbeat" if mode == "long_lived_heartbeat" else "fresh_session_recheck"
    session = value.get(binding_key)
    if not isinstance(binding, dict) or not isinstance(whole, dict) or not isinstance(session, dict):
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_MISMATCH"]

    expected = {
        "goal_ref": binding.get("goal_ref"),
        "phase_ref": binding.get("phase_ref"),
        "head_ref": binding.get("head_ref"),
        "blocker_fingerprint": binding.get("blocker_fingerprint"),
        "selected_action_id": binding.get("selected_action_id"),
    }
    if any(live_context.get(field) != expected[field] for field in expected):
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_MISMATCH"]
    if whole.get("goal_ref") != live_context.get("goal_ref"):
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_MISMATCH"]
    if session.get("source_merge_head") != live_context.get("head_ref"):
        return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_MISMATCH"]

    timestamps = [binding.get("evaluated_at"), whole.get("observed_at")]
    if session.get("session_kind") != PENDING_SESSION_KINDS.get(mode):
        timestamps.append(session.get("recheck_at"))
    for timestamp in timestamps:
        parsed = _parse_time(timestamp)
        state_age_seconds = (observed_at - parsed).total_seconds() if parsed is not None else None
        if (
            state_age_seconds is None
            or state_age_seconds < -LIVE_CONTEXT_CLOCK_SKEW_SECONDS
            or state_age_seconds > LIVE_CONTEXT_MAX_AGE_SECONDS
        ):
            return ["BLOCKED_FOR_INC178_LIVE_CONTEXT_MISMATCH"]
    return []


def _warning_counts(events: Any) -> dict[str, int] | None:
    if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
        return None
    counts: dict[str, int] = {}
    for row in events:
        if (
            set(row) != {"warning_class", "count", "first_observed_at", "last_observed_at", "source_ref"}
            or _empty(row.get("warning_class"))
            or not _positive_int(row.get("count"))
            or _parse_time(row.get("first_observed_at")) is None
            or _parse_time(row.get("last_observed_at")) is None
            or _parse_time(row.get("last_observed_at")) < _parse_time(row.get("first_observed_at"))
            or _empty(row.get("source_ref"))
        ):
            return None
        key = row["warning_class"]
        counts[key] = counts.get(key, 0) + row["count"]
    return counts


def _derived_action_class(row: dict[str, Any]) -> str | None:
    action_id = str(row.get("action_id") or "").lower().replace("_", "-")
    if any(marker in action_id for marker in EVIDENCE_ACTION_ID_MARKERS):
        return "evidence_only"
    return ACTION_CLASS_BY_OPERATION_TYPE.get(row.get("operation_type"))


def _computed_session_application(binding: Any, selected_action_id: Any) -> tuple[bool, bool]:
    if not isinstance(binding, dict) or not isinstance(selected_action_id, str) or not selected_action_id:
        return False, False
    before = binding.get("before_selected_action_id")
    after = binding.get("after_selected_action_id")
    transition_applied = (
        isinstance(before, str)
        and bool(before)
        and isinstance(after, str)
        and bool(after)
        and before != after
        and after == selected_action_id
        and binding.get("verified_non_application") is False
    )
    non_application_verified = (
        isinstance(before, str)
        and bool(before)
        and before == after
        and binding.get("verified_non_application") is True
    )
    return transition_applied, non_application_verified


def _terminal_receipt_consumed(
    value: Any,
    selected_action_id: Any,
    session_mode: str | None,
) -> bool:
    if not isinstance(value, dict):
        return False
    keys = {
        "long_lived_heartbeat": ("long_lived_heartbeat",),
        "fresh_session": ("fresh_session_recheck",),
    }.get(session_mode, ("long_lived_heartbeat", "fresh_session_recheck"))
    for key in keys:
        receipt = value.get(key)
        transition_applied, _ = _computed_session_application(receipt, selected_action_id)
        if (
            isinstance(receipt, dict)
            and receipt.get("checker_invoked") is True
            and receipt.get("result_produced") is True
            and receipt.get("result_status") == "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY"
            and transition_applied
        ):
            return True
    return False


def _pending_session_binding_valid(
    binding: Any,
    mode: str,
    selected_action_id: Any,
    head_ref: Any,
) -> bool:
    """Validate issuer preparation without treating it as a runtime receipt."""
    if not isinstance(binding, dict) or binding.get("session_kind") != PENDING_SESSION_KINDS[mode]:
        return False
    before_selected_action_id = binding.get("before_selected_action_id")
    common = (
        binding.get("session_started_at") is None
        and binding.get("source_merge_observed_at") is None
        and binding.get("recheck_at") is None
        and binding.get("source_merge_head") == head_ref
        and not _empty(binding.get("entrypoint_ref"))
        and _sha256(binding.get("entrypoint_digest"))
        and _valid_session_invocation_command(binding.get("invocation_command"), mode)
        and binding.get("prompt_mentions_inc178") is False
        and binding.get("checker_invoked") is False
        and binding.get("result_produced") is False
        and binding.get("result_status") == PENDING_SESSION_RESULT
        and isinstance(before_selected_action_id, str)
        and bool(before_selected_action_id)
        and before_selected_action_id != selected_action_id
        and binding.get("after_selected_action_id") is None
        and binding.get("verified_non_application") is False
        and binding.get("protected_next_action_id") == selected_action_id
        and binding.get("automatic_session_start_interception") == "unproven"
    )
    if mode == "long_lived_heartbeat":
        recent_activity = binding.get("recent_activity")
        return (
            common
            and binding.get("fresh_session_binding_state")
            == "separate_unproven_not_used_for_long_lived_recheck"
            and isinstance(recent_activity, dict)
            and set(recent_activity) == {"source", "ci", "audit"}
            and all(isinstance(recent_activity.get(field), bool) for field in recent_activity)
        )
    return (
        common
        and binding.get("long_lived_binding_state")
        == "separate_unproven_not_used_for_fresh_session_recheck"
    )


def genericize_transition_for_contract_test(
    value: dict[str, Any],
    *,
    goal_ref: str,
    phase_ref: str,
    work_class: str,
    source_ref: str,
) -> dict[str, Any]:
    """Remove incident/product values before a shared contract self-test."""
    generic = json.loads(json.dumps(value))
    generic["work_class"] = work_class
    generic["decision_binding"].update({
        "goal_ref": goal_ref,
        "phase_ref": phase_ref,
        "head_ref": "generic-current-head",
        "blocker_fingerprint": "generic-whole-goal-stagnation",
        "source_ref": source_ref,
    })
    generic["whole_goal"].update({
        "goal_ref": goal_ref,
        "current_biggest_blocker": "The generic user outcome remains unchanged while bounded work continues.",
        "active_elapsed_source": "bounded_estimate",
    })
    generic["progress_deltas"]["blocker_knowledge_delta"]["summary"] = (
        "A generic blocker classification changed."
    )
    generic["progress_deltas"]["runtime_milestone_delta"]["summary"] = (
        "No generic runtime milestone is claimed."
    )
    generic["progress_deltas"]["user_visible_capability_delta"].update({
        "summary": "No normal-user operation is claimed in the generic contract self-test.",
        "normal_user_operation_observed": False,
    })

    warning_map: dict[str, str] = {}
    for index, row in enumerate(generic["counters"]["user_warning_events"], start=1):
        old = row["warning_class"]
        warning_map[old] = f"generic_warning_{index}"
        row["warning_class"] = warning_map[old]
        row["source_ref"] = "generic-warning-source"
    for row in generic["counters"]["repeated_warning_classes"]:
        row["warning_class"] = warning_map[row["warning_class"]]
    generic["counters"]["warning_count_source"] = "generic-warning-count"

    candidates = generic["action_selection"]["candidate_actions"]
    replacement_ids = (
        "generic-user-capability-path",
        "generic-systems-review",
        "generic-exact-scope-check",
    )
    action_id_map = {
        row["action_id"]: replacement_ids[index]
        for index, row in enumerate(candidates)
    }
    for row in candidates:
        row["action_id"] = action_id_map[row["action_id"]]
    selected = generic["action_selection"]
    selected["selected_action_id"] = action_id_map[selected["selected_action_id"]]
    generic["decision_binding"]["selected_action_id"] = selected["selected_action_id"]
    selected["next_action"] = "Continue the generic highest-user-value bounded path."
    for row in selected["rejected_actions"]:
        row["action_id"] = action_id_map[row["action_id"]]
        row["reason"] = "The generic alternative has lower user value for this bounded test."
    generic["replan"]["selected_action_id"] = selected["selected_action_id"]

    result_ref = "scripts/ops/mk_whole_goal_control.py"
    result_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    audit = generic["audit_integration"]
    audit["implementation_owner_thread_id"] = "generic-implementation-thread"
    for index, row in enumerate(audit["audit_records"], start=1):
        row["audit_id"] = f"generic-review-{index}"
        row["source_thread_id"] = (
            "generic-systems-reviewer-thread"
            if row["audit_type"] == "systems"
            else "generic-exact-reviewer-thread"
        )
        row["head_ref"] = "generic-current-head"
        row["claim_scope"] = (
            "generic-architecture-cost-alternatives"
            if row["audit_type"] == "systems"
            else "generic-source-scope-only"
        )
        row["result_ref"] = result_ref
        row["result_digest"] = result_digest
        row["action_before_id"] = action_id_map[row["action_before_id"]]
        row["action_after_id"] = action_id_map[row["action_after_id"]]

    heartbeat = generic["heartbeat_self_health"]
    heartbeat["prompt_coverage_validator_ref"] = result_ref
    long_lived = generic["long_lived_heartbeat"]
    long_lived.update({
        "source_merge_head": "generic-current-head",
        "entrypoint_ref": result_ref,
        "entrypoint_digest": result_digest,
        "before_selected_action_id": action_id_map["repeat-exact-head-audit"],
        "after_selected_action_id": selected["selected_action_id"],
        "verified_non_application": False,
        "protected_next_action_id": selected["selected_action_id"],
    })
    fresh = generic["fresh_session_recheck"]
    fresh.update({
        "source_merge_head": "generic-current-head",
        "entrypoint_ref": result_ref,
        "entrypoint_digest": result_digest,
        "before_selected_action_id": action_id_map["repeat-exact-head-audit"],
        "after_selected_action_id": selected["selected_action_id"],
        "verified_non_application": False,
        "protected_next_action_id": selected["selected_action_id"],
    })
    gate = generic["gate_burden"]
    for row, control_id in zip(
        gate["inventory"],
        ("generic-protected-mutation", "generic-scoped-review", "generic-package-check"),
        strict=True,
    ):
        row["control_id"] = control_id
    gate["demote_or_retire_candidates"] = [
        "duplicate generic review without a state change",
        "support prerequisite that does not change a protected decision",
    ]
    def semantic_strings(node: Any):
        if isinstance(node, dict):
            for item in node.values():
                yield from semantic_strings(item)
        elif isinstance(node, list):
            for item in node:
                yield from semantic_strings(item)
        elif isinstance(node, str) and not _sha256(node):
            yield node.lower()

    semantic_values = tuple(semantic_strings(generic))
    forbidden_values = (
        "iphone",
        "safari",
        "remote-ops",
        "gg-ro",
        "ux_iphone",
        "a22",
        "simplify-live-product-path",
        "repeat-exact-head-audit",
        "inc178-longitudinal",
    )
    if any(marker in value for value in semantic_values for marker in forbidden_values):
        raise ValueError("generic whole-goal contract test retained product or incident sample values")
    return generic


def _threshold_reasons(value: dict[str, Any]) -> set[str]:
    whole = value.get("whole_goal", {}) or {}
    progress = value.get("progress_deltas", {}) or {}
    thresholds = value.get("thresholds", {}) or {}
    counters = value.get("counters", {}) or {}
    heartbeat = value.get("heartbeat_self_health", {}) or {}
    skills = value.get("skill_firing", {}) or {}
    gate = value.get("gate_burden", {}) or {}
    reasons: set[str] = set()
    visible_zero = (progress.get("user_visible_capability_delta") or {}).get("classification") == "zero"
    runtime_zero = (progress.get("runtime_milestone_delta") or {}).get("classification") == "zero"
    elapsed = whole.get("whole_goal_elapsed_ms", -1)
    elapsed_stopline = max(
        thresholds.get("elapsed_zero_delta_ms", 3_600_000),
        2 * whole.get("expected_completion_max_ms", 0),
    )
    if visible_zero and _nonnegative_int(elapsed) and elapsed > elapsed_stopline:
        reasons.add("elapsed_zero_visible_delta")
    if counters.get("chained_implementation_blocks", 0) > thresholds.get("max_chained_implementation_blocks", 3):
        reasons.add("chained_implementation_blocks")
    if counters.get("consecutive_zero_visible_delta_slices", 0) >= thresholds.get("zero_delta_slice_limit", 3):
        reasons.add("zero_visible_delta_slices")
    if counters.get("distinct_causal_blocker_count", 0) >= thresholds.get("distinct_causal_blocker_limit", 3):
        reasons.add("distinct_causal_blockers")
    if runtime_zero and counters.get("protected_mutation_or_pair_count", 0) >= thresholds.get("protected_mutation_without_milestone_limit", 2):
        reasons.add("protected_mutations_without_runtime_milestone")
    ratio = whole.get("estimate_error_ratio")
    if visible_zero and _number(ratio) and ratio > thresholds.get("estimate_error_ratio_limit", 2.0):
        reasons.add("estimate_overrun_without_visible_delta")
    repeated = counters.get("repeated_warning_classes", []) or []
    if any(isinstance(row, dict) and row.get("count", 0) >= thresholds.get("same_warning_replan_count", 2) for row in repeated):
        reasons.add("repeated_user_warning")
    if heartbeat.get("local_activity_present") is True and heartbeat.get("activity_class") in {"support", "audit", "ci"} and "elapsed_zero_visible_delta" in reasons:
        reasons.add("active_lane_masking_whole_goal_stagnation")
    if skills.get("non_fires"):
        reasons.add("skill_nonfire")
    if gate.get("actual_ms", 0) > gate.get("budget_ms", 0):
        reasons.add("gate_burden_overrun")
    return reasons


def _derived_value_first_packet(value: Any, blocks: list[str]) -> dict[str, Any]:
    """Build the smallest in-memory CMD decision from the current transition.

    The whole-goal checker is a Claim Check.  Its unsupported claims must not
    become a work stop.  A selected exact Authority action remains the one
    exception and is never bypassed by the cost comparison.
    """
    body = value if isinstance(value, dict) else {}
    action = body.get("action_selection") if isinstance(body.get("action_selection"), dict) else {}
    candidates = action.get("candidate_actions") if isinstance(action.get("candidate_actions"), list) else []
    selected_id = action.get("selected_action_id") or "continue-safe-local-work"
    selected = next(
        (row for row in candidates if isinstance(row, dict) and row.get("action_id") == selected_id),
        {},
    )
    counters = body.get("counters") if isinstance(body.get("counters"), dict) else {}
    timing = body.get("time_accounting") if isinstance(body.get("time_accounting"), dict) else {}
    user_epoch = counters.get("user_correction_count", 0)
    if not _nonnegative_int(user_epoch):
        user_epoch = 0
    selected_class = action.get("selected_action_class")
    immediate_authority = selected_class == "exact_authority_blocker"
    support_minutes = timing.get("support_work_elapsed_ms", 0) / 60_000
    prompt_elapsed_ms = timing.get("prompt_preparation_elapsed_ms", 0)
    prompt_minutes = (
        prompt_elapsed_ms / 60_000
        if _number(prompt_elapsed_ms) and prompt_elapsed_ms >= 0
        else 0
    )
    manual_relay = counters.get("user_relay_count", 0)
    false_blocks = counters.get("false_block_count", 0)
    paid_cost = counters.get("avoidable_model_cost_count", 0)
    packet = {
        "user_intent_epoch": user_epoch,
        "dispatch_epoch": user_epoch,
        "proposed_action": {
            "action_id": selected_id if immediate_authority else "mk-whole-goal-claim-check-stop",
            "action_class": "authority" if immediate_authority else "validator",
            "user_capability_delta": selected.get("user_capability_delta_score", 0) if not blocks else 0,
            "cause_changing": not blocks and selected_class in {"product_capability_path", "cause_changing_prerequisite"},
            "immediate_authority_transition": immediate_authority,
            "protected_asset": selected_id if immediate_authority else None,
            "requested_control_effect": "HOLD" if immediate_authority else "STOP" if blocks else "CONTINUE",
            "authoring_defects": (
                selected.get("authoring_defects")
                if isinstance(selected.get("authoring_defects"), list)
                else action.get("authoring_defects")
                if isinstance(action.get("authoring_defects"), list)
                else []
            ),
        },
        "primary_action": {"action_id": selected_id},
        "critical_path": {
            "support_only_items_before_proposed": counters.get("evidence_only_slice_count", 0),
            "support_only_elapsed_minutes": support_minutes,
            "prompt_preparation_elapsed_minutes": prompt_minutes,
            "expected_prevented_loss": 1 if immediate_authority else 0,
            "user_capability_loss": 1 if blocks and not immediate_authority else 0,
            "paid_provider_cost": paid_cost if _nonnegative_int(paid_cost) else 0,
            "manual_relay_cost": manual_relay if _nonnegative_int(manual_relay) else 0,
            "false_block_cost": false_blocks if _nonnegative_int(false_blocks) else 0,
            "smallest_nonblocking_alternative": selected_id,
        },
        "removed_or_retired_controls": [
            "mandatory_phase_audit_before_next_safe_task",
            "task_checklist_evidence_missing_next_work_stop",
        ],
    }
    precision_judgment = body.get("precision_judgment")
    if isinstance(precision_judgment, dict):
        packet["precision_judgment"] = json.loads(json.dumps(precision_judgment))
    return packet


def _actor_from_sources(
    sources: list[Any], field: str, fields: set[str]
) -> dict[str, str] | None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        candidate = source.get(field)
        if _valid_actor_shape(candidate, fields, allow_empty=False):
            return json.loads(json.dumps(candidate))
    return None


def _cmd_state_from_sources(
    value: Any, packet: Any, cmd_state: Any
) -> dict[str, Any] | None:
    if isinstance(cmd_state, dict):
        return cmd_state
    for source in (packet, value):
        if not isinstance(source, dict):
            continue
        for field in ("cmd_state", "persisted_cmd_state", "epoch_state"):
            candidate = source.get(field)
            if isinstance(candidate, dict):
                return candidate
        if (
            isinstance(source.get("next_operation"), str)
            and "decision_owner" in source
            and "transport_actor" in source
        ):
            return source
    return None


def _origin_channel_diagnostics(value: Any, packet: Any) -> tuple[str | None, list[str]]:
    """Classify delivery by channel; packet claims cannot elevate authority."""
    for source in (packet, value):
        if not isinstance(source, dict) or "origin_channel" not in source:
            continue
        origin_channel = source.get("origin_channel")
        if not isinstance(origin_channel, str) or not origin_channel:
            return None, [WHOLE_GOAL_ORIGIN_CHANNEL_REQUIRED]
        if origin_channel not in ORIGIN_CHANNELS:
            return origin_channel, [WHOLE_GOAL_ORIGIN_CHANNEL_INVALID]
        diagnostics: list[str] = []
        if origin_channel != SOVEREIGN_ORIGIN_CHANNEL:
            provenance = source.get("peer_packet_provenance")
            if not isinstance(provenance, dict) or set(provenance) != PEER_PACKET_PROVENANCE_FIELDS:
                diagnostics.append(WHOLE_GOAL_PEER_PACKET_PROVENANCE_REQUIRED)
            elif (
                provenance.get("origin_channel") != origin_channel
                or any(
                    not isinstance(provenance.get(field), str)
                    or not provenance[field].strip()
                    for field in ("packet_id", "source_cmd_epoch_id", "source_surface")
                )
                or not _sha256(provenance.get("payload_digest"))
            ):
                diagnostics.append(WHOLE_GOAL_ORIGIN_PROVENANCE_MISMATCH)
            if (
                source.get("directive_origin")
                or source.get("content_authority_claim")
                or source.get("literal_authority_claim")
                or source.get("self_asserts_bindingness") is True
            ):
                diagnostics.append(PEER_CMD_DIRECTIVE_BINDING)
        return origin_channel, sorted(set(diagnostics))
    return None, [WHOLE_GOAL_ORIGIN_CHANNEL_REQUIRED]


def _operation_capable(
    value: Any,
    packet: Any,
    *,
    canonical_action_bound: bool,
    action_change_requested: bool,
) -> bool:
    """Identify requests that can bind a next operation or create dispatch."""
    if action_change_requested:
        return True
    state = _cmd_state_from_sources(value, packet, None)
    if (
        isinstance(state, dict)
        and isinstance(state.get("next_operation"), str)
        and bool(state["next_operation"].strip())
    ):
        return True
    if not canonical_action_bound:
        return False
    return any(
        isinstance(source, dict)
        and any(
            isinstance(source.get(field), (dict, str))
            and bool(source.get(field))
            for field in ("next_operation", "dispatch_request", "dispatch_args", "child_bootstrap")
        )
        for source in (packet, value)
    )


def _action_change_requested(
    value: Any,
    packet: Any,
    *,
    canonical_action_bound: bool,
    explicit_packet: bool,
) -> bool:
    if not canonical_action_bound or not isinstance(value, dict):
        return False
    action_selection = value.get("action_selection")
    canonical_action_id = (
        action_selection.get("selected_action_id")
        if isinstance(action_selection, dict)
        else None
    )
    if not isinstance(canonical_action_id, str) or not canonical_action_id.strip():
        return False
    state = _cmd_state_from_sources(value, packet, None)
    origin_bound = any(
        isinstance(source, dict) and "origin_channel" in source
        for source in (packet, value)
    )
    if explicit_packet and origin_bound and isinstance(packet, dict):
        for field in ("primary_action",):
            candidate = packet.get(field)
            if isinstance(candidate, dict):
                action_id = candidate.get("action_id")
                if isinstance(action_id, str) and action_id.strip() and action_id != canonical_action_id:
                    return True
    return (
        isinstance(state, dict)
        and state.get("cmd_release_state") == "active"
        and isinstance(state.get("next_operation"), str)
        and bool(state.get("next_operation").strip())
        and state.get("next_operation") != canonical_action_id
    )


def _recorded_next_operation_class(
    value: Any, next_operation: str
) -> tuple[str, dict[str, Any] | None]:
    action_selection = value.get("action_selection") if isinstance(value, dict) else None
    candidates = action_selection.get("candidate_actions") if isinstance(action_selection, dict) else None
    if not isinstance(candidates, list):
        return "untrusted", None
    row = next(
        (
            candidate for candidate in candidates
            if isinstance(candidate, dict)
            and candidate.get("action_id") == next_operation
        ),
        None,
    )
    if row is None:
        return "untrusted", None
    derived_class = _derived_action_class(row)
    if derived_class is None or row.get("action_class") != derived_class:
        return "untrusted", row
    if derived_class == "exact_authority_blocker":
        return "protected", row
    if derived_class in {"product_capability_path", "cause_changing_prerequisite"}:
        return "safe", row
    return "untrusted", row


def _decision_owner_context(
    value: Any,
    packet: Any,
    *,
    cmd_state: Any = None,
    decision_owner: Any = None,
    transport_actor: Any = None,
) -> dict[str, Any] | None:
    state = _cmd_state_from_sources(value, packet, cmd_state)
    if not isinstance(state, dict) or state.get("cmd_release_state") != "active":
        return None
    owner = (
        decision_owner
        if _valid_actor_shape(decision_owner, DECISION_OWNER_FIELDS, allow_empty=False)
        else _actor_from_sources(
            [state, packet, value], "decision_owner", DECISION_OWNER_FIELDS
        )
    )
    actor = (
        transport_actor
        if _valid_actor_shape(transport_actor, TRANSPORT_ACTOR_FIELDS, allow_empty=False)
        else _actor_from_sources(
            [packet, value, state], "transport_actor", TRANSPORT_ACTOR_FIELDS
        )
    )
    next_operation = state.get("next_operation")
    epoch_id = state.get("cmd_epoch_id")
    if (
        owner is None
        or actor is None
        or not isinstance(next_operation, str)
        or not next_operation.strip()
        or not isinstance(epoch_id, str)
        or not epoch_id.strip()
        or owner.get("epoch_id") != epoch_id
    ):
        return None
    classification, row = _recorded_next_operation_class(value, next_operation)
    action_selection = value.get("action_selection") if isinstance(value, dict) else None
    canonical_action_id = (
        action_selection.get("selected_action_id")
        if isinstance(action_selection, dict)
        else None
    )
    if not isinstance(canonical_action_id, str) or not canonical_action_id.strip():
        return None
    if canonical_action_id == next_operation:
        return None
    return {
        "state": state,
        "decision_owner": owner,
        "transport_actor": actor,
        "next_operation": next_operation,
        "classification": classification,
        "recorded_action": row,
        "canonical_action_id": canonical_action_id,
        "canonical_action_class": action_selection.get("selected_action_class"),
        "is_owner": (
            actor.get("role") == owner.get("role")
            and actor.get("surface") == owner.get("surface")
        ),
    }


def _protective_next_operation_packet(
    packet: dict[str, Any], next_operation: str
) -> dict[str, Any]:
    protected = json.loads(json.dumps(packet))
    proposed = protected.get("proposed_action")
    proposed = proposed if isinstance(proposed, dict) else {}
    proposed.update({
        "action_id": next_operation,
        "action_class": "authority",
        "immediate_authority_transition": True,
        "protected_asset": next_operation,
        "requested_control_effect": "HOLD",
    })
    protected["proposed_action"] = proposed
    protected["primary_action"] = {"action_id": next_operation}
    return protected


def consume_value_first_next_action(
    value: Any,
    blocks: list[str],
    value_first_packet: Any = None,
    *,
    cmd_state: Any = None,
    decision_owner: Any = None,
    transport_actor: Any = None,
) -> dict[str, Any]:
    """Consume the existing value-first resolver at the real stop/dispatch edge."""
    explicit_packet = isinstance(value_first_packet, dict)
    derived = _derived_value_first_packet(value, blocks)
    packet = (
        json.loads(json.dumps(value_first_packet))
        if isinstance(value_first_packet, dict)
        else derived
    )
    # A caller-provided packet may add a fresher user-intent epoch and bounded
    # cost context.  It may not either downgrade or elevate the action class
    # selected by the canonical whole-goal record.  Authority classification
    # is owned by that record (or a separately trusted authority consumer),
    # never by this untrusted convenience packet.
    derived_proposed = derived["proposed_action"]
    action_selection = value.get("action_selection") if isinstance(value, dict) else None
    canonical_action_bound = (
        isinstance(action_selection, dict)
        and isinstance(action_selection.get("selected_action_id"), str)
        and bool(action_selection["selected_action_id"].strip())
        and isinstance(action_selection.get("selected_action_class"), str)
        and bool(action_selection["selected_action_class"].strip())
    )
    action_change_requested = _action_change_requested(
        value,
        packet,
        canonical_action_bound=canonical_action_bound,
        explicit_packet=explicit_packet,
    )
    origin_channel, origin_diagnostics = _origin_channel_diagnostics(value, packet)
    owner_context = _decision_owner_context(
        value,
        packet,
        cmd_state=cmd_state,
        decision_owner=decision_owner,
        transport_actor=transport_actor,
    )
    operation_capable = _operation_capable(
        value,
        packet,
        canonical_action_bound=canonical_action_bound,
        action_change_requested=action_change_requested,
    )
    origin_binding_diagnostics = sorted(set(origin_diagnostics))
    if operation_capable and origin_binding_diagnostics:
        state = _cmd_state_from_sources(value, packet, cmd_state)
        preserved_next_operation = (
            state.get("next_operation")
            if isinstance(state, dict)
            and isinstance(state.get("next_operation"), str)
            and state.get("next_operation").strip()
            else ""
        )
        preserved_action_id = preserved_next_operation or (
            action_selection.get("selected_action_id")
            if isinstance(action_selection, dict)
            else ""
        )
        decision = resolve_value_first(packet)
        return {
            **decision,
            "ok": False,
            "decision": origin_binding_diagnostics[0],
            "reason": "operation_origin_and_peer_packet_provenance_binding_required",
            "origin_channel": origin_channel,
            "peer_packet_provenance_bound": (
                isinstance(origin_channel, str)
                and origin_channel == SOVEREIGN_ORIGIN_CHANNEL
            ) or (
                isinstance(origin_channel, str)
                and origin_channel in ORIGIN_CHANNELS
                and origin_channel != SOVEREIGN_ORIGIN_CHANNEL
                and not any(
                    diagnostic in origin_binding_diagnostics
                    for diagnostic in (
                        WHOLE_GOAL_PEER_PACKET_PROVENANCE_REQUIRED,
                        WHOLE_GOAL_ORIGIN_PROVENANCE_MISMATCH,
                    )
                )
            ),
            "advisory_recorded": True,
            "advisory_diagnostics": origin_binding_diagnostics,
            "typed_diagnostics": origin_binding_diagnostics,
            "work_continuation_allowed": True,
            "state": "CONTINUE",
            "non_wait": True,
            "typed_diagnostic_scope": "origin_channel_and_peer_packet_provenance_binding_only",
            "blocked_operations": [],
            "protected_boundary_stop": False,
            "protected_boundary_stops_only": True,
            "selected_action_id": preserved_action_id,
            "recorded_next_operation": preserved_next_operation,
            "next_operation_preserved": bool(preserved_next_operation),
            "operation_applied": False,
            "dispatch_applied": False,
            "advisory_proposal": {
                "action_id": packet.get("primary_action", {}).get("action_id")
                if isinstance(packet.get("primary_action"), dict)
                else "",
                "source": "unbound_operation_origin_advisory",
            },
            "consumer": "scripts/ops/mk_whole_goal_control.py",
            "unsupported_claims": sorted(set(blocks)),
            "state_mutated": False,
            "claim_check_failure_does_not_stop_safe_work": True,
        }
    owner_state_required = (
        action_change_requested
        and action_selection.get("selected_action_class") != "exact_authority_blocker"
    )
    if owner_state_required and owner_context is None:
        decision = resolve_value_first(packet)
        required_diagnostics = sorted(set(origin_diagnostics + [WHOLE_GOAL_DECISION_OWNER_REQUIRED]))
        return {
            **decision,
            "ok": False,
            "decision": WHOLE_GOAL_DECISION_OWNER_REQUIRED,
            "reason": "decision_owner_state_required_for_action_change",
            "origin_channel": origin_channel,
            "advisory_recorded": False,
            "advisory_diagnostics": sorted(set(origin_diagnostics)),
            "typed_diagnostics": required_diagnostics,
            "work_continuation_allowed": False,
            "state": "STOP",
            "non_wait": True,
            "typed_diagnostic_scope": "decision_owner_state_required_on_action_change",
            "blocked_operations": [WHOLE_GOAL_DECISION_OWNER_REQUIRED],
            "protected_boundary_stop": True,
            "protected_boundary_stops_only": True,
            "consumer": "scripts/ops/mk_whole_goal_control.py",
            "unsupported_claims": sorted(set(blocks)),
            "state_mutated": False,
            "claim_check_failure_does_not_stop_safe_work": False,
        }
    if owner_context and owner_context["classification"] in {"protected", "untrusted"}:
        protected_decision = resolve_value_first(
            _protective_next_operation_packet(packet, owner_context["next_operation"])
        )
        return {
            **protected_decision,
            "consumer": "scripts/ops/mk_whole_goal_control.py",
            "unsupported_claims": sorted(set(blocks)),
            "decision_owner": owner_context["decision_owner"],
            "transport_actor": owner_context["transport_actor"],
            "recorded_next_operation": owner_context["next_operation"],
            "safety_exception": (
                "recorded_next_operation_protected"
                if owner_context["classification"] == "protected"
                else "recorded_next_operation_untrusted"
            ),
            "state_mutated": False,
            "claim_check_failure_does_not_stop_safe_work": False,
        }
    if owner_context and not owner_context["is_owner"] and owner_context["classification"] == "safe":
        advisory_proposal = {
            "action_id": owner_context["canonical_action_id"],
            "action_class": owner_context["canonical_action_class"],
            "proposed_action": json.loads(json.dumps(derived["proposed_action"])),
            "primary_action": json.loads(json.dumps(derived["primary_action"])),
            "source": "canonical_whole_goal_action_selection",
        }
        packet["advisory_proposal"] = advisory_proposal
        decision = resolve_value_first(packet)
        advisory_diagnostics = sorted(set(
            [WHOLE_GOAL_DECISION_OWNER_OVERRIDE, *origin_diagnostics]
        ))
        return {
            **decision,
            "ok": False,
            "decision": WHOLE_GOAL_DECISION_OWNER_OVERRIDE,
            "selected_action_id": owner_context["next_operation"],
            "reason": "active_decision_owner_next_operation_cannot_be_replaced_by_transport_actor",
            "origin_channel": origin_channel,
            "advisory_recorded": True,
            "advisory_diagnostics": advisory_diagnostics,
            "typed_diagnostics": advisory_diagnostics,
            "work_continuation_allowed": True,
            "state": "CONTINUE",
            "non_wait": True,
            "typed_diagnostic_scope": "decision_owner_binding_only",
            "blocked_operations": [],
            "protected_boundary_stop": False,
            "protected_boundary_stops_only": True,
            "next_operation_preserved": True,
            "consumer": "scripts/ops/mk_whole_goal_control.py",
            "unsupported_claims": sorted(set(blocks)),
            "decision_owner": owner_context["decision_owner"],
            "transport_actor": owner_context["transport_actor"],
            "recorded_next_operation": owner_context["next_operation"],
            "canonical_action": {
                "action_id": owner_context["canonical_action_id"],
                "action_class": owner_context["canonical_action_class"],
            },
            "advisory_proposal": advisory_proposal,
            "decision_owner_override": {
                "blocker": WHOLE_GOAL_DECISION_OWNER_OVERRIDE,
                "recorded_next_operation": owner_context["next_operation"],
                "recorded_action": {
                    "action_id": owner_context["next_operation"],
                    "action_class": owner_context["recorded_action"].get("action_class")
                    if isinstance(owner_context["recorded_action"], dict)
                    else None,
                },
                "canonical_action": {
                    "action_id": owner_context["canonical_action_id"],
                    "action_class": owner_context["canonical_action_class"],
                },
                "decision_owner": owner_context["decision_owner"],
                "transport_actor": owner_context["transport_actor"],
                "state_mutated": False,
            },
            "state_mutated": False,
            "claim_check_failure_does_not_stop_safe_work": True,
        }
    if canonical_action_bound:
        proposed = packet.get("proposed_action") if isinstance(packet.get("proposed_action"), dict) else {}
        proposed.update({
            "action_id": derived_proposed["action_id"],
            "action_class": derived_proposed["action_class"],
            "user_capability_delta": derived_proposed["user_capability_delta"],
            "cause_changing": derived_proposed["cause_changing"],
            "immediate_authority_transition": derived_proposed["immediate_authority_transition"],
            "protected_asset": derived_proposed["protected_asset"],
            "requested_control_effect": derived_proposed["requested_control_effect"],
            "authoring_defects": derived_proposed["authoring_defects"],
        })
        packet["proposed_action"] = proposed
        packet["primary_action"] = json.loads(json.dumps(derived["primary_action"]))
        if "precision_judgment" in derived:
            packet["precision_judgment"] = json.loads(json.dumps(derived["precision_judgment"]))
        else:
            packet.pop("precision_judgment", None)
    else:
        proposed = packet.get("proposed_action") if isinstance(packet.get("proposed_action"), dict) else {}
        if (
            proposed.get("action_class") == "authority"
            or proposed.get("immediate_authority_transition") is True
        ):
            proposed.update({
                "action_class": "validator",
                "immediate_authority_transition": False,
                "protected_asset": None,
                "requested_control_effect": "HOLD",
            })
            packet["proposed_action"] = proposed
    decision = resolve_value_first(packet)
    result = {
        **decision,
        "consumer": "scripts/ops/mk_whole_goal_control.py",
        "unsupported_claims": sorted(set(blocks)),
        "claim_check_failure_does_not_stop_safe_work": (
            bool(blocks) and decision.get("decision") != "WAIT_EXACT_AUTHORITY"
        ),
    }
    if owner_context:
        result.update({
            "decision_owner": owner_context["decision_owner"],
            "transport_actor": owner_context["transport_actor"],
            "recorded_next_operation": owner_context["next_operation"],
            "canonical_derivation_authorized": owner_context["is_owner"],
            "state_mutated": False,
        })
    return result


def _live_transition_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    return compact[:maximum] if compact else None


def _live_transition_source_event(material_result: Any) -> dict[str, Any]:
    if not isinstance(material_result, dict):
        return {}
    candidate = material_result.get("material_event")
    if not isinstance(candidate, dict):
        candidate = material_result.get("source_event")
    return candidate if isinstance(candidate, dict) else {}


def _live_transition_claim_containers(
    source_event: dict[str, Any],
    stop_event: Any,
) -> list[dict[str, Any]]:
    containers = [source_event]
    if isinstance(stop_event, dict):
        containers.append(stop_event)
    for container in list(containers):
        claims = container.get("claims")
        if isinstance(claims, dict):
            containers.append(claims)
    return containers


def _observation_ref(value: Any, maximum: int = 160) -> str | None:
    """Return a bounded identifier/reference, never raw command or payload text."""
    text = _live_transition_text(value, maximum)
    if text is None or any(ord(character) < 32 for character in text):
        return None
    return text


def _observation_shape(value: Any) -> str | None:
    text = _observation_ref(value, 80)
    if text is None:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-[]{}()")
    return text if all(character in allowed for character in text) else None


def _observation_provenance_ref(value: Any) -> str | None:
    """Accept only an opaque digest or identifier-shaped producer/origin ref."""
    text = _observation_ref(value, 240)
    if text is None:
        return None
    if _sha256(text):
        return text
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-#@"
    )
    return text if all(character in allowed for character in text) else None


def _observation_input(
    material_result: Any,
    source_event: dict[str, Any],
    stop_event: Any,
) -> dict[str, Any] | None:
    """Select the first structured optional observation already at the Stop edge."""
    candidates: list[Any] = [source_event.get("observability")]
    if isinstance(material_result, dict):
        candidates.append(material_result.get("observability"))
    if isinstance(stop_event, dict):
        candidates.append(stop_event.get("observability"))
    return next((item for item in candidates if isinstance(item, dict)), None)


def _evaluate_observability_thresholds(
    material_result: Any,
    source_event: dict[str, Any],
    stop_event: Any,
) -> dict[str, Any]:
    """Evaluate TH-OBS-1..10 without creating a new gate or runtime family."""
    observation = _observation_input(material_result, source_event, stop_event)
    rows: dict[str, dict[str, Any]] = {
        threshold_id: {
            "threshold_id": threshold_id,
            "status": "NOT_EXERCISED",
            "hold_scope": None,
        }
        for threshold_id in OBSERVABILITY_THRESHOLD_IDS
    }
    held_operations: list[str] = []
    disjoint_continuations: list[str] = []
    action_candidates: list[tuple[int, str, str]] = []

    def set_row(
        threshold_id: str,
        status: str,
        *,
        hold_scope: str | None = None,
        **details: Any,
    ) -> None:
        row = rows[threshold_id]
        row["status"] = status
        row["hold_scope"] = hold_scope
        row.update(details)
        if hold_scope is not None:
            held_operations.append(hold_scope)

    if observation is not None:
        repeated = observation.get("same_finding")
        if isinstance(repeated, dict):
            finding_id = _observation_provenance_ref(
                repeated.get("finding_id")
            )
            rounds = repeated.get("independent_rounds")
            if finding_id is None or not isinstance(rounds, list):
                set_row(
                    "TH-OBS-1",
                    "UNKNOWN",
                    reason="insufficient_independent_round_evidence",
                )
            else:
                round_ids = {
                    value
                    for item in rounds
                    if (value := _observation_provenance_ref(item)) is not None
                }
                if len(round_ids) >= 3:
                    action = f"Re-evaluate a different property or design for {finding_id}."
                    set_row(
                        "TH-OBS-1",
                        "TRIGGERED",
                        finding_id=finding_id,
                        independent_round_count=len(round_ids),
                        selected_action="different_property_design_reevaluation",
                        telemetry_only_count=True,
                    )
                    action_candidates.append((20, "TH-OBS-1", action))
                else:
                    set_row(
                        "TH-OBS-1",
                        "NOT_TRIGGERED",
                        finding_id=finding_id,
                        independent_round_count=len(round_ids),
                        telemetry_only_count=True,
                    )

        drift = observation.get("premise_drift")
        if isinstance(drift, dict):
            affected = _observation_provenance_ref(
                drift.get("affected_transition")
            )
            premise = drift.get("premise")
            measured = drift.get("measured")
            if (
                affected is None
                or not isinstance(premise, dict)
                or not isinstance(measured, dict)
            ):
                set_row(
                    "TH-OBS-2",
                    "UNKNOWN",
                    reason="premise_or_measurement_missing",
                )
            else:
                bounded_diff: dict[str, dict[str, Any]] = {}
                invalid = False
                for field in ("head", "branch", "count"):
                    if field not in premise and field not in measured:
                        continue
                    left = premise.get(field)
                    right = measured.get(field)
                    if field == "count":
                        if not (
                            isinstance(left, int)
                            and not isinstance(left, bool)
                            and left >= 0
                            and isinstance(right, int)
                            and not isinstance(right, bool)
                            and right >= 0
                        ):
                            invalid = True
                            continue
                    else:
                        left = _observation_provenance_ref(left)
                        right = _observation_provenance_ref(right)
                        if left is None or right is None:
                            invalid = True
                            continue
                    if left != right:
                        bounded_diff[field] = {"premise": left, "measured": right}
                disjoint = _observation_ref(drift.get("disjoint_continuation"), 1000)
                if invalid or not any(field in premise or field in measured for field in ("head", "branch", "count")):
                    set_row(
                        "TH-OBS-2",
                        "UNKNOWN",
                        reason="unsanitized_or_empty_drift_measurement",
                    )
                elif bounded_diff:
                    scope = f"affected_transition:{affected}"
                    set_row(
                        "TH-OBS-2",
                        "TRIGGERED",
                        hold_scope=scope,
                        affected_transition=affected,
                        sanitized_diff=bounded_diff,
                        selected_action="refresh_premise_then_retry_affected_transition",
                        disjoint_continuation=disjoint,
                    )
                    action_candidates.append(
                        (
                            30,
                            "TH-OBS-2",
                            f"Refresh the measured premise for {affected}.",
                        )
                    )
                    if disjoint is not None:
                        disjoint_continuations.append(disjoint)
                else:
                    set_row(
                        "TH-OBS-2",
                        "NOT_TRIGGERED",
                        affected_transition=affected,
                        sanitized_diff={},
                    )

        metric = observation.get("decision_metric")
        if isinstance(metric, dict):
            metric_id = _observation_provenance_ref(metric.get("metric_id"))
            relevant = metric.get("decision_relevant")
            origin_ref = (
                _observation_provenance_ref(metric.get("source_command_ref"))
                or _observation_provenance_ref(metric.get("data_origin_ref"))
                or _observation_provenance_ref(metric.get("producer_ref"))
            )
            if metric_id is None or not isinstance(relevant, bool):
                set_row("TH-OBS-3", "UNKNOWN", reason="metric_identity_or_relevance_missing")
            elif relevant and origin_ref is None:
                set_row(
                    "TH-OBS-3",
                    "TRIGGERED",
                    hold_scope=f"acceptance_metric:{metric_id}",
                    metric_id=metric_id,
                    source_status="missing_sanitized_source_reference",
                    selected_action="exclude_metric_from_acceptance",
                )
            else:
                set_row(
                    "TH-OBS-3",
                    "NOT_TRIGGERED",
                    metric_id=metric_id,
                    source_status=(
                        "sanitized_reference_present"
                        if origin_ref
                        else "not_decision_relevant"
                    ),
                    source_ref=origin_ref,
                )

        evidence = observation.get("primary_evidence")
        if isinstance(evidence, dict):
            untracked = evidence.get("untracked")
            destructive = evidence.get("destructive_or_cleanup_requested")
            evidence_ref = _observation_provenance_ref(
                evidence.get("evidence_ref")
            )
            operation = (
                _observation_provenance_ref(evidence.get("operation"))
                or "evidence_cleanup"
            )
            rescue = _observation_ref(evidence.get("snapshot_rescue_action"), 1000)
            if not isinstance(untracked, bool) or not isinstance(
                destructive,
                bool,
            ):
                set_row(
                    "TH-OBS-4",
                    "UNKNOWN",
                    reason="evidence_tracking_or_operation_state_missing",
                )
            elif untracked and destructive:
                set_row(
                    "TH-OBS-4",
                    "TRIGGERED",
                    hold_scope=f"destructive_or_cleanup:{operation}",
                    evidence_ref=evidence_ref,
                    selected_action="snapshot_rescue_first",
                    rescue_progress_credit=0,
                )
                action_candidates.append(
                    (
                        10,
                        "TH-OBS-4",
                        rescue
                        or "Create a sanitized snapshot rescue before cleanup.",
                    )
                )
            else:
                set_row("TH-OBS-4", "NOT_TRIGGERED", evidence_ref=evidence_ref)

        human_gate = observation.get("human_gate")
        if isinstance(human_gate, dict):
            waiting = human_gate.get("waiting")
            gate_id = _observation_provenance_ref(human_gate.get("gate_id"))
            gate_kind = _observation_provenance_ref(
                human_gate.get("gate_kind")
            )
            surface_ref = (
                _observation_provenance_ref(human_gate.get("surface_ref"))
                or gate_id
            )
            disjoint = _observation_ref(human_gate.get("disjoint_continuation"), 1000)
            deterministic = human_gate.get("deterministic_invariant_route")
            learned = human_gate.get("learned_low_risk_route")
            cooling_veto = human_gate.get("cooling_veto_route")
            system_review = human_gate.get("system_review_route")
            route_evaluations: dict[str, dict[str, Any]] = {
                "deterministic_invariant": {"status": "NOT_EXERCISED"},
                "learned_low_risk": {"status": "NOT_EXERCISED"},
                "cooling_veto": {"status": "NOT_EXERCISED"},
                "system_review": {"status": "NOT_EXERCISED"},
            }
            selected_route: dict[str, Any] | None = None
            strict_consent_kinds = {
                "true_user_consent",
                "external_consent",
            }

            if not isinstance(waiting, bool):
                set_row(
                    "TH-OBS-5",
                    "UNKNOWN",
                    reason="human_gate_state_missing",
                    route_evaluations=route_evaluations,
                )
            elif not waiting:
                set_row(
                    "TH-OBS-5",
                    "NOT_TRIGGERED",
                    residual_gate=gate_id,
                    route_evaluations=route_evaluations,
                )
            else:
                if disjoint is not None:
                    disjoint_continuations.append(disjoint)
                if gate_id is None or surface_ref is None or gate_kind is None:
                    hold_id = surface_ref or gate_id or "unidentified"
                    set_row(
                        "TH-OBS-5",
                        "UNKNOWN",
                        hold_scope=f"human_gate_resolution:{hold_id}",
                        reason="gate_identity_kind_or_surface_missing",
                        queued=False,
                        disjoint_continuation=disjoint,
                        route_evaluations=route_evaluations,
                    )
                elif gate_kind in strict_consent_kinds:
                    route_evaluations = {
                        "deterministic_invariant": {
                            "status": "NOT_APPLICABLE_STRICT_CONSENT",
                        },
                        "learned_low_risk": {
                            "status": "NOT_APPLICABLE_STRICT_CONSENT",
                        },
                        "cooling_veto": {
                            "status": "NOT_APPLICABLE_STRICT_CONSENT",
                        },
                        "system_review": {
                            "status": "NOT_APPLICABLE_STRICT_CONSENT",
                        },
                    }
                    set_row(
                        "TH-OBS-5",
                        "TRIGGERED",
                        hold_scope=f"human_gate:{gate_id}",
                        selected_route="true_user_or_external_consent_residual",
                        residual_gate=gate_id,
                        residual_gate_kind=gate_kind,
                        queued=True,
                        disjoint_continuation=disjoint,
                        route_evaluations=route_evaluations,
                    )
                else:
                    if isinstance(deterministic, dict):
                        route_ref = _observation_provenance_ref(
                            deterministic.get("route_ref")
                        )
                        invariant_ref = _observation_provenance_ref(
                            deterministic.get("invariant_ref")
                        )
                        decision_ref = _observation_provenance_ref(
                            deterministic.get("policy_decision_ref")
                        )
                        required_bools = {
                            "existing_route": deterministic.get("existing_route"),
                            "route_available": deterministic.get("route_available"),
                            "surface_explicitly_admitted": deterministic.get(
                                "surface_explicitly_admitted"
                            ),
                            "invariant_established": deterministic.get(
                                "invariant_established"
                            ),
                            "policy_decision_established": deterministic.get(
                                "policy_decision_established"
                            ),
                            "reversible_decision": deterministic.get(
                                "reversible_decision"
                            ),
                        }
                        if (
                            route_ref is None
                            or invariant_ref is None
                            or decision_ref is None
                            or any(
                                not isinstance(value, bool)
                                for value in required_bools.values()
                            )
                        ):
                            route_evaluations["deterministic_invariant"] = {
                                "status": "UNKNOWN",
                                "reason": "explicit_invariant_route_policy_or_reversibility_input_missing",
                            }
                        elif all(required_bools.values()):
                            route_evaluations["deterministic_invariant"] = {
                                "status": "SELECTED",
                                "route_ref": route_ref,
                                "invariant_ref": invariant_ref,
                                "policy_decision_ref": decision_ref,
                                "qualification_inferred": False,
                            }
                            selected_route = {
                                "kind": "existing_deterministic_invariant",
                                "route_ref": route_ref,
                                "action": (
                                    "Apply established reversible deterministic decision "
                                    f"{decision_ref} through {route_ref}."
                                ),
                            }
                        else:
                            route_evaluations["deterministic_invariant"] = {
                                "status": "NOT_SELECTED",
                                "reason": "available_admitted_established_reversible_invariant_not_proven",
                                "qualification_inferred": False,
                            }

                    if selected_route is None and isinstance(learned, dict):
                        route_ref = _observation_provenance_ref(
                            learned.get("route_ref")
                        )
                        action_ref = _observation_provenance_ref(
                            learned.get("action_ref")
                        )
                        version_ref = _observation_provenance_ref(
                            learned.get("version_ref")
                        )
                        scope_ref = _observation_provenance_ref(
                            learned.get("scope_ref")
                        )
                        observations_ref = _observation_provenance_ref(
                            learned.get("actual_observations_ref")
                        )
                        qualification_result_ref = _observation_provenance_ref(
                            learned.get("qualification_result_ref")
                        )
                        wilson_ref = _observation_provenance_ref(
                            learned.get("wilson_policy_result_ref")
                        )
                        decision_ref = _observation_provenance_ref(
                            learned.get("policy_decision_ref")
                        )
                        qualification_profile = learned.get(
                            "qualification_profile"
                        )
                        measurement_status = learned.get("measurement_status")
                        required_bools = {
                            "existing_route": learned.get("existing_route"),
                            "route_available": learned.get("route_available"),
                            "surface_explicitly_admitted": learned.get(
                                "surface_explicitly_admitted"
                            ),
                            "actual_observations_established": learned.get(
                                "actual_observations_established"
                            ),
                            "wilson_policy_result_established": learned.get(
                                "wilson_policy_result_established"
                            ),
                            "policy_decision_established": learned.get(
                                "policy_decision_established"
                            ),
                            "reversible_decision": learned.get(
                                "reversible_decision"
                            ),
                        }
                        if (
                            route_ref is None
                            or action_ref is None
                            or version_ref is None
                            or scope_ref is None
                            or observations_ref is None
                            or qualification_result_ref is None
                            or wilson_ref is None
                            or decision_ref is None
                            or qualification_profile
                            != PMS_LEARNED_LOW_RISK_QUALIFICATION_PROFILE
                            or measurement_status
                            not in {"ESTABLISHED", "NOT_MEASURABLE"}
                            or any(
                                not isinstance(value, bool)
                                for value in required_bools.values()
                            )
                        ):
                            route_evaluations["learned_low_risk"] = {
                                "status": "UNKNOWN",
                                "reason": "bound_qualification_policy_or_route_input_missing",
                                "wilson_result_computed": False,
                                "observation_count_inferred": False,
                            }
                        elif measurement_status == "NOT_MEASURABLE":
                            route_evaluations["learned_low_risk"] = {
                                "status": "NOT_MEASURABLE",
                                "reason": (
                                    "insufficient_natural_action_denominator_window_"
                                    "or_enabled_class_coverage"
                                ),
                                "qualification_profile": qualification_profile,
                                "qualification_result_ref": qualification_result_ref,
                                "wilson_result_computed": False,
                                "observation_count_inferred": False,
                            }
                        elif all(required_bools.values()):
                            route_evaluations["learned_low_risk"] = {
                                "status": "SELECTED",
                                "route_ref": route_ref,
                                "action_ref": action_ref,
                                "version_ref": version_ref,
                                "scope_ref": scope_ref,
                                "actual_observations_ref": observations_ref,
                                "qualification_profile": qualification_profile,
                                "qualification_result_ref": qualification_result_ref,
                                "wilson_policy_result_ref": wilson_ref,
                                "policy_decision_ref": decision_ref,
                                "wilson_result_computed": False,
                                "observation_count_inferred": False,
                            }
                            selected_route = {
                                "kind": "existing_learned_low_risk_policy",
                                "route_ref": route_ref,
                                "action_ref": action_ref,
                                "version_ref": version_ref,
                                "scope_ref": scope_ref,
                                "action": (
                                    "Apply established reversible observed-policy decision "
                                    f"{decision_ref} through {route_ref}."
                                ),
                            }
                        else:
                            route_evaluations["learned_low_risk"] = {
                                "status": "NOT_SELECTED",
                                "reason": "available_admitted_observed_wilson_reversible_policy_not_proven",
                                "wilson_result_computed": False,
                                "observation_count_inferred": False,
                            }

                    if selected_route is None and isinstance(cooling_veto, dict):
                        route_ref = _observation_provenance_ref(
                            cooling_veto.get("route_ref")
                        )
                        decision_ref = _observation_provenance_ref(
                            cooling_veto.get("cooling_veto_decision_ref")
                        )
                        required_bools = {
                            "existing_route": cooling_veto.get("existing_route"),
                            "route_available": cooling_veto.get("route_available"),
                            "surface_explicitly_admitted": cooling_veto.get(
                                "surface_explicitly_admitted"
                            ),
                            "cooling_veto_decision_established": cooling_veto.get(
                                "cooling_veto_decision_established"
                            ),
                            "reversible_decision": cooling_veto.get(
                                "reversible_decision"
                            ),
                        }
                        if (
                            route_ref is None
                            or decision_ref is None
                            or any(
                                not isinstance(value, bool)
                                for value in required_bools.values()
                            )
                        ):
                            route_evaluations["cooling_veto"] = {
                                "status": "UNKNOWN",
                                "reason": "explicit_cooling_veto_route_or_reversibility_input_missing",
                            }
                        elif all(required_bools.values()):
                            route_evaluations["cooling_veto"] = {
                                "status": "SELECTED",
                                "route_ref": route_ref,
                                "cooling_veto_decision_ref": decision_ref,
                            }
                            selected_route = {
                                "kind": "existing_cooling_veto",
                                "route_ref": route_ref,
                                "action": (
                                    "Apply established reversible cooling and veto decision "
                                    f"{decision_ref} through {route_ref}."
                                ),
                            }
                        else:
                            route_evaluations["cooling_veto"] = {
                                "status": "NOT_SELECTED",
                                "reason": "available_admitted_established_reversible_cooling_veto_not_proven",
                            }

                    if selected_route is None and isinstance(system_review, dict):
                        route_ref = _observation_provenance_ref(
                            system_review.get("route_ref")
                        )
                        proposer_id = _observation_provenance_ref(
                            system_review.get("proposer_id")
                        )
                        reviewer_id = _observation_provenance_ref(
                            system_review.get("reviewer_id")
                        )
                        reviewer_authority_ref = _observation_provenance_ref(
                            system_review.get("reviewer_authority_ref")
                        )
                        decision_semantics_ref = _observation_provenance_ref(
                            system_review.get("decision_semantics_ref")
                        )
                        decision = _observation_provenance_ref(
                            system_review.get("decision")
                        )
                        required_bools = {
                            "existing_route": system_review.get(
                                "existing_route"
                            ),
                            "route_available": system_review.get(
                                "route_available"
                            ),
                            "surface_permits_system_review": system_review.get(
                                "surface_permits_system_review"
                            ),
                            "reversible_decision": system_review.get(
                                "reversible_decision"
                            ),
                        }
                        if (
                            route_ref is None
                            or proposer_id is None
                            or reviewer_id is None
                            or reviewer_authority_ref is None
                            or decision_semantics_ref is None
                            or decision not in {"accept", "reject", "defer"}
                            or any(
                                not isinstance(value, bool)
                                for value in required_bools.values()
                            )
                        ):
                            route_evaluations["system_review"] = {
                                "status": "UNKNOWN",
                                "reason": "explicit_route_identity_authority_or_reversibility_input_missing",
                            }
                        elif proposer_id == reviewer_id:
                            route_evaluations["system_review"] = {
                                "status": "NOT_SELECTED",
                                "reason": "proposer_and_reviewer_must_differ",
                            }
                        elif all(required_bools.values()):
                            route_evaluations["system_review"] = {
                                "status": "SELECTED",
                                "route_ref": route_ref,
                                "proposer_id": proposer_id,
                                "reviewer_id": reviewer_id,
                                "reviewer_authority_ref": reviewer_authority_ref,
                                "decision_semantics_ref": decision_semantics_ref,
                                "decision": decision,
                                "authority_inferred": False,
                            }
                            selected_route = {
                                "kind": "existing_independent_system_review",
                                "route_ref": route_ref,
                                "proposer_id": proposer_id,
                                "reviewer_id": reviewer_id,
                                "action": (
                                    f"Apply the reversible {decision} decision for "
                                    f"{surface_ref} through {route_ref}."
                                ),
                            }
                        else:
                            route_evaluations["system_review"] = {
                                "status": "NOT_SELECTED",
                                "reason": "existing_permitted_reversible_system_review_not_proven",
                                "authority_inferred": False,
                            }

                    if selected_route is not None:
                        set_row(
                            "TH-OBS-5",
                            "PASS",
                            selected_route=selected_route["kind"],
                            route_ref=selected_route["route_ref"],
                            queued=False,
                            disjoint_continuation=disjoint,
                            route_evaluations=route_evaluations,
                            policy_qualification_inferred=False,
                            natural_firing_inferred=False,
                            observed_success_inferred=False,
                            content_authority_inferred=False,
                        )
                        action_candidates.append(
                            (25, "TH-OBS-5", selected_route["action"])
                        )
                    else:
                        residual = human_gate.get("residual_gate")
                        residual = residual if isinstance(residual, dict) else {}
                        residual_id = _observation_provenance_ref(
                            residual.get("gate_id")
                        )
                        residual_kind = _observation_provenance_ref(
                            residual.get("gate_kind")
                        )
                        unresolved = residual.get("unresolved")
                        if (
                            residual_id is not None
                            and residual_kind in strict_consent_kinds
                            and unresolved is True
                        ):
                            set_row(
                                "TH-OBS-5",
                                "TRIGGERED",
                                hold_scope=f"human_gate:{residual_id}",
                                selected_route="true_user_or_external_consent_residual",
                                residual_gate=residual_id,
                                residual_gate_kind=residual_kind,
                                queued=True,
                                disjoint_continuation=disjoint,
                                route_evaluations=route_evaluations,
                            )
                        else:
                            set_row(
                                "TH-OBS-5",
                                "UNKNOWN",
                                hold_scope=f"human_gate_resolution:{surface_ref}",
                                reason="no_admitted_route_or_explicit_unresolved_consent_residual",
                                queued=False,
                                disjoint_continuation=disjoint,
                                route_evaluations=route_evaluations,
                            )

        control = observation.get("new_control")
        if isinstance(control, dict):
            requested = control.get("requested")
            kind = _observation_provenance_ref(control.get("control_kind"))
            comparisons = control.get("existing_connection_comparisons")
            if (
                not isinstance(requested, bool)
                or (requested and kind is None)
                or not isinstance(comparisons, list)
            ):
                set_row("TH-OBS-6", "UNKNOWN", reason="control_request_or_comparison_missing")
            else:
                comparison_refs = [
                    value
                    for item in comparisons
                    if (value := _observation_provenance_ref(item)) is not None
                ]
                if requested and not comparison_refs:
                    set_row(
                        "TH-OBS-6",
                        "TRIGGERED",
                        hold_scope=f"new_control_creation:{kind}",
                        control_kind=kind,
                        existing_connection_comparison_count=0,
                    )
                else:
                    set_row(
                        "TH-OBS-6",
                        "NOT_TRIGGERED",
                        control_kind=kind,
                        existing_connection_comparison_count=len(comparison_refs),
                    )

        unknown = observation.get("unknown_classification")
        if isinstance(unknown, dict):
            is_unknown = unknown.get("is_unknown")
            classification = _observation_provenance_ref(
                unknown.get("classification")
            )
            digest = unknown.get("store_salted_digest")
            length = unknown.get("length")
            shape = _observation_shape(unknown.get("shape"))
            raw_supplied = any(key in unknown for key in ("raw", "raw_text", "content", "value"))
            valid_digest = isinstance(digest, str) and _sha256(digest)
            valid_length = isinstance(length, int) and not isinstance(length, bool) and length >= 0
            if not isinstance(is_unknown, bool):
                set_row(
                    "TH-OBS-7",
                    "UNKNOWN",
                    reason="unknown_state_missing",
                    raw_content_rejected=raw_supplied,
                )
            elif is_unknown and classification in {"other", "closed_other"}:
                set_row(
                    "TH-OBS-7",
                    "TRIGGERED",
                    hold_scope="unknown_classification_acceptance",
                    reason="closed_other_rejected",
                    raw_content_rejected=raw_supplied,
                )
            elif is_unknown:
                if raw_supplied:
                    set_row(
                        "TH-OBS-7",
                        "UNKNOWN",
                        hold_scope="unknown_classification_acceptance",
                        reason="raw_unknown_content_rejected",
                        raw_content_rejected=True,
                    )
                elif (
                    classification != "open_safe_degradation"
                    or not valid_digest
                    or not valid_length
                    or (length >= 16 and shape is None)
                ):
                    set_row(
                        "TH-OBS-7",
                        "UNKNOWN",
                        hold_scope="unknown_classification_acceptance",
                        reason="safe_open_degradation_metadata_incomplete",
                        raw_content_rejected=raw_supplied,
                    )
                else:
                    exposed: dict[str, Any] = {"store_salted_digest": digest}
                    if length >= 16:
                        exposed.update({"shape": shape, "length": length})
                    set_row(
                        "TH-OBS-7",
                        "PASS",
                        degradation="open_safe",
                        exposed_metadata=exposed,
                        raw_content_rejected=raw_supplied,
                    )
            else:
                set_row("TH-OBS-7", "NOT_TRIGGERED", raw_content_rejected=raw_supplied)

        cleanup = observation.get("evidence_cleanup")
        if isinstance(cleanup, dict):
            requested = cleanup.get("requested")
            touches = cleanup.get("touches_evidence")
            sealed = cleanup.get("capsule_sealed")
            operation = (
                _observation_provenance_ref(cleanup.get("operation"))
                or "evidence_deletion"
            )
            if (
                not isinstance(requested, bool)
                or not isinstance(touches, bool)
                or (
                    requested
                    and touches
                    and not isinstance(sealed, bool)
                )
            ):
                set_row("TH-OBS-8", "UNKNOWN", reason="cleanup_or_capsule_fact_missing")
            elif requested and touches and not sealed:
                set_row(
                    "TH-OBS-8",
                    "TRIGGERED",
                    hold_scope=f"evidence_deletion:{operation}",
                    capsule_sealed=False,
                )
            elif requested and touches and sealed:
                set_row("TH-OBS-8", "PASS", capsule_sealed=True)
            else:
                set_row("TH-OBS-8", "NOT_TRIGGERED")

        recon = observation.get("parallel_recon")
        if isinstance(recon, dict):
            requested = recon.get("requested")
            lanes = recon.get("lanes")
            if not isinstance(requested, bool) or (
                requested and not isinstance(lanes, list)
            ):
                set_row("TH-OBS-9", "UNKNOWN", reason="parallel_recon_state_missing")
            elif requested:
                lane_rows = [item for item in lanes if isinstance(item, dict)]
                authorities = [
                    value
                    for item in lane_rows
                    if (
                        value := _observation_provenance_ref(
                            item.get("fact_authority")
                        )
                    ) is not None
                ]
                lane_ids = [
                    value
                    for item in lane_rows
                    if (
                        value := _observation_provenance_ref(
                            item.get("lane_id")
                        )
                    ) is not None
                ]
                premises_verified = all(
                    item.get("premise_verified") is True
                    for item in lane_rows
                )
                explicit_not_found = all(
                    isinstance(item.get("not_found"), list)
                    for item in lane_rows
                )
                established = (
                    len(lane_rows) >= 2
                    and len(lane_ids) == len(lane_rows)
                    and len(authorities) == len(lane_rows)
                    and len(set(authorities)) == len(authorities)
                    and premises_verified
                    and explicit_not_found
                )
                if established:
                    set_row(
                        "TH-OBS-9",
                        "PASS",
                        lane_count=len(lane_rows),
                        independent_authority_count=len(set(authorities)),
                        explicit_not_found=True,
                    )
                else:
                    set_row(
                        "TH-OBS-9",
                        "TRIGGERED",
                        hold_scope="parallel_recon_acceptance",
                        lane_count=len(lane_rows),
                        premises_independently_verified=premises_verified,
                        explicit_not_found=explicit_not_found,
                        safe_reading_allowed=True,
                    )
            else:
                set_row("TH-OBS-9", "NOT_TRIGGERED")

        compromise = observation.get("constraint_compromise")
        if isinstance(compromise, dict):
            incompatible = compromise.get("incompatible")
            selected = compromise.get("compromise_selected")
            loss = _observation_ref(compromise.get("irreducible_loss_statement"), 1000)
            if not isinstance(incompatible, bool) or (
                incompatible and not isinstance(selected, bool)
            ):
                set_row("TH-OBS-10", "UNKNOWN", reason="constraint_or_compromise_state_missing")
            elif incompatible and selected and loss is None:
                set_row(
                    "TH-OBS-10",
                    "TRIGGERED",
                    hold_scope="compromise_selection",
                    reason="irreducible_loss_statement_missing",
                )
            elif incompatible and loss is not None:
                set_row("TH-OBS-10", "PASS", irreducible_loss_statement=loss)
            else:
                set_row("TH-OBS-10", "NOT_TRIGGERED")

    selected_action = None
    selected_action_source = None
    if action_candidates:
        _, selected_action_source, selected_action = min(action_candidates)
    return {
        "schema_version": "pms_hermes_observability_threshold_evaluation.v1",
        "input_status": "present" if observation is not None else "absent",
        "thresholds": [rows[threshold_id] for threshold_id in OBSERVABILITY_THRESHOLD_IDS],
        "held_operations": sorted(set(held_operations)),
        "retained_disjoint_continuations": sorted(set(disjoint_continuations)),
        "selected_next_action": selected_action,
        "selected_next_action_source": selected_action_source,
        "work_continuation_allowed": True,
        "ordinary_work_classes_allowed": [
            "bounded_repair",
            "correct_worker_continuation",
            "implementation",
            "read_only_investigation",
        ],
        "user_capability_delta": 0,
        "support_work_progress_credit": 0,
        "observed_effective": False,
        "citation_or_fitness_is_promotion_evidence": False,
    }


def select_live_material_continuation(
    material_result: Any,
    stop_event: Any = None,
) -> dict[str, Any]:
    """Select one deterministic continuation from a sanitized material event.

    This is a pure Claim Check consumer. It never calls ODG, a model, a
    provider, or an external writer, and it never blocks ordinary work.
    """
    source_event = _live_transition_source_event(material_result)
    goal = source_event.get("goal")
    goal = goal if isinstance(goal, dict) else {}
    state = source_event.get("state")
    state = state if isinstance(state, dict) else {}
    blockers = source_event.get("blockers")
    if not isinstance(blockers, list):
        blockers = source_event.get("blocker")
    blockers = blockers if isinstance(blockers, list) else []

    goal_id = _live_transition_text(goal.get("id"), 160)
    goal_summary = _live_transition_text(goal.get("summary"), 1000)
    current_phase = _live_transition_text(state.get("current"), 160)
    next_action = _live_transition_text(source_event.get("next_action"), 1000)
    correction = _live_transition_text(source_event.get("correction"), 1000)
    incident_signals = [
        value
        for value in (
            correction,
            *(
                _live_transition_text(item, 500)
                for item in blockers[:20]
            ),
        )
        if value is not None
    ]

    claim_holds: list[str] = []
    protected_operation: str | None = None
    containers = _live_transition_claim_containers(source_event, stop_event)
    if source_event.get("decision") == "DONT_NOTIFY" or any(
        container.get("DONT_NOTIFY") is True
        or container.get("dont_notify") is True
        or container.get("decision") == "DONT_NOTIFY"
        for container in containers
    ):
        claim_holds.append("DONT_NOTIFY")
    if any(container.get("phase_advanced") is True for container in containers):
        claim_holds.append("phase_advanced")
    if any(
        container.get("observed_effective") is True
        or container.get("observed_effective_claimed") is True
        for container in containers
    ):
        claim_holds.append("observed_effective")
    for container in containers:
        candidate = container.get("protected_operation_claim")
        if isinstance(candidate, dict) and candidate.get("claimed") is True:
            candidate = candidate.get("name")
        named = _live_transition_text(candidate, 160)
        if named:
            protected_operation = named
            claim_holds.append(f"protected_operation:{named}")
            break

    threshold_evaluation = _evaluate_observability_thresholds(
        material_result,
        source_event,
        stop_event,
    )
    scoped_operation_holds = threshold_evaluation["held_operations"]
    effective_next_action = (
        threshold_evaluation.get("selected_next_action") or next_action
    )

    normalized_action = effective_next_action.lower() if effective_next_action else ""
    cause_changing = bool(effective_next_action) and not any(
        normalized_action.startswith(prefix)
        for prefix in LIVE_TRANSITION_SUPPORT_ONLY_PREFIXES
    )
    decision = (
        "accountability_pending"
        if claim_holds or not cause_changing
        else "next_cause_changing_action"
    )
    result: dict[str, Any] = {
        "schema_version": LIVE_TRANSITION_DECISION_VERSION,
        "decision": decision,
        "grand_goal": {
            "id": goal_id,
            "summary": goal_summary,
        },
        "current_phase": current_phase,
        "user_capability_delta": {
            "classification": (
                "cause_changing_candidate"
                if cause_changing
                else "not_established"
            ),
            "source": (
                "observability_thresholds.selected_next_action"
                if threshold_evaluation.get("selected_next_action")
                else "material_event.next_action"
            ),
        },
        "incident_recurrence": {
            "classification": (
                "source_reported" if incident_signals else "not_reported"
            ),
            "signals": incident_signals,
        },
        "unsupported_claims": claim_holds,
        "protected_operation": protected_operation,
        "observability_thresholds": threshold_evaluation,
        "quiet_claims_available": (
            decision == "next_cause_changing_action"
            and not scoped_operation_holds
        ),
        "work_continuation_allowed": True,
        "ordinary_work_classes_allowed": [
            "bounded_repair",
            "correct_worker_continuation",
            "implementation",
            "read_only_investigation",
        ],
        "odg_effect": "advisory_fail_open_no_authority_progress_or_gate",
    }
    if decision == "next_cause_changing_action":
        result[decision] = {
            "action": effective_next_action,
            "owner": _live_transition_text(source_event.get("owner"), 160),
        }
    else:
        result[decision] = {
            "reason": (
                "unsupported_quiet_or_protected_claim"
                if claim_holds
                else "material_source_has_no_cause_changing_action"
            ),
            "source_next_action": effective_next_action,
        }
    return result


def _is_planning_phase(phase_ref: Any) -> bool:
    if not isinstance(phase_ref, str):
        return False
    normalized = phase_ref.strip().lower().replace("_", "-")
    return normalized in PLANNING_PHASE_REFS


def _check_planning_order(
    value: dict[str, Any],
    action: dict[str, Any],
    closeout: dict[str, Any],
) -> list[str]:
    """Reject evidence-first and topology-fixed planning before it reaches CMD.

    This is an ordering Claim Check. It is intentionally scoped to explicit
    planning phases; ordinary supervised or read-only work remains receipt-free.
    """
    planning = value.get("planning_order")
    if not isinstance(planning, dict) or set(planning) != PLANNING_ORDER_FIELDS:
        return ["BLOCKED_FOR_INC178_PLANNING_ORDER_CONTROL_REQUIRED"]

    blocks: list[str] = []
    diagnosis = planning.get("diagnosis")
    if (
        not isinstance(diagnosis, dict)
        or set(diagnosis) != PLANNING_DIAGNOSIS_FIELDS
        or diagnosis.get("state") not in {"pending", "consumed", "not_required"}
        or not isinstance(diagnosis.get("run_count"), int)
        or isinstance(diagnosis.get("run_count"), bool)
        or diagnosis.get("run_count") < 0
        or diagnosis.get("run_count") > 1
        or (
            diagnosis.get("state") in {"pending", "not_required"}
            and diagnosis.get("run_count") != 0
        )
        or (
            diagnosis.get("state") == "consumed"
            and diagnosis.get("run_count") != 1
        )
    ):
        return ["BLOCKED_FOR_INC178_PLANNING_ORDER_SCHEMA_INVALID"]
    if diagnosis.get("state") == "consumed":
        return []

    if planning.get("contract_version") != PLANNING_ORDER_VERSION:
        blocks.append("BLOCKED_FOR_INC178_PLANNING_ORDER_SCHEMA_INVALID")
    if planning.get("current_state") not in {"planning_open", "plan_locked"}:
        blocks.append("BLOCKED_FOR_INC178_PLANNING_ORDER_SCHEMA_INVALID")
    if planning.get("mode_transition") != "ultra_planning_then_high_cmd":
        blocks.append("BLOCKED_FOR_INC178_PLANNING_ORDER_MODE_INVERSION")
    if (
        _empty(planning.get("primary_work_id"))
        or planning.get("primary_work_class") != "primary_capability_planning"
        or _empty(planning.get("primary_path_action_id"))
        or not isinstance(planning.get("critical_path"), list)
        or planning.get("critical_path") != [
            "architectural_analysis",
            "implementation_plan_checklist",
            "agent_topology_optimization",
            "execution_instructions",
        ]
    ):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_ORDER_SCHEMA_INVALID")
    if planning.get("primary_path_action_id") != action.get("selected_action_id"):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_PRIMARY_PATH_NOT_SELECTED")
    if action.get("selected_action_class") == "evidence_only":
        blocks.append("BLOCKED_FOR_INC178_PLANNING_EVIDENCE_FIRST")

    support_items = planning.get("support_work_items")
    if not isinstance(support_items, list) or any(
        not isinstance(row, dict)
        or set(row) != PLANNING_SUPPORT_ITEM_FIELDS
        or _empty(row.get("work_id"))
        or _empty(row.get("work_class"))
        or not isinstance(row.get("on_critical_path"), bool)
        or not isinstance(row.get("may_block_primary"), bool)
        for row in support_items or []
    ):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_ORDER_SCHEMA_INVALID")
    elif any(
        row.get("work_class") in {"evidence", "receipt", "hash", "inventory", "audit", "support"}
        and (row.get("on_critical_path") is True or row.get("may_block_primary") is True)
        for row in support_items
    ):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_SUPPORT_WORK_ON_CRITICAL_PATH")

    topology = planning.get("topology")
    if (
        not isinstance(topology, dict)
        or set(topology) != PLANNING_TOPOLOGY_FIELDS
        or topology.get("architect_role") != "named_architect"
        or topology.get("optimization_owner") != "named_architect"
        or topology.get("pre_fixed") is not False
        or topology.get("optimization_required_before_execution") is not True
    ):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_TOPOLOGY_PRE_FIXED")

    gate_policy = planning.get("gate_policy")
    if (
        not isinstance(gate_policy, dict)
        or set(gate_policy) != PLANNING_GATE_POLICY_FIELDS
        or gate_policy.get("read_only_planning_allowed") is not True
        or gate_policy.get("execution_grade_gates_deferred") is not True
        or gate_policy.get("authority_gate_required_before_read_only_planning") is not False
    ):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_EXECUTION_GATE_BLOCKS_READ_ONLY")

    terminal_policy = planning.get("terminal_policy")
    if (
        not isinstance(terminal_policy, dict)
        or set(terminal_policy) != PLANNING_TERMINAL_POLICY_FIELDS
        or terminal_policy.get("plan_lock_required_before_execution") is not True
        or terminal_policy.get("execution_requires_plan_lock") is not True
        or terminal_policy.get("control_or_evidence_only_can_close") is not False
    ):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_TERMINAL_POLICY_INVALID")
    if (
        planning.get("current_state") != "plan_locked"
        and closeout.get("status") == "closed"
    ):
        blocks.append("BLOCKED_FOR_INC178_PLANNING_TERMINAL_CLOSEOUT_BEFORE_PLAN_LOCK")
    return sorted(set(blocks))


def planning_order_selection(value: Any, blocks: list[str] | None = None) -> dict[str, Any]:
    """Return the planner/CMD action selected by the planning-order Claim Check."""
    binding = value.get("decision_binding", {}) if isinstance(value, dict) else {}
    phase_ref = binding.get("phase_ref") if isinstance(binding, dict) else None
    if not _is_planning_phase(phase_ref):
        return {
            "fired": False,
            "decision": "NOT_APPLICABLE_NON_PLANNING_PHASE",
            "gate_class": "Claim Check",
            "authority_gate": False,
        }
    planning = value.get("planning_order", {}) if isinstance(value, dict) else {}
    diagnosis = planning.get("diagnosis", {}) if isinstance(planning, dict) else {}
    diagnosis_state = diagnosis.get("state") if isinstance(diagnosis, dict) else None
    diagnosis_run_count = diagnosis.get("run_count") if isinstance(diagnosis, dict) else None
    if diagnosis_state == "consumed" and diagnosis_run_count == 1:
        return {
            "fired": False,
            "trigger_point": "before_primary_planning_dispatch",
            "requires_user_correction": False,
            "decision": "SELF_DEMOTED_AFTER_BOUNDED_DIAGNOSIS",
            "selected_primary_action_id": planning.get("primary_path_action_id"),
            "planning_blocks": [],
            "read_only_planning_continues": True,
            "bounded_diagnosis_max_runs": 1,
            "diagnosis_transition": {
                "transition_applied": False,
                "before": {"state": "consumed", "run_count": 1},
                "after": {"state": "consumed", "run_count": 1},
            },
            "gate_class": "Claim Check",
            "authority_gate": False,
        }
    planning_blocks = sorted({
        block for block in (blocks or [])
        if block.startswith("BLOCKED_FOR_INC178_PLANNING_")
    })
    diagnosis_transition = {
        "transition_applied": bool(planning_blocks),
        "before": {
            "state": diagnosis_state,
            "run_count": diagnosis_run_count,
        },
        "after": (
            {"state": "consumed", "run_count": 1}
            if planning_blocks
            else {
                "state": diagnosis_state,
                "run_count": diagnosis_run_count,
            }
        ),
    }
    return {
        "fired": True,
        "trigger_point": "before_primary_planning_dispatch",
        "requires_user_correction": False,
        "decision": (
            "REORDER_PRIMARY_PLANNING_FIRST"
            if planning_blocks
            else "PROCEED_PRIMARY_PLANNING"
        ),
        "selected_primary_action_id": planning.get("primary_path_action_id"),
        "planning_blocks": planning_blocks,
        "support_work_demoted_from_critical_path": bool(planning_blocks),
        "topology_optimization_returned_to_named_architect": bool(planning_blocks),
        "terminal_closeout_withheld_until_plan_lock": True,
        "read_only_planning_continues": True,
        "bounded_diagnosis_max_runs": 1,
        "diagnosis_transition": diagnosis_transition,
        "next_replay_will_fire": not bool(planning_blocks),
        "gate_class": "Claim Check",
        "authority_gate": False,
    }


def check_contract(
    value: Any,
    base_dir: Path | None = None,
    *,
    session_mode: str | None = None,
) -> list[str]:
    blocks: list[str] = []
    if not isinstance(value, dict):
        return ["BLOCKED_FOR_INC178_WHOLE_GOAL_CONTRACT_MISSING"]
    if value.get("contract_version") != CONTRACT_VERSION:
        blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
    work_class = value.get("work_class")
    if work_class in EXEMPT_WORK_CLASSES:
        if set(value) != {"contract_version", "work_class", "exempt_reason"} or _empty(value.get("exempt_reason")):
            blocks.append("BLOCKED_FOR_INC178_NORMAL_SUPERVISED_SCOPE_OVERGATED")
        return sorted(set(blocks))
    if work_class not in PACED_WORK_CLASSES:
        return sorted(set(blocks + ["BLOCKED_FOR_INC178_WHOLE_GOAL_SCOPE_INVALID"]))

    required = {
        "contract_version", "work_class", "transition", "decision_binding", "whole_goal",
        "progress_deltas", "thresholds",
        "time_accounting", "counters", "skill_firing", "action_selection",
        "replan", "heartbeat_self_health", "audit_integration", "gate_burden",
        "closeout", "fable5_dependency_banned", "external_dependencies", "non_claims",
        "long_lived_heartbeat", "fresh_session_recheck", "terminal_continuation",
    }
    if not required <= set(value) <= required | {"planning_order", "cmd_epoch_control", "cmd_epoch_request"} or value.get("transition") not in TRANSITIONS:
        return sorted(set(blocks + ["BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID"]))

    if "cmd_epoch_control" in value and not validate_cmd_epoch_control(value.get("cmd_epoch_control")):
        blocks.append(CMD_EPOCH_SCHEMA_INVALID)
    if "cmd_epoch_request" in value and not validate_cmd_epoch_request(value.get("cmd_epoch_request")):
        blocks.append(CMD_EPOCH_SCHEMA_INVALID)

    whole = value.get("whole_goal")
    whole_fields = {
        "goal_ref", "started_at", "observed_at", "whole_goal_elapsed_ms",
        "active_elapsed_estimate_ms", "active_elapsed_source", "estimate_range_ms",
        "expected_completion_max_ms", "estimate_error_ratio",
        "phase_estimate_status", "whole_goal_estimate_status",
        "local_blocker_delta", "current_biggest_blocker",
    }
    if not isinstance(whole, dict) or set(whole) != whole_fields:
        blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        whole = {}
    else:
        started = _parse_time(whole.get("started_at"))
        observed = _parse_time(whole.get("observed_at"))
        elapsed = whole.get("whole_goal_elapsed_ms")
        estimate = whole.get("active_elapsed_estimate_ms")
        estimate_range = whole.get("estimate_range_ms")
        expected_max = whole.get("expected_completion_max_ms")
        ratio = whole.get("estimate_error_ratio")
        if (
            _empty(whole.get("goal_ref"))
            or started is None or observed is None or observed < started
            or not _nonnegative_int(elapsed) or not _nonnegative_int(estimate)
            or whole.get("active_elapsed_source") not in {"measured", "bounded_estimate", "user_corrected_estimate"}
            or not isinstance(estimate_range, dict) or set(estimate_range) != {"min_ms", "max_ms"}
            or not _nonnegative_int(estimate_range.get("min_ms")) or not _nonnegative_int(estimate_range.get("max_ms"))
            or estimate_range.get("min_ms", 1) > estimate_range.get("max_ms", 0)
            or not _positive_int(expected_max) or not _number(ratio) or ratio < 0
            or whole.get("phase_estimate_status") not in {"green", "overdue", "unknown"}
            or whole.get("whole_goal_estimate_status") not in {"green", "overdue", "unknown"}
            or whole.get("local_blocker_delta") not in {"positive", "zero", "unknown"}
            or _empty(whole.get("current_biggest_blocker"))
        ):
            blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        if started and observed and _nonnegative_int(elapsed):
            measured = int((observed - started).total_seconds() * 1000)
            if abs(measured - elapsed) > 1000:
                blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_TIME_BINDING_INVALID")
        if _nonnegative_int(estimate) and isinstance(estimate_range, dict):
            if not estimate_range.get("min_ms", 0) <= estimate <= estimate_range.get("max_ms", -1):
                blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_TIME_BINDING_INVALID")
        if _nonnegative_int(elapsed) and _positive_int(expected_max) and _number(ratio):
            if abs(ratio - (elapsed / expected_max)) > 0.01:
                blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_TIME_BINDING_INVALID")

    action = value.get("action_selection") if isinstance(value.get("action_selection"), dict) else {}
    binding = value.get("decision_binding")
    binding_fields = {
        "goal_ref", "phase_ref", "head_ref", "blocker_fingerprint", "selected_action_id",
        "evaluated_at", "source_ref", "live_state_matches",
    }
    if (
        not isinstance(binding, dict) or set(binding) != binding_fields
        or any(_empty(binding.get(field)) for field in binding_fields - {"live_state_matches"})
        or _parse_time(binding.get("evaluated_at")) is None
        or binding.get("live_state_matches") is not True
        or binding.get("goal_ref") != whole.get("goal_ref")
        or binding.get("selected_action_id") != action.get("selected_action_id")
    ):
        blocks.append("BLOCKED_FOR_INC178_STALE_DECISION_REFIRE_REQUIRED")

    progress = value.get("progress_deltas")
    progress_fields = {"blocker_knowledge_delta", "runtime_milestone_delta", "user_visible_capability_delta"}
    if not isinstance(progress, dict) or set(progress) != progress_fields:
        blocks.append("BLOCKED_FOR_INC178_PROGRESS_DELTA_CLASSIFICATION_INVALID")
        progress = {}
    else:
        for field in progress_fields:
            row = progress.get(field)
            required_delta_fields = {"classification", "summary"}
            if field == "user_visible_capability_delta":
                required_delta_fields.add("normal_user_operation_observed")
            if (
                not isinstance(row, dict) or set(row) != required_delta_fields
                or row.get("classification") not in {"positive", "zero", "unknown"}
                or _empty(row.get("summary"))
                or (field == "user_visible_capability_delta" and not isinstance(row.get("normal_user_operation_observed"), bool))
            ):
                blocks.append("BLOCKED_FOR_INC178_PROGRESS_DELTA_CLASSIFICATION_INVALID")
            if (
                field == "user_visible_capability_delta"
                and isinstance(row, dict)
                and row.get("classification") == "positive"
                and row.get("normal_user_operation_observed") is not True
            ):
                blocks.append("BLOCKED_FOR_INC178_UNOBSERVED_USER_VISIBLE_DELTA")

    thresholds = value.get("thresholds")
    threshold_fields = {
        "elapsed_zero_delta_ms", "max_chained_implementation_blocks", "zero_delta_slice_limit",
        "distinct_causal_blocker_limit", "protected_mutation_without_milestone_limit",
        "estimate_error_ratio_limit", "same_warning_replan_count", "return_decide_dispatch_checkpoint_ms",
    }
    if (
        not isinstance(thresholds, dict) or set(thresholds) != threshold_fields
        or not _positive_int(thresholds.get("elapsed_zero_delta_ms"))
        or thresholds.get("elapsed_zero_delta_ms") != 3_600_000
        or thresholds.get("max_chained_implementation_blocks") != 3
        or thresholds.get("zero_delta_slice_limit") != 3
        or thresholds.get("distinct_causal_blocker_limit") != 3
        or thresholds.get("protected_mutation_without_milestone_limit") != 2
        or thresholds.get("estimate_error_ratio_limit") != 2.0
        or thresholds.get("same_warning_replan_count") != 2
        or thresholds.get("return_decide_dispatch_checkpoint_ms") != 180_000
    ):
        blocks.append("BLOCKED_FOR_INC178_REPLAN_THRESHOLD_INVALID")

    time_accounting = value.get("time_accounting")
    time_fields = {"support_work_elapsed_ms", "support_work_ratio", "authority_gate_wait_ms", "claim_check_support_ms"}
    if not isinstance(time_accounting, dict) or set(time_accounting) != time_fields or any(
        not _nonnegative_int(time_accounting.get(field))
        for field in ("support_work_elapsed_ms", "authority_gate_wait_ms", "claim_check_support_ms")
    ) or not _number(time_accounting.get("support_work_ratio")) or not 0 <= time_accounting.get("support_work_ratio", -1) <= 1:
        blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
    elif whole:
        elapsed = whole.get("whole_goal_elapsed_ms", 0)
        if elapsed > 0 and abs(time_accounting["support_work_ratio"] - time_accounting["support_work_elapsed_ms"] / elapsed) > 0.01:
            blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_TIME_BINDING_INVALID")

    counters = value.get("counters")
    counter_fields = {
        "chained_implementation_blocks", "consecutive_zero_visible_delta_slices",
        "distinct_causal_blocker_count", "protected_mutation_or_pair_count",
        "evidence_only_slice_count", "user_correction_count", "user_warning_events",
        "warning_category_counts_overlap", "warning_count_source",
        "repeated_warning_classes", "user_relay_count", "idle_after_partial",
        "return_decide_dispatch_elapsed_ms", "false_block_count", "missed_block_count",
        "avoidable_model_cost_count", "product_decision_changed_count",
    }
    warning_counts: dict[str, int] | None = None
    if not isinstance(counters, dict) or set(counters) != counter_fields:
        blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        counters = {}
    else:
        for field in (
            "chained_implementation_blocks", "consecutive_zero_visible_delta_slices",
            "distinct_causal_blocker_count", "protected_mutation_or_pair_count",
            "evidence_only_slice_count", "user_correction_count", "user_relay_count",
            "return_decide_dispatch_elapsed_ms", "false_block_count", "missed_block_count",
            "avoidable_model_cost_count", "product_decision_changed_count",
        ):
            if not _nonnegative_int(counters.get(field)):
                blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        if not isinstance(counters.get("idle_after_partial"), bool):
            blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        if counters.get("warning_category_counts_overlap") is not True or _empty(counters.get("warning_count_source")):
            blocks.append("BLOCKED_FOR_INC178_USER_WARNING_THRESHOLD_NOT_COUNTED")
        warning_counts = _warning_counts(counters.get("user_warning_events"))
        repeated_rows = counters.get("repeated_warning_classes")
        if warning_counts is None or not isinstance(repeated_rows, list) or any(
            not isinstance(row, dict) or set(row) != {"warning_class", "count"}
            or _empty(row.get("warning_class")) or not _positive_int(row.get("count"))
            for row in repeated_rows
        ):
            blocks.append("BLOCKED_FOR_INC178_USER_WARNING_THRESHOLD_NOT_COUNTED")
        else:
            repeated_expected = {key: count for key, count in warning_counts.items() if count >= 2}
            repeated_actual = {row["warning_class"]: row["count"] for row in repeated_rows}
            if counters.get("user_correction_count", 0) < max(repeated_expected.values(), default=0) or repeated_actual != repeated_expected:
                blocks.append("BLOCKED_FOR_INC178_USER_WARNING_THRESHOLD_NOT_COUNTED")

    skills = value.get("skill_firing")
    skill_fields = {
        "expected_skills", "fired_skills", "non_fires", "invocation_records",
        "skill_ecosystem_repair_required", "skill_surface_state",
    }
    if not isinstance(skills, dict) or set(skills) != skill_fields:
        blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        skills = {}
    else:
        expected = skills.get("expected_skills")
        fired = skills.get("fired_skills")
        nonfires = skills.get("non_fires")
        invocations = skills.get("invocation_records")
        surface_state = skills.get("skill_surface_state")
        if (
            not isinstance(expected, list) or not expected or any(_empty(item) for item in expected)
            or not isinstance(fired, list) or any(_empty(item) for item in fired)
            or not isinstance(nonfires, list) or any(
                not isinstance(row, dict) or set(row) != {"skill", "reason"}
                or _empty(row.get("skill")) or row.get("reason") not in NONFIRE_REASONS for row in nonfires
            )
            or not isinstance(invocations, list) or any(
                not isinstance(row, dict) or set(row) != {
                    "skill", "surface", "result", "integrated_into_action_selection",
                    "source_thread_id", "result_ref", "result_digest", "observed_at",
                }
                or _empty(row.get("skill")) or _empty(row.get("surface")) or _empty(row.get("result"))
                or _empty(row.get("source_thread_id")) or _empty(row.get("result_ref"))
                or not _sha256(row.get("result_digest")) or _parse_time(row.get("observed_at")) is None
                or row.get("integrated_into_action_selection") is not True for row in invocations
            )
            or not isinstance(skills.get("skill_ecosystem_repair_required"), bool)
            or not isinstance(surface_state, dict)
            or set(surface_state) != SKILL_SURFACE_STATE_FIELDS
            or any(_empty(surface_state.get(field)) for field in (
                "canonical_source_state", "plugin_distribution_state", "plugin_cache_diagnostic_state",
                "unprefixed_skill_root_state", "active_resolution_root_state",
            ))
            or surface_state.get("presence_is_invocation_evidence") is not False
            or surface_state.get("invocation_is_result_consumption") is not False
        ):
            blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        accounted = set(fired or []) | {row.get("skill") for row in nonfires or [] if isinstance(row, dict)}
        if set(expected or []) - accounted:
            blocks.append("BLOCKED_FOR_INC178_EXPECTED_SKILL_NONFIRE_UNACCOUNTED")
        invocation_skills = [row.get("skill") for row in invocations or [] if isinstance(row, dict)]
        if set(invocation_skills) != set(fired or []) or len(invocation_skills) != len(set(invocation_skills)):
            blocks.append("BLOCKED_FOR_INC178_SKILL_INVOCATION_PROVENANCE_MISSING")
        if base_dir is not None and any(
            not _repo_ref_digest_matches(base_dir, row.get("result_ref"), row.get("result_digest"))
            for row in invocations or [] if isinstance(row, dict)
        ):
            blocks.append("BLOCKED_FOR_INC178_SKILL_INVOCATION_PROVENANCE_MISSING")
        if nonfires and skills.get("skill_ecosystem_repair_required") is not True:
            blocks.append("BLOCKED_FOR_INC178_EXPECTED_SKILL_NONFIRE_UNACCOUNTED")

    action = value.get("action_selection")
    action_fields = {
        "candidate_actions", "selected_action_id", "selected_action_class", "rejected_actions",
        "quantified_best_action_rationale", "support_work_progress_credit", "next_action",
        "audit_pass_selected_as_sufficient", "product_path_simplified_or_unnecessary_gate_removed",
        "cause_changing_repair", "classified_as_unchanged_retry", "dependency_map_reviewed",
        "pin_or_provenance_only_fast_path_eligible", "fast_path_used",
    }
    if not isinstance(action, dict) or set(action) != action_fields:
        blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        action = {}
    else:
        candidates = action.get("candidate_actions")
        rejected = action.get("rejected_actions")
        rationale = action.get("quantified_best_action_rationale")
        candidate_fields = {
            "action_id", "operation_type", "action_class", "user_capability_delta_score",
            "blocker_delta_score", "estimated_cost_ms", "gate_burden_ms",
        }
        if not isinstance(candidates, list) or len(candidates) < 3 or any(
            not isinstance(row, dict) or set(row) != candidate_fields
            or _empty(row.get("action_id")) or row.get("action_class") not in ACTION_CLASSES
            or not isinstance(row.get("user_capability_delta_score"), int)
            or not isinstance(row.get("blocker_delta_score"), int)
            or not _nonnegative_int(row.get("estimated_cost_ms")) or not _nonnegative_int(row.get("gate_burden_ms"))
            for row in candidates
        ):
            blocks.append("BLOCKED_FOR_INC178_BEST_ACTION_RATIONALE_INVALID")
            candidates = []
        elif len({row.get("action_id") for row in candidates}) != len(candidates):
            blocks.append("BLOCKED_FOR_INC178_BEST_ACTION_RATIONALE_INVALID")
            candidates = []
        for row in candidates:
            if _derived_action_class(row) != row.get("action_class"):
                blocks.append("BLOCKED_FOR_INC178_ACTION_CLASS_DERIVATION_INVALID")
        selected = next((row for row in candidates if row.get("action_id") == action.get("selected_action_id")), None)
        if not selected or action.get("selected_action_class") != selected.get("action_class") or _empty(action.get("next_action")):
            blocks.append("BLOCKED_FOR_INC178_BEST_ACTION_RATIONALE_INVALID")
        if not isinstance(rejected, list) or not rejected or any(
            not isinstance(row, dict) or set(row) != {"action_id", "reason"}
            or _empty(row.get("action_id")) or _empty(row.get("reason")) for row in rejected
        ):
            blocks.append("BLOCKED_FOR_INC178_BEST_ACTION_RATIONALE_INVALID")
        elif candidates:
            rejected_ids = {row.get("action_id") for row in rejected}
            expected_rejected_ids = {
                row.get("action_id") for row in candidates
                if row.get("action_id") != action.get("selected_action_id")
            }
            if rejected_ids != expected_rejected_ids:
                blocks.append("BLOCKED_FOR_INC178_BEST_ACTION_RATIONALE_INVALID")
        if not isinstance(rationale, dict) or set(rationale) != {"selected_score", "next_best_score", "user_value_weight", "cost_weight"} or any(
            not _number(rationale.get(field)) for field in rationale or {}
        ):
            blocks.append("BLOCKED_FOR_INC178_BEST_ACTION_RATIONALE_INVALID")
        elif candidates:
            def action_score(row: dict[str, Any]) -> float:
                return round(
                    row["user_capability_delta_score"] * rationale["user_value_weight"]
                    + row["blocker_delta_score"]
                    - ((row["estimated_cost_ms"] + row["gate_burden_ms"]) / 3_600_000)
                    * rationale["cost_weight"],
                    6,
                )

            scores = {row["action_id"]: action_score(row) for row in candidates}
            ranked = sorted(scores.values(), reverse=True)
            selected_score = scores.get(action.get("selected_action_id"))
            next_best = ranked[1] if len(ranked) > 1 else ranked[0]
            if (
                selected_score is None
                or selected_score != ranked[0]
                or abs(rationale.get("selected_score", float("inf")) - selected_score) > 0.000001
                or abs(rationale.get("next_best_score", float("inf")) - next_best) > 0.000001
            ):
                blocks.append("BLOCKED_FOR_INC178_BEST_ACTION_RATIONALE_INVALID")
        if action.get("support_work_progress_credit") != 0:
            blocks.append("BLOCKED_FOR_INC178_SUPPORT_WORK_PROGRESS_CREDIT_INVALID")
        if action.get("selected_action_class") == "evidence_only":
            blocks.append("BLOCKED_FOR_INC178_SUPPORT_WORK_SELECTED_OVER_USER_VALUE")
        if action.get("audit_pass_selected_as_sufficient") is not False:
            blocks.append("BLOCKED_FOR_INC178_SCOPED_AUDIT_PASS_SELECTED_AS_NEXT_ACTION")
        if any(not isinstance(action.get(field), bool) for field in (
            "product_path_simplified_or_unnecessary_gate_removed", "cause_changing_repair",
            "classified_as_unchanged_retry", "dependency_map_reviewed",
            "pin_or_provenance_only_fast_path_eligible", "fast_path_used",
        )):
            blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
        if action.get("cause_changing_repair") and action.get("classified_as_unchanged_retry"):
            blocks.append("BLOCKED_FOR_INC178_CAUSE_CHANGING_REPAIR_MISCLASSIFIED")
        if action.get("pin_or_provenance_only_fast_path_eligible") and not action.get("fast_path_used"):
            blocks.append("BLOCKED_FOR_INC178_FAST_PATH_NOT_USED")
        if counters.get("protected_mutation_or_pair_count", 0) >= 2 and (progress.get("runtime_milestone_delta") or {}).get("classification") == "zero" and not action.get("dependency_map_reviewed"):
            blocks.append("BLOCKED_FOR_INC178_DEPENDENCY_MAP_REVIEW_REQUIRED_BEFORE_PROTECTED_MUTATION")

    phase_ref = (value.get("decision_binding") or {}).get("phase_ref") if isinstance(value.get("decision_binding"), dict) else None
    if _is_planning_phase(phase_ref):
        blocks.extend(_check_planning_order(value, action, value.get("closeout") or {}))

    reasons = _threshold_reasons(value)
    replan = value.get("replan")
    replan_fields = {
        "required", "trigger_reasons", "decision", "action_changed", "selected_action_id",
        "exact_blocker", "next_protected_mutation_paused", "local_read_only_cause_repair_allowed",
        "read_only_and_supervised_local_work_allowed",
    }
    if not isinstance(replan, dict) or set(replan) != replan_fields:
        blocks.append("BLOCKED_FOR_INC178_REPLAN_TRIGGER_MISSING")
        replan = {}
    elif reasons:
        if (
            replan.get("required") is not True
            or not reasons <= set(replan.get("trigger_reasons", []) or [])
            or replan.get("decision") not in REPLAN_DECISIONS
            or replan.get("action_changed") is not True
            or replan.get("selected_action_id") != action.get("selected_action_id")
            or replan.get("next_protected_mutation_paused") is not True
            or replan.get("local_read_only_cause_repair_allowed") is not True
            or replan.get("read_only_and_supervised_local_work_allowed") is not True
        ):
            blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_ZERO_DELTA_CONTINUATION")
        if action.get("selected_action_class") == "exact_authority_blocker" and _empty(replan.get("exact_blocker")):
            blocks.append("BLOCKED_FOR_INC178_REPLAN_TRIGGER_MISSING")
    elif replan.get("required") is not False:
        blocks.append("BLOCKED_FOR_INC178_REPLAN_TRIGGER_MISSING")

    heartbeat = value.get("heartbeat_self_health")
    heartbeat_fields = {
        "automation_status", "local_activity_present", "whole_goal_stagnation_evaluated",
        "activity_class", "decision", "prompt_updated_manually", "prompt_coverage", "prompt_coverage_validator_ref",
    }
    coverage_fields = {
        "current_goal", "latest_incident", "terminal_marker", "whole_goal_cost",
        "zero_delta_streak", "correction_count", "required_skill_firings",
        "current_blocker", "audit_method", "stale",
    }
    if not isinstance(heartbeat, dict) or set(heartbeat) != heartbeat_fields:
        blocks.append("BLOCKED_FOR_INC178_AUTOMATION_PROMPT_COVERAGE_UNVALIDATED")
        heartbeat = {}
    else:
        coverage = heartbeat.get("prompt_coverage")
        if (
            heartbeat.get("automation_status") not in {"active", "inactive", "not_applicable"}
            or not isinstance(heartbeat.get("local_activity_present"), bool)
            or not isinstance(heartbeat.get("whole_goal_stagnation_evaluated"), bool)
            or heartbeat.get("activity_class") not in {"product", "cause_repair", "support", "audit", "ci", "idle"}
            or heartbeat.get("decision") not in {"DONT_NOTIFY", "NOTIFY_REPLAN_REQUIRED", "CONTINUE_CHANGED_ACTION"}
            or not isinstance(heartbeat.get("prompt_updated_manually"), bool)
            or not isinstance(coverage, dict) or set(coverage) != coverage_fields
            or any(coverage.get(field) is not True for field in coverage_fields - {"stale"})
            or coverage.get("stale") is not False
            or _empty(heartbeat.get("prompt_coverage_validator_ref"))
        ):
            blocks.append("BLOCKED_FOR_INC178_AUTOMATION_PROMPT_COVERAGE_UNVALIDATED")
        if reasons and heartbeat.get("decision") == "DONT_NOTIFY":
            blocks.append("BLOCKED_FOR_INC178_HEARTBEAT_ACTIVITY_SUBSTITUTED_FOR_GOAL_HEALTH")
        if reasons and heartbeat.get("whole_goal_stagnation_evaluated") is not True:
            blocks.append("BLOCKED_FOR_INC178_HEARTBEAT_ACTIVITY_SUBSTITUTED_FOR_GOAL_HEALTH")

    if whole and whole.get("phase_estimate_status") == "green" and whole.get("whole_goal_estimate_status") in {"overdue", "unknown"}:
        if "estimate_overrun_without_visible_delta" not in set(replan.get("trigger_reasons", []) or []):
            blocks.append("BLOCKED_FOR_INC178_PHASE_ETA_SUBSTITUTED_FOR_WHOLE_GOAL")

    long_lived = value.get("long_lived_heartbeat")
    if not isinstance(long_lived, dict) or set(long_lived) != LONG_LIVED_HEARTBEAT_FIELDS:
        blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_HEARTBEAT_BINDING_INVALID")
    elif long_lived.get("session_kind") == PENDING_SESSION_KINDS["long_lived_heartbeat"]:
        if (
            session_mode != "preparation"
            or not _pending_session_binding_valid(
                long_lived, "long_lived_heartbeat", action.get("selected_action_id"), binding.get("head_ref")
            )
            or (base_dir is not None and not _repo_ref_digest_matches(
                base_dir, long_lived.get("entrypoint_ref"), long_lived.get("entrypoint_digest")
            ))
        ):
            blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_HEARTBEAT_BINDING_INVALID")
    else:
        session_started = _parse_time(long_lived.get("session_started_at"))
        source_merge_observed = _parse_time(long_lived.get("source_merge_observed_at"))
        recheck_at = _parse_time(long_lived.get("recheck_at"))
        recent_activity = long_lived.get("recent_activity")
        transition_applied, non_application_verified = _computed_session_application(
            long_lived, action.get("selected_action_id")
        )
        if (
            long_lived.get("session_kind") != "long_lived_control_session"
            or session_started is None
            or source_merge_observed is None
            or recheck_at is None
            or recheck_at < session_started
            or _empty(long_lived.get("source_merge_head"))
            or long_lived.get("source_merge_head") != binding.get("head_ref")
            or _empty(long_lived.get("entrypoint_ref"))
            or not _sha256(long_lived.get("entrypoint_digest"))
            or not _valid_session_invocation_command(
                long_lived.get("invocation_command"), "long_lived_heartbeat"
            )
            or long_lived.get("result_status") != "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY"
            or long_lived.get("automatic_session_start_interception") != "unproven"
            or long_lived.get("fresh_session_binding_state") != "separate_unproven_not_used_for_long_lived_recheck"
            or not isinstance(recent_activity, dict)
            or set(recent_activity) != {"source", "ci", "audit"}
            or any(not isinstance(recent_activity.get(field), bool) for field in recent_activity)
        ):
            blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_HEARTBEAT_BINDING_INVALID")
        if base_dir is not None and not _repo_ref_digest_matches(
            base_dir, long_lived.get("entrypoint_ref"), long_lived.get("entrypoint_digest")
        ):
            blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_HEARTBEAT_BINDING_INVALID")
        if recheck_at and source_merge_observed and recheck_at < source_merge_observed:
            blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_SESSION_PREMERGE_RECHECK_REQUIRED")
        if long_lived.get("prompt_mentions_inc178") is not True or long_lived.get("checker_invoked") is not True:
            blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_HEARTBEAT_CHECKER_NOT_INVOKED")
        if (
            long_lived.get("result_produced") is not True
            or not (transition_applied or non_application_verified)
            or long_lived.get("protected_next_action_id") != action.get("selected_action_id")
        ):
            blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_RESULT_NOT_CONSUMED")
        visible_zero = (progress.get("user_visible_capability_delta") or {}).get("classification") == "zero"
        elapsed = whole.get("whole_goal_elapsed_ms", 0)
        if (
            visible_zero
            and _nonnegative_int(elapsed)
            and elapsed >= 86_400_000
            and heartbeat.get("decision") == "DONT_NOTIFY"
            and (any(recent_activity.values()) if isinstance(recent_activity, dict) else False)
        ):
            blocks.append("BLOCKED_FOR_INC178_LONG_LIVED_RECENT_ACTIVITY_MASKING_ZERO_VISIBLE_DELTA")

    fresh = value.get("fresh_session_recheck")
    if not isinstance(fresh, dict) or set(fresh) != FRESH_SESSION_RECHECK_FIELDS:
        blocks.append("BLOCKED_FOR_INC178_FRESH_SESSION_RECHECK_BINDING_INVALID")
    elif fresh.get("session_kind") == PENDING_SESSION_KINDS["fresh_session"]:
        if (
            session_mode != "preparation"
            or not _pending_session_binding_valid(
                fresh, "fresh_session", action.get("selected_action_id"), binding.get("head_ref")
            )
            or (base_dir is not None and not _repo_ref_digest_matches(
                base_dir, fresh.get("entrypoint_ref"), fresh.get("entrypoint_digest")
            ))
        ):
            blocks.append("BLOCKED_FOR_INC178_FRESH_SESSION_RECHECK_BINDING_INVALID")
    else:
        session_started = _parse_time(fresh.get("session_started_at"))
        source_merge_observed = _parse_time(fresh.get("source_merge_observed_at"))
        recheck_at = _parse_time(fresh.get("recheck_at"))
        transition_applied, non_application_verified = _computed_session_application(
            fresh, action.get("selected_action_id")
        )
        if (
            fresh.get("session_kind") != "fresh_session_explicit_recheck"
            or session_started is None
            or source_merge_observed is None
            or recheck_at is None
            or session_started < source_merge_observed
            or recheck_at < session_started
            or _empty(fresh.get("source_merge_head"))
            or fresh.get("source_merge_head") != binding.get("head_ref")
            or _empty(fresh.get("entrypoint_ref"))
            or not _sha256(fresh.get("entrypoint_digest"))
            or not _valid_session_invocation_command(
                fresh.get("invocation_command"), "fresh_session"
            )
            or fresh.get("result_status") != "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY"
            or fresh.get("automatic_session_start_interception") != "unproven"
            or fresh.get("long_lived_binding_state") != "separate_unproven_not_used_for_fresh_session_recheck"
        ):
            blocks.append("BLOCKED_FOR_INC178_FRESH_SESSION_RECHECK_BINDING_INVALID")
        if base_dir is not None and not _repo_ref_digest_matches(
            base_dir, fresh.get("entrypoint_ref"), fresh.get("entrypoint_digest")
        ):
            blocks.append("BLOCKED_FOR_INC178_FRESH_SESSION_RECHECK_BINDING_INVALID")
        if (
            fresh.get("prompt_mentions_inc178") is not True
            or fresh.get("checker_invoked") is not True
            or fresh.get("result_produced") is not True
            or not (transition_applied or non_application_verified)
            or fresh.get("protected_next_action_id") != action.get("selected_action_id")
        ):
            blocks.append("BLOCKED_FOR_INC178_FRESH_SESSION_RESULT_NOT_CONSUMED")

    audit = value.get("audit_integration")
    audit_fields = {
        "systems_audit_required", "systems_audit_dispatched", "systems_audit_readback_received",
        "systems_audit_result_integrated", "systems_audit_changed_action",
        "exact_head_audit_performed", "exact_head_audit_substituted_for_systems_audit",
        "duplicate_same_head_claim_audit", "subagent_lanes_exist",
        "fourth_oversight_present", "fourth_oversight_self_demoted",
        "audit_records", "implementation_owner_thread_id",
    }
    audit_boolean_fields = audit_fields - {"audit_records", "implementation_owner_thread_id"}
    if not isinstance(audit, dict) or set(audit) != audit_fields or any(
        not isinstance(audit.get(field), bool) for field in audit_boolean_fields
    ):
        blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
        audit = {}
    else:
        implementation_owner_thread_id = audit.get("implementation_owner_thread_id")
        if _empty(implementation_owner_thread_id):
            blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
        audit_records = audit.get("audit_records")
        audit_record_fields = {
            "audit_id", "audit_type", "auditor_role", "source_thread_id", "head_ref",
            "claim_scope", "result_ref", "result_digest", "readback_received",
            "integrated_into_action_selection", "action_before_id", "action_after_id",
            "independent_from_implementation",
        }
        if not isinstance(audit_records, list) or any(
            not isinstance(row, dict) or set(row) != audit_record_fields
            or row.get("audit_type") not in {"systems", "exact_head"}
            or any(_empty(row.get(field)) for field in (
                "audit_id", "auditor_role", "source_thread_id", "head_ref", "claim_scope",
                "result_ref", "action_before_id", "action_after_id",
            ))
            or not _sha256(row.get("result_digest"))
            or not isinstance(row.get("readback_received"), bool)
            or not isinstance(row.get("integrated_into_action_selection"), bool)
            or row.get("independent_from_implementation")
            != (row.get("source_thread_id") != implementation_owner_thread_id)
            or row.get("source_thread_id") == implementation_owner_thread_id
            for row in audit_records or []
        ):
            blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
            audit_records = []
        if base_dir is not None and any(
            not _repo_ref_digest_matches(base_dir, row.get("result_ref"), row.get("result_digest"))
            for row in audit_records if isinstance(row, dict)
        ):
            blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
        systems_records = [row for row in audit_records if row.get("audit_type") == "systems"]
        exact_records = [row for row in audit_records if row.get("audit_type") == "exact_head"]
        duplicate_keys = [(row.get("audit_type"), row.get("head_ref"), row.get("claim_scope")) for row in audit_records]
        derived_duplicate = len(duplicate_keys) != len(set(duplicate_keys))
        derived_systems_changed = any(
            row.get("readback_received") is True
            and row.get("integrated_into_action_selection") is True
            and row.get("action_before_id") != row.get("action_after_id")
            and row.get("action_after_id") == action.get("selected_action_id")
            for row in systems_records
        )
        if (
            audit.get("systems_audit_dispatched") != bool(systems_records)
            or audit.get("systems_audit_readback_received")
            != bool(systems_records and all(row.get("readback_received") for row in systems_records))
            or audit.get("systems_audit_result_integrated")
            != bool(systems_records and all(row.get("integrated_into_action_selection") for row in systems_records))
            or audit.get("systems_audit_changed_action") != derived_systems_changed
            or audit.get("exact_head_audit_performed") != bool(exact_records)
            or audit.get("duplicate_same_head_claim_audit") != derived_duplicate
        ):
            blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
        audit_triggered = "repeated_user_warning" in reasons or counters.get("evidence_only_slice_count", 0) >= 3
        if audit_triggered and audit.get("systems_audit_required") is not True:
            blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
        if audit.get("systems_audit_dispatched") and (
            audit.get("systems_audit_readback_received") is not True
            or audit.get("systems_audit_result_integrated") is not True
            or audit.get("systems_audit_changed_action") is not True
        ):
            blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
        if audit.get("subagent_lanes_exist") and audit.get("systems_audit_result_integrated") is not True:
            blocks.append("BLOCKED_FOR_INC178_AUDIT_RESULT_NOT_INTEGRATED")
        if audit.get("exact_head_audit_substituted_for_systems_audit") is not False:
            blocks.append("BLOCKED_FOR_INC178_SCOPED_AUDIT_PASS_SELECTED_AS_NEXT_ACTION")
        if audit.get("duplicate_same_head_claim_audit") is not False:
            blocks.append("BLOCKED_FOR_INC178_DUPLICATE_AUDIT_SELECTED")
        if audit.get("fourth_oversight_present") and counters.get("product_decision_changed_count", 0) == 0 and audit.get("fourth_oversight_self_demoted") is not True:
            blocks.append("BLOCKED_FOR_INC178_NO_VALUE_OVERSIGHT_NOT_SELF_DEMOTED")

    gate = value.get("gate_burden")
    gate_fields = {"budget_ms", "actual_ms", "avoidable_model_cost_ms", "gate_burden_breached", "inventory", "demote_or_retire_candidates"}
    if not isinstance(gate, dict) or set(gate) != gate_fields or not _positive_int(gate.get("budget_ms")) or not _nonnegative_int(gate.get("actual_ms")) or not _nonnegative_int(gate.get("avoidable_model_cost_ms")) or not isinstance(gate.get("gate_burden_breached"), bool) or not isinstance(gate.get("inventory"), list) or not isinstance(gate.get("demote_or_retire_candidates"), list):
        blocks.append("BLOCKED_FOR_INC178_GATE_BURDEN_OR_CLASSIFICATION_INVALID")
        gate = {}
    else:
        inventory_fields = {"control_id", "gate_class", "protected_asset", "hazard", "owner", "trigger", "scope", "metric", "expiry", "elapsed_ms", "changed_action"}
        for row in gate.get("inventory", []):
            if not isinstance(row, dict) or set(row) != inventory_fields or row.get("gate_class") not in {"Authority Gate", "Claim Check", "support prerequisite"} or any(_empty(row.get(field)) for field in ("control_id", "owner", "trigger", "scope", "metric", "expiry")) or not _nonnegative_int(row.get("elapsed_ms")) or not isinstance(row.get("changed_action"), bool):
                blocks.append("BLOCKED_FOR_INC178_GATE_BURDEN_OR_CLASSIFICATION_INVALID")
                continue
            if row.get("gate_class") == "Authority Gate" and (_empty(row.get("protected_asset")) or _empty(row.get("hazard"))):
                blocks.append("BLOCKED_FOR_INC178_GATE_BURDEN_OR_CLASSIFICATION_INVALID")
        breached = gate.get("actual_ms", 0) > gate.get("budget_ms", 0)
        if gate.get("gate_burden_breached") != breached:
            blocks.append("BLOCKED_FOR_INC178_GATE_BURDEN_OR_CLASSIFICATION_INVALID")
        if breached and not gate.get("demote_or_retire_candidates"):
            blocks.append("BLOCKED_FOR_INC178_GATE_BURDEN_OR_CLASSIFICATION_INVALID")

    closeout = value.get("closeout")
    closeout_fields = {"status", "report_only", "validator_only", "product_loop_simplified", "unnecessary_gate_reduced", "observed_effective_claimed"}
    if not isinstance(closeout, dict) or set(closeout) != closeout_fields or closeout.get("status") not in {"open_replanned", "continue_replanned", "blocked_exact_authority", "closed"} or any(not isinstance(closeout.get(field), bool) for field in closeout_fields - {"status"}):
        blocks.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCHEMA_INVALID")
    else:
        if closeout.get("status") == "closed" and (
            closeout.get("report_only") is True
            or closeout.get("validator_only") is True
            or not (closeout.get("product_loop_simplified") or closeout.get("unnecessary_gate_reduced"))
        ):
            blocks.append("BLOCKED_FOR_INC178_INCIDENT_CLOSEOUT_WITHOUT_PRODUCT_LOOP_CHANGE")
        if closeout.get("observed_effective_claimed") is not False:
            blocks.append("BLOCKED_FOR_INC178_OBSERVED_EFFECTIVE_OVERCLAIM")

    terminal = value.get("terminal_continuation")
    blocker = terminal.get("progress_blocker") if isinstance(terminal, dict) else None
    if (
        not isinstance(terminal, dict)
        or set(terminal) != TERMINAL_CONTINUATION_FIELDS
        or any(not isinstance(terminal.get(field), bool) for field in (
            "terminal_result_consumed", "protected_adoption_held",
            "bounded_local_repair_dispatchable", "quiet_closeout_requested",
            "current_transition_checker_invoked", "selected_action_result_consumed",
            "control_dispatch_sent", "target_readback_received",
        ))
        or terminal.get("primary_state") not in TERMINAL_PRIMARY_STATES
        or terminal.get("control_dispatch_mode") not in {"", "control_dispatch"}
        or any(not isinstance(terminal.get(field), str) for field in (
            "dispatch_target_thread_id", "target_readback_marker",
        ))
        or not isinstance(blocker, dict)
        or set(blocker) != TERMINAL_PROGRESS_BLOCKER_FIELDS
        or not isinstance(blocker.get("present"), bool)
        or any(not isinstance(blocker.get(field), str) for field in (
            "blocker_id", "summary", "owner", "unblock_condition",
        ))
    ):
        blocks.append("BLOCKED_FOR_INC178_TERMINAL_CONTINUATION_SCHEMA_INVALID")
    else:
        closed_status = closeout.get("status") == "closed" if isinstance(closeout, dict) else False
        terminal_facts_present = (
            terminal.get("terminal_result_consumed") is True
            and terminal.get("protected_adoption_held") is True
            and terminal.get("primary_state") in {"idle", "notLoaded"}
        )
        if (
            terminal_facts_present
            and closed_status
            and terminal.get("quiet_closeout_requested") is not True
        ):
            blocks.append("BLOCKED_FOR_INC178_TERMINAL_CLOSEOUT_BINDING_INVALID")
        terminal_triggered = terminal_facts_present
        if terminal_triggered:
            if (
                terminal.get("current_transition_checker_invoked") is not True
                or terminal.get("selected_action_result_consumed") is not True
            ):
                blocks.append("BLOCKED_FOR_INC178_TERMINAL_CONTINUATION_NOT_CONSUMED")
            if not _terminal_receipt_consumed(
                value,
                action.get("selected_action_id"),
                session_mode,
            ):
                blocks.append("BLOCKED_FOR_INC178_TERMINAL_RECEIPT_NOT_CONSUMED")
            if terminal.get("bounded_local_repair_dispatchable") is True:
                if (
                    terminal.get("control_dispatch_sent") is not True
                    or terminal.get("control_dispatch_mode") != "control_dispatch"
                    or _empty(terminal.get("dispatch_target_thread_id"))
                    or terminal.get("target_readback_received") is not True
                    or _empty(terminal.get("target_readback_marker"))
                ):
                    blocks.append("BLOCKED_FOR_INC178_TERMINAL_REPAIR_DISPATCH_READBACK_REQUIRED")
                if blocker.get("present") is not False or any(
                    not _empty(blocker.get(field))
                    for field in ("blocker_id", "summary", "owner", "unblock_condition")
                ):
                    blocks.append("BLOCKED_FOR_INC178_TERMINAL_PROGRESS_BLOCKER_SUBSTITUTED")
            else:
                if (
                    terminal.get("control_dispatch_sent") is not False
                    or terminal.get("control_dispatch_mode") != ""
                    or not _empty(terminal.get("dispatch_target_thread_id"))
                    or terminal.get("target_readback_received") is not False
                    or not _empty(terminal.get("target_readback_marker"))
                ):
                    blocks.append("BLOCKED_FOR_INC178_TERMINAL_PROGRESS_BLOCKER_DISPATCH_CONTRADICTION")
                if (
                    blocker.get("present") is not True
                    or any(_empty(blocker.get(field)) for field in (
                        "blocker_id", "summary", "owner", "unblock_condition",
                    ))
                ):
                    blocks.append("BLOCKED_FOR_INC178_TERMINAL_PROGRESS_BLOCKER_REQUIRED")

    dependencies = value.get("external_dependencies")
    if value.get("fable5_dependency_banned") is not True or not isinstance(dependencies, dict) or set(dependencies) != {"fable5_required", "odg_required", "telemetry_required_to_continue_supervised_work"} or any(dependencies.get(field) is not False for field in dependencies or {}):
        blocks.append("BLOCKED_FOR_INC178_FABLE5_DURABLE_DEPENDENCY")
    if not isinstance(value.get("non_claims"), list) or not REQUIRED_NON_CLAIMS <= set(value.get("non_claims", [])):
        blocks.append("BLOCKED_FOR_INC178_NON_CLAIMS_MISSING")
    if session_mode == "long_lived_heartbeat":
        blocks = [block for block in blocks if block not in FRESH_SESSION_BINDING_BLOCKS]
    elif session_mode == "fresh_session":
        blocks = [block for block in blocks if block not in LONG_LIVED_BINDING_BLOCKS]
    return sorted(set(blocks))


def run_session_recheck(
    value: Any,
    mode: str,
    base_dir: Path,
    *,
    live_context: Any = None,
    evaluation_time: datetime | None = None,
    consume_next_action: bool = False,
    value_first_packet: Any = None,
) -> dict[str, Any]:
    """Execute one explicit session recheck and consume its selected action."""
    if mode not in {"long_lived_heartbeat", "fresh_session"}:
        return {
            "mode": mode,
            "checker_invoked": False,
            "result_produced": False,
            "result_consumed": False,
            "blocks": ["BLOCKED_FOR_INC178_SESSION_RECHECK_MODE_INVALID"],
        }

    binding_key = "long_lived_heartbeat" if mode == "long_lived_heartbeat" else "fresh_session_recheck"
    binding = value.get(binding_key, {}) if isinstance(value, dict) else {}
    pending = (
        isinstance(binding, dict)
        and binding.get("session_kind") == PENDING_SESSION_KINDS[mode]
    )
    effective_evaluation_time = evaluation_time or datetime.now(timezone.utc)
    blocks = check_contract(
        value,
        base_dir,
        session_mode="preparation" if pending else mode,
    )
    blocks.extend(_check_live_context(value, mode, live_context, effective_evaluation_time))
    if consume_next_action:
        blocks.extend(cmd_epoch_continuation_blocks(value))
    blocks = sorted(set(blocks))
    action = value.get("action_selection", {}) if isinstance(value, dict) else {}
    selected_action_id = action.get("selected_action_id") if isinstance(action, dict) else None
    protected_next_action_id = binding.get("protected_next_action_id") if isinstance(binding, dict) else None
    if pending:
        before_selected_action_id = binding.get("before_selected_action_id") if isinstance(binding, dict) else None
        transition_applied = (
            not blocks
            and isinstance(before_selected_action_id, str)
            and bool(before_selected_action_id)
            and before_selected_action_id != selected_action_id
        )
        verified_non_application = False
        receipt_after_selected_action_id = selected_action_id if transition_applied else None
    else:
        transition_applied, verified_non_application = _computed_session_application(binding, selected_action_id)
        receipt_after_selected_action_id = (
            binding.get("after_selected_action_id") if isinstance(binding, dict) else None
        )
    replan = value.get("replan", {}) if isinstance(value, dict) else {}
    protected_mutation_paused = (
        bool(_threshold_reasons(value))
        and isinstance(replan, dict)
        and replan.get("next_protected_mutation_paused") is True
        and selected_action_id == protected_next_action_id
    )
    result_consumed = not blocks and transition_applied and protected_mutation_paused
    verified_non_application = (
        not blocks and verified_non_application and protected_mutation_paused
    )
    if not blocks and not (result_consumed or verified_non_application):
        blocks = [
            "BLOCKED_FOR_INC178_LONG_LIVED_RESULT_NOT_CONSUMED"
            if mode == "long_lived_heartbeat"
            else "BLOCKED_FOR_INC178_FRESH_SESSION_RESULT_NOT_CONSUMED"
        ]
    value_first_decision = consume_value_first_next_action(
        value,
        blocks,
        value_first_packet,
    )
    value_first_diagnostics = value_first_decision.get("typed_diagnostics", [])
    if isinstance(value_first_diagnostics, list):
        blocks = sorted(set(blocks + [
            diagnostic for diagnostic in value_first_diagnostics
            if isinstance(diagnostic, str) and diagnostic.startswith("BLOCKED_FOR_")
        ]))
    if value_first_decision.get("decision") in {
        WHOLE_GOAL_DECISION_OWNER_OVERRIDE,
        WHOLE_GOAL_DECISION_OWNER_REQUIRED,
        WHOLE_GOAL_ORIGIN_CHANNEL_REQUIRED,
        WHOLE_GOAL_ORIGIN_CHANNEL_INVALID,
    }:
        blocks = sorted(set(blocks + [value_first_decision["decision"]]))
    epoch_continuation_blocked = bool(set(blocks) & CMD_EPOCH_CONTINUATION_BLOCKERS)
    work_continuation_allowed = (
        consume_next_action
        and not epoch_continuation_blocked
        and value_first_decision.get("work_continuation_allowed") is True
    )
    if blocks and work_continuation_allowed:
        result_status = "PROCEED_WITH_NONCLAIM"
    elif blocks and consume_next_action and value_first_decision.get("decision") == "WAIT_EXACT_AUTHORITY":
        result_status = "WAIT_EXACT_AUTHORITY"
    elif not blocks and (result_consumed or verified_non_application):
        result_status = "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY"
    else:
        result_status = "FAIL_WHOLE_GOAL_REPLAN_REQUIRED"

    result_state = (
        "CONTINUE"
        if work_continuation_allowed
        else "WAIT"
        if result_status == "WAIT_EXACT_AUTHORITY"
        else "STOP"
    )
    result_non_wait = result_state != "WAIT"
    protected_boundary_stop = bool(
        set(blocks) & CMD_EPOCH_CONTINUATION_BLOCKERS
    )

    decision_binding = value.get("decision_binding", {}) if isinstance(value, dict) else {}
    heartbeat = value.get("heartbeat_self_health", {}) if isinstance(value, dict) else {}
    planning_selection = planning_order_selection(value, blocks)
    return {
        "mode": mode,
        "checker_invoked": True,
        "result_produced": True,
        "receipt_observed_at": effective_evaluation_time.isoformat().replace("+00:00", "Z"),
        "result_status": result_status,
        "result_consumed": result_consumed,
        "result_consumption_state": (
            "selected_action_transition_consumed"
            if result_consumed
            else "verified_non_application"
            if verified_non_application
            else "not_consumed"
        ),
        "before_selected_action_id": binding.get("before_selected_action_id") if isinstance(binding, dict) else None,
        "after_selected_action_id": receipt_after_selected_action_id,
        "verified_non_application": verified_non_application,
        "protected_mutation_paused": protected_mutation_paused,
        "protected_next_action_id": protected_next_action_id,
        "goal_ref": decision_binding.get("goal_ref") if isinstance(decision_binding, dict) else None,
        "phase_ref": decision_binding.get("phase_ref") if isinstance(decision_binding, dict) else None,
        "head_ref": decision_binding.get("head_ref") if isinstance(decision_binding, dict) else None,
        "blocker_fingerprint": decision_binding.get("blocker_fingerprint") if isinstance(decision_binding, dict) else None,
        "decision": heartbeat.get("decision") if isinstance(heartbeat, dict) else None,
        "planning_order_selection": planning_selection,
        "value_first_decision": value_first_decision,
        "work_continuation_allowed": work_continuation_allowed,
        "state": result_state,
        "non_wait": result_non_wait,
        "protected_boundary_stop": protected_boundary_stop,
        "blocked_operations": [] if work_continuation_allowed else sorted(set(blocks)),
        "epoch_continuation_blocked": epoch_continuation_blocked,
        "automatic_session_start_interception": binding.get("automatic_session_start_interception") if isinstance(binding, dict) else None,
        "blocks": sorted(set(blocks)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("--long-lived-heartbeat", action="store_true")
    parser.add_argument("--fresh-session", action="store_true")
    parser.add_argument("--live-context")
    parser.add_argument("--consume-next-action", action="store_true")
    parser.add_argument("--value-first-packet")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.long_lived_heartbeat and args.fresh_session:
        result = {
            "tool": "mk_whole_goal_control",
            "status": "FAIL_WHOLE_GOAL_REPLAN_REQUIRED",
            "blocks": ["BLOCKED_FOR_INC178_SESSION_RECHECK_MODE_INVALID"],
            "non_claims": sorted(REQUIRED_NON_CLAIMS),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else result["status"])
        return 1
    path = Path(args.record)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = None
    if isinstance(doc, dict) and "example_current_transition" in doc:
        doc = doc["example_current_transition"]
    live_context = None
    if args.live_context:
        try:
            live_context_doc = json.loads(Path(args.live_context).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            live_context_doc = None
        if isinstance(live_context_doc, dict) and "example_live_context" in live_context_doc:
            live_context = live_context_doc["example_live_context"]
        else:
            live_context = live_context_doc
    evaluation_time = datetime.now(timezone.utc)
    value_first_packet = None
    if args.value_first_packet:
        try:
            value_first_packet = json.loads(Path(args.value_first_packet).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value_first_packet = None
    mode = "fresh_session" if args.fresh_session else "long_lived_heartbeat" if args.long_lived_heartbeat else None
    execution = (
        run_session_recheck(
            doc,
            mode,
            Path.cwd(),
            live_context=live_context,
            evaluation_time=evaluation_time,
            consume_next_action=args.consume_next_action,
            value_first_packet=value_first_packet,
        )
        if mode
        else None
    )
    blocks = execution["blocks"] if execution else check_contract(doc, Path.cwd())
    continuation_allowed = bool(execution and execution.get("work_continuation_allowed"))
    result = {
        "tool": "mk_whole_goal_control",
        "status": (
            execution.get("result_status")
            if execution
            else "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY" if not blocks else "FAIL_WHOLE_GOAL_REPLAN_REQUIRED"
        ),
        "blocks": blocks,
        "non_claims": sorted(REQUIRED_NON_CLAIMS),
    }
    if args.long_lived_heartbeat:
        result["long_lived_heartbeat_recheck"] = execution
    if args.fresh_session:
        result["fresh_session_recheck"] = execution
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else result["status"])
    return 0 if not blocks or continuation_allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
