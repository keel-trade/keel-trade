"""The size-budget replay guard (spec 02 §2.3 / §4 G2, R-20).

What a host's model reads is bytes, and the budgets that bound them are
only real if something replays REAL results through the CURRENT code and
measures what comes out. This file does that:

* `fixtures/payloads/*.json` are raw MCP `tools/call` results recorded on
  the real staging LISTED server on 2026-09-23 (before any agent-surface-
  cleanup lane landed) by
  `keel-artifacts/projects/fable/agent-surface-cleanup/payload-measure-2026-09-23/measure.py`
  (the mcp-strategy-view script, re-run; SHA256SUMS beside it). Six are
  spec 02 §2.3's rows; `backtest_run.json` is the `keel_backtest_run`
  (wait) row, recorded the same way. One edit after recording:
  `backtest_summarize`'s presigned S3 `results_url` query (a temporary STS
  credential and signature) is replaced by `redacted=xxx…` of EQUAL length,
  so every byte count is unchanged and no credential is committed.
  `status.json` was RE-recorded the same way at 23:26Z (Q-1894): the
  pre-cleanup recording carried `workflow_routes`/`tools_visible`, which
  the listed server no longer emits, so it measured a result no host can
  receive. Its principal/org/user ids and the staging app host are
  replaced by zero-filled / `example.invalid` strings of EQUAL length.
* `_replay` recovers each recorded ENVELOPE, rebuilds it as a real
  `OutcomeResult`, and runs it through the real registered tool of a
  listed-profile `create_server()` (the handler is the only thing
  replaced), so `to_envelope`, the adapter's channel assignment and
  FastMCP's wrapping are all the code under test. It returns the three
  channels: `structuredContent`, the `content` text, and `_meta`. On the
  recording tree the replay was BYTE-EXACT against the recorded wire for
  all seven (structuredContent, text and `_meta`).
* Bytes follow measure.py's rule: compact JSON (`separators=(",", ":")`)
  for `structuredContent`, UTF-8 bytes of the text blocks for `content`.

**Two budget tables, two flag states.**

The §2.2 partition landed (L4) with the card-data MOVE behind the probe
flag `KEEL_CARD_META_MOVE` (spec 02 §2.7), default OFF. So the guard has
two arms, and each pins the flag explicitly rather than inheriting it:

* flag OFF (every host today) — `BUDGETS`, a ratchet: each channel's
  replayed bytes on the recorded fixture plus ~10%, so a regression that
  inflates a channel reds while copy-level drift does not.
* flag ON (staging only until the probe) — `SPEC_BUDGETS_A`, spec 02 §2.3
  column (a) verbatim, for every row the move governs (`CARD_META_ROWS`:
  the five view results). `components_search` meets its row only with the
  non-view text flag (`KEEL_NONVIEW_TEXT_ONLY`, probe arm 3) and is held
  there; `status` meets its row on the re-recorded fixture with both flags
  (its strict xfail came off with Q-1894).
* the STAGING flag set (Q-1894, founder "trim for model, keep for cards")
  — `HOST_BUDGETS`: what each HOST's model reads, per tool, with exactly
  the flags `infrastructure/helm/mcp-server/values-dev.yaml` sets (read
  from the file, so the test cannot drift from what staging runs), plus
  the prod pin that `values-prod.yaml` sets the same pair. claude.ai reads the
  text block alone; Codex reads `structuredContent` when present, else the
  text; ChatGPT reads `structuredContent` under a card and both carriers
  otherwise. `test_the_card_draws_the_replayed_curve_from_meta` is its
  non-vacuity: the REAL replayed summarize result renders its chart from
  `_meta` in both card dialects, and not without it.

Budget (b) (every conditional field present) needs synthetic all-fields
fixtures; none exist yet.

Proof it can fail (run 2026-09-23; reverted by reversing the edit):

  SEED (today's shape) — in `_mcp_adapter.view_tool_result`'s final
  `return ToolResult(...)`, send `text=envelope_json` instead of
  `text=text`: the model-visible text becomes the whole envelope again.
  ⇒ red: `test_every_channel_is_within_budget`, naming five rows —
    summarize content 21,418 > 860, compare 23,511 > 770, run
    19,371 > 640, strategy_get 12,761 > 720, compose 3,969 > 220.
  (A first seed at `text = markdown` reddened compare alone: every other
  view envelope carries an operational line, which rebuilds `text` on the
  next line. The seed must sit where the channel is assigned.)
  Control, green through the seed: `test_the_recorded_fixtures_are_real`
    and `test_the_replay_input_is_the_recorded_envelope`.

  SEED (spec G2's, flag ON; run 2026-09-23) — in `_channels._RESULT`, move
  `"metrics_raw"` from `card=` to `structured=`: summarize's
  structuredContent returns past 4,000 and
  `test_the_spec_02_budgets_hold_with_the_card_meta_move` reds, naming
  summarize 5,039 > 4,000 and run 4,953 > 4,000; the flag-OFF arm stays
  green through it (off, the card rows are in structuredContent either way).

Proof it is not vacuous: `test_the_recorded_fixtures_are_real` reads the
fixture FILES only — seven results, none an error envelope (a first
recording against the wrong org returned seven `not_found` envelopes of
~300 bytes, which every budget would have passed), and the summarize
curve carries 239 ≥ 200 points. No replay, no adapter, so no seed in the
code under test can move it. `test_every_channel_is_within_budget` also
asserts it replayed all seven.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import pathlib
from typing import Any, NamedTuple

import pytest


PAYLOADS = pathlib.Path(__file__).resolve().parent / "fixtures" / "payloads"

#: fixture → (tool, the arguments measure.py called it with).
RECORDED: dict[str, tuple[str, dict[str, Any]]] = {
    "backtest_summarize": (
        "keel_backtest_summarize",
        {"backtest_id": "btr_01m35gjzsyk9gwh3fgj1m7xsvr"},
    ),
    "backtest_compare": (
        "keel_backtest_compare",
        {"backtest_ids": ["btr_01m35gjzsyk9gwh3fgj1m7xsvr", "btr_01m35gne923525f2gsgfx03z8c"]},
    ),
    "backtest_run": (
        "keel_backtest_run",
        {"strategy_id": "str_01m35gh6y82737z2695nszb6ka", "wait": True},
    ),
    "strategy_get": ("keel_strategy_get", {"strategy_id": "str_01m35gh6y82737z2695nszb6ka"}),
    "compose_dry": ("keel_strategy_compose", {"source": "Pipeline()", "dry_run": True}),
    "status": ("keel_account_status", {}),
    "components_search": (
        "keel_components_search",
        {"query": "price above 50 day moving average bullish filter"},
    ),
}


class Budget(NamedTuple):
    structured: int
    content: int


#: Ceilings on THIS tree (see the module docstring). Replayed bytes on
#: 2026-09-23, pre-partition — summarize 20,320 / 777, compare 22,140 / 698,
#: run 18,291 / 579, strategy_get 12,091 / 646, compose 3,685 / 195,
#: components_search 6,036 / 5,516 — plus ~10%; status (re-recorded, Q-1894)
#: 2,842 / 516, the `{"result": string}` wrapper beside its markdown.
#: These stay the flag-OFF ratchet; the flag-ON arm is held to SPEC_BUDGETS_A.
#: Re-based 2026-09-23 (Q-1896, deliberate): the card preface grew by 71 B to
#: name what the card carries (name, links, ids), and a dry run is now
#: prefaced by DRAFT_CHECK_LINE (+40 B); replayed content then read summarize
#: 863, compare 784, run 665, strategy_get 732, compose 235 — the content
#: ceilings below are those plus ~10%.
BUDGETS: dict[str, Budget] = {
    "backtest_summarize": Budget(structured=22_400, content=950),
    "backtest_compare": Budget(structured=24_400, content=860),
    "backtest_run": Budget(structured=20_100, content=730),
    "strategy_get": Budget(structured=13_300, content=800),
    "compose_dry": Budget(structured=4_100, content=260),
    "status": Budget(structured=3_150, content=600),
    "components_search": Budget(structured=6_650, content=6_070),
}

#: Spec 02 §2.3 column (a) and the `content` budget column, verbatim
#: (compare at N = 2; components_search's structured budget is 0: its
#: result is text only after §2.2).
SPEC_BUDGETS_A: dict[str, Budget] = {
    "backtest_summarize": Budget(structured=4_000, content=3_000),
    "backtest_run": Budget(structured=4_000, content=3_000),
    "backtest_compare": Budget(structured=5_000, content=4_000),
    "strategy_get": Budget(structured=5_000, content=3_000),
    "compose_dry": Budget(structured=6_000, content=3_000),
    "status": Budget(structured=3_000, content=1_500),
    "components_search": Budget(structured=0, content=6_000),
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((PAYLOADS / f"{name}.json").read_text())


def _recorded_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    """The tool's own envelope inside a recorded result.

    A view tool's `structuredContent` IS the envelope; a `-> str` tool's
    is FastMCP's `{"result": "<json>"}` wrap of it.
    """
    structured = raw["structuredContent"]
    if set(structured) == {"result"} and isinstance(structured["result"], str):
        return json.loads(structured["result"])
    return structured


def _as_outcome(envelope: dict[str, Any]) -> Any:
    """Rebuild a real `OutcomeResult` whose `to_envelope()` is the envelope."""
    from keel.tools.outcomes._base import OutcomeResult

    contractual = ("run_id", "hero_url", "share_url", "summary_metrics", "resource_uri")
    return OutcomeResult(
        **{k: envelope[k] for k in contractual if k in envelope},
        extra={k: v for k, v in envelope.items() if k not in contractual and k != "url_line"},
    )


class Channels(NamedTuple):
    structured: Any
    content: str
    meta: Any


def _nbytes(value: Any) -> int:
    if value is None:
        return 0
    return len(json.dumps(value, separators=(",", ":")).encode())


def _replay(monkeypatch: pytest.MonkeyPatch, name: str) -> Channels:
    """One recorded result through the real listed-profile tool."""
    from keel.mcp.server import create_server
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()  # register every tool before replacing one handler
    tool_name, args = RECORDED[name]
    outcome = _as_outcome(_recorded_envelope(_load(name)))
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.setitem(
        OUTCOMES,
        tool_name,
        dataclasses.replace(OUTCOMES[tool_name], handler=lambda _args, _ctx: outcome),
    )

    async def call() -> Any:
        return await create_server().call_tool(tool_name, args)

    result = asyncio.run(call())
    text = "".join(getattr(block, "text", "") for block in result.content)
    return Channels(result.structured_content, text, getattr(result, "meta", None))


# ── non-vacuity: the fixture files alone ───────────────────────────────


def test_the_recorded_fixtures_are_real() -> None:
    assert sorted(p.stem for p in PAYLOADS.glob("*.json")) == sorted(RECORDED)
    for name in RECORDED:
        raw = _load(name)
        envelope = _recorded_envelope(raw)
        assert raw.get("isError") is False, name
        assert "code" not in envelope, f"{name} is an error envelope: {envelope.get('message')}"
        assert _nbytes(raw["structuredContent"]) > 1_000, name
    curve = _recorded_envelope(_load("backtest_summarize"))["curve"]
    assert len(curve["points"]) >= 200  # 239 recorded (spec 02 §4 G2)


def test_the_replay_input_is_the_recorded_envelope() -> None:
    """The rebuilt OutcomeResult serializes back to the recorded envelope,
    so the replay measures the code, not a lossy reconstruction."""
    for name in RECORDED:
        envelope = _recorded_envelope(_load(name))
        assert _as_outcome(envelope).to_envelope() == envelope, name


# ── the guard ──────────────────────────────────────────────────────────

CARD_META_ENV = "KEEL_CARD_META_MOVE"
NONVIEW_TEXT_ENV = "KEEL_NONVIEW_TEXT_ONLY"

#: The rows the `_meta["keel/card"]` move governs (view results).
CARD_META_ROWS = (
    "backtest_summarize",
    "backtest_run",
    "backtest_compare",
    "strategy_get",
    "compose_dry",
)


def _flags(monkeypatch: pytest.MonkeyPatch, *, card_meta: bool, nonview_text: bool) -> None:
    for env, on in ((CARD_META_ENV, card_meta), (NONVIEW_TEXT_ENV, nonview_text)):
        if on:
            monkeypatch.setenv(env, "1")
        else:
            monkeypatch.delenv(env, raising=False)


def _over(monkeypatch: pytest.MonkeyPatch, rows: dict[str, Budget]) -> tuple[list[str], dict]:
    over: list[str] = []
    replayed: dict[str, Channels] = {}
    for name, budget in rows.items():
        channels = _replay(monkeypatch, name)
        replayed[name] = channels
        structured = _nbytes(channels.structured)
        content = len(channels.content.encode())
        assert content > 0, f"{name}: the replay produced no text"
        if structured > budget.structured:
            over.append(f"{name}: structuredContent {structured} > {budget.structured}")
        if content > budget.content:
            over.append(f"{name}: content {content} > {budget.content}")
    return over, replayed


def test_every_channel_is_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag OFF — today's shape on every host."""
    _flags(monkeypatch, card_meta=False, nonview_text=False)
    over, replayed = _over(monkeypatch, BUDGETS)
    assert len(replayed) == len(RECORDED) == 7
    # Off means off: no card key rides `_meta`.
    for name in CARD_META_ROWS:
        assert "keel/card" not in (replayed[name].meta or {}), name
    assert not over, "\n".join(over)


