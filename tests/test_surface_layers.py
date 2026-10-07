"""Agent-surface layer guards G2, G3, G5, G6, G7 (05 §6; closes Q-2081).

05-agent-surface-layers.md splits what Keel serves into four layers — Contract,
Reference, Method, Opinion/in-app — each with ONE channel. These guards make
that hold on the LISTED profile, which is what the hosted directory
registration runs (``_surface_fixtures.listed_surface``), plus the runtime
payloads real SDK paths produce (``_surface_fixtures.runtime_payloads``):

* **G2 — vendored = served = reference.** ``keel_help``'s topic list and the
  ``keel://knowledge`` / ``keel://dsl/reference`` reads that resolve are
  exactly the pinned served set; the vendored ``keel/data`` corpus is exactly
  the LAYERS.yaml ``reference`` + ``sectioned`` set, byte-identical to source;
  nothing under a chat/ name is vendored.
* **G3 — the Reference register.** No served Reference body, and no
  instructions / tool description / prompt description, addresses the model
  as a conversational assistant (``AGENT_CONDUCT_PATTERNS``, calibrated on the
  305 labelled sections — see the helper module).
* **G5 — pointer integrity.** Every ``keel_help(topic=…)`` / ``topic=`` /
  ``keel://`` / catalogue pointer anywhere a client can read resolves to a
  served topic; no ``skill:`` pointer outside keel_help's own catalogue.
* **G6 — tool-name integrity.** Served text and results name only tools on the
  listed ``tools/list`` — neither an absent ``keel_*`` tool nor a Keel-chat
  tool (read from the chat-api / pipeline_engine registries, never a literal).
* **G7 — skills are method only.** The 7 listed prompt bodies carry no
  commerce or live money, no self-trigger, no unrequested write, and no relay
  of plan-wall fields.

G4 (commerce + live money) lives in ``test_policy_scan.py`` beside the rest of
the listed-surface string rules.

RED at the base commit e3591375c by design — the corpus, runtime and skills
lanes turn them green. Evidence and the per-guard offender lists:
``keel-artifacts/projects/fable/mcp-conversion/2026-09-28-directory-resubmit/
guards-red-at-base/``. Each guard carries an in-file control arm (a seeded
text that must red it and a clean twin that must not), so a green verdict is
about the surface, not a blind scanner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from . import _surface_fixtures as sf
from ._surface_fixtures import (
    NOT_SERVED_TOPICS,
    SERVED_DOC_TOPICS,
    SERVED_KNOWLEDGE_TOPICS,
    SERVED_REFERENCE_TOPICS,
    conduct_hits,
    extract_pointers,
    family_hits,
    is_exempt,
    normalize_topic,
    pointer_violation,
    sentences,
    tool_copy,
    tool_name_hits,
)


monorepo = pytest.mark.skipif(
    not sf.in_monorepo(), reason="reads libs/pipeline_engine/reference (monorepo only)"
)


@pytest.fixture(scope="module")
def surface() -> sf.ServedSurface:
    return sf.listed_surface()


@pytest.fixture(scope="module")
def payloads() -> dict[str, sf.Payload]:
    return sf.runtime_payloads()


def _body_items(surface: sf.ServedSurface) -> list[tuple[str, str, str]]:
    """(topic, owner, text) for every served Reference body."""
    items = [(t, f"keel_help:{t}", body) for t, body in sorted(surface.help_docs.items())]
    for uri, body in sorted(surface.resources.items()):
        items.append((normalize_topic(uri.rsplit("/", 1)[-1]), f"resource:{uri}", body))
    return items


def _contract_items(surface: sf.ServedSurface) -> list[tuple[str, str]]:
    """(owner, text): the instructions, every tool's copy, every prompt description."""
    items = [("instructions", surface.instructions)]
    for tool in surface.tools:
        items += tool_copy(tool)
    items += [
        (f"prompt:{n}.description", p["description"]) for n, p in sorted(surface.prompts.items())
    ]
    return items


