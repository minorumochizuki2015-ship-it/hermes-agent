#!/usr/bin/env python3
"""Pure transition decisions for bounded Hermes continuity controls."""

from __future__ import annotations

from typing import Any


TERMINAL_STATES = frozenset({"completed", "failed", "stopped", "obsolete"})
TERMINAL_TRANSITIONS = frozenset({"idle", "final", "protected_wait"})

ALLOW_FINAL_IDLE = "ALLOW_FINAL_IDLE"
ALLOW_NARROW_PROTECTED_WAIT = "ALLOW_NARROW_PROTECTED_WAIT"
ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR = "ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR"
REJECT_IDLE_DISJOINT_WORK_UNASSIGNED = "REJECT_IDLE_DISJOINT_WORK_UNASSIGNED"
CONTINUE_CURRENT_CONTROLLER = "CONTINUE_CURRENT_CONTROLLER"


def _result(
    *,
    action: str,
    code: str,
    notify: bool = False,
    launch: bool = False,
    create_control: bool = False,
    recovery_attempts: int = 0,
) -> dict[str, Any]:
    return {
        "action": action,
        "code": code,
        "notify": notify,
        "launch": launch,
        "create_control": create_control,
        "deliver_heartbeat": False,
        "recovery_attempts": recovery_attempts,
    }


def _terminal_result(decision: str, reason: str) -> dict[str, Any]:
    return {
        "terminal_decision": decision,
        "reason": reason,
        "allow_idle": decision
        in {
            ALLOW_FINAL_IDLE,
            ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR,
        },
        "allow_protected_wait": decision == ALLOW_NARROW_PROTECTED_WAIT,
        "continue_controller": decision
        in {
            REJECT_IDLE_DISJOINT_WORK_UNASSIGNED,
            CONTINUE_CURRENT_CONTROLLER,
        },
    }


TERMINAL_AUTHORITY_CONTRACT_ID = "INC191_PRE_IDLE_SUCCESSOR_ADMISSION_V1"
TERMINAL_AUTHORITY_CONTRACT_VERSION = "1.1.0"
TERMINAL_AUTHORITY_SOURCE_SHA256 = (
    "35ac9d266faf9841ada668efe10768ce383e5601ff362ad9b12cc670dd171942"
)
TERMINAL_AUTHORITY_PROFILE_SHA256 = (
    "a57c57fc6cbe65c5657324ebbc737a370c7ef24ea6ae5cc2f0305ec94607c0be"
)
_TERMINAL_ALLOW_BY_TRANSITION = {
    "idle": ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR,
    "final": ALLOW_FINAL_IDLE,
    "protected_wait": ALLOW_NARROW_PROTECTED_WAIT,
}
_TERMINAL_AUTHORITY_DECISIONS = frozenset({
    ALLOW_FINAL_IDLE,
    ALLOW_NARROW_PROTECTED_WAIT,
    ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR,
    REJECT_IDLE_DISJOINT_WORK_UNASSIGNED,
    CONTINUE_CURRENT_CONTROLLER,
})


