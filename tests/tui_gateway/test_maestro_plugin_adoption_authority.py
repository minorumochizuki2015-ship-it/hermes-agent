"""Contract tests for the sibling protected plugin-adoption consumer."""

from __future__ import annotations

import hashlib
import json

import pytest

from tui_gateway import maestro_plugin_adoption_authority as adoption


def test_ordinary_apply_and_predecessor_versions_are_fixed() -> None:
    assert getattr(adoption, "ORDINARY_APPLY_PLUGIN_VERSION", None) == "0.1.49"
    assert adoption.PLUGIN_VERSION == adoption.ORDINARY_APPLY_PLUGIN_VERSION
    assert adoption.PREVIOUS_TERMINAL_PLUGIN_VERSION == "0.1.42"
    request = _request()
    assert request["plan"]["plugin_version"] == "0.1.49"
    assert adoption.validate_request(request, now=1001.0) == request


def test_terminalize_request_is_an_exact_sibling_action() -> None:
    assert adoption.TERMINAL_OPERATION == "plugin.adoption.terminalize"
    assert adoption.TERMINAL_PLUGIN_VERSION == "0.1.48"
    assert adoption.TERMINAL_PLUGIN_VERSION != adoption.PLUGIN_VERSION
    assert adoption.TERMINAL_SOURCE_REVISION == (
        "9957e57afe528477986f889a1570c7ac7e113f0e"
    )
    assert adoption.TERMINAL_TRANSITION_SET == (
        "plugin.adoption.observe",
        "plugin.adoption.predecessor_terminalize",
    )
    current_identity_digest = hashlib.sha256(adoption.canonical_bytes({
        "claude": "6" * 64,
        "codex": "5" * 64,
        "target_set": list(adoption.TARGET_SET),
    })).hexdigest()
    plan_without_digest = {
        "marketplace_id": adoption.MARKETPLACE_ID,
        "plugin_id": adoption.PLUGIN_ID,
        "plugin_version": adoption.TERMINAL_PLUGIN_VERSION,
        "source_revision": adoption.TERMINAL_SOURCE_REVISION,
        "source_bundle_digest": "1" * 64,
        "target_set": list(adoption.TARGET_SET),
        "transition_set": list(adoption.TERMINAL_TRANSITION_SET),
        "predecessor_identity_digest": "2" * 64,
        "current_identity_digest": current_identity_digest,
        "canonical_identity_digest": "4" * 64,
        "codex_current_state_digest": "5" * 64,
        "claude_current_state_digest": "6" * 64,
        "before_state_digest": "7" * 64,
        "after_state_digest": "7" * 64,
        "rollback_manifest_digest": "8" * 64,
    }
    plan = {
        **plan_without_digest,
        "plan_digest": adoption.compute_terminal_plan_digest(plan_without_digest),
    }
    request = adoption.build_plugin_adoption_terminal_request(
        decision_id="decision-plugin-adoption-terminalize",
        transaction_id="transaction-plugin-adoption-terminalize",
        source_runtime_revision=adoption.TERMINAL_SOURCE_REVISION,
        issued_at=1000.0,
        expires_at=1120.0,
        plan=plan,
    )
    assert request["actual"]["operation"] == adoption.TERMINAL_OPERATION
    assert adoption.validate_terminal_request(request, now=1001.0) == request
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.validate_request(request, now=1001.0)


