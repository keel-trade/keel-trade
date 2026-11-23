"""The coin cap on screens, for the agent tools (value-and-pricing spec 02 §C4.2-§C4.4, §L2).

keel-api refuses a screen whose resolved universe is over the plan's coins per screen with
``SCREEN_COINS_OVER_PLAN`` (run, save, update, restore, copy and fork; nothing spent), and its compose check
(``POST /v1/strategies/validate``) reports the same limit as a ``plan_findings`` warning before anything is saved.
Both carry the §L2 remedy, whose ``apply`` (``{"narrow_to_plan": true}``) is the exact argument the SAME call takes to
keep the most traded coins the plan covers. The server never narrows on its own; the argument is the acceptance.

This module is the one place the screen tools turn that remedy into recovery arguments (the way
``BACKTEST_TOO_LARGE``'s window becomes the backtest's next call), project ``plan_narrowed`` and ``plan_findings`` to
the names-only shape an agent surface carries, and say the result in one line the model reads.
"""

from __future__ import annotations

from typing import Any

from keel.errors import SCREEN_COINS_CODES, KeelError, assert_plan_copy, more_plan_names


#: The input every tool that runs, saves or copies a screen takes (default false: nothing narrows without it).
NARROW_PARAM: dict[str, Any] = {
    "type": "boolean",
    "default": False,
    "description": (
        "Keep the highest-volume coins the plan covers when the screen resolves to more, and run, save or copy "
        "that version; a coin-limit refusal or a compose finding names it. False narrows nothing."
    ),
}

#: The remedy keys an agent surface carries (they name no other plan).
_REMEDY_KEYS: tuple[str, ...] = ("kind", "max_coins", "resolved_coins", "apply", "words", "options")

#: The ``plan_narrowed`` keys an agent surface carries (V3-API shape 2). A copy of a screener share adds what the
#: copy leaves out of the share's own top 10 (from its latest published result; spec 02 §C5.5) and the share's link,
#: which is not a billing link (D-422): the agent may point to Keel's full ranking.
_NARROWED_KEYS: tuple[str, ...] = (
    "unit",
    "max_coins",
    "resolved_coins",
    "kept_coins",
    "mode",
    "words",
    "message",
    "kept",
    "left_out",
    "outside_top10",
    "share_top",
    "share_ranked_coins",
    "share_result_at",
    "share_url",
)

#: The finding keys an agent surface carries (V3-API shape 5).
_FINDING_KEYS: tuple[str, ...] = (
    "code",
    "severity",
    "unit",
    "kind",
    "applies",
    "limit",
    "resolved",
    "floor",
    "requested",
    "plan",
    "plan_name",
    "message",
    "suggestion",
)


def _remedy(served: Any) -> dict[str, Any] | None:
    if not isinstance(served, dict):
        return None
    return {k: served[k] for k in _REMEDY_KEYS if served.get(k) is not None} or None


def coins_detail(err: KeelError) -> dict | None:
    """The coin-cap refusal's ``detail``, or ``None`` for any other error."""
    detail = err.detail if isinstance(getattr(err, "detail", None), dict) else None
    if detail is None or detail.get("code") not in SCREEN_COINS_CODES:
        return None
    return detail


def apply_narrow_recovery(err: KeelError, tool: str, args: dict) -> bool:
    """On a coin-cap refusal whose remedy carries ``apply``, make the next action the SAME ``tool`` call with it
    (spec 02 §C4.3a: one call keeps the top coins and runs). Returns whether it did. The suggestion stays the
    server's (the never-split rule, §C4.4); the refusal is not retryable unchanged."""
    detail = coins_detail(err)
    if detail is None:
        return False
    remedy = detail.get("remedy") if isinstance(detail.get("remedy"), dict) else {}
    apply = remedy.get("apply")
    if not isinstance(apply, dict) or not apply or args.get("narrow_to_plan"):
        return False
    err.recovery_tool = tool
    err.recovery_tool_args = {
        **{k: v for k, v in args.items() if v is not None and k != "dry_run"},
        **apply,
    }
    err.retryable = False
    return True


def narrowed_fields(response: Any) -> dict[str, Any]:
    """``plan_narrowed`` (names only, as served) and its one line (``plan_narrowed_notice``) from a run, save or
    copy response, or ``{}`` when nothing was narrowed. The line is the server's own words ("Keeps the 30 most
    traded of these 42, as a fixed list. Free screens cover up to 30 coins."), then the coins left out when the
    user named them (S2: the agent names them and offers to swap any back in)."""
    served = response.get("plan_narrowed") if isinstance(response, dict) else None
    if not isinstance(served, dict):
        return {}
    projected = {k: served[k] for k in _NARROWED_KEYS if served.get(k) is not None}
    if "higher_plans" in served or "more_plans" in served:
        projected["more_plans"] = more_plan_names(served)
    line = projected.get("message") or projected.get("words")
    if not isinstance(line, str) or not line.strip():
        return {"plan_narrowed": projected}
    left_out = projected.get("left_out")
    if projected.get("mode") == "manual" and isinstance(left_out, list) and left_out:
        line = f"{line.strip()} Left out: {', '.join(str(c) for c in left_out)}."
    return {"plan_narrowed": projected, "plan_narrowed_notice": assert_plan_copy(line.strip())}


def findings_fields(findings: Any) -> dict[str, Any]:
    """``plan_findings`` projected (names only: ``more_plans``, never other plans' numbers) and one line per finding
    the model reads (``plan_findings_notice``): the wall, the instruction, and the argument that keeps the in-plan
    version. ``{}`` when there are none. A finding is a warning: it never blocks composing or saving."""
    if not isinstance(findings, list):
        return {}
    projected: list[dict[str, Any]] = []
    lines: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("code"), str):
            continue
        entry = {k: finding[k] for k in _FINDING_KEYS if finding.get(k) is not None}
        if "higher_plans" in finding or "more_plans" in finding:
            entry["more_plans"] = more_plan_names(finding)
        remedy = _remedy(finding.get("remedy"))
        if remedy is not None:
            entry["remedy"] = remedy
        projected.append(entry)
        parts = [entry.get("message"), entry.get("suggestion")]
        apply = (remedy or {}).get("apply")
        if isinstance(apply, dict) and apply.get("narrow_to_plan") is True:
            words = (remedy or {}).get("words")
            parts.append(
                "With `narrow_to_plan: true`, the same keel_strategy_compose or keel_screener_run call keeps the in-plan version"
                + (f": {words}" if isinstance(words, str) and words else ".")
            )
        lines.append(" ".join(p.strip() for p in parts if isinstance(p, str) and p.strip()))
    if not projected:
        return {}
    return {
        "plan_findings": projected,
        "plan_findings_notice": " ".join(assert_plan_copy(line) for line in lines if line),
    }


__all__ = [
    "NARROW_PARAM",
    "apply_narrow_recovery",
    "coins_detail",
    "findings_fields",
    "narrowed_fields",
]
