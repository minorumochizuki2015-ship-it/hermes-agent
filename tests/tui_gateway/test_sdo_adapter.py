"""Focused FP-2 pure-consumer and projection contract."""

from contextlib import contextmanager
from types import SimpleNamespace
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


def _binding_for_context(operation_id: str, context: dict) -> dict[str, str]:
    value = _binding(operation_id)
    decision_binding = context.get("decision_binding") or {}
    for key in ("project_id", "logical_session_id"):
        if key in decision_binding:
            value[key] = decision_binding[key]
    return value


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
        "action_changed": False,
        "replan_required": False,
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
    def __init__(self, observation=None, *, reserve=True, operation_id=None):
        self.calls = []
        self.observation = observation or {}
        self.reserve = reserve
        self.operation_id = operation_id

    def preflight_orch_task_observation(self, *args, **kwargs):
        self.calls.append("preflight")
        return self.reserve

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

    def record_orch_task_runtime_identity(self, *args, **kwargs):
        self.calls.append("runtime_identity")
        return True

    def read_orch_task_observations(self, *args, **kwargs):
        if self.operation_id is not None:
            return [
                {
                    "operation_id": self.operation_id,
                    "session_id": args[0] if args else "",
                    "profile_name": kwargs.get("profile_name", ""),
                    "observation": self.observation,
                }
            ]
        return [{"observation": self.observation}]


class _NoTruthCallable:
    def __init__(self):
        self.called = False

    def __bool__(self):
        raise AssertionError("injected callable was truth-tested before reservation")

    def __call__(self, _context):
        self.called = True
        raise AssertionError("provider must not run when reservation is denied")


def test_reservation_precedes_injected_callable_truthiness(monkeypatch) -> None:
    db = _RecordingDB(reserve=False)

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {"session_key": "session-reservation", "profile_home": None}
    provider = _NoTruthCallable()
    result = server._consume_orch_sdo_submit(
        {},
        session,
        {"operation_id": "operation-reservation"},
        decision_callable=provider,
    )
    assert result["claim_withheld_reason"] == "sdo_reservation_unavailable"
    assert db.calls == ["preflight"]
    assert provider.called is False


def test_invalid_injected_callable_is_withheld_after_reservation(monkeypatch) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    result = server._consume_orch_sdo_submit(
        {},
        {"session_key": "session-callable", "profile_home": None},
        {"operation_id": "operation-callable"},
        decision_callable=object(),
    )
    assert result["claim_withheld_reason"] == "sdo_callable_invalid"
    assert db.calls == ["preflight", "begin", "finish"]


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"unexpected": "synthetic"}, "sdo_decision_malformed"),
        ({"binding": {**_binding("operation-closed"), "project_id": "other-project"}}, "sdo_binding_mismatch"),
        ({"binding": {**_binding("operation-closed"), "logical_session_id": "other-owner"}}, "sdo_binding_mismatch"),
        ({"binding": {**_binding("operation-closed"), "transition": "other-transition"}}, "sdo_binding_mismatch"),
        (
            {
                "decision": "REPLAN_NOW",
                "dispatch_mode": "replan_local",
                "replan_required": False,
            },
            "sdo_outcome_malformed",
        ),
        ({"action_changed": True}, "sdo_outcome_malformed"),
    ],
)
def test_decision_contract_is_closed_before_receipt_claim(overrides, expected_reason) -> None:
    value = _decision("operation-closed", **overrides)
    context = {
        "operation_id": "operation-closed",
        "decision_binding": {
            "project_id": "project-test",
            "logical_session_id": "logical-test",
        },
    }
    result = sdo_adapter.consume_sdo_decision(
        value,
        context=context,
        now=1000.0,
    )
    assert result["claim_status"] == "withheld"
    assert result["claim_withheld_reason"] == expected_reason
    assert result["receipt_digest"] is None


def test_decision_contract_normalizes_action_and_replan_outcome() -> None:
    value = _decision(
        "operation-replan",
        selected_action_id="action-next",
        base_selected_action_id="action-base",
        decision="REPLAN_NOW",
        dispatch_mode="replan_local",
        action_changed=True,
        replan_required=True,
    )
    result = sdo_adapter.consume_sdo_decision(
        value,
        context={"operation_id": "operation-replan"},
        now=1000.0,
    )
    assert result["claim_status"] == "admitted"
    assert result["outcome"]["action_changed"] is True
    assert result["outcome"]["replan_required"] is True


class _RuntimeAgent(SimpleNamespace):
    pass


@pytest.mark.parametrize(
    ("provider", "model", "effort", "tier"),
    [
        ("terra", "gpt-5.6-luna", "max", "priority"),
        ("openai-codex", "gpt-5.6-sol", "max", "priority"),
        ("openai-codex", "gpt-5.6-luna", "high", "priority"),
        ("openai-codex", "gpt-5.6-luna", "max", "standard"),
    ],
)
def test_ready_agent_identity_is_verified_per_operation(
    monkeypatch, provider, model, effort, tier
) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {
        "_orch_operational": True,
        "_orch_model_route": {
            "provider": "openai-codex",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "service_tier_preference": "fast",
        },
        "_orch_operation_id": "operation-ready",
    }
    agent = _RuntimeAgent(
        provider=provider,
        model=model,
        reasoning_config={"effort": effort},
        service_tier=tier,
    )
    with pytest.raises(RuntimeError):
        server._orch_prepare_orch_agent_for_turn(session, agent)
    assert db.calls == []
    assert session.get("_orch_runtime_identity_verified") is not True


