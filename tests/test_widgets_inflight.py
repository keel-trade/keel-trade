"""The in-flight card guards (Q-1769).

On an MCP Apps host (claude.ai) the card mounts when the tool call STARTS;
a waited ``keel_backtest_run`` returns 30 s+ later. The card drew a full
evidence skeleton, swapped it after a fixed 4 s for "Result did not arrive —
open in Keel." while the call was still running, then collapsed to the
one-row receipt when the result DID arrive. The founder: "those are not good
transitions, can we do that better".

Two layers of guard:

* ``test_the_inflight_table_*`` pin the adapter's ``INFLIGHT`` table — the
  card's prediction of how big each call's result will be — against the
  Python owners it mirrors: ``CARD_TOOLS``, ``TOOL_INVOCATION_STRINGS`` and
  every card tool's real ``input_schema``. They need no browser and run in CI.
* The DOM arms read samples taken by ``tests/fixtures/cards/c_inflight_check.mjs``,
  which drives the real cards through the real ``ui/initialize`` →
  ``tool-input`` → (wait past 4 s) → ``tool-result`` sequence in real
  Chromium. Like ``test_widgets_cadence.py`` they skip (never silently pass)
  when node or Playwright is absent, which includes the CI python lane.

Proof each guard can fail: every DOM test names its ``# SEED:``. The seed
that restores the old behaviour (the fixed 4 s ``showMiss`` regardless of a
call in flight) was run on 2026-09-22 and reverted by reversing the edit;
the result is recorded in the commit message.

Proof they are not vacuous: ``test_the_inflight_scan_was_not_vacuous``
requires every arm to have been sampled at every instant, the result arms to
show the FIXTURE's own strategy name after delivery (read off the JSON, not
typed twice), and the control arm to have really reached its miss.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest
from keel.tools.outcomes._channels import partition
from keel.widgets import CARD_KINDS, CARD_TOOLS, TOOL_INVOCATION_STRINGS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cards"
CHECK = FIXTURES / "c_inflight_check.mjs"
ADAPTER = HERE.parent / "keel" / "widgets" / "assets" / "host-adapter.js"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"

#: The terminal lines (Q-1883): a lost result, and a call the host stopped.
MISS = "No result reached this card."
STOPPED = "Stopped before a result came back."
#: Copy retired by Q-1883 — no card state may draw either, ever.
RETIRED = ("did not arrive", "Ask for the Keel link")
#: The receipt row's own measured height (BUILD §4.1: under 64 px).
RECEIPT_MAX_PX = 64


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ── The table: the card's prediction, pinned to its Python owners ───────────


def _inflight_table() -> dict:
    """Parse ``var INFLIGHT = {...};`` out of the served adapter.

    The literal is prettier-formatted JS (bare keys, trailing commas); the
    two rewrites below make it JSON. A table this parser cannot read fails
    the test rather than yielding an empty dict.
    """
    src = ADAPTER.read_text(encoding="utf-8")
    m = re.search(r"var INFLIGHT = (\{.*?\n  \});", src, re.S)
    assert m, "host-adapter.js no longer declares `var INFLIGHT = {...};`"
    body = re.sub(r"(?m)^(\s*)(\w+):", r'\1"\2":', m.group(1))
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


def _schemas() -> dict[str, dict]:
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    return {name: t.input_schema for name, t in OUTCOMES.items()}


def test_the_inflight_table_covers_exactly_the_card_tools() -> None:
    """Every tool that draws a card has a prediction, of the right kind, and
    nothing else does. A card tool missing from the table would fall back to
    its kind's candidates — an unannounced guess."""
    # SEED: delete the `keel_backtest_watch` entry from INFLIGHT — reds here.
    table = _inflight_table()
    assert len(table) == len(CARD_TOOLS) > 0
    assert {t: r["kind"] for t, r in table.items()} == CARD_TOOLS


def test_the_inflight_table_says_what_chatgpts_tool_row_says() -> None:
    """The in-flight row's words are the ChatGPT tool row's words
    (``openai/toolInvocation/invoking``), so both hosts say the same
    outcome-neutral thing while the call runs."""
    # SEED: change "Running backtest…" in INFLIGHT — reds here.
    table = _inflight_table()
    checked = 0
    for tool, rule in table.items():
        expected = TOOL_INVOCATION_STRINGS.get(tool, (None, None))[0]
        assert rule["invoking"] == expected, tool
        checked += expected is not None
    assert checked == len(TOOL_INVOCATION_STRINGS)


