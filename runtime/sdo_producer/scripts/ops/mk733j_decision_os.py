#!/usr/bin/env python3
"""Deterministic MK733J-N routing and receipt helpers.

Receipts are local support-control envelopes, not runtime activation evidence.
They intentionally contain no prompt, transcript, secret, or hidden reasoning.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import critical_thread_route

REPO = Path(__file__).resolve().parents[2]
IMPLEMENTATION = REPO / "research/mk675/fable5_decision_os/mk733j_n_decision_os_implementation.json"
CAPABILITY_BUNDLES = REPO / "research/mk675/fable5_decision_os/mk733j_n_capability_bundles.json"
CORPUS = REPO / "research/mk675/fable5_decision_os/mk733j_n_qualification_corpus.json"
WORKPACK = REPO / "research/mk675/fable5_decision_os/mk733j_gpt56_model_neutral_workpack.json"
DURABLE_QUALIFICATION_RESULTS = REPO / "research/mk675/fable5_decision_os/qualification-results"
TRUSTED_AUTHORITY_DIR = REPO / "research/mk675/fable5_decision_os/authorities"
FORBIDDEN_RECEIPT_KEYS = {"raw_prompt", "prompt", "transcript", "hidden_reasoning", "secret", "credential", "token", "api_key", "access_token"}
RECEIPT_FIELDS = {
    "receipt_version", "execution_tier", "delegated_autonomy", "work_id", "goal_ref", "task_class", "risk_class", "phase", "route", "final_audit",
    "selected_profile", "profile_digest", "qualification_result_ref", "qualification_expires_at",
    "qualification_digest", "qualification_state", "qualification_results", "qualification_results_digest", "runtime_model_identity",
    "model_identity_state", "runtime_identity_state", "runtime_identity_ref", "thread_run_id", "context_digest",
    "implementer_identity_ref", "implementer_identity_digest", "auditor_identity_ref", "auditor_identity_digest", "audit_request_ref", "audit_request_digest", "audit_head_sha", "comparison_base_sha", "audit_result_ref", "audit_result_digest",
    "workpack_digest", "binding_record_digest",
    "preflight_ref", "preflight_digest", "preflight_scope_digest", "preflight_operation_manifest_digest", "preflight_contract_version",
    "allowed_tools", "budget", "return_schema", "readback_required",
    "auditor_independent_from_implementer", "issued_at", "expires_at", "policy_refs",
    "non_claims", "claim_scope", "allowed_path_prefixes", "allowed_command_classes",
    "forbidden_operation_classes", "external_protected_authority_state", "external_authority_ref",
    "external_authority_digest", "operation_manifest", "operation_manifest_digest",
    "operation_manifest_policy_digest",
    "scope_policy_digest", "receipt_digest",
}
AUTHORITY_GATE_RECEIPT_FIELDS = {
    "receipt_version", "execution_tier", "delegated_autonomy", "phase", "work_id", "goal_ref",
    "task_class", "risk_class", "context_digest", "workpack_digest", "binding_record_digest",
    "external_protected_authority_state", "external_authority_ref", "external_authority_digest",
    "authority_request_digest", "receipt_ttl_seconds", "allowed_tools", "allowed_path_prefixes",
    "allowed_command_classes", "forbidden_operation_classes", "operation_manifest",
    "operation_manifest_digest", "operation_manifest_policy_digest", "scope_policy_digest", "budget",
    "readback_required", "policy_refs", "non_claims", "claim_scope", "issued_at", "expires_at",
    "receipt_digest", "target_repository", "target_path", "target_revision", "operation",
    "operation_digest", "rollback", "exclusions",
}
AUTHORITY_GATE_REQUEST_FIELDS = {
    "execution_tier", "delegated_autonomy", "work_id", "goal_ref", "task_class", "risk_class",
    "context_digest", "external_protected_authority_state", "external_authority_ref",
    "external_authority_digest", "policy_refs", "non_claims", "receipt_ttl_seconds", "allowed_tools",
    "allowed_path_prefixes", "allowed_command_classes", "forbidden_operation_classes", "operation_manifest",
    "budget", "readback_required", "target_repository", "target_path", "target_revision", "operation",
    "operation_digest", "rollback", "exclusions",
}
TARGET_BOUND_FIELDS = {
    "target_repository", "target_path", "target_revision", "operation", "operation_digest", "rollback", "exclusions",
}
TARGET_BOUND_EXCLUSIONS = (
    "public_deploy_or_release", "provider_or_admin_mutation", "developer_id_or_notarization",
    "device_mutation", "external_operation",
)
TARGET_BOUND_ALLOWED_CLASS_SETS = (
    frozenset({"protected_git"}),
    frozenset({"credential", "destructive", "runtime_release"}),
)
TARGET_BOUND_LOCAL_COMBINED_OPERATION = "local_credential_destructive_runtime_release"
CLOSEOUT_ACTION_ID = "DecisionOSCloseout"
CLOSEOUT_ACTION_DIGEST = hashlib.sha256(CLOSEOUT_ACTION_ID.encode()).hexdigest()
CLOSEOUT_OPERATION_BYTES = 0
CLOSEOUT_OPERATION_LINES = 0
SDO_ROUTE_FIXTURES = (
    "negative_pms_proposed_steers_action.json",
    "negative_odg_grants_action.json",
    "negative_missing_lower_layers_blocks_safe_work.json",
    "negative_protected_without_authority.json",
)
SDO_LOWER_LAYERS = ("brain", "pms", "odg", "fable")
SDO_BRAIN_SOURCE_REF = "brain-cli:brain/pages/sdo-synaptic-decision-os.md#sdo-synaptic-decision-os"
SDO_PMS_RECORD_PREFIXES = ("pms://maestro-kernel/", "pms:maestro-kernel/")
SDO_ODG_RECORD_PREFIXES = ("odg://maestro-kernel/", "odg:maestro-kernel/")
SDO_LAYER_RECORD_TYPES = {
    "brain": {"brain_decision.v1", "brain-cli-decision.v1"},
    "pms": {"mk748_pms_cognitive_context.v1", "mk748_accepted_record.v1"},
    "odg": {"odg_decision_projection.v1", "mk738_odg_projection.v1"},
    "fable": {"fable5_synthesis_record.v1", "mk747_fable5_synthesis.v1"},
}
SDO_ODG_FIELDS = {
    "available", "source_layer", "record_type", "status", "record_status",
    "source_ref", "source_digest", "candidate_actions", "ranked_actions",
    "candidates", "projection", "asserts_authority", "authority_grants",
}
SDO_ODG_CANDIDATE_FIELDS = {"action_id", "rank", "score"}
SDO_ODG_FORBIDDEN_KEYS = {
    "asserts_authority", "authority_grants", "authority", "authorized",
    "permission", "permissions", "scope", "operation", "operation_binding",
    "new_action", "new_action_id", "create_action", "introduce_action",
}
SDO_TRUSTED_REPOSITORY_FILES = {
    "routing_table": Path("controls/routing-table.json"),
    "active_policy": Path("controls/active-policy-index.json"),
    "activation_state": Path("research/mk675/fable5_decision_os/mk733j_n_activation_state.json"),
    "authority_registry": Path("research/mk675/fable5_decision_os/mk733j_n_trusted_activation_authorities.json"),
}
SDO_FACTS_REFERENCE_PAIRS = (
    ("source_ref", "source_digest"),
    ("record_ref", "record_digest"),
    ("repository_state_ref", "repository_state_digest"),
)
SDO_FACTS_PROVENANCE_KEYS = {
    "layer", "source_layer", "record_type", "status", "review_status",
}
SDO_FACTS_KNOWLEDGE_KEYS = {
    "summary", "recommendation", "recommendations", "ranking", "ranked_actions",
    "candidate_ranking", "preferred_action", "selected_action", "selection",
    "rationale", "reason", "advice", "suggestion", "proposed_action",
    "knowledge", "constraints", "blocked_actions", "allowed_actions", "score",
    "scores", "rank",
}
SDO_PROTECTED_POLICY_TOKENS = {
    "authority", "auth", "credential", "oauth", "secret", "security", "store",
    "migration", "sharedcore", "sharedruntime", "maestrokernel", "runtime",
    "release", "deploy", "merge", "push", "network", "destructive", "dispatch",
    "external", "paid", "provider", "session", "lease", "approval", "account",
}
SDO_AUTHORITY_RESULT_FIELDS = {
    "action_id", "current", "status", "authority_ref", "authority_digest",
    "scope", "operation_binding", "validator_result",
}
SDO_TARGET_BINDING_FIELDS = {"target_ref", "scope", "operation"}
SDO_VALIDATOR_RESULT_FIELDS = {
    "validator_id", "status", "result_ref", "result_digest", "authority_digest",
    "target_binding_digest",
}
PROTECTED_OPERATION_CLASSES = {
    "protected_git", "network", "credential", "credential_path", "destructive",
    "runtime_release", "python_inline", "shell_wrapper", "shell_chaining",
}
SECRET_PATH_PARTS = {".env", ".ssh", "credentials", "secrets", "tokens"}
PROFILE_RESULT_ROW_FIELDS = {
    "profile_id","profile_digest","task_class","bundle_id","bundle_version","bundle_digest","workpack_digest","binding_record_digest",
    "evaluation_corpus_digest","evaluation_schema_digest","evidence_class","result_ref","result_digest","identity_readback_ref","sealed_holdout_ref",
    "identity_envelope_digest","sealed_holdout_envelope_digest","authority_id","holdout_authority_ref","holdout_authority_digest",
    "authority_profile_result_digest","authority_identity_envelope_digest","runtime_model_identity","model","reasoning_effort","thread_run_id",
    "qualified_at","expires_at","qualification_state","qualification_digest",
}
PROFILE_RESULT_DOCUMENT_FIELDS = {
    "record_type","profile_id","profile_digest","task_class","bundle_id","bundle_version","bundle_digest","workpack_digest","binding_record_digest",
    "evaluation_corpus_digest","evaluation_schema_digest","evidence_class","source_result_digest","public_output_digest","metrics",
    "source_identity_envelope_digest","source_sealed_holdout_envelope_digest","identity_readback_ref","sealed_holdout_ref","identity_envelope_digest",
    "sealed_holdout_envelope_digest","authority_id","holdout_authority_ref","holdout_authority_digest","authority_profile_result_digest",
    "authority_identity_envelope_digest","runtime_model_identity","model","reasoning_effort","thread_run_id","qualified_at","expires_at","result_digest",
}
SEALED_PUBLIC_SEMANTIC_FIELDS = {"sealed_public_semantic_contract_ref","sealed_public_semantic_contract_digest"}
PUBLIC_SEMANTIC_RESULT_FIELDS = {"public_semantic_contract_ref","public_semantic_contract_digest"}

def profile_result_row_fields(bundle_id: Any, *, include_semantic: bool) -> set[str]:
    return PROFILE_RESULT_ROW_FIELDS | ((SEALED_PUBLIC_SEMANTIC_FIELDS | (PUBLIC_SEMANTIC_RESULT_FIELDS if bundle_id != "decision_judgment" else set())) if include_semantic else set())

def profile_result_document_fields(bundle_id: Any, *, include_semantic: bool) -> set[str]:
    return PROFILE_RESULT_DOCUMENT_FIELDS | ((SEALED_PUBLIC_SEMANTIC_FIELDS | (PUBLIC_SEMANTIC_RESULT_FIELDS if bundle_id != "decision_judgment" else set())) if include_semantic else set())


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path: Path) -> str:
    """Digest exact committed content; never reinterpret a metadata-record hash as content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_revision() -> str | None:
    """Read the exact current Git revision without accepting caller metadata."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", revision) else None


def target_operation_digest(target_repository: str, target_path: str, target_revision: str, operation: str) -> str:
    """Bind the exact target and operation name into the signed operation digest."""
    return digest({
        "target_repository": target_repository,
        "target_path": target_path,
        "target_revision": target_revision,
        "operation": operation,
    })


def target_bound_blocks(
    value: dict[str, Any], *, allowed_command_classes: Any = None,
    forbidden_operation_classes: Any = None, require_current_revision: bool = True,
) -> list[str]:
    """Validate the reusable MK733J target-bound operation boundary."""
    if not isinstance(value, dict) or not TARGET_BOUND_FIELDS <= set(value):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_FIELDS_MISSING"]
    if any(
        not isinstance(value.get(key), str) or not value[key] or value[key].upper() == "UNKNOWN"
        for key in ("target_repository", "target_path", "target_revision", "operation", "operation_digest", "rollback")
    ) or not isinstance(value.get("exclusions"), list):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_FIELDS_INVALID"]
    target_repository = value["target_repository"]
    try:
        resolved_repository = Path(target_repository).resolve()
    except (OSError, RuntimeError):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_TARGET_MISMATCH"]
    if not Path(target_repository).is_absolute() or resolved_repository != REPO.resolve():
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_TARGET_MISMATCH"]
    target_path = value["target_path"]
    if target_path != ".":
        path = Path(target_path)
        resolved_path = (REPO / path).resolve()
        if (
            path.is_absolute() or str(path) != target_path or resolved_path == REPO.resolve()
            or REPO.resolve() not in resolved_path.parents
            or any(part.lower() in SECRET_PATH_PARTS for part in resolved_path.relative_to(REPO.resolve()).parts)
        ):
            return ["BLOCKED_FOR_MK733J_TARGET_BOUND_TARGET_MISMATCH"]
    if not re.fullmatch(r"[0-9a-f]{40}", value["target_revision"]):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_REVISION_MISMATCH"]
    if require_current_revision and current_revision() != value["target_revision"]:
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_REVISION_MISMATCH"]
    if value["rollback"] != "explicit_local_rollback":
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_ROLLBACK_INVALID"]
    if value["exclusions"] != sorted(TARGET_BOUND_EXCLUSIONS) or len(set(value["exclusions"])) != len(value["exclusions"]):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_EXCLUSIONS_INVALID"]
    if value["operation_digest"] != target_operation_digest(
        target_repository, target_path, value["target_revision"], value["operation"]
    ):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_OPERATION_DIGEST_INVALID"]
    if value["operation"] in TARGET_BOUND_EXCLUSIONS:
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_OPERATION_MISMATCH"]
    classes = allowed_command_classes
    if classes is None:
        classes = value.get("allowed_command_classes")
    if (
        not isinstance(classes, list)
        or any(not isinstance(item, str) or not item or item.upper() == "UNKNOWN" for item in classes)
        or classes != sorted(set(classes))
        or frozenset(classes) not in TARGET_BOUND_ALLOWED_CLASS_SETS
    ):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_CLASSES_INVALID"]
    forbidden = forbidden_operation_classes
    if forbidden is None:
        forbidden = value.get("forbidden_operation_classes")
    expected_forbidden = sorted(PROTECTED_OPERATION_CLASSES - set(classes))
    if not isinstance(forbidden, list) or any(not isinstance(item, str) or not item or item.upper() == "UNKNOWN" for item in forbidden) or forbidden != expected_forbidden:
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_CLASSES_INVALID"]
    if frozenset(classes) == frozenset({"credential", "destructive", "runtime_release"}):
        if target_path != ".":
            return ["BLOCKED_FOR_MK733J_TARGET_BOUND_TARGET_MISMATCH"]
        if value["operation"] != TARGET_BOUND_LOCAL_COMBINED_OPERATION:
            return ["BLOCKED_FOR_MK733J_TARGET_BOUND_OPERATION_MISMATCH"]
    return []


def target_bound_consumer_blocks(value: dict[str, Any], *, observed_paths: list[str], command_class: str, operation_digest: str) -> list[str]:
    blocks = target_bound_blocks(value)
    if blocks:
        return blocks
    if command_class not in value["allowed_command_classes"]:
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_CLASSES_INVALID"]
    target_path = value["target_path"]
    if not isinstance(observed_paths, list) or any(not isinstance(item, str) or not item for item in observed_paths):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_TARGET_MISMATCH"]
    normalized_paths = sorted(set(observed_paths))
    if (target_path == "." and normalized_paths and normalized_paths != ["."]) or (target_path != "." and normalized_paths != [target_path]):
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_TARGET_MISMATCH"]
    if not isinstance(operation_digest, str) or not operation_digest or operation_digest.upper() == "UNKNOWN":
        return ["BLOCKED_FOR_MK733J_TARGET_BOUND_OPERATION_MISMATCH"]
    return []


def target_bound_authority_active(value: dict[str, Any]) -> bool:
    classes = value.get("allowed_command_classes") if isinstance(value, dict) else None
    return isinstance(classes, list) and bool(set(classes) & PROTECTED_OPERATION_CLASSES)


def manifest_policy_view(value: Any) -> Any:
    """Return immutable operation authority, excluding consumption counters."""
    if isinstance(value, dict):
        return {key: manifest_policy_view(item) for key, item in value.items() if key != "remaining"}
    if isinstance(value, list):
        return [manifest_policy_view(item) for item in value]
    return value


def manifest_policy_digest(value: Any) -> str:
    return digest(manifest_policy_view(value))


def authority_gate_request_digest(request: dict[str, Any]) -> str:
    """Digest the authority-approved request without its self-referential ref/digest.

    The activation authority stores this digest.  The emitted receipt recomputes
    it from its own immutable operation scope, so resealing a changed receipt
    cannot expand the authority-approved operation.
    """
    if set(request) != AUTHORITY_GATE_REQUEST_FIELDS:
        raise ValueError("authority transition request schema is invalid")
    scoped = dict(request)
    scoped.pop("external_authority_ref")
    scoped.pop("external_authority_digest")
    return digest(scoped)


def authority_gate_scope_policy(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "work_id", "goal_ref", "task_class", "risk_class", "context_digest", "workpack_digest",
            "binding_record_digest", "external_protected_authority_state", "external_authority_ref",
            "external_authority_digest", "authority_request_digest", "allowed_tools", "allowed_path_prefixes",
            "allowed_command_classes", "forbidden_operation_classes", "operation_manifest_digest",
            "operation_manifest_policy_digest", "target_repository", "target_path", "target_revision",
            "operation", "operation_digest", "rollback", "exclusions",
        )
    }


def receipt_scope_policy(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "work_id", "goal_ref", "task_class", "risk_class", "selected_profile", "profile_digest", "qualification_digest",
        "qualification_results_digest", "preflight_digest", "preflight_scope_digest", "preflight_operation_manifest_digest",
        "context_digest", "workpack_digest", "binding_record_digest", "allowed_tools", "allowed_path_prefixes",
        "allowed_command_classes", "forbidden_operation_classes", "external_protected_authority_state", "external_authority_ref",
        "external_authority_digest", "operation_manifest_digest", "operation_manifest_policy_digest",
    )
    if target_bound_authority_active(value):
        keys += ("target_repository", "target_path", "target_revision", "operation", "operation_digest", "rollback", "exclusions")
    return {key: value.get(key) for key in keys}


def manifest_blocks(value: Any, *, phase: str, fresh: bool) -> list[str]:
    """Validate bounded, immutable authority plus mutable remaining counters."""
    if not isinstance(value, dict):
        return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
    rows: list[dict[str, Any]] = []
    if phase == "closeout":
        if set(value) != {"closeout"} or not isinstance(value.get("closeout"), dict):
            return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
        row = value["closeout"]
        if (
            set(row) != {"operation_digest", "allowed_count", "remaining"}
            or row.get("operation_digest") != CLOSEOUT_ACTION_DIGEST
        ):
            return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
        rows = [row]
    else:
        if set(value) != {"bash_commands", "mutation_classes", "read_only_diagnostics"}:
            return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
        bash_rows, mutation_rows, diagnostics = (
            value.get("bash_commands"), value.get("mutation_classes"), value.get("read_only_diagnostics")
        )
        if not isinstance(bash_rows, list) or not isinstance(mutation_rows, dict) or not isinstance(diagnostics, list):
            return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
        for row in bash_rows:
            if not isinstance(row, dict) or set(row) != {"operation_digest", "command_class", "allowed_count", "remaining"} or not isinstance(row.get("command_class"), str) or not row["command_class"]:
                return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
            rows.append(row)
        for command_class, row in mutation_rows.items():
            required = {"command_class", "exact_files", "path_prefixes", "max_changed_files", "max_bytes", "max_lines", "allowed_count", "remaining"}
            if (
                not isinstance(command_class, str)
                or not isinstance(row, dict)
                or set(row) != required
                or row.get("command_class") != command_class
                or not all(isinstance(row.get(key), int) and not isinstance(row.get(key), bool) and row[key] > 0 for key in ("max_changed_files", "max_bytes", "max_lines"))
                or not all(isinstance(row.get(key), list) and all(isinstance(item, str) and item for item in row[key]) for key in ("exact_files", "path_prefixes"))
            ):
                return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
            rows.append(row)
        for row in diagnostics:
            if not isinstance(row, dict) or set(row) != {"operation_digest", "command_class"} or not isinstance(row.get("command_class"), str) or not row["command_class"]:
                return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
            if row["command_class"] in PROTECTED_OPERATION_CLASSES:
                return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
            operation_digest = row.get("operation_digest")
            if not isinstance(operation_digest, str) or len(operation_digest) != 64 or any(char not in "0123456789abcdef" for char in operation_digest):
                return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
    for row in rows:
        if (
            not isinstance(row.get("operation_digest", CLOSEOUT_ACTION_DIGEST), str)
            or len(row.get("operation_digest", CLOSEOUT_ACTION_DIGEST)) != 64
            or any(char not in "0123456789abcdef" for char in row.get("operation_digest", CLOSEOUT_ACTION_DIGEST))
            or not isinstance(row.get("allowed_count"), int)
            or isinstance(row.get("allowed_count"), bool)
            or row["allowed_count"] <= 0
            or not isinstance(row.get("remaining"), int)
            or isinstance(row.get("remaining"), bool)
            or row["remaining"] < 0
            or row["remaining"] > row["allowed_count"]
            or (fresh and row["remaining"] != row["allowed_count"])
        ):
            return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
    return []


def authority_gate_manifest_blocks(value: Any, *, fresh: bool) -> list[str]:
    """Require exact mutation digests only for authority-only receipts.

    Profile receipts retain their existing manifest schema.  Authority-only
    mutations are a separate, more narrowly bounded contract and cannot use
    class/path/size alone as their operation identity.
    """
    if not isinstance(value, dict) or not isinstance(value.get("mutation_classes"), dict):
        return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
    projected = {
        **value,
        "mutation_classes": {
            key: ({name: item for name, item in row.items() if name != "operation_digest"} if isinstance(row, dict) else row)
            for key, row in value["mutation_classes"].items()
        },
    }
    if manifest_blocks(projected, phase="pre_work", fresh=fresh):
        return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
    for row in value["mutation_classes"].values():
        if (
            not isinstance(row, dict)
            or set(row) != {
                "command_class", "operation_digest", "exact_files", "path_prefixes", "max_changed_files",
                "max_bytes", "max_lines", "allowed_count", "remaining",
            }
            or not isinstance(row.get("operation_digest"), str)
            or len(row["operation_digest"]) != 64
            or any(char not in "0123456789abcdef" for char in row["operation_digest"])
        ):
            return ["BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID"]
    return []


def scope_path_blocks(prefixes: Any, manifest: Any) -> list[str]:
    """Reject absolute, escaping, protected, or non-canonical path authority."""
    values: list[Any] = list(prefixes) if isinstance(prefixes, list) else []
    if isinstance(manifest, dict):
        for row in manifest.get("mutation_classes", {}).values():
            if isinstance(row, dict):
                values.extend(row.get("exact_files", []))
                values.extend(row.get("path_prefixes", []))
    for value in values:
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            return ["BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE"]
        resolved = (REPO / value).resolve()
        if resolved == REPO or REPO not in resolved.parents:
            return ["BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE"]
        relative = resolved.relative_to(REPO)
        if str(relative) != value.rstrip("/") or any(part.lower() in SECRET_PATH_PARTS for part in relative.parts):
            return ["BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE"]
    return []


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def profiles() -> dict[str, dict[str, Any]]:
    return {row["profile_id"]: row for row in load(IMPLEMENTATION)["model_profiles"]}


def current_workpack_binding() -> dict[str, Any]:
    binding = load(IMPLEMENTATION).get("workpack_binding", {})
    required = {"fixed_commit", "workpack_ref", "workpack_digest", "binding_record_digest", "grand_goal_ref"}
    if not isinstance(binding, dict) or not required <= set(binding):
        raise ValueError("workpack binding is incomplete")
    ref = Path(binding["workpack_ref"])
    resolved = (REPO / ref).resolve() if not ref.is_absolute() else ref.resolve()
    if resolved != WORKPACK.resolve() or REPO not in resolved.parents or binding["workpack_digest"] != file_digest(resolved):
        raise ValueError("workpack content digest is invalid")
    body = dict(binding)
    supplied = body.pop("binding_record_digest", None)
    if supplied != digest(body) or supplied == binding["workpack_digest"]:
        raise ValueError("workpack binding record digest is invalid")
    registry_binding = load(CAPABILITY_BUNDLES).get("workpack_binding", {})
    expected_registry = {"workpack_ref": binding["workpack_ref"], "workpack_digest": binding["workpack_digest"], "binding_record_digest": supplied}
    if registry_binding != expected_registry:
        raise ValueError("capability registry workpack binding is invalid")
    return binding


def current_workpack_digest() -> str:
    """Return the canonical SHA-256 of the workpack file bytes."""
    return current_workpack_binding()["workpack_digest"]


def current_binding_record_digest() -> str:
    """Return the separately named digest of workpack binding metadata."""
    return current_workpack_binding()["binding_record_digest"]


def capability_registry_path(*, test_isolated: bool = False) -> Path:
    canonical=(REPO/"research/mk675/fable5_decision_os/mk733j_n_capability_bundles.json").resolve();selected=CAPABILITY_BUNDLES.resolve()
    if selected==canonical:return selected
    if not test_isolated or not selected.is_file() or selected==REPO.resolve() or REPO.resolve() in selected.parents:raise ValueError("noncanonical capability registry is test-isolated outside-repo only")
    return selected


def configure_test_capability_registry(value: str | None, *, test_isolated: bool) -> None:
    """Select an outside-repo registry only for an explicit isolated API call."""
    global CAPABILITY_BUNDLES
    if value is None:return
    path=Path(value).resolve()
    if not test_isolated or not Path(value).is_absolute() or not path.is_file() or path==REPO or REPO in path.parents:
        raise ValueError("test capability registry must be an explicit outside-repo file")
    CAPABILITY_BUNDLES=path


def profile_aliases(profile_id: str, *, test_isolated: bool = False) -> list[str]:
    aliases = load(capability_registry_path(test_isolated=test_isolated)).get("profile_model_identity_aliases", {}).get(profile_id, [])
    return aliases if isinstance(aliases, list) and all(isinstance(x, str) and x for x in aliases) else []


def required_bundle(profile_id: str, task_class: Any, *, test_isolated: bool = False) -> tuple[str | None, dict[str, Any] | None]:
    registry = load(capability_registry_path(test_isolated=test_isolated))
    value = registry.get("profile_bundle_requirements", {}).get(profile_id, {}).get(task_class)
    bundle_id = value[-1] if isinstance(value,list) and value else value
    return bundle_id, registry.get("bundles", {}).get(bundle_id) if bundle_id else None


def required_bundles(profile_id: str, task_class: Any, request: dict[str, Any], *, test_isolated: bool = False) -> list[str]:
    registry=load(capability_registry_path(test_isolated=test_isolated));value=registry.get("profile_bundle_requirements",{}).get(profile_id,{}).get(task_class,[])
    bundles=list(value) if isinstance(value,list) else ([value] if value else [])
    # Terra exploration is inventory-only. Routing/design recommendations use
    # the registered ambiguous-design Sol task class, never an implicit extra
    # prerequisite on the read-only explorer profile.
    return bundles


def _durable_qualification_ref(value: Any, *, test_isolated: bool, registry_path: Path) -> Path | None:
    if not isinstance(value,str) or not value:return None
    if test_isolated:
        root=registry_path.resolve().parent;path=Path(value);path=path.resolve() if path.is_absolute() else (root/path).resolve()
        return path if path.is_file() and REPO not in path.parents and path!=REPO and root in path.parents else None
    if Path(value).is_absolute():return None
    path=(REPO/value).resolve();root=DURABLE_QUALIFICATION_RESULTS.resolve()
    return path if path.is_file() and root in path.parents else None


def profile_bundle_result(profile: dict[str, Any], task_class: Any, request: dict[str, Any], *, test_isolated: bool = False, _production_like: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    try:registry_path=capability_registry_path(test_isolated=test_isolated);registry=load(registry_path);required=required_bundles(profile["profile_id"],task_class,request,test_isolated=test_isolated)
    except (OSError,ValueError,json.JSONDecodeError):return [],["BLOCKED_FOR_MK733J_CAPABILITY_REGISTRY_OVERRIDE_INVALID"]
    request_results=request.get("qualification_results",{})
    blocks: list[str] = []
    results=[]
    try:workpack_digest=current_workpack_digest();binding_record_digest=current_binding_record_digest()
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError):return [],["BLOCKED_FOR_MK733J_WORKPACK_BINDING_INVALID"]
    if not required:return [],["BLOCKED_FOR_MK733J_PROFILE_TASK_CLASS_RESULT_MISSING"]
    if not isinstance(request_results,dict) or set(request_results)!=set(required):
        blocks.append("BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_KEYSET_INVALID")
    for bundle_id in required:
        bundle=registry.get("bundles",{}).get(bundle_id);key=f"{profile['profile_id']}:{task_class}:{bundle_id}";result=registry.get("profile_results",{}).get(key);provided=request_results.get(bundle_id,{}) if isinstance(request_results,dict) else {}
        if (
            not isinstance(provided, dict)
            or set(provided) != {"result_ref", "qualification_digest"}
            or not all(isinstance(provided.get(field), str) and provided[field] for field in provided)
        ):
            blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_REQUEST_SCHEMA_INVALID:{bundle_id}")
            provided = {}
        if not bundle or not isinstance(result,dict):blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_MISSING:{bundle_id}");continue
        try:
            import mk733j_capability_bundles as capability
            contract=capability.evaluation_contract_digests(bundle_id)
        except (ImportError,AttributeError,TypeError,ValueError):
            contract={}
            blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_CONTRACT_INVALID:{bundle_id}")
        expires=parse_time(result.get("expires_at"));expected={"profile_id":profile["profile_id"],"profile_digest":digest(profile),"task_class":task_class,"bundle_id":bundle_id,"bundle_version":registry.get("bundle_registry_version"),"bundle_digest":digest(bundle),"workpack_digest":workpack_digest,"binding_record_digest":binding_record_digest,**contract}
        production_semantics=_production_like or not test_isolated
        semantic=capability.public_semantic_binding(bundle_id) if production_semantics and bundle_id!="decision_judgment" else {}
        if production_semantics and bundle_id!="decision_judgment" and semantic is None:
            blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_CONTRACT_INVALID:{bundle_id}")
            semantic={}
        if semantic:expected.update({"public_semantic_contract_ref":semantic["public_semantic_contract_ref"],"public_semantic_contract_digest":semantic["public_semantic_contract_digest"]})
        expected_state="empirically_qualified_current" if _production_like or not test_isolated else "test_only_empirically_qualified_current";expected_evidence="trusted_observable_and_sol_holdout" if _production_like or not test_isolated else "test_only_harness"
        if set(result)!=profile_result_row_fields(bundle_id,include_semantic=production_semantics) or any(result.get(k)!=v for k,v in expected.items()) or result.get("qualification_state")!=expected_state or result.get("evidence_class")!=expected_evidence or not expires or expires<=datetime.now(timezone.utc):blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_STALE:{bundle_id}")
        copy=dict(result);supplied=copy.pop("qualification_digest",None)
        if supplied!=digest(copy):blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_DIGEST_INVALID:{bundle_id}")
        if provided.get("result_ref")!=result.get("result_ref") or provided.get("qualification_digest")!=supplied:blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_REQUEST_MISMATCH:{bundle_id}")
        resolved=_durable_qualification_ref(result.get("result_ref"),test_isolated=test_isolated,registry_path=registry_path);identity_path=_durable_qualification_ref(result.get("identity_readback_ref"),test_isolated=test_isolated,registry_path=registry_path);holdout_path=_durable_qualification_ref(result.get("sealed_holdout_ref"),test_isolated=test_isolated,registry_path=registry_path)
        try:
            if not resolved or not identity_path or not holdout_path:raise OSError
            stored=load(resolved);identity=load(identity_path);holdout=load(holdout_path)
            continuity_fields=("profile_id","profile_digest","task_class","bundle_id","bundle_version","bundle_digest","workpack_digest","binding_record_digest","evaluation_corpus_digest","evaluation_schema_digest","evidence_class","identity_readback_ref","sealed_holdout_ref","identity_envelope_digest","sealed_holdout_envelope_digest","authority_id","holdout_authority_ref","holdout_authority_digest","authority_profile_result_digest","authority_identity_envelope_digest","runtime_model_identity","model","reasoning_effort","thread_run_id","qualified_at","expires_at") + (("sealed_public_semantic_contract_ref","sealed_public_semantic_contract_digest") + (("public_semantic_contract_ref","public_semantic_contract_digest") if bundle_id!="decision_judgment" else ()) if production_semantics else ())
            stored_body=dict(stored);stored_internal_digest=stored_body.pop("result_digest",None)
            identity_body=dict(identity);identity_digest=identity_body.pop("envelope_digest",None)
            holdout_body=dict(holdout);holdout_digest=holdout_body.pop("envelope_digest",None);holdout_result_digest=holdout_body.get("holdout_result_digest")
            identity_source="observable_identity_readback" if _production_like or not test_isolated else "test_only_observable_identity_readback";holdout_source="sol_owned_sealed_holdout" if _production_like or not test_isolated else "test_only_sol_owned_sealed_holdout"
            if (
                set(stored)!=profile_result_document_fields(bundle_id,include_semantic=production_semantics) or digest(stored)!=result.get("result_digest") or stored_internal_digest!=digest(stored_body)
                or any(stored.get(k)!=result.get(k) for k in continuity_fields)
                or stored.get("record_type")!="mk733j_profile_task_class_qualification_result"
                or identity.get("record_type")!="mk733j_sanitized_qualification_envelope" or identity.get("source_class")!=identity_source or identity_digest!=digest(identity_body) or identity_digest!=result.get("identity_envelope_digest")
                or holdout.get("record_type")!="mk733j_sanitized_qualification_envelope" or holdout.get("source_class")!=holdout_source or holdout_digest!=digest(holdout_body) or holdout_digest!=result.get("sealed_holdout_envelope_digest") or not isinstance(holdout_result_digest,str) or ((_production_like or not test_isolated) and holdout.get("source_holdout_result_digest")!=holdout_result_digest)
            ):raise OSError
            envelope_expected={"profile_id":profile["profile_id"],"profile_digest":digest(profile),"task_class":bundle.get("task_class"),"bundle_id":bundle_id,"bundle_digest":digest(bundle),"workpack_digest":workpack_digest,"binding_record_digest":binding_record_digest,"runtime_model_identity":result.get("runtime_model_identity"),"model":result.get("model"),"reasoning_effort":result.get("reasoning_effort"),"thread_run_id":result.get("thread_run_id")}
            if any(identity.get(k)!=v or holdout.get(k)!=v for k,v in envelope_expected.items()) or identity.get("output_digest")!=stored.get("public_output_digest") or holdout.get("public_output_digest")!=stored.get("public_output_digest") or holdout.get("public_metrics")!=stored.get("metrics"):raise OSError
            if _production_like or not test_isolated:
                import mk733j_capability_bundles as capability
                authority_request={"thread_run_id":result.get("thread_run_id"),"profile_id":profile["profile_id"],"task_class":bundle.get("task_class")}
                if not capability._admitted_external_sol_authority_valid(holdout,authority_request,registry_path,registry_path.resolve().parent if _production_like else REPO,test_isolated=_production_like):raise OSError
        except (OSError,TypeError,ValueError,ImportError,json.JSONDecodeError):blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_REF_INVALID:{bundle_id}")
        aliases=profile_aliases(profile["profile_id"],test_isolated=test_isolated)
        if result.get("runtime_model_identity") not in aliases or result.get("model") not in aliases or result.get("reasoning_effort")!=profile.get("reasoning_effort") or not isinstance(result.get("thread_run_id"),str) or not result["thread_run_id"]:blocks.append(f"BLOCKED_FOR_MK733J_PROFILE_PREREQUISITE_IDENTITY_INVALID:{bundle_id}")
        results.append(result)
    if request.get("qualification_state")!="current" or request.get("runtime_model_identity") not in profile_aliases(profile["profile_id"],test_isolated=test_isolated) or any(r.get("runtime_model_identity")!=request.get("runtime_model_identity") for r in results):blocks.append("BLOCKED_FOR_MK733J_PROFILE_TASK_CLASS_IDENTITY_MISMATCH")
    if results:
        primary = results[-1]
        if (
            request.get("qualification_result_ref") != primary.get("result_ref")
            or request.get("qualification_digest") != primary.get("qualification_digest")
            or request.get("qualification_expires_at") != primary.get("expires_at")
        ):
            blocks.append("BLOCKED_FOR_MK733J_PROFILE_PRIMARY_RESULT_MISMATCH")
    return results,sorted(set(blocks))


def safe_ref(value: Any, *, test_isolated: bool) -> Path | None:
    if not isinstance(value, str) or not value or value.startswith("fixture:") or value.startswith("self:"):
        return None
    path = Path(value)
    if not path.is_absolute():
        path = REPO / path
    path = path.resolve()
    if not path.is_file() or "fixtures" in path.parts:
        return None
    if not test_isolated and REPO not in path.parents:
        return None
    if test_isolated and REPO in path.parents:
        return None
    return path


def policy_tools(profile: dict[str, Any], task_class: Any) -> list[str]:
    if task_class == "bounded_implementation" and profile.get("mutation_permissions") == "repo_local_scoped_only_after_empirical_qualification":
        return ["Bash", "apply_patch", "Edit", "Write"]
    return []


def preflight_consumer_result(doc: dict[str, Any]) -> dict[str, Any]:
    """Consume the current preflight decision at the CMD/receipt boundary.

    ``mk_decision_preflight`` is the producer of the planning and Fable5
    authority decisions, but this module is the current planner/CMD consumer
    that admits a route and issues a receipt.  Keep the source record
    immutable: derived selections are recomputed and only exposed in the
    returned decision envelope.  Planning Claim Check blockers are consumed
    into a one-cycle reorder continuation; paid Fable5 blockers remain
    Authority Gate blockers.
    """
    if not isinstance(doc, dict):
        return {
            "blocks": ["BLOCKED_FOR_MK733J_PREFLIGHT_SCHEMA_INVALID"],
            "status": "FAIL_PREFLIGHT_BLOCKED",
            "planning_order_selection": None,
            "planning_order_continuation": None,
            "fable5_execution_authority_selection": None,
        }

    import mk_decision_preflight as preflight

    derived_fields = getattr(preflight, "DERIVED_PREFLIGHT_FIELDS", set())
    body = {key: value for key, value in doc.items() if key not in derived_fields}
    raw_blocks = preflight.check_bound_work_selection_record(body)
    blocks, planning_selection, planning_continuation = (
        preflight.consume_planning_order_selection(body, raw_blocks)
    )
    authority_selection = None
    if preflight.fable5_execution_authority_required(body):
        authority_selection = preflight.fable5_execution_authority_selection(
            body.get("fable5_execution_authorization"),
            required=True,
        )

    reorder_consumed = (
        not blocks
        and isinstance(planning_selection, dict)
        and planning_selection.get("decision") == "REORDER_PRIMARY_PLANNING_FIRST"
        and planning_continuation is not None
    )
    status = (
        "PASS_PREFLIGHT_REORDER_SELECTION_CONSUMED"
        if reorder_consumed
        else "PASS_PREFLIGHT_SUPPORT_EVIDENCE_ONLY"
        if not blocks
        else "FAIL_PREFLIGHT_BLOCKED"
    )
    expected_derived = {
        "planning_order_selection": planning_selection,
        "planning_order_continuation": planning_continuation,
        "fable5_execution_authority_selection": authority_selection,
        "fable5_execution_authority_continuation": (
            authority_selection.get("approval_transition")
            if isinstance(authority_selection, dict)
            else None
        ),
    }
    # A materialized producer result is accepted only if it agrees with the
    # current consumer calculation.  A missing derived field is allowed for a
    # source preflight record; the consumer supplies it in its result.
    for key, expected in expected_derived.items():
        if key in doc and doc.get(key) != expected:
            blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_DERIVED_RESULT_MISMATCH")

    deterministic = doc.get("deterministic_result", {})
    if deterministic != {
        "validator": "mk_decision_preflight",
        "status": status,
        "record_digest": preflight.record_digest(body),
        "decision": (
            planning_selection.get("decision")
            if isinstance(planning_selection, dict)
            else "NO_PLANNING_ORDER_SELECTION"
        ),
    }:
        blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_RESULT_INVALID")
    return {
        "blocks": sorted(set(blocks)),
        "status": status if not blocks else "FAIL_PREFLIGHT_BLOCKED",
        "planning_order_selection": planning_selection,
        "planning_order_continuation": planning_continuation,
        "fable5_execution_authority_selection": authority_selection,
        "fable5_execution_authority_continuation": expected_derived[
            "fable5_execution_authority_continuation"
        ],
        "original_dispatch_allowed": not reorder_consumed and not blocks,
        "read_only_planning_continues": (
            planning_selection.get("read_only_planning_continues", True)
            if isinstance(planning_selection, dict)
            else True
        ),
    }


def preflight_blocks(request: dict[str, Any], *, test_isolated: bool) -> list[str]:
    ref=request.get("preflight_ref"); expected_digest=request.get("preflight_digest")
    if not isinstance(ref,str) or not ref:return ["BLOCKED_FOR_MK733J_PREFLIGHT_MISSING"]
    path=Path(ref)
    if not path.is_absolute():path=(REPO/path).resolve()
    else:path=path.resolve()
    if (not test_isolated and (REPO not in path.parents or "fixtures" in path.parts)) or (test_isolated and REPO in path.parents):return ["BLOCKED_FOR_MK733J_PREFLIGHT_REF_INVALID"]
    try:doc=load(path)
    except (OSError,json.JSONDecodeError):return ["BLOCKED_FOR_MK733J_PREFLIGHT_REF_INVALID"]
    if digest(doc)!=expected_digest:return ["BLOCKED_FOR_MK733J_PREFLIGHT_DIGEST_INVALID"]
    try:
        consumer = preflight_consumer_result(doc)
        blocks = list(consumer["blocks"])
    except Exception:
        return ["BLOCKED_FOR_MK733J_PREFLIGHT_VALIDATOR_ERROR"]
    try:
        workpack_digest=current_workpack_digest();binding_record_digest=current_binding_record_digest()
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError):
        return ["BLOCKED_FOR_MK733J_WORKPACK_BINDING_INVALID"]
    expected={
        "preflight_contract_version": request.get("preflight_contract_version"),
        "work_id": request.get("work_id"),
        "goal_ref": request.get("goal_ref"),
        "task_class": request.get("task_class"),
        "risk_class": request.get("risk_class"),
        "context_digest": request.get("context_digest"),
        "workpack_digest": workpack_digest,
        "binding_record_digest": binding_record_digest,
        "preflight_scope_digest": request.get("preflight_scope_digest"),
        "operation_manifest_digest": request.get(
            "preflight_operation_manifest_digest",
            manifest_policy_digest(request.get("operation_manifest")),
        ),
    }
    if any(doc.get(k)!=v for k,v in expected.items()):blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_BINDING_MISMATCH")
    if request.get("phase") != "closeout":
        manifest=request.get("operation_manifest")
        try:
            authorized_count=sum(row["allowed_count"] for row in manifest["bash_commands"])+sum(
                row["allowed_count"] for row in manifest["mutation_classes"].values()
            )
        except (KeyError,TypeError):
            authorized_count=None
        declared_max=doc.get("declared_budget",{}).get("max_tool_calls")
        if (
            not isinstance(authorized_count,int)
            or isinstance(authorized_count,bool)
            or authorized_count<=0
            or not isinstance(declared_max,int)
            or isinstance(declared_max,bool)
            or authorized_count>declared_max
            or request.get("budget",{}).get("total")!=authorized_count
        ):
            blocks.append("BLOCKED_FOR_MK733J_PREFLIGHT_BUDGET_BINDING_INVALID")
    return sorted(set(blocks))


def external_authority_scope_digest(value: dict[str, Any]) -> str:
    return digest({
        key:value.get(key) for key in (
            "work_id","goal_ref","task_class","risk_class","selected_profile","profile_digest",
            "qualification_digest","preflight_digest","context_digest","workpack_digest","binding_record_digest",
            "allowed_command_classes","forbidden_operation_classes","operation_manifest_policy_digest",
            "target_repository","target_path","target_revision","operation","operation_digest","rollback","exclusions",
        )
    })


def external_authority_blocks(value: dict[str, Any], *, test_isolated: bool) -> list[str]:
    if value.get("external_protected_authority_state")=="absent":
        return [] if value.get("external_authority_ref") is None and value.get("external_authority_digest") is None else ["BLOCKED_FOR_MK733J_EXTERNAL_AUTHORITY_STATE_INVALID"]
    ref=value.get("external_authority_ref")
    if not isinstance(ref,str) or not ref:return ["BLOCKED_FOR_MK733J_EXTERNAL_AUTHORITY_MISSING"]
    path=Path(ref);path=path.resolve() if path.is_absolute() else (REPO/path).resolve()
    if (
        (not test_isolated and TRUSTED_AUTHORITY_DIR.resolve() not in path.parents)
        or (test_isolated and REPO in path.parents)
    ):
        return ["BLOCKED_FOR_MK733J_EXTERNAL_AUTHORITY_REF_INVALID"]
    try:doc=load(path)
    except (OSError,json.JSONDecodeError):return ["BLOCKED_FOR_MK733J_EXTERNAL_AUTHORITY_REF_INVALID"]
    copy=dict(doc);supplied=copy.pop("authority_digest",None)
    required = {
        "authority_type", "source_class", "issuer", "issuer_class", "authority_ref", "scope",
        "work_id", "goal_ref", "selected_profile", "profile_digest", "qualification_digest",
        "preflight_digest", "context_digest", "workpack_digest", "binding_record_digest",
        "scope_policy_digest", "allowed_operation_classes", "issued_at", "expires_at",
        "authority_digest", "target_repository", "target_path", "target_revision", "operation", "operation_digest",
        "rollback", "exclusions",
    }
    source_classes = {"test_isolated_authority"} if test_isolated else {"cmd_owner_authority", "user_explicit_authority"}
    issuer_classes = {"test_isolated"} if test_isolated else {"cmd_owner", "user_explicit"}
    expected = {
        key: value.get(key)
        for key in (
            "work_id", "goal_ref", "selected_profile", "profile_digest", "qualification_digest",
            "preflight_digest", "context_digest", "workpack_digest", "binding_record_digest",
        )
    }
    expected.update({key: value.get(key) for key in TARGET_BOUND_FIELDS})
    expected["scope_policy_digest"]=external_authority_scope_digest(value)
    allowed_protected = sorted(set(value.get("allowed_command_classes", [])) & PROTECTED_OPERATION_CLASSES)
    issued, expires = parse_time(doc.get("issued_at")), parse_time(doc.get("expires_at"))
    now = datetime.now(timezone.utc)
    target_failures = target_bound_blocks(value) if target_bound_authority_active(value) else []
    if (
        set(doc) != required
        or doc.get("authority_type") != "mk733j_external_protected_operation_authority"
        or doc.get("scope") != "exact_receipt_scope"
        or doc.get("source_class") not in source_classes
        or doc.get("issuer_class") not in issuer_classes
        or not isinstance(doc.get("issuer"), str) or not doc["issuer"]
        or not isinstance(doc.get("authority_ref"), str) or not doc["authority_ref"]
        or any(doc.get(key) != expected_value for key, expected_value in expected.items())
        or doc.get("allowed_operation_classes") != allowed_protected
        or not allowed_protected
        or target_failures
        or not issued or not expires or issued > now or expires <= now or issued >= expires
        or supplied != digest(copy)
        or supplied != value.get("external_authority_digest")
    ):
        return ["BLOCKED_FOR_MK733J_EXTERNAL_AUTHORITY_BINDING_INVALID"]
    return []


def receipt_tools(profile: dict[str, Any], task_class: Any, phase: Any) -> list[str]:
    """Return the exact phase-bound tool/action identities for a receipt."""
    if phase == "closeout":
        return [CLOSEOUT_ACTION_ID]
    return policy_tools(profile, task_class)


def envelope_digest_valid(value: dict[str, Any]) -> bool:
    body = dict(value)
    supplied = body.pop("envelope_digest", None)
    return isinstance(supplied, str) and supplied == digest(body)


def runtime_identity_blocks(value: dict[str, Any], profile: dict[str, Any] | None, *, test_isolated: bool) -> list[str]:
    """Validate the current work thread identity separately from evaluator identity."""
    if test_isolated:
        return []  # test-isolated binding_blocks validates its paired envelope.
    if not profile:
        return ["BLOCKED_FOR_MK733J_RUNTIME_IDENTITY_REF_INVALID"]
    path = safe_ref(value.get("runtime_identity_ref"), test_isolated=False)
    if not path:
        return ["BLOCKED_FOR_MK733J_RUNTIME_IDENTITY_REF_INVALID"]
    try:
        identity = load(path)
    except (OSError, json.JSONDecodeError):
        return ["BLOCKED_FOR_MK733J_RUNTIME_IDENTITY_REF_INVALID"]
    expected = {
        "profile_id": profile["profile_id"],
        "profile_digest": digest(profile),
        "runtime_model_identity": value.get("runtime_model_identity"),
        "model": value.get("runtime_model_identity"),
        "reasoning_effort": profile.get("reasoning_effort"),
        "thread_run_id": value.get("thread_run_id"),
        "workpack_digest": current_workpack_digest(),
        "binding_record_digest": current_binding_record_digest(),
    }
    observed, expires = parse_time(identity.get("observed_at")), parse_time(identity.get("expires_at"))
    now = datetime.now(timezone.utc)
    if (
        identity.get("source_class") != "observable_runtime_identity_readback"
        or identity.get("identity_state") != "verified"
        or value.get("runtime_identity_state") != "verified"
        or value.get("runtime_model_identity") not in profile_aliases(profile["profile_id"],test_isolated=test_isolated)
        or any(identity.get(key) != expected_value for key, expected_value in expected.items())
        or not all(isinstance(expected_value, str) and expected_value for expected_value in expected.values())
        or not observed or not expires or observed > now or expires <= now or observed >= expires
        or not envelope_digest_valid(identity)
    ):
        return ["BLOCKED_FOR_MK733J_RUNTIME_IDENTITY_REF_INVALID"]
    return []


def final_audit_identity_blocks(value: dict[str, Any], profile: dict[str, Any] | None, *, test_isolated: bool) -> list[str]:
    """Final audit requires two independently attested identities, not a caller boolean."""
    if not value.get("final_audit"):
        return []
    fields=("implementer_identity_ref","implementer_identity_digest","auditor_identity_ref","auditor_identity_digest","audit_request_ref","audit_request_digest","audit_head_sha","comparison_base_sha","audit_result_ref","audit_result_digest")
    if not all(isinstance(value.get(field),str) and value[field] for field in fields):
        return ["BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_MISSING"]
    if not all(re.fullmatch(r"[0-9a-f]{40}",value[key]) for key in ("audit_head_sha","comparison_base_sha")) or value["audit_head_sha"]==value["comparison_base_sha"]:
        return ["BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_ATTESTATION_INVALID"]
    if value["implementer_identity_ref"]==value["auditor_identity_ref"]:
        return ["BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_NOT_INDEPENDENT"]
    request_path=safe_ref(value["audit_request_ref"],test_isolated=test_isolated);result_path=safe_ref(value["audit_result_ref"],test_isolated=test_isolated)
    try: audit_request=load(request_path) if request_path else None;audit_result=load(result_path) if result_path else None
    except (OSError,json.JSONDecodeError): audit_request=audit_result=None
    request_body=dict(audit_request) if isinstance(audit_request,dict) else {};request_digest=request_body.pop("request_digest",None)
    result_body=dict(audit_result) if isinstance(audit_result,dict) else {};result_digest=result_body.pop("result_digest",None)
    request_keys={"record_type","audit_request_id","audited_head_sha","comparison_base_sha","requested_implementer_profile","requested_implementer_model","requested_implementer_reasoning_effort","requested_auditor_profile","requested_model","requested_reasoning_effort","requested_execution_environment","requested_grader_gold_access","request_digest"}
    result_keys={"record_type","audit_request_id","audit_request_digest","audited_head_sha","comparison_base_sha","implementer_profile","implementer_model","implementer_reasoning_effort","auditor_profile","runtime_model_identity","model","reasoning_effort","auditor_thread_id","verdict","result_digest"}
    if set(audit_request or {})!=request_keys or set(audit_result or {})!=result_keys or request_digest!=digest(request_body) or result_digest!=digest(result_body) or value["audit_request_digest"]!=request_digest or value["audit_result_digest"]!=result_digest or audit_request.get("requested_implementer_profile")!="terra_high_implementer" or audit_request.get("requested_implementer_model")!="gpt-5.6-terra" or audit_request.get("requested_implementer_reasoning_effort")!="high" or audit_result.get("implementer_profile")!="terra_high_implementer" or audit_result.get("implementer_model")!="gpt-5.6-terra" or audit_result.get("implementer_reasoning_effort")!="high" or audit_request.get("audited_head_sha")!=value["audit_head_sha"] or audit_request.get("comparison_base_sha")!=value["comparison_base_sha"] or audit_request.get("requested_auditor_profile")!="sol_independent_reviewer" or audit_request.get("requested_model")!="gpt-5.6-sol" or audit_request.get("requested_reasoning_effort")!="ultra" or audit_request.get("requested_execution_environment") not in {"isolated","projectless"} or audit_request.get("requested_grader_gold_access") is not False or audit_result.get("audit_request_id")!=audit_request.get("audit_request_id") or audit_result.get("audit_request_digest")!=request_digest or audit_result.get("audited_head_sha")!=value["audit_head_sha"] or audit_result.get("comparison_base_sha")!=value["comparison_base_sha"] or audit_result.get("auditor_profile")!="sol_independent_reviewer" or not (audit_result.get("runtime_model_identity")==audit_result.get("model")=="gpt-5.6-sol") or audit_result.get("reasoning_effort")!="ultra" or audit_result.get("verdict") not in {"PASS","BLOCKED","PARTIAL"}:
        return ["BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_ATTESTATION_INVALID"]
    docs=[]
    for ref,digest_key in ((value["implementer_identity_ref"],"implementer_identity_digest"),(value["auditor_identity_ref"],"auditor_identity_digest")):
        path=safe_ref(ref,test_isolated=test_isolated)
        try: doc=load(path) if path else None
        except (OSError,json.JSONDecodeError): doc=None
        identity_keys={"record_type","source_class","profile_id","profile_digest","runtime_model_identity","model","reasoning_effort","thread_run_id","execution_environment","grader_gold_access","observed_at","expires_at","source_attestation_ref","source_attestation_digest","envelope_digest"}
        observed,expiry=(parse_time(doc.get("observed_at")),parse_time(doc.get("expires_at"))) if isinstance(doc,dict) else (None,None);moment=datetime.now(timezone.utc)
        if not isinstance(doc,dict) or set(doc)!=identity_keys or doc.get("record_type")!="mk733j_provider_attested_session_identity" or doc.get("source_class")!="cmd_provider_attested_session_identity" or not isinstance(doc.get("source_attestation_ref"),str) or not isinstance(doc.get("source_attestation_digest"),str) or not observed or not expiry or observed>=expiry or observed>moment or expiry<=moment or not envelope_digest_valid(doc) or value[digest_key]!=digest(doc):
            return ["BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_ATTESTATION_INVALID"]
        attestation_path=safe_ref(doc["source_attestation_ref"],test_isolated=test_isolated)
        try: attestation=load(attestation_path) if attestation_path else None
        except (OSError,json.JSONDecodeError): attestation=None
        try:
            import mk733j_qualification as qualification
            anchors=load(qualification.TRUSTED_ATTESTATIONS)
            registry_keys={"record_type","registry_version","trusted_attestations","non_claims"}
            expected_version="test" if test_isolated else "mk733j-provider-attestation-v1"
            rows=anchors.get("trusted_attestations",{}) if isinstance(anchors,dict) else None
            registry_valid=isinstance(anchors,dict) and set(anchors)==registry_keys and anchors.get("record_type")=="mk733j_provider_attestation_trust_registry" and anchors.get("registry_version")==expected_version and isinstance(rows,dict) and all(isinstance(row,dict) and set(row)=={"attestation_digest","issuer_class","capability"} for row in rows.values())
            admitted=rows.get(attestation.get("authority_id")) if registry_valid and isinstance(attestation,dict) else None
        except (OSError,ImportError,KeyError,TypeError,json.JSONDecodeError): admitted=None
        attestation_body=dict(attestation) if isinstance(attestation,dict) else {};attestation_digest=attestation_body.pop("attestation_digest",None)
        required_attestation=("profile_id","profile_digest","runtime_model_identity","model","reasoning_effort","thread_run_id","execution_environment","grader_gold_access","observed_at","expires_at")
        attestation_keys={"record_type","source_class","authority_id","issuer_class","capability","audit_request_ref","audit_request_digest","audit_head_sha","comparison_base_sha","audit_result_ref","audit_result_digest",*required_attestation,"attestation_digest"}
        attested_observed,attested_expiry=parse_time(attestation.get("observed_at") if isinstance(attestation,dict) else None),parse_time(attestation.get("expires_at") if isinstance(attestation,dict) else None)
        if not isinstance(attestation,dict) or set(attestation)!=attestation_keys or attestation.get("record_type")!="mk733j_provider_session_attestation" or attestation.get("source_class")!="cmd_provider_session_attestation" or not attested_observed or not attested_expiry or attested_observed>=attested_expiry or attested_observed>moment or attested_expiry<=moment or attestation_digest!=digest(attestation_body) or doc.get("source_attestation_digest")!=attestation_digest or admitted!={"attestation_digest":attestation_digest,"issuer_class":attestation.get("issuer_class"),"capability":"final_audit_identity"} or any(attestation.get(key)!=doc.get(key) for key in required_attestation) or any(attestation.get(key)!=value.get(key) for key in ("audit_request_ref","audit_request_digest","audit_head_sha","comparison_base_sha","audit_result_ref","audit_result_digest")):
            return ["BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_ATTESTATION_INVALID"]
        docs.append(doc)
    implementer,auditor=docs
    implementer_profile=profiles().get(implementer.get("profile_id"));auditor_profile=profiles().get("sol_independent_reviewer")
    implementer_aliases=profile_aliases(implementer.get("profile_id"),test_isolated=test_isolated) if implementer_profile else []
    implementer_invalid=implementer.get("profile_id")!="terra_high_implementer" or not implementer_profile or implementer.get("profile_digest")!=digest(implementer_profile) or implementer.get("runtime_model_identity") not in implementer_aliases or implementer.get("model") not in implementer_aliases or not (implementer.get("runtime_model_identity")==implementer.get("model")) or implementer.get("reasoning_effort")!=implementer_profile.get("reasoning_effort") or implementer.get("execution_environment") not in {"isolated","projectless"} or implementer.get("grader_gold_access") is not False
    auditor_invalid=not auditor_profile or auditor.get("profile_digest")!=digest(auditor_profile) or not (auditor.get("runtime_model_identity")==auditor.get("model")=="gpt-5.6-sol") or auditor.get("reasoning_effort")!="ultra" or auditor.get("execution_environment")!=audit_request.get("requested_execution_environment") or auditor.get("grader_gold_access") is not False
    if implementer_invalid or auditor_invalid or implementer.get("thread_run_id")==auditor.get("thread_run_id") or auditor.get("thread_run_id")!=value.get("thread_run_id") or auditor.get("runtime_model_identity")!=value.get("runtime_model_identity") or auditor.get("profile_id")!="sol_independent_reviewer" or (profile and auditor.get("profile_id")!=profile.get("profile_id")) or audit_result.get("auditor_thread_id")!=auditor.get("thread_run_id"):
        return ["BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_NOT_INDEPENDENT"]
    return []


def binding_blocks(value: dict[str, Any], profile: dict[str, Any] | None, *, test_isolated: bool) -> list[str]:
    """Validate observable identity and qualification envelopes, never bare refs."""
    if not profile:
        return ["BLOCKED_FOR_MK733J_RECEIPT_PROFILE_STALE"]
    if not test_isolated:
        _, blocks=profile_bundle_result(profile,value.get("task_class"),value)
        return blocks
    identity_path = safe_ref(value.get("runtime_identity_ref"), test_isolated=test_isolated)
    if not identity_path:
        return ["BLOCKED_FOR_MK733J_RECEIPT_BINDING_REF_INVALID"]
    try:
        identity = load(identity_path)
    except (OSError, json.JSONDecodeError):
        return ["BLOCKED_FOR_MK733J_RECEIPT_BINDING_REF_INVALID"]
    if identity.get("source_class") != "test_isolated_contract":
        return ["BLOCKED_FOR_MK733J_RECEIPT_BINDING_REF_INVALID"]
    aliases = profile_aliases(profile["profile_id"],test_isolated=test_isolated)
    expected = {
        "profile_id": value.get("selected_profile"), "profile_digest": value.get("profile_digest"),
        "runtime_model_identity": value.get("runtime_model_identity"), "model": value.get("runtime_model_identity"),
        "reasoning_effort": profile.get("reasoning_effort"), "thread_run_id": value.get("thread_run_id"),
        "workpack_digest": value.get("workpack_digest"), "binding_record_digest": value.get("binding_record_digest"),
    }
    if (
        value.get("runtime_model_identity") not in aliases
        or not all(isinstance(expected[k], str) and expected[k] for k in expected)
        or any(identity.get(k) != v for k, v in expected.items())
        or identity.get("identity_state") != "verified"
        or identity.get("qualification_result_ref") != value.get("qualification_result_ref")
        or not isinstance(identity.get("observed_at"), str)
        or not envelope_digest_valid(identity)
    ):
        return ["BLOCKED_FOR_MK733J_RECEIPT_BINDING_REF_INVALID"]
    base_profile=profiles().get(value.get("selected_profile"))
    _,composite_blocks=profile_bundle_result(base_profile,value.get("task_class"),value,test_isolated=True) if base_profile else ([],["BLOCKED_FOR_MK733J_RECEIPT_PROFILE_STALE"])
    return composite_blocks


def _sdo_payload(request: dict[str, Any]) -> dict[str, Any]:
    payload = request.get("sdo_route") if isinstance(request, dict) else None
    return payload if isinstance(payload, dict) else {}


def _sdo_candidate_ids(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    raw = value.get("candidate_actions")
    if raw is None:
        raw = value.get("ranked_actions", value.get("candidates"))
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        action_id = item if isinstance(item, str) else item.get("action_id") if isinstance(item, dict) else None
        if isinstance(action_id, str) and action_id and action_id not in result:
            result.append(action_id)
    return result


def _sdo_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _sdo_canonical_brain_digest() -> str | None:
    try:
        return file_digest(REPO / "brain/pages/sdo-synaptic-decision-os.md")
    except OSError:
        return None


def _sdo_layer_provenance(value: dict[str, Any], layer: str) -> str | None:
    if value.get("layer") not in (None, layer) or value.get("source_layer") != layer:
        return "BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"
    if value.get("record_type") not in SDO_LAYER_RECORD_TYPES[layer]:
        return "BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"
    if layer == "brain":
        if (
            not isinstance(value.get("source_ref"), str)
            or value["source_ref"] != SDO_BRAIN_SOURCE_REF
            or value.get("source_digest") != _sdo_canonical_brain_digest()
        ):
            return "BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"
    elif layer == "pms":
        binding = value.get("accepted_record_binding")
        if (
            not isinstance(binding, dict)
            or set(binding) != {"record_ref", "record_digest", "provenance_digest"}
            or not isinstance(binding.get("record_ref"), str)
            or not binding["record_ref"]
            or not binding["record_ref"].startswith(SDO_PMS_RECORD_PREFIXES)
            or not _sdo_sha256(binding.get("record_digest"))
            or binding.get("provenance_digest") != digest({
                "record_ref": binding.get("record_ref"),
                "record_digest": binding.get("record_digest"),
            })
        ):
            return "BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"
    elif layer == "odg":
        if (
            value.get("status") != "advisory"
            or value.get("record_status") != "advisory"
            or not isinstance(value.get("source_ref"), str)
            or not value["source_ref"].startswith(SDO_ODG_RECORD_PREFIXES)
            or not _sdo_sha256(value.get("source_digest"))
        ):
            return "BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"
    elif layer == "fable":
        if (
            value.get("status", value.get("disposition")) != "admitted"
            or not isinstance(value.get("source_ref"), str)
            or not value["source_ref"]
            or not _sdo_sha256(value.get("source_digest"))
        ):
            return "BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"
    return None


def _sdo_layer_value(payload: dict[str, Any], layer: str) -> tuple[dict[str, Any] | None, str | None]:
    if layer not in payload or payload.get(layer) is None:
        return None, f"SDO_{layer.upper()}_UNAVAILABLE"
    value = payload.get(layer)
    if not isinstance(value, dict):
        return None, f"BLOCKED_FOR_SDO_{layer.upper()}_LAYER_INVALID"
    if value.get("available") is False:
        return None, f"SDO_{layer.upper()}_UNAVAILABLE"
    return value, _sdo_layer_provenance(value, layer)


def _sdo_projection(value: dict[str, Any]) -> dict[str, Any]:
    projection = value.get("projection")
    return projection if isinstance(projection, dict) else value


def _sdo_odg_structure_blocks(value: Any) -> list[str]:
    blocks: set[str] = set()

    def walk(node: Any, *, candidate_item: bool = False) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, candidate_item=candidate_item)
            return
        if not isinstance(node, dict):
            return
        allowed = SDO_ODG_CANDIDATE_FIELDS if candidate_item else SDO_ODG_FIELDS
        for key, child in node.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if (
                key in SDO_ODG_FORBIDDEN_KEYS
                or "authority" in normalized
                or "authoriz" in normalized
                or "permission" in normalized
                or "operation" in normalized
                or "grant" in normalized
            ):
                blocks.add("BLOCKED_FOR_SDO_ODG_AUTHORITY_GRANT")
            if (
                normalized in {
                    "action", "new", "newaction", "newactionid", "create", "createaction",
                    "introduce", "introduceaction", "addaction", "appendaction", "actioncreate",
                }
                or normalized.startswith(("newaction", "createaction", "introduceaction", "addaction"))
            ):
                blocks.add("BLOCKED_FOR_SDO_ODG_NEW_ACTION")
            if key not in allowed and key not in SDO_ODG_FORBIDDEN_KEYS:
                blocks.add("BLOCKED_FOR_SDO_ODG_SCHEMA_INVALID")
            if key in {"candidate_actions", "ranked_actions", "candidates"}:
                if not isinstance(child, list):
                    blocks.add("BLOCKED_FOR_SDO_ODG_SCHEMA_INVALID")
                else:
                    for item in child:
                        if isinstance(item, dict):
                            walk(item, candidate_item=True)
                        elif not isinstance(item, str):
                            blocks.add("BLOCKED_FOR_SDO_ODG_SCHEMA_INVALID")
            elif isinstance(child, (dict, list)):
                walk(child)

    walk(value)
    return sorted(blocks)


def _sdo_normalized_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _sdo_trusted_base_dir(payload: dict[str, Any]) -> tuple[Path | None, list[str]]:
    raw = payload.get("base_dir")
    if raw is None:
        return REPO.resolve(), []
    if not isinstance(raw, str) or not raw:
        return None, ["BLOCKED_FOR_SDO_REPOSITORY_STATE_PROVENANCE_INVALID"]
    path = Path(raw)
    root = (REPO / path).resolve() if not path.is_absolute() else path.resolve()
    if root != REPO.resolve():
        return None, ["BLOCKED_FOR_SDO_REPOSITORY_STATE_PROVENANCE_INVALID"]
    return root, []


def _sdo_trusted_repository_state(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    root, blocks = _sdo_trusted_base_dir(payload)
    if root is None:
        return {"valid": False, "base_dir": None}, blocks

    documents: dict[str, dict[str, Any]] = {}
    for name, relative in SDO_TRUSTED_REPOSITORY_FILES.items():
        path = (root / relative).resolve()
        try:
            document = load(path)
            file_hash = file_digest(path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            blocks.append("BLOCKED_FOR_SDO_REPOSITORY_STATE_PROVENANCE_INVALID")
            continue
        if not isinstance(document, dict):
            blocks.append("BLOCKED_FOR_SDO_REPOSITORY_STATE_PROVENANCE_INVALID")
            continue
        documents[name] = {
            "path": path,
            "document": document,
            "file_digest": file_hash,
            "record_digest": digest(document),
        }

    routing = documents.get("routing_table", {}).get("document")
    policy = documents.get("active_policy", {}).get("document")
    activation = documents.get("activation_state", {}).get("document")
    authority_registry = documents.get("authority_registry", {}).get("document")
    compatibility = routing.get("compatibility") if isinstance(routing, dict) else None
    protected_repository_classes = (
        compatibility.get("protected_repository_classes")
        if isinstance(compatibility, dict)
        else None
    )
    protected_task_classes = (
        compatibility.get("protected_task_classes")
        if isinstance(compatibility, dict)
        else None
    )
    authority_gates = policy.get("authority_gates") if isinstance(policy, dict) else None
    trusted_authorities = (
        authority_registry.get("trusted_authorities")
        if isinstance(authority_registry, dict)
        else None
    )
    if (
        not isinstance(routing, dict)
        or routing.get("schema_version") != "maestro-kernel.routing-table/mk749/v1"
        or not isinstance(protected_repository_classes, list)
        or not all(isinstance(item, str) and item for item in protected_repository_classes)
        or not isinstance(protected_task_classes, list)
        or not all(isinstance(item, str) and item for item in protected_task_classes)
        or not isinstance(policy, dict)
        or policy.get("schema_version") != "control_classification_v2"
        or not isinstance(authority_gates, list)
        or not all(isinstance(item, str) and item for item in authority_gates)
        or not isinstance(activation, dict)
        or activation.get("record_type") != "mk733j_n_hook_activation_state"
        or not isinstance(activation.get("mode"), str)
        or not isinstance(activation.get("enforcement_active"), bool)
        or not isinstance(authority_registry, dict)
        or authority_registry.get("record_type") != "mk733j_n_trusted_activation_authority_registry"
        or authority_registry.get("source_class") != "committed_trust_registry"
        or not isinstance(trusted_authorities, list)
        or not all(isinstance(item, dict) for item in trusted_authorities)
    ):
        blocks.append("BLOCKED_FOR_SDO_REPOSITORY_STATE_PROVENANCE_INVALID")

    protected_operation_tokens: set[str] = set()
    for gate in authority_gates if isinstance(authority_gates, list) else []:
        normalized = _sdo_normalized_token(gate)
        protected_operation_tokens.update(
            token for token in SDO_PROTECTED_POLICY_TOKENS if token in normalized
        )
    for operation_class in PROTECTED_OPERATION_CLASSES:
        protected_operation_tokens.update(
            token
            for token in SDO_PROTECTED_POLICY_TOKENS
            if token in _sdo_normalized_token(operation_class)
        )
    authority_action_ids = {
        item.get("action_id")
        for item in (trusted_authorities if isinstance(trusted_authorities, list) else [])
        if isinstance(item, dict)
        if isinstance(item.get("action_id"), str) and item.get("action_id")
    }
    files_by_path = {
        item["path"]: item
        for item in documents.values()
        if isinstance(item.get("path"), Path)
    }
    return {
        "valid": not blocks,
        "base_dir": root,
        "files": documents,
        "files_by_path": files_by_path,
        "protected_repository_classes": set(protected_repository_classes or []),
        "protected_task_classes": set(protected_task_classes or []),
        "protected_operation_tokens": protected_operation_tokens,
        "authority_action_ids": authority_action_ids,
        "activation_state": activation if isinstance(activation, dict) else {},
    }, sorted(set(blocks))


def _sdo_repository_reference_path(value: Any, state: dict[str, Any]) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    reference = value
    if reference.startswith("repo://maestro-kernel/"):
        reference = reference[len("repo://maestro-kernel/") :]
    elif reference.startswith("repo:"):
        reference = reference[len("repo:") :].lstrip("/")
    elif "://" in reference:
        return None
    root = state.get("base_dir")
    if not isinstance(root, Path):
        return None
    path = Path(reference)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if path != root and root not in path.parents:
        return None
    return path if path.is_file() else None


def _sdo_facts_reference_valid(facts: dict[str, Any], state: dict[str, Any]) -> bool:
    for reference_key, digest_key in SDO_FACTS_REFERENCE_PAIRS:
        if reference_key not in facts and digest_key not in facts:
            continue
        if not isinstance(facts.get(reference_key), str) or not _sdo_sha256(facts.get(digest_key)):
            continue
        path = _sdo_repository_reference_path(facts[reference_key], state)
        record = state.get("files_by_path", {}).get(path)
        if not isinstance(record, dict):
            continue
        if facts[digest_key] in {record.get("file_digest"), record.get("record_digest")}:
            return True
    return False


def _sdo_facts_provenance_blocks(facts: Any, state: dict[str, Any]) -> list[str]:
    if not isinstance(facts, dict):
        return ["BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_MISSING"]
    if any(key in facts for key in SDO_FACTS_PROVENANCE_KEYS):
        return ["BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"]
    knowledge_keys = {_sdo_normalized_token(key) for key in SDO_FACTS_KNOWLEDGE_KEYS}
    knowledge_present = False

    def walk(node: Any) -> None:
        nonlocal knowledge_present
        if knowledge_present:
            return
        if isinstance(node, dict):
            for key, child in node.items():
                if _sdo_normalized_token(key) in knowledge_keys:
                    knowledge_present = True
                    return
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(facts)
    has_reference = _sdo_facts_reference_valid(facts, state)
    has_reference_fields = any(
        key in facts for pair in SDO_FACTS_REFERENCE_PAIRS for key in pair
    )
    if (knowledge_present or has_reference_fields) and not has_reference:
        return ["BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID"]
    return []


def _sdo_trusted_action_protected(
    candidate: dict[str, Any], action_id: str, state: dict[str, Any]
) -> bool | None:
    if not state.get("valid"):
        return None
    repository_class = candidate.get("repository_class")
    task_class = candidate.get("task_class")
    operation_class = candidate.get("operation_class")
    if repository_class in state.get("protected_repository_classes", set()):
        return True
    if task_class in state.get("protected_task_classes", set()):
        return True
    if operation_class in PROTECTED_OPERATION_CLASSES:
        return True
    if action_id in state.get("authority_action_ids", set()):
        return True
    normalized_action = _sdo_normalized_token(action_id)
    return any(
        token in normalized_action
        for token in state.get("protected_operation_tokens", set())
    )


def _sdo_trusted_action_facts(
    payload: dict[str, Any], candidates: dict[str, dict[str, Any]], state: dict[str, Any]
) -> tuple[dict[str, bool], list[str]]:
    facts = payload.get("facts")
    rows = facts.get("candidate_actions") if isinstance(facts, dict) else None
    blocks: set[str] = set(_sdo_facts_provenance_blocks(facts, state))
    classification: dict[str, bool] = {}
    if not isinstance(rows, list) or not state.get("valid"):
        blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_MISSING")
        return {}, sorted(blocks)

    claims: dict[str, bool] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("action_id"), str)
            or not isinstance(row.get("protected"), bool)
        ):
            blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_INVALID")
            continue
        if row["action_id"] in claims:
            blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_DUPLICATE")
            continue
        claims[row["action_id"]] = row["protected"]

    for action_id, candidate in candidates.items():
        protected = _sdo_trusted_action_protected(candidate, action_id, state)
        if protected is None:
            continue
        classification[action_id] = protected
        candidate_claim = candidate.get("protected")
        if "protected" in candidate:
            if not isinstance(candidate_claim, bool):
                blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_INVALID")
            elif candidate_claim != protected:
                blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_MISMATCH")

    for action_id, claim in claims.items():
        if action_id not in candidates:
            blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_UNKNOWN_ACTION")
            continue
        if action_id not in classification:
            continue
        if claim != classification[action_id]:
            blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_MISMATCH")
    if any(action_id not in claims for action_id in candidates):
        blocks.add("BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_MISSING")
        return {}, sorted(blocks)
    return classification, sorted(blocks)


def _sdo_trusted_authority_path(value: Any, base_dir: Path = REPO) -> Path | None:
    if not isinstance(value, str) or not value or value.startswith(("fixture:", "self:")):
        return None
    path = Path(value)
    path = (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
    trusted_root = (base_dir / TRUSTED_AUTHORITY_DIR.relative_to(REPO)).resolve()
    if trusted_root not in path.parents or not path.is_file():
        return None
    return path


def _sdo_authority_result_valid(
    row: Any, action_id: str, payload: dict[str, Any], base_dir: Path = REPO
) -> bool:
    target = payload.get("target_binding")
    if (
        not isinstance(row, dict)
        or set(row) != SDO_AUTHORITY_RESULT_FIELDS
        or row.get("action_id") != action_id
        or row.get("current") is not True
        or row.get("status") not in {"authorized", "approved"}
        or not isinstance(row.get("authority_ref"), str)
        or not row["authority_ref"]
        or not _sdo_sha256(row.get("authority_digest"))
        or not isinstance(target, dict)
        or set(target) != SDO_TARGET_BINDING_FIELDS
        or not isinstance(target.get("target_ref"), str)
        or not target["target_ref"]
        or not isinstance(target.get("operation"), str)
        or not target["operation"]
        or not isinstance(target.get("scope"), dict)
        or row.get("scope") != target["scope"]
        or row.get("operation_binding") != {
            "action_id": action_id,
            "target_ref": target["target_ref"],
            "operation": target["operation"],
            "scope": target["scope"],
        }
    ):
        return False
    validator = row.get("validator_result")
    if not (
        isinstance(validator, dict)
        and set(validator) == SDO_VALIDATOR_RESULT_FIELDS
        and validator.get("validator_id") == "sdo-authority-current.v1"
        and validator.get("status") == "PASS"
        and isinstance(validator.get("result_ref"), str)
        and bool(validator["result_ref"])
        and _sdo_sha256(validator.get("result_digest"))
        and validator.get("authority_digest") == row["authority_digest"]
        and validator.get("target_binding_digest") == digest(target)
    ):
        return False
    authority_path = _sdo_trusted_authority_path(row["authority_ref"], base_dir)
    validator_path = _sdo_trusted_authority_path(validator["result_ref"], base_dir)
    if authority_path is None or validator_path is None:
        return False
    try:
        authority_record = load(authority_path)
        validator_record = load(validator_path)
        authority_digest = file_digest(authority_path)
        validator_digest = file_digest(validator_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        authority_digest == row["authority_digest"]
        and validator_digest == validator["result_digest"]
        and isinstance(authority_record, dict)
        and set(authority_record) == {
            "record_type", "action_id", "current", "status", "target_binding",
            "scope", "operation_binding",
        }
        and authority_record["record_type"] == "sdo_current_authority.v1"
        and authority_record["action_id"] == action_id
        and authority_record["current"] is True
        and authority_record["status"] == "CURRENT"
        and authority_record["target_binding"] == target
        and authority_record["scope"] == target["scope"]
        and authority_record["operation_binding"] == row["operation_binding"]
        and isinstance(validator_record, dict)
        and set(validator_record) == {
            "record_type", "validator_id", "status", "authority_digest",
            "target_binding_digest",
        }
        and validator_record["record_type"] == "sdo_authority_validator_result.v1"
        and validator_record["validator_id"] == "sdo-authority-current.v1"
        and validator_record["status"] == "PASS"
        and validator_record["authority_digest"] == row["authority_digest"]
        and validator_record["target_binding_digest"] == digest(target)
    )


def _sdo_ranking_projection(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key)
        for key in ("route", "selected_action", "candidate_ranking", "ranking_source", "action_routes")
    }


def sdo_route(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the SDO selector without allowing advisory layers to add power.

    This is deliberately a route-engine helper rather than a second service or
    persistence surface.  ``repository_candidates`` is the only source of
    action identity and enumeration; trusted repository state classifies those
    identities.  Facts constrain that classification but never rank it, while
    admissible lower layers can reorder only existing, allowed identities.
    """
    payload = _sdo_payload(request)
    blockers: list[str] = []
    unavailable: list[str] = []
    repository_candidates = payload.get("repository_candidates")
    if not isinstance(repository_candidates, list) or not repository_candidates:
        return {
            "route": "stop_or_escalate",
            "selected_action": None,
            "candidate_ranking": [],
            "ranking_source": None,
            "action_routes": {},
            "blockers": ["BLOCKED_FOR_SDO_REPOSITORY_CANDIDATES_MISSING"],
            "typed_unavailable": [],
            "non_claims": ["no_advisory_authority", "no_runtime_readiness", "no_user_acceptance"],
        }

    candidates: dict[str, dict[str, Any]] = {}
    candidate_order: list[str] = []
    for item in repository_candidates:
        if not isinstance(item, dict) or not isinstance(item.get("action_id"), str) or not item["action_id"]:
            blockers.append("BLOCKED_FOR_SDO_REPOSITORY_CANDIDATE_INVALID")
            continue
        action_id = item["action_id"]
        if action_id in candidates:
            blockers.append("BLOCKED_FOR_SDO_REPOSITORY_CANDIDATE_DUPLICATE")
            continue
        candidates[action_id] = dict(item)
        candidate_order.append(action_id)
    if not candidates:
        blockers.append("BLOCKED_FOR_SDO_REPOSITORY_CANDIDATES_MISSING")

    trusted_state, trusted_state_blocks = _sdo_trusted_repository_state(payload)
    blockers.extend(trusted_state_blocks)

    layer_values: dict[str, dict[str, Any]] = {}
    for layer in SDO_LOWER_LAYERS:
        value, marker = _sdo_layer_value(payload, layer)
        if layer == "odg" and isinstance(value, dict):
            blockers.extend(_sdo_odg_structure_blocks(value))
        if marker is not None:
            (unavailable if marker.startswith("SDO_") else blockers).append(marker)
        elif value is not None:
            layer_values[layer] = value

    pms = layer_values.get("pms")
    if pms is not None and (pms.get("status") != "accepted" or pms.get("review_status") != "accepted"):
        blockers.append("BLOCKED_FOR_SDO_PMS_NOT_ACCEPTED")

    odg_source = layer_values.get("odg")
    odg = _sdo_projection(odg_source) if odg_source is not None else None
    if odg is not None:
        odg_ids = _sdo_candidate_ids(odg)
        if any(action_id not in candidates for action_id in odg_ids):
            blockers.append("BLOCKED_FOR_SDO_ODG_NEW_ACTION")

    fable = layer_values.get("fable")
    if fable is not None and fable.get("status", fable.get("disposition")) != "admitted":
        blockers.append("BLOCKED_FOR_SDO_FABLE_NOT_ADMITTED")

    trusted_classification, classification_blocks = _sdo_trusted_action_facts(
        payload, candidates, trusted_state
    )
    blockers.extend(classification_blocks)
    rankings: list[tuple[str, list[str]]] = []
    for layer in ("brain", "pms", "odg", "fable"):
        value = layer_values.get(layer)
        if value is None:
            continue
        if layer == "pms" and (value.get("status") != "accepted" or value.get("review_status") != "accepted"):
            continue
        if layer == "odg":
            projection = _sdo_projection(value)
            ids = _sdo_candidate_ids(projection)
            if any(action_id not in candidates for action_id in ids) or _sdo_odg_structure_blocks(value):
                continue
            rankings.append((layer, ids))
            continue
        if layer == "fable" and value.get("status", value.get("disposition")) != "admitted":
            continue
        rankings.append((layer, _sdo_candidate_ids(value)))

    ranking_source = "repository"
    ranked_ids = candidate_order[:]
    for source, proposed in rankings:
        valid = [action_id for action_id in proposed if action_id in candidates]
        if valid:
            ranking_source = source
            ranked_ids = valid + [action_id for action_id in candidate_order if action_id not in valid]
            break

    authority_results = payload.get("authority_results", [])
    if not isinstance(authority_results, list):
        authority_results = []
        blockers.append("BLOCKED_FOR_SDO_AUTHORITY_RESULTS_INVALID")
    exact_authorized = {
        action_id
        for action_id in candidate_order
        if trusted_classification.get(action_id) is True
        and any(
            _sdo_authority_result_valid(
                row,
                action_id,
                payload,
                trusted_state.get("base_dir") or REPO,
            )
            for row in authority_results
        )
    }
    action_routes: dict[str, dict[str, Any]] = {}
    for action_id in ranked_ids:
        action_blocks: list[str] = []
        protected = trusted_classification.get(action_id)
        if protected is None:
            action_blocks.append("BLOCKED_FOR_SDO_REPOSITORY_ACTION_CLASSIFICATION_MISSING")
        elif protected and action_id not in exact_authorized:
            action_blocks.append("BLOCKED_FOR_SDO_PROTECTED_ACTION_AUTHORITY_BINDING_INVALID")
        if action_blocks:
            blockers.extend(action_blocks)
        action_routes[action_id] = {
            "route": "deny" if action_blocks else "allow",
            "blockers": action_blocks,
            "protected": protected,
        }

    selected_action = next(
        (action_id for action_id in ranked_ids if action_routes[action_id]["route"] == "allow"),
        None,
    )
    return {
        "route": "allow" if selected_action is not None else "stop_or_escalate",
        "selected_action": selected_action,
        "candidate_ranking": ranked_ids,
        "ranking_source": ranking_source,
        "action_routes": action_routes,
        "blockers": sorted(set(blockers)),
        "typed_unavailable": sorted(set(unavailable)),
        "non_claims": ["no_advisory_authority", "no_runtime_readiness", "no_user_acceptance"],
    }


