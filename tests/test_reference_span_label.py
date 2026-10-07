"""The BTC hold reference names its span (Q-1870).

Two runs labelled "Dec 5, 2024 – Sep 22, 2026" showed BTC hold −8.8% and
−15.6%: each reference starts at its run's chart start (the first bar its
indicators were valid — Jan 15 vs Jan 21), not at the headline window, and
the line said "same span". Now the line carries the days its closes sit on,
and a comparison asks keel-api for ONE reference over the span every run
covers, labelled so only when the server echoes that span.

    SEED (run 2026-09-23): `span = "same span"` for the non-comparison arm in
    `reference_line` → `test_a_run_line_names_its_days` red; the compare arms
    stay green.
    SEED (run): `common = None` (drop the `common_reference_span(...)` call)
    in backtest_compare's handler →
    `test_a_compare_asks_for_the_common_span_on_run_one` red; the
    older-server arm stays green.
"""

from __future__ import annotations

from unittest.mock import patch

from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_compare as _bt_cmp_mod  # noqa: F401
from keel.tools.outcomes._backtest_view import common_reference_span, reference_line
from keel.tools.outcomes._base import ToolContext

from .test_outcomes_backtest_compare import METRICS_A, METRICS_B, SRC_A, SRC_B


REF = {
    "label": "BTC hold",
    "basis": "price only",
    "ret_pct": -15.6,
    "dd_pct": -30.1,
    "start_close": 102000.0,
    "end_close": 86100.0,
    "rebased_at": "2025-01-21",
    "end_close_at": "2026-09-21",
}


def test_a_run_line_names_its_days():
    line = reference_line(REF)
    assert line.startswith("BTC hold, price only, Jan 21, 2025 – Sep 21, 2026: −15.6%")
    assert "same span" not in line


def test_a_compare_line_says_which_span():
    common = reference_line({**REF, "common_span": True}, comparison=True)
    assert "Jan 21, 2025 – Sep 21, 2026 (the span every run covers)" in common
    own = reference_line(REF, comparison=True)
    assert "Jan 21, 2025 – Sep 21, 2026 (run 1's span)" in own


def test_control_an_older_server_without_days_keeps_the_old_words():
    bare = {k: v for k, v in REF.items() if k not in ("rebased_at", "end_close_at")}
    assert "same span" in reference_line(bare)
    assert "over run 1's span" in reference_line(bare, comparison=True)


def _metrics(base: dict, chart_start: str, effective_end: str) -> dict:
    return {**base, "chart_start": chart_start, "effective_end": effective_end}


def test_the_common_span_is_the_intersection_of_every_chart():
    details = [
        {"metrics": _metrics(METRICS_A, "2025-01-15T12:00:00+00:00", "2026-09-22T12:00:00+00:00")},
        {"metrics": _metrics(METRICS_B, "2025-01-21T00:00:00+00:00", "2026-09-23T00:00:00+00:00")},
    ]
    assert common_reference_span(details) == ("2025-01-21", "2026-09-22")
    # A run without the stamps: no common span is claimed.
    assert common_reference_span([*details, {"metrics": dict(METRICS_A)}]) is None


_B_CHART = ("2025-01-21T00:00:00+00:00", "2026-09-23T00:00:00+00:00")


def _compare(curve_payload_for, holds=("BTC",), b_chart=_B_CHART):
    asked: dict[str, dict] = {}
    details = {
        "btr_a": {
            "id": "btr_a",
            "status": "COMPLETED",
            "strategy_id": "str_1",
            "strategy_name": "Momentum",
            "commit_id": "cmt_a",
            "sequence_number": 1,
            "start_date": "2024-12-05",
            "end_date": "2026-09-23",
            "metrics": _metrics(
                METRICS_A, "2025-01-15T12:00:00+00:00", "2026-09-22T12:00:00+00:00"
            ),
        },
        "btr_b": {
            "id": "btr_b",
            "status": "COMPLETED",
            "strategy_id": "str_1",
            "strategy_name": "Momentum",
            "commit_id": "cmt_b",
            "sequence_number": 2,
            "start_date": "2024-12-05",
            "end_date": "2026-09-23",
            "metrics": _metrics(METRICS_B, *b_chart),
        },
    }

    def fake_get(path, **kw):
        if path.endswith("/curve"):
            run_id = path.split("/")[3]
            asked[run_id] = kw
            return curve_payload_for(run_id, kw)
        if path.startswith("/v1/backtests/"):
            return details[path.rsplit("/", 1)[1]]
        if "/versions/" in path:
            return {"source": SRC_A if "cmt_a" in path else SRC_B}
        return {}

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        env = (
            OUTCOMES["keel_backtest_compare"]
            .handler(
                {"backtest_ids": ["btr_a", "btr_b"], **({"holds": list(holds)} if holds else {})},
                ToolContext(is_tty=False),
            )
            .to_envelope()
        )
    return env, asked


