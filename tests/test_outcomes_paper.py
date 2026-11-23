"""Paper SDK surfaces (paper-trading-v2 S06 §5). The paper SDK is committed
released (``PAPER_SDK_RELEASED = True``, PT2-91); arms that drive a paper tool
or a paper touch-up ask for the ``paper_sdk_released`` fixture, which asserts
that state (``tests/_paper_sdk.py``).

This first arm proves the fixture: with the committed constant the paper
toolset is in the default set and the registry holds both paper tools.
"""

from __future__ import annotations

import json
import re

import keel.tools.outcomes as outcomes
import keel.tools.outcomes._toolsets as toolsets
import pytest


def test_fixture_releases_the_sdk(paper_sdk_released):
    assert toolsets.PAPER_SDK_RELEASED is True
    assert toolsets.PAPER_TOOLSET in toolsets._DEFAULT_TOOLSETS


# ─── keel_paper_deploy (M8-paper-deploy-tool; S06 §3.2, AC-06-1, IC-06-1) ──
#
# Driven through the real MCP server (full profile, local, API key) with
# `KeelClient`'s verbs faked: `post` answers as IC-01-9's route, every other
# verb records and fails. Seed (AC-06-1): drop the `remaining` relay in
# paper_deploy.py ⇒ test_paper_deploy_happy_path reds on
# `remaining.paper_slots`. Count: one request recorded, nine IC-06-1 keys
# asserted present.

#: IC-06-1's nine response keys peers rely on.
IC_06_1_KEYS = (
    "deployment_id",
    "venue",
    "status",
    "strategy_id",
    "schedule",
    "simulated_capital",
    "url",
    "remaining",
    "disclosures",
)

_PAPER_CREATE_RESPONSE = {
    "deployment_id": "dep_x",
    "strategy_id": "strat_y",
    "name": "Hype carry",
    "status": "LIVE",
    "schedule": "0 */4 * * *",
    "venue": {"code": "PAPER", "capital_class": "practice"},
    "remaining": {"paper_slots": 0},
}


class _RecordingClient:
    """`KeelClient`'s HTTP verbs, faked: POST /v1/paper answers; anything else
    is recorded and fails, so an extra request cannot pass unseen."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict]] = []

    def install(self, monkeypatch) -> None:
        from keel.client import KeelClient

        recorder = self

        def post(_self, path, json=None, **params):
            recorder.requests.append(("POST", path, {"json": json, "params": params}))
            if path == "/v1/paper":
                return dict(recorder.response)
            raise AssertionError(f"unexpected POST {path}")

        def other(method):
            def verb(_self, path, *args, **kwargs):
                recorder.requests.append((method, path, {"args": args, "kwargs": kwargs}))
                raise AssertionError(f"unexpected {method} {path}")

            return verb

        monkeypatch.setattr(KeelClient, "post", post)
        for method in ("get", "patch", "put", "delete"):
            monkeypatch.setattr(KeelClient, method, other(method.upper()))


def _local_full_env(monkeypatch, tmp_path) -> None:
    for key in (
        "KEEL_SERVER_PROFILE",
        "KEEL_EXECUTION_MODE",
        "KEEL_TOOLSETS",
        "KEEL_LISTED_PAPER",
        "KEEL_APP_URL",
        "KEEL_ALLOW_DIRECT_DEPLOY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KEEL_API_KEY", "keel_paper_deploy_test")
    monkeypatch.setenv("KEEL_ANON_AUTO", "0")
    monkeypatch.setattr("keel.config.CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr("keel.config.CONFIG_DIR", tmp_path)


def _call_paper_deploy(args: dict) -> tuple[dict, str]:
    import asyncio
    import json

    from keel.mcp.server import create_server

    result = asyncio.run(create_server().call_tool("keel_paper_deploy", args))
    text = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
    envelope = json.loads(text)
    return envelope, text


def test_paper_deploy_happy_path(paper_sdk_released, monkeypatch, tmp_path):
    from keel.tools.outcomes import _paper_strings as ps
    from keel.tools.outcomes.paper_deploy import IDLE_AUTO_PAUSE_DAYS, OVER_CAP_GRACE_DAYS

    _local_full_env(monkeypatch, tmp_path)
    client = _RecordingClient(_PAPER_CREATE_RESPONSE)
    client.install(monkeypatch)

    envelope, text = _call_paper_deploy(
        {"strategy_id": "strat_y", "schedule": "0 */4 * * *", "simulated_capital": 10000}
    )

    assert client.requests == [
        (
            "POST",
            "/v1/paper",
            {
                "json": {
                    "strategy_id": "strat_y",
                    "simulated_capital": 10000,
                    "schedule": "0 */4 * * *",
                },
                "params": {},
            },
        )
    ], client.requests
    # The route's `remaining` object is relayed (the AC-06-1 seed reds here).
    assert (envelope.get("remaining") or {}).get("paper_slots") == 0, envelope.get("remaining")
    present = [key for key in IC_06_1_KEYS if key in envelope]
    assert present == list(IC_06_1_KEYS), (
        f"missing IC-06-1 keys: {set(IC_06_1_KEYS) - set(present)}"
    )
    assert envelope["deployment_id"] == "dep_x"
    assert envelope["venue"] == {"code": "PAPER", "capital_class": "practice"}
    assert envelope["venue"]["capital_class"] == "practice"
    # keel-app's word for the run's status, never the raw LIVE (QA-W2).
    assert envelope["status"] == "Paper"
    assert envelope["strategy_id"] == "strat_y"
    assert envelope["schedule"] == "0 */4 * * *"
    assert envelope["simulated_capital"] == 10000
    assert envelope["url"].endswith("/paper/dep_x")
    assert envelope["hero_url"] == envelope["url"]
    assert envelope["disclosures"] == {"idle_auto_pause_days": 28, "over_cap_grace_days": 7}
    assert "mode" not in envelope  # the venue block replaces v1's mode (PT2-5)

    notes = envelope["run_notes"]
    fill_line = ps.DEPLOY_FILL_DISCLOSURE.full
    pause_line = ps.DEPLOY_AUTO_PAUSE_DISCLOSURE.full.format(
        idle_days=IDLE_AUTO_PAUSE_DAYS, grace_days=OVER_CAP_GRACE_DAYS
    )
    assert notes.splitlines() == [fill_line, pause_line, ps.LADDER_LINE.full]
    assert "simulated" in notes
    assert "Backtest → Paper → Live" in notes
    # The text block is the envelope a text host reads; it carries the notes.
    assert json.loads(text)["run_notes"] == notes


def test_paper_deploy_omits_remaining_the_route_did_not_send(
    paper_sdk_released, monkeypatch, tmp_path
):
    """Control for the seed: `remaining` is relayed, never invented."""
    _local_full_env(monkeypatch, tmp_path)
    response = {k: v for k, v in _PAPER_CREATE_RESPONSE.items() if k != "remaining"}
    client = _RecordingClient(response)
    client.install(monkeypatch)

    envelope, _ = _call_paper_deploy({"strategy_id": "strat_y"})

    assert len(client.requests) == 1
    assert client.requests[0][2]["json"] == {"strategy_id": "strat_y", "simulated_capital": 10000}
    assert "remaining" not in envelope
    assert envelope["simulated_capital"] == 10000


def test_paper_deploy_listed_profile_renders_the_listed_lines(paper_sdk_released, monkeypatch):
    """The handler's text follows the profile at call time (§3.4.6)."""
    from unittest.mock import MagicMock

    from keel.tools.outcomes import _paper_strings as ps
    from keel.tools.outcomes import get
    from keel.tools.outcomes._base import ToolContext

    client = MagicMock()
    client.post.return_value = dict(_PAPER_CREATE_RESPONSE)
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io")
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    env = get("keel_paper_deploy").handler({"strategy_id": "strat_y"}, ctx).to_envelope()
    assert env["url"] == "https://app.usekeel.io/paper/dep_x"
    assert env["run_notes"].splitlines()[0] == ps.DEPLOY_FILL_DISCLOSURE.listed
    assert env["run_notes"].splitlines()[-1] == ps.LADDER_LINE.listed


def test_paper_deploy_refuses_a_capital_outside_the_presets(paper_sdk_released):
    from unittest.mock import MagicMock

    from keel.errors import KeelError
    from keel.tools.outcomes import get
    from keel.tools.outcomes._base import ToolContext

    client = MagicMock()
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io")
    for bad in (5000, True, "10000"):
        # The declared enum refuses it before the handler (UsageError); the
        # route is the one validator past that (§3.2.4).
        with pytest.raises(KeelError):
            get("keel_paper_deploy").handler(
                {"strategy_id": "strat_y", "simulated_capital": bad}, ctx
            )
    assert client.post.call_count == 0


def test_paper_deploy_registration(paper_sdk_released):
    """§3.2.1: name, CLI path, action, toolset, annotations, titles."""
    from keel.tools.outcomes import get

    tool = get("keel_paper_deploy")
    assert tool.cli_path == ("paper", "deploy")
    assert tool.required_action == "backtest.create"
    assert tool.toolset == "paper"
    assert tool.confirm_in_cli is False
    assert tool.annotations == {
        "title": "Paper-Trade a Strategy (Simulated)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        # Bounded to the caller's own workspace (the annotation policy; S10,
        # PT2-90 aligned both paper tools with keel_backtest_run).
        "openWorldHint": False,
    }
    assert tool.listed_title == "Start Paper Run (Simulated)"
    first_sentence = tool.description.split(". ")[0]
    assert "simulated" in first_sentence
    assert len(tool.listed_description or "") >= 200
    props = tool.input_schema["properties"]
    assert set(props) == {"strategy_id", "schedule", "simulated_capital", "config_id"}
    assert props["simulated_capital"]["enum"] == [1000, 10000, 100000]
    assert props["simulated_capital"]["default"] == 10000
    assert tool.input_schema["required"] == ["strategy_id"]


