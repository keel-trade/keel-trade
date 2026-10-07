"""The derived backtest `view` — one owner, four consumers.

The twin of `_strategy_view.build_view()` for the backtest-shaped tools
(`keel_backtest_run`, `keel_backtest_watch`, `keel_backtest_summarize`).
One pure function turns the `GET /v1/backtests/{id}` detail dict those
tools already hold into the block the card draws, the text host relays,
the CLI prints and the tests read
(`RENDER-CADENCE-BUILD.md` §2.1, Q-1688).

Two rules this module lives by, both inherited from the strategy view:

* **Registry nouns and formatted numbers only.** No ids, no engine
  names, no commit hashes, no raw ISO stamps anywhere in the markdown —
  a reader sees "Simple Momentum (ROC 20) v3", never `btr_…` or
  `2026-09-22T00:00:00Z`.
* **`metrics` is the ONE owner of key names and signs on the wire.**
  Values are copied verbatim from the worker's stored metrics (the
  one-computation-owner rule: this module never recomputes a
  measurement); only the KEY and the SIGN are normalised, so every
  consumer reads `view.metrics.trades` and `view.metrics.carry` rather
  than re-deriving `total_trades` / `num_trades` /
  `funding_attribution` for itself. A missing metric is ABSENT — the
  card draws an em dash and keeps its tile, which is honest; a zero
  would not be.

**The curve is NOT here.** Exactly one copy of the compact curve rides
each envelope, at the TOP level, where the card already reads it. The
design said the view carried it "by reference"; JSON has no references,
and two copies is ~14 KB per run.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ._base import election_key


logger = logging.getLogger(__name__)


__all__ = [
    "KIND_BACKTEST",
    "NET_OF",
    "NET_OF_WITHOUT_CARRY",
    "MIXED_ERA_LINE",
    "attach_run_config",
    "build_backtest_view",
    "cost_model_block",
    "effective_backtest_config",
    "era_metrics",
    "funding_was_modelled",
    "net_of_for",
    "config_from_source",
    "exposure_block",
    "normalized_status",
    "notes_block",
    "slice_line",
    "view_metrics",
    "signed_drawdown",
    "trade_era",
    "window_block",
]


KIND_BACKTEST = "backtest"

#: What a Keel backtest is net of. STATED, never computed (PLAN §4.5).
#: "carry" rather than "funding": the listed surface's string rules apply
#: to everything it emits, responses included (see `_nudge`).
NET_OF: tuple[str, ...] = ("fees", "slippage", "carry")

#: The same claim for a run that did NOT account for funding. `net_of`
#: is read as a promise — the card prints "net of fees, slippage and
#: carry" under the tiles — so a run the worker marked
#: `funding_included: false` must not make it (Q-1715).
NET_OF_WITHOUT_CARRY: tuple[str, ...] = ("fees", "slippage")


def net_of_for(metrics: Any) -> list[str]:
    """What THIS run's numbers are net of — the one owner of the claim."""
    return list(NET_OF if funding_was_modelled(metrics) else NET_OF_WITHOUT_CARRY)


#: Terminal statuses, and the two that mean the run produced numbers.
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_SUCCESS = frozenset({"completed"})

# ── Measurement eras (trade-metrics spec 01 §4.1–§4.2, Q-2122) ────────
#
# One stored `total_trades` key has held two quantities. The run's
# `trade_model` stamp says which, and it has had three values:
#
#   A  no stamp               — standard trades (every order that reduced or
#                               closed a position, plus each position still
#                               open at the end); win rate, profit factor and
#                               expectancy over closed trades. No positions.
#   B  "position_round_trip"  — the SAME keys hold POSITION numbers, beside
#                               `rebalance_legs` (= resizes) and `turnover`.
#                               No trade count.
#   C  "reducing_order"       — the standard trade keys plus `positions`,
#                               `position_win_rate`, `resizes`, `turnover`,
#                               `avg_holding_duration`.
#
# Any other stamp is "unknown": no trade-family number is read, and a
# warning is logged. `era_metrics` is the ONE place a stored block's trade
# keys are read by era; `view_metrics`, `summary_metrics`, compare's
# blocks and the strategy evidence all read through it. Nothing is
# recomputed (no Era A positions, no Era B trades): "—" means "this run did
# not record it", never 0. The stamp itself is never rendered.

ERA_LEGACY = "A"
ERA_POSITIONS = "B"
ERA_CURRENT = "C"
ERA_UNKNOWN = "unknown"

_ERA_STAMPS = {"position_round_trip": ERA_POSITIONS, "reducing_order": ERA_CURRENT}

#: Every stored key whose meaning the era decides (the read model's sources).
_TRADE_FAMILY: frozenset[str] = frozenset(
    {
        "total_trades",
        "num_trades",
        "trades",
        "win_rate",
        "win_rate_pct",
        "profit_factor",
        "expectancy",
        "positions",
        "position_win_rate",
        "position_win_rate_pct",
        "resizes",
        "rebalance_legs",
        "turnover",
        "avg_holding_duration",
    }
)

#: Per era: the stored trade-family keys it records, and the name each is
#: read under (the §4.2 read model in stored-key spelling). Era B's keys are
#: RENAMED — its `total_trades` is a position count — and its
#: `rebalance_legs` stays readable beside `resizes` (spec 01 §9 L2).
_ERA_KEYS: dict[str, dict[str, tuple[str, ...]]] = {
    ERA_LEGACY: {
        "total_trades": ("total_trades",),
        "num_trades": ("num_trades",),
        "trades": ("trades",),
        "win_rate": ("win_rate",),
        "win_rate_pct": ("win_rate_pct",),
        "profit_factor": ("profit_factor",),
        "expectancy": ("expectancy",),
    },
    ERA_POSITIONS: {
        "total_trades": ("positions",),
        "win_rate": ("position_win_rate",),
        "win_rate_pct": ("position_win_rate_pct",),
        "profit_factor": ("position_profit_factor",),
        "expectancy": ("position_expectancy",),
        "rebalance_legs": ("resizes", "rebalance_legs"),
        "turnover": ("turnover",),
    },
    ERA_CURRENT: {
        "total_trades": ("total_trades",),
        "num_trades": ("num_trades",),
        "trades": ("trades",),
        "win_rate": ("win_rate",),
        "win_rate_pct": ("win_rate_pct",),
        "profit_factor": ("profit_factor",),
        "expectancy": ("expectancy",),
        "positions": ("positions",),
        "position_win_rate": ("position_win_rate",),
        "position_win_rate_pct": ("position_win_rate_pct",),
        "resizes": ("resizes",),
        "turnover": ("turnover",),
        "avg_holding_duration": ("avg_holding_duration",),
    },
    ERA_UNKNOWN: {},
}


def trade_era(metrics: Any) -> str:
    """`"A"` / `"B"` / `"C"` / `"unknown"` for a run's stored metrics.

    The stamp decides, never a date (spec 01 §6.3). An absent or null stamp
    is Era A; a stamp this build does not know is `"unknown"` and is logged.
    """
    stamp = metrics.get("trade_model") if isinstance(metrics, dict) else None
    if stamp is None:
        return ERA_LEGACY
    era = _ERA_STAMPS.get(stamp) if isinstance(stamp, str) else None
    if era is None:
        logger.warning("unknown trade_model stamp %r: no trade or win-rate number is shown", stamp)
        return ERA_UNKNOWN
    return era


def era_metrics(metrics: Any) -> dict[str, Any]:
    """A copy of a stored metrics block with its trade keys read by era.

    Keys outside the trade family pass through untouched (including the
    stamp — callers choose what they render). Trade-family keys are kept,
    renamed (Era B: `total_trades` → `positions`, `win_rate` →
    `position_win_rate`, `profit_factor` → `position_profit_factor`,
    `expectancy` → `position_expectancy`, `rebalance_legs` → `resizes`) or
    dropped when the era does not record them. An unknown era keeps none.
    """
    if not isinstance(metrics, dict):
        return {}
    keys = _ERA_KEYS[trade_era(metrics)]
    out: dict[str, Any] = {}
    for key, value in metrics.items():
        if key not in _TRADE_FAMILY:
            out[key] = value
            continue
        for name in keys.get(key, ()):
            out[name] = value
    return out


#: What the view says under a run's counts, by era (spec 01 §6.2 — founder
#: sign-off 1, §10.4). Era C and an unknown era carry none.
COUNT_BASIS: dict[str, str] = {
    ERA_LEGACY: "Positions are not recorded for this run.",
    ERA_POSITIONS: "This run counted positions, not trades; its trade count is not recorded.",
}

#: Added once to a comparison whose runs come from different eras (§6.2).
MIXED_ERA_LINE = "Runs from different measurement eras: '—' marks a count that run did not record."


#: The worker writes several spellings for one measurement. This map is
#: the ONLY place they are reconciled — `view.metrics` keys are the wire
#: contract, and `summary_metrics` / `metrics_raw` keep the worker's own
#: spellings for the model and for older callers — except the drawdown,
#: which `summary_metrics` states once, signed, via `signed_drawdown`
#: (Q-1805); only `metrics_raw` is verbatim. The trade keys are read
#: through `era_metrics` first, so a key here only ever names one quantity.
_METRIC_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sharpe", ("sharpe", "sharpe_ratio")),
    ("total_return_pct", ("total_return_pct", "total_return")),
    ("max_drawdown_pct", ("max_drawdown_pct", "max_drawdown")),
    # The standard trade count (spec 01 §3): every order that reduced or
    # closed a position, plus each position still open at the end — the
    # number the app's Trades tile quotes. It was keyed and labelled
    # "fills" here, which it is not (Q-1746). The view's labels say "Trades"
    # (founder ruling 2026-09-24, Q-1906); "trades" stays a banned token in
    # LISTED tool copy (descriptions/schemas), which therefore names no
    # count word at all. The runtime key is `trades` (2026-09-25): it is
    # result data, not listed copy.
    ("trades", ("total_trades", "num_trades", "trades")),
    ("win_rate_pct", ("win_rate_pct", "win_rate")),
    ("positions", ("positions",)),
    ("position_win_rate_pct", ("position_win_rate_pct", "position_win_rate")),
    ("resizes", ("resizes",)),
    ("turnover", ("turnover",)),
    ("avg_holding", ("avg_holding_duration",)),
    ("sortino", ("sortino", "sortino_ratio")),
    ("calmar", ("calmar", "calmar_ratio")),
    ("profit_factor", ("profit_factor",)),
    ("position_profit_factor", ("position_profit_factor",)),
    ("fees_paid", ("total_fees_paid", "fees_paid")),
    ("carry", ("funding_attribution", "carry")),
)

#: The four headline tiles — the Keel app's first four stat cards, in its
#: order and with its labels (founder ruling 2026-09-22, Q-1746). The
#: envelope OWNS the labels (`view.tiles`), so the card and the markdown
#: render one list instead of each keeping its own.
HEADLINE_TILES: tuple[tuple[str, str], ...] = (
    ("total_return_pct", "Return"),
    ("max_drawdown_pct", "Max drawdown"),
    ("sharpe", "Sharpe"),
    ("win_rate_pct", "Win rate"),
)

#: An Era B run's fourth tile: its stored win rate is over POSITIONS, so
#: it is labelled so (spec 01 §6.2) — never "Win rate".
POSITIONS_HEADLINE_TILES: tuple[tuple[str, str], ...] = HEADLINE_TILES[:3] + (
    ("position_win_rate_pct", "Position win rate"),
)

#: Everything else, behind "+N more" (`view.more_tiles`), labelled as the
#: app's metrics table labels them (spec 01 §6.2). "Trades" is the app's
#: word for the standard count (founder ruling 2026-09-24, Q-1906: never
#: "round trips"); "Positions" counts entries through to exits. A run
#: lists only the tiles its era recorded (`era_metrics`), so an Era A run
#: shows no Positions and an Era B run no Trades.
MORE_TILES: tuple[tuple[str, str], ...] = (
    ("trades", "Trades"),
    ("positions", "Positions"),
    ("position_win_rate_pct", "Position win rate"),
    ("resizes", "Resizes"),
    ("turnover", "Turnover (× capital)"),
    ("avg_holding", "Avg holding time"),
    ("sortino", "Sortino"),
    ("calmar", "Calmar"),
    ("profit_factor", "Profit factor"),
    ("position_profit_factor", "Profit factor (per position)"),
    ("fees_paid", "Fees paid"),
)


def count_label(key: str = "trades") -> str:
    """A count's word in running text (`1,761 trades`, `53 positions`) —
    its `MORE_TILES` label, lower-cased, so every surface says one word
    (Q-1906)."""
    return dict(MORE_TILES)[key].lower()


