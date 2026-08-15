#!/usr/bin/env python3
"""MK733G before-work decision preflight (firing surface for work selection).

Validates a candidate work-selection record BEFORE work starts, using the same
rule set that MK733E/MK733F enforce on recorded slices afterwards:

  - decision ledger: all ten questions answered non-empty;
  - quantified UX scorecard: baseline / target / measurement_method /
    failure_or_harm_condition all present and non-empty;
  - candidate options: the four required option classes present, a selected
    option identified, no empty option rows;
  - rejected options: at least one, each with reason and allowed reason class;
  - declared budget: positive integer max_tool_calls / max_validator_runs /
    max_iterations;
  - consulted_policy_refs: at least one entry whose policy_id exists in
    controls/active-policy-index.json;
  - non_claims: non-empty.

Exit 0 = preflight clean (support evidence only; NOT selection approval,
progress, or authority). Exit 1 = blocked; blockers are printed.

Usage:
  python3 scripts/ops/mk_decision_preflight.py --record path/to/record.json [--json]
  python3 scripts/ops/mk_decision_preflight.py --record path/to/mk747-decision.json --cognitive-shadow [--json]
  python3 scripts/ops/mk_decision_preflight.py --record path/to/mk747-decision.json --cognitive-shadow --hermes-creative-projection path/to/sanitized-result.json [--accepted-option-affinities path/to/affinities.json] [--json]

The MK747 path is explicit and shadow-only.  Without --cognitive-shadow this
module preserves the ordinary MK733J preflight path and result contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import re
from pathlib import Path
from typing import Any
from mk_adaptive_work_pace import check_contract as check_adaptive_work_pace_contract
from mk_fable5_execution_authority import (
    check_contract as check_fable5_execution_authority_contract,
    selection as fable5_execution_authority_selection,
)
from mk_whole_goal_control import (
    check_contract as check_whole_goal_control_contract,
    planning_order_selection,
)
from mk733j_schema_safety import contains_sensitive_key
from verify_task_ledger_v1 import validate_ledger

REPO = Path(__file__).resolve().parents[2]
ACTIVE_POLICY_INDEX = REPO / "controls" / "active-policy-index.json"
# Authoritative state precedence for T-402 is deliberately fixed:
#   1. the canonical repo ledger, if it exists;
#   2. an explicit CMD-owned fallback path, only while the canonical file is absent;
#   3. fail closed as unavailable.
# Candidate selection_record.ledger_state is never a state source for the cap.
CANONICAL_TASK_LEDGER = REPO / "mission-output" / "ultra" / "TASK_LEDGER.json"

REQUIRED_LEDGER_QUESTIONS = {
    "q1_what_user_experience_improves",
    "q2_why_this_action_is_necessary",
    "q3_why_now",
    "q4_what_happens_if_not_done",
    "q5_what_alternatives_were_considered",
    "q6_which_alternative_is_lower_cost",
    "q7_why_selected_path_is_optimal",
    "q8_how_effect_is_measured",
    "q9_what_proves_it_was_unnecessary_or_harmful",
    "q10_which_prior_incident_would_recur_if_missing",
}
REQUIRED_SCORECARD_FIELDS = {"baseline", "target", "measurement_method", "failure_or_harm_condition"}
REQUIRED_OPTION_CLASSES = {
    "selected_option",
    "lower_cost_option",
    "no_action_stop_option",
    "wrong_lane_or_evidence_only_option",
}
ALLOWED_REJECTED_REASON_CLASSES = {
    "ux_value", "cost", "authority_boundary", "incident_recurrence", "fake_pass_risk",
}
REQUIRED_BUDGET_FIELDS = ("max_tool_calls", "max_validator_runs", "max_iterations")
PREFLIGHT_CONTRACT_VERSION = "mk733j-preflight-v1"
REQUIRED_BINDING_FIELDS = {
    "preflight_contract_version", "work_id", "goal_ref", "task_class", "risk_class",
    "context_digest", "workpack_digest", "binding_record_digest", "preflight_scope_digest",
    "operation_manifest_digest", "incident_recurrence_scan",
}
PREFLIGHT_TOP_FIELDS = {
    "record_type", "mk_id", "native_goal_ref", "workpack_ref", "support_work_progress_credit", "ui_or_remote_ops_mutation",
    "decision_ledger", "ux_scorecard", "candidate_options", "selected_option_id", "rejected_options", "declared_budget", "consulted_policy_refs", "non_claims",
    *REQUIRED_BINDING_FIELDS, "operation_manifest_digest", "work_class", "deterministic_result",
}
PREFLIGHT_BOUND_RECORD_FIELDS = PREFLIGHT_TOP_FIELDS - {"deterministic_result"}
PREFLIGHT_BOUND_OPTIONAL_FIELDS = {
    "adaptive_work_pace_replan",
    "whole_goal_work_selection",
    "fable5_execution_authorization",
    "selection_record",
    # These are emitted by this consumer after validating the immutable
    # preflight body.  They are derived results, not caller-controlled scope.
    "planning_order_selection",
    "planning_order_continuation",
    "fable5_execution_authority_selection",
    "fable5_execution_authority_continuation",
}
DERIVED_PREFLIGHT_FIELDS = {
    "deterministic_result",
    "planning_order_selection",
    "planning_order_continuation",
    "fable5_execution_authority_selection",
    "fable5_execution_authority_continuation",
}
FABLE5_EXECUTION_TASK_CLASSES = {
    "fable5_external_submission",
    "fable5_session_resume",
    "fable5_followup_submission",
}
PAID_PROVIDER_RISK_CLASS = "paid_provider_or_credit_consuming_action"
CMD_TASK_SELECTION_CLASS = "cmd_task_selection"
DECISION_CHANGING_FIELDS = {
    "primary_user_goal_advanced",
    "specific_claim_or_asset",
    "minimum_confirmation",
    "next_action_changed",
    "safe_work_blocked_without_it",
}
SELECTION_RECORD_FIELDS = {
    "candidate_task_ref",
    "work_class",
    "selected_route",
    "decision_changing_record",
    "ledger_state",
}
LEDGER_STATE_FIELDS = {"schema_version", "active_support_lane_refs"}
REQUIRED_SELECTION_CASES = {
    "positive_support_with_concrete_decision_delta",
    "negative_credit_zero_support_on_critical_path",
    "negative_second_concurrent_support_lane",
    "negative_candidate_claims_empty_authoritative_occupied",
    "positive_authoritative_empty_support_lane",
    "neutral_unrelated_with_empty_selection_record",
    "neutral_unrelated_with_populated_selection_record",
}


def _sensitive_payload(value: Any) -> bool:
    """Reject private payload fields recursively before any preflight artifact is written."""
    return contains_sensitive_key(value)


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def record_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def preflight_scope_digest(record: dict[str, Any]) -> str:
    """Digest immutable work intent before operation-manifest authorization.

    The operation manifest is bound immediately afterwards, so excluding its
    digest here avoids a hash cycle while still making every operation digest
    depend on work/goal/task/risk/context and the ten-question decision record.
    """
    scope = {
        key: value
        for key, value in record.items()
        if key not in DERIVED_PREFLIGHT_FIELDS | {"preflight_scope_digest", "operation_manifest_digest"}
    }
    return record_digest(scope)


def known_policy_ids() -> set[str]:
    if not ACTIVE_POLICY_INDEX.exists():
        return set()
    doc = load_json(ACTIVE_POLICY_INDEX)
    return {r.get("id") for r in doc.get("active_policy_refs", []) or [] if r.get("id")}


def load_authoritative_task_ledger(
    fallback_path: Path | None = None,
    *,
    canonical_path: Path = CANONICAL_TASK_LEDGER,
) -> dict[str, Any]:
    """Resolve independently owned task_ledger.v1 state; never use candidate state.

    The canonical repo path always wins when present. An explicit fallback is
    accepted only when that path is absent, which supports CMD-owned runtime
    placement without making candidate JSON an authority source.
    """
    if canonical_path.exists():
        source_path = canonical_path
        source = "canonical_task_ledger"
    elif fallback_path is not None:
        source_path = fallback_path
        source = "explicit_cmd_fallback"
    else:
        return {
            "source": "unavailable",
            "path": str(canonical_path),
            "active_support_lane_refs": None,
            "errors": ["BLOCKED_FOR_T402_AUTHORITATIVE_LEDGER_UNAVAILABLE"],
        }

    try:
        payload = load_json(source_path)
    except (OSError, json.JSONDecodeError):
        return {
            "source": source,
            "path": str(source_path),
            "active_support_lane_refs": None,
            "errors": ["BLOCKED_FOR_T402_AUTHORITATIVE_LEDGER_INVALID"],
        }

    if validate_ledger(payload):
        return {
            "source": source,
            "path": str(source_path),
            "active_support_lane_refs": None,
            "errors": ["BLOCKED_FOR_T402_AUTHORITATIVE_LEDGER_INVALID"],
        }
    selection = payload.get("selection_record")
    active_refs = (
        selection.get("ledger_state", {}).get("active_support_lane_refs")
        if isinstance(selection, dict)
        else []
    )
    return {
        "source": source,
        "path": str(source_path),
        "active_support_lane_refs": active_refs,
        "errors": [],
    }


def check_decision_changing_selection(
    record: dict[str, Any],
    authoritative_ledger: dict[str, Any] | None = None,
) -> list[str]:
    """Validate the CMD selection slice using only authoritative cap state."""
    if record.get("task_class") != CMD_TASK_SELECTION_CLASS:
        return []

    selection = record.get("selection_record")
    if not isinstance(selection, dict) or set(selection) != SELECTION_RECORD_FIELDS:
        return ["BLOCKED_FOR_T402_DECISION_CHANGING_RECORD_REQUIRED"]

    blocks: list[str] = []
    decision = selection.get("decision_changing_record")
    if (
        not isinstance(decision, dict)
        or set(decision) != DECISION_CHANGING_FIELDS
        or not isinstance(decision.get("primary_user_goal_advanced"), bool)
        or not isinstance(decision.get("next_action_changed"), bool)
        or not isinstance(decision.get("safe_work_blocked_without_it"), bool)
        or not all(
            isinstance(decision.get(field), str) and bool(decision[field].strip())
            for field in ("specific_claim_or_asset", "minimum_confirmation")
        )
        or not isinstance(selection.get("candidate_task_ref"), str)
        or not selection["candidate_task_ref"].strip()
        or selection.get("work_class") not in {"primary", "support"}
        or selection.get("selected_route") not in {"critical_path", "support_lane", "deferred"}
    ):
        blocks.append("BLOCKED_FOR_T402_DECISION_CHANGING_RECORD_REQUIRED")

    # This candidate snapshot remains schema-validated for a usable
    # task_ledger.v1 selection_record, but is ignored for cap enforcement.
    candidate_ledger = selection.get("ledger_state")
    candidate_active_refs = (
        candidate_ledger.get("active_support_lane_refs")
        if isinstance(candidate_ledger, dict)
        else None
    )
    candidate_active_refs_valid = (
        isinstance(candidate_active_refs, list)
        and len(candidate_active_refs) <= 1
        and all(isinstance(ref, str) and bool(ref.strip()) for ref in candidate_active_refs)
        and len(candidate_active_refs) == len(set(candidate_active_refs))
    )
    if (
        not isinstance(candidate_ledger, dict)
        or set(candidate_ledger) != LEDGER_STATE_FIELDS
        or candidate_ledger.get("schema_version") != "task_ledger.v1"
        or not candidate_active_refs_valid
    ):
        blocks.append("BLOCKED_FOR_T402_SELECTION_LEDGER_STATE_INVALID")

    if isinstance(decision, dict) and selection.get("work_class") == "support":
        if (
            decision.get("primary_user_goal_advanced") is False
            and decision.get("next_action_changed") is False
            and selection.get("selected_route") == "critical_path"
        ):
            blocks.append("BLOCKED_FOR_T402_CREDIT_ZERO_SUPPORT_ON_CRITICAL_PATH")
        if selection.get("selected_route") == "support_lane":
            authoritative = authoritative_ledger or load_authoritative_task_ledger()
            authoritative_errors = authoritative.get("errors", [])
            if authoritative_errors:
                blocks.extend(authoritative_errors)
            elif authoritative.get("active_support_lane_refs"):
                blocks.append("BLOCKED_FOR_T402_SECOND_CONCURRENT_SUPPORT_LANE")
    return sorted(set(blocks))


def check_selection_schema_surface() -> list[str]:
    schema = load_json(REPO / "schemas/maestro-kernel/task_ledger.v1.schema.json")
    definition = schema.get("$defs", {}).get("selection_record", {})
    decision = definition.get("properties", {}).get("decision_changing_record", {})
    ledger = definition.get("properties", {}).get("ledger_state", {})
    active_refs = ledger.get("properties", {}).get("active_support_lane_refs", {})
    if (
        schema.get("properties", {}).get("selection_record", {}).get("$ref")
        != "#/$defs/selection_record"
        or set(definition.get("required", [])) != SELECTION_RECORD_FIELDS
        or set(decision.get("required", [])) != DECISION_CHANGING_FIELDS
        or set(ledger.get("required", [])) != LEDGER_STATE_FIELDS
        or active_refs.get("maxItems") != 1
    ):
        return ["BLOCKED_FOR_T402_SELECTION_SCHEMA_INVALID"]
    return []


def run_selection_fixture_suite(path: Path) -> dict[str, Any]:
    fixture = load_json(path)
    results = []
    authoritative_fixture_path = Path(fixture.get("authoritative_ledger_fixture", ""))
    if not authoritative_fixture_path.is_absolute():
        authoritative_fixture_path = (REPO / authoritative_fixture_path).resolve()
    authoritative_template = (
        load_json(authoritative_fixture_path)
        if authoritative_fixture_path.is_file()
        else None
    )
    for case in fixture.get("cases", []):
        authoritative_ledger = None
        expected_source = case.get("expected_authoritative_source")
        source_mode = case.get("authoritative_source")
        if source_mode in {"canonical", "explicit_fallback"} and isinstance(authoritative_template, dict):
            payload = json.loads(json.dumps(authoritative_template))
            payload["selection_record"]["ledger_state"]["active_support_lane_refs"] = case.get(
                "authoritative_active_support_lane_refs",
                [],
            )
            with tempfile.TemporaryDirectory(prefix="t402-ledger-fixture-") as temp_dir:
                temp_root = Path(temp_dir)
                canonical_path = temp_root / "mission-output" / "ultra" / "TASK_LEDGER.json"
                fallback_path = temp_root / "cmd-owned-task-ledger.json"
                if source_mode == "canonical":
                    canonical_path.parent.mkdir(parents=True, exist_ok=True)
                    canonical_path.write_text(
                        json.dumps(payload, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    fallback_payload = json.loads(json.dumps(payload))
                    fallback_payload["selection_record"]["ledger_state"][
                        "active_support_lane_refs"
                    ] = case.get("fallback_active_support_lane_refs", [])
                    fallback_path.write_text(
                        json.dumps(fallback_payload, indent=2) + "\n",
                        encoding="utf-8",
                    )
                else:
                    fallback_path.write_text(
                        json.dumps(payload, indent=2) + "\n",
                        encoding="utf-8",
                    )
                authoritative_ledger = load_authoritative_task_ledger(
                    fallback_path,
                    canonical_path=canonical_path,
                )
                actual = check_decision_changing_selection(
                    case.get("record", {}),
                    authoritative_ledger,
                )
        else:
            actual = check_decision_changing_selection(case.get("record", {}))
        expected = sorted(case.get("expected_blocks", []))
        actual_source = (
            authoritative_ledger.get("source")
            if isinstance(authoritative_ledger, dict)
            else "not_required"
        )
        source_ok = expected_source is None or actual_source == expected_source
        results.append({
            "case_id": case.get("case_id"),
            "expected_blocks": expected,
            "actual_blocks": actual,
            "authoritative_source": actual_source,
            "status": "PASS" if actual == expected and source_ok else "FAIL",
        })
    schema_blocks = check_selection_schema_surface()
    passed = (
        fixture.get("schema_version") == "task_ledger_selection_cases.v1"
        and {result["case_id"] for result in results} == REQUIRED_SELECTION_CASES
        and all(result["status"] == "PASS" for result in results)
        and not schema_blocks
    )
    return {
        "tool": "mk_decision_preflight",
        "mode": "selection_fixture_suite",
        "fixture": str(path),
        "cases": results,
        "schema_blocks": schema_blocks,
        "status": "PASS_T402_DECISION_CHANGING_SELECTION_FIXTURES" if passed else "FAIL_T402_SELECTION_FIXTURES",
    }


def fable5_execution_authority_required(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        record.get("task_class") in FABLE5_EXECUTION_TASK_CLASSES
        or record.get("risk_class") == PAID_PROVIDER_RISK_CLASS
        or "fable5_execution_authorization" in record
    )


def consume_planning_order_selection(
    record: Any,
    raw_blocks: list[str],
) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None]:
    """Consume planning-order Claim Check output without mutating the input.

    Planning inversion blockers become a deterministic REORDER decision for
    the planner/CMD consumer. Other blockers remain blocking. The returned
    continuation is the only state update the caller may persist.
    """
    if not isinstance(record, dict):
        return list(raw_blocks), None, None
    whole_goal = record.get("whole_goal_work_selection")
    if not isinstance(whole_goal, dict):
        return list(raw_blocks), None, None
    selection_result = planning_order_selection(whole_goal, raw_blocks)
    if selection_result.get("decision") != "REORDER_PRIMARY_PLANNING_FIRST":
        return list(raw_blocks), selection_result, None

    transition = selection_result.get("diagnosis_transition", {})
    continuation = None
    if (
        isinstance(transition, dict)
        and transition.get("transition_applied") is True
        and transition.get("before") == {"state": "pending", "run_count": 0}
        and transition.get("after") == {"state": "consumed", "run_count": 1}
    ):
        continuation = {
            "contract_version": "planning_order_continuation.v1",
            "apply_to": "whole_goal_work_selection.planning_order.diagnosis",
            "source_decision": "REORDER_PRIMARY_PLANNING_FIRST",
            "requires_user_correction": False,
            "before": transition["before"],
            "after": transition["after"],
        }
    if continuation is None:
        return list(raw_blocks), selection_result, None
    planning_blocks = set(selection_result.get("planning_blocks", []) or [])
    remaining_blocks = [block for block in raw_blocks if block not in planning_blocks]
    return sorted(set(remaining_blocks)), selection_result, continuation


def check_work_selection_record(
    record: dict[str, Any],
    authoritative_ledger: dict[str, Any] | None = None,
) -> list[str]:
    b: list[str] = []

    # This public validator is also called by receipt issuance.  Establish the
    # container types before touching .keys()/.get() so hostile malformed JSON
    # produces a deterministic blocker rather than an AttributeError.
    if not isinstance(record, dict):
        return [
            "BLOCKED_FOR_MK733G_PREFLIGHT_DECISION_LEDGER_INCOMPLETE",
            "BLOCKED_FOR_MK733G_PREFLIGHT_UX_SCORECARD_MISSING",
            "BLOCKED_FOR_MK733G_PREFLIGHT_CANDIDATE_OPTIONS_INCOMPLETE",
            "BLOCKED_FOR_MK733G_PREFLIGHT_REJECTED_OPTION_REASON_MISSING",
            "BLOCKED_FOR_MK733G_PREFLIGHT_BUDGET_MISSING",
            "BLOCKED_FOR_MK733G_PREFLIGHT_CONSULTED_POLICY_REFS_MISSING",
            "BLOCKED_FOR_MK733G_PREFLIGHT_NON_CLAIMS_MISSING",
        ]

    ledger = record.get("decision_ledger")
    if not isinstance(ledger, dict) or not REQUIRED_LEDGER_QUESTIONS <= set(ledger) or any(
        _empty(ledger.get(q)) for q in REQUIRED_LEDGER_QUESTIONS
    ):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_DECISION_LEDGER_INCOMPLETE")

    score = record.get("ux_scorecard")
    if not isinstance(score, dict) or not REQUIRED_SCORECARD_FIELDS <= set(score) or any(
        _empty(score.get(f)) for f in REQUIRED_SCORECARD_FIELDS
    ):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_UX_SCORECARD_MISSING")

    options = record.get("candidate_options")
    if not isinstance(options, list):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_CANDIDATE_OPTIONS_INCOMPLETE")
        options = []
    seen_classes = {o.get("option_class") for o in options if isinstance(o, dict)}
    selected_id = record.get("selected_option_id")
    selected = next(
        (o for o in options if isinstance(o, dict) and o.get("option_id") == selected_id), None
    )
    if not REQUIRED_OPTION_CLASSES <= seen_classes or _empty(selected_id) or not selected:
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_CANDIDATE_OPTIONS_INCOMPLETE")
    if any(not isinstance(o, dict) for o in options) or any(
        _empty(o.get("option_id")) or _empty(o.get("description")) or _empty(o.get("option_class"))
        for o in options
        if isinstance(o, dict)
    ):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_CANDIDATE_OPTIONS_INCOMPLETE")

    rejected = record.get("rejected_options")
    if not isinstance(rejected, list):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_REJECTED_OPTION_REASON_MISSING")
        rejected = []
    if not rejected or any(not isinstance(r, dict) for r in rejected) or any(
        _empty(r.get("option_id"))
        or _empty(r.get("reason"))
        or r.get("reason_class") not in ALLOWED_REJECTED_REASON_CLASSES
        for r in rejected
        if isinstance(r, dict)
    ):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_REJECTED_OPTION_REASON_MISSING")

    budget = record.get("declared_budget")
    if not isinstance(budget, dict) or any(not isinstance(budget.get(f), int) or budget.get(f, 0) <= 0 for f in REQUIRED_BUDGET_FIELDS):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_BUDGET_MISSING")

    # Work class is the independent trigger. A caller cannot suppress the
    # whole-goal check merely by omitting both optional control objects.
    work_class = record.get("work_class")
    paced_classes = {"delegated_nontrivial", "cross_repo_phase", "external_wait"}
    exempt_classes = {
        "read_only_exploration", "small_reversible_local",
        "normal_local_bounded_supervised", "local_read_only_cause_repair",
    }
    if work_class in paced_classes and "whole_goal_work_selection" not in record:
        b.append("BLOCKED_FOR_INC178_WHOLE_GOAL_TRANSITION_REQUIRED")
    if "preflight_contract_version" in record and work_class not in paced_classes | exempt_classes:
        b.append("BLOCKED_FOR_INC178_WHOLE_GOAL_SCOPE_INVALID")

    # This remains an ordering/claim check for explicitly paced routed
    # decisions, not a global gate for supervised or local cause-repair work.
    if "adaptive_work_pace_replan" in record:
        pace = record.get("adaptive_work_pace_replan")
        b.extend(check_adaptive_work_pace_contract(pace, enforce_timing=False))
        if isinstance(pace, dict) and pace.get("work_class") != work_class:
            b.append("BLOCKED_FOR_INC178_WHOLE_GOAL_BINDING_MISMATCH")
    if "whole_goal_work_selection" in record:
        whole_goal_control = record.get("whole_goal_work_selection")
        b.extend(check_whole_goal_control_contract(whole_goal_control, base_dir=REPO))
        binding = whole_goal_control.get("decision_binding", {}) if isinstance(whole_goal_control, dict) else {}
        whole = whole_goal_control.get("whole_goal", {}) if isinstance(whole_goal_control, dict) else {}
        if (
            not isinstance(whole_goal_control, dict)
            or whole_goal_control.get("work_class") != work_class
            or binding.get("goal_ref") != record.get("goal_ref")
            or whole.get("goal_ref") != record.get("goal_ref")
        ):
            b.append("BLOCKED_FOR_INC178_WHOLE_GOAL_BINDING_MISMATCH")

    if fable5_execution_authority_required(record):
        b.extend(
            check_fable5_execution_authority_contract(
                record.get("fable5_execution_authorization"),
                required=True,
            )
        )

    refs = record.get("consulted_policy_refs")
    if not isinstance(refs, list):
        refs = []
    known = known_policy_ids()
    valid_refs = [
        r for r in refs
        if isinstance(r, dict) and r.get("policy_id") in known and not _empty(r.get("why_consulted"))
    ]
    if not valid_refs:
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_CONSULTED_POLICY_REFS_MISSING")

    if not isinstance(record.get("non_claims"), list) or _empty(record.get("non_claims")):
        b.append("BLOCKED_FOR_MK733G_PREFLIGHT_NON_CLAIMS_MISSING")

    b.extend(check_decision_changing_selection(record, authoritative_ledger))
    return sorted(set(b))


def check_bound_work_selection_record(
    record: dict[str, Any],
    authoritative_ledger: dict[str, Any] | None = None,
) -> list[str]:
    b=check_work_selection_record(record, authoritative_ledger)
    if not isinstance(record,dict):
        return sorted(set(b + ["BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID"]))
    allowed_fields = PREFLIGHT_BOUND_RECORD_FIELDS | PREFLIGHT_BOUND_OPTIONAL_FIELDS
    if not (PREFLIGHT_BOUND_RECORD_FIELDS <= set(record) <= allowed_fields):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID")
    ledger=record.get("decision_ledger");score=record.get("ux_scorecard");budget=record.get("declared_budget")
    if not isinstance(ledger,dict) or set(ledger)!=REQUIRED_LEDGER_QUESTIONS or not all(isinstance(value,str) and value.strip() for value in ledger.values()) or not isinstance(score,dict) or set(score)!=REQUIRED_SCORECARD_FIELDS or not all(isinstance(value,str) and value.strip() for value in score.values()) or not isinstance(budget,dict) or set(budget)!=set(REQUIRED_BUDGET_FIELDS) or not all(isinstance(value,int) and not isinstance(value,bool) and value>0 for value in budget.values()):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID")
    candidate_rows=record.get("candidate_options");rejected_rows=record.get("rejected_options");policy_rows=record.get("consulted_policy_refs")
    if not isinstance(candidate_rows,list) or not isinstance(rejected_rows,list) or not isinstance(policy_rows,list) or not all(isinstance(row,dict) and set(row)=={"option_id","option_class","description"} and all(isinstance(row.get(key),str) and row[key] for key in row) and row.get("option_class") in REQUIRED_OPTION_CLASSES for row in candidate_rows) or not all(isinstance(row,dict) and set(row)=={"option_id","reason","reason_class"} and all(isinstance(row.get(key),str) and row[key] for key in row) and row.get("reason_class") in ALLOWED_REJECTED_REASON_CLASSES for row in rejected_rows) or not all(isinstance(row,dict) and set(row)=={"policy_id","why_consulted"} and all(isinstance(row.get(key),str) and row[key] for key in row) for row in policy_rows):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID")
    if not isinstance(record.get("non_claims"),list) or not record["non_claims"] or not all(isinstance(item,str) and item for item in record["non_claims"]):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID")
    scalar_fields=("record_type","mk_id","native_goal_ref","workpack_ref","preflight_contract_version","work_id","goal_ref","task_class","risk_class","context_digest","workpack_digest","binding_record_digest","preflight_scope_digest","operation_manifest_digest","work_class")
    if any(not isinstance(record.get(key),str) or not record[key] for key in scalar_fields):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID")
    if record.get("support_work_progress_credit") != 0 or record.get("ui_or_remote_ops_mutation") is not False or not isinstance(record.get("selected_option_id"),str) or not record["selected_option_id"]:
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID")
    if _sensitive_payload(record):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SENSITIVE_CONTENT")
    if not REQUIRED_BINDING_FIELDS <= set(record) or record.get("preflight_contract_version") != PREFLIGHT_CONTRACT_VERSION or any(_empty(record.get(k)) for k in REQUIRED_BINDING_FIELDS):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_BINDING_MISSING")
    incidents=record.get("incident_recurrence_scan")
    if not isinstance(incidents,list) or not incidents or any(not isinstance(row,dict) or set(row)!={"incident_ref","mitigation"} or _empty(row.get("incident_ref")) or _empty(row.get("mitigation")) for row in incidents):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_INCIDENT_SCAN_MISSING")
    if record.get("preflight_scope_digest") != preflight_scope_digest(record):
        b.append("BLOCKED_FOR_MK733J_PREFLIGHT_SCOPE_DIGEST_INVALID")
    return sorted(set(b))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".", help="accepted for command compatibility; repo root is inferred")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--record", help="path to the candidate work-selection record JSON")
    source.add_argument("--fixture-suite", help="run the registered T-402 selection fixture pack")
    parser.add_argument(
        "--authoritative-ledger",
        help="explicit CMD-owned task_ledger.v1 fallback; canonical repo ledger wins when present",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="write the repo-safe bound preflight result artifact")
    parser.add_argument(
        "--cognitive-shadow",
        action="store_true",
        help="explicitly evaluate an MK747 current decision in shadow-only mode",
    )
    parser.add_argument(
        "--hermes-creative-projection",
        help="optional sanitized Hermes creative result for the existing MK747 shadow consumer",
    )
    parser.add_argument(
        "--accepted-option-affinities",
        help="optional caller-accepted MK747 option affinities; never inferred from Hermes",
    )
    args = parser.parse_args()

    if args.fixture_suite:
        fixture_path = Path(args.fixture_suite)
        if not fixture_path.is_absolute():
            fixture_path = (REPO / fixture_path).resolve()
        result = run_selection_fixture_suite(fixture_path)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else result["status"])
        return 0 if result["status"].startswith("PASS") else 1

    record_path = Path(args.record)
    if not record_path.is_absolute():
        record_path = (REPO / args.record).resolve()
    if not record_path.exists():
        print(f"BLOCKED_FOR_MK733G_PREFLIGHT_RECORD_MISSING: {record_path}", file=sys.stderr)
        return 1

    record = load_json(record_path)
    if args.cognitive_shadow:
        # Lazy import keeps the established preflight path byte-for-byte
        # independent of the optional cognitive evaluator until explicitly
        # selected by the caller.
        from mk747_fable5_cognitive_core import evaluate_decision, write_json_atomic

        creative_projection_present = args.hermes_creative_projection is not None
        creative_projection: Any = None
        accepted_affinities: Any = None
        if args.accepted_option_affinities and not args.hermes_creative_projection:
            creative_projection = {"invalid_projection_binding": True}
        if args.hermes_creative_projection:
            creative_path = Path(args.hermes_creative_projection)
            if not creative_path.is_absolute():
                creative_path = (REPO / creative_path).resolve()
            try:
                creative_projection = load_json(creative_path)
            except (OSError, json.JSONDecodeError):
                creative_projection = {"invalid_projection_json": True}
        if args.accepted_option_affinities:
            affinity_path = Path(args.accepted_option_affinities)
            if not affinity_path.is_absolute():
                affinity_path = (REPO / affinity_path).resolve()
            try:
                accepted_affinities = load_json(affinity_path)
            except (OSError, json.JSONDecodeError):
                accepted_affinities = None
        evaluation_kwargs: dict[str, Any] = {}
        if creative_projection_present or args.accepted_option_affinities:
            evaluation_kwargs["hermes_creative_projection"] = creative_projection
        if args.accepted_option_affinities:
            evaluation_kwargs["accepted_option_affinities"] = accepted_affinities
        receipt = evaluate_decision(record, **evaluation_kwargs)
        if args.output:
            target = Path(args.output)
            target = target if target.is_absolute() else (REPO / target).resolve()
            write_json_atomic(target, receipt)
        if args.json:
            print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(receipt["status"])
            selected = receipt.get("recommendation", {}).get("selected_option_id")
            if selected:
                print(selected)
            for blocker in receipt.get("blocks", []):
                print(blocker)
        return 0 if receipt["status"] in {
            "PASS_SHADOW_RECOMMENDATION",
            "PASS_SHADOW_NO_ADMISSIBLE_DISCRIMINATION",
        } else 1

    # A preflight record is an object contract.  Do this before creating a
    # surrogate body or touching nested fields: arrays/scalars/null used to
    # accumulate unrelated legacy blockers (and callers could turn malformed
    # input into a validator-error path).  A top-level type violation has one
    # deterministic MK733J contract result instead.
    if not isinstance(record, dict):
        blocks = ["BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID"]
        deterministic_result = {
            "validator": "mk_decision_preflight",
            "status": "FAIL_PREFLIGHT_BLOCKED",
            "record_digest": record_digest(record),
        }
        result = {
            "tool": "mk_decision_preflight",
            "record": str(record_path),
            "blocks": blocks,
            "status": "FAIL_PREFLIGHT_BLOCKED",
            "deterministic_result": deterministic_result,
            "artifact_digest": record_digest(record),
            "non_claims": [
                "preflight_pass_is_not_selection_approval",
                "no_product_progress_from_preflight",
                "no_authority_change_from_preflight",
            ],
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(result["status"])
            print(blocks[0])
        return 1

    body = dict(record)
    body.pop("deterministic_result", None)
    fallback_path = None
    if args.authoritative_ledger:
        fallback_path = Path(args.authoritative_ledger)
        if not fallback_path.is_absolute():
            fallback_path = (REPO / fallback_path).resolve()
    authoritative_ledger = load_authoritative_task_ledger(fallback_path)
    raw_blocks = check_bound_work_selection_record(body, authoritative_ledger)
    fable5_authority_selection = (
        fable5_execution_authority_selection(
            body.get("fable5_execution_authorization"),
            required=True,
        )
        if fable5_execution_authority_required(body)
        else None
    )
    blocks, planning_selection, planning_continuation = consume_planning_order_selection(
        body,
        raw_blocks,
    )
    reorder_consumed = (
        not blocks
        and isinstance(planning_selection, dict)
        and planning_selection.get("decision") == "REORDER_PRIMARY_PLANNING_FIRST"
        and planning_continuation is not None
    )
    status = (
        "PASS_PREFLIGHT_REORDER_SELECTION_CONSUMED"
        if reorder_consumed
        else "PASS_PREFLIGHT_SUPPORT_EVIDENCE_ONLY"
        if not blocks
        else "FAIL_PREFLIGHT_BLOCKED"
    )
    deterministic_result = {
        "validator": "mk_decision_preflight",
        "status": status,
        "record_digest": record_digest(body),
        "decision": (
            planning_selection.get("decision")
            if isinstance(planning_selection, dict)
            else "NO_PLANNING_ORDER_SELECTION"
        ),
    }
    bound_record = {**body, "deterministic_result": deterministic_result}
    if planning_selection is not None:
        bound_record["planning_order_selection"] = planning_selection
    if planning_continuation is not None:
        bound_record["planning_order_continuation"] = planning_continuation
    if fable5_authority_selection is not None:
        bound_record["fable5_execution_authority_selection"] = fable5_authority_selection
        if fable5_authority_selection.get("approval_transition") is not None:
            bound_record["fable5_execution_authority_continuation"] = (
                fable5_authority_selection["approval_transition"]
            )
    result = {
        "tool": "mk_decision_preflight",
        "record": str(record_path),
        "blocks": blocks,
        "status": status,
        "deterministic_result": deterministic_result,
        "artifact_digest": record_digest(bound_record),
        "planning_order_selection": planning_selection,
        "planning_order_continuation": planning_continuation,
        "fable5_execution_authority_selection": fable5_authority_selection,
        "fable5_execution_authority_continuation": (
            fable5_authority_selection.get("approval_transition")
            if isinstance(fable5_authority_selection, dict)
            else None
        ),
        "original_dispatch_allowed": not reorder_consumed and not blocks,
        "authoritative_ledger_source": authoritative_ledger.get("source"),
        "authoritative_ledger_path": authoritative_ledger.get("path"),
        "read_only_planning_continues": (
            planning_selection.get("read_only_planning_continues", True)
            if isinstance(planning_selection, dict)
            else True
        ),
        "non_claims": [
            "preflight_pass_is_not_selection_approval",
            "no_product_progress_from_preflight",
            "no_authority_change_from_preflight",
        ],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(result["status"])
        for block in blocks:
            print(block)
    if args.output and not blocks:
        target=Path(args.output);target=target if target.is_absolute() else (REPO/target).resolve();target.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(dir=target.parent,prefix=".mk733j-preflight-")
        with os.fdopen(fd,"w",encoding="utf-8") as handle:json.dump(bound_record,handle,indent=2,sort_keys=True);handle.write("\n")
        os.replace(name,target)
    return 0 if not blocks else 1


if __name__ == "__main__":
    sys.exit(main())
