"""The error / empty-result card guards (Q-1773).

On ChatGPT (``window.openai`` dialect) a tool error drew the EMPTY card —
"Untitled", four tiles SHARPE / RETURN / MAX DD / FILLS all "—", "+5 more",
and "Ask for the Keel link to view this in the app." — the exact state the
runbook forbids (B9). ``toolOutput`` is the result's ``structuredContent``,
and an error envelope carried none (``_mcp_adapter.view_tool_result``
returned the bare JSON string when there was no ``view``), so the card was
handed ``{}``. The strategy, live and preflight cards had no error state at
all on ANY host. Since Q-1785 the server ships every error envelope as
``structuredContent`` too; the ``server_arms`` tests drive that end to end.

``tests/fixtures/cards/c_result_states_check.mjs`` drives the real cards in
Chromium through both dialects. Skips (never silently passes) without node
or Playwright.

Proof it can fail: ``# SEED:`` per test, run 2026-09-22, recorded in the commit.
Proof it is not vacuous: the two CONTROL arms (a real result on each dialect)
render tiles and the fixture's own strategy name — so the harness does
deliver data, and the terminal lines below are the card's verdict, not an
empty harness.
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
CHECK = FIXTURES / "c_result_states_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"
ASK_FOR_LINK = "Ask for the Keel link"
NO_RESULT = "No result to show here — see the reply."


#: One real tool error per card kind that owns or borrows an error line,
#: produced by the REAL server (`create_server().call_tool`) through the
#: real `_mcp_adapter.view_tool_result` — never a hand-written envelope.
_SERVER_ERROR_CALLS = {
    "strategy": ("keel_strategy_get", {}),
    "backtest": ("keel_backtest_run", {}),
    "compare": ("keel_backtest_compare", {}),
}


def _server_error_arms() -> dict:
    """What ChatGPT hands each card for a real tool error (Q-1785).

    ChatGPT's `window.openai.toolOutput` is the result's
    `structuredContent` — and `{}` when the result carries none, which is
    exactly what the adapter shipped for every error envelope before."""
    import asyncio

    from keel.mcp.server import create_server

    async def go() -> dict:
        server = create_server()
        arms = {}
        for kind, (tool, args) in _SERVER_ERROR_CALLS.items():
            result = await server.call_tool(tool, args)
            texts = [block.text for block in result.content]
            arms[kind] = {
                "kind": kind,
                "toolInput": args,
                "toolOutput": result.structured_content or {},
                "texts": texts,
                "message": (result.structured_content or {}).get("message"),
            }
        return arms

    return asyncio.run(go())


@pytest.fixture(scope="module")
def server_arms() -> dict:
    return _server_error_arms()


def test_a_server_error_keeps_its_json_text_block_for_the_model(server_arms: dict) -> None:
    """claude.ai hides structuredContent from the model (Part C probe (e)),
    so the text block carries the WHOLE envelope — led, since agent-surface-
    cleanup spec 02 §2.2 (R-27), by the human `message` and the catalogue
    lines, then the envelope's JSON, byte-parseable, with no card preface —
    and the structured copy is the same object."""
    assert len(server_arms) == len(_SERVER_ERROR_CALLS) > 0
    for kind, arm in server_arms.items():
        assert len(arm["texts"]) == 1, kind
        text = arm["texts"][0]
        assert text.startswith(arm["message"]), kind
        envelope = json.loads(text[text.index("\n{") + 1 :])
        assert envelope["code"] == "usage_error", kind
        assert arm["toolOutput"] == envelope, kind


def test_a_real_server_error_reaches_the_chatgpt_card_in_its_own_words(states: dict) -> None:
    """The contract end to end: the real server's error result, handed to
    the card the way ChatGPT does (`toolOutput` = structuredContent),
    renders "<Kind> unavailable — <message>" — never the empty-result
    line that `{}` draws."""
    # SEED: in `view_tool_result`, return `envelope_json` when there is no
    # markdown (the pre-Q-1785 early return) — every arm draws
    # "No result to show here — see the reply." instead of its message.
    adapter = states["adapter"]
    assert set(adapter) == set(_SERVER_ERROR_CALLS)
    for kind, m in adapter.items():
        _terminal(m)
        message = states["serverMessages"][kind]
        assert message and message in m["text"], (kind, m["text"])
        assert NO_RESULT not in m["text"], (kind, m["text"])
    assert (
        "Strategy unavailable — " + states["serverMessages"]["strategy"]
        in (adapter["strategy"]["text"])
    )
    assert "Comparison unavailable — " in adapter["compare"]["text"]


@pytest.fixture(scope="module")
def states(tmp_path_factory, server_arms: dict) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; result states are DOM rules")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("state-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    adapter = cards / "adapter-arms.json"
    adapter.write_text(json.dumps(server_arms), encoding="utf-8")
    proc = subprocess.run(
        [
            "node",
            str(CHECK),
            "--cards",
            str(cards),
            "--fixtures",
            str(FIXTURES),
            "--adapter",
            str(adapter),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the result-states check produced no measurements.\n{proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    out["serverMessages"] = {kind: arm["message"] for kind, arm in server_arms.items()}
    return out


def _terminal(m: dict) -> None:
    """A terminal line: one row, no tiles, no skeleton, no action band, and
    never the empty-card tells."""
    assert m["errLine"], m
    assert m["tiles"] == 0, m
    assert not m["skeleton"], m
    assert not m["linkRowVisible"], m
    assert "Untitled" not in m["text"] and "—\n" not in m["text"], m
    assert ASK_FOR_LINK not in m["text"], m
    assert m["height"] < 64, m


def test_the_states_scan_was_not_vacuous(states: dict) -> None:
    for arm in ("openaiResult", "mcpResult"):
        assert states[arm]["tiles"] > 0, arm
        assert states["btName"] in states[arm]["text"], arm


def test_an_empty_chatgpt_result_is_an_honest_line(states: dict) -> None:
    """ChatGPT's `{}` for a result with no structuredContent: say there is
    nothing to show here, on every kind — never "Untitled" + dashes."""
    # SEED: delete the `isEmptyResult(envelope)` branch in the result
    # listener — the backtest arm draws "Untitled" + four "—" tiles again.
    for arm in ("openaiEmptyBacktest", "openaiEmptyCompare", "openaiEmptyStrategy"):
        m = states[arm]
        _terminal(m)
        assert m["text"] == NO_RESULT, (arm, m["text"])


def test_an_error_envelope_says_what_failed_on_every_dialect(states: dict) -> None:
    """The error's own message reaches the card — through ChatGPT's
    `toolOutput` (once the server ships it as structuredContent) and
    through MCP Apps' text block — on every card kind."""
    # SEED: in the result listener drop `CARDS_OWNING_ERRORS` handling
    # (let every error through to the card) — the strategy arms render an
    # empty structure instead of their message.
    run_msg = states["messages"]["runErr"]
    strat_msg = states["messages"]["stratErr"]
    for arm, msg in (
        ("openaiErrorBacktest", run_msg),
        ("mcpErrorBacktest", run_msg),
        ("openaiErrorStrategy", strat_msg),
        ("mcpErrorStrategy", strat_msg),
        ("mcpErrorCompare", states["cmpErrMessage"]),
    ):
        m = states[arm]
        _terminal(m)
        assert msg in m["text"], (arm, m["text"])
    assert "Strategy unavailable — " in states["mcpErrorStrategy"]["text"]


