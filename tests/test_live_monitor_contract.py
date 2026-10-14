"""`keel_live_monitor` answers the view it was asked for, or refuses (Q-2270).

Before Q-2270 an explicit per-deployment view with no `deployment_id`
(`view="positions"`) fell into the portfolio branch and came back as
`view: portfolio`; the `missing_deployment_id` refusal below it was
unreachable. Driven over the real FastMCP wire on the hosted listed server,
`KeelClient.get` faked.

Seeds (run 2026-10-01, each reverted by reversing the edit):
* restore the old branch condition (`view == "portfolio" or deployment_id in
  ("", "all")`) — the refusal arms red (the portfolio is read instead); the
  no-argument and explicit-portfolio controls stay green.
* delete the `_TRADE_SORTS` refusal loop from `_handler` — the two unknown
  sort arms red (the value is forwarded); the known-value arm stays green.

Trade-history `sort_by` / `sort_dir` are closed vocabularies (keel-api's
`list_trades` sorted an unknown value by time, descending, without a word):
declared as enums and refused here when unknown.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.tools.outcomes import _bootstrap


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)


@contextmanager
def _hosted_listed():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
    token = bind_request_credentials(token="tok_test", api_url=API)
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


#: Per-request headers each faked read received (S06 §3.5.1: the
#: agent-telemetry marker rides the `headers` keyword, never the query).
HEADERS_SEEN: list = []


def _monitor(args: dict) -> tuple[list, str]:
    calls: list = []
    HEADERS_SEEN.clear()

    def get(self, path, *, headers=None, **params):
        calls.append((path, params))
        HEADERS_SEEN.append((path, headers))
        return {"items": [], "data": [], "points": []}

    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool("keel_live_monitor", args, raise_on_error=False)

    with _hosted_listed(), mock.patch("keel.client.KeelClient.get", get):
        result = asyncio.run(run())
    return calls, "\n".join(getattr(c, "text", "") for c in result.content)


@pytest.mark.parametrize(
    "args",
    [
        {"view": "positions"},
        {"view": "trade_history"},
        {"view": "overview"},
        {"view": "positions", "deployment_id": "all"},
    ],
)
def test_a_per_deployment_view_without_a_deployment_is_refused(args):
    calls, text = _monitor(args)
    assert calls == [], calls  # nothing read, the portfolio least of all
    assert "missing_deployment_id" in text, text
    assert args["view"] in text


@pytest.mark.parametrize("args", [{}, {"view": "portfolio"}, {"deployment_id": "all"}])
def test_the_portfolio_answers_no_arguments_or_the_portfolio_view(args):
    """Control: the documented portfolio cases still read the summary."""
    calls, text = _monitor(args)
    assert [c[0] for c in calls] == ["/v1/deployments/portfolio/summary"]
    assert json.loads(text)["view"] == "portfolio"


def test_a_per_deployment_view_with_a_deployment_reads_that_view():
    calls, text = _monitor({"view": "positions", "deployment_id": "dep_1"})
    # The detail row first, once: the one payload carrying the run's class
    # (QA B1); then the view asked for.
    assert [c[0] for c in calls] == ["/v1/deployments/dep_1", "/v1/deployments/dep_1/positions"]
    assert json.loads(text)["view"] == "positions"


@pytest.mark.parametrize("bad", [{"sort_by": "pnl"}, {"sort_dir": "sideways"}])
def test_an_unknown_trade_sort_is_refused_before_anything_is_read(bad):
    calls, text = _monitor({"deployment_id": "dep_1", "view": "trade_history", **bad})
    assert calls == [], calls
    # The published-enum front door (round 5) refuses it as a usage_error.
    assert "usage_error" in text and next(iter(bad.values())) in text


def test_the_declared_trade_sorts_are_forwarded_exactly():
    calls, _ = _monitor(
        {
            "deployment_id": "dep_1",
            "view": "trade_history",
            "sort_by": "closed_pnl",
            "sort_dir": "asc",
        }
    )
    assert calls == [
        ("/v1/deployments/dep_1", {}),
        ("/v1/deployments/dep_1/trades", {"sort_by": "closed_pnl", "sort_dir": "asc"}),
    ]


def test_the_trade_sorts_are_declared_enums_on_the_listed_schema():
    from keel.mcp.server import create_server

    with _hosted_listed():
        tools = asyncio.run(create_server().list_tools())
    props = next(t for t in tools if t.name == "keel_live_monitor").parameters["properties"]
    assert props["sort_by"]["enum"] == ["trade_time", "notional", "closed_pnl"]
    assert props["sort_dir"]["enum"] == ["asc", "desc"]


# ── The portfolio names its class (paper-trading-v2 M2-sdk-portfolio-real) ──
#
# S06 §3.5.5 / S01 §3.10 (R-71): the portfolio summary sums one named class,
# and every client Keel ships asks for the one it means, so the view sends
# `capital_class=real` before keel-api reads an absent class as `real`. The
# read changes; what the caller sees does not — on the listed connector, the
# full profile and the CLI the output equals the base commit's
# (`fixtures/listed_outputs_head.json`, `fixtures/portfolio_outputs_head.json`).
#
# Seeds (each applied by an exact reversible edit, then reversed):
# * drop `params["capital_class"] = "real"` from `_handler` ⇒ every request
#   arm reds while every output arm stays green — which is why the request is
#   asserted, not inferred from the output;
# * send `capital_class="practice"` instead ⇒ the request arms red the same way.
# Count: six portfolio calls (two per surface) each compared to a non-empty
# base-commit output, and exactly one keel-api read per call.

_REAL_SUMMARY_READ = [
    {
        "method": "GET",
        "path": "/v1/deployments/portfolio/summary",
        "params": [["capital_class", "real"]],
    }
]
#: The full profile and the CLI read the `practice` summary too, for the
#: portfolio's paper section (M8-portfolio-paper, S06 §3.5.5, AC-06-12). The
#: base-commit world runs no paper, so the faked keel-api answers it with the
#: empty summary and the output stays the base commit's.
_FULL_SUMMARY_READS = [
    *_REAL_SUMMARY_READ,
    {
        "method": "GET",
        "path": "/v1/deployments/portfolio/summary",
        "params": [["capital_class", "practice"]],
    },
]


@pytest.mark.parametrize("args", [{}, {"view": "portfolio"}, {"deployment_id": "all"}])
def test_the_portfolio_read_names_the_real_class(args):
    calls, _ = _monitor(args)
    assert calls == [("/v1/deployments/portfolio/summary", {"capital_class": "real"})]


def _without_requests(output: dict) -> dict:
    return {k: v for k, v in output.items() if k != "requests"}


@pytest.mark.parametrize("name", ["live_monitor:no_args", "live_monitor:portfolio"])
def test_the_listed_portfolio_output_is_the_base_commits(name):
    from tests._listed_head_fixtures import OUTPUTS_FIXTURE, call_listed, load_world

    head = next(o for o in json.loads(OUTPUTS_FIXTURE.read_text())["outputs"] if o["name"] == name)
    assert head["text"] and head["structured"], "empty base output; this arm proves nothing"
    got = call_listed(head["tool"], head["args"], load_world())
    assert got["requests"] == _REAL_SUMMARY_READ
    assert _without_requests(got) == _without_requests(
        {k: head[k] for k in ("structured", "text", "meta", "is_error", "requests")}
    )


@pytest.mark.parametrize("name", ["full:no_args", "full:portfolio", "cli:no_args", "cli:portfolio"])
def test_the_full_and_cli_portfolio_output_is_the_base_commits(name):
    from tests._listed_head_fixtures import load_world
    from tests._portfolio_head import PORTFOLIO_FIXTURE, cli_call, full_call

    head = next(
        o for o in json.loads(PORTFOLIO_FIXTURE.read_text())["outputs"] if o["name"] == name
    )
    if name.startswith("full:"):
        got = full_call(head["args"], load_world())
        assert head["text"], "empty base output; this arm proves nothing"
        keys = ("structured", "text", "meta", "is_error")
    else:
        got = cli_call(head["argv"], load_world())
        assert head["output"] and head["exit_code"] == 0, "empty base output"
        keys = ("output", "exit_code")
    assert got["requests"] == _FULL_SUMMARY_READS
    assert {k: got[k] for k in keys} == {k: head[k] for k in keys}


def test_the_listed_surface_is_unchanged():
    """The class rides the request only: the served listed surface (tools,
    instructions, cards) still equals the base commit's."""
    from tests._listed_head_fixtures import BASE_COMMIT, SURFACE_FIXTURE, listed_surface_snapshot

    head = json.loads(SURFACE_FIXTURE.read_text())
    assert head["tool_count"] == 29 and head["card_resources"]
    assert {"base_commit": BASE_COMMIT, **listed_surface_snapshot()} == head