def test_the_inflight_table_matches_each_tools_real_schema() -> None:
    """`needs` is the tool's `required`; `present` is exactly "the tool takes
    `present`"; `dryRun` exactly "the tool takes `dry_run`". These are what
    let the card name the tool from its arguments on a host that sends no
    tool name (ChatGPT, and any MCP Apps host that omits `toolInfo`)."""
    # SEED: drop `present: true` from keel_strategy_fork — reds here.
    table = _inflight_table()
    schemas = _schemas()
    assert set(table) <= set(schemas), set(table) - set(schemas)
    for tool, rule in table.items():
        schema = schemas[tool]
        props = schema.get("properties", {})
        assert sorted(rule["needs"]) == sorted(schema.get("required", [])), tool
        assert bool(rule.get("present")) == ("present" in props), tool
        assert bool(rule.get("dryRun")) == ("dry_run" in props), tool


def test_the_inflight_default_size_is_the_servers_default() -> None:
    """The one size rule the server states as a function is checked against
    that function: a submitted run is a receipt unless `present="view"`."""
    from keel.tools.outcomes.backtest_run import _view_size

    table = _inflight_table()
    size = {"receipt": "receipt", "evidence": "view"}
    assert table["keel_backtest_run"]["size"] == size[_view_size({})]
    assert size[_view_size({"present": "view"})] == "view"
    # Reads, compare, live and the fork/save defaults are full views
    # (BUILD §2.4); only a dry run and the two run tools start as a row.
    receipts = sorted(t for t, r in table.items() if r["size"] == "receipt")
    assert receipts == ["keel_backtest_run", "keel_backtest_watch"]
    assert table["keel_strategy_compose"]["dryRun"] == "receipt"


def test_the_old_fixed_timeout_is_gone() -> None:
    """Source pin for the one line the DOM arms exist to keep out: the
    no-input grace may never fire while a call is in flight."""
    src = ADAPTER.read_text(encoding="utf-8")
    assert 'if (!envelopeDelivered && !callInFlight) showMiss("lost");' in src
    assert "}, 4000);" not in src
    assert "WIRE.toolCancelled" in src


