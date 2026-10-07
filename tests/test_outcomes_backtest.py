"""Tests for the backtest outcome tools.

Covers `keel_backtest_run` (submit + optional polling) and
`keel_backtest_summarize` (read-only summary).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from keel.errors import KeelError, NotFoundError
from keel.tools.outcomes import OUTCOMES

# Import the modules directly — they self-register on import. We do NOT
# rely on `_bootstrap()` here because the bootstrap whitelist is owned
# by a different agent in the same fan-out.
from keel.tools.outcomes import backtest_run as _bt_run_mod  # noqa: F401
from keel.tools.outcomes import backtest_summarize as _bt_sum_mod  # noqa: F401
from keel.tools.outcomes import backtest_watch as _bt_watch_mod  # noqa: F401
from keel.tools.outcomes._base import ToolContext

from pipeline_engine.backtest_config import BacktestConfig


# ─── shared fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    """Make polling instantaneous so tests don't sleep."""
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_MAX_S", 0.05)
    monkeypatch.setattr("keel.tools.outcomes.backtest_watch.time.sleep", lambda _s: None)


# ─── keel_backtest_run ───────────────────────────────────────────────────


def test_backtest_run_returns_envelope_with_hero_url(ctx):
    """Submitting with wait=false returns immediately with status_url."""
    submitted = {
        "id": "bt_abc123",
        "status": "queued",
        "strategy_id": "strat_xyz",
    }

    with patch("keel.client.KeelClient.post", return_value=submitted) as mock_post:
        tool = OUTCOMES["keel_backtest_run"]
        result = tool.handler(
            {
                "strategy_id": "strat_xyz",
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "wait": False,
            },
            ctx,
        )

    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert call_args.args[0] == "/v1/backtests"
    body = call_args.kwargs["json"]
    assert body["strategy_id"] == "strat_xyz"
    assert body["start_date"] == "2025-01-01"
    assert body["end_date"] == "2025-06-30"

    env = result.to_envelope()
    assert env["run_id"] == "bt_abc123"
    assert env["hero_url"] == "https://app.usekeel.io/backtests/bt_abc123?tab=tearsheet"
    assert env["share_url"] is None
    assert env["status_url"] == env["hero_url"]
    assert env["resource_uri"] == "keel://backtest/bt_abc123/results"
    assert env["status"] == "queued"


def test_backtest_run_preserves_valid_canonical_config(ctx):
    submitted = {"id": "bt_config", "status": "queued", "strategy_id": "strat_xyz"}

    with patch("keel.client.KeelClient.post", return_value=submitted) as mock_post:
        OUTCOMES["keel_backtest_run"].handler(
            {
                "strategy_id": "strat_xyz",
                "config": {
                    "init_cash": 25_000,
                    "fees": 0.002,
                    "slippage": 0.003,
                    "leverage": 7.5,
                },
                "wait": False,
            },
            ctx,
        )

    assert mock_post.call_args.kwargs["json"]["backtest_config"] == {
        "init_cash": 25_000.0,
        "fees": 0.002,
        "slippage": 0.003,
        "leverage": 7.5,
    }


@pytest.mark.parametrize(
    "config",
    [
        {"leverage": 0},
        {"leverage": -1},
        {"leverage": 101},
        {"leverage": float("nan")},
        {"unknown": 1},
    ],
)
def test_backtest_run_rejects_invalid_config_before_http(ctx, config):
    with patch("keel.client.KeelClient.post") as mock_post:
        with pytest.raises(KeelError) as exc_info:
            OUTCOMES["keel_backtest_run"].handler(
                {"strategy_id": "strat_xyz", "config": config, "wait": False}, ctx
            )

    assert exc_info.value.error_code == "invalid_backtest_config"
    mock_post.assert_not_called()


def test_backtest_run_rejects_invalid_config_before_auto_push(ctx):
    with (
        patch("keel.workspace.get_workspace") as get_workspace,
        patch("keel.workspace.push") as push,
        patch("keel.client.KeelClient.post") as post,
        pytest.raises(KeelError) as exc_info,
    ):
        OUTCOMES["keel_backtest_run"].handler(
            {
                "strategy_id": "strat_xyz",
                "config": {"leverage": 0},
                "auto_push": True,
                "wait": False,
            },
            ctx,
        )

    assert exc_info.value.error_code == "invalid_backtest_config"
    get_workspace.assert_not_called()
    push.assert_not_called()
    post.assert_not_called()


def test_backtest_run_exact_legacy_initial_capital_alias(ctx):
    submitted = {"id": "bt_legacy", "status": "queued", "strategy_id": "strat_xyz"}

    with patch("keel.client.KeelClient.post", return_value=submitted) as mock_post:
        OUTCOMES["keel_backtest_run"].handler(
            {
                "strategy_id": "strat_xyz",
                "config": {"initial_capital": 25_000},
                "wait": False,
            },
            ctx,
        )

    assert mock_post.call_args.kwargs["json"]["backtest_config"] == {"init_cash": 25_000.0}


def test_backtest_run_rejects_conflicting_capital_aliases(ctx):
    with patch("keel.client.KeelClient.post") as mock_post:
        with pytest.raises(KeelError, match="initial_capital"):
            OUTCOMES["keel_backtest_run"].handler(
                {
                    "strategy_id": "strat_xyz",
                    "config": {"initial_capital": 25_000, "init_cash": 30_000},
                    "wait": False,
                },
                ctx,
            )

    mock_post.assert_not_called()


def test_backtest_run_config_schema_matches_canonical_authority():
    schema = OUTCOMES["keel_backtest_run"].input_schema["properties"]["config"]
    canonical = BacktestConfig.model_json_schema()

    assert schema["additionalProperties"] is False
    for field in canonical["properties"]:
        assert schema["properties"][field] == canonical["properties"][field]


def test_sdk_backtest_config_vendor_copy_matches_canonical_source():
    sdk_root = Path(__file__).resolve().parents[1]
    repo_root = sdk_root.parents[2]

    assert (sdk_root / "pipeline_engine" / "backtest_config.py").read_bytes() == (
        repo_root / "libs" / "pipeline_engine" / "backtest_config.py"
    ).read_bytes()


