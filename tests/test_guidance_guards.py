"""The guidance-architecture guards (GUIDANCE-ARCHITECTURE-SPEC.md §5).

One file, one guard per row of that table, each running the corpus
builder's pure verdict function over the LIVE surface: the registered
tools, the bundled corpus, the composed skills, the vendored catalog.

Two rules the whole file is written to (`.claude/rules/lessons.md`,
2026-08-23):

* **every guard ships a proof it can fail.** Each test carries a
  `# SEED:` line naming the ONE-LINE edit that reds it, and the verdict
  function it calls has a seeded arm in
  `libs/pipeline_engine/reference/system/assemble_test.py` that drives
  the failure with a synthetic input. Why not seed the live file here:
  several subjects are files other lanes own in this shared checkout,
  and a seed applied to one of those collides with whoever is editing
  it. Revert a seed by REVERSING THE EDIT, never `git checkout`.
* **every guard ships a proof it is not vacuous**, read off a quantity
  the seed cannot move — how many tools were registered, how many
  sentences were scanned, how many locators resolved. A zero you cannot
  distinguish from a blind spot is worthless.

Budgets, per founder ruling 2026-09-22 (spec §2, decision #38): a HARD
limit is a host fact we can cite (a description or an instructions string
truncated at 2 KB — BYTES; the first 512 CHARACTERS of instructions read
first). Everything else is an AIM: measured and PRINTED by these tests,
never asserted.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import keel.tools.outcomes
import pytest
from keel.data.knowledge import load_section
from keel.skills import BUNDLED_SKILLS, compose_skill, load_skill
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

from pipeline_engine.reference.system import assemble


_bootstrap()

REPO_ROOT = Path(__file__).resolve().parents[4]
SDK_ROOT = Path(__file__).resolve().parents[1]


# ─── Subjects ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def listed_descriptions() -> dict[str, str]:
    """Every listed tool's EFFECTIVE description, off a real server."""
    from keel.mcp.server import create_server

    saved = {
        k: os.environ.get(k)
        for k in ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS")
    }
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    os.environ["KEEL_EXECUTION_MODE"] = "hosted"
    os.environ.pop("KEEL_TOOLSETS", None)
    try:
        server = create_server()
        tools = asyncio.run(server.list_tools())
        return {t.name: t.description or "" for t in tools}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(scope="module")
def every_description() -> dict[str, str]:
    """Every REGISTERED tool's description across both profiles.

    The 2 KB cut is Claude Code's, and Claude Code runs the full profile
    — so the hard byte limit is scanned over the whole inventory, not
    only the listed 28.
    """
    out: dict[str, str] = {}
    for name, tool in OUTCOMES.items():
        out[name] = tool.description or ""
        if tool.listed_description:
            out[f"{name} (listed)"] = tool.listed_description
    return out


def _resolve_anchor_locators() -> dict[tuple[str, ...], str]:
    """Resolve every sole-carrier locator to the LIVE string it names.

    An unresolvable locator stays out of the mapping, and
    `assemble.missing_anchors` reports it as "did not resolve" — never
    silently as "no twin".
    """
    resolved: dict[tuple[str, ...], str] = {}
    # A `core` twin resolves through the SERVED base document's sections
    # (agent-surface-cleanup spec 01 §2.1.1): an opinion section never
    # reaches an MCP host, so it cannot twin anything.
    served = {s.id: s.body for s in assemble.base_sections()}
    for locator in assemble.anchor_locators():
        kind = locator[0]
        try:
            if kind == "core":
                resolved[locator] = served[locator[1]]
            elif kind == "knowledge":
                resolved[locator] = load_section(locator[1])
            elif kind == "skill":
                resolved[locator] = compose_skill(locator[1], profile="full")
            elif kind == "tool":
                resolved[locator] = OUTCOMES[locator[1]].description or ""
            elif kind == "param":
                props = (OUTCOMES[locator[1]].input_schema or {}).get("properties") or {}
                resolved[locator] = props.get(locator[2], {}).get("description", "")
        except (KeyError, FileNotFoundError):
            continue
    return resolved


# ─── Guard: instructions head ───────────────────────────────────────────


