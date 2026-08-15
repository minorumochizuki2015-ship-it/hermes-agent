"""Focused protocol and negative tests for the INC-191 host consumer."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import audit_firing_admission as admission
from tui_gateway import maestro_authority as authority


GENERATION = "inc191-g7"
RUNTIME_REVISION = "2" * 40
HOST_SERVICE_COMMIT = "0d37037d37fb68fb5c4557ecc189aafd942ece3c"
HOST_SERVICE_SHA256 = "8c5e32ef4ea48dc78b07f3b55d857f655831790969888d214c641ef5d6e56cb3"
HOST_DIST_SHA256 = "f7fe3443cc34fdf35283eff4ee203ada0495f5222181c21023f197bb203d3779"
HOST_SERVICE_PATH = Path(
    "/Users/moc/ORCH-Next/worktrees/maestro-kernel/inc191-host-service-v1/"
    "src/hermesProtectedAuthorityService.ts"
)
HOST_DIST_PATH = (
    HOST_SERVICE_PATH.parents[1] / "dist/hermesProtectedAuthorityService.js"
)
_DUMMY_PROOF = (
    "-----BEGIN SSH SIGNATURE-----\n"
    "test-only-hermetic-proof\n"
    "-----END SSH SIGNATURE-----\n"
)


@pytest.fixture(autouse=True)
def _reset_operation_state():
    with admission._state_lock:
        admission._pending_firing_ids.clear()
    yield
    with admission._state_lock:
        admission._pending_firing_ids.clear()


@pytest.fixture
def runtime_surface(monkeypatch, tmp_path):
    authority_dir = tmp_path / "authority"
    authority_dir.mkdir(mode=0o700)
    key_path = authority_dir / "inc191-runtime-key"
    key_path.write_bytes(b"disposable-test-key-material")
    key_path.chmod(0o600)
    config_path = authority_dir / "inc191-runtime.json"

    def write_config(
        *,
        generation: object = GENERATION,
        runtime_revision: object = RUNTIME_REVISION,
        signing_key_path: object = str(key_path),
        mode: int = 0o600,
    ) -> None:
        config_path.unlink(missing_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "generation": generation,
                    "runtime_revision": runtime_revision,
                    "signing_key_path": signing_key_path,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="ascii",
        )
        config_path.chmod(mode)

    write_config()
    monkeypatch.setattr(admission, "_runtime_config_path", lambda: config_path)
    return SimpleNamespace(
        root=tmp_path,
        authority_dir=authority_dir,
        key_path=key_path,
        config_path=config_path,
        write_config=write_config,
    )


def _firing_id(label: str, generation: str = GENERATION) -> str:
    suffix = hashlib.sha256(f"inc191-test:{label}".encode("ascii")).hexdigest()[:32]
    return f"{generation}:{suffix}"


def _request(label: str = "default") -> dict:
    return {
        "target_ref": f"project:task:{label}",
        "target_cursor_before": "cursor-1",
        "target_cursor_after": "cursor-2",
        "target_digest_before": "3" * 64,
        "target_digest_after": "4" * 64,
        "target_read_result": {
            "status": "success",
            "receipt_ref": "tool-receipt-target",
        },
        "independent_surfaces_checked": ["git"],
        "owner_claim_refs": ["owner-commentary"],
        "independent_evidence_refs": ["tool-receipt-git"],
        "trigger_set": ["user_correction"],
        "panoramic_surfaces_checked": [
            "A_LIFECYCLE_STATE",
            "B_RUNTIME_RECOVERY",
            "C_CONSUMER_JOURNEY",
        ],
        "decision_before": {"action": "keep"},
        "decision_after": {"action": "demote"},
        "decision_delta": True,
        "notification_decision": "NOTIFY",
        "visibility_debt": False,
        "model_invocation": {
            "occurred_before_admission": False,
            "requested_if_admitted": True,
        },
        "bounded_usage_delta": {"status": "known", "value": 7},
    }


def _audit_readback(
    request: dict,
    *,
    reason: str = "THREAT_BOUNDARY_UNIMPLEMENTED_WITHOUT_HOST_INTEGRATION",
) -> dict:
    firing_admission = {
        key: deepcopy(request[key])
        for key in admission._FIRING_ADMISSION_KEYS
        if key not in {"receipt_result", "bounded_usage_delta"}
    }
    firing_admission["bounded_usage_delta"] = "UNKNOWN"
    firing_admission["receipt_result"] = "MALFORMED_FIRING_REJECTED"
    return {
        "schema_version": admission.INC191_AUDIT_READBACK_SCHEMA,
        "firing_id": request["firing_id"],
        "receipt_result": "MALFORMED_FIRING_REJECTED",
        "audit_verdict_allowed": False,
        "model_invocation_allowed": False,
        "notification_allowed": False,
        "visibility_debt": False,
        "no_delta": False,
        "reasons": [reason],
        "bounded_usage_delta": "UNKNOWN",
        "firing_admission": firing_admission,
        "provider_authority": False,
        "model_invocations": 0,
    }


def _host_receipt(
    host_request: dict,
    *,
    reason: str = "THREAT_BOUNDARY_UNIMPLEMENTED_WITHOUT_HOST_INTEGRATION",
) -> dict:
    request = host_request["request"]
    return {
        "binding": {
            "authority_source": admission._authority_source(),
            "consumer_id": admission.INC191_HOST_CONSUMER,
            "firing_id": request["firing_id"],
            "request_sha256": host_request["request_sha256"],
            "operation_nonce": host_request["operation_nonce"],
            "consumed_once": True,
            "provenance": admission.INC191_HOST_PROVENANCE,
        },
        "audit_readback": _audit_readback(request, reason=reason),
    }


def _enable_hermetic_signing(monkeypatch):
    signed = []

    def sign(payload, config):
        signed.append((payload, config))
        return _DUMMY_PROOF

    monkeypatch.setattr(admission, "_sign_unsigned_request", sign)
    return signed


def _mutate_to_impossible_admitted(receipt):
    receipt["audit_readback"].update(
        receipt_result="ADMITTED_INDEPENDENT_AUDIT",
        audit_verdict_allowed=True,
        model_invocation_allowed=True,
        notification_allowed=True,
    )
    receipt["audit_readback"]["firing_admission"].update(
        receipt_result="ADMITTED_INDEPENDENT_AUDIT"
    )


def _mutate_to_known_usage(receipt):
    receipt["audit_readback"]["bounded_usage_delta"] = 7
    receipt["audit_readback"]["firing_admission"]["bounded_usage_delta"] = 7


def test_public_api_is_one_exact_sanitized_request_only():
    assert tuple(
        inspect.signature(admission.consume_inc191_audit_firing).parameters
    ) == ("request",)
    assert admission.__all__ == ["consume_inc191_audit_firing"]
    with pytest.raises(TypeError):
        admission.consume_inc191_audit_firing(_request(), object())  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "key,value",
    [
        ("host_verifier", object()),
        ("provider", object()),
        ("receipt_catalog", []),
        ("broker", "alternate"),
        ("socket_path", "/tmp/alternate.sock"),
        ("generation", "inc191-g999"),
        ("runtime_revision", "f" * 40),
        ("signing_key_path", "/tmp/alternate-key"),
        ("signature_namespace", "alternate"),
        ("caller_identity", "alternate"),
        ("firing_id", _firing_id("caller-selected")),
    ],
)
def test_caller_transport_and_authority_injection_fails_before_sign_or_send(
    monkeypatch, runtime_surface, key, value
):
    signed = _enable_hermetic_signing(monkeypatch)
    sent = []
    monkeypatch.setattr(
        admission, "_request_fixed_host", lambda item: sent.append(item)
    )
    request = _request(f"injection-{key}")
    request[key] = value
    assert admission.consume_inc191_audit_firing(request) == {
        "outcome": "deny",
        "code": "inc191_audit_request_invalid",
    }
    assert signed == []
    assert sent == []


def test_exact_nine_key_request_signs_canonical_unsigned_eight_keys(
    monkeypatch, runtime_surface
):
    random_sizes = []

    def random_bytes(length):
        random_sizes.append(length)
        return b"\x11" * length

    monkeypatch.setattr(admission.os, "urandom", random_bytes)
    signed = _enable_hermetic_signing(monkeypatch)
    observed = []

    def host(value):
        observed.append(deepcopy(value))
        return _host_receipt(value)

    monkeypatch.setattr(admission, "_request_fixed_host", host)
    request = _request("exact-wire")
    result = admission.consume_inc191_audit_firing(request)
    assert len(observed) == 1 and len(signed) == 1
    assert random_sizes == [16, 32]
    wire = observed[0]
    assert result == _audit_readback(wire["request"])
    assert wire["request"]["bounded_usage_delta"] == {"status": "known", "value": 7}
    assert result["bounded_usage_delta"] == "UNKNOWN"
    assert result["firing_admission"]["bounded_usage_delta"] == "UNKNOWN"
    assert result["reasons"] == [
        "THREAT_BOUNDARY_UNIMPLEMENTED_WITHOUT_HOST_INTEGRATION"
    ]
    assert set(wire) == {
        "operation",
        "authority_source",
        "consumer_id",
        "generation",
        "runtime_revision",
        "request",
        "request_sha256",
        "operation_nonce",
        "caller_proof",
    }
    assert wire["operation"] == "verify_consume_inc191_audit_firing"
    assert wire["consumer_id"] == "hermes_host_runtime"
    assert wire["generation"] == GENERATION
    assert wire["runtime_revision"] == RUNTIME_REVISION
    assert wire["request"]["firing_id"] == f"{GENERATION}:{'11' * 16}"
    assert wire["caller_proof"] == _DUMMY_PROOF
    assert len(wire["operation_nonce"]) == 64
    unsigned = {key: wire[key] for key in wire if key != "caller_proof"}
    assert signed[0][0] == authority._canonical_authority_payload(unsigned)
    assert set(unsigned) == admission._UNSIGNED_REQUEST_KEYS
    assert (
        wire["request_sha256"]
        == hashlib.sha256(
            authority._canonical_authority_payload(wire["request"])
        ).hexdigest()
    )


def test_firing_id_helper_is_generation_bound_and_exactly_16_random_bytes(monkeypatch):
    monkeypatch.setattr(admission.os, "urandom", lambda length: b"\xab" * length)
    assert admission._new_firing_id("inc191-g999999999") == (
        "inc191-g999999999:" + "ab" * 16
    )
    with pytest.raises(ValueError):
        admission._new_firing_id("inc191-g0")


@pytest.mark.parametrize(
    "generation,revision,key_value",
    [
        ("inc191-g0", RUNTIME_REVISION, None),
        ("inc191-g1000000000", RUNTIME_REVISION, None),
        (GENERATION, "A" * 40, None),
        (GENERATION, "1" * 39, None),
        (GENERATION, RUNTIME_REVISION, "relative-key"),
    ],
)
def test_malformed_runtime_configuration_is_typed_unavailable(
    runtime_surface, generation, revision, key_value
):
    runtime_surface.write_config(
        generation=generation,
        runtime_revision=revision,
        signing_key_path=key_value or str(runtime_surface.key_path),
    )
    assert admission.consume_inc191_audit_firing(_request("bad-config")) == {
        "outcome": "deny",
        "code": "inc191_runtime_configuration_unavailable",
    }


def test_missing_loose_and_symlinked_config_are_unavailable(runtime_surface):
    runtime_surface.config_path.unlink()
    assert admission._load_runtime_config() is None
    runtime_surface.write_config(mode=0o644)
    assert admission._load_runtime_config() is None
    real = runtime_surface.authority_dir / "real-config"
    runtime_surface.config_path.replace(real)
    runtime_surface.config_path.symlink_to(real)
    assert admission._load_runtime_config() is None


@pytest.mark.parametrize(
    "content",
    [
        b"{}",
        b'{"generation":"inc191-g7"}',
        b'{"generation":"inc191-g7","runtime_revision":"' + b"2" * 40 + b'"}',
        b'{"generation":"inc191-g7","runtime_revision":"'
        + b"2" * 40
        + b'","signing_key_path":""}',
        b"not-json",
    ],
)
def test_missing_or_malformed_config_fields_are_unavailable(runtime_surface, content):
    runtime_surface.config_path.write_bytes(content)
    runtime_surface.config_path.chmod(0o600)
    assert admission.consume_inc191_audit_firing(_request("missing-config-field")) == {
        "outcome": "deny",
        "code": "inc191_runtime_configuration_unavailable",
    }


def test_missing_loose_and_symlinked_private_key_are_unavailable(runtime_surface):
    runtime_surface.key_path.unlink()
    assert admission._load_runtime_config() is None
    runtime_surface.key_path.write_bytes(b"test-key")
    runtime_surface.key_path.chmod(0o644)
    assert admission._load_runtime_config() is None
    runtime_surface.key_path.unlink()
    target = runtime_surface.authority_dir / "real-key"
    target.write_bytes(b"test-key")
    target.chmod(0o600)
    runtime_surface.key_path.symlink_to(target)
    assert admission._load_runtime_config() is None


@pytest.mark.parametrize(
    "firing_id",
    [
        "inc191-g6:" + "0" * 32,
        "inc191-g7:" + "0" * 31,
        "inc191-g7:" + "A" * 32,
        "inc191-g7:" + "0" * 32 + ":extra",
        "inc191-g7:private-prompt-value-00000000000",
    ],
)
def test_any_caller_selected_firing_id_is_an_extra_field_and_fails_before_send(
    monkeypatch, runtime_surface, firing_id
):
    signed = _enable_hermetic_signing(monkeypatch)
    request = _request("caller-id")
    request["firing_id"] = firing_id
    assert (
        admission.consume_inc191_audit_firing(request)["code"]
        == "inc191_audit_request_invalid"
    )
    assert signed == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request["bounded_usage_delta"].update(value=True),
        lambda request: request.update(visibility_debt=1),
        lambda request: request["decision_before"].update(prompt="hidden"),
        lambda request: request.update(target_ref="https://private.invalid/value"),
        lambda request: request.update(target_digest_before="digest-not-sha256"),
        lambda request: request["independent_evidence_refs"].append("tool-receipt-git"),
    ],
)
def test_non_sanitized_c0_values_and_bool_as_int_fail_before_send(
    monkeypatch, runtime_surface, mutate
):
    signed = _enable_hermetic_signing(monkeypatch)
    sent = []
    monkeypatch.setattr(
        admission, "_request_fixed_host", lambda item: sent.append(item)
    )
    request = _request("unsafe-c0")
    mutate(request)
    assert (
        admission.consume_inc191_audit_firing(request)["code"]
        == "inc191_audit_request_invalid"
    )
    assert signed == []
    assert sent == []


def test_exact_allowlisted_c0_action_decision_reaches_signer_and_host(
    monkeypatch, runtime_surface
):
    signed = _enable_hermetic_signing(monkeypatch)
    sent = []

    def host(value):
        sent.append(deepcopy(value))
        return _host_receipt(value)

    monkeypatch.setattr(admission, "_request_fixed_host", host)
    request = _request("allowlisted-action")
    request["decision_before"] = {"action": "keep"}
    request["decision_after"] = {"action": "demote"}
    result = admission.consume_inc191_audit_firing(request)
    assert result["firing_admission"]["decision_before"] == {"action": "keep"}
    assert result["firing_admission"]["decision_after"] == {"action": "demote"}
    assert len(signed) == 1
    assert len(sent) == 1


@pytest.mark.parametrize(
    "decision_field,key,value",
    [
        ("decision_before", "api_key", "AKIA" + "0" * 16),
        ("decision_after", "access_key", "AKIA" + "0" * 16),
        ("decision_before", "provider_payload", "AKIA" + "0" * 16),
        ("decision_after", "bearer", "BearerCredentialMaterial"),
        ("decision_before", "oauth_access_token", "oauth-material"),
        ("decision_after", "action", "AKIA" + "0" * 16),
        ("decision_before", "action", {"api_key": "AKIA" + "0" * 16}),
        (
            "decision_after",
            "action",
            {"provider_payload": "AKIA" + "0" * 16},
        ),
    ],
)
def test_undeclared_decision_material_fails_before_snapshot_sign_or_host(
    monkeypatch, runtime_surface, decision_field, key, value
):
    canonicalized = []
    signed = _enable_hermetic_signing(monkeypatch)
    sent = []
    original_canonical = authority._canonical_authority_payload

    def canonical(item):
        canonicalized.append(deepcopy(item))
        return original_canonical(item)

    monkeypatch.setattr(authority, "_canonical_authority_payload", canonical)
    monkeypatch.setattr(
        admission, "_request_fixed_host", lambda item: sent.append(item)
    )
    request = _request(f"unsafe-decision-{key}")
    if key == "action":
        request[decision_field] = {key: value}
    else:
        request[decision_field][key] = value
    assert admission.consume_inc191_audit_firing(request) == {
        "outcome": "deny",
        "code": "inc191_audit_request_invalid",
    }
    assert canonicalized == []
    assert signed == []
    assert sent == []


@pytest.mark.parametrize("prefix", ["AKIA", "ASIA"])
@pytest.mark.parametrize(
    "field",
    [
        "target_ref",
        "target_cursor_before",
        "target_cursor_after",
        "target_read_result.receipt_ref",
        "independent_surfaces_checked",
        "owner_claim_refs",
        "independent_evidence_refs",
        "trigger_set",
        "panoramic_surfaces_checked",
    ],
)
def test_aws_access_key_shape_fails_every_safe_ref_before_snapshot_sign_or_host(
    monkeypatch, runtime_surface, prefix, field
):
    canonicalized = []
    signed = _enable_hermetic_signing(monkeypatch)
    sent = []
    original_canonical = authority._canonical_authority_payload

    def canonical(item):
        canonicalized.append(deepcopy(item))
        return original_canonical(item)

    monkeypatch.setattr(authority, "_canonical_authority_payload", canonical)
    monkeypatch.setattr(
        admission, "_request_fixed_host", lambda item: sent.append(item)
    )
    request = _request(f"unsafe-aws-ref-{field}")
    credential = prefix + "0" * 16
    if field == "target_read_result.receipt_ref":
        request["target_read_result"]["receipt_ref"] = credential
    elif field in {
        "independent_surfaces_checked",
        "owner_claim_refs",
        "independent_evidence_refs",
        "trigger_set",
        "panoramic_surfaces_checked",
    }:
        request[field][0] = credential
    else:
        request[field] = credential
    assert admission.consume_inc191_audit_firing(request) == {
        "outcome": "deny",
        "code": "inc191_audit_request_invalid",
    }
    assert canonicalized == []
    assert signed == []
    assert sent == []


def test_real_sshsig_caller_proof_uses_fixed_identity_namespace_and_temp_cleanup(
    monkeypatch, runtime_surface
):
    if not admission._SSH_KEYGEN.is_file():
        pytest.skip("ssh-keygen unavailable")
    runtime_surface.key_path.unlink()
    subprocess.run(
        [
            str(admission._SSH_KEYGEN),
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(runtime_surface.key_path),
        ],
        check=True,
        capture_output=True,
    )
    runtime_surface.key_path.chmod(0o600)
    config = admission._load_runtime_config()
    assert config is not None
    payload = b'{"authority_source":"test"}'
    signature = admission._sign_unsigned_request(payload, config)
    assert signature is not None
    public_key = (
        Path(f"{runtime_surface.key_path}.pub").read_text(encoding="ascii").strip()
    )
    allowed = runtime_surface.authority_dir / "allowed-callers"
    allowed.write_text(
        f'hermes_host_runtime namespaces="{admission.INC191_RUNTIME_SIGNATURE_NAMESPACE}" {public_key}\n',
        encoding="ascii",
    )
    signature_path = runtime_surface.authority_dir / "caller.sig"
    signature_path.write_text(signature, encoding="ascii")
    verified = subprocess.run(
        [
            str(admission._SSH_KEYGEN),
            "-Y",
            "verify",
            "-f",
            str(allowed),
            "-I",
            "hermes_host_runtime",
            "-n",
            admission.INC191_RUNTIME_SIGNATURE_NAMESPACE,
            "-s",
            str(signature_path),
        ],
        input=payload,
        capture_output=True,
    )
    assert verified.returncode == 0
    assert list(runtime_surface.authority_dir.glob(".inc191-sign-*")) == []


def test_sign_failure_timeout_and_oversize_are_sanitized_and_retryable(
    monkeypatch, runtime_surface
):
    monkeypatch.setattr(admission, "_validated_ssh_keygen", lambda: True)
    config = admission._load_runtime_config()
    assert config is not None

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(admission.subprocess, "run", timeout)
    assert admission._sign_unsigned_request(b"{}", config) is None

    def oversized(args, **kwargs):
        path = Path(f"{args[-1]}.sig")
        path.write_text("x" * (admission._MAX_SIGNATURE_BYTES + 1), encoding="ascii")
        path.chmod(0o600)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(admission.subprocess, "run", oversized)
    assert admission._sign_unsigned_request(b"{}", config) is None
    assert list(runtime_surface.authority_dir.glob(".inc191-sign-*")) == []


def test_pre_send_authentication_failure_releases_for_safe_retry(
    monkeypatch, runtime_surface
):
    suffix_attempt = 0
    attempted_firing_ids = []

    def random_bytes(length):
        nonlocal suffix_attempt
        if length == 16:
            suffix_attempt += 1
            return bytes([suffix_attempt]) * 16
        return b"\x99" * length

    monkeypatch.setattr(admission.os, "urandom", random_bytes)

    def sign(payload, config):
        unsigned = authority._parse_canonical_authority_payload(payload)
        attempted_firing_ids.append(unsigned["request"]["firing_id"])
        return None if len(attempted_firing_ids) == 1 else _DUMMY_PROOF

    sent = []
    monkeypatch.setattr(admission, "_sign_unsigned_request", sign)
    monkeypatch.setattr(
        admission,
        "_request_fixed_host",
        lambda wire: sent.append(wire) or _host_receipt(wire),
    )
    request = _request("pre-send-retry")
    assert admission.consume_inc191_audit_firing(request) == {
        "outcome": "deny",
        "code": "inc191_caller_authentication_unavailable",
    }
    assert admission.consume_inc191_audit_firing(request) == _audit_readback(
        sent[0]["request"]
    )
    assert len(sent) == 1
    assert attempted_firing_ids == [
        f"{GENERATION}:{'01' * 16}",
        f"{GENERATION}:{'02' * 16}",
    ]


def test_no_retry_after_socket_send_or_uncertain_response(monkeypatch, runtime_surface):
    _enable_hermetic_signing(monkeypatch)
    calls = []

    def uncertain(wire):
        calls.append(wire)
        raise RuntimeError("private transport detail")

    monkeypatch.setattr(admission, "_request_fixed_host", uncertain)
    request = _request("uncertain")
    assert admission.consume_inc191_audit_firing(request) == {
        "outcome": "deny",
        "code": "inc191_host_response_unavailable",
    }
    assert len(calls) == 1


def test_connect_failure_before_send_releases_for_retry(monkeypatch, runtime_surface):
    _enable_hermetic_signing(monkeypatch)
    calls = []

    def fail_then_succeed(wire):
        calls.append(wire)
        if len(calls) == 1:
            raise admission._HostTransportFailure(sent=False)
        return _host_receipt(wire)

    monkeypatch.setattr(admission, "_request_fixed_host", fail_then_succeed)
    request = _request("connect-retry")
    assert (
        admission.consume_inc191_audit_firing(request)["code"]
        == "inc191_host_verifier_unavailable"
    )
    assert admission.consume_inc191_audit_firing(request) == _audit_readback(
        calls[1]["request"]
    )
    assert len(calls) == 2
    assert calls[0]["request"]["firing_id"] != calls[1]["request"]["firing_id"]


def test_no_caller_field_influences_the_operation_owned_suffix(
    monkeypatch, runtime_surface
):
    monkeypatch.setattr(admission.os, "urandom", lambda length: b"\x44" * length)
    _enable_hermetic_signing(monkeypatch)
    observed = []
    monkeypatch.setattr(
        admission,
        "_request_fixed_host",
        lambda wire: observed.append(wire) or _host_receipt(wire),
    )
    assert admission.consume_inc191_audit_firing(_request("alpha"))["firing_id"] == (
        f"{GENERATION}:{'44' * 16}"
    )
    assert admission.consume_inc191_audit_firing(_request("beta"))["firing_id"] == (
        f"{GENERATION}:{'44' * 16}"
    )
    assert observed[0]["request"]["target_ref"] != observed[1]["request"]["target_ref"]


def test_concurrent_same_process_firing_is_fenced(monkeypatch, runtime_surface):
    monkeypatch.setattr(admission.os, "urandom", lambda length: b"\x33" * length)
    _enable_hermetic_signing(monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def host(wire):
        entered.set()
        assert release.wait(timeout=2)
        return _host_receipt(wire)

    monkeypatch.setattr(admission, "_request_fixed_host", host)
    request = _request("concurrent")
    results = []
    worker = threading.Thread(
        target=lambda: results.append(admission.consume_inc191_audit_firing(request))
    )
    worker.start()
    assert entered.wait(timeout=2)
    assert admission.consume_inc191_audit_firing(request) == {
        "outcome": "deny",
        "code": "inc191_audit_operation_in_flight",
    }
    release.set()
    worker.join(timeout=2)
    internal_request = {**request, "firing_id": f"{GENERATION}:{'33' * 16}"}
    assert results == [_audit_readback(internal_request)]


@pytest.mark.parametrize(
    "reason",
    [
        "caller_authentication_unavailable",
        "caller_authentication_failed",
        "runtime_revision_mismatch",
        "firing_generation_mismatch",
        "firing_ledger_capacity_reached",
        "firing_id_already_terminal",
        "authority_classification_invalid",
        "authority_classification_unavailable",
    ],
)
def test_signed_host_terminal_reason_is_final_without_resend(
    monkeypatch, runtime_surface, reason
):
    _enable_hermetic_signing(monkeypatch)
    calls = []
    monkeypatch.setattr(
        admission,
        "_request_fixed_host",
        lambda wire: calls.append(wire) or _host_receipt(wire, reason=reason),
    )
    request = _request(f"terminal-{reason}")
    result = admission.consume_inc191_audit_firing(request)
    assert result["reasons"] == [reason]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda receipt: receipt["binding"].update(consumed_once=1),
        lambda receipt: receipt["binding"].update(operation_nonce="0" * 64),
        lambda receipt: receipt["binding"].update(firing_id="inc191-g7:" + "0" * 32),
        lambda receipt: receipt["audit_readback"].update(model_invocations=True),
        lambda receipt: receipt["audit_readback"].update(visibility_debt=0),
        lambda receipt: receipt["audit_readback"].update(bounded_usage_delta=True),
        _mutate_to_known_usage,
        _mutate_to_impossible_admitted,
        lambda receipt: receipt["audit_readback"].update(
            receipt_result="NO_DELTA_NO_MODEL", no_delta=True
        ),
        lambda receipt: receipt["audit_readback"].update(audit_verdict_allowed=True),
        lambda receipt: receipt["audit_readback"].update(model_invocation_allowed=True),
        lambda receipt: receipt["audit_readback"].update(notification_allowed=True),
        lambda receipt: receipt["audit_readback"].update(
            reasons=[
                "THREAT_BOUNDARY_UNIMPLEMENTED_WITHOUT_HOST_INTEGRATION",
                "authority_classification_invalid",
            ]
        ),
        lambda receipt: receipt["audit_readback"]["firing_admission"].update(
            bounded_usage_delta=True
        ),
        lambda receipt: receipt["audit_readback"]["firing_admission"][
            "model_invocation"
        ].update(requested_if_admitted=1),
        lambda receipt: receipt["audit_readback"]["firing_admission"].update(
            firing_id="inc191-g7:" + "0" * 32
        ),
        lambda receipt: receipt["audit_readback"].update(
            reasons=["arbitrary_signed_reason"]
        ),
        lambda receipt: receipt["audit_readback"].update(reasons=["private-key-path"]),
    ],
)
def test_binding_type_bool_as_int_nested_firing_id_and_reason_tampering_rejected(
    monkeypatch, runtime_surface, mutate
):
    _enable_hermetic_signing(monkeypatch)

    def hostile(wire):
        receipt = _host_receipt(wire)
        mutate(receipt)
        return receipt

    monkeypatch.setattr(admission, "_request_fixed_host", hostile)
    assert admission.consume_inc191_audit_firing(_request("tamper")) == {
        "outcome": "deny",
        "code": "inc191_host_receipt_invalid",
    }


def test_actual_maestro_host_result_crosses_real_hermes_validation_path(
    monkeypatch, runtime_surface, request
):
    if not HOST_SERVICE_PATH.is_file() or not HOST_DIST_PATH.is_file():
        pytest.skip("exact Maestro host source/dist worktree not mounted")
    assert (
        hashlib.sha256(HOST_SERVICE_PATH.read_bytes()).hexdigest()
        == HOST_SERVICE_SHA256
    )
    assert hashlib.sha256(HOST_DIST_PATH.read_bytes()).hexdigest() == HOST_DIST_SHA256
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=HOST_SERVICE_PATH.parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == HOST_SERVICE_COMMIT

    runtime_surface.key_path.unlink()
    subprocess.run(
        [
            str(admission._SSH_KEYGEN),
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(runtime_surface.key_path),
        ],
        check=True,
        capture_output=True,
    )
    runtime_surface.key_path.chmod(0o600)

    oracle_root = Path(tempfile.mkdtemp(prefix="ha-cross-", dir="/tmp"))
    oracle_root.chmod(0o700)
    request.addfinalizer(lambda: shutil.rmtree(oracle_root, ignore_errors=True))
    oracle_authority = oracle_root / "authority"
    oracle_authority.mkdir(mode=0o700)
    authority_key = oracle_root / "authority-key"
    subprocess.run(
        [
            str(admission._SSH_KEYGEN),
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(authority_key),
        ],
        check=True,
        capture_output=True,
    )
    authority_key.chmod(0o600)
    runtime_public = (
        Path(f"{runtime_surface.key_path}.pub").read_text(encoding="ascii").strip()
    )
    runtime_allowed = oracle_root / "runtime-allowed-signers"
    runtime_allowed.write_text(
        f"hermes_host_runtime {runtime_public}\n", encoding="ascii"
    )
    runtime_allowed.chmod(0o600)
    socket_path = oracle_authority / "maestro-authority-v3.sock"
    state_path = oracle_authority / "decisions.json"
    launcher = oracle_root / "launch-oracle.mjs"
    launcher.write_text(
        """
