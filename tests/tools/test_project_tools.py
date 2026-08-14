"""Behavioral tests for the per-turn project workspace callback seam."""

from __future__ import annotations

import contextvars

from tools import project_tools


def test_workspace_callback_is_context_local_and_restorable():
    calls: list[tuple[str, str, str, str]] = []

    old_token = project_tools.set_project_workspace_callback(
        lambda task_id, path, name: calls.append(("old", task_id, path, name))
    )
    old_context = contextvars.copy_context()
    project_tools.reset_project_workspace_callback(old_token)

    new_token = project_tools.set_project_workspace_callback(
        lambda task_id, path, name: calls.append(("new", task_id, path, name))
    )
    try:
        old_context.run(
            project_tools._apply_workspace,
            "same-durable-key",
            "/old/path",
            "Old",
        )
        project_tools._apply_workspace(
            "same-durable-key",
            "/new/path",
            "New",
        )
    finally:
        project_tools.reset_project_workspace_callback(new_token)

    assert calls == [
        ("old", "same-durable-key", "/old/path", "Old"),
        ("new", "same-durable-key", "/new/path", "New"),
    ]


def test_workspace_callback_absent_outside_bound_context():
    calls: list[tuple] = []
    token = project_tools.set_project_workspace_callback(
        lambda *args: calls.append(args)
    )
    project_tools.reset_project_workspace_callback(token)

    project_tools._apply_workspace("durable", "/workspace", "Project")

    assert calls == []
