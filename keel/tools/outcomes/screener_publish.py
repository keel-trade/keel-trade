"""Publish one explicitly acknowledged, pinned screen snapshot."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

import ast

from keel.api_path import seg

from ._screener_commands import NONNEGATIVE_INT, SID, B, S, lines_result, register_command, route


REQUIRED_ACTION = "screener.publish"
DISCLOSURE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "visibility": {"type": "string", "enum": ["public", "unlisted"]},
        "include_source": B,
        "show_author": B,
        "data_depth": {"type": "string", "enum": ["history", "latest"]},
        "discoverability": {"type": "string", "enum": ["unlisted"]},
    },
    "required": ["visibility", "include_source", "show_author", "data_depth", "discoverability"],
}
NOTE_MAX = 280
_post = route("post", "/v1/screener-shares")


def view_description(source):
    """The screen's one-sentence description, ``Page(about=...)``, and the
    first ``Columns`` label, read from view source without executing it.

    Returns ``(about, label)``; either is None when the source does not say.
    """
    try:
        module = ast.parse(source or "")
    except (SyntaxError, ValueError):
        return None, None
    about = label = None
    for stmt in module.body:
        call = stmt.value if isinstance(stmt, ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id == "Page":
            for kw in call.keywords:
                if kw.arg == "about" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str) and kw.value.value.strip():
                        about = kw.value.value.strip()
        elif call.func.id == "Columns" and label is None:
            for kw in call.keywords:
                col = kw.value
                if (
                    isinstance(col, ast.Call)
                    and col.args
                    and isinstance(col.args[0], ast.Constant)
                    and isinstance(col.args[0].value, str)
                ):
                    label = col.args[0].value
                    break
    return about, label


def suggested_description(label, title):
    """A starting sentence the author edits; never published by itself."""
    if label:
        return f"Ranks the screen's coins by {label[0].lower() + label[1:]}."
    return f"{title or 'This screen'}: what it ranks and why, in one sentence."


def _description(args, ctx):
    """``(note, suggestion)``: the caller's note, else the screen's description.

    The share's note is the screen's description (``Page(about=...)``). A
    description longer than the note allows is not cut: the publish goes
    ahead without it and the next step says so.
    """
    if args.get("note"):
        return args["note"], None
    view = ctx.get_client().get(f"/v1/screeners/{seg(str(args['strategy_id']))}/views")
    about, label = view_description((view or {}).get("source"))
    if about and len(about) <= NOTE_MAX:
        return about, None
    return None, suggested_description(label, args.get("title"))


def publish_lines(share, *, suggestion=None):
    """§D1.7: the link, the referral line, what is public, and the next step."""
    lines = [f"Published: {share.get('title') or share.get('share_id')} · {share['url']}"]
    if share.get("note"):
        lines.append(f"Description: {share['note']}")
    lines.append(share["referral_line"])
    summary = share.get("disclosure_summary") or []
    if summary:
        lines.append("What is public:")
        lines += [f"- {item}" for item in summary]
    if suggestion is not None:
        command = f"keel screener describe {share['share_id']} --note {_quote(suggestion)}"
        lines.append(f"This screen has no description. Add a description: {command}")
        lines.append(f"next: {command}")
    elif not (share.get("refresh") or {}).get("registered"):
        lines.append("next: keel_screener_refresh(on=true)")
    return lines


def _quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def _handler(args, ctx):
    note, suggestion = _description(args, ctx)
    body = {**args, "note": note} if note else {k: v for k, v in args.items() if k != "note"}
    share = _post(body, ctx).extra
    return lines_result(
        share, publish_lines(share, suggestion=suggestion), share_url=share.get("url")
    )


TOOL = register_command(
    "screener_publish",
    ("screener", "publish"),
    (
        "Publish an acknowledged screen snapshot pinned to its commit, run and view revision. "
        "Pass note: one plain sentence (under 280 characters) saying what the screen shows; it is "
        "the public page's description. Omitted, it defaults to the view's Page(about=...); with "
        "neither, the screen publishes without one and the result says how to add it. "
        "This creates no refresh schedule."
    ),
    {
        "strategy_id": SID,
        "commit_id": S,
        "screener_run_id": S,
        "view_document_revision": NONNEGATIVE_INT,
        "disclosure": DISCLOSURE,
        "disclosure_acknowledged_at": {
            **S,
            "description": "ISO instant of the explicit disclosure acknowledgement.",
        },
        "title": S,
        "note": {
            **S,
            "maxLength": NOTE_MAX,
            "description": "One plain sentence saying what the screen shows (the public description).",
        },
    },
    [
        "strategy_id",
        "commit_id",
        "screener_run_id",
        "view_document_revision",
        "disclosure",
        "disclosure_acknowledged_at",
    ],
    REQUIRED_ACTION,
    _handler,
    destructive=True,
    toolset="share",
    title="Publish Screen",
    rate_tier="mutation",
)
