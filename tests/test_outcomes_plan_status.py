"""`keel_plan_usage` (spec 04 R2, minimized by D-12) — the caller's own plan.

The contract under test (mcp-conversion 04 §4.3, D-12, 2026-09-28):

* full MCP round-trip (create_server → call_tool → keel-api mock): the
  result is EXACTLY ``{plan, period, quota, talking_points}`` (+ the
  envelope's ``share_url``) on EVERY surface — full and listed, with or
  without a declared client. ``quota`` is one block per metered backtest
  unit, projected to ``{unit, limit, used, remaining, resets_at}``;
* the numbers are the server's, verbatim — the SDK invents none;
* ONE neutral talking point, exact shape;
* NOT served, though keel-api still sends every one of them (D-10):
  ``upgrade_options`` (other plans, their limits and prices),
  ``builder_fee_bps``, ``manage_url`` / ``hero_url`` (the billing tab),
  ``live_slots``, and the "plan changes are an account action …" and
  "doing nothing is also fine" points;
* older keel-api (no ``quotas`` block, or no ``plan_status`` at all) →
  the same shape from the ``/v1/me`` balances — never client-side
  reconstructions.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re

import pytest
import respx
from httpx import Response
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import (
    LISTED_PROFILE_TOOLS,
    listed_client,
    manage_links_allowed,
)


_bootstrap()

TOOL = OUTCOMES["keel_plan_usage"]

API = "https://api.test.keel"

RESETS_AT = "2026-09-29T00:00:00Z"  # Tue 29 Sep 2026

# The server-computed plan_status block for a mid-consumption free org
# (4 of 50 runs used, 120 of 1,500 compute seconds used, 1 of 1 live slot
# used) — the shape platform_auth.pricing.plan_status_summary produces,
# INCLUDING everything D-12 removed from the agent surface (other plans,
# prices, the builder fee, the billing link, live slots), so every "not
# served" assertion below is non-vacuous by construction.
PLAN_STATUS_BLOCK = {
    "plan": "free",
    "builder_fee_bps": 5,
    "limits": {
        "backtest_runs": 50,
        "compute_seconds": 1500,
        "live_slots": 1,
        "ai_messages": 15,
    },
    "remaining": {"backtest_runs": 46, "compute_seconds": 1380, "live_slots": 0},
    "quotas": [
        {
            "unit": "backtest_runs",
            "label": "backtest runs",
            "limit": 50,
            "used": 4,
            "remaining": 46,
            "unlimited": False,
            "period": "weekly",
            "resets_at": RESETS_AT,
            "seconds_to_reset": 12345,
        },
        {
            "unit": "backtest_compute_seconds",
            "label": "backtest compute time",
            "limit": 1500,
            "used": 120,
            "remaining": 1380,
            "unlimited": False,
            "period": "weekly",
            "resets_at": RESETS_AT,
            "seconds_to_reset": 12345,
        },
        {
            "unit": "live_strategies_max",
            "label": "live strategies",
            "limit": 1,
            "used": 1,
            "remaining": 0,
            "unlimited": False,
        },
        {
            "unit": "ai_messages",
            "label": "AI messages",
            "limit": 15,
            "used": 0,
            "remaining": 15,
            "unlimited": False,
            "period": "weekly",
            "resets_at": RESETS_AT,
        },
    ],
    "upgrade_options": [
        {
            "plan": "starter",
            "price": {"usd_per_month": 29, "usd_per_month_billed_annually": 23},
            "what_changes": {
                "backtest_runs": {"from": 50, "to": 500},
                "builder_fee_bps": {"from": 5, "to": 3},
            },
        },
        {
            "plan": "trader",
            "price": {"usd_per_month": 79, "usd_per_month_billed_annually": 63},
            "what_changes": {"backtest_runs": {"from": 50, "to": "unlimited"}},
        },
    ],
    "manage_url": "https://app.usekeel.io/settings?tab=billing",
}

#: The one talking point for the block above (04 §4.3, exact shape).
FREE_POINT = (
    "Current plan: Free. This week: 46 of 50 backtest runs and 1,380 of 1,500 "
    "compute seconds remaining; resets Tue 29 Sep 00:00 UTC."
)

#: The projected per-unit blocks for the block above.
FREE_QUOTA = [
    {
        "unit": "backtest_runs",
        "limit": 50,
        "used": 4,
        "remaining": 46,
        "resets_at": RESETS_AT,
    },
    {
        "unit": "backtest_compute_seconds",
        "limit": 1500,
        "used": 120,
        "remaining": 1380,
        "resets_at": RESETS_AT,
    },
]

#: The whole served key set, exactly (the envelope adds `share_url`).
RESULT_KEYS = {"plan", "period", "quota", "talking_points", "share_url"}


def _me_response(plan_status: dict | None = PLAN_STATUS_BLOCK) -> dict:
    body = {
        "principal": {"id": "prn_1", "type": "user"},
        "org": {"id": "org_1", "name": "Test Org", "plan": "free", "status": "active"},
        "entitlements": [
            {
                "unit": "backtest_runs",
                "type": "consumable",
                "granted": 50,
                "spent": 4,
                "reserved": 0,
                "available": 46,
            },
            {
                "unit": "backtest_compute_seconds",
                "type": "consumable",
                "granted": 1500,
                "spent": 100,
                "reserved": 20,
                "available": 1380,
            },
            {
                "unit": "live_strategies_max",
                "type": "cap",
                "granted": 1,
                "enabled": False,
                "cap_current": 1,
                "spent": 0,
                "reserved": 0,
                "available": 1,
            },
        ],
        "credential_scopes": None,
    }
    if plan_status is not None:
        body["plan_status"] = copy.deepcopy(plan_status)
    return body


@pytest.fixture()
def _api_env(monkeypatch):
    """Authenticated SDK config pointed at the mock API; full profile."""
    monkeypatch.setenv("KEEL_API_KEY", "test-key")
    monkeypatch.setenv("KEEL_API_URL", API)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_LISTED_CLIENT", raising=False)
    monkeypatch.setattr("keel.client.time.sleep", lambda *_: None)


def _call_mcp() -> dict:
    """Full MCP round-trip: real FastMCP server, tools/call, JSON body."""
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        result = await server.call_tool("keel_plan_usage", {})
        return json.loads(result.content[0].text)

    return asyncio.run(go())


#: Every surface the one endpoint and the local server run as — including
#: the configuration prod deploys (`listed`, no declared client).
SURFACES = [
    ("full", None),
    ("full", "claude"),
    ("listed", "claude"),
    ("listed", "chatgpt"),
    ("listed", None),  # values-prod.yaml
]


def _set_surface(monkeypatch, profile: str, client_env: str | None) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
    if client_env:
        monkeypatch.setenv("KEEL_LISTED_CLIENT", client_env)
    else:
        monkeypatch.delenv("KEEL_LISTED_CLIENT", raising=False)


# "upgrade now"-class marketing phrases (spec 04 AC) — scanned against the
# WHOLE serialized envelope.
BANNED_PHRASES_RE = re.compile(
    r"upgrade"
    r"|\bunlock\b"
    r"|\bsubscribe now\b"
    r"|\bbuy now\b"
    r"|\bact now\b"
    r"|\blimited[- ]time\b"
    r"|\bdon'?t miss\b"
    r"|\bbest value\b"
    r"|\bsupercharge\b"
    r"|\bpowerful\b"
    r"|\bpremium\b"
    r"|\bgenerous\b",
    re.IGNORECASE,
)


def _all_keys(obj) -> list[str]:
    if isinstance(obj, dict):
        keys: list[str] = []
        for key, value in obj.items():
            keys.append(key)
            keys.extend(_all_keys(value))
        return keys
    if isinstance(obj, list):
        return [k for item in obj for k in _all_keys(item)]
    return []


# ─── The minimized shape (D-12, 04 §4.3) ─────────────────────────────────


@respx.mock
def test_the_result_is_exactly_the_callers_own_state_on_every_surface(_api_env, monkeypatch):
    """The key set is EXACT on every surface, and the numbers are the
    server's own, verbatim.

    SEED (run 2026-09-28): put `"upgrade_options": ps.get(
    "upgrade_options")` back into the `_handler` body — this test and the
    whole-output scan below red; `test_schema_takes_no_arguments` (control)
    stays green. Reverted by reversing the edit.

    Not vacuous: the served block DOES carry upgrade_options, prices, the
    builder fee, the billing link and live slots (asserted first), and the
    surface matrix is the full product the endpoint runs as.
    """
    served = json.dumps(PLAN_STATUS_BLOCK)
    for token in ("upgrade_options", "usd_per_month", "builder_fee_bps", "manage_url"):
        assert token in served, f"non-vacuity: the served block carries {token}"
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    assert len(SURFACES) == 5
    for profile, client_env in SURFACES:
        _set_surface(monkeypatch, profile, client_env)
        env = _call_mcp()
        where = (profile, client_env)
        assert set(env) == RESULT_KEYS, (where, sorted(env))
        assert env["plan"] == "free", where
        assert env["period"] == "weekly", where
        assert env["quota"] == FREE_QUOTA, where
        assert env["talking_points"] == [FREE_POINT], where
        assert env["share_url"] is None, where


@respx.mock
def test_nothing_d12_removed_is_served_anywhere(_api_env, monkeypatch):
    """Whole-output scan, keys AND text: no other plan, no price, no fee,
    no destination, no live slots, no marketing, no do-nothing line."""
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    for profile, client_env in SURFACES:
        _set_surface(monkeypatch, profile, client_env)
        env = _call_mcp()
        blob = json.dumps(env)
        keys = set(_all_keys(env))
        for key in (
            "upgrade_options",
            "builder_fee_bps",
            "manage_url",
            "hero_url",
            "url_line",
            "live_slots",
            "limits",
            "remaining_quota",
            "price",
            "what_changes",
        ):
            assert key not in keys, (profile, client_env, key)
        for token in (
            "http",
            "settings",
            "billing",
            "checkout",
            "stripe",
            "starter",
            "trader",
            "Starter",
            "live",
            "fee",
            "Doing nothing",
            "account action",
            "$",
        ):
            assert token not in blob, (profile, client_env, token)
        assert not BANNED_PHRASES_RE.search(blob), (profile, client_env)


def test_the_talking_point_passes_the_d12_wall_scan():
    """The ONE point passes the SAME validator and D-12 scan the walls use,
    with no do-nothing line required (a neutral fact proposes no action)."""
    from keel.errors import assert_neutral_wall_text
    from keel.tools.outcomes._handoff import validate_talking_points

    assert assert_neutral_wall_text(FREE_POINT, free_plan=False) == FREE_POINT
    assert validate_talking_points([FREE_POINT], require_do_nothing=False) == [FREE_POINT]
    # Control: the default (action walls) still requires the do-nothing line.
    with pytest.raises(ValueError, match="do-nothing"):
        validate_talking_points([FREE_POINT])


@respx.mock
def test_a_paid_plan_reads_the_same_way(_api_env):
    """The plan name is the caller's OWN plan, whatever it is; nothing about
    any other plan appears."""
    block = copy.deepcopy(PLAN_STATUS_BLOCK)
    block["plan"] = "starter"
    for q in block["quotas"]:
        if q["unit"] == "backtest_runs":
            q.update(limit=500, used=4, remaining=496)
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
    env = _call_mcp()
    assert env["plan"] == "starter"
    assert env["talking_points"] == [
        "Current plan: Starter. This week: 496 of 500 backtest runs and 1,380 of 1,500 "
        "compute seconds remaining; resets Tue 29 Sep 00:00 UTC."
    ]


@respx.mock
def test_an_unlimited_unit_is_named_not_counted(_api_env):
    block = copy.deepcopy(PLAN_STATUS_BLOCK)
    block["plan"] = "trader"
    block["quotas"][0] = {"unit": "backtest_runs", "label": "backtest runs", "unlimited": True}
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
    env = _call_mcp()
    assert env["quota"][0] == {
        "unit": "backtest_runs",
        "limit": "unlimited",
        "remaining": "unlimited",
    }
    assert "2147483647" not in json.dumps(env)
    assert env["talking_points"][0].startswith(
        "Current plan: Trader. This week: unlimited backtest runs and 1,380 of 1,500"
    )


@respx.mock
def test_no_reset_is_reported_when_the_server_sends_none(_api_env):
    """The honesty rule: a client that computes a period boundary locally
    is how three hardcoded "Resets daily" claims ended up on weekly grants
    (Q-1597). Control arm for the exact point above, which names a reset."""
    block = copy.deepcopy(PLAN_STATUS_BLOCK)
    for q in block["quotas"]:
        q.pop("resets_at", None)
        q.pop("seconds_to_reset", None)
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
    env = _call_mcp()
    assert all("resets_at" not in q for q in env["quota"])
    assert "reset" not in env["talking_points"][0].lower()


# ─── The first-week allowance (connect-onboarding spec 01 §1.8/§1.9) ─────

ENDS_AT = "2026-10-13T15:02:11Z"  # Tue 13 Oct 2026

#: The one first-week sentence for the blocks below, exactly (§1.9).
FIRST_WEEK_POINT = "154 of 200 first-week backtests left; they end Tue 13 Oct 15:02 UTC."


def _first_week_block() -> dict:
    """A free org in its first week: the effective limit is L + R (§1.8),
    each unit carries `first_week`, plus a field no surface was reviewed for."""
    block = copy.deepcopy(PLAN_STATUS_BLOCK)
    runs, compute = block["quotas"][0], block["quotas"][1]
    runs.update(limit=250, used=50, remaining=200)
    runs["first_week"] = {
        "granted": 200,
        "remaining": 154,
        "ends_at": ENDS_AT,
        "grant_id": "grt_unreviewed",
    }
    compute.update(limit=7500, used=1500, remaining=6000)
    compute["first_week"] = {"granted": 6000, "remaining": 4620, "ends_at": ENDS_AT}
    return block


@respx.mock
def test_first_week_sentence_is_always_served_when_present(_api_env, monkeypatch):
    """§1.9: always in this tool when the server reports the allowance — as
    its own talking point after the plan sentence, on every surface — and
    `first_week` rides each unit's block, projected to its allow-list."""
    respx.get(f"{API}/v1/me").mock(
        return_value=Response(200, json=_me_response(_first_week_block()))
    )
    for profile, client_env in SURFACES:
        _set_surface(monkeypatch, profile, client_env)
        env = _call_mcp()
        where = (profile, client_env)
        assert set(env) == RESULT_KEYS, where
        assert env["talking_points"] == [
            "Current plan: Free. This week: 200 of 250 backtest runs and 6,000 of 7,500 "
            "compute seconds remaining; resets Tue 29 Sep 00:00 UTC.",
            FIRST_WEEK_POINT,
        ], where
        assert env["quota"][0]["first_week"] == {
            "granted": 200,
            "remaining": 154,
            "ends_at": ENDS_AT,
        }, where
        assert env["quota"][1]["first_week"]["granted"] == 6000, where
        blob = json.dumps(env)
        assert "grt_unreviewed" not in blob, where
        assert blob.count("first-week") == 1, where
        # §1.9: no other bonus wording, ever.
        assert not re.search(r"\b(?:bonus|extra|trial|upgrade)\b|then 50", blob, re.I), where


