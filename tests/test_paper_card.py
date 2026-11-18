"""The paper tools draw the live card: receipts, walls and the waiting state (Q-7603).

Before Q-7603 ``keel_paper_deploy`` and ``keel_paper_control`` registered in
``text`` mode under prod's flags: one text block, no structuredContent, no card
``_meta``. Their plan walls (the paper slot cap, the timeframe floor) reached the
host as prose only, the one plan limit with no plan-limit card, and a started
run drew nothing at all. They now take the monitor's registration mode
(``_channels.KIND_BY_TOOL`` → ``live``) and its card (``widgets.CARD_TOOLS``).
The card-only ``render.paper`` block moves to ``_meta["keel/card"]``, so the
text block the model reads is what it was.

Every result here comes from the REAL tool over the FastMCP wire (the SDK's own
server in-process, keel-api faked at ``KeelClient``), under prod's flags
(``infrastructure/helm/mcp-server/values-prod.yaml``: ``KEEL_CARD_META_MOVE=1``,
``KEEL_NONVIEW_TEXT_ONLY=1``, ``KEEL_LISTED_PAPER_HOSTS=all``). The card arms
draw those results in real Chromium (``fixtures/cards/c_paper_card_check.mjs``)
on the full and the listed live card (and a real run on the listed card as
served with paper off, Q-7640), through both host dialects. They skip
(never silently pass) without node or Playwright; ``test_card_browser.py``
fails the suite under CI when the browser is missing.

Proofs it can fail (run 2026-11-16, each reversed by reversing the exact edit):

* drop the two paper rows from ``_channels.KIND_BY_TOOL``: the wire arms red
  (no structuredContent, text mode); the walls' card arms red (``planLimit``
  false on ChatGPT, whose ``toolOutput`` is then null). The real-run control
  stays green.
* drop ``render`` from ``_channels._LIVE.card``: ``test_the_text_block_*`` red
  (the receipt facts reach the model's text). The walls' arms stay green.
* make ``isReceipt`` in ``card-live-paper.js`` return ``false``: the receipt
  arms red (``Untitled`` and six dashed tiles); the walls, the waiting state and
  the control stay green.
* make ``isWaiting`` return ``false``: the waiting arms red (dashed tiles); the
  history arm and the control stay green.
* (Q-7640, run 2026-11-16) remove the v-strip from ``host-adapter-listed.js``
  ``header()``: the paper-off listed arm and the listed arms of the version
  test red on ``'vv3' == 'v3'`` (the full arms stay green); the pin test reds
  too, since the drawn card is no longer the pinned bytes.

Proof it is not vacuous: the control arm (a real run's overview through the
same harness) draws tiles and a chart with no stamp, so "no tiles" on a paper
arm is the fragment's verdict, not a harness that draws nothing; and every
paper arm is asserted to have drawn a header or the plan-limit state.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import shutil
import subprocess
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.errors import translate_http_error


HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE / "fixtures" / "cards" / "c_paper_card_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"
API = "https://api.paper-card.test"
APP = "https://app.usekeel.io"

#: prod's mcp-server flags (values-prod.yaml).
PROD_FLAGS = {
    "KEEL_CARD_META_MOVE": "1",
    "KEEL_NONVIEW_TEXT_ONLY": "1",
    "KEEL_LISTED_PAPER_HOSTS": "all",
    "KEEL_HOSTED_PAPER_SDK": "true",
}
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
    "KEEL_APP_URL",
    *PROD_FLAGS,
)

#: keel-api's paper deployment row (`schemas/live.py` DeploymentResponse), as
#: POST /v1/paper and the lifecycle routes answer it.
ROW = {
    "deployment_id": "dep_p1",
    "strategy_id": "str_s1",
    "name": "BTC trend",
    "status": "LIVE",
    "schedule": "0 * * * *",
    "evaluation_grid": {"period_minutes": 60, "offset_minutes": 0},
    "next_run_at": "2026-11-17T14:00:00Z",
    "deployed_at": "2026-11-16T13:02:00Z",
    "deployed_version_string": "v3",
    "venue": {"code": "PAPER", "capital_class": "practice"},
    "org_id": "org_secret",
    "account_id": "acc_secret",
}

#: keel-api's 403 at the paper slot cap (test_paper_quota_wall_served_envelope).
CAP_403 = {
    "title": "Forbidden",
    "status": 403,
    "detail": "You're using 1 of 1 paper strategies, the maximum on this plan.",
    "code": "quota_cap_reached",
    "reasons": ["entitlement:cap_exceeded:paper_strategies_max:limit=1:used=1:remaining=0"],
    "quota": {
        "kind": "cap_exceeded",
        "unit": "paper_strategies_max",
        "label": "paper strategies",
        "code": "quota_cap_reached",
        "limit": 1,
        "used": 1,
        "remaining": 0,
        "plan": "free",
        "plan_name": "Free",
        "higher_plans": [
            {"plan": "trader", "version": "v2", "name": "Trader", "limit": 5},
            {"plan": "pro", "version": "v2", "name": "Pro", "limit": 20},
        ],
    },
}

#: keel-api's 422 at the paper/live timeframe floor (§L7; test_errors).
FLOOR_422 = {
    "detail": {
        "code": "SERVING_CLOCK_BELOW_PLAN_FLOOR",
        "message": (
            "This plan runs paper and live strategies at a 1-hour timeframe or longer; "
            "this limit does not reset on a clock."
        ),
        "floor": "1h",
        "requested": "15min",
        "plan": "free",
        "plan_name": "Free",
        "higher_plans": [
            {"plan": "trader", "version": "v2", "name": "Trader", "floor": "5min"},
            {"plan": "pro", "version": "v2", "name": "Pro", "floor": "1min"},
        ],
    }
}
FLOOR_LINE = "Paper and live on Free need 1h or slower; this is 15min."
CAP_LINE = "Paper strategies: 1 of 1 running on Free"

#: A paper run with eight hours of history: the monitor's detail, stats and
#: equity reads (LiveStatsResponse; the equity route's points).
STATS = {
    "net_pnl": 42.5,
    "today_pnl": 42.5,
    "max_drawdown_pct": -0.8,
    "total_return_pct": 0.43,
    "sharpe": 1.4,
    "total_trades": 6,
}
EQUITY = {
    "baseline_value": 10000.0,
    "current_value": 10042.5,
    "points": [
        {"timestamp": f"2026-11-16T{h:02d}:00:00Z", "equity": 10000 + 5.3 * i, "twr_pct": i / 20}
        for i, h in enumerate(range(14, 22))
    ],
}

#: The paper positions read (ExpandedPositionsEnvelope): no name, no venue.
POSITIONS = {
    "account_value": 10042.5,
    "account_mode": "standard",
    "perp_positions": [
        {
            "symbol": "BTC",
            "side": "long",
            "size": 0.05,
            "entry_price": 95000.0,
            "mark_price": 95400.0,
            "unrealized_pnl": 20.0,
        }
    ],
    "margin_summary": {"total_margin_used": 950.0, "total_ntl_pos": 4770.0},
}

#: The create route's last-slot block (doc 07 §5; `last_slot_quota`).
LAST_SLOT = {
    "unit": "paper_strategies_max",
    "label": "paper strategies",
    "limit": 1,
    "used": 1,
    "remaining": 0,
    "unlimited": False,
    "tier": "exhausted",
    "plan": "free",
    "plan_name": "Free",
    "higher_plans": [
        {"plan": "trader", "version": "v2", "name": "Trader", "limit": 5},
        {"plan": "pro", "version": "v2", "name": "Pro", "limit": 20},
    ],
}

#: The portfolio summary for one class (S01 §3.10): this org runs paper only.
PAPER_SUMMARY = {
    "total_account_value": 10042.5,
    "total_unrealized_pnl": 20.0,
    "total_realized_pnl": 22.5,
    "active_count": 1,
    "total_count": 1,
    "deployments": [
        {
            "deployment_id": "dep_p1",
            "name": "BTC trend",
            "status": "LIVE",
            "total_pnl": 42.5,
            "position_count": 1,
            "venue": {"code": "PAPER", "capital_class": "practice"},
        }
    ],
}
NO_RUNS = {"total_account_value": 0, "active_count": 0, "total_count": 0, "deployments": []}


@contextmanager
def _world(profile: str):
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(PROD_FLAGS, KEEL_EXECUTION_MODE="hosted", KEEL_APP_URL=APP)
    if profile == "listed":
        os.environ["KEEL_SERVER_PROFILE"] = "listed"
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


def _answer(routes: dict, method: str, path: str, params: dict | None = None):
    for (m, prefix), value in routes.items():
        if m == method and (path == prefix or path.startswith(prefix + "?")):
            if callable(value):
                value = value(params or {})
            if isinstance(value, BaseException):
                raise value
            return value
    raise AssertionError(f"unrouted {method} {path}")


def _call(profile: str, tool: str, args: dict, routes: dict) -> dict:
    """One call over the FastMCP wire, and the tool's tools/list record."""

    def get(self, path, *a, headers=None, **params):
        return _answer(routes, "GET", path, params)

    def post(self, path, *a, **k):
        return _answer(routes, "POST", path)

    def delete(self, path, *a, **k):
        return _answer(routes, "DELETE", path)

    async def go():
        from fastmcp import Client
        from keel.mcp.server import create_server

        server = create_server()
        listed = {t.name: t for t in await server.list_tools()}
        async with Client(server) as client:
            res = await client.call_tool(tool, args, raise_on_error=False)
        return listed[tool], res

    with (
        _world(profile),
        mock.patch("keel.client.KeelClient.get", get),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.client.KeelClient.delete", delete),
        mock.patch("keel.tools.outcomes._handoff._is_anon_session", lambda: False),
    ):
        tool_obj, res = asyncio.run(go())
    wire = tool_obj.to_mcp_tool()
    return {
        "result": {
            "content": [{"type": "text", "text": c.text} for c in res.content],
            "structuredContent": res.structured_content,
            "_meta": res.meta or {},
            "isError": res.is_error,
        },
        "input": args,
        "tool_meta": wire.meta or {},
        "output_schema": wire.output_schema,
    }


