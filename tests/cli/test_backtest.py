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
                "--skip-readiness",
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


# ── `keel backtest compare` through the real CLI entry point (Q-2211) ──
#
# Q-1886 added compare's optional `holds` array and tested it only by calling
# the handler with a list (the shape MCP sends). The CLI builds its args from
# Click, which hands `multiple=True` options over as a TUPLE — `()` when the
# flag is absent — so every CLI compare was refused with "`holds` is a list of
# symbols", with or without `--holds`. These arms drive `cli` itself.
#
# SEED A (run 2026-09-30): make the array-normalisation loop in
# `_cli_adapter._build_click_command`'s callback iterate nothing → all four
# arms go red: the three accept arms exit 2 with "`holds` is a list of
# symbols" (the staging symptom), and the DOGE arm gets that message instead
# of the handler's symbol refusal. Restored → 4 green.
# SEED B (run): keep a given value as a tuple (the empty one still dropped) →
# the no-holds arm stays green and the two `--holds` arms plus DOGE go red.
# SEED C (run): keep the absent flag's `()` → ONLY the no-holds arm goes red.
# So the arms see the tuple→list half and the ()→absent half separately.


def _compare_get(seen: list[tuple[str, dict]]):
    def fake_get(path, **kwargs):
        seen.append((path, kwargs))
        if path.endswith("/curve"):
            return {}
        if path.startswith("/v1/backtests/"):
            bt_id = path.rsplit("/", 1)[1]
            return {
                "id": bt_id,
                "status": "COMPLETED",
                "strategy_id": "strat_cmp",
                "commit_id": f"cmt_{bt_id}",
                "engine": "native",
                "start_date": "2024-08-15",
                "end_date": "2026-02-27",
                "metrics": {"sharpe_ratio": 1.0},
            }
        # Version source / message reads are best-effort for compare.
        return {}

    return fake_get


