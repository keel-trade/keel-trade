"""The `window:` line: copyable as input, names the clock, claims no warm-up.

Q-2422 (founder ruling 2026-10-04): every completed run's window line names
the run's clock and carries the pair that re-runs it, so putting a variant on
the same window is one copy:

    window: 1h · Mar 9, 2026 – Oct 3, 2026 (209 days, the 1h default)
            · same window: start_date=2026-03-09 end_date=2026-10-04

"the 1h default" only when keel-api served `window.default` (submit stamped
that an omitted start took the clock's default) — never inferred. The clock
is the `config:` line's own (`config_from_source` → `clock_label`), read from
the run's commit source.

Q-1991 (founder ruling 2026-10-04): no warm-up count, for any value — the
stored count cannot tell warm-up from a decision to stay flat. The exposure
line stays: it is the truthful fact.

Every envelope here comes from the REAL `keel_backtest_summarize` handler
(HTTP faked at `KeelClient.get` with keel-api's wire shapes), and the line is
the one `operational_lines` puts in the text block.

SEEDS (run 2026-10-04; each reverted by reversing the edit):
* On today's code (this file copied into a scratch worktree at e4533b8ab):
  8 of 10 red — every line arm, both no-warm-up arms and both approach arms;
  the fixture non-vacuity arm and the exposure control stay green.
* restore `detail.append(f"{window['warmup_bars']:,} warm-up bars")` in
  `window_line_for` → 6 red: both no-warm-up arms and the four exact-line
  arms (every fixture carries an integer count); the fixture, exposure and
  two approach arms stay green.
* drop the `default` clause in `window_line_for` → exactly the default-run
  arm reds.
* print `ran.last_bar` as the pair's end in `window_rerun_pair` → 4 red: the
  default, explicit, old-run and moved arms; the warm-up, exposure, fixture
  and approach arms stay green.
* stop passing `clock=` from `_mcp_adapter._render_field` → 3 red: the
  default, explicit and moved arms; the old-run arm (no source served, so no
  clock either way) stays green.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_summarize as _bt_sum_mod  # noqa: F401
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import operational_lines


_SOURCE_1H = """\
Globals(target_timeframe="1h")
Universe(mode="manual", symbols=["BTC", "ETH"])
Pipeline([
    PriceDataLoader(timeframe="1h"),
    TimeSeriesMeanReversionForecast(k=20),
    ForecastScaler(),
    ForecastWeightNormalizer(),
])
"""

_RAN = {
    "start": "2026-03-09",
    "end_exclusive": "2026-10-04",
    "last_bar": "2026-10-03",
    "days": 209,
}

#: An omitted start that took the 1h default (Q-2422's stamp, served).
DEFAULT_WINDOW = {
    "requested": {"start": None, "end": None},
    "ran": _RAN,
    "rerun": {"start_date": "2026-03-09", "end_date": "2026-10-04"},
    "default": {"timeframe": "1h", "days": 209},
    "warmup_bars": 0,
    "chart_start": None,
    "policy": "consumed_eras",
}
#: The same window asked for explicitly — and a non-zero warm-up count.
EXPLICIT_WINDOW = {
    "requested": {"start": "2026-03-09", "end": "2026-10-04"},
    "ran": _RAN,
    "rerun": {"start_date": "2026-03-09", "end_date": "2026-10-04"},
    "warmup_bars": 26,
    "chart_start": None,
    "policy": "consumed_eras",
}
#: A run from before the stamp: no `rerun`, no `default`, an omitted start.
OLD_WINDOW = {
    "requested": {"start": None, "end": None},
    "ran": _RAN,
    "warmup_bars": 0,
    "chart_start": None,
    "policy": None,
}
#: A requested window the platform moved and capped (the control clauses).
MOVED_WINDOW = {
    "requested": {"start": "2024-01-01", "end": "2026-10-10"},
    "ran": {
        "start": "2024-07-27",
        "end_exclusive": "2026-10-04",
        "last_bar": "2026-10-03",
        "days": 799,
    },
    "rerun": {"start_date": "2024-07-27", "end_date": "2026-10-04"},
    "warmup_bars": 30,
    "chart_start": "2024-08-19",
    "start_reason_code": "platform_floor",
    "start_reason": "Keel's price history begins Jul 27, 2024",
}

EXPOSURE = {
    "bars_held": 608,
    "n_bars": 799,
    "first_position_at": "2024-09-21T00:00:00+00:00",
    "last_position_at": "2026-10-03T00:00:00+00:00",
}


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


def _window_line(ctx, window: dict, *, source: str | None = _SOURCE_1H, metrics=None) -> str:
    detail = {
        "id": "bt_w",
        "status": "COMPLETED",
        "strategy_id": "str_w",
        "strategy_name": "W",
        "commit_id": "cmt_w",
        "sequence_number": 4,
        "start_date": window["ran"]["start"] + " 00:00:00+00:00",
        "end_date": window["ran"]["end_exclusive"] + " 00:00:00+00:00",
        "metrics": {"sharpe_ratio": 0.74, **(metrics or {})},
        "window": window,
    }

    def fake_get(path, **kw):
        if path == "/v1/backtests/bt_w":
            return detail
        if path == "/v1/strategies/str_w/versions/cmt_w/source":
            if source is None:
                raise KeelError("no source in this fixture")
            return {"source": source}
        if path in ("/v1/backtests/bt_w/results", "/v1/backtests/bt_w/curve"):
            return {}
        raise KeelError(f"not in this fixture: {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        env = OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "bt_w"}, ctx)
        env = env.to_envelope()
    lines = operational_lines(env)
    (line,) = [x for x in lines if x.startswith("window: ")]
    return line, lines, env


def test_the_fixture_windows_are_real_windows():
    """Non-vacuity, read from the fixtures (no seed moves them): every window
    has a start, a last bar and an exclusive end one day past the last bar."""
    for window in (DEFAULT_WINDOW, EXPLICIT_WINDOW, OLD_WINDOW, MOVED_WINDOW):
        ran = window["ran"]
        assert ran["start"] and ran["last_bar"] and ran["end_exclusive"]
        assert date.fromisoformat(ran["end_exclusive"]) == date.fromisoformat(
            ran["last_bar"]
        ) + timedelta(days=1)


def test_a_default_window_names_the_clock_the_default_and_the_pair(ctx):
    line, _, env = _window_line(ctx, DEFAULT_WINDOW)
    assert env["view"]["config"]["clock"] == "1h"  # the config line's own clock
    assert line == (
        "window: 1h · Mar 9, 2026 – Oct 3, 2026 (209 days, the 1h default) · "
        "same window: start_date=2026-03-09 end_date=2026-10-04"
    )


def test_an_explicit_window_carries_the_pair_but_no_default(ctx):
    line, _, _ = _window_line(ctx, EXPLICIT_WINDOW)
    assert line == (
        "window: 1h · Mar 9, 2026 – Oct 3, 2026 (209 days) · "
        "same window: start_date=2026-03-09 end_date=2026-10-04"
    )


def test_a_run_without_the_stamp_names_no_default(ctx):
    """An omitted start on a run from before the stamp: no default clause —
    it may have been the full history — and no clock when no source is read.
    The pair falls back to the same served facts it is made of."""
    line, _, _ = _window_line(ctx, OLD_WINDOW, source=None)
    assert "default" not in line
    assert line == (
        "window: Mar 9, 2026 – Oct 3, 2026 (209 days) · "
        "same window: start_date=2026-03-09 end_date=2026-10-04"
    )


@pytest.mark.parametrize("window", [DEFAULT_WINDOW, EXPLICIT_WINDOW], ids=["0-bars", "26-bars"])
def test_the_line_claims_no_warm_up(ctx, window):
    """Q-1991: no warm-up count for ANY value. Non-vacuity: the served
    window really carries an integer count the old line printed."""
    assert isinstance(window["warmup_bars"], int)
    line, lines, _ = _window_line(ctx, window)
    assert "warm" not in line.lower()
    assert not any("warm-up bars" in x for x in lines)


def test_control_the_moved_and_capped_clauses_still_render(ctx):
    line, _, _ = _window_line(ctx, MOVED_WINDOW)
    assert line == (
        "window: 1h · Jul 27, 2024 – Oct 3, 2026 (799 days · chart from Aug 19, 2024); "
        "start moved from Jan 1, 2024 — Keel's price history begins Jul 27, 2024; "
        "end capped at today · same window: start_date=2024-07-27 end_date=2026-10-04"
    )


def test_control_the_exposure_line_is_unchanged(ctx):
    _, lines, _ = _window_line(ctx, MOVED_WINDOW, metrics={"exposure": EXPOSURE})
    assert "exposure: Held positions on 608 of 799 bars · first position Sep 21, 2024" in lines


class TestTheApproachSentence:
    """The one founder-approved approach sentence, pinned, in the run tool's
    description (full profile) and on its listed `start_date` (the listed
    descriptions are at their ratified ceiling; parameter copy is outside)."""

    TEXT = (
        "Windows default by clock (shorter on fine clocks). To compare variants — another "
        "clock, parameters or universe — run the first, then pass its start_date/end_date "
        "to the others; a finer clock may take longer, and a start before its data is "
        "moved and named."
    )

    def test_the_text_is_pinned(self):
        from keel.tools.outcomes._backtest_view import WINDOW_APPROACH_TEXT

        assert WINDOW_APPROACH_TEXT == self.TEXT

    def test_it_rides_the_run_tools_description_and_listed_start_date(self):
        from keel.tools.outcomes.backtest_run import BACKTEST_RUN as tool

        assert self.TEXT in tool.description
        listed = tool.listed_input_schema["properties"]["start_date"]["description"]
        assert self.TEXT in listed
