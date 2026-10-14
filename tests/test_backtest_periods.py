"""The period table on the agent surface (Q-2565).

`keel_backtest_summarize(periods=monthly|quarterly)` and
`keel_backtest_compare(periods=…)` read keel-api's `/periods` — the slice
owner called once per calendar period — and relay it as `periods` + one
`periods:` GFM table in the text block; the CLI prints the same table and
`--format json` the same block. Without `periods` nothing is read and the
envelope is byte-identical to today's. Every cell is the server's number:
nothing in the SDK computes a return or a drawdown.

SEED (run 2026-10-08): make `periods_markdown` print the run row's return in
the August cell (`row.get('return_pct')` → `run.get('return_pct')` in
`single`) → 5 red: the summarize text-block arm, the cells-are-the-server's
arm, the CLI human arm, the no-bar dashes arm and test_operational_lines'
`periods` appears-arm; the control arms (no `periods`, bad grain, the
refusal, CLI JSON parity, compare alignment — the list renderer — and the
five-run header) stay green. Reverted by reversing the edit, byte-exact.
"""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from keel.cli.main import cli
from keel.errors import KeelError, ValidationError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._backtest_view import (
    PERIOD_GRAINS,
    align_periods,
    periods_markdown,
)
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import operational_lines


_bootstrap()

BASIS = (
    "Each period is a slice of the run as it ran, opening at the run's equity just "
    "before its first bar, so the periods' returns compound to the whole run's; max "
    "drawdown is within the period, from its own peak. A period before the run's "
    "first equity move is warm-up."
)


def _row(period, start, end, bars, ret, dd, opened, closed, carried, **flags):
    return {
        "period": period,
        "start": start,
        "end": end,
        "bars": bars,
        "return_pct": ret,
        "max_drawdown_pct": dd,
        "equity": {"open": 10_000.0, "close": 10_000.0},
        "positions": {"opened": opened, "closed": closed, "carried_in": carried},
        "trades_closed": closed,
        **flags,
    }


#: keel-api's monthly block for the services/keel-api/tests/test_backtest_periods.py
#: run (2026-07-15 → 10-10): the numbers that file proves.
MONTHLY = {
    "grain": "monthly",
    "rows": [
        _row(
            "2026-07", "2026-07-15", "2026-07-31", 17, 0.0, 0.0, 0, 0, 0, partial=True, warmup=True
        ),
        _row("2026-08", "2026-08-01", "2026-08-31", 31, 5.0, -2.88, 1, 1, 0),
        _row("2026-09", "2026-09-01", "2026-09-30", 30, -2.0, -2.92, 1, 0, 0),
        _row("2026-10", "2026-10-01", "2026-10-10", 10, 4.96, 0.0, 0, 1, 1, partial=True),
    ],
    "run": _row("run", "2026-07-15", "2026-10-10", 88, 8.0, -2.92, 2, 2, 0),
    "basis": BASIS,
}


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


def _detail(run_id="bt_x"):
    return {
        "id": run_id,
        "status": "COMPLETED",
        "strategy_id": "str_x",
        "strategy_name": "X",
        "start_date": "2026-07-15",
        "end_date": "2026-10-11",
        "metrics": {"sharpe_ratio": 1.2, "total_return": 8.0, "max_drawdown": -2.92},
    }


def _fake_get(periods_answer=MONTHLY):
    calls: list[tuple[str, dict]] = []

    def fake_get(path, **kw):
        calls.append((path, kw))
        if path == "/v1/backtests/bt_x":
            return _detail()
        if path == "/v1/backtests/bt_x/periods":
            if isinstance(periods_answer, Exception):
                raise periods_answer
            return {"job_id": "bt_x", "periods": deepcopy(periods_answer), "run_window": {}}
        if path in ("/v1/backtests/bt_x/results", "/v1/backtests/bt_x/curve"):
            return {}
        raise KeelError(f"not in this fixture: {path}")

    return fake_get, calls


