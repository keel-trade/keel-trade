"""Paper trading on the Claude connector (paper-trading-v2 S10 §10; PT2-90).

The listed connector serves ``keel_paper_deploy`` and ``keel_paper_control``
to a caller whose host the listed switch names (FDR-10). This file proves the
runtime half of S10:

* ``keel_paper_control``'s ``update`` action: the route, no body, the version
  label it returns, and every refusal typed (up to date, stopped, known issue,
  server validation passed through);
* the listed forms of its pointers and its live-id guard name only listed
  tools, and its listed result carries no internal identifier (Q-2268);
* the paper wall's listed slot-freeing line (S10 §5), present only where
  ``keel_paper_control`` is served, with HEAD's concurrent-cap clause kept;
* ``keel_plan_usage``'s paper block, present only where paper is served, so a
  ChatGPT session with the switch on for Claude gets HEAD's bytes.

Seeds (each applied by an exact edit and reversed by the inverse edit):

* drop ``"update": ("POST", "/update"),`` from ``paper_control._ACTIONS`` ⇒
  ``test_update_maps_to_the_update_route_with_no_body`` reds (unknown action);
* make ``_paper_walls._slot_points`` return the line unconditionally ⇒
  ``test_paper_wall_slot_line_only_where_control_is_served[claude-*]`` red (the
  flag-off control now carries the line too);
* make ``plan_status._paper_served`` return ``True`` on listed ⇒
  ``test_plan_usage_paper_block_follows_the_host`` reds for all three hosts;
* delete ``LISTED_PAPER_APPEND_ONLY``'s row, or set keel_paper_control's
  ``openWorldHint`` to True ⇒ ``test_claude_on_annotations_follow_the_policy``
  reds. All five were run on 2026-10-13 (lane RCL) and reversed by exact edit;
* the upsell seed is a test of its own (``test_an_upsell_slot_line_is_refused``).
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager

import pytest
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes._toolsets import listed_host_context


_INTERNAL = ("org_id", "account_id", "config_id", "source_hash", "s3_key", "principal_id")


class _Transport:
    """Records (method, path, json) per call and answers from a route table."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, str, object]] = []

    def _answer(self, method, path, body):
        self.calls.append((method, path, body))
        answer = self.routes[(method, path)]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def get(self, path, *, headers=None, **params):
        return self._answer("GET", path, None)

    def post(self, path, json=None, *, headers=None, **params):
        return self._answer("POST", path, json)

    def delete(self, path):
        return self._answer("DELETE", path, None)


def _ctx(transport):
    from keel.tools.outcomes._base import ToolContext

    return ToolContext(api_client=transport, app_url="https://app.usekeel.io")


@contextmanager
def _listed(hosts: str | None, host: str | None):
    """The hosted listed profile with the per-host switch, restored on exit."""
    keys = ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_LISTED_PAPER_HOSTS")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    os.environ["KEEL_EXECUTION_MODE"] = "hosted"
    os.environ.pop("KEEL_LISTED_PAPER_HOSTS", None)
    if hosts:
        os.environ["KEEL_LISTED_PAPER_HOSTS"] = hosts
    try:
        with listed_host_context(host):
            yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _control(args, transport):
    from keel.tools.outcomes import get

    return get("keel_paper_control").handler(args, _ctx(transport))


def _conflict(detail: str):
    return translate_http_error(409, json.dumps({"detail": detail}))


_ROW = {
    "deployment_id": "dep_p",
    "status": "LIVE",
    "deployed_version_string": "v3",
    "venue": {"code": "PAPER", "capital_class": "practice"},
    "org_id": "org_secret",
    "account_id": "acc_secret",
    "config_id": "cfg_secret",
    "source_hash": "abc123",
}


# ── update: route, result, refusals ──────────────────────────────────────


