"""`keel_strategy_restore` — restore a historical commit as new HEAD.

Wraps `POST /v1/strategies/<id>/versions/restore`. Server-side
operation: takes an old commit ref (sequence number, commit_id, or
tag), reads the source from that commit, creates a NEW commit on
HEAD with that source. So history is preserved (you can see the
restore as a new entry in `keel_strategy_history`).

The "git revert" of the sync model — except it creates a forward
commit rather than a reverse-diff commit. Use this when an agent or
user made a change that should be undone, OR when a backtest of an
older version showed better results and you want to go back.

After restoring, the local workspace (if checked out) will be
'behind' — pull to catch up.
"""

from __future__ import annotations

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._surface_hints import strategy_ids_hint
from ._toolsets import is_listed_profile
from .open_in_app import app_url_for


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    ref = (args.get("ref") or "").strip()

    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=(
                "Pass the strategy id explicitly (e.g. "
                "`keel_strategy_restore strategy_id=str_abc ref=3`). " + strategy_ids_hint()
            ),
        )
    if not ref:
        raise KeelError(
            "Missing required `ref` — the commit to restore.",
            error_code="missing_ref",
            exit_code=2,
            suggestion=(
                "Pass a sequence number, commit_id, or tag (e.g. "
                "`keel_strategy_restore strategy_id=str_abc ref=3` or "
                "`ref=cmt_xyz` or `ref=v1.0`). Use `keel_strategy_history` "
                "to find the ref you want."
            ),
        )

    # No SDK default (spec 03 §2.4, R-15): keel-api's own "Restored from vN"
    # is the default label, and a caller's `message` wins. Sending
    # "Restore version <ref>" here made the listed description false.
    body: dict = {"ref": ref}
    message = args.get("message")
    if isinstance(message, str) and message.strip():
        body["message"] = message.strip()

    client = ctx.get_client()
    try:
        result = client.post(
            f"/v1/strategies/{strategy_id}/versions/restore",
            json=body,
        )
    except NotFoundError:
        raise
    except KeelError as e:
        detail = e.detail or {}
        if detail.get("code") == "RESTORE_DOES_NOT_COMPILE":
            # Spec 03 §2.4 (atomic restore): the source at `ref` does not
            # compile under today's registry, and NOTHING was written — no
            # commit, no HEAD move. The source is readable to fix forward.
            e.recovery_tool = "keel_strategy_get"
            e.recovery_tool_args = {
                "strategy_id": strategy_id,
                "version": ref,
                "include_source": True,
            }
            e.suggestion = (
                f"The source at {ref} does not compile today; nothing was written. "
                "keel_strategy_get returns that source, and keel_strategy_compose saves a "
                "corrected version."
            )
        raise
    except Exception as e:
        raise KeelError(
            f"Failed to restore strategy {strategy_id}@{ref}: {e}",
            suggestion="Verify the ref exists via `keel_strategy_history`.",
        ) from e

    new_sequence = result.get("current_sequence") or result.get("sequence")

    # The API returns StrategyResponse: no commit id, no commit message, no
    # `restored_from`. All three are on the commit the restore wrote, so ONE
    # follow-up read of the head version supplies them (Q-2270: the result
    # used to echo the raw `ref` alone, so a tag or commit-id restore never
    # said which version it restored, and the applied label went unshown).
    head = _head_version(client, strategy_id, new_sequence)
    new_commit_id: str | None = result.get("commit_id") or head.get("commit_id")
    restored_from = (head.get("meta") or {}).get("restored_from")
    if not isinstance(restored_from, int) or isinstance(restored_from, bool):
        restored_from = _sequence_of(ref)
    hero_url = app_url_for("strategy", strategy_id, ctx)
    extra: dict = {
        "strategy_id": strategy_id,
        "restored_from_ref": ref,
        "restored_from_sequence": restored_from,
        "new_sequence": new_sequence,
        "new_commit_id": new_commit_id,
        "next": restore_next(ref, new_sequence, {"restored_from_sequence": restored_from}),
    }
    # `message` keeps its meaning — the new version's commit message as
    # written: the stored one, else the caller's.
    stored_message = head.get("message") or (
        result.get("message") if isinstance(result, dict) else None
    )
    if isinstance(stored_message, str) and stored_message:
        extra["message"] = stored_message
    elif "message" in body:
        extra["message"] = body["message"]
    if not is_listed_profile():
        # A local-only fact (`keel_strategy_pull` is not served on the
        # listed profile): a checkout on THIS machine is now behind.
        extra["sync_note"] = (
            "A local checkout of this strategy is now behind server HEAD; "
            "keel_strategy_pull brings it up to date."
        )

    # The strategy kind, same rule as compose (R-15): the restored HEAD as a
    # view, so the text block is its markdown rather than a JSON blob.
    # Advisory — the restore already succeeded.
    from .strategy_get import view_for_strategy

    view = view_for_strategy(client, strategy_id, ctx)
    if view is not None:
        extra["view"] = view
    return OutcomeResult(
        run_id=strategy_id,
        hero_url=hero_url,
        share_url=None,
        extra=extra,
    )


