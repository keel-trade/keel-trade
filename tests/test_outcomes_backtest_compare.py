"""Tests for `keel_backtest_compare` (Q-0581).

The register-key evidence case is the spine here: two runs whose signal
block is identical, where the only changes are an allocator deletion and
`execution.rebalance` buffered → every_bar — the exact shape the old
surfaces (structural differ included) could not show.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from keel.errors import KeelError, NotFoundError
from keel.tools.outcomes import OUTCOMES

# Import directly — self-registers on import (bootstrap list is a
# separate concern, covered by the CLI wiring test below).
from keel.tools.outcomes import backtest_compare as _bt_cmp_mod  # noqa: F401
from keel.tools.outcomes._base import ToolContext


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


# ── fixtures: the evidence-shaped pair ───────────────────────────────

SRC_A = """\
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH"])
Execution(rebalance="buffered", buffer_threshold=0.1, buffer_mode="relative", rebalance_method="to_edge")
Pipeline([
    PriceDataLoader(timeframe="1d"),
    ROC(period=8),
    BetaHedgeAllocator(benchmark="BTC", window=60, hedge_ratio=1),
])
"""

# v2: allocator deleted, execution flipped to every_bar — signal identical.
SRC_B = """\
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH"])
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(timeframe="1d"),
    ROC(period=8),
])
"""

METRICS_A = {
    "sharpe_ratio": 1.8,
    "total_return": 42.5,
    "max_drawdown": -12.0,
    "turnover": 12.0,
    "total_trades": 87,
    "rebalance_legs": 210,
    # A positions-era run (trade-metrics spec 01 §4): its churn rides
    # `rebalance_legs`, read as `resizes`.
    "trade_model": "position_round_trip",
    "total_fees_paid": 55.0,
    "fees_pct_of_initial": 0.55,
    "fees_pct_of_gross_profit": 1.28,
    "fees_pct_of_net_profit": 1.29,
    "funding_included": True,
}

METRICS_B = {
    "sharpe_ratio": 1.1,
    "total_return": 30.0,
    "max_drawdown": -14.0,
    "turnover": 48.0,
    "total_trades": 87,
    "rebalance_legs": 840,
    "trade_model": "position_round_trip",
    "total_fees_paid": 220.0,
    "fees_pct_of_initial": 2.20,
    "fees_pct_of_gross_profit": 6.83,
    "fees_pct_of_net_profit": 7.33,
    "funding_included": True,
}


def _detail(bt_id, strategy_id, commit_id, metrics, start="2024-08-15", end="2026-02-27"):
    return {
        "id": bt_id,
        "status": "COMPLETED",
        "strategy_id": strategy_id,
        "strategy_name": f"strat {strategy_id}",
        "commit_id": commit_id,
        "engine": "native",
        "start_date": start,
        "end_date": end,
        "metrics": metrics,
    }


def _fake_get_factory(detail_a, detail_b, sources=None, source_error=None, messages=None):
    sources = sources or {}
    messages = messages or {}

    def fake_get(path, **_kw):
        if path == f"/v1/backtests/{detail_a['id']}":
            return detail_a
        if path == f"/v1/backtests/{detail_b['id']}":
            return detail_b
        # Since the render-cadence build each run also contributes its
        # compact curve to the comparison view (best-effort, 120 points).
        if path.endswith("/curve"):
            return {}
        for (sid, cid), src in sources.items():
            if path == f"/v1/strategies/{sid}/versions/{cid}/source":
                return {"source": src}
        # The version's commit message — the run label's fallback (Q-1792).
        for detail in (detail_a, detail_b):
            if path == f"/v1/strategies/{detail['strategy_id']}/versions/{detail['commit_id']}":
                return {"message": messages.get(detail["commit_id"])}
        if source_error is not None:
            raise source_error
        raise AssertionError(f"unexpected GET {path}")

    return fake_get


def _run_compare(ctx, detail_a, detail_b, **kw):
    fake_get = _fake_get_factory(detail_a, detail_b, **kw)
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        tool = OUTCOMES["keel_backtest_compare"]
        result = tool.handler({"backtest_ids": [detail_a["id"], detail_b["id"]]}, ctx)
    return result.to_envelope()


# ── metrics + cost profile ───────────────────────────────────────────


def test_cost_profile_rows_carry_verbatim_values_and_deltas(ctx):
    """Can-fail arm: exact a/b values and B-minus-A deltas, independently
    stated — a copied-from-the-wrong-run or recomputed value goes red."""
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_2", "c_2", METRICS_B)
    env = _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A, ("str_2", "c_2"): SRC_B})

    cost = env["cost_profile"]
    assert cost["fees_pct_of_initial"] == {"a": 0.55, "b": 2.20, "delta": pytest.approx(1.65)}
    assert cost["fees_pct_of_gross_profit"]["a"] == 1.28
    assert cost["fees_pct_of_gross_profit"]["b"] == 6.83
    assert cost["total_fees_paid"]["delta"] == pytest.approx(165.0)
    assert cost["turnover"]["delta"] == pytest.approx(36.0)
    assert cost["resizes"] == {"a": 210, "b": 840, "delta": 630}
    # Era B's stored count is a POSITION count (spec 01 §4.2): it is named
    # so, and no "total_trades" row claims it.
    assert cost["positions"] == {"a": 87, "b": 87, "delta": 0}
    assert "total_trades" not in cost and "rebalance_legs" not in cost

    perf = env["performance"]
    assert perf["sharpe_ratio"] == {"a": 1.8, "b": 1.1, "delta": pytest.approx(-0.7)}
    # Verbatim raw blobs ride along (Q-0415 discipline).
    assert env["metrics_raw_a"] == METRICS_A
    assert env["metrics_raw_b"] == METRICS_B
    # Fee drag is quoted in the one-line summary.
    assert "fee drag" in env["summary_text"]


def test_identical_periods_and_engines_produce_no_warnings(ctx):
    """Non-vacuous control arm for the warning logic.

    The pair spans two strategies, which since BUILD §2.2 is itself a
    comparability warning, and its sources run different Execution —
    named in words since Q-1802. Those two are the ONLY warnings:
    identical periods, engines, settings and carry inclusion say nothing.
    """
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_2", "c_2", METRICS_B)
    env = _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A, ("str_2", "c_2"): SRC_B})
    assert env["comparability_warnings"] == [
        "Different strategies with the same universe; their pipelines differ.",
        "Execution differs — strat str_1 rebalances only outside a 10% band of target, "
        "trading only to the band edge; strat str_2 rebalances every bar.",
    ]
    assert env["view"]["notes"] == []


def test_period_mismatch_is_flagged(ctx):
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_2", "c_2", METRICS_B, start="2025-01-01")
    env = _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A, ("str_2", "c_2"): SRC_B})
    assert any(w.startswith("Windows differ by ") for w in env["comparability_warnings"])


# ── spec diff: the evidence case ─────────────────────────────────────


def test_declaration_diff_catches_the_rebalance_flip(ctx):
    """The register's evidence case: execution.rebalance buffered →
    every_bar, allocator deleted. The structural differ alone cannot see
    the declaration change — this is the arm that proves the tool can."""
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_2", "c_2", METRICS_B)
    env = _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A, ("str_2", "c_2"): SRC_B})

    decl = env["spec_diff"]["declarations"]
    assert decl["execution"]["rebalance"] == {"a": "buffered", "b": "every_bar"}
    assert decl["execution"]["buffer_threshold"] == {"a": 0.1, "b": None}
    assert decl["execution"]["rebalance_method"] == {"a": "to_edge", "b": "to_center"}

    removed = env["spec_diff"]["pipeline"]["removed"]
    assert any(s.get("component") == "BetaHedgeAllocator" for s in removed)


def test_identical_sources_diff_empty(ctx):
    """Non-vacuous arm: the declaration diff must be silent on a
    genuinely identical pair — a diff that always reports proves nothing."""
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_1", "c_1", METRICS_B)
    env = _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A})
    assert env["spec_diff"]["declarations"] == {}
    assert env["spec_diff"]["pipeline"]["added"] == []
    assert env["spec_diff"]["pipeline"]["removed"] == []


def test_the_pair_envelope_is_unchanged_by_the_shared_declaration_owner(ctx):
    """Q-1707 moved `_declaration_view` / `_diff_declarations` into
    `_declarations` so `keel_strategy_compose` reads the same function.
    This tool's wire is what the move must NOT touch — every existing
    caller, fixture and golden reads these exact keys (§2.2)."""
    # SEED: in `_declarations.diff_declarations`, rename the `"a"`/`"b"`
    # pair keys to `"old"`/`"new"` — the declaration golden below reds
    # while the key set stays green, which is the drift this pins.
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_2", "c_2", METRICS_B)
    env = _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A, ("str_2", "c_2"): SRC_B})

    assert set(env) == {
        "run_a",
        "run_b",
        "performance",
        "cost_profile",
        # The set's shared capital and costs (Q-2270) — additive.
        "cost_model",
        "comparability_warnings",
        "metrics_raw_a",
        "metrics_raw_b",
        "summary_text",
        "spec_diff",
        "spec_diffs",
        "render",
        "view",
        "share_url",
    }, sorted(env)
    assert set(env["spec_diff"]) == {"pipeline", "declarations"}
    # The declaration block, byte for byte — the shape BOTH surfaces now
    # carry: `{section: {key: {"a": before, "b": after}}}`.
    assert env["spec_diff"]["declarations"] == {
        "execution": {
            "rebalance": {"a": "buffered", "b": "every_bar"},
            "buffer_threshold": {"a": 0.1, "b": None},
            "rebalance_method": {"a": "to_edge", "b": "to_center"},
        }
    }
    # Not vacuous: the summary really renders those declarations, so the
    # block above is the one this tool puts on screen and not a spare.
    assert "execution.rebalance buffered→every_bar" in env["summary_text"]


def test_spec_diff_failure_degrades_without_killing_metrics(ctx):
    """Two completed runs must compare even when source fetch fails."""
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_2", "c_2", METRICS_B)
    env = _run_compare(ctx, da, db, sources={}, source_error=NotFoundError("gone"))
    assert "spec_diff" not in env
    assert "Spec diff unavailable" in env["spec_diff_error"]
    assert env["cost_profile"]["fees_pct_of_initial"]["b"] == 2.20


# ── Q-1708 · the envelope budget, measured on what the TOOL emits ────
#
# The shipped guard sized a hand-made card fixture whose curve points are
# `["2024-08-15", 10000, 0]` — 24 B — and which carries only `view` and
# `hero_url`. It read 29 KB and stayed green while a real 8-run result was
# 75 KB on staging. Nothing below is a fixture: the envelope is built by
# the real handler from staging-SHAPED inputs (ISO-8601 stamps with a UTC
# offset, 2dp floats, a per-asset metrics blob), so the guard cannot go
# green on an envelope the tool does not produce.

#: BUILD §2.2's cap on one compare result.
COMPARE_ENVELOPE_BUDGET = 60_000

#: What the staging curve rows actually look like, per point.
_CURVE_T0 = "2024-08-19T12:00:00+00:00"


def _heavy_metrics(i: int) -> dict:
    """One run's stored metrics at the size the worker really writes."""
    import random

    rnd = random.Random(i)
    m = {
        "sharpe_ratio": round(0.6 + i * 0.03, 4),
        "sortino_ratio": round(1.0 + i * 0.05, 4),
        "calmar_ratio": round(0.5 + i * 0.02, 4),
        "total_return": round(50 + i * 5.5, 4),
        "max_drawdown": round(-45 - i * 0.7, 4),
        "win_rate": round(26 + i * 0.4, 4),
        "profit_factor": round(1.05 + i * 0.01, 4),
        "expectancy": round(3.1 + i, 4),
        "end_value": round(15000 + i * 111.3, 4),
        "funding_included": True,
        "total_trades": 1761 + i,
        "positions": 540 + i,
        "position_win_rate": round(38 + i * 0.3, 4),
        "resizes": 210 + i,
        "avg_holding_duration": f"{3 + i} days 04:00:00",
        "trade_model": "reducing_order",
        "total_orders": 900 + i,
        "turnover": round(12.0 + i, 4),
        "total_fees_paid": round(1091.4 + i, 4),
        "fees_pct_of_initial": round(0.55 + i * 0.01, 4),
        "fees_pct_of_gross_profit": round(1.28 + i * 0.02, 4),
        "fees_pct_of_net_profit": round(1.29 + i * 0.02, 4),
        "funding_attribution": round(-13.3 - i, 4),
    }
    m["per_asset"] = {
        f"SYM{k:02d}": {
            "return": round(rnd.uniform(-40, 90), 4),
            "trades": rnd.randint(20, 400),
            "fees": round(rnd.uniform(10, 300), 4),
            "carry": round(rnd.uniform(-9, 9), 4),
        }
        for k in range(30)
    }
    return m


