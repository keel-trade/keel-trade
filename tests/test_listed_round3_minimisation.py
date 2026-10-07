"""Round-3 audit fixes on the listed results (Q-2268, 2026-10-01).

The final OpenAI app-review read-through (`scratchpad/audit3/`, drive results
under the prod flags) still found internal identifiers in listed results that
pass keel-api rows through — the strategy row (`org_id`, `s3_key`,
`source_hash`, `lock_hash`, `deployment_id`), its version rows (`meta`,
`client_name`, `auth_surface`), the search row's `owner`, the live deployment
row (`org_id`, `account_id`, `config_id`, `source_hash`), notes and Library
variants — plus copy that names the CLI (`render.surface_hints`, the
connection check's hint, `exit_code` on error envelopes, the internal-error
remedy) and a one-hour signed storage URL. Every arm below drives the real
handler with a fake keel-api whose rows carry TAINT values for each internal
key, on the LISTED profile and on the full profile (the control: the CLI and
local server are unchanged).

Proof it can fail (run 2026-10-01, each reverted by reversing the exact
edit): replacing `listed_strategy_metadata(meta) if listed else meta` with
`meta` in `strategy_get.py` reds the metadata arm on listed while its full
control stays green; the same for `_listed_versions`, the `pick(src, …)` of
the source, `with_owner` in `strategy_search.py`, `listed_live_data` in
`live_monitor.py`, `served_note`'s listed branch, `_served_variants`, the
`is_listed_profile()` gate in `_render.card_render_block`, `_wire_error`'s
pop, compose's listed `missing_input` branch, and the summarize/watch gates
(15 seeds, every one red on its listed arm, no `[full]` control red);
restoring "the full local toolset is on the Keel CLI" to `LISTED_LIVE_HINT`
reds the connection-check arm. The internal-error remedy is MCP-only copy,
host-neutral on BOTH profiles, so restoring "Run keel doctor to diagnose."
reds both of its arms. Emptying `_base.LISTED_REQUIRED_ADDITIONS` reds the
adapter arm here and the table test in test_server_profiles.py.

Non-vacuity: each arm first asserts the fake row really carried the tainted
keys into the FULL result (the control proves the taint reaches an
unfiltered result), and that the listed result still carries the facts the
cards read (name, status, version, P&L, `is_live`).
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap


_bootstrap()

_PROFILE_ENV = ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS")

#: The internal keys the round-3 audit grep counts (and a few of their kin).
INTERNAL = (
    "org_id",
    "principal_id",
    "account_id",
    "config_id",
    "lock_hash",
    "source_hash",
    "s3_key",
    "conversation_id",
    "client_name",
    "auth_surface",
)


def _taint(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: f"TAINT_{k}" for k in INTERNAL}
    out.update(
        {
            "source_conversation_id": "TAINT_cnv",
            "head_source_hash": "TAINT_hsh",
            "component_lock": {"ROC": 1},
        }
    )
    out.update(row)
    return out


def _leaks(env: Any) -> list[str]:
    blob = json.dumps(env, default=str)
    return [k for k in INTERNAL if k in blob] + (["TAINT"] if "TAINT" in blob else [])


@contextmanager
def _profile(profile: str):
    saved = {k: os.environ.get(k) for k in _PROFILE_ENV}
    for k in _PROFILE_ENV:
        os.environ.pop(k, None)
    if profile == "listed":
        os.environ.update(KEEL_SERVER_PROFILE="listed")
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeClient:
    def __init__(self, payloads: dict[str, Any]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(path)
        if path not in self.payloads:
            raise KeelError(f"no route {path}")
        return self.payloads[path]

    def post(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(path)
        return self.payloads.get(path, {})


def _run(tool: str, args: dict, payloads: dict[str, Any], profile: str) -> dict:
    client = _FakeClient(payloads)
    with _profile(profile):
        return (
            OUTCOMES[tool].handler(args, ToolContext(api_client=client, is_tty=False)).to_envelope()
        )


# ── keel_strategy_get ──────────────────────────────────────────────────

STRAT = _taint(
    {
        "strategy_id": "str_x",
        "id": "str_x",
        "name": "HYPE MACD trend",
        "description": "thesis",
        "status": "active",
        "current_sequence": 3,
        "head_commit_id": "cmt_3",
        "deployment_id": "dep_1",
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
        "latest_backtest_metrics": {"sharpe_ratio": 1.2},
    }
)
VERSIONS = {
    "data": [
        _taint(
            {
                "sequence_number": 3,
                "commit_id": "cmt_3",
                "message": "v3",
                "created_at": "2026-09-30T00:00:00Z",
                "tags": [],
                # keel-api's `meta` blob, and the raw provenance the rendered
                # `modified_via` sentence carries (round 2 keeps that).
                "meta": {"run_id": "TAINT_run", "actor": "TAINT_actor"},
                "client_name": "claude.ai",
                "auth_surface": "hosted-mcp",
            }
        )
    ],
    "pagination": {},
}
SOURCE = _taint({"source": "Pipeline([])\n", "sequence_number": 3, "commit_id": "cmt_3"})
STRATEGY_PAYLOADS = {
    "/v1/strategies/str_x": STRAT,
    "/v1/strategies/str_x/versions": VERSIONS,
    "/v1/strategies/str_x/versions/HEAD/source": SOURCE,
    "/v1/backtests": {"data": [], "pagination": {}},
}


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_strategy_get_metadata_versions_and_source_are_allow_listed_on_listed(profile):
    args = {
        "strategy_id": "str_x",
        "include_source": True,
        "include_versions": True,
        "skip_readiness": True,
    }
    env = _run("keel_strategy_get", args, STRATEGY_PAYLOADS, profile)
    meta, versions, source = env["metadata"], env["versions"], env["source"]
    if profile == "listed":
        from keel.tools.outcomes._listed_projection import (
            LISTED_SOURCE_FIELDS,
            LISTED_STRATEGY_METADATA_FIELDS,
            LISTED_VERSION_FIELDS,
        )

        assert set(meta) <= {*LISTED_STRATEGY_METADATA_FIELDS, "is_live"}, meta
        assert "deployment_id" not in meta and meta["is_live"] is True
        # The card's facts survive (non-vacuity).
        assert meta["name"] == "HYPE MACD trend" and meta["current_sequence"] == 3
        assert meta["latest_backtest_metrics"] == {"sharpe_ratio": 1.2}
        [row] = versions["data"]
        assert set(row) <= set(LISTED_VERSION_FIELDS), row
        assert row["commit_id"] == "cmt_3" and row["modified_via"] is not None
        assert set(source) <= set(LISTED_SOURCE_FIELDS) and source["source"]
        for block in (meta, versions, source):
            assert _leaks(block) == [], block
    else:
        assert meta == STRAT and versions == VERSIONS and source == SOURCE
        assert {"org_id", "s3_key", "source_hash", "lock_hash"} <= set(meta)


def test_strategy_get_listed_metadata_without_a_deployment_is_not_live():
    row = {k: v for k, v in STRAT.items() if k != "deployment_id"}
    env = _run(
        "keel_strategy_get",
        {"strategy_id": "str_x", "skip_readiness": True},
        {**STRATEGY_PAYLOADS, "/v1/strategies/str_x": row},
        "listed",
    )
    assert env["metadata"]["is_live"] is False


# ── keel_strategy_search ───────────────────────────────────────────────


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_strategy_search_rows_carry_no_owner_on_listed(profile):
    rows = {"data": [_taint({"strategy_id": "str_x", "name": "X", "updated_at": "t"})]}
    env = _run("keel_strategy_search", {}, {"/v1/strategies": rows}, profile)
    [row] = env["results"]
    assert row["strategy_id"] == "str_x" and row["name"] == "X"
    if profile == "listed":
        assert "owner" not in row and _leaks(env) == []
    else:
        assert row["owner"] == "TAINT_org_id"


# ── keel_live_monitor ──────────────────────────────────────────────────

DEP = _taint(
    {
        "deployment_id": "dep_1",
        "strategy_id": "str_x",
        "name": "HYPE MACD trend",
        "status": "running",
        "schedule": "1d",
        "deployed_version_string": "v3",
        "total_pnl": 12.3,
        "position_count": 2,
        "tranching": {"mode": "TAINT"},
    }
)
LIVE_PAYLOADS = {
    "/v1/deployments/dep_1": DEP,
    "/v1/deployments/dep_1/stats": _taint({"sharpe": 1.0}),
    "/v1/deployments/dep_1/equity": {"points": []},
    "/v1/deployments/dep_1/positions": _taint(
        {"account_value": 1000.0, "perp_positions": [_taint({"symbol": "BTC", "size": 0.1})]}
    ),
    "/v1/deployments/portfolio/summary": _taint(
        {"total_realized_pnl": 1.0, "active_count": 1, "total_count": 1, "deployments": [DEP]}
    ),
}


@pytest.mark.parametrize("profile", ["listed", "full"])
@pytest.mark.parametrize(
    "args,view",
    [
        ({"deployment_id": "dep_1"}, "overview"),
        ({"deployment_id": "dep_1", "view": "positions"}, "positions"),
        ({}, "portfolio"),
    ],
    ids=["overview", "positions", "portfolio"],
)
def test_live_monitor_rows_carry_no_internal_ids_on_listed(profile, args, view):
    env = _run("keel_live_monitor", args, LIVE_PAYLOADS, profile)
    assert env["view"] == view
    if profile == "listed":
        assert _leaks(env) == [], json.dumps(env, default=str)[:600]
        data = env["data"]
        if view == "overview":
            # What the live card draws survives (non-vacuity).
            assert data["name"] == "HYPE MACD trend" and data["status"] == "running"
            assert data["total_pnl"] == 12.3 and data["deployed_version_string"] == "v3"
            assert env["stats"] == {"sharpe": 1.0}
        elif view == "portfolio":
            assert data["deployments"][0]["deployment_id"] == "dep_1"
            assert data["active_count"] == 1
        else:
            assert data["perp_positions"][0]["symbol"] == "BTC"
    else:
        assert "TAINT_account_id" in json.dumps(env)


# ── keel_strategy_notes_read / keel_library_get ────────────────────────


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_notes_and_library_variants_are_allow_listed_on_listed(profile):
    notes = {"data": [_taint({"memory_id": "m1", "content": "baseline", "created_at": "t"})]}
    env = _run(
        "keel_strategy_notes_read",
        {"strategy_id": "str_x"},
        {"/v1/strategies/str_x/memory": notes},
        profile,
    )
    [note] = env["notes"]
    assert note["content"] == "baseline"
    assert "source_conversation_id" not in note  # every profile (Q-2268 round 2)
    lib = _taint({"slug": "ma", "name": "MA", "variants": [_taint({"variant_id": "fast"})]})
    lenv = _run("keel_library_get", {"slug": "ma"}, {"/v1/library/ma": lib}, profile)
    [variant] = lenv["variants"]
    assert variant["variant_id"] == "fast"
    if profile == "listed":
        assert _leaks(note) == [] and _leaks(variant) == []
    else:
        assert note["org_id"] == "TAINT_org_id" and variant["org_id"] == "TAINT_org_id"


# ── render hints, the connection-check hint, error envelopes ───────────


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_card_render_block_names_no_other_client_on_listed(profile):
    from keel.tools.outcomes._render import card_render_block

    with _profile(profile):
        block = card_render_block("strategy", fallback_url="https://app/x", ctx=ToolContext())
    assert block["card"] == "strategy" and block["fallback_url"] == "https://app/x"
    if profile == "listed":
        assert "surface_hints" not in block and "keel open" not in json.dumps(block)
    else:
        assert "keel open" in block["surface_hints"]["claude_code"]


def test_listed_connection_check_hint_names_no_cli(monkeypatch, tmp_path):
    import keel.config as _config

    cfg = tmp_path / "config.yaml"
    cfg.write_text("api_key: dummy_test_key\napi_url: https://api.usekeel.io\n")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    me = {"principal": {"id": "prn_1"}, "org": {"id": "org_1", "name": "O", "plan": "free"}}
    monkeypatch.setattr("keel.auth.get_identity", lambda: me)
    env = _run("keel_connection_check", {}, {"/v1/me": me}, "listed")
    hints = " ".join(env["surface_hints"])
    assert "keel_app_link" in hints  # non-vacuity: the hint is served
    for phrase in ("CLI", "keel open", "keel doctor", "pipx"):
        assert phrase not in hints, phrase


def _mcp_call(tool_name: str, profile: str, **kwargs: Any) -> dict:
    from keel.tools.outcomes._mcp_adapter import _make_handler

    with _profile(profile):
        handler = _make_handler(OUTCOMES[tool_name], frozenset())
        return json.loads(handler(**kwargs))


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_mcp_error_envelope_drops_exit_code_on_listed(profile):
    # An empty `source` passes the schema's `required` and reaches the
    # handler's own refusal — a KeelError through the adapter.
    env = _mcp_call("keel_strategy_compose", profile, source="")
    assert env["code"] == "missing_input" and env["retryable"] is False
    blob = json.dumps(env)
    if profile == "listed":
        assert "exit_code" not in env
        assert "source_file" not in blob, blob
    else:
        assert env["exit_code"] == 2 and "source_file" in blob


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_listed_output_schemas_declare_no_exit_code(profile):
    """The published schema matches the envelope: no listed kind declares
    `exit_code` (`LISTED_OUTPUT_OMISSIONS`); the full profile keeps it.
    Seed: drop `"exit_code"` from the strategy kind's omissions — reds."""
    from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS, output_schema_for

    assert len(OUTPUT_SCHEMAS) >= 10  # non-vacuity: every view tool is checked
    with _profile(profile):
        declared = {
            n: "exit_code" in (output_schema_for(n) or {}).get("properties", {})
            for n in OUTPUT_SCHEMAS
        }
    declared.pop("keel_account_status")  # published only under the non-view arm
    if profile == "listed":
        assert not any(declared.values()), declared
    else:
        assert all(declared.values()), declared


