"""`keel_app_link` (spec 01 R4) — navigation URL correctness.

Pure URL construction per id type, config-driven URL bases
(ToolContext defaults = prod; KEEL_APP_URL / KEEL_SHARE_URL_ROOT env
override on hosted deployments), instructive errors on unknown ids,
and presence on both server profiles.
"""

from __future__ import annotations

import json

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()

TOOL = OUTCOMES["keel_app_link"]


def _run(args: dict, **ctx_kwargs):
    return TOOL.handler(args, ToolContext(**ctx_kwargs))


# ─── URL correctness per id type ────────────────────────────────────────


def test_strategy_id_links_to_the_editor():
    """V-6, ratified 2026-09-19: a strategy link lands in the editor —
    where the work happens — and `/strategies/{id}` redirects there. The
    `target_kind` wire label is unchanged."""
    result = _run({"id": "str_01ABC"})
    assert result.hero_url == "https://app.usekeel.io/strategies/str_01ABC/edit"
    assert result.extra["url"] == result.hero_url
    assert result.extra["target_kind"] == "strategy_overview"


def test_backtest_id_links_to_results_tearsheet():
    result = _run({"id": "btr_01XYZ"})
    assert result.hero_url == "https://app.usekeel.io/backtests/btr_01XYZ?tab=tearsheet"
    assert result.extra["target_kind"] == "backtest_results"


def test_share_id_links_to_public_share_page():
    """A REAL share id (Q-2263): `share_links._generate_share_id` mints
    `secrets.token_urlsafe(16)[:21]` — an unprefixed 21-character token, the
    value `keel_share_create` returns as `share_id`. Until 2026-10-01 the tool
    refused every one of them as an unknown prefix.

    # SEED (run 2026-10-01): in open_in_app.py make `_SHARE_TOKEN_RE` require
    # 22 characters — this reds; the legacy and control arms stay green.
    """
    result = _run({"id": "gDXjURKqWPs8CZ4eXdqAI"})
    assert result.hero_url == "https://usekeel.io/share/gDXjURKqWPs8CZ4eXdqAI"
    assert result.extra["target_kind"] == "share_page"
    # Tokens carry `-` and `_` too.
    result = _run({"id": "a-b_C9dEfGhIjKlMnOpQr"})
    assert result.hero_url == "https://usekeel.io/share/a-b_C9dEfGhIjKlMnOpQr"


def test_legacy_shr_prefix_still_links_to_the_share_page():
    result = _run({"id": "shr_01PUB"})
    assert result.hero_url == "https://usekeel.io/share/shr_01PUB"
    assert result.extra["target_kind"] == "share_page"


def test_a_token_of_the_wrong_length_is_not_mistaken_for_a_share():
    """Control arms: platform ids are `<kind>_<ulid>` (30 chars) and nothing
    else of another length routes anywhere."""
    for bad in ("gDXjURKqWPs8CZ4eXdqA", "gDXjURKqWPs8CZ4eXdqAIx", "abc_01HZXK3Y6N3V7Q9S2T4W8R5M1E"):
        with pytest.raises(KeelError) as exc_info:
            _run({"id": bad})
        assert exc_info.value.error_code == "unknown_id_prefix", bad


def test_share_url_stays_null_navigation_is_not_publication():
    """share_create is the single tool with a non-null share_url; a
    navigation link must not look like a publication event."""
    for target in ("str_1", "btr_1", "shr_1"):
        assert _run({"id": target}).share_url is None


def test_url_bases_come_from_server_config():
    result = _run(
        {"id": "str_01ABC"},
        app_url="https://staging-app.tailf4d598.ts.net",
        share_url_root="https://staging.usekeel.io/share",
    )
    assert result.hero_url == "https://staging-app.tailf4d598.ts.net/strategies/str_01ABC/edit"
    result = _run(
        {"id": "shr_01PUB"},
        share_url_root="https://staging.usekeel.io/share",
    )
    assert result.hero_url == "https://staging.usekeel.io/share/shr_01PUB"


def test_id_is_trimmed():
    result = _run({"id": "  str_01ABC  "})
    assert result.hero_url == "https://app.usekeel.io/strategies/str_01ABC/edit"


# ─── Errors ─────────────────────────────────────────────────────────────


def test_missing_id_is_instructive():
    with pytest.raises(KeelError) as exc_info:
        _run({})
    envelope = exc_info.value.to_envelope()
    assert envelope["code"] == "missing_id"
    assert "str_" in json.dumps(envelope)


def test_a_deployment_id_links_to_the_running_strategy_page():
    """`keel_live_monitor`'s listed copy says `keel_app_link` returns the link
    for a deployment; until Q-2273 L6 the `dep_` prefix was refused as
    unknown, so that hint ended in an error."""
    result = _run({"id": "dep_01HZXK3Y6N3V7Q9S2T4W8R5M1E"})
    assert result.hero_url == "https://app.usekeel.io/live/dep_01HZXK3Y6N3V7Q9S2T4W8R5M1E"
    assert result.extra["target_kind"] == "live_view"


