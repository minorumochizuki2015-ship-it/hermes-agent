"""Focused FP-2 pure-consumer and projection contract."""

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import threading
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


def _natural_producer_result(
    context: dict,
    *,
    receipt_overrides: dict | None = None,
    result_overrides: dict | None = None,
) -> dict:
    expires_at = datetime.fromtimestamp(
        time.time() + 120.0, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")
    route = {
        "provider": sdo_adapter.NATURAL_PROVIDER,
        "model": sdo_adapter.NATURAL_MODEL,
        "reasoning_effort": sdo_adapter.NATURAL_EFFORT,
        "selection_reason": sdo_adapter.NATURAL_SELECTION_REASON,
        "runtime_identity_verified": False,
        "fast_mode": {
            "selected": True,
            "service_tier_preference": sdo_adapter.NATURAL_TIER,
            "claim_withheld": True,
            "runtime_verified": False,
            "selection_reason": "luna_max_precision_latency_priority",
        },
    }
    selected_action = "natural-safe-local-cause-repair"
    receipt = {
        "schema_version": "sdo_decision_receipt.v1",
        "project_id": context["decision_binding"]["project_id"],
        "repo_facts": {
            "head_ref": context["decision_binding"]["runtime_revision"],
            "goal_ref": context["goal"],
            "phase_ref": context["task_declaration"]["task_class"],
            "transition": "return_decide_dispatch",
        },
        "model_route": route,
        "receipt_id": "c" * 64,
        "receipt_digest": "c" * 64,
        "receipt_consumed": False,
        "consumed_by": "",
        "expiry": {
            "claim_ttl_seconds": 300,
            "expires_at": expires_at,
            "scope": "current_transition",
        },
        "protected_transition": {
            "requested": False,
            "allowed": False,
            "execution_authorized": False,
            "reason": "PROTECTED_TRANSITION_NOT_REQUESTED",
        },
        "receipt_expiry_is_authority": False,
        "support_work_progress_credit": 0,
        "selected_action_id": selected_action,
        "base_selected_action_id": selected_action,
        "decision": "CONTINUE_LOCAL",
        "capability_delta": {"action_changed": False},
        "safe_local_continuation": True,
    }
    receipt.update(receipt_overrides or {})
    _reseal_natural_result_receipt({"sdo_decision_receipt": receipt})
    result = {
        "status": "PASS_WHOLE_GOAL_CONTROL_SUPPORT_ONLY",
        "support_work_progress_credit": 0,
        "decision": receipt["decision"],
        "selected_action_id": receipt["selected_action_id"],
        "authority_transition": {
            "requested": False,
            "allowed": False,
            "execution_authorized": False,
        },
        "model_routing": {
            key: route[key]
            for key in (
                "provider",
                "model",
                "reasoning_effort",
                "selection_reason",
                "runtime_identity_verified",
            )
        },
        "sdo_decision_receipt": receipt,
    }
    result.update(result_overrides or {})
    return result


def _reseal_natural_result_receipt(result: dict) -> None:
    receipt = result["sdo_decision_receipt"]
    receipt_content = {
        key: value
        for key, value in receipt.items()
        if key not in {
            "receipt_id",
            "receipt_digest",
            "receipt_consumed",
            "consumed_by",
        }
    }
    receipt_digest = hashlib.sha256(
        json.dumps(receipt_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt["receipt_id"] = receipt_digest
    receipt["receipt_digest"] = receipt_digest


def test_current_producer_projection_is_compact_and_never_grants_authority() -> None:
    context = _orch_context("operation-natural-projection")
    source_identity = {
        "head": context["decision_binding"]["runtime_revision"],
        "repo_id": "opaque:sha256:" + "d" * 64,
        "worktree_id": "opaque:sha256:" + "e" * 64,
    }

    projected = sdo_adapter.project_natural_producer_result(
        _natural_producer_result(context),
        context=context,
        source_identity=source_identity,
    )

    assert projected is not None
    assert set(projected) == sdo_adapter.NATURAL_DECISION_FIELDS
    assert projected["binding"] == {
        "project_id": context["decision_binding"]["project_id"],
        "repo_id": source_identity["repo_id"],
        "worktree_id": source_identity["worktree_id"],
        "goal_ref": context["goal"],
        "request_ref": context["decision_binding"]["decision_id"],
        "transition": sdo_adapter.NATURAL_TRANSITION,
        "logical_session_id": context["decision_binding"]["logical_session_id"],
        "operation_id": context["operation_id"],
    }
    assert projected["provider"] == "openai-codex"
    assert projected["model"] == "gpt-5.6-luna"
    assert projected["effort"] == "max"
    assert projected["tier"] == "fast"
    assert "execution_authorized" not in projected
    assert "protected_transition" not in projected
    admitted = sdo_adapter.consume_sdo_decision(projected, context=context)
    assert admitted["claim_status"] == "admitted"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_project",
        "wrong_head",
        "wrong_goal",
        "wrong_phase",
        "stale_context",
        "stale_receipt",
        "receipt_digest_drift",
        "receipt_already_consumed",
        "protected_requested",
        "protected_allowed",
        "execution_authorized",
        "top_authority_grant",
        "wrong_model",
        "wrong_tier",
        "top_decision_mismatch",
    ],
)
def test_current_producer_projection_rejects_unbound_or_authorizing_results(
    mutation,
) -> None:
    context = _orch_context("operation-natural-reject")
    source_identity = {
        "head": context["decision_binding"]["runtime_revision"],
        "repo_id": "opaque:sha256:" + "d" * 64,
        "worktree_id": "opaque:sha256:" + "e" * 64,
    }
    result = _natural_producer_result(context)
    receipt = result["sdo_decision_receipt"]

    if mutation == "wrong_project":
        receipt["project_id"] = "other-project"
    elif mutation == "wrong_head":
        receipt["repo_facts"]["head_ref"] = "2" * 40
    elif mutation == "wrong_goal":
        receipt["repo_facts"]["goal_ref"] = "other-goal"
    elif mutation == "wrong_phase":
        receipt["repo_facts"]["phase_ref"] = "other-phase"
    elif mutation == "stale_context":
        context["expires_at"] = time.time() - 1.0
    elif mutation == "stale_receipt":
        receipt["expiry"]["expires_at"] = "2000-01-01T00:00:00Z"
    elif mutation == "receipt_digest_drift":
        receipt["decision"] = "REPLAN_NOW"
    elif mutation == "receipt_already_consumed":
        receipt["receipt_consumed"] = True
        receipt["consumed_by"] = "other-consumer"
    elif mutation == "protected_requested":
        receipt["protected_transition"]["requested"] = True
    elif mutation == "protected_allowed":
        receipt["protected_transition"]["allowed"] = True
    elif mutation == "execution_authorized":
        receipt["protected_transition"]["execution_authorized"] = True
    elif mutation == "top_authority_grant":
        result["authority_transition"] = {
            "allowed": True,
            "execution_authorized": True,
        }
    elif mutation == "wrong_model":
        receipt["model_route"]["model"] = "gpt-5.6-sol"
    elif mutation == "wrong_tier":
        receipt["model_route"]["fast_mode"]["service_tier_preference"] = "standard"
    elif mutation == "top_decision_mismatch":
        result["decision"] = "REPLAN_NOW"

    if mutation != "receipt_digest_drift":
        _reseal_natural_result_receipt(result)

    assert (
        sdo_adapter.project_natural_producer_result(
            result,
            context=context,
            source_identity=source_identity,
        )
        is None
    )


def test_default_natural_producer_uses_pinned_source_and_private_state(
    monkeypatch, tmp_path
) -> None:
    context = _orch_context("operation-natural-default")
    repo_root = Path(server.__file__).resolve().parents[1]
    producer = repo_root / "runtime" / "sdo-producer-test.py"
    common_git_dir = tmp_path / "common.git"
    common_git_dir.mkdir(mode=0o700)
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    calls = []

    monkeypatch.setattr(
        server,
        "_orch_sdo_producer_source",
        lambda: (repo_root, producer),
        raising=False,
    )
    monkeypatch.setattr(server, "get_hermes_home", lambda: hermes_home)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs.get("shell") is False
        if command[0] == "/usr/bin/git":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    f"{repo_root}\n"
                    f"{context['decision_binding']['runtime_revision']}\n"
                    f"{common_git_dir}\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_natural_producer_result(context)),
            stderr="",
        )

    monkeypatch.setattr(server.subprocess, "run", run)

    decision = server._orch_current_sdo_decision(context)

    assert decision is not None
    assert decision["provider"] == "openai-codex"
    assert decision["model"] == "gpt-5.6-luna"
    assert decision["effort"] == "max"
    assert decision["tier"] == "fast"
    producer_command, producer_kwargs = calls[1]
    assert producer_command[0] == server.sys.executable
    assert producer_command[1] == str(producer)
    assert producer_command[2:] == [
        "--natural-project-id",
        context["decision_binding"]["project_id"],
        "--natural-goal-ref",
        context["goal"],
        "--natural-phase-ref",
        context["task_declaration"]["task_class"],
        "--natural-operation-id",
        context["operation_id"],
        "--natural-task-root",
        str(repo_root),
        "--output-dir",
        producer_command[13],
        "--cmd-state-file",
        producer_command[15],
        "--json",
    ]
    assert Path(producer_command[13]).parent.parent.is_relative_to(
        hermes_home / "state" / "orch-sdo-producer"
    )
    assert Path(producer_command[15]).is_relative_to(
        hermes_home / "state" / "orch-sdo-producer"
    )
    state_root = Path(producer_command[15]).parent
    assert not state_root.is_symlink()
    assert state_root.stat().st_mode & 0o077 == 0
    assert producer_kwargs["cwd"] == repo_root
    assert producer_kwargs["timeout"] > 0
    assert producer_kwargs["stderr"] is subprocess.DEVNULL
    assert producer_kwargs["env"]["HERMES_HOME"].startswith(str(hermes_home))
    assert "sdo_decision_receipt" not in " ".join(producer_command)
    assert "execution_authorized" not in " ".join(producer_command)


