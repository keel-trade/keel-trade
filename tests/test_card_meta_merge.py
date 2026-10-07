"""The card merges `_meta["keel/card"]` over `structuredContent` (spec 02 §3,
guard G11).

When the probe moves card-only data to `_meta["keel/card"]`, the card must
still draw it: `host-adapter.js` deep-merges it over the parsed envelope on
both dialects. A host that drops `_meta` leaves a card that still renders,
without the chart (the CONTROL).

SEED (run 2026-09-23, reverted by reversing the edit): make `parseToolResult`
ignore its card meta (`var card = null;`) — both `WithMeta` arms red while
the no-meta CONTROL stays green. (A partial seed that only drops the explicit
`meta` argument reds the ChatGPT arm alone — the MCP Apps arm reads
`result._meta`; each dialect has its own arm for that reason.)

Skips (never silently passes) without node or Playwright.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
from keel.widgets import CARD_KINDS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE / "fixtures" / "cards" / "c_card_meta_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"


@pytest.fixture(scope="module")
def merged(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the merge is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("meta-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(cards)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the meta-merge check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_series_is_not_vacuous(merged: dict) -> None:
    assert merged["points"] >= 200


def test_the_mcp_apps_card_draws_the_meta_curve(merged: dict) -> None:
    assert merged["mcpWithMeta"]["chart"], merged["mcpWithMeta"]["text"][:300]


def test_the_chatgpt_card_draws_the_meta_curve(merged: dict) -> None:
    assert merged["openaiWithMeta"]["chart"], merged["openaiWithMeta"]["text"][:300]


def test_the_host_objects_are_never_written(merged: dict) -> None:
    """Review 2 #3: the merge wrote INTO `window.openai.toolOutput`. A host
    that freezes it made the card throw under "use strict"; one that does
    not had its own object rewritten. The merge now works on a copy.

    SEED (run 2026-09-23, reverted by reversing the edit): in
    host-adapter.js `parseToolResult`, merge in place again
    (`mergeCard(env, card); return env;`) — the frozen arm draws no chart and
    the unfrozen arm reports its host object written; the MCP Apps arms and
    the no-meta CONTROL stay green.
    """
    assert merged["openaiWithMeta"]["hostOutputUntouched"] is True
    frozen = merged["openaiFrozen"]
    assert frozen["chart"], frozen["text"][:300]
    assert frozen["hostOutputUntouched"] is True


def test_without_meta_the_card_still_renders(merged: dict) -> None:
    """CONTROL: a host that drops `_meta` — no chart, but the card renders
    its window from `structuredContent` alone."""
    control = merged["mcpWithoutMeta"]
    assert not control["chart"]
    assert "Aug 19, 2024 – Sep 22, 2026" in control["text"]
