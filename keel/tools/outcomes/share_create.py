"""`keel_share_create` — publish a strategy or backtest at a public URL.

Replaces:
- The `sharing_create_link` MCP tool.
- The `keel sharing create-link` CLI command.

Per spec §4 row "share_create" + §8 sharing model:

- The single outcome tool where `share_url` IS non-null on success
  (every other tool returns `share_url = None`).
- Destructive (privacy): publishing data makes it world-readable. Always
  routes through host confirmation (`destructiveHint=true`,
  `confirm_in_cli=true`).
- `target_id` auto-detects from prefix:
    * `str_*` → POST /v1/strategies/{strategy_id}/share-links
    * `btr_*` → POST /v1/backtests/{backtest_run_id}/share-link
- `target_type` is an explicit override when the prefix is ambiguous.

Do NOT use to fetch an existing share URL — read the strategy or
backtest resource instead.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


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

    include_source: bool = bool(args.get("include_source", False))
    permission: str = args.get("permission") or "view"
    if permission not in {"view", "fork"}:
        raise KeelError(
            f"Invalid permission {permission!r}; expected 'view' or 'fork'.",
            error_code="invalid_permission",
            exit_code=2,
            suggestion=(
                "Pass `permission='view'` (read-only) or `permission='fork'` "
                "(recipient can fork into their own strategy). Defaults to 'view'."
            ),
        )

    expires_at = args.get("expires_at")

    client = ctx.get_client()

    if target_type == "strategy":
        body: dict[str, Any] = {
            "permission": permission,
            "include_source": include_source,
        }
        if expires_at:
            body["expires_at"] = expires_at
        # Pin latest backtest by default so the share card always has
        # metrics. Callers can override later by recreating the link.
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
                    "Run `keel_doctor` to diagnose auth issues."
                ),
            )
    else:  # backtest
        body = {"include_source": include_source}
        try:
            result = client.post(f"/v1/backtests/{target_id}/share-link", json=body)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to create backtest share link for {target_id}: {e}",
                suggestion=(
                    "Verify the backtest_run_id is COMPLETED and in your org. "
                    "Run `keel_doctor` to diagnose auth issues."
                ),
            )

    share_id = result.get("share_id") or result.get("id") or ""
    if not share_id:
        raise KeelError(
            "Share creation succeeded but no share_id was returned.",
            error_code="malformed_response",
            suggestion="Run `keel_doctor`; check API version compatibility.",
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

    share_url = f"{ctx.share_url_root}/{share_id}"
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


SHARE_CREATE = register(
    OutcomeTool(
        name="keel_share_create",
        required_action="sharing.create",
        cli_path=("share", "create"),
        toolset="share",
        description=(
            "Publish a strategy or a backtest result at a public "
            "usekeel.io/share/<id> URL. THIS MAKES SELECTED DATA "
            "WORLD-READABLE. Always routes through host confirmation. "
            "The default surface returns authenticated app.usekeel.io URLs; "
            "only call this when the user explicitly asks to share publicly. "
            "Do NOT use to fetch an existing share URL — read the strategy "
            "or backtest resource instead."
        ),
        input_schema={
            "type": "object",
            "required": ["target_id"],
            "properties": {
                "target_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "str_xxx (strategy share) or btr_xxx (backtest "
                        "share). Type inferred from prefix unless "
                        "target_type is set."
                    ),
                },
                "target_type": {
                    "type": "string",
                    "enum": ["strategy", "backtest"],
                    "description": ("Explicit override when the id prefix is ambiguous."),
                },
                "include_source": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Only meaningful when target_type='strategy'. "
                        "When true, the share URL exposes the Pipeline DSL "
                        "source (forkable, readable). When false, only the "
                        "tearsheet + metrics are public."
                    ),
                },
                "permission": {
                    "type": "string",
                    "enum": ["view", "fork"],
                    "default": "view",
                    "description": (
                        "Recipients can either view-only ('view') or fork "
                        "into their own workspace ('fork'). Forking requires "
                        "include_source=true for strategy shares."
                    ),
                },
                "expires_at": {
                    "type": "string",
                    "description": (
                        "Optional ISO-8601 timestamp at which the share "
                        "auto-revokes. Default: never."
                    ),
                },
            },
        },
        annotations={
            "title": "Create Public Share Link",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
        confirm_in_cli=True,
    )
)
