"""Small route adapters shared by the full-profile screen and Use commands."""

from keel.api_path import seg
from keel.errors import EntitlementError, KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool
from ._screener_view import invalid


S = {"type": "string"}
B = {"type": "boolean"}
NONNEGATIVE_INT = {"type": "integer", "minimum": 0}
SID = {**S, "x-cli-positional": True, "description": "Screen or strategy identifier."}
DRY = {**B, "default": False, "description": "Validate and preview without writing."}


def register_command(
    name,
    path,
    description,
    properties,
    required,
    action,
    handler,
    *,
    read=False,
    destructive=False,
    idempotent=False,
    toolset=None,
    title=None,
    cli_only=False,
    listed_input_schema=None,
    listed_title=None,
    rate_tier=None,
):
    if "dry_run" in properties:
        route_handler = handler

        def handler(args, ctx):
            # CLI flags live in ToolContext; MCP arguments stay in args.
            return route_handler(
                {**args, "dry_run": bool(ctx.dry_run or args.get("dry_run", False))}, ctx
            )

    handler = _with_refusals(handler, screen=name.startswith("screener_"))
    return register(
        OutcomeTool(
            name="keel_" + name,
            cli_path=path,
            toolset=toolset or ("read-only" if read else "backtest"),
            description=description,
            input_schema={"type": "object", "properties": properties, "required": required},
            listed_input_schema=listed_input_schema,
            listed_title=listed_title,
            cli_only=cli_only,
            annotations={
                # A human title (approval prompts show it); the name-derived
                # one ("Screener view set") read as a machine label.
                "title": title or name.replace("_", " ").capitalize(),
                "readOnlyHint": read,
                "destructiveHint": destructive,
                "idempotentHint": idempotent,
                "openWorldHint": False,
            },
            handler=handler,
            required_action=action,
            rate_tier=rate_tier,
            confirm_in_cli=destructive,
        )
    )


def route(method, template, *, rename=None, public=False, omit=()):
    import re

    fields = re.findall(r"{(\w+)}", template)

    def handler(args, ctx):
        for key in fields:
            if not args.get(key):
                invalid(f"{key} is required.")
        path = template.format(**{key: seg(str(args[key])) for key in fields})
        body = {
            (rename or {}).get(k, k): v
            for k, v in args.items()
            if k not in {*fields, *omit} and v is not None
        }
        client = ctx.get_client()
        if method == "get":
            result = (client.get_public if public else client.get)(path, **body)
        elif method == "delete":
            result = client.delete(path, **body)
        else:
            result = getattr(client, method)(path, json=body)
        return OutcomeResult(extra=result if isinstance(result, dict) else {"items": result})

    return handler


# ---------- results and refusals (spec 06 §D1.7, INV-24) ----------


def lines_result(extra, lines, *, share_url=None):
    """The route's response as structured data, and its human lines as the
    text every surface prints (the CLI prints them verbatim; MCP hosts read
    them as the text block). ``share_url`` stays the envelope's explicit link."""
    text = "\n".join(line for line in lines if line)
    return OutcomeResult(
        share_url=share_url,
        extra={**extra, "view": {"kind": "screener", "size": "receipt", "markdown": text}},
    )


def _publish_args(args, detail):
    return {k: v for k, v in args.items() if k != "dry_run"}


