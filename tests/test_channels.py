"""The channel contract (agent-surface-cleanup spec 02 §2.1–§2.2, §2.7).

G1 — the assignment is TOTAL and DISJOINT: every top-level key and every
`view.*` member of the recorded envelopes (the listed server's own results,
`tests/fixtures/channels/`) and of a real plan-wall refusal is claimed by
exactly one of structured ∪ ERROR_FIELDS / card / drop.

G3 — no double-send: under the non-view probe arm every non-view tool is ONE
text block with no `structuredContent` and publishes no `outputSchema`; with
the arm OFF (the default) the served surface is exactly today's.

G10 — the refusal keeps its facts on every host: the whole handoff envelope in
`structuredContent`, an empty card, and a text block led by the human message.

The `_meta["keel/card"]` move is probe-gated (`KEEL_CARD_META_MOVE`, default
OFF): both states are asserted here.

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* G1 — add `"curve"` to `_channels._BACKTEST.structured` as well as `card`:
  `test_the_assignment_is_disjoint` reds; the totality arm stays green.
* G3 — annotate the non-view handler `-> str` under the arm again
  (`registration_mode` returning "legacy" for "text"):
  `test_non_view_tools_are_one_text_block_under_the_arm` reds.
* G10 — the spec's seed (assign `limit_view` to the card) moves nothing,
  because an error envelope never partitions; the seed that reds the verdict
  is disabling the adapter's error branch (`if is_error_envelope(envelope):`
  → `if False:` in `view_tool_result`): both arms of
  `test_the_refusal_keeps_its_facts_on_every_host` red.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from unittest.mock import MagicMock

import pytest
from keel.errors import translate_http_error
from keel.tools.outcomes._channels import (
    CARD_META_ENV,
    CARD_META_KEY,
    CHANNEL_MAP,
    ERROR_FIELDS,
    NONVIEW_TEXT_ENV,
    partition,
    unassigned_paths,
)


HERE = pathlib.Path(__file__).resolve().parent
RECORDED = HERE / "fixtures" / "channels"

#: kind → the recorded envelope and the minimum top-level key count the
#: recording carries (spec 02 §4 G1's non-vacuity — a quantity no seed moves).
RECORDINGS = {
    "backtest": ("backtest_summarize.envelope.json", 24),
    "comparison": ("backtest_compare.envelope.json", 16),
    "strategy": ("strategy_get.envelope.json", 13),
}

WALL_403 = json.dumps(
    {
        "detail": "Plan limit reached: backtest_runs",
        "code": "quota_exhausted",
        "quota": {
            "unit": "backtest_runs",
            "label": "backtests",
            "limit": 50,
            "used": 50,
            "remaining": 0,
            "period": "weekly",
            "resets_at": "2026-09-28T00:00:00Z",
            "reset_epoch": 1790553600,
            "plan": "free",
            "higher_plans": [{"plan": "starter", "limit": 500, "period": "weekly"}],
        },
    }
)


def _recorded(kind: str) -> dict:
    name, _ = RECORDINGS[kind]
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))


def _refusal(monkeypatch) -> dict:
    """A REAL plan-wall refusal: the real handler's handoff envelope."""
    from keel.tools.outcomes import _bootstrap, get
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._handoff import HandoffRequired

    monkeypatch.setattr("keel.tools.outcomes._handoff._is_anon_session", lambda: False)
    _bootstrap()
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, WALL_403)
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io")
    with pytest.raises(HandoffRequired) as exc:
        get("keel_backtest_run").handler({"strategy_id": "str_x", "end_date": "2026-09-23"}, ctx)
    return exc.value.to_envelope()


# ── G1 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(RECORDINGS))
def test_the_recordings_are_not_vacuous(kind: str) -> None:
    _, minimum = RECORDINGS[kind]
    assert len(_recorded(kind)) >= minimum


@pytest.mark.parametrize("kind", sorted(RECORDINGS))
def test_the_assignment_is_total(kind: str) -> None:
    """Every field of the recorded envelope has a declared home."""
    assert unassigned_paths(_recorded(kind), kind) == []


@pytest.mark.parametrize("kind", sorted(CHANNEL_MAP))
def test_the_assignment_is_disjoint(kind: str) -> None:
    spec = CHANNEL_MAP[kind]
    homes = [spec.structured, spec.card, spec.drop]
    for i, a in enumerate(homes):
        for b in homes[i + 1 :]:
            assert not (a & b), (kind, sorted(a & b))
    # The error set is its own home: never also a success card/drop row.
    assert not (ERROR_FIELDS & (spec.card | spec.drop)), kind


def test_the_refusal_is_all_error_fields(monkeypatch) -> None:
    envelope = _refusal(monkeypatch)
    # Non-vacuity: the refusal carries its facts (Q-1806, Q-1807).
    assert len(ERROR_FIELDS & set(envelope)) >= 6
    assert set(envelope) <= ERROR_FIELDS, sorted(set(envelope) - ERROR_FIELDS)


