"""`keel open` — open the canonical Keel app page in the browser.

Spec 06 R4: ``keel open <strategy|backtest|live> <id>`` builds the
canonical web-app URL and launches the local browser. LOCAL machines
only — a hosted server must never attempt a browser open (guarded via
``keel.hosting.is_hosted``).

URL routing is `keel.tools.outcomes.open_in_app.app_url_for` — the SAME
single source of truth behind `keel_app_link` (MCP) and `keel app
open` (URL-printing CLI twin). This verb adds exactly one behavior on
top: the browser launch (via the same ``_try_open_browser`` helper the
OAuth login flow uses).
"""

from __future__ import annotations

import click


_KINDS = ("strategy", "backtest", "live")

# Prefix sanity: when the id carries a known prefix it must agree with
# the requested kind — `keel open backtest str_x` is a mistake, not a
# preference.
_KIND_PREFIXES = {"strategy": "str_", "backtest": "btr_"}


@click.command("open")
@click.argument("kind", type=click.Choice(_KINDS))
@click.argument("target_id")
@click.pass_context
def open_cmd(ctx: click.Context, kind: str, target_id: str) -> None:
    """Open a strategy, backtest, or live deployment in your browser.

    Examples: `keel open backtest btr_01ABC`, `keel open strategy
    str_01ABC`, `keel open live dep_01ABC`. Prints the URL either way,
    so the link works even when no browser can be launched.
    """
    import os
    import sys

    from keel.errors import KeelError
    from keel.hosting import is_hosted
    from keel.output import emit_error
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes.open_in_app import app_url_for, kind_for_id

    fmt = (ctx.obj or {}).get("format", "human")

    def _fail(err: KeelError) -> None:
        emit_error(err, "json" if fmt == "json" else "human")
        raise click.exceptions.Exit(err.exit_code)

    target_id = target_id.strip()
    implied = kind_for_id(target_id)
    if implied == "share":
        _fail(
            KeelError(
                f"{target_id!r} is a share id — `keel open` takes strategy, "
                "backtest, or live targets.",
                error_code="share_id_not_openable_here",
                exit_code=2,
                suggestion=(
                    "Use `keel app open " + target_id + "` to print the public "
                    "share URL, then open it directly."
                ),
            )
        )
    if implied is not None and implied != kind:
        _fail(
            KeelError(
                f"Id {target_id!r} looks like a {implied} id, not a {kind} id.",
                error_code="kind_id_mismatch",
                exit_code=2,
                suggestion=f"Run `keel open {implied} {target_id}` instead.",
            )
        )
    if kind in _KIND_PREFIXES and implied is None:
        _fail(
            KeelError(
                f"Id {target_id!r} does not look like a {kind} id "
                f"(expected the {_KIND_PREFIXES[kind]!r} prefix).",
                error_code="unknown_id_prefix",
                exit_code=2,
                suggestion=("Find ids via `keel strategy search` or `keel backtest run`."),
            )
        )

    # LOCAL ONLY (spec 06 R4): a hosted pod has no browser and must
    # never pretend to open one.
    if is_hosted():
        _fail(
            KeelError(
                "`keel open` launches a local browser and is not available on hosted servers.",
                error_code="open_not_available_hosted",
                exit_code=2,
                suggestion=(
                    "Call `keel_app_link` instead — it returns the same "
                    "canonical URL as a link for the user."
                ),
            )
        )

    _app_url = os.environ.get("KEEL_APP_URL")
    tool_ctx = ToolContext(
        is_tty=sys.stdout.isatty(),
        **({"app_url": _app_url} if _app_url else {}),
    )
    url = app_url_for(kind, target_id, tool_ctx)

    # Print the plain URL line first (spec 06 R4: URLs as text
    # everywhere), then attempt the browser launch.
    click.echo(f"View in Keel: {url}")

    from keel import browser_login

    opened = browser_login._try_open_browser(url)
    if not opened:
        click.echo("Could not launch a browser — open the URL above manually.", err=True)