def sdo_route_fixture(path: Path, *, expect_fail: bool) -> tuple[dict[str, Any], int]:
    document = load(path)
    request = document.get("request") if isinstance(document, dict) else None
    expected = document.get("expected", {}) if isinstance(document, dict) else {}
    actual = sdo_route(request if isinstance(request, dict) else document)
    expected_blocks = sorted(expected.get("blocks", []))
    expected_unavailable = sorted(expected.get("typed_unavailable", []))
    errors: list[str] = []
    actual_blocks = sorted(actual.get("blockers", []))
    if actual_blocks != expected_blocks:
        errors.append("BLOCKED_FOR_SDO_ROUTE_FIXTURE_BLOCKS_MISMATCH")
    if actual.get("typed_unavailable") != expected_unavailable:
        errors.append("BLOCKED_FOR_SDO_ROUTE_FIXTURE_UNAVAILABLE_MISMATCH")
    for key in ("route", "selected_action", "ranking_source"):
        if expected.get(key) != actual.get(key):
            errors.append(f"BLOCKED_FOR_SDO_ROUTE_FIXTURE_{key.upper()}_MISMATCH")
    protected_route = expected.get("protected_action_route")
    if protected_route is not None and actual.get("action_routes", {}).get("protected_release", {}).get("route") != protected_route:
        errors.append("BLOCKED_FOR_SDO_ROUTE_FIXTURE_PROTECTED_ROUTE_MISMATCH")
    comparison_equal = None
    if isinstance(document, dict) and isinstance(document.get("comparison_request"), dict):
        comparison = sdo_route(document["comparison_request"])
        comparison_equal = _sdo_ranking_projection(actual) == _sdo_ranking_projection(comparison)
        if expected.get("comparison_equal") is not comparison_equal:
            errors.append("BLOCKED_FOR_SDO_ROUTE_FIXTURE_COMPARISON_MISMATCH")
    expected_typed_failure = bool(expected_blocks or expected_unavailable)
    if expect_fail and not expected_typed_failure:
        errors.append("BLOCKED_FOR_SDO_ROUTE_FIXTURE_EXPECTED_FAILURE_MISSING")
    passed = not errors and (expect_fail == expected_typed_failure)
    return {
        "fixture": str(path),
        "status": "PASS" if passed else "FAIL",
        "expected_blocks": expected_blocks,
        "actual_blocks": actual_blocks,
        "expected_typed_unavailable": expected_unavailable,
        "actual_typed_unavailable": actual.get("typed_unavailable", []),
        "route": actual.get("route"),
        "selected_action": actual.get("selected_action"),
        "ranking_source": actual.get("ranking_source"),
        "comparison_equal": comparison_equal,
        "blocks": errors,
    }, 0 if passed else 1


