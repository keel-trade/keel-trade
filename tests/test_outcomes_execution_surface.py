"""The execution-outcome tools (Q-0913, audit A8 F-7).

`keel_deployments_list`, `keel_live_receipt`, `keel_live_quality`,
`keel_accounts_safety`, and `keel_live_monitor`'s `expand_orders` /
`execution_run_id` — the reads that let an agent say why the last bar
traded nothing, what it cost, and whether the account is halted.

Payloads are shaped from prod rows read read-only on 2026-09-02 and cited
in `audit-2026-09-02/A8-surfaces.md`: customer deployment
`dep_01kzrcd41p52akm9azeaahe7j0` on account
`acc_01kzrc72y5aha44b7r75t2js2x`, run `ern_01m1fpn80j7n4ghzmh2xjqj6xj`
(11 episodes / 11 sealed receipts), and the halted run
`ern_01m0wtkz1kng3f31p4cs8b76e8` (halt event 572).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from keel.errors import EntitlementError, KeelError, ValidationError
from keel.tools.outcomes import OUTCOMES, _bootstrap, get
from keel.tools.outcomes._base import ToolContext


DEPLOYMENT_ID = "dep_01kzrcd41p52akm9azeaahe7j0"
ACCOUNT_ID = "acc_01kzrc72y5aha44b7r75t2js2x"
SESSION_ID = "exs_01m1fpn80e3b412yzdadnj5r9k"


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


# ─── keel_deployments_list ───────────────────────────────────────────────


def test_deployments_list_reads_the_canonical_endpoint():
    client = MagicMock()
    client.get.return_value = {
        "data": [
            {
                "deployment_id": DEPLOYMENT_ID,
                "strategy_id": "str_abc",
                "name": "Carry",
                "status": "LIVE",
                "account_id": ACCOUNT_ID,
                "deployed_version_string": "v3",
                "total_pnl": 12.34,
                "position_count": 11,
            }
        ],
        "pagination": {"cursor": "eyJvIjoyMH0", "has_more": True},
    }
    result = get("keel_deployments_list").handler({}, _ctx(client))

    client.get.assert_called_once_with("/v1/deployments")
    env = result.to_envelope()
    assert env["deployment_count"] == 1
    assert env["deployments"][0]["account_id"] == ACCOUNT_ID
    assert env["next_cursor"] == "eyJvIjoyMH0"
    assert env["hero_url"] == "https://app.usekeel.io/live"


def test_deployments_list_forwards_include_stopped_and_paging():
    client = MagicMock()
    client.get.return_value = {"data": [], "pagination": {}}
    get("keel_deployments_list").handler(
        {"include_stopped": True, "limit": 50, "cursor": "abc"}, _ctx(client)
    )
    client.get.assert_called_once_with(
        "/v1/deployments", limit=50, cursor="abc", include_stopped=True
    )


def test_deployments_list_omits_include_stopped_when_not_asked():
    """The endpoint's default is active-only; sending `false` changes nothing
    and would only make the request signature noisier."""
    client = MagicMock()
    client.get.return_value = {"data": [], "pagination": {}}
    get("keel_deployments_list").handler({"include_stopped": False}, _ctx(client))
    client.get.assert_called_once_with("/v1/deployments")


# ─── keel_live_receipt ───────────────────────────────────────────────────


def test_live_receipt_addresses_an_episode_not_a_run():
    client = MagicMock()
    client.get.return_value = {"receipt_id": "rcp_1", "outcome": "FILLED"}
    result = get("keel_live_receipt").handler(
        {"deployment_id": DEPLOYMENT_ID, "session_id": SESSION_ID}, _ctx(client)
    )

    client.get.assert_called_once_with(
        f"/v1/deployments/{DEPLOYMENT_ID}/executions/{SESSION_ID}/revisions/1/receipt"
    )
    env = result.to_envelope()
    assert env["session_id"] == SESSION_ID
    assert env["intent_rev"] == 1
    assert env["receipt"]["outcome"] == "FILLED"


def test_live_receipt_honours_intent_rev_and_version():
    client = MagicMock()
    client.get.return_value = {}
    get("keel_live_receipt").handler(
        {
            "deployment_id": DEPLOYMENT_ID,
            "session_id": SESSION_ID,
            "intent_rev": 3,
            "version": 2,
        },
        _ctx(client),
    )
    client.get.assert_called_once_with(
        f"/v1/deployments/{DEPLOYMENT_ID}/executions/{SESSION_ID}/revisions/3/receipt",
        version=2,
    )


def test_live_receipt_without_a_session_refuses_and_says_where_to_get_one():
    client = MagicMock()
    with pytest.raises(ValidationError) as excinfo:
        get("keel_live_receipt").handler({"deployment_id": DEPLOYMENT_ID}, _ctx(client))
    # The refusal must teach the per-episode address, which is the whole
    # point of A8 F-1 — a run does not name a receipt.
    assert "quality_summary" in excinfo.value.suggestion
    assert "per episode" in excinfo.value.suggestion.lower()
    client.get.assert_not_called()


# ─── keel_live_quality ───────────────────────────────────────────────────


def test_live_quality_reads_the_precomputed_rollup():
    client = MagicMock()
    client.get.return_value = {"rows": []}
    result = get("keel_live_quality").handler({"deployment_id": DEPLOYMENT_ID}, _ctx(client))
    client.get.assert_called_once_with(f"/v1/deployments/{DEPLOYMENT_ID}/execution-quality")
    assert result.to_envelope()["execution_quality"] == {"rows": []}


def test_live_quality_forwards_only_the_filters_that_were_set():
    client = MagicMock()
    client.get.return_value = {}
    get("keel_live_quality").handler(
        {
            "deployment_id": DEPLOYMENT_ID,
            "start_date": "2026-08-25",
            "end_date": "2026-09-02",
            "style": "",
            "style_version": None,
        },
        _ctx(client),
    )
    client.get.assert_called_once_with(
        f"/v1/deployments/{DEPLOYMENT_ID}/execution-quality",
        start_date="2026-08-25",
        end_date="2026-09-02",
    )


# ─── keel_accounts_safety ────────────────────────────────────────────────


def test_accounts_safety_reads_the_operator_route():
    client = MagicMock()
    client.get.return_value = {
        "safety": {"account_id": ACCOUNT_ID, "status": "ACTIVE", "current_halt_event_id": None},
        "nonterminal_frozen_sessions": 0,
        "unresolved_halt_cancels": 0,
        "unhalt_ready": False,
    }
    result = get("keel_accounts_safety").handler({"account_id": ACCOUNT_ID}, _ctx(client))
    client.get.assert_called_once_with(f"/internal/v1/execution-safety/{ACCOUNT_ID}")
    assert result.to_envelope()["execution_safety"]["safety"]["status"] == "ACTIVE"


def test_accounts_safety_403_is_truthful_and_never_suggests_re_login():
    """The operator gate is not a scope tier.

    The SDK's generic 403 translation says "re-login with a wider scope",
    which for this route is FALSE — `platform.execution_routing_operators`
    / `execution_oncall_operators` membership is the only key, and no
    login adds it. Telling an agent to re-authenticate here sends it into
    a loop it cannot exit.
    """
    client = MagicMock()
    generic = EntitlementError(
        "execution unhalt requires an allowlisted platform or on-call operator"
    )
    assert generic.recovery_tool == "keel_auth_login"  # the copy we must NOT inherit
    client.get.side_effect = generic

    with pytest.raises(KeelError) as excinfo:
        get("keel_accounts_safety").handler({"account_id": ACCOUNT_ID}, _ctx(client))

    err = excinfo.value
    assert err.error_code == "operator_only"
    assert "operator-only" in str(err)
    assert "re-authenticate" in err.suggestion
    assert "Do NOT retry" in err.suggestion
    # The server's own denial stays inspectable rather than being swallowed.
    assert "allowlisted platform or on-call operator" in err.input["server_denial"]
    assert err.__cause__ is generic


def test_accounts_safety_does_not_expose_the_unhalt_post():
    """Releasing a halt is an operator ceremony, deliberately absent here."""
    tool = get("keel_accounts_safety")
    assert tool.annotations["readOnlyHint"] is True
    assert set(tool.input_schema["properties"]) == {"account_id"}
    assert "unhalt" not in str(tool.input_schema).lower()
    assert not any("unhalt" in name for name in OUTCOMES)


def test_accounts_safety_without_an_account_refuses_before_calling():
    client = MagicMock()
    with pytest.raises(ValidationError):
        get("keel_accounts_safety").handler({}, _ctx(client))
    client.get.assert_not_called()


# ─── keel_live_monitor: expand_orders / execution_run_id ─────────────────


def test_executions_view_forwards_expand_orders():
    client = MagicMock()
    client.get.return_value = []
    get("keel_live_monitor").handler(
        {"deployment_id": DEPLOYMENT_ID, "view": "executions", "expand_orders": True, "limit": 5},
        _ctx(client),
    )
    client.get.assert_called_once_with(
        f"/v1/deployments/{DEPLOYMENT_ID}/executions", limit=5, expand_orders=True
    )


def test_executions_view_forwards_execution_run_id():
    client = MagicMock()
    client.get.return_value = []
    get("keel_live_monitor").handler(
        {
            "deployment_id": DEPLOYMENT_ID,
            "view": "executions",
            "execution_run_id": "ern_01m1fpn80j7n4ghzmh2xjqj6xj",
        },
        _ctx(client),
    )
    client.get.assert_called_once_with(
        f"/v1/deployments/{DEPLOYMENT_ID}/executions",
        execution_run_id="ern_01m1fpn80j7n4ghzmh2xjqj6xj",
    )


@pytest.mark.parametrize("view", ["trades", "orders", "overview"])
def test_expand_orders_is_not_forwarded_to_other_views(view):
    """It is an `/executions` query param; sending it elsewhere is a lie
    about what the endpoint accepts."""
    client = MagicMock()
    client.get.return_value = []
    get("keel_live_monitor").handler(
        {"deployment_id": DEPLOYMENT_ID, "view": view, "expand_orders": True},
        _ctx(client),
    )
    (_path,), kwargs = client.get.call_args
    assert "expand_orders" not in kwargs


def test_listed_schema_mirrors_the_new_executions_params():
    """The listed profile must accept the same arguments as the full one."""
    tool = get("keel_live_monitor")
    shared = tool.input_schema["properties"]
    listed = tool.listed_input_schema["properties"]
    assert set(shared) == set(listed)
    for key in ("expand_orders", "execution_run_id"):
        assert shared[key]["type"] == listed[key]["type"]
        assert shared[key].get("default") == listed[key].get("default")


# ─── The CLI surface end to end ──────────────────────────────────────────


_EXECUTIONS_PAYLOAD = [
    {
        "attempt_id": "ern_01m1fpn80j7n4ghzmh2xjqj6xj",
        "attempt_kind": "execution",
        "execution_run_id": "ern_01m1fpn80j7n4ghzmh2xjqj6xj",
        "execution_status": "completed",
        "activity_at": "2026-09-02T00:00:18.456058+00:00",
        "weight_count": 11,
        "order_count": 16,
        "filled_count": 6,
        "rejected_count": 0,
        "skipped_count": 1,
        "avg_slippage_bps": -0.641077210475534,
        "avg_slippage_lane": "SESSION_ARRIVAL_LEDGER_PRICE_ONLY",
        "slippage_measured_sessions": 5,
        "quality_episode_count": 11,
        "quality_sealed_receipt_count": 11,
        "quality_provisional_count": 0,
        "session_outcomes": {
            "FILLED/target_filled": 5,
            "SKIPPED/NO_DELTA": 4,
            "SKIPPED/BELOW_MIN_TRADE": 2,
        },
    }
]


def _envelope_block(output: str, key: str) -> str:
    """The lines the human envelope printed under one top-level key.

    A block ends at the next column-0 `key:` line — that is exactly how
    `_cli_adapter._render` lays the envelope out, so this reads the real
    boundary rather than guessing at which sibling key comes next.
    """
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{key}:"))
    block = []
    for line in lines[start + 1 :]:
        if line and not line[0].isspace() and re.match(r"^[a-z_]+:", line):
            break
        block.append(line)
    assert block, f"envelope key {key!r} printed no block"
    return "\n".join(block)


def test_cli_executions_human_output_carries_no_raw_json():
    """The end-to-end shape of the Q-0913 fix on the command line."""
    from keel.cli.main import cli

    with patch("keel.client.KeelClient.get", return_value=_EXECUTIONS_PAYLOAD):
        result = CliRunner().invoke(
            cli,
            [
                "live",
                "monitor",
                DEPLOYMENT_ID,
                "--view",
                "executions",
                "--expand-orders",
                "--format",
                "human",
            ],
        )

    assert result.exit_code == 0, result.output
    # Scope to the timeline block. The envelope's `freshness` metadata is
    # an unregistered field and keeps this surface's long-standing
    # indented-JSON fallback on purpose — the finding is about the
    # EXECUTION fields, and narrowing here keeps the assertion honest
    # about what changed.
    assert "\ndata:\n" in result.output, result.output
    rows = _envelope_block(result.output, "data")
    assert '{"' not in rows, rows
    assert '": ' not in rows, rows
    assert "11 sessions" in rows
    assert "SKIPPED/BELOW_MIN_TRADE 2" in rows
    assert "SESSION_ARRIVAL_LEDGER_PRICE_ONLY" in rows
    assert "5 sessions measured" in rows
    assert "no single receipt" in rows


def test_cli_registers_every_new_verb():
    """`keel --help` reaches all five commands the audit asked for."""
    from keel.cli.main import cli

    expected = {
        ("deployments", "list"): "keel_deployments_list",
        ("live", "receipt"): "keel_live_receipt",
        ("live", "quality"): "keel_live_quality",
        ("accounts", "safety"): "keel_accounts_safety",
        ("live", "monitor"): "keel_live_monitor",
    }
    for path, tool_name in expected.items():
        command = cli
        for part in path:
            command = command.commands[part]
        assert command is not None, f"`keel {' '.join(path)}` is not registered"
        assert OUTCOMES[tool_name].cli_path == path

    # A new CLI group must describe itself rather than render blank.
    assert cli.commands["deployments"].help


def test_new_tools_are_default_visible_and_carry_an_action():
    """A hosted tool with no `required_action` is invisible in tools/list
    and answers `tool_not_found` (services/mcp-server/src/scope_gate.py)."""
    from keel.tools.outcomes._toolsets import is_tool_loaded, load_toolsets

    toolsets = load_toolsets()
    for name in (
        "keel_deployments_list",
        "keel_live_receipt",
        "keel_live_quality",
        "keel_accounts_safety",
    ):
        tool = OUTCOMES[name]
        assert tool.required_action, f"{name} has no required_action"
        assert is_tool_loaded(tool.toolset, toolsets), f"{name} is not default-visible"
        assert not tool.local_only
