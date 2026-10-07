"""Tests for the outcome-tool infrastructure (shared between CLI + MCP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from keel.cli.main import cli
from keel.errors import NotFoundError
from keel.tools.outcomes import OUTCOMES, OutcomeResult, _bootstrap, all_tools
from keel.tools.outcomes._base import envelope_error
from keel.tools.outcomes._toolsets import is_tool_loaded, load_toolsets


runner = CliRunner()


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


# ─── _base.py ────────────────────────────────────────────────────────────


def test_outcome_result_to_envelope_includes_share_url_as_explicit_null():
    """`share_url` is always present in the envelope — `null` until
    explicitly created. Agents see the deliberate "private by default" signal."""
    result = OutcomeResult(run_id="bt_x", hero_url="https://app.usekeel.io/backtests/bt_x")
    env = result.to_envelope()
    assert "share_url" in env
    assert env["share_url"] is None
    assert env["hero_url"].startswith("https://app.usekeel.io/")
    assert env["run_id"] == "bt_x"


def test_outcome_result_extra_cannot_clobber_contract_fields():
    result = OutcomeResult(
        run_id="bt_x",
        hero_url="https://app.usekeel.io/x",
        extra={"hero_url": "ATTACKER", "summary_metrics": {"a": 1}},
    )
    env = result.to_envelope()
    assert env["hero_url"] == "https://app.usekeel.io/x"
    # `summary_metrics` is contractual but None — extra fills it in
    assert env["summary_metrics"] == {"a": 1}


def test_envelope_error_has_five_required_fields():
    e = envelope_error(
        code="missing_x",
        message="x is required",
        what_was_expected="A string x.",
        example={"x": "abc"},
        suggested_next_action={"tool": "keel_help", "args": {"topic": "x"}, "why": "..."},
    )
    assert set(e.keys()) == {
        "code",
        "message",
        "what_was_expected",
        "example",
        "suggested_next_action",
    }


def test_outcome_input_schemas_are_top_level_strict():
    """All public outcome schemas should reject unknown top-level args."""
    for name, tool in OUTCOMES.items():
        schema = tool.input_schema
        assert schema.get("type") == "object", f"{name}: input_schema must be an object"
        assert schema.get("additionalProperties") is False, (
            f"{name}: input_schema must set additionalProperties=false"
        )
        assert isinstance(schema.get("properties"), dict), (
            f"{name}: input_schema must define properties"
        )
        assert isinstance(schema.get("required"), list), (
            f"{name}: input_schema must define required as a list"
        )


# ─── _toolsets.py ────────────────────────────────────────────────────────


def test_load_toolsets_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    t = load_toolsets()
    assert "always" in t
    assert "read-only" in t
    assert "backtest" in t
    assert "share" in t
    assert "live-read" in t
    assert "live-write" not in t  # default excludes live write per spec §4
    assert "live" not in t  # deprecated alias is not returned


def test_load_toolsets_parses_env(monkeypatch):
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,backtest,live-write")
    t = load_toolsets()
    assert "live-read" in t
    assert "live-write" in t
    assert "share" not in t
    assert "always" in t  # always implicit


def test_load_toolsets_expands_legacy_live_alias(monkeypatch):
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,backtest,live")
    t = load_toolsets()
    assert "live-read" in t
    assert "live-write" in t
    assert "live" not in t


def test_load_toolsets_ignores_unknown_entries(monkeypatch):
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,wibble,backtest")
    t = load_toolsets()
    assert "wibble" not in t
    assert "read-only" in t
    assert "backtest" in t


def test_is_tool_loaded_respects_always(monkeypatch):
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only")
    active = load_toolsets()
    assert is_tool_loaded("always", active)
    assert is_tool_loaded("read-only", active)
    assert not is_tool_loaded("live-write", active)


# ─── Pilot tools (status / doctor / help) ────────────────────────────────


def test_pilot_tools_registered():
    names = {t.name for t in all_tools()}
    assert "keel_account_status" in names
    assert "keel_connection_check" in names
    assert "keel_help" in names


def test_pilot_tools_are_always_loaded():
    for name in ("keel_account_status", "keel_connection_check", "keel_help"):
        assert OUTCOMES[name].toolset == "always"


def test_pilot_tool_descriptions_name_their_neighbours_as_facts():
    """Spec §4 line 389 asked every description to say when NOT to use the
    tool. Q-1804: on the listed surface that is a neutral statement naming
    the neighbour ("Enumerating strategies is `keel_strategy_search`"), never
    an imperative `Do NOT use… — call` (ChatGPT's per-call review reads the
    imperative as injected steering)."""
    import re

    for name in ("keel_account_status", "keel_connection_check", "keel_help"):
        desc = OUTCOMES[name].description
        # Each names its own confusable neighbour, backticked, as a fact.
        neighbours = set(re.findall(r"`(keel_[a-z_]+)`", desc)) - {name}
        assert neighbours, name
        assert "Do NOT" not in desc, f"{name} description still carries an imperative"


# ─── CLI adapter ─────────────────────────────────────────────────────────


def test_cli_status_returns_envelope_with_share_url_null():
    result = runner.invoke(cli, ["--format", "json", "status"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["share_url"] is None
    assert data["hero_url"].startswith("https://app.usekeel.io/")
    assert "toolsets_loaded" in data
    assert "tools_visible" in data
    assert "live-read" in data["toolsets_loaded"]
    assert "live-write" not in data["toolsets_loaded"]
    assert "keel_live_monitor" in data["tools_visible"]
    assert "keel_live_deploy" not in data["tools_visible"]
    assert "keel_live_control" not in data["tools_visible"]


def test_cli_status_includes_progressive_workflow_routes():
    result = runner.invoke(cli, ["--format", "json", "status"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    # Agent-surface-cleanup spec 02 §2.5 (R-22): on the full profile the
    # routes are `{name, when, tools}` only — no `next`/`prompt` method
    # prose — filtered to routes whose EVERY tool this server loads.
    routes = {route["name"]: route for route in data["workflow_routes"]}
    assert {"research_strategy", "debug_recovery", "live_monitoring"} <= set(routes)
    for route in routes.values():
        assert set(route) == {"name", "when", "tools"}, route
        assert set(route["tools"]) <= set(data["tools_visible"]), route
    research = routes["research_strategy"]
    assert research["tools"] == [
        "keel_components_search",
        "keel_components_get_many",
        "keel_strategy_compose",
        "keel_backtest_run",
        "keel_backtest_summarize",
    ]
    assert routes["live_monitoring"]["tools"] == [
        "keel_accounts_list",
        "keel_live_monitor",
    ]
    # Live-write is not loaded by default, so the route that names
    # `keel_live_deploy` is not served — the capability block says it.
    assert "live_trading" not in routes
    assert data["capabilities"] == {
        "research": True,
        "backtest": True,
        "monitor": "read-only",
        "live_actions": "web app",
    }


def test_cli_doctor_runs_checks(monkeypatch):
    # With no auth configured, doctor exits 1 — by design so CI scripts
    # `keel doctor && deploy` gate correctly. Force a clean env to get the
    # consistent unauth fail-path.
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    result = runner.invoke(cli, ["--format", "json", "doctor"])
    # Exit code 1 indicates failed checks — the structured payload still
    # has the per-check details we assert on below.
    assert result.exit_code in (0, 1), result.output
    # Payload may land in stdout (when checks pass) or stderr (envelope
    # error rendering when checks fail). Look in both.
    raw = result.output or result.stderr
    data = json.loads(raw)
    # Failed checks come through the spec §13.5 envelope's `example`
    # field; success path puts them at top level.
    checks = data.get("checks") or data.get("example", {}).get("checks")
    assert isinstance(checks, list) and checks
    names = {c["name"] for c in checks}
    assert "auth" in names
    assert "toolsets" in names


def test_cli_help_positional_topic():
    result = runner.invoke(cli, ["--format", "json", "help", "dsl_syntax"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["topic"] == "dsl_syntax"
    assert data.get("source") in {"bundled", "api"}
    assert data["resource_uri"] == "keel://knowledge/dsl_syntax"
    assert len(data.get("body", "")) > 0


def test_cli_help_reference_topic_returns_actual_resource_uri():
    result = runner.invoke(cli, ["--format", "json", "help", "phases"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["topic"] == "phases"
    assert data["resource_uri"] == "keel://dsl/reference/phases"
    assert len(data.get("body", "")) > 0


def test_cli_help_pattern_topic_omits_missing_resource_uri():
    result = runner.invoke(cli, ["--format", "json", "help", "combining_signals"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["topic"] == "combining_signals"
    assert "resource_uri" not in data
    assert len(data.get("body", "")) > 0


def test_help_result_carries_the_current_resource_uri_never_the_stale_one():
    """The claim this test has always made — `keel_help` points at the
    resource URI that exists — moved off the DESCRIPTION and onto the
    RESULT.

    The description used to spend 108 characters restating the mirror
    (`keel://dsl/reference/<topic>` / `keel://knowledge/<section>`); that
    budget now carries the `rule:<CODE>` explain channel, which is
    reachable no other way on claude.ai, while the resources stay
    registered and `keel_help` stays the universal path (guidance spec
    §3 L3). Asserting the RESULT is the stronger form anyway: a stale URI
    in prose is a typo, a stale URI in `resource_uri` is a dead fetch.
    """
    from keel.tools.outcomes import ToolContext

    handler = OUTCOMES["keel_help"].handler
    reference = handler({"topic": "types"}, ToolContext()).to_envelope()
    knowledge = handler({"topic": "tool_usage"}, ToolContext()).to_envelope()
    assert reference["resource_uri"] == "keel://dsl/reference/types"
    assert knowledge["resource_uri"] == "keel://knowledge/tool_usage"
    # The stale spelling appears nowhere an agent reads.
    assert "keel://reference/dsl" not in OUTCOMES["keel_help"].description
    assert "keel://reference/dsl" not in reference["resource_uri"]


def test_cli_help_unknown_topic_surfaces_known_topics_without_network():
    """A topic miss is instant and offline (Q-0573): the old fallback fired
    a doomed `GET /v1/reference/{topic}` (an endpoint that has never existed
    in any keel-api router) before failing. Any network call now fails this
    test outright."""

    def _no_network(*_a, **_kw):
        raise AssertionError("help topic miss must not make a network call")

    with patch("keel.client.KeelClient.get", side_effect=_no_network):
        result = runner.invoke(cli, ["--format", "json", "help", "zz_no_such_topic_zz"])
    # Exit 3 = not found per keel error codes
    assert result.exit_code == 3
    err_text = result.stderr if hasattr(result, "stderr") else result.output
    assert "not_found" in err_text or "Known topics" in err_text


def test_cli_help_normalizes_topic_separators_and_case():
    """`keel help dsl-syntax` / `DSL Syntax` hit the bundled snake_case doc
    (Q-0573: kebab-case guesses used to miss and 404 against the API)."""
    for guess in ("dsl-syntax", "DSL_SYNTAX", "dsl syntax"):
        result = runner.invoke(cli, ["--format", "json", "help", guess])
        assert result.exit_code == 0, (guess, result.output)
        data = json.loads(result.stdout)
        assert data["topic"] == "dsl_syntax"
        assert data["source"] == "bundled"
        assert len(data["body"]) > 100


def test_cli_help_miss_suggests_close_topics():
    """A near-miss names the closest bundled topics, not just the full list."""
    result = runner.invoke(cli, ["--format", "json", "help", "strategy-pattern"])
    assert result.exit_code == 3
    err_text = result.stderr if hasattr(result, "stderr") else result.output
    assert "Did you mean" in err_text
    assert "strategy_patterns" in err_text


def test_cli_help_bare_lists_topics():
    """`keel help` with no topic returns the list of bundled topics
    instead of raising missing_topic. v0.5.4-fix: agents reach for help
    to orient themselves; the bare call should be useful, not an error."""
    result = runner.invoke(cli, ["--format", "json", "help"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "topics" in data
    assert isinstance(data["topics"], list) and len(data["topics"]) > 0
    assert "info" in data


def test_keel_help_schema_topic_is_optional():
    from keel.tools.outcomes import OUTCOMES

    schema = OUTCOMES["keel_help"].input_schema
    assert "topic" not in schema["required"], (
        "topic must be optional — bare `keel_help` returns the topic list"
    )


def test_cli_accepts_format_at_subcommand_position():
    """`keel status --format json` must work (v0.4.2). Users reach for the
    subcommand-level flag first; the top-level form keeps working too."""
    result = runner.invoke(cli, ["status", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["share_url"] is None
    assert "tools_visible" in data


def test_cli_subcommand_format_overrides_top_level():
    """If both --format positions are used, the subcommand one wins."""
    result = runner.invoke(cli, ["--format", "human", "status", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "tools_visible" in data


def test_cli_agent_mode_requires_yes_for_destructive_commands(monkeypatch):
    def fail_delete(*_args, **_kwargs):
        raise AssertionError("handler should not run without --yes")

    monkeypatch.setattr("keel.client.KeelClient.delete", fail_delete)

    result = runner.invoke(
        cli,
        ["strategy", "delete", "str_abc", "--format", "json"],
        env={"KEEL_AGENT_MODE": "true"},
    )

    assert result.exit_code == 2
    data = json.loads(result.stderr)
    assert data["code"] == "cli_confirmation_required"
    assert "--yes" in data["what_was_expected"]
    assert data["example"]["args"]["strategy_id"] == "str_abc"


def test_cli_agent_mode_yes_allows_destructive_commands(monkeypatch):
    monkeypatch.setattr("keel.client.KeelClient.delete", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli,
        ["strategy", "delete", "str_abc", "--yes", "--format", "json"],
        env={"KEEL_AGENT_MODE": "true"},
    )

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["deleted"] is True
    assert data["run_id"] == "str_abc"


def test_cli_agent_mode_requires_yes_for_public_share():
    result = runner.invoke(
        cli,
        ["share", "create", "str_abc", "--format", "json"],
        env={"KEEL_AGENT_MODE": "true"},
    )

    assert result.exit_code == 2
    data = json.loads(result.stderr)
    assert data["code"] == "cli_confirmation_required"
    assert "shared publicly" in data["what_was_expected"]


def test_cli_agent_mode_allows_live_deploy_preview_without_yes(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def fake_post(_self, path, json=None, **_params):
        assert path == "/v1/deployments/preview"
        assert json == {"strategy_id": "str_abc"}
        return {"derived_schedule": "0 */4 * * *", "weights": []}

    monkeypatch.setattr("keel.client.KeelClient.post", fake_post)

    result = runner.invoke(
        cli,
        [
            "live",
            "deploy",
            "str_abc",
            "--account-id",
            "acct_1",
            "--direct",
            "--format",
            "json",
        ],
        env={"KEEL_AGENT_MODE": "true"},
    )

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["preview"]["strategy_id"] == "str_abc"
    assert data["next_action"]["args"]["confirmation_token"]


def test_cli_live_deploy_default_returns_web_handoff(monkeypatch, tmp_path):
    """D7: `keel live deploy <id>` (no --direct) hands the user to the web
    app — a handoff_required envelope, never an in-terminal deploy, and no
    account enumeration. No --yes needed (it's a link-out, not destructive)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def fake_post(_self, path, json=None, **_params):
        # Only the deploy-intent mint is allowed — never /v1/deployments[/preview].
        assert path == "/v1/deployments/deploy-intents", path
        return {
            "handoff_url": "https://app.usekeel.io/deploy?intent=tokC",
            "intent_token": "tokC",
        }

    def fail_get(*_args, **_kwargs):
        raise AssertionError("default handoff must not enumerate accounts")

    monkeypatch.setattr("keel.client.KeelClient.post", fake_post)
    monkeypatch.setattr("keel.client.KeelClient.get", fail_get)

    result = runner.invoke(
        cli,
        ["live", "deploy", "str_abc", "--format", "json"],
        env={"KEEL_AGENT_MODE": "true"},
    )

    assert result.exit_code == 6, result.stderr
    data = json.loads(result.stderr)
    assert data["code"] == "handoff_required"
    assert data["blocked_action"] == "live_deploy"
    assert data["action_url"] == "https://app.usekeel.io/deploy?intent=tokC"


def test_cli_agent_mode_requires_yes_for_actual_live_deploy(monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("handler should not run without --yes")

    monkeypatch.setattr("keel.client.KeelClient.post", fail_post)

    result = runner.invoke(
        cli,
        [
            "live",
            "deploy",
            "str_abc",
            "--account-id",
            "acct_1",
            "--direct",
            "--no-preview",
            "--confirmation-token",
            "tok_abc",
            "--format",
            "json",
        ],
        env={"KEEL_AGENT_MODE": "true"},
    )

    assert result.exit_code == 2
    data = json.loads(result.stderr)
    assert data["code"] == "cli_confirmation_required"
    assert "--yes" in data["what_was_expected"]


def test_cli_agent_mode_false_keeps_non_tty_script_compat(monkeypatch):
    monkeypatch.setattr("keel.client.KeelClient.delete", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli,
        ["strategy", "delete", "str_abc", "--format", "json"],
        env={"KEEL_AGENT_MODE": "false"},
    )

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["deleted"] is True


def test_cli_status_unauth_includes_next_hint_to_keel_auth_login(monkeypatch, tmp_path):
    """Unauthed status must tell the agent how to recover (v0.4.2)."""
    # CONFIG_FILE is module-level, so HOME alone won't unauth us — patch
    # the resolved path AND clear the env override.
    import keel.config as _config

    monkeypatch.setattr(_config, "CONFIG_FILE", tmp_path / "does-not-exist.yaml")
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    result = runner.invoke(cli, ["status", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["authenticated"] is False
    # ONE `next` string (spec 02 §2.5), the local profile's sign-in arm.
    assert data["next"] == (
        "Not signed in — keel_auth_login opens the browser sign-in "
        "(keel auth login from a terminal)."
    )


def test_keel_status_identity_reads_nested_me_shape(monkeypatch, tmp_path):
    """keel_account_status.identity must extract fields from the nested /v1/me shape.

    Regression — caught in v0.4.2 prod-readiness smoke. The handler used
    to read flat `me['principal_id']`/`me['org_id']`/`me['plan']` and
    silently return None for every field, even though the live response
    shape is `{principal: {id, ...}, org: {id, name, plan}, credential_scopes: [...]}`
    (same shape `_login_summary` reads). Both call sites must agree.
    """
    import keel.config as _config
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    # Force an "authed" path by stubbing get_identity directly and
    # supplying a non-empty api_key in the config.
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_key: dummy_test_key\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)

    me_payload = {
        "principal": {"id": "prn_abc123"},
        "org": {"id": "org_xyz789", "name": "Test Org", "plan": "trader"},
        "credential_scopes": ["strategy.read", "backtest.read"],
    }
    monkeypatch.setattr("keel.auth.get_identity", lambda: me_payload)
    # `keel_account_status` also runs a best-effort `GET /v1/entitlements` probe
    # once it believes it's authed. The dummy api_key above satisfies the
    # local auth precheck, so unstubbed that probe went to the PRODUCTION
    # API. Same stub the entitlements tests below use.
    monkeypatch.setattr(
        "keel.client.KeelClient.get",
        lambda self, path, **kw: {"balances": []},
    )

    tool = OUTCOMES["keel_account_status"]
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    env = tool.handler({}, ctx).to_envelope()

    assert "entitlements_error" not in env
    identity = env.get("identity") or {}
    assert identity["principal_id"] == "prn_abc123"
    assert identity["org_id"] == "org_xyz789"
    assert identity["org_name"] == "Test Org"
    assert identity["plan"] == "trader"
    assert identity["tier"] == "base"
    # No identity_error key when the call succeeds.
    assert "identity_error" not in env


def test_mcp_adapter_wraps_missing_required_in_spec13_envelope():
    """Required-arg validation must yield a spec §13.5 envelope, NOT
    a raw pydantic stacktrace. (v0.4.2 regression — pre-fix FastMCP's
    auto-pydantic layer raised upstream of our wrapper, so agents saw
    opaque text instead of structured suggested_next_action.)"""
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._mcp_adapter import _make_param_synthesized_handler

    # keel_strategy_fork has a single required arg `source`.
    tool = OUTCOMES["keel_strategy_fork"]
    fn = _make_param_synthesized_handler(tool, frozenset({"backtest"}))
    # Call with no args (missing required `source`). A view tool's error is
    # a ToolResult carrying the whole envelope as structuredContent (Q-1785)
    # and a text block led by the human message, ending in the envelope JSON
    # (spec 02 §2.2, R-27).
    result = fn()
    raw = result.content[0].text
    env = result.structured_content
    assert raw.startswith(env["message"])
    assert json.loads(raw[raw.index("\n{") + 1 :]) == env
    assert env["code"] == "usage_error"
    assert "missing required argument" in env["message"]
    assert "source" in env["message"]
    assert env["suggested_next_action"]["tool"] == "keel_strategy_fork"
    assert "source" in env["suggested_next_action"]["args"]


def test_synthesized_handler_does_not_use_var_kwargs():
    """FastMCP rejects functions with **kwargs. The synthesizer must NOT
    produce them — caught when an earlier v0.4.2 attempt added **_extra
    and the entire MCP server failed to register.

    Unknown arg names are handled by the registered tool object's
    validation wrapper, not by adding **kwargs to the synthesized
    function signature.
    """
    import inspect

    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._mcp_adapter import _make_param_synthesized_handler

    for name, tool in OUTCOMES.items():
        if tool.mcp_only:
            continue
        fn = _make_param_synthesized_handler(
            tool, frozenset({"always", "backtest", "read-only", "share"})
        )
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            assert param.kind != inspect.Parameter.VAR_KEYWORD, (
                f"{name}: synthesized handler must not use **kwargs (FastMCP rejects)"
            )
            assert param.kind != inspect.Parameter.VAR_POSITIONAL, (
                f"{name}: synthesized handler must not use *args (FastMCP rejects)"
            )


def test_mcp_tools_list_uses_declared_outcome_schema():
    """tools/list must show Keel's declared schema, not FastMCP's lossy
    inferred schema. Agents need required fields, descriptions, enums,
    formats, and strictness before they call the tool."""
    import asyncio

    from keel.mcp.server import create_server
    from keel.tools.outcomes import OUTCOMES

    async def go():
        s = create_server()
        tools = {t.name: t for t in await s.list_tools()}

        backtest = tools["keel_backtest_run"]
        expected = OUTCOMES["keel_backtest_run"].input_schema
        assert backtest.parameters["additionalProperties"] is False
        assert backtest.parameters["required"] == expected["required"]
        assert "end_date" not in backtest.parameters["required"]
        assert backtest.parameters["properties"]["start_date"]["format"] == "date"
        assert backtest.parameters["properties"]["end_date"]["format"] == "date"
        assert "description" in backtest.parameters["properties"]["strategy_id"]
        # CLI-only schema hints should not leak into MCP tools/list.
        assert "x-cli-positional" not in backtest.parameters["properties"]["strategy_id"]

        components = tools["keel_components_search"]
        category = components.parameters["properties"]["category"]
        assert "enum" in category and "indicator" in category["enum"]
        assert "description" in category

    asyncio.run(go())


def test_mcp_server_wraps_unknown_arguments_in_spec13_envelope():
    """Unknown MCP args should not leak raw FastMCP/Pydantic validation."""
    import asyncio

    from keel.mcp.server import create_server

    async def go():
        s = create_server()
        result = await s.call_tool("keel_strategy_fork", {"share": "str_abc"})
        env = result.structured_content
        assert result.content[0].text.startswith(env["message"])
        assert env["code"] == "usage_error"
        assert "unexpected argument" in env["message"]
        assert "share" in env["message"]
        assert env["suggested_next_action"]["tool"] == "keel_strategy_fork"

    asyncio.run(go())


def test_mcp_server_missing_required_still_uses_spec13_envelope():
    """Publishing `required` in tools/list must not let FastMCP reject
    missing args before Keel can return its structured recovery hint."""
    import asyncio

    from keel.mcp.server import create_server

    async def go():
        s = create_server()
        result = await s.call_tool("keel_strategy_fork", {})
        env = result.structured_content
        assert result.content[0].text.startswith(env["message"])
        assert env["code"] == "usage_error"
        assert "missing required argument" in env["message"]
        assert env["suggested_next_action"]["tool"] == "keel_strategy_fork"

    asyncio.run(go())


def test_mcp_adapter_passes_valid_args_through(monkeypatch):
    """Sanity — when args ARE valid, the wrapper invokes the handler."""
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._mcp_adapter import _make_param_synthesized_handler

    tool = OUTCOMES["keel_account_status"]
    fn = _make_param_synthesized_handler(tool, frozenset({"always", "backtest"}))
    result = fn()  # status has no required args
    # Until the non-view probe arm flips, status keeps the `{"result":
    # string}` wrapper old connectors hold (R-25): the envelope is a string.
    env = json.loads(result.structured_content["result"])
    # Should return the status envelope, not a usage_error
    assert env.get("code") != "usage_error"
    assert "api_url" in env
    # ...and the text block is the status view's markdown (spec 02 §2.5).
    assert result.content[0].text.startswith("**Keel**")


def test_keel_status_surfaces_entitlement_summary(monkeypatch, tmp_path):
    """Agents need a window into plan-limit usage BEFORE running bulk
    backtest sweeps. `keel_account_status` now fetches `/v1/entitlements` and
    surfaces the consumable units (backtest_runs, ai_messages,
    compute_seconds, live_strategies, eval_runs) in
    `entitlements.summary` so the agent can warn the user proactively
    if they're close to a cap. Carries no plan destination (D-12)."""
    import keel.config as _config
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_key: dummy_test_key\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)

    # Stub identity probe
    monkeypatch.setattr(
        "keel.auth.get_identity",
        lambda: {
            "principal": {"id": "prn_x"},
            "org": {"id": "org_x", "name": "Test", "plan": "starter"},
            "credential_scopes": ["strategy.read", "backtest.read"],
        },
    )

    # Stub the entitlements API response (mirrors live shape:
    # granted/spent/reserved/available).
    fake_balances = {
        "balances": [
            {
                "unit": "backtest_runs",
                "type": "consumable",
                "granted": 150,
                "spent": 25,
                "reserved": 0,
                "available": 125,
            },
            {
                "unit": "ai_messages",
                "type": "consumable",
                "granted": 50,
                "spent": 10,
                "reserved": 0,
                "available": 40,
            },
            {
                "unit": "live_strategies_max",
                "type": "cap",
                "granted": 3,
                "spent": 0,
                "reserved": 0,
                "available": 3,
            },
            {
                "unit": "backtest_compute_seconds",
                "type": "consumable",
                "granted": 10000,
                "spent": 1200,
                "reserved": 0,
                "available": 8800,
            },
            # Plus some unit we DON'T surface — verify it's filtered out
            {
                "unit": "feature:api_access",
                "type": "boolean",
                "granted": 1,
                "spent": 0,
                "reserved": 0,
                "available": 1,
            },
        ],
    }
    monkeypatch.setattr("keel.client.KeelClient.get", lambda self, path, **kw: fake_balances)

    tool = OUTCOMES["keel_account_status"]
    env = tool.handler({}, ToolContext(is_tty=False)).to_envelope()

    ent = env.get("entitlements")
    assert ent is not None, "keel_account_status must surface `entitlements`"
    # D-12: the caller's own units only — no plan destination. The key set
    # is exact, so a re-added `upgrade_url` (SEED, run 2026-09-28) reds here.
    assert set(ent) == {"summary"}, ent
    # hero_url is the strategies list, never `/settings` (billing by default).
    assert env["hero_url"] == "https://app.usekeel.io/strategies"
    assert "/settings" not in json.dumps(env)

    by_unit = {b["unit"]: b for b in ent["summary"]}
    # Consumable units surface
    assert "backtest_runs" in by_unit
    assert by_unit["backtest_runs"]["granted"] == 150
    assert by_unit["backtest_runs"]["spent"] == 25
    assert by_unit["backtest_runs"]["available"] == 125
    assert "unlimited" not in by_unit["backtest_runs"], "starter plan = 150 limit, NOT unlimited"
    # Boolean features filtered out
    assert "feature:api_access" not in by_unit


def test_keel_status_reports_the_reset_instant_when_the_server_sends_it(monkeypatch, tmp_path):
    """M1.3: the agent needs WHEN a unit refills, not just how much is
    left. Read verbatim from the balance; a balance without one is
    reported without one (control arm below) rather than with a guess.

    The link host comes from the environment too — it was a hardcoded
    prod literal, so a staging agent pointed users at production.
    """
    import keel.config as _config
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"api_key": "k", "api_url": "https://api.test", "refresh_token": null}')
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.setattr(
        "keel.auth.get_identity",
        lambda: {
            "principal": {"id": "prn_x"},
            "org": {"id": "org_x", "name": "Test", "plan": "free"},
            "credential_scopes": [],
        },
    )
    balances = {
        "balances": [
            {
                "unit": "backtest_runs",
                "type": "consumable",
                "granted": 50,
                "spent": 44,
                "reserved": 0,
                "available": 6,
                "period": "weekly",
                "resets_at": "2026-09-22T00:00:00Z",
                "seconds_to_reset": 24300,
            },
            # Control arm: same call, a unit the server sent no reset for.
            {
                "unit": "live_strategies_max",
                "type": "cap",
                "granted": 1,
                "spent": 0,
                "reserved": 0,
                "available": 1,
                "cap_current": 0,
            },
        ],
    }
    monkeypatch.setattr("keel.client.KeelClient.get", lambda self, path, **kw: balances)
    monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.tailf4d598.ts.net")

    ctx = ToolContext(is_tty=False, app_url="https://staging-app.tailf4d598.ts.net")
    env = OUTCOMES["keel_account_status"].handler({}, ctx).to_envelope()
    ent = env["entitlements"]
    by_unit = {b["unit"]: b for b in ent["summary"]}
    assert by_unit["backtest_runs"]["resets_at"] == "2026-09-22T00:00:00Z"
    assert by_unit["backtest_runs"]["period"] == "weekly"
    assert by_unit["backtest_runs"]["seconds_to_reset"] == 24300
    # Control: nothing invented for the unit the server said nothing about.
    assert "resets_at" not in by_unit["live_strategies_max"]
    assert "period" not in by_unit["live_strategies_max"]
    # D-12: no plan destination on any host (the staging arm of the test
    # above); the one app link follows the environment.
    assert "upgrade_url" not in ent
    assert env["hero_url"] == "https://staging-app.tailf4d598.ts.net/strategies"


def test_keel_status_annotates_each_unit_with_consumed_by_surfaces():
    """Each entitlement summary entry must carry `consumed_by` so the
    agent can answer "will doing X burn my Y quota?" without guessing
    from unit names. Critically: `ai_messages` must NOT list MCP/CLI
    as a consumer — it's the in-app chat at app.usekeel.io/chat only,
    and a `note` field must say so explicitly (so the agent doesn't
    tell the user "your MCP backtest will use one of your AI messages")."""
    import pytest
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    @pytest.fixture
    def _setup(monkeypatch, tmp_path):
        pass

    # Inline monkeypatching since we can't use pytest fixture in this signature
    from unittest.mock import patch

    # (pre-existing F841: the bound name was never read; dropping it is
    # what lets ruff's pre-commit gate pass on any commit touching this file)
    with patch(
        "keel.config.CONFIG_FILE", new=__import__("pathlib").Path("/tmp/_test_status_cfg.yaml")
    ):
        cfg_file = __import__("pathlib").Path("/tmp/_test_status_cfg.yaml")
        cfg_file.write_text("api_key: dummy\napi_url: https://api.usekeel.io\n")

        import os

        os.environ.pop("KEEL_API_KEY", None)

        with (
            patch(
                "keel.auth.get_identity",
                return_value={
                    "principal": {"id": "x"},
                    "org": {"id": "y", "plan": "free"},
                    "credential_scopes": [],
                },
            ),
            patch(
                "keel.client.KeelClient.get",
                return_value={
                    "balances": [
                        {
                            "unit": "backtest_runs",
                            "type": "consumable",
                            "granted": 30,
                            "spent": 5,
                            "reserved": 0,
                            "available": 25,
                        },
                        {
                            "unit": "ai_messages",
                            "type": "consumable",
                            "granted": 15,
                            "spent": 3,
                            "reserved": 0,
                            "available": 12,
                        },
                        {
                            "unit": "live_strategies_max",
                            "type": "cap",
                            "granted": 1,
                            "spent": 0,
                            "reserved": 0,
                            "available": 1,
                        },
                        {
                            "unit": "backtest_compute_seconds",
                            "type": "consumable",
                            "granted": 3000,
                            "spent": 280,
                            "reserved": 0,
                            "available": 2720,
                        },
                    ],
                },
            ),
        ):
            tool = OUTCOMES["keel_account_status"]
            env = tool.handler({}, ToolContext(is_tty=False)).to_envelope()

    by_unit = {b["unit"]: b for b in env["entitlements"]["summary"]}

    # Every surfaced unit must have consumed_by populated.
    for unit_name, entry in by_unit.items():
        assert "consumed_by" in entry, f"{unit_name} missing consumed_by annotation"
        assert isinstance(entry["consumed_by"], list)
        assert len(entry["consumed_by"]) > 0

    # ai_messages MUST NOT list MCP/CLI as a consumer.
    ai = by_unit["ai_messages"]
    consumed = " ".join(ai["consumed_by"]).lower()
    assert "mcp" not in consumed, (
        f"ai_messages consumed_by mentions MCP — would mislead agent. Got: {ai['consumed_by']}"
    )
    assert "cli" not in consumed, (
        f"ai_messages consumed_by mentions CLI — would mislead agent. Got: {ai['consumed_by']}"
    )
    # And the explicit note must call this out — generic host language,
    # NOT vendor-specific (no "Claude" / "OpenAI" mentions).
    assert "note" in ai
    assert "MCP" in ai["note"] and "CLI" in ai["note"], (
        "ai_messages note should explicitly mention MCP+CLI are NOT consumers"
    )
    # No vendor lock-in language.
    for vendor in ("Claude", "Anthropic", "OpenAI", "Cursor", "Windsurf"):
        assert vendor not in ai["note"], (
            f"ai_messages note mentions vendor '{vendor}' — keep it generic"
        )

    # backtest_runs MUST list MCP and CLI as consumers (the inverse).
    bt = by_unit["backtest_runs"]
    consumed_bt = " ".join(bt["consumed_by"]).lower()
    assert "mcp" in consumed_bt and "cli" in consumed_bt, (
        f"backtest_runs should list MCP + CLI as consumers. Got: {bt['consumed_by']}"
    )


def test_keel_status_marks_unlimited_for_int_max_grants(monkeypatch, tmp_path):
    """`granted == 2147483647` (INT_MAX sentinel for "unlimited" plan
    grants) must surface as `unlimited: True` so agents don't tell
    users they have "2147483647 backtest runs remaining"."""
    import keel.config as _config
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_key: dummy\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.setattr(
        "keel.auth.get_identity",
        lambda: {
            "principal": {"id": "x"},
            "org": {"id": "y", "plan": "trader"},
            "credential_scopes": [],
        },
    )
    monkeypatch.setattr(
        "keel.client.KeelClient.get",
        lambda self, path, **kw: {
            "balances": [
                {
                    "unit": "backtest_runs",
                    "type": "consumable",
                    "granted": 2147483647,
                    "spent": 0,
                    "reserved": 0,
                    "available": 2147483647,
                },
            ],
        },
    )

    tool = OUTCOMES["keel_account_status"]
    env = tool.handler({}, ToolContext(is_tty=False)).to_envelope()
    summary = env["entitlements"]["summary"]
    by_unit = {b["unit"]: b for b in summary}
    assert by_unit["backtest_runs"]["unlimited"] is True


def test_keel_status_identity_network_error_does_not_suggest_reauth(monkeypatch, tmp_path):
    """A non-auth identity probe failure (network blip, 5xx, parse error)
    must NOT add a `keel_auth_login` `next` hint or flip `authenticated`
    to false. v0.5.3-fix: the old except-Exception block told the agent
    `session likely expired; re-authenticate` for ANY exception, so a
    transient API outage produced a contradictory `authenticated: true`
    response that simultaneously suggested re-auth.
    """
    import keel.config as _config
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_key: dummy\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)

    def _network_fail():
        raise ConnectionError("simulated network blip")

    monkeypatch.setattr("keel.auth.get_identity", _network_fail)
    monkeypatch.setattr(
        "keel.client.KeelClient.get",
        lambda self, path, **kw: {"balances": []},
    )

    tool = OUTCOMES["keel_account_status"]
    env = tool.handler({}, ToolContext(is_tty=False)).to_envelope()

    assert env["authenticated"] is True, "non-auth errors must not flip authenticated"
    assert "identity_error" in env, "error must still surface for visibility"
    assert "next" not in env, (
        "non-auth identity errors must not add a re-auth next hint — "
        "doing so contradicts `authenticated: true` and confuses agents"
    )


