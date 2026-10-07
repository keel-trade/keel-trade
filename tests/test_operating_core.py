"""Gates for the always-on MCP operating core (`operating_core.md`).

The operating core is the corpus-distilled behavioral guide the MCP server
embeds in its `instructions=` string on BOTH profiles. It must therefore be:

* policy-clean ON THE LISTED PROFILE — no forbidden verb family (the same
  FORBIDDEN_TEXT_RE the directory-policy gate in tests/test_policy_scan.py
  runs over server instructions). Since the 2026-09-22 restructure the FILE
  also carries the full-profile-only sections (auth / state / live /
  hosted SURFACE), which legitimately name `keel_auth_login` and the CLI
  package, so the scan runs over `assemble.instructions("listed")` — the
  string the directory actually sees — not over the whole file;
* listed-tools-only — the listed assembly never routes to a `keel_*` tool
  absent from the listed surface;
* small — the listed assembly fits the 2,048-BYTE host limit Claude Code
  truncates at, so it can't silently bloat back into a second always-on
  corpus. (The old est-token ratchet measured the FILE, which now also
  holds the pull-tier long forms; the host limit is the real bound and
  the only one decision #38 admits as a guard.)
* in sync — the bundled copy the SDK server reads must be byte-identical to
  the libs source (a missed `build_data.py` rebuild fails here, not in prod);
* a live pointer — every `keel://knowledge/{section}` it names must resolve
  to a bundled section (no dead pointers);
* front-loaded — the six head facts begin within the first 512 characters
  (OpenAI/Codex keep the first 512 chars self-contained). The head-fact
  guard itself lives in tests/test_guidance_guards.py, which runs it over
  BOTH profiles; the arm kept here proves the head is where the file
  starts.

`load_system_knowledge()` (chat-api's always-on prompt) deliberately does
NOT include the core — that non-disruption invariant is covered here too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from keel.data.knowledge import load_operating_core, load_section
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

from pipeline_engine.reference.system import assemble


_bootstrap()

# Must stay identical to tests/test_policy_scan.py::FORBIDDEN_TEXT_RE — the
# directory-policy gate that scans server.instructions (which embed this
# core). Re-declared so a corpus edit fails HERE, at the core, not three
# layers up in the policy scan.
FORBIDDEN_TEXT_RE = re.compile(
    r"\b("
    r"deploy(?:s|ed|ing|ment|ments)?"
    r"|fund(?:s|ed|ing)?"
    r"|trade[sd]?|trading"
    r"|buy(?:s|ing)?|bought"
    r"|sell(?:s|ing)?|sold"
    r"|wallets?"
    r"|leverage[sd]?"
    r"|amounts?"
    r"|upgrade[sd]?"
    r"|go live|going live|start trading"
    r")\b",
    re.IGNORECASE,
)

# Money-semantics tokens (FORBIDDEN_PARAM_TOKENS in test_policy_scan) — the
# core carries no financial numbers (defaults live in tool descriptions).
FORBIDDEN_MONEY_RE = re.compile(
    r"\b(amount|wallet|leverage|notional|margin|collateral|qty|quantity|usd)\b",
    re.IGNORECASE,
)

# The knowledge sections the core's pull-deeper pointer routes to. Each must
# resolve to a bundled `keel://knowledge/{section}` file (no dead pointer).
# Only SERVED topics (mcp-conversion 05 §3.3): strategy_phases, editor_ui,
# component_versioning and costs_and_fees are Keel's in-app corpus now, and
# the cost pointer names the shared `backtest_costs`.
POINTER_SECTIONS = (
    "strategy_patterns",
    "composition_mechanics",
    "strategy_paths",
    "universe_selection",
    "trading_domain",
    "mistakes",
    "tool_usage",
    "backtest_costs",
)

#: Topics the core must NOT point at: in-app only, never vendored or served.
IN_APP_ONLY_TOPICS = ("strategy_phases", "editor_ui", "component_versioning", "costs_and_fees")

#: The no-trading boundary the listed line states (mcp-conversion 04 §5.3,
#: founder-approved D-12). It is a NEGATION of the forbidden families, so the
#: verb scan reads the listed string with this one clause removed — and the
#: clause is asserted present, so the carve-out can never exempt nothing.
BOUNDARY_CLAUSE = "It cannot place orders, move funds or connect wallets"


#: The listed instructions' sections (agent-surface-cleanup spec 01 §2.2):
#: the head and three body sentences — nothing else is always-on there.
LISTED_SECTIONS = ("head", "sequence", "quota", "surface-listed")


def _libs_source() -> Path:
    # tests/ -> keel-sdk -> keel-trade -> packages -> <repo root>
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "libs" / "pipeline_engine" / "reference" / "system" / "operating_core.md"


def test_listed_assembly_is_policy_clean() -> None:
    """No forbidden verb family — so the core rides the listed profile."""
    listed = assemble.instructions("listed")
    assert listed.count(BOUNDARY_CLAUSE) == 1, "the listed line lost its boundary clause"
    scanned = listed.replace(BOUNDARY_CLAUSE, "")
    verb_hits = sorted({m.group(0).lower() for m in FORBIDDEN_TEXT_RE.finditer(scanned)})
    money_hits = sorted({m.group(0).lower() for m in FORBIDDEN_MONEY_RE.finditer(scanned)})
    assert not verb_hits, f"listed instructions have forbidden verbs: {verb_hits}"
    assert not money_hits, f"listed instructions have money-semantics tokens: {money_hits}"
    # Non-vacuity, on quantities no wording change can move: the subject is
    # the real four-section assembly, and it names the first call.
    assert assemble.instruction_section_ids("listed") == LISTED_SECTIONS
    assert "keel_account_status" in listed


def test_listed_assembly_references_only_listed_tools() -> None:
    """Every `keel_*` name the listed string routes to is on that surface."""
    listed = assemble.instructions("listed")
    refs = sorted(set(re.findall(r"\bkeel_[a-z0-9_]+\b", listed)))
    known = set(OUTCOMES)
    non_listed = [r for r in refs if r in known and r not in LISTED_PROFILE_TOOLS]
    assert not non_listed, f"listed instructions route to non-listed tools: {non_listed}"
    # And every reference is a real tool (no typo'd tool name).
    unknown = [r for r in refs if r not in known]
    assert not unknown, f"listed instructions reference unknown `keel_*` names: {unknown}"
    # Non-vacuity: it does route somewhere.
    assert len(refs) >= 5, f"only {len(refs)} tool references — the scan found nothing"


def test_every_core_tool_reference_is_a_real_tool() -> None:
    """Across the WHOLE file (every profile, every pull-tier section)."""
    refs = sorted(set(re.findall(r"\bkeel_[a-z0-9_]+\b", load_operating_core())))
    unknown = [r for r in refs if r not in set(OUTCOMES)]
    assert not unknown, f"operating core references unknown `keel_*` names: {unknown}"
    assert len(refs) >= 10, f"only {len(refs)} tool references found in the core"


# The est-token ratchet that stood here from 2026-09-19 measured the FILE.
# Since the 2026-09-22 restructure (guidance spec §4) the file is the whole
# corpus source — head + body + the pull-tier long forms + the front matter
# — so its length stopped being a statement about what any host receives.
# The bound that IS a statement about a host is the one decision #38 admits:
# Claude Code truncates server instructions at 2 KB, and "2 KB" is BYTES.
# Every profile's ASSEMBLED string is measured below; the listed profile
# (the directory registration) must fit, and the full profiles are reported
# with what falls past the cut, which W2 §2.4 accepts because every
# sentence past it has an L1 twin on the one host that truncates.


def test_listed_assembly_fits_the_host_limit() -> None:
    """The listed instructions fit Claude Code's 2 KB cut, in BYTES."""
    listed = assemble.instructions("listed")
    size = len(listed.encode("utf-8"))
    assert size <= assemble.INSTRUCTIONS_MAX_BYTES, (
        f"listed instructions are {size} bytes "
        f"(> {assemble.INSTRUCTIONS_MAX_BYTES}) — Claude Code truncates them"
    )
    # Non-vacuity: a quantity the size seed cannot move — the listed
    # assembly is built from the sections the corpus declares for it.
    ids = assemble.instruction_section_ids("listed")
    assert ids == LISTED_SECTIONS, f"listed assembly sections: {ids}"


