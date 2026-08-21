"""Fixed consumer for Maestro's protected plugin-adoption contract.

This sibling consumer deliberately does not alter ``maestro_authority`` or the
existing prompt/session authority bytes.  It accepts only the one immutable
ORCH-Next Hermes plugin, marketplace, ordered host set, and ordered transition
set.  Paths and commands never cross the authority wire.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Final

from tui_gateway import maestro_authority as _base


CONTRACT_ID: Final = "HERMES_PROTECTED_PLUGIN_ADOPTION_V1"
CONTRACT_VERSION: Final = "hermes-protected-plugin-adoption.v1"
CONTRACT_DIGEST: Final = (
    "999ca51fea4d0dd6f77a7d1393bde5bdbd94b425c1c25e6146157dc0d8f97f07"
)
AUTHORITY_OWNER: Final = "maestro-kernel"
AUTHORITY_CONSUMER: Final = "hermes_operational_harness"
AUTHORITY_BUNDLE_VERSION: Final = _base.HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION
AUTHORITY_BUNDLE_DIGEST: Final = _base.HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST
OPERATION: Final = "plugin.adoption.apply"
TERMINAL_OPERATION: Final = "plugin.adoption.terminalize"
TERMINAL_CONTRACT_ID: Final = CONTRACT_ID
TERMINAL_CONTRACT_VERSION: Final = (
    "hermes-protected-plugin-adoption.terminalize.v1"
)
TERMINAL_CONTRACT_DESCRIPTOR: Final = {
    "admission": (
        "all terminal plan fields equal boot-admitted immutable manifest; "
        "before_state_digest equals after_state_digest"
    ),
    "constants": {
        "marketplace_id": "orch-next-hermes-local",
        "max_ttl_seconds": 300,
        "observation_only": True,
        "operation": "plugin.adoption.terminalize",
        "plugin_id": "orch-next-hermes-harness",
        "plugin_version": "0.1.48",
        "requester": "hermes_operational_harness",
        "source_revision": "9957e57afe528477986f889a1570c7ac7e113f0e",
        "target_set": ["codex", "claude"],
        "transition_set": [
            "plugin.adoption.observe",
            "plugin.adoption.predecessor_terminalize",
        ],
    },
    "contract": {
        "id": TERMINAL_CONTRACT_ID,
        "keys": ["id", "version", "digest"],
        "version": TERMINAL_CONTRACT_VERSION,
    },
    "forbidden_request_content": [
        "paths",
        "commands",
        "caller_actions",
        "host_cache_install_process_mutation",
    ],
    "plan_digest": "sha256(canonical plan excluding plan_digest)",
    "receipt_keys": [
        "outcome",
        "code",
        "decision_id",
        "transaction_id",
        "authority_owner",
        "authority_bundle_version",
        "authority_bundle_digest",
        "authority_consumer",
        "contract_id",
        "contract_version",
        "contract_digest",
        "operation",
        "marketplace_id",
        "plugin_id",
        "plugin_version",
        "source_runtime_revision",
        "source_revision",
        "source_bundle_digest",
        "target_set",
        "transition_set",
        "predecessor_identity_digest",
        "current_identity_digest",
        "canonical_identity_digest",
        "codex_current_state_digest",
        "claude_current_state_digest",
        "before_state_digest",
        "after_state_digest",
        "rollback_manifest_digest",
        "plan_digest",
        "issued_at",
        "expires_at",
        "final_decision_state",
        "final_execution_permitted",
        "consumed_once",
        "request_digest",
    ],
    "replay": {
        "active_slots": 1,
        "allow": "one durable signed envelope",
        "cross_action_ids": "signed deny",
        "recent_completed": "bounded",
        "same_ids_changed_request": "signed deny",
        "stable_request": (
            "byte-identical stored signed envelope before fresh TTL validation"
        ),
    },
    "request": {
        "actual_keys": [
            "decision_id",
            "transaction_id",
            "requester",
            "operation",
            "issued_at",
            "expires_at",
            "source_runtime_revision",
        ],
        "plan_keys": [
            "marketplace_id",
            "plugin_id",
            "plugin_version",
            "source_revision",
            "source_bundle_digest",
            "target_set",
            "transition_set",
            "predecessor_identity_digest",
            "current_identity_digest",
            "canonical_identity_digest",
            "codex_current_state_digest",
            "claude_current_state_digest",
            "before_state_digest",
            "after_state_digest",
            "rollback_manifest_digest",
            "plan_digest",
        ],
        "top_keys": ["actual", "challenge", "contract", "plan"],
    },
    "signature_payload": {
        "canonical": "ascii-json",
        "keys": ["request", "receipt"],
    },
}
TERMINAL_CONTRACT_DIGEST: Final = (
    "af7b40de7bf29d27adace625e04671e8e5250fa7a1f9ab7fb4b85c9c68fbefc9"
)
MARKETPLACE_ID: Final = "orch-next-hermes-local"
PLUGIN_ID: Final = "orch-next-hermes-harness"
ORDINARY_APPLY_PLUGIN_VERSION: Final = "0.1.49"
PLUGIN_VERSION: Final = ORDINARY_APPLY_PLUGIN_VERSION
TERMINAL_PLUGIN_VERSION: Final = "0.1.48"
TERMINAL_SOURCE_REVISION: Final = "9957e57afe528477986f889a1570c7ac7e113f0e"
PREVIOUS_TERMINAL_PLUGIN_VERSION: Final = "0.1.42"
TARGET_SET: Final = ("codex", "claude")
TRANSITION_SET: Final = (
    "marketplace.rebind",
    "plugin.install",
    "plugin.activate",
)
TERMINAL_TRANSITION_SET: Final = (
    "plugin.adoption.observe",
    "plugin.adoption.predecessor_terminalize",
)
TERMINAL_ALLOW_CODE: Final = "plugin_adoption_terminalize_allowed"
TERMINAL_DENY_CODE: Final = "plugin_adoption_terminalize_denied"
MAX_TTL_SECONDS: Final = 300.0

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_REQUEST_KEYS = frozenset({"actual", "challenge", "contract", "plan"})
_CONTRACT_KEYS = frozenset({"id", "version", "digest"})
_ACTUAL_KEYS = frozenset({
    "decision_id",
    "transaction_id",
    "requester",
    "operation",
    "issued_at",
    "expires_at",
    "source_runtime_revision",
})
_PLAN_KEYS = frozenset({
    "marketplace_id",
    "plugin_id",
    "plugin_version",
    "source_revision",
    "source_bundle_digest",
    "target_set",
    "transition_set",
    "before_state_digest",
    "after_state_digest",
    "rollback_manifest_digest",
    "plan_digest",
})
_TERMINAL_PLAN_KEYS = frozenset({
    "marketplace_id",
    "plugin_id",
    "plugin_version",
    "source_revision",
    "source_bundle_digest",
    "target_set",
    "transition_set",
    "predecessor_identity_digest",
    "current_identity_digest",
    "canonical_identity_digest",
    "codex_current_state_digest",
    "claude_current_state_digest",
    "before_state_digest",
    "after_state_digest",
    "rollback_manifest_digest",
    "plan_digest",
})
_RECEIPT_KEYS = frozenset({
    "outcome",
    "code",
    "decision_id",
    "transaction_id",
    "authority_owner",
    "authority_bundle_version",
    "authority_bundle_digest",
    "authority_consumer",
    "contract_id",
    "contract_version",
    "contract_digest",
    "operation",
    "marketplace_id",
    "plugin_id",
    "plugin_version",
    "source_runtime_revision",
    "source_revision",
    "source_bundle_digest",
    "target_set",
    "transition_set",
    "before_state_digest",
    "after_state_digest",
    "rollback_manifest_digest",
    "plan_digest",
    "issued_at",
    "expires_at",
    "final_decision_state",
    "final_execution_permitted",
    "consumed_once",
    "request_digest",
})
_TERMINAL_RECEIPT_KEYS = _RECEIPT_KEYS | frozenset({
    "predecessor_identity_digest",
    "current_identity_digest",
    "canonical_identity_digest",
    "codex_current_state_digest",
    "claude_current_state_digest",
})


class PluginAdoptionAuthorityError(RuntimeError):
    """A stable, value-free protected-adoption failure."""


@dataclass(frozen=True, slots=True)
class VerifiedPluginAdoptionEnvelope:
    """Verified request/receipt binding plus its exact durable bytes."""

    request: dict[str, Any]
    receipt: dict[str, Any]
    request_bytes: bytes
    envelope_bytes: bytes
    request_digest: str
    envelope_digest: str
    allowed: bool


def canonical_bytes(value: object) -> bytes:
    """Return the existing V3 canonical ASCII encoding or fail closed."""

    encoded = _base._canonical_authority_payload(value)
    if encoded is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    return encoded


def _exact_dict(value: object, keys: frozenset[str]) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != keys:
        return None
    return {key: dict.__getitem__(value, key) for key in keys}


def _safe_id(value: object) -> str | None:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        return None
    lowered = value.lower()
    if (
        "://" in value
        or value.startswith(("/", "~", "./", "../"))
        or lowered.startswith(("sk-", "ghp_", "gho_", "ghs_", "xox"))
        or value.startswith(("AIza", "eyJ"))
    ):
        return None
    return value


def _finite_time(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def compute_plan_digest(plan_without_digest: object) -> str:
    """Digest the exact plan with ``plan_digest`` omitted."""

    if type(plan_without_digest) is not dict or set(plan_without_digest) != (
        _PLAN_KEYS - {"plan_digest"}
    ):
        raise PluginAdoptionAuthorityError("authority_plan_mismatch")
    return hashlib.sha256(canonical_bytes(plan_without_digest)).hexdigest()


def compute_terminal_plan_digest(plan_without_digest: object) -> str:
    """Digest the exact terminal-only plan with ``plan_digest`` omitted."""

    if type(plan_without_digest) is not dict or set(plan_without_digest) != (
        _TERMINAL_PLAN_KEYS - {"plan_digest"}
    ):
        raise PluginAdoptionAuthorityError("authority_plan_mismatch")
    return hashlib.sha256(canonical_bytes(plan_without_digest)).hexdigest()


def _validated_plan(
    value: object,
    *,
    plugin_version: str = PLUGIN_VERSION,
) -> dict[str, Any] | None:
    plan = _exact_dict(value, _PLAN_KEYS)
    if plan is None:
        return None
    if (
        plan["marketplace_id"] != MARKETPLACE_ID
        or plan["plugin_id"] != PLUGIN_ID
        or plan["plugin_version"] != plugin_version
        or type(plan["source_revision"]) is not str
        or _GIT_SHA_RE.fullmatch(plan["source_revision"]) is None
        or any(
            type(plan[key]) is not str or _SHA256_RE.fullmatch(plan[key]) is None
            for key in (
                "source_bundle_digest",
                "before_state_digest",
                "after_state_digest",
                "rollback_manifest_digest",
                "plan_digest",
            )
        )
        or type(plan["target_set"]) is not list
        or tuple(plan["target_set"]) != TARGET_SET
        or type(plan["transition_set"]) is not list
        or tuple(plan["transition_set"]) != TRANSITION_SET
    ):
        return None
    without_digest = {
        key: plan[key] for key in sorted(_PLAN_KEYS - {"plan_digest"})
    }
    try:
        digest = compute_plan_digest(without_digest)
    except PluginAdoptionAuthorityError:
        return None
    return dict(plan) if digest == plan["plan_digest"] else None


def _validated_terminal_plan(value: object) -> dict[str, Any] | None:
    plan = _exact_dict(value, _TERMINAL_PLAN_KEYS)
    digest_keys = (
        "source_bundle_digest",
        "predecessor_identity_digest",
        "current_identity_digest",
        "canonical_identity_digest",
        "codex_current_state_digest",
        "claude_current_state_digest",
        "before_state_digest",
        "after_state_digest",
        "rollback_manifest_digest",
        "plan_digest",
    )
    if (
        plan is None
        or plan["marketplace_id"] != MARKETPLACE_ID
        or plan["plugin_id"] != PLUGIN_ID
        or plan["plugin_version"] != TERMINAL_PLUGIN_VERSION
        or plan["source_revision"] != TERMINAL_SOURCE_REVISION
        or any(
            type(plan[key]) is not str or _SHA256_RE.fullmatch(plan[key]) is None
            for key in digest_keys
        )
        or type(plan["target_set"]) is not list
        or tuple(plan["target_set"]) != TARGET_SET
        or type(plan["transition_set"]) is not list
        or tuple(plan["transition_set"]) != TERMINAL_TRANSITION_SET
        or plan["before_state_digest"] != plan["after_state_digest"]
        or plan["codex_current_state_digest"]
        == plan["claude_current_state_digest"]
    ):
        return None
    expected_current = hashlib.sha256(canonical_bytes({
        "claude": plan["claude_current_state_digest"],
        "codex": plan["codex_current_state_digest"],
        "target_set": list(TARGET_SET),
    })).hexdigest()
    if plan["current_identity_digest"] != expected_current:
        return None
    without_digest = {
        key: plan[key]
        for key in sorted(_TERMINAL_PLAN_KEYS - {"plan_digest"})
    }
    try:
        digest = compute_terminal_plan_digest(without_digest)
    except PluginAdoptionAuthorityError:
        return None
    return dict(plan) if digest == plan["plan_digest"] else None


def build_plugin_adoption_request(
    *,
    decision_id: object,
    transaction_id: object,
    source_runtime_revision: object,
    issued_at: object,
    expires_at: object,
    plan: object,
) -> dict[str, Any]:
    """Build the byte-stable exact request for one protected transaction."""

    return _build_plugin_adoption_request(
        decision_id=decision_id,
        transaction_id=transaction_id,
        source_runtime_revision=source_runtime_revision,
        issued_at=issued_at,
        expires_at=expires_at,
        plan=plan,
        plugin_version=PLUGIN_VERSION,
    )


def _build_plugin_adoption_request(
    *,
    decision_id: object,
    transaction_id: object,
    source_runtime_revision: object,
    issued_at: object,
    expires_at: object,
    plan: object,
    plugin_version: str,
) -> dict[str, Any]:

    checked_decision = _safe_id(decision_id)
    checked_transaction = _safe_id(transaction_id)
    issued = _finite_time(issued_at)
    expires = _finite_time(expires_at)
    checked_plan = _validated_plan(plan, plugin_version=plugin_version)
    if (
        checked_decision is None
        or checked_transaction is None
        or type(source_runtime_revision) is not str
        or _GIT_SHA_RE.fullmatch(source_runtime_revision) is None
        or issued is None
        or expires is None
        or expires <= issued
        or expires - issued > MAX_TTL_SECONDS
        or checked_plan is None
    ):
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    actual = {
        "decision_id": checked_decision,
        "transaction_id": checked_transaction,
        "requester": AUTHORITY_CONSUMER,
        "operation": OPERATION,
        "issued_at": issued,
        "expires_at": expires,
        "source_runtime_revision": source_runtime_revision,
    }
    contract = {
        "id": CONTRACT_ID,
        "version": CONTRACT_VERSION,
        "digest": CONTRACT_DIGEST,
    }
    challenge_material = canonical_bytes({
        "contract": contract,
        "decision_id": checked_decision,
        "plan_digest": checked_plan["plan_digest"],
        "transaction_id": checked_transaction,
    })
    return {
        "actual": actual,
        "challenge": hashlib.sha256(challenge_material).hexdigest(),
        "contract": contract,
        "plan": checked_plan,
    }


def build_plugin_adoption_terminal_request(
    *,
    decision_id: object,
    transaction_id: object,
    source_runtime_revision: object,
    issued_at: object,
    expires_at: object,
    plan: object,
) -> dict[str, Any]:
    """Build one exact terminal-predecessor request in the adoption family."""

    checked_decision = _safe_id(decision_id)
    checked_transaction = _safe_id(transaction_id)
    issued = _finite_time(issued_at)
    expires = _finite_time(expires_at)
    checked_plan = _validated_terminal_plan(plan)
    if (
        checked_decision is None
        or checked_transaction is None
        or source_runtime_revision != TERMINAL_SOURCE_REVISION
        or issued is None
        or expires is None
        or expires <= issued
        or expires - issued > MAX_TTL_SECONDS
        or checked_plan is None
    ):
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    actual = {
        "decision_id": checked_decision,
        "transaction_id": checked_transaction,
        "requester": AUTHORITY_CONSUMER,
        "operation": TERMINAL_OPERATION,
        "issued_at": issued,
        "expires_at": expires,
        "source_runtime_revision": TERMINAL_SOURCE_REVISION,
    }
    contract = {
        "id": TERMINAL_CONTRACT_ID,
        "version": TERMINAL_CONTRACT_VERSION,
        "digest": TERMINAL_CONTRACT_DIGEST,
    }
    challenge_material = canonical_bytes({
        "contract": contract,
        "decision_id": checked_decision,
        "plan_digest": checked_plan["plan_digest"],
        "transaction_id": checked_transaction,
    })
    return {
        "actual": actual,
        "challenge": hashlib.sha256(challenge_material).hexdigest(),
        "contract": contract,
        "plan": checked_plan,
    }


def validate_request(value: object, *, now: float | None = None) -> dict[str, Any]:
    """Strictly validate a request before transport or persisted replay."""

    return _validate_request(value, now=now, plugin_version=PLUGIN_VERSION)


def validate_terminal_request(
    value: object,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Strictly validate only the terminal-predecessor sibling action."""

    request = _exact_dict(value, _REQUEST_KEYS)
    if request is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    contract = _exact_dict(request["contract"], _CONTRACT_KEYS)
    actual = _exact_dict(request["actual"], _ACTUAL_KEYS)
    plan = _validated_terminal_plan(request["plan"])
    if contract is None or actual is None or plan is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    issued = _finite_time(actual["issued_at"])
    expires = _finite_time(actual["expires_at"])
    if (
        contract != {
            "id": TERMINAL_CONTRACT_ID,
            "version": TERMINAL_CONTRACT_VERSION,
            "digest": TERMINAL_CONTRACT_DIGEST,
        }
        or _safe_id(actual["decision_id"]) is None
        or _safe_id(actual["transaction_id"]) is None
        or actual["requester"] != AUTHORITY_CONSUMER
        or actual["operation"] != TERMINAL_OPERATION
        or actual["source_runtime_revision"] != TERMINAL_SOURCE_REVISION
        or issued is None
        or expires is None
        or expires <= issued
        or expires - issued > MAX_TTL_SECONDS
        or type(request["challenge"]) is not str
        or _SHA256_RE.fullmatch(request["challenge"]) is None
    ):
        raise PluginAdoptionAuthorityError("authority_mismatch")
    expected = build_plugin_adoption_terminal_request(
        decision_id=actual["decision_id"],
        transaction_id=actual["transaction_id"],
        source_runtime_revision=actual["source_runtime_revision"],
        issued_at=issued,
        expires_at=expires,
        plan=plan,
    )
    if request != expected:
        raise PluginAdoptionAuthorityError("authority_mismatch")
    if now is not None:
        checked_now = _finite_time(now)
        if checked_now is None or issued > checked_now + 5.0 or expires <= checked_now:
            raise PluginAdoptionAuthorityError("authority_stale")
    return request


