"""`keel_strategy_workspaces` — list all checked-out strategy workspaces.

Useful when an agent (or user) doesn't remember what's been checked
out, or wants to enumerate work-in-progress across strategies. Lists
the workspace dir + sync metadata for each. No server call needed —
this is purely local filesystem reading.
"""

from __future__ import annotations

from typing import Any

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from keel.workspace import (
        WORKSPACE_ROOT,
        find_project_root,
        list_workspaces,
        project_workspace_root,
    )

    workspaces = list_workspaces()
    project_root = find_project_root()
    project_ws_root = project_workspace_root(project_root) if project_root else None

    items: list[dict[str, Any]] = []
    for ws in workspaces:
        # Figure out where this entry lives so the agent can show the
        # user a full path AND tell them whether it's project-local
        # (visible in their editor) or hidden in ~/.keel/workspace.
        ws_dir = None
        mode = None
        if project_ws_root is not None:
            candidate = project_ws_root / ws.strategy_id
            if candidate.exists():
                ws_dir = candidate
                mode = "project"
        if ws_dir is None:
            candidate = WORKSPACE_ROOT / ws.strategy_id
            if candidate.exists():
                ws_dir = candidate
                mode = "home"
        items.append(
            {
                "strategy_id": ws.strategy_id,
                "name": ws.name,
                "source_hash": (ws.source_hash or "")[:12],
                "checked_out_at": ws.checked_out_at,
                "current_sequence": ws.current_sequence,
                "workspace": str(ws_dir) if ws_dir else None,
                "mode": mode,
            }
        )

    next_steps: list[str]
    if not items:
        next_steps = [
            "No local workspaces yet. Create one via `keel_strategy_checkout <strategy_id>`.",
        ]
        # When cwd isn't a project, project-mode workspaces from OTHER
        # projects are invisible — this is by design (per-project
        # isolation) but easy to forget. Surface the hint so the user
        # doesn't think their work is lost.
        if project_root is None:
            next_steps.append(
                "If you have workspaces in another project, cd into that "
                "project dir (the one with `.keel/workspace.yaml`) to see "
                "them. Project-mode workspaces are scoped per-project."
            )
    else:
        next_steps = [
            "Use `keel_strategy_status <id>` to see sync state per workspace.",
            "Use `keel_strategy_discard <id>` to remove a workspace "
            "(server-side strategy is unaffected).",
        ]
        # Encourage `keel project init` if all are home-mode and cwd
        # isn't in a project — IDE collaboration is otherwise impossible.
        if project_ws_root is None and items and all(i["mode"] == "home" for i in items):
            next_steps.append(
                "Workspaces are in the hidden home dir. Run `keel project init` "
                "in your working dir + re-checkout to make them IDE-visible."
            )

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/strategies",
        share_url=None,
        extra={
            "workspaces": items,
            "count": len(items),
            "project_root": str(project_root) if project_root else None,
            "next": next_steps,
        },
    )


STRATEGY_WORKSPACES = register(
    OutcomeTool(
        name="keel_strategy_workspaces",
        required_action="strategy.read",
        cli_path=("strategy", "workspaces"),
        toolset="read-only",
        local_only=True,  # lists local checkouts under ~/.keel/workspace
        description=(
            "List all locally checked-out strategy workspaces. Each entry has "
            "the strategy id, name, sync metadata (source hash, last "
            "checkout time, sequence at checkout). No server call — pure "
            "filesystem read of `~/.keel/workspace/` (and any project-local "
            "`.keel/workspace.yaml`-scoped workspaces). "
            "Use when you've lost track of what's been checked out, or to "
            "enumerate WIP across strategies. "
            "Do NOT use to list strategies on the server — call "
            "`keel_strategy_search`. Do NOT use to check sync state of one "
            "workspace — call `keel_strategy_status`."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={
            "title": "List Local Workspaces",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
