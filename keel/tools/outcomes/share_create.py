"""`keel_share_create` — publish a strategy or backtest at a public URL.

Replaces:
- The `sharing_create_link` MCP tool.
- The `keel sharing create-link` CLI command.

Per spec §4 row "share_create" + §8 sharing model:

- The single outcome tool where `share_url` IS non-null on success
  (every other tool returns `share_url = None`).
- Public AND irreversible (2026-10-01, Q-2080; reverses Q-1506): a share
  discloses the selected data publicly, and revoking the link later does
  not retract what a recipient already opened or copied — so it carries
  `destructiveHint=true`. It is a write (`readOnlyHint=false`), and the
  CLI asks before publishing (`confirm_in_cli=true`).
- `target_id` auto-detects from prefix:
    * `str_*` → POST /v1/strategies/{strategy_id}/share-links
    * `btr_*` → POST /v1/backtests/{backtest_run_id}/share-link
- `target_type` is an explicit override when the prefix is ambiguous.
- Both routes are STRICT since Q-2255 (keel-api forbids unknown fields and
  422s a contradictory pair): the recipient's permission is DERIVED from
  `include_source` — source shown ⇒ `fork`, hidden ⇒ `view` — so this tool
  never sends `permission`; a full-profile caller who passes one that
  contradicts `include_source` is refused here, before anything publishes.
  `expires_at` is sent on both routes and must be a future, timezone-aware
  ISO-8601 instant.

The link is returned ready to post (Q-2587, founder 2026-10-09: "for create
share, yes just wait till generation done so it's ready"). Both routes are
called with `wait_for_card=true`, and keel-api — the one owner of the wait,
so the CLI, the local MCP server and the hosted one inherit it identically —
returns once the link-preview card exists, bounded at ~10 s. A link whose
card is not there yet comes back with `preview_note` saying so, because a
crawler that unfurls it before the card lands caches a text-only preview.

Do NOT use to fetch an existing share URL — read the strategy or
backtest resource instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from keel.api_path import seg
from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext, listed_schema
from .open_in_app import app_url_for


def permission_for(include_source: bool) -> str:
    """keel-api's rule (Q-2255): the source shown ⇒ recipients may fork it;
    hidden ⇒ view only. One owner of the derivation on this side."""
    return "fork" if include_source else "view"


def parsed_expiry(value: Any) -> str | None:
    """``expires_at`` as keel-api accepts it: a future, timezone-aware
    ISO-8601 instant, or ``None`` when omitted. Anything else is a usage
    error here — the API would 422 the same call after the fact."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise KeelError(
            f"`expires_at` must be an ISO-8601 datetime string (got {value!r}).",
            error_code="invalid_expires_at",
            exit_code=2,
            suggestion="Pass e.g. `expires_at='2026-12-31T00:00:00Z'`, or omit it for no expiry.",
        )
    text = value.strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        raise KeelError(
            f"`expires_at` is not an ISO-8601 datetime: {value!r}.",
            error_code="invalid_expires_at",
            exit_code=2,
            suggestion="Pass e.g. `expires_at='2026-12-31T00:00:00Z'`, or omit it for no expiry.",
        ) from None
    if when.tzinfo is None:
        raise KeelError(
            f"`expires_at` needs a timezone (got {value!r}).",
            error_code="invalid_expires_at",
            exit_code=2,
            suggestion="Add an offset or `Z`, e.g. `expires_at='2026-12-31T00:00:00Z'`.",
        )
    if when <= datetime.now(UTC):
        raise KeelError(
            f"`expires_at` is in the past ({value!r}); a share cannot expire before it exists.",
            error_code="invalid_expires_at",
            exit_code=2,
            suggestion="Pass a future instant, or omit `expires_at` for no expiry.",
        )
    return when.isoformat()


#: Asks keel-api to hold the create until the share card exists (Q-2587).
#: A query parameter, not a body field: the strict body models refuse
#: unknown fields, and an older keel-api ignores an unknown query parameter.
_WAIT_FOR_CARD = {"wait_for_card": "true"}

PREVIEW_PENDING_NOTE = (
    "The preview image is still generating; wait a few seconds before posting the link."
)
PREVIEW_FAILED_NOTE = (
    "The preview image could not be generated; the link works but will unfurl without an image."
)