#: The word a card draws when the full label does not fit its tile (Q-1799)
#: — "MAX DRAWDOWN" cut to "MAX DRAWDO…" at 481 px. Server-owned like the
#: label itself; a label that always fits has no short form.
SHORT_LABELS: dict[str, str] = {
    "max_drawdown_pct": "Max DD",
    "turnover": "Turnover",
}

#: The numbers the evidence layout can draw, in reading order.
EVIDENCE_METRIC_KEYS: tuple[str, ...] = tuple(k for k, _ in HEADLINE_TILES) + tuple(
    k for k, _ in MORE_TILES
)

#: One minus, everywhere (U+2212) — the card system's number rule.
MINUS = "−"

#: An en dash separates the two ends of a window.
_RANGE_DASH = "–"

#: A failed run's message is a receipt line, not a stack trace.
MAX_ERROR_CHARS = 120

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


# ── Values ────────────────────────────────────────────────────────────


def _number(value: Any) -> float | None:
    """The value as a number, or None — booleans are never numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fixed(value: Any, digits: int, suffix: str = "") -> str:
    """`26.0%` · `0.60` — a number at a fixed precision, unsigned, one minus.

    The ONE unsigned renderer. `_plain` stood beside it doing the same
    job with `.rstrip("0").rstrip(".")` on the end, which is how a Sharpe
    of 0.60 reached staging as `0.6` beside `0.82` — a column a reader
    cannot compare down, and a divergence from the card, whose
    `fmt.ratio` has always been a bare `toFixed(dp)` (Q-1710).
    """
    number = _number(value)
    if number is None:
        return "—"
    text = f"{number:.{digits}f}"
    if text.startswith("-"):
        text = MINUS + text[1:]
    return text + suffix


def _signed(value: Any, digits: int, suffix: str = "") -> str:
    """`+60.5%` / `−43.4%` — the sign is a glyph, never only a colour."""
    number = _number(value)
    if number is None:
        return "—"
    text = f"{number:.{digits}f}"
    if text.startswith("-"):
        return MINUS + text[1:] + suffix
    return "+" + text + suffix


def _count(value: Any) -> str:
    """`1,761` — a count with its thousands separator."""
    number = _number(value)
    if number is None:
        return "—"
    return f"{int(round(number)):,}"


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def window_block(start: Any, end: Any, served: Any = None) -> dict[str, Any]:
    """`{"start": "2024-08-15", "end": "2026-09-22"}` — the ONE owner.

    `view.window` is a DATE pair (BUILD §2.1, §2.2). keel-api answers
    `start_date` / `end_date` as `"2024-07-27 00:00:00+00:00"` — a
    midnight datetime, space-separated, which is not even ISO-8601, so
    `date.fromisoformat` raises on it and `Date.parse` is left to a
    renderer's goodwill (Q-1709). Normalised here so the card, the
    markdown and the receipt cannot disagree about the shape.

    A value that is not a date at all becomes `None`, which is what every
    consumer already renders for a missing window (`_day` draws an em
    dash, `_window_span` returns nothing) — a wire shape the contract
    forbids is not passed through for a renderer to trip over.
    """
    parsed_start, parsed_end = _parse_date(start), _parse_date(end)
    block: dict[str, Any] = {
        "start": parsed_start.isoformat() if parsed_start else None,
        "end": parsed_end.isoformat() if parsed_end else None,
    }
    # The additive keys (spec 03 §2.5, R-3): `end` KEEPS its exclusive
    # meaning; `last_bar` is the last covered day — the served
    # `window.ran.last_bar` when the read carries it, else the exclusive end
    # minus one day (windows are midnight-pinned; the server's own
    # pre-completion rule, so an older keel-api prints what a new one would).
    ran = served.get("ran") if isinstance(served, dict) else None
    ran = ran if isinstance(ran, dict) else {}
    last_bar = _parse_date(ran.get("last_bar"))
    if last_bar is None and parsed_end is not None:
        last_bar = parsed_end - timedelta(days=1)
    if last_bar is not None and parsed_start is not None and last_bar >= parsed_start:
        block["last_bar"] = last_bar.isoformat()
        days = ran.get("days")
        block["days"] = (
            days
            if isinstance(days, int) and not isinstance(days, bool)
            else (last_bar - parsed_start).days + 1
        )
    if isinstance(served, dict):
        warmup = served.get("warmup_bars")
        if isinstance(warmup, int) and not isinstance(warmup, bool):
            block["warmup_bars"] = warmup
        chart_start = _parse_date(served.get("chart_start"))
        if chart_start is not None:
            block["chart_start"] = chart_start.isoformat()
    return block


def _day(value: Any) -> str:
    """`Aug 15, 2024` — a formatted date, never a raw stamp."""
    parsed = _parse_date(value)
    if parsed is None:
        return "—"
    return f"{_MONTHS[parsed.month - 1]} {parsed.day}, {parsed.year}"


def window_last_day(window: Any) -> date | None:
    """The day a window's prose ENDS on — the ONE owner of window display
    dates (spec 03 §2.5): the last covered day, never the exclusive `end`.

    The served `last_bar` when the block carries it, else the exclusive
    `end` minus one day (windows are midnight-pinned). Every sentence that
    names where a run's window ends — the card's range, compare's window
    notes (Q-1903: "v1 ends Sep 24" beside a card reading "Sep 23") —
    reads its date here, so no surface can print the exclusive bound.
    """
    if not isinstance(window, dict):
        return None
    last = _parse_date(window.get("last_bar"))
    if last is not None:
        return last
    end = _parse_date(window.get("end"))
    return end - timedelta(days=1) if end is not None else None


def _window_line(window: dict) -> str | None:
    """`Jul 27, 2024 – Sep 22, 2026` — the ONE display rule (spec 03 §2.5):
    a range ends on its last covered day (`window_last_day`)."""
    start = window.get("start")
    last = window_last_day(window)
    if not start or last is None:
        return None
    return f"{_day(start)} {_RANGE_DASH} {_day(last.isoformat())}"


def _window_span(window: dict) -> str | None:
    """`2.1 years` · `7 months` · `12 days` — how long the run covers."""
    start, end = _parse_date(window.get("start")), _parse_date(window.get("end"))
    if start is None or end is None:
        return None
    days = window.get("days")
    if not isinstance(days, int) or isinstance(days, bool):
        days = (end - start).days
    if days < 0:
        return None
    if days >= 365:
        return f"{days / 365.25:.1f} years"
    if days >= 60:
        return f"{round(days / 30.44)} months"
    return f"{days} day" + ("" if days == 1 else "s")


# ── Windows across a set of runs (Q-1802) ─────────────────────────────
#
# The ONE owner of what a comparison says when its runs do not cover the
# same dates. It used to be a pairwise tuple test that printed keel-api's
# raw stamps in A/B wording — "Periods differ (A: 2024-07-27 00:00:00+00:00
# ..2026-09-23 00:00:00+00:00, B: …) — absolute metrics are not directly
# comparable." — for two runs one UTC midnight apart: the default window
# ends at today UTC (half-open), so a run submitted after 00:00Z has one
# more complete day than its sibling. A one-day tail on a two-year window
# is a note; a window that starts five months later is a warning.

#: A difference is a NOTE — not a warning — only when BOTH hold: every run
#: shares at least this fraction of its own span with the common window,
#: and no edge (start or end) moves by more than `WINDOW_NOTE_MAX_EDGE_DAYS`.
#: The second bound keeps a short window honest: 5 days off a 30-day run is
#: 83% overlap and fails the first; 7 days off a 2-year run is 99% and still
#: a note, because the metrics are dominated by the shared 99%.
WINDOW_NOTE_MIN_OVERLAP = 0.98
WINDOW_NOTE_MAX_EDGE_DAYS = 7


def _names(labels: list[str]) -> str:
    """`v5` · `v5 and v6` · `v5, v6 and v7` — runs named in prose."""
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def _gap_words(days: int) -> str:
    """How far one edge moved: `1 day`, `12 days`, `5 months`, `1.2 years`."""
    if days >= 365:
        return f"{days / 365.25:.1f} years"
    if days >= 60:
        return f"{round(days / 30.44)} months"
    return f"{days} day" + ("" if days == 1 else "s")


def _pct_share(share: float) -> str:
    """`99.9%` — one decimal, capped at 99.9 so a window that is not
    identical never reads 100.0%."""
    return f"{min(round(share * 100, 1), 99.9):.1f}%"


#: How to put variants on one window (Q-2422, founder-approved 2026-10-04):
#: the ONE approach sentence, in `keel_backtest_run`'s description and in
#: `compare_windows`' different-windows warning. Facts only: windows default
#: by clock (keel-api `default_window_days`), a run's `same window:` pair
#: re-runs its window, and a start before the data is moved and named
#: (keel-api `resolve_effective_window`). `{pair}` is "" or " (v1:
#: start_date=… end_date=…)" — compare names the first run's exact pair.
WINDOW_APPROACH_TEMPLATE = (
    "Windows default by clock (shorter on fine clocks). To compare variants — another "
    "clock, parameters or universe — run the first, then pass its start_date/end_date "
    "to the others{pair}; a finer clock may take longer, and a start before its data "
    "is moved and named."
)
WINDOW_APPROACH_TEXT = WINDOW_APPROACH_TEMPLATE.format(pair="")


def compare_windows(windows: list[dict], labels: list[str]) -> dict | None:
    """What a set of run windows says about the comparison, or None.

    `windows` are `window_block` pairs, `labels` the run labels, both in
    the caller's order (the baseline first). None when every window is
    the same. Otherwise::

        {"severity": "note" | "warning",
         "sentence": "Windows differ by 1 day at the end — v1 ends …",
         "overlap": {"start": "…", "end": "…"} | None,   # the common window
         "overlap_pct": 99.87}                            # min share, 0–100

    The sentence names which runs cover what, in the cards' date format
    (`_day`), never a raw stamp and never A/B. It does NOT recompute any
    metric on the common window: every metric is copied from its run
    (one computation owner), so the sentence says which window each run's
    numbers cover instead.
    """
    parsed = [(_parse_date(w.get("start")), _parse_date(w.get("end"))) for w in windows]
    if len(set(parsed)) <= 1:
        return None
    if any(s is None or e is None or e <= s for s, e in parsed):
        missing = [label for label, (s, e) in zip(labels, parsed) if s is None or e is None]
        who = _names(missing) if missing else "one or more runs"
        return {
            "severity": "warning",
            "sentence": (
                f"Windows could not be compared — {who} carries no usable window; "
                "check each run's dates before comparing return, drawdown or fees."
            ),
            "overlap": None,
            "overlap_pct": None,
        }

    common_start = max(s for s, _ in parsed)
    common_end = min(e for _, e in parsed)
    overlap_days = max(0, (common_end - common_start).days)
    share = min(overlap_days / (e - s).days for s, e in parsed)
    start_gap = (max(s for s, _ in parsed) - min(s for s, _ in parsed)).days
    end_gap = (max(e for _, e in parsed) - min(e for _, e in parsed)).days

    # Runs grouped by window, in first-seen order (the baseline's group first).
    # Each group keeps its first run's window block: an end is printed
    # through `window_last_day` — the last covered day, as on the cards.
    groups: list[tuple[tuple[date, date], dict, list[str]]] = []
    for label, key, block in zip(labels, parsed, windows):
        for seen, _, names in groups:
            if seen == key:
                names.append(label)
                break
        else:
            groups.append((key, block, [label]))

    def describe(key: tuple[date, date], block: dict, names: list[str]) -> str:
        s = "s" if len(names) == 1 else ""
        who = _names(names)
        shown = {"start": key[0].isoformat(), "end": key[1].isoformat()}
        if _parse_date(block.get("last_bar")) is not None:
            shown["last_bar"] = block["last_bar"]
        if start_gap and end_gap:
            return f"{who} cover{s} {_window_line(shown)}"
        if end_gap:
            last = window_last_day(shown)
            return f"{who} end{s} {_day(last.isoformat() if last else None)}"
        return f"{who} start{s} {_day(key[0].isoformat())}"

    runs = "; ".join(describe(key, block, names) for key, block, names in groups)
    # The approach, naming the FIRST run's exact pair (Q-2422): its served
    # start and exclusive end, copied — the run tool's own input semantics.
    first_start, first_end = parsed[0]
    approach = WINDOW_APPROACH_TEMPLATE.format(
        pair=(
            f" ({labels[0]}: start_date={first_start.isoformat()} end_date={first_end.isoformat()})"
        )
    )
    if overlap_days == 0:
        return {
            "severity": "warning",
            "sentence": (
                f"Windows do not overlap — {runs}. Every metric describes a different "
                f"period, so these runs are not comparable on it. {approach}"
            ),
            "overlap": None,
            "overlap_pct": 0.0,
        }

    edges = [
        f"{_gap_words(gap)} at the {edge}"
        for gap, edge in ((start_gap, "start"), (end_gap, "end"))
        if gap
    ]
    pct = _pct_share(share)
    note = share >= WINDOW_NOTE_MIN_OVERLAP and max(start_gap, end_gap) <= WINDOW_NOTE_MAX_EDGE_DAYS
    if note:
        tail = f"{pct} overlap, too small to change the comparison."
    else:
        tail = f"{pct} overlap: return, drawdown and fee totals cover different periods. {approach}"
    return {
        "severity": "note" if note else "warning",
        "sentence": f"Windows differ by {' and '.join(edges)} — {runs}. {tail}",
        "overlap": {"start": common_start.isoformat(), "end": common_end.isoformat()},
        "overlap_pct": round(share * 100, 2),
    }


# ── The detail dict, projected ────────────────────────────────────────


def normalized_status(detail: Any) -> str:
    """The five statuses the view speaks, from whatever the API said.

    The API answers `COMPLETED` / `succeeded` / `RUNNING` in a mix of
    cases and two spellings for success; every consumer of the view
    compares against ONE vocabulary.
    """
    raw = ""
    if isinstance(detail, dict):
        raw = str(detail.get("status") or "").lower()
    if raw == "succeeded":
        return "completed"
    if raw in ("completed", "failed", "cancelled", "canceled", "running", "queued"):
        return "cancelled" if raw == "canceled" else raw
    return raw or "unknown"


def funding_was_modelled(metrics: Any) -> bool:
    """Whether this run accounted for funding — the ONE reading of the flag.

    `False` ONLY on the worker's explicit `funding_included: false`.
    An ABSENT flag leaves the standing claim: it predates the key, and
    treating silence as "not modelled" would withdraw a true statement
    from every older envelope (silence is not evidence, in either
    direction — see `net_of` below for the half that matters).
    """
    if not isinstance(metrics, dict):
        return True
    return metrics.get("funding_included") is not False


def _carry(metrics: dict) -> Any:
    """The run's carry P&L in account currency, signed: negative = paid.

    The card draws this tile with `F.money`, so `carry` is MONEY — and
    the only money-denominated funding figure the worker writes is
    `metrics.funding.cumulative_funding`, which is a COST magnitude
    (positive when the strategy paid funding away). Its sign is proven
    against the stored library runs, where
    `funding_return_pct == -cumulative_funding / 100` holds on every
    one, in both directions:

        momentum-hyperliquid   cumulative 1456.52  → return_pct −14.57
        funding-carry          cumulative −4777.88 → return_pct +47.78

    so the signed contribution is the negation. Until Q-1707's sibling
    Q-1715 this key's only source was `funding_attribution`, which
    NOTHING in the platform has ever written (the 2026-08-19 cohort
    analysis found the same thing), so the Carry tile had never drawn
    from a real envelope while the receipt line beneath it claimed the
    numbers were net of carry.

    NOT `funding.funding_boost_pct` (−23.71 here) and NOT
    `funding.funding_return_pct` (−17.73): those are the app's "Funding
    Carry" in points of RETURN and funding as a percent of INITIAL
    capital. Three different quantities; this is the one whose unit
    matches the tile.
    """
    if not funding_was_modelled(metrics):
        return None
    for source in ("funding_attribution", "carry"):
        if metrics.get(source) is not None:
            return metrics[source]
    funding = metrics.get("funding")
    if not isinstance(funding, dict):
        return None
    cumulative = _number(funding.get("cumulative_funding"))
    return None if cumulative is None else -cumulative


def view_metrics(metrics: Any) -> dict[str, Any]:
    """`view.metrics` — the wire's key names, the worker's values.

    Copied verbatim except for two SIGNS, both of which the worker
    writes in a convention the view states once so no consumer has to
    guess: max drawdown (`max_drawdown: 43.4` and `max_drawdown_pct:
    -43.4` are the same measurement, always rendered negative-or-zero)
    and carry (see `_carry`). The trade keys are read by era first
    (`era_metrics`): an Era B run's stored count arrives as `positions`.
    """
    if not isinstance(metrics, dict):
        return {}
    read = era_metrics(metrics)
    out: dict[str, Any] = {}
    for key, sources in _METRIC_SOURCES:
        for source in sources:
            if source in read and read[source] is not None:
                out[key] = read[source]
                break
    drawdown = _number(out.get("max_drawdown_pct"))
    if drawdown is not None and drawdown > 0:
        out["max_drawdown_pct"] = -drawdown
    carry = _carry(metrics)
    if carry is not None:
        out["carry"] = carry
    else:
        out.pop("carry", None)
    # The headline Sharpe is the full-window `sharpe_ratio` (Q-1991), by the
    # same rule as the app's tearsheet and cards and the chat payload, so no
    # two surfaces quote different Sharpes for one run (Q-1746). The
    # warm-up-trimmed `sharpe_ratio_active` is not headlined: its trim count
    # cannot tell warm-up from a decision to stay flat.
    return out


def exposure_block(metrics: Any) -> dict | None:
    """How much of the window held positions, from the run's own metrics.

    The worker stamps ``metrics.exposure`` (Q-1993: ``bars_held``,
    ``n_bars``, ``first_position_at``, ``last_position_at``) on every new
    run; this copies it verbatim and adds ``line``, the SAME sentence the
    app's run page shows (``keel-app ExposureLine.tsx``):
    ``Held positions on 6 of 25 bars · first position Sep 20, 2026``.
    ``None`` when the run carries no such fact (runs before the stamp are
    not backfilled) — absent, never a guess. No warm-up reading, threshold
    or judgement: the numbers are the reader's.

    ``gross_mean`` / ``gross_max`` (Q-2502) are the MEASURED gross leverage
    of the simulated book on its held bars, ``Σ|position × price| / equity``
    — how big the book actually ran, whatever sized or capped it — and add
    ``· gross on held bars avg 0.30×, max 1.00×`` to the line. Runs stamped
    before them carry neither, and the line then says nothing about size.
    """
    raw = metrics.get("exposure") if isinstance(metrics, dict) else None
    if not isinstance(raw, dict):
        return None
    held, n_bars = raw.get("bars_held"), raw.get("n_bars")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (held, n_bars)):
        return None
    if n_bars <= 0:
        return None
    first, last = raw.get("first_position_at"), raw.get("last_position_at")
    line = f"Held positions on {held:,} of {n_bars:,} bars"
    gross_mean, gross_max = _number(raw.get("gross_mean")), _number(raw.get("gross_max"))
    has_gross = gross_mean is not None and gross_max is not None
    if has_gross:
        line += f" · gross on held bars avg {gross_mean:.2f}×, max {gross_max:.2f}×"
    if isinstance(first, str) and _parse_date(first) is not None:
        # A bar is a UTC instant on the run's clock: the date is its UTC day,
        # as the window beside it (the app formats it the same way).
        line += f" · first position {_day(first)}"
    out = {
        "bars_held": held,
        "n_bars": n_bars,
        "first_position_at": first if isinstance(first, str) else None,
        "last_position_at": last if isinstance(last, str) else None,
    }
    if has_gross:
        out["gross_mean"], out["gross_max"] = gross_mean, gross_max
    out["line"] = line
    return out


def slice_line(block: Any) -> str | None:
    """The `slice:` line: keel-api's summary sentence plus what a slice is.

    Both halves are the server's (`utils/backtest_slice`, the one owner);
    this only joins them.
    """
    if not isinstance(block, dict):
        return None
    summary = block.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    basis = block.get("basis")
    return f"{summary.strip()} {basis.strip()}" if isinstance(basis, str) else summary.strip()


def notes_block(metrics: Any) -> dict | None:
    """``{"non_result": {...}, "assets": [{"code", "message"}, ...]}`` from
    the worker's durable metrics — present only when the run has
    something to say.

    The worker writes both conditionally (`metrics.py`: `non_result`
    only when `derive_non_result` returns one, `warnings` only when the
    executor collected any), so a clean universe over a clean window
    carries NEITHER, and its absence from an envelope is not evidence
    that the path is dead. What WAS dead until Q-1716: this block was
    built only by `keel_backtest_summarize`, while `keel_backtest_run`
    and `keel_backtest_watch` render the same card — which reads
    `env.notes` — so a run with delisted symbols said nothing about them
    on two of its three surfaces.

    ``assets`` keeps the worker's order and shape (``code`` +
    ``message`` per note, e.g. ``SYMBOL_DELISTED``,
    ``SYMBOL_UNAVAILABLE``, ``SYMBOL_ABSENT``, ``SYMBOL_DATA_ENDED``,
    ``DATA_COPY_LAG``). Copied verbatim; nothing is re-derived here.
    """
    if not isinstance(metrics, dict):
        return None
    out: dict = {}
    non_result = metrics.get("non_result")
    if isinstance(non_result, dict) and non_result.get("message"):
        out["non_result"] = non_result
    warnings = metrics.get("warnings")
    if isinstance(warnings, list):
        assets = [w for w in warnings if isinstance(w, dict) and w.get("code") and w.get("message")]
        if assets:
            out["assets"] = assets
    return out or None


def signed_drawdown(metrics: dict[str, Any]) -> dict[str, Any]:
    """A copy of a metrics dict with the max drawdown stated ONCE: ``max_drawdown_pct`` ≤ 0.

    The model-visible metric blocks (`summary_metrics`, compare's
    `performance` rows) use the convention `view.metrics`, the card and
    the text block use: a percent, negative or zero, under a `_pct` name.
    The worker writes `max_drawdown` as an unsigned magnitude (`60.88`), so
    a model reading `summary_metrics` beside a card showing `−60.9%` saw
    two signs for one number (Q-1805). The worker's own spelling survives
    only in `metrics_raw*`, which are its sealed metrics verbatim.
    """
    out = {k: v for k, v in metrics.items() if k != "max_drawdown"}
    value = _number(metrics.get("max_drawdown_pct"))
    if value is None:
        value = _number(metrics.get("max_drawdown"))
    if value is not None:
        out["max_drawdown_pct"] = -abs(value)
    return out


#: How a card formats a metric whose KEY it does not know (2026-09-25): the
#: served card HTML is listed copy under the policy scan, so it names no
#: count key; the envelope says the kind instead.
FORMAT_KINDS: dict[str, str] = {"trades": "count", "positions": "count", "resizes": "count"}


def _tile(key: str, label: str, metrics: dict) -> dict[str, Any]:
    """One tile: the key, the app's label, the raw value, the display."""
    tile = {
        "key": key,
        "label": label,
        "value": metrics.get(key),
        "display": _tile_display(key, metrics),
    }
    if key in FORMAT_KINDS:
        tile["format"] = FORMAT_KINDS[key]
    if key in SHORT_LABELS:
        tile["short_label"] = SHORT_LABELS[key]
    return tile


