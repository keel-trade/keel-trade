"""The listed paper exposure is switched per host (founder ruling FDR-10, 2026-10-12).

One hosted endpoint serves claude.ai and ChatGPT. ChatGPT reviews a scanned
29-tool snapshot, so its sessions must keep exactly HEAD's listed surface
while Claude sessions may carry the simulated paper tool. The switch is
``KEEL_LISTED_PAPER_HOSTS`` (``claude``, ``chatgpt``, ``all``); the caller's
host key is bound per request (``listed_host_context``) by the hosted server.

Arms:

* defaults: every host (claude, chatgpt, unknown) is served HEAD's listed
  surface byte for byte (the serialized ``listed_surface_head.json``);
* ``KEEL_LISTED_PAPER_HOSTS=claude``, in ONE process: a Claude session lists 31
  tools (``keel_paper_deploy`` and ``keel_paper_control``, both simulated; S10
  §3, PT2-90) and the amended boundary sentence; a ChatGPT session and an unknown client get HEAD's surface byte
  for byte, and the ChatGPT session's monitor output is HEAD's bytes;
* an invalid host key refuses server creation; the deprecated alias still
  means every host.

Seed (applied by an exact reversible edit, then reversed): make
``listed_paper_enabled()`` ignore the host check (``return True`` once any host
is on) ⇒ the ChatGPT and unknown arms red with the surface diff naming
``keel_paper_deploy`` and the instructions.
"""

from __future__ import annotations

import json

import pytest
from keel.tools.outcomes._toolsets import listed_host_context

from tests._listed_head_fixtures import (
    OUTPUTS_FIXTURE,
    SURFACE_FIXTURE,
    call_listed,
    listed_hosted_env,
    listed_surface_snapshot,
    load_world,
)
from tests.test_listed_outputs_head import _surface_diff
from tests.test_policy_scan import LISTED_BOUNDARY_SENTENCE, LISTED_PAPER_BOUNDARY_SENTENCE


_HEAD = json.loads(SURFACE_FIXTURE.read_text())
_CLAUDE_ON = {"KEEL_LISTED_PAPER_HOSTS": "claude"}


def _assert_heads(snapshot: dict) -> None:
    assert _HEAD["tool_count"] == 29 and len(_HEAD["tools"]) == 29
    diffs, compared = _surface_diff(_HEAD, snapshot)
    assert compared >= 29 * 6, f"compared only {compared} fields"
    assert diffs == [], f"the listed surface moved for this host: {diffs}"
    assert snapshot["tool_count"] == 29
    assert LISTED_BOUNDARY_SENTENCE in snapshot["instructions"]


@pytest.mark.parametrize("host", ["claude", "chatgpt", None])
def test_defaults_serve_heads_surface_to_every_host(host):
    with listed_host_context(host):
        _assert_heads(listed_surface_snapshot())


@pytest.mark.parametrize("host", ["chatgpt", None])
def test_claude_on_leaves_other_hosts_on_heads_surface(host, paper_sdk_released):
    # Non-vacuity: in this same process a Claude session IS served paper.
    with listed_host_context("claude"):
        claude = listed_surface_snapshot(_CLAUDE_ON)
    assert claude["tool_count"] == 31
    with listed_host_context(host):
        _assert_heads(listed_surface_snapshot(_CLAUDE_ON))


def test_claude_on_serves_claude_the_simulated_paper_tool(paper_sdk_released):
    with listed_host_context("claude"):
        snap = listed_surface_snapshot(_CLAUDE_ON)
    names = {t["name"] for t in snap["tools"]}
    assert snap["tool_count"] == 31
    assert names - {t["name"] for t in _HEAD["tools"]} == {
        "keel_paper_deploy",
        "keel_paper_control",
    }
    for name in ("keel_paper_deploy", "keel_paper_control"):
        paper = next(t for t in snap["tools"] if t["name"] == name)
        assert "simulated" in paper["description"].lower(), name
    assert not names & {"keel_live_deploy", "keel_live_control", "keel_live_update"}
    assert LISTED_PAPER_BOUNDARY_SENTENCE in snap["instructions"]
    assert LISTED_BOUNDARY_SENTENCE not in snap["instructions"]


def test_claude_on_chatgpt_monitor_output_is_heads(paper_sdk_released):
    outputs = {o["name"]: o for o in json.loads(OUTPUTS_FIXTURE.read_text())["outputs"]}
    head = outputs["live_monitor:portfolio"]
    assert head["text"] and head["structured"], "empty base output; this arm proves nothing"
    world = load_world()
    with listed_host_context("chatgpt"):
        got = call_listed(head["tool"], head["args"], world, env=_CLAUDE_ON)
    fields = ("structured", "text", "meta", "is_error")
    assert {k: got[k] for k in fields} == {k: head[k] for k in fields}


@pytest.mark.parametrize("value", ["claud", "claude,openai", "none"])
def test_an_invalid_host_setting_refuses_server_creation(value, paper_sdk_released):
    from keel.mcp.server import create_server

    with listed_hosted_env({"KEEL_LISTED_PAPER_HOSTS": value}):
        with pytest.raises(ValueError, match="KEEL_LISTED_PAPER_HOSTS"):
            create_server()


@pytest.mark.parametrize("host", ["claude", "chatgpt", None])
def test_the_deprecated_alias_means_every_host(host, paper_sdk_released):
    from keel.tools.outcomes._toolsets import listed_paper_enabled

    with listed_hosted_env({"KEEL_LISTED_PAPER": "1"}), listed_host_context(host):
        assert listed_paper_enabled() is True
    with listed_hosted_env(_CLAUDE_ON), listed_host_context(host):
        assert listed_paper_enabled() is (host == "claude")


def test_an_unknown_host_key_cannot_be_bound():
    with pytest.raises(ValueError, match="Unknown listed host key"):
        with listed_host_context("openai"):
            pass