# ─── keel_paper_control + the live guard (M8-paper-control; S06 §3.6, AC-06-13) ─
#
# The transport is a recording fake client: every call is recorded with its
# method, path and per-request headers, and answers from a route table (a
# value, or an exception to raise). Seeds (AC-06-13): drop the `headers`
# argument from _paper_guard's detail read ⇒
# test_paper_control_guard_names_keel_live_control_for_a_live_row reds on the
# header assertion (and the live mirror with it); return the old id as the
# restart's deployment_id ⇒ test_paper_control_restart_returns_the_new_run
# reds. Count: four action routes and two guard directions exercised.

PAPER_CAP_403_BODY = (
    '{"type": "about:blank", "title": "Forbidden", "status": 403, '
    '"detail": "Insufficient entitlements", '
    '"reasons": ["entitlement:cap_exceeded:paper_strategies_max:limit=1:current=1"]}'
)
NOT_FOUND_BODY = (
    '{"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not found"}'
)
TELEMETRY = {"X-Keel-Agent-Telemetry": "1"}


class _FakeTransport:
    """A `KeelClient` stand-in that records (method, path, headers) per call."""

    def __init__(self, routes: dict[tuple[str, str], object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict | None]] = []

    def _answer(self, method: str, path: str, headers: dict | None):
        self.calls.append((method, path, headers))
        answer = self.routes[(method, path)]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def get(self, path, *, headers=None, **params):
        return self._answer("GET", path, headers)

    def post(self, path, json=None, *, headers=None, **params):
        return self._answer("POST", path, headers)

    def delete(self, path):
        return self._answer("DELETE", path, None)


@pytest.fixture
def arming_spy(monkeypatch):
    """`assert_armed_for_account` must never be called by a paper tool."""
    calls: list[object] = []
    monkeypatch.setattr(
        "keel.permissions.assert_armed_for_account", lambda *a, **k: calls.append((a, k))
    )
    return calls


def _paper_ctx(transport):
    from keel.tools.outcomes._base import ToolContext

    return ToolContext(api_client=transport, app_url="https://app.usekeel.io")


@pytest.mark.parametrize(
    ("action", "method", "path"),
    [
        ("pause", "POST", "/v1/paper/dep_p/pause"),
        ("resume", "POST", "/v1/paper/dep_p/resume"),
        ("stop", "DELETE", "/v1/paper/dep_p"),
    ],
)
def test_paper_control_maps_each_action_to_its_route(
    paper_sdk_released, arming_spy, action, method, path
):
    from keel.tools.outcomes import get

    transport = _FakeTransport({(method, path): {"deployment_id": "dep_p", "status": "PAUSED"}})
    env = (
        get("keel_paper_control")
        .handler({"deployment_id": "dep_p", "action": action}, _paper_ctx(transport))
        .to_envelope()
    )
    assert transport.calls == [(method, path, None)]
    assert env["action"] == action
    assert env["deployment_id"] == "dep_p"
    assert env["url"] == "https://app.usekeel.io/paper/dep_p"
    assert arming_spy == []


def test_paper_control_restart_returns_the_new_run(paper_sdk_released, arming_spy):
    from keel.tools.outcomes import get

    transport = _FakeTransport(
        {
            ("POST", "/v1/paper/dep_old/restart"): {
                "deployment_id": "dep_new",
                "status": "LIVE",
                "venue": {"code": "PAPER", "capital_class": "practice"},
                "restarted_from": "dep_old",
            }
        }
    )
    env = (
        get("keel_paper_control")
        .handler({"deployment_id": "dep_old", "action": "restart"}, _paper_ctx(transport))
        .to_envelope()
    )
    assert transport.calls == [("POST", "/v1/paper/dep_old/restart", None)]
    assert env["deployment_id"] == "dep_new"
    assert env["url"].endswith("/paper/dep_new")
    assert env["hero_url"] == env["url"]
    assert env["restarted_from"] == "dep_old"
    assert env["venue"]["capital_class"] == "practice"
    assert arming_spy == []


def test_paper_control_registration(paper_sdk_released):
    from keel.tools.outcomes import get

    tool = get("keel_paper_control")
    assert tool.cli_path == ("paper", "control")
    assert tool.required_action == "backtest.create"
    assert tool.toolset == "paper"
    assert tool.confirm_in_cli is True
    assert tool.annotations["destructiveHint"] is True
    assert tool.annotations["title"] == "Control a Paper Run (Simulated)"
    assert "simulated" in tool.description
    props = tool.input_schema["properties"]
    assert props["action"]["enum"] == ["pause", "resume", "stop", "restart", "update"]
    assert tool.annotations["openWorldHint"] is False
    assert tool.input_schema["required"] == ["deployment_id", "action"]


def test_paper_control_resume_denial_relays_the_neutral_quota_envelope(
    paper_sdk_released, arming_spy
):
    from keel.errors import translate_http_error
    from keel.tools.outcomes import get
    from keel.tools.outcomes._handoff import HandoffRequired

    transport = _FakeTransport(
        {("POST", "/v1/paper/dep_p/resume"): translate_http_error(403, PAPER_CAP_403_BODY)}
    )
    with pytest.raises(HandoffRequired) as exc:
        get("keel_paper_control").handler(
            {"deployment_id": "dep_p", "action": "resume"}, _paper_ctx(transport)
        )
    env = exc.value.to_envelope()
    assert env["blocked_action"] == "paper_control"
    assert env["limit_details"]["unit"] == "paper_strategies_max"
    assert (env["limit_details"]["limit"], env["limit_details"]["current"]) == (1, 1)
    assert "action_url" not in env
    assert transport.calls == [("POST", "/v1/paper/dep_p/resume", None)]
    assert arming_spy == []


def test_paper_control_guard_names_keel_live_control_for_a_live_row(paper_sdk_released, arming_spy):
    from keel.errors import KeelError, translate_http_error
    from keel.tools.outcomes import get

    transport = _FakeTransport(
        {
            ("POST", "/v1/paper/dep_live/pause"): translate_http_error(404, NOT_FOUND_BODY),
            ("GET", "/v1/deployments/dep_live"): {
                "deployment_id": "dep_live",
                "venue": {"code": "HYPERLIQUID", "capital_class": "real"},
            },
        }
    )
    with pytest.raises(KeelError) as exc:
        get("keel_paper_control").handler(
            {"deployment_id": "dep_live", "action": "pause"}, _paper_ctx(transport)
        )
    # The guard read carries the telemetry marker through `headers`, and no
    # header-less read happens on the error path.
    assert transport.calls == [
        ("POST", "/v1/paper/dep_live/pause", None),
        ("GET", "/v1/deployments/dep_live", TELEMETRY),
    ]
    assert exc.value.error_code == "invalid_target"
    assert exc.value.suggestion == "use keel_live_control"
    assert arming_spy == []


def test_paper_control_guard_lets_a_true_404_stand(paper_sdk_released):
    from keel.errors import NotFoundError, translate_http_error
    from keel.tools.outcomes import get

    for detail in (
        translate_http_error(404, NOT_FOUND_BODY),  # no such row at all
        {"deployment_id": "dep_p", "venue": {"code": "PAPER", "capital_class": "practice"}},
    ):
        transport = _FakeTransport(
            {
                ("DELETE", "/v1/paper/dep_p"): translate_http_error(404, NOT_FOUND_BODY),
                ("GET", "/v1/deployments/dep_p"): detail,
            }
        )
        with pytest.raises(NotFoundError):
            get("keel_paper_control").handler(
                {"deployment_id": "dep_p", "action": "stop"}, _paper_ctx(transport)
            )
        assert transport.calls[-1] == ("GET", "/v1/deployments/dep_p", TELEMETRY)


def test_live_control_mirrors_the_guard_for_a_practice_row(paper_sdk_released, monkeypatch):
    from keel.errors import KeelError, translate_http_error
    from keel.tools.outcomes import get

    monkeypatch.setattr("keel.permissions.assert_armed_for_account", lambda *a, **k: None)
    transport = _FakeTransport(
        {
            ("POST", "/v1/deployments/dep_paper/pause"): translate_http_error(404, NOT_FOUND_BODY),
            ("GET", "/v1/deployments/dep_paper"): {
                "deployment_id": "dep_paper",
                "venue": {"code": "PAPER", "capital_class": "practice"},
            },
        }
    )
    with pytest.raises(KeelError) as exc:
        get("keel_live_control").handler(
            {"deployment_id": "dep_paper", "action": "pause"}, _paper_ctx(transport)
        )
    assert transport.calls == [
        ("POST", "/v1/deployments/dep_paper/pause", None),
        ("GET", "/v1/deployments/dep_paper", TELEMETRY),
    ]
    assert exc.value.error_code == "invalid_target"
    assert exc.value.suggestion == "use keel_paper_control"


def test_live_control_description_touch_up_ships_released(monkeypatch, paper_sdk_released):
    """§3.6's line ships with the released paper SDK, read when the description
    is published (PT2-91)."""
    from keel.tools.outcomes import get
    from keel.tools.outcomes._mcp_adapter import effective_description

    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    tool = get("keel_live_control")
    released = effective_description(tool)
    assert released != tool.description
    assert released.startswith(tool.description)
    assert released.endswith(
        "It refuses a paper (simulated) run with invalid_target; "
        "`keel_paper_control` controls paper runs."
    )


# ─── The neutral paper quota wall (M8-quota-wall; S06 §3.2.4b, AC-06-4) ───
#
# A slot denial with limit ≥ 1 is HEAD's plan-limit wall through
# maybe_quota_handoff, unchanged (_handoff.py has no diff): no action_url, its
# talking points exactly quota_wall_lines(parsed), no line naming
# keel_paper_control, stopping or a slot, require_do_nothing=False; the CLI
# adapter relays the same envelope the MCP adapter does. Seed (AC-06-4):
# append "stopping a running paper run frees the slot" to the wall's talking
# points in paper_deploy.py's quota branch (before it raises) ⇒ the
# exact-equality arm reds. Count: at least one talking point, and
# limit_details carries the three numbers (unit, limit, current).

