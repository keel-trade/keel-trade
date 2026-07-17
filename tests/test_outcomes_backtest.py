"""Tests for the backtest outcome tools.

Covers `keel_backtest_run` (submit + optional polling) and
`keel_backtest_summarize` (read-only summary).
"""

from __future__ import annotations

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


def test_backtest_run_defaults_start_date_when_omitted(ctx):
    """Omitting start_date defaults to 2024-08-15 (earliest cached HL data).

    Regression — v0.5.3 had start_date in required[] which forced agents
    to interrogate the user for dates before running anything. New
    behavior: agent calls keel_backtest_run with just strategy_id, gets
    a real backtest back, and reports the dates used in its reply.
    """
    submitted = {"id": "bt_def", "status": "queued", "strategy_id": "s_def"}

    with patch("keel.client.KeelClient.post", return_value=submitted) as mock_post:
        tool = OUTCOMES["keel_backtest_run"]
        tool.handler({"strategy_id": "s_def", "wait": False}, ctx)

    body = mock_post.call_args.kwargs["json"]
    assert body["start_date"] == "2024-08-15"
    # end_date also defaults — anything that looks like a date is fine
    assert body["end_date"]


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

    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch(
            "keel.client.KeelClient.get",
            side_effect=[running, completed],
        ),
    ):
        tool = OUTCOMES["keel_backtest_run"]
        result = tool.handler(
            {
                "strategy_id": "s1",
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "wait": True,
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

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_sum":
            return detail
        if path == "/v1/backtests/bt_sum/results":
            return results
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
        raise AssertionError(f"unexpected GET {path}")

    fake_get.calls = 0

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        env = (
            OUTCOMES["keel_backtest_watch"]
            .handler(
                {"backtest_id": "bt_watch", "interval_s": 1, "timeout_s": 5},
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
                {"strategy_id": "strat_xyz", "wait": False, "no_ownership_hint": True},
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
                {"strategy_id": "strat_xyz", "wait": False, "no_ownership_hint": True},
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
                {"strategy_id": "strat_xyz", "wait": True, "no_ownership_hint": True},
                ctx,
            )
            .to_envelope()
        )
    assert env["status"] == "completed"
    assert env["remaining"] == {"backtest_runs": 2}
