from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
import plistlib
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from scripts import orch_next_hermes_serve_service as service


_REAL_PREPARE_SESSION_TOKEN_SOURCE = service._prepare_session_token_source


@pytest.fixture
def service_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[service.ServiceConfig]:
    stable_parent = tmp_path / "stable-account-parent"
    stable_parent.mkdir(mode=0o700)
    user_home = stable_parent / "user-home"
    hermes_home = user_home / ".hermes" / "profiles" / "orch"
    worktree = tmp_path / "hermes-worktree"
    runtime = worktree / ".venv" / "bin" / "hermes"
    python = worktree / ".venv" / "bin" / "python"
    for directory in (hermes_home, worktree, python.parent):
        directory.mkdir(parents=True)
    hermes_home.chmod(0o700)
    runtime.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    python.write_text("python\n", encoding="utf-8")
    python.chmod(0o755)
    stable_parent.chmod(0o555)
    monkeypatch.setattr(service, "_passwd_account_home", lambda: user_home)
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(
        service,
        "_admitted_checkout_module",
        lambda _config, module_name, _relative_file: importlib.import_module(
            module_name
        ),
    )
    monkeypatch.setattr(service, "_checkout_import_preflight", lambda _config: True)
    monkeypatch.setattr(service, "STOP_CONFIRM_INTERVAL_SECONDS", 0.0)
    config = service.ServiceConfig(
        worktree=worktree,
        runtime=runtime,
        python=python,
        hermes_home=hermes_home,
    )
    # Existing lifecycle fixtures intentionally begin without service-owned
    # state. Token-source behavior has focused tests below; unrelated launchd
    # safety tests keep their original filesystem fault surface.
    monkeypatch.setattr(
        service, "_prepare_session_token_source", lambda *_a, **_k: True
    )
    native_run_launchctl = service._run_launchctl
    disabled_overrides: dict[str, bool] = {}

    def launchctl_with_explicit_definition(
        runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **options: object,
    ) -> subprocess.CompletedProcess[str]:
        operation = arguments[0]
        if operation in {"disable", "enable"}:
            domain = arguments[1].rsplit("/", 1)[0]
            disabled_overrides[domain] = operation == "disable"
            return subprocess.CompletedProcess(
                [service.LAUNCHCTL_PATH, *arguments], 0, stdout="", stderr=""
            )
        if operation == "print-disabled":
            domain = arguments[1]
            entry = (
                f'\t\t"{config.label}" => disabled\n'
                if disabled_overrides.get(domain, False)
                else ""
            )
            return subprocess.CompletedProcess(
                [service.LAUNCHCTL_PATH, *arguments],
                0,
                stdout=f"\n\tdisabled services = {{\n{entry}\t}}\n",
                stderr="",
            )
        completed = native_run_launchctl(runner, arguments, **options)
        if (
            arguments[0] == "print"
            and completed.returncode == 0
            and not completed.stdout
        ):
            domain = arguments[1].rsplit("/", 1)[0]
            return subprocess.CompletedProcess(
                completed.args,
                0,
                stdout=_valid_launchd_print(config, domain),
                stderr=completed.stderr,
            )
        return completed

    monkeypatch.setattr(service, "_run_launchctl", launchctl_with_explicit_definition)
    yield config
    stable_parent.chmod(0o700)


def _valid_plist_bytes(config: service.ServiceConfig) -> bytes:
    return service.render_launchd_plist(config).encode("utf-8")


def _session_authority_context(decision_id: str = "decision-once") -> dict[str, object]:
    return {
        "decision_binding": {
            "decision_id": decision_id,
            "runtime_revision": "a" * 40,
        }
    }


def _session_authority_allow(
    decision_id: str = "decision-once",
    runtime_revision: str = "a" * 40,
) -> dict[str, object]:
    provenance = {
        "upstreamReleaseTag": "v2026.8.3",
        "upstreamPackageVersion": "0.20.0",
        "upstreamCommit": "3c27eb6234bf91b8ceee9e9071591b31e9b148cb",
        "runtimeCommit": runtime_revision,
        "runtimeContentDigest": "4" * 64,
    }
    provenance_digest = hashlib.sha256(
        json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return {
        "outcome": "allow",
        "decision_id": decision_id,
        "consumed_once": True,
        "runtime_provenance_manifest": provenance,
        "runtime_provenance_manifest_digest": provenance_digest,
    }


def _session_authority_provenance(
    runtime_revision: str = "a" * 40,
) -> tuple[dict[str, str], str]:
    allowed = _session_authority_allow(runtime_revision=runtime_revision)
    return (
        allowed["runtime_provenance_manifest"],
        allowed["runtime_provenance_manifest_digest"],
    )


def _session_runtime_identity(
    runtime_revision: str = "a" * 40,
) -> tuple[str, dict[str, str], str]:
    provenance, digest = _session_authority_provenance(runtime_revision)
    return runtime_revision, provenance, digest


def _valid_launchd_print(
    config: service.ServiceConfig, domain: str = "user/501"
) -> str:
    arguments = "\n".join(f"\t\t{value}" for value in config.program_arguments)
    plist_path = service.default_plist_path(home=config._account_home)
    stage_path = (
        plist_path.parent / f".{plist_path.name}.bootstrap-consume-{'a' * 32}.plist"
    )
    return (
        f"{domain}/{config.label} = {{\n"
        "\tactive count = 1\n"
        "\tasid = 100026\n"
        f"\tdomain = {domain} [100026]\n"
        f"\tpath = {stage_path}\n"
        "\ttype = LaunchAgent\n"
        "\tstate = running\n"
        f"\tprogram = {service.ENV_PATH}\n"
        "\targuments = {\n"
        f"{arguments}\n"
        "\t}\n"
        f"\tworking directory = {config.worktree}\n"
        "\tstdout path = /dev/null\n"
        "\tstderr path = /dev/null\n"
        "\tenvironment = {\n"
        "\t\tOSLogRateLimit => 64\n"
        f"\t\tXPC_SERVICE_NAME => {config.label}\n"
        "\t}\n"
        "\tminimum runtime = 30\n"
        "\texit timeout = 25\n"
        "\tspawn type = background (5)\n"
        "\tumask = 77\n"
        "\tcpumon = default\n"
        "\tjetsam priority = 40\n"
        "\tjetsam memory limit (active) = (unlimited)\n"
        "\tjetsam memory limit (inactive) = (unlimited)\n"
        "\tjetsam thread limit = 32\n"
        "\tjetsamproperties category = daemon\n"
        "\tjetsam coalition = {\n"
        "\t\tID = 123\n"
        "\t}\n"
        "\tproperties = keepalive | runatload | inferred program | managed LWCR | has LWCR\n"
        "}\n"
    )


def _disabled_services_print(config: service.ServiceConfig, *, disabled: bool) -> str:
    entry = f'\t\t"{config.label}" => disabled\n' if disabled else ""
    return f"\n\tdisabled services = {{\n{entry}\t}}\n"


@pytest.mark.parametrize(
    ("case", "expected_detail"),
    [
        ("malformed_value", "launchctl_disabled_state_malformed"),
        ("outside_structure", "launchctl_disabled_state_malformed"),
        ("duplicate_label", "launchctl_disabled_state_ambiguous"),
        ("oversize", "launchctl_disabled_state_oversize"),
    ],
)
def test_disabled_state_parser_rejects_unbounded_or_ambiguous_output(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_detail: str,
) -> None:
    if case == "malformed_value":
        output = f'disabled services = {{\n\t"{service_config.label}" => maybe\n}}\n'
    elif case == "outside_structure":
        output = f'"{service_config.label}" => disabled\n'
    elif case == "duplicate_label":
        output = (
            "disabled services = {\n"
            f'\t"{service_config.label}" => disabled\n'
            f'\t"{service_config.label}" => enabled\n'
            "}\n"
        )
    else:
        output = "disabled services = {\n" + ("x" * 300_000) + "\n}\n"

    def invalid_print_disabled(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="secret")

    monkeypatch.setattr(service, "_run_launchctl", invalid_print_disabled)
    observed, detail = service._launchd_disabled_override(
        service_config,
        lambda *_args, **_kwargs: None,
        "gui/501",
    )

    assert observed is None
    assert detail == expected_detail


def _assert_identity_bearing_recovery_records(
    result: service.ServiceResult, *forbidden: str
) -> None:
    assert result.recovery_records
    for record in result.recovery_records:
        assert record.leaf
        assert "/" not in record.leaf
        assert "\\" not in record.leaf
        assert record.device > 0
        assert record.inode > 0
        assert len(record.sha256) == 64
        assert all(character in "0123456789abcdef" for character in record.sha256)
        assert record.mode & 0o777 == record.mode
        assert record.expected_label == service.DEFAULT_LABEL
        if record.artifact_kind is service.RecoveryArtifactKind.RESTORABLE_PLIST:
            assert record.label_validated is True
        else:
            assert (
                record.artifact_kind is service.RecoveryArtifactKind.PARTIAL_ATOMIC_TEMP
            )
            assert record.label_validated is False
    encoded = json.dumps(result.as_dict(), sort_keys=True)
    for value in forbidden:
        assert value not in encoded


def test_plist_is_deterministic_loopback_profile_aware_and_secret_free(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("HERMES_SESSION_TOKEN", "must-not-leak-either")

    first = service.render_launchd_plist(service_config)
    second = service.render_launchd_plist(service_config)
    parsed = plistlib.loads(first.encode("utf-8"))

    assert first == second
    assert parsed["ProgramArguments"] == [
        service.ENV_PATH,
        "-i",
        f"HERMES_HOME={service_config.hermes_home}",
        f"PATH={service.SERVICE_PATH}",
        str(service_config.python),
        str(service_config.runtime),
        "serve",
        "--isolated",
        "--host",
        "127.0.0.1",
        "--port",
        "3517",
    ]
    assert parsed["WorkingDirectory"] == str(service_config.worktree)
    assert "EnvironmentVariables" not in parsed
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True
    assert parsed["Umask"] == 0o077
    assert parsed["StandardOutPath"] == "/dev/null"
    assert parsed["StandardErrorPath"] == "/dev/null"
    assert str(service_config.hermes_home / "logs") not in first
    assert ".log" not in first
    assert "0.0.0.0" not in first
    assert "must-not-leak" not in first
    assert "TOKEN" not in first.upper()
    assert "PROMPT" not in first.upper()


def test_custom_port_is_an_argument_not_an_environment_secret(
    service_config: service.ServiceConfig,
) -> None:
    custom = service.ServiceConfig(
        worktree=service_config.worktree,
        runtime=service_config.runtime,
        python=service_config.python,
        hermes_home=service_config.hermes_home,
        port=4517,
    )
    parsed = plistlib.loads(service.render_launchd_plist(custom).encode("utf-8"))

    assert parsed["ProgramArguments"][-2:] == ["--port", "4517"]
    assert not any(
        argument.startswith("PORT=") for argument in parsed["ProgramArguments"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "wrong_header",
        "wrong_type",
        "wrong_domain_projection",
        "wrong_asid_projection",
        "wrong_program",
        "wrong_arguments",
        "missing_environment_clear",
        "wrong_clean_environment",
        "wrong_working_directory",
        "wrong_stdout",
        "wrong_stderr",
        "extra_environment",
        "wrong_spawn_type",
        "wrong_minimum_runtime",
        "wrong_exit_timeout",
        "wrong_umask",
        "wrong_oslog_rate_limit",
        "wrong_xpc_service_name",
        "missing_keepalive",
        "missing_has_lwcr",
        "duplicate_program",
        "forbidden_trigger",
        "extra_property",
        "semaphores_trigger",
        "unknown_launch_condition",
        "start_interval_trigger",
        "nice_policy",
        "cpumon_policy",
        "jetsam_priority_policy",
        "jetsam_active_limit_policy",
        "jetsam_inactive_limit_policy",
        "jetsam_thread_limit_policy",
        "jetsam_category_policy",
        "trailing_payload",
    ],
)
def test_launchd_registered_definition_rejects_ambiguous_or_foreign_shape(
    service_config: service.ServiceConfig,
    mutation: str,
) -> None:
    valid = _valid_launchd_print(service_config)
    assert service._launchd_definition_matches(service_config, "user/501", valid)
    replacements = {
        "wrong_header": ("user/501/", "gui/501/"),
        "wrong_type": ("type = LaunchAgent", "type = XPCService"),
        "wrong_domain_projection": (
            "domain = user/501 [100026]",
            "domain = system [0]",
        ),
        "wrong_asid_projection": ("asid = 100026", "asid = 0"),
        "wrong_program": (
            f"program = {service.ENV_PATH}",
            "program = /tmp/foreign",
        ),
        "wrong_arguments": (
            f"\t\t{service_config.runtime}\n",
            "\t\t/tmp/foreign-runtime\n",
        ),
        "missing_environment_clear": ("\t\t-i\n", "\t\t--\n"),
        "wrong_clean_environment": (
            f"\t\tHERMES_HOME={service_config.hermes_home}\n",
            "\t\tHERMES_HOME=/tmp/foreign-home\n",
        ),
        "wrong_working_directory": (
            f"working directory = {service_config.worktree}",
            "working directory = /tmp",
        ),
        "wrong_stdout": ("stdout path = /dev/null", "stdout path = /tmp/out"),
        "wrong_stderr": ("stderr path = /dev/null", "stderr path = /tmp/err"),
        "extra_environment": (
            "\t}\n\tminimum runtime",
            "\t\tFOREIGN => enabled\n\t}\n\tminimum runtime",
        ),
        "wrong_spawn_type": (
            "spawn type = background (5)",
            "spawn type = interactive (4)",
        ),
        "wrong_minimum_runtime": ("minimum runtime = 30", "minimum runtime = 1"),
        "wrong_exit_timeout": ("exit timeout = 25", "exit timeout = 1"),
        "wrong_umask": ("umask = 77", "umask = 22"),
        "wrong_oslog_rate_limit": ("OSLogRateLimit => 64", "OSLogRateLimit => 0"),
        "wrong_xpc_service_name": (
            f"XPC_SERVICE_NAME => {service_config.label}",
            "XPC_SERVICE_NAME => com.foreign.service",
        ),
        "missing_keepalive": (
            "properties = keepalive | runatload | inferred program | managed LWCR | has LWCR",
            "properties = runatload | inferred program | managed LWCR | has LWCR",
        ),
        "missing_has_lwcr": (
            "properties = keepalive | runatload | inferred program | managed LWCR | has LWCR",
            "properties = keepalive | runatload | inferred program | managed LWCR",
        ),
        "duplicate_program": (
            "\targuments = {",
            f"\tprogram = {service_config.python}\n\targuments = {{",
        ),
        "forbidden_trigger": (
            "\tminimum runtime = 30",
            "\twatch paths = {\n\t\t/tmp\n\t}\n\tminimum runtime = 30",
        ),
        "extra_property": (
            "properties = keepalive | runatload | inferred program | managed LWCR | has LWCR",
            "properties = keepalive | runatload | inferred program | managed LWCR | has LWCR | debug",
        ),
        "semaphores_trigger": (
            "\tminimum runtime = 30",
            "\tsemaphores = {\n\t\t/tmp/trigger => true\n\t}\n\tminimum runtime = 30",
        ),
        "unknown_launch_condition": (
            "\tminimum runtime = 30",
            "\tlaunch condition = foreign\n\tminimum runtime = 30",
        ),
        "start_interval_trigger": (
            "\tminimum runtime = 30",
            "\trun interval = 1\n\tminimum runtime = 30",
        ),
        "nice_policy": (
            "\tminimum runtime = 30",
            "\tnice = 20\n\tminimum runtime = 30",
        ),
        "cpumon_policy": (
            "\tcpumon = default",
            "\tcpumon = 80",
        ),
        "jetsam_priority_policy": (
            "\tjetsam priority = 40",
            "\tjetsam priority = 20",
        ),
        "jetsam_active_limit_policy": (
            "\tjetsam memory limit (active) = (unlimited)",
            "\tjetsam memory limit (active) = 64",
        ),
        "jetsam_inactive_limit_policy": (
            "\tjetsam memory limit (inactive) = (unlimited)",
            "\tjetsam memory limit (inactive) = 32",
        ),
        "jetsam_thread_limit_policy": (
            "\tjetsam thread limit = 32",
            "\tjetsam thread limit = 8",
        ),
        "jetsam_category_policy": (
            "\tjetsamproperties category = daemon",
            "\tjetsamproperties category = application",
        ),
    }
    if mutation == "empty":
        changed = ""
    elif mutation == "trailing_payload":
        changed = valid + "foreign\n"
    else:
        old, new = replacements[mutation]
        changed = valid.replace(old, new, 1)
        assert changed != valid
    assert not service._launchd_definition_matches(service_config, "user/501", changed)
    mismatch_code = service._launchd_definition_mismatch_code(
        service_config, "user/501", changed
    )
    assert isinstance(mismatch_code, str)
    assert re.fullmatch(r"[a-z_]+", mismatch_code)
    assert "raw" not in mismatch_code
    if mutation == "missing_keepalive":
        assert mismatch_code == "properties_missing_keepalive"
    elif mutation == "missing_has_lwcr":
        assert mismatch_code == "properties_missing_has_lwcr"
    elif mutation == "extra_property":
        assert mismatch_code == "properties_extra"


def test_launchd_registered_definition_accepts_exact_private_cli_stage_only(
    service_config: service.ServiceConfig,
) -> None:
    managed = _valid_launchd_print(service_config)
    cli_owned = managed.replace(" | managed LWCR | has LWCR", "", 1)

    assert service._launchd_definition_matches(service_config, "user/501", cli_owned)

    missing_spawn_constraint = managed.replace(" | has LWCR", "", 1)
    assert (
        service._launchd_definition_mismatch_code(
            service_config, "user/501", missing_spawn_constraint
        )
        == "properties_missing_has_lwcr"
    )

    unknown_extra = cli_owned.replace(
        "inferred program", "inferred program | foreign", 1
    )
    assert (
        service._launchd_definition_mismatch_code(
            service_config, "user/501", unknown_extra
        )
        == "properties_extra"
    )

    unexpected_cli_constraint = cli_owned.replace(
        "inferred program", "inferred program | has LWCR", 1
    )
    assert (
        service._launchd_definition_mismatch_code(
            service_config, "user/501", unexpected_cli_constraint
        )
        == "properties_extra"
    )

    canonical_path = service.default_plist_path(home=service_config._account_home)
    wrong_cli_path = re.sub(
        r"^\tpath = .*\.plist$",
        f"\tpath = {canonical_path}",
        cli_owned,
        count=1,
        flags=re.MULTILINE,
    )
    assert wrong_cli_path != cli_owned
    assert (
        service._launchd_definition_mismatch_code(
            service_config, "user/501", wrong_cli_path
        )
        == "cli_stage_path"
    )


def test_launchd_definition_accepts_strict_last_terminating_signal(
    service_config: service.ServiceConfig,
) -> None:
    recovered = _valid_launchd_print(service_config).replace(
        "\tproperties = ",
        "\tlast terminating signal = 15\n\tproperties = ",
        1,
    )

    assert (
        service._launchd_definition_mismatch_code(
            service_config,
            "user/501",
            recovered,
        )
        is None
    )


@pytest.mark.parametrize("value", ["0", "32", "+15", "SIGTERM", "15 ", "1.5"])
def test_launchd_definition_rejects_malformed_last_terminating_signal(
    service_config: service.ServiceConfig,
    value: str,
) -> None:
    changed = _valid_launchd_print(service_config).replace(
        "\tproperties = ",
        f"\tlast terminating signal = {value}\n\tproperties = ",
        1,
    )

    assert (
        service._launchd_definition_mismatch_code(
            service_config,
            "user/501",
            changed,
        )
        == "last_terminating_signal"
    )


def test_status_accepts_sigterm_recovery_projection(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    recovered = _valid_launchd_print(service_config).replace(
        "\tstate = running\n",
        "\tstate = running\n\tpid = 4321\n\tlast terminating signal = 15\n",
        1,
    )
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)

    def recovered_runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert command[1] == "print"
        return subprocess.CompletedProcess(command, 0, stdout=recovered, stderr="")

    result = service.service_status(
        service_config,
        plist_path,
        runner=recovered_runner,
        domain="user/501",
    )

    assert result.state is service.ServiceState.RUNNING
    assert result.pid == 4321
    assert result.detail is None


def test_current_launchctl_binary_matches_pinned_print_contract() -> None:
    assert service.QUALIFIED_LAUNCHCTL_OS == "macOS 26.5.2 build 25F84"
    assert service._launchctl_binary_qualified()


def test_launchctl_path_shadow_cannot_replace_qualified_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = tmp_path / "launchctl"
    shadow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shadow.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    observed: list[list[str]] = []

    def capture(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    service._run_launchctl(capture, ["print", "user/501/example"])

    assert observed == [[service.LAUNCHCTL_PATH, "print", "user/501/example"]]
    assert Path(observed[0][0]).samefile("/bin/launchctl")
    assert not Path(observed[0][0]).samefile(shadow)


def test_runtime_must_be_pinned_inside_the_absolute_worktree(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    outside_runtime = tmp_path / "outside-hermes"
    outside_runtime.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    with pytest.raises(service.ConfigurationError, match="contained by worktree"):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=outside_runtime,
            python=service_config.python,
            hermes_home=service_config.hermes_home,
        )


def test_runtime_must_be_the_canonical_hermes_console_entrypoint(
    service_config: service.ServiceConfig,
) -> None:
    interactive_cli = service_config.worktree / "cli.py"
    interactive_cli.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    with pytest.raises(
        service.ConfigurationError,
        match="worktree Hermes console entrypoint",
    ):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=interactive_cli,
            python=service_config.python,
            hermes_home=service_config.hermes_home,
        )


def test_python_must_be_the_worktree_virtualenv_interpreter(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    foreign_python = tmp_path / "foreign-runtime" / "bin" / "python"
    foreign_python.parent.mkdir(parents=True)
    foreign_python.write_text("python\n", encoding="utf-8")
    foreign_python.chmod(0o755)

    with pytest.raises(
        service.ConfigurationError,
        match="worktree virtualenv interpreter",
    ):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=service_config.runtime,
            python=foreign_python,
            hermes_home=service_config.hermes_home,
        )


def test_python_must_be_an_executable_regular_file(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    non_executable_python = tmp_path / "python-not-executable"
    non_executable_python.write_text("python\n", encoding="utf-8")
    non_executable_python.chmod(0o600)

    with pytest.raises(service.ConfigurationError, match="python must be executable"):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=service_config.runtime,
            python=non_executable_python,
            hermes_home=service_config.hermes_home,
        )


def test_hermes_home_must_preexist_be_private_owned_and_not_symlinked(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-home"
    with pytest.raises(service.ConfigurationError, match="must already exist"):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=service_config.runtime,
            python=service_config.python,
            hermes_home=missing,
        )

    public_home = tmp_path / "public-home"
    public_home.mkdir(mode=0o755)
    public_home.chmod(0o755)
    with pytest.raises(service.ConfigurationError, match="owner-private"):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=service_config.runtime,
            python=service_config.python,
            hermes_home=public_home,
        )

    target_home = tmp_path / "target-home"
    target_home.mkdir(mode=0o700)
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(target_home, target_is_directory=True)
    with pytest.raises(service.ConfigurationError, match="real directory"):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=service_config.runtime,
            python=service_config.python,
            hermes_home=linked_home,
        )

    real_lstat = os.lstat

    def foreign_home_lstat(path: os.PathLike[str] | str) -> os.stat_result:
        info = real_lstat(path)
        if Path(path) == service_config.hermes_home:
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(service.os, "lstat", foreign_home_lstat)
    with pytest.raises(service.ConfigurationError, match="owned by current account"):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=service_config.runtime,
            python=service_config.python,
            hermes_home=service_config.hermes_home,
        )