def _tile_display(key: str, metrics: dict) -> str:
    """The formatted value — the ONE place each tile's number is rendered."""
    if key == "total_return_pct":
        return _signed(metrics.get(key), 1, "%")
    if key in ("max_drawdown_pct", "win_rate_pct", "position_win_rate_pct"):
        return _fixed(metrics.get(key), 1, "%")
    if key in ("trades", "positions", "resizes"):
        return _count(metrics.get(key))
    if key == "avg_holding":
        return _duration(metrics.get(key))
    if key == "turnover":
        return f"{_fixed(metrics.get(key), 1)}x" if key in metrics else "—"
    if key == "fees_paid":
        return _money(metrics.get(key))
    return _fixed(metrics.get(key), 2)


#: Count keys: whole numbers wherever they are shown.
_COUNT_KEYS = frozenset(
    {"round_trips", "total_trades", "trades", "num_trades", "positions", "resizes"}
)


def _duration(value: Any) -> str:
    """`12.2 days` · `5.0 hours` — a stored duration (`"12 days 04:00:00"`,
    the form `max_drawdown_duration` uses), read for a person. A value in
    any other form is shown as stored, and a missing one as an em dash."""
    if not isinstance(value, str) or not value.strip():
        return "—"
    text = value.strip()
    days = 0
    clock = text
    if " day" in text:
        head, _, clock = text.partition(" day")
        clock = clock.lstrip("s").strip()
        try:
            days = int(head)
        except ValueError:
            return text
    parts = clock.split(":") if clock else ["0", "0", "0"]
    try:
        hours, minutes, seconds = (float(p) for p in parts)
    except ValueError:
        return text
    total_hours = days * 24 + hours + minutes / 60 + seconds / 3600
    if total_hours >= 24:
        return f"{total_hours / 24:.1f} days"
    return f"{total_hours:.1f} hours"


