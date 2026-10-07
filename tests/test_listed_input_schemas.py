"""The listed input schemas omit what a listed caller cannot use (R-8;
spec 01 §2.5, spec 03 §2.8 — guard 16's SDK half).

Under `KEEL_SERVER_PROFILE=listed` the SERVED `keel_backtest_run` schema has
no `auto_push`, `push_message`, `config.leverage` or `config.initial_capital`,
and `keel_strategy_compose` has no `source_file`; under `full` all of them
are served, with `leverage` worded as the margin cap it is.

SEED (run 2026-09-23, reverted by reversing the edit): serve the full schema
on listed (`effective_input_schema` returns `tool.input_schema`) —
`test_the_served_listed_schema_omits_the_write_through_and_margin_parameters`
reds while `test_the_full_schema_keeps_every_parameter` (CONTROL) stays green.

The spec 04 nested policy-scan arm (L5's `test_policy_scan.py`) uses the
PRE-change listed schema as its seed; this file does not duplicate it.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_run as _run_mod  # noqa: F401 — registers
from keel.tools.outcomes import strategy_compose as _compose_mod  # noqa: F401 — registers
from keel.tools.outcomes._mcp_adapter import effective_input_schema


@pytest.fixture
def listed(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")


@pytest.fixture
def full(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")


def _config_props(schema: dict) -> dict:
    return schema["properties"]["config"]["properties"]


def test_the_served_listed_schema_omits_the_write_through_and_margin_parameters(listed):
    run = effective_input_schema(OUTCOMES["keel_backtest_run"])
    assert "auto_push" not in run["properties"]
    assert "push_message" not in run["properties"]
    config = _config_props(run)
    assert "leverage" not in config
    assert "initial_capital" not in config
    # Non-vacuity (a quantity the seed cannot move): the nested config the
    # scan reads still carries the three parameters a listed caller sets.
    assert {"init_cash", "fees", "slippage"} <= set(config)
    compose = effective_input_schema(OUTCOMES["keel_strategy_compose"])
    assert "source_file" not in compose["properties"]
    assert "source_file" not in compose["properties"]["source"]["description"]


def test_the_full_schema_keeps_every_parameter(full):
    """CONTROL: the CLI and the local server serve everything."""
    run = effective_input_schema(OUTCOMES["keel_backtest_run"])
    assert {"auto_push", "push_message", "version", "commit_id"} <= set(run["properties"])
    config = _config_props(run)
    assert {"leverage", "initial_capital", "init_cash"} <= set(config)
    assert "source_file" in effective_input_schema(OUTCOMES["keel_strategy_compose"])["properties"]


def test_version_is_served_on_both_profiles(listed):
    assert "version" in effective_input_schema(OUTCOMES["keel_backtest_run"])["properties"]


def _call_through_a_real_server(name: str, arguments: dict) -> tuple[list[dict], str]:
    """(args the handler saw, the text block) for one `tools/call` through a
    real FastMCP registration — the synthesized signature, FastMCP's own
    validation and the adapter pre-flight all run; only the handler is fake."""
    import asyncio
    import dataclasses

    from fastmcp import FastMCP
    from keel.tools.outcomes._base import OutcomeResult
    from keel.tools.outcomes._mcp_adapter import register_all

    seen: list[dict] = []

    def fake(args, ctx):
        seen.append(dict(args))
        return OutcomeResult(run_id="bt_fake", extra={"strategy_id": args.get("strategy_id")})

    server = FastMCP("frozen-catalog")
    register_all(server, {name: dataclasses.replace(OUTCOMES[name], handler=fake)})
    result = asyncio.run(server.call_tool(name, arguments))
    return seen, "".join(getattr(c, "text", "") for c in result.content)


def test_a_frozen_catalog_argument_is_accepted_and_dropped_on_listed(listed, monkeypatch):
    """Review 2 (lane A): an old ChatGPT connector's FROZEN catalog still
    sends `auto_push` / `push_message` / `source_file`. The listed server
    keeps them out of the published schema but must not refuse the call.

    SEED (run 2026-09-23, reverted by reversing the edit): make
    `_base.listed_ignored_arguments` return `frozenset()` — this reds with
    `usage_error: unexpected argument(s) auto_push, push_message` while the
    full-profile CONTROL below stays green.
    """
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "local")
    seen, text = _call_through_a_real_server(
        "keel_backtest_run",
        {"strategy_id": "str_frozen", "auto_push": True, "push_message": "old catalog"},
    )
    assert "usage_error" not in text and "unexpected argument" not in text, text
    assert len(seen) == 1 and seen[0]["strategy_id"] == "str_frozen"
    assert not {"auto_push", "push_message"} & set(seen[0])
    seen, text = _call_through_a_real_server(
        "keel_strategy_compose",
        {"source": "x = 1", "name": "frozen", "source_file": "/tmp/strategy.py"},
    )
    assert "unexpected argument" not in text, text
    assert seen and "source_file" not in seen[0]
    # Still out of the published schema (the reason the omission exists).
    assert "auto_push" not in effective_input_schema(OUTCOMES["keel_backtest_run"])["properties"]


def test_a_truly_unknown_argument_is_still_refused_on_listed(listed, monkeypatch):
    """The accept-list is exactly the omissions — anything else still reds."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "local")
    seen, text = _call_through_a_real_server(
        "keel_backtest_run", {"strategy_id": "str_frozen", "not_a_param": 1}
    )
    assert seen == [] and "unexpected argument" in text, text


def test_the_full_profile_passes_write_through_arguments_to_the_handler(full, monkeypatch):
    """CONTROL: the local server serves `auto_push` and hands it on."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "local")
    seen, _ = _call_through_a_real_server(
        "keel_backtest_run", {"strategy_id": "str_full", "auto_push": True}
    )
    assert len(seen) == 1 and seen[0]["auto_push"] is True