def test_update_maps_to_the_update_route_with_no_body(paper_sdk_released):
    transport = _Transport({("POST", "/v1/paper/dep_p/update"): dict(_ROW)})
    env = _control({"deployment_id": "dep_p", "action": "update"}, transport).to_envelope()
    assert transport.calls == [("POST", "/v1/paper/dep_p/update", None)]
    assert env["action"] == "update"
    assert env["version"] == "v3"
    assert env["new_state"] == "Paper"  # keel-app's word, never the raw LIVE
    assert env["url"] == "https://app.usekeel.io/paper/dep_p"
    # The full profile keeps the route's whole row (its callers script on it).
    assert env["result"]["org_id"] == "org_secret"


def test_update_onto_the_running_version_is_paper_run_up_to_date(paper_sdk_released):
    transport = _Transport(
        {
            ("POST", "/v1/paper/dep_p/update"): _conflict(
                "The paper run is already at the latest version and schedule"
            )
        }
    )
    with pytest.raises(KeelError) as exc:
        _control({"deployment_id": "dep_p", "action": "update"}, transport)
    assert exc.value.error_code == "paper_run_up_to_date"
    assert "nothing was changed" in str(exc.value)
    assert "keel_strategy_compose" in exc.value.suggestion


def test_update_of_a_stopped_run_is_a_state_conflict(paper_sdk_released):
    transport = _Transport(
        {
            ("POST", "/v1/paper/dep_p/update"): _conflict(
                "Cannot update a paper run in status 'STOPPED'. Must be LIVE or PAUSED."
            )
        }
    )
    with pytest.raises(KeelError) as exc:
        _control({"deployment_id": "dep_p", "action": "update"}, transport)
    assert exc.value.error_code == "paper_run_state_conflict"
    assert "is stopped and cannot be updated" in str(exc.value)
    assert "keel_paper_deploy" in exc.value.suggestion


def test_update_onto_a_known_issue_version_names_the_upgrade(paper_sdk_released):
    body = {
        "detail": {
            "detail": "Update blocked: this strategy uses components with a known issue.",
            "error": {
                "code": "KNOWN_ISSUE_UPGRADE_REQUIRED",
                "tier": "gate",
                "action": "update",
                "strategy_id": "str_9",
                "known_issues": [{"id": "Q-2448", "summary": "stale price"}],
                "components": [{"name": "MaxDrawdownStopLoss", "version": 1}],
            },
        }
    }
    transport = _Transport(
        {("POST", "/v1/paper/dep_p/update"): translate_http_error(422, json.dumps(body))}
    )
    with pytest.raises(KeelError) as exc:
        _control({"deployment_id": "dep_p", "action": "update"}, transport)
    assert exc.value.error_code == "known_issue_upgrade_required"


def test_update_validation_refusal_passes_through_with_the_servers_message(paper_sdk_released):
    original = translate_http_error(
        422, json.dumps({"detail": "Strategy failed validation: SLOT_TYPE_MISMATCH at step 3"})
    )
    transport = _Transport({("POST", "/v1/paper/dep_p/update"): original})
    with pytest.raises(KeelError) as exc:
        _control({"deployment_id": "dep_p", "action": "update"}, transport)
    assert exc.value is original
    assert "SLOT_TYPE_MISMATCH" in str(exc.value)


# ── listed forms (the switch on for Claude) ──────────────────────────────


def test_listed_result_carries_no_internal_identifier(paper_sdk_released):
    transport = _Transport({("POST", "/v1/paper/dep_p/update"): dict(_ROW)})
    with _listed("claude", "claude"):
        env = _control({"deployment_id": "dep_p", "action": "update"}, transport).to_envelope()
    leaked = [k for k in _INTERNAL if k in json.dumps(env)]
    assert not leaked, leaked
    # Non-vacuity: the projection kept the run's facts.
    assert env["result"]["deployment_id"] == "dep_p" and env["version"] == "v3"


