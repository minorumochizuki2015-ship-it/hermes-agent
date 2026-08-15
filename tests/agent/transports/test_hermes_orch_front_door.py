"""Tests for the credential-free Hermes operational WebSocket front door."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable

import pytest

from agent.transports.hermes_orch_front_door import (
    HERMES_FRONT_DOOR_UNAVAILABLE,
    HERMES_ORCH_FRONT_DOOR_URL,
    ORCH_FRONT_DOOR_TOOLS,
    HermesOrchFrontDoor,
    _MAX_FRAME_BYTES,
    serialize_front_door_result,
)


def _ready() -> str:
    return json.dumps({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "gateway.ready", "payload": {}},
    })


def _response(request_id: str, **payload) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, **payload})


class FakeWebSocket:
    def __init__(self, frames: Iterable[object]):
        self.frames = list(frames)
        self.sent: list[str] = []
        self.recv_timeouts: list[float] = []
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def recv(self, *, timeout: float):
        self.recv_timeouts.append(timeout)
        if not self.frames:
            raise TimeoutError("fixture timeout")
        frame = self.frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        return frame

    def send(self, payload: str):
        self.sent.append(payload)


class FakeConnector:
    def __init__(self, websocket: FakeWebSocket | None = None, error=None):
        self.websocket = websocket
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, uri: str, **kwargs):
        self.calls.append((uri, kwargs))
        if self.error is not None:
            raise self.error
        assert self.websocket is not None
        return self.websocket


def _client(frames: Iterable[object]):
    websocket = FakeWebSocket(frames)
    connector = FakeConnector(websocket)
    client = HermesOrchFrontDoor(
        connect_fn=connector,
        token_resolver=lambda: "a" * 64,
        id_factory=lambda: "request-1",
        monotonic=lambda: 100.0,
    )
    return client, connector, websocket


def _assert_unavailable(result: dict) -> None:
    assert result == {
        "error": {
            "code": HERMES_FRONT_DOOR_UNAVAILABLE,
            "message": "Hermes front door unavailable",
        }
    }


def test_tool_projection_is_exactly_the_seven_existing_gateway_methods():
    assert [(item["name"], item["method"]) for item in ORCH_FRONT_DOOR_TOOLS] == [
        ("orch_session_create", "session.create"),
        ("orch_prompt_submit", "prompt.submit"),
        ("orch_session_status", "session.status"),
        ("orch_session_interrupt", "session.interrupt"),
        ("orch_session_stop", "session.stop"),
        ("orch_session_resume", "session.resume"),
        ("orch_session_reconnect", "session.reconnect"),
    ]

    expected_gateway_parameters = {
        "session.create": {
            "cols",
            "messages",
            "title",
            "parent_session_id",
            "cwd",
            "source",
            "profile",
            "model",
            "provider",
            "reasoning_effort",
            "fast",
            "close_on_disconnect",
        },
        "prompt.submit": {
            "session_id",
            "text",
            "operational_class",
            "operational_context",
            "sdo_decision_receipt",
            "sdo_source_binding",
            "sdo_candidate_action_ids",
            "sdo_producer_input",
            "queued",
            "interrupted",
            "truncate_before_user_ordinal",
            "confirm_empty_truncate",
        },
        "session.status": {"session_id"},
        "session.interrupt": {"session_id"},
        "session.stop": {
            "session_id",
            "include_quiescence_receipt",
            "terminal_transition",
        },
        "session.resume": {
            "session_id",
            "cols",
            "profile",
            "lazy",
            "source",
            "close_on_disconnect",
            "eager_build",
        },
        "session.reconnect": {"session_id", "profile"},
    }
    for tool in ORCH_FRONT_DOOR_TOOLS:
        schema = tool["input_schema"]
        assert set(schema["properties"]) == expected_gateway_parameters[tool["method"]]
        assert schema["additionalProperties"] is False

    prompt = next(
        item for item in ORCH_FRONT_DOOR_TOOLS if item["method"] == "prompt.submit"
    )
    assert prompt["input_schema"]["required"] == ["session_id", "text"]
    assert prompt["input_schema"]["properties"]["operational_class"] == {
        "type": "string",
        "enum": ["ordinary", "orch"],
    }
    assert prompt["input_schema"]["properties"]["operational_context"] == {
        "type": "object"
    }


def test_prompt_schema_admits_exact_discriminator_and_rejects_unknown_keys():
    from agent.transports.hermes_tools_mcp_server import _signature_from_schema

    prompt = next(
        item for item in ORCH_FRONT_DOOR_TOOLS if item["method"] == "prompt.submit"
    )
    signature, _ = _signature_from_schema(prompt["input_schema"])

    signature.bind(
        session_id="s1",
        text="continue",
        operational_class="orch",
        operational_context={"schema": "hermes-operational-context.v1"},
    )
    with pytest.raises(TypeError):
        signature.bind(
            session_id="s1",
            text="continue",
            operation_class="orch",
        )


def test_one_correlated_request_uses_authenticated_loopback():
    client, connector, websocket = _client([
        _ready(),
        _response("request-1", result={"session_id": "s1"}),
    ])

    result = client.request("session.create", {"cwd": "/repo"})

    assert result == {"result": {"session_id": "s1"}}
    assert len(connector.calls) == 1
    uri, options = connector.calls[0]
    assert uri == HERMES_ORCH_FRONT_DOOR_URL
    assert "a" * 64 not in uri
    assert options == {
        "open_timeout": 2.0,
        "close_timeout": 1.0,
        "max_size": _MAX_FRAME_BYTES,
        "compression": None,
        "proxy": None,
        "additional_headers": {"X-Hermes-Session-Token": "a" * 64},
    }
    assert websocket.enter_count == 1
    assert len(websocket.sent) == 1
    assert json.loads(websocket.sent[0]) == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "method": "session.create",
        "params": {"cwd": "/repo"},
    }


def test_front_door_url_is_fixed_to_the_sdo_sidecar_port():
    assert HERMES_ORCH_FRONT_DOOR_URL == "ws://127.0.0.1:3518/api/ws"


@pytest.mark.parametrize("resolved", [None, "", "not-a-token", "A" * 64])
def test_missing_or_malformed_credential_is_typed_unavailable_before_connect(
    resolved,
):
    connector = FakeConnector(error=AssertionError("must not connect"))
    client = HermesOrchFrontDoor(
        connect_fn=connector,
        token_resolver=lambda: resolved,
        id_factory=lambda: "request-1",
    )

    _assert_unavailable(client.request("session.status", {"session_id": "s1"}))
    assert connector.calls == []


def test_real_fastapi_gateway_auth_accepts_the_resolved_loopback_token(
    monkeypatch,
):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from hermes_cli import web_server

    token = "a" * 64
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", token)
    monkeypatch.setattr(web_server, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True)
    # Starlette's in-process peer is named ``testclient`` rather than carrying
    # a loopback IP. Keep the production auth route real while isolating this
    # test from the separately covered host/peer admission guard.
    monkeypatch.setattr(web_server, "_ws_request_is_allowed", lambda _ws: True)
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = "127.0.0.1"
    web_server.app.state.bound_port = 3517

    client = TestClient(web_server.app, base_url="http://127.0.0.1:3517")
    try:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("/api/ws"):
                pass
        assert rejected.value.code == 4401

        with client.websocket_connect(
            "/api/ws", headers={"X-Hermes-Session-Token": token}
        ) as websocket:
            assert _is_ready_for_test(websocket.receive_json())

        # Browser/short-lived clients keep the legacy query form, but the
        # server must refuse ambiguous mixed credentials before comparison.
        with client.websocket_connect(f"/api/ws?token={token}") as websocket:
            assert _is_ready_for_test(websocket.receive_json())

        with pytest.raises(WebSocketDisconnect) as ambiguous:
            with client.websocket_connect(
                f"/api/ws?token={token}",
                headers={"X-Hermes-Session-Token": token},
            ):
                pass
        assert ambiguous.value.code == 4401
    finally:
        client.close()


def test_loopback_ws_auth_rejects_repeated_header_or_query_credentials(monkeypatch):
    """Framework accessor convenience must not collapse repeated credentials."""

    from hermes_cli import web_server

    token = "a" * 64

    class Values:
        def __init__(self, values):
            self._values = values

        def getlist(self, _name):
            return list(self._values)

    class WebSocketFixture:
        def __init__(self, query_values, header_values):
            self.query_params = Values(query_values)
            self.headers = Values(header_values)

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", token)
    web_server.app.state.auth_required = False

    assert web_server._ws_auth_reason(
        WebSocketFixture([token, token], [])
    ) == ("ambiguous_credential", "ambiguous")
    assert web_server._ws_auth_reason(
        WebSocketFixture([], [token, token])
    ) == ("ambiguous_credential", "ambiguous")


@pytest.mark.parametrize(
    "query_values,header_values",
    [
        ({"internal": ["i"], "ticket": ["t"]}, []),
        ({"ticket": ["t"], "token": ["q"]}, []),
        ({"internal": ["i"]}, ["h"]),
    ],
)
def test_gated_ws_auth_rejects_mixed_mechanisms_before_precedence(
    monkeypatch,
    query_values,
    header_values,
):
    """Internal/ticket precedence never consumes a request with another key."""

    from hermes_cli import web_server

    class Values:
        def __init__(self, values):
            self._values = values

        def getlist(self, name):
            return list(self._values.get(name, []))

    class WebSocketFixture:
        def __init__(self):
            self.query_params = Values(query_values)
            self.headers = Values({"X-Hermes-Session-Token": header_values})

    monkeypatch.setattr(web_server.app.state, "auth_required", True)
    assert web_server._ws_auth_reason(WebSocketFixture()) == (
        "ambiguous_credential",
        "ambiguous",
    )


def _is_ready_for_test(frame: dict) -> bool:
    return (
        frame.get("jsonrpc") == "2.0"
        and frame.get("method") == "event"
        and frame.get("params", {}).get("type") == "gateway.ready"
    )


def test_real_in_process_websocket_exercises_ready_and_jsonrpc_correlation():
    from websockets.sync.client import connect
    from websockets.sync.server import serve

    received = []

    def handler(websocket):
        websocket.send(_ready())
        request = json.loads(websocket.recv(timeout=2.0))
        received.append(request)
        websocket.send(_response(request["id"], result={"session_id": "live-session"}))

    with serve(handler, "127.0.0.1", 0, compression=None) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def test_connector(_production_uri, **kwargs):
            return connect(f"ws://127.0.0.1:{port}", **kwargs)

        try:
            client = HermesOrchFrontDoor(
                connect_fn=test_connector,
                token_resolver=lambda: "a" * 64,
                id_factory=lambda: "real-request",
            )
            result = client.request("session.create", {"source": "mcp"})
        finally:
            server.shutdown()
            thread.join(timeout=2.0)

    assert result == {"result": {"session_id": "live-session"}}
    assert received == [
        {
            "jsonrpc": "2.0",
            "id": "real-request",
            "method": "session.create",
            "params": {"source": "mcp"},
        }
    ]


def test_operational_context_is_forwarded_unchanged_for_gateway_authority():
    context = {
        "schema": "hermes-operational-context.v1",
        "decision_binding": {"method": "prompt.submit", "target": "s1"},
    }
    client, _, websocket = _client([
        _ready(),
        _response("request-1", result={"status": "streaming"}),
    ])

    result = client.request(
        "prompt.submit",
        {
            "session_id": "s1",
            "text": "continue",
            "operational_class": "orch",
            "operational_context": context,
        },
    )

    assert result == {"result": {"status": "streaming"}}
    sent = json.loads(websocket.sent[0])
    assert sent["params"] == {
        "session_id": "s1",
        "text": "continue",
        "operational_class": "orch",
        "operational_context": context,
    }


def test_ordinary_submit_does_not_manufacture_protected_context():
    client, _, websocket = _client([
        _ready(),
        _response("request-1", result={"status": "streaming"}),
    ])

    client.request("prompt.submit", {"session_id": "s1", "text": "hello"})

    assert json.loads(websocket.sent[0])["params"] == {
        "session_id": "s1",
        "text": "hello",
    }


def test_exact_server_error_is_returned_without_retry_or_reinterpretation():
    error = {"code": 4007, "message": "session not found", "data": {"retry": False}}
    client, connector, websocket = _client([
        _ready(),
        _response("request-1", error=error),
    ])

    assert client.request("session.status", {"session_id": "missing"}) == {
        "error": error
    }
    assert len(connector.calls) == 1
    assert len(websocket.sent) == 1


def test_bounded_interleaved_gateway_event_precedes_correlated_response():
    event = json.dumps({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "session.info", "session_id": "s1"},
    })
    client, _, websocket = _client([
        _ready(),
        event,
        _response("request-1", result={"status": "interrupted"}),
    ])

    assert client.request("session.interrupt", {"session_id": "s1"}) == {
        "result": {"status": "interrupted"}
    }
    assert len(websocket.sent) == 1


@pytest.mark.parametrize(
    "ready_frame",
    [
        "not json",
        json.dumps({"jsonrpc": "2.0", "method": "event", "params": {}}),
        json.dumps({
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "event",
            "params": {"type": "gateway.ready"},
        }),
        json.dumps({"jsonrpc": "2.0", "id": "request-1", "result": {}}),
        "x" * (_MAX_FRAME_BYTES + 1),
        b"\xff",
    ],
)
def test_invalid_or_oversize_ready_is_typed_unavailable_without_request(ready_frame):
    client, connector, websocket = _client([ready_frame])

    _assert_unavailable(client.request("session.status", {"session_id": "s1"}))
    assert len(connector.calls) == 1
    assert websocket.sent == []


@pytest.mark.parametrize(
    "response_frame",
    [
        "not json",
        _response("wrong-id", result={}),
        _response("request-1"),
        _response("request-1", result={}, error={"code": 1}),
        json.dumps({"jsonrpc": "1.0", "id": "request-1", "result": {}}),
        '{"jsonrpc":"2.0","id":"request-1","id":"other","result":{}}',
        '{"jsonrpc":"2.0","id":"request-1","result":NaN}',
        json.dumps({
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "event",
            "result": {},
        }),
        "x" * (_MAX_FRAME_BYTES + 1),
    ],
)
def test_malformed_oversize_or_id_mismatched_response_is_typed_unavailable(
    response_frame,
):
    client, connector, websocket = _client([_ready(), response_frame])

    _assert_unavailable(client.request("session.status", {"session_id": "s1"}))
    assert len(connector.calls) == 1
    assert len(websocket.sent) == 1


@pytest.mark.parametrize("failure", [TimeoutError("late"), OSError("private-route")])
def test_connect_or_ready_failure_is_sanitized_and_never_retried(failure, caplog):
    if isinstance(failure, OSError):
        connector = FakeConnector(error=failure)
    else:
        connector = FakeConnector(FakeWebSocket([failure]))
    client = HermesOrchFrontDoor(
        connect_fn=connector,
        token_resolver=lambda: "a" * 64,
        id_factory=lambda: "request-1",
    )

    result = client.request("session.resume", {"session_id": "s1"})

    _assert_unavailable(result)
    assert len(connector.calls) == 1
    assert "private-route" not in json.dumps(result)
    assert "private-route" not in caplog.text


def test_response_timeout_is_typed_unavailable_and_has_no_fallback():
    client, connector, websocket = _client([_ready(), TimeoutError("late response")])

    _assert_unavailable(client.request("session.stop", {"session_id": "s1"}))
    assert len(connector.calls) == 1
    assert len(websocket.sent) == 1
    assert websocket.frames == []


def test_interleaved_events_are_bounded_without_sending_another_request():
    event = json.dumps({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "session.info"},
    })
    client, connector, websocket = _client([_ready(), *([event] * 33)])

    _assert_unavailable(client.request("session.status", {"session_id": "s1"}))
    assert len(connector.calls) == 1
    assert len(websocket.sent) == 1


def test_mismatched_response_id_fails_immediately_without_accepting_later_frame():
    client, _, websocket = _client([
        _ready(),
        _response("other", result={"status": "wrong"}),
        _response("request-1", result={"status": "stopped"}),
    ])

    _assert_unavailable(client.request("session.stop", {"session_id": "s1"}))
    assert len(websocket.sent) == 1
    assert len(websocket.frames) == 1


def test_unsupported_method_and_non_json_params_never_connect():
    connector = FakeConnector(error=AssertionError("must not connect"))
    client = HermesOrchFrontDoor(
        connect_fn=connector,
        token_resolver=lambda: "a" * 64,
        id_factory=lambda: "request-1",
    )

    _assert_unavailable(client.request("maestro.execute", {}))
    _assert_unavailable(client.request("session.status", {"bad": {1, 2}}))
    assert connector.calls == []


def test_request_id_failure_is_typed_unavailable_before_connect():
    connector = FakeConnector(error=AssertionError("must not connect"))

    def fail_id():
        raise RuntimeError("private id failure")

    client = HermesOrchFrontDoor(
        connect_fn=connector,
        token_resolver=lambda: "a" * 64,
        id_factory=fail_id,
    )

    _assert_unavailable(client.request("session.status", {"session_id": "s1"}))
    assert connector.calls == []


def test_oversize_request_never_connects():
    connector = FakeConnector(error=AssertionError("must not connect"))
    client = HermesOrchFrontDoor(
        connect_fn=connector,
        token_resolver=lambda: "a" * 64,
        id_factory=lambda: "request-1",
    )

    _assert_unavailable(
        client.request(
            "prompt.submit",
            {"session_id": "s1", "text": "x" * (_MAX_FRAME_BYTES + 1)},
        )
    )
    assert connector.calls == []


def test_serialization_is_bounded_and_never_emits_nan_or_raw_failure():
    payload = serialize_front_door_result({"result": {"value": float("nan")}})
    _assert_unavailable(json.loads(payload))

    payload = serialize_front_door_result({
        "result": {"value": "x" * (_MAX_FRAME_BYTES + 1)}
    })
    _assert_unavailable(json.loads(payload))