def display_number(key: str, value: Any) -> Any:
    """A metric as a NUMBER at the precision `_tile_display` renders it —
    for model-visible JSON that carries numbers, not strings (Q-1877):
    percents and turnover 1 dp, counts whole, fees whole dollars, ratios
    2 dp. Anything that is not a number passes through untouched."""
    number = _number(value)
    if number is None:
        return value
    if key in _COUNT_KEYS:
        return int(round(number))
    if key.endswith("_pct") or key == "turnover":
        return round(number, 1)
    if key in ("fees_paid", "total_fees_paid"):
        return round(number)
    return round(number, 2)


def view_tiles(metrics: dict, era: str = ERA_LEGACY) -> tuple[list[dict], list[dict]]:
    """`(tiles, more_tiles)` — the headline four and the rest, in order.

    A missing headline metric keeps its tile (the card draws an em dash —
    honest); a missing "more" metric is simply not listed, so "+N more"
    counts what the run actually carries. An Era B run's fourth tile is
    its Position win rate (spec 01 §6.2).
    """
    headline = POSITIONS_HEADLINE_TILES if era == ERA_POSITIONS else HEADLINE_TILES
    shown = {key for key, _ in headline}
    tiles = [_tile(key, label, metrics) for key, label in headline]
    more = [
        _tile(key, label, metrics)
        for key, label in MORE_TILES
        if key in metrics and key not in shown
    ]
    return tiles, more


def _error_line(detail: dict) -> str | None:
    """The first line of the run's error, capped — a receipt, not a trace."""
    message = detail.get("error_message")
    if not isinstance(message, str) or not message.strip():
        return None
    first = message.strip().splitlines()[0].strip()
    if len(first) > MAX_ERROR_CHARS:
        first = first[: MAX_ERROR_CHARS - 1].rstrip() + "…"
    return first or None


# ── Markdown ──────────────────────────────────────────────────────────


def _title(view: dict) -> str:
    name = view.get("name") or "Untitled"
    version = view.get("version")
    return f"**{name}**" + (f" v{version}" if version is not None else "")


def _receipt_markdown(view: dict) -> str:
    """ONE line, plus the link line. A guard counts them."""
    status = view["status"]
    window = _window_line(view.get("window") or {})
    bits = [f"Backtest {_title(view)}"]

    if status in _SUCCESS:
        # The headline tiles, in the app's order and with its labels.
        for tile in view.get("tiles") or []:
            bits.append(f"{tile['label']} {tile['display']}")
        if window:
            bits.append(window)
    elif status == "failed":
        bits.append("failed")
        error = view.get("error")
        if error:
            bits.append(error)
    elif status == "cancelled":
        bits.append("cancelled")
    else:
        # A snapshot, and it says so: no second result ever updates this
        # line, so "still running" without "when checked" would read as a
        # live status the transcript cannot keep.
        bits.append("still running when checked")
        if window:
            bits.append(window)

    lines = [" · ".join(bits)]
    if view.get("url_line"):
        lines.append(str(view["url_line"]))
    return "\n".join(lines) + "\n"


def _evidence_markdown(view: dict) -> str:
    window = view.get("window") or {}

    head = [f"**{view.get('name') or 'Untitled'}**"]
    if view.get("version") is not None:
        head.append(f"v{view['version']}")
    head.append(str(view["status"]))
    window_line = _window_line(window)
    if window_line:
        head.append(window_line)
    span = _window_span(window)
    if span:
        head.append(span)
    lines = [" · ".join(head)]

    # Line 2: the four headline tiles (the app's first four); line 3: the
    # rest, as the app's metrics table labels them (Q-1746).
    for tiles in (view.get("tiles") or [], view.get("more_tiles") or []):
        if tiles:
            lines.append(" · ".join(f"{t['label']} {t['display']}" for t in tiles))
    # What the counts above are, when the run's era leaves one unrecorded
    # (spec 01 §6.2).
    if view.get("count_basis"):
        lines.append(str(view["count_basis"]))
    error = view.get("error")
    if error:
        lines.append(error)
    # The PROSE reads the view's own `net_of`, never the constant: the
    # sentence under the numbers and the field a card reads are one
    # claim, and a run that did not model funding must not make it in
    # either place (Q-1715).
    net_of = view.get("net_of") or list(NET_OF)
    lines.append("net of " + ", ".join(net_of[:-1]) + f" and {net_of[-1]}")
    if view.get("url_line"):
        lines.append(str(view["url_line"]))
    return "\n".join(lines) + "\n"


# ── The view ──────────────────────────────────────────────────────────


def build_backtest_view(
    detail: Any,
    *,
    size: str = "receipt",
    url: str | None = None,
    curve: Any = None,
    reference: Any = None,
) -> dict | None:
    """The `view` block for one backtest run, or None without a detail.

    `detail` is the `GET /v1/backtests/{id}` dict every backtest tool
    already holds. `size` is `receipt` (a step in a set) or `evidence`
    (the run being discussed) — `present` resolves it (BUILD §2.4).

    `curve` is accepted and DELIBERATELY not stored: the compact curve
    rides the envelope once, at the top level. The parameter exists so a
    caller cannot conclude the view "forgot" it — see the module
    docstring.
    """
    del curve  # one copy per envelope, at the top level (§2.1)
    if not isinstance(detail, dict) or not detail:
        return None

    status = normalized_status(detail)
    window = window_block(detail.get("start_date"), detail.get("end_date"), detail.get("window"))
    view: dict[str, Any] = {
        "kind": KIND_BACKTEST,
        "size": "evidence" if size == "evidence" else "receipt",
        "name": detail.get("strategy_name") or detail.get("name"),
        "version": detail.get("sequence_number"),
        "status": status,
        "window": window,
        "metrics": view_metrics(detail.get("metrics")),
        "net_of": net_of_for(detail.get("metrics")),
        "error": _error_line(detail),
    }
    if status in _SUCCESS:
        era = trade_era(detail.get("metrics"))
        view["tiles"], view["more_tiles"] = view_tiles(view["metrics"], era)
        if era in COUNT_BASIS:
            view["count_basis"] = COUNT_BASIS[era]
        exposure = exposure_block(detail.get("metrics"))
        if exposure is not None:
            # The card's one line (spec 07 §5); the text block's is `exposure:`.
            view["exposure_line"] = exposure["line"]
        if isinstance(reference, dict) and _number(reference.get("ret_pct")) is not None:
            # Spec 03 §2.2: one tile, `BTC hold`, beside the run's own
            # metrics — never a metric of the run (`summary_metrics` gains
            # nothing).
            view["more_tiles"].append(
                {
                    "key": "btc_hold",
                    "label": REFERENCE_LABEL,
                    "value": reference["ret_pct"],
                    "display": _signed(reference["ret_pct"], 1, "%"),
                }
            )
    if url:
        view["url_line"] = f"View in Keel: {url}"
    view.update(election_key(detail.get("id") or detail.get("backtest_id")))
    view["markdown"] = (
        _evidence_markdown(view) if view["size"] == "evidence" else _receipt_markdown(view)
    )
    return view


# ── The run's strategy config (Q-1789) ────────────────────────────────
#
# A backtest result named the run's numbers and window but not WHAT RAN.
# An agent reasoning about the result then advised blind: on claude.ai it
# twice recommended "try a rebalance buffer if it currently rebalances
# every bar" for a version that already had a 20% buffer. The config is
# read from the run's OWN commit (never HEAD — a later save must not
# relabel an old run) and rides as one short line in the text block (the
# model's only channel on claude.ai) and as `view.config`.

#: Pipeline blocks named on the config line before it says "+N more".
MAX_CONFIG_BLOCKS = 6


def _step_name(step: Any) -> str:
    name = getattr(step, "name", None)
    if isinstance(name, str) and name:
        return name
    kind = type(step).__name__
    return kind[:-4] if kind.endswith("Spec") else kind


def config_from_source(source: Any, *, version: Any = None) -> dict | None:
    """`{universe, clock, execution, blocks, line}` for one DSL source.

    Pure. The labels are the strategy view's own (`universe_block`,
    `execution_block`, `clock_label`) over the declaration view the
    change view diffs (`_declarations.declaration_view`) — no second
    vocabulary. A source that does not parse yields None: the line is a
    nicety and never a reason to fail a result.
    """
    if not isinstance(source, str) or not source.strip():
        return None
    try:
        from pipeline_engine.dsl import parse_strategy

        from ._declarations import declaration_view
        from ._strategy_view import clock_label, execution_block, execution_dsl, universe_block

        parsed = parse_strategy(source)
        declared = declaration_view(parsed)
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None

    universe = universe_block(declared.get("universe") or None)
    clock = clock_label(declared.get("globals") or None)
    execution = declared.get("execution") or {}
    rebalance = execution.get("rebalance")
    exec_label = (execution_block(execution) or {}).get("label") or "Every bar"
    if rebalance == "buffered":
        extras = [
            str(v) for v in (execution.get("buffer_mode"), execution.get("rebalance_method")) if v
        ]
        if extras:
            exec_label += f" ({', '.join(extras)})"
    steps = list(getattr(getattr(parsed, "pipeline", None), "steps", None) or [])
    blocks = [_step_name(step) for step in steps]

    parts = []
    if universe:
        parts.append(f"universe {universe['label']}")
    if clock:
        parts.append(f"clock {clock}")
    # The declaration as DSL a model can copy (Q-1846) — this line is the
    # text block's `config:`, read by the model and drawn by no card. The
    # human label is the fallback when the registry cannot be read.
    parts.append(execution_dsl(execution) or f"rebalance {exec_label[:1].lower()}{exec_label[1:]}")
    if blocks:
        shown = blocks[:MAX_CONFIG_BLOCKS]
        rest = len(blocks) - len(shown)
        parts.append("pipeline " + " → ".join(shown) + (f" → +{rest} more" if rest > 0 else ""))
    sizing = sizing_clause(steps)
    if sizing:
        parts.append(f"sizing: {sizing}")
    head = f"v{version}: " if version is not None else ""
    out = {
        "universe": universe["label"] if universe else None,
        "clock": clock,
        "execution": {k: v for k, v in execution.items() if v is not None},
        "blocks": blocks,
        "line": head + " · ".join(parts),
    }
    if sizing:
        out["sizing"] = sizing
    return out


# ── The sizing clause (spec 03 §2.7) ──────────────────────────────────
#
# `config:` names universe, clock, execution and blocks — and until now not
# how big the book is, which is what an agent reasoning about a Sharpe or a
# drawdown most needs. The TERMINAL sizer is the last `position_sizer` in
# the pipeline, skipping the cadence/rebalance ops that sit last by
# convention; a cap is a later `risk_manager` among the two below. Values
# come from the parsed source, defaults from the bundled registry — one
# owner. Parameter NAMES are never printed (`target_leverage=` would read as
# leverage the run took); the words are "gross", "per open position",
# "per signal", "capped".

#: Position-sizer ops that set cadence, not size — skipped when finding the
#: terminal sizer.
CADENCE_OPS = frozenset({"WeightCadence", "CalendarRebalance", "EqualWeightRebalancer"})

#: Sizer → `(parameter, template)`; `{v}` is the parameter's value (`:g`).
#: A sizer here whose parameter has no value renders its name alone.
SIZING_TEMPLATES: dict[str, tuple[str | None, str]] = {
    "FixedWeightSizer": ("weight_per_position", "FixedWeightSizer {v}× per open position"),
    "EqualWeightSizer": ("target_leverage", "EqualWeightSizer {v}× gross across held names"),
    "ForecastWeightNormalizer": ("target_leverage", "ForecastWeightNormalizer {v}× gross target"),
    "VolWeightSizer": ("target_leverage", "VolWeightSizer {v}× gross, vol-scaled"),
    "VolTargetWeightConverter": ("pct_target", "VolTargetWeightConverter {v}% vol target"),
    "EqualWeightAllocator": (None, "EqualWeightAllocator equal weights across selected names"),
    "DynamicEqualWeightAllocator": (
        None,
        "DynamicEqualWeightAllocator equal weights across selected names",
    ),
    "FixedWeightAllocator": (None, "FixedWeightAllocator equal weights across selected names"),
    "ManualWeightAllocator": (None, "ManualWeightAllocator fixed weights per name"),
    "RiskParityAllocator": (None, "RiskParityAllocator risk parity across selected names"),
    "RiskBudgetSizer": ("risk_per_position", "RiskBudgetSizer {v} risk per position"),
    "StopDistanceRiskSizer": ("risk_fraction", "StopDistanceRiskSizer {v} risk per position"),
    "BinaryToWeight": ("fixed_weight", "BinaryToWeight {v}× per signal"),
    "BetaHedgeAllocator": ("hedge_ratio", "BetaHedgeAllocator hedge ratio {v}"),
    "MarketLeverageScaler": ("base_leverage", "MarketLeverageScaler {v}× gross, regime-scaled"),
    "MarketRiskScaler": ("base_scale", "MarketRiskScaler {v}× gross, risk-scaled"),
}

