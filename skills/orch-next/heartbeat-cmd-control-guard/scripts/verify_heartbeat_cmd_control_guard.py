#!/usr/bin/env python3
"""Verify the portable Hermes heartbeat runtime contract."""

from __future__ import annotations

from pathlib import Path
import sys

from heartbeat_control import (
    ALLOW_FINAL_IDLE,
    ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR,
    ALLOW_NARROW_PROTECTED_WAIT,
    REJECT_IDLE_DISJOINT_WORK_UNASSIGNED,
    decide_control,
    TERMINAL_AUTHORITY_CONTRACT_ID,
    TERMINAL_AUTHORITY_CONTRACT_VERSION,
    TERMINAL_AUTHORITY_PROFILE_SHA256,
    TERMINAL_AUTHORITY_SOURCE_SHA256,
)


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
REQUIRED_TERMS = (
    "Hermes owns this operation",
    "Maestro is never an execution fallback",
    "Prefer native event delivery",
    "`cronjob`",
    "`state_transition_only`",
    "`no_decision_delta_self_demotion`",
    "`self_delete_at_terminal_or_obsolete`",
    "product-progress credit zero",
    "`keep`, `demote`, or `remove`",
    "user as a session router",
    "`FABLE5_M10_TPL_H_QUESTION_6`",
    "`STOP_AND_REPLAN`",
    "`BLOCKED_FOR_FABLE5_NF_M10_SILENT_THIRD_ACCEPTANCE_ATTEMPT`",
    "`ALLOW_FINAL_IDLE`",
    "monotonic owner-transfer epoch",
)


def _terminal_base(**updates):
    value = {
        "control_id": "goal-1",
        "status": "completed",
        "fingerprint": "done",
        "terminal_transition": "idle",
        "current_owner": "controller-a",
        "observed_owner": "controller-a",
        "owner_epoch": 7,
        "observed_owner_epoch": 7,
        "grand_goal_final": False,
        "final_acceptance_authorized": False,
        "disjoint_work_remaining": False,
        "terminal_authority_result": _terminal_authority(
            ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR
        ),
    }
    value.update(updates)
    return value


def _terminal_authority(decision):
    return {
        "contract_id": TERMINAL_AUTHORITY_CONTRACT_ID,
        "contract_version": TERMINAL_AUTHORITY_CONTRACT_VERSION,
        "authority_source_sha256": TERMINAL_AUTHORITY_SOURCE_SHA256,
        "profile_sha256": TERMINAL_AUTHORITY_PROFILE_SHA256,
        "consumer_decision": decision,
        "admitted": decision.startswith("ALLOW_"),
        "blocking_findings": [],
    }


