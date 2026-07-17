"""Good-result deploy nudge — spec 03 R3(a) (agent-first-build M3.3).

When a backtest carries the durable ``metrics.good_result`` marker
(written by the backtest worker exactly when the spec-02 gate fires:
Sharpe >= threshold over >= the minimum range), the tool response that
renders that backtest includes ONE line: an honest deploy suggestion +
the surface-appropriate link.

Surface rules (push posture, policy boundary):

* FULL / CLI profile → the M3.2 deploy-intent deep link
  (``{app}/deploy?intent=…``) when mintable, else the strategy overview.
  Copy may say "deploy" — this surface ships live-write tools.
* LISTED directory profile → the strategy OVERVIEW url and
  "view in the Keel app" language ONLY. No deploy/fund/trade/live verbs
  ever (research/08 string rules apply to everything the listed surface
  emits, responses included).

Honesty rules (agent-first-keel research/08): numbers are cited ONLY
when the max drawdown can be named alongside them; never "earn X%";
the do-nothing alternative is always present on the full surface.

Adopters: ``keel_backtest_run`` (wait-path success) and
``keel_backtest_summarize``. The marker is READ from the run's metrics —
never recomputed here (single gate lives in
libs/backtest_messages/good_result.py).
"""

from __future__ import annotations

from typing import Any

from ._base import ToolContext
from ._toolsets import is_listed_profile


__all__ = ["good_result_nudge"]


def _fmt_num(value: Any, digits: int) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"{value:.{digits}f}"


def _numbers_fragment(metrics: dict, good_result: dict) -> str | None:
    """ "(Sharpe X, max drawdown Y%, Z days)" — or None.

    Honesty rule: if the drawdown can't be named, NO numbers are cited
    at all (never a Sharpe without its drawdown).
    """
    sharpe = _fmt_num(good_result.get("sharpe"), 2) or _fmt_num(metrics.get("sharpe_ratio"), 2)
    drawdown = _fmt_num(metrics.get("max_drawdown"), 1)
    days = good_result.get("range_days")
    days_s = f"{days:.0f}" if isinstance(days, (int, float)) else None
    if sharpe is None or drawdown is None:
        return None
    fragment = f"Sharpe {sharpe}, max drawdown {drawdown}%"
    if days_s is not None:
        fragment += f", over {days_s} days"
    return fragment


def good_result_nudge(
    detail: dict | None,
    *,
    strategy_id: str | None,
    ctx: ToolContext,
) -> str | None:
    """The one-line nudge for a backtest response, or ``None``.

    ``detail`` is the ``GET /v1/backtests/{id}``-shaped dict. Returns a
    line exactly when ``metrics.good_result`` is truthy — sub-threshold
    runs and runs without the marker never nudge.
    """
    if not isinstance(detail, dict) or not strategy_id:
        return None
    metrics = detail.get("metrics")
    if not isinstance(metrics, dict):
        return None
    good = metrics.get("good_result")
    if not good:
        return None
    if not isinstance(good, dict):
        good = {}

    numbers = _numbers_fragment(metrics, good)
    qualifier = f" ({numbers})" if numbers else ""
    overview_url = f"{ctx.app_url}/strategies/{strategy_id}"

    if is_listed_profile():
        # Navigation-only language (research/08) — the overview page is
        # where the user proceeds under their own steam.
        return (
            f"This backtest clears Keel's good-result bar{qualifier}. "
            f"You can view this strategy in the Keel app: {overview_url}"
        )

    # Full/CLI surface: prefer the signed deploy-intent deep link
    # (standalone handoff flow, server-computed sizing prefill).
    from ._handoff import mint_deploy_intent

    intent = mint_deploy_intent(ctx, strategy_id)
    url = intent["handoff_url"] if intent else overview_url
    return (
        f"This backtest clears Keel's good-result bar{qualifier}. "
        f"Taking it live is a human step — review and deploy at {url} — "
        f"or do nothing: nothing goes live without your explicit approval."
    )
