"""The run's count is "trades", never "round trips" (Q-1906).

Founder ruling 2026-09-24: user-facing trading language says "trades" —
"use the correct language anywhere in our app". This scans every string
the SDK renders for a person or a model that can name the count — the
backtest view's labels and markdown (every size), the comparison view's
rows and markdown, the strategy evidence line, the library facts line,
the sample-size note, the positions basis — plus the string LITERALS of
every card script, for the old trading word.

The listed-copy rules (tests/test_policy_scan.py) still forbid "trades"
in tool descriptions and in the cards' STATIC HTML, so the word is served:
`MORE_TILES` owns the label, `count_label()` the running-text word, and the
cards draw what the envelope carries. Identifiers and wire keys
(`round_trips`, `position_round_trip`) are data, not copy, and the pattern
does not match them (an underscore is neither a space nor a hyphen).

# SEED A: in `_backtest_view.MORE_TILES` relabel ("trades", "Trades")
# back to "Round trips" — the view arm reds (labels, markdown, evidence
# line, library line, few-trades note all follow the one label).
# SEED B: in card-strategy.js append `+ " round trips"` to the evidence
# count text — the card-literal arm reds while the view arm stays green.

Trade-metrics spec 01 (Q-2122): the scan also reads every era's view (A, B,
C, unknown), the era basis lines, the positions tool's basis and
not-recorded line, and the mixed-era compare line; and it refuses the era
STAMP in rendered copy — `position_round_trip` is stored data (§6.3), and a
string that renders it names the count "round trip" in a model's reply.
SEED C (run 2026-09-30): make `COUNT_BASIS["B"]` end "(position_round_trip)."
— the view arm reds on the stamp while the card-literal arm stays green.
"""

from __future__ import annotations

import re
from pathlib import Path

from keel.tools.outcomes._backtest_view import (
    COUNT_BASIS,
    MIXED_ERA_LINE,
    MORE_TILES,
    build_backtest_view,
    count_label,
    few_fills_next,
)
from keel.tools.outcomes._strategy_view import _evidence, _evidence_line
from keel.tools.outcomes.backtest_positions import _BASIS, _NOT_RECORDED
from keel.tools.outcomes.library import _metrics_phrase


#: The trading sense: "round trip(s)", "round-trip", "roundtrip". Not
#: `round_trips` (a key) — the underscore matches neither alternative.
OLD_WORD_RE = re.compile(r"round[ \-]?trips?", re.IGNORECASE)

#: The Era B stamp (spec 01 §6.3): stored data, never rendered copy.
STAMP_RE = re.compile(r"position_round_trip")

_ASSETS = Path(__file__).resolve().parents[1] / "keel" / "widgets" / "assets"

_DETAIL = {
    "id": "btr_9f3",
    "status": "COMPLETED",
    "strategy_id": "str_mom",
    "strategy_name": "Simple Momentum (ROC 20)",
    "sequence_number": 3,
    "commit_id": "c_1a2b3c4d",
    "engine": "native",
    "start_date": "2024-08-15",
    "end_date": "2026-09-22",
    "completed_at": "2026-09-22T11:04:00Z",
    "metrics": {
        "sharpe": 0.67,
        "total_return_pct": 60.5,
        "max_drawdown_pct": -20.1,
        "win_rate_pct": 26.0,
        "total_trades": 1761,
        "turnover": 220.0,
        "sortino": 1.06,
        "calmar": 2.0,
        "profit_factor": 1.08,
        "total_fees_paid": 1217.0,
    },
}