def test_full_profile_overflow_is_reported_not_silent(capsys) -> None:
    """The full profiles run past 2 KB by design (W2 §2.4) — print it."""
    for profile in ("full-hosted", "full-local"):
        text = assemble.instructions(profile)
        size = len(text.encode("utf-8"))
        print(f"{profile}: {size} bytes ({size - assemble.INSTRUCTIONS_MAX_BYTES:+d} vs 2 KB cut)")
        assert size > 0
    assert "bytes" in capsys.readouterr().out


def test_core_names_the_metered_units_rule() -> None:
    """mcp-conversion M1.4: the core is where an agent learns quotas exist.

    Asserted by CONTENT, not by length — the three load-bearing facts are
    that a metered unit refills on a period, that `keel_plan_usage`
    reports it without spending anything, and that a plan-limit refusal is
    not retryable. An agent that does not know the third one burns the
    user's remaining allowance retrying a wall.
    """
    core = load_operating_core()
    assert "keel_plan_usage" in core
    assert re.search(r"\bspends nothing\b", core), "the core must say the check is free"
    assert re.search(r"\bnot retryable\b", core), "the core must say a plan-limit refusal is final"


def test_bundled_copy_is_byte_identical_to_libs_source() -> None:
    """A missed `build_data.py` rebuild must fail here, not in production."""
    libs_src = _libs_source()
    assert libs_src.exists(), f"libs source missing: {libs_src}"
    # The SDK server reads the bundled copy via load_section/load_operating_core.
    bundled_text = load_operating_core()
    assert bundled_text == libs_src.read_text(), (
        "bundled operating_core.md drifted from the libs source — re-run "
        "`PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/build_data.py`"
    )