def _payload_items(payloads: dict[str, sf.Payload]) -> list[tuple[str, str]]:
    return [
        (f"payload:{name}", text) for name, p in sorted(payloads.items()) for text in p.strings()
    ]


def _catalogue_items(surface: sf.ServedSurface) -> list[tuple[str, str]]:
    """keel_help's own listings — the one place a skill pointer belongs."""
    items = [
        (f"keel_help.listing.{k}", v) for k, v in surface.help_listing.items() if isinstance(v, str)
    ]
    items += [
        (f"keel_help.skills.{k}", v) for k, v in surface.help_skills.items() if isinstance(v, str)
    ]
    return items


# ═══ G2 — vendored = served = reference ═════════════════════════════════


def test_g2_keel_help_serves_exactly_the_pinned_topics(surface):
    """# SEED: a chat/ file listed as `reference` in LAYERS.yaml (or any
    in-app file vendored) reappears here as an unexpected topic."""
    docs = surface.help_topics() - sf.HELP_NAMESPACES
    unexpected = sorted(docs - SERVED_DOC_TOPICS)
    missing = sorted(SERVED_DOC_TOPICS - docs)
    assert not unexpected and not missing, (
        f"keel_help topics drifted from the pinned served set — in-app only but served: "
        f"{unexpected}; pinned but not served: {missing}"
    )
    # Non-vacuity: the pinned list is the whole Reference layer, and a shared
    # file (control) is present in it.
    assert len(SERVED_DOC_TOPICS) == 31
    assert "types" in docs and "operating_core" in docs
    assert not (SERVED_DOC_TOPICS & NOT_SERVED_TOPICS)


def test_g2_resource_reads_resolve_exactly_the_pinned_topics(surface):
    knowledge = surface.resource_topics("knowledge")
    reference = surface.resource_topics("dsl/reference")
    assert knowledge == SERVED_KNOWLEDGE_TOPICS, (
        f"keel://knowledge reads — in-app only but served: {sorted(knowledge - SERVED_KNOWLEDGE_TOPICS)}; "
        f"pinned but refused: {sorted(SERVED_KNOWLEDGE_TOPICS - knowledge)}"
    )
    assert reference == SERVED_REFERENCE_TOPICS, sorted(reference ^ SERVED_REFERENCE_TOPICS)
    # Non-vacuity: every corpus stem (both layers, both spellings) was probed
    # on both templates, and a known-good read resolved (control).
    assert len(surface.probed) >= 2 * 35
    assert "keel://dsl/reference/types" in surface.resources


def _vendored_relpaths() -> set[str]:
    return {
        p.relative_to(sf.VENDORED_DATA).as_posix()
        for sub in ("knowledge", "reference", "patterns")
        for p in (sf.VENDORED_DATA / sub).rglob("*.md")
    }


def _vendored_path_for(source_rel: str) -> str:
    """LAYERS.yaml source path → where build_data.py vendors it."""
    parts = Path(source_rel).parts
    if parts[0] == "system" and len(parts) == 2:
        return f"knowledge/{parts[1]}"
    if parts[0] == "patterns" and len(parts) == 2:
        return f"patterns/{parts[1]}"
    if len(parts) == 1:
        return f"reference/{parts[0]}"
    raise AssertionError(f"{source_rel}: no vendored home (a chat/ file must never be vendored)")


def _manifest_files() -> dict[str, str]:
    import yaml

    assert sf.LAYERS_MANIFEST.is_file(), (
        "libs/pipeline_engine/reference/LAYERS.yaml is missing — the vendored set cannot be "
        "checked against a declaration that does not exist (G1)"
    )
    return yaml.safe_load(sf.LAYERS_MANIFEST.read_text(encoding="utf-8"))["files"]