#: The full-profile wall names no paper tool (R-59); its "stop one to free
#: it" is the cap's own fact since D-14 (doc 07 §5).
_WALL_FORBIDDEN_RE = re.compile(r"keel_paper_control", re.IGNORECASE)


@pytest.fixture
def wall_kwargs(monkeypatch):
    """Record the kwargs maybe_quota_handoff builds its HandoffRequired with."""
    import keel.tools.outcomes._handoff as handoff

    seen: list[dict] = []
    real = handoff.HandoffRequired

    class Recording(real):
        def __init__(self, *args, **kwargs):
            seen.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(handoff, "HandoffRequired", Recording)
    return seen


def _structured(result) -> dict:
    """The envelope a host reads: a legacy `-> str` tool wraps it as
    `{"result": <json>}` (FastMCP's wrapper), so unwrap that one shape."""
    structured = result.structured_content
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return json.loads(structured["result"])
    return structured


def _paper_wall_error():
    from keel.errors import translate_http_error

    return translate_http_error(403, PAPER_CAP_403_BODY)


def _deny_paper_create(monkeypatch, tmp_path):
    from keel.client import KeelClient

    calls: list[str] = []

    def post(_self, path, json=None, **params):
        calls.append(path)
        raise _paper_wall_error()

    _local_full_env(monkeypatch, tmp_path)
    monkeypatch.setattr(KeelClient, "post", post)
    return calls


def test_paper_quota_wall_is_the_neutral_plan_limit_wall(
    paper_sdk_released, monkeypatch, tmp_path, wall_kwargs
):
    import asyncio

    from keel.errors import quota_wall_lines
    from keel.mcp.server import create_server

    calls = _deny_paper_create(monkeypatch, tmp_path)
    result = asyncio.run(create_server().call_tool("keel_paper_deploy", {"strategy_id": "str_x"}))
    # Q-7927 (plans spec 02 §C0 test 4, G9): a plan limit is not an error; the envelope is the refusal.
    assert result.is_error is False
    envelope = _structured(result)
    assert calls == ["/v1/paper"]
    assert envelope["code"] == "handoff_required"
    assert envelope["blocked_action"] == "paper_deploy"
    details = envelope["limit_details"]
    assert {k: details.get(k) for k in ("unit", "limit", "current")} == {
        "unit": "paper_strategies_max",
        "limit": 1,
        "current": 1,
    }
    assert "action_url" not in envelope or envelope["action_url"] is None
    points = envelope["talking_points"]
    assert len(points) >= 1
    # The exact-equality arm: the wall's lines plus the ask-first slot line
    # (agents swapped running paper runs without asking; ledger Q-7605), and
    # nothing else.
    from keel.tools.outcomes._paper_strings import PAPER_WALL_SLOT_LINE

    assert points == [
        *quota_wall_lines(_paper_wall_error().input),
        PAPER_WALL_SLOT_LINE.form(listed=False),
    ]
    assert "ask the user" in points[-1].lower()
    assert not [p for p in points if _WALL_FORBIDDEN_RE.search(p)]
    assert envelope["resume"]["verify_call"]["tool"] == "keel_paper_deploy"
    # The call's own arguments, as the adapter handed them to the handler
    # (the schema default included).
    assert envelope["resume"]["verify_call"]["args"] == {
        "strategy_id": "str_x",
        "simulated_capital": 10000,
    }
    assert [k["require_do_nothing"] for k in wall_kwargs] == [False]


def test_paper_quota_wall_seed_control():
    """The seeded line the AC names fails the exact-equality comparison and
    the forbidden-line scan (proof both can fail)."""
    from keel.errors import quota_wall_lines

    lines = quota_wall_lines(_paper_wall_error().input)
    seeded = [*lines, "Stopping a paper run frees its slot (`keel_paper_control` `stop`)."]
    assert seeded != lines
    assert [p for p in seeded if _WALL_FORBIDDEN_RE.search(p)]


def test_paper_quota_wall_cli_relays_the_same_envelope(paper_sdk_released, monkeypatch, tmp_path):
    import asyncio

    import click
    from click.testing import CliRunner
    from keel.mcp.server import create_server
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._cli_adapter import register_all

    calls = _deny_paper_create(monkeypatch, tmp_path)
    mcp_envelope = _structured(
        asyncio.run(create_server().call_tool("keel_paper_deploy", {"strategy_id": "str_x"}))
    )
    group = click.Group(name="keel")
    register_all(group, {"keel_paper_deploy": OUTCOMES["keel_paper_deploy"]})
    result = CliRunner().invoke(
        group, ["paper", "deploy", "str_x", "--format", "json"], obj={"format": "json"}
    )
    cli_envelope = json.loads(result.stderr)
    assert result.exit_code == cli_envelope["exit_code"] != 0
    assert cli_envelope == mcp_envelope
    assert calls == ["/v1/paper", "/v1/paper"]


# ─── The claim case and the auth arms (M8-auth-arms; S06 §3.2.4a, R-58, AC-06-5) ─
#
# keel-api is faked at the HTTP layer (respx), so the real KeelClient decides
# what a 401 becomes. Seed (AC-06-5): route a 401 to the claim wall in
# paper_deploy.py (catch AuthError and raise anon_signin_handoff with
# reason="account_required", as the pre-W8 draft did) ⇒ arms (b) and (c)
# produce account_required and red. Count: seven cases ran (two in (a), two
# in (b), one each in (c), (d) and the control); the anonymous arm's retried
# arguments are the non-empty dict the call made.

AUTH_API_HOST = "api.paper-auth.test"
AUTH_API = f"https://{AUTH_API_HOST}"
_CASES_RAN: list[str] = []


def _reason_body(reason: str) -> dict:
    return {"title": "Forbidden", "status": 403, "detail": "Forbidden", "reasons": [reason]}


def _paper_create(status: int, body: dict, *, args: dict | None = None):
    """Call keel_paper_deploy's handler with keel-api answering POST /v1/paper."""
    import httpx
    import respx
    from keel.tools.outcomes import get
    from keel.tools.outcomes._base import ToolContext

    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{AUTH_API}/v1/paper").mock(
            return_value=httpx.Response(status, json=body)
        )
        try:
            get("keel_paper_deploy").handler(
                dict(args or {"strategy_id": "str_x"}),
                ToolContext(app_url="https://app.usekeel.io"),
            )
        except Exception as e:  # noqa: BLE001 — the arms assert on what was raised
            assert route.called
            return e
    raise AssertionError("the paper create did not raise")


@pytest.fixture
def anon_session(monkeypatch):
    from keel.config import KeelConfig, save_config

    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    save_config(
        KeelConfig(
            api_key="anon_access",
            api_url=AUTH_API,
            refresh_token="krt_anon",
            anon_org_id="org_anon_1",
        )
    )


@pytest.fixture
def stored_login(monkeypatch):
    from keel.config import KeelConfig, save_config

    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    save_config(KeelConfig(api_key="real_access", api_url=AUTH_API))


def _no_account_required(e) -> None:
    envelope = e.to_envelope()
    assert envelope.get("reason") != "account_required"
    assert "account_required" not in json.dumps(envelope)


@pytest.mark.parametrize(
    "reason",
    [
        "entitlement:no_cap_grants:paper_strategies_max",
        "entitlement:cap_exceeded:paper_strategies_max:limit=0:current=0",
    ],
)
def test_auth_arm_a_anonymous_session_gets_the_claim_wall(paper_sdk_released, anon_session, reason):
    from keel.anon import is_anon
    from keel.tools.outcomes import _paper_strings as ps
    from keel.tools.outcomes._handoff import ANON_SIGNIN_TALKING_POINT, HandoffRequired

    assert is_anon() is True
    call_args = {"strategy_id": "str_x", "simulated_capital": 1000}
    e = _paper_create(403, _reason_body(reason), args=call_args)
    assert isinstance(e, HandoffRequired), e
    envelope = e.to_envelope()
    assert envelope["reason"] == "account_required"
    assert envelope.get("action_url") is None
    assert envelope["blocked_action"] == "paper_deploy"
    verify = envelope["resume"]["verify_call"]
    assert verify["tool"] == "keel_auth_login"
    assert verify["then_retry"] == {"tool": "keel_paper_deploy", "args": call_args}
    assert verify["then_retry"]["args"], "the retried arguments are the call's own, non-empty"
    assert envelope["talking_points"][0] == ps.CLAIM_CONTEXT_POINT.full
    assert ANON_SIGNIN_TALKING_POINT in envelope["talking_points"]
    _CASES_RAN.append(f"a:{reason}")


def test_auth_arm_b_env_key_401_is_the_key_rejected_auth_error(paper_sdk_released, monkeypatch):
    from keel.errors import AuthError

    monkeypatch.setenv("KEEL_API_KEY", "keel_rejected_key")
    monkeypatch.setenv("KEEL_API_URL", AUTH_API)
    e = _paper_create(401, {"title": "Unauthorized", "status": 401})
    assert type(e) is AuthError, e
    assert "KEEL_API_KEY" in str(e)
    _no_account_required(e)
    _CASES_RAN.append("b:env-key")


def test_auth_arm_b_stored_login_401_is_the_stored_login_auth_error(
    paper_sdk_released, stored_login
):
    from keel.errors import AuthError

    e = _paper_create(401, {"title": "Unauthorized", "status": 401})
    assert type(e) is AuthError, e
    assert e.recovery_tool == "keel_auth_login"
    assert "KEEL_API_KEY" not in str(e)
    _no_account_required(e)
    _CASES_RAN.append("b:stored-login")


def test_auth_arm_c_hosted_401_is_the_hosted_auth_error(paper_sdk_released, monkeypatch):
    from keel.errors import AuthError
    from keel.hosting import bind_request_credentials, clear_request_credentials

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    reset = bind_request_credentials(token="hosted-token", api_url=AUTH_API)
    try:
        e = _paper_create(401, {"title": "Unauthorized", "status": 401})
    finally:
        clear_request_credentials(reset)
    assert isinstance(e, AuthError), e
    assert e.recovery_tool is None
    assert "keel_auth_login" not in json.dumps(e.to_envelope())
    _no_account_required(e)
    _CASES_RAN.append("c:hosted")