def _staging_curve(buckets: int) -> dict:
    """`GET /curve?points=N` as the server answers it: N + 1 samples."""
    import datetime as _dt
    import random

    t0 = _dt.datetime.fromisoformat(_CURVE_T0)
    rnd = random.Random(7)
    equity = 10000.0
    points = []
    for k in range(buckets + 1):
        equity = round(equity * (1 + rnd.uniform(-0.03, 0.035)), 2)
        points.append(
            {
                "t": (t0 + _dt.timedelta(days=6 * k)).isoformat(),
                "equity": equity,
                "drawdown_pct": round(rnd.uniform(-55, 0), 2),
            }
        )
    return {"points": points, "start": "2024-08-19", "end": "2026-09-22", "source_points": 5000}


def _staging_references(buckets: int, holds: tuple[str, ...]) -> list[dict]:
    """keel-api's `references`: one hold line per symbol, its series
    downsampled to run 1's bucket count (N + 1 samples, like the curve)."""
    curve = _staging_curve(buckets)["points"]
    return [
        {
            "symbol": symbol,
            "label": f"{symbol} hold",
            "ret_pct": 41.5 + k,
            "dd_pct": 33.25 + k,
            "start_close": 58000.12,
            "end_close": 112000.5,
            "series": [
                {"t": p["t"], "equity": round(p["equity"] * (1 + k / 10), 2)} for p in curve
            ],
        }
        for k, symbol in enumerate(holds)
    ]


