"""Tests for first-session ownership outcome surface."""

from __future__ import annotations

from typing import Any

import pytest
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


class _FakeClient:
    def __init__(self, payloads: dict[str, Any]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(("GET", path, params or None))
        return self.payloads.get(path, {})

    def post(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(("POST", path, kwargs or None))
        return self.payloads.get(path, {})


def test_ownership_status_registers_cli_path():
    tool = OUTCOMES["keel_strategy_readiness"]

    assert tool.cli_path == ("ownership", "status")
    assert tool.input_schema["properties"]["strategy_id"]["x-cli-positional"] is True


def test_ownership_group_has_cli_help():
    """`keel --help` must describe the group, not render it blank."""
    from keel.tools.outcomes._cli_adapter import _GROUP_HELP

    assert _GROUP_HELP[("ownership",)]


class TestOneRequestToTheStrategyScopedRoute:
    """Q-0533 / spec 20 §6.1: the SDK reads the projection with ONE request,
    to keel-api's strategy-scoped route. The old two-hop
    (list work sessions → read the newest session's ownership) fired a
    guaranteed 404 on the wrong host (Q-0500) and, once served, still had
    nothing to say about a strategy with no work session — the state an
    external agent meets most often."""

    def test_fetch_makes_exactly_one_request_to_strategy_work(self):
        from keel.tools.outcomes._ownership import fetch_ownership_projection

        client = _FakeClient({"/v1/strategy-work": {"overall_status": "not_started"}})
        ctx = ToolContext(api_client=client)

        projection = fetch_ownership_projection(ctx, "str_1")

        assert projection is not None
        assert client.calls == [("GET", "/v1/strategy-work", {"strategy_id": "str_1"})]

    def test_fetch_never_touches_the_work_session_routes(self):
        from keel.tools.outcomes._ownership import fetch_ownership_projection

        client = _FakeClient({"/v1/strategy-work": {"overall_status": "not_started"}})
        ctx = ToolContext(api_client=client)
        fetch_ownership_projection(ctx, "str_1")
        assert not any("strategy-work-sessions" in path for _, path, _ in client.calls)

    def test_strategy_get_carries_the_hint_from_one_extra_request(self):
        client = _FakeClient(
            {
                "/v1/strategies/str_abc": {"strategy_id": "str_abc"},
                "/v1/strategy-work": {
                    "overall_status": "owned_baseline",
                    "missing_evidence": ["failure_modes"],
                    "live_readiness_blockers": ["no_diagnosis"],
                },
            }
        )
        ctx = ToolContext(api_client=client, is_tty=False)

        env = OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()

        assert env["ownership_status"] == "owned_baseline"
        assert env["missing_evidence"] == ["failure_modes"]
        assert [path for _, path, _ in client.calls].count("/v1/strategy-work") == 1

    def test_no_ownership_hint_suppresses_the_request_entirely(self):
        client = _FakeClient({"/v1/strategies/str_abc": {"strategy_id": "str_abc"}})
        ctx = ToolContext(api_client=client, is_tty=False)

        OUTCOMES["keel_strategy_get"].handler(
            {"strategy_id": "str_abc", "skip_readiness": True}, ctx
        )

        assert not any("strategy-work" in path for _, path, _ in client.calls)


class TestOwnershipStatusBody:
    def test_returns_the_projection_fields(self):
        client = _FakeClient(
            {
                "/v1/strategy-work": {
                    "overall_status": "owned_baseline",
                    "next_recommended_action": {"kind": "show_failure_modes"},
                    "missing_evidence": ["failure_modes"],
                    "live_readiness_blockers": ["no_diagnosis"],
                }
            }
        )
        ctx = ToolContext(api_client=client)

        env = (
            OUTCOMES["keel_strategy_readiness"].handler({"strategy_id": "str_1"}, ctx).to_envelope()
        )

        assert env["resource_uri"] == "keel://ownership/strategy/str_1"
        assert env["projection_available"] is True
        assert env["ownership_status"] == "owned_baseline"
        assert env["next_recommended_action"]["kind"] == "show_failure_modes"
        assert env["missing_evidence"] == ["failure_modes"]

    def test_a_strategy_with_no_work_session_reports_the_served_projection(self):
        """Spec 20 §2.4 N1 through the SDK: keel-api answers 200 with a real
        projection whose session_id is null, so 'not started' reaches the
        agent as COMPUTED evidence, not as a hardcoded guess."""
        client = _FakeClient(
            {
                "/v1/strategy-work": {
                    "session_id": None,
                    "overall_status": "not_started",
                    "missing_evidence": [
                        "strategy_brief",
                        "baseline_evidence",
                        "failure_modes",
                    ],
                    "live_readiness_blockers": ["no_baseline"],
                }
            }
        )
        ctx = ToolContext(api_client=client)

        env = (
            OUTCOMES["keel_strategy_readiness"].handler({"strategy_id": "str_1"}, ctx).to_envelope()
        )

        assert env["projection_available"] is True
        assert env["projection"]["session_id"] is None
        assert env["ownership_status"] == "not_started"
        assert "unavailable_reason" not in env


class TestHonestAbsenceSurvives:
    """Q-0500's rule outlives its cause: when there IS no projection to read,
    the surfaces say so and say why — and assert no evidence they never saw."""

    def _not_found_client(self):
        from keel.errors import NotFoundError

        class _Missing:
            calls: list = []

            def get(self, path, **params):
                raise NotFoundError("HTTP 404: strategy not found")

        return _Missing()

    def test_unknown_strategy_is_reported_as_not_visible_without_fabrication(self):
        from keel.tools.outcomes._ownership import UNAVAILABLE_STRATEGY_NOT_VISIBLE

        ctx = ToolContext(api_client=self._not_found_client())

        env = (
            OUTCOMES["keel_strategy_readiness"].handler({"strategy_id": "str_1"}, ctx).to_envelope()
        )

        assert env["projection_available"] is False
        assert env["unavailable_code"] == UNAVAILABLE_STRATEGY_NOT_VISIBLE
        assert "str_1" in env["unavailable_reason"]
        # The anti-fabrication assertions Q-0500 added, retained verbatim.
        assert env.get("ownership_status") != "not_started"
        assert "missing_evidence" not in env
        assert "live_readiness_blockers" not in env

    def test_a_read_failure_is_reported_as_such_and_logged(self, caplog):
        import logging

        from keel.errors import KeelError
        from keel.tools.outcomes._ownership import UNAVAILABLE_READ_FAILED, fetch_projection

        class _Boom:
            def get(self, path, **params):
                raise KeelError("HTTP 500: upstream sad", error_code="server_error")

        ctx = ToolContext(api_client=_Boom())
        with caplog.at_level(logging.WARNING, logger="keel.tools.outcomes._ownership"):
            fetch = fetch_projection(ctx, "str_1")

        assert fetch.projection is None
        assert fetch.unavailable_code == UNAVAILABLE_READ_FAILED
        assert any("ownership projection fetch failed" in r.getMessage() for r in caplog.records)

    def test_an_envelope_hint_adds_no_fields_when_the_projection_is_absent(self):
        """A strategy read must not grow a 'why not' key on its happy path."""
        from keel.errors import NotFoundError

        class _Missing(_FakeClient):
            def get(self, path, **params):
                if path == "/v1/strategy-work":
                    raise NotFoundError("HTTP 404: strategy not found")
                return super().get(path, **params)

        ctx = ToolContext(
            api_client=_Missing({"/v1/strategies/str_abc": {"strategy_id": "str_abc"}}),
            is_tty=False,
        )
        env = OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()

        assert "unavailable_reason" not in env
        assert "ownership_status" not in env
        assert "missing_evidence" not in env

    def test_the_kill_switch_still_makes_zero_requests(self, monkeypatch):
        """PROJECTION_ROUTES_SERVED is the ordering interlock AND the kill
        switch (spec 20 §7): off means not one request leaves the process."""
        from keel.tools.outcomes._ownership import UNAVAILABLE_NOT_SERVED, fetch_projection

        monkeypatch.setattr("keel.tools.outcomes._ownership.PROJECTION_ROUTES_SERVED", False)
        client = _FakeClient({})
        ctx = ToolContext(api_client=client)

        fetch = fetch_projection(ctx, "str_1")

        assert client.calls == []
        assert fetch.projection is None
        assert fetch.unavailable_code == UNAVAILABLE_NOT_SERVED
        assert "/strategies/str_1" in fetch.unavailable_reason


class TestProjectionFetchInvariant:
    """Every admission arm has its invariant arm (.claude/rules/lessons.md).

    `fetch_projection` returns on five arms; each must carry either the
    projection or a code AND a reason. A bare ProjectionFetch() would render
    as `projection_available: false` with a null reason — the least useful
    answer a surface can give."""

    def test_neither_is_refused(self):
        from keel.tools.outcomes._ownership import ProjectionFetch

        with pytest.raises(ValueError, match="never neither"):
            ProjectionFetch()

    def test_both_is_refused(self):
        from keel.tools.outcomes._ownership import ProjectionFetch

        with pytest.raises(ValueError, match="never both"):
            ProjectionFetch(projection={}, unavailable_code="x", unavailable_reason="y")

    def test_a_code_without_a_reason_is_refused(self):
        from keel.tools.outcomes._ownership import ProjectionFetch

        with pytest.raises(ValueError, match="unavailable_reason"):
            ProjectionFetch(unavailable_code="x")

    @pytest.mark.parametrize(
        "client",
        [
            "not_served",
            "not_found",
            "keel_error",
            "unexpected_error",
            "non_dict_body",
        ],
    )
    def test_every_failure_arm_satisfies_the_invariant(self, client, monkeypatch):
        """Drives each arm for real — constructing the object IS the check."""
        from keel.errors import KeelError, NotFoundError
        from keel.tools.outcomes._ownership import fetch_projection

        class _Arm:
            def get(self, path, **params):
                if client == "not_found":
                    raise NotFoundError("HTTP 404")
                if client == "keel_error":
                    raise KeelError("HTTP 500")
                if client == "unexpected_error":
                    raise RuntimeError("boom")
                return ["not", "a", "dict"]

        if client == "not_served":
            monkeypatch.setattr("keel.tools.outcomes._ownership.PROJECTION_ROUTES_SERVED", False)

        fetch = fetch_projection(ToolContext(api_client=_Arm()), "str_1")

        assert fetch.projection is None
        assert fetch.unavailable_code
        assert fetch.unavailable_reason


class TestMcpResource:
    def test_resource_returns_the_projection(self):
        import json

        from keel.tools.outcomes import _ownership

        captured = {}

        def _fake_fetch(ctx, strategy_id):
            captured["app_url"] = ctx.app_url
            return _ownership.ProjectionFetch(
                projection={"overall_status": "owned_baseline", "missing_evidence": []}
            )

        payload = _resource_payload(_fake_fetch)
        assert json.loads(payload)["projection"]["overall_status"] == "owned_baseline"

    def test_resource_reports_honest_absence_without_fabrication(self):
        import json

        from keel.tools.outcomes import _ownership

        def _fake_fetch(ctx, strategy_id):
            return _ownership.ProjectionFetch(
                unavailable_code=_ownership.UNAVAILABLE_STRATEGY_NOT_VISIBLE,
                unavailable_reason="nope",
            )

        body = json.loads(_resource_payload(_fake_fetch))
        assert body["projection_available"] is False
        assert body["unavailable_code"] == _ownership.UNAVAILABLE_STRATEGY_NOT_VISIBLE
        assert "missing_evidence" not in body
        assert "live_readiness_blockers" not in body

    def test_resource_honors_keel_app_url(self, monkeypatch):
        monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.example")
        captured = {}

        def _fake_fetch(ctx, strategy_id):
            from keel.tools.outcomes import _ownership

            captured["app_url"] = ctx.app_url
            return _ownership.ProjectionFetch(unavailable_reason="x", unavailable_code="y")

        _resource_payload(_fake_fetch)
        assert captured["app_url"] == "https://staging-app.example"


def _resource_payload(fake_fetch) -> str:
    """Drive the real resource body with a stubbed fetch.

    ``keel.mcp.server.ownership_resource_payload`` IS the registered
    resource's implementation (the decorator delegates to it in one line), so
    this exercises the shipped code path rather than a re-implementation.
    """
    import keel.tools.outcomes._ownership as ownership_mod
    from keel.mcp.server import ownership_resource_payload

    original = ownership_mod.fetch_projection
    ownership_mod.fetch_projection = fake_fetch
    try:
        return ownership_resource_payload("str_1")
    finally:
        ownership_mod.fetch_projection = original


def test_resource_is_registered_and_delegates_to_the_shared_body():
    """Guards the guard: the tests above only mean something if the
    registered resource really is that function."""
    import inspect

    from keel.mcp import server as server_mod

    source = inspect.getsource(server_mod.create_server)
    assert 'mcp.resource("keel://ownership/strategy/{strategy_id}")' in source
    assert "return ownership_resource_payload(strategy_id)" in source