def test_terminal_contract_descriptor_is_distinct_and_canonical() -> None:
    assert (
        adoption.CONTRACT_ID,
        adoption.CONTRACT_VERSION,
        adoption.CONTRACT_DIGEST,
    ) == (
        "HERMES_PROTECTED_PLUGIN_ADOPTION_V1",
        "hermes-protected-plugin-adoption.v1",
        "999ca51fea4d0dd6f77a7d1393bde5bdbd94b425c1c25e6146157dc0d8f97f07",
    )
    assert adoption.TERMINAL_CONTRACT_DESCRIPTOR["contract"] == {
        "id": "HERMES_PROTECTED_PLUGIN_ADOPTION_V1",
        "keys": ["id", "version", "digest"],
        "version": "hermes-protected-plugin-adoption.terminalize.v1",
    }
    assert adoption.TERMINAL_CONTRACT_DESCRIPTOR["constants"]["operation"] == (
        "plugin.adoption.terminalize"
    )
    assert adoption.TERMINAL_CONTRACT_DESCRIPTOR["replay"]["stable_request"] == (
        "byte-identical stored signed envelope before fresh TTL validation"
    )
    assert (
        adoption.TERMINAL_CONTRACT_ID,
        adoption.TERMINAL_CONTRACT_VERSION,
        adoption.TERMINAL_CONTRACT_DIGEST,
    ) == (
        "HERMES_PROTECTED_PLUGIN_ADOPTION_V1",
        "hermes-protected-plugin-adoption.terminalize.v1",
        "af7b40de7bf29d27adace625e04671e8e5250fa7a1f9ab7fb4b85c9c68fbefc9",
    )
    assert adoption.TERMINAL_CONTRACT_DIGEST == hashlib.sha256(
        adoption.canonical_bytes(adoption.TERMINAL_CONTRACT_DESCRIPTOR)
    ).hexdigest()

    request = _terminal_request()
    assert request["contract"] == {
        "id": adoption.TERMINAL_CONTRACT_ID,
        "version": adoption.TERMINAL_CONTRACT_VERSION,
        "digest": adoption.TERMINAL_CONTRACT_DIGEST,
    }
    request["contract"] = {
        "id": adoption.CONTRACT_ID,
        "version": adoption.CONTRACT_VERSION,
        "digest": adoption.CONTRACT_DIGEST,
    }
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.validate_terminal_request(request, now=1001.0)


def _plan(**updates) -> dict:
    value = {
        "marketplace_id": adoption.MARKETPLACE_ID,
        "plugin_id": adoption.PLUGIN_ID,
        "plugin_version": adoption.PLUGIN_VERSION,
        "source_revision": "1" * 40,
        "source_bundle_digest": "2" * 64,
        "target_set": list(adoption.TARGET_SET),
        "transition_set": list(adoption.TRANSITION_SET),
        "before_state_digest": "3" * 64,
        "after_state_digest": "4" * 64,
        "rollback_manifest_digest": "5" * 64,
    }
    value.update(updates)
    value["plan_digest"] = adoption.compute_plan_digest(value)
    return value


def _request(**plan_updates) -> dict:
    return adoption.build_plugin_adoption_request(
        decision_id="decision-plugin-adoption",
        transaction_id="transaction-plugin-adoption",
        source_runtime_revision="6" * 40,
        issued_at=1000.0,
        expires_at=1120.0,
        plan=_plan(**plan_updates),
    )


def _terminal_plan(**updates) -> dict:
    codex_digest = updates.pop("codex_current_state_digest", "a" * 64)
    claude_digest = updates.pop("claude_current_state_digest", "b" * 64)
    value = {
        "marketplace_id": adoption.MARKETPLACE_ID,
        "plugin_id": adoption.PLUGIN_ID,
        "plugin_version": adoption.TERMINAL_PLUGIN_VERSION,
        "source_revision": adoption.TERMINAL_SOURCE_REVISION,
        "source_bundle_digest": "1" * 64,
        "target_set": list(adoption.TARGET_SET),
        "transition_set": list(adoption.TERMINAL_TRANSITION_SET),
        "predecessor_identity_digest": "2" * 64,
        "current_identity_digest": hashlib.sha256(adoption.canonical_bytes({
            "claude": claude_digest,
            "codex": codex_digest,
            "target_set": list(adoption.TARGET_SET),
        })).hexdigest(),
        "canonical_identity_digest": "4" * 64,
        "codex_current_state_digest": codex_digest,
        "claude_current_state_digest": claude_digest,
        "before_state_digest": "7" * 64,
        "after_state_digest": "7" * 64,
        "rollback_manifest_digest": "8" * 64,
    }
    value.update(updates)
    value["plan_digest"] = adoption.compute_terminal_plan_digest(value)
    return value


def _terminal_request(**plan_updates) -> dict:
    return adoption.build_plugin_adoption_terminal_request(
        decision_id="decision-plugin-adoption-terminalize",
        transaction_id="transaction-plugin-adoption-terminalize",
        source_runtime_revision=adoption.TERMINAL_SOURCE_REVISION,
        issued_at=1000.0,
        expires_at=1120.0,
        plan=_terminal_plan(**plan_updates),
    )