#: spec 06 §D1.7's refusal table: the API's code → (the tool that recovers,
#: its arguments from the call and the refusal's detail, what was expected).
#: ``message`` stays the API's own sentence (keel-api renders it from
#: ``screeners.copy.REFUSAL_WORDS``); this table adds only the next step.
REFUSALS = {
    "SCREENER_ALREADY_PUBLISHED": (
        None,
        None,
        "This screen already has a link; update it from the screen in Keel.",
    ),
    "SCREENER_PUBLISH_DISABLED": (None, None, "Publishing screens is not open on this account."),
    "SCREENER_SHARE_NOT_A_SCREENER": (
        "keel_share_create",
        lambda a, d: {"strategy_id": a.get("strategy_id")},
        "The entry is a strategy; strategies share through keel_share_create.",
    ),
    "SCREENER_SHARE_RUN_NOT_PUBLISHABLE": (
        "keel_screener_run",
        lambda a, d: {"strategy_id": a.get("strategy_id")},
        "A publish needs a completed, unexpired saved run of that version.",
    ),
    "SCREENER_VIEW_DOCUMENT_REVISION_CONFLICT": (
        "keel_screener_publish",
        lambda a, d: {**_publish_args(a, d), "view_document_revision": d.get("current")},
        "Publish again with the view revision the refusal names (`current`).",
    ),
    "SCREENER_PRESENTATION_INADMISSIBLE": (
        "keel_screener_view_set",
        lambda a, d: {"strategy_id": a.get("strategy_id"), "dry_run": True},
        "Fix the view against the published run, then publish.",
    ),
    "SCREENER_DISCLOSURE_VIEW_CONFLICT": (
        None,
        None,
        "Change the disclosure fields or the named views.",
    ),
    "SCREENER_DISCOVERY_DISABLED": (
        "keel_screener_publish",
        lambda a, d: {
            **_publish_args(a, d),
            "disclosure": {**(a.get("disclosure") or {}), "discoverability": "unlisted"},
        },
        "Listing waits for a moderation pass; publish it unlisted.",
    ),
    "SCREENER_SNAPSHOT_COPY_FAILED": (
        "keel_screener_publish",
        _publish_args,
        "Nothing was written; publish again.",
    ),
    "SCREENER_REFRESH_NOT_LIVE_VERSION": (
        "keel_screener_refresh",
        lambda a, d: {"strategy_id": a.get("strategy_id")},
        "The published version moved; refresh again reads it afresh.",
    ),
    "SCREENER_AUTO_REFRESH_DISABLED": (None, None, "Auto-refresh is not open on this account."),
    "SCREEN_FOLLOW_OWN": (None, None, "This is your own screen; there is nothing to follow."),
    "SCREEN_COMMUNITY_COPY_NONE": (
        "keel_screener_view",
        lambda a, d: {"share_id": a.get("share_id")},
        "No community copy was made; the dated snapshot stays readable.",
    ),
    "SCREEN_FOLLOW_VIEW_FIXED": (
        None,
        None,
        "The author's view declares no control, so there is nothing to set.",
    ),
    "SCREEN_FOLLOW_VIEW_NOT_A_CONTROL": (
        None,
        None,
        "Only the controls the author's view declares can be set.",
    ),
    "SCREEN_FOLLOW_VIEW_OUT_OF_RANGE": (
        None,
        None,
        "A value inside the range the author's control declares.",
    ),
    "SCREEN_FOLLOW_VIEW_NOT_WHOLE": (
        None,
        None,
        "A whole number: the control is a count of coins or bars.",
    ),
    "SCREENER_SHARE_DEMOTION_PENDING": (
        None,
        None,
        "The last update is finishing; retry in a few minutes.",
    ),
    "SCREENER_REFRESH_BELOW_FLOOR": (
        None,
        None,
        "Auto-refresh needs the screen's Globals target_timeframe at the plan's floor "
        "(`floor_minutes`) or slower; change it, save, then turn auto-refresh on.",
    ),
    "SCREENER_VIEWS_CONFLICT": (
        "keel_screener_view",
        lambda a, d: {"strategy_id": a.get("strategy_id")},
        "The view was saved elsewhere first: read its source again, apply the change to it, "
        "and save with the revision the refusal names (`current_revision`).",
    ),
    "SCREENER_SHARE_REVISION_CONFLICT": (
        None,
        None,
        "The link changed elsewhere first: read it again and retry with the revision the "
        "refusal names (`current`).",
    ),
    "SCREENER_STORAGE_UNAVAILABLE": (
        None,
        None,
        "Stored results could not be read just now; retry the same call in a minute.",
    ),
    "platform:refresh_capacity_exhausted": (
        None,
        None,
        "No auto-refresh capacity is free; keep the screen without auto-refresh or turn "
        "another one off. Retrying unchanged does not help.",
    ),
    "platform:refresh_capacity_unconfigured": (
        None,
        None,
        "Auto-refresh is briefly unavailable; retry in a few minutes.",
    ),
}
#: Whether retrying the same call can succeed by itself, per refusal code
#: (the API's own ``retryable`` wins when it states one).
RETRYABLE = {
    "SCREENER_SHARE_DEMOTION_PENDING": True,
    "SCREENER_STORAGE_UNAVAILABLE": True,
    "platform:refresh_capacity_unconfigured": True,
    "platform:refresh_capacity_exhausted": False,
    "SCREENER_REFRESH_BELOW_FLOOR": False,
    "SCREENER_VIEWS_CONFLICT": False,
    "SCREENER_SHARE_REVISION_CONFLICT": False,
}
#: The two feature-flag refusals carry their own SDK code (§D1.6's pattern).
DISABLED_CODES = {
    "SCREENER_PUBLISH_DISABLED": "screener_publish_disabled",
    "SCREENER_AUTO_REFRESH_DISABLED": "screener_auto_refresh_disabled",
}
#: The anonymous tier's publish refusal (spec 07 §D9): a cap of 0 on public
#: screens, and the work carries over on sign-in. keel-api's 403 sentence is
#: replaced by the SDK's plan wall, so the carry-over is said here: keel-api's
#: ``errors.ANON_CARRY_OVER`` verbatim, without the sign-up URL (D-12). A
#: parity test pins the two equal (O-23).
ANON_CARRY_OVER = (
    "Sign in (`keel auth login`) or create a free account to continue — "
    "your work carries over automatically"
)