def test_correct_reused_agent_identity_is_recorded_before_call(monkeypatch) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {
        "_orch_operational": True,
        "_orch_model_route": {
            "provider": "openai-codex",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "service_tier_preference": "fast",
        },
        "_orch_operation_id": "operation-reused",
    }
    agent = _RuntimeAgent(
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_config={"effort": "max"},
        service_tier="priority",
    )
    assert server._orch_prepare_orch_agent_for_turn(session, agent) is True
    assert db.calls == ["runtime_identity"]
    assert session["_orch_runtime_identity_verified"] is True


def test_status_separates_selected_route_from_unobserved_live_identity(monkeypatch) -> None:
    db = _RecordingDB({"result": {"status": "running"}})

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    admitted = sdo_adapter.consume_sdo_decision(
        _decision("operation-live-unknown"),
        context={"operation_id": "operation-live-unknown"},
        now=1000.0,
    )
    session = {
        "session_key": "session-live-unknown",
        "profile_home": None,
        "_orch_operation_id": "operation-live-unknown",
        "_orch_sdo_decision": sdo_adapter.public_decision_projection(admitted),
    }
    projection = server._orch_sdo_status_projection(session)
    assert projection["model_route_consumed"] is True
    assert projection["live_model"] == "UNKNOWN"
    assert projection["live_effort"] == "UNKNOWN"
    assert projection["tier"] == "UNKNOWN"


def test_status_reads_exact_operation_and_owner_not_latest_row(monkeypatch) -> None:
    class _RowsDB(_RecordingDB):
        def read_orch_task_observations(self, *args, **kwargs):
            return [
                {
                    "operation_id": "operation-other",
                    "session_id": "session-exact",
                    "profile_name": "",
                    "observation": {"result": {"status": "failed"}},
                },
                {
                    "operation_id": "operation-exact",
                    "session_id": "session-exact",
                    "profile_name": "",
                    "observation": {"result": {"status": "complete"}},
                },
            ]

    db = _RowsDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    projection = server._orch_sdo_status_projection(
        {
            "session_key": "session-exact",
            "profile_home": None,
            "_orch_status_operation_id": "operation-exact",
        }
    )
    assert projection["terminal_result_status"] == "complete"


def test_turn_binding_rejects_stale_generation_and_scrubs_route_state() -> None:
    session = {
        "session_key": "session-turn",
        "profile_home": None,
        "_orch_operational": True,
        "_orch_operation_id": "operation-turn",
        "_orch_model_route": {"model": "gpt-5.6-luna"},
        "_orch_turn_generation": 0,
        "_orch_sdo_decision": {"claim_status": "admitted"},
    }
    first = server._orch_open_orch_turn(
        session,
        "gateway-turn",
        {"operation_id": "operation-turn", "decision_binding": {"logical_session_id": "logical-turn"}},
    )
    second = server._orch_open_orch_turn(
        session,
        "gateway-turn",
        {"operation_id": "operation-turn-2", "decision_binding": {"logical_session_id": "logical-turn"}},
    )
    assert server._orch_turn_token_matches(session, first) is False
    assert server._orch_turn_token_matches(session, second) is True
    server._orch_clear_orch_turn(session)
    assert "_orch_model_route" not in session
    assert "_orch_sdo_decision" not in session
    assert "model_override" not in session
    assert session.get("_orch_operational") is not True


def test_turn_binding_is_immutable_and_fences_captured_operation_swap(monkeypatch) -> None:
    class _CallbackDB(_RecordingDB):
        def __init__(self):
            super().__init__()
            self.marked = []
            self.finished = []

        def mark_orch_task_first_delta(self, operation_id, *args, **kwargs):
            self.marked.append(operation_id)
            return True

        def finish_orch_task_observation(self, operation_id, *args, **kwargs):
            self.finished.append(operation_id)
            return True

    db = _CallbackDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {
        "session_key": "persisted-session-owner",
        "profile_home": "/tmp/profile-owner",
    }
    context = {
        "operation_id": "operation-owner",
        "decision_binding": {"logical_session_id": "logical-owner"},
    }
    token = server._orch_open_orch_turn(session, "gateway-owner", context)

    assert not isinstance(token, dict)
    assert token.gateway_session_id == "gateway-owner"
    assert token.session_key == "persisted-session-owner"
    assert token.profile == "profile-owner"
    assert token.logical_session_id == "logical-owner"
    assert token.operation_id == "operation-owner"
    with pytest.raises((AttributeError, TypeError)):
        token.operation_id = "operation-swapped"

    server._orch_mark_first_delta(session, token)
    server._orch_finish_task_observation(session, token, "complete")
    assert db.marked == ["operation-owner"]
    assert db.finished == ["operation-owner"]

    session["_orch_operation_id"] = "operation-swapped"
    server._orch_mark_first_delta(session, token)
    server._orch_finish_task_observation(session, token, "complete")
    assert db.marked == ["operation-owner"]
    assert db.finished == ["operation-owner"]

    server._orch_open_orch_turn(
        session,
        "gateway-owner",
        {
            "operation_id": "operation-next",
            "decision_binding": {"logical_session_id": "logical-other"},
        },
    )
    server._orch_mark_first_delta(session, token)
    server._orch_finish_task_observation(session, token, "complete")
    assert db.marked == ["operation-owner"]
    assert db.finished == ["operation-owner"]


