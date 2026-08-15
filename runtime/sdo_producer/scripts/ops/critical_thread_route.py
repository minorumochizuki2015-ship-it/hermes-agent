#!/usr/bin/env python3
"""Executable pre-create/post-create admission for critical Codex threads.

This source consumer does not create a thread. It returns the exact model and
effort arguments that the CMD must pass to the supported thread tool, then
requires the created child identity to be read back before work or a final
verdict is consumed. Ordinary bounded work remains outside the critical route.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requirement_anchor_semantic as semantic

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "research/mk675/fable5_decision_os/critical_thread_route.v1.json"
ROUTE_FIELDS = {
    "record_type", "route_id", "role_class", "model_route_authority",
    "required_model", "required_effort", "actual_model", "actual_effort",
    "final_verdict_authority", "paid_operation", "paid_operation_authority",
    "one_writer_target", "child_bootstrap", "post_create_readback",
    "post_create_mismatch_stop", "elevated_effort_justification",
    "issued_at", "expires_at", "rollback",
}
OPTIONAL_ROUTE_FIELDS = {"candidate_manifest"}
OPTIONAL_ROUTE_FIELDS |= {
    "next_operation", "surface_kind", "origin_channel", "peer_packet_provenance",
    "directive_origin", "content_authority_claim", "literal_authority_claim",
    "self_asserts_bindingness",
    "relabeled_planning_path_replay", "invalid_one_writer_target_replay",
}
APP_BOUND_CMD_SURFACE = "app_bound_cmd"
KNOWN_SURFACE_KINDS = frozenset({APP_BOUND_CMD_SURFACE, "codex_internal", "ordinary_worker"})
APP_BOUND_CMD_BLOCK = "BLOCKED_FOR_CRITICAL_ROUTE_APP_BOUND_CMD"
ORIGIN_CHANNELS = frozenset({
    "direct_user_turn_on_primary_cmd_session",
    "peer_cmd_transport_readback",
    "tool_result",
    "relayed_pasted_packet_body",
    "notification",
})
SOVEREIGN_ORIGIN_CHANNEL = "direct_user_turn_on_primary_cmd_session"
PEER_CMD_DIRECTIVE_BINDING = "BLOCKED_FOR_HEARTBEAT_PEER_CMD_DIRECTIVE_BINDING"
CMD_ORIGIN_CHANNEL_REQUIRED = "BLOCKED_FOR_CRITICAL_ROUTE_CMD_ORIGIN_CHANNEL_REQUIRED"
CMD_ORIGIN_CHANNEL_INVALID = "BLOCKED_FOR_CRITICAL_ROUTE_CMD_ORIGIN_CHANNEL_INVALID"
CMD_PEER_PACKET_PROVENANCE_REQUIRED = (
    "BLOCKED_FOR_CRITICAL_ROUTE_CMD_PEER_PACKET_PROVENANCE_REQUIRED"
)
CMD_ORIGIN_PROVENANCE_MISMATCH = (
    "BLOCKED_FOR_CRITICAL_ROUTE_CMD_ORIGIN_PROVENANCE_MISMATCH"
)
PEER_PACKET_PROVENANCE_FIELDS = frozenset({
    "packet_id", "source_cmd_epoch_id", "source_surface", "origin_channel",
    "payload_digest",
})
ADVISORY_ROUTE_BLOCKS = frozenset({
    APP_BOUND_CMD_BLOCK,
    CMD_ORIGIN_CHANNEL_REQUIRED,
    CMD_ORIGIN_CHANNEL_INVALID,
    CMD_PEER_PACKET_PROVENANCE_REQUIRED,
    CMD_ORIGIN_PROVENANCE_MISMATCH,
    "BLOCKED_FOR_CRITICAL_THREAD_ROUTE_ID_REQUIRED",
    "BLOCKED_FOR_ONE_WRITER_TARGET_REQUIRED",
    "BLOCKED_FOR_CHILD_BOOTSTRAP_IDENTITY_REQUIRED",
    "BLOCKED_FOR_PRE_CREATE_READBACK_STATE_INVALID",
    PEER_CMD_DIRECTIVE_BINDING,
})


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def _hex_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _cmd_origin_blocks(value: dict[str, Any], *, operation_capable: bool) -> list[str]:
    if not operation_capable:
        return []
    blocks: list[str] = []
    origin_channel = value.get("origin_channel")
    if not isinstance(origin_channel, str) or not origin_channel:
        blocks.append(CMD_ORIGIN_CHANNEL_REQUIRED)
    elif origin_channel not in ORIGIN_CHANNELS:
        blocks.append(CMD_ORIGIN_CHANNEL_INVALID)
    elif origin_channel != SOVEREIGN_ORIGIN_CHANNEL:
        provenance = value.get("peer_packet_provenance")
        if not isinstance(provenance, dict) or set(provenance) != PEER_PACKET_PROVENANCE_FIELDS:
            blocks.append(CMD_PEER_PACKET_PROVENANCE_REQUIRED)
        elif (
            provenance.get("origin_channel") != origin_channel
            or not all(
                isinstance(provenance.get(field), str)
                and bool(provenance[field].strip())
                for field in ("packet_id", "source_cmd_epoch_id", "source_surface")
            )
            or not _hex_digest(provenance.get("payload_digest"))
        ):
            blocks.append(CMD_ORIGIN_PROVENANCE_MISMATCH)
    if (
        value.get("directive_origin")
        or value.get("content_authority_claim")
        or value.get("literal_authority_claim")
        or value.get("self_asserts_bindingness") is True
    ) and value.get("origin_channel") != SOVEREIGN_ORIGIN_CHANNEL:
        blocks.append(PEER_CMD_DIRECTIVE_BINDING)
    return blocks


def semantic_compilation_blocks(candidate: Any, *, root: Path = ROOT) -> list[str]:
    """Validate the fixed requirements graph without gating safe local work."""
    try:
        canonical = load(POLICY_PATH if root == ROOT else root / POLICY_PATH.relative_to(ROOT))
    except (OSError, json.JSONDecodeError):
        return ["BLOCKED_FOR_CRITICAL_THREAD_ROUTE_POLICY_MISSING"]
    if not isinstance(candidate, dict):
        return ["BLOCKED_FOR_SEMANTIC_REQUIREMENT_COMPILATION_REQUIRED"]

    blocks: list[str] = []
    exact = (
        "record_type", "policy_id", "requirements_seal_sha256",
        "requirements_classification", "critical_role_classes",
        "ordinary_role_classes", "critical_route", "ordinary_luna_route",
        "elevated_sol_efforts", "required_lifecycle", "fixed_invariants",
        "optimizable_topology_choices", "admission_boundaries", "consumer_refs",
        "catalog_paths", "rollback", "expiry",
    )
    if set(candidate) != set(exact):
        blocks.append("BLOCKED_FOR_SEMANTIC_REQUIREMENT_COMPILATION_INCOMPLETE")
    for key in exact:
        if candidate.get(key) != canonical.get(key):
            blocks.append(f"BLOCKED_FOR_SEMANTIC_REQUIREMENT_MUTATION:{key}")

    lifecycle = candidate.get("required_lifecycle", [])
    for edge in (
        ("CMD_READBACK_AND_RELEASE", "WORKER_DISPATCH"),
        ("CMD_CONSUME_RETURN", "AUDIT_REQUIRED_OR_REASONED_NOT_REQUIRED"),
        ("INDEPENDENT_AUDIT_WHEN_REQUIRED", "CMD_CONSUME_AUDIT"),
        ("CMD_CONSUME_AUDIT", "REMEDIATE_OR_INTEGRATE"),
    ):
        try:
            if lifecycle.index(edge[0]) + 1 != lifecycle.index(edge[1]):
                blocks.append(f"BLOCKED_FOR_FIXED_LIFECYCLE_EDGE_MISSING:{edge[0]}->{edge[1]}")
        except (AttributeError, ValueError):
            blocks.append(f"BLOCKED_FOR_FIXED_LIFECYCLE_EDGE_MISSING:{edge[0]}->{edge[1]}")

    invariants = set(candidate.get("fixed_invariants", []))
    if "exact_single_use_paid_operation_approval" not in invariants:
        blocks.append("BLOCKED_FOR_EXACT_PAID_OPERATION_APPROVAL_REQUIREMENT_MISSING")
    if not candidate.get("optimizable_topology_choices"):
        blocks.append("BLOCKED_FOR_OPTIMIZABLE_TOPOLOGY_CHOICES_HIDDEN")
    for path in candidate.get("catalog_paths", {}).values() if isinstance(candidate.get("catalog_paths"), dict) else []:
        if not isinstance(path, str) or not (root / path).is_file():
            blocks.append(f"BLOCKED_FOR_CATALOG_PATH_MISMATCH:{path}")
    for consumer in candidate.get("consumer_refs", []):
        if not isinstance(consumer, str):
            blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROUTE_CONSUMER_MISSING")
            continue
        relative = consumer.split(" ", 1)[0]
        if not (root / relative).is_file():
            blocks.append(f"BLOCKED_FOR_CRITICAL_THREAD_ROUTE_CONSUMER_MISSING:{relative}")
    return sorted(set(blocks))


def evaluate_route(value: Any, *, phase: str, now: datetime | None = None) -> dict[str, Any]:
    policy = load(POLICY_PATH)
    blocks: list[str] = []
    if (
        not isinstance(value, dict)
        or not ROUTE_FIELDS <= set(value)
        or set(value) - ROUTE_FIELDS - OPTIONAL_ROUTE_FIELDS
        or value.get("record_type") != "critical_thread_route.v1"
    ):
        blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROUTE_CONTRACT_REQUIRED")
        value = value if isinstance(value, dict) else {}

    role = value.get("role_class")
    declared_surface_kind = value.get("surface_kind")
    surface_kind = declared_surface_kind
    if surface_kind is None:
        if role == "cmd":
            surface_kind = APP_BOUND_CMD_SURFACE
        elif role in policy["critical_role_classes"]:
            surface_kind = policy["critical_route"].get("surface_kind")
        elif role in policy["ordinary_role_classes"]:
            surface_kind = "ordinary_worker"
    if declared_surface_kind is not None and declared_surface_kind not in KNOWN_SURFACE_KINDS:
        blocks.append("BLOCKED_FOR_CRITICAL_ROUTE_SURFACE_KIND_INVALID")
    app_bound_cmd = surface_kind == APP_BOUND_CMD_SURFACE
    critical = role in policy["critical_role_classes"] and not app_bound_cmd
    ordinary = role in policy["ordinary_role_classes"] and not app_bound_cmd
    operation_capable = (
        phase == "pre_create"
        or isinstance(value.get("next_operation"), str)
        and bool(value.get("next_operation").strip())
    )
    blocks.extend(_cmd_origin_blocks(value, operation_capable=operation_capable))
    if not app_bound_cmd and not critical and not ordinary:
        blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROLE_CLASS_INVALID")
    if app_bound_cmd:
        blocks.append(APP_BOUND_CMD_BLOCK)
    if not isinstance(value.get("route_id"), str) or not value.get("route_id"):
        blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROUTE_ID_REQUIRED")
    if not isinstance(value.get("final_verdict_authority"), bool) or not isinstance(value.get("paid_operation"), bool):
        blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROUTE_BOOLEAN_FIELDS_INVALID")

    required_model = value.get("required_model")
    required_effort = value.get("required_effort")
    actual_model = value.get("actual_model")
    actual_effort = value.get("actual_effort")
    authority = value.get("model_route_authority")
    if critical:
        if not all(isinstance(item, str) and item for item in (required_model, required_effort, actual_model, actual_effort)):
            blocks.append("BLOCKED_FOR_CRITICAL_THREAD_EXPLICIT_MODEL_REQUIRED")
        if authority not in {"user_explicit_correction", "current_active_policy"}:
            blocks.append("BLOCKED_FOR_CRITICAL_THREAD_MODEL_ROUTE_AUTHORITY_MISSING")
        if required_model != policy["critical_route"]["model"] or actual_model != required_model:
            blocks.append("BLOCKED_FOR_CRITICAL_THREAD_SOL_ROUTE_REQUIRED")
        if required_effort not in {policy["critical_route"]["effort"], *policy["elevated_sol_efforts"]} or actual_effort != required_effort:
            blocks.append("BLOCKED_FOR_CRITICAL_THREAD_EFFORT_MISMATCH")
        if required_effort in policy["elevated_sol_efforts"]:
            justification = value.get("elevated_effort_justification")
            justification_expiry = parse_time(justification.get("expires_at")) if isinstance(justification, dict) else None
            if not isinstance(justification, dict) or set(justification) != {"reason", "authority", "expires_at"} or not justification.get("reason") or justification.get("authority") not in {"user_explicit_correction", "current_active_policy"} or justification_expiry is None or justification_expiry <= (now or datetime.now(timezone.utc)):
                blocks.append("BLOCKED_FOR_UNJUSTIFIED_SOL_ELEVATED_EFFORT")
        elif value.get("elevated_effort_justification") is not None:
            blocks.append("BLOCKED_FOR_UNNEEDED_SOL_ELEVATED_EFFORT_JUSTIFICATION")
    elif ordinary:
        expected = policy["ordinary_luna_route"]
        if (required_model, required_effort, actual_model, actual_effort) != (expected["model"], expected["effort"], expected["model"], expected["effort"]):
            blocks.append("BLOCKED_FOR_ORDINARY_LUNA_ROUTE_INVALID")

    if value.get("final_verdict_authority") is True and (not critical or actual_model != "gpt-5.6-sol"):
        blocks.append("BLOCKED_FOR_LUNA_FINAL_VERDICT_FORBIDDEN")
    if value.get("paid_operation") is True and (role != "approval_handoff" or value.get("paid_operation_authority") != "exact_single_use_required"):
        blocks.append("BLOCKED_FOR_EXACT_PAID_OPERATION_APPROVAL_REQUIRED")
    if value.get("paid_operation") is False and value.get("paid_operation_authority") != "not_applicable":
        blocks.append("BLOCKED_FOR_PAID_OPERATION_AUTHORITY_FLAG_MISMATCH")

    writer = value.get("one_writer_target")
    if not isinstance(writer, dict) or set(writer) != {"worktree", "owner_thread_id"} or not all(isinstance(writer.get(key), str) and writer[key] for key in writer):
        blocks.append("BLOCKED_FOR_ONE_WRITER_TARGET_REQUIRED")
    bootstrap = value.get("child_bootstrap")
    if not isinstance(bootstrap, dict) or set(bootstrap) != {"model", "effort", "identity_readback_required"} or bootstrap.get("model") != required_model or bootstrap.get("effort") != required_effort or bootstrap.get("identity_readback_required") is not True:
        blocks.append("BLOCKED_FOR_CHILD_BOOTSTRAP_IDENTITY_REQUIRED")
    if value.get("post_create_mismatch_stop") is not True:
        blocks.append("BLOCKED_FOR_POST_CREATE_MISMATCH_STOP_REQUIRED")
    if not isinstance(value.get("rollback"), str) or not value.get("rollback"):
        blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROUTE_ROLLBACK_MISSING")
    issued, expires = parse_time(value.get("issued_at")), parse_time(value.get("expires_at"))
    moment = now or datetime.now(timezone.utc)
    if not issued or not expires or issued > moment or issued >= expires or expires <= moment:
        blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROUTE_EXPIRED")

    readback = value.get("post_create_readback")
    if phase == "pre_create":
        if not isinstance(readback, dict) or set(readback) != {"state", "child_thread_id", "actual_model", "actual_effort"} or readback.get("state") != "pending" or any(readback.get(key) is not None for key in ("child_thread_id", "actual_model", "actual_effort")):
            blocks.append("BLOCKED_FOR_PRE_CREATE_READBACK_STATE_INVALID")
    elif phase == "post_create":
        if not isinstance(readback, dict) or set(readback) != {"state", "child_thread_id", "actual_model", "actual_effort"} or readback.get("state") != "verified" or not isinstance(readback.get("child_thread_id"), str) or not readback.get("child_thread_id") or readback.get("actual_model") != required_model or readback.get("actual_effort") != required_effort:
            blocks.append("BLOCKED_FOR_POST_CREATE_IDENTITY_MISMATCH")
    else:
        blocks.append("BLOCKED_FOR_CRITICAL_THREAD_ROUTE_PHASE_INVALID")

    semantic_admission: dict[str, Any] | None = None
    if phase == "pre_create" and role == "bounded_implementation":
        semantic_admission = semantic.check_boundary(
            "codex_implementation_dispatch",
            candidate_manifest=semantic.resolve_candidate_manifest(
                value.get("candidate_manifest")
            ),
            identity_attestation={
                "configured": {
                    "value": required_model,
                    "source": "configuration",
                    "ref": "critical_thread_route.required_model",
                },
                "observed": {
                    "value": actual_model,
                    "source": "external_harness",
                    "ref": "critical_thread_route.actual_model",
                },
            },
            route_class="unqualified_supervised_local",
            dispatch_origin="machine_dispatched",
            paid_work=False,
            approval_artifact_present=False,
            authority_gate_required=False,
            authority_gate_satisfied=True,
        )
        blocks.extend(semantic_admission["blocks"])

    advisory_diagnostics = sorted(set(blocks) & ADVISORY_ROUTE_BLOCKS)
    protected_diagnostics = sorted(set(blocks) - set(ADVISORY_ROUTE_BLOCKS))
    advisory_route_rejection = bool(advisory_diagnostics) and not protected_diagnostics
    app_bound_cmd_rejection = (
        app_bound_cmd
        and APP_BOUND_CMD_BLOCK in advisory_diagnostics
        and advisory_route_rejection
    )
    allowed = not blocks
    if app_bound_cmd_rejection:
        decision = "REPLAN_TO_ROUTING_TABLE_CMD_PRIMARY_OR_ALTERNATE"
    elif advisory_route_rejection:
        decision = "REPLAN_TO_VALID_CRITICAL_ROUTE"
    elif allowed and phase == "pre_create":
        decision = "ALLOW_THREAD_CREATE"
    elif allowed:
        decision = "ALLOW_CHILD_WORK_OR_VERDICT_CONSUMPTION"
    else:
        decision = "STOP_CRITICAL_THREAD_ROUTE"
    return {
        "record_type": "critical_thread_route_decision.v1",
        "phase": phase,
        "decision": decision,
        "dispatch_args": {"model": required_model, "thinking": required_effort} if allowed and phase == "pre_create" else None,
        "critical_role": critical,
        "app_bound_cmd": app_bound_cmd,
        "ordinary_bounded_luna": ordinary and allowed,
        "safe_local_read_only_planning_continues": True,
        "requirement_semantic_admission": semantic_admission,
        "blocks": sorted(set(blocks)),
        "origin_channel": value.get("origin_channel") if operation_capable else None,
        "peer_packet_provenance_bound": (
            operation_capable
            and isinstance(value.get("origin_channel"), str)
            and value.get("origin_channel") != SOVEREIGN_ORIGIN_CHANNEL
            and isinstance(value.get("peer_packet_provenance"), dict)
            and not any(
                block in blocks
                for block in (CMD_PEER_PACKET_PROVENANCE_REQUIRED, CMD_ORIGIN_PROVENANCE_MISMATCH)
            )
        ),
        "advisory_recorded": advisory_route_rejection,
        "advisory_diagnostics": advisory_diagnostics,
        "protected_diagnostics": protected_diagnostics,
        "preserved_next_operation": value.get("next_operation") if advisory_route_rejection and operation_capable else None,
        "next_operation_preserved": advisory_route_rejection and operation_capable and isinstance(value.get("next_operation"), str) and bool(value.get("next_operation")),
        "work_continuation_allowed": advisory_route_rejection,
        "state": "CONTINUE" if advisory_route_rejection else "STOP",
        "non_wait": advisory_route_rejection,
        "typed_diagnostic_scope": (
            "app_bound_cmd_surface_only"
            if app_bound_cmd
            else "origin_channel_and_peer_packet_provenance_binding_only"
            if any(block in advisory_diagnostics for block in (
                CMD_ORIGIN_CHANNEL_REQUIRED,
                CMD_ORIGIN_CHANNEL_INVALID,
                CMD_PEER_PACKET_PROVENANCE_REQUIRED,
                CMD_ORIGIN_PROVENANCE_MISMATCH,
                PEER_CMD_DIRECTIVE_BINDING,
            ))
            else None
        ),
        "blocked_operations": [] if advisory_route_rejection else protected_diagnostics,
        "protected_boundary_stop": bool(protected_diagnostics),
        "protected_boundary_stops_only": True,
        "dispatch_valid": allowed and phase == "pre_create",
        "operation_applied": allowed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", type=Path)
    parser.add_argument("--phase", choices=("pre_create", "post_create"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.route and args.phase:
        result = evaluate_route(load(args.route), phase=args.phase)
    else:
        parser.error("use --route with --phase")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not result["blocks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