def _receipt(request: dict, **updates) -> dict:
    actual = request["actual"]
    plan = request["plan"]
    value = {
        "outcome": "allow",
        "code": "plugin_adoption_allowed",
        "decision_id": actual["decision_id"],
        "transaction_id": actual["transaction_id"],
        "authority_owner": adoption.AUTHORITY_OWNER,
        "authority_bundle_version": adoption.AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": adoption.AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": adoption.AUTHORITY_CONSUMER,
        "contract_id": adoption.CONTRACT_ID,
        "contract_version": adoption.CONTRACT_VERSION,
        "contract_digest": adoption.CONTRACT_DIGEST,
        "operation": adoption.OPERATION,
        "marketplace_id": plan["marketplace_id"],
        "plugin_id": plan["plugin_id"],
        "plugin_version": plan["plugin_version"],
        "source_runtime_revision": actual["source_runtime_revision"],
        "source_revision": plan["source_revision"],
        "source_bundle_digest": plan["source_bundle_digest"],
        "target_set": plan["target_set"],
        "transition_set": plan["transition_set"],
        "before_state_digest": plan["before_state_digest"],
        "after_state_digest": plan["after_state_digest"],
        "rollback_manifest_digest": plan["rollback_manifest_digest"],
        "plan_digest": plan["plan_digest"],
        "issued_at": actual["issued_at"],
        "expires_at": actual["expires_at"],
        "final_decision_state": "final_allowed_once",
        "final_execution_permitted": True,
        "consumed_once": True,
        "request_digest": hashlib.sha256(adoption.canonical_bytes(request)).hexdigest(),
    }
    value.update(updates)
    return value


def _envelope_bytes(request: dict, **receipt_updates) -> bytes:
    return adoption.canonical_bytes({
        "receipt": _receipt(request, **receipt_updates),
        "signature": (
            "-----BEGIN SSH SIGNATURE-----\n"
            "fixture\n"
            "-----END SSH SIGNATURE-----\n"
        ),
    })


def _terminal_receipt(request: dict, **updates) -> dict:
    actual = request["actual"]
    plan = request["plan"]
    value = {
        "outcome": "allow",
        "code": adoption.TERMINAL_ALLOW_CODE,
        "decision_id": actual["decision_id"],
        "transaction_id": actual["transaction_id"],
        "authority_owner": adoption.AUTHORITY_OWNER,
        "authority_bundle_version": adoption.AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": adoption.AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": adoption.AUTHORITY_CONSUMER,
        "contract_id": adoption.TERMINAL_CONTRACT_ID,
        "contract_version": adoption.TERMINAL_CONTRACT_VERSION,
        "contract_digest": adoption.TERMINAL_CONTRACT_DIGEST,
        "operation": adoption.TERMINAL_OPERATION,
        "marketplace_id": plan["marketplace_id"],
        "plugin_id": plan["plugin_id"],
        "plugin_version": plan["plugin_version"],
        "source_runtime_revision": actual["source_runtime_revision"],
        "source_revision": plan["source_revision"],
        "source_bundle_digest": plan["source_bundle_digest"],
        "target_set": plan["target_set"],
        "transition_set": plan["transition_set"],
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
        "final_decision_state": "final_allowed_once",
        "final_execution_permitted": True,
        "consumed_once": True,
        "request_digest": hashlib.sha256(adoption.canonical_bytes(request)).hexdigest(),
    }
    value.update(updates)
    return value


def _terminal_envelope_bytes(request: dict, **receipt_updates) -> bytes:
    return adoption.canonical_bytes({
        "receipt": _terminal_receipt(request, **receipt_updates),
        "signature": (
            "-----BEGIN SSH SIGNATURE-----\n"
            "fixture\n"
            "-----END SSH SIGNATURE-----\n"
        ),
    })


