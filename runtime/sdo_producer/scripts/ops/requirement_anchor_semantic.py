#!/usr/bin/env python3
"""Library-only T-206 requirement anchor and semantic admission check.

There is deliberately no CLI entry point.  Production consumers may invoke
``check_boundary`` only at the three named admission boundaries and must pass
the candidate artifact actually carried by that boundary packet.  Tests use
the explicitly named ``canonical_manifest_for_self_test`` and
``mutation_results`` helpers so the corpus cannot become a generic workflow
gate or an implicit production candidate.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "fixtures/mk675/requirement_semantic_check"
CATALOG = CORPUS / "02_REQUIREMENT_CATALOG.json"
BINDINGS = CORPUS / "12_PER_REQUIREMENT_NEGATIVE_BINDINGS.json"
PROVENANCE = CORPUS / "PROVENANCE.json"

BOUNDARIES = frozenset(
    {
        "machine_dispatched_paid_prompt_admission",
        "plan_lock_high_activation",
        "codex_implementation_dispatch",
    }
)
NEUTRAL_WARNING = "SEMANTIC_CHECK_BECAME_BROAD_EVIDENCE_GATE"
RQ_PATTERN = tuple(f"RQ-{number:03d}" for number in range(1, 39))
EXTERNAL_SOURCES = frozenset({"external_receipt", "external_harness"})
IDENTITY_SOURCES = EXTERNAL_SOURCES | {
    "configuration",
    "provider_internal_absent",
    "self_declared",
}
ROUTE_CLASSES = frozenset(
    {
        "unqualified_supervised_local",
        "autonomous",
        "protected",
    }
)
DISPATCH_ORIGINS = frozenset(
    {
        "user_direct",
        "machine_dispatched",
        "ambiguous",
    }
)
EXPECTED_HASHES = {
    "02_REQUIREMENT_CATALOG.json": "a86193106eccd5d02a64bb5d24c90f50d4277978679cbdb389d23b01977a3d74",
    "12_PER_REQUIREMENT_NEGATIVE_BINDINGS.json": "ebdf172d6d3933a28d3ed9c3fd225b65ed0a34e157179458df32131e1a79325a",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_blocks() -> list[str]:
    """Verify exact sealed members and 38/38 catalog/binding cardinality."""
    blocks: list[str] = []
    try:
        provenance = _load(PROVENANCE)
        catalog = _load(CATALOG)
        bindings = _load(BINDINGS)
    except (OSError, json.JSONDecodeError):
        return ["REQUIREMENT_SEMANTIC_CORPUS_UNAVAILABLE"]
    if provenance.get("requirements_seal_sha256") != (
        "98299ca7d4dfc0994341b013e4bbb663e1f51005af1c2cef2b2dac1ea2c63dbc"
    ):
        blocks.append("REQUIREMENT_SEMANTIC_CORPUS_SEAL_MISMATCH")
    for name, expected in EXPECTED_HASHES.items():
        path = CORPUS / name
        if not path.is_file() or _sha256(path) != expected:
            blocks.append(f"REQUIREMENT_SEMANTIC_CORPUS_MEMBER_MISMATCH:{name}")
        if provenance.get("members", {}).get(name) != expected:
            blocks.append(f"REQUIREMENT_SEMANTIC_CORPUS_PROVENANCE_MISMATCH:{name}")
    catalog_ids = [
        row.get("id")
        for row in catalog.get("requirements", [])
        if isinstance(row, dict)
    ]
    binding_ids = [
        row.get("requirement")
        for row in bindings.get("bindings", [])
        if isinstance(row, dict)
    ]
    if tuple(catalog_ids) != RQ_PATTERN:
        blocks.append("REQUIREMENT_CATALOG_NOT_EXACTLY_38")
    if tuple(binding_ids) != RQ_PATTERN:
        blocks.append("REQUIREMENT_NEGATIVE_BINDINGS_NOT_EXACTLY_38")
    return sorted(set(blocks))


def _canonical_manifest() -> dict[str, Any]:
    """Compile the sealed comparison target; never use as a boundary default."""
    catalog = _load(CATALOG)
    bindings = _load(BINDINGS)
    by_id = {row["requirement"]: row for row in bindings["bindings"]}
    requirements = []
    for index, row in enumerate(catalog["requirements"]):
        requirement_id = row["id"]
        requirements.append(
            {
                "requirement_id": requirement_id,
                "quote_ref": (
                    "fixtures/mk675/requirement_semantic_check/"
                    f"02_REQUIREMENT_CATALOG.json#/requirements/{index}/statement"
                ),
                "quote": row["statement"],
                "classification": row["class"],
                "must_map_to": list(row["must_map_to"]),
                "expected_failure": by_id[requirement_id]["expected_failure"],
                "evidence_boundary": by_id[requirement_id]["evidence_boundary"],
            }
        )
    body = {
        "schema_version": "t206-compiled-requirement-manifest.v1",
        "requirements_seal_sha256": (
            "98299ca7d4dfc0994341b013e4bbb663e1f51005af1c2cef2b2dac1ea2c63dbc"
        ),
        "requirement_ids": list(RQ_PATTERN),
        "requirements": requirements,
        "fixed_lifecycle_ref": (
            "research/mk675/fable5_decision_os/critical_thread_route.v1.json"
            "#/required_lifecycle"
        ),
        "optimizable_dimensions_ref": (
            "fixtures/mk675/requirement_semantic_check/"
            "02_REQUIREMENT_CATALOG.json#/optimizable_dimensions"
        ),
        "non_claims": [
            "static_contract_is_not_runtime_firing",
            "admission_is_not_provider_execution",
            "admission_is_not_observed_effective_prevention",
            "admission_is_not_user_or_final_acceptance",
        ],
    }
    body["manifest_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


def canonical_manifest_for_self_test() -> dict[str, Any]:
    """Return the sealed target only for an explicit deterministic self-test."""
    return _canonical_manifest()


def _binding_digest(row: dict[str, Any]) -> str:
    meaning = {
        key: row[key]
        for key in (
            "requirement_id",
            "quote_ref",
            "quote",
            "classification",
            "must_map_to",
            "expected_failure",
            "evidence_boundary",
        )
    }
    return hashlib.sha256(
        json.dumps(meaning, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def production_candidate_for_self_test() -> dict[str, Any]:
    """Build the compact production artifact only for fixture authoring/tests."""
    canonical = _canonical_manifest()
    return {
        "schema_version": "t206-production-candidate-manifest.v1",
        "artifact_id": "t206-sealed-requirement-candidate",
        "requirements_seal_sha256": canonical["requirements_seal_sha256"],
        "requirement_ids": list(RQ_PATTERN),
        "requirement_binding_sha256": {
            row["requirement_id"]: _binding_digest(row)
            for row in canonical["requirements"]
        },
    }


def resolve_candidate_manifest(candidate_artifact: Any) -> Any:
    """Resolve an explicit packet artifact; never infer or synthesize one."""
    if not isinstance(candidate_artifact, dict) or not candidate_artifact:
        return candidate_artifact
    if set(candidate_artifact) != {"artifact_ref", "artifact_sha256"}:
        return candidate_artifact
    ref = candidate_artifact.get("artifact_ref")
    expected = candidate_artifact.get("artifact_sha256")
    if not isinstance(ref, str) or not ref or not isinstance(expected, str):
        return {"candidate_manifest_resolution_error": "INVALID_REFERENCE"}
    path = (REPO / ref).resolve()
    try:
        path.relative_to(REPO)
    except ValueError:
        return {"candidate_manifest_resolution_error": "OUTSIDE_REPOSITORY"}
    try:
        if not path.is_file() or _sha256(path) != expected:
            return {"candidate_manifest_resolution_error": "DIGEST_MISMATCH"}
        return _load(path)
    except (OSError, json.JSONDecodeError):
        return {"candidate_manifest_resolution_error": "UNAVAILABLE"}


def quote_refs(requirement_ids: list[str]) -> list[dict[str, str]]:
    """Resolve ordered, stable quote references for intent/packet anchoring."""
    manifest = _canonical_manifest()
    by_id = {row["requirement_id"]: row for row in manifest["requirements"]}
    if (
        not isinstance(requirement_ids, list)
        or not requirement_ids
        or len(requirement_ids) != len(set(requirement_ids))
        or any(item not in by_id for item in requirement_ids)
    ):
        raise ValueError("REQUIREMENT_IDS_INVALID_OR_UNRESOLVED")
    return [
        {
            "requirement_id": requirement_id,
            "quote_ref": by_id[requirement_id]["quote_ref"],
        }
        for requirement_id in requirement_ids
    ]


def anchor_blocks(
    requirement_ids: Any,
    requirement_quote_refs: Any,
) -> list[str]:
    """Require one resolvable quote ref for every stable requirement ID."""
    try:
        expected = quote_refs(requirement_ids)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return ["REQUIREMENT_ANCHOR_IDS_UNRESOLVED"]
    if requirement_quote_refs != expected:
        return ["REQUIREMENT_ANCHOR_QUOTE_REF_MISMATCH"]
    return []


def semantic_blocks(candidate: Any) -> list[str]:
    """Compare meaning-bearing quote/mapping fields, not marker presence."""
    corpus = corpus_blocks()
    if corpus:
        return corpus
    canonical = _canonical_manifest()
    if not isinstance(candidate, dict):
        return ["REQUIREMENT_COMPILER_ADMISSION_MISSING"]
    if candidate.get("schema_version") == "t206-production-candidate-manifest.v1":
        expected_bindings = {
            row["requirement_id"]: _binding_digest(row)
            for row in canonical["requirements"]
        }
        observed_bindings = candidate.get("requirement_binding_sha256")
        blocks = []
        for requirement_id in RQ_PATTERN:
            if (
                not isinstance(observed_bindings, dict)
                or observed_bindings.get(requirement_id)
                != expected_bindings[requirement_id]
            ):
                expected = next(
                    row
                    for row in canonical["requirements"]
                    if row["requirement_id"] == requirement_id
                )
                blocks.append(expected["expected_failure"])
        if (
            candidate.get("artifact_id") != "t206-sealed-requirement-candidate"
            or candidate.get("requirement_ids") != list(RQ_PATTERN)
            or candidate.get("requirements_seal_sha256")
            != canonical["requirements_seal_sha256"]
            or not isinstance(observed_bindings, dict)
            or set(observed_bindings) != set(RQ_PATTERN)
        ):
            blocks.append("REQUIREMENT_COMPILER_ADMISSION_MISSING")
        return sorted(set(blocks))
    rows = candidate.get("requirements")
    if not isinstance(rows, list):
        return ["REQUIREMENT_COMPILER_ADMISSION_MISSING"]
    canonical_by_id = {
        row["requirement_id"]: row for row in canonical["requirements"]
    }
    candidate_by_id: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        requirement_id = row.get("requirement_id")
        if requirement_id in candidate_by_id:
            duplicates.add(str(requirement_id))
        if isinstance(requirement_id, str):
            candidate_by_id[requirement_id] = row
    blocks: list[str] = []
    for requirement_id in RQ_PATTERN:
        expected = canonical_by_id[requirement_id]
        observed = candidate_by_id.get(requirement_id)
        if (
            observed is None
            or requirement_id in duplicates
            or observed.get("quote_ref") != expected["quote_ref"]
            or observed.get("quote") != expected["quote"]
            or observed.get("classification") != expected["classification"]
            or observed.get("must_map_to") != expected["must_map_to"]
            or observed.get("expected_failure") != expected["expected_failure"]
            or observed.get("evidence_boundary") != expected["evidence_boundary"]
        ):
            blocks.append(expected["expected_failure"])
    if candidate.get("requirement_ids") != list(RQ_PATTERN):
        blocks.append("REQUIREMENT_COMPILER_ADMISSION_MISSING")
    if candidate.get("requirements_seal_sha256") != canonical[
        "requirements_seal_sha256"
    ]:
        blocks.append("REQUIREMENT_COMPILER_ADMISSION_MISSING")
    return sorted(set(blocks))


def identity_blocks(
    identity_attestation: Any,
    *,
    route_class: str,
) -> tuple[list[str], list[dict[str, str]]]:
    """Prefer external attestations and preserve truthful unobservability."""
    if route_class not in ROUTE_CLASSES:
        return ["ADMISSION_ROUTE_CLASS_INVALID"], []
    if not isinstance(identity_attestation, dict):
        if route_class == "unqualified_supervised_local":
            return [], [
                {
                    "fact": "provider_internal_identity",
                    "state": "unobservable",
                    "source": "provider_internal_absent",
                }
            ]
        return ["EXTERNAL_IDENTITY_ATTESTATION_REQUIRED"], []
    configured = identity_attestation.get("configured")
    observed = identity_attestation.get("observed")
    facts = (configured, observed)
    if any(
        not isinstance(fact, dict)
        or fact.get("source") not in IDENTITY_SOURCES
        for fact in facts
    ):
        return ["IDENTITY_ATTESTATION_SHAPE_INVALID"], []
    observations: list[dict[str, str]] = []
    sources = {fact["source"] for fact in facts}
    if "provider_internal_absent" in sources:
        observations.append(
            {
                "fact": "provider_internal_identity",
                "state": "unobservable",
                "source": "provider_internal_absent",
            }
        )
        if route_class != "unqualified_supervised_local":
            return ["EXTERNAL_IDENTITY_ATTESTATION_REQUIRED"], observations
    if "self_declared" in sources and route_class in {"autonomous", "protected"}:
        return ["SELF_DECLARED_IDENTITY_INSUFFICIENT"], observations
    if "provider_internal_absent" not in sources and "self_declared" not in sources:
        if (
            configured.get("source") != "configuration"
            or observed.get("source") not in EXTERNAL_SOURCES
            or not configured.get("ref")
            or not observed.get("ref")
            or configured.get("ref") == observed.get("ref")
        ):
            return ["IDENTITY_ATTESTATION_DISTINCT_SOURCES_REQUIRED"], observations
        if configured.get("value") != observed.get("value"):
            return ["EXTERNAL_IDENTITY_ATTESTATION_MISMATCH"], observations
    elif route_class in {"autonomous", "protected"} and (
        configured.get("source") == "provider_internal_absent"
        or observed.get("source") == "provider_internal_absent"
    ):
        return ["EXTERNAL_IDENTITY_ATTESTATION_REQUIRED"], observations
    return [], observations


def approval_scope_blocks(
    *,
    dispatch_origin: str,
    paid_work: bool,
    approval_artifact_present: bool,
    user_owned_session: bool,
    user_session_source: str,
    authority_gate_required: bool,
    authority_gate_satisfied: bool,
) -> list[str]:
    """Partition prompt approval without weakening any Authority Gate."""
    if dispatch_origin not in DISPATCH_ORIGINS:
        return ["DISPATCH_ORIGIN_INVALID"]
    blocks: list[str] = []
    direct_owned = (
        dispatch_origin == "user_direct"
        and user_owned_session
        and user_session_source in EXTERNAL_SOURCES
    )
    if paid_work and not direct_owned and not approval_artifact_present:
        blocks.append("EXACT_FABLE_PROMPT_APPROVAL_MISSING")
    if (
        dispatch_origin == "user_direct"
        and user_owned_session
        and user_session_source not in EXTERNAL_SOURCES
    ):
        blocks.append("USER_DIRECT_SESSION_OWNERSHIP_UNATTESTED")
    if authority_gate_required and not authority_gate_satisfied:
        blocks.append("AUTHORITY_GATE_REQUIRED_UNAFFECTED_BY_DISPATCH_ORIGIN")
    return sorted(set(blocks))


def check_boundary(
    boundary: str,
    *,
    candidate_manifest: Any = None,
    identity_attestation: Any = None,
    route_class: str = "protected",
    dispatch_origin: str = "machine_dispatched",
    paid_work: bool = False,
    approval_artifact_present: bool = True,
    user_owned_session: bool = False,
    user_session_source: str = "external_harness",
    authority_gate_required: bool = False,
    authority_gate_satisfied: bool = True,
) -> dict[str, Any]:
    """Run only at one of the three narrow admission boundaries."""
    if boundary not in BOUNDARIES:
        return {
            "decision": "NEUTRAL_OUTSIDE_SEMANTIC_BOUNDARIES",
            "boundary": boundary,
            "blocks": [],
            "warnings": [NEUTRAL_WARNING],
            "observations": [],
            "claim_scope": "unrelated_work_continues",
        }
    missing_candidate = (
        candidate_manifest is None
        or candidate_manifest == ""
        or candidate_manifest == []
        or candidate_manifest == {}
    )
    blocks = (
        ["BLOCKED_FOR_MISSING_CANDIDATE_MANIFEST"]
        if missing_candidate
        else semantic_blocks(candidate_manifest)
    )
    identity, observations = identity_blocks(
        identity_attestation,
        route_class=route_class,
    )
    blocks.extend(identity)
    blocks.extend(
        approval_scope_blocks(
            dispatch_origin=dispatch_origin,
            paid_work=paid_work,
            approval_artifact_present=approval_artifact_present,
            user_owned_session=user_owned_session,
            user_session_source=user_session_source,
            authority_gate_required=authority_gate_required,
            authority_gate_satisfied=authority_gate_satisfied,
        )
    )
    return {
        "decision": (
            "ALLOW_BOUNDARY_CLAIM"
            if not blocks
            else "BLOCK_ONLY_THIS_ADMISSION_CLAIM"
        ),
        "boundary": boundary,
        "blocks": sorted(set(blocks)),
        "warnings": [],
        "observations": observations,
        "claim_scope": "three_named_admission_boundaries_only",
    }


def mutation_results() -> list[dict[str, Any]]:
    """Apply one meaning-changing mutation per sealed RQ deterministically."""
    canonical = canonical_manifest_for_self_test()
    bindings = _load(BINDINGS)["bindings"]
    results: list[dict[str, Any]] = []
    mutation_kinds = ("remove", "substitute_quote", "demote_mapping")
    for index, binding in enumerate(bindings):
        candidate = copy.deepcopy(canonical)
        requirement_id = binding["requirement"]
        row_index = next(
            offset
            for offset, row in enumerate(candidate["requirements"])
            if row["requirement_id"] == requirement_id
        )
        mutation_kind = mutation_kinds[index % len(mutation_kinds)]
        if mutation_kind == "remove":
            candidate["requirements"].pop(row_index)
        elif mutation_kind == "substitute_quote":
            candidate["requirements"][row_index]["quote"] = (
                "Optional generic guidance may be used when convenient."
            )
        else:
            candidate["requirements"][row_index]["must_map_to"] = []
        observed = semantic_blocks(candidate)
        expected = binding["expected_failure"]
        results.append(
            {
                "requirement_id": requirement_id,
                "mutation_kind": mutation_kind,
                "expected_failure": expected,
                "observed_blocks": observed,
                "passed": observed == [expected],
            }
        )
    return results
