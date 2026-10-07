"""`keel_help` skill topics — skills reachable through a TOOL (spec 01 R2).

Hosts that only speak `tools/list` + `tools/call` (claude.ai connectors,
ChatGPT) never see MCP prompts, so the eight bundled Agent Skills were
unreachable there. `keel_help` gained two reserved topics:

* `topic="skills"` — the list, one line per skill;
* `topic="skill:<name>"` — `compose_skill(name)`, the full body.

The contract this file pins:

1. the reserved namespace shadows no bundled doc (non-vacuity — if a
   `skills.md` ever landed under `keel.data`, the route would silently
   hide it);
2. the LISTED profile omits EXACTLY what `_register_skill_prompts`
   omits, computed from the same `LISTED_EXCLUDED_SKILLS` constant —
   asserted as a set equality against the live prompt surface, so a
   second hard-coded list cannot creep in;
3. AC-2.1 — one `tools/call` on a listed-profile server returns the
   strategy-creation body;
4. the excluded skill is refused through the same call, and its name
   never appears in the refusal;
5. the CONTROL arm: the identical call on the full profile SUCCEEDS.
   Without it, a refusal caused by a broken skill file would read
   exactly like a refusal caused by the profile.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from click.testing import CliRunner
from keel.cli.main import cli
from keel.mcp.server import LISTED_EXCLUDED_SKILLS
from keel.skills import BUNDLED_SKILLS, compose_skill
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS, SERVER_PROFILE_ENV
from keel.tools.outcomes.help import _list_bundled_topics


_bootstrap()

HANDLER = OUTCOMES["keel_help"].handler
runner = CliRunner()


def _call_handler(topic: str | None = None) -> dict:
    args = {} if topic is None else {"topic": topic}
    return HANDLER(args, ToolContext()).to_envelope()


def _call_mcp(topic: str) -> dict:
    """Full MCP round-trip through a real FastMCP server — the path a
    tools-only client actually takes."""
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        result = await server.call_tool("keel_help", {"topic": topic})
        return json.loads(result.content[0].text)

    return asyncio.run(go())


def _list_mcp_prompt_names() -> set[str]:
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        return {p.name for p in await server.list_prompts()}

    return asyncio.run(go())


@pytest.fixture
def listed_profile(monkeypatch):
    monkeypatch.setenv(SERVER_PROFILE_ENV, "listed")


@pytest.fixture
def full_profile(monkeypatch):
    monkeypatch.delenv(SERVER_PROFILE_ENV, raising=False)


# ─── Non-vacuity ────────────────────────────────────────────────────────


def test_the_fixtures_this_file_reasons_over_are_non_empty():
    """Every assertion below is a set operation; prove the sets exist."""
    assert BUNDLED_SKILLS, "no bundled skills — every skill assertion would be vacuous"
    assert LISTED_EXCLUDED_SKILLS, (
        "LISTED_EXCLUDED_SKILLS is empty — the exclusion arm would pass by doing nothing"
    )
    assert set(LISTED_EXCLUDED_SKILLS) <= set(BUNDLED_SKILLS), (
        "the exclusion list names a skill that does not exist"
    )
    assert "keel_help" in LISTED_PROFILE_TOOLS, (
        "keel_help is not on the listed profile — the tool path would be unreachable "
        "on the very surface spec 01 R2 exists for"
    )


def test_reserved_skill_topics_shadow_no_bundled_doc():
    """`skills` / `skill:*` route into `keel.skills`, so a bundled doc by
    either name would become unreachable. Guard the collision rather
    than discover it."""
    docs = set(_list_bundled_topics())
    assert "skills" not in docs
    assert not [d for d in docs if d.startswith("skill:")]


# ─── The exclusion is one list, not two ─────────────────────────────────


def test_full_profile_lists_every_bundled_skill(full_profile):
    listed = [row["name"] for row in _call_handler("skills")["skills"]]
    assert listed == list(BUNDLED_SKILLS)


def test_listed_profile_omits_exactly_the_prompt_exclusions(listed_profile):
    full_names = set(BUNDLED_SKILLS)
    tool_names = {row["name"] for row in _call_handler("skills")["skills"]}
    assert full_names - tool_names == set(LISTED_EXCLUDED_SKILLS)


def test_tool_and_prompt_surfaces_agree_on_the_listed_profile(listed_profile):
    """The conjunction R2 actually asks for: whatever `prompts/list`
    shows, the tool path shows — and nothing more."""
    tool_names = {row["name"] for row in _call_handler("skills")["skills"]}
    assert tool_names == _list_mcp_prompt_names()


# ─── AC-2.1 — one tool call gets the body ───────────────────────────────


def test_listed_profile_returns_the_strategy_creation_body_in_one_call(listed_profile):
    env = _call_mcp("skill:strategy-creation")
    assert env["skill"] == "strategy-creation"
    assert env["source"] == "skill"
    assert env["body"] == compose_skill("strategy-creation")
    # A composed skill is frontmatter + knowledge + workflow; a stub or
    # an error envelope would be orders of magnitude shorter.
    assert len(env["body"]) > 5000
    assert env["body"].startswith("---\nname: strategy-creation")


def test_listed_profile_index_call_carries_descriptions(listed_profile):
    env = _call_mcp("skills")
    assert env["source"] == "skills"
    assert env["skills"]
    for row in env["skills"]:
        assert row["description"].strip(), f"{row['name']}: no one-line description"
    assert "# Keel agent skills" in env["body"]


# ─── The excluded skill, and the control arm that gives it meaning ──────


@pytest.mark.parametrize("excluded", sorted(LISTED_EXCLUDED_SKILLS))
def test_listed_profile_refuses_the_excluded_skill(listed_profile, excluded):
    env = _call_mcp(f"skill:{excluded}")
    assert env["code"] == "not_found", env
    # The refusal echoes the caller's own argument (that is not
    # disclosure); what it must never do is OFFER the hidden skill. The
    # known-skills roster it hands back is the offer.
    assert excluded not in env["what_was_expected"], (
        f"the refusal offers the hidden skill {excluded!r} as available: {env}"
    )


@pytest.mark.parametrize("excluded", sorted(LISTED_EXCLUDED_SKILLS))
def test_listed_profile_index_omits_the_excluded_skill(listed_profile, excluded):
    env = _call_mcp("skills")
    assert excluded not in {row["name"] for row in env["skills"]}
    assert excluded not in env["body"]


@pytest.mark.parametrize("excluded", sorted(LISTED_EXCLUDED_SKILLS))
def test_control_full_profile_serves_the_same_skill(full_profile, excluded):
    """CONTROL: identical call, only the profile differs. A green here
    proves the refusal above is caused by the PROFILE, not by a missing
    or unparseable skill file."""
    env = _call_mcp(f"skill:{excluded}")
    assert env.get("skill") == excluded, env
    assert env["body"] == compose_skill(excluded)


# ─── Normalization + routing hygiene ────────────────────────────────────


def test_skill_slug_separators_and_case_normalize(full_profile):
    for guess in (
        "skill:strategy_creation",
        "SKILL:Strategy-Creation",
        "skill:strategy creation",
    ):
        env = _call_handler(guess)
        assert env["skill"] == "strategy-creation", guess


def test_unknown_skill_suggests_close_visible_names(full_profile):
    from keel.errors import KeelError

    with pytest.raises(KeelError) as exc:
        _call_handler("skill:strategy-create")
    assert exc.value.error_code == "not_found"
    assert "strategy-creation" in exc.value.suggestion


def test_bare_help_advertises_the_skill_routes(full_profile):
    env = _call_handler()
    assert "skills" in env["topics"]
    assert env["skills"] == list(BUNDLED_SKILLS)
    assert 'topic="skill:<name>"' in env["info"]


def test_doc_topics_still_resolve(full_profile):
    """The skill namespace must not have swallowed the doc path."""
    env = _call_handler("dsl-syntax")
    assert env["topic"] == "dsl_syntax"
    assert env["source"] == "bundled"


# ─── CLI parity (one surface, spec 01 R2 / CLAUDE.md "same outcomes") ───


def test_cli_help_skills_and_skill_show_the_same_content():
    result = runner.invoke(cli, ["--format", "json", "help", "skills"])
    assert result.exit_code == 0, result.output
    assert [r["name"] for r in json.loads(result.stdout)["skills"]] == list(BUNDLED_SKILLS)

    result = runner.invoke(cli, ["--format", "json", "help", "skill:strategy-creation"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["body"] == compose_skill("strategy-creation")