# ── The venue contract (paper-trading-v2 M8-monitor-venue; S06 §3.5.1-3.5.3, AC-06-11) ──
#
# Driven over the real FastMCP wire on a full-profile local server (API key),
# KeelClient.get faked per path. A practice overview relays venue, health,
# health_detail and the parity block byte-verbatim and is framed "Paper run
# (simulated)", never "Live"; a real row's response is today's plus the venue
# block (health null relayed, no label, no health line). The overview's
# detail read carries no telemetry header (the owner visit); a positions read
# carries it. keel_deployments_list forwards capital_class=practice and
# nothing else new (the parameter is the held paper form of the tool).
#
# Seed (AC-06-11): derive the label from `status` instead of
# `venue.capital_class` (`"Live" if status == "LIVE"`) ⇒ the practice arm
# renders "Live" and reds. Count: the practice fixture's four relayed fields
# are non-null; the real fixture's diff against today's response is exactly
# the `venue` key.

_PRACTICE_ROW = {
    "deployment_id": "dep_paper",
    "strategy_id": "str_x",
    "name": "Hype carry",
    "status": "LIVE",
    "schedule": "0 */4 * * *",
    "venue": {"code": "PAPER", "capital_class": "practice"},
    "health": "degraded",
    "health_detail": {
        "reason": "consecutive_missing",
        "consecutive_missing": 2,
        "last_complete_at": "2026-10-11T08:00:00Z",
        "since": "2026-10-11T12:00:00Z",
    },
    "parity": {
        "residual_bps": 4.2,
        "status": "tracking",
        "window_start": "2026-10-01T00:00:00Z",
        "window_end": "2026-10-11T00:00:00Z",
        "as_of": "2026-10-11T00:00:00Z",
        "eval_count": 12,
        "meter_eligible": True,
        "day0_backtest_run_id": "btr_day0",
    },
}
_REAL_ROW_TODAY = {
    "deployment_id": "dep_live",
    "strategy_id": "str_y",
    "name": "Momentum",
    "status": "LIVE",
    "schedule": "0 * * * *",
    "health": None,
    "health_detail": None,
}
_REAL_ROW = {**_REAL_ROW_TODAY, "venue": {"code": "HYPERLIQUID", "capital_class": "real"}}