def test_the_head_carries_its_facts_inside_512_chars_on_both_profiles():
    """Spec §5 `instructions head`. ChatGPT and Codex read the first 512
    CHARACTERS as the essentials, so every head fact must land there — on
    every profile, because the head is the same string on all of them.
    Five facts since mcp-conversion 05 §3.2 removed the skill pointer
    (R-L3: nothing we serve tells the model to fetch guidance).

    # SEED: move the defaults sentence to the end of the `head` section's
    # paragraph in libs/pipeline_engine/reference/system/operating_core.md
    # (and pad it past 512 chars). Revert by reversing that edit.
    """
    for profile in assemble.PROFILE_TAGS:
        text = assemble.instructions(profile)
        positions = assemble.head_fact_positions(text[: assemble.HEAD_MAX_CHARS])
        late = {k: v for k, v in positions.items() if v < 0}
        assert not late, f"{profile}: head facts absent from the first 512 chars: {late}"
    # Non-vacuity, on quantities the seed cannot move: there are three
    # profiles, the head is non-empty, and five facts are declared.
    assert len(assemble.PROFILE_TAGS) == 3
    assert len(assemble.head()) > 0
    assert len(assemble.HEAD_FACTS) == 5


def test_every_profile_fits_or_reports_the_instructions_byte_cut(capsys):
    """The listed profile (the directory registration) fits Claude Code's
    2 KB cut. The full profiles overrun it by design — W2 §2.4: every
    sentence past the cut has an L1 twin on the one host that truncates,
    and Claude Code additionally gets prompts and resources. Reported,
    not asserted, exactly as decision #38 requires of a non-limit."""
    listed = assemble.instructions("listed").encode("utf-8")
    assert len(listed) <= assemble.INSTRUCTIONS_MAX_BYTES, (
        f"listed instructions are {len(listed)} bytes"
    )
    for profile in ("full-hosted", "full-local"):
        size = len(assemble.instructions(profile).encode("utf-8"))
        print(f"AIM  instructions[{profile}]: {size} bytes ({size - 2048:+d} vs the 2 KB cut)")
    assert "AIM" in capsys.readouterr().out


# ─── Guard: description size (HARD bytes; aims reported) ────────────────


def test_no_description_exceeds_the_hard_byte_limit(every_description, capsys):
    """Spec §5 `description size`. Claude Code truncates a tool
    description at 2 KB and "2 KB" is BYTES — W1's compose is 1,998
    chars = 2,004 bytes, so characters are not the measure.

    # SEED: pad `keel/tools/outcomes/strategy_compose.py`'s description
    # past 2,100 bytes. Revert by reversing that edit.
    """
    report = assemble.description_size_report(every_description)
    print(f"AIM  descriptions: {report['count']} scanned, {report['total_chars']} chars total")
    for line in report["over_aim_chars"]:
        print(f"AIM  over {assemble.AIM_DESCRIPTION_CHARS} chars — {line}")
    # Non-vacuity, on a quantity no padding can move: the whole
    # registered inventory was measured.
    assert report["count"] >= 40, f"only {report['count']} descriptions scanned"
    assert all(v["bytes"] > 0 for v in report["sizes"].values()), "an empty description"
    assert not report["over_hard_bytes"], (
        "tool descriptions past Claude Code's 2 KB cut (HARD — the tail is "
        "silently dropped):\n" + "\n".join(report["over_hard_bytes"])
    )


def test_the_listed_total_is_reported_against_its_aim(listed_descriptions, capsys):
    """AIM, never asserted (decision #38): the listed total near today's,
    ≤ 22k chars including the skeleton and the help table of contents."""
    report = assemble.description_size_report(listed_descriptions)
    total = report["total_chars"]
    print(
        f"AIM  listed total: {total} chars across {report['count']} tools "
        f"(aim {assemble.AIM_LISTED_TOTAL_CHARS}; "
        f"{'over' if total > assemble.AIM_LISTED_TOTAL_CHARS else 'under'})"
    )
    assert report["count"] == len(LISTED_PROFILE_TOOLS)
    assert "AIM  listed total" in capsys.readouterr().out


# ─── Guard: sole-carrier ────────────────────────────────────────────────


