"""Under CI the card checks' browser is present: their skips are local-only.

More than a dozen test modules here drive a card through real Chromium
(``tests/fixtures/cards/*.mjs``) and skip when node or
``services/keel-app/node_modules/playwright`` is absent, so a laptop without
keel-app's node_modules can still run the suite. But the SDK lanes
(sdk-test.yml, sdk-release.yml) are the ONLY lanes that run this suite, and
until 2026-11-04 they never installed playwright: every one of those checks
skipped in CI, and the two screener checks that do not skip failed with
"Cannot find module 'playwright'". A skip that happens in the only lane that
runs the test proves nothing.

The lanes now run ``infrastructure/ci/install_card_browser.sh``. This test is
what keeps that step there: under ``CI`` (GitHub sets it on every runner; the
same variable ``db.testing.disposable_pg.missing_dep`` keys on) a missing node,
playwright or Chromium FAILS the suite instead of letting those modules skip.

Proof it can fail: run under ``CI=1`` in a checkout with no
``services/keel-app/node_modules`` (a fresh ``git archive``) and it reds on the
playwright assertion; recorded in the commit that added it.
Proof it is not vacuous: ``test_every_card_harness_resolves_playwright_from_keel_app``
reads the harnesses and requires at least one, each resolving playwright from
the one place this test checks, so a harness moved to another root cannot slip
out from under the gate.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest


HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
KEEL_APP = REPO / "services" / "keel-app"
PLAYWRIGHT = KEEL_APP / "node_modules" / "playwright"
HARNESSES = sorted((HERE / "fixtures" / "cards").glob("*.mjs"))

_LAUNCH = """
const { createRequire } = require("node:module");
const req = createRequire(process.argv[1]);
const { chromium } = req("playwright");
(async () => {
  const browser = await chromium.launch();
  console.log(browser.version());
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_every_card_harness_resolves_playwright_from_keel_app():
    users = [
        p for p in HARNESSES if re.search(r"require\(\s*[\"']playwright[\"']\s*\)", p.read_text())
    ]
    assert users, f"no card harness under {HERE / 'fixtures' / 'cards'} requires playwright"
    for path in users:
        assert "services/keel-app/package.json" in path.read_text(), (
            f"{path.name} resolves playwright from somewhere test_card_browser does not check"
        )


def test_the_card_browser_is_installed_where_ci_runs_the_suite():
    if not os.environ.get("CI"):
        pytest.skip("local run: the card checks may skip here; under CI they may not")
    assert shutil.which("node"), (
        "node is not on PATH; run infrastructure/ci/install_card_browser.sh"
    )
    assert PLAYWRIGHT.exists(), (
        f"{PLAYWRIGHT} is absent, so every card check skips; "
        "run infrastructure/ci/install_card_browser.sh"
    )
    proc = subprocess.run(
        ["node", "-e", _LAUNCH, str(KEEL_APP / "package.json")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"Chromium did not launch: {proc.stderr[-2000:]}"
    assert proc.stdout.strip(), "Chromium launched but reported no version"