def test_keel_status_identity_auth_error_suggests_reauth(monkeypatch, tmp_path):
    """An actual AuthError (401 from /v1/me) DOES suggest re-auth and
    flips `authenticated` to false — the stored tokens are real but the
    server rejected them, which is the legitimate re-login path."""
    import keel.config as _config
    from keel.errors import AuthError
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_key: dummy\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)

    def _auth_fail():
        raise AuthError("tokens rejected")

    monkeypatch.setattr("keel.auth.get_identity", _auth_fail)
    monkeypatch.setattr(
        "keel.client.KeelClient.get",
        lambda self, path, **kw: {"balances": []},
    )

    tool = OUTCOMES["keel_account_status"]
    env = tool.handler({}, ToolContext(is_tty=False)).to_envelope()

    assert env["authenticated"] is False, "AuthError must flip authenticated to false"
    assert "identity_error" in env
    assert "next" in env
    assert "keel_auth_login" in env["next"]


def test_keel_status_entitlements_probe_fails_soft(monkeypatch, tmp_path):
    """Entitlements outage must NOT block status — the failure
    surfaces in `entitlements_error` so the agent knows quota info
    isn't visible, but the rest of the status payload still works."""
    import keel.config as _config
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_key: dummy\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.setattr(
        "keel.auth.get_identity",
        lambda: {
            "principal": {"id": "x"},
            "org": {"id": "y", "plan": "free"},
            "credential_scopes": [],
        },
    )

    def _fail(self, path, **kw):
        raise RuntimeError("simulated entitlements outage")

    monkeypatch.setattr("keel.client.KeelClient.get", _fail)

    tool = OUTCOMES["keel_account_status"]
    env = tool.handler({}, ToolContext(is_tty=False)).to_envelope()
    # Status still returns
    assert env["authenticated"] is True
    assert "identity" in env
    # entitlements field absent; error surfaced
    assert "entitlements" not in env or not env.get("entitlements", {}).get("summary")
    assert "entitlements_error" in env


