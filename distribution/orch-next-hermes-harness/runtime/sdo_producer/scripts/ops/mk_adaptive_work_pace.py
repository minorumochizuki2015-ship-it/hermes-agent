#!/usr/bin/env python3
"""Validate the bounded Adaptive Work Pace/Replan claim-check contract.

This is deliberately not a scheduler or an Authority Gate. It describes when a
non-trivial, routed decision must reconsider its current strategy. Read-only,
small reversible, and normal supervised local work remain exempt.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "adaptive_work_pace_replan.v1"
EXEMPT_WORK_CLASSES = {
    "read_only_exploration",
    "small_reversible_local",
    "normal_local_bounded_supervised",
}
PACED_WORK_CLASSES = {
    "delegated_nontrivial",
    "cross_repo_phase",
    "external_wait",
}
REPLAN_DECISIONS = {
    "parallelize_diagnosis",
    "pivot",
    "rollback",
    "report_exact_blocker",
}
PANORAMIC_OPTIONS = {
    "continue_with_revised_estimate",
    "parallelize_diagnosis",
    "pivot",
    "rollback",
    "report_exact_blocker",
}
SCARCE_MUTATION_NEXT_ACTIONS = {
    "run_non_consuming_diagnostic",
    "report_exact_external_blocker",
    "retry_scarce_mutation",
}
SERIAL_STALL_VERSION = "inc_a30_serial_stall_parallel_response.v1"
SERIAL_STALL_DECISIONS = {
    "CONTINUE_ACTIVE_OWNER_NO_PARALLEL_TRIGGER",
    "DISPATCH_BOUNDED_PARALLEL_DIAGNOSIS_OR_REHEARSAL",
    "EMIT_ACTIONABLE_PARALLEL_NONFIRE",
    "INVALID_SERIAL_STALL_STATE",
}
SERIAL_STALL_STATE_FIELDS = {
    "schema_version", "estimate_ms", "elapsed_ms", "user_visible_capability_delta",
    "serial_cause_changing_cycles", "implementation_owner_active",
    "one_writer_owner_bound", "parallel_candidate", "prior_control_firing",
    "authority_cardinality_review",
}
SERIAL_STALL_OPTIONAL_STATE_FIELDS = {"authority_contract_profile"}
AUTHORITY_CONTRACT_PROFILE_GENERIC = "generic_cross_layer"
AUTHORITY_CONTRACT_PROFILE_A30_BOOTSTRAP_OIDC = "a30_bootstrap_oidc"
AUTHORITY_CONTRACT_PROFILES = {
    AUTHORITY_CONTRACT_PROFILE_GENERIC,
    AUTHORITY_CONTRACT_PROFILE_A30_BOOTSTRAP_OIDC,
}
SERIAL_STALL_CANDIDATE_FIELDS = {
    "useful", "kind", "active_lane_count", "write_access", "write_set_overlap",
    "target_binding", "context_packet_ref", "required_checks", "return_schema", "budget",
}
SERIAL_STALL_BUDGET_FIELDS = {
    "spawn_depth", "max_turns", "max_tool_calls", "max_runtime_seconds",
    "max_files_to_touch", "readback_required", "closeout_required",
}
SERIAL_STALL_FIRING_FIELDS = {
    "source_state", "plugin_distribution_state", "installed_cache_state",
    "selected", "invoked", "result_integrated", "observed_effective",
}
SERIAL_STALL_RETURN_FIELDS = {
    "agent_role", "work_performed", "findings", "changed_files_or_none",
    "blockers", "recommended_next_action", "non_claims",
}
SERIAL_STALL_LANE_KINDS = {"none", "read_only_diagnosis", "post_stage_rehearsal"}
SERIAL_STALL_CACHE_STATES = {"unknown", "absent", "present", "stale"}
NORMAL_RUNTIME_REPLACEMENT_WORK_PC_PROJECTION_PASS_LABEL = (
    "normal_runtime_replacement_work_pc_projection_pass"
)
AUTHORITY_CARDINALITY_FIELDS = {
    "applicable", "cross_layer_applicable", "authority_surfaces", "proposed_provenance_fields",
    "histories_share_merge_base", "review_completed", "mutation_started",
    "contract_path", "cross_layer_rehearsal_completed", "physical_cta_started",
    "producer_adapter_tests_passed", "exact_head_ci_green",
    "final_executable_consumer_bound", "superseded_invariants_retained",
    "rehearsal_execution",
}
AUTHORITY_SURFACE_FIELDS = {
    "authority", "owner", "required_provenance_field", "history_ref",
}
AUTHORITY_CARDINALITY_REQUIRED_CHECKS = {
    "authority_ownership_cardinality",
    "provenance_identity_noncollapse",
}
CROSS_LAYER_CONTRACT_REQUIRED_CHECK = "producer_dispatcher_broker_workflow_contract_preservation"
FINAL_CONSUMER_REQUIRED_CHECK = "final_executable_consumer_contract_preservation"
REHEARSAL_EXECUTION_FIELDS = {
    "applicable", "runtime_implementation_kind", "target_runtime_transition",
    "transition_event_source", "transition_executed",
    "required_producer_operations", "executed_producer_operations",
    "producer_result_source", "claimed_final_capability",
    "claimed_final_capability_observed",
}
CONSUMER_BINDING_FIELDS = {
    "consumer_chain",
    "earliest_rejecting_consumer",
    "final_compared_field_pair",
    "provenance_source",
}
CONSUMER_CHAIN_FIELDS = {
    "stage", "authority", "consumer", "required_provenance_field",
}
FINAL_COMPARED_FIELD_PAIR_FIELDS = {
    "expected_field", "observed_field", "expected_value", "observed_value", "equal",
}
REQUIRED_CONSUMER_STAGES = (
    "producer", "broker", "dispatcher", "runner", "final_executable_consumer",
)
A30_BOOTSTRAP_OIDC_AUTHORITY_MAP = {
    "runtime_source": ("runtime_source_owner", "runtime_source_sha"),
    "public_published": ("public_release_owner", "public_published_sha"),
    "broker_bundle": ("broker_bundle_owner", "broker_bundle_sha"),
    "ai_workload": ("ai_workload_owner", "ai_workload_sha"),
}
A30_BOOTSTRAP_OIDC_CONSUMER_CHAIN = (
    ("producer", "runtime_source", "runtime_producer", "runtime_source_sha"),
    ("broker", "broker_bundle", "ai_broker_oidc_final_consumer", "broker_bundle_sha"),
    ("dispatcher", "ai_workload", "remote_dispatcher", "ai_workload_sha"),
    ("runner", "ai_workload", "workflow_oidc_runner", "workflow_trigger_sha"),
    (
        "final_executable_consumer",
        "ai_workload",
        "bootstrap_workload_final_consumer",
        "bootstrap_trigger_sha",
    ),
)
A30_BOOTSTRAP_OIDC_FINAL_PAIR = (
    "workflow_trigger_sha", "bootstrap_trigger_sha",
)
A30_BOOTSTRAP_OIDC_EARLIEST_REJECTING_CONSUMER = (
    "ai_broker_oidc_final_consumer"
)
RUNTIME_IMPLEMENTATION_KINDS = {
    "not_applicable", "target_runtime", "fixture_runtime", "mock_runtime",
    "substitute_runtime",
}
EXECUTION_EVENT_SOURCES = {
    "not_applicable", "not_observed", "actual_target_runtime",
    "injected_fixture", "mock", "hardcoded",
}
SUBSTITUTED_RUNTIME_KINDS = {
    "fixture_runtime", "mock_runtime", "substitute_runtime",
}
SUBSTITUTED_EXECUTION_SOURCES = {"injected_fixture", "mock", "hardcoded"}


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


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


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_contract(value: Any, *, now: datetime | None = None, enforce_timing: bool = False) -> list[str]:
    """Return deterministic blockers for one pace/replan contract.

    ``enforce_timing`` is used by a heartbeat/checkpoint. Pre-dispatch
    validation only checks structure so a newly created decision is not judged
    against a clock before work begins.
    """
    blocks: list[str] = []
    if not isinstance(value, dict):
        return ["BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_CONTRACT_MISSING"]
    if value.get("contract_version") != CONTRACT_VERSION:
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_VERSION_INVALID")
    work_class = value.get("work_class")
    if work_class in EXEMPT_WORK_CLASSES:
        if set(value) != {"contract_version", "work_class", "exempt_reason"} or _empty(value.get("exempt_reason")):
            blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_EXEMPTION_INVALID")
        return sorted(set(blocks))
    if work_class not in PACED_WORK_CLASSES:
        return sorted(set(blocks + ["BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_SCOPE_INVALID"]))

    required = {
        "contract_version",
        "work_class",
        "started_at",
        "checkpoint_seconds",
        "expected_first_meaningful_delta_seconds",
        "expected_completion_max_seconds",
        "same_strategy_attempt_count",
        "max_same_strategy_attempts",
        "no_delta_checkpoint_count",
        "blocker_delta",
        "checkpoint_review",
        "replan_decision",
        "external_wait",
    }
    optional = {"scarce_mutation_admission"}
    if not required <= set(value) or not set(value) <= required | optional:
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_SCHEMA_INVALID")
        return sorted(set(blocks))

    started_at = _parse_time(value.get("started_at"))
    if started_at is None:
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_SCHEMA_INVALID")
    if not all(_positive_int(value.get(field)) for field in (
        "checkpoint_seconds",
        "expected_first_meaningful_delta_seconds",
        "expected_completion_max_seconds",
        "max_same_strategy_attempts",
    )):
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_BUDGET_INVALID")
    elif not (
        value["expected_first_meaningful_delta_seconds"] <= value["checkpoint_seconds"]
        <= value["expected_completion_max_seconds"]
    ):
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_BUDGET_INVALID")
    elif value["checkpoint_seconds"] > min(
        value["expected_completion_max_seconds"] * 0.25,
        15 * 60,
    ):
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_BUDGET_INVALID")
    if value.get("max_same_strategy_attempts") != 2:
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_REPLAN_LIMIT_INVALID")

    for field in ("same_strategy_attempt_count", "no_delta_checkpoint_count"):
        if not isinstance(value.get(field), int) or isinstance(value.get(field), bool) or value[field] < 0:
            blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_SCHEMA_INVALID")
    if value.get("blocker_delta") not in {"positive", "zero", "unknown"}:
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_SCHEMA_INVALID")

    no_delta_count = value.get("no_delta_checkpoint_count", -1)
    review = value.get("checkpoint_review")
    if no_delta_count == 0:
        if review is not None:
            blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_CHECKPOINT_REVIEW_INVALID")
    elif not isinstance(review, dict) or set(review) != {
        "observed_at", "panoramic_options", "selected_action", "reason"
    }:
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_CHECKPOINT_REVIEW_INVALID")
    elif (
        _parse_time(review.get("observed_at")) is None
        or not isinstance(review.get("panoramic_options"), list)
        or not PANORAMIC_OPTIONS <= set(review["panoramic_options"])
        or review.get("selected_action") not in PANORAMIC_OPTIONS
        or _empty(review.get("reason"))
    ):
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_CHECKPOINT_REVIEW_INVALID")

    productive_delta = value.get("blocker_delta") == "positive"
    replan_required = (
        not productive_delta
        and isinstance(value.get("same_strategy_attempt_count"), int)
        and isinstance(value.get("max_same_strategy_attempts"), int)
        and value["same_strategy_attempt_count"] >= value["max_same_strategy_attempts"]
    ) or (isinstance(no_delta_count, int) and no_delta_count >= 2)
    replan = value.get("replan_decision")
    if replan_required:
        if not isinstance(replan, dict) or set(replan) != {
            "decision", "reason", "strategy_changed", "next_action"
        }:
            blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_REPLAN_REQUIRED")
        elif (
            replan.get("decision") not in REPLAN_DECISIONS
            or replan.get("strategy_changed") is not True
            or _empty(replan.get("reason"))
            or _empty(replan.get("next_action"))
        ):
            blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_REPLAN_REQUIRED")
    elif replan is not None:
        blocks.append("BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_REPLAN_REQUIRED")

    external_wait = value.get("external_wait")
    if work_class == "external_wait":
        if not isinstance(external_wait, dict) or set(external_wait) != {
            "automation_ref", "worker_polling_allowed", "next_action"
        }:
            blocks.append("BLOCKED_FOR_MK741_EXTERNAL_WAIT_AUTOMATION_REQUIRED")
        elif (
            not isinstance(external_wait.get("automation_ref"), str)
            or not external_wait["automation_ref"].strip()
            or external_wait.get("worker_polling_allowed") is not False
            or external_wait.get("next_action") != "await_external_with_automation"
        ):
            blocks.append("BLOCKED_FOR_MK741_EXTERNAL_WAIT_AUTOMATION_REQUIRED")
    elif external_wait is not None:
        blocks.append("BLOCKED_FOR_MK741_EXTERNAL_WAIT_AUTOMATION_REQUIRED")

    scarce = value.get("scarce_mutation_admission")
    actual_next_action = replan.get("next_action") if isinstance(replan, dict) else None
    scarce_action_selected = actual_next_action in SCARCE_MUTATION_NEXT_ACTIONS
    if scarce_action_selected and scarce is None:
        blocks.append("BLOCKED_FOR_MK741_SCARCE_MUTATION_ADMISSION_REQUIRED")
    if scarce is not None:
        required_scarce = {
            "protected_asset", "hazard", "owner", "trigger", "blocking_scope",
            "metric", "expiry_or_review", "prior_same_hypothesis_attempts",
            "latest_causal_code", "causal_specificity",
            "non_consuming_diagnostic_e2e", "remediation_validated",
            "fresh_authority_and_budget_bound", "rollback_bound",
            "requested_next_action",
        }
        if not isinstance(scarce, dict) or set(scarce) != required_scarce:
            blocks.append("BLOCKED_FOR_MK741_SCARCE_MUTATION_ADMISSION_SCHEMA_INVALID")
        else:
            required_text = {
                "protected_asset", "hazard", "owner", "trigger", "blocking_scope",
                "metric", "expiry_or_review", "latest_causal_code",
            }
            if (
                any(_empty(scarce.get(field)) for field in required_text)
                or not isinstance(scarce.get("prior_same_hypothesis_attempts"), int)
                or scarce.get("prior_same_hypothesis_attempts", -1) < 0
                or scarce.get("causal_specificity") not in {"generic", "specific_actionable"}
                or scarce.get("non_consuming_diagnostic_e2e") not in {"unproven", "passed_specific_actionable"}
                or scarce.get("requested_next_action") not in SCARCE_MUTATION_NEXT_ACTIONS
                or any(not isinstance(scarce.get(field), bool) for field in (
                    "remediation_validated", "fresh_authority_and_budget_bound", "rollback_bound"
                ))
            ):
                blocks.append("BLOCKED_FOR_MK741_SCARCE_MUTATION_ADMISSION_SCHEMA_INVALID")
            retry_eligible = scarce.get("requested_next_action") == "retry_scarce_mutation"
            retry_ready = (
                scarce.get("prior_same_hypothesis_attempts") == 1
                and scarce.get("causal_specificity") == "specific_actionable"
                and scarce.get("non_consuming_diagnostic_e2e") == "passed_specific_actionable"
                and scarce.get("remediation_validated") is True
                and scarce.get("fresh_authority_and_budget_bound") is True
                and scarce.get("rollback_bound") is True
            )
            if actual_next_action != scarce.get("requested_next_action"):
                blocks.append("BLOCKED_FOR_MK741_SCARCE_MUTATION_ACTION_BINDING_REQUIRED")
            if retry_eligible and not retry_ready:
                blocks.append("BLOCKED_FOR_MK741_SCARCE_MUTATION_DIAGNOSTIC_OR_CAUSAL_CLASS_REQUIRED")

    if enforce_timing and started_at is not None and not blocks:
        observed_at = now or datetime.now(timezone.utc)
        elapsed_seconds = (observed_at - started_at).total_seconds()
        if elapsed_seconds > value["checkpoint_seconds"] and no_delta_count == 0:
            blocks.append("BLOCKED_FOR_MK741_CHECKPOINT_NOT_RECORDED")
        if elapsed_seconds > value["expected_completion_max_seconds"] and not replan_required:
            blocks.append("BLOCKED_FOR_MK741_OVERRUN_REPLAN_REQUIRED")
    return sorted(set(blocks))


def _serial_stall_budget_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != SERIAL_STALL_BUDGET_FIELDS:
        return False
    bounded = (
        ("max_turns", 3),
        ("max_tool_calls", 20),
        ("max_runtime_seconds", 1200),
    )
    return (
        all(_positive_int(value.get(field)) and value[field] <= maximum for field, maximum in bounded)
        and _nonnegative_int(value.get("spawn_depth"))
        and value["spawn_depth"] <= 1
        and value.get("max_files_to_touch") == 0
        and not isinstance(value.get("max_files_to_touch"), bool)
        and value.get("readback_required") is True
        and value.get("closeout_required") is True
    )


def _serial_stall_prior_nonfires(value: dict[str, Any]) -> list[str]:
    if value.get("source_state") != "present":
        return []
    reasons: list[str] = []
    if value.get("plugin_distribution_state") != "present":
        reasons.append("SOURCE_PRESENT_BUT_NOT_DISTRIBUTED")
    if value.get("selected") is not True:
        reasons.append("SOURCE_PRESENT_BUT_NOT_SELECTED")
    if value.get("invoked") is not True:
        reasons.append("SOURCE_PRESENT_BUT_NOT_INVOKED")
    if value.get("result_integrated") is not True:
        reasons.append("SOURCE_RESULT_NOT_INTEGRATED")
    return reasons


def _serial_stall_firing_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != SERIAL_STALL_FIRING_FIELDS:
        return False
    if (
        value.get("source_state") not in {"present", "absent"}
        or value.get("plugin_distribution_state") not in {"present", "absent", "mismatch"}
        or value.get("installed_cache_state") not in SERIAL_STALL_CACHE_STATES
        or any(
            not isinstance(value.get(field), bool)
            for field in ("selected", "invoked", "result_integrated", "observed_effective")
        )
    ):
        return False
    if value["invoked"] and not value["selected"]:
        return False
    if value["result_integrated"] and not value["invoked"]:
        return False
    if value["selected"] and value["source_state"] != "present":
        return False
    if value["invoked"] and value["plugin_distribution_state"] != "present":
        return False
    if value["observed_effective"] and (
        not value["result_integrated"]
        or value["source_state"] != "present"
        or value["plugin_distribution_state"] != "present"
        or value["installed_cache_state"] != "present"
    ):
        return False
    return True


def _authority_cardinality_review_valid(
    value: Any, authority_contract_profile: str
) -> bool:
    optional = {"consumer_binding"}
    if (
        not isinstance(value, dict)
        or not AUTHORITY_CARDINALITY_FIELDS <= set(value)
        or set(value) - AUTHORITY_CARDINALITY_FIELDS - optional
    ):
        return False
    surfaces = value.get("authority_surfaces")
    proposed_fields = value.get("proposed_provenance_fields")
    contract_path = value.get("contract_path")
    retained_invariants = value.get("superseded_invariants_retained")
    rehearsal_execution = value.get("rehearsal_execution")
    strict_consumer_binding_required = (
        value.get("cross_layer_applicable") is True
        and (
            authority_contract_profile
            == AUTHORITY_CONTRACT_PROFILE_A30_BOOTSTRAP_OIDC
            or (isinstance(surfaces, list) and len(surfaces) >= 4)
        )
    )
    if (
        not isinstance(value.get("applicable"), bool)
        or not isinstance(value.get("cross_layer_applicable"), bool)
        or not isinstance(surfaces, list)
        or not isinstance(proposed_fields, list)
        or not isinstance(value.get("histories_share_merge_base"), bool)
        or not isinstance(value.get("review_completed"), bool)
        or not isinstance(value.get("mutation_started"), bool)
        or not isinstance(contract_path, list)
        or any(not _nonempty_text(stage) for stage in contract_path)
        or len(contract_path) != len(set(contract_path))
        or not isinstance(value.get("cross_layer_rehearsal_completed"), bool)
        or not isinstance(value.get("physical_cta_started"), bool)
        or not isinstance(value.get("producer_adapter_tests_passed"), bool)
        or not isinstance(value.get("exact_head_ci_green"), bool)
        or not isinstance(value.get("final_executable_consumer_bound"), bool)
        or not isinstance(retained_invariants, list)
        or any(not _nonempty_text(item) for item in retained_invariants)
        or len(retained_invariants) != len(set(retained_invariants))
        or any(not _nonempty_text(field) for field in proposed_fields)
        or len(proposed_fields) != len(set(proposed_fields))
        or not _rehearsal_execution_valid(
            rehearsal_execution, value.get("cross_layer_applicable")
        )
    ):
        return False
    for surface in surfaces:
        if (
            not isinstance(surface, dict)
            or set(surface) != AUTHORITY_SURFACE_FIELDS
            or any(
                not _nonempty_text(surface.get(field))
                for field in AUTHORITY_SURFACE_FIELDS
            )
        ):
            return False
    authority_ids = [surface["authority"] for surface in surfaces]
    required_fields = [surface["required_provenance_field"] for surface in surfaces]
    if len(authority_ids) != len(set(authority_ids)) or len(required_fields) != len(set(required_fields)):
        return False
    if (
        authority_contract_profile
        == AUTHORITY_CONTRACT_PROFILE_A30_BOOTSTRAP_OIDC
    ):
        actual_map = {
            surface["authority"]: (
                surface["owner"], surface["required_provenance_field"]
            )
            for surface in surfaces
        }
        if actual_map != A30_BOOTSTRAP_OIDC_AUTHORITY_MAP:
            return False
    if not _consumer_binding_valid(
        value.get("consumer_binding"),
        required=strict_consumer_binding_required,
        authority_surfaces=surfaces,
        proposed_provenance_fields=proposed_fields,
        authority_contract_profile=authority_contract_profile,
    ):
        return False
    authority_valid = (
        len(surfaces) >= 2 and bool(proposed_fields)
        if value["applicable"] else not surfaces and not proposed_fields
    )
    cross_layer_valid = (
        len(contract_path) >= 4 and contract_path[-1] == "final_executable_consumer"
        if value["cross_layer_applicable"] else not contract_path and not retained_invariants
    )
    return authority_valid and cross_layer_valid


def _consumer_binding_valid(
    value: Any,
    *,
    required: bool,
    authority_surfaces: Any = None,
    proposed_provenance_fields: Any = None,
    authority_contract_profile: str = AUTHORITY_CONTRACT_PROFILE_GENERIC,
) -> bool:
    """Require an ordered, non-substituted final-consumer binding for strict states."""
    if value is None:
        return not required
    if not isinstance(value, dict) or set(value) != CONSUMER_BINDING_FIELDS:
        return False
    chain = value.get("consumer_chain")
    pair = value.get("final_compared_field_pair")
    if (
        not isinstance(chain, list)
        or len(chain) < len(REQUIRED_CONSUMER_STAGES)
        or not isinstance(pair, dict)
        or set(pair) != FINAL_COMPARED_FIELD_PAIR_FIELDS
        or not isinstance(value.get("earliest_rejecting_consumer"), str)
        or value.get("provenance_source") not in {
            "actual_execution", "fixture", "mock", "hardcoded", "not_observed"
        }
    ):
        return False
    if any(
        not isinstance(row, dict)
        or set(row) != CONSUMER_CHAIN_FIELDS
        or any(not _nonempty_text(row.get(field)) for field in CONSUMER_CHAIN_FIELDS)
        for row in chain
    ):
        return False
    stages = [row["stage"] for row in chain]
    if any(stage not in stages for stage in REQUIRED_CONSUMER_STAGES):
        return False
    if [stages.index(stage) for stage in REQUIRED_CONSUMER_STAGES] != sorted(
        stages.index(stage) for stage in REQUIRED_CONSUMER_STAGES
    ):
        return False
    if (
        not _nonempty_text(pair.get("expected_field"))
        or not _nonempty_text(pair.get("observed_field"))
        or not _nonempty_text(pair.get("expected_value"))
        or not _nonempty_text(pair.get("observed_value"))
        or not isinstance(pair.get("equal"), bool)
        or pair["equal"] != (pair["expected_value"] == pair["observed_value"])
    ):
        return False
    if value["provenance_source"] != "actual_execution" and pair["equal"]:
        return False
    if pair["equal"]:
        if value["earliest_rejecting_consumer"]:
            return False
    elif value["earliest_rejecting_consumer"] not in {
        row["consumer"] for row in chain
    }:
        return False
    if (
        authority_contract_profile
        == AUTHORITY_CONTRACT_PROFILE_A30_BOOTSTRAP_OIDC
    ):
        actual_chain = tuple(
            (
                row["stage"],
                row["authority"],
                row["consumer"],
                row["required_provenance_field"],
            )
            for row in chain
        )
        if actual_chain != A30_BOOTSTRAP_OIDC_CONSUMER_CHAIN:
            return False
        if (
            pair["expected_field"], pair["observed_field"]
        ) != A30_BOOTSTRAP_OIDC_FINAL_PAIR:
            return False
        if (
            not pair["equal"]
            and value["earliest_rejecting_consumer"]
            != A30_BOOTSTRAP_OIDC_EARLIEST_REJECTING_CONSUMER
        ):
            return False
        surface_authorities = {
            row["authority"] for row in authority_surfaces or []
        }
        proposed_fields = set(proposed_provenance_fields or [])
        chain_fields = {
            row["required_provenance_field"] for row in chain
        }
        if (
            {row["authority"] for row in chain} - surface_authorities
            or chain_fields - proposed_fields
            or set(A30_BOOTSTRAP_OIDC_FINAL_PAIR) - proposed_fields
        ):
            return False
    return True


def _rehearsal_execution_valid(value: Any, cross_layer_applicable: Any) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != REHEARSAL_EXECUTION_FIELDS
        or not isinstance(value.get("applicable"), bool)
        or value.get("applicable") is not cross_layer_applicable
        or value.get("runtime_implementation_kind")
        not in RUNTIME_IMPLEMENTATION_KINDS
        or value.get("transition_event_source") not in EXECUTION_EVENT_SOURCES
        or value.get("producer_result_source") not in EXECUTION_EVENT_SOURCES
        or not isinstance(value.get("transition_executed"), bool)
        or not isinstance(value.get("claimed_final_capability_observed"), bool)
    ):
        return False
    transition = value.get("target_runtime_transition")
    required = value.get("required_producer_operations")
    executed = value.get("executed_producer_operations")
    if any(
        not isinstance(rows, list)
        or any(not _nonempty_text(item) for item in rows)
        or len(rows) != len(set(rows))
        for rows in (transition, required, executed)
    ) or not set(executed) <= set(required):
        return False
    if value["applicable"]:
        return (
            value["runtime_implementation_kind"] != "not_applicable"
            and bool(transition)
            and bool(required)
            and _nonempty_text(value.get("claimed_final_capability"))
            and value["transition_event_source"] != "not_applicable"
            and value["producer_result_source"] != "not_applicable"
        )
    return (
        value["runtime_implementation_kind"] == "not_applicable"
        and transition == []
        and value["transition_event_source"] == "not_applicable"
        and value["transition_executed"] is False
        and required == []
        and executed == []
        and value["producer_result_source"] == "not_applicable"
        and value.get("claimed_final_capability") == ""
        and value["claimed_final_capability_observed"] is False
    )


def _rehearsal_execution_result(value: dict[str, Any]) -> dict[str, Any]:
    applicable = value["applicable"]
    required = value["required_producer_operations"]
    executed = value["executed_producer_operations"]
    fixture_substitution = applicable and (
        value["runtime_implementation_kind"] in SUBSTITUTED_RUNTIME_KINDS
        or value["transition_event_source"] in SUBSTITUTED_EXECUTION_SOURCES
        or value["producer_result_source"] in SUBSTITUTED_EXECUTION_SOURCES
    )
    target_runtime_transition_executed = (
        not applicable
        or (
            value["runtime_implementation_kind"] == "target_runtime"
            and value["transition_event_source"] == "actual_target_runtime"
            and value["transition_executed"] is True
        )
    )
    producer_operations_complete = (
        not applicable
        or (
            bool(required)
            and set(executed) == set(required)
            and value["producer_result_source"] == "actual_target_runtime"
        )
    )
    qualifying_actual_execution = (
        not fixture_substitution
        and target_runtime_transition_executed
        and producer_operations_complete
        and (
            not applicable
            or value["claimed_final_capability_observed"] is True
        )
    )
    normal_runtime_projection = (
        applicable
        and qualifying_actual_execution
        and value["target_runtime_transition"]
        == ["ready", "connector_offline", "replacement_online"]
        and set(required) == {"authenticated_pairing", "list_sessions"}
        and value["claimed_final_capability"] == "work_pc_session_projection"
    )
    return {
        "applicable": applicable,
        "runtime_implementation_kind": value["runtime_implementation_kind"],
        "target_runtime_transition": value["target_runtime_transition"],
        "transition_event_source": value["transition_event_source"],
        "transition_executed": value["transition_executed"],
        "required_producer_operations": required,
        "executed_producer_operations": executed,
        "producer_result_source": value["producer_result_source"],
        "claimed_final_capability": value["claimed_final_capability"],
        "claimed_final_capability_observed": value[
            "claimed_final_capability_observed"
        ],
        "fixture_substitution_detected": fixture_substitution,
        "target_runtime_transition_executed": target_runtime_transition_executed,
        "producer_operations_complete": producer_operations_complete,
        "qualifying_actual_execution": qualifying_actual_execution,
        "normal_runtime_replacement_work_pc_projection_supported": (
            normal_runtime_projection
        ),
        "pass_label": (
            NORMAL_RUNTIME_REPLACEMENT_WORK_PC_PROJECTION_PASS_LABEL
            if normal_runtime_projection
            else ""
        ),
    }


def _authority_cardinality_result(
    value: dict[str, Any], authority_contract_profile: str
) -> dict[str, Any]:
    surfaces = value["authority_surfaces"]
    required_fields = sorted({surface["required_provenance_field"] for surface in surfaces})
    proposed_fields = sorted(set(value["proposed_provenance_fields"]))
    independent_histories = value["histories_share_merge_base"] is False
    review_required = value["applicable"] and (
        len(surfaces) >= 2 or independent_histories
    )
    missing_fields = sorted(set(required_fields) - set(proposed_fields))
    collapse_detected = review_required and (
        bool(missing_fields) or len(proposed_fields) < len(surfaces)
    )
    cross_layer_rehearsal_required = value["cross_layer_applicable"]
    stale_final_consumer_invariant = bool(value["superseded_invariants_retained"])
    rehearsal_execution = _rehearsal_execution_result(value["rehearsal_execution"])
    strict_consumer_binding_required = (
        value["cross_layer_applicable"]
        and (
            authority_contract_profile
            == AUTHORITY_CONTRACT_PROFILE_A30_BOOTSTRAP_OIDC
            or len(surfaces) >= 4
        )
    )
    if rehearsal_execution["fixture_substitution_detected"]:
        cross_layer_disposition = (
            "REHEARSE_ACTUAL_TARGET_RUNTIME_AND_PRODUCER_BEFORE_PHYSICAL_CTA"
        )
    elif stale_final_consumer_invariant:
        cross_layer_disposition = "REHEARSE_FINAL_EXECUTABLE_CONSUMER_BEFORE_PHYSICAL_CTA"
    elif value["final_executable_consumer_bound"] is not True:
        cross_layer_disposition = "BIND_FINAL_EXECUTABLE_CONSUMER_BEFORE_PHYSICAL_CTA"
    elif value["cross_layer_rehearsal_completed"] is not True:
        cross_layer_disposition = "EXECUTE_CROSS_LAYER_CONTRACT_REHEARSAL_BEFORE_PHYSICAL_CTA"
    elif not rehearsal_execution["qualifying_actual_execution"]:
        cross_layer_disposition = (
            "EXECUTE_ACTUAL_TARGET_RUNTIME_TRANSITION_AND_PRODUCER_BEFORE_PHYSICAL_CTA"
        )
    else:
        cross_layer_disposition = "CROSS_LAYER_CONTRACT_PRESERVED"
    if not review_required:
        disposition = "AUTHORITY_CARDINALITY_REVIEW_NOT_APPLICABLE"
    elif collapse_detected and value["mutation_started"]:
        disposition = "AUTHORITY_CARDINALITY_COLLAPSE_DETECTED_AFTER_MUTATION"
    elif collapse_detected:
        disposition = "REHEARSE_AUTHORITY_CARDINALITY_BEFORE_MUTATION"
    elif value["review_completed"] is not True:
        disposition = "COMPLETE_AUTHORITY_CARDINALITY_REVIEW_BEFORE_MUTATION"
    else:
        disposition = "DISTINCT_PROVENANCE_IDENTITIES_PRESERVED"
    consumer_binding = value.get("consumer_binding")
    consumer_binding_valid = _consumer_binding_valid(
        consumer_binding,
        required=strict_consumer_binding_required,
        authority_surfaces=surfaces,
        proposed_provenance_fields=value["proposed_provenance_fields"],
        authority_contract_profile=authority_contract_profile,
    )
    consumer_pair_equal = (
        not strict_consumer_binding_required
        or (
            isinstance(consumer_binding, dict)
            and isinstance(consumer_binding.get("final_compared_field_pair"), dict)
            and consumer_binding["final_compared_field_pair"].get("equal") is True
        )
    )
    earliest_consumer_rejects = (
        strict_consumer_binding_required
        and consumer_binding_valid
        and consumer_binding.get("provenance_source") == "actual_execution"
        and consumer_pair_equal is False
    )
    if strict_consumer_binding_required and not consumer_binding_valid:
        cross_layer_disposition = (
            "BIND_EARLIEST_REJECTING_FINAL_CONSUMER_AND_FIELD_PAIR_BEFORE_PHYSICAL_CTA"
        )
    elif earliest_consumer_rejects:
        cross_layer_disposition = (
            "REHEARSE_EARLIEST_REJECTING_FINAL_CONSUMER_BEFORE_PHYSICAL_CTA"
        )
    integration_readiness_supported = (
        not collapse_detected
        and value["review_completed"] is True
        and value["cross_layer_rehearsal_completed"] is True
        and value["final_executable_consumer_bound"] is True
        and not stale_final_consumer_invariant
        and rehearsal_execution["qualifying_actual_execution"] is True
        and consumer_binding_valid
        and consumer_pair_equal
    )
    return {
        "review_required": review_required,
        "authority_contract_profile": authority_contract_profile,
        "distinct_authority_count": len(surfaces),
        "independent_histories": independent_histories,
        "required_provenance_fields": required_fields,
        "proposed_provenance_fields": proposed_fields,
        "missing_provenance_fields": missing_fields,
        "premature_authority_collapse_detected": collapse_detected,
        "detected_before_mutation": collapse_detected and value["mutation_started"] is False,
        "review_completed": value["review_completed"],
        "mutation_started": value["mutation_started"],
        "contract_path": value["contract_path"],
        "cross_layer_contract_rehearsal_required": cross_layer_rehearsal_required,
        "cross_layer_rehearsal_completed": value["cross_layer_rehearsal_completed"],
        "physical_cta_started": value["physical_cta_started"],
        "producer_adapter_tests_passed": value["producer_adapter_tests_passed"],
        "exact_head_ci_green": value["exact_head_ci_green"],
        "final_executable_consumer_bound": value["final_executable_consumer_bound"],
        "superseded_invariants_retained": value["superseded_invariants_retained"],
        "stale_final_consumer_invariant_detected": stale_final_consumer_invariant,
        "rehearsal_execution_qualification": rehearsal_execution,
        "rehearsal_qualification_pass_label": (
            rehearsal_execution["pass_label"]
            if integration_readiness_supported
            else ""
        ),
        "cross_layer_disposition": cross_layer_disposition,
        "strict_consumer_binding_required": strict_consumer_binding_required,
        "consumer_binding_present": consumer_binding is not None,
        "consumer_binding_valid": consumer_binding_valid,
        "earliest_consumer_rejects": earliest_consumer_rejects,
        "final_compared_field_pair_equal": consumer_pair_equal,
        "integration_readiness_supported": integration_readiness_supported,
        "detected_before_physical_cta": (
            cross_layer_rehearsal_required
            and integration_readiness_supported is False
            and value["physical_cta_started"] is False
        ),
        "disposition": disposition,
    }


def evaluate_serial_stall_parallel_response(value: Any) -> dict[str, Any]:
    """Select one nonblocking parallel action when serial work outruns user value."""
    invalid = (
        not isinstance(value, dict)
        or not SERIAL_STALL_STATE_FIELDS <= set(value)
        or bool(set(value) - SERIAL_STALL_STATE_FIELDS - SERIAL_STALL_OPTIONAL_STATE_FIELDS)
    )
    candidate = value.get("parallel_candidate", {}) if isinstance(value, dict) else {}
    firing = value.get("prior_control_firing", {}) if isinstance(value, dict) else {}
    authority_review = value.get("authority_cardinality_review", {}) if isinstance(value, dict) else {}
    authority_contract_profile = (
        value.get("authority_contract_profile", AUTHORITY_CONTRACT_PROFILE_GENERIC)
        if isinstance(value, dict)
        else AUTHORITY_CONTRACT_PROFILE_GENERIC
    )
    if not invalid:
        invalid = (
            value.get("schema_version") != SERIAL_STALL_VERSION
            or authority_contract_profile not in AUTHORITY_CONTRACT_PROFILES
            or not _positive_int(value.get("estimate_ms"))
            or not _nonnegative_int(value.get("elapsed_ms"))
            or not _nonnegative_int(value.get("user_visible_capability_delta"))
            or not _nonnegative_int(value.get("serial_cause_changing_cycles"))
            or not isinstance(value.get("implementation_owner_active"), bool)
            or not isinstance(value.get("one_writer_owner_bound"), bool)
            or not isinstance(candidate, dict)
            or set(candidate) != SERIAL_STALL_CANDIDATE_FIELDS
            or candidate.get("kind") not in SERIAL_STALL_LANE_KINDS
            or not isinstance(candidate.get("useful"), bool)
            or not _nonnegative_int(candidate.get("active_lane_count"))
            or not isinstance(candidate.get("write_access"), bool)
            or not isinstance(candidate.get("write_set_overlap"), bool)
            or not _nonempty_text(candidate.get("target_binding"))
            or not _nonempty_text(candidate.get("context_packet_ref"))
            or not isinstance(candidate.get("return_schema"), list)
            or any(
                not _nonempty_text(field)
                for field in candidate.get("return_schema", [])
            )
            or len(candidate.get("return_schema", []))
            != len(set(candidate.get("return_schema", [])))
            or not isinstance(candidate.get("required_checks"), list)
            or any(
                not _nonempty_text(field)
                for field in candidate.get("required_checks", [])
            )
            or len(candidate.get("required_checks", [])) != len(set(candidate.get("required_checks", [])))
            or not _serial_stall_firing_valid(firing)
            or not _authority_cardinality_review_valid(
                authority_review, authority_contract_profile
            )
        )
    if invalid:
        return {
            "schema_version": SERIAL_STALL_VERSION,
            "decision": "INVALID_SERIAL_STALL_STATE",
            "reasons": ["SERIAL_STALL_STATE_INVALID"],
            "triggered": False,
            "active_owner_continues": False,
            "owner_paused": False,
            "dispatch_count": 0,
            "actionable_nonfire_code": "SERIAL_STALL_STATE_INVALID",
            "demoted_non_authority_controls": [],
            "support_work_progress_credit": 0,
            "non_claims": ["no_runtime_product_or_final_acceptance"],
        }

    estimate_overrun = value["elapsed_ms"] >= value["estimate_ms"] * 2
    absolute_overrun = value["elapsed_ms"] >= 1_800_000
    authority_result = _authority_cardinality_result(
        authority_review, authority_contract_profile
    )
    stall_threshold_reached = (
        (estimate_overrun or absolute_overrun)
        and value["user_visible_capability_delta"] == 0
        and value["serial_cause_changing_cycles"] >= 3
    )
    useful_parallel_work_available = (
        candidate["useful"] is True and candidate["kind"] != "none"
    )
    triggered = stall_threshold_reached and useful_parallel_work_available
    prior_nonfires = _serial_stall_prior_nonfires(firing)
    decision = "CONTINUE_ACTIVE_OWNER_NO_PARALLEL_TRIGGER"
    reasons = ["SERIAL_CAPABILITY_STALL_THRESHOLD_NOT_REACHED"]
    dispatch_count = 0
    actionable_nonfire = ""
    if stall_threshold_reached:
        reasons = ["SERIAL_CAPABILITY_STALL_THRESHOLD_REACHED", *prior_nonfires]
        if authority_result["review_required"]:
            reasons.append("AUTHORITY_CARDINALITY_REVIEW_REQUIRED_BEFORE_MUTATION")
        if authority_result["cross_layer_contract_rehearsal_required"]:
            reasons.append("CROSS_LAYER_CONTRACT_REHEARSAL_REQUIRED_BEFORE_PHYSICAL_CTA")
        if authority_result["stale_final_consumer_invariant_detected"]:
            reasons.append("STALE_FINAL_EXECUTABLE_CONSUMER_INVARIANT_DETECTED")
        if (
            authority_result["producer_adapter_tests_passed"]
            and authority_result["exact_head_ci_green"]
            and authority_result["stale_final_consumer_invariant_detected"]
        ):
            reasons.append("PER_REPO_GREEN_CHECKS_INSUFFICIENT_FOR_INTEGRATION_READINESS")
        if authority_result["premature_authority_collapse_detected"]:
            reasons.append("PREMATURE_LOCAL_CAUSE_AUTHORITY_COLLAPSE_DETECTED")
        rehearsal_qualification = authority_result[
            "rehearsal_execution_qualification"
        ]
        if rehearsal_qualification["fixture_substitution_detected"]:
            reasons.append("FIXTURE_OR_MOCK_REHEARSAL_SUBSTITUTION_DETECTED")
        elif (
            authority_result["cross_layer_rehearsal_completed"]
            and not rehearsal_qualification["qualifying_actual_execution"]
        ):
            reasons.append(
                "COMPLETED_REHEARSAL_LACKS_ACTUAL_RUNTIME_PRODUCER_EXECUTION"
            )
        if value["implementation_owner_active"] is not True:
            actionable_nonfire = "ACTIVE_IMPLEMENTATION_OWNER_REQUIRED"
        elif candidate["active_lane_count"] > 1:
            actionable_nonfire = "PARALLEL_LANE_COUNT_EXCEEDS_ONE"
        elif candidate["active_lane_count"] == 1:
            actionable_nonfire = "BOUNDED_PARALLEL_LANE_ALREADY_ACTIVE_CONSUME_READBACK"
        elif candidate["useful"] is not True or candidate["kind"] == "none":
            actionable_nonfire = "USEFUL_INDEPENDENT_READ_ONLY_OR_REHEARSAL_WORK_NOT_BOUND"
        elif (
            value["one_writer_owner_bound"] is not True
            or candidate["write_access"] is not False
            or candidate["write_set_overlap"] is not False
        ):
            actionable_nonfire = "ONE_WRITER_READ_ONLY_PARALLELISM_CONTRACT_INVALID"
        elif (
            _empty(candidate.get("target_binding"))
            or _empty(candidate.get("context_packet_ref"))
            or not SERIAL_STALL_RETURN_FIELDS <= set(candidate["return_schema"])
        ):
            actionable_nonfire = "PARALLEL_LANE_CONTEXT_OR_RETURN_CONTRACT_MISSING"
        elif (
            authority_result["review_required"]
            and not AUTHORITY_CARDINALITY_REQUIRED_CHECKS <= set(candidate["required_checks"])
        ):
            actionable_nonfire = "AUTHORITY_CARDINALITY_REHEARSAL_NOT_BOUND"
        elif (
            authority_result["cross_layer_contract_rehearsal_required"]
            and CROSS_LAYER_CONTRACT_REQUIRED_CHECK not in candidate["required_checks"]
        ):
            actionable_nonfire = "CROSS_LAYER_CONTRACT_REHEARSAL_NOT_BOUND"
        elif (
            authority_result["cross_layer_contract_rehearsal_required"]
            and FINAL_CONSUMER_REQUIRED_CHECK not in candidate["required_checks"]
        ):
            actionable_nonfire = "FINAL_EXECUTABLE_CONSUMER_REHEARSAL_NOT_BOUND"
        elif rehearsal_qualification["fixture_substitution_detected"]:
            actionable_nonfire = (
                "QUALIFYING_REHEARSAL_FIXTURE_SUBSTITUTION_DETECTED"
            )
        elif (
            authority_result["cross_layer_contract_rehearsal_required"]
            and authority_result["cross_layer_rehearsal_completed"]
            and not rehearsal_qualification["qualifying_actual_execution"]
        ):
            actionable_nonfire = (
                "QUALIFYING_REHEARSAL_ACTUAL_RUNTIME_OR_PRODUCER_NOT_EXECUTED"
            )
        elif authority_result["strict_consumer_binding_required"] and not authority_result[
            "consumer_binding_valid"
        ]:
            actionable_nonfire = "FINAL_CONSUMER_PROVENANCE_BINDING_MISSING_OR_INVALID"
        elif authority_result["earliest_consumer_rejects"]:
            actionable_nonfire = "EARLIEST_FINAL_CONSUMER_FIELD_PAIR_REJECTS"
        elif (
            authority_result["cross_layer_contract_rehearsal_required"]
            and authority_result["physical_cta_started"]
            and authority_result["cross_layer_rehearsal_completed"] is not True
        ):
            actionable_nonfire = "CROSS_LAYER_CONTRACT_REHEARSAL_LATE_AFTER_PHYSICAL_CTA"
        elif (
            authority_result["review_required"]
            and authority_result["mutation_started"]
            and (
                authority_result["premature_authority_collapse_detected"]
                or authority_result["review_completed"] is not True
            )
        ):
            actionable_nonfire = "AUTHORITY_CARDINALITY_REVIEW_LATE_AFTER_MUTATION"
        elif not _serial_stall_budget_valid(candidate.get("budget")):
            actionable_nonfire = "BOUNDED_PARALLEL_LANE_BUDGET_INVALID"

        if actionable_nonfire:
            decision = "EMIT_ACTIONABLE_PARALLEL_NONFIRE"
            reasons.append(actionable_nonfire)
        else:
            decision = "DISPATCH_BOUNDED_PARALLEL_DIAGNOSIS_OR_REHEARSAL"
            reasons.append("BOUNDED_PARALLEL_DIAGNOSIS_OR_REHEARSAL_REQUIRED")
            dispatch_count = 1

    return {
        "schema_version": SERIAL_STALL_VERSION,
        "decision": decision,
        "reasons": reasons,
        "trigger": {
            "estimate_overrun_at_least_2x": estimate_overrun,
            "absolute_overrun_at_least_30_minutes": absolute_overrun,
            "user_visible_capability_delta_zero": value["user_visible_capability_delta"] == 0,
            "serial_cause_changing_cycles_at_least_3": value["serial_cause_changing_cycles"] >= 3,
            "useful_independent_work_available": useful_parallel_work_available,
        },
        "triggered": triggered,
        "active_owner_continues": value["implementation_owner_active"],
        "owner_paused": False,
        "one_writer_preserved": (
            value["one_writer_owner_bound"] is True
            and candidate["write_access"] is False
            and candidate["write_set_overlap"] is False
        ),
        "dispatch_count": dispatch_count,
        "max_dispatch_count": 1,
        "parallel_lane_kind": candidate["kind"],
        "parallel_lane_budget": candidate["budget"],
        "parallel_lane_required_checks": candidate["required_checks"],
        "authority_cardinality_review": authority_result,
        "actionable_nonfire_code": actionable_nonfire,
        "prior_control_firing": firing,
        "prior_control_nonfires": prior_nonfires,
        "demoted_non_authority_controls": (
            ["status_only_heartbeat_monitoring"] if stall_threshold_reached else []
        ),
        "support_work_progress_credit": 0,
        "non_claims": [
            "no_product_owner_pause_or_ownership_transfer",
            "no_protected_external_or_product_repo_mutation_authority",
            "no_installed_cache_or_fresh_session_firing_proof",
            "no_observed_effectiveness_from_source_or_fixture_validation",
            "no_runtime_product_or_final_acceptance",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--contract", help="path to one adaptive_work_pace_replan JSON object")
    inputs.add_argument("--serial-stall-state", help="path to an INC A30 serial-stall state or incident")
    parser.add_argument("--now", help="UTC evaluation time in ISO-8601 form; defaults to the real current clock")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.serial_stall_state:
        try:
            serial_doc = json.loads(Path(args.serial_stall_state).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            serial_doc = None
        if isinstance(serial_doc, dict) and "serial_stall_state" in serial_doc:
            serial_doc = serial_doc["serial_stall_state"]
        response = evaluate_serial_stall_parallel_response(serial_doc)
        ok = response.get("decision") in SERIAL_STALL_DECISIONS - {"INVALID_SERIAL_STALL_STATE"}
        result = {
            "tool": "mk_adaptive_work_pace",
            "status": (
                "PASS_SERIAL_STALL_ACTION_SELECTED_SUPPORT_ONLY"
                if ok else "FAIL_SERIAL_STALL_STATE"
            ),
            "serial_stall_response": response,
            "non_claims": response.get("non_claims", []),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else result["status"])
        return 0 if ok else 1
    path = Path(args.contract)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = None
    now = _parse_time(args.now) if args.now else datetime.now(timezone.utc)
    blocks = ["BLOCKED_FOR_MK741_ADAPTIVE_WORK_PACE_EVALUATION_TIME_INVALID"] if args.now and now is None else check_contract(value, now=now, enforce_timing=True)
    replan_due_codes = {
        "BLOCKED_FOR_MK741_CHECKPOINT_NOT_RECORDED",
        "BLOCKED_FOR_MK741_OVERRUN_REPLAN_REQUIRED",
    }
    replan_due = [block for block in blocks if block in replan_due_codes]
    blocking_errors = [block for block in blocks if block not in replan_due_codes]
    result = {
        "tool": "mk_adaptive_work_pace",
        "status": (
            "FAIL_PACE_CONTRACT_INVALID"
            if blocking_errors
            else "REPLAN_REVIEW_DUE_SUPPORT_ONLY"
            if replan_due
            else "PASS_PACE_CONTRACT_SUPPORT_ONLY"
        ),
        "blocks": blocking_errors,
        "claim_checks": replan_due,
        "unchanged_stalled_continuation_claim_allowed": not bool(replan_due),
        "safe_local_work_continues": not bool(blocking_errors),
        "non_claims": [
            "not_an_authority_gate",
            "does_not_block_normal_local_bounded_supervised_work",
            "does_not_grant_external_mutation_authority",
            "no_observed_effective_prevention",
            "no_runtime_or_product_acceptance",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else result["status"])
    return 0 if not blocking_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