def test_backtest_run_omits_start_date_so_the_platform_floors_it(ctx):
    """An omitted start_date is sent as OMITTED, never as a constant (Q-1701).

    keel-api resolves the window from the universe's own coverage and the
    clock's era (`max(universe_floor, series_era(timeframe))`); a date
    invented here would arrive as an EXPLICIT start and defeat both, naming
    a 2024 start for a 5-minute run the loader can only serve from the 1m
    grid era. The agent still calls with just `strategy_id` — that is what
    v0.5.3's required[] broke and this keeps.

    # SEED: restore `start_date = args.get("start_date") or "2024-08-15"` in
    # backtest_run._handler and this goes red on the first assertion.
    # Revert by reversing that edit.
    """
    submitted = {"id": "bt_def", "status": "queued", "strategy_id": "s_def"}

    with patch("keel.client.KeelClient.post", return_value=submitted) as mock_post:
        tool = OUTCOMES["keel_backtest_run"]
        tool.handler({"strategy_id": "s_def", "wait": False}, ctx)

    body = mock_post.call_args.kwargs["json"]
    assert "start_date" not in body
    # Non-vacuity: the payload was really built (the seed cannot remove these).
    assert body["strategy_id"] == "s_def"
    assert body["end_date"]

    # An explicit start still wins, unchanged.
    with patch("keel.client.KeelClient.post", return_value=submitted) as mock_post:
        tool.handler({"strategy_id": "s_def", "start_date": "2025-01-01", "wait": False}, ctx)
    assert mock_post.call_args.kwargs["json"]["start_date"] == "2025-01-01"


def test_backtest_run_schema_does_not_require_start_date():
    """The MCP-published schema must reflect the optional default — if
    start_date stays in required[], hosts that pre-validate against the
    schema will reject calls that omit it, defeating the default."""
    from keel.tools.outcomes import OUTCOMES

    schema = OUTCOMES["keel_backtest_run"].input_schema
    assert "start_date" not in schema["required"], (
        "start_date must not be required; it defaults to 2024-08-15. "
        "If required[] reverts, agents will ask the user for dates "
        "instead of running."
    )
    assert "strategy_id" in schema["required"]


def test_backtest_run_defaults_end_date_to_today(ctx):
    """Omitting end_date defaults to today's UTC date before POSTing."""
    submitted = {
        "id": "bt_today",
        "status": "queued",
        "strategy_id": "strat_xyz",
    }

    with (
        patch(
            "keel.tools.outcomes.backtest_run._default_end_date",
            return_value="2026-05-25",
        ),
        patch("keel.client.KeelClient.post", return_value=submitted) as mock_post,
    ):
        tool = OUTCOMES["keel_backtest_run"]
        result = tool.handler(
            {
                "strategy_id": "strat_xyz",
                "start_date": "2025-01-01",
                "wait": False,
            },
            ctx,
        )

    body = mock_post.call_args.kwargs["json"]
    assert body["end_date"] == "2026-05-25"
    assert result.to_envelope()["run_id"] == "bt_today"


def test_backtest_run_polls_when_wait_true(ctx):
    """wait=true polls until terminal, returns summary_metrics + tearsheet."""
    submitted = {"id": "bt_done", "status": "queued", "strategy_id": "s1"}
    running = {"id": "bt_done", "status": "RUNNING"}
    completed = {
        "id": "bt_done",
        "status": "COMPLETED",
        "completed_at": "2026-05-18T00:00:00Z",
        "execution_time": 12.5,
        "metrics": {
            "sharpe": 2.3,
            "total_return_pct": 145.2,
            "max_drawdown_pct": -18.4,
            "win_rate_pct": 56.0,
            "unrecognized_key": "drop_to_extra",
        },
    }

    # Path-based rather than a call-ordered list: since the render
    # cadence build a completed run also reads its curve and the
    # strategy's recent runs (both best-effort), and an ordered
    # side_effect would make every future read a test edit.
    polls = {"n": 0}

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_done":
            polls["n"] += 1
            return running if polls["n"] == 1 else completed
        if path == "/v1/backtests/bt_done/curve":
            return {}
        if path == "/v1/backtests":
            return {"data": [], "pagination": {}}
        if path.startswith("/v1/strategy-work"):
            return {}
        raise AssertionError(f"unexpected GET {path}")

    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch("keel.client.KeelClient.get", side_effect=fake_get),
    ):
        tool = OUTCOMES["keel_backtest_run"]
        result = tool.handler(
            {
                "strategy_id": "s1",
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "wait": True,
                "skip_readiness": True,
            },
            ctx,
        )

    env = result.to_envelope()
    assert env["run_id"] == "bt_done"
    assert env["status"] == "completed"
    assert env["hero_url"].endswith("?tab=tearsheet")
    assert env["tearsheet_url"] == env["hero_url"]
    assert env["summary_metrics"]["sharpe"] == 2.3
    assert env["summary_metrics"]["total_return_pct"] == 145.2
    assert env["summary_metrics"]["max_drawdown_pct"] == -18.4
    assert "unrecognized_key" not in env["summary_metrics"]
    assert env["execution_time_s"] == 12.5


# ─── divergence guard ────────────────────────────────────────────────────


def test_backtest_run_skips_divergence_check_when_no_workspace(ctx):
    """Strategy not checked out → no warning, proceeds silently."""
    submitted = {"id": "bt_skip", "status": "queued", "strategy_id": "s_no_ws"}

    with (
        patch("keel.workspace.get_workspace", return_value=None) as gw,
        patch("keel.client.KeelClient.post", return_value=submitted),
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "s_no_ws",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                },
                ctx,
            )
            .to_envelope()
        )

    gw.assert_called_once_with("s_no_ws")
    assert env["status"] == "queued"
    # No divergence warning leaked into info
    assert "ahead" not in env.get("info", "").lower()


def test_backtest_run_clean_workspace_proceeds(ctx):
    """Checked out + local hash == meta hash → no warning."""
    from keel.workspace import WorkspaceMeta

    submitted = {"id": "bt_clean", "status": "queued", "strategy_id": "s_clean"}
    meta = WorkspaceMeta(
        strategy_id="s_clean",
        name="C",
        source_hash="hash_x" * 10,
        checked_out_at="2026-05-21T00:00:00Z",
        current_sequence=1,
    )

    with (
        patch("keel.workspace.get_workspace", return_value=meta),
        patch("keel.workspace.read_local_source", return_value="src"),
        patch("keel.workspace._compute_hash", return_value=meta.source_hash),
        patch("keel.client.KeelClient.post", return_value=submitted),
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "s_clean",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                },
                ctx,
            )
            .to_envelope()
        )

    assert env["status"] == "queued"
    assert "ahead" not in env.get("info", "").lower()


def test_backtest_run_local_ahead_opt_out_raises(ctx):
    """auto_push=False (the explicit opt-out) → raise `local_ahead`.

    Spec 08 R2: write-through is the default; the old raise-and-ask
    behavior is now behind the explicit opt-out flag.
    """
    from keel.workspace import WorkspaceMeta

    meta = WorkspaceMeta(
        strategy_id="s_ahead",
        name="A",
        source_hash="OLD_HASH",
        checked_out_at="2026-05-21T00:00:00Z",
        current_sequence=1,
    )

    with (
        patch("keel.workspace.get_workspace", return_value=meta),
        patch("keel.workspace.read_local_source", return_value="edited_src"),
        patch("keel.workspace._compute_hash", return_value="NEW_HASH"),
        patch("keel.workspace.push") as push_mock,
        patch("keel.client.KeelClient.post") as mock_post,
    ):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_backtest_run"].handler(
                {
                    "strategy_id": "s_ahead",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                    "auto_push": False,
                },
                ctx,
            )

    # Did NOT push and did NOT call the backtest endpoint
    push_mock.assert_not_called()
    mock_post.assert_not_called()
    assert exc.value.error_code == "local_ahead"
    sug = exc.value.suggestion or ""
    assert "keel_strategy_push" in sug
    assert "commit_id" in sug


