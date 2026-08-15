"""Executable Maestro V3 authority-consumer contract for ORCH operations."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
from copy import deepcopy
from pathlib import Path

import pytest

from tui_gateway import maestro_authority as authority
from tui_gateway import server


def test_gateway_binds_authority_consumer_before_project_imports():
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    authority_import_seen = False
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules = [node.module]
        else:
            continue
        if "tui_gateway.maestro_authority" in modules:
            authority_import_seen = True
            break
        assert all(
            module.split(".", 1)[0] in sys.stdlib_module_names for module in modules
        )

    assert authority_import_seen is True


def _context(decision_id: str = "decision-authority-consumer") -> dict:
    now = time.time()
    return {
        "contract_version": authority.HERMES_OPERATIONAL_CONTEXT_VERSION,
        "authority_bundle": {
            "identity": authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_ID,
            "version": authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
            "digest": authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST,
        },
        "threshold_policy": {"version": "threshold-policy.v1", "digest": "a" * 64},
        "decision_binding": {
            "decision_id": decision_id,
            "requester": authority.HERMES_AUTHORITY_CONSUMER,
            "account_id": "account-test",
            "project_id": "project-test",
            "logical_session_id": "logical-test",
            "method": authority.HERMES_OPERATIONAL_METHOD,
            "target": authority.HERMES_OPERATIONAL_TARGET,
            "runtime_revision": "1" * 40,
        },
        "goal": authority.HERMES_OPERATIONAL_GOAL,
        "operation": authority.HERMES_OPERATIONAL_METHOD,
        "target": authority.HERMES_OPERATIONAL_TARGET,
        "revision": authority.HERMES_OPERATIONAL_REVISION,
        "issued_at": now - 1,
        "expires_at": now + 120,
        "operation_id": "operation-test",
        "task_declaration": {
            "task_class": "implementation",
            "prompt_contract_version": "orch_prompt.v1",
            "prompt_contract_digest": "b" * 64,
        },
    }


def _actual() -> dict:
    return {
        "logical_session_id": "logical-test",
        "ui_session_id": "ui-test",
        "method": authority.HERMES_OPERATIONAL_METHOD,
        "target": authority.HERMES_OPERATIONAL_TARGET,
        "runtime_revision": "1" * 40,
    }


def test_session_token_install_request_is_fresh_bounded_and_policy_pinned(
    monkeypatch,
):
    nonces = iter((b"\x01" * 16, b"\x02" * 16))
    monkeypatch.setattr(authority, "_wall_clock_now", lambda: 1_000.0)
    monkeypatch.setattr(authority, "_NATIVE_OS_URANDOM", lambda size: next(nonces))

    first = authority.build_session_token_install_authority_request(
        logical_session_id="orch-next-session-token-target",
        runtime_revision="1" * 40,
    )
    second = authority.build_session_token_install_authority_request(
        logical_session_id="orch-next-session-token-target",
        runtime_revision="1" * 40,
    )

    assert first is not None
    assert second is not None
    assert first["decision_binding"] == {
        "decision_id": first["operation_id"],
        "requester": authority.HERMES_AUTHORITY_CONSUMER,
        "account_id": "orch-next-runtime",
        "project_id": "hermes-exclusive-harness",
        "logical_session_id": "orch-next-session-token-target",
        "method": authority.HERMES_OPERATIONAL_METHOD,
        "target": authority.HERMES_OPERATIONAL_TARGET,
        "runtime_revision": "1" * 40,
    }
    assert (
        first["decision_binding"]["decision_id"]
        != second["decision_binding"]["decision_id"]
    )
    assert first["authority_bundle"] == {
        "identity": authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_ID,
        "version": authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
        "digest": "7d6bc36e50938f74ad2728ed3d87f272620086de7bfd928616c84bbdfd09412e",
    }
    assert first["threshold_policy"] == {
        "version": "hermes-operational-telemetry-schema.v2",
        "digest": "7c391860dd39fb01b9a466e3826d74261d30fafd1c609869a6d55a275dcb8748",
    }
    assert first["task_declaration"] == {
        "task_class": "operations",
        "prompt_contract_version": "orch_prompt.v1",
        "prompt_contract_digest": (
            "9a7f77b1dfa79c28b6d4532d11f73a99c26e6fe868eace7050edaf143ad3e8c2"
        ),
    }
    assert first["goal"] == authority.HERMES_OPERATIONAL_GOAL
    assert first["operation"] == authority.HERMES_OPERATIONAL_METHOD
    assert first["target"] == authority.HERMES_OPERATIONAL_TARGET
    assert first["revision"] == authority.HERMES_OPERATIONAL_REVISION
    assert first["issued_at"] == 1_000.0
    assert first["expires_at"] == 1_060.0
    assert first["expires_at"] - first["issued_at"] < (
        authority.HERMES_CONTEXT_MAX_TTL_SECONDS
    )


@pytest.mark.parametrize(
    ("logical_session_id", "runtime_revision"),
    [
        ("/private/host/profile", "1" * 40),
        ("orch-next-session-token-target", "1" * 39),
        ("orch-next-session-token-target", "A" * 40),
        ("orch-next-session-token-target", True),
    ],
)
def test_session_token_install_request_rejects_private_or_invalid_binding(
    logical_session_id,
    runtime_revision,
):
    assert (
        authority.build_session_token_install_authority_request(
            logical_session_id=logical_session_id,
            runtime_revision=runtime_revision,
        )
        is None
    )


def test_session_token_install_request_has_no_caller_policy_injection_surface():
    with pytest.raises(TypeError):
        authority.build_session_token_install_authority_request(
            logical_session_id="orch-next-session-token-target",
            runtime_revision="1" * 40,
            authority_bundle_digest="0" * 64,
        )


def _runtime_provenance_manifest(runtime_commit: str = "1" * 40) -> dict:
    return {
        "upstreamReleaseTag": "v2026.8.3",
        "upstreamPackageVersion": "0.20.0",
        "upstreamCommit": "3" * 40,
        "runtimeCommit": runtime_commit,
        "runtimeContentDigest": "4" * 64,
    }


def _runtime_provenance_digest(manifest: dict) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _allow_result(decision_id: str, runtime_commit: str = "1" * 40) -> dict:
    manifest = _runtime_provenance_manifest(runtime_commit)
    return {
        "outcome": "allow",
        "decision_id": decision_id,
        "consumed_once": True,
        "runtime_provenance_manifest": manifest,
        "runtime_provenance_manifest_digest": _runtime_provenance_digest(manifest),
    }


def _terminal_actual(transition: str = "final") -> dict:
    return {
        "logical_session_id": "logical-test",
        "ui_session_id": "ui-test",
        "runtime_revision": "1" * 40,
        "requested_transition": transition,
        "controller_owner_id": "controller-test",
        "owner_epoch": 7,
    }


def _terminal_receipt(
    actual: dict,
    *,
    decision_id: str = "terminal-decision-test",
    decision: str = "ALLOW_FINAL_IDLE",
    admitted: bool = True,
    findings: list[str] | None = None,
) -> dict:
    now = time.time()
    return {
        "contract_id": authority.HERMES_TERMINAL_AUTHORITY_CONTRACT_ID,
        "contract_version": authority.HERMES_TERMINAL_AUTHORITY_CONTRACT_VERSION,
        "authority_source_sha256": authority.HERMES_TERMINAL_AUTHORITY_SOURCE_SHA256,
        "profile_sha256": authority.HERMES_TERMINAL_PROFILE_SHA256,
        "decision_id": decision_id,
        "consumer_decision": decision,
        "admitted": admitted,
        "blocking_findings": findings or [],
        **actual,
        "issued_at": now - 1,
        "expires_at": now + 120,
        "consumed_once": True,
    }


def _receipt(context: dict, actual: dict, *, outcome: str = "allow") -> dict:
    binding = context["decision_binding"]
    allowed = outcome == "allow"
    provenance = _runtime_provenance_manifest(actual["runtime_revision"])
    return {
        "outcome": outcome,
        "code": "authority_allowed" if allowed else "authority_denied",
        "decision_id": binding["decision_id"],
        "authority_owner": authority.HERMES_AUTHORITY_OWNER,
        "authority_bundle_version": authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": authority.HERMES_AUTHORITY_CONSUMER,
        "telemetry_schema_version": authority.HERMES_TELEMETRY_SCHEMA_VERSION,
        "telemetry_schema_digest": authority.HERMES_TELEMETRY_SCHEMA_DIGEST,
        "rollback_admission_version": authority.HERMES_ROLLBACK_ADMISSION_VERSION,
        "rollback_admission_digest": authority.HERMES_ROLLBACK_ADMISSION_DIGEST,
        "account_id": binding["account_id"],
        "project_id": binding["project_id"],
        "logical_session_id": actual["logical_session_id"],
        "ui_session_id": actual["ui_session_id"],
        "method": actual["method"],
        "target": actual["target"],
        "runtime_revision": actual["runtime_revision"],
        "runtime_provenance_manifest": provenance,
        "runtime_provenance_manifest_digest": _runtime_provenance_digest(provenance),
        "issued_at": context["issued_at"],
        "expires_at": context["expires_at"],
        "final_decision_state": "final_allowed_once" if allowed else "final_denied",
        "final_execution_permitted": allowed,
        "consumed_once": True,
    }


@pytest.fixture()
def installed_transport(monkeypatch):
    handles = []

    def install(transport, *, provenance=True):
        authenticated_receipts = set()

        def protected_transport(context, actual):
            receipt = transport(context, actual)
            if provenance:
                authenticated_receipts.add(id(receipt))
            return receipt

        def verify_origin(receipt, _context, _actual):
            return id(receipt) in authenticated_receipts

        monkeypatch.setattr(
            authority,
            "_load_protected_receipt_origin_verifier",
            lambda admitted_transport: (
                verify_origin if admitted_transport is protected_transport else None
            ),
        )
        handle = authority.install_maestro_authority_transport(protected_transport)
        handles.append(handle)
        return handle

    yield install
    for handle in reversed(handles):
        authority.reset_maestro_authority_transport(handle)


def test_server_defaults_to_executable_fail_closed_consumer():
    assert (
        server._orch_authority_validator is authority.consume_maestro_authority_decision
    )


def test_ordinary_prompt_submit_is_authority_inapplicable_without_a_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server,
        "_orch_authority_validator",
        lambda *_args: calls.append(True),
    )

    checked, error = server._validate_orch_submit_context(
        {"operational_class": "ordinary"}, "ordinary-rid"
    )

    assert checked is None
    assert error is None
    assert calls == []
    source = (Path(server.__file__).parent / "methods_prompt.py").read_text(
        encoding="utf-8"
    )
    assert "ordinary" in source
    assert source.index("if orch_context is not None:") < source.index(
        "_admit_orch_submit_context"
    )


def test_protected_orch_prompt_stays_required_and_fail_closed():
    checked, error = server._validate_orch_submit_context(
        {"operational_class": "orch"}, "protected-rid"
    )

    assert checked is None
    assert error is not None
    assert error["error"]["message"] == "orch_operational_context_missing"
    source = (Path(server.__file__).parent / "methods_prompt.py").read_text(
        encoding="utf-8"
    )
    assert "_admit_orch_submit_context" in source
    assert "_orch_operational_error" in source


def test_old_receipt_without_runtime_provenance_fails_closed(
    installed_transport,
):
    def old_transport(context, actual):
        receipt = _receipt(context, actual)
        receipt.pop("runtime_provenance_manifest")
        receipt.pop("runtime_provenance_manifest_digest")
        return receipt

    installed_transport(old_transport)
    assert authority.consume_maestro_authority_decision(
        _context("decision-old-receipt"), _actual()
    ) == {"outcome": "deny", "code": "authority_contract_unavailable"}


@pytest.mark.parametrize("mutation", ["missing", "extra", "digest", "foreign"])
def test_runtime_provenance_decision_export_fails_closed(
    installed_transport, mutation: str
):
    def invalid_transport(context, actual):
        receipt = _receipt(context, actual)
        manifest = receipt["runtime_provenance_manifest"]
        if mutation == "missing":
            manifest.pop("runtimeContentDigest")
        elif mutation == "extra":
            manifest["callerSelected"] = "forbidden"
        elif mutation == "digest":
            receipt["runtime_provenance_manifest_digest"] = "0" * 64
        else:
            manifest["runtimeCommit"] = "9" * 40
            receipt["runtime_provenance_manifest_digest"] = _runtime_provenance_digest(
                manifest
            )
        return receipt

    installed_transport(invalid_transport)
    result = authority.consume_maestro_authority_decision(
        _context(f"decision-provenance-{mutation}"), _actual()
    )
    assert result == {"outcome": "deny", "code": "authority_mismatch"}


def test_signed_predecessor_provenance_remains_a_typed_deny(installed_transport):
    def predecessor_transport(context, actual):
        receipt = _receipt(context, actual, outcome="deny")
        manifest = receipt["runtime_provenance_manifest"]
        manifest["runtimeCommit"] = "8" * 40
        manifest["runtimeContentDigest"] = "9" * 64
        receipt["runtime_provenance_manifest_digest"] = _runtime_provenance_digest(
            manifest
        )
        return receipt

    installed_transport(predecessor_transport)
    assert authority.consume_maestro_authority_decision(
        _context("decision-predecessor-deny"), _actual()
    ) == {"outcome": "deny", "code": "authority_denied"}


@pytest.mark.parametrize("contract_pair", ["legacy_lifecycle", "caller_selected"])
def test_signed_policy_denial_never_reaches_runtime_action(
    installed_transport,
    contract_pair: str,
):
    context = _context(f"decision-policy-deny-{contract_pair}")
    if contract_pair == "legacy_lifecycle":
        context["threshold_policy"] = {
            "version": "runtime-provenance-startup.v1",
            "digest": hashlib.sha256(
                b"orch-next-hermes-runtime-provenance-startup.v1"
            ).hexdigest(),
        }
        context["task_declaration"] = {
            "task_class": "operations",
            "prompt_contract_version": "hermes-mcp-startup.v1",
            "prompt_contract_digest": hashlib.sha256(
                b"hermes-mcp-startup-runtime-provenance.v1"
            ).hexdigest(),
        }
    else:
        context["threshold_policy"] = {
            "version": "caller-threshold-policy.v1",
            "digest": "5" * 64,
        }
        context["task_declaration"] = {
            "task_class": "operations",
            "prompt_contract_version": "caller-prompt-contract.v1",
            "prompt_contract_digest": "6" * 64,
        }

    def policy_transport(checked_context, actual):
        policy_allowed = checked_context["threshold_policy"] == {
            "version": authority.HERMES_TELEMETRY_SCHEMA_VERSION,
            "digest": authority.HERMES_TELEMETRY_SCHEMA_DIGEST,
        } and checked_context["task_declaration"] == {
            "task_class": "operations",
            "prompt_contract_version": (
                authority.HERMES_SESSION_TOKEN_PROMPT_CONTRACT_VERSION
            ),
            "prompt_contract_digest": (
                authority.HERMES_SESSION_TOKEN_PROMPT_CONTRACT_DIGEST
            ),
        }
        return _receipt(
            checked_context,
            actual,
            outcome="allow" if policy_allowed else "deny",
        )

    installed_transport(policy_transport)
    result = authority.consume_maestro_authority_decision(context, _actual())
    runtime_actions = []
    if result.get("outcome") == "allow":
        runtime_actions.append("runtime-action")

    assert result == {"outcome": "deny", "code": "authority_denied"}
    assert runtime_actions == []


def test_unverified_receipt_never_reads_runtime_provenance(installed_transport):
    class PoisonedManifest(dict):
        def keys(self):
            raise AssertionError("unverified provenance was read")

    def unauthenticated_transport(context, actual):
        receipt = _receipt(context, actual)
        receipt["runtime_provenance_manifest"] = PoisonedManifest()
        return receipt

    installed_transport(unauthenticated_transport, provenance=False)
    assert authority.consume_maestro_authority_decision(
        _context("decision-unverified-provenance"), _actual()
    ) == {"outcome": "deny", "code": "authority_contract_unavailable"}
    result = server._orch_authority_validator(
        _context("decision-default-unavailable"), _actual()
    )
    assert result == {"outcome": "deny", "code": "authority_contract_unavailable"}
    assert (
        server._orch_terminal_authority_validator
        is authority.consume_maestro_terminal_decision
    )


def test_terminal_authority_accepts_only_atomic_signed_shape(monkeypatch):
    actual = _terminal_actual()
    receipt = _terminal_receipt(actual)
    monkeypatch.setattr(
        authority,
        "_fixed_protected_terminal_transport",
        lambda checked: receipt if checked == actual else None,
    )
    result = authority.consume_maestro_terminal_decision(actual)
    assert result == {
        "consumer_decision": "ALLOW_FINAL_IDLE",
        "admitted": True,
        "blocking_findings": [],
        "decision_id": "terminal-decision-test",
        "controller_owner_id": "controller-test",
        "owner_epoch": 7,
        "consumed_once": True,
    }


def test_terminal_authority_rejects_replay_and_non_atomic_allow(monkeypatch):
    actual = _terminal_actual()
    receipts = iter((
        _terminal_receipt(
            actual,
            decision_id="terminal-non-atomic",
            admitted=True,
            findings=["BLOCKED_FOR_INC191_OWNER_MISMATCH"],
        ),
        _terminal_receipt(actual, decision_id="terminal-replay"),
        _terminal_receipt(actual, decision_id="terminal-replay"),
    ))
    monkeypatch.setattr(
        authority, "_fixed_protected_terminal_transport", lambda _actual: next(receipts)
    )
    assert authority.consume_maestro_terminal_decision(actual) == {
        "consumer_decision": "CONTINUE_CURRENT_CONTROLLER",
        "admitted": False,
        "blocking_findings": ["terminal_authority_mismatch"],
        "code": "terminal_authority_mismatch",
    }
    assert authority.consume_maestro_terminal_decision(actual)["admitted"] is True
    assert authority.consume_maestro_terminal_decision(actual) == {
        "consumer_decision": "CONTINUE_CURRENT_CONTROLLER",
        "admitted": False,
        "blocking_findings": ["terminal_authority_replay"],
        "code": "terminal_authority_replay",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("controller_owner_id", "other-controller"),
        ("owner_epoch", 6),
    ],
)
def test_terminal_authority_rejects_wrong_owner_or_stale_positive_epoch(
    monkeypatch, field, value
):
    actual = _terminal_actual()
    receipt = _terminal_receipt(
        actual,
        decision_id=f"terminal-binding-{field}",
    )
    receipt[field] = value
    monkeypatch.setattr(
        authority,
        "_fixed_protected_terminal_transport",
        lambda _actual: receipt,
    )

    assert authority.consume_maestro_terminal_decision(actual) == {
        "consumer_decision": "CONTINUE_CURRENT_CONTROLLER",
        "admitted": False,
        "blocking_findings": ["terminal_authority_mismatch"],
        "code": "terminal_authority_mismatch",
    }


def test_server_source_has_one_default_and_telemetry_callable_shape():
    assert (
        server._orch_authority_validator is authority.consume_maestro_authority_decision
    )
    parameters = inspect.signature(
        authority.consume_maestro_authority_decision
    ).parameters
    assert tuple(parameters) == ("operational_context", "actual_identity")


def test_exact_allow_and_deny_receipts_are_consumed_once(installed_transport):
    calls = []

    def transport(context, actual):
        calls.append((context, actual))
        return _receipt(context, actual)

    allow_handle = installed_transport(transport)
    context = _context("decision-exact-allow")
    result = authority.consume_maestro_authority_decision(context, _actual())
    assert result == _allow_result("decision-exact-allow")
    assert len(calls) == 1
    assert calls[0][0] is not context
    assert set(calls[0][0]) == set(_context())
    assert "text" not in calls[0][0]

    assert authority.reset_maestro_authority_transport(allow_handle) is True
    denied_context = _context("decision-exact-deny")
    installed_transport(
        lambda context, actual: _receipt(context, actual, outcome="deny")
    )
    denied = authority.consume_maestro_authority_decision(denied_context, _actual())
    assert denied == {"outcome": "deny", "code": "authority_denied"}


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("authority_bundle", "identity"), "HERMES_MAESTRO_AUTHORITY_BUNDLE_V1"),
        (("authority_bundle", "version"), "hermes-maestro-authority-bundle.v1"),
        (("authority_bundle", "digest"), "0" * 64),
        (("decision_binding", "requester"), "maestro-kernel"),
        (("decision_binding", "method"), "session.status"),
        (("decision_binding", "target"), "maestro"),
        (("decision_binding", "runtime_revision"), "2" * 40),
    ],
)
def test_wrong_context_authority_identity_never_calls_transport(
    installed_transport, path, value
):
    calls = []
    installed_transport(lambda *_args: calls.append(True))
    context = _context(f"decision-context-mismatch-{path[-1]}")
    context[path[0]][path[1]] = value
    result = authority.consume_maestro_authority_decision(context, _actual())
    assert result == {"outcome": "deny", "code": "authority_mismatch"}
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("logical_session_id", "other-session"),
        ("method", "session.status"),
        ("target", "maestro"),
        ("runtime_revision", "2" * 40),
    ],
)
def test_wrong_actual_identity_never_calls_transport(installed_transport, field, value):
    calls = []
    installed_transport(lambda *_args: calls.append(True))
    actual = _actual()
    actual[field] = value
    result = authority.consume_maestro_authority_decision(
        _context(f"decision-actual-mismatch-{field}"), actual
    )
    assert result == {"outcome": "deny", "code": "authority_mismatch"}
    assert calls == []


@pytest.mark.parametrize(
    ("case", "invalid"),
    [
        ("short", "1" * 39),
        ("nonhex", "G" * 40),
        ("uppercase", "A" * 40),
        ("boolean", True),
    ],
)
def test_runtime_revision_requires_exact_full_lowercase_sha(
    installed_transport, case, invalid
):
    calls = []
    installed_transport(lambda *_args: calls.append(True))

    context = _context(f"decision-invalid-context-runtime-{case}")
    context["decision_binding"]["runtime_revision"] = invalid
    assert authority.consume_maestro_authority_decision(context, _actual()) == {
        "outcome": "deny",
        "code": "authority_contract_unavailable",
    }

    actual = _actual()
    actual["runtime_revision"] = invalid
    assert authority.consume_maestro_authority_decision(
        _context(f"decision-invalid-actual-runtime-{case}"),
        actual,
    ) == {"outcome": "deny", "code": "authority_mismatch"}
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authority_owner", "hermes"),
        ("authority_bundle_version", "hermes-maestro-authority-bundle.v1"),
        ("authority_bundle_digest", "0" * 64),
        ("authority_consumer", "maestro_generic_runtime"),
        ("telemetry_schema_digest", "0" * 64),
        ("rollback_admission_digest", "0" * 64),
        ("account_id", "other-account"),
        ("project_id", "other-project"),
        ("logical_session_id", "other-session"),
        ("ui_session_id", "other-ui"),
        ("method", "session.status"),
        ("target", "maestro"),
        ("runtime_revision", "2" * 40),
        ("decision_id", "other-decision"),
    ],
)
def test_wrong_receipt_binding_is_typed_mismatch_and_never_retried(
    installed_transport, field, value
):
    calls = []

    def transport(context, actual):
        calls.append(True)
        receipt = _receipt(context, actual)
        receipt[field] = value
        return receipt

    installed_transport(transport)
    decision_id = f"decision-receipt-mismatch-{field}"
    context = _context(decision_id)
    first = authority.consume_maestro_authority_decision(context, _actual())
    second = authority.consume_maestro_authority_decision(context, _actual())
    assert first == {"outcome": "deny", "code": "authority_mismatch"}
    assert second == {"outcome": "deny", "code": "authority_replay"}
    assert calls == [True]


def test_local_forged_or_malformed_receipt_cannot_authorize(installed_transport):
    calls = []

    def transport(*_args):
        calls.append(True)
        return {
            "outcome": "allow",
            "decision_id": "decision-local-forgery",
            "consumed_once": True,
        }

    installed_transport(transport)
    result = authority.consume_maestro_authority_decision(
        _context("decision-local-forgery"), _actual()
    )
    assert result == {"outcome": "deny", "code": "authority_contract_unavailable"}
    assert calls == [True]


def test_field_perfect_local_receipt_without_origin_provenance_cannot_authorize(
    installed_transport,
):
    installed_transport(
        lambda context, actual: _receipt(context, actual), provenance=False
    )
    result = authority.consume_maestro_authority_decision(
        _context("decision-field-perfect-unproven"), _actual()
    )
    assert result == {"outcome": "deny", "code": "authority_contract_unavailable"}


def test_public_install_has_no_source_local_bootstrap_or_issuer():
    with pytest.raises(RuntimeError, match="protected Maestro bootstrap unavailable"):
        authority.install_maestro_authority_transport(lambda *_args: None)
    assert not hasattr(
        authority, "_issue_maestro_bootstrap_capability_for_protected_integration"
    )
    assert not hasattr(authority, "_BOOTSTRAP_CAPABILITY_BRAND")


def test_import_substitution_cannot_install_an_always_true_origin_verifier(
    monkeypatch,
):
    package = types.ModuleType("maestro_protected_transition")
    bootstrap = types.ModuleType("maestro_protected_transition.hermes_authority")
    bootstrap.admit_hermes_authority_transport = lambda *_args: (
        lambda *_verify_args: True
    )
    monkeypatch.setitem(sys.modules, "maestro_protected_transition", package)
    monkeypatch.setitem(
        sys.modules,
        "maestro_protected_transition.hermes_authority",
        bootstrap,
    )

    with pytest.raises(RuntimeError, match="protected Maestro bootstrap unavailable"):
        authority.install_maestro_authority_transport(lambda *_args: None)


def test_fixed_receipt_verifier_accepts_only_pinned_sshsig(monkeypatch, tmp_path):
    private_key = tmp_path / "signing-key"
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "hermes-authority-test",
            "-f",
            str(private_key),
        ],
        check=True,
        capture_output=True,
    )
    public_key = (tmp_path / "signing-key.pub").read_text(encoding="ascii").strip()
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text(
        f"{authority.HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY} {public_key}\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o600)
    payload = b'{"bounded":"receipt"}'
    payload_path = tmp_path / "payload"
    payload_path.write_bytes(payload)
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            authority.HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE,
            str(payload_path),
        ],
        check=True,
        capture_output=True,
    )
    signature = (tmp_path / "payload.sig").read_text(encoding="ascii")
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        allowed_signers,
    )
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
    )

    assert authority._verify_sshsig(payload, signature) is True
    assert authority._verify_sshsig(payload + b"-tampered", signature) is False
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        "0" * 64,
    )
    assert authority._verify_sshsig(payload, signature) is False


def test_committed_allowed_signers_is_exact_public_trust_anchor():
    expected = (
        b"maestro-kernel ssh-ed25519 "
        b"AAAAC3NzaC1lZDI1NTE5AAAAIKmhfh3yegLM7LuaSQPt/kLnhrK038kFpHIbdytA+dUZ\n"
    )

    assert authority._PROTECTED_AUTHORITY_ALLOWED_SIGNERS == Path(
        authority.__file__
    ).with_name("maestro_authority_allowed_signers")
    assert authority._fixed_allowed_signers_content() == expected
    assert hashlib.sha256(expected).hexdigest() == (
        authority.HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256
    )


def test_fixed_allowed_signers_ignores_preimport_caller_environment(monkeypatch):
    monkeypatch.setenv(
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        "/tmp/caller-selected-allowed-signers",
    )
    script = """