def _validate_request(
    value: object,
    *,
    now: float | None,
    plugin_version: str,
) -> dict[str, Any]:

    request = _exact_dict(value, _REQUEST_KEYS)
    if request is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    contract = _exact_dict(request["contract"], _CONTRACT_KEYS)
    actual = _exact_dict(request["actual"], _ACTUAL_KEYS)
    plan = _validated_plan(request["plan"], plugin_version=plugin_version)
    if contract is None or actual is None or plan is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    issued = _finite_time(actual["issued_at"])
    expires = _finite_time(actual["expires_at"])
    if (
        contract != {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "digest": CONTRACT_DIGEST,
        }
        or _safe_id(actual["decision_id"]) is None
        or _safe_id(actual["transaction_id"]) is None
        or actual["requester"] != AUTHORITY_CONSUMER
        or actual["operation"] != OPERATION
        or issued is None
        or expires is None
        or expires <= issued
        or expires - issued > MAX_TTL_SECONDS
        or type(actual["source_runtime_revision"]) is not str
        or _GIT_SHA_RE.fullmatch(actual["source_runtime_revision"]) is None
        or type(request["challenge"]) is not str
        or _SHA256_RE.fullmatch(request["challenge"]) is None
    ):
        raise PluginAdoptionAuthorityError("authority_mismatch")
    expected = _build_plugin_adoption_request(
        decision_id=actual["decision_id"],
        transaction_id=actual["transaction_id"],
        source_runtime_revision=actual["source_runtime_revision"],
        issued_at=issued,
        expires_at=expires,
        plan=plan,
        plugin_version=plugin_version,
    )
    if request != expected:
        raise PluginAdoptionAuthorityError("authority_mismatch")
    if now is not None:
        checked_now = _finite_time(now)
        if checked_now is None or issued > checked_now + 5.0 or expires <= checked_now:
            raise PluginAdoptionAuthorityError("authority_stale")
    return request


