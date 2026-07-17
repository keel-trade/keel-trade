"""Shared write-through guard for run-path tools (spec 08 R2).

Server HEAD is the single source of truth for strategy source: every
runnable action (backtest, deploy) resolves against a server commit,
never a local file. The local workspace is a working copy that WRITES
THROUGH by default — if it is ahead when a run-path tool fires, the
edits are pushed (with a generated commit message) before the action
runs, so the action always references what the user actually has.

Rules encoded here:

  * Not checked out / clean / hosted server → no-op (None).
  * Ahead + ``auto_push`` unset or True → push first, return the push
    result so the caller can pin to the new commit.
  * Ahead + ``auto_push=False`` (the explicit opt-out) → raise
    ``local_ahead`` so the agent decides (push / pin / pull).
  * Ahead AND server moved since checkout (true conflict) → the push's
    optimistic-concurrency check 409s and we STOP with a conflict
    error. Write-through NEVER force-overwrites (spec 08 R4).
  * Any non-Keel workspace failure (offline, corrupt meta, …) → no-op.
    The guard is advisory; a workspace bug must never block an
    otherwise-valid server-side action.
"""

from __future__ import annotations

from typing import Any

from keel.errors import ConflictError, KeelError


def _local_ahead_error(strategy_id: str, action: str) -> KeelError:
    return KeelError(
        f"Local workspace has unpushed edits — {action} would use the OLD "
        "server version, not your local changes (auto_push=False opts out "
        "of the default write-through push).",
        error_code="local_ahead",
        exit_code=2,
        suggestion=(
            "Either `keel_strategy_push` first, OR re-run without "
            "`auto_push=False` to push automatically (the default), OR "
            "pass an explicit `commit_id` to pin to a historical version. "
            "See `keel_strategy_status` for the diff."
        ),
    )


def write_through_guard(
    args: dict,
    *,
    strategy_id: str,
    action: str,
    default_message: str,
    check_only: bool = False,
    check_only_suggestion: str | None = None,
) -> dict[str, Any] | None:
    """Ensure a run-path action never references a silently-ahead local copy.

    Returns the ``keel.workspace.push`` result dict when a write-through
    push happened, else ``None`` (nothing to do). See module docstring
    for the full rule table.

    Args:
        args: The tool args (reads ``auto_push`` and ``push_message``).
        strategy_id: Strategy the action targets.
        action: Human name of the action ("backtest", "live-deploy
            preview", …) for error messages.
        default_message: Generated commit message when auto-pushing.
        check_only: If True, never push — raise ``local_ahead`` with
            ``check_only_suggestion`` when the local copy is ahead.
            Used where pushing would invalidate an earlier preview.
        check_only_suggestion: Suggestion text for the check_only raise.
    """
    from keel.hosting import is_hosted

    # Hosted servers have no caller filesystem: there is no local working
    # copy that could diverge (spec 01 R2 — local branches no-op hosted).
    if is_hosted():
        return None

    raw_auto_push = args.get("auto_push")
    auto_push = True if raw_auto_push is None else bool(raw_auto_push)

    try:
        from keel.workspace import (
            _compute_hash,
            get_workspace,
            read_local_source,
        )
        from keel.workspace import (
            push as _ws_push,
        )

        meta = get_workspace(strategy_id)
        if meta is None:
            return None
        local_hash = _compute_hash(read_local_source(strategy_id))
        if local_hash == meta.source_hash:
            return None

        # Local is ahead of its checkout base.
        if check_only:
            raise KeelError(
                f"Local workspace changed — {action} stopped so it can't "
                "silently reference a strategy whose local copy is ahead.",
                error_code="local_ahead",
                exit_code=2,
                suggestion=check_only_suggestion
                or "Re-run the preview step — it pushes local edits by default.",
            )
        if not auto_push:
            raise _local_ahead_error(strategy_id, action)

        try:
            return _ws_push(
                strategy_id=strategy_id,
                message=args.get("push_message") or default_message,
            )
        except ConflictError as exc:
            # True conflict: local edited AND server HEAD moved since
            # checkout. The optimistic-concurrency push 409'd. NEVER
            # force — stop with three-way context (spec 08 R4).
            from keel.workspace import build_conflict_envelope

            raise build_conflict_envelope(
                strategy_id,
                base_hash=meta.source_hash,
                local_hash=local_hash,
                action=action,
            ) from exc
    except KeelError:
        raise
    except Exception:  # noqa: BLE001 — advisory guard: workspace lib problem must not block the action
        return None