def test_listed_guard_and_pointers_name_only_listed_tools(paper_sdk_released):
    from keel.tools.outcomes._toolsets import listed_profile_tools

    not_found = translate_http_error(404, json.dumps({"detail": "Not found"}))
    live_row = {"deployment_id": "dep_live", "venue": {"code": "HL", "capital_class": "real"}}
    transport = _Transport(
        {
            ("POST", "/v1/paper/dep_live/pause"): not_found,
            ("GET", "/v1/deployments/dep_live"): live_row,
            ("POST", "/v1/paper/dep_none/pause"): not_found,
            ("GET", "/v1/deployments/dep_none"): not_found,
        }
    )
    with _listed("claude", "claude"):
        served = listed_profile_tools()
        with pytest.raises(KeelError) as live:
            _control({"deployment_id": "dep_live", "action": "pause"}, transport)
        with pytest.raises(KeelError) as missing:
            _control({"deployment_id": "dep_none", "action": "pause"}, transport)
    assert live.value.error_code == "invalid_target"
    assert "keel_paper_control" in served
    for err in (live.value, missing.value):
        text = f"{err} {err.suggestion}"
        names = {w.strip("`.,;:()") for w in text.split() if w.strip("`.,;:()").startswith("keel_")}
        assert names, f"no tool named in {text!r}; the arm proves nothing"
        assert names <= served, sorted(names - served)
        assert "deployment" not in text.replace("deployment_id", "")
    # Control arm: the full profile still names keel_live_control.
    with pytest.raises(KeelError) as full:
        _control({"deployment_id": "dep_live", "action": "pause"}, transport)
    assert "keel_live_control" in full.value.suggestion


# ── the paper wall's listed slot-freeing line (S10 §5) ──────────────────


_FREE_403 = (
    '{"type": "about:blank", "title": "Forbidden", "status": 403, '
    '"detail": "Insufficient entitlements", '
    '"reasons": ["entitlement:cap_exceeded:paper_strategies_max:limit=1:current=1:plan=free"]}'
)
_PAID_403 = _FREE_403.replace("limit=1:current=1:plan=free", "limit=5:current=5:plan=starter")


def _wall(body: str):
    from keel.tools.outcomes._paper_walls import paper_wall

    e = translate_http_error(403, body)
    return paper_wall(e, blocked_action="paper_deploy", retry_call={"tool": "x", "args": {}})


@pytest.mark.parametrize("body", [_FREE_403, _PAID_403], ids=["free", "paid"])
@pytest.mark.parametrize("host", ["claude", "chatgpt"])
def test_paper_wall_slot_line_only_where_control_is_served(paper_sdk_released, body, host):
    from keel.tools.outcomes._paper_strings import PAPER_WALL_SLOT_LINE

    line = PAPER_WALL_SLOT_LINE.form(listed=True)
    with _listed("claude", host):
        wall = _wall(body).to_envelope()
    with _listed(None, None):
        head = _wall(body).to_envelope()
    points = wall["talking_points"]
    if host == "claude":
        assert points == [*head["talking_points"], line]
    else:
        assert wall == head  # ChatGPT: HEAD's wall, byte for byte
    # The cap's instruction (doc 07 §4) is kept on every form.
    assert "before a slot frees" in wall["what_was_expected"]
    assert all("upgrade" not in p.lower() for p in points)


def test_an_upsell_slot_line_is_refused(paper_sdk_released, monkeypatch):
    """Seed: an upsell word in the paper wall fails the D-12 scan loudly."""
    import keel.tools.outcomes._paper_walls as walls
    from keel.tools.outcomes._paper_strings import PaperString

    monkeypatch.setattr(
        walls,
        "PAPER_WALL_SLOT_LINE",
        PaperString(key="seed", context="seed", full=None, listed="Upgrade to unlock more slots."),
    )
    with _listed("claude", "claude"), pytest.raises(ValueError, match="upsell|upgrade|unlock"):
        _wall(_FREE_403)


# ── keel_plan_usage's paper block (S10 §5) ──────────────────────────────


_ME = {
    "plan_status": {
        "plan": "free",
        "quotas": [
            {"unit": "backtest_runs", "limit": 50, "used": 4, "remaining": 46, "period": "weekly"},
        ],
    },
    "entitlements": [
        {
            "unit": "paper_strategies_max",
            "type": "cap",
            "granted": 1,
            "cap_current": 1,
            "spent": 0,
            "reserved": 0,
            "available": 1,
        },
    ],
}


