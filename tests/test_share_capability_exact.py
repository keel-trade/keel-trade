"""`keel_share_create` applies the requested capability EXACTLY (Q-2255).

The Anthropic MCP Directory review (2026-10-01) called the hosted tool with
`permission="view", include_source=false` and got a public, forkable page
with the source shown; `expires_at` was dropped on a backtest share and
refused as "Input should be a valid datetime" on a strategy share (both the
`Z` and the `+00:00` spelling). What they asked for, and what every arm
below drives through the REAL FastMCP wire on the hosted listed server (the
Keel API faked at `KeelClient.post` / `.get`):

* `permission` and `include_source` applied exactly as requested — either
  alone decides, both must agree, a disagreeing pair is refused before
  anything is published;
* the default is view-only with the source hidden;
* ISO-8601 `expires_at` accepted in both spellings, on both routes;
* the effective settings come back in the result.

Seeds (run 2026-10-01, each reverted by reversing the edit):
* `include_source = requested == "fork"` → `include_source = False` — the
  `permission="fork"` arm reds (body sends false), every other arm green;
* re-add `"keel_share_create": frozenset({"permission"})` to
  `_base.LISTED_SCHEMA_OMISSIONS` — the published-schema arm, the
  `permission="fork"` arm and all three refusal arms red (the input is
  dropped, so a disagreeing pair publishes instead of being refused); the
  reviewers' exact call stays green because its pair already agrees.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.tools.outcomes import _bootstrap


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)


@contextmanager
def _hosted_listed():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
    token = bind_request_credentials(token="tok_test", api_url=API)
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _share(args: dict) -> tuple[list[tuple[str, dict]], dict, bool]:
    """POSTs the tool made, the decoded result, and whether it errored. The
    fake API echoes what keel-api stores: permission derived from the
    request's include_source (the server's own rule)."""
    posts: list[tuple[str, dict]] = []

    def post(self, path, json=None, **_kw):
        body = dict(json or {})
        posts.append((path, body))
        shown = bool(body.get("include_source", True))
        return {
            "share_id": "gDXjURKqWPs8CZ4eXdqAI",
            "share_type": "backtest" if "/backtests/" in path else "strategy",
            "include_source": shown,
            "permission": "fork" if shown else "view",
            "expires_at": body.get("expires_at"),
        }

    async def call():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool("keel_share_create", args, raise_on_error=False)

    with (
        _hosted_listed(),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.client.KeelClient.get", lambda self, path, **_: {}),
    ):
        result = asyncio.run(call())
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    try:
        decoded = json.loads(text)
    except ValueError:
        decoded = {"_text": text}
    return posts, decoded, bool(result.is_error)


def _effective(result: dict) -> dict:
    body = result.get("result", result)
    if isinstance(body, str):
        body = json.loads(body)
    return body


def test_the_reviewers_exact_call_publishes_view_only_with_source_hidden():
    posts, result, err = _share(
        {"target_id": "str_x", "permission": "view", "include_source": False}
    )
    assert not err, result
    assert len(posts) == 1  # non-vacuous: one share was created
    path, body = posts[0]
    assert path == "/v1/strategies/str_x/share-links"
    assert body["include_source"] is False and "permission" not in body
    eff = _effective(result)
    assert (eff["include_source"], eff["permission"]) == (False, "view")


def test_the_default_is_view_only_with_source_hidden():
    posts, result, err = _share({"target_id": "str_x"})
    assert not err, result
    assert posts[0][1]["include_source"] is False
    assert _effective(result)["permission"] == "view"


@pytest.mark.parametrize(
    ("args", "shown"),
    [
        ({"permission": "fork"}, True),
        ({"permission": "view"}, False),
        ({"include_source": True}, True),
        ({"include_source": True, "permission": "fork"}, True),
    ],
)
def test_either_field_alone_decides_and_an_agreeing_pair_applies(args, shown):
    posts, result, err = _share({"target_id": "str_x", **args})
    assert not err, result
    assert posts[0][1]["include_source"] is shown
    eff = _effective(result)
    assert (eff["include_source"], eff["permission"]) == (shown, "fork" if shown else "view")


@pytest.mark.parametrize(
    "args",
    [
        {"permission": "fork", "include_source": False},
        {"permission": "view", "include_source": True},
        {"permission": "edit"},
    ],
)
def test_a_disagreeing_or_unknown_request_is_refused_before_publishing(args):
    posts, result, err = _share({"target_id": "str_x", **args})
    assert posts == []  # nothing published
    assert "invalid_permission" in json.dumps(result), result


@pytest.mark.parametrize("stamp", ["2099-10-02T00:00:00Z", "2099-10-02T00:00:00+00:00"])
@pytest.mark.parametrize("target", ["str_x", "btr_1"])
def test_iso_expiry_is_accepted_in_both_spellings_on_both_routes(stamp, target):
    posts, result, err = _share({"target_id": target, "expires_at": stamp})
    assert not err, result
    sent = posts[0][1]["expires_at"]
    assert sent.startswith("2099-10-02T00:00:00") and sent.endswith("+00:00")
    assert _effective(result)["expires_at"] == sent  # returned, not dropped


def test_permission_is_in_the_published_listed_schema():
    from keel.mcp.server import create_server

    with _hosted_listed():
        tools = asyncio.run(create_server().list_tools())
    props = next(t for t in tools if t.name == "keel_share_create").parameters["properties"]
    assert props["permission"]["enum"] == ["view", "fork"]
    assert "default" not in props["include_source"]
