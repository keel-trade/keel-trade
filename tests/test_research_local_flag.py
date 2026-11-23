"""One flag hides Research on the LOCAL surfaces too (FINAL-LIST §D; 07 §5.3; AUDIT finding 1).

The hosted server binds ``research_served`` per request from the caller's ``/v1/me``. Unbound, a local surface (the
CLI, a full-profile ``keel mcp serve``) used to serve Research to everyone, so a prod user of the published wheel
saw the research tools, topics, the study card and ``keel research`` while ``research.enabled`` was off, and every
call answered "Research is paused". Now it reads its own caller's ``/v1/me`` (``_research_local``), cached:

- the read: ``research`` block present -> served; absent -> not; no credential -> not, and no request is made (the
  read would mint an anonymous org); a failed read -> the last good answer on disk, else not;
- the cache: one request per process, and one per TTL across processes, keyed by the API and the credential;
- the surfaces: off, the CLI has no ``research`` group and the full MCP profile lists no research tool, topic or
  study card; on, all of them, the same tool set the bound ``True`` serves.

Seeded red 2026-11-20 (each reverted): ``served_from_me`` answering true for any object reds the absent-block arm;
``local_research_served`` returning true without a credential reds the no-credential arm; the ``is_tool_loaded``
research line removed reds the MCP off arm (the on arm, the control, stays green); the CLI adapter's research filter
removed reds the CLI off arm.
"""

from __future__ import annotations

import asyncio
import json

import click
import httpx
import pytest
import respx
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import _bootstrap as _outcomes_bootstrap
from keel.tools.outcomes._toolsets import research_served, research_served_context


API = "http://keel-api.test"
ME_ON = {"principal_id": "prn_1", "research": {"authoring": True}}
ME_OFF = {"principal_id": "prn_1"}


@pytest.fixture
def caller(monkeypatch, real_local_research_read):
    """A local caller acting as an API key against ``API``."""
    monkeypatch.setenv("KEEL_API_KEY", "keel_test_key")
    monkeypatch.setenv("KEEL_API_URL", API)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    return real_local_research_read


def test_served_follows_the_me_research_block(caller):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{API}/v1/me").mock(return_value=httpx.Response(200, json=ME_ON))
        assert caller.local_research_served() is True
        assert research_served() is True
        assert route.call_count == 1  # one request per process
    caller.forget()
    (caller._cache_path()).unlink()
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{API}/v1/me").mock(return_value=httpx.Response(200, json=ME_OFF))
        assert caller.local_research_served() is False
    assert caller.served_from_me({"research": None}) is False
    assert caller.served_from_me([]) is False


def test_no_credential_is_not_served_and_makes_no_request(monkeypatch, real_local_research_read):
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    with respx.mock(assert_all_mocked=True) as mock:
        assert real_local_research_read.local_research_served() is False
        assert mock.calls.call_count == 0


def test_the_disk_cache_spares_the_next_process_and_keeps_the_last_good_answer(caller):
    with respx.mock() as mock:
        mock.get(f"{API}/v1/me").mock(return_value=httpx.Response(200, json=ME_ON))
        assert caller.local_research_served() is True
    rows = json.loads(caller._cache_path().read_text())
    assert len(rows) == 1 and "keel_test_key" not in json.dumps(
        rows
    )  # keyed by a hash, never the credential
    caller.forget()  # a new process within the TTL: no request
    with respx.mock(assert_all_mocked=True) as mock:
        assert caller.local_research_served() is True
        assert mock.calls.call_count == 0
    # Past the TTL, a failed read keeps the last good answer; with no answer on disk it is hidden.
    for row in rows.values():
        row["at"] = 0
    caller._cache_path().write_text(json.dumps(rows))
    caller.forget()
    with respx.mock() as mock:
        mock.get(f"{API}/v1/me").mock(return_value=httpx.Response(503))
        assert caller.local_research_served() is True
    caller.forget()
    caller._cache_path().unlink()
    with respx.mock() as mock:
        mock.get(f"{API}/v1/me").mock(side_effect=httpx.ConnectError("down"))
        assert caller.local_research_served() is False


def _full_profile_names(served: bool) -> tuple[set[str], list[str], list[str]]:
    from keel.data.reference import load_reference
    from keel.mcp.server import create_server
    from keel.tools.outcomes.help import _bundled_index

    with research_served_context(served):
        server = create_server()
        tools = {t.name for t in asyncio.run(server.list_tools())}
        resources = [str(r.uri) for r in asyncio.run(server.list_resources())]
        topics = sorted(_bundled_index()) + list(load_reference()["topics"])
    return tools, resources, topics


def test_the_full_mcp_profile_hides_research_while_off(monkeypatch):
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    on_tools, on_resources, on_topics = _full_profile_names(True)
    off_tools, off_resources, off_topics = _full_profile_names(False)
    research = {n for n in on_tools if n.startswith("keel_research_")}
    assert len(research) >= 8  # non-vacuity: the on profile serves Research
    assert "ui://keel/cards/study.html" in on_resources and "research" in on_topics
    assert not {n for n in off_tools if n.startswith("keel_research_")}
    assert off_tools == on_tools - research  # nothing else moves
    assert not [u for u in off_resources if "/study" in u]
    assert not [t for t in off_topics if t.startswith("research")]


def _cli_groups(served: bool) -> click.Group:
    from keel.tools.outcomes._cli_adapter import register_all

    _outcomes_bootstrap()
    root = click.Group("keel")
    with research_served_context(served):
        register_all(root, OUTCOMES)
    return root


def test_the_cli_has_no_research_group_while_off():
    on, off = _cli_groups(True), _cli_groups(False)
    assert "research" in on.commands and len(on.commands["research"].commands) >= 20
    assert "research" not in off.commands
    assert set(off.commands) == set(on.commands) - {
        "research"
    }  # control: every other group is there
