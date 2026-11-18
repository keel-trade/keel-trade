"""Save a screen view source, or restore an earlier revision, with its expected revision."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.
#
# Not destructive (founder F3, 2026-11-03): every save adds a revision to
# `strategy.result_view_revisions` and keeps the earlier ones, and
# `revert_to` restores one of them as a new revision.

from keel.api_path import seg
from keel.errors import KeelError

from ._screener_commands import (
    DRY,
    NONNEGATIVE_INT,
    NOT_RUN_CODES,
    SID,
    lines_result,
    refusal_code,
    register_command,
    route,
)
from ._screener_view import build_result, cell, invalid, read_envelope


REQUIRED_ACTION = "strategy.update"
DESCRIPTION = (
    "Replace a saved screen's view — title, description, column labels, which coins pass, "
    "sort, top-N and charts — with a complete view source and the revision it was read at. "
    "It changes how results are shown, not what the screen computes; a new series is a "
    "new version through `keel_strategy_compose`. Start from "
    "`keel_screener_view(include_view_source=true)` and change only the asked-for lines. "
    "Earlier revisions are kept; `revert_to` restores one as a new revision. "
    "`dry_run=true` checks without saving. The result names the revision saved and "
    "shows the screen with the new view."
)
PROPERTIES = {
    "strategy_id": SID,
    "view": {"type": "string", "description": "The complete view source."},
    "revert_to": {
        "type": "integer",
        "minimum": 1,
        "description": "An earlier revision to restore instead of sending `view`.",
    },
    "label": {
        "type": "string",
        "maxLength": 80,
        "description": "A few plain words naming the change, shown in the view's history.",
    },
    "expected_revision": {
        **NONNEGATIVE_INT,
        "description": "The revision the view was read at; a newer one refuses the save.",
    },
    "dry_run": DRY,
}

_put = route("put", "/v1/screeners/{strategy_id}/views", rename={"view": "source"})


def _handler(args, ctx):
    if (args.get("view") is None) == (args.get("revert_to") is None):
        invalid("Send either view (the complete source) or revert_to (a revision), not both.")
    # Every revision an agent saves says so in the view's history; a revert is
    # recorded as a restore by the server whatever origin is sent.
    saved = _put({**args, "origin": "agent"}, ctx).extra
    result = _dry_run_result(args, saved, ctx) if args.get("dry_run") else None
    result = result or _saved_result(args, saved, ctx)
    # The route's answer about the write: the revision saved (the next
    # `expected_revision`), the view check and the output units.
    result.extra.update({key: saved.get(key) for key in ROUTE_FIELDS if key in saved})
    return result


#: The PUT response's fields every result keeps beside the screen.
ROUTE_FIELDS = ("revision", "check", "units")
#: What the screen card says about a saved edit (`run.status_words`).
SAVED_WORDS = "View saved."


def _check_line(check):
    """The view check's warnings (an error refuses the write before here)."""
    issues = [i for i in (check or {}).get("issues") or [] if isinstance(i, dict)]
    if not issues:
        return None
    words = "; ".join(cell(i.get("message") or i.get("code") or "") for i in issues[:5])
    more = f"; +{len(issues) - 5} more" if len(issues) > 5 else ""
    return f"view check: {len(issues)} note{'' if len(issues) == 1 else 's'} — {words}{more}"


def _read_screen(args, ctx, *, size, view=None):
    """The screen as it reads with this view, as `keel_screener_view` builds it
    (the saved view after a save, the sent source on a dry run), or the lines
    that say why there is none: a screen with no run yet has no rows to draw,
    so it says so and names the run (Q-3634)."""
    read = {"strategy_id": args["strategy_id"], **({"view": view} if view else {})}
    try:
        envelope = read_envelope(read, ctx)
    except KeelError as err:
        if refusal_code(err) in NOT_RUN_CODES:
            return None, [
                "The screen hasn't run yet, so there are no rows to show.",
                f'next: keel_screener_run(strategy_id="{args["strategy_id"]}")',
            ]
        return None, [f"The screen could not be read back just now: {err}"]
    if view:
        # A preview draws an unsaved view: no saved revision names it.
        envelope = {**envelope, "screener": dict(envelope.get("screener") or {})}
        envelope["screener"].pop("view_revision", None)
    return build_result(envelope, ctx, size=size), None


def _joined(lines):
    """Joined here, not by `lines_result`, so the screen's blank line before
    its table survives (`lines_result` drops empty lines)."""
    return "\n".join(line for line in lines if line is not None).strip()


