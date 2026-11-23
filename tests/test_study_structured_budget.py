"""MCP-FINAL S2 (13 §3.5, §7.2): a study read's structuredContent stays within 8,000 B at the largest legal shape, at
RUNTIME, and the call never fails on its size.

Measured on staging: a Trader 7-bar Check with a v2 Draft and a linked study read 7,996 B, and a legal 200-character
title (repeated on the study, the card, the footer, the linked note, the versions-tried family and each check row's
``result``) put it near 9.8 KB. Nothing bounded structuredContent at runtime; the budget test read fixtures only.

The base is the real chk_v3 read (``fixtures/cards/study/study-chk_v3.json``: structuredContent and its card merged
back, as the host's adapter does). It is grown to the largest legal shape: a 200-character title everywhere the
title is served, every check row naming that one subject, the most check rows structured_study keeps, a linked
study, a Draft and three stale notes, every footing name list long.

1. ``partition`` serves it within 8,000 B; the model keeps the study, counts, links, can_run and the bars' count and
   met; everything that left structuredContent is in the card (merging the two gives back every member).
2. Seeded overflow (the runtime fit off: ``structured_max`` 0): the same read is over 8,000 B, so the fit is what
   keeps it (non-vacuity), and it warns with what moved.
3. Control: the real chk_v3 read is under the bound and nothing is fitted (no warning).
4. A uniform ``result`` (every row the study's own subject) leaves the rows; a Compare's two results stay.

SEED (reverse the edit): ``_fit`` returning at once -> arm 1 reds (over 8,000 B), arms 3 and 4 stay green.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
from pathlib import Path

from keel.tools.outcomes import _channels


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cards" / "study"
STRUCTURED_MAX = 8_000
TITLE = (
    "Momentum rotation with a volatility cap and a trend filter on the top coins by volume " * 3
)[:200]


def _b(value) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def _envelope(name: str = "study-chk_v3") -> dict:
    """A fixture's envelope as keel-api served it: structuredContent with its card merged back."""
    fx = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    env = copy.deepcopy(fx["structuredContent"])
    _channels._merge(env, copy.deepcopy((fx.get("_meta") or {}).get("keel/card") or {}))
    return env


def _largest(env: dict) -> dict:
    env = copy.deepcopy(env)
    old = env["study"]["title"]
    env = json.loads(json.dumps(env, ensure_ascii=False).replace(old, TITLE))
    env["study"]["title"] = TITLE
    env["card_summary"]["title"] = TITLE
    env["card_summary"]["footer"]["subject"] = TITLE
    rows = env["checks"]["rows"]
    for r in rows:
        r["result"] = TITLE
        r["withheld_reason"] = (
            "Not measured: Random books need at least twice as many active coins as the book holds: it holds as "
            "many as 8 at once, so it needs 16, and only 8 are active."
        )
    env["study"]["linked_from"] = {
        "study_id": "stu_" + "l" * 26,
        "title": TITLE,
        "words": f"From {TITLE} (new days)",
    }
    env["study"]["draft"] = {
        "version": 2,
        "words": f"v2 of {TITLE} is a Draft: the costs and the window changed.",
    }
    env["stale"] = [
        {"words": f"A newer version of {TITLE} (v4) exists. This study stays on v3."}
    ] * 3
    return env


def test_the_largest_legal_read_is_served_within_the_bound(caplog) -> None:
    env = _largest(_envelope())
    with caplog.at_level(logging.WARNING, logger=_channels.__name__):
        structured, card = _channels.partition(env, "study")
    assert _b(structured) <= STRUCTURED_MAX, _b(structured)
    for key in ("study", "counts", "links", "can_run"):
        assert key in structured, key
    assert structured["checks"]["count"] and "met" in structured["checks"]
    assert structured["study"]["title"] == TITLE
    # nothing is lost: the card holds every member that left
    back = copy.deepcopy(structured)
    _channels._merge(back, card)
    for key in ("footing", "card_summary", "checks"):
        assert key in back, key
    assert [r["label"] for r in back["checks"]["rows"]] == [
        r["label"] for r in env["checks"]["rows"]
    ][: len(back["checks"]["rows"])]
    assert [r for r in caplog.records if "moved" in r.getMessage()]


def test_seeded_without_the_runtime_fit_the_same_read_is_over(monkeypatch) -> None:
    spec = dataclasses.replace(_channels.CHANNEL_MAP["study"], structured_max=0)
    monkeypatch.setitem(_channels.CHANNEL_MAP, "study", spec)
    structured, _ = _channels.partition(_largest(_envelope()), "study")
    assert _b(structured) > STRUCTURED_MAX, _b(structured)  # non-vacuity: the fit is what keeps it


def test_a_real_read_is_not_fitted(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=_channels.__name__):
        structured, _ = _channels.partition(_envelope(), "study")
    assert _b(structured) <= STRUCTURED_MAX
    assert not [r for r in caplog.records if "moved" in r.getMessage()]


def test_a_uniform_result_leaves_the_rows_and_a_compares_stay() -> None:
    env = _envelope()
    subject = env["card_summary"]["footer"]["subject"]
    for r in env["checks"]["rows"]:
        r["result"] = subject
    env["checks"]["rows"][0]["result"] = (
        None  # a summary row (luck.summary) names no result, as on staging
    )
    structured, _ = _channels.partition(env, "study")
    assert all("result" not in r for r in structured["checks"]["rows"])
    two = _envelope()
    for i, r in enumerate(two["checks"]["rows"]):
        r["result"] = "Momentum rotation v3" if i % 2 else "Momentum rotation v4"
    structured, _ = _channels.partition(two, "study")
    assert {r.get("result") for r in structured["checks"]["rows"]} == {
        "Momentum rotation v3",
        "Momentum rotation v4",
    }


def test_a_top_rows_spark_rides_the_card_and_its_interval_stays() -> None:
    """R4 (06 §3.5): a Compare's top row carries its own sparkline (the key thumb's series), card data the model
    never reads, and its interval ``{lo, hi}``, which it may. SEED: take the spark path out of the study's card set
    -> red. Control: the row's label and value stay in structuredContent."""
    env = _envelope()
    env["card_summary"]["top5"] = {
        "kind": "results",
        "rows": [
            {"label": "v4", "value": 1.2, "interval": {"lo": 0.4, "hi": 2.0},
             "spark": {"family": "Lines", "view_index": 0, "facts": {"series": [{"c": 2, "y": "zz"}]}}},
        ],
        "total": 1,
    }  # fmt: skip
    structured, card = _channels.partition(env, "study")
    row = structured["card_summary"]["top5"]["rows"][0]
    assert "spark" not in row and row["interval"] == {"lo": 0.4, "hi": 2.0} and row["label"] == "v4"
    assert card["card_summary"]["top5"]["rows"][0]["spark"]["family"] == "Lines"