def main() -> int:
    text = SKILL.read_text(encoding="utf-8")
    missing = [term for term in REQUIRED_TERMS if term not in text]
    if missing:
        print("FAIL heartbeat Hermes runtime contract")
        for term in missing:
            print(f"- missing: {term}")
        return 1
    started = decide_control(
        None,
        {"control_id": "c1", "status": "running", "fingerprint": "a"},
    )
    unchanged = decide_control(
        {"status": "running", "fingerprint": "a"},
        {"control_id": "c1", "status": "running", "fingerprint": "a"},
    )
    denied = decide_control(
        {"status": "running", "fingerprint": "a"},
        {
            "control_id": "c1",
            "status": "timeout",
            "fingerprint": "b",
            "cause_changed": True,
            "external_dispatch": True,
            "authority_admitted": False,
        },
    )
    blocked_third_attempt = decide_control(
        {"status": "running", "fingerprint": "a"},
        {
            "control_id": "c1",
            "status": "running",
            "fingerprint": "b",
            "same_acceptance_predicate_failures": 2,
            "third_acceptance_attempt_scheduled": True,
            "intervening_user_decision": False,
            "changed_acceptance_predicate": False,
            "changed_causal_hypothesis": False,
        },
    )
    changed_hypothesis = decide_control(
        {"status": "running", "fingerprint": "a"},
        {
            "control_id": "c1",
            "status": "running",
            "fingerprint": "b",
            "same_acceptance_predicate_failures": 2,
            "third_acceptance_attempt_scheduled": True,
            "changed_causal_hypothesis": True,
        },
    )
    cost_replan = decide_control(
        {"status": "running", "fingerprint": "a"},
        {
            "control_id": "c1",
            "status": "running",
            "fingerprint": "b",
            "cost_checkpoint_percent": 70,
            "first_material_delta": False,
        },
    )
    corrected_premise = decide_control(
        {"status": "running", "fingerprint": "a"},
        {
            "control_id": "c1",
            "status": "running",
            "fingerprint": "b",
            "user_corrected_same_premise": True,
        },
    )
    rejected_idle = decide_control(
        {"status": "running", "fingerprint": "a"},
        _terminal_base(
            disjoint_work_remaining=True,
            terminal_authority_result=_terminal_authority(
                REJECT_IDLE_DISJOINT_WORK_UNASSIGNED
            ),
        ),
    )
    final_idle = decide_control(
        {"status": "running", "fingerprint": "a"},
        _terminal_base(
            terminal_transition="final",
            grand_goal_final=True,
            final_acceptance_authorized=True,
            terminal_authority_result=_terminal_authority(ALLOW_FINAL_IDLE),
        ),
    )
    protected_wait = decide_control(
        {"status": "running", "fingerprint": "a"},
        _terminal_base(
            terminal_transition="protected_wait",
            protected_action_required=True,
            protected_seam_scoped=True,
            protected_seam="credential/private-key",
            terminal_authority_result=_terminal_authority(ALLOW_NARROW_PROTECTED_WAIT),
        ),
    )
    successor_idle = decide_control(
        {"status": "running", "fingerprint": "a"},
        _terminal_base(
            disjoint_work_remaining=True,
            next_owner="controller-b",
            next_slice="distribution-adoption",
            successor_receipt={
                "receipt_id": "receipt-8",
                "control_id": "goal-1",
                "from_owner": "controller-a",
                "from_epoch": 7,
                "to_owner": "controller-b",
                "to_epoch": 8,
                "next_slice": "distribution-adoption",
                "readback_verified": True,
            },
        ),
    )
    ordinary_completed = decide_control(
        {"status": "running", "fingerprint": "a"},
        {"control_id": "c1", "status": "completed", "fingerprint": "done"},
    )
    unchanged_final = decide_control(
        {"status": "completed", "fingerprint": "done"},
        _terminal_base(
            terminal_transition="final",
            grand_goal_final=True,
            final_acceptance_authorized=True,
            terminal_authority_result=_terminal_authority(ALLOW_FINAL_IDLE),
        ),
    )
    if (
        started["action"] != "start"
        or unchanged["action"] != "none"
        or denied["code"] != "maestro_authority_unavailable"
        or denied["launch"]
        or blocked_third_attempt["action"] != "stop_and_replan"
        or blocked_third_attempt["code"]
        != "BLOCKED_FOR_FABLE5_NF_M10_SILENT_THIRD_ACCEPTANCE_ATTEMPT"
        or blocked_third_attempt["launch"]
        or changed_hypothesis["code"] != "state_transition"
        or cost_replan["code"] != "FABLE5_M10_COST_CHECKPOINT_NO_DELTA_REPLAN_REQUIRED"
        or corrected_premise["code"]
        != "FABLE5_M10_USER_PREMISE_CORRECTION_REPLAN_REQUIRED"
        or rejected_idle["terminal_decision"] != REJECT_IDLE_DISJOINT_WORK_UNASSIGNED
        or final_idle["terminal_decision"] != ALLOW_FINAL_IDLE
        or protected_wait["terminal_decision"] != ALLOW_NARROW_PROTECTED_WAIT
        or successor_idle["terminal_decision"] != ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR
        or ordinary_completed["code"] != "terminal_completed"
        or unchanged_final["terminal_decision"] != ALLOW_FINAL_IDLE
        or any(result["deliver_heartbeat"] for result in (started, unchanged, denied))
    ):
        print("FAIL heartbeat Hermes behavioral fixtures")
        return 1
    print("PASS heartbeat Hermes runtime contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
