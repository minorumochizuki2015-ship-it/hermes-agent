"""Focused FP-2 pure-consumer and projection contract."""

from contextlib import contextmanager
import time

import pytest

import hermes_state
from tui_gateway import sdo_adapter, server


def _binding(operation_id: str) -> dict[str, str]:
    return {
        "project_id": "project-test",
        "repo_id": "repo-test",
        "worktree_id": "worktree-test",
        "goal_ref": "goal-test",
        "request_ref": "request-test",
        "transition": "natural-transition",
        "logical_session_id": "logical-test",
        "operation_id": operation_id,
    }


def _decision(operation_id: str, **overrides) -> dict:
    value = {
        "selection_reason": sdo_adapter.NATURAL_SELECTION_REASON,
        "provider": sdo_adapter.NATURAL_PROVIDER,
        "model": sdo_adapter.NATURAL_MODEL,
        "effort": sdo_adapter.NATURAL_EFFORT,
        "tier": sdo_adapter.NATURAL_TIER,
        "receipt_digest": "a" * 64,
        "expires_at": time.time() + 120.0,
        "binding": _binding(operation_id),
        "selected_action_id": "action-test",
        "base_selected_action_id": "action-test",
        "decision": "CONTINUE_LOCAL",
        "dispatch_mode": "continue_local",
    }
    value.update(overrides)
    return value


def _orch_context(operation_id: str) -> dict:
    now = time.time()
    return {
        "contract_version": hermes_state.HERMES_ORCH_OPERATIONAL_CONTEXT_VERSION,
        "authority_bundle": {
            "identity": hermes_state.HERMES_MAESTRO_AUTHORITY_BUNDLE_ID,
            "version": hermes_state.HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
            "digest": hermes_state.HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST,
        },
        "threshold_policy": {
            "version": "synthetic-threshold-policy.v1",
            "digest": "b" * 64,
        },
        "decision_binding": {
            "decision_id": "decision-" + operation_id,
            "requester": "hermes_operational_harness",
            "account_id": "account-test",
            "project_id": "project-test",
            "logical_session_id": "logical-test",
            "method": "prompt.submit",
            "target": "hermes",
            "runtime_revision": "1" * 40,
        },
        "goal": hermes_state.HERMES_ORCH_OPERATIONAL_GOAL,
        "operation": "prompt.submit",
        "target": "hermes",
        "revision": 1,
        "issued_at": now - 1.0,
        "expires_at": now + 120.0,
        "operation_id": operation_id,
        "task_declaration": {
            "task_class": "implementation",
            "prompt_contract_version": "prompt.v1",
            "prompt_contract_digest": "a" * 64,
        },
    }


def test_natural_route_binds_before_lazy_agent_build() -> None:
    decision = sdo_adapter.consume_sdo_decision(
        _decision("operation-current"),
        context={"operation_id": "operation-current"},
        now=1000.0,
    )
    assert decision["claim_status"] == "admitted"
    assert decision["model_route"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "service_tier_preference": "fast",
    }
    session = {}
    sdo_adapter.apply_sdo_decision_to_session(session, decision)
    assert session["model_override"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
    }
    assert session["create_reasoning_override"] == {
        "enabled": True,
        "effort": "max",
    }
    assert session["create_service_tier_override"] == "priority"


@pytest.mark.parametrize(
    ("provider", "expected_reason"),
    [
        (None, "sdo_provider_malformed"),
        ("terra", "sdo_provider_unsupported"),
    ],
)
def test_provider_withholding_never_selects_a_fallback(provider, expected_reason) -> None:
    value = _decision("operation-provider")
    if provider is None:
        value.pop("provider")
    else:
        value["provider"] = provider
    result = sdo_adapter.consume_sdo_decision(
        value,
        context={"operation_id": "operation-provider"},
        now=1000.0,
    )
    assert result["claim_status"] == "withheld"
    assert result["claim_withheld_reason"] == expected_reason
    assert result["safe_local_continuation"] is True
    assert result["model_route"] is None
    assert result["receipt_digest"] is None