def _compare_n(ctx, n_ids: int, holds: tuple[str, ...] = ()) -> tuple[dict, list[int]]:
    """The real envelope for `n_ids` runs, plus the bucket counts asked for."""
    ids = [f"btr_{i:026d}" for i in range(n_ids)]
    details = {
        bid: {
            "id": bid,
            "status": "COMPLETED",
            "strategy_id": "str_1",
            "strategy_name": "sv-verify-roc20",
            "commit_id": f"cmt_{i}",
            "engine": "native",
            "start_date": "2024-07-27",
            "end_date": "2026-09-22",
            "sequence_number": i + 1,
            "metrics": _heavy_metrics(i),
        }
        for i, bid in enumerate(ids)
    }
    asked: list[int] = []

    def fake_get(path, **kw):
        if path.startswith("/v1/backtests/") and path.endswith("/curve"):
            asked.append(kw.get("points"))
            curve = _staging_curve(kw.get("points"))
            if kw.get("references") == "true":
                curve["references"] = _staging_references(kw.get("points"), holds)
            return curve
        if path.startswith("/v1/backtests/"):
            return details[path.rsplit("/", 1)[1]]
        if "/versions/" in path:
            return {"source": SRC_A}
        raise AssertionError(f"unexpected GET {path}")

    args: dict = {"backtest_ids": ids}
    if holds:
        args["holds"] = list(holds)
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        result = OUTCOMES["keel_backtest_compare"].handler(args, ctx)
    return result.to_envelope(), asked