def _paper_routes() -> dict:
    return {("POST", "/v1/paper"): dict(ROW)}


def _monitor_routes(*, history: bool, real: bool = False) -> dict:
    row = dict(ROW, status="LIVE", position_count=2 if history else 0)
    if real:
        row = dict(row, venue={"code": "HL", "capital_class": "real"}, name="adx_trend_crypto")
    return {
        ("GET", "/v1/deployments/dep_p1"): row,
        ("GET", "/v1/deployments/dep_p1/stats"): dict(STATS) if history else {},
        ("GET", "/v1/deployments/dep_p1/equity"): (
            dict(EQUITY) if history else {"points": [], "baseline_value": 10000.0}
        ),
        ("GET", "/v1/deployments/dep_p1/executions"): (
            [
                {
                    "signal_status": "completed",
                    "execution_status": "completed",
                    "order_count": 2,
                    "filled_count": 2,
                    "signal_as_of_time": "2026-11-16T21:00:00Z",
                }
            ]
            if history
            else []
        ),
    }


CASES: dict[str, tuple[str, dict, dict]] = {
    "deploy": ("keel_paper_deploy", {"strategy_id": "str_s1"}, _paper_routes()),
    "pause": (
        "keel_paper_control",
        {"deployment_id": "dep_p1", "action": "pause"},
        {("POST", "/v1/paper/dep_p1/pause"): dict(ROW, status="PAUSED", pause_reason="user")},
    ),
    "stop": (
        "keel_paper_control",
        {"deployment_id": "dep_p1", "action": "stop"},
        {("DELETE", "/v1/paper/dep_p1"): None},
    ),
    "cap_wall": (
        "keel_paper_deploy",
        {"strategy_id": "str_s1"},
        {("POST", "/v1/paper"): translate_http_error(403, json.dumps(CAP_403))},
    ),
    "floor_wall": (
        "keel_paper_deploy",
        {"strategy_id": "str_s1"},
        {("POST", "/v1/paper"): translate_http_error(422, json.dumps(FLOOR_422))},
    ),
    "waiting": (
        "keel_live_monitor",
        {"deployment_id": "dep_p1"},
        _monitor_routes(history=False),
    ),
    "history": (
        "keel_live_monitor",
        {"deployment_id": "dep_p1"},
        _monitor_routes(history=True),
    ),
    "real": (
        "keel_live_monitor",
        {"deployment_id": "dep_p1"},
        _monitor_routes(history=True, real=True),
    ),
    "positions": (
        "keel_live_monitor",
        {"deployment_id": "dep_p1", "view": "positions"},
        {
            ("GET", "/v1/deployments/dep_p1"): dict(ROW, position_count=1),
            ("GET", "/v1/deployments/dep_p1/positions"): POSITIONS,
        },
    ),
    "portfolio": (
        "keel_live_monitor",
        {},
        {
            ("GET", "/v1/deployments/portfolio/summary"): lambda params: (
                PAPER_SUMMARY if params.get("capital_class") == "practice" else NO_RUNS
            )
        },
    ),
    "portfolio_empty": (
        "keel_live_monitor",
        {},
        {("GET", "/v1/deployments/portfolio/summary"): NO_RUNS},
    ),
    "last_slot": (
        "keel_paper_deploy",
        {"strategy_id": "str_s1"},
        {("POST", "/v1/paper"): dict(ROW, quota=LAST_SLOT)},
    ),
}
PAPER_WRITES = ("deploy", "pause", "stop", "cap_wall", "floor_wall", "last_slot")