@monorepo
def test_g2_vendored_corpus_is_exactly_the_shared_layer():
    files = _manifest_files()
    shared = sorted(rel for rel, layer in files.items() if layer in ("reference", "sectioned"))
    expected = {_vendored_path_for(rel) for rel in shared}
    vendored = _vendored_relpaths()
    assert vendored == expected, (
        f"vendored but not shared: {sorted(vendored - expected)}; "
        f"shared but not vendored: {sorted(expected - vendored)}"
    )
    # Byte-identical: what ships is what the corpus says.
    drift = [
        rel
        for rel in shared
        if (sf.REFERENCE_ROOT / rel).read_bytes()
        != (sf.VENDORED_DATA / _vendored_path_for(rel)).read_bytes()
    ]
    assert not drift, f"vendored copies differ from source (re-run build_data.py): {drift}"
    assert len(shared) >= 31


@monorepo
def test_g2_served_topics_are_the_manifests_shared_layer():
    """The pinned list and the declaration agree — neither may drift alone."""
    files = _manifest_files()
    shared = {
        normalize_topic(Path(rel).stem)
        for rel, layer in files.items()
        if layer in ("reference", "sectioned")
    }
    assert shared == SERVED_DOC_TOPICS, sorted(shared ^ SERVED_DOC_TOPICS)
    opinion = {rel for rel, layer in files.items() if layer == "opinion"}
    assert opinion, "no opinion layer declared — the split has not happened"


def test_g2_nothing_in_app_only_is_vendored():
    vendored = _vendored_relpaths()
    under_chat = sorted(p for p in vendored if "chat" in Path(p).parts[:-1])
    named_in_app = sorted(p for p in vendored if normalize_topic(Path(p).stem) in NOT_SERVED_TOPICS)
    assert not under_chat and not named_in_app, (
        f"in-app-only corpus vendored into the SDK wheel: {under_chat + named_in_app}"
    )
    # Non-vacuity: the vendored corpus is there to be checked.
    assert len(vendored) >= 31


def test_g2_vendoring_map_refuses_a_chat_path():
    """Control arm for the map the vendored-set check leans on."""
    assert (
        _vendored_path_for("system/composition_mechanics.md")
        == "knowledge/composition_mechanics.md"
    )
    assert _vendored_path_for("types.md") == "reference/types.md"
    with pytest.raises(AssertionError):
        _vendored_path_for("system/chat/collaboration.md")


# ═══ G3 — the Reference register ════════════════════════════════════════


def test_g3_served_reference_bodies_carry_no_agent_conduct(surface):
    """# SEED: append "Always say so in your reply." to a shared file (e.g.
    `types.md`) — this reds naming that sentence."""
    violations: list[str] = []
    scanned = 0
    for topic, owner, text in _body_items(surface):
        scanned += len(sentences(text))
        for rule, sentence in conduct_hits(text):
            if is_exempt("G3", topic, sentence):
                continue
            violations.append(f"{owner}: [{rule}] {sentence[:160]}")
    assert not violations, (
        "served Reference bodies address the model as an assistant (in-app conduct belongs in "
        "system/chat/ — 05 §3):\n" + "\n".join(sorted(set(violations)))
    )
    # Non-vacuity, on a quantity the seed cannot move: the served corpus was
    # read, both channels. The post-split corpus is ~70% of the base one.
    assert scanned >= 3000, scanned
    assert not conduct_hits(surface.help_docs["types"]), "control: types.md is conduct-free"


def test_g3_contract_copy_carries_no_agent_conduct(surface):
    items = _contract_items(surface)
    violations = [
        f"{owner}: [{rule}] {sentence[:160]}"
        for owner, text in items
        for rule, sentence in conduct_hits(text)
    ]
    assert not violations, "contract copy addresses the model as an assistant:\n" + "\n".join(
        violations
    )
    assert len(items) >= 29 * 2, len(items)