@respx.mock
def test_first_week_absent_serves_the_one_point_unchanged(_api_env):
    """Control arm: the same org with no `first_week` key (the allowance is
    not active) gets exactly the pre-existing single point and block keys."""
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    env = _call_mcp()
    assert env["talking_points"] == [FREE_POINT]
    assert all("first_week" not in q for q in env["quota"])
    assert "first-week" not in json.dumps(env)


@respx.mock
def test_first_week_on_the_compute_unit_alone_renders_no_sentence(_api_env):
    """The sentence counts backtests: a `first_week` on the compute block
    only is projected, and no sentence is invented from it."""
    block = _first_week_block()
    del block["quotas"][0]["first_week"]
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
    env = _call_mcp()
    assert len(env["talking_points"]) == 1
    assert "first_week" in env["quota"][1]
    assert "first-week" not in json.dumps(env)


@respx.mock
def test_first_week_with_a_missing_fact_renders_no_sentence(_api_env):
    """No invented numbers (Q-1590): an `ends_at` the SDK cannot read, or a
    missing count, means no sentence rather than a half one."""
    for broken in ({"ends_at": "not-a-time"}, {"remaining": None}, {"granted": True}):
        block = _first_week_block()
        block["quotas"][0]["first_week"].update(broken)
        respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
        env = _call_mcp()
        assert len(env["talking_points"]) == 1, broken