def test_keel_status_identity_marks_tier_live_with_runner_scope(monkeypatch, tmp_path):
    """credential_scopes containing 'runner.*' flips tier to live."""
    import keel.config as _config
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_key: dummy_test_key\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_file)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)

    me_payload = {
        "principal": {"id": "prn_x"},
        "org": {"id": "org_x", "name": "Test", "plan": "trader"},
        "credential_scopes": ["strategy.read", "runner.*"],
    }
    monkeypatch.setattr("keel.auth.get_identity", lambda: me_payload)
    # Stub the best-effort entitlements probe — the dummy api_key clears
    # the local auth precheck, so unstubbed it reached the PRODUCTION API.
    monkeypatch.setattr(
        "keel.client.KeelClient.get",
        lambda self, path, **kw: {"balances": []},
    )

    tool = OUTCOMES["keel_account_status"]
    env = tool.handler({}, ToolContext(is_tty=False)).to_envelope()

    assert env["identity"]["tier"] == "live"


# ─── MCP adapter ─────────────────────────────────────────────────────────


def test_mcp_server_registers_pilot_outcomes():
    """The MCP server should expose the always-loaded pilot trio."""
    import asyncio

    from keel.mcp.server import create_server

    async def go():
        s = create_server()
        tools = await s.list_tools()
        names = {t.name for t in tools}
        for required in ("keel_account_status", "keel_connection_check", "keel_help"):
            assert required in names, f"MCP missing outcome {required}"
        assert "keel_audit_replay" not in names

    asyncio.run(go())


