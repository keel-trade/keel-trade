"""Tests for `keel_components_detail_batch`.

The canonical pre-composition step per the `strategy-creation` skill:
fetch full details for many components in one call, walk the result
pair-wise to verify types fit BEFORE drafting DSL. Replaces N
round-trips of `keel_components_compose_help`.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from keel.cli.main import cli
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext

# Import for side-effect registration.
from keel.tools.outcomes import components_detail_batch as _batch_mod  # noqa: F401


runner = CliRunner()


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx():
    return ToolContext(
        is_tty=False,
        app_url="https://app.usekeel.io",
        share_url_root="https://usekeel.io/share",
    )


# ─── Tool registration ────────────────────────────────────────────────────


def test_batch_detail_tool_registered():
    assert "keel_components_detail_batch" in OUTCOMES
    tool = OUTCOMES["keel_components_detail_batch"]
    assert tool.toolset == "read-only"
    assert tool.required_action == "component.read"


# ─── Happy path: batch returns dict keyed by name ─────────────────────────


def test_batch_returns_dict_keyed_by_name():
    tool = OUTCOMES["keel_components_detail_batch"]
    result = tool.handler({"names": ["ROC", "EWMA", "ForecastScaler"]}, _ctx())
    env = result.to_envelope()
    assert env["found"] == 3
    assert env["missing"] == 0
    assert set(env["components"].keys()) == {"ROC", "EWMA", "ForecastScaler"}
    # Each entry must carry the full single-detail shape
    for name, detail in env["components"].items():
        assert detail["name"] == name
        assert "category" in detail
        assert "input_type" in detail
        assert "output_type" in detail
        assert "parameters" in detail


# ─── Partial-success semantics (unknown names don't fail the batch) ──────


def test_batch_returns_error_entries_for_unknown_names():
    """Unknown component names become `{"error": "..."}` entries — the
    batch as a whole succeeds. Matches chat-api's
    `strategy_component_detail_batch` shape."""
    tool = OUTCOMES["keel_components_detail_batch"]
    result = tool.handler({"names": ["ROC", "DefinitelyNotARealComponent_XYZ"]}, _ctx())
    env = result.to_envelope()
    assert env["found"] == 1
    assert env["missing"] == 1
    assert env["components"]["ROC"]["category"] == "indicator"
    assert "error" in env["components"]["DefinitelyNotARealComponent_XYZ"]


def test_batch_with_only_unknown_names_still_returns_partial():
    """All-not-found is still a valid response, not an exception."""
    tool = OUTCOMES["keel_components_detail_batch"]
    result = tool.handler({"names": ["NopeA", "NopeB"]}, _ctx())
    env = result.to_envelope()
    assert env["found"] == 0
    assert env["missing"] == 2
    assert "error" in env["components"]["NopeA"]
    assert "error" in env["components"]["NopeB"]


# ─── Input validation ────────────────────────────────────────────────────


def test_batch_missing_names_raises_usage_error():
    """Empty names list is a usage error (no batch to perform)."""
    from keel.errors import KeelError

    tool = OUTCOMES["keel_components_detail_batch"]
    with pytest.raises(KeelError) as exc:
        tool.handler({"names": []}, _ctx())
    assert "missing required" in str(exc.value).lower() or "names" in str(exc.value).lower()


def test_batch_strips_blank_names():
    """Whitespace-only or empty strings in the list are silently dropped."""
    tool = OUTCOMES["keel_components_detail_batch"]
    result = tool.handler({"names": ["ROC", "", "  ", "EWMA"]}, _ctx())
    env = result.to_envelope()
    # Only the two real names processed.
    assert set(env["components"].keys()) == {"ROC", "EWMA"}


def test_batch_accepts_comma_separated_string_for_cli_convenience():
    """CLI users may sometimes pass `--names ROC,EWMA` as a single string."""
    tool = OUTCOMES["keel_components_detail_batch"]
    result = tool.handler({"names": "ROC, EWMA, ForecastScaler"}, _ctx())
    env = result.to_envelope()
    assert set(env["components"].keys()) == {"ROC", "EWMA", "ForecastScaler"}


# ─── CLI: variadic positional ─────────────────────────────────────────────


def test_cli_describe_batch_accepts_positional_variadic_names():
    """`keel components describe-batch ROC EWMA ForecastScaler` (no flags)
    must work. Click default for array types is `--names X --names Y`
    which is bash-hostile; the schema marks `names` as
    `x-cli-positional` so the CLI adapter renders it as `nargs=-1`."""
    result = runner.invoke(
        cli,
        ["components", "describe-batch", "ROC", "EWMA", "ForecastScaler", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["found"] == 3
    assert set(data["components"].keys()) == {"ROC", "EWMA", "ForecastScaler"}


def test_cli_describe_batch_partial_failure_returns_success_exit():
    """Unknown components in the batch don't make the CLI exit non-zero —
    they surface as `error` entries in the partial result."""
    result = runner.invoke(
        cli,
        ["components", "describe-batch", "ROC", "TotallyMadeUp", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["found"] == 1
    assert data["missing"] == 1
    assert "error" in data["components"]["TotallyMadeUp"]


def test_cli_describe_batch_zero_args_errors_cleanly():
    """No names → usage error with a clear remediation hint."""
    result = runner.invoke(cli, ["components", "describe-batch", "--format", "json"])
    # Click 'argument required' OR our usage_error envelope
    assert result.exit_code != 0
