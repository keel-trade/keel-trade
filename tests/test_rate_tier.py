"""One owner for the hosted rate tier (Q-6789; research-lab spec 07 §5.2, AT-12).

``OutcomeTool.rate_tier`` is the only place a tool's hosted rate tier is
declared; mcp-server reads it from this registry. A hostable tool that is not
read-only must declare a write tier or the SDK refuses to import.

Proof it can fail (recorded in the Q-6789 commit): deleting
``rate_tier="mutation"`` from ``keel_screener_action``'s declaration makes the
registry import raise, so every test here that bootstraps it reds.
"""

from __future__ import annotations

import pytest

from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import RATE_TIERS, OutcomeResult, OutcomeTool


#: Spec 07 §5.2's reviewed map, whole: every hostable write tool and its tier.
EXPECTED_WRITE_TIERS: dict[str, str] = {
    # backtest: starts compute
    "keel_backtest_run": "backtest",
    "keel_screener_run": "backtest",
    "keel_paper_deploy": "backtest",
    # live: real capital
    "keel_live_deploy": "live",
    "keel_live_control": "live",
    "keel_live_update": "live",
    # mutation: writes a stored record
    "keel_strategy_compose": "mutation",
    "keel_strategy_fork": "mutation",
    "keel_strategy_delete": "mutation",
    "keel_strategy_notes_add": "mutation",
    "keel_share_create": "mutation",
    "keel_feedback": "mutation",
    "keel_library_fork": "mutation",
    "keel_paper_control": "mutation",
    "keel_screener_action": "mutation",
    "keel_screener_follow": "mutation",
    "keel_screener_publish": "mutation",
    "keel_screener_refresh": "mutation",
    "keel_screener_unpublish": "mutation",
    "keel_screener_view_set": "mutation",
    "keel_strategy_restore": "mutation",
    "keel_strategy_upgrade": "mutation",
    "keel_use_detach": "mutation",
    "keel_use_upgrade": "mutation",
    # Research declares its own tiers (07 §5.2): run is backtest; view_set,
    # note, cancel and make_signal are mutation; the rest are read.
    "keel_research_run": "backtest",
    "keel_research_view_set": "mutation",
    "keel_research_note": "mutation",
    "keel_research_cancel": "mutation",
    "keel_research_make_signal": "mutation",
}

#: Write tools that are never hosted, and so declare no tier (exempt by name).
EXEMPT_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # local_only: the user's own machine
        "keel_auth_login",
        "keel_auth_logout",
        "keel_strategy_checkout",
        "keel_strategy_discard",
        "keel_strategy_pull",
        "keel_strategy_push",
        # cli_only: no MCP registration on any profile
        "keel_screener_copy",
        "keel_screener_describe",
        "keel_screener_follow_view_set",
        # Research's CLI-only writes (07 §2.2)
        "keel_research_save",
        "keel_research_run_draft",
        "keel_research_resume",
        "keel_research_retry",
        "keel_research_copy",
        "keel_research_project",
        "keel_research_set_aside",
        "keel_research_restore",
        "keel_research_archive",
        "keel_research_delete",
    }
)


def _registry() -> dict[str, OutcomeTool]:
    _bootstrap()
    return OUTCOMES


def _is_write(tool: OutcomeTool) -> bool:
    return tool.annotations.get("readOnlyHint") is not True


def test_hostable_write_tools_carry_the_reviewed_tiers():
    tools = _registry()
    hostable_writes = {n: t for n, t in tools.items() if t.hostable and _is_write(t)}
    # Not vacuous: the registry is populated and the sweep selected the
    # 24 hostable write tools spec 07 §5.2 counted, plus Research's five.
    assert len(tools) > len(hostable_writes) > 0
    assert len(hostable_writes) == 24 + 5 == len(EXPECTED_WRITE_TIERS)
    assert {n: t.rate_tier for n, t in hostable_writes.items()} == EXPECTED_WRITE_TIERS


def test_every_hostable_tool_resolves_a_tier_and_no_write_resolves_read():
    tools = _registry()
    hostable = [t for t in tools.values() if t.hostable]
    assert len(hostable) > 24
    for tool in hostable:
        assert tool.rate_tier in RATE_TIERS, tool.name
        assert (tool.rate_tier == "read") == (not _is_write(tool)), tool.name


def test_never_hosted_writes_are_exactly_the_exempt_set():
    tools = _registry()
    exempt = {n for n, t in tools.items() if not t.hostable and _is_write(t)}
    assert exempt == EXEMPT_WRITE_TOOLS
    assert all(tools[n].rate_tier is None for n in exempt)


def _tool(**overrides) -> OutcomeTool:
    kwargs = dict(
        name="keel_rate_tier_probe",
        cli_path=("rate", "probe"),
        toolset="backtest",
        description="Probe.",
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={"title": "Probe", "readOnlyHint": False},
        handler=lambda args, ctx: OutcomeResult(),
    )
    kwargs.update(overrides)
    return OutcomeTool(**kwargs)


def test_a_read_only_tool_with_no_tier_resolves_read():
    """The control arm: a read-only tool needs no declaration."""
    tool = _tool(annotations={"title": "Probe", "readOnlyHint": True})
    assert tool.rate_tier == "read"


def test_a_hostable_write_with_no_tier_raises():
    with pytest.raises(ValueError, match="must declare rate_tier"):
        _tool()


def test_a_hostable_write_declaring_read_raises():
    with pytest.raises(ValueError, match="must declare rate_tier"):
        _tool(rate_tier="read")


def test_an_unknown_tier_raises():
    with pytest.raises(ValueError, match="is not one of"):
        _tool(rate_tier="bulk")


@pytest.mark.parametrize("flag", ["local_only", "cli_only"])
def test_a_never_hosted_write_needs_no_tier(flag):
    assert _tool(**{flag: True}).rate_tier is None


def test_a_declared_write_tier_is_kept():
    assert _tool(rate_tier="backtest").rate_tier == "backtest"
