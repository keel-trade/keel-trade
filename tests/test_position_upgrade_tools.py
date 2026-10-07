"""The SDK's half of the position-layer upgrade path (Q-2448, spec 04).

- 04-R21 ``keel_strategy_upgrade``: the dry run writes nothing; ``apply``
  writes ONE new version through ``PATCH`` with ``expected_source_hash``; a
  moved HEAD is a 409 that writes nothing; an assisted group with no answers
  returns its questions and ``source: None``; an answer outside the choices
  is ``upgrade_answer_invalid``; ``keep`` on every question changes nothing.
  The real vendored planner runs for the assisted arms (it needs no
  position-layer component, so it runs before lane L1 lands).
- 04-R22 ``keel_strategy_push`` carries the server's validation, or says it
  is unavailable — never silently omitted.
- 04-R23 the validation line leads with the upgrade issue and names the tool.
- 04-R26 ``keel_components_get`` returns the full deprecation record.
- 04-R34 / D-42 the three live tools map keel-api's 422
  ``KNOWN_ISSUE_UPGRADE_REQUIRED`` to ``known_issue_upgrade_required`` whose
  suggestion names the upgrade (or, on the listed profile, D-48's fallback).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from keel.errors import ConflictError, KeelError, translate_http_error
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


VSEF = """Globals(target_timeframe="1d")
Universe(symbols=["BTC"])
Execution(rebalance="on_change")