def test_backtest_run_local_ahead_default_pushes_and_pins(ctx):
    """No auto_push flag at all → write-through default: push, pin, run.

    Spec 08 R2 acceptance: local checkout edited → `keel_backtest_run`
    (no flags) pushes, pins, runs; the divergence note names the new
    commit.
    """
    from keel.workspace import WorkspaceMeta

    meta = WorkspaceMeta(
        strategy_id="s_wt",
        name="WT",
        source_hash="OLD_HASH",
        checked_out_at="2026-05-21T00:00:00Z",
        current_sequence=3,
    )
    pushed = {
        "strategy_id": "s_wt",
        "status": "pushed",
        "source_hash": "NEW_HASH",
        "sequence": 4,
        "commit_id": "cmt_wt_new",
    }
    submitted = {"id": "bt_wt", "status": "queued", "strategy_id": "s_wt"}

    with (
        patch("keel.workspace.get_workspace", return_value=meta),
        patch("keel.workspace.read_local_source", return_value="edited_src"),
        patch("keel.workspace._compute_hash", return_value="NEW_HASH"),
        patch("keel.workspace.push", return_value=pushed) as push_mock,
        patch("keel.client.KeelClient.post", return_value=submitted) as bt_mock,
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "s_wt",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                    # NO auto_push key — the default must write through.
                },
                ctx,
            )
            .to_envelope()
        )

    push_mock.assert_called_once()
    # Generated commit message used
    assert push_mock.call_args.kwargs["message"] == "Auto-push before backtest"
    # Backtest pinned to the freshly pushed commit
    assert bt_mock.call_args.kwargs["json"]["commit_id"] == "cmt_wt_new"
    assert env["auto_pushed_commit_id"] == "cmt_wt_new"
    # The divergence note names the new commit
    assert "cmt_wt_new" in env["info"]
    assert "auto-pushed" in env["info"].lower()


def test_backtest_run_conflict_stops_never_forces(ctx):
    """Local ahead AND server moved → push 409s → stop with sync_conflict.

    Spec 08 R2/R4: write-through never force-overwrites. The conflict
    stops the backtest with three-way context + recovery options.
    """
    from keel.errors import ConflictError
    from keel.workspace import WorkspaceMeta

    meta = WorkspaceMeta(
        strategy_id="s_cfl",
        name="CFL",
        source_hash="BASE_HASH",
        checked_out_at="2026-05-21T00:00:00Z",
        current_sequence=2,
    )

    with (
        patch("keel.workspace.get_workspace", return_value=meta),
        patch("keel.workspace.read_local_source", return_value="edited_src"),
        patch("keel.workspace._compute_hash", return_value="LOCAL_HASH"),
        patch(
            "keel.workspace.push",
            side_effect=ConflictError("Source hash mismatch"),
        ) as push_mock,
        patch("keel.client.KeelClient.post") as bt_mock,
    ):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_backtest_run"].handler(
                {
                    "strategy_id": "s_cfl",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                },
                ctx,
            )

    # Pushed exactly once (the optimistic-concurrency attempt), never
    # retried with force, and the backtest endpoint was never reached.
    push_mock.assert_called_once()
    assert push_mock.call_args.kwargs.get("force") is not True
    bt_mock.assert_not_called()
    assert exc.value.error_code == "sync_conflict"
    payload = exc.value.input or {}
    assert payload["base_hash"] == "BASE_HASH"
    assert payload["local_hash"] == "LOCAL_HASH"
    option_names = {o["option"] for o in payload["options"]}
    assert option_names == {"pull_force", "manual_merge", "pin_commit"}


def test_backtest_run_local_ahead_auto_push_pushes_first(ctx):
    """auto_push=True → call workspace.push, then backtest the new commit."""
    from keel.workspace import WorkspaceMeta

    meta = WorkspaceMeta(
        strategy_id="s_ahead",
        name="A",
        source_hash="OLD_HASH",
        checked_out_at="2026-05-21T00:00:00Z",
        current_sequence=1,
    )
    pushed = {
        "strategy_id": "s_ahead",
        "status": "pushed",
        "source_hash": "NEW_HASH",
        "sequence": 2,
        "commit_id": "cmt_new",
    }
    submitted = {"id": "bt_auto", "status": "queued", "strategy_id": "s_ahead"}

    with (
        patch("keel.workspace.get_workspace", return_value=meta),
        patch("keel.workspace.read_local_source", return_value="edited_src"),
        patch("keel.workspace._compute_hash", return_value="NEW_HASH"),
        patch("keel.workspace.push", return_value=pushed) as push_mock,
        patch("keel.client.KeelClient.post", return_value=submitted) as bt_mock,
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "s_ahead",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                    "auto_push": True,
                },
                ctx,
            )
            .to_envelope()
        )

    push_mock.assert_called_once()
    # Backtest body included the new commit_id
    assert bt_mock.call_args.kwargs["json"]["commit_id"] == "cmt_new"
    # Warning surfaced in info + auto_pushed_commit_id recorded
    assert env["auto_pushed_commit_id"] == "cmt_new"
    assert "auto-pushed" in env["info"].lower()


def test_backtest_run_explicit_commit_id_skips_divergence_check(ctx):
    """When user pins commit_id, skip workspace check entirely — they
    explicitly want a historical backtest."""
    submitted = {"id": "bt_pin", "status": "queued", "strategy_id": "s_pin"}

    with (
        patch("keel.workspace.get_workspace") as gw,
        patch("keel.client.KeelClient.post", return_value=submitted),
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "s_pin",
                    "commit_id": "cmt_old",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                },
                ctx,
            )
            .to_envelope()
        )

    gw.assert_not_called()
    assert env["status"] == "queued"


def test_backtest_run_workspace_lib_failure_does_not_block(ctx):
    """If workspace lib raises a non-KeelError (corrupt meta, missing file,
    etc.), proceed with backtest rather than blocking — the divergence
    check is advisory."""
    submitted = {"id": "bt_recover", "status": "queued", "strategy_id": "s_x"}

    with (
        patch("keel.workspace.get_workspace", side_effect=OSError("permission denied")),
        patch("keel.client.KeelClient.post", return_value=submitted) as mock_post,
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "s_x",
                    "start_date": "2025-01-01",
                    "end_date": "2025-06-30",
                    "wait": False,
                },
                ctx,
            )
            .to_envelope()
        )

    mock_post.assert_called_once()
    assert env["status"] == "queued"