@pytest.fixture(scope="module", params=["listed", "full"])
def wire(request, paper_sdk_released_module) -> dict:
    return {
        name: _call(request.param, tool, dict(args), routes)
        for name, (tool, args, routes) in CASES.items()
    }


@pytest.fixture(scope="module")
def paper_sdk_released_module():
    from tests._paper_sdk import paper_sdk_release

    with paper_sdk_release():
        yield


def _text(case: dict) -> str:
    blocks = case["result"]["content"]
    assert len(blocks) == 1, blocks
    return blocks[0]["text"]


# ── The wire ─────────────────────────────────────────────────────────────


def test_the_paper_tools_advertise_the_live_card(wire: dict) -> None:
    from keel.widgets import card_resource_uri

    for name in PAPER_WRITES:
        meta = wire[name]["tool_meta"]
        assert meta.get("ui", {}).get("resourceUri") == card_resource_uri("live"), name
        assert "paper run" in meta["openai/widgetDescription"], name
        # The monitor's frozen `{"result": string}` shape (R-25), not a new one.
        schema = wire[name]["output_schema"]
        assert schema["properties"] == {"result": {"type": "string"}}, name


def test_every_paper_result_carries_structured_content(wire: dict) -> None:
    """The card draws from structuredContent; ChatGPT hands it nothing else."""
    for name in PAPER_WRITES:
        result = wire[name]["result"]
        structured = result["structuredContent"]
        assert structured is not None, name
        assert structured["result"] == _text(wire[name]), name
        assert result["isError"] is name.endswith("_wall"), name