def test_pointer_sections_all_resolve() -> None:
    """Every knowledge section the pointer names is a live, loadable file,
    and the served core points at no in-app-only topic (R-L3)."""
    core = load_operating_core()
    for section in POINTER_SECTIONS:
        assert re.search(rf"\b{re.escape(section)}\b", core), (
            f"expected pointer section '{section}' not named in the core"
        )
        text = load_section(section)  # raises FileNotFoundError if it's a dead pointer
        assert len(text) > 100, f"pointer section '{section}' is suspiciously short"
    served = assemble.base_document()
    for topic in IN_APP_ONLY_TOPICS:
        assert not re.search(rf"\b{topic}\b", served), f"the served core points at {topic}"
        with pytest.raises(FileNotFoundError):
            load_section(topic)
    # Non-vacuity: the base document is the real one and names the pointers.
    assert "backtest_costs" in served and len(served) > 1000


def test_the_head_is_what_every_profile_opens_with() -> None:
    """OpenAI/Codex read the first 512 characters as the essentials.

    The six-fact head guard runs over BOTH assembled profiles in
    tests/test_guidance_guards.py. The arm kept here is the structural
    half: the head section is `layer: head`, it is the FIRST block of
    every assembly, and it fits 512 characters on its own.
    """
    head = assemble.head()
    assert len(head) <= assemble.HEAD_MAX_CHARS, f"head is {len(head)} chars"
    assert assemble.section("head").layer == "head"
    for profile in assemble.PROFILE_TAGS:
        text = assemble.instructions(profile)
        assert text.startswith(head), f"{profile} does not open with the head"
    # Non-vacuity: three profiles were actually assembled.
    assert len(assemble.PROFILE_TAGS) == 3


def test_core_is_excluded_from_chat_api_always_on_prompt() -> None:
    """The chat-api non-disruption invariant: the core is the thin-context
    MCP projection, never one of the knowledge slots the chat injects. The
    chat's prompt is assembled from the monorepo (both corpus layers), not
    from this wheel, so the invariant is checked on the DECLARATION both
    sides read (`assemble.declared_always_on()` / `declared_pull()`)."""
    declared = assemble.declared_always_on() + assemble.declared_pull()
    assert "operating_core" not in declared
    # Non-vacuity: the declaration is the real eleven + six.
    assert len(declared) == 17


