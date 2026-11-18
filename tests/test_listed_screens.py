"""The listed screens flag (screeners L13, founder 2026-11-03).

Off (prod until the founder flips it), the hosted connector serves the base
surface byte for byte: the same 29 tools (31 with paper), instructions, card
resources and prompts as the frozen head fixture, and it withholds the three
screen topics, which describe tools it does not register. On for a host, that
host also gets exactly the four screen tools, the screen card, the screen-aware
``keel_strategy_compose``, the ``screen-creation`` prompt, the SCREENS line and
the screen topics — and nothing else moves. The budgets are the founder's F4:
15,000 description characters (17,000 with paper); the 13,000 base ceiling
keeps guarding the flag-off surface (test_description_shape).
"""

from __future__ import annotations

import json

import pytest

from pipeline_engine.reference.system import assemble
from tests._listed_head_fixtures import SURFACE_FIXTURE, listed_surface_snapshot


SCREEN_TOOLS = {
    "keel_screener_run",
    "keel_screener_view",
    "keel_screener_view_set",
    "keel_screener_list",
}
SCREENS_ON_CEILING = 15_000
SCREENS_PAPER_ON_CEILING = 17_000


def _by_name(snapshot):
    return {t["name"]: t for t in snapshot["tools"]}


def _prompts(env):
    import asyncio

    from keel.mcp.server import create_server
    from tests._listed_head_fixtures import listed_hosted_env

    with listed_hosted_env(env):
        return {p.name for p in asyncio.run(create_server().list_prompts())}


def _help_topics(env):
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext
    from tests._listed_head_fixtures import listed_hosted_env

    with listed_hosted_env(env):
        return set(OUTCOMES["keel_help"].handler({}, ToolContext()).extra["topics"])


@pytest.fixture(scope="module")
def off():
    return listed_surface_snapshot()


@pytest.fixture(scope="module")
def on():
    return listed_surface_snapshot({"KEEL_LISTED_SCREENS_HOSTS": "all"})


def test_flag_off_is_the_frozen_head(off):
    """# SEED (L13): add "keel_screener_list" to `LISTED_PROFILE_TOOLS` in
    `_toolsets.py` — the flag-off surface gains a tool and this reds. Reverse it."""
    head = json.loads(SURFACE_FIXTURE.read_text())
    assert off["tool_count"] == head["tool_count"] == 29
    assert off["tools"] == head["tools"]
    assert off["instructions"] == head["instructions"]
    assert off["card_resources"] == head["card_resources"]
    assert not SCREEN_TOOLS & set(_by_name(off))
    assert not any("screener" in c["uri"] for c in off["card_resources"])


def test_flag_on_adds_exactly_the_screen_surface(off, on):
    """# SEED (L13): drop `keel_screener_list` from `LISTED_SCREEN_TOOLS` — the
    added set is three tools and this reds. Reverse it."""
    before, after = _by_name(off), _by_name(on)
    assert set(after) - set(before) == SCREEN_TOOLS
    assert set(before) <= set(after)
    changed = {n for n in before if before[n] != after[n]}
    assert changed == {"keel_strategy_compose"}, changed
    compose = after["keel_strategy_compose"]
    assert "`Outputs(slots=[...])`" in compose["description"]
    assert compose["annotations"]["title"] == "Save Strategy or Screen"
    assert "screen" in compose["inputSchema"]["properties"]["source"]["description"]
    # The screen card joins, both host templates; the base cards are unchanged.
    new_cards = {c["uri"] for c in on["card_resources"]} - {
        c["uri"] for c in off["card_resources"]
    }
    assert new_cards == {"ui://keel/cards/screener.html", "ui://keel/cards/screener.openai.html"}
    for uri in ("keel_screener_run", "keel_screener_view", "keel_screener_view_set"):
        assert after[uri]["_meta"]["ui"]["resourceUri"] == "ui://keel/cards/screener.html"
    # Instructions: the base plus the SCREENS line, nothing else.
    screens = assemble.section("screens").terse
    assert on["instructions"].count(screens) == 1
    assert on["instructions"].replace("\n\n" + screens, "", 1) == off["instructions"]


def test_screen_tools_annotations_and_titles(on):
    tools = _by_name(on)
    expected = {
        "keel_screener_run": ("Run Screen", False, False),
        "keel_screener_view": ("View Screen", True, False),
        "keel_screener_view_set": ("Edit Screen View", False, False),
        "keel_screener_list": ("List Screens", True, False),
    }
    for name, (title, read_only, destructive) in expected.items():
        ann = tools[name]["annotations"]
        assert ann["title"] == title, name
        assert ann["readOnlyHint"] is read_only, name
        assert ann["destructiveHint"] is destructive, name
        assert ann["openWorldHint"] is False, name
        assert len(tools[name]["description"]) >= assemble.MIN_DESCRIPTION_CHARS, name
    # The listed schemas drop only the declared omissions.
    assert "source_file" not in tools["keel_screener_run"]["inputSchema"]["properties"]
    assert set(tools["keel_screener_list"]["inputSchema"]["properties"]) == {"scope"}
    assert "include_view_source" in tools["keel_screener_view"]["inputSchema"]["properties"]
    assert "revert_to" in tools["keel_screener_view_set"]["inputSchema"]["properties"]