from tui_gateway import maestro_authority as candidate

print(candidate._PROTECTED_AUTHORITY_ALLOWED_SIGNERS.name)
print(candidate._fixed_allowed_signers_content() is not None)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.splitlines() == [
        "maestro_authority_allowed_signers",
        "True",
    ]


@pytest.mark.parametrize("failure", ["missing", "digest", "symlink", "mode", "owner"])
def test_fixed_allowed_signers_rejects_unadmitted_files(
    monkeypatch,
    tmp_path,
    failure,
):
    content = b"maestro-kernel ssh-ed25519 AAAAC3NzaUnadmitted\n"
    admitted = tmp_path / "admitted"
    admitted.write_bytes(content)
    admitted.chmod(0o600)
    selected = admitted
    digest = hashlib.sha256(content).hexdigest()

    if failure == "missing":
        selected = tmp_path / "missing"
    elif failure == "digest":
        digest = "0" * 64
    elif failure == "symlink":
        selected = tmp_path / "alias"
        selected.symlink_to(admitted)
    elif failure == "mode":
        admitted.chmod(0o620)
    elif failure == "owner":
        monkeypatch.setattr(authority.os, "getuid", lambda: os.getuid() + 1)

    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS", selected)
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        digest,
    )

    assert authority._fixed_allowed_signers_content() is None