def _view_strings() -> list[str]:
    """Every rendered string that can name the count, from the real owners."""
    out: list[str] = []
    for size in ("receipt", "evidence"):
        view = build_backtest_view(_DETAIL, size=size, url="https://app.usekeel.io/b")
        out.append(view["markdown"])
        out += [t["label"] for t in view.get("tiles") or []]
        out += [t["label"] for t in view.get("more_tiles") or []]
    from tests.test_render_cadence import _compare_varying

    env, _ = _compare_varying(3)
    out.append(env["view"]["markdown"])
    out += [r["label"] for r in env["view"]["rows"]]
    evidence = _evidence(_DETAIL["metrics"], version=3, start="2024-08-15", end="2026-09-22")
    out.append(evidence["count_label"])
    out.append(_evidence_line(evidence))
    out.append(_metrics_phrase(0.76, 56.5, 39.7, 1143))
    out.append(few_fills_next({"trades": 3, "win_rate_pct": 33.3}))
    out.append(few_fills_next({"positions": 3, "position_win_rate_pct": 33.3}))
    out.append(_BASIS)
    out.append(_NOT_RECORDED)
    out += list(COUNT_BASIS.values())
    out.append(MIXED_ERA_LINE)
    # Every era's view, labels and markdown (spec 01 §6.2).
    from tests.test_render_cadence import ERA_METRICS

    for metrics in ERA_METRICS.values():
        detail = {**_DETAIL, "metrics": metrics}
        for size in ("receipt", "evidence"):
            view = build_backtest_view(detail, size=size, url="https://app.usekeel.io/b")
            out.append(view["markdown"])
            out += [t["label"] for t in (view.get("tiles") or []) + (view.get("more_tiles") or [])]
        evidence = _evidence(metrics, version=3, start="2024-08-15", end="2026-09-22")
        out.append(_evidence_line(evidence))
    return out


_LINE_COMMENT_RE = re.compile(r"(?<![:\"'\\])//.*$", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LITERAL_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`")


def _card_literals() -> dict[str, list[str]]:
    """Each card script's string literals, comments stripped — the copy a
    card can draw from its own source (engineering prose in comments, such
    as host-adapter's "a JSON round trip", is not copy)."""
    out: dict[str, list[str]] = {}
    for path in sorted(_ASSETS.glob("*.js")):
        src = _BLOCK_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
        src = _LINE_COMMENT_RE.sub("", src)
        out[path.name] = _LITERAL_RE.findall(src)
    return out


def test_no_rendered_string_calls_the_count_round_trips():
    strings = _view_strings()
    hits = [s for s in strings if OLD_WORD_RE.search(s or "") or STAMP_RE.search(s or "")]
    assert not hits, "the old word reached rendered copy:\n" + "\n---\n".join(hits)
    # Not vacuous: the scan read real copy, and that copy names the count
    # with the founder's word.
    assert len(strings) >= 20, len(strings)
    assert dict(MORE_TILES)["trades"] == "Trades"
    assert count_label() == "trades"
    joined = "\n".join(strings)
    assert "Trades 1,761" in joined  # the evidence markdown's count
    assert "1,143 trades" in joined and "few trades (3)" in joined
    # Not vacuous on the new copy (spec 01): the scan visited both era
    # basis lines, the mixed-era line and an Era B view's labels — and the
    # stamp pattern can fire.
    assert "Positions are not recorded for this run." in joined
    assert "This run counted positions, not trades" in joined
    assert MIXED_ERA_LINE in joined and "Position win rate 40.0%" in joined
    assert "few positions (3)" in joined and "53 positions" in joined
    assert STAMP_RE.search("trade_model: position_round_trip")


def test_no_card_literal_calls_the_count_round_trips():
    literals = _card_literals()
    hits = [
        f"{name}: {lit}"
        for name, lits in literals.items()
        for lit in lits
        if OLD_WORD_RE.search(lit)
    ]
    assert not hits, "\n".join(hits)
    # Not vacuous: every card script was read and yielded literals — and the
    # extractor sees the count's key (so it would see a label beside it).
    assert {"card-backtest.js", "card-compare.js", "card-strategy.js", "card-live.js"} <= set(
        literals
    )
    assert sum(len(v) for v in literals.values()) > 500
    # The extractor reads label-shaped literals: the compare card's own
    # static labels and its count key both come through it.
    assert '"Win rate"' in literals["card-compare.js"]
    assert '"round_trips"' in literals["card-compare.js"]