@pytest.mark.parametrize(
    "env,ceiling",
    [
        ({"KEEL_LISTED_SCREENS_HOSTS": "all"}, SCREENS_ON_CEILING),
        (
            {"KEEL_LISTED_SCREENS_HOSTS": "all", "KEEL_LISTED_PAPER_HOSTS": "all"},
            SCREENS_PAPER_ON_CEILING,
        ),
    ],
)
def test_flag_on_budgets(env, ceiling):
    """F4: the flag-on description totals, and the 2 KB instruction cut."""
    snapshot = listed_surface_snapshot(env)
    total = sum(len(t.get("description", "")) for t in snapshot["tools"])
    print(f"AIM  listed flag-on total {env}: {total}")
    assert total <= ceiling, total
    assert len(snapshot["instructions"].encode()) <= assemble.INSTRUCTIONS_MAX_BYTES


def test_prompts_and_topics_follow_the_flag():
    off_prompts = _prompts(None)
    on_prompts = _prompts({"KEEL_LISTED_SCREENS_HOSTS": "all"})
    assert on_prompts - off_prompts == {"screen-creation"}
    assert len(off_prompts) == 7
    screen_topics = {"screener", "view_language", "screen_recipes"}
    off_topics = _help_topics(None)
    on_topics = _help_topics({"KEEL_LISTED_SCREENS_HOSTS": "all"})
    assert not screen_topics & off_topics
    assert screen_topics <= on_topics
    assert on_topics - off_topics == screen_topics


def test_per_host_switch():
    """FDR-10's shape: a host key turns screens on for that host only."""
    from keel.tools.outcomes._toolsets import (
        listed_host_context,
        listed_screens_enabled,
    )
    from tests._listed_head_fixtures import listed_hosted_env

    with listed_hosted_env({"KEEL_LISTED_SCREENS_HOSTS": "claude"}):
        with listed_host_context("claude"):
            assert listed_screens_enabled()
        with listed_host_context("chatgpt"):
            assert not listed_screens_enabled()
        with listed_host_context(None):
            assert not listed_screens_enabled()


def test_an_invalid_host_key_fails_server_creation():
    from tests._listed_head_fixtures import listed_hosted_env

    with listed_hosted_env({"KEEL_LISTED_SCREENS_HOSTS": "claude,gemini"}):
        from keel.mcp.server import create_server

        with pytest.raises(ValueError, match="KEEL_LISTED_SCREENS_HOSTS"):
            create_server()



# ── Research (07 §8.5, AT-13; 00 §H14, D-20) ─────────────────────────────────

_SCREENS = {"KEEL_LISTED_SCREENS_HOSTS": "all"}
_PAPER = {"KEEL_LISTED_PAPER_HOSTS": "all"}
#: compose's pinned exception (RL-39): it may only shrink.
_COMPOSE_PINNED = {False: 1206, True: 1516}


@pytest.mark.parametrize(
    "env,ceiling",
    [
        ({}, SCREENS_ON_CEILING),
        (_SCREENS, SCREENS_ON_CEILING),
        ({**_SCREENS, **_PAPER}, SCREENS_PAPER_ON_CEILING),
        (_PAPER, SCREENS_PAPER_ON_CEILING),
    ],
    ids=["research", "screens+research", "screens+paper+research", "paper+research"],
)
def test_flag_on_budgets_with_research(env, ceiling):
    """F4 with Research served: the totals stay inside 15,000 / 17,000, each
    description is 200 to 1,000 characters and under 2,048 bytes (compose held
    at its pinned length), and every Screens and Research description opens with
    "Use when" and says "not for" in its first sentence."""
    base = listed_surface_snapshot(env or None)
    snapshot = listed_surface_snapshot(env or None, research=True)
    names = set(_by_name(snapshot))
    # Not vacuous: the research tools are in the snapshot, and only they were added.
    research = {"keel_research_plan", "keel_research_run", "keel_research_get"}
    assert names - set(_by_name(base)) == research and len(names) == len(base["tools"]) + 3
    total = sum(len(t.get("description", "")) for t in snapshot["tools"])
    print(f"AIM  listed research total {env}: {total}")
    assert total <= ceiling, total
    screens = "KEEL_LISTED_SCREENS_HOSTS" in env
    for tool in snapshot["tools"]:
        text = tool.get("description", "")
        if tool["name"] == "keel_strategy_compose":
            assert len(text) <= _COMPOSE_PINNED[screens], len(text)
            continue
        if tool["name"].startswith(("keel_screener_", "keel_research_")):
            assert 200 <= len(text) <= 1_000, (tool["name"], len(text))
            first = text.split("; ", 1)[0] + text.split("; ", 1)[1].split(". ", 1)[0]
            assert text.startswith("Use when") and "not for" in first, tool["name"]
        assert len(text.encode()) < 2_048, tool["name"]
    assert len(snapshot["instructions"].encode()) <= assemble.INSTRUCTIONS_MAX_BYTES


