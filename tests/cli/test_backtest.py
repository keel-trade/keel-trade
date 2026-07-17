"""CLI contract tests for backtest commands."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from keel.cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_backtest_run_config_accepts_json_object(runner):
    submitted = {"id": "bt_cli", "status": "queued", "strategy_id": "strat_xyz"}

    with (
        patch("keel.workspace.get_workspace", return_value=None),
        patch("keel.client.KeelClient.post", return_value=submitted) as post,
    ):
        result = runner.invoke(
            cli,
            [
                "--format",
                "json",
                "backtest",
                "run",
                "strat_xyz",
                "--config",
                json.dumps({"init_cash": 25_000, "leverage": 7.5}),
                "--no-wait",
                "--no-ownership-hint",
            ],
        )

    assert result.exit_code == 0, result.output
    assert post.call_args.kwargs["json"]["backtest_config"] == {
        "init_cash": 25_000.0,
        "leverage": 7.5,
    }


@pytest.mark.parametrize(
    ("raw_config", "message"),
    [
        ('{"leverage":', "must be a valid JSON object"),
        ("[]", "must decode to a JSON object"),
        ("null", "must decode to a JSON object"),
    ],
)
def test_backtest_run_config_rejects_invalid_cli_json(runner, raw_config, message):
    with patch("keel.client.KeelClient.post") as post:
        result = runner.invoke(
            cli,
            ["backtest", "run", "strat_xyz", "--config", raw_config, "--no-wait"],
        )

    assert result.exit_code == 2
    assert message in result.output
    post.assert_not_called()