def test_every_id_prefix_a_listed_description_routes_here_resolves():
    """Every `xxx_` id a listed tool's copy sends to `keel_app_link` is one it
    accepts — the hint and the router cannot drift apart again."""
    import os
    import re

    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._mcp_adapter import effective_description
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    _bootstrap()
    saved = os.environ.get("KEEL_SERVER_PROFILE")
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    try:
        text = effective_description(OUTCOMES["keel_app_link"])
    finally:
        if saved is None:
            os.environ.pop("KEEL_SERVER_PROFILE", None)
        else:
            os.environ["KEEL_SERVER_PROFILE"] = saved
    prefixes = set(re.findall(r"`([a-z]{3}_)\.\.\.`", text))
    assert prefixes >= {"str_", "btr_", "dep_"}, prefixes
    for prefix in prefixes:
        assert _run({"id": prefix + "01HZXK3Y6N3V7Q9S2T4W8R5M1E"}).hero_url
    assert "keel_live_monitor" in LISTED_PROFILE_TOOLS


def test_unknown_prefix_is_instructive_not_a_guess():
    with pytest.raises(KeelError) as exc_info:
        _run({"id": "abc_123"})
    envelope = exc_info.value.to_envelope()
    assert envelope["code"] == "unknown_id_prefix"
    assert "keel_strategy_search" in json.dumps(envelope)


def test_no_api_call_ever(monkeypatch):
    """Navigation must work with no client and no network."""

    def _boom(self):  # pragma: no cover — must not be called
        raise AssertionError("keel_app_link must never construct an API client")

    monkeypatch.setattr(ToolContext, "get_client", _boom)
    assert _run({"id": "str_1"}).hero_url.endswith("/strategies/str_1/edit")


# ─── Adapter env overrides (hosted server config path) ──────────────────


def test_adapter_env_overrides_reach_the_tool(monkeypatch):
    from keel.tools.outcomes._mcp_adapter import _make_handler

    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.tailf4d598.ts.net")
    monkeypatch.setenv("KEEL_SHARE_URL_ROOT", "https://staging.usekeel.io/share")
    handler = _make_handler(TOOL, frozenset())

    env = json.loads(handler(id="str_9"))
    assert env["hero_url"] == "https://staging-app.tailf4d598.ts.net/strategies/str_9/edit"
    env = json.loads(handler(id="shr_9"))
    assert env["hero_url"] == "https://staging.usekeel.io/share/shr_9"


# ─── Registration surface ───────────────────────────────────────────────


def test_tool_is_on_both_profiles(monkeypatch):
    from keel.tools.outcomes._mcp_adapter import loaded_tool_names
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    assert "keel_app_link" in LISTED_PROFILE_TOOLS

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    for profile in ("full", "listed"):
        monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
        assert "keel_app_link" in loaded_tool_names(OUTCOMES), profile


def test_cli_command_registers():
    """`keel app open <id>` is the CLI face of the same outcome (one
    surface, both channels — CLI/MCP parity rule)."""
    import click
    from keel.tools.outcomes._cli_adapter import register_all as cli_register_all

    root = click.Group("keel")
    cli_register_all(root, OUTCOMES)
    assert "app" in root.commands
    assert "open" in root.commands["app"].commands


def test_policy_vetted_description():
    """R4's exact description language must stay put (research/08)."""
    assert TOOL.description.startswith(
        "Returns a link to view and manage this strategy in the Keel web app."
    )
    assert TOOL.annotations["readOnlyHint"] is True
    assert TOOL.annotations["title"] == "Get App Link"


# ─── Spec 09 review finding: anon sessions get no org-resource links ────


def test_anon_session_blocks_org_resource_links(monkeypatch):
    """While anonymous, strategy/backtest links dead-end at sign-in with a
    wrong-account 404 — the tool points at the claim instead (CL-2)."""
    monkeypatch.setattr("keel.tools.outcomes._handoff._is_anon_session", lambda: True)
    for target in ("str_01ABC", "btr_01XYZ"):
        with pytest.raises(KeelError) as exc:
            _run({"id": target})
        assert exc.value.error_code == "anon_no_app_link"
        assert "keel_auth_login" in (exc.value.suggestion or "")


def test_anon_session_share_links_still_work(monkeypatch):
    """Public share pages are org-independent — anon sessions keep them."""
    monkeypatch.setattr("keel.tools.outcomes._handoff._is_anon_session", lambda: True)
    result = _run({"id": "shr_01PUB"})
    assert result.hero_url == "https://usekeel.io/share/shr_01PUB"