@respx.mock
def test_first_week_from_the_older_api_balances(_api_env):
    """A keel-api whose plan_status carries no `quotas` block: the /v1/me
    balances carry `first_week` (EntitlementBalance, §1.8) and the same
    sentence is served from them."""
    block = {k: v for k, v in PLAN_STATUS_BLOCK.items() if k != "quotas"}
    me = _me_response(block)
    me["entitlements"][0].update(
        granted=250,
        period="weekly",
        resets_at=RESETS_AT,
        first_week={"granted": 200, "remaining": 154, "ends_at": ENDS_AT},
    )
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=me))
    env = _call_mcp()
    assert env["talking_points"][-1] == FIRST_WEEK_POINT
    assert env["quota"][0]["first_week"]["remaining"] == 154


def test_an_urgent_first_week_sentence_fails_the_call(monkeypatch):
    """SEEDED arm (in-process): if the renderer ever emitted expiry urgency,
    the tool refuses to serve it — the emit guard fails the call rather than
    letting the text reach a host. Control: the approved sentence passes the
    same two guards in test_policy_scan."""
    import keel.tools.outcomes.plan_status as ps

    monkeypatch.setattr(
        ps,
        "render_first_week_sentence",
        lambda _block: FIRST_WEEK_POINT[:-1] + "; use them before they expire.",
    )

    class _Client:
        def get(self, path):
            return _me_response(_first_week_block())

    class _Ctx:
        def get_client(self):
            return _Client()

    with pytest.raises(ValueError, match="before they expire"):
        ps._handler({}, _Ctx())