def test_admitted_route_snapshot_precedes_apply_and_restores_exact_override(monkeypatch) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    original = {
        "model_override": {"provider": "ordinary-provider", "model": "ordinary-model"},
        "create_reasoning_override": {"enabled": True, "effort": "ordinary-effort"},
        "create_service_tier_override": "ordinary-tier",
    }
    session = {
        "session_key": "session-route-restore",
        "profile_home": None,
        **original,
    }
    context = {"operation_id": "operation-route-restore"}
    result = server._consume_orch_sdo_submit(
        {},
        session,
        context,
        decision_callable=lambda _context: _decision("operation-route-restore"),
    )
    assert result["claim_status"] == "admitted"
    assert session["model_override"]["model"] == "gpt-5.6-luna"

    server._orch_open_orch_turn(session, "gateway-route", context)
    server._orch_clear_orch_turn(session)
    assert session["model_override"] == original["model_override"]
    assert session["create_reasoning_override"] == original["create_reasoning_override"]
    assert session["create_service_tier_override"] == original["create_service_tier_override"]


def test_withheld_route_clear_preserves_ordinary_overrides(monkeypatch) -> None:
    db = _RecordingDB(reserve=False)

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    original = {
        "model_override": {"provider": "ordinary-provider", "model": "ordinary-model"},
        "create_reasoning_override": {"enabled": True, "effort": "ordinary-effort"},
        "create_service_tier_override": "ordinary-tier",
    }
    session = {
        "session_key": "session-route-withheld",
        "profile_home": None,
        **original,
    }
    result = server._consume_orch_sdo_submit(
        {},
        session,
        {"operation_id": "operation-route-withheld"},
        decision_callable=lambda _context: _decision("operation-route-withheld"),
    )
    assert result["claim_status"] == "withheld"
    server._orch_clear_orch_turn(session)
    assert session["model_override"] == original["model_override"]
    assert session["create_reasoning_override"] == original["create_reasoning_override"]
    assert session["create_service_tier_override"] == original["create_service_tier_override"]


def test_status_keeps_profile_db_handle_inside_context(monkeypatch) -> None:
    class _LifetimeDB(_RecordingDB):
        def __init__(self):
            super().__init__()
            self.active = False
            self.read_inside_context = False

        def read_orch_task_observations(self, *args, **kwargs):
            if not self.active:
                raise AssertionError("profile DB used after context exit")
            self.read_inside_context = True
            return [
                {
                    "operation_id": "operation-lifetime",
                    "session_id": "session-lifetime",
                    "profile_name": "",
                    "observation": {"result": {"status": "complete"}},
                }
            ]

    db = _LifetimeDB()

    @contextmanager
    def db_context(_session):
        db.active = True
        try:
            yield db
        finally:
            db.active = False

    monkeypatch.setattr(server, "_session_db", db_context)
    projection = server._orch_sdo_status_projection(
        {
            "session_key": "session-lifetime",
            "profile_home": None,
            "_orch_status_operation_id": "operation-lifetime",
        }
    )
    assert db.read_inside_context is True
    assert projection["terminal_result_status"] == "complete"


def test_operational_inflight_failure_is_fixed_and_value_free() -> None:
    session = {
        "inflight_turn": {"assistant": "", "user": "synthetic"},
        "_orch_operational": True,
    }
    server._fail_inflight_turn(session, "https://private.invalid/provider?token=synthetic")
    assert session["inflight_turn"]["error"] == "agent operation failed"


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
        assert received_context == context
        return _decision(
            "operation-current",
            binding=_binding_for_context("operation-current", received_context),
        )

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
        decision_callable=lambda value: _decision(
            "operation-real", binding=_binding_for_context("operation-real", value)
        ),
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
        },
        operation_id="operation-status",
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
        "_orch_operation_id": "operation-status",
        "_orch_sdo_decision": sdo_adapter.public_decision_projection(admitted),
    }
    projection = server._orch_sdo_status_projection(session)
    assert projection["first_delta_observed"] is expected
    assert projection["terminal_category"] == "agent_initialization_failed"
    assert projection["terminal_result_consumed"] is True
    assert "exception" not in projection


def test_default_callable_is_typed_unavailable() -> None:
    assert server._orch_sdo_unavailable({}) is None