@contextmanager
def _full_local():
    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_API_KEY="keel_venue_contract", KEEL_API_URL=API)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _full_call(tool: str, args: dict, routes: dict) -> tuple[list, str, dict]:
    """(reads as (path, params, headers), text block, envelope) on a full local server."""
    reads: list = []

    def get(self, path, *, headers=None, **params):
        reads.append((path, params, headers))
        return routes.get(path, {"points": []})

    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    with _full_local(), mock.patch("keel.client.KeelClient.get", get):
        result = asyncio.run(run())
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    structured = result.structured_content
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        structured = json.loads(structured["result"])
    return reads, text, structured if isinstance(structured, dict) else json.loads(text)


#: "Live" as a label, in either casing a label takes: title case ("Live") or
#: the raw status ("LIVE"). Lowercase "live" is prose ("going live", "live
#: prices") or a machine token (`"card": "live"`), never a label (QA V2).
_STANDALONE_LIVE_RE = re.compile(r"\b(?:Live|LIVE)\b")


def _overview(row: dict) -> tuple[list, str, dict]:
    return _full_call(
        "keel_live_monitor",
        {"deployment_id": row["deployment_id"]},
        {f"/v1/deployments/{row['deployment_id']}": row},
    )


def test_a_practice_overview_relays_the_venue_contract_under_paper_labels():
    reads, text, envelope = _overview(_PRACTICE_ROW)
    data = envelope["data"]
    relayed = ("venue", "health", "health_detail", "parity")
    assert all(_PRACTICE_ROW[k] is not None for k in relayed)  # non-vacuity
    for key in relayed:
        assert json.dumps(data[key], sort_keys=True) == json.dumps(
            _PRACTICE_ROW[key], sort_keys=True
        ), key
    assert envelope["label"] == "Paper run (simulated)"
    assert "Paper run (simulated)" in text
    # The run's status in keel-app's words, never the raw LIVE (QA W2, V2).
    assert _PRACTICE_ROW["status"] == "LIVE" and data["status"] == "Paper · degraded"
    assert not _STANDALONE_LIVE_RE.search(text), "a practice render path emitted 'Live'"
    # The detail read is the owner visit: no header; the enrichment reads
    # carry the agent-telemetry marker.
    assert reads[0] == ("/v1/deployments/dep_paper", {}, None)
    assert all(h == {"X-Keel-Agent-Telemetry": "1"} for _, _, h in reads[1:]) and reads[1:]


