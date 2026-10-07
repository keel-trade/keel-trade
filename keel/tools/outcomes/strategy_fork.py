"""`keel_strategy_fork` — fork a strategy by id or share-link id.

Replaces: `sharing_fork`, `sharing_fork_by_id`, and the write portion of
`strategy_import_share`.

The single positional `source` arg auto-detects: values that look like a
Keel strategy id (`str_*`) hit `POST /v1/strategies/{id}/fork`; everything
else is treated as a share-link id and goes through
`POST /v1/strategies/fork-with-edits`.

`description` (optional, ≤2000 chars) is the fork's thesis in the user's
own words, written onto the NEW strategy through the update endpoint after
the fork (the fork endpoints take no description) and shown on its
strategy page (Q-1617). Never the conversation — the thesis only.

Do NOT use to compose a new strategy from scratch — call `keel_strategy_compose`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import (
    STRATEGY_NAME_FACT,
    OutcomeResult,
    OutcomeTool,
    ToolContext,
    listed_schema,
    present_choice,
    present_param_schema,
)


#: This tool's `present` default, as its parameter description states it.
PRESENT_DEFAULT = "`view`"


def _looks_like_strategy_id(value: str) -> bool:
    return value.startswith("str_")


def fork_note(result: Any, *, source: str) -> dict[str, Any] | None:
    """`{parent_strategy_id}` exactly when the caller forked its OWN org's
    strategy (spec 03 §2.9, R-2), else None.

    The condition is the org, never the id shape: `/fork` admits ANY org's
    PUBLIC strategy by `str_` id, so "a `str_` fork succeeded" says nothing
    about ownership. keel-api's `source_org_id` beside the new strategy's
    `org_id` is the one fact; a response without both (an older keel-api,
    the share-link path) carries no note — never a guess.

    Spec 03 §2.9 also named a `parent_name`; keel-api's
    `ForkStrategyResponse` carries no source name (only `source_org_id`), so
    that key could only ever be `null` — a field that is always empty reads
    as a fact about the parent. It is not emitted (review 2 #4).
    """
    if not _looks_like_strategy_id(source) or not isinstance(result, dict):
        return None
    source_org, target_org = result.get("source_org_id"), result.get("org_id")
    if not isinstance(source_org, str) or not source_org or source_org != target_org:
        return None
    return {"parent_strategy_id": source}


def fork_next(note: dict[str, Any]) -> str:
    """Spec 02 §2.4 #1(g): a fact, then the call — the phrase "a version,
    not a fork" is the one spec 01 §7 pins."""
    parent = note.get("parent_strategy_id")
    return (
        f'This is your own strategy — a new version is keel_strategy_compose(strategy_id="{parent}"); '
        "this fork is a separate copy. A variant is a version, not a fork."
    )


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    source: str = (args.get("source") or "").strip()
    if not source:
        raise KeelError(
            "Missing required `source` (strategy_id or share_id).",
            error_code="missing_source",
            exit_code=2,
            suggestion=(
                "Pass `source=str_abc` to fork an existing strategy, OR "
                "`source=<share_link_id>` from a `keel://share/<id>` URL. "
                "Find ids via `keel_strategy_search`."
            ),
        )

    name: str | None = args.get("name")
    target_workspace_id: str | None = args.get("target_workspace_id")

    client = ctx.get_client()

    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if target_workspace_id:
        body["target_workspace_id"] = target_workspace_id

    if _looks_like_strategy_id(source):
        try:
            # Always send an object body (even if empty). The
            # /v1/strategies/{id}/fork endpoint requires a JSON body
            # (ForkStrategyRequest); passing `None` causes the API to
            # 422 with `Field required` since the body is missing
            # entirely. {} is accepted (all fields optional).
            result = client.post(f"/v1/strategies/{source}/fork", json=body)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to fork strategy {source}: {e}",
                suggestion=(
                    "Verify the strategy id is correct + you have read access "
                    "(`keel_strategy_get {source}`). If the source strategy "
                    "is in another org, you need a share link instead — "
                    "pass `source=<share_link_id>`."
                ),
            )
    else:
        # share-id path: fork-with-edits
        try:
            # `ForkWithEditsRequest` keys the link by `share_id` — the share
            # link's own primary key. /fork calls the same id `share_link_id`;
            # sending that name here 422'd every share-link fork (Q-1621).
            payload = dict(body)
            payload["share_id"] = source
            result = client.post("/v1/strategies/fork-with-edits", json=payload)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to fork share {source}: {e}",
                suggestion=(
                    "Verify the share link is still valid and not expired. "
                    "Share links can also be revoked by the creator. If you "
                    "have a strategy id instead, pass it as `source=str_...`."
                ),
            )

    new_sid = result.get("strategy_id") or result.get("id")
    extra: dict[str, Any] = {
        "strategy_id": new_sid,
        "parent": source,
    }
    if target_workspace_id:
        # What was applied, as keel-api reports it (Q-2270: the share route
        # used to drop the workspace without a word).
        extra["workspace_id"] = result.get("workspace_id")
    note = fork_note(result, source=source)
    if note is not None:
        extra["fork_note"] = note
        extra["next"] = fork_next(note)

    # The thesis line (Q-1617). Neither fork endpoint takes a description
    # (`ForkStrategyRequest` / `ForkWithEditsRequest` carry none), so it is
    # written through the update endpoint that already accepts it — a
    # second call, on the new strategy only. The fork has already happened
    # by now, so a failure here is reported beside the new id rather than
    # turning a created strategy into an error response.
    description: str | None = args.get("description")
    if description is not None and new_sid:
        try:
            client.patch(f"/v1/strategies/{new_sid}", json={"description": description})
        except KeelError as e:
            extra["description_error"] = str(e)

    # What the user now has, drawn the way the editor draws it (PLAN
    # §4.2). The moment after a fork is exactly the moment an id and a
    # link say nothing (Q-1619), so the fork reads its new strategy back
    # and carries the view. Advisory — the fork has already happened.
    from .open_in_app import app_url_for
    from .strategy_get import view_for_strategy

    if new_sid:
        view = view_for_strategy(client, new_sid, ctx)
        if view is not None:
            # A fork is a thing the user now HAS, so the default is the
            # whole structure (BUILD §2.4). `present="receipt"` is the
            # caller saying this fork is an intermediate step — forking
            # eight library entries to compare them, say.
            if present_choice(args) == "receipt":
                from ._strategy_view import resize_view

                view = resize_view(view, "receipt")
            extra["view"] = view

    return OutcomeResult(
        run_id=new_sid,
        # No fork id means there is nothing to open. Pointing at the
        # strategy LIST invents a destination the reader did not ask for —
        # the same defect Q-1686 fixed in compose, and this is the other
        # card tool carrying it. `app_url_for` has no "no target" output, so
        # a caller that improvises one is always inventing. Every consumer
        # already reads None correctly (`_base.py`, `card-strategy.js`
        # linkUrl, `host-adapter.js` renderLinkRow).
        hero_url=app_url_for("strategy", new_sid, ctx) if new_sid else None,
        share_url=None,
        extra=extra,
    )