def _summarize(ctx, args, periods_answer=MONTHLY):
    fake_get, calls = _fake_get(periods_answer)
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        env = (
            OUTCOMES["keel_backtest_summarize"]
            .handler({"backtest_id": "bt_x", **args}, ctx)
            .to_envelope()
        )
    return env, calls


AUGUST_LINE = "| 2026-08 | +5.00% | −2.88% | 1 | 31 |"
JULY_LINE = "| 2026-07 (part) | warm-up | warm-up | 0 | 17 |"
RUN_LINE = "| run | +8.00% | −2.92% | 2 | 88 |"


class TestSummarize:
    def test_periods_is_one_read_copied_verbatim_and_one_table(self, ctx):
        env, calls = _summarize(ctx, {"periods": "monthly"})
        (asked,) = [kw for path, kw in calls if path == "/v1/backtests/bt_x/periods"]
        assert asked == {"periods": "monthly"}
        assert env["periods"] == MONTHLY
        (line,) = [x for x in operational_lines(env) if x.startswith("periods: ")]
        assert line.startswith(f"periods: monthly periods — {BASIS}\n| period | return | max DD |")
        assert AUGUST_LINE in line and JULY_LINE in line and RUN_LINE in line
        # The table ends on the whole run; no cell is a number the server
        # did not send.
        assert line.rstrip().endswith(RUN_LINE)

    def test_without_periods_nothing_is_read_and_the_envelope_is_unchanged(self, ctx):
        """CONTROL: the default path is byte-identical to today's — the only
        difference `periods` makes is the one key it adds."""
        plain, calls = _summarize(ctx, {})
        assert "periods" not in plain
        assert all(path != "/v1/backtests/bt_x/periods" for path, _ in calls)
        assert calls[0][0] == "/v1/backtests/bt_x"  # non-vacuity: the read happened
        assert not any(x.startswith("periods:") for x in operational_lines(plain))
        with_table, _ = _summarize(ctx, {"periods": "monthly"})
        with_table.pop("periods")
        # The election key (`view.at`, `view.seq`) is minted per call.
        for env in (plain, with_table):
            env["view"].pop("at", None)
            env["view"].pop("seq", None)
        assert with_table == plain

    def test_quarterly_is_asked_as_quarterly(self, ctx):
        _, calls = _summarize(ctx, {"periods": "quarterly"})
        (asked,) = [kw for path, kw in calls if path == "/v1/backtests/bt_x/periods"]
        assert asked == {"periods": "quarterly"}

    def test_a_grain_outside_the_enum_is_refused_before_any_read(self, ctx):
        with patch("keel.client.KeelClient.get") as get, pytest.raises(KeelError) as exc:
            OUTCOMES["keel_backtest_summarize"].handler(
                {"backtest_id": "bt_x", "periods": "weekly"}, ctx
            )
        assert exc.value.error_code == "usage_error"
        assert "`monthly`" in str(exc.value) and "`quarterly`" in str(exc.value)
        get.assert_not_called()
        assert tuple(PERIOD_GRAINS) == ("monthly", "quarterly")

    def test_the_servers_refusal_is_the_result(self, ctx):
        refusal = ValidationError(
            "46 monthly periods over 2023-01-01 → 2026-10-10 is more than the 36 a table "
            "carries; ask for `quarterly`.",
            error_code="PERIODS_TOO_MANY",
        )
        with pytest.raises(ValidationError) as exc:
            _summarize(ctx, {"periods": "monthly"}, periods_answer=refusal)
        assert "46 monthly periods" in str(exc.value)

    def test_every_cell_is_the_servers_number(self, ctx):
        """Non-vacuity + the one-owner rule: four rows (≥ 3 periods) and each
        return / drawdown / count cell is the block's own value, formatted."""
        env, _ = _summarize(ctx, {"periods": "monthly"})
        table = periods_markdown(env["periods"])
        rows = env["periods"]["rows"]
        assert len(rows) == 4
        for row in rows:
            if row.get("warmup"):
                continue
            ret = f"{row['return_pct']:+.2f}%".replace("-", "−")
            dd = f"{row['max_drawdown_pct']:.2f}%".replace("-", "−")
            assert f"| {ret} | {dd} | {row['positions']['opened']} | {row['bars']} |" in table