@pytest.mark.parametrize("context_failure", ["goal", "operation", "target", "stale"])
def test_invalid_authenticated_context_never_reaches_source_or_producer(
    monkeypatch, context_failure
) -> None:
    context = _orch_context("operation-context-reject")
    if context_failure == "stale":
        context["expires_at"] = time.time() - 1.0
    else:
        context[context_failure] = "other-" + context_failure

    monkeypatch.setattr(
        server,
        "_orch_sdo_producer_source",
        lambda: pytest.fail("invalid context reached producer source"),
    )

    assert server._orch_current_sdo_decision(context) is None


def test_runtime_head_mismatch_never_reaches_private_state_or_producer(
    monkeypatch,
) -> None:
    context = _orch_context("operation-head-reject")
    repo_root = Path(server.__file__).resolve().parents[1]
    monkeypatch.setattr(
        server,
        "_orch_sdo_producer_source",
        lambda: (repo_root, repo_root / "synthetic-producer.py"),
    )
    monkeypatch.setattr(
        server,
        "_orch_sdo_git_identity",
        lambda _root: {
            "head": "2" * 40,
            "repo_id": "opaque:sha256:" + "d" * 64,
            "worktree_id": "opaque:sha256:" + "e" * 64,
        },
    )
    monkeypatch.setattr(
        server,
        "_orch_sdo_private_state_root",
        lambda _project: pytest.fail("wrong HEAD reached private state"),
    )

    assert server._orch_current_sdo_decision(context) is None


@pytest.mark.parametrize("binding_failure", ["wrong_root", "path_escape"])
def test_distribution_source_resolution_rejects_wrong_root_or_escape(
    monkeypatch, tmp_path, binding_failure
) -> None:
    from scripts import orch_next_hermes_distribution as distribution

    repo_root = Path(server.__file__).resolve().parents[1]
    if binding_failure == "wrong_root":
        monkeypatch.setattr(distribution, "_repo_root", lambda: tmp_path)
    else:
        monkeypatch.setattr(distribution, "_repo_root", lambda: repo_root)
        monkeypatch.setattr(
            distribution,
            "_sdo_producer_binding",
            lambda _root: {
                "root": "../outside",
                "consumer_path": "producer.py",
            },
        )

    with pytest.raises(RuntimeError):
        server._orch_sdo_producer_source()


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
    "source_failure",
    ["missing", "drift", "symlink", "path_escape", "wrong_worktree"],
)
def test_default_source_failure_is_safe_before_receipt_claim(
    monkeypatch, source_failure
) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    def unavailable_source():
        raise RuntimeError("sdo producer " + source_failure)

    monkeypatch.setattr(server, "_session_db", db_context)
    monkeypatch.setattr(server, "_orch_sdo_producer_source", unavailable_source)
    monkeypatch.setattr(
        server, "_orch_sdo_decision_callable", server._orch_current_sdo_decision
    )
    session = {"session_key": "session-source-" + source_failure, "profile_home": None}

    result = server._consume_orch_sdo_submit(
        {},
        session,
        _orch_context("operation-source-" + source_failure),
    )

    assert result["claim_status"] == "withheld"
    assert result["safe_local_continuation"] is True
    assert db.calls == ["preflight", "begin", "finish"]
    assert "_orch_model_route" not in session


@pytest.mark.parametrize("producer_failure", ["timeout", "non_json", "nonzero"])
def test_default_process_failure_is_safe_before_receipt_claim(
    monkeypatch, tmp_path, producer_failure
) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    context = _orch_context("operation-process-" + producer_failure)
    repo_root = Path(server.__file__).resolve().parents[1]
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    source_identity = {
        "head": context["decision_binding"]["runtime_revision"],
        "repo_id": "opaque:sha256:" + "d" * 64,
        "worktree_id": "opaque:sha256:" + "e" * 64,
    }

    monkeypatch.setattr(server, "_session_db", db_context)
    monkeypatch.setattr(
        server,
        "_orch_sdo_producer_source",
        lambda: (repo_root, repo_root / "synthetic-producer.py"),
    )
    monkeypatch.setattr(server, "_orch_sdo_git_identity", lambda _root: source_identity)
    monkeypatch.setattr(server, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(
        server, "_orch_sdo_decision_callable", server._orch_current_sdo_decision
    )

    def run(command, **kwargs):
        assert command[0] == server.sys.executable
        assert kwargs["shell"] is False
        if producer_failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if producer_failure == "non_json":
            return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")
        return subprocess.CompletedProcess(command, 17, stdout="{}", stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)
    session = {"session_key": "session-process-" + producer_failure, "profile_home": None}

    result = server._consume_orch_sdo_submit({}, session, context)

    assert result["claim_status"] == "withheld"
    assert result["safe_local_continuation"] is True
    assert db.calls == ["preflight", "begin", "finish"]
    assert "_orch_model_route" not in session


def test_default_path_ignores_all_caller_sdo_sidebands(monkeypatch) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    context = _orch_context("operation-sideband")
    received = []

    def current_provider(value):
        received.append(value)
        return _decision(
            context["operation_id"],
            binding=_binding_for_context(context["operation_id"], value),
        )

    monkeypatch.setattr(server, "_session_db", db_context)
    monkeypatch.setattr(server, "_orch_sdo_decision_callable", current_provider)
    session = {"session_key": "session-sideband", "profile_home": None}
    params = {
        "sdo_decision_receipt": {"execution_authorized": True},
        "sdo_source_binding": {"head": "attacker"},
        "sdo_candidate_action_ids": ["attacker-action"],
        "sdo_producer_input": {"project_id": "attacker-project"},
    }

    result = server._consume_orch_sdo_submit(params, session, context)

    assert result["claim_status"] == "admitted"
    assert received == [context]
    assert db.calls == ["preflight", "claim", "begin"]
    assert session["_orch_model_route"]["model"] == "gpt-5.6-luna"


def test_actual_prompt_submit_default_path_produces_claims_begins_then_builds(
    monkeypatch,
) -> None:
    order = []

    class OrderedDB(_RecordingDB):
        def preflight_orch_task_observation(self, *args, **kwargs):
            order.append("reserve")
            return True

        def claim_orch_sdo_receipt(self, *args, **kwargs):
            order.append("claim")
            return True

        def begin_orch_task_observation(self, *args, **kwargs):
            order.append("begin")
            return True

    db = OrderedDB()

    @contextmanager
    def db_context(_session):
        yield db

    class NoopThread:
        def __init__(self, target, daemon=True):
            self.target = target
            self.daemon = daemon

        def start(self):
            order.append("deferred-thread")

        def is_alive(self):
            return True

    context = _orch_context("operation-actual-default")
    session = {
        "session_key": "session-actual-default",
        "profile_home": None,
        "history": [],
        "history_lock": threading.RLock(),
        "running": False,
        "agent_ready": threading.Event(),
        "agent": None,
        "cols": 80,
    }

    def current_provider(value):
        order.append("produce-validate")
        return _decision(
            context["operation_id"],
            binding=_binding_for_context(context["operation_id"], value),
        )

    monkeypatch.setattr(
        server, "_validate_orch_submit_context", lambda _params, _rid: (context, None)
    )
    monkeypatch.setattr(server, "_sess_nowait", lambda _params, _rid: (session, None))
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_args: None)
    monkeypatch.setattr(server, "_voice_mode_enabled", lambda: False)
    monkeypatch.setattr(server, "current_transport", lambda: None)
    monkeypatch.setattr(
        server,
        "_load_dashboard_process_isolation_config",
        lambda: {"turn_isolation": False},
    )
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *_args: False)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _session: None)
    monkeypatch.setattr(server, "_session_db", db_context)
    monkeypatch.setattr(server, "_orch_sdo_decision_callable", current_provider)
    monkeypatch.setattr(
        server,
        "_start_agent_build",
        lambda *_args: order.append("lazy-build"),
    )
    monkeypatch.setattr(server.threading, "Thread", NoopThread)

    response = server._methods["prompt.submit"](
        "rid-actual-default",
        {
            "session_id": "gateway-actual-default",
            "text": "自然な依頼",
            "operational_class": "orch",
            "operational_context": context,
            "sdo_decision_receipt": {"execution_authorized": True},
            "sdo_source_binding": {"head": "caller-sideband"},
            "sdo_candidate_action_ids": ["caller-sideband"],
            "sdo_producer_input": {"project_id": "caller-sideband"},
        },
    )

    assert response["result"]["status"] == "streaming"
    assert order == [
        "reserve",
        "produce-validate",
        "claim",
        "begin",
        "lazy-build",
        "deferred-thread",
    ]
    assert session["_orch_model_route"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "service_tier_preference": "fast",
    }


