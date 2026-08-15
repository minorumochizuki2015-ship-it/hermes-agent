"""Credential-free loopback client for the Hermes operational JSON-RPC API.

This module is a transport projection of the existing TUI gateway protocol.
It doesn't own session semantics, authority decisions, retries, or fallback
execution.  Each call opens one bounded loopback WebSocket, admits the
``gateway.ready`` handshake, sends one request, and returns the correlated
server result or exact server error.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Final

logger = logging.getLogger(__name__)

HERMES_ORCH_FRONT_DOOR_URL: Final = "ws://127.0.0.1:3518/api/ws"
HERMES_FRONT_DOOR_UNAVAILABLE: Final = "hermes_front_door_unavailable"
_SESSION_TOKEN_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_SESSION_HEADER_NAME: Final = "X-Hermes-Session-Token"

_CONNECT_TIMEOUT_SECONDS: Final = 2.0
_READY_TIMEOUT_SECONDS: Final = 2.0
_RESPONSE_TIMEOUT_SECONDS: Final = 10.0
_CLOSE_TIMEOUT_SECONDS: Final = 1.0
_MAX_FRAME_BYTES: Final = 1024 * 1024
_MAX_INTERLEAVED_EVENTS: Final = 32


def _object_schema(
    properties: Mapping[str, dict[str, Any]],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build an MCP projection of parameters already accepted by the gateway."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_SESSION_ID = {
    "type": "string",
    "description": "Existing Hermes runtime or stored session identifier.",
}
_PROFILE = {
    "type": "string",
    "description": "Optional existing Hermes profile name.",
}

# These are MCP projections of the existing tui_gateway method parameters.
# The gateway remains the only protocol and validation authority.
ORCH_FRONT_DOOR_TOOLS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "orch_session_create",
        "method": "session.create",
        "description": (
            "Create a Hermes operational session through the persistent loopback "
            "gateway. This performs no Maestro or Codex execution fallback."
        ),
        "input_schema": _object_schema({
            "cols": {"type": "integer"},
            "messages": {"type": "array"},
            "title": {"type": "string"},
            "parent_session_id": {"type": "string"},
            "cwd": {"type": "string"},
            "source": {"type": "string"},
            "profile": _PROFILE,
            "model": {"type": "string"},
            "provider": {"type": "string"},
            "reasoning_effort": {"type": "string"},
            "fast": {"type": "boolean"},
            "close_on_disconnect": {"type": "boolean"},
        }),
    },
    {
        "name": "orch_prompt_submit",
        "method": "prompt.submit",
        "description": (
            "Submit one prompt to an existing Hermes session. An optional "
            "operational_class and operational_context are forwarded unchanged "
            "for the gateway's existing Maestro authority consumer to decide. "
            "A source-bound SDO receipt is an optional advisory Claim Check "
            "consumed before the native agent build."
        ),
        "input_schema": _object_schema(
            {
                "session_id": _SESSION_ID,
                "text": {"type": "string"},
                "operational_class": {
                    "type": "string",
                    "enum": ["ordinary", "orch"],
                },
                "operational_context": {"type": "object"},
                "sdo_decision_receipt": {"type": "object"},
                "sdo_source_binding": {"type": "object"},
                "sdo_candidate_action_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "sdo_producer_input": {"type": "object"},
                "queued": {"type": "boolean"},
                "interrupted": {"type": "boolean"},
                "truncate_before_user_ordinal": {"type": "integer"},
                "confirm_empty_truncate": {"type": "boolean"},
            },
            required=("session_id", "text"),
        ),
    },
    {
        "name": "orch_session_status",
        "method": "session.status",
        "description": "Return the existing Hermes session.status result.",
        "input_schema": _object_schema(
            {"session_id": _SESSION_ID}, required=("session_id",)
        ),
    },
    {
        "name": "orch_session_interrupt",
        "method": "session.interrupt",
        "description": "Interrupt the current turn in an existing Hermes session.",
        "input_schema": _object_schema(
            {"session_id": _SESSION_ID}, required=("session_id",)
        ),
    },
    {
        "name": "orch_session_stop",
        "method": "session.stop",
        "description": "Interrupt and retire one live Hermes session generation.",
        "input_schema": _object_schema(
            {
                "session_id": _SESSION_ID,
                "include_quiescence_receipt": {"type": "boolean"},
                "terminal_transition": {
                    "type": "string",
                    "enum": ["idle", "final", "protected_wait"],
                    "description": (
                        "Required for an ORCH operational session. Maestro's "
                        "pinned terminal authority decides whether retirement "
                        "may proceed."
                    ),
                },
            },
            required=("session_id",),
        ),
    },
    {
        "name": "orch_session_resume",
        "method": "session.resume",
        "description": "Resume an existing Hermes stored session.",
        "input_schema": _object_schema(
            {
                "session_id": _SESSION_ID,
                "cols": {"type": "integer"},
                "profile": _PROFILE,
                "lazy": {"type": "boolean"},
                "source": {"type": "string"},
                "close_on_disconnect": {"type": "boolean"},
                "eager_build": {"type": "boolean"},
            },
            required=("session_id",),
        ),
    },
    {
        "name": "orch_session_reconnect",
        "method": "session.reconnect",
        "description": (
            "Resolve a Hermes session reference to its current successor identity "
            "without constructing an agent or calling a provider."
        ),
        "input_schema": _object_schema(
            {"session_id": _SESSION_ID, "profile": _PROFILE},
            required=("session_id",),
        ),
    },
)

_METHODS: Final = frozenset(spec["method"] for spec in ORCH_FRONT_DOOR_TOOLS)


def _unavailable() -> dict[str, Any]:
    return {
        "error": {
            "code": HERMES_FRONT_DOOR_UNAVAILABLE,
            "message": "Hermes front door unavailable",
        }
    }


def _default_connect(uri: str, **kwargs: Any) -> Any:
    from websockets.sync.client import connect

    return connect(uri, **kwargs)


def _decode_frame(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        encoded = raw.encode("utf-8")
        text = raw
    elif isinstance(raw, bytes):
        encoded = raw
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        return None
    if len(encoded) > _MAX_FRAME_BYTES:
        return None

    def _reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON value")

    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    try:
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    return value if type(value) is dict else None


def _is_ready(frame: dict[str, Any]) -> bool:
    params = frame.get("params")
    return (
        frame.get("jsonrpc") == "2.0"
        and frame.get("method") == "event"
        and "id" not in frame
        and "result" not in frame
        and "error" not in frame
        and type(params) is dict
        and params.get("type") == "gateway.ready"
    )


class HermesOrchFrontDoor:
    """One-request client for the existing persistent Hermes gateway."""

    def __init__(
        self,
        *,
        connect_fn: Callable[..., Any] = _default_connect,
        token_resolver: Callable[[], str | None] = lambda: None,
        id_factory: Callable[[], str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect_fn
        self._token_resolver = token_resolver
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._monotonic = monotonic

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Send one existing gateway method with no retry or execution fallback."""
        if method not in _METHODS or type(params) is not dict:
            return _unavailable()

        try:
            session_token = self._token_resolver()
        except Exception:
            return _unavailable()
        if (
            type(session_token) is not str
            or _SESSION_TOKEN_PATTERN.fullmatch(session_token) is None
        ):
            return _unavailable()
        # The internal MCP front door is not a browser navigation.  Keep the
        # long-lived credential out of the URL (and therefore proxy/history
        # metadata) while the server retains its narrow legacy query path for
        # browser and other short-lived clients.
        front_door_url = HERMES_ORCH_FRONT_DOOR_URL

        try:
            request_id = self._id_factory()
        except Exception:
            return _unavailable()
        if type(request_id) is not str or not request_id:
            return _unavailable()

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            payload = json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError):
            return _unavailable()
        if len(payload.encode("utf-8")) > _MAX_FRAME_BYTES:
            return _unavailable()

        try:
            connection = self._connect(
                front_door_url,
                open_timeout=_CONNECT_TIMEOUT_SECONDS,
                close_timeout=_CLOSE_TIMEOUT_SECONDS,
                max_size=_MAX_FRAME_BYTES,
                compression=None,
                proxy=None,
                additional_headers={_SESSION_HEADER_NAME: session_token},
            )
            with connection as websocket:
                ready = _decode_frame(websocket.recv(timeout=_READY_TIMEOUT_SECONDS))
                if ready is None or not _is_ready(ready):
                    return _unavailable()

                websocket.send(payload)
                deadline = self._monotonic() + _RESPONSE_TIMEOUT_SECONDS
                interleaved_events = 0
                while True:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        return _unavailable()
                    response = _decode_frame(websocket.recv(timeout=remaining))
                    if response is None or response.get("jsonrpc") != "2.0":
                        return _unavailable()

                    if response.get("method") == "event" and "id" not in response:
                        interleaved_events += 1
                        if interleaved_events > _MAX_INTERLEAVED_EVENTS:
                            return _unavailable()
                        continue

                    if "method" in response:
                        return _unavailable()
                    if response.get("id") != request_id:
                        return _unavailable()
                    has_result = "result" in response
                    has_error = "error" in response
                    if has_result == has_error:
                        return _unavailable()
                    if has_error:
                        error = response["error"]
                        if type(error) is not dict:
                            return _unavailable()
                        return {"error": error}
                    return {"result": response["result"]}
        except Exception:
            # WebSocket exceptions can embed URLs, proxy details, headers, or
            # payload fragments. Keep both tool output and logs sanitized.
            logger.warning("Hermes front door request unavailable")
            return _unavailable()


def serialize_front_door_result(result: Mapping[str, Any]) -> str:
    """Serialize a bounded front-door result for an MCP tool response."""
    try:
        payload = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        payload = json.dumps(_unavailable(), sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > _MAX_FRAME_BYTES:
        return json.dumps(_unavailable(), sort_keys=True, separators=(",", ":"))
    return payload


__all__ = [
    "HERMES_FRONT_DOOR_UNAVAILABLE",
    "HERMES_ORCH_FRONT_DOOR_URL",
    "HermesOrchFrontDoor",
    "ORCH_FRONT_DOOR_TOOLS",
    "serialize_front_door_result",
]