#: A screen read with no completed run behind it (Q-3634): keel-api's
#: ``SCREENER_NOT_RUN_YET`` (contract 4), and the bare ``RESULT_RUN_NOT_FOUND``
#: an older keel-api answers the same read with. Hit on every new screen.
NOT_RUN_CODES = frozenset({"SCREENER_NOT_RUN_YET", "RESULT_RUN_NOT_FOUND"})
#: The sentence when keel-api sent none (its own sentence always wins).
NOT_RUN_WORDS = "This screen hasn't run yet. Run it first."
NO_RUN_WORDS = "No screen run was found."
NOT_RUN_EXPECTED = (
    "A completed run of this screen; keel_screener_run runs it, and its result is the screen."
)
#: Every other "not found" on a screen tool: keel-api's generic 404 advice
#: names strategies, versions and backtests, none of which a screen id is.
SCREEN_NOT_FOUND_EXPECTED = (
    "A screen id you can read: keel_screener_list lists your screens, the ones "
    "you follow and Keel's, with the id keel_screener_view takes."
)


def _bare(err):
    """keel-api sent no sentence: the client's placeholder stands in for one."""
    message = str(err)
    return message.startswith("API error (fields:") or message in ("", "Resource not found")


def refusal_code(err):
    detail = err.detail if isinstance(getattr(err, "detail", None), dict) else {}
    code = detail.get("code")
    return code if isinstance(code, str) else err.error_code


def _not_run(err, args, detail):
    """A screen with no completed run: one plain sentence, and the run as the
    next step (or the list, when no screen was named)."""
    sid = args.get("strategy_id") or detail.get("strategy_id")
    if sid and not args.get("run_id"):
        if _bare(err):
            err.args = (NOT_RUN_WORDS,)
        err.error_code = "SCREENER_NOT_RUN_YET"
        err.recovery_tool, err.recovery_tool_args = "keel_screener_run", {"strategy_id": sid}
        err.suggestion = NOT_RUN_EXPECTED
    else:
        if _bare(err):
            err.args = (NO_RUN_WORDS,)
        err.recovery_tool, err.recovery_tool_args = "keel_screener_list", {}
        err.suggestion = SCREEN_NOT_FOUND_EXPECTED
    err.retryable = False
    return err


def apply_refusal(err, args, *, screen=False):
    """Set §D1.7's next step on a screener refusal; other errors pass through.

    ``screen`` marks a screen tool: its not-found refusals name the screen's
    own next step, never the generic advice about backtests (Q-3634).
    """
    if (
        isinstance(err, EntitlementError)
        and (getattr(err, "input", None) or {}).get("plan") == "anon"
    ):
        err.args = (f"{err} {ANON_CARRY_OVER}.".strip(),)
        err.recovery_tool, err.recovery_tool_args = None, None
        return err
    code = refusal_code(err)
    detail = err.detail if isinstance(getattr(err, "detail", None), dict) else {}
    if screen and code in NOT_RUN_CODES:
        return _not_run(err, args, detail)
    row = REFUSALS.get(code)
    if row is None:
        if screen and isinstance(err, NotFoundError):
            err.recovery_tool, err.recovery_tool_args = "keel_screener_list", {}
            err.suggestion = SCREEN_NOT_FOUND_EXPECTED
        return err
    tool, build, expected = row
    if code in DISABLED_CODES:
        err.error_code, err.exit_code, err.retryable = DISABLED_CODES[code], 6, False
    if code in RETRYABLE:
        stated = detail.get("retryable")
        err.retryable = stated if isinstance(stated, bool) else RETRYABLE[code]
    err.recovery_tool = tool
    err.recovery_tool_args = build(args, detail) if build else None
    err.suggestion = expected
    return err


def _with_refusals(handler, *, screen=False):
    def guarded(args, ctx):
        try:
            return handler(args, ctx)
        except KeelError as err:
            apply_refusal(err, args, screen=screen)
            raise

    return guarded
