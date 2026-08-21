"""Gates for the always-on MCP operating core (`operating_core.md`).

The operating core is the corpus-distilled behavioral guide the MCP server
embeds in its `instructions=` string on BOTH profiles. It must therefore be:

* policy-clean — no forbidden verb family (the same FORBIDDEN_TEXT_RE the
  directory-policy gate in tests/test_policy_scan.py runs over server
  instructions), so it rides the listed profile safely;
* listed-tools-only — never routes to a `keel_*` tool absent from the
  listed surface;
* small — a token budget so it can't silently bloat back into a second
  always-on corpus;
* in sync — the bundled copy the SDK server reads must be byte-identical to
  the libs source (a missed `build_data.py` rebuild fails here, not in prod);
* a live pointer — every `keel://knowledge/{section}` it names must resolve
  to a bundled section (no dead pointers);
* front-loaded — the non-negotiables begin within the first ~512 chars, the
  only quantitative host rule (OpenAI/Codex keep the first 512 chars
  self-contained).

`load_system_knowledge()` (chat-api's always-on prompt) deliberately does
NOT include the core — that non-disruption invariant is covered here too.
"""

from __future__ import annotations

import re
from pathlib import Path

from keel.data.knowledge import load_operating_core, load_section
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


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
POINTER_SECTIONS = (
    "strategy_patterns",
    "composition_mechanics",
    "strategy_paths",
    "universe_selection",
    "trading_domain",
    "mistakes",
    "tool_usage",
    "costs_and_fees",
    "strategy_phases",
    "editor_ui",
    "component_versioning",
)


def _libs_source() -> Path:
    # tests/ -> keel-sdk -> keel-trade -> packages -> <repo root>
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "libs" / "pipeline_engine" / "reference" / "system" / "operating_core.md"


def test_core_is_policy_clean() -> None:
    """No forbidden verb family — so the core rides the listed profile."""
    core = load_operating_core()
    verb_hits = sorted({m.group(0).lower() for m in FORBIDDEN_TEXT_RE.finditer(core)})
    money_hits = sorted({m.group(0).lower() for m in FORBIDDEN_MONEY_RE.finditer(core)})
    assert not verb_hits, f"operating core has forbidden verbs: {verb_hits}"
    assert not money_hits, f"operating core has money-semantics tokens: {money_hits}"


def test_core_references_only_listed_tools() -> None:
    """Every `keel_*` name the core routes to is on the listed surface."""
    core = load_operating_core()
    refs = sorted(set(re.findall(r"\bkeel_[a-z0-9_]+\b", core)))
    known = set(OUTCOMES)
    non_listed = [r for r in refs if r in known and r not in LISTED_PROFILE_TOOLS]
    assert not non_listed, f"core routes to non-listed tools: {non_listed}"
    # And every reference is a real tool (no typo'd tool name).
    unknown = [r for r in refs if r not in known]
    assert not unknown, f"core references unknown `keel_*` names: {unknown}"


def test_core_token_budget_under_600() -> None:
    """The always-on core can't silently bloat back into a full corpus."""
    core = load_operating_core()
    est_tokens = len(core) / 4  # same chars/4 estimate used to size the draft
    assert est_tokens < 600, f"operating core is {est_tokens:.0f} est-tokens (>= 600)"


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
    """Every knowledge section the pointer names is a live, loadable file."""
    core = load_operating_core()
    for section in POINTER_SECTIONS:
        assert re.search(rf"\b{re.escape(section)}\b", core), (
            f"expected pointer section '{section}' not named in the core"
        )
        text = load_section(section)  # raises FileNotFoundError if it's a dead pointer
        assert len(text) > 100, f"pointer section '{section}' is suspiciously short"


def test_non_negotiables_are_front_loaded_in_first_512_chars() -> None:
    """OpenAI/Codex keep the first ~512 chars self-contained — the load-bearing
    rules (role, WeightSeries build discipline, required discovery,
    iterate-don't-rewrite, primary routing) must all BEGIN there."""
    core = load_operating_core()
    checks = {
        "role/ethos": "strategy builder on Keel",
        "not single-name": "single-name prediction",
        "WeightSeries discipline": "WeightSeries",
        "required discovery": "Discovery is required",
        "iterate-don't-rewrite": "Iterate,",
        "primary routing": "Route:",
    }
    # Each non-negotiable must BEGIN within the first ~512 chars (a host that
    # truncates keeps the load-bearing rules).
    late = {
        label: core.find(needle)
        for label, needle in checks.items()
        if not 0 <= core.find(needle) < 512
    }
    assert not late, f"non-negotiables not front-loaded (start index >= 512): {late}"


def test_core_is_excluded_from_chat_api_always_on_prompt() -> None:
    """The chat-api non-disruption invariant: `load_system_knowledge()` (the
    always-on prompt) must NOT contain the core — it is the thin-context MCP
    projection, not part of the 15-file corpus chat-api injects."""
    from keel.data.knowledge import load_system_knowledge

    assert load_operating_core() not in load_system_knowledge()
