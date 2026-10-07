"""The lines catalogue is conditional and factual (spec 02 §2.4, guard G4).

Every listed label has an envelope where its line APPEARS and one where it
does NOT; no line's text (the template, not the `label:` prefix) carries an
evaluative word; `next` is at most one string. The full profile's `deploy:`
line is counted separately (never emitted on listed).

SEEDS (run 2026-09-23, reverted by reversing the edit):
* make `_backtest_view.good_result_line` return a string when `good_result`
  is falsy (`if not isinstance(good, dict) or not good: return "Sharpe"`) —
  the `good_result` absent arm reds while every other label's arms stay green;
* put "strong" into the reference template — `test_no_line_is_evaluative`
  reds.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes._mcp_adapter import (
    _FULL_PROFILE_FIELDS,
    _OPERATIONAL_FIELDS,
    operational_lines,
)


#: Spec 02 §2.4's word list — never in a line Keel writes.
EVALUATIVE = (
    "good",
    "great",
    "strong",
    "clears",
    "impressive",
    "promising",
    "you should",
    "consider",
)

_VIEW_BT = {"kind": "backtest", "status": "completed", "version": 3, "markdown": "x"}
_WINDOW = {
    "requested": {"start": "2024-01-01", "end": "2026-10-01"},
    "ran": {
        "start": "2024-07-27",
        "end_exclusive": "2026-09-23",
        "last_bar": "2026-09-22",
        "days": 788,
    },
    "warmup_bars": 30,
    "chart_start": "2024-08-19",
    "start_reason_code": "platform_floor",
    "start_reason": "Keel's price history begins Jul 27, 2024",
}
_GOOD = {
    "sharpe": 1.42,
    "sharpe_threshold": 1.0,
    "range_days": 600.0,
    "min_range_days": 180.0,
    "total_return": 55.2,
    "min_total_return_exclusive": 0.0,
    "max_drawdown": 22.5,
    "max_drawdown_magnitude": 22.5,
    "max_drawdown_magnitude_limit": 40.0,
    "halves": [["2024-07-27", "2025-08-24"], ["2025-08-24", "2026-09-23"]],
}

#: label → (an envelope where the line appears, the substring it must carry).
APPEARS: dict[str, tuple[dict, str]] = {
    "next": ({"view": _VIEW_BT, "next": "2 completed runs on this strategy"}, "2 completed runs"),
    "window": (
        {"view": _VIEW_BT, "status": "completed", "window": _WINDOW},
        "Jul 27, 2024 – Sep 22, 2026",
    ),
    "config": ({"view": _VIEW_BT, "strategy_config": "v3: universe Top 30"}, "universe Top 30"),
    "execution": (
        {
            "view": {
                "kind": "strategy",
                "markdown": "x",
                "header": {"execution": {"params": {"rebalance": "every_bar"}}},
            }
        },
        "Execution(",
    ),
    "reference": (
        {
            "view": _VIEW_BT,
            "reference": {
                "ret_pct": 41.2,
                "dd_pct": -57.0,
                "start_close": 58123,
                "end_close": 81990,
            },
        },
        "BTC hold, price only, same span: +41.2% ($58,123 → $81,990) · max drawdown −57.0%",
    ),
    "realism": (
        {
            "view": _VIEW_BT,
            "realism": {
                "n_orders": 10,
                "n_below": 3,
                "share": 0.3,
                "avg_order_notional": 42.0,
                "min_notional": 10,
            },
        },
        # Both halves (Q-1881): the backtest's returns include them.
        "3 of 10 orders (30%) were below the $10 live minimum; this backtest fills them "
        "(they are in its returns), live execution would skip them",
    ),
    "good_result": (
        {"view": _VIEW_BT, "strategy_id": "str_a", "good_result": _GOOD},
        "meets Keel's bar (Sharpe ≥ 1 over ≥ 180 days, return > 0, drawdown ≤ 40%)",
    ),
    "facts": (
        {"view": {"kind": "strategy", "markdown": "x"}, "library_facts": "verified run"},
        "verified run",
    ),
    "quota": ({"view": _VIEW_BT, "quota_notice": "3 of 50 backtests left"}, "3 of 50"),
    "error": ({"view": _VIEW_BT, "error_message": "worker died"}, "worker died"),
    "info": ({"view": _VIEW_BT, "info": "Submitted; not waiting."}, "Submitted"),
    "validation": (
        {
            "view": {"kind": "strategy", "markdown": "x"},
            "validation": {"errors": [{"code": "X", "message": "m"}], "warnings": []},
        },
        '1 error — X: m (keel_help topic="rule:X")',
    ),
    "sample": ({"view": _VIEW_BT, "few_fills_note": "few trades (3)"}, "few trades (3)"),
    # Position-layer spec 04-R24: a strategy view pinning deprecated
    # components names them, the known issue, and the upgrade route (on the
    # listed profile, D-48's fallback: compose carries the upgraded source).
    "upgrade": (
        {
            "view": {
                "kind": "strategy",
                "markdown": "x",
                "validation": {
                    "ok": True,
                    "errors": 0,
                    "warnings": 2,
                    "deprecations": [
                        {
                            "component": "TrailingStopExit",
                            "version": 1,
                            "known_issue": {"id": "Q-2448", "summary": "stale price"},
                            "replacement_text": "A TradeManager rule",
                        }
                    ],
                },
            }
        },
        "TrailingStopExit v1 is deprecated (known issue Q-2448: stale price); "
        "keel_strategy_compose carries the upgraded source",
    ),
    # Spec 07 §5: how much of the window held positions (Q-1993's stamp).
    "costs": (
        {
            "view": _VIEW_BT,
            "cost_model": {
                "line": "$5,000 starting capital · fees 10 bps and slippage 4.5 bps per fill"
            },
        },
        "fees 10 bps",
    ),
    "exposure": (
        {
            "view": _VIEW_BT,
            "exposure": {"line": "Held positions on 6 of 25 bars · first position Sep 20, 2026"},
        },
        "Held positions on 6 of 25 bars · first position Sep 20, 2026",
    ),
    # Spec 07 §6: part of the run, keel-api's sentence + what a slice is.
    "slice": (
        {
            "view": _VIEW_BT,
            "slice": {
                "summary": "Slice covers 7 daily bars 2026-09-19 → 2026-09-25: +4.12%",
                "basis": "A slice of the run as it ran.",
            },
        },
        "Slice covers 7 daily bars 2026-09-19 → 2026-09-25: +4.12% A slice of the run as it ran.",
    ),
    # ChatGPT R4 #5: the evidence is another version's run.
    "evidence_matches_version": (
        {
            "view": {
                "kind": "strategy",
                "markdown": "x",
                "version": 4,
                "evidence": {"version": 2, "matches_version": False},
            }
        },
        "false — the backtest numbers are v2's run; v4 has no completed run",
    ),
    # A compose save keel-api answered `unchanged: true` (Q-2102).
    "unchanged": (
        {"view": {"kind": "strategy", "markdown": "x"}, "version": 3, "unchanged": True},
        "No changes — source matches the current version (v3).",
    ),
    # `include_source=true` (Q-1874): keel-api's source read, fenced.
    "source": (
        {
            "view": {"kind": "strategy", "markdown": "x"},
            "version": "3",
            "source": {"source": "Pipeline([ROC(period=14)])"},
        },
        "the DSL at 3, 1 line\n```python\nPipeline([ROC(period=14)])\n```",
    ),
}

#: label → an envelope that carries the NEIGHBOURING datum but not this line's
#: condition (the absent arm).
ABSENT: dict[str, dict] = {
    "next": {"view": _VIEW_BT},
    # R-3: a queued run carries no window line even with the object present.
    "window": {"view": {**_VIEW_BT, "status": "queued"}, "status": "queued", "window": _WINDOW},
    "config": {"view": _VIEW_BT},
    "execution": {"view": {"kind": "backtest", "markdown": "x"}},
    # A reference on a STRATEGY result (library facts) renders no line.
    "reference": {
        "view": {"kind": "strategy", "markdown": "x"},
        "reference": {"ret_pct": 1.0, "dd_pct": -2.0},
    },
    # n_below == 0 ⇒ no line.
    "realism": {
        "view": _VIEW_BT,
        "realism": {"n_orders": 10, "n_below": 0, "share": 0.0, "min_notional": 10},
    },
    "good_result": {"view": _VIEW_BT, "good_result": None},
    "facts": {"view": {"kind": "strategy", "markdown": "x"}},
    "quota": {"view": _VIEW_BT, "quota": []},
    "error": {"view": _VIEW_BT},
    "info": {"view": _VIEW_BT},
    "validation": {
        "view": {"kind": "strategy", "markdown": "x"},
        "validation": {"errors": [], "warnings": []},
    },
    "sample": {"view": _VIEW_BT},
    # No deprecated pin: the validation block carries no key, so no line.
    "upgrade": {
        "view": {"kind": "strategy", "markdown": "x", "validation": {"ok": True, "errors": 0}},
    },
    # A run from before the worker's stamp carries no exposure: no line.
    "costs": {"view": _VIEW_BT, "cost_model": None},
    "exposure": {"view": _VIEW_BT, "exposure": None},
    # No start/end/capital asked: no slice block, no line.
    "slice": {"view": _VIEW_BT, "slice": {"summary": ""}},
    # The run IS the version shown: no key, no line.
    "evidence_matches_version": {
        "view": {"kind": "strategy", "markdown": "x", "version": 4, "evidence": {"version": 4}},
    },
    # A save that wrote a version carries no key: no line (Q-2102).
    "unchanged": {"view": {"kind": "strategy", "markdown": "x"}, "version": 4},
    # The Code tab's `view.source` alone is the card's, never a line.
    "source": {
        "view": {"kind": "strategy", "markdown": "x", "source": {"text": "Pipeline([])"}},
    },
}


def _labels(lines: list[str]) -> list[str]:
    return [line.split(":", 1)[0] for line in lines]


def test_the_catalogue_is_exercised_in_full() -> None:
    """Non-vacuity: every listed label has both arms (20), and the full
    profile's one extra label is counted separately."""
    labels = [label for _, label, _ in _OPERATIONAL_FIELDS]
    assert len(labels) == 20 == len(set(labels))
    assert set(APPEARS) == set(ABSENT) == set(labels)
    assert [label for _, label, _ in _FULL_PROFILE_FIELDS] == ["deploy"]


