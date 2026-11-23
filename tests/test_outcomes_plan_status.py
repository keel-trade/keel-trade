"""`keel_plan_usage` (spec 04 R2; D-12 minimized it, D-14 / doc 07 §5 names more).

The contract under test (mcp-conversion D-14, 2026-11-16):

* full MCP round-trip (create_server → call_tool → keel-api mock): the
  result is EXACTLY ``{plan, plan_name, period, quota, talking_points}`` (+
  the envelope's ``share_url``) on EVERY surface, plus ``paper`` / ``live`` /
  ``screens`` / ``serving_floor`` only where the caller can meet them.
  ``quota`` is one block per metered unit, projected to ``{unit, limit, used,
  remaining, resets_at, more_plans}``;
* the numbers are the server's, verbatim — the SDK invents none;
* an opening line naming the plans that include more (served names, computed
  here from plans.yaml), then one short line per limit;
* NOT served, though keel-api still sends every one of them (D-10):
  ``upgrade_options`` (other plans, their limits and prices),
  ``builder_fee_bps``, ``manage_url`` / ``hero_url`` (the billing tab), the
  other plans' limits inside ``higher_plans``, and the "plan changes are an
  account action …" and "doing nothing is also fine" points;
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
from keel.errors import join_plan_names
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import (
    LISTED_PROFILE_TOOLS,
    listed_client,
    manage_links_allowed,
)

from . import _plan_catalog as cat


_bootstrap()

TOOL = OUTCOMES["keel_plan_usage"]


def _served_higher(plan: str, unit: str) -> list[dict]:
    """keel-api's `higher_plans` for `plan` on `unit`: the catalog's names AND
    their limits (D-10), so "no other plan's number" is non-vacuous."""
    out = cat.higher_plans(plan, unit)
    for entry in out:
        entry["limit"] = int(cat._value(entry["plan"], entry["version"], unit))
        entry["period"] = "30d"
    return out


def _opening(plan: str) -> str:
    """The opening line for `plan`, computed from the catalog (doc 07 §5)."""
    names = cat.names(cat.higher_plans(plan, "backtest_runs"))
    head = f"Plan: {cat.display_name(plan)}."
    if not names:
        return head
    verb = "includes" if len(names) == 1 else "include"
    return f"{head} {join_plan_names(names)} {verb} more; plans are changed in the Keel web app."


FREE_MORE = cat.names(cat.higher_plans("free", "backtest_runs"))

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
    "plan_name": "Free",
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
            "higher_plans": _served_higher("free", "backtest_runs"),
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
            "higher_plans": _served_higher("free", "backtest_compute_seconds"),
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

#: The talking points for the block above (doc 07 §5): the opening line,
#: then one line per limit.
#: Compute is silent (PLANS-REVIEW R7): its block rides `quota`, and it has no line until it is spent.
FREE_POINTS = [
    _opening("free"),
    "Backtests: 4 of 50 used on Free; resets Sep 29.",
]

#: The projected per-unit blocks for the block above.
FREE_QUOTA = [
    {
        "unit": "backtest_runs",
        "limit": 50,
        "used": 4,
        "remaining": 46,
        "resets_at": RESETS_AT,
        "more_plans": FREE_MORE,
    },
    {
        "unit": "backtest_compute_seconds",
        "limit": 1500,
        "used": 120,
        "remaining": 1380,
        "resets_at": RESETS_AT,
        "more_plans": FREE_MORE,
    },
]

#: The whole served key set, exactly (the envelope adds `share_url`).
RESULT_KEYS = {"plan", "plan_name", "period", "quota", "talking_points", "share_url"}


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
        assert env["plan"] == "free" and env["plan_name"] == "Free", where
        assert env["period"] == "weekly", where
        assert env["quota"] == FREE_QUOTA, where
        assert env["talking_points"] == FREE_POINTS, where
        assert env["share_url"] is None, where


@respx.mock
def test_nothing_d12_removed_is_served_anywhere(_api_env, monkeypatch):
    """Whole-output scan, keys AND text: no other plan's NUMBERS, no price, no
    fee, no destination, no marketing, no do-nothing line. Plan names appear
    only as the served plans that include more."""
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
            "higher_plans",
        ):
            assert key not in keys, (profile, client_env, key)
        for number in ("1000", "1,000", "4000", "4,000"):
            assert number not in blob, (profile, client_env, number)
        for name in cat.all_plan_names():
            if name not in ("Free", *FREE_MORE):
                assert name not in blob, (profile, client_env, name)
        for token in (
            "http",
            "settings",
            "billing",
            "checkout",
            "stripe",
            "starter",
            "fee",
            "Doing nothing",
            "account action",
            "$",
        ):
            assert token not in blob, (profile, client_env, token)
        assert not BANNED_PHRASES_RE.search(blob), (profile, client_env)


def test_the_talking_points_pass_the_plan_copy_scan():
    """The points pass the SAME validator and plan-copy scan the walls use,
    with no do-nothing line required (a fact proposes no action)."""
    from keel.errors import assert_plan_copy
    from keel.tools.outcomes._handoff import validate_talking_points

    for point in FREE_POINTS:
        assert assert_plan_copy(point) == point
    assert validate_talking_points(FREE_POINTS, require_do_nothing=False) == FREE_POINTS
    # Control: the default (action walls) still requires the do-nothing line.
    with pytest.raises(ValueError, match="do-nothing"):
        validate_talking_points(FREE_POINTS)


@respx.mock
def test_a_paid_plan_reads_the_same_way(_api_env):
    """The plan name is the caller's OWN plan, and the opening names only the
    plans the server says include more (Trader: Pro, from the catalog); the
    top plan's opening names none (Pro gets nothing)."""
    for plan in ("trader", "pro"):
        block = copy.deepcopy(PLAN_STATUS_BLOCK)
        block["plan"] = plan
        block.pop("plan_name")  # an older server: the id, capitalised
        for q in block["quotas"]:
            if q["unit"] in ("backtest_runs", "backtest_compute_seconds"):
                q["higher_plans"] = _served_higher(plan, q["unit"])
            if q["unit"] == "backtest_runs":
                q.update(limit=1000, used=4, remaining=996)
        respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
        env = _call_mcp()
        assert env["plan"] == plan and env["plan_name"] == cat.display_name(plan)
        assert env["talking_points"][:2] == [
            _opening(plan),
            f"Backtests: 4 of 1,000 used on {cat.display_name(plan)}; resets Sep 29.",
        ], plan
    assert _opening("pro") == "Plan: Pro.", "non-vacuity: the top plan has no clause"


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
    assert env["talking_points"][1] == "Backtests: unlimited."


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
    assert all("reset" not in p.lower() for p in env["talking_points"][1:])