# ── the move, both ways ───────────────────────────────────────────────


def test_with_the_arm_off_the_card_rows_stay_structured(monkeypatch) -> None:
    """DEFAULT (probe not run): nothing moves to `_meta`; only duplicates go."""
    monkeypatch.delenv(CARD_META_ENV, raising=False)
    envelope = _recorded("backtest")
    structured, card = partition(envelope, "backtest")
    assert card == {}
    assert "curve" in structured and "metrics_raw" in structured
    assert "tiles" in structured["view"]
    # The drop rows leave in both states — they are duplicates.
    assert "period" not in structured


def test_with_the_arm_on_the_card_rows_move_and_keep_their_paths(monkeypatch) -> None:
    monkeypatch.setenv(CARD_META_ENV, "1")
    envelope = _recorded("backtest")
    structured, card = partition(envelope, "backtest")
    assert "curve" not in structured and "metrics_raw" not in structured
    assert "render" not in structured and "results_url" not in structured
    assert "tiles" not in structured["view"] and "url_line" not in structured["view"]
    # G2's non-vacuity anchor: the recorded curve really is a series.
    assert len(card["curve"]["points"]) >= 200
    assert card["view"]["tiles"] == envelope["view"]["tiles"]
    # R2: the model's whole text is in structuredContent.
    assert structured["view"]["markdown"] == envelope["view"]["markdown"]
    # Budget (a) on the recorded fixture (spec 02 §2.3): ≤ 4,000 B.
    size = len(json.dumps(structured, separators=(",", ":")).encode())
    assert size <= 4_000, size


def test_a_comparison_moves_each_runs_curve(monkeypatch) -> None:
    monkeypatch.setenv(CARD_META_ENV, "1")
    envelope = _recorded("comparison")
    structured, card = partition(envelope, "comparison")
    assert all("curve" not in run for run in structured["view"]["runs"])
    assert len(card["view"]["runs"]) == len(envelope["view"]["runs"])
    for drop in ("run_a", "run_b", "performance", "cost_profile"):
        assert drop not in structured
    size = len(json.dumps(structured, separators=(",", ":")).encode())
    assert size <= 5_000, size


def _built_compare_with_holds() -> dict:
    """A REAL `keel_backtest_compare` envelope with hold lines (Q-2223): the
    recordings were made without `holds`, so they could not see the field."""
    from keel.tools.outcomes._base import ToolContext

    from tests.test_outcomes_backtest_compare import _compare_n

    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    env, _asked = _compare_n(ctx, 3, holds=("BTC", "SOL"))
    return env


def test_a_built_compare_with_holds_is_totally_assigned() -> None:
    env = _built_compare_with_holds()
    assert len(env["references"]) == 2  # non-vacuity: the field under test is there
    assert unassigned_paths(env, "comparison") == []


def test_hold_series_ride_the_card_and_their_numbers_stay_structured(monkeypatch) -> None:
    monkeypatch.setenv(CARD_META_ENV, "1")
    env = _built_compare_with_holds()
    structured, card = partition(env, "comparison")
    assert len(structured["references"]) == len(env["references"]) == 2
    for i, ref in enumerate(env["references"]):
        assert "series" not in structured["references"][i]
        assert structured["references"][i]["ret_pct"] == ref["ret_pct"]
        assert card["references"][i]["series"] == ref["series"]
        assert ref["series"]  # non-vacuity: a real series moved


def test_a_strategy_moves_its_graph_and_keeps_a_requested_source(monkeypatch) -> None:
    monkeypatch.setenv(CARD_META_ENV, "1")
    envelope = _recorded("strategy")
    structured, card = partition(envelope, "strategy")
    assert "structure" not in structured["view"] and "source" not in structured["view"]
    assert "graph" not in structured.get("metadata", {})
    assert card["view"]["structure"] == envelope["view"]["structure"]
    size = len(json.dumps(structured, separators=(",", ":")).encode())
    assert size <= 5_000, size
    # CONTROL: a dry run's own source is the model's (Q-1840).
    dry = json.loads((RECORDED / "compose_dry.envelope.json").read_text())
    dry_structured, _ = partition(dry, "strategy")
    assert "source" in dry_structured["view"]


# ── G10 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("arm", ["", "1"])
def test_the_refusal_keeps_its_facts_on_every_host(monkeypatch, arm: str) -> None:
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    monkeypatch.setenv(CARD_META_ENV, arm)
    envelope = _refusal(monkeypatch)
    # Non-vacuity: the resume is the blocked call, with its arguments.
    assert len(envelope["resume"]["verify_call"]["args"]) >= 2
    result = view_tool_result(json.dumps(envelope), "keel_backtest_run")
    structured = result.structured_content
    for key in ("limit_details", "limit_view", "resume", "code", "message"):
        assert key in structured, key
    assert structured == envelope
    assert (result.meta or {}).get(CARD_META_KEY, {}) == {}
    text = result.content[0].text
    assert text.startswith(envelope["message"])
    # The resume as the one `next` (spec 02 §2.4 #1(e), review 06 M-5 #5).
    assert "\nnext: " in text and "keel_backtest_run(" in text