def _validate_receipt(
    receipt_value: object,
    *,
    request: dict[str, Any],
    request_digest: str,
    now: float,
    plugin_version: str = PLUGIN_VERSION,
) -> tuple[dict[str, Any], bool]:
    receipt = _exact_dict(receipt_value, _RECEIPT_KEYS)
    if receipt is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    actual = request["actual"]
    plan = request["plan"]
    echoes = {
        "decision_id": actual["decision_id"],
        "transaction_id": actual["transaction_id"],
        "authority_owner": AUTHORITY_OWNER,
        "authority_bundle_version": AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": AUTHORITY_CONSUMER,
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "contract_digest": CONTRACT_DIGEST,
        "operation": OPERATION,
        "marketplace_id": MARKETPLACE_ID,
        "plugin_id": PLUGIN_ID,
        "plugin_version": plugin_version,
        "source_runtime_revision": actual["source_runtime_revision"],
        "source_revision": plan["source_revision"],
        "source_bundle_digest": plan["source_bundle_digest"],
        "target_set": list(TARGET_SET),
        "transition_set": list(TRANSITION_SET),
        "before_state_digest": plan["before_state_digest"],
        "after_state_digest": plan["after_state_digest"],
        "rollback_manifest_digest": plan["rollback_manifest_digest"],
        "plan_digest": plan["plan_digest"],
        "issued_at": actual["issued_at"],
        "expires_at": actual["expires_at"],
        "consumed_once": True,
        "request_digest": request_digest,
    }
    if any(type(receipt[key]) is not type(value) for key, value in echoes.items()):
        raise PluginAdoptionAuthorityError("authority_mismatch")
    if any(receipt[key] != value for key, value in echoes.items()):
        raise PluginAdoptionAuthorityError("authority_mismatch")
    if receipt["expires_at"] <= now:
        raise PluginAdoptionAuthorityError("authority_stale")
    allow = (
        receipt["outcome"] == "allow"
        and receipt["code"] == "plugin_adoption_allowed"
        and receipt["final_decision_state"] == "final_allowed_once"
        and receipt["final_execution_permitted"] is True
    )
    deny = (
        receipt["outcome"] == "deny"
        and receipt["code"] == "plugin_adoption_denied"
        and receipt["final_decision_state"] == "final_denied"
        and receipt["final_execution_permitted"] is False
    )
    if not allow and not deny:
        raise PluginAdoptionAuthorityError("authority_mismatch")
    return receipt, allow