def test_served_operating_core_is_base_register_only() -> None:
    """agent-surface-cleanup spec 01 §4 #6b (R-5): `keel_help
    topic=operating_core` serves the BASE document — every `register:
    base` section in full — and no sentence of the chat's opinion layer,
    which lives in the same file.

    # SEED: make `help.py`'s `_operating_core_result` return
    # `load_operating_core()` (the raw file) as `body` — the opinion arm
    # reds. Revert by reversing that edit.
    """
    from keel.data.knowledge import active_profile
    from keel.tools.outcomes._base import ToolContext

    handler = OUTCOMES["keel_help"].handler
    served = handler({"topic": "operating_core"}, ToolContext()).to_envelope()["body"]
    assert served == assemble.base_document(profile=active_profile())
    assert "quantitative crypto research platform" in served  # the identity anchor
    opinion = [s for s in assemble.load_sections() if s.register == "opinion"]
    for sec in opinion:
        for para in sec.paragraphs:
            assert para not in served, f"opinion section {sec.id} is served over MCP"
    # Non-vacuity, on quantities the seed cannot move: there IS opinion in
    # the file to keep out (the raw file carries it), the chat layer is
    # real, and the served document spans every base section.
    assert opinion and all(p in load_operating_core() for s in opinion for p in s.paragraphs)
    assert len(assemble.chat_layer()) >= 500
    assert len(assemble.base_sections()) >= 8
    # Case and separators fold like every other topic.
    again = handler({"topic": "Operating-Core"}, ToolContext()).to_envelope()["body"]
    assert again == served


def test_operating_core_resource_serves_the_base_document() -> None:
    """The `keel://knowledge/operating_core` MCP resource served the RAW
    file — chat opinion layer included — while `keel_help` served the base
    document (L6a finding, 2026-09-23). Both now read
    `keel.data.knowledge.served_section`.

    # SEED: make `server.py`'s `knowledge_resource` return
    # `load_section(section)` again — `served == base_document()` reds first
    # (the raw file opens with the corpus comment and carries the opinion
    # sections); the control section below stays green. Revert by
    # reversing that edit.
    """
    import asyncio

    from keel.mcp.server import create_server

    async def read(uri: str) -> str:
        result = await create_server().read_resource(uri)
        return result.contents[0].content

    from keel.data.knowledge import active_profile

    served = asyncio.run(read("keel://knowledge/operating_core"))
    assert served == assemble.base_document(profile=active_profile())
    opinion = [s for s in assemble.load_sections() if s.register == "opinion"]
    for sec in opinion:
        for para in sec.paragraphs:
            assert para not in served, f"opinion section {sec.id} is served as a resource"
    # Non-vacuity the seed cannot move: the raw file DOES carry opinion.
    assert opinion and all(p in load_operating_core() for s in opinion for p in s.paragraphs)
    # Control: every other section is still its file verbatim.
    assert asyncio.run(read("keel://knowledge/dsl_syntax")) == load_section("dsl_syntax")


def test_the_listed_base_document_carries_no_full_only_section(monkeypatch) -> None:
    """mcp-conversion 05 §3.3: the served `operating_core` is filtered by
    each section's `profiles:` for THIS server's profile, so the listed
    profile never receives AUTH, the write-through STATE model, LIVE or the
    `KEEL_TOOLSETS` notice — the full profiles still do.

    # SEED: drop the `profile=` argument from `served_section`'s
    # `base_document` call in keel/data/knowledge.py — the listed arm reds.
    # Revert by reversing that edit.
    """
    from keel.data.knowledge import served_section

    full_only = [s for s in assemble.base_sections() if s.profiles and "listed" not in s.profiles]
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    listed = served_section("operating_core")
    for sec in full_only:
        assert sec.body.strip() not in listed, f"{sec.id} is served on the listed profile"
    assert assemble.section("surface-listed").body.strip() in listed
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    full = served_section("operating_core")
    assert assemble.section("auth").body.strip() in full
    assert assemble.section("surface-listed").body.strip() not in full
    # Non-vacuity: there ARE full-only sections to keep out (auth, state,
    # live, the toolsets notice, the hosted/local surfaces).
    assert {"auth", "state-model", "live", "live-write-notice"} <= {s.id for s in full_only}


def test_every_base_emission_is_in_the_base_register() -> None:
    """agent-surface-cleanup spec 01 §4 #7, over the VENDORED builder the
    SDK server runs: the three profiles' instructions and the served base
    document match no directive, imperative or opinion word.

    # SEED: add "keep moving" to the `sequence` section's body in
    # `keel/data/knowledge/operating_core.md` — this reds. Revert by
    # reversing that edit.
    """
    emissions = {f"instructions:{p}": assemble.instructions(p) for p in assemble.PROFILE_TAGS}
    emissions["base_document"] = assemble.base_document()
    violations, scanned = assemble.register_violations(emissions)
    assert violations == [], "\n".join(violations)
    # Non-vacuity: every emission was scanned, and the lint's word list
    # fires on its positive control.
    assert scanned == len(assemble.PROFILE_TAGS) + 1
    assert assemble.REGISTER_WORD_RE.search("keep moving")