def test_backtest_run_returns_status_url_on_timeout(ctx):
    """Perpetual RUNNING → envelope with status_url, no exception."""
    submitted = {"id": "bt_slow", "status": "queued", "strategy_id": "s2"}
    running = {"id": "bt_slow", "status": "RUNNING"}

    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch("keel.client.KeelClient.get", return_value=running) as mock_get,
    ):
        tool = OUTCOMES["keel_backtest_run"]
        result = tool.handler(
            {
                "strategy_id": "s2",
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "wait": True,
            },
            ctx,
        )

    # We polled at least once.
    assert mock_get.called
    env = result.to_envelope()
    assert env["run_id"] == "bt_slow"
    assert env["status"] == "running"
    assert env["status_url"] == "https://app.usekeel.io/backtests/bt_slow?tab=tearsheet"
    assert env["share_url"] is None
    # No summary_metrics yet — still running.
    assert "summary_metrics" not in env or env.get("summary_metrics") is None
    assert "info" in env and "still running" in env["info"].lower()


# ─── split polling budgets (Q-0573) ──────────────────────────────────────


def test_poll_budget_selected_by_surface():
    """Interactive terminals get the long budget, everything else the MCP
    budget — and the interactive budget covers the ~141s healthy runs
    observed in prod (register: cli-backtest-wait-budget-too-short).
    Non-vacuous: the two budgets must actually differ."""
    from keel.tools.outcomes import backtest_run as m

    tty_budget = m._poll_budget_s(ToolContext(is_tty=True))
    mcp_budget = m._poll_budget_s(ToolContext(is_tty=False))
    assert tty_budget == m._POLL_MAX_INTERACTIVE_S
    assert mcp_budget == m._POLL_MAX_S
    assert tty_budget != mcp_budget
    # _POLL_MAX_INTERACTIVE_S is NOT touched by the _fast_poll fixture, so
    # this reads the real shipped value.
    assert m._POLL_MAX_INTERACTIVE_S > 141, "must cover observed ~141s healthy runs"


def test_backtest_run_tty_outlives_mcp_budget(monkeypatch, capsys):
    """The old failure mode, gone: a healthy run that outlasts the MCP
    budget used to time out for terminal users too. With split budgets a
    tty session keeps polling (with a stderr progress line) and returns
    the completed result."""
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_MAX_S", 0.05)
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_MAX_INTERACTIVE_S", 5.0)
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_INTERVAL_S", 0.01)

    import time as _time

    start = _time.monotonic()
    submitted = {"id": "bt_141", "status": "queued", "strategy_id": "s_tty"}

    def slow_get(path, **_kw):
        # The run "takes" 0.3s of wall clock — past the (scaled) MCP
        # budget, comfortably inside the tty budget.
        if _time.monotonic() - start < 0.3:
            return {"id": "bt_141", "status": "RUNNING"}
        return {"id": "bt_141", "status": "COMPLETED", "metrics": {"sharpe": 1.0}}

    tty_ctx = ToolContext(is_tty=True, app_url="https://app.usekeel.io")
    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch("keel.client.KeelClient.get", side_effect=slow_get),
    ):
        result = OUTCOMES["keel_backtest_run"].handler(
            {
                "strategy_id": "s_tty",
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "wait": True,
            },
            tty_ctx,
        )

    env = result.to_envelope()
    assert env["status"] == "completed", "tty budget must outlive the MCP budget"
    assert env["summary_metrics"]["sharpe"] == 1.0
    # Liveness: the interactive wait showed a progress line on stderr.
    assert "Ctrl-C to stop waiting" in capsys.readouterr().err


def test_backtest_run_timeout_reports_budget_actually_used(monkeypatch):
    """The timeout message names the budget of the surface that hit it,
    not a hardcoded 90s."""
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_MAX_S", 7.0)
    monkeypatch.setattr(
        "keel.tools.outcomes.backtest_run.time.monotonic",
        _FakeMonotonic(step=4.0),
    )
    submitted = {"id": "bt_to", "status": "queued", "strategy_id": "s_to"}
    running = {"id": "bt_to", "status": "RUNNING"}

    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch("keel.client.KeelClient.get", return_value=running),
    ):
        result = OUTCOMES["keel_backtest_run"].handler(
            {
                "strategy_id": "s_to",
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "wait": True,
            },
            ToolContext(is_tty=False, app_url="https://app.usekeel.io"),
        )

    assert "after 7s" in result.to_envelope()["info"]


class _FakeMonotonic:
    """Deterministic monotonic clock advancing `step` seconds per call."""

    def __init__(self, step: float):
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        now = self._now
        self._now += self._step
        return now


# ─── keel_backtest_summarize ─────────────────────────────────────────────


def test_backtest_summarize_returns_metrics(ctx):
    """Summarize a completed backtest: metrics + period + presigned URL."""
    detail = {
        "id": "bt_sum",
        "status": "COMPLETED",
        "strategy_id": "strat_q",
        "strategy_name": "Momentum XS",
        "commit_id": "c_001",
        "sequence_number": 3,
        "engine": "native",
        "start_date": "2024-08-15",
        "end_date": "2026-02-27",
        "queued_at": "2026-05-18T00:00:00Z",
        "started_at": "2026-05-18T00:00:05Z",
        "completed_at": "2026-05-18T00:01:30Z",
        "execution_time": 85.0,
        "metrics": {
            "sharpe": 3.13,
            "total_return_pct": 717.5,
            "max_drawdown_pct": -22.1,
        },
    }
    results = {
        "job_id": "bt_sum",
        "presigned_url": "https://s3.example/results.json?sig=abc",
        "expires_in": 3600,
    }
    curve = {
        "job_id": "bt_sum",
        "points": [
            {"t": "2024-08-15T00:00:00+00:00", "equity": 10000.0, "drawdown_pct": 0.0},
            {"t": "2025-06-01T00:00:00+00:00", "equity": 9100.0, "drawdown_pct": -9.0},
            {"t": "2026-02-27T00:00:00+00:00", "equity": 81750.0, "drawdown_pct": 0.0},
        ],
        "start": "2024-08-15T00:00:00+00:00",
        "end": "2026-02-27T00:00:00+00:00",
        "source_points": 54000,
    }

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_sum":
            return detail
        if path == "/v1/backtests/bt_sum/results":
            return results
        if path == "/v1/backtests/bt_sum/curve":
            return curve
        raise AssertionError(f"unexpected GET {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        tool = OUTCOMES["keel_backtest_summarize"]
        result = tool.handler({"backtest_id": "bt_sum"}, ctx)

    env = result.to_envelope()
    assert env["run_id"] == "bt_sum"
    assert env["hero_url"] == "https://app.usekeel.io/backtests/bt_sum?tab=tearsheet"
    assert env["share_url"] is None
    assert env["summary_metrics"]["sharpe"] == 3.13
    assert env["summary_metrics"]["total_return_pct"] == 717.5
    assert env["status"] == "completed"
    assert env["period"]["start_date"] == "2024-08-15"
    assert env["period"]["end_date"] == "2026-02-27"
    assert env["strategy_id"] == "strat_q"
    assert env["strategy_name"] == "Momentum XS"
    assert env["results_url"] == "https://s3.example/results.json?sig=abc"
    assert env["resource_uri"] == "keel://backtest/bt_sum/results"
    # The card chart's series rides the envelope as compact triples (Q-1505).
    assert env["curve"]["points"] == [
        ["2024-08-15T00:00:00+00:00", 10000.0, 0.0],
        ["2025-06-01T00:00:00+00:00", 9100.0, -9.0],
        ["2026-02-27T00:00:00+00:00", 81750.0, 0.0],
    ]
    assert env["curve"]["start"] == "2024-08-15T00:00:00+00:00"
    assert env["curve"]["source_points"] == 54000
    # No embed route since Q-1505: the card draws from `curve`.
    assert "embed_url" not in env["render"]


def test_backtest_summarize_curve_is_best_effort(ctx):
    """A curve fetch failure (or an empty series) never fails the
    summary — the envelope simply carries no `curve`."""
    detail = {"id": "bt_nc", "status": "COMPLETED", "strategy_id": "s", "metrics": {"sharpe": 1.0}}

    def fake_get_error(path, **_kw):
        if path == "/v1/backtests/bt_nc":
            return detail
        if path == "/v1/backtests/bt_nc/results":
            return {}
        if path == "/v1/backtests/bt_nc/curve":
            raise KeelError("curve down")
        raise AssertionError(f"unexpected GET {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get_error):
        env = (
            OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "bt_nc"}, ctx).to_envelope()
        )
    assert "curve" not in env
    assert env["summary_metrics"]["sharpe"] == 1.0

    def fake_get_empty(path, **_kw):
        if path == "/v1/backtests/bt_nc":
            return detail
        if path == "/v1/backtests/bt_nc/results":
            return {}
        if path == "/v1/backtests/bt_nc/curve":
            return {"job_id": "bt_nc", "points": [], "start": None, "end": None, "source_points": 0}
        raise AssertionError(f"unexpected GET {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get_empty):
        env = (
            OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "bt_nc"}, ctx).to_envelope()
        )
    assert "curve" not in env