def _usage():
    from keel.tools.outcomes import get

    transport = _Transport({("GET", "/v1/me"): json.loads(json.dumps(_ME))})
    return get("keel_plan_usage").handler({}, _ctx(transport)).to_envelope()


@pytest.mark.parametrize("host", ["claude", "chatgpt", None])
def test_plan_usage_paper_block_follows_the_host(paper_sdk_released, host):
    with _listed("claude", host):
        got = _usage()
    with _listed(None, None):
        head = _usage()
    assert "paper" not in head
    if host == "claude":
        assert got["paper"] == {
            "unit": "paper_strategies_max",
            "limit": 1,
            "used": 1,
            "remaining": 0,
        }
        assert got["talking_points"][-1] == "Paper strategies: 1 of 1 running."
    else:
        assert got == head  # ChatGPT and unknown clients: HEAD's bytes


def test_plan_usage_paper_block_on_the_full_profile_follows_the_sdk_release(
    monkeypatch, paper_sdk_released
):
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    assert "paper" in _usage()  # released (PT2-91): the full profile reports slots


def test_plan_usage_paper_block_on_the_full_profile_once_released(paper_sdk_released, monkeypatch):
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    assert _usage()["paper"]["remaining"] == 0


# ── Claude-on arms of the listed sweeps (X-1) ──────────────────────────────
#
# The listed annotation, description-shape and isError gates iterate the
# static 29; these arms run the same rules over the surface a Claude session
# is served (31), so the two paper tools cannot escape them. Seeds: set
# keel_paper_control's openWorldHint back to True ⇒ the annotation arm reds on
# the open-world set; delete keel_paper_deploy's LISTED_PAPER_APPEND_ONLY row
# ⇒ it reds on the append-only set; pad PAPER_CONTROL_LISTED_DESCRIPTION by
# 400 chars ⇒ the total arm reds.

#: The project's listed description ceiling (13,000 chars, ratified
#: 2026-09-23) was set for the 29-tool surface, which measures 12,958 at this
#: commit. The Claude-on surface (31 tools, plus the paper sentences on
#: monitor, share, status and plan usage) measures above it; its ceiling is a
#: founder decision (lane RCL, S10). Until then this arm pins the measured
#: surface so it cannot grow unnoticed (14,839 after the S10 review's monitor
#: fix and backticked tool names; 14,671 before).
CLAUDE_ON_LISTED_TOTAL_PIN = 14_850


@contextmanager
def _claude_on_surface():
    """(served names, {name: mcp Tool}) off a real server, Claude host, switch on."""
    import asyncio

    from keel.mcp.server import create_server
    from keel.tools.outcomes._toolsets import listed_profile_tools

    from tests.test_policy_scan import listed_flag_state

    with listed_flag_state(True, "claude_on-claude"):
        tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
        yield listed_profile_tools(), tools


def test_claude_on_annotations_follow_the_policy():
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._annotation_justifications import (
        served_append_only,
        served_destructive_tools,
        served_justifications,
        served_open_world_tools,
    )
    from keel.tools.outcomes._mcp_adapter import effective_annotations

    with _claude_on_surface() as (served, tools):
        assert len(served) == 31 and set(tools) == served
        rows = served_justifications()
        ann = {n: effective_annotations(OUTCOMES[n]) for n in served}
        open_world, destructive = served_open_world_tools(), served_destructive_tools()
        append_only = served_append_only()
    assert set(rows) == served, sorted(set(served) ^ set(rows))
    assert all(all(f.strip() for f in row) for row in rows.values())
    for name, a in ann.items():
        assert isinstance(a.get("title"), str) and 0 < len(a["title"]) <= 60, name
        for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert isinstance(a.get(hint), bool), (name, hint)
    titles = [a["title"] for a in ann.values()]
    assert len(titles) == len(set(titles))
    assert {n for n, a in ann.items() if a["openWorldHint"]} == open_world
    assert {n for n, a in ann.items() if a["destructiveHint"]} == destructive
    writes = {n for n, a in ann.items() if not a["readOnlyHint"]}
    assert set(append_only) == writes - destructive
    for name in ("keel_paper_deploy", "keel_paper_control"):
        assert "Simulated" in ann[name]["title"]
        t = tools[name]
        wire = json.dumps(
            [t.description, t.parameters, t.annotations.model_dump(mode="json")], default=str
        )
        assert not any(f in wire for f in rows[name]), f"{name}: a justification reached the wire"