def decide_terminal_transition(observation: dict[str, Any]) -> dict[str, Any]:
    """Consume one atomic Maestro result at an explicit terminal transition.

    This portable consumer never derives an ALLOW from caller facts. Grand Goal
    finality, protected scope, disjoint work and successor receipt semantics are
    evaluated by the pinned Maestro authority producer. Local checks only reject
    stale/wrong-owner/replayed inputs before consuming that atomic result.
    """

    transition = str(observation.get("terminal_transition") or "").strip()
    if transition not in TERMINAL_TRANSITIONS:
        return _terminal_result(
            CONTINUE_CURRENT_CONTROLLER,
            "not_an_admitted_terminal_transition",
        )

    control_id = str(observation.get("control_id") or "").strip()
    current_owner = str(observation.get("current_owner") or "").strip()
    observed_owner = str(observation.get("observed_owner") or "").strip()
    current_epoch = observation.get("owner_epoch")
    observed_epoch = observation.get("observed_owner_epoch")
    if not control_id or not current_owner or observed_owner != current_owner:
        return _terminal_result(CONTINUE_CURRENT_CONTROLLER, "wrong_or_missing_owner")
    if (
        type(current_epoch) is not int
        or current_epoch < 0
        or type(observed_epoch) is not int
        or observed_epoch != current_epoch
    ):
        return _terminal_result(CONTINUE_CURRENT_CONTROLLER, "stale_owner_epoch")
    if type(observation.get("disjoint_work_remaining")) is not bool:
        return _terminal_result(
            CONTINUE_CURRENT_CONTROLLER,
            "terminal_inputs_incomplete",
        )

    receipt = observation.get("successor_receipt")
    if type(receipt) is dict:
        receipt_id = str(receipt.get("receipt_id") or "").strip()
        consumed = observation.get("consumed_successor_receipts")
        consumed_ids = (
            {str(value) for value in consumed if isinstance(value, str)}
            if isinstance(consumed, (list, tuple, set, frozenset))
            else set()
        )
        if receipt_id and receipt_id in consumed_ids:
            return _terminal_result(
                CONTINUE_CURRENT_CONTROLLER,
                "successor_receipt_replay",
            )

    authority = observation.get("terminal_authority_result")
    if type(authority) is not dict:
        return _terminal_result(
            CONTINUE_CURRENT_CONTROLLER,
            "terminal_authority_result_missing",
        )
    decision = authority.get("consumer_decision")
    findings = authority.get("blocking_findings")
    authority_valid = (
        authority.get("contract_id") == TERMINAL_AUTHORITY_CONTRACT_ID
        and authority.get("contract_version") == TERMINAL_AUTHORITY_CONTRACT_VERSION
        and authority.get("authority_source_sha256") == TERMINAL_AUTHORITY_SOURCE_SHA256
        and authority.get("profile_sha256") == TERMINAL_AUTHORITY_PROFILE_SHA256
        and decision in _TERMINAL_AUTHORITY_DECISIONS
        and type(authority.get("admitted")) is bool
        and type(findings) is list
        and all(type(item) is str and item for item in findings)
    )
    if not authority_valid:
        return _terminal_result(
            CONTINUE_CURRENT_CONTROLLER,
            "terminal_authority_result_invalid",
        )
    if decision.startswith("ALLOW_"):
        if (
            authority.get("admitted") is not True
            or findings
            or decision != _TERMINAL_ALLOW_BY_TRANSITION[transition]
        ):
            return _terminal_result(
                CONTINUE_CURRENT_CONTROLLER,
                "terminal_authority_result_non_atomic_or_mismatched",
            )
        return _terminal_result(decision, "pinned_atomic_authority_allow")

    if authority.get("admitted") is not False:
        return _terminal_result(
            CONTINUE_CURRENT_CONTROLLER,
            "terminal_authority_result_invalid",
        )
    if decision == REJECT_IDLE_DISJOINT_WORK_UNASSIGNED:
        return _terminal_result(decision, "authority_rejected_unassigned_work")
    return _terminal_result(
        CONTINUE_CURRENT_CONTROLLER,
        "authority_requires_controller_continuation",
    )


