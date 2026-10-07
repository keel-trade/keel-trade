"""Bundled Anthropic Agent Skills for the Keel MCP / CLI surface.

Each `keel/skills/<name>/SKILL.md` is a markdown file with YAML frontmatter
(`name`, `description`, `references`, `tools`) and a body with five
required sections (Method / Decision points / Output shape / When NOT to
use / Test prompts).

**A skill is METHOD, not a library** (guidance-architecture spec §3 L3).
At session start only `name + description` is exposed; activation loads
the method and a plain REFERENCE INDEX — one line per Reference topic the
method cites, naming the step it belongs to and what the topic carries.
Nothing is appended: `dsl_syntax` and `composition_mechanics` alone are
~3.3k tokens, so any skill that inlined its references could not meet
the 5,000-token activation budget.

**Method layer, user-picked** (mcp-conversion 05 §3.2, D-13 L2/L3). Third
parties reach a skill only as an MCP prompt the user picks, or through
the `keel_help` skills catalogue. A body is procedural: no persuasion, no
commerce, no live money, no self-triggering, no unrequested writes; the
index is a list of Reference topics, never an instruction to fetch them,
and every topic it names must be one the MCP serves.

Frontmatter carries ONE always-on field. `description` says what the
skill does, when to use it, and when not to; :attr:`Skill.trigger` is
DERIVED from its "Use when / Use after" sentence onward, so the index
paths (`keel skills list`, the published skills manifest, the MCP
prompt docstring) keep a populated trigger with one owner for the text
and no second string to drift. Likewise :attr:`Skill.knowledge` is
derived from `references` — the ordered, de-duplicated topics — so every
existing reader keeps working. A legacy `trigger:` or `knowledge:` key
is a loud parse error naming its replacement, never a silent
second source of truth (`.claude/rules/lessons.md`, "Never Add Silent
Fallbacks in Parsers").

See spec §11 in `projects/agent-v2/03-ideal-experience-spec.md` for the
original design, and `projects/fable/mcp-strategy-view/
GUIDANCE-ARCHITECTURE-SPEC.md` §3 L3 for the method-plus-index shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import yaml


@dataclass(frozen=True)
class Reference:
    """One entry of a skill's reference index.

    Attributes:
        step: The method step this reference belongs to (0 = whole skill).
        topic: A `keel_help` topic — a bundled doc stem, or `rule:<CODE>`.
        why: What the topic carries, in one clause.
    """

    step: int
    topic: str
    why: str


# The sentence that opens a description's "when" half. `trigger` is the
# description from here on, so the two can never disagree.
_TRIGGER_RE = re.compile(r"\bUse (?:when|after)\b")


@dataclass(frozen=True)
class Skill:
    """A parsed bundled skill.

    Attributes:
        name: Canonical skill name (matches the filename stem).
        description: The one always-on field — what the skill does, when
            to use it, and when not to.
        references: The reference index: the Reference topics the method
            cites, each a topic `keel_help` serves.
        tools: List of MCP tool names this skill orchestrates.
        body: The markdown body (Method / Decision points / etc.).
    """

    name: str
    description: str
    references: tuple[Reference, ...]
    tools: tuple[str, ...]
    body: str

    @property
    def trigger(self) -> str:
        """The "when to use / when not" half of `description`.

        Derived, never stored: one owner for the text (the index paths
        and the manifest render this, the activation path renders the
        whole description). Empty only if a description omits the
        "Use when / Use after" sentence, which the skill-frontmatter
        test forbids.
        """
        match = _TRIGGER_RE.search(self.description)
        return self.description[match.start() :].strip() if match else ""

    @property
    def knowledge(self) -> tuple[str, ...]:
        """Reference topics, in order, de-duplicated.

        The shape every pre-`references` reader expects (`keel_help`'s
        skill index, the surface-routing checker).
        """
        seen: dict[str, None] = {}
        for ref in self.references:
            seen.setdefault(ref.topic, None)
        return tuple(seen)


# Public list of bundled skill names — also used by `keel skills list`
# and the MCP prompts registration. Order is the §11.2 canonical order.
BUNDLED_SKILLS = (
    "strategy-creation",
    "strategy-fork-and-iterate",
    "backtest-and-analyze",
    "overfit-check",
    "deploy-and-monitor",
    "portfolio-review",
    "component-discovery",
    "recover-from-error",
)


def _skills_dir():
    """Return a Traversable pointing at the bundled skills directory."""
    return resources.files("keel.skills")


def _read_skill_file(name: str) -> str:
    """Read a bundled skill by name (`<name>/SKILL.md` — the portable
    Agent Skills layout, agent-distribution spec 02 R1)."""
    ref = _skills_dir().joinpath(name).joinpath("SKILL.md")
    try:
        return ref.read_text()
    except FileNotFoundError as exc:
        available = list_skills()
        raise FileNotFoundError(
            f"Unknown skill '{name}'. Available: {sorted(available.keys())}"
        ) from exc


def _parse(raw: str, name: str) -> Skill:
    """Split frontmatter from body and build a Skill."""
    if not raw.startswith("---"):
        raise ValueError(f"Skill '{name}' missing YAML frontmatter (no leading ---)")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Skill '{name}' has malformed frontmatter (need two --- fences)")
    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    for retired, replacement in (
        ("trigger", "`description` (its 'Use when ...' sentence onward is the trigger)"),
        ("references", None),
        ("knowledge", "`references:` — a list of {step, topic, why}"),
    ):
        if replacement is None:
            continue
        if retired in fm:
            raise ValueError(
                f"Skill '{name}' frontmatter carries retired key '{retired}:'. "
                f"It folded into {replacement}. Two sources for one string is how "
                f"they drift — remove the key."
            )
    raw_refs = fm.get("references")
    if not raw_refs:
        raise ValueError(
            f"Skill '{name}' frontmatter has no 'references:' index. A skill is "
            f"method plus an index of what it left out; an empty index means the "
            f"dropped sections are unreachable."
        )
    references = []
    for entry in raw_refs:
        if not isinstance(entry, dict) or "topic" not in entry:
            raise ValueError(
                f"Skill '{name}': reference entry {entry!r} is not a {{step, topic, why}} mapping"
            )
        references.append(
            Reference(
                step=int(entry.get("step", 0)),
                topic=str(entry["topic"]).strip(),
                why=str(entry.get("why", "")).strip(),
            )
        )
    return Skill(
        name=fm.get("name", name),
        description=(fm.get("description") or "").strip(),
        references=tuple(references),
        tools=tuple(fm.get("tools") or ()),
        body=body,
    )


@lru_cache(maxsize=None)
def load_skill(name: str) -> Skill:
    """Parse and return a single skill by name."""
    return _parse(_read_skill_file(name), name)


@lru_cache(maxsize=1)
def list_skills() -> dict[str, Skill]:
    """Return all bundled skills, name → Skill. Cached."""
    out: dict[str, Skill] = {}
    for name in BUNDLED_SKILLS:
        out[name] = load_skill(name)
    return out


# ── Profile-scoped fences (agent-surface AS-19 / Q-1452) ─────────────────
#
# One SKILL.md is served on two profiles whose tool surfaces differ
# (`full` = CLI / local MCP; `listed` = the hosted endpoint — see
# `keel.tools.outcomes._toolsets`). Every listed tool also exists on
# full, so a body written against the listed tools is true everywhere;
# guidance that only makes sense where a local workspace or the
# live-write tools exist is fenced:
#
#     <!-- profile: full -->
#     On the CLI / local MCP, `keel_strategy_checkout` ...
#     <!-- /profile -->
#
# `compose_skill` drops a fenced block whose tag is not the active
# profile and always drops the fence lines themselves, so a raw reader
# of the file (the public mirror, an Agent Skills registry) still sees
# one coherent document. Fences do not nest, a tag must be a real
# profile name, and an unclosed fence is a parse error — never a silent
# fallback (`.claude/rules/lessons.md`, "Never Add Silent Fallbacks").
_PROFILE_OPEN_RE = re.compile(r"^\s*<!--\s*profile:\s*([a-z]+)\s*-->\s*$")
_PROFILE_CLOSE_RE = re.compile(r"^\s*<!--\s*/profile\s*-->\s*$")


def render_body_for_profile(body: str, profile: str, *, name: str = "<skill>") -> str:
    """Return `body` with every fence resolved for `profile`.

    Lines inside a fence tagged with another profile are dropped; the
    fence lines themselves are always dropped; everything else is kept
    verbatim. Runs of three or more newlines left by a dropped block
    collapse to one blank line. Raises ``ValueError`` on an unknown
    profile or tag, a nested fence, a stray close, or an unclosed open.
    """
    from keel.tools.outcomes._toolsets import _VALID_PROFILES

    if profile not in _VALID_PROFILES:
        raise ValueError(
            f"Unknown server profile {profile!r} for skill '{name}'. "
            f"Valid: {', '.join(_VALID_PROFILES)}."
        )
    kept: list[str] = []
    active: str | None = None
    for line_no, line in enumerate(body.splitlines(), 1):
        opened = _PROFILE_OPEN_RE.match(line)
        if opened:
            if active is not None:
                raise ValueError(
                    f"Skill '{name}' line {line_no}: profile fence opened inside "
                    f"an open '{active}' fence (fences do not nest)"
                )
            tag = opened.group(1)
            if tag not in _VALID_PROFILES:
                raise ValueError(
                    f"Skill '{name}' line {line_no}: unknown profile tag {tag!r}. "
                    f"Valid: {', '.join(_VALID_PROFILES)}."
                )
            active = tag
            continue
        if _PROFILE_CLOSE_RE.match(line):
            if active is None:
                raise ValueError(
                    f"Skill '{name}' line {line_no}: profile fence closed without an open"
                )
            active = None
            continue
        if active is None or active == profile:
            kept.append(line)
    if active is not None:
        raise ValueError(f"Skill '{name}': '{active}' profile fence opened and never closed")
    text = "\n".join(kept)
    if body.endswith("\n"):
        text += "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


def render_reference_index(skill: Skill) -> str:
    """Render a skill's reference index as markdown.

    A plain list: one line per entry — the step it belongs to, the topic
    name, and what the topic carries. It states which Reference topics the
    workflow cites and says nothing about fetching them (mcp-conversion
    05 R-L3: nothing served tells the model to fetch guidance). The
    topics are `keel_help` topic names, which the heading says once.
    """
    lines = [
        "# References",
        "",
        "Reference topics this workflow cites (keel_help topics):",
        "",
    ]
    for ref in sorted(skill.references, key=lambda r: (r.step,)):
        step = f"Step {ref.step}" if ref.step else "Any step"
        why = f" — {ref.why}" if ref.why else ""
        lines.append(f"- {step} — `{ref.topic}`{why}")
    return "\n".join(lines) + "\n"


def compose_skill(name: str, *, profile: str | None = None) -> str:
    """Return the fully-composed skill content for activation.

    Format:
        <frontmatter block>
        <reference index>
        <skill body>

    **Method plus index, never an appended library** (guidance spec §3
    L3). Reference topics are NOT inlined: the index lists each one and
    the step it belongs to; `keel_help` serves a topic when an agent asks
    for it. That is what keeps activation inside the 5,000-token budget
    that inlining `dsl_syntax` + `composition_mechanics` alone would
    blow, and it is why the old "Tool Surface Note" is gone — the note
    existed to disclaim chat tool names inside the appended corpus, and the
    served corpus names none of them since mcp-conversion 05 §2.

    The body is rendered for ``profile`` (default: the active
    ``KEEL_SERVER_PROFILE``, see :func:`render_body_for_profile`), and on
    the listed profile the re-emitted frontmatter ``tools:`` list is
    restricted to ``LISTED_PROFILE_TOOLS`` — the same allow-list that
    decides registration, so the served text and the served tool
    surface cannot disagree (AS-19 / Q-1452).
    """
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS, server_profile

    skill = load_skill(name)
    active_profile = profile if profile is not None else server_profile()
    tools = skill.tools
    if active_profile == "listed":
        tools = tuple(t for t in tools if t in LISTED_PROFILE_TOOLS)
    fm_lines = [
        "---",
        f"name: {skill.name}",
        f"description: |\n  {_indent(skill.description, '  ')}",
        "references:",
    ]
    for r in skill.references:
        fm_lines.append(f"  - step: {r.step}")
        fm_lines.append(f"    topic: {_yaml_scalar(r.topic)}")
        fm_lines.append(f"    why: {_yaml_scalar(r.why)}")
    fm_lines.append("tools:")
    fm_lines.extend(f"  - {t}" for t in tools)
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)

    body = render_body_for_profile(skill.body, active_profile, name=name)
    return frontmatter + "\n\n" + render_reference_index(skill) + "\n" + body


def _yaml_scalar(text: str) -> str:
    """Quote a reference `why` so the re-emitted frontmatter round-trips."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _indent(text: str, prefix: str) -> str:
    """Indent every line after the first by `prefix` (YAML block scalar)."""
    lines = text.splitlines() or [""]
    return ("\n" + prefix).join(lines)


__all__ = [
    "BUNDLED_SKILLS",
    "Reference",
    "Skill",
    "compose_skill",
    "list_skills",
    "load_skill",
    "render_body_for_profile",
    "render_reference_index",
]