def test_the_card_owns_the_failure_lead(states: dict) -> None:
    """Q-1800: "Backtest not submitted — Cannot backtest — start_date …"
    said the failure twice. The card's lead stays; the server's own
    preamble (up to its first dash) goes — the rest of the message is
    verbatim."""
    # SEED: make `withoutFailureLead` return its input unchanged
    # (`return text;`) — both arms read "Cannot …" again and this reds.
    bt = states["openaiLeadBacktest"]
    _terminal(bt)
    assert (
        "Backtest not submitted — start_date 2025-06-01 is after end_date 2025-01-01."
        in (bt["text"])
    ), bt["text"]
    assert "Swap them to run 2025-01-01 to 2025-06-01." in bt["text"]
    assert "Cannot backtest" not in bt["text"], bt["text"]
    strat = states["mcpLeadStrategy"]
    _terminal(strat)
    assert "Strategy unavailable — Strategy str_nope was not found." in strat["text"]
    assert "Cannot" not in strat["text"], strat["text"]
    # Control: a message with no preamble is untouched (the existing arm).
    assert states["messages"]["runErr"] in states["openaiErrorBacktest"]["text"]


def test_a_framework_error_shows_its_own_words(states: dict) -> None:
    """An `isError` result whose text is not our JSON is still an error the
    card can name — not an object rendered as a result."""
    m = states["mcpIsErrorText"]
    _terminal(m)
    assert "Error executing tool keel_strategy_get: boom" in m["text"]