def test_the_text_block_is_the_envelope_without_the_card_facts(wire: dict) -> None:
    """The model reads exactly what it read before Q-7603: the envelope JSON,
    with the card-only `render` block moved to `_meta["keel/card"]`."""
    for name in ("deploy", "pause", "stop", "last_slot", "waiting", "positions"):
        text = _text(wire[name])
        envelope = json.loads(text)
        assert "render" not in envelope, name
        assert (envelope.get("url") or envelope["hero_url"]).startswith(f"{APP}/paper/"), name
        card = wire[name]["result"]["_meta"]["keel/card"]
        assert "paper" in card["render"], (name, card)
    deploy = json.loads(_text(wire["deploy"]))
    assert deploy["status"] == "Paper" and deploy["simulated_capital"] == 10000
    assert "run_notes" in deploy


def test_the_walls_keep_their_words_for_the_model(wire: dict) -> None:
    """The agent still reads the wall verbatim, and the card gets `limit_view`."""
    for name, line in (("cap_wall", CAP_LINE), ("floor_wall", FLOOR_LINE)):
        text = _text(wire[name])
        envelope = json.loads(text)
        assert envelope["code"] == "handoff_required", name
        assert envelope["message"].startswith(line), (name, envelope["message"])
        assert envelope["limit_view"]["headline"], name
        assert "keel/card" not in wire[name]["result"]["_meta"], name