def test_exact_receipt_digest_is_single_use_across_db_reopen(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "sdo-reopen.db"
    current_db = [hermes_state.SessionDB(db_path=database_path)]
    current_db[0].create_session("session-reopen-one", source="cli")

    @contextmanager
    def db_context(_session):
        yield current_db[0]

    monkeypatch.setattr(server, "_session_db", db_context)
    first_context = _orch_context("operation-reopen-one")
    first = server._consume_orch_sdo_submit(
        {},
        {"session_key": "session-reopen-one", "profile_home": None},
        first_context,
        decision_callable=lambda value: _decision(
            first_context["operation_id"],
            binding=_binding_for_context(first_context["operation_id"], value),
        ),
    )
    assert first["claim_status"] == "admitted"
    assert current_db[0].finish_orch_task_observation(
        first_context["operation_id"], result_status="complete"
    ) is True
    current_db[0].close()

    current_db[0] = hermes_state.SessionDB(db_path=database_path)
    current_db[0].create_session("session-reopen-two", source="cli")
    second_context = _orch_context("operation-reopen-two")
    try:
        second_session = {"session_key": "session-reopen-two", "profile_home": None}
        second = server._consume_orch_sdo_submit(
            {},
            second_session,
            second_context,
            decision_callable=lambda value: _decision(
                second_context["operation_id"],
                binding=_binding_for_context(second_context["operation_id"], value),
            ),
        )
        assert second["claim_status"] == "withheld"
        assert second["claim_withheld_reason"] == "sdo_claim_unavailable"
        assert "_orch_model_route" not in second_session
    finally:
        current_db[0].close()


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
        "session_key": "session-ready",
        "profile_home": None,
        "_orch_operational": True,
    }
    token = server._orch_open_orch_turn(
        session,
        "gateway-ready",
        {
            "operation_id": "operation-ready",
            "decision_binding": {"logical_session_id": "logical-ready"},
        },
    )
    session["_orch_model_route"] = {
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "service_tier_preference": "fast",
    }
    agent = _RuntimeAgent(
        provider=provider,
        model=model,
        reasoning_config={"effort": effort},
        service_tier=tier,
    )
    with pytest.raises(RuntimeError):
        server._orch_prepare_orch_agent_for_turn(session, agent, token)
    assert db.calls == []
    assert session.get("_orch_runtime_identity_verified") is not True


def test_correct_reused_agent_identity_is_recorded_before_call(monkeypatch) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {
        "session_key": "session-reused",
        "profile_home": None,
        "_orch_operational": True,
    }
    token = server._orch_open_orch_turn(
        session,
        "gateway-reused",
        {
            "operation_id": "operation-reused",
            "decision_binding": {"logical_session_id": "logical-reused"},
        },
    )
    session["_orch_model_route"] = {
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "service_tier_preference": "fast",
    }
    agent = _RuntimeAgent(
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_config={"effort": "max"},
        service_tier="priority",
    )
    assert server._orch_prepare_orch_agent_for_turn(session, agent, token) is True
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


@pytest.mark.parametrize("mutation", ["runtime_identity", "first_delta", "finish"])
def test_turn_mutation_serializes_context_swap_until_db_and_flags_finish(
    monkeypatch, mutation
) -> None:
    class _BlockingDB(_RecordingDB):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
            self.recorded = []

        def record_orch_task_runtime_identity(self, operation_id, *args, **kwargs):
            self.entered.set()
            self.release.wait(timeout=2.0)
            self.recorded.append(("runtime_identity", operation_id))
            return True

        def mark_orch_task_first_delta(self, operation_id, *args, **kwargs):
            self.entered.set()
            self.release.wait(timeout=2.0)
            self.recorded.append(("first_delta", operation_id))
            return True

        def finish_orch_task_observation(self, operation_id, *args, **kwargs):
            self.entered.set()
            self.release.wait(timeout=2.0)
            self.recorded.append(("finish", operation_id))
            return True

    db = _BlockingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {"session_key": "session-serialized", "profile_home": None}
    token = server._orch_open_orch_turn(
        session,
        "gateway-serialized",
        {
            "operation_id": "operation-serialized",
            "decision_binding": {"logical_session_id": "logical-serialized"},
        },
    )
    session["_orch_model_route"] = {
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "service_tier_preference": "fast",
    }
    agent = _RuntimeAgent(
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_config={"effort": "max"},
        service_tier="priority",
    )

    def mutate():
        if mutation == "runtime_identity":
            server._orch_prepare_orch_agent_for_turn(session, agent, token)
        elif mutation == "first_delta":
            server._orch_mark_first_delta(session, token)
        else:
            server._orch_finish_task_observation(session, token, "complete")

    worker = threading.Thread(target=mutate)
    worker.start()
    assert db.entered.wait(timeout=1.0)

    swapped = threading.Event()

    def swap_turn():
        server._orch_open_orch_turn(
            session,
            "gateway-next",
            {
                "operation_id": "operation-next",
                "decision_binding": {"logical_session_id": "logical-next"},
            },
        )
        swapped.set()

    swapper = threading.Thread(target=swap_turn)
    swapper.start()
    assert swapped.wait(timeout=0.05) is False

    db.release.set()
    worker.join(timeout=2.0)
    swapper.join(timeout=2.0)
    assert not worker.is_alive()
    assert not swapper.is_alive()
    assert swapped.is_set()
    assert db.recorded == [(mutation, "operation-serialized")]
    assert session.get("_orch_runtime_identity_verified") is not True
    assert session.get("_orch_first_delta_observed") is not True
    assert session.get("_orch_result_consumed") is not True


