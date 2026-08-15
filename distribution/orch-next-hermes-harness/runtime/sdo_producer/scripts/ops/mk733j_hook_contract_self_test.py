#!/usr/bin/env python3
"""Isolated CLI/hook E2E for MK733J preflight, receipts, and rollback.

This harness never activates the repository-local state and never fabricates a
profile result.  Its synthetic qualification and authority envelopes live only
in a temporary directory and exercise the explicit test-isolated contract.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/ops"))
import mk733j_decision_os as decision  # noqa: E402
import mk_decision_preflight as preflight  # noqa: E402
import mk733j_activation as activation  # noqa: E402
import mk733j_capability_bundles as capability  # noqa: E402
from mk_whole_goal_control import genericize_transition_for_contract_test  # noqa: E402

ACTIVATION = REPO / "scripts/ops/mk733j_activation.py"
PREFLIGHT = REPO / "scripts/ops/mk_decision_preflight.py"
PRETOOL = REPO / ".codex/hooks/mk733j-pretooluse.py"
STOP = REPO / ".codex/hooks/mk733j-stop.py"
SESSION_START = REPO / ".codex/hooks/mk733j-session-start.sh"
SAMPLE = REPO / "research/mk675/fable5_decision_os/mk733j_n_terra_preflight.json"
GOAL_REF = "GG-MK733J-N-GPT56-FABLE5-DECISION-OS"
SCOPE_POLICY_FIELDS = (
    "work_id", "goal_ref", "task_class", "risk_class", "selected_profile", "profile_digest",
    "qualification_digest", "qualification_results_digest", "preflight_digest",
    "preflight_scope_digest", "preflight_operation_manifest_digest", "context_digest",
    "workpack_digest", "binding_record_digest", "allowed_tools", "allowed_path_prefixes",
    "allowed_command_classes", "forbidden_operation_classes",
    "external_protected_authority_state", "external_authority_ref", "external_authority_digest",
    "operation_manifest_digest", "operation_manifest_policy_digest", "target_repository", "target_path",
    "target_revision", "operation", "operation_digest", "rollback", "exclusions",
)


def write(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run(
    args: list[str], *, env: dict[str, str] | None = None, wire: dict[str, Any] | None = None,
    cwd: Path = REPO,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=json.dumps(wire) if wire is not None else None,
        text=True,
        capture_output=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def parsed(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        value = json.loads(proc.stdout)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def seal(value: dict[str, Any], field: str = "envelope_digest") -> dict[str, Any]:
    result = dict(value)
    result[field] = decision.digest(result)
    return result


def reseal_receipt(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["operation_manifest_digest"] = decision.digest(result["operation_manifest"])
    result["operation_manifest_policy_digest"] = decision.manifest_policy_digest(result["operation_manifest"])
    result["scope_policy_digest"] = decision.digest(decision.receipt_scope_policy(result))
    result.pop("receipt_digest", None)
    result["receipt_digest"] = decision.digest(result)
    return result


def permission(proc: subprocess.CompletedProcess[str]) -> str | None:
    return parsed(proc).get("hookSpecificOutput", {}).get("permissionDecision")


def operation_digest(
    *, tool_name: str, tool_input: dict[str, Any], command_class: str, paths: list[str],
    work_id: str, context_digest: str, preflight_scope_digest: str,
) -> str:
    return decision.digest({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "command_class": command_class,
        "paths": paths,
        "work_id": work_id,
        "context_digest": context_digest,
        "preflight_scope_digest": preflight_scope_digest,
    })


def build_composite_registry(root: Path, profile: dict[str, Any], thread_run_id: str, expires_at: str, *, task_class: str="bounded_implementation", bundle_ids: tuple[str,...] = ("decision_judgment", "bounded_implementation")) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    registry = json.loads(decision.CAPABILITY_BUNDLES.read_text(encoding="utf-8"))
    profile_digest = decision.digest(profile)
    results: dict[str, Any] = {}
    durable = root / "qualification-results"
    durable.mkdir()
    profile_id=profile["profile_id"];model=profile["runtime_model_identity_pattern"];effort=profile["reasoning_effort"]
    for bundle_id in bundle_ids:
        bundle = registry["bundles"][bundle_id]
        evaluation_contract = capability.evaluation_contract_digests(bundle_id)
        slug = f"{profile_id}--{task_class}--{bundle_id}"
        identity_path = durable / f"{slug}--identity.json"
        holdout_path = durable / f"{slug}--sealed.json"
        result_path = durable / f"{slug}--result.json"
        relative = lambda path: str(path.relative_to(root))
        common = {
            "profile_id": profile_id, "profile_digest": profile_digest,
            "task_class": bundle["task_class"], "bundle_id": bundle_id,
            "bundle_digest": decision.digest(bundle), "runtime_model_identity": model,
            "model": model, "reasoning_effort": effort, "thread_run_id": thread_run_id,
            "workpack_digest": decision.current_workpack_digest(),
            "binding_record_digest": decision.current_binding_record_digest(),
            **evaluation_contract,
            "output_digest": decision.digest({"test_only_bundle": bundle_id}),
        }
        identity = seal({"record_type": "mk733j_sanitized_qualification_envelope", "source_class": "test_only_observable_identity_readback", **common})
        write(identity_path, identity)
        authority_fields = {
            "authority_id": f"test-only-{bundle_id}",
            "holdout_authority_ref": str(root / f"test-only-{bundle_id}-authority.json"),
            "holdout_authority_digest": decision.digest({"authority": bundle_id}),
            "authority_profile_result_digest": decision.digest({"profile_result": bundle_id}),
            "authority_identity_envelope_digest": decision.digest({"authority_identity": bundle_id}),
        }
        holdout = {
            "record_type": "mk733j_sanitized_qualification_envelope",
            "source_class": "test_only_sol_owned_sealed_holdout", **common,
            "public_output_digest": common["output_digest"], "public_metrics": {}, **authority_fields,
        }
        holdout["holdout_result_digest"] = decision.digest(holdout)
        holdout = seal(holdout)
        write(holdout_path, holdout)
        safe_result = {
            "record_type": "mk733j_profile_task_class_qualification_result",
            "profile_id": profile_id, "profile_digest": profile_digest,
            "task_class": task_class, "bundle_id": bundle_id,
            "bundle_version": registry["bundle_registry_version"], "bundle_digest": decision.digest(bundle),
            "workpack_digest": decision.current_workpack_digest(),
            "binding_record_digest": decision.current_binding_record_digest(),
            **evaluation_contract,
            "evidence_class": "test_only_harness", "identity_readback_ref": relative(identity_path),
            "sealed_holdout_ref": relative(holdout_path), "identity_envelope_digest": identity["envelope_digest"],
            "sealed_holdout_envelope_digest": holdout["envelope_digest"], **authority_fields,
            "runtime_model_identity": model, "model": model,
            "reasoning_effort": effort, "thread_run_id": thread_run_id,
            "qualified_at": "2026-07-10T00:00:00Z", "expires_at": expires_at,
            "source_result_digest": decision.digest({"test_only_source": bundle_id}),
            "source_identity_envelope_digest": identity["envelope_digest"],
            "source_sealed_holdout_envelope_digest": holdout["envelope_digest"],
            "public_output_digest": common["output_digest"], "metrics": {},
        }
        safe_result["result_digest"] = decision.digest(safe_result)
        write(result_path, safe_result)
        entry = {
            key: safe_result[key] for key in (
                "profile_id", "profile_digest", "task_class", "bundle_id", "bundle_version", "bundle_digest",
                "workpack_digest", "binding_record_digest", "evaluation_corpus_digest", "evaluation_schema_digest", "evidence_class", "identity_readback_ref",
                "sealed_holdout_ref", "identity_envelope_digest", "sealed_holdout_envelope_digest", "authority_id",
                "holdout_authority_ref", "holdout_authority_digest", "authority_profile_result_digest",
                "authority_identity_envelope_digest", "runtime_model_identity", "model", "reasoning_effort",
                "thread_run_id", "qualified_at", "expires_at",
            )
        }
        entry.update({"result_ref": relative(result_path), "result_digest": decision.digest(safe_result), "qualification_state": "test_only_empirically_qualified_current"})
        entry["qualification_digest"] = decision.digest(entry)
        registry["profile_results"][f"{profile_id}:{task_class}:{bundle_id}"] = entry
        results[bundle_id] = {"result_ref": entry["result_ref"], "qualification_digest": entry["qualification_digest"]}
    registry_path = write(root / "capability-registry.json", registry)
    primary = registry["profile_results"][f"{profile_id}:{task_class}:{bundle_ids[-1]}"]
    return registry_path, results, primary


def build_contract(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    work_id = "mk733j-isolated-hook-e2e"
    context_digest = "mk733j-isolated-context-v1"
    thread_run_id = "mk733j-isolated-terra-thread"
    body = copy.deepcopy(json.loads(SAMPLE.read_text(encoding="utf-8")))
    whole_goal = json.loads(
        (REPO / "research/mk675/fable5_decision_os/inc178_whole_goal_work_selection_control.json").read_text(encoding="utf-8")
    )["example_current_transition"]
    whole_goal = genericize_transition_for_contract_test(
        whole_goal,
        goal_ref=GOAL_REF,
        phase_ref="generic-bounded-implementation",
        work_class="delegated_nontrivial",
        source_ref="generic-hook-contract-self-test",
    )
    body.update({
        "preflight_contract_version": preflight.PREFLIGHT_CONTRACT_VERSION,
        "work_id": work_id,
        "goal_ref": GOAL_REF,
        "task_class": "bounded_implementation",
        "work_class": "delegated_nontrivial",
        "risk_class": "medium",
        "context_digest": context_digest,
        "workpack_digest": decision.current_workpack_digest(),
        "binding_record_digest": decision.current_binding_record_digest(),
        "adaptive_work_pace_replan": {
            "contract_version": "adaptive_work_pace_replan.v1",
            "work_class": "delegated_nontrivial",
            "started_at": "2026-07-14T00:00:00Z",
            "checkpoint_seconds": 450,
            "expected_first_meaningful_delta_seconds": 300,
            "expected_completion_max_seconds": 1800,
            "same_strategy_attempt_count": 0,
            "max_same_strategy_attempts": 2,
            "no_delta_checkpoint_count": 0,
            "blocker_delta": "unknown",
            "checkpoint_review": None,
            "replan_decision": None,
            "external_wait": None,
        },
        "whole_goal_work_selection": whole_goal,
        "incident_recurrence_scan": [{
            "incident_ref": "INC-MK733J-HOOK-SCOPE-CONTINUITY",
            "mitigation": "Bind the work preflight to a bounded operation manifest before mutation.",
        }],
    })
    body["preflight_scope_digest"] = preflight.preflight_scope_digest(body)

    patch_input = {
        "patch": "*** Begin Patch\n*** Update File: scripts/ops/mk733j-e2e-target.py\n@@\n-old\n+new\n*** End Patch"
    }
    bash_command = "python3 --version"
    bash_input = {"command": shlex.join(shlex.split(bash_command))}
    patch_digest = operation_digest(
        tool_name="apply_patch", tool_input=patch_input, command_class="repo_patch",
        paths=["scripts/ops/mk733j-e2e-target.py"], work_id=work_id,
        context_digest=context_digest, preflight_scope_digest=body["preflight_scope_digest"],
    )
    bash_digest = operation_digest(
        tool_name="Bash", tool_input=bash_input, command_class="repo_script", paths=[],
        work_id=work_id, context_digest=context_digest,
        preflight_scope_digest=body["preflight_scope_digest"],
    )
    manifest = {
        "bash_commands": [{
            "operation_digest": bash_digest,
            "command_class": "repo_script",
            "allowed_count": 1,
            "remaining": 1,
        }],
        "mutation_classes": {
            "repo_patch": {
                "command_class": "repo_patch",
                "exact_files": ["scripts/ops/mk733j-e2e-target.py"],
                "path_prefixes": ["scripts/ops"],
                "max_changed_files": 1,
                "max_bytes": 4096,
                "max_lines": 40,
                "allowed_count": 1,
                "remaining": 1,
            },
        },
        "read_only_diagnostics": [],
    }
    body["operation_manifest_digest"] = decision.manifest_policy_digest(manifest)
    candidate_path = write(root / "preflight-candidate.json", body)
    preflight_path = root / "preflight.json"
    checked = run([
        sys.executable, str(PREFLIGHT), "--record", str(candidate_path),
        "--output", str(preflight_path), "--json",
    ])
    if checked.returncode or not preflight_path.is_file():
        raise ValueError("bound preflight CLI did not produce a passing artifact")
    preflight_doc = json.loads(preflight_path.read_text(encoding="utf-8"))

    qualification_expiry = "2099-01-01T00:00:00Z"
    base_profile = copy.deepcopy(decision.profiles()["terra_high_implementer"])
    registry_path, qualification_results, primary = build_composite_registry(root, base_profile, thread_run_id, qualification_expiry)
    qualification_digest = primary["qualification_digest"]
    profile = copy.deepcopy(base_profile)
    profile.update({
        "qualification_state": "empirically_qualified_current",
        "qualification_digest": qualification_digest,
        "expires_at": qualification_expiry,
    })
    profile_digest = decision.digest(profile)
    identity_path = root / "identity.json"
    common = {
        "source_class": "test_isolated_contract",
        "profile_id": "terra_high_implementer",
        "profile_digest": profile_digest,
        "runtime_model_identity": "gpt-5.6-terra",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "thread_run_id": thread_run_id,
        "workpack_digest": decision.current_workpack_digest(),
        "binding_record_digest": decision.current_binding_record_digest(),
    }
    identity = seal({
        **common,
        "identity_state": "verified",
        "observed_at": "2026-07-10T00:00:00Z",
        "qualification_result_ref": primary["result_ref"],
    })
    write(identity_path, identity)

    request = {
        "profile_id": "terra_high_implementer",
        "runtime_identity_state": "verified",
        "qualification_state": "current",
        "task_class": "bounded_implementation",
        "risk_class": "medium",
        "test_only_qualified_profile": True,
        "runtime_model_identity": "gpt-5.6-terra",
        "runtime_identity_ref": str(identity_path),
        "thread_run_id": thread_run_id,
        "qualification_result_ref": primary["result_ref"],
        "qualification_expires_at": qualification_expiry,
        "qualification_digest": qualification_digest,
        "qualification_results": qualification_results,
        "context_digest": context_digest,
        "work_id": work_id,
        "goal_ref": GOAL_REF,
        "allowed_tools": ["Bash", "apply_patch", "Edit", "Write"],
        "budget": {"total": 2, "remaining": 2},
        "return_schema": "dispatch_readback_v1",
        "readback_required": True,
        "auditor_independent_from_implementer": True,
        "policy_refs": ["mk733j_gpt56_model_neutral_decision_os_workpack"],
        "non_claims": ["test_isolated_not_runtime_activation"],
        "allowed_path_prefixes": ["scripts/ops"],
        "allowed_command_classes": ["repo_patch", "repo_script"],
        "forbidden_operation_classes": [
            "protected_git", "network", "credential", "credential_path", "destructive",
            "runtime_release", "python_inline", "shell_wrapper", "shell_chaining",
        ],
        "external_protected_authority_state": "absent",
        "external_authority_ref": None,
        "external_authority_digest": None,
        "operation_manifest": manifest,
        "preflight_ref": str(preflight_path),
        "preflight_digest": decision.digest(preflight_doc),
        "preflight_scope_digest": body["preflight_scope_digest"],
        "preflight_operation_manifest_digest": decision.manifest_policy_digest(manifest),
        "preflight_contract_version": preflight.PREFLIGHT_CONTRACT_VERSION,
    }
    return request, patch_input, {"command": bash_command}, registry_path


def authority_for(request: dict[str, Any], root: Path) -> dict[str, Any]:
    issued_at = "2026-07-10T00:00:00Z"
    expires_at = "2099-01-01T00:00:00Z"
    evidence = {
        "record_type": "mk733j_activation_authority_readback",
        "source_class": "test_isolated_authority_readback",
        "issuer": "isolated-e2e",
        "issuer_class": "test_isolated",
        "authority_ref": "isolated-e2e-authority",
        "scope": "mk733j_n_local_hook_activation",
        "profile_request_digest": decision.digest(request),
        "workpack_digest": decision.current_workpack_digest(),
        "binding_record_digest": decision.current_binding_record_digest(),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "approved": True,
    }
    evidence["evidence_digest"] = decision.digest(evidence)
    evidence_path = write(root / "authority-evidence.json", evidence)
    return seal({
        "authority": "MK733J_ENFORCEMENT_ACTIVATION",
        "approved": True,
        "issuer": "isolated-e2e",
        "issuer_class": "test_isolated",
        "authority_ref": "isolated-e2e-authority",
        "scope": "mk733j_n_local_hook_activation",
        "workpack_digest": decision.current_workpack_digest(),
        "binding_record_digest": decision.current_binding_record_digest(),
        "profile_request_digest": decision.digest(request),
        "authority_evidence_source": "test_isolated",
        "authority_evidence_ref": str(evidence_path),
        "authority_evidence_digest": evidence["evidence_digest"],
        "issued_at": issued_at,
        "expires_at": expires_at,
        "source_class": "test_isolated_activation_authority",
    })


def hook(
    env: dict[str, str], tool_name: str, tool_input: dict[str, Any], *, interpreter: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(
        [interpreter or sys.executable, str(PRETOOL)], env=env,
        wire={"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input},
    )


def main() -> int:
    blocks: list[str] = []
    composite_controls = False
    protected_authority_controls = False
    recursive_sensitive_activation_authority_rejected = False
    extra_qualification_bundle_route_rejected = False
    extra_qualification_bundle_receipt_rejected = False
    extra_qualification_row_route_rejected = False
    extra_qualification_row_receipt_rejected = False
    protected_path_exact_match_controls = False
    active_policy_index_fail_closed_controls = False
    git_read_argv_exactness_controls = False
    trusted_executable_provenance_controls = False
    receipt_free_mutation_one_shot_controls = False
    no_receipt_read_controls = False
    no_receipt_patch = False
    no_receipt_edit = False
    no_receipt_edit_omitted = False
    no_receipt_edit_denials = False
    no_receipt_denial_controls = False
    preflight_malformed_and_sensitive_rejected = False
    non_object_preflight_schema_rejected = False
    tiered_stale_receipt_controls = False
    recovery_tier_controls = False
    recovery_bootstrap_rejected = False
    test_branch_override_controls = False
    configured_hook_interpreter_compatibility = False
    git_root_resolved_hook_launchers = False
    session_start_trusted_dirname_controls = False
    activation_execution_tier_controls = False
    supervised_shadow_tier_controls = False
    with tempfile.TemporaryDirectory(prefix="mk733j-hook-e2e-") as directory:
        root = Path(directory)
        env = {
            **os.environ,
            "MK733J_TEST_ISOLATED": "true",
            "MK733J_TEST_BRANCH": "codex/mk733j-hook-e2e",
            "MK733J_TEST_WORKTREE_STATUS": "",
            "MK733J_STATE_DIR": str(root),
        }

        pretool_spec = importlib.util.spec_from_file_location("mk733j_pretooluse_contract", PRETOOL)
        if pretool_spec is None or pretool_spec.loader is None:
            blocks.append("BLOCKED_FOR_MK733J_BASH_SCOPE_PARSER_LOAD")
            pretool_module = None
        else:
            pretool_module = importlib.util.module_from_spec(pretool_spec)
            pretool_spec.loader.exec_module(pretool_module)
            probe_root = root / "path-probe"
            (probe_root / "docs").mkdir(parents=True)
            (probe_root / "controls").mkdir(parents=True)
            authority_root = probe_root / "research/mk675/fable5_decision_os"
            (authority_root / "qualification-results").mkdir(parents=True)
            (authority_root / "authorities").mkdir(parents=True)
            (authority_root / "qualification-authorities").mkdir(parents=True)
            (probe_root / "package.json").write_text("{}\n", encoding="utf-8")
            (probe_root / "docs/controls-not-authority.md").write_text("probe\n", encoding="utf-8")
            (probe_root / "docs/qualification-authorities.md").write_text("probe\n", encoding="utf-8")
            (probe_root / "docs/trusted_activation.md").write_text("probe\n", encoding="utf-8")
            (probe_root / "controls/authority.json").write_text("{}\n", encoding="utf-8")
            active_policy_rel = Path("docs/ops/MK_FG000O_AUDIT_SIDE_DEEP_SUPPORT_ARTIFACT_RESEARCH_GATE_INSTALL_20260618.json")
            active_policy = probe_root / active_policy_rel
            active_policy.parent.mkdir(parents=True, exist_ok=True)
            active_policy.write_text("active policy probe\n", encoding="utf-8")
            write(probe_root / "controls/active-policy-index.json", {
                "active_policy_refs": [{"path": str(active_policy_rel)}],
            })
            (authority_root / "mk733j_n_capability_bundles.json").write_text("{}\n", encoding="utf-8")
            (authority_root / "mk733j_n_decision_os_implementation.json").write_text("{}\n", encoding="utf-8")
            (authority_root / "mk733j_n_trusted_activation_authorities.json").write_text("{}\n", encoding="utf-8")
            (authority_root / "qualification-results/profile.json").write_text("{}\n", encoding="utf-8")
            (authority_root / "authorities/authority.json").write_text("{}\n", encoding="utf-8")
            (authority_root / "qualification-authorities/provider-attestations.json").write_text("{}\n", encoding="utf-8")
            (authority_root / "ordinary-analysis.md").write_text("probe\n", encoding="utf-8")
            protected_path_exact_match_controls = (
                not pretool_module.protected_low_risk_path(probe_root, "docs/controls-not-authority.md")
                and pretool_module.protected_low_risk_path(probe_root, "controls/authority.json")
                and pretool_module.protected_low_risk_path(probe_root, str(active_policy_rel))
                and active_policy_rel in pretool_module.active_policy_paths(REPO)
                and pretool_module.protected_low_risk_path(REPO, str(active_policy_rel))
                and pretool_module.protected_low_risk_path(probe_root, "research/mk675/fable5_decision_os/mk733j_n_capability_bundles.json")
                and pretool_module.protected_low_risk_path(probe_root, "research/mk675/fable5_decision_os/mk733j_n_decision_os_implementation.json")
                and pretool_module.protected_low_risk_path(probe_root, "research/mk675/fable5_decision_os/mk733j_n_trusted_activation_authorities.json")
                and pretool_module.protected_low_risk_path(probe_root, "research/mk675/fable5_decision_os/qualification-results/profile.json")
                and pretool_module.protected_low_risk_path(probe_root, "research/mk675/fable5_decision_os/authorities/authority.json")
                and pretool_module.protected_low_risk_path(probe_root, "research/mk675/fable5_decision_os/qualification-authorities/provider-attestations.json")
                and pretool_module.protected_low_risk_path(probe_root, "package.json")
                and not pretool_module.protected_low_risk_path(probe_root, "research/mk675/fable5_decision_os/ordinary-analysis.md")
                and not pretool_module.protected_low_risk_path(probe_root, "docs/qualification-authorities.md")
                and not pretool_module.protected_low_risk_path(probe_root, "docs/trusted_activation.md")
            )
            if not protected_path_exact_match_controls:
                blocks.append("BLOCKED_FOR_MK733J_LOW_RISK_PATH_EXACT_MATCH")
            original_index = (probe_root / "controls/active-policy-index.json").read_bytes()
            original_branch_eligible = pretool_module.low_risk_branch_eligible
            original_worktree_clean = pretool_module.low_risk_worktree_clean
            try:
                # This unit-level policy-index probe makes branch/worktree
                # eligibility deterministic without changing the real tree.
                pretool_module.low_risk_branch_eligible = lambda _: True
                pretool_module.low_risk_worktree_clean = lambda _: True
                lightweight_patch = {
                    "patch": "*** Begin Patch\n*** Update File: docs/controls-not-authority.md\n@@\n-old\n+new\n*** End Patch"
                }
                patch_paths = ["docs/controls-not-authority.md"]
                valid_index_allows = (
                    pretool_module.active_policy_index(probe_root)[0]
                    and pretool_module.tiered_non_authority_allow(
                        "apply_patch", lightweight_patch, patch_paths, "repo_patch", probe_root
                    ) is not None
                )
                malformed_indexes = (
                    None,
                    "[]",
                    json.dumps({"active_policy_refs": {}}),
                    json.dumps({"active_policy_refs": [{"path": 7}]}),
                    json.dumps({"active_policy_refs": [{"path": "../escape.md"}]}),
                )
                failures_fail_closed = True
                reads_remain_allowed = True
                index_path = probe_root / "controls/active-policy-index.json"
                for malformed in malformed_indexes:
                    if malformed is None:
                        index_path.unlink()
                    else:
                        index_path.write_text(malformed, encoding="utf-8")
                    failures_fail_closed = failures_fail_closed and not pretool_module.active_policy_index(probe_root)[0]
                    failures_fail_closed = failures_fail_closed and pretool_module.tiered_non_authority_allow(
                        "apply_patch", lightweight_patch, patch_paths, "repo_patch", probe_root
                    ) is None
                    reads_remain_allowed = reads_remain_allowed and pretool_module.tiered_non_authority_allow(
                        "Bash", {"command": "cat docs/controls-not-authority.md"}, ["docs/controls-not-authority.md"], "read_only_exploration", probe_root
                    ) is not None
                active_policy_index_fail_closed_controls = valid_index_allows and failures_fail_closed and reads_remain_allowed
            finally:
                (probe_root / "controls/active-policy-index.json").write_bytes(original_index)
                pretool_module.low_risk_branch_eligible = original_branch_eligible
                pretool_module.low_risk_worktree_clean = original_worktree_clean
            git_read_argv_exactness_controls = (
                pretool_module.mutation_scope(
                    {"tool_name": "Bash", "tool_input": {"command": "git branch --show-current"}}, REPO
                )[1] == "read_only_git"
                and pretool_module.mutation_scope(
                    {"tool_name": "Bash", "tool_input": {"command": "git --no-pager log -3 --oneline"}}, REPO
                )[1] == "read_only_git"
                and pretool_module.mutation_scope(
                    {"tool_name": "Bash", "tool_input": {"command": "git status --short --branch"}}, REPO
                )[1] == "read_only_git"
                and all(
                    pretool_module.mutation_scope(
                        {"tool_name": "Bash", "tool_input": {"command": command}}, REPO
                    )[1] != "read_only_git"
                    for command in (
                        "git branch -D codex/example",
                        "git branch -d codex/example",
                        "git branch -m codex/example renamed",
                        "git log",
                        "git log -3 --oneline",
                        "git --no-pager log --oneline",
                        "git log --ext-diff",
                        "git log --output=/tmp/out",
                        "git log --format=%H",
                        "git log -1001 --oneline",
                        "git -c core.pager=cat log",
                        "git submodule status",
                        "git status --output=/tmp/out",
                    )
                )
            )
            if not active_policy_index_fail_closed_controls:
                blocks.append("BLOCKED_FOR_MK733J_ACTIVE_POLICY_INDEX_FAIL_CLOSED")
            if not git_read_argv_exactness_controls:
                blocks.append("BLOCKED_FOR_MK733J_GIT_READ_ARGV_EXACTNESS")
            saved_isolated = os.environ.pop("MK733J_TEST_ISOLATED", None)
            saved_test_branch = os.environ.get("MK733J_TEST_BRANCH")
            try:
                os.environ["MK733J_TEST_BRANCH"] = "codex/test-override"
                production_override_rejected = not pretool_module.low_risk_branch_eligible(root)
                os.environ["MK733J_TEST_ISOLATED"] = "true"
                isolated_override_accepted = pretool_module.low_risk_branch_eligible(root)
                test_branch_override_controls = production_override_rejected and isolated_override_accepted
            finally:
                if saved_isolated is None:
                    os.environ.pop("MK733J_TEST_ISOLATED", None)
                else:
                    os.environ["MK733J_TEST_ISOLATED"] = saved_isolated
                if saved_test_branch is None:
                    os.environ.pop("MK733J_TEST_BRANCH", None)
                else:
                    os.environ["MK733J_TEST_BRANCH"] = saved_test_branch
            if not test_branch_override_controls:
                blocks.append("BLOCKED_FOR_MK733J_TEST_BRANCH_OVERRIDE_ISOLATION")
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            marker = root / "fake-executable-ran"
            for name in ("cat", "rg", "sed", "git"):
                fake = fake_bin / name
                fake.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 99\n", encoding="utf-8")
                fake.chmod(0o755)
            saved_path = os.environ.get("PATH", "")
            try:
                os.environ["PATH"] = str(fake_bin) + os.pathsep + saved_path
                shadow_rejected = all(pretool_module.trusted_read_executable(name) is None for name in ("cat", "rg", "sed", "git"))
            finally:
                os.environ["PATH"] = saved_path
            hooks_config = json.loads((REPO / ".codex/hooks.json").read_text(encoding="utf-8"))
            hook_groups = hooks_config.get("hooks", {})
            launcher_text = json.dumps(hooks_config, sort_keys=True)
            git_root_prefix = "$(/usr/bin/git rev-parse --show-toplevel)/.codex/hooks/"
            configured_python = Path("/usr/bin/python3")
            configured_handlers = [
                handler.get("command", "")
                for groups in hooks_config.get("hooks", {}).values()
                for group in groups
                for handler in group.get("hooks", [])
                if isinstance(handler, dict)
            ]
            configured_compile = all(
                run([str(configured_python), "-m", "py_compile", str(path)], env={**env, "PYTHONPYCACHEPREFIX": str(root / "configured-pycache")}).returncode == 0
                for path in (PRETOOL, STOP, REPO / "scripts/ops/mk733j_decision_os.py", REPO / "scripts/ops/mk733j_qualification.py")
            )
            configured_pretool = run(
                [str(configured_python), str(PRETOOL)], env=env,
                wire={"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat AGENTS.md"}},
            )
            configured_stop = run(
                [str(configured_python), str(STOP)], env=env,
                wire={"hook_event_name": "Stop", "stop_hook_active": True},
            )
            launcher_commands = {
                event: groups[0]["hooks"][0]["command"]
                for event, groups in hook_groups.items()
                if isinstance(groups, list) and groups and isinstance(groups[0], dict)
                and isinstance(groups[0].get("hooks"), list) and groups[0]["hooks"]
                and isinstance(groups[0]["hooks"][0], dict)
            }
            nested_cwd = REPO / "scripts"
            nested_session_start = run(
                ["/bin/bash", "-c", launcher_commands.get("SessionStart", "false")],
                env=env, wire={"hook_event_name": "SessionStart", "source": "startup"}, cwd=nested_cwd,
            )
            nested_pretool = run(
                ["/bin/bash", "-c", launcher_commands.get("PreToolUse", "false")],
                env=env, wire={"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat AGENTS.md"}}, cwd=nested_cwd,
            )
            nested_stop = run(
                ["/bin/bash", "-c", launcher_commands.get("Stop", "false")],
                env=env, wire={"hook_event_name": "Stop", "stop_hook_active": True}, cwd=nested_cwd,
            )
            git_root_resolved_hook_launchers = (
                set(launcher_commands) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "SubagentStart", "Stop"}
                and all(git_root_prefix in command for command in launcher_commands.values())
                and "$(git" not in launcher_text
                and nested_session_start.returncode == 0
                and "Decision OS is in shadow diagnostics" in nested_session_start.stdout
                and nested_pretool.returncode == 0
                and bool(parsed(nested_pretool).get("hookSpecificOutput", {}).get("additionalContext"))
                and nested_stop.returncode == 0
                and bool(parsed(nested_stop).get("systemMessage"))
            )
            configured_hook_interpreter_compatibility = (
                configured_python.is_file()
                and any(command.startswith("/usr/bin/python3 ") for command in configured_handlers)
                and configured_compile
                and configured_pretool.returncode == 0
                and configured_stop.returncode == 0
            )
            trusted_executable_provenance_controls = (
                pretool_module.trusted_read_executable("/tmp/cat") is None
                and shadow_rejected
                and pretool_module.trusted_read_executable("/bin/cat") is not None
                and git_root_resolved_hook_launchers
                and not marker.exists()
            )
            fake_dirname = fake_bin / "dirname"
            fake_dirname.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 99\n", encoding="utf-8")
            fake_dirname.chmod(0o755)
            session_start = run(["/bin/bash", str(SESSION_START)], env={**env, "PATH": str(fake_bin) + os.pathsep + env.get("PATH", "")})
            session_start_trusted_dirname_controls = session_start.returncode == 0 and not marker.exists()
            if not trusted_executable_provenance_controls:
                blocks.append("BLOCKED_FOR_MK733J_TRUSTED_READ_EXECUTABLE_PROVENANCE")
            if not configured_hook_interpreter_compatibility:
                blocks.append("BLOCKED_FOR_MK733J_CONFIGURED_HOOK_INTERPRETER")
            if not git_root_resolved_hook_launchers:
                blocks.append("BLOCKED_FOR_MK734_GIT_ROOT_HOOK_LAUNCHER")
            if not session_start_trusted_dirname_controls:
                blocks.append("BLOCKED_FOR_MK733J_SESSION_START_EXECUTABLE_PROVENANCE")
        parser_negatives = (
            "python3 scripts/ops/safe.py --output=/tmp/escaped.json",
            "python3 scripts/ops/safe.py -o/tmp/escaped.json",
            "python3 scripts/ops/safe.py --output=$HOME/out.json",
            "printf x > outside.txt",
            "python3 <(printf x)",
            "python3 scripts/ops/*.py",
        )
        if pretool_module is not None:
            parsed_negatives = [
                pretool_module.mutation_scope(
                    {"tool_name": "Bash", "tool_input": {"command": command}}, REPO
                )[2]
                for command in parser_negatives
            ]
            safe_paths, safe_class, safe_error = pretool_module.mutation_scope(
                {"tool_name": "Bash", "tool_input": {"command": "python3 --version"}}, REPO
            )
            if not all(parsed_negatives) or safe_paths or safe_class != "repo_script" or safe_error is not None:
                blocks.append("BLOCKED_FOR_MK733J_BASH_SCOPE_PARSER_NEGATIVE_CONTROLS")
            branch_helper_controls=(pretool_module.low_risk_branch_eligible(REPO,lambda:"codex/test\n") and not pretool_module.low_risk_branch_eligible(REPO,lambda:"main\n") and not pretool_module.low_risk_branch_eligible(REPO,lambda:"\n") and pretool_module.low_risk_worktree_clean(REPO,lambda:"") and not pretool_module.low_risk_worktree_clean(REPO,lambda:" M docs/example.md\n"))
            if not branch_helper_controls: blocks.append("BLOCKED_FOR_MK733J_LOW_RISK_BRANCH_HELPER")

        # The CLI itself must reject a shape-complete legacy record that lacks
        # work/context/workpack/manifest bindings.
        unbound = write(root / "unbound-preflight.json", json.loads(SAMPLE.read_text(encoding="utf-8")))
        unbound_result = run([sys.executable, str(PREFLIGHT), "--record", str(unbound), "--json"])
        if unbound_result.returncode == 0 or "BLOCKED_FOR_MK733J_PREFLIGHT_BINDING_MISSING" not in parsed(unbound_result).get("blocks", []):
            blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_CLI_UNBOUND_NEGATIVE")

        request, patch_input, bash_input, test_registry = build_contract(root)
        env["MK733J_TEST_CAPABILITY_REGISTRY"] = str(test_registry)
        request_path = write(root / "request.json", request)
        patch_wire = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": patch_input}
        shadow = run([sys.executable, str(PRETOOL)], env=env, wire=patch_wire)
        if shadow.returncode or "additionalContext" not in parsed(shadow).get("hookSpecificOutput", {}):
            blocks.append("BLOCKED_FOR_MK733J_SHADOW_NONBLOCKING")

        unqualified = {
            "profile_id": "terra_high_implementer",
            "runtime_identity_state": "verified",
            "runtime_model_identity": "gpt-5.6-terra",
            "qualification_state": "provisional",
            "task_class": "bounded_implementation",
            "risk_class": "medium",
        }
        unqualified_path = write(root / "unqualified-request.json", unqualified)
        unqualified_authority = write(root / "unqualified-authority.json", authority_for(unqualified, root))
        bootstrap_args = ["--state-dir", str(root), "--test-isolated"]
        blocked_bootstrap = run([
            sys.executable, str(ACTIVATION), "bootstrap", "--authority", str(unqualified_authority),
            "--request", str(unqualified_path), *bootstrap_args,
        ])
        if blocked_bootstrap.returncode == 0:
            blocks.append("BLOCKED_FOR_MK733J_UNQUALIFIED_BOOTSTRAP_NEGATIVE")

        # Bound-preflight negatives use the real receipt issuer, not a status
        # label supplied by the test.
        def issue(candidate: dict[str, Any]) -> subprocess.CompletedProcess[str]:
            path = write(root / "candidate-request.json", candidate)
            return run([
                sys.executable, str(REPO / "scripts/ops/mk733j_decision_os.py"),
                "issue-receipt", "--request", str(path), "--phase", "pre_work",
                "--test-isolated", "--test-capability-registry", str(test_registry), "--json",
            ])

        extra_bundle_request=copy.deepcopy(request)
        extra_bundle_request["qualification_results"]["unrelated_bundle"]={"result_ref":request["qualification_result_ref"],"qualification_digest":request["qualification_digest"]}
        extra_route=decision.route(extra_bundle_request,test_isolated=True)
        extra_qualification_bundle_route_rejected=extra_route.get("route")=="stop_or_escalate" and "BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_KEYSET_INVALID" in extra_route.get("blockers",[])
        extra_qualification_bundle_receipt_rejected=issue(extra_bundle_request).returncode != 0
        if not extra_qualification_bundle_route_rejected or not extra_qualification_bundle_receipt_rejected:
            blocks.append("BLOCKED_FOR_MK733J_EXTRA_QUALIFICATION_BUNDLE_CONTROLS")
        extra_row_request=copy.deepcopy(request)
        extra_row_request["qualification_results"]["bounded_implementation"]["extra"]="reject"
        extra_row_route=decision.route(extra_row_request,test_isolated=True)
        extra_qualification_row_route_rejected=(
            extra_row_route.get("route")=="stop_or_escalate"
            and "BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_REQUEST_SCHEMA_INVALID:bounded_implementation" in extra_row_route.get("blockers",[])
        )
        extra_qualification_row_receipt_rejected=issue(extra_row_request).returncode != 0
        if not extra_qualification_row_route_rejected or not extra_qualification_row_receipt_rejected:
            blocks.append("BLOCKED_FOR_MK733J_EXTRA_QUALIFICATION_ROW_CONTROLS")

        missing_preflight = dict(request); missing_preflight.pop("preflight_ref")
        wrong_work = {**request, "work_id": "other-work"}
        wrong_context = {**request, "context_digest": "other-context"}
        fractional_budget = {**request, "budget": {"total": 2.0, "remaining": 2.0}}
        if any(proc.returncode == 0 for proc in (issue(missing_preflight), issue(wrong_work), issue(wrong_context), issue(fractional_budget))):
            blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_BINDING_NEGATIVES")
        missing_composite = {**request, "qualification_results": {}}
        partial_composite = {**request, "qualification_results": {"decision_judgment": request["qualification_results"]["decision_judgment"]}}
        wrong_result_ref = copy.deepcopy(request); wrong_result_ref["qualification_results"]["bounded_implementation"]["result_ref"] = "qualification-results/missing.json"
        wrong_result_digest = copy.deepcopy(request); wrong_result_digest["qualification_results"]["decision_judgment"]["qualification_digest"] = "0" * 64
        composite_procs = [issue(candidate) for candidate in (missing_composite, partial_composite, wrong_result_ref, wrong_result_digest)]
        composite_controls = list(request["qualification_results"]) == ["decision_judgment", "bounded_implementation"] and all(proc.returncode != 0 for proc in composite_procs)
        if not composite_controls:
            blocks.append("BLOCKED_FOR_MK733J_COMPOSITE_QUALIFICATION_CONTROLS")

        def preflight_variant(name: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            original = json.loads(Path(request["preflight_ref"]).read_text(encoding="utf-8"))
            body = {
                key: value
                for key, value in original.items()
                if key not in preflight.DERIVED_PREFLIGHT_FIELDS
            }
            mutate(body)
            body["preflight_scope_digest"] = preflight.preflight_scope_digest(body)
            variant = {
                **body,
                "deterministic_result": {
                    "validator": "mk_decision_preflight",
                    "status": "PASS_PREFLIGHT_SUPPORT_EVIDENCE_ONLY",
                    "record_digest": decision.digest(body),
                    "decision": original.get("deterministic_result", {}).get(
                        "decision", "NO_PLANNING_ORDER_SELECTION"
                    ),
                },
            }
            path = write(root / name, variant)
            return {
                **request,
                "preflight_ref": str(path),
                "preflight_digest": decision.digest(variant),
                "preflight_scope_digest": body["preflight_scope_digest"],
            }

        def mismatch_whole_goal(body: dict[str, Any]) -> None:
            body["whole_goal_work_selection"]["decision_binding"]["goal_ref"] = "WRONG-GOAL"
            body["whole_goal_work_selection"]["whole_goal"]["goal_ref"] = "WRONG-GOAL"

        def omit_paced_controls(body: dict[str, Any]) -> None:
            body.pop("adaptive_work_pace_replan", None)
            body.pop("whole_goal_work_selection", None)

        empty_ux = preflight_variant("empty-ux.json", lambda body: body.update({"ux_scorecard": {}, "candidate_options": []}))
        stale_workpack = preflight_variant("stale-workpack.json", lambda body: body.update({"workpack_digest": "0" * 64}))
        validator_label_only = preflight_variant(
            "validator-label-only.json",
            lambda body: body.update({"decision_ledger": {}, "ux_scorecard": {}, "candidate_options": [], "rejected_options": []}),
        )
        budget_bypass = preflight_variant(
            "budget-bypass.json",
            lambda body: body["declared_budget"].update({"max_tool_calls": 1}),
        )
        malformed_preflight = preflight_variant(
            "malformed-preflight.json",
            lambda body: body["candidate_options"][0].update({"option_id": {"not": "a string"}}),
        )
        sensitive_preflight = preflight_variant(
            "sensitive-preflight.json",
            lambda body: body["decision_ledger"].update({"raw_transcript": "forbidden"}),
        )
        cross_goal = preflight_variant("cross-goal-preflight.json", mismatch_whole_goal)
        omitted_paced_controls = preflight_variant("omitted-paced-controls.json", omit_paced_controls)
        preflight_malformed_and_sensitive_rejected = all(
            issue(candidate).returncode != 0 for candidate in (malformed_preflight, sensitive_preflight)
        )
        if any(issue(candidate).returncode == 0 for candidate in (
            empty_ux, stale_workpack, validator_label_only, budget_bypass,
            cross_goal, omitted_paced_controls,
        )) or not preflight_malformed_and_sensitive_rejected:
            blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_SUBSTANCE_NEGATIVES")

        # Top-level JSON must be an object before any legacy field validator
        # runs.  Exercise the CLI boundary for all JSON non-object forms,
        # rather than merely calling the in-process helper.
        non_object_results = []
        for index, value in enumerate(([1], "not-an-object", 1, None)):
            candidate = write(root / f"non-object-preflight-{index}.json", value)
            proc = run([sys.executable, str(PREFLIGHT), "--record", str(candidate), "--json"])
            payload = parsed(proc)
            non_object_results.append(
                proc.returncode == 1
                and payload.get("status") == "FAIL_PREFLIGHT_BLOCKED"
                and payload.get("blocks") == ["BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID"]
                and "VALIDATOR_ERROR" not in proc.stdout
                and "Traceback" not in proc.stderr
            )
        non_object_preflight_schema_rejected = all(non_object_results)
        if not non_object_preflight_schema_rejected:
            blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_NON_OBJECT_TOP_LEVEL")

        # A protected command can never be hidden in the non-consuming
        # diagnostic lane.  Exercise both the issuer and the actual consumer.
        protected_input = {"command": "curl"}
        protected_digest = operation_digest(
            tool_name="Bash", tool_input=protected_input, command_class="network", paths=[],
            work_id=request["work_id"], context_digest=request["context_digest"],
            preflight_scope_digest=request["preflight_scope_digest"],
        )
        protected_manifest = copy.deepcopy(request["operation_manifest"])
        protected_manifest["read_only_diagnostics"].append({
            "operation_digest": protected_digest,
            "command_class": "network",
        })
        protected_request = preflight_variant(
            "protected-diagnostic-preflight.json",
            lambda body: body.update({
                "operation_manifest_digest": decision.manifest_policy_digest(protected_manifest),
            }),
        )
        protected_request.update({
            "operation_manifest": protected_manifest,
            "allowed_command_classes": sorted(set(request["allowed_command_classes"] + ["network"])),
        })
        base_protected_receipt = parsed(issue(request))
        forged_protected_receipt = copy.deepcopy(base_protected_receipt)
        forged_protected_receipt.update({
            "preflight_ref": protected_request["preflight_ref"],
            "preflight_digest": protected_request["preflight_digest"],
            "preflight_scope_digest": protected_request["preflight_scope_digest"],
            "preflight_operation_manifest_digest": decision.manifest_policy_digest(protected_manifest),
            "operation_manifest": protected_manifest,
            "allowed_command_classes": protected_request["allowed_command_classes"],
        })
        protected_receipt_path = write(
            root / "protected-diagnostic-receipt.json",
            reseal_receipt(forged_protected_receipt),
        )
        protected_consume = run([
            sys.executable, str(REPO / "scripts/ops/mk733j_decision_os.py"),
            "consume-receipt", "--receipt", str(protected_receipt_path),
            "--phase", "pre_work", "--tool", "Bash", "--command-class", "network",
            "--operation-digest", protected_digest, "--operation-bytes", "18",
            "--operation-lines", "1", "--test-isolated", "--test-capability-registry", str(test_registry), "--json",
        ])
        if issue(protected_request).returncode == 0 or protected_consume.returncode == 0:
            blocks.append("BLOCKED_FOR_MK733J_PROTECTED_DIAGNOSTIC_BYPASS")

        issued_before_alter = issue(request)
        if issued_before_alter.returncode:
            blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_POSITIVE_ISSUANCE")
        else:
            issued_path = write(root / "issued-before-preflight-alter.json", parsed(issued_before_alter))
            preflight_path = Path(request["preflight_ref"])
            original_preflight = preflight_path.read_text(encoding="utf-8")
            altered = json.loads(original_preflight)
            altered["decision_ledger"]["q1_what_user_experience_improves"] = "altered after receipt issuance"
            write(preflight_path, altered)
            altered_verify = run([
                sys.executable, str(REPO / "scripts/ops/mk733j_decision_os.py"),
                "verify-receipt", "--receipt", str(issued_path), "--phase", "pre_work",
                "--tool", "apply_patch", "--test-isolated", "--json",
            ])
            preflight_path.write_text(original_preflight, encoding="utf-8")
            if altered_verify.returncode == 0:
                blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_POST_ISSUANCE_TAMPER")

        authority_document = authority_for(request, root)
        authority_path = write(root / "authority.json", authority_document)
        evidence_path = Path(authority_document["authority_evidence_ref"])
        original_evidence = evidence_path.read_bytes()
        sensitive_evidence = json.loads(original_evidence)
        sensitive_evidence["metadata"] = {"hidden_reasoning": "forbidden"}
        sensitive_evidence.pop("evidence_digest", None)
        sensitive_evidence["evidence_digest"] = decision.digest(sensitive_evidence)
        evidence_path.write_text(json.dumps(sensitive_evidence), encoding="utf-8")
        sensitive_authority = copy.deepcopy(authority_document)
        sensitive_authority["authority_evidence_digest"] = sensitive_evidence["evidence_digest"]
        sensitive_authority.pop("envelope_digest", None)
        sensitive_authority["envelope_digest"] = decision.digest(sensitive_authority)
        recursive_sensitive_activation_authority_rejected = bool(activation.authority_blocks(sensitive_authority, authority_path, request, True))
        evidence_path.write_bytes(original_evidence)
        malformed_expiry = copy.deepcopy(authority_document)
        malformed_expiry["expires_at"] = {"not": "a timestamp"}
        malformed_expiry.pop("envelope_digest", None)
        malformed_expiry["envelope_digest"] = decision.digest(malformed_expiry)
        forged_production = copy.deepcopy(authority_document)
        forged_production.update({
            "issuer_class": "cmd_owner",
            "source_class": "trusted_activation_authority_envelope",
            "authority_evidence_source": "separate_trusted_authority_readback",
        })
        forged_production.pop("envelope_digest", None)
        forged_production["envelope_digest"] = decision.digest(forged_production)
        if (
            not activation.authority_blocks(malformed_expiry, authority_path, request, True)
            or not activation.authority_blocks(
                forged_production,
                activation.TRUSTED_AUTHORITY_DIR / "worker-authored-prose.json",
                request,
                False,
            )
            or not recursive_sensitive_activation_authority_rejected
        ):
            blocks.append("BLOCKED_FOR_MK733J_ACTIVATION_AUTHORITY_NEGATIVE_CONTROLS")
        activated = run([
            sys.executable, str(ACTIVATION), "bootstrap", "--authority", str(authority_path),
            "--request", str(request_path), *bootstrap_args, "--test-capability-registry", str(test_registry),
        ])
        receipt_path = root / "current-receipt.json"
        activation_state_path = root / "activation-state.json"
        if activated.returncode or not receipt_path.is_file() or not activation_state_path.is_file():
            blocks.append("BLOCKED_FOR_MK733J_TEST_ACTIVATION")
        else:
            activated_state = json.loads(activation_state_path.read_text(encoding="utf-8"))
            activation_execution_tier_controls = (
                activated_state.get("mode") == "enforce"
                and activated_state.get("enforcement_active") is True
                and activated_state.get("execution_tier") == "autonomous_profile_qualified"
                and activated_state.get("delegated_autonomy") is True
            )
            original_activation_state = activation_state_path.read_bytes()
            malformed_tier_states_rejected = True
            for name, mutate in (
                ("autonomous-missing-delegation", lambda value: value.pop("delegated_autonomy", None)),
                ("autonomous-false-delegation", lambda value: value.update({"delegated_autonomy": False})),
                ("authority-missing-delegation", lambda value: (value.update({"execution_tier": "authority_gate_transition"}), value.pop("delegated_autonomy", None))),
                ("authority-nonbool-delegation", lambda value: value.update({"execution_tier": "authority_gate_transition", "delegated_autonomy": "false"})),
            ):
                malformed = json.loads(original_activation_state)
                mutate(malformed)
                malformed.pop("state_digest", None)
                malformed["state_digest"] = decision.digest(malformed)
                write(activation_state_path, malformed)
                malformed_tier_states_rejected = (
                    malformed_tier_states_rejected
                    and permission(hook(env, "apply_patch", patch_input)) == "deny"
                )
            activation_state_path.write_bytes(original_activation_state)
            activation_execution_tier_controls = activation_execution_tier_controls and malformed_tier_states_rejected
            if not activation_execution_tier_controls:
                blocks.append("BLOCKED_FOR_MK733J_ACTIVATION_EXECUTION_TIER")
            saved_receipt=receipt_path.read_bytes();receipt_path.unlink()
            no_receipt_reads=["pwd","ls","cat AGENTS.md","head -n 1 AGENTS.md","tail -n 1 AGENTS.md","nl -ba AGENTS.md","wc -l AGENTS.md","stat AGENTS.md","rg --files","sed -n 1,2p scripts/ops/mk733j_decision_os.py","sed -n 1p AGENTS.md docs/ops/MK733J_N_ACTIVATION_AND_ROLLBACK_20260710.md","git status --short","git status --short --branch","git diff --check","git --no-pager show --stat","git rev-parse HEAD","git --no-pager log -3 --oneline","git branch --show-current"]
            no_receipt_read_controls=all(permission(hook(env,"Bash",{"command":command}))!="deny" for command in no_receipt_reads)
            low_path="docs/ops/MK733J_N_ACTIVATION_AND_ROLLBACK_20260710.md"
            no_receipt_patch=permission(hook(env,"apply_patch",{"patch":"*** Begin Patch\n*** Update File: "+low_path+"\n@@\n-old\n+new\n*** End Patch"}))!="deny"
            no_receipt_edit=permission(hook(env,"Edit",{"file_path":low_path,"old_string":"old","new_string":"new","replace_all":False}))!="deny"
            no_receipt_edit_omitted=permission(hook(env,"Edit",{"file_path":low_path,"old_string":"old","new_string":"new"}))!="deny"
            no_receipt_edit_denials=(permission(hook(env,"Edit",{"file_path":".codex/hooks.json","old_string":"old","new_string":"new","replace_all":False}))=="deny" and permission(hook(env,"Edit",{"file_path":"docs/missing.md","old_string":"old","new_string":"new","replace_all":False}))=="deny" and permission(hook(env,"Edit",{"file_path":low_path,"old_string":"x"*4096,"new_string":"y","replace_all":False}))=="deny" and permission(hook(env,"Edit",{"file_path":low_path,"old_string":"old","new_string":"new","replace_all":True}))=="deny" and permission(hook(env,"Write",{"file_path":low_path,"content":"overwrite"}))=="deny")
            no_receipt_denials=["git push origin HEAD","git branch -D codex/example","git log","git log -3 --oneline","git --no-pager log --oneline","git log --ext-diff","git log --format=%H","git log -1001 --oneline","git --no-pager log -1001 --oneline","git -c core.pager=cat log","git submodule status","curl https://example.invalid","git status && git show","printf x > x","sed -i s/a/b/ x","sed --in-place s/a/b/ x","sed -n 'w out' x","sed -n 1p -e 'w out' AGENTS.md","sed -n 1p --expression 'w out' AGENTS.md","tail -f AGENTS.md","tail -F AGENTS.md","tail --follow AGENTS.md","tail --follow=name AGENTS.md","tail --retry AGENTS.md","rg --hidden x","rg --pre cat x","rg --follow x","rg -uuu x","git diff --ext-diff","git show --stat","git show --textconv","git status --output x","/tmp/cat AGENTS.md","/tmp/rg --files","/tmp/sed -n 1p AGENTS.md","/tmp/git status --short"]
            no_receipt_patches=["*** Begin Patch\n*** Update File: .codex/hooks.json\n@@\n-a\n+b\n*** End Patch","*** Begin Patch\n*** Update File: trusted_activation/a.json\n@@\n-a\n+b\n*** End Patch","*** Begin Patch\n*** Update File: mk733j_n_activation_state.json\n@@\n-a\n+b\n*** End Patch","*** Begin Patch\n*** Update File: scripts/ops/mk733j_x.py\n@@\n-a\n+b\n*** End Patch","*** Begin Patch\n*** Update File: docs/a.md\n*** Update File: docs/b.md\n@@\n-a\n+b\n*** End Patch","*** Begin Patch\n*** Add File: docs/new.md\n+x\n*** End Patch","*** Begin Patch\n*** Delete File: docs/a.md\n*** End Patch"]
            no_receipt_denial_controls=all(permission(hook(env,"Bash",{"command":command}))=="deny" for command in no_receipt_denials) and all(permission(hook(env,"apply_patch",{"patch":patch}))=="deny" for patch in no_receipt_patches)
            shadow_env={**env,"PATH":str(fake_bin)+os.pathsep+env.get("PATH","")}
            path_shadowed_reads_denied = all(
                permission(hook(shadow_env,"Bash",{"command":command})) == "deny"
                for command in ("cat AGENTS.md", "rg --files", "sed -n 1p AGENTS.md", "git status --short")
            ) and not marker.exists()
            no_receipt_denial_controls=(
                no_receipt_denial_controls
                and path_shadowed_reads_denied
            )
            # A receipt-free mutation is available only while the worktree is
            # clean.  It removes the state-bound receipt before the tool runs;
            # the next mutation sees the dirty worktree and is denied.
            write(receipt_path, json.loads(saved_receipt.decode("utf-8")))
            first_lightweight = hook(env, "apply_patch", {"patch":"*** Begin Patch\n*** Update File: "+low_path+"\n@@\n-old\n+new\n*** End Patch"})
            first_invalidated_receipt = permission(first_lightweight) != "deny" and not receipt_path.exists()
            env["MK733J_TEST_WORKTREE_STATUS"] = " M " + low_path + "\n"
            second_lightweight = hook(env, "Edit", {"file_path":low_path,"old_string":"old","new_string":"new","replace_all":False})
            env["MK733J_TEST_WORKTREE_STATUS"] = ""
            receipt_free_mutation_one_shot_controls = first_invalidated_receipt and permission(second_lightweight) == "deny"
            if not receipt_free_mutation_one_shot_controls:
                blocks.append("BLOCKED_FOR_MK733J_RECEIPT_FREE_MUTATION_ONE_SHOT")
            stale_receipt=reseal_receipt({**json.loads(saved_receipt),"expires_at":"2000-01-01T00:00:00Z"})
            write(receipt_path,stale_receipt)
            stale_read_allowed=permission(hook(env,"Bash",{"command":"cat AGENTS.md"}))!="deny"
            stale_edit_allowed=permission(hook(env,"Edit",{"file_path":low_path,"old_string":"old","new_string":"new"}))!="deny"
            stale_nontier_denied=permission(hook(env,"Bash",{"command":"python3 --version"}))=="deny"
            receipt_path.write_text("{unreadable\n",encoding="utf-8")
            unreadable_read_allowed=permission(hook(env,"Bash",{"command":"git --no-pager show --stat"}))!="deny"
            unreadable_edit_allowed=permission(hook(env,"Edit",{"file_path":low_path,"old_string":"old","new_string":"new","replace_all":False}))!="deny"
            unreadable_protected_edit_denied=permission(hook(env,"Edit",{"file_path":".codex/hooks.json","old_string":"old","new_string":"new","replace_all":False}))=="deny"
            unreadable_nontier_denied=permission(hook(env,"Bash",{"command":"python3 --version"}))=="deny"
            tiered_stale_receipt_controls=all((stale_read_allowed,stale_edit_allowed,stale_nontier_denied,unreadable_read_allowed,unreadable_edit_allowed,unreadable_protected_edit_denied,unreadable_nontier_denied))
            receipt_path.write_bytes(saved_receipt)
            if not no_receipt_read_controls or not no_receipt_patch or not no_receipt_edit or not no_receipt_edit_omitted or not no_receipt_edit_denials or not no_receipt_denial_controls or not tiered_stale_receipt_controls:
                blocks.append("BLOCKED_FOR_MK733J_TIERED_NONAUTHORITY_BOUNDARY")
            # Denials happen before the valid operations, proving they do not
            # consume or expand the bounded manifest.
            denial_cases = [
                ("Bash", {"command": "python3 -V"}),
                ("Bash", {"command": "python3 --version && git status"}),
                ("Bash", {"command": "git push origin HEAD"}),
                ("Bash", {"command": "git merge main"}),
                ("Bash", {"command": "git reset --hard HEAD"}),
                ("Bash", {"command": "curl https://example.invalid"}),
                ("Bash", {"command": "python3 -c 'print(1)'"}),
                ("Bash", {"command": "bash -c 'python3 --version'"}),
                ("Bash", {"command": "python3 scripts/ops/safe.py --output=/tmp/escaped.json"}),
                ("Bash", {"command": "python3 scripts/ops/safe.py -o/tmp/escaped.json"}),
                ("Bash", {"command": "python3 scripts/ops/safe.py --output=$HOME/out.json"}),
                ("Bash", {"command": "printf x > outside.txt"}),
                ("Bash", {"command": "python3 <(printf x)"}),
                ("Bash", {"command": "python3 scripts/ops/*.py"}),
                ("Bash", {}),
                ("Write", {"file_path": ".env"}),
                ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: docs/out-of-scope.md\n@@\n-a\n+b\n*** End Patch"}),
                ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: scripts/ops/mk733j-e2e-target.py\n*** Move to: ../escape.py\n*** End Patch"}),
                ("apply_patch", {"patch": "*** Begin Patch\n*** Rename File: scripts/ops/mk733j-e2e-target.py\n*** End Patch"}),
                ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: scripts/ops/mk733j-e2e-target.py\n@@\n-old\n+" + ("x" * 5000) + "\n*** End Patch"}),
            ]
            if any(permission(hook(env, tool, tool_input)) != "deny" for tool, tool_input in denial_cases):
                blocks.append("BLOCKED_FOR_MK733J_SCOPE_NEGATIVE_CONTROLS")

            allowed_patch = hook(env, "apply_patch", patch_input)
            replay_patch = hook(env, "apply_patch", patch_input)
            allowed_bash = hook(env, "Bash", bash_input)
            safe_diagnostic = hook(env, "Bash", {"command": "git status --short"})
            receipt_after = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                allowed_patch.returncode
                or permission(allowed_patch) == "deny"
                or permission(replay_patch) != "deny"
                or allowed_bash.returncode
                or permission(allowed_bash) == "deny"
                or safe_diagnostic.returncode
                or permission(safe_diagnostic) == "deny"
                or receipt_after.get("budget", {}).get("remaining") != 0
                or receipt_after.get("operation_manifest", {}).get("bash_commands", [{}])[0].get("remaining") != 0
                or receipt_after.get("operation_manifest", {}).get("mutation_classes", {}).get("repo_patch", {}).get("remaining") != 0
            ):
                blocks.append("BLOCKED_FOR_MK733J_MULTI_STEP_OPERATION_CONSUMPTION")

            receipt_path.write_text("{}\n", encoding="utf-8")
            if permission(hook(env, "Bash", bash_input)) != "deny":
                blocks.append("BLOCKED_FOR_MK733J_RECEIPT_TAMPER_NEGATIVE")

            protected_input = {"command": "git push origin HEAD"}
            protected_operation_digest = operation_digest(
                tool_name="Bash", tool_input=protected_input, command_class="protected_git", paths=[],
                work_id=request["work_id"], context_digest=request["context_digest"],
                preflight_scope_digest=request["preflight_scope_digest"],
            )
            exact_protected_manifest = {
                "bash_commands": [{"operation_digest": protected_operation_digest, "command_class": "protected_git", "allowed_count": 1, "remaining": 1}],
                "mutation_classes": {}, "read_only_diagnostics": [],
            }
            protected_preflight = preflight_variant(
                "exact-protected-preflight.json",
                lambda body: body.update({"operation_manifest_digest": decision.manifest_policy_digest(exact_protected_manifest)}),
            )
            protected_request = {
                **protected_preflight,
                "operation_manifest": exact_protected_manifest,
                "preflight_operation_manifest_digest": decision.manifest_policy_digest(exact_protected_manifest),
                "allowed_command_classes": ["protected_git"],
                "forbidden_operation_classes": sorted(decision.PROTECTED_OPERATION_CLASSES - {"protected_git"}),
                "budget": {"total": 1, "remaining": 1},
                "external_protected_authority_state": "explicitly_authorized_exact_scope",
            }
            protected_target = {
                "target_repository": str(decision.REPO.resolve()),
                "target_path": ".",
                "target_revision": decision.current_revision(),
                "operation": "protected_git_push",
                "operation_digest": decision.target_operation_digest(
                    str(decision.REPO.resolve()), ".", decision.current_revision(), "protected_git_push"
                ),
                "rollback": "explicit_local_rollback",
                "exclusions": sorted(decision.TARGET_BOUND_EXCLUSIONS),
            }
            protected_request.update(protected_target)
            external_authority_path = root / "protected-operation-authority.json"
            identity_doc = json.loads(Path(request["runtime_identity_ref"]).read_text(encoding="utf-8"))
            authority_scope_value = {
                **protected_request, "selected_profile": "terra_high_implementer",
                "profile_digest": identity_doc["profile_digest"],
                "allowed_command_classes": sorted(set(protected_request["allowed_command_classes"])),
                "forbidden_operation_classes": sorted(set(protected_request["forbidden_operation_classes"])),
                "operation_manifest_policy_digest": decision.manifest_policy_digest(exact_protected_manifest),
                "workpack_digest": decision.current_workpack_digest(),
                "binding_record_digest": decision.current_binding_record_digest(),
            }
            external_authority = {
                "authority_type": "mk733j_external_protected_operation_authority",
                "source_class": "test_isolated_authority", "issuer": "isolated-e2e",
                "issuer_class": "test_isolated", "authority_ref": "isolated-protected-operation",
                "scope": "exact_receipt_scope", "work_id": request["work_id"], "goal_ref": request["goal_ref"],
                "selected_profile": "terra_high_implementer", "profile_digest": identity_doc["profile_digest"],
                "qualification_digest": request["qualification_digest"], "preflight_digest": protected_request["preflight_digest"],
                "context_digest": request["context_digest"], "workpack_digest": decision.current_workpack_digest(),
                "binding_record_digest": decision.current_binding_record_digest(),
                "scope_policy_digest": decision.external_authority_scope_digest(authority_scope_value),
                "allowed_operation_classes": ["protected_git"], "issued_at": "2026-07-10T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                **protected_target,
            }
            external_authority["authority_digest"] = decision.digest(external_authority)
            write(external_authority_path, external_authority)
            protected_request.update({"external_authority_ref": str(external_authority_path), "external_authority_digest": external_authority["authority_digest"]})
            protected_issue = issue(protected_request)
            if protected_issue.returncode:
                blocks.append("BLOCKED_FOR_MK733J_EXTERNAL_AUTHORITY_POSITIVE_ISSUANCE")
            else:
                protected_receipt = parsed(protected_issue)
                protected_negatives = []
                for name, mutate in (
                    ("wrong-scope", lambda value: value.update({"scope": "broad_scope"})),
                    ("wrong-issuer", lambda value: value.update({"issuer": ""})),
                    ("wrong-class", lambda value: value.update({"issuer_class": "cmd_owner"})),
                ):
                    bad_authority = copy.deepcopy(external_authority);mutate(bad_authority);bad_authority.pop("authority_digest",None);bad_authority["authority_digest"]=decision.digest(bad_authority)
                    write(external_authority_path,bad_authority);write(receipt_path,protected_receipt)
                    protected_negatives.append(permission(hook(env,"Bash",protected_input))=="deny")
                write(external_authority_path,external_authority)
                wrong_digest_receipt=copy.deepcopy(protected_receipt);wrong_digest_receipt["external_authority_digest"]="0"*64;write(receipt_path,reseal_receipt(wrong_digest_receipt))
                protected_negatives.append(permission(hook(env,"Bash",protected_input))=="deny")
                write(receipt_path,protected_receipt)
                protected_allow=hook(env,"Bash",protected_input);protected_after=json.loads(receipt_path.read_text(encoding="utf-8"))
                protected_authority_controls=all(protected_negatives) and protected_allow.returncode==0 and permission(protected_allow)!="deny" and protected_after.get("budget",{}).get("remaining")==0 and protected_after.get("operation_manifest",{}).get("bash_commands",[{}])[0].get("remaining")==0
                if not protected_authority_controls:
                    blocks.append("BLOCKED_FOR_MK733J_EXTERNAL_AUTHORITY_HOOK_CONTROLS")

        # Closeout issuance and Stop use the exact DecisionOSCloseout action.
        closeout_issue = run([
            sys.executable, str(REPO / "scripts/ops/mk733j_decision_os.py"),
            "issue-receipt", "--request", str(request_path), "--phase", "closeout",
            "--test-isolated", "--test-capability-registry", str(test_registry), "--json",
        ])
        closeout_path = root / "closeout-receipt.json"
        if closeout_issue.returncode:
            blocks.append("BLOCKED_FOR_MK733J_CLOSEOUT_ISSUANCE")
        else:
            closeout_path.write_text(closeout_issue.stdout, encoding="utf-8")
            closeout_doc = json.loads(closeout_issue.stdout)

            def verify_receipt(path: Path, phase: str = "closeout", tool: str = "DecisionOSCloseout") -> subprocess.CompletedProcess[str]:
                return run([
                    sys.executable, str(REPO / "scripts/ops/mk733j_decision_os.py"),
                    "verify-receipt", "--receipt", str(path), "--phase", phase,
                    "--tool", tool, "--test-isolated", "--test-capability-registry", str(test_registry), "--json",
                ])

            def mutated_receipt(name: str, change: Callable[[dict[str, Any]], None]) -> Path:
                value = copy.deepcopy(closeout_doc)
                change(value)
                value.pop("receipt_digest", None)
                value["receipt_digest"] = decision.digest(value)
                return write(root / name, value)

            def extra_qualification_row_receipt() -> Path:
                value = copy.deepcopy(closeout_doc)
                value["qualification_results"]["bounded_implementation"]["extra"] = "reject"
                value["qualification_results_digest"] = decision.digest(value["qualification_results"])
                return write(root / "extra-qualification-row.json", reseal_receipt(value))

            negative_receipts = [
                (closeout_path, "pre_work", "DecisionOSCloseout"),
                (closeout_path, "closeout", "Bash"),
                (mutated_receipt("stale.json", lambda value: value.update({"expires_at": "2000-01-01T00:00:00Z"})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("wrong-workpack.json", lambda value: value.update({"workpack_digest": "0" * 64})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("wrong-binding.json", lambda value: value.update({"binding_record_digest": value["workpack_digest"]})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("wrong-profile.json", lambda value: value.update({"profile_digest": "0" * 64})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("zero-budget.json", lambda value: value.update({"budget": {"total": 1, "remaining": 0}})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("wrong-identity-ref.json", lambda value: value.update({"runtime_identity_ref": str(root / "not-the-identity.json")})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("wrong-qualification-ref.json", lambda value: value.update({"qualification_result_ref": str(root / "not-the-qualification.json")})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("future-issued.json", lambda value: value.update({"issued_at": "2099-01-01T00:00:00Z"})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("mixed-case-prompt.json", lambda value: value.update({"Raw_Prompt": "sensitive"})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("prompt-alias.json", lambda value: value.update({"PROMPT_TEXT": "sensitive"})), "closeout", "DecisionOSCloseout"),
                (mutated_receipt("camel-prompt.json", lambda value: value.update({"rawPrompt": "sensitive"})), "closeout", "DecisionOSCloseout"),
                (extra_qualification_row_receipt(), "closeout", "DecisionOSCloseout"),
            ]
            if any(verify_receipt(path, phase, tool).returncode == 0 for path, phase, tool in negative_receipts):
                blocks.append("BLOCKED_FOR_MK733J_RECEIPT_CLI_NEGATIVE_CONTROLS")

            stop_env = {**env, "MK733J_CLOSEOUT_RECEIPT_PATH": str(closeout_path)}
            first_stop = run([sys.executable, str(STOP)], env=stop_env, wire={"hook_event_name": "Stop", "stop_hook_active": False})
            consumed = json.loads(closeout_path.read_text(encoding="utf-8"))
            second_stop = run([sys.executable, str(STOP)], env=stop_env, wire={"hook_event_name": "Stop", "stop_hook_active": False})
            loop_guard = run([sys.executable, str(STOP)], env=stop_env, wire={"hook_event_name": "Stop", "stop_hook_active": True})
            if (
                first_stop.returncode or first_stop.stdout.strip()
                or consumed.get("budget", {}).get("remaining") != 0
                or consumed.get("operation_manifest", {}).get("closeout", {}).get("remaining") != 0
                or second_stop.returncode or parsed(second_stop).get("decision") != "block"
                or not parsed(second_stop).get("reason")
                or loop_guard.returncode or loop_guard.stdout.strip()
            ):
                blocks.append("BLOCKED_FOR_MK733J_STOP_CLOSEOUT_CONSUMPTION")

        # Corruption is recovery-only, ordinary mutation is denied, diagnostics
        # remain available, and rollback restores shadow without touching the
        # repository-local activation directory.
        (root / "activation-state.json").write_text("{bad\n", encoding="utf-8")
        corrupt_mutation = hook(env, "apply_patch", patch_input)
        corrupt_diagnostic = hook(env, "Bash", {"command": "git status --short"})
        corrupt_safe_read = hook(env, "Bash", {"command": "cat AGENTS.md"})
        corrupt_edit = hook(env, "Edit", {"file_path": "docs/ops/MK733J_N_ACTIVATION_AND_ROLLBACK_20260710.md", "old_string": "old", "new_string": "new"})
        configured_recovery_python = "/usr/bin/python3"
        documented_relative_rollback = hook(env, "Bash", {"command": f"{configured_recovery_python} scripts/ops/mk733j_activation.py rollback"}, interpreter=configured_recovery_python)
        path_resolved_python_rejected = hook(env, "Bash", {"command": "python3 scripts/ops/mk733j_activation.py rollback"}, interpreter=configured_recovery_python)
        foreign_cwd_relative_rollback = hook(env, "Bash", {"command": f"{configured_recovery_python} scripts/ops/mk733j_activation.py rollback", "cwd": str(root / "foreign-cwd")}, interpreter=configured_recovery_python)
        near_relative_rollback = hook(env, "Bash", {"command": f"{configured_recovery_python} scripts/ops/mk733j_activation.py rollback --extra"}, interpreter=configured_recovery_python)
        corrupt_evil_python = hook(env, "Bash", {"command": "evilpython3 scripts/ops/mk733j_activation.py rollback"}, interpreter=configured_recovery_python)
        corrupt_bootstrap = hook(env, "Bash", {"command": f"{configured_recovery_python} {ACTIVATION} bootstrap --authority {authority_path} --request {request_path}"}, interpreter=configured_recovery_python)
        foreign_cwd_relative_rollback_rejected = permission(foreign_cwd_relative_rollback) == "deny"
        recovery_tier_controls = permission(corrupt_safe_read) != "deny" and permission(corrupt_edit) == "deny"
        recovery_bootstrap_rejected = permission(corrupt_bootstrap) == "deny"
        corrupt_stop = run([sys.executable, str(STOP)], env=env, wire={"hook_event_name": "Stop", "stop_hook_active": False})
        rollback = run([configured_recovery_python, str(ACTIVATION), "rollback", *bootstrap_args])
        after_rollback = hook(env, "apply_patch", patch_input)
        rollback_state = json.loads((root / "activation-state.json").read_text(encoding="utf-8")) if rollback.returncode == 0 else {}
        supervised_multi_patch = hook(env, "apply_patch", {
            "patch": "*** Begin Patch\n*** Update File: docs/ops/MK733J_N_ACTIVATION_AND_ROLLBACK_20260710.md\n@@\n-old\n+new\n*** Update File: docs/ops/MK733J_N_TERRA_IMPLEMENTATION_STATUS_20260710.md\n@@\n-old\n+new\n*** End Patch"
        })
        supervised_validator = hook(env, "Bash", {
            "command": "python3 scripts/ops/verify_mk733g_decision_os_firing.py --base-dir . --json"
        })
        supervised_shadow_tier_controls = (
            rollback_state.get("mode") == "shadow"
            and rollback_state.get("enforcement_active") is False
            and rollback_state.get("execution_tier") == "normal_local_bounded_supervised"
            and permission(supervised_multi_patch) != "deny"
            and permission(supervised_validator) != "deny"
        )
        configured_recovery_rollback_e2e = (
            documented_relative_rollback.returncode == 0
            and permission(documented_relative_rollback) != "deny"
            and permission(path_resolved_python_rejected) == "deny"
            and rollback.returncode == 0
        )
        if (
            permission(corrupt_mutation) != "deny"
            or corrupt_diagnostic.returncode or permission(corrupt_diagnostic) == "deny"
            or not recovery_tier_controls
            or not recovery_bootstrap_rejected
            or not configured_recovery_rollback_e2e
            or not foreign_cwd_relative_rollback_rejected
            or permission(near_relative_rollback) != "deny"
            or permission(corrupt_evil_python) != "deny"
            or corrupt_stop.returncode or parsed(corrupt_stop).get("decision") == "block"
            or "recovery-only" not in parsed(corrupt_stop).get("systemMessage", "")
            or rollback.returncode
            or not supervised_shadow_tier_controls
            or "additionalContext" not in parsed(after_rollback).get("hookSpecificOutput", {})
            or permission(after_rollback) == "deny"
        ):
            blocks.append("BLOCKED_FOR_MK733J_RECOVERY_ROLLBACK_E2E")

    result = {
        "status": "PASS_RECEIPT_HOOK_E2E_NEGATIVE_CONTROLS" if not blocks else "FAIL_RECEIPT_HOOK_E2E_NEGATIVE_CONTROLS",
        "blocks": sorted(set(blocks)),
        "controls": {
            "ordered_composite_qualification_results": composite_controls,
            "external_protected_authority_hook_path": protected_authority_controls,
            "foreign_cwd_relative_rollback_rejected": foreign_cwd_relative_rollback_rejected,
            "configured_recovery_rollback_e2e": configured_recovery_rollback_e2e,
            "path_resolved_python_rejected": permission(path_resolved_python_rejected) == "deny",
            "tiered_no_receipt_read_and_reversible_patch_boundary": no_receipt_read_controls and no_receipt_patch and no_receipt_denial_controls,
            "low_risk_branch_and_edit_parity_controls": branch_helper_controls and no_receipt_edit and no_receipt_edit_denials,
            "protected_path_exact_match_controls": protected_path_exact_match_controls,
            "active_policy_index_fail_closed_controls": active_policy_index_fail_closed_controls,
            "git_read_argv_exactness_controls": git_read_argv_exactness_controls,
            "trusted_read_executable_provenance_controls": trusted_executable_provenance_controls,
            "configured_hook_interpreter_compatibility": configured_hook_interpreter_compatibility,
            "git_root_resolved_hook_launchers": git_root_resolved_hook_launchers,
            "activation_execution_tier_controls": activation_execution_tier_controls,
            "normal_local_bounded_supervised_shadow_controls": supervised_shadow_tier_controls,
            "session_start_trusted_dirname_controls": session_start_trusted_dirname_controls,
            "receipt_free_mutation_one_shot_controls": receipt_free_mutation_one_shot_controls,
            "tiered_stale_or_unreadable_receipt_controls": tiered_stale_receipt_controls,
            "recovery_read_only_tier_controls": recovery_tier_controls,
            "recovery_bootstrap_rejected": recovery_bootstrap_rejected,
            "test_branch_override_isolated": test_branch_override_controls,
            "recursive_sensitive_activation_authority_rejected": recursive_sensitive_activation_authority_rejected,
            "preflight_malformed_and_sensitive_rejected": preflight_malformed_and_sensitive_rejected,
            "non_object_preflight_schema_rejected": non_object_preflight_schema_rejected,
            "extra_qualification_bundle_route_rejected": extra_qualification_bundle_route_rejected,
            "extra_qualification_bundle_receipt_rejected": extra_qualification_bundle_receipt_rejected,
            "extra_qualification_row_route_rejected": extra_qualification_row_route_rejected,
            "extra_qualification_row_receipt_rejected": extra_qualification_row_receipt_rejected,
        },
        "non_claims": [
            "test_isolated_only",
            "no_empirical_model_qualification",
            "no_repository_enforcement_activation",
            "no_fresh_session_runtime_trust",
        ],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not blocks else 1


if __name__ == "__main__":
    raise SystemExit(main())
