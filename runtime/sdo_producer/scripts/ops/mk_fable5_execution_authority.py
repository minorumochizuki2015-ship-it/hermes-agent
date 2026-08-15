#!/usr/bin/env python3
"""Authorize exactly one user-approved paid Fable5 operation.

This guard never launches Fable5. It protects only prompt submission, session
resume, or follow-up submission. Prompt drafting, CLI preparation, read-only
inspection, and ordinary local work remain outside this Authority Gate.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import requirement_anchor_semantic as semantic


CONTRACT_VERSION = "fable5_execution_authority.v1"
OPERATIONS = {
    "submit_initial_prompt",
    "resume_session",
    "send_followup_prompt",
}
CONTRACT_FIELDS = {
    "contract_version",
    "provider",
    "operation",
    "target_model",
    "prompt_ref",
    "prompt_revision",
    "prompt_origin",
    "dispatch_origin",
    "candidate_manifest",
    "user_session",
    "identity_attestation",
    "codex_derived_summary_substituted",
    "cli_or_pack_preparation_treated_as_approval",
    "user_review",
    "approval",
    "dispatch_policy",
}
USER_REVIEW_FIELDS = {
    "exact_prompt_shown_in_full",
    "target_model_and_paid_execution_shown",
    "user_visual_review_completed",
    "model_identity_state",
}
APPROVAL_FIELDS = {
    "state",
    "source",
    "approval_id",
    "approved_operation",
    "approved_target_model",
    "approved_prompt_ref",
    "approved_prompt_revision",
    "paid_execution_acknowledged",
    "single_use",
    "consumption_state",
    "use_count",
}
DISPATCH_POLICY_FIELDS = {
    "attempt_number",
    "automatic_start",
    "automatic_retry",
    "reuse_prior_approval",
}
USER_SESSION_FIELDS = {"state", "source", "receipt_ref"}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_contract(value: Any, *, required: bool = True) -> list[str]:
    """Return exact blockers for the Fable5 paid-execution Authority Gate."""
    if value is None and not required:
        return []
    if not isinstance(value, dict) or set(value) != CONTRACT_FIELDS:
        return ["BLOCKED_FOR_FABLE5_EXECUTION_AUTHORIZATION_CONTRACT_REQUIRED"]

    blocks: list[str] = []
    if (
        value.get("contract_version") != CONTRACT_VERSION
        or value.get("provider") != "fable5"
        or value.get("operation") not in OPERATIONS
        or value.get("target_model") != "fable5-ultra"
        or not _nonempty(value.get("prompt_ref"))
        or not _nonempty(value.get("prompt_revision"))
    ):
        blocks.append("BLOCKED_FOR_FABLE5_EXECUTION_AUTHORIZATION_CONTRACT_REQUIRED")

    if (
        value.get("prompt_origin") != "user_authored_or_user_approved"
        or value.get("codex_derived_summary_substituted") is not False
    ):
        blocks.append("BLOCKED_FOR_FABLE5_USER_INSTRUCTION_SUBSTITUTION")
    if value.get("cli_or_pack_preparation_treated_as_approval") is not False:
        blocks.append("BLOCKED_FOR_FABLE5_EXPLICIT_USER_EXECUTION_APPROVAL_REQUIRED")

    dispatch_origin = value.get("dispatch_origin")
    user_session = value.get("user_session")
    direct_owned = (
        dispatch_origin == "user_direct"
        and isinstance(user_session, dict)
        and set(user_session) == USER_SESSION_FIELDS
        and user_session.get("state") == "user_owned"
        and user_session.get("source") in semantic.EXTERNAL_SOURCES
        and _nonempty(user_session.get("receipt_ref"))
    )
    if dispatch_origin not in semantic.DISPATCH_ORIGINS:
        blocks.append("BLOCKED_FOR_FABLE5_DISPATCH_ORIGIN_INVALID")
    if (
        not isinstance(user_session, dict)
        or set(user_session) != USER_SESSION_FIELDS
        or user_session.get("state") not in {
            "user_owned",
            "not_user_owned",
            "ambiguous",
        }
        or user_session.get("source") not in semantic.IDENTITY_SOURCES
    ):
        blocks.append("BLOCKED_FOR_FABLE5_USER_SESSION_ATTESTATION_INVALID")

    review = value.get("user_review")
    if (
        not isinstance(review, dict)
        or set(review) != USER_REVIEW_FIELDS
        or review.get("exact_prompt_shown_in_full") is not True
        or review.get("target_model_and_paid_execution_shown") is not True
        or review.get("user_visual_review_completed") is not True
        or review.get("model_identity_state") != "user_visually_verified_fable5_ultra"
    ):
        blocks.append("BLOCKED_FOR_FABLE5_USER_PROMPT_MODEL_COST_REVIEW_REQUIRED")

    approval = value.get("approval")
    if not direct_owned:
        if (
            not isinstance(approval, dict)
            or set(approval) != APPROVAL_FIELDS
            or approval.get("state") != "explicitly_approved"
            or approval.get("source") != "direct_user_instruction_after_exact_prompt_review"
            or not _nonempty(approval.get("approval_id"))
            or approval.get("paid_execution_acknowledged") is not True
            or approval.get("single_use") is not True
        ):
            blocks.append("BLOCKED_FOR_FABLE5_EXPLICIT_USER_EXECUTION_APPROVAL_REQUIRED")
        elif (
            approval.get("approved_operation") != value.get("operation")
            or approval.get("approved_target_model") != value.get("target_model")
            or approval.get("approved_prompt_ref") != value.get("prompt_ref")
            or approval.get("approved_prompt_revision") != value.get("prompt_revision")
        ):
            blocks.append("BLOCKED_FOR_FABLE5_APPROVAL_SCOPE_MISMATCH")
    elif approval is not None:
        blocks.append("BLOCKED_FOR_FABLE5_USER_DIRECT_SEPARATE_APPROVAL_ARTIFACT_UNEXPECTED")

    dispatch = value.get("dispatch_policy")
    if (
        not isinstance(dispatch, dict)
        or set(dispatch) != DISPATCH_POLICY_FIELDS
        or dispatch.get("attempt_number") != 1
        or dispatch.get("automatic_start") is not False
        or dispatch.get("automatic_retry") is not False
        or dispatch.get("reuse_prior_approval") is not False
    ):
        blocks.append("BLOCKED_FOR_FABLE5_SINGLE_USE_APPROVAL_REQUIRED")
    if not direct_owned and (
        not isinstance(approval, dict)
        or approval.get("consumption_state") != "pending"
        or approval.get("use_count") != 0
    ):
        blocks.append("BLOCKED_FOR_FABLE5_SINGLE_USE_APPROVAL_REQUIRED")
    return sorted(set(blocks))


def selection(value: Any, *, required: bool = True) -> dict[str, Any]:
    contract_blocks = check_contract(value, required=required)
    payload = value if isinstance(value, dict) else {}
    approval = payload.get("approval")
    dispatch_origin = payload.get("dispatch_origin", "ambiguous")
    session = payload.get("user_session", {})
    direct_owned = (
        dispatch_origin == "user_direct"
        and isinstance(session, dict)
        and session.get("state") == "user_owned"
        and session.get("source") in semantic.EXTERNAL_SOURCES
    )
    semantic_result = (
        semantic.check_boundary(
            "machine_dispatched_paid_prompt_admission",
            candidate_manifest=semantic.resolve_candidate_manifest(
                payload.get("candidate_manifest")
            ),
            identity_attestation=payload.get("identity_attestation"),
            route_class="protected",
            dispatch_origin=dispatch_origin,
            paid_work=True,
            approval_artifact_present=isinstance(approval, dict),
            user_owned_session=session.get("state") == "user_owned",
            user_session_source=session.get("source", "self_declared"),
            authority_gate_required=True,
            authority_gate_satisfied=direct_owned or not contract_blocks,
        )
        if dispatch_origin in {"machine_dispatched", "ambiguous"}
        else {
            "blocks": semantic.approval_scope_blocks(
                dispatch_origin=dispatch_origin,
                paid_work=True,
                approval_artifact_present=isinstance(approval, dict),
                user_owned_session=session.get("state") == "user_owned",
                user_session_source=session.get("source", "self_declared"),
                authority_gate_required=True,
                authority_gate_satisfied=direct_owned or not contract_blocks,
            ),
            "warnings": [],
            "observations": [],
        }
    )
    blocks = sorted(set(contract_blocks + semantic_result["blocks"]))
    approval = value.get("approval", {}) if isinstance(value, dict) else {}
    approval_transition = None
    if not blocks:
        if direct_owned:
            approval_transition = {
                "contract_version": "fable5_execution_authority_continuation.v1",
                "apply_to": "direct_user_operation",
                "approval_id": None,
                "before": {"dispatch_origin": "user_direct"},
                "after": {"dispatch_origin": "user_direct_consumed_once"},
            }
        else:
            approval_transition = {
                "contract_version": "fable5_execution_authority_continuation.v1",
                "apply_to": "fable5_execution_authorization.approval",
                "approval_id": approval.get("approval_id"),
                "before": {"consumption_state": "pending", "use_count": 0},
                "after": {"consumption_state": "consumed", "use_count": 1},
            }
    return {
        "authority_gate": True,
        "gate_class": "Authority Gate",
        "protected_operation": "paid_provider_or_credit_consuming_action",
        "decision": (
            "ALLOW_ONE_USER_APPROVED_FABLE5_OPERATION"
            if not blocks
            else "HOLD_FABLE5_OPERATION_FOR_EXPLICIT_USER_APPROVAL"
        ),
        "single_use": not blocks,
        "semantic_requirements_compiled": (
            not semantic_result["blocks"]
            if dispatch_origin in {"machine_dispatched", "ambiguous"}
            else None
        ),
        "dispatch_origin": dispatch_origin,
        "user_direct_session_owned": direct_owned,
        "identity_observations": semantic_result.get("observations", []),
        "approval_transition": approval_transition,
        "local_non_fable_work_continues": True,
        "blocks": blocks,
        "non_claims": [
            "no_fable5_invocation_performed",
            "no_approval_inferred_from_cli_or_launch_pack_preparation",
            "no_codex_derived_summary_treated_as_user_instruction",
            "no_automatic_retry_or_resume_authorized",
        ],
    }


def dispatch_once(value: Any, provider: Any) -> dict[str, Any]:
    """Apply the paid-operation gate immediately before a provider callback.

    This is the source-side protected-launcher adapter.  The callback is the
    downstream launcher's provider boundary; it is deliberately injected so
    local tests can prove ordering without contacting Fable5.  An invalid or
    already-consumed approval returns before the callback is touched.  A
    valid approval is copied and transitioned to consumed before exactly one
    callback opportunity is made; retries must present a new contract.
    """
    decision = selection(value)
    result = {
        **decision,
        "provider_invoked": False,
        "provider_result": None,
        "consumed_authority": None,
    }
    if decision["blocks"]:
        return result
    if not callable(provider):
        result["blocks"] = ["BLOCKED_FOR_FABLE5_PROVIDER_CALLBACK_MISSING"]
        result["decision"] = "HOLD_FABLE5_OPERATION_FOR_EXPLICIT_USER_APPROVAL"
        result["single_use"] = False
        return result

    consumed = copy.deepcopy(value)
    transition = decision["approval_transition"]
    approval = consumed.get("approval")
    if not isinstance(transition, dict):
        result["blocks"] = ["BLOCKED_FOR_FABLE5_SINGLE_USE_APPROVAL_REQUIRED"]
        result["decision"] = "HOLD_FABLE5_OPERATION_FOR_EXPLICIT_USER_APPROVAL"
        result["single_use"] = False
        return result
    if transition.get("apply_to") == "fable5_execution_authorization.approval":
        if not isinstance(approval, dict):
            result["blocks"] = ["BLOCKED_FOR_FABLE5_SINGLE_USE_APPROVAL_REQUIRED"]
            result["decision"] = "HOLD_FABLE5_OPERATION_FOR_EXPLICIT_USER_APPROVAL"
            result["single_use"] = False
            return result
        approval.update(transition["after"])
    result["consumed_authority"] = {
        "contract_version": transition["contract_version"],
        "approval_id": transition["approval_id"],
        "before": transition["before"],
        "after": transition["after"],
    }
    result["provider_result"] = provider(consumed)
    result["provider_invoked"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    packet_path = Path(args.packet)
    if not packet_path.exists():
        result = selection(None)
    else:
        try:
            value = json.loads(packet_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = None
        result = selection(value)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(result["decision"])
        for block in result["blocks"]:
            print(block)
    return 0 if not result["blocks"] else 1


if __name__ == "__main__":
    sys.exit(main())