def _wire(obj) -> int:
    import json

    return len(json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"))


def test_an_eight_run_compare_envelope_stays_inside_the_host_budget(ctx):
    """Q-1708: 75 KB on staging against BUILD §2.2's 60 KB, because the
    curve budget was per-RUN and the envelope cap is per-ENVELOPE."""
    # SEED: make `compare_curve_points` return `COMPARE_CURVE_POINTS`
    # unconditionally — the envelope goes back over 70 KB and this reds,
    # which IS the shipped defect.
    env, asked = _compare_n(ctx, 8)
    size = _wire(env)
    assert size <= COMPARE_ENVELOPE_BUDGET, f"an 8-run compare envelope is {size} bytes"

    # Not vacuous, and keyed on quantities the budget cannot move: all
    # eight runs really carry a curve, and the curves really are the bulk
    # of the envelope — so the number above is bounding the thing that
    # grows with arity rather than an envelope that has no curves in it.
    carried = [r for r in env["view"]["runs"] if r.get("curve")]
    assert len(carried) == 8
    curve_bytes = sum(_wire(r["curve"]) for r in carried)
    assert curve_bytes > size / 3, (curve_bytes, size)
    # And the points really are staging's shape — a date-only stamp is
    # half the bytes and is what made the old fixture unrepresentative.
    assert carried[0]["curve"]["points"][0][0] == _CURVE_T0


def test_the_comparison_window_is_a_date_pair(ctx):
    """Q-1709: `view.window` is specified as dates in BUILD §2.2, and
    staging carried `"2024-07-27 00:00:00+00:00"` on every
    backtest-shaped tool including this one."""
    # SEED: in `_handler`, rebuild `window` as
    # `{"start": baseline.get("start_date"), "end": baseline.get("end_date")}`
    # — this reds, which IS what staging shipped.
    from datetime import date

    da = _detail("bt_a", "str_1", "c_1", METRICS_A, start="2024-07-27 00:00:00+00:00")
    db = _detail("bt_b", "str_2", "c_2", METRICS_B, start="2024-07-27 00:00:00+00:00")
    env = _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A, ("str_2", "c_2"): SRC_B})
    window = env["view"]["window"]
    assert {k: window[k] for k in ("start", "end")} == {"start": "2024-07-27", "end": "2026-02-27"}
    # Additive (spec 03 §2.5): the last covered day; `end` stays exclusive.
    assert window["last_bar"] == "2026-02-26"
    assert date.fromisoformat(window["start"]) == date(2024, 7, 27)
    # Not vacuous: the RAW detail really carried the datetime, so the
    # date above was normalised rather than never having been one; and
    # the prose built from the same pair is unchanged.
    assert da["start_date"] == "2024-07-27 00:00:00+00:00"
    assert "Jul 27, 2024" in env["view"]["markdown"]