def sdo_route_self_test() -> dict[str, Any]:
    controls: dict[str, bool] = {}
    results: dict[str, Any] = {}
    for name in SDO_ROUTE_FIXTURES:
        result, _ = sdo_route_fixture(REPO / "fixtures/mk675/fable5_decision_os" / name, expect_fail=True)
        controls[name] = result["status"] == "PASS"
        results[name] = result

    facts_relabel = sdo_route({"sdo_route": {
        "base_dir": ".",
        "repository_candidates": [
            {"action_id": "safe_local_check"},
            {"action_id": "preferred_pms_action"},
        ],
        "facts": {
            "source_layer": "pms",
            "record_type": "mk748_pms_cognitive_context.v1",
            "status": "proposed",
            "review_status": "pending",
            "summary": "proposed PMS knowledge",
            "recommendation": "preferred_pms_action",
            "candidate_actions": [
                {"action_id": "preferred_pms_action", "protected": False},
                {"action_id": "safe_local_check", "protected": False},
            ],
        },
    }})
    controls["facts_relabel_rejected"] = (
        "BLOCKED_FOR_SDO_LAYER_PROVENANCE_INVALID" in facts_relabel["blockers"]
        and facts_relabel["selected_action"] == "safe_local_check"
        and facts_relabel["ranking_source"] == "repository"
    )
    results["facts_relabel_rejected"] = facts_relabel

    protected_false_override = sdo_route({"sdo_route": {
        "base_dir": ".",
        "repository_candidates": [
            {
                "action_id": "protected_release",
                "protected": False,
                "operation_class": "runtime_release",
            },
            {"action_id": "safe_local_patch", "protected": False},
        ],
        "facts": {"candidate_actions": [
            {"action_id": "protected_release", "protected": False},
            {"action_id": "safe_local_patch", "protected": False},
        ]},
        "authority_results": [],
    }})
    protected_route = protected_false_override["action_routes"].get("protected_release", {})
    controls["protected_false_override_rejected"] = (
        protected_false_override["selected_action"] == "safe_local_patch"
        and protected_route.get("protected") is True
        and protected_route.get("route") == "deny"
        and "BLOCKED_FOR_SDO_REPOSITORY_FACTS_CLASSIFICATION_MISMATCH"
        in protected_false_override["blockers"]
        and "BLOCKED_FOR_SDO_PROTECTED_ACTION_AUTHORITY_BINDING_INVALID"
        in protected_false_override["blockers"]
    )
    results["protected_false_override_rejected"] = protected_false_override

    lower_layer_ranking = sdo_route({"sdo_route": {
        "base_dir": ".",
        "repository_candidates": [
            {"action_id": "allowed_a"},
            {"action_id": "allowed_b"},
        ],
        "facts": {"candidate_actions": [
            {"action_id": "allowed_a", "protected": False},
            {"action_id": "allowed_b", "protected": False},
        ]},
        "brain": {
            "source_layer": "brain",
            "record_type": "brain_decision.v1",
            "source_ref": SDO_BRAIN_SOURCE_REF,
            "source_digest": _sdo_canonical_brain_digest(),
            "candidate_actions": ["allowed_b", "allowed_a"],
        },
        "authority_results": [],
    }})
    controls["lower_layer_ranking_reachable"] = (
        lower_layer_ranking["selected_action"] == "allowed_b"
        and lower_layer_ranking["ranking_source"] == "brain"
        and lower_layer_ranking["candidate_ranking"] == ["allowed_b", "allowed_a"]
    )
    results["lower_layer_ranking_reachable"] = lower_layer_ranking
    passed = all(controls.values())
    return {
        "status": "PASS_SDO_ROUTE_PRECEDENCE_ABSENCE" if passed else "FAIL_SDO_ROUTE_PRECEDENCE_ABSENCE",
        "blocks": [] if passed else ["BLOCKED_FOR_SDO_ROUTE_SELF_TEST"],
        "controls": controls,
        "fixtures": results,
        "non_claims": ["no_runtime_activation", "no_natural_effectiveness", "no_user_acceptance"],
    }