def test_fixed_receipt_verifier_rejects_wrong_signer_identity(monkeypatch, tmp_path):
    private_key = tmp_path / "wrong-identity-key"
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(private_key),
        ],
        check=True,
        capture_output=True,
    )
    public_key = (
        (tmp_path / "wrong-identity-key.pub").read_text(encoding="ascii").split()
    )
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text(
        f"attacker-identity {public_key[0]} {public_key[1]}\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o600)
    payload = b'{"bounded":"wrong-identity"}'
    payload_path = tmp_path / "payload"
    payload_path.write_bytes(payload)
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            authority.HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE,
            str(payload_path),
        ],
        check=True,
        capture_output=True,
    )
    signature = (tmp_path / "payload.sig").read_text(encoding="ascii")
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        allowed_signers,
    )
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
    )

    assert authority._verify_sshsig(payload, signature) is False


def test_fixed_receipt_verifier_uses_hashed_snapshot_not_swapped_path(
    monkeypatch,
    tmp_path,
):
    def create_key(name: str) -> tuple[Path, str]:
        private_key = tmp_path / name
        subprocess.run(
            [
                "/usr/bin/ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                name,
                "-f",
                str(private_key),
            ],
            check=True,
            capture_output=True,
        )
        return private_key, (tmp_path / f"{name}.pub").read_text(
            encoding="ascii"
        ).strip()

    _trusted_key, trusted_public = create_key("trusted")
    attacker_key, attacker_public = create_key("attacker")
    allowed_signers = tmp_path / "allowed-signers"
    trusted_content = (
        f"{authority.HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY} {trusted_public}\n"
    ).encode("ascii")
    attacker_content = (
        f"{authority.HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY} {attacker_public}\n"
    ).encode("ascii")
    allowed_signers.write_bytes(trusted_content)
    allowed_signers.chmod(0o600)
    payload = b'{"bounded":"attacker-receipt"}'
    payload_path = tmp_path / "attacker-payload"
    payload_path.write_bytes(payload)
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(attacker_key),
            "-n",
            authority.HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE,
            str(payload_path),
        ],
        check=True,
        capture_output=True,
    )
    attacker_signature = (tmp_path / "attacker-payload.sig").read_text(encoding="ascii")
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        allowed_signers,
    )
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        hashlib.sha256(trusted_content).hexdigest(),
    )
    real_spawn = authority._spawn_sshsig_verify

    def swap_then_verify(payload_bytes, signature_bytes, signer_snapshot):
        allowed_signers.write_bytes(attacker_content)
        allowed_signers.chmod(0o600)
        return real_spawn(payload_bytes, signature_bytes, signer_snapshot)

    monkeypatch.setattr(authority, "_spawn_sshsig_verify", swap_then_verify)
    assert authority._verify_sshsig(payload, attacker_signature) is False