def _validate_terminal_receipt(
    receipt_value: object,
    *,
    request: dict[str, Any],
    request_digest: str,
    now: float,
) -> tuple[dict[str, Any], bool]:
    receipt = _exact_dict(receipt_value, _TERMINAL_RECEIPT_KEYS)
    if receipt is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    actual = request["actual"]
    plan = request["plan"]
    echoes = {
        "decision_id": actual["decision_id"],
        "transaction_id": actual["transaction_id"],
        "authority_owner": AUTHORITY_OWNER,
        "authority_bundle_version": AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": AUTHORITY_CONSUMER,
        "contract_id": TERMINAL_CONTRACT_ID,
        "contract_version": TERMINAL_CONTRACT_VERSION,
        "contract_digest": TERMINAL_CONTRACT_DIGEST,
        "operation": TERMINAL_OPERATION,
        "marketplace_id": MARKETPLACE_ID,
        "plugin_id": PLUGIN_ID,
        "plugin_version": TERMINAL_PLUGIN_VERSION,
        "source_runtime_revision": TERMINAL_SOURCE_REVISION,
        "source_revision": TERMINAL_SOURCE_REVISION,
        "source_bundle_digest": plan["source_bundle_digest"],
        "target_set": list(TARGET_SET),
        "transition_set": list(TERMINAL_TRANSITION_SET),
        "predecessor_identity_digest": plan["predecessor_identity_digest"],
        "current_identity_digest": plan["current_identity_digest"],
        "canonical_identity_digest": plan["canonical_identity_digest"],
        "codex_current_state_digest": plan["codex_current_state_digest"],
        "claude_current_state_digest": plan["claude_current_state_digest"],
        "before_state_digest": plan["before_state_digest"],
        "after_state_digest": plan["after_state_digest"],
        "rollback_manifest_digest": plan["rollback_manifest_digest"],
        "plan_digest": plan["plan_digest"],
        "issued_at": actual["issued_at"],
        "expires_at": actual["expires_at"],
        "consumed_once": True,
        "request_digest": request_digest,
    }
    if any(type(receipt[key]) is not type(value) for key, value in echoes.items()):
        raise PluginAdoptionAuthorityError("authority_mismatch")
    if any(receipt[key] != value for key, value in echoes.items()):
        raise PluginAdoptionAuthorityError("authority_mismatch")
    if receipt["expires_at"] <= now:
        raise PluginAdoptionAuthorityError("authority_stale")
    allow = (
        receipt["outcome"] == "allow"
        and receipt["code"] == TERMINAL_ALLOW_CODE
        and receipt["final_decision_state"] == "final_allowed_once"
        and receipt["final_execution_permitted"] is True
    )
    deny = (
        receipt["outcome"] == "deny"
        and receipt["code"] == TERMINAL_DENY_CODE
        and receipt["final_decision_state"] == "final_denied"
        and receipt["final_execution_permitted"] is False
    )
    if not allow and not deny:
        raise PluginAdoptionAuthorityError("authority_mismatch")
    return receipt, allow