class TestCliParity:
    def test_the_cli_json_is_the_mcp_envelope(self, ctx, monkeypatch):
        monkeypatch.setenv("KEEL_API_KEY", "kt_test")
        fake_get, calls = _fake_get()
        with patch("keel.client.KeelClient.get", side_effect=fake_get):
            result = CliRunner().invoke(
                cli,
                ["--format", "json", "backtest", "summarize", "bt_x", "--periods", "monthly"],
            )
        assert result.exit_code == 0, result.output
        from_cli = json.loads(result.stdout)
        assert from_cli["periods"] == MONTHLY
        env, _ = _summarize(ctx, {"periods": "monthly"})
        # Same block, same metrics, same view: one handler behind both verbs.
        assert from_cli["periods"] == env["periods"]
        assert from_cli["summary_metrics"] == env["summary_metrics"]
        assert from_cli["view"]["markdown"] == env["view"]["markdown"]
        (asked,) = [kw for path, kw in calls if path == "/v1/backtests/bt_x/periods"]
        assert asked == {"periods": "monthly"}

    def test_the_cli_human_output_prints_the_table_not_the_rows(self, monkeypatch):
        monkeypatch.setenv("KEEL_API_KEY", "kt_test")
        fake_get, _ = _fake_get()
        with patch("keel.client.KeelClient.get", side_effect=fake_get):
            result = CliRunner().invoke(
                cli, ["--format", "human", "backtest", "summarize", "bt_x", "--periods", "monthly"]
            )
        assert result.exit_code == 0, result.output
        assert f"periods: monthly periods — {BASIS}" in result.output
        assert AUGUST_LINE in result.output and RUN_LINE in result.output
        assert '"return_pct"' not in result.output

    def test_the_cli_flag_takes_only_the_enum(self):
        result = CliRunner().invoke(cli, ["backtest", "summarize", "bt_x", "--periods", "weekly"])
        assert result.exit_code != 0
        assert "monthly" in result.output and "quarterly" in result.output


def _block_without(block: dict, period: str) -> dict:
    out = deepcopy(block)
    out["rows"] = [r for r in out["rows"] if r["period"] != period]
    return out


class TestCompare:
    def _envelope(self, args: dict, blocks: dict[str, dict]):
        details = {f"bt_{i}": {**_detail(f"bt_{i}"), "sequence_number": i} for i in (1, 2)}
        calls: list[tuple[str, dict]] = []

        def fake_get(path, **kw):
            calls.append((path, kw))
            parts = path.split("/")
            if path.startswith("/v1/backtests/") and path.endswith("/periods"):
                return {"job_id": parts[3], "periods": deepcopy(blocks[parts[3]]), "run_window": {}}
            if path.startswith("/v1/backtests/") and path.endswith("/curve"):
                return {}
            if path.startswith("/v1/backtests/"):
                return details[parts[3]]
            if path.startswith("/v1/strategies/str_x/versions/"):
                return {"source": "Pipeline([])"} if path.endswith("/source") else {"message": None}
            if path == "/v1/strategies/str_x":
                return {"id": "str_x"}
            raise KeelError(f"not in this fixture: {path}")

        ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
        with patch("keel.client.KeelClient.get", side_effect=fake_get):
            env = (
                OUTCOMES["keel_backtest_compare"]
                .handler({"backtest_ids": ["bt_1", "bt_2"], **args}, ctx)
                .to_envelope()
            )
        return env, calls

    def test_the_runs_tables_are_aligned_by_period(self):
        second = deepcopy(MONTHLY)
        second["rows"][1]["return_pct"] = 6.5  # v2's August
        second["run"]["return_pct"] = 9.5
        env, calls = self._envelope(
            {"periods": "monthly"}, {"bt_1": MONTHLY, "bt_2": _block_without(second, "2026-07")}
        )
        asked = [(path, kw) for path, kw in calls if path.endswith("/periods")]
        assert asked == [
            ("/v1/backtests/bt_1/periods", {"periods": "monthly"}),
            ("/v1/backtests/bt_2/periods", {"periods": "monthly"}),
        ]
        block = env["periods"]
        labels = block["labels"]
        assert len(labels) == 2 and block["grain"] == "monthly" and block["basis"] == BASIS
        assert [r["period"] for r in block["rows"]] == ["2026-07", "2026-08", "2026-09", "2026-10"]
        july, august = block["rows"][0], block["rows"][1]
        # v2 has no July: its cell is null, never a number.
        assert july["return_pct"] == [0.0, None] and july["warmup"] == [True, False]
        assert august["return_pct"] == [5.0, 6.5]
        assert august["max_drawdown_pct"] == [-2.88, -2.88]
        assert block["run"]["return_pct"] == [8.0, 9.5]
        (line,) = [x for x in operational_lines(env) if x.startswith("periods: ")]
        header = f"| period | {labels[0]} return | {labels[1]} return | {labels[0]} max DD | {labels[1]} max DD |"
        assert header in line
        assert "| 2026-07 | warm-up | — | warm-up | — |" in line
        assert "| 2026-08 | +5.00% | +6.50% | −2.88% | −2.88% |" in line
        assert line.rstrip().endswith("| run | +8.00% | +9.50% | −2.92% | −2.92% |")

    def test_without_periods_no_table_is_read(self):
        env, calls = self._envelope({}, {})
        assert "periods" not in env
        assert not any(path.endswith("/periods") for path, _ in calls)
        assert any(path == "/v1/backtests/bt_1" for path, _ in calls)  # non-vacuity