def test_fixed_receipt_verifier_does_not_import_subprocess_for_trust(
    monkeypatch,
    tmp_path,
):
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text(
        f"{authority.HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY} "
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKnownInvalidKey test\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o600)
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        allowed_signers,
    )
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
    )
    forged_subprocess = types.ModuleType("subprocess")
    forged_subprocess.run = lambda *_args, **_kwargs: types.SimpleNamespace(
        returncode=0
    )
    monkeypatch.setitem(sys.modules, "subprocess", forged_subprocess)
    framed_garbage = (
        "-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n"
    )
    assert authority._verify_sshsig(b"forged", framed_garbage) is False


def test_fixed_receipt_verifier_rejects_mutated_native_runtime_boundary(
    monkeypatch,
):
    framed_garbage = (
        "-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n"
    )
    monkeypatch.setattr(authority.os, "posix_spawn", lambda *_args, **_kwargs: 1)

    assert authority._trusted_runtime_boundary() is False
    assert authority._verify_sshsig(b"forged", framed_garbage) is False


def test_fixed_receipt_verifier_ignores_post_import_hash_mutation(
    monkeypatch,
):
    monkeypatch.setattr(authority.hashlib, "sha256", lambda *_args: object())

    assert authority._trusted_runtime_boundary() is True
    assert (
        authority._NATIVE_SHA256(b"fixed").hexdigest()
        == "992a93455c71fedd36ac9bbc439952c041cf61445958472af479269b8d873513"
    )