def test_auth_arm_d_local_permission_refusal_is_a_backtests_refusal(
    paper_sdk_released, stored_login
):
    from keel.errors import EntitlementError
    from keel.tools.outcomes import _paper_strings as ps
    from keel.tools.outcomes._handoff import HandoffRequired

    e = _paper_create(403, _reason_body("credential:scope_denied:backtest.create"))
    assert isinstance(e, EntitlementError) and not isinstance(e, HandoffRequired), e
    assert e.recovery_tool is None
    assert str(e).lower().startswith(ps.PERMISSION_REFUSAL_LINE.full.lower())
    text = json.dumps(e.to_envelope())
    for banned in ("scope='live'", "--scope live", "keel auth login"):
        assert banned not in text, banned
    _CASES_RAN.append("d:scope-denied")


def test_auth_control_signed_in_limit_1_is_the_quota_wall(paper_sdk_released, stored_login):
    from keel.tools.outcomes._handoff import HandoffRequired

    e = _paper_create(
        403, _reason_body("entitlement:cap_exceeded:paper_strategies_max:limit=1:current=1")
    )
    assert isinstance(e, HandoffRequired), e
    envelope = e.to_envelope()
    assert envelope["limit_details"]["limit"] == 1
    assert envelope["resume"]["verify_call"]["tool"] == "keel_paper_deploy"
    _no_account_required(e)
    _CASES_RAN.append("control:limit-1")


_AUTH_ARMS = (
    "test_auth_arm_a_anonymous_session_gets_the_claim_wall",
    "test_auth_arm_b_env_key_401_is_the_key_rejected_auth_error",
    "test_auth_arm_b_stored_login_401_is_the_stored_login_auth_error",
    "test_auth_arm_c_hosted_401_is_the_hosted_auth_error",
    "test_auth_arm_d_local_permission_refusal_is_a_backtests_refusal",
    "test_auth_control_signed_in_limit_1_is_the_quota_wall",
)


def test_auth_arms_all_ran(request):
    """Count (AC-06-5): the seven cases above ran (file order). Skipped only
    when a selection (-k, a node id) did not include all seven."""
    selected = [i for i in request.session.items if i.name.split("[")[0] in _AUTH_ARMS]
    if len(selected) != 7:
        pytest.skip(f"only {len(selected)} of the seven auth cases were selected")
    assert len(_CASES_RAN) == 7, _CASES_RAN


def test_auth_off_arm_1_slot_denial_relays_the_routes_403(paper_sdk_released, stored_login):
    """A no_cap_grants denial off arm 1 (a signed-in session) is the route's
    403 with no wall added (§3.2.4a)."""
    from keel.errors import EntitlementError
    from keel.tools.outcomes._handoff import HandoffRequired

    e = _paper_create(403, _reason_body("entitlement:no_cap_grants:paper_strategies_max"))
    assert isinstance(e, EntitlementError) and not isinstance(e, HandoffRequired), e
    assert e.recovery_tool is None
    assert e.input["kind"] == "no_cap_grants"


def test_auth_viewer_refusal_and_hosted_permission_403(
    paper_sdk_released, stored_login, monkeypatch
):
    """A viewer gets the same permission line; on hosted the hosted 403 arm
    stands (it names neither a re-login nor live consent)."""
    from keel.hosting import bind_request_credentials, clear_request_credentials
    from keel.tools.outcomes import _paper_strings as ps

    viewer = _paper_create(403, _reason_body("role:viewer_cannot:backtest.create"))
    assert str(viewer).lower().startswith(ps.PERMISSION_REFUSAL_LINE.full.lower())
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    reset = bind_request_credentials(token="hosted-token", api_url=AUTH_API)
    try:
        hosted = _paper_create(403, _reason_body("credential:scope_denied:backtest.create"))
    finally:
        clear_request_credentials(reset)
    assert hosted.recovery_tool is None
    assert ps.PERMISSION_REFUSAL_LINE.full not in str(hosted).lower()
    assert "keel_auth_login" not in json.dumps(hosted.to_envelope())


def test_paper_control_permission_refusal(paper_sdk_released, stored_login):
    import httpx
    import respx
    from keel.errors import EntitlementError
    from keel.tools.outcomes import _paper_strings as ps
    from keel.tools.outcomes import get
    from keel.tools.outcomes._base import ToolContext

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{AUTH_API}/v1/paper/dep_p/pause").mock(
            return_value=httpx.Response(
                403, json=_reason_body("credential:scope_denied:backtest.create")
            )
        )
        with pytest.raises(EntitlementError) as exc:
            get("keel_paper_control").handler(
                {"deployment_id": "dep_p", "action": "pause"},
                ToolContext(app_url="https://app.usekeel.io"),
            )
    assert exc.value.recovery_tool is None
    assert str(exc.value).lower().startswith(ps.PERMISSION_REFUSAL_LINE.full.lower())


# ── Clients ask for what they mean (M8-client-class; S06 §3.5.1, AC-06-30) ──
#
# PT2-16, PT2-28. The transport is faked as keel-api serves the classed lists:
# an absent class reads as `real`, `all` returns every row. The account tools
# never surface the paper account (PT2-3): `keel_accounts_list`'s list mode and
# `keel_live_deploy`'s direct-path no-account probe send `capital_class=real`
# (not held: they render no public text). Under the release fixture
# `keel_deployments_list` names `all` when the caller names no class and labels
# each row of that mixed result Live or Paper from `venue.capital_class`; a
# one-class request carries no label, and `practice` points at `/paper`. With
# the committed constant the deployments tool is HEAD's and sends no class
# (`tests/test_live_monitor_contract.py::test_deployments_list_held_publishes_and_sends_no_class`).
#
# Seeds (each by an exact reversible edit in the named module, then reversed):
# derive the label from `status` instead of the venue block ⇒ the label arm
# reds (both fixture rows are LIVE); drop the deployments tool's `all` default
# ⇒ the faked server returns the real row only and the no-class arm reds; drop
# the account tool's class parameter ⇒ the request arm reds (its output does
# not: the route reads an absent class as `real`, which is why every request
# is asserted). Count: four tool calls ran, each recorded request is asserted
# by its params, and the deployments fixture holds one row of each class.

_REAL_DEP = {
    "deployment_id": "dep_live",
    "strategy_id": "str_y",
    "status": "LIVE",
    "venue": {"code": "HYPERLIQUID", "capital_class": "real"},
}
_PAPER_DEP = {
    "deployment_id": "dep_paper",
    "strategy_id": "str_x",
    "status": "LIVE",
    "venue": {"code": "PAPER", "capital_class": "practice"},
}
#: _PAPER_DEP as keel_deployments_list renders it: keel-app's status word (QA-W2).
_PAPER_PRESENTED = {**_PAPER_DEP, "status": "Paper"}
_REAL_ACCT = {"account_id": "acct_real", "capital_class": "real"}
_PAPER_ACCT = {"account_id": "acct_paper", "capital_class": "practice"}


class _ClassedServer:
    """A `KeelClient` stand-in serving classed lists the way keel-api does
    (S01 §3.10, PT2-28): an absent class is `real`; `all` is every row."""

    def __init__(self, rows: dict[str, list[dict]]) -> None:
        outcomes._bootstrap()  # these arms may run alone: register the HEAD tools
        self.rows = rows  # path -> every row the org has, any class
        self.requests: list[tuple[str, dict]] = []

    def get(self, path, *, headers=None, **params):
        self.requests.append((path, dict(params)))
        wanted = params.get("capital_class") or "real"
        rows = [
            r
            for r in self.rows[path]
            if wanted == "all"
            or (r.get("venue") or {}).get("capital_class", r.get("capital_class")) == wanted
        ]
        return {"data": [dict(r) for r in rows], "pagination": {"cursor": None, "has_more": False}}

    def post(self, path, json=None, **_kw):
        from keel.errors import KeelError

        raise KeelError("no deploy intent in this fake")  # the handoff falls back


_CLIENT_CLASS_CALLS: list[str] = []


def _classed_ctx(server):
    from keel.tools.outcomes._base import ToolContext

    return ToolContext(api_client=server, app_url="https://app.usekeel.io")


def test_accounts_list_asks_for_real_and_lists_no_paper_account():
    from keel.tools.outcomes import get

    server = _ClassedServer({"/v1/accounts": [_REAL_ACCT, _PAPER_ACCT]})
    env = get("keel_accounts_list").handler({"limit": 5}, _classed_ctx(server)).to_envelope()
    _CLIENT_CLASS_CALLS.append("accounts_list")
    assert server.requests == [("/v1/accounts", {"capital_class": "real", "limit": 5})]
    assert [a["account_id"] for a in env["accounts"]] == ["acct_real"]


def test_live_deploy_probe_counts_no_paper_account(monkeypatch):
    from keel.tools.outcomes import get
    from keel.tools.outcomes._handoff import HandoffRequired

    monkeypatch.setenv("KEEL_ALLOW_DIRECT_DEPLOY", "1")
    monkeypatch.setattr("keel.tools.outcomes._handoff._is_anon_session", lambda: False)
    server = _ClassedServer({"/v1/accounts": [_PAPER_ACCT]})
    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "str_x", "direct": True}, _classed_ctx(server)
        )
    _CLIENT_CLASS_CALLS.append("live_deploy_probe")
    assert server.requests == [("/v1/accounts", {"limit": 1, "capital_class": "real"})]
    env = exc.value.to_envelope()
    assert env["blocked_action"] == "live_deploy"
    assert env.get("code") != "missing_account_id"
    assert env["action_url"] == "https://app.usekeel.io/deploy/str_x"


