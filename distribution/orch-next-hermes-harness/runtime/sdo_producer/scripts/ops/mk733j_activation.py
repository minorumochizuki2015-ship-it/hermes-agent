#!/usr/bin/env python3
"""Local-only MK733J-N receipt bootstrap and explicit shadow rollback."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mk733j_decision_os as decision

REPO = Path(__file__).resolve().parents[2]
TRUSTED_AUTHORITY_DIR = REPO / "research/mk675/fable5_decision_os/authorities"
TRUSTED_AUTHORITY_REGISTRY = REPO / "research/mk675/fable5_decision_os/mk733j_n_trusted_activation_authorities.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True); handle.write("\n")
    os.replace(tmp, path)


def paths(state_dir: str | None, test_isolated: bool) -> tuple[Path, Path]:
    directory = Path(state_dir).resolve() if state_dir else REPO / ".codex/decision-os"
    if test_isolated:
        if not state_dir or REPO in directory.parents or directory == REPO:
            raise ValueError("test activation state must be an explicit directory outside the repository")
    return directory / "activation-state.json", directory / "current-receipt.json"


def trusted_registry() -> tuple[dict[str, Any] | None, list[str]]:
    """Load the committed, digest-bound authority admission registry.

    This is a repo-review trust anchor, not a cryptographic identity claim.  An
    empty registry deliberately makes normal activation impossible.
    """
    try:
        implementation = load(decision.IMPLEMENTATION)
        binding = implementation.get("activation_authority", {})
        expected_ref = str(TRUSTED_AUTHORITY_REGISTRY.relative_to(REPO))
        if (
            binding.get("trusted_registry_ref") != expected_ref
            or binding.get("trusted_registry_digest") != decision.file_digest(TRUSTED_AUTHORITY_REGISTRY)
            or binding.get("normal_activation_requires_admitted_authority") is not True
        ):
            raise ValueError
        registry = load(TRUSTED_AUTHORITY_REGISTRY)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None, ["BLOCKED_FOR_MK733J_BOOTSTRAP_TRUST_REGISTRY_INVALID"]
    rows = registry.get("trusted_authorities")
    required_registry = {
        "record_type", "registry_version", "source_class", "trusted_authorities",
        "default_activation_state", "non_claims",
    }
    row_fields = {
        "authority_ref", "activation_envelope_ref", "authority_evidence_ref",
        "authority_evidence_digest", "issuer", "issuer_class", "source_class", "scope",
    }
    if (
        set(registry) != required_registry
        or registry.get("record_type") != "mk733j_n_trusted_activation_authority_registry"
        or registry.get("registry_version") != "mk733j-trusted-activation-authorities-v1"
        or registry.get("source_class") != "committed_trust_registry"
        or not isinstance(rows, list)
        or any(not isinstance(row, dict) or set(row) != row_fields for row in rows)
        or binding.get("current_admitted_authority_count") != len(rows)
    ):
        return None, ["BLOCKED_FOR_MK733J_BOOTSTRAP_TRUST_REGISTRY_INVALID"]
    return registry, []


def authority_evidence_blocks(authority: dict[str, Any], test_isolated: bool) -> list[str]:
    ref = authority.get("authority_evidence_ref")
    if not isinstance(ref, str) or not ref:
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_EVIDENCE_INVALID"]
    path = Path(ref).resolve() if Path(ref).is_absolute() else (REPO / ref).resolve()
    if (test_isolated and REPO in path.parents) or (not test_isolated and TRUSTED_AUTHORITY_DIR.resolve() not in path.parents):
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_EVIDENCE_INVALID"]
    try:
        evidence = load(path)
    except (OSError, TypeError, json.JSONDecodeError):
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_EVIDENCE_INVALID"]
    body = dict(evidence)
    supplied = body.pop("evidence_digest", None)
    def sensitive(value: Any) -> bool:
        if isinstance(value,dict): return any("".join(ch for ch in str(key).lower() if ch.isalnum()) in {"rawprompt","prompt","transcript","hiddenreasoning","secret","credential","token"} or sensitive(item) for key,item in value.items())
        if isinstance(value,list): return any(sensitive(item) for item in value)
        return False
    expected_source = (
        "test_isolated_authority_readback"
        if test_isolated
        else "cmd_owner_authority_readback" if authority.get("issuer_class") == "cmd_owner" else "user_explicit_authority_readback"
    )
    expected = {
        "record_type": "mk733j_activation_authority_readback",
        "source_class": expected_source,
        "issuer": authority.get("issuer"),
        "issuer_class": authority.get("issuer_class"),
        "authority_ref": authority.get("authority_ref"),
        "scope": authority.get("scope"),
        "profile_request_digest": authority.get("profile_request_digest"),
        "workpack_digest": authority.get("workpack_digest"),
        "binding_record_digest": authority.get("binding_record_digest"),
        "issued_at": authority.get("issued_at"),
        "expires_at": authority.get("expires_at"),
        "approved": True,
    }
    if decision.TARGET_BOUND_FIELDS <= set(authority):
        expected.update({key: authority.get(key) for key in decision.TARGET_BOUND_FIELDS})
    if "execution_tier" in authority or "delegated_autonomy" in authority:
        expected.update({
            "execution_tier": authority.get("execution_tier"),
            "delegated_autonomy": authority.get("delegated_autonomy"),
        })
    if (
        set(evidence) != set(expected)|{"evidence_digest"}
        or sensitive(evidence)
        or any(evidence.get(key) != value for key, value in expected.items())
        or supplied != decision.digest(body)
        or supplied != authority.get("authority_evidence_digest")
    ):
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_EVIDENCE_INVALID"]
    return []


def execution_tier_blocks(request: dict[str, Any], test_isolated: bool) -> tuple[str | None, bool | None, list[str]]:
    """Require explicit production tier/delegation, with legacy isolated compatibility."""
    tier = request.get("execution_tier")
    delegated = request.get("delegated_autonomy")
    if tier is None and delegated is None and test_isolated:
        return "autonomous_profile_qualified", True, []
    if tier not in {"autonomous_profile_qualified", "authority_gate_transition"} or not isinstance(delegated, bool):
        return None, None, ["BLOCKED_FOR_MK733J_EXECUTION_TIER_INVALID"]
    if tier == "autonomous_profile_qualified" and delegated is not True:
        return None, None, ["BLOCKED_FOR_MK733J_EXECUTION_TIER_INVALID"]
    return tier, delegated, []


def request_binding_digest(request: dict[str, Any], execution_tier: str, delegated_autonomy: bool) -> str:
    """Avoid a self-reference when an authority-only request binds its envelope digest."""
    if execution_tier == "authority_gate_transition" and delegated_autonomy is False:
        return decision.authority_gate_request_digest(request)
    return decision.digest(request)


def authority_blocks(authority: dict[str, Any], authority_path: Path, request: dict[str, Any], test_isolated: bool) -> list[str]:
    required = {"authority", "approved", "issuer", "issuer_class", "authority_ref", "scope", "workpack_digest", "binding_record_digest", "profile_request_digest", "authority_evidence_source", "authority_evidence_ref", "authority_evidence_digest", "issued_at", "expires_at", "source_class", "envelope_digest"}
    tier, delegated, tier_blocks = execution_tier_blocks(request, test_isolated)
    if tier_blocks:
        return tier_blocks
    tier_fields = {"execution_tier", "delegated_autonomy"}
    if not test_isolated:
        required |= tier_fields
    elif bool(tier_fields & set(authority)):
        required |= tier_fields
    try:
        workpack = decision.current_workpack_digest()
        binding_record = decision.current_binding_record_digest()
        request_digest = request_binding_digest(request, tier, delegated)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return ["BLOCKED_FOR_MK733J_WORKPACK_BINDING_INVALID"]
    target_active = decision.target_bound_authority_active(request)
    if target_active:
        required |= decision.TARGET_BOUND_FIELDS
    if set(authority) != required or authority.get("authority") != "MK733J_ENFORCEMENT_ACTIVATION" or authority.get("approved") is not True or authority.get("scope") != "mk733j_n_local_hook_activation" or authority.get("workpack_digest") != workpack or authority.get("binding_record_digest") != binding_record or authority.get("profile_request_digest") != request_digest or (tier_fields <= required and (authority.get("execution_tier") != tier or authority.get("delegated_autonomy") is not delegated)):
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_MISSING"]
    target_blocks = decision.target_bound_blocks(
        request, allowed_command_classes=request.get("allowed_command_classes"),
        forbidden_operation_classes=request.get("forbidden_operation_classes"),
    ) if target_active else []
    if target_blocks or (target_active and any(authority.get(key) != request.get(key) for key in decision.TARGET_BOUND_FIELDS)):
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_TARGET_BINDING_INVALID"]
    if not isinstance(authority.get("issuer"), str) or not authority["issuer"] or not isinstance(authority.get("authority_ref"), str) or not authority["authority_ref"]:
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_MISSING"]
    source = authority.get("authority_evidence_source")
    if source != ("test_isolated" if test_isolated else "separate_trusted_authority_readback"):
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_MISSING"]
    copy=dict(authority); supplied=copy.pop("envelope_digest",None)
    if supplied!=decision.digest(copy):return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_DIGEST_INVALID"]
    resolved=authority_path.resolve()
    if test_isolated:
        if REPO in resolved.parents or authority.get("source_class")!="test_isolated_activation_authority" or authority.get("issuer_class")!="test_isolated":return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_SOURCE_INVALID"]
    else:
        registry, registry_blocks = trusted_registry()
        if registry_blocks:
            return registry_blocks
        row = next((item for item in registry["trusted_authorities"] if item.get("authority_ref") == authority.get("authority_ref")), None)
        expected_envelope = str(resolved.relative_to(REPO)) if REPO in resolved.parents else None
        if (
            TRUSTED_AUTHORITY_DIR.resolve() not in resolved.parents
            or authority.get("source_class") != "trusted_activation_authority_envelope"
            or authority.get("issuer_class") not in {"cmd_owner", "user_explicit"}
            or not row
            or row.get("activation_envelope_ref") != expected_envelope
            or row.get("authority_evidence_ref") != authority.get("authority_evidence_ref")
            or row.get("authority_evidence_digest") != authority.get("authority_evidence_digest")
            or row.get("issuer") != authority.get("issuer")
            or row.get("issuer_class") != authority.get("issuer_class")
            or row.get("source_class") != authority.get("source_class")
            or row.get("scope") != authority.get("scope")
        ):
            return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_SOURCE_INVALID"]
    issued = decision.parse_time(authority.get("issued_at"))
    expiry = decision.parse_time(authority.get("expires_at"))
    now = datetime.now(timezone.utc)
    if issued is None or expiry is None or issued > now or expiry <= now or issued >= expiry:
        return ["BLOCKED_FOR_MK733J_BOOTSTRAP_AUTHORITY_EXPIRED"]
    return authority_evidence_blocks(authority, test_isolated)


def bootstrap(authority: dict[str, Any], authority_path: Path, request: dict[str, Any], state_dir: str | None, test_isolated: bool) -> dict[str, Any]:
    try:
        state_path, receipt_path = paths(state_dir, test_isolated)
    except ValueError:
        return {"blocks": ["BLOCKED_FOR_MK733J_TEST_STATE_DIR_INVALID"]}
    blocks = authority_blocks(authority, authority_path, request, test_isolated)
    if blocks:
        return {"blocks": blocks}
    execution_tier, delegated_autonomy, tier_blocks = execution_tier_blocks(request, test_isolated)
    if tier_blocks:
        return {"blocks": tier_blocks}
    try:
        if execution_tier == "authority_gate_transition" and delegated_autonomy is False:
            if (
                request.get("external_protected_authority_state") != "explicitly_authorized_exact_scope"
                or request.get("external_authority_ref") != str(authority_path)
                or request.get("external_authority_digest") != authority.get("envelope_digest")
            ):
                return {"blocks": ["BLOCKED_FOR_MK733J_EXTERNAL_OR_PROTECTED_AUTHORITY_MISSING"]}
            receipt = decision.authority_gate_transition_receipt(request)
        else:
            routed = decision.route(request, test_isolated=test_isolated)
            if routed["route"] != "allow":
                return {"blocks": ["BLOCKED_FOR_MK733J_BOOTSTRAP_PROFILE_NOT_EMPIRICALLY_QUALIFIED", *routed["blockers"]]}
            # Durable pre-work receipt precedes the activation-state transition.
            receipt = decision.receipt(routed, request, "pre_work")
    except ValueError as exc:
        return {"blocks": ["BLOCKED_FOR_MK733J_BOOTSTRAP_RECEIPT_HANDSHAKE_INVALID"], "detail": str(exc)}
    atomic_json(receipt_path, receipt)
    state = {"mode": "enforce", "enforcement_active": True, "execution_tier": execution_tier, "delegated_autonomy": delegated_autonomy, "workpack_digest": decision.current_workpack_digest(), "binding_record_digest": decision.current_binding_record_digest(), "authority_digest": decision.digest(authority), "receipt_digest": receipt["receipt_digest"], "receipt_path": str(receipt_path)}
    state["state_digest"] = decision.digest(state)
    atomic_json(state_path, state)
    return {"blocks": [], "status": "LOCAL_ENFORCEMENT_BOOTSTRAPPED_NOT_RUNTIME_CLAIM", "receipt": str(receipt_path), "state": str(state_path)}


def rollback(state_dir: str | None, test_isolated: bool) -> dict[str, Any]:
    try:
        state_path, _ = paths(state_dir, test_isolated)
    except ValueError:
        return {"blocks": ["BLOCKED_FOR_MK733J_TEST_STATE_DIR_INVALID"]}
    state = {"mode": "shadow", "enforcement_active": False, "execution_tier": "normal_local_bounded_supervised", "rollback": "explicit_local_rollback"}
    state["state_digest"] = decision.digest(state)
    atomic_json(state_path, state)
    return {"blocks": [], "status": "LOCAL_SHADOW_ROLLBACK_COMPLETE", "state": str(state_path)}


def authority_gate_self_test() -> dict[str, Any]:
    """Exercise authority-only issuance/consumption without touching repo state."""
    from copy import deepcopy
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    issued = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    expires = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    target_fields = {
        "target_repository": str(decision.REPO.resolve()),
        "target_path": "docs/ops",
        "target_revision": decision.current_revision(),
        "operation": "protected_git_push",
        "operation_digest": decision.target_operation_digest(
            str(decision.REPO.resolve()), "docs/ops", decision.current_revision(), "protected_git_push"
        ),
        "rollback": "explicit_local_rollback",
        "exclusions": sorted(decision.TARGET_BOUND_EXCLUSIONS),
    }
    operation_digest = decision.digest("authority-gate-protected-operation")
    with tempfile.TemporaryDirectory(prefix="mk733j-authority-gate-") as directory:
        root = Path(directory)
        authority_path = root / "authority.json"
        evidence_path = root / "evidence.json"
        state_dir = root / "state"
        request = {
            "execution_tier": "authority_gate_transition", "delegated_autonomy": False,
            "work_id": "authority-work", "goal_ref": "authority-goal", "task_class": "authority_gate_transition",
            "risk_class": "protected", "context_digest": decision.digest("authority-context"),
            "external_protected_authority_state": "explicitly_authorized_exact_scope",
            "external_authority_ref": str(authority_path), "external_authority_digest": "pending",
            **target_fields,
            "policy_refs": ["controls/active-policy-index.json"], "non_claims": ["no_runtime_activation"],
            "receipt_ttl_seconds": 300, "allowed_tools": ["Bash"], "allowed_path_prefixes": ["docs/ops"],
            "allowed_command_classes": ["protected_git"],
            "forbidden_operation_classes": sorted(decision.PROTECTED_OPERATION_CLASSES - {"protected_git"}),
            "operation_manifest": {
                "bash_commands": [{"operation_digest": operation_digest, "command_class": "protected_git", "allowed_count": 1, "remaining": 1}],
                "mutation_classes": {}, "read_only_diagnostics": [],
            },
            "budget": {"total": 1, "remaining": 1}, "readback_required": True,
        }

        def make_authority(value: dict[str, Any]) -> dict[str, Any]:
            authority = {
                "authority": "MK733J_ENFORCEMENT_ACTIVATION", "approved": True, "issuer": "test",
                "issuer_class": "test_isolated", "authority_ref": "test-authority", "scope": "mk733j_n_local_hook_activation",
                "workpack_digest": decision.current_workpack_digest(), "binding_record_digest": decision.current_binding_record_digest(),
                "profile_request_digest": request_binding_digest(value, value["execution_tier"], value["delegated_autonomy"]),
                "authority_evidence_source": "test_isolated", "authority_evidence_ref": str(evidence_path),
                "authority_evidence_digest": "pending", "issued_at": issued, "expires_at": expires,
                "source_class": "test_isolated_activation_authority", "execution_tier": value["execution_tier"],
                "delegated_autonomy": value["delegated_autonomy"],
                **({key: value[key] for key in decision.TARGET_BOUND_FIELDS} if decision.target_bound_authority_active(value) else {}),
            }
            evidence = {
                "record_type": "mk733j_activation_authority_readback", "source_class": "test_isolated_authority_readback",
                "issuer": authority["issuer"], "issuer_class": authority["issuer_class"], "authority_ref": authority["authority_ref"],
                "scope": authority["scope"], "profile_request_digest": authority["profile_request_digest"],
                "workpack_digest": authority["workpack_digest"], "binding_record_digest": authority["binding_record_digest"],
                "issued_at": authority["issued_at"], "expires_at": authority["expires_at"], "approved": True,
                "execution_tier": authority["execution_tier"], "delegated_autonomy": authority["delegated_autonomy"],
                **({key: authority[key] for key in decision.TARGET_BOUND_FIELDS} if decision.TARGET_BOUND_FIELDS <= set(authority) else {}),
            }
            evidence["evidence_digest"] = decision.digest(evidence)
            authority["authority_evidence_digest"] = evidence["evidence_digest"]
            authority["envelope_digest"] = decision.digest(authority)
            atomic_json(evidence_path, evidence)
            atomic_json(authority_path, authority)
            return authority

        authority = make_authority(request)
        request["external_authority_digest"] = authority["envelope_digest"]
        boot = bootstrap(authority, authority_path, request, str(state_dir), True)
        receipt_path = state_dir / "current-receipt.json"
        pristine = load(receipt_path) if not boot["blocks"] else {}
        one_time = decision.consume_receipt(
            receipt_path, "pre_work", "Bash", observed_paths=["docs/ops"], command_class="protected_git",
            operation_digest=operation_digest, operation_bytes=0, operation_lines=0, test_isolated=True,
        )
        replay = decision.consume_receipt(
            receipt_path, "pre_work", "Bash", observed_paths=["docs/ops"], command_class="protected_git",
            operation_digest=operation_digest, operation_bytes=0, operation_lines=0, test_isolated=True,
        )
        atomic_json(receipt_path, pristine)
        out_of_scope = decision.consume_receipt(
            receipt_path, "pre_work", "Bash", observed_paths=["controls/active-policy-index.json"], command_class="protected_git",
            operation_digest=operation_digest, operation_bytes=0, operation_lines=0, test_isolated=True,
        )
        atomic_json(receipt_path, pristine)
        wrong_tool = decision.consume_receipt(
            receipt_path, "pre_work", "Edit", observed_paths=["docs/ops"], command_class="protected_git",
            operation_digest=operation_digest, operation_bytes=0, operation_lines=0, test_isolated=True,
        )
        atomic_json(receipt_path, pristine)
        wrong_class = decision.consume_receipt(
            receipt_path, "pre_work", "Bash", observed_paths=["docs/ops"], command_class="network",
            operation_digest=operation_digest, operation_bytes=0, operation_lines=0, test_isolated=True,
        )
        atomic_json(receipt_path, pristine)
        wrong_digest = decision.consume_receipt(
            receipt_path, "pre_work", "Bash", observed_paths=["docs/ops"], command_class="protected_git",
            operation_digest=decision.digest("different-operation"), operation_bytes=0, operation_lines=0, test_isolated=True,
        )
        tampered = deepcopy(pristine)
        tampered["allowed_path_prefixes"] = ["controls"]
        tampered["scope_policy_digest"] = decision.digest(decision.authority_gate_scope_policy(tampered))
        tampered.pop("receipt_digest", None); tampered["receipt_digest"] = decision.digest(tampered)
        atomic_json(receipt_path, tampered)
        tamper = decision.receipt_blocks(load(receipt_path), "pre_work", "Bash", test_isolated=True)
        extra = deepcopy(pristine); extra["unexpected"] = "authority-expansion"
        extra_schema = decision.receipt_blocks(extra, "pre_work", "Bash", test_isolated=True)
        stale_authority = deepcopy(authority)
        stale_authority["expires_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        stale_authority.pop("envelope_digest", None); stale_authority["envelope_digest"] = decision.digest(stale_authority)
        atomic_json(authority_path, stale_authority)
        stale_receipt = deepcopy(pristine)
        stale_receipt["external_authority_digest"] = stale_authority["envelope_digest"]
        stale_receipt["scope_policy_digest"] = decision.digest(decision.authority_gate_scope_policy(stale_receipt))
        stale_receipt.pop("receipt_digest", None); stale_receipt["receipt_digest"] = decision.digest(stale_receipt)
        stale = decision.receipt_blocks(stale_receipt, "pre_work", "Bash", test_isolated=True)
        atomic_json(authority_path, authority)

        def reseal_target_receipt(value: dict[str, Any], *, update_digest: bool = True) -> dict[str, Any]:
            result = deepcopy(value)
            if update_digest:
                result["scope_policy_digest"] = decision.digest(decision.authority_gate_scope_policy(result))
            result.pop("receipt_digest", None)
            result["receipt_digest"] = decision.digest(result)
            return result

        wrong_target = deepcopy(pristine)
        wrong_target["target_path"] = "controls"
        wrong_target["operation_digest"] = decision.target_operation_digest(
            wrong_target["target_repository"], wrong_target["target_path"], wrong_target["target_revision"], wrong_target["operation"]
        )
        wrong_target_blocks = decision.receipt_blocks(reseal_target_receipt(wrong_target), "pre_work", "Bash", test_isolated=True)
        wrong_revision = deepcopy(pristine)
        wrong_revision["target_revision"] = "0" * 40
        wrong_revision["operation_digest"] = decision.target_operation_digest(
            wrong_revision["target_repository"], wrong_revision["target_path"], wrong_revision["target_revision"], wrong_revision["operation"]
        )
        wrong_revision_blocks = decision.receipt_blocks(reseal_target_receipt(wrong_revision), "pre_work", "Bash", test_isolated=True)
        wrong_operation = deepcopy(pristine)
        wrong_operation["operation"] = "public_deploy_or_release"
        wrong_operation["operation_digest"] = decision.target_operation_digest(
            wrong_operation["target_repository"], wrong_operation["target_path"], wrong_operation["target_revision"], wrong_operation["operation"]
        )
        wrong_operation_blocks = decision.receipt_blocks(reseal_target_receipt(wrong_operation), "pre_work", "Bash", test_isolated=True)
        digest_tamper = deepcopy(pristine)
        digest_tamper["operation_digest"] = "0" * 64
        digest_tamper_blocks = decision.receipt_blocks(reseal_target_receipt(digest_tamper, update_digest=False), "pre_work", "Bash", test_isolated=True)
        broadened = deepcopy(pristine)
        broadened["allowed_command_classes"] = sorted(set(broadened["allowed_command_classes"]) | {"network"})
        broadened["forbidden_operation_classes"] = sorted(decision.PROTECTED_OPERATION_CLASSES - set(broadened["allowed_command_classes"]))
        broadened_blocks = decision.receipt_blocks(reseal_target_receipt(broadened), "pre_work", "Bash", test_isolated=True)

        combined_request = deepcopy(request)
        combined_classes = {"credential", "destructive", "runtime_release"}
        combined_request.update({
            "target_path": ".",
            "operation": decision.TARGET_BOUND_LOCAL_COMBINED_OPERATION,
            "allowed_path_prefixes": [],
            "allowed_command_classes": sorted(combined_classes),
            "forbidden_operation_classes": sorted(decision.PROTECTED_OPERATION_CLASSES - combined_classes),
        })
        combined_request["operation_digest"] = decision.target_operation_digest(
            combined_request["target_repository"], combined_request["target_path"], combined_request["target_revision"], combined_request["operation"]
        )
        combined_request["operation_manifest"] = {
            "bash_commands": [
                {"operation_digest": decision.digest(f"combined-{command_class}"), "command_class": command_class, "allowed_count": 1, "remaining": 1}
                for command_class in sorted(combined_classes)
            ],
            "mutation_classes": {}, "read_only_diagnostics": [],
        }
        combined_request["budget"] = {"total": 3, "remaining": 3}
        combined_consumption = False
        try:
            combined_authority = make_authority(combined_request)
            combined_request["external_authority_digest"] = combined_authority["envelope_digest"]
            combined_receipt = decision.authority_gate_transition_receipt(combined_request)
            combined_boundary = not decision.target_bound_blocks(combined_request) and not decision.target_bound_blocks(combined_receipt)
            combined_receipt_path = root / "combined-receipt.json"
            atomic_json(authority_path, combined_authority)
            atomic_json(combined_receipt_path, combined_receipt)
            combined_results = [
                decision.consume_receipt(
                    combined_receipt_path, "pre_work", "Bash", observed_paths=[], command_class=command_class,
                    operation_digest=row["operation_digest"], operation_bytes=0, operation_lines=0, test_isolated=True,
                )
                for command_class, row in ((row["command_class"], row) for row in combined_request["operation_manifest"]["bash_commands"])
            ]
            combined_replay = decision.consume_receipt(
                combined_receipt_path, "pre_work", "Bash", observed_paths=[], command_class="credential",
                operation_digest=combined_request["operation_manifest"]["bash_commands"][0]["operation_digest"],
                operation_bytes=0, operation_lines=0, test_isolated=True,
            )
            combined_consumption = all(result == [] for result in combined_results) and bool(combined_replay)
        except ValueError:
            combined_boundary = False
        broadened_request = deepcopy(combined_request)
        broadened_request["allowed_command_classes"] = sorted(combined_classes | {"protected_git"})
        broadened_request["forbidden_operation_classes"] = sorted(decision.PROTECTED_OPERATION_CLASSES - set(broadened_request["allowed_command_classes"]))
        broadened_boundary_rejected = bool(decision.target_bound_blocks(broadened_request))

        def mutation_digest_controls(tool: str) -> tuple[bool, bool, bool]:
            command_class = {"apply_patch": "repo_patch", "Edit": "repo_edit", "Write": "repo_write"}[tool]
            mutation_digest = decision.digest(f"authority-gate-{tool}-operation")
            mutation_request = deepcopy(request)
            mutation_request.update({
                "allowed_tools": [tool], "allowed_command_classes": [command_class],
                "operation_manifest": {
                    "bash_commands": [],
                    "mutation_classes": {
                        command_class: {
                            "command_class": command_class, "operation_digest": mutation_digest,
                            "exact_files": ["docs/ops/MK733H_FINAL_GOAL_REVERSE_PLAN_AND_UTILIZATION_AUDIT_20260709.md"],
                            "path_prefixes": [], "max_changed_files": 1, "max_bytes": 128,
                            "max_lines": 4, "allowed_count": 1, "remaining": 1,
                        }
                    },
                    "read_only_diagnostics": [],
                },
                "budget": {"total": 1, "remaining": 1}, "external_authority_digest": "pending",
            })
            missing_digest = deepcopy(mutation_request)
            missing_digest["operation_manifest"]["mutation_classes"][command_class].pop("operation_digest")
            try:
                decision.authority_gate_transition_receipt(missing_digest)
                missing_rejected = False
            except ValueError:
                missing_rejected = True
            mutation_authority = make_authority(mutation_request)
            mutation_request["external_authority_digest"] = mutation_authority["envelope_digest"]
            mutation_state_dir = root / f"{tool}-state"
            mutation_boot = bootstrap(mutation_authority, authority_path, mutation_request, str(mutation_state_dir), True)
            mutation_receipt_path = mutation_state_dir / "current-receipt.json"
            mutation_pristine = load(mutation_receipt_path) if not mutation_boot["blocks"] else {}
            wrong = decision.consume_receipt(
                mutation_receipt_path, "pre_work", tool,
                observed_paths=["docs/ops/MK733H_FINAL_GOAL_REVERSE_PLAN_AND_UTILIZATION_AUDIT_20260709.md"],
                command_class=command_class, operation_digest=decision.digest(f"different-{tool}-operation"),
                operation_bytes=1, operation_lines=1, test_isolated=True,
            )
            atomic_json(mutation_receipt_path, mutation_pristine)
            exact = decision.consume_receipt(
                mutation_receipt_path, "pre_work", tool,
                observed_paths=["docs/ops/MK733H_FINAL_GOAL_REVERSE_PLAN_AND_UTILIZATION_AUDIT_20260709.md"],
                command_class=command_class, operation_digest=mutation_digest,
                operation_bytes=1, operation_lines=1, test_isolated=True,
            )
            return missing_rejected, bool(wrong), exact == []

        mutation_controls = {tool: mutation_digest_controls(tool) for tool in ("apply_patch", "Edit", "Write")}

        delegated_request = deepcopy(request)
        delegated_request["delegated_autonomy"] = True
        delegated_request["external_authority_ref"] = str(root / "separate-external-authority.json")
        delegated_request["external_authority_digest"] = "separate-external-authority-digest"
        delegated_authority = make_authority(delegated_request)
        delegated = bootstrap(delegated_authority, authority_path, delegated_request, str(root / "delegated-state"), True)
        controls = {
            "authority_only_exact_operation_consumed_once": not boot["blocks"] and one_time == [],
            "authority_only_replay_rejected": bool(replay),
            "authority_only_out_of_scope_rejected": bool(out_of_scope),
            "authority_only_wrong_tool_rejected": bool(wrong_tool),
            "authority_only_wrong_command_class_rejected": bool(wrong_class),
            "authority_only_wrong_operation_digest_rejected": bool(wrong_digest),
            "authority_only_resealed_tamper_rejected": bool(tamper),
            "authority_only_extra_field_rejected": bool(extra_schema),
            "authority_only_expired_authority_rejected": bool(stale),
            "target_bound_wrong_target_rejected": bool(wrong_target_blocks),
            "target_bound_wrong_revision_rejected": bool(wrong_revision_blocks),
            "target_bound_wrong_operation_rejected": bool(wrong_operation_blocks),
            "target_bound_operation_digest_tamper_rejected": bool(digest_tamper_blocks),
            "target_bound_broadened_classes_rejected": bool(broadened_blocks) and broadened_boundary_rejected,
            "target_bound_local_combined_exact_boundary": combined_boundary,
            "target_bound_local_combined_consumed_once": combined_consumption,
            "authority_only_mutation_schema_requires_operation_digest": all(result[0] for result in mutation_controls.values()),
            "authority_only_apply_patch_wrong_digest_rejected": mutation_controls["apply_patch"][1],
            "authority_only_apply_patch_exact_digest_consumed": mutation_controls["apply_patch"][2],
            "authority_only_edit_wrong_digest_rejected": mutation_controls["Edit"][1],
            "authority_only_edit_exact_digest_consumed": mutation_controls["Edit"][2],
            "authority_only_write_wrong_digest_rejected": mutation_controls["Write"][1],
            "authority_only_write_exact_digest_consumed": mutation_controls["Write"][2],
            "delegated_authority_requires_profile_route": "BLOCKED_FOR_MK733J_BOOTSTRAP_PROFILE_NOT_EMPIRICALLY_QUALIFIED" in delegated.get("blocks", []),
            "autonomous_profile_tier_requires_delegation": bool(execution_tier_blocks({"execution_tier": "autonomous_profile_qualified", "delegated_autonomy": False}, True)[2]),
        }
    return {
        "status": "PASS_AUTHORITY_GATE_RECEIPT_CONSUMPTION_CONTROLS" if all(controls.values()) else "FAIL_AUTHORITY_GATE_RECEIPT_CONSUMPTION_CONTROLS",
        "blocks": [] if all(controls.values()) else ["BLOCKED_FOR_MK733J_AUTHORITY_GATE_RECEIPT_SELF_TEST"],
        "controls": controls,
        "non_claims": ["test_isolated_only", "no_runtime_activation", "no_profile_qualification"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap"); boot.add_argument("--authority", required=True); boot.add_argument("--request", required=True); boot.add_argument("--state-dir"); boot.add_argument("--test-isolated", action="store_true"); boot.add_argument("--test-capability-registry")
    rollback_parser = sub.add_parser("rollback"); rollback_parser.add_argument("--state-dir"); rollback_parser.add_argument("--test-isolated", action="store_true")
    self_test_parser = sub.add_parser("authority-gate-self-test"); self_test_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:decision.configure_test_capability_registry(getattr(args,"test_capability_registry",None),test_isolated=bool(getattr(args,"test_isolated",False)))
    except ValueError as exc:
        result={"blocks":["BLOCKED_FOR_MK733J_CAPABILITY_REGISTRY_OVERRIDE_INVALID"],"detail":str(exc)};print(json.dumps(result,indent=2,sort_keys=True));return 1
    if args.command == "authority-gate-self-test":
        result = authority_gate_self_test()
    else:
        result = bootstrap(load(Path(args.authority)), Path(args.authority), load(Path(args.request)), args.state_dir, args.test_isolated) if args.command == "bootstrap" else rollback(args.state_dir, args.test_isolated)
    print(json.dumps(result, indent=2, sort_keys=True)); return 0 if not result["blocks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