def test_the_curve_budget_is_spent_per_envelope_not_per_run(ctx):
    """The run curves share the 424-bucket envelope budget (spec 03 §2.2
    point 5; Q-2441): two runs keep the single-run resolution; four and
    eight share it. No hold is asked, so no hold line is counted."""
    # SEED: as above — `asked` reads `[120]` at eight ids and the point
    # counts stop differing between the arms.
    for n_ids, expected_buckets in ((2, 120), (4, 106), (8, 53)):
        env, asked = _compare_n(ctx, n_ids)
        assert set(asked) == {expected_buckets}, (n_ids, sorted(set(asked)))
        # `points=N` buckets ⇒ N + 1 samples: both endpoints are inclusive.
        # The contract said "120 points" while the wire carried 121.
        counts = {len(r["curve"]["points"]) for r in env["view"]["runs"] if r.get("curve")}
        assert counts == {expected_buckets + 1}, (n_ids, counts)
        assert len(env["view"]["runs"]) == n_ids


def _samples(env: dict) -> int:
    runs = sum(len(r["curve"]["points"]) for r in env["view"]["runs"] if r.get("curve"))
    holds = sum(len(ref.get("series") or []) for ref in env.get("references") or [])
    legacy = len((env.get("reference") or {}).get("series") or [])
    return runs + holds + legacy