def test_a_real_overview_is_todays_response_plus_the_venue_block():
    _, _, today = _overview(_REAL_ROW_TODAY)
    _, text, now = _overview(_REAL_ROW)
    assert set(now["data"]) - set(today["data"]) == {"venue"}
    assert {k: v for k, v in now["data"].items() if k != "venue"} == today["data"]
    assert {k: v for k, v in now.items() if k != "data"} == {
        k: v for k, v in today.items() if k != "data"
    }
    assert now["data"]["health"] is None  # relayed, null included (R-42)
    assert "label" not in now and "health_line" not in now
    assert "Paper run" not in text


def test_a_positions_read_carries_the_telemetry_marker():
    reads, _, _ = _full_call(
        "keel_live_monitor", {"deployment_id": "dep_paper", "view": "positions"}, {}
    )
    # The class guard read of the detail row is never an owner visit either.
    marker = {"X-Keel-Agent-Telemetry": "1"}
    assert reads == [
        ("/v1/deployments/dep_paper", {}, marker),
        ("/v1/deployments/dep_paper/positions", {}, marker),
    ]


# ── Every view of a paper run is labelled and links to /paper/ (QA B1, W5) ──
#
# keel-api serves the venue block on the detail row only
# (`DeploymentResponse.venue`, services/keel-api/src/schemas/live.py:393); no
# per-view payload carries it. The monitor learns the class with one guard
# read of the detail row, so on the full profile every view of a practice
# run is framed "Paper run (simulated)" and links to its /paper/{id} page on
# the same tab (S06 §3.2.3, §3.5.2; PT2-3), and a real run's views carry no
# label and keep their /live/ link. The per-view payload here carries no
# venue, as keel-api serves it.
#
# Seeds (each by an exact reversible edit, then reversed):
# * build the hero link with kind "live" unconditionally ⇒ every practice
#   arm reds on its /live/ link; the real control stays green;
# * make `_read_run_class` return None ⇒ every practice arm reds (no label,
#   /live/ link); the real control stays green.
# Count: the ten non-overview views and the overview, each a practice run.

_PER_VIEW = (
    "positions",
    "equity",
    "pnl",
    "stats",
    "weights",
    "weights-history",
    "executions",
    "orders",
    "trades",
    "funding",
)