# ─── The first-week allowance (connect-onboarding spec 01 §1.8/§1.9) ─────

ENDS_AT = "2026-10-13T15:02:11Z"  # Tue 13 Oct 2026

#: The first-week clause for the blocks below (Q-7601 fix round): their
#: `remaining` (154) and end date; the exact rendering is pinned once in
#: test_plan_limits_first_week_golden.py.
FIRST_WEEK_CLAUSE = "(154 first-week extras until Oct 13)"
#: The backtests line with the clause folded in.
FIRST_WEEK_POINT = f"Backtests: 50 of 250 used on Free {FIRST_WEEK_CLAUSE}; resets Sep 29."


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
    """§1.9 / Q-7601: always in this tool when the server reports the
    allowance with some left — folded into the backtests line, on every
    surface — and `first_week` rides each unit's block, projected to its
    allow-list."""
    respx.get(f"{API}/v1/me").mock(
        return_value=Response(200, json=_me_response(_first_week_block()))
    )
    for profile, client_env in SURFACES:
        _set_surface(monkeypatch, profile, client_env)
        env = _call_mcp()
        where = (profile, client_env)
        assert set(env) == RESULT_KEYS, where
        assert env["talking_points"] == [FREE_POINTS[0], FIRST_WEEK_POINT], where
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
    assert env["talking_points"] == FREE_POINTS
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
    assert len(env["talking_points"]) == len(FREE_POINTS)
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
        assert len(env["talking_points"]) == len(FREE_POINTS), broken


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
    assert any(FIRST_WEEK_CLAUSE in p for p in env["talking_points"]), env["talking_points"]
    assert env["quota"][0]["first_week"]["remaining"] == 154


