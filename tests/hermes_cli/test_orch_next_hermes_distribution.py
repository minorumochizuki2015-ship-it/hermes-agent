"""Focused contract tests for the ORCH-Next Hermes source distribution."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

import pytest


REPO_ROOT = Path(__file__).parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "orch_next_hermes_distribution.py"
LAUNCHER_PATH = REPO_ROOT / "scripts" / "orch_next_hermes_mcp_launcher.py"
SPEC = importlib.util.spec_from_file_location(
    "orch_next_hermes_distribution", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
distribution = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(distribution)
LAUNCHER_SPEC = importlib.util.spec_from_file_location(
    "orch_next_hermes_mcp_launcher", LAUNCHER_PATH
)
assert LAUNCHER_SPEC is not None and LAUNCHER_SPEC.loader is not None
launcher = importlib.util.module_from_spec(LAUNCHER_SPEC)
LAUNCHER_SPEC.loader.exec_module(launcher)
DISPATCHER_PATH = (
    REPO_ROOT
    / "skills"
    / "orch-next"
    / "codex-parallel-lanes"
    / "scripts"
    / "dispatch_provider.py"
)
DISPATCHER_SPEC = importlib.util.spec_from_file_location(
    "orch_next_codex_parallel_lane_dispatcher", DISPATCHER_PATH
)
assert DISPATCHER_SPEC is not None and DISPATCHER_SPEC.loader is not None
provider_dispatch = importlib.util.module_from_spec(DISPATCHER_SPEC)
DISPATCHER_SPEC.loader.exec_module(provider_dispatch)


def _source_copy(tmp_path: Path) -> Path:
    target = tmp_path / "source-skills"
    shutil.copytree(REPO_ROOT / "skills" / "orch-next", target)
    return target


def _bundle(tmp_path: Path, source: Path | None = None) -> tuple[Path, Path]:
    source = source or REPO_ROOT / "skills" / "orch-next"
    target = tmp_path / "bundle"
    result = distribution.transactional_install(source, target)
    assert result["status"] == "verified"
    return source, target


def _wrapper_command(bundle: Path) -> list[str]:
    mcp = json.loads((bundle / ".mcp.json").read_text())
    server = mcp["mcpServers"][distribution.PLUGIN_ID]
    return [str(bundle / server["command"].removeprefix("./")), *server["args"]]


def _materialized_manifest(tmp_path: Path) -> tuple[Path, Path]:
    source_bundle = REPO_ROOT / "distribution" / distribution.PLUGIN_ID
    source_manifest_path = source_bundle / "SOURCE_MANIFEST.json"
    installed_bundle = tmp_path.resolve() / "installed" / distribution.PLUGIN_ID
    result = distribution.transactional_install(
        REPO_ROOT / "skills" / "orch-next", installed_bundle
    )
    assert result["status"] == "verified"
    installed_manifest_path = installed_bundle / launcher.SOURCE_MANIFEST_NAME
    return source_manifest_path, installed_manifest_path


def _provider_request(tmp_path: Path, **updates: object) -> dict:
    worktree = (tmp_path / "worktree").resolve()
    worktree.mkdir(exist_ok=True)
    executable = tmp_path / "default-admitted-codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    request = {
        "provider": "codex_luna",
        "surface": "direct_codex_exec",
        "prompt": "apply the bounded implementation slice",
        "cwd": str(worktree),
        "worktree": str(worktree),
        "job_id": "job-luna-001",
        "thread_id": "thread-luna-001",
        "resume": {"mode": "new_session", "session_id": ""},
        "operation_class": "local_patch",
        "work_class": "precision_difficult",
        "requested_model": provider_dispatch.LUNA_MODEL,
        "requested_effort": provider_dispatch.LUNA_EFFORT,
        "advertised_models": [provider_dispatch.LUNA_MODEL],
        "advertised_efforts": [provider_dispatch.LUNA_EFFORT],
        "admitted_codex_provenance": {
            "source": "hermes_local_codex",
            "path": str(executable.resolve()),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "size": executable.stat().st_size,
            "mode": stat.S_IMODE(executable.stat().st_mode),
        },
        "codex_executable": "/tmp/fake-codex",
        "owner_id": "owner-luna-001",
        "write_set": [str((worktree / "owned.py").resolve())],
        "shared_runtime": "runtime-luna-001",
        "authority_disposition": "INAPPLICABLE",
        "app_visible": False,
        "stop_auto_review": False,
    }
    request.update(updates)
    return request


class _FakeCodexResult:
    returncode = 0
    stdout = ""
    stderr = (
        "model: gpt-5.6-luna\n"
        "reasoning effort: max\n"
        "sandbox: danger-full-access\n"
        "approval: never\n"
        "session id: actual-session-001\n"
    )
    native_receipt = None


_DEFAULT_NATIVE_RECEIPT = object()


def _fake_codex_runner(
    calls: list[tuple[list[str], Path]],
    output: str = "done",
    *,
    stderr: str | None = None,
    native_receipt: dict | None | object = _DEFAULT_NATIVE_RECEIPT,
    returncode: int = 0,
):
    def run(command: list[str], cwd: Path) -> _FakeCodexResult:
        calls.append((command, cwd))
        result_path = Path(command[command.index("-o") + 1])
        result_path.write_text(output, encoding="utf-8")
        result = _FakeCodexResult()
        result.returncode = returncode
        if stderr is not None:
            result.stderr = stderr
        result.native_receipt = (
            _native_receipt()
            if native_receipt is _DEFAULT_NATIVE_RECEIPT
            else native_receipt
        )
        return result

    return run


def _native_receipt(
    *,
    model: str = "gpt-5.6-luna",
    effort: str = "max",
    service_tier: str = "UNKNOWN",
    session_id: str = "actual-session-001",
) -> dict:
    return {
        "record_type": provider_dispatch.IDENTITY_RECEIPT_TYPE,
        "source": "codex_native",
        "model": model,
        "reasoning_effort": effort,
        "service_tier": service_tier,
        "sandbox": "danger-full-access",
        "approval_policy": "never",
        "session_id": session_id,
    }


def _ready_provider_request(tmp_path: Path, **updates: object) -> dict:
    executable = tmp_path / "admitted-codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    provenance = {
        "source": "hermes_local_codex",
        "path": str(executable.resolve()),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "size": executable.stat().st_size,
        "mode": stat.S_IMODE(executable.stat().st_mode),
    }
    request = _provider_request(
        tmp_path,
        operation_class="local_patch",
        work_class="precision_difficult",
        owner_id="owner-luna-001",
        write_set=[str((tmp_path / "worktree" / "owned.py").resolve())],
        shared_runtime="runtime-luna-001",
        admitted_codex_provenance=provenance,
        codex_executable=str(tmp_path / "caller-selected-untrusted-codex"),
    )
    request.update(updates)
    return request


def _write_codex_0147_exec_fixture(tmp_path: Path) -> tuple[Path, Path]:
    trace_path = tmp_path / "codex-0147-argv.json"
    executable = tmp_path / "codex-0147-fixture"
    executable.write_text(
        """#!__PYTHON__
import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
with Path(os.environ["CODEX_FIXTURE_TRACE"]).open("a", encoding="utf-8") as trace:
    trace.write(json.dumps(arguments) + "\\n")
if "-a" in arguments or "--ask-for-approval" in arguments:
    print("error: unexpected argument: -a", file=sys.stderr)
    sys.exit(2)

if arguments and arguments[0] == "app-server":
    for raw_line in sys.stdin:
        request = json.loads(raw_line)
        if request.get("id") == 1:
            response = {
                "userAgent": "codex",
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
            }
        elif request.get("id") == 2:
            response = {
                "model": "gpt-5.6-luna",
                "reasoningEffort": "max",
                "serviceTier": "fast",
                "approvalPolicy": "never",
                "sandbox": {"type": "dangerFullAccess"},
                "cwd": os.getcwd(),
                "thread": {"id": "actual-session-001"},
            }
        else:
            continue
        print(json.dumps({"id": request["id"], "result": response}), flush=True)
    sys.exit(0)

