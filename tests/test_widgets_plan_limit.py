"""The plan-limit card state (Q-1806).

Observed on ChatGPT (staging, 2026-09-23), `keel_backtest_run` at 50/50: the
card drew the red error line "Backtest not submitted — Plan limit reached on
backtests: 50 of 50 used this week. … Report the numbers in `example`, name
the reset if one is given, include `docs_url`, and do not retry." — a
directive to the model shown to the user, with no word about how to get more.

The handoff envelope now carries `limit_view` (human sentences, and — since
D-12, 2026-09-28 — NO link) and the host adapter draws it as a calm state on
every card kind.
``tests/fixtures/cards/c_plan_limit_check.mjs`` drives the real cards in
Chromium through both dialects with the envelope the REAL SDK path produced
(403 body → ``translate_http_error`` → ``keel_backtest_run`` handler →
``view_tool_result``). Skips (never silently passes) without node or
Playwright.

Proof it can fail (run 2026-09-23, recorded in the commit): ``# SEED:`` per
test. Proof it is not vacuous: the CONTROL arm (a plain error envelope)
still draws the red error line in the same harness, so "no error line" on
the limit arms is the adapter's verdict, not a harness that draws nothing.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest
from keel.errors import translate_http_error
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._handoff import HandoffRequired
from keel.widgets import CARD_KINDS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE / "fixtures" / "cards" / "c_plan_limit_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"
BILLING = "https://staging-app.tailf4d598.ts.net/settings?tab=billing&from=agent"

WALL_403 = json.dumps(
    {
        "title": "Forbidden",
        "status": 403,
        "detail": "You've used all 50 backtests included on this plan.",
        "code": "quota_exhausted",
        "quota": {
            "kind": "insufficient",
            "unit": "backtest_runs",
            "label": "backtests",
            "code": "quota_exhausted",
            "limit": 50,
            "used": 50,
            "remaining": 0,
            "period": "weekly",
            "reset_epoch": 1790553600,
            "plan": "free",
            "higher_plans": [
                {"plan": "starter", "limit": 500, "period": "weekly"},
                {"plan": "trader", "unlimited": True},
            ],
        },
    }
)

#: Words written for the model; none may be drawn on the card.
MODEL_DIRECTED = re.compile(
    r"`|\bexample\b|\bdocs_url\b|\blimit_details\b|\bdo not retry\b|\breport the\b",
    re.IGNORECASE,
)


def _real_envelope(monkeypatch) -> dict:
    """The envelope ChatGPT hands the card: the real handler's handoff,
    through the real `view_tool_result` (its structuredContent)."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.tailf4d598.ts.net")
    # A signed-in wall (the hosted surface is never anonymous); the local
    # machine's own claim state must not decide which wall this test draws.
    monkeypatch.setattr("keel.tools.outcomes._handoff._is_anon_session", lambda: False)
    _bootstrap()
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, WALL_403)
    ctx = ToolContext(api_client=client, app_url="https://staging-app.tailf4d598.ts.net")
    with pytest.raises(HandoffRequired) as exc:
        get("keel_backtest_run").handler({"strategy_id": "str_x", "end_date": "2026-09-23"}, ctx)
    result = view_tool_result(json.dumps(exc.value.to_envelope()), "keel_backtest_run")
    return result.structured_content


@pytest.fixture(scope="module")
def states(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the plan-limit state is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    mp = pytest.MonkeyPatch()
    try:
        envelope = _real_envelope(mp)
    finally:
        mp.undo()
    cards = tmp_path_factory.mktemp("plan-limit-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    env_file = cards / "envelope.json"
    env_file.write_text(json.dumps(envelope), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(cards), "--envelope", str(env_file)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the plan-limit check produced no measurements.\n{proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    out["envelope"] = envelope
    return out


def test_the_scan_is_not_vacuous(states: dict) -> None:
    """CONTROL: a plain error still draws the red error line in this harness."""
    ctrl = states["openaiError"]
    assert ctrl["errLine"] and not ctrl["planLimit"], ctrl
    assert ctrl["errColor"], ctrl


def test_a_plan_limit_is_a_calm_state_on_both_dialects(states: dict) -> None:
    # SEED: delete the `planLimitOf(envelope)` branch in the result listener
    # — the backtest arms draw "Backtest not submitted — …" in the red error
    # line again and `planLimit` is false.
    for arm in ("openaiLimit", "mcpLimit", "openaiLimitCompare"):
        m = states[arm]
        assert m["planLimit"] and not m["errLine"], (arm, m)
        assert "not submitted" not in m["text"] and "unavailable" not in m["text"], (arm, m)
        assert m["tiles"] == 0 and not m["linkRowVisible"], (arm, m)
    # Not the error colour: the control arm's red is a different colour.
    assert states["openaiLimit"]["headlineColor"] != states["openaiError"]["errColor"]


def test_the_card_draws_the_d12_facts_only(states: dict) -> None:
    """04 §4.4: headline, reset, the free plan's one plan sentence, and the
    "doesn't use this allowance" note — no other plan, no number of one."""
    for arm in ("openaiLimit", "mcpLimit"):
        text = states[arm]["text"]
        assert "Weekly backtests used — 50 of 50 on the Free plan" in text, text
        assert "They reset " in text, text
        assert "Plans are changed in the Keel web app." in text
        assert "Paid plans" not in text, text
        assert (
            "Composing, validating and reading existing results don't use this allowance." in text
        )
        # The server DID send higher_plans (non-vacuity: WALL_403 carries
        # them); none is drawn.
        for token in ("Higher plans", "Starter", "Trader", "500", "See plans"):
            assert token not in text, (arm, token, text)
    # SEED: append " Report the numbers in `example` and do not retry." to
    # `limit_view.note` in `maybe_quota_handoff` — this line reds.
    for arm in ("openaiLimit", "mcpLimit"):
        assert not MODEL_DIRECTED.search(states[arm]["text"]), states[arm]["text"]
    # No price, pitch verb, other plan or destination — the D-12 scan over
    # what is drawn.
    from keel.tools.outcomes._handoff import _FORBIDDEN_UPSELL_RE

    assert not _FORBIDDEN_UPSELL_RE.search(states["openaiLimit"]["text"])


def test_the_card_draws_no_link_on_either_dialect(states: dict) -> None:
    """D-12 (Q-2080): the plan-limit card draws NO link and no button — the
    "See plans in Keel ↗" button opened the billing tab, whose plan buttons
    start Stripe Checkout. Nothing opens on a click either.

    SEED (run 2026-09-28): in `renderPlanLimit`, append an `<a>` to the
    billing tab labelled "See plans in Keel ↗" — `links == 1` on both
    dialects and this test and the facts test red; the vacuity control and
    the calm-state test stay green. Reverted by reversing the edit."""
    envelope = states["envelope"]
    assert "link" not in (envelope.get("limit_view") or {}), envelope.get("limit_view")
    assert "action_url" not in envelope
    for arm in ("openaiLimit", "mcpLimit"):
        m = states[arm]
        assert m["links"] == 0, (arm, m)
        assert m["linkHref"] is None and m["linkLabel"] is None, (arm, m)
        assert not m.get("opened"), (arm, m)
        assert BILLING not in json.dumps(m) and "/settings" not in json.dumps(m), (arm, m)