def preview_note(result: dict) -> str | None:
    """One honest line when the link's preview card is not ready, else None.

    Silent when keel-api did not report a status, or renders no cards at all
    (`artifacts_enabled=false`): there is no image to wait for."""
    status = result.get("artifacts_status")
    if not status or status == "ready" or result.get("artifacts_enabled") is False:
        return None
    return PREVIEW_FAILED_NOTE if status == "failed" else PREVIEW_PENDING_NOTE


def _infer_target_type(target_id: str) -> str:
    if target_id.startswith("stu_"):
        return "study"
    if target_id.startswith("str_"):
        return "strategy"
    if target_id.startswith("btr_"):
        return "backtest"
    # Unknown prefix — let the caller force it via `target_type`.
    return ""


def _paper_targets_enabled() -> bool:
    """Whether this process serves the `paper` target (S06 §3.7, §3.11 item 1):
    on the full profile once the paper SDK is released, and on listed only
    with the listed flag on too (§3.4.2). Otherwise the tool is HEAD's."""
    from . import _toolsets

    if not _toolsets.PAPER_SDK_RELEASED:
        return False
    return not _toolsets.is_listed_profile() or _toolsets.listed_paper_enabled()


def _share_run(target_id: str, target_type: str, include_source: bool, ctx: ToolContext) -> dict:
    """A `dep_` id's share (S06 §3.7, R-10): the venue block decides, read once
    before any share call. A `practice` row creates through the paper share
    route (S05 IC-05-5); a `real` row is refused, also when the caller passed
    `target_type="paper"`: sharing a live run is not this tool's job. The
    tool never calls a live share route."""
    from ._paper_guard import AGENT_TELEMETRY_HEADERS

    client = ctx.get_client()
    # The share inference read is never an owner visit (S06 §3.5.1, R-61).
    row = client.get(f"/v1/deployments/{seg(target_id)}", headers=AGENT_TELEMETRY_HEADERS)
    venue = row.get("venue") if isinstance(row, dict) else None
    capital_class = venue.get("capital_class") if isinstance(venue, dict) else None
    if capital_class != "practice":
        page = app_url_for("live", target_id, ctx)
        raise KeelError(
            f"{target_id} is a live deployment, not a paper run; this tool shares strategies, "
            "backtests and paper runs only. Nothing was published.",
            error_code="unsupported_target",
            exit_code=2,
            suggestion=f"Share a live run from its page in the Keel web app: {page}",
            input={"target_id": target_id, "target_type": target_type or None},
        )
    # The paper family mirrors the live family's strict configuration body
    # (S05 §3.12.1): every disclosure defaults off; the source switch is the
    # one control this tool carries.
    return client.post(
        f"/v1/paper/{seg(target_id)}/share-links", json={"include_strategy_source": include_source}
    )


#: What each kind-specific argument reads as when the caller leaves it off. The
#: CLI sends a boolean flag the caller did not pass as its default
#: (`_cli_adapter`: `--flag/--no-flag` defaults to False), so a value equal to
#: the argument's default asks for nothing and is never a reason to refuse
#: (RI-281; research_plan's J14.6 rule for `resume`/`retry`). A value that asks
#: for something this kind of share cannot do is still refused.
_UNSET: dict[str, tuple[Any, ...]] = {
    "include_source": (None, False),
    "permission": (None, "", "view"),
    "expires_at": (None, ""),
    "show_author": (None, False),
    "views": (None,),
}


def _given(args: dict, keys: tuple[str, ...]) -> list[str]:
    """The `keys` the caller set to something other than their default."""
    return [k for k in keys if args.get(k) not in _UNSET[k]]


