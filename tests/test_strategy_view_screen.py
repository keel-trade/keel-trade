"""A screen saved or checked through compose reads as a screen (Q-3637).

The founder's claude.ai test (2026-11-05) saved a screen and read
"next: No backtest of this strategy has been run yet.", then called
`keel_screener_view` before any run existed; the card showed the pipeline
and nothing a screen produces, and the dry run's text said
"Preview · 3 blocks · valid" with neither the universe nor any unit. Each arm
below drives the real compose handler (or `build_view`) and pairs the screen
with a strategy control arm that must stay exactly as it was.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap
from keel.tools.outcomes import _strategy_view as sv


SCREEN = """Globals(target_timeframe="1h")
Universe(mode="top_volume", top_n=200, market="perp")
Outputs(slots=["funding_now", "ret_1d"])
Pipeline([
    {
        "funding": [FundingDataLoader(), Scale(by=8760, unit="rate_per_year"), Store("funding_now")],
        "returns": [PriceDataLoader(), ROC(period=24), Store("ret_1d")],
    },
])
"""

STRATEGY = """Globals(target_timeframe="1d")
Universe(mode="top_volume", top_n=30, market="perp")
Execution(rebalance="buffered", buffer_threshold=0.2, buffer_mode="relative", rebalance_method="to_edge")
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="s")
"""

SID = "str_scr1"

#: keel-api's `GET /v1/screeners/{id}/views` `units` rows (contract item 3).
SERVED_UNITS = [
    {
        "output": "funding_now",
        "unit": "rate_per_year",
        "sentence": "funding_now is a decimal per year: 0.05 = 5% per year",
    },
    {"output": "ret_1d", "unit": "percent", "sentence": "ret_1d is percent × 100: 5.0 = 5%"},
]


def _graph(source: str) -> dict:
    """The graph keel-api serves for ``source`` — its own `spec_to_graph`."""
    from pipeline_engine.dsl.emitter import spec_to_graph
    from pipeline_engine.dsl.parser import parse_strategy

    return spec_to_graph(parse_strategy(source)).to_dict()


class _Client:
    """The keel-api reads a compose save and dry run make, canned."""

    def __init__(
        self, source: str, *, views: Any = None, runs: Any = None, unchanged: bool = False
    ):
        self.source = source
        self.views = views
        self.runs = runs if runs is not None else []
        self.unchanged = unchanged
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(("GET", path))
        if path == f"/v1/strategies/{SID}":
            return {
                "strategy_id": SID,
                "name": "Funding now",
                "status": "DRAFT",
                "current_sequence": 1,
                "graph": _graph(self.source),
                "backtest_state": "never_backtested",
            }
        if path == f"/v1/strategies/{SID}/versions/HEAD/source":
            return {"source": self.source, "sequence_number": 1}
        if path == f"/v1/screeners/{SID}/views":
            if isinstance(self.views, Exception):
                raise self.views
            return self.views if self.views is not None else {}
        if path == f"/v1/screeners/{SID}/runs":
            return self.runs
        if path == "/v1/backtests":
            return {"data": [], "pagination": {}}
        return {}

    def post(self, path: str, json: dict | None = None, **params: Any) -> Any:
        self.calls.append(("POST", path))
        if path == "/v1/strategies":
            return {"strategy_id": SID, "current_sequence": 1}
        if path == "/v1/strategies/parse":
            return {"graph": _graph(json["source"])}
        return {}

    def patch(self, path: str, json: dict | None = None) -> Any:
        self.calls.append(("PATCH", path))
        return {"strategy_id": SID, "current_sequence": 1, "unchanged": self.unchanged}


@pytest.fixture(autouse=True)
def _boot(monkeypatch):
    _bootstrap()
    # The compile endpoint is the server's; a canned "compiled" keeps the
    # dry run's own verdict the subject.
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"compiled": True}, raising=False
    )


def _compose(args: dict, client: _Client) -> dict:
    ctx = ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")
    return OUTCOMES["keel_strategy_compose"].handler(args, ctx).to_envelope()


# ── The save's `next` ─────────────────────────────────────────────────


def test_a_saved_screen_points_to_the_run_and_a_strategy_still_to_no_backtest():
    """# SEED (2026-11-05): in strategy_compose._handler replace
    `if screen:` with `if False:` before the next-line choice — the screen
    arm reds on the backtest sentence; the strategy arm stays green."""
    env = _compose({"source": SCREEN, "name": "Funding now"}, _Client(SCREEN))
    assert env["next"] == (
        "This version of the screen has not run yet, so it has no values to view; "
        f'keel_screener_run(strategy_id="{SID}") runs it.'
    )
    assert "backtest" not in env["next"].lower()
    # Control arm: the strategy save is untouched.
    strat = _compose({"source": STRATEGY, "name": "S"}, _Client(STRATEGY))
    assert strat["next"] == "No backtest of this strategy has been run yet."


def test_an_unchanged_screen_save_with_a_run_of_its_version_says_nothing():
    """A save that wrote no version keeps HEAD; a completed run of HEAD is
    the values to view, so no run line. A run of an OLDER version is not."""
    ran = [{"run_id": "rr_1", "status": "completed", "sequence": 1}]
    env = _compose(
        {"source": SCREEN, "strategy_id": SID}, _Client(SCREEN, runs=ran, unchanged=True)
    )
    assert "next" not in env
    older = [{"run_id": "rr_0", "status": "completed", "sequence": 0}]
    env = _compose(
        {"source": SCREEN, "strategy_id": SID}, _Client(SCREEN, runs=older, unchanged=True)
    )
    assert env["next"].startswith("This version of the screen has not run yet")


def test_the_run_call_is_named_only_where_screens_are_served(monkeypatch):
    """R-L4: a surface without the screen tools never names one."""
    from keel.tools.outcomes import strategy_compose

    monkeypatch.setattr("keel.tools.outcomes._toolsets.screens_served", lambda: False)
    assert strategy_compose.screen_not_run_line(SID) == (
        "This version of the screen has not run yet, so it has no values to view."
    )


# ── The save's view: outputs, units, no backtest note ────────────────


def test_a_saved_screen_view_names_its_outputs_in_the_served_units():
    """# SEED (2026-11-05): in strategy_compose._handler drop the
    `view = with_outputs(...)` call — the units/words assertions red while
    the names (read from the graph) stay."""
    client = _Client(SCREEN, views={"revision": 0, "units": SERVED_UNITS})
    env = _compose({"source": SCREEN, "name": "Funding now"}, client)
    view = env["view"]
    assert [o["name"] for o in view["outputs"]] == ["funding_now", "ret_1d"]
    assert [o.get("words") for o in view["outputs"]] == ["% a year", "%"]
    assert ("GET", f"/v1/screeners/{SID}/views") in client.calls
    md = view["markdown"]
    assert "outputs   funding_now (% a year), ret_1d (%)" in md
    assert "units     funding_now is a decimal per year: 0.05 = 5% per year" in md
    assert "universe  Top 200 by volume · HL perps" in md
    # A screen is run, never backtested: no backtest note anywhere.
    assert "evidence_note" not in view
    assert "backtest" not in md.lower()


def test_without_served_units_the_save_reads_its_own_validation_units():
    """Contract item 3 degrades cleanly: a keel-api that serves no `units`
    (or fails the read) leaves the local validator's — the same derivation."""
    for views in ({"revision": 0}, RuntimeError("503")):
        env = _compose({"source": SCREEN, "name": "Funding now"}, _Client(SCREEN, views=views))
        assert [o.get("unit") for o in env["view"]["outputs"]] == ["rate_per_year", "percent"]


