"""Published enums and date formats are enforced (Q-2273 L5).

The round-5 audit sent `present: "huge"`, `kind: "rant"`,
`category: "nonsense"` and `start_date: "2025-13-45"` to listed tools: each
was accepted and treated as a default (or forwarded), because the adapter
types an enum as `str` and nothing read `format`. `register()` now wraps
every handler with `_declared_choices.enforce_declared_choices`.

Arms:

* EVERY top-level enum parameter of EVERY registered tool (both schemas): a
  bogus value is refused before the handler runs — `usage_error`, or the
  code the handler already used for that parameter (`OWNED_CODES`) —
  naming the parameter and the value (non-vacuous: the sweep counts its
  parameters and names the audit's four);
* CONTROL: every published value of every enum passes, in both spellings
  `LISTED_ENUM_RENAMES` accepts;
* `format: date`: impossible dates and other spellings refused, a real date
  passes;
* WIRE: `keel_backtest_run` with `start_date: "2025-13-45"` on the hosted
  listed server is an error result and calls keel-api zero times.

Seed (2026-10-01, reverted by reversing the edit): `register()` no longer
wrapping the handler → the sweep, date and wire arms red, the control arm
green.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._declared_choices import OWNED_CODES, choice_violation


_bootstrap()


def _enum_params() -> list[tuple[str, str]]:
    out = []
    for name, tool in sorted(OUTCOMES.items()):
        keys = []
        for schema in (tool.input_schema, tool.listed_input_schema or {}):
            for key, spec in (schema.get("properties") or {}).items():
                if "enum" in spec and key not in keys:
                    keys.append(key)
        out += [(name, key) for key in keys]
    return out


ENUMS = _enum_params()


def test_the_sweep_covers_the_published_enums():
    assert len(ENUMS) >= 12
    for expected in [
        ("keel_backtest_run", "present"),
        ("keel_feedback", "kind"),
        ("keel_components_search", "category"),
        ("keel_live_monitor", "view"),
        ("keel_share_create", "permission"),
    ]:
        assert expected in ENUMS
    assert set(OWNED_CODES) - set(ENUMS) <= {
        ("keel_backtest_summarize", "start"),
        ("keel_backtest_summarize", "end"),
    }


@pytest.mark.parametrize(("tool_name", "param"), ENUMS)
def test_a_value_outside_the_enum_is_refused_before_the_handler(tool_name, param):
    tool = OUTCOMES[tool_name]
    with pytest.raises(KeelError) as exc:
        tool.handler({param: "not_a_value_q2273"}, ToolContext(is_tty=False))
    expected = OWNED_CODES.get((tool_name, param), ("UsageError", "usage_error"))[1]
    assert exc.value.error_code == expected, (tool_name, param, str(exc.value))
    assert f"`{param}`" in str(exc.value) and "not_a_value_q2273" in str(exc.value)


@pytest.mark.parametrize(("tool_name", "param"), ENUMS)
def test_control_every_published_value_passes(tool_name, param):
    tool = OUTCOMES[tool_name]
    values = []
    for schema in (tool.input_schema, tool.listed_input_schema or {}):
        values += ((schema.get("properties") or {}).get(param) or {}).get("enum") or []
    assert values
    for value in values:
        assert choice_violation(tool, {param: value}) is None, value


@pytest.mark.parametrize(
    "bad", ["2025-13-45", "2025-02-30", "yesterday", "2025/01/01", "20250101", "2025-1-1"]
)
@pytest.mark.parametrize(
    ("tool_name", "param", "code"),
    [
        ("keel_backtest_run", "start_date", "usage_error"),
        ("keel_backtest_run", "end_date", "usage_error"),
        ("keel_backtest_summarize", "start", "invalid_slice_bound"),
    ],
)
def test_a_malformed_date_is_refused(tool_name, param, code, bad):
    with pytest.raises(KeelError) as exc:
        OUTCOMES[tool_name].handler({param: bad}, ToolContext(is_tty=False))
    assert exc.value.error_code == code
    assert "YYYY-MM-DD" in str(exc.value)


def test_control_a_real_date_passes():
    assert choice_violation(OUTCOMES["keel_backtest_run"], {"start_date": "2025-02-28"}) is None


def test_the_listed_view_spelling_is_echoed_and_the_published_enum_listed(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    refusal = choice_violation(OUTCOMES["keel_live_monitor"], {"view": "trade-history"})
    assert refusal is not None and refusal.error_code == "validation_failed"
    message = str(refusal)
    assert "'trade-history'" in message and "`trade_history`" in message
    assert "`trades`" not in message and "`orders`" not in message


def test_a_bad_start_date_never_reaches_keel_api_over_the_wire():
    from unittest import mock

    from keel.hosting import bind_request_credentials, clear_request_credentials

    calls: list = []

    def record(self, method, path, **_kw):
        calls.append((method, path))
        return {}

    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool(
                "keel_backtest_run",
                {"strategy_id": "str_x", "start_date": "2025-13-45", "wait": False},
                raise_on_error=False,
            )

    keys = ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
    token = bind_request_credentials(token="tok", api_url="https://api.choices.test")
    try:
        with mock.patch("keel.client.KeelClient._request", record):
            result = asyncio.run(run())
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert result.is_error
    assert calls == []