#: Cap → the parameter whose value the suffix names.
CAP_PARAMS: dict[str, str] = {"LeverageCap": "max_leverage", "PortfolioMarginCap": "hard_limit"}

#: Sizers whose `{v}× gross` holds only while their own per-name `max_weight`
#: cannot bind: each clips a name to `max_weight` AFTER dividing the target
#: and sends the excess to cash, so with `max_weight` below the target the
#: book holds the target only on bars with enough names open (a "5×" book
#: measured median 1.0×, Q-2502). There the clause names the sizer alone —
#: no gross is claimed; the size the book actually ran is the exposure
#: line's measured gross (`metrics.exposure.gross_mean` / `gross_max`).
MAX_WEIGHT_CLIPPED = frozenset({"EqualWeightSizer", "ForecastWeightNormalizer", "VolWeightSizer"})


def _registry_components() -> dict[str, dict]:
    try:
        from keel.data.registry import _load_json

        components = _load_json("registry.json").get("components")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return {}
    if not isinstance(components, list):
        return {}
    return {str(c["name"]): c for c in components if isinstance(c, dict) and c.get("name")}


def _param_value(step: Any, name: str, registry: dict[str, dict]) -> Any:
    params = getattr(step, "params", None)
    if isinstance(params, dict) and params.get(name) is not None:
        return params[name]
    component = registry.get(_step_name(step)) or {}
    for param in component.get("parameters") or []:
        if isinstance(param, dict) and param.get("name") == name:
            return param.get("default")
    return None


def _fmt_g(value: Any) -> str | None:
    number = _number(value)
    return None if number is None else f"{number:g}"


def sizing_clause(steps: Any, registry: dict[str, dict] | None = None) -> str | None:
    """`FixedWeightSizer 0.2× per open position, capped at 2× gross` — or None
    when the pipeline has no position sizer (the line is then unchanged)."""
    registry = _registry_components() if registry is None else registry
    steps = list(steps or [])
    terminal_index = None
    for index, step in enumerate(steps):
        name = _step_name(step)
        category = (registry.get(name) or {}).get("category")
        if category == "position_sizer" and name not in CADENCE_OPS:
            terminal_index = index
    if terminal_index is None:
        return None
    sizer = steps[terminal_index]
    name = _step_name(sizer)
    param, template = SIZING_TEMPLATES.get(name, (None, name))
    if param is None:
        clause = template
    else:
        value = _fmt_g(_param_value(sizer, param, registry))
        clause = template.format(v=value) if value is not None else name
        if name in MAX_WEIGHT_CLIPPED and value is not None:
            cap = _number(_param_value(sizer, "max_weight", registry))
            target = _number(_param_value(sizer, param, registry))
            if cap is not None and target is not None and cap < target:
                clause = name
    cap = None
    for step in steps[terminal_index + 1 :]:
        cap_name = _step_name(step)
        if cap_name in CAP_PARAMS:
            value = _fmt_g(_param_value(step, CAP_PARAMS[cap_name], registry))
            if value is not None:
                cap = f", capped at {value}× gross"
    if cap:
        clause += cap
    elif name == "FixedWeightSizer":
        clause += ", uncapped"
    return clause


def run_config(client: Any, detail: Any) -> dict | None:
    """The config of the commit a run ran, or None. Advisory: one read of
    that commit's source; any failure costs the line, nothing more."""
    if not isinstance(detail, dict):
        return None
    strategy_id, commit_id = detail.get("strategy_id"), detail.get("commit_id")
    if not strategy_id or not commit_id:
        return None
    try:
        payload = client.get(f"/v1/strategies/{strategy_id}/versions/{commit_id}/source")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    source = payload.get("source") if isinstance(payload, dict) else None
    return config_from_source(source, version=detail.get("sequence_number"))


# ── The run's cost model (Q-2270) ─────────────────────────────────────
#
# `keel_backtest_run` takes `config.init_cash` / `fees` / `slippage`, and
# every net figure in a result moves with them — yet no result said what
# they were. `backtest_config` is SPARSE on the wire (only what the caller
# set, `BacktestConfig.as_overrides`), so an absent key IS the default; the
# effective value is resolved from the one model both engines resolve it
# from. ONE owner: `keel_backtest_compare`'s settings sentence reads it too.

#: The keys a run's cost model names (the margin cap rides the config line,
#: spec 03 §2.8).
COST_MODEL_KEYS: tuple[str, ...] = ("init_cash", "fees", "slippage")


def effective_backtest_config(detail: Any) -> dict[str, float] | None:
    """The run's effective `init_cash`, `fees`, `slippage` and `leverage`:
    its stored overrides over `BacktestConfig`'s defaults, or None when the
    model cannot be read (the line is then omitted, never invented)."""
    if not isinstance(detail, dict):
        return None
    try:
        from pipeline_engine.backtest_config import BacktestConfig

        defaults = BacktestConfig()
    except Exception:  # noqa: BLE001 — without the model there is no default to name
        return None
    raw = detail.get("backtest_config")
    overrides = raw if isinstance(raw, dict) else {}
    out: dict[str, float] = {}
    for key in (*COST_MODEL_KEYS, "leverage"):
        value = _number(overrides.get(key))
        out[key] = float(value) if value is not None else float(getattr(defaults, key))
    return out


def _bps(rate: float) -> str:
    return f"{rate * 10_000:g} bps"


def cost_model_block(detail: Any) -> dict | None:
    """`{init_cash, fees, slippage, fees_bps, slippage_bps, set_for_run, line}`
    — the capital, fee rate and slippage a run's numbers were computed at.

    `set_for_run` names the keys the caller set (the rest are platform
    defaults), so an agent can confirm the `config` it sent was applied.
    """
    effective = effective_backtest_config(detail)
    if effective is None:
        return None
    raw = detail.get("backtest_config")
    overrides = raw if isinstance(raw, dict) else {}
    set_for_run = [k for k in COST_MODEL_KEYS if _number(overrides.get(k)) is not None]
    line = (
        f"${effective['init_cash']:,.0f} starting capital · fees {_bps(effective['fees'])} "
        f"and slippage {_bps(effective['slippage'])} per fill"
    )
    if set_for_run:
        words = {"init_cash": "capital", "fees": "fees", "slippage": "slippage"}
        line += f" (set for this run: {', '.join(words[k] for k in set_for_run)})"
    else:
        line += " (platform defaults)"
    return {
        "init_cash": effective["init_cash"],
        "fees": effective["fees"],
        "slippage": effective["slippage"],
        "fees_bps": round(effective["fees"] * 10_000, 6),
        "slippage_bps": round(effective["slippage"] * 10_000, 6),
        "set_for_run": set_for_run,
        "line": line,
    }


def _default_leverage() -> float | None:
    try:
        from pipeline_engine.backtest_config import BacktestConfig

        return float(BacktestConfig().leverage)
    except Exception:  # noqa: BLE001 — without the model, name any explicit cap
        return None


#: `attach_run_config`'s "read it yourself" default — distinct from a
#: prefetched `None` (the read ran and found no config).
_READ = object()


def attach_run_config(
    extra: dict, view: Any, client: Any, detail: Any, *, config: Any = _READ
) -> None:
    """Put the run's config on an envelope in progress: `view.config` for
    the card and every structured reader, `strategy_config` for the text
    block (`_mcp_adapter._OPERATIONAL_FIELDS` renders it as `config: …`).

    `config` is `run_config(client, detail)`'s answer when the caller already
    read it (concurrently with its other reads, Q-1870); omitted, it is read
    here."""
    if config is _READ:
        config = run_config(client, detail)
    if config is None:
        return
    # The run's account margin cap, named only when the caller set one
    # (spec 03 §2.8): `backtest_config` is sparse on the wire, so an absent
    # key IS the default and the line stays silent about it.
    overrides = detail.get("backtest_config") if isinstance(detail, dict) else None
    leverage = _number(overrides.get("leverage")) if isinstance(overrides, dict) else None
    if leverage is not None and leverage != _default_leverage():
        config = {**config, "line": f"{config['line']} · margin cap {leverage:g}×"}
    if isinstance(view, dict):
        view["config"] = config
    extra["strategy_config"] = config["line"]


def is_terminal(detail: Any) -> bool:
    """Whether the run has stopped — the one place the word is decided."""
    return normalized_status(detail) in _TERMINAL


def is_success(detail: Any) -> bool:
    """Whether the run produced numbers (and therefore a curve to fetch)."""
    return normalized_status(detail) in _SUCCESS


# ── The `next` line (BUILD §2.7) ──────────────────────────────────────
#
# A string field in the envelope, present ONLY when its condition holds
# and never otherwise. The model reads it; the card ignores it. Advisory
# in every direction: a read that fails costs the line, nothing more —
# a hint must never gate a result the user already paid for.

#: How far back "in the last hour" reaches.
COMPARE_WINDOW_S = 3600

#: Runs named in the compare hint (the tool's own cap is 8).
MAX_COMPARE_IDS = 8

#: Below this, a run's fill count is itself the finding (W5 P2).
FEW_FILLS_THRESHOLD = 20


def few_fills_next(metrics: Any) -> str | None:
    """`few positions (3) — …` when the run barely traded (W5 P2).

    The cheapest carrier for the diagnosis: a run with three positions has
    not measured a strategy, and the reader would otherwise reason about
    a Sharpe computed from noise. `metrics` is `view.metrics`: the sample
    is the run's POSITIONS when it recorded them, else its trades (spec 01
    §9 L2), and the line names whichever it counted.
    """
    metrics = metrics or {}
    key, rate = "trades", "win_rate_pct"
    if _number(metrics.get("positions")) is not None:
        key, rate = "positions", "position_win_rate_pct"
    count = _number(metrics.get(key))
    if count is None or count >= FEW_FILLS_THRESHOLD:
        return None
    if count >= 1 and _number(metrics.get(rate)) is None:
        # Every position it opened was still open at the end: nothing
        # CLOSED, so the engine has no win rate to report (it is NaN over
        # zero closed records and the extractor drops it). That is a held
        # position — buy-and-hold, a constant weight — not a sparse signal,
        # and the note was a false alarm on exactly the benchmark a user
        # runs to compare against (R3 probe, Q-1844).
        return None
    # The fact only: the pointer that followed it (`keel_help(topic=
    # "strategy_phases")`) named a topic that is in-app journey posture and
    # is not served over MCP (spec 05 §2 R-L3, G5).
    return (
        f"few {count_label(key)} ({int(count)}) — a sparse signal or a window shorter "
        "than the warmup"
    )