def _payload(run_id, kw, *, echo: bool):
    start = kw.get("reference_start") or ("2025-01-15" if run_id == "btr_a" else "2025-01-21")
    end = kw.get("reference_end") or "2026-09-22"
    out = {
        "points": [{"t": "2025-02-01T00:00:00+00:00", "equity": 10000.0, "drawdown_pct": 0.0}],
        "start": start,
        "end": end,
        "source_points": 10,
        "references": [{**REF, "rebased_at": start, "end_close_at": "2026-09-21"}, None, None],
    }
    if echo:
        out["reference_span"] = {"start": start, "end": end}
    return out


def test_a_compare_asks_for_the_common_span_on_run_one():
    env, asked = _compare(lambda run_id, kw: _payload(run_id, kw, echo=True))
    assert asked["btr_a"].get("reference_start") == "2025-01-21"
    assert asked["btr_a"].get("reference_end") == "2026-09-22"
    assert "reference_start" not in asked["btr_b"]  # one reference for the set
    assert env["reference"]["common_span"] is True
    assert env["reference"]["rebased_at"] == "2025-01-21"


def test_control_an_older_server_that_ignores_the_span_is_labelled_run_ones():
    # keel-api without the parameters: it serves run 1's own span and echoes
    # nothing, so the reference is NOT claimed to be the common one.
    env, _ = _compare(lambda run_id, kw: _payload(run_id, {}, echo=False))
    assert env["reference"]["rebased_at"] == "2025-01-15"  # run 1's own span
    # Said in the structured result too (Q-2270), not only in the text line.
    assert env["reference"]["common_span"] is False
    assert "did not confirm" in env["reference"]["span_note"]
    line = reference_line(env["reference"], comparison=True)
    assert "(run 1's span)" in line


def test_runs_that_share_no_span_mark_the_hold_line_as_run_ones():
    """Q-2270: `holds` promises the span every run covers. Runs whose charts
    do not overlap have none, so the line covers run 1's span — marked
    `common_span: false` with the reason, and once in the comparison notes.
    Seed: dropping `_hold_span_fallback`'s marking reds this arm and the
    older-server arm; the common-span arm stays green."""
    env, asked = _compare(
        lambda run_id, kw: _payload(run_id, kw, echo=True),
        b_chart=("2027-01-01T00:00:00+00:00", "2027-03-01T00:00:00+00:00"),
    )
    assert "reference_start" not in asked["btr_a"]  # no common span to ask for
    ref = env["reference"]
    assert ref["common_span"] is False
    assert "share no common span" in ref["span_note"]
    assert any("Hold lines cover run 1's span" in n for n in env["view"].get("notes") or [])


def test_several_holds_are_one_line_each_over_the_common_span():
    def payload(run_id, kw):
        out = _payload(run_id, kw, echo=True)
        out["references"] = [
            {**out["references"][0], "symbol": sym, "label": f"{sym} hold", "ret_pct": r}
            for sym, r in (("BTC", -15.6), ("ETH", -40.2), ("SOL", 12.0))
        ]
        return out

    env, asked = _compare(payload, holds=("SOL", "BTC"))
    assert asked["btr_a"]["references"] == "true"
    assert [r["label"] for r in env["references"]] == ["SOL hold", "BTC hold"]
    assert env["reference"]["label"] == "SOL hold"
    assert all(r["common_span"] for r in env["references"])


def test_a_hold_outside_the_set_is_refused():
    import pytest
    from keel.errors import KeelError

    with pytest.raises(KeelError):
        _compare(lambda run_id, kw: _payload(run_id, kw, echo=True), holds=("DOGE",))