def test_passwd_home_admission_rejects_writable_parent_symlink_and_write_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writable_parent = tmp_path / "writable-parent"
    writable_parent.mkdir(mode=0o700)
    writable_home = writable_parent / "home"
    writable_home.mkdir(mode=0o700)
    with pytest.raises(service.ConfigurationError, match="account-replaceable"):
        service._admit_stable_account_home(writable_home, expected_uid=os.getuid())

    stable_parent = tmp_path / "stable-parent"
    stable_parent.mkdir(mode=0o700)
    real_home = stable_parent / "real-home"
    real_home.mkdir(mode=0o700)
    linked_home = stable_parent / "linked-home"
    linked_home.symlink_to(real_home, target_is_directory=True)
    stable_parent.chmod(0o555)
    try:
        with pytest.raises(service.ConfigurationError, match="real directory"):
            service._admit_stable_account_home(linked_home, expected_uid=os.getuid())

        real_access = os.access
        monkeypatch.setattr(
            service.os,
            "access",
            lambda path, mode: (
                True
                if Path(path) == stable_parent and mode == os.W_OK
                else real_access(path, mode)
            ),
        )
        with pytest.raises(service.ConfigurationError, match="account-replaceable"):
            service._admit_stable_account_home(real_home, expected_uid=os.getuid())
    finally:
        stable_parent.chmod(0o700)