@pytest.mark.parametrize("label", sorted(APPEARS))
def test_each_line_appears_on_its_condition(label: str, monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    envelope, needle = APPEARS[label]
    lines = [line for line in operational_lines(envelope) if line.startswith(f"{label}: ")]
    assert len(lines) == 1, (label, operational_lines(envelope))
    assert needle in lines[0], lines[0]


@pytest.mark.parametrize("label", sorted(ABSENT))
def test_each_line_is_absent_without_its_condition(label: str, monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert label not in _labels(operational_lines(ABSENT[label]))


def test_no_line_is_evaluative(monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    checked = 0
    for label, (envelope, _) in APPEARS.items():
        for line in operational_lines(envelope):
            if not line.startswith(f"{label}: "):
                continue
            text = line.split(": ", 1)[1].lower()
            for word in EVALUATIVE:
                assert word not in text, (label, word, line)
            checked += 1
    assert checked == 20


def test_the_first_week_sentence_rides_the_quota_line_only(monkeypatch) -> None:
    """connect-onboarding spec 01 §1.9: the first-week sentence is part of
    the ONE `quota:` line (it rides `quota_notice`), never `next`, and the
    line stays factual. The envelope is the real handler's, so the line is
    the one a host reads."""
    from unittest.mock import patch

    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    block = {
        "unit": "backtest_runs",
        "label": "backtests",
        "limit": 250,
        "used": 210,
        "remaining": 40,
        "period": "weekly",
        "resets_at": "2026-09-22T00:00:00Z",
        "tier": "critical",
        "first_week": {"granted": 200, "remaining": 40, "ends_at": "2026-10-13T15:02:11Z"},
    }
    submitted = {"id": "bt_fw", "status": "queued", "strategy_id": "s", "quota": [block]}
    with patch("keel.client.KeelClient.post", return_value=submitted):
        envelope = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {"strategy_id": "s", "wait": False, "skip_readiness": True},
                ToolContext(is_tty=False, app_url="https://app.usekeel.io"),
            )
            .to_envelope()
        )
    envelope["view"] = _VIEW_BT
    envelope["next"] = "A run of this strategy from the last hour exists."
    lines = operational_lines(envelope)
    carrying = [line for line in lines if "first-week" in line]
    assert carrying == [
        "quota: 40 of 250 backtests left this week; they reset Tue 22 Sep 00:00 UTC. "
        "40 of 200 first-week backtests left; they end Tue 13 Oct 15:02 UTC."
    ], lines
    assert "next" in _labels(lines)  # non-vacuity: a `next` line was there to avoid
    for word in EVALUATIVE:
        assert word not in carrying[0].lower(), word


def test_the_deploy_line_is_full_profile_only(monkeypatch) -> None:
    envelope = {"view": _VIEW_BT, "deploy": "Taking it live is a human step"}
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    assert "deploy" in _labels(operational_lines(envelope))
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert "deploy" not in _labels(operational_lines(envelope))


def test_next_is_at_most_one_string(monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    for envelope, _ in APPEARS.values():
        assert _labels(operational_lines(envelope)).count("next") <= 1


def test_the_window_line_says_what_moved(monkeypatch) -> None:
    envelope, _ = APPEARS["window"]
    (line,) = [x for x in operational_lines(envelope) if x.startswith("window: ")]
    # Q-1991: no warm-up count; Q-2422: the pair that re-runs this window.
    assert line == (
        "window: Jul 27, 2024 – Sep 22, 2026 (788 days · chart from "
        "Aug 19, 2024); start moved from Jan 1, 2024 — Keel's price history begins "
        "Jul 27, 2024; end capped at today · same window: start_date=2024-07-27 "
        "end_date=2026-09-23"
    )


def test_a_comparison_reference_says_run_1s_span(monkeypatch) -> None:
    """R-28: never "same span" on a compare."""
    envelope = {
        "view": {"kind": "comparison", "markdown": "x"},
        "reference": {"ret_pct": 5.0, "dd_pct": -3.0},
    }
    (line,) = [x for x in operational_lines(envelope) if x.startswith("reference: ")]
    assert "over run 1's span" in line and "same span" not in line


def test_the_good_result_line_proposes_no_runs() -> None:
    """Spec 05 R-L4 / D-13 L3: the fact line never proposes runs. It used
    to spell out the two half-window runs as `keel_backtest_run(…,
    start_date=…, end_date=…)` plus `keel_backtest_compare` — a
    quota-spending suggestion, chained, with dates the user never named.
    The halves stay as DATA (`good_result.halves`); the line states facts.

    SEED (run 2026-09-28): restore the halves clause in `good_result_line`
    — the three not-in assertions red; the threshold assertion (the
    control, which the seed cannot move) stays green."""
    from keel.tools.outcomes._backtest_view import good_result_line

    assert _GOOD.get("halves"), "non-vacuity: the marker carries halves"
    line = good_result_line(_GOOD)
    assert "Each half" not in line
    assert "keel_backtest_run" not in line and "keel_backtest_compare" not in line
    # The thresholds are the marker's own fields, never literals.
    assert "Sharpe ≥ 1 over ≥ 180 days" in line


def test_the_library_overlap_clause(monkeypatch) -> None:
    from keel.tools.outcomes._backtest_view import good_result_line

    same = good_result_line({**_GOOD, "library_fork": True, "library_overlap": 0.99})
    assert "Same window as the library's verified run — not out-of-sample." in same
    differs = good_result_line({**_GOOD, "library_fork": True, "library_overlap": 0.7})
    assert "(70% overlap)" in differs