Pipeline([
    PriceDataLoader(),
    Store("ohlcv"),
    SMA(period=20), ThresholdCross(upper=0.0, lower=0.0), Store("e"),
    {"vs": [ValueStopExitFilter(entry_slot="e", ohlcv_slot="ohlcv", retracement_pct=0.03, lookback=2)]},
    MaskOr(),
    Store("x"),
    PositionStateMachine(entry_slot="e", exit_slot="x"),
    EqualWeightSizer(target_leverage=1.0),
])
"""

HEAD = {
    "source": VSEF,
    "component_lock": {"ValueStopExitFilter": 1, "PositionStateMachine": 2},
    "commit_id": "cmt_old",
    "sequence_number": 4,
    "source_hash": "sha_old",
}


def _client(head: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.get.return_value = dict(head or HEAD)
    return client


def _run(args: dict, client: MagicMock) -> dict:
    return get("keel_strategy_upgrade").handler(args, _ctx(client)).to_envelope()


# ─── 04-R21 keel_strategy_upgrade ───────────────────────────────────────────


class TestStrategyUpgrade:
    def test_assisted_without_answers_returns_questions_and_writes_nothing(self):
        client = _client()
        env = _run({"strategy_id": "str_1"}, client)
        client.get.assert_called_once_with("/v1/strategies/str_1/versions/HEAD/source")
        client.patch.assert_not_called()
        assert env["status"] == "needs_answers"
        assert env["source"] is None and env["applied"] is False
        assert [g["mode"] for g in env["groups"]] == ["assisted"]
        # Non-vacuity: the real planner asked its scripted question.
        (q,) = env["questions"]
        assert q["id"] == "Q-VSEF" and {c["id"] for c in q["choices"]} == {"trade_peak", "keep"}
        assert q["group"] == env["groups"][0]["path"]

    def test_an_answer_outside_the_choices_is_refused_by_code(self):
        client = _client()
        with pytest.raises(KeelError) as exc:
            _run({"strategy_id": "str_1", "answers": ["Q-VSEF=maybe"]}, client)
        assert exc.value.error_code == "upgrade_answer_invalid"
        assert exc.value.detail["code"] == "UPGRADE_ANSWER_INVALID"
        client.patch.assert_not_called()

    def test_keep_on_every_question_changes_nothing_and_writes_nothing(self):
        client = _client()
        env = _run({"strategy_id": "str_1", "answers": ["Q-VSEF=keep"], "apply": True}, client)
        assert env["status"] == "unchanged"
        assert env["source"] == VSEF
        client.patch.assert_not_called()

    def _engine_returns(self, monkeypatch, upgraded: str = "UPGRADED") -> list:
        from keel.tools.outcomes import strategy_upgrade

        calls: list = []

        def fake(source, lock, answers):
            calls.append((source, lock, answers))
            return {
                "groups": [{"mode": "mechanical", "path": "step[7]", "questions": []}],
                "source": upgraded,
                "diff": "--- before\n+++ after\n",
                "validation": {"ok": True, "errors": [], "warnings": []},
                "component_lock": {"TradeManager": 1, "EqualWeightSizer": 1},
            }

        monkeypatch.setattr(strategy_upgrade, "run_upgrade", fake)
        return calls

    def test_dry_run_returns_plan_diff_and_source_without_writing(self, monkeypatch):
        calls = self._engine_returns(monkeypatch)
        client = _client()
        env = _run({"strategy_id": "str_1"}, client)
        assert env["status"] == "ready" and env["applied"] is False
        assert env["source"] == "UPGRADED" and env["diff"].startswith("--- before")
        assert calls == [(VSEF, HEAD["component_lock"], [])]
        client.patch.assert_not_called()

    def test_apply_writes_one_new_version_against_the_read_head(self, monkeypatch):
        self._engine_returns(monkeypatch)
        client = _client()
        client.patch.return_value = {"head_commit_id": "cmt_new", "current_sequence": 5}
        env = _run({"strategy_id": "str_1", "apply": True}, client)
        client.patch.assert_called_once()
        path, kwargs = client.patch.call_args.args[0], client.patch.call_args.kwargs
        assert path == "/v1/strategies/str_1"
        body = kwargs["json"]
        assert body["source"] == "UPGRADED"
        assert body["expected_source_hash"] == "sha_old"
        assert body["component_lock"] == {"TradeManager": 1, "EqualWeightSizer": 1}
        assert body["message"] == "Upgrade to TradeManager (position layer, Q-2448)"
        assert env["applied"] is True and env["status"] == "applied"
        assert env["previous_commit_id"] == "cmt_old" and env["commit_id"] == "cmt_new"
        assert env["previous_commit_id"] != env["commit_id"]
        # The comparison is the existing tools (D-29): run both, compare.
        assert any("keel_backtest_compare" in line for line in env["next"])

    def test_a_moved_head_is_a_conflict_that_writes_nothing_new(self, monkeypatch):
        self._engine_returns(monkeypatch)
        client = _client()
        client.patch.side_effect = translate_http_error(409, '{"detail": "source hash mismatch"}')
        with pytest.raises(ConflictError) as exc:
            _run({"strategy_id": "str_1", "apply": True}, client)
        assert "keel_strategy_upgrade again" in exc.value.suggestion
        assert client.patch.call_count == 1

    def test_nothing_deprecated_is_nothing_to_upgrade(self):
        clean = VSEF.replace(
            '    {"vs": [ValueStopExitFilter(entry_slot="e", ohlcv_slot="ohlcv", '
            'retracement_pct=0.03, lookback=2)]},\n    MaskOr(),\n    Store("x"),\n'
            '    PositionStateMachine(entry_slot="e", exit_slot="x"),\n',
            "",
        )
        assert "PositionStateMachine" not in clean  # the replace matched
        client = _client({**HEAD, "source": clean, "component_lock": {}})
        env = _run({"strategy_id": "str_1", "apply": True}, client)
        assert env["status"] == "nothing_to_upgrade" and env["groups"] == []
        client.patch.assert_not_called()

    def test_registered_on_the_cli_and_off_the_listed_profile(self):
        from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

        tool = get("keel_strategy_upgrade")
        assert tool.cli_path == ("strategy", "upgrade")
        assert tool.required_action == "strategy.update"
        # D-48: the 13,000-char listed ceiling has 42 chars of headroom, so
        # the tool stays off the listed profile; compose carries the source.
        assert "keel_strategy_upgrade" not in LISTED_PROFILE_TOOLS


# ─── 04-R22 keel_strategy_push validation ───────────────────────────────────


class TestPushValidation:
    def test_server_validation_is_carried_and_rendered(self):
        from keel.tools.outcomes.strategy_push import push_validation

        out = push_validation(
            {
                "validation": {
                    "valid": True,
                    "errors": [],
                    "warnings": [{"code": "DEPRECATED_COMPONENT", "message": "m"}],
                }
            }
        )
        assert out["validation"]["ok"] is True
        assert len(out["validation"]["warnings"]) == 1
        assert out["validation_line"].startswith("validation: 1 warning")

    def test_a_response_without_validation_says_unavailable(self):
        from keel.tools.outcomes.strategy_push import push_validation

        out = push_validation({"status": "pushed"})
        assert out["validation"] == {"unavailable": True}
        assert "unavailable" in out["validation_line"]

    def test_deprecations_ride_the_push_envelope(self):
        from keel.tools.outcomes.strategy_push import push_validation

        out = push_validation(
            {
                "validation": {"valid": True, "errors": [], "warnings": []},
                "deprecations": [
                    {
                        "component": "TakeProfitExit",
                        "version": 1,
                        "known_issue": {"id": "Q-2448", "summary": "s"},
                        "replacement_text": "t",
                    }
                ],
            }
        )
        assert out["deprecations"][0]["component"] == "TakeProfitExit"
        assert out["upgrade"].startswith("TakeProfitExit v1 is deprecated (known issue Q-2448: s)")


# ─── 04-R23 the validation line leads with the upgrade ──────────────────────


def test_the_upgrade_issue_leads_the_warnings_and_names_the_tool():
    from keel.tools.outcomes._mcp_adapter import _validation_line

    warnings = [{"code": f"W{i}", "message": "m"} for i in range(6)]
    warnings.append({"code": "POSITION_UPGRADE_AVAILABLE", "message": "upgrade"})
    line = _validation_line({"errors": [{"code": "E1", "message": "e"}], "warnings": warnings})
    parts = line.split(" — ", 1)[1]
    # Errors first, then the upgrade, inside the first five named issues.
    assert parts.index("E1") < parts.index("POSITION_UPGRADE_AVAILABLE") < parts.index("W0")
    assert "POSITION_UPGRADE_AVAILABLE: upgrade" in line
    assert "keel_strategy_upgrade" in line
    # Control: a line with no upgrade issue keeps the server's order and
    # names no upgrade tool.
    plain = _validation_line({"errors": [], "warnings": warnings[:6]})
    assert "keel_strategy_upgrade" not in plain


# ─── 04-R26 keel_components_get deprecation record ──────────────────────────


def test_component_detail_carries_the_full_deprecation_record():
    from keel.tools.outcomes.components_help import deprecation_record

    detail = {
        "status": "deprecated",
        "replacement": "TradeManager",
        "replacement_shape": {
            "head": "TradeManager",
            "text": "A TradeManager rule",
            "recipe": "q2448.stop",
            "uses": ["TradeManager"],
        },
        "known_issue": {"id": "Q-2448", "summary": "stale"},
    }
    assert deprecation_record(detail) == {
        "replacement": "TradeManager",
        "replacement_shape": {
            "head": "TradeManager",
            "text": "A TradeManager rule",
            "recipe": "q2448.stop",
        },
        "known_issue": {"id": "Q-2448", "summary": "stale"},
    }
    # Control: an active component's record gains nothing.
    assert deprecation_record({**detail, "status": "active"}) == {}


# ─── 04-R34 / D-42 the live tools map the refusal ───────────────────────────


def _refusal(action: str) -> KeelError:
    body = {
        "detail": {
            "detail": f"{action.capitalize()} blocked: this strategy uses components with a known issue.",
            "error": {
                "code": "KNOWN_ISSUE_UPGRADE_REQUIRED",
                "tier": "gate",
                "action": action,
                "strategy_id": "str_9",
                "commit_id": "cmt_9",
                "known_issues": [{"id": "Q-2448", "summary": "stale price"}],
                "components": [
                    {"name": "MaxDrawdownStopLoss", "version": 1, "known_issue": "Q-2448"}
                ],
                "upgrade_with": [
                    "keel_strategy_upgrade",
                    "keel strategy upgrade",
                    "Upgrade with agent",
                ],
            },
        }
    }
    return translate_http_error(422, json.dumps(body))


def _arm(tmp_path, monkeypatch, account_id: str = "acct_1") -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id=account_id)


def _assert_mapped(exc: KeelError, *, listed: bool = False) -> None:
    assert exc.error_code == "known_issue_upgrade_required"
    assert "MaxDrawdownStopLoss v1" in str(exc) and "Q-2448" in str(exc)
    if listed:
        assert "keel_strategy_compose" in exc.suggestion
        assert "keel_strategy_upgrade" not in exc.suggestion
    else:
        assert "keel_strategy_upgrade" in exc.suggestion
        assert exc.recovery_tool == "keel_strategy_upgrade"
        assert exc.recovery_tool_args == {"strategy_id": "str_9"}


class TestLiveRefusal:
    def test_resume_refusal(self, tmp_path, monkeypatch):
        _arm(tmp_path, monkeypatch)
        client = MagicMock()
        client.post.side_effect = _refusal("resume")
        with pytest.raises(KeelError) as exc:
            get("keel_live_control").handler(
                {"deployment_id": "dep_1", "action": "resume"}, _ctx(client)
            )
        _assert_mapped(exc.value)
        assert exc.value.detail["action"] == "resume"

    def test_update_refusal(self):
        client = MagicMock()
        client.post.side_effect = _refusal("update")
        with pytest.raises(KeelError) as exc:
            get("keel_live_update").handler({"deployment_id": "dep_1"}, _ctx(client))
        _assert_mapped(exc.value)

    def test_deploy_refusal(self, tmp_path, monkeypatch):
        _arm(tmp_path, monkeypatch)
        client = MagicMock()
        client.post.side_effect = [
            {
                "strategy_name": "s",
                "derived_schedule": "0 0 * * *",
                "weights": [],
                "known_issues": [],
            },
            {
                "handoff_url": "https://app/x",
                "intent_token": "t",
                "expires_at": "2026-12-01T00:00:00+00:00",
            },
            _refusal("deploy"),
        ]
        tool = get("keel_live_deploy")
        args = {
            "strategy_id": "str_9",
            "account_id": "acct_1",
            "direct": True,
            "schedule": "0 0 * * *",
        }
        token = tool.handler({**args, "preview": True}, _ctx(client)).to_envelope()[
            "confirmation_token"
        ]
        with pytest.raises(KeelError) as exc:
            tool.handler({**args, "preview": False, "confirmation_token": token}, _ctx(client))
        _assert_mapped(exc.value)

    def test_listed_profile_names_the_compose_fallback(self, monkeypatch):
        monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
        from keel.tools.outcomes._known_issue import known_issue_refusal

        mapped = known_issue_refusal(_refusal("deploy"))
        assert mapped is not None
        _assert_mapped(mapped, listed=True)

    def test_other_refusals_pass_through_unchanged(self):
        from keel.tools.outcomes._known_issue import known_issue_refusal

        other = translate_http_error(422, json.dumps({"detail": {"code": "STRATEGY_INVALID"}}))
        assert known_issue_refusal(other) is None
        client = MagicMock()
        client.post.side_effect = other
        with pytest.raises(KeelError) as exc:
            get("keel_live_update").handler({"deployment_id": "dep_1"}, _ctx(client))
        assert exc.value is other
