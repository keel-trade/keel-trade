"""The replay-size guard (Q-1770).

Reopening a claude.ai conversation re-renders every card. The host frames
start at 150 px; receipts settled to 48 px only after an unrelated resize,
and a full ``keel_backtest_summarize`` card stayed clipped at 150 px (header,
tiles and "+5 more" visible; receipt line, chart and action row cut off). The
adapter posted ``size-changed`` before the replaying host listened, and then
deduplicated every later post of the same height — so nothing re-sent it.

``tests/fixtures/cards/c_replay_size_check.mjs`` drives the real cards in
Chromium against a host that delivers the result in the ``ui/initialize``
response (the replay shape) and IGNORES size messages until 600 ms after the
handshake. The frame starts at 150 px and moves only on an accepted message.
Skips (never silently passes) when node or Playwright is absent.

Proof it can fail: ``# SEED:`` below — run 2026-09-22, recorded in the commit.
Proof it is not vacuous: every replay arm really had posts IGNORED (the host
was deaf when the card first spoke) and its content really is taller than
the host's 150 px default for the full arms, so "frame == content" cannot be
satisfied by the default; the control arm never ignored anything.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
from keel.widgets import CARD_KINDS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cards"
CHECK = FIXTURES / "c_replay_size_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"
HOST_DEFAULT_PX = 150


@pytest.fixture(scope="module")
def arms(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; replay sizing is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("replay-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(cards), "--fixtures", str(FIXTURES)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the replay-size check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_replay_scan_was_not_vacuous(arms: dict) -> None:
    for name in ("replayFull", "replayReceipt", "replayStrategy"):
        assert arms[name]["ignored"] > 0, f"{name}: the host was never deaf — no replay modelled"
    for name in ("replayFull", "replayStrategy", "liveFull"):
        assert arms[name]["content"] > HOST_DEFAULT_PX, name
    assert arms["replayReceipt"]["content"] < 64
    assert arms["liveFull"]["ignored"] == 0


def test_a_replayed_card_is_sized_to_its_content(arms: dict) -> None:
    """The host that started listening late still ends at the content height
    — for a full card (the clipped summarize case), a receipt, and a strategy
    card."""
    # SEED: in host-adapter.js replace the `resendSize();` after the
    # handshake and at the end of `deliverEnvelope` with `scheduleSize();`
    # — the deaf host never hears the final size, the frame stays at 150 px.
    for name in ("replayFull", "replayReceipt", "replayStrategy"):
        a = arms[name]
        assert a["accepted"], f"{name}: the host never heard a size"
        assert a["accepted"][-1] == a["content"], (name, a)
        assert a["frameHeight"] == a["content"], (name, a)


def test_a_live_card_is_sized_to_its_content(arms: dict) -> None:
    """CONTROL: a host listening from the start is unaffected."""
    a = arms["liveFull"]
    assert a["accepted"][-1] == a["content"] == a["frameHeight"]


def test_grow_and_shrink_both_repost(arms: dict) -> None:
    """A second result on an open card re-sizes it in both directions."""
    grow, shrink = arms["grow"], arms["shrink"]
    assert grow["accepted"][-1] == grow["content"] > HOST_DEFAULT_PX
    assert shrink["accepted"][-1] == shrink["content"] < 64
    assert max(shrink["accepted"]) > shrink["content"], "the shrink really followed a taller card"
