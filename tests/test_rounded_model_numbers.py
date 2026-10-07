"""Model-visible numbers are at display precision (Q-1877, R4 probe).

The R4 agent read "Sharpe 2.7447805047596745" in the strategy view's text and
in `keel_library_list`'s JSON; the card rounds the same numbers to two places.

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* `_strategy_view._evidence_line` prints the raw Sharpe again
  (`f"Sharpe {evidence.get('sharpe')}"`) — the evidence test reds; the
  library tests stay green;
* `library._headline` returns the headline unchanged — the library-list test
  reds; the pass-through control stays green.
"""

from __future__ import annotations

import json
import pathlib
import re

import respx
from httpx import Response


HERE = pathlib.Path(__file__).resolve().parent
RECORDED = json.loads((HERE / "fixtures" / "channels" / "strategy_get.envelope.json").read_text())
API = "https://api.test.keel"

#: More than four decimals anywhere is an unrounded float.
LONG_FLOAT = re.compile(r"\d\.\d{5,}")


def test_the_strategy_evidence_line_is_rounded() -> None:
    from keel.tools.outcomes._strategy_view import build_view

    meta = dict(RECORDED["metadata"])
    meta["latest_backtest_metrics"] = {
        "sharpe_ratio": 2.7447805047596745,
        "total_return_pct": 67.61234987,
        "max_drawdown": 11.73491234,
        "win_rate_pct": 44.4444444,
        "total_trades": 123,
    }
    view = build_view(meta["graph"], meta)
    line = next(line for line in view["markdown"].splitlines() if line.startswith("backtest"))
    assert "Sharpe 2.74 · return +67.6% · max DD −11.7% · 123 trades" in line, line
    assert not LONG_FLOAT.search(view["markdown"])


@respx.mock
def test_library_list_headlines_are_rounded(monkeypatch) -> None:
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.setenv("KEEL_API_KEY", "test-key")
    monkeypatch.setenv("KEEL_API_URL", API)
    monkeypatch.setattr("keel.client.time.sleep", lambda *_: None)
    _bootstrap()
    # The exact shape `libs/strategy_library/data/entries/*/entry.json` stores.
    headline = {
        "max_drawdown_pct": 39.66028040140011,
        "price_only_sharpe": 0.8478533906476133,
        "sharpe": 0.7554992060140537,
        "total_return_pct": 56.454531127965964,
        "trades": 1143,
    }
    respx.get(f"{API}/v1/library").mock(
        return_value=Response(
            200,
            json={"count": 1, "entries": [{"slug": "adx-trend-crypto", "headline": headline}]},
        )
    )
    env = OUTCOMES["keel_library_list"].handler({}, ToolContext()).to_envelope()
    assert env["entries"][0]["headline"] == {
        "max_drawdown_pct": 39.7,
        "price_only_sharpe": 0.85,
        "sharpe": 0.76,
        "total_return_pct": 56.5,
        "trades": 1143,
    }
    assert not LONG_FLOAT.search(json.dumps(env))


def test_a_headline_that_is_not_numbers_passes_through() -> None:
    """CONTROL: rounding never invents or drops a field."""
    from keel.tools.outcomes.library import _headline

    assert _headline(None) is None
    assert _headline({"sharpe": None, "note": "n/a"}) == {"sharpe": None, "note": "n/a"}