def route(request: dict[str, Any], *, test_isolated: bool = False, _production_like: bool = False) -> dict[str, Any]:
    """Fail closed on identity/qualification/risk; never infer from a label."""
    if isinstance(request, dict) and isinstance(request.get("sdo_route"), dict):
        return sdo_route(request)
    all_profiles = profiles()
    profile = all_profiles.get(request.get("profile_id", ""))
    blockers: list[str] = []
    # INC-180 current-route override. Historical workpacks and isolated
    # qualification fixtures remain inspectable, but the production route may
    # no longer grant Terra write eligibility. Callers retain the supervised
    # Sol fallback defined by agent-dispatch.
    if (
        profile
        and profile.get("profile_id") == "terra_high_implementer"
        and not test_isolated
    ):
        blockers.append("BLOCKED_FOR_MK749_TERRA_WRITE_PROFILE_RETIRED")
    try:
        workpack_digest=current_workpack_digest();binding_record_digest=current_binding_record_digest()
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError):
        workpack_digest=binding_record_digest=None
        blockers.append("BLOCKED_FOR_MK733J_WORKPACK_BINDING_INVALID")
    model_identity_state=request.get("model_identity_state",request.get("runtime_identity_state"))
    if model_identity_state != request.get("runtime_identity_state"):
        blockers.append("BLOCKED_FOR_MK733J_MODEL_RUNTIME_IDENTITY_STATE_DRIFT")
    if request.get("runtime_identity_state") != "verified":
        blockers.append("BLOCKED_FOR_MK733J_IDENTITY_UNVERIFIED")
    if not profile:
        blockers.append("BLOCKED_FOR_MK733J_PROFILE_UNKNOWN")
    else:
        try:bundle_id, bundle = required_bundle(profile["profile_id"], request.get("task_class"),test_isolated=test_isolated);aliases=profile_aliases(profile["profile_id"],test_isolated=test_isolated);registry_available=True
        except (OSError,ValueError,json.JSONDecodeError):bundle_id,bundle,aliases,registry_available=None,None,[],False;blockers.append("BLOCKED_FOR_MK733J_CAPABILITY_REGISTRY_OVERRIDE_INVALID")
        profile_result, qualification_blocks = profile_bundle_result(profile, request.get("task_class"), request, test_isolated=test_isolated,_production_like=_production_like)
        if request.get("runtime_model_identity") not in aliases:
            blockers.append("BLOCKED_FOR_MK733J_RUNTIME_MODEL_IDENTITY_MISMATCH")
        if profile.get("runtime_model_identity_pattern") not in aliases:
            blockers.append("BLOCKED_FOR_MK733J_PROFILE_MODEL_ALIAS_INVALID")
        if profile.get("workpack_digest") != workpack_digest or profile.get("binding_record_digest") != binding_record_digest:
            blockers.append("BLOCKED_FOR_MK733J_PROFILE_WORKPACK_BINDING_INVALID")
        if registry_available:blockers.extend(runtime_identity_blocks(request, profile, test_isolated=test_isolated))
        qualified = not qualification_blocks
        if not qualified:
            blockers.extend(qualification_blocks or ["BLOCKED_FOR_MK733J_TASK_CLASS_BUNDLE_ABSENT_OR_STALE"])
        if request.get("task_class") not in profile.get("allowed_task_classes", []):
            blockers.append("BLOCKED_FOR_MK733J_TASK_CLASS_UNQUALIFIED")
        if request.get("routing_or_design_recommendation") is True and profile["profile_id"] == "terra_readonly_explorer":
            blockers.append("BLOCKED_FOR_MK733J_TERRA_EXPLORER_RECOMMENDATION_ESCALATION")
        if request.get("risk_class") not in profile.get("risk_classes", []):
            blockers.append("BLOCKED_FOR_MK733J_RISK_CEILING_EXCEEDED")
        if request.get("final_audit") and profile["profile_id"] != "sol_independent_reviewer":
            blockers.append("BLOCKED_FOR_MK733J_FINAL_AUDIT_PROFILE_INVALID")
        blockers.extend(final_audit_identity_blocks(request, profile, test_isolated=test_isolated))
        if request.get("implementer_identity") and request.get("auditor_identity") and request["implementer_identity"] == request["auditor_identity"]:
            blockers.append("BLOCKED_FOR_MK733J_AUDITOR_INDEPENDENCE")
    selected = profile["profile_id"] if profile and not blockers else (profile.get("fallback_profile") if profile else "sol_ultra_architect_cmd")
    profile_for_digest = dict(profile) if profile else None
    if profile_for_digest and test_isolated and request.get("test_only_qualified_profile") is True:
        profile_for_digest.update({
            "qualification_state": "empirically_qualified_current",
            "qualification_digest": request.get("qualification_digest"),
            "expires_at": request.get("qualification_expires_at"),
        })
    return {
        "route": "allow" if not blockers else "stop_or_escalate",
        "selected_profile": selected,
        "blockers": sorted(set(blockers)),
        "profile_digest": digest(profile_for_digest) if profile_for_digest else None,
        "workpack_digest": workpack_digest,
        "binding_record_digest": binding_record_digest,
        "profile_snapshot": profile_for_digest,
        "qualification_bundle_id": required_bundle(profile["profile_id"], request.get("task_class"),test_isolated=test_isolated)[0] if profile and bundle else None,
        "profile_qualification_result": profile_result if profile and not test_isolated else None,
        "non_claims": ["no_blanket_model_parity", "no_runtime_readiness", "no_automatic_runtime_firing"],
    }