def _completed_at(row: Any) -> Any:
    from datetime import datetime

    if not isinstance(row, dict):
        return None
    raw = row.get("completed_at") or row.get("queued_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


#: Runs the compare hint names — the latest few, not the tool's cap (Q-1878).
MAX_HINT_IDS = 4

#: Two ends this close are one window (the default end moves at 00:00 UTC,
#: so a set straddling midnight is one day apart — Q-1802's case).
SAME_WINDOW_END_SLACK_DAYS = 1


def _same_window(a: tuple[Any, Any], b: tuple[Any, Any]) -> bool:
    """Whether two `(start, end)` date pairs are one window. A side that is
    unknown cannot separate them (never a guessed difference)."""
    (a_start, a_end), (b_start, b_end) = a, b
    if a_start is not None and b_start is not None and a_start != b_start:
        return False
    if a_end is not None and b_end is not None:
        return abs((a_end - b_end).days) <= SAME_WINDOW_END_SLACK_DAYS
    return True


def compare_next(
    client: Any,
    *,
    strategy_id: Any,
    backtest_id: Any,
    end_date: Any = None,
    start_date: Any = None,
) -> str | None:
    """`3 runs on this strategy in the last hour — …` (BUILD §2.7).

    One cheap read. The ids are listed OLDEST first, because compare's
    deltas are each run minus the FIRST id — chronological order makes
    the baseline the first run of the set, which is what a reader
    comparing an iteration means by "against the baseline".

    Silent unless there is at least one OTHER completed run in the
    window: a single run is not a set, and suggesting a comparison of
    one would be noise on every first backtest.

    The line also names the baseline's `end_date` (Q-1802): this is the
    moment an agent is building a set of variants, and the default end
    moves at 00:00 UTC, so a set that straddles midnight compares windows
    one day apart. Pinning every later run to the baseline's end keeps
    the set on one window. `end_date` is THIS run's, used when it is the
    only run the listing does not carry yet.
    """
    from datetime import UTC, datetime, timedelta

    from keel.errors import KeelError

    from ._pagination import extract_paginated

    if not strategy_id:
        return None
    try:
        payload = client.get("/v1/backtests", strategy_id=strategy_id, limit=MAX_COMPARE_IDS)
    except KeelError:
        return None
    except Exception:  # noqa: BLE001 — a hint never fails the result
        return None
    rows, _ = extract_paginated(payload)
    cutoff = datetime.now(UTC) - timedelta(seconds=COMPARE_WINDOW_S)

    this_start, this_end = _parse_date(start_date), _parse_date(end_date)
    recent: list[tuple[Any, str, date | None]] = []
    other_windows = 0
    for row in rows:
        if not isinstance(row, dict) or normalized_status(row) not in _SUCCESS:
            continue
        run_id = row.get("id") or row.get("backtest_id")
        stamp = _completed_at(row)
        if not run_id or stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if stamp < cutoff:
            continue
        row_end = _parse_date(row.get("end_date"))
        if str(run_id) != str(backtest_id) and not _same_window(
            (this_start, this_end), (_parse_date(row.get("start_date")), row_end)
        ):
            # A deliberate sub-window (a half-period split, a stress
            # window) is not a variant of THIS run (Q-1878): comparing it
            # here is the unsafe comparison the compare tool warns about.
            other_windows += 1
            continue
        recent.append((stamp, str(run_id), row_end))

    ordered = sorted(recent, key=lambda triple: triple[0])
    ids = [run_id for _, run_id, _ in ordered]
    ends = [end for _, _, end in ordered]
    if backtest_id and str(backtest_id) not in ids:
        # This run completed moments ago and may not be in the listing
        # yet; it is the newest, so it goes last.
        ids.append(str(backtest_id))
        ends.append(this_end)
    if len(ids) < 2:
        return None
    total = len(ids)
    ids = ids[-MAX_HINT_IDS:]
    ends = ends[-MAX_HINT_IDS:]
    listed = ", ".join(f'"{run_id}"' for run_id in ids)
    # A fact, then the call (spec 02 §2.4 #1(a), R-23) — never when or how
    # to present: "when the set is complete, show them together once" was
    # reply cadence, the chat layer's to say. Capped (Q-1878): the line
    # rode every run and grew by one id each time.
    where = "on this strategy" if not other_windows else "over this run's window"
    count = f"{total} completed runs {where} in the last hour"
    if total > len(ids):
        count += f"; the latest {len(ids)}"
    line = f"{count}: keel_backtest_compare(backtest_ids=[{listed}]) sets them side by side"
    # A fact about the set, never a further run with a hard-coded date (spec
    # 05 R-L4: no quota-spending suggestion; Q-2012/Q-2029: no dates the user
    # did not name). It used to add `end_date="…" on a further run aligns
    # the windows`.
    if len({end for end in ends if end is not None}) > 1:
        line += "; their end dates differ"
    return line


def backtest_next(
    client: Any,
    detail: Any,
    view: Any,
    *,
    strategy_id: Any,
    few_fills: bool = True,
    compare: bool = True,
) -> str | None:
    """The one `next` line a finished backtest carries, or None.

    The two flags are the §2.7 table's own scoping, not a preference:
    the few-fills line belongs to `keel_backtest_run` and
    `keel_backtest_summarize` (the surfaces that present a run's
    numbers), the compare hint to `keel_backtest_run` and
    `keel_backtest_watch` (the surfaces that finish one).

    When a tool carries both, order matters and is deliberate: a run
    with three fills has not measured anything, so saying so beats
    inviting a comparison of it.
    """
    del few_fills  # the sample-size fact is its own data line now (`few_fills_note`)
    if not is_success(detail):
        return None
    if not compare:
        return None
    return compare_next(
        client,
        strategy_id=strategy_id,
        backtest_id=(detail or {}).get("id"),
        end_date=(detail or {}).get("end_date"),
        start_date=(detail or {}).get("start_date"),
    )


#: Concurrent reads one tool call may hold open against keel-api — the
#: compare ceiling (8 runs), so a full set is one round trip deep.
MAX_PARALLEL_READS = 8


def parallel_map(fn: Any, items: Any) -> list[Any]:
    """``[fn(item) for item in items]``, the reads concurrently (Q-1870).

    Results come back in ``items`` order, and the FIRST failure in that order
    is the one raised — exactly what the sequential comprehension raised, so
    a caller swaps one for the other without changing a single outcome, only
    the wall clock (a compare of N runs waited N sequential `/curve` reads).
    Each read runs in a copy of the caller's context, so a hosted request's
    bound credentials and outcome slot are the ones it sees.
    """
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    items = list(items)
    if len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_READS, len(items))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
        return [future.result() for future in futures]


def fetch_curve(client: Any, backtest_id: str) -> dict | None:
    """The compact curve for one completed run, or None.

    ONE copy per envelope, at the top level (§2.1). Best-effort at the
    same boundary as the presigned URL: a `KeelError` degrades to no
    curve (the card says so), programming errors propagate.
    """
    return fetch_curve_and_reference(client, backtest_id)[0]


def fetch_curve_and_reference(
    client: Any, backtest_id: str, *, points: int | None = None
) -> tuple[dict | None, dict | None]:
    """`(compact curve, None)` from ONE `/curve` read — the card's chart.

    No hold line on the default path (founder, 2026-09-23, Q-1886): a price
    read on every result was the 5-30 s the batch added, and a hold line is
    asked for, never volunteered. The second slot stays so the run, watch and
    summarize call sites keep one shape; `fetch_curve_and_holds` is the
    opt-in read.
    """
    curve, _ = fetch_curve_and_holds(client, backtest_id, points=points)
    return curve, None


def fetch_curve_and_holds(
    client: Any,
    backtest_id: str,
    *,
    points: int | None = None,
    holds: tuple[str, ...] | list[str] = (),
    reference_span: tuple[str, str] | None = None,
) -> tuple[dict | None, list[dict]]:
    """`(compact curve, [hold reference per requested symbol])`, ONE `/curve` read.

    keel-api computes the hold lines only when asked (`references=true`, spec
    03 §2.2's one owner, `build_reference`); without `holds` nothing is asked
    and nothing is projected — even from an older keel-api that still sends
    `references` unasked. `reference_span` — `(first day, exclusive end day)`
    — asks for the lines over that span instead of the run's own (a
    comparison's common span); each line is marked `common_span` only when
    the server ECHOES the span, so an older keel-api's run-1 span is never
    labelled as the common one.
    """
    from keel.errors import KeelError

    from .backtest_summarize import CURVE_POINTS, _compact_curve

    params: dict[str, Any] = {"points": points if points is not None else CURVE_POINTS}
    if holds:
        params["references"] = "true"
        if reference_span is not None:
            params["reference_start"], params["reference_end"] = reference_span
    try:
        payload = client.get(f"/v1/backtests/{backtest_id}/curve", **params)
    except KeelError:
        return None, []
    lines: list[dict] = []
    served = payload.get("reference_span") if isinstance(payload, dict) else None
    common = (
        reference_span is not None
        and isinstance(served, dict)
        and (served.get("start"), served.get("end")) == tuple(reference_span)
    )
    for symbol in holds:
        reference = reference_from_curve(payload, symbol=symbol)
        if reference is None:
            continue
        if common:
            reference["common_span"] = True
        lines.append(reference)
    return _compact_curve(payload), lines


def common_reference_span(details: list[dict]) -> tuple[str, str] | None:
    """`(first day, exclusive end day)` every run's chart covers, or None.

    Each run's chart runs from `metrics.chart_start` (the worker's stamp,
    equal to `/curve`'s trimmed start — guard 19) to `metrics.effective_end`
    (its last bar); a reference over their intersection is one number that
    means the same thing beside every run. None when any run lacks either
    stamp or the runs share no day.
    """
    starts, ends = [], []
    for detail in details:
        metrics = detail.get("metrics") if isinstance(detail, dict) else None
        if not isinstance(metrics, dict):
            return None
        start, end = (
            _parse_date(metrics.get("chart_start")),
            _parse_date(metrics.get("effective_end")),
        )
        if start is None or end is None:
            return None
        starts.append(start)
        ends.append(end)
    if not starts:
        return None
    lo, hi = max(starts), min(ends)
    if lo >= hi:
        return None
    return lo.isoformat(), hi.isoformat()


# ── The comparison view (BUILD §2.2) ──────────────────────────────────

KIND_COMPARISON = "comparison"

#: Runs the comparison markdown draws as a TABLE. Beyond this a table is
#: wider than any host frame, so the markdown becomes one line per run.
MAX_TABLE_RUNS = 4

#: Metrics `deltas[i]` carries — each run minus the baseline.
COMPARISON_DELTA_KEYS: tuple[str, ...] = (
    "sharpe",
    "total_return_pct",
    "max_drawdown_pct",
    "trades",
    "win_rate_pct",
    "positions",
    "position_win_rate_pct",
    "resizes",
    "turnover",
    "sortino",
    "calmar",
    "profit_factor",
    "position_profit_factor",
    "fees_paid",
    "carry",
)

#: The rows the markdown table draws, as `(label, key)` — the backtest
#: view's four headline tiles, same order, same labels (founder ruling
#: 2026-09-22, Q-1746).
_TABLE_ROWS: tuple[tuple[str, str], ...] = tuple((label, key) for key, label in HEADLINE_TILES)

#: The rows a run's ERA decides (spec 01 §6.2): drawn only when some run in
#: the set recorded them, so a set of legacy runs keeps its table, and each
#: cell compares like with like — a run that did not record a row's
#: quantity shows "—" there, and no delta is taken against it.
_ERA_ROWS: frozenset[str] = frozenset(
    {"positions", "position_win_rate_pct", "resizes", "position_profit_factor"}
)

#: Every row the CARD's table can draw, served as `view.rows` (Q-1787):
#: the four headline rows first, then the backtest view's "more" tiles in
#: their order and words — so the card renders the server's list and keeps
#: no labels of its own. `avg_holding`, a duration with no delta, is left
#: out. `_CARD_ROWS` are the rows every set draws; `_era_rows` adds the era
#: rows its runs recorded.
_ALL_CARD_ROWS: tuple[tuple[str, str], ...] = _TABLE_ROWS + tuple(
    (label, key) for key, label in MORE_TILES if key != "avg_holding"
)
_CARD_ROWS: tuple[tuple[str, str], ...] = tuple(
    (label, key) for label, key in _ALL_CARD_ROWS if key not in _ERA_ROWS
)

#: The count and cost rows the TEXT table draws under the headline four
#: (Q-1876): the tool's description promises the cost profile, and on
#: claude.ai the model reads nothing but this text. Labels are the "more"
#: tiles' own words, and every cell goes through `_cell` — copied from the
#: run's metrics, never recomputed.
_COST_TABLE_KEYS: tuple[str, ...] = (
    "trades",
    "positions",
    "position_win_rate_pct",
    "resizes",
    "turnover",
    "fees_paid",
)
_MORE_LABELS: dict[str, str] = dict(MORE_TILES)
_COST_TABLE_ROWS: tuple[tuple[str, str], ...] = tuple(
    (_MORE_LABELS[key], key) for key in _COST_TABLE_KEYS
)


def _era_rows(rows: tuple[tuple[str, str], ...], runs: list[dict]) -> tuple[tuple[str, str], ...]:
    """`rows` without the era rows no run in the set recorded."""
    carried = {key for run in runs for key in (run.get("metrics") or {})}
    return tuple((label, key) for label, key in rows if key not in _ERA_ROWS or key in carried)


def _money(value: Any) -> str:
    """`$1,217` — whole dollars, the way a fee total is read."""
    number = _number(value)
    if number is None:
        return "—"
    sign = MINUS if number < 0 else ""
    return f"{sign}${abs(number):,.0f}"