def test_terminal_receipt_verifies_and_cannot_cross_with_ordinary(monkeypatch) -> None:
    terminal_request = _terminal_request()
    terminal_envelope = _terminal_envelope_bytes(terminal_request)
    ordinary_request = _request()
    ordinary_envelope = _envelope_bytes(ordinary_request)
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)

    verified = adoption.verify_plugin_adoption_terminal_envelope(
        request_bytes=adoption.canonical_bytes(terminal_request),
        envelope_bytes=terminal_envelope,
        now=1001.0,
    )
    assert verified.allowed is True
    assert verified.receipt["code"] == adoption.TERMINAL_ALLOW_CODE
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.verify_plugin_adoption_envelope(
            request_bytes=adoption.canonical_bytes(terminal_request),
            envelope_bytes=terminal_envelope,
            now=1001.0,
        )
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.verify_plugin_adoption_terminal_envelope(
            request_bytes=adoption.canonical_bytes(ordinary_request),
            envelope_bytes=ordinary_envelope,
            now=1001.0,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "operation",
        "version",
        "source",
        "target_order",
        "transition_order",
        "state_equality",
        "before_after",
        "current_identity",
        "digest",
    ],
)
def test_terminal_request_negative_matrix(mutation: str) -> None:
    request = _terminal_request()
    if mutation == "missing":
        del request["plan"]["canonical_identity_digest"]
    elif mutation == "extra":
        request["plan"]["path"] = "/forbidden"
    elif mutation == "operation":
        request["actual"]["operation"] = adoption.OPERATION
    elif mutation == "version":
        request["plan"]["plugin_version"] = adoption.PLUGIN_VERSION
    elif mutation == "source":
        request["plan"]["source_revision"] = "0" * 40
    elif mutation == "target_order":
        request["plan"]["target_set"].reverse()
    elif mutation == "transition_order":
        request["plan"]["transition_set"].reverse()
    elif mutation == "state_equality":
        request["plan"]["claude_current_state_digest"] = request["plan"][
            "codex_current_state_digest"
        ]
    elif mutation == "before_after":
        request["plan"]["after_state_digest"] = "9" * 64
    elif mutation == "current_identity":
        request["plan"]["current_identity_digest"] = "9" * 64
    elif mutation == "digest":
        request["plan"]["plan_digest"] = "9" * 64
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.validate_terminal_request(request, now=1001.0)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("operation", "plugin.adoption.apply"),
        ("plugin_version", adoption.PLUGIN_VERSION),
        ("source_revision", "0" * 40),
        ("target_set", ["claude", "codex"]),
        ("transition_set", list(reversed(adoption.TERMINAL_TRANSITION_SET))),
        ("codex_current_state_digest", "9" * 64),
        ("claude_current_state_digest", "9" * 64),
        ("request_digest", "9" * 64),
        ("consumed_once", False),
        ("code", "plugin_adoption_allowed"),
    ],
)
def test_terminal_receipt_substitution_is_denied(
    monkeypatch, field: str, replacement
) -> None:
    request = _terminal_request()
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.verify_plugin_adoption_terminal_envelope(
            request_bytes=adoption.canonical_bytes(request),
            envelope_bytes=_terminal_envelope_bytes(
                request,
                **{field: replacement},
            ),
            now=1001.0,
        )


def test_terminal_receipt_wrong_signer_and_stale_are_denied(monkeypatch) -> None:
    request = _terminal_request()
    request_bytes = adoption.canonical_bytes(request)
    envelope_bytes = _terminal_envelope_bytes(request)
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: False)
    with pytest.raises(
        adoption.PluginAdoptionAuthorityError,
        match="authority_signature_unavailable",
    ):
        adoption.verify_plugin_adoption_terminal_envelope(
            request_bytes=request_bytes,
            envelope_bytes=envelope_bytes,
            now=1001.0,
        )
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)
    with pytest.raises(
        adoption.PluginAdoptionAuthorityError,
        match="authority_stale",
    ):
        adoption.verify_plugin_adoption_terminal_envelope(
            request_bytes=request_bytes,
            envelope_bytes=envelope_bytes,
            now=1120.0,
        )


def test_terminal_receipt_has_dedicated_exact_deny(monkeypatch) -> None:
    request = _terminal_request()
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)
    verified = adoption.verify_plugin_adoption_terminal_envelope(
        request_bytes=adoption.canonical_bytes(request),
        envelope_bytes=_terminal_envelope_bytes(
            request,
            outcome="deny",
            code=adoption.TERMINAL_DENY_CODE,
            final_decision_state="final_denied",
            final_execution_permitted=False,
        ),
        now=1001.0,
    )
    assert verified.allowed is False
    assert verified.receipt["code"] == adoption.TERMINAL_DENY_CODE