def test_claude_on_descriptions_fit_the_shape_and_the_pinned_total():
    from pipeline_engine.reference.system import assemble

    with _claude_on_surface() as (_served, tools):
        descriptions = {n: t.description or "" for n, t in tools.items()}
    violations, sentences = assemble.description_shape_violations(descriptions, listed=True)
    assert not violations, "\n".join(violations)
    report = assemble.description_size_report(descriptions)
    assert report["count"] == 31 and sentences >= 100
    assert not report["over_hard_bytes"], report["over_hard_bytes"]
    print(f"AIM  claude-on listed total: {report['total_chars']} chars across 31 tools")
    assert report["total_chars"] <= CLAUDE_ON_LISTED_TOTAL_PIN, report["total_chars"]


def test_claude_on_paper_tool_errors_set_is_error_and_name_only_served_tools():
    import asyncio
    from unittest import mock

    from fastmcp import Client
    from keel.mcp.server import create_server
    from keel.tools.outcomes._toolsets import listed_profile_tools

    from tests.test_error_results_set_is_error import _envelope, _not_found
    from tests.test_policy_scan import listed_flag_state

    calls = [
        ("keel_paper_control", {"deployment_id": "", "action": "pause"}),
        ("keel_paper_control", {"deployment_id": "dep_x", "action": "launch"}),
        ("keel_paper_control", {"deployment_id": "dep_x", "action": "update"}),
        ("keel_paper_control", {"not_an_argument_q2273": 1}),
        ("keel_paper_deploy", {"strategy_id": ""}),
        ("keel_paper_deploy", {"not_an_argument_q2273": 1}),
    ]

    async def run():
        out = []
        async with Client(create_server()) as client:
            for name, args in calls:
                out.append((name, await client.call_tool(name, args, raise_on_error=False)))
        return out

    with (
        listed_flag_state(True, "claude_on-claude"),
        mock.patch("keel.client.KeelClient._request", _not_found),
    ):
        served = listed_profile_tools()
        results = asyncio.run(run())
    assert len(results) == len(calls)
    for name, result in results:
        env = _envelope(result)
        assert result.is_error and isinstance(env.get("code"), str), (name, env)
        named = set(re.findall(r"\bkeel_[a-z_]+\b", json.dumps(env)))
        assert named <= served, (name, sorted(named - served))


# ── server errors, the deploy pointer and the listed known-issue form ─────
# (S10 review F3, F5, W2). Seed: make `paper_server_error` return None ⇒ the
# two 5xx arms red (the server's sentence and the paper read-back are lost).


def _server_error(method: str, detail: str):
    from keel.errors import write_unconfirmed

    return write_unconfirmed(method, translate_http_error(503, json.dumps({"detail": detail})))


@pytest.mark.parametrize("host", [None, "claude"])
def test_a_paper_update_503_keeps_the_servers_sentence(paper_sdk_released, host):
    detail = "Paper update could not be finalized. The run is paused safely; retry the same update."
    transport = _Transport({("POST", "/v1/paper/dep_p/update"): _server_error("POST", detail)})
    with _listed("claude", host) if host else _listed(None, None):
        with pytest.raises(KeelError) as exc:
            _control({"deployment_id": "dep_p", "action": "update"}, transport)
    err = exc.value
    assert err.error_code == "server_error"
    assert str(err) == detail and err.retryable is True
    assert err.suggestion == "`keel_live_monitor` reads the run's current state."


