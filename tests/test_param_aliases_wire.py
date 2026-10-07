"""A renamed parameter's OLD name, and a RETIRED argument, over the wire (Q-2267).

Round 1 (Q-2262 / Q-2264) renamed `no_ownership_hint` → `skip_readiness` and
removed `keel_strategy_search`'s `tag` / `owner` / `share_id`. The round-2
functionality check reproduced four regressions on the frozen-catalog path,
all of which pass `_make_handler` in isolation and fail only through FastMCP:

* `no_ownership_hint=true` was silently IGNORED over the wire — FastMCP's
  synthesized signature hands the handler `skip_readiness=False` (the schema
  default) on every call, so `args.setdefault(new, args.pop(old))` kept the
  default and dropped the caller's value (`_mcp_adapter._make_handler`);
* `keel_strategy_status` had no alias row at all;
* the CLI lost `--no-ownership-hint` with no alias;
* a frozen Claude / ChatGPT catalog still sending `tag` / `owner` /
  `share_id` got `usage_error` (`_toolsets.RETIRED_ARGUMENTS`).

Every arm here drives the REAL registered tool through `fastmcp.Client` (the
wire handler, the synthesized signature, the adapter) or through Click, on
BOTH servers the one codebase runs, with the Keel API replaced at
`KeelClient.get` / `.post` by a path recorder. The verdict is behavioural:
the readiness read (`GET /v1/strategy-work`) is made or not made.

Proof it can fail (run 2026-10-01, each reverted by reversing the edit):

* `args[new_name] = args.pop(old_name)` back to `args.setdefault(...)` —
  every `old_name` arm of `test_old_parameter_name_is_honoured_over_the_wire`
  reds (the readiness read is made), the `new_name` and `default` control
  arms stay green;
* drop the `"default"` strip in `_make_param_synthesized_handler`'s alias
  row — the `new_name` arms red (the absent old flag's False overrides the
  caller's `skip_readiness=True`);
* delete the `keel_strategy_status` row of `PARAM_ALIASES` —
  `test_strategy_status_has_an_alias_row` and its wire arm red;
* delete the `keel_strategy_search` row of `RETIRED_ARGUMENTS` —
  `test_retired_search_arguments_are_accepted_and_dropped` reds with
  `usage_error`, the control arm (an argument that was never an input) stays
  red-as-expected;
* remove the alias loop from `_cli_adapter` — the `--no-ownership-hint` CLI
  arms red.

Non-vacuity: every arm asserts the recorder saw the primary read (the tool
ran), and the control arms prove the readiness read IS made when nothing
asks to skip it.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import click
import pytest
from click.testing import CliRunner
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import (
    LISTED_PROFILE_TOOLS,
    PARAM_ALIASES,
    RETIRED_ARGUMENTS,
)


_bootstrap()

_PROFILE_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_LISTED_CLIENT",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)
API = "https://api.test.usekeel.io"
READINESS = "/v1/strategy-work"


@contextmanager
def _profile(profile: str):
    saved = {k: os.environ.get(k) for k in _PROFILE_ENV}
    for k in _PROFILE_ENV:
        os.environ.pop(k, None)
    token = None
    if profile == "hosted-listed":
        from keel.hosting import bind_request_credentials

        os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
        token = bind_request_credentials(token="tok_test", api_url=API)
    else:
        os.environ.update(
            KEEL_TOOLSETS="read-only,backtest,share,live-read,live-write",
            KEEL_API_KEY="test-key",
            KEEL_API_URL=API,
        )
    try:
        yield
    finally:
        if token is not None:
            from keel.hosting import clear_request_credentials

            clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_COMPLETED_RUN = {
    "id": "btr_1",
    "backtest_run_id": "btr_1",
    "strategy_id": "str_x",
    "status": "completed",
    "commit_id": "cmt_1",
    "start_date": "2025-01-01",
    "end_date": "2025-06-01",
    "metrics": {"sharpe_ratio": 1.2, "total_return_pct": 10.0, "max_drawdown_pct": -5.0},
    "completed_at": "2026-09-30T00:01:00Z",
}


WORKSPACE_STATUS = "workspace:status"


def _recorder():
    """`(paths, get, post, status)` — a Keel API that answers the minimum
    each tool needs and records every path, plus the local workspace read
    `keel_strategy_status` makes (recorded under `WORKSPACE_STATUS`)."""
    paths: list[str] = []

    def get(self, path, **_kw):
        paths.append(path)
        if path == "/v1/strategies/str_x":
            return {"strategy_id": "str_x", "id": "str_x", "name": "X", "current_sequence": 1}
        if path == "/v1/backtests/btr_1":
            return dict(_COMPLETED_RUN)
        if path == "/v1/backtests":
            return {"data": [], "pagination": {}}
        if path == "/v1/strategies":
            return {"items": [], "next_cursor": None}
        if path == READINESS:
            return {"overall_status": "not_started", "missing_evidence": []}
        return {}

    def post(self, path, **_kw):
        paths.append(path)
        if path == "/v1/backtests":
            return {"id": "btr_1", "backtest_run_id": "btr_1", "status": "queued"}
        return {}

    def status(**_kw):
        paths.append(WORKSPACE_STATUS)
        return {"strategy_id": "str_x", "state": "current", "sequence": 1}

    return paths, get, post, status


#: (tool, the primary read that proves the tool ran, the call's other args)
_ALIASED_CALLS = {
    "keel_strategy_get": ("/v1/strategies/str_x", {"strategy_id": "str_x"}),
    "keel_backtest_watch": ("/v1/backtests/btr_1", {"backtest_id": "btr_1"}),
    "keel_backtest_run": ("/v1/backtests", {"strategy_id": "str_x", "wait": False}),
    "keel_strategy_status": (WORKSPACE_STATUS, {"strategy_id": "str_x"}),
}


async def _call(name: str, args: dict):
    from fastmcp import Client
    from keel.mcp.server import create_server

    async with Client(create_server()) as client:
        return await client.call_tool(name, args, raise_on_error=False)


def _text(result) -> str:
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    if result.structured_content is not None:
        text += "\n" + json.dumps(result.structured_content, default=str)
    return text


def _wire(profile: str, name: str, args: dict) -> tuple[list[str], str, bool]:
    paths, get, post, status = _recorder()
    with (
        _profile(profile),
        mock.patch("keel.client.KeelClient.get", get),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.workspace.status", status),
    ):
        result = asyncio.run(_call(name, args))
    return paths, _text(result), bool(result.is_error)


def _arms():
    for tool_name, renames in sorted(PARAM_ALIASES.items()):
        for old, new in sorted(renames.items()):
            for profile in ("hosted-listed", "local-full"):
                if profile == "hosted-listed" and tool_name not in LISTED_PROFILE_TOOLS:
                    continue
                for sent in ("old_name", "new_name", "default"):
                    yield pytest.param(
                        profile, tool_name, old, new, sent, id=f"{profile}-{tool_name}-{sent}"
                    )


@pytest.mark.parametrize("profile,tool_name,old,new,sent", list(_arms()))
def test_old_parameter_name_is_honoured_over_the_wire(profile, tool_name, old, new, sent):
    primary, base_args = _ALIASED_CALLS[tool_name]
    args = dict(base_args)
    if sent == "old_name":
        args[old] = True
    elif sent == "new_name":
        args[new] = True
    paths, text, is_error = _wire(profile, tool_name, args)
    assert not is_error and "usage_error" not in text, text[:400]
    assert primary in paths, f"the tool never ran: {paths}"  # non-vacuity
    if sent == "default":
        # Control arm: nothing asked to skip, so the readiness read is made.
        assert READINESS in paths, paths
    else:
        assert READINESS not in paths, f"{sent}={True} was ignored: {paths}"


def test_the_arms_cover_every_alias_row_on_both_servers():
    """Non-vacuity for the parametrisation itself: every `PARAM_ALIASES`
    row has a wire arm, and the listed server has an arm for each row it
    serves."""
    ids = {p.id for p in _arms()}
    for tool_name in PARAM_ALIASES:
        assert f"local-full-{tool_name}-old_name" in ids
        if tool_name in LISTED_PROFILE_TOOLS:
            assert f"hosted-listed-{tool_name}-old_name" in ids
    assert len(ids) >= 3 * len(PARAM_ALIASES)


def test_strategy_status_has_an_alias_row():
    """Round 1 renamed the parameter on four tools and aliased three."""
    assert PARAM_ALIASES["keel_strategy_status"] == {"no_ownership_hint": "skip_readiness"}
    assert "skip_readiness" in OUTCOMES["keel_strategy_status"].input_schema["properties"]
    assert set(PARAM_ALIASES) == {
        "keel_strategy_get",
        "keel_strategy_status",
        "keel_backtest_run",
        "keel_backtest_watch",
    }


# ── Retired arguments ───────────────────────────────────────────────────


@pytest.mark.parametrize("profile", ["hosted-listed", "local-full"])
def test_retired_search_arguments_are_accepted_and_dropped(profile):
    """A frozen catalog's `tag` / `owner` / `share_id` are dropped without a
    word on every profile; the search still runs on `query`. Control: an
    argument that was never an input is still refused."""
    retired = RETIRED_ARGUMENTS["keel_strategy_search"]
    assert retired == {"tag", "owner", "share_id"}
    assert not retired & set(OUTCOMES["keel_strategy_search"].input_schema["properties"])

    args = {"query": "Mo", "tag": "t1", "owner": "org_1", "share_id": "shr_9"}
    paths, text, is_error = _wire(profile, "keel_strategy_search", args)
    assert not is_error and "usage_error" not in text, text[:400]
    assert "/v1/strategies" in paths  # non-vacuity: the search ran

    paths, text, is_error = _wire(profile, "keel_strategy_search", {"query": "Mo", "zz_never": 1})
    assert "usage_error" in text and "zz_never" in text, text[:400]
    assert "/v1/strategies" not in paths


def test_retired_arguments_are_absent_from_every_published_schema():
    from keel.mcp.server import create_server

    for profile in ("hosted-listed", "local-full"):
        with _profile(profile):
            tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
        for tool_name, retired in RETIRED_ARGUMENTS.items():
            props = set((tools[tool_name].parameters or {}).get("properties", {}))
            assert not (props & retired), (profile, tool_name, sorted(props & retired))
            assert props, (profile, tool_name)  # non-vacuity


# ── The CLI's hidden alias flag ─────────────────────────────────────────


def _cli_group() -> click.Group:
    from keel.tools.outcomes._cli_adapter import register_all

    root = click.Group()
    register_all(root, OUTCOMES)
    return root


@pytest.mark.parametrize(
    "command,flag_then_args",
    [
        (("strategy", "get"), ["str_x"]),
        (("strategy", "status"), ["str_x"]),
        (("backtest", "run"), ["str_x", "--no-wait"]),
        (("backtest", "watch"), ["btr_1"]),
    ],
    ids=["strategy-get", "strategy-status", "backtest-run", "backtest-watch"],
)
@pytest.mark.parametrize("flag", ["--no-ownership-hint", "--skip-readiness", None])
def test_cli_keeps_the_old_flag_as_a_hidden_alias(command, flag_then_args, flag):
    paths, get, post, status = _recorder()
    argv = [*command, *flag_then_args, "--format", "json"]
    if flag:
        argv.append(flag)
    with (
        _profile("local-full"),
        mock.patch("keel.client.KeelClient.get", get),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.workspace.status", status),
        mock.patch("keel.workspace.get_workspace", return_value=None),
    ):
        result = CliRunner().invoke(_cli_group(), argv)
    assert result.exit_code == 0, result.output
    assert paths, "the command never reached the API"  # non-vacuity
    if flag is None:
        assert READINESS in paths, paths
    else:
        assert READINESS not in paths, (flag, paths)


def test_cli_help_shows_the_new_flag_and_hides_the_old_one():
    result = CliRunner().invoke(_cli_group(), ["strategy", "get", "--help"])
    assert result.exit_code == 0, result.output
    assert "--skip-readiness" in result.output
    assert "--no-ownership-hint" not in result.output


# ── Listed-only alias: a frozen catalog's `commit_id` pins the run ─────────
# The listed schema keeps one version selector (`version`); a catalog frozen
# before that still sends `commit_id`. Dropping it ran HEAD instead of the
# pinned commit (final functionality check, 2026-10-01). Seed: delete the
# `keel_backtest_run` row of `LISTED_PARAM_ALIASES` — the hosted arm reds
# (no pin reaches POST /v1/backtests) while the local-full control, which
# sends `commit_id` as itself, stays green.


def _backtest_bodies(profile: str, args: dict) -> tuple[list[dict], str]:
    bodies: list[dict] = []
    paths, get, _post, status = _recorder()

    def post(self, path, json=None, **_kw):
        paths.append(path)
        if path == "/v1/backtests":
            bodies.append(dict(json or {}))
            return {"id": "btr_1", "backtest_run_id": "btr_1", "status": "queued"}
        return {}

    with (
        _profile(profile),
        mock.patch("keel.client.KeelClient.get", get),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.workspace.status", status),
    ):
        result = asyncio.run(_call("keel_backtest_run", args))
    return bodies, _text(result)


def test_listed_commit_id_is_rewritten_to_version_not_dropped():
    from keel.tools.outcomes._toolsets import LISTED_PARAM_ALIASES

    assert LISTED_PARAM_ALIASES["keel_backtest_run"] == {"commit_id": "version"}
    bodies, text = _backtest_bodies(
        "hosted-listed", {"strategy_id": "str_x", "commit_id": "cmt_1", "wait": False}
    )
    assert len(bodies) == 1, text[:400]  # non-vacuous: the run was submitted
    assert bodies[0].get("version") == "cmt_1" and "commit_id" not in bodies[0]


def test_full_profile_still_sends_commit_id_as_itself():
    bodies, text = _backtest_bodies(
        "local-full", {"strategy_id": "str_x", "commit_id": "cmt_1", "wait": False}
    )
    assert len(bodies) == 1, text[:400]
    assert bodies[0].get("commit_id") == "cmt_1" and "version" not in bodies[0]


def test_listed_commit_id_stays_out_of_the_published_schema():
    from keel.mcp.server import create_server

    with _profile("hosted-listed"):
        tools = asyncio.run(create_server().list_tools())
    props = next(t for t in tools if t.name == "keel_backtest_run").parameters["properties"]
    assert "commit_id" not in props and "version" in props
