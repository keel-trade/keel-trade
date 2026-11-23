"""The study card's byte budgets, member by member, on the real fixtures (research spec 13 §3.5, §7.2, WL-CU-13).

Every fixture in ``tests/fixtures/cards/study/`` is a real tool result (``research.card_fixtures``). Per result:

* ``structuredContent`` ≤ 8,000 B (model context on ChatGPT, Codex and Claude Code; it does not move);
* each ``card_data`` member within §3.5's bound: ``key`` ≤ 12,000, ``charts`` ≤ 16,000, ``checks`` ≤ 7,000, the
  fixed members (copy, links, chips, method_line, notes, top, steps, focus) ≤ 2,000, the thumb ≤ 3,000;
* ``_meta["keel/card"]`` ≤ 40,000 (≤ 34,000 for the list read); the whole wire (text UTF-8 +
  structuredContent + _meta) ≤ 60,000.

Bytes are compact UTF-8 JSON (``separators=(",", ":")``), as SCR06 measures. The table prints member by member.

Non-vacuity: the set holds a study with card_data (a key chart and gallery entries), a list, a run and a refusal,
and every member measured is non-empty somewhere. Seeded reds (in-test, on copies): a key widened past 12,000 B
reds the key bound; checks padded to 41 rows of real row words reds the checks bound; a structuredContent carrying
the thumb back and 100 check rows reds the 8,000 bound. Controls: the fixtures as built.

13 §7.2's largest legal study read (100 bars, 50 results, 24 views at their largest shapes, the gallery fill and
its fill-off seed over 40,000 B) is built where its builder lives, ``libs/research/card_data_largest_test.py``
(CARD-FINAL): it fits every bound, and a card past 40,000 B is degraded (``card_summary.fit_card_data``), never a
failed read.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cards" / "study"

STRUCTURED_MAX = 8_000
CARD_META_MAX = 40_000
LIST_CARD_META_MAX = 34_000
WHOLE_MAX = 60_000
MEMBER_MAX = {"key": 12_000, "charts": 16_000, "checks": 7_000, "fixed": 2_000, "thumb": 3_000}
FIXED = ("copy", "links", "chips", "method_line", "notes", "top", "steps", "focus")
#: Measured overages, reported to their owner and held as a ratchet: the test reds on any other overage, on any of
#: these growing, and on any of these fixed (then it leaves this map). Empty since AGENT-2's 240d879c9b moved the
#: check rows' stricter/looser lenses and default-bar words to the card channel (D-363).
KNOWN_OVER: dict[str, int] = {}


def _bytes(value) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def _fixtures() -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(FIXTURES.glob("*.json"))
        if p.name != "MANIFEST.json"
    }


def _measure(fx: dict) -> dict[str, int]:
    meta = fx.get("_meta") or {}
    card = meta.get("keel/card") or {}
    data = card.get("card_data") or (fx.get("structuredContent") or {}).get("card_data") or {}
    thumb = ((card.get("card_summary") or {}).get("thumb")) or (
        ((fx.get("structuredContent") or {}).get("card_summary") or {}).get("thumb")
    )
    text = "".join(b.get("text", "") for b in fx.get("content") or [])
    return {
        "structured": _bytes(fx.get("structuredContent") or {}),
        "card": _bytes(card),
        "whole": len(text.encode()) + _bytes(fx.get("structuredContent") or {}) + _bytes(meta),
        "key": _bytes(data.get("key")) if data.get("key") else 0,
        "charts": _bytes(data.get("charts")) if data.get("charts") else 0,
        "checks": _bytes(data.get("checks")) if data.get("checks") else 0,
        "fixed": _bytes({k: data.get(k) for k in FIXED if k in data}) if data else 0,
        "thumb": _bytes(thumb) if thumb else 0,
    }


def _violations(name: str, m: dict[str, int]) -> list[str]:
    out = []
    if m["structured"] > STRUCTURED_MAX:
        out.append(f"{name}: structuredContent {m['structured']:,} > {STRUCTURED_MAX:,}")
    cap = LIST_CARD_META_MAX if name.startswith("list") else CARD_META_MAX
    if m["card"] > cap:
        out.append(f"{name}: _meta keel/card {m['card']:,} > {cap:,}")
    if m["whole"] > WHOLE_MAX:
        out.append(f"{name}: whole {m['whole']:,} > {WHOLE_MAX:,}")
    for member, bound in MEMBER_MAX.items():
        if m[member] > bound:
            out.append(f"{name}: card_data.{member} {m[member]:,} > {bound:,}")
    return out


def test_every_fixture_is_within_every_budget_member_by_member(capsys) -> None:
    fixtures = _fixtures()
    rows = {name: _measure(fx) for name, fx in fixtures.items()}
    with capsys.disabled():
        print("\nstudy card budgets (B):")
        cols = ("structured", "card", "whole", "key", "charts", "checks", "fixed", "thumb")
        print(f"  {'fixture':28}" + "".join(f"{c:>11}" for c in cols))
        for name, m in rows.items():
            print(f"  {name:28}" + "".join(f"{m[c]:>11,}" for c in cols))
    problems = [p for name, m in rows.items() for p in _violations(name, m)]
    others = [
        p for p in problems if p.split(":")[0] not in KNOWN_OVER or "structuredContent" not in p
    ]
    assert others == []
    over = {n: m["structured"] for n, m in rows.items() if m["structured"] > STRUCTURED_MAX}
    assert over == KNOWN_OVER, (
        "a known overage moved (it grew, or was fixed: then drop it from KNOWN_OVER)"
    )


def test_the_budget_population_is_not_vacuous() -> None:
    fixtures = _fixtures()
    assert {"list", "run-done", "refusal-limit"} <= set(fixtures)
    rows = {name: _measure(fx) for name, fx in fixtures.items()}
    with_data = [n for n, m in rows.items() if m["key"] and m["charts"]]
    assert with_data, "no fixture carries card_data with a key chart and gallery entries"
    for member in ("structured", "card", "key", "charts", "checks", "fixed", "thumb"):
        assert any(m[member] for m in rows.values()), member


def _card_data(fx: dict) -> dict:
    return fx["_meta"]["keel/card"]["card_data"]


@pytest.mark.parametrize("seed", ["key", "checks", "structured"])
def test_each_bound_reds_when_seeded(seed) -> None:
    fixtures = _fixtures()
    name = next(
        n
        for n, fx in fixtures.items()
        if n.startswith("study-") and _measure(fx)["key"] and not _violations(n, _measure(fx))
    )
    fx = copy.deepcopy(fixtures[name])
    assert _violations(name, _measure(fx)) == []  # control
    if seed == "key":
        key = _card_data(fx)["key"]
        read = key.get("read") or {}
        key["read"] = read
        while _bytes(key) <= MEMBER_MAX["key"]:  # served words past the key's bound
            read["x"] = (read.get("x") or "") + "|Mar 23, 2025" * 50
    elif seed == "checks":
        checks = _card_data(fx)["checks"] or {"count": 0, "met": 0, "rows": [], "more_words": None}
        row = {"label": "Sharpe at twice the costs, the bar the user set", "met": False, "locked": False,
               "value_words": "1.90", "interval_words": "1.37 to 2.26", "sample_words": "585 days · 100 coins",
               "bar_words": "your bar ≥ 2.00", "sel": "bar:0"}  # fmt: skip
        checks["rows"] = [dict(row, sel=f"bar:{i}") for i in range(41)]
        _card_data(fx)["checks"] = checks
    else:
        sc = fx["structuredContent"]
        sc["card_summary"]["thumb"] = fx["_meta"]["keel/card"].get("card_summary", {}).get("thumb")
        sc["checks"] = {"rows": [{"label": f"Bar {i}", "value_words": "1.00"} for i in range(300)]}
    problems = _violations(name, _measure(fx))
    assert problems, seed
    needle = {
        "key": "card_data.key",
        "checks": "card_data.checks",
        "structured": "structuredContent",
    }[seed]
    assert any(needle in p for p in problems), problems