def test_every_instructions_sentence_has_a_live_twin():
    """Spec §5 `sole-carrier`. claude.ai DROPS server instructions, so a
    sentence that exists only there reaches nobody on the surface we care
    about most. The twin is an ANCHOR PHRASE found VERBATIM in the named
    live string — a paraphrase is not a twin.

    # SEED: change H4's anchor phrase in `assemble.SOLE_CARRIER` from
    # "explain it in your own words" to the paraphrase "explain the view
    # yourself". Revert by reversing that edit. (The synthetic arm of the
    # same seed is `assemble_test.py::test_a_paraphrased_twin_is_not_a_twin`.)
    """
    resolved = _resolve_anchor_locators()
    violations = assemble.missing_anchors(resolved)
    assert not violations, "instructions sentences with no live twin:\n" + "\n".join(violations)
    # Non-vacuity, on quantities the seed cannot move: the table maps the
    # fourteen listed sentences (spec 01 §2.6) plus the full profiles', and
    # every locator resolved to a NON-EMPTY live string.
    assert len(assemble.SOLE_CARRIER) >= 14, f"only {len(assemble.SOLE_CARRIER)} sentences"
    assert assemble.core_twin_violations() == [], (
        "an anchor points at a sentence the instructions themselves carry — that is not a twin"
    )
    unresolved = [loc for loc in assemble.anchor_locators() if not resolved.get(loc)]
    assert not unresolved, f"locators that resolved to nothing: {unresolved}"
    assert len(resolved) >= 10, f"only {len(resolved)} distinct live strings read"


# ─── Guard: one owner per rule ──────────────────────────────────────────


def test_each_rule_id_has_exactly_one_always_on_owner():
    """Spec §5 `one owner per rule`. 25 rules were stated in up to 28
    places; the owner map is what stops that regrowing. A `layer: skill`
    or `reference` RESTATEMENT is allowed — it must match the owner's
    canonical sentence, which `rule_owner_violations` checks.

    A removal elsewhere needs a twin in a PUBLISHED string on the SAME
    profile; code comments are never counted (review B1). That half is
    the sole-carrier guard above, which reads live strings only.

    Since agent-surface-cleanup spec 01 §2.6 every owner KIND is settled
    here, against live evidence: `core:` against the corpus, `tool:` /
    `param:` against the registered descriptions (both profiles), and
    `rule:` against the vendored validator catalog — a `rule:` owner
    resolves only while its code is at WARNING or ERROR (R-30).

    # SEED (a): add `R-QUOTA` to the `sequence` section's `rules:` list in
    # operating_core.md. SEED (b): point R-CONCAT-BRANCHES-SIZED at
    # `rule:XS_BEFORE_UNIVERSE_MASK` (DORMANT on this tree) in assemble.py —
    # NORMALIZER_BEFORE_CONCAT, the earlier seed, is armed and owns it now.
    # Revert each by reversing that edit.
    """
    violations = assemble.rule_owner_violations(
        rule_severities=_catalog_severities(), carriers=_carriers()
    )
    assert violations == [], "\n".join(violations)
    # Non-vacuity: ≥ 25 rules mapped, every owner kind is exercised, and
    # the catalog resolver read both an armed and a dormant code — counted
    # from the catalog, which neither seed touches.
    assert len(assemble.RULE_OWNERS) >= 25, f"only {len(assemble.RULE_OWNERS)} rules mapped"
    kinds = {o.owner.split(":", 1)[0] for o in assemble.RULE_OWNERS}
    assert kinds == {"core", "tool", "param", "rule"}, kinds
    severities = _catalog_severities()
    assert any(v in ("warning", "error") for v in severities.values())
    assert any(v == "" for v in severities.values()), "no dormant code to resolve against"
    named = {r for s in assemble.load_sections() for r in s.rules}
    assert len(named) >= 15, f"only {len(named)} rule ids declared by sections"


def _catalog_severities() -> dict[str, str]:
    """Code → effective severity (`""` for DORMANT), off the VENDORED catalog."""
    from pipeline_engine.dsl.catalog import RULES, rules_to_jsonable

    return {c: e["severity"] for c, e in rules_to_jsonable(RULES).items() if "severity" in e}


def _carriers() -> dict[str, str]:
    """`tool:<name>` / `param:<tool>.<name>` → every profile's live text."""
    out: dict[str, str] = {}
    for name, tool in OUTCOMES.items():
        out[f"tool:{name}"] = "\n".join(t for t in (tool.description, tool.listed_description) if t)
        for schema in (tool.input_schema, tool.listed_input_schema):
            for pname, pschema in ((schema or {}).get("properties") or {}).items():
                key = f"param:{name}.{pname}"
                text = pschema.get("description", "")
                out[key] = f"{out[key]}\n{text}" if key in out else text
    return out


# ─── Guard: skill-index conservation (size reported) ────────────────────