def test_db_acquisition_turn_swap_cannot_fence_current_callback(monkeypatch) -> None:
    class _SwapDB(_RecordingDB):
        def __init__(self):
            super().__init__()
            self.marked = []

        def mark_orch_task_first_delta(self, operation_id, *args, **kwargs):
            self.marked.append(operation_id)
            return True

    db = _SwapDB()
    session = {"session_key": "session-acquisition", "profile_home": None}
    token = server._orch_open_orch_turn(
        session,
        "gateway-acquisition",
        {
            "operation_id": "operation-acquisition",
            "decision_binding": {"logical_session_id": "logical-acquisition"},
        },
    )
    attempted_swaps = []

    @contextmanager
    def db_context(_session):
        attempted_swaps.append(
            server._orch_open_orch_turn(
                session,
                "gateway-raced",
                {
                    "operation_id": "operation-raced",
                    "decision_binding": {"logical_session_id": "logical-raced"},
                },
            )
        )
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    server._orch_mark_first_delta(session, token)

    assert attempted_swaps == [token]
    assert server._orch_turn_token_matches(session, token) is True
    assert db.marked == ["operation-acquisition"]
    assert session.get("_orch_first_delta_observed") is True


def test_pre_ready_cancellation_finishes_interrupted_and_restores_ordinary_route(
    monkeypatch,
) -> None:
    class _CancellationDB(_RecordingDB):
        def __init__(self):
            super().__init__()
            self.finished = []

        def finish_orch_task_observation(self, operation_id, *, result_status):
            self.finished.append((operation_id, result_status))
            return True

    db = _CancellationDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    ordinary = {
        "model_override": {"provider": "ordinary-provider", "model": "ordinary-model"},
        "create_reasoning_override": {"enabled": True, "effort": "ordinary-effort"},
        "create_service_tier_override": "ordinary-tier",
    }
    session = {"session_key": "session-cancel", "profile_home": None, **ordinary}
    context = {
        "operation_id": "operation-cancel",
        "decision_binding": {"logical_session_id": "logical-cancel"},
    }
    token = server._orch_open_orch_turn(session, "gateway-cancel", context)
    admitted = sdo_adapter.consume_sdo_decision(
        _decision("operation-cancel"),
        context={"operation_id": "operation-cancel"},
        now=1000.0,
    )
    sdo_adapter.apply_sdo_decision_to_session(session, admitted)
    session["running"] = False
    session["_turn_cancel_requested"] = True

    assert server._orch_cancel_before_agent_ready(session, token) is True
    assert db.finished == [("operation-cancel", "interrupted")]
    assert session.get("_orch_turn_binding") is None
    assert session.get("_orch_operation_id") is None
    assert session.get("_orch_operational") is None
    assert session["model_override"] == ordinary["model_override"]
    assert session["create_reasoning_override"] == ordinary["create_reasoning_override"]
    assert session["create_service_tier_override"] == ordinary["create_service_tier_override"]

    session["model_override"] = {
        "provider": "ordinary-next-provider",
        "model": "ordinary-next-model",
    }
    server._orch_clear_orch_turn(session)
    assert session["model_override"] == {
        "provider": "ordinary-next-provider",
        "model": "ordinary-next-model",
    }


def test_stale_deferred_initialization_failure_has_zero_mutation_or_event(
    monkeypatch,
) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    session = {
        "session_key": "session-init-stale",
        "profile_home": None,
        "agent_error": "synthetic-original-error",
    }
    stale_token = server._orch_open_orch_turn(
        session,
        "gateway-init-stale",
        {
            "operation_id": "operation-init-stale",
            "decision_binding": {"logical_session_id": "logical-init-stale"},
        },
    )
    server._orch_clear_orch_turn(session)
    current_token = server._orch_open_orch_turn(
        session,
        "gateway-init-current",
        {
            "operation_id": "operation-init-current",
            "decision_binding": {"logical_session_id": "logical-init-current"},
        },
    )
    session["_orch_model_route"] = {"provider": "synthetic-current-route"}
    before = {
        key: session.get(key)
        for key in (
            "agent_error",
            "_orch_turn_binding",
            "_orch_operation_id",
            "_orch_model_route",
            "_orch_operational",
        )
    }

    server._orch_record_initialization_failure(session, stale_token)

    assert db.calls == []
    assert {
        key: session.get(key)
        for key in before
    } == before
    assert session["_orch_turn_binding"] == current_token


def test_deferred_cancel_owner_swap_suppresses_stale_error_event(monkeypatch) -> None:
    session = {
        "session_key": "session-cancel-stale",
        "profile_home": None,
        "history": [],
        "history_lock": threading.RLock(),
        "running": False,
        "agent_ready": threading.Event(),
        "agent": None,
        "cols": 80,
    }
    stale_token = server._orch_open_orch_turn(
        session,
        "gateway-cancel-stale",
        {
            "operation_id": "operation-cancel-stale",
            "decision_binding": {"logical_session_id": "logical-cancel-stale"},
        },
    )
    current_context = {
        "operation_id": "operation-cancel-current",
        "decision_binding": {"logical_session_id": "logical-cancel-current"},
    }
    events = []
    cancel_calls = []

    def swap_before_finalization(active_session, captured_token):
        cancel_calls.append(captured_token)
        assert captured_token == stale_token
        server._orch_clear_orch_turn(active_session)
        current_token = server._orch_open_orch_turn(
            active_session,
            "gateway-cancel-current",
            current_context,
        )
        active_session["_orch_model_route"] = {"provider": "synthetic-current"}
        active_session["_orch_current_test_token"] = current_token
        return False

    class _ImmediateThread:
        def __init__(self, target, daemon=True):
            self._target = target
            self.daemon = daemon

        def start(self):
            self._target()

    monkeypatch.setattr(
        server,
        "_validate_orch_submit_context",
        lambda _params, _rid: ({"operation_id": stale_token.operation_id}, None),
    )
    monkeypatch.setattr(server, "_sess_nowait", lambda _params, _rid: (session, None))
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_args: None)
    monkeypatch.setattr(server, "_voice_mode_enabled", lambda: False)
    monkeypatch.setattr(
        server,
        "_load_dashboard_process_isolation_config",
        lambda: {"turn_isolation": False},
    )
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *_args: False)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _session: None)
    monkeypatch.setattr(
        server,
        "_consume_orch_sdo_submit",
        lambda *_args, **_kwargs: {"claim_status": "admitted"},
    )
    monkeypatch.setattr(
        server,
        "_start_agent_build",
        lambda _sid, active_session: active_session.__setitem__(
            "_turn_cancel_requested", True
        ),
    )
    monkeypatch.setattr(server, "_wait_agent_for_prompt", lambda *_args: None)
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *_args: pytest.fail("stale turn ran"))
    monkeypatch.setattr(server, "_emit", lambda *args: events.append(args))
    monkeypatch.setattr(
        server,
        "_orch_cancel_before_agent_ready",
        swap_before_finalization,
    )
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)

    result = server._methods["prompt.submit"](
        "rid-cancel-stale",
        {"session_id": "gateway-cancel-stale", "text": "synthetic prompt"},
    )

    assert result["result"]["status"] == "streaming"
    assert cancel_calls == [stale_token]
    assert events == []
    assert session["_orch_turn_binding"] == session["_orch_current_test_token"]
    assert session["_orch_operation_id"] == "operation-cancel-current"