def test_backtest_summarize_not_completed_never_fetches_curve(ctx):
    detail = {"id": "bt_run", "status": "RUNNING", "strategy_id": "s", "metrics": None}

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_run":
            return detail
        raise AssertionError(f"unexpected GET {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        env = (
            OUTCOMES["keel_backtest_summarize"]
            .handler({"backtest_id": "bt_run"}, ctx)
            .to_envelope()
        )
    assert "curve" not in env and "results_url" not in env


#: The Q-0415 drift guard's stored blocks, one per measurement era
#: (trade-metrics spec 01 §4): Era C is today's `_STATS_KEYS`; Era B the
#: 2026-08-25 → spec-01 shape, whose churn rode `rebalance_legs`.
_ERA_C_TRADE_KEYS = {
    "total_trades": 1198,
    "positions": 87,
    "position_win_rate": 40.2,
    "resizes": 233,
    "avg_holding_duration": "6 days 12:00:00",
    "turnover": 12.4,
    "trade_model": "reducing_order",
}
_ERA_B_TRADE_KEYS = {
    "total_trades": 87,
    "rebalance_legs": 233,
    "turnover": 12.4,
    "trade_model": "position_round_trip",
}


@pytest.mark.parametrize("era", ["C", "B"])
def test_backtest_summarize_carries_every_stored_metric_key(ctx, era):
    """Drift guard (Q-0398 residue): the envelope must carry EVERY key the
    worker stores — canonical keys in summary_metrics, everything verbatim
    in metrics_raw. Pre-fix, the hand whitelist dropped 16 of 21 keys,
    including the fee ratios shipped the same day they became invisible."""
    # Prod-shaped worker dict: every db key in strategy_executor._STATS_KEYS
    # plus the LL-15 warnings entry.
    worker_metrics = {
        "sharpe_ratio": 1.24,
        "sharpe_ratio_active": 1.31,
        "warmup_bars": 200,
        "sortino_ratio": 1.9,
        "calmar_ratio": 0.8,
        "omega_ratio": 1.1,
        "total_return": 42.5,
        "max_drawdown": -18.3,
        "win_rate": 51.2,
        "profit_factor": 1.4,
        "expectancy": 0.02,
        # The era's trade keys (spec 01 §3): C records trades AND positions,
        # B stored positions under the trade keys.
        **(_ERA_C_TRADE_KEYS if era == "C" else _ERA_B_TRADE_KEYS),
        "total_orders": 120,
        "total_fees_paid": 55.1,
        "fees_pct_of_initial": 13.3911,
        "fees_pct_of_net_profit": 107.10,
        # Q-0581: fee drag vs gross PnL — derived by the worker's
        # build_enriched_metrics, present on every enriched payload.
        "fees_pct_of_gross_profit": 51.71,
        "max_drawdown_duration": "12 days 04:00:00",
        "end_value": 14250.0,
        "wipeout_bar": None,
        "wipeout_date": None,
        # Q-0489: explicit funding-inclusion fact — never inferred from the
        # funding key's absence.
        "funding_included": False,
        "warnings": [
            {
                "code": "SYMBOL_DELISTED",
                "message": "IP delisted 2026-06-29 — position exited at last listed bar",
            }
        ],
    }
    detail = {
        "id": "bt_drift",
        "status": "COMPLETED",
        "strategy_id": "strat_q",
        "metrics": worker_metrics,
    }

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_drift":
            return detail
        if path == "/v1/backtests/bt_drift/results":
            return {}
        if path == "/v1/backtests/bt_drift/curve":
            return {}
        raise AssertionError(f"unexpected GET {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        tool = OUTCOMES["keel_backtest_summarize"]
        result = tool.handler({"backtest_id": "bt_drift"}, ctx)

    env = result.to_envelope()
    raw = env["metrics_raw"]
    # Verbatim passthrough: 22/22 keys survive, values untouched.
    assert raw == worker_metrics
    assert raw["fees_pct_of_initial"] == 13.3911
    assert raw["fees_pct_of_net_profit"] == 107.10
    assert raw["fees_pct_of_gross_profit"] == 51.71
    # Q-0581: fee drag is FIRST-CLASS — canonical summary, not only raw.
    summary = env["summary_metrics"]
    assert summary["total_fees_paid"] == 55.1
    assert summary["fees_pct_of_initial"] == 13.3911
    assert summary["fees_pct_of_gross_profit"] == 51.71
    assert summary["fees_pct_of_net_profit"] == 107.10
    assert raw["warnings"][0]["code"] == "SYMBOL_DELISTED"
    assert raw["funding_included"] is False
    assert raw["turnover"] == 12.4 and summary["turnover"] == 12.4
    # The stamp rides metrics_raw verbatim and is never a summary key.
    assert raw["trade_model"] == worker_metrics["trade_model"]
    assert "trade_model" not in summary
    if era == "C":
        assert summary["total_trades"] == 1198 and summary["win_rate"] == 51.2
        assert summary["positions"] == 87 and summary["position_win_rate"] == 40.2
        assert summary["resizes"] == 233
        assert summary["avg_holding_duration"] == "6 days 12:00:00"
    else:
        # Era B's stored count and win rate are POSITION numbers: summary
        # names them so, and `rebalance_legs` stays readable beside `resizes`.
        assert summary["positions"] == 87 and summary["position_win_rate"] == 51.2
        assert summary["position_profit_factor"] == 1.4
        assert summary["resizes"] == 233 and summary["rebalance_legs"] == 233
        assert not {"total_trades", "win_rate", "profit_factor"} & set(summary)
        assert raw["rebalance_legs"] == 233 and raw["total_trades"] == 87


def test_backtest_run_success_populates_metrics_raw(ctx):
    """The run tool's docstring promised extra.metrics_raw; it must exist."""
    submitted = {"id": "bt_raw", "status": "QUEUED"}
    final = {
        "id": "bt_raw",
        "status": "COMPLETED",
        "metrics": {"sharpe_ratio": 2.0, "fees_pct_of_initial": 1.5},
    }
    calls = {"n": 0}

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_raw":
            calls["n"] += 1
            return final
        if path == "/v1/backtests/bt_raw/curve":
            return {}
        if path == "/v1/backtests":
            return {"data": [], "pagination": {}}
        raise AssertionError(f"unexpected GET {path}")

    def fake_post(path, **_kw):
        assert path == "/v1/backtests"
        return submitted

    with (
        patch("keel.client.KeelClient.post", side_effect=fake_post),
        patch("keel.client.KeelClient.get", side_effect=fake_get),
    ):
        tool = OUTCOMES["keel_backtest_run"]
        result = tool.handler(
            {
                "strategy_id": "strat_q",
                "commit_id": "c_1",
                "wait": True,
                "skip_readiness": True,
            },
            ctx,
        )

    env = result.to_envelope()
    assert env["metrics_raw"] == final["metrics"]


def _window_422(code: str, message: str, **fields):
    import json

    from keel.errors import translate_http_error

    return translate_http_error(
        422, json.dumps({"detail": {"code": code, "message": message, **fields}})
    )


def test_backtest_run_inverted_window_suggests_the_swapped_call(ctx):
    """Q-1741: the server names the inversion; the envelope's next action is
    the same run with the dates swapped — not "choose a wider range"."""
    refusal = _window_422(
        "WINDOW_INVERTED",
        "Cannot backtest — start_date 2025-06-01 is after end_date 2025-01-01.",
        requested_start="2025-06-01",
        requested_end="2025-01-01",
    )
    with patch("keel.client.KeelClient.post", side_effect=refusal):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_backtest_run"].handler(
                {"strategy_id": "strat_xyz", "start_date": "2025-06-01", "end_date": "2025-01-01"},
                ctx,
            )
    env = exc.value.to_envelope()
    assert env["message"].endswith("start_date 2025-06-01 is after end_date 2025-01-01.")
    assert env["detail"]["code"] == "WINDOW_INVERTED"
    assert env["code"] == "WINDOW_INVERTED"  # Q-1751: survives to the envelope
    assert env["suggested_next_action"]["tool"] == "keel_backtest_run"
    assert env["suggested_next_action"]["args"] == {
        "strategy_id": "strat_xyz",
        "start_date": "2025-01-01",
        "end_date": "2025-06-01",
    }