def test_every_skill_names_every_section_it_declares(capsys):
    """Spec §5 `skill size`: the guard asserts CONSERVATION, not a size.
    A skill may move a section out of its body, but its reference index
    must still NAME it, or the content is simply gone.

    # SEED: delete one entry from `knowledge:` in
    # `keel/skills/strategy-creation/SKILL.md` while leaving its section
    # inlined — or, once G3 lands the method+index shape, drop a section
    # from the `references:` index. Revert by reversing that edit.
    """
    violations: list[str] = []
    for name in BUNDLED_SKILLS:
        skill = load_skill(name)
        composed = compose_skill(name, profile="full")
        missing = assemble.missing_index_entries(list(skill.knowledge), composed)
        if missing:
            violations.append(f"{name}: declares {missing} but the composed text never names them")
        tokens = len(composed) / 4
        print(
            f"AIM  skill[{name}]: {tokens:.0f} est-tokens "
            f"(aim {assemble.AIM_SKILL_TOKENS}; "
            f"{'over' if tokens > assemble.AIM_SKILL_TOKENS else 'under'})"
        )
    assert not violations, "\n".join(violations)
    # Non-vacuity, on quantities the seed cannot move: eight skills, each
    # composing to a real body, each declaring at least one section.
    assert len(BUNDLED_SKILLS) == 8
    for name in BUNDLED_SKILLS:
        assert len(compose_skill(name, profile="full")) > 500, f"{name} did not compose"
        assert load_skill(name).knowledge, f"{name} declares no sections — nothing to conserve"
    assert "AIM  skill[" in capsys.readouterr().out


# ─── Guard: catalog identity (the explain channel's freshness) ──────────


def test_the_vendored_rule_catalog_is_byte_identical():
    """Spec §5 `catalog identity` / decision #35. `keel_help(topic=
    "rule:<CODE>")` serves `pipeline_engine.dsl.catalog.RULES` from the
    VENDORED copy; a monorepo catalog edit that never reaches the wheel
    ships explanations that no longer match the validator's verdicts.

    # SEED: append a character to one `explain=` string in
    # `packages/keel-trade/keel-sdk/pipeline_engine/dsl/catalog.py`.
    # Revert by reversing that edit (re-run build_data.py to restore).
    """
    monorepo = REPO_ROOT / "libs" / "pipeline_engine" / "dsl" / "catalog.py"
    vendored = SDK_ROOT / "pipeline_engine" / "dsl" / "catalog.py"
    assert monorepo.exists() and vendored.exists()
    monorepo_bytes = monorepo.read_bytes()
    vendored_bytes = vendored.read_bytes()
    # Non-vacuity: both files are real and compared WHOLE, never by a
    # literal count or a prefix.
    assert len(monorepo_bytes) > 10_000, f"monorepo catalog is {len(monorepo_bytes)} bytes"
    assert len(vendored_bytes) > 10_000, f"vendored catalog is {len(vendored_bytes)} bytes"
    assert vendored_bytes == monorepo_bytes, (
        "the vendored rule catalog drifted from the monorepo source — re-run "
        "`PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/build_data.py`"
    )


def test_the_vendored_corpus_builder_is_byte_identical():
    """The same freshness rule for `assemble.py`: the SDK server's
    instructions come from the VENDORED builder, so a libs-side change
    that never reaches the wheel ships last release's guidance.

    # SEED: append a comment line to
    # `packages/keel-trade/keel-sdk/pipeline_engine/reference/system/assemble.py`.
    # Revert by reversing that edit (re-run build_data.py to restore).
    """
    monorepo = REPO_ROOT / "libs" / "pipeline_engine" / "reference" / "system" / "assemble.py"
    vendored = SDK_ROOT / "pipeline_engine" / "reference" / "system" / "assemble.py"
    assert monorepo.exists() and vendored.exists()
    a, b = monorepo.read_bytes(), vendored.read_bytes()
    assert len(a) > 10_000 and len(b) > 10_000
    assert a == b, (
        "the vendored corpus builder drifted from the libs source — re-run "
        "`PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/build_data.py`"
    )


# ─── Guard: staleness lint ──────────────────────────────────────────────


def _served_knowledge_stems() -> tuple[str, ...]:
    """Every knowledge doc the wheel ships, bar the sectioned core source."""
    from importlib import resources

    stems = sorted(
        f.name[:-3]
        for f in resources.files("keel.data").joinpath("knowledge").iterdir()
        if f.name.endswith(".md") and f.name != "operating_core.md"
    )
    assert len(stems) >= 12, f"only {len(stems)} served knowledge docs — did vendoring run?"
    return tuple(stems)


