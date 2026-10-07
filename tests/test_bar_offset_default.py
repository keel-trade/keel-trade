"""One daily-close rule across every skeleton an agent is served (Q-1895).

R4 (2026-09-23, ChatGPT, staging): the strategy-creation skill and
`keel_strategy_compose`'s description drew the skeleton as
`Globals(target_timeframe='1d', bar_offset='12h')` while `dsl_syntax` said an
omitted offset closes the daily bar at 00:00 UTC. Two otherwise-identical HYPE
strategies built from the two sources returned −7.1% (12:00 close) and
+44.6% (00:00 close). The rule every served surface now states: a `1d`
strategy omits `bar_offset` unless the user asks for a different close, and an
example that declares one says what it changes.

What is scanned is what an agent is SERVED from this wheel — the bundled
knowledge / patterns / reference copies (`keel/data`), the skills, and the
compose tool's two descriptions — not the repo sources they are built from.

# SEED (run 2026-09-23): put `bar_offset='12h'` back into the skeleton line of
# `keel/skills/strategy-creation/SKILL.md` (`Globals(target_timeframe='1d',
# bar_offset='12h')`) — `test_every_served_skeleton_omits_the_offset` reds
# naming that skill while the other skeletons stay green. Revert by reversing
# the edit (never `git checkout` in a shared tree).

The served examples that DO declare an offset are held to saying what it
changes by `libs/pipeline_engine/reference/bar_offset_rule_test.py`, which
reads the repo corpus the wheel's copies are built from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap


_bootstrap()

_SDK = Path(__file__).resolve().parents[1] / "keel"

#: A `1d` Globals that declares an offset, either quote style.
_DAILY_WITH_OFFSET = re.compile(
    r"Globals\(\s*target_timeframe=['\"]1d['\"]\s*,\s*bar_offset=['\"](\w+)['\"]\s*\)"
)
_DAILY_BARE = re.compile(r"Globals\(\s*target_timeframe=['\"]1d['\"]\s*\)")


def _skeletons() -> dict[str, str]:
    """The skeletons a first strategy is drafted from, by owner."""
    compose = OUTCOMES["keel_strategy_compose"]
    skill = (_SDK / "skills" / "strategy-creation" / "SKILL.md").read_text(encoding="utf-8")
    dsl = (_SDK / "data" / "knowledge" / "dsl_syntax.md").read_text(encoding="utf-8")
    return {
        "keel_strategy_compose.description": compose.description,
        "keel_strategy_compose.listed_description": compose.listed_description or "",
        "skills/strategy-creation/SKILL.md": skill,
        "data/knowledge/dsl_syntax.md": dsl.split("Key patterns:")[0],
    }


def test_every_served_skeleton_omits_the_offset():
    skeletons = _skeletons()
    offenders = [
        f"{owner}: bar_offset={m.group(1)!r}"
        for owner, text in skeletons.items()
        for m in _DAILY_WITH_OFFSET.finditer(text)
    ]
    assert not offenders, "a served skeleton declares a daily offset:\n" + "\n".join(offenders)
    # Non-vacuity, on quantities the seed cannot move: four skeletons were
    # read and each IS a skeleton (a Globals line and the loader opening), so
    # a green is a verdict on real skeletons, not on text that lost them.
    assert len(skeletons) == 4
    not_skeletons = [
        owner
        for owner, text in skeletons.items()
        if "Globals(target_timeframe=" not in text or "PriceDataLoader()" not in text
    ]
    assert not not_skeletons, f"no skeleton found in: {not_skeletons}"
    # And, unseeded, each draws the bare daily Globals the rule names.
    assert all(_DAILY_BARE.search(text) for text in skeletons.values())


@pytest.mark.parametrize("owner", ["keel_strategy_compose"])
def test_the_compose_description_states_the_close(owner):
    """The skeleton's convention names the close it implies."""
    text = OUTCOMES[owner].listed_description or ""
    assert "00:00 UTC" in text