def test_an_urgent_first_week_sentence_fails_the_call(monkeypatch):
    """SEEDED arm (in-process): if the renderer ever emitted expiry urgency,
    the tool refuses to serve it — the emit guard fails the call rather than
    letting the text reach a host. Control: the approved sentence passes the
    same two guards in test_policy_scan."""
    import keel.errors as errors
    import keel.tools.outcomes.plan_status as ps

    monkeypatch.setattr(
        errors,
        "render_first_week_clause",
        lambda _block: FIRST_WEEK_CLAUSE[:-1] + "; use them before they expire)",
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
    # compute used = 100 spent + 20 reserved; the balances carry no higher
    # plans, so no name is guessed (the opening is the bare plan line).
    assert env["quota"] == [{k: v for k, v in q.items() if k != "more_plans"} for q in FREE_QUOTA]
    assert env["talking_points"] == ["Plan: Free.", *FREE_POINTS[1:]]


@respx.mock
def test_older_api_without_plan_status_degrades_honestly(_api_env):
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(plan_status=None)))
    env = _call_mcp()
    assert set(env) == RESULT_KEYS
    assert env["plan"] == "free"  # from org.plan
    assert env["period"] is None, "the balances named no period — none is invented"
    assert [q["remaining"] for q in env["quota"]] == [46, 1380]
    assert env["talking_points"] == ["Plan: Free.", "Backtests: 46 of 50 left."]


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


def test_descriptions_name_the_limits_without_numbers_or_plan_names():
    """doc 07 §3.8/§6.1: each profile's description names the limit kinds
    its caller can meet, and never a number, a price or a plan name. The
    listed forms leave out the live strategies (no live tool is listed)."""
    texts = {
        "full": TOOL.description,
        "listed": TOOL.listed_description,
        "listed+paper": TOOL.listed_paper_description,
    }
    for where, text in texts.items():
        for token in ("upgrade", "price", "builder fee", "manage_url", "$"):
            assert token not in text.lower(), (where, token)
        assert not re.search(r"\d", text), (where, text)
        for name in cat.all_plan_names():
            assert name not in text, (where, name)
        assert "reset instant" in text and "which plans include more" in text, where
        assert "timeframe" in text and "spends no quota" in text, where
    # Counts of things, never "slots" (spec 02 §C3.6, PLANS-REVIEW R53).
    assert "live strategies running" in texts["full"]
    assert (
        "live strategies" not in texts["listed"] and "live strategies" not in texts["listed+paper"]
    )
    assert "paper strategies running" in texts["listed+paper"]
    assert "paper strategies" not in texts["listed"]
    for where, text in texts.items():
        assert "slot" not in text.lower(), (where, text)


#: The tools whose call spends one backtest (a screen run is counted as one,
#: keel-api `backtest.screener.run`).
_BACKTEST_SPENDERS = ("keel_backtest_run", "keel_screener_run")


def test_every_tool_that_spends_a_backtest_says_so_once():
    """doc 07 §5 "Descriptions" (Q-7601 fix round): each spender's full and
    listed description carries the ONE allowance clause exactly once, with no
    number and no plan name (§3.8); a read tool beside them carries none.

    The clause REPLACES screener_run's "Each call queues one run, counted as
    a backtest run.": with both, the listed flag-on description total is
    15,026 against F4's 15,000 ceiling (test_listed_screens); with the clause
    alone it is 15,000.

    SEED (run 2026-11-16, reversed by reversing the edit): screener_run's
    description back to "Each call queues one run, counted as a backtest run."
    reds this test alone; the read-tool control stays green."""
    from keel.errors import BACKTEST_ALLOWANCE_CLAUSE

    assert not re.search(r"\d", BACKTEST_ALLOWANCE_CLAUSE)
    assert not any(n in BACKTEST_ALLOWANCE_CLAUSE for n in cat.all_plan_names())
    for name in _BACKTEST_SPENDERS:
        tool = OUTCOMES[name]
        for where, text in (("full", tool.description), ("listed", tool.listed_description)):
            assert text.count(BACKTEST_ALLOWANCE_CLAUSE) == 1, (name, where, text)
    for control in ("keel_backtest_summarize", "keel_screener_view"):
        assert BACKTEST_ALLOWANCE_CLAUSE not in OUTCOMES[control].description, control


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