class TestAlignAndRender:
    def test_beyond_four_runs_the_table_keeps_returns_and_drops_drawdowns(self):
        labels = [f"v{i}" for i in range(1, 6)]
        aligned = align_periods([deepcopy(MONTHLY)] * 5, labels)
        table = periods_markdown(aligned)
        assert "| period | v1 return | v2 return | v3 return | v4 return | v5 return |" in table
        assert "max DD" not in table
        assert "| 2026-08 | +5.00% | +5.00% | +5.00% | +5.00% | +5.00% |" in table
        # CONTROL: four runs keep the drawdown columns.
        four = periods_markdown(align_periods([deepcopy(MONTHLY)] * 4, labels[:4]))
        assert "v4 max DD" in four

    def test_a_block_with_no_rows_is_no_table(self):
        assert periods_markdown({"grain": "monthly", "rows": []}) is None
        assert periods_markdown(None) is None
        assert align_periods([None, None], ["a", "b"]) is None

    def test_a_period_with_no_bar_renders_dashes(self):
        block = deepcopy(MONTHLY)
        block["rows"][2].update(
            {
                "bars": 0,
                "return_pct": None,
                "max_drawdown_pct": None,
                "equity": None,
                "positions": None,
            }
        )
        assert "| 2026-09 | — | — | — | 0 |" in periods_markdown(block)


def test_the_parameter_is_declared_identically_on_both_tools_and_profiles():
    """One surface: the same enum and the same words on the CLI flag, the
    full MCP schema and the listed MCP schema."""
    from keel.tools.outcomes._mcp_adapter import effective_input_schema

    for name in ("keel_backtest_summarize", "keel_backtest_compare"):
        tool = OUTCOMES[name]
        prop = tool.input_schema["properties"]["periods"]
        assert prop["enum"] == list(PERIOD_GRAINS) and prop["type"] == "string"
        assert "Optional" in prop["description"] and "Omitted, no table." in prop["description"]
        assert "periods" not in tool.input_schema.get("required", [])
        if tool.listed_input_schema is not None:
            assert tool.listed_input_schema["properties"]["periods"] == prop
        assert effective_input_schema(tool)["properties"]["periods"]["enum"] == list(PERIOD_GRAINS)
    for verb in ("summarize", "compare"):
        result = CliRunner().invoke(cli, ["backtest", verb, "--help"])
        assert result.exit_code == 0 and "--periods" in result.output, verb