def verify_plugin_adoption_envelope(
    *,
    request_bytes: bytes,
    envelope_bytes: bytes,
    now: float,
) -> VerifiedPluginAdoptionEnvelope:
    """Re-verify exact persisted bytes using the fixed signer trust anchor."""

    return _verify_plugin_adoption_envelope(
        request_bytes=request_bytes,
        envelope_bytes=envelope_bytes,
        now=now,
        plugin_version=PLUGIN_VERSION,
    )


def verify_previous_terminal_plugin_adoption_envelope(
    *,
    request_bytes: bytes,
    envelope_bytes: bytes,
    now: float,
) -> VerifiedPluginAdoptionEnvelope:
    """Re-verify only the one fixed predecessor version for terminal archival."""

    return _verify_plugin_adoption_envelope(
        request_bytes=request_bytes,
        envelope_bytes=envelope_bytes,
        now=now,
        plugin_version=PREVIOUS_TERMINAL_PLUGIN_VERSION,
    )


def verify_plugin_adoption_terminal_envelope(
    *,
    request_bytes: bytes,
    envelope_bytes: bytes,
    now: float,
) -> VerifiedPluginAdoptionEnvelope:
    """Verify only the terminal-predecessor request and receipt sibling."""

    request_value = _base._parse_canonical_authority_payload(request_bytes)
    envelope_value = _base._parse_canonical_authority_payload(envelope_bytes)
    request = validate_terminal_request(request_value, now=now)
    envelope = _exact_dict(envelope_value, frozenset({"receipt", "signature"}))
    if envelope is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    signed_payload = canonical_bytes({
        "request": request,
        "receipt": envelope["receipt"],
    })
    if not _base._verify_sshsig(signed_payload, envelope["signature"]):
        raise PluginAdoptionAuthorityError("authority_signature_unavailable")
    request_digest = hashlib.sha256(request_bytes).hexdigest()
    receipt, allowed = _validate_terminal_receipt(
        envelope["receipt"],
        request=request,
        request_digest=request_digest,
        now=now,
    )
    return VerifiedPluginAdoptionEnvelope(
        request=request,
        receipt=receipt,
        request_bytes=request_bytes,
        envelope_bytes=envelope_bytes,
        request_digest=request_digest,
        envelope_digest=hashlib.sha256(envelope_bytes).hexdigest(),
        allowed=allowed,
    )


