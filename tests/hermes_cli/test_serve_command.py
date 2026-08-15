"""Contract for the headless ``hermes serve`` backend command.

``serve`` is what the desktop app and remote backends launch — the same gateway
as ``dashboard`` (shared handler) but always headless, and decoupled in name so
the desktop never invokes ``dashboard``. These tests pin that contract:

- ``serve`` routes to the same handler as ``dashboard``;
- ``serve`` is headless by default, ``dashboard`` is not;
- both expose the identical server-runtime flag surface.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from hermes_cli.subcommands.dashboard import build_dashboard_parser


def _dash(args):  # sentinel handler — identity-compared, never invoked
    return args


def _register(args):
    return args


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    build_dashboard_parser(
        parser.add_subparsers(dest="command"),
        cmd_dashboard=_dash,
        cmd_dashboard_register=_register,
    )
    return parser






def test_serve_supports_the_lifecycle_flags():
    for flag in ("--stop", "--status"):
        assert getattr(_parser().parse_args(["serve", flag]), flag.lstrip("-")) is True


def test_serve_admits_the_fixed_orch_sidecar_role():
    args = _parser().parse_args(
        [
            "serve",
            "--orch-sidecar",
            "--host",
            "127.0.0.1",
            "--port",
            "3518",
        ]
    )

    assert args.orch_sidecar is True
    assert args.host == "127.0.0.1"
    assert args.port == 3518


def test_serve_admits_a_non_live_loopback_dry_sidecar_port():
    args = _parser().parse_args(
        ["serve", "--orch-sidecar", "--host", "127.0.0.1", "--port", "3599"]
    )

    assert args.orch_sidecar is True
    assert args.port == 3599


@pytest.mark.parametrize(
    "overrides",
    [
        {"host": "0.0.0.0"},
        {"port": 3517},
        {"insecure": True},
        {"status": True},
    ],
)
def test_cmd_dashboard_rejects_sidecar_identity_mismatches(overrides):
    from hermes_cli import main

    values = {
        "orch_sidecar": True,
        "headless_backend": True,
        "host": "127.0.0.1",
        "port": 3518,
        "insecure": False,
        "status": False,
        "stop": False,
        "ssh_session_token_file": None,
        "ssh_owner_nonce": None,
        "open_profile": "",
    }
    values.update(overrides)

    with pytest.raises(SystemExit, match="orch sidecar identity rejected"):
        main.cmd_dashboard(SimpleNamespace(**values))


def test_cmd_dashboard_forwards_sidecar_without_shared_startup(monkeypatch):
    from hermes_cli import main, mcp_startup, plugins, web_server

    def forbidden(*_args, **_kwargs):
        raise AssertionError("shared startup owner must be suppressed")

    monkeypatch.setattr(main, "_sync_bundled_skills_quietly", forbidden)
    monkeypatch.setattr(main, "_maybe_setup_dashboard_auth_interactively", forbidden)
    monkeypatch.setattr(plugins, "discover_plugins", forbidden)
    monkeypatch.setattr(mcp_startup, "start_background_mcp_discovery", forbidden)
    captured = {}
    monkeypatch.setattr(
        web_server,
        "start_server",
        lambda **kwargs: captured.update(kwargs),
    )
    args = SimpleNamespace(
        orch_sidecar=True,
        headless_backend=True,
        host="127.0.0.1",
        port=3518,
        insecure=False,
        status=False,
        stop=False,
        ssh_session_token_file=None,
        ssh_owner_nonce=None,
        open_profile="",
        isolated=True,
        no_open=True,
        skip_build=False,
    )

    main.cmd_dashboard(args)

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 3518
    assert captured["orch_sidecar"] is True
    monkeypatch.delenv("HERMES_ORCH_SIDECAR", raising=False)


def test_serve_is_a_headless_backend_but_dashboard_is_not():
    # `headless_backend` is the flag cmd_dashboard reads to skip the web UI
    # build; only `serve` carries it.
    assert getattr(_parser().parse_args(["serve"]), "headless_backend", False) is True
    assert getattr(_parser().parse_args(["dashboard"]), "headless_backend", False) is False
