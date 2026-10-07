"""`keel_help` — fetch a knowledge doc by topic.

Per spec §13.6: hosts that don't browse MCP resources well can hit
this tool to retrieve the same content that backs registered knowledge
and DSL reference resources.

Lookups are bundled-only and offline (Q-0573 /
cli-help-topic-miss-no-normalization): the planned Phase 2C
`/v1/reference/{topic}` keel-api endpoint never shipped, so the old
network fallback was a guaranteed 404 round-trip on every miss.
Topic names are normalized (case, `-`/space → `_`) and a miss suggests
the closest bundled topics instead of calling the API.

Two reserved topics route into the bundled agent skills instead of
`keel.data` (spec 01 R2 — skills reachable through a TOOL, so a
tools-only client that never calls `prompts/list` can still load
them): `skills` lists them, `skill:<name>` returns
`compose_skill(name)`. The listed profile hides exactly the skills
`_register_skill_prompts` hides, reading the SAME
`LISTED_EXCLUDED_SKILLS` constant — never a second copy of the list.

Two more reach the VALIDATION CATALOG — the explain channel of
`GUIDANCE-ARCHITECTURE-SPEC.md` §3 L3, Rust's `--explain` for a
validation issue: `rules` indexes every code, `rule:<CODE>` serves one
code's message, suggestion and lesson. The source is
`pipeline_engine.dsl.catalog.RULES`, imported directly — the SDK
VENDORS `pipeline_engine` (`pyproject.toml` `packages = [...]`), so
there is ONE catalog, not a bundled JSON copy that could drift from the
validator whose errors it explains (decision #35 / review E1). The
freshness guard is a byte-identity test between the vendored
`pipeline_engine/dsl/catalog.py` and the monorepo's, not a regeneration
step someone has to remember. 49 KB of `explain` prose was unreachable
from every agent surface before this.
"""

from __future__ import annotations

import difflib
import re
from importlib import resources

from keel import __version__ as _sdk_version
from keel.errors import KeelError
from keel.hosting import record_outcome

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


_BUNDLED_KNOWLEDGE_DIRS = ("knowledge", "reference", "patterns")


def _normalize_topic(topic: str) -> str:
    """Canonical bundled-doc slug: lowercase snake_case.

    Users and agents reasonably guess `strategy-phases`, `Strategy Phases`,
    or `STRATEGY_PHASES` for `strategy_phases.md` — separators and case
    carry no meaning, so fold them instead of missing."""
    return topic.strip().lower().replace("-", "_").replace(" ", "_")


def _bundled_index() -> dict[str, tuple[str, str]]:
    """Normalized slug → (stem as the index lists it, subdir).

    Resolution goes through the same normalization as the request so the
    two sides cannot disagree: `_list_bundled_topics` advertises the RAW
    filename stem, the request is folded by `_normalize_topic`, and a raw
    stem that is not already canonical (`platform-operations.md`, the one
    hyphenated file in the corpus — Q-1456) used to be listed but never
    fetchable. Folding the filename too closes that gap for every file,
    present and future, without renaming anything in the shared corpus
    (`libs/pipeline_engine/reference/system`, which chat-api also loads).
    """
    index: dict[str, tuple[str, str]] = {}
    for subdir in _BUNDLED_KNOWLEDGE_DIRS:
        try:
            dir_ref = resources.files("keel.data").joinpath(subdir)
            if not dir_ref.is_dir():
                continue
            for entry in dir_ref.iterdir():
                if entry.name.endswith(".md"):
                    stem = entry.name[:-3]
                    index.setdefault(_normalize_topic(stem), (stem, subdir))
        except (FileNotFoundError, ModuleNotFoundError):
            continue
    return index


def _try_bundled(topic: str) -> tuple[str, str, str] | None:
    """Fetch a bundled doc by (already normalized) slug.

    Returns ``(body, subdir, listed_stem)`` — ``listed_stem`` is the
    spelling the index advertises and `keel://knowledge/{section}`
    resolves (``load_section`` opens ``{section}.md`` verbatim), so the
    result echoes THAT, never the folded request."""
    index = _bundled_index()
    hit = index.get(topic)
    if hit is None:
        # A doc's own declared name (Q-2504): a pattern doc says
        # `<!-- pattern: entry_exit -->`, the loaders and the docs' prose cite
        # it by that name, and it resolves to the file. A file name always
        # wins (the alias table never shadows an index key).
        alias = _declared_aliases(index).get(topic)
        hit = index.get(alias) if alias is not None else None
    if hit is None:
        return None
    stem, subdir = hit
    try:
        ref = resources.files("keel.data").joinpath(subdir).joinpath(f"{stem}.md")
        return ref.read_text(encoding="utf-8"), subdir, stem
    except (FileNotFoundError, ModuleNotFoundError):
        return None