def test_backtest_run_other_window_refusals_carry_no_invented_next_call(ctx):
    """Control arm: a genuinely empty window has no mechanical fix, so it
    re-raises with no tool named."""
    refusal = _window_422("WINDOW_EMPTY", "Cannot backtest — the window is empty.")
    with patch("keel.client.KeelClient.post", side_effect=refusal):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_backtest_run"].handler(
                {"strategy_id": "strat_xyz", "start_date": "2026-09-01", "end_date": "2026-09-01"},
                ctx,
            )
    env = exc.value.to_envelope()
    assert env["suggested_next_action"]["tool"] is None
    assert env["detail"]["code"] == "WINDOW_EMPTY"


def test_backtest_summarize_404_raises_NotFoundError(ctx):
    """Missing backtest_id surfaces NotFoundError (exit_code=3)."""
    with patch(
        "keel.client.KeelClient.get",
        side_effect=NotFoundError("backtest bt_nope not found"),
    ):
        tool = OUTCOMES["keel_backtest_summarize"]
        with pytest.raises(NotFoundError):
            tool.handler({"backtest_id": "bt_nope"}, ctx)


# ─── keel_backtest_watch ─────────────────────────────────────────────────


def test_backtest_watch_polls_until_complete(ctx):
    running = {"id": "bt_watch", "status": "RUNNING", "strategy_id": "strat_q"}
    completed = {
        "id": "bt_watch",
        "status": "COMPLETED",
        "strategy_id": "strat_q",
        "strategy_name": "Momentum XS",
        "completed_at": "2026-05-18T00:01:30Z",
        "execution_time": 85.0,
        "metrics": {"sharpe": 2.1, "max_drawdown_pct": -9.7},
    }
    results = {"presigned_url": "https://s3.example/results.json?sig=abc"}

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_watch":
            calls = fake_get.calls
            fake_get.calls += 1
            return running if calls == 0 else completed
        if path == "/v1/backtests/bt_watch/results":
            return results
        if path == "/v1/backtests/bt_watch/curve":
            return {}
        if path == "/v1/backtests":
            return {"data": [], "pagination": {}}
        raise AssertionError(f"unexpected GET {path}")

    fake_get.calls = 0

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        env = (
            OUTCOMES["keel_backtest_watch"]
            .handler(
                {
                    "backtest_id": "bt_watch",
                    "interval_s": 1,
                    "timeout_s": 5,
                    "skip_readiness": True,
                },
                ctx,
            )
            .to_envelope()
        )

    assert env["run_id"] == "bt_watch"
    assert env["status"] == "completed"
    assert env["terminal"] is True
    assert env["timed_out"] is False
    assert env["polls"] == 2
    assert env["summary_metrics"]["sharpe"] == 2.1
    assert env["results_url"] == "https://s3.example/results.json?sig=abc"
    assert env["hero_url"].endswith("/backtests/bt_watch?tab=tearsheet")


def test_backtest_watch_returns_running_snapshot_on_timeout(ctx):
    running = {"id": "bt_slow", "status": "RUNNING", "strategy_id": "strat_q"}

    with patch("keel.client.KeelClient.get", return_value=running):
        env = (
            OUTCOMES["keel_backtest_watch"]
            .handler(
                {"backtest_id": "bt_slow", "timeout_s": 0},
                ctx,
            )
            .to_envelope()
        )

    assert env["run_id"] == "bt_slow"
    assert env["status"] == "running"
    assert env["terminal"] is False
    assert env["timed_out"] is True
    assert env["next_action"]["tool"] == "keel_backtest_watch"
    assert env["status_url"] == env["hero_url"]