def _share_study(target_id: str, args: dict, ctx: ToolContext) -> OutcomeResult:
    """A study's snapshot share (Research 07 §2.2, 08 §3.6): the views it shows
    and whether the author's name is on it. A study share never serves the
    source, so `include_source`, `permission` and `expires_at` are refused when
    they ask for anything but the default (source hidden, view only, no expiry).

    The listed profile does not share a study here (07 §2.2): its schema omits
    `views` and `show_author`, so the adapter drops them and a share made here
    would publish every view whatever the agent asked. It is refused, and the
    agent opens the study with `keel_app_link`, where the owner picks the views."""
    from . import _toolsets

    if _toolsets.is_listed_profile():
        raise KeelError(
            "On this connector a study is shared from its page in the Keel web app, where you "
            "pick the views it shows. Nothing was published.",
            error_code="unsupported_target",
            exit_code=2,
            suggestion=f"Open the study with `keel_app_link` (target_id='{target_id}') and share it there.",
            input={"target_id": target_id},
        )
    given = _given(args, ("include_source", "permission", "expires_at"))
    if given:
        raise KeelError(
            f"A study share never shows the study's source: {', '.join(given)} "
            "cannot be set. Nothing was published.",
            error_code="invalid_arguments",
            exit_code=2,
            suggestion="Send `views` and `show_author` only.",
        )
    body: dict[str, Any] = {"show_author": bool(args.get("show_author", False))}
    if args.get("views") is not None:
        # The CLI hands `--views 0 --views 3` over as text; the route takes the
        # indexes as integers, the shape the MCP schema already sends.
        try:
            body["views"] = [int(v) for v in args["views"]]
        except (TypeError, ValueError):
            raise KeelError(
                f"`views` takes view indexes (0, 1, ...); got {list(args['views'])!r}. "
                "Nothing was published.",
                error_code="invalid_arguments",
                exit_code=2,
                suggestion="Pass the indexes of the views to show, e.g. `--views 0 --views 3`.",
            ) from None
    result = ctx.get_client().post(f"/v1/research/studies/{seg(target_id)}/shares", json=body)
    share_id = result.get("share_id") or ""
    if not share_id:
        raise KeelError(
            "Share creation succeeded but no share_id was returned.",
            error_code="malformed_response",
            suggestion="Run `keel_connection_check`; check API version compatibility.",
        )
    share_url = result.get("url") or app_url_for("share", share_id, ctx)
    return OutcomeResult(
        share_url=share_url,
        extra={
            "share_id": share_id,
            "share_type": "study",
            "study_id": result.get("study_id", target_id),
            "version": result.get("version"),
            "views": result.get("views"),
            "show_author": result.get("show_author"),
        },
    )


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    target_id: str = (args.get("target_id") or "").strip()
    if not target_id:
        raise KeelError(
            "Missing required `target_id` (str_xxx or btr_xxx).",
            error_code="missing_target_id",
            exit_code=2,
            suggestion=(
                "Pass a strategy id (str_*) or a backtest id (btr_*) as the first argument."
            ),
        )

    # Resolve target_type — explicit override beats prefix inference.
    target_type: str = (args.get("target_type") or "").strip()
    paper_targets = _paper_targets_enabled()
    if not target_type:
        target_type = _infer_target_type(target_id)
        if not target_type and paper_targets and target_id.startswith("dep_"):
            # A `dep_` id is a live deployment or a paper run; the prefix
            # cannot say which, so the venue block decides (§3.7).
            target_type = "paper"
    if target_type == "study":
        return _share_study(target_id, args, ctx)
    if _given(args, ("views", "show_author")):
        raise KeelError(
            "`views` and `show_author` are for a study share (stu_*); nothing was published.",
            error_code="invalid_arguments",
            exit_code=2,
            suggestion="Omit them for a strategy, backtest or paper run share.",
        )
    allowed = {"strategy", "backtest"} | ({"paper"} if paper_targets else set())
    if target_type not in allowed:
        raise KeelError(
            f"Cannot infer share target from id {target_id!r}.",
            error_code="ambiguous_target",
            exit_code=2,
            suggestion=(
                "Pass `target_type='strategy'` or `target_type='backtest'` "
                "explicitly when the id prefix is non-standard."
            ),
        )

    # The capability is applied EXACTLY as requested (Anthropic directory
    # review, 2026-10-01; Q-2255): `include_source` and `permission` are one
    # control (source shown ⇔ `fork`, hidden ⇔ `view`). Either may be given
    # alone and decides; both may be given if they agree; a disagreeing pair
    # is refused HERE, before anything is published. Neither ⇒ view-only with
    # the source hidden. `include_source` carries no schema default so an
    # omitted value is distinguishable from an explicit `false` over the wire.
    requested = args.get("permission")
    if requested in ("", None):
        requested = None
    elif requested not in {"view", "fork"}:
        raise KeelError(
            f"Invalid permission {requested!r}; expected 'view' or 'fork'.",
            error_code="invalid_permission",
            exit_code=2,
            suggestion="`view` keeps the source hidden; `fork` shows it and lets viewers copy it.",
        )
    raw_include = args.get("include_source")
    if raw_include is not None:
        include_source = bool(raw_include)
    else:
        include_source = requested == "fork"
    permission = permission_for(include_source)
    if requested is not None and requested != permission:
        raise KeelError(
            f"`permission={requested!r}` contradicts `include_source={include_source}`.",
            error_code="invalid_permission",
            exit_code=2,
            suggestion=(
                "`include_source=true` lets viewers see and fork the source (`fork`); "
                "`false` keeps it hidden (`view`). Give one of them, or both matching."
            ),
        )

    expires_at = parsed_expiry(args.get("expires_at"))

    client = ctx.get_client()

    if target_type == "paper":
        if expires_at:
            raise KeelError(
                "`expires_at` is not available for a paper run share; nothing was published.",
                error_code="invalid_expires_at",
                exit_code=2,
                suggestion="Omit `expires_at`; the link can be revoked from the run's page.",
            )
        try:
            result = _share_run(target_id, args.get("target_type") or "", include_source, ctx)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to create paper run share link for {target_id}: {e}",
                suggestion="Run `keel_connection_check` to diagnose auth issues.",
            )
    elif target_type == "strategy":
        # Exactly the fields the strict route declares (Q-2255): the source
        # switch, the expiry, and the pinned latest backtest so the share
        # card always has metrics. Never `permission` — derived server-side.
        body: dict[str, Any] = {"include_source": include_source}
        if expires_at:
            body["expires_at"] = expires_at
        body["pin_latest_backtest"] = True
        try:
            result = client.post(
                f"/v1/strategies/{seg(target_id)}/share-links", json=body, **_WAIT_FOR_CARD
            )
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to create strategy share link for {target_id}: {e}",
                suggestion=(
                    "Verify the strategy_id and that you own it. "
                    "Run `keel_connection_check` to diagnose auth issues."
                ),
            )
    else:  # backtest
        body = {"include_source": include_source}
        if expires_at:
            body["expires_at"] = expires_at
        try:
            result = client.post(
                f"/v1/backtests/{seg(target_id)}/share-link", json=body, **_WAIT_FOR_CARD
            )
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to create backtest share link for {target_id}: {e}",
                suggestion=(
                    "Verify the backtest_run_id is COMPLETED and in your org. "
                    "Run `keel_connection_check` to diagnose auth issues."
                ),
            )

    share_id = result.get("share_id") or result.get("id") or ""
    if not share_id:
        raise KeelError(
            "Share creation succeeded but no share_id was returned.",
            error_code="malformed_response",
            suggestion="Run `keel_connection_check`; check API version compatibility.",
        )

    # The creator's referral code comes back on the create response (Q-2076),
    # so the share URL drives credit back to the creator. It used to be read
    # from GET /v1/me, which never carried one, and GET /v1/referrals/code
    # needs referral.read, which a hosted-connector token does not hold. An
    # older keel-api omits the field: the URL then carries no ref, as before.
    ref_code = result.get("referral_code") or ""

    share_url = app_url_for("share", share_id, ctx)
    if ref_code:
        share_url = f"{share_url}?{urlencode({'ref': ref_code})}"

    extra: dict[str, Any] = {
        "share_id": share_id,
        "share_type": result.get("share_type") or target_type,
        "include_source": bool(result.get("include_source", include_source)),
        "permission": result.get("permission") or permission,
        "expires_at": result.get("expires_at"),
    }
    note = preview_note(result)
    if note:
        extra["preview_note"] = note

    return OutcomeResult(
        run_id=share_id,
        hero_url=f"{ctx.app_url}/share-links",
        share_url=share_url,
        extra=extra,
    )