#: Calibration controls (labelled in layer-classification/sections.md at base).
_OPINION_SENTENCES = (
    "Vague request ('build me a strategy') → Ask one clarifying question, then build the simplest viable version.",
    "Every capability-sensitive request gets ONE of five verdicts, named in your reply with its standard phrase.",
    "Call `think` before complex decisions:",
    "Proactively inform the user about available updates when relevant",
    "Suggest that ONE change, explain why, and let the user test it.",
    "Reply in the language the user wrote in, using the writing system they used.",
)
_REFERENCE_SENTENCES = (
    "Every strategy MUST end with a sizer that produces WeightSeries.",
    "A Parallel's branches each receive the current data and never see each other's output.",
    "The user wants a single gated entry, not accumulation.",
    "Bar-close semantics: a weight change takes effect at the close of the bar that triggered it.",
    "Never use future data: every signal is computed from bars that have closed.",
    "Use `VolTargetWeightConstructor` to scale positions to a volatility target.",
)


def test_g3_rule_table_separates_conduct_from_reference():
    """Control arms: the rules fire on in-app conduct and stay quiet on the
    reference register ("MUST", "never", a user-intent → component mapping)."""
    missed = [s for s in _OPINION_SENTENCES if not conduct_hits(s)]
    fired = [(s, conduct_hits(s)) for s in _REFERENCE_SENTENCES if conduct_hits(s)]
    assert not missed, f"conduct the rules miss: {missed}"
    assert not fired, f"reference the rules flag: {fired}"


@monorepo
def test_no_pending_rephrase_exemptions_remain():
    """The B2 rephrase is complete (2026-09-28): every shared sentence the
    G3/G4/G6 guards flagged was rewritten or moved to its chat companion, so
    the exemption table is empty. A new exemption is a deliberate, reviewed
    edit to this pin — never a quiet addition to the table.

    # SEED (2026-09-28): add any Exemption to PENDING_REPHRASE → red; revert
    by reversing that edit."""
    assert sf.PENDING_REPHRASE == (), [e.sentence[:80] for e in sf.PENDING_REPHRASE]


def _pointer_sources(surface, payloads) -> list[tuple[str, str]]:
    items = list(_contract_items(surface))
    items += [(f"prompt:{n}.body", p["body"]) for n, p in sorted(surface.prompts.items())]
    items += [(owner, text) for _t, owner, text in _body_items(surface)]
    items += [(f"keel_help:{t}.info", v) for t, v in sorted(surface.help_info.items())]
    items += _payload_items(payloads)
    return items


def _is_catalogue_owner(owner: str) -> bool:
    return (
        owner.startswith("keel_help.")
        or owner.startswith("keel_help.description")
        or (owner.startswith("keel_help.") and "Schema" in owner)
    )


def test_g5_every_pointer_resolves_to_a_served_topic(surface, payloads):
    """# SEED (standing): `_backtest_view.few_fills_next` at base says `see
    keel_help(topic="strategy_phases")` — a result that sends the model to an
    in-app-only file. That line reds this guard."""
    stems = sf.corpus_stems()
    violations: list[str] = []
    counted = 0
    for owner, text in _pointer_sources(surface, payloads) + _catalogue_items(surface):
        for p in extract_pointers(text):
            counted += 1
            why = pointer_violation(p, corpus_stems=stems)
            if why:
                violations.append(f"{owner}: {why} — …{p.context.strip()[:120]}…")
    assert not violations, "pointers to topics this surface does not serve (R-L3):\n" + "\n".join(
        sorted(set(violations))
    )
    assert counted >= 20, f"only {counted} pointers found — the extractor is blind"