@pytest.mark.parametrize("detail", ["An unexpected error occurred.", "Internal Server Error"])
def test_a_paper_control_500_keeps_the_write_unconfirmed_message(paper_sdk_released, detail):
    """An unhandled 500 is not the route's fixed prose (S10 re-review R1): the
    agent keeps "may still have been applied" rather than a generic body."""
    from keel.errors import write_unconfirmed

    failure = write_unconfirmed("POST", translate_http_error(500, json.dumps({"detail": detail})))
    transport = _Transport({("POST", "/v1/paper/dep_p/pause"): failure})
    with _listed("claude", "claude"):
        with pytest.raises(KeelError) as exc:
            _control({"deployment_id": "dep_p", "action": "pause"}, transport)
    err = exc.value
    assert err.error_code == "server_error"
    assert "may still have been applied" in str(err)
    assert detail not in str(err)


@pytest.mark.parametrize(
    ("hosted", "pointer"),
    [(False, "keel_deployments_list"), (True, "keel_live_monitor")],
    ids=["full", "claude"],
)
def test_a_paper_deploy_5xx_points_at_the_run_list(paper_sdk_released, hosted, pointer):
    from keel.tools.outcomes import get

    transport = _Transport({("POST", "/v1/paper"): _server_error("POST", "boom")})
    with _listed("claude", "claude") if hosted else _listed(None, None):
        if not hosted:
            os.environ.pop("KEEL_SERVER_PROFILE")
        with pytest.raises(KeelError) as exc:
            get("keel_paper_deploy").handler({"strategy_id": "str_1"}, _ctx(transport))
    err = exc.value
    assert err.error_code == "server_error" and err.retryable is False
    assert "may still have been applied" in str(err)  # a resend may start a second run
    assert pointer in err.suggestion and "call again only if none did" in err.suggestion


def test_a_listed_deploy_without_an_id_points_at_a_served_tool(paper_sdk_released):
    from keel.tools.outcomes import get
    from keel.tools.outcomes._toolsets import listed_profile_tools

    transport = _Transport({("POST", "/v1/paper"): {"status": "DEPLOYING"}})
    with _listed("claude", "claude"):
        served = listed_profile_tools()
        with pytest.raises(KeelError) as exc:
            get("keel_paper_deploy").handler({"strategy_id": "str_1"}, _ctx(transport))
    assert exc.value.error_code == "unexpected_response"
    named = set(re.findall(r"\bkeel_[a-z_]+\b", f"{exc.value} {exc.value.suggestion}"))
    assert named and named <= served, sorted(named - served)


def test_the_listed_known_issue_refusal_is_neutral(paper_sdk_released):
    body = {
        "detail": {
            "detail": "Update blocked: this strategy uses components with a known issue.",
            "error": {
                "code": "KNOWN_ISSUE_UPGRADE_REQUIRED",
                "action": "update",
                "strategy_id": "str_9",
                "known_issues": [{"id": "Q-2448", "summary": "stale price"}],
                "components": [
                    {"name": "MaxDrawdownStopLoss", "version": 1, "known_issue": "Q-2448"}
                ],
                "upgrade_with": ["keel_strategy_upgrade", "keel strategy upgrade"],
            },
        }
    }
    transport = _Transport(
        {("POST", "/v1/paper/dep_p/update"): translate_http_error(422, json.dumps(body))}
    )
    with _listed("claude", "claude"):
        with pytest.raises(KeelError) as exc:
            _control({"deployment_id": "dep_p", "action": "update"}, transport)
        env = exc.value.to_envelope()
    text = json.dumps(env)
    assert env["code"] == "known_issue_upgrade_required"
    assert "MaxDrawdownStopLoss v1" in text and "stale price" in text
    assert (
        "Q-2448" not in text
        and "upgrade" not in text.replace("known_issue_upgrade_required", "").lower()
    )
    assert "keel_strategy_compose" in text and "keel strategy" not in text