def test_hold_lines_share_the_envelope_budget(ctx):
    """Q-2441: with `holds`, every line the envelope carries — N curves, one
    series per hold, and `reference.series` (the first hold, again) — is
    counted, so the samples stay within COMPARE_CURVE_TOTAL_POINTS plus one
    endpoint per line and the 8-run envelope stays inside the host budget.
    Seeded red on today's arithmetic: 605/679/594 samples and 68.8 KB."""
    from keel.tools.outcomes.backtest_compare import COMPARE_CURVE_TOTAL_POINTS

    holds = ("BTC", "ETH", "SOL")
    for n_ids in (2, 4, 8):
        env, asked = _compare_n(ctx, n_ids, holds=holds)
        # Non-vacuity on what the budget cannot move: every hold line is
        # there with a series, and every run asked for a non-zero curve.
        assert len(env["references"]) == len(holds)
        assert all(ref["series"] for ref in env["references"])
        assert asked and min(asked) > 0
        lines = n_ids + len(holds) + 1
        assert _samples(env) <= COMPARE_CURVE_TOTAL_POINTS + lines, (n_ids, _samples(env))
        size = _wire(env)
        assert size <= COMPARE_ENVELOPE_BUDGET, (n_ids, size)


# ── guards ───────────────────────────────────────────────────────────


def test_incomplete_run_raises_with_watch_suggestion(ctx):
    da = _detail("bt_a", "str_1", "c_1", METRICS_A)
    db = _detail("bt_b", "str_2", "c_2", None)
    db["metrics"] = None
    db["status"] = "RUNNING"
    fake_get = _fake_get_factory(da, db)
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        tool = OUTCOMES["keel_backtest_compare"]
        with pytest.raises(KeelError) as exc:
            tool.handler({"backtest_ids": ["bt_a", "bt_b"]}, ctx)
    assert "no metrics yet" in str(exc.value)
    assert "`keel_backtest_watch`" in (exc.value.suggestion or "")


def test_unknown_run_id_names_the_tool_not_the_cli_command(ctx):
    """Q-1743: on the MCP surface the recovery copy named `keel backtest run`
    — a CLI command a hosted connector cannot run."""
    with patch("keel.client.KeelClient.get", side_effect=NotFoundError("gone")):
        with pytest.raises(NotFoundError) as exc:
            OUTCOMES["keel_backtest_compare"].handler({"backtest_ids": ["bt_x", "bt_y"]}, ctx)
    expected = exc.value.to_envelope()["what_was_expected"]
    assert "`keel_backtest_run`" in expected
    assert "keel backtest run" not in expected
    assert "backtest_run_id returned when the backtest was submitted" in expected


@pytest.mark.parametrize("ids", [[], ["bt_a"], ["a"] * 9])
def test_wrong_arity_raises_usage_error(ctx, ids):
    """Re-parametrised to 1 and 9 for the 2–8 arity (BUILD §2.2): three
    ids is now a legal comparison, and nine is the cap being enforced."""
    tool = OUTCOMES["keel_backtest_compare"]
    with pytest.raises(KeelError) as exc:
        tool.handler({"backtest_ids": ids}, ctx)
    assert exc.value.exit_code == 2


# ── CLI wiring ───────────────────────────────────────────────────────


