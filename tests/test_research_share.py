"""keel_share_create shares a study (Research 07 §2.2, 08 §3.6).

A `stu_` id infers the study target; the share carries `views` and
`show_author` and never the source, so `include_source`, `permission` and
`expires_at` are refused before anything is published. The listed schema does
not change (the two study arguments are listed omissions), and a strategy or
backtest share refuses the study arguments.

SEED (reverted by reversing the edit): drop the `stu_` arm from
``_infer_target_type``; the study arms red with ``ambiguous_target``.

RI-281: the CLI sends a boolean flag the caller left off as its default
(`--include-source/--no-include-source` and `--show-author/--no-show-author`
both arrive as False), so J10.2's confirmed `keel share create stu_… --views 0
--views 3 --yes` was refused ("include_source cannot be set") and every CLI
strategy, backtest and paper share was refused ("`views` and `show_author` are
for a study share"). A value equal to an argument's default now asks for
nothing; a value that asks for the source is still refused in the same words.
The CLI arms drive the real `keel` entry; the control arms pin main's request
bodies for the other kinds (scratch matrix: 72 CLI and MCP cases byte-identical
to main).
"""

from __future__ import annotations

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()
TOOL = OUTCOMES["keel_share_create"]


class _Client:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post(self, path, json=None, **params):
        self.posts.append((path, json))
        return {
            "share_id": "S" * 23,
            "study_id": "stu_a",
            "version": 2,
            "url": "https://usekeel.io/s",
            "views": json.get("views") or [0, 1],
            "show_author": json["show_author"],
        }


def test_a_study_id_shares_the_snapshot() -> None:
    client = _Client()
    result = TOOL.handler({"target_id": "stu_a", "views": [0, 3]}, ToolContext(api_client=client))
    assert client.posts == [
        ("/v1/research/studies/stu_a/shares", {"show_author": False, "views": [0, 3]})
    ]
    env = result.to_envelope()
    assert env["share_type"] == "study" and env["share_url"] == "https://usekeel.io/s"


@pytest.mark.parametrize(
    "arg",
    [{"include_source": True}, {"permission": "fork"}, {"expires_at": "2027-01-01T00:00:00Z"}],
)
def test_a_study_share_refuses_the_source_fields(arg) -> None:
    client = _Client()
    with pytest.raises(KeelError) as caught:
        TOOL.handler({"target_id": "stu_a", **arg}, ToolContext(api_client=client))
    assert caught.value.error_code == "invalid_arguments" and not client.posts


def test_a_strategy_share_refuses_the_study_arguments() -> None:
    client = _Client()
    with pytest.raises(KeelError):
        TOOL.handler({"target_id": "str_a", "views": [0]}, ToolContext(api_client=client))
    assert not client.posts


def test_the_listed_schema_does_not_change() -> None:
    from keel.tools.outcomes._base import listed_schema

    listed = listed_schema("keel_share_create", TOOL.input_schema)["properties"]
    assert "views" not in listed and "show_author" not in listed
    assert {"views", "show_author"} <= set(TOOL.input_schema["properties"])  # not vacuous


# ── RI-281: the CLI's defaults never trip a refusal ─────────────────────────

FUTURE = "2099-01-01T00:00:00Z"


def _cli(monkeypatch, *argv: str):
    """`keel share create …` through the real CLI entry, as J10.2 runs it (agent
    mode, confirmed with --yes), the Keel API faked at KeelClient: exit code,
    the POSTs made, and what the CLI printed."""
    from unittest import mock

    from click.testing import CliRunner
    from keel.cli.main import cli

    for k in ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KEEL_API_KEY", "kl_test")
    monkeypatch.setenv("KEEL_API_URL", "https://api.test.usekeel.io")
    monkeypatch.setenv("KEEL_AGENT_MODE", "true")
    posts: list[tuple[str, dict, dict]] = []

    def post(self, path, json=None, **params):
        posts.append((path, json, params))
        return {
            "share_id": "S" * 23,
            "study_id": "stu_a",
            "version": 2,
            "url": "https://usekeel.io/s",
            "views": (json or {}).get("views"),
            "show_author": (json or {}).get("show_author"),
        }

    def get(self, path, **_):
        return (
            {"venue": {"capital_class": "practice"}} if path.startswith("/v1/deployments/") else {}
        )

    with (
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.client.KeelClient.get", get),
    ):
        result = CliRunner().invoke(cli, ["share", "create", *argv, "--yes", "--format", "json"])
    return result.exit_code, posts, result.output


def test_the_cli_shares_a_study_with_the_flags_left_off(monkeypatch) -> None:
    code, posts, out = _cli(monkeypatch, "stu_a", "--views", "0", "--views", "3")
    assert code == 0, out
    # One POST, the views as the integers the route takes (the CLI hands text).
    assert posts == [
        ("/v1/research/studies/stu_a/shares", {"show_author": False, "views": [0, 3]}, {})
    ]
    assert '"share_type": "study"' in out


