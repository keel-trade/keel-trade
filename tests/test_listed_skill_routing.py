"""Guidance served on the listed profile must route somewhere reachable.

Two guards, both about the same failure: the surface tells an agent to go
somewhere the surface does not have.

**Q-1445 — skill bodies naming an excluded skill.**
`LISTED_EXCLUDED_SKILLS` is read in exactly two places
(`mcp/server.py::_register_skill_prompts`, `outcomes/help.py::_skill_names`)
and both decide MEMBERSHIP. `compose_skill` runs no text pass, so prose in a
served skill naming a hidden sibling ships verbatim and the agent that
follows it gets `not_found`. The skills were authored as one set for the only
profile that then existed, where the cross-reference IS the routing; the
listed profile subtracted a member and allow-lists cannot see prose.

**AS-17 — instructions pointing at `prompts/list` and nothing else.**
The listed profile exists for clients that expose tools only (claude.ai,
ChatGPT — `agent-first-keel/research/05` §1, §4). Naming `prompts/list` there
is fine as an ALTERNATIVE; naming it as the only way to reach a skill is a
dead end on the exact surface those strings are for. So every listed-surface
string that mentions prompts must also carry the tool path in the same
string.

Both guards scan the COMPOSED / SERVED text, not the source files, because
the composed body re-emits the YAML `trigger` (which the `keel_help` index
deliberately drops) and one of Q-1445's five occurrences lived there.

**Q-1458 — the prompts-pointer guard must judge each POINTER, not each
served string.** `server.instructions` is the blocks `assemble.instructions()`
emits from `operating_core.md`, joined with a blank line, and
`keel_strategy_compose`'s
description carries two pointers (strategy-creation, then the fork skill).
A per-string check let either half regress to prompts-only while the other
half still satisfied it — two seeds proved it (entry Q-1458). So the AS-17
arms below judge (a) each authored block of the instructions on its own and
(b) each skill NAME a string mentions on its own: a named skill must have its
own `skill:<that name>` tool path in the same string. The three pointers
AS-17 rests on are additionally pinned by identity.

Each guard ships a control arm that proves the scanner fires at all, and a
non-vacuity arm that proves the scanned population is non-empty — on
quantities the seeded defect cannot move.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from keel.mcp.server import LISTED_EXCLUDED_SKILLS
from keel.skills import BUNDLED_SKILLS, compose_skill, load_skill, render_body_for_profile
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS, SERVER_PROFILE_ENV
from keel.tools.outcomes.help import _skill_names

from pipeline_engine.reference.system import assemble


_bootstrap()


@pytest.fixture
def listed_profile(monkeypatch):
    monkeypatch.setenv(SERVER_PROFILE_ENV, "listed")


def _served_bodies() -> dict[str, str]:
    """name → composed body, for every skill this profile serves."""
    return {name: compose_skill(name) for name in _skill_names()}


# ─── Q-1445: no served body names a skill this profile hides ────────────


def test_served_skill_population_is_non_empty(listed_profile):
    """Non-vacuity for the sweep below, on quantities a seeded prose
    reference cannot move: the served set, the hidden set, and the size
    of each composed body."""
    served = _served_bodies()
    assert served, "the listed profile serves no skills — the sweep would be vacuous"
    assert LISTED_EXCLUDED_SKILLS, (
        "LISTED_EXCLUDED_SKILLS is empty — the sweep would have nothing to look for"
    )
    assert set(LISTED_EXCLUDED_SKILLS) <= set(BUNDLED_SKILLS), (
        "the exclusion list names a skill that does not exist"
    )
    assert set(served).isdisjoint(LISTED_EXCLUDED_SKILLS), (
        "an excluded skill is itself being served — membership filtering is broken"
    )
    for name, body in served.items():
        assert len(body) > 500, f"{name}: composed body is {len(body)} chars — did it compose?"


def test_no_served_skill_body_names_an_excluded_skill(listed_profile):
    """Q-1445. Guidance that names a hidden skill is a dead end: the agent
    calls `keel_help(topic="skill:<it>")` and gets `not_found`."""
    violations: list[str] = []
    for name, body in _served_bodies().items():
        for line_no, line in enumerate(body.splitlines(), 1):
            for hidden in sorted(LISTED_EXCLUDED_SKILLS):
                if hidden in line:
                    violations.append(
                        f"{name}:{line_no} names hidden skill {hidden}: {line.strip()}"
                    )
    assert not violations, (
        "a skill served on the listed profile routes to a skill that profile hides "
        "(route to the Keel web app handoff instead — AS-14):\n" + "\n".join(violations)
    )


def test_the_excluded_skills_own_body_does_trip_the_scanner():
    """CONTROL arm. Same scanner, a body that legitimately contains the
    name. Without this, a scanner that matched nothing at all would make
    the sweep above pass for the wrong reason."""
    found: list[str] = []
    for hidden in sorted(LISTED_EXCLUDED_SKILLS):
        body = compose_skill(hidden)
        if hidden in body:
            found.append(hidden)
    assert found == sorted(LISTED_EXCLUDED_SKILLS), (
        "the scanner failed to find a skill's own name inside its own composed body — "
        f"it detects nothing, so the sweep proves nothing. Found: {found}"
    )


def test_the_excluded_skill_is_genuinely_unreachable_through_the_tool(listed_profile):
    """Why the dead end is a dead end, pinned rather than assumed."""
    from keel.errors import KeelError
    from keel.tools.outcomes._base import ToolContext

    handler = OUTCOMES["keel_help"].handler
    for hidden in sorted(LISTED_EXCLUDED_SKILLS):
        with pytest.raises(KeelError) as exc:
            handler({"topic": f"skill:{hidden}"}, ToolContext())
        assert exc.value.error_code == "not_found"


# ─── AS-17: a prompts pointer always carries the tool path with it ──────

_PROMPT_CHANNEL_TOKENS = ("prompts/list", "prompts/get", "MCP prompt")


def _names_the_tool_path(owner: str, text: str) -> bool:
    """The tool path is `keel_help` with `topic="skill:<name>"`. A string
    must carry the topic form, and — unless it IS `keel_help`'s own
    description, where naming itself would be noise — the tool name too."""
    if "skill:" not in text:
        return False
    return owner.startswith("keel_help.") or "keel_help" in text


def _listed_strings() -> dict[str, str]:
    """Every string the listed profile serves that can carry a pointer:
    the server instructions plus each tool's description and parameter
    descriptions, read off a real FastMCP server."""
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        tools = await server.list_tools()
        out = {"server.instructions": server.instructions or ""}
        for tool in tools:
            out[f"{tool.name}.description"] = tool.description or ""
            props = (tool.parameters or {}).get("properties") or {}
            for pname, pschema in props.items():
                out[f"{tool.name}.param[{pname}]"] = pschema.get("description", "")
        return out

    return asyncio.run(go())


def test_listed_surface_strings_are_non_empty(listed_profile):
    """Non-vacuity, on quantities the seeded defect cannot move: the
    server registers the listed tool set and publishes instructions."""
    strings = _listed_strings()
    names = {k.split(".", 1)[0] for k in strings if k != "server.instructions"}
    assert names == set(LISTED_PROFILE_TOOLS), (
        f"listed surface drifted — unexpected: {sorted(names - LISTED_PROFILE_TOOLS)}, "
        f"missing: {sorted(LISTED_PROFILE_TOOLS - names)}"
    )
    assert len(strings["server.instructions"]) > 500, "server instructions are empty or stub"


def _pointer_units(strings: dict[str, str]) -> dict[str, str]:
    """The served strings, with `server.instructions` split into the
    blank-line-separated blocks it is composed from (`assemble.instructions`
    joins one block per emitted `operating_core.md` section with "\n\n"), so
    each authored block is judged on its own — Q-1458 seed S7 was one block
    regressed to prompts-only while another block's last line kept the
    concatenation green."""
    units: dict[str, str] = {}
    for owner, text in strings.items():
        if owner == "server.instructions":
            for i, block in enumerate(text.split("\n\n")):
                units[f"server.instructions[block {i}]"] = block
        else:
            units[owner] = text
    return units


def test_every_prompts_pointer_also_names_the_tool_path(listed_profile):
    """AS-17. `prompts/list` is unavailable on the clients this profile
    exists for, so it may appear only ALONGSIDE the `keel_help`
    `topic="skill:<name>"` route — never as the only way there.

    Judged per POINTER UNIT (Q-1458): a tool string, or one authored block
    of the server instructions. A block that says "see prompts/list" with no
    tool path is a dead end on a tools-only client whatever the blocks
    around it say.
    """
    units = _pointer_units(_listed_strings())
    assert len([u for u in units if u.startswith("server.instructions[")]) >= 3, (
        "server.instructions did not split into its authored blocks — the per-block "
        "judgement is not happening"
    )
    mentions = {
        owner: text
        for owner, text in units.items()
        if any(token in text for token in _PROMPT_CHANNEL_TOKENS)
    }
    assert mentions, (
        "no listed-surface string mentions the prompt channel at all — this guard "
        "is scanning nothing. If the pointers were deliberately removed, delete "
        "this test rather than letting it pass vacuously."
    )
    violations = [
        owner for owner, text in mentions.items() if not _names_the_tool_path(owner, text)
    ]
    assert not violations, (
        "these listed-surface strings point at the prompt channel without naming the "
        'tool path (`keel_help` with `topic="skill:<name>"`) in the same string, so a '
        "tools-only client is sent nowhere (AS-17): " + ", ".join(sorted(violations))
    )


def test_every_named_skill_carries_its_own_tool_path(listed_profile):
    """Q-1458, the per-pointer rule. A string that names a skill is a
    pointer to that skill, so it must carry `skill:<that name>` — the one
    form `keel_help` accepts — for EACH name it mentions. Seed S6 (the
    compose fork pointer reworded to "invoke the strategy-fork-and-iterate
    MCP prompt") left every per-string check green because the same
    description names `skill:strategy-creation` elsewhere; per name, it is
    a miss."""
    units = _pointer_units(_listed_strings())
    served = set(_skill_names())
    assert served, "no skills served — nothing to point at"
    naming = {
        owner: [name for name in BUNDLED_SKILLS if name in text] for owner, text in units.items()
    }
    naming = {owner: names for owner, names in naming.items() if names}
    # None since R-L3 finished (mcp-conversion 05 §2, lane B2c): the head's
    # pointer left the instructions, and `keel_help`'s description and
    # `topic` parameter now describe the catalogue (`topic="skills"`,
    # `skill:<name>`) without naming one skill. The per-name rule below
    # still binds any string that regresses to naming one.
    assert not naming, f"listed strings name a specific skill (05 R-L3): {naming}"
    # Non-vacuity, on quantities no seed moves: the sweep read every listed
    # tool, and the name matcher fires on a string that does name a skill.
    assert len(units) > len(LISTED_PROFILE_TOOLS), len(units)
    probe = f"see `skill:{sorted(served)[0]}`"
    assert [name for name in BUNDLED_SKILLS if name in probe], probe
    violations = [
        f"{owner} names {name} without `skill:{name}`"
        for owner, names in naming.items()
        for name in names
        if f"skill:{name}" not in units[owner]
    ]
    assert not violations, (
        "a listed-surface string points at a skill without that skill's own tool "
        "path (AS-17, Q-1458):\n" + "\n".join(sorted(violations))
    )


def test_the_three_as17_pointers_each_name_the_tool_path(listed_profile):
    """The three pointers AS-17 says decide whether a tools-only agent
    loads a skill, pinned BY IDENTITY rather than through any concatenation
    (Q-1458): the instructions HEAD and the PULL index as their own corpus
    sections (and served verbatim), the compose description's
    strategy-creation pointer, and the same description's fork pointer.

    The pin moved off `_MCP_SURFACE` on 2026-09-22: the hand-written
    instruction blocks were replaced by `operating_core.md` sections
    assembled by `pipeline_engine.reference.system.assemble` (guidance
    spec §4), so the block that carries the skill pointer is now the head
    and the block that carries the generic tool path is the pull index.
    Re-pinned 2026-09-23 (agent-surface-cleanup spec 01 §2.1/§2.5): the
    pull index left the instructions, so the generic path is pinned on
    `keel_help`'s description, and compose carries ONE skill pointer. The
    claim being pinned is unchanged.
    """
    strings = _listed_strings()
    compose = strings["keel_strategy_compose.description"]

    # (1a) the head, on its own — not the joined instructions. Since
    # mcp-conversion 05 §3.2 (R-L3, "nothing we serve tells the model to
    # fetch guidance") the head carries NO skill pointer: skills are reached
    # through the channels the user controls (MCP prompts) and through
    # keel_help's own catalogue (1b).
    head = assemble.head()
    assert head in strings["server.instructions"], (
        "the head is not served verbatim — this pin would be on a dead string"
    )
    assert "skill:" not in head and "keel_help" not in head, (
        "the head points the model at a skill again (mcp-conversion 05 §3.2)"
    )
    # (1b) the generic `topic=<name>` route. The PULL index left the
    # instructions (agent-surface-cleanup spec 01 §2.1: `keel_help` with no
    # topic IS the index, and its description says so), so the pin moves to
    # the one string every tools-only client reads for it.
    pull = strings["keel_help.description"]
    assert 'topic="skill:<name>"' in pull and '`topic="skills"`' in pull, (
        "keel_help's description no longer gives the tool path to the skills and to the skills list"
    )

    # (2) the compose tool carries NO skill pointer (Q-1947, 2026-09-25):
    # ChatGPT's approval gate read it as the tool "specifying validation/help
    # methods" and showed a Suspicious Instruction warning on every save. The
    # head (1) and keel_help's description (1b) are the route.
    assert 'topic="skill:' not in compose, (
        "keel_strategy_compose names a skill route again — ChatGPT flags it (Q-1947)"
    )


def test_the_skill_route_is_named_by_the_instructions_not_the_compose_tool(listed_profile):
    """AS-17's operative claim — one call reaches the right skill because the
    skill NAME is in the pointer — rides keel_help's own catalogue, the one
    channel that lists skills on request. Neither the instructions
    (mcp-conversion 05 §3.2, R-L3) nor compose's description (Q-1947:
    ChatGPT's approval gate flagged it on every save) push a skill."""
    strings = _listed_strings()
    compose = strings["keel_strategy_compose.description"]
    instructions = strings["server.instructions"]
    assert "skill:" not in instructions
    assert "skill:strategy-creation" not in compose
    assert '`topic="skills"` lists the agent skills' in strings["keel_help.description"]


def test_a_tools_only_client_reaches_the_named_skill_in_one_call(listed_profile):
    """End to end through a real server: the exact call the compose
    description tells the agent to make returns the skill body."""
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        result = await server.call_tool("keel_help", {"topic": "skill:strategy-creation"})
        return json.loads(result.content[0].text)

    envelope = asyncio.run(go())
    assert envelope["skill"] == "strategy-creation", envelope
    assert envelope["source"] == "skill"
    # Method plus index (guidance spec §3 L3): the served body opens with
    # the reference index and carries the method, not an appended corpus.
    assert "# References" in envelope["body"]
    assert "# Method" in envelope["body"]


# ─── Q-1452 / AS-19: no served body names a tool this profile does not register ───
#
# The same failure as Q-1445 one level down: `LISTED_PROFILE_TOOLS` decides
# which TOOLS register, `compose_skill` used to run no text pass, so prose
# written for the CLI / local MCP (where `keel_strategy_checkout`,
# `keel_strategy_push`, `keel_live_control`, … all exist) shipped verbatim on
# the hosted profile that has none of them. AS-19: the hosted path is the
# body's primary path (every listed tool exists on full, so it is true
# everywhere), and the local-workspace / live-write guidance is fenced
# `<!-- profile: full -->` and dropped by the serving path on `listed`.

_TOOL_NAME_RE = re.compile(r"\bkeel_[a-z0-9_]+\b")
_FENCE_MARKERS = ("<!-- profile:", "<!-- /profile")
_SKILLS_MANIFEST = (
    Path(__file__).resolve().parents[3]
    / "services"
    / "keel-site"
    / "public"
    / ".well-known"
    / "skills"
    / "index.json"
)


def _tool_names_in(text: str) -> set[str]:
    return set(_TOOL_NAME_RE.findall(text))


def test_no_served_skill_body_names_an_unregistered_tool(listed_profile):
    """Q-1452 / AS-19. A `keel_*` name in a served body is an instruction;
    on the listed profile it must be a tool that profile registers. A name
    that is a tool elsewhere is a dead end ("tool not available"); a name
    that is no tool at all is a dead end everywhere."""
    violations: list[str] = []
    for name, body in _served_bodies().items():
        for line_no, line in enumerate(body.splitlines(), 1):
            for ref in sorted(_tool_names_in(line)):
                if ref in LISTED_PROFILE_TOOLS:
                    continue
                why = (
                    "registered only on the full profile"
                    if ref in OUTCOMES
                    else "not an outcome tool at all"
                )
                violations.append(f"{name}:{line_no} names {ref} ({why}): {line.strip()}")
    assert not violations, (
        "a skill served on the listed profile names a tool that profile does not "
        "register (AS-19: route to the hosted equivalent, state the app handoff, or "
        "fence the block `<!-- profile: full -->`):\n" + "\n".join(violations)
    )


def test_the_served_population_matches_the_registry_and_the_manifest(listed_profile):
    """Non-vacuity for the sweep above, on quantities a seeded tool name
    cannot move: how many skills are served, that each names tools at all,
    and that the served set is exactly what the prompt surface and the
    published manifest say minus the one exclusion list."""
    served = _served_bodies()
    expected = set(BUNDLED_SKILLS) - set(LISTED_EXCLUDED_SKILLS)
    assert len(served) == len(BUNDLED_SKILLS) - len(LISTED_EXCLUDED_SKILLS)
    assert set(served) == expected

    from keel.mcp.server import create_server

    async def prompt_names():
        return {p.name for p in await create_server().list_prompts()}

    assert asyncio.run(prompt_names()) == set(served), (
        "the tool path and prompts/list disagree on which skills the listed profile serves"
    )

    per_skill = {name: _tool_names_in(body) for name, body in served.items()}
    for name, names in per_skill.items():
        assert names, f"{name}: names no keel_* tool at all — the scanner had nothing to read"
    scanned = set().union(*per_skill.values())
    assert len(scanned) >= 5, f"only {sorted(scanned)} tool names across every served body"

    if _SKILLS_MANIFEST.exists():  # monorepo checkout — the public mirror has no keel-site
        manifest = {s["name"] for s in json.loads(_SKILLS_MANIFEST.read_text())["skills"]}
        assert manifest == set(BUNDLED_SKILLS), "the published skills manifest is stale"
        assert set(served) == manifest - set(LISTED_EXCLUDED_SKILLS)


def test_the_full_profile_bodies_do_trip_the_scanner(monkeypatch):
    """CONTROL arm. The same scanner over the same skills composed for the
    FULL profile finds the local-workspace and live-write tools, so the
    listed green above is the fence working — not a scanner that matches
    nothing, and not bodies that stopped mentioning those tools."""
    monkeypatch.delenv(SERVER_PROFILE_ENV, raising=False)
    found: dict[str, set[str]] = {}
    for name in BUNDLED_SKILLS:
        names = {
            ref
            for ref in _tool_names_in(compose_skill(name))
            if ref in OUTCOMES and ref not in LISTED_PROFILE_TOOLS
        }
        if names:
            found[name] = names
    assert found, (
        "no full-profile body names a non-listed tool — either the scanner detects "
        "nothing or the local-workspace guidance was deleted instead of fenced"
    )
    # The fence, not the environment, is what decides: explicit profile argument.
    fenced = next(iter(found))
    ref = sorted(found[fenced])[0]
    assert ref in compose_skill(fenced, profile="full")
    assert ref not in compose_skill(fenced, profile="listed")


@pytest.mark.parametrize("profile", ["full", "listed"])
def test_no_fence_marker_survives_composition(profile):
    """The fences are for the serving path; an agent must never see one."""
    for name in BUNDLED_SKILLS:
        body = compose_skill(name, profile=profile)
        for marker in _FENCE_MARKERS:
            assert marker not in body, (
                f"{name} ({profile}): fence marker leaked into the served body"
            )


def test_fences_are_in_use_and_parse_strictly():
    """Non-vacuity: at least one bundled skill carries a fence (otherwise the
    two tests above prove nothing about fencing). Strictness: every
    malformed fence is a loud parse error, never a silent pass-through."""
    fenced = [n for n in BUNDLED_SKILLS if _FENCE_MARKERS[0] in load_skill(n).body]
    assert fenced, "no bundled SKILL.md carries a profile fence"

    doc = "a\n<!-- profile: full -->\nb\n<!-- /profile -->\nc\n"
    assert render_body_for_profile(doc, "listed", name="t") == "a\nc\n"
    assert render_body_for_profile(doc, "full", name="t") == "a\nb\nc\n"

    for bad, why in (
        ("<!-- profile: full -->\nx\n", "unclosed"),
        ("<!-- profile: hosted -->\nx\n<!-- /profile -->\n", "unknown tag"),
        ("x\n<!-- /profile -->\n", "close without open"),
        ("<!-- profile: full -->\n<!-- profile: listed -->\nx\n<!-- /profile -->\n", "nested"),
    ):
        with pytest.raises(ValueError):
            render_body_for_profile(bad, "listed", name="t")
    with pytest.raises(ValueError):
        render_body_for_profile("x\n", "hosted", name="t")


def test_the_tool_path_serves_the_listed_rendering(listed_profile):
    """End to end through a real server: what `keel_help` hands a
    tools-only client is the rendered body, not the source file."""
    from keel.mcp.server import create_server

    async def go(name: str):
        server = create_server()
        result = await server.call_tool("keel_help", {"topic": f"skill:{name}"})
        return json.loads(result.content[0].text)["body"]

    for name in _skill_names():
        body = asyncio.run(go(name))
        stray = {r for r in _tool_names_in(body) if r not in LISTED_PROFILE_TOOLS}
        assert not stray, f"{name}: keel_help served {sorted(stray)} on the listed profile"
        assert _FENCE_MARKERS[0] not in body
