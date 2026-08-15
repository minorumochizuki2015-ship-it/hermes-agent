#!/usr/bin/env python3
"""Fail-closed verifier for the repo-local MK733J-N Decision OS implementation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import mk733j_capability_bundles as capability
import mk733j_context_compiler as context_compiler
import mk733j_qualification as qualification

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "research/mk675/fable5_decision_os/mk733j_n_decision_os_implementation.json"
TOOL = REPO / "scripts/ops/mk733j_decision_os.py"
MEASUREMENT_ROOT = REPO / "research/mk675/fable5_decision_os/mk733n_measurements"
MEASUREMENT_RECORD = MEASUREMENT_ROOT / "mk733n_qwen_public_pair_measurement.json"
REQUIRED_SKILLS = ["grand-goal-native-goal-formulation", "goal-audit-checklist", "best-evaluate", "agent-dispatch", "fable5-derived-advisory-synthesis", "skill-lifecycle"]
SOURCE_CHECK_RUNTIME_CLAIM_BLOCKS = frozenset(
    {
        "BLOCKED_FOR_MK733J_N_ACTIVATION_E2E",
        "BLOCKED_FOR_MK733J_N_FINAL_AUDIT_RECEIPT_EXECUTION",
        "BLOCKED_FOR_MK733J_N_HOOK_DENY_CONTRACT",
        "BLOCKED_FOR_MK733J_N_HOOK_SHADOW_CONTRACT",
        "BLOCKED_FOR_MK733J_N_PERMANENT_LOCKOUT",
    }
)
SKILL_REQUIREMENTS = {
    "grand-goal-native-goal-formulation": ["Trigger:", "mk733j_decision_os.py route", "In shadow", "in enforce", "Roll back", "does not"],
    "goal-audit-checklist": ["Trigger:", "consume-receipt", "In enforce", "In shadow", "rollback", "does not"],
    "best-evaluate": ["Trigger:", "mk733j_decision_os.py route", "shadow", "enforce", "rollback", "does not"],
    "agent-dispatch": ["Trigger:", "mk733j_decision_os.py route", "Shadow", "enforce", "readback", "rolls back"],
    "fable5-derived-advisory-synthesis": ["Trigger:", "Produce observable criteria", "shadow", "enforce", "rolls back", "does not"],
    "skill-lifecycle": ["Trigger:", "verify_mk733j_n_implementation.py", "Shadow", "Enforce", "readback", "rollback", "never claim"],
}
AGENT_COMMON_RETURN_FIELDS = (
    "work_id", "goal_ref", "profile_id", "task_class", "risk_class", "runtime_model_identity",
    "runtime_identity_ref", "qualification_result_ref", "profile_result_ref",
    "workpack_digest", "binding_record_digest", "context_digest", "preflight_ref",
    "preflight_digest", "preflight_contract_version", "budget_used", "blockers",
    "evidence_refs", "non_claims", "next_boundary", "return_thread_id",
    "readback_required=true",
)
AGENT_CONTRACTS = {
    "terra-high-implementer.toml": {
        "name": "terra-high-implementer", "model": "gpt-5.6-terra", "effort": "high",
        "sandbox": "workspace-write",
        "identity": {"runtime_model_identity": "gpt-5.6-terra", "runtime_family": "terra", "profile_id": "terra_high_implementer", "reasoning_effort": "high"},
        "skills": [
            "../../skills/workflow-plan-test-patch/SKILL.md",
            "../../skills/task-tracker/SKILL.md",
            "../../skills/verify-claims/SKILL.md",
            "../../skills/report-guard/SKILL.md",
        ],
        "instruction_tokens": AGENT_COMMON_RETURN_FIELDS + (
            "runtime_family=terra", "profile_id=terra_high_implementer",
            "reasoning_effort=high", "aliases and substring matches do not qualify",
            "changed_files", "tests_run", "test_results", "validations", "unauthorized_files",
            "result_artifact_ref", "result_digest", "budget_outcome",
            "Do not issue the final independent audit verdict",
        ),
    },
    "sol-independent-reviewer.toml": {
        "name": "sol-independent-reviewer", "model": "gpt-5.6", "effort": "ultra",
        "sandbox": "read-only",
        "identity": {"runtime_model_identity": "gpt-5.6-sol", "runtime_family": "sol", "profile_id": "sol_independent_reviewer", "reasoning_effort": "ultra"},
        "skills": [
            "../../skills/implementation-audit-dispatch/SKILL.md",
            "../../skills/verify-claims/SKILL.md",
            "../../skills/report-guard/SKILL.md",
        ],
        "instruction_tokens": AGENT_COMMON_RETURN_FIELDS + (
            "runtime_family=sol", "profile_id=sol_independent_reviewer",
            "reasoning_effort=ultra", "aliases and substring matches do not qualify",
            "reviewer_thread_run_id", "implementer_thread_run_id", "independence_verified",
            "audited_head_sha", "comparison_base_sha", "findings", "critical_findings",
            "tests_observed", "validations", "patch_or_diff_emitted=false", "verdict",
            "Never create, edit, apply, or emit a patch/diff",
        ),
    },
    "terra-readonly-explorer.toml": {
        "name": "terra-readonly-explorer", "model": "gpt-5.6-terra", "effort": "medium",
        "sandbox": "read-only",
        "identity": {"runtime_model_identity": "gpt-5.6-terra", "runtime_family": "terra", "profile_id": "terra_readonly_explorer", "reasoning_effort": "medium"},
        "skills": [
            "../../skills/repo-entrypoints-and-rules/SKILL.md",
            "../../skills/skill-select/SKILL.md",
        ],
        "instruction_tokens": AGENT_COMMON_RETURN_FIELDS + (
            "runtime_family=terra", "profile_id=terra_readonly_explorer",
            "reasoning_effort=medium", "aliases and substring matches do not qualify",
            "thread_run_id", "inventory", "cited_facts", "uncertainties", "missing_paths",
            "mutation_artifact=false", "validations_or_none", "path, line_or_symbol, fact, and citation",
        ),
    },
}
REQUIRED_FILES = [
    ".codex/hooks.json", ".codex/hooks/mk733j-session-start.sh", ".codex/hooks/mk733j-pretooluse.py",
    ".codex/hooks/mk734b_event_claim.py",
    ".codex/hooks/mk733j-stop.py", ".codex/hooks/mk748_session_start.py",
    ".codex/agents/terra-high-implementer.toml",
    ".codex/agents/sol-independent-reviewer.toml", ".codex/agents/terra-readonly-explorer.toml",
    "scripts/ops/mk733j_hook_contract_self_test.py",
    "scripts/ops/mk733j_final_audit_receipt_self_test.py",
    "scripts/ops/mk733j_shadow_telemetry.py",
    "scripts/ops/mk733j_session_continuity.py",
    "scripts/ops/mk748_session_start_cognitive_context_self_test.py",
    "schemas/mk675/mk748_session_cognitive_binding.v1.schema.json",
    "scripts/ops/mk733j_intent_lock.py",
    "scripts/ops/mk_worktree_status.py",
    "scripts/ops/check_mk734b_plugin_distribution.py",
    "scripts/ops/mk734b_plugin_hook_self_test.py",
    "plugins/orch-next-codex-harness/hooks/hooks.json",
    "plugins/orch-next-codex-harness/hooks/decision_os_shadow.py",
    "scripts/ops/run_mk733n_public_qwen_measurement.py",
    "research/mk675/fable5_decision_os/mk733j_n_trusted_activation_authorities.json",
    "research/mk675/fable5_decision_os/qualification-authorities/provider-attestations.json",
    "research/mk675/fable5_decision_os/subagent_audit_register.json",
    "research/mk675/fable5_decision_os/mk733n_measurements/mk733n_qwen_public_pair_measurement.json",
    "docs/ops/MK733J_N_ACTIVATION_AND_ROLLBACK_20260710.md",
]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def measurement_ref(value: Any) -> Path | None:
    """Resolve only committed-style JSON refs inside the MK733N measurement root."""
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    path = (REPO / value).resolve()
    if (
        MEASUREMENT_ROOT.resolve() not in path.parents
        or path.suffix != ".json"
        or not path.is_file()
    ):
        return None
    return path


def qualification_threshold_passed(grade: dict[str, Any]) -> bool:
    thresholds = qualification.QUALIFICATION_THRESHOLDS
    return (
        grade.get("critical_false_accepts") == thresholds["critical_false_accepts"]
        and grade.get("required_escalation_recall", 0) >= thresholds["required_escalation_recall"]
        and grade.get("weighted_disposition_match", 0) >= thresholds["weighted_disposition_match"]
        and grade.get("seeded_mutation_rejection", 0) >= thresholds["seeded_mutation_rejection"]
        and grade.get("unnecessary_sol_escalation_rate", 1) <= thresholds["unnecessary_sol_escalation_rate"]
    )


def measurement_binding_blocks(measurement: Any) -> list[str]:
    """Recompute the retained public measurement instead of trusting its labels."""
    block = "BLOCKED_FOR_MK733N_PUBLIC_MEASUREMENT_BINDING"
    blocks: list[str] = []
    required_keys = {
        "artifact_type", "artifact_version", "status", "measurement_mode", "model",
        "reasoning_effort", "evaluation_corpus_digest", "evaluation_schema_digest",
        "context_refs", "context_digests", "compiled_to_baseline_ratio",
        "attempt_budget", "attempts", "strict_existing_grade",
        "aggregate_diagnostic_not_acceptance", "decision_score_regression_points",
        "pair_comparison_state", "observed_conclusions", "lora_decision",
        "support_work_progress_credit", "observed_effective_prevention_claimed",
        "non_claims",
    }
    if not isinstance(measurement, dict) or set(measurement) != required_keys:
        return [block]
    if (
        measurement.get("artifact_type") != "mk733n_qwen_public_context_pair_measurement"
        or measurement.get("artifact_version") != 1
        or measurement.get("status") != "PAIR_SCORE_NOT_OBTAINED_BASELINE_STRUCTURAL_FAILURE_COMPILED_JUDGMENT_BELOW_THRESHOLD"
        or measurement.get("measurement_mode") != "public_observable_not_qualified"
        or measurement.get("model") != "qwen3.6:35b-a3b-coding-mxfp8"
        or measurement.get("reasoning_effort") != "high"
        or measurement.get("decision_score_regression_points") is not None
        or measurement.get("pair_comparison_state") != "not_scored_because_baseline_did_not_produce_an_expandable_output"
        or measurement.get("support_work_progress_credit") != 0
        or measurement.get("observed_effective_prevention_claimed") is not False
    ):
        blocks.append(block)
    contract_digests = qualification.evaluation_contract_digests()
    if any(measurement.get(key) != value for key, value in contract_digests.items()):
        blocks.append(block)
    required_non_claims = {
        "no_profile_qualification_or_import", "no_route_unlock", "no_model_parity",
        "no_runtime_readiness", "no_product_user_or_final_acceptance",
        "no_ui_or_remote_ops_mutation", "no_observed_effective_prevention",
    }
    if set(measurement.get("non_claims", [])) != required_non_claims:
        blocks.append(block)
    lora = measurement.get("lora_decision")
    if (
        not isinstance(lora, dict)
        or lora.get("mechanical_smoke") != "completed_in_isolated_background_lane_not_quality_or_repo_acceptance_evidence"
        or lora.get("direct_promotion_or_route_unlock") is not False
        or not isinstance(lora.get("training_targets"), list)
        or not isinstance(lora.get("required_before_quality_training"), list)
    ):
        blocks.append(block)
    budget = measurement.get("attempt_budget")
    if budget != {
        "rule": "one_request_maximum_per_unique_context_variant",
        "same_variant_retries_performed": 0,
        "additional_model_attempts_allowed_in_current_goal": 0,
    }:
        blocks.append(block)

    context_refs = measurement.get("context_refs")
    context_digests = measurement.get("context_digests")
    contexts: dict[str, dict[str, Any]] = {}
    if not isinstance(context_refs, dict) or set(context_refs) != {"baseline", "compiled"}:
        blocks.append(block)
    if not isinstance(context_digests, dict) or set(context_digests) != {"baseline", "compiled"}:
        blocks.append(block)
    if not blocks:
        for role in ("baseline", "compiled"):
            path = measurement_ref(context_refs[role])
            try:
                context = load(path) if path else None
            except (OSError, json.JSONDecodeError):
                context = None
            if (
                not context_compiler.valid_context_artifact(context, role)
                or context.get("context_digest") != context_digests[role]
            ):
                blocks.append(block)
                continue
            contexts[role] = context
        compiled = contexts.get("compiled", {})
        if compiled.get("compiled_to_baseline_ratio") != measurement.get("compiled_to_baseline_ratio"):
            blocks.append(block)

    expected_attempts = (
        ("mk733n-qwen-public-baseline-v3", "baseline", "mk733j-compact-ordered-v3", "failed", "contract_validation_failed"),
        ("mk733n-qwen-public-baseline-v3h1", "baseline", "mk733j-compact-ordered-v3", "failed", "compact_expansion_failed"),
        ("mk733n-qwen-public-baseline-v4", "baseline", "mk733j-compact-ordered-v4", "failed", "compact_expansion_failed"),
        ("mk733n-qwen-public-compiled-v4", "compiled", "mk733j-compact-ordered-v4", "completed", None),
    )
    attempts = measurement.get("attempts")
    completed_marker: dict[str, Any] | None = None
    if not isinstance(attempts, list) or len(attempts) != len(expected_attempts):
        blocks.append(block)
    else:
        for row, (variant, role, protocol, status, failure_class) in zip(attempts, expected_attempts):
            expected_row_keys = {"context_variant", "protocol", "attempt_ref", "status"}
            if status == "failed":
                expected_row_keys.add("failure_class")
            else:
                expected_row_keys |= {"output_ref", "output_digest"}
            if not isinstance(row, dict) or set(row) != expected_row_keys:
                blocks.append(block)
                continue
            marker_path = measurement_ref(row.get("attempt_ref"))
            try:
                marker = load(marker_path) if marker_path else None
            except (OSError, json.JSONDecodeError):
                marker = None
            context = contexts.get(role, {})
            if (
                not isinstance(marker, dict)
                or row.get("context_variant") != variant
                or row.get("protocol") != protocol
                or row.get("status") != status
                or marker.get("artifact_type") != "mk733n_public_qwen_attempt_marker"
                or marker.get("context_variant") != variant
                or marker.get("context_role") != role
                or marker.get("context_ref") != context_refs.get(role)
                or marker.get("context_digest") != context.get("context_digest")
                or marker.get("protocol") != protocol
                or marker.get("status") != status
                or marker.get("model") != measurement.get("model")
                or marker.get("reasoning_effort") != measurement.get("reasoning_effort")
                or marker.get("attempt_limit_for_variant") != 1
                or not isinstance(marker.get("run_family"), str)
                or not marker.get("run_family")
            ):
                blocks.append(block)
                continue
            if status == "failed":
                if row.get("failure_class") != failure_class or marker.get("failure_class") != failure_class:
                    blocks.append(block)
            else:
                if (
                    marker.get("output_ref") != row.get("output_ref")
                    or marker.get("output_digest") != row.get("output_digest")
                ):
                    blocks.append(block)
                completed_marker = marker

    completed_row = attempts[-1] if isinstance(attempts, list) and attempts else {}
    output_path = measurement_ref(completed_row.get("output_ref")) if isinstance(completed_row, dict) else None
    try:
        compact_output = load(output_path) if output_path else None
        if not isinstance(compact_output, dict) or qualification.sensitive(compact_output):
            raise ValueError("invalid retained compact output")
        output_digest = qualification.digest(compact_output)
        expanded = qualification.expand_compact_outputs(compact_output)
        grade = qualification.grade(expanded)
        diagnostic = qualification.diagnose_compact_outputs(compact_output)
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError, AttributeError):
        blocks.append(block)
    else:
        metric_keys = (
            "critical_false_accepts", "required_escalation_recall",
            "weighted_disposition_match", "seeded_mutation_rejection",
            "unnecessary_sol_escalation_rate",
        )
        strict = {key: grade.get(key) for key in metric_keys}
        strict["qualification_threshold_passed"] = qualification_threshold_passed(grade)
        if (
            grade.get("blocks")
            or output_digest != completed_row.get("output_digest")
            or strict != measurement.get("strict_existing_grade")
            or diagnostic != measurement.get("aggregate_diagnostic_not_acceptance")
            or not isinstance(completed_marker, dict)
            or completed_marker.get("observable_grade") != {key: grade.get(key) for key in metric_keys}
            or completed_marker.get("diagnostic_grade") != diagnostic
        ):
            blocks.append(block)
    return sorted(set(blocks))


def subagent_register_blocks(implementation: dict[str, Any]) -> list[str]:
    block = "BLOCKED_FOR_MK734_SUBAGENT_REGISTER_DISPOSITION_DRIFT"
    path = REPO / "research/mk675/fable5_decision_os/subagent_audit_register.json"
    try:
        register = load(path)
    except (OSError, json.JSONDecodeError):
        return [block]
    roles = {
        row.get("role_id"): row
        for row in register.get("roles", [])
        if isinstance(row, dict) and isinstance(row.get("role_id"), str)
    }
    expected = implementation.get("subagent_dispositions", {})
    expected_status = {
        "SUB-001": "dormant_until_explicit_ui_authority",
        "SUB-002": "not_required_this_slice",
        "SUB-003": "retired",
    }
    if (
        set(roles) != set(expected_status)
        or "dormant_until_explicit_ui_authority" not in set(register.get("status_enum", []))
        or any(
            roles[role_id].get("status") != status
            or roles[role_id].get("disposition") != expected.get(role_id)
            for role_id, status in expected_status.items()
        )
    ):
        return [block]
    return []


def installed_skill_cache_health(cache_root: Path | None = None, canonical_root: Path | None = None) -> dict[str, Any]:
    """Read-only cache drift detection; a matching copy is never firing proof."""
    cache_root = cache_root or (Path.home() / ".codex" / "plugins" / "cache")
    canonical_root = canonical_root or (REPO / "skills")
    result = {"checked": cache_root.exists(), "matching": [], "stale": [], "missing": []}
    for skill in REQUIRED_SKILLS:
        canonical = canonical_root / skill / "SKILL.md"
        candidates = list(cache_root.glob(f"**/skills/{skill}/SKILL.md")) if cache_root.exists() else []
        if not candidates:
            result["missing"].append(skill)
        elif canonical.exists():
            matching=[path for path in candidates if path.read_bytes() == canonical.read_bytes()]
            stale=[path for path in candidates if path.read_bytes() != canonical.read_bytes()]
            if matching: result["matching"].append(skill)
            if stale: result["stale"].append(skill)
        else:
            result["stale"].append(skill)
    result["status"] = "installed_cache_not_present_or_stale_manual_gate_required" if result["missing"] or result["stale"] else "installed_cache_content_matches_only_no_runtime_firing_claim"
    return result


def installed_skill_cache_health_self_test() -> bool:
    """Read-only synthetic cache classification; never touches the installed cache."""
    with tempfile.TemporaryDirectory(prefix="mk733j-cache-health-") as directory:
        root=Path(directory);canonical=root/"canonical";cache=root/"cache";skill=REQUIRED_SKILLS[0]
        source=(REPO/"skills"/skill/"SKILL.md").read_bytes();(canonical/skill).mkdir(parents=True);(canonical/skill/"SKILL.md").write_bytes(source)
        current=cache/"current"/"skills"/skill;stale=cache/"stale"/"skills"/skill;current.mkdir(parents=True);stale.mkdir(parents=True);(current/"SKILL.md").write_bytes(source);(stale/"SKILL.md").write_text("stale",encoding="utf-8")
        mixed=installed_skill_cache_health(cache,canonical)
        missing=installed_skill_cache_health(root/"missing",canonical)
        return skill in mixed["matching"] and skill in mixed["stale"] and mixed["status"]=="installed_cache_not_present_or_stale_manual_gate_required" and skill in missing["missing"]


def mutate(doc: dict[str, Any], mutations: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(doc)
    for dotted, value in mutations.items():
        cursor = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            cursor = cursor[int(part)] if isinstance(cursor, list) else cursor.setdefault(part, {})
        if isinstance(cursor, list):
            cursor[int(parts[-1])] = value
        else:
            cursor[parts[-1]] = value
    return out


def public_observation_invalid(row: Any, local_profile: dict[str, Any]) -> bool:
    required = {
        "profile_id", "runtime_model_identity", "reasoning_effort", "task_class", "bundle_id",
        "run_family", "thread_run_id", "packet_digest", "evaluation_corpus_digest",
        "evaluation_schema_digest", "public_semantic_contract_ref",
        "public_semantic_contract_digest", "output_digest", "output", "metrics",
        "execution_environment", "grader_gold_access", "status", "qualification_state",
        "artifact_retention", "route_unlock", "support_work_progress_credit",
    }
    if not isinstance(row, dict) or set(row) != required:
        return True
    if (
        row.get("profile_id") != "local_qualified_worker"
        or row.get("runtime_model_identity") != local_profile.get("runtime_model_identity_pattern")
        or row.get("reasoning_effort") != local_profile.get("reasoning_effort")
        or row.get("task_class") != "deterministic_support"
        or row.get("bundle_id") != "deterministic_support"
        or not isinstance(row.get("run_family"), str)
        or not row["run_family"]
        or not isinstance(row.get("thread_run_id"), str)
        or not row["thread_run_id"]
        or row.get("execution_environment") != "projectless"
        or row.get("grader_gold_access") is not False
        or row.get("status") != "public_observable_pass_not_qualified"
        or row.get("qualification_state") != "not_qualified_without_separate_sol_owned_sealed_holdout"
        or row.get("artifact_retention") != "safe_structured_output_embedded_no_provider_raw_or_reasoning"
        or row.get("route_unlock") is not False
        or row.get("support_work_progress_credit") != 0
    ):
        return True
    try:
        packet = capability.packet(row["bundle_id"], row["run_family"], row["profile_id"])
        output_blocks, recomputed_metrics = capability.public(row["bundle_id"], row["output"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return True
    observed_metrics = row.get("metrics")
    metric_shape_invalid = (
        not isinstance(observed_metrics, dict)
        or set(observed_metrics) != set(recomputed_metrics)
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in observed_metrics.values()
        )
        or any(observed_metrics[key] != recomputed_metrics[key] for key in recomputed_metrics)
    )
    return bool(
        output_blocks
        or packet.get("task_class") != row["task_class"]
        or packet.get("packet_digest") != row["packet_digest"]
        or packet.get("evaluation_corpus_digest") != row["evaluation_corpus_digest"]
        or packet.get("evaluation_schema_digest") != row["evaluation_schema_digest"]
        or packet.get("public_semantic_contract_ref") != row["public_semantic_contract_ref"]
        or packet.get("public_semantic_contract_digest") != row["public_semantic_contract_digest"]
        or capability.digest(row["output"]) != row["output_digest"]
        or metric_shape_invalid
    )


def skill_surface_blocks(skill: str, canonical: str | None, plugin: str | None) -> list[str]:
    if canonical is None or plugin is None:
        return ["BLOCKED_FOR_MK733J_N_SKILL_CANONICAL_OR_PLUGIN_MISSING"]
    if canonical != plugin:
        return ["BLOCKED_FOR_MK733J_N_SKILL_CANONICAL_PLUGIN_DRIFT"]
    if "MK733J-N Model-Neutral Decision OS" not in canonical:
        return ["BLOCKED_FOR_MK733J_N_SKILL_MARKER_MISSING"]
    missing = [token for token in SKILL_REQUIREMENTS[skill] if token not in canonical]
    return ["BLOCKED_FOR_MK733J_N_SKILL_OWNER_WORKFLOW_MISSING"] if missing else []


def skill_surface_negative_controls() -> bool:
    for skill in REQUIRED_SKILLS:
        text = (REPO / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        marker_only = "## MK733J-N Model-Neutral Decision OS\nmarker only\n"
        if not skill_surface_blocks(skill, marker_only, marker_only): return False
        if not skill_surface_blocks(skill, text, None): return False
        if not skill_surface_blocks(skill, None, text): return False
        required_command = next((x for x in SKILL_REQUIREMENTS[skill] if ".py" in x), None)
        if required_command and not skill_surface_blocks(skill, text.replace(required_command, "command-removed"), text.replace(required_command, "command-removed")):
            return False
    return True


def agent_surface_blocks(path: Path, agent: dict[str, Any]) -> list[str]:
    """Validate supported custom-agent config plus exact role/return ownership."""
    contract = AGENT_CONTRACTS.get(path.name)
    if contract is None:
        return [f"BLOCKED_FOR_MK733J_N_AGENT_SCHEMA:{path.name}"]
    blocks: list[str] = []
    required = {"name", "description", "developer_instructions", "model", "model_reasoning_effort", "sandbox_mode", "skills"}
    allowed = required
    if not required <= set(agent) or set(agent) - allowed or not isinstance(agent.get("description"), str) or not agent.get("description"):
        blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_SCHEMA:{path.name}")
    if any(agent.get(key) != contract[value] for key, value in (("name", "name"), ("model", "model"), ("model_reasoning_effort", "effort"), ("sandbox_mode", "sandbox"))):
        blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_ROLE_BOUNDARY:{path.name}")
    skill_table = agent.get("skills")
    rows = skill_table.get("config") if isinstance(skill_table, dict) else None
    expected_paths = contract["skills"]
    actual_paths: list[str] = []
    if not isinstance(rows, list) or not rows:
        blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_SKILL_CONFIG:{path.name}")
    else:
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "enabled"} or row.get("enabled") is not True or not isinstance(row.get("path"), str):
                blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_SKILL_CONFIG:{path.name}")
                continue
            skill_ref = row["path"]
            actual_paths.append(skill_ref)
            resolved = (path.parent / skill_ref).resolve()
            if not skill_ref.endswith("/SKILL.md") or not resolved.is_file() or resolved.name != "SKILL.md" or resolved.parent.parent != (REPO / "skills").resolve():
                blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_SKILL_PATH:{path.name}")
        if actual_paths != expected_paths or len(actual_paths) != len(set(actual_paths)):
            blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_SKILL_CONFIG:{path.name}")
    instructions = agent.get("developer_instructions")
    if not isinstance(instructions, str) or any(token not in instructions for token in contract["instruction_tokens"]):
        blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_RETURN_READBACK:{path.name}")
    if isinstance(instructions, str):
        for field, expected in contract["identity"].items():
            observed = re.findall(rf"(?<![A-Za-z0-9_]){re.escape(field)}=([A-Za-z0-9_.-]+)", instructions)
            if observed != [expected]:
                blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_IDENTITY_ALIAS:{path.name}")
    if path.name == "sol-independent-reviewer.toml" and isinstance(instructions, str):
        lowered = instructions.lower()
        if any(phrase in lowered for phrase in ("you may patch", "apply fixes", "edit the implementation branch")):
            blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_REVIEWER_MUTATION:{path.name}")
    return sorted(set(blocks))


def agent_surface_negative_controls() -> bool:
    """Exercise actual parsed configs; caller labels cannot satisfy role ownership."""
    for filename, contract in AGENT_CONTRACTS.items():
        path = REPO / ".codex/agents" / filename
        try:
            agent = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return False
        no_skills = deepcopy(agent); no_skills.pop("skills", None)
        disabled = deepcopy(agent); disabled["skills"]["config"][0]["enabled"] = False
        directory_only = deepcopy(agent); directory_only["skills"]["config"][0]["path"] = contract["skills"][0].removesuffix("/SKILL.md")
        missing_path = deepcopy(agent); missing_path["skills"]["config"][0]["path"] = "../../skills/not-a-real-role-skill/SKILL.md"
        wrong_role = deepcopy(agent); wrong_role["skills"]["config"][0]["path"] = "../../skills/human-ux-route-contract/SKILL.md"
        alias_model = deepcopy(agent); alias_model["model"] = contract["model"] + "-alias"
        alias_profile = deepcopy(agent); alias_profile["developer_instructions"] = alias_profile["developer_instructions"].replace(f"profile_id={contract['identity']['profile_id']}", f"profile_id={contract['identity']['profile_id']}-alias")
        substring_policy = deepcopy(agent); substring_policy["developer_instructions"] = substring_policy["developer_instructions"].replace("aliases and substring matches do not qualify", "substring aliases qualify")
        missing_return = deepcopy(agent); missing_return["developer_instructions"] = missing_return["developer_instructions"].replace("readback_required=true", "readback omitted")
        mutations = [no_skills, disabled, directory_only, missing_path, wrong_role, alias_model, alias_profile, substring_policy, missing_return]
        if filename == "sol-independent-reviewer.toml":
            writable = deepcopy(agent); writable["sandbox_mode"] = "workspace-write"
            patching = deepcopy(agent); patching["developer_instructions"] += "\nYou may patch and apply fixes."
            downgraded = deepcopy(agent); downgraded["model_reasoning_effort"] = "xhigh"
            mutations.extend([writable, patching, downgraded])
        if any(not agent_surface_blocks(path, mutated) for mutated in mutations):
            return False
    return True


def outcome_aggregate_state(aggregated: Any) -> str | None:
    if not isinstance(aggregated, dict):
        return None
    provenance = aggregated.get("outcomes_provenance")
    proposals = aggregated.get("lifecycle_proposals")
    measurement_status = aggregated.get("metric_target_matrix", {}).get("measurement_status")
    if (
        aggregated.get("status") == "NO_OBSERVABLE_OUTCOMES_NOT_MEASURED"
        and aggregated.get("record_count") == 0
        and aggregated.get("promotion_eligible_record_count") == 0
        and proposals == []
        and provenance == []
        and measurement_status == "not_measured_pending_outcomes"
    ):
        return "empty"
    count = aggregated.get("record_count")
    if (
        aggregated.get("status") != "AGGREGATED_SUPPORT_DATA_ONLY"
        or not isinstance(count, int)
        or count < 10
        or not isinstance(provenance, list)
        or len(provenance) != count
        or aggregated.get("promotion_eligible_record_count") != 0
        or measurement_status != "observable_support_data_only"
        or not isinstance(proposals, list)
        or not all(isinstance(row, dict) and row.get("automatic_mutation") is False for row in proposals)
        or "no_observed_effective_prevention" not in aggregated.get("non_claims", [])
    ):
        return None
    for key in ("run_id", "evidence_ref", "evidence_digest"):
        values = [row.get(key) for row in provenance if isinstance(row, dict)]
        if len(values) != count or any(not isinstance(value, str) or not value for value in values) or len(set(values)) != count:
            return None
    return "measured"


def outcome_aggregate_state_self_test() -> bool:
    empty = {
        "status": "NO_OBSERVABLE_OUTCOMES_NOT_MEASURED",
        "record_count": 0,
        "promotion_eligible_record_count": 0,
        "lifecycle_proposals": [],
        "outcomes_provenance": [],
        "metric_target_matrix": {"measurement_status": "not_measured_pending_outcomes"},
    }
    one_row = {
        "status": "AGGREGATED_SUPPORT_DATA_ONLY",
        "record_count": 1,
        "promotion_eligible_record_count": 0,
        "lifecycle_proposals": [],
        "outcomes_provenance": [{"run_id": "one", "evidence_ref": "one.json", "evidence_digest": "a" * 64}],
        "metric_target_matrix": {"measurement_status": "observable_support_data_only"},
        "non_claims": ["no_observed_effective_prevention"],
    }
    return outcome_aggregate_state(empty) == "empty" and outcome_aggregate_state(one_row) is None


def semantic_blocks(doc: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    if doc.get("mk_id") != "MK733J-N" or doc.get("support_work_progress_credit") != 0 or doc.get("ui_or_remote_ops_mutation") is not False:
        blocks.append("BLOCKED_FOR_MK733J_N_SCOPE_OR_RECORD")
    if doc.get("hidden_chain_of_thought_collection") is not False:
        blocks.append("BLOCKED_FOR_MK733J_N_HIDDEN_REASONING")
    activation_authority=doc.get("activation_authority",{})
    authority_ref=activation_authority.get("trusted_registry_ref")
    authority_path=(REPO/authority_ref).resolve() if isinstance(authority_ref,str) else Path("/")
    try:
        authority_registry=load(authority_path)
        authority_rows=authority_registry.get("trusted_authorities")
        authority_row_fields={"authority_ref","activation_envelope_ref","authority_evidence_ref","authority_evidence_digest","issuer","issuer_class","source_class","scope"}
        authority_valid=(
            REPO in authority_path.parents
            and hashlib.sha256(authority_path.read_bytes()).hexdigest()==activation_authority.get("trusted_registry_digest")
            and activation_authority.get("normal_activation_requires_admitted_authority") is True
            and authority_registry.get("record_type")=="mk733j_n_trusted_activation_authority_registry"
            and authority_registry.get("registry_version")=="mk733j-trusted-activation-authorities-v1"
            and isinstance(authority_rows,list)
            and activation_authority.get("current_admitted_authority_count")==len(authority_rows)
            and all(isinstance(row,dict) and set(row)==authority_row_fields for row in authority_rows)
        )
    except (OSError,TypeError,ValueError,json.JSONDecodeError):
        authority_valid=False
    if not authority_valid:
        blocks.append("BLOCKED_FOR_MK733J_N_ACTIVATION_AUTHORITY_REGISTRY")
    profiles = {p.get("profile_id"): p for p in doc.get("model_profiles", []) if isinstance(p, dict)}
    required_profiles = {"sol_ultra_architect_cmd", "terra_high_implementer", "terra_readonly_explorer", "sol_independent_reviewer", "local_qualified_worker"}
    profile_fields = {"runtime_model_identity_pattern", "reasoning_effort", "risk_ceiling", "qualification_score", "qualification_digest", "qualified_at", "expires_at", "escalation_triggers", "context_compiler_version", "workpack_digest", "binding_record_digest", "fallback_profile", "non_claims"}
    if set(profiles) != required_profiles or any(not profile_fields <= set(p) or p.get("risk_ceiling") not in p.get("risk_classes", []) for p in profiles.values()):
        blocks.append("BLOCKED_FOR_MK733J_N_QUALIFICATION_PROFILES")
    final_reviewer = profiles.get("sol_independent_reviewer", {})
    if final_reviewer.get("runtime_model_identity_pattern") != "gpt-5.6-sol" or final_reviewer.get("reasoning_effort") != "ultra" or final_reviewer.get("mutation_permissions") != "read_only":
        blocks.append("BLOCKED_FOR_MK733J_N_SOL_REVIEWER_PROFILE")
    bundle_path = REPO / doc.get("capability_bundle_registry_ref", "")
    if not bundle_path.exists():
        blocks.append("BLOCKED_FOR_MK733J_N_TASK_CLASS_CAPABILITY_BUNDLES")
    else:
        bundles = load(bundle_path)
        expected = {"sol_ultra_architect_cmd":{"ambiguous_design":["decision_judgment"]},"terra_high_implementer":{"bounded_implementation":["decision_judgment","bounded_implementation"]},"sol_independent_reviewer":{"independent_audit":["decision_judgment","independent_audit"]},"terra_readonly_explorer":{"read_only_exploration":["read_only_exploration"]},"local_qualified_worker":{"deterministic_support":["deterministic_support"]}}
        allowed_bundle_states = {"not_measured", "empirically_qualified_current", "expired", "revoked"}
        expected_public_cases = {
            "bounded_implementation": {"bi-14a7", "bi-2c91", "bi-38d4", "bi-45e8", "bi-52b6", "bi-61f3", "bi-70c5", "bi-8a42", "bi-93d1", "bi-a5e7", "bi-b824"},
            "independent_audit": {"ia-31c8", "ia-74e2"},
            "read_only_exploration": {"rx-2a91", "rx-8d43"},
            "deterministic_support": {"ds-19b4", "ds-6f82"},
        }
        profile_results = bundles.get("profile_results", {})
        result_fields = {"profile_id","profile_digest","task_class","bundle_id","bundle_version","bundle_digest","result_ref","result_digest","identity_readback_ref","sealed_holdout_ref","runtime_model_identity","qualified_at","expires_at","qualification_state","qualification_digest"}
        results_valid = isinstance(profile_results, dict) and all(isinstance(row,dict) and result_fields <= set(row) and row.get("qualification_state") in {"empirically_qualified_current","expired","revoked"} for row in profile_results.values())
        cases_valid = all(set(bundles.get("bundles", {}).get(bundle_id, {}).get("public_cases", [])) == cases for bundle_id, cases in expected_public_cases.items())
        if bundles.get("profile_bundle_requirements") != expected or any(row.get("qualification_status") not in allowed_bundle_states for row in bundles.get("bundles", {}).values()) or not results_valid or not cases_valid:
            blocks.append("BLOCKED_FOR_MK733J_N_TASK_CLASS_CAPABILITY_BUNDLES")
    if profiles.get("local_qualified_worker", {}).get("mutation_permissions") != "qualified_task_class_only":
        blocks.append("BLOCKED_FOR_MK733J_N_CHEAP_UNQUALIFIED_ROUTE")
    local_profile = profiles.get("local_qualified_worker", {})
    local_contract = doc.get("local_model_identity_contract", {})
    local_aliases = bundles.get("profile_model_identity_aliases", {}).get("local_qualified_worker", []) if bundle_path.exists() else []
    generic_local_labels = {"local_qualified_worker", "local-qualified-worker"}
    if (
        local_contract.get("profile_id") != "local_qualified_worker"
        or local_contract.get("provider") != "ollama"
        or local_contract.get("role_label_is_model_identity") is not False
        or local_contract.get("runtime_model_identity") != local_profile.get("runtime_model_identity_pattern")
        or local_contract.get("reasoning_effort_observation") != local_profile.get("reasoning_effort")
        or local_contract.get("reasoning_effort_control") != "ollama_run_--think_high"
        or not isinstance(local_aliases, list)
        or local_aliases != [local_contract.get("runtime_model_identity")]
        or any(alias in generic_local_labels for alias in local_aliases)
        or local_profile.get("runtime_model_identity_pattern") in generic_local_labels
    ):
        blocks.append("BLOCKED_FOR_MK733J_N_LOCAL_MODEL_IDENTITY_GENERIC")
    public_claim = doc.get("public_evaluation_claim_boundary", {})
    public_observations = doc.get("public_evaluation_observations", [])
    if public_claim != {
        "public_pass_is_profile_qualification": False,
        "route_unlock_allowed": False,
        "import_requires_separate_sol_owned_sealed_holdout": True,
        "public_metrics_are_model_parity": False,
    }:
        blocks.append("BLOCKED_FOR_MK733J_N_PUBLIC_SCORE_AS_QUALIFICATION")
    if not isinstance(public_observations, list) or not public_observations or any(
        public_observation_invalid(row, local_profile) for row in public_observations
    ):
        blocks.append("BLOCKED_FOR_MK733J_N_PUBLIC_OBSERVATION_NOT_RECOMPUTABLE")
    firing = doc.get("mandatory_firing", {})
    if firing.get("hooks_only_sufficient") is not False or not firing.get("receipt_launcher") or not firing.get("ci_binding"):
        blocks.append("BLOCKED_FOR_MK733J_N_HOOKS_ONLY_ENFORCEMENT")
    if firing.get("fresh_session_activation") != "not_verified_non_claim":
        blocks.append("BLOCKED_FOR_MK733J_N_RUNTIME_FIRING_OVERCLAIM")
    handshake = doc.get("dispatch_handshake", {})
    required_handshake = {"work_id", "goal_ref", "workpack_digest", "binding_record_digest", "model_identity_state", "runtime_identity_ref", "thread_run_id", "profile_id", "profile_digest", "qualification_result_ref", "qualification_results", "qualification_expires_at", "context_digest", "preflight_ref", "preflight_digest", "preflight_scope_digest", "preflight_contract_version", "task_class", "risk_class", "allowed_tools", "allowed_path_prefixes", "allowed_command_classes", "forbidden_operation_classes", "operation_manifest", "budget", "return_schema", "readback_required", "auditor_independent_from_implementer"}
    if not required_handshake <= set(handshake.get("required_fields", [])) or handshake.get("same_worker_final_auditor_allowed") is not False:
        blocks.append("BLOCKED_FOR_MK733J_N_DISPATCH_HANDSHAKE")
    dna = doc.get("dna_outcome_fitness", {})
    outcome_tiers = dna.get("execution_tiers", {})
    if (
        dna.get("citation_count_is_telemetry_only") is not True
        or dna.get("supervised_measurement_can_promote") is not False
        or dna.get("supervised_measurement_blocks_normal_work") is not False
        or outcome_tiers.get("normal_local_bounded_supervised") != "single_observation_operational_telemetry_only_no_identity_qualification_preflight_or_receipt_chain"
        or outcome_tiers.get("autonomous_profile_qualified") != "full_canonical_chain_required_and_only_tier_eligible_for_gene_promotion"
        or dna.get("measurement_status") != "not_measured_blocks_only_observed_effective_or_enforce_promotion_claims"
        or not {"duplicate_run", "fabricated_outcome", "self_scored_success", "fixture_only_observed_effective"} <= set(dna.get("reject", []))
    ):
        blocks.append("BLOCKED_FOR_MK733J_N_OUTCOME_FITNESS")
    context = doc.get("context_compiler", {})
    if context.get("required_policy_nonclaim_recall") != 1.0 or context.get("irrelevant_policy_refs_max") != 0 or context.get("baseline_ratio_max") != 0.5:
        blocks.append("BLOCKED_FOR_MK733J_N_CONTEXT_THRESHOLDS")
    matrix_path = REPO / "research/mk675/fable5_decision_os/mk733j_n_measurement_matrix.json"
    if not matrix_path.exists():
        blocks.append("BLOCKED_FOR_MK733J_N_MEASUREMENT_MATRIX")
    else:
        matrix = load(matrix_path)
        required_cases = {"shape_complete_nonsense", "copied_gold_after_context_change", "duplicate_or_irrelevant_options", "existing_but_unrelated_fixture", "self_declared_model_identity", "local_role_label_as_runtime_model_identity", "deterministic_support_hidden_check_id_unanswerable", "stale_qualification", "cheaper_unqualified_routing", "same_worker_and_final_auditor", "always_escalate_to_sol", "hooks_only_enforcement", "missing_stale_or_wrong_workpack_receipt", "raw_prompt_or_transcript_in_receipt", "citation_or_gene_count_as_success", "fixture_or_validator_as_observed_effective", "docs_canonical_plugin_or_marker_only_skill_propagation"}
        if not required_cases <= set(matrix.get("mandatory_adversarial_cases", [])) or any(row.get("measurement_status") != "not_measured" for row in matrix.get("thresholds", {}).values()):
            blocks.append("BLOCKED_FOR_MK733J_N_MEASUREMENT_MATRIX")
    claims = set(doc.get("non_claims", []))
    required_claims = {"no_blanket_model_parity", "no_permanent_runtime_firing", "no_observed_effective_prevention", "no_runtime_readiness", "no_product_final_user_acceptance", "no_release_readiness", "no_a3_plus_autonomy"}
    if not required_claims <= claims:
        blocks.append("BLOCKED_FOR_MK733J_N_NON_CLAIMS")
    skill_contracts = doc.get("skill_contracts", {})
    required_contracts = {"grand-goal-native-goal-formulation":"delegated_goal_fields","goal-audit-checklist":"enforce_receipt_check","best-evaluate":"route_cost_escalation_option","agent-dispatch":"dispatch_envelope_readback","fable5-derived-advisory-synthesis":"observable_criteria","skill-health-check":"owner_trigger_command_receipt_rollback","skill-quality-review":"owner_specific_review"}
    if any(skill_contracts.get(skill, {}).get(field) is not True for skill, field in required_contracts.items()):
        blocks.append("BLOCKED_FOR_MK733J_N_SKILL_SUBSTANTIVE_TRIGGER")
    return sorted(set(blocks))


def live_blocks(doc: dict[str, Any]) -> list[str]:
    blocks = semantic_blocks(doc)
    blocks.extend(subagent_register_blocks(doc))
    for rel in REQUIRED_FILES:
        if not (REPO / rel).exists():
            blocks.append(f"BLOCKED_FOR_MK733J_N_REQUIRED_FILE:{rel}")
    try:
        measurement = load(MEASUREMENT_RECORD)
        blocks.extend(measurement_binding_blocks(measurement))
        tampered = deepcopy(measurement)
        tampered["attempts"][-1]["output_digest"] = "0" * 64
        if not measurement_binding_blocks(tampered):
            blocks.append("BLOCKED_FOR_MK733N_PUBLIC_MEASUREMENT_NEGATIVE_CONTROL")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, IndexError):
        blocks.append("BLOCKED_FOR_MK733N_PUBLIC_MEASUREMENT_BINDING")
    provider_registry = REPO / "research/mk675/fable5_decision_os/qualification-authorities/provider-attestations.json"
    try:
        provider = load(provider_registry)
        provider_keys = {"record_type", "registry_version", "trusted_attestations", "non_claims"}
        rows = provider.get("trusted_attestations") if isinstance(provider, dict) else None
        if (
            not isinstance(provider, dict) or set(provider) != provider_keys
            or provider.get("record_type") != "mk733j_provider_attestation_trust_registry"
            or provider.get("registry_version") != "mk733j-provider-attestation-v1"
            or not isinstance(rows, dict)
            or any(not isinstance(row, dict) or set(row) != {"attestation_digest", "issuer_class", "capability"} for row in rows.values())
            or not isinstance(provider.get("non_claims"), list) or not all(isinstance(item, str) and item for item in provider["non_claims"])
        ):
            blocks.append("BLOCKED_FOR_MK733J_N_PROVIDER_ATTESTATION_REGISTRY")
    except (OSError, json.JSONDecodeError, AttributeError):
        blocks.append("BLOCKED_FOR_MK733J_N_PROVIDER_ATTESTATION_REGISTRY")
    qualification_tool = REPO / "scripts/ops/mk733j_qualification.py"
    capability_tool = REPO / "scripts/ops/mk733j_capability_bundles.py"
    qualification_corpus = REPO / "research/mk675/fable5_decision_os/mk733j_n_public_observable_qualification_corpus.json"
    if not qualification_tool.exists() or not qualification_corpus.exists():
        blocks.append("BLOCKED_FOR_MK733J_N_PUBLIC_QUALIFICATION_HARNESS")
    else:
        public = load(qualification_corpus)
        required_cases = {
            "reconcile-conflicting-authority",
            "fake-pass-claim",
            "costed-route-selection",
            "shape-complete-nonsense",
            "copied-gold-context-changed",
            "duplicate-irrelevant-options",
            "unrelated-existing-fixture",
            "always-escalate",
            "real-target-disconnected",
            "proposal-operational-last-mile-missing",
            "comparison-order-self-evaluation-bias",
            "countercritic-overapplied-to-supervised-repair",
        }
        if not required_cases <= {case.get("case_id") for case in public.get("cases", [])} or public.get("evaluation_scope") != "observable_response_outputs_only":
            blocks.append("BLOCKED_FOR_MK733J_N_PUBLIC_QUALIFICATION_HARNESS")
        packet_proc = subprocess.run([sys.executable, str(qualification_tool), "render-evaluation-packet"], text=True, capture_output=True)
        try:
            packet=json.loads(packet_proc.stdout)
            compact_schema=packet.get("compact_response_schema",{})
            compact_outputs=compact_schema.get("properties",{}).get("outputs",{}) if isinstance(compact_schema,dict) else {}
            compact_row=compact_outputs.get("items",{}) if isinstance(compact_outputs,dict) else {}
            compact_required=set(compact_row.get("required",[])) if isinstance(compact_row,dict) else set()
            compact_properties=compact_row.get("properties",{}) if isinstance(compact_row,dict) else {}
            required_compact_fields={
                "disposition","selected_profile_index","contradiction_source_indices",
                "fake_pass_detected","implementation_target_index","negative_test_index",
                "warning_stop_condition_index","evidence_classification_index",
                "ux_delta","cost_units","stop_budget",
                "incident_choice_indices","file_choice_indices","next_check_index",
                "next_stop_condition_index",
            }
            header_constants={"prompt_context_digest":packet.get("prompt_context_digest"),"context_variant":packet.get("context_variant"),"run_family":packet.get("run_family"),"issuance_id":packet.get("issuance_id")}
            unique_lists=("contradiction_source_indices","incident_choice_indices","file_choice_indices")
            if packet_proc.returncode or "expected_disposition" in json.dumps(packet) or "gold_criteria" in json.dumps(packet) or packet.get("corpus_digest") != __import__("hashlib").sha256(json.dumps(public,sort_keys=True,separators=(",",":")).encode()).hexdigest() or packet.get("preferred_response_format") != "mk733j-compact-ordered-v4" or compact_schema.get("properties",{}).get("output_format",{}).get("const") != "mk733j-compact-ordered-v4" or any(compact_schema.get("properties",{}).get(key,{})!={"const":value} for key,value in header_constants.items()) or compact_outputs.get("minItems") != len(public.get("cases",[])) or compact_outputs.get("maxItems") != len(public.get("cases",[])) or compact_required != required_compact_fields or "case_id" in compact_row.get("properties",{}) or any(compact_properties.get(key,{}).get("uniqueItems") is not True for key in unique_lists):
                blocks.append("BLOCKED_FOR_MK733J_N_GOLD_FREE_EVALUATION_PACKET")
        except json.JSONDecodeError:
            blocks.append("BLOCKED_FOR_MK733J_N_GOLD_FREE_EVALUATION_PACKET")
        grader_controls = subprocess.run([sys.executable, str(qualification_tool), "self-test"], text=True, capture_output=True)
        try:
            if grader_controls.returncode or json.loads(grader_controls.stdout).get("status") != "PASS_QUALIFICATION_GRADER_NEGATIVE_CONTROLS":
                blocks.append("BLOCKED_FOR_MK733J_N_QUALIFICATION_GRADER_NEGATIVE_CONTROLS")
        except json.JSONDecodeError:
            blocks.append("BLOCKED_FOR_MK733J_N_QUALIFICATION_GRADER_NEGATIVE_CONTROLS")
    if not capability_tool.exists():
        blocks.append("BLOCKED_FOR_MK733J_N_TASK_CLASS_CAPABILITY_BUNDLES")
    else:
        capability = subprocess.run([sys.executable, str(capability_tool), "self-test"], text=True, capture_output=True)
        try:
            if capability.returncode or json.loads(capability.stdout).get("status") != "PASS_TASK_CLASS_BUNDLE_NEGATIVE_CONTROLS": blocks.append("BLOCKED_FOR_MK733J_N_TASK_CLASS_CAPABILITY_BUNDLES")
        except json.JSONDecodeError:
            blocks.append("BLOCKED_FOR_MK733J_N_TASK_CLASS_CAPABILITY_BUNDLES")
    hooks_path = REPO / ".codex/hooks.json"
    if hooks_path.exists():
        try:
            hooks = load(hooks_path).get("hooks", {})
            required_events = {"SessionStart", "UserPromptSubmit", "PreToolUse", "SubagentStart", "Stop"}
            if not required_events <= set(hooks) or any(not isinstance(rows, list) or not rows or not isinstance(rows[0].get("hooks"), list) or not rows[0]["hooks"] or any(handler.get("type") != "command" or not handler.get("command") or "timeout" not in handler or "statusMessage" not in handler for handler in rows[0]["hooks"]) for rows in hooks.values()):
                blocks.append("BLOCKED_FOR_MK733J_N_HOOK_SCHEMA")
            if not any(row.get("matcher") == "Bash|apply_patch|Edit|Write" for row in hooks.get("PreToolUse", [])):
                blocks.append("BLOCKED_FOR_MK733J_N_HOOK_SCHEMA")
            hook_commands = [
                handler.get("command")
                for rows in hooks.values()
                if isinstance(rows, list)
                for row in rows
                if isinstance(row, dict)
                for handler in row.get("hooks", [])
                if isinstance(handler, dict)
            ]
            if (
                len(hook_commands) != 5
                or any(
                    not isinstance(command, str)
                    or "$(/usr/bin/git rev-parse --show-toplevel)/.codex/hooks/" not in command
                    for command in hook_commands
                )
            ):
                blocks.append("BLOCKED_FOR_MK734_GIT_ROOT_HOOK_LAUNCHER")
        except (json.JSONDecodeError, AttributeError):
            blocks.append("BLOCKED_FOR_MK733J_N_HOOK_SCHEMA")
    plugin_manifest = REPO / "plugins/orch-next-codex-harness/.codex-plugin/plugin.json"
    plugin_hooks = REPO / "plugins/orch-next-codex-harness/hooks/hooks.json"
    plugin_hook_code = REPO / "plugins/orch-next-codex-harness/hooks/decision_os_shadow.py"
    plugin_manifest_version = None
    try:
        manifest = load(plugin_manifest)
        plugin_manifest_version = manifest.get("version")
        hooks = load(plugin_hooks).get("hooks", {})
        expected_plugin_commands = {
            "SessionStart": '/usr/bin/python3 "$PLUGIN_ROOT/hooks/decision_os_shadow.py" session-start',
            "PreToolUse": '/usr/bin/python3 "$PLUGIN_ROOT/hooks/decision_os_shadow.py" pre-tool-use',
            "Stop": '/usr/bin/python3 "$PLUGIN_ROOT/hooks/decision_os_shadow.py" stop',
        }
        handlers_valid = isinstance(hooks, dict) and set(hooks) == set(expected_plugin_commands)
        if handlers_valid:
            for event_name, expected_command in expected_plugin_commands.items():
                rows = hooks.get(event_name)
                if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                    handlers_valid = False
                    break
                handlers = rows[0].get("hooks")
                if not isinstance(handlers, list) or len(handlers) != 1:
                    handlers_valid = False
                    break
                handler = handlers[0]
                if (
                    not isinstance(handler, dict)
                    or handler.get("type") != "command"
                    or handler.get("command") != expected_command
                ):
                    handlers_valid = False
                    break
        default_prompts = manifest.get("interface", {}).get("defaultPrompt", [])
        if (manifest.get("version") != "0.1.2" or manifest.get("hooks") != "./hooks/hooks.json" or not isinstance(default_prompts, list) or not 1 <= len(default_prompts) <= 3 or not all(isinstance(prompt, str) and prompt.strip() for prompt in default_prompts) or not handlers_valid or not plugin_hook_code.is_file()):
            blocks.append("BLOCKED_FOR_MK734B_PLUGIN_HOOK_DISCOVERY")
        source = plugin_hook_code.read_text(encoding="utf-8")
        if "PLUGIN_DATA" not in source or "authorized-repositories.json" not in source or "CANONICAL_REMOTE" not in source or "importlib" in source:
            blocks.append("BLOCKED_FOR_MK734B_PLUGIN_TRUST_BOUNDARY")
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        blocks.append("BLOCKED_FOR_MK734B_PLUGIN_HOOK_DISCOVERY")
    agent_paths = {path.name: path for path in (REPO / ".codex/agents").glob("*.toml")}
    if set(agent_paths) != set(AGENT_CONTRACTS):
        blocks.append("BLOCKED_FOR_MK733J_N_AGENT_SCHEMA:agent-set")
    for filename in AGENT_CONTRACTS:
        path = agent_paths.get(filename)
        if path is None:
            blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_SCHEMA:{filename}")
            continue
        try:
            agent = tomllib.loads(path.read_text(encoding="utf-8"))
            blocks.extend(agent_surface_blocks(path, agent))
        except tomllib.TOMLDecodeError:
            blocks.append(f"BLOCKED_FOR_MK733J_N_AGENT_SCHEMA:{path.name}")
    if not agent_surface_negative_controls():
        blocks.append("BLOCKED_FOR_MK733J_N_AGENT_NEGATIVE_CONTROLS")
    pretool = REPO / ".codex/hooks/mk733j-pretooluse.py"
    stop_hook = REPO / ".codex/hooks/mk733j-stop.py"
    if pretool.exists() and stop_hook.exists():
        proc = subprocess.run([sys.executable, str(pretool)], input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "true"}}), text=True, capture_output=True)
        try:
            denial = json.loads(proc.stdout)
            expected = denial["hookSpecificOutput"]
            if proc.returncode or expected.get("hookEventName") != "PreToolUse" or not expected.get("additionalContext"):
                blocks.append("BLOCKED_FOR_MK733J_N_HOOK_SHADOW_CONTRACT")
        except (json.JSONDecodeError, KeyError, TypeError):
            blocks.append("BLOCKED_FOR_MK733J_N_HOOK_SHADOW_CONTRACT")
        with tempfile.TemporaryDirectory() as corrupt_directory:
            corrupt = Path(corrupt_directory) / "activation-state.json"; corrupt.write_text("{bad")
            corrupt_env = {**__import__("os").environ, "MK733J_TEST_ISOLATED": "true", "MK733J_STATE_DIR": corrupt_directory}
            enforced = subprocess.run([sys.executable, str(pretool)], input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "true"}}), text=True, capture_output=True, env=corrupt_env)
            corrupt_stop = subprocess.run([sys.executable, str(stop_hook)], input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}), text=True, capture_output=True, env=corrupt_env)
            corrupt.write_text(json.dumps({"mode":"enforce","enforcement_active":True,"receipt_path":str(Path(corrupt_directory)/"current-receipt.json"),"state_digest":"tampered"}))
            corrupt_digest_stop = subprocess.run([sys.executable, str(stop_hook)], input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}), text=True, capture_output=True, env=corrupt_env)
        try:
            denial = json.loads(enforced.stdout)["hookSpecificOutput"]
            stop_recovery = json.loads(corrupt_stop.stdout)
            digest_recovery = json.loads(corrupt_digest_stop.stdout)
            if enforced.returncode or denial.get("permissionDecision") != "deny" or not denial.get("permissionDecisionReason") or corrupt_stop.returncode or stop_recovery.get("decision") == "block" or "recovery-only" not in stop_recovery.get("systemMessage", "") or "fail-closed" not in stop_recovery.get("systemMessage", "") or "shadow mode" in stop_recovery.get("systemMessage", "") or corrupt_digest_stop.returncode or digest_recovery.get("decision") == "block" or "recovery-only" not in digest_recovery.get("systemMessage", "") or "shadow mode" in digest_recovery.get("systemMessage", ""):
                blocks.append("BLOCKED_FOR_MK733J_N_HOOK_DENY_CONTRACT")
        except (json.JSONDecodeError, KeyError, TypeError):
            blocks.append("BLOCKED_FOR_MK733J_N_HOOK_DENY_CONTRACT")
        after = subprocess.run([sys.executable, str(pretool)], input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "true"}}), text=True, capture_output=True)
        try:
            if json.loads(after.stdout)["hookSpecificOutput"].get("permissionDecision") == "deny":
                blocks.append("BLOCKED_FOR_MK733J_N_PERMANENT_LOCKOUT")
        except (json.JSONDecodeError, KeyError, TypeError):
            blocks.append("BLOCKED_FOR_MK733J_N_PERMANENT_LOCKOUT")
    if not installed_skill_cache_health_self_test():
        blocks.append("BLOCKED_FOR_MK733J_N_INSTALLED_CACHE_HEALTH_CONTROL")
    hook_contract = REPO / "scripts/ops/mk733j_hook_contract_self_test.py"
    if not hook_contract.exists():
        blocks.append("BLOCKED_FOR_MK733J_N_ACTIVATION_E2E")
    else:
        hook_e2e = subprocess.run([sys.executable, str(hook_contract)], text=True, capture_output=True)
        try:
            hook_result = json.loads(hook_e2e.stdout)
            hook_controls=hook_result.get("controls",{})
            if hook_e2e.returncode or hook_result.get("status") != "PASS_RECEIPT_HOOK_E2E_NEGATIVE_CONTROLS" or hook_result.get("blocks") or hook_controls.get("ordered_composite_qualification_results") is not True or hook_controls.get("external_protected_authority_hook_path") is not True or hook_controls.get("git_root_resolved_hook_launchers") is not True:
                blocks.append("BLOCKED_FOR_MK733J_N_ACTIVATION_E2E")
        except json.JSONDecodeError:
            blocks.append("BLOCKED_FOR_MK733J_N_ACTIVATION_E2E")
    authority_self_test = subprocess.run(
        [sys.executable, str(REPO / "scripts/ops/mk733j_activation.py"), "authority-gate-self-test", "--json"],
        text=True, capture_output=True,
    )
    try:
        authority_result = json.loads(authority_self_test.stdout)
        authority_controls = authority_result.get("controls", {})
        required_target_controls = {
            "target_bound_wrong_target_rejected", "target_bound_wrong_revision_rejected",
            "target_bound_wrong_operation_rejected", "target_bound_operation_digest_tamper_rejected",
            "target_bound_broadened_classes_rejected", "target_bound_local_combined_exact_boundary",
        }
        if (
            authority_self_test.returncode
            or authority_result.get("status") != "PASS_AUTHORITY_GATE_RECEIPT_CONSUMPTION_CONTROLS"
            or authority_result.get("blocks")
            or not all(authority_controls.get(name) is True for name in required_target_controls)
        ):
            blocks.append("BLOCKED_FOR_MK733J_TARGET_BOUND_AUTHORITY_CONTROLS")
    except json.JSONDecodeError:
        blocks.append("BLOCKED_FOR_MK733J_TARGET_BOUND_AUTHORITY_CONTROLS")
    telemetry_tool = REPO / "scripts/ops/mk733j_shadow_telemetry.py"
    if not telemetry_tool.exists():
        blocks.append("BLOCKED_FOR_MK734_SHADOW_TELEMETRY_NONBLOCKING_CONTROL")
    else:
        telemetry = subprocess.run(
            [sys.executable, str(telemetry_tool), "self-test"],
            text=True,
            capture_output=True,
        )
        try:
            telemetry_result = json.loads(telemetry.stdout)
            checks = telemetry_result.get("checks", {})
            if (
                telemetry.returncode
                or telemetry_result.get("status") != "PASS_SHADOW_TELEMETRY_NONBLOCKING_CONTROLS"
                or not checks
                or not all(checks.values())
            ):
                blocks.append("BLOCKED_FOR_MK734_SHADOW_TELEMETRY_NONBLOCKING_CONTROL")
        except json.JSONDecodeError:
            blocks.append("BLOCKED_FOR_MK734_SHADOW_TELEMETRY_NONBLOCKING_CONTROL")
    continuity_tools = {
        "mk733j_session_continuity.py": (
            "PASS_SESSION_CONTINUITY_NONAUTHORITY_CONTROLS",
            "BLOCKED_FOR_MK734_SESSION_CONTINUITY_CONTROL",
        ),
        "mk733j_intent_lock.py": (
            "PASS_INTENT_LOCK_CONTINUITY_CONTROLS",
            "BLOCKED_FOR_MK734_INTENT_LOCK_CONTROL",
        ),
        "mk_worktree_status.py": (
            "PASS_WORKTREE_STATUS_READ_ONLY_CONTROLS",
            "BLOCKED_FOR_MK734_WORKTREE_STATUS_CONTROL",
        ),
    }
    for filename, (expected_status, blocker) in continuity_tools.items():
        tool = REPO / "scripts/ops" / filename
        if not tool.exists():
            blocks.append(blocker)
            continue
        control = subprocess.run(
            [sys.executable, str(tool), "self-test"],
            text=True,
            capture_output=True,
        )
        try:
            result = json.loads(control.stdout)
            checks = result.get("checks", {})
            if control.returncode or result.get("status") != expected_status or not checks or not all(checks.values()):
                blocks.append(blocker)
        except json.JSONDecodeError:
            blocks.append(blocker)
        if filename == "mk_worktree_status.py":
            help_result = subprocess.run(
                [sys.executable, str(tool), "write-local", "--help"],
                text=True,
                capture_output=True,
            )
            if help_result.returncode or "--output" in help_result.stdout:
                blocks.append(blocker)
    for skill in REQUIRED_SKILLS:
        canonical = REPO / "skills" / skill / "SKILL.md"
        plugin = REPO / "plugins/orch-next-codex-harness/skills" / skill / "SKILL.md"
        if skill_surface_blocks(skill, canonical.read_text(encoding="utf-8") if canonical.exists() else None, plugin.read_text(encoding="utf-8") if plugin.exists() else None):
            blocks.append(f"BLOCKED_FOR_MK733J_N_SKILL_DISTRIBUTION:{skill}")
    if not skill_surface_negative_controls():
        blocks.append("BLOCKED_FOR_MK733J_N_SKILL_NEGATIVE_CONTROLS")
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8") if (REPO / ".github/workflows/ci.yml").exists() else ""
    if "verify_mk733j_n_implementation.py" not in ci:
        blocks.append("BLOCKED_FOR_MK733J_N_CI_BINDING")
    proc = subprocess.run([sys.executable, str(TOOL), "evaluate", "--json"], text=True, capture_output=True, check=False)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        blocks.append("BLOCKED_FOR_MK733J_N_QUALIFICATION_EVALUATOR")
        result = {}
    if proc.returncode or result.get("status") != "PASS_ROUTER_CONTRACT_ONLY":
        blocks.append("BLOCKED_FOR_MK733J_N_ROUTER_CONTRACT")
    audit_identity=subprocess.run([sys.executable,str(TOOL),"final-audit-self-test","--json"],text=True,capture_output=True)
    try:
        audit_controls=json.loads(audit_identity.stdout)
        if audit_identity.returncode or audit_controls.get("status")!="PASS_FINAL_AUDIT_IDENTITY_NEGATIVE_CONTROLS" or audit_controls.get("blocks"):
            blocks.append("BLOCKED_FOR_MK733J_N_FINAL_AUDIT_IDENTITY_EXECUTION")
    except json.JSONDecodeError:
        blocks.append("BLOCKED_FOR_MK733J_N_FINAL_AUDIT_IDENTITY_EXECUTION")
    audit_receipt=subprocess.run([sys.executable,str(REPO/"scripts/ops/mk733j_final_audit_receipt_self_test.py"),"--json"],text=True,capture_output=True)
    try:
        audit_receipt_result=json.loads(audit_receipt.stdout)
        if audit_receipt.returncode or audit_receipt_result.get("status")!="PASS_FINAL_AUDIT_RECEIPT_E2E" or audit_receipt_result.get("blocks"):
            blocks.append("BLOCKED_FOR_MK733J_N_FINAL_AUDIT_RECEIPT_EXECUTION")
    except json.JSONDecodeError:
        blocks.append("BLOCKED_FOR_MK733J_N_FINAL_AUDIT_RECEIPT_EXECUTION")
    outcome = subprocess.run([sys.executable, str(REPO / "scripts/ops/mk733j_outcomes.py"), "aggregate", "--json"], text=True, capture_output=True)
    outcome_controls = subprocess.run([sys.executable, str(REPO / "scripts/ops/mk733j_outcomes.py"), "self-test", "--json"], text=True, capture_output=True)
    context = subprocess.run([sys.executable, str(REPO / "scripts/ops/mk733j_context_compiler.py"), "--request", str(REPO / "fixtures/mk675/fable5_decision_os/positive_mk733j_n_context_request.json"), "--json"], text=True, capture_output=True)
    context_controls = subprocess.run([sys.executable, str(REPO / "scripts/ops/mk733j_context_compiler.py"), "--self-test", "--json"], text=True, capture_output=True)
    try:
        aggregated = json.loads(outcome.stdout)
        outcome_negative = json.loads(outcome_controls.stdout)
        measurement = load(REPO / "research/mk675/fable5_decision_os/mk733j_n_measurement_matrix.json")
        source_metrics = measurement.get("outcome_source_derived_metrics", {})
        required_provenance = {"preflight_ref","route_request_ref","route_decision_ref","pre_work_receipt_before_ref","pre_work_receipt_after_ref","dispatch_ref","readback_ref","audit_ref","closeout_receipt_before_ref","closeout_receipt_after_ref","closeout_readback_ref"}
        required_outcome_negatives = {"outcome_envelope_without_canonical_source_artifacts","outcome_synthetic_test_only_source_as_real","outcome_preflight_deterministic_result_missing","outcome_route_result_not_recomputed_or_profile_unqualified","outcome_receipt_consumption_transition_invalid","outcome_target_thread_dispatch_or_readback_unverified","outcome_auditor_unqualified_or_same_thread","outcome_closeout_receipt_or_readback_unconsumed","outcome_fitness_metric_tamper_after_envelope","outcome_unknown_metric_promotion","outcome_supervised_claims_autonomous_chain_or_qualification"}
        outcome_provenance = source_metrics.get("outcomes_provenance", {})
        tier_chains = outcome_provenance.get("required_chain_by_execution_tier", {})
        supervised_controls = outcome_negative.get("supervised_scope_controls", {})
        aggregate_state = outcome_aggregate_state(aggregated)
        empty_aggregate_ok = aggregate_state == "empty"
        measured_aggregate_ok = aggregate_state == "measured"
        if not outcome_aggregate_state_self_test():
            blocks.append("BLOCKED_FOR_MK733J_N_OUTCOME_STATE_NEGATIVE_CONTROLS")
        calibration_record_ok = True
        if measured_aggregate_ok:
            activation_record = load(REPO / "research/mk675/fable5_decision_os/mk734_runtime_activation_measurement_loop.json")
            task_register = load(REPO / "research/mk675/registers/task_register.json")
            mk734_task = next((row for row in task_register.get("tasks", []) if isinstance(row, dict) and row.get("task_id") == "mk734-decision-os-runtime-activation-measurement-loop"), {})
            task_summary = mk734_task.get("value_calibration_summary", {})
            readback_observation = activation_record.get("installed_plugin_readback_observation", {})
            task_readback_summary = mk734_task.get("installed_plugin_readback_summary", {})
            calibration = activation_record.get("real_supervised_value_calibration", {})
            aggregate_metrics = aggregated.get("metrics", {})
            metric_pairs = {
                "cost_units": "cost_units",
                "time_to_first_valid_action_ms_mean": "time_to_first_valid_action_ms_mean",
                "closeout_elapsed_ms_mean": "closeout_elapsed_ms_mean",
                "correct_allow": "correct_allow",
                "correct_block": "correct_block",
                "false_blocks": "false_block",
                "missed_blocks": "missed_block",
                "wrong_lane": "wrong_lane",
                "unnecessary_escalations": "unnecessary_escalation",
                "manual_user_relay_count": "manual_user_relay_count",
                "user_corrections": "user_correction",
                "completed_within_manifest_budget": "completed_within_manifest_budget",
            }
            routes = {
                row.get("route"): row.get("decision")
                for row in activation_record.get("route_lifecycle_decisions", [])
                if isinstance(row, dict)
            }
            threshold_failed = (
                aggregate_metrics.get("missed_block", 0) > 0
                or aggregate_metrics.get("wrong_lane", 0) > 0
                or aggregate_metrics.get("completed_within_manifest_budget", 0) < aggregated.get("record_count", 0)
            )
            expected_task_summary = {
                "record_count": aggregated.get("record_count"),
                "false_blocks": aggregate_metrics.get("false_block"),
                "missed_blocks": aggregate_metrics.get("missed_block"),
                "wrong_lane": aggregate_metrics.get("wrong_lane"),
                "completed_within_manifest_budget": aggregate_metrics.get("completed_within_manifest_budget"),
                "manual_user_relay_count": aggregate_metrics.get("manual_user_relay_count"),
                "user_corrections": aggregate_metrics.get("user_correction"),
                "threshold_status": "failed_value_thresholds_route_repair_required" if threshold_failed else "value_thresholds_met",
            }
            expected_task_readback = {
                "installed_plugin_version": readback_observation.get("installed_plugin_version"),
                "source_main_sha": readback_observation.get("source_main_sha"),
                "fresh_tasks": readback_observation.get("fresh_tasks"),
                "session_start_events": readback_observation.get("session_start_events"),
                "pre_tool_use_events": readback_observation.get("pre_tool_use_events"),
                "stop_events": readback_observation.get("stop_events"),
                "handoff_readbacks": readback_observation.get("handoff_readbacks"),
                "distinct_readback_sessions": readback_observation.get("distinct_readback_sessions"),
                "distinct_source_sessions": readback_observation.get("distinct_source_sessions"),
                "manual_user_relay_count": readback_observation.get("manual_user_relay_count"),
                "permanent_or_hot_load_firing_claimed": readback_observation.get("permanent_or_hot_load_firing_claimed"),
                "observed_effective_prevention_claimed": readback_observation.get("observed_effective_prevention_claimed"),
            }
            installed_readback_ok = (
                activation_record.get("session_continuity_contract", {}).get("handoff_readback_verified") is True
                and activation_record.get("mk734b_durable_plugin_distribution", {}).get("handoff_readback_verified") is True
                and readback_observation.get("installed_plugin_version") == plugin_manifest_version
                and isinstance(readback_observation.get("authorized_repository_registry_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", readback_observation.get("authorized_repository_registry_sha256")) is not None
                and readback_observation.get("fresh_tasks", 0) >= 6
                and readback_observation.get("session_start_events", 0) >= 6
                and readback_observation.get("pre_tool_use_events", 0) >= 6
                and readback_observation.get("stop_events", 0) >= 6
                and readback_observation.get("handoff_readbacks", 0) >= 5
                and readback_observation.get("distinct_readback_sessions", 0) >= 5
                and readback_observation.get("distinct_source_sessions", 0) >= 5
                and readback_observation.get("all_readbacks_on_session_start") is True
                and readback_observation.get("manual_user_relay_count") == 0
                and readback_observation.get("raw_prompt_transcript_reasoning_or_secret_retained") is False
                and readback_observation.get("permanent_or_hot_load_firing_claimed") is False
                and readback_observation.get("observed_effective_prevention_claimed") is False
                and task_readback_summary == expected_task_readback
            )
            calibration_record_ok = (
                calibration.get("record_count") == aggregated.get("record_count")
                and calibration.get("distinct_run_ids") == aggregated.get("record_count")
                and all(calibration.get(record_key) == aggregate_metrics.get(aggregate_key) for record_key, aggregate_key in metric_pairs.items())
                and calibration.get("threshold_status") == ("failed_value_thresholds_route_repair_required" if threshold_failed else "value_thresholds_met")
                and activation_record.get("mk734b_durable_plugin_distribution", {}).get("plugin_version") == plugin_manifest_version
                and activation_record.get("session_continuity_contract", {}).get("plugin_owned_source_readback_implemented") is True
                and task_summary == expected_task_summary
                and installed_readback_ok
                and mk734_task.get("next_selected_action") == activation_record.get("next_selected_action")
                and (
                    not threshold_failed
                    or (
                        routes.get("llm_for_deterministic_comparison_schema_fixture_path_or_git_state") == "demoted"
                        and routes.get("disable_remote_plugin_as_context_optimization") == "retired"
                        and "real_task_sample_measured_but_value_threshold_not_met" in activation_record.get("non_claims", [])
                    )
                )
            )
            if not calibration_record_ok:
                blocks.append("BLOCKED_FOR_MK734_VALUE_CALIBRATION_DRIFT")
        if outcome.returncode or not (empty_aggregate_ok or measured_aggregate_ok) or source_metrics.get("measurement_status") != "not_measured_pending_observable_outcomes" or outcome_provenance.get("supervised_promotion_eligible") is not False or tier_chains.get("normal_local_bounded_supervised") != ["evidence_ref"] or set(tier_chains.get("autonomous_profile_qualified", [])) != required_provenance or not required_outcome_negatives <= set(measurement.get("mandatory_adversarial_cases", [])) or outcome_controls.returncode or outcome_negative.get("status") != "PASS_OUTCOME_EVIDENCE_NEGATIVE_CONTROLS" or not supervised_controls or not all(supervised_controls.values()):
            blocks.append("BLOCKED_FOR_MK733J_N_OUTCOME_EXECUTION")
        compiled = json.loads(context.stdout)
        context_negative = json.loads(context_controls.stdout)
        required_context_negatives = {"context_artifact_role_swap","context_artifact_forged_current_binding","context_artifact_outside_repo"}
        if context.returncode or compiled.get("blocks") or compiled.get("decision_score_measurement_status") != "not_measured_blocking" or compiled.get("artifact_role") != "compiled" or not compiled.get("artifact_digest") or not required_context_negatives <= set(measurement.get("mandatory_adversarial_cases", [])) or context_controls.returncode or context_negative.get("status") != "PASS_CONTEXT_COMPARATOR_NEGATIVE_CONTROLS":
            blocks.append("BLOCKED_FOR_MK733J_N_CONTEXT_EXECUTION")
    except json.JSONDecodeError:
        blocks.extend(["BLOCKED_FOR_MK733J_N_OUTCOME_EXECUTION", "BLOCKED_FOR_MK733J_N_CONTEXT_EXECUTION"])
    return sorted(set(blocks))


def source_check_blocks_allowed(blocks: object) -> bool:
    return (
        isinstance(blocks, list)
        and all(isinstance(item, str) for item in blocks)
        and set(blocks) <= SOURCE_CHECK_RUNTIME_CLAIM_BLOCKS
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--fixture")
    parser.add_argument("--expect-fail", action="store_true")
    parser.add_argument("--source-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.fixture and args.source_check:
        parser.error("--source-check cannot be combined with --fixture")
    if args.fixture:
        fixture = Path(args.fixture)
        if not fixture.is_absolute(): fixture = REPO / fixture
        fixture_doc = load(fixture)
        blocks = semantic_blocks(mutate(load(RECORD), fixture_doc.get("mutations", {})))
        expected = fixture_doc.get("expected_blocks", [])
        passed = bool(expected) and sorted(blocks) == sorted(expected) if args.expect_fail else not blocks
        result = {"verifier": "verify_mk733j_n_implementation", "mode": "fixture", "status": "PASS" if passed else "FAIL", "blocks": blocks, "expected_blocks": expected, "unexpected_blocks": sorted(set(blocks) - set(expected))}
    else:
        blocks = live_blocks(load(RECORD))
        source_check_passed = args.source_check and source_check_blocks_allowed(blocks)
        result = {"verifier": "verify_mk733j_n_implementation", "mode": "source_check" if args.source_check else "live", "status": "PASS_SOURCE_CONTRACT_RUNTIME_CLAIM_CHECKS_OPEN" if source_check_passed else ("PASS_IMPLEMENTATION_CONTRACT_INFRASTRUCTURE_EMPIRICAL_RUNTIME_AND_QUALITY_BOUNDARIES_OPEN" if not blocks else "FAIL"), "blocks": blocks, "installed_skill_cache_health": installed_skill_cache_health(), "non_claims": ["no_empirical_model_qualification", "no_runtime_activation", "no_permanent_firing", "no_custom_agent_runtime_load_or_invocation", "no_outcome_effectiveness", "no_context_quality_score", "no_product_or_user_acceptance"]}
        passed = source_check_passed or not blocks
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