def test_the_screens_trim_rides_research_only():
    """00 §H14 (D-20): with Research not served the four Screens descriptions are
    the frozen head's; served, they are the trimmed copy (and shorter)."""
    from keel.tools.outcomes._base import LISTED_RESEARCH_COPY

    off = _by_name(listed_surface_snapshot(_SCREENS))
    on = _by_name(listed_surface_snapshot(_SCREENS, research=True))
    assert set(LISTED_RESEARCH_COPY) == SCREEN_TOOLS | {"keel_app_link"}
    # WL-AG-06: keel_app_link is every research answer's fallback, so served it names studies; off it is the head.
    assert on["keel_app_link"]["description"] == LISTED_RESEARCH_COPY["keel_app_link"]
    assert "`stu_...`" in on["keel_app_link"]["description"]
    assert "stu_" not in off["keel_app_link"]["description"]
    for name in SCREEN_TOOLS:
        assert on[name]["description"] == LISTED_RESEARCH_COPY[name]
        assert off[name]["description"] != LISTED_RESEARCH_COPY[name]
        assert len(on[name]["description"]) < len(off[name]["description"])


def test_every_listed_research_parameter_says_what_it_takes():
    """A3 #7 / WL-AG-16 (07 §8.5, R-25): with Research served, each of the three listed research tools' parameters
    carries a one-line description (they had 62 undescribed); `subjects` shows its call form and `_run`'s plan fields
    say they are the direct run's. Measured: the listed ceilings count tool descriptions only, so these cost none of
    them. SEED (reversed): drop "subjects" from ``_PLAN_WORDS`` -> this reds on plan and run; control: the flag-off
    head (no research tools) is unchanged."""
    snapshot = _by_name(listed_surface_snapshot(None, research=True))
    counted = 0
    for name in ("keel_research_plan", "keel_research_run", "keel_research_get"):
        props = snapshot[name]["inputSchema"]["properties"]
        for key, prop in props.items():
            text = prop.get("description") or ""
            # one line; `present` is the shared presentation text (400 at most)
            assert 20 <= len(text) <= (400 if key == "present" else 320), (name, key, text)
            counted += 1
    assert counted >= 62, counted  # not vacuous: every listed research parameter
    assert 'Strategy("str_…@3")' in snapshot["keel_research_plan"]["inputSchema"]["properties"]["subjects"]["description"]
    assert snapshot["keel_research_run"]["inputSchema"]["properties"]["subjects"]["description"].startswith("Direct run: ")


def test_d344_run_carries_the_allowance_clause_and_each_research_tool_holds_its_budget():
    """D-344 (4), CONFORM-MCP F6: keel_research_run spends a study, so it carries D-14's one clause
    (RESEARCH_ALLOWANCE_CLAUSE: no number, no plan name) as keel_backtest_run and keel_screener_run carry theirs; get
    was trimmed back to 07 §8.5's 230 to pay for it. Budgets pinned per tool: plan 300, get 230, run 255 (07 §8.5's
    230 plus the clause, inside the research total of 760; amended by D-344 (4)). SEED (reversed): drop the clause from
    run's description -> this reds; control: get and plan carry no clause."""
    import re as _re

    from keel.errors import RESEARCH_ALLOWANCE_CLAUSE
    from keel.tools.outcomes.research_cli import RESEARCH_CLI_HELP  # noqa: F401  (registry import)

    snapshot = _by_name(listed_surface_snapshot(None, research=True))
    run, get, plan = (snapshot[f"keel_research_{n}"]["description"] for n in ("run", "get", "plan"))
    assert not _re.search(r"\d", RESEARCH_ALLOWANCE_CLAUSE)
    assert run.count(RESEARCH_ALLOWANCE_CLAUSE) == 1
    assert RESEARCH_ALLOWANCE_CLAUSE not in get and RESEARCH_ALLOWANCE_CLAUSE not in plan
    assert len(plan) <= 300 and len(get) <= 230 and len(run) <= 255, (len(plan), len(get), len(run))
    assert len(plan) + len(get) + len(run) <= 760
