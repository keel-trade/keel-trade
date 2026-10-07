"""Tests for `keel_components_get_many`.

The canonical pre-composition step per the `strategy-creation` skill:
fetch full details for many components in one call, walk the result
pair-wise to verify types fit BEFORE drafting DSL. Replaces N
round-trips of `keel_components_get`.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
import respx
from click.testing import CliRunner
from keel.cli.main import cli
from keel.errors import NotFoundError
from keel.tools.outcomes import OUTCOMES, _bootstrap

# Import for side-effect registration.
from keel.tools.outcomes import components_detail_batch as _batch_mod  # noqa: F401
from keel.tools.outcomes._base import ToolContext


runner = CliRunner()


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


@pytest.fixture(autouse=True)
def _no_component_api():
    """Pin the bundled registry as the data source for this file.

    `components_help._detail_via_api` opportunistically tries
    `GET /v1/components/{name}` (the Phase-2C endpoint that doesn't exist
    yet) and swallows every failure. Left alone in a CLI test that is
    `_isolate_user_config`-ed into having no credentials, that probe hit
    the PRODUCTION API and minted a real anonymous org per invocation.
    Stub it with the miss it will get in reality so the batch resolves
    from `keel/data/registry.json` — which is what these tests assert.

    Patched at `_request`, not `get`, so `KeelClient._require_auth` still
    runs for real — the stream-discipline test below depends on the
    genuine auth branch.
    """
    with patch(
        "keel.client.KeelClient._request",
        side_effect=NotFoundError("no /v1/components endpoint in this deployment"),
    ):
        yield


def _ctx():
    return ToolContext(
        is_tty=False,
        app_url="https://app.usekeel.io",
        share_url_root="https://usekeel.io/share",
    )


# ─── Tool registration ────────────────────────────────────────────────────


def test_batch_detail_tool_registered():
    assert "keel_components_get_many" in OUTCOMES
    tool = OUTCOMES["keel_components_get_many"]
    assert tool.toolset == "read-only"
    assert tool.required_action == "component.read"


# ─── Happy path: batch returns dict keyed by name ─────────────────────────


def test_batch_returns_dict_keyed_by_name():
    tool = OUTCOMES["keel_components_get_many"]
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
    tool = OUTCOMES["keel_components_get_many"]
    result = tool.handler({"names": ["ROC", "DefinitelyNotARealComponent_XYZ"]}, _ctx())
    env = result.to_envelope()
    assert env["found"] == 1
    assert env["missing"] == 1
    assert env["components"]["ROC"]["category"] == "indicator"
    assert "error" in env["components"]["DefinitelyNotARealComponent_XYZ"]


def test_batch_with_only_unknown_names_still_returns_partial():
    """All-not-found is still a valid response, not an exception."""
    tool = OUTCOMES["keel_components_get_many"]
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

    tool = OUTCOMES["keel_components_get_many"]
    with pytest.raises(KeelError) as exc:
        tool.handler({"names": []}, _ctx())
    assert "missing required" in str(exc.value).lower() or "names" in str(exc.value).lower()


def test_batch_strips_blank_names():
    """Whitespace-only or empty strings in the list are silently dropped."""
    tool = OUTCOMES["keel_components_get_many"]
    result = tool.handler({"names": ["ROC", "", "  ", "EWMA"]}, _ctx())
    env = result.to_envelope()
    # Only the two real names processed.
    assert set(env["components"].keys()) == {"ROC", "EWMA"}


def test_batch_accepts_comma_separated_string_for_cli_convenience():
    """CLI users may sometimes pass `--names ROC,EWMA` as a single string."""
    tool = OUTCOMES["keel_components_get_many"]
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
    data = json.loads(result.stdout)
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
    data = json.loads(result.stdout)
    assert data["found"] == 1
    assert data["missing"] == 1
    assert "error" in data["components"]["TotallyMadeUp"]


def test_cli_describe_batch_zero_args_errors_cleanly():
    """No names → usage error with a clear remediation hint."""
    result = runner.invoke(cli, ["components", "describe-batch", "--format", "json"])
    # Click 'argument required' OR our usage_error envelope
    assert result.exit_code != 0


def test_cli_json_payload_is_stdout_only_notices_go_to_stderr(monkeypatch):
    """`--format json` puts ONLY the payload on stdout (spec: `output.emit`
    writes stdout, `emit_error` + notices write stderr).

    Regression, sdk-test run 30684... : the two CLI tests above parsed
    `result.output`, which on Click ≥8.2 interleaves stderr into stdout.
    When the CLI happened to print the anonymous instant-start notice
    ("Running anonymously — …") the parse died with
    `JSONDecodeError: Extra data` — and whether it printed depended on
    whether a previous test had already left credentials behind. That is
    an order-dependent flake, so pin the stream discipline directly.
    """
    monkeypatch.delenv("KEEL_ANON_AUTO", raising=False)  # arm instant start
    grant = {
        "access_token": "eyJanon.access.token",
        "refresh_token": "krt_anon_refresh",
        "token_type": "Bearer",
        "expires_in": 3600,
        "org_id": "org_anon_1",
        "notice": "Running anonymously — run `keel auth login` to keep your work.",
    }
    with respx.mock(assert_all_called=False) as api:
        mint = api.post("https://api.usekeel.io/v1/auth/anonymous").mock(
            return_value=httpx.Response(201, json=grant)
        )
        # Q-2494: a bundled lookup never mints, so it prints no notice at all.
        lookup = runner.invoke(cli, ["components", "describe-batch", "ROC", "--format", "json"])
        assert not mint.called
        assert json.loads(lookup.stdout)["components"]["ROC"]["name"] == "ROC"
        # A command that needs auth mints — and its notice stays off stdout.
        # (`_request` answers its /v1/me read; the mint itself is httpx.post.)
        with patch("keel.client.KeelClient._request", return_value={"org": {"plan": "anon"}}):
            result = runner.invoke(cli, ["plan", "status", "--format", "json"])

    assert mint.called
    assert result.exit_code == 0, result.output
    # The notice went out — and it is NOT on the machine-readable stream.
    assert "Running anonymously" in result.stderr
    assert "Running anonymously" not in result.stdout
    assert json.loads(result.stdout)["plan"] == "anon"
