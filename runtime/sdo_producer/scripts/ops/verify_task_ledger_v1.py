#!/usr/bin/env python3
"""Verify TASK_LEDGER v1 structural and cross-row invariants.

The repository intentionally has no runtime jsonschema dependency. This
validator checks the schema surface plus the invariants that JSON Schema cannot
express across append-only rows.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA = "schemas/maestro-kernel/task_ledger.v1.schema.json"
POSITIVE = "fixtures/maestro-kernel/task-ledger/positive_next_session_kickoff_from_ledger.json"
ROOT_REQUIRED = {
    "schema_version",
    "ledger_id",
    "mission_id",
    "authority",
    "grand_goal_ref",
    "requirement_refs",
    "requirement_ids",
    "created_at",
    "updated_at",
    "state_revision",
    "current_state",
    "current_task_ref",
    "next_action",
    "entries",
    "returns",
    "audits",
    "rounds",
    "kpi",
    "witness_refs",
    "kickoff",
}
ROOT_ALLOWED = ROOT_REQUIRED | {"selection_record"}
SELECTION_RECORD_REQUIRED = {
    "candidate_task_ref",
    "work_class",
    "selected_route",
    "decision_changing_record",
    "ledger_state",
}
DECISION_CHANGING_REQUIRED = {
    "primary_user_goal_advanced",
    "specific_claim_or_asset",
    "minimum_confirmation",
    "next_action_changed",
    "safe_work_blocked_without_it",
}
LEDGER_STATE_REQUIRED = {"schema_version", "active_support_lane_refs"}
INPUT_CLASSES = {"confirmed", "witness", "contradiction", "unknown", "inference"}
WRITER_READBACK_CLASSES = {"witness", "contradiction", "unknown"}
MISSION_STATES = {
    "planned",
    "dispatched",
    "executing",
    "awaiting_return",
    "awaiting_audit",
    "remediation",
    "ready_for_cmd_consumption",
    "blocked",
    "closed",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_schema_surface(schema: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema_not_object"]
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("schema_draft_mismatch")
    if set(schema.get("required", [])) != ROOT_REQUIRED:
        errors.append("schema_root_required_fields_mismatch")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not ROOT_ALLOWED.issubset(properties):
        errors.append("schema_root_properties_incomplete")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        errors.append("schema_defs_missing")
    else:
        for name in ("selection_record", "classified_input", "entry", "return", "audit", "witness"):
            if name not in definitions:
                errors.append(f"schema_def_missing:{name}")
        selection = definitions.get("selection_record", {})
        decision = selection.get("properties", {}).get("decision_changing_record", {})
        ledger_state = selection.get("properties", {}).get("ledger_state", {})
        if set(selection.get("required", [])) != SELECTION_RECORD_REQUIRED:
            errors.append("schema_selection_record_required_fields_mismatch")
        if set(decision.get("required", [])) != DECISION_CHANGING_REQUIRED:
            errors.append("schema_decision_changing_required_fields_mismatch")
        if set(ledger_state.get("required", [])) != LEDGER_STATE_REQUIRED:
            errors.append("schema_selection_ledger_state_required_fields_mismatch")
    claim_enum = (
        definitions.get("classified_input", {})
        .get("properties", {})
        .get("input_class", {})
        .get("enum", [])
        if isinstance(definitions, dict)
        else []
    )
    if set(claim_enum) != INPUT_CLASSES:
        errors.append("schema_input_class_enum_mismatch")
    rounds = properties.get("rounds", {}).get("properties", {}) if isinstance(properties, dict) else {}
    for name in ("remediation", "recheck"):
        if rounds.get(name, {}).get("maximum") != 1:
            errors.append(f"schema_round_limit_missing:{name}")
    return errors


def check_classified_input(row: Any, label: str, errors: list[str]) -> None:
    if not isinstance(row, dict):
        errors.append(f"{label}:not_object")
        return
    input_class = row.get("input_class")
    writer_dependency = row.get("writer_dependency")
    if input_class not in INPUT_CLASSES:
        errors.append(f"{label}:invalid_input_class")
    if not isinstance(writer_dependency, bool):
        errors.append(f"{label}:writer_dependency_not_boolean")
    if "remeasurement_task_ref" not in row:
        errors.append(f"{label}:remeasurement_task_ref_missing")
    if "completed_readback_ref" not in row:
        errors.append(f"{label}:completed_readback_ref_missing")
    if (
        writer_dependency is True
        and input_class in WRITER_READBACK_CLASSES
        and not nonempty(row.get("completed_readback_ref"))
    ):
        errors.append(f"{label}:writer_dependent_input_missing_completed_readback")


def check_selection_record(row: Any, errors: list[str]) -> None:
    label = "selection_record"
    if not isinstance(row, dict):
        errors.append(f"{label}:not_object")
        return
    if set(row) != SELECTION_RECORD_REQUIRED:
        errors.append(f"{label}:fields_invalid")
    if not nonempty(row.get("candidate_task_ref")):
        errors.append(f"{label}:candidate_task_ref_invalid")
    if row.get("work_class") not in {"primary", "support"}:
        errors.append(f"{label}:work_class_invalid")
    if row.get("selected_route") not in {"critical_path", "support_lane", "deferred"}:
        errors.append(f"{label}:selected_route_invalid")

    decision = row.get("decision_changing_record")
    if not isinstance(decision, dict):
        errors.append(f"{label}:decision_changing_record_not_object")
    else:
        if set(decision) != DECISION_CHANGING_REQUIRED:
            errors.append(f"{label}:decision_changing_record_fields_invalid")
        for field in (
            "primary_user_goal_advanced",
            "next_action_changed",
            "safe_work_blocked_without_it",
        ):
            if not isinstance(decision.get(field), bool):
                errors.append(f"{label}:{field}_not_boolean")
        for field in ("specific_claim_or_asset", "minimum_confirmation"):
            if not nonempty(decision.get(field)):
                errors.append(f"{label}:{field}_invalid")

    ledger_state = row.get("ledger_state")
    if not isinstance(ledger_state, dict):
        errors.append(f"{label}:ledger_state_not_object")
        return
    if set(ledger_state) != LEDGER_STATE_REQUIRED:
        errors.append(f"{label}:ledger_state_fields_invalid")
    if ledger_state.get("schema_version") != "task_ledger.v1":
        errors.append(f"{label}:ledger_state_schema_version_invalid")
    active_refs = ledger_state.get("active_support_lane_refs")
    active_refs_valid = (
        isinstance(active_refs, list)
        and len(active_refs) <= 1
        and all(nonempty(ref) for ref in active_refs)
        and len(active_refs) == len(set(active_refs))
    )
    if not active_refs_valid:
        errors.append(f"{label}:active_support_lane_refs_invalid")


def validate_ledger(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["ledger_not_object"]
    missing = sorted(ROOT_REQUIRED - set(payload))
    if missing:
        errors.append(f"root_required_missing:{','.join(missing)}")
    extra = sorted(set(payload) - ROOT_ALLOWED)
    if extra:
        errors.append(f"root_additional_properties:{','.join(extra)}")
    if payload.get("schema_version") != "task_ledger.v1":
        errors.append("schema_version_mismatch")
    if "selection_record" in payload:
        check_selection_record(payload.get("selection_record"), errors)
    for field in ("ledger_id", "mission_id", "grand_goal_ref", "current_task_ref", "next_action"):
        if not nonempty(payload.get(field)):
            errors.append(f"{field}_missing")

    authority = payload.get("authority")
    if not isinstance(authority, dict):
        errors.append("authority_not_object")
    elif authority != {
        "authoritative_identity": True,
        "writer_role": "CMD",
        "state_source": "TASK_LEDGER",
        "witness_state_mutation_allowed": False,
    }:
        errors.append("single_authoritative_ledger_identity_invalid")

    requirements = payload.get("requirement_refs")
    if not isinstance(requirements, list) or not requirements or any(not nonempty(item) for item in requirements):
        errors.append("requirement_refs_invalid")
    elif len(requirements) != len(set(requirements)):
        errors.append("requirement_refs_not_unique")
    requirement_ids = payload.get("requirement_ids")
    if (
        not isinstance(requirement_ids, list)
        or not requirement_ids
        or len(requirement_ids) != len(set(requirement_ids))
        or any(
            not isinstance(item, str)
            or len(item) != 6
            or not item.startswith("RQ-")
            or not item[3:].isdigit()
            for item in requirement_ids
        )
    ):
        errors.append("requirement_ids_invalid")

    current_state = payload.get("current_state")
    if current_state not in MISSION_STATES:
        errors.append("current_state_invalid")

    returns = payload.get("returns")
    consumed_returns: dict[str, dict[str, Any]] = {}
    if not isinstance(returns, list):
        errors.append("returns_not_array")
        returns = []
    return_ids: set[str] = set()
    for index, row in enumerate(returns):
        label = f"returns[{index}]"
        check_classified_input(row, label, errors)
        if not isinstance(row, dict):
            continue
        return_id = row.get("return_id")
        if not nonempty(return_id):
            errors.append(f"{label}:return_id_missing")
        elif return_id in return_ids:
            errors.append(f"{label}:duplicate_return_id")
        else:
            return_ids.add(return_id)
        if row.get("producer_role") not in {"WORKER", "AUDIT"}:
            errors.append(f"{label}:producer_role_invalid")
        if row.get("state_advance") not in {"no_change", "advance"}:
            errors.append(f"{label}:state_advance_invalid")
        if row.get("state_advance") == "advance":
            if row.get("consumed_by_cmd") is not True or not nonempty(row.get("cmd_consumption_ref")):
                errors.append(f"{label}:unconsumed_return_advances_state")
        if row.get("consumed_by_cmd") is True and nonempty(return_id):
            if not nonempty(row.get("cmd_consumption_ref")):
                errors.append(f"{label}:consumed_return_missing_cmd_ref")
            consumed_returns[return_id] = row

    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("entries_not_nonempty_array")
        entries = []
    entry_ids: set[str] = set()
    previous_state: str | None = None
    state_changes = 0
    for index, row in enumerate(entries):
        label = f"entries[{index}]"
        check_classified_input(row, label, errors)
        if not isinstance(row, dict):
            continue
        entry_id = row.get("entry_id")
        if not nonempty(entry_id):
            errors.append(f"{label}:entry_id_missing")
        elif entry_id in entry_ids:
            errors.append(f"{label}:duplicate_entry_id")
        if row.get("sequence") != index:
            errors.append(f"{label}:sequence_not_append_only")
        before = row.get("state_before")
        after = row.get("state_after")
        if before not in MISSION_STATES or after not in MISSION_STATES:
            errors.append(f"{label}:state_invalid")
        if previous_state is not None and before != previous_state:
            errors.append(f"{label}:state_chain_broken")
        previous_state = after if after in MISSION_STATES else previous_state
        state_change = row.get("state_change")
        if not isinstance(state_change, bool):
            errors.append(f"{label}:state_change_not_boolean")
        elif state_change:
            state_changes += 1
            return_ref = row.get("return_ref")
            consumed = consumed_returns.get(return_ref)
            if (
                row.get("actor_role") != "CMD"
                or row.get("event_type") not in {"return_consumed", "state_advanced", "closeout"}
                or not isinstance(consumed, dict)
                or consumed.get("state_advance") != "advance"
                or row.get("cmd_consumption_ref") != consumed.get("cmd_consumption_ref")
            ):
                errors.append(f"{label}:state_advanced_without_cmd_consumed_return")
        if row.get("event_type") == "witness_recorded":
            if state_change is not False or not nonempty(row.get("witness_ref")):
                errors.append(f"{label}:witness_attempts_state_mutation")
        if row.get("event_type") == "correction":
            supersedes = row.get("supersedes_ref")
            if not nonempty(supersedes) or supersedes not in entry_ids:
                errors.append(f"{label}:correction_not_forward_append_only")
        if nonempty(entry_id):
            entry_ids.add(entry_id)

    if entries and previous_state != current_state:
        errors.append("current_state_does_not_match_last_entry")
    if payload.get("state_revision") != state_changes:
        errors.append("state_revision_mismatch")

    witnesses = payload.get("witness_refs")
    if not isinstance(witnesses, list):
        errors.append("witness_refs_not_array")
    else:
        witness_ids: set[str] = set()
        for index, row in enumerate(witnesses):
            label = f"witness_refs[{index}]"
            if not isinstance(row, dict):
                errors.append(f"{label}:not_object")
                continue
            witness_id = row.get("witness_id")
            if not nonempty(witness_id) or witness_id in witness_ids:
                errors.append(f"{label}:witness_identity_invalid")
            else:
                witness_ids.add(witness_id)
            if row.get("kind") not in {"handoff", "pms", "obsidian", "other"}:
                errors.append(f"{label}:kind_invalid")
            if row.get("can_advance_state") is not False:
                errors.append(f"{label}:witness_can_advance_state")

    audits = payload.get("audits")
    if not isinstance(audits, list):
        errors.append("audits_not_array")
        audits = []
    max_remediation = 0
    max_recheck = 0
    for index, row in enumerate(audits):
        label = f"audits[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label}:not_object")
            continue
        decision = row.get("audit_decision")
        if decision not in {"required", "not_required_with_reason"}:
            errors.append(f"{label}:audit_decision_invalid")
        if decision == "not_required_with_reason" and not nonempty(row.get("not_required_reason")):
            errors.append(f"{label}:not_required_reason_missing")
        remediation = row.get("remediation_rounds_used")
        recheck = row.get("recheck_rounds_used")
        if not isinstance(remediation, int) or isinstance(remediation, bool) or not 0 <= remediation <= 1:
            errors.append(f"{label}:remediation_round_limit_exceeded")
        else:
            max_remediation = max(max_remediation, remediation)
        if not isinstance(recheck, int) or isinstance(recheck, bool) or not 0 <= recheck <= 1:
            errors.append(f"{label}:recheck_round_limit_exceeded")
        else:
            max_recheck = max(max_recheck, recheck)
        for field in ("task_signature", "audit_signature", "replan_signature"):
            if not nonempty(row.get(field)):
                errors.append(f"{label}:{field}_missing")
        if (remediation == 1 or recheck == 1) and row.get("replan_signature") in {
            row.get("task_signature"),
            row.get("audit_signature"),
        }:
            errors.append(f"{label}:post_limit_replan_signature_unchanged")

    rounds = payload.get("rounds")
    if not isinstance(rounds, dict):
        errors.append("rounds_not_object")
    else:
        if rounds.get("remediation") != max_remediation:
            errors.append("rounds_remediation_mismatch")
        if rounds.get("recheck") != max_recheck:
            errors.append("rounds_recheck_mismatch")
        for field in ("remediation", "recheck"):
            value = rounds.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1:
                errors.append(f"rounds_{field}_limit_exceeded")

    kpi = payload.get("kpi")
    if not isinstance(kpi, dict) or kpi.get("support_work_progress_credit") != 0:
        errors.append("support_work_progress_credit_nonzero")

    kickoff = payload.get("kickoff")
    if not isinstance(kickoff, dict):
        errors.append("kickoff_not_object")
    else:
        if kickoff.get("source") != "TASK_LEDGER_ONLY":
            errors.append("kickoff_uses_nonledger_source")
        if kickoff.get("next_action") != payload.get("next_action"):
            errors.append("kickoff_next_action_mismatch")
        for field in ("current_priority", "next_action", "next_owner"):
            if not nonempty(kickoff.get(field)):
                errors.append(f"kickoff_{field}_missing")
        for field in ("current_blockers", "active_corrections"):
            if not isinstance(kickoff.get(field), list):
                errors.append(f"kickoff_{field}_not_array")

    return errors


def self_test(positive: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cases: list[tuple[str, dict[str, Any], str]] = []

    if "selection_record" not in positive:
        errors.append("self_test_selection_record_fixture_missing")
        return errors

    malformed_selection = copy.deepcopy(positive)
    malformed_selection["selection_record"]["decision_changing_record"].pop("minimum_confirmation")
    cases.append(
        (
            "malformed_selection_record",
            malformed_selection,
            "selection_record:decision_changing_record_fields_invalid",
        )
    )

    missing_readback = copy.deepcopy(positive)
    missing_readback["returns"][0]["completed_readback_ref"] = None
    cases.append(("writer_readback", missing_readback, "writer_dependent_input_missing_completed_readback"))

    unconsumed_advance = copy.deepcopy(positive)
    unconsumed_advance["returns"][0]["consumed_by_cmd"] = False
    unconsumed_advance["returns"][0]["cmd_consumption_ref"] = None
    cases.append(("cmd_consumption", unconsumed_advance, "unconsumed_return_advances_state"))

    witness_mutation = copy.deepcopy(positive)
    witness_mutation["witness_refs"][0]["can_advance_state"] = True
    cases.append(("witness_state", witness_mutation, "witness_can_advance_state"))

    extra_round = copy.deepcopy(positive)
    extra_round["audits"][0]["remediation_rounds_used"] = 2
    extra_round["rounds"]["remediation"] = 2
    cases.append(("round_limit", extra_round, "remediation_round_limit_exceeded"))

    unchanged_replan = copy.deepcopy(positive)
    unchanged_replan["audits"][0]["replan_signature"] = unchanged_replan["audits"][0]["task_signature"]
    cases.append(("replan_signature", unchanged_replan, "post_limit_replan_signature_unchanged"))

    witness_state_entry = copy.deepcopy(positive)
    witness_state_entry["entries"][0]["event_type"] = "witness_recorded"
    witness_state_entry["entries"][0]["witness_ref"] = "wit-1"
    witness_state_entry["entries"][0]["state_change"] = True
    cases.append(("witness_entry", witness_state_entry, "witness_attempts_state_mutation"))

    correction_overwrite = copy.deepcopy(positive)
    correction_overwrite["entries"][3]["supersedes_ref"] = None
    cases.append(("forward_correction", correction_overwrite, "correction_not_forward_append_only"))

    for name, candidate, expected in cases:
        observed = validate_ledger(candidate)
        if not any(expected in item for item in observed):
            errors.append(f"self_test_{name}_did_not_fail_as_expected:{observed}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--fixture", default=POSITIVE)
    parser.add_argument("--expect-fail", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base = Path(args.base_dir).resolve()
    schema_errors = check_schema_surface(load_json(base / SCHEMA))
    payload = load_json(base / args.fixture)
    ledger_errors = validate_ledger(payload)
    negative_observed = bool(ledger_errors)
    expectation_ok = negative_observed if args.expect_fail else not negative_observed
    self_test_errors = self_test(payload) if args.self_test and not ledger_errors else []
    errors = schema_errors + ([] if expectation_ok else ledger_errors or ["expected_failure_but_passed"]) + self_test_errors
    result = {
        "ok": not errors,
        "verdict": "PASS_TASK_LEDGER_V1_CONTRACT" if not errors else "FAIL_TASK_LEDGER_V1_CONTRACT",
        "fixture": args.fixture,
        "expect_fail": args.expect_fail,
        "schema_errors": schema_errors,
        "ledger_errors": ledger_errors,
        "self_test_errors": self_test_errors,
        "controls": {
            "single_authoritative_ledger_identity": True,
            "cmd_consumed_returns_only_advance_state": True,
            "witness_refs_cannot_advance_state": True,
            "writer_dependent_uncertain_input_requires_completed_readback": True,
            "append_only_forward_correction": True,
            "audit_rounds_bounded_to_one": True,
            "identical_post_limit_replan_rejected": True,
            "ledger_only_kickoff_reconstruction": True,
            "selection_record_extension_validated": True,
        },
        "non_claims": [
            "schema_or_fixture_pass_is_not_runtime_operation",
            "schema_or_fixture_pass_is_not_independent_audit",
            "no_final_user_acceptance",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["verdict"])
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