def test_deployments_list_names_all_and_labels_each_row(paper_sdk_released):
    from keel.tools.outcomes import get

    server = _ClassedServer({"/v1/deployments": [_REAL_DEP, _PAPER_DEP]})
    assert {r["venue"]["capital_class"] for r in server.rows["/v1/deployments"]} == {
        "real",
        "practice",
    }
    env = get("keel_deployments_list").handler({}, _classed_ctx(server)).to_envelope()
    _CLIENT_CLASS_CALLS.append("deployments_all")
    assert server.requests == [("/v1/deployments", {"capital_class": "all"})]
    labels = {d["deployment_id"]: d.get("label") for d in env["deployments"]}
    assert labels == {"dep_live": "Live", "dep_paper": "Paper"}
    # Each row is otherwise relayed verbatim, but a practice row's status is
    # keel-app's word for it (QA-W2).
    for row in env["deployments"]:
        source = _REAL_DEP if row["deployment_id"] == "dep_live" else _PAPER_PRESENTED
        assert {k: v for k, v in row.items() if k != "label"} == source
    assert env["hero_url"] == "https://app.usekeel.io/live"


def test_deployments_list_forwards_practice_with_no_label(paper_sdk_released):
    from keel.tools.outcomes import get

    server = _ClassedServer({"/v1/deployments": [_REAL_DEP, _PAPER_DEP]})
    env = (
        get("keel_deployments_list")
        .handler({"capital_class": "practice"}, _classed_ctx(server))
        .to_envelope()
    )
    _CLIENT_CLASS_CALLS.append("deployments_practice")
    assert server.requests == [("/v1/deployments", {"capital_class": "practice"})]
    assert env["deployments"] == [_PAPER_PRESENTED]
    assert env["hero_url"].endswith("/paper")


def test_client_class_arms_all_ran(request):
    """Count (AC-06-30): the four tool calls above ran in this session."""
    names = {i.name for i in request.session.items}
    expected = {
        "test_accounts_list_asks_for_real_and_lists_no_paper_account",
        "test_live_deploy_probe_counts_no_paper_account",
        "test_deployments_list_names_all_and_labels_each_row",
        "test_deployments_list_forwards_practice_with_no_label",
    }
    if not expected <= names:
        pytest.skip("run with the whole module to count the client-class arms")
    assert sorted(_CLIENT_CLASS_CALLS) == sorted(
        ["accounts_list", "live_deploy_probe", "deployments_all", "deployments_practice"]
    )


# ── Sharing, tool side (M8-share-tool; S06 §3.7, R-61) ──
#
# A `dep_` id is read once (the telemetry-marked detail read, never an owner
# visit) before any share call; the venue block decides: `practice` creates
# through the paper share route (S05 IC-05-5) and returns the URL with
# `share_type` and `permission`; `real` is refused with `unsupported_target`
# (also when the caller passes `target_type="paper"`), and nothing is posted.
# No live share route is ever called. Held (committed constant), and on listed
# with the listed flag off, the tool is HEAD's: no `paper` in the published
# enum, a `dep_` id answers HEAD's `ambiguous_target` with no read, and
# `target_type="paper"` is refused the same way. With the flag on the listed
# twin carries `paper`.
#
# Seed: post a `real` row to the paper route instead of refusing it ⇒ the
# refusal arms red. Count: the practice share made exactly one read and one
# post; both refusal arms posted nothing.

_PAPER_SHARE = {
    "share_id": "shr_paper1",
    "share_type": "paper",
    "deployment_id": "dep_paper",
    "include_strategy_source": False,
    "permission": "view",
}


class _ShareTransport:
    def __init__(self, rows: dict[str, dict]) -> None:
        outcomes._bootstrap()
        self.rows = rows
        self.calls: list[tuple] = []

    def get(self, path, *, headers=None, **params):
        self.calls.append(("GET", path, headers, None))
        if path == "/v1/me":
            return {"referral_code": ""}
        return self.rows[path]

    def post(self, path, json=None, *, headers=None, **params):
        self.calls.append(("POST", path, headers, json))
        return dict(_PAPER_SHARE)


def _share(args: dict, rows: dict[str, dict]):
    from keel.tools.outcomes import get

    transport = _ShareTransport(rows)
    ctx = _classed_ctx(transport)
    return transport, get("keel_share_create").handler(args, ctx)


def test_share_a_paper_run_through_the_paper_route(paper_sdk_released):
    rows = {"/v1/deployments/dep_paper": dict(_PAPER_DEP)}
    transport, result = _share({"target_id": "dep_paper"}, rows)
    env = result.to_envelope()
    assert transport.calls[0] == ("GET", "/v1/deployments/dep_paper", TELEMETRY, None)
    posts = [c for c in transport.calls if c[0] == "POST"]
    assert posts == [
        ("POST", "/v1/paper/dep_paper/share-links", None, {"include_strategy_source": False})
    ]
    assert env["share_type"] == "paper" and env["permission"] == "view"
    assert env["share_url"].endswith("/share/shr_paper1")


@pytest.mark.parametrize("args", [{}, {"target_type": "paper"}])
def test_share_refuses_a_live_run(paper_sdk_released, args):
    from keel.errors import KeelError
    from keel.tools.outcomes import get

    transport = _ShareTransport({"/v1/deployments/dep_live": dict(_REAL_DEP)})
    with pytest.raises(KeelError) as exc:
        get("keel_share_create").handler({"target_id": "dep_live", **args}, _classed_ctx(transport))
    assert exc.value.error_code == "unsupported_target"
    assert "/live/dep_live" in (exc.value.suggestion or "")
    # Only the one class read ran: nothing was posted anywhere.
    assert [c[:2] for c in transport.calls] == [("GET", "/v1/deployments/dep_live")]


def test_share_listed_target_moves_only_with_the_flag(paper_sdk_released, monkeypatch):
    from keel.errors import KeelError
    from keel.tools.outcomes import get
    from keel.tools.outcomes._mcp_adapter import effective_description, effective_input_schema

    tool = get("keel_share_create")
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.delenv("KEEL_LISTED_PAPER", raising=False)
    off_schema = effective_input_schema(tool)
    assert "paper" not in off_schema["properties"]["target_type"]["enum"]
    assert effective_description(tool) == tool.description
    transport = _ShareTransport({"/v1/deployments/dep_paper": dict(_PAPER_DEP)})
    with pytest.raises(KeelError) as exc:
        tool.handler({"target_id": "dep_paper"}, _classed_ctx(transport))
    assert exc.value.error_code == "ambiguous_target" and transport.calls == []

    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    on_schema = effective_input_schema(tool)
    assert on_schema["properties"]["target_type"]["enum"] == ["strategy", "backtest", "paper"]
    assert "paper run" in effective_description(tool)
    transport, result = _share(
        {"target_id": "dep_paper"}, {"/v1/deployments/dep_paper": dict(_PAPER_DEP)}
    )
    assert result.to_envelope()["share_type"] == "paper"
    assert ("POST", "/v1/paper/dep_paper/share-links") in [c[:2] for c in transport.calls]


# ── The live card's PAPER stamp (S06 §3.5.6) ──
#
# The fragment is served ahead of card-live.js on the full profile, and on
# listed only with the listed flag on (the scan's card arm pins both listed
# states). Driven in node against a stub KeelHost: a practice result gets the
# "PAPER — simulated" chip on every header draw, its account value tile
# relabelled "Simulated Value", a running state drawn "paper" and its table
# captioned "Simulated <view>" (the copy law's rendered-label gate, S06
# §3.10: "Live" never labels practice data); a real result is drawn
# untouched. Seed: key the stamp on `status` instead of the venue block ⇒
# the real arm reds.

_STAMP_DRIVER = r"""
const vm = require("vm");
const src = require("fs").readFileSync(process.argv[2], "utf8");
const out = {};
function run(env) {
  const meta = { kids: [], appendChild(n) { this.kids.push(n.textContent); } };
  const calls = { tiles: [], states: [], captions: [] };
  let ready;
  const H = {
    ready(cb) { ready = cb; },
    header(spec) { meta.kids = []; calls.states.push(spec.state || null); return spec; },
    tiles(entries) { calls.tiles.push(entries.map((e) => e[0])); },
    table(rows, opts) { calls.captions.push(opts.caption); return rows; },
  };
  const ctx = {
    window: { KeelHost: H },
    document: {
      getElementById: (id) => (id === "head-meta" ? meta : null),
      createElement: () => ({ className: "", textContent: "" }),
    },
  };
  vm.runInNewContext(src, ctx);
  H.ready(function (e) {
    H.header({ name: "x", state: "live" });
    H.tiles([["Account value", 1], ["Positions", 2]]);
    H.table([], { caption: "Live positions" });
  });
  ready(env);
  H.header({ name: "re-render", state: "paused" });
  return { chips: meta.kids, tiles: calls.tiles, states: calls.states, captions: calls.captions };
}
// A run with history (two curve points, fills): the live renderer draws it;
// one with none is the fragment's waiting state (tests/test_paper_card.py).
const history = { curve: { points: [[1, 1], [2, 2]] }, stats: { total_trades: 3 } };
out.practice = run({ data: { status: "LIVE", venue: { capital_class: "practice" } }, ...history });
out.real = run({ data: { status: "LIVE", venue: { capital_class: "real" } } });
console.log(JSON.stringify(out));
"""


