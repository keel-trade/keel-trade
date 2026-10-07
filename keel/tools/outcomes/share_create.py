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

Do NOT use to fetch an existing share URL — read the strategy or
backtest resource instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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


def _infer_target_type(target_id: str) -> str:
    if target_id.startswith("str_"):
        return "strategy"
    if target_id.startswith("btr_"):
        return "backtest"
    # Unknown prefix — let the caller force it via `target_type`.
    return ""


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
    if not target_type:
        target_type = _infer_target_type(target_id)
    if target_type not in {"strategy", "backtest"}:
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

    if target_type == "strategy":
        # Exactly the fields the strict route declares (Q-2255): the source
        # switch, the expiry, and the pinned latest backtest so the share
        # card always has metrics. Never `permission` — derived server-side.
        body: dict[str, Any] = {"include_source": include_source}
        if expires_at:
            body["expires_at"] = expires_at
        body["pin_latest_backtest"] = True
        try:
            result = client.post(f"/v1/strategies/{target_id}/share-links", json=body)
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
            result = client.post(f"/v1/backtests/{target_id}/share-link", json=body)
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

    # Look up referral code (best effort) so the share URL drives credit
    # back to the creator. Failures here MUST NOT block the response.
    ref_code = ""
    try:
        identity = client.get("/v1/me")
        ref_code = (identity or {}).get("referral_code") or (identity or {}).get("ref") or ""
    except Exception:  # noqa: BLE001
        # Identity probe is purely cosmetic for the share URL.
        ref_code = ""

    share_url = app_url_for("share", share_id, ctx)
    if ref_code:
        share_url = f"{share_url}?ref={ref_code}"

    extra: dict[str, Any] = {
        "share_id": share_id,
        "share_type": result.get("share_type") or target_type,
        "include_source": bool(result.get("include_source", include_source)),
        "permission": result.get("permission") or permission,
        "expires_at": result.get("expires_at"),
    }

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
    },
}


SHARE_CREATE = register(
    OutcomeTool(
        name="keel_share_create",
        required_action="sharing.create",
        cli_path=("share", "create"),
        toolset="share",
        # grounded-in: share_create.py module docstring (spec §4 + §8 sharing
        # model — the one tool that returns a non-null share_url; it makes
        # content public, a write rather than a destructive update);
        # context-architecture-design
        # (default app URLs are authenticated + private).
        description=(
            "Publish a strategy (`str_*`) or a backtest (`btr_*`) at a public "
            "usekeel.io/share/<id> URL. The page shows the strategy's name, "
            "description, backtest metrics, fork count, live status, market count and "
            "risk settings; `include_source` (or `permission`) decides whether viewers "
            "also see and can copy the DSL source. `expires_at` ends it later. What "
            "recipients copied cannot be taken back. Returns `share_url`, `share_id` "
            "and the applied `include_source`, `permission` and `expires_at`."
        ),
        input_schema=SHARE_INPUT_SCHEMA,
        # The listed twin keeps `permission` (Anthropic review, 2026-10-01):
        # it is the same control as `include_source`, applied as requested.
        listed_input_schema=listed_schema("keel_share_create", SHARE_INPUT_SCHEMA),
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