def test_stale_route_error_terminalization_is_noop_and_current_is_once(
    monkeypatch,
) -> None:
    db = _RecordingDB()

    @contextmanager
    def db_context(_session):
        yield db

    monkeypatch.setattr(server, "_session_db", db_context)
    events = []
    monkeypatch.setattr(
        server,
        "_emit_terminal_turn_error",
        lambda *args: events.append(args),
    )
    session = {
        "session_key": "session-route-stale",
        "profile_home": None,
        "agent_error": "synthetic-route-error",
    }
    stale_token = server._orch_open_orch_turn(
        session,
        "gateway-route-stale",
        {
            "operation_id": "operation-route-stale",
            "decision_binding": {"logical_session_id": "logical-route-stale"},
        },
    )
    server._orch_clear_orch_turn(session)
    current_token = server._orch_open_orch_turn(
        session,
        "gateway-route-current",
        {
            "operation_id": "operation-route-current",
            "decision_binding": {"logical_session_id": "logical-route-current"},
        },
    )
    session["_orch_model_route"] = {"provider": "synthetic-current-route"}

    server._orch_terminalize_before_agent_call(
        "gateway-route-current", session, stale_token
    )

    assert db.calls == []
    assert events == []
    assert session["_orch_turn_binding"] == current_token
    assert session["_orch_operation_id"] == "operation-route-current"

    server._orch_terminalize_before_agent_call(
        "gateway-route-current", session, current_token
    )
    server._orch_terminalize_before_agent_call(
        "gateway-route-current", session, current_token
    )

    assert db.calls == ["terminal"]
    assert len(events) == 1
    assert session.get("_orch_turn_binding") is None


def _deferred_turn_state(session: dict) -> dict:
    return copy.deepcopy(
        {
            key: session.get(key)
            for key in (
                "running",
                "last_active",
                "_turn_cancel_requested",
                "history",
                "inflight_turn",
                "_orch_operational",
                "_orch_operation_id",
                "_orch_turn_binding",
                "_orch_gateway_session_id",
                "_orch_model_route",
                "_orch_status_operation_id",
                "_orch_initialization_category",
                "agent_error",
            )
        }
    )


def _install_deferred_prompt_hooks(
    monkeypatch,
    session: dict,
    context: dict,
    wait_for_agent,
    run_prompt,
    events: list,
    *,
    terminal_events: list | None = None,
):
    real_thread = threading.Thread

    class _InlineThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

        def is_alive(self):
            return True

    monkeypatch.setattr(
        server,
        "_validate_orch_submit_context",
        lambda _params, _rid: (context, None),
    )
    monkeypatch.setattr(server, "_sess_nowait", lambda _params, _rid: (session, None))
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_args: None)
    monkeypatch.setattr(server, "_voice_mode_enabled", lambda: False)
    monkeypatch.setattr(server, "current_transport", lambda: None)
    monkeypatch.setattr(
        server,
        "_load_dashboard_process_isolation_config",
        lambda: {
            "turn_isolation": False,
            "compute_host_heartbeat_secs": 15,
            "compute_host_respawn_max": 3,
        },
    )
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *_args: False)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _session: None)
    monkeypatch.setattr(
        server,
        "_consume_orch_sdo_submit",
        lambda *_args, **_kwargs: {"claim_status": "admitted"},
    )
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_wait_agent_for_prompt", wait_for_agent)
    monkeypatch.setattr(server, "_run_prompt_submit", run_prompt)
    monkeypatch.setattr(server, "_emit", lambda *args: events.append(args))
    if terminal_events is not None:
        monkeypatch.setattr(
            server,
            "_emit_terminal_turn_error",
            lambda *args: terminal_events.append(args),
        )
    monkeypatch.setattr(server.threading, "Thread", _InlineThread)
    return real_thread


def _deferred_session_and_token(operation_id: str, gateway_session_id: str):
    session = {
        "session_key": "session-deferred-" + operation_id,
        "profile_home": None,
        "history": [],
        "history_lock": threading.RLock(),
        "running": False,
        "agent_ready": threading.Event(),
        "agent": None,
        "cols": 80,
    }
    context = {
        "operation_id": operation_id,
        "decision_binding": {"logical_session_id": "logical-" + operation_id},
    }
    token = server._orch_open_orch_turn(session, gateway_session_id, context)
    return session, context, token


