#!/usr/bin/env python3
"""MK733N deterministic context compiler and current-artifact comparator."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO / "research/mk675/fable5_decision_os"
CORPUS = SOURCE_ROOT / "mk733j_n_policy_corpus.json"
BASELINE = SOURCE_ROOT / "mk733j_n_context_baseline.json"
WORKPACK = SOURCE_ROOT / "mk733j_gpt56_model_neutral_workpack.json"
IMPLEMENTATION = SOURCE_ROOT / "mk733j_n_decision_os_implementation.json"
COMPILER_VERSION = "mk733j-n-context-compiler.v2"
FORBIDDEN = ("raw_prompt", "prompt", "transcript", "secret", "credential", "token", "hidden_reasoning")
REQUEST_FIELDS = ("policy_ids", "required_policy_ids", "required_non_claims")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_ref(path: Path) -> str:
    return path.resolve().relative_to(REPO.resolve()).as_posix()


def contains_forbidden(value: Any) -> bool:
    """Reject raw payload structure, not safe nonclaim/prohibition wording."""
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN or normalized.startswith("raw_"):
                return True
            if normalized in {"content_class", "payload_class"} and isinstance(nested, str) and nested.lower() in FORBIDDEN:
                return True
            if contains_forbidden(nested):
                return True
    elif isinstance(value, list):
        return any(contains_forbidden(item) for item in value)
    return False


def nonempty_unique_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
        and len(value) == len(set(value))
    )


def normalized_request(request: Any) -> dict[str, list[str]]:
    if not isinstance(request, dict):
        return {key: [] for key in REQUEST_FIELDS}
    return {
        "policy_ids": list(request.get("policy_ids", [])) if isinstance(request.get("policy_ids", []), list) else [],
        "required_policy_ids": list(request.get("required_policy_ids", [])) if isinstance(request.get("required_policy_ids", []), list) else [],
        "required_non_claims": list(request.get("required_non_claims", [])) if isinstance(request.get("required_non_claims", []), list) else [],
    }


def current_source_binding() -> dict[str, str]:
    baseline = load(BASELINE)
    return {
        "compiler_ref": relative_ref(Path(__file__)),
        "compiler_version": COMPILER_VERSION,
        "compiler_digest": file_digest(Path(__file__)),
        "corpus_ref": relative_ref(CORPUS),
        "corpus_digest": file_digest(CORPUS),
        "workpack_ref": relative_ref(WORKPACK),
        "workpack_digest": file_digest(WORKPACK),
        "baseline_ref": relative_ref(BASELINE),
        "baseline_digest": digest(baseline.get("baseline_payload")),
        "baseline_artifact_digest": file_digest(BASELINE),
    }


def seal_artifact(value: dict[str, Any]) -> dict[str, Any]:
    artifact = dict(value)
    artifact["artifact_digest"] = digest(artifact)
    return artifact


def compile_context(
    request: dict[str, Any],
    baseline_path: Path = BASELINE,
    artifact_role: str = "compiled",
) -> dict[str, Any]:
    corpus = load(CORPUS)
    implementation = load(IMPLEMENTATION)
    binding = current_source_binding()
    blocks: list[str] = []
    resolved_baseline = baseline_path.resolve()
    if resolved_baseline != BASELINE.resolve():
        blocks.append("BLOCKED_FOR_MK733N_BASELINE_REF_NOT_CURRENT")
    if REPO.resolve() not in resolved_baseline.parents or not resolved_baseline.is_file():
        baseline = {}
        blocks.append("BLOCKED_FOR_MK733N_CONTEXT_ARTIFACT_REF_INVALID")
    else:
        try:
            baseline = load(resolved_baseline)
        except (OSError, json.JSONDecodeError, TypeError):
            baseline = {}
            blocks.append("BLOCKED_FOR_MK733N_BASELINE_TAMPERED")
    normalized = normalized_request(request)
    requested = normalized["policy_ids"]
    required = set(normalized["required_policy_ids"])
    required_nonclaims = set(normalized["required_non_claims"])
    if contains_forbidden(request):
        blocks.append("BLOCKED_FOR_MK733N_RAW_CONTEXT_INPUT")
    if (
        not isinstance(request, dict)
        or not set(REQUEST_FIELDS) <= set(request)
        or any(not nonempty_unique_strings(request.get(key, [])) for key in REQUEST_FIELDS)
    ):
        blocks.append("BLOCKED_FOR_MK733N_CONTEXT_REQUEST_SCHEMA")
    if artifact_role not in {"baseline", "compiled"}:
        blocks.append("BLOCKED_FOR_MK733N_CONTEXT_ARTIFACT_ROLE")
    allowed = {
        policy.get("policy_id"): policy
        for policy in corpus.get("policies", [])
        if isinstance(policy, dict) and isinstance(policy.get("policy_id"), str)
    }
    if not required <= set(requested):
        blocks.append("BLOCKED_FOR_MK733N_REQUIRED_POLICY_OR_NONCLAIM_MISSING")
    if any(policy_id not in allowed for policy_id in requested):
        blocks.append("BLOCKED_FOR_MK733N_IRRELEVANT_POLICY_REF")
    if baseline.get("baseline_digest") != digest(baseline.get("baseline_payload")):
        blocks.append("BLOCKED_FOR_MK733N_BASELINE_TAMPERED")
    implementation_binding = implementation.get("workpack_binding", {})
    if (
        corpus.get("workpack_digest") != binding["workpack_digest"]
        or implementation_binding.get("workpack_digest") != binding["workpack_digest"]
        or implementation_binding.get("workpack_ref") != binding["workpack_ref"]
    ):
        blocks.append("BLOCKED_FOR_MK733N_CURRENT_SOURCE_BINDING")

    selected = [allowed[policy_id] for policy_id in requested if policy_id in allowed]
    compiled = {
        "policy_ids": requested,
        "brief_requirements": [policy["brief"] for policy in selected],
        "non_claims": sorted({claim for policy in selected for claim in policy["non_claims"]}),
        "workpack_digest": binding["workpack_digest"],
        "stop_and_escalation_rules": ["unknown_or_unqualified_identity_stops_or_escalates"],
    }
    if not required_nonclaims <= set(compiled["non_claims"]):
        blocks.append("BLOCKED_FOR_MK733N_REQUIRED_POLICY_OR_NONCLAIM_MISSING")
    baseline_payload = baseline.get("baseline_payload", {})
    baseline_bytes = len(canonical(baseline_payload))
    compiled_bytes = len(canonical(compiled))
    ratio = compiled_bytes / baseline_bytes if baseline_bytes else 1.0
    if artifact_role == "compiled" and ratio > 0.5:
        blocks.append("BLOCKED_FOR_MK733N_CONTEXT_RATIO_EXCEEDED")
    context_payload = compiled if artifact_role == "compiled" else baseline_payload
    status = (
        "CONTEXT_COMPILED_RATIO_MEASURED_QUALITY_UNMEASURED"
        if artifact_role == "compiled"
        else "CONTEXT_BASELINE_ARTIFACT_PRODUCED_QUALITY_UNMEASURED"
    )
    artifact = {
        "artifact_type": "mk733j_n_context_artifact",
        "artifact_version": 2,
        "artifact_role": artifact_role,
        "compiler_request": normalized,
        "request_digest": digest(normalized),
        "source_binding": binding,
        "context_payload": context_payload,
        "context_digest": digest(context_payload),
        "required_policy_recall": len(required & set(requested)) / len(required) if required else 1.0,
        "irrelevant_policy_refs": sum(policy_id not in allowed for policy_id in requested),
        "baseline_version": baseline.get("version"),
        "baseline_digest": baseline.get("baseline_digest"),
        "compiled_bytes": compiled_bytes,
        "baseline_bytes": baseline_bytes,
        "compiled_to_baseline_ratio": ratio,
        "decision_score_regression_points": None,
        "decision_score_measurement_status": "not_measured_blocking",
        "blocks": sorted(set(blocks)),
        "status": status if not blocks else "FAIL_CONTEXT_COMPILATION",
        "non_claims": ["no_model_quality_measurement", "no_raw_prompt_or_transcript_retention"],
    }
    if artifact_role == "compiled":
        artifact["compiled"] = compiled
    else:
        artifact["baseline"] = baseline_payload
    return seal_artifact(artifact)


def safe_repo_ref(value: Any) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    path = (REPO / value).resolve()
    if not path.is_file() or REPO.resolve() not in path.parents or "fixtures" in path.parts:
        return None
    return path


def valid_context_artifact(artifact: Any, expected_role: str) -> bool:
    if (
        not isinstance(artifact, dict)
        or artifact.get("artifact_type") != "mk733j_n_context_artifact"
        or artifact.get("artifact_version") != 2
        or artifact.get("artifact_role") != expected_role
        or artifact.get("blocks")
        or artifact.get("source_binding") != current_source_binding()
        or not isinstance(artifact.get("compiler_request"), dict)
    ):
        return False
    expected = compile_context(artifact["compiler_request"], BASELINE, expected_role)
    return artifact == expected


def compare_failure(block: str, reason: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "FAIL_CONTEXT_QUALITY_CONTRACT", "blocks": [block]}
    if reason:
        result["reason"] = reason
    return result


def public_observable_compare(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Measure paired public outputs without importing qualification evidence.

    This path intentionally accepts only gold-free, structured evaluator output
    and current compiler artifacts.  It is not an identity, provider, holdout,
    profile-import, or route-unlock path.
    """
    required = {
        "measurement_mode", "baseline_context_ref", "compiled_context_ref",
        "baseline_output_ref", "compiled_output_ref", "baseline_variant",
        "compiled_variant", "model", "reasoning_effort", "run_family",
        "evaluation_corpus_digest", "evaluation_schema_digest",
    }
    if not isinstance(evaluation, dict) or set(evaluation) != required:
        return compare_failure("BLOCKED_FOR_MK733N_PUBLIC_OBSERVABLE_SCHEMA")
    if evaluation.get("measurement_mode") != "public_observable_not_qualified":
        return compare_failure("BLOCKED_FOR_MK733N_PUBLIC_OBSERVABLE_SCHEMA")
    if not all(isinstance(evaluation.get(key), str) and evaluation[key].strip() for key in required):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_REF_INVALID")
    refs = (
        "baseline_context_ref", "compiled_context_ref",
        "baseline_output_ref", "compiled_output_ref",
    )
    paths = {key: safe_repo_ref(evaluation[key]) for key in refs}
    if not all(paths.values()):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_REF_INVALID")
    if (
        evaluation["baseline_context_ref"] == evaluation["compiled_context_ref"]
        or evaluation["baseline_output_ref"] == evaluation["compiled_output_ref"]
        or evaluation["baseline_variant"] == evaluation["compiled_variant"]
    ):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_COMPARATOR_BINDING")
    try:
        baseline = load(paths["baseline_context_ref"])
        compiled = load(paths["compiled_context_ref"])
        baseline_output = load(paths["baseline_output_ref"])
        compiled_output = load(paths["compiled_output_ref"])
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_REF_INVALID")
    if not valid_context_artifact(baseline, "baseline") or not valid_context_artifact(compiled, "compiled"):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_ARTIFACT_NOT_CURRENT_COMPILER_OUTPUT")
    if (
        baseline.get("context_digest") == compiled.get("context_digest")
        or baseline.get("compiler_request") != compiled.get("compiler_request")
        or baseline.get("request_digest") != compiled.get("request_digest")
        or baseline.get("required_policy_recall") != 1.0
        or compiled.get("required_policy_recall") != 1.0
        or baseline.get("irrelevant_policy_refs") != 0
        or compiled.get("irrelevant_policy_refs") != 0
        or compiled.get("compiled_to_baseline_ratio", 1) > 0.5
    ):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_COMPARATOR_BINDING")
    try:
        sys.path.insert(0, str(REPO / "scripts/ops"))
        import mk733j_qualification as qualification

        output_fields = {
            "prompt_context_digest", "context_variant", "run_family", "issuance_id",
            "declared_model", "declared_reasoning_effort", "outputs",
        }
        compact_output_fields = output_fields | {"output_format"}
        def normalized_public_output(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict) or qualification.sensitive(value):
                raise ValueError
            if set(value) == output_fields:
                return value
            if set(value) == compact_output_fields and value.get("output_format") == "mk733j-compact-ordered-v4":
                return qualification.expand_compact_outputs(value)
            raise ValueError
        baseline_raw = baseline_output
        compiled_raw = compiled_output
        baseline_output = normalized_public_output(baseline_raw)
        compiled_output = normalized_public_output(compiled_raw)
        if (
            set(baseline_output) != output_fields
            or set(compiled_output) != output_fields
            or qualification.sensitive(baseline_raw)
            or qualification.sensitive(compiled_raw)
        ):
            raise ValueError
        baseline_grade = qualification.grade(baseline_output)
        compiled_grade = qualification.grade(compiled_output)
        contract = qualification.evaluation_contract_digests()
        if (
            baseline_grade.get("blocks")
            or compiled_grade.get("blocks")
            or baseline_grade.get("corpus_digest") != evaluation["evaluation_corpus_digest"]
            or compiled_grade.get("corpus_digest") != evaluation["evaluation_corpus_digest"]
            or contract.get("evaluation_schema_digest") != evaluation["evaluation_schema_digest"]
            or baseline_output.get("prompt_context_digest") != baseline.get("context_digest")
            or compiled_output.get("prompt_context_digest") != compiled.get("context_digest")
            or baseline_output.get("context_variant") != evaluation["baseline_variant"]
            or compiled_output.get("context_variant") != evaluation["compiled_variant"]
            or baseline_output.get("run_family") != evaluation["run_family"]
            or compiled_output.get("run_family") != evaluation["run_family"]
            or baseline_output.get("declared_model") != evaluation["model"]
            or compiled_output.get("declared_model") != evaluation["model"]
            or baseline_output.get("declared_reasoning_effort") != evaluation["reasoning_effort"]
            or compiled_output.get("declared_reasoning_effort") != evaluation["reasoning_effort"]
            or qualification.digest(baseline_raw) == qualification.digest(compiled_raw)
        ):
            raise ValueError
        regression = (
            baseline_grade["weighted_disposition_match"]
            - compiled_grade["weighted_disposition_match"]
        ) * 100
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError, json.JSONDecodeError):
        return compare_failure("BLOCKED_FOR_MK733N_PUBLIC_OBSERVABLE_BINDING")
    return {
        "status": "CONTEXT_QUALITY_PUBLIC_OBSERVABLE_MEASURED_WITHIN_THRESHOLD" if regression <= 2 else "FAIL_CONTEXT_QUALITY_CONTRACT",
        "blocks": [] if regression <= 2 else ["BLOCKED_FOR_MK733N_CONTEXT_REGRESSION"],
        "measurement_mode": "public_observable_not_qualified",
        "declared_model": evaluation["model"],
        "declared_reasoning_effort": evaluation["reasoning_effort"],
        "run_family": evaluation["run_family"],
        "context_variants": {
            "baseline": evaluation["baseline_variant"],
            "compiled": evaluation["compiled_variant"],
        },
        "decision_score_regression_points": regression,
        "required_policy_recall": 1.0,
        "irrelevant_policy_refs": 0,
        "compiled_to_baseline_ratio": compiled["compiled_to_baseline_ratio"],
        "source_binding": current_source_binding(),
        "output_digests": {
            "baseline": qualification.digest(baseline_raw),
            "compiled": qualification.digest(compiled_raw),
        },
        "qualification_state": "not_qualified_public_observable_measurement_only",
        "non_claims": [
            "no_profile_qualification_or_import", "no_route_unlock", "no_model_parity",
            "no_runtime_readiness", "no_product_or_user_acceptance",
        ],
    }