def _view_call(row: dict, view: str) -> tuple[list, str, dict]:
    dep = row["deployment_id"]
    # The view's own payload carries no venue block (keel-api's shape).
    return _full_call(
        "keel_live_monitor",
        {"deployment_id": dep, "view": view},
        {f"/v1/deployments/{dep}": row},
    )


@pytest.mark.parametrize("view", [*_PER_VIEW, "overview"])
def test_every_view_of_a_paper_run_is_labelled_and_links_to_paper(view):
    reads, text, envelope = _view_call(_PRACTICE_ROW, view)
    assert envelope["view"] == view
    assert envelope["label"] == "Paper run (simulated)"
    assert envelope["hero_url"].endswith(f"/paper/dep_paper?tab={view}"), envelope["hero_url"]
    assert envelope["render"]["fallback_url"] == envelope["hero_url"]
    assert "/live/" not in text and "/live/" not in json.dumps(envelope)
    # One detail read per call: the overview's own, or the guard read.
    assert [r[0] for r in reads].count("/v1/deployments/dep_paper") == 1


@pytest.mark.parametrize("view", _PER_VIEW)
def test_every_view_of_a_real_run_keeps_its_live_link_and_no_label(view):
    """CONTROL: a real run's view is unlabelled and links to /live/."""
    _, text, envelope = _view_call(_REAL_ROW, view)
    assert "label" not in envelope and "Paper run" not in text
    assert envelope["hero_url"].endswith(f"/live/dep_live?tab={view}"), envelope["hero_url"]


def test_deployments_list_forwards_the_class_and_nothing_else_new(paper_sdk_released):
    rows = {"/v1/deployments": {"data": [], "pagination": {}}}
    held_reads, _, _ = _full_call("keel_deployments_list", {}, rows)
    reads, _, _ = _full_call("keel_deployments_list", {"capital_class": "practice"}, rows)
    assert len(reads) == len(held_reads) == 1
    path, params, headers = reads[0]
    assert path == "/v1/deployments"
    # A caller that names no class gets `all` (M8-client-class, AC-06-30);
    # beside the class, nothing else new rides the request.
    assert held_reads[0][1]["capital_class"] == "all"
    assert {k: v for k, v in params.items() if k != "capital_class"} == {
        k: v for k, v in held_reads[0][1].items() if k != "capital_class"
    }
    assert params["capital_class"] == "practice"
    assert headers is None


def test_the_monitor_description_touch_up_ships_released(paper_sdk_released):
    """§3.5.7's sentence ships with the released paper SDK (§3.11 item 1, PT2-91)."""
    from keel.tools.outcomes import get as get_tool
    from keel.tools.outcomes._mcp_adapter import effective_description

    sentence = "Results include simulated (paper) runs, labelled by `venue`."
    with _full_local():
        tool = get_tool("keel_live_monitor")
        assert effective_description(tool).endswith(sentence)
        assert effective_description(tool).startswith(tool.description)


# ── The graduation envelope (paper-trading-v2 M8-monitor-graduation; S06 §3.5.4, AC-06-10) ──
#
# A practice detail row's IC-03-5 `graduation` is embedded byte-equal; exactly
# when `ready` is true the response gains `graduation_next` with an
# action_url (the mint's handoff_url on full/CLI, the run's /paper/{id} page
# when the mint returns None and always on listed), the keep-paper line and
# no time-remaining or expiry text. Seed (AC-06-10): emit graduation_next
# without checking `ready` ⇒ the ready=false arm reds. Count: both fixtures
# carry a non-null graduation with all four criteria.