def test_mcp_server_instructions_teach_skills_discovery():
    """Skills are METHOD, reached only through user-controlled channels
    (mcp-conversion 05 §3.2, R-L3 — "nothing we serve tells the model to
    fetch guidance"): the MCP prompts a user picks, and `keel_help`'s own
    catalogue (`topic="skills"`), which its description states. The
    instructions no longer carry a `skill:` pointer; the 2026-05-21 chat
    parity story's discovery need is met by the catalogue instead."""
    from keel.mcp.server import create_server
    from keel.tools.outcomes import OUTCOMES

    instr = create_server().instructions or ""
    assert "skill:" not in instr, "the instructions point the model at a skill"
    # The tools-only path to the pull tier and to the skill catalogue is
    # `keel_help` itself: its description names both, so a client that
    # never lists prompts or resources can still reach them.
    help_desc = OUTCOMES["keel_help"].description or ""
    assert '`topic="skills"` lists the agent skills' in help_desc
    assert "With no `topic` it lists the topic names" in help_desc
    # Non-vacuity: the instructions are the real assembly.
    assert "keel_account_status" in instr and len(instr) > 400


def test_mcp_server_instructions_teach_progressive_workflows():
    """Instructions should give generic agents a route before they pick
    lower-level tools from tools/list. Strategy routing comes from the
    corpus: the `sequence` section ("New strategy: …", agent-surface-cleanup
    spec 01 §2.2 — a FACT about the platform's order of operations; the
    discovery gate is the chat's opinion layer) replaced `LOOP` on
    2026-09-23; the full profiles add the plumbing blocks (AUTH, STATE,
    LIVE)."""
    from keel.mcp.server import create_server

    instr = create_server().instructions or ""
    # Corpus-provided strategy routing + full-profile plumbing blocks.
    for marker in ("New strategy:", "AUTH —", "STATE —", "LIVE —"):
        assert marker in instr, marker
    # The concrete tool chain agents follow.
    assert "keel_components_search" in instr
    assert "keel_components_get_many" in instr
    assert "keel_strategy_compose" in instr
    assert "keel_backtest_run" in instr
    # No skill pointer rides the instructions (mcp-conversion 05 §3.2).
    assert "skill:strategy-creation" not in instr


