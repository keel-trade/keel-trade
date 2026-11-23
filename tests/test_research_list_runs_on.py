"""D-371 (V6 H6 / F11): the MCP list read never hands a model the app's "Runs on {plan}" badge.

keel-api's ``GET /v1/research/studies`` serves ``runs_on`` (WL-SV-31): each home template's capability line in the
APP's form ("Runs on Trader"). D-371: agents get the agent form of plan words, never that badge. CHAT-v71 fixed the
in-app agent's ``list_studies``; this proves the MCP ``keel_research_get`` list read (``research_get._list``) the same
way, over a real envelope: keel-api's served list body (the Research mock world's list rows, plus the ``runs_on`` map
as keel-api adds it) through the real server (``create_server().call_tool``: the handler, the envelope, the channel
reducer, the adapter). What a model reads is the result's ``content`` text and its ``structuredContent``; neither may
carry the badge or the key. ``_meta`` is the card's (read by no model) and is not asserted on.

SEED (reversed by reversing the edit): ``"runs_on"`` out of ``CHANNEL_MAP["study"].drop`` (``_channels.py``) reds
the structuredContent arm on both profiles; the text arm and the served-body control stay green (the text never
named it). Not vacuous: the served body carries three "Runs on" lines and 25 rows reach the model.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from tests.test_research_structured_output import _list_body


RUNS_ON = {"check": None, "compare": "Runs on Trader", "entries": "Runs on Trader", "map": "Runs on Pro"}


def _served() -> dict:
    """keel-api's list read: the world's rows, with ``runs_on`` as research.py's list_studies adds it."""
    return {**_list_body(), "runs_on": dict(RUNS_ON)}


def _list_read(profile: str):
    from keel.tools.outcomes._toolsets import research_served_context

    body = _served()

    def get(self, path, *, headers=None, **params):
        assert path == "/v1/research/studies", path
        return body

    async def go():
        from keel.mcp.server import create_server

        return await create_server().call_tool("keel_research_get", {})

    saved = os.environ.get("KEEL_SERVER_PROFILE")
    os.environ["KEEL_SERVER_PROFILE"] = profile
    try:
        with research_served_context(True), patch("keel.client.KeelClient.get", get):
            return asyncio.run(go())
    finally:
        if saved is None:
            os.environ.pop("KEEL_SERVER_PROFILE", None)
        else:
            os.environ["KEEL_SERVER_PROFILE"] = saved


def _text(result) -> str:
    return "\n".join(getattr(block, "text", "") or "" for block in result.content or [])


@pytest.mark.parametrize("profile", ("full", "listed"))
def test_d371_the_list_reads_structured_content_never_carries_runs_on(profile: str) -> None:
    result = _list_read(profile)
    structured = result.structured_content or {}
    assert "runs_on" not in structured, sorted(structured)
    assert "Runs on" not in json.dumps(structured), "a 'Runs on' badge reached structuredContent"
    assert len(structured.get("studies") or []) == 25  # not vacuous: the rows reached the model


@pytest.mark.parametrize("profile", ("full", "listed"))
def test_d371_the_list_reads_text_never_names_a_runs_on_badge(profile: str) -> None:
    text = _text(_list_read(profile))
    assert "Studies: 25 shown of" in text, text[:200]  # not vacuous: the list's own text reached the model
    assert "Runs on" not in text and "runs_on" not in text


def test_d371_control_the_served_body_carries_the_badge() -> None:
    """Control (green through the seed): keel-api's body does carry the app's badge, so the arms above test a drop."""
    served = _served()
    assert sum(1 for v in served["runs_on"].values() if v and v.startswith("Runs on ")) == 3