FORK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["source"],
    "properties": {
        "source": {
            "type": "string",
            "x-cli-positional": True,
            "description": (
                "Strategy id (`str_*`) or share-link id (the token in a "
                "`usekeel.io/share/<id>` URL)."
            ),
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": f"Optional new name for the fork. {STRATEGY_NAME_FACT}",
        },
        "target_workspace_id": {
            "type": "string",
            "description": (
                "Workspace to place the fork in (default: org default); it must "
                "belong to the caller's org, and the result's `workspace_id` names "
                "where the fork landed."
            ),
        },
        "present": present_param_schema(PRESENT_DEFAULT),
        "description": {
            "type": "string",
            "maxLength": 2000,
            "description": (
                "The fork's thesis in the user's own words — one or two sentences "
                "on what this copy is for and what it changes (not a transcript of "
                "the conversation). Shown on the strategy page in the Keel app, "
                "where the user can edit it."
            ),
        },
    },
}


STRATEGY_FORK = register(
    OutcomeTool(
        name="keel_strategy_fork",
        required_action="strategy.create",
        cli_path=("strategy", "fork"),
        toolset="backtest",
        # grounded-in: system/chat/collaboration.md §4 (iterate, don't rewrite —
        # the SMALLEST change, one at a time; never rearchitect a working
        # strategy without asking); system/chat/tool_usage.md:21-25 (discovery applies
        # to every edit); context-architecture-design Part F (fork to
        # iterate on a copy).
        # Q-1961: facts about what a fork IS, never a purpose to reach for —
        # "or one to develop apart from the original" read as "fork to keep
        # the original clean for comparison", which versions already do.
        description=(
            "Copy a strategy into the caller's org as a separate strategy with its own "
            "history; the original's source and versions are unchanged (saving a new "
            "version of a strategy is `keel_strategy_compose`). The copy starts with one "
            "version and no backtests; the original keeps its versions and their "
            "backtests. `source` is a strategy id (`str_*`) or the id in a "
            "`usekeel.io/share/<id>` URL showing its source, any organisation's; "
            "forking one adds to its public fork count. Sharing is `keel_share_create`."
        ),
        input_schema=FORK_INPUT_SCHEMA,
        # The listed twin omits `target_workspace_id` (`_base.LISTED_SCHEMA_OMISSIONS`):
        # the share-link route never reads it, and the hosted server has no workspace.
        listed_input_schema=listed_schema("keel_strategy_fork", FORK_INPUT_SCHEMA),
        annotations={
            "title": "Fork Strategy",
            "readOnlyHint": False,
            "destructiveHint": False,
            # Every call creates a NEW strategy (Q-1748) — calling it twice
            # with the same arguments leaves two forks.
            "idempotentHint": False,
            # `source` accepts a share link whose source is shown (keel-api
            # refuses one with `include_source` false), so the strategy copied
            # can be another organisation's (Q-2080, 2026-10-01): the input
            # side is open-ended even though the copy lands in the caller's
            # own workspace.
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