def _verify_plugin_adoption_envelope(
    *,
    request_bytes: bytes,
    envelope_bytes: bytes,
    now: float,
    plugin_version: str,
) -> VerifiedPluginAdoptionEnvelope:

    request_value = _base._parse_canonical_authority_payload(request_bytes)
    envelope_value = _base._parse_canonical_authority_payload(envelope_bytes)
    request = _validate_request(
        request_value,
        now=now,
        plugin_version=plugin_version,
    )
    envelope = _exact_dict(envelope_value, frozenset({"receipt", "signature"}))
    if envelope is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    signed_payload = canonical_bytes({
        "request": request,
        "receipt": envelope["receipt"],
    })
    if not _base._verify_sshsig(signed_payload, envelope["signature"]):
        raise PluginAdoptionAuthorityError("authority_signature_unavailable")
    request_digest = hashlib.sha256(request_bytes).hexdigest()
    receipt, allowed = _validate_receipt(
        envelope["receipt"],
        request=request,
        request_digest=request_digest,
        now=now,
        plugin_version=plugin_version,
    )
    return VerifiedPluginAdoptionEnvelope(
        request=request,
        receipt=receipt,
        request_bytes=request_bytes,
        envelope_bytes=envelope_bytes,
        request_digest=request_digest,
        envelope_digest=hashlib.sha256(envelope_bytes).hexdigest(),
        allowed=allowed,
    )