def test_the_live_card_stamp_marks_practice_runs_only(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    import keel.widgets as widgets
    from keel.widgets import build_card_html

    if shutil.which("node") is None:
        pytest.skip("node is not installed; the stamp is card script")
    fragment = Path(widgets.__file__).resolve().parent / "assets" / "card-live-paper.js"
    assert fragment.is_file(), fragment
    driver = tmp_path / "stamp_driver.js"
    driver.write_text(_STAMP_DRIVER, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(driver), str(fragment)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["practice"]["chips"] == ["PAPER — simulated"]  # after the re-render too
    assert got["practice"]["tiles"] == [["Simulated Value", "Positions"]]
    assert got["practice"]["states"] == ["paper", "paused"]
    assert got["practice"]["captions"] == ["Simulated positions"]
    assert got["real"] == {
        "chips": [],
        "tiles": [["Account value", "Positions"]],
        "states": ["live", "paused"],
        "captions": ["Live positions"],
    }
    # Served ahead of the card's own script, and only when asked for.
    html = build_card_html("live", paper_stamp=True)
    assert html.index("PAPER — simulated") < html.index("/* Live view card")
    assert "PAPER — simulated" not in build_card_html("live")


# ── paper_not_enabled relayed (M8-sdk-not-enabled; S06 §3.2.4 (c), AC-06-28) ──
#
# keel-api answers every paper route 404 {"code": "paper_not_enabled"} while
# its flag is off. Both paper tools relay the typed NotFoundError: on the CLI
# (exit code 3 and the envelope) and the local MCP (the envelope), and the
# listed keel_paper_deploy with KEEL_LISTED_PAPER=1 returns the same
# envelope. keel_paper_control relays it without its guard read. Seed:
# delete the arm in keel/errors.py ⇒ every case relays `not_found` and the
# code arm reds (and keel_paper_control makes the guard read, so its request
# count reds too). Count: five cases, one recorded request each.

PAPER_OFF_404 = '{"detail": "Paper trading is not available yet.", "code": "paper_not_enabled"}'
_PAPER_OFF_CASES: list[str] = []


def _paper_off_transport(monkeypatch) -> list[tuple[str, str]]:
    """KeelClient's verbs, faked: every /v1/paper route answers the typed 404;
    anything else is recorded and fails."""
    from keel.client import KeelClient
    from keel.errors import translate_http_error

    calls: list[tuple[str, str]] = []

    def verb(method):
        def call(_self, path, *args, **kwargs):
            calls.append((method, path))
            if path == "/v1/paper" or path.startswith("/v1/paper/"):
                raise translate_http_error(404, PAPER_OFF_404)
            raise AssertionError(f"unexpected {method} {path}")

        return call

    for method in ("get", "post", "patch", "put", "delete"):
        monkeypatch.setattr(KeelClient, method, verb(method.upper()))
    return calls


def _assert_paper_off_envelope(envelope: dict, *, listed: bool = False) -> None:
    """The typed error's own envelope, relayed whole; the listed profile
    drops only `exit_code` (the CLI's process status), as at HEAD."""
    from keel.errors import translate_http_error

    assert envelope["code"] == "paper_not_enabled"
    assert envelope["message"] == "Paper trading is not available on this Keel environment yet."
    nxt = envelope.get("suggested_next_action") or {}
    assert not nxt.get("reason"), nxt
    expected = translate_http_error(404, PAPER_OFF_404).to_envelope()
    assert expected["exit_code"] == 3
    if listed:
        expected.pop("exit_code")
    assert envelope == expected


@pytest.mark.parametrize(
    ("tool", "args", "argv", "path"),
    [
        (
            "keel_paper_deploy",
            {"strategy_id": "str_x"},
            ["paper", "deploy", "str_x"],
            ("POST", "/v1/paper"),
        ),
        (
            "keel_paper_control",
            {"deployment_id": "dep_p", "action": "pause"},
            ["paper", "control", "dep_p", "--action", "pause", "--yes"],
            ("POST", "/v1/paper/dep_p/pause"),
        ),
    ],
)
@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_paper_not_enabled_is_relayed(
    paper_sdk_released, monkeypatch, tmp_path, tool, args, argv, path, surface
):
    import asyncio

    import click
    from click.testing import CliRunner
    from keel.mcp.server import create_server
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._cli_adapter import register_all

    _local_full_env(monkeypatch, tmp_path)
    calls = _paper_off_transport(monkeypatch)
    if surface == "mcp":
        result = asyncio.run(create_server().call_tool(tool, args))
        assert result.is_error is True
        envelope = _structured(result)
    else:
        group = click.Group(name="keel")
        register_all(group, {tool: OUTCOMES[tool]})
        result = CliRunner().invoke(group, [*argv, "--format", "json"], obj={"format": "json"})
        envelope = json.loads(result.stderr)
        assert result.exit_code == 3, result.output
    _assert_paper_off_envelope(envelope)
    assert calls == [path]  # one request: no guard read, no retry
    _PAPER_OFF_CASES.append(f"{tool}:{surface}")


def test_listed_paper_deploy_relays_paper_not_enabled(paper_sdk_released, monkeypatch, tmp_path):
    import asyncio

    from keel.hosting import bind_request_credentials, clear_request_credentials
    from keel.mcp.server import create_server

    _local_full_env(monkeypatch, tmp_path)
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    calls = _paper_off_transport(monkeypatch)
    server = create_server()
    reset = bind_request_credentials(token="caller-token", api_url="https://staging-api.test")
    try:
        result = asyncio.run(server.call_tool("keel_paper_deploy", {"strategy_id": "str_x"}))
    finally:
        clear_request_credentials(reset)
    assert result.is_error is True
    _assert_paper_off_envelope(_structured(result), listed=True)
    assert calls == [("POST", "/v1/paper")]
    _PAPER_OFF_CASES.append("keel_paper_deploy:listed")


def test_paper_not_enabled_cases_all_ran():
    assert sorted(_PAPER_OFF_CASES) == [
        "keel_paper_control:cli",
        "keel_paper_control:mcp",
        "keel_paper_deploy:cli",
        "keel_paper_deploy:listed",
        "keel_paper_deploy:mcp",
    ]


# ── The copy law (M8-copy-law; S06 §3.10, AC-06-20) ──
#
# Every paper copy source S06 owns passes §3.10's grep gates. The nine source
# kinds (COPY_SOURCE_KINDS) are read where they live: the tool descriptions,
# parameter descriptions and titles both profiles publish (read off the
# released registry, the held touch-ups' paper forms included, plus the paper
# tool modules' own literals); `_paper_strings.py` whole (comments included:
# it is all paper copy); `_nudge.py`'s literals (its docstrings state the
# honesty rules, which name the banned word); the deploy-and-monitor skill;
# the knowledge rows (the operating core's paper sections and every
# `paper:on` block of the chat corpus); chat-api's prompt constants and its
# tool catalog's paper literals (read by AST from the source: the SDK cannot
# import chat-api); `cli-reference.md` (whole: its paper rows land with the
# wheel's follow-up, PT2-28, and are gated the day they land); and the card
# copy (the live card's stamp fragment and the chat paper card).
#
# The banned arm: `prove it in paper`, `instead of live`, `safer than live`,
# `Papertrade` and a word-bounded `earn` (the `_FORBIDDEN_TALKING_POINT_RE`
# precedent), case-insensitive. The required arm: "simulated" in the four
# description variants (the SDK deploy tool's full and listed descriptions,
# chat's `paper_deploy`, the control tool) and "paper" in every paper tool
# title both profiles publish. The rendered-label arm drives the practice
# render paths (the deploy and control envelopes, the monitor's overview and
# graduation, the portfolio's paper section, the deployments list's practice
# row, every `_paper_strings` form) and finds no standalone "Live", the
# ladder rung excepted; the card's practice draw is asserted by
# test_the_live_card_stamp_marks_practice_runs_only above.
#
# Seeds (exact reversible edits): "prove it in paper" inserted into a
# `_paper_strings.py` row ⇒ the banned-phrase arm reds; the deployments
# list's practice label set to "Live" ⇒ the rendered-label arm reds; the
# monitor's practice status mapping skipped (raw "LIVE" relayed) ⇒ the
# rendered-label arm reds on the overview, graduation and paper section
# (QA V2: with the old case-sensitive pattern that seed stayed green).
# Count: the scanned units cover all nine kinds, each non-empty; the
# rendered-label arm reads at least eight practice renders, and its control
# (the real row's "Live" label) proves the detector fires on a real render.

from pathlib import Path  # noqa: E402


_SDK_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_OUTCOMES_DIR = _SDK_ROOT / "keel" / "tools" / "outcomes"

COPY_LAW_BANNED_RE = re.compile(
    r"prove it in paper|instead of live|safer than live|papertrade|\bearn", re.IGNORECASE
)
_PAPER_WORD_RE = re.compile(r"paper", re.IGNORECASE)
_SIMULATED_RE = re.compile(r"simulated", re.IGNORECASE)
_LADDER_RUNG_RE = re.compile(r"Backtest → Paper → Live")
#: "Live" as a label, in either casing a label takes: title case ("Live", the
#: deployments list's row label) and upper case ("LIVE", keel-api's raw run
#: status, which a reader takes for real capital). Until QA V2 the pattern was
#: case-sensitive and every practice render carrying `"status": "LIVE"` passed.
#: Word-bounded, so "Delivery" and "alive" never match; lowercase "live" is
#: prose ("going live is …", "live Hyperliquid prices") or a machine token
#: (`"card": "live"`), never a label (S06 §3.5.2, copy law rule 4).
_STANDALONE_LIVE = re.compile(r"\b(?:Live|LIVE)\b")

COPY_SOURCE_KINDS = (
    "tool descriptions and titles",
    "_paper_strings.py",
    "_nudge.py",
    "skill",
    "knowledge rows",
    "chat prompt constants",
    "chat tool description",
    "cli-reference paper rows",
    "card copy",
)

_CHAT_AGENT = _REPO_ROOT / "services" / "chat-api" / "src" / "agent"
_SYSTEM_KNOWLEDGE = _REPO_ROOT / "libs" / "pipeline_engine" / "reference" / "system"
_CHAT_PAPER_CARD = (
    _REPO_ROOT
    / "services"
    / "keel-app"
    / "src"
    / "app"
    / "(app)"
    / "strategies"
    / "_components"
    / "ChatPaperDeployCard.tsx"
)


def _fold_str(node) -> str | None:
    """A string expression's text: a literal, an f-string (fields as `{}`), or
    a `+` of those. Anything else is not one copy unit."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold_str(node.left), _fold_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _py_literals(path: Path) -> list[tuple[int, str]]:
    """Every maximal non-blank string expression in a module, docstrings excluded."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    found: list[tuple[int, str]] = []

    def visit(node) -> None:
        if id(node) in docstrings:
            return
        text = _fold_str(node)
        if text is not None:
            if text.strip():  # "" and "\n" joiners carry no copy
                found.append((node.lineno, text))
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def _chat_paper_tool_description() -> str:
    """chat-api's `paper_deploy` description, read from its catalog source."""
    import ast

    tree = ast.parse((_CHAT_AGENT / "tools.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            fields = {
                k.value: v
                for k, v in zip(node.keys, node.values, strict=True)
                if isinstance(k, ast.Constant)
            }
            name = fields.get("name")
            if isinstance(name, ast.Constant) and name.value == "paper_deploy":
                found.append(_fold_str(fields["description"]))
    assert len(found) == 1 and found[0], "chat-api's paper_deploy definition was not found"
    return found[0]


def _markdown_paper_blocks(path: Path) -> list[tuple[int, str]]:
    """The `paper:on` blocks of a chat corpus file (S06 §3.9 rows 1, 2, 11)."""
    text = path.read_text(encoding="utf-8")
    return [
        (text[: m.start()].count("\n") + 1, m.group(1))
        for m in re.finditer(r"<!-- paper:on -->(.*?)<!-- paper:end -->", text, re.S)
    ]


def _operating_core_paper_sections() -> list[tuple[int, str]]:
    """The operating core's sections whose id names paper (§3.9 row 3)."""
    text = (_SYSTEM_KNOWLEDGE / "operating_core.md").read_text(encoding="utf-8")
    parts = re.split(r"^---\n", text, flags=re.M)
    sections = []
    for i in range(1, len(parts) - 1, 2):
        meta, body = parts[i], parts[i + 1]
        ident = re.search(r"^id: (\S+)$", meta, re.M)
        if ident and "paper" in ident.group(1):
            sections.append((ident.group(1), body))
    return [(0, f"{ident}: {body}") for ident, body in sections]


def _tool_copy_units() -> list[tuple[str, str]]:
    """(origin, text) for every tool text the two profiles publish that names
    paper, read under the released SDK (held touch-ups in their paper form)."""
    from keel.tools.outcomes import OUTCOMES

    units: list[tuple[str, str]] = []
    for name, tool in sorted(OUTCOMES.items()):
        paper_tool = name.startswith("keel_paper_")
        texts = {
            "description": tool.description,
            "paper_description": tool.paper_description,
            "listed_description": tool.listed_description,
            "listed_paper_description": tool.listed_paper_description,
            "title": (tool.annotations or {}).get("title"),
            "listed_title": tool.listed_title,
        }
        for attr in (
            "input_schema",
            "paper_input_schema",
            "listed_input_schema",
            "listed_paper_input_schema",
        ):
            schema = getattr(tool, attr, None) or {}
            for prop, spec in (schema.get("properties") or {}).items():
                texts[f"{attr}.{prop}"] = spec.get("description")
        for field, text in texts.items():
            if isinstance(text, str) and (paper_tool or _PAPER_WORD_RE.search(text)):
                units.append((f"{name}.{field}", text))
    for module in ("paper_deploy.py", "paper_control.py", "_paper_walls.py"):
        for line, text in _py_literals(_OUTCOMES_DIR / module):
            units.append((f"{module}:{line}", text))
    return units


def _copy_units() -> list[tuple[str, str, str]]:
    """(kind, origin, text) over §3.10's nine source kinds."""
    units: list[tuple[str, str, str]] = []
    units += [("tool descriptions and titles", o, t) for o, t in _tool_copy_units()]
    strings_py = _OUTCOMES_DIR / "_paper_strings.py"
    units.append(("_paper_strings.py", strings_py.name, strings_py.read_text(encoding="utf-8")))
    units += [
        ("_nudge.py", f"_nudge.py:{line}", text)
        for line, text in _py_literals(_OUTCOMES_DIR / "_nudge.py")
    ]
    skill = _SDK_ROOT / "keel" / "skills" / "deploy-and-monitor" / "SKILL.md"
    units.append(("skill", "deploy-and-monitor/SKILL.md", skill.read_text(encoding="utf-8")))
    units += [
        ("knowledge rows", f"operating_core.md {origin}", text)
        for origin, text in _operating_core_paper_sections()
    ]
    for md in sorted(_SYSTEM_KNOWLEDGE.rglob("*.md")):
        units += [
            ("knowledge rows", f"{md.relative_to(_SYSTEM_KNOWLEDGE)}:{line}", text)
            for line, text in _markdown_paper_blocks(md)
        ]
    for module in ("system_prompt.py", "context_providers.py"):
        units += [
            ("chat prompt constants", f"{module}:{line}", text)
            for line, text in _py_literals(_CHAT_AGENT / module)
            if _PAPER_WORD_RE.search(text)
        ]
    units.append(("chat tool description", "tools.py paper_deploy", _chat_paper_tool_description()))
    units += [
        ("chat tool description", f"tools.py:{line}", text)
        for line, text in _py_literals(_CHAT_AGENT / "tools.py")
        if _PAPER_WORD_RE.search(text)
    ]
    cli_ref = _REPO_ROOT / "packages" / "keel-trade" / "docs" / "cli-reference.md"
    units.append(("cli-reference paper rows", cli_ref.name, cli_ref.read_text(encoding="utf-8")))
    fragment = _SDK_ROOT / "keel" / "widgets" / "assets" / "card-live-paper.js"
    units.append(("card copy", fragment.name, fragment.read_text(encoding="utf-8")))
    units.append(("card copy", _CHAT_PAPER_CARD.name, _CHAT_PAPER_CARD.read_text(encoding="utf-8")))
    return units


def test_copy_law_banned_phrases_appear_in_no_paper_copy_source(paper_sdk_released):
    units = _copy_units()
    # Count: every one of the nine kinds is scanned, and no unit is empty.
    assert {kind for kind, _, _ in units} == set(COPY_SOURCE_KINDS)
    assert all(text.strip() for _, _, text in units), [o for _, o, t in units if not t.strip()]
    assert sum(1 for k, _, _ in units if k == "knowledge rows") >= 5  # rows 1, 2, 3 (×2), 11
    hits = [
        (kind, origin, m.group(0))
        for kind, origin, text in units
        for m in COPY_LAW_BANNED_RE.finditer(text)
    ]
    assert hits == [], f"copy-law banned phrases (S06 §3.10): {hits}"


def test_copy_law_banned_regex_bites():
    """Non-vacuity of the banned arm: each banned phrase is found, a word that
    merely contains 'earn' is not."""
    for phrase in (
        "Prove it in paper first",
        "paper instead of live",
        "safer than live trading",
        "Papertrade it",
        "earn 12% a month",
    ):
        assert COPY_LAW_BANNED_RE.search(phrase), phrase
    assert not COPY_LAW_BANNED_RE.search("learn how the run behaves")


def test_copy_law_required_strings(paper_sdk_released, monkeypatch):
    from keel.tools.outcomes import OUTCOMES, get
    from keel.tools.outcomes._mcp_adapter import effective_annotations

    deploy, control = get("keel_paper_deploy"), get("keel_paper_control")
    variants = {
        "keel_paper_deploy (full)": deploy.description,
        "keel_paper_deploy (listed)": deploy.listed_description,
        "chat paper_deploy": _chat_paper_tool_description(),
        "keel_paper_control": control.description,
    }
    assert all(isinstance(v, str) and v for v in variants.values()), variants
    assert [k for k, v in variants.items() if not _SIMULATED_RE.search(v)] == []

    titles: dict[str, str] = {}
    paper_tools = [t for n, t in sorted(OUTCOMES.items()) if "paper" in n]
    assert [t.name for t in paper_tools] == ["keel_paper_control", "keel_paper_deploy"]
    for profile in ("full", "listed"):
        if profile == "listed":
            monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
        else:
            monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
        for tool in paper_tools:
            titles[f"{tool.name} ({profile})"] = effective_annotations(tool)["title"]
    assert len(titles) == 4
    assert [k for k, v in titles.items() if not _PAPER_WORD_RE.search(v or "")] == []


def _practice_renders(monkeypatch) -> tuple[list[tuple[str, str]], dict]:
    """(render path, text) for every practice render path the SDK draws."""
    from keel.tools.outcomes import _paper_strings as ps
    from keel.tools.outcomes import get

    from tests import test_live_monitor_contract as monitor

    def dump(value) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    renders: list[tuple[str, str]] = []
    for row in ps.ALL_PAPER_STRINGS:
        for listed in (False, True):
            form = row.form(listed=listed)
            if form is not None:
                renders.append(
                    (f"_paper_strings.{row.key} ({'listed' if listed else 'full'})", form)
                )

    _, text, envelope = monitor._overview(monitor._PRACTICE_ROW)
    renders.append(("keel_live_monitor overview (MCP text)", text))
    renders.append(("keel_live_monitor overview (envelope)", dump(envelope)))
    _, _, graduated = monitor._graduation_overview(True, {"handoff_url": "https://app.test/i"})
    renders.append(("keel_live_monitor graduation", dump(graduated)))
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    _, portfolio = monitor._portfolio(monitor._PRACTICE_SUMMARY)
    renders.append(
        (
            "keel_live_monitor portfolio paper section",
            dump({k: portfolio[k] for k in ("paper", "paper_label")}),
        )
    )

    server = _ClassedServer({"/v1/deployments": [_REAL_DEP, _PAPER_DEP]})
    rows = get("keel_deployments_list").handler({}, _classed_ctx(server)).to_envelope()
    by_id = {r["deployment_id"]: r for r in rows["deployments"]}
    renders.append(("keel_deployments_list practice row", dump(by_id["dep_paper"])))

    class _Create:
        def post(self, path, json=None, **_kw):
            return dict(_PAPER_CREATE_RESPONSE)

    ctx = _classed_ctx(_Create())
    deploy = get("keel_paper_deploy")
    renders.append(
        (
            "keel_paper_deploy (full)",
            dump(deploy.handler({"strategy_id": "strat_y"}, ctx).to_envelope()),
        )
    )
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    renders.append(
        (
            "keel_paper_deploy (listed)",
            dump(deploy.handler({"strategy_id": "strat_y"}, ctx).to_envelope()),
        )
    )
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_LISTED_PAPER", raising=False)

    transport = _FakeTransport(
        {
            ("POST", "/v1/paper/dep_p/pause"): {"deployment_id": "dep_p", "status": "PAUSED"},
            ("POST", "/v1/paper/dep_p/restart"): {
                "deployment_id": "dep_q",
                "status": "LIVE",
                "venue": {"code": "PAPER", "capital_class": "practice"},
                "restarted_from": "dep_p",
            },
        }
    )
    control = get("keel_paper_control")
    for action in ("pause", "restart"):
        env = control.handler(
            {"deployment_id": "dep_p", "action": action}, _paper_ctx(transport)
        ).to_envelope()
        renders.append((f"keel_paper_control {action}", dump(env)))
    return renders, by_id["dep_live"]


def test_copy_law_no_standalone_live_on_a_practice_render(
    paper_sdk_released, monkeypatch, tmp_path
):
    _local_full_env(monkeypatch, tmp_path)
    renders, real_row = _practice_renders(monkeypatch)
    # Count: the eight render paths beside every string form, each non-empty.
    assert len(renders) >= 8 + 13 and all(text for _, text in renders)
    # Controls: the detector fires on a real row's render, on its "Live" label
    # and on its raw "LIVE" status, and on neither near-miss word.
    assert real_row["label"] == "Live" and real_row["status"] == "LIVE"
    assert _STANDALONE_LIVE.search(json.dumps(real_row["label"]))
    assert _STANDALONE_LIVE.search(json.dumps({"status": real_row["status"]}))
    assert not _STANDALONE_LIVE.search("Delivery stays alive; DELIVERY, ALIVE")
    # Control: legitimate prose "live" is not a label, so the paper fill
    # disclosure ("simulated at live Hyperliquid prices") passes the gate.
    from keel.tools.outcomes._paper_strings import DEPLOY_FILL_DISCLOSURE

    disclosure = DEPLOY_FILL_DISCLOSURE.full
    assert re.search(r"\blive\b", disclosure) and not _STANDALONE_LIVE.search(disclosure)
    offenders = [
        (path, m.group(0))
        for path, text in renders
        for m in _STANDALONE_LIVE.finditer(_LADDER_RUNG_RE.sub("", text))
    ]
    assert offenders == [], f"a practice render emitted the label 'Live' (S06 §3.10): {offenders}"
    # The ladder rung itself is the one exception, and it is rendered.
    assert any(_LADDER_RUNG_RE.search(text) for _, text in renders)


# ── The full-profile deploy line (M8-regen; S06 §3.9 row 7) ──
#
# Released, the signed-in full/CLI deploy line carries §3.4.6's paper
# sentence between the live link and the do-nothing clause; the live link
# stays first. Held, the line is HEAD's byte for byte; the anon and listed
# arms never change. Seed: emit the clause without the PAPER_SDK_RELEASED
# check ⇒ the held arm reds.

_HEAD_DEPLOY_LINE = (
    "This backtest clears Keel's good-result bar (Sharpe 1.72, max drawdown -14.3%, "
    "over 364 days). Taking it live is a human step — review and deploy at {url} — or do "
    "nothing: nothing goes live without your explicit approval."
)


def _deploy_line(monkeypatch) -> str | None:
    from unittest.mock import MagicMock

    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._nudge import deploy_line

    from tests.test_nudge import GOOD_METRICS, MINT_RESPONSE, _detail

    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.setattr("keel.tools.outcomes._handoff._is_anon_session", lambda: False)
    client = MagicMock()
    client.post.return_value = MINT_RESPONSE
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io")
    return deploy_line(_detail("completed", GOOD_METRICS), strategy_id="strat_n", ctx=ctx)


def test_the_released_deploy_line_offers_paper_after_the_live_link(paper_sdk_released, monkeypatch):
    from keel.tools.outcomes import _paper_strings as ps

    url = "https://app.usekeel.io/deploy?intent=tokN"
    line = _deploy_line(monkeypatch)
    sentence = ps.FULL_DEPLOY_LINE_SENTENCE.full
    assert line == _HEAD_DEPLOY_LINE.format(url=url).replace(
        f"{url} — or do", f"{url} {sentence} — or do"
    )
    # The live link first, then the paper sentence, then the do-nothing clause.
    assert line.index(url) < line.index(sentence) < line.index("or do nothing")


# ── AC-06-33's released arm, whole (M8-regen; S06 §3.11 item 1, PT2-28) ──
#
# Under the fixture: both paper tools register, `paper` is in the default
# set, compute_numbers()'s tools_registry and tools_local_default each move
# by exactly two, the operating core's `paper` section is in the full-profile
# instructions, and every held touch-up of §3.11 item 1 takes its paper form:
# the deployments list's parameter and description (§3.5.1), the monitor's
# sentence (§3.5.7), keel_live_control's line and its guard's paper arm
# (§3.6, the guard driven by test_live_control_mirrors_the_guard_for_a_
# practice_row above), the share tool's target and description (§3.7) and
# the full-profile deploy line (§3.9 row 7, the two tests above). After
# teardown the registry equals the held one
# (test_release_restores_the_held_state). Count: exactly two paper tools and
# the six touch-ups. The held arm is tests/test_paper_sdk_hold.py.

_ROW3_SENTENCE = (
    "PAPER — keel_paper_deploy creates a simulated paper-trading run from a saved "
    "strategy and completes here; running with real capital is a web-app step."
)


def _surface_checker():
    import importlib.util

    path = _SDK_ROOT / "scripts" / "check_surface_routing.py"
    spec = importlib.util.spec_from_file_location("check_surface_routing_regen", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_released_arm_holds_whole(paper_sdk_released, monkeypatch, tmp_path):
    from keel.mcp.server import create_server
    from keel.tools.outcomes import OUTCOMES, get
    from keel.tools.outcomes._mcp_adapter import effective_description, effective_input_schema

    from tests._research_delta import RESEARCH_ADDITIONS, RESEARCH_MCP_ADDITIONS
    from tests._screeners_delta import SCREENER_ADDITIONS, SCREENER_MCP_ADDITIONS

    assert len(SCREENER_ADDITIONS) == 19 and SCREENER_ADDITIONS <= set(OUTCOMES)
    assert len(SCREENER_MCP_ADDITIONS) == 14
    held_numbers = {"tools_registry": 48, "tools_local_default": 45}
    assert sorted(n for n in OUTCOMES if "paper" in n) == [
        "keel_paper_control",
        "keel_paper_deploy",
    ]
    assert toolsets.PAPER_TOOLSET in toolsets._DEFAULT_TOOLSETS
    numbers = _surface_checker().compute_numbers()
    # The registry counts every screen command; the local MCP default counts
    # the 14 an MCP tools/list carries (L13: five are CLI-only).
    # Research (07 §2.2) adds its 24 commands, 8 of them MCP tools.
    screen_delta = {
        "tools_registry": len(SCREENER_ADDITIONS) + len(RESEARCH_ADDITIONS),
        "tools_local_default": len(SCREENER_MCP_ADDITIONS) + len(RESEARCH_MCP_ADDITIONS),
    }
    assert {k: numbers[k] - screen_delta[k] - v for k, v in held_numbers.items()} == {
        "tools_registry": 2,
        "tools_local_default": 2,
    }

    _local_full_env(monkeypatch, tmp_path)
    assert create_server().instructions.count(_ROW3_SENTENCE) == 1

    touched = {
        "keel_deployments_list": ("description", "input_schema"),
        "keel_live_monitor": ("description",),
        "keel_live_control": ("description",),
        "keel_share_create": ("description", "input_schema"),
    }
    for name, parts in touched.items():
        tool = get(name)
        if "description" in parts:
            assert effective_description(tool) == tool.paper_description != tool.description, name
        if "input_schema" in parts:
            published = effective_input_schema(tool)
            assert published == tool.paper_input_schema != tool.input_schema, name
    props = effective_input_schema(get("keel_deployments_list"))["properties"]
    assert "capital_class" in props
    share = effective_input_schema(get("keel_share_create"))["properties"]["target_type"]
    assert "paper" in share["enum"]
    assert effective_description(get("keel_live_monitor")).endswith(
        "Results include simulated (paper) runs, labelled by `venue`."
    )
    assert "keel_paper_control" in effective_description(get("keel_live_control"))
    # The sixth, the deploy line, is test_the_released_deploy_line_offers_paper_after_the_live_link.
    assert len(touched) + 2 == 6  # four descriptions, the guard's paper arm, the deploy line


def test_paper_descriptions_say_ask_before_stopping_a_run():
    """Agents swapped a running paper run for a new one without asking far more
    often than they hit the slot wall (prod, 2026-10-17..11-16), so every paper
    description that says a stop frees a slot also says to ask the user first.
    Control: the deploy description still says `update` keeps a run's record."""
    from keel.tools.outcomes.paper_control import (
        PAPER_CONTROL_DESCRIPTION,
        PAPER_CONTROL_LISTED_DESCRIPTION,
    )
    from keel.tools.outcomes.paper_deploy import (
        PAPER_DEPLOY_DESCRIPTION,
        PAPER_DEPLOY_LISTED_DESCRIPTION,
    )

    texts = {
        "deploy": PAPER_DEPLOY_DESCRIPTION,
        "deploy_listed": PAPER_DEPLOY_LISTED_DESCRIPTION,
        "control": PAPER_CONTROL_DESCRIPTION,
        "control_listed": PAPER_CONTROL_LISTED_DESCRIPTION,
    }
    assert len(texts) == 4 and all(texts.values())  # non-vacuity
    missing = [name for name, text in texts.items() if "ask the user" not in text]
    assert not missing, missing
    assert "`update` that run instead" in PAPER_DEPLOY_DESCRIPTION