def test_terminal_authority_consumer_sends_and_verifies_exact_terminal_bytes(
    monkeypatch,
) -> None:
    request = _terminal_request()
    request_bytes = adoption.canonical_bytes(request)
    envelope_bytes = _terminal_envelope_bytes(request)
    client = object()
    sent: list[bytes] = []
    connected: list[str] = []
    closed: list[object] = []

    monkeypatch.setattr(adoption._base, "_trusted_runtime_boundary", lambda: True)
    monkeypatch.setattr(
        adoption._base,
        "_fixed_authority_socket_path",
        lambda: "/private/fixture-authority.sock",
    )
    monkeypatch.setattr(adoption._base, "_NATIVE_TIME_MONOTONIC", lambda: 10.0)
    monkeypatch.setattr(adoption._base, "_NATIVE_SOCKET_CLASS", lambda *_args: client)
    monkeypatch.setattr(adoption._base, "_NATIVE_SOCKET_SETTIMEOUT", lambda *_args: None)
    monkeypatch.setattr(
        adoption._base,
        "_NATIVE_SOCKET_CONNECT",
        lambda _client, path: connected.append(path),
    )
    monkeypatch.setattr(
        adoption._base,
        "_NATIVE_SOCKET_SENDALL",
        lambda _client, payload: sent.append(payload),
    )
    monkeypatch.setattr(
        adoption._base,
        "_NATIVE_SOCKET_RECV",
        lambda *_args: envelope_bytes + b"\n",
    )
    monkeypatch.setattr(
        adoption._base,
        "_NATIVE_SOCKET_CLOSE",
        lambda value: closed.append(value),
    )
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)

    verified = adoption.request_plugin_adoption_terminal_decision(
        request,
        now=1001.0,
    )

    assert verified.allowed is True
    assert verified.request == request
    assert verified.request_digest == hashlib.sha256(request_bytes).hexdigest()
    assert connected == ["/private/fixture-authority.sock"]
    assert sent == [request_bytes + b"\n"]
    assert closed == [client]


def test_terminal_authority_consumer_replays_stored_envelope_before_ttl_denial(
    monkeypatch,
) -> None:
    request = _terminal_request()
    request_bytes = adoption.canonical_bytes(request)
    envelope_bytes = _terminal_envelope_bytes(request)
    client = object()
    sent: list[bytes] = []

    monkeypatch.setattr(adoption._base, "_trusted_runtime_boundary", lambda: True)
    monkeypatch.setattr(
        adoption._base,
        "_fixed_authority_socket_path",
        lambda: "/private/fixture-authority.sock",
    )
    monkeypatch.setattr(adoption._base, "_NATIVE_TIME_MONOTONIC", lambda: 10.0)
    monkeypatch.setattr(adoption._base, "_NATIVE_SOCKET_CLASS", lambda *_args: client)
    monkeypatch.setattr(adoption._base, "_NATIVE_SOCKET_SETTIMEOUT", lambda *_args: None)
    monkeypatch.setattr(adoption._base, "_NATIVE_SOCKET_CONNECT", lambda *_args: None)
    monkeypatch.setattr(
        adoption._base,
        "_NATIVE_SOCKET_SENDALL",
        lambda _client, payload: sent.append(payload),
    )
    monkeypatch.setattr(
        adoption._base,
        "_NATIVE_SOCKET_RECV",
        lambda *_args: envelope_bytes + b"\n",
    )
    monkeypatch.setattr(adoption._base, "_NATIVE_SOCKET_CLOSE", lambda *_args: None)
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)

    verified = adoption.request_plugin_adoption_terminal_decision(
        request,
        now=1200.0,
        prepared_replay=True,
    )

    assert verified.allowed is True
    assert verified.request_bytes == request_bytes
    assert verified.envelope_bytes == envelope_bytes
    assert sent == [request_bytes + b"\n"]


def test_request_is_exact_stable_and_contains_no_path_or_command() -> None:
    first = _request()
    second = _request()
    assert first == second
    assert set(first) == {"actual", "challenge", "contract", "plan"}
    assert first["contract"] == {
        "id": "HERMES_PROTECTED_PLUGIN_ADOPTION_V1",
        "version": "hermes-protected-plugin-adoption.v1",
        "digest": "999ca51fea4d0dd6f77a7d1393bde5bdbd94b425c1c25e6146157dc0d8f97f07",
    }
    assert first["actual"]["operation"] == "plugin.adoption.apply"
    encoded = adoption.canonical_bytes(first).decode("ascii")
    assert '"target_set":["codex","claude"]' in encoded
    assert '"transition_set":["marketplace.rebind","plugin.install","plugin.activate"]' in encoded
    assert '"path"' not in encoded
    assert '"command"' not in encoded
    assert '"prompt"' not in encoded
    assert "/Users/" not in encoded


@pytest.mark.parametrize(
    "mutation",
    ["version", "target_order", "transition_order", "plan_digest", "extra"],
)
def test_request_rejects_wrong_contract_or_plan(mutation: str) -> None:
    request = _request()
    if mutation == "version":
        request["contract"]["version"] = "hermes-protected-plugin-adoption.v2"
    elif mutation == "target_order":
        request["plan"]["target_set"].reverse()
    elif mutation == "transition_order":
        request["plan"]["transition_set"].reverse()
    elif mutation == "plan_digest":
        request["plan"]["plan_digest"] = "0" * 64
    else:
        request["plan"]["path"] = "/private/forbidden"
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.validate_request(request, now=1001.0)