@pytest.mark.parametrize(
    ("extra", "asked"),
    [
        ([], None),
        (["--holds", "BTC"], ("BTC",)),
        (["--holds", "eth", "--holds", "SOL", "--holds", "ETH"], ("ETH", "SOL")),
    ],
    ids=["no-holds", "one-hold", "dedup-two-holds"],
)
def test_backtest_compare_cli_accepts_holds_or_none(runner, extra, asked):
    seen: list[tuple[str, dict]] = []
    with patch("keel.client.KeelClient.get", side_effect=_compare_get(seen)):
        result = runner.invoke(
            cli, ["--format", "json", "backtest", "compare", "bt_a", "bt_b", *extra]
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["summary_text"] == "Sharpe 1.00→1.00"
    # Non-vacuity: both runs were read, and the holds (or their absence)
    # reached the /curve read — run 1 only asks for references when `holds` did.
    curve_reads = {path: kw for path, kw in seen if path.endswith("/curve")}
    assert set(curve_reads) == {"/v1/backtests/bt_a/curve", "/v1/backtests/bt_b/curve"}
    assert "references" not in curve_reads["/v1/backtests/bt_b/curve"]
    if asked is None:
        assert "references" not in curve_reads["/v1/backtests/bt_a/curve"]
    else:
        assert curve_reads["/v1/backtests/bt_a/curve"]["references"] == "true"


def test_backtest_compare_cli_refuses_an_unknown_hold_through_the_handler(runner):
    """Control arm: the CLI now reaches the SAME validation MCP does — a symbol
    outside the set is still refused, by the handler, not the adapter."""
    seen: list[tuple[str, dict]] = []
    with patch("keel.client.KeelClient.get", side_effect=_compare_get(seen)):
        result = runner.invoke(
            cli, ["--format", "json", "backtest", "compare", "bt_a", "bt_b", "--holds", "DOGE"]
        )

    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["message"] == (
        "`holds` takes 1–3 of BTC, ETH, SOL (got DOGE)."
    )
    assert seen == []


# ── `keel backtest compare --format human` (Q-2221) ─────────────────────
#
# Staging, 2026-09-30: the human output printed the table and then every
# envelope field as indented JSON (~900 lines: `metrics_raw_*`, the spec
# diffs, `render`, and each hold line with its whole series, twice). The
# `--holds` lines appeared only inside that JSON. These arms drive `cli`
# with two holds whose `/curve` serves 121-point series.

_HOLDS = (
    ("BTC", 41.2, 30.0, 100000, 141200),
    ("ETH", -5.0, 50.0, 3300, 3135),
    ("SOL", 12.5, 40.0, 200, 225),
)


def _holds_get(path, **kwargs):
    if path.endswith("/curve"):
        points = [
            {
                "t": f"2025-03-{(i % 28) + 1:02d}T00:00:00Z",
                "equity": 10000 + i,
                "drawdown_pct": -1.0,
            }
            for i in range(121)
        ]
        out: dict = {"points": points, "start": "2025-01-21", "end": "2025-06-01"}
        if kwargs.get("references") == "true":
            out["references"] = [
                {
                    "symbol": symbol,
                    "ret_pct": ret,
                    "dd_pct": dd,
                    "start_close": first,
                    "end_close": last,
                    "rebased_at": "2025-01-21",
                    "end_close_at": "2025-06-01",
                    "series": [{"t": p["t"], "equity": p["equity"]} for p in points],
                }
                for symbol, ret, dd, first, last in _HOLDS
            ]
            out["reference_span"] = {
                "start": kwargs.get("reference_start"),
                "end": kwargs.get("reference_end"),
            }
        return out
    if path.startswith("/v1/backtests/"):
        bt_id = path.rsplit("/", 1)[1]
        return {
            "id": bt_id,
            "status": "COMPLETED",
            "strategy_id": "strat_cmp",
            "strategy_name": "Momentum",
            "commit_id": f"cmt_{bt_id}",
            "sequence_number": 1 if bt_id == "bt_a" else 2,
            "engine": "native",
            "start_date": "2025-01-01",
            "end_date": "2025-06-01",
            "metrics": {
                "sharpe_ratio": 1.25 if bt_id == "bt_a" else 1.5,
                "chart_start": "2025-01-21",
                "effective_end": "2025-06-01",
            },
        }
    return {}


_ARGS = ["backtest", "compare", "bt_a", "bt_b", "--holds", "BTC", "--holds", "SOL"]


def test_backtest_compare_human_prints_the_table_and_its_hold_lines_and_no_json(runner):
    """SEED A (run 2026-09-30): make the `_render_comparison` branch in
    `_cli_adapter._render` never match → red on the no-JSON assert (`{`:
    the ~1,580-line dump is back). SEED B (run): drop the per-hold
    `reference:` lines in `_render_comparison` → red on the BTC hold
    assert, past the table and no-JSON asserts. The json control arm stays
    green through both."""
    with patch("keel.client.KeelClient.get", side_effect=_holds_get):
        result = runner.invoke(cli, ["--format", "human", *_ARGS])

    assert result.exit_code == 0, result.output
    out = result.output
    lines = out.splitlines()
    # The table, first.
    assert lines[0].startswith("**Momentum** · 2 runs")
    assert "| Sharpe | 1.25 | 1.50 |" in lines
    # No raw envelope: no JSON object, no series, no structured twin.
    for token in ("{", '"series"', "metrics_raw", "spec_diffs", "render:", "references:"):
        assert token not in out, token
    assert len(lines) < 30
    # One sentence per asked hold, over the span every run covers; not ETH.
    span = "Jan 21, 2025 – Jun 1, 2025 (the span every run covers)"
    assert (
        f"reference: BTC hold, price only, {span}: +41.2% ($100,000 → $141,200) "
        "· max drawdown −30.0%"
    ) in lines
    assert (
        f"reference: SOL hold, price only, {span}: +12.5% ($200 → $225) · max drawdown −40.0%"
    ) in lines
    assert "ETH hold" not in out
    # The link still ends the output, clickable.
    assert lines[-1] == "https://app.usekeel.io/strategies/strat_cmp/edit"


def test_backtest_compare_json_keeps_the_full_envelope(runner):
    """Control arm: `--format json` is the whole envelope — every hold with
    its series, and the fields the terminal leaves out."""
    with patch("keel.client.KeelClient.get", side_effect=_holds_get):
        result = runner.invoke(cli, ["--format", "json", *_ARGS])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert [r["label"] for r in envelope["references"]] == ["BTC hold", "SOL hold"]
    assert all(len(r["series"]) == 121 for r in envelope["references"])
    assert envelope["reference"]["label"] == "BTC hold"
    for key in ("metrics_raw_a", "metrics_raw_b", "spec_diffs", "render", "view"):
        assert key in envelope, key