def test_the_receipt_facts_are_worded_from_the_route_row(wire: dict) -> None:
    facts = wire["deploy"]["result"]["_meta"]["keel/card"]["render"]["paper"]
    assert facts == {
        "name": "BTC trend",
        "version": "v3",
        "state": "Paper",
        "clock": "1h",
        "next_run": "17 Nov 2026 at 14:00 UTC",
        "capital": 10000,
    }
    # A stop answers 204: the state is all there is, and nothing is guessed.
    stop = wire["stop"]["result"]["_meta"]["keel/card"]["render"]["paper"]
    assert stop == {"state": "Stopped"}


def test_clock_words_follow_the_timeframe_alphabet() -> None:
    from keel.tools.outcomes._paper_card import clock_words

    assert [clock_words({"period_minutes": m}) for m in (15, 60, 240, 1440)] == [
        "15min",
        "1h",
        "4h",
        "1d",
    ]
    assert clock_words(None) is None and clock_words({"period_minutes": 0}) is None


# ── The card ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def drawn(tmp_path_factory, paper_sdk_released_module) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the paper states are card script")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    from keel.widgets import build_card_html

    # Each card draws its own profile's results: the listed row is the
    # listed allow-list (no evaluation grid), the full one keel-api's row.
    cases = {
        card: {
            name: _call(profile, tool, dict(args), routes)
            for name, (tool, args, routes) in CASES.items()
        }
        for card, profile in (("live", "full"), ("live_listed", "listed"))
    }
    root = tmp_path_factory.mktemp("paper-cards")
    cards = root / "cards"
    cards.mkdir()
    (cards / "live.html").write_text(build_card_html("live", paper_stamp=True), encoding="utf-8")
    with _world("listed"):
        listed = build_card_html("live", paper_stamp=True)
        listed_off = build_card_html("live", paper_stamp=False)
    (cards / "live_listed.html").write_text(listed, encoding="utf-8")
    # The listed card with paper off (AC-06-7: the pinned listed surface,
    # `test_the_listed_card_with_paper_off_draws_one_v` checks it is those
    # bytes) draws a real run only: no paper run reaches it (Q-7640).
    (cards / f"{LISTED_OFF}.html").write_text(listed_off, encoding="utf-8")
    cases[LISTED_OFF] = {"real": cases["live_listed"]["real"]}
    cases_file = root / "cases.json"
    cases_file.write_text(
        json.dumps(
            {
                card: {k: {"result": v["result"], "input": v["input"]} for k, v in by.items()}
                for card, by in cases.items()
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(cards), "--cases", str(cases_file)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the paper card check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


#: The listed live card as served with paper off (Q-7640).
LISTED_OFF = "live_listed_off"


def _arms(drawn: dict, case: str) -> list[tuple[str, dict]]:
    """The two paper-served cards' arms (the paper-off listed card is drawn
    only for the version label, `_off_arms`)."""
    arms = [
        (k, v)
        for k, v in drawn.items()
        if k.endswith("/" + case) and not k.startswith(LISTED_OFF + "/")
    ]
    assert len(arms) == 4, sorted(drawn)  # two cards × two dialects
    return arms


def _off_arms(drawn: dict) -> list[tuple[str, dict]]:
    arms = [(k, v) for k, v in drawn.items() if k.startswith(LISTED_OFF + "/")]
    assert len(arms) == 2, sorted(drawn)  # one real run × two dialects
    return arms


#: Never on a paper card: an id, a raw stamp, an empty tile, a doubled "v".
_LEAKS = re.compile(r"dep_|str_|org_|acc_|\d{4}-\d{2}-\d{2}T|\bvv\d|—\s*$|Untitled", re.M)


def test_the_harness_is_not_vacuous(drawn: dict) -> None:
    """CONTROL: a real run's overview draws tiles and its chart, no stamp, and
    keeps the live view's link label."""
    for key, m in _arms(drawn, "real"):
        assert m["tiles"] >= 4 and m["charts"] > 0, (key, m)
        # A real run is drawn as without the fragment: its schedule row stays.
        assert ["Schedule", "0 * * * *"] in m["kv"], (key, m["kv"])


def test_a_prefixed_version_label_draws_one_v_on_both_paper_served_cards(drawn: dict) -> None:
    """keel-api's label is "v3"; the header used to draw "vv3" for every run.
    The full adapter strips it (Q-7603); the listed adapter too (Q-7640)."""
    for key, m in _arms(drawn, "real"):
        assert m["chips"][0] == "v3", (key, m["chips"])
        assert "PAPER — simulated" not in m["chips"], (key, m)
        assert m["linkLabel"].startswith("Open live view in Keel"), (key, m)


def test_the_listed_card_with_paper_off_draws_one_v(drawn: dict) -> None:
    """The card the listed profile serves with paper off (the ChatGPT and
    Claude directory connector) drew "vv3" on every real run: its adapter,
    ``host-adapter-listed.js``, prefixed keel-api's already-prefixed label
    (Q-7640). The card drawn is the pinned listed surface's own bytes
    (`test_the_drawn_paper_off_listed_card_is_the_pinned_one`)."""
    for key, m in _off_arms(drawn):
        # Non-vacuous: the header drew the run, so a missing chip is a verdict.
        assert m["headVisible"] and m["name"] == "adx_trend_crypto", (key, m)
        assert m["chips"][0] == "v3", (key, m["chips"])
        assert not re.search(r"\bvv\d", m["text"]), (key, m["text"])
        assert "PAPER — simulated" not in m["chips"], (key, m)


def test_the_drawn_paper_off_listed_card_is_the_pinned_one() -> None:
    """The paper-off listed card `drawn` draws is byte-for-byte the
    `ui://keel/cards/live.html` resource ``listed_surface_head.json`` pins
    (AC-06-7), so the arm above reads the card that is served, not a copy."""
    import hashlib

    from keel.widgets import build_card_html

    from tests._listed_head_fixtures import SURFACE_FIXTURE

    with _world("listed"):
        served = build_card_html("live", paper_stamp=False).encode()
    pinned = {
        c["uri"]: c["contents"][0]["sha256"]
        for c in json.loads(SURFACE_FIXTURE.read_text(encoding="utf-8"))["card_resources"]
    }
    assert hashlib.sha256(served).hexdigest() == pinned["ui://keel/cards/live.html"]


def test_a_paper_start_draws_its_receipt(drawn: dict) -> None:
    for key, m in _arms(drawn, "deploy"):
        assert m["headVisible"] and m["name"] == "BTC trend", (key, m)
        assert m["chips"] == ["v3", "paper", "1h", "PAPER — simulated"], (key, m)
        assert m["kv"] == [
            ["Starting balance", "$10,000"],
            ["Next run", "17 Nov 2026 at 14:00 UTC"],
        ], (key, m)
        assert m["notes"] == ["Started. No fills yet; the first run is queued now."], (key, m)
        assert m["tiles"] == 0 and m["charts"] == 0, (key, m)
        assert m["linkLabel"].startswith("Open paper run in Keel"), (key, m)
        assert not _LEAKS.search(m["text"]), (key, m["text"])


def test_a_pause_and_a_stop_draw_where_the_run_now_stands(drawn: dict) -> None:
    for key, m in _arms(drawn, "pause"):
        assert m["chips"] == ["v3", "paused", "1h", "PAPER — simulated"], (key, m)
        assert m["notes"] == ["Paused. No runs until it is resumed."], (key, m)
        assert m["kv"] == [], (key, m)  # no next run while paused
        assert not _LEAKS.search(m["text"]), (key, m["text"])
    for key, m in _arms(drawn, "stop"):
        assert m["name"] == "Paper run", (key, m)
        assert m["chips"] == ["stopped", "PAPER — simulated"], (key, m)
        assert m["notes"] == ["Stopped for good. Its record is kept."], (key, m)
        assert m["tiles"] == 0, (key, m)


def test_the_paper_walls_draw_the_plan_limit_card(drawn: dict) -> None:
    for case, line in (("cap_wall", CAP_LINE), ("floor_wall", FLOOR_LINE)):
        for key, m in _arms(drawn, case):
            assert m["planLimit"] and not m["errLine"], (key, m)
            assert m["headline"] and m["headline"].startswith(line.split(";")[0]), (key, m)
            assert m["planLinks"] == 0 and m["tiles"] == 0, (key, m)
            assert not re.search(r"\$|https?://|[Uu]pgrade", m["text"]), (key, m["text"])


def test_a_paper_run_before_its_first_fill_waits_plainly(drawn: dict) -> None:
    for key, m in _arms(drawn, "waiting"):
        assert m["name"] == "BTC trend" and "PAPER — simulated" in m["chips"], (key, m)
        assert m["tiles"] == 0 and m["charts"] == 0, (key, m)
        assert m["notes"] == ["No fills yet, so there is no P&L or chart to show."], (key, m)
        assert m["kv"] == [
            ["Next run", "17 Nov 2026 at 14:00 UTC"],
            ["Last run", "It has not run yet."],
        ], (key, m)
        assert not _LEAKS.search(m["text"]), (key, m["text"])


def test_a_paper_run_with_history_draws_the_live_overview_stamped(drawn: dict) -> None:
    for key, m in _arms(drawn, "history"):
        assert m["tiles"] >= 4 and m["charts"] > 0, (key, m)
        assert m["chips"][:2] == ["v3", "paper"] and "PAPER — simulated" in m["chips"], (key, m)
        assert "+$42.50" in m["text"], (key, m["text"])
        # keel-api's cron is no label: the full profile says the clock from the
        # evaluation grid; the listed row carries no grid, so the cron is dropped.
        schedule = [r for r in m["kv"] if r[0] in ("Schedule", "Runs")]
        assert schedule in ([["Runs", "Every 1h"]], []), (key, m["kv"])
        assert "* * *" not in m["text"], (key, m["text"])
        assert m["linkLabel"].startswith("Open paper run in Keel"), (key, m)
        assert not _LEAKS.search(m["text"]), (key, m["text"])


def test_a_paper_run_s_other_views_name_the_run_and_stamp_it(drawn: dict) -> None:
    """A per-view payload names no run and carries no venue; the SDK's facts
    block does, so the card is stamped and named, never "Untitled"."""
    for key, m in _arms(drawn, "positions"):
        assert m["name"] == "BTC trend" and "PAPER — simulated" in m["chips"], (key, m)
        assert "Simulated Value" in m["text"] or "SIMULATED VALUE" in m["text"], (key, m["text"])
        assert "Live positions" not in m["text"], (key, m["text"])
        assert not _LEAKS.search(m["text"]), (key, m["text"])


def test_taking_the_last_slot_says_so_on_the_receipt(drawn: dict) -> None:
    for key, m in _arms(drawn, "last_slot"):
        assert m["notes"][0].startswith("Started."), (key, m)
        assert len(m["notes"]) == 2 and "Trader and Pro" in m["notes"][1], (key, m)
        assert not re.search(r"\$\d+ ?/|https?://|[Uu]pgrade", m["text"]), (key, m["text"])


def test_a_paper_run_s_positions_never_claim_an_exchange_read(wire: dict) -> None:
    """keel-api reads a paper run's positions from Keel's ledger, never from
    Hyperliquid; the model's freshness note says so (a real run keeps its
    exchange note, `tests/test_live_monitor_*`)."""
    envelope = json.loads(_text(wire["positions"]))
    assert envelope["freshness"]["source"] == "keel_paper_ledger", envelope["freshness"]
    assert "Hyperliquid" not in envelope["freshness"]["note"]
    assert envelope["label"] == "Paper run (simulated)"


def test_an_org_with_paper_runs_only_never_reads_nothing_running(drawn: dict) -> None:
    """The portfolio's paper section is drawn beside the real totals, so a
    running paper run is never "Nothing running yet."."""
    for key, m in _arms(drawn, "portfolio"):
        assert "Nothing running yet." not in m["text"], (key, m["text"])
        assert m["notes"] == ["No live runs.", "Paper (simulated)"], (key, m)
        assert "BTC trend" in m["text"] and "+$42.50" in m["text"], (key, m["text"])
    # CONTROL: an org with no runs at all still says so.
    for key, m in _arms(drawn, "portfolio_empty"):
        assert m["notes"] == ["Nothing running yet."], (key, m)