SHARE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["target_id"],
    "properties": {
        "target_id": {
            "type": "string",
            "x-cli-positional": True,
            "description": (
                "str_xxx (strategy share) or btr_xxx (backtest share). Type "
                "inferred from prefix unless target_type is set."
            ),
        },
        "target_type": {
            "type": "string",
            "enum": ["strategy", "backtest"],
            "description": ("Explicit override when the id prefix is ambiguous."),
        },
        "include_source": {
            "type": "boolean",
            "description": (
                "Decides whether anyone with the link can see and copy the strategy's "
                "DSL source — on a strategy share and on a backtest share alike. `false` "
                "(the default) still publishes the name, description, backtest metrics, "
                "fork count, live status, market count and risk settings; `true` adds "
                "the source."
            ),
        },
        "permission": {
            "type": "string",
            "enum": ["view", "fork"],
            "description": (
                "`view` (source hidden) or `fork` (source shown and copyable): the same "
                "choice as `include_source`. Give either one, or both if they agree; a "
                "disagreeing pair is refused before anything is published. Default: `view`."
            ),
        },
        "expires_at": {
            "type": "string",
            "description": (
                "Optional future ISO-8601 instant, with a timezone, after which the "
                "link stops working. Default: no expiry."
            ),
        },
        # A study (`stu_*`): its snapshot, never its source (Research 08 §3.1).
        "views": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "description": "For a study (stu_*): the view indexes the snapshot shows. Default: every view.",
        },
        "show_author": {
            "type": "boolean",
            "description": "For a study (stu_*): show your name on the snapshot. Default: false.",
        },
    },
}