def test_a_strategy_save_view_carries_no_outputs():
    env = _compose({"source": STRATEGY, "name": "S"}, _Client(STRATEGY))
    assert "outputs" not in env["view"]
    assert "outputs " not in env["view"]["markdown"]


def test_a_screen_receipt_still_says_what_it_produces():
    """`present="receipt"` keeps one header line plus the screen's facts."""
    env = _compose(
        {"source": SCREEN, "name": "Funding now", "present": "receipt"},
        _Client(SCREEN, views={"units": SERVED_UNITS}),
    )
    lines = env["view"]["markdown"].splitlines()
    assert lines[0].startswith("**Funding now** · v1")
    assert "universe  Top 200 by volume · HL perps" in lines
    assert "outputs   funding_now (% a year), ret_1d (%)" in lines


# ── The dry run ───────────────────────────────────────────────────────


def test_a_screen_dry_run_names_universe_outputs_and_units():
    """# SEED (2026-11-05): in _strategy_view._receipt_markdown delete the
    universe `lines.append` — the universe assertion reds, the outputs one
    stays (the control the seed cannot move)."""
    env = _compose({"source": SCREEN, "dry_run": True}, _Client(SCREEN))
    lines = env["view"]["markdown"].splitlines()
    assert lines[0] == "Preview · 6 blocks · valid", lines
    assert "universe  Top 200 by volume · HL perps" in lines
    assert "outputs   funding_now (% a year), ret_1d (%)" in lines
    units = [line for line in lines if line.startswith("units     ")]
    assert units and "0.05 = 5% per year" in units[0] and "5.0 = 5%" in units[0]


def test_a_strategy_dry_run_names_its_universe_and_nothing_screen_shaped():
    env = _compose({"source": STRATEGY, "dry_run": True}, _Client(STRATEGY))
    lines = env["view"]["markdown"].splitlines()
    assert lines[0].startswith("Preview · ") and lines[0].endswith(" · valid")
    assert "universe  Top 30 by volume · HL perps" in lines
    assert not any(line.startswith(("outputs", "units")) for line in lines)


# ── build_view, pure ──────────────────────────────────────────────────


def test_build_view_never_gives_a_screen_the_backtest_note():
    """`backtest_state: never_backtested` is keel-api's word for a strategy;
    a screen's view drops it. The strategy control arm keeps it."""
    meta = {"name": "x", "current_sequence": 1, "backtest_state": "never_backtested"}
    screen = sv.build_view(_graph(SCREEN), meta)
    strategy = sv.build_view(_graph(STRATEGY), meta)
    assert "evidence_note" not in screen and screen["outputs"]
    assert strategy["evidence_note"] == "No backtest yet." and "outputs" not in strategy