def _cell(key: str, metrics: dict) -> str:
    """One table cell, at a FIXED precision per row (Q-1710).

    Every column of a row is rendered by the same call, so a reader can
    compare down it: `0.60` under `0.82`, never `0.6`.
    """
    return _tile_display(key, metrics)


def comparison_deltas(runs: list[dict]) -> list[Any]:
    """`[None, {...}, ...]` — each run minus the BASELINE, by index.

    Baseline-relative rather than pairwise: `deltas[i]` answers "what did
    run i change against the first id", which is the question a reader
    comparing an iteration is asking. A key missing from either run has
    no delta at all — a delta against an absent measurement would be the
    measurement itself wearing a plus sign.
    """
    if not runs:
        return []
    base = runs[0].get("metrics") or {}
    out: list[Any] = [None]
    for run in runs[1:]:
        metrics = run.get("metrics") or {}
        row: dict[str, Any] = {}
        for key in COMPARISON_DELTA_KEYS:
            here, there = _number(metrics.get(key)), _number(base.get(key))
            if here is None or there is None:
                continue
            row[key] = here - there
        out.append(row)
    return out


def realism_cell(realism: Any) -> str:
    """`12% · avg $102` — one run's share of orders under the live minimum
    and its average order, copied from `envelope.realism` (the worker's
    `metrics.realism`); `—` when the run recorded none. A capped order log
    makes the share a floor, said with `≥`."""
    if not isinstance(realism, dict):
        return "—"
    share = _number(realism.get("share"))
    if share is None:
        return "—"
    text = ("≥" if realism.get("truncated") else "") + f"{round(share * 100)}%"
    avg = _number(realism.get("avg_order_notional"))
    if avg is not None:
        text += f" · avg ${avg:,.0f}"
    return text


def _realism_row(runs: list[dict]) -> tuple[str, list[str]] | None:
    """`("Orders under $10", cells)` for the comparison text, or None when no
    run carries realism. The minimum is the one a run RECORDED, never a
    literal; runs recorded under different minimums get no shared row."""
    blocks = [run.get("realism") for run in runs]
    minimums = {_number(b.get("min_notional")) for b in blocks if isinstance(b, dict)} - {None}
    if len(minimums) != 1:
        return None
    (minimum,) = minimums
    return f"Orders under ${minimum:g}", [realism_cell(b) for b in blocks]


def _comparison_markdown(view: dict) -> str:
    runs: list[dict] = view.get("runs") or []
    head = [f"**{view.get('name') or 'Untitled'}**", f"{len(runs)} runs"]
    window_line = _window_line(view.get("window") or {})
    if window_line:
        head.append(window_line)
    lines = [" · ".join(head)]

    if len(runs) <= MAX_TABLE_RUNS:
        labels = [str(run.get("label") or "") for run in runs]
        lines.append("| | " + " | ".join(labels) + " |")
        # The delimiter row is what makes a table a table: GFM — and every
        # renderer that follows it — draws six pipe-filled paragraphs
        # without one (Q-1710). One cell per HEADER cell, the empty
        # row-label column included, and the run columns right-aligned so
        # a reader compares down them.
        lines.append("| --- | " + " | ".join(["---:"] * len(labels)) + " |")
        for label, key in _era_rows(_TABLE_ROWS + _COST_TABLE_ROWS, runs):
            cells = [_cell(key, run.get("metrics") or {}) for run in runs]
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        realism = _realism_row(runs)
        if realism is not None:
            label, cells = realism
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        listed = " · ".join(
            f"{run.get('label')} {run.get('url')}" for run in runs if run.get("url")
        )
        if listed:
            lines.append(f"Runs: {listed}")
    else:
        # Beyond four columns a table is wider than any host frame, so
        # each run gets its own line — and carries its own link, which
        # is why there is no separate `Runs:` line on this arm.
        realism = _realism_row(runs)
        for index, run in enumerate(runs):
            metrics = run.get("metrics") or {}
            bits = [
                f"{label} {_cell(key, metrics)}"
                for label, key in _era_rows(_TABLE_ROWS + _COST_TABLE_ROWS, runs)
            ]
            if realism is not None:
                bits.append(f"{realism[0]} {realism[1][index]}")
            line = f"{run.get('label')} — " + " · ".join(bits)
            if run.get("url"):
                line += f" · {run['url']}"
            lines.append(line)

    for warning in view.get("warnings") or []:
        lines.append(str(warning))
    for note in view.get("notes") or []:
        lines.append(str(note))
    if view.get("url_line"):
        lines.append(str(view["url_line"]))
    return "\n".join(lines) + "\n"


def build_comparison_view(
    *,
    runs: list[dict],
    name: Any,
    window: Any = None,
    warnings: Any = None,
    notes: Any = None,
    overlap: Any = None,
    url: str | None = None,
    object_id: str | None = None,
) -> dict | None:
    """The `view` block for a set of runs, or None without any.

    `runs` keep the CALLER's order and `baseline` is index 0 — the tool
    documents "the baseline first", and every delta is measured from it.

    `warnings` are findings that make a comparison unsafe; `notes` are
    differences worth saying that do not (Q-1802 — a one-day window
    tail). `overlap` is `compare_windows`' common window plus its
    severity, present only when the windows differ: the card shades the
    chart outside it.
    """
    if not runs:
        return None
    view: dict[str, Any] = {
        "kind": KIND_COMPARISON,
        "size": "comparison",
        "name": name,
        "window": window,
        "runs": runs,
        "baseline": 0,
        "deltas": comparison_deltas(runs),
        "warnings": list(warnings or []),
        "notes": list(notes or []),
        # The row list the card's table draws, server-owned (Q-1746,
        # Q-1787): the first four inline, the rest behind "+N more".
        "rows": [
            {
                "key": key,
                "label": label,
                **({"short_label": SHORT_LABELS[key]} if key in SHORT_LABELS else {}),
                **({"format": FORMAT_KINDS[key]} if key in FORMAT_KINDS else {}),
            }
            for label, key in _era_rows(_ALL_CARD_ROWS, runs)
        ],
    }
    if overlap:
        view["overlap"] = overlap
    if url:
        view["url_line"] = f"View in Keel: {url}"
    view.update(election_key(object_id))
    view["markdown"] = _comparison_markdown(view)
    return view


# ── The result's data lines (agent-surface-cleanup spec 02 §2.4) ──────
#
# Each builder below turns data the tool ALREADY holds into (a) the field
# `structuredContent` carries and (b) the one line the text block carries —
# the field is the datum, the line its rendering (R3). Every line is a
# FACT: no adjective, no advice about the reply, and every number is the
# producer's own recorded field, never a literal (the 2026-09-02 lesson).
# `_mcp_adapter._OPERATIONAL_FIELDS` decides order and presence; nothing
# here decides whether a line is shown.

#: The reference's words (spec 03 §2.2): never "buy-and-hold" (the listed
#: rules ban "buy"), never "benchmark", never "baseline" (compare's first run).
REFERENCE_LABEL = "BTC hold"
REFERENCE_BASIS = "price only"
#: The symbols a hold line can be asked for — keel-api's REFERENCE_SYMBOLS.
HOLD_SYMBOLS: tuple[str, ...] = ("BTC", "ETH", "SOL")