def test_superseded_contract_digest_is_rejected() -> None:
    request = _request()
    request["contract"]["digest"] = (
        "c7a4f7be6377fb6ae2bac77060870116632cc3da9163cb3d9de727302f36bcee"
    )
    with pytest.raises(adoption.PluginAdoptionAuthorityError, match="authority_mismatch"):
        adoption.validate_request(request, now=1001.0)


def test_signed_envelope_verifies_and_persisted_bytes_reverify(monkeypatch) -> None:
    request = _request()
    request_bytes = adoption.canonical_bytes(request)
    envelope_bytes = _envelope_bytes(request)
    signed_payloads: list[bytes] = []

    def verify(payload: bytes, _signature: str) -> bool:
        signed_payloads.append(payload)
        return True

    monkeypatch.setattr(adoption._base, "_verify_sshsig", verify)
    first = adoption.verify_plugin_adoption_envelope(
        request_bytes=request_bytes,
        envelope_bytes=envelope_bytes,
        now=1001.0,
    )
    second = adoption.verify_plugin_adoption_envelope(
        request_bytes=request_bytes,
        envelope_bytes=envelope_bytes,
        now=1002.0,
    )
    assert first == second
    assert first.allowed is True
    assert first.request_digest == hashlib.sha256(request_bytes).hexdigest()
    assert first.envelope_digest == hashlib.sha256(envelope_bytes).hexdigest()
    assert signed_payloads == [
        adoption.canonical_bytes({"request": request, "receipt": _receipt(request)}),
        adoption.canonical_bytes({"request": request, "receipt": _receipt(request)}),
    ]


def test_wrong_signer_fails_closed(monkeypatch) -> None:
    request = _request()
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: False)
    with pytest.raises(
        adoption.PluginAdoptionAuthorityError,
        match="authority_signature_unavailable",
    ):
        adoption.verify_plugin_adoption_envelope(
            request_bytes=adoption.canonical_bytes(request),
            envelope_bytes=_envelope_bytes(request),
            now=1001.0,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("contract_digest", "0" * 64),
        ("plan_digest", "0" * 64),
        ("request_digest", "0" * 64),
        ("plugin_version", "0.1.15"),
        ("target_set", ["claude", "codex"]),
    ],
)
def test_signed_receipt_substitution_fails_closed(monkeypatch, field, replacement) -> None:
    request = _request()
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)
    with pytest.raises(adoption.PluginAdoptionAuthorityError, match="authority_mismatch"):
        adoption.verify_plugin_adoption_envelope(
            request_bytes=adoption.canonical_bytes(request),
            envelope_bytes=_envelope_bytes(request, **{field: replacement}),
            now=1001.0,
        )


def test_prompt_receipt_cannot_substitute_for_adoption(monkeypatch) -> None:
    request = _request()
    prompt_receipt = {
        "outcome": "allow",
        "code": "authority_allowed",
        "decision_id": request["actual"]["decision_id"],
        "consumed_once": True,
    }
    envelope = adoption.canonical_bytes({
        "receipt": prompt_receipt,
        "signature": "-----BEGIN SSH SIGNATURE-----\nfixture\n-----END SSH SIGNATURE-----\n",
    })
    monkeypatch.setattr(adoption._base, "_verify_sshsig", lambda *_args: True)
    with pytest.raises(
        adoption.PluginAdoptionAuthorityError,
        match="authority_contract_unavailable",
    ):
        adoption.verify_plugin_adoption_envelope(
            request_bytes=adoption.canonical_bytes(request),
            envelope_bytes=envelope,
            now=1001.0,
        )


def test_noncanonical_or_extra_envelope_fails_before_signature(monkeypatch) -> None:
    request = _request()
    envelope = json.dumps(
        {"receipt": _receipt(request), "signature": "x", "extra": True}
    ).encode("ascii")
    called = False

    def verify(*_args) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(adoption._base, "_verify_sshsig", verify)
    with pytest.raises(adoption.PluginAdoptionAuthorityError):
        adoption.verify_plugin_adoption_envelope(
            request_bytes=adoption.canonical_bytes(request),
            envelope_bytes=envelope,
            now=1001.0,
        )
    assert called is False