# ── The rendered card over time ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def samples(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the in-flight rules are DOM rules")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("inflight-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    # The metadata arms deliver the evidence envelope exactly as the server
    # splits it with KEEL_CARD_META_MOVE on — through the real partition, so
    # the arms move with the channel map rather than a hand-made split.
    structured, card = partition(_fixture("c_backtest.envelope.json"), "backtest", move_card=True)
    assert card, "the partition moved nothing to _meta; the metadata arms are vacuous"
    split = cards / "split.json"
    split.write_text(json.dumps({"structured": structured, "card": card}), encoding="utf-8")
    results = cards / "results.json"
    results.write_text(json.dumps(_real_meta_move_results()), encoding="utf-8")
    proc = subprocess.run(
        [
            "node",
            str(CHECK),
            "--cards",
            str(cards),
            "--fixtures",
            str(FIXTURES),
            "--split",
            str(split),
            "--results",
            str(results),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the in-flight check produced no samples.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def _real_meta_move_results() -> dict:
    """A receipt-size run and a plan-limit refusal, each as the REAL
    `view_tool_result` returns it with KEEL_CARD_META_MOVE on: what ChatGPT
    hands the card as `toolOutput` (structuredContent) and
    `toolResponseMetadata` (`_meta`)."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    from tests.test_widgets_plan_limit import _real_envelope

    mp = pytest.MonkeyPatch()
    try:
        mp.setenv("KEEL_CARD_META_MOVE", "1")
        receipt = view_tool_result(
            (FIXTURES / "c_bt_receipt_completed.envelope.json").read_text(encoding="utf-8"),
            "keel_backtest_run",
        )
        limit = view_tool_result(json.dumps(_real_envelope(mp)), "keel_backtest_run")
    finally:
        mp.undo()
    # Not vacuous: the move really split the receipt, and the refusal is
    # the plan-limit state (its card channel empty by R-27).
    assert receipt.meta["keel/card"], "the receipt moved nothing to _meta"
    assert "curve" not in receipt.structured_content
    assert limit.structured_content["code"] == "handoff_required"
    assert limit.meta["keel/card"] == {}
    run_input = {"strategy_id": "str_x"}
    return {
        "metaMoveReceipt": {
            "kind": "backtest",
            "input": run_input,
            "toolOutput": receipt.structured_content,
            "meta": receipt.meta,
        },
        "metaMoveLimit": {
            "kind": "backtest",
            "input": run_input,
            "toolOutput": limit.structured_content,
            "meta": limit.meta,
        },
    }


def _at(samples: dict, arm: str, instant: str) -> dict:
    return samples[arm][str(samples["instants"][instant])]


def test_the_inflight_scan_was_not_vacuous(samples: dict) -> None:
    arms = {
        "runDefault": 3,
        "runNamed": 3,
        "noCall": 2,
        "presentView": 3,
        "cancelled": 2,
        "openai": 3,
        "composeDryRun": 1,
        "strategyGet": 1,
        "preInputBacktest": 1,
        "preInputCompare": 1,
        "openaiLateMeta300": 3,
        "openaiLateMeta1500": 3,
        "openaiMetaNever": 3,
        "openaiNoInput": 3,
        "mcpMetaAtomic": 2,
        "mcpNoMeta": 1,
        "openaiBridge": 4,
        "openaiBridgeCompose": 1,
        "openaiOutputNever": 2,
        "metaMoveReceipt": 2,
        "metaMoveLimit": 2,
    }
    for arm, n in arms.items():
        assert len(samples[arm]) == n, arm
    # The result really landed (after the old timeout) and was really read:
    # the name comes from the fixture, not from this file.
    receipt_name = _fixture("c_bt_receipt_completed.envelope.json")["strategy_name"]
    evidence_name = _fixture("c_backtest.envelope.json")["strategy_name"]
    for arm in ("runDefault", "runNamed", "openai"):
        assert receipt_name in _at(samples, arm, "AFTER")["text"], arm
    assert evidence_name in _at(samples, "presentView", "AFTER")["text"]
    assert samples["instants"]["LATE"] > 4000 < samples["instants"]["RESULT"]
    # The metadata arms read the fixture's own name once the result lands,
    # and the chart is the one thing only `_meta["keel/card"]` carries.
    for arm in ("openaiLateMeta300", "openaiLateMeta1500", "openaiNoInput"):
        last = samples[arm][max(samples[arm], key=int)]
        assert evidence_name in last["text"], arm
        assert last["chart"], arm
    assert samples["mcpMetaAtomic"]["950"]["chart"]


def test_a_call_in_flight_never_says_the_result_did_not_arrive(samples: dict) -> None:
    """(a) The founder's case. Past the old 4 s mark the call is still
    running, and the card says so — as the receipt row it will become."""
    # SEED: in host-adapter.js change
    # `if (!envelopeDelivered && !callInFlight) showMiss();` to
    # `if (!envelopeDelivered) showMiss();` — every LATE sample below turns
    # into the miss line and this reds on the first assertion.
    for arm in ("runDefault", "runNamed", "openai"):
        for instant in ("EARLY", "LATE"):
            s = _at(samples, arm, instant)
            assert MISS not in s["text"], (arm, instant)
            assert not any(r in s["text"] for r in RETIRED), (arm, instant)
            assert s["expect"] == "receipt", (arm, instant)
            assert s["liveVisible"], (arm, instant)
            assert "Running backtest…" in s["liveText"], (arm, instant)
            assert s["shapes"] == ["rr sk-live"], (arm, instant)
            assert s["height"] < RECEIPT_MAX_PX, (arm, instant)
            assert not s["headVisible"], (arm, instant)


def test_the_receipt_swaps_in_place(samples: dict) -> None:
    """(a) → result: the in-flight row and the receipt row are one shape,
    so the swap moves nothing. No full skeleton was ever drawn."""
    # SEED: in card.css delete `body[data-expect="receipt"] .keel-card {
    # padding … }` — the in-flight row sits in the full card padding and
    # the heights stop matching.
    for arm in ("runDefault", "runNamed", "openai"):
        late = _at(samples, arm, "LATE")
        after = _at(samples, arm, "AFTER")
        assert after["receiptRow"], arm
        assert after["expect"] is None and not after["liveVisible"], arm
        assert abs(after["height"] - late["height"]) <= 2, (arm, late["height"], after["height"])
        assert after["tiles"] == 0, arm


def test_the_wordmark_follows_the_host(samples: dict) -> None:
    """The in-flight row follows the receipt row's wordmark rule: the brand
    on MCP Apps hosts, none on ChatGPT (whose own tool row names the app)."""
    assert _at(samples, "runDefault", "LATE")["liveText"].startswith("Keel")
    assert not _at(samples, "openai", "LATE")["liveText"].startswith("Keel")


def test_no_call_still_gets_the_honest_miss(samples: dict) -> None:
    """(b) CONTROL. No arguments and no result: nothing is coming, and the
    card says so after the grace — the Q-1627 promise still holds."""
    early = _at(samples, "noCall", "EARLY")
    late = _at(samples, "noCall", "LATE")
    assert MISS not in early["text"]
    assert MISS in late["text"]
    assert not late["skeleton"] and late["ariaBusy"] is None
    # One plain line: no instruction to ask the agent for anything.
    assert not any(r in late["text"] for r in RETIRED), late["text"]
    assert late["linkRow"] == "", late["linkRow"]


def test_a_cancelled_call_is_a_terminal_miss(samples: dict) -> None:
    """(d) The host's `tool-cancelled` is the protocol's own terminal miss —
    it needs no grace period."""
    # SEED: delete the `WIRE.toolCancelled` branch in the message handler —
    # the card stays in flight and this reds.
    s = samples["cancelled"]
    early, after = s[str(samples["instants"]["EARLY"])], s["1400"]
    assert early["liveVisible"] and STOPPED not in early["text"]
    assert STOPPED in after["text"] and not after["skeleton"]


def test_a_view_call_waits_as_the_full_card_and_grows_once(samples: dict) -> None:
    """(c) `present="view"` resolves to the full evidence card, so the card
    waits as that shape — never as a row that would then jump."""
    # SEED: make `sizeFor` ignore `args.present` — the view arm waits as a
    # receipt row and this reds on `expect`.
    for instant in ("EARLY", "LATE"):
        s = _at(samples, "presentView", instant)
        assert s["expect"] == "view", instant
        assert MISS not in s["text"], instant
        assert any("sk-tiles" in c for c in s["shapes"]), (instant, s["shapes"])
        # Words, not only grey blocks (Q-1883): the status line takes the
        # title bar's place above the full shape.
        assert s["liveVisible"], instant
        assert s["liveText"] == "Running backtest…", (instant, s["liveText"])
        assert "sk-line sk-title" not in s["shapes"], instant
        assert s["height"] > 100, instant
    late = _at(samples, "presentView", "LATE")
    after = _at(samples, "presentView", "AFTER")
    assert after["tiles"] > 0
    assert after["height"] >= late["height"], "a view result may only grow"


def test_the_strategy_card_predicts_from_its_arguments(samples: dict) -> None:
    """(f) With no tool name, the arguments name the tool: `dry_run` is a
    compose preview (a receipt, carrying the name it was given); a bare
    `strategy_id` is a read (a view)."""
    dry = _at(samples, "composeDryRun", "EARLY")
    assert dry["expect"] == "receipt" and dry["liveVisible"]
    assert "Composing…" in dry["liveText"] and "trend_v2" in dry["liveText"]
    get = _at(samples, "strategyGet", "EARLY")
    assert get["expect"] == "view"
    assert any("sk-tiles" in c for c in get["shapes"])


def test_before_the_arguments_a_card_waits_as_its_smallest_shape(samples: dict) -> None:
    """(g) Before `tool-input` lands, a card that can become a receipt waits
    as one bare row — so nothing is ever drawn big first — while a card
    that is always full (a comparison) waits as its own table."""
    bt = _at(samples, "preInputBacktest", "EARLY")
    assert bt["expect"] == "receipt"
    assert bt["shapes"] == ["sk-line sk-row"]
    assert bt["height"] < RECEIPT_MAX_PX
    cmp_ = _at(samples, "preInputCompare", "EARLY")
    assert cmp_["expect"] == "view"
    assert any("sk-table" in c for c in cmp_["shapes"])


def test_a_view_skeleton_promises_the_same_four_tiles(samples: dict) -> None:
    """Q-1781: the skeleton's four tiles carry the labels the result will
    — the app's first four, in its order — so the view grows into the
    tiles it promised."""
    # SEED: delete the `labelSkeleton` IIFE in card-backtest.js — the
    # skeleton tiles go blank and this reds.
    labels = _at(samples, "presentView", "EARLY")["skLabels"]
    assert labels == ["Return", "Max drawdown", "Sharpe", "Win rate"], labels


# ── The metadata race and the argument-less ChatGPT call (Q-1883) ───────────


def _in_flight(s: dict) -> bool:
    return s["liveVisible"] and s["skeleton"] and s["ariaBusy"] == "true"


def test_chatgpt_with_no_arguments_waits_instead_of_saying_the_result_is_lost(
    samples: dict,
) -> None:
    """The founder's 2026-09-23 card. ChatGPT left `toolInput` null for the
    whole call, so the 4 s no-input grace read the running call as "no
    host" and drew the miss plus "Ask for the Keel link"; the result then
    replaced it. A ChatGPT frame exists only for a call: it waits."""
    # SEED: in host-adapter.js `readOpenAIGlobals` delete the final
    # `else startInFlight();` — at 4.7 s the card draws the miss line.
    s = samples["openaiNoInput"]
    for t in ("400", "4700"):
        assert _in_flight(s[t]), (t, s[t]["text"])
        assert MISS not in s[t]["text"], t
        assert not any(r in s[t]["text"] for r in RETIRED), t
        # It cannot know which backtest tool this is, so it names none.
        assert s[t]["liveText"] == "Working on the backtest…", s[t]["liveText"]
        assert s[t]["expect"] == "receipt", "no arguments: the smallest shape"
    done = s["5900"]
    assert not _in_flight(done) and done["chart"]


@pytest.mark.parametrize("arm", ["openaiLateMeta300", "openaiLateMeta1500"])
def test_a_result_waits_for_its_render_data(samples: dict, arm: str) -> None:
    """`toolOutput` lands; `toolResponseMetadata` 300 ms / 1.5 s later. Until
    it does, the card is the call in flight — never the miss, never a card
    drawn without the series its metadata carries — then renders whole."""
    # SEED: in `readOpenAIGlobals` make the hold unreachable
    # (`if (false && o.toolResponseMetadata === null …`) — the first sample
    # is a drawn card with no chart instead of the in-flight line.
    s = samples[arm]
    first, before, after = (s[t] for t in sorted(s, key=int))
    for x in (first, before):
        assert _in_flight(x), x["text"]
        assert x["liveText"] == "Running backtest…"
        assert not x["chart"] and x["tiles"] == 0
        assert MISS not in x["text"]
        assert not any(r in x["text"] for r in RETIRED)
    assert not _in_flight(after) and after["chart"] and after["tiles"] > 0
    assert after["height"] >= before["height"], "the wait may only grow"


def test_metadata_that_never_lands_ends_the_wait_after_the_grace(samples: dict) -> None:
    """CONTROL: the metadata never comes. Inside the 3 s grace the card
    still waits; past it the card draws what `structuredContent` carried —
    the designed degradation, no chart — and never the miss."""
    # SEED: set META_GRACE_MS to 15 * 60 * 1000 — the last sample is still
    # in flight and this reds.
    s = samples["openaiMetaNever"]
    first, inside, past = (s[t] for t in sorted(s, key=int))
    assert _in_flight(first) and _in_flight(inside)
    assert not _in_flight(past), past["text"]
    assert past["tiles"] > 0 and not past["chart"]
    assert MISS not in past["text"]


def test_mcp_apps_has_no_metadata_window(samples: dict) -> None:
    """MCP Apps: one `tool-result` carries `structuredContent` and `_meta`
    together, so the card draws on the notification — whole with `_meta`,
    structured-only without — and never waits for a second message."""
    before = samples["mcpMetaAtomic"]["500"]
    assert _in_flight(before) and before["liveText"] == "Running backtest…"
    atomic = samples["mcpMetaAtomic"]["950"]
    assert not _in_flight(atomic) and atomic["chart"]
    bare = samples["mcpNoMeta"]["950"]
    assert not _in_flight(bare) and bare["tiles"] > 0 and not bare["chart"]


def test_the_retired_copy_is_gone_from_the_adapter() -> None:
    """Source pin: neither retired line survives anywhere a card could draw
    it from."""
    src = ADAPTER.read_text(encoding="utf-8")
    assert "Result did not arrive" not in src
    assert "Ask for the Keel link to view this in the app." not in src


def test_chatgpt_hears_the_bridge_and_waits_as_the_card_it_becomes(samples: dict) -> None:
    """2026-09-23 live: on ChatGPT a compose save and a backtest run waited
    as a bare ~20 px bar with no words, then jumped to the full card. The
    card read only the `window.openai` globals, whose `toolInput` stayed
    null; ChatGPT's bridge is MCP Apps underneath, so the card now also
    shakes hands and takes the tool's name and arguments from it — and
    waits as the full card, worded."""
    # SEED: in `ready()` run `handshake()` only when `!isOpenAI` — both
    # arms wait as the argument-less receipt row and this reds on `expect`.
    run = samples["openaiBridge"]
    for t in ("600", "1400"):
        s = run[t]
        assert _in_flight(s) and s["expect"] == "view", (t, s["expect"])
        assert s["liveText"] == "Running backtest…", s["liveText"]
        assert s["height"] > 200, s["height"]
    compose = samples["openaiBridgeCompose"]["600"]
    assert _in_flight(compose) and compose["expect"] == "view", compose["expect"]
    assert "Composing…" in compose["liveText"] and "trend_v2" in compose["liveText"]
    assert compose["height"] > 150, compose["height"]
    assert run["1800"]["height"] >= run["1400"]["height"], "the wait may only grow"


def test_a_bridge_result_is_not_undone_by_a_later_bare_tool_output(samples: dict) -> None:
    """The bridge's `tool-result` carries `_meta` with `structuredContent`.
    A `toolOutput` global that lands afterwards WITHOUT its metadata must
    not re-render the card structured-only when the grace runs out."""
    # SEED: in `readOpenAIGlobals` drop the `bridgeResultSeen` branch — at
    # 5.2 s (past the 3 s grace after the 1.6 s global) the chart is gone.
    run = samples["openaiBridge"]
    assert run["1800"]["chart"] and not _in_flight(run["1800"])
    assert run["5200"]["chart"], run["5200"]["text"][:200]


def test_a_result_with_metadata_but_no_output_ends_on_its_empty_line(samples: dict) -> None:
    """The metadata only a finished result carries lands, and `toolOutput`
    never does. Inside the grace the card still waits; past it the card
    ends on the empty-result line — terminal, and never the retired miss."""
    # SEED: in `readOpenAIGlobals` delete the `toolResponseMetadata != null`
    # branch — the card waits as a running call and this reds on the last
    # sample.
    s = samples["openaiOutputNever"]
    first, past = (s[t] for t in sorted(s, key=int))
    assert _in_flight(first), first["text"]
    assert not _in_flight(past), past["text"]
    assert "No result to show here" in past["text"], past["text"]
    assert not any(r in past["text"] for r in RETIRED)


def test_chatgpt_draws_a_meta_move_receipt_and_plan_limit(samples: dict) -> None:
    """2026-09-23 sweep turn: receipts and plan-limit refusals were reported
    as blank frames on ChatGPT with KEEL_CARD_META_MOVE on. With the REAL
    split envelopes, output first and metadata 400 ms later, the card waits
    (never blank, never the miss) and then draws each from what it carries."""
    # SEED: in `ready()`'s result listener delete the `planLimitOf` branch —
    # the refusal draws the red error line and this reds on the headline.
    receipt = samples["metaMoveReceipt"]
    wait, done = (receipt[t] for t in sorted(receipt, key=int))
    assert _in_flight(wait) and wait["liveText"] == "Running backtest…"
    assert done["receiptRow"] and not _in_flight(done), done["text"]
    assert "Simple Momentum" in done["text"], done["text"]
    limit = samples["metaMoveLimit"]
    wait, done = (limit[t] for t in sorted(limit, key=int))
    assert _in_flight(wait)
    assert done["text"].startswith("Weekly backtests used"), done["text"][:120]
    assert done["height"] > 100 and not _in_flight(done)
    for s in (*receipt.values(), *limit.values()):
        assert s["text"].strip(), "a blank card"
        assert not any(r in s["text"] for r in RETIRED)