_DECLARED_NAME = re.compile(r"<!--\s*pattern:\s*([A-Za-z0-9_\-]+)\s*-->")


def _read_bundled(stem: str, subdir: str) -> str:
    try:
        ref = resources.files("keel.data").joinpath(subdir).joinpath(f"{stem}.md")
        return ref.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return ""


def _declared_aliases(index: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Declared-name slug → the index key of the doc that declares it.

    Read off the docs themselves (the first lines' ``<!-- pattern: x -->``),
    never a hand table, so the corpus describes itself (Q-2504). A declared
    name equal to some index key adds nothing — the file name wins.
    """
    aliases: dict[str, str] = {}
    for key, (stem, subdir) in index.items():
        head = "\n".join(_read_bundled(stem, subdir).splitlines()[:5])
        match = _DECLARED_NAME.search(head)
        if match is None:
            continue
        alias = _normalize_topic(match.group(1))
        if alias != key and alias not in index:
            aliases.setdefault(alias, key)
    return aliases


def _docs_mentioning(term: str, limit: int = 3) -> list[str]:
    """The bundled docs that mention ``term`` as a word, best first (Q-2504).

    A concept with no doc named after it (``Execution``, ``Globals``) is
    still documented somewhere; a miss names where. Separators fold like the
    topic itself (``top volume`` matches ``top_volume``). Ranked by a hit in
    a heading or a bold definition first, then by count. Empty when nothing
    mentions it — the caller still raises not_found; nothing is guessed.
    """
    words = [w for w in re.split(r"[\s_\-]+", term.strip()) if w]
    if not words:
        return []
    pattern = re.compile(
        r"(?<![A-Za-z0-9])" + r"[\s_\-]+".join(map(re.escape, words)) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    scored: list[tuple[int, int, str]] = []
    for stem, subdir in _bundled_index().values():
        body = _read_bundled(stem, subdir)
        hits = pattern.findall(body)
        if not hits:
            continue
        defined = any(
            pattern.search(line)
            for line in body.splitlines()
            if line.lstrip().startswith("#") or "**" in line
        )
        scored.append((0 if defined else 1, -len(hits), stem))
    return [stem for _defined, _count, stem in sorted(scored)[:limit]]


def _bundled_resource_uri(topic: str, subdir: str) -> str | None:
    if subdir == "reference":
        return f"keel://dsl/reference/{topic}"
    if subdir == "knowledge":
        return f"keel://knowledge/{topic}"
    return None


# Reserved topics that route into `keel.skills` rather than `keel.data`.
# `skills` is asserted NOT to be a bundled doc name by
# tests/test_outcomes_skills_topic.py, so this namespace shadows nothing.
_SKILLS_TOPIC = "skills"
_SKILL_PREFIX = "skill:"


def _normalize_skill_name(name: str) -> str:
    """Canonical bundled-skill slug: lowercase kebab-case.

    The mirror of :func:`_normalize_topic` for the skill namespace —
    skill slugs are kebab-case (`strategy-creation`), so separators
    fold the other way. Same reasoning: case and separator style carry
    no meaning, so `Strategy_Creation` must not miss."""
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def _skill_names() -> tuple[str, ...]:
    """Bundled skill names this server profile exposes.

    The listed profile hides exactly what ``_register_skill_prompts``
    hides, by reading the same ``LISTED_EXCLUDED_SKILLS`` constant
    (spec 01 R2) — there is deliberately no second list here. Imports
    are function-local: ``keel.mcp.server`` pulls in fastmcp, and the
    CLI keeps that off its startup path.
    """
    from keel.mcp.server import LISTED_EXCLUDED_SKILLS
    from keel.skills import BUNDLED_SKILLS

    from ._toolsets import is_listed_profile

    excluded = LISTED_EXCLUDED_SKILLS if is_listed_profile() else frozenset()
    return tuple(name for name in BUNDLED_SKILLS if name not in excluded)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _skill_rows() -> list[dict]:
    """`name` + one-line `description` per visible skill.

    The §11 session-start shape: enough to pick one, not the body. The
    field set is deliberately IDENTICAL to what `_register_skill_prompts`
    publishes (name + one-line description), so the tool path and
    `prompts/list` agree on content as well as membership. `trigger` is
    excluded on purpose: some triggers cross-reference by name a skill
    the listed profile hides, and an index that rendered them would
    point a listed agent at a skill it cannot load. Triggers stay
    available through `keel skills list` and inside the composed body.
    """
    from keel.skills import load_skill

    rows = []
    for name in _skill_names():
        skill = load_skill(name)
        rows.append({"name": skill.name, "description": _one_line(skill.description)})
    return rows


def _render_skill_rows(rows: list[dict]) -> str:
    """Markdown rendering of the skill list, for hosts that show text."""
    lines = [
        "# Keel agent skills",
        "",
        'Call `keel_help` again with `topic="skill:<name>"` to load one in full.',
        "",
    ]
    for row in rows:
        lines.append(f"- **{row['name']}** — {row['description']}")
    return "\n".join(lines) + "\n"


def _skills_index_result() -> OutcomeResult:
    rows = _skill_rows()
    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        extra={
            "topic": _SKILLS_TOPIC,
            "source": "skills",
            "skills": rows,
            "body": _render_skill_rows(rows),
            "info": (
                f"{len(rows)} bundled skills. Call again with "
                '`topic="skill:<name>"` to load one in full — the same '
                "content this server registers as MCP prompts, reachable "
                "as a tool call when a client cannot list prompts."
            ),
        },
    )


def _skill_result(requested: str) -> OutcomeResult:
    """Fetch one composed skill, or raise `not_found`.

    A skill hidden on this profile is indistinguishable from one that
    does not exist: it is absent from `available` and therefore from
    the did-you-mean suggestions too."""
    from keel.skills import compose_skill

    name = _normalize_skill_name(requested)
    available = list(_skill_names())
    if name not in available:
        close = difflib.get_close_matches(name, available, n=3, cutoff=0.5)
        did_you_mean = f"Did you mean: {', '.join(close)}? " if close else ""
        raise KeelError(
            f"Skill not found: {requested!r}.",
            error_code="not_found",
            exit_code=3,
            suggestion=(
                f"{did_you_mean}Known skills: {', '.join(available)}. "
                'Call `keel_help` with `topic="skills"` for the full list.'
            ),
        )
    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        extra={
            "topic": f"{_SKILL_PREFIX}{name}",
            "skill": name,
            "source": "skill",
            "body": compose_skill(name),
        },
    )


# ── The explain channel: `rules` and `rule:<CODE>` ──────────────────────
#
# Reserved like `skills` / `skill:` and asserted (in
# tests/test_help_rule_explain.py) not to collide with a bundled doc stem.
_RULES_TOPIC = "rules"
_RULE_PREFIX = "rule:"

# The placeholder a skill's reference index carries when the code is only
# known at runtime ("the lesson behind whatever code the dry run returns").
RULE_TOPIC_PLACEHOLDER = "rule:<CODE>"

# Related pull-tier topics by validator pass — a small static map beside the
# channel, never a hand-typed row per rule (94+ rules, one drift source each).
# Passes are the catalog's own `passes` field; the families follow the
# validator's pass semantics, checked against the live grouping. Every topic
# named here is a SERVED doc: `component_versioning` (chat-tool protocol)
# moved to Keel's in-app corpus (mcp-conversion 05 §3.1) and is no longer a
# topic this server can return, so the lock/version passes point at the DSL
# reference alone.
_RELATED_BY_PASS: dict[str, tuple[str, ...]] = {
    # Pass 0 is the PARSER (Q-1695, 2026-09-22): the tier migration lands in.
    # `dsl_syntax` is the grammar it enforces; `strategy_paths` is where the
    # four-declaration shape the legacy form migrates TO is taught.
    "0": ("dsl_syntax", "strategy_paths"),
    "pre": ("dsl_syntax",),
    "1": ("dsl_syntax",),
    "2": ("dsl_syntax",),
    "3": ("dsl_syntax",),
    "4": ("dsl_syntax",),
    # Pass 4u is the position-layer upgrade offer (POSITION_UPGRADE_AVAILABLE,
    # spec 04-R9): a deprecated position group and its TradeManager rewrite.
    # Spec 05 names `entry_exit_patterns` as its topic once lane L5 serves it;
    # until then the composition and mistakes references are the honest pair.
    "4u": ("composition_mechanics", "mistakes"),
    "5": ("dsl_syntax", "mistakes"),
    "6": ("types", "composition_mechanics"),
    "7": ("mistakes", "composition_mechanics"),
    "8": ("slots", "composition_mechanics"),
    "9": ("dsl_syntax", "universe_selection"),
    "9.resampler": ("composition_mechanics", "data_loading"),
}
# Rules the catalog gives no pass (blob, lock, parse and runtime codes): the
# DSL reference plus the structural-mistake catalog is the honest pair.
_RELATED_DEFAULT: tuple[str, ...] = ("dsl_syntax", "mistakes")


def _normalize_rule_code(code: str) -> str:
    """Canonical catalog code: UPPER_SNAKE.

    The mirror of :func:`_normalize_topic` for the rule namespace. An
    agent pasting `rule:type-mismatch` out of prose must not miss a code
    the validator spells `TYPE_MISMATCH`.
    """
    return code.strip().upper().replace("-", "_").replace(" ", "_")


def _enum_value(value) -> str:
    """Render a catalog enum (or plain string) as its wire value."""
    return str(getattr(value, "value", value))


def _rule_stage(rule) -> str:
    """The staging stage of a rule's severity ramp: the catalog's own word.

    A DORMANT code is declared and never emitted (structural silence,
    catalog spec 05 §2.2), so `severity_for` REFUSES to resolve one — a
    dormant rule has no severity the validator would ever report. The
    channel still serves it, with `severity: null` and the stage said out
    loud, because an agent that reads a code in a spec or a draft should
    learn that it cannot fire yet rather than get a miss.

    Enumerated, not caught: only "no staging key" and the kind-(c)
    flow-shape entries (which carry no severity by design) are stages of
    their own. Any other resolution failure is a catalog defect and is
    allowed to raise.
    """
    from pipeline_engine.dsl.catalog import STAGED_CHANGES

    key = rule.staged_by
    if not key:
        return "promoted"
    change = STAGED_CHANGES.get(key)
    if change is None or change.kind == "flow-shape":
        return "promoted"
    return _enum_value(change.stage)


def _related_topics(rule) -> tuple[str, ...]:
    """Pull-tier topics to read beside this code, in pass order."""
    out: dict[str, None] = {}
    for pass_id in rule.passes:
        for topic in _RELATED_BY_PASS.get(str(pass_id), ()):
            out.setdefault(topic, None)
    if not out:
        for topic in _RELATED_DEFAULT:
            out.setdefault(topic, None)
    return tuple(out)


def explain_for(code: str) -> dict:
    """Return the catalog's full explanation of one validation issue code.

    The ONE owner of the explain channel's payload (W5 P6): `keel_help`
    renders it here and any other surface that explains a code reads the
    same function, so a code cannot mean two things on two surfaces.

    `severity` is the value the validator would actually report —
    `severity_for`, with the rule's override and ceiling applied — not
    the raw category, because the category is what an agent gets wrong.
    `suggestion_template` is returned with its placeholders UNRENDERED:
    the caller already holds the rendered one on the issue, and the
    template is what tells it which fields the suggestion depends on.

    Raises ``KeyError`` for an unknown code; the tool path turns that
    into a `not_found` envelope with close matches.
    """
    from pipeline_engine.dsl.catalog import RULES, severity_for

    rule = RULES[_normalize_rule_code(code)]
    stage = _rule_stage(rule)
    severity = None if stage == "dormant" else severity_for(rule)
    payload = {
        "topic": f"{_RULE_PREFIX}{rule.code}",
        "source": "rule",
        "code": rule.code,
        "category": _enum_value(rule.category),
        "severity": severity,
        "stage": stage,
        "status": _enum_value(rule.status),
        "summary": rule.summary,
        "message_template": rule.message_template,
        "suggestion_template": rule.suggestion_template,
        "template_params": list(rule.template_params),
        "explain": rule.explain,
        "applicability": _enum_value(rule.applicability),
        "passes": [str(p) for p in rule.passes],
        "surfaces": [_enum_value(sf) for sf in rule.surfaces],
        "promote_in_production": bool(rule.promote_in_production),
        "recoverable": bool(rule.recoverable),
        "related": list(_related_topics(rule)),
    }
    payload["body"] = _render_rule_body(payload)
    return payload


def _render_rule_body(rule: dict) -> str:
    """Markdown rendering for text-showing hosts (as `_render_skill_rows`)."""
    severity = rule["severity"] or "not emitted"
    lines = [f"# {rule['code']} — {severity} ({rule['category']})", ""]
    if rule["stage"] == "dormant":
        lines += [
            "This code's severity ramp is **dormant**: it is declared and the "
            "validator never emits it yet, so you will not meet it in a result.",
            "",
        ]
    if rule["status"] != "active":
        lines += [
            f"This code is **{rule['status']}**; it is served so an older error in a "
            "pasted trace still explains itself.",
            "",
        ]
    lines += [rule["summary"], ""]
    lines.append(f"**Message:** {rule['message_template']}")
    if rule["suggestion_template"]:
        lines.append(f"**Suggestion:** {rule['suggestion_template']}")
    if rule["promote_in_production"]:
        lines.append(
            "**Note:** a warning while you iterate; an error once the strategy runs in production."
        )
    lines += ["", "## Why", "", rule["explain"].strip(), "", "## Related", ""]
    lines += [f'- `keel_help(topic="{topic}")`' for topic in rule["related"]]
    return "\n".join(lines) + "\n"


def _rules_index_result() -> OutcomeResult:
    """Every code with its severity and one-line summary.

    The role `skills` plays for skills: an agent that has a SYMPTOM and
    no code can find the code, and one that has a code can see it exists
    before spending a call on it.
    """
    from pipeline_engine.dsl.catalog import RULES, severity_for

    rows = []
    for _code, rule in sorted(RULES.items()):
        stage = _rule_stage(rule)
        rows.append(
            {
                "code": rule.code,
                "severity": None if stage == "dormant" else severity_for(rule),
                "stage": stage,
                "category": _enum_value(rule.category),
                "status": _enum_value(rule.status),
                "summary": rule.summary,
            }
        )
    lines = [
        "# Keel validation issue codes",
        "",
        'Call `keel_help` again with `topic="rule:<CODE>"` for one code\'s message, '
        "suggestion and lesson.",
        "",
    ]
    lines += [
        f"- **{r['code']}** — {r['severity'] or 'not emitted'} · {r['summary']}" for r in rows
    ]
    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        extra={
            "topic": _RULES_TOPIC,
            "source": "rules",
            "rules": rows,
            "body": "\n".join(lines) + "\n",
            "info": (
                f"{len(rows)} validation issue codes. Every issue a compose or push "
                'result carries names one; `topic="rule:<CODE>"` explains it.'
            ),
        },
    )


def _rule_result(requested: str) -> OutcomeResult:
    """Explain one code, or raise `not_found` with close matches.

    A `deprecated` or `reserved` code is FOUND — its `status` says so and
    the body opens with it — so an old error in a pasted trace still
    explains itself instead of looking like a typo.
    """
    from pipeline_engine.dsl.catalog import RULES

    code = _normalize_rule_code(requested)
    try:
        payload = explain_for(code)
    except KeyError:
        known = sorted(RULES)
        close = difflib.get_close_matches(code, known, n=3, cutoff=0.5)
        did_you_mean = f"Did you mean: {', '.join(close)}? " if close else ""
        raise KeelError(
            f"Rule not found: {requested!r}.",
            error_code="not_found",
            exit_code=3,
            suggestion=(
                f"{did_you_mean}Known codes include: {', '.join(known[:20])}. "
                f'Call `keel_help` with `topic="rules"` for the full index. '
                f"This is keel-trade {_sdk_version}; a code added after it was "
                f"published is not in its catalog."
            ),
        ) from None
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=payload)


#: The doc reference a no-topic call records: it was served the listing.
_INDEX_DOC_REF = "(index)"


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    """Serve the topic, then record WHICH document was served (Q-2377).

    The audit row gets the resolved, canonical name the result already
    carries in ``extra["topic"]`` (``capability_boundaries``,
    ``skill:strategy-creation``, ``rule:TYPE_MISMATCH``), or ``(index)`` for
    the no-topic listing — never the ``topic`` argument the caller typed.

    Why this does not break the Directory rule "events never carry
    arguments" (agent-funnel-measurement spec 02 principle 7): the served
    name is Keel's own resource id, drawn from the bundled index, the skill
    list or the rule catalog, exactly as a ``strategy_id`` is Keel's id for
    the thing a call touched. It exists only because the lookup HIT, so it is
    Keel's vocabulary by construction; ``Capability Boundaries`` and
    ``capability-boundaries`` both record ``capability_boundaries``. A MISS
    raises before anything is recorded, because a miss's only "name" is the
    caller's free text. Founder ruling 2026-10-04: the audit row only — this
    value is deliberately not mirrored to PostHog (``mcp_tool_called``).
    """
    result = _resolve(args)
    if not args.get("topic", "").strip():
        record_outcome(doc_ref=_INDEX_DOC_REF)
    else:
        served = result.extra.get("topic") if isinstance(result.extra, dict) else None
        if isinstance(served, str):
            record_outcome(doc_ref=served)
    return result


def _resolve(args: dict) -> OutcomeResult:
    topic = args.get("topic", "").strip()
    if not topic:
        # Return the list of bundled topics so the agent can pick the
        # next call without a round-trip. Previously raised
        # missing_topic, which forced agents to interrogate the user
        # for a topic name instead of just surfacing what's available.
        docs = sorted(_list_bundled_topics())
        skills = list(_skill_names())
        return OutcomeResult(
            run_id=None,
            hero_url=None,
            share_url=None,
            extra={
                # `skills` and `rules` are real topics, so they belong in
                # the one list an agent picks its next call from.
                "topics": sorted([*docs, _SKILLS_TOPIC, _RULES_TOPIC]),
                # One line per topic (review 06 M-5 #3): the listing used to
                # return bare names, so a description that said "lists every
                # topic with one line each" was false, and trimming the
                # description's per-topic index would lose it. Additive —
                # `topics` keeps its shape.
                "topic_index": topic_index(docs),
                "skills": skills,
                "info": (
                    "No topic specified — listing what this tool can fetch. "
                    "Call again with `topic=<name>` to fetch one. The "
                    f"{len(docs)} bundled docs cover the DSL reference, "
                    "platform knowledge, and composition patterns; "
                    f'`topic="skills"` lists the {len(skills)} bundled agent '
                    'skills and `topic="skill:<name>"` loads one in full; '
                    f'`topic="rules"` lists the {_rule_count()} validation '
                    'issue codes and `topic="rule:<CODE>"` explains one.'
                ),
            },
        )

    # Skill namespace first (spec 01 R2). `_normalize_topic` folds
    # `-` → `_`, which would mangle a kebab-case skill slug, so the
    # skill routes are matched on the raw topic and normalized by
    # `_normalize_skill_name` instead.
    if topic.lower() == _SKILLS_TOPIC:
        return _skills_index_result()
    if topic.lower().startswith(_SKILL_PREFIX):
        return _skill_result(topic[len(_SKILL_PREFIX) :])

    # Rule namespace, matched on the raw topic for the same reason: a code
    # is UPPER_SNAKE and `_normalize_topic` would lowercase it.
    if topic.lower() == _RULES_TOPIC:
        return _rules_index_result()
    if topic.lower().startswith(_RULE_PREFIX):
        return _rule_result(topic[len(_RULE_PREFIX) :])

    # Bundled lookup on the normalized slug (works without auth; the only
    # storage — there is no API fallback, see module docstring).
    normalized = _normalize_topic(topic)
    if normalized == _OPERATING_CORE_TOPIC:
        return _operating_core_result()
    bundled = _try_bundled(normalized)
    if bundled is not None:
        body, subdir, listed = bundled
        return OutcomeResult(
            run_id=None,
            hero_url=None,
            share_url=None,
            resource_uri=_bundled_resource_uri(listed, subdir),
            extra={
                "topic": listed,
                "body": body,
                "source": "bundled",
            },
        )

    # Miss: instant + offline. Say where the word IS documented (Q-2504),
    # keep a did-you-mean for near-typos only, and list EVERY topic (the
    # old `[:20]` cut hid 13 of 33). Still not_found: nothing is guessed.
    available = sorted([*_list_bundled_topics(), _SKILLS_TOPIC, _RULES_TOPIC])
    close = difflib.get_close_matches(normalized, available, n=3, cutoff=0.75)
    did_you_mean = f"Did you mean: {', '.join(close)}? " if close else ""
    mentioned = _docs_mentioning(topic)
    mentioned_in = f"Mentioned in: {', '.join(mentioned)}. " if mentioned else ""
    raise KeelError(
        f"Help topic not found: {topic!r}.",
        error_code="not_found",
        exit_code=3,
        suggestion=f"{did_you_mean}{mentioned_in}Known topics: {', '.join(available)}",
    )


#: The corpus source. Its served form is the BASE document, never the raw
#: file: the file also carries Keel's chat opinion layer (`register:
#: opinion`), which no MCP surface serves (agent-surface-cleanup spec 01
#: §2.1.1, R-5). The byte-identity pin between the libs source and the
#: vendored copy stays on the FILE (`load_operating_core`).
_OPERATING_CORE_TOPIC = "operating_core"


def _operating_core_result() -> OutcomeResult:
    from keel.data.knowledge import served_section

    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        resource_uri=f"keel://knowledge/{_OPERATING_CORE_TOPIC}",
        extra={
            "topic": _OPERATING_CORE_TOPIC,
            "body": served_section(_OPERATING_CORE_TOPIC),
            "source": "bundled",
        },
    )


def _rule_count() -> int:
    """How many validation issue codes the channel serves right now.

    Read off the live catalog, never a literal: a count that can go stale
    is the one number an agent would quote back at a user.
    """
    from pipeline_engine.dsl.catalog import RULES

    return len(RULES)


# The served docs are the SHARED layer of the corpus (mcp-conversion 05
# §3): Keel's in-app-only guidance lives under `chat/` and never reaches this
# wheel, and the shared text names none of the in-app assistant's tools
# (05 §2 rule 2).
# A doc result therefore carries no `info` note: the one it carried mapped the
# in-app assistant's tool names onto `keel_*` for text that named them, and
# listing those names is itself a pointer at tools this surface lacks.


def _doc_summary(stem: str) -> str | None:
    """A bundled doc's one line: its first markdown heading, text only."""
    hit = _try_bundled(_normalize_topic(stem))
    if hit is None:
        return None
    for raw in hit[0].splitlines():
        line = raw.strip()
        if line.startswith("#"):
            text = line.lstrip("#").strip()
            if text:
                return text
    return None


def topic_index(docs: list[str]) -> list[dict[str, str | None]]:
    """`[{topic, summary}]` for every topic the no-topic listing names —
    the bundled docs by their first heading, then the two index topics."""
    rows: list[dict[str, str | None]] = [
        {"topic": stem, "summary": _doc_summary(stem)} for stem in sorted(docs)
    ]
    rows.append(
        {
            "topic": _SKILLS_TOPIC,
            "summary": "the bundled agent skills, one line each; skill:<name> loads one",
        }
    )
    rows.append(
        {
            "topic": _RULES_TOPIC,
            "summary": "the validation issue codes; rule:<CODE> explains one",
        }
    )
    return rows


def _list_bundled_topics() -> list[str]:
    """Every bundled doc, by the stem the index advertises.

    Read off the same index the fetch path resolves through, so a topic
    this returns is fetchable by construction (Q-1456)."""
    return [stem for stem, _subdir in _bundled_index().values()]


HELP = register(
    OutcomeTool(
        name="keel_help",
        required_action="audit.read",
        cli_path=("help",),
        toolset="always",
        # grounded-in: system/chat/tool_usage.md:21-25 ("Tool Usage Guide" —
        # reference docs are pulled on demand, not recalled) + :31-39 ("Don't
        # Falsely Claim a Tool is Missing" — orient from the real surface,
        # don't guess). Pull-on-demand pointer for the always-loaded description.
        description=(
            "Fetch one Keel knowledge or DSL-reference document by topic — components are "
            "`keel_components_search`, one component's contract "
            "`keel_components_get`. With no `topic` it lists the topic names. "
            '`topic="skills"` lists the agent skills (also served as MCP prompts) and '
            '`topic="skill:<name>"` returns one in full; '
            '`topic="rule:<CODE>"` explains a validation issue code and `rules` lists '
            "them."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic slug. Optional; when omitted, the tool returns the list of "
                        "available topics. Case and separators are normalized (`dsl-syntax` "
                        "== `dsl_syntax`). Examples: `dsl_syntax`, `strategy_patterns`, "
                        "`mistakes`, `types`, `slots`. Reserved namespaces: `skills` lists "
                        "the bundled agent skills and `skill:<name>` returns one in full; "
                        "`rules` lists every "
                        "validation issue code and `rule:<CODE>` explains one (for example "
                        "`rule:TYPE_MISMATCH`)."
                    ),
                    "x-cli-positional": True,
                },
            },
        },
        annotations={
            "title": "Get Help Topic",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
