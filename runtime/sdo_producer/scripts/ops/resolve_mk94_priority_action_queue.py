#!/usr/bin/env python3
"""Resolve the MK-94 priority action queue deterministically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


RECORD_PATH = "docs/ops/MK94_AUTONOMOUS_CONTINUATION_PRIORITY_ROUTER_20260508.json"
TASK_REGISTER_PATH = "plans/maestro-kernel-task-register-20260426.json"
COMPLETED_STATUSES = {
    "supervisor_contract_ready",
    "app_server_continuation_passed",
    "validated_local",
    "shared_runtime_evidence_dropbox_ready",
    "runtime_report_intake_passed",
    "post_refresh_hash_version_match_passed",
    "partial_closeout_ui_shell_implemented",
    "worktree_smoke_passed_new_session_lane_classified",
}
STALE_ACTION_TASK_IDS = {
    "completed_goal_autonomous_continuation_supervisor": "MK-95",
}
ACTION_TASK_IDS = {
    "completed_goal_autonomous_continuation_supervisor": "MK-95",
    "isolated_runtime_smoke_dropbox_report_intake": "MK-108",
    "codex_plusplus_runtime_e2e": "MK-93",
    "codex_use_skill": "MK-92",
    "remote_ops_live_adapter": "MK-90",
}
FRESH_RUNTIME_ACTION = {
    "action_id": "isolated_runtime_smoke_dropbox_report_intake",
    "title": "Run isolated runtime smoke and intake the shared dropbox report",
    "score_class": "runtime_evidence_before_ui_or_adoption",
    "priority_rank": 1,
    "next_task_id": "MK-108",
}
SCORE_ORDER = [
    "safety_or_authority_blocker",
    "product_reality_first_user_capability_delta",
    "realistic_operation_or_data_loop",
    "completed_goal_new_session_continuation_blocker",
    "runtime_evidence_before_ui_or_adoption",
    "validator_or_task_sync_blocker",
    "user_visible_ui_or_convenience_work",
    "exploratory_research_without_active_adoption_gate",
]

VALUE_DECISIONS = {
    "PROCEED_PRIMARY_WORK",
    "PROCEED_WITH_NONCLAIM",
    "WAIT_EXACT_AUTHORITY",
    "CANCEL_STALE_DISPATCH",
    "REMOVE_SUPPORT_FROM_CRITICAL_PATH",
}
SUPPORT_CLASSES = {"audit", "validator", "evidence", "receipt", "status", "red_team", "admission"}
COST_CATEGORIES = {"none", "low", "medium", "high", "unknown", "legacy_nonzero_unscaled"}


def _cost_category(critical: dict[str, Any], category_field: str, legacy_field: str) -> str:
    """Keep unlike costs separate while tolerating older numeric packets."""
    explicit = critical.get(category_field)
    if explicit in COST_CATEGORIES:
        return explicit
    legacy = critical.get(legacy_field)
    if not isinstance(legacy, (int, float)) or isinstance(legacy, bool) or legacy < 0:
        return "unknown"
    if legacy == 0:
        return "none"
    return "legacy_nonzero_unscaled"


def _contains_forbidden_precision_key(value: Any) -> bool:
    forbidden = {
        "raw_prompt",
        "private_prompt",
        "chain_of_thought",
        "hidden_chain_of_thought",
        "argv",
        "raw_argv",
        "process_command_column",
        "raw_process_command",
        "raw_process_command_column",
        "raw_command",
        "raw_terminal_log",
    }
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden or _contains_forbidden_precision_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_precision_key(child) for child in value)
    return False


def _precision_judgment(packet: dict[str, Any], user_epoch: int) -> dict[str, Any]:
    """Project only sanitized, decision-relevant Fable5 judgment context."""
    value = packet.get("precision_judgment")
    required_candidate_classes = {"baseline", "meaningfully_divergent", "no_action"}
    valid = isinstance(value, dict) and not _contains_forbidden_precision_key(value)
    candidates = value.get("candidate_options") if isinstance(value, dict) else None
    candidate_classes = {
        row.get("candidate_class")
        for row in candidates or []
        if isinstance(row, dict)
    } if isinstance(candidates, list) else set()
    candidate_ids = {
        row.get("candidate_id")
        for row in candidates or []
        if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)
    } if isinstance(candidates, list) else set()
    recommended_action_id = value.get("recommended_action_id") if isinstance(value, dict) else None
    valid = bool(
        valid
        and value.get("sanitized") is True
        and value.get("user_intent_epoch") == user_epoch
        and isinstance(value.get("phase"), str)
        and value["phase"].strip()
        and value.get("premise_disposition") in {"accepted", "adapted", "rejected", "held"}
        and isinstance(value.get("decision_changing_test"), str)
        and value["decision_changing_test"].strip()
        and required_candidate_classes <= candidate_classes
        and isinstance(recommended_action_id, str)
        and recommended_action_id in candidate_ids
        and isinstance(value.get("counterfactual_result"), str)
        and value["counterfactual_result"].strip()
        and value.get("fact_classification") in {"repo_fact", "observed_external_fact", "inference", "advisory"}
    )
    if not valid:
        return {
            "valid": False,
            "implementation_ready_claim_allowed": False,
            "safe_analysis_allowed": True,
            "claim_disposition": "PROCEED_WITH_NONCLAIM",
            "reason": "precision_judgment_missing_or_unsanitized",
        }
    return {
        "valid": True,
        # Fable5 precision judgment is a high-value advisory input.  Repo and
        # authority consumers, not the advisory record, own readiness claims.
        "implementation_ready_claim_allowed": False,
        "safe_analysis_allowed": True,
        "claim_disposition": "DECISION_CONTEXT_ACCEPTED_ADVISORY_ONLY",
        "user_intent_epoch": value["user_intent_epoch"],
        "phase": value["phase"],
        "premise_disposition": value["premise_disposition"],
        "decision_changing_test": value["decision_changing_test"],
        "candidate_classes": sorted(candidate_classes),
        "recommended_action_id": recommended_action_id,
        "counterfactual_result": value["counterfactual_result"],
        "fact_classification": value["fact_classification"],
    }


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def score_index(action: dict[str, Any]) -> int:
    score_class = action.get("score_class")
    try:
        return SCORE_ORDER.index(score_class)
    except ValueError:
        return len(SCORE_ORDER)


def task_statuses(task_register: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("task_id")): str(item.get("status"))
        for item in task_register.get("active_tasks", [])
        if isinstance(item, dict) and item.get("task_id")
    }


def is_completed(task_id: str | None, statuses: dict[str, str]) -> bool:
    return bool(task_id and statuses.get(task_id) in COMPLETED_STATUSES)


def resolve(record: dict[str, Any], task_register: dict[str, Any] | None = None) -> dict[str, Any]:
    statuses = task_statuses(task_register or {})
    candidates = [item for item in record.get("candidate_actions", []) if isinstance(item, dict)]
    ordered = sorted(candidates, key=lambda item: (score_index(item), int(item.get("priority_rank", 999)), str(item.get("action_id", ""))))
    selected = ordered[0] if ordered else None
    stale_selected = selected
    stale_next_task_id = record.get("selected_action", {}).get("next_task_id")
    selected_next_task_id = stale_next_task_id
    freshness_override_applied = False

    if selected and is_completed(STALE_ACTION_TASK_IDS.get(str(selected.get("action_id"))), statuses):
        freshness_override_applied = True
        runtime_task_id = FRESH_RUNTIME_ACTION["next_task_id"]
        if not is_completed(runtime_task_id, statuses):
            selected = FRESH_RUNTIME_ACTION
            selected_next_task_id = runtime_task_id
            ordered = [FRESH_RUNTIME_ACTION, *ordered]
        else:
            ordered = [FRESH_RUNTIME_ACTION, *ordered]
            selected = next(
                (
                    item
                    for item in ordered
                    if not is_completed(ACTION_TASK_IDS.get(str(item.get("action_id"))), statuses)
                ),
                None,
            )
            selected_next_task_id = ACTION_TASK_IDS.get(str(selected.get("action_id"))) if selected else None

    return {
        "ok": bool(selected),
        "resolver": "scripts/ops/resolve_mk94_priority_action_queue.py",
        "ranking_version": record.get("priority_model", {}).get("ranking_version"),
        "selected_action_id": selected.get("action_id") if selected else None,
        "ordered_action_ids": [item.get("action_id") for item in ordered],
        "task_register_update_required": record.get("selected_action", {}).get("task_register_update_required"),
        "next_task_id": selected_next_task_id,
        "freshness_override_applied": freshness_override_applied,
        "stale_selected_action_id": stale_selected.get("action_id") if stale_selected else None,
        "stale_next_task_id": stale_next_task_id,
        "completed_task_statuses_consulted": bool(task_register),
    }


def resolve_value_first(packet: dict[str, Any]) -> dict[str, Any]:
    """Resolve one CMD continuation without turning Claim Checks into work gates.

    This is deliberately a small decision, not another admission schema.  It
    protects only an immediate Authority Gate and otherwise returns the work to
    the highest-value cause-changing action.
    """
    user_epoch = packet.get("user_intent_epoch")
    dispatch_epoch = packet.get("dispatch_epoch")
    proposed = packet.get("proposed_action") if isinstance(packet.get("proposed_action"), dict) else {}
    primary = packet.get("primary_action") if isinstance(packet.get("primary_action"), dict) else {}
    critical = packet.get("critical_path") if isinstance(packet.get("critical_path"), dict) else {}

    invalid = []
    if not isinstance(user_epoch, int) or user_epoch < 0:
        invalid.append("user_intent_epoch")
    if not isinstance(dispatch_epoch, int) or dispatch_epoch < 0:
        invalid.append("dispatch_epoch")
    if not proposed.get("action_id"):
        invalid.append("proposed_action.action_id")
    if not primary.get("action_id"):
        invalid.append("primary_action.action_id")
    if invalid:
        return {
            "ok": False,
            "decision": "INVALID_VALUE_FIRST_PACKET",
            "invalid_fields": invalid,
            "selected_action_id": None,
        }
    precision_judgment = _precision_judgment(packet, user_epoch)

    immediate_authority = (
        proposed.get("action_class") == "authority"
        and proposed.get("immediate_authority_transition") is True
    )
    protected_asset = proposed.get("protected_asset")
    requested_control_effect = proposed.get("requested_control_effect")
    stop_or_hold_requested = requested_control_effect in {"STOP", "HOLD"}
    support_minutes = critical.get("support_only_elapsed_minutes", 0)
    prompt_minutes = critical.get("prompt_preparation_elapsed_minutes", 0)
    prevented_loss_category = _cost_category(
        critical, "expected_prevented_loss_category", "expected_prevented_loss"
    )
    user_capability_loss_category = _cost_category(
        critical, "user_capability_loss_category", "user_capability_loss"
    )
    paid_provider_cost_category = _cost_category(
        critical, "paid_provider_cost_category", "paid_provider_cost"
    )
    manual_relay_cost_category = _cost_category(
        critical, "manual_relay_cost_category", "manual_relay_cost"
    )
    false_block_risk_category = _cost_category(
        critical, "false_block_risk_category", "false_block_cost"
    )
    support_checkpoint_reached = (
        isinstance(support_minutes, (int, float))
        and not isinstance(support_minutes, bool)
        and support_minutes >= 30
    )
    prompt_checkpoint_reached = (
        isinstance(prompt_minutes, (int, float))
        and not isinstance(prompt_minutes, bool)
        and prompt_minutes >= 45
    )
    smallest_nonblocking_alternative = critical.get("smallest_nonblocking_alternative")
    if dispatch_epoch < user_epoch:
        decision = "CANCEL_STALE_DISPATCH"
        selected = primary["action_id"]
        reason = "direct_user_correction_invalidated_older_internal_dispatch"
    elif immediate_authority and isinstance(protected_asset, str) and protected_asset:
        decision = "WAIT_EXACT_AUTHORITY"
        selected = proposed["action_id"]
        reason = "next_exact_operation_mutates_a_named_protected_asset"
    elif stop_or_hold_requested:
        decision = "PROCEED_WITH_NONCLAIM"
        selected = primary["action_id"]
        reason = "claim_check_cannot_stop_safe_work_without_an_immediate_authority_transition"
    else:
        action_class = proposed.get("action_class")
        support_count = critical.get("support_only_items_before_proposed", 0)
        user_delta = proposed.get("user_capability_delta", 0)
        named_primary_blocker = proposed.get("named_primary_phase_blocker_unlocked")
        first_bounded_named_unblock = (
            action_class in SUPPORT_CLASSES
            and isinstance(named_primary_blocker, str)
            and bool(named_primary_blocker.strip())
            and support_count == 0
            and isinstance(support_minutes, (int, float))
            and support_minutes < 30
            and isinstance(prompt_minutes, (int, float))
            and prompt_minutes < 45
        )
        authoring_defects = set(proposed.get("authoring_defects") or [])
        forbidden_fable_checks = {
            "fable_self_checks_host_time",
            "fable_self_checks_input_hashes",
            "fable_self_checks_hidden_fallback",
            "fable_self_checks_transcript_health",
            "red_team_launch_prerequisite",
            "derived_requirements_override_user_baseline",
        }
        support_over_budget = (
            action_class in SUPPORT_CLASSES
            and (
                support_count >= 1
                or (isinstance(support_minutes, (int, float)) and support_minutes >= 30)
                or (isinstance(prompt_minutes, (int, float)) and prompt_minutes >= 45)
                or (user_delta == 0 and not first_bounded_named_unblock)
            )
        )
        if authoring_defects & forbidden_fable_checks or support_over_budget:
            decision = "REMOVE_SUPPORT_FROM_CRITICAL_PATH"
            selected = primary["action_id"]
            reason = "support_or_authoring_gate_delays_primary_user_value_without_authority_hazard"
        else:
            decision = "PROCEED_PRIMARY_WORK"
            proposed_is_admitted = (
                proposed.get("cause_changing") is True
                and user_delta > 0
                and action_class not in SUPPORT_CLASSES | {"authority"}
            ) or first_bounded_named_unblock
            default_selected = proposed["action_id"] if proposed_is_admitted else primary["action_id"]
            recommended = precision_judgment.get("recommended_action_id")
            admitted_action_ids = {primary["action_id"]}
            if proposed_is_admitted:
                admitted_action_ids.add(proposed["action_id"])
            selected = recommended if recommended in admitted_action_ids else default_selected
            reason = (
                "first_bounded_support_item_unlocks_named_primary_phase_blocker"
                if first_bounded_named_unblock and selected == proposed["action_id"]
                else "sanitized_precision_judgment_selected_an_already_admitted_safe_action"
                if selected == recommended and selected != default_selected
                else "no_immediate_authority_hazard_blocks_cause_changing_work"
            )

    return {
        "ok": decision in VALUE_DECISIONS,
        "decision": decision,
        "selected_action_id": selected,
        "reason": reason,
        "authority_gate_applied": decision == "WAIT_EXACT_AUTHORITY",
        "stale_dispatch_cancelled": decision == "CANCEL_STALE_DISPATCH",
        "work_continuation_allowed": decision != "WAIT_EXACT_AUTHORITY",
        "gate_cost_assessment": {
            "method": "categorical_bilateral_stop_assessment",
            "evaluated_only_for_stop_hold_or_authority": stop_or_hold_requested or immediate_authority,
            "prevention_side": {
                "expected_prevented_loss_category": prevented_loss_category,
                "named_protected_asset": protected_asset if immediate_authority else None,
            },
            "burden_side": {
                "user_capability_loss_category": user_capability_loss_category,
                "paid_provider_cost_category": paid_provider_cost_category,
                "manual_relay_cost_category": manual_relay_cost_category,
                "false_block_risk_category": false_block_risk_category,
                "support_30m_pivot_checkpoint_reached": support_checkpoint_reached,
                "prompt_45m_pivot_checkpoint_reached": prompt_checkpoint_reached,
            },
            "assessment_outcome": (
                "WAIT_EXACT_AUTHORITY_OUTSIDE_COST_TRADEOFF"
                if decision == "WAIT_EXACT_AUTHORITY"
                else "PIVOT_TO_NONBLOCKING_ALTERNATIVE"
                if stop_or_hold_requested or support_checkpoint_reached or prompt_checkpoint_reached
                else "NO_STOP_ASSESSMENT_REQUIRED"
            ),
            "smallest_nonblocking_alternative": smallest_nonblocking_alternative or primary["action_id"],
            "checkpoints_are_nonblocking_pivots": True,
            "authority_gate_not_bypassable_by_assessment": True,
            "authority_gate_not_bypassable_by_economics": True,
            "expected_prevented_loss": critical.get("expected_prevented_loss"),
            "total_blocking_cost": None,
            "false_block_outweighs_prevented_loss": None,
            "legacy_numeric_fields_are_deprecated_nondecision_inputs": True,
        },
        "support_only_progress_credit": 0,
        "precision_judgment_selection": precision_judgment,
        "precision_recommendation_applied": (
            precision_judgment.get("valid") is True
            and selected == precision_judgment.get("recommended_action_id")
        ),
        "new_control_requires_retirement_pair": True,
        "removed_or_retired_controls": packet.get("removed_or_retired_controls") or [],
        "non_claims": [
            "no_product_progress_from_router_or_incident_record",
            "no_provider_or_paid_operation_authority",
            "no_release_merge_deploy_or_credential_authority",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--decision-packet")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    base_dir = Path(args.base_dir).resolve()
    if args.decision_packet:
        packet = load_json(Path(args.decision_packet).resolve())
        result = resolve_value_first(packet)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else result["decision"])
        return 0 if result["ok"] else 1
    record = load_json(base_dir / RECORD_PATH)
    task_register_path = base_dir / TASK_REGISTER_PATH
    task_register = load_json(task_register_path) if task_register_path.is_file() else {}
    result = resolve(record, task_register)
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else result["selected_action_id"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