def _receipt(args, extra, lines, words, *, screen=None, run=None, hero_url=None):
    """A one-line screen card: the text's lines, and `words` as the sentence
    the card's receipt row draws (`run.status_words`, the card's no-result
    line). Used where there is no screen to draw: a dry run, or a save whose
    screen has no run yet."""
    result = lines_result(extra, [_joined(lines)])
    view = result.extra["view"]
    screen = dict(screen or {"strategy_id": args["strategy_id"]})
    view.update(
        {
            key: value
            for key, value in {
                "name": screen.get("name"),
                "version": screen.get("version"),
                "object": screen.get("strategy_id") or args["strategy_id"],
            }.items()
            if value is not None
        }
    )
    result.extra.update({"screener": screen, "run": {**(run or {}), "status_words": words}})
    result.hero_url = hero_url
    return result


def _dry_run_result(args, saved, ctx):
    """Checked, not saved: the check, the units, and the screen as the sent
    view would draw it on one line. A text receipt; the card draws that one
    line too, never the full screen of a view nobody saved."""
    units = [u.get("sentence") for u in saved.get("units") or [] if isinstance(u, dict)]
    head = (
        f"Checked, not saved: restoring revision {args['revert_to']} is valid."
        if args.get("revert_to") is not None
        else "Checked, not saved: the view is valid."
    )
    lines = [
        head,
        _check_line(saved.get("check")),
        ("units: " + "; ".join(u for u in units if u)) if units else None,
    ]
    preview = None
    if args.get("view") is not None:
        # The screen as this view would draw it, on one line.
        preview, missing = _read_screen(args, ctx, size="receipt", view=args["view"])
        shown = preview.extra["view"]["markdown"].splitlines() if preview else missing
        lines += ["", "With this view:"] + shown
    if preview is None:
        return _receipt(args, {}, lines, head)
    return _receipt(
        args,
        {},
        lines,
        head,
        screen=preview.extra.get("screener"),
        run=preview.extra.get("run"),
        hero_url=preview.hero_url,
    )


def _saved_name(sid, ctx):
    """`{name, version}` of a saved screen, best-effort: the never-run arm's
    card header. A read that fails costs the name, never the save's answer."""
    try:
        meta = ctx.get_client().get(f"/v1/strategies/{seg(str(sid))}")
    except Exception:  # noqa: BLE001 — a render nicety never fails a saved write
        return {}
    if not isinstance(meta, dict):
        return {}
    found = {"name": meta.get("name"), "version": meta.get("current_sequence")}
    return {key: value for key, value in found.items() if value is not None}


def _saved_result(args, saved, ctx):
    """What a view write did, then the screen it now shows (Q-3634, Q-3639).

    The founder: "shouldn't view also show screen in chat?" — after an edit
    the result IS the screen `keel_screener_view` returns, text and card,
    with the revision it saved (the next `expected_revision`) leading the
    text, so the user sees the edited screen and the agent can confirm the
    edit without a second call.
    """
    revision = saved.get("revision")
    label = cell(args.get("label")) if args.get("label") else None
    head = (
        f"Restored revision {args['revert_to']} as view revision {revision}"
        if args.get("revert_to") is not None
        else f"Saved view revision {revision}"
    ) + (f": {label}." if label else ".")
    lead = [head, _check_line(saved.get("check")), ""]
    result, missing = _read_screen(args, ctx, size="view")
    if result is None:
        # No screen to draw: the card says what the text says, in one row,
        # under the screen's own name (never "Untitled" for a saved screen).
        screen = {"strategy_id": args["strategy_id"], "view_revision": revision}
        screen.update(_saved_name(args["strategy_id"], ctx))
        return _receipt(args, {}, lead + missing, f"{head} {missing[0]}", screen=screen)
    view = result.extra["view"]
    view["markdown"] = _joined(lead + view["markdown"].splitlines())
    # The card's own words for the edit, beside the revision it drew with:
    # the saved one, whatever the re-read happened to carry.
    result.extra["run"] = {**(result.extra.get("run") or {}), "status_words": SAVED_WORDS}
    if revision is not None:
        result.extra["screener"] = {
            **(result.extra.get("screener") or {}),
            "view_revision": revision,
        }
    return result


TOOL = register_command(
    "screener_view_set",
    ("screener", "view-set"),
    DESCRIPTION,
    PROPERTIES,
    ["strategy_id", "expected_revision"],
    REQUIRED_ACTION,
    _handler,
    title="Edit Screen View",
    rate_tier="mutation",
)