# ─── Older-API degraded paths — no invented numbers ─────────────────────


@respx.mock
def test_older_api_without_quotas_reads_the_balances(_api_env):
    """A keel-api whose plan_status predates the `quotas` block: the units
    come from the /v1/me balances — `used` is spent + reserved (the
    QuotaView definition), never limit − remaining."""
    block = {k: v for k, v in PLAN_STATUS_BLOCK.items() if k != "quotas"}
    me = _me_response(block)
    me["entitlements"][0].update(period="weekly", resets_at=RESETS_AT)
    me["entitlements"][1].update(period="weekly", resets_at=RESETS_AT)
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=me))
    env = _call_mcp()
    assert set(env) == RESULT_KEYS
    assert env["plan"] == "free"
    assert env["quota"] == FREE_QUOTA  # compute used = 100 spent + 20 reserved
    assert env["talking_points"] == [FREE_POINT]


@respx.mock
def test_older_api_without_plan_status_degrades_honestly(_api_env):
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(plan_status=None)))
    env = _call_mcp()
    assert set(env) == RESULT_KEYS
    assert env["plan"] == "free"  # from org.plan
    assert env["period"] is None, "the balances named no period — none is invented"
    assert [q["remaining"] for q in env["quota"]] == [46, 1380]
    assert env["talking_points"] == [
        "Current plan: Free. This period: 46 of 50 backtest runs and 1,380 of 1,500 "
        "compute seconds remaining."
    ]