def test_an_output_with_an_unknown_unit_reads_by_its_name_alone():
    """Founder 2026-11-04: no "unknown" unit label anywhere."""
    rows = sv.outputs_block(
        {"outputs": ["oi"]}, [{"output": "oi", "unit": "unknown", "sentence": "oi is unknown"}]
    )
    assert rows == [{"name": "oi", "unit": "unknown", "sentence": "oi is unknown"}]
    assert sv._outputs_text(rows) == "oi"


def test_unit_words_are_the_screen_pages_words():
    """`OUTPUT_UNIT_WORDS` is the SDK twin of `libs/screeners/copy.py`
    `UNIT_DISPLAY_WORDS` minus `unknown` (read from the source's literal:
    this lane has no `libs` on its path).

    # SEED: change "% a year" in OUTPUT_UNIT_WORDS to "%/yr" — reds."""
    copy = Path(__file__).resolve().parents[4] / "libs/screeners/copy.py"
    tree = ast.parse(copy.read_text(encoding="utf-8"))
    served = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "UNIT_DISPLAY_WORDS" for t in node.targets)
    )
    # Non-vacuous: the served map covers the unit vocabulary.
    assert len(served) >= 10 and "rate_per_year" in served
    assert {k: v for k, v in served.items() if k != "unknown"} == sv.OUTPUT_UNIT_WORDS


# ── Labels from the saved view, and keel_strategy_get (Q-3639) ────────

#: The same rows once keel-api serves each output's view column label
#: (Q-3638, CONTRACT field 3): one label that states its unit, one that does
#: not, and the label equal to the name a view with no Column serves.
LABELLED_UNITS = [
    {**SERVED_UNITS[0], "label": "Funding now, a year"},
    {**SERVED_UNITS[1], "label": "1-day return"},
]


def test_a_saved_screen_reads_its_outputs_by_their_view_labels():
    """# SEED: in `_strategy_view.outputs_block` drop the `row["label"] = …`
    line — the labelled arm reds on the raw names; the unlabelled control
    (the label IS the name) stays exactly as before."""
    client = _Client(SCREEN, views={"revision": 2, "units": LABELLED_UNITS})
    env = _compose({"source": SCREEN, "name": "Funding now"}, client)
    view = env["view"]
    # Not vacuous: both outputs came back with their served rows.
    assert [o["name"] for o in view["outputs"]] == ["funding_now", "ret_1d"]
    assert [o.get("label") for o in view["outputs"]] == ["Funding now, a year", "1-day return"]
    # The label that already says "a year" carries no second unit; the other
    # keeps its unit words. A comma inside a label joins the rows with ";".
    assert "outputs   Funding now, a year; 1-day return (%)" in view["markdown"]
    # The slot names stay readable for the next edit: the units line.
    assert "units     funding_now is a decimal per year" in view["markdown"]
    # Control: a label equal to the name (no Column) reads as before.
    plain = [{**u, "label": u["output"]} for u in SERVED_UNITS]
    env = _compose(
        {"source": SCREEN, "name": "Funding now"},
        _Client(SCREEN, views={"revision": 0, "units": plain}),
    )
    assert "label" not in env["view"]["outputs"][0]
    assert "outputs   funding_now (% a year), ret_1d (%)" in env["view"]["markdown"]


def _get(client: _Client) -> dict:
    ctx = ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")
    args = {"strategy_id": SID, "skip_readiness": True}
    return OUTCOMES["keel_strategy_get"].handler(args, ctx).to_envelope()


def test_strategy_get_names_a_screens_outputs_in_their_units_and_labels():
    """keel_strategy_get listed a screen's outputs as bare slot names.

    # SEED: pass `units=None` to build_view in strategy_get._handler — the
    # screen arm reds on the outputs line; the strategy arm stays green."""
    client = _Client(SCREEN, views={"revision": 2, "units": LABELLED_UNITS})
    env = _get(client)
    md = env["view"]["markdown"]
    assert "outputs   Funding now, a year; 1-day return (%)" in md
    assert "units     funding_now is a decimal per year: 0.05 = 5% per year" in md
    assert ("GET", f"/v1/screeners/{SID}/views") in client.calls
    # A keel-api without labels still gives the units (contract 3 absent).
    env = _get(_Client(SCREEN, views={"revision": 0, "units": SERVED_UNITS}))
    assert "outputs   funding_now (% a year), ret_1d (%)" in env["view"]["markdown"]
    # A failed read costs the units, never the read.
    env = _get(_Client(SCREEN, views=RuntimeError("503")))
    assert "outputs   funding_now, ret_1d" in env["view"]["markdown"]
    # Control: a strategy makes no screen read and carries no outputs.
    strat = _Client(STRATEGY, views={"units": SERVED_UNITS})
    env = _get(strat)
    assert "outputs" not in env["view"]
    assert not any(path.endswith("/views") for _, path in strat.calls)