result_path = Path(arguments[arguments.index("-o") + 1])
result_path.write_text("done", encoding="utf-8")
print(json.dumps({"type": "thread.started", "thread_id": "actual-session-001"}))
""".replace("__PYTHON__", sys.executable),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, trace_path


def _writer(
    tmp_path: Path,
    index: int,
    *,
    worktree: Path | None = None,
    write_set: list[str] | None = None,
    shared_runtime: str | None = None,
) -> dict:
    worktree = worktree or (tmp_path / f"writer-{index}")
    worktree.mkdir(parents=True, exist_ok=True)
    return {
        "owner_id": f"owner-{index}",
        "worktree": str(worktree.resolve()),
        "write_set": write_set or [str((worktree / "owned.py").resolve())],
        "shared_runtime": shared_runtime or f"runtime-{index}",
    }


class _FakeAppServerStdin:
    def __init__(self, process: "_FakeAppServerProcess") -> None:
        self.process = process
        self.closed = False

    def write(self, value: str) -> int:
        self.process.write_count += 1
        if self.process.fail_write_at == self.process.write_count:
            raise BrokenPipeError("disconnected app-server")
        self.process.inputs.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        self.process.stdin_eof = True
        self.process.eof_read_count = self.process.read_count


class _FakeAppServerStdout:
    def __init__(self, process: "_FakeAppServerProcess", output: str) -> None:
        self.process = process
        self.lines = output.splitlines(keepends=True)

    def fileno(self) -> int:
        return 0

    def readline(self) -> str:
        self.process.read_count += 1
        if not self.lines:
            return ""
        return self.lines.pop(0)


class _FakeAppServerProcess:
    def __init__(
        self,
        output: str,
        *,
        returncode: int = 0,
        fail_write_at: int | None = None,
        wait_timeout: bool = False,
    ) -> None:
        self.returncode = returncode
        self.inputs: list[str] = []
        self.stdin_eof = False
        self.eof_read_count: int | None = None
        self.read_count = 0
        self.write_count = 0
        self.fail_write_at = fail_write_at
        self.wait_timeout = wait_timeout
        self.wait_timeouts: list[float | None] = []
        self.wait_called = False
        self.stdin = _FakeAppServerStdin(self)
        self.stdout = _FakeAppServerStdout(self, output)

    def wait(self, timeout: float | None = None) -> int:
        self.wait_called = True
        self.wait_timeouts.append(timeout)
        if self.wait_timeout:
            raise subprocess.TimeoutExpired("codex app-server", timeout)
        return self.returncode


def _thread_resume_response(
    worktree: Path,
    session_id: str,
    *,
    effort: str = provider_dispatch.LUNA_EFFORT,
    service_tier: str | None = "fast",
) -> dict:
    return {
        "model": provider_dispatch.LUNA_MODEL,
        "reasoningEffort": effort,
        "serviceTier": service_tier,
        "approvalPolicy": "never",
        "sandbox": {"type": "dangerFullAccess"},
        "cwd": str(worktree),
        "thread": {"id": session_id},
    }


def _production_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    event_session_id: str = "actual-session-001",
    response_session_id: str | None = None,
    omit_field: str | None = None,
    malformed_sandbox: bool = False,
    effort: str | None = None,
) -> tuple[dict, list[tuple[list[str], Path, dict]], _FakeAppServerProcess]:
    request = _ready_provider_request(tmp_path)
    if effort is not None:
        request.update(
            work_class="deterministic_mechanical",
            requested_effort=effort,
            advertised_efforts=[effort],
        )
    executable = Path(request["admitted_codex_provenance"]["path"])
    monkeypatch.setattr(provider_dispatch.shutil, "which", lambda _: str(executable))
    direct_calls: list[tuple[list[str], Path, dict]] = []
    response = _thread_resume_response(
        Path(request["worktree"]),
        response_session_id or event_session_id,
        effort=request["requested_effort"],
        service_tier=(
            "default" if request["requested_effort"] == "high" else "fast"
        ),
    )
    if omit_field is not None:
        response.pop(omit_field)
    if malformed_sandbox:
        response["sandbox"] = {"type": "workspaceWrite"}
    app_server_output = "\n".join(
        [
            json.dumps(
                {
                    "id": 1,
                    "result": {
                        "userAgent": "codex",
                        "codexHome": "/tmp/codex-home",
                        "platformFamily": "unix",
                        "platformOs": "macos",
                    },
                }
            ),
            json.dumps({"id": 2, "result": response}),
        ]
    ) + "\n"
    app_server = _FakeAppServerProcess(app_server_output)

    def fake_run(command, cwd, **kwargs):
        direct_calls.append((command, cwd, kwargs))
        assert command[1] == "exec"
        result_path = Path(command[command.index("-o") + 1])
        result_path.write_text("done", encoding="utf-8")
        event_stream = "\n".join(
            [
                json.dumps(
                    {"type": "thread.started", "thread_id": event_session_id}
                ),
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "turn.completed"}),
            ]
        ) + "\n"
        return subprocess.CompletedProcess(command, 0, event_stream, "")

    popen_calls: list[tuple[list[str], dict]] = []

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        assert command == [str(executable), "app-server"]
        return app_server

    monkeypatch.setattr(
        provider_dispatch.select,
        "select",
        lambda streams, _write, _error, _timeout: (
            ([streams[0]], [], []) if app_server.stdout.lines else ([], [], [])
        ),
    )
    monkeypatch.setattr(provider_dispatch.subprocess, "run", fake_run)
    monkeypatch.setattr(provider_dispatch.subprocess, "Popen", fake_popen)
    receipt = provider_dispatch.dispatch(request)
    assert len(popen_calls) == 1
    return receipt, direct_calls, app_server


def test_plain_completed_process_uses_jsonl_and_same_session_app_server_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, direct_calls, app_server = _production_dispatch(tmp_path, monkeypatch)

    assert receipt["status"] == "completed"
    identity = receipt["runtime_identity_receipt"]
    assert identity["actual_model"] == provider_dispatch.LUNA_MODEL
    assert identity["actual_reasoning_effort"] == provider_dispatch.LUNA_EFFORT
    assert identity["actual_service_tier"] == "fast"
    assert identity["sandbox"] == provider_dispatch.SANDBOX
    assert identity["approval_policy"] == provider_dispatch.APPROVAL_POLICY
    assert identity["actual_session_id"] == "actual-session-001"
    assert identity["binding_source"] == "codex_app_server_thread_resume"
    assert identity["verified"] is True
    assert receipt["service_tier_runtime_verified"] is True
    assert receipt["result_consumed"] is False
    assert "--json" in direct_calls[0][0]

    assert app_server.stdin_eof is True
    assert app_server.wait_called is True
    assert app_server.eof_read_count == 2
    assert app_server.read_count == 2
    assert len(app_server.inputs) == 2
    wire = "".join(app_server.inputs)
    messages = [json.loads(line) for line in wire.splitlines() if line.strip()]
    assert [message.get("method") for message in messages] == [
        "initialize",
        "notifications/initialized",
        "thread/resume",
    ]
    assert messages[2]["params"]["threadId"] == "actual-session-001"
    assert messages[2]["params"]["excludeTurns"] is True
    assert messages[0]["params"]["capabilities"] == {"experimentalApi": True}


def test_deterministic_high_accepts_truthful_default_service_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, _direct_calls, _app_server = _production_dispatch(
        tmp_path,
        monkeypatch,
        effort="high",
    )
    assert receipt["status"] == "completed"
    assert receipt["runtime_identity_receipt"]["actual_service_tier"] == "default"
    assert receipt["runtime_identity_receipt"]["verified"] is True
    assert receipt["service_tier_runtime_verified"] is True


@pytest.mark.parametrize("effort", ["high", provider_dispatch.LUNA_EFFORT])
def test_app_server_rejects_null_service_tier(
    tmp_path: Path,
    effort: str,
) -> None:
    response = _thread_resume_response(
        tmp_path,
        "actual-session-001",
        effort=effort,
        service_tier=None,
    )

    with pytest.raises(provider_dispatch.DispatchError) as raised:
        provider_dispatch._validated_app_server_identity(
            response,
            worktree=tmp_path,
            session_id="actual-session-001",
            effort=effort,
        )

    assert raised.value.code == "child_identity_unavailable"


def test_app_server_initialize_requires_experimental_api_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeAppServerProcess(
        json.dumps(
            {
                "id": 1,
                "error": {
                    "code": -32600,
                    "message": "thread/resume.excludeTurns requires experimentalApi capability",
                },
            }
        )
        + "\n"
    )
    app_server_wire = provider_dispatch._app_server_wire
    monkeypatch.setattr(
        provider_dispatch,
        "_app_server_wire",
        lambda session_id: app_server_wire(session_id, experimental_api=False),
    )
    monkeypatch.setattr(
        provider_dispatch.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        provider_dispatch.select,
        "select",
        lambda streams, _write, _error, _timeout: ([streams[0]], [], []),
    )

    with pytest.raises(provider_dispatch.DispatchError) as raised:
        provider_dispatch._app_server_thread_resume(
            tmp_path / "codex", tmp_path, "session-001"
        )

    assert raised.value.code == "child_identity_unavailable"
    initialize = json.loads(process.inputs[0])
    assert initialize["params"]["capabilities"] == {}
    assert process.stdin_eof is True


def test_app_server_timeout_closes_stdin_and_waits_for_graceful_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeAppServerProcess("")
    monkeypatch.setattr(
        provider_dispatch.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        provider_dispatch.select,
        "select",
        lambda *_args: ([], [], []),
    )
    monkeypatch.setattr(
        provider_dispatch,
        "MAX_APP_SERVER_READ_TIMEOUT_SECONDS",
        0.0,
        raising=False,
    )

    with pytest.raises(provider_dispatch.DispatchError) as raised:
        provider_dispatch._app_server_thread_resume(
            tmp_path / "codex", tmp_path, "session-001"
        )

    assert raised.value.code == "child_identity_unavailable"
    assert process.stdin_eof is True
    assert process.wait_called is True


def test_app_server_eof_cleanup_wait_is_bounded_and_typed_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeAppServerProcess("", wait_timeout=True)
    monkeypatch.setattr(
        provider_dispatch.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        provider_dispatch.select,
        "select",
        lambda *_args: ([], [], []),
    )

    with pytest.raises(provider_dispatch.DispatchError) as raised:
        provider_dispatch._app_server_thread_resume(
            tmp_path / "codex", tmp_path, "session-001"
        )

    assert raised.value.code == "child_cleanup_timeout"
    assert process.stdin_eof is True
    assert process.wait_called is True
    assert process.wait_timeouts == [
        provider_dispatch.MAX_APP_SERVER_WAIT_TIMEOUT_SECONDS
    ]


def test_app_server_disconnect_closes_stdin_and_waits_for_graceful_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeAppServerProcess("")
    monkeypatch.setattr(
        provider_dispatch.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        provider_dispatch.select,
        "select",
        lambda streams, _write, _error, _timeout: ([streams[0]], [], []),
    )

    with pytest.raises(provider_dispatch.DispatchError) as raised:
        provider_dispatch._app_server_thread_resume(
            tmp_path / "codex", tmp_path, "session-001"
        )

    assert raised.value.code == "child_identity_unavailable"
    assert process.stdin_eof is True
    assert process.wait_called is True


def test_production_readback_session_mismatch_is_typed_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, _direct_calls, app_server = _production_dispatch(
        tmp_path,
        monkeypatch,
        response_session_id="different-session-001",
    )

    assert receipt["status"] == "blocked"
    assert receipt["code"] == "session_identity_mismatch"
    assert receipt["result_consumed"] is False
    assert receipt["runtime_identity_receipt"]["actual_session_id"] == (
        "different-session-001"
    )
    assert receipt["runtime_identity_receipt"]["verified"] is False
    assert app_server.stdin_eof is True


@pytest.mark.parametrize(
    ("omit_field", "malformed_sandbox"),
    [("serviceTier", False), (None, True)],
)
def test_production_readback_malformed_or_missing_identity_is_typed_and_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    omit_field: str | None,
    malformed_sandbox: bool,
) -> None:
    receipt, _direct_calls, app_server = _production_dispatch(
        tmp_path,
        monkeypatch,
        omit_field=omit_field,
        malformed_sandbox=malformed_sandbox,
    )

    assert receipt["status"] == "blocked"
    assert receipt["code"] == "child_identity_unavailable"
    assert receipt["result_consumed"] is False
    assert receipt["runtime_identity_receipt"]["verified"] is False
    assert app_server.stdin_eof is True


def test_operation_class_derives_protected_maestro_handoff_not_caller_disposition(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    receipt = provider_dispatch.dispatch(
        _provider_request(
            tmp_path,
            operation_class="public_deploy_or_release",
            authority_disposition="INAPPLICABLE",
        ),
        runner=_fake_codex_runner(calls),
    )

    assert receipt["status"] == "handoff"
    assert receipt["code"] == "maestro_authority_handoff"
    assert receipt["launch"] is False
    assert receipt["authority_disposition"] == "REQUIRED"
    assert receipt["handoff"]["operation_class"] == "public_deploy_or_release"
    assert receipt["handoff"]["execution"] == "maestro_authority"
    assert calls == []


def test_routine_operation_class_is_derived_inapplicable_even_if_caller_claims_required(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _ready_provider_request(
        tmp_path,
        authority_disposition="REQUIRED",
    )
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(
            calls,
            native_receipt=_native_receipt(),
        ),
    )

    assert receipt["status"] == "completed"
    assert receipt["authority_disposition"] == "INAPPLICABLE"
    assert receipt["operation_class"] == "local_patch"


def test_terminal_result_is_bounded_sanitized_and_unconsumed_without_ack(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    receipt = provider_dispatch.dispatch(
        _ready_provider_request(tmp_path),
        runner=_fake_codex_runner(
            calls,
            output="token=super-secret\n\x1b[31mDONE\x1b[0m\n",
            native_receipt=_native_receipt(),
        ),
    )

    assert receipt["status"] == "completed"
    assert receipt["result_consumed"] is False
    result = receipt["result"]
    assert result["terminal_output"] == "token=<redacted>\nDONE\n"
    assert "\x1b" not in result["terminal_output"]
    assert result["acknowledged"] is False
    assert result["consumption"] == "awaiting_consumer_acknowledgment"
    assert result["sha256"]


@pytest.mark.parametrize(
    ("work_class", "effort", "service_tier", "argv_marker"),
    [
        ("deterministic_mechanical", "high", "default", "service_tier=fast"),
        ("precision_difficult", "max", "fast", "service_tier=fast"),
    ],
)
def test_luna_effort_and_fast_service_tier_are_bound_to_work_class(
    tmp_path: Path,
    work_class: str,
    effort: str,
    service_tier: str,
    argv_marker: str | None,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _ready_provider_request(
        tmp_path,
        work_class=work_class,
        requested_effort=effort,
        advertised_efforts=[effort],
    )
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(
            calls,
            native_receipt=_native_receipt(
                effort=effort,
                service_tier=service_tier,
            ),
        ),
    )

    assert receipt["status"] == "completed"
    assert receipt["requested"]["effort"] == effort
    assert receipt["requested"]["service_tier"] == "fast"
    assert receipt["runtime_identity_receipt"]["actual_service_tier"] == service_tier
    command = calls[0][0]
    assert f"model_reasoning_effort={effort}" in command
    if argv_marker is None:
        assert not any("service_tier=" in item for item in command)
    else:
        assert argv_marker in command
    assert receipt["stop_auto_review_runtime_bound"] is False


def test_caller_selected_codex_is_ignored_and_admitted_provenance_is_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _ready_provider_request(tmp_path)
    admitted_path = Path(request["admitted_codex_provenance"]["path"])
    monkeypatch.setattr(provider_dispatch.shutil, "which", lambda _: str(admitted_path))
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(
            calls,
            native_receipt=_native_receipt(),
        ),
    )

    assert receipt["status"] == "completed"
    assert calls[0][0][0] == str(admitted_path)
    assert calls[0][0][0] != request["codex_executable"]
    assert receipt["executable_provenance"]["verified"] is True
    assert receipt["executable_provenance"]["sha256"] == request[
        "admitted_codex_provenance"
    ]["sha256"]


def test_path_symlink_to_admitted_codex_target_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _ready_provider_request(tmp_path)
    admitted_path = Path(request["admitted_codex_provenance"]["path"])
    path_alias = tmp_path / "path-codex"
    path_alias.symlink_to(admitted_path)
    monkeypatch.setattr(provider_dispatch.shutil, "which", lambda _: str(path_alias))

    resolved, provenance = provider_dispatch._resolve_admitted_codex(
        request, test_runner=None
    )

    assert resolved == admitted_path
    assert provenance["path"] == str(admitted_path)


def test_provenance_symlink_is_rejected_even_when_target_matches(
    tmp_path: Path,
) -> None:
    request = _ready_provider_request(tmp_path)
    admitted_path = Path(request["admitted_codex_provenance"]["path"])
    provenance_alias = tmp_path / "provenance-codex"
    provenance_alias.symlink_to(admitted_path)
    provenance = dict(request["admitted_codex_provenance"])
    provenance["path"] = str(provenance_alias)
    request["admitted_codex_provenance"] = provenance

    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner([], native_receipt=_native_receipt()),
    )

    assert receipt["status"] == "blocked"
    assert receipt["code"] == "codex_executable_provenance_invalid"


def test_path_symlink_to_mismatched_codex_target_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _ready_provider_request(tmp_path)
    other_target = tmp_path / "other-codex"
    other_target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    other_target.chmod(0o755)
    path_alias = tmp_path / "path-codex"
    path_alias.symlink_to(other_target)
    monkeypatch.setattr(provider_dispatch.shutil, "which", lambda _: str(path_alias))

    with pytest.raises(provider_dispatch.DispatchError) as raised:
        provider_dispatch._resolve_admitted_codex(request, test_runner=None)

    assert raised.value.code == "codex_executable_provenance_mismatch"


def test_arbitrary_child_text_cannot_verify_runtime_identity(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []
    receipt = provider_dispatch.dispatch(
        _ready_provider_request(tmp_path),
        runner=_fake_codex_runner(calls, native_receipt=None),
    )

    assert receipt["status"] == "blocked"
    assert receipt["code"] == "child_identity_unavailable"
    assert receipt["runtime_identity_receipt"]["verified"] is False
    assert receipt["runtime_identity_receipt"]["binding_source"] != (
        "codex_startup_header"
    )


def test_exact_resume_compares_requested_and_actual_session_identity(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _ready_provider_request(
        tmp_path,
        resume={"mode": "exact_session_id", "session_id": "requested-session-001"},
    )
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(
            calls,
            native_receipt=_native_receipt(session_id="actual-session-001"),
        ),
    )

    assert receipt["status"] == "blocked"
    assert receipt["code"] == "session_identity_mismatch"
    identity = receipt["runtime_identity_receipt"]
    assert identity["requested_session_id"] == "requested-session-001"
    assert identity["actual_session_id"] == "actual-session-001"
    assert identity["session_identity_match"] is False


def test_writer_admission_allows_four_disjoint_writers_and_rejects_fifth(
    tmp_path: Path,
) -> None:
    writers = [_writer(tmp_path, index) for index in range(4)]
    calls: list[tuple[list[str], Path]] = []
    request = _ready_provider_request(
        tmp_path,
        owner_id=writers[0]["owner_id"],
        worktree=writers[0]["worktree"],
        cwd=writers[0]["worktree"],
        write_set=writers[0]["write_set"],
        shared_runtime=writers[0]["shared_runtime"],
        writers=writers,
    )
    accepted = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(
            calls,
            native_receipt=_native_receipt(),
        ),
    )
    assert accepted["status"] == "completed"
    assert accepted["writer_admission"]["count"] == 4
    assert accepted["writer_admission"]["max"] == 4

    fifth_writers = [_writer(tmp_path, index) for index in range(5)]
    rejected = provider_dispatch.dispatch(
        _ready_provider_request(
            tmp_path,
            owner_id=fifth_writers[0]["owner_id"],
            worktree=fifth_writers[0]["worktree"],
            cwd=fifth_writers[0]["worktree"],
            write_set=fifth_writers[0]["write_set"],
            shared_runtime=fifth_writers[0]["shared_runtime"],
            writers=fifth_writers,
        ),
        runner=_fake_codex_runner(calls),
    )
    assert rejected["status"] == "blocked"
    assert rejected["code"] == "writer_cohort_too_large"
    assert rejected["launch"] is False


@pytest.mark.parametrize("conflict", ["worktree", "write_set", "shared_runtime"])
def test_writer_admission_rejects_scope_conflicts(
    tmp_path: Path,
    conflict: str,
) -> None:
    first = _writer(tmp_path, 1)
    second = _writer(tmp_path, 2)
    if conflict == "worktree":
        second["worktree"] = first["worktree"]
    elif conflict == "write_set":
        second["write_set"] = first["write_set"]
    else:
        second["shared_runtime"] = first["shared_runtime"]
    request = _ready_provider_request(
        tmp_path,
        writers=[first, second],
        owner_id=first["owner_id"],
        worktree=first["worktree"],
        cwd=first["worktree"],
        write_set=first["write_set"],
        shared_runtime=first["shared_runtime"],
    )

    receipt = provider_dispatch.dispatch(request)

    assert receipt["status"] == "blocked"
    assert receipt["code"] == "operation_scope_conflict"
    assert receipt["launch"] is False


def test_explicit_luna_uses_direct_codex_with_bounded_receipt(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _provider_request(tmp_path)
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(calls),
    )

    assert receipt["status"] == "completed"
    assert receipt["code"] == "codex_luna_completed"
    assert receipt["fallback_used"] is False
    assert receipt["result_consumed"] is False
    assert receipt["result"]["acknowledged"] is False
    assert receipt["terminal_class"] == "completed"
    assert receipt["requested"] == {
        "model": "gpt-5.6-luna",
        "effort": "max",
        "service_tier": "fast",
    }
    identity = receipt["runtime_identity_receipt"]
    assert receipt["advertised"] == {
        "models": ["gpt-5.6-luna"],
        "efforts": ["max"],
        "surface": "direct_codex_exec",
    }
    assert identity["actual_model"] == "gpt-5.6-luna"
    assert identity["actual_reasoning_effort"] == "max"
    assert identity["cwd"] == request["cwd"] == identity["worktree"]
    assert identity["approval_policy"] == "never"
    assert identity["sandbox"] == "danger-full-access"
    assert identity["job_id"] == "job-luna-001"
    assert identity["thread_id"] == "thread-luna-001"
    assert identity["session_id"] == "actual-session-001"
    assert identity["verified"] is True
    assert receipt["app_visibility"]["required_for_scheduling"] is False
    assert receipt["stop_auto_review_enabled"] is False
    assert len(calls) == 1
    command, cwd = calls[0]
    assert cwd == Path(request["worktree"])
    assert command[:10] == [
        request["admitted_codex_provenance"]["path"],
        "exec",
        "-m",
        "gpt-5.6-luna",
        "-C",
        str(cwd),
        "-s",
        "danger-full-access",
        "-c",
        "model_reasoning_effort=max",
    ]
    assert "model_reasoning_effort=max" in command
    assert "approval_policy=never" in command


def test_codex_0147_actual_exec_rejects_obsolete_approval_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, trace_path = _write_codex_0147_exec_fixture(tmp_path)
    provenance = {
        "source": "hermes_local_codex",
        "path": str(executable.resolve()),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "size": executable.stat().st_size,
        "mode": stat.S_IMODE(executable.stat().st_mode),
    }
    request = _ready_provider_request(
        tmp_path,
        admitted_codex_provenance=provenance,
        codex_executable=str(executable),
    )
    monkeypatch.setattr(provider_dispatch.shutil, "which", lambda _: str(executable))
    monkeypatch.setenv("CODEX_FIXTURE_TRACE", str(trace_path))

    receipt = provider_dispatch.dispatch(request)

    assert receipt["status"] == "completed"
    assert receipt["runtime_identity_receipt"]["actual_model"] == provider_dispatch.LUNA_MODEL
    assert receipt["runtime_identity_receipt"]["actual_reasoning_effort"] == provider_dispatch.LUNA_EFFORT
    assert receipt["runtime_identity_receipt"]["actual_service_tier"] == "fast"
    assert receipt["runtime_identity_receipt"]["actual_session_id"] == "actual-session-001"
    traced_commands = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    argv = traced_commands[0]
    assert "-a" not in argv
    assert "--ask-for-approval" not in argv
    assert "approval_policy=never" in argv


def test_nonzero_child_failure_returns_bounded_category_without_stderr(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    receipt = provider_dispatch.dispatch(
        _provider_request(tmp_path),
        runner=_fake_codex_runner(
            calls,
            stderr="error: unexpected argument: -a api_key=secret-value",
            returncode=2,
        ),
    )

    assert receipt["status"] == "blocked"
    assert receipt["code"] == "codex_exec_failed"
    assert receipt["result"] == {
        "state": "typed_failure",
        "reason": "child_exit_nonzero",
        "failure_category": "cli_argument_rejected",
    }
    serialized = json.dumps(receipt)
    assert "unexpected argument" not in serialized
    assert "secret-value" not in serialized


def test_hermes_native_is_delegate_task_handoff_only(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _provider_request(
        tmp_path,
        provider="hermes_native",
        surface="delegate_task",
        requested_model="UNKNOWN",
        requested_effort="UNKNOWN",
        advertised_models=[],
    )
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(calls),
    )

    assert receipt["status"] == "handoff"
    assert receipt["handoff"] == {
        "provider": "hermes_native",
        "surface": "delegate_task",
        "execution": "delegate_task",
        "installed": False,
        "qualified": False,
    }
    assert calls == []


def test_codex_plugin_cc_is_bridge_handoff_only(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _provider_request(
        tmp_path,
        provider="codex_plugin_cc",
        surface="codex_plugin_cc",
        requested_model="UNKNOWN",
        requested_effort="UNKNOWN",
        advertised_models=[],
    )
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(calls),
    )

    assert receipt["status"] == "handoff"
    assert receipt["code"] == "codex_plugin_cc_bridge_handoff"
    assert receipt["handoff"]["execution"] == "codex_plugin_cc_bridge"
    assert receipt["handoff"]["installed"] is False
    assert calls == []


def test_missing_luna_advertisement_is_typed_unavailable_without_substitution(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _provider_request(tmp_path, advertised_models=["gpt-5.6-sol"])
    receipt = provider_dispatch.dispatch(request, runner=_fake_codex_runner(calls))

    assert receipt["status"] == "unavailable"
    assert receipt["code"] == "codex_luna_unavailable"
    assert receipt["launch"] is False
    assert receipt["requested"]["model"] == "gpt-5.6-luna"
    assert receipt["result"]["reason"] == "advertised_model_missing"
    assert calls == []


def test_exact_session_resume_is_required_and_latest_is_rejected(tmp_path: Path) -> None:
    latest = _provider_request(
        tmp_path,
        resume={"mode": "latest", "session_id": "latest", "latest_allowed": True},
    )
    receipt = provider_dispatch.dispatch(latest)
    assert receipt["status"] == "blocked"
    assert receipt["code"] == "latest_session_resume_forbidden"

    missing = _provider_request(
        tmp_path,
        resume={"mode": "exact_session_id", "session_id": ""},
    )
    receipt = provider_dispatch.dispatch(missing)
    assert receipt["status"] == "blocked"
    assert receipt["code"] == "exact_session_resume_required"


def test_exact_session_is_passed_to_codex_without_latest_fallback(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []
    request = _provider_request(
        tmp_path,
        resume={"mode": "exact_session_id", "session_id": "session-42"},
    )
    receipt = provider_dispatch.dispatch(
        request,
        runner=_fake_codex_runner(
            calls,
            native_receipt=_native_receipt(session_id="session-42"),
        ),
    )
    assert receipt["status"] == "completed"
    command = calls[0][0]
    assert command[-3:] == ["resume", "session-42", request["prompt"]]
    assert receipt["runtime_identity_receipt"]["resume_mode"] == "exact_session_id"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("approval_policy", "on-request", "approval_policy_must_be_never"),
        ("sandbox", "read-only", "sandbox_must_be_danger_full_access"),
        ("stop_auto_review", True, "stop_auto_review_must_be_false"),
    ],
)
def test_local_capability_is_bounded_and_stop_review_stays_disabled(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    request = _provider_request(tmp_path, **{field: value})
    receipt = provider_dispatch.dispatch(request)
    assert receipt["status"] == "blocked"
    assert receipt["code"] == code


@pytest.mark.parametrize("output", ["", "UNKNOWN", "unknown"])
def test_empty_or_unknown_child_result_is_typed_failure(
    tmp_path: Path, output: str
) -> None:
    calls: list[tuple[list[str], Path]] = []
    receipt = provider_dispatch.dispatch(
        _provider_request(tmp_path),
        runner=_fake_codex_runner(calls, output=output),
    )
    assert receipt["status"] == "blocked"
    assert receipt["code"] == "empty_or_unknown_output"
    assert receipt["result_consumed"] is False
    assert receipt["terminal_class"] == "blocked"


def test_protected_route_is_required_and_fail_closed(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []
    receipt = provider_dispatch.dispatch(
        _provider_request(
            tmp_path,
            operation_class="public_deploy_or_release",
            authority_disposition="INAPPLICABLE",
        ),
        runner=_fake_codex_runner(calls),
    )
    assert receipt["status"] == "handoff"
    assert receipt["code"] == "maestro_authority_handoff"
    assert receipt["launch"] is False
    assert receipt["continuation_allowed"] is False
    assert calls == []


def test_orch_skill_authority_scope_is_narrow_and_provider_aware() -> None:
    source_root = REPO_ROOT / "skills" / "orch-next"
    codex = (source_root / "codex-parallel-lanes" / "SKILL.md").read_text()
    assert "hermes_native" in codex
    assert "codex_luna" in codex
    assert "codex_plugin_cc" in codex
    assert "gpt-5.6-luna" in codex
    assert "delegate_task" in codex
    assert "direct_codex_exec" in codex
    assert "Codex.app" in codex
    assert "never a scheduler" in codex
    for name in ("agent-dispatch", "cmd-delegation-orchestration"):
        text = (source_root / name / "SKILL.md").read_text()
        assert "INAPPLICABLE" in text
        assert "continuation_allowed=true" in text
        assert "REQUIRED" in text
        assert "final_acceptance" in text
        assert "credential_oauth_or_secret_mutation" in text
        assert "model-risk disposition" not in text
        assert "validator verdict" not in text
        assert "claim promotion" not in text


def test_source_manifest_binds_hermes_source_and_separates_authority() -> None:
    bundle = REPO_ROOT / "distribution" / distribution.PLUGIN_ID
    manifest = json.loads((bundle / "SOURCE_MANIFEST.json").read_text())
    profile = manifest["operational_profile"]
    assert manifest["operational_source_authority"] == (
        distribution.OPERATIONAL_SOURCE_AUTHORITY
    )
    assert manifest["operational_source_root"] == distribution.OPERATIONAL_SOURCE_ROOT
    assert manifest["authority_policy_source"] == distribution.AUTHORITY_POLICY_SOURCE
    binding = manifest["operational_source_binding"]
    assert binding["binding_kind"] == "immutable_SOURCE_MANIFEST"
    assert binding["binding_present"] is True
    assert binding["source_root"] == "skills/orch-next"
    assert binding["self_content_binding"]["digest"] == (
        distribution._source_manifest_self_digest(manifest)
    )
    assert profile["operational_source_authority"] == (
        distribution.OPERATIONAL_SOURCE_AUTHORITY
    )
    assert profile["source_root"] == distribution.OPERATIONAL_SOURCE_ROOT
    assert profile["authority_policy_source"] == distribution.AUTHORITY_POLICY_SOURCE
    assert profile["authority_bundle"] == {
        "digest": distribution.MAESTRO_AUTHORITY_BUNDLE_DIGEST,
        "identity": distribution.MAESTRO_AUTHORITY_BUNDLE_ID,
        "version": distribution.MAESTRO_AUTHORITY_BUNDLE_VERSION,
    }
    compatibility = manifest["legacy_maestro_compatibility"]
    assert compatibility["classification"] == (
        "legacy_non_authoritative_compatibility_material"
    )
    assert compatibility["same_name_bytes_authoritative"] is False
    assert compatibility["plugin_distribution_is_mirror"] is False
    assert compatibility["zero_consumer_proof"] is False
    assert compatibility["retirement_claimed"] is False


def test_mcp_manifest_binds_relative_launcher_to_plugin_root() -> None:
    bundle = REPO_ROOT / "distribution" / distribution.PLUGIN_ID
    mcp = json.loads((bundle / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"][distribution.PLUGIN_ID]

    assert server["cwd"] == "."


def test_codex_mcp_manifest_uses_contained_executable_launcher() -> None:
    bundle = REPO_ROOT / "distribution" / distribution.PLUGIN_ID
    mcp = json.loads((bundle / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"][distribution.PLUGIN_ID]
    wrapper = bundle / distribution.RUNTIME_WRAPPER_PATH

    assert server["command"] == f"./{distribution.RUNTIME_WRAPPER_PATH}"
    assert server["args"] == []
    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o755
    assert wrapper.read_text(encoding="utf-8").splitlines()[0] == "#!/usr/bin/python3"


def test_codex_mcp_command_contract_rejects_absolute_bootstrap() -> None:
    with pytest.raises(
        distribution.DistributionError,
        match="Codex Agent Plugins stdio command must be bare or contained",
    ):
        distribution._validate_codex_mcp_command("/usr/bin/python3")

    distribution._validate_codex_mcp_command(
        f"./{distribution.RUNTIME_WRAPPER_PATH}"
    )


def test_bundle_admits_fixed_sdo_producer_mirror() -> None:
    bundle = REPO_ROOT / "distribution" / distribution.PLUGIN_ID
    binding = json.loads(
        (bundle / distribution.RUNTIME_BINDING_PATH).read_text(encoding="utf-8")
    )
    mirror = binding["sdo_producer"]["root"]
    assert mirror == "runtime/sdo_producer"
    assert binding["sdo_producer"]["source_revision"] == (
        "c25555b54315b8dc868d12b8699b500b9aab8094"
    )
    assert binding["sdo_producer"]["source_tree"] == (
        "ba7e28fef29e9a28c93ff9226f260e74bc061e3c"
    )
    for relative in distribution.SDO_PRODUCER_MIRROR_FILES:
        path = bundle / mirror / relative
        assert path.is_file()
        assert path.is_relative_to(bundle)


def test_mk733j_successor_closure_is_pinned_to_integrated_maestro_source() -> None:
    required = {
        "scripts/ops/mk733j_activation.py",
        "scripts/ops/mk733j_decision_os.py",
        "scripts/ops/mk733j_hook_contract_self_test.py",
        "scripts/ops/verify_mk733j_n_implementation.py",
        "scripts/ops/critical_thread_route.py",
        "scripts/ops/mk_decision_preflight.py",
        "scripts/ops/mk733j_qualification.py",
        "scripts/ops/mk733j_capability_bundles.py",
        "scripts/ops/mk733j_context_compiler.py",
        "scripts/ops/requirement_anchor_semantic.py",
        "scripts/ops/mk_adaptive_work_pace.py",
        "scripts/ops/mk_fable5_execution_authority.py",
        "scripts/ops/mk733j_schema_safety.py",
        "scripts/ops/verify_task_ledger_v1.py",
        "scripts/ops/mk747_fable5_cognitive_core.py",
    }

    assert distribution.PLUGIN_VERSION == "0.1.47"
    assert distribution.SDO_PRODUCER_SOURCE_REVISION == (
        "c25555b54315b8dc868d12b8699b500b9aab8094"
    )
    assert distribution.SDO_PRODUCER_SOURCE_TREE == (
        "ba7e28fef29e9a28c93ff9226f260e74bc061e3c"
    )
    assert required <= set(distribution.SDO_PRODUCER_MIRROR_FILES)


@pytest.mark.parametrize(
    "relative",
    (
        "scripts/ops/mk733j_activation.py",
        "scripts/ops/mk733j_decision_os.py",
        "scripts/ops/mk733j_hook_contract_self_test.py",
        "scripts/ops/verify_mk733j_n_implementation.py",
        "scripts/ops/critical_thread_route.py",
        "scripts/ops/mk_decision_preflight.py",
        "scripts/ops/mk733j_qualification.py",
        "scripts/ops/mk733j_capability_bundles.py",
        "scripts/ops/mk733j_context_compiler.py",
        "scripts/ops/requirement_anchor_semantic.py",
        "scripts/ops/mk_adaptive_work_pace.py",
        "scripts/ops/mk_fable5_execution_authority.py",
        "scripts/ops/mk733j_schema_safety.py",
        "scripts/ops/verify_task_ledger_v1.py",
        "scripts/ops/mk747_fable5_cognitive_core.py",
    ),
)
def test_mk733j_successor_closure_rejects_each_tampered_file(
    relative: str, tmp_path: Path
) -> None:
    mirror = tmp_path / distribution.SDO_PRODUCER_MIRROR_ROOT
    shutil.copytree(
        REPO_ROOT / distribution.SDO_PRODUCER_MIRROR_ROOT,
        mirror,
    )
    path = mirror / relative
    path.write_bytes(path.read_bytes() + b"tamper")

    with pytest.raises(
        distribution.DistributionError,
        match=f"SDO producer mirror drift: {relative}",
    ):
        distribution._sdo_producer_binding(tmp_path)


def test_contained_codex_launcher_lists_prompt_submit_over_mcp() -> None:
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("MCP runtime package is unavailable in this test environment")
    bundle = REPO_ROOT / "distribution" / distribution.PLUGIN_ID
    wrapper = bundle / distribution.RUNTIME_WRAPPER_PATH
    source_launcher = REPO_ROOT / distribution.RUNTIME_LAUNCHER_PATH
    manifest = bundle / "SOURCE_MANIFEST.json"
    child = f'''
import importlib.util
import os
import sys
from pathlib import Path

bundle = Path({str(bundle)!r})
sys.path.insert(0, {str(REPO_ROOT)!r})
spec = importlib.util.spec_from_file_location("source_launcher_fixture", {str(source_launcher)!r})
launcher = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(launcher)
# The protected authority is an external live boundary.  This focused loader
# fixture supplies only the existing authority consumer result so it can
# exercise real candidate provenance and the launcher-to-MCP path without
# contacting or mutating that boundary.  The generated contained wrapper is
# probed separately below.
launcher._bundle_root = lambda: None
from tui_gateway import maestro_authority
manifest_path = Path({str(manifest)!r})
launcher._runtime_head = lambda: "1" * 40
candidate, candidate_digest = launcher._candidate_runtime_provenance(str(manifest_path))

def fake_consumer(context, _actual):
    return {{
        "outcome": "allow",
        "decision_id": context["decision_binding"]["decision_id"],
        "consumed_once": True,
        "runtime_provenance_manifest": candidate,
        "runtime_provenance_manifest_digest": candidate_digest,
    }}

maestro_authority.consume_maestro_authority_decision = fake_consumer
launcher._load_authority_consumer = lambda _source_root: maestro_authority
import model_tools
model_tools.get_tool_definitions = lambda **_kwargs: []
os.environ.pop("ORCH_SDO_PRODUCER_ROOT", None)
sys.argv = [
    {str(wrapper)!r},
    launcher.RUNTIME_PROVENANCE_MANIFEST_FLAG,
    {str(manifest)!r},
]
launcher.main()
'''
    wire = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "distribution-fixture", "version": "1"},
                    },
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            ),
        ]
    ) + "\n"
    env = os.environ.copy()
    env.update({"HERMES_QUIET": "1", "HERMES_REDACT_SECRETS": "true"})
    env.pop("ORCH_SDO_PRODUCER_ROOT", None)
    with tempfile.TemporaryDirectory(prefix="hermes-codex-loader-") as hermes_home:
        env["HERMES_HOME"] = hermes_home
        origin_probe = subprocess.run(
            [str(wrapper), "--verify-origin"],
            cwd=Path("/private/tmp"),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert origin_probe.returncode == 0
        completed = subprocess.run(
            [str(distribution.runtime_python()), "-I", "-c", child],
            cwd=Path("/private/tmp"),
            env=env,
            input=wire,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    assert completed.returncode == 0
    responses = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip()
    ]
    listed = next(response for response in responses if response.get("id") == 2)
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "orch_prompt_submit" in names


def _rewrite_binding(bundle: Path, mutate) -> None:
    binding_path = bundle / distribution.RUNTIME_BINDING_PATH
    binding = json.loads(binding_path.read_text())
    mutate(binding)
    binding_bytes = distribution._json_bytes(binding)
    binding_path.write_bytes(binding_bytes)
    manifest_path = bundle / "SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["mcp"]["locator"]["binding_sha256"] = hashlib.sha256(
        binding_bytes
    ).hexdigest()
    manifest["mcp"]["locator"]["mode"] = binding["mode"]
    manifest["mcp"]["locator"]["rollback_identity"] = binding["rollback_identity"]
    manifest_path.write_bytes(distribution._json_bytes(manifest))


def _latest_release(tag: str | None = None) -> dict:
    return {
        "tag_name": tag or distribution.HERMES_AGENT_UPSTREAM_TAG,
        "name": "Hermes Agent stable",
        "body": "Bounded release notes",
        "published_at": "2026-08-03T16:57:52Z",
        "draft": False,
        "prerelease": False,
    }


def test_latest_stable_preflight_accepts_only_current_pinned_release() -> None:
    assert distribution.verify_latest_stable_release(_latest_release()) == {
        "tag": distribution.HERMES_AGENT_UPSTREAM_TAG,
        "name": "Hermes Agent stable",
        "published_at": "2026-08-03T16:57:52Z",
        "release_notes": "present",
        "candidate_commit": distribution.HERMES_AGENT_UPSTREAM_COMMIT,
        "candidate_version": distribution.HERMES_AGENT_RUNTIME_VERSION,
        "status": "latest_stable_verified",
    }

    with pytest.raises(
        distribution.DistributionError,
        match="newer official stable release requires bounded forward port",
    ):
        distribution.verify_latest_stable_release(_latest_release("v2026.8.4"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("draft", True),
        ("prerelease", True),
        ("body", ""),
    ],
)
def test_latest_stable_preflight_rejects_unadmitted_release_metadata(
    field: str, value: object
) -> None:
    release = _latest_release()
    release[field] = value
    with pytest.raises(
        distribution.DistributionError,
        match="official latest stable release metadata invalid",
    ):
        distribution.verify_latest_stable_release(release)


def test_checked_in_bundle_is_exact_dual_channel_source() -> None:
    bundle = REPO_ROOT / "distribution" / distribution.PLUGIN_ID
    result = distribution.verify_bundle(bundle, REPO_ROOT / "skills" / "orch-next")
    assert result == {
        "bundle": str(bundle),
        "identity": "orch-next-hermes-harness",
        "mcp_module": "agent.transports.hermes_tools_mcp_server",
        "recursive_file_count": 70,
        "skill_count": 47,
            "skill_closure_digest": (
                "c869e171d1cb15c6e5004642b9db3a51fa19033471cc824a65566269ab073329"
            ),
        "status": "verified",
    }

    source_manifest = json.loads((bundle / "SOURCE_MANIFEST.json").read_text())
    assert set(source_manifest["channels"]) == {"codex", "claude"}
    assert source_manifest["operational_adoption"] == "qualification_pending"
    profile = source_manifest["operational_profile"]
    profile_digest = profile.pop("profile_digest")
    assert profile == {
        "authority_bundle": {
            "digest": distribution.MAESTRO_AUTHORITY_BUNDLE_DIGEST,
            "identity": distribution.MAESTRO_AUTHORITY_BUNDLE_ID,
            "version": distribution.MAESTRO_AUTHORITY_BUNDLE_VERSION,
        },
        "authority_policy_reference": distribution.MAESTRO_OWNERSHIP_MANIFEST,
        "authority_policy_source": distribution.AUTHORITY_POLICY_SOURCE,
        "content": {
            "digest": distribution.EXPECTED_SKILL_CLOSURE_DIGEST,
            "recursive_file_count": 70,
            "source_root": "skills/orch-next",
        },
        "full_skill_closure_injected_into_runtime_prompt": False,
        "identity": distribution.COMPACT_OPERATIONAL_PROFILE_ID,
        "legacy_maestro_compatibility": distribution.LEGACY_MAESTRO_COMPATIBILITY,
        "operational_source_authority": distribution.OPERATIONAL_SOURCE_AUTHORITY,
        "operational_source_binding": {
            "binding_kind": "immutable_SOURCE_MANIFEST",
            "binding_present": True,
            "binding_required_at": "source_verification",
            "manifest_path": "SOURCE_MANIFEST.json",
            "self_content_binding": {
                "algorithm": "sha256",
                "scope": "canonical_SOURCE_MANIFEST_without_self_digest",
            },
        },
        "prompt_materialization": distribution.COMPACT_OPERATIONAL_PROFILE_MODE,
        "source_root": "skills/orch-next",
        "runtime_baseline": {
            "commit": distribution.HERMES_AGENT_UPSTREAM_COMMIT,
            "tag": distribution.HERMES_AGENT_UPSTREAM_TAG,
            "version": distribution.HERMES_AGENT_RUNTIME_VERSION,
        },
        "terminal_authority": {
            "contract_id": distribution.TERMINAL_AUTHORITY_CONTRACT_ID,
            "contract_version": distribution.TERMINAL_AUTHORITY_CONTRACT_VERSION,
            "profile": distribution.TERMINAL_AUTHORITY_PROFILE,
            "profile_sha256": distribution.TERMINAL_AUTHORITY_PROFILE_SHA256,
            "source": distribution.TERMINAL_AUTHORITY_SOURCE,
            "source_revision": distribution.MAESTRO_AUTHORITY_SOURCE_REVISION,
            "source_sha256": distribution.TERMINAL_AUTHORITY_SOURCE_SHA256,
        },
    }
    assert (
        profile_digest
        == hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    assert source_manifest["claims"] == {
        "cross_project_acceptance": False,
        "exclusive_default": False,
        "installed_adoption": False,
        "persistent_runtime_acceptance": False,
        "source_bundle": True,
    }
    assert source_manifest["hermes_agent_runtime"] == {
        "upstream_commit": distribution.HERMES_AGENT_UPSTREAM_COMMIT,
        "upstream_tag": distribution.HERMES_AGENT_UPSTREAM_TAG,
        "version": distribution.HERMES_AGENT_RUNTIME_VERSION,
    }
    assert source_manifest["heartbeat_terminal_authority"] == {
        "contract_id": distribution.TERMINAL_AUTHORITY_CONTRACT_ID,
        "contract_version": distribution.TERMINAL_AUTHORITY_CONTRACT_VERSION,
        "profile": distribution.TERMINAL_AUTHORITY_PROFILE,
        "profile_sha256": distribution.TERMINAL_AUTHORITY_PROFILE_SHA256,
        "source": distribution.TERMINAL_AUTHORITY_SOURCE,
        "source_revision": distribution.MAESTRO_AUTHORITY_SOURCE_REVISION,
        "source_sha256": distribution.TERMINAL_AUTHORITY_SOURCE_SHA256,
    }
    assert source_manifest["mcp"]["launcher"] == distribution.RUNTIME_WRAPPER_PATH
    assert source_manifest["mcp"]["python"] == distribution.RUNTIME_SYSTEM_PYTHON
    assert source_manifest["mcp"]["locator"]["mode"] == (
        distribution.RUNTIME_LOCATOR_MODE_PORTABLE
    )
    assert source_manifest["version"] == "0.1.47"
    assert source_manifest["maestro_skill_source"] == (
        distribution._maestro_skill_source_binding()
    )
    assert [entry["path"] for entry in source_manifest["maestro_skill_source"]["validation_files"]] == [
        "scripts/ops/verify_critical_thread_route.py",
        "scripts/ops/verify_inc178_whole_goal_work_selection.py",
        "scripts/ops/verify_heartbeat_cmd_control_guard_skill.py",
    ]
    profile_index_path = bundle / distribution.OPERATIONAL_PROFILE_INDEX_PATH
    profile_index = json.loads(profile_index_path.read_text(encoding="utf-8"))
    assert profile_index["schema"] == distribution.OPERATIONAL_PROFILE_INDEX_SCHEMA
    assert profile_index["identity"] == distribution.COMPACT_OPERATIONAL_PROFILE_ID
    assert profile_index["package"]["plugin_version"] == distribution.PLUGIN_VERSION
    assert profile_index["execution_contract"] == (
        distribution._topology_neutral_execution_contract()
    )
    assert profile_index["execution_contract"]["repo_count_is_selector"] is False
    profiles = {
        row["qualified_skill_id"]: row for row in profile_index["skills"]
    }
    assert profiles[
        f"{distribution.PLUGIN_ID}:best-evaluate"
    ]["trigger_mode"] == "unresolved_comparison"
    assert profiles[
        f"{distribution.PLUGIN_ID}:orch-skill-ecosystem-improvement"
    ]["trigger_mode"] == "repeated_miss_nonfire"
    assert profiles[
        f"{distribution.PLUGIN_ID}:workflow-plan-test-patch"
    ]["trigger_mode"] == "task_specific"
    design_taste = profiles[f"{distribution.PLUGIN_ID}:design-taste"]
    assert [row["path"] for row in design_taste["required_references"]] == [
        "references/anti-generic-rules.md",
        "references/japanese-typography.md",
        "references/llmo-aio-evidence.md",
        "references/reference-site-teardowns.md",
    ]
    declared_index = source_manifest["operational_profile_index"]
    assert declared_index["path"] == distribution.OPERATIONAL_PROFILE_INDEX_PATH
    assert declared_index["digest"] == hashlib.sha256(
        profile_index_path.read_bytes()
    ).hexdigest()
    binding = json.loads(
        (bundle / distribution.RUNTIME_BINDING_PATH).read_text(encoding="utf-8")
    )
    assert binding["minimum_source_revision"] == distribution.ORCH_OVERLAY_MINIMUM_REVISION
    assert binding["authority_source"] == distribution.TERMINAL_AUTHORITY_SOURCE
    assert binding["authority_source_revision"] == (
        distribution.MAESTRO_AUTHORITY_SOURCE_REVISION
    )
    assert binding["authority_source_sha256"] == (
        distribution.TERMINAL_AUTHORITY_SOURCE_SHA256
    )
    assert tuple(entry["path"] for entry in binding["runtime_files"]) == (
        "scripts/orch_next_hermes_mcp_launcher.py",
        "agent/__init__.py",
        "agent/skill_materializer.py",
        "agent/skill_utils.py",
        "agent/jiter_preload.py",
        "agent/secret_sources/__init__.py",
        "agent/secret_sources/_cache.py",
        "agent/secret_sources/base.py",
        "agent/secret_sources/bitwarden.py",
        "agent/secret_sources/command.py",
        "agent/transports/hermes_orch_front_door.py",
        "agent/transports/hermes_tools_mcp_server.py",
        "hermes_cli/__init__.py",
        "hermes_cli/audit_firing_admission.py",
        "hermes_cli/env_loader.py",
        "hermes_cli/main.py",
        "hermes_cli/subcommands/dashboard.py",
        "hermes_cli/web_server.py",
        "hermes_constants.py",
        "hermes_state.py",
        "model_tools.py",
        "pyproject.toml",
        "scripts/__init__.py",
        "scripts/orch_next_hermes_plugin_adoption.py",
        "scripts/orch_next_hermes_serve_service.py",
        "scripts/orch_next_hermes_serve_service_launcher.sh",
        "scripts/orch_next_hermes_session_token_source.py",
        "tui_gateway/__init__.py",
        "tui_gateway/maestro_authority.py",
        "tui_gateway/maestro_authority_allowed_signers",
        "tui_gateway/maestro_plugin_adoption_authority.py",
        "tui_gateway/sdo_adapter.py",
        "tui_gateway/server.py",
        "runtime/sdo_producer/scripts/ops/issue_inc178_current_transition.py",
        "runtime/sdo_producer/scripts/ops/mk_whole_goal_control.py",
        "runtime/sdo_producer/scripts/ops/resolve_mk94_priority_action_queue.py",
        "runtime/sdo_producer/research/mk675/fable5_derived/synthesis_records.json",
        "runtime/sdo_producer/scripts/ops/mk733j_activation.py",
        "runtime/sdo_producer/scripts/ops/mk733j_decision_os.py",
        "runtime/sdo_producer/scripts/ops/mk733j_hook_contract_self_test.py",
        "runtime/sdo_producer/scripts/ops/verify_mk733j_n_implementation.py",
        "runtime/sdo_producer/scripts/ops/critical_thread_route.py",
        "runtime/sdo_producer/scripts/ops/mk_decision_preflight.py",
        "runtime/sdo_producer/scripts/ops/mk733j_qualification.py",
        "runtime/sdo_producer/scripts/ops/mk733j_capability_bundles.py",
        "runtime/sdo_producer/scripts/ops/mk733j_context_compiler.py",
        "runtime/sdo_producer/scripts/ops/requirement_anchor_semantic.py",
        "runtime/sdo_producer/scripts/ops/mk_adaptive_work_pace.py",
        "runtime/sdo_producer/scripts/ops/mk_fable5_execution_authority.py",
        "runtime/sdo_producer/scripts/ops/mk733j_schema_safety.py",
        "runtime/sdo_producer/scripts/ops/verify_task_ledger_v1.py",
        "runtime/sdo_producer/scripts/ops/mk747_fable5_cognitive_core.py",
        "tools/skills_tool.py",
        "skills/orch-next/heartbeat-cmd-control-guard/scripts/heartbeat_control.py",
    )
    assert binding["plugin_version"] == source_manifest["version"]
    assert (
        binding["runtime_python_sha256"]
        == hashlib.sha256(
            distribution.runtime_python().resolve(strict=True).read_bytes()
        ).hexdigest()
    )
    assert (
        binding["runtime_python_size"]
        == distribution.runtime_python().resolve(strict=True).stat().st_size
    )
    assert (
        binding["skill_closure_digest"]
        == source_manifest["skills"]["sorted_recursive_file_sha256_stream_digest"]
    )
    anchor = next(
        entry
        for entry in binding["runtime_files"]
        if entry["path"] == "tui_gateway/maestro_authority_allowed_signers"
    )
    assert (
        anchor["sha256"]
        == hashlib.sha256((REPO_ROOT / anchor["path"]).read_bytes()).hexdigest()
    )
    serialized = json.dumps(source_manifest, sort_keys=True)
    assert str(REPO_ROOT) not in serialized


def test_authority_digest_is_identical_across_producer_consumer_and_manifest() -> None:
    import hermes_state
    from tui_gateway import maestro_authority

    source_manifest = json.loads(
        (
            REPO_ROOT / "distribution" / distribution.PLUGIN_ID / "SOURCE_MANIFEST.json"
        ).read_text(encoding="utf-8")
    )
    manifest_digest = source_manifest["operational_profile"]["authority_bundle"][
        "digest"
    ]

    assert hermes_state.HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST == manifest_digest
    assert maestro_authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST == manifest_digest
    assert distribution.MAESTRO_AUTHORITY_BUNDLE_DIGEST == manifest_digest


def test_checked_in_marketplace_binds_both_hosts_to_one_plugin() -> None:
    if not (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").is_file():
        pytest.skip("Codex marketplace manifest is unavailable in this read-only worktree")
    result = distribution.verify_marketplace(REPO_ROOT)
    assert result == {
        "identity": "orch-next-hermes-local",
        "plugin": "orch-next-hermes-harness",
        "status": "verified",
    }

    codex = json.loads(
        (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    claude = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert codex["plugins"][0]["name"] == claude["plugins"][0]["name"]
    assert codex["plugins"][0]["source"]["path"] == claude["plugins"][0]["source"]


@pytest.mark.parametrize("channel", ["codex", "claude"])
def test_marketplace_verifier_rejects_identity_drift(
    tmp_path: Path, channel: str
) -> None:
    if not (REPO_ROOT / ".agents").is_dir():
        pytest.skip("Codex marketplace tree is unavailable in this read-only worktree")
    root = tmp_path / "marketplace"
    shutil.copytree(REPO_ROOT / ".agents", root / ".agents")
    shutil.copytree(REPO_ROOT / ".claude-plugin", root / ".claude-plugin")
    (root / "distribution" / distribution.PLUGIN_ID).mkdir(parents=True)
    path = (
        root / ".agents" / "plugins" / "marketplace.json"
        if channel == "codex"
        else root / ".claude-plugin" / "marketplace.json"
    )
    manifest = json.loads(path.read_text())
    manifest["plugins"][0]["name"] = "legacy-maestro-executor"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        distribution.DistributionError, match="marketplace manifest drift"
    ):
        distribution.verify_marketplace(root)


def test_mcp_uses_only_existing_hermes_server_without_fallback(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    mcp = json.loads((bundle / ".mcp.json").read_text())
    assert mcp == {
        "mcpServers": {
            "orch-next-hermes-harness": {
                "args": [],
                "command": f"./{distribution.RUNTIME_WRAPPER_PATH}",
                "cwd": ".",
                "env": {
                    "HERMES_HOME": str(distribution.runtime_hermes_home()),
                    "HERMES_QUIET": "1",
                    "HERMES_REDACT_SECRETS": "true",
                },
                "type": "stdio",
            }
        }
    }


def test_pinned_mcp_runtime_resolves_module_outside_source_cwd(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_pinned_mcp_runtime_ignores_path_and_pythonpath_hijack(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    rogue_bin = tmp_path / "bin"
    rogue_agent = tmp_path / "agent" / "transports"
    rogue_bin.mkdir()
    rogue_agent.mkdir(parents=True)
    (rogue_bin / "python3").write_text("#!/bin/sh\nexit 99\n")
    (rogue_bin / "python3").chmod(0o755)
    (tmp_path / "agent" / "__init__.py").write_text("")
    (tmp_path / "agent" / "transports" / "__init__.py").write_text("")
    (rogue_agent / "hermes_tools_mcp_server.py").write_text("raise SystemExit(98)\n")
    env = os.environ.copy()
    env["PATH"] = f"{rogue_bin}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(tmp_path)
    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0


def test_launcher_fails_when_expected_origin_is_not_admitted(tmp_path: Path) -> None:
    copied_launcher = tmp_path / "scripts" / "orch_next_hermes_mcp_launcher.py"
    copied_launcher.parent.mkdir()
    shutil.copy2(distribution.runtime_launcher(), copied_launcher)
    completed = subprocess.run(
        [
            str(distribution.runtime_python()),
            "-I",
            str(copied_launcher),
            "--verify-origin",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode != 0
    assert "outside the admitted checkout" in completed.stderr


def test_installer_materialized_locator_survives_bundle_relocation(
    tmp_path: Path,
) -> None:
    _, bundle = _bundle(tmp_path)
    relocated = tmp_path / "relocated" / "bundle"
    relocated.parent.mkdir()
    bundle.rename(relocated)

    completed = subprocess.run(
        [*_wrapper_command(relocated), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_runtime_locator_rejects_path_escape_before_runtime_call(
    tmp_path: Path,
) -> None:
    _, bundle = _bundle(tmp_path)

    def mutate(binding: dict) -> None:
        binding["runtime_files"][0]["path"] = "../outside-launcher.py"
        binding["runtime_files_digest"] = distribution._runtime_files_digest(
            binding["runtime_files"]
        )

    _rewrite_binding(bundle, mutate)
    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert completed.stderr == "runtime locator admitted file set drift\n"


def test_runtime_locator_rejects_symlinked_source_alias(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    alias = tmp_path / "source-alias"
    alias.symlink_to(REPO_ROOT, target_is_directory=True)

    def mutate(binding: dict) -> None:
        binding["source_root"] = str(alias)

    _rewrite_binding(bundle, mutate)
    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert "symlink or alias" in completed.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "runtime_launcher",
            "scripts/missing-launcher.py",
            "runtime locator scalar binding drift",
        ),
        (
            "runtime_python",
            ".venv/bin/missing-python",
            "runtime locator scalar binding drift",
        ),
    ],
)
def test_runtime_locator_rejects_missing_runtime_target(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    _, bundle = _bundle(tmp_path)
    _rewrite_binding(bundle, lambda binding: binding.__setitem__(field, value))

    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert message in completed.stderr


def test_runtime_locator_rejects_alternate_existing_executable_before_exec(
    tmp_path: Path,
) -> None:
    _, bundle = _bundle(tmp_path)
    alternate = REPO_ROOT / ".venv" / "bin" / "python3"
    assert alternate.is_file() and os.access(alternate, os.X_OK)
    _rewrite_binding(
        bundle,
        lambda binding: binding.__setitem__("runtime_python", ".venv/bin/python3"),
    )

    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "runtime locator scalar binding drift\n"


def test_runtime_interpreter_retarget_after_admission_fails_closed(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    python_link = source_root / ".venv" / "bin" / "python"
    python_link.parent.mkdir(parents=True)
    admitted = distribution.runtime_python().resolve(strict=True)
    python_link.symlink_to(admitted)
    binding = {
        "runtime_python": ".venv/bin/python",
        "runtime_python_sha256": hashlib.sha256(admitted.read_bytes()).hexdigest(),
        "runtime_python_size": admitted.stat().st_size,
    }
    assert launcher._verified_runtime_interpreter(source_root, binding) == (
        python_link,
        admitted,
    )
    python_link.unlink()
    python_link.symlink_to("/usr/bin/true")
    with pytest.raises(SystemExit, match="runtime interpreter identity drift"):
        launcher._verified_runtime_interpreter(source_root, binding)


@pytest.mark.parametrize(
    "missing",
    ["model_tools.py", "hermes_cli/env_loader.py", "hermes_constants.py"],
)
def test_sparse_runtime_dependency_closure_fails_before_exec(
    tmp_path: Path, missing: str
) -> None:
    source_root = tmp_path / "sparse-source"
    for relative in distribution.RUNTIME_ADMITTED_FILES:
        if relative == missing:
            continue
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)

    with pytest.raises(
        distribution.DistributionError,
        match=f"admitted runtime file unavailable: {missing}",
    ):
        distribution._runtime_file_entries(source_root)


def test_verified_startup_never_discovers_optional_provider_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("MCP runtime package is unavailable in this test environment")
    import model_tools

    calls = []

    def forbidden_optional_discovery(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("optional tool discovery must not run in origin dry-run")

    monkeypatch.setattr(
        model_tools, "get_tool_definitions", forbidden_optional_discovery
    )
    launcher.verified_startup()

    assert calls == []
    assert model_tools.get_tool_definitions is forbidden_optional_discovery


def test_installed_manifest_projects_to_source_provenance_through_fake_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tui_gateway import maestro_authority

    source_manifest_path, installed_manifest_path = _materialized_manifest(tmp_path)
    runtime_head = "1" * 40
    monkeypatch.setattr(launcher, "_runtime_head", lambda: runtime_head)

    source_candidate, source_digest = launcher._candidate_runtime_provenance(
        str(source_manifest_path)
    )
    installed_candidate, installed_digest = launcher._candidate_runtime_provenance(
        str(installed_manifest_path)
    )

    assert installed_candidate == source_candidate
    assert installed_digest == source_digest
    assert source_candidate["runtimeContentDigest"] == hashlib.sha256(
        source_manifest_path.read_bytes()
    ).hexdigest()

    observed: dict[str, object] = {}

    def fake_consumer(context: dict, actual: dict) -> dict:
        observed["actual"] = actual
        return {
            "outcome": "allow",
            "decision_id": context["decision_binding"]["decision_id"],
            "consumed_once": True,
            "runtime_provenance_manifest": installed_candidate,
            "runtime_provenance_manifest_digest": installed_digest,
        }

    monkeypatch.setattr(
        maestro_authority, "consume_maestro_authority_decision", fake_consumer
    )
    monkeypatch.setattr(
        launcher,
        "_load_authority_consumer",
        lambda _source_root: maestro_authority,
    )

    consumed_candidate, consumed_digest = launcher._consume_runtime_provenance_authority(
        str(installed_manifest_path)
    )

    assert consumed_candidate == source_candidate
    assert consumed_digest == source_digest
    assert observed["actual"]["runtime_revision"] == runtime_head


@pytest.mark.parametrize("mutation", ["tampered", "missing", "malformed", "extra", "stale"])
def test_installed_manifest_rejects_invalid_self_content_binding(
    tmp_path: Path, mutation: str
) -> None:
    source_manifest_path, installed_manifest_path = _materialized_manifest(tmp_path)
    manifest = json.loads(installed_manifest_path.read_text(encoding="utf-8"))
    self_binding = manifest["operational_source_binding"]["self_content_binding"]
    if mutation == "tampered":
        self_binding["digest"] = "0" * 64
    elif mutation == "missing":
        self_binding.pop("digest")
    elif mutation == "malformed":
        manifest["operational_source_binding"]["self_content_binding"] = []
    elif mutation == "extra":
        self_binding["unexpected"] = True
    else:
        canonical = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        self_binding["digest"] = canonical["operational_source_binding"][
            "self_content_binding"
        ]["digest"]
    installed_manifest_path.write_bytes(distribution._json_bytes(manifest))

    with pytest.raises(
        SystemExit, match="Hermes runtime provenance authority unavailable"
    ):
        launcher._candidate_runtime_provenance(str(installed_manifest_path))


def test_wrapper_keeps_binding_verification_before_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path.resolve() / "installed" / distribution.PLUGIN_ID
    bundle.mkdir(parents=True)
    (bundle / launcher.SOURCE_MANIFEST_NAME).write_text("{}\n", encoding="utf-8")
    calls: list[str] = []

    def verify(_bundle: Path) -> tuple[Path, Path, Path]:
        calls.append("verify")
        return Path("python"), Path("python-target"), Path("launcher")

    def capture_exec(*_args: object) -> None:
        calls.append("exec")
        raise RuntimeError("exec-captured")

    monkeypatch.setattr(launcher, "_verify_binding", verify)
    monkeypatch.setattr(launcher.os, "execve", capture_exec)
    monkeypatch.setattr(launcher.sys, "argv", [str(bundle / "runtime" / "wrapper")])

    with pytest.raises(RuntimeError, match="exec-captured"):
        launcher._run_portable_wrapper(bundle)

    assert calls == ["verify", "verify", "exec"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.__setitem__("identity", "not-hermes"),
        lambda manifest: manifest["mcp"].__setitem__("transport", "http"),
    ],
)
def test_manifest_projection_rejects_non_locator_mutation(
    tmp_path: Path,
    mutation,
) -> None:
    _, installed_manifest_path = _materialized_manifest(tmp_path)
    manifest = json.loads(installed_manifest_path.read_text(encoding="utf-8"))
    mutation(manifest)
    installed_manifest_path.write_bytes(distribution._json_bytes(manifest))

    with pytest.raises(
        SystemExit, match="Hermes runtime provenance authority unavailable"
    ):
        launcher._candidate_runtime_provenance(str(installed_manifest_path))


def test_runtime_provenance_authority_binds_exact_candidate_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tui_gateway import maestro_authority

    manifest_path = (
        REPO_ROOT / "distribution" / distribution.PLUGIN_ID / "SOURCE_MANIFEST.json"
    ).resolve(strict=True)
    runtime_head = "1" * 40
    monkeypatch.setattr(launcher, "_runtime_head", lambda: runtime_head)
    candidate, digest = launcher._candidate_runtime_provenance(str(manifest_path))
    observed = {}

    def exact_allow(context, actual):
        observed["context"] = context
        observed["actual"] = actual
        assert context["threshold_policy"] == {
            "version": maestro_authority.HERMES_TELEMETRY_SCHEMA_VERSION,
            "digest": maestro_authority.HERMES_TELEMETRY_SCHEMA_DIGEST,
        }
        assert context["task_declaration"] == {
            "task_class": "operations",
            "prompt_contract_version": (
                maestro_authority.HERMES_SESSION_TOKEN_PROMPT_CONTRACT_VERSION
            ),
            "prompt_contract_digest": (
                maestro_authority.HERMES_SESSION_TOKEN_PROMPT_CONTRACT_DIGEST
            ),
        }
        return {
            "outcome": "allow",
            "decision_id": context["decision_binding"]["decision_id"],
            "consumed_once": True,
            "runtime_provenance_manifest": candidate,
            "runtime_provenance_manifest_digest": digest,
        }

    monkeypatch.setattr(
        maestro_authority, "consume_maestro_authority_decision", exact_allow
    )
    monkeypatch.setattr(
        launcher,
        "_load_authority_consumer",
        lambda _source_root: maestro_authority,
    )
    launcher._consume_runtime_provenance_authority(str(manifest_path))

    assert observed["actual"]["runtime_revision"] == runtime_head
    assert observed["context"]["decision_binding"]["runtime_revision"] == runtime_head
    serialized = json.dumps(observed, sort_keys=True)
    assert "secret" not in serialized.lower()
    assert "credential" not in serialized.lower()
    assert str(REPO_ROOT) not in serialized


@pytest.mark.parametrize("mutation", ["runtime_commit", "provenance_digest"])
def test_runtime_provenance_authority_rejects_nonexact_allowed_tuple(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from tui_gateway import maestro_authority

    manifest_path = (
        REPO_ROOT / "distribution" / distribution.PLUGIN_ID / "SOURCE_MANIFEST.json"
    ).resolve(strict=True)
    runtime_head = "1" * 40
    monkeypatch.setattr(launcher, "_runtime_head", lambda: runtime_head)
    candidate, digest = launcher._candidate_runtime_provenance(str(manifest_path))

    def nonexact_allow(context, _actual):
        returned_candidate = dict(candidate)
        returned_digest = digest
        if mutation == "runtime_commit":
            returned_candidate["runtimeCommit"] = "2" * 40
        else:
            returned_digest = "3" * 64
        return {
            "outcome": "allow",
            "decision_id": context["decision_binding"]["decision_id"],
            "consumed_once": True,
            "runtime_provenance_manifest": returned_candidate,
            "runtime_provenance_manifest_digest": returned_digest,
        }

    monkeypatch.setattr(
        maestro_authority, "consume_maestro_authority_decision", nonexact_allow
    )
    monkeypatch.setattr(
        launcher,
        "_load_authority_consumer",
        lambda _source_root: maestro_authority,
    )

    with pytest.raises(
        SystemExit, match="Hermes runtime provenance authority unavailable"
    ):
        launcher._consume_runtime_provenance_authority(str(manifest_path))


def test_signed_predecessor_deny_keeps_operational_launcher_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tui_gateway import maestro_authority

    manifest_path = (
        REPO_ROOT / "distribution" / distribution.PLUGIN_ID / "SOURCE_MANIFEST.json"
    ).resolve(strict=True)
    monkeypatch.setattr(launcher, "_runtime_head", lambda: "1" * 40)
    monkeypatch.setattr(
        maestro_authority,
        "consume_maestro_authority_decision",
        lambda _context, _actual: {"outcome": "deny", "code": "authority_denied"},
    )
    monkeypatch.setattr(
        launcher,
        "_load_authority_consumer",
        lambda _source_root: maestro_authority,
    )

    with pytest.raises(
        SystemExit, match="Hermes runtime provenance authority unavailable"
    ):
        launcher._consume_runtime_provenance_authority(str(manifest_path))


def test_runtime_provenance_consumer_digest_is_controller_pinned() -> None:
    consumer = REPO_ROOT / launcher.AUTHORITY_CONSUMER_PATH

    assert hashlib.sha256(consumer.read_bytes()).hexdigest() == (
        launcher.AUTHORITY_CONSUMER_SHA256
    )


def test_direct_source_launcher_cannot_bypass_runtime_provenance_authority(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [str(distribution.runtime_python()), "-I", str(LAUNCHER_PATH)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "Hermes runtime provenance authority unavailable\n"


def test_runtime_locator_rejects_wrong_source_revision(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    _rewrite_binding(
        bundle,
        lambda binding: binding.__setitem__("minimum_source_revision", "0" * 40),
    )

    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert completed.stderr == "runtime locator scalar binding drift\n"


@pytest.mark.parametrize(
    "field",
    [
        "authority_bundle_digest",
        "authority_source",
        "authority_source_revision",
        "authority_source_sha256",
    ],
)
def test_runtime_locator_rejects_wrong_authority_binding(
    tmp_path: Path, field: str
) -> None:
    _, bundle = _bundle(tmp_path)
    _rewrite_binding(bundle, lambda binding: binding.__setitem__(field, "0" * 64))

    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "runtime locator scalar binding drift\n"


def test_runtime_locator_rejects_runtime_content_digest_drift(
    tmp_path: Path,
) -> None:
    _, bundle = _bundle(tmp_path)

    def mutate(binding: dict) -> None:
        binding["runtime_files"][0]["sha256"] = "0" * 64
        binding["runtime_files_digest"] = distribution._runtime_files_digest(
            binding["runtime_files"]
        )

    _rewrite_binding(bundle, mutate)
    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert "runtime locator content digest drift" in completed.stderr


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "runtime locator admitted file set drift"),
        ("tampered", "runtime locator content digest drift"),
    ],
)
def test_runtime_locator_rejects_public_trust_anchor_drift_before_exec(
    tmp_path: Path, mutation: str, message: str
) -> None:
    _, bundle = _bundle(tmp_path)

    def mutate(binding: dict) -> None:
        anchor = next(
            entry
            for entry in binding["runtime_files"]
            if entry["path"] == "tui_gateway/maestro_authority_allowed_signers"
        )
        if mutation == "missing":
            anchor["path"] = "tui_gateway/missing_maestro_authority_allowed_signers"
        else:
            anchor["sha256"] = "0" * 64
        binding["runtime_files_digest"] = distribution._runtime_files_digest(
            binding["runtime_files"]
        )

    _rewrite_binding(bundle, mutate)
    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert message in completed.stderr


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "runtime locator admitted file set drift"),
        ("tampered", "runtime locator content digest drift"),
    ],
)
def test_runtime_locator_rejects_session_token_helper_drift_before_exec(
    tmp_path: Path, mutation: str, message: str
) -> None:
    _, bundle = _bundle(tmp_path)

    def mutate(binding: dict) -> None:
        helper = next(
            entry
            for entry in binding["runtime_files"]
            if entry["path"] == "scripts/orch_next_hermes_session_token_source.py"
        )
        if mutation == "missing":
            helper["path"] = "scripts/missing_orch_next_hermes_session_token_source.py"
        else:
            helper["sha256"] = "0" * 64
        binding["runtime_files_digest"] = distribution._runtime_files_digest(
            binding["runtime_files"]
        )

    _rewrite_binding(bundle, mutate)
    completed = subprocess.run(
        [*_wrapper_command(bundle), "--verify-origin"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert message in completed.stderr


def test_bundle_verification_fails_when_pinned_runtime_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    target = tmp_path / "bundle"
    monkeypatch.setattr(distribution, "RUNTIME_PYTHON_PATH", "missing-venv/python")
    with pytest.raises(
        distribution.DistributionError,
        match="portable Hermes runtime locator",
    ):
        distribution.transactional_install(source, target)


def test_bundle_verification_fails_when_pinned_launcher_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    target = tmp_path / "bundle"
    monkeypatch.setattr(
        distribution, "RUNTIME_LAUNCHER_PATH", "scripts/missing-launcher.py"
    )
    with pytest.raises(
        distribution.DistributionError,
        match="portable Hermes runtime locator",
    ):
        distribution.transactional_install(source, target)


def test_successive_syncs_are_byte_deterministic(tmp_path: Path) -> None:
    source, bundle = _bundle(tmp_path)

    def snapshot() -> dict[str, tuple[bytes, int]]:
        return {
            path.relative_to(bundle).as_posix(): (
                path.read_bytes(),
                path.stat().st_mode & 0o777,
            )
            for path in bundle.rglob("*")
            if path.is_file()
        }

    first = snapshot()
    distribution.transactional_install(source, bundle)
    assert snapshot() == first


def test_generated_python_cache_is_excluded_from_distribution(tmp_path: Path) -> None:
    source = _source_copy(tmp_path)
    cache = source / "demo-video-production" / "scripts" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "generated.cpython-313.pyc").write_bytes(b"generated-runtime-state")
    loose_cache = source / "demo-video-production" / "scripts" / "generated.pyc"
    loose_cache.write_bytes(b"generated-runtime-state")

    _, bundle = _bundle(tmp_path, source)

    assert not (bundle / "skills" / cache.relative_to(source)).exists()
    assert not (bundle / "skills" / loose_cache.relative_to(source)).exists()
    assert (
        distribution.verify_bundle(bundle, source, runtime_root=REPO_ROOT)["status"]
        == "verified"
    )


@pytest.mark.parametrize("mutation", ["extra", "missing", "drift"])
def test_verify_rejects_extra_missing_and_drifted_skill_files(
    tmp_path: Path, mutation: str
) -> None:
    _, bundle = _bundle(tmp_path)
    if mutation == "extra":
        (bundle / "skills" / "unexpected.txt").write_text("extra", encoding="utf-8")
    elif mutation == "missing":
        next((bundle / "skills").rglob("SKILL.md")).unlink()
    else:
        path = next((bundle / "skills").rglob("SKILL.md"))
        path.write_text(
            path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8"
        )
    with pytest.raises(distribution.DistributionError, match="skill mirror mismatch"):
        distribution.verify_bundle(bundle, runtime_root=REPO_ROOT)


def _installed_bundle(tmp_path: Path) -> Path:
    root = (
        tmp_path
        / distribution.MARKETPLACE_ID
        / distribution.PLUGIN_ID
        / distribution.PLUGIN_VERSION
    )
    distribution.transactional_install(REPO_ROOT / "skills" / "orch-next", root)
    return root


@pytest.mark.parametrize("active_marker", ["absent", "file", "directory"])
def test_installed_verifier_accepts_exact_admitted_closure(
    tmp_path: Path, active_marker: str
) -> None:
    installed = _installed_bundle(tmp_path)
    if active_marker == "file":
        (installed / ".in_use").write_text("host-owned marker\n", encoding="utf-8")
    elif active_marker == "directory":
        (installed / ".in_use").mkdir()

    result = distribution.verify_installed_bundle(installed)

    assert result["installed_cache"] is True
    assert result["version"] == distribution.PLUGIN_VERSION
    assert result["runtime_markers"] == (
        [] if active_marker == "absent" else [".in_use"]
    )
    assert result["skill_closure_digest"] == distribution.EXPECTED_SKILL_CLOSURE_DIGEST


def test_0116_install_preserves_019_rollback_directory(tmp_path: Path) -> None:
    marketplace = tmp_path / distribution.MARKETPLACE_ID / distribution.PLUGIN_ID
    prior = marketplace / "0.1.9"
    prior.mkdir(parents=True)
    rollback_marker = prior / "rollback-identity"
    rollback_marker.write_text("0.1.9-prior\n", encoding="utf-8")

    current = marketplace / distribution.PLUGIN_VERSION
    distribution.transactional_install(
        REPO_ROOT / "skills" / "orch-next",
        current,
    )

    assert distribution.PLUGIN_VERSION == "0.1.47"
    assert rollback_marker.read_text(encoding="utf-8") == "0.1.9-prior\n"
    assert current.is_dir()


def test_0117_distributed_dispatch_contract_keeps_adaptive_luna_pace() -> None:
    source_root = REPO_ROOT / "skills" / "orch-next"
    bundle_root = REPO_ROOT / "distribution" / "orch-next-hermes-harness" / "skills"
    required_markers = {
        "agent-dispatch": (
            "service_tier_preference=fast",
            "keep exact billed cost `UNKNOWN`",
            "Elapsed time\nalone cannot stop productive work",
        ),
        "heartbeat-cmd-control-guard": (
            "The CMD response checkpoint is not the worker deadline",
            "Count only a strategy, hypothesis, target",
        ),
        "priority-action-router": (
            "Treat every meaningful-delta time as a review point",
            "patch/test syntax, path resolution, receipt metadata",
        ),
        "product-build-fast-lane": (
            "These are review points, not elapsed-only",
            "productive work with a real material delta continues",
        ),
    }

    for skill_name, markers in required_markers.items():
        source_text = (source_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
        bundle_text = (bundle_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert bundle_text == source_text
        for marker in markers:
            assert marker in bundle_text


def test_installed_verifier_rejects_orphaned_cache(tmp_path: Path) -> None:
    installed = _installed_bundle(tmp_path)
    (installed / ".orphaned_at").write_text("retired\n", encoding="utf-8")

    with pytest.raises(
        distribution.DistributionError, match="installed bundle is orphaned"
    ):
        distribution.verify_installed_bundle(installed)


def test_installed_verifier_rejects_same_version_content_drift(tmp_path: Path) -> None:
    installed = _installed_bundle(tmp_path)
    manifest = json.loads((installed / "SOURCE_MANIFEST.json").read_text())
    assert manifest["version"] == distribution.PLUGIN_VERSION
    path = installed / "skills" / "environment-ground-truth" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")

    with pytest.raises(distribution.DistributionError, match="skill mirror mismatch"):
        distribution.verify_installed_bundle(installed)


@pytest.mark.parametrize(
    "identity",
    ["upstream", "runtime", "profile", "authority"],
)
def test_same_version_different_identity_digest_fails_closed(
    tmp_path: Path, identity: str
) -> None:
    _, installed = _bundle(tmp_path)
    manifest_path = installed / "SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if identity == "upstream":
        manifest["hermes_agent_runtime"]["upstream_commit"] = "0" * 40
    elif identity == "runtime":
        manifest["mcp"]["locator"]["binding_sha256"] = "0" * 64
    elif identity == "profile":
        manifest["operational_profile"]["profile_digest"] = "0" * 64
    else:
        manifest["operational_profile"]["authority_bundle"]["digest"] = "0" * 64
    manifest_path.write_bytes(distribution._json_bytes(manifest))

    with pytest.raises(distribution.DistributionError, match="source manifest drift"):
        distribution.verify_bundle(
            installed,
            REPO_ROOT / "skills" / "orch-next",
            runtime_root=REPO_ROOT,
        )


def test_installed_verifier_rejects_wrong_version_path(tmp_path: Path) -> None:
    installed = _installed_bundle(tmp_path)
    wrong_version = installed.with_name("0.1.2")
    installed.rename(wrong_version)

    with pytest.raises(
        distribution.DistributionError, match="installed bundle version path mismatch"
    ):
        distribution.verify_installed_bundle(wrong_version)


def test_verify_rejects_quarantined_skill_inclusion(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    quarantined = bundle / "skills" / "fable5-os-durable-user-value-goal"
    quarantined.mkdir()
    (quarantined / "SKILL.md").write_text(
        "---\nname: forbidden\n---\n", encoding="utf-8"
    )
    with pytest.raises(distribution.DistributionError, match="quarantined skill"):
        distribution.verify_bundle(bundle, runtime_root=REPO_ROOT)


def test_verify_rejects_wrong_mcp_server(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    mcp_path = bundle / ".mcp.json"
    mcp = json.loads(mcp_path.read_text())
    mcp["mcpServers"][distribution.PLUGIN_ID]["command"] = "maestro.runtime"
    mcp_path.write_text(json.dumps(mcp), encoding="utf-8")
    with pytest.raises(distribution.DistributionError, match="MCP manifest drift"):
        distribution.verify_bundle(bundle, runtime_root=REPO_ROOT)


def test_verify_rejects_execution_fallback(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    manifest_path = bundle / "SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["mcp"]["maestro_execution_fallback_allowed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(distribution.DistributionError, match="source manifest drift"):
        distribution.verify_bundle(bundle, runtime_root=REPO_ROOT)


def test_verify_rejects_declared_path_escape(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)
    manifest_path = bundle / "SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["channels"]["claude"]["skills"] = "../outside"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(distribution.DistributionError, match="path escape"):
        distribution.verify_bundle(bundle, runtime_root=REPO_ROOT)


def test_verify_rejects_source_and_bundle_symlinks(tmp_path: Path) -> None:
    source, bundle = _bundle(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    os.symlink(outside, bundle / "skills" / "linked")
    with pytest.raises(distribution.DistributionError, match="symlink"):
        distribution.verify_bundle(bundle, source, runtime_root=REPO_ROOT)

    bundle_link = tmp_path / "bundle-link"
    os.symlink(bundle, bundle_link)
    with pytest.raises(distribution.DistributionError, match="symlink"):
        distribution.verify_bundle(bundle_link, source, runtime_root=REPO_ROOT)


def test_sync_rejects_symlinked_source(tmp_path: Path) -> None:
    source = _source_copy(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    os.symlink(outside, source / "action-first-operator-communication" / "escape")
    with pytest.raises(distribution.DistributionError, match="symlink"):
        distribution.transactional_install(source, tmp_path / "bundle")


def test_failed_successor_handoff_preserves_prior_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = _bundle(tmp_path)
    marker = target / "prior-marker"
    marker.write_text("prior", encoding="utf-8")
    original = distribution._atomic_replace
    failed = False

    def fail_successor_once(source_path: Path, destination_path: Path) -> None:
        nonlocal failed
        if not failed and ".stage-" in source_path.name and destination_path == target:
            failed = True
            raise OSError("injected successor handoff failure")
        original(source_path, destination_path)

    monkeypatch.setattr(distribution, "_atomic_replace", fail_successor_once)
    with pytest.raises(OSError, match="injected successor handoff failure"):
        distribution.transactional_install(source, target)

    assert marker.read_text(encoding="utf-8") == "prior"
    assert not list(tmp_path.glob(".bundle.stage-*"))
    assert not list(tmp_path.glob(".bundle.rollback-*"))


def test_post_handoff_verification_failure_rolls_back_prior_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = _bundle(tmp_path)
    marker = target / "prior-marker"
    marker.write_text("prior", encoding="utf-8")
    prior_binding = (target / distribution.RUNTIME_BINDING_PATH).read_bytes()
    prior_manifest = (target / "SOURCE_MANIFEST.json").read_bytes()
    original = distribution.verify_bundle
    target_verification_calls = 0

    def fail_published_target(
        bundle_root: Path,
        source_skills: Path | None = None,
        **kwargs,
    ):
        nonlocal target_verification_calls
        if bundle_root == target:
            target_verification_calls += 1
            if target_verification_calls == 1:
                raise distribution.DistributionError(
                    "injected final verification failure"
                )
        return original(bundle_root, source_skills, **kwargs)

    monkeypatch.setattr(distribution, "verify_bundle", fail_published_target)
    with pytest.raises(distribution.DistributionError, match="injected final"):
        distribution.transactional_install(source, target)

    assert marker.read_text(encoding="utf-8") == "prior"
    assert (target / distribution.RUNTIME_BINDING_PATH).read_bytes() == prior_binding
    assert (target / "SOURCE_MANIFEST.json").read_bytes() == prior_manifest
    assert not list(tmp_path.glob(".bundle.failed-*"))
    assert not list(tmp_path.glob(".bundle.rollback-*"))


def test_concurrent_writer_lock_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "bundle"
    lock = tmp_path / ".bundle.distribution.lock"
    lock.write_text("held", encoding="utf-8")
    with pytest.raises(
        distribution.DistributionError, match="another distribution writer"
    ):
        distribution.transactional_sync(REPO_ROOT / "skills" / "orch-next", target)


def test_sync_rejects_existing_non_directory_target(tmp_path: Path) -> None:
    target = tmp_path / "bundle"
    target.write_text("not a bundle", encoding="utf-8")
    with pytest.raises(distribution.DistributionError, match="must be a directory"):
        distribution.transactional_sync(REPO_ROOT / "skills" / "orch-next", target)
    assert target.read_text(encoding="utf-8") == "not a bundle"


def _skill_tree(root: Path, name: str, content: str = "skill\n") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(content, encoding="utf-8")
    return skill


def test_unprefixed_collision_inventory_is_sorted_and_channel_specific(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    _skill_tree(canonical, "zeta")
    _skill_tree(canonical, "alpha")
    claude = tmp_path / "claude"
    _skill_tree(claude, "zeta", "legacy zeta\n")
    _skill_tree(claude, "unrelated")
    codex = tmp_path / "codex"
    _skill_tree(codex, "alpha", "legacy alpha\n")

    inventory = distribution.inventory_unprefixed_skill_collisions(
        canonical,
        {"claude": claude, "codex": codex},
    )

    assert inventory == {
        "canonical_skill_count": 2,
        "channels": {
            "claude": {
                "active_root": str(claude),
                "collision_count": 1,
                "collisions": ["zeta"],
            },
            "codex": {
                "active_root": str(codex),
                "collision_count": 1,
                "collisions": ["alpha"],
            },
        },
        "status": "active_collisions",
        "total_active_collision_count": 2,
    }


def test_unprefixed_collision_inventory_rejects_symlink_alias(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    _skill_tree(canonical, "alpha")
    actual = tmp_path / "actual"
    _skill_tree(actual, "alpha")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    with pytest.raises(distribution.DistributionError, match="must not be a symlink"):
        distribution.inventory_unprefixed_skill_collisions(
            canonical,
            {"claude": alias},
        )


def test_live_consumer_classifier_does_not_misclassify_plain_codex_worker(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "orch-next-codex-harness" / "0.1.2"
    legacy_root.mkdir(parents=True)

    classified = distribution.classify_live_consumer_processes(
        [
            {
                "executable": "/Applications/ChatGPT.app/Contents/Resources/codex",
                "argv": ["codex", "exec", "--json"],
                "parent_launcher": "codex-app-server",
            }
        ],
        legacy_roots=[legacy_root],
    )

    assert classified == {
        "active_legacy_consumer_count": 0,
        "active_legacy_consumers": [],
        "observed_codex_worker_count": 1,
        "status": "no_live_legacy_consumer",
    }


@pytest.mark.parametrize("binding_field", ["argv", "parent_launcher"])
def test_live_consumer_classifier_rejects_proven_legacy_path_binding(
    tmp_path: Path, binding_field: str
) -> None:
    legacy_root = tmp_path / "orch-next-codex-harness" / "0.1.2"
    launcher = legacy_root / "scripts" / "legacy-launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("legacy\n", encoding="utf-8")
    process: dict[str, object] = {
        "executable": "/Applications/ChatGPT.app/Contents/Resources/codex",
        "argv": ["codex", "exec", "--json"],
        "parent_launcher": "codex-app-server",
    }
    if binding_field == "argv":
        argv = process["argv"]
        assert isinstance(argv, list)
        argv.append(str(launcher))
    else:
        process["parent_launcher"] = str(launcher)

    classified = distribution.classify_live_consumer_processes(
        [process], legacy_roots=[legacy_root]
    )

    assert classified["active_legacy_consumer_count"] == 1
    assert classified["active_legacy_consumers"] == [
        {"binding": binding_field, "consumer": "legacy_direct_dispatch"}
    ]
    assert "legacy" in classified["status"]


@pytest.mark.parametrize("binding_field", ["executable", "parent_launcher", "argv"])
def test_live_consumer_classifier_detects_symlink_alias_into_legacy_root(
    tmp_path: Path, binding_field: str
) -> None:
    legacy_root = tmp_path / "orch-next-codex-harness" / "0.1.2"
    launcher = legacy_root / "scripts" / "legacy-launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("legacy\n", encoding="utf-8")
    alias = tmp_path / "legacy-alias"
    alias.symlink_to(legacy_root, target_is_directory=True)

    alias_launcher = str(alias / "scripts/legacy-launcher.py")
    process = {
        "executable": "/Applications/ChatGPT.app/Contents/Resources/codex",
        "argv": ["codex", "exec", "--json"],
        "parent_launcher": "codex-app-server",
    }
    if binding_field == "argv":
        process["argv"].append(alias_launcher)
    else:
        process[binding_field] = alias_launcher

    classified = distribution.classify_live_consumer_processes(
        [process], legacy_roots=[legacy_root]
    )

    assert classified["active_legacy_consumers"] == [
        {"binding": binding_field, "consumer": "legacy_direct_dispatch"}
    ]


def test_legacy_cache_classification_binds_registry_path_and_tree_digest(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "orch-next-local" / distribution.LEGACY_PLUGIN_ID
    old = _skill_tree(cache_root / "0.1.2", "skills", "legacy cache\n").parent
    active = _skill_tree(cache_root / "0.1.3", "skills", "active cache\n").parent

    classified = distribution.classify_legacy_plugin_caches(
        channel="claude",
        legacy_cache_root=cache_root,
        registry_observation={
            "identity": distribution.LEGACY_PLUGIN_ID,
            "source": "claude_installed_plugins",
            "state": "enabled",
        },
        active_install_paths=[active],
    )

    assert classified["active_cache_count"] == 1
    assert classified["rollback_only_cache_count"] == 1
    assert classified["registry_observation"] == {
        "identity": distribution.LEGACY_PLUGIN_ID,
        "source": "claude_installed_plugins",
        "state": "enabled",
    }
    assert classified["caches"] == [
        {
            "cache_path": str(old),
            "content_digest": distribution._all_file_tree_identity(old)[1],
            "file_count": 1,
            "registry_referenced": False,
            "state": "rollback_only",
            "version": "0.1.2",
        },
        {
            "cache_path": str(active),
            "content_digest": distribution._all_file_tree_identity(active)[1],
            "file_count": 1,
            "registry_referenced": True,
            "state": "active",
            "version": "0.1.3",
        },
    ]


@pytest.mark.parametrize(
    "observation",
    [
        None,
        {},
        {
            "identity": distribution.LEGACY_PLUGIN_ID,
            "source": "claude_installed_plugins",
            "state": "unknown",
        },
        {
            "identity": "wrong-plugin",
            "source": "claude_installed_plugins",
            "state": "absent",
        },
        {
            "identity": distribution.LEGACY_PLUGIN_ID,
            "source": "caller_prose",
            "state": "absent",
        },
    ],
)
def test_legacy_cache_classification_rejects_unadmitted_registry_observation(
    tmp_path: Path, observation: object
) -> None:
    cache_root = tmp_path / "orch-next-local" / distribution.LEGACY_PLUGIN_ID
    _skill_tree(cache_root / "0.1.2", "skills", "legacy cache\n")

    with pytest.raises(distribution.DistributionError, match="registry observation"):
        distribution.classify_legacy_plugin_caches(
            channel="claude",
            legacy_cache_root=cache_root,
            registry_observation=observation,
            active_install_paths=[],
        )


def test_absent_registry_observation_cannot_hide_active_install_path(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "orch-next-local" / distribution.LEGACY_PLUGIN_ID
    active = _skill_tree(cache_root / "0.1.2", "skills", "legacy cache\n").parent

    with pytest.raises(
        distribution.DistributionError, match="inactive registry observation"
    ):
        distribution.classify_legacy_plugin_caches(
            channel="claude",
            legacy_cache_root=cache_root,
            registry_observation={
                "identity": distribution.LEGACY_PLUGIN_ID,
                "source": "claude_installed_plugins",
                "state": "absent",
            },
            active_install_paths=[active],
        )


def test_claude_unprefixed_quarantine_round_trip_is_digest_bound(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    _skill_tree(canonical, "alpha")
    active = tmp_path / "claude-skills"
    source = _skill_tree(active, "alpha", "legacy alpha\n")
    (source / "nested").mkdir()
    (source / "nested" / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    quarantine = tmp_path / "quarantine"
    rollback_record = quarantine / "ROLLBACK_MAP.json"

    plan = distribution.plan_claude_unprefixed_quarantine(
        canonical,
        active,
        quarantine,
        rollback_record,
    )
    assert plan["collision_count"] == 1
    assert plan["entries"][0]["file_count"] == 2
    assert len(plan["entries"][0]["tree_digest"]) == 64

    moved = distribution.execute_claude_unprefixed_quarantine(plan)
    assert moved["status"] == "quarantined"
    assert not source.exists()
    destination = quarantine / "alpha"
    assert destination.is_dir()
    record = json.loads(rollback_record.read_text(encoding="utf-8"))
    assert record["entries"] == plan["entries"]

    restored = distribution.rollback_claude_unprefixed_quarantine(
        rollback_record,
        active_root=active,
        quarantine_root=quarantine,
    )
    assert restored["status"] == "rolled_back"
    assert source.is_dir()
    assert not destination.exists()
    assert rollback_record.is_file()


@pytest.mark.parametrize("hazard", ["source_symlink", "destination_present"])
def test_claude_unprefixed_quarantine_fails_closed_without_overwrite(
    tmp_path: Path, hazard: str
) -> None:
    canonical = tmp_path / "canonical"
    _skill_tree(canonical, "alpha")
    active = tmp_path / "claude-skills"
    if hazard == "source_symlink":
        real = _skill_tree(tmp_path / "real", "alpha")
        active.mkdir()
        (active / "alpha").symlink_to(real, target_is_directory=True)
    else:
        _skill_tree(active, "alpha")
    quarantine = tmp_path / "quarantine"
    if hazard == "destination_present":
        _skill_tree(quarantine, "alpha", "do not overwrite\n")

    with pytest.raises(distribution.DistributionError):
        distribution.plan_claude_unprefixed_quarantine(
            canonical,
            active,
            quarantine,
            quarantine / "ROLLBACK_MAP.json",
        )
    if hazard == "destination_present":
        assert (quarantine / "alpha" / "SKILL.md").read_text() == ("do not overwrite\n")


def test_claude_unprefixed_quarantine_rejects_second_writer(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    _skill_tree(canonical, "alpha")
    active = tmp_path / "claude-skills"
    _skill_tree(active, "alpha")
    quarantine = tmp_path / "quarantine"
    plan = distribution.plan_claude_unprefixed_quarantine(
        canonical,
        active,
        quarantine,
        quarantine / "ROLLBACK_MAP.json",
    )
    lock = tmp_path / ".quarantine.distribution.lock"
    lock.write_text("owned\n", encoding="utf-8")

    with pytest.raises(
        distribution.DistributionError, match="another distribution writer"
    ):
        distribution.execute_claude_unprefixed_quarantine(plan)
    assert (active / "alpha" / "SKILL.md").is_file()
    assert not quarantine.exists()


def _two_skill_quarantine(tmp_path: Path) -> tuple[dict, Path, Path]:
    canonical = tmp_path / "canonical"
    active = tmp_path / "claude-skills"
    for name in ("alpha", "beta"):
        _skill_tree(canonical, name)
        _skill_tree(active, name, f"legacy {name}\n")
    quarantine = tmp_path / "quarantine"
    plan = distribution.plan_claude_unprefixed_quarantine(
        canonical,
        active,
        quarantine,
        quarantine / "ROLLBACK_MAP.json",
    )
    return plan, active, quarantine


def test_claude_quarantine_rejects_tampered_subset_plan(tmp_path: Path) -> None:
    plan, active, quarantine = _two_skill_quarantine(tmp_path)
    plan["entries"] = plan["entries"][:1]
    plan["collision_count"] = 1

    with pytest.raises(distribution.DistributionError, match="plan drift"):
        distribution.execute_claude_unprefixed_quarantine(plan)
    assert (active / "alpha").is_dir()
    assert (active / "beta").is_dir()
    assert not quarantine.exists()


def test_claude_quarantine_resumes_after_partial_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, active, quarantine = _two_skill_quarantine(tmp_path)
    original = distribution._atomic_replace
    calls = 0

    def crash_after_first(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SystemExit("injected crash")
        original(source, destination)

    monkeypatch.setattr(distribution, "_atomic_replace", crash_after_first)
    with pytest.raises(SystemExit, match="injected crash"):
        distribution.execute_claude_unprefixed_quarantine(plan)
    assert (quarantine / "ROLLBACK_MAP.json").is_file()
    assert sum((active / name).is_dir() for name in ("alpha", "beta")) == 1
    assert sum((quarantine / name).is_dir() for name in ("alpha", "beta")) == 1

    monkeypatch.setattr(distribution, "_atomic_replace", original)
    resumed = distribution.execute_claude_unprefixed_quarantine(plan)
    assert resumed["status"] == "quarantined"
    assert resumed["already_moved_count"] == 1
    assert resumed["moved_count"] == 1


def test_claude_quarantine_rollback_resumes_after_mid_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, active, quarantine = _two_skill_quarantine(tmp_path)
    distribution.execute_claude_unprefixed_quarantine(plan)
    original = distribution._atomic_replace
    calls = 0

    def fail_second_restore(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected rollback interruption")
        original(source, destination)

    monkeypatch.setattr(distribution, "_atomic_replace", fail_second_restore)
    with pytest.raises(OSError, match="rollback interruption"):
        distribution.rollback_claude_unprefixed_quarantine(
            quarantine / "ROLLBACK_MAP.json",
            active_root=active,
            quarantine_root=quarantine,
        )
    monkeypatch.setattr(distribution, "_atomic_replace", original)

    restored = distribution.rollback_claude_unprefixed_quarantine(
        quarantine / "ROLLBACK_MAP.json",
        active_root=active,
        quarantine_root=quarantine,
    )
    assert restored["status"] == "rolled_back"
    assert restored["already_restored_count"] == 1
    assert restored["restored_count"] == 1


@pytest.mark.parametrize("hazard", ["both", "neither", "drift"])
def test_claude_quarantine_resume_rejects_ambiguous_or_drifted_state(
    tmp_path: Path, hazard: str
) -> None:
    plan, active, quarantine = _two_skill_quarantine(tmp_path)
    distribution.execute_claude_unprefixed_quarantine(plan)
    source = active / "alpha"
    destination = quarantine / "alpha"
    if hazard == "both":
        _skill_tree(active, "alpha", "duplicate\n")
    elif hazard == "neither":
        shutil.rmtree(destination)
    else:
        (destination / "SKILL.md").write_text("drift\n", encoding="utf-8")

    with pytest.raises(distribution.DistributionError, match="quarantine entry"):
        distribution.execute_claude_unprefixed_quarantine(plan)


def _zero_consumer_inputs() -> dict:
    return {
        "inventory": {
            "total_active_collision_count": 0,
            "channels": {},
        },
        "plugin_classifications": [
            {
                "active_cache_count": 0,
                "caches": [
                    {
                        "cache_path": "/cache/codex/0.1.2",
                        "content_digest": "c" * 64,
                        "file_count": 1,
                        "registry_referenced": False,
                        "state": "rollback_only",
                        "version": "0.1.2",
                    }
                ],
                "channel": "codex",
                "identity": distribution.LEGACY_PLUGIN_ID,
                "registry_observation": {
                    "identity": distribution.LEGACY_PLUGIN_ID,
                    "source": "codex_config",
                    "state": "disabled",
                },
                "rollback_only_cache_count": 1,
            },
            {
                "active_cache_count": 0,
                "caches": [],
                "channel": "claude",
                "identity": distribution.LEGACY_PLUGIN_ID,
                "registry_observation": {
                    "identity": distribution.LEGACY_PLUGIN_ID,
                    "source": "claude_installed_plugins",
                    "state": "absent",
                },
                "rollback_only_cache_count": 0,
            },
        ],
        "process_classification": {
            "active_legacy_consumer_count": 0,
            "active_legacy_consumers": [],
        },
        "quarantine_records": [],
        "quarantined_skill_names": [],
        "installed_identity_records": [
            {
                "active": True,
                "content_digest": "a" * 64,
                "expected_content_digest": "a" * 64,
                "identity": distribution.PLUGIN_ID,
                "version": distribution.PLUGIN_VERSION,
            }
        ],
    }


def test_zero_consumer_verifier_accepts_disabled_rollback_only_cache() -> None:
    result = distribution.verify_zero_legacy_consumers(**_zero_consumer_inputs())
    assert result == {
        "active_collision_count": 0,
        "active_legacy_consumer_count": 0,
        "legacy_plugin_enabled": False,
        "rollback_only_cache_count": 1,
        "status": "zero_legacy_consumers_verified",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("active_collision", "active unprefixed skill collision"),
        ("enabled_plugin", "legacy plugin remains enabled"),
        ("unknown_registry", "registry observation"),
        ("legacy_process", "live legacy direct dispatch"),
        ("missing_rollback", "quarantined skill missing rollback record"),
        ("same_semver_drift", "same-semver installed content drift"),
    ],
)
def test_zero_consumer_verifier_fails_closed(mutation: str, message: str) -> None:
    inputs = _zero_consumer_inputs()
    if mutation == "active_collision":
        inputs["inventory"]["total_active_collision_count"] = 1
    elif mutation == "enabled_plugin":
        inputs["plugin_classifications"][0]["registry_observation"]["state"] = "enabled"
    elif mutation == "unknown_registry":
        inputs["plugin_classifications"][0]["registry_observation"]["state"] = "unknown"
    elif mutation == "legacy_process":
        inputs["process_classification"]["active_legacy_consumer_count"] = 1
    elif mutation == "missing_rollback":
        inputs["quarantined_skill_names"] = ["alpha"]
    else:
        inputs["installed_identity_records"][0]["content_digest"] = "b" * 64

    with pytest.raises(distribution.DistributionError, match=message):
        distribution.verify_zero_legacy_consumers(**inputs)