@respx.mock
def test_older_api_unlimited_sentinel_is_labelled(_api_env):
    me = _me_response(plan_status=None)
    me["entitlements"][0]["granted"] = 2147483647
    me["entitlements"][0]["available"] = 2147483647
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=me))
    env = _call_mcp()
    assert env["quota"][0]["remaining"] == "unlimited"
    assert "2147483647" not in json.dumps(env)


# ─── The link policy seam (D-12) ─────────────────────────────────────────


def test_listed_client_env_validation(monkeypatch):
    """chatgpt/claude/unset are the only accepted values — a typo raises
    instead of silently picking a policy branch."""
    monkeypatch.delenv("KEEL_LISTED_CLIENT", raising=False)
    assert listed_client() is None
    monkeypatch.setenv("KEEL_LISTED_CLIENT", " ChatGPT ")
    assert listed_client() == "chatgpt"
    monkeypatch.setenv("KEEL_LISTED_CLIENT", "claude")
    assert listed_client() == "claude"
    monkeypatch.setenv("KEEL_LISTED_CLIENT", "openai")
    with pytest.raises(ValueError, match="KEEL_LISTED_CLIENT"):
        listed_client()


def test_manage_links_policy_is_no_link_on_every_surface(monkeypatch):
    """D-12: the named seam answers "no link" on every profile, with or
    without a declared client. The matrix is kept as the regression net
    for re-introducing a per-surface arm. Not vacuous: all five cases run,
    and the `listed`/None row is the configuration prod deploys.

    SEED (run 2026-09-28): `return True` — this reds; the client-env
    validation test (control) stays green. Reverted by reversing it."""
    for profile, client_env in SURFACES:
        _set_surface(monkeypatch, profile, client_env)
        assert manage_links_allowed() is False, (profile, client_env)


