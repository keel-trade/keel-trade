"""Realistic screen envelopes for the card's saved-view checks (Q-3635).

Not a test module. Builds the six cases the founder's 2026-11-05 claude.ai
test exposed, as `keel.screener.envelope/1` sources that run through the
SDK's real `build_result` (so the card sees exactly what a host would):

* ``top15``     — Table "Top 15" of funding now: signed values, an 8-way tie
                  at +10.9% straddling the cutoff, label "Funding now, a year".
* ``above20``   — the same screen with a rule filter (above +20% a year): 8 rows.
* ``movers``    — the curated "Top movers this week": three columns.
* ``lines``     — a Lines default view with one coin spiking to 2,500% for
                  two bars, every other line in a tight band.
* ``receipt``   — a saved view: no result, one receipt line.
* ``nofilter``  — a Table default with no filter: 25 rows, "+154 more".

And the three the 2026-11-05 polish batch added (Q-3640):

* ``movers_top`` — the curated list as the founder's chat received it: the
                   envelope ships ``latest_top`` (the ten passing rows) and no
                   ``latest``, so every 7-day return the card can see is
                   positive while a 1-day return among them is negative.
* ``movers_top_norange`` — the same without ``completed.range`` (an envelope
                   from before lane A): the sign rule degrades to the unit.
* ``view_set``   — the top15 screen as ``keel_screener_view_set`` returns it
                   once it carries the full envelope (lane B): the same
                   result, a bumped view revision, the save words on ``run``.

Every completed case carries ``completed.range`` (contract field 1: the
ranked output over every coin with a value) and the server's cadence word
(``copy.clock`` "Hourly", contract field 2; the card adds nothing to it).

The numbers are the founder's from the Hyperliquid fundingHistory check
(README of the test): XMR +83.2, MINA −140.6, BLUR −47.1, SAGA +40.5,
2Z +10.9 (% a year). The other coins are made up around them.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "tests" / "fixtures" / "cards" / "screener_card.json"

#: The card's served templates (`copy.card`), as keel-api serves them plus
#: the two Q-3635 asks of lane 3: the tie note and the view revision.
CARD_COPY = {
    "pass_now": "{passing} of {of} pass now",
    "more_in_keel": "+{k} more in Keel",
    "show": "Show",
    "show_all": "All ({n})",
    "expand": "Open the interactive chart",
    "scrub": "Move through the window",
    "latest_note": "The numbers and the table stay at the latest bar.",
    "sorted_within": "Sorted within the {n} shipped.",
    "more_views": "More views in Keel",
    "caption": "Coloured: the top {k} of {A} by {ranked_by}, over {n} of {source_points} bars.",
    "scatter_caption": "{n} of {A} coins; colour follows {ranked_by}.",
    "tie": "{n} coins tie at {value} at the cutoff",
    "view_rev": "view rev {n}",
    # Q-3640, contract field 1: the ranked output's range over every coin
    # with a value, under the rows, when the card shows fewer than that.
    # `{low}` / `{high}` are formatted by the card in the output's unit with
    # the column's sign rule; `{label}` is the column header. The below-zero
    # clause is its own template, joined with " · " when `below_zero` > 0.
    "range": "Across {count} coins: {low} ({low_asset}) to {high} ({high_asset})",
    "range_below": "{n} below zero",
    "units": {"rate_per_hour": "an hour", "rate_per_year": "a year"},
}

AS_OF_MS = 1_793_883_600_000  # 2026-11-05T13:00:00Z
HOUR = 3_600_000
N_BARS = 99
UNIVERSE = 179

#: funding now, as a rate per year (decimal); the ten above the tie, the
#: eight tied at the cutoff, the three deeply negative.
NAMED = [
    ("XMR", 0.832),
    ("SAGA", 0.405),
    ("HYPE", 0.31),
    ("BTC", 0.21),
    ("ETH", 0.29),
    ("SOL", 0.27),
    ("TAO", 0.25),
    ("PEPE", 0.23),
    ("OP", 0.13),
    ("NEAR", 0.12),
    ("2Z", 0.109),
    ("ATOM", 0.109),
    ("WLD", 0.109),
    ("DOGE", 0.109),
    ("AVAX", 0.109),
    ("LINK", 0.109),
    ("SUI", 0.109),
    ("ARB", 0.109),
    ("MINA", -1.406),
    ("BLUR", -0.471),
    ("FARTCOIN", -0.3),
]


def _coins() -> list[tuple[str, float]]:
    rng = random.Random(3635)
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    rows = list(NAMED)
    seen = {name for name, _ in rows}
    while len(rows) < UNIVERSE:
        name = "".join(rng.choice(letters) for _ in range(3))
        if name in seen:
            continue
        seen.add(name)
        rows.append((name, round(rng.uniform(-0.1, 0.1), 4)))
    return rows


def _series(end: float, rng: random.Random, *, spike_at: int | None = None) -> list[float]:
    """99 hourly values that wander a little and end exactly at `end`."""
    values = [end]
    for _ in range(N_BARS - 1):
        values.append(values[-1] + rng.gauss(0, abs(end) * 0.03 + 0.004))
    values.reverse()
    values[-1] = end
    if spike_at is not None:
        values[spike_at] = 25.0
        values[spike_at + 1] = 24.2
    return [round(v, 5) for v in values]


def _source() -> dict:
    return json.loads(BASE.read_text())


def _range(output: str, rows: list[list], ci: int) -> dict:
    """``completed.range`` (contract field 1) over every row with a value."""
    valued = [(r[0], r[ci]) for r in rows if isinstance(r[ci], (int, float))]
    low = min(valued, key=lambda r: r[1])
    high = max(valued, key=lambda r: r[1])
    return {
        "output": output,
        "low": {"asset": low[0], "value": low[1]},
        "high": {"asset": high[0], "value": high[1]},
        "below_zero": sum(1 for _, v in valued if v < 0),
        "count": len(valued),
    }


def _completed(
    src: dict,
    coins: list[tuple[str, float]],
    *,
    outputs,
    labels,
    latest_rows,
    hero_assets,
    spike=None,
):
    c = src["completed"]
    rng = random.Random(1105)
    c["manifest"]["universe"] = {"resolved": [], "count": UNIVERSE, "column_map": {}}
    c["manifest"]["clock"] = {
        "target_timeframe": "1h",
        "bar_offset": None,
        "bar_ms": HOUR,
        "bar_ms_convention": "close",
    }
    c["manifest"]["bars"] = {
        "count": 2160,
        "first_ms": AS_OF_MS - 2159 * HOUR,
        "closed_through_ms": AS_OF_MS,
    }
    c["manifest"]["outputs"] = outputs
    c["manifest"]["disclosures"] = []
    c["disclosures"] = []
    c["as_of_ms"] = AS_OF_MS
    c["window"] = {"start_ms": AS_OF_MS - 2159 * HOUR, "end_ms": AS_OF_MS}
    c["freshness"] = "live"
    c["labels"] = labels
    c["latest"] = {"columns": ["asset", *[o["name"] for o in outputs]], "rows": latest_rows}
    c["latest_top"] = {"columns": c["latest"]["columns"], "rows": latest_rows[:5]}
    by_name = dict(coins)
    c["hero"] = {
        "output": outputs[0]["name"],
        "t0_ms": AS_OF_MS - (N_BARS - 1) * HOUR,
        "dt_ms": HOUR,
        "n": N_BARS,
        "assets": hero_assets,
        "values": [
            _series(by_name[a], rng, spike_at=40 if spike == a else None) for a in hero_assets
        ],
        "anchor_values": {},
        "relative": "none",
        "anchor_ms": AS_OF_MS - (N_BARS - 1) * HOUR,
        "y_scale": "linear",
        "sampling": "last_in_bucket",
        "source_points": 2160,
    }
    c["coverage"] = {
        "with_value": UNIVERSE,
        "of": UNIVERSE,
        "missing_at_cursor": 0,
        "interior_gaps": None,
    }
    c["copy"] = {
        "ranking": "Ranked by Funding now, a year (high → low)",
        "freshness": "Updated 4 minutes ago",
        "coverage": None,
        "gaps": None,
        "rebased": None,
        # contract field 2: the server's cadence word, no " bars"
        "clock": "Hourly",
        "as_of": {"as_of_ms": AS_OF_MS, "words": "as of 5 Nov 13:00 UTC"},
        "description": None,
        "card": copy.deepcopy(CARD_COPY),
    }
    return c


FUNDING_OUTPUT = [{"name": "funding_now", "shape": "panel", "value_type": "PanelFrame"}]
FUNDING_LABEL = [{"label": "Funding now, a year", "unit": "rate_per_year"}]


def funding_source(*, name="Top funding rate coins", revision=3, spike="FARTCOIN") -> dict:
    coins = _coins()
    src = _source()
    src["screener"] = {
        "strategy_id": "str_funding000000000000000000",
        "name": name,
        "version": 2,
        "view_revision": revision,
    }
    src["run"] = {"run_id": "res_funding000000000000000000", "status": "COMPLETED"}
    ranked = sorted(coins, key=lambda c: -c[1])
    latest_rows = [[n, v] for n, v in ranked]
    hero_assets = [n for n, _ in ranked[:17]] + ["MINA", "BLUR", "FARTCOIN"]
    c = _completed(
        src,
        coins,
        outputs=FUNDING_OUTPUT,
        labels=FUNDING_LABEL,
        latest_rows=latest_rows,
        hero_assets=hero_assets,
        spike=spike,
    )
    c["ranked_by"] = "funding_now"
    c["order"] = "desc"
    c["range"] = _range("funding_now", latest_rows, 1)
    return src


def _passing(count: int, assets: list[str], *, tie=None) -> dict:
    return {
        "filter_hash": "f" * 64,
        "now": {"bar_ms": AS_OF_MS, "assets": assets[:5]},
        "previous": {"bar_ms": AS_OF_MS - HOUR, "assets": assets[:5]},
        "entered": [],
        "left": [],
        "counts": {"passing": count, "entered": 0, "left": 0},
        "count": count,
        "total": UNIVERSE,
        "tie": tie,
    }


def top15() -> dict:
    """Table "Top 15" of funding now; the cutoff splits an 8-way tie."""
    src = funding_source()
    c = src["completed"]
    passing = [n for n, _ in NAMED[:15]]
    c["passing"] = _passing(15, passing, tie={"value": 0.109, "count": 8})
    c["headline"] = {"passing": 15, "of": UNIVERSE}
    c["hero"]["passing"] = passing
    c["graphs"] = {
        "default_channels": {
            "view_id": "Top 15",
            "kind": "Table",
            "columns": ["funding_now"],
            "sort": "funding_now",
            "sort_dir": "desc",
        },
        "kinds": ["Table", "Lines"],
        "default": "Table",
        "top_control": False,
    }
    c["copy"]["description"] = "The screen shows the top 15 by Funding now, a year."
    return src


def above20() -> dict:
    """The same screen filtered to funding above +20% a year: 8 coins."""
    src = funding_source(revision=4)
    c = src["completed"]
    passing = [n for n, v in NAMED if v > 0.2]
    assert len(passing) == 8, passing
    c["passing"] = _passing(8, passing)
    c["headline"] = {"passing": 8, "of": UNIVERSE}
    c["hero"]["passing"] = passing
    c["graphs"] = {
        "default_channels": {
            "view_id": "Above 20%",
            "kind": "Table",
            "columns": ["funding_now"],
            "sort": "funding_now",
            "sort_dir": "desc",
        },
        "kinds": ["Table", "Lines"],
        "default": "Table",
        "top_control": False,
    }
    c["copy"]["description"] = (
        "A coin is in the screen when Funding now, a year is above +20.0% a year."
    )
    return src


def nofilter() -> dict:
    """A Table default view with no filter at all: every coin, ranked."""
    src = funding_source(revision=None)
    c = src["completed"]
    c["passing"] = None
    c["headline"] = {"passing": None, "of": UNIVERSE}
    c["hero"].pop("passing", None)
    c["graphs"] = {
        "default_channels": {
            "view_id": "All coins",
            "kind": "Table",
            "columns": ["funding_now"],
            "sort": "funding_now",
            "sort_dir": "desc",
        },
        "kinds": ["Table"],
        "default": "Table",
        "top_control": False,
    }
    src["screener"]["view_revision"] = None
    return src


def lines() -> dict:
    """A Lines default view, no filter; the top coin spikes to 2,500% for two bars."""
    src = funding_source(name="Funding now, every coin", revision=1, spike="XMR")
    c = src["completed"]
    c["passing"] = None
    c["headline"] = {"passing": None, "of": UNIVERSE}
    c["hero"].pop("passing", None)
    c["graphs"] = {
        "default_channels": {"view_id": "History", "kind": "Lines", "plot": "funding_now"},
        "kinds": ["Lines", "Table"],
        "default": "Lines",
        "top_control": False,
    }
    c["copy"]["description"] = (
        "Each line is one coin's Funding now, a year, over the displayed window."
    )
    c["copy"]["coverage"] = "178 of 179 coins have a value."
    return src


def movers() -> dict:
    """The curated "Top movers this week": three columns, top 10 by 7-day return."""
    rng = random.Random(7)
    coins = _coins()
    src = _source()
    src["screener"] = {
        "strategy_id": "str_movers0000000000000000000",
        "name": "Top movers this week",
        "version": 5,
        "view_revision": 2,
    }
    src["run"] = {"run_id": "res_movers0000000000000000000", "status": "COMPLETED"}
    outputs = [
        {"name": "ret_7d", "shape": "panel", "value_type": "PanelFrame"},
        {"name": "rel_vol_rank", "shape": "panel", "value_type": "PanelFrame"},
        {"name": "ret_1d", "shape": "panel", "value_type": "PanelFrame"},
    ]
    labels = [
        {"label": "7-day return", "unit": "percent"},
        {"label": "Relative volume rank", "unit": "rank"},
        {"label": "1-day return", "unit": "percent"},
    ]
    weekly = {n: round(rng.gauss(2, 14), 2) for n, _ in coins}
    weekly.update({"HYPE": 61.4, "SAGA": 48.2, "XMR": 37.9, "2Z": 33.1, "TAO": 29.6})
    rows = sorted(coins, key=lambda c: -weekly[c[0]])
    latest_rows = [
        [n, weekly[n], round(rng.uniform(0.3, 0.99), 2), round(weekly[n] / 7 + rng.gauss(0, 2), 2)]
        for n, _ in rows
    ]
    # the founder's case: a 1-day return inside the top ten is negative while
    # every 7-day return there is positive (Q-3640)
    latest_rows[3][3] = -3.4
    coins_7d = [(n, weekly[n] / 100) for n, _ in coins]
    c = _completed(
        src,
        coins_7d,
        outputs=outputs,
        labels=labels,
        latest_rows=latest_rows,
        hero_assets=[r[0] for r in latest_rows[:20]],
    )
    c["hero"]["output"] = "ret_7d"
    c["hero"]["values"] = [[v * 100 for v in s] for s in c["hero"]["values"]]
    c["ranked_by"] = "ret_7d"
    c["order"] = "desc"
    c["range"] = _range("ret_7d", latest_rows, 1)
    passing = [r[0] for r in latest_rows[:10]]
    c["passing"] = _passing(10, passing)
    c["headline"] = {"passing": 10, "of": UNIVERSE}
    c["hero"]["passing"] = passing
    c["graphs"] = {
        "default_channels": {
            "view_id": "Top 10",
            "kind": "Table",
            "columns": ["ret_7d", "rel_vol_rank", "ret_1d"],
            "sort": "ret_7d",
            "sort_dir": "desc",
        },
        "kinds": ["Table", "Lines", "Scatter"],
        "default": "Table",
        "top_control": False,
    }
    c["copy"]["ranking"] = "Ranked by 7-day return (high → low)"
    c["copy"]["description"] = "The screen shows the top 10 by 7-day return."
    return src


def movers_top() -> dict:
    """The curated list as the founder's chat received it (Q-3640): the
    envelope ships the ten passing rows as ``latest_top`` and no ``latest``
    (the SDK ships ``latest`` only for the ``card`` shape), so the card's
    rows hold no negative 7-day return and one negative 1-day return."""
    src = movers()
    src["shape"] = "fallback"
    c = src["completed"]
    c["latest_top"] = {"columns": c["latest"]["columns"], "rows": c["latest"]["rows"][:10]}
    assert all(r[1] > 0 for r in c["latest_top"]["rows"])
    assert any(r[3] < 0 for r in c["latest_top"]["rows"])
    return src


def movers_top_norange() -> dict:
    """``movers_top`` from a server that serves no ``completed.range``."""
    src = movers_top()
    del src["completed"]["range"]
    return src


def view_set() -> dict:
    """The top15 screen as ``keel_screener_view_set`` returns it once it
    carries the full screen envelope (lane B, 2026-11-05): the same result
    under the new revision, with the save words on ``run``."""
    src = top15()
    src["screener"]["view_revision"] = 4
    src["run"]["status_words"] = "View saved."
    return src


def receipt() -> dict:
    """What a saved view (keel_screener_view_set) hands the card: no result."""
    return {
        "view": {
            "kind": "screener",
            "size": "receipt",
            "name": "Top funding rate coins",
            "version": 2,
            "status": "COMPLETED",
            "object": "str_funding000000000000000000",
        },
        "screener": {
            "strategy_id": "str_funding000000000000000000",
            "name": "Top funding rate coins",
            "version": 2,
            "view_revision": 4,
        },
        "run": {"status": "COMPLETED", "status_words": "View saved."},
        "copy": {"card": {"view_rev": "view rev {n}"}},
        "hero_url": "https://app.usekeel.io/screens/str_funding000000000000000000",
        "actions": [
            {
                "label": "Open in Keel",
                "url": "https://app.usekeel.io/screens/str_funding000000000000000000",
            }
        ],
    }


def card_envelopes() -> dict[str, dict]:
    """Every case as the card receives it (through the SDK's build_result)."""
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._screener_view import build_result

    def card(src):
        env = build_result(src, ToolContext(), size="view").to_envelope()
        # `completed.range` (contract field 1) reaches the card beside the
        # other completed keys (`headline`, `latest_top`, ...). The SDK's
        # completed-key list in `_screener_view.build_result` is the seam
        # that carries it; the fixture places it where the card reads it.
        if "range" in src.get("completed", {}):
            env["range"] = src["completed"]["range"]
        return env

    return {
        "top15": card(top15()),
        "above20": card(above20()),
        "movers": card(movers()),
        "movers_top": card(movers_top()),
        "movers_top_norange": card(movers_top_norange()),
        "lines": card(lines()),
        "receipt": receipt(),
        "nofilter": card(nofilter()),
        "view_set": card(view_set()),
    }
