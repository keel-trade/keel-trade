"""`keel_share_create` returns a link that is ready to post (Q-2587).

Founder 2026-10-09: "for create share, yes just wait till generation done so
it's ready". keel-api owns the wait (`wait_for_card=true` holds the create
until the link-preview card exists, bounded at ~10 s); this tool asks for it
on both routes and, when the card is still not there, says so in ONE line —
the same `preview_note` text on the CLI handler and on the MCP wire.

Seeds (each reverted by reversing the edit): drop `**_WAIT_FOR_CARD` from the
strategy post → the strategy wait arm reds; make `preview_note` return None
→ the pending/failed arms red on both surfaces while the ready and
cards-disabled controls stay green.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.tools.outcomes import _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes.share_create import PREVIEW_FAILED_NOTE, PREVIEW_PENDING_NOTE


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)


def _fake_post(status: str | None, enabled: bool = True):
    calls: list[tuple[str, dict, dict]] = []

    def post(self, path, json=None, **params):
        calls.append((path, dict(json or {}), params))
        body = {
            "share_id": "gDXjURKqWPs8CZ4eXdqAI",
            "share_type": "backtest" if "/backtests/" in path else "strategy",
            "include_source": False,
            "permission": "view",
            "artifacts_enabled": enabled,
        }
        if status is not None:
            body["artifacts_status"] = status
        return body

    return calls, post


def _cli(target: str, status: str | None, enabled: bool = True):
    from keel.tools.outcomes import OUTCOMES

    calls, post = _fake_post(status, enabled)
    with (
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.client.KeelClient.get", lambda self, path, **_: {}),
    ):
        env = OUTCOMES["keel_share_create"].handler({"target_id": target}, ToolContext())
    return calls, env.to_envelope()


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


def _mcp(target: str, status: str | None) -> tuple[list, dict]:
    calls, post = _fake_post(status)

    async def call():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool(
                "keel_share_create", {"target_id": target}, raise_on_error=False
            )

    with (
        _hosted_listed(),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.client.KeelClient.get", lambda self, path, **_: {}),
    ):
        result = asyncio.run(call())
    assert not result.is_error
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    body = json.loads(text)
    body = body.get("result", body)
    if isinstance(body, str):
        body = json.loads(body)
    return calls, body


@pytest.mark.parametrize(
    ("target", "path"),
    [
        ("str_x", "/v1/strategies/str_x/share-links"),
        ("btr_x", "/v1/backtests/btr_x/share-link"),
    ],
)
def test_both_routes_ask_keel_api_to_wait_for_the_card(target, path):
    calls, _ = _cli(target, "ready")
    assert len(calls) == 1  # non-vacuous: exactly one create was made
    assert calls[0][0] == path
    assert calls[0][2] == {"wait_for_card": "true"}
    # Query, never body: the strict body models refuse unknown fields (Q-2255).
    assert "wait_for_card" not in calls[0][1]


@pytest.mark.parametrize(
    ("status", "note"),
    [("pending", PREVIEW_PENDING_NOTE), ("failed", PREVIEW_FAILED_NOTE)],
)
def test_a_card_that_is_not_ready_is_said_once_the_same_on_cli_and_mcp(status, note):
    _, cli_env = _cli("str_x", status)
    _, mcp_env = _mcp("str_x", status)
    assert cli_env["share_id"] == mcp_env["share_id"] == "gDXjURKqWPs8CZ4eXdqAI"
    assert cli_env.get("preview_note") == note
    assert mcp_env.get("preview_note") == note


@pytest.mark.parametrize(
    ("status", "enabled"),
    [("ready", True), ("pending", False), (None, True)],
    ids=["ready", "cards-disabled", "older-api-no-status"],
)
def test_control_no_note_when_there_is_nothing_to_wait_for(status, enabled):
    _, env = _cli("str_x", status, enabled)
    assert env["share_url"].startswith("https://usekeel.io/share/gDXjURKqWPs8CZ4eXdqAI")
    assert "preview_note" not in env


def test_the_note_text_is_the_founders_line():
    assert PREVIEW_PENDING_NOTE == (
        "The preview image is still generating; wait a few seconds before posting the link."
    )
