"""The strategy card's rendering guard (mcp-strategy-view M2).

The card's contract is about the RENDERED DOM — a block budget, a parallel
that collapses to a row naming its branches, marks on exactly the changed
blocks, nothing wider than the frame, no internal scroll — so asserting on
the served JS source would prove nothing about any of it. The real assertions
live in ``tests/fixtures/cards/strategy_card_check.mjs``, which drives the
card through the same ``ui/initialize`` → ``tool-result`` sequence the host
uses and measures the result; this module runs it and reports its failures.

**This file does not run in CI, and that is deliberate.** The python lane
executes inside the keel-runtime image, which has no node, so this skips on
every CI run — a guard that can only pass. What protects the contract in CI is
``services/keel-app/src/lib/__tests__/mcp-strategy-card.unit.test.ts``, which
drives the same card over the same fixtures in jsdom (keel-app's vitest job is
the CI lane with a DOM, which is also why ``mcp-card.unit.test.ts`` lives
there). The split is by what each environment can honestly answer: jsdom has
no layout engine, so the rules that need REAL layout — packed column counts,
rendered heights, horizontal overflow — can only be proven here, and this file
is the pre-push proof for them.

Skips (never silently passes) when node or Playwright is absent: the browser
comes from ``services/keel-app/node_modules``, which a bare Python checkout
does not have.

Proof it can fail (run 2026-09-20, both arms reverted by reversing the edit):

* ``INLINE_BUDGET = 12`` → ``999`` in card-strategy.js: red with
  ``budget: 36 blocks inline at 720`` plus six ``collapsed row omits branch``
  failures — the HRP's 45-block parallel stopped collapsing.
* ``.card-head[hidden]{display:none!important}`` → ``{display:flex}``: red on
  all four fixtures with ``the template brand row is still visible``. This
  arm was itself vacuous on its first draft (it keyed on the ``hidden``
  attribute, which the defect leaves set) and only became a real guard when
  it was rewritten to key on computed visibility. That rule moved to
  ``card.css`` with the series palette on 2026-09-22 (Q-1688) — every card
  that draws its own head or a receipt row needs it, not just this one — so
  the seed is now an edit to card.css rather than to card-strategy.js.

Proof it is not vacuous: the check script asserts the HRP fixture really is
the extreme case the rules were measured on — 55 blocks and a six-way top
parallel, both counted from the fixture JSON, quantities no seed in the
renderer can move — and refuses a render that produced fewer than 8 blocks
or a height under 120 px, so a card that rendered nothing cannot pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from keel.widgets import build_card_html


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cards"
CHECK = FIXTURES / "strategy_card_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the card's DOM contract needs a browser")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    card = tmp_path_factory.mktemp("card") / "strategy.html"
    card.write_text(build_card_html("strategy"), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--card", str(card), "--fixtures", str(FIXTURES)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[3],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the check script produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_card_renders_its_contract(rendered: dict) -> None:
    """Every assertion in the check script, reported as one verdict."""
    assert rendered["failures"] == [], "\n".join(rendered["failures"])


def test_the_render_was_not_vacuous(rendered: dict) -> None:
    """The fixture is the extreme case, and the card actually drew it.

    Counted from the fixture JSON, so seeding the renderer cannot move them.
    """
    assert rendered["hrp_total_blocks"] == 55
    assert rendered["hrp_branches"] == 6
    assert rendered["wide"]["blocks"] >= 8, "the card rendered almost nothing"
    assert rendered["wide"]["height"] > 120


def test_the_budget_bounds_a_55_block_pipeline(rendered: dict) -> None:
    """PLAN §4.4: 12 blocks inline, 8 under 480 px, the rest named."""
    assert rendered["wide"]["blocks"] <= 14
    assert rendered["narrow"]["blocks"] < rendered["wide"]["blocks"]
    assert any("more" in t for t in rendered["narrow"]["more"])
    assert rendered["wide"]["parRows"] >= 1, "the 45-block parallel did not collapse"


def test_the_change_view_marks_only_what_changed(rendered: dict) -> None:
    """One modified block, one added, nothing else (PLAN §4.3)."""
    assert rendered["change"]["marks"] == {"mod": 1, "add": 1, "rem": 0}