def test_cli_exposes_backtest_compare_with_two_positionals():
    """`keel backtest compare <a> <b>` — the register's literal ask."""
    from click.testing import CliRunner
    from keel.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["backtest", "compare", "--help"])
    assert result.exit_code == 0, result.output
    assert "BACKTEST_IDS" in result.output.upper()
    # The arity moved 2 -> 2–8 (BUILD §2.2); the CLI takes the ids as
    # variadic positionals, so the schema is what bounds them.
    from keel.tools.outcomes import OUTCOMES as _OUTCOMES

    ids_schema = _OUTCOMES["keel_backtest_compare"].input_schema["properties"]["backtest_ids"]
    assert (ids_schema["minItems"], ids_schema["maxItems"]) == (2, 8)


# ── Measurement eras: like with like (trade-metrics spec 01 §6.2) ──────
#
# SEED (run 2026-09-30): delete the Era B entry of
# `_backtest_view._ERA_KEYS` → the Era B arms here go red (its positions
# and resizes cells read nothing) while the A/C and unknown arms stay green.


def _era_compare(ctx, metrics_a, metrics_b):
    da = _detail("bt_a", "str_1", "c_1", {**METRICS_BASE, **metrics_a})
    db = _detail("bt_b", "str_1", "c_2", {**METRICS_BASE, **metrics_b})
    return _run_compare(ctx, da, db, sources={("str_1", "c_1"): SRC_A, ("str_1", "c_2"): SRC_A})


METRICS_BASE = {"sharpe_ratio": 1.2, "total_return": 30.0, "max_drawdown": -12.0}
ERA_ROWS = {
    "A": {"total_trades": 120, "win_rate": 48.0},
    "B": {
        "total_trades": 53,
        "win_rate": 40.0,
        "rebalance_legs": 900,
        "trade_model": "position_round_trip",
    },
    "C": {
        "total_trades": 1198,
        "win_rate": 84.8,
        "positions": 60,
        "position_win_rate": 37.8,
        "resizes": 700,
        "trade_model": "reducing_order",
    },
    "unknown": {"total_trades": 99, "win_rate": 50.0, "trade_model": "flat_to_flat_v9"},
}


def _cells(md: str, label: str) -> list[str]:
    row = next(line for line in md.splitlines() if line.startswith(f"| {label} |"))
    return [c.strip() for c in row.strip("|").split("|")[1:]]


@pytest.mark.parametrize(
    ("a", "b", "trades", "positions", "mixed"),
    [
        ("A", "C", ["120", "1,198"], ["—", "60"], True),
        ("B", "C", ["—", "1,198"], ["53", "60"], True),
        ("C", "C", ["1,198", "1,198"], ["60", "60"], False),
        ("A", "unknown", ["120", "—"], None, True),
    ],
    ids=["A-vs-C", "B-vs-C", "C-vs-C", "A-vs-unknown"],
)
def test_each_cell_compares_like_with_like(ctx, a, b, trades, positions, mixed):
    from keel.tools.outcomes._backtest_view import MIXED_ERA_LINE

    env = _era_compare(ctx, ERA_ROWS[a], ERA_ROWS[b])
    view = env["view"]
    md = view["markdown"]
    assert _cells(md, "Trades") == trades
    if positions is None:
        # No run recorded positions: the row is not drawn.
        assert "| Positions |" not in md
    else:
        assert _cells(md, "Positions") == positions
    # A delta only between two recorded values.
    delta = view["deltas"][1]
    assert ("trades" in delta) == ("—" not in trades)
    assert ("positions" in delta) == (positions is not None and "—" not in positions)
    assert (MIXED_ERA_LINE in view["notes"]) is mixed
    assert (MIXED_ERA_LINE in md) is mixed
    # The model-visible performance block reads by era too: an Era B run's
    # stored win rate is a POSITION win rate.
    perf = env["performance"]
    if a == "B":
        assert perf["win_rate"]["a"] is None and perf["position_win_rate"]["a"] == 40.0
        assert env["cost_profile"]["resizes"]["a"] == 900
    assert "position_round_trip" not in md


def test_the_era_compare_fixtures_are_non_vacuous():
    assert set(ERA_ROWS) == {"A", "B", "C", "unknown"}
    assert len({r.get("trade_model") for r in ERA_ROWS.values()}) == 4