# ─── The plan change (spec 01 §C6/§C11/§L6): the current plans ──────────

RESETS_30D_AT = "2026-11-29T14:00:00Z"  # Sun 29 Nov 2026, an account's cycle end
BONUS_ENDS_AT = "2026-11-06T14:00:00Z"  # Fri 6 Nov 2026


def _v2_block(*, first_week: bool) -> dict:
    """keel-api's /v1/me `plan_status` for a current-plans Free org, with
    EVERY field §L6 adds (plan_version, clock_floor, the catalog `offers` with
    their names, prices and limits) — so each "not served" assertion below is
    non-vacuous by construction."""
    block = copy.deepcopy(PLAN_STATUS_BLOCK)
    block["plan_version"] = "v2"
    block["clock_floor"] = "1h"
    # The catalog's paid ladder, with its prices and limits (D-10: the API carries numbers).
    block["offers"] = [
        {
            "plan": plan,
            "version": version,
            "name": cat.display_name(plan, version),
            "monthly_price_usd": 49 + 50 * i,
            "annual_monthly_price_usd": 39 + 40 * i,
            "bps": 3,
            "clock_floor": cat.floor_of(plan, version),
            "limits": {
                "backtest_runs": {
                    "limit": int(cat._value(plan, version, "backtest_runs")),
                    "period": "30d",
                }
            },
        }
        for i, (plan, version) in enumerate(cat.paid_rows())
    ]
    runs, compute = block["quotas"][0], block["quotas"][1]
    runs.update(limit=25, used=4, remaining=21, period="30d", resets_at=RESETS_30D_AT)
    compute.update(limit=750, used=120, remaining=630, period="30d", resets_at=RESETS_30D_AT)
    if first_week:
        # What keel-api serves 10 runs / 300 s into the bonus (spend goes to
        # the bonus first): the combined allowance, and in `first_week` the
        # bonus part and the plan's own pot (test_plan_both_pots.py pins the
        # server side of these numbers).
        runs.update(limit=75, used=10, remaining=65)
        runs["first_week"] = {
            "granted": 50,
            "remaining": 40,
            "ends_at": BONUS_ENDS_AT,
            "then_remaining": 25,
            "then_limit": 25,
        }
        compute.update(limit=2250, used=300, remaining=1950)
        compute["first_week"] = {
            "granted": 1500,
            "remaining": 1200,
            "ends_at": BONUS_ENDS_AT,
            "then_remaining": 750,
            "then_limit": 750,
        }
    return block


@respx.mock
def test_the_30_day_cycle_reads_this_30_day_period(_api_env, monkeypatch):
    """§C11: the one new period wording; the result keeps exactly its keys —
    none of §L6's additions (offers, prices, the floor, the version) reach an
    agent surface."""
    respx.get(f"{API}/v1/me").mock(
        return_value=Response(200, json=_me_response(_v2_block(first_week=False)))
    )
    for profile, client_env in SURFACES:
        _set_surface(monkeypatch, profile, client_env)
        env = _call_mcp()
        where = (profile, client_env)
        assert set(env) == RESULT_KEYS, where
        assert env["period"] == "30d", where
        assert env["talking_points"] == [
            FREE_POINTS[0],
            "Backtests: 4 of 25 used on Free; resets Nov 29.",
        ], where
        blob = json.dumps(env)
        for leaked in ("offers", "clock_floor", "plan_version", "49", "199", "1h", "15min"):
            assert leaked not in blob, (where, leaked)
        assert not re.search(r"\bmonth", blob, re.I), where