@pytest.mark.parametrize(
    ("flags", "name"),
    [
        (["--include-source"], "include_source"),
        (["--permission", "fork"], "permission"),
        (["--expires-at", FUTURE], "expires_at"),
    ],
)
def test_the_cli_still_refuses_a_source_flag_on_a_study(monkeypatch, flags, name) -> None:
    code, posts, out = _cli(monkeypatch, "stu_a", "--views", "0", *flags)
    assert code == 2 and not posts
    assert (
        f"A study share never shows the study's source: {name} cannot be set. Nothing was published."
        in out
    )
    assert "invalid_arguments" in out


@pytest.mark.parametrize(
    ("argv", "path", "body"),
    [
        (
            ["str_a"],
            "/v1/strategies/str_a/share-links",
            {"include_source": False, "pin_latest_backtest": True},
        ),
        (
            ["str_a", "--include-source"],
            "/v1/strategies/str_a/share-links",
            {"include_source": True, "pin_latest_backtest": True},
        ),
        (
            ["btr_a", "--expires-at", FUTURE],
            "/v1/backtests/btr_a/share-link",
            {"include_source": False, "expires_at": "2099-01-01T00:00:00+00:00"},
        ),
        (["dep_a"], "/v1/paper/dep_a/share-links", {"include_strategy_source": False}),
    ],
)
def test_control_the_cli_shares_every_other_kind_as_main_does(
    monkeypatch, argv, path, body
) -> None:
    code, posts, out = _cli(monkeypatch, *argv)
    assert code == 0, out
    assert len(posts) == 1  # not vacuous: the share was made
    assert posts[0][:2] == (path, body)


def test_the_cli_still_refuses_a_study_argument_on_a_strategy(monkeypatch) -> None:
    code, posts, out = _cli(monkeypatch, "str_a", "--show-author")
    assert code == 2 and not posts
    assert "`views` and `show_author` are for a study share (stu_*); nothing was published." in out


@pytest.mark.parametrize(
    "arg", [{"include_source": False}, {"permission": "view"}, {"expires_at": ""}]
)
def test_a_default_value_asks_nothing_of_a_study_share(arg) -> None:
    client = _Client()
    TOOL.handler({"target_id": "stu_a", **arg}, ToolContext(api_client=client))
    assert client.posts == [("/v1/research/studies/stu_a/shares", {"show_author": False})]


def test_a_views_list_that_is_not_indexes_is_refused() -> None:
    client = _Client()
    with pytest.raises(KeelError) as caught:
        TOOL.handler({"target_id": "stu_a", "views": ["first"]}, ToolContext(api_client=client))
    assert caught.value.error_code == "invalid_arguments" and not client.posts


# ── The MCP dispatch, both profiles ─────────────────────────────────────────


def _mcp(monkeypatch, profile: str, args: dict):
    """``tools/call keel_share_create`` on a real hosted server of ``profile``,
    the Keel API faked at KeelClient: (is_error, POSTs, served text)."""
    import asyncio
    from unittest import mock

    from fastmcp import Client
    from keel.hosting import bind_request_credentials, clear_request_credentials
    from keel.mcp.server import create_server

    for k in ("KEEL_TOOLSETS", "KEEL_API_KEY", "KEEL_API_URL", "KEEL_LISTED_CLIENT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    posts: list[tuple[str, dict]] = []

    def post(self, path, json=None, **params):
        posts.append((path, json))
        return {"share_id": "S" * 23, "study_id": "stu_a", "version": 2, "share_type": "strategy"}

    async def call():
        async with Client(create_server()) as client:
            return await client.call_tool("keel_share_create", args, raise_on_error=False)

    token = bind_request_credentials(token="tok_test", api_url="https://api.test.usekeel.io")
    try:
        with (
            mock.patch("keel.client.KeelClient.post", post),
            mock.patch("keel.client.KeelClient.get", lambda *a, **k: {}),
        ):
            result = asyncio.run(call())
    finally:
        clear_request_credentials(token)
    return result.is_error, posts, "\n".join(getattr(c, "text", "") for c in result.content)


def test_the_full_profile_shares_a_study_and_takes_a_default_as_unset(monkeypatch) -> None:
    err, posts, text = _mcp(
        monkeypatch, "full", {"target_id": "stu_a", "views": [0, 3], "include_source": False}
    )
    assert not err, text
    assert posts == [("/v1/research/studies/stu_a/shares", {"show_author": False, "views": [0, 3]})]


def test_the_full_profile_still_refuses_the_source_on_a_study(monkeypatch) -> None:
    err, posts, text = _mcp(monkeypatch, "full", {"target_id": "stu_a", "include_source": True})
    assert err and not posts
    assert "A study share never shows the study's source: include_source cannot be set." in text


def test_the_listed_profile_does_not_share_a_study(monkeypatch) -> None:
    # Its schema drops `views`, so a share here would publish every view (07 §2.2: keel_app_link).
    err, posts, text = _mcp(monkeypatch, "listed", {"target_id": "stu_a", "views": [0]})
    assert err and not posts
    assert "unsupported_target" in text and "keel_app_link" in text


@pytest.mark.parametrize("profile", ["full", "listed"])
def test_control_a_strategy_share_is_mains_on_both_profiles(monkeypatch, profile) -> None:
    err, posts, text = _mcp(monkeypatch, profile, {"target_id": "str_a", "show_author": False})
    assert not err, text
    assert posts == [
        ("/v1/strategies/str_a/share-links", {"include_source": False, "pin_latest_backtest": True})
    ]