def request_plugin_adoption_decision(
    request: object,
    *,
    now: float,
) -> VerifiedPluginAdoptionEnvelope:
    """Send one exact request to the fixed V3 service and verify its envelope."""

    checked = validate_request(request, now=now)
    request_bytes = canonical_bytes(checked)
    if not _base._trusted_runtime_boundary():
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    socket_path = _base._fixed_authority_socket_path()
    if socket_path is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    try:
        deadline = _base._NATIVE_TIME_MONOTONIC() + (
            _base._PROTECTED_AUTHORITY_CONNECT_TIMEOUT_SECONDS
        )
        client = _base._NATIVE_SOCKET_CLASS(
            _base._NATIVE_SOCKET_AF_UNIX,
            _base._NATIVE_SOCKET_SOCK_STREAM,
        )
        try:
            remaining = deadline - _base._NATIVE_TIME_MONOTONIC()
            if remaining <= 0:
                raise TimeoutError
            _base._NATIVE_SOCKET_SETTIMEOUT(client, remaining)
            _base._NATIVE_SOCKET_CONNECT(client, str(socket_path))
            _base._NATIVE_SOCKET_SENDALL(client, request_bytes + b"\n")
            chunks = bytearray()
            while b"\n" not in chunks:
                remaining = deadline - _base._NATIVE_TIME_MONOTONIC()
                if remaining <= 0:
                    raise TimeoutError
                _base._NATIVE_SOCKET_SETTIMEOUT(client, remaining)
                chunk = _base._NATIVE_SOCKET_RECV(client, 8192)
                if not chunk:
                    raise RuntimeError
                chunks.extend(chunk)
                if len(chunks) > _base._PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES:
                    raise RuntimeError
        finally:
            _base._NATIVE_SOCKET_CLOSE(client)
        line, separator, remainder = bytes(chunks).partition(b"\n")
        if separator != b"\n" or remainder:
            raise RuntimeError
    except Exception as exc:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable") from exc
    return verify_plugin_adoption_envelope(
        request_bytes=request_bytes,
        envelope_bytes=line,
        now=now,
    )


