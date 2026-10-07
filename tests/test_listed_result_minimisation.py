"""Round-2 audit fixes on the listed results and copy (Q-2268, Q-2269).

The OpenAI app-review read-through (`scratchpad/audit/codex-a2/FINDINGS.md`
F7–F9, 2026-10-01) asked for internal account identifiers out of listed
results, a slim readiness projection, an honest `delivered` on feedback, the
exact scope of a hidden-source share, and the free-plan wall's D-12 fallback
sentence; the review-case walk found no listed tool that lists a strategy's
recent runs. Each arm here is a seeded-defect guard with a control:

* `keel_account_status` / `keel_connection_check`: no `principal_id` /
  `org_id` on the LISTED profile; the full profile keeps them (control);
* `keel_strategy_history`: no raw `client_name` / `auth_surface` on listed,
  `modified_via` kept; full keeps both (control);
* `keel_strategy_readiness`: the listed `projection` is the allow-list
  (`LISTED_PROJECTION_FIELDS`), never the session id, UI state or artifact
  payloads; full keeps the whole projection (control);
* `keel_feedback`: a 200 whose note says the row could not be persisted is
  `delivered: false`, `stored: false`; a stored row is both true (control);
* `keel_strategy_get.recent_runs`: newest first, capped, the headline
  metrics as the backtest card quotes them, empty when the listing fails;
* the hosted no-credential remedy names no client's menu;
* the free-plan wall's plan sentence is D-12's recorded fallback.

Proof it can fail (run 2026-10-01, each reverted by reversing the edit):
drop the `is_listed_profile()` gate in `status.py` — the status arm reds and
its full-profile control stays green; same for `doctor.py`, `strategy_log.py`
and `ownership_status.py`; set `delivered = True` unconditionally in
`feedback.py` — the persist-failure arm reds; return `rows` unsorted in
`recent_runs` — the ordering arm reds.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

import pytest
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap


_bootstrap()

_PROFILE_ENV = ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS")


@contextmanager
def _profile(profile: str):
    saved = {k: os.environ.get(k) for k in _PROFILE_ENV}
    for k in _PROFILE_ENV:
        os.environ.pop(k, None)
    if profile == "listed":
        # The profile alone: these arms drive handlers with a fake client,
        # and hosted execution mode would demand a bound request credential.
        os.environ.update(KEEL_SERVER_PROFILE="listed")
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeClient:
    def __init__(self, payloads: dict[str, Any], *, fail: set[str] = frozenset()) -> None:
        self.payloads = payloads
        self.fail = set(fail)
        self.calls: list[str] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(path)
        if path in self.fail:
            raise RuntimeError(f"{path} down")
        return self.payloads.get(path, {})

    def post(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(path)
        return self.payloads.get(path, {})


ME = {
    "principal": {"id": "prn_abc123", "display_name": "Ada"},
    "org": {"id": "org_xyz789", "name": "Ada's Org", "plan": "trader"},
    "credential_scopes": ["strategy.read"],
}


# ── keel_account_status / keel_connection_check ────────────────────────


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_account_status_identity_carries_no_internal_ids_on_listed(monkeypatch, tmp_path, profile):
    import keel.config as _config

    cfg = tmp_path / "config.yaml"
    cfg.write_text("api_key: dummy_test_key\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.setattr("keel.auth.get_identity", lambda: dict(ME))
    monkeypatch.setattr("keel.client.KeelClient.get", lambda self, path, **kw: {"balances": []})

    with _profile(profile):
        env = OUTCOMES["keel_account_status"].handler({}, ToolContext(is_tty=False)).to_envelope()
    identity = env["identity"]
    assert identity["org_name"] == "Ada's Org" and identity["plan"] == "trader"
    if profile == "listed":
        assert "principal_id" not in identity and "org_id" not in identity, identity
        assert identity["display_name"] == "Ada"
        assert "prn_abc123" not in str(env) and "org_xyz789" not in str(env)
        assert env["view"]["object"] == "account"
    else:
        assert identity["principal_id"] == "prn_abc123"
        assert identity["org_id"] == "org_xyz789"
        assert env["view"]["object"] == "org_xyz789"


def test_account_status_never_labels_the_account_with_an_email(monkeypatch, tmp_path):
    import keel.config as _config

    cfg = tmp_path / "config.yaml"
    cfg.write_text("api_key: dummy_test_key\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    me = {**ME, "principal": {"id": "prn_abc123", "display_name": "ada@example.com"}}
    monkeypatch.setattr("keel.auth.get_identity", lambda: me)
    monkeypatch.setattr("keel.client.KeelClient.get", lambda self, path, **kw: {"balances": []})
    with _profile("listed"):
        env = OUTCOMES["keel_account_status"].handler({}, ToolContext(is_tty=False)).to_envelope()
    assert "display_name" not in env["identity"]
    assert "ada@example.com" not in str(env)


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_connection_check_auth_detail_carries_no_internal_ids_on_listed(
    monkeypatch, tmp_path, profile
):
    import keel.config as _config

    cfg = tmp_path / "config.yaml"
    cfg.write_text("api_key: dummy_test_key\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.setattr("keel.auth.get_identity", lambda: dict(ME))
    client = _FakeClient({"/v1/me": dict(ME)})
    with _profile(profile):
        env = (
            OUTCOMES["keel_connection_check"]
            .handler({}, ToolContext(api_client=client, is_tty=False))
            .to_envelope()
        )
    auth = next(c for c in env["checks"] if c["name"] == "auth")
    assert auth["ok"] is True and auth["detail"]["org_name"] == "Ada's Org"
    if profile == "listed":
        assert set(auth["detail"]) == {"org_name", "plan"}
    else:
        assert auth["detail"]["principal_id"] == "prn_abc123"
        assert auth["detail"]["org_id"] == "org_xyz789"


# ── keel_strategy_history ──────────────────────────────────────────────

VERSIONS = {
    "data": [
        {
            "sequence_number": 2,
            "commit_id": "cmt_2",
            "created_at": "2026-09-30T00:00:00Z",
            "client_name": "claude.ai",
            "auth_surface": "hosted-mcp",
        }
    ],
    "pagination": {},
}


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_history_entries_carry_no_raw_provenance_on_listed(profile):
    client = _FakeClient({"/v1/strategies/str_x/versions": VERSIONS})
    with _profile(profile):
        env = (
            OUTCOMES["keel_strategy_history"]
            .handler({"strategy_id": "str_x"}, ToolContext(api_client=client, is_tty=False))
            .to_envelope()
        )
    [entry] = env["commits"]
    assert entry["commit_id"] == "cmt_2"
    assert entry["modified_via"].startswith("modified via claude.ai (hosted-mcp)")
    if profile == "listed":
        assert "client_name" not in entry and "auth_surface" not in entry, entry
    else:
        assert entry["client_name"] == "claude.ai" and entry["auth_surface"] == "hosted-mcp"


# ── keel_strategy_readiness ────────────────────────────────────────────

PROJECTION = {
    "session_id": "sws_secret",
    "strategy_id": "str_x",
    "current_stage": "baseline",
    "role_mode": "guided",
    "overall_status": "owned_baseline",
    "ui_state": {"dismissed_cards": [], "last_action_id": "act_1"},
    "proof_steps": [{"kind": "strategy_brief", "artifact": {"payload": {"big": "blob"}}}],
    "items": [{"artifact_id": "art_1"}],
    "next_recommended_action": {"kind": "show_failure_modes"},
    "missing_evidence": ["failure_modes"],
    "live_readiness_blockers": ["no_diagnosis"],
    "latest_backtest": {
        "backtest_id": "btr_9",
        "status": "completed",
        "source_hash": "deadbeef",
        "commit_id": "cmt_2",
        "sequence_number": 2,
        "date_range": {"start": "2025-01-01", "end": "2025-06-01"},
        "metrics": {"sharpe": 1.1},
        "artifact_s3_key": "s3://bucket/key",
        "completed_at": "2026-09-30T00:00:00Z",
    },
    "workflow_state_version": 3,
    "updated_at": "2026-09-30T00:00:00Z",
}


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_readiness_projection_is_the_allow_list_on_listed(profile):
    from keel.tools.outcomes.ownership_status import (
        LISTED_LATEST_BACKTEST_FIELDS,
        LISTED_PROJECTION_FIELDS,
    )

    client = _FakeClient({"/v1/strategy-work": dict(PROJECTION)})
    with _profile(profile):
        env = (
            OUTCOMES["keel_strategy_readiness"]
            .handler({"strategy_id": "str_x"}, ToolContext(api_client=client, is_tty=False))
            .to_envelope()
        )
    assert env["projection_available"] is True
    assert env["ownership_status"] == "owned_baseline"
    projection = env["projection"]
    if profile == "listed":
        assert set(projection) <= set(LISTED_PROJECTION_FIELDS)
        assert set(projection["latest_backtest"]) <= set(LISTED_LATEST_BACKTEST_FIELDS)
        assert projection["latest_backtest"]["backtest_id"] == "btr_9"
        assert projection["current_stage"] == "baseline"
        for secret in ("sws_secret", "act_1", "blob", "art_1", "s3://", "deadbeef"):
            assert secret not in str(env), secret
        assert "live_readiness_blockers" not in env
        # Non-vacuity: the allow-list is not empty and every kept key is served.
        assert len(projection) >= 5
    else:
        assert projection["session_id"] == "sws_secret"
        assert projection["latest_backtest"]["artifact_s3_key"] == "s3://bucket/key"


# ── keel_feedback ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "response,delivered",
    [
        ({"status": "ok", "feedback_id": "fbk_1"}, True),
        ({"status": "ok", "feedback_id": "fbk_1", "note": "kind 'rant' stored as-is"}, True),
        (
            {"status": "ok", "feedback_id": "fbk_1", "note": "feedback could not be persisted"},
            False,
        ),
        (
            {
                "status": "ok",
                "feedback_id": "fbk_1",
                "note": "a; Feedback could NOT be persisted; b",
            },
            False,
        ),
    ],
    ids=["stored", "stored-with-note", "unpersisted", "unpersisted-among-notes"],
)
def test_feedback_delivered_is_the_stored_fact(response, delivered):
    client = _FakeClient({"/v1/feedback": response})
    env = (
        OUTCOMES["keel_feedback"]
        .handler({"text": "hi", "kind": "praise"}, ToolContext(api_client=client, is_tty=False))
        .to_envelope()
    )
    assert env["status"] == "ok" and "code" not in env
    assert env["delivered"] is delivered and env["stored"] is delivered
    assert env["feedback_id"] == "fbk_1"
    if not delivered:
        assert "could not be persisted" in env["note"].lower()
    assert client.calls == ["/v1/feedback"]  # non-vacuity: the note was sent


# ── keel_strategy_get.recent_runs ──────────────────────────────────────


def _run(run_id: str, seq: int, completed: str | None, status: str = "completed") -> dict:
    return {
        "id": run_id,
        "status": status,
        "sequence_number": seq,
        "start_date": "2025-01-01",
        "end_date": "2025-06-01",
        "completed_at": completed,
        "queued_at": "2026-09-01T00:00:00Z",
        "metrics": {
            "sharpe_ratio": 1.5,
            "total_return_pct": 12.0,
            "max_drawdown": 4.0,
            "win_rate_pct": 55.0,
            "total_trades": 10,
        },
    }


RUNS = [
    _run("btr_old", 1, "2026-09-10T00:00:00Z"),
    _run("btr_new", 3, "2026-09-30T00:00:00Z"),
    _run("btr_mid", 2, "2026-09-20T00:00:00Z"),
    _run("btr_queued", 3, None, status="queued"),
    _run("btr_5", 1, "2026-09-05T00:00:00Z"),
    _run("btr_6", 1, "2026-09-04T00:00:00Z"),
    _run("btr_7", 1, "2026-09-03T00:00:00Z"),
]


def _get(runs, *, fail: bool = False) -> dict:
    client = _FakeClient(
        {
            "/v1/strategies/str_x": {"strategy_id": "str_x", "id": "str_x", "name": "X"},
            "/v1/backtests": {"data": runs, "pagination": {}},
        },
        fail={"/v1/backtests"} if fail else set(),
    )
    args = {"strategy_id": "str_x", "skip_readiness": True}
    return (
        OUTCOMES["keel_strategy_get"]
        .handler(args, ToolContext(api_client=client, is_tty=False))
        .to_envelope()
    )


def test_recent_runs_are_newest_first_capped_and_card_shaped():
    from keel.tools.outcomes.strategy_get import RECENT_RUNS_LIMIT

    env = _get(RUNS)
    runs = env["recent_runs"]
    assert [r["run_id"] for r in runs] == ["btr_new", "btr_mid", "btr_old", "btr_5", "btr_6"]
    assert len(runs) == RECENT_RUNS_LIMIT == 5 and len(RUNS) > RECENT_RUNS_LIMIT
    newest = runs[0]
    assert newest["status"] == "completed" and newest["version"] == 3
    assert newest["window"]["start"] == "2025-01-01" and newest["window"]["end"] == "2025-06-01"
    assert newest["completed_at"] == "2026-09-30T00:00:00Z"
    # The headline tiles as the backtest card quotes them: the drawdown is
    # signed and the active-period Sharpe is the one named `sharpe`.
    assert newest["metrics"]["max_drawdown_pct"] == -4.0
    assert newest["metrics"]["total_return_pct"] == 12.0
    assert set(newest["metrics"]) <= {
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe",
        "win_rate_pct",
    }
    assert "sharpe" in newest["metrics"]


def test_recent_runs_keep_every_status_and_survive_a_failed_listing():
    env = _get([RUNS[3], RUNS[1]])
    assert [r["run_id"] for r in env["recent_runs"]] == ["btr_new", "btr_queued"]
    assert env["recent_runs"][1]["status"] == "queued"
    assert env["recent_runs"][1]["metrics"]  # whatever the row carried
    assert _get([])["recent_runs"] == []
    failed = _get(RUNS, fail=True)
    assert failed["recent_runs"] == [] and failed["metadata"]["strategy_id"] == "str_x"


def test_recent_runs_is_declared_in_the_strategy_output_schema():
    from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS

    props = OUTPUT_SCHEMAS["keel_strategy_get"]["properties"]
    assert props["recent_runs"]["type"] == "array"
    assert {"run_id", "status", "version", "message", "window", "completed_at", "metrics"} <= set(
        props["recent_runs"]["items"]["properties"]
    )


def test_recent_runs_name_each_versions_commit_message():
    """Q-2505: "v3" alone does not say which variant it is. Each row carries
    its version's commit message; a commit with none is null, not "" and not
    a neighbour's message."""
    labelled = [
        {**RUNS[1], "commit_message": "BTC (rules unchanged)"},
        {**RUNS[2], "commit_message": "SOL (rules unchanged)"},
        {**RUNS[0], "commit_message": ""},
    ]
    runs = _get(labelled)["recent_runs"]
    assert [r["version"] for r in runs] == [3, 2, 1]  # non-vacuity: three versions
    assert [r["message"] for r in runs] == ["BTC (rules unchanged)", "SOL (rules unchanged)", None]


# ── the hosted no-credential remedy, and the free-plan wall ────────────


def test_hosted_auth_error_names_no_client():
    from keel.hosting import hosted_auth_error

    err = hosted_auth_error()
    text = (err.suggestion or "") + " " + str(err)
    for client in ("Claude Code", "/mcp", "ChatGPT", "claude.ai", "Cursor"):
        assert client not in text, client
    assert "re-authenticate" in text.lower() and "connector" in text.lower()


def test_free_plan_wall_sentence_is_the_d12_fallback():
    from keel.errors import FREE_PLAN_SENTENCE, quota_plans_line

    assert FREE_PLAN_SENTENCE == "Plans are changed in the Keel web app."
    assert quota_plans_line({"plan": "free", "unit": "backtest_runs"}) == FREE_PLAN_SENTENCE
    assert "paid" not in FREE_PLAN_SENTENCE.lower() and "more" not in FREE_PLAN_SENTENCE.lower()
    for plan in ("starter", "trader", "pro", None):
        assert quota_plans_line({"plan": plan, "unit": "backtest_runs"}) is None