#: The held paper form of the schema (S06 §3.7; §3.11 item 1): `paper` joins
#: the target enum and `dep_` ids are named. Listed gets its twin only with
#: the listed flag on (§3.4.2).
PAPER_SHARE_INPUT_SCHEMA: dict[str, Any] = {
    **SHARE_INPUT_SCHEMA,
    "properties": {
        **SHARE_INPUT_SCHEMA["properties"],
        "target_id": {
            "type": "string",
            "x-cli-positional": True,
            "description": (
                "str_xxx (strategy share), btr_xxx (backtest share) or dep_xxx (a paper "
                "run, simulated). Type inferred from prefix unless target_type is set; "
                "a dep_ id is read first and shared only if it is a paper run."
            ),
        },
        "target_type": {
            "type": "string",
            "enum": ["strategy", "backtest", "paper"],
            "description": "Explicit override when the id prefix is ambiguous.",
        },
    },
}

_DESCRIPTION = (
    "Publish a strategy (`str_*`) or a backtest (`btr_*`) at a public "
    "usekeel.io/share/<id> URL. The page shows the strategy's name, "
    "description, backtest metrics, fork count, live status, market count and "
    "risk settings; `include_source` (or `permission`) decides whether viewers "
    "also see and can copy the DSL source. `expires_at` ends it later. What "
    "recipients copied cannot be taken back. Returns `share_url`, `share_id` "
    "and the applied `include_source`, `permission` and `expires_at`."
)

#: §3.7's touch-up, in a form clean for the listed scan too (§3.4.4): it
#: names no verb the listed rules ban, and `paper run` is not a compound.
_PAPER_SENTENCE = (
    " A paper run (`dep_*`, simulated) is shared the same way: its page is "
    "stamped as simulated. A `dep_*` id that is not a paper run is refused "
    "(`unsupported_target`); share those from their page in the Keel web app."
)


SHARE_CREATE = register(
    OutcomeTool(
        name="keel_share_create",
        required_action="sharing.create",
        rate_tier="mutation",
        cli_path=("share", "create"),
        toolset="share",
        # grounded-in: share_create.py module docstring (spec §4 + §8 sharing
        # model — the one tool that returns a non-null share_url; it makes
        # content public, a write rather than a destructive update);
        # context-architecture-design
        # (default app URLs are authenticated + private).
        description=_DESCRIPTION,
        input_schema=SHARE_INPUT_SCHEMA,
        # The listed twin keeps `permission` (Anthropic review, 2026-10-01):
        # it is the same control as `include_source`, applied as requested.
        listed_input_schema=listed_schema("keel_share_create", SHARE_INPUT_SCHEMA),
        # The `paper` target (S06 §3.7): held with the paper SDK on the full
        # profile (§3.11 item 1), and on listed also behind the listed flag.
        paper_description=_DESCRIPTION + _PAPER_SENTENCE,
        paper_input_schema=PAPER_SHARE_INPUT_SCHEMA,
        listed_paper_description=_DESCRIPTION + _PAPER_SENTENCE,
        listed_paper_input_schema=listed_schema("keel_share_create", PAPER_SHARE_INPUT_SCHEMA),
        annotations={
            "title": "Create Public Share Link",
            "readOnlyHint": False,
            # Irreversible (2026-10-01, Q-2080; reverses Q-1506): the link
            # discloses the shared data publicly, and revoking it later does
            # not retract what a recipient already opened or copied. OpenAI's
            # rule holds destructiveHint to irreversible OUTCOMES, not only
            # to deletes — a host prompting on every share is the price.
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
        confirm_in_cli=True,
    )
)