def _graduation(ready: bool) -> dict:
    return {
        "ready": ready,
        "ready_since": "2026-10-20T00:00:00Z" if ready else None,
        "nudged_at": None,
        "evaluated_at": "2026-10-23T02:10:00Z",
        "record": {
            "started_at": "2026-10-01T00:00:00Z",
            "commit_id": "cmt_4",
            "commit_seq": 4,
            "commit_changed_at": None,
        },
        "thresholds": {
            "min_age_days": 21,
            "min_evals": 20,
            "parity_band_bps": 50,
            "backtest_max_dd_pct": 8.1,
            "dd_floor_pending": False,
            "defaults_version": 1,
        },
        "criteria": {
            "age": {"met": ready, "days": 22 if ready else 14, "required_days": 21},
            "evals": {"met": True, "count": 27, "required": 20},
            "parity": {
                "met": True,
                "residual_bps": 12.4,
                "band_bps": 50,
                "as_of": "2026-10-23T02:14:09Z",
                "eval_count": 27,
                "meter_eligible": True,
                "stale": False,
            },
            "drawdown": {"met": True, "paper_max_dd_pct": 3.2, "backtest_max_dd_pct": 8.1},
        },
        "carry_over": {
            "strategy_id": "str_x",
            "commit_id": "cmt_4",
            "schedule": "0 */4 * * *",
            "source_paper_deployment_id": "dep_paper",
            "head_commit_id": "cmt_6",
            "commit_ahead": True,
            "commit_seq": 4,
            "head_commit_seq": 6,
        },
    }


_URGENCY_RE = re.compile(
    r"\b(left|expir\w*|countdown|deadline|hurry|before it|only \d+|time remaining)\b",
    re.IGNORECASE,
)


def _graduation_overview(ready: bool, mint: dict | Exception | None):
    row = {**_PRACTICE_ROW, "graduation": _graduation(ready)}
    posts: list = []

    def post(self, path, json=None, *, headers=None, **params):
        posts.append((path, json))
        if isinstance(mint, Exception):
            raise mint
        return mint

    with mock.patch("keel.client.KeelClient.post", post):
        reads, text, envelope = _overview(row)
    return row, posts, envelope


def test_a_not_ready_graduation_is_embedded_and_proposes_nothing():
    row, posts, envelope = _graduation_overview(False, None)
    assert all(row["graduation"]["criteria"][k] for k in ("age", "evals", "parity", "drawdown"))
    assert json.dumps(envelope["graduation"], sort_keys=True) == json.dumps(
        row["graduation"], sort_keys=True
    )
    assert "graduation_next" not in envelope
    assert posts == []  # nothing minted for a run that is not ready


def test_a_ready_graduation_proposes_the_web_step_through_the_minted_link():
    from keel.tools.outcomes import _paper_strings as ps

    handoff = "https://app.usekeel.io/deploy/intent/int_1"
    row, posts, envelope = _graduation_overview(True, {"handoff_url": handoff})
    assert all(row["graduation"]["criteria"][k] for k in ("age", "evals", "parity", "drawdown"))
    nxt = envelope["graduation_next"]
    assert nxt["action_url"] == handoff
    assert posts == [("/v1/deployments/deploy-intents", {"strategy_id": "str_x"})]
    points = nxt["talking_points"]
    assert ps.GRADUATION_KEEP_PAPER_LINE.full in points
    assert ps.GRADUATION_LIVE_CALL_LINE.full in points
    assert "your paper run proved commit 4; the strategy has moved ahead" in points
    assert not [p for p in points if _URGENCY_RE.search(p)]
    assert json.dumps(envelope["graduation"], sort_keys=True) == json.dumps(
        row["graduation"], sort_keys=True
    )


def test_a_ready_graduation_falls_back_to_the_paper_page_when_nothing_is_minted():
    from keel.errors import KeelError

    _, _, envelope = _graduation_overview(True, KeelError("mint refused"))
    assert envelope["graduation_next"]["action_url"].endswith("/paper/dep_paper")