def test_mcp_server_exposes_knowledge_resources():
    """The bundled knowledge files (same set chat-api always-loads) must
    be reachable as MCP resources for direct fetch."""
    # `keel.data.knowledge` is the loader's owner; `keel.skills` used to
    # re-export it only because composing a skill appended these files.
    from keel.data.knowledge import load_section

    # Sanity — direct loader works for canonical sections
    for section in (
        "tool_usage",
        "mistakes",
        "reasoning_principles",
        "composition_mechanics",
        "dsl_syntax",
    ):
        text = load_section(section)
        assert len(text) > 100, f"knowledge section '{section}' suspiciously short"


def test_mcp_server_exposes_latest_backtest_resources(monkeypatch):
    """Agents should have a latest-run pointer without ad-hoc list loops."""
    import asyncio

    from keel.mcp.server import create_server

    calls = []

    def fake_get(_self, path, **params):
        calls.append((path, params))
        if path == "/v1/backtests":
            assert params == {"limit": 1, "strategy_id": "str_1"}
            return {
                "data": [
                    {
                        "id": "bt_1",
                        "strategy_id": "str_1",
                        "status": "completed",
                        "metrics": {"sharpe": 1.7},
                    }
                ],
                "pagination": {"cursor": None, "has_more": False},
            }
        if path == "/v1/backtests/bt_1/results":
            return {"presigned_url": "https://s3.example/results.json"}
        raise AssertionError(f"unexpected GET {path}")

    monkeypatch.setattr("keel.client.KeelClient.get", fake_get)
    monkeypatch.setattr("keel.client.KeelClient.close", lambda _self: None)

    async def go():
        s = create_server()
        resources = await s.list_resources()
        templates = await s.list_resource_templates()
        concrete_uris = {str(r.uri) for r in resources}
        uris = {t.uri_template for t in templates}
        assert "keel://backtest/latest" in concrete_uris
        assert "keel://strategy/{strategy_id}/backtest/latest" in uris

        result = await s.read_resource("keel://strategy/str_1/backtest/latest")
        body = json.loads(result.contents[0].content)
        assert body["found"] is True
        assert body["backtest_id"] == "bt_1"
        assert body["result_resource_uri"] == "keel://backtest/bt_1/results"
        assert body["results_available"] is True
        assert body["results"]["presigned_url"].startswith("https://s3.example/")

    asyncio.run(go())
    assert calls == [
        ("/v1/backtests", {"limit": 1, "strategy_id": "str_1"}),
        ("/v1/backtests/bt_1/results", {}),
    ]