def test_protected_codec_ignores_preimport_json_substitution():
    script = """
import json as real_json
import sys
import types

forged_json = types.ModuleType("json")
forged_json.__dict__.update(real_json.__dict__)
forged_json.dumps = lambda *_args, **_kwargs: "replay"
forged_json.dumps.__module__ = "json"
sys.modules["json"] = forged_json

from tui_gateway import maestro_authority as candidate

print(candidate._trusted_runtime_boundary())
print(candidate._canonical_authority_payload({"challenge": "fresh"}).decode("ascii"))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.splitlines() == [
        "True",
        '{"challenge":"fresh"}',
    ]


def test_protected_codec_preserves_existing_canonical_json_contract():
    payload = {
        "array": [True, False, None, -0.0, 1.0, 1e-07],
        "nested": {"z": "\U0001f642", "a": 'line\nquote"slash\\'},
        "value": 42,
    }
    expected = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")

    assert authority._canonical_authority_payload(payload) == expected
    assert authority._parse_canonical_authority_payload(expected) == payload
    assert authority._parse_canonical_authority_payload(b'{"a":1,"a":2}') is None
    assert authority._parse_canonical_authority_payload(b'{"a": 1}') is None


def test_fixed_receipt_verifier_ignores_post_import_exit_status_mutation(
    monkeypatch,
):
    framed_garbage = (
        "-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n"
    )
    monkeypatch.setattr(authority.os, "waitstatus_to_exitcode", lambda _status: 0)

    assert authority._trusted_runtime_boundary() is True
    assert authority._verify_sshsig(b"forged", framed_garbage) is False


def test_fixed_receipt_verifier_rejects_preimport_exit_status_substitution():
    script = """