def test_backtest_watch_404_raises_not_found(ctx):
    with patch(
        "keel.client.KeelClient.get",
        side_effect=NotFoundError("backtest bt_nope not found"),
    ):
        with pytest.raises(NotFoundError):
            OUTCOMES["keel_backtest_watch"].handler({"backtest_id": "bt_nope"}, ctx)


# ─── quota visibility pass-through (spec 04 R5) ──────────────────────────


def test_backtest_run_passes_through_remaining_when_present(ctx):
    """A sub-20% `remaining` block from the API surfaces in the envelope."""
    submitted = {
        "id": "bt_low",
        "status": "queued",
        "strategy_id": "strat_xyz",
        "remaining": {"backtest_runs": 4, "compute_seconds": 200},
    }
    with patch("keel.client.KeelClient.post", return_value=submitted):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True},
                ctx,
            )
            .to_envelope()
        )
    assert env["remaining"] == {"backtest_runs": 4, "compute_seconds": 200}


def test_backtest_run_omits_remaining_when_absent(ctx):
    """No `remaining` from the API (>=20% quota left) → no envelope field."""
    submitted = {"id": "bt_ok", "status": "queued", "strategy_id": "strat_xyz"}
    with patch("keel.client.KeelClient.post", return_value=submitted):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True},
                ctx,
            )
            .to_envelope()
        )
    assert "remaining" not in env


def test_backtest_run_keeps_remaining_through_wait_path(ctx):
    """`remaining` from the SUBMIT response survives the polled final envelope."""
    submitted = {
        "id": "bt_low2",
        "status": "queued",
        "strategy_id": "strat_xyz",
        "remaining": {"backtest_runs": 2},
    }
    final = {
        "id": "bt_low2",
        "status": "completed",
        "metrics": {"sharpe": 1.2},
    }
    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch("keel.client.KeelClient.get", return_value=final),
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {"strategy_id": "strat_xyz", "wait": True, "skip_readiness": True},
                ctx,
            )
            .to_envelope()
        )
    assert env["status"] == "completed"
    assert env["remaining"] == {"backtest_runs": 2}


# ─── the quota ladder + rendered sentence (mcp-conversion M1.1/M1.2) ─────


def _quota_block(**over) -> dict:
    """One served block, exactly as `QuotaView.to_dict() | {"tier": …}`."""
    block = {
        "unit": "backtest_runs",
        "label": "backtests",
        "limit": 50,
        "used": 44,
        "remaining": 6,
        "unlimited": False,
        "period": "weekly",
        "resets_at": "2026-09-22T00:00:00Z",
        "seconds_to_reset": 24300,
        "tier": "warn",
    }
    block.update(over)
    return block


def test_backtest_run_renders_one_sentence_from_the_served_quota_block(ctx):
    """M1.2 / D-10: the API carries numbers, the SDK renders the line.

    The block is passed through PROJECTED to the caller's own numbers
    (D-12, 04 §4.4 — `{unit, limit, used, remaining, resets_at}`) AND one
    factual sentence is rendered — both halves of the fraction, the window,
    the reset.
    """
    submitted = {
        "id": "bt_q",
        "status": "queued",
        "strategy_id": "strat_xyz",
        "quota": [_quota_block()],
    }
    with patch("keel.client.KeelClient.post", return_value=submitted):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True}, ctx)
            .to_envelope()
        )
    assert env["quota"] == [
        {
            "unit": "backtest_runs",
            "limit": 50,
            "used": 44,
            "remaining": 6,
            "resets_at": "2026-09-22T00:00:00Z",
        }
    ], "the served block rides through projected to its allow-list"
    assert env["quota_notice"] == (
        "6 of 50 backtests left this week; they reset Tue 22 Sep 00:00 UTC."
    )


def test_backtest_run_headline_is_the_most_urgent_unit(ctx):
    """Two notable units → the sentence names the binding one, and BOTH
    still ride in `quota` so nothing is dropped."""
    runs = _quota_block(tier="notice", used=25, remaining=25)
    compute = _quota_block(
        unit="backtest_compute_seconds",
        label="backtest compute time",
        limit=1500,
        used=1450,
        remaining=50,
        tier="critical",
    )
    submitted = {"id": "bt_q2", "status": "queued", "quota": [runs, compute]}
    with patch("keel.client.KeelClient.post", return_value=submitted):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True}, ctx)
            .to_envelope()
        )
    assert env["quota_notice"].startswith("50 of 1500 backtest compute time left this week")
    assert len(env["quota"]) == 2


def test_backtest_run_omits_quota_keys_when_nothing_is_notable(ctx):
    """Control arm: a comfortable org gets neither key — an empty `quota`
    would read as "you have none"."""
    submitted = {"id": "bt_ok", "status": "queued", "strategy_id": "strat_xyz"}
    with patch("keel.client.KeelClient.post", return_value=submitted):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True}, ctx)
            .to_envelope()
        )
    assert "quota" not in env and "quota_notice" not in env


def test_backtest_run_quota_survives_the_wait_path(ctx):
    """The block comes off the SUBMIT response, so it must survive polling."""
    submitted = {"id": "bt_q3", "status": "queued", "quota": [_quota_block(tier="critical")]}
    final = {"id": "bt_q3", "status": "completed", "metrics": {"sharpe": 1.2}}
    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch("keel.client.KeelClient.get", return_value=final),
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "strat_xyz", "wait": True, "skip_readiness": True}, ctx)
            .to_envelope()
        )
    assert env["status"] == "completed"
    assert env["quota_notice"].startswith("6 of 50 backtests left this week")