def test_listed_with_the_flag_on_links_the_paper_page(paper_sdk_released, monkeypatch):
    """On listed the mint returns None by policy, so the URL is /paper/{id}
    and the talking points take their listed forms."""
    from keel.tools.outcomes import _paper_strings as ps
    from keel.tools.outcomes import get as get_tool
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    client = mock.MagicMock()
    client.get.side_effect = lambda path, **kw: (
        {**_PRACTICE_ROW, "graduation": _graduation(True)}
        if path == "/v1/deployments/dep_paper"
        else {"points": []}
    )
    env = (
        get_tool("keel_live_monitor")
        .handler(
            {"deployment_id": "dep_paper"},
            ToolContext(api_client=client, app_url="https://app.usekeel.io"),
        )
        .to_envelope()
    )
    nxt = env["graduation_next"]
    assert nxt["action_url"] == "https://app.usekeel.io/paper/dep_paper"
    assert ps.GRADUATION_LIVE_CALL_LINE.listed in nxt["talking_points"]
    assert not any("proved commit" in p for p in nxt["talking_points"])  # carry-over: full only
    client.post.assert_not_called()


# ── The portfolio's paper section (paper-trading-v2 M8-portfolio-paper; S06 §3.5.5, AC-06-12) ──
#
# The summary sums one named class (S01 §3.10, R-71), so the portfolio view
# makes two explicit reads, `real` for the live summary and `practice` for a
# separate `paper` section, and never sums one into the other (GOAL
# invariant 3). The `paper` object is the practice summary verbatim, beside its
# "Paper (simulated)" label; an empty practice summary omits both. The full
# profile and listed with the flag on read both; listed with the flag off reads
# `real` only and renders HEAD (R-60).
#
# The spec's illustrative `paper` keys (`running_count`, `paused_count`,
# `total_simulated_value`) are not keel-api's: the practice summary is the same
# `PortfolioSummaryResponse` the live one is (S01 §3.10), and S05 reads its
# "Simulated Value" off `total_account_value`. The section relays it verbatim,
# as AC-06-12 states, so the simulated value is the practice summary's
# `total_account_value`.
#
# Seed (AC-06-12): add the practice summary's `total_account_value` (the
# simulated value) into the live total in `_handler` ⇒ the byte-identical
# live-totals arm reds. Count: the recorded reads number two (full, listed with
# the flag on) and one (listed, flag off), and the practice fixture holds one
# deployment row.

_SUMMARY_PATH = "/v1/deployments/portfolio/summary"
_TELEMETRY = {"X-Keel-Agent-Telemetry": "1"}
_LIVE_SUMMARY = {
    "active_count": 1,
    "capital_class": "real",
    "deployments": [{**_REAL_ROW_TODAY, "total_pnl": 412.37, "position_count": 3}],
    "total_account_value": 10412.37,
    "total_count": 1,
    "total_fees": 21.85,
    "total_funding": -12.4,
    "total_realized_pnl": 388.12,
    "total_unrealized_pnl": 24.25,
}
_PRACTICE_SUMMARY = {
    "active_count": 1,
    "capital_class": "practice",
    "deployments": [
        {
            **{k: v for k, v in _PRACTICE_ROW.items() if k != "parity"},
            "total_pnl": 1430.55,
            "position_count": 2,
            "org_id": "org_x",
        }
    ],
    "total_account_value": 21430.55,
    "total_count": 1,
    "total_fees": 3.1,
    "total_funding": 0.0,
    "total_realized_pnl": 1201.0,
    "total_unrealized_pnl": 229.55,
}
_EMPTY_PRACTICE_SUMMARY = {
    "active_count": 0,
    "capital_class": "practice",
    "deployments": [],
    "total_account_value": 0.0,
    "total_count": 0,
    "total_fees": 0.0,
    "total_funding": 0.0,
    "total_realized_pnl": 0.0,
    "total_unrealized_pnl": None,
}


def _portfolio(practice: dict, *, listed: bool = False) -> tuple[list, dict]:
    """(reads as (path, params, headers), envelope) for the portfolio view, the
    faked summary honouring `capital_class` as keel-api does (S01 §3.10)."""
    from keel.tools.outcomes import get as get_tool
    from keel.tools.outcomes._base import ToolContext

    reads: list = []

    def get(path, *, headers=None, **params):
        reads.append((path, params, headers))
        assert path == _SUMMARY_PATH, path
        return json.loads(
            json.dumps(practice if params["capital_class"] == "practice" else _LIVE_SUMMARY)
        )

    client = mock.MagicMock()
    client.get.side_effect = get
    env = (
        get_tool("keel_live_monitor")
        .handler({}, ToolContext(api_client=client, app_url="https://app.usekeel.io"))
        .to_envelope()
    )
    client.post.assert_not_called()
    return reads, env