def test_cli_components_describe_alias_works():
    """`keel components describe <name>` is the natural verb agents
    reach for. Canonical command is `keel components compose-help`
    (the MCP tool is misnamed; rename is a v0.5.0 breaking change).
    Until then, `describe` and `detail` are registered as aliases so
    agents aren't blocked by a guess-and-fail on verb name."""
    # The bundled registry is the data source under test; stub the
    # opportunistic `GET /v1/components/{name}` probe with the miss it
    # gets in reality (see `components_help._detail_via_api`). Unstubbed,
    # each of these three invocations hit the PRODUCTION API.
    with patch(
        "keel.client.KeelClient.get",
        side_effect=NotFoundError("no /v1/components endpoint in this deployment"),
    ):
        result = runner.invoke(cli, ["components", "describe", "ROC", "--format", "json"])
        result2 = runner.invoke(cli, ["components", "detail", "ROC", "--format", "json"])
        result3 = runner.invoke(cli, ["components", "compose-help", "ROC", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["name"] == "ROC"
    assert data["category"] == "indicator"

    # `detail` is the second alias.
    assert result2.exit_code == 0
    assert json.loads(result2.stdout)["name"] == "ROC"

    # Canonical name still works too — aliases don't replace it.
    assert result3.exit_code == 0
    assert json.loads(result3.stdout)["name"] == "ROC"


def test_strategy_compose_description_leaves_the_skill_route_to_the_instructions():
    """Q-1947 (2026-09-25): ChatGPT's approval gate read compose's skill
    pointer as the tool "specifying validation/help methods" and showed a
    Suspicious Instruction warning on every save. The route to the
    strategy-creation skill lives in the server instructions' head and in
    keel_help's description (test_listed_skill_routing pins both)."""
    from keel.tools.outcomes import OUTCOMES

    desc = OUTCOMES["keel_strategy_compose"].description
    assert "strategy-creation" not in desc
    # Non-vacuous: this is the compose description (its skeleton is present).
    assert "Declarations, then one Pipeline:" in desc
