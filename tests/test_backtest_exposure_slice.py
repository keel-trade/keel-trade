"""Spec 07 §5–§6 on the agent surface (Q-2025).

* `exposure` — the worker's Q-1993 stamp — is a first-class field on
  `keel_backtest_run` / `keel_backtest_summarize`, an `exposure:` text-block
  line, and `view.exposure_line` for the card: the same sentence the app
  shows. A run from before the stamp carries none of them.
* `keel_backtest_summarize(start, end, capital)` reads keel-api's `/slice`
  (the one owner of the numbers) and relays it as `slice` + a `slice:` line;
  with none of the three it never calls `/slice`.
* The spec 07 §5 sentences are pinned, so a later edit to them is deliberate.

SEED (run 2026-09-27): make `exposure_block` read no stamp (`raw = None`) →
4 red: the block, the thousands, the summarize-surfaces and the run arms; the
absent-stamp arms, the card-source arm, the slice arms and the text pins stay
green (the control). Reverted by reversing the edit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from keel.errors import KeelError, ValidationError
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_run as _bt_run_mod
from keel.tools.outcomes import backtest_summarize as _bt_sum_mod
from keel.tools.outcomes._backtest_view import exposure_block
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import operational_lines


EXPOSURE = {
    "bars_held": 6,
    "n_bars": 25,
    "first_position_at": "2026-09-20T00:00:00+00:00",
    "last_position_at": "2026-09-26T00:00:00+00:00",
}
LINE = "Held positions on 6 of 25 bars · first position Sep 20, 2026"

SLICE = {
    "bars": 7,
    "timeframe": "1d",
    "first_day": "2026-09-19",
    "last_day": "2026-09-25",
    "capital": 3657.36,
    "capital_source": "caller",
    "return_pct": 4.12,
    "pnl": 150.68,
    "max_drawdown_pct": -2.0,
    "summary": (
        "Slice covers 7 daily bars 2026-09-19 → 2026-09-25: +4.12% "
        "(+$150.68 on $3,657.36), max drawdown -2.00%."
    ),
    "basis": "A slice of the run as it ran.",
}


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


def _detail(metrics):
    return {
        "id": "bt_x",
        "status": "COMPLETED",
        "strategy_id": "str_x",
        "strategy_name": "X",
        "start_date": "2026-09-01",
        "end_date": "2026-09-26",
        "metrics": metrics,
    }


def _summarize(ctx, args, metrics, slice_answer=None):
    calls: list[tuple[str, dict]] = []

    def fake_get(path, **kw):
        calls.append((path, kw))
        if path == "/v1/backtests/bt_x":
            return _detail(metrics)
        if path == "/v1/backtests/bt_x/slice":
            if isinstance(slice_answer, Exception):
                raise slice_answer
            return {"job_id": "bt_x", "slice": slice_answer, "run_window": {}}
        if path in ("/v1/backtests/bt_x/results", "/v1/backtests/bt_x/curve"):
            return {}
        raise KeelError(f"not in this fixture: {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        env = (
            OUTCOMES["keel_backtest_summarize"]
            .handler({"backtest_id": "bt_x", **args}, ctx)
            .to_envelope()
        )
    return env, calls


class TestExposure:
    def test_the_block_is_the_stamp_plus_the_apps_line(self):
        assert exposure_block({"exposure": EXPOSURE}) == {**EXPOSURE, "line": LINE}

    def test_counts_carry_thousands_separators_and_no_first_position(self):
        block = exposure_block({"exposure": {"bars_held": 0, "n_bars": 17280}})
        assert block["line"] == "Held positions on 0 of 17,280 bars"

    def test_the_measured_gross_rides_the_line(self, ctx):
        """Q-2502: the Dragos "5×" book — measured on its held bars, stated
        as the worker stamped it, on the block, the text line and the card.

        SEED (2026-10-06): today's `exposure_block` (no gross read) reds this
        arm while `test_the_block_is_the_stamp_plus_the_apps_line` — the
        no-gross control — stays green. Reverted by restoring the edit."""
        stamp = {
            "bars_held": 4141,
            "n_bars": 17280,
            "first_position_at": "2026-09-20T00:00:00+00:00",
            "gross_mean": 1.5112,
            "gross_max": 5.0,
        }
        line = (
            "Held positions on 4,141 of 17,280 bars · gross on held bars avg 1.51×, max 5.00×"
            " · first position Sep 20, 2026"
        )
        block = exposure_block({"exposure": stamp})
        assert block["line"] == line
        assert (block["gross_mean"], block["gross_max"]) == (1.5112, 5.0)
        env, _ = _summarize(ctx, {}, {"sharpe": 0.74, "exposure": stamp})
        assert env["view"]["exposure_line"] == line
        assert f"exposure: {line}" in operational_lines(env)

    def test_a_partial_gross_stamp_states_no_size(self):
        """CONTROL: both numbers or neither — never half a measurement."""
        block = exposure_block({"exposure": {**EXPOSURE, "gross_mean": 0.3}})
        assert block == {**EXPOSURE, "line": LINE}

    @pytest.mark.parametrize(
        "metrics",
        [None, {}, {"exposure": None}, {"exposure": {"bars_held": 3, "n_bars": 0}}],
    )
    def test_no_stamp_means_no_block(self, metrics):
        assert exposure_block(metrics) is None

    def test_summarize_surfaces_it_first_class(self, ctx):
        env, _ = _summarize(ctx, {}, {"sharpe": 0.74, "exposure": EXPOSURE})
        assert env["exposure"]["line"] == LINE
        assert env["exposure"]["bars_held"] == 6 and env["exposure"]["n_bars"] == 25
        assert env["view"]["exposure_line"] == LINE
        assert f"exposure: {LINE}" in operational_lines(env)

    def test_an_old_run_carries_no_exposure(self, ctx):
        env, _ = _summarize(ctx, {}, {"sharpe": 0.74})
        assert "exposure" not in env
        assert "exposure_line" not in env["view"]
        assert not any(line.startswith("exposure:") for line in operational_lines(env))

    def test_run_surfaces_it_on_a_completed_poll(self, ctx, monkeypatch):
        monkeypatch.setattr(_bt_run_mod, "_POLL_INTERVAL_S", 0.0)
        monkeypatch.setattr(_bt_run_mod, "_POLL_FAST_INTERVAL_S", 0.0)
        detail = _detail({"sharpe": 0.74, "exposure": EXPOSURE})

        def fake_get(path, **_kw):
            if path == "/v1/backtests/bt_x":
                return detail
            if path.startswith("/v1/backtests/bt_x/"):
                return {}
            raise KeelError(f"not in this fixture: {path}")

        with (
            patch("keel.client.KeelClient.post", return_value={"id": "bt_x", "status": "queued"}),
            patch("keel.client.KeelClient.get", side_effect=fake_get),
        ):
            env = (
                OUTCOMES["keel_backtest_run"]
                .handler({"strategy_id": "str_x", "commit_id": "c1"}, ctx)
                .to_envelope()
            )
        assert env["exposure"]["line"] == LINE

    def test_the_card_draws_the_line(self):
        from pathlib import Path

        card = (
            Path(_bt_sum_mod.__file__).resolve().parents[2] / "widgets/assets/card-backtest.js"
        ).read_text()
        assert "v.exposure_line" in card and "env.slice.summary" in card


class TestSummarizeSlice:
    def test_no_bounds_never_calls_slice(self, ctx):
        env, calls = _summarize(ctx, {}, {"sharpe": 1.0})
        assert "slice" not in env
        assert all(path != "/v1/backtests/bt_x/slice" for path, _ in calls)
        # Non-vacuity: the detail read happened.
        assert calls[0][0] == "/v1/backtests/bt_x"

    def test_bounds_and_capital_reach_the_slice_read(self, ctx):
        env, calls = _summarize(
            ctx,
            {"start": "2026-09-19", "end": "2026-09-25", "capital": 3657.36},
            {"sharpe": 1.0},
            slice_answer=SLICE,
        )
        (sliced,) = [kw for path, kw in calls if path == "/v1/backtests/bt_x/slice"]
        assert sliced == {"start": "2026-09-19", "end": "2026-09-25", "capital": 3657.36}
        assert env["slice"]["pnl"] == 150.68
        (line,) = [x for x in operational_lines(env) if x.startswith("slice: ")]
        assert line == f"slice: {SLICE['summary']} {SLICE['basis']}"

    def test_capital_alone_slices_the_whole_run(self, ctx):
        _, calls = _summarize(ctx, {"capital": 3000}, {"sharpe": 1.0}, slice_answer=SLICE)
        (sliced,) = [kw for path, kw in calls if path == "/v1/backtests/bt_x/slice"]
        assert sliced == {"capital": 3000}

    def test_a_bound_outside_the_run_is_the_servers_refusal(self, ctx):
        refusal = ValidationError(
            "This run covers 2026-09-01 → 2026-09-25; start 2026-08-01 is before it.",
            error_code="SLICE_OUTSIDE_RUN",
        )
        with pytest.raises(ValidationError) as exc:
            _summarize(ctx, {"start": "2026-08-01"}, {"sharpe": 1.0}, slice_answer=refusal)
        assert "2026-09-01 → 2026-09-25" in str(exc.value)

    @pytest.mark.parametrize(
        ("args", "code"),
        [
            ({"start": "last week"}, "invalid_slice_bound"),
            ({"end": "2026/09/25"}, "invalid_slice_bound"),
            # The declared `exclusiveMinimum: 0` is enforced before the
            # handler runs (Q-2270), with the generic out-of-range code.
            ({"capital": 0}, "argument_out_of_range"),
            ({"capital": True}, "invalid_slice_capital"),
        ],
    )
    def test_malformed_arguments_refuse_before_any_read(self, ctx, args, code):
        with patch("keel.client.KeelClient.get") as get, pytest.raises(KeelError) as exc:
            OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "bt_x", **args}, ctx)
        assert exc.value.error_code == code
        get.assert_not_called()


class TestTheSpec07Text:
    """Pinned so a later edit to the approved agent text is deliberate."""

    def test_start_date_says_the_default_window_and_short_windows(self):
        full = OUTCOMES["keel_backtest_run"].input_schema["properties"]["start_date"]
        assert full["description"] == _bt_run_mod.START_DATE_TEXT
        assert (
            "when omitted the run uses the default window for the strategy's timeframe "
            "(5min 60 days, 15min 90 days, coarser 5,000 bars, capped at available data)"
        ) in full["description"]
        assert "Any window works, including the last day or week" in full["description"]

    def test_the_warmup_lines_are_on_both_profiles(self):
        tool = OUTCOMES["keel_backtest_run"]
        assert "Warm-up: indicators need history before they produce signals." in (
            _bt_run_mod.WARMUP_TEXT
        )
        assert "`notes` flags a window that was all warm-up." in _bt_run_mod.WARMUP_TEXT
        # 05 R-L4 / Q-2012: a fact about warm-up, never a further run or a date.
        assert "re-run" not in _bt_run_mod.WARMUP_TEXT
        assert "earlier `start_date`" not in _bt_run_mod.WARMUP_TEXT
        assert _bt_run_mod.WARMUP_TEXT in tool.description
        # Listed: the same words on `start_date` (the listed description total
        # is at its ratified ceiling), never dropped.
        listed_start = tool.listed_input_schema["properties"]["start_date"]["description"]
        assert _bt_run_mod.WARMUP_TEXT in listed_start
        assert "trades" not in listed_start

    def test_summarize_parameters_carry_the_slice_text(self):
        tool = OUTCOMES["keel_backtest_summarize"]
        props = tool.input_schema["properties"]
        assert set(props) == {"backtest_id", "start", "end", "capital"}
        assert props["start"]["description"] == _bt_sum_mod.SLICE_PARAM_TEXT
        assert "e.g. its last week on $3,000" in _bt_sum_mod.SLICE_PARAM_TEXT
        assert 'For "if I had started on X", run a backtest from X instead.' in (
            _bt_sum_mod.SLICE_PARAM_TEXT
        )
        listed = tool.listed_input_schema["properties"]["start"]["description"]
        assert listed == _bt_sum_mod.SLICE_PARAM_TEXT.replace("already trading", "already running")
