"""A plan's structuredContent stays within 13 §3.5's 8,000 B, on real plan results (CARD-RENDER, D-381).

``tests/fixtures/cards/plan/*.json`` are seven plan results recorded from staging by CARD-RENDER (the keel-trade
0.10.0 server over STG-SURFACES' test accounts, 2026-11-19), as the SDK's envelope before the channel split (its
structuredContent with the card channel merged back and the text as ``view.markdown``); ids are normalised. Four of
them read 8,265-14,386 B of structuredContent under the old rule (``filled`` 3.2 KB, ``source`` 2.8 KB, ``words``
2.5 KB). The split now moves what only the card or the app reads (``_channels._PLAN_CARD``) to
``_meta["keel/card"]`` and drops the source (D-353).

Asserted per result: structuredContent ≤ 8,000 B; the model still reads what it plans and runs by (``plan_id``,
``steps``, ``issues``, ``words.work``); the card still gets what it draws (``words.card_line``); no source rides
either channel.

SEED (in-test, on the split itself): the study kind without ``_PLAN_CARD`` and without the source drop -> the
budget arm reds on plan-check and plan-free-limit. Control: the three plans already under budget stay under it in
both arms.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from keel.tools.outcomes import _channels


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cards" / "plan"
STRUCTURED_MAX = 8_000
#: Measured under the old rule (CARD-RENDER's table); the seed arm must find these over again.
WERE_OVER = {"plan-check", "plan-free-limit", "plan-map-wall", "plan-bigmap"}


def _bytes(value) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def _plans() -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURES.glob("*.json"))
    }


def test_the_fixtures_are_real_plans() -> None:
    plans = _plans()
    assert len(plans) == 7 and WERE_OVER <= set(plans)  # non-vacuity
    for name, env in plans.items():
        assert env["plan_id"].startswith("pln_") and env["steps"], name
        assert env["view"]["mode"] == "plan", name


@pytest.mark.parametrize("name", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_a_plans_structured_content_fits_and_keeps_what_the_model_reads(name: str) -> None:
    env = _plans()[name]
    structured, card = _channels.partition(env, "study")
    assert _bytes(structured) <= STRUCTURED_MAX, (name, _bytes(structured))
    assert (
        structured["plan_id"] == env["plan_id"] and structured["steps"] and "issues" in structured
    )
    assert structured["words"]["work"] == env["words"]["work"]
    assert "source" not in structured and "source" not in card  # D-353: never a channel's
    assert "filled" not in structured and card.get("filled") == env.get("filled")
    assert card["words"]["card_line"] == env["words"]["card_line"]  # the card draws its line


def test_seed_the_old_split_puts_four_plans_back_over() -> None:
    old = dataclasses.replace(
        _channels.CHANNEL_MAP["study"],
        card=_channels.CHANNEL_MAP["study"].card - _channels._PLAN_CARD,
        drop=_channels.CHANNEL_MAP["study"].drop - {"source"},
        # the old split had no runtime fit either (MCP-FINAL S2 added it): without this the fit hides the overflow
        structured_max=0,
    )
    over = set()
    for name, env in _plans().items():
        saved = _channels.CHANNEL_MAP["study"]
        _channels.CHANNEL_MAP["study"] = old
        try:
            structured, _ = _channels.partition(env, "study")
        finally:
            _channels.CHANNEL_MAP["study"] = saved
        if _bytes(structured) > STRUCTURED_MAX:
            over.add(name)
    assert (
        over == WERE_OVER
    )  # the seed reds exactly the plans CARD-RENDER measured; the rest stay under
