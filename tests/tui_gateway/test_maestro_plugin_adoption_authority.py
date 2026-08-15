"""Contract tests for the sibling protected plugin-adoption consumer."""

from __future__ import annotations

import hashlib
import json

import pytest

from tui_gateway import maestro_plugin_adoption_authority as adoption


def test_successor_and_predecessor_versions_are_fixed() -> None:
    assert adoption.PLUGIN_VERSION == "0.1.47"
    assert adoption.PREVIOUS_TERMINAL_PLUGIN_VERSION == "0.1.42"
    request = _request()
    assert request["plan"]["plugin_version"] == "0.1.47"
    assert adoption.validate_request(request, now=1001.0) == request


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