def test_description_names_the_callers_own_state_only():
    """The description is the Lovable `get_workspace` shape — and there is
    one description (no listed variant to drift)."""
    text = TOOL.description
    assert TOOL.listed_description is None
    for token in (
        "upgrade",
        "price",
        "builder fee",
        "manage_url",
        "other plans",
        "live strategy slots",
        "where plans are listed",
    ):
        assert token not in text.lower(), token
    assert "plan name" in text and "reset instant" in text


# ─── Registration / gating ──────────────────────────────────────────────


def test_read_only_toolset_listed_inclusion_and_read_bucket(monkeypatch):
    from keel.tools.outcomes._mcp_adapter import loaded_tool_names

    assert TOOL.toolset == "read-only"
    assert TOOL.local_only is False
    assert TOOL.required_action == "audit.read"
    assert TOOL.annotations["readOnlyHint"] is True
    assert "keel_plan_usage" in LISTED_PROFILE_TOOLS

    # Present on the default toolsets, both local and hosted, both profiles.
    for mode in (None, "hosted"):
        if mode:
            monkeypatch.setenv("KEEL_EXECUTION_MODE", mode)
        else:
            monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
        for profile in ("full", "listed"):
            monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
            monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
            assert "keel_plan_usage" in loaded_tool_names(OUTCOMES), (mode, profile)


def test_cli_command_registers():
    """`keel plan status` is the CLI face of the same outcome."""
    import click
    from keel.tools.outcomes._cli_adapter import register_all as cli_register_all

    root = click.Group("keel")
    cli_register_all(root, OUTCOMES)
    assert "plan" in root.commands
    assert "status" in root.commands["plan"].commands


def test_schema_takes_no_arguments():
    assert TOOL.input_schema["required"] == []
    assert TOOL.input_schema["properties"] == {}