def compare(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Compare matched qualifications only through current compiler artifacts."""
    if isinstance(evaluation, dict) and evaluation.get("measurement_mode") == "public_observable_not_qualified":
        return public_observable_compare(evaluation)
    required = {
        "baseline_context_ref",
        "compiled_context_ref",
        "baseline_qualification_ref",
        "compiled_qualification_ref",
        "baseline_variant",
        "compiled_variant",
        "model",
        "reasoning_effort",
        "run_family",
    }
    if not isinstance(evaluation, dict) or not required <= set(evaluation):
        return {
            "status": "CONTEXT_QUALITY_NOT_MEASURED_BLOCKING",
            "blocks": [],
            "reason": "observable baseline/compiled evaluation packets have not both been imported",
        }
    if not all(isinstance(evaluation[key], str) and bool(evaluation[key].strip()) for key in required):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_REF_INVALID")
    paths = {key: safe_repo_ref(evaluation[key]) for key in (
        "baseline_context_ref", "compiled_context_ref", "baseline_qualification_ref", "compiled_qualification_ref"
    )}
    if not all(paths.values()):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_REF_INVALID")
    if (
        evaluation["baseline_context_ref"] == evaluation["compiled_context_ref"]
        or evaluation["baseline_qualification_ref"] == evaluation["compiled_qualification_ref"]
        or evaluation["baseline_variant"] == evaluation["compiled_variant"]
    ):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_COMPARATOR_BINDING")
    try:
        baseline = load(paths["baseline_context_ref"])
        compiled = load(paths["compiled_context_ref"])
        baseline_qualification = load(paths["baseline_qualification_ref"])
        compiled_qualification = load(paths["compiled_qualification_ref"])
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_REF_INVALID")
    if not valid_context_artifact(baseline, "baseline") or not valid_context_artifact(compiled, "compiled"):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_ARTIFACT_NOT_CURRENT_COMPILER_OUTPUT")
    if (
        baseline.get("context_digest") == compiled.get("context_digest")
        or baseline.get("compiler_request") != compiled.get("compiler_request")
        or baseline.get("request_digest") != compiled.get("request_digest")
        or baseline.get("required_policy_recall") != 1.0
        or compiled.get("required_policy_recall") != 1.0
        or baseline.get("irrelevant_policy_refs") != 0
        or compiled.get("irrelevant_policy_refs") != 0
        or compiled.get("compiled_to_baseline_ratio", 1) > 0.5
    ):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_COMPARATOR_BINDING")
    nested_refs = ("outputs_ref", "identity_verification_ref", "evidence_ref")
    if any(
        not safe_repo_ref(result.get(ref_key))
        for result in (baseline_qualification, compiled_qualification)
        for ref_key in nested_refs
    ):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_BINDING")
    try:
        sys.path.insert(0, str(REPO / "scripts/ops"))
        import mk733j_qualification as qualification

        if qualification.validate_import(baseline_qualification,test_isolated=True) or qualification.validate_import(compiled_qualification,test_isolated=True):
            raise ValueError
        if any(
            result.get(key) != evaluation[key]
            for result in (baseline_qualification, compiled_qualification)
            for key in ("model", "reasoning_effort")
        ):
            raise ValueError
        if any(result.get("run_family") != evaluation["run_family"] for result in (baseline_qualification, compiled_qualification)):
            raise ValueError
        if baseline_qualification.get("prompt_context_digest") != baseline["context_digest"]:
            raise ValueError
        if compiled_qualification.get("prompt_context_digest") != compiled["context_digest"]:
            raise ValueError
        if baseline_qualification.get("context_variant") != evaluation["baseline_variant"]:
            raise ValueError
        if compiled_qualification.get("context_variant") != evaluation["compiled_variant"]:
            raise ValueError
        if baseline_qualification.get("output_digest") == compiled_qualification.get("output_digest"):
            raise ValueError
        if baseline_qualification.get("outputs_ref") == compiled_qualification.get("outputs_ref"):
            raise ValueError
        regression = (
            baseline_qualification["grade"]["weighted_disposition_match"]
            - compiled_qualification["grade"]["weighted_disposition_match"]
        ) * 100
    except (ValueError, KeyError, TypeError, AttributeError):
        return compare_failure("BLOCKED_FOR_MK733N_CONTEXT_EVALUATION_BINDING")
    return {
        "status": "CONTEXT_QUALITY_MEASURED_WITHIN_THRESHOLD" if regression <= 2 else "FAIL_CONTEXT_QUALITY_CONTRACT",
        "blocks": [] if regression <= 2 else ["BLOCKED_FOR_MK733N_CONTEXT_REGRESSION"],
        "decision_score_regression_points": regression,
        "required_policy_recall": 1.0,
        "irrelevant_policy_refs": 0,
        "compiled_to_baseline_ratio": compiled["compiled_to_baseline_ratio"],
        "source_binding": current_source_binding(),
        "non_claims": ["no_runtime_readiness", "no_product_or_user_acceptance"],
    }


def write_qualification_result(
    temporary: Path,
    context_artifact: dict[str, Any],
    context_variant: str,
    name: str,
    qualification: Any,
) -> Path:
    run_family = "mk733n-harness-family"
    thread_run_id = f"mk733n-{name}-thread"
    corpus = load(qualification.CORPUS)
    issuance=qualification.issuance_seed(corpus,context_artifact["context_digest"],context_variant,run_family)
    outputs = {
        "prompt_context_digest": context_artifact["context_digest"],
        "context_variant": context_variant,
        "run_family": run_family,
        "issuance_id": issuance,
        "outputs": [qualification.synthetic_output(case,issuance) for case in corpus["cases"]],
    }
    outputs_path = temporary / f"{name}-outputs.json"
    outputs_path.write_text(json.dumps(outputs, sort_keys=True), encoding="utf-8")
    grade = qualification.grade(outputs)
    profile_id = "sol_ultra_architect_cmd"
    profile = qualification.profiles()[profile_id]
    runtime_model_identity = qualification.aliases(profile_id)[0]
    workpack = qualification.workpack_binding()
    qualified_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    bind = {
        "bundle_id": "decision_judgment",
        "task_class": "ambiguous_design",
        "profile_id": profile_id,
        "profile_digest": qualification.digest(profile),
        "runtime_model_identity": runtime_model_identity,
        "model": runtime_model_identity,
        "reasoning_effort": profile["reasoning_effort"],
        "thread_run_id": thread_run_id,
        "run_family": run_family,
        "prompt_context_digest": context_artifact["context_digest"],
        "context_variant": context_variant,
        "corpus_digest": grade["corpus_digest"],
        "evaluation_schema_digest": qualification.evaluation_contract_digests()["evaluation_schema_digest"],
        "output_digest": grade["output_digest"],
        "outputs_ref": relative_ref(outputs_path),
        "qualified_at": qualified_at,
        "expires_at": expires_at,
        "workpack_digest": workpack["workpack_digest"],
        "binding_record_digest": workpack["binding_record_digest"],
    }
    identity_path = temporary / f"{name}-identity.json"
    evidence_path = temporary / f"{name}-evidence.json"
    attestation = {"record_type":"mk733j_provider_session_attestation","source_class": "test_only_cmd_provider_session_attestation", "authority_id":"test-only-context-authority", "issuer_class":"test_only_cmd", "capability":"qualification_identity", **bind, "execution_environment": "isolated", "grader_gold_access": False, "observed_at": qualified_at}
    attestation["attestation_digest"] = qualification.digest(attestation)
    attestation_path = temporary / f"{name}-provider-attestation.json"
    attestation_path.write_text(json.dumps(attestation, sort_keys=True), encoding="utf-8")
    identity = {"source_class": "test_only_cmd_provider_attested_session_identity", **bind, "execution_environment": "isolated", "grader_gold_access": False, "source_attestation_ref": relative_ref(attestation_path), "source_attestation_digest": attestation["attestation_digest"], "observed_at": qualified_at}
    identity["envelope_digest"] = qualification.digest(identity)
    evidence = {"source_class": "test_only_observable_structured_output", **bind, "observed_at": "2026-07-10T00:00:01Z"}
    evidence["envelope_digest"] = qualification.digest(evidence)
    identity_path.write_text(json.dumps(identity, sort_keys=True), encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    result = {
        **bind,
        "identity_verification_ref": relative_ref(identity_path),
        "evidence_ref": relative_ref(evidence_path),
        "grade": grade,
    }
    result_path = temporary / f"{name}-qualification.json"
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return result_path


def write_public_observable_output(
    temporary: Path,
    context_artifact: dict[str, Any],
    context_variant: str,
    name: str,
    qualification: Any,
    run_family: str,
    declared_model: str,
    declared_reasoning_effort: str,
) -> tuple[Path, dict[str, Any]]:
    """Create a safe, gold-free-shaped output only for isolated comparator tests."""
    corpus = load(qualification.CORPUS)
    issuance = qualification.issuance_seed(
        corpus, context_artifact["context_digest"], context_variant, run_family
    )
    output = {
        "prompt_context_digest": context_artifact["context_digest"],
        "context_variant": context_variant,
        "run_family": run_family,
        "issuance_id": issuance,
        "declared_model": declared_model,
        "declared_reasoning_effort": declared_reasoning_effort,
        "outputs": [qualification.synthetic_output(case, issuance) for case in corpus["cases"]],
    }
    path = temporary / f"{name}-public-output.json"
    path.write_text(json.dumps(output, sort_keys=True), encoding="utf-8")
    return path, output


def self_test() -> dict[str, Any]:
    request = load(REPO / "fixtures/mk675/fable5_decision_os/positive_mk733j_n_context_request.json")
    sys.path.insert(0, str(REPO / "scripts/ops"))
    import mk733j_qualification as qualification

    with tempfile.TemporaryDirectory(prefix=".mk733n-self-test-", dir=SOURCE_ROOT) as directory:
        temporary = Path(directory)
        baseline = compile_context(request, BASELINE, "baseline")
        compiled = compile_context(request, BASELINE, "compiled")
        baseline_path = temporary / "baseline-context.json"
        compiled_path = temporary / "compiled-context.json"
        baseline_path.write_text(json.dumps(baseline, sort_keys=True), encoding="utf-8")
        compiled_path.write_text(json.dumps(compiled, sort_keys=True), encoding="utf-8")
        baseline_qualification_path = write_qualification_result(
            temporary, baseline, "mk733n-baseline", "baseline", qualification
        )
        compiled_qualification_path = write_qualification_result(
            temporary, compiled, "mk733n-compiled", "compiled", qualification
        )
        public_run_family = "mk733n-public-observable-harness"
        public_model = "qwen3.6:35b-a3b-coding-mxfp8"
        public_reasoning = "high"
        baseline_public_path, baseline_public = write_public_observable_output(
            temporary, baseline, "mk733n-public-baseline", "baseline", qualification,
            public_run_family, public_model, public_reasoning,
        )
        compiled_public_path, compiled_public = write_public_observable_output(
            temporary, compiled, "mk733n-public-compiled", "compiled", qualification,
            public_run_family, public_model, public_reasoning,
        )
        evaluation = {
            "baseline_context_ref": relative_ref(baseline_path),
            "compiled_context_ref": relative_ref(compiled_path),
            "baseline_qualification_ref": relative_ref(baseline_qualification_path),
            "compiled_qualification_ref": relative_ref(compiled_qualification_path),
            "baseline_variant": "mk733n-baseline",
            "compiled_variant": "mk733n-compiled",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "ultra",
            "run_family": "mk733n-harness-family",
        }
        public_evaluation = {
            "measurement_mode": "public_observable_not_qualified",
            "baseline_context_ref": relative_ref(baseline_path),
            "compiled_context_ref": relative_ref(compiled_path),
            "baseline_output_ref": relative_ref(baseline_public_path),
            "compiled_output_ref": relative_ref(compiled_public_path),
            "baseline_variant": "mk733n-public-baseline",
            "compiled_variant": "mk733n-public-compiled",
            "model": public_model,
            "reasoning_effort": public_reasoning,
            "run_family": public_run_family,
            "evaluation_corpus_digest": qualification.digest(load(qualification.CORPUS)),
            "evaluation_schema_digest": qualification.evaluation_contract_digests()["evaluation_schema_digest"],
        }
        positive = compare(evaluation)
        public_positive = compare(public_evaluation)
        swapped = deepcopy(evaluation)
        swapped["baseline_context_ref"], swapped["compiled_context_ref"] = (
            swapped["compiled_context_ref"], swapped["baseline_context_ref"]
        )
        swapped_qualifications = deepcopy(evaluation)
        swapped_qualifications["baseline_qualification_ref"], swapped_qualifications["compiled_qualification_ref"] = (
            swapped_qualifications["compiled_qualification_ref"],
            swapped_qualifications["baseline_qualification_ref"],
        )
        forged_artifact = deepcopy(compiled)
        forged_artifact["source_binding"]["corpus_digest"] = "0" * 64
        forged_artifact.pop("artifact_digest", None)
        forged_artifact["artifact_digest"] = digest(forged_artifact)
        forged_path = temporary / "forged-context.json"
        forged_path.write_text(json.dumps(forged_artifact, sort_keys=True), encoding="utf-8")
        forged = {**evaluation, "compiled_context_ref": relative_ref(forged_path)}
        public_same_output = {**public_evaluation, "compiled_output_ref": public_evaluation["baseline_output_ref"]}
        tampered_public = deepcopy(compiled_public)
        tampered_public["outputs"][0]["disposition"] = "block"
        tampered_path = temporary / "tampered-public-output.json"
        tampered_path.write_text(json.dumps(tampered_public, sort_keys=True), encoding="utf-8")
        public_tampered = {**public_evaluation, "compiled_output_ref": relative_ref(tampered_path)}
        stale_schema = {**public_evaluation, "evaluation_schema_digest": "0" * 64}
        absolute = {**evaluation, "baseline_context_ref": str(baseline_path.resolve())}
        with tempfile.TemporaryDirectory(prefix="mk733n-outside-") as external_directory:
            external_path = Path(external_directory) / "baseline-context.json"
            external_path.write_text(json.dumps(baseline, sort_keys=True), encoding="utf-8")
            outside = {
                **evaluation,
                "baseline_context_ref": os.path.relpath(external_path, REPO),
            }
            outside_rejected = bool(compare(outside).get("blocks"))
        results = {
            "positive_current_artifact_pair": positive.get("status") == "CONTEXT_QUALITY_MEASURED_WITHIN_THRESHOLD",
            "swapped_roles_rejected": bool(compare(swapped).get("blocks")),
            "swapped_qualifications_rejected": bool(compare(swapped_qualifications).get("blocks")),
            "forged_current_binding_rejected": bool(compare(forged).get("blocks")),
            "absolute_ref_rejected": bool(compare(absolute).get("blocks")),
            "outside_repo_ref_rejected": outside_rejected,
            "public_observable_pair_without_qualification_import": public_positive.get("status") == "CONTEXT_QUALITY_PUBLIC_OBSERVABLE_MEASURED_WITHIN_THRESHOLD" and public_positive.get("qualification_state") == "not_qualified_public_observable_measurement_only",
            "public_observable_identical_output_rejected": bool(compare(public_same_output).get("blocks")),
            "public_observable_tampered_output_rejected": bool(compare(public_tampered).get("blocks")),
            "public_observable_stale_schema_rejected": bool(compare(stale_schema).get("blocks")),
        }
        passed = all(results.values())
        return {
            "status": "PASS_CONTEXT_COMPARATOR_NEGATIVE_CONTROLS" if passed else "FAIL_CONTEXT_COMPARATOR_NEGATIVE_CONTROLS",
            "blocks": [] if passed else ["BLOCKED_FOR_MK733N_CONTEXT_COMPARATOR_NEGATIVE_CONTROL"],
            "checks": results,
            "non_claim": "synthetic_harness_qualifications_are_not_empirical_context_quality_evidence",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request")
    parser.add_argument("--baseline")
    parser.add_argument("--artifact-role", choices=("baseline", "compiled"), default="compiled")
    parser.add_argument("--compare")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        result = self_test()
    elif args.compare:
        result = compare(load(Path(args.compare)))
    elif args.request:
        baseline_path = Path(args.baseline) if args.baseline else BASELINE
        if not baseline_path.is_absolute():
            baseline_path = REPO / baseline_path
        result = compile_context(load(Path(args.request)), baseline_path, args.artifact_role)
    else:
        parser.error("one of --request, --compare, or --self-test is required")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not result.get("blocks") else 1


if __name__ == "__main__":
    raise SystemExit(main())