@respx.mock
def test_both_pots_on_the_30_day_cycle(_api_env, monkeypatch):
    """§C6 / Q-7601: during the signup allowance the backtests line folds
    in what the first-week part leaves and the date it ends; both pots stay
    in the structured `first_week` (the plan's own pot is `then_remaining`,
    read, never subtracted here) and no window phrase is rendered."""
    respx.get(f"{API}/v1/me").mock(
        return_value=Response(200, json=_me_response(_v2_block(first_week=True)))
    )
    for profile, client_env in SURFACES:
        _set_surface(monkeypatch, profile, client_env)
        env = _call_mcp()
        where = (profile, client_env)
        # The v3 rows' first-week sentence (R16): the bonus ends (Nov 6) before the reset (Nov 29), so only the
        # period's own 25 come back.
        assert env["talking_points"][1:] == [
            "Backtests: 10 of 75 used on Free (25 plus your first-week 50); resets to 25 on Nov 29.",
        ], where
        assert env["quota"][0]["first_week"] == {
            "granted": 50,
            "remaining": 40,
            "ends_at": BONUS_ENDS_AT,
            "then_remaining": 25,
            "then_limit": 25,
        }, where


@respx.mock
def test_an_older_server_without_the_plan_pot_keeps_the_first_week_sentence(_api_env):
    """Control: a 30-day first week served WITHOUT `then_*` (a keel-api from
    before both pots) folds the same clause: it reads only the first-week
    part's own `remaining` and end, never the plan pot."""
    block = _v2_block(first_week=True)
    stripped = 0
    for quota in block["quotas"]:
        if "first_week" in quota:
            quota["first_week"].pop("then_remaining")
            quota["first_week"].pop("then_limit")
            stripped += 1
    assert stripped == 2  # non-vacuity: both backtest units carried the pair
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
    env = _call_mcp()
    assert env["talking_points"][1] == (
        "Backtests: 10 of 75 used on Free (25 plus your first-week 50); resets to 25 on Nov 29."
    )
    assert set(env["quota"][0]["first_week"]) == {"granted", "remaining", "ends_at"}


@respx.mock
def test_the_current_plans_render_no_unlimited(_api_env):
    """D-6: no current-plans backtest limit reads "unlimited". CONTROL (the
    older-plan arm, test_an_unlimited_unit_is_named_not_counted above): an
    unlimited unit still reads "unlimited backtest runs"."""
    respx.get(f"{API}/v1/me").mock(
        return_value=Response(200, json=_me_response(_v2_block(first_week=True)))
    )
    env = _call_mcp()
    assert "nlimited" not in json.dumps(env)
    assert all(isinstance(q["limit"], int) for q in env["quota"])


# ─── Every limit kind the caller can meet (doc 07 §5, D-14) ──────────────


def _me_every_kind(plan: str = "free") -> dict:
    """/v1/me as Lane A's keel-api serves it for a v2 org: the serving floor
    and the paper and screens cap balances, each with the catalog's
    `higher_plans` (names AND numbers, so the names-only rule is tested)."""
    me = _me_response()
    floor_higher = cat.faster_plans(plan)
    for entry in floor_higher:
        entry["floor"] = cat.floor_of(entry["plan"], entry["version"])
    me["serving_floor"] = {"floor": cat.floor_of(plan), "higher_plans": floor_higher}
    for unit, used in (
        ("paper_strategies_max", 1),
        ("screeners_live_max", 1),
        ("screeners_published_max", 2),
        ("screeners_private_max", 0),
    ):
        granted = int(cat._value(plan, "v2", unit))
        me["entitlements"].append(
            {
                "unit": unit,
                "type": "cap",
                "granted": granted,
                "cap_current": used,
                "available": granted,
                "higher_plans": _served_higher(plan, unit),
            }
        )
    return me