def test_g5_no_skill_pointer_outside_the_keel_help_catalogue(surface, payloads):
    """Skills reach an agent as prompts the USER picks, and as keel_help's
    catalogue listing (the agent's choice) — never as a pointer another text
    pushes (05 R-L3, §3.2)."""
    violations = [
        f"{owner}: skill:{p.target} — …{p.context.strip()[:120]}…"
        for owner, text in _pointer_sources(surface, payloads)
        if not owner.startswith("keel_help.")
        for p in extract_pointers(text)
        if p.kind == "skill"
    ]
    assert not violations, "skill pointers outside the catalogue:\n" + "\n".join(
        sorted(set(violations))
    )
    # Non-vacuity: the catalogue itself IS read and does name skills.
    catalogue = [p for _o, t in _catalogue_items(surface) for p in extract_pointers(t)]
    help_copy = [text for owner, text in _contract_items(surface) if owner.startswith("keel_help.")]
    assert any(p.kind == "skill" for p in catalogue) or any("skill:" in t for t in help_copy)


def test_g5_extractor_control_arms():
    stems = sf.corpus_stems()

    def bad(text: str) -> list[str]:
        return [
            why for p in extract_pointers(text) if (why := pointer_violation(p, corpus_stems=stems))
        ]

    assert bad('few trades (3); see keel_help(topic="strategy_phases")')
    assert bad("PULL — each is a keel_help topic=<name>: what next → strategy_phases; fees → x")
    assert bad("read keel://knowledge/collaboration")
    assert not bad('see keel_help(topic="types") and keel://dsl/reference/slots')
    assert not bad(
        "PULL — each is a keel_help topic=<name>: validation error → mistakes, rule:<CODE>"
    )
    assert not bad('`keel_help topic="rule:MISSING_SIZER"` explains it')
    assert [
        p.kind for p in extract_pointers('Method: keel_help(topic="skill:strategy-creation")')
    ] == [
        "topic",
        "skill",
    ]


# ═══ G6 — tool-name integrity ═══════════════════════════════════════════


@monorepo
def test_g6_served_text_names_only_tools_on_the_listed_surface(surface, payloads):
    """# SEED (standing at base): platform-operations says `get_usage`,
    dsl_syntax `update_strategy`, and keel_strategy_history's empty result
    `keel_strategy_push` — each reds this guard."""
    listed = surface.listed_tool_names()
    topics_by_owner = {owner: topic for topic, owner, _ in _body_items(surface)}
    violations: list[str] = []
    for owner, text in _pointer_sources(surface, payloads):
        topic = topics_by_owner.get(owner)
        for sentence in sentences(text) if topic else [text]:
            names = tool_name_hits(sentence, listed)
            if names and not (topic and is_exempt("G6", topic, sentence)):
                violations.append(f"{owner}: {sorted(set(names))} — {sentence[:120]}")
    assert not violations, (
        "served text names tools this surface does not register (R-L4):\n"
        + "\n".join(sorted(set(violations)))
    )
    # Non-vacuity: the chat registry was read from code, and the listed
    # surface is the 29-tool allow-list.
    assert len(sf.chat_tool_names()) >= 10, sorted(sf.chat_tool_names())
    assert len(listed) == 29


@monorepo
def test_g6_doc_results_carry_no_tool_mapping_note(surface):
    """The shared-corpus `info` note (which listed the in-app assistant's
    tool names for an agent to map) is gone with its G6 field exemption
    (05 §2 rule 2, lane B2c): a doc result carries no `info` line at all,
    so every served string is read by G6 above with no carve-out.
    Non-vacuity: the fixture fetched every served doc."""
    assert surface.help_info, "no doc was fetched — the check reads nothing"
    assert not any(surface.help_info.values()), {t: v for t, v in surface.help_info.items() if v}


@monorepo
def test_g6_control_arms(surface):
    listed = surface.listed_tool_names()
    assert tool_name_hits("call **`get_usage`** for the numbers", listed) == ["get_usage"]
    assert tool_name_hits("Push via `keel_strategy_push -m 'msg'`.", listed) == [
        "keel_strategy_push"
    ]
    assert not tool_name_hits(
        "keel_backtest_run(strategy_id=...) then keel_backtest_compare", listed
    )
    assert not tool_name_hits("think about it; update the strategy", listed)


# ═══ G7 — skills are method only ════════════════════════════════════════