def request_plugin_adoption_terminal_decision(
    request: object,
    *,
    now: float,
    prepared_replay: bool = False,
) -> VerifiedPluginAdoptionEnvelope:
    """Send a fresh request or replay exact durably prepared request bytes."""

    if type(prepared_replay) is not bool:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    checked = validate_terminal_request(
        request,
        now=None if prepared_replay else now,
    )
    request_bytes = canonical_bytes(checked)
    if not _base._trusted_runtime_boundary():
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    socket_path = _base._fixed_authority_socket_path()
    if socket_path is None:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable")
    try:
        deadline = _base._NATIVE_TIME_MONOTONIC() + (
            _base._PROTECTED_AUTHORITY_CONNECT_TIMEOUT_SECONDS
        )
        client = _base._NATIVE_SOCKET_CLASS(
            _base._NATIVE_SOCKET_AF_UNIX,
            _base._NATIVE_SOCKET_SOCK_STREAM,
        )
        try:
            remaining = deadline - _base._NATIVE_TIME_MONOTONIC()
            if remaining <= 0:
                raise TimeoutError
            _base._NATIVE_SOCKET_SETTIMEOUT(client, remaining)
            _base._NATIVE_SOCKET_CONNECT(client, str(socket_path))
            _base._NATIVE_SOCKET_SENDALL(client, request_bytes + b"\n")
            chunks = bytearray()
            while b"\n" not in chunks:
                remaining = deadline - _base._NATIVE_TIME_MONOTONIC()
                if remaining <= 0:
                    raise TimeoutError
                _base._NATIVE_SOCKET_SETTIMEOUT(client, remaining)
                chunk = _base._NATIVE_SOCKET_RECV(client, 8192)
                if not chunk:
                    raise RuntimeError
                chunks.extend(chunk)
                if len(chunks) > _base._PROTECTED_AUTHORITY_MAX_RESPONSE_BYTES:
                    raise RuntimeError
        finally:
            _base._NATIVE_SOCKET_CLOSE(client)
        line, separator, remainder = bytes(chunks).partition(b"\n")
        if separator != b"\n" or remainder:
            raise RuntimeError
    except Exception as exc:
        raise PluginAdoptionAuthorityError("authority_contract_unavailable") from exc
    return verify_plugin_adoption_terminal_envelope(
        request_bytes=request_bytes,
        envelope_bytes=line,
        now=(
            float(checked["actual"]["issued_at"]) + 0.001
            if prepared_replay
            else now
        ),
    )


__all__ = [
    "AUTHORITY_BUNDLE_DIGEST",
    "AUTHORITY_BUNDLE_VERSION",
    "AUTHORITY_CONSUMER",
    "AUTHORITY_OWNER",
    "CONTRACT_DIGEST",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "MARKETPLACE_ID",
    "OPERATION",
    "ORDINARY_APPLY_PLUGIN_VERSION",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "PREVIOUS_TERMINAL_PLUGIN_VERSION",
    "PluginAdoptionAuthorityError",
    "TARGET_SET",
    "TERMINAL_CONTRACT_DESCRIPTOR",
    "TERMINAL_CONTRACT_DIGEST",
    "TERMINAL_CONTRACT_ID",
    "TERMINAL_CONTRACT_VERSION",
    "TERMINAL_ALLOW_CODE",
    "TERMINAL_DENY_CODE",
    "TERMINAL_OPERATION",
    "TERMINAL_PLUGIN_VERSION",
    "TERMINAL_SOURCE_REVISION",
    "TERMINAL_TRANSITION_SET",
    "TRANSITION_SET",
    "VerifiedPluginAdoptionEnvelope",
    "build_plugin_adoption_request",
    "build_plugin_adoption_terminal_request",
    "canonical_bytes",
    "compute_plan_digest",
    "compute_terminal_plan_digest",
    "request_plugin_adoption_decision",
    "request_plugin_adoption_terminal_decision",
    "validate_terminal_request",
    "validate_request",
    "verify_plugin_adoption_envelope",
    "verify_plugin_adoption_terminal_envelope",
    "verify_previous_terminal_plugin_adoption_envelope",
]