def test_the_spec_02_budgets_hold_with_the_card_meta_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON — spec 02 §2.3 column (a) for every view row the move governs."""
    _flags(monkeypatch, card_meta=True, nonview_text=False)
    over, replayed = _over(monkeypatch, {n: SPEC_BUDGETS_A[n] for n in CARD_META_ROWS})
    # Non-vacuous on a quantity the seed cannot move: the card data really
    # moved (it is in `_meta`, not dropped) for every governed row.
    for name in CARD_META_ROWS:
        card = (replayed[name].meta or {}).get("keel/card")
        assert card, f"{name}: flag ON but no _meta['keel/card']"
    assert len(replayed["backtest_summarize"].meta["keel/card"]["curve"]["points"]) >= 200
    assert not over, "\n".join(over)


def test_components_search_meets_its_budget_as_text_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe arm 3 ON — the non-view row is one text block, no structuredContent."""
    _flags(monkeypatch, card_meta=True, nonview_text=True)
    over, replayed = _over(monkeypatch, {"components_search": SPEC_BUDGETS_A["components_search"]})
    assert replayed["components_search"].structured is None
    assert not over, "\n".join(over)


def test_the_status_spec_budget_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Status as a view tool (probe arm 3) on the re-recorded fixture: 2,479 B
    structured / 516 B text against 3,000 / 1,500 (Q-1894 lifted the strict
    xfail the pre-cleanup recording had carried)."""
    _flags(monkeypatch, card_meta=True, nonview_text=True)
    over, _ = _over(monkeypatch, {"status": SPEC_BUDGETS_A["status"]})
    assert not over, "\n".join(over)


# ── the staging flag set: what each host's MODEL reads (Q-1894) ────────

REPO = pathlib.Path(__file__).resolve().parents[4]
HELM = REPO / "infrastructure" / "helm" / "mcp-server"


class HostBudget(NamedTuple):
    """Model-visible bytes per host. claude.ai: the text block alone (Part C
    probe (e)). Codex: `structuredContent` when present, else the text
    (codex #10334). ChatGPT: `structuredContent` when a card renders the
    result, both carriers otherwise (spec 02 §2.1, §2.7 step 5)."""

    claude: int
    chatgpt: int
    codex: int


#: Replayed under the staging flags on 2026-09-23 (Q-1894), text / structured:
#: summarize 777 / 3,216 · run 579 / 3,138 · compare 698 / 3,629 ·
#: strategy_get 646 / 3,083 · compose (dry, source requested) 195 / 2,222 ·
#: status 516 / 2,479 · components_search 3,658 / none. Ceilings, founder's
#: "summarize model copy ≤ ~4 KB" the anchor for the view rows.
HOST_BUDGETS: dict[str, HostBudget] = {
    "backtest_summarize": HostBudget(claude=1_000, chatgpt=4_000, codex=4_000),
    "backtest_run": HostBudget(claude=1_000, chatgpt=4_000, codex=4_000),
    "backtest_compare": HostBudget(claude=1_000, chatgpt=4_000, codex=4_000),
    "strategy_get": HostBudget(claude=1_000, chatgpt=3_500, codex=3_500),
    "compose_dry": HostBudget(claude=1_000, chatgpt=2_600, codex=2_600),
    "status": HostBudget(claude=1_500, chatgpt=3_500, codex=3_000),
    "components_search": HostBudget(claude=6_000, chatgpt=6_000, codex=6_000),
}


def _helm_env(name: str) -> dict[str, str]:
    """`env:` of one mcp-server values file, name → literal value."""
    yaml = pytest.importorskip("yaml")
    path = HELM / name
    if not path.exists():
        pytest.skip(f"{path} is not in this checkout (the SDK's standalone mirror)")
    values = yaml.safe_load(path.read_text()) or {}
    return {
        e["name"]: str(e["value"])
        for e in values.get("env") or []
        if isinstance(e, dict) and "value" in e
    }


def _staging_flags() -> dict[str, bool]:
    from keel.tools.outcomes._channels import _TRUE

    env = _helm_env("values-dev.yaml")
    return {
        flag: env.get(flag, "").strip().lower() in _TRUE
        for flag in (CARD_META_ENV, NONVIEW_TEXT_ENV)
    }


def _host_copies(tool_name: str, channels: Channels) -> HostBudget:
    from keel.widgets import card_kind_for_tool

    text = len(channels.content.encode())
    if channels.structured is None:
        return HostBudget(text, text, text)
    structured = _nbytes(channels.structured)
    carded = card_kind_for_tool(tool_name) is not None
    return HostBudget(
        claude=text,
        chatgpt=structured if carded else structured + text,
        codex=structured,
    )


def test_staging_and_prod_run_both_trims() -> None:
    """The flags the host budgets below are measured under are the flags the
    staging chart sets — and prod runs the same pair (founder, R5 2026-09-23:
    proven on both hosts, fresh and pre-batch connectors, so they ship).

    SEED (run 2026-09-24, reverted by reversing the edit): removing
    `KEEL_CARD_META_MOVE` from values-prod.yaml's env → red; removing
    `KEEL_NONVIEW_TEXT_ONLY` from values-dev.yaml → red.
    """
    assert _staging_flags() == {CARD_META_ENV: True, NONVIEW_TEXT_ENV: True}
    prod = _helm_env("values-prod.yaml")
    assert len(prod) >= 5  # non-vacuous: the prod env list was really read
    assert prod.get(CARD_META_ENV) == "1" and prod.get(NONVIEW_TEXT_ENV) == "1"


def test_the_model_copy_is_lean_on_every_host_under_the_staging_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Founder, 2026-09-23: "trim for model, keep for cards". Every recorded
    row, replayed under the staging flag set, per host.

    SEED (run 2026-09-23, reverted by reversing the edit): in
    `_channels._BACKTEST`, move `"curve"` from `card=` back to `structured=`
    → red, naming summarize chatgpt/codex 14,221 > 4,000 and run
    14,086 > 4,000; every other row stays inside its budget, and
    `test_the_recorded_fixtures_are_real` (the fixture files alone) stays
    green. `test_the_card_draws_the_replayed_curve_from_meta` reds too, on
    its control ("the chart came from structuredContent, not _meta").
    SEED 2 (same run): drop `| _STRATEGY_METADATA_PLUMBING` from
    `_channels._STRATEGY.card` → red, strategy_get chatgpt/codex
    3,814 > 3,500, every other row green.
    """
    flags = _staging_flags()
    _flags(monkeypatch, card_meta=flags[CARD_META_ENV], nonview_text=flags[NONVIEW_TEXT_ENV])
    over: list[str] = []
    replayed = 0
    for name, budget in HOST_BUDGETS.items():
        channels = _replay(monkeypatch, name)
        replayed += 1
        assert channels.content, f"{name}: the replay produced no text"
        copies = _host_copies(RECORDED[name][0], channels)
        for host, ceiling, got in zip(HostBudget._fields, budget, copies, strict=True):
            if got > ceiling:
                over.append(f"{name}: {host} reads {got} > {ceiling}")
        if RECORDED[name][0] not in _view_tools():
            # One carrier for a non-view result: no `{"result": string}` copy.
            assert channels.structured is None, name
    assert replayed == len(RECORDED) == 7
    assert not over, "\n".join(over)


def _view_tools() -> frozenset[str]:
    from keel.tools.outcomes._strategy_view import VIEW_TOOLS

    return frozenset(VIEW_TOOLS) | {"keel_account_status"}


def test_the_card_draws_the_replayed_curve_from_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Keep for cards: the REAL summarize result the staging flags produce —
    the channels above, not a synthetic envelope — draws its equity chart
    from `_meta["keel/card"]` on both card dialects (MCP Apps notification
    `_meta`; ChatGPT `toolResponseMetadata`). CONTROL: the same result with
    `_meta` removed mounts and names the run but draws no chart, so the
    chart is the `_meta` series and nothing else.
    Skips (never silently passes) without node or Playwright.
    """
    import shutil
    import subprocess

    from keel.widgets import CARD_KINDS, build_card_html

    playwright = REPO / "services" / "keel-app" / "node_modules" / "playwright"
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the card render is a DOM rule")
    if not playwright.exists():
        pytest.skip(f"Playwright is not installed at {playwright}")
    flags = _staging_flags()
    _flags(monkeypatch, card_meta=flags[CARD_META_ENV], nonview_text=flags[NONVIEW_TEXT_ENV])
    channels = _replay(monkeypatch, "backtest_summarize")
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            {
                "content": [{"type": "text", "text": channels.content}],
                "structuredContent": channels.structured,
                "_meta": channels.meta,
            }
        )
    )
    cards = tmp_path / "cards"
    cards.mkdir()
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    check = pathlib.Path(__file__).resolve().parent / "fixtures" / "cards" / "c_card_meta_check.mjs"
    proc = subprocess.run(
        ["node", str(check), "--cards", str(cards), "--result", str(result)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO,
    )
    if not proc.stdout.strip():
        pytest.fail(f"the card check produced no measurements.\n{proc.stderr[-2000:]}")
    replay = json.loads(proc.stdout)["replay"]
    name = _recorded_envelope(_load("backtest_summarize"))["view"]["name"]
    control = replay["mcpWithoutMeta"]
    assert name in control["text"], control["text"][:300]  # the card mounted
    assert not control["chart"], "the chart came from structuredContent, not _meta"
    assert replay["metaPoints"] >= 200
    assert replay["mcp"]["chart"], replay["mcp"]["text"][:300]
    assert replay["openai"]["chart"], replay["openai"]["text"][:300]
