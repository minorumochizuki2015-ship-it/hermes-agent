"""Crash, rollback, CAS, and privacy tests for plugin adoption."""

from __future__ import annotations

import base64
import errno
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from scripts import orch_next_hermes_plugin_adoption as executor
from tui_gateway import maestro_plugin_adoption_authority as authority


def _temporary_cli_spec(tmp_path: Path) -> executor._FixedCliSpec:
    root = tmp_path / "homebrew"
    bin_dir = root / "bin"
    target_dir = root / "lib"
    bin_dir.mkdir(parents=True)
    target_dir.mkdir()
    root.chmod(0o755)
    bin_dir.chmod(0o755)
    target_dir.chmod(0o755)
    target = target_dir / "tool"
    target.write_bytes(b"fixture-cli")
    target.chmod(0o755)
    link = bin_dir / "tool"
    link.symlink_to("../lib/tool")
    uid = os.getuid()
    gid = root.lstat().st_gid
    return executor._FixedCliSpec(
        name="fixture",
        link=link,
        target=target,
        link_target="../lib/tool",
        version_output=b"fixture 1.0.0\n",
        nodes=(
            executor._FixedCliNode(root, "directory", uid, gid, 0o755),
            executor._FixedCliNode(bin_dir, "directory", uid, gid, 0o755),
            executor._FixedCliNode(
                link, "symlink", uid, gid, stat.S_IMODE(link.lstat().st_mode),
                link_target="../lib/tool",
            ),
            executor._FixedCliNode(target_dir, "directory", uid, gid, 0o755),
            executor._FixedCliNode(
                target, "regular", uid, gid, 0o755, size=len(b"fixture-cli")
            ),
        ),
    )


