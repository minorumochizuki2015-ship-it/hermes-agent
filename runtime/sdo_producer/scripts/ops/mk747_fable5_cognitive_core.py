#!/usr/bin/env python3
"""Deterministic MK747-P1 shadow cognitive decision evaluator.

This module evaluates a machine-readable *current* decision.  It does not call
an LLM, accept model self-scores, promote learned genes, mutate authority, or
block ordinary supervised work.  A failure withholds only the unsupported
shadow recommendation and emits a replayable receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "schemas" / "mk675" / "mk747_cognitive_decision.v1.schema.json"
EVALUATOR_VERSION = "mk747-p1-cognitive-shadow-v1"
HERMES_CREATIVE_SKILL_VERSION = "2.1.0"
HERMES_CREATIVE_METHODS = {
    "inversion",
    "analogy",
    "constraint_reframing",
    "combination",
}
HERMES_CREATIVE_RESULT_KEYS = {
    "statusCode",
    "accepted",
    "completed",
    "running",
    "messageCount",
    "eventCount",
    "candidateCount",
    "creativeSkillVersion",
    "candidates",
    "discriminationStatus",
}
HERMES_CREATIVE_CANDIDATE_KEYS = {"id", "title", "summary", "method"}
HERMES_CREATIVE_PROJECTION_ABSENT = object()
CREATIVE_UNSAFE_TEXT_RE = re.compile(
    r"(?:secret|credential|password|passphrase|api[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|bearer|private[_ -]?key|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|provider[_ -]?payload|raw[_ -]?(?:prompt|text|log)|"
    r"terminal[_ -]?log|(?:^|\s)(?:/Users/|/home/|[A-Za-z]:\\|https?://)|"
    r"-----BEGIN [A-Z ]+-----)",
    re.IGNORECASE,
)

DIMENSIONS = ("iq", "eq", "ultracode", "overview", "innovation", "discovery")
REQUIRED_OPTION_CLASSES = {
    "content_capability_action",
    "lower_cost_existing_primitive",
    "stop_or_no_action",
    "wrong_lane_or_evidence_only",
}
REQUIRED_TWO_OPTION_CLASSES = {
    "content_capability_action",
    "lower_cost_existing_primitive",
}
OPTION_SEMANTICS = {
    "content_capability_action": ({"capability"}, {"direct_capability"}),
    "lower_cost_existing_primitive": ({"existing_primitive"}, {"enabling_capability"}),
    "stop_or_no_action": ({"stop"}, {"no_user_delta"}),
    "wrong_lane_or_evidence_only": (
        {"inventory", "evidence_only"},
        {"support_only", "no_user_delta"},
    ),
}
REQUIRED_NON_CLAIMS = {
    "shadow_receipt_is_not_runtime_firing",
    "no_iq_or_eq_improvement_claim",
    "no_agi_completion",
    "no_model_qualification",
    "no_product_or_user_acceptance",
    "no_protected_integration_or_authority_mutation",
    "no_automatic_gene_promotion",
}
CAUSAL_VALUE = {
    "direct_capability": 30,
    "enabling_capability": 20,
    "support_only": 5,
    "no_user_delta": 0,
}
PROHIBITED_KEYS = {
    "chain_of_thought",
    "raw_chain_of_thought",
    "private_prompt",
    "raw_prompt",
    "credential",
    "credentials",
    "password",
    "api_key",
    "access_token",
    "model_self_score",
    "model_iq_score",
    "model_eq_score",
}
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|AKIA[A-Z0-9]{8,}|"
    r"xox[baprs]-|BEGIN [A-Z ]*PRIVATE KEY)"
)
SENSITIVE_TEXT_RE = re.compile(
    r"(?:raw[_ -]*private[_ -]*prompt|private[_ -]*prompt|"
    r"hidden[_ -]*chain[_ -]*of[_ -]*thought|chain[_ -]*of[_ -]*thought)",
    re.IGNORECASE,
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=".mk747-cognitive-receipt-"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _empty(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or value == []
        or value == {}
    )


def _resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported non-local schema ref: {ref}")
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"unresolved schema ref: {ref}")
        current = current[part]
    if not isinstance(current, dict):
        raise ValueError(f"schema ref is not an object: {ref}")
    return current


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    """Validate the JSON-schema subset used by the MK747 contract.

    The repository intentionally has no runtime jsonschema dependency.  This
    checker supports local refs, type, required, properties,
    additionalProperties, enum, const, minItems, maxItems, and uniqueItems.
    Semantic quality remains in the evaluator below.
    """

    root_schema = root_schema or schema
    if "$ref" in schema:
        try:
            schema = _resolve_ref(root_schema, schema["$ref"])
        except ValueError:
            return [f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}:unresolved_ref"]

    blocks: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(_type_matches(value, item) for item in expected_types):
            return [f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}:type"]

    if "const" in schema and value != schema["const"]:
        blocks.append(f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}:const")
    if "enum" in schema and value not in schema["enum"]:
        blocks.append(f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}:enum")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                blocks.append(
                    f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}.{required}:required"
                )
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    blocks.append(
                        f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}.{key}:additional"
                    )
        for key, child_schema in properties.items():
            if key in value:
                blocks.extend(
                    validate_schema(
                        value[key], child_schema, root_schema, f"{path}.{key}"
                    )
                )

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            blocks.append(f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}:minItems")
        if isinstance(maximum, int) and len(value) > maximum:
            blocks.append(f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}:maxItems")
        if schema.get("uniqueItems") is True:
            serialized = [
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value
            ]
            if len(serialized) != len(set(serialized)):
                blocks.append(f"BLOCKED_FOR_MK747_SCHEMA_INVALID:{path}:uniqueItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                blocks.extend(
                    validate_schema(
                        item, item_schema, root_schema, f"{path}[{index}]"
                    )
                )

    return sorted(set(blocks))


def _contains_prohibited_content(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in PROHIBITED_KEYS or _contains_prohibited_content(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_prohibited_content(item) for item in value)
    return isinstance(value, str) and bool(
        SECRET_RE.search(value) or SENSITIVE_TEXT_RE.search(value)
    )


def _safe_creative_text(value: Any, maximum_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum_length
        and not re.search(r"[\x00-\x1f\x7f]", value)
        and not CREATIVE_UNSAFE_TEXT_RE.search(value)
    )


def _validate_hermes_creative_projection(
    projection: Any,
) -> tuple[list[str], list[dict[str, str]]]:
    """Admit only the already-sanitized Hermes creative projection shape.

    This validates an upstream advisory projection; it does not invoke Hermes,
    qualify a model, infer affinities, rank candidates, or grant authority.
    """

    malformed = "BLOCKED_FOR_MK747_HERMES_CREATIVE_PROJECTION_MALFORMED"
    unsafe = "BLOCKED_FOR_MK747_HERMES_CREATIVE_PROJECTION_UNSAFE"
    duplicate = "BLOCKED_FOR_MK747_HERMES_CREATIVE_PROJECTION_DUPLICATE"
    under_three = "BLOCKED_FOR_MK747_HERMES_CREATIVE_PROJECTION_UNDER_THREE"
    qualification = (
        "BLOCKED_FOR_MK747_HERMES_CREATIVE_PROJECTION_QUALIFICATION_MISMATCH"
    )
    if _contains_prohibited_content(projection):
        return [unsafe], []
    if (
        not isinstance(projection, dict)
        or not set(projection) <= HERMES_CREATIVE_RESULT_KEYS
    ):
        return [malformed], []

    blocks: list[str] = []
    status_code = projection.get("statusCode")
    if (
        (
            "statusCode" in projection
            and (
                not isinstance(status_code, str)
                or status_code
                not in {
                    "accepted",
                    "queued",
                    "running",
                    "completed",
                    "interrupted",
                }
            )
        )
        or any(
            key in projection and not isinstance(projection[key], bool)
            for key in ("accepted", "completed", "running")
        )
        or any(
            key in projection
            and (
                not isinstance(projection[key], int)
                or isinstance(projection[key], bool)
                or projection[key] < 0
            )
            for key in ("messageCount", "eventCount")
        )
    ):
        blocks.append(malformed)
    if (
        projection.get("creativeSkillVersion") != HERMES_CREATIVE_SKILL_VERSION
        or projection.get("discriminationStatus")
        != "no_admissible_discrimination"
    ):
        blocks.append(qualification)

    candidates = projection.get("candidates")
    if not isinstance(candidates, list):
        return sorted(set(blocks + [malformed])), []
    if len(candidates) < 3:
        blocks.append(under_three)
    if len(candidates) > 8 or projection.get("candidateCount") != len(candidates):
        blocks.append(malformed)

    admitted: list[dict[str, str]] = []
    ids: set[str] = set()
    titles: set[str] = set()
    summaries: set[str] = set()
    has_required_method = False
    for candidate in candidates:
        if (
            not isinstance(candidate, dict)
            or set(candidate) != HERMES_CREATIVE_CANDIDATE_KEYS
        ):
            blocks.append(malformed)
            continue
        candidate_id = candidate.get("id")
        title = candidate.get("title")
        summary = candidate.get("summary")
        method = candidate.get("method")
        if (
            not _safe_creative_text(candidate_id, 64)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", candidate_id)
            or not _safe_creative_text(title, 120)
            or not _safe_creative_text(summary, 280)
            or not isinstance(method, str)
            or method not in HERMES_CREATIVE_METHODS
        ):
            blocks.append(
                unsafe
                if any(
                    isinstance(value, str)
                    and CREATIVE_UNSAFE_TEXT_RE.search(value)
                    for value in (candidate_id, title, summary)
                )
                else malformed
            )
            continue
        identity = candidate_id.casefold()
        title_identity = title.casefold()
        summary_identity = summary.casefold()
        if (
            identity in ids
            or title_identity in titles
            or summary_identity in summaries
        ):
            blocks.append(duplicate)
            continue
        ids.add(identity)
        titles.add(title_identity)
        summaries.add(summary_identity)
        has_required_method = has_required_method or method in {
            "inversion",
            "analogy",
        }
        admitted.append(
            {"id": candidate_id, "title": title, "summary": summary, "method": method}
        )
    if not has_required_method:
        blocks.append(malformed)
    if blocks:
        return sorted(set(blocks)), []
    return [], admitted


def _accepted_affinity_selection(
    affinities: Any, option_ids: set[str]
) -> tuple[str | None, list[dict[str, Any]]]:
    """Use caller-accepted affinities only when they uniquely discriminate.

    Missing, partial, malformed, duplicate, unknown, non-finite, or tied input
    is deliberately non-discriminating.  No score is derived from Hermes.
    """

    if not isinstance(affinities, list) or len(affinities) < 2:
        return None, []
    admitted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in affinities:
        if not isinstance(row, dict) or set(row) != {"option_id", "affinity"}:
            return None, []
        option_id = row.get("option_id")
        affinity = row.get("affinity")
        if (
            not isinstance(option_id, str)
            or option_id not in option_ids
            or option_id in seen
            or not isinstance(affinity, (int, float))
            or isinstance(affinity, bool)
            or (isinstance(affinity, float) and not math.isfinite(affinity))
            or affinity < 0
            or affinity > 1
        ):
            return None, []
        seen.add(option_id)
        admitted.append({"option_id": option_id, "affinity": float(affinity)})
    if seen != option_ids:
        return None, []
    maximum = max(row["affinity"] for row in admitted)
    winners = [row["option_id"] for row in admitted if row["affinity"] == maximum]
    return (winners[0] if len(winners) == 1 else None), admitted


def _repo_file_ref_exists(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    file_ref = value.split("#", 1)[0].strip()
    if not file_ref:
        return False
    candidate = Path(file_ref)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    try:
        return (REPO / candidate).is_file()
    except OSError:
        return False


def _assessment(score: int, signals: list[str]) -> dict[str, Any]:
    if score >= 8:
        status = "pass"
    elif score >= 4:
        status = "partial"
    else:
        status = "fail"
    return {"score": score, "status": status, "signals": signals}


def _score_iq(
    option: dict[str, Any],
    fact_ids: set[str],
    counterevidence_ids: set[str],
    uncertainty_ids: set[str],
) -> dict[str, Any]:
    iq = option["cognitive_dimensions"]["iq"]
    score = 0
    signals: list[str] = []
    if set(option["fact_refs"]) & fact_ids:
        score += 2
        signals.append("verified_fact_reference")
    if set(option["counterevidence_refs"]) & counterevidence_ids:
        score += 2
        signals.append("counterevidence_considered")
    if set(option["uncertainty_refs"]) & uncertainty_ids:
        score += 1
        signals.append("uncertainty_bounded")
    if len(iq["causal_chain"]) >= 3:
        score += 2
        signals.append("causal_chain_present")
    if not _empty(iq["counterfactual"]):
        score += 1
        signals.append("counterfactual_present")
    if not _empty(iq["contradiction_check"]):
        score += 1
        signals.append("contradiction_check_present")
    if not _empty(iq["fake_pass_check"]):
        score += 1
        signals.append("fake_pass_check_present")
    return _assessment(score, signals)


def _score_eq(
    option: dict[str, Any], intent_id: str, prior_action: str
) -> dict[str, Any]:
    eq = option["cognitive_dimensions"]["eq"]
    score = 0
    signals: list[str] = []
    if eq["intent_ref"] == intent_id:
        score += 2
        signals.append("current_intent_bound")
    if eq["correction_effect"] == "changes_action":
        score += 3
        signals.append("correction_changes_action")
    action_change = eq["action_change"].strip()
    if action_change and action_change != prior_action.strip():
        score += 2
        signals.append("action_delta_explicit")
    if not _empty(eq["trust_or_friction_effect"]):
        score += 1
        signals.append("trust_or_friction_effect_explicit")
    if not _empty(eq["explanation"]):
        score += 2
        signals.append("user_explanation_present")
    return _assessment(score, signals)


def _score_ultracode(option: dict[str, Any]) -> dict[str, Any]:
    ultracode = option["cognitive_dimensions"]["ultracode"]
    chain = ultracode["consumer_chain"]
    score = 0
    signals: list[str] = []
    if _repo_file_ref_exists(ultracode["source_ref"]):
        score += 1
        signals.append("source_bound")
    if not _empty(ultracode["first_failing_predicate"]):
        score += 1
        signals.append("first_failing_predicate")
    if not _empty(ultracode["enforcement_layer"]):
        score += 1
        signals.append("enforcement_layer_match")
    if ultracode["change_closure"]:
        score += 1
        signals.append("change_closure")
    if ultracode["risk_derived_tests"]:
        score += 1
        signals.append("risk_derived_tests")
    if not _empty(ultracode["rollback"]):
        score += 2
        signals.append("rollback_present")
    if all(
        not _empty(chain[field])
        for field in (
            "producer",
            "owning_operation",
            "actual_consumer",
            "last_mile",
            "observable_result",
        )
    ):
        score += 3
        signals.append("consumer_last_mile_complete")
    return _assessment(score, signals)


def _score_overview(option: dict[str, Any]) -> dict[str, Any]:
    overview = option["cognitive_dimensions"]["overview"]
    score = 0
    signals: list[str] = []
    if overview["grand_goal_preserved"] is True:
        score += 2
        signals.append("grand_goal_preserved")
    if overview["normal_user_loop_preserved"] is True:
        score += 1
        signals.append("normal_user_loop_preserved")
    if overview["dependencies"]:
        score += 1
        signals.append("dependencies_explicit")
    if not _empty(overview["time_horizon"]):
        score += 1
        signals.append("time_horizon_explicit")
    if isinstance(overview["cost_units"], int) and overview["cost_units"] >= 0:
        score += 1
        signals.append("cost_explicit")
    if not _empty(overview["reversibility"]):
        score += 2
        signals.append("reversibility_explicit")
    if overview["downstream_effects"]:
        score += 2
        signals.append("downstream_effects_explicit")
    return _assessment(score, signals)


def _bounded_experiment_complete(experiment: dict[str, Any]) -> bool:
    budget = experiment.get("budget", {})
    required_text = (
        "experiment_id",
        "hypothesis",
        "consumer",
        "method",
        "success_metric",
        "success_threshold",
        "stop_condition",
        "expiry",
        "rollback",
        "feasibility_check",
        "value_hypothesis",
    )
    return (
        all(not _empty(experiment.get(field)) for field in required_text)
        and isinstance(budget, dict)
        and isinstance(budget.get("max_actions"), int)
        and budget["max_actions"] > 0
        and isinstance(budget.get("max_minutes"), int)
        and budget["max_minutes"] > 0
    )


def _score_innovation(
    option: dict[str, Any], option_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    innovation = option["cognitive_dimensions"]["innovation"]
    score = 0
    signals: list[str] = []
    if not _empty(innovation["hypothesis"]):
        score += 1
        signals.append("hypothesis_present")
    if not _empty(innovation["constraint_preserved"]):
        score += 1
        signals.append("constraint_preserved")
    if not _empty(innovation["value"]):
        score += 2
        signals.append("value_explicit")
    if not _empty(innovation["feasibility"]):
        score += 2
        signals.append("feasibility_explicit")
    cheaper_id = innovation["cheaper_existing_primitive_option_id"]
    cheaper = option_by_id.get(cheaper_id)
    if cheaper and cheaper.get("option_class") == "lower_cost_existing_primitive":
        score += 1
        signals.append("cheaper_existing_primitive_compared")
    if _bounded_experiment_complete(innovation["bounded_experiment"]):
        score += 3
        signals.append("bounded_experiment_complete")
    return _assessment(score, signals)


def _score_discovery(option: dict[str, Any]) -> dict[str, Any]:
    discovery = option["cognitive_dimensions"]["discovery"]
    experiment = option["cognitive_dimensions"]["innovation"]["bounded_experiment"]
    score = 0
    signals: list[str] = []
    if not _empty(discovery["observation"]):
        score += 2
        signals.append("observation_present")
    if not _empty(discovery["unmet_need"]):
        score += 2
        signals.append("unmet_need_present")
    if not _empty(discovery["feature_hypothesis"]):
        score += 2
        signals.append("feature_hypothesis_present")
    if discovery["experiment_ref"] == experiment["experiment_id"]:
        score += 1
        signals.append("experiment_linked")
    if not _empty(discovery["adoption_metric"]):
        score += 2
        signals.append("adoption_metric_present")
    if not _empty(discovery["retirement_condition"]):
        score += 1
        signals.append("retirement_condition_present")
    return _assessment(score, signals)


def _prior_assessments(
    decision: dict[str, Any], option_ids: set[str], blocks: list[str]
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    policy = decision["prior_policy"]
    measured_outcomes = decision["measured_outcomes"]
    prior_ids = [row["prior_id"] for row in decision["priors"]]
    prior_ids_unique = len(prior_ids) == len(set(prior_ids))
    if not prior_ids_unique:
        blocks.append("BLOCKED_FOR_MK747_PRIOR_ID_DUPLICATE")
    outcome_ids = [row["outcome_id"] for row in measured_outcomes]
    outcome_ids_unique = len(outcome_ids) == len(set(outcome_ids))
    outcome_by_id = {row["outcome_id"]: row for row in measured_outcomes}
    if not outcome_ids_unique:
        blocks.append("BLOCKED_FOR_MK747_MEASURED_PRIOR_LINEAGE_INVALID")
    applied_by_option = {option_id: 0.0 for option_id in option_ids}
    assessments: list[dict[str, Any]] = []
    policy_valid = (
        policy["proposed_or_unmeasured_influence"] == 0
        and policy["bounded_prior_max_influence"] == 5
        and policy["option_removal_allowed"] is False
    )
    if not policy_valid:
        blocks.append("BLOCKED_FOR_MK747_PRIOR_POLICY_UNBOUNDED")

    for prior in decision["priors"]:
        status = prior["status"]
        targets = prior["target_option_ids"]
        targets_unique = len(targets) == len(set(targets))
        applied = 0.0
        disposition = "zero_influence"
        if not targets_unique:
            blocks.append("BLOCKED_FOR_MK747_PRIOR_TARGET_DUPLICATE")
        if any(target not in option_ids for target in targets):
            blocks.append("BLOCKED_FOR_MK747_PRIOR_TARGET_UNKNOWN")
        if prior["remove_option_ids"]:
            blocks.append("BLOCKED_FOR_MK747_OPTION_DELETION_BY_PRIOR")
        if status in {"proposed", "unmeasured"}:
            if prior["promotion_requested"]:
                blocks.append("BLOCKED_FOR_MK747_PROPOSED_PRIOR_SELF_PROMOTION")
        else:
            cap = prior["influence_cap"]
            requested = prior["requested_influence"]
            outcome = outcome_by_id.get(prior["measured_outcome_ref"])
            lineage_valid = (
                prior_ids_unique
                and outcome_ids_unique
                and outcome is not None
                and outcome["prior_id"] == prior["prior_id"]
                and outcome["status"] == "accepted"
                and outcome["measurement_state"] == "measured"
                and not _empty(outcome["metric"])
                and not _empty(outcome["observed_value"])
                and _repo_file_ref_exists(outcome["source_ref"])
            )
            bounded = (
                lineage_valid
                and 0 <= requested <= cap
                and 0 <= cap <= policy["bounded_prior_max_influence"]
            )
            if not prior_ids_unique:
                disposition = "zero_influence_duplicate_prior_id"
            elif not lineage_valid:
                blocks.append("BLOCKED_FOR_MK747_MEASURED_PRIOR_LINEAGE_INVALID")
            elif not bounded:
                blocks.append("BLOCKED_FOR_MK747_BOUNDED_PRIOR_CAP_EXCEEDED")
            elif not targets_unique:
                disposition = "zero_influence_duplicate_target"
            elif prior["promotion_requested"]:
                blocks.append(
                    "BLOCKED_FOR_MK747_AUTOMATIC_PROMOTION_OR_AUTHORITY_MUTATION"
                )
            else:
                applied = float(requested)
                disposition = "bounded_measured_influence_applied"
                for target in targets:
                    if target in applied_by_option:
                        applied_by_option[target] += applied
        assessments.append(
            {
                "prior_id": prior["prior_id"],
                "status": status,
                "target_option_ids": targets,
                "requested_influence": prior["requested_influence"],
                "disclosed_cap": prior["influence_cap"],
                "applied_influence_per_target": applied,
                "disposition": disposition,
                "option_removal_applied": False,
                "promotion_applied": False,
            }
        )
    maximum = policy["bounded_prior_max_influence"]
    if any(total > maximum for total in applied_by_option.values()):
        blocks.append("BLOCKED_FOR_MK747_CUMULATIVE_PRIOR_CAP_EXCEEDED")
        applied_by_option = {option_id: 0.0 for option_id in option_ids}
        for row in assessments:
            if row["applied_influence_per_target"]:
                row["applied_influence_per_target"] = 0.0
                row["disposition"] = "zero_influence_cumulative_cap_exceeded"
    return assessments, applied_by_option


def _semantic_blocks_and_assessments(
    decision: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    blocks: list[str] = []
    current = decision["current_decision"]
    authority = current["authority"]
    facts = current["current_facts"]
    counterevidence = current["counterevidence"]
    uncertainties = current["uncertainties"]
    correction = current["user_correction"]
    intent = current["user_intent"]
    goal = current["goal"]
    options = decision["candidate_options"]

    lineage_ids = (
        ([row["fact_id"] for row in facts], "fact"),
        ([row["counterevidence_id"] for row in counterevidence], "counterevidence"),
        ([row["uncertainty_id"] for row in uncertainties], "uncertainty"),
    )
    for identifiers, _lineage_kind in lineage_ids:
        if len(identifiers) != len(set(identifiers)):
            blocks.append("BLOCKED_FOR_MK747_LINEAGE_ID_DUPLICATE")

    if _empty(decision["grand_goal_ref"]) or any(
        _empty(goal[field])
        for field in (
            "goal_id",
            "normal_user_outcome",
            "user_loop",
            "success_condition",
        )
    ):
        blocks.append("BLOCKED_FOR_MK747_GOAL_CONTEXT_MISSING")
    if any(_empty(intent[field]) for field in ("intent_id", "statement", "priority")):
        blocks.append("BLOCKED_FOR_MK747_USER_INTENT_INVALID")
    if any(
        _empty(correction[field])
        for field in (
            "correction_id",
            "prior_action",
            "correction",
            "required_action_change",
        )
    ):
        blocks.append("BLOCKED_FOR_MK747_USER_CORRECTION_INVALID")
    if not facts:
        blocks.append("BLOCKED_FOR_MK747_CURRENT_FACTS_MISSING")
    if facts and any(
        _empty(row["fact_id"])
        or _empty(row["statement"])
        or row["confidence"] != "verified"
        or not _repo_file_ref_exists(row["source_ref"])
        for row in facts
    ):
        blocks.append("BLOCKED_FOR_MK747_CURRENT_FACTS_UNVERIFIED_OR_EMPTY")
    if not counterevidence or not uncertainties:
        blocks.append("BLOCKED_FOR_MK747_COUNTEREVIDENCE_OR_UNCERTAINTY_MISSING")
    if counterevidence and any(
        _empty(row["counterevidence_id"])
        or _empty(row["statement"])
        or not row["affects_option_ids"]
        for row in counterevidence
    ):
        blocks.append("BLOCKED_FOR_MK747_COUNTEREVIDENCE_INVALID")
    if uncertainties and any(
        _empty(row["uncertainty_id"])
        or _empty(row["statement"])
        or _empty(row["effect_on_selection"])
        or _empty(row["resolution_or_stop"])
        for row in uncertainties
    ):
        blocks.append("BLOCKED_FOR_MK747_UNCERTAINTY_INVALID")
    if (
        authority["ordinary_supervised_work"] != "continue"
        or authority["unsupported_recommendation_scope"] != "recommendation_only"
    ):
        blocks.append("BLOCKED_FOR_MK747_GLOBAL_OVER_GATING")
    if authority["mode"] != "shadow_only":
        blocks.append("BLOCKED_FOR_MK747_NON_SHADOW_MODE")
    if (
        authority["automatic_gene_promotion"] is not False
        or authority["authority_mutation"] is not False
    ):
        blocks.append("BLOCKED_FOR_MK747_AUTOMATIC_PROMOTION_OR_AUTHORITY_MUTATION")
    if not REQUIRED_NON_CLAIMS <= set(decision["required_non_claims"]):
        blocks.append("BLOCKED_FOR_MK747_NON_CLAIMS_MISSING")
    if _contains_prohibited_content(decision):
        blocks.append("BLOCKED_FOR_MK747_PRIVATE_OR_SELF_SCORE_CONTENT")

    option_ids = [option["option_id"] for option in options]
    option_id_set = set(option_ids)
    if len(option_ids) != len(option_id_set):
        blocks.append("BLOCKED_FOR_MK747_DUPLICATE_OPTION_ID")
    option_classes = {option["option_class"] for option in options}
    required_option_classes = (
        REQUIRED_TWO_OPTION_CLASSES if len(options) == 2 else REQUIRED_OPTION_CLASSES
    )
    if option_classes != required_option_classes:
        blocks.append("BLOCKED_FOR_MK747_OPTION_CLASSES_INCOMPLETE")
    for option in options:
        allowed_actions, allowed_causal_deltas = OPTION_SEMANTICS.get(
            option["option_class"], (set(), set())
        )
        if (
            option["action_kind"] not in allowed_actions
            or option["user_value"]["causal_delta"] not in allowed_causal_deltas
        ):
            blocks.append("BLOCKED_FOR_MK747_OPTION_CLASS_CAUSAL_SEMANTICS_INVALID")
        if any(
            _empty(value)
            for value in (
                option["option_id"],
                option["description"],
                option["implementation_action"],
                option["consideration_reason"],
                option["user_value"]["normal_user_can_now"],
                option["user_value"]["causal_mechanism"],
                option["user_value"]["measurement"],
                option["stop_condition"],
            )
        ):
            blocks.append("BLOCKED_FOR_MK747_OPTION_ACTION_OR_USER_VALUE_MISSING")
        if option["cognitive_dimensions"]["eq"]["intent_ref"] != intent["intent_id"]:
            blocks.append("BLOCKED_FOR_MK747_USER_INTENT_INVALID")
        if option["cognitive_dimensions"]["overview"]["cost_units"] < 0:
            blocks.append("BLOCKED_FOR_MK747_NEGATIVE_COST_UNITS")
        if not _repo_file_ref_exists(
            option["cognitive_dimensions"]["ultracode"]["source_ref"]
        ):
            blocks.append("BLOCKED_FOR_MK747_ULTRACODE_SOURCE_REF_INVALID")

    proposed_id = decision["proposed_recommendation"]["option_id"]
    proposed = next(
        (option for option in options if option["option_id"] == proposed_id), None
    )
    if proposed is None:
        blocks.append("BLOCKED_FOR_MK747_PROPOSED_RECOMMENDATION_UNKNOWN")

    proposed_rejection_ids = {
        row["option_id"] for row in decision["proposed_rejections"]
    }
    if proposed_rejection_ids != option_id_set - {proposed_id}:
        blocks.append("BLOCKED_FOR_MK747_OPTION_OR_REJECTION_PRESERVATION_MISSING")

    prior_rows, applied_prior = _prior_assessments(
        decision, option_id_set, blocks
    )
    fact_ids = {row["fact_id"] for row in facts}
    counterevidence_ids = {
        row["counterevidence_id"] for row in counterevidence
    }
    uncertainty_ids = {row["uncertainty_id"] for row in uncertainties}
    option_by_id = {option["option_id"]: option for option in options}
    if any(
        not option["evidence_refs"]
        or not option["fact_refs"]
        or not option["counterevidence_refs"]
        or not option["uncertainty_refs"]
        for option in options
    ):
        blocks.append("BLOCKED_FOR_MK747_OPTION_EVIDENCE_LINEAGE_MISSING")
    elif facts and counterevidence and uncertainties and any(
        not set(option["fact_refs"]) <= fact_ids
        or not set(option["counterevidence_refs"]) <= counterevidence_ids
        or not set(option["uncertainty_refs"]) <= uncertainty_ids
        for option in options
    ):
        blocks.append("BLOCKED_FOR_MK747_OPTION_EVIDENCE_LINEAGE_INVALID")
    counterevidence_by_id = {
        row["counterevidence_id"]: set(row["affects_option_ids"])
        for row in counterevidence
    }
    if counterevidence and any(
        not set(row["affects_option_ids"]) <= option_id_set
        for row in counterevidence
    ):
        blocks.append("BLOCKED_FOR_MK747_COUNTEREVIDENCE_OPTION_BINDING_INVALID")
    elif any(
        option["option_id"]
        not in counterevidence_by_id.get(counterevidence_id, set())
        for option in options
        for counterevidence_id in option["counterevidence_refs"]
    ):
        blocks.append("BLOCKED_FOR_MK747_COUNTEREVIDENCE_OPTION_BINDING_INVALID")

    assessments: list[dict[str, Any]] = []
    for original_index, option in enumerate(options):
        dimension_assessments = {
            "iq": _score_iq(
                option, fact_ids, counterevidence_ids, uncertainty_ids
            ),
            "eq": _score_eq(option, intent["intent_id"], correction["prior_action"]),
            "ultracode": _score_ultracode(option),
            "overview": _score_overview(option),
            "innovation": _score_innovation(option, option_by_id),
            "discovery": _score_discovery(option),
        }
        dimension_total = sum(
            dimension_assessments[name]["score"] for name in DIMENSIONS
        )
        causal_value = CAUSAL_VALUE[option["user_value"]["causal_delta"]]
        cost_penalty = min(max(option["cognitive_dimensions"]["overview"]["cost_units"], 0), 10)
        prior_influence = applied_prior.get(option["option_id"], 0.0)
        semantic_score = causal_value + dimension_total - cost_penalty + prior_influence
        assessments.append(
            {
                "option_id": option["option_id"],
                "option_class": option["option_class"],
                "action_kind": option["action_kind"],
                "description": option["description"],
                "implementation_action": option["implementation_action"],
                "original_index": original_index,
                "causal_value_score": causal_value,
                "dimension_assessments": dimension_assessments,
                "dimension_total": dimension_total,
                "cost_penalty": cost_penalty,
                "bounded_prior_influence": prior_influence,
                "evidence_count_observed": option["evidence_count"],
                "evidence_count_ranking_influence": 0,
                "semantic_score": semantic_score,
            }
        )

    ranked = sorted(
        assessments, key=lambda row: (-row["semantic_score"], row["option_id"])
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    rank_by_id = {row["option_id"]: row["rank"] for row in ranked}
    for row in assessments:
        row["rank"] = rank_by_id[row["option_id"]]

    best = ranked[0] if ranked else None
    if proposed is not None and best is not None:
        proposed_assessment = next(
            row for row in assessments if row["option_id"] == proposed_id
        )
        best_option = option_by_id[best["option_id"]]
        bases = set(decision["proposed_recommendation"]["basis"])
        if (
            proposed["action_kind"] == "inventory"
            and best_option["user_value"]["causal_delta"] == "direct_capability"
            and proposed_assessment["semantic_score"] < best["semantic_score"]
        ):
            blocks.append(
                "BLOCKED_FOR_MK747_INVENTORY_SELECTED_OVER_CAUSE_CHANGING_CAPABILITY"
            )
        elif (
            "evidence_volume" in bases
            and proposed["evidence_count"] > best_option["evidence_count"]
            and proposed_assessment["semantic_score"] < best["semantic_score"]
        ):
            blocks.append(
                "BLOCKED_FOR_MK747_EVIDENCE_COUNT_SUBSTITUTED_FOR_CAUSAL_VALUE"
            )

        # Validate both the upstream proposal and the semantic action the
        # circuit would actually return. Affinity-selected actions reuse this
        # same check in evaluate_decision before they can be selected.
        validation_candidates = [proposed]
        if best_option["option_id"] != proposed["option_id"]:
            validation_candidates.append(best_option)
        for recommendation_candidate in validation_candidates:
            blocks.extend(
                _recommendation_candidate_blocks(
                    recommendation_candidate, option_by_id, correction
                )
            )

    return sorted(set(blocks)), assessments, prior_rows


def _recommendation_candidate_blocks(
    recommendation_candidate: dict[str, Any],
    option_by_id: dict[str, dict[str, Any]],
    correction: dict[str, Any],
) -> list[str]:
    """Return every support block that applies to a selectable recommendation."""

    blocks: list[str] = []
    eq = recommendation_candidate["cognitive_dimensions"]["eq"]
    if (
        eq["correction_effect"] != "changes_action"
        or _empty(eq["action_change"])
        or eq["action_change"].strip() == correction["prior_action"].strip()
    ):
        blocks.append("BLOCKED_FOR_MK747_MISSING_EQ_CORRECTION_EFFECT")

    ultracode = recommendation_candidate["cognitive_dimensions"]["ultracode"]
    chain = ultracode["consumer_chain"]
    if any(
        _empty(chain[field])
        for field in (
            "producer",
            "owning_operation",
            "actual_consumer",
            "last_mile",
            "observable_result",
        )
    ):
        blocks.append("BLOCKED_FOR_MK747_CONSUMER_LAST_MILE_MISSING")
    if _empty(ultracode["rollback"]):
        blocks.append("BLOCKED_FOR_MK747_ROLLBACK_MISSING")

    if recommendation_candidate["impact_class"] == "high":
        innovation = recommendation_candidate["cognitive_dimensions"]["innovation"]
        if (
            _empty(innovation["hypothesis"])
            or _empty(innovation["constraint_preserved"])
            or _empty(innovation["value"])
            or _empty(innovation["feasibility"])
            or not _bounded_experiment_complete(innovation["bounded_experiment"])
        ):
            blocks.append("BLOCKED_FOR_MK747_INNOVATION_WITHOUT_BOUNDED_EXPERIMENT")
        cheaper = option_by_id.get(
            innovation["cheaper_existing_primitive_option_id"]
        )
        if (
            cheaper is None
            or cheaper["option_class"] != "lower_cost_existing_primitive"
        ):
            blocks.append("BLOCKED_FOR_MK747_HIGH_IMPACT_CHEAPER_PRIMITIVE_MISSING")
        elif (
            cheaper["cognitive_dimensions"]["overview"]["cost_units"]
            >= recommendation_candidate["cognitive_dimensions"]["overview"][
                "cost_units"
            ]
        ):
            blocks.append("BLOCKED_FOR_MK747_HIGH_IMPACT_COMPARISON_NOT_CHEAPER")
        discovery = recommendation_candidate["cognitive_dimensions"]["discovery"]
        if any(
            _empty(discovery[field])
            for field in (
                "observation",
                "unmet_need",
                "feature_hypothesis",
                "experiment_ref",
                "adoption_metric",
                "retirement_condition",
            )
        ):
            blocks.append("BLOCKED_FOR_MK747_DISCOVERY_HYPOTHESIS_UNBOUNDED")
        elif (
            discovery["experiment_ref"]
            != innovation["bounded_experiment"]["experiment_id"]
        ):
            blocks.append("BLOCKED_FOR_MK747_DISCOVERY_EXPERIMENT_LINK_INVALID")
    return blocks


def _schema_failure_receipt(
    decision: Any, schema_blocks: list[str], *, redact_identity: bool = False
) -> dict[str, Any]:
    input_digest = canonical_digest(decision)
    receipt: dict[str, Any] = {
        "schema_version": "mk747_cognitive_decision_receipt.v1",
        "evaluator_version": EVALUATOR_VERSION,
        "mode": "shadow_only",
        "deterministic": True,
        "decision_id": (
            "redacted_sensitive_input"
            if redact_identity
            else decision.get("decision_id", "invalid")
            if isinstance(decision, dict)
            else "invalid"
        ),
        "input_digest": input_digest,
        "status": "FAIL_UNSUPPORTED_RECOMMENDATION_WITHHELD",
        "blocks": sorted(set(schema_blocks)),
        "candidate_assessments": [],
        "prior_assessments": [],
        "recommendation": {
            "selected_option_id": None,
            "counterfactual_best_option_id": None,
            "blocked_scope": "shadow_recommendation_only",
        },
        "preserved_proposed_rejections": [],
        "rejected_options": [],
        "ordinary_supervised_work": {
            "status": "continue",
            "global_gate_created": False,
        },
        "authority_effects": {
            "automatic_gene_promotion": False,
            "authority_mutation": False,
            "option_deletion": False,
        },
        "non_claims": sorted(REQUIRED_NON_CLAIMS),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def evaluate_decision(
    decision: Any,
    schema_path: Path = SCHEMA_PATH,
    hermes_creative_projection: Any = HERMES_CREATIVE_PROJECTION_ABSENT,
    accepted_option_affinities: Any = None,
) -> dict[str, Any]:
    schema = load_json(schema_path)
    schema_blocks = validate_schema(decision, schema)
    contains_prohibited_content = _contains_prohibited_content(decision)
    if contains_prohibited_content:
        return _schema_failure_receipt(
            decision,
            schema_blocks + ["BLOCKED_FOR_MK747_PRIVATE_OR_SELF_SCORE_CONTENT"],
            redact_identity=True,
        )
    if schema_blocks:
        return _schema_failure_receipt(decision, schema_blocks)

    blocks, assessments, prior_rows = _semantic_blocks_and_assessments(decision)
    ranked = sorted(
        assessments, key=lambda row: (-row["semantic_score"], row["option_id"])
    )
    candidate_best_id = ranked[0]["option_id"] if ranked else None
    option_by_id = {
        option["option_id"]: option for option in decision["candidate_options"]
    }
    creative_advisory: dict[str, Any] | None = None
    creative_route_selected = (
        hermes_creative_projection is not HERMES_CREATIVE_PROJECTION_ABSENT
    )
    affinity_by_option_id: dict[str, float] = {}
    if creative_route_selected:
        creative_blocks, creative_candidates = _validate_hermes_creative_projection(
            hermes_creative_projection
        )
        blocks = sorted(set(blocks + creative_blocks))
        affinity_selected_id, admitted_affinities = _accepted_affinity_selection(
            accepted_option_affinities, set(option_by_id)
        )
        if creative_blocks:
            affinity_selected_id = None
            admitted_affinities = []
        affinity_candidate_blocks: list[str] = []
        if affinity_selected_id is not None:
            affinity_candidate_blocks = _recommendation_candidate_blocks(
                option_by_id[affinity_selected_id],
                option_by_id,
                decision["current_decision"]["user_correction"],
            )
            blocks = sorted(set(blocks + affinity_candidate_blocks))
        affinity_by_option_id = {
            row["option_id"]: row["affinity"] for row in admitted_affinities
        }
        selected_id = None if blocks else affinity_selected_id
        discrimination_status = (
            "accepted_option_affinity_discrimination"
            if selected_id is not None
            else "no_admissible_discrimination"
        )
        creative_advisory = {
            "status": (
                "withheld"
                if creative_blocks or affinity_candidate_blocks
                else "accepted_advisory"
            ),
            "creative_skill_version": (
                HERMES_CREATIVE_SKILL_VERSION if not creative_blocks else None
            ),
            "candidate_count": len(creative_candidates),
            "candidates": creative_candidates,
            "accepted_option_affinities": admitted_affinities,
            "discrimination_status": discrimination_status,
            "ranking_or_selection_authority": False,
            "goal_authority": False,
            "store_authority": False,
            "acceptance_authority": False,
            "mutation_authority": False,
        }
    else:
        selected_id = None if blocks else candidate_best_id
    proposed_rejections = decision["proposed_rejections"]
    rejection_reason_by_id = {
        row["option_id"]: row["reason"] for row in proposed_rejections
    }
    selected_score = (
        affinity_by_option_id.get(selected_id)
        if creative_route_selected
        else ranked[0]["semantic_score"] if ranked else None
    )
    rejected_options: list[dict[str, Any]] = []
    if selected_id is not None:
        rejected_rows = [row for row in ranked if row["option_id"] != selected_id]
        for row in rejected_rows:
            if creative_route_selected:
                deterministic_reason = (
                    f"accepted affinity {affinity_by_option_id.get(row['option_id'])} "
                    f"is below selected affinity {selected_score}; no affinity was inferred"
                )
            else:
                deterministic_reason = (
                    f"semantic_score {row['semantic_score']} is below selected "
                    f"score {selected_score}; evidence count had zero influence"
                )
            rejected_options.append(
                {
                    "option_id": row["option_id"],
                    "input_reason": rejection_reason_by_id.get(
                        row["option_id"], "not provided"
                    ),
                    "deterministic_reason": deterministic_reason,
                }
            )

    selected_option = option_by_id.get(selected_id) if selected_id else None
    recommendation: dict[str, Any] = {
        "selected_option_id": selected_id,
        "counterfactual_best_option_id": (
            None if creative_route_selected else candidate_best_id
        ),
        "blocked_scope": "none" if not blocks else "shadow_recommendation_only",
    }
    if creative_route_selected:
        recommendation["discrimination_status"] = (
            creative_advisory["discrimination_status"]
            if creative_advisory is not None
            else "no_admissible_discrimination"
        )
    if selected_option is not None:
        chain = selected_option["cognitive_dimensions"]["ultracode"][
            "consumer_chain"
        ]
        recommendation.update(
            {
                "implementation_action": selected_option["implementation_action"],
                "normal_user_can_now": selected_option["user_value"][
                    "normal_user_can_now"
                ],
                "actual_consumer": chain["actual_consumer"],
                "last_mile": chain["last_mile"],
                "measurement": selected_option["user_value"]["measurement"],
                "stop_condition": selected_option["stop_condition"],
            }
        )

    receipt: dict[str, Any] = {
        "schema_version": "mk747_cognitive_decision_receipt.v1",
        "evaluator_version": EVALUATOR_VERSION,
        "mode": "shadow_only",
        "deterministic": True,
        "decision_id": decision["decision_id"],
        "grand_goal_ref": decision["grand_goal_ref"],
        "input_digest": canonical_digest(
            {
                "decision": decision,
                "hermes_creative_projection": hermes_creative_projection,
                "accepted_option_affinities": accepted_option_affinities,
            }
            if creative_route_selected
            else decision
        ),
        "status": (
            "PASS_SHADOW_RECOMMENDATION"
            if not blocks and selected_id is not None
            else "PASS_SHADOW_NO_ADMISSIBLE_DISCRIMINATION"
            if not blocks and creative_route_selected
            else "FAIL_UNSUPPORTED_RECOMMENDATION_WITHHELD"
        ),
        "blocks": blocks,
        "candidate_assessments": assessments,
        "prior_assessments": prior_rows,
        "recommendation": recommendation,
        "preserved_proposed_rejections": proposed_rejections,
        "rejected_options": rejected_options,
        "ordinary_supervised_work": {
            "status": "continue",
            "global_gate_created": False,
        },
        "authority_effects": {
            "automatic_gene_promotion": False,
            "authority_mutation": False,
            "option_deletion": False,
        },
        "non_claims": sorted(REQUIRED_NON_CLAIMS),
    }
    if creative_advisory is not None:
        receipt["creative_advisory"] = creative_advisory
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--decision", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    decision_path = Path(args.decision)
    if not decision_path.is_absolute():
        decision_path = base_dir / decision_path
    try:
        decision = load_json(decision_path)
        receipt = evaluate_decision(decision)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "schema_version": "mk747_cognitive_decision_receipt.v1",
            "evaluator_version": EVALUATOR_VERSION,
            "mode": "shadow_only",
            "deterministic": True,
            "decision_id": "invalid",
            "status": "FAIL_UNSUPPORTED_RECOMMENDATION_WITHHELD",
            "blocks": ["BLOCKED_FOR_MK747_DECISION_JSON_INVALID"],
            "error_class": type(exc).__name__,
            "ordinary_supervised_work": {
                "status": "continue",
                "global_gate_created": False,
            },
            "authority_effects": {
                "automatic_gene_promotion": False,
                "authority_mutation": False,
                "option_deletion": False,
            },
            "non_claims": sorted(REQUIRED_NON_CLAIMS),
        }
        result["receipt_digest"] = canonical_digest(result)
        receipt = result

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = base_dir / output_path
        write_json_atomic(output_path, receipt)
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(receipt["status"])
        selected = receipt.get("recommendation", {}).get("selected_option_id")
        if selected:
            print(selected)
        for blocker in receipt.get("blocks", []):
            print(blocker)
    return 0 if receipt["status"] == "PASS_SHADOW_RECOMMENDATION" else 1


if __name__ == "__main__":
    sys.exit(main())