import os as real_os
import sys
import types

forged_os = types.ModuleType("os")
forged_os.__dict__.update(real_os.__dict__)
forged_os.waitstatus_to_exitcode = lambda _status: 0
sys.modules["os"] = forged_os

from tui_gateway import maestro_authority as candidate

print(candidate._trusted_runtime_boundary())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "False"


def test_fixed_receipt_verifier_rejects_preimport_nonblocking_flag_substitution():
    script = """
import os as real_os
import sys
import types

forged_os = types.ModuleType("os")
forged_os.__dict__.update(real_os.__dict__)
forged_os.WNOHANG = real_os.WUNTRACED
sys.modules["os"] = forged_os

from tui_gateway import maestro_authority as candidate

print(candidate._trusted_runtime_boundary())
print(candidate._NATIVE_OS_WNOHANG)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=1.0,
    )

    assert completed.stdout.splitlines() == ["False", str(os.WUNTRACED)]


def test_fixed_transport_rejects_preimport_native_socket_substitution():
    script = """
import _socket as real_socket
import sys
import types

forged_socket = types.ModuleType("_socket")
forged_socket.__dict__.update(real_socket.__dict__)

class ForgedSocket:
    def settimeout(self, _timeout):
        pass
    def connect(self, _path):
        pass
    def sendall(self, _payload):
        pass
    def recv(self, _size):
        return b""
    def close(self):
        pass

forged_socket.socket = ForgedSocket
sys.modules["_socket"] = forged_socket

from tui_gateway import maestro_authority as candidate

print(candidate._trusted_runtime_boundary())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=1.0,
    )

    assert completed.stdout.strip() == "False"


def test_fixed_allowed_signers_fifo_fails_bounded(monkeypatch, tmp_path):
    fifo = tmp_path / "allowed-signers-fifo"
    os.mkfifo(fifo, 0o600)
    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS", fifo)
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        "0" * 64,
    )
    started = time.monotonic()

    assert authority._fixed_allowed_signers_content() is None
    assert time.monotonic() - started < 0.5


def test_fixed_receipt_verifier_stalled_child_has_end_to_end_deadline(
    monkeypatch,
    tmp_path,
):
    stalled_verifier = tmp_path / "stalled-verifier"
    stalled_verifier.write_text("#!/bin/sh\nexec /bin/sleep 10\n", encoding="ascii")
    stalled_verifier.chmod(0o700)
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_SSH_KEYGEN",
        stalled_verifier,
    )
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_VERIFY_TIMEOUT_SECONDS",
        0.1,
    )
    monkeypatch.setattr(authority.os, "WNOHANG", 0)
    monkeypatch.setattr(authority.os, "kill", lambda *_args: None)
    assert authority._trusted_runtime_boundary() is True
    descriptors_before = len(os.listdir("/dev/fd"))
    started = time.monotonic()

    verified = authority._spawn_sshsig_verify(
        b"x" * 100_000,
        b"-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n",
        b"maestro-kernel ssh-ed25519 AAAA\n",
    )

    assert verified is False
    assert time.monotonic() - started < 1.0
    assert len(os.listdir("/dev/fd")) == descriptors_before


def test_fixed_receipt_verifier_handles_payload_larger_than_pipe_buffer(
    monkeypatch,
    tmp_path,
):
    private_key = tmp_path / "large-payload-key"
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(private_key),
        ],
        check=True,
        capture_output=True,
    )
    public_key = (
        (tmp_path / "large-payload-key.pub").read_text(encoding="ascii").strip()
    )
    allowed_signers = tmp_path / "allowed-signers-large"
    allowed_signers.write_text(
        f"{authority.HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY} {public_key}\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o600)
    payload = b"x" * 100_000
    payload_path = tmp_path / "large-payload"
    payload_path.write_bytes(payload)
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            authority.HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE,
            str(payload_path),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        allowed_signers,
    )
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
    )
    signature = (tmp_path / "large-payload.sig").read_text(encoding="ascii")

    assert authority._verify_sshsig(payload, signature) is True


def test_fixed_socket_route_rejects_symlinked_home(monkeypatch, tmp_path):
    real_home = tmp_path / "real-home"
    authority_dir = real_home / "authority"
    authority_dir.mkdir(parents=True, mode=0o700)
    real_home.chmod(0o700)
    authority_dir.chmod(0o700)
    configured_home = tmp_path / "configured-home"
    configured_home.symlink_to(real_home, target_is_directory=True)
    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_HOME", configured_home)

    assert authority._fixed_authority_socket_path() is None


def test_fixed_socket_route_rejects_symlinked_authority_directory(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "home"
    real_authority = tmp_path / "real-authority"
    home.mkdir(mode=0o700)
    real_authority.mkdir(mode=0o700)
    (home / "authority").symlink_to(real_authority, target_is_directory=True)
    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_HOME", home)

    assert authority._fixed_authority_socket_path() is None


def test_fixed_socket_route_ignores_in_process_environment_mutation(
    monkeypatch,
    request,
    tmp_path,
):
    home = Path("/private/tmp") / f"ha-home-{time.time_ns()}"
    authority_dir = home / "authority"
    authority_dir.mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    authority_dir.chmod(0o700)
    socket_path = authority_dir / authority._PROTECTED_AUTHORITY_SOCKET_LEAF
    request.addfinalizer(lambda: home.rmdir())
    request.addfinalizer(lambda: authority_dir.rmdir())
    request.addfinalizer(lambda: socket_path.unlink(missing_ok=True))
    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_HOME", home)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "attacker-home"))
        assert authority._fixed_authority_socket_path() == socket_path


def test_fixed_socket_route_rejects_insecure_home_permissions(
    monkeypatch,
    request,
):
    home = Path("/private/tmp") / f"ha-mode-{time.time_ns()}"
    authority_dir = home / "authority"
    authority_dir.mkdir(parents=True, mode=0o700)
    home.chmod(0o755)
    authority_dir.chmod(0o700)
    socket_path = authority_dir / authority._PROTECTED_AUTHORITY_SOCKET_LEAF
    request.addfinalizer(lambda: home.rmdir())
    request.addfinalizer(lambda: authority_dir.rmdir())
    request.addfinalizer(lambda: socket_path.unlink(missing_ok=True))
    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_HOME", home)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        assert authority._fixed_authority_socket_path() is None


def test_fixed_socket_route_rejects_wrong_owner(monkeypatch, request):
    home = Path("/private/tmp") / f"ha-owner-{time.time_ns()}"
    authority_dir = home / "authority"
    authority_dir.mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    authority_dir.chmod(0o700)
    socket_path = authority_dir / authority._PROTECTED_AUTHORITY_SOCKET_LEAF
    request.addfinalizer(lambda: home.rmdir())
    request.addfinalizer(lambda: authority_dir.rmdir())
    request.addfinalizer(lambda: socket_path.unlink(missing_ok=True))
    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_HOME", home)
    real_lstat = os.lstat

    def wrong_owner_for_authority_dir(path):
        result = real_lstat(path)
        if Path(path) != authority_dir:
            return result
        values = list(result)
        values[4] = os.getuid() + 1
        return os.stat_result(values)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        monkeypatch.setattr(authority.os, "lstat", wrong_owner_for_authority_dir)
        assert authority._fixed_authority_socket_path() is None


def test_fixed_socket_route_rejects_non_socket_and_replacement(
    monkeypatch,
    request,
):
    home = Path("/private/tmp") / f"ha-replace-{time.time_ns()}"
    authority_dir = home / "authority"
    authority_dir.mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    authority_dir.chmod(0o700)
    socket_path = authority_dir / authority._PROTECTED_AUTHORITY_SOCKET_LEAF
    request.addfinalizer(lambda: home.rmdir())
    request.addfinalizer(lambda: authority_dir.rmdir())
    request.addfinalizer(lambda: socket_path.unlink(missing_ok=True))
    monkeypatch.setattr(authority, "_PROTECTED_AUTHORITY_HOME", home)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        assert authority._fixed_authority_socket_path() == socket_path
    socket_path.unlink()
    socket_path.write_text("not a socket", encoding="ascii")
    assert authority._fixed_authority_socket_path() is None


def test_credential_free_transport_fails_before_socket_disclosure(monkeypatch):
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        "",
    )
    monkeypatch.setattr(
        authority,
        "_fixed_authority_socket_path",
        lambda: pytest.fail("socket route must not be consulted without trust anchor"),
    )

    with pytest.raises(RuntimeError, match="trust anchor unavailable"):
        authority._fixed_protected_authority_transport(_context(), _actual())