# ── G3 ────────────────────────────────────────────────────────────────


def _served(monkeypatch, *, arm: str):
    from keel.mcp.server import create_server

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    monkeypatch.setenv(NONVIEW_TEXT_ENV, arm)
    server = create_server()
    return server, asyncio.run(server.list_tools())


def test_non_view_tools_are_one_text_block_under_the_arm(monkeypatch) -> None:
    from keel.tools.outcomes._strategy_view import VIEW_TOOLS
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    server, tools = _served(monkeypatch, arm="1")
    non_view = [t for t in tools if t.name not in VIEW_TOOLS and t.name != "keel_live_monitor"]
    # Non-vacuity (a count the seed cannot move): the listed surface.
    assert {t.name for t in tools} == LISTED_PROFILE_TOOLS
    assert len(non_view) >= 15
    for tool in non_view:
        schema = tool.to_mcp_tool().outputSchema
        assert schema is None, (tool.name, schema)
    result = asyncio.run(server.call_tool("keel_help", {"topic": "dsl_syntax"}))
    assert result.structured_content is None
    assert len(result.content) == 1
    # Status joins the view tools under the arm: its declared schema.
    (status,) = [t for t in tools if t.name == "keel_account_status"]
    assert status.to_mcp_tool().outputSchema["properties"]["capabilities"]


def test_with_the_arm_off_the_surface_is_todays(monkeypatch) -> None:
    """CONTROL: default OFF — non-view tools keep FastMCP's `{"result":
    string}` wrapper, exactly what an old connector's frozen schema holds."""
    from keel.tools.outcomes._strategy_view import VIEW_TOOLS

    server, tools = _served(monkeypatch, arm="")
    non_view = [t for t in tools if t.name not in VIEW_TOOLS and t.name != "keel_live_monitor"]
    assert len(non_view) >= 15
    for tool in non_view:
        assert tool.to_mcp_tool().outputSchema.get("x-fastmcp-wrap-result"), tool.name
    result = asyncio.run(server.call_tool("keel_help", {"topic": "dsl_syntax"}))
    assert result.structured_content == {"result": result.content[0].text}


def test_the_card_meta_arm_keeps_the_live_monitor_shape(monkeypatch) -> None:
    """Review 2 #2 (R-25): `KEEL_CARD_META_MOVE` switched the listed
    `keel_live_monitor` to a result with NO outputSchema and the bare
    envelope as `structuredContent` — while an old connector holds its
    frozen `{"result": string}` schema. The arm may move the series into
    `_meta["keel/card"]`; it may not change the tool's schema or shape.

    SEED (run 2026-09-23, reverted by reversing the edit): in
    `_mcp_adapter.live_result`, return `structured_content=structured` again —
    the shape arm reds; the schema arm stays green. A second seed, dropping
    `"live"` from the `-> str` modes in `_make_param_synthesized_handler`,
    reds the schema arm.
    """
    from keel.mcp.server import create_server
    from keel.tools.outcomes._mcp_adapter import registration_mode, result_for_mode

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    schemas = {}
    for arm in ("", "1"):
        monkeypatch.setenv(CARD_META_ENV, arm)
        tools = asyncio.run(create_server().list_tools())
        (live,) = [t for t in tools if t.name == "keel_live_monitor"]
        schemas[arm] = live.to_mcp_tool().outputSchema
    assert schemas[""] and schemas[""].get("x-fastmcp-wrap-result")
    assert schemas["1"] == schemas[""]

    monkeypatch.setenv(CARD_META_ENV, "1")
    envelope = {
        "deployment_id": "dep_x",
        "status": "running",
        "curve": {"points": [["2026-09-01T00:00:00Z", 1000.0], ["2026-09-02T00:00:00Z", 1010.0]]},
        "render": {"card": "live", "fallback_url": "https://app.usekeel.io/x"},
    }
    mode = registration_mode("keel_live_monitor")
    assert mode == "live"  # non-vacuous: the arm really reached the live path
    result = result_for_mode(mode, json.dumps(envelope), "keel_live_monitor")
    assert set(result.structured_content) == {"result"}
    inner = json.loads(result.structured_content["result"])
    assert inner["status"] == "running" and "curve" not in inner
    assert result.meta[CARD_META_KEY]["curve"] == envelope["curve"]
    # Q-1894: the text block (claude.ai's model's only carrier) is the
    # partitioned envelope too — the series lives in the card alone.
    assert result.content[0].text == result.structured_content["result"]
    assert "curve" not in json.loads(result.content[0].text)