def _head_version(client, strategy_id: str, new_sequence: object) -> dict:
    """The head version row (`GET /versions?limit=1`) when it is the commit
    this restore wrote, else `{}`. Advisory: the restore already succeeded,
    and a head another write has since moved is not this restore's."""
    from keel.workspace import _normalize_paginated_versions

    try:
        rows = _normalize_paginated_versions(
            client.get(f"/v1/strategies/{strategy_id}/versions", limit=1)
        )
    except Exception:  # noqa: BLE001 — advisory read; the restore stands without it
        return {}
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    if new_sequence is not None and row.get("sequence_number") != new_sequence:
        return {}
    return row


def _sequence_of(ref: str) -> int | None:
    """`3` / `#3` → 3; a tag or a commit id → None."""
    text = ref.strip().lstrip("#")
    return int(text) if text.isdigit() else None


def restore_next(ref: str, new_sequence: object, result: object = None) -> str:
    """The restore's one fact line (spec 02 §2.4 #1(h), spec 03 §2.4).

    `Restored v{N} as v{M}; keel_strategy_history lists both.` — N is the ref's
    sequence number, or the server's `restored_from` sequence when the ref
    was a tag or commit id; failing both, the ref as given.
    """
    source = _sequence_of(ref)
    if source is None and isinstance(result, dict):
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        for candidate in (result.get("restored_from_sequence"), meta.get("restored_from")):
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                source = candidate
                break
    named = f"v{source}" if source is not None else ref
    target = f"v{new_sequence}" if new_sequence is not None else "a new version"
    return f"Restored {named} as {target}; keel_strategy_history lists both."


STRATEGY_RESTORE = register(
    OutcomeTool(
        name="keel_strategy_restore",
        required_action="strategy.update",
        cli_path=("strategy", "restore"),
        toolset="backtest",
        # grounded-in: strategy_restore.py docstring (git-revert semantics —
        # forward commit, history preserved); spec 08 sync contract (after a
        # server-side HEAD move, a checked-out workspace is 'behind' → pull);
        # system/chat/collaboration.md:86 (recovery over destruction).
        # Spec 03 §2.4 / spec 01 R-4: the listed description plus the
        # full-profile facts (the local checkout, the local discard tools).
        description=(
            "Add a new version of a strategy equal to an earlier one — a restore; the "
            "history is `keel_strategy_history`, the current strategy `keel_strategy_get`. "
            "`ref` is a sequence number, tag or commit id; that version's source becomes "
            'the next, latest version, labelled "Restored from vN" unless `message` names '
            "it. The history is kept and nothing is deleted. After a restore, a "
            "checked-out local workspace is behind server HEAD; `keel_strategy_pull` "
            "catches it up. Forking into a new strategy is `keel_strategy_fork`; "
            "discarding local edits is `keel_strategy_pull force=True` or "
            "`keel_strategy_discard`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id", "ref"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy to restore.",
                },
                "ref": {
                    "type": "string",
                    # Spec 03 §2.4: commit ids are UUIDs (there is no
                    # `cmt_` prefix — `resolve_commit_ref` strips hyphens).
                    "description": "A sequence number, tag or commit id (from `keel_strategy_history`).",
                },
                "message": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Commit message for the new version. Omitted: 'Restored from vN'.",
                },
            },
        },
        annotations={
            "title": "Restore Strategy Version",
            "readOnlyHint": False,
            "destructiveHint": False,  # creates new commit, doesn't delete
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_handler,
        # SEAM (lane L4 → L6a): spec 03 §2.4's listed description, verbatim
        # — the listing needs one (the shared text names local-only tools and
        # an imperative). Copy is lane L6a's; this is the spec's frozen text,
        # placed so the listing is policy-clean, for L6a to own.
        listed_description=(
            "Add a new version of a strategy equal to an earlier one — a restore; the "
            "history is `keel_strategy_history`, the current strategy `keel_strategy_get`. "
            "`ref` is a sequence number, tag or commit id; that version's source becomes "
            'the next, latest version, labelled "Restored from vN" unless `message` names '
            "it. The history is kept and nothing is deleted."
        ),
    )
)