def _close(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"${number:,.0f}" if number >= 100 else f"${number:,.2f}"


def reference_from_curve(curve: Any, *, symbol: str = "BTC") -> dict | None:
    """`envelope.reference` from `GET /v1/backtests/{id}/curve` (spec 03 §2.2).

    keel-api's `/curve` serves `references` — BTC first — computed ONCE
    over the trimmed curve's span; this only projects `references[0]`:
    `{label, basis, ret_pct, dd_pct}` (+ the start/end close pair and
    `rebased_at` when served — the sanity anchor review 06 §4 asked for)
    and its `series` as `[t, equity]` pairs, which ride `_meta` on hosts
    that take it. A null or absent entry (fewer than two closes, or an
    older keel-api) is None: no line, no tile, never a fabricated series.
    """
    if not isinstance(curve, dict):
        return None
    references = curve.get("references")
    if not isinstance(references, list) or not references:
        return None
    first = next((r for r in references if isinstance(r, dict) and r.get("symbol") == symbol), None)
    if first is None and symbol in HOLD_SYMBOLS:
        # An entry that carries no `symbol`: keel-api's fixed order.
        index = HOLD_SYMBOLS.index(symbol)
        entry = references[index] if index < len(references) else None
        first = entry if isinstance(entry, dict) and not entry.get("symbol") else None
    if not isinstance(first, dict):
        return None
    ret_pct, dd_pct = _number(first.get("ret_pct")), _number(first.get("dd_pct"))
    if ret_pct is None or dd_pct is None:
        return None
    out: dict[str, Any] = {
        "label": f"{symbol} hold",
        "basis": REFERENCE_BASIS,
        "ret_pct": ret_pct,
        "dd_pct": -abs(dd_pct),
    }
    for key in ("start_close", "end_close", "rebased_at", "end_close_at", "symbol"):
        if first.get(key) is not None:
            out[key] = first[key]
    series = []
    for point in first.get("series") or []:
        if isinstance(point, dict):
            t, eq = point.get("t"), point.get("equity")
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            t, eq = point[0], point[1]
        else:
            continue
        if isinstance(t, str) and _number(eq) is not None:
            series.append([t, eq])
    if series:
        out["series"] = series
    return out


def reference_span_words(reference: Any) -> str | None:
    """`Jan 21, 2025 – Sep 21, 2026` — the days the reference's first and last
    closes sit on (`rebased_at`, `end_close_at`), or None when not served."""
    if not isinstance(reference, dict):
        return None
    start, end = reference.get("rebased_at"), reference.get("end_close_at")
    if _parse_date(start) is None or _parse_date(end) is None:
        return None
    return f"{_day(start)} {_RANGE_DASH} {_day(end)}"


def reference_line(reference: Any, *, comparison: bool = False) -> str | None:
    """Spec 02 §2.4 #5 — `BTC hold, price only, Jan 21, 2025 – Sep 21, 2026:
    +41.2% · max drawdown −57.0%`.

    The span is NAMED (Q-1870), never "same span": a reference starts at the
    run's chart start — the first bar its indicators were valid — which can
    sit weeks after the headline window's start, so two runs over one window
    showed −8.8% and −15.6% and read as one span. On a comparison the line
    says which span it is: the one every run covers (`common_span`, the
    server echoed the span asked for) or run 1's (R-28). The close pair,
    when served, rides beside the return so a reader can catch a data
    problem a percentage hides."""
    if not isinstance(reference, dict):
        return None
    ret_pct, dd_pct = _number(reference.get("ret_pct")), _number(reference.get("dd_pct"))
    if ret_pct is None or dd_pct is None:
        return None
    words = reference_span_words(reference)
    if words is None:
        span = "over run 1's span" if comparison else "same span"
    elif not comparison:
        span = words
    elif reference.get("common_span"):
        span = f"{words} (the span every run covers)"
    else:
        span = f"{words} (run 1's span)"
    ret = _signed(ret_pct, 1, "%")
    start, end = _close(reference.get("start_close")), _close(reference.get("end_close"))
    if start and end:
        ret += f" ({start} → {end})"
    drawdown = _fixed(-abs(dd_pct), 1, "%")
    label = reference.get("label") or REFERENCE_LABEL
    return f"{label}, {REFERENCE_BASIS}, {span}: {ret} · max drawdown {drawdown}"


def realism_from_metrics(metrics: Any) -> dict | None:
    """`envelope.realism` (spec 03 §2.6, R-19) from the worker's
    `metrics.realism`: `{n_orders, n_below, share (0–1), avg_order_notional,
    min_notional}` (+ `truncated`). The minimum is the value the RUN
    recorded, so a later change to the constant cannot relabel an old run."""
    block = metrics.get("realism") if isinstance(metrics, dict) else None
    if not isinstance(block, dict):
        return None
    n_orders = block.get("n_orders")
    n_below = block.get("n_below_live_minimum", block.get("n_below"))
    if not isinstance(n_orders, int) or not isinstance(n_below, int):
        return None
    out: dict[str, Any] = {
        "n_orders": n_orders,
        "n_below": n_below,
        "share": block.get("share_below_live_minimum", block.get("share")),
        "avg_order_notional": block.get("avg_order_notional"),
        "min_notional": block.get("live_minimum_notional", block.get("min_notional")),
    }
    if block.get("truncated") is not None:
        out["truncated"] = bool(block["truncated"])
    return out


def realism_line(realism: Any) -> str | None:
    """Spec 02 §2.4 #6 — only when `n_below > 0`; the share renders as a
    percentage and the minimum is read from the data, never a literal.

    It says BOTH sides (Q-1881): the backtest fills every order — the
    worker's `derive_realism` reports the minimum and never enforces it, and
    nothing in the simulator skips an order — so those orders ARE in the
    returns, while live execution would skip them. Saying only the live half
    left the R4 agent unable to tell which way the result is biased."""
    if not isinstance(realism, dict):
        return None
    n_below, n_orders = realism.get("n_below"), realism.get("n_orders")
    share, minimum = _number(realism.get("share")), _number(realism.get("min_notional"))
    if not isinstance(n_below, int) or n_below <= 0 or not isinstance(n_orders, int):
        return None
    if share is None or minimum is None:
        return None
    avg = _number(realism.get("avg_order_notional"))
    line = (
        f"{n_below:,} of {n_orders:,} orders ({round(share * 100)}%) were below the "
        f"${minimum:g} live minimum; this backtest fills them (they are in its returns), "
        "live execution would skip them"
    )
    if avg is not None:
        line += f" — avg order ${avg:,.0f}"
    if realism.get("truncated"):
        line += " (the order log was capped; the counts are floors)"
    return line


#: A run shorter than this carries no halves (spec 03 §2.10 — 30 days per half).
HALVES_MIN_DAYS = 60


def halves_for(window: Any) -> list[list[str]] | None:
    """`[[start, mid], [mid, end_exclusive]]` for a completed run of ≥ 60
    days (spec 03 §2.10) — derived from `window.ran`, never stored."""
    ran = window.get("ran") if isinstance(window, dict) else None
    if not isinstance(ran, dict):
        return None
    start = _parse_date(ran.get("start"))
    last = _parse_date(ran.get("last_bar"))
    end = _parse_date(ran.get("end_exclusive"))
    if start is None or last is None or end is None:
        return None
    span = (last - start).days
    if span < HALVES_MIN_DAYS:
        return None
    mid = start + timedelta(days=span // 2)
    return [[start.isoformat(), mid.isoformat()], [mid.isoformat(), end.isoformat()]]


#: `good_result.library_overlap` at or above this reads "Same window".
LIBRARY_SAME_WINDOW = 0.98


def window_overlap(ran: Any, other: Any) -> float | None:
    """The fraction of `ran`'s covered days that `other` also covers (Q-1802's
    overlap, 0–1). `ran` is `window.ran`; `other` is `{start, end}` with an
    exclusive end (a library entry's `backtest_window`)."""
    if not isinstance(ran, dict) or not isinstance(other, dict):
        return None
    a0, a1 = _parse_date(ran.get("start")), _parse_date(ran.get("end_exclusive"))
    b0, b1 = _parse_date(other.get("start")), _parse_date(other.get("end"))
    if a0 is None or a1 is None or b0 is None or b1 is None or a1 <= a0:
        return None
    shared = (min(a1, b1) - max(a0, b0)).days
    return max(0.0, shared / (a1 - a0).days)


def good_result_block(
    metrics: Any,
    window: Any,
    *,
    library_window: Any = None,
    library_fork: bool = False,
) -> dict | None:
    """`envelope.good_result` (spec 02 §7): the marker's OWN recorded keys
    (`libs/backtest_messages/good_result.py` — thresholds included), plus
    `library_fork`/`library_overlap` and `halves` when they apply. None
    when the run carries no marker: a sub-threshold run says nothing."""
    marker = metrics.get("good_result") if isinstance(metrics, dict) else None
    if not isinstance(marker, dict) or not marker:
        return None
    out = dict(marker)
    if library_fork:
        out["library_fork"] = True
        ran = window.get("ran") if isinstance(window, dict) else None
        overlap = window_overlap(ran, library_window)
        if overlap is not None:
            out["library_overlap"] = round(overlap, 4)
    halves = halves_for(window)
    if halves:
        out["halves"] = halves
    return out


def good_result_line(good: Any) -> str | None:
    """Spec 02 §2.4 #7 — the fact line that replaced the nudge.

    Every number is the marker's recorded field; the line never cites a
    Sharpe without its drawdown (the honesty rule `_nudge` kept). The
    library clause reads `library_overlap`.

    It used to end with the two half-window runs spelled out as calls
    (``keel_backtest_run(…, start_date=…, end_date=…)``, then
    ``keel_backtest_compare``). That was a quota-spending suggestion with
    hard-coded dates, chaining three calls — spec 05 R-L4 / D-13 L3 forbid
    all three. The halves stay as DATA in `good_result.halves`; the line
    proposes no run.
    """
    if not isinstance(good, dict) or not good:
        return None
    sharpe, drawdown = _number(good.get("sharpe")), _number(good.get("max_drawdown"))
    days, total = _number(good.get("range_days")), _number(good.get("total_return"))
    if sharpe is None or drawdown is None or days is None or total is None:
        return None
    s_min = _number(good.get("sharpe_threshold"))
    d_min = _number(good.get("min_range_days"))
    r_min = _number(good.get("min_total_return_exclusive"))
    dd_max = _number(good.get("max_drawdown_magnitude_limit"))
    line = (
        f"Sharpe {sharpe:.2f} over {days:.0f} days, return {total:.1f}%, "
        f"max drawdown {abs(drawdown):.1f}%"
    )
    if s_min is not None and d_min is not None and r_min is not None and dd_max is not None:
        line += (
            f" — meets Keel's bar (Sharpe ≥ {s_min:g} over ≥ {d_min:g} days, "
            f"return > {r_min:g}, drawdown ≤ {dd_max:g}%)."
        )
    else:
        line += "."
    if good.get("library_fork"):
        overlap = _number(good.get("library_overlap"))
        if overlap is not None and overlap >= LIBRARY_SAME_WINDOW:
            line += " Same window as the library's verified run — not out-of-sample."
        elif overlap is not None:
            line += (
                f" Window differs from the library's verified run "
                f"({round(overlap * 100)}% overlap)."
            )
    return line


def window_rerun_pair(window: Any) -> tuple[str, str] | None:
    """`("2026-03-09", "2026-10-04")` — a run's window as the run tool's
    INPUT: keel-api's served `window.rerun` (Q-2422; inclusive start,
    exclusive end), else the same two served facts it is made of
    (`ran.start`, `ran.end_exclusive`) on an older keel-api. A copy, never a
    computation: passing the pair back runs the same window."""
    if not isinstance(window, dict):
        return None
    rerun = window.get("rerun")
    if isinstance(rerun, dict):
        start, end = _parse_date(rerun.get("start_date")), _parse_date(rerun.get("end_date"))
    else:
        ran = window.get("ran") if isinstance(window.get("ran"), dict) else {}
        start, end = _parse_date(ran.get("start")), _parse_date(ran.get("end_exclusive"))
    if start is None or end is None or end <= start:
        return None
    return start.isoformat(), end.isoformat()


def rerun_args_text(window: Any) -> str | None:
    """`start_date=2026-03-09 end_date=2026-10-04` — `window_rerun_pair` as
    the run tool's arguments, the one spelling every surface prints."""
    pair = window_rerun_pair(window)
    return f"start_date={pair[0]} end_date={pair[1]}" if pair else None


def window_line_for(window: Any, *, status: Any, clock: Any = None) -> str | None:
    """Spec 02 §2.4 #2 — `window:` from the served window object, on a
    COMPLETED run only (R-3)::

        1h · Mar 9 – Oct 3, 2026 (209 days, the 1h default · chart from …)
        · same window: start_date=2026-03-09 end_date=2026-10-04

    plus the start-move reason and the end cap when they apply. `clock` is
    the run's declared clock as the `config:` line names it
    (`config_from_source` → `clock_label`); `the 1h default` only when
    keel-api served `window.default` (submit stamped that an omitted start
    took the clock's default, Q-2422 — never inferred); `same window:` is
    the pair that re-runs this window (`rerun_args_text`). No warm-up count
    (Q-1991): the stored count cannot tell warm-up from a decision to stay
    flat, so the line states only what ran. The dates follow the one display
    rule."""
    if str(status or "").lower() not in ("completed", "succeeded"):
        return None
    if not isinstance(window, dict):
        return None
    ran = window.get("ran")
    if not isinstance(ran, dict) or not ran.get("start") or not ran.get("last_bar"):
        return None
    line = f"{_day(ran['start'])} {_RANGE_DASH} {_day(ran['last_bar'])}"
    if isinstance(clock, str) and clock.strip():
        line = f"{clock.strip()} · {line}"
    detail = []
    if isinstance(ran.get("days"), int):
        days = f"{ran['days']:,} days"
        default = window.get("default")
        if isinstance(default, dict) and isinstance(default.get("timeframe"), str):
            days += f", the {default['timeframe']} default"
        detail.append(days)
    if window.get("chart_start"):
        detail.append(f"chart from {_day(window['chart_start'])}")
    if detail:
        line += f" ({' · '.join(detail)})"
    requested = window.get("requested") if isinstance(window.get("requested"), dict) else {}
    req_start, req_end = _parse_date(requested.get("start")), _parse_date(requested.get("end"))
    ran_start, ran_end = _parse_date(ran.get("start")), _parse_date(ran.get("end_exclusive"))
    if req_start is not None and ran_start is not None and req_start != ran_start:
        reason = window.get("start_reason")
        line += f"; start moved from {_day(req_start.isoformat())}"
        if isinstance(reason, str) and reason.strip():
            line += f" — {reason.strip()}"
    if req_end is not None and ran_end is not None and req_end > ran_end:
        line += "; end capped at today"
    rerun = rerun_args_text(window)
    if rerun:
        line += f" · same window: {rerun}"
    return line


def attach_run_facts(
    extra: dict,
    detail: Any,
    *,
    client: Any = None,
    reference: Any = None,
) -> None:
    """Put a run's served facts on an envelope in progress (spec 02 §2.4):

    * `window` — the nested object keel-api serves (spec 03 §2.5), on every
      read that carries it;
    * on a COMPLETED run: `reference` (§2.2), `realism` (§2.6),
      `few_fills_note` (the sample-size fact, formerly a `next`),
      `exposure` (how much of the window held positions, spec 07 §5) and
      `good_result` (the marker's own keys + `halves`, + the library
      overlap when the strategy came from the library).

    Additive and absent-tolerant: an older keel-api that serves none of
    them leaves the envelope as it was. Advisory reads only (the library
    provenance, when a good-result marker needs it); a failure costs the
    clause, never the result.
    """
    if not isinstance(detail, dict):
        return
    window = detail.get("window") if isinstance(detail.get("window"), dict) else None
    if window is not None:
        extra["window"] = window
    # The capital and costs the run was submitted at — known from submission,
    # so it rides every exit, finished or not (Q-2270).
    cost_model = cost_model_block(detail)
    if cost_model is not None:
        extra["cost_model"] = cost_model
    if not is_success(detail):
        return
    metrics = detail.get("metrics")
    if isinstance(reference, dict):
        extra["reference"] = reference
    realism = realism_from_metrics(metrics)
    if realism is not None:
        extra["realism"] = realism
    few = few_fills_next(view_metrics(metrics))
    if few:
        extra["few_fills_note"] = few
    # How much of the window held positions (Q-1993's stamp), first-class
    # beside the metrics so an agent reads it without `metrics_raw`.
    exposure = exposure_block(metrics)
    if exposure is not None:
        extra["exposure"] = exposure
    if isinstance(metrics, dict) and metrics.get("good_result"):
        library_window, library_fork = _library_provenance(client, detail.get("strategy_id"))
        good = good_result_block(
            metrics, window, library_window=library_window, library_fork=library_fork
        )
        if good is not None:
            extra["good_result"] = good


def _library_provenance(client: Any, strategy_id: Any) -> tuple[dict | None, bool]:
    """`(entry backtest_window, library_fork)` for a strategy forked from the
    library (`metadata.library_source_slug`), else `(None, False)`."""
    if client is None or not strategy_id:
        return None, False
    try:
        meta = client.get(f"/v1/strategies/{strategy_id}")
    except Exception:  # noqa: BLE001 — a clause, never the result
        return None, False
    slug = None
    if isinstance(meta, dict):
        metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
        slug = meta.get("library_source_slug") or metadata.get("library_source_slug")
    if not isinstance(slug, str) or not slug:
        return None, False
    try:
        entry = client.get(f"/v1/library/{slug}")
    except Exception:  # noqa: BLE001 — the fork is known; its window is not
        return None, True
    window = entry.get("backtest_window") if isinstance(entry, dict) else None
    return (window if isinstance(window, dict) else None), True