def _corpus() -> dict[str, str]:
    """Every prose surface the staleness lint reads.

    Keyed `tool:<name>` / `skill:<name>` / `knowledge:<stem>` /
    `core:<section>` so `assemble.STALENESS_ACKNOWLEDGED` can name an
    owner exactly.
    """
    out: dict[str, str] = {}
    for name, tool in OUTCOMES.items():
        parts = [tool.description or "", tool.listed_description or ""]
        for schema in (tool.input_schema, tool.listed_input_schema):
            for pschema in ((schema or {}).get("properties") or {}).values():
                parts.append(pschema.get("description", ""))
        out[f"tool:{name}"] = "\n".join(parts)
    for name in BUNDLED_SKILLS:
        out[f"skill:{name}"] = compose_skill(name, profile="full")
    # Every SERVED knowledge doc — the wheel carries the shared layer only
    # (mcp-conversion 05 §3); Keel's in-app companions never reach an MCP
    # agent, so they are not this lint's subject.
    for stem in _served_knowledge_stems():
        out[f"knowledge:{stem}"] = load_section(stem)
    for section in assemble.load_sections():
        out[f"core:{section.id}"] = section.body
    return out


def test_no_forward_references_in_the_corpus():
    """Spec §5 `staleness lint`. "(yet)", "future … resource",
    "Phase 2C" — prose promising a future the product either already has
    or never shipped. Items another lane of this build owns are named in
    `assemble.STALENESS_ACKNOWLEDGED`, and an acknowledgement whose item
    is gone is itself a failure (below), so the list cannot rot.

    # SEED: add "(a dedicated comparison skill does not exist yet)" to
    # `keel/skills/overfit-check/SKILL.md`. Revert by reversing that edit.
    """
    corpus = _corpus()
    violations = assemble.forward_reference_violations(corpus)
    assert not violations, "forward references in the corpus:\n" + "\n".join(violations)
    # Non-vacuity, on a quantity the seed cannot move.
    assert len(corpus) >= 60, f"only {len(corpus)} corpus entries read"


def test_every_staleness_acknowledgement_still_matches_something():
    """An acknowledgement whose item has been fixed must be deleted, not
    left standing — otherwise it is a permanent exemption nobody reads."""
    stale = assemble.acknowledged_but_absent(_corpus())
    assert not stale, "\n".join(stale)


def test_no_prose_names_a_tool_the_registry_does_not_have():
    """S11's rule: `keel_live_stop` has not existed for months and a
    `suggestion` still named it.

    # SEED: change one `keel_live_control` mention in
    # `keel/tools/outcomes/strategy_delete.py` to `keel_live_stop`.
    # Revert by reversing that edit.
    """
    corpus = _corpus()
    violations = assemble.unknown_tool_violations(corpus, frozenset(OUTCOMES))
    assert not violations, "prose names unregistered tools:\n" + "\n".join(violations)
    # Non-vacuity: the scan found real tool references to judge.
    names_found = sum(1 for text in corpus.values() if "keel_" in text)
    assert names_found >= 30, f"only {names_found} corpus entries name any tool"


def test_the_earliest_data_fact_has_one_owner(capsys):
    """Decision D-g, revised by Q-1701: "the earliest data Keel has" is a
    MEASURED cache fact with exactly one claimant in the corpus, and the
    SDK holds no start-date constant at all. It used to name
    `backtest_run._DEFAULT_START_DATE` as the owner; that constant was
    removed because it arrived at keel-api as an explicit start and
    defeated the platform's own floor (`max(universe_floor,
    series_era(timeframe))`), which is per-universe and per-clock and
    cannot be computed here. S13 found three dates wearing this one word;
    the invariant is ONE, whatever it is.

    # SEED: add "the earliest cached data is 2024-06-25" to
    # `libs/pipeline_engine/reference/system/platform-operations.md`, or
    # re-add `_DEFAULT_START_DATE = "2024-08-15"` to backtest_run.py.
    # Revert by reversing that edit.
    """
    corpus = _corpus()
    claims = assemble.earliest_data_claims(corpus)
    for date, owners in sorted(claims.items()):
        print(f"AIM  earliest-data claim {date}: {sorted(set(owners))}")

    # Non-vacuity first: a clean run must mean "one owner", never "the scan
    # read nothing". The seed cannot empty this.
    assert claims, (
        "no corpus string claims an earliest-data date — the lint found zero "
        "because it is blind, not because it is clean"
    )
    assert len(claims) == 1, (
        "more than one date is claimed as the earliest data Keel has; the "
        "measured cache line in platform-operations.md is the one owner:\n"
        + "\n".join(f"  {date}: {sorted(set(owners))}" for date, owners in sorted(claims.items()))
    )

    # And no module may reintroduce a start-date constant (Q-1701).
    outcomes = Path(keel.tools.outcomes.__file__).parent
    offenders = [
        f"{path.name}:{i}"
        for path in sorted(outcomes.glob("*.py"))
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if re.match(r"\s*_?DEFAULT_START_DATE\s*=", line)
    ]
    assert not offenders, (
        "a hardcoded backtest start date is back; the platform's floor is "
        f"per-universe and per-clock and owns this: {offenders}"
    )
    assert "AIM  earliest-data claim" in capsys.readouterr().out