def receipt(route_result: dict[str, Any], request: dict[str, Any], phase: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Issue a complete local envelope after route approval only."""
    if route_result["route"] != "allow":
        raise ValueError("cannot issue receipt for blocked route")
    if phase not in {"pre_work", "closeout"}:
        raise ValueError("receipt phase is invalid")
    required = {
        "work_id", "goal_ref", "task_class", "risk_class", "runtime_identity_state", "runtime_identity_ref", "thread_run_id",
        "qualification_result_ref", "qualification_expires_at", "qualification_digest", "qualification_results", "runtime_model_identity", "context_digest", "allowed_tools", "budget",
        "return_schema", "readback_required", "auditor_independent_from_implementer", "policy_refs", "non_claims",
        "allowed_path_prefixes", "allowed_command_classes", "forbidden_operation_classes", "external_protected_authority_state", "external_authority_ref", "external_authority_digest", "operation_manifest",
        "preflight_ref", "preflight_digest", "preflight_scope_digest", "preflight_contract_version",
    }
    admitted_optional={"profile_id","qualification_state","test_only_qualified_profile","model_identity_state","preflight_operation_manifest_digest","receipt_ttl_seconds","execution_tier","delegated_autonomy","final_audit","implementer_identity_ref","implementer_identity_digest","auditor_identity_ref","auditor_identity_digest","audit_request_ref","audit_request_digest","audit_head_sha","comparison_base_sha","audit_result_ref","audit_result_digest"} | TARGET_BOUND_FIELDS
    if not required <= set(request) or set(request)-required-admitted_optional:
        raise ValueError("receipt envelope request is incomplete")
    execution_tier = request.get("execution_tier", "autonomous_profile_qualified")
    delegated_autonomy = request.get("delegated_autonomy", True)
    if execution_tier not in {"autonomous_profile_qualified", "authority_gate_transition"} or delegated_autonomy is not True:
        raise ValueError("profile receipt execution tier is invalid")
    target_failures = target_bound_blocks(request) if target_bound_authority_active(request) else []
    if target_failures:
        raise ValueError("profile receipt target binding is invalid: " + ",".join(target_failures))
    preflight_failures=preflight_blocks(request,test_isolated=bool(request.get("test_only_qualified_profile")))
    if preflight_failures:raise ValueError("preflight invalid: "+",".join(preflight_failures))
    if not isinstance(request["allowed_tools"], list) or sorted(set(request["allowed_tools"])) != sorted(policy_tools(route_result["profile_snapshot"], request["task_class"])):
        raise ValueError("allowed_tools is invalid")
    budget = request["budget"]
    if not isinstance(budget, dict) or set(budget)!={"total","remaining"} or not all(isinstance(budget.get(k), int) and not isinstance(budget.get(k), bool) and budget[k] >= 0 for k in ("total", "remaining")) or budget["remaining"] > budget["total"]:
        raise ValueError("budget is invalid")
    qualification_results=request["qualification_results"]
    if not isinstance(qualification_results,dict) or not qualification_results or any(not isinstance(row,dict) or set(row)!={"result_ref","qualification_digest"} or not all(isinstance(row.get(key),str) and row[key] for key in row) for row in qualification_results.values()):
        raise ValueError("qualification results are invalid")
    audit_fields={"implementer_identity_ref","implementer_identity_digest","auditor_identity_ref","auditor_identity_digest","audit_request_ref","audit_request_digest","audit_head_sha","comparison_base_sha","audit_result_ref","audit_result_digest"}
    independent=request.get("task_class")=="independent_audit" and route_result.get("selected_profile")=="sol_independent_reviewer"
    if independent:
        if request.get("final_audit") is not True or not all(isinstance(request.get(key),str) and request[key] for key in audit_fields):
            raise ValueError("final audit identity contract is incomplete")
    elif request.get("final_audit") is True or any(request.get(key) is not None for key in audit_fields):
        raise ValueError("audit fields are out of scope for this receipt")
    if manifest_blocks(request["operation_manifest"], phase="pre_work", fresh=True):
        raise ValueError("operation manifest is invalid")
    target_active = target_bound_authority_active(request)
    manifest_classes = {
        row["command_class"] for row in request["operation_manifest"]["bash_commands"]
    } | set(request["operation_manifest"]["mutation_classes"]) | {
        row["command_class"] for row in request["operation_manifest"]["read_only_diagnostics"]
    }
    if (
        not manifest_classes <= set(request["allowed_command_classes"])
        or (not target_active and not PROTECTED_OPERATION_CLASSES <= set(request["forbidden_operation_classes"]))
        or scope_path_blocks(request["allowed_path_prefixes"], request["operation_manifest"])
    ):
        raise ValueError("operation manifest authority is outside bounded policy")
    authorized_count = sum(row["allowed_count"] for row in request["operation_manifest"]["bash_commands"]) + sum(
        row["allowed_count"] for row in request["operation_manifest"]["mutation_classes"].values()
    )
    if budget != {"total": authorized_count, "remaining": authorized_count}:
        raise ValueError("budget is not bound to operation manifest")
    issued = now or datetime.now(timezone.utc)
    ttl = request.get("receipt_ttl_seconds", 900)
    if not isinstance(ttl, int) or ttl <= 0 or ttl > 3600:
        raise ValueError("receipt ttl is invalid")
    qualification_expiry = parse_time(request["qualification_expires_at"])
    if qualification_expiry is None or qualification_expiry <= issued:
        raise ValueError("qualification expiry is invalid")
    expires = min(issued + timedelta(seconds=ttl), qualification_expiry)
    closeout = phase == "closeout"
    payload = {
        "receipt_version": "mk733j-n.v2", "execution_tier": execution_tier, "delegated_autonomy": delegated_autonomy, "work_id": request["work_id"], "goal_ref": request["goal_ref"],
        "task_class": request["task_class"], "risk_class": request["risk_class"], "phase": phase,
        "route": route_result["route"], "final_audit": request.get("final_audit") is True, "selected_profile": route_result["selected_profile"],
        "profile_digest": route_result["profile_digest"], "qualification_result_ref": request["qualification_result_ref"],
        "qualification_expires_at": request["qualification_expires_at"], "qualification_digest": request["qualification_digest"], "qualification_state":"current", "qualification_results":request["qualification_results"], "runtime_model_identity": request["runtime_model_identity"], "runtime_identity_state": request["runtime_identity_state"],
        "model_identity_state": request.get("model_identity_state",request["runtime_identity_state"]), "runtime_identity_ref": request["runtime_identity_ref"], "thread_run_id": request["thread_run_id"], "context_digest": request["context_digest"],
        "implementer_identity_ref": request.get("implementer_identity_ref"), "implementer_identity_digest": request.get("implementer_identity_digest"), "auditor_identity_ref": request.get("auditor_identity_ref"), "auditor_identity_digest": request.get("auditor_identity_digest"), "audit_request_ref": request.get("audit_request_ref"), "audit_request_digest": request.get("audit_request_digest"), "audit_head_sha": request.get("audit_head_sha"), "comparison_base_sha": request.get("comparison_base_sha"), "audit_result_ref": request.get("audit_result_ref"), "audit_result_digest": request.get("audit_result_digest"),
        "preflight_ref": request["preflight_ref"], "preflight_digest": request["preflight_digest"],
        "preflight_scope_digest": request["preflight_scope_digest"],
        "preflight_operation_manifest_digest": manifest_policy_digest(request["operation_manifest"]),
        "preflight_contract_version": request["preflight_contract_version"],
        "workpack_digest": route_result["workpack_digest"], "binding_record_digest": route_result["binding_record_digest"],
        "allowed_tools": receipt_tools(route_result["profile_snapshot"], request["task_class"], phase),
        "budget": {"total": 1, "remaining": 1} if phase == "closeout" else dict(budget),
        "return_schema": request["return_schema"], "readback_required": request["readback_required"],
        "auditor_independent_from_implementer": request["auditor_independent_from_implementer"],
        "issued_at": issued.isoformat().replace("+00:00", "Z"), "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "policy_refs": request["policy_refs"], "non_claims": request["non_claims"],
        "allowed_path_prefixes": [] if closeout else sorted(set(request["allowed_path_prefixes"])),
        "allowed_command_classes": [CLOSEOUT_ACTION_ID] if closeout else sorted(set(request["allowed_command_classes"])),
        "forbidden_operation_classes": [] if closeout else sorted(set(request["forbidden_operation_classes"])),
        "external_protected_authority_state": "absent" if closeout else request["external_protected_authority_state"],
        "external_authority_ref": None if closeout else request["external_authority_ref"],
        "external_authority_digest": None if closeout else request["external_authority_digest"],
        "operation_manifest": {"closeout":{"operation_digest":CLOSEOUT_ACTION_DIGEST,"allowed_count":1,"remaining":1}} if closeout else request["operation_manifest"],
        "claim_scope": "support_control_local_only",
    }
    if not closeout and target_bound_authority_active(request):
        payload.update({key: request[key] for key in TARGET_BOUND_FIELDS})
    payload["qualification_results_digest"]=digest(payload["qualification_results"])
    if not all(isinstance(payload[k], str) and payload[k] for k in ("work_id", "goal_ref", "qualification_result_ref", "qualification_digest", "runtime_model_identity", "runtime_identity_ref", "thread_run_id", "context_digest", "preflight_digest", "preflight_scope_digest", "workpack_digest", "binding_record_digest")) or payload["runtime_identity_state"] != "verified" or payload["auditor_independent_from_implementer"] is not True or payload["readback_required"] is not True or not isinstance(payload["return_schema"], str) or not payload["return_schema"].replace("_", "").isalnum() or not all(isinstance(x, str) and x for x in payload["policy_refs"] + payload["non_claims"]):
        raise ValueError("receipt dispatch handshake is invalid")
    payload["operation_manifest_digest"] = digest(payload["operation_manifest"])
    payload["operation_manifest_policy_digest"] = manifest_policy_digest(payload["operation_manifest"])
    scope_policy = receipt_scope_policy(payload)
    payload["scope_policy_digest"] = digest(scope_policy)
    manifest=payload["operation_manifest"]
    if not all(isinstance(x,str) and x for x in payload["allowed_path_prefixes"]+payload["allowed_command_classes"]+payload["forbidden_operation_classes"]) or payload["external_protected_authority_state"] not in {"absent","explicitly_authorized_exact_scope"} or manifest_blocks(manifest, phase=phase, fresh=True):
        raise ValueError("receipt scope policy is invalid")
    external_failures=external_authority_blocks(payload, test_isolated=bool(request.get("test_only_qualified_profile")))
    if external_failures:
        raise ValueError("external or protected authority is invalid: "+",".join(external_failures))
    payload["receipt_digest"] = digest(payload)
    return payload


def authority_gate_transition_receipt(request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Issue the minimal receipt for a human-supervised Authority Gate transition.

    This is intentionally not a profile receipt: no profile, model identity,
    qualification result, or preflight may appear here.  It still carries a
    finite, authority-bound operation manifest; otherwise enforcement would
    enter a dead state or become a broad bypass.
    """
    if set(request) != AUTHORITY_GATE_REQUEST_FIELDS:
        raise ValueError("authority transition request schema is invalid")
    if (
        request.get("execution_tier") != "authority_gate_transition"
        or request.get("delegated_autonomy") is not False
        or request.get("task_class") != "authority_gate_transition"
        or request.get("external_protected_authority_state") != "explicitly_authorized_exact_scope"
        or not all(isinstance(request.get(key), str) and request[key] for key in (
            "work_id", "goal_ref", "risk_class", "context_digest", "external_authority_ref", "external_authority_digest",
        ))
        or request.get("readback_required") is not True
        or not all(isinstance(request.get(key), list) and request[key] and all(isinstance(item, str) and item for item in request[key]) for key in ("policy_refs", "non_claims", "allowed_tools", "allowed_command_classes"))
        or not all(isinstance(request.get(key), list) and all(isinstance(item, str) and item for item in request[key]) for key in ("allowed_path_prefixes", "forbidden_operation_classes"))
    ):
        raise ValueError("authority transition request binding is invalid")
    target_blocks = target_bound_blocks(request) if target_bound_authority_active(request) else []
    if target_blocks:
        raise ValueError("authority transition target binding is invalid: " + ",".join(target_blocks))
    ttl = request.get("receipt_ttl_seconds")
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0 or ttl > 3600:
        raise ValueError("authority transition ttl is invalid")
    manifest = request.get("operation_manifest")
    budget = request.get("budget")
    if (
        authority_gate_manifest_blocks(manifest, fresh=True)
        or scope_path_blocks(request.get("allowed_path_prefixes"), manifest)
        or not isinstance(budget, dict)
        or set(budget) != {"total", "remaining"}
        or any(not isinstance(budget.get(key), int) or isinstance(budget.get(key), bool) or budget[key] < 0 for key in budget)
        or set(request["allowed_command_classes"]) & set(request["forbidden_operation_classes"])
        or any(row.get("command_class") not in request["allowed_command_classes"] for row in manifest.get("bash_commands", []) if isinstance(row, dict))
        or set(manifest.get("mutation_classes", {})) - set(request["allowed_command_classes"])
        or manifest.get("read_only_diagnostics") != []
    ):
        raise ValueError("authority transition operation scope is invalid")
    mutation_rows = manifest.get("mutation_classes", {})
    if any(not row.get("exact_files") and not row.get("path_prefixes") for row in mutation_rows.values() if isinstance(row, dict)):
        raise ValueError("authority transition mutation path scope is invalid")
    authorized_count = sum(row["allowed_count"] for row in manifest["bash_commands"]) + sum(
        row["allowed_count"] for row in mutation_rows.values()
    )
    if authorized_count <= 0 or budget != {"total": authorized_count, "remaining": authorized_count}:
        raise ValueError("authority transition budget is invalid")
    authority_request_digest = authority_gate_request_digest(request)
    issued = now or datetime.now(timezone.utc)
    payload = {
        "receipt_version": "mk733j-n.authority-gate.v1",
        "execution_tier": "authority_gate_transition",
        "delegated_autonomy": False,
        # The existing hook consume wire uses pre_work.  Tier/version dispatch
        # keeps this distinct from a profile pre-work receipt.
        "phase": "pre_work",
        "work_id": request["work_id"], "goal_ref": request["goal_ref"],
        "task_class": request["task_class"], "risk_class": request["risk_class"],
        "context_digest": request["context_digest"],
        "workpack_digest": current_workpack_digest(), "binding_record_digest": current_binding_record_digest(),
        "external_protected_authority_state": request["external_protected_authority_state"],
        "external_authority_ref": request["external_authority_ref"],
        "external_authority_digest": request["external_authority_digest"],
        "authority_request_digest": authority_request_digest,
        "receipt_ttl_seconds": ttl,
        "allowed_tools": request["allowed_tools"],
        "allowed_path_prefixes": request["allowed_path_prefixes"],
        "allowed_command_classes": request["allowed_command_classes"],
        "forbidden_operation_classes": request["forbidden_operation_classes"],
        "operation_manifest": manifest,
        "budget": budget,
        "readback_required": True,
        "policy_refs": request["policy_refs"], "non_claims": request["non_claims"],
        "claim_scope": "support_control_authority_transition_only",
        **{key: request[key] for key in TARGET_BOUND_FIELDS},
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z"),
    }
    payload["operation_manifest_digest"] = digest(payload["operation_manifest"])
    payload["operation_manifest_policy_digest"] = manifest_policy_digest(payload["operation_manifest"])
    payload["scope_policy_digest"] = digest(authority_gate_scope_policy(payload))
    payload["receipt_digest"] = digest(payload)
    return payload


def authority_gate_transition_receipt_blocks(value: dict[str, Any], expected_phase: str | None = None, expected_tool: str | None = None, expected_work_id: str | None = None, *, now: datetime | None = None, test_isolated: bool = False) -> list[str]:
    if set(value) != AUTHORITY_GATE_RECEIPT_FIELDS:
        return ["BLOCKED_FOR_MK733J_AUTHORITY_GATE_RECEIPT_SCHEMA_INVALID"]
    target_blocks = target_bound_blocks(value) if target_bound_authority_active(value) else []
    if target_blocks:
        return target_blocks
    body = dict(value); supplied = body.pop("receipt_digest", None)
    issued, expires = parse_time(value.get("issued_at")), parse_time(value.get("expires_at"))
    moment = now or datetime.now(timezone.utc)
    manifest = value.get("operation_manifest")
    budget = value.get("budget")
    if (
        supplied != digest(body)
        or value.get("receipt_version") != "mk733j-n.authority-gate.v1"
        or value.get("execution_tier") != "authority_gate_transition"
        or value.get("delegated_autonomy") is not False
        or value.get("phase") != "pre_work"
        or value.get("task_class") != "authority_gate_transition"
        or value.get("external_protected_authority_state") != "explicitly_authorized_exact_scope"
        or value.get("workpack_digest") != current_workpack_digest()
        or value.get("binding_record_digest") != current_binding_record_digest()
        or not issued or not expires or issued >= expires or issued > moment or expires <= moment
        or not all(isinstance(value.get(key), str) and value[key] for key in ("work_id", "goal_ref", "risk_class", "context_digest", "external_authority_ref", "external_authority_digest"))
        or value.get("readback_required") is not True
        or not all(isinstance(value.get(key), list) and value[key] and all(isinstance(item, str) and item for item in value[key]) for key in ("policy_refs", "non_claims", "allowed_tools", "allowed_command_classes"))
        or not all(isinstance(value.get(key), list) and all(isinstance(item, str) and item for item in value[key]) for key in ("allowed_path_prefixes", "forbidden_operation_classes"))
        or not isinstance(value.get("receipt_ttl_seconds"), int) or isinstance(value.get("receipt_ttl_seconds"), bool) or value["receipt_ttl_seconds"] <= 0 or value["receipt_ttl_seconds"] > 3600
        or expires != issued + timedelta(seconds=value["receipt_ttl_seconds"])
        or authority_gate_manifest_blocks(manifest, fresh=False)
        or scope_path_blocks(value.get("allowed_path_prefixes"), manifest)
        or not isinstance(budget, dict) or set(budget) != {"total", "remaining"}
        or any(not isinstance(budget.get(key), int) or isinstance(budget.get(key), bool) or budget[key] < 0 for key in budget)
        or value.get("operation_manifest_digest") != digest(manifest)
        or value.get("operation_manifest_policy_digest") != manifest_policy_digest(manifest)
        or value.get("scope_policy_digest") != digest(authority_gate_scope_policy(value))
        or set(value.get("allowed_command_classes", [])) & set(value.get("forbidden_operation_classes", []))
        or any(row.get("command_class") not in value.get("allowed_command_classes", []) for row in manifest.get("bash_commands", []) if isinstance(row, dict))
        or set(manifest.get("mutation_classes", {})) - set(value.get("allowed_command_classes", []))
        or manifest.get("read_only_diagnostics") != []
    ):
        return ["BLOCKED_FOR_MK733J_AUTHORITY_GATE_RECEIPT_BINDING_INVALID"]
    mutation_rows = manifest.get("mutation_classes", {})
    if any(not row.get("exact_files") and not row.get("path_prefixes") for row in mutation_rows.values() if isinstance(row, dict)):
        return ["BLOCKED_FOR_MK733J_AUTHORITY_GATE_RECEIPT_BINDING_INVALID"]
    authorized_count = sum(row["allowed_count"] for row in manifest["bash_commands"]) + sum(row["allowed_count"] for row in mutation_rows.values())
    remaining_count = sum(row["remaining"] for row in manifest["bash_commands"]) + sum(row["remaining"] for row in mutation_rows.values())
    if budget != {"total": authorized_count, "remaining": remaining_count}:
        return ["BLOCKED_FOR_MK733J_AUTHORITY_GATE_RECEIPT_BINDING_INVALID"]
    request = {
        "execution_tier": value["execution_tier"], "delegated_autonomy": value["delegated_autonomy"],
        "work_id": value["work_id"], "goal_ref": value["goal_ref"], "task_class": value["task_class"],
        "risk_class": value["risk_class"], "context_digest": value["context_digest"],
        "external_protected_authority_state": value["external_protected_authority_state"],
        "external_authority_ref": value["external_authority_ref"], "external_authority_digest": value["external_authority_digest"],
        "policy_refs": value["policy_refs"], "non_claims": value["non_claims"],
        "receipt_ttl_seconds": value["receipt_ttl_seconds"], "allowed_tools": value["allowed_tools"],
        "allowed_path_prefixes": value["allowed_path_prefixes"], "allowed_command_classes": value["allowed_command_classes"],
        "forbidden_operation_classes": value["forbidden_operation_classes"], "operation_manifest": value["operation_manifest"],
        "budget": value["budget"], "readback_required": value["readback_required"],
        **{key: value[key] for key in TARGET_BOUND_FIELDS},
    }
    try:
        request_digest = authority_gate_request_digest({**request, "budget": {"total": budget["total"], "remaining": budget["total"]}, "operation_manifest": {
            **manifest, "bash_commands": [{**row, "remaining": row["allowed_count"]} for row in manifest["bash_commands"]],
            "mutation_classes": {key: {**row, "remaining": row["allowed_count"]} for key, row in mutation_rows.items()},
        }})
    except (TypeError, ValueError):
        return ["BLOCKED_FOR_MK733J_AUTHORITY_GATE_RECEIPT_BINDING_INVALID"]
    if value.get("authority_request_digest") != request_digest:
        return ["BLOCKED_FOR_MK733J_AUTHORITY_GATE_RECEIPT_BINDING_INVALID"]
    authority_path = Path(value["external_authority_ref"])
    authority_path = authority_path.resolve() if authority_path.is_absolute() else (REPO / authority_path).resolve()
    if (test_isolated and REPO in authority_path.parents) or (not test_isolated and TRUSTED_AUTHORITY_DIR.resolve() not in authority_path.parents):
        return ["BLOCKED_FOR_MK733J_EXTERNAL_OR_PROTECTED_AUTHORITY_MISSING"]
    try:
        authority = load(authority_path)
        authority_body = dict(authority); authority_digest = authority_body.pop("envelope_digest", None)
        authority_issued, authority_expires = parse_time(authority.get("issued_at")), parse_time(authority.get("expires_at"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return ["BLOCKED_FOR_MK733J_EXTERNAL_OR_PROTECTED_AUTHORITY_MISSING"]
    if (
        authority_digest != digest(authority_body)
        or authority_digest != value.get("external_authority_digest")
        or authority.get("approved") is not True
        or authority.get("execution_tier") != "authority_gate_transition"
        or authority.get("delegated_autonomy") is not False
        or authority.get("profile_request_digest") != value.get("authority_request_digest")
        or authority.get("workpack_digest") != value.get("workpack_digest")
        or authority.get("binding_record_digest") != value.get("binding_record_digest")
        or (target_bound_authority_active(value) and any(authority.get(key) != value.get(key) for key in TARGET_BOUND_FIELDS))
        or not authority_issued or not authority_expires or authority_issued >= authority_expires or authority_expires <= moment
    ):
        return ["BLOCKED_FOR_MK733J_EXTERNAL_OR_PROTECTED_AUTHORITY_MISSING"]
    if expected_phase and value.get("phase") != expected_phase:
        return ["BLOCKED_FOR_MK733J_RECEIPT_PHASE_INVALID"]
    if expected_tool and expected_tool not in value.get("allowed_tools", []):
        return ["BLOCKED_FOR_MK733J_RECEIPT_TOOL_NOT_ALLOWED"]
    if expected_work_id and value.get("work_id") != expected_work_id:
        return ["BLOCKED_FOR_MK733J_RECEIPT_WORK_ID_INVALID"]
    return []


def receipt_blocks(value: dict[str, Any], expected_phase: str | None = None, expected_tool: str | None = None, expected_work_id: str | None = None, *, now: datetime | None = None, test_isolated: bool = False) -> list[str]:
    if not isinstance(value, dict):
        return ["BLOCKED_FOR_MK733J_RECEIPT_SCHEMA_INVALID"]
    receipt_version = value.get("receipt_version")
    if receipt_version == "mk733j-n.authority-gate.v1":
        return authority_gate_transition_receipt_blocks(
            value, expected_phase, expected_tool, expected_work_id, now=now, test_isolated=test_isolated,
        )
    if receipt_version != "mk733j-n.v2":
        return ["BLOCKED_FOR_MK733J_RECEIPT_SCHEMA_INVALID"]
    blocks: list[str] = []
    target_active = target_bound_authority_active(value)
    if (
        set(value) - RECEIPT_FIELDS - TARGET_BOUND_FIELDS
        or (target_active and not TARGET_BOUND_FIELDS <= set(value))
        or (not target_active and TARGET_BOUND_FIELDS & set(value))
    ):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_INCOMPLETE")
    if target_active:
        blocks.extend(target_bound_blocks(value))
    def sensitive_key(key: Any) -> bool:
        if not isinstance(key,str):
            return True
        snake=re.sub(r"([a-z0-9])([A-Z])",r"\1_\2",key)
        tokens={token for token in re.split(r"[^a-z0-9]+",snake.lower()) if token}
        compact="".join(tokens)
        forbidden={"rawprompt","prompt","transcript","rawtranscript","hiddenreasoning","hiddencot","chainofthought","secret","credential","credentials","token","apikey","accesstoken"}
        return bool(tokens & {"prompt","transcript","secret","credential","credentials","token"}) or compact in forbidden or {"hidden","reasoning"} <= tokens
    def sensitive(node: Any) -> bool:
        if isinstance(node, dict): return any(sensitive_key(k) or sensitive(v) for k,v in node.items())
        if isinstance(node, list): return any(sensitive(v) for v in node)
        return False
    if sensitive(value):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_SENSITIVE_CONTENT")
    copy = dict(value); supplied = copy.pop("receipt_digest", None)
    if supplied != digest(copy):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_DIGEST_INVALID")
    current = load(IMPLEMENTATION)
    if value.get("workpack_digest") != current_workpack_digest():
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_WORKPACK_STALE")
    if value.get("binding_record_digest") != current_binding_record_digest():
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_BINDING_RECORD_STALE")
    profile = profiles().get(value.get("selected_profile"))
    profile_for_digest = dict(profile) if profile else None
    if profile_for_digest and test_isolated:
        profile_for_digest.update({"qualification_state": "empirically_qualified_current", "qualification_digest": value.get("qualification_digest"), "expires_at": value.get("qualification_expires_at")})
    if not profile_for_digest or value.get("profile_digest") != digest(profile_for_digest):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_PROFILE_STALE")
    moment = now or datetime.now(timezone.utc)
    issued, expires, qualified_until = (parse_time(value.get(k)) for k in ("issued_at", "expires_at", "qualification_expires_at"))
    if not issued or not expires or not qualified_until or issued >= expires or issued > moment or expires <= moment or qualified_until <= moment or expires > qualified_until:
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_EXPIRED")
    budget = value.get("budget")
    qualification_results=value.get("qualification_results")
    if not isinstance(budget, dict) or set(budget)!={"total","remaining"} or not all(isinstance(budget.get(k), int) and not isinstance(budget.get(k), bool) and budget[k] >= 0 for k in ("total", "remaining")) or budget.get("remaining", 1) > budget.get("total", 0):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_BUDGET_INVALID")
    if not isinstance(qualification_results,dict) or not qualification_results or any(not isinstance(item,dict) or set(item)!={"result_ref","qualification_digest"} or not all(isinstance(item.get(key),str) and item[key] for key in item) for item in qualification_results.values()):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_SCHEMA_INVALID")
    if value.get("phase") == "closeout" and (budget != {"total": 1, "remaining": 1} and budget != {"total": 1, "remaining": 0}):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_BUDGET_INVALID")
    if value.get("model_identity_state") != value.get("runtime_identity_state") or value.get("runtime_identity_state") != "verified" or not all(isinstance(value.get(key), str) and value.get(key) for key in ("runtime_identity_ref", "thread_run_id", "preflight_digest", "preflight_scope_digest")) or value.get("readback_required") is not True or value.get("auditor_independent_from_implementer") is not True:
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_HANDSHAKE_INVALID")
    if value.get("execution_tier") not in {"autonomous_profile_qualified", "authority_gate_transition"} or value.get("delegated_autonomy") is not True:
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_HANDSHAKE_INVALID")
    if value.get("selected_profile") == "sol_independent_reviewer" and value.get("task_class") == "independent_audit":
        if value.get("final_audit") is not True:
            blocks.append("BLOCKED_FOR_MK733J_FINAL_AUDIT_IDENTITY_MISSING")
        else:
            blocks.extend(final_audit_identity_blocks(value,profile,test_isolated=test_isolated))
    if not isinstance(value.get("allowed_tools"), list) or not value["allowed_tools"] or not isinstance(value.get("policy_refs"), list) or not isinstance(value.get("non_claims"), list):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_HANDSHAKE_INVALID")
    scope_policy = receipt_scope_policy(value)
    if value.get("qualification_results_digest")!=digest(value.get("qualification_results")) or value.get("operation_manifest_digest") != digest(value.get("operation_manifest")) or value.get("operation_manifest_policy_digest") != manifest_policy_digest(value.get("operation_manifest")) or value.get("scope_policy_digest") != digest(scope_policy) or not all(isinstance(value.get(k),list) and all(isinstance(x,str) and x for x in value[k]) for k in ("allowed_path_prefixes","allowed_command_classes","forbidden_operation_classes")):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_SCOPE_POLICY_INVALID")
    if manifest_blocks(value.get("operation_manifest"), phase=value.get("phase"), fresh=False):
        blocks.append("BLOCKED_FOR_MK733J_OPERATION_MANIFEST_INVALID")
    if scope_path_blocks(value.get("allowed_path_prefixes"), value.get("operation_manifest")):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE")
    elif value.get("phase") == "pre_work":
        manifest = value["operation_manifest"]
        authorized_count = sum(row["allowed_count"] for row in manifest["bash_commands"]) + sum(
            row["allowed_count"] for row in manifest["mutation_classes"].values()
        )
        remaining_count = sum(row["remaining"] for row in manifest["bash_commands"]) + sum(
            row["remaining"] for row in manifest["mutation_classes"].values()
        )
        if budget != {"total": authorized_count, "remaining": remaining_count}:
            blocks.append("BLOCKED_FOR_MK733J_RECEIPT_BUDGET_INVALID")
    if value.get("phase") == "closeout" and (
        value.get("allowed_path_prefixes") != []
        or value.get("allowed_command_classes") != [CLOSEOUT_ACTION_ID]
        or value.get("forbidden_operation_classes") != []
        or value.get("external_protected_authority_state") != "absent"
        or value.get("operation_manifest") != {"closeout":{"operation_digest":CLOSEOUT_ACTION_DIGEST,"allowed_count":1,"remaining":value.get("budget",{}).get("remaining")}}
    ):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_SCOPE_POLICY_INVALID")
    if expected_phase and value.get("phase") != expected_phase:
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_PHASE_INVALID")
    if expected_tool and expected_tool not in set(value.get("allowed_tools", [])):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_TOOL_NOT_ALLOWED")
    if expected_work_id and value.get("work_id") != expected_work_id:
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_WORK_ID_INVALID")
    if value.get("phase") not in {"pre_work", "closeout"}:
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_PHASE_INVALID")
    if profile and value.get("allowed_tools") != receipt_tools(profile, value.get("task_class"), value.get("phase")):
        blocks.append("BLOCKED_FOR_MK733J_RECEIPT_TOOL_NOT_ALLOWED")
    blocks.extend(preflight_blocks(value,test_isolated=test_isolated))
    blocks.extend(external_authority_blocks(value,test_isolated=test_isolated))
    blocks.extend(runtime_identity_blocks(value, profile, test_isolated=test_isolated))
    blocks.extend(binding_blocks(value, profile_for_digest, test_isolated=test_isolated))
    return sorted(set(blocks))


def consume_receipt(path: Path, expected_phase: str, expected_tool: str, *, observed_paths: list[str], command_class: str, operation_digest: str, operation_bytes: int, operation_lines: int, test_isolated: bool) -> list[str]:
    """Atomically consume one listed operation and the total budget."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            value = load(path)
            if isinstance(value, dict) and value.get("receipt_version") == "mk733j-n.authority-gate.v1":
                blocks = authority_gate_transition_receipt_blocks(value, expected_phase, expected_tool, test_isolated=test_isolated)
                if blocks:
                    return blocks
                target_blocks = target_bound_consumer_blocks(
                    value, observed_paths=observed_paths, command_class=command_class, operation_digest=operation_digest,
                ) if target_bound_authority_active(value) else []
                if target_blocks:
                    return target_blocks
                manifest = value["operation_manifest"]
                if command_class not in value["allowed_command_classes"]:
                    return ["BLOCKED_FOR_MK733J_RECEIPT_COMMAND_CLASS_OUT_OF_SCOPE"]
                if command_class in value["forbidden_operation_classes"]:
                    return ["BLOCKED_FOR_MK733J_EXTERNAL_OR_PROTECTED_AUTHORITY_MISSING"]
                if expected_tool == "Bash":
                    if observed_paths and any(
                        not any(item == prefix or item.startswith(prefix.rstrip("/") + "/") for prefix in value["allowed_path_prefixes"])
                        for item in observed_paths
                    ):
                        return ["BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE"]
                    row = next((item for item in manifest["bash_commands"] if item["operation_digest"] == operation_digest and item["command_class"] == command_class), None)
                    if not row or row["remaining"] <= 0:
                        return ["BLOCKED_FOR_MK733J_BASH_OPERATION_NOT_MANIFESTED"]
                    row["remaining"] -= 1
                else:
                    policy = manifest["mutation_classes"].get(command_class)
                    if not policy or policy["remaining"] <= 0:
                        return ["BLOCKED_FOR_MK733J_MUTATION_CLASS_NOT_MANIFESTED"]
                    if policy["operation_digest"] != operation_digest:
                        return ["BLOCKED_FOR_MK733J_MUTATION_OPERATION_NOT_MANIFESTED"]
                    exact = set(policy["exact_files"])
                    prefixes = policy["path_prefixes"]
                    if not observed_paths or any(item not in exact and not any(item == prefix or item.startswith(prefix.rstrip("/") + "/") for prefix in prefixes) for item in observed_paths):
                        return ["BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE"]
                    if len(observed_paths) > policy["max_changed_files"] or operation_bytes > policy["max_bytes"] or operation_lines > policy["max_lines"]:
                        return ["BLOCKED_FOR_MK733J_MUTATION_SIZE_EXCEEDED"]
                    policy["remaining"] -= 1
                if value["budget"]["remaining"] < 1:
                    return ["BLOCKED_FOR_MK733J_RECEIPT_BUDGET_EXHAUSTED"]
                value["budget"]["remaining"] -= 1
                value["operation_manifest_digest"] = digest(value["operation_manifest"])
                value["scope_policy_digest"] = digest(authority_gate_scope_policy(value))
                value.pop("receipt_digest", None)
                value["receipt_digest"] = digest(value)
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
                temporary.replace(path)
                return []
            blocks = receipt_blocks(value, expected_phase, expected_tool, test_isolated=test_isolated)
            if blocks: return blocks
            if target_bound_authority_active(value):
                target_blocks = target_bound_consumer_blocks(
                    value, observed_paths=observed_paths, command_class=command_class, operation_digest=operation_digest,
                )
                if target_blocks:
                    return target_blocks
            if expected_phase == "closeout":
                row=value["operation_manifest"].get("closeout",{})
                if (
                    expected_tool != CLOSEOUT_ACTION_ID
                    or command_class != CLOSEOUT_ACTION_ID
                    or observed_paths
                    or operation_digest != CLOSEOUT_ACTION_DIGEST
                    or operation_bytes != CLOSEOUT_OPERATION_BYTES
                    or operation_lines != CLOSEOUT_OPERATION_LINES
                    or row.get("remaining") != 1
                ):
                    return ["BLOCKED_FOR_MK733J_RECEIPT_CLOSEOUT_ACTION_INVALID"]
                row["remaining"]-=1
            else:
                allowed_prefixes = value["allowed_path_prefixes"]; manifest=value["operation_manifest"]
                if command_class not in value["allowed_command_classes"]:
                    return ["BLOCKED_FOR_MK733J_RECEIPT_COMMAND_CLASS_OUT_OF_SCOPE"]
                if (command_class in PROTECTED_OPERATION_CLASSES or command_class in value["forbidden_operation_classes"]) and value.get("external_protected_authority_state")!="explicitly_authorized_exact_scope":return ["BLOCKED_FOR_MK733J_EXTERNAL_OR_PROTECTED_AUTHORITY_MISSING"]
                diagnostic=next((x for x in manifest.get("read_only_diagnostics",[]) if isinstance(x,dict) and x.get("operation_digest")==operation_digest and x.get("command_class")==command_class),None)
                if diagnostic:
                    return []
                if expected_tool=="Bash":
                    if observed_paths and any(
                        not any(path==prefix or path.startswith(prefix.rstrip("/")+"/") for prefix in allowed_prefixes)
                        for path in observed_paths
                    ):
                        return ["BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE"]
                    row=next((x for x in manifest.get("bash_commands",[]) if isinstance(x,dict) and x.get("operation_digest")==operation_digest and x.get("command_class")==command_class),None)
                    if not row or not isinstance(row.get("remaining"),int) or row["remaining"]<=0:return ["BLOCKED_FOR_MK733J_BASH_OPERATION_NOT_MANIFESTED"]
                    row["remaining"]-=1
                else:
                    policy=manifest.get("mutation_classes",{}).get(command_class)
                    if not isinstance(policy,dict) or not isinstance(policy.get("remaining"),int) or policy["remaining"]<=0:return ["BLOCKED_FOR_MK733J_MUTATION_CLASS_NOT_MANIFESTED"]
                    exact=set(policy.get("exact_files",[]));prefixes=policy.get("path_prefixes",allowed_prefixes)
                    if not observed_paths or any(p not in exact and not any(p==prefix or p.startswith(prefix.rstrip("/")+"/") for prefix in prefixes) for p in observed_paths):return ["BLOCKED_FOR_MK733J_RECEIPT_PATH_OUT_OF_SCOPE"]
                    if len(observed_paths)>policy.get("max_changed_files",0) or operation_bytes>policy.get("max_bytes",0) or operation_lines>policy.get("max_lines",0):return ["BLOCKED_FOR_MK733J_MUTATION_SIZE_EXCEEDED"]
                    policy["remaining"]-=1
            if value["budget"]["remaining"] < 1:
                return ["BLOCKED_FOR_MK733J_RECEIPT_BUDGET_EXHAUSTED"]
            value["budget"]["remaining"] -= 1
            value["operation_manifest_digest"]=digest(value["operation_manifest"])
            scope_policy = receipt_scope_policy(value)
            value["scope_policy_digest"]=digest(scope_policy)
            value.pop("receipt_digest", None); value["receipt_digest"] = digest(value)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(path)
            return []
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def evaluate() -> dict[str, Any]:
    corpus = load(CORPUS)["cases"]
    expected_ids = {"identity-unverified", "qualification-stale", "cheap-unqualified", "same-worker-auditor"}
    invalid = [case.get("id", "unknown") for case in corpus if not isinstance(case.get("request"), dict) or not case.get("id") or not case.get("expected")]
    return {"router_contract_cases": len(corpus), "required_adversarial_router_cases_present": expected_ids <= {case.get("id") for case in corpus}, "invalid_cases": invalid, "status": "PASS_ROUTER_CONTRACT_ONLY" if not invalid and expected_ids <= {case.get("id") for case in corpus} else "FAIL_ROUTER_CONTRACT", "non_claims": ["not_an_empirical_model_qualification", "no_global_model_equivalence", "no_threshold_measurement"]}


def final_audit_self_test() -> dict[str, Any]:
    """Isolated exact-attestation matrix; canonical trust registry is never mutated."""
    import tempfile
    import mk733j_qualification as qualification
    now=datetime.now(timezone.utc);observed=(now-timedelta(minutes=1)).isoformat().replace("+00:00","Z");expires=(now+timedelta(hours=1)).isoformat().replace("+00:00","Z")
    head="a"*40;base="b"*40
    with tempfile.TemporaryDirectory(prefix="mk733j-final-audit-") as directory:
        root=Path(directory);request={"record_type":"mk733j_final_audit_request","audit_request_id":"audit-1","audited_head_sha":head,"comparison_base_sha":base,"requested_implementer_profile":"terra_high_implementer","requested_implementer_model":"gpt-5.6-terra","requested_implementer_reasoning_effort":"high","requested_auditor_profile":"sol_independent_reviewer","requested_model":"gpt-5.6-sol","requested_reasoning_effort":"ultra","requested_execution_environment":"isolated","requested_grader_gold_access":False};request["request_digest"]=digest(request);request_path=root/"request.json";request_path.write_text(json.dumps(request),encoding="utf-8")
        result={"record_type":"mk733j_final_audit_result","audit_request_id":"audit-1","audit_request_digest":request["request_digest"],"audited_head_sha":head,"comparison_base_sha":base,"implementer_profile":"terra_high_implementer","implementer_model":"gpt-5.6-terra","implementer_reasoning_effort":"high","auditor_profile":"sol_independent_reviewer","runtime_model_identity":"gpt-5.6-sol","model":"gpt-5.6-sol","reasoning_effort":"ultra","auditor_thread_id":"audit-thread","verdict":"BLOCKED"};result["result_digest"]=digest(result);result_path=root/"result.json";result_path.write_text(json.dumps(result),encoding="utf-8")
        anchors={"record_type":"mk733j_provider_attestation_trust_registry","registry_version":"test","trusted_attestations":{},"non_claims":["test_only"]};anchor_path=root/"anchors.json"
        def identity(profile_id,thread):
            profile=profiles()[profile_id];model=profile_aliases(profile_id,test_isolated=True)[0] if profile_aliases(profile_id,test_isolated=True) else profile["runtime_model_identity_pattern"]
            common={"profile_id":profile_id,"profile_digest":digest(profile),"runtime_model_identity":model,"model":model,"reasoning_effort":profile["reasoning_effort"],"thread_run_id":thread,"execution_environment":"isolated","grader_gold_access":False,"observed_at":observed,"expires_at":expires}
            att={"record_type":"mk733j_provider_session_attestation","source_class":"cmd_provider_session_attestation","authority_id":"auth-"+profile_id,"issuer_class":"cmd","capability":"final_audit_identity","audit_request_ref":str(request_path),"audit_request_digest":request["request_digest"],"audit_head_sha":head,"comparison_base_sha":base,"audit_result_ref":str(result_path),"audit_result_digest":result["result_digest"],**common};att["attestation_digest"]=digest(att);att_path=root/(profile_id+"-att.json");att_path.write_text(json.dumps(att),encoding="utf-8");anchors["trusted_attestations"][att["authority_id"]]={"attestation_digest":att["attestation_digest"],"issuer_class":"cmd","capability":"final_audit_identity"}
            doc={"record_type":"mk733j_provider_attested_session_identity","source_class":"cmd_provider_attested_session_identity",**common,"source_attestation_ref":str(att_path),"source_attestation_digest":att["attestation_digest"]};doc["envelope_digest"]=digest(doc);path=root/(profile_id+"-identity.json");path.write_text(json.dumps(doc),encoding="utf-8");return path,doc
        implementer_path,implementer=identity("terra_high_implementer","implementer-thread");auditor_path,auditor=identity("sol_independent_reviewer","audit-thread");anchor_path.write_text(json.dumps(anchors),encoding="utf-8")
        value={"final_audit":True,"implementer_identity_ref":str(implementer_path),"implementer_identity_digest":digest(implementer),"auditor_identity_ref":str(auditor_path),"auditor_identity_digest":digest(auditor),"audit_request_ref":str(request_path),"audit_request_digest":request["request_digest"],"audit_head_sha":head,"comparison_base_sha":base,"audit_result_ref":str(result_path),"audit_result_digest":result["result_digest"],"thread_run_id":"audit-thread","runtime_model_identity":"gpt-5.6-sol"}
        prior=qualification.TRUSTED_ATTESTATIONS;qualification.TRUSTED_ATTESTATIONS=anchor_path
        try:
            positive=not final_audit_identity_blocks(value,profiles()["sol_independent_reviewer"],test_isolated=True)
            same=json.loads(json.dumps(value));same["auditor_identity_ref"]=same["implementer_identity_ref"];same["auditor_identity_digest"]=same["implementer_identity_digest"]
            wrong_sha=json.loads(json.dumps(value));wrong_sha["audit_head_sha"]="x"
            expired=json.loads(json.dumps(value));doc=json.loads(auditor_path.read_text());doc["expires_at"]=(now-timedelta(seconds=1)).isoformat().replace("+00:00","Z");doc["envelope_digest"]=digest(doc);auditor_path.write_text(json.dumps(doc),encoding="utf-8");expired["auditor_identity_digest"]=digest(doc);expired_bad=bool(final_audit_identity_blocks(expired,profiles()["sol_independent_reviewer"],test_isolated=True));auditor_path.write_text(json.dumps(auditor),encoding="utf-8")
            controls={"positive_exact_chain":positive,"same_identity_rejected":bool(final_audit_identity_blocks(same,profiles()["sol_independent_reviewer"],test_isolated=True)),"malformed_sha_rejected":bool(final_audit_identity_blocks(wrong_sha,profiles()["sol_independent_reviewer"],test_isolated=True)),"expired_identity_rejected":expired_bad}
        finally: qualification.TRUSTED_ATTESTATIONS=prior
    sdo_controls = sdo_route_self_test()
    controls["sdo_route_precedence_absence"] = sdo_controls["status"] == "PASS_SDO_ROUTE_PRECEDENCE_ABSENCE"
    blocks = [] if all(controls.values()) else ["BLOCKED_FOR_MK733J_FINAL_AUDIT_SELF_TEST"]
    blocks.extend(sdo_controls["blocks"])
    return {"status":"PASS_FINAL_AUDIT_IDENTITY_NEGATIVE_CONTROLS" if not blocks else "FAIL_FINAL_AUDIT_IDENTITY_NEGATIVE_CONTROLS","blocks":sorted(set(blocks)),"controls":controls,"sdo_route":sdo_controls}


def main() -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    route_parser = sub.add_parser("route"); route_parser.add_argument("--request", required=True); route_parser.add_argument("--test-isolated", action="store_true"); route_parser.add_argument("--test-capability-registry"); route_parser.add_argument("--expect-fail", action="store_true")
    critical_parser = sub.add_parser("critical-thread-route"); critical_parser.add_argument("--packet", required=True); critical_parser.add_argument("--phase", required=True, choices=("pre_create", "post_create"))
    receipt_parser = sub.add_parser("issue-receipt"); receipt_parser.add_argument("--request", required=True); receipt_parser.add_argument("--phase", required=True); receipt_parser.add_argument("--test-isolated", action="store_true"); receipt_parser.add_argument("--test-capability-registry")
    verify_parser = sub.add_parser("verify-receipt"); verify_parser.add_argument("--receipt", required=True); verify_parser.add_argument("--phase"); verify_parser.add_argument("--tool"); verify_parser.add_argument("--work-id"); verify_parser.add_argument("--test-isolated", action="store_true"); verify_parser.add_argument("--test-capability-registry")
    consume_parser = sub.add_parser("consume-receipt"); consume_parser.add_argument("--receipt", required=True); consume_parser.add_argument("--phase", required=True); consume_parser.add_argument("--tool", required=True); consume_parser.add_argument("--path", action="append", default=[]); consume_parser.add_argument("--command-class", required=True); consume_parser.add_argument("--operation-digest", required=True); consume_parser.add_argument("--operation-bytes", type=int, required=True); consume_parser.add_argument("--operation-lines", type=int, required=True); consume_parser.add_argument("--test-isolated", action="store_true"); consume_parser.add_argument("--test-capability-registry")
    sdo_self_test_parser = sub.add_parser("sdo-route-self-test"); sdo_self_test_parser.add_argument("--fixture"); sdo_self_test_parser.add_argument("--expect-fail", action="store_true")
    for p in (route_parser, critical_parser, receipt_parser, verify_parser, consume_parser, sub.add_parser("evaluate"), sub.add_parser("final-audit-self-test"), sdo_self_test_parser): p.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:configure_test_capability_registry(getattr(args,"test_capability_registry",None),test_isolated=bool(getattr(args,"test_isolated",False)))
    except ValueError as exc:
        print(json.dumps({"blocks":["BLOCKED_FOR_MK733J_CAPABILITY_REGISTRY_OVERRIDE_INVALID"],"detail":str(exc)},indent=2,sort_keys=True));return 1
    if args.command == "evaluate": result, code = evaluate(), 0
    elif args.command == "final-audit-self-test": result=final_audit_self_test();code=0 if not result["blocks"] else 1
    elif args.command == "critical-thread-route":
        semantic_blocks = critical_thread_route.semantic_compilation_blocks(
            critical_thread_route.load(critical_thread_route.POLICY_PATH)
        )
        result = critical_thread_route.evaluate_route(
            load(Path(args.packet)), phase=args.phase
        )
        result["blocks"] = sorted(set(result["blocks"] + semantic_blocks))
        if semantic_blocks:
            result["decision"] = "STOP_CRITICAL_THREAD_ROUTE"
            result["dispatch_args"] = None
        result["semantic_requirements_compiled"] = not semantic_blocks
        code = 0 if not result["blocks"] else 1
    elif args.command == "route":
        if args.expect_fail:
            result, code = sdo_route_fixture(Path(args.request), expect_fail=True)
        else:
            request = load(Path(args.request))
            if isinstance(request, dict) and isinstance(request.get("request"), dict) and "expected" in request:
                request = request["request"]
            result = route(request,test_isolated=args.test_isolated)
            code = 0 if result["route"] == "allow" else 1
    elif args.command == "sdo-route-self-test":
        if args.fixture:
            result, code = sdo_route_fixture(Path(args.fixture), expect_fail=args.expect_fail)
        else:
            result = sdo_route_self_test(); code = 0 if not result["blocks"] else 1
    elif args.command == "issue-receipt":
        try:
            request = load(Path(args.request)); result, code = receipt(route(request, test_isolated=args.test_isolated), request, args.phase), 0
        except ValueError as exc: result, code = {"blocks": ["BLOCKED_FOR_MK733J_RECEIPT_ROUTE_OR_HANDSHAKE_INVALID"], "detail": str(exc)}, 1
    elif args.command == "verify-receipt":
        receipt_value = load(Path(args.receipt)); result = {"blocks": receipt_blocks(receipt_value, args.phase, args.tool, args.work_id, test_isolated=args.test_isolated), "consumption": {"total": receipt_value.get("budget", {}).get("total"), "remaining": receipt_value.get("budget", {}).get("remaining")}}; code = 0 if not result["blocks"] else 1
    else:
        result = {"blocks": consume_receipt(Path(args.receipt), args.phase, args.tool, observed_paths=args.path, command_class=args.command_class, operation_digest=args.operation_digest, operation_bytes=args.operation_bytes, operation_lines=args.operation_lines, test_isolated=args.test_isolated)}; code = 0 if not result["blocks"] else 1
    print(json.dumps(result, indent=2, sort_keys=True)); return code


if __name__ == "__main__":
    raise SystemExit(main())
