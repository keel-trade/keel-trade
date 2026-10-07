"""A backtest result's served facts (spec 03 §2.2, §2.3, §2.6, §2.7, §2.10;
review 06 §4) — the SDK half.

* the BTC hold reference: read from `/curve`'s `references[0]`, one tile, one
  line; words never "buy", "benchmark" or "baseline"; absent when unservable
  (guards 5, 6);
* the headroom arm: when the compare hint fires, the quota ladder rides —
  the 201's when sent, else ONE entitlements read (guard 7);
* realism from `metrics.realism`, the line only when `n_below > 0` (guard 14);
* the sizing clause on `config:` (guard 15);
* `halves` for a ≥ 60-day completed run only (guard 18);
* the strategy view names the version its evidence ran on (review 06 §4 a);
* a compare over ≥ 2 versions names HEAD, the versions it ran and
  `keel_strategy_restore` — never a winner (review 06 §4 b, Q-1875).

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* sizing — print `target_leverage=` verbatim in one template
  (`"EqualWeightSizer target_leverage={v}"`): `test_no_clause_names_a_parameter`
  reds;
* reference — rename the label to "BTC buy-and-hold":
  `test_the_reference_words` reds;
* halves — drop the 60-day floor (`HALVES_MIN_DAYS = 0`):
  `test_halves_only_for_a_long_enough_run` reds on the 40-day arm;
* headroom — attach the block unconditionally (move the `extra.update(...)`
  out of `if line:`): the no-hint arm of `test_the_headroom_arm` reds;
* evidence — drop the version from `_evidence_line`: the not-HEAD arm reds;
* neutrality (Q-1875) — restore the winner pick (append
  `f" (highest Sharpe v{max(versions)})"` to the line): both arms of
  `test_a_multi_version_set_names_head_and_restore_neutrally` red.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._backtest_view import (
    build_backtest_view,
    halves_for,
    realism_from_metrics,
    reference_from_curve,
    sizing_clause,
)
from keel.tools.outcomes._base import ToolContext


@pytest.fixture(autouse=True)
def _registry():
    _bootstrap()


# ── guard 15 · the sizing clause ──────────────────────────────────────


def _steps(source: str):
    from pipeline_engine.dsl import parse_strategy

    return parse_strategy(source).pipeline.steps


_HEAD = "Globals(target_timeframe='1d')\nUniverse(mode='top_volume', top_n=30, market='perp')\nExecution(rebalance='every_bar')\n"

SIZERS = {
    "FixedWeightSizer(weight_per_position=0.2)": "FixedWeightSizer 0.2× per open position, uncapped",
    "EqualWeightSizer(target_leverage=1.5)": "EqualWeightSizer 1.5× gross across held names",
    "ForecastWeightNormalizer(target_leverage=1.0)": "ForecastWeightNormalizer 1× gross target",
    "EqualWeightAllocator()": "EqualWeightAllocator equal weights across selected names",
    "RiskParityAllocator()": "RiskParityAllocator risk parity across selected names",
    # Q-2502: `max_weight` at or above the target can never bind — the gross
    # claim stays (control). Below it, the book holds the target only with
    # enough names open, so the clause names the sizer and claims no gross.
    "EqualWeightSizer(target_leverage=1.0, max_weight=1.0)": "EqualWeightSizer 1× gross across held names",
    "EqualWeightSizer(target_leverage=1.0, max_weight=0.2)": "EqualWeightSizer",
    "EqualWeightSizer(target_leverage=5.0, max_weight=1.0)": "EqualWeightSizer",
    "VolWeightSizer(vol_slot='v', target_leverage=2.0, max_weight=0.5)": "VolWeightSizer",
    "ForecastWeightNormalizer(target_leverage=1.0, max_weight=0.3)": "ForecastWeightNormalizer",
}

#: The rows where the sizer's own `max_weight` binds (Q-2502).
CAPPED = [s for s, c in SIZERS.items() if "max_weight" in s and "×" not in c]


def _pipeline(*blocks: str) -> str:
    body = ",\n".join(f"    {b}" for b in ("PriceDataLoader()", "ROC(period=20)", *blocks))
    return _HEAD + f"Pipeline([\n{body},\n], name='s')\n"


@pytest.mark.parametrize("sizer,clause", sorted(SIZERS.items()))
def test_each_terminal_sizer_yields_its_clause(sizer: str, clause: str) -> None:
    assert sizing_clause(_steps(_pipeline(sizer))) == clause


def test_cadence_ops_are_skipped_and_a_cap_is_named() -> None:
    steps = _steps(
        _pipeline(
            "FixedWeightSizer(weight_per_position=0.2)",
            "LeverageCap(max_leverage=2.0)",
            "WeightCadence(every=5)",
        )
    )
    assert sizing_clause(steps) == "FixedWeightSizer 0.2× per open position, capped at 2× gross"


def test_a_binding_max_weight_claims_no_gross() -> None:
    """Q-2502 (founder ruling 2026-10-06: remove the claim where it can be
    wrong, add no cap wording): a sizer clipped below its target names
    itself, and a later cap still reads as a cap.

    SEED (2026-10-06): drop the `MAX_WEIGHT_CLIPPED` arm in `sizing_clause`
    (today's code) → the four CAPPED rows of
    `test_each_terminal_sizer_yields_its_clause` and this arm red; the
    `max_weight=1.0` control row and the other rows stay green. Reverted by
    restoring the edit."""
    assert len(CAPPED) >= 4  # non-vacuity: the arm this guard exists for
    for sizer in CAPPED:
        clause = sizing_clause(_steps(_pipeline(sizer)))
        assert clause and "gross" not in clause and "×" not in clause, clause
    steps = _steps(
        _pipeline(
            "EqualWeightSizer(target_leverage=1.0, max_weight=0.2)", "LeverageCap(max_leverage=2.0)"
        )
    )
    assert sizing_clause(steps) == "EqualWeightSizer, capped at 2× gross"


def test_no_sizer_yields_no_clause() -> None:
    assert sizing_clause(_steps(_pipeline())) is None


def test_no_clause_names_a_parameter() -> None:
    """Non-vacuity: ≥ 5 distinct terminal components and ≥ 1 cadence op."""
    assert len({s.split("(")[0] for s in SIZERS}) >= 5
    for sizer in SIZERS:
        clause = sizing_clause(_steps(_pipeline(sizer, "WeightCadence(every=5)")))
        assert clause and "leverage" not in clause.lower() and "=" not in clause, clause


# ── guards 5/6 · the reference ────────────────────────────────────────

CURVE = {
    "points": [{"t": "2024-08-19T00:00:00Z", "equity": 10000.0, "drawdown_pct": 0.0}],
    "references": [
        {
            "label": "BTC hold",
            "basis": "price only",
            "symbol": "BTC",
            "ret_pct": 41.2,
            "dd_pct": -57.0,
            "start_close": 58123.4,
            "end_close": 81990.2,
            "series": [
                {"t": "2024-08-19", "equity": 10000.0},
                {"t": "2024-08-20", "equity": 10100.0},
            ],
        },
        {"label": "ETH hold", "ret_pct": 3.0, "dd_pct": -60.0},
    ],
}


def test_the_reference_words() -> None:
    ref = reference_from_curve(CURVE)
    detail = {"id": "btr_1", "status": "COMPLETED", "metrics": {"sharpe_ratio": 1.0}}
    view = build_backtest_view(detail, size="evidence", reference=ref)
    tile = [t for t in view["more_tiles"] if t["key"] == "btc_hold"]
    from keel.tools.outcomes._backtest_view import reference_line

    rendered = [
        reference_line(ref),
        reference_line(ref, comparison=True),
        tile[0]["label"],
    ]
    # Non-vacuity: three strings scanned.
    assert len(rendered) == 3 and all(rendered)
    for text in rendered:
        assert not re.search(r"buy|benchmark|baseline", text, re.IGNORECASE), text
    assert tile[0]["display"] == "+41.2%"
    assert ref["series"] == [["2024-08-19", 10000.0], ["2024-08-20", 10100.0]]


def test_the_reference_is_absent_when_unservable() -> None:
    assert reference_from_curve({"points": [], "references": [None]}) is None
    assert reference_from_curve({"points": []}) is None  # an older keel-api
    detail = {"id": "btr_1", "status": "COMPLETED", "metrics": {"sharpe_ratio": 1.0}}
    view = build_backtest_view(detail, size="evidence", reference=None)
    assert all(t["key"] != "btc_hold" for t in view["more_tiles"])


# ── guard 14 · realism ────────────────────────────────────────────────


def test_realism_maps_the_workers_keys() -> None:
    metrics = {
        "realism": {
            "n_orders": 10,
            "avg_order_notional": 42.0,
            "n_below_live_minimum": 3,
            "share_below_live_minimum": 0.3,
            "live_minimum_notional": 15.0,
            "truncated": False,
        }
    }
    assert realism_from_metrics(metrics) == {
        "n_orders": 10,
        "n_below": 3,
        "share": 0.3,
        "avg_order_notional": 42.0,
        "min_notional": 15.0,
        "truncated": False,
    }
    assert realism_from_metrics({}) is None


# ── guard 18 · halves ─────────────────────────────────────────────────


def _window(days: int) -> dict:
    start = datetime(2024, 1, 1).date()
    last = start + timedelta(days=days - 1)
    return {
        "ran": {
            "start": start.isoformat(),
            "last_bar": last.isoformat(),
            "end_exclusive": (last + timedelta(days=1)).isoformat(),
            "days": days,
        }
    }


def test_halves_only_for_a_long_enough_run() -> None:
    assert (_window(401)["ran"]["days"], _window(40)["ran"]["days"]) == (401, 40)
    long = halves_for(_window(401))
    assert long == [["2024-01-01", "2024-07-19"], ["2024-07-19", "2025-02-05"]]
    assert halves_for(_window(40)) is None


# ── guard 7 · the headroom arm ────────────────────────────────────────


def _run_with(*, sibling: bool, ladder: bool):
    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    client = MagicMock()
    submission = {"id": "btr_new", "status": "queued"}
    if ladder:
        submission["quota"] = [
            {
                "unit": "backtest_runs",
                "label": "backtests",
                "limit": 50,
                "used": 46,
                "remaining": 4,
                "period": "weekly",
                "resets_at": "2026-09-28T00:00:00Z",
                "tier": "critical",
            }
        ]
    client.post.return_value = submission
    detail = {
        "id": "btr_new",
        "status": "COMPLETED",
        "strategy_id": "str_a",
        "end_date": "2026-09-23",
        "start_date": "2024-07-27",
        "metrics": {"sharpe_ratio": 1.0, "total_trades": 50, "win_rate": 50.0},
    }
    rows = [{"id": "btr_old", "status": "COMPLETED", "completed_at": recent}] if sibling else []
    reads: list[str] = []

    def get(path, **_kw):
        reads.append(path)
        if path == "/v1/backtests/btr_new":
            return detail
        if path == "/v1/backtests":
            return {"data": rows, "pagination": {}}
        if path == "/v1/entitlements":
            return {
                "balances": [
                    {
                        "unit": "backtest_runs",
                        "granted": 50,
                        "spent": 10,
                        "available": 40,
                        "period": "weekly",
                        "resets_at": "2026-09-28T00:00:00Z",
                    }
                ]
            }
        return {}

    client.get.side_effect = get
    ctx = ToolContext(api_client=client, is_tty=False)
    env = (
        OUTCOMES["keel_backtest_run"]
        .handler({"strategy_id": "str_a", "skip_readiness": True}, ctx)
        .to_envelope()
    )
    return env, reads


def test_the_headroom_arm() -> None:
    hint_no_ladder, reads = _run_with(sibling=True, ladder=False)
    assert reads.count("/v1/entitlements") == 1
    assert hint_no_ladder["quota"][0]["resets_at"] == "2026-09-28T00:00:00Z"
    hint_ladder, reads = _run_with(sibling=True, ladder=True)
    assert reads.count("/v1/entitlements") == 0 and hint_ladder["quota"][0]["remaining"] == 4
    no_hint_ladder, reads = _run_with(sibling=False, ladder=True)
    assert reads.count("/v1/entitlements") == 0 and no_hint_ladder["quota"][0]["remaining"] == 4
    no_hint, reads = _run_with(sibling=False, ladder=False)
    assert "quota" not in no_hint and reads.count("/v1/entitlements") == 0
    # Non-vacuity on the INPUTS: the remaining values differ pairwise (40, 4).
    assert hint_no_ladder["quota"][0]["remaining"] != hint_ladder["quota"][0]["remaining"]


# ── review 06 §4 (a) · the evidence names its version ─────────────────


def test_the_strategy_view_names_the_evidence_version() -> None:
    from keel.tools.outcomes._strategy_view import _evidence_line

    evidence = {"version": 2, "sharpe": 0.8, "window": {"start": "2024-07-27", "end": "2026-09-23"}}
    older = _evidence_line(evidence, head_version=4)
    assert older.startswith("backtest v2  ")
    assert older.endswith("— latest evidence is from v2; v4 not backtested")
    assert "2024-07-27 → 2026-09-22" in older  # the last covered day
    # CONTROL: the evidence IS HEAD's — no note.
    same = _evidence_line({**evidence, "version": 4}, head_version=4)
    assert "not backtested" not in same


# ── review 06 §4 (b) · HEAD drift names restore ───────────────────────


def _compare_with(head: int):
    client = MagicMock()

    def detail(run_id, seq, sharpe):
        return {
            "id": run_id,
            "status": "COMPLETED",
            "strategy_id": "str_a",
            "strategy_name": "S",
            "sequence_number": seq,
            "commit_id": f"c{seq}",
            "start_date": "2024-07-27",
            "end_date": "2026-09-23",
            "metrics": {"sharpe_ratio": sharpe},
        }

    runs = {
        "btr_1": detail("btr_1", 3, 0.5),
        "btr_2": detail("btr_2", 5, 1.4),
        "btr_3": detail("btr_3", 7, 0.9),
    }

    def get(path, **_kw):
        if path.startswith("/v1/backtests/") and path.count("/") == 3:
            return runs[path.rsplit("/", 1)[1]]
        if path == "/v1/strategies/str_a":
            return {"current_sequence": head}
        return {}

    client.get.side_effect = get
    ctx = ToolContext(api_client=client, is_tty=False)
    return (
        OUTCOMES["keel_backtest_compare"].handler({"backtest_ids": list(runs)}, ctx).to_envelope()
    )


@pytest.mark.parametrize("head", [7, 5])
def test_a_multi_version_set_names_head_and_restore_neutrally(head: int) -> None:
    """The line is the same shape whether HEAD is the top-Sharpe run (v5,
    1.40) or not (v7): a fact about HEAD, the versions, the call — no
    metric and no chosen version (Q-1875, R4)."""
    env = _compare_with(head=head)
    assert env["next"] == (
        f"HEAD is v{head}; this set ran v3, v5, v7: "
        'keel_strategy_restore(strategy_id="str_a", ref="<version>") makes any of them '
        "the next version."
    )
    for word in ("Sharpe", "highest", "best", "1.40"):
        assert word not in env["next"], word
    json.dumps(env)  # serialisable


def test_a_one_version_set_carries_no_restore_line() -> None:
    """CONTROL: runs of ONE version (a window split) have nothing to restore."""
    from keel.tools.outcomes.backtest_compare import _head_drift_next

    client = MagicMock()
    client.get.return_value = {"current_sequence": 4}
    one = [
        {"status": "COMPLETED", "sequence_number": 4},
        {"status": "COMPLETED", "sequence_number": 4},
    ]
    assert _head_drift_next(client, "str_a", one) is None
    two = [*one, {"status": "COMPLETED", "sequence_number": 2}]
    assert _head_drift_next(client, "str_a", two).startswith("HEAD is v4; this set ran v2, v4:")