def test_backtest_run_critical_notice_is_the_same_line_for_every_plan(ctx):
    """D-12 §4.2: near the wall the server attaches `plan` and the plans
    with a higher limit (`higher_plans`, D-10) — the notice renders NEITHER.
    It is the one line, identical for the free plan and a paid one, and the
    projected `quota` carries no `plan`/`higher_plans`.

    SEEDS (run 2026-09-28, each reverted by reversing the edit): append
    "Plans are listed at <billing link>." to the notice (bypassing the D-12
    scan) — the equality reds while the most-urgent-unit test stays green;
    restore the passthrough (`"quota": notable`) — this and the
    one-sentence test red while the omit-when-nothing-notable control
    stays green."""
    paths = [
        {"plan": "starter", "limit": 500, "period": "weekly"},
        {"plan": "trader", "unlimited": True},
    ]
    notices = {}
    for plan in ("free", "starter"):
        submitted = {
            "id": "bt_q5",
            "status": "queued",
            "quota": [
                _quota_block(tier="critical", used=46, remaining=4, plan=plan, higher_plans=paths)
            ],
        }
        with patch("keel.client.KeelClient.post", return_value=submitted):
            env = (
                OUTCOMES["keel_backtest_run"]
                .handler({"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True}, ctx)
                .to_envelope()
            )
        notices[plan] = env["quota_notice"]
        assert set(env["quota"][0]) == {"unit", "limit", "used", "remaining", "resets_at"}
        assert "higher_plans" not in json.dumps(env) and "Starter" not in json.dumps(env)
    assert (
        notices["free"]
        == notices["starter"]
        == ("4 of 50 backtests left this week; they reset Tue 22 Sep 00:00 UTC.")
    )
    from keel.tools.outcomes._handoff import _FORBIDDEN_UPSELL_RE

    assert not _FORBIDDEN_UPSELL_RE.search(notices["free"]), "facts, not a pitch (D-12 guard)"


def test_backtest_run_quota_notice_never_invents_numbers(ctx):
    """A block with no finite allowance renders no sentence at all — the
    null-number line ("you have  left") is the Q-1590 shape one layer up —
    and, having no allowance to report, no `quota` block either (its
    projection would be a bare unit)."""
    submitted = {
        "id": "bt_q4",
        "status": "queued",
        "quota": [{"unit": "backtest_runs", "label": "backtests", "unlimited": True}],
    }
    with patch("keel.client.KeelClient.post", return_value=submitted):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True}, ctx)
            .to_envelope()
        )
    assert "quota" not in env
    assert "quota_notice" not in env


# ─── the first-week allowance (connect-onboarding spec 01 §1.8/§1.9) ─────

#: A served first-week block (spec 01 §1.8), plus a field no agent surface
#: was reviewed for — the projection must drop it.
_FIRST_WEEK = {
    "granted": 200,
    "remaining": 40,
    "ends_at": "2026-10-13T15:02:11Z",
    "grant_id": "grt_unreviewed",
}

_FIRST_WEEK_SENTENCE = "40 of 200 first-week backtests left; they end Tue 13 Oct 15:02 UTC."


def _submit(ctx, quota: list[dict]) -> dict:
    submitted = {"id": "bt_fw", "status": "queued", "strategy_id": "strat_xyz", "quota": quota}
    with patch("keel.client.KeelClient.post", return_value=submitted):
        return (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True}, ctx)
            .to_envelope()
        )


def test_first_week_rides_a_notice_that_renders_without_it(ctx):
    """Spec 01 §1.9: the sentence follows the quota line it rides, verbatim,
    and the block's `first_week` is projected to granted/remaining/ends_at."""
    runs = _quota_block(
        limit=250, used=210, remaining=40, tier="critical", first_week=dict(_FIRST_WEEK)
    )
    env = _submit(ctx, [runs])
    assert env["quota_notice"] == (
        "40 of 250 backtests left this week; they reset Tue 22 Sep 00:00 UTC. "
        + _FIRST_WEEK_SENTENCE
    )
    assert env["quota"][0]["first_week"] == {
        "granted": 200,
        "remaining": 40,
        "ends_at": "2026-10-13T15:02:11Z",
    }
    # Only in the quota line: never `next`, never a second key.
    blob = json.dumps(env)
    assert blob.count("first-week") == 1, blob
    assert "next" not in env
    assert "grt_unreviewed" not in blob


def test_first_week_never_makes_a_response_carry_a_quota_line(ctx):
    """The bonus is never what makes a response carry a quota line (§1.9).

    A block whose allowance renders no sentence (no finite limit/remaining)
    carries `first_week` — the notice stays absent, and the first-week
    sentence does not appear on its own.

    SEED (run 2026-10-06, reverted by reversing the edit): render the
    first-week sentence before the `if sentence:` gate — this test reds
    while the rides-a-notice test (control) stays green."""
    bare = {"unit": "backtest_runs", "label": "backtests", "first_week": dict(_FIRST_WEEK)}
    env = _submit(ctx, [bare])
    assert "quota_notice" not in env
    assert "first-week" not in json.dumps(env)


def test_first_week_below_the_runs_threshold_adds_nothing(ctx):
    """The runs unit is still in the `ok` tier, so keel-api sent only the
    compute block — which carries its own `first_week` (§1.8). The compute
    line renders as before, and no first-week sentence rides it: the
    sentence counts backtests, and the runs block is not in the response."""
    compute = _quota_block(
        unit="backtest_compute_seconds",
        label="backtest compute time",
        limit=7500,
        used=6900,
        remaining=600,
        tier="critical",
        first_week={"granted": 6000, "remaining": 600, "ends_at": "2026-10-13T15:02:11Z"},
    )
    env = _submit(ctx, [compute])
    assert env["quota_notice"] == (
        "600 of 7500 backtest compute time left this week; they reset Tue 22 Sep 00:00 UTC."
    )
    assert "first-week" not in json.dumps(env)


def test_first_week_absent_when_the_submit_carries_no_quota(ctx):
    """Every unit in the `ok` tier: keel-api attaches no `quota` at all, so
    a first-week account's submit carries no quota line and no sentence."""
    submitted = {"id": "bt_fw", "status": "queued", "strategy_id": "strat_xyz"}
    with patch("keel.client.KeelClient.post", return_value=submitted):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "strat_xyz", "wait": False, "skip_readiness": True}, ctx)
            .to_envelope()
        )
    assert "quota_notice" not in env and "quota" not in env
    assert "first-week" not in json.dumps(env)


def test_no_first_week_block_leaves_the_notice_unchanged(ctx):
    """Control arm: the same critical block without `first_week` renders the
    pre-existing line and the pre-existing projection, byte for byte."""
    env = _submit(ctx, [_quota_block(limit=250, used=210, remaining=40, tier="critical")])
    assert env["quota_notice"] == (
        "40 of 250 backtests left this week; they reset Tue 22 Sep 00:00 UTC."
    )
    assert set(env["quota"][0]) == {"unit", "limit", "used", "remaining", "resets_at"}


def test_headroom_blocks_carry_first_week_from_the_entitlements_read():
    """The compare-hint headroom arm reads `/v1/entitlements`, whose balances
    carry `first_week` (§1.8); the notice it renders rides the sentence."""
    from keel.tools.outcomes.backtest_run import _headroom_blocks, _quota_envelope_fields

    class _Client:
        def get(self, path):
            assert path == "/v1/entitlements"
            return {
                "balances": [
                    {
                        "unit": "backtest_runs",
                        "granted": 250,
                        "spent": 210,
                        "available": 40,
                        "period": "weekly",
                        "resets_at": "2026-09-22T00:00:00Z",
                        "first_week": dict(_FIRST_WEEK),
                    }
                ]
            }

    fields = _quota_envelope_fields(_headroom_blocks(_Client()))
    # A balance carries no label, so the unit name reads mechanically.
    assert fields["quota_notice"] == (
        "40 of 250 backtest runs left this week; they reset Tue 22 Sep 00:00 UTC. "
        + _FIRST_WEEK_SENTENCE
    )
    assert fields["quota"][0]["first_week"]["remaining"] == 40