import { pathToFileURL } from "node:url";
const [modulePath, socketPath, statePath, signingKeyPath, runtimeSigners, runtimeRevision, generation] = process.argv.slice(2);
const { createHermesProtectedAuthorityService } = await import(pathToFileURL(modulePath).href);
let stopping = false;
let service;
const stop = async () => { if (stopping) return; stopping = true; await service.stop(); process.exit(0); };
service = createHermesProtectedAuthorityService({
  socketPath, statePath, signingKeyPath, allowedRuntimeCommit: runtimeRevision,
  inc191AllowedRuntimeSignersPath: runtimeSigners, inc191Generation: generation,
  ioTimeoutMs: 3000,
}, {
  onInc191Classification: () => { setTimeout(() => { void stop(); }, 500); },
});
await service.start();
process.on("SIGTERM", () => { void stop(); });
process.on("SIGINT", () => { void stop(); });
setTimeout(() => { void stop(); }, 10000);
await new Promise(() => {});
""".strip()
        + "\n",
        encoding="ascii",
    )
    node_value = shutil.which("node")
    if node_value is None:
        pytest.skip("Node runtime for exact Maestro oracle unavailable")
    node = Path(node_value)
    oracle = subprocess.Popen(
        [
            str(node),
            str(launcher),
            str(HOST_DIST_PATH),
            str(socket_path),
            str(state_path),
            str(authority_key),
            str(runtime_allowed),
            RUNTIME_REVISION,
            GENERATION,
        ],
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if oracle.poll() is not None:
                pytest.fail("exact Maestro oracle exited before listening")
            try:
                if stat.S_ISSOCK(socket_path.lstat().st_mode):
                    break
            except FileNotFoundError:
                pass
            time.sleep(0.01)
        else:
            pytest.fail("exact Maestro oracle did not become reachable")

        authority_public = (
            Path(f"{authority_key}.pub").read_text(encoding="ascii").strip()
        )
        allowed_authority = (
            f"{authority.HERMES_PROTECTED_AUTHORITY_SIGNER_IDENTITY} "
            f'namespaces="{authority.HERMES_PROTECTED_AUTHORITY_SIGNATURE_NAMESPACE}" '
            f"{authority_public}\n"
        ).encode("ascii")
        monkeypatch.setattr(
            authority, "_fixed_authority_socket_path", lambda: socket_path
        )
        monkeypatch.setattr(
            authority, "_fixed_allowed_signers_content", lambda: allowed_authority
        )
        result = admission.consume_inc191_audit_firing(_request("actual-maestro"))
        assert "receipt_result" in result, result
        assert result["receipt_result"] == "MALFORMED_FIRING_REJECTED"
        assert result["bounded_usage_delta"] == "UNKNOWN"
        assert result["firing_admission"]["bounded_usage_delta"] == "UNKNOWN"
        assert result["reasons"] == [
            "THREAT_BOUNDARY_UNIMPLEMENTED_WITHOUT_HOST_INTEGRATION"
        ]
        assert result["firing_id"].startswith(f"{GENERATION}:")
    finally:
        try:
            oracle.wait(timeout=12)
        except subprocess.TimeoutExpired:
            pytest.fail("exact Maestro oracle did not self-stop")


def test_transport_rejects_signature_mismatch_after_send(monkeypatch):
    short_root = Path(tempfile.mkdtemp(prefix="ha-sig-", dir="/tmp"))
    socket_path = short_root / "maestro-authority-v3.sock"
    monkeypatch.setattr(authority, "_trusted_runtime_boundary", lambda: True)
    monkeypatch.setattr(authority, "_fixed_allowed_signers_content", lambda: b"pinned")
    monkeypatch.setattr(authority, "_fixed_authority_socket_path", lambda: socket_path)
    monkeypatch.setattr(
        authority, "_verify_sshsig_with_allowed_signers", lambda *args: False
    )
    ready = threading.Event()

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                incoming = bytearray()
                while b"\n" not in incoming:
                    incoming.extend(connection.recv(8192))
                envelope = authority._canonical_authority_payload({
                    "receipt": {},
                    "signature": _DUMMY_PROOF,
                })
                assert envelope is not None
                connection.sendall(envelope + b"\n")

    server = threading.Thread(target=serve)
    try:
        server.start()
        assert ready.wait(timeout=2)
        with pytest.raises(admission._HostTransportFailure) as caught:
            admission._request_fixed_host({"operation": "test"})
        server.join(timeout=2)
        assert not server.is_alive()
        assert caught.value.sent is True
    finally:
        server.join(timeout=2)
        shutil.rmtree(short_root, ignore_errors=True)


def test_authority_source_remains_the_independently_passed_c0():
    assert admission._authority_source() == {
        "repository": "maestro-kernel",
        "commit": "0c7ba204f210687c1d294592965929ab45294f8b",
        "contract": "INC191AuditFiringAdmissionC0.v1",
        "path": "scripts/ops/inc191_audit_firing_admission.py",
        "sha256": "829bb45928b02a5cdafa042ec6098020f8d5611d9aa7a4bf3af3435e77d94289",
    }