def decide_control(
    previous: dict[str, Any] | None,
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Return one deterministic action without performing a launch.

    Callers may execute a returned `launch=true` recovery only after applying
    the surrounding Hermes session and authority contracts. Every deny,
    unavailable, duplicate, paused, and unchanged result is no-launch.
    """
    control_id = str(observation.get("control_id") or "").strip()
    dont_notify = bool(observation.get("dont_notify"))
    notify = not dont_notify
    if not control_id:
        return _result(action="none", code="invalid_observation")

    attempts = int((previous or {}).get("recovery_attempts") or 0)
    if observation.get("event") == "heartbeat":
        return _result(
            action="none",
            code="heartbeat_delivery_denied",
            recovery_attempts=attempts,
        )
    if observation.get("duplicate_active"):
        return _result(
            action="update_existing",
            code="duplicate_control_reused",
            recovery_attempts=attempts,
        )

    status = str(observation.get("status") or "unknown")
    fingerprint = str(observation.get("fingerprint") or status)
    if status == "paused":
        return _result(
            action="none",
            code="control_paused",
            recovery_attempts=attempts,
        )

    if not bool(observation.get("destination_available", True)):
        return _result(
            action="failed_destination",
            code="destination_unavailable",
            notify=notify,
            recovery_attempts=attempts,
        )

    external_dispatch = bool(observation.get("external_dispatch"))
    authority_admitted = bool(observation.get("authority_admitted"))
    if external_dispatch and not authority_admitted:
        return _result(
            action="none",
            code="maestro_authority_unavailable",
            notify=notify,
            recovery_attempts=attempts,
        )

    same_predicate_failures = observation.get("same_acceptance_predicate_failures")
    changed_premise = (
        observation.get("intervening_user_decision") is True
        or observation.get("changed_acceptance_predicate") is True
        or observation.get("changed_causal_hypothesis") is True
    )
    if observation.get("user_corrected_same_premise") is True:
        return _result(
            action="stop_and_replan",
            code="FABLE5_M10_USER_PREMISE_CORRECTION_REPLAN_REQUIRED",
            notify=notify,
            recovery_attempts=attempts,
        )
    if observation.get("fixed_decision_impossible") is True:
        return _result(
            action="stop_and_replan",
            code="FABLE5_M10_FIXED_DECISION_IMPOSSIBLE_REPLAN_REQUIRED",
            notify=notify,
            recovery_attempts=attempts,
        )
    cost_checkpoint_percent = observation.get("cost_checkpoint_percent")
    if (
        type(cost_checkpoint_percent) in {int, float}
        and cost_checkpoint_percent >= 70
        and observation.get("first_material_delta") is not True
    ):
        return _result(
            action="stop_and_replan",
            code="FABLE5_M10_COST_CHECKPOINT_NO_DELTA_REPLAN_REQUIRED",
            notify=notify,
            recovery_attempts=attempts,
        )
    if (
        type(same_predicate_failures) is int
        and same_predicate_failures >= 2
        and not changed_premise
    ):
        code = "FABLE5_M10_TWO_FAILURE_REPLAN_REQUIRED"
        if observation.get("third_acceptance_attempt_scheduled") is True:
            code = "BLOCKED_FOR_FABLE5_NF_M10_SILENT_THIRD_ACCEPTANCE_ATTEMPT"
        return _result(
            action="stop_and_replan",
            code=code,
            notify=notify,
            recovery_attempts=attempts,
        )

    terminal_transition = str(observation.get("terminal_transition") or "").strip()
    if terminal_transition in TERMINAL_TRANSITIONS:
        terminal = decide_terminal_transition(observation)
        decision = terminal["terminal_decision"]
        if decision in {ALLOW_FINAL_IDLE, ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR}:
            action = "retire"
        elif decision == ALLOW_NARROW_PROTECTED_WAIT:
            action = "protected_wait"
        else:
            action = "continue_current"
        result = _result(
            action=action,
            code=decision,
            notify=notify,
            recovery_attempts=attempts,
        )
        result.update(terminal)
        return result

    if previous is None:
        return _result(
            action="start",
            code="control_started",
            notify=notify,
            create_control=True,
        )

    if status == str(previous.get("status") or "unknown") and fingerprint == str(
        previous.get("fingerprint") or previous.get("status") or ""
    ):
        return _result(
            action="none",
            code="state_unchanged",
            recovery_attempts=attempts,
        )

    if status == "timeout":
        if bool(observation.get("cause_changed")) and attempts < 1:
            return _result(
                action="bounded_recovery",
                code="bounded_recovery_admitted",
                notify=notify,
                launch=True,
                recovery_attempts=attempts + 1,
            )
        return _result(
            action="pause",
            code="timeout_recovery_exhausted",
            notify=notify,
            recovery_attempts=attempts,
        )

    if status in TERMINAL_STATES:
        return _result(
            action="retire",
            code=f"terminal_{status}",
            notify=notify,
            recovery_attempts=attempts,
        )

    return _result(
        action="emit_transition",
        code="state_transition",
        notify=notify,
        recovery_attempts=attempts,
    )