@respx.mock
def test_every_limit_kind_has_its_line_and_block(_api_env, monkeypatch):
    """The timeframe floor (no longer hidden — D-14 supersedes the clock_floor
    ban), the paper slots and the screens caps each get one line and one
    allow-listed block, on the full profile where all are served.

    SEED (run 2026-11-16): drop `_floor_line` from the handler — this reds
    on the floor line; the backtest lines (control) stay as asserted."""
    monkeypatch.setenv("KEEL_LISTED_SCREENS_HOSTS", "all")
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_every_kind()))
    env = _call_mcp()
    assert set(env) == RESULT_KEYS | {"paper", "screens", "serving_floor"}, sorted(env)
    points = env["talking_points"]
    assert points[:2] == FREE_POINTS
    assert points[2:] == [
        "Paper strategies: 1 of 1 running on Free.",
        "Auto-refreshing screens: 1 of 4 on Free.",
        "Public screens: 2 of 4 on Free.",
        "Private auto-refreshing screens: not on Free.",
        "Paper and live on Free run at 1 hour or slower.",
    ]
    assert env["serving_floor"] == {
        "floor": "1h",
        "more_plans": cat.names(cat.faster_plans("free")),
    }
    assert env["paper"] == {
        "unit": "paper_strategies_max",
        "used": 1,
        "limit": 1,
        "remaining": 0,
        "more_plans": cat.names(cat.higher_plans("free", "paper_strategies_max")),
    }
    assert {k: b["unit"] for k, b in env["screens"].items()} == {
        "live": "screeners_live_max",
        "published": "screeners_published_max",
        "private": "screeners_private_max",
    }
    blob = json.dumps(env)
    assert "higher_plans" not in blob and "15min" not in blob and "5min" not in blob


@respx.mock
def test_the_listed_profile_shows_only_what_it_serves(_api_env, monkeypatch):
    """Control arm: listed, paper and screens off for this host, live never —
    the floor still shows (a backtest can meet it before any deploy), and the
    caps do not."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_PAPER_HOSTS", "")
    monkeypatch.setenv("KEEL_LISTED_SCREENS_HOSTS", "")
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_every_kind()))
    env = _call_mcp()
    assert set(env) == RESULT_KEYS | {"serving_floor"}, sorted(env)
    assert env["talking_points"][-1] == "Paper and live on Free run at 1 hour or slower."
    assert not any("Paper strategies" in p or "screens" in p for p in env["talking_points"])


@respx.mock
def test_live_slots_show_where_the_live_tools_are_loaded(_api_env, monkeypatch):
    """`live` rides only a server that registers keel_live_deploy."""
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,backtest,live-read,live-write")
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    env = _call_mcp()
    assert env["live"] == {"unit": "live_strategies_max", "used": 1, "limit": 1, "remaining": 0}
    assert "Live strategies: 1 of 1 running on Free." in env["talking_points"]
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,backtest")
    assert "live" not in _call_mcp()


@respx.mock
def test_ri589_the_studies_block_has_its_line_and_names_the_plans_that_include_more(_api_env):
    """RI-589 (Research 07 §4.1, 08 §5.2): while research is served, keel-api's plan_status.quotas carries the
    ``research_studies`` block (platform_auth.pricing RESEARCH_QUOTA_UNITS), and keel_plan_usage reports it as every
    metered unit: its projected block with ``more_plans`` (the served names, never their numbers) and the line
    "Studies: 2 of 3 left; resets …". Control: without the block (research off) the output is the Free baseline,
    unchanged. Seeded red (reversed): ``research_studies`` dropped from plan_status._UNITS -> this arm reds; the
    control stays green."""
    block = copy.deepcopy(PLAN_STATUS_BLOCK)
    higher = _served_higher("free", "research_studies")
    assert higher  # non-vacuous: another plan includes more studies
    block["quotas"].append(
        {
            "unit": "research_studies",
            "label": "studies",
            "limit": 3,
            "used": 1,
            "remaining": 2,
            "unlimited": False,
            "period": "30d",
            "resets_at": RESETS_AT,
            "plan": "free",
            "higher_plans": higher,
        }
    )
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(block)))
    env = _call_mcp()
    (studies,) = [q for q in env["quota"] if q["unit"] == "research_studies"]
    assert (studies["limit"], studies["used"], studies["remaining"]) == (3, 1, 2)
    assert studies["more_plans"] == cat.names(cat.higher_plans("free", "research_studies"))
    assert "Studies: 1 of 3 used on Free; resets Sep 29." in env["talking_points"]
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    assert _call_mcp()["talking_points"] == FREE_POINTS  # control: no studies block, no line