def test_passwd_home_identity_change_blocks_before_lifecycle_mutation(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_lstat = os.lstat

    def changed_account_home(path: os.PathLike[str] | str) -> os.stat_result:
        info = real_lstat(path)
        if Path(path) == service_config._account_home:
            values = list(info)
            values[1] = info.st_ino + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(service.os, "lstat", changed_account_home)
    result = service.install_service(
        service_config,
        tmp_path / "service.plist",
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("changed account-home anchor must precede launchctl")
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_lock_error"


def test_default_passwd_home_admission_is_read_only_and_stable() -> None:
    account_home = service._passwd_account_home()
    admitted = service._admit_stable_account_home(
        account_home, expected_uid=os.getuid()
    )

    assert account_home.is_absolute()
    assert admitted.st_uid == os.getuid()
    assert admitted.st_ino == os.lstat(account_home).st_ino


def test_render_cli_exercises_the_service_front_door(
    service_config: service.ServiceConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    return_code = service.main([
        "render",
        "--worktree",
        str(service_config.worktree),
        "--runtime",
        str(service_config.runtime),
        "--python",
        str(service_config.python),
        "--hermes-home",
        str(service_config.hermes_home),
    ])

    assert return_code == 0
    parsed = plistlib.loads(capsys.readouterr().out.encode("utf-8"))
    assert parsed["Label"] == service.DEFAULT_LABEL
    assert parsed["ProgramArguments"][-2:] == ["--port", "3517"]


def test_orch_sidecar_has_fixed_identity_and_does_not_mutate_primary_definition(
    service_config: service.ServiceConfig,
) -> None:
    primary_arguments = list(service_config.program_arguments)
    primary_plist = _valid_plist_bytes(service_config)

    sidecar = service.ServiceConfig(
        worktree=service_config.worktree,
        runtime=service_config.runtime,
        python=service_config.python,
        hermes_home=service_config.hermes_home,
        role=service.ServiceRole.ORCH_SIDECAR,
        host=service.DEFAULT_HOST,
        port=service.ORCH_SIDECAR_PORT,
        label=service.ORCH_SIDECAR_LABEL,
    )

    assert sidecar.label == service.ORCH_SIDECAR_LABEL
    assert sidecar.port == service.ORCH_SIDECAR_PORT == 3518
    assert sidecar.host == service.DEFAULT_HOST == "127.0.0.1"
    assert sidecar.service_root != service_config.service_root
    assert sidecar.state_dir != service_config.state_dir
    sidecar_plist = service.default_plist_path(
        label=sidecar.label, home=sidecar._account_home
    )
    assert sidecar_plist.name == f"{service.ORCH_SIDECAR_LABEL}.plist"
    assert sidecar_plist != service.default_plist_path(
        home=service_config._account_home
    )
    assert "--orch-sidecar" in sidecar.program_arguments
    assert sidecar.program_arguments[-2:] == ["--port", "3518"]

    assert service_config.program_arguments == primary_arguments
    assert _valid_plist_bytes(service_config) == primary_plist


def test_render_cli_admits_only_the_fixed_orch_sidecar_tuple(
    service_config: service.ServiceConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    return_code = service.main([
        "render",
        "--worktree",
        str(service_config.worktree),
        "--runtime",
        str(service_config.runtime),
        "--python",
        str(service_config.python),
        "--hermes-home",
        str(service_config.hermes_home),
        "--role",
        service.ServiceRole.ORCH_SIDECAR.value,
        "--port",
        str(service.ORCH_SIDECAR_PORT),
    ])

    parsed = plistlib.loads(capsys.readouterr().out.encode("utf-8"))
    assert return_code == 0
    assert parsed["Label"] == service.ORCH_SIDECAR_LABEL
    assert parsed["ProgramArguments"][-2:] == ["--port", "3518"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "0.0.0.0"),
        ("port", service.DEFAULT_PORT),
        ("label", service.DEFAULT_LABEL),
    ],
)
def test_orch_sidecar_rejects_non_fixed_identity(
    service_config: service.ServiceConfig,
    field: str,
    value: object,
) -> None:
    kwargs = {
        "worktree": service_config.worktree,
        "runtime": service_config.runtime,
        "python": service_config.python,
        "hermes_home": service_config.hermes_home,
        "role": service.ServiceRole.ORCH_SIDECAR,
        "host": service.DEFAULT_HOST,
        "port": service.ORCH_SIDECAR_PORT,
        "label": service.ORCH_SIDECAR_LABEL,
    }
    kwargs[field] = value

    with pytest.raises(service.ConfigurationError, match="sidecar|fixed|loopback"):
        service.ServiceConfig(**kwargs)


def test_orch_sidecar_bootstrap_rollback_touches_only_sidecar(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    sidecar = service.ServiceConfig(
        worktree=service_config.worktree,
        runtime=service_config.runtime,
        python=service_config.python,
        hermes_home=service_config.hermes_home,
        role=service.ServiceRole.ORCH_SIDECAR,
        port=service.ORCH_SIDECAR_PORT,
        label=service.ORCH_SIDECAR_LABEL,
    )
    primary_plist = tmp_path / "primary.plist"
    primary_bytes = _valid_plist_bytes(service_config)
    primary_plist.write_bytes(primary_bytes)
    sidecar_plist = tmp_path / "sidecar.plist"
    calls: list[list[str]] = []

    def failed_bootstrap_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return_code = 0 if command[1] == "bootout" else 77
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    result = service.install_service(
        sidecar,
        sidecar_plist,
        runner=failed_bootstrap_runner,
        domain="user/501",
        command_config_prepared=True,
    )

    assert result.state is service.ServiceState.ERROR
    assert primary_plist.read_bytes() == primary_bytes
    assert not sidecar_plist.exists()
    command_text = json.dumps(calls)
    assert any(command[1] == "bootout" for command in calls)
    assert service.ORCH_SIDECAR_LABEL in command_text
    command_tokens = {token for command in calls for token in command}
    assert service.DEFAULT_LABEL not in command_tokens


def test_orch_sidecar_rejects_the_primary_plist_target_before_launchctl(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = service.ServiceConfig(
        worktree=service_config.worktree,
        runtime=service_config.runtime,
        python=service_config.python,
        hermes_home=service_config.hermes_home,
        role=service.ServiceRole.ORCH_SIDECAR,
        port=service.ORCH_SIDECAR_PORT,
        label=service.ORCH_SIDECAR_LABEL,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        service,
        "_run_launchctl",
        lambda _runner, command, **_kwargs: calls.append(command),
    )

    result = service.install_service(
        sidecar,
        service.default_plist_path(home=sidecar._account_home),
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("primary target must be rejected before launchctl")
        ),
        domain="user/501",
    )

    assert result.detail == "plist_target_rejected"
    assert calls == []


def test_orch_sidecar_install_never_prepares_or_rotates_primary_token_source(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = service.ServiceConfig(
        worktree=service_config.worktree,
        runtime=service_config.runtime,
        python=service_config.python,
        hermes_home=service_config.hermes_home,
        role=service.ServiceRole.ORCH_SIDECAR,
        port=service.ORCH_SIDECAR_PORT,
        label=service.ORCH_SIDECAR_LABEL,
    )
    seen: dict[str, object] = {}

    def observe_token_source(_config: service.ServiceConfig, **kwargs: object) -> bool:
        seen.update(kwargs)
        return True

    monkeypatch.setattr(service, "_prepare_session_token_source", observe_token_source)

    def denied_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 77, stdout="", stderr="")

    result = service.install_service(
        sidecar,
        tmp_path / "sidecar.plist",
        runner=denied_runner,
        domain="user/501",
    )

    assert result.detail == "launchctl_bootout_error"
    assert seen == {
        "authority_context": None,
        "rotate": False,
        "prepare_config": False,
    }


def test_orch_sidecar_rejects_shared_config_refresh_action(
    service_config: service.ServiceConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        service.main([
            "refresh-session-token-command-config",
            "--worktree",
            str(service_config.worktree),
            "--runtime",
            str(service_config.runtime),
            "--python",
            str(service_config.python),
            "--hermes-home",
            str(service_config.hermes_home),
            "--role",
            service.ServiceRole.ORCH_SIDECAR.value,
            "--port",
            str(service.ORCH_SIDECAR_PORT),
            "--current-worktree",
            str(service_config.worktree),
            "--current-runtime",
            str(service_config.runtime),
            "--current-python",
            str(service_config.python),
            "--current-port",
            str(service.ORCH_SIDECAR_PORT),
        ])

    assert exc_info.value.code == 2
    assert "orch sidecar does not support action" in capsys.readouterr().err


def test_operational_cli_rejects_arbitrary_plist_target(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        service.main([
            "install",
            "--worktree",
            str(service_config.worktree),
            "--runtime",
            str(service_config.runtime),
            "--python",
            str(service_config.python),
            "--hermes-home",
            str(service_config.hermes_home),
            "--plist",
            str(tmp_path / "arbitrary-file"),
            "--dry-run",
        ])

    assert exc_info.value.code == 2


def test_service_label_is_fixed_and_cli_rejects_collision_override(
    service_config: service.ServiceConfig,
) -> None:
    with pytest.raises(service.ConfigurationError, match="service label is fixed"):
        service.ServiceConfig(
            worktree=service_config.worktree,
            runtime=service_config.runtime,
            python=service_config.python,
            hermes_home=service_config.hermes_home,
            label="com.orchnext.hermes.serve.collision",
        )

    with pytest.raises(SystemExit) as exc_info:
        service.main([
            "render",
            "--worktree",
            str(service_config.worktree),
            "--runtime",
            str(service_config.runtime),
            "--python",
            str(service_config.python),
            "--hermes-home",
            str(service_config.hermes_home),
            "--label",
            "com.orchnext.hermes.serve.collision",
        ])

    assert exc_info.value.code == 2


def test_production_cli_routes_authority_context_only_to_explicit_install(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_marker = "private-context-must-not-print"
    context = {"decision_binding": {"decision_id": raw_marker}}
    build_calls: list[service.ServiceConfig] = []
    action_calls: list[tuple[str, dict[str, object]]] = []

    def build(checked: service.ServiceConfig) -> object:
        build_calls.append(checked)
        return context

    def install_action(
        checked: service.ServiceConfig,
        _plist_path: Path,
        **kwargs: object,
    ) -> service.ServiceResult:
        action_calls.append(("install", kwargs))
        return service.ServiceResult(
            "install", service.ServiceState.INSTALLED, checked.label, True
        )

    monkeypatch.setattr(service, "_session_token_install_authority_context", build)
    monkeypatch.setattr(
        service,
        "_prepare_session_token_command_config",
        lambda _config: True,
    )
    monkeypatch.setitem(service._ACTIONS, "install", install_action)
    monkeypatch.setattr(
        service, "default_plist_path", lambda: tmp_path / "service.plist"
    )

    return_code = service.main([
        "install",
        "--worktree",
        str(service_config.worktree),
        "--runtime",
        str(service_config.runtime),
        "--python",
        str(service_config.python),
        "--hermes-home",
        str(service_config.hermes_home),
    ])

    output = capsys.readouterr().out
    assert return_code == 0
    assert build_calls == [service_config]
    assert action_calls == [
        (
            "install",
            {
                "dry_run": False,
                "command_config_prepared": True,
                "session_token_authority_context": context,
            },
        )
    ]
    assert raw_marker not in output


def test_production_cli_config_preparation_failure_never_builds_authority_context(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source

    calls: list[dict[str, object]] = []
    config_path = service_config.hermes_home / "config.yaml"
    concurrent = b"unrelated: concurrent-cli-writer\n"
    original_atomic_rename = source._atomic_config_rename
    injected = False

    def inject_immediately_before_atomic_rename(
        home_fd: int,
        source_name: str,
        destination: str,
        operation: int,
    ) -> None:
        nonlocal injected
        if not injected and destination == "config.yaml":
            injected = True
            config_path.write_bytes(concurrent)
            config_path.chmod(0o600)
        original_atomic_rename(home_fd, source_name, destination, operation)

    monkeypatch.setattr(
        source,
        "_atomic_config_rename",
        inject_immediately_before_atomic_rename,
    )
    monkeypatch.setattr(
        service,
        "_session_token_install_authority_context",
        lambda _config: pytest.fail(
            "config preparation failure must not build or transport authority"
        ),
    )

    def install_action(
        checked: service.ServiceConfig,
        _plist_path: Path,
        **kwargs: object,
    ) -> service.ServiceResult:
        calls.append(kwargs)
        return service.ServiceResult(
            "install",
            service.ServiceState.UNAVAILABLE,
            checked.label,
            False,
            detail="session_token_source_unavailable",
        )

    monkeypatch.setitem(service._ACTIONS, "install", install_action)
    monkeypatch.setattr(
        service,
        "default_plist_path",
        lambda: tmp_path / "service.plist",
    )

    assert (
        service.main([
            "install",
            "--worktree",
            str(service_config.worktree),
            "--runtime",
            str(service_config.runtime),
            "--python",
            str(service_config.python),
            "--hermes-home",
            str(service_config.hermes_home),
        ])
        == 1
    )
    assert calls == [{"dry_run": False, "command_config_prepared": False}]
    assert injected
    assert config_path.read_bytes() == concurrent
    token_state = service_config.hermes_home / "services" / "orch-next-serve" / "state"
    assert not (token_state / source.TOKEN_LEAF).exists()
    assert not (token_state / source.LOCK_LEAF).exists()


def test_only_official_launcher_can_reach_external_authority_boundary(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    runtime = root / ".venv" / "bin" / "hermes"
    script = root / "scripts" / "orch_next_hermes_serve_service.py"
    launcher = root / "scripts" / "orch_next_hermes_serve_service_launcher.sh"
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    foreign = tmp_path / "foreign"
    for package in (
        foreign / "scripts",
        foreign / "agent" / "secret_sources",
        foreign / "tui_gateway",
    ):
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    for relative in (
        "scripts/orch_next_hermes_mcp_launcher.py",
        "scripts/orch_next_hermes_session_token_source.py",
        "agent/secret_sources/base.py",
        "agent/secret_sources/_cache.py",
        "agent/secret_sources/bitwarden.py",
        "agent/secret_sources/command.py",
        "tui_gateway/maestro_authority.py",
    ):
        target = foreign / relative
        target.write_text("raise SystemExit(91)\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(foreign)
    identity_args = [
        "--worktree",
        str(root),
        "--runtime",
        str(runtime),
        "--python",
        str(python),
        "--hermes-home",
        str(profile),
    ]

    admitted = subprocess.run(
        [str(launcher), "preflight", *identity_args],
        cwd=foreign,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    direct = subprocess.run(
        [str(python), str(script), "preflight", *identity_args],
        cwd=foreign,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    module = subprocess.run(
        [
            str(python),
            "-m",
            "scripts.orch_next_hermes_serve_service",
            "preflight",
            *identity_args,
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert admitted.returncode == 1
    assert admitted.stdout == ""
    assert "Hermes runtime provenance authority unavailable" in admitted.stderr
    assert direct.returncode == 1
    assert json.loads(direct.stdout)["detail"] == "isolated_launcher_required"
    assert module.returncode == 1
    assert json.loads(module.stdout)["detail"] == "module_entrypoint_unavailable"
    assert direct.stderr == module.stderr == ""
    assert service._IMPORT_PREFLIGHT_ACTIONS == {
        "install",
        "status",
        "start",
        "restart",
        "recover-config",
        "refresh-session-token-command-config",
    }

    foreign_worktree = foreign / "checkout"
    foreign_bin = foreign_worktree / ".venv" / "bin"
    foreign_bin.mkdir(parents=True)
    foreign_runtime = foreign_bin / "hermes"
    foreign_python = foreign_bin / "python"
    foreign_runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    foreign_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    foreign_runtime.chmod(0o755)
    foreign_python.chmod(0o755)
    rejected = subprocess.run(
        [
            str(launcher),
            "preflight",
            "--worktree",
            str(foreign_worktree),
            "--runtime",
            str(foreign_runtime),
            "--python",
            str(foreign_python),
            "--hermes-home",
            str(profile),
        ],
        cwd=foreign,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert rejected.returncode == 1
    assert rejected.stdout == ""
    assert "Hermes runtime provenance authority unavailable" in rejected.stderr


@pytest.mark.parametrize("action", ["preflight", "status", "start"])
def test_public_admission_env_cannot_reach_lifecycle_action(
    tmp_path: Path,
    action: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    runtime = root / ".venv" / "bin" / "hermes"
    script = root / "scripts" / "orch_next_hermes_serve_service.py"
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    args = [
        str(python),
        "-I",
        "-S",
        str(script),
        action,
        "--worktree",
        str(root),
        "--runtime",
        str(runtime),
        "--python",
        str(python),
        "--hermes-home",
        str(profile),
    ]
    if action != "preflight":
        args.append("--dry-run")

    result = subprocess.run(
        args,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "ORCH_LIFECYCLE_CONTROLLER_ADMISSION": (
                "7d6bc36e50938f74ad2728ed3d87f272620086de7bfd928616c84bbdfd09412e"
            ),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["detail"] == "isolated_launcher_required"
    assert result.stderr == ""
    assert list(profile.iterdir()) == []


@pytest.mark.parametrize("alias_kind", ["launcher", "parent"])
def test_lifecycle_launcher_rejects_symlink_aliases_before_foreign_interpreter(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = root / "scripts" / "orch_next_hermes_serve_service_launcher.sh"
    marker = tmp_path / "foreign-interpreter-used"

    if alias_kind == "launcher":
        fake_root = tmp_path / "fake-root"
        fake_scripts = fake_root / "scripts"
        fake_bin = fake_root / ".venv" / "bin"
        fake_scripts.mkdir(parents=True)
        fake_bin.mkdir(parents=True)
        alias = fake_scripts / launcher.name
        alias.symlink_to(launcher)
        (fake_scripts / "orch_next_hermes_serve_service.py").symlink_to(
            root / "scripts" / "orch_next_hermes_serve_service.py"
        )
        foreign_python = fake_bin / "python"
        foreign_python.write_text(
            f"#!/bin/sh\n: > {str(marker)!r}\nexit 0\n",
            encoding="utf-8",
        )
        foreign_python.chmod(0o755)
    else:
        alias_root = tmp_path / "aliased-root"
        alias_root.symlink_to(root, target_is_directory=True)
        alias = alias_root / "scripts" / launcher.name

    result = subprocess.run(
        [str(alias), "preflight"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 65
    assert result.stdout == result.stderr == ""
    assert not marker.exists()


@pytest.mark.parametrize(
    "alias_kind",
    ["hardlink_foreign", "copy_foreign", "copy_exact_controller"],
)
def test_lifecycle_launcher_binds_controller_before_python_exec(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = root / "scripts" / "orch_next_hermes_serve_service_launcher.sh"
    controller = root / "scripts" / "orch_next_hermes_mcp_launcher.py"
    fake_root = tmp_path / "fake-root"
    fake_scripts = fake_root / "scripts"
    fake_bin = fake_root / ".venv" / "bin"
    fake_scripts.mkdir(parents=True)
    fake_bin.mkdir(parents=True)
    alias = fake_scripts / launcher.name
    if alias_kind == "hardlink_foreign":
        os.link(launcher, alias)
    else:
        alias.write_bytes(launcher.read_bytes())
        alias.chmod(0o755)

    marker = tmp_path / "foreign-controller-used"
    fake_controller = fake_scripts / controller.name
    if alias_kind == "copy_exact_controller":
        fake_controller.write_bytes(controller.read_bytes())
        fake_controller.chmod(0o755)
        foreign_python = fake_bin / "python"
        foreign_python.write_text(
            f"#!/bin/sh\n: > {str(marker)!r}\nexit 0\n",
            encoding="utf-8",
        )
        foreign_python.chmod(0o755)
    else:
        fake_controller.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('used')\n",
            encoding="utf-8",
        )

    result = subprocess.run(
        [str(alias), "preflight"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert not marker.exists()


def test_lifecycle_launcher_hashes_and_executes_one_controller_snapshot() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = root / "scripts" / "orch_next_hermes_serve_service_launcher.sh"
    controller = root / "scripts" / "orch_next_hermes_mcp_launcher.py"
    source = launcher.read_text(encoding="utf-8")
    controller_digest = hashlib.sha256(controller.read_bytes()).hexdigest()
    embedded = re.search(r'controller_sha256="([0-9a-f]{64})"', source)

    assert embedded is not None
    assert embedded.group(1) == controller_digest
    assert service._LIFECYCLE_CONTROLLER_SHA256 == controller_digest
    assert source.count("descriptor = os.open(path, flags)") == 1
    assert "O_NOFOLLOW" in source
    assert "before = os.fstat(descriptor)" in source
    assert "after = os.fstat(descriptor)" in source
    assert "hashlib.sha256(source).hexdigest()" in source
    assert 'code = compile(source, path, "exec", dont_inherit=True)' in source
    assert "exec(code, namespace)" in source
    assert "/usr/bin/shasum" not in source
    assert "/usr/bin/python3 -I -S -c" in source


def test_public_launcher_refresh_dry_run_reaches_existing_refresh_consumer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bind the public pin before exercising its existing refresh consumer."""

    root = Path(__file__).resolve().parents[1]
    launcher = root / "scripts" / "orch_next_hermes_serve_service_launcher.sh"
    controller = root / "scripts" / "orch_next_hermes_mcp_launcher.py"
    launcher_source = launcher.read_text(encoding="utf-8")
    embedded = re.search(r'controller_sha256="([0-9a-f]{64})"', launcher_source)

    assert embedded is not None
    assert embedded.group(1) == hashlib.sha256(controller.read_bytes()).hexdigest()
    assert 'exec /usr/bin/env -i' in launcher_source
    assert '--orch-lifecycle-service "$@"' in launcher_source

    # The public process performs a separate external authority admission
    # before this service snapshot.  Keep this focused test at the existing
    # source-level seam so it remains deterministic and does not contact or
    # mutate that boundary; the real consumer still receives the exact public
    # argv and dry-run flag below.
    monkeypatch.setattr(service, "_checkout_import_preflight", lambda _config: True)
    monkeypatch.setattr(service, "default_plist_path", lambda: tmp_path / "service.plist")
    arguments = [
        "refresh-session-token-command-config",
        "--worktree",
        str(service_config.worktree),
        "--runtime",
        str(service_config.runtime),
        "--python",
        str(service_config.python),
        "--hermes-home",
        str(service_config.hermes_home),
        "--current-worktree",
        str(service_config.worktree),
        "--current-runtime",
        str(service_config.runtime),
        "--current-python",
        str(service_config.python),
        "--current-port",
        str(service_config.port),
        "--dry-run",
    ]

    assert service.main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == {
        "action": "refresh-session-token-command-config",
        "detail": "refresh-session-token-command-config_dry_run",
        "installed": False,
        "label": service.DEFAULT_LABEL,
        "loaded": False,
        "pid": None,
        "recovery_records": [],
        "state": "planned",
    }


def test_lifecycle_service_import_closure_runs_in_fixed_system_controller() -> None:
    root = Path(__file__).resolve().parents[1]
    service_path = root / "scripts" / "orch_next_hermes_serve_service.py"
    probe = f"""
import json
import runpy
import sys
import types
from pathlib import Path

namespace = runpy.run_path({str(service_path)!r}, run_name="orch_preflight_probe")
ready = namespace["_checkout_import_preflight"](
    types.SimpleNamespace(worktree=Path({str(root)!r}))
)
from agent.secret_sources import base, command
print(json.dumps({{
    "ready": ready,
    "shared_result": command.FetchResult is base.FetchResult,
    "bitwarden_loaded": "agent.secret_sources.bitwarden" in sys.modules,
}}, sort_keys=True))
"""

    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-S", "-c", probe],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "bitwarden_loaded": False,
        "ready": True,
        "shared_result": True,
    }
    assert result.stderr == ""


def test_lifecycle_authority_home_uses_exact_profile_not_ambient_fake_socket(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    account_home = Path("/private/tmp") / f"ha-route-{os.getpid()}"
    request.addfinalizer(lambda: shutil.rmtree(account_home, ignore_errors=True))
    profile = account_home / ".hermes" / "profiles" / "orch"
    fake_profile = account_home / ".hermes" / "profiles" / "fake"
    for home in (profile, fake_profile):
        authority_dir = home / "authority"
        authority_dir.mkdir(parents=True, mode=0o700)
        home.chmod(0o700)
        authority_dir.chmod(0o700)
    monkeypatch.setenv("HERMES_HOME", str(fake_profile))
    admitted = controller._admit_lifecycle_authority_home([
        "preflight",
        "--hermes-home",
        str(profile),
    ])
    exact_socket = profile / "authority" / "maestro-authority-v3.sock"
    fake_socket = fake_profile / "authority" / "maestro-authority-v3.sock"
    prior = sys.modules.get("tui_gateway.maestro_authority")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as exact_listener:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as fake_listener:
            exact_listener.bind(str(exact_socket))
            fake_listener.bind(str(fake_socket))
            loaded = controller._load_authority_consumer(
                Path(__file__).resolve().parents[1], authority_home=admitted
            )
            try:
                assert loaded._PROTECTED_AUTHORITY_HOME == profile
                assert loaded._fixed_authority_socket_path() == exact_socket
                assert loaded._fixed_authority_socket_path() != fake_socket
            finally:
                sys.modules.pop("tui_gateway.maestro_authority", None)
                if prior is not None:
                    sys.modules["tui_gateway.maestro_authority"] = prior


def test_lifecycle_authority_home_rejects_ambiguous_noncanonical_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    account_home = tmp_path / "account"
    profile = account_home / ".hermes" / "profiles" / "orch"
    profile.mkdir(parents=True, mode=0o700)
    profile.chmod(0o700)
    linked_profile = account_home / ".hermes" / "profiles" / "linked"
    linked_profile.symlink_to(profile, target_is_directory=True)
    invalid_routes = (
        ["preflight"],
        [
            "preflight",
            "--hermes-home",
            str(profile),
            "--hermes-home",
            str(profile),
        ],
        ["preflight", "--hermes-home", "relative/profile"],
        ["preflight", f"--hermes-home={profile}"],
        ["preflight", "--hermes-home", str(linked_profile)],
    )
    for service_args in invalid_routes:
        with pytest.raises(
            SystemExit, match="Hermes lifecycle authority home unavailable"
        ):
            controller._admit_lifecycle_authority_home(service_args)


def test_invalid_lifecycle_authority_home_denies_before_service_or_runtime_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    monkeypatch.setattr(
        controller,
        "_verify_binding",
        lambda *_args, **_kwargs: pytest.fail("runtime verify must not start"),
    )
    monkeypatch.setattr(
        controller,
        "_consume_runtime_provenance_authority",
        lambda *_args, **_kwargs: pytest.fail("authority transport must not start"),
    )
    monkeypatch.setattr(
        controller,
        "_execute_lifecycle_service_snapshot",
        lambda *_args, **_kwargs: pytest.fail("service action must not start"),
    )
    monkeypatch.setattr(
        controller.sys,
        "argv",
        ["controller", controller.LIFECYCLE_SERVICE_FLAG, "status"],
    )

    with pytest.raises(SystemExit, match="Hermes lifecycle authority home unavailable"):
        controller._run_lifecycle_service()


def test_service_fresh_authority_consumes_validated_config_home(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tui_gateway

    observed: dict[str, object] = {}
    module_name = "tui_gateway.maestro_authority"
    missing = object()
    prior_module = sys.modules.get(module_name, missing)
    prior_parent_attribute = getattr(tui_gateway, "maestro_authority", missing)

    def consume(manifest, *, source_root, authority_home):
        observed.update(
            manifest=manifest,
            source_root=source_root,
            authority_home=authority_home,
        )

    controller = SimpleNamespace(
        PLUGIN_ID="orch-next-hermes-harness",
        SOURCE_MANIFEST_NAME="SOURCE_MANIFEST.json",
        _consume_runtime_provenance_authority=consume,
    )
    monkeypatch.setattr(
        service, "_load_lifecycle_controller_snapshot", lambda: controller
    )

    try:
        assert (
            service._consume_lifecycle_runtime_authority(service_config.hermes_home)
            is True
        )
        assert observed["authority_home"] == service_config.hermes_home
        assert observed["source_root"] == service._ADMITTED_CHECKOUT_ROOT
    finally:
        sys.modules.pop(module_name, None)
        if prior_module is not missing:
            sys.modules[module_name] = prior_module
        if prior_parent_attribute is missing:
            try:
                delattr(tui_gateway, "maestro_authority")
            except AttributeError:
                pass
        else:
            tui_gateway.maestro_authority = prior_parent_attribute


def test_lifecycle_controller_consumes_external_provenance_before_service_exec() -> (
    None
):
    root = Path(__file__).resolve().parents[1]
    controller = root / "scripts" / "orch_next_hermes_mcp_launcher.py"
    source = controller.read_text(encoding="utf-8")
    function = source[source.index("def _run_lifecycle_service(") :]

    authority_home = function.index("_admit_lifecycle_authority_home(")
    authority_call = function.index("_consume_runtime_provenance_authority(")
    source_lock = function.index("_acquire_lifecycle_source_lock(")
    post_authority_verify = function.index("verify()", source_lock)
    service_snapshot = function.index("_open_verified_lifecycle_service(", source_lock)
    service_exec = function.index("_execute_lifecycle_service_snapshot(", source_lock)
    assert authority_home < authority_call < source_lock < post_authority_verify
    assert post_authority_verify < service_snapshot < service_exec
    assert "ORCH_LIFECYCLE_CONTROLLER_ADMISSION" not in function
    assert "os.execve" not in function


def test_installed_manifest_routes_lifecycle_before_mcp_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    bundle = tmp_path / controller.PLUGIN_ID
    manifest = bundle / controller.SOURCE_MANIFEST_NAME
    bundle.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")
    observed: dict[str, object] = {}

    monkeypatch.setattr(controller, "verified_origin", lambda: Path(__file__))
    monkeypatch.setattr(
        controller,
        "_run_lifecycle_service",
        lambda **kwargs: observed.update(kwargs),
    )
    monkeypatch.setattr(
        controller.runpy,
        "run_module",
        lambda *_args, **_kwargs: pytest.fail("MCP module must not start"),
    )
    monkeypatch.setattr(
        controller.sys,
        "argv",
        [
            "controller",
            controller.RUNTIME_PROVENANCE_MANIFEST_FLAG,
            str(manifest),
            controller.LIFECYCLE_SERVICE_FLAG,
            "preflight",
            "--hermes-home",
            str(tmp_path),
        ],
    )

    controller.main()

    assert observed == {
        "bundle_root": bundle,
        "service_args": ["preflight", "--hermes-home", str(tmp_path)],
    }


def test_provenance_lifecycle_routes_before_mcp_origin_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    bundle = tmp_path / controller.PLUGIN_ID
    manifest = bundle / controller.SOURCE_MANIFEST_NAME
    bundle.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")
    observed: dict[str, object] = {}
    monkeypatch.setattr(controller, "_bundle_root", lambda: None)
    monkeypatch.setattr(
        controller,
        "verified_origin",
        lambda: pytest.fail("lifecycle must not require MCP module import"),
    )
    monkeypatch.setattr(
        controller,
        "_run_lifecycle_service",
        lambda **kwargs: observed.update(kwargs),
    )
    monkeypatch.setattr(
        controller.sys,
        "argv",
        [
            "source-controller",
            controller.RUNTIME_PROVENANCE_MANIFEST_FLAG,
            str(manifest),
            controller.LIFECYCLE_SERVICE_FLAG,
            "preflight",
        ],
    )

    controller.main()

    assert observed == {"bundle_root": bundle, "service_args": ["preflight"]}


def test_installed_entry_uses_portable_wrapper_before_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    bundle = tmp_path / controller.PLUGIN_ID
    observed: dict[str, object] = {}
    monkeypatch.setattr(controller, "_bundle_root", lambda: bundle)
    monkeypatch.setattr(
        controller,
        "_run_portable_wrapper",
        lambda root: observed.update({"portable": root}),
    )
    monkeypatch.setattr(
        controller,
        "_run_lifecycle_service",
        lambda **_kwargs: pytest.fail("installed entry must not run source lifecycle"),
    )
    monkeypatch.setattr(
        controller.sys,
        "argv",
        ["installed-controller", controller.LIFECYCLE_SERVICE_FLAG, "preflight"],
    )

    controller.main()

    assert observed == {"portable": bundle}


def test_installed_portable_lifecycle_uses_fixed_isolated_system_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    bundle = tmp_path / controller.PLUGIN_ID
    manifest = bundle / controller.SOURCE_MANIFEST_NAME
    launcher = tmp_path / "source" / "scripts" / "orch_next_hermes_mcp_launcher.py"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# controller\n", encoding="utf-8")
    monkeypatch.setattr(
        controller,
        "_verify_binding",
        lambda _root: (Path("venv-python"), Path("venv-target"), launcher),
    )
    monkeypatch.setattr(
        controller.sys,
        "argv",
        ["installed-controller", controller.LIFECYCLE_SERVICE_FLAG, "preflight"],
    )
    observed: dict[str, object] = {}
    def capture_exec(executable: str, argv: list[str], env: dict[str, str]) -> None:
        observed.update({"executable": executable, "argv": argv, "env": env})
        raise RuntimeError("exec-captured")

    monkeypatch.setattr(
        controller.os,
        "execve",
        capture_exec,
    )

    with pytest.raises(RuntimeError, match="exec-captured"):
        controller._run_portable_wrapper(bundle)

    assert observed["executable"] == controller.SYSTEM_PYTHON
    assert observed["argv"] == [
        controller.SYSTEM_PYTHON,
        "-I",
        "-S",
        str(launcher),
        controller.RUNTIME_PROVENANCE_MANIFEST_FLAG,
        str(manifest),
        controller.LIFECYCLE_SERVICE_FLAG,
        "preflight",
    ]


def test_installed_lifecycle_resolves_source_from_runtime_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    source_root = tmp_path / "immutable-source"
    bundle = tmp_path / "installed" / controller.PLUGIN_ID
    runtime = bundle / "runtime"
    profile = tmp_path / "profile"
    source_root.mkdir()
    source_bundle = source_root / "distribution" / controller.PLUGIN_ID
    source_bundle.mkdir(parents=True)
    (source_bundle / controller.SOURCE_MANIFEST_NAME).write_text(
        "{}\n", encoding="utf-8"
    )
    runtime.mkdir(parents=True)
    profile.mkdir(mode=0o700)
    (bundle / controller.SOURCE_MANIFEST_NAME).write_text("{}\n", encoding="utf-8")
    (runtime / controller.RUNTIME_BINDING_NAME).write_text(
        json.dumps({
            "mode": controller.RUNTIME_LOCATOR_MODE_INSTALLED,
            "source_root": str(source_root),
        }),
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    def verify(_bundle: Path, **kwargs: object) -> tuple[Path, Path, Path]:
        observed.update({"bundle": _bundle, **kwargs})
        return Path("python"), Path("python-target"), Path("launcher")

    def stop_before_mutation(
        manifest_path: str,
        *,
        source_root: Path,
        authority_home: Path,
    ) -> tuple[dict, str]:
        observed.update({
            "manifest_path": manifest_path,
            "source_root": source_root,
            "authority_home": authority_home,
        })
        raise RuntimeError("stop-before-mutation")

    monkeypatch.setattr(controller, "_verify_binding", verify)
    monkeypatch.setattr(
        controller,
        "_consume_runtime_provenance_authority",
        stop_before_mutation,
    )

    with pytest.raises(RuntimeError, match="stop-before-mutation"):
        controller._run_lifecycle_service(
            bundle_root=bundle,
            service_args=["preflight", "--hermes-home", str(profile)],
        )

    assert observed["bundle"] == bundle
    assert observed["runtime_dir"] == runtime
    assert observed["expected_source_root"] == source_root
    assert observed["manifest_path"] == str(
        source_bundle / controller.SOURCE_MANIFEST_NAME
    )
    assert observed["source_root"] == source_root
    assert observed["authority_home"] == profile


def test_installed_lifecycle_locks_the_admitted_source_distribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    source_root = tmp_path / "immutable-source"
    source_bundle = source_root / "distribution" / controller.PLUGIN_ID
    installed_bundle = tmp_path / "installed" / controller.PLUGIN_ID
    runtime = installed_bundle / "runtime"
    profile = tmp_path / "profile"
    source_bundle.mkdir(parents=True)
    (source_bundle / controller.SOURCE_MANIFEST_NAME).write_text(
        "{}\n", encoding="utf-8"
    )
    runtime.mkdir(parents=True)
    profile.mkdir(mode=0o700)
    (installed_bundle / controller.SOURCE_MANIFEST_NAME).write_text(
        "{}\n", encoding="utf-8"
    )
    (runtime / controller.RUNTIME_BINDING_NAME).write_text(
        json.dumps({
            "mode": controller.RUNTIME_LOCATOR_MODE_INSTALLED,
            "source_root": str(source_root),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        controller,
        "_verify_binding",
        lambda _bundle, **_kwargs: (Path("python"), Path("python-target"), Path("launcher")),
    )
    monkeypatch.setattr(
        controller,
        "_consume_runtime_provenance_authority",
        lambda *_args, **_kwargs: ({"runtimeContentDigest": "0" * 64}, "1" * 64),
    )
    observed: dict[str, Path] = {}

    def capture_lock(bundle: Path) -> tuple[int, Path]:
        observed["bundle"] = bundle
        raise RuntimeError("lock-captured")

    monkeypatch.setattr(controller, "_acquire_lifecycle_source_lock", capture_lock)

    with pytest.raises(RuntimeError, match="lock-captured"):
        controller._run_lifecycle_service(
            bundle_root=installed_bundle,
            service_args=["preflight", "--hermes-home", str(profile)],
        )

    assert observed["bundle"] == source_bundle


def test_installed_lifecycle_submits_the_clean_source_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    source_root = tmp_path / "immutable-source"
    source_bundle = source_root / "distribution" / controller.PLUGIN_ID
    installed_bundle = tmp_path / "installed" / controller.PLUGIN_ID
    runtime = installed_bundle / "runtime"
    profile = tmp_path / "profile"
    source_bundle.mkdir(parents=True)
    runtime.mkdir(parents=True)
    profile.mkdir(mode=0o700)
    source_manifest = source_bundle / controller.SOURCE_MANIFEST_NAME
    source_manifest.write_text("{\"source\":true}\n", encoding="utf-8")
    (installed_bundle / controller.SOURCE_MANIFEST_NAME).write_text(
        "{\"installed_locator\":true}\n", encoding="utf-8"
    )
    (runtime / controller.RUNTIME_BINDING_NAME).write_text(
        json.dumps({
            "mode": controller.RUNTIME_LOCATOR_MODE_INSTALLED,
            "source_root": str(source_root),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        controller,
        "_verify_binding",
        lambda _bundle, **_kwargs: (Path("python"), Path("python-target"), Path("launcher")),
    )
    observed: dict[str, str] = {}

    def capture_manifest(
        manifest_path: str,
        **_kwargs: object,
    ) -> tuple[dict, str]:
        observed["manifest_path"] = manifest_path
        raise RuntimeError("manifest-captured")

    monkeypatch.setattr(controller, "_consume_runtime_provenance_authority", capture_manifest)

    with pytest.raises(RuntimeError, match="manifest-captured"):
        controller._run_lifecycle_service(
            bundle_root=installed_bundle,
            service_args=["preflight", "--hermes-home", str(profile)],
        )

    assert observed["manifest_path"] == str(source_manifest)


def test_lifecycle_controller_executes_one_pinned_authority_consumer_snapshot() -> None:
    root = Path(__file__).resolve().parents[1]
    controller = root / "scripts" / "orch_next_hermes_mcp_launcher.py"
    consumer = root / "tui_gateway" / "maestro_authority.py"
    source = controller.read_text(encoding="utf-8")
    function = source[
        source.index("def _load_authority_consumer(") : source.index(
            "def _consume_runtime_provenance_authority("
        )
    ]

    assert hashlib.sha256(consumer.read_bytes()).hexdigest() in source
    assert function.count("descriptor = os.open(path, flags)") == 1
    assert "O_NOFOLLOW" in function
    assert "before = os.fstat(descriptor)" in function
    assert "after = os.fstat(descriptor)" in function
    assert "hashlib.sha256(source).hexdigest() != AUTHORITY_CONSUMER_SHA256" in function
    assert 'code = compile(source, str(path), "exec", dont_inherit=True)' in function
    assert "exec(code, module.__dict__)" in function
    assert "from tui_gateway import maestro_authority" not in source


def test_lifecycle_service_pins_controller_and_reconsumes_external_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    controller = root / "scripts" / "orch_next_hermes_mcp_launcher.py"
    service_path = root / "scripts" / "orch_next_hermes_serve_service.py"
    source = service_path.read_text(encoding="utf-8")
    loader = source[
        source.index("def _load_lifecycle_controller_snapshot(") : source.index(
            "def _consume_lifecycle_runtime_authority("
        )
    ]
    main = source[source.index("def main(") :]

    assert hashlib.sha256(controller.read_bytes()).hexdigest() in source
    assert loader.count("descriptor = os.open(path, flags)") == 1
    assert "O_NOFOLLOW" in loader
    assert "before = os.fstat(descriptor)" in loader
    assert "after = os.fstat(descriptor)" in loader
    assert "exec(code, module.__dict__)" in loader
    assert main.index("_consume_lifecycle_runtime_authority(") < main.index(
        'if args.action == "preflight"'
    )
    assert "ORCH_LIFECYCLE_CONTROLLER_ADMISSION" not in source


@pytest.mark.parametrize("swap_target", ["service", "interpreter"])
def test_lifecycle_controller_rejects_swap_during_external_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap_target: str,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    root = tmp_path / "checkout"
    bundle = root / "distribution" / controller.PLUGIN_ID
    runtime_dir = bundle / "runtime"
    scripts = root / "scripts"
    runtime_bin = root / ".venv" / "bin"
    runtime_dir.mkdir(parents=True)
    scripts.mkdir(parents=True)
    runtime_bin.mkdir(parents=True)
    manifest = bundle / controller.SOURCE_MANIFEST_NAME
    manifest.write_text("{}\n", encoding="utf-8")
    service_path = root / controller.LIFECYCLE_SERVICE_PATH
    admitted_service = b"raise SystemExit('admitted snapshot only')\n"
    service_path.write_bytes(admitted_service)
    service_path.chmod(0o755)
    runtime_python = runtime_bin / "python"
    admitted_interpreter = b"#!/bin/sh\nexit 0\n"
    runtime_python.write_bytes(admitted_interpreter)
    runtime_python.chmod(0o755)
    (runtime_dir / controller.RUNTIME_BINDING_NAME).write_text(
        json.dumps({
            "runtime_files": [
                {
                    "path": controller.LIFECYCLE_SERVICE_PATH,
                    "sha256": hashlib.sha256(admitted_service).hexdigest(),
                }
            ]
        }),
        encoding="utf-8",
    )

    def verify(*_args, **_kwargs):
        if runtime_python.read_bytes() != admitted_interpreter:
            raise SystemExit("runtime interpreter identity drift")
        return runtime_python, runtime_python, scripts / "controller.py"

    def consume(*_args, **_kwargs):
        target = service_path if swap_target == "service" else runtime_python
        replacement = target.with_name(target.name + ".replacement")
        replacement.write_bytes(b"#!/bin/sh\nexit 91\n")
        replacement.chmod(0o755)
        os.replace(replacement, target)
        return (
            {"runtimeContentDigest": hashlib.sha256(manifest.read_bytes()).hexdigest()},
            "unused",
        )

    monkeypatch.setattr(controller, "REPO_ROOT", root)
    monkeypatch.setattr(
        controller, "_admit_lifecycle_authority_home", lambda _args: tmp_path
    )
    monkeypatch.setattr(controller, "_verify_binding", verify)
    monkeypatch.setattr(controller, "_consume_runtime_provenance_authority", consume)
    monkeypatch.setattr(
        controller.os,
        "execve",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("replacement service/interpreter must never execute")
        ),
    )
    monkeypatch.setattr(
        controller.sys,
        "argv",
        ["controller", controller.LIFECYCLE_SERVICE_FLAG, "preflight"],
    )

    with pytest.raises(SystemExit):
        controller._run_lifecycle_service()

    lock = bundle.parent / f".{bundle.name}.distribution.lock"
    assert not lock.exists()


def test_lifecycle_controller_never_reopens_interpreter_after_final_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    root = tmp_path / "checkout"
    bundle = root / "distribution" / controller.PLUGIN_ID
    runtime_dir = bundle / "runtime"
    scripts = root / "scripts"
    runtime_bin = root / ".venv" / "bin"
    runtime_dir.mkdir(parents=True)
    scripts.mkdir(parents=True)
    runtime_bin.mkdir(parents=True)
    manifest = bundle / controller.SOURCE_MANIFEST_NAME
    manifest.write_text("{}\n", encoding="utf-8")
    service_path = root / controller.LIFECYCLE_SERVICE_PATH
    admitted_service = b"raise SystemExit('admitted service snapshot')\n"
    service_path.write_bytes(admitted_service)
    service_path.chmod(0o755)
    runtime_python = runtime_bin / "python"
    admitted_interpreter = b"#!/bin/sh\nexit 0\n"
    runtime_python.write_bytes(admitted_interpreter)
    runtime_python.chmod(0o755)
    (runtime_dir / controller.RUNTIME_BINDING_NAME).write_text(
        json.dumps({
            "runtime_files": [
                {
                    "path": controller.LIFECYCLE_SERVICE_PATH,
                    "sha256": hashlib.sha256(admitted_service).hexdigest(),
                }
            ]
        }),
        encoding="utf-8",
    )
    verify_calls = 0

    def verify(*_args, **_kwargs):
        nonlocal verify_calls
        verify_calls += 1
        assert runtime_python.read_bytes() == admitted_interpreter
        return runtime_python, runtime_python, scripts / "controller.py"

    monkeypatch.setattr(controller, "REPO_ROOT", root)
    monkeypatch.setattr(
        controller, "_admit_lifecycle_authority_home", lambda _args: tmp_path
    )
    monkeypatch.setattr(controller, "_verify_binding", verify)
    monkeypatch.setattr(
        controller,
        "_consume_runtime_provenance_authority",
        lambda *_args, **_kwargs: (
            {"runtimeContentDigest": hashlib.sha256(manifest.read_bytes()).hexdigest()},
            "unused",
        ),
    )
    execute_snapshot = controller._execute_lifecycle_service_snapshot
    replacement_marker = tmp_path / "replacement-interpreter-executed"
    original_env = os.environ.copy()

    def replace_at_execution(*args, **kwargs):
        replacement = runtime_python.with_name("python.replacement")
        replacement.write_text(
            f"#!/bin/sh\n: > {str(replacement_marker)!r}\nexit 91\n",
            encoding="utf-8",
        )
        replacement.chmod(0o755)
        os.replace(replacement, runtime_python)
        try:
            return execute_snapshot(*args, **kwargs)
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    monkeypatch.setattr(
        controller, "_execute_lifecycle_service_snapshot", replace_at_execution
    )
    monkeypatch.setattr(
        controller.sys,
        "argv",
        ["controller", controller.LIFECYCLE_SERVICE_FLAG, "preflight"],
    )

    with pytest.raises(SystemExit, match="admitted service snapshot"):
        controller._run_lifecycle_service()

    assert verify_calls == 3
    assert not replacement_marker.exists()
    lock = bundle.parent / f".{bundle.name}.distribution.lock"
    assert not lock.exists()


def test_clean_revision_checks_exclude_only_retained_distribution_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as controller

    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=root, check=True, timeout=5)
    subprocess.run(
        ["/usr/bin/git", "config", "user.name", "Test"],
        cwd=root,
        check=True,
        timeout=5,
    )
    subprocess.run(
        ["/usr/bin/git", "config", "user.email", "test@example.invalid"],
        cwd=root,
        check=True,
        timeout=5,
    )
    (root / "seed").write_text("committed\n", encoding="utf-8")
    (root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    subprocess.run(
        ["/usr/bin/git", "add", "seed", ".gitignore"],
        cwd=root,
        check=True,
        timeout=5,
    )
    subprocess.run(
        ["/usr/bin/git", "commit", "-q", "-m", "seed"],
        cwd=root,
        check=True,
        timeout=5,
    )
    head = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    lock = root / "distribution" / ".orch-next-hermes-harness.distribution.lock"
    lock.parent.mkdir()
    lock.write_text(f"pid={os.getpid()}\n", encoding="ascii")
    runtime_bin = root / ".venv" / "bin"
    runtime_bin.mkdir(parents=True)
    runtime = runtime_bin / "hermes"
    python = runtime_bin / "python"
    runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.chmod(0o755)
    python.chmod(0o755)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    config = service.ServiceConfig(
        worktree=root,
        runtime=runtime,
        python=python,
        hermes_home=profile,
    )
    monkeypatch.setattr(controller, "REPO_ROOT", root)

    assert controller._runtime_head() == head
    assert service._session_token_runtime_revision(config) == head

    (root / "unrelated").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(
        SystemExit, match="Hermes runtime provenance authority unavailable"
    ):
        controller._runtime_head()
    assert service._session_token_runtime_revision(config) is None


@pytest.mark.parametrize("forge_expected_origin", [False, True])
def test_preloaded_foreign_command_dependency_stops_before_lifecycle_action(
    tmp_path: Path,
    forge_expected_origin: bool,
) -> None:
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    runtime = root / ".venv" / "bin" / "hermes"
    script = root / "scripts" / "orch_next_hermes_serve_service.py"
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    marker = tmp_path / "foreign-provider-used"
    foreign_origin = (
        str(root / "agent" / "secret_sources" / "bitwarden.py")
        if forge_expected_origin
        else "/foreign/agent/secret_sources/bitwarden.py"
    )
    probe_source = f"""
import runpy
import sys
import types
from pathlib import Path

marker = Path({str(marker)!r})

class ForeignBitwarden(types.ModuleType):
    def __getattr__(self, name):
        marker.write_text(name, encoding="utf-8")
        raise AttributeError(name)

foreign = ForeignBitwarden("agent.secret_sources.bitwarden")
foreign.__file__ = {foreign_origin!r}
foreign.FetchResult = type("ForeignFetchResult", (), {{}})
sys.modules[foreign.__name__] = foreign
sys.argv = [
    {str(script)!r},
    "status",
    "--worktree", {str(root)!r},
    "--runtime", {str(runtime)!r},
    "--python", {str(python)!r},
    "--hermes-home", {str(profile)!r},
    "--dry-run",
]
runpy.run_path({str(script)!r}, run_name="__main__")
"""

    result = subprocess.run(
        [str(python), "-c", probe_source],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output["action"] == "status"
    assert output["detail"] == "isolated_launcher_required"
    assert result.stderr == ""
    assert not marker.exists()


def test_isolated_launcher_ignores_hostile_sitecustomize_and_pythonpath(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    runtime = root / ".venv" / "bin" / "hermes"
    launcher = root / "scripts" / "orch_next_hermes_serve_service_launcher.sh"
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    marker = tmp_path / "sitecustomize-used"
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        f"""
import importlib.machinery
import sys
import types
from pathlib import Path

marker = Path({str(marker)!r})
marker.write_text("sitecustomize", encoding="utf-8")
sys.modules["secrets"] = types.ModuleType("secrets")
sys.modules["subprocess"] = types.ModuleType("subprocess")
original_exec = importlib.machinery.SourceFileLoader.exec_module
def hostile_exec(self, module):
    marker.write_text("loader", encoding="utf-8")
    return original_exec(self, module)
importlib.machinery.SourceFileLoader.exec_module = hostile_exec
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)

    result = subprocess.run(
        [
            str(launcher),
            "preflight",
            "--worktree",
            str(root),
            "--runtime",
            str(runtime),
            "--python",
            str(python),
            "--hermes-home",
            str(profile),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Hermes runtime provenance authority unavailable" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("action", ["install", "status", "start", "restart"])
def test_lifecycle_cli_fails_before_action_when_checkout_imports_are_unavailable(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
) -> None:
    monkeypatch.setattr(service, "_checkout_import_preflight", lambda _config: False)
    monkeypatch.setitem(
        service._ACTIONS,
        action,
        lambda *_args, **_kwargs: pytest.fail(
            "unadmitted checkout imports must stop before lifecycle action"
        ),
    )

    result = service.main([
        action,
        "--worktree",
        str(service_config.worktree),
        "--runtime",
        str(service_config.runtime),
        "--python",
        str(service_config.python),
        "--hermes-home",
        str(service_config.hermes_home),
        "--dry-run",
    ])

    assert result == 1
    output = json.loads(capsys.readouterr().out)
    assert output["action"] == action
    assert output["state"] == "unavailable"
    assert output["detail"] == "checkout_imports_unavailable"


@pytest.mark.parametrize("action", ["start", "restart"])
def test_production_cli_start_and_restart_never_build_or_route_authority_context(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
) -> None:
    def forbidden_builder(_config: service.ServiceConfig) -> object:
        raise AssertionError("start/restart must not build token authority context")

    calls: list[dict[str, object]] = []

    def lifecycle_action(
        checked: service.ServiceConfig,
        _plist_path: Path,
        **kwargs: object,
    ) -> service.ServiceResult:
        calls.append(kwargs)
        return service.ServiceResult(
            action, service.ServiceState.STOPPED, checked.label, True
        )

    monkeypatch.setattr(
        service, "_session_token_install_authority_context", forbidden_builder
    )
    monkeypatch.setitem(service._ACTIONS, action, lifecycle_action)
    monkeypatch.setattr(
        service, "default_plist_path", lambda: tmp_path / "service.plist"
    )

    return_code = service.main([
        action,
        "--worktree",
        str(service_config.worktree),
        "--runtime",
        str(service_config.runtime),
        "--python",
        str(service_config.python),
        "--hermes-home",
        str(service_config.hermes_home),
    ])

    assert return_code == 0
    assert calls == [{"dry_run": False}]
    assert "session_token_authority_context" not in capsys.readouterr().out


def test_production_cli_dry_run_install_does_not_build_authority_context(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_builder(_config: service.ServiceConfig) -> object:
        raise AssertionError("dry-run must not build token authority context")

    calls: list[dict[str, object]] = []

    def install_action(
        checked: service.ServiceConfig,
        plist_path: Path,
        **kwargs: object,
    ) -> service.ServiceResult:
        calls.append(kwargs)
        return service._planned("install", checked, plist_path)

    monkeypatch.setattr(
        service, "_session_token_install_authority_context", forbidden_builder
    )
    monkeypatch.setattr(
        service,
        "_prepare_session_token_command_config",
        forbidden_builder,
    )
    monkeypatch.setitem(service._ACTIONS, "install", install_action)
    monkeypatch.setattr(
        service, "default_plist_path", lambda: tmp_path / "service.plist"
    )

    assert (
        service.main([
            "install",
            "--worktree",
            str(service_config.worktree),
            "--runtime",
            str(service_config.runtime),
            "--python",
            str(service_config.python),
            "--hermes-home",
            str(service_config.hermes_home),
            "--dry-run",
        ])
        == 0
    )
    assert calls == [{"dry_run": True}]
    capsys.readouterr()


@pytest.mark.parametrize(
    "flag",
    [
        "--authority-bundle-digest",
        "--threshold-policy-digest",
        "--prompt-contract-digest",
        "--runtime-revision",
        "--token-helper",
        "--token",
        "--receipt",
    ],
)
def test_production_cli_rejects_authority_policy_runtime_and_token_injection(
    service_config: service.ServiceConfig,
    flag: str,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        service.main([
            "install",
            "--worktree",
            str(service_config.worktree),
            "--runtime",
            str(service_config.runtime),
            "--python",
            str(service_config.python),
            "--hermes-home",
            str(service_config.hermes_home),
            flag,
            "caller-selected",
        ])
    assert exc_info.value.code == 2


def test_default_plist_path_uses_passwd_home_not_profile_home(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_home = tmp_path / "actual-account-home"
    monkeypatch.setattr(Path, "home", lambda: service_config.hermes_home)
    monkeypatch.setenv("HOME", str(service_config.hermes_home))

    path = service.default_plist_path(
        service_config.label,
        uid=501,
        passwd_lookup=lambda uid: SimpleNamespace(pw_dir=account_home, pw_uid=uid),
    )

    assert path == (
        account_home / "Library" / "LaunchAgents" / "com.orchnext.hermes.serve.plist"
    )
    assert not str(path).startswith(str(service_config.hermes_home))


@pytest.mark.parametrize("loaded_domain", ["gui/501", "user/501"])
def test_domain_selection_probes_gui_then_user(
    service_config: service.ServiceConfig,
    loaded_domain: str,
) -> None:
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        target = command[-1]
        return_code = 0 if target.startswith(f"{loaded_domain}/") else 113
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    selected = service.select_launchd_domain(
        service_config, runner=fake_runner, uid=501
    )

    assert selected == loaded_domain
    assert calls[0][-1] == "gui/501/com.orchnext.hermes.serve"
    if loaded_domain == "user/501":
        assert calls[1][-1] == "user/501/com.orchnext.hermes.serve"


def test_install_uses_private_state_without_creating_a_log_surface(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    runner_options: list[dict[str, object]] = []
    staged_payloads: list[bytes] = []
    staged_modes: list[int] = []

    def fake_runner(
        command: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        runner_options.append(options)
        if command[1] == "bootstrap":
            stage_path = Path(command[-1])
            # Match the real host's path-classification boundary: launchctl
            # returned 66 for the same valid bytes when the unique stage did
            # not retain a .plist suffix.
            if stage_path.suffix != ".plist":
                return subprocess.CompletedProcess(command, 66, stdout="", stderr="")
            staged_payloads.append(stage_path.read_bytes())
            staged_modes.append(stage_path.stat().st_mode & 0o777)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    plist_path = tmp_path / "user-home" / "Library" / "LaunchAgents" / "serve.plist"
    plist_path.parent.mkdir(parents=True)
    result = service.install_service(
        service_config, plist_path, runner=fake_runner, domain="gui/501"
    )

    assert result.state is service.ServiceState.INSTALLED
    assert calls == [
        [service.LAUNCHCTL_PATH, "bootout", "gui/501/com.orchnext.hermes.serve"],
        [service.LAUNCHCTL_PATH, "bootstrap", "gui/501", calls[1][-1]],
        [service.LAUNCHCTL_PATH, "print", "gui/501/com.orchnext.hermes.serve"],
    ]
    assert calls[1][-1].startswith(
        str(plist_path.parent / ".serve.plist.bootstrap-consume-")
    )
    assert calls[1][-1].endswith(".plist")
    assert calls[1][-1] != str(plist_path)
    assert "pass_fds" not in runner_options[1]
    assert staged_payloads == [_valid_plist_bytes(service_config)]
    assert staged_modes == [service.BOOTSTRAP_STAGE_MODE]
    assert not Path(calls[1][-1]).exists()
    _assert_identity_bearing_recovery_records(result, str(tmp_path))
    assert plist_path.is_file()
    assert os.stat(plist_path).st_mode & 0o777 == 0o600
    for directory in (
        service_config.services_dir,
        service_config.service_root,
        service_config.state_dir,
    ):
        assert os.stat(directory).st_mode & 0o777 == 0o700
    assert not (service_config.service_root / "logs").exists()
    installed = plistlib.loads(plist_path.read_bytes())
    assert installed["StandardOutPath"] == "/dev/null"
    assert installed["StandardErrorPath"] == "/dev/null"


@pytest.mark.parametrize("symlink_component", ["service_root", "state_dir"])
def test_install_rejects_service_directory_symlinks(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    symlink_component: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    if symlink_component == "service_root":
        service_config.service_root.parent.mkdir()
        service_config.service_root.symlink_to(outside, target_is_directory=True)
    else:
        service_config.services_dir.mkdir(mode=0o700)
        service_config.service_root.mkdir(mode=0o700)
        service_config.state_dir.symlink_to(outside, target_is_directory=True)
    plist_path = tmp_path / "service.plist"

    def absent_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=absent_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_lock_error"
    assert not plist_path.exists()
    assert getattr(service_config, symlink_component).is_symlink()


@pytest.mark.parametrize("owned_component", ["service_root", "state_dir"])
def test_private_service_directories_reject_foreign_owner(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
    owned_component: str,
) -> None:
    service_config.services_dir.mkdir(mode=0o700)
    service_config.service_root.mkdir(mode=0o700)
    service_config.state_dir.mkdir(mode=0o700)
    foreign_path = getattr(service_config, owned_component)
    real_lstat = os.lstat

    def foreign_lstat(path: os.PathLike[str] | str) -> os.stat_result:
        info = real_lstat(path)
        if Path(path) == foreign_path:
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(service.os, "lstat", foreign_lstat)

    with pytest.raises(service.ConfigurationError, match="owned by current account"):
        service.ensure_private_directories(service_config)


def test_lifecycle_directory_creation_failure_precedes_launchctl(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_mkdir = Path.mkdir

    def fail_service_root(path: Path, *args: object, **kwargs: object) -> None:
        if path == service_config.service_root:
            raise OSError("raw-secret mkdir failure")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_service_root)
    result = service.install_service(
        service_config,
        tmp_path / "service.plist",
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("launchctl must not run before lifecycle locking")
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_lock_error"
    assert result.installed is False
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_concurrent_installer_is_rejected_by_single_writer_lock(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    service.ensure_private_directories(service_config)
    lock_path = service_config.state_dir / "lifecycle.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = service.install_service(
            service_config,
            tmp_path / "service.plist",
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("concurrent installer must not invoke launchctl")
            ),
            domain="gui/501",
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_busy"
    assert result.installed is False


def test_config_recovery_service_uses_existing_lifecycle_lock_and_family(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service.ensure_private_directories(service_config)
    recovery = service_config.hermes_home / ".config.yaml.orch-recovery"
    retired = service_config.hermes_home / ".config.yaml.orch-retired"
    for path, content in (
        (recovery, b"recovery: generation\n"),
        (retired, b"retired: generation\n"),
    ):
        path.write_bytes(content)
        path.chmod(0o600)

    def expectation(path: Path) -> service.ConfigArtifactExpectation:
        info = path.lstat()
        return service.ConfigArtifactExpectation(
            "regular",
            info.st_uid,
            stat.S_IMODE(info.st_mode),
            info.st_dev,
            info.st_ino,
        )

    signal_mask_calls: list[tuple[int, set[signal.Signals] | set[int]]] = []

    def record_signal_mask(
        operation: int,
        signals: set[signal.Signals] | set[int],
    ) -> set[int]:
        signal_mask_calls.append((operation, set(signals)))
        return set()

    monkeypatch.setattr(service.signal, "pthread_sigmask", record_signal_mask)

    result = service.recover_config_service(
        service_config,
        tmp_path / "service.plist",
        request=service.ConfigRecoveryRequest(
            expectation(recovery),
            "quarantine",
            expectation(retired),
            "quarantine",
        ),
    )

    assert result.state is service.ServiceState.RECOVERED
    assert result.detail == "session_token_config_recovery_quarantined"
    assert not recovery.exists()
    assert not retired.exists()
    assert not (service_config.state_dir / "lifecycle.lock").read_bytes()
    assert signal_mask_calls == [
        (
            signal.SIG_BLOCK,
            {signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM},
        ),
        (signal.SIG_SETMASK, set()),
    ]


def test_config_recovery_service_refuses_second_lifecycle_writer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service.ensure_private_directories(service_config)
    lock_path = service_config.state_dir / "lifecycle.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    monkeypatch.setattr(
        service,
        "_admitted_checkout_module",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("second writer must fail before recovery import")
        ),
    )
    request = service.ConfigRecoveryRequest(
        service.ConfigArtifactExpectation("regular", os.getuid(), 0o600, 1, 1),
        "quarantine",
        None,
        None,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = service.recover_config_service(
            service_config,
            tmp_path / "service.plist",
            request=request,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_busy"


def test_config_recovery_cli_identity_is_metadata_only_and_restore_is_bound() -> None:
    parser = service.build_parser()
    identity = service._config_artifact_expectation("regular:501:0600:42:99")
    assert identity == service.ConfigArtifactExpectation(
        "regular",
        501,
        0o600,
        42,
        99,
    )
    with pytest.raises(service.ConfigurationError, match="active config identity"):
        service._config_recovery_request_from_args(
            parser.parse_args(
                [
                    "recover-config",
                    "--worktree",
                    "/tmp/worktree",
                    "--runtime",
                    "/tmp/worktree/.venv/bin/hermes",
                    "--python",
                    "/tmp/worktree/.venv/bin/python",
                    "--hermes-home",
                    "/tmp/profile",
                    "--recovery-identity",
                    "regular:501:0600:42:99",
                    "--recovery-disposition",
                    "restore",
                ]
            )
        )


def test_state_directory_swap_cannot_create_a_second_lifecycle_writer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    service.ensure_private_directories(service_config)
    displaced_state = service_config.service_root / "displaced-state"

    with service._lifecycle_lock(service_config):
        service_config.state_dir.rename(displaced_state)
        service_config.state_dir.mkdir(mode=0o700)
        result = service.install_service(
            service_config,
            tmp_path / "service.plist",
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("state-dir swap must not admit a second writer")
            ),
            domain="gui/501",
        )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_busy"
    assert result.installed is False


def test_hermes_home_swap_cannot_create_a_second_lifecycle_writer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    service.ensure_private_directories(service_config)
    displaced_home = service_config.hermes_home.with_name("orch-displaced")

    with service._lifecycle_lock(service_config):
        service_config.hermes_home.rename(displaced_home)
        service_config.hermes_home.mkdir(mode=0o700)
        result = service.install_service(
            service_config,
            tmp_path / "service.plist",
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("HERMES_HOME swap must not admit a second writer")
            ),
            domain="gui/501",
        )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_busy"
    assert result.installed is False


def test_lock_leaf_recreation_cannot_create_a_second_lifecycle_writer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    service.ensure_private_directories(service_config)
    lock_path = service_config.state_dir / "lifecycle.lock"

    with service._lifecycle_lock(service_config):
        lock_path.unlink()
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)
        result = service.install_service(
            service_config,
            tmp_path / "service.plist",
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("lock-leaf swap must not admit a second writer")
            ),
            domain="gui/501",
        )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_busy"
    assert result.installed is False


def test_existing_plist_rejects_symlink_and_foreign_owner(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.plist"
    target.write_text("target", encoding="utf-8")
    linked = tmp_path / "linked.plist"
    linked.symlink_to(target)

    symlink_result = service.service_status(
        service_config,
        linked,
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("launchctl must not run")
        ),
        domain="gui/501",
    )
    assert symlink_result.state is service.ServiceState.ERROR
    assert symlink_result.detail == "plist_target_rejected"

    real_stat = os.stat

    def foreign_stat(
        path: os.PathLike[str] | str, *args: object, **kwargs: object
    ) -> os.stat_result:
        info = real_stat(path, *args, **kwargs)
        if Path(path) == Path(target.name) and kwargs.get("dir_fd") is not None:
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(service.os, "stat", foreign_stat)
    owner_result = service.service_status(
        service_config,
        target,
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("launchctl must not run")
        ),
        domain="gui/501",
    )
    assert owner_result.state is service.ServiceState.ERROR
    assert owner_result.detail == "plist_target_rejected"


def test_launchagents_parent_rejects_group_or_world_write_mode(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    launchagents = tmp_path / "LaunchAgents"
    launchagents.mkdir(mode=0o700)
    plist_path = launchagents / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    launchagents.chmod(0o722)

    result = service.service_status(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe parent must be rejected before launchctl")
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_target_rejected"


@pytest.mark.parametrize("unsafe_mode", [0o620, 0o602])
def test_existing_plist_rejects_group_or_world_write_mode(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    unsafe_mode: int,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    plist_path.chmod(unsafe_mode)

    result = service.service_status(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe plist must be rejected before launchctl")
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_target_rejected"


@pytest.mark.parametrize("already_absent_returncode", [3, 113])
def test_install_allows_only_explicit_already_absent_bootout_results(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    already_absent_returncode: int,
) -> None:
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return_code = already_absent_returncode if command[1] == "bootout" else 0
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    plist_path = tmp_path / "service.plist"
    result = service.install_service(
        service_config, plist_path, runner=fake_runner, domain="gui/501"
    )

    assert result.state is service.ServiceState.INSTALLED
    assert [command[1] for command in calls] == ["bootout", "bootstrap", "print"]
    assert plist_path.is_file()


def test_install_detects_launchagents_parent_swap_and_cleans_admitted_candidate(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    launchagents = tmp_path / "LaunchAgents"
    launchagents.mkdir(mode=0o700)
    admitted_parent = tmp_path / "admitted-LaunchAgents"
    plist_path = launchagents / "service.plist"
    bootout_calls = 0

    def swap_parent_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootout_calls
        if command[1] == "bootout":
            bootout_calls += 1
            if bootout_calls == 1:
                launchagents.rename(admitted_parent)
                launchagents.mkdir(mode=0o700)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=swap_parent_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_parent_changed_candidate_quarantined"
    assert result.installed is False
    assert bootout_calls == 2
    assert not (launchagents / plist_path.name).exists()
    assert not (admitted_parent / plist_path.name).exists()


def test_install_rejects_leaf_swap_immediately_after_atomic_replace(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    attacker = tmp_path / "attacker.plist"
    calls: list[list[str]] = []
    real_atomic_write = service._atomic_write_at

    def swap_after_replace(*args: object, **kwargs: object) -> object:
        candidate = real_atomic_write(*args, **kwargs)
        attacker.write_text("attacker-controlled", encoding="utf-8")
        attacker.chmod(0o600)
        os.replace(attacker, plist_path)
        return candidate

    monkeypatch.setattr(service, "_atomic_write_at", swap_after_replace)

    def successful_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=successful_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_candidate_changed_rollback_quarantine_error"
    assert result.installed is True
    assert result.loaded is False
    assert plist_path.read_text(encoding="utf-8") == "attacker-controlled"
    assert [command[1] for command in calls] == ["bootout", "bootout"]


@pytest.mark.parametrize("swap_phase", ["bootstrap", "print"])
def test_install_rechecks_exact_candidate_after_bootstrap_and_confirmation(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    swap_phase: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    attacker = tmp_path / "attacker.plist"
    swapped = False
    bootout_calls = 0

    def swap_during_launchctl(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootout_calls, swapped
        if command[1] == "bootout":
            bootout_calls += 1
        if command[1] == swap_phase and not swapped:
            attacker.write_text("attacker-controlled", encoding="utf-8")
            attacker.chmod(0o600)
            os.replace(attacker, plist_path)
            swapped = True
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=swap_during_launchctl,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert (
        result.detail
        == "launchctl_postconfirm_identity_error_rollback_quarantine_error"
    )
    assert result.installed is True
    assert result.loaded is False
    assert bootout_calls == 2
    assert plist_path.read_text(encoding="utf-8") == "attacker-controlled"


def test_install_bootout_error_preserves_existing_plist_and_stops(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_text("original-plist", encoding="utf-8")
    calls: list[list[str]] = []

    def denied_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 77, stdout="credential=raw-secret", stderr="permission raw-secret"
        )

    result = service.install_service(
        service_config, plist_path, runner=denied_runner, domain="gui/501"
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_bootout_error"
    assert plist_path.read_text(encoding="utf-8") == "original-plist"
    assert [command[1] for command in calls] == ["bootout"]
    assert service_config.state_dir.is_dir()
    assert (service_config.state_dir / "lifecycle.lock").is_file()
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_install_timeout_restores_prior_bytes_mode_and_registration(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    prior_bytes = _valid_plist_bytes(service_config)
    plist_path.write_bytes(prior_bytes)
    plist_path.chmod(0o640)
    bootstrap_calls = 0
    calls: list[list[str]] = []

    def timeout_then_restore_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_calls
        calls.append(command)
        if command[1] == "bootout":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        bootstrap_calls += 1
        if bootstrap_calls == 1:
            raise subprocess.TimeoutExpired(command, 30, output="raw-secret")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=timeout_then_restore_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "launchctl_timeout_rolled_back"
    assert result.installed is True
    assert result.loaded is True
    assert plist_path.read_bytes() == prior_bytes
    assert os.stat(plist_path).st_mode & 0o777 == 0o640
    assert [command[1] for command in calls] == [
        "bootout",
        "bootstrap",
        "bootout",
        "bootstrap",
        "print",
    ]
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_install_nonzero_bootstrap_restores_and_rebootstraps_prior_definition(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    prior_bytes = _valid_plist_bytes(service_config)
    plist_path.write_bytes(prior_bytes)
    plist_path.chmod(0o600)
    bootstrap_calls = 0

    def fail_new_then_restore_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_calls
        if command[1] == "bootout":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1] == "print":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        bootstrap_calls += 1
        return_code = 77 if bootstrap_calls == 1 else 0
        return subprocess.CompletedProcess(
            command,
            return_code,
            stdout="credential=raw-secret",
            stderr="permission raw-secret",
        )

    result = service.install_service(
        service_config,
        plist_path,
        runner=fail_new_then_restore_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_bootstrap_error_rolled_back"
    assert result.loaded is True
    assert bootstrap_calls == 2
    assert plist_path.read_bytes() == prior_bytes
    assert os.stat(plist_path).st_mode & 0o777 == 0o600
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_first_install_bootstrap_failure_removes_candidate_plist(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"

    def failed_bootstrap_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return_code = 113 if command[1] == "bootout" else 77
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=failed_bootstrap_runner,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_bootstrap_error_candidate_quarantined"
    assert result.installed is False
    assert not plist_path.exists()


@pytest.mark.parametrize("failure_stage", ["render", "fchmod", "write", "replace"])
def test_post_bootout_filesystem_failures_restore_prior_definition(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    prior_bytes = _valid_plist_bytes(service_config)
    plist_path.write_bytes(prior_bytes)
    plist_path.chmod(0o640)

    if failure_stage == "render":
        monkeypatch.setattr(
            service,
            "render_launchd_plist",
            lambda _config: (_ for _ in ()).throw(RuntimeError("raw-secret render")),
        )
    else:
        attribute = {
            "fchmod": "fchmod",
            "write": "write",
            "replace": "replace",
        }[failure_stage]
        real_operation = getattr(service.os, attribute)
        failed = False

        def fail_once(*args: object, **kwargs: object) -> object:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(f"raw-secret {failure_stage}")
            return real_operation(*args, **kwargs)

        monkeypatch.setattr(service.os, attribute, fail_once)

    def successful_launchctl(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=successful_launchctl,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "filesystem_error_rolled_back"
    assert result.loaded is True
    assert plist_path.read_bytes() == prior_bytes
    assert os.stat(plist_path).st_mode & 0o777 == 0o640
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)
    if failure_stage != "render":
        _assert_identity_bearing_recovery_records(result, str(tmp_path))


def test_failed_confirmation_rolls_back_and_never_reports_loaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    prior_bytes = _valid_plist_bytes(service_config)
    plist_path.write_bytes(prior_bytes)
    print_calls = 0

    def confirmation_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls
        if command[1] == "print":
            print_calls += 1
            return_code = 77 if print_calls == 1 else 0
            stdout = (
                "raw-secret"
                if return_code
                else _valid_launchd_print(service_config, "gui/501")
            )
            return subprocess.CompletedProcess(
                command, return_code, stdout=stdout, stderr="raw-secret"
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=confirmation_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is True
    assert result.detail == "launchctl_confirmation_error_rolled_back"
    assert print_calls == 2
    assert plist_path.read_bytes() == prior_bytes
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_foreign_definition_reregistered_during_rollback_is_finally_booted_out(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[str] = []
    registered = False

    def foreign_twice(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal registered
        operation = command[1]
        calls.append(operation)
        if operation == "bootout":
            registered = False
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if operation == "bootstrap":
            registered = True
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if operation == "print":
            foreign = _valid_launchd_print(service_config, "gui/501").replace(
                str(service_config.runtime), "/tmp/foreign-runtime", 1
            )
            return subprocess.CompletedProcess(command, 0, stdout=foreign, stderr="")
        raise AssertionError(f"unexpected launchctl operation: {operation}")

    result = service.install_service(
        service_config,
        plist_path,
        runner=foreign_twice,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert (
        result.detail
        == "launchctl_confirmation_error_rollback_confirmation_error_contained"
    )
    assert calls == [
        "bootout",
        "bootstrap",
        "print",
        "bootout",
        "bootstrap",
        "print",
        "bootout",
    ]
    assert registered is False


@pytest.mark.parametrize("containment_failure", ["returncode", "timeout"])
def test_rollback_foreign_definition_containment_failure_is_typed_not_loaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    containment_failure: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    bootouts = 0

    def containment_fails(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootouts
        operation = command[1]
        if operation == "bootout":
            bootouts += 1
            if bootouts == 3:
                if containment_failure == "timeout":
                    raise subprocess.TimeoutExpired(command, 30, output="raw-secret")
                return subprocess.CompletedProcess(command, 77, stdout="", stderr="")
        if operation == "print":
            foreign = _valid_launchd_print(service_config, "gui/501").replace(
                str(service_config.runtime), "/tmp/foreign-runtime", 1
            )
            return subprocess.CompletedProcess(command, 0, stdout=foreign, stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=containment_fails,
        domain="gui/501",
    )

    suffix = (
        "bootout_unavailable" if containment_failure == "timeout" else "bootout_error"
    )
    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert result.detail == (
        "launchctl_confirmation_error_rollback_confirmation_error_" + suffix
    )
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


@pytest.mark.parametrize("rollback_swap_phase", ["bootstrap", "print"])
def test_rollback_revalidates_restored_definition_after_launchctl_confirmation(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    rollback_swap_phase: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    attacker = tmp_path / "attacker.plist"
    bootstrap_calls = 0
    print_calls = 0
    bootout_calls = 0

    def swap_restored_definition(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_calls, print_calls, bootout_calls
        if command[1] == "bootout":
            bootout_calls += 1
        if command[1] == "bootstrap":
            bootstrap_calls += 1
            if bootstrap_calls == 1:
                return subprocess.CompletedProcess(command, 77, stdout="", stderr="")
            if rollback_swap_phase == "bootstrap":
                attacker.write_text("attacker", encoding="utf-8")
                attacker.chmod(0o600)
                os.replace(attacker, plist_path)
        if command[1] == "print":
            print_calls += 1
            if rollback_swap_phase == "print":
                attacker.write_text("attacker", encoding="utf-8")
                attacker.chmod(0o600)
                os.replace(attacker, plist_path)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=swap_restored_definition,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert (
        result.detail == "launchctl_bootstrap_error_rollback_postconfirm_identity_error"
    )
    assert result.loaded is False
    assert result.installed is True
    assert bootout_calls == 3
    assert plist_path.read_text(encoding="utf-8") == "attacker"


def test_thirteen_byte_atomic_temp_is_cleanup_only_not_restorable(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    intended = _valid_plist_bytes(service_config)
    real_write = os.write
    writes = 0

    def interrupt_after_thirteen_bytes(fd: int, content: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            return real_write(fd, content[:13])
        raise OSError("interrupted atomic write")

    monkeypatch.setattr(service.os, "write", interrupt_after_thirteen_bytes)
    with service._open_plist_directory(plist_path) as directory:
        with pytest.raises(service.AtomicWriteError) as raised:
            service._atomic_write_at(
                directory,
                intended,
                service.PRIVATE_FILE_MODE,
                expected=None,
                expected_label=service_config.label,
            )

    record = raised.value.recovery_record
    assert record is not None
    assert record.artifact_kind is service.RecoveryArtifactKind.PARTIAL_ATOMIC_TEMP
    assert record.label_validated is False
    assert record.sha256 == hashlib.sha256(intended[:13]).hexdigest()
    assert (tmp_path / record.leaf).read_bytes() == intended[:13]
    assert not plist_path.exists()


def test_first_install_candidate_is_quarantined_without_physical_unlink(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    real_unlink = os.unlink

    def forbid_candidate_unlink(
        path: os.PathLike[str] | str, *args: object, **kwargs: object
    ) -> None:
        if ".remove-" in str(path) and kwargs.get("dir_fd") is not None:
            raise AssertionError("proven quarantine must not be physically unlinked")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(service.os, "unlink", forbid_candidate_unlink)

    def failed_bootstrap(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return_code = 77 if command[1] == "bootstrap" else 0
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=failed_bootstrap,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_bootstrap_error_candidate_quarantined"
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


def test_rollback_write_failure_is_typed_and_does_not_overclaim_loaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    real_fchmod = os.fchmod
    fchmod_calls = 0

    def fail_restore_fchmod(fd: int, mode: int) -> None:
        nonlocal fchmod_calls
        fchmod_calls += 1
        if fchmod_calls == 4:
            raise OSError("raw-secret restore chmod")
        real_fchmod(fd, mode)

    monkeypatch.setattr(service.os, "fchmod", fail_restore_fchmod)

    def failed_new_bootstrap(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return_code = 77 if command[1] == "bootstrap" else 0
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=failed_new_bootstrap,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert result.detail == "launchctl_bootstrap_error_rollback_write_error"
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_rollback_bootout_failure_preserves_candidate_and_is_typed(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_text("prior", encoding="utf-8")
    bootout_calls = 0

    def rollback_bootout_denied(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootout_calls
        if command[1] == "bootout":
            bootout_calls += 1
            return_code = 0 if bootout_calls == 1 else 77
        else:
            return_code = 77
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=rollback_bootout_denied,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert result.detail == "launchctl_bootstrap_error_rollback_bootout_error"
    assert plist_path.read_text(encoding="utf-8") != "prior"


def test_rollback_bootstrap_timeout_is_typed_and_not_loaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    bootstrap_calls = 0

    def rollback_timeout_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_calls
        if command[1] == "bootstrap":
            bootstrap_calls += 1
            if bootstrap_calls == 2:
                raise subprocess.TimeoutExpired(command, 30, output="raw-secret")
            return subprocess.CompletedProcess(command, 77, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=rollback_timeout_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.loaded is False
    assert result.detail == "launchctl_bootstrap_error_rollback_unavailable_contained"
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


@pytest.mark.parametrize("rollback_swap", ["symlink", "absent"])
def test_rollback_failure_reports_installed_from_no_follow_state(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    rollback_swap: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_text("prior", encoding="utf-8")
    bootout_calls = 0

    def swap_during_rollback_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootout_calls
        if command[1] == "bootout":
            bootout_calls += 1
            if bootout_calls == 2:
                plist_path.unlink()
                if rollback_swap == "symlink":
                    target = tmp_path / "foreign-target"
                    target.write_text("foreign", encoding="utf-8")
                    plist_path.symlink_to(target)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1] == "bootstrap":
            return subprocess.CompletedProcess(command, 77, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=swap_during_rollback_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_bootstrap_error_rollback_write_error"
    assert result.installed is False
    assert result.loaded is False


@pytest.mark.parametrize("already_absent_returncode", [3, 113])
def test_uninstall_allows_explicit_already_absent_bootout_results(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    already_absent_returncode: int,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def absent_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, already_absent_returncode, stdout="", stderr=""
        )

    result = service.uninstall_service(
        service_config, plist_path, runner=absent_runner, domain="gui/501"
    )

    assert result.state is service.ServiceState.REMOVED_QUARANTINED
    assert result.detail == "plist_quarantined"
    _assert_identity_bearing_recovery_records(result, str(tmp_path))
    assert not plist_path.exists()


def test_foreign_label_is_never_quarantined_as_a_restorable_service_definition(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    foreign = plistlib.loads(_valid_plist_bytes(service_config))
    foreign["Label"] = "com.foreign.service"
    foreign_bytes = plistlib.dumps(foreign, sort_keys=True)
    plist_path.write_bytes(foreign_bytes)

    def forbidden_runner(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("foreign-label plist must not reach launchctl")

    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=forbidden_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_label_mismatch"
    assert result.recovery_records == ()
    assert plist_path.read_bytes() == foreign_bytes
    assert list(tmp_path.glob(".service.plist.remove-*")) == []


def test_uninstall_bootout_error_keeps_plist_and_returns_typed_error(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def denied_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 77, stdout="credential=raw-secret", stderr="permission raw-secret"
        )

    result = service.uninstall_service(
        service_config, plist_path, runner=denied_runner, domain="gui/501"
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_bootout_error"
    assert plist_path.read_bytes() == _valid_plist_bytes(service_config)
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_uninstall_retains_proven_quarantine_without_physical_unlink(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    real_unlink = os.unlink

    def forbid_plist_unlink(
        path: os.PathLike[str] | str, *args: object, **kwargs: object
    ) -> None:
        if ".remove-" in str(path) and kwargs.get("dir_fd") is not None:
            raise AssertionError("proven quarantine must not be physically unlinked")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(service.os, "unlink", forbid_plist_unlink)
    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.REMOVED_QUARANTINED
    assert result.detail == "plist_quarantined"
    assert result.installed is False
    assert not plist_path.exists()
    assert list(tmp_path.glob(".service.plist.remove-*"))
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


def test_uninstall_replacement_race_preserves_new_leaf_and_returns_error(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    replacement = tmp_path / "replacement.plist"
    replacement.write_text("replacement", encoding="utf-8")

    def replace_after_bootout(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        if command[1] == "bootout":
            os.replace(replacement, plist_path)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=replace_after_bootout,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_replaced_after_bootout"
    assert result.installed is True
    assert plist_path.read_text(encoding="utf-8") == "replacement"


def test_uninstall_final_window_quarantines_and_preserves_replacement(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    attacker = tmp_path / "attacker.plist"
    real_quarantine_delete = service._quarantine_delete_admitted

    def swap_after_final_identity_check(
        *args: object, **kwargs: object
    ) -> service.RecoveryRecord | None:
        attacker.write_text("replacement", encoding="utf-8")
        attacker.chmod(0o600)
        os.replace(attacker, plist_path)
        return real_quarantine_delete(*args, **kwargs)

    monkeypatch.setattr(
        service, "_quarantine_delete_admitted", swap_after_final_identity_check
    )
    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_replaced_after_bootout"
    assert result.installed is True
    assert plist_path.read_text(encoding="utf-8") == "replacement"
    quarantined = list(tmp_path.glob(".service.plist.remove-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "replacement"


def test_uninstall_rejects_new_leaf_created_after_quarantine_rename(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    attacker = tmp_path / "attacker.plist"
    real_quarantine = service._quarantine_delete_admitted

    def inject_after_quarantine(
        *args: object, **kwargs: object
    ) -> service.RecoveryRecord | None:
        recovery_record = real_quarantine(*args, **kwargs)
        attacker.write_text("replacement", encoding="utf-8")
        attacker.chmod(0o600)
        os.replace(attacker, plist_path)
        return recovery_record

    monkeypatch.setattr(service, "_quarantine_delete_admitted", inject_after_quarantine)
    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "quarantine_recovery_changed"
    assert result.installed is True
    assert plist_path.read_text(encoding="utf-8") == "replacement"


def test_uninstall_after_proof_quarantine_swap_preserves_every_inode(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    attacker = tmp_path / "attacker.plist"
    preserved = tmp_path / "admitted-preserved"
    real_snapshot = service._snapshot_plist_at
    real_unlink = os.unlink
    quarantine_reads = 0

    def swap_after_first_quarantine_proof(
        directory: service._PlistDirectory,
    ) -> service._PlistSnapshot | None:
        nonlocal quarantine_reads
        if ".remove-" in directory.name:
            quarantine_reads += 1
            if quarantine_reads == 2:
                (tmp_path / directory.name).rename(preserved)
                attacker.write_text("foreign-replacement", encoding="utf-8")
                attacker.chmod(0o600)
                os.replace(attacker, tmp_path / directory.name)
        return real_snapshot(directory)

    monkeypatch.setattr(
        service, "_snapshot_plist_at", swap_after_first_quarantine_proof
    )
    monkeypatch.setattr(
        service.os,
        "unlink",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(f"quarantine unlink forbidden: {path}")
            )
            if ".remove-" in str(path)
            else real_unlink(path, *args, **kwargs)
        ),
    )
    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_replaced_after_bootout"
    assert preserved.read_bytes() == _valid_plist_bytes(service_config)
    quarantined = list(tmp_path.glob(".service.plist.remove-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "foreign-replacement"


def test_uninstall_after_helper_revalidates_recovery_inode_and_bytes(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    admitted_bytes = _valid_plist_bytes(service_config)
    plist_path.write_bytes(admitted_bytes)
    preserved = tmp_path / "admitted-preserved"
    real_quarantine = service._quarantine_delete_admitted

    def swap_recovery_after_helper(
        *args: object, **kwargs: object
    ) -> service.RecoveryRecord | None:
        recovery_record = real_quarantine(*args, **kwargs)
        assert recovery_record is not None
        recovery_path = tmp_path / recovery_record.leaf
        recovery_path.rename(preserved)
        recovery_path.write_text("foreign", encoding="utf-8")
        recovery_path.chmod(0o600)
        return recovery_record

    monkeypatch.setattr(
        service, "_quarantine_delete_admitted", swap_recovery_after_helper
    )
    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "quarantine_recovery_changed"
    _assert_identity_bearing_recovery_records(result, str(tmp_path))
    record = result.recovery_records[0]
    assert record.inode == preserved.stat().st_ino
    assert record.sha256 != service.hashlib.sha256(b"foreign").hexdigest()
    assert preserved.read_bytes() == admitted_bytes
    assert (
        list(tmp_path.glob(".service.plist.remove-*"))[0].read_text(encoding="utf-8")
        == "foreign"
    )


def test_uninstall_parent_swap_preserves_admitted_leaf_and_replacement_parent(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    launchagents = tmp_path / "LaunchAgents"
    launchagents.mkdir(mode=0o700)
    admitted_parent = tmp_path / "admitted-LaunchAgents"
    plist_path = launchagents / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def swap_parent_after_bootout(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        if command[1] == "bootout":
            launchagents.rename(admitted_parent)
            launchagents.mkdir(mode=0o700)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.uninstall_service(
        service_config,
        plist_path,
        runner=swap_parent_after_bootout,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_parent_changed"
    assert result.installed is True
    assert (admitted_parent / plist_path.name).read_bytes() == _valid_plist_bytes(
        service_config
    )
    assert not (launchagents / plist_path.name).exists()


def test_lifecycle_and_status_use_fake_subprocess_without_raw_output(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_text(
        service.render_launchd_plist(service_config), encoding="utf-8"
    )
    calls: list[list[str]] = []
    bootout_calls = 0
    bootstrap_calls = 0

    def fake_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootout_calls, bootstrap_calls
        calls.append(command)
        if command[1] == "bootout":
            bootout_calls += 1
        if command[1] == "bootstrap":
            bootstrap_calls += 1
        if command[1] == "print":
            if bootout_calls > bootstrap_calls:
                return subprocess.CompletedProcess(
                    command, 113, stdout="raw-secret", stderr="raw-log-secret"
                )
            registered = _valid_launchd_print(service_config, "gui/501")
            registered = registered.replace(
                "\tstate = running\n",
                "\tstate = running raw-secret\n\tpid = 4321\n",
                1,
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=registered,
                stderr="raw-log-secret",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    started = service.start_service(
        service_config, plist_path, runner=fake_runner, domain="gui/501"
    )
    assert started.state is service.ServiceState.LOADED
    assert started.loaded is True
    assert started.detail is None
    restarted = service.restart_service(
        service_config, plist_path, runner=fake_runner, domain="gui/501"
    )
    assert restarted.state is service.ServiceState.LOADED
    assert restarted.loaded is True
    assert restarted.detail is None
    status = service.service_status(
        service_config, plist_path, runner=fake_runner, domain="gui/501"
    )
    assert status.state is service.ServiceState.RUNNING
    assert status.pid == 4321
    encoded = json.dumps(status.as_dict(), sort_keys=True)
    assert "raw-secret" not in encoded
    assert "raw-log-secret" not in encoded
    assert (
        service.stop_service(
            service_config, plist_path, runner=fake_runner, domain="gui/501"
        ).state
        is service.ServiceState.STOPPED
    )
    assert (
        service.uninstall_service(
            service_config, plist_path, runner=fake_runner, domain="gui/501"
        ).state
        is service.ServiceState.REMOVED_QUARANTINED
    )
    assert not plist_path.exists()
    assert [call[1:3] for call in calls] == [
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["bootout", "gui/501/com.orchnext.hermes.serve"],
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["bootstrap", "gui/501"],
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["bootout", "gui/501/com.orchnext.hermes.serve"],
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["bootstrap", "gui/501"],
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["bootout", "gui/501/com.orchnext.hermes.serve"],
        ["print", "gui/501/com.orchnext.hermes.serve"],
        ["bootout", "gui/501/com.orchnext.hermes.serve"],
    ]


def test_stop_waits_for_launchd_to_report_terminal_absence(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[list[str]] = []
    print_calls = 0
    monkeypatch.setattr(service, "STOP_CONFIRM_INTERVAL_SECONDS", 0.0)

    def delayed_absence(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls
        calls.append(command)
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=_valid_launchd_print(service_config, "gui/501"),
                    stderr="",
                )
            return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.stop_service(
        service_config,
        plist_path,
        runner=delayed_absence,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.STOPPED
    assert result.loaded is False
    assert [call[1] for call in calls] == ["bootout", "print", "print"]


def test_stop_fails_closed_when_launchd_remains_loaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    monkeypatch.setattr(service, "STOP_CONFIRM_ATTEMPTS", 2)
    monkeypatch.setattr(service, "STOP_CONFIRM_INTERVAL_SECONDS", 0.0)

    def still_loaded(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        output = (
            _valid_launchd_print(service_config, "gui/501")
            if command[1] == "print"
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="secret")

    result = service.stop_service(
        service_config,
        plist_path,
        runner=still_loaded,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is True
    assert result.detail == "stop_confirmation_still_loaded_disabled"
    assert "secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_stop_disables_before_bootout_and_blocks_btm_reregistration(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[str] = []
    disabled = False
    loaded = True
    btm_attempted = False

    def controlled_launchctl(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal disabled, loaded, btm_attempted
        operation = arguments[0]
        calls.append(operation)
        if operation == "disable":
            disabled = True
        elif operation == "print-disabled":
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=_disabled_services_print(service_config, disabled=disabled),
                stderr="",
            )
        elif operation == "bootout":
            loaded = False
        elif operation == "print":
            btm_attempted = True
            if not disabled:
                loaded = True
            return subprocess.CompletedProcess(
                arguments,
                0 if loaded else 113,
                stdout=(
                    _valid_launchd_print(service_config, "gui/501") if loaded else ""
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr(service, "_run_launchctl", controlled_launchctl)
    result = service.stop_service(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: None,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.STOPPED
    assert result.detail == "service_disabled"
    assert disabled is True
    assert loaded is False
    assert btm_attempted is True
    assert plist_path.exists()
    assert calls == [
        "disable",
        "print-disabled",
        "bootout",
        "print",
        "print-disabled",
    ]


def test_stop_disable_failure_never_boots_out(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[str] = []

    def disable_denied(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments[0])
        return subprocess.CompletedProcess(arguments, 77, stdout="raw", stderr="secret")

    monkeypatch.setattr(service, "_run_launchctl", disable_denied)
    result = service.stop_service(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: None,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_disable_error"
    assert calls == ["disable"]
    assert "secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_stop_bootout_failure_retains_verified_disabled_hold(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    disabled = False

    def bootout_denied(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal disabled
        operation = arguments[0]
        if operation == "disable":
            disabled = True
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        if operation == "print-disabled":
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=_disabled_services_print(service_config, disabled=disabled),
                stderr="",
            )
        assert operation == "bootout"
        return subprocess.CompletedProcess(arguments, 77, stdout="", stderr="secret")

    monkeypatch.setattr(service, "_run_launchctl", bootout_denied)
    result = service.stop_service(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: None,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is True
    assert result.detail == "launchctl_bootout_error_disabled"
    assert disabled is True


def test_stop_rejects_print_disabled_mismatch_before_bootout(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[str] = []

    def mismatch(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments[0])
        output = (
            _disabled_services_print(service_config, disabled=False)
            if arguments[0] == "print-disabled"
            else ""
        )
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(service, "_run_launchctl", mismatch)
    result = service.stop_service(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: None,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_disabled_state_mismatch"
    assert calls == ["disable", "print-disabled"]


def test_failed_start_restores_prior_disabled_hold(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    disabled = True
    calls: list[str] = []

    def failed_start(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal disabled
        operation = arguments[0]
        calls.append(operation)
        if operation == "print-disabled":
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=_disabled_services_print(service_config, disabled=disabled),
                stderr="",
            )
        if operation == "enable":
            disabled = False
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        if operation == "disable":
            disabled = True
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        if operation == "bootstrap":
            return subprocess.CompletedProcess(
                arguments, 77, stdout="", stderr="secret"
            )
        if operation == "print":
            return subprocess.CompletedProcess(arguments, 113, stdout="", stderr="")
        if operation == "bootout":
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected operation: {operation}")

    monkeypatch.setattr(service, "_run_launchctl", failed_start)
    result = service.start_service(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: None,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "fallback_launchctl_error"
    assert disabled is True
    assert "enable" in calls
    assert "disable" in calls
    assert calls[-1] == "print-disabled"


def test_failed_start_restores_hold_after_initial_containment_bootout_exception(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    disabled = True
    containment_bootouts = 0

    def transient_containment_failure(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal disabled, containment_bootouts
        operation = arguments[0]
        if operation == "print-disabled":
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=_disabled_services_print(service_config, disabled=disabled),
                stderr="",
            )
        if operation == "enable":
            disabled = False
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        if operation == "disable":
            disabled = True
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        if operation == "bootstrap":
            return subprocess.CompletedProcess(arguments, 77, stdout="", stderr="")
        if operation == "print":
            return subprocess.CompletedProcess(arguments, 113, stdout="", stderr="")
        if operation == "bootout":
            containment_bootouts += 1
            if containment_bootouts == 1:
                raise subprocess.TimeoutExpired(arguments, 30)
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected operation: {operation}")

    monkeypatch.setattr(
        service,
        "_run_launchctl",
        transient_containment_failure,
    )
    result = service.start_service(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: None,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "fallback_launchctl_error_containment_unavailable"
    assert containment_bootouts == 2
    assert disabled is True


def test_durable_hold_stop_rejects_a_second_writer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    service.ensure_private_directories(service_config)
    lock_path = service_config.state_dir / "lifecycle.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = service.stop_service(
            service_config,
            plist_path,
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("second writer must not invoke launchctl")
            ),
            domain="gui/501",
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_busy"


def test_status_distinguishes_verified_disabled_hold_from_transient_absence(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def disabled_and_absent(
        _runner: service.Runner,
        arguments: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "print-disabled":
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=_disabled_services_print(service_config, disabled=True),
                stderr="",
            )
        assert arguments[0] == "print"
        return subprocess.CompletedProcess(arguments, 113, stdout="", stderr="")

    monkeypatch.setattr(service, "_run_launchctl", disabled_and_absent)
    result = service.service_status(
        service_config,
        plist_path,
        runner=lambda *_args, **_kwargs: None,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.STOPPED
    assert result.loaded is False
    assert result.detail == "service_disabled"


@pytest.mark.parametrize("not_loaded_returncode", [3, 113])
def test_restart_bootstraps_installed_plist_when_job_is_unloaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    not_loaded_returncode: int,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_text(
        service.render_launchd_plist(service_config), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def unloaded_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return_code = not_loaded_returncode if len(calls) == 1 else 0
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    result = service.restart_service(
        service_config,
        plist_path,
        runner=unloaded_runner,
        domain="user/501",
    )

    assert result.state is service.ServiceState.LOADED
    _assert_identity_bearing_recovery_records(result, str(tmp_path))
    assert calls == [
        [service.LAUNCHCTL_PATH, "print", "user/501/com.orchnext.hermes.serve"],
        [service.LAUNCHCTL_PATH, "bootstrap", "user/501", calls[1][-1]],
        [service.LAUNCHCTL_PATH, "print", "user/501/com.orchnext.hermes.serve"],
    ]
    assert calls[1][-1].startswith(
        str(plist_path.parent / ".service.plist.bootstrap-consume-")
    )
    assert calls[1][-1] != str(plist_path)
    assert not Path(calls[1][-1]).exists()


@pytest.mark.parametrize("action", ["start", "restart"])
def test_foreign_registered_definition_rejects_before_mutation(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[list[str]] = []

    def foreign_registration(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        foreign = _valid_launchd_print(service_config, "user/501").replace(
            str(service_config.python), "/tmp/foreign", 1
        )
        return subprocess.CompletedProcess(command, 0, stdout=foreign, stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=foreign_registration,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert result.detail == "registered_definition_mismatch"
    assert calls == [
        [service.LAUNCHCTL_PATH, "print", "user/501/com.orchnext.hermes.serve"]
    ]


@pytest.mark.parametrize("action", ["start", "restart"])
def test_ambiguous_registered_status_rejects_without_mutation(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[list[str]] = []

    def ambiguous_status(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 125, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=ambiguous_status,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert result.detail == "registered_definition_status_error"
    assert calls == [
        [service.LAUNCHCTL_PATH, "print", "user/501/com.orchnext.hermes.serve"]
    ]


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize("swap_phase", ["bootstrap", "print"])
def test_fallback_bootstrap_revalidates_exact_definition_before_loaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
    swap_phase: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    attacker = tmp_path / "attacker.plist"
    print_calls = 0
    bootout_calls = 0
    swapped = False

    def swap_fallback_definition(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls, bootout_calls, swapped
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        if command[1] == "bootout":
            bootout_calls += 1
        should_swap = command[1] == swap_phase and (
            swap_phase != "print" or print_calls > 1
        )
        if should_swap and not swapped:
            attacker.write_text("attacker", encoding="utf-8")
            attacker.chmod(0o600)
            os.replace(attacker, plist_path)
            swapped = True
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=swap_fallback_definition,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "postbootstrap_identity_error"
    assert result.loaded is False
    assert result.installed is True
    assert bootout_calls == 1
    assert plist_path.read_text(encoding="utf-8") == "attacker"


@pytest.mark.parametrize("action", ["start", "restart"])
def test_private_stage_bootstrap_never_activates_foreign_canonical_replacement(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    foreign = plistlib.loads(_valid_plist_bytes(service_config))
    foreign["Label"] = "com.foreign.service"
    active_labels: set[str] = set()
    calls: list[list[str]] = []
    print_calls = 0

    def boundary_runner(
        command: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls
        calls.append(command)
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        elif command[1] == "bootstrap":
            stage_path = Path(command[-1])
            assert stage_path.name.startswith(".service.plist.bootstrap-consume-")
            assert stage_path != plist_path
            assert "pass_fds" not in options
            assert stage_path.stat().st_mode & 0o777 == service.BOOTSTRAP_STAGE_MODE
            with stage_path.open("rb") as staged:
                activated = plistlib.loads(staged.read())
            active_labels.add(activated["Label"])
            plist_path.write_bytes(plistlib.dumps(foreign, sort_keys=True))
        elif command[1] == "bootout":
            active_labels.discard(command[-1].split("/", 2)[-1])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=boundary_runner,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "postbootstrap_identity_error"
    assert active_labels == set()
    assert "com.foreign.service" not in active_labels
    assert [call[1] for call in calls] == ["print", "bootstrap", "print", "bootout"]
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


@pytest.mark.parametrize("action", ["start", "restart"])
def test_foreign_recovery_path_mutation_cannot_change_private_stage_bytes(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    foreign = plistlib.loads(_valid_plist_bytes(service_config))
    foreign["Label"] = "com.foreign.service"
    active_labels: set[str] = set()
    print_calls = 0

    def mutate_recovery_path(
        command: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        elif command[1] == "bootstrap":
            recovery_paths = list(tmp_path.glob(".service.plist.bootstrap-recovery-*"))
            assert len(recovery_paths) == 1
            recovery_paths[0].chmod(0o600)
            recovery_paths[0].write_bytes(plistlib.dumps(foreign, sort_keys=True))
            assert "pass_fds" not in options
            stage_path = Path(command[-1])
            assert stage_path.name.startswith(".service.plist.bootstrap-consume-")
            with stage_path.open("rb") as staged:
                activated = plistlib.loads(staged.read())
            active_labels.add(activated["Label"])
        elif command[1] == "bootout":
            active_labels.discard(command[-1].split("/", 2)[-1])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=mutate_recovery_path,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "bootstrap_stage_changed"
    assert active_labels == set()
    assert "com.foreign.service" not in active_labels
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


@pytest.mark.parametrize("bootstrap_returncode", [5, 66])
def test_private_stage_bootstrap_unavailable_never_falls_back_to_canonical_path(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    bootstrap_returncode: int,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[list[str]] = []

    def descriptor_unavailable(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1] == "print":
            return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        if command[1] == "bootstrap":
            return subprocess.CompletedProcess(
                command, bootstrap_returncode, stdout="", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.start_service(
        service_config,
        plist_path,
        runner=descriptor_unavailable,
        domain="user/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "fallback_launchctl_unavailable"
    bootstrap_paths = [call[-1] for call in calls if call[1] == "bootstrap"]
    assert len(bootstrap_paths) == (2 if bootstrap_returncode == 5 else 1)
    assert all(
        path.startswith(str(plist_path.parent / ".service.plist.bootstrap-consume-"))
        for path in bootstrap_paths
    )
    assert str(plist_path) not in bootstrap_paths
    assert all(not Path(path).exists() for path in bootstrap_paths)
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


@pytest.mark.parametrize(
    "bootstrap_family", ["install", "rollback", "start", "restart"]
)
def test_path_backed_bootstrap_needs_no_pass_fds_for_every_bootstrap_family(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    bootstrap_family: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    if bootstrap_family != "install":
        plist_path.write_bytes(_valid_plist_bytes(service_config))
    bootstrap_paths: list[str] = []
    bootstrap_calls = 0
    print_calls = 0

    def no_descriptor_inheritance(
        command: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_calls, print_calls
        if command[1] == "print" and bootstrap_family in {"start", "restart"}:
            print_calls += 1
            return_code = 113 if print_calls == 1 else 0
            return subprocess.CompletedProcess(
                command, return_code, stdout="", stderr=""
            )
        if command[1] == "bootstrap":
            bootstrap_calls += 1
            bootstrap_paths.append(command[-1])
            assert "pass_fds" not in options
            stage_path = Path(command[-1])
            assert stage_path.read_bytes() == _valid_plist_bytes(service_config)
            assert stage_path.stat().st_mode & 0o777 == service.BOOTSTRAP_STAGE_MODE
            if bootstrap_family == "rollback" and bootstrap_calls == 1:
                return subprocess.CompletedProcess(command, 77, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    if bootstrap_family in {"install", "rollback"}:
        result = service.install_service(
            service_config,
            plist_path,
            runner=no_descriptor_inheritance,
            domain="user/501",
        )
    else:
        lifecycle = (
            service.start_service
            if bootstrap_family == "start"
            else service.restart_service
        )
        result = lifecycle(
            service_config,
            plist_path,
            runner=no_descriptor_inheritance,
            domain="user/501",
        )

    if bootstrap_family == "rollback":
        assert result.state is service.ServiceState.ERROR
        assert result.loaded is True
        assert result.detail == "launchctl_bootstrap_error_rolled_back"
    elif bootstrap_family == "install":
        assert result.state is service.ServiceState.INSTALLED
        assert result.loaded is True
    else:
        assert result.state is service.ServiceState.LOADED
        assert result.loaded is True
    assert bootstrap_paths
    assert all(
        path.startswith(str(plist_path.parent / ".service.plist.bootstrap-consume-"))
        for path in bootstrap_paths
    )
    assert str(plist_path) not in bootstrap_paths
    assert all(not Path(path).exists() for path in bootstrap_paths)
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


def test_foreign_private_stage_replacement_is_contained_and_retained(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    foreign_stage: Path | None = None

    def replace_private_stage(
        command: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal foreign_stage
        if command[1] == "print":
            return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        if command[1] == "bootstrap":
            assert "pass_fds" not in options
            foreign_stage = Path(command[-1])
            foreign_stage.unlink()
            foreign_stage.write_text("foreign", encoding="utf-8")
            foreign_stage.chmod(service.BOOTSTRAP_STAGE_MODE)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.start_service(
        service_config,
        plist_path,
        runner=replace_private_stage,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "bootstrap_stage_changed"
    assert result.loaded is False
    assert foreign_stage is not None
    assert foreign_stage.read_text(encoding="utf-8") == "foreign"
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


def test_swap_consume_restore_cannot_produce_false_loaded_result(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    foreign_runtime = "/tmp/foreign-runtime"
    consumed_arguments: list[str] = []
    print_calls = 0
    kickstarts = 0
    bootouts = 0

    def swap_consume_restore(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls, kickstarts, bootouts
        if command[1] == "kickstart":
            kickstarts += 1
            return_code = 113 if kickstarts == 1 else 0
            return subprocess.CompletedProcess(
                command, return_code, stdout="", stderr=""
            )
        if command[1] == "bootstrap":
            stage = Path(command[-1])
            saved = stage.with_name(stage.name + ".saved")
            os.replace(stage, saved)
            foreign = plistlib.loads(_valid_plist_bytes(service_config))
            foreign["ProgramArguments"][1] = foreign_runtime
            stage.write_bytes(plistlib.dumps(foreign, sort_keys=True))
            stage.chmod(service.BOOTSTRAP_STAGE_MODE)
            consumed_arguments.extend(
                plistlib.loads(stage.read_bytes())["ProgramArguments"]
            )
            stage.unlink()
            os.replace(saved, stage)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
            foreign_print = _valid_launchd_print(service_config).replace(
                str(service_config.runtime), foreign_runtime, 1
            )
            return subprocess.CompletedProcess(
                command, 0, stdout=foreign_print, stderr=""
            )
        if command[1] == "bootout":
            bootouts += 1
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = service.start_service(
        service_config,
        plist_path,
        runner=swap_consume_restore,
        domain="user/501",
    )

    assert foreign_runtime in consumed_arguments
    assert result.state is service.ServiceState.ERROR
    assert result.detail == "fallback_launchctl_error"
    assert result.loaded is False
    assert kickstarts == 0
    assert bootouts == 1
    assert plist_path.read_bytes() == _valid_plist_bytes(service_config)


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_label",
        "wrong_program_arguments",
        "wrong_environment",
        "wrong_working_directory",
    ],
)
def test_direct_lifecycle_never_kickstarts_noncanonical_definition(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
    mutation: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    parsed = plistlib.loads(_valid_plist_bytes(service_config))
    if mutation == "wrong_label":
        parsed["Label"] = "com.foreign.service"
    elif mutation == "wrong_program_arguments":
        parsed["ProgramArguments"] = ["/tmp/foreign-runtime"]
    elif mutation == "wrong_environment":
        parsed["EnvironmentVariables"] = {"HERMES_HOME": "/tmp/foreign-home"}
    else:
        parsed["WorkingDirectory"] = "/tmp/foreign-worktree"
    plist_path.write_bytes(plistlib.dumps(parsed, sort_keys=True))
    calls: list[list[str]] = []

    def unloaded_then_contain(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return_code = 113 if len(calls) == 1 else 0
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=unloaded_then_contain,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "plist_definition_mismatch"
    assert calls == []


@pytest.mark.parametrize("action", ["start", "restart"])
def test_exact_registered_definition_reactivates_without_label_kick(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[list[str]] = []
    bootout_seen = False
    bootstrap_seen = False

    def exact_registration(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal bootout_seen, bootstrap_seen
        calls.append(command)
        if command[1] == "bootout":
            bootout_seen = True
        elif command[1] == "bootstrap":
            bootstrap_seen = True
        if command[1] == "print" and bootout_seen and not bootstrap_seen:
            return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        output = (
            _valid_launchd_print(service_config, "user/501")
            if command[1] == "print"
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=exact_registration,
        domain="user/501",
    )

    assert result.state is service.ServiceState.LOADED
    assert result.detail is None
    assert result.loaded is True
    assert result.installed is True
    assert [call[1] for call in calls] == [
        "print",
        "bootout",
        "print",
        "bootstrap",
        "print",
    ]
    assert all(call[1] != "kickstart" for call in calls)
    _assert_identity_bearing_recovery_records(result, str(tmp_path))


@pytest.mark.parametrize("action", ["start", "restart"])
def test_reactivation_waits_for_bootout_fixed_point_before_bootstrap(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[list[str]] = []
    post_bootout_prints = 0
    bootstrap_seen = False
    monkeypatch.setattr(service, "STOP_CONFIRM_INTERVAL_SECONDS", 0.0)

    def delayed_bootout(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal post_bootout_prints, bootstrap_seen
        calls.append(command)
        if command[1] == "bootstrap":
            bootstrap_seen = True
        if command[1] == "print":
            bootout_seen = any(call[1] == "bootout" for call in calls)
            if bootout_seen and not bootstrap_seen:
                post_bootout_prints += 1
                if post_bootout_prints == 1:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=_valid_launchd_print(service_config, "user/501"),
                        stderr="",
                    )
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_valid_launchd_print(service_config, "user/501"),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=delayed_bootout,
        domain="user/501",
    )

    assert result.state is service.ServiceState.LOADED
    assert [call[1] for call in calls] == [
        "print",
        "bootout",
        "print",
        "print",
        "bootstrap",
        "print",
    ]


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize("failure_phase", ["bootstrap", "print"])
def test_fallback_failure_after_bootstrap_is_contained(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
    failure_phase: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    print_calls = 0
    bootout_calls = 0

    def failing_fallback(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls, bootout_calls
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        if command[1] == "bootout":
            bootout_calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        should_fail = command[1] == failure_phase
        return subprocess.CompletedProcess(
            command, 77 if should_fail else 0, stdout="", stderr=""
        )

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=failing_fallback,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "fallback_launchctl_error"
    assert result.loaded is False
    assert bootout_calls == 1


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize("post_bootout_status", [0, 77])
def test_stale_registration_without_terminal_absence_cannot_report_loaded(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    post_bootout_status: int,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[list[str]] = []
    print_calls = 0
    bootstrap_calls = 0
    monkeypatch.setattr(service, "STOP_CONFIRM_ATTEMPTS", 3)
    monkeypatch.setattr(service, "STOP_CONFIRM_INTERVAL_SECONDS", 0.0)

    def stale_registration(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls, bootstrap_calls
        calls.append(command)
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
            return subprocess.CompletedProcess(
                command,
                post_bootout_status,
                stdout=(
                    _valid_launchd_print(service_config, "user/501")
                    if post_bootout_status == 0
                    else ""
                ),
                stderr="",
            )
        if command[1] == "bootstrap":
            bootstrap_calls += 1
            return subprocess.CompletedProcess(command, 5, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=stale_registration,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "fallback_launchctl_error"
    assert result.loaded is False
    assert bootstrap_calls == 1
    assert [call[1] for call in calls].count("bootstrap") == 1


@pytest.mark.parametrize("action", ["start", "restart"])
def test_fallback_timeout_after_bootstrap_attempts_containment(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    print_calls = 0
    bootout_calls = 0

    def timeout_after_bootstrap(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls, bootout_calls
        if command[1] == "print":
            print_calls += 1
            if print_calls == 1:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
            raise subprocess.TimeoutExpired(command, 30)
        if command[1] == "bootout":
            bootout_calls += 1
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    lifecycle = service.start_service if action == "start" else service.restart_service
    result = lifecycle(
        service_config,
        plist_path,
        runner=timeout_after_bootstrap,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "fallback_launchctl_timeout"
    assert bootout_calls == 1


@pytest.mark.parametrize("not_loaded_returncode", [3, 113])
def test_status_maps_only_known_not_loaded_codes_to_stopped(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    not_loaded_returncode: int,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def stopped_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, not_loaded_returncode, stdout="raw-secret", stderr="raw-secret"
        )

    result = service.service_status(
        service_config,
        plist_path,
        runner=stopped_runner,
        domain="user/501",
    )

    assert result.state is service.ServiceState.STOPPED
    assert result.detail == "service_not_loaded"
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_status_permission_error_is_not_reported_as_stopped(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def denied_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 77, stdout="credential=raw-secret", stderr="permission raw-secret"
        )

    result = service.service_status(
        service_config,
        plist_path,
        runner=denied_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_status_error"
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_status_125_is_ambiguous_error_not_stopped(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def ambiguous_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 125, stdout="", stderr="")

    result = service.service_status(
        service_config,
        plist_path,
        runner=ambiguous_runner,
        domain="user/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "launchctl_status_error"


def test_status_pid_zero_is_loaded_not_running(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def zero_pid_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        registered = _valid_launchd_print(service_config, "gui/501").replace(
            "\tstate = running\n", "\tstate = waiting\n\tpid = 0\n", 1
        )
        return subprocess.CompletedProcess(command, 0, stdout=registered, stderr="")

    result = service.service_status(
        service_config,
        plist_path,
        runner=zero_pid_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.LOADED
    assert result.loaded is True
    assert result.pid is None


def test_status_rejects_foreign_registered_definition_without_mutation(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    calls: list[str] = []

    def foreign_status(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command[1])
        foreign = _valid_launchd_print(service_config, "gui/501").replace(
            str(service_config.python), "/tmp/foreign", 1
        )
        foreign = foreign.replace(
            "\tstate = running\n", "\tstate = waiting\n\tpid = 0\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=foreign, stderr="")

    result = service.service_status(
        service_config,
        plist_path,
        runner=foreign_status,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert result.detail == "launchctl_definition_mismatch"
    assert calls == ["print"]


def test_status_rejects_foreign_persisted_definition_before_launchctl(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_text("foreign but owner-only", encoding="utf-8")
    calls: list[str] = []

    def exact_registered_status(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command[1])
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_valid_launchd_print(service_config, "gui/501"),
            stderr="",
        )

    result = service.service_status(
        service_config,
        plist_path,
        runner=exact_registered_status,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.installed is True
    assert result.loaded is False
    assert result.detail == "plist_definition_mismatch"
    assert calls == []


def test_status_rejects_persisted_definition_swap_during_print(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    attacker = tmp_path / "attacker.plist"
    calls: list[str] = []

    def swap_during_print(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command[1])
        attacker.write_text("foreign but owner-only", encoding="utf-8")
        attacker.chmod(0o600)
        os.replace(attacker, plist_path)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_valid_launchd_print(service_config, "gui/501"),
            stderr="",
        )

    result = service.service_status(
        service_config,
        plist_path,
        runner=swap_during_print,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.ERROR
    assert result.loaded is False
    assert result.detail == "plist_definition_changed"
    assert calls == ["print"]
    assert plist_path.read_text(encoding="utf-8") == "foreign but owner-only"


@pytest.mark.parametrize(
    "action",
    ["install", "uninstall", "start", "stop", "restart", "status"],
)
def test_unqualified_launchctl_prevents_every_lifecycle_call(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    plist_path = tmp_path / "service.plist"
    content = _valid_plist_bytes(service_config)
    plist_path.write_bytes(content)
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: False)

    def forbidden_runner(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("unqualified launchctl must not be called")

    operation = {
        "install": service.install_service,
        "uninstall": service.uninstall_service,
        "start": service.start_service,
        "stop": service.stop_service,
        "restart": service.restart_service,
        "status": service.service_status,
    }[action]
    result = operation(
        service_config,
        plist_path,
        runner=forbidden_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.loaded is False
    assert result.detail == "launchctl_binary_unqualified"
    assert plist_path.read_bytes() == content


def test_status_unrelated_positive_number_is_not_a_running_pid(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def no_pid_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        registered = _valid_launchd_print(service_config, "gui/501").replace(
            "\tactive count = 1\n", "\tactive count = 123\n", 1
        )
        return subprocess.CompletedProcess(command, 0, stdout=registered, stderr="")

    result = service.service_status(
        service_config,
        plist_path,
        runner=no_pid_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.LOADED
    assert result.pid is None


def test_status_nested_coalition_pid_is_not_job_pid(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))

    def nested_pid_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        registered = _valid_launchd_print(service_config, "gui/501").replace(
            "\t\tID = 123\n", "\t\tpid = 4242\n", 1
        )
        return subprocess.CompletedProcess(command, 0, stdout=registered, stderr="")

    result = service.service_status(
        service_config,
        plist_path,
        runner=nested_pid_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.LOADED
    assert result.loaded is True
    assert result.pid is None


def test_dry_run_has_no_file_or_subprocess_effects(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "not-written.plist"

    def forbidden_runner(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("dry-run must not invoke launchctl")

    result = service.install_service(
        service_config,
        plist_path,
        runner=forbidden_runner,
        dry_run=True,
    )

    assert result.state is service.ServiceState.PLANNED
    assert result.detail == "install_dry_run"
    assert not plist_path.exists()
    assert not service_config.service_root.exists()


def test_unavailable_status_is_typed_and_sanitized(
    service_config: service.ServiceConfig,
    tmp_path: Path,
) -> None:
    plist_path = tmp_path / "service.plist"
    plist_path.write_text(
        service.render_launchd_plist(service_config), encoding="utf-8"
    )

    def missing_runner(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("launchctl missing near raw-secret")

    result = service.service_status(
        service_config, plist_path, runner=missing_runner, domain="gui/501"
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "launchctl_not_found"
    assert "raw-secret" not in json.dumps(result.as_dict(), sort_keys=True)


def test_session_source_creation_requires_an_explicit_consumed_authority(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing source is not created merely because install was requested."""

    from scripts import orch_next_hermes_session_token_source as source

    created: list[bool] = []
    consumed: list[tuple[object, bool]] = []
    ready_results = iter((False, False, True))
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(
        service,
        "_session_token_source_ready",
        lambda _config: next(ready_results),
    )
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        service,
        "_consume_session_token_authority",
        lambda _config, context, *, rotate: (
            consumed.append((context, rotate))
            or context == _session_authority_context("decision-create")
        ),
    )
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _config: _session_runtime_identity(),
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda _home, *, rotate: created.append(rotate),
    )

    assert not service._prepare_session_token_source(service_config)
    assert created == []
    assert consumed == [(None, False)]

    assert service._prepare_session_token_source(
        service_config,
        authority_context=_session_authority_context("decision-create"),
    )
    assert created == [False]
    assert consumed[-1] == (_session_authority_context("decision-create"), False)


def test_session_source_rotate_always_consumes_fresh_authority(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-ready source does not make rotate an implicit privilege."""

    from scripts import orch_next_hermes_session_token_source as source

    created: list[bool] = []
    consumed: list[bool] = []
    ready_results = iter((True, True))
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(
        service,
        "_session_token_source_ready",
        lambda _config: next(ready_results),
    )
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        service,
        "_consume_session_token_authority",
        lambda _config, context, *, rotate: (
            consumed.append(rotate)
            or context == _session_authority_context("decision-rotate")
        ),
    )
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _config: _session_runtime_identity(),
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda _home, *, rotate: created.append(rotate),
    )

    assert not service._prepare_session_token_source(
        service_config,
        authority_context=None,
        rotate=True,
    )
    assert created == []
    assert consumed == [True]

    assert service._prepare_session_token_source(
        service_config,
        authority_context=_session_authority_context("decision-rotate"),
        rotate=True,
    )
    assert created == [True]


def test_session_source_authority_uses_target_bound_existing_consumer(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service creation accepts only the existing exact one-use allow shape."""

    from tui_gateway import maestro_authority

    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _c: _session_runtime_identity(),
    )

    def exact_allow(_context: object, actual: dict[str, object]) -> dict[str, object]:
        captured.append(actual)
        return _session_authority_allow()

    monkeypatch.setattr(
        maestro_authority, "consume_maestro_authority_decision", exact_allow
    )
    assert service._consume_session_token_authority(
        service_config,
        _session_authority_context(),
        rotate=False,
    )
    assert captured == [
        {
            "logical_session_id": service._session_token_logical_id(service_config),
            "ui_session_id": "orch-next-session-token-create",
            "method": "prompt.submit",
            "target": "hermes",
            "runtime_revision": "a" * 40,
        }
    ]

    monkeypatch.setattr(
        maestro_authority,
        "consume_maestro_authority_decision",
        lambda *_args: {**_session_authority_allow(), "extra": "not-admitted"},
    )
    assert not service._consume_session_token_authority(
        service_config,
        _session_authority_context(),
        rotate=True,
    )


def test_session_token_install_context_derives_target_and_head_without_private_ids(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tui_gateway import maestro_authority

    captured: list[dict[str, object]] = []
    expected = _session_authority_context("fresh-request")
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _c: _session_runtime_identity(),
    )

    def build(**kwargs: object) -> object:
        captured.append(kwargs)
        return expected

    monkeypatch.setattr(
        maestro_authority,
        "build_session_token_install_authority_request",
        build,
    )

    assert service._session_token_install_authority_context(service_config) is expected
    assert captured == [
        {
            "logical_session_id": service._session_token_logical_id(service_config),
            "runtime_revision": "a" * 40,
        }
    ]
    serialized = json.dumps(captured, sort_keys=True)
    assert str(service_config.hermes_home) not in serialized
    assert str(service_config.worktree) not in serialized
    assert str(service_config.runtime) not in serialized
    assert str(service_config.python) not in serialized


@pytest.mark.parametrize(
    ("status_output", "status_returncode"),
    [
        (b" M scripts/orch_next_hermes_serve_service.py\0", 0),
        (b"M  scripts/orch_next_hermes_serve_service.py\0", 0),
        (b"?? local-authority-substitution.py\0", 0),
        (
            b" M distribution/orch-next-hermes-harness/SOURCE_MANIFEST.json\0",
            0,
        ),
        (b"", 1),
    ],
    ids=["tracked", "staged", "untracked", "same-shape-manifest", "git-error"],
)
def test_install_request_fails_before_transport_for_any_unclean_git_state(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
    status_output: bytes,
    status_returncode: int,
) -> None:
    """The request boundary is NUL-safe and admits no dirty Git category."""

    from scripts import orch_next_hermes_mcp_launcher as launcher
    from tui_gateway import maestro_authority

    commands: list[tuple[str, ...]] = []

    def git_run(arguments: list[str], **_kwargs: object):
        commands.append(tuple(arguments))
        operation = arguments[3]
        if operation == "status":
            return subprocess.CompletedProcess(
                arguments,
                status_returncode,
                stdout=status_output,
                stderr=b"private-git-diagnostic",
            )
        if arguments[-1] == "--show-toplevel":
            output = f"{service_config.worktree}\n".encode()
        else:
            output = ("a" * 40 + "\n").encode()
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr=b"")

    monkeypatch.setattr(service.subprocess, "run", git_run)
    monkeypatch.setattr(
        launcher,
        "verified_lifecycle_runtime_provenance",
        lambda *_args, **_kwargs: pytest.fail(
            "dirty source must fail before portable locator consumption"
        ),
    )
    monkeypatch.setattr(
        maestro_authority,
        "build_session_token_install_authority_request",
        lambda **_kwargs: pytest.fail("dirty source must not reach transport"),
    )

    assert service._session_token_install_authority_context(service_config) is None
    assert any("--porcelain=v1" in command for command in commands)
    assert any("-z" in command for command in commands)
    assert any("--untracked-files=all" in command for command in commands)


def test_session_token_runtime_identity_reuses_admitted_portable_locator(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_mcp_launcher as launcher

    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        service,
        "_session_token_runtime_revision",
        lambda _config: "a" * 40,
    )
    monkeypatch.setattr(
        launcher,
        "verified_lifecycle_runtime_provenance",
        lambda bundle, *, expected_source_root: (
            calls.append((bundle, expected_source_root))
            or _session_authority_provenance()
        ),
    )

    assert (
        service._session_token_runtime_identity(service_config)
        == _session_runtime_identity()
    )
    assert calls == [
        (
            service_config.worktree / "distribution" / "orch-next-hermes-harness",
            service_config.worktree,
        )
    ]


@pytest.mark.parametrize(
    "mutation",
    ["upstream", "content", "digest"],
)
def test_session_source_authority_rejects_nonexact_runtime_provenance(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from tui_gateway import maestro_authority

    expected = _session_authority_provenance()
    result = _session_authority_allow()
    manifest = dict(result["runtime_provenance_manifest"])
    if mutation == "upstream":
        manifest["upstreamCommit"] = "5" * 40
        result["runtime_provenance_manifest"] = manifest
        result["runtime_provenance_manifest_digest"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
    elif mutation == "content":
        manifest["runtimeContentDigest"] = "6" * 64
        result["runtime_provenance_manifest"] = manifest
        result["runtime_provenance_manifest_digest"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
    else:
        result["runtime_provenance_manifest_digest"] = "7" * 64
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _c: ("a" * 40, *expected),
    )
    monkeypatch.setattr(
        maestro_authority,
        "consume_maestro_authority_decision",
        lambda *_args: result,
    )

    assert not service._consume_session_token_authority(
        service_config,
        _session_authority_context(),
        rotate=False,
    )


@pytest.mark.parametrize(
    ("case", "context", "consumer_result", "expected_consumer_calls"),
    [
        ("missing", None, None, 0),
        ("malformed", {"decision_binding": {}}, None, 0),
        (
            "stale",
            _session_authority_context("decision-stale"),
            {"outcome": "deny", "code": "authority_stale"},
            1,
        ),
        (
            "foreign",
            _session_authority_context("decision-foreign"),
            {"outcome": "deny", "code": "authority_mismatch"},
            1,
        ),
        (
            "deny",
            _session_authority_context("decision-deny"),
            {"outcome": "deny", "code": "authority_denied"},
            1,
        ),
        (
            "replay",
            _session_authority_context("decision-replay"),
            {"outcome": "deny", "code": "authority_replay"},
            1,
        ),
    ],
)
def test_install_authority_failure_never_calls_token_writer_and_keeps_hold(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    context: object,
    consumer_result: object,
    expected_consumer_calls: int,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source
    from tui_gateway import maestro_authority

    writer_calls: list[bool] = []
    consumer_calls: list[object] = []
    containment_calls: list[str] = []
    plist_path = tmp_path / f"{case}.plist"
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _c: False)
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _c: _session_runtime_identity(),
    )
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "prepare_protected_command_config",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda _home, *, rotate: writer_calls.append(rotate),
    )

    def consume(received: object, _actual: object) -> object:
        consumer_calls.append(received)
        return consumer_result

    monkeypatch.setattr(
        maestro_authority, "consume_maestro_authority_decision", consume
    )

    def containment_only_runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        containment_calls.append(command[1])
        if command[1] not in {"bootout", "print"}:
            raise AssertionError("authority failure must not bootstrap or start")
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    result = service.install_service(
        service_config,
        plist_path,
        runner=containment_only_runner,
        domain="gui/501",
        session_token_authority_context=context,
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.loaded is False
    assert result.detail == "session_token_source_unavailable"
    assert writer_calls == []
    assert len(consumer_calls) == expected_consumer_calls
    assert containment_calls == ["bootout", "print"]
    assert not plist_path.exists()
    assert not (
        service_config.hermes_home
        / "services"
        / "orch-next-serve"
        / "state"
        / "dashboard-session-token"
    ).exists()


def test_post_request_preconsume_drift_never_calls_consumer_or_writer_and_holds(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drift after request construction fails at the pre-consume boundary."""

    from scripts import orch_next_hermes_session_token_source as source
    from tui_gateway import maestro_authority

    identities = iter((_session_runtime_identity(), None))
    consumer_calls: list[object] = []
    writer_calls: list[bool] = []
    containment_calls: list[str] = []
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _config: next(identities),
    )
    monkeypatch.setattr(
        maestro_authority,
        "build_session_token_install_authority_request",
        lambda **_kwargs: _session_authority_context("decision-post-request"),
    )
    monkeypatch.setattr(
        maestro_authority,
        "consume_maestro_authority_decision",
        lambda *args: consumer_calls.append(args),
    )
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _c: False)
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "prepare_protected_command_config",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda _home, *, rotate: writer_calls.append(rotate),
    )
    context = service._session_token_install_authority_context(service_config)
    assert context == _session_authority_context("decision-post-request")

    def containment_only_runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        containment_calls.append(command[1])
        if command[1] not in {"bootout", "print"}:
            raise AssertionError("pre-consume drift must preserve the disabled hold")
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    result = service.install_service(
        service_config,
        tmp_path / "post-request.plist",
        runner=containment_only_runner,
        domain="gui/501",
        session_token_authority_context=context,
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "session_token_source_unavailable"
    assert consumer_calls == []
    assert writer_calls == []
    assert containment_calls == ["bootout", "print"]


def test_install_config_preparation_failure_never_calls_authority_or_writer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source
    from tui_gateway import maestro_authority

    containment_calls: list[str] = []
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _c: False)
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "prepare_protected_command_config",
        lambda *_args, **_kwargs: pytest.fail(
            "typed preparation failure must not be retried inside install"
        ),
    )
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: pytest.fail(
            "failed config preparation must stop before admission"
        ),
    )
    monkeypatch.setattr(
        maestro_authority,
        "consume_maestro_authority_decision",
        lambda *_args: pytest.fail(
            "failed config preparation must not reach authority transport"
        ),
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda *_args, **_kwargs: pytest.fail(
            "failed config preparation must not reach token writer"
        ),
    )

    def containment_only_runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        containment_calls.append(command[1])
        if command[1] not in {"bootout", "print"}:
            raise AssertionError("config preparation failure must preserve hold")
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    result = service.install_service(
        service_config,
        tmp_path / "config-preparation-failure.plist",
        runner=containment_only_runner,
        domain="gui/501",
        session_token_authority_context=_session_authority_context(
            "decision-config-preparation"
        ),
        command_config_prepared=False,
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "session_token_source_unavailable"
    assert containment_calls == ["bootout", "print"]


def test_install_may_prepare_no_token_config_before_missing_authority_hold(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source

    containment_calls: list[str] = []
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _c: False)
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda *_args, **_kwargs: pytest.fail(
            "missing authority must not reach token writer"
        ),
    )

    def containment_only_runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        containment_calls.append(command[1])
        if command[1] not in {"bootout", "print"}:
            raise AssertionError("missing authority must preserve hold")
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    result = service.install_service(
        service_config,
        tmp_path / "no-authority.plist",
        runner=containment_only_runner,
        domain="gui/501",
        session_token_authority_context=None,
    )

    command_cfg = source.protected_command_config(service_config.python)
    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "session_token_source_unavailable"
    assert source.command_source_is_admitted(
        service_config.hermes_home,
        command_cfg,
        runtime=service_config.python,
    )
    assert (service_config.hermes_home / "config.yaml").stat().st_mode & 0o777 == 0o600
    token_state = service_config.hermes_home / "services" / "orch-next-serve" / "state"
    assert not (token_state / source.TOKEN_LEAF).exists()
    assert not (token_state / source.LOCK_LEAF).exists()
    assert containment_calls == ["bootout", "print"]


def test_post_allow_prewriter_drift_never_calls_writer(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact allow is insufficient when the final locator check drifts."""

    from scripts import orch_next_hermes_session_token_source as source
    from tui_gateway import maestro_authority

    identities = iter((_session_runtime_identity(), None))
    consumer_calls: list[object] = []
    writer_calls: list[bool] = []
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _config: next(identities),
    )
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _c: False)
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda _home, *, rotate: writer_calls.append(rotate),
    )

    def exact_allow(context: object, actual: object) -> dict[str, object]:
        consumer_calls.append((context, actual))
        return _session_authority_allow("decision-post-allow")

    monkeypatch.setattr(
        maestro_authority,
        "consume_maestro_authority_decision",
        exact_allow,
    )

    assert not service._prepare_session_token_source(
        service_config,
        authority_context=_session_authority_context("decision-post-allow"),
    )
    assert len(consumer_calls) == 1
    assert writer_calls == []


def test_exact_allow_writes_once_and_replay_never_reaches_second_writer(
    service_config: service.ServiceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source
    from tui_gateway import maestro_authority

    context = _session_authority_context("decision-single-writer")
    ready = iter((False, True, False))
    consumer_results = iter((
        _session_authority_allow("decision-single-writer"),
        {"outcome": "deny", "code": "authority_replay"},
    ))
    writers: list[bool] = []
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _c: next(ready))
    monkeypatch.setattr(
        service,
        "_session_token_runtime_identity",
        lambda _c: _session_runtime_identity(),
    )
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda _home, *, rotate: writers.append(rotate),
    )
    monkeypatch.setattr(
        maestro_authority,
        "consume_maestro_authority_decision",
        lambda *_args: next(consumer_results),
    )

    assert service._prepare_session_token_source(
        service_config,
        authority_context=context,
    )
    assert not service._prepare_session_token_source(
        service_config,
        authority_context=context,
    )
    assert writers == [False]


@pytest.mark.parametrize("operation", [service.start_service, service.restart_service])
def test_start_and_restart_never_create_or_rotate_a_session_source(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation,
) -> None:
    """Read-only lifecycle actions hold on source failure before bootstrap."""

    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    preflight_calls: list[tuple[object, bool]] = []
    launchctl_operations: list[str] = []

    def unavailable_source(
        _config: service.ServiceConfig,
        *,
        authority_context: object = None,
        rotate: bool = False,
    ) -> bool:
        preflight_calls.append((authority_context, rotate))
        return False

    def absent_runner(
        command: list[str], **_options: object
    ) -> subprocess.CompletedProcess[str]:
        launchctl_operations.append(command[1])
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    monkeypatch.setattr(service, "_prepare_session_token_source", unavailable_source)
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)

    result = operation(
        service_config,
        plist_path,
        runner=absent_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "session_token_source_unavailable"
    assert preflight_calls == [(None, False)]
    assert "bootstrap" not in launchctl_operations


@pytest.mark.parametrize("operation", [service.start_service, service.restart_service])
def test_start_and_restart_never_prepare_missing_command_config(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source

    plist_path = tmp_path / "service-no-config.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    launchctl_operations: list[str] = []
    monkeypatch.setattr(
        service,
        "_prepare_session_token_source",
        _REAL_PREPARE_SESSION_TOKEN_SOURCE,
    )
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _c: False)
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(source, "protected_command_config", lambda _runtime: {})
    monkeypatch.setattr(
        source,
        "prepare_protected_command_config",
        lambda *_args, **_kwargs: pytest.fail(
            "start/restart must not repair command config"
        ),
    )
    monkeypatch.setattr(
        source,
        "command_source_is_admitted",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        source,
        "create_or_rotate_token",
        lambda *_args, **_kwargs: pytest.fail("start/restart must not write a token"),
    )

    def absent_runner(
        command: list[str], **_options: object
    ) -> subprocess.CompletedProcess[str]:
        launchctl_operations.append(command[1])
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    result = operation(
        service_config,
        plist_path,
        runner=absent_runner,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "session_token_source_unavailable"
    assert not (service_config.hermes_home / "config.yaml").exists()
    assert "bootstrap" not in launchctl_operations


def _prepare_refresh_fixture(config: service.ServiceConfig) -> tuple[Path, bytes]:
    """Create opaque config/token fixtures for config-only refresh coverage."""

    service.ensure_private_directories(config)
    config_path = config.hermes_home / "config.yaml"
    original_config = b"providers: {}\n"
    config_path.write_bytes(original_config)
    config_path.chmod(0o600)
    token_path = config.state_dir / "dashboard-session-token"
    token_path.write_bytes(b"a" * 64)
    token_path.chmod(0o600)
    return config_path, original_config


def _running_refresh_status(
    config: service.ServiceConfig,
    *,
    pid: int = 4242,
) -> service.ServiceResult:
    return service.ServiceResult(
        "status",
        service.ServiceState.RUNNING,
        config.label,
        True,
        True,
        pid,
    )


def test_refresh_session_token_command_config_updates_only_config_for_bound_running_service(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _original_config = _prepare_refresh_fixture(service_config)
    token_path = service_config.state_dir / "dashboard-session-token"
    before_token = token_path.stat()
    status_calls: list[object] = []
    prepare_calls: list[object] = []

    def bound_status(*_args: object, **_kwargs: object) -> service.ServiceResult:
        observed = _running_refresh_status(service_config)
        status_calls.append(observed)
        return observed

    def refresh_config(_config: service.ServiceConfig) -> bool:
        prepare_calls.append(None)
        config_path.write_bytes(b"secrets:\n  command:\n    stale: false\n")
        config_path.chmod(0o600)
        return True

    monkeypatch.setattr(service, "service_status", bound_status)
    monkeypatch.setattr(service, "_prepare_session_token_command_config", refresh_config)
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _config: True)

    result = service.refresh_session_token_command_config(
        service_config,
        tmp_path / "service.plist",
        current_config=service_config,
        runner=lambda *_args, **_kwargs: pytest.fail("refresh must not mutate service"),
        domain="gui/501",
    )

    after_token = token_path.stat()
    assert result == service.ServiceResult(
        "refresh-session-token-command-config",
        service.ServiceState.RUNNING,
        service.DEFAULT_LABEL,
        True,
        True,
        4242,
        detail="session_token_command_config_refreshed",
    )
    assert prepare_calls == [None]
    assert len(status_calls) == 2
    assert status_calls == [
        _running_refresh_status(service_config),
        _running_refresh_status(service_config),
    ]
    assert config_path.read_bytes() == b"secrets:\n  command:\n    stale: false\n"
    assert (
        before_token.st_dev,
        before_token.st_ino,
        stat.S_IMODE(before_token.st_mode),
        before_token.st_uid,
        before_token.st_gid,
        before_token.st_nlink,
        before_token.st_size,
        before_token.st_mtime_ns,
        before_token.st_ctime_ns,
    ) == (
        after_token.st_dev,
        after_token.st_ino,
        stat.S_IMODE(after_token.st_mode),
        after_token.st_uid,
        after_token.st_gid,
        after_token.st_nlink,
        after_token.st_size,
        after_token.st_mtime_ns,
        after_token.st_ctime_ns,
    )


def test_refresh_session_token_command_config_refuses_second_lifecycle_writer(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_refresh_fixture(service_config)
    lock_path = service_config.state_dir / "lifecycle.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: pytest.fail("lock refusal must precede status"),
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = service.refresh_session_token_command_config(
            service_config,
            tmp_path / "service.plist",
            current_config=service_config,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "lifecycle_busy"


def test_refresh_session_token_command_config_rolls_back_on_postwrite_readiness_failure(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, original_config = _prepare_refresh_fixture(service_config)
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: _running_refresh_status(service_config),
    )

    def refresh_config(_config: service.ServiceConfig) -> bool:
        config_path.write_bytes(b"secrets:\n  command:\n    stale: false\n")
        config_path.chmod(0o600)
        return True

    monkeypatch.setattr(service, "_prepare_session_token_command_config", refresh_config)
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _config: False)

    result = service.refresh_session_token_command_config(
        service_config,
        tmp_path / "service.plist",
        current_config=service_config,
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "session_token_command_config_not_ready"
    assert config_path.read_bytes() == original_config


def test_refresh_session_token_command_config_never_clobbers_raced_postwrite_config(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _original_config = _prepare_refresh_fixture(service_config)
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: _running_refresh_status(service_config),
    )

    def refresh_config(_config: service.ServiceConfig) -> bool:
        config_path.write_bytes(b"secrets:\n  command:\n    stale: false\n")
        config_path.chmod(0o600)
        return True

    def raced_readiness(_config: service.ServiceConfig) -> bool:
        config_path.write_bytes(b"raced: generation\n")
        config_path.chmod(0o600)
        return False

    monkeypatch.setattr(service, "_prepare_session_token_command_config", refresh_config)
    monkeypatch.setattr(service, "_session_token_source_ready", raced_readiness)

    result = service.refresh_session_token_command_config(
        service_config,
        tmp_path / "service.plist",
        current_config=service_config,
    )

    assert result.state is service.ServiceState.ERROR
    assert result.detail == "session_token_command_config_rollback_failed"
    assert config_path.read_bytes() == b"raced: generation\n"


@pytest.mark.parametrize(
    "status",
    [
        service.ServiceResult(
            "status", service.ServiceState.LOADED, service.DEFAULT_LABEL, True, True
        ),
        service.ServiceResult(
            "status", service.ServiceState.RUNNING, service.DEFAULT_LABEL, True, True
        ),
        service.ServiceResult(
            "status", service.ServiceState.RUNNING, "foreign.service", True, True, 4242
        ),
        service.ServiceResult(
            "status", service.ServiceState.RUNNING, service.DEFAULT_LABEL, True, True, 0
        ),
    ],
)
def test_refresh_session_token_command_config_rejects_unbound_current_runtime(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: service.ServiceResult,
) -> None:
    config_path, original_config = _prepare_refresh_fixture(service_config)
    monkeypatch.setattr(service, "service_status", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(
        service,
        "_prepare_session_token_command_config",
        lambda *_args: pytest.fail("unbound runtime must not write config"),
    )

    result = service.refresh_session_token_command_config(
        service_config,
        tmp_path / "service.plist",
        current_config=service_config,
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "service_runtime_not_bound"
    assert config_path.read_bytes() == original_config


def test_refresh_session_token_command_config_rejects_listener_mismatch_without_write(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, original_config = _prepare_refresh_fixture(service_config)
    plist_path = tmp_path / "service.plist"
    plist_path.write_bytes(_valid_plist_bytes(service_config))
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(
        service,
        "_prepare_session_token_command_config",
        lambda *_args: pytest.fail("listener mismatch must not write config"),
    )

    def wrong_listener(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        registered = _valid_launchd_print(service_config, "gui/501").replace(
            "\t\t3517\n",
            "\t\t3518\n",
            1,
        )
        return subprocess.CompletedProcess(command, 0, stdout=registered, stderr="")

    result = service.refresh_session_token_command_config(
        service_config,
        plist_path,
        current_config=service_config,
        runner=wrong_listener,
        domain="gui/501",
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "service_runtime_not_bound"
    assert config_path.read_bytes() == original_config


def test_refresh_session_token_command_config_rolls_back_when_running_pid_changes(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, original_config = _prepare_refresh_fixture(service_config)
    statuses = iter(
        (
            _running_refresh_status(service_config, pid=4242),
            _running_refresh_status(service_config, pid=4243),
        )
    )
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: next(statuses),
    )

    def refresh_config(_config: service.ServiceConfig) -> bool:
        config_path.write_bytes(b"secrets:\n  command:\n    stale: false\n")
        config_path.chmod(0o600)
        return True

    monkeypatch.setattr(service, "_prepare_session_token_command_config", refresh_config)
    monkeypatch.setattr(service, "_session_token_source_ready", lambda _config: True)

    result = service.refresh_session_token_command_config(
        service_config,
        tmp_path / "service.plist",
        current_config=service_config,
    )

    assert result.state is service.ServiceState.UNAVAILABLE
    assert result.detail == "service_runtime_binding_changed"
    assert config_path.read_bytes() == original_config


def _versioned_refresh_config(
    tmp_path: Path,
    base: service.ServiceConfig,
    version: str,
) -> service.ServiceConfig:
    worktree = tmp_path / f"hermes-{version}"
    runtime = worktree / ".venv" / "bin" / "hermes"
    python = worktree / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    runtime.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    python.write_text("python\n", encoding="utf-8")
    python.chmod(0o755)
    return service.ServiceConfig(
        worktree=worktree,
        runtime=runtime,
        python=python,
        hermes_home=base.hermes_home,
        port=base.port,
    )


def _running_current_service_runner(
    current: service.ServiceConfig,
) -> service.Runner:
    def runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert command == [
            service.LAUNCHCTL_PATH,
            "print",
            f"gui/501/{current.label}",
        ]
        registered = _valid_launchd_print(current, "gui/501").replace(
            "\tstate = running\n",
            "\tstate = running\n\tpid = 4242\n",
            1,
        )
        return subprocess.CompletedProcess(command, 0, stdout=registered, stderr="")

    return runner


def test_refresh_cli_binds_current_service_separately_from_desired_command_source(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source

    current = _versioned_refresh_config(tmp_path, service_config, "0.1.37")
    desired = _versioned_refresh_config(tmp_path, service_config, "0.1.38")
    service.ensure_private_directories(desired)
    source.create_or_rotate_token(desired.hermes_home, rotate=False)
    assert source.prepare_protected_command_config(
        desired.hermes_home,
        source.protected_command_config(current.python),
        runtime=current.python,
    )
    current_plist = tmp_path / "current-service.plist"
    current_plist.write_bytes(_valid_plist_bytes(current))
    parser = service.build_parser()
    arguments = [
        "refresh-session-token-command-config",
        "--worktree",
        str(desired.worktree),
        "--runtime",
        str(desired.runtime),
        "--python",
        str(desired.python),
        "--hermes-home",
        str(desired.hermes_home),
        "--current-worktree",
        str(current.worktree),
        "--current-runtime",
        str(current.runtime),
        "--current-python",
        str(current.python),
        "--current-port",
        str(current.port),
    ]
    args = parser.parse_args(arguments)
    parsed_desired = service._config_from_args(args)
    parsed_current = service._current_service_config_from_args(args)
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(
        service,
        "_session_token_source_ready",
        lambda config: source.command_source_is_admitted(
            config.hermes_home,
            source.protected_command_config(config.python),
            runtime=config.python,
        ),
    )

    result = service.refresh_session_token_command_config(
        parsed_desired,
        current_plist,
        current_config=parsed_current,
        runner=_running_current_service_runner(parsed_current),
        domain="gui/501",
    )

    assert result.state is service.ServiceState.RUNNING
    assert result.pid == 4242
    assert source.command_source_is_admitted(
        desired.hermes_home,
        source.protected_command_config(desired.python),
        runtime=desired.python,
    )

    with pytest.raises(SystemExit) as missing_current_identity:
        parser.parse_args(arguments[:10])
    assert missing_current_identity.value.code == 2


def test_refresh_reuses_config_recovery_slots_for_next_version_without_token_or_pid_change(
    service_config: service.ServiceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import orch_next_hermes_session_token_source as source

    current = _versioned_refresh_config(tmp_path, service_config, "0.1.37")
    first = _versioned_refresh_config(tmp_path, service_config, "0.1.38")
    next_version = _versioned_refresh_config(tmp_path, service_config, "0.1.39")
    service.ensure_private_directories(first)
    source.create_or_rotate_token(first.hermes_home, rotate=False)
    token_before = (first.state_dir / source.TOKEN_LEAF).stat()
    assert source.prepare_protected_command_config(
        first.hermes_home,
        source.protected_command_config(current.python),
        runtime=current.python,
    )
    current_plist = tmp_path / "current-service.plist"
    current_plist.write_bytes(_valid_plist_bytes(current))
    monkeypatch.setattr(service, "_launchctl_binary_qualified", lambda: True)
    monkeypatch.setattr(
        service,
        "_session_token_source_ready",
        lambda config: source.command_source_is_admitted(
            config.hermes_home,
            source.protected_command_config(config.python),
            runtime=config.python,
        ),
    )
    runner = _running_current_service_runner(current)

    first_result = service.refresh_session_token_command_config(
        first,
        current_plist,
        current_config=current,
        runner=runner,
        domain="gui/501",
    )
    second_result = service.refresh_session_token_command_config(
        next_version,
        current_plist,
        current_config=current,
        runner=runner,
        domain="gui/501",
    )
    token_after = (first.state_dir / source.TOKEN_LEAF).stat()

    assert first_result.state is service.ServiceState.RUNNING
    assert second_result.state is service.ServiceState.RUNNING
    assert second_result.pid == first_result.pid == 4242
    assert second_result.detail == (
        "session_token_command_config_refreshed_recovery_quarantined"
    )
    assert source.command_source_is_admitted(
        next_version.hermes_home,
        source.protected_command_config(next_version.python),
        runtime=next_version.python,
    )
    assert (first.hermes_home / source._CONFIG_RECOVERY_LEAF).exists()
    assert (first.hermes_home / source._CONFIG_QUARANTINED_RECOVERY_LEAF).exists()
    assert (
        token_before.st_dev,
        token_before.st_ino,
        stat.S_IMODE(token_before.st_mode),
        token_before.st_uid,
        token_before.st_gid,
        token_before.st_nlink,
        token_before.st_size,
        token_before.st_mtime_ns,
        token_before.st_ctime_ns,
    ) == (
        token_after.st_dev,
        token_after.st_ino,
        stat.S_IMODE(token_after.st_mode),
        token_after.st_uid,
        token_after.st_gid,
        token_after.st_nlink,
        token_after.st_size,
        token_after.st_mtime_ns,
        token_after.st_ctime_ns,
    )