SELF_TRIGGER_RE = re.compile(
    r"\bwhenever\b|\bautomatically\b|\bproactively\b"
    r"|\bafter a (?:single )?run shows\b"
    r"|\bafter (?:\w+ ){0,2}(?:\d+|two|three|several) (?:consecutive )?\S*\s?errors?\b",
    re.IGNORECASE,
)
#: A write into the user's account, or a report sent to Keel, named as a step.
WRITE_RE = re.compile(
    r"\bkeel_strategy_notes_add\b|\bkeel_feedback\b|\bstrategy (?:memory|notes?)\b",
    re.IGNORECASE,
)
#: …which is fine only when the same sentence makes it the user's request.
CONSENT_RE = re.compile(
    r"\b(?:if|when|only when|once|unless) the user (?:asks|asked|wants|says|agrees|confirms|requests)"
    r"|\buser'?s? (?:say-so|consent|go-ahead|permission|request)\b|\bwith the user'?s\b"
    r"|\bif asked\b|\bon request\b|\bwhen asked\b",
    re.IGNORECASE,
)
RELAY_RE = re.compile(r"\baction_url\b|\blimit_view\b|\bpaths to more\b", re.IGNORECASE)
_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def skill_violations(name: str, description: str, body: str) -> list[str]:
    """Method-only verdicts for one listed skill.

    The rendered prompt re-emits the SKILL.md front matter (name, the
    description again, the tool allow-list, the reference rows). The
    description is judged as the description; the tool LIST is not a write
    instruction; the reference rows reappear verbatim in the body's
    "# References" index, which is judged as body. So the front matter is
    dropped before the sentence rules run.
    """
    body = _FRONT_MATTER_RE.sub("", body, count=1)
    out = [f"{name}: {fam} {m!r}" for fam, m in family_hits(description, scope="strict")]
    out += [f"{name}: {fam} {m!r}" for fam, m in family_hits(body, scope="body")]
    for text in (description, body):
        for sentence in sentences(text):
            if SELF_TRIGGER_RE.search(sentence):
                out.append(f"{name}: self-trigger — {sentence[:140]}")
            if WRITE_RE.search(sentence) and not CONSENT_RE.search(sentence):
                out.append(f"{name}: unrequested write — {sentence[:140]}")
            if RELAY_RE.search(sentence):
                out.append(f"{name}: relays a plan-wall field — {sentence[:140]}")
    return out


def test_g7_listed_skill_bodies_are_method_only(surface):
    """# SEED (standing at base): overfit-check self-triggers on Sharpe > 3 and
    says "run forward small"; recover-from-error self-triggers after three
    errors and files keel_feedback unasked; backtest-and-analyze writes
    strategy memory unasked and relays `action_url`."""
    from keel.mcp.server import LISTED_EXCLUDED_SKILLS

    violations: list[str] = []
    for name, prompt in sorted(surface.prompts.items()):
        violations += skill_violations(name, prompt["description"], prompt["body"])
    assert not violations, "listed skills carry more than method:\n" + "\n".join(
        sorted(set(violations))
    )
    # Non-vacuity: the 7 listed skills were rendered and read in full.
    assert len(surface.prompts) == 7, sorted(surface.prompts)
    assert not (set(surface.prompts) & LISTED_EXCLUDED_SKILLS)
    assert all(len(sentences(p["body"])) >= 20 for p in surface.prompts.values())


def test_g7_control_arms():
    assert skill_violations("x", "Use after three consecutive keel_* errors.", "")
    assert skill_violations("x", "", "Then `keel_strategy_notes_add(note=...)` the summary.")
    assert skill_violations("x", "", "MIXED — thesis holds in most windows; run forward small.")
    assert skill_violations("x", "", "give the user the `action_url`.")
    assert not skill_violations(
        "x", "", "When the user asks to save it, `keel_strategy_notes_add` stores one note."
    )
    assert not skill_violations("x", "", "Upgrade to Vol-Targeted Sizing once signals combine.")