def test_fixed_socket_transport_drip_feed_has_one_end_to_end_deadline(
    monkeypatch,
    request,
    tmp_path,
):
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_bytes(b"maestro-kernel ssh-ed25519 AAAA\n")
    allowed_signers.chmod(0o600)
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        allowed_signers,
    )
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_CONNECT_TIMEOUT_SECONDS",
        0.1,
    )
    socket_path = Path("/tmp") / f"ha-drip-{time.time_ns()}.sock"
    request.addfinalizer(lambda: socket_path.unlink(missing_ok=True))
    monkeypatch.setattr(
        authority,
        "_fixed_authority_socket_path",
        lambda: socket_path,
    )
    listening = threading.Event()
    server_errors = []

    def serve_drip():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(1)
                listening.set()
                connection, _address = listener.accept()
                with connection:
                    request_line = bytearray()
                    while b"\n" not in request_line:
                        request_line.extend(connection.recv(8192))
                    until = time.monotonic() + 0.6
                    while time.monotonic() < until:
                        connection.sendall(b"{")
                        time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - asserted in caller
            server_errors.append(exc)
            listening.set()

    service = threading.Thread(target=serve_drip)
    service.start()
    assert listening.wait(timeout=2)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="transport unavailable"):
        authority._fixed_protected_authority_transport(_context(), _actual())
    elapsed = time.monotonic() - started
    service.join(timeout=2)

    assert not service.is_alive()
    assert server_errors == []
    assert elapsed < 0.3