def test_listed_compose_requires_source_at_the_adapter():
    env = _mcp_call("keel_strategy_compose", "listed", dry_run=True)
    assert env["code"] == "usage_error" and "source" in env["message"]
    assert "source_file" not in json.dumps(env)


@pytest.mark.parametrize("profile", ["listed", "full"])
def test_internal_error_remedy_is_host_neutral(profile):
    from dataclasses import replace

    from keel.tools.outcomes._mcp_adapter import _make_handler

    def boom(args, ctx):
        raise RuntimeError("bug")

    tool = replace(OUTCOMES["keel_help"], handler=boom)
    with _profile(profile):
        env = json.loads(_make_handler(tool, frozenset())())
    assert env["code"] == "internal_error"
    reason = env["suggested_next_action"]["reason"]
    assert env["suggested_next_action"]["tool"] == "keel_connection_check"
    assert "keel doctor" not in reason and "keel_connection_check" in reason


# ── keel_backtest_summarize / keel_backtest_watch: no signed URL ───────

RUN = {
    "id": "btr_a",
    "status": "completed",
    "strategy_id": "str_x",
    "sequence_number": 3,
    "start_date": "2025-01-01",
    "end_date": "2026-01-01",
    "completed_at": "2026-01-02T00:00:00Z",
    "metrics": {"sharpe_ratio": 1.2, "total_return_pct": 10.0},
}
RESULTS = {"presigned_url": "https://bucket.s3.example/results.json?sig=TAINT", "expires_in": 3600}


@pytest.mark.parametrize("profile", ["listed", "full"])
@pytest.mark.parametrize("tool", ["keel_backtest_summarize", "keel_backtest_watch"])
def test_backtest_results_url_is_not_served_on_listed(profile, tool):
    payloads = {"/v1/backtests/btr_a": RUN, "/v1/backtests/btr_a/results": RESULTS}
    args = {"backtest_id": "btr_a", "skip_readiness": True}
    if tool == "keel_backtest_summarize":
        args.pop("skip_readiness")
    env = _run(tool, args, payloads, profile)
    if profile == "listed":
        assert "results_url" not in env and "results_url_expires_in_s" not in env
        assert "sig=" not in json.dumps(env, default=str)
        assert env["hero_url"].endswith("?tab=tearsheet")  # the link the user follows
    else:
        assert env["results_url"] == RESULTS["presigned_url"]