def _compiled_cli_spec(
    tmp_path: Path, monkeypatch, source_text: str
) -> executor._FixedCliSpec:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    spec.target.unlink()
    source = tmp_path / "cli_fixture.c"
    source.write_text(source_text, encoding="ascii")
    subprocess.run(
        ("/usr/bin/clang", "-o", str(spec.target), str(source)),
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    target_info = spec.target.lstat()
    return replace(
        spec,
        nodes=(
            *spec.nodes[:-1],
            replace(
                spec.nodes[-1],
                uid=target_info.st_uid,
                gid=target_info.st_gid,
                mode=stat.S_IMODE(target_info.st_mode),
                size=target_info.st_size,
            ),
        ),
    )


def _completed(stdout: bytes, returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(("fixture",), returncode, stdout, b"")


def test_fixed_cli_admits_exact_chain_and_version(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    monkeypatch.setattr(
        executor,
        "_invoke_fixed_cli",
        lambda _spec, _args, **_kwargs: _completed(spec.version_output),
    )
    assert executor._admit_fixed_cli(
        spec, tmp_path / "transaction"
    ) == executor._capture_fixed_cli(spec)


@pytest.mark.parametrize("drift", ["escape", "extra_link", "mode"])
def test_fixed_cli_rejects_path_and_metadata_drift(
    tmp_path: Path, monkeypatch, drift: str
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    if drift == "escape":
        spec.link.unlink()
        spec.link.symlink_to("../../escape")
    elif drift == "extra_link":
        spec.target.unlink()
        spec.target.parent.rmdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        spec.target.parent.symlink_to(elsewhere)
    else:
        spec.target.chmod(0o775)
    with pytest.raises(executor.AdoptionError, match="host_cli_drift"):
        executor._capture_fixed_cli(spec)


def test_fixed_cli_rejects_version_and_during_command_identity_drift(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    monkeypatch.setattr(
        executor,
        "_invoke_fixed_cli",
        lambda _spec, _args, **_kwargs: _completed(b"fixture 2.0.0\n"),
    )
    with pytest.raises(executor.AdoptionError, match="host_cli_drift"):
        executor._admit_fixed_cli(spec, tmp_path / "transaction")

    calls = 0

    def drift_after_version(_spec, args, **_kwargs):
        nonlocal calls
        calls += 1
        if args != ("--version",):
            spec.target.chmod(0o775)
        return _completed(spec.version_output if calls == 1 else b"{}\n")

    monkeypatch.setattr(executor, "_invoke_fixed_cli", drift_after_version)
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter._cli = spec
    adapter._transaction_root = tmp_path / "transaction"
    with pytest.raises(executor.AdoptionError, match="host_cli_drift"):
        adapter._run(("plugin", "list", "--json"), json_output=True)


def test_fixed_cli_hardlink_pin_is_exact_and_cleaned(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    with executor._pinned_cli_executable(spec, identity, transaction) as pin:
        assert pin.read_bytes() == b"fixture-cli"
        assert pin.lstat().st_ino == spec.target.lstat().st_ino
        assert pin.lstat().st_nlink == identity.nlink + 1
    assert not (transaction / "cli-exec" / spec.name).exists()
    assert spec.target.lstat().st_nlink == identity.nlink


def test_fixed_cli_rejects_swap_before_hardlink(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    identity = executor._capture_fixed_cli(spec)[-1]
    real_link = os.link

    def swap_then_link(source, destination, **kwargs):
        original = spec.target.with_name("original")
        spec.target.rename(original)
        spec.target.write_bytes(b"swapped-cli")
        spec.target.chmod(0o755)
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(executor.os, "link", swap_then_link)
    with pytest.raises(executor.AdoptionError, match="host_cli_pin_drift"):
        with executor._pinned_cli_executable(spec, identity, tmp_path / "transaction"):
            raise AssertionError("swapped inode must never execute")
    assert not (tmp_path / "transaction" / "cli-exec" / spec.name).exists()


def test_fixed_cli_pin_survives_source_path_swap_after_link(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    identity = executor._capture_fixed_cli(spec)[-1]
    with executor._pinned_cli_executable(
        spec, identity, tmp_path / "transaction"
    ) as pin:
        original = spec.target.with_name("original")
        spec.target.rename(original)
        spec.target.write_bytes(b"swapped-cli")
        spec.target.chmod(0o755)
        assert pin.read_bytes() == b"fixture-cli"
        assert pin.lstat().st_ino == identity.inode
    assert executor._capture_fixed_cli(spec)[-1] != identity


def test_fixed_cli_rejects_wrong_hardlink_inode(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    identity = executor._capture_fixed_cli(spec)[-1]
    wrong = spec.target.with_name("wrong")
    wrong.write_bytes(b"fixture-cli")
    wrong.chmod(0o755)
    real_link = os.link
    monkeypatch.setattr(
        executor.os,
        "link",
        lambda _source, destination, **kwargs: real_link(
            wrong, destination, **kwargs
        ),
    )
    with pytest.raises(executor.AdoptionError, match="host_cli_pin_drift"):
        with executor._pinned_cli_executable(spec, identity, tmp_path / "transaction"):
            raise AssertionError("wrong inode must never execute")


def test_fixed_cli_copy_fallback_is_content_bound_and_cleaned(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    identity = executor._capture_fixed_cli(spec)[-1]

    def cross_device(*_args, **_kwargs):
        raise OSError(errno.EXDEV, "fixture cross-device")

    monkeypatch.setattr(executor.os, "link", cross_device)
    transaction = tmp_path / "transaction"
    with executor._pinned_cli_executable(spec, identity, transaction) as pin:
        assert pin.read_bytes() == spec.target.read_bytes()
        assert pin.lstat().st_ino != identity.inode
        assert stat.S_IMODE(pin.lstat().st_mode) == 0o500
        assert pin.lstat().st_nlink == 1
    assert not (transaction / "cli-exec" / spec.name).exists()


@pytest.mark.parametrize("outcome", ["error", "signal"])
def test_fixed_cli_pin_cleanup_on_child_error_or_signal(
    tmp_path: Path, monkeypatch, outcome: str
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    mask_calls: list[int] = []

    def signal_mask(operation, _mask):
        mask_calls.append(operation)
        return frozenset()

    monkeypatch.setattr(executor.signal, "pthread_sigmask", signal_mask)

    def child(*_args, **_kwargs):
        if outcome == "error":
            raise RuntimeError("fixture child error")
        return _completed(b"", returncode=-15)

    monkeypatch.setattr(executor, "_run_pinned_child", child)
    if outcome == "error":
        with pytest.raises(RuntimeError, match="fixture child error"):
            executor._invoke_fixed_cli(
                spec,
                ("--version",),
                expected_final=identity,
                transaction_root=transaction,
            )
    else:
        completed = executor._invoke_fixed_cli(
            spec,
            ("--version",),
            expected_final=identity,
            transaction_root=transaction,
        )
        assert completed.returncode == -15
    assert not (transaction / "cli-exec" / spec.name).exists()
    assert mask_calls == [executor.signal.SIG_BLOCK, executor.signal.SIG_SETMASK]


def test_fixed_cli_real_child_accepts_term_and_pin_cleanup_restores_parent_mask(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _compiled_cli_spec(
        tmp_path,
        monkeypatch,
        "#include <unistd.h>\nint main(void) { sleep(5); return 0; }\n",
    )
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    original_mask = executor.signal.pthread_sigmask(
        executor.signal.SIG_BLOCK, executor._PROTECTED_PIN_SIGNALS
    )
    killed: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def observed_killpg(pid, signal_number):
        killed.append((pid, signal_number))
        real_killpg(pid, signal_number)

    monkeypatch.setattr(executor, "_CHILD_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(executor, "_CHILD_TERM_GRACE_SECONDS", 1.0)
    monkeypatch.setattr(executor.os, "killpg", observed_killpg)
    try:
        with pytest.raises(executor.AdoptionError, match="host_cli_operation_failed"):
            executor._invoke_fixed_cli(
                spec,
                ("5",),
                expected_final=identity,
                transaction_root=transaction,
            )
        assert [signal_number for _pid, signal_number in killed] == [
            executor.signal.SIGTERM,
            0,
        ]
        assert not (transaction / "cli-exec" / spec.name).exists()
        assert not (tmp_path / "mutation-complete").exists()
        assert executor.signal.pthread_sigmask(
            executor.signal.SIG_BLOCK, set()
        ) == set(executor._PROTECTED_PIN_SIGNALS) | set(original_mask)
    finally:
        executor.signal.pthread_sigmask(executor.signal.SIG_SETMASK, original_mask)


def test_fixed_cli_timeout_escalates_to_sigkill_and_cleans_pin(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _compiled_cli_spec(
        tmp_path,
        monkeypatch,
        "#include <signal.h>\n#include <unistd.h>\n"
        "int main(void) { signal(SIGTERM, SIG_IGN); sleep(5); return 0; }\n",
    )
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    killed: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def observed_killpg(pid, signal_number):
        killed.append((pid, signal_number))
        real_killpg(pid, signal_number)

    monkeypatch.setattr(executor, "_CHILD_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(executor, "_CHILD_TERM_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(
        executor.os, "killpg", observed_killpg
    )
    with pytest.raises(executor.AdoptionError, match="host_cli_operation_failed"):
        executor._invoke_fixed_cli(
            spec,
            (),
            expected_final=identity,
            transaction_root=transaction,
        )
    observed_signals = [sig for _pid, sig in killed]
    assert observed_signals[0] == executor.signal.SIGTERM
    assert observed_signals[-1] == executor.signal.SIGKILL
    assert set(observed_signals[1:-1]) == {0}
    assert not (transaction / "cli-exec" / spec.name).exists()


def test_fixed_cli_stdout_flood_is_bounded_killed_and_never_completes(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _compiled_cli_spec(
        tmp_path,
        monkeypatch,
        "#include <fcntl.h>\n#include <string.h>\n#include <unistd.h>\n"
        "int main(int argc, char **argv) { char b[65536]; memset(b, 'x', sizeof(b)); "
        "for (int i=0; i<256; i++) write(1, b, sizeof(b)); "
        "int f=open(argv[1], O_CREAT|O_WRONLY, 0600); if (f>=0) close(f); return 0; }\n",
    )
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    marker = tmp_path / "mutation-complete"
    monkeypatch.setattr(executor, "_MAX_COMMAND_OUTPUT", 128 * 1024)
    with pytest.raises(executor.AdoptionError, match="host_cli_operation_failed"):
        executor._invoke_fixed_cli(
            spec,
            (str(marker),),
            expected_final=identity,
            transaction_root=transaction,
        )
    assert not marker.exists()
    assert not (transaction / "cli-exec" / spec.name).exists()


def test_fixed_cli_real_stderr_write_succeeds_and_cleans_pin(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _compiled_cli_spec(
        tmp_path,
        monkeypatch,
        "#include <unistd.h>\nint main(void) { return write(2, \"diagnostic\\n\", 11) == 11 ? 0 : 1; }\n",
    )
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    completed = executor._invoke_fixed_cli(
        spec, (), expected_final=identity, transaction_root=transaction
    )
    assert completed.returncode == 0
    assert completed.stdout == b""
    assert not (transaction / "cli-exec" / spec.name).exists()


def test_fixed_cli_child_defaults_and_unblocks_sigpipe(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _compiled_cli_spec(
        tmp_path,
        monkeypatch,
        "#include <signal.h>\n"
        "int main(void) { struct sigaction a; sigset_t m; "
        "if (sigaction(SIGPIPE, 0, &a) || a.sa_handler != SIG_DFL) return 2; "
        "if (pthread_sigmask(SIG_BLOCK, 0, &m) || sigismember(&m, SIGPIPE)) return 3; "
        "return 0; }\n",
    )
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    original_handler = executor.signal.getsignal(executor.signal.SIGPIPE)
    original_mask = executor.signal.pthread_sigmask(
        executor.signal.SIG_BLOCK, {executor.signal.SIGPIPE}
    )
    try:
        executor.signal.signal(executor.signal.SIGPIPE, executor.signal.SIG_IGN)
        expected_mask = executor.signal.pthread_sigmask(
            executor.signal.SIG_BLOCK, set()
        )
        completed = executor._invoke_fixed_cli(
            spec, (), expected_final=identity, transaction_root=transaction
        )
        assert completed.returncode == 0
        assert executor.signal.getsignal(executor.signal.SIGPIPE) is executor.signal.SIG_IGN
        assert (
            executor.signal.pthread_sigmask(executor.signal.SIG_BLOCK, set())
            == expected_mask
        )
    finally:
        executor.signal.pthread_sigmask(executor.signal.SIG_SETMASK, original_mask)
        executor.signal.signal(executor.signal.SIGPIPE, original_handler)
    assert not (transaction / "cli-exec" / spec.name).exists()


def test_fixed_cli_detached_stdout_holder_cannot_extend_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _compiled_cli_spec(
        tmp_path,
        monkeypatch,
        "#include <stdlib.h>\n#include <unistd.h>\n"
        "int main(void) { pid_t p=fork(); if (p<0) return 2; "
        "if (p==0) { setsid(); sleep(1); _exit(0); } return 0; }\n",
    )
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    monkeypatch.setattr(executor, "_CHILD_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(executor, "_CHILD_TERM_GRACE_SECONDS", 0.05)
    started = executor.time.monotonic()
    with pytest.raises(executor.AdoptionError, match="host_cli_operation_failed"):
        executor._invoke_fixed_cli(
            spec, (), expected_final=identity, transaction_root=transaction
        )
    assert executor.time.monotonic() - started < 0.5
    assert not (transaction / "cli-exec" / spec.name).exists()


def test_fixed_cli_detached_stdout_flood_cannot_reset_terminal_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    marker = tmp_path / "detached-flood-completed"
    pid_file = tmp_path / "detached-flood.pid"
    spec = _compiled_cli_spec(
        tmp_path,
        monkeypatch,
        "#include <fcntl.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <unistd.h>\n"
        "int main(int argc, char **argv) { pid_t p=fork(); if (p<0) return 2; "
        "if (p==0) { char b[65536] = {0}; setsid(); "
        "int pf=open(argv[2], O_WRONLY|O_CREAT, 0600); "
        "if (pf>=0) { dprintf(pf, \"%d\", getpid()); close(pf); } "
        "for (;;) { if (write(1, b, sizeof b) < 0) break; } "
        "int f=open(argv[1], O_WRONLY|O_CREAT, 0600); "
        "if (f>=0) { write(f, \"done\", 4); close(f); } _exit(0); } return 0; }\n",
    )
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    protected = {
        executor.signal.SIGHUP,
        executor.signal.SIGINT,
        executor.signal.SIGQUIT,
        executor.signal.SIGTERM,
    }
    original_mask = executor.signal.pthread_sigmask(
        executor.signal.SIG_BLOCK, protected
    )
    try:
        expected_mask = executor.signal.pthread_sigmask(
            executor.signal.SIG_BLOCK, set()
        )
        monkeypatch.setattr(executor, "_MAX_COMMAND_OUTPUT", 4096)
        monkeypatch.setattr(executor, "_CHILD_TIMEOUT_SECONDS", 0.5)
        monkeypatch.setattr(executor, "_CHILD_TERM_GRACE_SECONDS", 0.05)
        started = executor.time.monotonic()
        with pytest.raises(executor.AdoptionError, match="host_cli_operation_failed"):
            executor._invoke_fixed_cli(
                spec,
                (str(marker), str(pid_file)),
                expected_final=identity,
                transaction_root=transaction,
            )
        assert executor.time.monotonic() - started < 1.0
        assert (
            executor.signal.pthread_sigmask(executor.signal.SIG_BLOCK, set())
            == expected_mask
        )
    finally:
        executor.signal.pthread_sigmask(executor.signal.SIG_SETMASK, original_mask)
    descendant_pid = int(pid_file.read_text(encoding="ascii"))
    reaction_deadline = executor.time.monotonic() + 0.5
    while executor.time.monotonic() < reaction_deadline:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            break
        executor.time.sleep(0.01)
    else:
        pytest.fail("detached SIGPIPE fixture remained alive after pipe closure")
    pid_file.unlink()
    assert not marker.exists()
    assert not (transaction / "cli-exec" / spec.name).exists()


def test_fixed_cli_cleanup_refuses_substituted_pin(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _temporary_cli_spec(tmp_path)
    monkeypatch.setattr(executor, "_HOMEBREW_ROOT", tmp_path / "homebrew")
    identity = executor._capture_fixed_cli(spec)[-1]
    transaction = tmp_path / "transaction"
    with pytest.raises(executor.AdoptionError, match="host_cli_pin_cleanup_failed"):
        with executor._pinned_cli_executable(spec, identity, transaction) as pin:
            pin.unlink()
            pin.write_bytes(b"substitution")
            pin.chmod(0o500)
    assert (transaction / "cli-exec" / spec.name / "executable").exists()


def _receipt(request: dict) -> dict:
    actual = request["actual"]
    plan = request["plan"]
    return {
        "outcome": "allow",
        "code": "plugin_adoption_allowed",
        "decision_id": actual["decision_id"],
        "transaction_id": actual["transaction_id"],
        "authority_owner": authority.AUTHORITY_OWNER,
        "authority_bundle_version": authority.AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": authority.AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": authority.AUTHORITY_CONSUMER,
        "contract_id": authority.CONTRACT_ID,
        "contract_version": authority.CONTRACT_VERSION,
        "contract_digest": authority.CONTRACT_DIGEST,
        "operation": authority.OPERATION,
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
        "request_digest": hashlib.sha256(authority.canonical_bytes(request)).hexdigest(),
    }


def _authority_result(request: dict, *, now: float):
    envelope = authority.canonical_bytes({
        "receipt": _receipt(request),
        "signature": "-----BEGIN SSH SIGNATURE-----\nfixture\n-----END SSH SIGNATURE-----\n",
    })
    return authority.verify_plugin_adoption_envelope(
        request_bytes=authority.canonical_bytes(request),
        envelope_bytes=envelope,
        now=now,
    )


def _terminal_receipt(request: dict) -> dict:
    actual = request["actual"]
    plan = request["plan"]
    return {
        "outcome": "allow",
        "code": authority.TERMINAL_ALLOW_CODE,
        "decision_id": actual["decision_id"],
        "transaction_id": actual["transaction_id"],
        "authority_owner": authority.AUTHORITY_OWNER,
        "authority_bundle_version": authority.AUTHORITY_BUNDLE_VERSION,
        "authority_bundle_digest": authority.AUTHORITY_BUNDLE_DIGEST,
        "authority_consumer": authority.AUTHORITY_CONSUMER,
        "contract_id": authority.TERMINAL_CONTRACT_ID,
        "contract_version": authority.TERMINAL_CONTRACT_VERSION,
        "contract_digest": authority.TERMINAL_CONTRACT_DIGEST,
        "operation": authority.TERMINAL_OPERATION,
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
        "request_digest": hashlib.sha256(authority.canonical_bytes(request)).hexdigest(),
    }


def _terminal_authority_result(
    request: dict,
    *,
    now: float,
    prepared_replay: bool = False,
):
    envelope = authority.canonical_bytes({
        "receipt": _terminal_receipt(request),
        "signature": "-----BEGIN SSH SIGNATURE-----\nfixture\n-----END SSH SIGNATURE-----\n",
    })
    return authority.verify_plugin_adoption_terminal_envelope(
        request_bytes=authority.canonical_bytes(request),
        envelope_bytes=envelope,
        now=(
            float(request["actual"]["issued_at"]) + 0.001
            if prepared_replay
            else now
        ),
    )


class ReplayAwareTerminalIssuer:
    """One-budget issuer fixture with byte-stable completed-envelope replay."""

    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.consumed_budget = 0
        self.request_bytes: bytes | None = None
        self.envelope_bytes: bytes | None = None
        self.decision_ids: set[str] = set()
        self.transaction_ids: set[str] = set()
        self.envelope_digests: set[str] = set()

    def __call__(self, request, *, now, prepared_replay=False):
        request_bytes = authority.canonical_bytes(request)
        self.calls.append(request_bytes)
        if self.request_bytes is None:
            if (
                prepared_replay
                and now >= float(request["actual"]["expires_at"])
            ):
                raise authority.PluginAdoptionAuthorityError(
                    "authority_replay_unrecoverable"
                )
            self.consumed_budget += 1
            self.request_bytes = request_bytes
            self.decision_ids.add(request["actual"]["decision_id"])
            self.transaction_ids.add(request["actual"]["transaction_id"])
            self.envelope_bytes = authority.canonical_bytes({
                "receipt": _terminal_receipt(request),
                "signature": (
                    "-----BEGIN SSH SIGNATURE-----\n"
                    "single-use-fixture\n"
                    "-----END SSH SIGNATURE-----\n"
                ),
            })
            self.envelope_digests.add(
                hashlib.sha256(self.envelope_bytes).hexdigest()
            )
        elif request_bytes != self.request_bytes:
            raise authority.PluginAdoptionAuthorityError(
                "authority_replay_request_mismatch"
            )
        assert self.envelope_bytes is not None
        return authority.verify_plugin_adoption_terminal_envelope(
            request_bytes=request_bytes,
            envelope_bytes=self.envelope_bytes,
            now=(
                float(request["actual"]["issued_at"]) + 0.001
                if prepared_replay
                else now
            ),
        )


class ReplayAwareOrdinaryIssuer:
    """One-budget ordinary issuer with exact prepared-request replay."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, bool]] = []
        self.consumed_budget = 0
        self.request_bytes: bytes | None = None
        self.envelope_bytes: bytes | None = None

    def __call__(self, request, *, now, prepared_replay=False):
        request_bytes = authority.canonical_bytes(request)
        self.calls.append((request_bytes, prepared_replay))
        if self.request_bytes is None:
            assert prepared_replay is False
            self.consumed_budget += 1
            self.request_bytes = request_bytes
            self.envelope_bytes = authority.canonical_bytes({
                "receipt": _receipt(request),
                "signature": (
                    "-----BEGIN SSH SIGNATURE-----\n"
                    "ordinary-single-use-fixture\n"
                    "-----END SSH SIGNATURE-----\n"
                ),
            })
        else:
            assert prepared_replay is True
            if request_bytes != self.request_bytes:
                raise authority.PluginAdoptionAuthorityError(
                    "authority_replay_request_mismatch"
                )
        assert self.envelope_bytes is not None
        return authority.verify_plugin_adoption_envelope(
            request_bytes=request_bytes,
            envelope_bytes=self.envelope_bytes,
            now=float(request["actual"]["issued_at"]) + 0.001,
        )


class TerminalPublicationFault:
    """Inject one write-boundary fault while recording visible-path exposure."""

    def __init__(
        self,
        *,
        target_leaves: set[str],
        visible_path: Path,
        fault: str,
    ) -> None:
        self.target_leaves = target_leaves
        self.visible_path = visible_path
        self.fault = fault
        self.target_descriptors: set[int] = set()
        self.visible_during_write: list[bool] = []
        self.write_calls = 0

    def install(self, monkeypatch) -> None:
        original_open = executor.os.open
        original_write = executor.os.write
        original_fsync = executor.os.fsync
        original_close = executor.os.close

        def open_(*args, **kwargs):
            path = os.fspath(args[0])
            flags = args[1]
            targeted = (
                Path(path).name in self.target_leaves
                and flags & os.O_WRONLY
                and flags & os.O_CREAT
            )
            if targeted and self.fault == "open_error":
                raise OSError(errno.EIO, "injected publication open failure")
            descriptor = original_open(*args, **kwargs)
            if targeted:
                self.target_descriptors.add(descriptor)
            return descriptor

        def write_(descriptor: int, content: bytes) -> int:
            if descriptor not in self.target_descriptors:
                return original_write(descriptor, content)
            self.write_calls += 1
            self.visible_during_write.append(
                self.visible_path.exists() or self.visible_path.is_symlink()
            )
            if self.fault == "crash_before_write":
                raise executor.InjectedCrash(self.fault)
            if self.fault in {"crash_after_partial", "write_error"}:
                written = original_write(descriptor, content[:17])
                if self.fault == "crash_after_partial":
                    raise executor.InjectedCrash(self.fault)
                raise OSError(errno.EIO, "injected publication write failure")
            if self.fault == "short_write":
                return original_write(descriptor, content[:17])
            return original_write(descriptor, content)

        def fsync_(descriptor: int) -> None:
            if (
                descriptor in self.target_descriptors
                and self.fault == "file_fsync_error"
            ):
                raise OSError(errno.EIO, "injected publication fsync failure")
            original_fsync(descriptor)

        def close_(descriptor: int) -> None:
            try:
                original_close(descriptor)
            finally:
                self.target_descriptors.discard(descriptor)

        monkeypatch.setattr(executor.os, "open", open_)
        monkeypatch.setattr(executor.os, "write", write_)
        monkeypatch.setattr(executor.os, "fsync", fsync_)
        monkeypatch.setattr(executor.os, "close", close_)


_PREPARED_TEMP_LEAF = ".terminal-journal.prepared.tmp"
_PREPARED_BACKUP_LEAF = ".terminal-journal.prepared"
_STAGE_TEMP_LEAF = ".terminal-journal.stage.tmp"


def _before(host: str) -> executor.HostState:
    quarantine_entries = ()
    cache_digest = executor.PREDECESSOR_BUNDLE_DIGEST
    if host == "claude":
        cache_digest = executor.CLAUDE_RESIDUAL_PREDECESSOR_DIGEST
        quarantine_entries = (
            executor.QuarantineEntry(
                handle=executor.TARGET_CACHE_HANDLES[executor.PREDECESSOR_VERSION],
                version=executor.PREDECESSOR_VERSION,
                cache_digest=executor.CLAUDE_RESIDUAL_PREDECESSOR_DIGEST,
                full_digest="a" * 64,
                identity_digest="b" * 64,
                in_use_present=True,
            ),
            executor.QuarantineEntry(
                handle=executor.TARGET_CACHE_HANDLES[executor.CLAUDE_RESIDUE_VERSION],
                version=executor.CLAUDE_RESIDUE_VERSION,
                cache_digest=executor.CLAUDE_RESIDUE_OPAQUE_DIGEST,
                full_digest="c" * 64,
                identity_digest="d" * 64,
                in_use_present=False,
            ),
        )
    return executor.HostState(
        host=host,
        marketplace_present=True,
        marketplace_digest=executor._predecessor_marketplace_digest(),
        marketplace_binding_digest=executor._predecessor_binding_digest(),
        plugin_present=True,
        plugin_version=executor.PREDECESSOR_VERSION,
        active=True,
        cache_digest=cache_digest,
        quarantine_entries=quarantine_entries,
    )


def test_successor_cache_handle_and_previous_terminal_binding_are_fixed() -> None:
    assert getattr(authority, "ORDINARY_APPLY_PLUGIN_VERSION", None) == "0.1.49"
    assert authority.PLUGIN_VERSION == authority.ORDINARY_APPLY_PLUGIN_VERSION
    assert authority.PREVIOUS_TERMINAL_PLUGIN_VERSION == "0.1.42"
    assert executor.TARGET_CACHE_VERSIONS[-2:] == ("0.1.48", "0.1.49")
    assert executor.TARGET_CACHE_HANDLES["0.1.26"] == "target-cache-v026"
    assert executor.TARGET_CACHE_HANDLES["0.1.35"] == "target-cache-v035"
    assert executor.TARGET_CACHE_HANDLES["0.1.36"] == "target-cache-v036"
    assert executor.TARGET_CACHE_HANDLES["0.1.37"] == "target-cache-v037"
    assert executor.TARGET_CACHE_HANDLES["0.1.38"] == "target-cache-v038"
    assert executor.TARGET_CACHE_HANDLES["0.1.39"] == "target-cache-v039"
    assert executor.TARGET_CACHE_HANDLES["0.1.40"] == "target-cache-v040"
    assert executor.TARGET_CACHE_HANDLES["0.1.41"] == "target-cache-v041"
    assert executor.TARGET_CACHE_HANDLES["0.1.42"] == "target-cache-v042"
    assert executor.TARGET_CACHE_HANDLES["0.1.43"] == "target-cache-v043"
    assert executor.TARGET_CACHE_HANDLES["0.1.44"] == "target-cache-v044"
    assert executor.TARGET_CACHE_HANDLES["0.1.45"] == "target-cache-v045"
    assert executor.TARGET_CACHE_HANDLES["0.1.46"] == "target-cache-v046"
    assert executor.TARGET_CACHE_HANDLES["0.1.47"] == "target-cache-v047"
    assert executor.TARGET_CACHE_HANDLES["0.1.48"] == "target-cache-v048"
    assert executor.TARGET_CACHE_HANDLES["0.1.49"] == "target-cache-v049"


def _after(host: str) -> executor.HostState:
    return executor.HostState(
        host=host,
        marketplace_present=True,
        marketplace_digest="7" * 64,
        marketplace_binding_digest=executor._candidate_binding_digest(
            source_revision="1" * 40,
            source_bundle_digest="2" * 64,
            marketplace_digest="7" * 64,
        ),
        plugin_present=True,
        plugin_version=authority.PLUGIN_VERSION,
        active=True,
        cache_digest="9" * 64,
    )


def test_residual_host_state_round_trips_without_raw_host_material() -> None:
    state = _before("claude")
    assert executor.CLAUDE_RESIDUE_OPAQUE_DIGEST == (
        "7aeec22ebd07df360afab3ca35a37560607e893108df41ffceb44ad8a3466687"
    )
    assert state.quarantine_entries[1].version == "0.1.15"
    assert not state.quarantine_entries[1].in_use_present
    projection = state.projection()
    assert executor.HostState.from_projection(
        projection, expected_host="claude"
    ) == state
    serialized = executor._json_bytes(projection).decode("ascii")
    for forbidden in ("/Users/", "path", "device", "inode", "command", "config"):
        assert forbidden not in serialized
    assert projection["quarantine_entries"] == [
        entry.projection() for entry in state.quarantine_entries
    ]


def test_residual_host_state_rejects_liar_handle_and_digest() -> None:
    projection = _before("claude").projection()
    projection["quarantine_entries"][0]["handle"] = "../target-cache-v013"
    with pytest.raises(
        executor.AdoptionError, match="journal_state_projection_invalid"
    ):
        executor.HostState.from_projection(projection, expected_host="claude")

    state = _before("claude")
    liar = replace(
        state,
        quarantine_entries=(
            state.quarantine_entries[0],
            replace(state.quarantine_entries[1], cache_digest="e" * 64),
        ),
    )
    with pytest.raises(
        executor.AdoptionError, match="before_state_not_exactly_reversible"
    ):
        executor._require_reversible_before_states((_before("codex"), liar))


def _residual_cache_adapter(tmp_path: Path, monkeypatch):
    root = tmp_path / "transaction"
    root.mkdir(mode=0o700)
    cache = tmp_path / "cache" / authority.PLUGIN_VERSION
    cache.parent.mkdir(mode=0o700, parents=True)
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._transaction_root = root
    adapter._cache = cache
    monkeypatch.setattr(executor, "_validate_fixed_host_chain", lambda *_a, **_k: None)
    for version, content, marker in (
        (executor.PREDECESSOR_VERSION, b"drifted-predecessor", True),
        (executor.CLAUDE_RESIDUE_VERSION, b"residue", False),
    ):
        leaf = cache.parent / version
        leaf.mkdir(mode=0o700)
        payload = leaf / "payload"
        payload.write_bytes(content)
        payload.chmod(0o600)
        if marker:
            in_use = leaf / ".in_use"
            in_use.write_bytes(b"active")
            in_use.chmod(0o600)
    entries = tuple(
        adapter._entry_for_path(
            cache.parent / version,
            version=version,
            handle=executor.TARGET_CACHE_HANDLES[version],
        )
        for version in (
            executor.PREDECESSOR_VERSION,
            executor.CLAUDE_RESIDUE_VERSION,
        )
    )
    before = replace(
        _before("claude"),
        cache_digest=entries[0].cache_digest,
        quarantine_entries=entries,
    )
    return adapter, before


def test_quarantine_and_restore_preserve_exact_directory_identities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, before = _residual_cache_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(
        executor,
        "CLAUDE_RESIDUAL_PREDECESSOR_DIGEST",
        before.quarantine_entries[0].cache_digest,
    )
    monkeypatch.setattr(
        executor,
        "CLAUDE_RESIDUE_OPAQUE_DIGEST",
        before.quarantine_entries[1].cache_digest,
    )
    cache_parent = adapter._cache.parent

    adapter._quarantine_before(before)
    assert not (cache_parent / executor.PREDECESSOR_VERSION).exists()
    assert not (cache_parent / executor.CLAUDE_RESIDUE_VERSION).exists()
    assert {
        path.name for path in adapter._quarantine_root().iterdir()
    } == {entry.handle for entry in before.quarantine_entries}

    adapter._registry_is_exact_predecessor = lambda: True
    adapter._restore_quarantine(before)
    for entry in before.quarantine_entries:
        assert adapter._entry_for_path(
            cache_parent / entry.version,
            version=entry.version,
            handle=entry.handle,
        ) == entry
    assert list(adapter._quarantine_root().iterdir()) == []


def test_claude_install_skips_redundant_enable_when_install_is_active(
    tmp_path: Path,
) -> None:
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._transaction_root = tmp_path / "transaction"
    calls: list[tuple[str, ...]] = []

    def run(args: tuple[str, ...], *, json_output: bool = False):
        calls.append(args)
        if json_output:
            return {
                "plugins": [{
                    "enabled": True,
                    "id": (
                        f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}"
                    ),
                    "version": authority.PLUGIN_VERSION,
                }]
            }
        return None

    adapter._run = run
    marketplace = adapter._stage_marketplace_root()
    adapter._install_from(marketplace)
    assert calls == [
        ("plugin", "marketplace", "add", str(marketplace), "--scope", "user"),
        ("plugin", "install", f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}", "--scope", "user"),
        ("plugin", "list", "--json"),
    ]
    assert executor._generated_candidate_quarantine_handle() == "generated-cache-v049"


def test_claude_install_enables_only_an_inactive_exact_candidate(
    tmp_path: Path,
) -> None:
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._transaction_root = tmp_path / "transaction"
    calls: list[tuple[str, ...]] = []

    def run(args: tuple[str, ...], *, json_output: bool = False):
        calls.append(args)
        if json_output:
            return {
                "plugins": [{
                    "enabled": False,
                    "id": f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}",
                    "version": authority.PLUGIN_VERSION,
                }]
            }
        return None

    adapter._run = run
    adapter._install_from(adapter._stage_marketplace_root())
    assert calls[-1] == (
        "plugin",
        "enable",
        f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}",
        "--scope",
        "user",
    )


def test_claude_rollback_quarantines_exact_orphaned_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, before = _residual_cache_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(
        executor,
        "CLAUDE_RESIDUAL_PREDECESSOR_DIGEST",
        before.quarantine_entries[0].cache_digest,
    )
    monkeypatch.setattr(
        executor,
        "CLAUDE_RESIDUE_OPAQUE_DIGEST",
        before.quarantine_entries[1].cache_digest,
    )
    adapter._quarantine_before(before)
    adapter._registry_is_exact_predecessor = lambda: True

    stage = adapter._stage_root()
    stage.mkdir(mode=0o700, parents=True)
    payload = stage / "payload"
    payload.write_bytes(b"candidate")
    payload.chmod(0o600)
    candidate = adapter._cache
    shutil.copytree(stage, candidate)
    marker = candidate / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"1786320000000")
    marker.chmod(0o644)
    os.chown(marker, os.getuid(), os.getgid())

    adapter._restore_quarantine(before)

    generated = (
        adapter._quarantine_root()
        / executor._generated_candidate_quarantine_handle()
    )
    assert not candidate.exists()
    assert generated.is_dir()
    assert (generated / executor.distribution.ORPHANED_INSTALLED_MARKER).is_file()
    for entry in before.quarantine_entries:
        assert adapter._entry_for_path(
            adapter._cache.parent / entry.version,
            version=entry.version,
            handle=entry.handle,
        ) == entry


def test_failed_candidate_quarantine_rejects_invalid_orphan_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, _before_state = _residual_cache_adapter(tmp_path, monkeypatch)
    stage = adapter._stage_root()
    stage.mkdir(mode=0o700, parents=True)
    payload = stage / "payload"
    payload.write_bytes(b"candidate")
    payload.chmod(0o600)
    shutil.copytree(stage, adapter._cache)
    marker = adapter._cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"not-a-timestamp")
    marker.chmod(0o644)

    with pytest.raises(executor.AdoptionError, match="generated_candidate_drift"):
        adapter._quarantine_failed_candidate()
    assert adapter._cache.is_dir()
    assert not (
        adapter._quarantine_root()
        / executor._generated_candidate_quarantine_handle()
    ).exists()


def test_failed_candidate_quarantine_rejects_in_place_payload_rewrite_between_digests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, _before_state = _residual_cache_adapter(tmp_path, monkeypatch)
    stage = adapter._stage_root()
    stage.mkdir(mode=0o700, parents=True)
    payload = stage / "payload"
    payload.write_bytes(b"candidate")
    payload.chmod(0o600)
    shutil.copytree(stage, adapter._cache)
    marker = adapter._cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"1786320000000")
    marker.chmod(0o644)
    os.chown(marker, os.getuid(), os.getgid())
    candidate_payload = adapter._cache / "payload"
    original_sha256 = executor.hashlib.sha256
    candidate_digest = original_sha256(b"candidate").hexdigest()
    rewrote = False

    class RewriteAfterDigest:
        def __init__(self, *args, **kwargs) -> None:
            self._inner = original_sha256(*args, **kwargs)

        def update(self, value: bytes) -> None:
            self._inner.update(value)

        def hexdigest(self) -> str:
            nonlocal rewrote
            digest = self._inner.hexdigest()
            if not rewrote and digest == candidate_digest:
                rewrote = True
                candidate_payload.write_bytes(b"intruder!")
                candidate_payload.chmod(0o600)
            return digest

    monkeypatch.setattr(executor.hashlib, "sha256", RewriteAfterDigest)
    with pytest.raises(executor.AdoptionError, match="generated_candidate_drift"):
        adapter._quarantine_failed_candidate()
    assert rewrote
    assert adapter._cache.is_dir()
    assert not (
        adapter._quarantine_root()
        / executor._generated_candidate_quarantine_handle()
    ).exists()


def test_failed_candidate_quarantine_rejects_marker_rewrite_after_semantic_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, _before_state = _residual_cache_adapter(tmp_path, monkeypatch)
    stage = adapter._stage_root()
    stage.mkdir(mode=0o700, parents=True)
    payload = stage / "payload"
    payload.write_bytes(b"candidate")
    payload.chmod(0o600)
    shutil.copytree(stage, adapter._cache)
    marker = adapter._cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"1786320000000")
    marker.chmod(0o644)
    os.chown(marker, os.getuid(), os.getgid())
    validated = False

    def rewrite_after_semantic_check(info: os.stat_result, content: bytes) -> None:
        nonlocal validated
        assert stat.S_IMODE(info.st_mode) == 0o644
        assert info.st_nlink == 1
        assert info.st_size == 13
        assert content == b"1786320000000"
        validated = True
        marker.write_bytes(b"not-a-marker!")
        marker.chmod(0o644)

    monkeypatch.setattr(
        executor,
        "_validate_generated_orphan_marker",
        rewrite_after_semantic_check,
        raising=False,
    )
    with pytest.raises(executor.AdoptionError, match="generated_candidate_drift"):
        adapter._quarantine_failed_candidate()
    assert validated
    assert adapter._cache.is_dir()
    assert not (
        adapter._quarantine_root()
        / executor._generated_candidate_quarantine_handle()
    ).exists()


@pytest.mark.parametrize("after_rename", (False, True))
def test_failed_candidate_quarantine_rename_resumes_without_loss(
    tmp_path: Path,
    monkeypatch,
    after_rename: bool,
) -> None:
    adapter, _before_state = _residual_cache_adapter(tmp_path, monkeypatch)
    stage = adapter._stage_root()
    stage.mkdir(mode=0o700, parents=True)
    payload = stage / "payload"
    payload.write_bytes(b"candidate")
    payload.chmod(0o600)
    shutil.copytree(stage, adapter._cache)
    marker = adapter._cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"1786320000000")
    marker.chmod(0o644)
    os.chown(marker, os.getuid(), os.getgid())
    original = executor._rename_directory_between_exclusive

    def crash(*args, **kwargs) -> None:
        if after_rename:
            original(*args, **kwargs)
        raise executor.InjectedCrash("FAILED_CANDIDATE_QUARANTINE")

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", crash)
    with pytest.raises(
        executor.InjectedCrash,
        match="FAILED_CANDIDATE_QUARANTINE",
    ):
        adapter._quarantine_failed_candidate()
    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", original)

    recovery_fsyncs: list[int] = []
    original_fsync = executor.os.fsync

    def record_fsync(descriptor: int) -> None:
        recovery_fsyncs.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(executor.os, "fsync", record_fsync)

    adapter._quarantine_failed_candidate()
    generated = (
        adapter._quarantine_root()
        / executor._generated_candidate_quarantine_handle()
    )
    assert not adapter._cache.exists()
    assert generated.is_dir()
    assert (generated / executor.distribution.ORPHANED_INSTALLED_MARKER).is_file()
    if after_rename:
        assert len(recovery_fsyncs) >= 2


@pytest.mark.parametrize("replace_target", ("cache", "quarantine"))
def test_failed_candidate_recovery_rejects_parent_replacement(
    tmp_path: Path,
    monkeypatch,
    replace_target: str,
) -> None:
    adapter, _before_state = _residual_cache_adapter(tmp_path, monkeypatch)
    stage = adapter._stage_root()
    stage.mkdir(mode=0o700, parents=True)
    payload = stage / "payload"
    payload.write_bytes(b"candidate")
    payload.chmod(0o600)
    shutil.copytree(stage, adapter._cache)
    marker = adapter._cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"1786320000000")
    marker.chmod(0o644)
    os.chown(marker, os.getuid(), os.getgid())
    adapter._quarantine_failed_candidate()

    quarantine = adapter._quarantine_root()
    target = adapter._cache.parent if replace_target == "cache" else quarantine
    displaced = target.with_name(f"{target.name}-displaced")
    original_fsync = executor.os.fsync
    replaced = False

    def replace_parent(descriptor: int) -> None:
        nonlocal replaced
        if not replaced:
            replaced = True
            os.rename(target, displaced)
            target.mkdir(mode=0o700)
        original_fsync(descriptor)

    monkeypatch.setattr(executor.os, "fsync", replace_parent)
    with pytest.raises(
        executor.AdoptionError,
        match="generated_candidate_parent_drift",
    ):
        adapter._quarantine_failed_candidate()
    generated = executor._generated_candidate_quarantine_handle()
    if replace_target == "quarantine":
        assert (displaced / generated).is_dir()
    else:
        assert (quarantine / generated).is_dir()


def test_failed_candidate_recovery_rejects_destination_inode_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, _before_state = _residual_cache_adapter(tmp_path, monkeypatch)
    stage = adapter._stage_root()
    stage.mkdir(mode=0o700, parents=True)
    payload = stage / "payload"
    payload.write_bytes(b"candidate")
    payload.chmod(0o600)
    shutil.copytree(stage, adapter._cache)
    marker = adapter._cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"1786320000000")
    marker.chmod(0o644)
    os.chown(marker, os.getuid(), os.getgid())
    adapter._quarantine_failed_candidate()

    quarantine = adapter._quarantine_root()
    generated = quarantine / executor._generated_candidate_quarantine_handle()
    displaced = quarantine / "generated-cache-displaced"
    original_fsync = executor.os.fsync
    replaced = False

    def replace_entry(descriptor: int) -> None:
        nonlocal replaced
        if not replaced:
            replaced = True
            os.rename(generated, displaced)
            shutil.copytree(displaced, generated)
        original_fsync(descriptor)

    monkeypatch.setattr(executor.os, "fsync", replace_entry)
    with pytest.raises(
        executor.AdoptionError,
        match="generated_candidate_drift|generated_candidate_parent_drift",
    ):
        adapter._quarantine_failed_candidate()


@pytest.mark.parametrize("failure_call", (1, 2))
def test_quarantine_rename_crash_resumes_without_duplicate_or_loss(
    tmp_path: Path,
    monkeypatch,
    failure_call: int,
) -> None:
    adapter, before = _residual_cache_adapter(tmp_path, monkeypatch)
    original = executor._rename_directory_between_exclusive
    calls = 0

    def crash(*args, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise executor.InjectedCrash("QUARANTINE_RENAME")
        original(*args, **kwargs)

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", crash)
    with pytest.raises(executor.InjectedCrash, match="QUARANTINE_RENAME"):
        adapter._quarantine_before(before)
    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", original)
    adapter._quarantine_before(before)
    assert {
        path.name for path in adapter._quarantine_root().iterdir()
    } == {entry.handle for entry in before.quarantine_entries}
    assert not any(
        (adapter._cache.parent / entry.version).exists()
        for entry in before.quarantine_entries
    )


@pytest.mark.parametrize("failure_call", (1, 2))
def test_quarantine_restore_crash_resumes_exact_before(
    tmp_path: Path,
    monkeypatch,
    failure_call: int,
) -> None:
    adapter, before = _residual_cache_adapter(tmp_path, monkeypatch)
    adapter._quarantine_before(before)
    adapter._registry_is_exact_predecessor = lambda: True
    original = executor._rename_directory_between_exclusive
    calls = 0

    def crash(*args, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise executor.InjectedCrash("RESTORE_RENAME")
        original(*args, **kwargs)

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", crash)
    with pytest.raises(executor.InjectedCrash, match="RESTORE_RENAME"):
        adapter._restore_quarantine(before)
    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", original)
    adapter._restore_quarantine(before)
    for entry in before.quarantine_entries:
        assert adapter._entry_for_path(
            adapter._cache.parent / entry.version,
            version=entry.version,
            handle=entry.handle,
        ) == entry


@pytest.mark.parametrize(
    "failure_step",
    ("remove_plugin", "remove_marketplace", "install_candidate"),
)
def test_apply_phase_failure_restores_exact_residual_before(
    tmp_path: Path,
    monkeypatch,
    failure_step: str,
) -> None:
    adapter, before = _residual_cache_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(
        executor,
        "CLAUDE_RESIDUAL_PREDECESSOR_DIGEST",
        before.quarantine_entries[0].cache_digest,
    )
    monkeypatch.setattr(
        executor,
        "CLAUDE_RESIDUE_OPAQUE_DIGEST",
        before.quarantine_entries[1].cache_digest,
    )
    adapter._marketplace_cache = tmp_path / "absent-marketplace-cache"
    adapter._verify_marketplace_root = lambda *_args, **_kwargs: None
    predecessor = tmp_path / executor.PREDECESSOR_WORKTREE_LEAF
    predecessor.mkdir()
    monkeypatch.setattr(executor, "_resolve_predecessor_source", lambda: predecessor)

    def observe() -> executor.HostState:
        if all(
            (adapter._cache.parent / entry.version).exists()
            for entry in before.quarantine_entries
        ):
            return before
        raise executor.AdoptionError("plugin_registry_cache_mismatch")

    adapter.observe = observe

    def maybe_crash(step: str) -> None:
        if failure_step == step:
            raise executor.InjectedCrash(step)

    adapter._remove_plugin = lambda: maybe_crash("remove_plugin")
    adapter._remove_marketplace = lambda: maybe_crash("remove_marketplace")
    adapter._install_from = lambda _source: maybe_crash("install_candidate")
    with pytest.raises(executor.InjectedCrash, match=failure_step):
        adapter.apply("plugin-adoption-fixture", before, _after("claude"))
    assert all(
        (adapter._quarantine_root() / entry.handle).exists()
        for entry in before.quarantine_entries
    )

    adapter._registry_is_exact_predecessor = lambda: True
    assert adapter.rollback("plugin-adoption-fixture", before) == before
    for entry in before.quarantine_entries:
        assert adapter._entry_for_path(
            adapter._cache.parent / entry.version,
            version=entry.version,
            handle=entry.handle,
        ) == entry


def test_residual_observation_rejects_unexpected_target_and_invalid_leaf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, before = _residual_cache_adapter(tmp_path, monkeypatch)
    unexpected = adapter._cache.parent / "0.1.18"
    unexpected.mkdir(mode=0o700)
    payload = unexpected / "payload"
    payload.write_bytes(b"unexpected")
    payload.chmod(0o600)
    invalid = adapter._cache.parent / "not-a-version"
    invalid.mkdir(mode=0o700)
    entries, foreign, invalid_count, ambiguous = (
        adapter._cache_quarantine_projection(active_version=executor.PREDECESSOR_VERSION)
    )
    assert tuple(entry.version for entry in entries) == (
        executor.PREDECESSOR_VERSION,
        executor.CLAUDE_RESIDUE_VERSION,
        "0.1.18",
    )
    assert foreign == 0
    assert invalid_count == 1
    assert ambiguous == 0
    liar = replace(
        before,
        quarantine_entries=entries,
        invalid_cache_leaf_count=invalid_count,
    )
    with pytest.raises(
        executor.AdoptionError, match="before_state_not_exactly_reversible"
    ):
        executor._require_reversible_before_states((_before("codex"), liar))


def test_terminal_cache_is_quarantinable_but_not_an_ordinary_predecessor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, _before_state = _residual_cache_adapter(tmp_path, monkeypatch)
    terminal_cache = adapter._cache.parent / authority.TERMINAL_PLUGIN_VERSION
    terminal_cache.mkdir(mode=0o700)
    payload = terminal_cache / "payload"
    payload.write_bytes(b"terminal-observation")
    payload.chmod(0o600)

    entries, foreign, invalid, ambiguous = adapter._cache_quarantine_projection(
        active_version=executor.PREDECESSOR_VERSION
    )
    terminal_handles = [
        entry.handle
        for entry in entries
        if entry.version == authority.TERMINAL_PLUGIN_VERSION
    ]
    assert terminal_handles == ["target-cache-v048"]
    assert (foreign, invalid, ambiguous) == (0, 0, 0)

    observed_terminal = tuple(
        replace(_after(host), plugin_version=authority.TERMINAL_PLUGIN_VERSION)
        for host in executor.HOST_ORDER
    )
    with pytest.raises(
        executor.AdoptionError,
        match="before_state_not_exactly_reversible",
    ):
        executor._require_reversible_before_states(observed_terminal)


class MemoryAdapter:
    def __init__(
        self,
        name: str,
        *,
        fail_apply: bool = False,
        initial_state: executor.HostState | None = None,
    ):
        self.name = name
        self.state = initial_state or _before(name)
        self.fail_apply = fail_apply
        self.prepare_count = 0
        self.apply_count = 0
        self.rollback_count = 0

    def observe(self):
        return self.state

    def prepare(self, _transaction_id: str, _expected_after: executor.HostState):
        self.prepare_count += 1

    def apply(
        self,
        _transaction_id: str,
        expected_before: executor.HostState,
        _expected_after: executor.HostState,
    ):
        assert self.state == expected_before
        self.apply_count += 1
        if self.fail_apply:
            raise executor.AdoptionError("injected_host_failure")
        self.state = _after(self.name)
        return self.state

    def verify(self, _transaction_id: str, expected_after: executor.HostState):
        if self.state != expected_after:
            raise executor.AdoptionError("after_state_mismatch")
        return self.state

    def rollback(self, _transaction_id: str, expected_before: executor.HostState):
        self.rollback_count += 1
        self.state = expected_before
        return self.state


class BridgeMemoryAdapter(MemoryAdapter):
    def __init__(self, name: str, *, fail_apply: bool = False):
        super().__init__(name, fail_apply=fail_apply)
        self.configure_count = 0
        self.capture_count = 0
        self.verify_capture_count = 0

    def configure_terminal_bridge(
        self,
        terminal_state: executor.TerminalHostState,
        binding_digest: str,
    ) -> None:
        assert terminal_state.host == self.name
        self.configure_count += 1
        if self.configure_count == 1:
            self.state = executor.HostState(
                host=self.name,
                marketplace_present=True,
                marketplace_digest=terminal_state.marketplace_digest,
                marketplace_binding_digest=binding_digest,
                plugin_present=True,
                plugin_version=authority.TERMINAL_PLUGIN_VERSION,
                active=terminal_state.registry_state in {"active", "installed"},
                cache_digest=terminal_state.cache_digest,
            )

    def capture_terminal_rollback(
        self,
        expected_before: executor.HostState,
    ) -> dict[str, object]:
        assert self.state == expected_before
        self.capture_count += 1
        return {
            "before_state_digest": executor._canonical_digest(
                expected_before.projection()
            ),
            "cache_digest": expected_before.cache_digest,
            "cache_source_digest": expected_before.cache_digest,
            "host": self.name,
            "install_projection_digest": expected_before.cache_digest,
            "marketplace_digest": expected_before.marketplace_digest,
            "orphan_marker_content_digest": None,
            "source_digest": expected_before.marketplace_digest,
            "source_version": authority.TERMINAL_PLUGIN_VERSION,
        }

    def verify_terminal_rollback(
        self,
        expected_before: executor.HostState,
        capture: dict[str, object],
    ) -> None:
        self.verify_capture_count += 1
        assert capture == self.capture_terminal_projection(expected_before)

    def capture_terminal_projection(
        self,
        expected_before: executor.HostState,
    ) -> dict[str, object]:
        return {
            "before_state_digest": executor._canonical_digest(
                expected_before.projection()
            ),
            "cache_digest": expected_before.cache_digest,
            "cache_source_digest": expected_before.cache_digest,
            "host": self.name,
            "install_projection_digest": expected_before.cache_digest,
            "marketplace_digest": expected_before.marketplace_digest,
            "orphan_marker_content_digest": None,
            "source_digest": expected_before.marketplace_digest,
            "source_version": authority.TERMINAL_PLUGIN_VERSION,
        }


class TerminalMemoryObserver:
    def __init__(self, state: executor.TerminalHostState):
        self.name = state.host
        self.state = state
        self.observe_count = 0

    def observe(self) -> executor.TerminalHostState:
        self.observe_count += 1
        return self.state


class TerminalRecoveryObserver:
    def __init__(self, state: executor.CanonicalRecoveryState):
        self.state = state
        self.observe_count = 0

    def observe(self) -> executor.CanonicalRecoveryState:
        self.observe_count += 1
        return self.state


class ForbiddenOrdinaryAdapter:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[str] = []

    def _forbidden(self, method: str):
        self.calls.append(method)
        raise AssertionError(f"terminalize called ordinary host method {method}")

    def observe(self):
        return self._forbidden("observe")

    def prepare(self, *_args):
        return self._forbidden("prepare")

    def apply(self, *_args):
        return self._forbidden("apply")

    def verify(self, *_args):
        return self._forbidden("verify")

    def rollback(self, *_args):
        return self._forbidden("rollback")

    def remove(self, *_args):
        return self._forbidden("remove")

    def install(self, *_args):
        return self._forbidden("install")

    def enable(self, *_args):
        return self._forbidden("enable")

    def registry(self, *_args):
        return self._forbidden("registry")


@pytest.mark.parametrize("mismatch", ("distribution", "compatibility_alias"))
def test_ordinary_version_mismatch_fails_before_host_or_authority_use(
    tmp_path: Path,
    monkeypatch,
    mismatch: str,
) -> None:
    monkeypatch.setattr(
        authority,
        "ORDINARY_APPLY_PLUGIN_VERSION",
        "0.1.49",
        raising=False,
    )
    if mismatch == "distribution":
        monkeypatch.setattr(executor.distribution, "PLUGIN_VERSION", "0.1.50")
    else:
        monkeypatch.setattr(authority, "PLUGIN_VERSION", "0.1.48")
    adapters = tuple(
        ForbiddenOrdinaryAdapter(host) for host in executor.HOST_ORDER
    )

    with pytest.raises(
        executor.AdoptionError,
        match="ordinary_apply_plugin_version_mismatch",
    ):
        executor.PluginAdoptionExecutor(
            state_root=tmp_path / "ordinary-version-mismatch",
            adapters=adapters,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "version mismatch must fail before authority use"
            ),
        ).run()
    assert [adapter.calls for adapter in adapters] == [[], []]


def test_candidate_staging_reports_typed_version_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        authority,
        "ORDINARY_APPLY_PLUGIN_VERSION",
        authority.PLUGIN_VERSION,
        raising=False,
    )
    monkeypatch.setattr(executor.distribution, "PLUGIN_VERSION", "0.1.50")
    scratch = tmp_path / "candidate-staging"
    scratch.mkdir(mode=0o700)

    with pytest.raises(
        executor.AdoptionError,
        match="ordinary_apply_plugin_version_mismatch",
    ):
        executor._candidate_installed_digests(scratch)


def _terminal_state(host: str) -> executor.TerminalHostState:
    if host == "codex":
        return executor.TerminalHostState(
            host="codex",
            plugin_version=authority.TERMINAL_PLUGIN_VERSION,
            operational_adoption="qualification_pending",
            registry_state="active",
            cache_digest="a" * 64,
            cache_identity_digest="b" * 64,
            marketplace_digest="c" * 64,
            marketplace_identity_digest="d" * 64,
            orphan_marker_digest=None,
        )
    return executor.TerminalHostState(
        host="claude",
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        operational_adoption="orphaned",
        registry_state="installed",
        cache_digest="e" * 64,
        cache_identity_digest="f" * 64,
        marketplace_digest="1" * 64,
        marketplace_identity_digest="2" * 64,
        orphan_marker_digest="3" * 64,
    )


def _canonical_recovery() -> executor.CanonicalRecoveryState:
    return executor.CanonicalRecoveryState(
        anchor="fp1-canonical-recovery",
        source_revision=authority.TERMINAL_SOURCE_REVISION,
        source_bundle_digest="4" * 64,
        source_tree_digest="5" * 64,
        interpreter_digest="6" * 64,
        clean=True,
        interpreter_executable=True,
    )


def _commit_terminal_v048_journal(root: Path) -> tuple[bytes, dict[str, object]]:
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=TerminalRecoveryObserver(
            _canonical_recovery()
        ),
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
    ).run()
    return (root / "journal.json").read_bytes(), executor._read_terminal_journal(
        root
    )


def test_terminalize_records_divergent_v048_without_host_mutation_and_replays_once(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "plugin-adoption-v048-terminal"
    ordinary = (
        ForbiddenOrdinaryAdapter("codex"),
        ForbiddenOrdinaryAdapter("claude"),
    )
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    authority_calls = 0

    def counted(request, *, now, prepared_replay=False):
        nonlocal authority_calls
        authority_calls += 1
        return _terminal_authority_result(
            request,
            now=now,
            prepared_replay=prepared_replay,
        )

    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=ordinary,
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=counted,
        clock=lambda: 1000.0,
    )
    result = run.run()
    assert result["status"] == "terminalized"
    assert authority_calls == 1
    assert [adapter.calls for adapter in ordinary] == [[], []]
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "journal.json").stat().st_mode) == 0o600
    assert set(path.name for path in root.iterdir()) == {"journal.json"}
    record = executor._read_terminal_journal(root)
    assert record["operation"] == authority.TERMINAL_OPERATION
    assert record["phase"] == "COMMITTED"
    assert record["host_mutation_count"] == 0
    assert record["before_state_digest"] == record["after_state_digest"]
    assert record["before_states"] == record["after_states"]
    assert record["before_states"][0] != record["before_states"][1]

    replay = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=ordinary,
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "terminal replay must not request a second authority result"
        ),
        clock=lambda: 2000.0,
    )
    assert replay.run() == result
    assert authority_calls == 1
    assert [adapter.calls for adapter in ordinary] == [[], []]


def test_ordinary_v049_bridge_requires_committed_terminal_journal_before_authority(
    tmp_path: Path,
) -> None:
    adapters = tuple(
        ForbiddenOrdinaryAdapter(host) for host in executor.HOST_ORDER
    )
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )

    with pytest.raises(
        executor.AdoptionError,
        match="terminal_journal_unavailable",
    ):
        executor.PluginAdoptionExecutor(
            state_root=tmp_path / "ordinary-v049",
            adapters=adapters,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "missing terminal journal must fail before ordinary authority"
            ),
            terminal_bridge_root=tmp_path / "terminal-v048",
            terminal_observers=terminal,
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
        ).run()
    assert [adapter.calls for adapter in adapters] == [[], []]


def test_ordinary_v049_bridge_binds_terminal_evidence_in_fresh_ordinary_authority(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    terminal_bytes, terminal_record = _commit_terminal_v048_journal(
        terminal_root
    )
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    ordinary_requests: list[dict[str, object]] = []

    def request(request, *, now, prepared_replay=False):
        assert prepared_replay is False
        ordinary_requests.append(request)
        return _authority_result(request, now=now)

    result = executor.PluginAdoptionExecutor(
        state_root=ordinary_root,
        adapters=adapters,
        authority_request=request,
        terminal_bridge_root=terminal_root,
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(
            _canonical_recovery()
        ),
        clock=lambda: 2000.0,
    ).run()

    assert result["status"] == "committed"
    assert len(ordinary_requests) == 1
    ordinary = ordinary_requests[0]
    assert ordinary["actual"]["operation"] == authority.OPERATION
    assert ordinary["plan"]["plugin_version"] == authority.PLUGIN_VERSION
    manifest = executor._read_terminal_bridge_manifest(ordinary_root)
    assert manifest["terminal_journal_digest"] == hashlib.sha256(
        terminal_bytes
    ).hexdigest()
    assert manifest["terminal_request_digest"] == terminal_record["request_digest"]
    assert manifest["terminal_envelope_digest"] == terminal_record["envelope_digest"]
    assert (
        manifest["terminal_current_identity_digest"]
        == terminal_record["current_identity_digest"]
    )
    assert (
        manifest["terminal_canonical_identity_digest"]
        == terminal_record["canonical_identity_digest"]
    )
    assert ordinary["plan"]["rollback_manifest_digest"] == hashlib.sha256(
        executor._terminal_bridge_manifest_bytes(manifest)
    ).hexdigest()
    assert (terminal_root / "journal.json").read_bytes() == terminal_bytes
    assert ordinary_root / "journal.json" != terminal_root / "journal.json"
    assert [adapter.capture_count for adapter in adapters] == [1, 1]


@pytest.mark.parametrize(
    "crash_phase",
    ("ORDINARY_AUTHORIZED", "ORDINARY_JOURNAL_PUBLISHED"),
)
def test_ordinary_v049_prepared_request_replays_without_second_decision(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
    crash_phase: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    issuer = ReplayAwareOrdinaryIssuer()
    crashed = False

    def crash(phase: str) -> None:
        nonlocal crashed
        if phase == crash_phase and not crashed:
            crashed = True
            raise executor.InjectedCrash(phase)

    with pytest.raises(executor.InjectedCrash, match=crash_phase):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            crash_hook=crash,
            clock=lambda: 2000.0,
        ).run()
    assert issuer.consumed_budget == 1
    assert (ordinary_root / executor._ORDINARY_PREPARED_LEAF).is_file()

    resumed_authority = (
        issuer
        if crash_phase == "ORDINARY_AUTHORIZED"
        else lambda *_args, **_kwargs: pytest.fail(
            "durable ordinary journal must replay without another decision"
        )
    )
    result = executor.PluginAdoptionExecutor(
        state_root=ordinary_root,
        adapters=adapters,
        authority_request=resumed_authority,
        terminal_bridge_root=terminal_root,
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(
            _canonical_recovery()
        ),
        clock=lambda: 4000.0,
    ).run()
    assert result["status"] == "committed"
    assert issuer.consumed_budget == 1
    assert not (ordinary_root / executor._ORDINARY_PREPARED_LEAF).exists()
    if crash_phase == "ORDINARY_AUTHORIZED":
        assert [replay for _request, replay in issuer.calls] == [False, True]
        assert issuer.calls[0][0] == issuer.calls[1][0]
    else:
        assert [replay for _request, replay in issuer.calls] == [False]


@pytest.mark.parametrize(
    "crash_phase",
    ("ORDINARY_AUTHORIZED", "ORDINARY_JOURNAL_PUBLISHED", "PREPARED"),
)
def test_ordinary_v049_durable_authority_recovers_without_external_terminal(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
    crash_phase: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    issuer = ReplayAwareOrdinaryIssuer()
    crashed = False

    def crash(phase: str) -> None:
        nonlocal crashed
        if phase == crash_phase and not crashed:
            crashed = True
            raise executor.InjectedCrash(phase)

    with pytest.raises(executor.InjectedCrash, match=crash_phase):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            crash_hook=crash,
            clock=lambda: 2000.0,
        ).run()
    assert issuer.consumed_budget == 1
    (terminal_root / "journal.json").unlink()
    unavailable_observers = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    for observer in unavailable_observers:
        observer.observe = lambda: (_ for _ in ()).throw(
            executor.AdoptionError("terminal evidence unavailable")
        )
    unavailable_recovery = TerminalRecoveryObserver(_canonical_recovery())
    unavailable_recovery.observe = lambda: (_ for _ in ()).throw(
        executor.AdoptionError("canonical evidence unavailable")
    )
    resume_authority = (
        issuer
        if crash_phase == "ORDINARY_AUTHORIZED"
        else lambda *_args, **_kwargs: pytest.fail(
            "durable ordinary authority must not be requested again"
        )
    )
    result = executor.PluginAdoptionExecutor(
        state_root=ordinary_root,
        adapters=adapters,
        authority_request=resume_authority,
        terminal_bridge_root=terminal_root,
        terminal_observers=unavailable_observers,
        canonical_recovery_observer=unavailable_recovery,
        clock=lambda: 4000.0,
    ).run()
    assert result["status"] == "rolled_back"
    assert executor._read_journal(ordinary_root)["phase"] == "ROLLED_BACK"
    assert issuer.consumed_budget == 1
    if crash_phase == "ORDINARY_AUTHORIZED":
        assert [replay for _request, replay in issuer.calls] == [False, True]
    else:
        assert [replay for _request, replay in issuer.calls] == [False]


def test_ordinary_v049_prepared_request_rejects_fresh_source_mismatch(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )

    with pytest.raises(executor.InjectedCrash, match="ORDINARY_REQUEST_PREPARED"):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "prepared boundary precedes authority"
            ),
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            crash_hook=lambda phase: (
                (_ for _ in ()).throw(executor.InjectedCrash(phase))
                if phase == "ORDINARY_REQUEST_PREPARED"
                else None
            ),
            clock=lambda: 2000.0,
        ).run()
    monkeypatch.setattr(executor, "_source_revision", lambda: "9" * 40)
    with pytest.raises(
        executor.AdoptionError,
        match="ordinary_prepared_request_mismatch",
    ):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "mismatched prepared bytes must fail before authority"
            ),
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            clock=lambda: 4000.0,
        ).run()


def test_ordinary_v049_prepared_without_stored_envelope_expires(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )

    with pytest.raises(executor.InjectedCrash, match="ORDINARY_REQUEST_PREPARED"):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "prepared boundary precedes authority"
            ),
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            crash_hook=lambda phase: (
                (_ for _ in ()).throw(executor.InjectedCrash(phase))
                if phase == "ORDINARY_REQUEST_PREPARED"
                else None
            ),
            clock=lambda: 2000.0,
        ).run()

    calls = 0

    def no_stored_envelope(request, *, now, prepared_replay=False):
        nonlocal calls
        calls += 1
        assert prepared_replay is True
        assert now > request["actual"]["expires_at"]
        raise authority.PluginAdoptionAuthorityError("authority_stale")

    with pytest.raises(
        authority.PluginAdoptionAuthorityError,
        match="authority_stale",
    ):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=no_stored_envelope,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            clock=lambda: 4000.0,
        ).run()
    assert calls == 1
    assert (ordinary_root / executor._ORDINARY_PREPARED_LEAF).is_file()


def test_ordinary_v049_prepared_replay_rejects_changed_authority_ids(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    issuer = ReplayAwareOrdinaryIssuer()

    with pytest.raises(executor.InjectedCrash, match="ORDINARY_AUTHORIZED"):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            crash_hook=lambda phase: (
                (_ for _ in ()).throw(executor.InjectedCrash(phase))
                if phase == "ORDINARY_AUTHORIZED"
                else None
            ),
            clock=lambda: 2000.0,
        ).run()
    assert issuer.consumed_budget == 1

    prepared_path = ordinary_root / executor._ORDINARY_PREPARED_LEAF
    prepared = json.loads(prepared_path.read_text(encoding="ascii"))
    old_request = authority._base._parse_canonical_authority_payload(
        base64.b64decode(str(prepared["request_b64"]), validate=True)
    )
    changed_request = authority.build_plugin_adoption_request(
        decision_id="plugin-adoption-decision-" + "9" * 32,
        transaction_id="plugin-adoption-" + "8" * 32,
        source_runtime_revision=old_request["actual"][
            "source_runtime_revision"
        ],
        issued_at=float(old_request["actual"]["issued_at"]),
        expires_at=float(old_request["actual"]["expires_at"]),
        plan=old_request["plan"],
    )
    changed_bytes = authority.canonical_bytes(changed_request)
    prepared["decision_id"] = changed_request["actual"]["decision_id"]
    prepared["transaction_id"] = changed_request["actual"]["transaction_id"]
    prepared["request_b64"] = base64.b64encode(changed_bytes).decode("ascii")
    prepared["request_digest"] = hashlib.sha256(changed_bytes).hexdigest()
    prepared_path.write_bytes(executor._ordinary_prepared_bytes(prepared))
    prepared_path.chmod(0o600)

    with pytest.raises(
        executor.AdoptionError,
        match="ordinary_prepared_request_mismatch",
    ):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            clock=lambda: 2001.0,
        ).run()
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1


@pytest.mark.parametrize("crash_host", executor.HOST_ORDER)
def test_ordinary_v049_resume_does_not_repeat_exact_applied_host_effect(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
    crash_host: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    selected = adapters[executor.HOST_ORDER.index(crash_host)]
    original_apply = selected.apply
    crashed = False

    def apply_then_crash(*args, **kwargs):
        nonlocal crashed
        result = original_apply(*args, **kwargs)
        if not crashed:
            crashed = True
            raise executor.InjectedCrash(f"{crash_host}_effect")
        return result

    selected.apply = apply_then_crash
    issuer = ReplayAwareOrdinaryIssuer()
    with pytest.raises(executor.InjectedCrash, match=f"{crash_host}_effect"):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            clock=lambda: 2000.0,
        ).run()
    assert selected.apply_count == 1
    selected.apply = original_apply
    assert executor.PluginAdoptionExecutor(
        state_root=ordinary_root,
        adapters=adapters,
        authority_request=lambda *_args, **_kwargs: pytest.fail(
            "durable ordinary journal must not request a second decision"
        ),
        terminal_bridge_root=terminal_root,
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(
            _canonical_recovery()
        ),
    ).run()["status"] == "committed"
    assert selected.apply_count == 1


def test_ordinary_v049_rollback_resume_uses_private_capture_and_never_archives_it(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = (
        BridgeMemoryAdapter("codex"),
        BridgeMemoryAdapter("claude", fail_apply=True),
    )
    observers = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    issuer = ReplayAwareOrdinaryIssuer()

    with pytest.raises(executor.InjectedCrash, match="ROLLING_BACK"):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=observers,
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            crash_hook=lambda phase: (
                (_ for _ in ()).throw(executor.InjectedCrash(phase))
                if phase == "ROLLING_BACK"
                else None
            ),
            clock=lambda: 2000.0,
        ).run()
    assert executor._read_journal(ordinary_root)["phase"] == "ROLLING_BACK"
    assert adapters[0].state.plugin_version == authority.PLUGIN_VERSION
    assert (
        adapters[1].state.plugin_version
        == authority.TERMINAL_PLUGIN_VERSION
    )
    # A crash may expose a state outside either signed endpoint.  Once the
    # journal is ROLLING_BACK, the signed bridge manifest and private capture
    # must drive cleanup instead of rejecting that intermediate projection.
    adapters[0].state = replace(
        adapters[0].state,
        cache_digest="9" * 64,
    )

    def obsolete_terminal_source() -> executor.TerminalHostState:
        raise executor.AdoptionError("terminal source no longer selected")

    for observer in observers:
        observer.observe = obsolete_terminal_source
    (terminal_root / "journal.json").unlink()
    unavailable_recovery = TerminalRecoveryObserver(_canonical_recovery())
    unavailable_recovery.observe = lambda: (_ for _ in ()).throw(
        executor.AdoptionError("canonical recovery no longer available")
    )
    resumed = executor.PluginAdoptionExecutor(
        state_root=ordinary_root,
        adapters=adapters,
        authority_request=lambda *_args, **_kwargs: pytest.fail(
            "durable ordinary journal must not request authority again"
        ),
        terminal_bridge_root=terminal_root,
        terminal_observers=observers,
        canonical_recovery_observer=unavailable_recovery,
        archive_rolled_back=True,
        clock=lambda: 4000.0,
    )
    assert resumed.run()["status"] == "rolled_back"
    assert executor._read_journal(ordinary_root)["phase"] == "ROLLED_BACK"

    result = resumed.run()
    assert result["status"] == "rolled_back"
    assert ordinary_root.is_dir()
    assert not (
        executor._history_root(ordinary_root)
        / str(executor._read_journal(ordinary_root)["transaction_id"])
    ).exists()
    assert issuer.consumed_budget == 1


def test_ordinary_v049_partial_host_effect_routes_to_private_rollback(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    ordinary_root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    codex = adapters[0]
    issuer = ReplayAwareOrdinaryIssuer()

    def partial_apply(
        _transaction_id: str,
        expected_before: executor.HostState,
        expected_after: executor.HostState,
    ) -> executor.HostState:
        assert codex.state == expected_before
        codex.apply_count += 1
        codex.state = replace(expected_after, cache_digest="8" * 64)
        raise executor.InjectedCrash("codex_partial_effect")

    codex.apply = partial_apply
    with pytest.raises(executor.InjectedCrash, match="codex_partial_effect"):
        executor.PluginAdoptionExecutor(
            state_root=ordinary_root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            clock=lambda: 2000.0,
        ).run()
    assert executor._read_journal(ordinary_root)["phase"] == "PREPARED"

    observers = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    for observer in observers:
        observer.observe = lambda: (_ for _ in ()).throw(
            executor.AdoptionError("terminal source unavailable")
        )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    recovery.observe = lambda: (_ for _ in ()).throw(
        executor.AdoptionError("canonical source unavailable")
    )
    (terminal_root / "journal.json").unlink()
    result = executor.PluginAdoptionExecutor(
        state_root=ordinary_root,
        adapters=adapters,
        authority_request=lambda *_args, **_kwargs: pytest.fail(
            "durable ordinary journal must not request authority again"
        ),
        terminal_bridge_root=terminal_root,
        terminal_observers=observers,
        canonical_recovery_observer=recovery,
        clock=lambda: 4000.0,
    ).run()
    assert result["status"] == "rolled_back"
    assert executor._read_journal(ordinary_root)["phase"] == "ROLLED_BACK"
    assert [adapter.rollback_count for adapter in adapters] == [1, 1]
    assert issuer.consumed_budget == 1


def test_ordinary_v049_bridge_rejects_live_terminal_drift_before_capture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    drifted = replace(
        _terminal_state("claude"),
        cache_digest="9" * 64,
    )
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_current_state_drift",
    ):
        executor.PluginAdoptionExecutor(
            state_root=tmp_path / "ordinary-v049",
            adapters=adapters,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "live terminal drift must fail before ordinary authority"
            ),
            terminal_bridge_root=terminal_root,
            terminal_observers=(
                TerminalMemoryObserver(_terminal_state("codex")),
                TerminalMemoryObserver(drifted),
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
        ).run()
    assert [adapter.capture_count for adapter in adapters] == [0, 0]


@pytest.mark.parametrize(
    "mutation",
    ("not_committed", "wrong_contract", "wrong_digest", "wrong_source_version"),
)
def test_ordinary_v049_bridge_rejects_invalid_terminal_journal_variants(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _terminal_bytes, original = _commit_terminal_v048_journal(terminal_root)
    record = json.loads(json.dumps(original))
    if mutation == "not_committed":
        record["phase"] = "AUTHORIZED"
    elif mutation == "wrong_contract":
        record["operation"] = authority.OPERATION
    elif mutation == "wrong_digest":
        record["request_digest"] = "0" * 64
    else:
        request = json.loads(
            base64.b64decode(record["request_b64"], validate=True)
        )
        request["plan"]["source_revision"] = "9" * 40
        request["plan"]["plugin_version"] = authority.PLUGIN_VERSION
        request_bytes = authority.canonical_bytes(request)
        record["request_b64"] = base64.b64encode(request_bytes).decode("ascii")
        record["request_digest"] = hashlib.sha256(request_bytes).hexdigest()
    executor._atomic_private_write(
        terminal_root / "journal.json",
        executor._json_bytes(record),
    )
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    with pytest.raises(executor.AdoptionError, match="terminal_"):
        executor.PluginAdoptionExecutor(
            state_root=tmp_path / "ordinary-v049",
            adapters=adapters,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "invalid terminal journal must fail before ordinary authority"
            ),
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
        ).run()
    assert [adapter.capture_count for adapter in adapters] == [0, 0]


def test_terminal_and_ordinary_journals_reject_cross_verifiers(
    tmp_path: Path,
    monkeypatch,
    fixed_source,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal_root = tmp_path / "terminal-v048"
    _terminal_bytes, terminal_record = _commit_terminal_v048_journal(
        terminal_root
    )
    with pytest.raises(executor.AdoptionError):
        executor._reverify_journal(terminal_record)

    ordinary_root = tmp_path / "ordinary-v049"
    executor.PluginAdoptionExecutor(
        state_root=ordinary_root,
        adapters=tuple(
            BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
        ),
        authority_request=lambda request, *, now, prepared_replay=False: (
            _authority_result(request, now=now)
        ),
        terminal_bridge_root=terminal_root,
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(
            _canonical_recovery()
        ),
        clock=lambda: 2000.0,
    ).run()
    with pytest.raises(executor.AdoptionError):
        executor._reverify_terminal_journal(
            executor._read_journal(ordinary_root)
        )


def test_fixed_terminal_observer_reads_exact_v048_without_host_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(executor, "_fixed_user_home", lambda: home)
    transaction = tmp_path / "transaction"
    transaction.mkdir(mode=0o700)
    observer = executor.FixedTerminalHostObserver(
        "codex",
        transaction,
        transaction,
    )
    cache = observer._cache
    marketplace = observer._marketplace_source_root
    cache.mkdir(parents=True, mode=0o700)
    marketplace.mkdir(parents=True, mode=0o700)
    manifest = cache / "SOURCE_MANIFEST.json"
    manifest.write_text(
        json.dumps({"operational_adoption": "qualification_pending"}) + "\n",
        encoding="ascii",
    )
    manifest.chmod(0o600)
    marketplace_manifest = marketplace / "marketplace.json"
    marketplace_manifest.write_text("{}\n", encoding="ascii")
    marketplace_manifest.chmod(0o600)

    def listing(args):
        if args == ("plugin", "list", "--json"):
            return [{
                "enabled": True,
                "id": f"{authority.PLUGIN_ID}@{authority.MARKETPLACE_ID}",
                "version": authority.TERMINAL_PLUGIN_VERSION,
            }]
        return [{
            "id": authority.MARKETPLACE_ID,
            "source": str(marketplace),
        }]

    monkeypatch.setattr(observer, "_run_json", listing)
    state = observer.observe()
    assert state.host == "codex"
    assert state.plugin_version == authority.TERMINAL_PLUGIN_VERSION
    assert state.operational_adoption == "qualification_pending"
    assert state.orphan_marker_digest is None
    assert state.cache_digest != state.marketplace_digest

    orphan = cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    orphan.write_text("1234567890123", encoding="ascii")
    orphan.chmod(0o644)
    monkeypatch.setattr(executor.os, "getgid", lambda: orphan.lstat().st_gid)
    state = observer.observe()
    assert state.operational_adoption == "orphaned"
    assert state.orphan_marker_digest is not None


def test_fixed_canonical_recovery_observer_binds_clean_fp1_and_interpreter(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "hermes-fp1-state-schema-20260814"
    bundle = root / "distribution" / authority.PLUGIN_ID / "SOURCE_MANIFEST.json"
    bundle.parent.mkdir(parents=True, mode=0o700)
    bundle.write_text("{}\n", encoding="ascii")
    bundle.chmod(0o600)
    interpreter = root / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True, mode=0o700)
    interpreter.write_bytes(b"fixture-python")
    interpreter.chmod(0o500)

    def git(_root, args, **_kwargs):
        if args == ("rev-parse", "HEAD"):
            return authority.TERMINAL_SOURCE_REVISION + "\n"
        if args == ("rev-parse", "HEAD^{tree}"):
            return "7" * 40 + "\n"
        if args == ("status", "--porcelain", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(executor, "_git_text", git)
    observer = executor.FixedCanonicalRecoveryObserver(root)
    state = observer.observe()
    assert state.anchor == "fp1-canonical-recovery"
    assert state.clean is True
    assert state.interpreter_executable is True
    assert state.source_revision == authority.TERMINAL_SOURCE_REVISION
    interpreter.unlink()
    with pytest.raises(
        executor.AdoptionError,
        match="canonical_recovery_interpreter_unavailable",
    ):
        observer.observe()


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        (
            replace(_canonical_recovery(), interpreter_executable=False),
            "canonical_recovery_interpreter_unavailable",
        ),
        (
            replace(_canonical_recovery(), clean=False),
            "canonical_recovery_source_dirty",
        ),
        (
            replace(_canonical_recovery(), source_revision="0" * 40),
            "canonical_recovery_projection_invalid",
        ),
        (
            replace(_canonical_recovery(), source_bundle_digest="0" * 64),
            "canonical_recovery_drift",
        ),
        (
            replace(_canonical_recovery(), source_tree_digest="0" * 64),
            "canonical_recovery_drift",
        ),
        (
            replace(_canonical_recovery(), interpreter_digest="0" * 64),
            "canonical_recovery_drift",
        ),
    ],
)
def test_terminalize_rejects_canonical_recovery_drift(
    tmp_path: Path, monkeypatch, replacement, code: str
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())

    def drift_after_authority(request, *, now, prepared_replay=False):
        verified = _terminal_authority_result(
            request,
            now=now,
            prepared_replay=prepared_replay,
        )
        recovery.state = replacement
        return verified

    run = executor.PluginAdoptionExecutor(
        state_root=tmp_path / code,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=drift_after_authority,
        clock=lambda: 1000.0,
    )
    with pytest.raises(executor.AdoptionError, match=code):
        run.run()
    assert executor._read_terminal_prepared(tmp_path / code)["phase"] == (
        "REQUEST_PREPARED"
    )


@pytest.mark.parametrize(
    "field",
    [
        "cache_digest",
        "cache_identity_digest",
        "marketplace_digest",
        "marketplace_identity_digest",
        "orphan_marker_digest",
    ],
)
def test_terminalize_rejects_host_cas_and_orphan_marker_drift(
    tmp_path: Path, monkeypatch, field: str
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )

    def drift_after_authority(request, *, now, prepared_replay=False):
        verified = _terminal_authority_result(
            request,
            now=now,
            prepared_replay=prepared_replay,
        )
        terminal[1].state = replace(terminal[1].state, **{field: "9" * 64})
        return verified

    root = tmp_path / field
    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=drift_after_authority,
        clock=lambda: 1000.0,
    )
    with pytest.raises(executor.AdoptionError, match="terminal_current_state_drift"):
        run.run()
    assert executor._read_terminal_prepared(root)["phase"] == "REQUEST_PREPARED"


def test_terminalize_crash_boundaries_never_claim_without_durable_journal(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)

    def make(crash_phase: str, authority_request=_terminal_authority_result):
        crashed = False

        def crash(phase: str) -> None:
            nonlocal crashed
            if phase == crash_phase and not crashed:
                crashed = True
                raise executor.InjectedCrash(phase)

        return executor.PluginAdoptionExecutor(
            state_root=tmp_path / crash_phase,
            adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
            authority_request=_authority_result,
            action="terminalize",
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            terminal_authority_request=authority_request,
            clock=lambda: 1000.0,
            crash_hook=crash,
        )

    pre = make("TERMINAL_AUTHORIZED")
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_AUTHORIZED"):
        pre.run()
    assert executor._read_terminal_prepared(
        tmp_path / "TERMINAL_AUTHORIZED"
    )["phase"] == "REQUEST_PREPARED"

    post = make("COMMITTED")
    with pytest.raises(executor.InjectedCrash, match="COMMITTED"):
        post.run()
    assert executor._read_terminal_journal(
        tmp_path / "COMMITTED"
    )["phase"] == "COMMITTED"
    replay = make(
        "never",
        authority_request=lambda *_args, **_kwargs: pytest.fail(
            "published terminal record must replay without authority"
        ),
    )
    replay.root = tmp_path / "COMMITTED"
    assert replay.run()["status"] == "terminalized"


def test_terminal_authorized_crash_reuses_one_prepared_request_and_one_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "exact-once"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())

    issuer = ReplayAwareTerminalIssuer()
    crashed = False

    def crash(phase: str) -> None:
        nonlocal crashed
        if phase == "TERMINAL_AUTHORIZED" and not crashed:
            crashed = True
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_AUTHORIZED"):
        first.run()

    prepared = executor._read_terminal_prepared(root)
    assert prepared["phase"] == "REQUEST_PREPARED"
    assert stat.S_IMODE((root / "journal.json").stat().st_mode) == 0o600
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 2000.0,
    )
    assert resumed.run()["status"] == "terminalized"

    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 2
    assert issuer.calls[0] == issuer.calls[1] == issuer.request_bytes
    assert len(set(issuer.calls)) == 1
    assert len(issuer.decision_ids) == 1
    assert len(issuer.transaction_ids) == 1
    assert len(issuer.envelope_digests) == 1
    assert issuer.envelope_bytes is not None
    record = executor._read_terminal_journal(root)
    assert record["request_digest"] == hashlib.sha256(issuer.request_bytes).hexdigest()
    assert record["envelope_digest"] == hashlib.sha256(
        issuer.envelope_bytes
    ).hexdigest()
    assert record["host_mutation_count"] == 0
    assert stat.S_IMODE((root / "journal.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "fault",
    [
        "open_error",
        "crash_before_write",
        "crash_after_partial",
        "write_error",
        "file_fsync_error",
    ],
)
def test_terminal_prepared_atomic_publication_recovers_byte_boundary_faults(
    tmp_path: Path,
    monkeypatch,
    fault: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / fault
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()
    ordinary = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    injected = TerminalPublicationFault(
        target_leaves={"journal.json", _PREPARED_TEMP_LEAF},
        visible_path=root / "journal.json",
        fault=fault,
    )
    with monkeypatch.context() as fault_patch:
        injected.install(fault_patch)
        first = executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=ordinary,
            authority_request=_authority_result,
            action="terminalize",
            terminal_observers=terminal,
            canonical_recovery_observer=recovery,
            terminal_authority_request=issuer,
            clock=lambda: 1000.0,
        )
        expected = (
            executor.InjectedCrash
            if fault.startswith("crash_")
            else executor.AdoptionError
        )
        with pytest.raises(expected):
            first.run()

    if fault == "open_error":
        assert injected.write_calls == 0
        assert injected.visible_during_write == []
    else:
        assert injected.write_calls >= 1
        assert injected.visible_during_write
    assert not any(injected.visible_during_write)
    assert issuer.consumed_budget == 0
    assert issuer.calls == []
    assert not (root / "journal.json").exists()
    assert set(path.name for path in root.iterdir()) <= {_PREPARED_TEMP_LEAF}

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=ordinary,
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1001.0,
    )
    assert resumed.run()["status"] == "terminalized"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert len(issuer.decision_ids) == 1
    assert len(issuer.transaction_ids) == 1
    assert len(issuer.envelope_digests) == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0
    assert [adapter.apply_count for adapter in ordinary] == [0, 0]
    assert [adapter.rollback_count for adapter in ordinary] == [0, 0]
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


def test_terminal_prepared_short_writes_never_expose_partial_journal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "prepared-short-write"
    injected = TerminalPublicationFault(
        target_leaves={"journal.json", _PREPARED_TEMP_LEAF},
        visible_path=root / "journal.json",
        fault="short_write",
    )
    issuer = ReplayAwareTerminalIssuer()
    with monkeypatch.context() as fault_patch:
        injected.install(fault_patch)
        run = executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
            authority_request=_authority_result,
            action="terminalize",
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            terminal_authority_request=issuer,
            clock=lambda: 1000.0,
        )
        assert run.run()["status"] == "terminalized"
    assert injected.write_calls > 1
    assert not any(injected.visible_during_write)
    assert issuer.consumed_budget == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0


@pytest.mark.parametrize(
    "fault",
    [
        "open_error",
        "crash_before_write",
        "crash_after_partial",
        "write_error",
        "file_fsync_error",
    ],
)
def test_terminal_stage_atomic_publication_recovers_one_consumed_budget(
    tmp_path: Path,
    monkeypatch,
    fault: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / f"stage-{fault}"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()
    ordinary = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    injected = TerminalPublicationFault(
        target_leaves={executor._TERMINAL_STAGE_LEAF, _STAGE_TEMP_LEAF},
        visible_path=root / executor._TERMINAL_STAGE_LEAF,
        fault=fault,
    )
    with monkeypatch.context() as fault_patch:
        injected.install(fault_patch)
        first = executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=ordinary,
            authority_request=_authority_result,
            action="terminalize",
            terminal_observers=terminal,
            canonical_recovery_observer=recovery,
            terminal_authority_request=issuer,
            clock=lambda: 1000.0,
        )
        expected = (
            executor.InjectedCrash
            if fault.startswith("crash_")
            else executor.AdoptionError
        )
        with pytest.raises(expected):
            first.run()

    if fault == "open_error":
        assert injected.write_calls == 0
        assert injected.visible_during_write == []
    else:
        assert injected.write_calls >= 1
        assert injected.visible_during_write
    assert not any(injected.visible_during_write)
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert executor._read_terminal_prepared(root)["phase"] == "REQUEST_PREPARED"
    assert not (root / executor._TERMINAL_STAGE_LEAF).exists()
    assert set(path.name for path in root.iterdir()) <= {
        "journal.json",
        _STAGE_TEMP_LEAF,
    }

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=ordinary,
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 2000.0,
    )
    assert resumed.run()["status"] == "terminalized"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 2
    assert issuer.calls[0] == issuer.calls[1]
    assert len(issuer.decision_ids) == 1
    assert len(issuer.transaction_ids) == 1
    assert len(issuer.envelope_digests) == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0
    assert [adapter.apply_count for adapter in ordinary] == [0, 0]
    assert [adapter.rollback_count for adapter in ordinary] == [0, 0]
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


def test_terminal_stage_short_writes_never_expose_partial_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "stage-short-write"
    injected = TerminalPublicationFault(
        target_leaves={executor._TERMINAL_STAGE_LEAF, _STAGE_TEMP_LEAF},
        visible_path=root / executor._TERMINAL_STAGE_LEAF,
        fault="short_write",
    )
    issuer = ReplayAwareTerminalIssuer()
    with monkeypatch.context() as fault_patch:
        injected.install(fault_patch)
        run = executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
            authority_request=_authority_result,
            action="terminalize",
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            terminal_authority_request=issuer,
            clock=lambda: 1000.0,
        )
        assert run.run()["status"] == "terminalized"
    assert injected.write_calls > 1
    assert not any(injected.visible_during_write)
    assert issuer.consumed_budget == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0


@pytest.mark.parametrize(
    ("phase", "temp_leaf", "authority_calls_after_crash"),
    [
        ("TERMINAL_PREPARED_TEMP_READY", _PREPARED_TEMP_LEAF, 0),
        ("TERMINAL_STAGE_TEMP_READY", _STAGE_TEMP_LEAF, 1),
    ],
)
def test_terminal_complete_private_temp_is_promoted_after_crash(
    tmp_path: Path,
    monkeypatch,
    phase: str,
    temp_leaf: str,
    authority_calls_after_crash: int,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / phase
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()
    ordinary = (MemoryAdapter("codex"), MemoryAdapter("claude"))

    def crash(crash_phase: str) -> None:
        if crash_phase == phase:
            raise executor.InjectedCrash(crash_phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=ordinary,
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match=phase):
        first.run()
    assert (root / temp_leaf).is_file()
    assert stat.S_IMODE((root / temp_leaf).stat().st_mode) == 0o600
    assert len(issuer.calls) == authority_calls_after_crash

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=ordinary,
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=(
            issuer
            if authority_calls_after_crash == 0
            else lambda *_args, **_kwargs: pytest.fail(
                "complete signed stage temp must not replay authority"
            )
        ),
        clock=lambda: (
            1001.0 if authority_calls_after_crash == 0 else 2000.0
        ),
    )
    assert resumed.run()["status"] == "terminalized"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert len(issuer.decision_ids) == 1
    assert len(issuer.transaction_ids) == 1
    assert len(issuer.envelope_digests) == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0
    assert [adapter.apply_count for adapter in ordinary] == [0, 0]
    assert [adapter.rollback_count for adapter in ordinary] == [0, 0]
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


def test_terminal_partial_prepared_temp_is_cleaned_once_and_restarts_fresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "partial-prepared-residue"
    root.mkdir(mode=0o700)
    temp = root / _PREPARED_TEMP_LEAF
    temp.write_bytes(b'{"after_state_digest"')
    temp.chmod(0o600)
    issuer = ReplayAwareTerminalIssuer()
    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
    )
    assert run.run()["status"] == "terminalized"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


@pytest.mark.parametrize("drift", ["mode", "symlink", "link"])
def test_terminal_prepared_temp_metadata_drift_is_not_cleaned_or_promoted(
    tmp_path: Path,
    monkeypatch,
    drift: str,
) -> None:
    root = tmp_path / drift
    root.mkdir(mode=0o700)
    temp = root / _PREPARED_TEMP_LEAF
    if drift == "symlink":
        outside = tmp_path / "outside-prepared-temp"
        outside.write_bytes(b'{"partial"')
        outside.chmod(0o600)
        temp.symlink_to(outside)
    elif drift == "link":
        outside = tmp_path / "linked-prepared-temp"
        outside.write_bytes(b'{"partial"')
        outside.chmod(0o600)
        os.link(outside, temp)
    else:
        temp.write_bytes(b'{"partial"')
        temp.chmod(0o644)

    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "metadata drift must fail before authority"
        ),
        clock=lambda: 1000.0,
    )
    with pytest.raises(executor.AdoptionError, match="terminal_temp_drift"):
        run.run()
    assert temp.exists() or temp.is_symlink()


def test_terminal_complete_stage_temp_state_substitution_is_not_promoted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    foreign_root = tmp_path / "foreign"
    foreign_terminal = (
        TerminalMemoryObserver(
            replace(_terminal_state("codex"), cache_digest="9" * 64)
        ),
        TerminalMemoryObserver(_terminal_state("claude")),
    )

    def crash_foreign(phase: str) -> None:
        if phase == "TERMINAL_STAGE_TEMP_READY":
            raise executor.InjectedCrash(phase)

    foreign = executor.PluginAdoptionExecutor(
        state_root=foreign_root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=foreign_terminal,
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=ReplayAwareTerminalIssuer(),
        clock=lambda: 1000.0,
        crash_hook=crash_foreign,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_STAGE_TEMP_READY"):
        foreign.run()

    root = tmp_path / "target"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()

    def crash_target(phase: str) -> None:
        if phase == "TERMINAL_AUTHORIZED":
            raise executor.InjectedCrash(phase)

    target = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash_target,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_AUTHORIZED"):
        target.run()

    stage_temp = root / _STAGE_TEMP_LEAF
    shutil.copyfile(foreign_root / _STAGE_TEMP_LEAF, stage_temp)
    stage_temp.chmod(0o600)
    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "substituted complete stage temp must fail before authority replay"
        ),
        clock=lambda: 2000.0,
    )
    with pytest.raises(executor.AdoptionError, match="terminal_stage_drift"):
        resumed.run()
    assert stage_temp.is_file()
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1


def test_terminal_repeated_partial_crashes_keep_one_bounded_temp_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "bounded-residue"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()
    for attempt in range(3):
        injected = TerminalPublicationFault(
            target_leaves={"journal.json", _PREPARED_TEMP_LEAF},
            visible_path=root / "journal.json",
            fault="crash_after_partial",
        )
        with monkeypatch.context() as fault_patch:
            injected.install(fault_patch)
            run = executor.PluginAdoptionExecutor(
                state_root=root,
                adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
                authority_request=_authority_result,
                action="terminalize",
                terminal_observers=terminal,
                canonical_recovery_observer=recovery,
                terminal_authority_request=issuer,
                clock=lambda: 1000.0 + attempt,
            )
            with pytest.raises(executor.InjectedCrash, match="crash_after_partial"):
                run.run()
        assert set(path.name for path in root.iterdir()) == {
            _PREPARED_TEMP_LEAF
        }
        assert issuer.calls == []

    final = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1004.0,
    )
    assert final.run()["status"] == "terminalized"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


@pytest.mark.parametrize("publication", ["prepared", "stage", "final"])
def test_terminal_directory_fsync_failure_leaves_only_complete_visible_state(
    tmp_path: Path,
    monkeypatch,
    publication: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / publication
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()
    original_fsync = executor.os.fsync
    failed = False

    def fsync(descriptor: int) -> None:
        nonlocal failed
        info = os.fstat(descriptor)
        stage_exists = (root / executor._TERMINAL_STAGE_LEAF).exists()
        should_fail = (
            not failed
            and stat.S_ISDIR(info.st_mode)
            and (
                (
                    publication == "prepared"
                    and (root / "journal.json").exists()
                    and not stage_exists
                    and issuer.calls == []
                )
                or (
                    publication == "stage"
                    and stage_exists
                    and len(issuer.calls) == 1
                )
                or (
                    publication == "final"
                    and (root / "journal.json").exists()
                    and not stage_exists
                    and len(issuer.calls) == 1
                )
            )
        )
        if should_fail:
            failed = True
            raise OSError(errno.EIO, "injected directory fsync failure")
        original_fsync(descriptor)

    with monkeypatch.context() as fault_patch:
        fault_patch.setattr(executor.os, "fsync", fsync)
        first = executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
            authority_request=_authority_result,
            action="terminalize",
            terminal_observers=terminal,
            canonical_recovery_observer=recovery,
            terminal_authority_request=issuer,
            clock=lambda: 1000.0,
        )
        with pytest.raises(executor.AdoptionError):
            first.run()
    assert failed is True

    if publication == "prepared":
        assert executor._read_terminal_prepared(root)["phase"] == (
            "REQUEST_PREPARED"
        )
        expected_authority_calls = 0
        replay_authority = issuer
        replay_clock = 1001.0
    elif publication == "stage":
        assert executor._read_terminal_prepared(root)["phase"] == (
            "REQUEST_PREPARED"
        )
        assert executor._read_terminal_stage(root)["phase"] == "COMMITTED"
        expected_authority_calls = 1
        replay_authority = lambda *_args, **_kwargs: pytest.fail(
            "complete visible stage must not replay authority"
        )
        replay_clock = 2000.0
    else:
        assert executor._read_terminal_journal(root)["phase"] == "COMMITTED"
        expected_authority_calls = 1
        replay_authority = lambda *_args, **_kwargs: pytest.fail(
            "complete final journal must not replay authority"
        )
        replay_clock = 2000.0
    assert len(issuer.calls) == expected_authority_calls

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=replay_authority,
        clock=lambda: replay_clock,
    )
    assert resumed.run()["status"] == "terminalized"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


@pytest.mark.parametrize(
    ("crash_phase", "durable_phase", "stage_present"),
    [
        ("TERMINAL_FINAL_READY", "REQUEST_PREPARED", True),
        ("TERMINAL_FINAL_REPLACED", "COMMITTED", False),
    ],
)
def test_terminal_final_publication_crash_recovers_without_authority_replay(
    tmp_path: Path,
    monkeypatch,
    crash_phase: str,
    durable_phase: str,
    stage_present: bool,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / crash_phase
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()
    crashed = False

    def crash(phase: str) -> None:
        nonlocal crashed
        if phase == crash_phase and not crashed:
            crashed = True
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match=crash_phase):
        first.run()

    record = executor._read_terminal_record(root)
    assert record["phase"] == durable_phase
    stage = root / executor._TERMINAL_STAGE_LEAF
    assert stage.exists() is stage_present
    if stage_present:
        assert stat.S_IMODE(stage.stat().st_mode) == 0o600
        assert set(path.name for path in root.iterdir()) == {
            "journal.json",
            executor._TERMINAL_STAGE_LEAF,
        }
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "durably staged or committed envelope must not replay authority"
        ),
        clock=lambda: 3000.0,
    )
    assert resumed.run()["status"] == "terminalized"
    assert executor._read_terminal_journal(root)["phase"] == "COMMITTED"
    assert not stage.exists()
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1


def test_terminal_final_stage_swap_preserves_prepared_for_one_budget_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "final-stage-swap"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()
    ordinary = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    original_replace = executor.os.replace
    original_exclusive_rename = executor._rename_directory_exclusive
    swapped = False
    saved_signed_stage = tmp_path / "attacker-saved-signed-stage"

    def swap_stage() -> None:
        nonlocal swapped
        if swapped:
            return
        original_replace(
            root / executor._TERMINAL_STAGE_LEAF,
            saved_signed_stage,
        )
        substituted = root / executor._TERMINAL_STAGE_LEAF
        substituted.write_bytes(b'{"substituted":true}\n')
        substituted.chmod(0o600)
        swapped = True

    def replace_(source, destination, *args, **kwargs):
        if (
            Path(os.fspath(source)).name == executor._TERMINAL_STAGE_LEAF
            and Path(os.fspath(destination)).name == "journal.json"
        ):
            swap_stage()
        return original_replace(source, destination, *args, **kwargs)

    def exclusive_rename(directory: int, source: str, destination: str) -> None:
        if (
            source == executor._TERMINAL_STAGE_LEAF
            and destination == "journal.json"
        ):
            swap_stage()
        original_exclusive_rename(directory, source, destination)

    with monkeypatch.context() as attack:
        attack.setattr(executor.os, "replace", replace_)
        attack.setattr(executor, "_rename_directory_exclusive", exclusive_rename)
        first = executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=ordinary,
            authority_request=_authority_result,
            action="terminalize",
            terminal_observers=terminal,
            canonical_recovery_observer=recovery,
            terminal_authority_request=issuer,
            clock=lambda: 1000.0,
        )
        with pytest.raises(executor.AdoptionError):
            first.run()

    assert swapped is True
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert executor._read_terminal_prepared(root)["phase"] == "REQUEST_PREPARED"
    assert set(path.name for path in root.iterdir()) == {"journal.json"}

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=ordinary,
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 2000.0,
    )
    assert resumed.run()["status"] == "terminalized"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 2
    assert issuer.calls[0] == issuer.calls[1]
    assert len(issuer.decision_ids) == 1
    assert len(issuer.transaction_ids) == 1
    assert len(issuer.envelope_digests) == 1
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0
    assert [adapter.apply_count for adapter in ordinary] == [0, 0]
    assert [adapter.rollback_count for adapter in ordinary] == [0, 0]
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


@pytest.mark.parametrize(
    ("phase", "expected_names"),
    [
        (
            "TERMINAL_PREPARED_BACKED_UP",
            {_PREPARED_BACKUP_LEAF, executor._TERMINAL_STAGE_LEAF},
        ),
        (
            "TERMINAL_FINAL_BOUND",
            {_PREPARED_BACKUP_LEAF, "journal.json"},
        ),
    ],
)
def test_terminal_no_clobber_finalization_crash_recovers_without_authority(
    tmp_path: Path,
    monkeypatch,
    phase: str,
    expected_names: set[str],
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / phase
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()

    def crash(current: str) -> None:
        if current == phase:
            raise executor.InjectedCrash(current)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match=phase):
        first.run()

    assert set(path.name for path in root.iterdir()) == expected_names
    backup = root / _PREPARED_BACKUP_LEAF
    assert backup.is_file()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    prepared = json.loads(backup.read_text(encoding="ascii"))
    assert prepared["phase"] == "REQUEST_PREPARED"
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "durable no-clobber finalization must not replay authority"
        ),
        clock=lambda: 3000.0,
    )
    assert resumed.run()["status"] == "terminalized"
    assert executor._read_terminal_journal(root)["phase"] == "COMMITTED"
    assert executor._read_terminal_journal(root)["host_mutation_count"] == 0
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1
    assert set(path.name for path in root.iterdir()) == {"journal.json"}


@pytest.mark.parametrize("drift", ["bytes", "mode", "symlink", "prepared"])
def test_terminal_durable_stage_tamper_fails_closed_without_authority_replay(
    tmp_path: Path,
    monkeypatch,
    drift: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / drift
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()

    def crash(phase: str) -> None:
        if phase == "TERMINAL_FINAL_READY":
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_FINAL_READY"):
        first.run()

    stage = root / executor._TERMINAL_STAGE_LEAF
    if drift == "bytes":
        staged = json.loads(stage.read_text(encoding="ascii"))
        staged["envelope_digest"] = "9" * 64
        stage.write_bytes(executor._json_bytes(staged))
        stage.chmod(0o600)
    elif drift == "mode":
        stage.chmod(0o644)
    elif drift == "symlink":
        outside = tmp_path / "outside-stage.json"
        outside.write_bytes(stage.read_bytes())
        outside.chmod(0o600)
        stage.unlink()
        stage.symlink_to(outside)
    else:
        journal = root / "journal.json"
        prepared = json.loads(journal.read_text(encoding="ascii"))
        prepared["request_digest"] = "9" * 64
        journal.write_bytes(executor._json_bytes(prepared))
        journal.chmod(0o600)

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "durable stage recovery must never replay authority"
        ),
        clock=lambda: 3000.0,
    )
    with pytest.raises(executor.AdoptionError):
        resumed.run()
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1


def test_terminal_durable_stage_rechecks_host_cas_before_atomic_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "stage-host-cas"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()

    def crash(phase: str) -> None:
        if phase == "TERMINAL_FINAL_READY":
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_FINAL_READY"):
        first.run()

    original_observe = terminal[1].observe
    resume_observations = 0

    def drift_on_second_resume_observation():
        nonlocal resume_observations
        resume_observations += 1
        if resume_observations == 2:
            terminal[1].state = replace(
                terminal[1].state,
                orphan_marker_digest="9" * 64,
            )
        return original_observe()

    terminal[1].observe = drift_on_second_resume_observation
    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "durable stage recovery must never replay authority"
        ),
        clock=lambda: 3000.0,
    )
    with pytest.raises(executor.AdoptionError, match="terminal_current_state_drift"):
        resumed.run()
    assert executor._read_terminal_prepared(root)["phase"] == "REQUEST_PREPARED"
    assert (root / executor._TERMINAL_STAGE_LEAF).exists()
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 1


def test_terminal_stage_publish_uses_exclusive_collision_denial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "stage-collision"

    def collide(request, *, now, prepared_replay=False):
        verified = _terminal_authority_result(
            request,
            now=now,
            prepared_replay=prepared_replay,
        )
        stage = root / executor._TERMINAL_STAGE_LEAF
        stage.write_bytes(b"{}\n")
        stage.chmod(0o600)
        return verified

    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=collide,
        clock=lambda: 1000.0,
    )
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_journal_publish_failed",
    ):
        run.run()
    assert executor._read_terminal_prepared(root)["phase"] == "REQUEST_PREPARED"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "request_bytes",
        "request_digest",
        "host_state",
        "canonical_recovery",
    ],
)
def test_terminal_prepared_tamper_denies_before_authority_replay(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / mutation
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())

    def crash(phase: str) -> None:
        if phase == "TERMINAL_REQUEST_PREPARED":
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "crash before authority must not consume a decision"
        ),
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_REQUEST_PREPARED"):
        first.run()

    journal = root / "journal.json"
    record = json.loads(journal.read_text(encoding="ascii"))
    if mutation == "missing":
        del record["current_identity_digest"]
    elif mutation == "extra":
        record["path"] = "/forbidden"
    elif mutation == "request_bytes":
        changed = b"{}"
        record["request_b64"] = base64.b64encode(changed).decode("ascii")
        record["request_digest"] = hashlib.sha256(changed).hexdigest()
    elif mutation == "request_digest":
        record["request_digest"] = "9" * 64
    elif mutation == "host_state":
        record["before_states"][0]["cache_digest"] = "9" * 64
        record["after_states"][0]["cache_digest"] = "9" * 64
    elif mutation == "canonical_recovery":
        record["canonical_recovery"]["source_bundle_digest"] = "9" * 64
    journal.write_bytes(executor._json_bytes(record))
    journal.chmod(0o600)

    authority_calls = 0

    def forbidden_authority(*_args, **_kwargs):
        nonlocal authority_calls
        authority_calls += 1
        pytest.fail("invalid prepared bytes must fail before issuer replay")

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=forbidden_authority,
        clock=lambda: 2000.0,
    )
    with pytest.raises(executor.AdoptionError):
        resumed.run()
    assert authority_calls == 0
    assert json.loads(journal.read_text(encoding="ascii"))["phase"] == (
        "REQUEST_PREPARED"
    )


@pytest.mark.parametrize("drift", ["mode", "symlink"])
def test_terminal_prepared_metadata_drift_denies_before_authority_replay(
    tmp_path: Path,
    monkeypatch,
    drift: str,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / drift
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())

    def crash(phase: str) -> None:
        if phase == "TERMINAL_REQUEST_PREPARED":
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_REQUEST_PREPARED"):
        first.run()

    journal = root / "journal.json"
    if drift == "mode":
        journal.chmod(0o644)
    else:
        outside = tmp_path / "outside-prepared.json"
        outside.write_bytes(journal.read_bytes())
        outside.chmod(0o600)
        journal.unlink()
        journal.symlink_to(outside)

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=lambda *_args, **_kwargs: pytest.fail(
            "prepared metadata drift must fail before issuer replay"
        ),
        clock=lambda: 2000.0,
    )
    with pytest.raises(executor.AdoptionError):
        resumed.run()


def test_terminal_prepared_without_stored_envelope_expires_without_new_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "expired-unissued"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())

    def crash(phase: str) -> None:
        if phase == "TERMINAL_REQUEST_PREPARED":
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_REQUEST_PREPARED"):
        first.run()

    calls = 0

    def no_stored_envelope(request, *, now, prepared_replay=False):
        nonlocal calls
        calls += 1
        assert prepared_replay is True
        assert now > request["actual"]["expires_at"]
        raise authority.PluginAdoptionAuthorityError("authority_stale")

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=no_stored_envelope,
        clock=lambda: 2000.0,
    )
    with pytest.raises(
        authority.PluginAdoptionAuthorityError,
        match="authority_stale",
    ):
        resumed.run()
    assert calls == 1
    assert executor._read_terminal_prepared(root)["phase"] == "REQUEST_PREPARED"


def test_terminal_authorized_changed_prepared_request_is_replay_denied(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "changed-request"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    recovery = TerminalRecoveryObserver(_canonical_recovery())
    issuer = ReplayAwareTerminalIssuer()

    def crash(phase: str) -> None:
        if phase == "TERMINAL_AUTHORIZED":
            raise executor.InjectedCrash(phase)

    first = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_AUTHORIZED"):
        first.run()
    assert issuer.consumed_budget == 1

    prepared = executor._read_terminal_prepared(root)
    old_request = authority._base._parse_canonical_authority_payload(
        base64.b64decode(str(prepared["request_b64"]), validate=True)
    )
    changed_request = authority.build_plugin_adoption_terminal_request(
        decision_id=old_request["actual"]["decision_id"],
        transaction_id=old_request["actual"]["transaction_id"],
        source_runtime_revision=authority.TERMINAL_SOURCE_REVISION,
        issued_at=1001.0,
        expires_at=1121.0,
        plan=old_request["plan"],
    )
    changed_bytes = authority.canonical_bytes(changed_request)
    prepared["request_b64"] = base64.b64encode(changed_bytes).decode("ascii")
    prepared["request_digest"] = hashlib.sha256(changed_bytes).hexdigest()
    (root / "journal.json").write_bytes(executor._json_bytes(prepared))
    (root / "journal.json").chmod(0o600)

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=recovery,
        terminal_authority_request=issuer,
        clock=lambda: 1001.0,
    )
    with pytest.raises(
        authority.PluginAdoptionAuthorityError,
        match="authority_replay_request_mismatch",
    ):
        resumed.run()
    assert issuer.consumed_budget == 1
    assert len(issuer.calls) == 2
    assert issuer.calls[0] != issuer.calls[1]
    assert executor._read_terminal_prepared(root)["phase"] == "REQUEST_PREPARED"


def test_ordinary_resume_rejects_terminal_prepared_journal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "ordinary-rejects-prepared"

    def crash(phase: str) -> None:
        if phase == "TERMINAL_REQUEST_PREPARED":
            raise executor.InjectedCrash(phase)

    terminal = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
        crash_hook=crash,
    )
    with pytest.raises(executor.InjectedCrash, match="TERMINAL_REQUEST_PREPARED"):
        terminal.run()

    ordinary = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.AdoptionError, match="journal_contract_mismatch"):
        executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=ordinary,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "ordinary resume must reject terminal prepared before authority"
            ),
        ).run()
    assert [adapter.prepare_count for adapter in ordinary] == [0, 0]
    assert [adapter.apply_count for adapter in ordinary] == [0, 0]
    assert [adapter.rollback_count for adapter in ordinary] == [0, 0]


def test_terminal_journal_collision_and_ordinary_resume_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "collision"

    def collide(request, *, now, prepared_replay=False):
        verified = _terminal_authority_result(
            request,
            now=now,
            prepared_replay=prepared_replay,
        )
        journal = root / "journal.json"
        journal.write_bytes(b"{}\n")
        journal.chmod(0o600)
        return verified

    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=collide,
        clock=lambda: 1000.0,
    )
    with pytest.raises(executor.AdoptionError, match="terminal_prepared_drift"):
        run.run()
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_journal_contract_mismatch",
    ):
        run.run()

    committed_root = tmp_path / "committed"
    committed = executor.PluginAdoptionExecutor(
        state_root=committed_root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
    )
    assert committed.run()["status"] == "terminalized"
    ordinary = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.AdoptionError, match="journal_contract_mismatch"):
        executor.PluginAdoptionExecutor(
            state_root=committed_root,
            adapters=ordinary,
            authority_request=lambda *_args, **_kwargs: pytest.fail(
                "ordinary resume must not consume terminal authority"
            ),
        ).run()
    monkeypatch.setattr(executor, "_previous_state_root", lambda: committed_root)
    with pytest.raises(executor.AdoptionError, match="journal_contract_mismatch"):
        executor._previous_committed_context()
    assert [adapter.apply_count for adapter in ordinary] == [0, 0]
    assert [adapter.rollback_count for adapter in ordinary] == [0, 0]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "host_state",
        "canonical_recovery",
        "request_bytes",
        "envelope_digest",
    ],
)
def test_terminal_journal_tampering_is_denied(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / mutation
    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
    )
    assert run.run()["status"] == "terminalized"
    record = json.loads(json.dumps(executor._read_terminal_journal(root)))
    if mutation == "missing":
        del record["current_identity_digest"]
    elif mutation == "extra":
        record["path"] = "/forbidden"
    elif mutation == "host_state":
        record["before_states"][0]["cache_digest"] = "9" * 64
        record["after_states"][0]["cache_digest"] = "9" * 64
    elif mutation == "canonical_recovery":
        record["canonical_recovery"]["source_bundle_digest"] = "9" * 64
    elif mutation == "request_bytes":
        record["request_b64"] = base64.b64encode(b"{}").decode("ascii")
    elif mutation == "envelope_digest":
        record["envelope_digest"] = "9" * 64
    with pytest.raises(executor.AdoptionError):
        executor._reverify_terminal_journal(record)


def test_terminal_journal_mode_symlink_and_signer_drift_are_denied(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)
    root = tmp_path / "journal-drift"
    terminal = tuple(
        TerminalMemoryObserver(_terminal_state(host))
        for host in executor.HOST_ORDER
    )
    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=terminal,
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
    )
    assert run.run()["status"] == "terminalized"
    journal = root / "journal.json"
    journal.chmod(0o644)
    with pytest.raises(executor.AdoptionError, match="journal_drift"):
        run.run()
    journal.chmod(0o600)
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: False)
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_journal_authority_verification_failed",
    ):
        run.run()

    outside = tmp_path / "outside.json"
    outside.write_bytes(journal.read_bytes())
    outside.chmod(0o600)
    journal.unlink()
    journal.symlink_to(outside)
    with pytest.raises(executor.AdoptionError, match="terminal_journal_unavailable"):
        run.run()


def test_terminalize_rejects_state_root_and_lock_metadata_drift(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "mode-drift"
    root.mkdir(mode=0o755)
    run = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=(MemoryAdapter("codex"), MemoryAdapter("claude")),
        authority_request=_authority_result,
        action="terminalize",
        terminal_observers=tuple(
            TerminalMemoryObserver(_terminal_state(host))
            for host in executor.HOST_ORDER
        ),
        canonical_recovery_observer=TerminalRecoveryObserver(_canonical_recovery()),
        terminal_authority_request=_terminal_authority_result,
        clock=lambda: 1000.0,
    )
    with pytest.raises(executor.AdoptionError, match="protected_state_drift"):
        run.run()

    root.chmod(0o700)
    lock_root = root.parent / ".plugin-adoption-locks"
    lock_root.mkdir(mode=0o700)
    lock = lock_root / "codex.lock"
    lock.write_bytes(b"")
    lock.chmod(0o644)
    with pytest.raises(executor.AdoptionError, match="host_lock_drift"):
        run.run()

    lock.chmod(0o600)
    alias = lock_root / "codex-alias.lock"
    os.link(lock, alias)
    with pytest.raises(executor.AdoptionError, match="host_lock_drift"):
        run.run()
    alias.unlink()
    legacy = root / "legacy-evidence.json"
    legacy.write_text("{}\n", encoding="ascii")
    legacy.chmod(0o600)
    with pytest.raises(executor.AdoptionError, match="terminal_state_root_not_fresh"):
        run.run()
    legacy.unlink()
    monkeypatch.setattr(
        executor.fcntl,
        "flock",
        lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
    )
    with pytest.raises(executor.AdoptionError, match="host_lock_unavailable"):
        run.run()


def test_host_lock_rejects_same_uid_path_swap_after_flock(tmp_path: Path) -> None:
    root = tmp_path / "transaction"
    root.mkdir(mode=0o700)
    lock_root = root.parent / ".plugin-adoption-locks"
    with pytest.raises(executor.AdoptionError, match="host_lock_drift"):
        with executor._host_locks(root, typed_failure=True):
            lock = lock_root / "codex.lock"
            displaced = lock_root / "codex.displaced"
            lock.rename(displaced)
            replacement = lock_root / "codex.lock"
            replacement.write_bytes(b"")
            replacement.chmod(0o600)


def test_main_terminalize_wires_only_fixed_read_only_consumers(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "fresh-terminal-v048"
    source_root = tmp_path / "plugin-adoption-v048"
    recovery_root = tmp_path / "hermes-fp1-state-schema-20260814"
    seen: dict[str, object] = {}

    class Observer:
        def __init__(
            self,
            name: str,
            transaction_root: Path,
            marketplace_transaction_root: Path,
        ):
            self.name = name
            seen.setdefault("observer_roots", []).append(transaction_root)
            seen.setdefault("observer_source_roots", []).append(
                marketplace_transaction_root
            )

    class RecoveryObserver:
        def __init__(self, source_root: Path):
            seen["recovery_root"] = source_root

    class Runner:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            return {"status": "terminalized", "transaction_id": "terminal-fixture"}

    monkeypatch.setattr(executor, "_fixed_terminal_state_root", lambda: root)
    monkeypatch.setattr(
        executor,
        "_fixed_terminal_source_root",
        lambda: source_root,
    )
    monkeypatch.setattr(
        executor,
        "_fixed_canonical_recovery_root",
        lambda: recovery_root,
    )
    monkeypatch.setattr(executor, "FixedTerminalHostObserver", Observer)
    monkeypatch.setattr(executor, "FixedCanonicalRecoveryObserver", RecoveryObserver)
    monkeypatch.setattr(executor, "PluginAdoptionExecutor", Runner)

    assert executor.main(["terminalize"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "terminalized",
        "transaction_id": "terminal-fixture",
    }
    assert seen["state_root"] == root
    assert seen["action"] == "terminalize"
    assert seen["adapters"] == ()
    assert [observer.name for observer in seen["terminal_observers"]] == [
        "codex",
        "claude",
    ]
    assert seen["observer_roots"] == [root, root]
    assert seen["observer_source_roots"] == [source_root, source_root]
    assert seen["recovery_root"] == recovery_root
    assert seen["terminal_authority_request"] is (
        authority.request_plugin_adoption_terminal_decision
    )


@pytest.mark.parametrize(
    "resume_leaf",
    (
        executor._ORDINARY_PREPARED_LEAF,
        executor._TERMINAL_BRIDGE_MANIFEST_LEAF,
    ),
)
def test_main_resume_admits_durable_bridge_without_published_journal(
    tmp_path: Path,
    monkeypatch,
    capsys,
    resume_leaf: str,
) -> None:
    root = tmp_path / "ordinary-v049"
    root.mkdir(mode=0o700)
    durable_bridge = root / resume_leaf
    durable_bridge.write_bytes(b"bridge-fixture\n")
    durable_bridge.chmod(0o600)
    terminal_root = tmp_path / "terminal-v048"
    terminal_source_root = tmp_path / "plugin-adoption-v048"
    recovery_root = tmp_path / "recovery"
    seen: dict[str, object] = {}

    class Adapter:
        def __init__(self, name: str, transaction_root: Path, **kwargs):
            self.name = name
            seen.setdefault("adapter_roots", []).append(transaction_root)
            seen.setdefault("adapter_source_roots", []).append(
                kwargs["terminal_source_transaction_root"]
            )

    class Observer:
        def __init__(
            self,
            name: str,
            transaction_root: Path,
            marketplace_transaction_root: Path,
        ):
            self.name = name
            seen.setdefault("observer_roots", []).append(transaction_root)
            seen.setdefault("observer_source_roots", []).append(
                marketplace_transaction_root
            )

    class RecoveryObserver:
        def __init__(self, source_root: Path):
            seen["recovery_root"] = source_root

    class Runner:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            return {"status": "committed", "transaction_id": "resume-fixture"}

    monkeypatch.setattr(executor, "_fixed_state_root", lambda: root)
    monkeypatch.setattr(
        executor, "_fixed_terminal_state_root", lambda: terminal_root
    )
    monkeypatch.setattr(
        executor,
        "_fixed_terminal_source_root",
        lambda: terminal_source_root,
    )
    monkeypatch.setattr(
        executor,
        "_fixed_canonical_recovery_root",
        lambda: recovery_root,
    )
    monkeypatch.setattr(executor, "FixedHostAdapter", Adapter)
    monkeypatch.setattr(executor, "FixedTerminalHostObserver", Observer)
    monkeypatch.setattr(
        executor, "FixedCanonicalRecoveryObserver", RecoveryObserver
    )
    monkeypatch.setattr(executor, "PluginAdoptionExecutor", Runner)

    assert executor.main(["resume"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "committed",
        "transaction_id": "resume-fixture",
    }
    assert seen["state_root"] == root
    assert seen["terminal_bridge_root"] == terminal_root
    assert seen["adapter_source_roots"] == [
        terminal_source_root,
        terminal_source_root,
    ]
    assert seen["observer_roots"] == [terminal_root, terminal_root]
    assert seen["observer_source_roots"] == [
        terminal_source_root,
        terminal_source_root,
    ]


@pytest.fixture()
def fixed_source(monkeypatch):
    monkeypatch.setattr(executor, "_source_revision", lambda: "1" * 40)
    monkeypatch.setattr(executor, "_source_bundle_digest", lambda: "2" * 64)
    monkeypatch.setattr(
        executor,
        "_candidate_installed_digests",
        lambda _scratch_root: ("9" * 64, "7" * 64),
    )
    monkeypatch.setattr(authority._base, "_verify_sshsig", lambda *_args: True)


def _runner(
    root: Path,
    adapters,
    *,
    crash_phase: str | None = None,
    archive_rolled_back: bool = False,
    authority_request=_authority_result,
):
    crashed = False

    def crash_hook(phase: str):
        nonlocal crashed
        if phase == crash_phase and not crashed:
            crashed = True
            raise executor.InjectedCrash(phase)

    return executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=adapters,
        authority_request=authority_request,
        clock=lambda: 1000.0,
        crash_hook=crash_hook,
        archive_rolled_back=archive_rolled_back,
    )


def test_complete_transaction_uses_canonical_host_order_and_one_authority(
    tmp_path: Path, fixed_source
) -> None:
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    authority_calls = 0

    def counted(request, *, now):
        nonlocal authority_calls
        authority_calls += 1
        return _authority_result(request, now=now)

    run = executor.PluginAdoptionExecutor(
        state_root=tmp_path / "state",
        adapters=adapters,
        authority_request=counted,
        clock=lambda: 1000.0,
    )
    assert run.run()["status"] == "committed"
    assert authority_calls == 1
    assert [adapter.state for adapter in adapters] == [_after("codex"), _after("claude")]
    assert executor._read_journal(tmp_path / "state")["phase"] == "COMMITTED"


@pytest.mark.parametrize("phase", executor.FORWARD_PHASES)
def test_crash_after_every_forward_phase_resumes_without_re_request(
    tmp_path: Path, fixed_source, phase: str
) -> None:
    root = tmp_path / phase.lower()
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match=phase):
        _runner(root, adapters, crash_phase=phase).run()
    assert executor._read_journal(root)["phase"] == phase

    def forbidden_authority(*_args, **_kwargs):
        raise AssertionError("resume must not request another ALLOW")

    resumed = executor.PluginAdoptionExecutor(
        state_root=root,
        adapters=adapters,
        authority_request=forbidden_authority,
        clock=lambda: 9999.0,
    )
    assert resumed.run()["status"] == "committed"
    assert executor._read_journal(root)["phase"] == "COMMITTED"


def test_one_host_partial_success_never_claims_complete(tmp_path: Path, fixed_source) -> None:
    root = tmp_path / "partial"
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match="CODEX_APPLIED"):
        _runner(root, adapters, crash_phase="CODEX_APPLIED").run()
    record = executor._read_journal(root)
    assert record["phase"] == "CODEX_APPLIED"
    assert adapters[0].state == _after("codex")
    assert adapters[1].state == _before("claude")
    assert record["phase"] != "COMMITTED"


def test_committed_resume_reverifies_both_hosts_before_claim(
    tmp_path: Path, fixed_source
) -> None:
    root = tmp_path / "committed-drift"
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match="COMMITTED"):
        _runner(root, adapters, crash_phase="COMMITTED").run()
    adapters[1].state = _before("claude")
    with pytest.raises(executor.AdoptionError, match="after_state_mismatch"):
        _runner(root, adapters).run()
    assert executor._read_journal(root)["phase"] == "COMMITTED"


def test_host_failure_rolls_back_both_and_persists_terminal_state(
    tmp_path: Path, fixed_source
) -> None:
    root = tmp_path / "rollback"
    adapters = (
        MemoryAdapter("codex"),
        MemoryAdapter("claude", fail_apply=True),
    )
    with pytest.raises(executor.AdoptionError, match="plugin_adoption_rolled_back"):
        _runner(root, adapters).run()
    assert [adapter.state for adapter in adapters] == [_before("codex"), _before("claude")]
    assert executor._read_journal(root)["phase"] == "ROLLED_BACK"


def _write_previous_terminal_journal(
    root: Path,
    *,
    phase: str = "ROLLED_BACK",
) -> str:
    before = [_before(host) for host in executor.HOST_ORDER]
    previous_after = [
        replace(_after(host), plugin_version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION)
        for host in executor.HOST_ORDER
    ]
    plan_without_digest = {
        "marketplace_id": authority.MARKETPLACE_ID,
        "plugin_id": authority.PLUGIN_ID,
        "plugin_version": authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
        "source_revision": "0" * 40,
        "source_bundle_digest": "2" * 64,
        "target_set": list(authority.TARGET_SET),
        "transition_set": list(authority.TRANSITION_SET),
        "before_state_digest": executor._states_digest(before),
        "after_state_digest": executor._states_digest(previous_after),
        "rollback_manifest_digest": executor._canonical_digest(
            executor._rollback_manifest(before)
        ),
    }
    plan = {
        **plan_without_digest,
        "plan_digest": authority.compute_plan_digest(plan_without_digest),
    }
    request = authority._build_plugin_adoption_request(
        decision_id="previous-terminal-decision",
        transaction_id="previous-terminal-transaction",
        source_runtime_revision="0" * 40,
        issued_at=900.0,
        expires_at=1020.0,
        plan=plan,
        plugin_version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
    )
    request_bytes = authority.canonical_bytes(request)
    envelope_bytes = authority.canonical_bytes({
        "receipt": _receipt(request),
        "signature": (
            "-----BEGIN SSH SIGNATURE-----\nfixture\n"
            "-----END SSH SIGNATURE-----\n"
        ),
    })
    with pytest.raises(
        authority.PluginAdoptionAuthorityError,
        match="authority_contract_unavailable",
    ):
        authority.verify_plugin_adoption_envelope(
            request_bytes=request_bytes,
            envelope_bytes=envelope_bytes,
            now=900.001,
        )
    verified = authority.verify_previous_terminal_plugin_adoption_envelope(
        request_bytes=request_bytes,
        envelope_bytes=envelope_bytes,
        now=900.001,
    )
    record = executor._record_from_verified(
        verified,
        before=before,
        after=previous_after,
    )
    executor._write_journal(root, {**record, "phase": phase})
    return str(record["transaction_id"])


def test_previous_committed_journal_admits_one_sequential_upgrade(
    tmp_path: Path, fixed_source
) -> None:
    previous_root = tmp_path / "previous"
    previous_root.mkdir(mode=0o700)
    _write_previous_terminal_journal(previous_root, phase="COMMITTED")
    previous_record = executor._read_journal(previous_root)
    verified = executor._reverify_journal(previous_record)
    previous_states = tuple(
        executor.HostState.from_projection(value, expected_host=host)
        for value, host in zip(
            previous_record["after_states"], executor.HOST_ORDER, strict=True
        )
    )
    assert verified.request["plan"]["plugin_version"] == (
        authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
    )

    adapters = tuple(
        MemoryAdapter(host, initial_state=state)
        for host, state in zip(executor.HOST_ORDER, previous_states, strict=True)
    )
    result = executor.PluginAdoptionExecutor(
        state_root=tmp_path / "current",
        adapters=adapters,
        authority_request=_authority_result,
        clock=lambda: 1000.0,
        admitted_previous_states=previous_states,
    ).run()
    assert result["status"] == "committed"
    assert [adapter.state for adapter in adapters] == [
        _after("codex"),
        _after("claude"),
    ]


def test_resume_reloads_the_signed_previous_context(tmp_path: Path) -> None:
    root = tmp_path / "current"
    root.mkdir(mode=0o700)
    assert executor._previous_context_required("apply", root)
    assert executor._previous_context_required("resume", root)
    (root / "journal.json").write_bytes(b"current")
    assert executor._previous_context_required("apply", root)
    assert executor._previous_context_required("resume", root)
    assert not executor._previous_context_required("status", root)


def test_claude_reinstall_accepts_the_exact_previous_terminal_version(
    tmp_path: Path, monkeypatch
) -> None:
    current_root = tmp_path / "current"
    previous_root = tmp_path / "previous"
    current_root.mkdir(mode=0o700)
    previous_root.mkdir(mode=0o700)
    adapter = executor.FixedHostAdapter.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._transaction_root = current_root
    adapter._previous_transaction_root = previous_root
    calls: list[tuple[str, ...]] = []

    def run(arguments, *, json_output=False):
        calls.append(tuple(arguments))
        if json_output:
            return [{
                "id": "orch-next-hermes-harness@orch-next-hermes-local",
                "version": authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
                "enabled": True,
            }]
        return None

    monkeypatch.setattr(adapter, "_run", run)
    adapter._install_from(previous_root / "stage" / "claude")
    assert calls[-1] == ("plugin", "list", "--json")


def test_sequential_apply_quarantines_a_cli_retained_previous_cache(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(executor, "_fixed_user_home", lambda: tmp_path)
    cache_parent = tmp_path / "cache"
    source = cache_parent / authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
    source.mkdir(parents=True, mode=0o700)
    (source / "skill.txt").write_text("previous", encoding="ascii")
    transaction = tmp_path / "transaction"
    transaction.mkdir(mode=0o700)
    adapter = executor.FixedHostAdapter.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._cache = cache_parent / authority.PLUGIN_VERSION
    adapter._transaction_root = transaction
    digest = executor._tree_digest(source, ignored=frozenset({".in_use"}))
    assert digest is not None
    previous = executor.HostState(
        host="claude",
        marketplace_present=True,
        marketplace_digest="1" * 64,
        marketplace_binding_digest="2" * 64,
        plugin_present=True,
        plugin_version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
        active=True,
        # The fixed CLI may rewrite its retained cache after the pre-operation
        # CAS. The private copy is non-authoritative; rollback uses the signed
        # previous stage instead of trusting this post-operation digest.
        cache_digest="f" * 64,
    )
    adapter._previous_state = previous

    adapter._quarantine_previous_active_cache(previous)

    previous_handle = "previous-active-v" + (
        authority.PREVIOUS_TERMINAL_PLUGIN_VERSION.replace(".", "")
    )
    destination = transaction / "quarantine" / "claude" / previous_handle
    assert not source.exists()
    assert executor._tree_digest(
        destination, ignored=frozenset({".in_use"})
    ) == digest


def test_previous_committed_admission_rejects_state_drift(fixed_source) -> None:
    previous_states = tuple(
        replace(
            _after(host),
            plugin_version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
        )
        for host in executor.HOST_ORDER
    )
    drifted = list(previous_states)
    drifted[1] = replace(drifted[1], active=False)
    with pytest.raises(
        executor.AdoptionError, match="before_state_not_exactly_reversible"
    ):
        executor._require_reversible_before_states(
            drifted,
            admitted_previous=previous_states,
        )


def test_apply_archives_previous_signed_reverified_terminal_and_starts_new(
    tmp_path: Path, fixed_source
) -> None:
    root = tmp_path / "archive-and-restart"
    root.mkdir(mode=0o700)
    previous_transaction = _write_previous_terminal_journal(root)
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    authority_calls = 0

    def counted(request, *, now):
        nonlocal authority_calls
        authority_calls += 1
        return _authority_result(request, now=now)

    result = _runner(
        root,
        adapters,
        archive_rolled_back=True,
        authority_request=counted,
    ).run()
    assert result["status"] == "committed"
    assert result["archived_transaction_id"] == previous_transaction
    assert result["transaction_id"] != previous_transaction
    assert authority_calls == 1
    archive = executor._history_root(root) / previous_transaction
    assert not archive.is_symlink()
    assert stat.S_IMODE(archive.stat().st_mode) == 0o700
    assert executor._read_journal(archive)["phase"] == "ROLLED_BACK"
    marker = executor._private_file_bytes(archive / "archive.json")
    assert b'"phase": "ROLLED_BACK"' in marker
    assert executor._read_journal(root)["phase"] == "COMMITTED"
    assert executor._lock_root(root).is_dir()


@pytest.mark.parametrize("crash_phase", ["TERMINAL_ARCHIVE_READY", "TERMINAL_ARCHIVED"])
def test_terminal_archive_crash_is_retryable_without_evidence_loss(
    tmp_path: Path, fixed_source, crash_phase: str
) -> None:
    root = tmp_path / crash_phase.lower()
    root.mkdir(mode=0o700)
    previous_transaction = _write_previous_terminal_journal(root)
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match=crash_phase):
        _runner(
            root,
            adapters,
            archive_rolled_back=True,
            crash_phase=crash_phase,
        ).run()
    archive = executor._history_root(root) / previous_transaction
    if crash_phase == "TERMINAL_ARCHIVE_READY":
        assert executor._read_journal(root)["phase"] == "ROLLED_BACK"
        assert not archive.exists()
        result = _runner(root, adapters, archive_rolled_back=True).run()
        assert result["archived_transaction_id"] == previous_transaction
    else:
        assert not root.exists()
        assert executor._read_journal(archive)["phase"] == "ROLLED_BACK"
        assert _runner(root, adapters, archive_rolled_back=True).run()["status"] == "committed"
    assert executor._read_journal(archive)["phase"] == "ROLLED_BACK"


def test_terminal_archive_refuses_host_drift_and_existing_history(
    tmp_path: Path, fixed_source
) -> None:
    root = tmp_path / "archive-negatives"
    root.mkdir(mode=0o700)
    transaction_id = _write_previous_terminal_journal(root)
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    adapters[0].state = replace(_before("codex"), active=False)
    with pytest.raises(executor.AdoptionError, match="rolled_back_state_drift"):
        _runner(root, adapters, archive_rolled_back=True).run()
    assert executor._read_journal(root)["phase"] == "ROLLED_BACK"

    adapters[0].state = _before("codex")
    history = executor._history_root(root)
    history.mkdir(mode=0o700)
    collision = history / transaction_id
    collision.mkdir(mode=0o700)
    with pytest.raises(executor.AdoptionError, match="terminal_archive_exists"):
        _runner(root, adapters, archive_rolled_back=True).run()
    assert executor._read_journal(root)["phase"] == "ROLLED_BACK"


def test_terminal_archive_requires_exact_reverified_journal_bytes(
    tmp_path: Path, fixed_source
) -> None:
    root = tmp_path / "journal-bytes"
    root.mkdir(mode=0o700)
    _write_previous_terminal_journal(root)
    record = executor._read_journal(root)
    executor._atomic_private_write(
        executor._journal_path(root),
        executor._journal_bytes(record) + b" ",
    )
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_archive_journal_mismatch",
    ):
        executor._archive_terminal_transaction(root, record)
    assert not (executor._history_root(root) / record["transaction_id"]).exists()


@pytest.mark.parametrize("mutation", ["root", "journal", "entry"])
def test_terminal_archive_rejects_same_uid_swap_before_publish(
    tmp_path: Path,
    fixed_source,
    mutation: str,
) -> None:
    root = tmp_path / f"swap-{mutation}"
    root.mkdir(mode=0o700)
    _write_previous_terminal_journal(root)
    stage = root / "stage"
    stage.mkdir(mode=0o700)
    payload = stage / "payload"
    payload.write_bytes(b"exact-artifact")
    payload.chmod(0o600)
    record = executor._read_journal(root)

    def mutate(phase: str) -> None:
        if phase != "TERMINAL_ARCHIVE_READY":
            return
        if mutation == "root":
            original = root.with_name(root.name + "-bound")
            os.rename(root, original)
            shutil.copytree(original, root)
            root.chmod(0o700)
        elif mutation == "journal":
            executor._journal_path(root).write_bytes(
                executor._journal_bytes(record) + b" "
            )
        else:
            original = root.parent / "bound-stage"
            os.rename(stage, original)
            shutil.copytree(original, stage)

    with pytest.raises(executor.AdoptionError, match="terminal_archive_drift"):
        executor._archive_terminal_transaction(
            root,
            record,
            crash_hook=mutate,
        )
    archive = executor._history_root(root) / str(record["transaction_id"])
    assert not archive.exists()


@pytest.mark.parametrize("mutation", ["journal", "entry"])
def test_terminal_archive_rejects_swap_before_marker_baseline_capture(
    tmp_path: Path,
    fixed_source,
    mutation: str,
) -> None:
    root = tmp_path / f"before-marker-{mutation}"
    root.mkdir(mode=0o700)
    _write_previous_terminal_journal(root)
    stage = root / "stage"
    stage.mkdir(mode=0o700)
    payload = stage / "payload"
    payload.write_bytes(b"exact-artifact")
    payload.chmod(0o600)
    record = executor._read_journal(root)

    def mutate(phase: str) -> None:
        if phase != "TERMINAL_ARCHIVE_BEFORE_MARKER":
            return
        if mutation == "journal":
            executor._journal_path(root).write_bytes(
                executor._journal_bytes(record) + b" "
            )
        else:
            original = root.parent / "pre-marker-bound-stage"
            os.rename(stage, original)
            shutil.copytree(original, stage)

    with pytest.raises(executor.AdoptionError, match="terminal_archive_drift"):
        executor._archive_terminal_transaction(
            root,
            record,
            crash_hook=mutate,
        )
    archive = executor._history_root(root) / str(record["transaction_id"])
    assert not archive.exists()


@pytest.mark.parametrize("phase", ["ROLLING_BACK", "ROLLED_BACK"])
def test_crash_during_rollback_resumes_exact_inverse(
    tmp_path: Path, fixed_source, phase: str
) -> None:
    root = tmp_path / phase.lower()
    adapters = (
        MemoryAdapter("codex"),
        MemoryAdapter("claude", fail_apply=True),
    )
    with pytest.raises(executor.InjectedCrash, match=phase):
        _runner(root, adapters, crash_phase=phase).run()
    assert executor._read_journal(root)["phase"] == phase
    resumed = _runner(root, adapters)
    assert resumed.run()["status"] == "rolled_back"
    assert [adapter.state for adapter in adapters] == [_before("codex"), _before("claude")]


def test_before_state_swap_after_allow_fails_closed_and_rolls_back(
    tmp_path: Path, fixed_source
) -> None:
    root = tmp_path / "cas"
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match="PREPARED"):
        _runner(root, adapters, crash_phase="PREPARED").run()
    adapters[0].state = executor.HostState(
        host="codex",
        marketplace_present=True,
        marketplace_digest="8" * 64,
        marketplace_binding_digest="5" * 64,
        plugin_present=False,
        plugin_version=None,
        active=False,
        cache_digest=None,
    )
    with pytest.raises(executor.AdoptionError, match="plugin_adoption_rolled_back"):
        _runner(root, adapters).run()
    assert executor._read_journal(root)["phase"] == "ROLLED_BACK"


def test_journal_is_owner_private_sanitized_and_has_no_raw_host_material(
    tmp_path: Path, fixed_source
) -> None:
    root = tmp_path / "privacy"
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    _runner(root, adapters).run()
    journal_path = root / "journal.json"
    assert journal_path.stat().st_mode & 0o777 == 0o600
    record = executor._read_journal(root)
    assert set(record) == executor._JOURNAL_KEYS
    forbidden_keys = {
        "path",
        "command",
        "config",
        "credential",
        "token",
        "cookie",
        "prompt",
        "log",
        "url",
    }
    assert not (set(record) & forbidden_keys)
    visible = {
        key: value
        for key, value in record.items()
        if key not in {"request_b64", "envelope_b64"}
    }
    serialized = executor._json_bytes(visible).decode("ascii").lower()
    assert "/users/" not in serialized
    assert "api_key" not in serialized
    assert "private_url" not in serialized


def test_host_order_is_not_caller_selectable(tmp_path: Path, fixed_source) -> None:
    with pytest.raises(executor.AdoptionError, match="host_order_mismatch"):
        executor.PluginAdoptionExecutor(
            state_root=tmp_path / "state",
            adapters=(MemoryAdapter("claude"), MemoryAdapter("codex")),
            authority_request=_authority_result,
        )


def test_tree_digest_rejects_symlink_and_writable_mode(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir(mode=0o700)
    regular = tree / "regular.json"
    regular.write_text("{}\n", encoding="utf-8")
    regular.chmod(0o600)
    assert executor._tree_digest(tree) is not None

    link = tree / "link.json"
    link.symlink_to(regular)
    with pytest.raises(executor.AdoptionError, match="cache_metadata_drift"):
        executor._tree_digest(tree)
    link.unlink()

    regular.chmod(0o666)
    with pytest.raises(executor.AdoptionError, match="cache_metadata_drift"):
        executor._tree_digest(tree)


def _snapshot_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True, mode=0o700)
    source.chmod(0o700)
    payload = nested / "payload.json"
    payload.write_text('{"version":"0.1.48"}\n', encoding="ascii")
    payload.chmod(0o600)
    return source


def test_terminal_rollback_snapshot_is_exact_private_and_no_clobber(
    tmp_path: Path,
) -> None:
    source = _snapshot_source(tmp_path)
    destination_parent = tmp_path / "private"
    destination_parent.mkdir(mode=0o700)
    destination = destination_parent / "marketplace"
    digest = executor._tree_digest(source)
    assert digest is not None

    executor._copy_private_tree_exclusive(
        source,
        destination,
        expected_digest=digest,
    )
    assert executor._tree_digest(destination) == digest
    assert stat.S_IMODE(destination.lstat().st_mode) == 0o700
    assert stat.S_IMODE(
        (destination / "nested" / "payload.json").lstat().st_mode
    ) == 0o600

    (source / "nested" / "payload.json").write_text(
        '{"version":"drift"}\n', encoding="ascii"
    )
    assert executor._tree_digest(destination) == digest
    collision = destination_parent / "cache"
    collision.mkdir(mode=0o700)
    foreign = collision / "foreign"
    foreign.write_bytes(b"foreign")
    foreign.chmod(0o600)
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_capture_no_clobber",
    ):
        executor._copy_private_tree_exclusive(
            source,
            collision,
            expected_digest=digest,
        )


@pytest.mark.parametrize("mutation", ("symlink", "writable", "hardlink"))
def test_terminal_rollback_snapshot_rejects_unadmitted_source_metadata(
    tmp_path: Path,
    mutation: str,
) -> None:
    source = _snapshot_source(tmp_path)
    payload = source / "nested" / "payload.json"
    if mutation == "symlink":
        link = source / "nested" / "link.json"
        link.symlink_to(payload)
    elif mutation == "writable":
        payload.chmod(0o666)
    else:
        os.link(payload, source / "nested" / "hardlink.json")
    destination_parent = tmp_path / "private"
    destination_parent.mkdir(mode=0o700)
    with pytest.raises(executor.AdoptionError):
        executor._copy_private_tree_exclusive(
            source,
            destination_parent / "snapshot",
            expected_digest="a" * 64,
        )
    assert not (destination_parent / "snapshot").exists()


def test_terminal_rollback_snapshot_rechecks_source_cas_before_publish(
    tmp_path: Path,
) -> None:
    source = _snapshot_source(tmp_path)
    destination_parent = tmp_path / "private"
    destination_parent.mkdir(mode=0o700)
    destination = destination_parent / "snapshot"
    digest = executor._tree_digest(source)
    assert digest is not None

    def drift(phase: str) -> None:
        if phase == "TERMINAL_ROLLBACK_SNAPSHOT_READY":
            payload = source / "nested" / "payload.json"
            payload.write_text("drift\n", encoding="ascii")
            payload.chmod(0o600)

    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_capture_drift",
    ):
        executor._copy_private_tree_exclusive(
            source,
            destination,
            expected_digest=digest,
            crash_hook=drift,
        )
    assert not destination.exists()


@pytest.mark.parametrize(
    ("fault", "code"),
    (
        ("enospc", "terminal_rollback_capture_enospc"),
        ("cross_device", "terminal_rollback_capture_cross_device"),
        ("size", "terminal_rollback_capture_limit_exceeded"),
        ("count", "terminal_rollback_capture_limit_exceeded"),
    ),
)
def test_terminal_rollback_snapshot_reports_typed_resource_boundaries(
    tmp_path: Path,
    monkeypatch,
    fault: str,
    code: str,
) -> None:
    source = _snapshot_source(tmp_path)
    destination_parent = tmp_path / "private"
    destination_parent.mkdir(mode=0o700)
    digest = executor._tree_digest(source)
    assert digest is not None
    if fault == "enospc":
        monkeypatch.setattr(
            executor.os,
            "write",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.ENOSPC, "fixture full")
            ),
        )
    elif fault == "cross_device":
        monkeypatch.setattr(
            executor,
            "_rename_directory_exclusive",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EXDEV, "fixture cross-device")
            ),
        )
    elif fault == "size":
        monkeypatch.setattr(executor, "_MAX_ROLLBACK_SNAPSHOT_BYTES", 1)
    else:
        monkeypatch.setattr(executor, "_MAX_ROLLBACK_SNAPSHOT_ENTRIES", 0)
    with pytest.raises(executor.AdoptionError, match=code):
        executor._copy_private_tree_exclusive(
            source,
            destination_parent / "snapshot",
            expected_digest=digest,
        )


def test_terminal_rollback_snapshot_rejects_stale_stage_without_deleting_it(
    tmp_path: Path,
) -> None:
    source = _snapshot_source(tmp_path)
    destination_parent = tmp_path / "private"
    destination_parent.mkdir(mode=0o700)
    destination = destination_parent / "snapshot"
    stale_stage = destination_parent / ".snapshot.snapshot"
    stale_stage.mkdir(mode=0o700)
    foreign = stale_stage / "foreign.json"
    foreign.write_bytes(b"foreign\n")
    foreign.chmod(0o600)
    stale_before = executor._tree_digest(stale_stage)
    digest = executor._tree_digest(source)
    assert stale_before is not None
    assert digest is not None

    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_capture_no_clobber",
    ):
        executor._copy_private_tree_exclusive(
            source,
            destination,
            expected_digest=digest,
        )

    assert executor._tree_digest(stale_stage) == stale_before
    assert foreign.read_bytes() == b"foreign\n"
    assert not destination.exists()


def test_terminal_rollback_snapshot_recovery_enforces_limits_and_stage_inode_cas(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _snapshot_source(tmp_path)
    destination_parent = tmp_path / "private"
    destination_parent.mkdir(mode=0o700)
    destination = destination_parent / "snapshot"
    stale_stage = destination_parent / ".snapshot.snapshot"
    shutil.copytree(source, stale_stage)
    digest = executor._tree_digest(source)
    assert digest is not None

    monkeypatch.setattr(executor, "_MAX_ROLLBACK_SNAPSHOT_ENTRIES", 0)
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_capture_limit_exceeded",
    ):
        executor._copy_private_tree_exclusive(
            source,
            destination,
            expected_digest=digest,
        )
    assert stale_stage.is_dir()
    monkeypatch.setattr(executor, "_MAX_ROLLBACK_SNAPSHOT_ENTRIES", 8192)

    replacement = destination_parent / ".snapshot.replacement"
    shutil.copytree(source, replacement)
    displaced = destination_parent / ".snapshot.displaced"
    original_rename = executor._rename_directory_exclusive

    def swap_stage_before_publish(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        if source_name == stale_stage.name:
            os.rename(
                source_name,
                displaced.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.rename(
                replacement.name,
                source_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        original_rename(parent_descriptor, source_name, destination_name)

    monkeypatch.setattr(
        executor,
        "_rename_directory_exclusive",
        swap_stage_before_publish,
    )
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_capture_drift",
    ):
        executor._copy_private_tree_exclusive(
            source,
            destination,
            expected_digest=digest,
        )


def _terminal_projection_tree(
    root: Path,
    *,
    source_root: str,
    payload_value: str = "v048",
) -> None:
    runtime = root / "runtime"
    runtime.mkdir(parents=True, mode=0o700)
    payload = root / "payload.json"
    payload.write_bytes(
        executor._json_bytes({"payload": payload_value})
    )
    payload.chmod(0o600)
    binding = runtime / "RUNTIME_BINDING.json"
    binding.write_bytes(executor._json_bytes({
        "schema": "runtime-binding.v1",
        "source_root": source_root,
    }))
    binding.chmod(0o600)
    manifest = root / "SOURCE_MANIFEST.json"
    manifest.write_bytes(executor._json_bytes({
        "mcp": {
            "locator": {
                "binding_sha256": hashlib.sha256(binding.read_bytes()).hexdigest(),
            },
        },
        "operational_source_binding": {
            "self_content_binding": {
                "algorithm": "sha256",
                "digest": hashlib.sha256(source_root.encode()).hexdigest(),
            },
        },
    }))
    manifest.chmod(0o600)


def test_terminal_install_projection_binds_marketplace_bundle_to_cache(
    tmp_path: Path,
) -> None:
    marketplace_bundle = tmp_path / "marketplace-bundle"
    cache = tmp_path / "cache"
    _terminal_projection_tree(marketplace_bundle, source_root="/staged/source")
    _terminal_projection_tree(cache, source_root="/installed/source")
    marker = cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(b"1234567890123")
    marker.chmod(0o644)

    marketplace_projection = executor._terminal_install_projection_digest(
        marketplace_bundle,
    )
    cache_projection = executor._terminal_install_projection_digest(cache)
    assert marketplace_projection == cache_projection

    payload = cache / "payload.json"
    payload.write_bytes(b'{"payload":"drift"}\n')
    payload.chmod(0o600)
    assert executor._terminal_install_projection_digest(cache) != (
        marketplace_projection
    )


def _terminal_marketplace_projection_fixture(
    marketplace: Path,
    cache: Path,
    *,
    host: str,
) -> Path:
    marketplace.mkdir(parents=True, mode=0o700)
    marketplace.chmod(0o700)
    bundle = (
        marketplace
        / "distribution"
        / authority.MARKETPLACE_ID
        / authority.PLUGIN_ID
        / authority.TERMINAL_PLUGIN_VERSION
    )
    _terminal_projection_tree(
        bundle,
        source_root=f"/staged/{host}",
        payload_value=host,
    )
    _terminal_projection_tree(
        cache,
        source_root=f"/installed/{host}",
        payload_value=host,
    )
    return bundle


def test_private_journal_reader_rejects_hardlink(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    journal = root / "journal.json"
    journal.write_bytes(b"{}\n")
    journal.chmod(0o600)
    os.link(journal, root / "alias.json")
    with pytest.raises(executor.AdoptionError, match="journal_drift"):
        executor._private_file_bytes(journal)


def test_journal_transition_cas_binds_expected_bytes_to_exchanged_inode(
    tmp_path: Path,
    fixed_source,
    monkeypatch,
) -> None:
    root = tmp_path / "journal-cas-swap"
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match="AUTHORIZED"):
        _runner(root, adapters, crash_phase="AUTHORIZED").run()
    expected = executor._read_journal(root)
    successor = {**expected, "phase": "PREPARED"}
    journal = root / "journal.json"
    displaced = root / "journal.expected"
    foreign = root / "journal.foreign"
    foreign.write_bytes(b"{}\n")
    foreign.chmod(0o600)
    original_reader = executor._private_file_bytes
    swapped = False

    def read_then_swap(path: Path, *, maximum: int = 512 * 1024) -> bytes:
        nonlocal swapped
        content = original_reader(path, maximum=maximum)
        if path == journal and not swapped:
            swapped = True
            journal.rename(displaced)
            foreign.rename(journal)
        return content

    monkeypatch.setattr(executor, "_private_file_bytes", read_then_swap)
    with pytest.raises(
        executor.AdoptionError,
        match="journal_transition_cas_mismatch",
    ):
        executor._replace_journal_cas(root, expected, successor)
    assert journal.read_bytes() == b"{}\n"
    assert displaced.read_bytes() == executor._journal_bytes(expected)


def test_private_exclusive_publish_never_deletes_swapped_temp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "private-publish-swap"
    root.mkdir(mode=0o700)
    target = root / "manifest.json"
    foreign = root / "foreign.json"
    held = root / "held.json"
    foreign.write_bytes(b"foreign\n")
    foreign.chmod(0o600)
    swapped_leaf: list[str] = []

    def swap_then_fail(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        del destination_name
        os.rename(
            source_name,
            held.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.rename(
            foreign.name,
            source_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        swapped_leaf.append(source_name)
        raise OSError(errno.EIO, "injected publish failure")

    monkeypatch.setattr(
        executor,
        "_rename_directory_exclusive",
        swap_then_fail,
    )
    with pytest.raises(executor.AdoptionError, match="publish_collision"):
        executor._write_private_file_exclusive(
            target,
            b"bound\n",
            failure_code="publish_collision",
        )
    assert not target.exists()
    assert held.read_bytes() == b"bound\n"
    assert (root / swapped_leaf[0]).read_bytes() == b"foreign\n"


def test_bridge_journal_cas_retains_swapped_displaced_inode(
    tmp_path: Path,
    fixed_source,
    monkeypatch,
) -> None:
    root = tmp_path / "bridge-journal-cas-swap"
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match="AUTHORIZED"):
        _runner(root, adapters, crash_phase="AUTHORIZED").run()
    expected = executor._read_journal(root)
    successor = {**expected, "phase": "PREPARED"}
    foreign = root / "journal.foreign"
    held = root / "journal.held"
    foreign.write_bytes(b"foreign\n")
    foreign.chmod(0o600)
    original_exchange = executor._exchange_directory_entries
    prior_leaf = ".ordinary-journal-edge-authorized-to-prepared"

    def exchange_then_swap(
        directory: int,
        first: str,
        second: str,
    ) -> None:
        original_exchange(directory, first, second)
        os.rename(
            prior_leaf,
            held.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.rename(
            foreign.name,
            prior_leaf,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )

    monkeypatch.setattr(
        executor,
        "_exchange_directory_entries",
        exchange_then_swap,
    )
    with pytest.raises(
        executor.AdoptionError,
        match="journal_transition_cas_mismatch",
    ):
        executor._replace_bridge_journal_cas(root, expected, successor)
    assert (root / prior_leaf).read_bytes() == b"foreign\n"
    assert held.read_bytes() == executor._journal_bytes(expected)


def test_bridge_journal_cas_uses_signed_role_and_distinct_recovery_edge(
    tmp_path: Path,
    fixed_source,
    monkeypatch,
) -> None:
    terminal_root = tmp_path / "terminal-v048"
    _commit_terminal_v048_journal(terminal_root)
    root = tmp_path / "ordinary-v049"
    adapters = tuple(
        BridgeMemoryAdapter(host) for host in executor.HOST_ORDER
    )
    issuer = ReplayAwareOrdinaryIssuer()
    with pytest.raises(executor.InjectedCrash, match="AUTHORIZED"):
        executor.PluginAdoptionExecutor(
            state_root=root,
            adapters=adapters,
            authority_request=issuer,
            terminal_bridge_root=terminal_root,
            terminal_observers=tuple(
                TerminalMemoryObserver(_terminal_state(host))
                for host in executor.HOST_ORDER
            ),
            canonical_recovery_observer=TerminalRecoveryObserver(
                _canonical_recovery()
            ),
            crash_hook=lambda phase: (
                (_ for _ in ()).throw(executor.InjectedCrash(phase))
                if phase == "AUTHORIZED"
                else None
            ),
            clock=lambda: 2000.0,
        ).run()
    expected = executor._read_journal(root)
    prepared = {**expected, "phase": "PREPARED"}
    rolling_back = {**expected, "phase": "ROLLING_BACK"}
    original_exchange = executor._exchange_directory_entries
    blocked_once = False

    def fail_before_exchange(*_args) -> None:
        nonlocal blocked_once
        if not blocked_once:
            blocked_once = True
            raise OSError(errno.EIO, "injected pre-exchange crash")
        original_exchange(*_args)

    monkeypatch.setattr(
        executor,
        "_exchange_directory_entries",
        fail_before_exchange,
    )
    with pytest.raises(
        executor.AdoptionError,
        match="journal_transition_publish_failed",
    ):
        executor._replace_bridge_journal_cas(root, expected, prepared)
    assert executor._read_journal(root)["phase"] == "AUTHORIZED"
    assert (
        root / ".ordinary-journal-edge-authorized-to-prepared"
    ).read_bytes() == executor._journal_bytes(prepared)

    # Hiding the mutable manifest pathname cannot downgrade a signed .48
    # bridge record into the legacy CAS, and the recovery successor uses a
    # separate edge leaf from the interrupted normal successor.
    manifest = root / executor._TERMINAL_BRIDGE_MANIFEST_LEAF
    manifest.rename(root / "terminal-manifest.hidden")
    executor._replace_journal_cas(root, expected, rolling_back)
    assert executor._read_journal(root)["phase"] == "ROLLING_BACK"
    assert (
        root / ".ordinary-journal-edge-authorized-to-rolling_back"
    ).read_bytes() == executor._journal_bytes(expected)


def test_ordinary_prepared_removal_rejects_path_swap_without_unlinking_foreign(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "prepared-remove-swap"
    root.mkdir(mode=0o700)
    before = tuple(_before(host) for host in executor.HOST_ORDER)
    after = tuple(_after(host) for host in executor.HOST_ORDER)
    plan_without_digest = {
        "marketplace_id": authority.MARKETPLACE_ID,
        "plugin_id": authority.PLUGIN_ID,
        "plugin_version": authority.PLUGIN_VERSION,
        "source_revision": "1" * 40,
        "source_bundle_digest": "2" * 64,
        "target_set": list(authority.TARGET_SET),
        "transition_set": list(authority.TRANSITION_SET),
        "before_state_digest": executor._states_digest(before),
        "after_state_digest": executor._states_digest(after),
        "rollback_manifest_digest": "4" * 64,
    }
    plan = {
        **plan_without_digest,
        "plan_digest": authority.compute_plan_digest(plan_without_digest),
    }
    request = authority.build_plugin_adoption_request(
        decision_id="decision-prepared-remove-swap",
        transaction_id="transaction-prepared-remove-swap",
        source_runtime_revision="1" * 40,
        issued_at=1000.0,
        expires_at=1120.0,
        plan=plan,
    )
    prepared = executor._ordinary_prepared_from_request(
        request,
        before=before,
        after=after,
    )
    executor._write_ordinary_prepared(root, prepared)
    path = root / executor._ORDINARY_PREPARED_LEAF
    expected = path.read_bytes()
    foreign = root / ".prepared.foreign"
    foreign.write_bytes(b"foreign\n")
    foreign.chmod(0o600)
    held = root / ".prepared.expected"
    original_rename = executor._rename_directory_exclusive

    def swap_before_rename(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        if source_name == executor._ORDINARY_PREPARED_LEAF:
            os.rename(
                source_name,
                held.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.rename(
                foreign.name,
                source_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        original_rename(parent_descriptor, source_name, destination_name)

    monkeypatch.setattr(
        executor,
        "_rename_directory_exclusive",
        swap_before_rename,
    )
    with pytest.raises(executor.AdoptionError, match="ordinary_prepared_drift"):
        executor._remove_ordinary_prepared(root, prepared)
    assert path.read_bytes() == b"foreign\n"
    assert held.read_bytes() == expected


def test_fixed_bridge_captures_host_specific_v048_marketplace_and_cache_sources(
    tmp_path: Path,
) -> None:
    transaction = tmp_path / "ordinary"
    terminal = tmp_path / "terminal"
    transaction.mkdir(mode=0o700)
    captures: list[dict[str, object]] = []
    for index, host in enumerate(executor.HOST_ORDER):
        marketplace = terminal / "stage" / host
        cache = tmp_path / "cache" / host / authority.TERMINAL_PLUGIN_VERSION
        _terminal_marketplace_projection_fixture(
            marketplace,
            cache,
            host=host,
        )
        terminal_state = executor.TerminalHostState(
            host=host,
            plugin_version=authority.TERMINAL_PLUGIN_VERSION,
            operational_adoption="qualification_pending",
            registry_state="active",
            cache_digest=str(executor._tree_digest(cache)),
            cache_identity_digest=str(index + 1) * 64,
            marketplace_digest=str(executor._tree_digest(marketplace)),
            marketplace_identity_digest=str(index + 3) * 64,
            orphan_marker_digest=None,
        )
        binding = hashlib.sha256(f"binding:{host}".encode()).hexdigest()
        before = executor.HostState(
            host=host,
            marketplace_present=True,
            marketplace_digest=terminal_state.marketplace_digest,
            marketplace_binding_digest=binding,
            plugin_present=True,
            plugin_version=authority.TERMINAL_PLUGIN_VERSION,
            active=True,
            cache_digest=terminal_state.cache_digest,
        )
        adapter = object.__new__(executor.FixedHostAdapter)
        adapter.name = host
        adapter._transaction_root = transaction
        adapter._terminal_source_transaction_root = terminal
        adapter._terminal_bridge_state = terminal_state
        adapter._terminal_bridge_binding_digest = binding
        adapter._terminal_bridge_capture = None
        adapter._previous_state = None
        adapter._cache = cache.with_name(authority.PLUGIN_VERSION)
        captures.append(adapter.capture_terminal_rollback(before))
        rollback = transaction / "rollback" / host
        assert executor._tree_digest(rollback / "marketplace") == (
            terminal_state.marketplace_digest
        )
        assert executor._tree_digest(rollback / "cache") == captures[-1][
            "cache_source_digest"
        ]
    assert captures[0]["source_digest"] != captures[1]["source_digest"]
    assert captures[0]["cache_source_digest"] != captures[1][
        "cache_source_digest"
    ]


def test_fixed_bridge_rejects_mismatched_marketplace_cache_projection(
    tmp_path: Path,
) -> None:
    transaction = tmp_path / "ordinary"
    terminal = tmp_path / "terminal"
    transaction.mkdir(mode=0o700)
    marketplace = terminal / "stage" / "codex"
    cache = tmp_path / "cache" / authority.TERMINAL_PLUGIN_VERSION
    _terminal_marketplace_projection_fixture(
        marketplace,
        cache,
        host="codex",
    )
    payload = cache / "payload.json"
    payload.write_bytes(executor._json_bytes({"payload": "stale-cache"}))
    payload.chmod(0o600)
    terminal_state = executor.TerminalHostState(
        host="codex",
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        operational_adoption="qualification_pending",
        registry_state="active",
        cache_digest=str(executor._tree_digest(cache)),
        cache_identity_digest="1" * 64,
        marketplace_digest=str(executor._tree_digest(marketplace)),
        marketplace_identity_digest="2" * 64,
        orphan_marker_digest=None,
    )
    before = executor.HostState(
        host="codex",
        marketplace_present=True,
        marketplace_digest=terminal_state.marketplace_digest,
        marketplace_binding_digest="3" * 64,
        plugin_present=True,
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        active=True,
        cache_digest=terminal_state.cache_digest,
    )
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = transaction
    adapter._terminal_source_transaction_root = terminal
    adapter._terminal_bridge_state = terminal_state
    adapter._terminal_bridge_binding_digest = "3" * 64
    adapter._terminal_bridge_capture = None
    adapter._previous_state = None
    adapter._cache = cache.with_name(authority.PLUGIN_VERSION)

    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_source_digest_mismatch",
    ):
        adapter.capture_terminal_rollback(before)
    assert not (transaction / "rollback" / "codex" / "marketplace").exists()


def test_fixed_bridge_rollback_installs_only_from_private_v048_snapshot(
    tmp_path: Path,
) -> None:
    transaction = tmp_path / "ordinary"
    terminal = tmp_path / "terminal"
    transaction.mkdir(mode=0o700)
    marketplace = terminal / "stage" / "codex"
    cache = tmp_path / "cache" / authority.TERMINAL_PLUGIN_VERSION
    _terminal_marketplace_projection_fixture(
        marketplace,
        cache,
        host="codex",
    )
    terminal_state = executor.TerminalHostState(
        host="codex",
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        operational_adoption="qualification_pending",
        registry_state="active",
        cache_digest=str(executor._tree_digest(cache)),
        cache_identity_digest="1" * 64,
        marketplace_digest=str(executor._tree_digest(marketplace)),
        marketplace_identity_digest="2" * 64,
        orphan_marker_digest=None,
    )
    binding = "3" * 64
    before = executor.HostState(
        host="codex",
        marketplace_present=True,
        marketplace_digest=terminal_state.marketplace_digest,
        marketplace_binding_digest=binding,
        plugin_present=True,
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        active=True,
        cache_digest=terminal_state.cache_digest,
    )
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = transaction
    adapter._terminal_source_transaction_root = terminal
    adapter._terminal_bridge_state = terminal_state
    adapter._terminal_bridge_binding_digest = binding
    adapter._terminal_bridge_capture = None
    adapter._previous_state = None
    adapter._cache = cache.with_name(authority.PLUGIN_VERSION)
    adapter.capture_terminal_rollback(before)
    private_marketplace = transaction / "rollback" / "codex" / "marketplace"
    shutil.rmtree(terminal)
    assert adapter._terminal_admitted_marketplace_root(
        private_marketplace.resolve(strict=True)
    ) == private_marketplace.resolve(strict=True)
    assert adapter._binding_digest(
        source=private_marketplace.resolve(strict=True),
        version=authority.TERMINAL_PLUGIN_VERSION,
        marketplace_digest=terminal_state.marketplace_digest,
    ) == binding
    current = _after("codex")
    installed_from: list[Path] = []
    adapter.observe = lambda: current
    adapter._cleanup_presence = lambda: (True, True)
    adapter._remove_plugin = lambda: None
    adapter._remove_marketplace = lambda: None
    adapter._quarantine_failed_candidate = lambda: None

    def install(source: Path) -> None:
        nonlocal current
        installed_from.append(source)
        current = before

    adapter._install_from = install
    assert adapter.rollback("plugin-adoption-private-v048", before) == before
    assert installed_from == [transaction / "rollback" / "codex" / "marketplace"]
    assert installed_from[0] != marketplace


def test_terminal_bridge_quarantines_cli_retained_active_v048_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transaction = tmp_path / "ordinary"
    transaction.mkdir(mode=0o700)
    cache = tmp_path / "cache" / authority.TERMINAL_PLUGIN_VERSION
    _terminal_projection_tree(cache, source_root="/terminal/cache")
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._transaction_root = transaction
    adapter._cache = cache.with_name(authority.PLUGIN_VERSION)
    adapter._previous_state = None
    adapter._terminal_bridge_capture = {"captured": True}
    before = executor.HostState(
        host="claude",
        marketplace_present=True,
        marketplace_digest="1" * 64,
        marketplace_binding_digest="2" * 64,
        plugin_present=True,
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        active=True,
        cache_digest=str(executor._tree_digest(cache)),
    )
    monkeypatch.setattr(
        executor,
        "_validate_fixed_host_chain",
        lambda *_args, **_kwargs: None,
    )

    adapter._quarantine_previous_active_cache(before)
    retained = (
        transaction
        / "quarantine"
        / "claude"
        / "terminal-active-v048"
    )
    assert not cache.exists()
    assert executor._tree_digest(retained) == before.cache_digest


def test_terminal_bridge_rollback_resumes_cache_reconciliation_without_reinstall(
    tmp_path: Path,
) -> None:
    transaction = tmp_path / "ordinary"
    rollback = transaction / "rollback" / "codex"
    rollback.mkdir(parents=True, mode=0o700)
    expected_entry = executor.QuarantineEntry(
        handle=executor.TARGET_CACHE_HANDLES["0.1.47"],
        version="0.1.47",
        cache_digest="3" * 64,
        full_digest="4" * 64,
        identity_digest="5" * 64,
        in_use_present=False,
    )
    before = executor.HostState(
        host="codex",
        marketplace_present=True,
        marketplace_digest="1" * 64,
        marketplace_binding_digest="2" * 64,
        plugin_present=True,
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        active=True,
        cache_digest="6" * 64,
        quarantine_entries=(expected_entry,),
    )
    current = replace(before, quarantine_entries=())
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = transaction
    adapter._terminal_bridge_capture = {"captured": True}
    adapter._terminal_bridge_state = executor.TerminalHostState(
        host="codex",
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        operational_adoption="qualification_pending",
        registry_state="active",
        cache_digest=before.cache_digest,
        cache_identity_digest="7" * 64,
        marketplace_digest=before.marketplace_digest,
        marketplace_identity_digest="8" * 64,
        orphan_marker_digest=None,
    )
    effects: list[str] = []
    adapter.observe = lambda: current
    adapter._verify_rollback_marker = lambda *_args: None
    adapter.verify_terminal_rollback = lambda *_args: None
    adapter._restore_terminal_orphan_marker = lambda: effects.append("marker")
    adapter._quarantine_failed_candidate = lambda: effects.append("failed")

    def restore_entries(_before: executor.HostState) -> None:
        nonlocal current
        effects.append("entries")
        current = before

    adapter._restore_quarantine_entries = restore_entries
    adapter._cleanup_presence = lambda: (_ for _ in ()).throw(
        AssertionError("host CLI cleanup repeated")
    )
    adapter._install_from = lambda _source: (_ for _ in ()).throw(
        AssertionError("host CLI install repeated")
    )
    assert adapter.rollback("plugin-adoption-terminal-resume", before) == before
    assert effects == ["marker", "failed", "entries"]


def test_terminal_bridge_quarantine_restore_binds_child_before_live_rename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transaction = tmp_path / "ordinary"
    quarantine = transaction / "quarantine" / "codex"
    quarantine.mkdir(parents=True, mode=0o700)
    cache_parent = tmp_path / "cache"
    cache_parent.mkdir(mode=0o700)
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = transaction
    adapter._cache = cache_parent / authority.PLUGIN_VERSION
    handle = executor.TARGET_CACHE_HANDLES["0.1.47"]
    expected_path = quarantine / handle
    _terminal_projection_tree(expected_path, source_root="/expected")
    entry = adapter._entry_for_path(
        expected_path,
        version="0.1.47",
        handle=handle,
    )
    foreign = quarantine / "foreign"
    _terminal_projection_tree(foreign, source_root="/foreign")
    held = quarantine / "held-expected"
    before = replace(_before("codex"), quarantine_entries=(entry,))
    monkeypatch.setattr(
        executor,
        "_validate_fixed_host_chain",
        lambda *_args, **_kwargs: None,
    )
    original_open = executor.os.open
    swapped = False

    def swap_before_child_bind(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == handle and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            expected_path.rename(held)
            foreign.rename(expected_path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(executor.os, "open", swap_before_child_bind)
    with pytest.raises(
        executor.AdoptionError,
        match="quarantine_restore_cas_mismatch",
    ):
        adapter._restore_quarantine_entries(before)
    assert not (cache_parent / "0.1.47").exists()
    assert adapter._entry_for_path(
        held,
        version="0.1.47",
        handle=handle,
    ) == entry


def _terminal_orphan_marker_adapter(
    tmp_path: Path,
) -> tuple[
    executor.FixedHostAdapter,
    executor.HostState,
    Path,
    bytes,
    Path,
    Path,
]:
    transaction = tmp_path / "ordinary"
    terminal = tmp_path / "terminal"
    transaction.mkdir(mode=0o700)
    marketplace = terminal / "stage" / "claude"
    cache = tmp_path / "cache" / authority.TERMINAL_PLUGIN_VERSION
    bundle = _terminal_marketplace_projection_fixture(
        marketplace,
        cache,
        host="claude",
    )
    marker_content = b"1234567890123"
    marker = cache / executor.distribution.ORPHANED_INSTALLED_MARKER
    marker.write_bytes(marker_content)
    marker.chmod(0o644)
    os.chown(marker, -1, os.getgid())
    marker_info = marker.lstat()
    marker_identity_digest = executor._canonical_digest({
        "content_sha256": hashlib.sha256(marker_content).hexdigest(),
        "device": marker_info.st_dev,
        "group": marker_info.st_gid,
        "inode": marker_info.st_ino,
        "mode": stat.S_IMODE(marker_info.st_mode),
        "owner": marker_info.st_uid,
    })
    terminal_state = executor.TerminalHostState(
        host="claude",
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        operational_adoption="orphaned",
        registry_state="installed",
        cache_digest=str(
            executor._tree_digest(
                cache,
                ignored=frozenset({
                    executor.distribution.ORPHANED_INSTALLED_MARKER,
                }),
            )
        ),
        cache_identity_digest="1" * 64,
        marketplace_digest=str(executor._tree_digest(marketplace)),
        marketplace_identity_digest="2" * 64,
        orphan_marker_digest=marker_identity_digest,
    )
    before = executor.HostState(
        host="claude",
        marketplace_present=True,
        marketplace_digest=terminal_state.marketplace_digest,
        marketplace_binding_digest="4" * 64,
        plugin_present=True,
        plugin_version=authority.TERMINAL_PLUGIN_VERSION,
        active=True,
        cache_digest=terminal_state.cache_digest,
    )
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._transaction_root = transaction
    adapter._terminal_source_transaction_root = terminal
    adapter._terminal_bridge_state = terminal_state
    adapter._terminal_bridge_binding_digest = "4" * 64
    adapter._terminal_bridge_capture = None
    adapter._previous_state = None
    adapter._cache = cache.with_name(authority.PLUGIN_VERSION)
    return adapter, before, marker, marker_content, bundle, cache


def test_fixed_bridge_rollback_restores_orphan_marker_from_private_cache(
    tmp_path: Path,
) -> None:
    adapter, before, marker, marker_content, bundle, cache = (
        _terminal_orphan_marker_adapter(tmp_path)
    )
    transaction = adapter._transaction_root
    adapter.capture_terminal_rollback(before)
    shutil.rmtree(cache)
    current = _after("claude")
    adapter.observe = lambda: current
    adapter._cleanup_presence = lambda: (True, True)
    adapter._remove_plugin = lambda: None
    adapter._remove_marketplace = lambda: None
    adapter._quarantine_failed_candidate = lambda: None

    def install(source: Path) -> None:
        nonlocal current
        assert source == transaction / "rollback" / "claude" / "marketplace"
        shutil.copytree(bundle, cache)
        current = before

    adapter._install_from = install
    assert adapter.rollback("plugin-adoption-private-marker", before) == before
    assert marker.read_bytes() == marker_content
    assert stat.S_IMODE(marker.lstat().st_mode) == 0o644

    # Model a crash after the registry/cache install effect became observable
    # but before the separately bound terminal-only marker was restored.  The
    # idempotent resume must not return solely on HostState equality.
    marker.unlink()
    current = before
    assert adapter.rollback("plugin-adoption-private-marker", before) == before
    assert marker.read_bytes() == marker_content
    assert stat.S_IMODE(marker.lstat().st_mode) == 0o644


def test_fixed_bridge_marker_restore_rejects_unbound_valid_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, before, _marker, marker_content, _bundle, _cache = (
        _terminal_orphan_marker_adapter(tmp_path)
    )
    capture = adapter.capture_terminal_rollback(before)
    adapter.verify_terminal_rollback(before, capture)
    private_marker = (
        adapter._transaction_root
        / "rollback"
        / "claude"
        / "cache"
        / executor.distribution.ORPHANED_INSTALLED_MARKER
    )
    held = tmp_path / "held-private-marker"
    transient = tmp_path / "transient-private-marker"
    transient.write_bytes(b"9876543210987")
    transient.chmod(0o644)
    os.chown(transient, -1, os.getgid())
    original_record = executor._terminal_regular_file_record
    swapped = False

    def swap_read_restore(path: Path, *, maximum: int):
        nonlocal swapped
        if path == private_marker and not swapped:
            swapped = True
            private_marker.rename(held)
            transient.rename(private_marker)
            record = original_record(path, maximum=maximum)
            private_marker.rename(transient)
            held.rename(private_marker)
            return record
        return original_record(path, maximum=maximum)

    monkeypatch.setattr(
        executor,
        "_terminal_regular_file_record",
        swap_read_restore,
    )

    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_marker_drift",
    ):
        adapter._restore_terminal_orphan_marker()
    assert private_marker.read_bytes() == marker_content


def test_terminal_rollback_capture_rejects_orphan_marker_snapshot_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter, before, marker, marker_content, _bundle, cache = (
        _terminal_orphan_marker_adapter(tmp_path)
    )
    original_copy = executor._copy_private_tree_exclusive

    def copy_then_swap(
        source: Path,
        destination: Path,
        *,
        expected_digest: str,
        crash_hook=lambda _phase: None,
    ) -> None:
        original_copy(
            source,
            destination,
            expected_digest=expected_digest,
            crash_hook=crash_hook,
        )
        if source == cache:
            captured = (
                destination
                / executor.distribution.ORPHANED_INSTALLED_MARKER
            )
            captured.write_bytes(b"9999999999999")
            captured.chmod(0o644)

    monkeypatch.setattr(
        executor,
        "_copy_private_tree_exclusive",
        copy_then_swap,
    )
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_source_digest_mismatch",
    ):
        adapter.capture_terminal_rollback(before)
    assert marker.read_bytes() == marker_content


def test_terminal_rollback_install_rechecks_snapshot_at_each_cli_boundary(
    tmp_path: Path,
) -> None:
    adapter, before, _marker, _marker_content, _bundle, _cache = (
        _terminal_orphan_marker_adapter(tmp_path)
    )
    adapter.capture_terminal_rollback(before)
    marketplace = adapter._rollback_root() / "marketplace"
    calls: list[tuple[str, ...]] = []

    def run(args: tuple[str, ...], **_kwargs):
        calls.append(args)
        if len(calls) == 1:
            payload = (
                marketplace
                / "distribution"
                / authority.MARKETPLACE_ID
                / authority.PLUGIN_ID
                / authority.TERMINAL_PLUGIN_VERSION
                / "payload.json"
            )
            payload.write_bytes(b'{"payload":"swapped"}\n')
            payload.chmod(0o600)
        return None

    adapter._run = run
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_source_drift",
    ):
        adapter._install_from(marketplace)
    assert len(calls) == 1
    assert calls[0][0:3] == ("plugin", "marketplace", "add")


def test_terminal_rollback_rejects_in_call_marketplace_aba_effect(
    tmp_path: Path,
) -> None:
    adapter, before, _marker, _marker_content, _bundle, cache = (
        _terminal_orphan_marker_adapter(tmp_path)
    )
    adapter.capture_terminal_rollback(before)
    marketplace = adapter._rollback_root() / "marketplace"
    foreign = tmp_path / "foreign-marketplace"
    held = tmp_path / "held-marketplace"
    shutil.copytree(marketplace, foreign)
    foreign_bundle = adapter._terminal_marketplace_bundle_root(foreign)
    payload = foreign_bundle / "payload.json"
    payload.write_bytes(b'{"payload":"foreign-aba"}\n')
    payload.chmod(0o600)
    shutil.rmtree(cache)
    calls: list[tuple[str, ...]] = []

    def run(args: tuple[str, ...], **_kwargs):
        calls.append(args)
        marketplace.rename(held)
        foreign.rename(marketplace)
        try:
            if args[1] == "install":
                shutil.copytree(
                    adapter._terminal_marketplace_bundle_root(marketplace),
                    cache,
                )
        finally:
            marketplace.rename(foreign)
            held.rename(marketplace)
        return None

    adapter._run = run
    with pytest.raises(
        executor.AdoptionError,
        match="terminal_rollback_effect_mismatch",
    ):
        adapter._install_from(marketplace)
    assert len(calls) == 2
    assert executor._bounded_private_tree_digest(marketplace) == (
        adapter._terminal_bridge_capture["source_digest"]
    )
    assert executor._terminal_install_projection_digest(cache) != (
        adapter._terminal_bridge_capture["install_projection_digest"]
    )


@pytest.mark.parametrize(
    "version",
    [".", "..", "../0.1.15", "0.1/15", "0.1\\15", "/0.1.15", "v0.1.15"],
)
def test_host_registry_version_cannot_select_a_cache_path(version: str) -> None:
    with pytest.raises(executor.AdoptionError, match="host_registry_invalid"):
        executor._row_projection({"version": version, "enabled": True})


def test_fixed_state_and_cache_ancestor_symlinks_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    monkeypatch.setattr(executor, "_fixed_user_home", lambda: home)

    (home / ".hermes").symlink_to(outside, target_is_directory=True)
    with pytest.raises(executor.AdoptionError, match="protected_state_unavailable"):
        executor._lstat_admitted_directory(
            home / ".hermes" / "profiles" / "orch" / "plugin-adoption",
            create=True,
        )
    (home / ".hermes").unlink()

    codex = home / ".codex"
    codex.mkdir(mode=0o700)
    (codex / "plugins").symlink_to(outside, target_is_directory=True)
    with pytest.raises(executor.AdoptionError, match="host_root_drift"):
        executor._validate_fixed_host_chain(
            codex / "plugins" / "cache" / "marketplace" / "plugin" / "0.1.15",
            allow_missing=True,
        )


def test_source_bundle_identity_is_source_manifest_sha256() -> None:
    manifest = (
        executor.distribution.default_bundle_target() / "SOURCE_MANIFEST.json"
    )
    assert executor._source_bundle_digest() == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()


def test_candidate_digest_stage_stays_under_private_transaction_root(
    tmp_path: Path, monkeypatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    observed_targets: list[Path] = []

    def install(_source: Path, target: Path) -> None:
        observed_targets.append(target)
        target.mkdir(mode=0o700, parents=True)
        content = target / "bundle.json"
        content.write_text("{}\n", encoding="utf-8")
        content.chmod(0o600)

    monkeypatch.setattr(executor.distribution, "transactional_install", install)
    monkeypatch.setattr(
        executor.distribution,
        "verify_installed_bundle",
        lambda _target: None,
    )

    assert executor._candidate_installed_digests(scratch)
    assert len(observed_targets) == 1
    observed_targets[0].relative_to(scratch)


def test_marketplace_stage_binds_exact_manifest_bytes_and_shape(
    tmp_path: Path, monkeypatch
) -> None:
    marketplace = tmp_path / "marketplace"
    marketplace.mkdir(mode=0o700)
    bundle = (
        marketplace
        / "distribution"
        / authority.MARKETPLACE_ID
        / authority.PLUGIN_ID
        / authority.PLUGIN_VERSION
    )
    bundle.mkdir(mode=0o700, parents=True)
    content = bundle / "plugin.json"
    content.write_text("{}\n", encoding="utf-8")
    content.chmod(0o600)
    manifest = executor.FixedHostAdapter._marketplace_manifest(
        authority.PLUGIN_VERSION
    )
    for path in (
        marketplace / ".claude-plugin" / "marketplace.json",
        marketplace / ".agents" / "plugins" / "marketplace.json",
    ):
        executor._atomic_private_write(path, executor._json_bytes(manifest))
    assert stat.S_IMODE((marketplace / ".claude-plugin").lstat().st_mode) == 0o700
    assert stat.S_IMODE((marketplace / ".agents").lstat().st_mode) == 0o700
    assert stat.S_IMODE((marketplace / ".agents" / "plugins").lstat().st_mode) == 0o700
    digest = executor._tree_digest(bundle, ignored=frozenset({".in_use"}))
    assert digest is not None
    monkeypatch.setattr(
        executor.distribution,
        "verify_installed_bundle",
        lambda _target: None,
    )
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._verify_marketplace_root(
        marketplace,
        version=authority.PLUGIN_VERSION,
        expected_bundle_digest=digest,
        expected_marketplace_digest=executor._tree_digest(marketplace),
        verify_current_bundle=True,
    )

    substituted = {
        **manifest,
        "plugins": [{**manifest["plugins"][0], "source": "./redirected"}],
    }
    executor._atomic_private_write(
        marketplace / ".claude-plugin" / "marketplace.json",
        executor._json_bytes(substituted),
    )
    with pytest.raises(executor.AdoptionError, match="marketplace_stage_drift"):
        adapter._verify_marketplace_root(
            marketplace,
            version=authority.PLUGIN_VERSION,
            expected_bundle_digest=digest,
            expected_marketplace_digest=executor._tree_digest(marketplace),
            verify_current_bundle=True,
        )

    executor._atomic_private_write(
        marketplace / ".claude-plugin" / "marketplace.json",
        executor._json_bytes(manifest),
    )
    extra = marketplace / "unexpected"
    extra.write_text("swap\n", encoding="utf-8")
    extra.chmod(0o600)
    with pytest.raises(executor.AdoptionError, match="marketplace_stage_drift"):
        adapter._verify_marketplace_root(
            marketplace,
            version=authority.PLUGIN_VERSION,
            expected_bundle_digest=digest,
            expected_marketplace_digest="0" * 64,
            verify_current_bundle=True,
        )


def test_previous_terminal_prepared_bundle_uses_signed_versioned_shape(
    tmp_path: Path,
) -> None:
    marketplace = tmp_path / "marketplace"
    previous_version = authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
    bundle = (
        marketplace
        / "distribution"
        / authority.MARKETPLACE_ID
        / authority.PLUGIN_ID
        / previous_version
    )
    bundle.mkdir(mode=0o700, parents=True)
    payload = bundle / "signed-previous-bundle.json"
    payload.write_text(
        f'{{"version":"{previous_version}"}}\n', encoding="ascii"
    )
    payload.chmod(0o600)
    manifest = executor.FixedHostAdapter._marketplace_manifest(previous_version)
    for path in (
        marketplace / ".claude-plugin" / "marketplace.json",
        marketplace / ".agents" / "plugins" / "marketplace.json",
    ):
        executor._atomic_private_write(path, executor._json_bytes(manifest))

    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    expected_bundle_digest = executor._tree_digest(
        bundle, ignored=frozenset({".in_use"})
    )
    expected_marketplace_digest = executor._tree_digest(marketplace)
    adapter._verify_marketplace_root(
        marketplace,
        version=previous_version,
        expected_bundle_digest=expected_bundle_digest,
        expected_marketplace_digest=expected_marketplace_digest,
        verify_current_bundle=True,
    )

    payload.write_text('{"version":"drift"}\n', encoding="ascii")
    with pytest.raises(executor.AdoptionError, match="prepared_cache_drift"):
        adapter._verify_marketplace_root(
            marketplace,
            version=previous_version,
            expected_bundle_digest=expected_bundle_digest,
            expected_marketplace_digest=expected_marketplace_digest,
            verify_current_bundle=True,
        )


def test_previous_terminal_cache_ignores_marker_subtree(
    tmp_path: Path,
) -> None:
    previous_root = tmp_path / "previous"
    marketplace = previous_root / "stage" / "claude"
    bundle = (
        marketplace
        / "distribution"
        / authority.MARKETPLACE_ID
        / authority.PLUGIN_ID
        / authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
    )
    bundle.mkdir(mode=0o700, parents=True)
    payload = bundle / "signed-previous-bundle.json"
    payload.write_text(
        f'{{"version":"{authority.PREVIOUS_TERMINAL_PLUGIN_VERSION}"}}\n',
        encoding="ascii",
    )
    payload.chmod(0o600)
    manifest = executor.FixedHostAdapter._marketplace_manifest(
        authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
    )
    for path in (
        marketplace / ".claude-plugin" / "marketplace.json",
        marketplace / ".agents" / "plugins" / "marketplace.json",
    ):
        executor._atomic_private_write(path, executor._json_bytes(manifest))

    installed = (
        tmp_path
        / "installed"
        / "plugins"
        / "cache"
        / authority.MARKETPLACE_ID
        / authority.PLUGIN_ID
        / authority.PREVIOUS_TERMINAL_PLUGIN_VERSION
    )
    installed.mkdir(mode=0o700, parents=True)
    (installed / "signed-previous-bundle.json").write_text(
        f'{{"version":"{authority.PREVIOUS_TERMINAL_PLUGIN_VERSION}"}}\n',
        encoding="ascii",
    )
    (installed / "signed-previous-bundle.json").chmod(0o600)
    marker = installed / ".in_use" / "marker"
    marker.parent.mkdir(mode=0o700)
    marker.write_bytes(b"host-owned")
    marker.chmod(0o600)

    bundle_digest = executor._tree_digest(
        bundle, ignored=frozenset({".in_use"})
    )
    marketplace_digest = executor._tree_digest(marketplace)
    assert bundle_digest is not None
    assert marketplace_digest is not None
    state = executor.HostState(
        host="claude",
        marketplace_present=True,
        marketplace_digest=marketplace_digest,
        marketplace_binding_digest="b" * 64,
        plugin_present=True,
        plugin_version=authority.PREVIOUS_TERMINAL_PLUGIN_VERSION,
        active=True,
        cache_digest=bundle_digest,
    )
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "claude"
    adapter._previous_transaction_root = previous_root
    adapter._previous_state = state

    assert adapter._previous_stage_marketplace_digest(
        source=marketplace,
        cache_digest=executor._tree_digest(
            installed, ignored=frozenset({".in_use"})
        ),
    ) == marketplace_digest


@pytest.fixture(scope="module")
def _candidate_stage_digests(tmp_path_factory) -> tuple[str, str]:
    transaction = tmp_path_factory.mktemp("candidate-stage") / "transaction"
    transaction.mkdir(mode=0o700)
    return executor._candidate_installed_digests(transaction)


def _manifest_only_stage_adapter(
    tmp_path: Path,
    digests: tuple[str, str],
    *,
    host: str = "codex",
) -> tuple[executor.FixedHostAdapter, executor.HostState]:
    transaction = tmp_path / "transaction"
    transaction.mkdir(mode=0o700)
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = host
    adapter._transaction_root = transaction

    installed_digest, marketplace_digest = digests
    expected = executor._expected_after_state(
        host,
        installed_digest,
        marketplace_digest,
        "1" * 40,
        "2" * 64,
        _before(host),
    )
    manifest = executor.FixedHostAdapter._marketplace_manifest(
        authority.PLUGIN_VERSION
    )
    executor._atomic_private_write(
        adapter._stage_marketplace_root() / ".claude-plugin" / "marketplace.json",
        executor._json_bytes(manifest),
    )
    executor._atomic_private_write(
        adapter._stage_marketplace_root()
        / ".agents"
        / "plugins"
        / "marketplace.json",
        executor._json_bytes(manifest),
    )
    return adapter, expected


def test_codex_prepare_repairs_manifest_only_stage_with_complete_bundle(
    tmp_path: Path,
    _candidate_stage_digests: tuple[str, str],
) -> None:
    adapter, expected = _manifest_only_stage_adapter(
        tmp_path,
        _candidate_stage_digests,
    )

    adapter.prepare("manifest-only-repair", expected)

    assert adapter._stage_root().is_dir()
    assert (adapter._stage_root() / "SOURCE_MANIFEST.json").is_file()
    manifest = json.loads(
        executor._private_file_bytes(
            adapter._stage_marketplace_root()
            / ".claude-plugin"
            / "marketplace.json"
        ).decode("ascii")
    )
    advertised = Path(manifest["plugins"][0]["source"].removeprefix("./"))
    assert (
        adapter._stage_marketplace_root() / advertised
    ).resolve(strict=True) == adapter._stage_root().resolve(strict=True)

    adapter.prepare("manifest-only-repair-retry", expected)
    assert not (
        adapter._transaction_root
        / "stage"
        / ".codex-bundle-candidate"
    ).exists()


@pytest.mark.parametrize("drift", ["missing_child", "symlink", "extra_file", "digest"])
def test_codex_prepare_rejects_existing_stage_drift(
    tmp_path: Path,
    _candidate_stage_digests: tuple[str, str],
    drift: str,
) -> None:
    adapter, expected = _manifest_only_stage_adapter(
        tmp_path,
        _candidate_stage_digests,
    )
    adapter.prepare("stage-drift-seed", expected)
    target = adapter._stage_root()
    if drift == "missing_child":
        (target / "SOURCE_MANIFEST.json").unlink()
    elif drift == "symlink":
        (target / ".mcp.json").unlink()
        outside = tmp_path / "outside.json"
        outside.write_text("{}\n", encoding="ascii")
        (target / ".mcp.json").symlink_to(outside)
    elif drift == "extra_file":
        extra = target / "unexpected"
        extra.write_text("unexpected\n", encoding="ascii")
        extra.chmod(0o600)
    else:
        (target / ".mcp.json").write_text("drift\n", encoding="ascii")
        (target / ".mcp.json").chmod(0o600)

    with pytest.raises(
        executor.AdoptionError,
        match="marketplace_stage_conflict",
    ):
        adapter.prepare("stage-drift-reject", expected)


def test_codex_prepare_resumes_after_pre_rename_crash(
    tmp_path: Path,
    monkeypatch,
    _candidate_stage_digests: tuple[str, str],
) -> None:
    adapter, expected = _manifest_only_stage_adapter(
        tmp_path,
        _candidate_stage_digests,
    )
    original = executor._rename_directory_between_exclusive

    def crash_before(*_args, **_kwargs):
        raise executor.InjectedCrash()

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", crash_before)
    with pytest.raises(executor.InjectedCrash):
        adapter.prepare("stage-pre-rename-crash", expected)
    assert adapter._stage_candidate_root().is_dir()
    assert not adapter._stage_root().exists()

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", original)
    adapter.prepare("stage-pre-rename-resume", expected)
    assert adapter._stage_root().is_dir()
    assert not (
        adapter._transaction_root
        / "stage"
        / ".codex-bundle-candidate"
    ).exists()


def test_codex_prepare_resumes_after_post_rename_crash(
    tmp_path: Path,
    monkeypatch,
    _candidate_stage_digests: tuple[str, str],
) -> None:
    adapter, expected = _manifest_only_stage_adapter(
        tmp_path,
        _candidate_stage_digests,
    )
    original = executor._rename_directory_between_exclusive

    def crash_after(*args, **kwargs):
        original(*args, **kwargs)
        raise executor.InjectedCrash()

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", crash_after)
    with pytest.raises(executor.InjectedCrash):
        adapter.prepare("stage-post-rename-crash", expected)
    assert adapter._stage_root().is_dir()
    assert (
        adapter._stage_marketplace_root()
        / ".claude-plugin"
        / "marketplace.json"
    ).is_file()

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", original)
    adapter.prepare("stage-post-rename-resume", expected)
    assert adapter._stage_root().is_dir()
    assert (
        adapter._stage_marketplace_root()
        / ".claude-plugin"
        / "marketplace.json"
    ).is_file()
    assert not (
        adapter._transaction_root
        / "stage"
        / ".codex-bundle-candidate"
    ).exists()


def test_codex_prepare_rejects_present_destination_conflict(
    tmp_path: Path,
    monkeypatch,
    _candidate_stage_digests: tuple[str, str],
) -> None:
    adapter, expected = _manifest_only_stage_adapter(
        tmp_path,
        _candidate_stage_digests,
    )
    original = executor._rename_directory_between_exclusive

    def crash_before(*_args, **_kwargs):
        raise executor.InjectedCrash()

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", crash_before)
    with pytest.raises(executor.InjectedCrash):
        adapter.prepare("stage-conflict-seed", expected)
    shutil.copytree(adapter._stage_candidate_root(), adapter._stage_root())
    (adapter._stage_root() / ".mcp.json").write_text("conflict\n", encoding="ascii")
    (adapter._stage_root() / ".mcp.json").chmod(0o600)

    monkeypatch.setattr(executor, "_rename_directory_between_exclusive", original)
    with pytest.raises(
        executor.AdoptionError,
        match="marketplace_stage_conflict",
    ):
        adapter.prepare("stage-conflict-reject", expected)


def test_claude_prepare_accepts_existing_complete_stage(
    tmp_path: Path,
    _candidate_stage_digests: tuple[str, str],
) -> None:
    adapter, expected = _manifest_only_stage_adapter(
        tmp_path,
        _candidate_stage_digests,
        host="claude",
    )
    adapter.prepare("claude-stage-prepare", expected)
    first_digest = executor._tree_digest(adapter._stage_marketplace_root())
    adapter.prepare("claude-stage-retry", expected)
    assert executor._tree_digest(adapter._stage_marketplace_root()) == first_digest


def test_exact_predecessor_locator_and_path_free_binding() -> None:
    source = executor._resolve_predecessor_source()
    assert source.name == executor.PREDECESSOR_WORKTREE_LEAF
    descriptor = executor._predecessor_binding_descriptor()
    assert descriptor == {
        "branch": executor.PREDECESSOR_BRANCH,
        "bundle_tree_oid": executor.PREDECESSOR_BUNDLE_TREE_OID,
        "marketplace_id": authority.MARKETPLACE_ID,
        "marketplace_manifest_blob_oid": executor.PREDECESSOR_MARKETPLACE_MANIFEST_BLOB_OID,
        "marketplace_manifest_digest": executor.PREDECESSOR_MARKETPLACE_MANIFEST_DIGEST,
        "plugin_bundle_digest": executor.PREDECESSOR_BUNDLE_DIGEST,
        "plugin_id": authority.PLUGIN_ID,
        "plugin_version": executor.PREDECESSOR_VERSION,
        "schema": executor.SOURCE_BINDING_SCHEMA,
        "source_manifest_digest": executor.PREDECESSOR_SOURCE_MANIFEST_DIGEST,
        "source_manifest_blob_oid": executor.PREDECESSOR_SOURCE_MANIFEST_BLOB_OID,
        "source_revision": executor.PREDECESSOR_REVISION,
    }
    assert "/Users/" not in executor._json_bytes(descriptor).decode("ascii")
    identity_digest = executor._predecessor_marketplace_digest()
    assert len(identity_digest) == 64
    assert identity_digest != executor._predecessor_binding_digest()


def _predecessor_observation_adapter(
    tmp_path: Path,
    monkeypatch,
    *,
    active: bool = True,
    marketplace_cache_digest: str | None = None,
    marketplace_present: bool = True,
    plugin_cache_digest: str | None = executor.PREDECESSOR_BUNDLE_DIGEST,
) -> executor.FixedHostAdapter:
    predecessor = tmp_path / executor.PREDECESSOR_WORKTREE_LEAF
    predecessor.mkdir(exist_ok=True)
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = tmp_path / "transaction"
    adapter._host_home = tmp_path / "host"
    adapter._cache = tmp_path / "cache" / authority.PLUGIN_VERSION
    adapter._marketplace_cache = tmp_path / "marketplace-cache"
    adapter._run = lambda *_args, **_kwargs: {
        "plugins": [{
            "enabled": active,
            "name": authority.PLUGIN_ID,
            "version": executor.PREDECESSOR_VERSION,
        }]
    }
    adapter._marketplace_row = lambda: (
        {"name": authority.MARKETPLACE_ID, "source": str(predecessor)}
        if marketplace_present
        else None
    )
    monkeypatch.setattr(executor, "_validate_fixed_host_chain", lambda *_a, **_k: None)
    monkeypatch.setattr(executor, "_resolve_predecessor_source", lambda: predecessor)

    predecessor_cache = adapter._cache.with_name(executor.PREDECESSOR_VERSION)

    def tree_digest(path: Path, **_kwargs) -> str | None:
        if path == predecessor_cache:
            return plugin_cache_digest
        if path == adapter._marketplace_cache:
            return marketplace_cache_digest
        if path == adapter._cache:
            return None
        raise AssertionError(f"unexpected digest path leaf: {path.name}")

    monkeypatch.setattr(executor, "_tree_digest", tree_digest)
    return adapter


def test_exact_predecessor_direct_source_without_marketplace_cache_is_admitted(
    tmp_path: Path, monkeypatch
) -> None:
    state = _predecessor_observation_adapter(tmp_path, monkeypatch).observe()
    assert state == _before("codex")
    executor._require_reversible_before_states((state, _before("claude")))
    assert "/Users/" not in executor._json_bytes(state.projection()).decode("ascii")


@pytest.mark.parametrize(
    ("active", "marketplace_cache_digest", "marketplace_present", "plugin_cache_digest"),
    [
        (True, "4" * 64, True, executor.PREDECESSOR_BUNDLE_DIGEST),
        (False, None, True, executor.PREDECESSOR_BUNDLE_DIGEST),
        (True, None, False, executor.PREDECESSOR_BUNDLE_DIGEST),
        (True, None, True, "5" * 64),
        (True, None, True, None),
    ],
)
def test_predecessor_projection_drift_is_rejected(
    tmp_path: Path,
    monkeypatch,
    active: bool,
    marketplace_cache_digest: str | None,
    marketplace_present: bool,
    plugin_cache_digest: str | None,
) -> None:
    adapter = _predecessor_observation_adapter(
        tmp_path,
        monkeypatch,
        active=active,
        marketplace_cache_digest=marketplace_cache_digest,
        marketplace_present=marketplace_present,
        plugin_cache_digest=plugin_cache_digest,
    )
    with pytest.raises(
        executor.AdoptionError,
        match="marketplace_registry_cache_mismatch|plugin_registry_cache_mismatch",
    ):
        adapter.observe()


def _candidate_observation_adapter(tmp_path: Path, monkeypatch):
    root = tmp_path / "transaction"
    root.mkdir(mode=0o700)
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = root
    adapter._host_home = tmp_path / "host"
    adapter._host_home.mkdir(mode=0o700)
    adapter._cache = tmp_path / "cache" / authority.PLUGIN_VERSION
    adapter._cache.parent.mkdir(mode=0o700, parents=True)
    adapter._marketplace_cache = tmp_path / "physical-marketplace-cache"
    installed_digest, marketplace_digest = executor._candidate_installed_digests(root)
    expected = executor._expected_after_state(
        "codex",
        installed_digest,
        marketplace_digest,
        "1" * 40,
        "2" * 64,
        _before("codex"),
    )
    adapter.prepare("candidate-fixture", expected)
    shutil.copytree(adapter._stage_root(), adapter._cache)
    adapter._run = lambda *_args, **_kwargs: {
        "plugins": [{
            "enabled": True,
            "name": authority.PLUGIN_ID,
            "version": authority.PLUGIN_VERSION,
        }]
    }
    adapter._marketplace_row = lambda: {
        "name": authority.MARKETPLACE_ID,
        "source": str(adapter._stage_marketplace_root()),
    }
    monkeypatch.setattr(executor, "_validate_fixed_host_chain", lambda *_a, **_k: None)
    monkeypatch.setattr(executor, "_source_revision", lambda: "1" * 40)
    monkeypatch.setattr(executor, "_source_bundle_digest", lambda: "2" * 64)
    return adapter, expected


def test_candidate_projection_admits_exact_stage_without_physical_marketplace_tree(
    tmp_path: Path, monkeypatch
) -> None:
    adapter, expected = _candidate_observation_adapter(tmp_path, monkeypatch)
    assert not adapter._marketplace_cache.exists()
    assert adapter.observe() == expected


def test_candidate_projection_rejects_physical_cache_foreign_source_and_stage_drift(
    tmp_path: Path, monkeypatch
) -> None:
    adapter, _expected = _candidate_observation_adapter(tmp_path, monkeypatch)
    adapter._marketplace_cache.mkdir(mode=0o700)
    physical = adapter._marketplace_cache / "payload"
    physical.write_bytes(b"unexpected")
    physical.chmod(0o600)
    with pytest.raises(
        executor.AdoptionError, match="marketplace_registry_cache_mismatch"
    ):
        adapter.observe()

    shutil.rmtree(adapter._marketplace_cache)
    foreign = tmp_path / "foreign-marketplace"
    foreign.mkdir(mode=0o700)
    adapter._marketplace_row = lambda: {
        "name": authority.MARKETPLACE_ID,
        "source": str(foreign),
    }
    with pytest.raises(executor.AdoptionError, match="marketplace_binding_unadmitted"):
        adapter.observe()

    adapter._marketplace_row = lambda: {
        "name": authority.MARKETPLACE_ID,
        "source": str(adapter._stage_marketplace_root()),
    }
    manifest = adapter._stage_marketplace_root() / ".claude-plugin" / "marketplace.json"
    executor._atomic_private_write(manifest, b"{}")
    with pytest.raises(executor.AdoptionError, match="marketplace_stage_drift"):
        adapter.observe()


def test_reversible_before_rejects_wrong_synthesized_marketplace_digest() -> None:
    state = replace(_before("codex"), marketplace_digest="0" * 64)
    with pytest.raises(
        executor.AdoptionError, match="before_state_not_exactly_reversible"
    ):
        executor._require_reversible_before_states((state, _before("claude")))


def test_foreign_ambiguous_and_drifted_marketplace_bindings_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    predecessor = tmp_path / executor.PREDECESSOR_WORKTREE_LEAF
    foreign = tmp_path / "foreign"
    predecessor.mkdir()
    foreign.mkdir()
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    monkeypatch.setattr(executor, "_resolve_predecessor_source", lambda: predecessor)
    with pytest.raises(executor.AdoptionError, match="marketplace_binding_unadmitted"):
        adapter._binding_digest(
            source=foreign,
            version=executor.PREDECESSOR_VERSION,
            marketplace_digest="6" * 64,
        )
    with pytest.raises(executor.AdoptionError, match="host_registry_ambiguous"):
        executor._find_marketplace_row([
            {"name": authority.MARKETPLACE_ID, "source": str(predecessor)},
            {"name": authority.MARKETPLACE_ID, "source": str(foreign)},
        ])

    def drifted():
        raise executor.AdoptionError("predecessor_source_drift")

    monkeypatch.setattr(executor, "_resolve_predecessor_source", drifted)
    with pytest.raises(executor.AdoptionError, match="predecessor_source_drift"):
        adapter._binding_digest(
            source=predecessor,
            version=executor.PREDECESSOR_VERSION,
            marketplace_digest="6" * 64,
        )


def test_rollback_restores_exact_predecessor_source_not_equal_content_copy(
    tmp_path: Path, monkeypatch
) -> None:
    transaction = tmp_path / "transaction"
    predecessor = tmp_path / executor.PREDECESSOR_WORKTREE_LEAF
    equal_content_copy = tmp_path / "equal-content-copy"
    predecessor.mkdir()
    equal_content_copy.mkdir()
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = transaction
    before = _before("codex")
    rollback = transaction / "rollback" / "codex"
    rollback.mkdir(mode=0o700, parents=True)
    marker = {
        "before_state_digest": executor._canonical_digest(before.projection()),
        "binding": executor._predecessor_binding_descriptor(),
        "binding_digest": executor._predecessor_binding_digest(),
        "schema": executor.ROLLBACK_SCHEMA,
    }
    executor._atomic_private_write(
        rollback / "predecessor.json", executor._json_bytes(marker)
    )
    current = [replace(before, cache_digest="f" * 64)]
    adapter.observe = lambda: current[0]
    adapter._cleanup_presence = lambda: (True, True)
    adapter._remove_plugin = lambda: None
    adapter._remove_marketplace = lambda: None
    adapter._quarantine_failed_candidate = lambda: None
    installed_from: list[Path] = []
    def install(source: Path) -> None:
        installed_from.append(source)
        current[0] = before

    adapter._install_from = install
    monkeypatch.setattr(executor, "_resolve_predecessor_source", lambda: predecessor)

    assert adapter.rollback("plugin-adoption-fixture", before) == before
    assert installed_from == [predecessor]
    assert installed_from != [equal_content_copy]


def test_rollback_marker_does_not_copy_private_source_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    transaction = tmp_path / "transaction"
    predecessor = tmp_path / executor.PREDECESSOR_WORKTREE_LEAF
    predecessor.mkdir()
    adapter = object.__new__(executor.FixedHostAdapter)
    adapter.name = "codex"
    adapter._transaction_root = transaction
    adapter._marketplace_cache = tmp_path / "marketplace-cache"
    private = tmp_path / "source-metadata"
    private.write_text("private_url=https://secret.invalid/token\n", encoding="utf-8")
    adapter._cache = tmp_path / "cache" / authority.PLUGIN_VERSION
    predecessor_cache = adapter._cache.with_name(executor.PREDECESSOR_VERSION)
    predecessor_cache.mkdir(parents=True)
    before = _before("codex")
    monkeypatch.setattr(executor, "_resolve_predecessor_source", lambda: predecessor)
    monkeypatch.setattr(
        executor,
        "_tree_digest",
        lambda path, **_kwargs: (
            None if path == adapter._marketplace_cache else before.cache_digest
        ),
    )

    adapter._capture_rollback_marketplace(before)
    rollback = transaction / "rollback" / "codex"
    assert {path.name for path in rollback.iterdir()} == {"predecessor.json"}
    serialized = (rollback / "predecessor.json").read_text(encoding="ascii")
    assert "private_url" not in serialized
    assert str(predecessor) not in serialized

def test_exact_no_cache_predecessor_apply_and_rollback_resume(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = _predecessor_observation_adapter(tmp_path, monkeypatch)
    before = adapter.observe()
    after = _after("codex")
    state = [before]
    adapter.observe = lambda: state[0]
    adapter._verify_marketplace_root = lambda *_args, **_kwargs: None
    removed: list[str] = []
    adapter._remove_plugin = lambda: removed.append("plugin")
    adapter._remove_marketplace = lambda: removed.append("marketplace")
    adapter._install_from = lambda _source: state.__setitem__(0, after)

    assert adapter.apply("plugin-adoption-fixture", before, after) == after
    rollback = adapter._rollback_root()
    assert {path.name for path in rollback.iterdir()} == {"predecessor.json"}
    assert removed == ["plugin", "marketplace"]

    adapter._cleanup_presence = lambda: (True, True)
    adapter._remove_plugin = lambda: None
    adapter._remove_marketplace = lambda: None
    predecessor = tmp_path / executor.PREDECESSOR_WORKTREE_LEAF

    def restore(source: Path) -> None:
        assert source == predecessor
        state[0] = before

    adapter._install_from = restore
    assert adapter.rollback("plugin-adoption-fixture", before) == before


def test_capture_rejects_marketplace_cache_race_before_creating_rollback(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = _predecessor_observation_adapter(tmp_path, monkeypatch)
    before = adapter.observe()
    predecessor_cache = adapter._cache.with_name(executor.PREDECESSOR_VERSION)
    monkeypatch.setattr(
        executor,
        "_tree_digest",
        lambda path, **_kwargs: (
            executor.PREDECESSOR_BUNDLE_DIGEST
            if path == predecessor_cache
            else "4" * 64
        ),
    )
    with pytest.raises(executor.AdoptionError, match="before_state_cas_mismatch"):
        adapter._capture_rollback_marketplace(before)
    assert not adapter._rollback_root().exists()


def test_capture_rejects_source_drift_before_creating_rollback(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = _predecessor_observation_adapter(tmp_path, monkeypatch)
    before = adapter.observe()

    def drifted() -> Path:
        raise executor.AdoptionError("predecessor_source_drift")

    monkeypatch.setattr(executor, "_resolve_predecessor_source", drifted)
    with pytest.raises(executor.AdoptionError, match="predecessor_source_drift"):
        adapter._capture_rollback_marketplace(before)
    assert not adapter._rollback_root().exists()


def test_capture_rejects_synthesized_identity_drift_before_creating_rollback(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = _predecessor_observation_adapter(tmp_path, monkeypatch)
    before = adapter.observe()
    monkeypatch.setattr(executor, "_predecessor_marketplace_digest", lambda: "0" * 64)
    with pytest.raises(executor.AdoptionError, match="rollback_source_unavailable"):
        adapter._capture_rollback_marketplace(before)
    assert not adapter._rollback_root().exists()


@pytest.mark.parametrize(
    "fault",
    (
        "marker_write",
        "marker_fsync",
        "stage_fsync",
        "rename",
        "parent_fsync",
    ),
)
def test_claude_atomic_rollback_capture_fault_after_codex_apply_recovers(
    tmp_path: Path, fixed_source, monkeypatch, fault: str
) -> None:
    root = tmp_path / fault
    predecessor = tmp_path / executor.PREDECESSOR_WORKTREE_LEAF
    predecessor.mkdir()
    states = {host: _before(host) for host in executor.HOST_ORDER}
    adapters: list[executor.FixedHostAdapter] = []
    predecessor_caches: dict[Path, str] = {}
    marketplace_caches: set[Path] = set()

    for host in executor.HOST_ORDER:
        adapter = object.__new__(executor.FixedHostAdapter)
        adapter.name = host
        adapter._transaction_root = root
        adapter._cache = tmp_path / "cache" / host / authority.PLUGIN_VERSION
        adapter._marketplace_cache = tmp_path / "marketplaces" / host
        predecessor_caches[
            adapter._cache.with_name(executor.PREDECESSOR_VERSION)
        ] = states[host].cache_digest
        marketplace_caches.add(adapter._marketplace_cache)
        adapter.prepare = lambda *_args, **_kwargs: None
        adapter.observe = lambda host=host: states[host]
        adapter._verify_marketplace_root = lambda *_args, **_kwargs: None
        adapter._remove_plugin = lambda: None
        adapter._remove_marketplace = lambda: None
        adapter._cleanup_presence = lambda: (True, True)

        def install(source: Path, *, host: str = host, adapter=adapter) -> None:
            states[host] = (
                _after(host)
                if source == adapter._stage_marketplace_root()
                else _before(host)
            )

        def verify(
            _transaction_id: str,
            expected: executor.HostState,
            *,
            host: str = host,
        ) -> executor.HostState:
            if states[host] != expected:
                raise executor.AdoptionError("after_state_mismatch")
            return states[host]

        adapter._install_from = install
        adapter.verify = verify
        adapters.append(adapter)

    monkeypatch.setattr(executor, "_resolve_predecessor_source", lambda: predecessor)

    def tree_digest(path: Path, **_kwargs) -> str | None:
        if path in predecessor_caches:
            return predecessor_caches[path]
        if path in marketplace_caches:
            return None
        raise AssertionError(f"unexpected digest path leaf: {path.name}")

    monkeypatch.setattr(executor, "_tree_digest", tree_digest)
    active = False
    fault_used = False
    fsync_count = 0
    claude = adapters[1]
    original_capture = claude._capture_rollback_marketplace

    def capture_with_fault(before: executor.HostState) -> None:
        nonlocal active, fsync_count
        active = True
        fsync_count = 0
        try:
            original_capture(before)
        finally:
            active = False

    claude._capture_rollback_marketplace = capture_with_fault
    original_write = os.write
    original_fsync = os.fsync
    original_rename = executor._rename_directory_exclusive

    def injected_write(descriptor: int, content: bytes | memoryview) -> int:
        nonlocal fault_used
        if active and fault == "marker_write" and not fault_used:
            fault_used = True
            raise OSError(errno.EIO, "injected marker write failure")
        return original_write(descriptor, content)

    def injected_fsync(descriptor: int) -> None:
        nonlocal fault_used, fsync_count
        if active:
            fsync_count += 1
            target = {"marker_fsync": 1, "stage_fsync": 2, "parent_fsync": 3}
            if target.get(fault) == fsync_count and not fault_used:
                fault_used = True
                raise OSError(errno.EIO, "injected fsync failure")
        original_fsync(descriptor)

    def injected_rename(*args, **kwargs) -> None:
        nonlocal fault_used
        if active and fault == "rename" and not fault_used:
            fault_used = True
            raise OSError(errno.EIO, "injected rename failure")
        original_rename(*args, **kwargs)

    monkeypatch.setattr(os, "write", injected_write)
    monkeypatch.setattr(os, "fsync", injected_fsync)
    monkeypatch.setattr(executor, "_rename_directory_exclusive", injected_rename)

    with pytest.raises(
        executor.AdoptionError, match="plugin_adoption_rolled_back"
    ) as caught:
        _runner(root, tuple(adapters)).run()

    assert str(caught.value).startswith("plugin_adoption_rolled_back:")
    assert "rollback_incomplete" not in str(caught.value)
    assert fault_used
    assert states == {host: _before(host) for host in executor.HOST_ORDER}
    assert executor._read_journal(root)["phase"] == "ROLLED_BACK"
    rollback_parent = root / "rollback"
    assert not any(path.name.startswith(".") for path in rollback_parent.iterdir())
    codex_rollback = rollback_parent / "codex"
    claude_rollback = rollback_parent / "claude"
    assert {path.name for path in codex_rollback.iterdir()} == {"predecessor.json"}
    assert claude_rollback.exists() is (fault == "parent_fsync")
    for rollback in (codex_rollback, claude_rollback):
        if rollback.exists():
            assert stat.S_IMODE(rollback.stat().st_mode) == 0o700
            assert stat.S_IMODE(
                (rollback / "predecessor.json").stat().st_mode
            ) == 0o600
            expected_marker = executor._json_bytes(
                executor.FixedHostAdapter._rollback_marker(_before(rollback.name))
            )
            assert executor._private_file_bytes(
                rollback / "predecessor.json"
            ) == expected_marker


def test_signed_source_swap_after_authorization_fails_before_prepare(
    tmp_path: Path, fixed_source, monkeypatch
) -> None:
    root = tmp_path / "source-swap"
    adapters = (MemoryAdapter("codex"), MemoryAdapter("claude"))
    with pytest.raises(executor.InjectedCrash, match="AUTHORIZED"):
        _runner(root, adapters, crash_phase="AUTHORIZED").run()
    monkeypatch.setattr(executor, "_source_bundle_digest", lambda: "3" * 64)

    with pytest.raises(executor.AdoptionError, match="plugin_adoption_rolled_back"):
        _runner(root, adapters).run()
    assert [adapter.prepare_count for adapter in adapters] == [0, 0]
    assert executor._read_journal(root)["phase"] == "ROLLED_BACK"


@pytest.mark.parametrize(
    "state",
    [
        executor.HostState("codex", False, None, None, False, None, False, None),
        executor.HostState(
            "codex", True, "7" * 64, "5" * 64, True, "0.1.15", True, "8" * 64
        ),
        executor.HostState(
            "codex",
            True,
            "7" * 64,
            executor._predecessor_binding_digest(),
            True,
            executor.PREDECESSOR_VERSION,
            False,
            executor.PREDECESSOR_BUNDLE_DIGEST,
        ),
    ],
)
def test_non_exactly_reversible_before_state_is_rejected(
    tmp_path: Path, fixed_source, state: executor.HostState
) -> None:
    codex = MemoryAdapter("codex")
    codex.state = state
    claude = MemoryAdapter("claude")
    run = _runner(tmp_path / "unsupported", (codex, claude))
    with pytest.raises(executor.AdoptionError, match="before_state_not_exactly_reversible"):
        run.run()