def test_fixed_socket_transport_rejects_captured_envelope_after_restart(
    monkeypatch,
    request,
    tmp_path,
):
    private_key = tmp_path / "signing-key"
    subprocess.run(
        [
            "/usr/bin/ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "hermes-authority-test",
            "-f",
            str(private_key),
        ],
        check=True,
        capture_output=True,
    )
    public_key = (tmp_path / "signing-key.pub").read_text(encoding="ascii").strip()
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text(
        f"{authority.HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY} {public_key}\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o600)
    monkeypatch.setattr(
        authority,
        "_PROTECTED_AUTHORITY_ALLOWED_SIGNERS",
        allowed_signers,
    )
    monkeypatch.setattr(
        authority,
        "HERMES_PROTECTED_AUTHORITY_ALLOWED_SIGNERS_SHA256",
        hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
    )

    socket_path = Path("/tmp") / f"ha-{time.time_ns()}.sock"
    request.addfinalizer(lambda: socket_path.unlink(missing_ok=True))
    monkeypatch.setattr(
        authority,
        "_fixed_authority_socket_path",
        lambda: socket_path,
    )
    listening = threading.Event()
    server_errors = []

    observed_requests = []

    def serve_replay():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(2)
                listening.set()
                envelope = None
                for attempt in range(2):
                    connection, _address = listener.accept()
                    with connection:
                        request_line = bytearray()
                        while b"\n" not in request_line:
                            request_line.extend(connection.recv(8192))
                        received = json.loads(
                            bytes(request_line).rstrip(b"\n").decode("ascii")
                        )
                        observed_requests.append(received)
                        if attempt == 0:
                            receipt = _receipt(received["context"], received["actual"])
                            signed_payload = authority._canonical_authority_payload({
                                "request": received,
                                "receipt": receipt,
                            })
                            assert signed_payload is not None
                            payload_path = tmp_path / "signed-payload"
                            payload_path.write_bytes(signed_payload)
                            subprocess.run(
                                [
                                    "/usr/bin/ssh-keygen",
                                    "-Y",
                                    "sign",
                                    "-f",
                                    str(private_key),
                                    "-n",
                                    authority.HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE,
                                    str(payload_path),
                                ],
                                check=True,
                                capture_output=True,
                            )
                            envelope = authority._canonical_authority_payload({
                                "receipt": receipt,
                                "signature": (
                                    tmp_path / "signed-payload.sig"
                                ).read_text(encoding="ascii"),
                            })
                            assert envelope is not None
                        assert envelope is not None
                        connection.sendall(envelope + b"\n")
        except Exception as exc:  # pragma: no cover - asserted in caller
            server_errors.append(exc)
            listening.set()

    service = threading.Thread(target=serve_replay)
    service.start()
    assert listening.wait(timeout=2)
    context = _context("decision-fixed-socket-signed")
    first_result = authority.consume_maestro_authority_decision(context, _actual())
    with authority._state_lock:
        authority._pending_decision_ids.clear()
        authority._consumed_decision_ids.clear()
        authority._protected_origin_attestations.clear()
    replay_result = authority.consume_maestro_authority_decision(context, _actual())
    service.join(timeout=5)

    assert not service.is_alive()
    assert server_errors == []
    assert len(observed_requests) == 2
    assert set(observed_requests[0]) == {"context", "actual", "challenge"}
    assert observed_requests[0]["challenge"] != observed_requests[1]["challenge"]
    assert first_result == _allow_result("decision-fixed-socket-signed")
    assert replay_result == {
        "outcome": "deny",
        "code": "authority_contract_unavailable",
    }


def test_expiry_equality_is_stale_before_transport(installed_transport, monkeypatch):
    calls = []
    installed_transport(lambda *_args: calls.append(True))
    context = _context("decision-expiry-equality")
    context["issued_at"] = 990.0
    context["expires_at"] = 1_000.0
    monkeypatch.setattr(authority, "_wall_clock_now", lambda: 1_000.0)
    result = authority.consume_maestro_authority_decision(context, _actual())
    assert result == {"outcome": "deny", "code": "authority_stale"}
    assert calls == []


def test_duplicate_and_concurrent_replay_call_transport_once(installed_transport):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def transport(context, actual):
        calls.append(True)
        entered.set()
        assert release.wait(timeout=2)
        return _receipt(context, actual)

    installed_transport(transport)
    context = _context("decision-concurrent-replay")
    results = []
    first = threading.Thread(
        target=lambda: results.append(
            authority.consume_maestro_authority_decision(context, _actual())
        )
    )
    first.start()
    assert entered.wait(timeout=2)
    concurrent = authority.consume_maestro_authority_decision(context, _actual())
    release.set()
    first.join(timeout=2)
    assert not first.is_alive()
    repeated = authority.consume_maestro_authority_decision(context, _actual())
    assert concurrent == {"outcome": "deny", "code": "authority_replay"}
    assert repeated == {"outcome": "deny", "code": "authority_replay"}
    assert results == [_allow_result("decision-concurrent-replay")]
    assert calls == [True]


def test_transport_exception_is_sanitized_terminal_and_never_retried(
    installed_transport,
):
    calls = []

    def transport(*_args):
        calls.append(True)
        raise RuntimeError("secret-token=do-not-return")

    installed_transport(transport)
    context = _context("decision-secret-error")
    first = authority.consume_maestro_authority_decision(context, _actual())
    second = authority.consume_maestro_authority_decision(context, _actual())
    assert first == {"outcome": "deny", "code": "authority_contract_unavailable"}
    assert "secret-token" not in repr(first)
    assert second == {"outcome": "deny", "code": "authority_replay"}
    assert calls == [True]


def test_transport_cannot_mutate_expected_binding_into_an_allow(installed_transport):
    def transport(context, actual):
        context["decision_binding"]["account_id"] = "other-account"
        actual["logical_session_id"] = "other-session"
        return _receipt(context, actual)

    installed_transport(transport)
    result = authority.consume_maestro_authority_decision(
        _context("decision-mutated-snapshot"), _actual()
    )
    assert result == {"outcome": "deny", "code": "authority_mismatch"}


def test_receipt_that_expires_during_transport_is_stale(
    installed_transport, monkeypatch
):
    context = _context("decision-expired-during-transport")
    context["issued_at"] = 900.0
    context["expires_at"] = 1_000.0
    clock = iter((999.0, 1_000.0))
    monkeypatch.setattr(authority, "_wall_clock_now", lambda: next(clock))
    installed_transport(lambda checked, actual: _receipt(checked, actual))
    result = authority.consume_maestro_authority_decision(context, _actual())
    assert result == {"outcome": "deny", "code": "authority_stale"}


def test_transport_rotation_during_call_fails_closed_and_reset_is_generation_bound(
    installed_transport,
):
    old_handle = None
    replacement_blocked = []

    def old_transport(context, actual):
        assert old_handle is not None
        assert authority.reset_maestro_authority_transport(old_handle) is False
        with pytest.raises(RuntimeError, match="already installed"):
            installed_transport(lambda c, a: _receipt(c, a))
        replacement_blocked.append(True)
        return _receipt(context, actual)

    old_handle = installed_transport(old_transport)
    result = authority.consume_maestro_authority_decision(
        _context("decision-transport-rotated"), _actual()
    )
    assert result == _allow_result("decision-transport-rotated")
    assert replacement_blocked == [True]


def test_second_install_cannot_silently_replace_active_transport(installed_transport):
    installed_transport(lambda context, actual: _receipt(context, actual))
    with pytest.raises(RuntimeError, match="already installed"):
        installed_transport(lambda context, actual: _receipt(context, actual))


def test_missing_transport_terminalizes_decision_before_later_install(
    installed_transport,
):
    context = _context("decision-missing-terminal")
    first = authority.consume_maestro_authority_decision(context, _actual())
    assert first == {"outcome": "deny", "code": "authority_contract_unavailable"}
    calls = []
    installed_transport(
        lambda checked, actual: calls.append(True) or _receipt(checked, actual)
    )
    second = authority.consume_maestro_authority_decision(context, _actual())
    assert second == {"outcome": "deny", "code": "authority_replay"}
    assert calls == []


class _HostileString(str):
    __hash__ = str.__hash__

    def __eq__(self, _other):
        raise AssertionError("hostile comparison executed")

    def __ne__(self, _other):
        raise AssertionError("hostile comparison executed")


def test_hostile_scalar_subclasses_fail_typed_before_comparison(installed_transport):
    calls = []
    installed_transport(lambda *_args: calls.append(True))
    context = _context("decision-hostile-context")
    context["goal"] = _HostileString(authority.HERMES_OPERATIONAL_GOAL)
    result = authority.consume_maestro_authority_decision(context, _actual())
    assert result == {"outcome": "deny", "code": "authority_contract_unavailable"}
    assert calls == []


def test_hostile_receipt_scalars_fail_typed_before_comparison(installed_transport):
    def transport(context, actual):
        receipt = _receipt(context, actual)
        receipt["outcome"] = _HostileString("allow")
        return receipt

    installed_transport(transport)
    result = authority.consume_maestro_authority_decision(
        _context("decision-hostile-receipt"), _actual()
    )
    assert result == {"outcome": "deny", "code": "authority_mismatch"}


def test_dict_subclasses_and_non_builtin_keys_are_rejected_without_transport(
    installed_transport,
):
    calls = []
    installed_transport(lambda *_args: calls.append(True))

    class DictSubclass(dict):
        pass

    subclass_result = authority.consume_maestro_authority_decision(
        DictSubclass(_context("decision-dict-subclass")), _actual()
    )
    assert subclass_result == {
        "outcome": "deny",
        "code": "authority_contract_unavailable",
    }

    keyed = _context("decision-key-subclass")
    value = keyed.pop("goal")
    keyed[_HostileString("goal")] = value
    key_result = authority.consume_maestro_authority_decision(keyed, _actual())
    assert key_result == {
        "outcome": "deny",
        "code": "authority_contract_unavailable",
    }
    assert calls == []


def test_transport_receives_no_raw_prompt_logs_secrets_or_private_payloads(
    installed_transport,
):
    observed = []

    def transport(context, actual):
        observed.append((deepcopy(context), deepcopy(actual)))
        return _receipt(context, actual)

    installed_transport(transport)
    context = _context("decision-sanitized-only")
    context["private_payload"] = "must-not-cross"
    result = authority.consume_maestro_authority_decision(context, _actual())
    assert result == {"outcome": "deny", "code": "authority_contract_unavailable"}
    assert observed == []