def test_deferred_success_stale_operational_owner_has_zero_dispatch_or_mutation(
    monkeypatch,
) -> None:
    session, context, stale_token = _deferred_session_and_token(
        "wait-success-stale", "gateway-wait-success"
    )
    wait_entered = threading.Event()
    release_wait = threading.Event()
    events = []
    dispatches = []

    def wait_for_agent(*_args):
        wait_entered.set()
        assert release_wait.wait(timeout=2.0)
        return None

    def run_prompt(*args):
        dispatches.append(args)

    real_thread = _install_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        run_prompt,
        events,
    )
    result_holder = []
    error_holder = []

    def submit():
        try:
            result_holder.append(
                server._methods["prompt.submit"](
                    "rid-wait-success",
                    {
                        "session_id": "gateway-wait-success",
                        "text": "synthetic wait-success",
                    },
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic assertion
            error_holder.append(exc)

    submit_thread = real_thread(target=submit)
    submit_thread.start()
    assert wait_entered.wait(timeout=1.0)

    server._orch_clear_orch_turn(session)
    successor_token = server._orch_open_orch_turn(
        session,
        "gateway-wait-success-next",
        {
            "operation_id": "wait-success-next",
            "decision_binding": {"logical_session_id": "logical-wait-success-next"},
        },
    )
    session["_orch_model_route"] = {"provider": "synthetic-success-next"}
    after_successor = _deferred_turn_state(session)
    release_wait.set()
    submit_thread.join(timeout=2.0)

    assert not submit_thread.is_alive()
    assert error_holder == []
    assert result_holder[0]["result"]["status"] == "streaming"
    assert dispatches == []
    assert events == []
    assert _deferred_turn_state(session) == after_successor
    assert session["_orch_turn_binding"] == successor_token
    assert stale_token != successor_token


def test_deferred_wait_error_stale_operational_owner_to_ordinary_is_noop(
    monkeypatch,
) -> None:
    session, context, stale_token = _deferred_session_and_token(
        "wait-error-stale", "gateway-wait-error"
    )
    wait_entered = threading.Event()
    release_wait = threading.Event()
    events = []
    terminal_events = []

    def wait_for_agent(*_args):
        wait_entered.set()
        assert release_wait.wait(timeout=2.0)
        return {"error": {"message": "synthetic wait failure"}}

    real_thread = _install_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        lambda *_args: pytest.fail("stale wait-error turn dispatched"),
        events,
        terminal_events=terminal_events,
    )
    result_holder = []
    error_holder = []

    def submit():
        try:
            result_holder.append(
                server._methods["prompt.submit"](
                    "rid-wait-error",
                    {
                        "session_id": "gateway-wait-error",
                        "text": "synthetic wait-error",
                    },
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic assertion
            error_holder.append(exc)

    submit_thread = real_thread(target=submit)
    submit_thread.start()
    assert wait_entered.wait(timeout=1.0)
    server._orch_clear_orch_turn(session)
    after_successor = _deferred_turn_state(session)
    release_wait.set()
    submit_thread.join(timeout=2.0)

    assert not submit_thread.is_alive()
    assert error_holder == []
    assert result_holder[0]["result"]["status"] == "streaming"
    assert terminal_events == []
    assert events == []
    assert _deferred_turn_state(session) == after_successor
    assert stale_token is not None


def test_deferred_cancel_stale_operational_owner_to_ordinary_is_noop(
    monkeypatch,
) -> None:
    session, context, stale_token = _deferred_session_and_token(
        "cancel-stale", "gateway-cancel-wait"
    )
    wait_entered = threading.Event()
    release_wait = threading.Event()
    events = []

    def wait_for_agent(*_args):
        wait_entered.set()
        assert release_wait.wait(timeout=2.0)
        return None

    real_thread = _install_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        lambda *_args: pytest.fail("stale cancellation turn dispatched"),
        events,
    )
    result_holder = []
    error_holder = []

    def submit():
        try:
            result_holder.append(
                server._methods["prompt.submit"](
                    "rid-cancel-wait",
                    {
                        "session_id": "gateway-cancel-wait",
                        "text": "synthetic cancellation",
                    },
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic assertion
            error_holder.append(exc)

    submit_thread = real_thread(target=submit)
    submit_thread.start()
    assert wait_entered.wait(timeout=1.0)
    server._orch_clear_orch_turn(session)
    session["_turn_cancel_requested"] = True
    after_successor = _deferred_turn_state(session)
    release_wait.set()
    submit_thread.join(timeout=2.0)

    assert not submit_thread.is_alive()
    assert error_holder == []
    assert result_holder[0]["result"]["status"] == "streaming"
    assert events == []
    assert _deferred_turn_state(session) == after_successor
    assert stale_token is not None


def test_deferred_current_operational_success_dispatches_once_with_expected_token(
    monkeypatch,
) -> None:
    session, context, current_token = _deferred_session_and_token(
        "dispatch-current", "gateway-dispatch-current"
    )
    events = []
    dispatches = []

    def run_prompt(rid, sid, active_session, text, expected_orch_turn_token):
        dispatches.append(
            (rid, sid, active_session, text, expected_orch_turn_token)
        )

    _install_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        lambda *_args: None,
        run_prompt,
        events,
    )

    result = server._methods["prompt.submit"](
        "rid-dispatch-current",
        {
            "session_id": "gateway-dispatch-current",
            "text": "synthetic dispatch",
        },
    )

    assert result["result"]["status"] == "streaming"
    assert len(dispatches) == 1
    assert dispatches[0][4] == current_token
    assert server._orch_turn_token_matches(session, current_token) is True
    assert events == []


def _ordinary_deferred_session_and_context(operation_id: str, gateway_session_id: str):
    session, context, stale_token = _deferred_session_and_token(
        operation_id, gateway_session_id
    )
    server._orch_clear_orch_turn(session)
    return session, context, stale_token


def _install_ordinary_deferred_prompt_hooks(
    monkeypatch,
    session: dict,
    context: dict,
    wait_for_agent,
    run_prompt,
    events: list,
    *,
    terminal_events: list | None = None,
):
    real_thread = _install_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        run_prompt,
        events,
        terminal_events=terminal_events,
    )
    monkeypatch.setattr(
        server,
        "_validate_orch_submit_context",
        lambda _params, _rid: (None, None),
    )
    return real_thread


def test_ordinary_deferred_success_successor_is_zero_dispatch_and_mutation(
    monkeypatch,
) -> None:
    session, context, stale_token = _ordinary_deferred_session_and_context(
        "ordinary-success-stale", "gateway-ordinary-success"
    )
    wait_entered = threading.Event()
    release_wait = threading.Event()
    events = []
    dispatches = []

    def wait_for_agent(*_args):
        wait_entered.set()
        assert release_wait.wait(timeout=2.0)
        return None

    real_thread = _install_ordinary_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        lambda *args, **kwargs: dispatches.append((args, kwargs)),
        events,
    )
    result_holder = []
    error_holder = []

    def submit():
        try:
            result_holder.append(
                server._methods["prompt.submit"](
                    "rid-ordinary-success",
                    {
                        "session_id": "gateway-ordinary-success",
                        "text": "synthetic ordinary success",
                    },
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic assertion
            error_holder.append(exc)

    submit_thread = real_thread(target=submit)
    submit_thread.start()
    assert wait_entered.wait(timeout=1.0)
    successor_token = server._orch_open_orch_turn(
        session,
        "gateway-ordinary-success-next",
        {
            "operation_id": "ordinary-success-next",
            "decision_binding": {"logical_session_id": "logical-ordinary-success-next"},
        },
    )
    session["_orch_model_route"] = {"provider": "synthetic-success-next"}
    after_successor = _deferred_turn_state(session)
    release_wait.set()
    submit_thread.join(timeout=2.0)

    assert not submit_thread.is_alive()
    assert error_holder == []
    assert result_holder[0]["result"]["status"] == "streaming"
    assert dispatches == []
    assert events == []
    assert _deferred_turn_state(session) == after_successor
    assert session["_orch_turn_binding"] == successor_token
    assert stale_token is not None


def test_ordinary_deferred_wait_error_successor_is_zero_mutation_or_event(
    monkeypatch,
) -> None:
    session, context, stale_token = _ordinary_deferred_session_and_context(
        "ordinary-error-stale", "gateway-ordinary-error"
    )
    wait_entered = threading.Event()
    release_wait = threading.Event()
    events = []
    terminal_events = []

    def wait_for_agent(*_args):
        wait_entered.set()
        assert release_wait.wait(timeout=2.0)
        return {"error": {"message": "synthetic ordinary wait failure"}}

    real_thread = _install_ordinary_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        lambda *_args: pytest.fail("stale ordinary wait-error dispatched"),
        events,
        terminal_events=terminal_events,
    )
    monkeypatch.setattr(
        server,
        "_orch_terminalize_before_agent_call",
        lambda *_args: pytest.fail("stale ordinary wait-error terminalized"),
    )
    result_holder = []
    error_holder = []

    def submit():
        try:
            result_holder.append(
                server._methods["prompt.submit"](
                    "rid-ordinary-error",
                    {
                        "session_id": "gateway-ordinary-error",
                        "text": "synthetic ordinary error",
                    },
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic assertion
            error_holder.append(exc)

    submit_thread = real_thread(target=submit)
    submit_thread.start()
    assert wait_entered.wait(timeout=1.0)
    server._orch_open_orch_turn(
        session,
        "gateway-ordinary-error-next",
        {
            "operation_id": "ordinary-error-next",
            "decision_binding": {"logical_session_id": "logical-ordinary-error-next"},
        },
    )
    session["_orch_model_route"] = {"provider": "synthetic-error-next"}
    after_successor = _deferred_turn_state(session)
    release_wait.set()
    submit_thread.join(timeout=2.0)

    assert not submit_thread.is_alive()
    assert error_holder == []
    assert result_holder[0]["result"]["status"] == "streaming"
    assert terminal_events == []
    assert events == []
    assert _deferred_turn_state(session) == after_successor
    assert stale_token is not None


def test_ordinary_deferred_cancel_successor_is_zero_inflight_mutation_or_event(
    monkeypatch,
) -> None:
    session, context, stale_token = _ordinary_deferred_session_and_context(
        "ordinary-cancel-stale", "gateway-ordinary-cancel"
    )
    wait_entered = threading.Event()
    release_wait = threading.Event()
    events = []

    def wait_for_agent(*_args):
        wait_entered.set()
        assert release_wait.wait(timeout=2.0)
        return None

    real_thread = _install_ordinary_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        lambda *_args: pytest.fail("stale ordinary cancellation dispatched"),
        events,
    )
    result_holder = []
    error_holder = []

    def submit():
        try:
            result_holder.append(
                server._methods["prompt.submit"](
                    "rid-ordinary-cancel",
                    {
                        "session_id": "gateway-ordinary-cancel",
                        "text": "synthetic ordinary cancel",
                    },
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic assertion
            error_holder.append(exc)

    submit_thread = real_thread(target=submit)
    submit_thread.start()
    assert wait_entered.wait(timeout=1.0)
    server._orch_open_orch_turn(
        session,
        "gateway-ordinary-cancel-next",
        {
            "operation_id": "ordinary-cancel-next",
            "decision_binding": {"logical_session_id": "logical-ordinary-cancel-next"},
        },
    )
    session["_orch_model_route"] = {"provider": "synthetic-cancel-next"}
    session["_turn_cancel_requested"] = True
    after_successor = _deferred_turn_state(session)
    release_wait.set()
    submit_thread.join(timeout=2.0)

    assert not submit_thread.is_alive()
    assert error_holder == []
    assert result_holder[0]["result"]["status"] == "streaming"
    assert events == []
    assert _deferred_turn_state(session) == after_successor
    assert stale_token is not None


def test_current_ordinary_deferred_success_dispatches_once_with_expected_state(
    monkeypatch,
) -> None:
    session, context, stale_token = _ordinary_deferred_session_and_context(
        "ordinary-current", "gateway-ordinary-current"
    )
    events = []
    dispatches = []

    def run_prompt(*args, **kwargs):
        dispatches.append((args, kwargs))

    _install_ordinary_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        lambda *_args: None,
        run_prompt,
        events,
    )
    result = server._methods["prompt.submit"](
        "rid-ordinary-current",
        {
            "session_id": "gateway-ordinary-current",
            "text": "synthetic ordinary current",
        },
    )

    assert result["result"]["status"] == "streaming"
    assert len(dispatches) == 1
    assert dispatches[0][1]["expected_orch_turn_token"] is not None
    assert dispatches[0][1]["expected_orch_turn_token"].gateway_session_id == (
        "gateway-ordinary-current"
    )
    assert events == []
    assert stale_token is not None


def test_current_ordinary_deferred_cancel_emits_one_error_unchanged(monkeypatch) -> None:
    session, context, stale_token = _ordinary_deferred_session_and_context(
        "ordinary-current-cancel", "gateway-ordinary-current-cancel"
    )
    events = []

    def wait_for_agent(*_args):
        session["_turn_cancel_requested"] = True
        return None

    _install_ordinary_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        wait_for_agent,
        lambda *_args: pytest.fail("ordinary cancellation dispatched"),
        events,
    )
    result = server._methods["prompt.submit"](
        "rid-ordinary-current-cancel",
        {
            "session_id": "gateway-ordinary-current-cancel",
            "text": "synthetic ordinary current cancel",
        },
    )

    assert result["result"]["status"] == "streaming"
    assert len([event for event in events if event[0] == "error"]) == 1
    assert session.get("inflight_turn") is None
    assert stale_token is not None


def test_ordinary_post_wait_successor_at_run_entry_is_rejected(monkeypatch) -> None:
    session, context, stale_token = _ordinary_deferred_session_and_context(
        "ordinary-entry-race", "gateway-ordinary-entry"
    )
    events = []
    original_run_prompt = server._run_prompt_submit
    successor_state = []

    _install_ordinary_deferred_prompt_hooks(
        monkeypatch,
        session,
        context,
        lambda *_args: None,
        lambda *_args, **_kwargs: pytest.fail("race reached unrestricted dispatch"),
        events,
    )

    def race_at_run_entry(
        rid,
        sid,
        active_session,
        text,
        *,
        expected_orch_turn_token,
    ):
        successor = server._orch_open_orch_turn(
            active_session,
            "gateway-ordinary-entry-next",
            {
                "operation_id": "ordinary-entry-next",
                "decision_binding": {"logical_session_id": "logical-ordinary-entry-next"},
            },
        )
        active_session["_orch_model_route"] = {"provider": "synthetic-entry-next"}
        successor_state.append(_deferred_turn_state(active_session))
        original_run_prompt(
            rid,
            sid,
            active_session,
            text,
            expected_orch_turn_token=expected_orch_turn_token,
        )

    monkeypatch.setattr(server, "_run_prompt_submit", race_at_run_entry)
    result = server._methods["prompt.submit"](
        "rid-ordinary-entry",
        {
            "session_id": "gateway-ordinary-entry",
            "text": "synthetic ordinary entry race",
        },
    )

    assert result["result"]["status"] == "streaming"
    assert len(successor_state) == 1
    assert _deferred_turn_state(session) == successor_state[0]
    assert events == []
    assert stale_token is not None


class _WorkerLifetimeAgent:
    model = "synthetic-model"
    provider = "synthetic-provider"
    base_url = ""
    api_key = ""
    api_mode = ""
    session_id = "worker-session"
    _config_context_length = None

    def __init__(self, behavior):
        self._behavior = behavior
        self.interim_assistant_callback = None
        self._on_session_title = None

    def clear_interrupt(self) -> None:
        return None

    def run_conversation(self, _prompt, **kwargs):
        return self._behavior(self, kwargs)


def _worker_lifetime_session(agent: _WorkerLifetimeAgent, sid: str) -> dict:
    return {
        "session_key": "session-worker-" + sid,
        "profile_home": None,
        "history": [],
        "history_version": 0,
        "history_lock": threading.RLock(),
        "running": True,
        "last_active": 0.0,
        "_turn_cancel_requested": False,
        "agent": agent,
        "attached_images": [],
        "image_counter": 0,
        "cols": 80,
        "show_reasoning": False,
        "tool_progress_mode": "all",
        "pending_title": None,
        "transport": None,
    }


def _worker_lifetime_state(session: dict) -> dict:
    return copy.deepcopy(
        {
            key: session.get(key)
            for key in (
                "running",
                "last_active",
                "history",
                "history_version",
                "inflight_turn",
                "_orch_operational",
                "_orch_operation_id",
                "_orch_turn_binding",
                "_orch_gateway_session_id",
                "_orch_model_route",
                "_orch_status_operation_id",
                "_orch_first_delta_observed",
                "_orch_result_consumed",
                "_orch_initialization_category",
                "pending_title",
            )
        }
    )


def _install_worker_lifetime_hooks(
    monkeypatch,
    events: list,
    *,
    first_delta_calls: list | None = None,
    finish_calls: list | None = None,
    terminal_calls: list | None = None,
) -> None:
    monkeypatch.setattr(server, "_emit", lambda *args: events.append(args))
    monkeypatch.setattr(server, "_emit_settled_session_info", lambda *_args: None)
    monkeypatch.setattr(server, "_wire_callbacks", lambda *_args: None)
    monkeypatch.setattr(server, "_set_session_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda *_args: None)
    monkeypatch.setattr(server, "_sync_agent_model_with_config", lambda *_args: None)
    monkeypatch.setattr(server, "_session_cwd", lambda *_args: "/tmp")
    monkeypatch.setattr(server, "_register_session_cwd", lambda *_args: None)
    monkeypatch.setattr(server, "make_stream_renderer", lambda *_args: None)
    monkeypatch.setattr(server, "render_message", lambda *_args: None)
    monkeypatch.setattr(server, "_get_usage", lambda *_args: {})
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_drain_queued_prompt", lambda *_args: False)
    monkeypatch.setattr(server, "_voice_mode_enabled", lambda: False)
    monkeypatch.setattr(server, "_voice_tts_enabled", lambda: False)
    monkeypatch.setattr(server, "_tts_stream_begin", lambda: None)
    monkeypatch.setattr(server, "_pending_reaction_notes", lambda *_args: "")
    monkeypatch.setattr(server, "_hud_surface_note", lambda *_args: "")
    monkeypatch.setattr(server, "_load_interim_assistant_messages", lambda: True)
    monkeypatch.setattr(server, "_retire_turn_marker", lambda *_args: None)
    monkeypatch.setattr(server, "record_turn_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_load_cfg", lambda: {})
    monkeypatch.setattr(server, "_is_successful_goal_turn", lambda *_args: False)
    monkeypatch.setattr(
        server,
        "_plan_goal_compression_recovery",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(server, "_orch_prepare_orch_agent_for_turn", lambda *_args: True)
    if first_delta_calls is not None:
        monkeypatch.setattr(
            server,
            "_orch_mark_first_delta",
            lambda *args: first_delta_calls.append(args),
        )
    if finish_calls is not None:
        monkeypatch.setattr(
            server,
            "_orch_finish_task_observation",
            lambda *args: finish_calls.append(args),
        )
    if terminal_calls is not None:
        monkeypatch.setattr(
            server,
            "_orch_terminalize_before_agent_call",
            lambda *args: terminal_calls.append(args),
        )


def _open_worker_successor(session: dict, sid: str) -> object:
    token = server._orch_open_orch_turn(
        session,
        sid,
        {
            "operation_id": sid + "-operation",
            "decision_binding": {"logical_session_id": sid + "-logical"},
        },
    )
    session["_orch_model_route"] = {"provider": sid + "-provider"}
    return token


def test_ordinary_worker_callbacks_are_fenced_after_operational_successor(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release_callbacks = threading.Event()
    callbacks_done = threading.Event()
    release_result = threading.Event()
    events = []
    first_delta_calls = []

    def behavior(agent, kwargs):
        entered.set()
        assert release_callbacks.wait(timeout=2.0)
        kwargs["stream_callback"]("stale stream delta")
        if agent.interim_assistant_callback is not None:
            agent.interim_assistant_callback("stale interim")
        if agent._on_session_title is not None:
            agent._on_session_title("stale title", "synthetic")
        callbacks_done.set()
        assert release_result.wait(timeout=2.0)
        return {
            "final_response": "stale ordinary result",
            "messages": [{"role": "assistant", "content": "stale ordinary result"}],
        }

    agent = _WorkerLifetimeAgent(behavior)
    session = _worker_lifetime_session(agent, "ordinary-callbacks")
    expectation = server._orch_capture_turn_expectation(
        session, "gateway-ordinary-callbacks", None
    )
    _install_worker_lifetime_hooks(
        monkeypatch,
        events,
        first_delta_calls=first_delta_calls,
    )

    server._run_prompt_submit(
        "rid-ordinary-callbacks",
        "gateway-ordinary-callbacks",
        session,
        "ordinary callback prompt",
        expected_orch_turn_token=expectation,
    )
    assert entered.wait(timeout=2.0)
    events.clear()
    successor = _open_worker_successor(session, "gateway-ordinary-callbacks-next")
    after_successor = _worker_lifetime_state(session)

    release_callbacks.set()
    assert callbacks_done.wait(timeout=2.0)
    assert events == []
    assert first_delta_calls == []
    assert _worker_lifetime_state(session) == after_successor
    assert session["_orch_turn_binding"] == successor

    release_result.set()
    session["_run_thread"].join(timeout=2.0)
    assert not session["_run_thread"].is_alive()


def test_omitted_expectation_worker_is_fenced_after_operational_successor(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release_worker = threading.Event()
    callbacks_done = threading.Event()
    events = []
    first_delta_calls = []

    def behavior(agent, kwargs):
        entered.set()
        assert release_worker.wait(timeout=2.0)
        kwargs["stream_callback"]("stale omitted stream")
        if agent.interim_assistant_callback is not None:
            agent.interim_assistant_callback("stale omitted interim")
        if agent._on_session_title is not None:
            agent._on_session_title("stale omitted title", "synthetic")
        callbacks_done.set()
        return {
            "final_response": "OLD-RESULT",
            "messages": [{"role": "assistant", "content": "OLD-RESULT"}],
        }

    agent = _WorkerLifetimeAgent(behavior)
    session = _worker_lifetime_session(agent, "omitted-expectation")
    _install_worker_lifetime_hooks(
        monkeypatch,
        events,
        first_delta_calls=first_delta_calls,
    )

    # Exercise the real internal caller shape: no expectation keyword.
    server._run_prompt_submit(
        "rid-omitted-expectation",
        "gateway-omitted-expectation",
        session,
        "omitted expectation prompt",
    )
    assert entered.wait(timeout=2.0)
    events.clear()
    successor = _open_worker_successor(
        session, "gateway-omitted-expectation-next"
    )
    after_successor = _worker_lifetime_state(session)

    release_worker.set()
    assert callbacks_done.wait(timeout=2.0)
    session["_run_thread"].join(timeout=2.0)
    assert not session["_run_thread"].is_alive()
    assert events == []
    assert first_delta_calls == []
    assert _worker_lifetime_state(session) == after_successor
    assert session["_orch_turn_binding"] == successor


@pytest.mark.parametrize("result_kind", ["complete", "error"])
def test_ordinary_worker_result_after_operational_successor_is_zero_mutation(
    monkeypatch, result_kind
) -> None:
    entered = threading.Event()
    release_result = threading.Event()
    events = []
    finish_calls = []

    def behavior(_agent, _kwargs):
        entered.set()
        assert release_result.wait(timeout=2.0)
        result = {
            "final_response": "stale ordinary result",
            "messages": [{"role": "assistant", "content": "stale ordinary result"}],
        }
        if result_kind == "error":
            result.update({"error": "synthetic stale provider error", "failed": True})
        return result

    agent = _WorkerLifetimeAgent(behavior)
    session = _worker_lifetime_session(agent, "ordinary-result-" + result_kind)
    expectation = server._orch_capture_turn_expectation(
        session, "gateway-ordinary-result-" + result_kind, None
    )
    _install_worker_lifetime_hooks(monkeypatch, events, finish_calls=finish_calls)

    server._run_prompt_submit(
        "rid-ordinary-result-" + result_kind,
        "gateway-ordinary-result-" + result_kind,
        session,
        "ordinary result prompt",
        expected_orch_turn_token=expectation,
    )
    assert entered.wait(timeout=2.0)
    events.clear()
    _open_worker_successor(session, "gateway-ordinary-result-" + result_kind + "-next")
    after_successor = _worker_lifetime_state(session)

    release_result.set()
    session["_run_thread"].join(timeout=2.0)
    assert not session["_run_thread"].is_alive()
    assert events == []
    assert finish_calls == []
    assert _worker_lifetime_state(session) == after_successor


def test_ordinary_worker_exception_finally_cannot_clear_operational_successor(
    monkeypatch, tmp_path
) -> None:
    entered = threading.Event()
    release_error = threading.Event()
    events = []
    terminal_calls = []

    def behavior(_agent, _kwargs):
        entered.set()
        assert release_error.wait(timeout=2.0)
        raise RuntimeError("synthetic stale worker exception")

    agent = _WorkerLifetimeAgent(behavior)
    session = _worker_lifetime_session(agent, "ordinary-finally")
    expectation = server._orch_capture_turn_expectation(
        session, "gateway-ordinary-finally", None
    )
    _install_worker_lifetime_hooks(
        monkeypatch,
        events,
        terminal_calls=terminal_calls,
    )
    monkeypatch.setattr(
        server,
        "_restore_agent_history_after_turn_error",
        lambda *_args: pytest.fail("stale ordinary worker restored history"),
    )
    monkeypatch.setattr(server, "_CRASH_LOG", str(tmp_path / "synthetic-crash.log"))

    server._run_prompt_submit(
        "rid-ordinary-finally",
        "gateway-ordinary-finally",
        session,
        "ordinary exception prompt",
        expected_orch_turn_token=expectation,
    )
    assert entered.wait(timeout=2.0)
    events.clear()
    _open_worker_successor(session, "gateway-ordinary-finally-next")
    after_successor = _worker_lifetime_state(session)

    release_error.set()
    session["_run_thread"].join(timeout=2.0)
    assert not session["_run_thread"].is_alive()
    assert events == []
    assert terminal_calls == []
    assert _worker_lifetime_state(session) == after_successor


@pytest.mark.parametrize("operational", [False, True])
def test_current_worker_expectation_completes_for_ordinary_and_operational(
    monkeypatch, operational
) -> None:
    events = []
    first_delta_calls = []
    finish_calls = []

    def behavior(_agent, kwargs):
        kwargs["stream_callback"]("current stream delta")
        return {
            "final_response": "current result",
            "messages": [{"role": "assistant", "content": "current result"}],
        }

    agent = _WorkerLifetimeAgent(behavior)
    sid = "gateway-current-" + ("operational" if operational else "ordinary")
    session = _worker_lifetime_session(agent, "current-" + sid)
    if operational:
        expectation = server._orch_open_orch_turn(
            session,
            sid,
            {
                "operation_id": "operation-" + sid,
                "decision_binding": {"logical_session_id": "logical-" + sid},
            },
        )
        session["_orch_model_route"] = {
            "provider": "openai-codex",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "service_tier_preference": "fast",
        }
    else:
        expectation = server._orch_capture_turn_expectation(session, sid, None)
    _install_worker_lifetime_hooks(
        monkeypatch,
        events,
        first_delta_calls=first_delta_calls,
        finish_calls=finish_calls,
    )

    server._run_prompt_submit(
        "rid-" + sid,
        sid,
        session,
        "current worker prompt",
        expected_orch_turn_token=expectation,
    )
    session["_run_thread"].join(timeout=2.0)
    assert not session["_run_thread"].is_alive()
    event_types = [event[0] for event in events]
    assert event_types.count("message.start") == 1
    assert event_types.count("message.delta") == 1
    assert event_types.count("message.complete") == 1
    if operational:
        assert len(first_delta_calls) == 1
        assert len(finish_calls) == 1
    else:
        assert first_delta_calls == []
        assert finish_calls == []


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
        "session_key": "session-init",
        "profile_home": None,
    }
    token = server._orch_open_orch_turn(
        session,
        "gateway-init",
        {
            "operation_id": "operation-init",
            "decision_binding": {"logical_session_id": "logical-init"},
        },
    )
    assert (
        server._orch_record_initialization_failure(session, token)
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