def test_non_natural_route_is_withheld_without_identity_or_source_fallback() -> None:
    value = _decision("operation-route", selection_reason="legacy_luna")
    result = sdo_adapter.consume_sdo_decision(
        value,
        context={"operation_id": "operation-route"},
        now=1000.0,
    )
    assert result["claim_status"] == "withheld"
    assert result["claim_withheld_reason"] == "sdo_route_unsupported"
    assert result["provider"] is None
    assert result["model"] is None


class _RecordingDB:
    def __init__(self, observation=None):
        self.calls = []
        self.observation = observation or {}

    def preflight_orch_task_observation(self, *args, **kwargs):
        self.calls.append("preflight")
        return True

    def claim_orch_sdo_receipt(self, *args, **kwargs):
        self.calls.append("claim")
        return True

    def begin_orch_task_observation(self, *args, **kwargs):
        self.calls.append("begin")
        return True

    def finish_orch_task_observation(self, *args, **kwargs):
        self.calls.append("finish")
        return True

    def mark_orch_task_finalization_unavailable(self, *args, **kwargs):
        self.calls.append("terminal")
        return True

    def read_orch_task_observations(self, *args, **kwargs):
        return [{"observation": self.observation}]


def test_server_reserves_before_injected_decision_and_binds_route(monkeypatch) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {"session_key": "session-current", "profile_home": None}
    context = {
        "operation_id": "operation-current",
        "decision_binding": {"logical_session_id": "logical-current"},
    }
    call_order = []

    def provider(received_context):
        call_order.append("provider")
        assert received_context is context
        return _decision("operation-current")

    result = server._consume_orch_sdo_submit(
        {},
        session,
        context,
        decision_callable=provider,
    )
    assert result["claim_status"] == "admitted"
    assert db.calls == ["preflight", "claim", "begin"]
    assert call_order == ["provider"]
    assert session["model_override"]["provider"] == "openai-codex"
    assert session["model_override"]["model"] == "gpt-5.6-luna"


def test_server_real_fp1_state_claims_and_begins_before_agent_build(
    monkeypatch, tmp_path
) -> None:
    db = hermes_state.SessionDB(db_path=tmp_path / "sdo-state.db")
    db.create_session("session-real", source="cli")

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    context = _orch_context("operation-real")
    session = {"session_key": "session-real", "profile_home": None}
    result = server._consume_orch_sdo_submit(
        {},
        session,
        context,
        decision_callable=lambda _context: _decision("operation-real"),
    )
    try:
        assert result["claim_status"] == "admitted"
        rows = db.read_orch_task_observations("session-real", limit=1)
        assert rows[0]["state"] == "running"
        assert rows[0]["observation"].get("first_delta", {}).get("present") is False
    finally:
        db.close()


def test_initialization_failure_persists_category_and_drops_raw_error(monkeypatch) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {
        "agent_error": "synthetic provider unavailable detail",
        "_orch_operation_id": "operation-init",
    }
    assert (
        server._orch_record_initialization_failure(session)
        == "model_provider_unavailable"
    )
    assert session["agent_error"] is None
    assert db.calls == ["terminal"]


@pytest.mark.parametrize(
    ("status", "present", "expected"),
    [
        ("not_observed", False, False),
        ("observed", False, False),
        ("not_observed", True, False),
        ("observed", True, True),
    ],
)
def test_status_first_delta_requires_observed_and_present(
    monkeypatch, status, present, expected
) -> None:
    db = _RecordingDB(
        {
            "first_delta": {"status": status, "present": present},
            "result": {
                "status": "failed",
                "terminal_category": "agent_initialization_failed",
                "exception": "synthetic raw exception must not project",
            },
        }
    )

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    admitted = sdo_adapter.consume_sdo_decision(
        _decision("operation-status"),
        context={"operation_id": "operation-status"},
        now=1000.0,
    )
    session = {
        "session_key": "session-status",
        "profile_home": None,
        "_orch_sdo_decision": sdo_adapter.public_decision_projection(admitted),
    }
    projection = server._orch_sdo_status_projection(session)
    assert projection["first_delta_observed"] is expected
    assert projection["terminal_category"] == "agent_initialization_failed"
    assert projection["terminal_result_consumed"] is True
    assert "exception" not in projection


def test_default_callable_is_typed_unavailable() -> None:
    assert server._orch_sdo_unavailable({}) is None
