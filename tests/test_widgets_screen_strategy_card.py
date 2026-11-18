"""A screen drawn through the strategy card reads as a screen (Q-3639).

compose, get and fork draw a screen with the STRATEGY card. The founder's
claude.ai test read raw slot names (``funding_now``) where the screen's own
view names its columns, and a link row that said "Open strategy in Keel".
``tests/fixtures/cards/c_screen_strategy_card_check.mjs`` renders the real
compose envelopes, a screen and a strategy, under BOTH host adapters (the
full profile's ``host-adapter.js`` and the listed connector's
``host-adapter-listed.js``) in real Chromium and reads what the card shows.

Skips (never silently passes) when node or Playwright is absent, like every
card DOM check; ``tests/test_card_browser.py`` fails the suite under CI when
the browser is missing.

Proof it can fail (run 2026-11-05, each reversed by reversing the edit):

* ``isScreenView`` returning ``false`` in host-adapter.js: the full card's
  screen arm reds on "Open strategy in Keel ↗"; the listed card and both
  strategy arms stay green.
* the same in host-adapter-listed.js: the listed card's screen arm reds.
* card-strategy.js ``outputsOf`` drawing ``o.name`` again: both cards' screen
  arms red on ``funding_now``.

Proof it is not vacuous: each arm asserts the card drew a body and a link,
the strategy arm (the control) draws no outputs at all, and both adapters
were rendered.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from keel.widgets import build_card_html

from tests.test_strategy_view_screen import (
    LABELLED_UNITS,
    SCREEN,
    STRATEGY,
    _Client,
    _compose,
)


HERE = Path(__file__).resolve().parent
CHECK = HERE / "fixtures" / "cards" / "c_screen_strategy_card_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"


@pytest.fixture(scope="module")
def envelopes() -> dict:
    from keel.tools.outcomes import _bootstrap

    _bootstrap()
    screen = _compose(
        {"source": SCREEN, "name": "Funding now"},
        _Client(SCREEN, views={"revision": 2, "units": LABELLED_UNITS}),
    )
    strategy = _compose({"source": STRATEGY, "name": "S"}, _Client(STRATEGY))
    return {"screen": screen, "strategy": strategy}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, envelopes) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the card rules are DOM rules")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("screen-strategy-cards")
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("KEEL_SERVER_PROFILE", raising=False)
        (cards / "full.html").write_text(build_card_html("strategy"), encoding="utf-8")
        mp.setenv("KEEL_SERVER_PROFILE", "listed")
        (cards / "listed.html").write_text(build_card_html("strategy"), encoding="utf-8")
    env_file = cards / "envelopes.json"
    env_file.write_text(json.dumps(envelopes), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(cards), "--envelopes", str(env_file)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the screen card check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_two_adapters_really_differ(envelopes, monkeypatch) -> None:
    """Not vacuous: the listed card carries the listed adapter, the full card
    the full one, and the screen envelope names labelled outputs."""
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    full = build_card_html("strategy")
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    listed = build_card_html("strategy")
    assert full != listed
    assert [o.get("label") for o in envelopes["screen"]["view"]["outputs"]] == [
        "Funding now, a year",
        "1-day return",
    ]
    assert "outputs" not in envelopes["strategy"]["view"]


@pytest.mark.parametrize("card", ["full", "listed"])
def test_a_screen_card_names_its_outputs_by_label_and_opens_a_screen(rendered, card) -> None:
    m = rendered[card]["screen"]
    assert m["blocks"] > 10, m  # the card drew a body
    assert m["outputs"] == ["Funding now, a year", "1-day return"], m
    # The slot name stays one hover away, for the agent's next view edit.
    assert m["tooltips"] == ["funding_now", "ret_1d"], m
    # The label that already says "a year" carries no second unit.
    assert m["units"] == ["", "%"], m
    assert m["links"][0] == "Open screen in Keel ↗", m


@pytest.mark.parametrize("card", ["full", "listed"])
def test_a_strategy_card_is_unchanged(rendered, card) -> None:
    """The control arm: a strategy draws no outputs and keeps its label."""
    m = rendered[card]["strategy"]
    assert m["blocks"] > 10, m
    assert m["outputs"] == [], m
    assert m["links"][0] == "Open strategy in Keel ↗", m