def _live_side(envelope: dict) -> str:
    return json.dumps(
        {k: v for k, v in envelope.items() if k not in ("paper", "paper_label")}, sort_keys=True
    )


_BOTH_READS = [
    (_SUMMARY_PATH, {"capital_class": "real"}, _TELEMETRY),
    (_SUMMARY_PATH, {"capital_class": "practice"}, _TELEMETRY),
]


def test_the_paper_section_never_moves_the_live_totals(monkeypatch):
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    assert len(_PRACTICE_SUMMARY["deployments"]) >= 1  # non-vacuity
    reads, with_paper = _portfolio(_PRACTICE_SUMMARY)
    absent_reads, without = _portfolio(_EMPTY_PRACTICE_SUMMARY)
    assert reads == absent_reads == _BOTH_READS
    # The live side is byte-identical with and without paper runs.
    assert _live_side(with_paper) == _live_side(without)
    assert with_paper["data"] == _LIVE_SUMMARY
    # The paper object is the practice summary verbatim, under its label,
    # except that each row's status is keel-app's word for it (QA W2, V2).
    presented = {
        **_PRACTICE_SUMMARY,
        "deployments": [
            {**r, "status": "Paper · degraded"} for r in _PRACTICE_SUMMARY["deployments"]
        ],
    }
    assert json.dumps(with_paper["paper"], sort_keys=True) == json.dumps(presented, sort_keys=True)
    assert with_paper["paper_label"] == "Paper (simulated)"
    # An empty practice summary omits the section.
    assert "paper" not in without and "paper_label" not in without


def test_listed_with_the_flag_off_reads_real_only_and_shows_no_paper(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.delenv("KEEL_LISTED_PAPER", raising=False)
    reads, env = _portfolio(_PRACTICE_SUMMARY)
    assert reads == _BOTH_READS[:1]
    assert "paper" not in env and "paper_label" not in env


def test_listed_with_the_flag_on_reads_both_and_projects_the_paper_rows(
    paper_sdk_released, monkeypatch
):
    from keel.tools.outcomes._listed_projection import listed_deployment_fields

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    reads, env = _portfolio(_PRACTICE_SUMMARY)
    assert reads == _BOTH_READS
    assert env["paper_label"] == "Paper (simulated)"
    rows = env["paper"]["deployments"]
    # With the flag on the listed row allow-list gains the venue block and
    # the health fields (M8-listed-reads-real, §3.5.1); org ids still drop.
    assert len(rows) == 1 and set(rows[0]) <= set(listed_deployment_fields(paper=True))
    assert rows[0]["venue"] == {"code": "PAPER", "capital_class": "practice"}
    assert "org_id" not in rows[0]
    assert env["paper"]["total_account_value"] == _PRACTICE_SUMMARY["total_account_value"]
    assert env["data"]["total_account_value"] == _LIVE_SUMMARY["total_account_value"]


def test_listed_with_the_flag_on_relays_a_practice_overview(paper_sdk_released, monkeypatch):
    """M8-listed-reads-real (§3.5.1): with the listed flag on, a practice row
    is not refused; the row keeps the venue block and the health fields and is
    framed under the paper label."""
    from keel.tools.outcomes import get as get_tool
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    client = mock.MagicMock()
    client.get.side_effect = lambda path, **kw: (
        dict(_PRACTICE_ROW) if path == "/v1/deployments/dep_paper" else {"points": []}
    )
    env = (
        get_tool("keel_live_monitor")
        .handler({"deployment_id": "dep_paper"}, ToolContext(api_client=client))
        .to_envelope()
    )
    data = env["data"]
    for key in ("venue", "health", "health_detail"):
        assert data[key] == _PRACTICE_ROW[key], key
    assert env["label"] == "Paper run (simulated)"