# ─── Guard: chat parity (TEMPLATE parity — decision #29) ────────────────


def test_the_chat_head_is_the_same_facts_in_the_chat_vocabulary():
    """Spec §5 `chat parity`. Byte parity would make the chat's prompt
    name tools it does not have (`keel_account_status`, `keel_backtest_summarize`),
    so the contract is TEMPLATE parity: the same sentences rendered through
    ONE vocabulary map. The chat side (G5)
    reads its head and its file set from this builder rather than from
    any live list, which is what makes the two provably the same text.

    # SEED: change one entry of `assemble.CHAT_VOCABULARY` to a sentence
    # rewrite rather than a token substitution. Revert by reversing it.
    """
    mcp_head = assemble.head()
    chat = assemble.chat_head()
    assert chat == assemble.render_vocabulary(mcp_head), "the chat head is not a rendering"
    positions = assemble.head_fact_positions(chat)
    absent = {k: v for k, v in positions.items() if v < 0}
    # Facts whose sentence the vocabulary rewrites move; the rest must
    # survive verbatim (agent-surface-cleanup spec 01 §2.3). Fact 4 is
    # substituted WHOLE: no chat tool result carries a `view`, so the chat
    # states the editor's own mechanism instead of promising one.
    substituted = {"status-first", "explain-not-repeat", "summarize-or-compare"}
    assert set(absent) == substituted, f"chat head facts moved: {absent}"
    for token in assemble.CHAT_VOCABULARY:
        assert token not in chat, f"MCP token {token} survived into the chat head"
    # Non-vacuity: five facts, a non-empty vocabulary, a head that changed.
    assert len(assemble.HEAD_FACTS) == 5
    assert len(assemble.CHAT_VOCABULARY) >= 4
    assert chat != mcp_head


def test_the_declared_always_on_set_is_enumerated_and_real():
    """The chat's file set comes from `assemble.declared_always_on()`.

    # SEED: delete one name from `_ALWAYS_ON` in assemble.py. Revert by
    # reversing that edit.
    """
    always_on = assemble.declared_always_on()
    pull = assemble.declared_pull()
    assert always_on, "the declared set is empty — the parity guard would be vacuous"
    assert not set(always_on) & set(pull), "a file is declared both always-on and pull"
    # Each declared name is a chat SLOT (mcp-conversion 05 §3): the shared
    # file, its in-app companion under system/chat/, or both. The wheel
    # carries only the shared half, so the slot is resolved against the
    # monorepo tree the manifest describes; an in-app-only slot must NOT be
    # bundled.
    # The SDK tests import the VENDORED builder, which has no manifest; the
    # monorepo's is read by path and parsed by the same strict parser.
    root = REPO_ROOT / "libs" / "pipeline_engine" / "reference" / "system"
    layers = assemble.parse_layers((root.parent / "LAYERS.yaml").read_text())
    bundled = set(_served_knowledge_stems())
    for stem in always_on + pull:
        shared, companion = f"system/{stem}.md", f"system/chat/{stem}.md"
        assert shared in layers or companion in layers, f"{stem} is in neither tree"
        text = "".join((root.parent / p).read_text() for p in (shared, companion) if p in layers)
        assert len(text) > 500, f"{stem} is {len(text)} chars — a declared file that is empty"
        assert (stem in bundled) == (shared in layers), f"{stem}: bundled set != shared tree"
    # Non-vacuity on the SPLIT itself: W5 §3 measured eleven staying and
    # six moving.
    assert len(always_on) == 11, f"{len(always_on)} files declared always-on"
    assert len(pull) == 6, f"{len(pull)} files declared pull"
