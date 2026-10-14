"""POLICY SCAN — hard gate for the listed-profile tool surface.

Spec 01 R3 + the research/08 string rules (the directory policy
boundary, binding per GOAL.md): the LISTED registration's tools/list
JSON must carry

* no excluded tool (live deploy/control, strategy delete, local-only);
* no parameter named or described with amount/wallet/leverage/size
  money-semantics;
* no deploy/fund/trade/buy/sell verb family in tool names, titles,
  descriptions, or parameter descriptions (directory reviews are
  automated string scans — noun inflections are banned too, because a
  reviewer's scanner won't parse grammar either);
* no routing to tools that are absent from the listed surface.

The scan is data-driven: it walks whatever the listed profile actually
registers (names, titles, descriptions, input schemas, plus the server
instructions), so ANY future tool or copy change re-enters the gate
automatically. Scope notes, deliberate and documented:

* Identifier references (`deployment_id`, `keel_live_monitor`) do not
  trip the word-boundary text rules — underscores are word characters.
* Enum VALUES are API data, not copy; they are scanned only for
  outright money tokens (amount/wallet/leverage), not the verb rule
  (e.g. the live_monitor `view` enum legitimately contains history
  slice names).

Never weaken this test to make a new tool pass — reword the tool
(listed_* overrides exist for exactly that) or keep it off the listed
profile.
"""

from __future__ import annotations

import ast
import asyncio
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes import _paper_strings as paper_strings
from keel.tools.outcomes._toolsets import (
    LISTED_PAPER_TOOLS,
    LISTED_PROFILE_TOOLS,
    listed_profile_tools,
)


try:
    from . import _surface_fixtures as sf
except ImportError:
    # test_surface_routing.py loads THIS file by path, as a bare module, to
    # read FORBIDDEN_TEXT_RE; there is no package for the relative import.
    # Only the G4 arms use `sf`, and pytest always collects them in-package.
    sf = None


_bootstrap()


# ─── Rule tables (data-driven — edit deliberately, with review) ─────────

# Tools that must NEVER appear on the listed profile (spec 01 R3).
# The scan also asserts exact equality with LISTED_PROFILE_TOOLS, which
# subsumes this — the named list exists for readable failures and so a
# refactor of the allow-list can't silently re-admit one of these.
EXPLICITLY_EXCLUDED_TOOLS = frozenset(
    {
        "keel_live_deploy",
        "keel_live_control",
        # Mints update-intent handoff links — the listed directory profile
        # never emits deploy/update-intent links (research/08 policy;
        # keel_app_link is its only app bridge).
        "keel_live_update",
        "keel_strategy_delete",
        "keel_accounts_list",
        "keel_audit_list_last",
        # keel_strategy_diff moved ONTO the listed profile (D1 2026-07-19):
        # a read-only version/source diff with no money/wallet params and
        # no forbidden verbs — it belongs on the single hosted surface.
        # keel_strategy_restore moved ONTO it too (agent-surface-cleanup
        # spec 03 §2.4): a forward commit that deletes nothing.
        # local-only (spec 01 R2) — absent hosted-side anyway
        "keel_strategy_checkout",
        "keel_strategy_push",
        "keel_strategy_pull",
        "keel_strategy_status",
        "keel_strategy_discard",
        "keel_strategy_workspaces",
        "keel_auth_login",
        "keel_auth_logout",
    }
)

# Tool-name rule: substring stems (names are the highest-signal review
# surface; research/08: "never deploy_*, fund_*, trade_*").
FORBIDDEN_NAME_STEMS = (
    "deploy",
    "fund",
    "trade",
    "buy",
    "sell",
    "wallet",
    "leverage",
    "amount",
)

# Text rule (titles, descriptions, param descriptions, instructions):
# word-boundary token families incl. inflections + phrases.
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

#: The listed instructions' no-trading boundary, exactly (04 §5.3, D-12). The
#: second sentence is the one place listed copy may NAME money nouns, because
#: it denies them; the text scan exempts it verbatim and nothing wider.
LISTED_BOUNDARY_SENTENCE = (
    "It cannot place orders, move funds or connect wallets; strategies that are running "
    "are managed in the Keel web app (keel_app_link)."
)
LISTED_BOUNDARY_LINE = "This connector is for research and backtests. " + LISTED_BOUNDARY_SENTENCE

#: The same boundary as keel_account_status states it (`_status_view.CAPABILITY_PHRASE`,
#: 04 §5.3): the listed keel_account_status description and the served status line
#: quote this clause verbatim. The scan exempts exactly this clause in exactly
#: those two owners, each asserted to carry it once, so the carve-out can
#: neither exempt nothing nor widen to another sentence or tool.
STATUS_BOUNDARY_CLAUSE = "it cannot place orders, move funds or connect wallets"

# Parameter rule: money-semantics tokens forbidden in parameter NAMES
# (underscore-split segments) and, word-bounded, in parameter
# descriptions. "size" is included — pagination params must say
# "maximum rows", not "page size", so the scan needs no allow-list.
FORBIDDEN_PARAM_TOKENS = (
    "amount",
    "wallet",
    "leverage",
    "size",
    "notional",
    "margin",
    "collateral",
    "qty",
    "quantity",
    "usd",
)
FORBIDDEN_PARAM_DESC_RE = re.compile(
    r"\b(" + "|".join(FORBIDDEN_PARAM_TOKENS) + r")\b", re.IGNORECASE
)

# Enum values: outright money tokens only (see module docstring).
FORBIDDEN_ENUM_TOKENS = ("amount", "wallet", "leverage")

# ─── The listed paper carve-out (PT2-2; paper-trading-v2 S06 §3.4.3) ─────
#
# Two allowances, each applied ONLY when the surface under test was built
# with KEEL_LISTED_PAPER on, each a no-op off (the scan is then byte-identical
# to its pre-paper self). Nothing above this block moves with the flag.
#
# 1. An exact-name stem allowance: `keel_paper_deploy` and nothing else —
#    never a `keel_paper_` prefix rule (a `keel_paper_fund` still fails).
LISTED_PAPER_NAME_ALLOWANCE = frozenset({"keel_paper_deploy"})

# 2. A paper-compound phrase allowance: a verb token carrying "paper" in the
#    same breath is replaced by one neutral word with no banned stem before
#    FORBIDDEN_TEXT_RE runs unchanged over the remainder. "simulated" was
#    never banned.
PAPER_COMPOUND_RE = re.compile(
    r"\bpaper[ -]?(?:trading|trade[sd]?"
    r"|deploy(?:s|ed|ing|ment|ments)?)\b",
    re.IGNORECASE,
)
PAPER_COMPOUND_NEUTRAL = "papercompound"

#: The flag-on boundary (S06 §3.4.5): the instructions' amended sentence and
#: the status view's clause, one boundary in two registers.
LISTED_PAPER_BOUNDARY_SENTENCE = (
    "It cannot place real orders, move funds or connect wallets; paper-trading runs are "
    "simulated, and they can be started, paused, updated and stopped here; real-capital "
    "strategies are managed in the Keel web app (keel_app_link)."
)
LISTED_PAPER_BOUNDARY_LINE = (
    "This connector is for research, backtests and paper-trading runs. "
    + LISTED_PAPER_BOUNDARY_SENTENCE
)
STATUS_PAPER_BOUNDARY_CLAUSE = "it cannot place real orders, move funds or connect wallets"


def listed_boundary_sentence(paper: bool) -> str:
    return LISTED_PAPER_BOUNDARY_SENTENCE if paper else LISTED_BOUNDARY_SENTENCE


def listed_boundary_line(paper: bool) -> str:
    return LISTED_PAPER_BOUNDARY_LINE if paper else LISTED_BOUNDARY_LINE


def status_boundary_clause(paper: bool) -> str:
    return STATUS_PAPER_BOUNDARY_CLAUSE if paper else STATUS_BOUNDARY_CLAUSE


def boundary_clause(paper: bool) -> str:
    """The instructions' clause: the status clause with a sentence-initial capital."""
    clause = status_boundary_clause(paper)
    return clause[0].upper() + clause[1:]


#: Every listed state the scan runs, as (KEEL_LISTED_PAPER_HOSTS, the
#: caller's host key, paper expected on the surface). ``flag_off`` is HEAD's
#: default; ``flag_on`` is the deprecated ``KEEL_LISTED_PAPER=1`` alias (all
#: hosts). The ``claude_on-*`` states are FDR-10's per-host switch with only
#: Claude on: a Claude session gets paper, a ChatGPT session and an unknown
#: client (no host key) in the SAME process get exactly HEAD's surface. The
#: ``chatgpt_on-*`` and ``all_on-*`` states run every gate for the later
#: ChatGPT flip (S10 §10).
_LISTED_STATE_SPECS: dict[str, tuple[str | None, str | None, bool]] = {
    "flag_off": (None, None, False),
    "flag_on": ("legacy", None, True),
    "claude_on-claude": ("claude", "claude", True),
    "claude_on-chatgpt": ("claude", "chatgpt", False),
    "claude_on-unknown": ("claude", None, False),
    # ChatGPT readiness (S10 §10, PT2-90): the same gates with the switch at
    # `chatgpt` and at `all`, so a later ChatGPT flip is a values change only.
    "chatgpt_on-chatgpt": ("chatgpt", "chatgpt", True),
    "chatgpt_on-claude": ("chatgpt", "claude", False),
    "all_on-unknown": ("all", None, True),
}


@contextmanager
def listed_flag_state(paper: bool, state: str | None = None):
    """The listed + hosted environment with the listed flag off or on. On also
    releases the held paper SDK (the flag is refused while it is held,
    S06 §3.4.2), and every one of those is restored on exit.

    ``state`` (a :data:`_LISTED_STATE_SPECS` key) selects a per-host state
    instead (FDR-10): the SDK is released whenever any host is switched on,
    and the caller's host key is bound for the enclosed build."""
    import os

    from keel.tools.outcomes._toolsets import listed_host_context

    if state is None:
        state = "flag_on" if paper else "flag_off"
    hosts, host, _expected = _LISTED_STATE_SPECS[state]
    keys = (
        "KEEL_SERVER_PROFILE",
        "KEEL_EXECUTION_MODE",
        "KEEL_TOOLSETS",
        "KEEL_LISTED_PAPER",
        "KEEL_LISTED_PAPER_HOSTS",
    )
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    os.environ["KEEL_EXECUTION_MODE"] = "hosted"
    os.environ.pop("KEEL_TOOLSETS", None)
    os.environ.pop("KEEL_LISTED_PAPER", None)
    os.environ.pop("KEEL_LISTED_PAPER_HOSTS", None)
    try:
        if hosts is not None:
            from tests._paper_sdk import paper_sdk_release

            with paper_sdk_release(), listed_host_context(host):
                if hosts == "legacy":
                    os.environ["KEEL_LISTED_PAPER"] = "1"
                else:
                    os.environ["KEEL_LISTED_PAPER_HOSTS"] = hosts
                yield
        else:
            yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ListedSurface(tuple):
    """(tools_json, instructions, prompt_names), plus the flag state it was
    built in: `paper`, the registry then (`registry`) and the listed set the
    server then served by `listed_profile_tools()` (`listed_tools`)."""

    paper: bool
    registry: frozenset
    listed_tools: frozenset


#: Both flag states: every listed-surface arm runs off (HEAD's surface) and on.
LISTED_FLAG_STATES = tuple(_LISTED_STATE_SPECS)


# ─── Build the listed surface exactly as the hosted server would ────────


@pytest.fixture(scope="module", params=LISTED_FLAG_STATES)
def listed_surface(request):
    """(tools_json, instructions, prompt_names) from a real FastMCP
    server built under KEEL_SERVER_PROFILE=listed +
    KEEL_EXECUTION_MODE=hosted — the deployment configuration a
    directory registration actually runs — once with KEEL_LISTED_PAPER off
    and once on (paper SDK released; S06 §3.4.3, AC-06-6)."""
    from keel.mcp.server import create_server

    paper = _LISTED_STATE_SPECS[request.param][2]
    with listed_flag_state(paper, request.param):
        server = create_server()
        tools = asyncio.run(server.list_tools())
        prompts = asyncio.run(server.list_prompts())
        tools_json = [
            {
                "name": t.name,
                "title": t.annotations.title if t.annotations else None,
                "description": t.description or "",
                "inputSchema": t.parameters or {},
                "outputSchema": t.output_schema or {},
            }
            for t in tools
        ]
        surface = ListedSurface((tools_json, server.instructions or "", {p.name for p in prompts}))
        surface.paper = paper
        surface.registry = frozenset(OUTCOMES)
        surface.listed_tools = listed_profile_tools()
    return surface


def _text_violations(owner: str, field: str, text: str, *, paper: bool = False) -> list[str]:
    """FORBIDDEN_TEXT_RE over `text`. `paper` applies the paper-compound
    allowance first (flag-on surfaces only, S06 §3.4.3)."""
    if paper:
        text = PAPER_COMPOUND_RE.sub(PAPER_COMPOUND_NEUTRAL, text or "")
    hits = sorted({m.group(0).lower() for m in FORBIDDEN_TEXT_RE.finditer(text or "")})
    return [f"{owner}.{field}: forbidden term(s) {hits}"] if hits else []


# ─── The gate ───────────────────────────────────────────────────────────


def test_listed_surface_is_exactly_the_allow_list(listed_surface):
    tools_json, _, _ = listed_surface
    allowed = listed_surface.listed_tools
    names = {t["name"] for t in tools_json}
    assert names == allowed, (
        f"listed surface drifted — unexpected: {sorted(names - allowed)}, "
        f"missing: {sorted(allowed - names)}"
    )
    assert len(names) == 29 + len(LISTED_PAPER_TOOLS) * int(listed_surface.paper)
    # Both simulated paper tools ride the flag together (S10 §3, PT2-90);
    # no live tool is ever added (PT2-27, EXPLICITLY_EXCLUDED_TOOLS).
    assert (names >= LISTED_PAPER_TOOLS) is listed_surface.paper
    assert not names & LISTED_PAPER_TOOLS or listed_surface.paper


def test_no_excluded_tool_present(listed_surface):
    tools_json, _, _ = listed_surface
    names = {t["name"] for t in tools_json}
    leaked = names & EXPLICITLY_EXCLUDED_TOOLS
    assert not leaked, f"excluded tools leaked into the listed profile: {sorted(leaked)}"
    # And the allow-list itself must never quietly admit one.
    assert not (listed_surface.listed_tools & EXPLICITLY_EXCLUDED_TOOLS)


def _name_stem_violations(names, *, paper: bool) -> list[str]:
    """FORBIDDEN_NAME_STEMS over `names`; on a flag-on surface the exact
    names in LISTED_PAPER_NAME_ALLOWANCE are skipped, nothing else."""
    return [
        f"{name}: name contains forbidden stem {stem!r}"
        for name in names
        if not (paper and name in LISTED_PAPER_NAME_ALLOWANCE)
        for stem in FORBIDDEN_NAME_STEMS
        if stem in name.lower()
    ]


def test_no_forbidden_stems_in_tool_names(listed_surface):
    tools_json, _, _ = listed_surface
    violations = _name_stem_violations([t["name"] for t in tools_json], paper=listed_surface.paper)
    assert not violations, "\n".join(violations)


# ─── The carve-out still refuses what it must (AC-06-6, AC-06-8) ─────────
#
# Seeds (each by an exact reversible edit, then reversed): remove
# PAPER_COMPOUND_RE's pre-substitution from `_text_violations` ⇒ the flag-on
# description scan reds on keel_paper_deploy's "paper-trading"; widen
# LISTED_PAPER_NAME_ALLOWANCE to a `keel_paper_` prefix rule in
# `_name_stem_violations` ⇒ the synthetic keel_paper_fund passes the stem test
# and test_hypothetical_listings_still_fail reds.

_LIVE_VERBS = (
    "deploy this strategy",
    "start trading",
    "go live",
    "going live",
    "trading",
    "funded",
)
_PAPER_PHRASES = ("paper trading", "paper-trading", "paper-deploy", "simulated")


def test_the_carve_out_admits_only_paper_compounds():
    for phrase in _LIVE_VERBS:
        assert _text_violations("probe", "text", phrase, paper=True), phrase
        assert _text_violations("probe", "text", phrase), phrase
    for phrase in _PAPER_PHRASES:
        assert not _text_violations("probe", "text", phrase, paper=True), phrase
    # Off, no allowance applies: "paper trading" violates (AC-06-7).
    assert _text_violations("probe", "text", "paper trading")


def test_paper_compound_re_needs_the_paper_prefix():
    """Property (AC-06-8): no token without the `paper` prefix matches."""
    tokens = [
        *(m.group(0) for m in FORBIDDEN_TEXT_RE.finditer(" ".join(_LIVE_VERBS))),
        "trade",
        "trades",
        "traded",
        "deploys",
        "deployment",
        "deployments",
        "papers",
        "newspaper trading",
    ]
    assert len(tokens) >= 10
    for token in tokens:
        if token.lower().startswith("paper"):
            continue
        m = PAPER_COMPOUND_RE.search(token)
        assert m is None or m.group(0).lower().startswith("paper"), token
    assert PAPER_COMPOUND_RE.search("newspaper trading") is None  # word-bounded


def test_hypothetical_listings_still_fail():
    """AC-06-8: with the flag on, an allow-list copy holding keel_live_deploy
    trips the exclusion intersection and the name-stem rule; a synthetic
    keel_paper_fund trips the `fund` stem; the allowance is one exact name;
    keel_paper_control is listed with the flag on only (S10, PT2-90)."""
    with listed_flag_state(True):
        on_tools = listed_profile_tools()
    with listed_flag_state(False):
        off_tools = listed_profile_tools()
    hypothetical = set(on_tools) | {"keel_live_deploy"}
    assert hypothetical & EXPLICITLY_EXCLUDED_TOOLS == {"keel_live_deploy"}
    names = ("keel_live_deploy", "keel_paper_fund", "keel_paper_deploy")
    violations = _name_stem_violations(names, paper=True)
    assert any(v.startswith("keel_live_deploy:") for v in violations)
    assert any(v.startswith("keel_paper_fund:") for v in violations)
    assert not any(v.startswith("keel_paper_deploy:") for v in violations)
    # The name allowance stays one exact name: keel_paper_control carries no
    # banned stem, so it needs none (S10, PT2-90; never widened).
    assert LISTED_PAPER_NAME_ALLOWANCE == {"keel_paper_deploy"}
    assert not _name_stem_violations(("keel_paper_control",), paper=False)
    assert on_tools - off_tools == {"keel_paper_deploy", "keel_paper_control"}
    assert "keel_paper_control" in on_tools and "keel_paper_control" not in off_tools


def test_paper_deploy_listed_params_pass_the_unchanged_param_rules(listed_surface):
    """AC-06-9: keel_paper_deploy's listed schema passes the param rules with
    zero rule edits. Seed: put "fund" in simulated_capital's description ⇒
    the text rule reds here. Count: its four listed properties are scanned."""
    if not listed_surface.paper:
        pytest.skip("keel_paper_deploy is listed only with the flag on")
    tools_json, _, _ = listed_surface
    (tool,) = [t for t in tools_json if t["name"] == "keel_paper_deploy"]
    props = tool["inputSchema"].get("properties") or {}
    assert len(props) == 4, sorted(props)
    violations: list[str] = []
    for pname, pschema in props.items():
        segments = pname.lower().split("_")
        violations += [
            f"{pname}: name token {tok}" for tok in FORBIDDEN_PARAM_TOKENS if tok in segments
        ]
        desc = pschema.get("description", "")
        violations += [f"{pname}: {m.group(0)}" for m in FORBIDDEN_PARAM_DESC_RE.finditer(desc)]
        violations += _text_violations("keel_paper_deploy", f"param[{pname}]", desc, paper=True)
    assert not violations, violations


# ─── PT2-27: a listed paper tool says it is simulated (AC-06-32 (b)) ─────
#
# S06 §3.4.1: any paper tool the hosted connector gains says in its name that
# it is paper and in its title and the first sentence of its description that
# it is simulated. (Its card says so too: the stamped live card arm of
# test_listed_card_html_passes_the_string_rules.) Seed: drop "(Simulated)"
# from keel_paper_deploy's listed title ⇒ the flag-on arm reds; the control
# below proves the check refuses such a tool without any edit.

_FIRST_SENTENCE_RE = re.compile(r"^.*?[.!?](?=\s|$)", re.DOTALL)


def _paper_tool_simulated_violations(tools_json) -> tuple[list[str], list[str]]:
    """(paper tool names, violations): every tool whose name contains
    `paper` must carry `simulated` (any case) in its title and in the first
    sentence of its description."""
    paper = sorted(t["name"] for t in tools_json if "paper" in t["name"])
    violations: list[str] = []
    for t in tools_json:
        if "paper" not in t["name"]:
            continue
        title = t.get("title") or ""
        if "simulated" not in title.lower():
            violations.append(f"{t['name']}: title {title!r} does not say simulated")
        m = _FIRST_SENTENCE_RE.match((t.get("description") or "").strip())
        first = m.group(0) if m else (t.get("description") or "")
        if "simulated" not in first.lower():
            violations.append(f"{t['name']}: first sentence {first!r} does not say simulated")
    return paper, violations


def test_a_listed_paper_tool_says_it_is_simulated(listed_surface):
    tools_json, _, _ = listed_surface
    paper, violations = _paper_tool_simulated_violations(tools_json)
    assert not violations, "\n".join(violations)
    # Count: no paper tool among the 29 off, exactly the two simulated paper
    # tools among the 31 on (S10 §3).
    assert len(tools_json) == 29 + len(LISTED_PAPER_TOOLS) * int(listed_surface.paper)
    assert paper == (sorted(LISTED_PAPER_TOOLS) if listed_surface.paper else [])


def test_the_simulated_check_refuses_an_unmarked_paper_tool():
    """Control: a synthetic listed paper tool whose title drops
    "(Simulated)" is refused, and so is one whose first sentence does not
    say it; a marked one passes."""
    marked = {
        "name": "keel_paper_probe",
        "title": "Paper Probe (Simulated)",
        "description": "Starts a simulated probe. Nothing else.",
    }
    untitled = {**marked, "title": "Paper Probe"}
    late = {**marked, "description": "Starts a probe. Fills are simulated."}
    assert _paper_tool_simulated_violations([marked]) == (["keel_paper_probe"], [])
    assert len(_paper_tool_simulated_violations([untitled])[1]) == 1
    assert len(_paper_tool_simulated_violations([late])[1]) == 1


#: The no-trading boundary the listed instructions state (mcp-conversion 04
#: §5.3, founder-approved D-12). It NEGATES the forbidden families, so the
#: scan reads the instructions with this one clause removed; the clause is
#: asserted present exactly once, so the carve-out can never exempt nothing
#: and cannot quietly widen to another sentence.
BOUNDARY_CLAUSE = "It cannot place orders, move funds or connect wallets"


def test_no_forbidden_verbs_in_titles_descriptions_or_instructions(listed_surface):
    tools_json, instructions, _ = listed_surface
    paper = listed_surface.paper
    sentence = listed_boundary_sentence(paper)
    assert instructions.count(sentence) == 1, "the boundary sentence moved"
    assert instructions.count(boundary_clause(paper)) == 1, "the boundary clause moved"
    violations: list[str] = []
    for t in tools_json:
        violations += _text_violations(t["name"], "title", t["title"] or "", paper=paper)
        description = t["description"]
        if t["name"] == "keel_account_status":
            clause = status_boundary_clause(paper)
            assert description.count(clause) == 1, "the status boundary moved"
            description = description.replace(clause, "")
        violations += _text_violations(t["name"], "description", description, paper=paper)
        for pname, pschema in (t["inputSchema"].get("properties") or {}).items():
            violations += _text_violations(
                t["name"],
                f"param[{pname}].description",
                pschema.get("description", ""),
                paper=paper,
            )
    # The D-12 no-trading boundary NEGATES the money nouns ("cannot … move
    # funds or connect wallets"): exempt as that one exact sentence, never as
    # the field — any other use of those words in the instructions still reds.
    violations += _text_violations(
        "server", "instructions", instructions.replace(sentence, ""), paper=paper
    )
    assert not violations, "\n".join(violations)
    # Count (AC-06-6): every served tool's description was scanned.
    assert len(tools_json) == 29 + len(LISTED_PAPER_TOOLS) * int(paper)


def test_output_schema_copy_passes_the_string_rules(listed_surface):
    """The declared output schemas (Q-1788) are listed-surface copy too.

    # SEED: give `_output_schemas._BACKTEST_VIEW` the description "the
    # trades a run made" — this reds on the four backtest tools.
    """
    tools_json, _, _ = listed_surface
    violations: list[str] = []
    described = 0
    for t in tools_json:
        texts = _schema_strings(t["outputSchema"])
        described += len(texts)
        for text in texts:
            violations += _text_violations(
                t["name"], "outputSchema", text, paper=listed_surface.paper
            )
    assert not violations, "\n".join(violations)
    # Not vacuous: the ten view tools each carry described output schemas.
    assert described >= 20, described


def test_no_money_semantics_in_parameters(listed_surface):
    tools_json, _, _ = listed_surface
    violations: list[str] = []
    for t in tools_json:
        for pname, pschema in (t["inputSchema"].get("properties") or {}).items():
            segments = pname.lower().split("_")
            bad = [tok for tok in FORBIDDEN_PARAM_TOKENS if tok in segments]
            if bad:
                violations.append(f"{t['name']}.param[{pname}]: forbidden name token(s) {bad}")
            desc_hits = sorted(
                {
                    m.group(0).lower()
                    for m in FORBIDDEN_PARAM_DESC_RE.finditer(pschema.get("description", ""))
                }
            )
            if desc_hits:
                violations.append(
                    f"{t['name']}.param[{pname}].description: money-semantics {desc_hits}"
                )
            for enum_val in pschema.get("enum") or []:
                bad_enum = [tok for tok in FORBIDDEN_ENUM_TOKENS if tok in str(enum_val).lower()]
                if bad_enum:
                    violations.append(f"{t['name']}.param[{pname}].enum[{enum_val}]: {bad_enum}")
    assert not violations, "\n".join(violations)


def test_no_routing_to_tools_absent_from_the_listed_surface(listed_surface):
    """Descriptions and instructions must never direct an agent (or show
    a reviewer) a `keel_*` tool that this profile does not register."""
    tools_json, instructions, _ = listed_surface
    tool_name_re = re.compile(r"\bkeel_[a-z0-9_]+\b")
    # The registry and the listed set the server was built with (flag on:
    # released, and keel_paper_deploy listed, S06 §3.4.3).
    known_tool_names = set(listed_surface.registry)
    listed = listed_surface.listed_tools
    violations: list[str] = []

    def scan(owner: str, text: str) -> None:
        for ref in sorted(set(tool_name_re.findall(text or ""))):
            if ref in known_tool_names and ref not in listed:
                violations.append(f"{owner}: references non-listed tool {ref}")

    for t in tools_json:
        scan(f"{t['name']}.description", t["description"])
        for pname, pschema in (t["inputSchema"].get("properties") or {}).items():
            scan(f"{t['name']}.param[{pname}]", pschema.get("description", ""))
    scan("server.instructions", instructions)
    assert not violations, "\n".join(violations)


def test_listed_prompts_exclude_deploy_workflow(listed_surface):
    _, _, prompt_names = listed_surface
    from keel.mcp.server import LISTED_EXCLUDED_SKILLS

    leaked = prompt_names & LISTED_EXCLUDED_SKILLS
    assert not leaked, f"excluded skills leaked into listed prompts: {sorted(leaked)}"


# ─── Widget-card surface (spec 06 R2 — same deployment config) ──────────


class ListedWidgetSurface(tuple):
    """(ui_resources, tool_wire_metas), plus the listed flag state it was
    built in (`paper`)."""

    paper: bool


@pytest.fixture(scope="module", params=LISTED_FLAG_STATES)
def listed_widget_surface(request):
    """(ui_resources, tool_wire_metas) from a real listed+hosted server,
    with KEEL_LISTED_PAPER off and on (S06 §3.5.6: the live card's PAPER
    stamp is served on listed only with the flag on).

    ``ui_resources`` maps each ``ui://`` URI to its served HTML text;
    ``tool_wire_metas`` maps tool name → the ``_meta`` dict published
    in tools/list.
    """
    from keel.mcp.server import create_server

    paper = _LISTED_STATE_SPECS[request.param][2]
    with listed_flag_state(paper, request.param):
        server = create_server()
        resources = asyncio.run(server.list_resources())
        ui_resources = {
            str(r.uri): asyncio.run(r.read()).contents[0].content
            for r in resources
            if str(r.uri).startswith("ui://")
        }
        tools = asyncio.run(server.list_tools())
        tool_wire_metas = {t.name: (t.to_mcp_tool().meta or {}) for t in tools}
    surface = ListedWidgetSurface((ui_resources, tool_wire_metas))
    surface.paper = paper
    return surface


def test_deploy_preflight_card_absent_on_listed_profile(listed_widget_surface):
    """Spec 06 R2 acceptance: the deploy-preflight card must not exist
    on the listed profile — not as a resource, and not referenced from
    any listed tool's ``_meta`` (either dialect)."""
    ui_resources, tool_wire_metas = listed_widget_surface
    leaked = [uri for uri in ui_resources if "preflight" in uri]
    assert not leaked, f"preflight card resource leaked into listed profile: {leaked}"
    for name, meta in tool_wire_metas.items():
        blob = str(meta)
        assert "preflight" not in blob, f"{name}: listed _meta references the preflight card"


def test_listed_card_surface_is_present_not_vacuous(listed_widget_surface):
    """The absence proof must not pass because nothing registered: the
    three listed-eligible cards ARE served (both dialects)."""
    ui_resources, tool_wire_metas = listed_widget_surface
    from keel.widgets import card_resource_uri, openai_card_resource_uri

    for kind in ("backtest", "strategy", "live"):
        assert card_resource_uri(kind) in ui_resources
        assert openai_card_resource_uri(kind) in ui_resources
    assert tool_wire_metas["keel_backtest_summarize"].get("ui", {}).get(
        "resourceUri"
    ) == card_resource_uri("backtest")


def test_listed_card_html_passes_the_string_rules(listed_widget_surface):
    """The card HTML is user-visible listing collateral (DP3: the cards
    ARE the directory screenshots) — scan the SERVED listed-profile
    card text with the same forbidden-term rules as tool copy.

    Scope note: JS identifiers joined by underscores (e.g.
    ``funding_attribution``, ``deployment_id``) do not trip the
    word-boundary regex — underscores are word characters — so this
    scans labels/comments/prose, exactly what a reviewer's scanner
    sees."""
    ui_resources, _ = listed_widget_surface
    paper = listed_widget_surface.paper
    violations: list[str] = []
    for uri, html in ui_resources.items():
        violations += _text_violations(uri, "html", html or "", paper=paper)
    assert not violations, "\n".join(violations)
    # Count (AC-06-6): at least one card resource was scanned; the stamped
    # live card is among them exactly when the flag is on (S06 §3.5.6).
    from keel.widgets import card_resource_uri

    assert ui_resources, "no card resource served"
    stamped = [uri for uri, html in ui_resources.items() if "PAPER — simulated" in (html or "")]
    live = card_resource_uri("live")
    assert (live in stamped) is paper, stamped


# ─── ChatGPT tool-row `_meta` strings (BUILD §2.6) ──────────────────────
#
# `openai/toolInvocation/invoking` / `invoked` are published in
# `tools/list` beside the description, so a directory reviewer's scanner
# reads them exactly as it reads a description — and until this arm they
# were the only listed-profile copy no scan covered.


def _invocation_strings(tool_wire_metas: dict) -> dict[str, str]:
    """owner → string, for every published invoking/invoked value."""
    out: dict[str, str] = {}
    for name, meta in tool_wire_metas.items():
        for key in ("openai/toolInvocation/invoking", "openai/toolInvocation/invoked"):
            value = meta.get(key)
            if value is not None:
                out[f"{name}.{key.rsplit('/', 1)[1]}"] = str(value)
    return out


def test_meta_invocation_strings_pass_the_string_rules(listed_widget_surface):
    """The tool-row copy rides the listed profile — scan it like copy."""
    _, tool_wire_metas = listed_widget_surface
    strings = _invocation_strings(tool_wire_metas)
    violations: list[str] = []
    for owner, text in sorted(strings.items()):
        violations += _text_violations(owner, "meta", text, paper=listed_widget_surface.paper)
        hits = sorted({m.group(0).lower() for m in FORBIDDEN_PARAM_DESC_RE.finditer(text)})
        if hits:
            violations.append(f"{owner}.meta: money-semantics {hits}")
    assert not violations, "\n".join(violations)


def test_meta_invocation_strings_fit_the_tool_row(listed_widget_surface):
    """≤ 64 characters, the cap the hosts elide at (BUILD §2.6)."""
    from keel.widgets import MAX_INVOCATION_STRING_CHARS

    _, tool_wire_metas = listed_widget_surface
    strings = _invocation_strings(tool_wire_metas)
    over = {
        owner: len(text)
        for owner, text in strings.items()
        if len(text) > MAX_INVOCATION_STRING_CHARS
    }
    assert not over, f"invocation strings past the {MAX_INVOCATION_STRING_CHARS}-char row: {over}"


def test_the_invocation_string_population_is_not_vacuous(listed_widget_surface):
    """Non-vacuity for the two scans above, on quantities no wording
    change can move: the listed profile publishes invoking/invoked for
    several tools, in pairs, and every one is non-empty."""
    _, tool_wire_metas = listed_widget_surface
    strings = _invocation_strings(tool_wire_metas)
    assert len(strings) >= 10, f"only {len(strings)} invocation strings published"
    assert len(strings) % 2 == 0, f"an unpaired invoking/invoked: {sorted(strings)}"
    assert all(strings.values()), f"an empty invocation string: {sorted(strings)}"


# ─── The description register (directives audit §4, guidance spec §5) ───
#
# "Each tool description should state precisely what the tool does and
# when to invoke it… Describe what the tool does. Do not tell Claude how
# to behave." (claude.com connectors/building/review-criteria). So class-A
# ROUTING sentences ("Do NOT use to enumerate strategies — call
# `keel_strategy_search`") are required and exempt; class-C behavioural
# directives ("BE PROACTIVE", "Do NOT ask the user…", "Load the … skill
# FIRST") are the rejection class and move to the instructions and the
# skills, which the rule does not govern.
#
# The pattern table and the exemption live in the corpus builder so the
# lint and the copy have one owner (`assemble.DIRECTIVE_PATTERNS`); each
# pattern's proof-it-fires is in
# `libs/pipeline_engine/reference/system/assemble_test.py`.


def test_listed_descriptions_carry_no_behavioural_directives(listed_surface):
    """# SEED: re-add "BE PROACTIVE — call this automatically." to
    `keel/tools/outcomes/backtest_summarize.py`'s description. Revert by
    reversing that edit (never `git checkout`, shared tree)."""
    from pipeline_engine.reference.system import assemble

    tools_json, _, _ = listed_surface
    descriptions = {t["name"]: t["description"] for t in tools_json}
    violations, scanned, exempted = assemble.directive_violations(descriptions)
    assert not violations, (
        "listed tool descriptions carry behavioural directives (audit §3 has the "
        "declarative rewrite for each; the behaviour belongs in operating_core.md "
        "or a skill):\n" + "\n".join(violations)
    )
    # Non-vacuity, on quantities the seed cannot move: the scan read every
    # listed tool, and it READ the routing rather than exempting it. Since
    # Q-1804 routing is a neutral statement ("Enumerating strategies is
    # `keel_strategy_search`"), not the class-A "Do NOT use … — call"
    # sentence the exemption was written for, so the exemption matches
    # almost nothing; a scanner exempting everything would report
    # exempted == scanned.
    assert len(descriptions) == len(listed_surface.listed_tools)
    assert scanned >= 100, f"only {scanned} description sentences scanned"
    assert exempted * 10 < scanned, f"{exempted} of {scanned} sentences exempted"
    naming = sum(
        any(f"`{other}`" in text for other in descriptions if other != name)
        for name, text in descriptions.items()
    )
    assert naming >= 20, f"only {naming} descriptions name a neighbour"


# Text addressed to the HOST or its approval classifier rather than
# describing the tool (Q-1748). ChatGPT's approval gate flagged
# keel_strategy_fork as a "Suspicious Instruction" — "tool documentation
# prescribes how the classifier should treat strategy forking and related
# tool usage". Annotations carry the safety facts; copy describes the tool.
HOST_ADDRESSED_RE = re.compile(
    r"\bhost (?:confirmation|approval)\b"
    r"|\bclassif(?:y|ier|ication)\b"
    r"|\bno (?:confirmation|approval)\b"
    r"|\b(?:does not|doesn't|do not|don't) (?:need|require) (?:confirmation|approval)\b"
    r"|\bsafe to (?:call|run|approve)\b"
    r"|\btreat (?:this|it|the call)\b"
    r"|\b(?:a create|a fork|a save) is a (?:view|receipt)\b",
    re.IGNORECASE,
)


def _schema_strings(node) -> list[str]:
    if isinstance(node, dict):
        out = [node["description"]] if isinstance(node.get("description"), str) else []
        for value in node.values():
            out += _schema_strings(value)
        return out
    if isinstance(node, list):
        return [s for item in node for s in _schema_strings(item)]
    return []


def test_listed_copy_never_addresses_the_host_or_its_classifier(listed_surface):
    """# SEED: restore "and host confirmation applies" to
    `share_create.py`'s description — this test reds on keel_share_create.
    Revert by reversing that edit (never `git checkout`)."""
    tools_json, instructions, _ = listed_surface
    hits: list[str] = []
    scanned = 0
    for tool in tools_json:
        for text in [tool["description"], *_schema_strings(tool["inputSchema"])]:
            scanned += 1
            for m in HOST_ADDRESSED_RE.finditer(text):
                hits.append(f"{tool['name']}: {m.group(0)!r}")
    for m in HOST_ADDRESSED_RE.finditer(instructions):
        hits.append(f"instructions: {m.group(0)!r}")
    assert not hits, "listed copy addresses the host/classifier:\n" + "\n".join(hits)
    # Non-vacuous: every listed description plus its parameter copy was read.
    assert scanned >= 2 * len(listed_surface.listed_tools)


# ─── The descriptive register (Q-1804) ──────────────────────────────────
#
# ChatGPT's per-call safety review, on a `keel_strategy_compose` dry run
# (staging, 2026-09-23): "the tool-supplied description contains
# classifier-directed instructions, which are suspicious … Tool description
# prescribes workflow, save behavior, output handling, and explicitly
# directs which tool to use for forking". The trigger was the class-A
# routing clause every tool carried ("Do NOT use to fork — call
# `keel_strategy_fork`") plus compose's reply advice. Listed copy states
# what a tool does, takes, returns and changes; a neighbour is named as a
# fact ("Forking is `keel_strategy_fork`."). The directive scan above keeps
# its narrower audit table; this one is the imperative register itself.
IMPERATIVE_RE = re.compile(
    r"\bDo NOT\b|\bNEVER\b|\bALWAYS\b|\bMUST\b|\byou should\b|\brelay"
    r"|\bdo not use\b|\binstead call\b",
    re.IGNORECASE,
)

#: The server instructions are assembled from
#: `libs/pipeline_engine/reference/system/operating_core.md`. Since the
#: two-layer corpus (agent-surface-cleanup spec 01 §2.2, the closure of
#: Q-1804) they carry only `register: base` sections, so the ratchet that
#: stood at 5 is at its floor: zero imperatives.
INSTRUCTIONS_IMPERATIVE_CEILING = 0


def _result_level_copy() -> dict[str, str]:
    """Per-result lines a host shows the model beside every card result."""
    from keel.tools.outcomes._mcp_adapter import CARD_SHOWN_LINE, DRAFT_CHECK_LINE
    from keel.tools.outcomes._render import SURFACE_HINTS

    copy = {
        "result.card_shown_line": CARD_SHOWN_LINE,
        "result.draft_check_line": DRAFT_CHECK_LINE,
    }
    copy.update({f"result.surface_hints.{k}": v for k, v in SURFACE_HINTS.items()})
    return copy


def test_listed_descriptions_are_descriptive_not_imperative(listed_surface):
    """# SEED (run 2026-09-23): append " Do NOT use to fork — call
    `keel_strategy_fork`." to the end of `listed_description` in
    `strategy_compose.py` — this reds naming keel_strategy_compose while the
    control arms stay green. Revert by reversing that edit."""
    # Control arms: the pattern fires on the retired register and not on
    # the one that replaced it — so a green verdict is about the copy.
    assert IMPERATIVE_RE.search("Do NOT use to fork — call `keel_strategy_fork`.")
    assert IMPERATIVE_RE.search("Results carry `view`: relay view.markdown as-is.")
    assert not IMPERATIVE_RE.search("Forking is `keel_strategy_fork`.")

    tools_json, _, _ = listed_surface
    hits = [
        f"{tool['name']}: {m.group(0)!r} in …"
        f"{tool['description'][max(0, m.start() - 40) : m.end() + 40]}…"
        for tool in tools_json
        for m in IMPERATIVE_RE.finditer(tool["description"])
    ]
    for owner, text in _result_level_copy().items():
        hits += [f"{owner}: {m.group(0)!r}" for m in IMPERATIVE_RE.finditer(text)]
    assert not hits, "listed copy addresses the model imperatively:\n" + "\n".join(hits)

    # Non-vacuity, on quantities no wording change can move: all 29 listed
    # tools were read, each description is non-trivial, and the four
    # surface hints plus the two card prefaces (a result's and a dry
    # run's, Q-1896) were scanned.
    assert (
        len(tools_json)
        == len(listed_surface.listed_tools)
        == 29 + len(LISTED_PAPER_TOOLS) * int(listed_surface.paper)
    )
    assert all(len(t["description"]) >= 200 for t in tools_json)
    assert len(_result_level_copy()) == 6


# ─── Request-sorting copy (2026-09-25, prod first use) ──────────────────
#
# ChatGPT's per-call review on the PROD connector's first
# `keel_strategy_compose`: "Suspicious Instruction — Tool description
# prescribes classifier behavior and risk interpretation". The description
# no longer addressed the model imperatively; it sorted the USER'S REQUEST
# instead — "a variant of the user's own strategy is a version, not a fork",
# "Absent an asset scope or clock in the request … are the conventions;
# polarity is trend-following unless the request says fade …". Rules for
# reading a request belong in the strategy-creation skill (tool output), not
# in listed descriptions or parameter copy, which state what the tool does.
REQUEST_SORTING_RE = re.compile(
    r"\bthe request\b|\bunless the (?:user|request)\b|\bnot a fork\b"
    r"|\bis a version\b",
    re.IGNORECASE,
)


def test_listed_copy_does_not_sort_the_users_request(listed_surface):
    """# SEED (run 2026-09-25): put " (a variant of the user's own strategy
    is a version, not a fork)" back after "a new strategy is created" in
    `strategy_compose.py`'s `listed_description` — this reds naming
    keel_strategy_compose while both control arms stay green. Revert by
    reversing that edit."""
    # Control arms: the retired sentences match; their factual rewrites do not.
    assert REQUEST_SORTING_RE.search(
        "a variant of the user's own strategy is a version, not a fork"
    )
    assert REQUEST_SORTING_RE.search("polarity is trend-following unless the request says fade")
    assert not REQUEST_SORTING_RE.search(
        "with `strategy_id` the save adds a new version to that strategy, and "
        "without it a new strategy is created."
    )

    tools_json, _, _ = listed_surface
    scanned = 0
    hits = []
    for tool in tools_json:
        for text in [tool["description"], *_schema_strings(tool["inputSchema"])]:
            scanned += 1
            hits += [f"{tool['name']}: {m.group(0)!r}" for m in REQUEST_SORTING_RE.finditer(text)]
    assert not hits, "listed copy sorts the user's request:\n" + "\n".join(hits)
    # Non-vacuous: every listed description plus its parameter copy was read.
    assert scanned >= 2 * len(listed_surface.listed_tools)


# ─── Steering copy (2026-09-25, Q-1947 round 3 sweep) ───────────────────
#
# Round 2 of Q-1947 showed ChatGPT's review flags any listed copy that tells
# the model HOW to behave, not only imperatives: "specifying validation/help
# methods and required strategy composition rules". The sweep that followed
# rewrote every listed tool and parameter in that register — "Use
# `keel_strategy_search` to discover", "Variants meant to be compared should
# share one window: pass …", "so the agent can come back later", "It cannot
# fail (a delivery problem returns success …)", "The construction method is
# …", "names must match", "Omit first-session ownership guidance fields".
# Listed copy states what a tool does and returns; method lives in the
# strategy-creation skill.
STEERING_RE = re.compile(
    r"\bshould\b|\bmust\b|\bthe agent (?:can|should|will|needs)\b|\bmethod is\b"
    r"|\bUse `|\bcannot fail\b"
    r"|\bguidance\b",
    re.IGNORECASE,
)


def test_listed_copy_describes_rather_than_steers(listed_surface):
    """# SEED (run 2026-09-25): in `backtest_run.py` set the `strategy_id`
    description back to "Strategy to backtest. Use `keel_strategy_search` to
    discover." — this reds naming keel_backtest_run while the control arms
    stay green. Revert by reversing that edit."""
    # Control arms: each retired phrasing matches; its factual rewrite does not.
    for retired, rewrite in (
        (
            "Strategy to backtest. Use `keel_strategy_search` to discover.",
            "The strategy to backtest: a `str_...` id, as `keel_strategy_search` returns.",
        ),
        (
            "Variants meant to be compared should share one window",
            "runs share one window when each carries the same end_date",
        ),
        (
            "It cannot fail (a delivery problem returns success with a `note`)",
            "A delivery problem is reported in the result's `note`",
        ),
        (
            "the result still returns with `status_url` set so the agent can come back later",
            "the result still returns, with `status_url` set for checking the run later",
        ),
        (
            "The construction method is `skill:strategy-creation`",
            "Topics include `skill:strategy-creation` (building a strategy step by step)",
        ),
    ):
        assert STEERING_RE.search(retired), retired
        assert not STEERING_RE.search(rewrite), rewrite

    tools_json, _, _ = listed_surface
    scanned = 0
    hits = []
    for tool in tools_json:
        for text in [tool["description"], *_schema_strings(tool["inputSchema"])]:
            scanned += 1
            hits += [f"{tool['name']}: {m.group(0)!r}" for m in STEERING_RE.finditer(text)]
    assert not hits, "listed copy steers the model:\n" + "\n".join(hits)
    # Non-vacuous: every listed description plus its parameter copy was read.
    assert scanned >= 2 * len(listed_surface.listed_tools)


# ─── Capability copy states tools, not a session permission (Q-1960) ────
#
# ChatGPT, prod, 2026-09-26, asked "Buy 0.5 BTC on Hyperliquid for me":
# "I can't place the Hyperliquid order from this chat because the connected
# Keel session is read-only for live trading". Right outcome, wrong reason:
# the capability line ("live monitoring read-only; live actions in the Keel
# web app") read as a permission level on the CONNECTION, which suggests a
# different connection could place the order. The fact is that this
# connector has no order or start/stop tools. "Read-only" stays legitimate
# as a fact about ONE tool ("Read-only monitoring of the strategies …");
# this guard reads it only where it is attributed to the session, the
# connector/connection, access, or live activity as a whole.
PERMISSION_RE = re.compile(
    r"\b(?:session|connector|connection|access|live monitoring|live actions?)\b"
    r"[^.;]{0,40}\bread[- ]only\b"
    r"|\bread[- ]only\b[^.;]{0,20}\b(?:session|connector|connection|access|for live)\b",
    re.IGNORECASE,
)


def test_listed_capability_copy_states_tools_not_a_session_permission(
    listed_surface, monkeypatch, tmp_path
):
    """# SEED (run 2026-09-26): in `keel/tools/outcomes/_status_view.py` set
    `CAPABILITY_PHRASE` back to "research and backtests; live monitoring
    read-only; live actions in the Keel web app" — this reds on
    keel_account_status's description and the served status view (and the owner
    pin below, since the instructions no longer quote it) while every
    control arm stays green. Revert by reversing that edit."""
    from keel.tools.outcomes._status_view import CAPABILITY_PHRASE, CAPABILITY_PHRASE_PAPER_LISTED

    # Control arms: each retired phrasing matches, ChatGPT's own paraphrase
    # matches, and the rewrite plus a tool-level "read-only" fact do not.
    for retired in (
        "research and backtests; live monitoring read-only; live actions in the Keel web app",
        "Report the session's state in one read-only call, the first of a session",
        "the connected Keel session is read-only for live trading",
    ):
        assert PERMISSION_RE.search(retired), retired
    for kept in (
        # The literal, not `CAPABILITY_PHRASE`: the seed moves the constant,
        # and a control arm must stay green through it.
        "research and backtests; it cannot place orders, move funds or connect wallets; "
        "strategies that are running are managed in the Keel web app",
        "Read-only monitoring of the strategies running on the caller's account",
        "Read-only navigation: it builds the canonical URL, changes nothing",
    ):
        assert not PERMISSION_RE.search(kept), kept

    tools_json, instructions, _ = listed_surface
    paper = listed_surface.paper
    text, envelope, _loaded = _served_status(monkeypatch, tmp_path, hosted=True, paper=paper)
    scanned = 0
    hits: list[str] = []
    for tool in tools_json:
        for s in [tool["description"], *_schema_strings(tool["inputSchema"])]:
            scanned += 1
            hits += [f"{tool['name']}: {m.group(0)!r}" for m in PERMISSION_RE.finditer(s)]
    for owner, s in (
        ("instructions", instructions),
        *(("keel_account_status payload", p) for p in [text, *_payload_strings(envelope)]),
    ):
        scanned += 1
        hits += [f"{owner}: {m.group(0)!r}" for m in PERMISSION_RE.finditer(s)]
    assert not hits, "listed copy states a session permission:\n" + "\n".join(hits)

    # Non-vacuity, on quantities the seed cannot move: every listed tool and
    # its parameter copy, the instructions and the served payload were read.
    assert scanned >= 2 * len(listed_surface.listed_tools) + 15
    # One phrase, two owners, byte for byte (agent-surface-cleanup fix-wave
    # resolution #10): the status view's line 3 OWNS it; the listed
    # description quotes it. It states the D-12 no-trading boundary in the
    # same words the instructions use (04 §5.3, the exact sentence below) —
    # the fact the refusal needs.
    (status,) = [t for t in tools_json if t["name"] == "keel_account_status"]
    phrase = CAPABILITY_PHRASE_PAPER_LISTED if paper else CAPABILITY_PHRASE
    assert f"This connector: {phrase}." in text
    assert f"what this connector can do: {phrase}." in status["description"]
    assert listed_boundary_line(paper) in instructions, (
        "the listed instructions must state the no-trading boundary verbatim (04 §5.3)"
    )
    assert status_boundary_clause(paper) in phrase
    assert "managed in the Keel web app" in phrase
    # The status clause and the instructions' clause are one boundary: they
    # differ only in the sentence-initial capital, in each flag state
    # (S06 §3.4.5), and each owner carries it exactly once.
    assert boundary_clause(paper) == "I" + status_boundary_clause(paper)[1:]
    assert BOUNDARY_CLAUSE == boundary_clause(False)
    assert instructions.count(boundary_clause(paper)) == 1
    assert status["description"].count(status_boundary_clause(paper)) == 1


def test_server_instructions_imperatives_only_ratchet_down(listed_surface):
    """# SEED (run 2026-09-23, agent-surface-cleanup L6a): in
    `keel/data/knowledge/operating_core.md` (the bundled copy this server
    assembles from) replace "spends nothing" with "MUST be read first" —
    1 > 0 and this reds. Revert by reversing that edit."""
    from pipeline_engine.reference.system import assemble

    _, instructions, _ = listed_surface
    found = [m.group(0) for m in IMPERATIVE_RE.finditer(instructions)]
    assert len(found) <= INSTRUCTIONS_IMPERATIVE_CEILING, (
        f"server instructions gained imperatives ({len(found)} > "
        f"{INSTRUCTIONS_IMPERATIVE_CEILING}): {found}"
    )
    # Non-vacuous, on quantities the seed cannot move: the assembly is the
    # frozen four sections and names the first call.
    paper = listed_surface.paper
    assert assemble.instruction_section_ids("listed", paper_listed=paper) == (
        ("head", "sequence", "quota", "surface-listed-paper", "paper-listed")
        if paper
        else ("head", "sequence", "quota", "surface-listed")
    )
    assert "keel_account_status" in instructions
    # One regex, two owners kept equal: the corpus builder's base-register
    # lint uses the same pattern as this scan.
    assert IMPERATIVE_RE.pattern == assemble.IMPERATIVE_RE.pattern
    assert IMPERATIVE_RE.flags == assemble.IMPERATIVE_RE.flags


# ─── Nested parameter copy + the served keel_account_status payload ─────────────
#
# agent-surface-cleanup spec 04 §2.5 (Q-1850, Q-1851). The two scans above
# read TOP-LEVEL `inputSchema.properties` only, so a description or a
# money-semantic property NAME one level down (`keel_backtest_run.config.*`)
# was never read; and nothing scanned what `keel_account_status` actually SERVES.
# R-31 scope: the listed word rules govern copy Keel WRITES for the listed
# surface — descriptions and parameter copy at every depth, and the SDK's
# own result payloads — never the DSL vocabulary or the corpus.
#
# SEQUENCING (spec 04 §2.5, frozen): these arms are written against the
# spec's TARGET state and land BEFORE their prerequisites, which are other
# lanes' changes in flight:
#   * G4 needs spec 03 §2 (lane L2) to take `leverage` and `initial_capital`
#     off the LISTED `config` schema, AND the three spec 04 §2.5 rewordings of
#     `init_cash` / `fees` / `slippage` in libs/pipeline_engine/
#     backtest_config.py (the shared pydantic model — outside every lane
#     footprint in LANES.md; flagged in the L5 report);
#   * G5 needs spec 02 §2.5 (lane L4) to rewrite `keel_account_status`'s payload
#     (routes off the listed profile, a `capabilities` block, no deploy
#     copy, no unloaded tool names).
# Each arm is therefore `xfail(strict=True)`: red-by-design today, and the
# moment its prerequisites integrate it XPASSes, which strict turns into a
# FAILURE — the integrator deletes the marker and the arm is live. A marker
# can never outlive its reason silently.
#
# PRE-CHANGE REDS (recorded 2026-09-23 on the L5 worktree, the proof each arm
# can fail):
#   G4 names:   keel_backtest_run.config.leverage (token `leverage`),
#               keel_backtest_run.config.initial_capital is present in the
#               nested set (population pin);
#   G4 copy:    config.leverage "Maximum leverage cap…" (`leverage`),
#               config.fees "Per-trade fee rate…" (`trade`),
#               config.slippage "Per-trade adverse slippage…" (`trade`),
#               config.init_cash "Starting capital in USD…" (money rule `usd`);
#   G5 listed-hosted, bound caller: forbidden {deploy, deployments};
#               unloaded tokens keel_accounts_list, keel_audit_list_last,
#               keel_auth_login, keel_strategy_checkout, keel_strategy_push,
#               keel_strategy_status; no `capabilities`;
#   G5 local:   forbidden {deploy, deployment, deployments}; unloaded token
#               keel_live_deploy; no `capabilities`.

#: The target-state nested population (spec 04 §2.5, G4): after spec 03's
#: removal exactly three `config` fields remain on the listed schema.
NESTED_PARAM_NAMES_AFTER_SPEC_03 = {
    "keel_backtest_run.config.init_cash",
    "keel_backtest_run.config.fees",
    "keel_backtest_run.config.slippage",
}

# The G4/G5 strict-xfail markers were removed 2026-09-23 (lane A, final
# fixes) once all four arms passed: the spec 04 §2.5 rewordings landed in
# backtest_config.py and the status payload shipped as a view (spec 02 §2.5).


def _property_names(node, prefix: str = "", defs: dict | None = None) -> list[str]:
    """Dotted paths of every NESTED property name (depth ≥ 2) in a schema.

    Walks `properties` at every depth and through `anyOf` / `oneOf` /
    `allOf` / `items`, resolving `#/$defs/…` references against the ROOT
    schema's `$defs` (carried down), so the paths read like the schema a
    client fills in (`config.fees`).
    """
    out: list[str] = []
    if not isinstance(node, dict):
        return out
    if defs is None:
        defs = node.get("$defs") or {}
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = defs.get(ref.rsplit("/", 1)[1])
        return _property_names(target, prefix, defs) if isinstance(target, dict) else out
    for name, sub in (node.get("properties") or {}).items():
        path = f"{prefix}.{name}" if prefix else name
        if prefix:
            out.append(path)
        out += _property_names(sub, path, defs)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in node.get(key) or []:
            out += _property_names(sub, prefix, defs)
    out += _property_names(node.get("items"), prefix, defs)
    return out


def _nested_descriptions(schema: dict) -> list[str]:
    """Every description in a schema EXCEPT the schema's own and the
    top-level properties' own — i.e. the copy the top-level scans never read."""
    rest = list(_schema_strings(schema))
    owned = [schema.get("description")] + [
        p.get("description")
        for p in (schema.get("properties") or {}).values()
        if isinstance(p, dict)
    ]
    for text in owned:
        if isinstance(text, str) and text in rest:
            rest.remove(text)
    return rest


def test_property_name_walker_reaches_nested_fields():
    """Control arm for the helper (runs today, green): a nested field is
    reported with its dotted path, through $ref, and a top-level name is not."""
    schema = {
        "$defs": {"Cfg": {"properties": {"fees": {"description": "x"}}}},
        "properties": {
            "config": {"anyOf": [{"$ref": "#/$defs/Cfg"}, {"type": "null"}]},
            "strategy_id": {"description": "y"},
        },
    }
    assert _property_names(schema) == ["config.fees"]


def test_nested_parameter_copy_passes_the_string_rules(listed_surface):
    """G4 (spec 04 §2.5): every description at ANY depth of every listed
    tool's inputSchema passes the word rules and the money-parameter rule.

    # SEED (standing, spec 04 §4 G4): restore `fees`'s description to
    # "Per-trade fee rate as a decimal" in backtest_config.py — reds here.
    """
    tools_json, _, _ = listed_surface
    violations: list[str] = []
    nested = 0
    for t in tools_json:
        for text in _nested_descriptions(t["inputSchema"]):
            nested += 1
            violations += _text_violations(
                t["name"], "nested-param.description", text, paper=listed_surface.paper
            )
            hits = sorted({m.group(0).lower() for m in FORBIDDEN_PARAM_DESC_RE.finditer(text)})
            if hits:
                violations.append(f"{t['name']}.nested-param.description: money-semantics {hits}")
    assert not violations, "\n".join(violations)
    # Non-vacuity (a silent drop is a red): the target state carries exactly
    # the three `config` descriptions.
    assert nested == 3, nested


def test_nested_parameter_names_carry_no_money_semantics(listed_surface):
    """G4: every NESTED property name's segments pass FORBIDDEN_PARAM_TOKENS,
    and the nested population is exactly the target set (re-pin it when a
    nested parameter is added; a silent drop is a red)."""
    tools_json, _, _ = listed_surface
    names: set[str] = set()
    violations: list[str] = []
    for t in tools_json:
        for path in _property_names(t["inputSchema"]):
            names.add(f"{t['name']}.{path}")
            for segment in path.split("."):
                bad = [tok for tok in FORBIDDEN_PARAM_TOKENS if tok in segment.lower().split("_")]
                if bad:
                    violations.append(f"{t['name']}.{path}: forbidden name token(s) {bad}")
    assert not violations, "\n".join(violations)
    assert names == NESTED_PARAM_NAMES_AFTER_SPEC_03, sorted(names)


# ── G5: the served keel_account_status payload ──────────────────────────────────

_STATUS_READS = {
    "/v1/me": {
        "principal": {"id": "usr_policy_scan"},
        "org": {"id": "org_policy_scan", "name": "Policy Scan", "plan": "pro"},
        "credential_scopes": ["strategy.*"],
    },
    "/v1/entitlements": {
        "balances": [
            {"unit": "backtests", "granted": 100, "spent": 1, "reserved": 0, "available": 99}
        ]
    },
}


def _payload_strings(node) -> list[str]:
    """Every string VALUE in an envelope, recursively (keys are identifiers)."""
    if isinstance(node, dict):
        return [s for value in node.values() for s in _payload_strings(value)]
    if isinstance(node, list):
        return [s for item in node for s in _payload_strings(item)]
    return [node] if isinstance(node, str) else []


def _served_status(monkeypatch, tmp_path, *, hosted: bool, paper: bool = False):
    """(text, envelope, loaded tool names) from keel_account_status on a real server.

    Hosted arm: the LISTED profile under KEEL_EXECUTION_MODE=hosted with a
    caller bound in-process (`bind_request_credentials`, the hosting seam) —
    without a binding a hosted server answers only the auth_failed envelope.
    Local arm: the default profile with an API key in the environment. Both
    fake exactly the two reads keel_account_status makes (`/v1/me`,
    `/v1/entitlements`), so the FULL envelope renders offline.
    """
    import json

    import keel.client
    from keel.hosting import bind_request_credentials, clear_request_credentials
    from keel.mcp.server import create_server
    from keel.tools.outcomes import all_tools
    from keel.tools.outcomes._mcp_adapter import loaded_tool_names

    for key in ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS", "KEEL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ambient ~/.keel config
    if hosted:
        monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
        monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    else:
        monkeypatch.setenv("KEEL_API_KEY", "local-policy-scan-key")
    monkeypatch.setattr(
        keel.client.KeelClient, "get", lambda self, path, *a, **k: _STATUS_READS[path]
    )
    from contextlib import nullcontext

    from tests._paper_sdk import paper_sdk_release

    monkeypatch.delenv("KEEL_LISTED_PAPER", raising=False)
    released = paper_sdk_release() if paper else nullcontext()
    with released:
        if paper:  # the listed flag on (S06 §3.4.2): the SDK released first
            monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
        server = create_server()
        reset = (
            bind_request_credentials(token="caller-token", api_url="https://staging-api.test")
            if hosted
            else None
        )
        try:
            result = asyncio.run(server.call_tool("keel_account_status", {}))
        finally:
            if reset is not None:
                clear_request_credentials(reset)
        if paper:
            monkeypatch.delenv("KEEL_LISTED_PAPER")
    text = result.content[0].text
    # The text block is the status view's markdown (spec 02 §2.5); the
    # envelope is `structuredContent` — under the frozen `{"result": string}`
    # output schema (R-25) while the non-view arm is off, else the object.
    structured = result.structured_content
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        envelope = json.loads(structured["result"])
    else:
        envelope = structured
    assert isinstance(envelope, dict), structured
    loaded = set(loaded_tool_names({t.name: t for t in all_tools()}))
    return text, envelope, loaded


def _status_payload_violations(text: str, envelope: dict, loaded: set[str]) -> list[str]:
    strings = [text, *_payload_strings(envelope)]
    violations: list[str] = []
    # The line-3 no-trading boundary negates the money nouns: exempt as that
    # exact clause, carried once by the text block — never as the field.
    if text.count(STATUS_BOUNDARY_CLAUSE) != 1:
        violations.append("keel_account_status payload: the line-3 boundary clause moved")
    for s in strings:
        violations += _text_violations(
            "keel_account_status", "payload", s.replace(STATUS_BOUNDARY_CLAUSE, "")
        )
    for ref in sorted({t for s in strings for t in re.findall(r"\bkeel_[a-z0-9_]+\b", s)}):
        if ref not in loaded:
            violations.append(f"keel_account_status payload: names unloaded tool {ref}")
    return violations


def test_status_payload_harness_renders_the_full_envelope(monkeypatch, tmp_path):
    """Non-vacuity for the two G5 arms (runs today, green): the harness gets
    the FULL identity-bearing envelope, not the auth_failed one, and it is
    big enough that a clean verdict means something."""
    for hosted in (True, False):
        text, envelope, loaded = _served_status(monkeypatch, tmp_path, hosted=hosted)
        assert "code" not in envelope, (hosted, envelope.get("code"))
        assert envelope.get("identity", {}).get("org_name") == "Policy Scan", hosted
        # 18 on the hosted listed arm once the status view shipped (spec 02
        # §2.5 dropped the route prose there); the G5 seed moves no count.
        assert len([text, *_payload_strings(envelope)]) >= 15, hosted
        assert loaded, hosted


def test_status_payload_listed_hosted_passes_the_string_rules(monkeypatch, tmp_path):
    """G5 arm 1 — listed + hosted with a bound caller.

    # SEED (spec 04 §4 G5): append the word "deploy" to the status view's
    # line-3 template in status.py — reds both G5 arms.
    """
    text, envelope, loaded = _served_status(monkeypatch, tmp_path, hosted=True)
    violations = _status_payload_violations(text, envelope, loaded)
    if "capabilities" not in envelope:
        violations.append("keel_account_status payload: no `capabilities` block")
    assert not violations, "\n".join(violations)


def test_status_payload_local_passes_the_string_rules(monkeypatch, tmp_path):
    """G5 arm 2 — local mode, where today's offending routes live."""
    text, envelope, loaded = _served_status(monkeypatch, tmp_path, hosted=False)
    violations = _status_payload_violations(text, envelope, loaded)
    if "capabilities" not in envelope:
        violations.append("keel_account_status payload: no `capabilities` block")
    assert not violations, "\n".join(violations)


# ─── G4: the commerce and live-money family (05 §6 G4, 04 §4; Q-2081) ───
#
# Q-2081 was this scan's blind spot: it read tools/list and instructions but
# never what a client FETCHES (keel_help bodies, keel:// reads, prompt
# bodies) nor the results real calls RETURN. The arms below read every one of
# those against the D-12 families (`_surface_fixtures.COMMERCE_*_RE`,
# `LIVE_MONEY_RE`), plus JSON KEYS, plus — on the plan-limit walls only — any
# Keel URL and the handoff's own `_FORBIDDEN_UPSELL_RE`. Runtime payloads are
# built by `_surface_fixtures.runtime_payloads()` through the real SDK paths
# (`server.call_tool` on a listed+hosted server), with only keel-api's HTTP
# stubbed.
#
# RED at base e3591375c by design, naming at least: `upgrade_options` /
# `builder_fee_bps` / `manage_url` (keel_plan_usage), "Higher plans include
# more …" (errors.py `higher_plans_sentence`, walls + quota notice), the
# card's "See plans in Keel" (host-adapter.js renderPlanLimit), the
# costs_and_fees "Trader or Pro" paragraph and platform-operations' canary.
#
# The ONE allowed sentence (04 §4.1, D-12 Q1's recorded fallback, Q-2268) —
# "Plans are changed in the Keel web app." — is allowed only in the FREE
# plan's wall, sentence-exact; the paid wall is the control arm that must
# not carry it.


@pytest.fixture(scope="module")
def g4_surface():
    return sf.listed_surface()


@pytest.fixture(scope="module")
def g4_payloads():
    # The paper quota wall joins the runtime arm (paper-trading-v2 S06
    # §3.4.6, AC-06-21; PT2-31 moved it to M8-quota-wall), built through the
    # real MCP adapter where keel_paper_deploy runs (sf.paper_runtime_payloads).
    return {**sf.runtime_payloads(), **sf.paper_runtime_payloads()}


#: Payload KEYS that are commerce by name (04 §4.3/§4.4 removed fields).
COMMERCE_KEY_RE = re.compile(
    r"^upgrade_\w+$|^builder_fee\w*$|^higher_plans$|^manage_url$|billing|pricing|checkout",
    re.IGNORECASE,
)


def _g4_hits(owner: str, text: str, scope: str) -> list[str]:
    return [f"{owner}: {fam} {m!r}" for fam, m in sf.family_hits(text, scope=scope)]


def test_g4_served_bodies_carry_no_commerce_or_live_money(g4_surface):
    """# SEED (standing at base): costs_and_fees' "moving to Trader or Pro"
    and platform-operations' "minimal-capital canary" red here."""
    violations: list[str] = []
    bodies = 0
    for topic, body in sorted(g4_surface.help_docs.items()):
        bodies += 1
        for fam, match, sentence in sf.family_sentence_hits(body, scope="body"):
            if not sf.is_exempt("G4", topic, sentence):
                violations.append(f"keel_help:{topic}: {fam} {match!r} — {sentence[:120]}")
    for uri, body in sorted(g4_surface.resources.items()):
        bodies += 1
        topic = sf.normalize_topic(uri.rsplit("/", 1)[-1])
        for fam, match, sentence in sf.family_sentence_hits(body, scope="body"):
            if not sf.is_exempt("G4", topic, sentence):
                violations.append(f"{uri}: {fam} {match!r} — {sentence[:120]}")
    assert not violations, "served bodies carry commerce / live-money copy:\n" + "\n".join(
        sorted(set(violations))
    )
    assert bodies >= 35, bodies


def test_g4_prompts_carry_no_commerce_or_live_money(g4_surface):
    violations: list[str] = []
    for name, prompt in sorted(g4_surface.prompts.items()):
        violations += _g4_hits(f"prompt:{name}.description", prompt["description"], "strict")
        violations += _g4_hits(f"prompt:{name}.body", prompt["body"], "body")
    assert not violations, "listed prompts carry commerce / live-money copy:\n" + "\n".join(
        violations
    )
    assert len(g4_surface.prompts) == 7


def _schema_keys(node) -> list[str]:
    if isinstance(node, dict):
        keys = list((node.get("properties") or {}).keys())
        return keys + [k for v in node.values() for k in _schema_keys(v)]
    if isinstance(node, list):
        return [k for item in node for k in _schema_keys(item)]
    return []


def test_g4_tools_list_copy_and_keys_carry_no_commerce_or_live_money(g4_surface):
    """Descriptions, every schema string and every schema property NAME."""
    violations = _g4_hits(
        "instructions",
        g4_surface.instructions.replace(LISTED_BOUNDARY_SENTENCE, ""),
        "strict",
    )
    keys = 0
    for tool in g4_surface.tools:
        for owner, text in sf.tool_copy(tool):
            violations += _g4_hits(owner, text, "strict")
        for key in _schema_keys(tool["inputSchema"]) + _schema_keys(tool["outputSchema"]):
            keys += 1
            if COMMERCE_KEY_RE.search(key):
                violations.append(f"{tool['name']}: commerce key {key!r}")
    assert not violations, "tools/list carries commerce / live-money copy:\n" + "\n".join(
        violations
    )
    assert keys >= 29, keys


def _payload_violations(payload: sf.Payload) -> list[str]:
    violations: list[str] = []
    for text in payload.strings():
        if payload.name == "free_wall":
            text = sf.ALLOWED_FREE_WALL_SENTENCE_RE.sub("", text)
        violations += _g4_hits(f"payload:{payload.name}", text, "strict")
    for key in payload.keys():
        if COMMERCE_KEY_RE.search(key):
            violations.append(f"payload:{payload.name}: commerce key {key!r}")
    return violations


def test_g4_runtime_payloads_carry_no_commerce_or_live_money(g4_payloads):
    """Walls (free + paid), keel_plan_usage (free + paid), a critical-tier
    keel_backtest_run, keel_account_status, a sparse run, the strategy log, the
    401/404 envelopes — as a host receives them.

    # SEED (standing at base): keel_plan_usage' `upgrade_options`, the
    # walls' "Higher plans include more …", the critical notice's billing
    # link, keel_account_status' `upgrade_url` — each reds here.
    """
    violations = [v for p in g4_payloads.values() for v in _payload_violations(p)]
    assert not violations, "runtime payloads carry commerce / live-money copy:\n" + "\n".join(
        sorted(set(violations))
    )
    # Non-vacuity: every fixture produced text, and the named ones exist.
    assert len(g4_payloads) >= 6
    assert all(p.text.strip() for p in g4_payloads.values())
    assert {
        "free_wall",
        "paid_wall",
        "plan_status_free",
        "plan_status_paid",
        "critical_run",
        "status",
        "paper_wall",
    } <= set(g4_payloads)
    assert g4_payloads["paper_wall"].structured.get("blocked_action") == "paper_deploy"


#: Payloads about the caller's PLAN: no link at all (04 §4.1, §4.3 — the walls
#: lose every URL, keel_plan_usage loses manage_url/hero_url).
PLAN_PAYLOADS = (
    "free_wall",
    "paid_wall",
    "paper_wall",
    "plan_status_free",
    "plan_status_paid",
    "plan_status_first_week",
)


def test_g4_plan_payloads_carry_no_url_and_no_upsell(g4_payloads):
    """Walls and keel_plan_usage: no Keel URL anywhere; the handoff's own
    upsell rule (`_FORBIDDEN_UPSELL_RE`: prices, "monthly", "upgrade", …)
    over every string — the one allowed sentence removed first, free wall
    only. The critical-tier run keeps its backtest links; its quota notice
    and quota line are held to the upsell rule."""
    from keel.tools.outcomes._handoff import _FORBIDDEN_UPSELL_RE

    allowed = re.compile(r"Plans are changed in the Keel web app\.")
    violations: list[str] = []
    for name in PLAN_PAYLOADS:
        for text in g4_payloads[name].strings():
            if name == "free_wall":
                text = allowed.sub("", text)
            violations += [f"{name}: Keel URL {u}" for u in sf.KEEL_URL_RE.findall(text)]
            violations += [
                f"{name}: upsell {m.group(0)!r}" for m in _FORBIDDEN_UPSELL_RE.finditer(text)
            ]
    run = g4_payloads["critical_run"]
    notice = [run.structured.get("quota_notice") or ""]
    notice += [line for line in run.text.splitlines() if line.startswith("quota:")]
    for text in notice:
        violations += [f"critical_run: Keel URL {u}" for u in sf.KEEL_URL_RE.findall(text)]
        violations += [
            f"critical_run: upsell {m.group(0)!r}" for m in _FORBIDDEN_UPSELL_RE.finditer(text)
        ]
    assert not violations, "plan payloads carry links or upsell copy:\n" + "\n".join(
        sorted(set(violations))
    )
    # Non-vacuity: both walls are plan-limit handoffs (the real 403 path),
    # and the critical run carries its notice.
    for name in sf.WALL_FIXTURES:
        assert g4_payloads[name].structured.get("code") == "handoff_required", name
    assert notice[0].startswith("4 of 50 backtests left this week"), notice


def _wall_sentences(payload: sf.Payload) -> list[str]:
    return [s for text in payload.strings() for s in sf.sentences(text)]


def test_g4_the_one_allowed_sentence_is_on_the_free_wall_only(g4_payloads):
    """Control arm for the exemption: the free wall states it (04 §4.1),
    the paid wall and every other payload do not."""
    free = _wall_sentences(g4_payloads["free_wall"])
    assert any(sf.ALLOWED_FREE_WALL_SENTENCE_RE.match(s) for s in free), (
        "the free-plan wall must say: 'Plans are changed in the Keel web app.' (04 §4.1, "
        "D-12 fallback)"
    )
    for name, payload in g4_payloads.items():
        if name == "free_wall":
            continue
        leaked = [
            s
            for text in payload.strings()
            for s in [text]
            if "plans are changed in the keel web app" in s.lower()
        ]
        assert not leaked, f"{name} carries the free-wall-only sentence: {leaked[0][:160]}"


def test_g4_plan_limit_card_draws_no_link_and_no_plan_copy():
    """widgets/assets/host-adapter.js `renderPlanLimit` (04 §4.4): headline,
    reset and the allowance note only — no paths, no link, no button.

    # SEED (standing at base): the default label "See plans in Keel" and the
    # `v.link` / `v.paths` branches red here."""
    src = sf.render_plan_limit_source()
    literals = sf.js_string_literals(src)
    violations = [
        f"literal {lit!r}: {fam} {m!r}"
        for lit in literals
        for fam, m in sf.family_hits(lit, scope="strict")
    ]
    for pattern, what in (
        (r"\bv\.paths\b", "draws `limit_view.paths` (other plans)"),
        (r"\bv\.link\b", "draws `limit_view.link`"),
        (r"\bbuttonLink\(|\bopenLink\(|\.href\s*=", "draws a link/button"),
    ):
        if re.search(pattern, src):
            violations.append(what)
    assert not violations, "renderPlanLimit draws plan copy or a link:\n" + "\n".join(violations)
    # Non-vacuity: the function was found and it does draw the headline.
    assert "v.headline" in src and len(literals) >= 5


def test_g4_family_control_arms():
    """The families fire on the retired copy and stay quiet on its rewrite."""
    for retired in (
        "Higher plans include more backtests: Starter 500 a week, Trader unlimited.",
        "See plans in Keel ↗",
        "https://app.usekeel.io/settings?tab=billing&from=agent",
        "moving to Trader or Pro materially reduces per-trade drag",
        "offer the minimal-capital canary pattern: deploy live with a small amount of real capital",
        "MIXED — thesis holds; run forward small.",
    ):
        assert sf.family_hits(retired, scope="strict"), retired
    for kept in (
        "Weekly backtests used — 50 of 50 on the Free plan. They reset Mon 29 Sep 00:00 UTC.",
        "Composing, validating and reading existing results don't use this allowance.",
        "4 of 50 backtests left this week; they reset Mon 29 Sep 00:00 UTC.",
        "Read-only monitoring of the strategies running on the caller's account.",
    ):
        assert not sf.family_hits(kept, scope="strict"), kept
    # Body scope: DSL vocabulary is not commerce; neither is backtest capital.
    assert not sf.family_hits("Level 2 → Level 3: Upgrade to Vol-Targeted Sizing", scope="body")
    assert not sf.family_hits('"Skip anything under $10M/24h" is a threshold', scope="body")
    assert not sf.family_hits("a local checkout writes through", scope="body")
    assert not sf.family_hits("its last week on $3,000 of capital", scope="strict")
    assert not sf.family_hits("signed in as Ada · Trader plan", scope="strict")
    assert sf.family_hits("Starter is $29/month", scope="strict")
    assert sf.family_hits("the real upgrade path when they are near a wall", scope="body")
    assert sf.ALLOWED_FREE_WALL_SENTENCE_RE.match("Plans are changed in the Keel web app.")
    assert not sf.ALLOWED_FREE_WALL_SENTENCE_RE.match(
        "Paid plans include more backtests; plans are changed in the Keel web app."
    )
    assert not sf.ALLOWED_FREE_WALL_SENTENCE_RE.match(
        "Paid plans include more backtests: Starter 500 a week."
    )


# ─── The expiry-urgency family (connect-onboarding spec 01 §1.9) ─────────
#
# A first-week allowance is the one time-limited number an agent surface
# states, and it is stated as a fact: "154 of 200 first-week backtests left;
# they end Tue 13 Oct 15:02 UTC." The family below is how that sentence could
# drift into a push — and it is refused by every guard that reads agent copy:
# the runtime emit guard (`assert_neutral_wall_text`), the talking-point
# validator (`validate_talking_points`) and the G4 strict scan
# (`sf.family_hits`, which reads the SAME pattern from keel.errors).


def _urgency_seeds() -> tuple[str, ...]:
    """One seeded sentence per phrase the spec names, each otherwise identical
    to the approved sentence — so a refusal reacts to the phrase, not the
    facts. A function, not a constant: test_surface_routing.py loads this
    file by path, where `sf` is None."""
    approved = sf.FIRST_WEEK_SENTENCE
    return (
        approved[:-1] + "; use them before they expire.",
        approved[:-1] + "; spend it before it expires.",
        approved + " Don't lose them.",
        approved + " Use them before Tuesday.",
        approved + " You are running out.",
        "154 of 200 first-week backtests left; the allowance EXPIRES SOON.",
    )


#: Words the first-week copy never uses (spec 01 §1.9).
FIRST_WEEK_BANNED_RE = re.compile(r"\b(?:bonus|extra|trial|upgrade)\b|then 50", re.IGNORECASE)


def _every_guard_refuses(text: str) -> list[str]:
    """The guards that did NOT refuse ``text`` (empty means all refused)."""
    from keel.errors import assert_neutral_wall_text
    from keel.tools.outcomes._handoff import validate_talking_points

    missed: list[str] = []
    for free_plan in (True, False):
        try:
            assert_neutral_wall_text(text, free_plan=free_plan)
            missed.append(f"assert_neutral_wall_text(free_plan={free_plan})")
        except ValueError:
            pass
    try:
        validate_talking_points([text], require_do_nothing=False)
        missed.append("validate_talking_points")
    except ValueError:
        pass
    if not any(fam == "urgency" for fam, _ in sf.family_hits(text, scope="strict")):
        missed.append("G4 strict family_hits")
    return missed


def test_expiry_urgency_is_refused_by_every_guard():
    """SEEDED arm: each phrase of the family, in a sentence otherwise equal
    to the approved one, fails every guard that reads agent copy.

    SEEDS (run 2026-10-06, each reverted by reversing the edit):
    * drop the family from `keel.errors.FORBIDDEN_UPSELL_RE` — this reds
      naming `assert_neutral_wall_text` and `validate_talking_points` for
      all six seeds, and test_outcomes_plan_status'
      `test_an_urgent_first_week_sentence_fails_the_call` reds with it;
    * disable the strict-scope urgency family in `sf.family_hits` — this
      reds naming only `G4 strict family_hits`, for all six seeds.
    In both, `test_the_approved_first_week_sentence_passes_every_guard`
    (control) stays green."""
    seeds = _urgency_seeds()
    assert len(seeds) == 6  # one per phrase the spec names
    failures = {seed: missed for seed in seeds if (missed := _every_guard_refuses(seed))}
    assert not failures, f"urgency copy slipped past: {failures}"


def test_the_approved_first_week_sentence_passes_every_guard():
    """CONTROL arm: the approved sentence passes every guard the seeds fail,
    and carries none of the banned words."""
    from keel.errors import assert_neutral_wall_text
    from keel.tools.outcomes._handoff import validate_talking_points

    text = sf.FIRST_WEEK_SENTENCE
    for free_plan in (True, False):
        assert assert_neutral_wall_text(text, free_plan=free_plan) == text
    assert validate_talking_points([text], require_do_nothing=False) == [text]
    assert sf.family_hits(text, scope="strict") == []
    assert not FIRST_WEEK_BANNED_RE.search(text)


def test_g4_first_week_payloads_carry_the_sentence_and_nothing_else(g4_payloads):
    """Both first-week payloads, as a host receives them: the sentence is
    there (non-vacuity for every G4 arm that scans them), and no banned word
    or urgency phrase rides anywhere in the payload."""
    plan = g4_payloads["plan_status_first_week"]
    assert sf.FIRST_WEEK_SENTENCE in plan.structured["talking_points"], plan.structured
    run = g4_payloads["critical_run_first_week"]
    assert run.structured["quota_notice"] == (
        "4 of 50 backtests left this week; they reset Mon 28 Sep 00:00 UTC. "
        + sf.FIRST_WEEK_SENTENCE
    ), run.structured.get("quota_notice")
    for payload in (plan, run):
        for text in payload.strings():
            assert not FIRST_WEEK_BANNED_RE.search(text), (payload.name, text[:200])
            assert not sf.EXPIRY_URGENCY_RE.search(text), (payload.name, text[:200])


# ─── Paper runtime strings (paper-trading-v2 S06 §3.4.6, AC-06-21) ───────
#
# Every paper envelope, talking-point and label string ships from ONE module,
# `keel/tools/outcomes/_paper_strings.py`, as a row carrying a full form and a
# listed-safe form (or an explicit None where a profile never emits it). Three
# arms:
#
# * enumeration — every row the module declares is registered, declares both
#   forms, and the module carries no stray text outside a row;
# * listed scan — every non-None listed form passes FORBIDDEN_TEXT_RE WITHOUT
#   the paper-compound allowance (§3.4.4) and names no non-listed tool;
# * sourcing — no module under keel/ but the strings module carries any row's
#   text as a literal, and every declared paper render path imports the module
#   and references the rows it emits (and every importer is declared).
#
# PT2-31: the render paths join PAPER_RENDER_PATHS in the task that builds
# them — the envelope path at M8-quota-wall, the graduation path at
# M8-monitor-graduation, each tool's path in its own task — and each must be
# non-vacuous where it lands (seeding a literal into it reds the sourcing arm).
#
# Seeds (exact reversible edits, AC-06-21): drop LADDER_LINE's `listed=` ⇒
# test_paper_strings_enumeration_declares_every_form reds naming ladder_line;
# paste a row's text as a literal into any keel/ module (e.g. live_monitor.py)
# ⇒ test_paper_strings_are_sourced_from_the_module reds naming that file.

#: The nine §3.4.6 rows (PT2-28 added the paper-id refusal). The module may
#: carry more; it may never carry fewer.
PAPER_STRING_KEYS_SPEC = frozenset(
    {
        "claim_context_point",
        "permission_refusal_line",
        "graduation_live_call_line",
        "graduation_keep_paper_line",
        "graduation_carry_over_line",
        "ladder_line",
        "full_deploy_line_sentence",
        "monitor_paper_label",
        "monitor_paper_id_refusal",
    }
)

#: Paper render paths (module path relative to keel/) → the row keys it emits.
#: Each joins in the task that builds it (the PT2-31 note above).
PAPER_RENDER_PATHS: dict[str, frozenset[str]] = {
    # M8-paper-deploy-tool: the deploy result's run notes (§3.2.3). The same
    # module raises the envelope (§3.2.4): the quota wall (M8-quota-wall,
    # PT2-31) renders HEAD's neutral lines and no paper row. A paper text
    # retyped anywhere in it reds the package-wide literal scan.
    "tools/outcomes/paper_deploy.py": frozenset(
        {"deploy_fill_disclosure", "deploy_auto_pause_disclosure", "ladder_line"}
    ),
    # M8-auth-arms: the envelope render path's paper rows, the claim wall's
    # context point and the permission refusal (§3.2.4a), shared by both
    # paper tools.
    "tools/outcomes/_paper_walls.py": frozenset({"claim_context_point", "permission_refusal_line"}),
    # M8-monitor-venue: the monitor's run label (§3.5.2); M8-monitor-graduation
    # (PT2-31): the graduation path's rows (§3.5.4); M8-portfolio-paper: the
    # portfolio's paper section label (§3.5.5).
    "tools/outcomes/live_monitor.py": frozenset(
        {
            "monitor_paper_label",
            "portfolio_paper_section_label",
            "graduation_criteria_line",
            "graduation_live_call_line",
            "graduation_keep_paper_line",
            "graduation_carry_over_line",
        }
    ),
    # M8-regen (§3.9 row 7, PT2-28): the full-profile deploy line's paper
    # sentence, released only with the paper SDK.
    "tools/outcomes/_nudge.py": frozenset({"full_deploy_line_sentence"}),
    # QA-W3 / QA-W2: every paper_control outcome carries the paper label, and
    # its statuses render through the paper status words.
    "tools/outcomes/paper_control.py": frozenset({"monitor_paper_label"}),
}

KEEL_PKG_ROOT = Path(paper_strings.__file__).resolve().parents[2]
PAPER_STRINGS_REL = "tools/outcomes/_paper_strings.py"
_KEEL_TOOL_NAME_RE = re.compile(r"\bkeel_[a-z0-9_]+\b")
_TEMPLATE_FIELD_RE = re.compile(r"\{[^{}]*\}")
#: A literal fragment shorter than this is too generic to attribute to a row.
_MIN_PROBE_LEN = 12


def _module_rows(module) -> list[tuple[str, paper_strings.PaperString]]:
    return [
        (name, value)
        for name, value in vars(module).items()
        if isinstance(value, paper_strings.PaperString)
    ]


def _paper_string_probes(rows) -> dict[str, str]:
    """Literal fragment → row key, over every declared text form of every row.

    A template's fields split it; fragments shorter than `_MIN_PROBE_LEN` are
    dropped, so a probe is always specific to its row."""
    probes: dict[str, str] = {}
    for row in rows:
        for form in (row.full, row.listed):
            if not isinstance(form, str):
                continue
            for fragment in _TEMPLATE_FIELD_RE.split(form):
                fragment = fragment.strip()
                if len(fragment) >= _MIN_PROBE_LEN:
                    probes[fragment] = row.key
    return probes


def _string_constants(tree: ast.AST) -> list[tuple[int, str]]:
    """Every str constant in a module (docstrings and f-string parts too)."""
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _imports_paper_strings(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "_paper_strings":
                return True
            if any(alias.name == "_paper_strings" for alias in node.names):
                return True
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[-1] == "_paper_strings" for alias in node.names
        ):
            return True
    return False


def _referenced_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _sourcing_violations(
    pkg_root: Path, render_paths: dict[str, frozenset[str]], rows
) -> tuple[list[str], int]:
    """(violations, modules scanned) for the sourcing arm over `pkg_root`.

    1. No module but the strings module carries a row's text as a literal.
    2. Every declared render path exists, imports the strings module and
       references the constant of every row it declares.
    3. Every module importing the strings module is a declared render path.
    """
    probes = _paper_string_probes(rows)
    const_by_key = {row.key: name for name, row in _rows_by_constant(rows)}
    violations: list[str] = []
    importers: set[str] = set()
    scanned = 0
    for path in sorted(pkg_root.rglob("*.py")):
        rel = path.relative_to(pkg_root).as_posix()
        if rel == PAPER_STRINGS_REL:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanned += 1
        for lineno, value in _string_constants(tree):
            for probe, key in probes.items():
                if probe in value:
                    violations.append(
                        f"{rel}:{lineno}: retypes paper string {key!r} as a literal; "
                        f"import it from _paper_strings"
                    )
        if _imports_paper_strings(tree):
            importers.add(rel)
        if rel in render_paths:
            if not _imports_paper_strings(tree):
                violations.append(
                    f"{rel}: declared paper render path does not import _paper_strings"
                )
            missing = sorted(
                key for key in render_paths[rel] if const_by_key[key] not in _referenced_names(tree)
            )
            if missing:
                violations.append(f"{rel}: declared rows not referenced from the module: {missing}")
    for rel in sorted(
        set(render_paths) - {p.relative_to(pkg_root).as_posix() for p in pkg_root.rglob("*.py")}
    ):
        violations.append(f"{rel}: declared paper render path does not exist")
    for rel in sorted(importers - set(render_paths)):
        violations.append(f"{rel}: imports _paper_strings but is not a declared paper render path")
    return violations, scanned


def _rows_by_constant(rows) -> list[tuple[str, paper_strings.PaperString]]:
    """(constant name, row) for each row, read off the strings module."""
    by_id = {id(row): name for name, row in _module_rows(paper_strings)}
    return [(by_id[id(row)], row) for row in rows]


def test_paper_strings_enumeration_declares_every_form():
    """Enumeration arm (AC-06-21): every row is registered in
    ALL_PAPER_STRINGS, keys are unique, the nine §3.4.6 rows are present,
    every row declares BOTH forms (a text or an explicit None, never MISSING),
    and no public text sits in the module outside a row."""
    declared = _module_rows(paper_strings)
    registered = paper_strings.ALL_PAPER_STRINGS
    assert {id(row) for _, row in declared} == {id(row) for row in registered}, (
        "a PaperString row is declared but not in ALL_PAPER_STRINGS (or the reverse)"
    )
    keys = [row.key for row in registered]
    assert len(keys) == len(set(keys)), f"duplicate paper string keys: {keys}"
    assert len(keys) >= 9 and PAPER_STRING_KEYS_SPEC <= set(keys), (
        f"missing §3.4.6 rows: {sorted(PAPER_STRING_KEYS_SPEC - set(keys))}"
    )
    undeclared = [
        f"{row.key}.{side}"
        for row in registered
        for side in ("full", "listed")
        if not isinstance(getattr(row, side), (str, type(None)))
    ]
    assert not undeclared, (
        f"paper string forms neither a text nor an explicit None: {undeclared} "
        "(S06 §3.4.6: every row ships a listed variant or an explicit None)"
    )
    stray = sorted(
        name
        for name, value in vars(paper_strings).items()
        if not name.startswith("_") and isinstance(value, str)
    )
    assert not stray, f"paper text outside a PaperString row: {stray}"
    # The AC counts the rows whose form is never emitted on a profile; the
    # spec names at least one None per side (the refusal's full form; the
    # claim, permission, carry-over and deploy-line listed forms).
    assert any(row.full is None for row in registered)
    assert sum(row.listed is None for row in registered) >= 4


def test_paper_string_form_refuses_an_undeclared_form():
    """`form()` raises on MISSING rather than falling back to the other form,
    and selects by the profile at call time."""
    row = paper_strings.PaperString(key="probe", context="test", full="full text")
    assert row.form(listed=False) == "full text"
    with pytest.raises(LookupError, match="declares no listed form"):
        row.form(listed=True)
    # A real row with two distinct forms (not the ladder, which the AC's seed
    # moves; control arms stay green through every seed).
    live_call = paper_strings.GRADUATION_LIVE_CALL_LINE
    assert live_call.form(listed=True) != live_call.form(listed=False)


def test_paper_string_for_profile_reads_the_profile_at_call_time(monkeypatch):
    live_call = paper_strings.GRADUATION_LIVE_CALL_LINE
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert live_call.for_profile() == live_call.listed
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    assert live_call.for_profile() == live_call.full
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert paper_strings.MONITOR_PAPER_ID_REFUSAL.for_profile() == (
        paper_strings.MONITOR_PAPER_ID_REFUSAL.listed
    )


def test_paper_listed_variants_pass_the_string_rules():
    """Listed scan (AC-06-21, §3.4.4): every non-None listed form passes
    FORBIDDEN_TEXT_RE with NO paper-compound allowance, and every keel_* name
    it carries is a listed tool. Non-vacuity: the population is every row whose
    listed form is not an explicit None, at least the five §3.4.6 listed
    variants. A form left undeclared (MISSING) is the enumeration arm's verdict,
    so the AC's seed reds that arm and leaves this population count standing."""
    population = [row for row in paper_strings.ALL_PAPER_STRINGS if row.listed is not None]
    assert len(population) >= 5, f"only {len(population)} listed variants to scan"
    listed = [(row.key, row.listed) for row in population if isinstance(row.listed, str)]
    violations: list[str] = []
    for key, text in listed:
        violations += _text_violations("paper_strings", key, text)
        row = next(r for r in population if r.key == key)
        # A flag-on-only row (S10, PT2-90) is served beside the listed paper
        # tools, so it may name them; every other row may not.
        served = LISTED_PROFILE_TOOLS | (LISTED_PAPER_TOOLS if row.listed_flag_on_only else set())
        for name in _KEEL_TOOL_NAME_RE.findall(text):
            if name not in served:
                violations.append(f"paper_strings.{key}: names non-listed tool {name!r}")
    assert not violations, "\n".join(violations)
    # Non-vacuity of the flag-on allowance: it is used by at least one row.
    assert any(r.listed_flag_on_only for r in population)


def test_paper_listed_scan_control_arm():
    """CONTROL: the same scan reds on a full form that is not listed-safe
    (the ladder's 'going live'; the deploy line's keel_paper_deploy), so the
    green above is the listed variants' own."""
    ladder_full = paper_strings.LADDER_LINE.full
    assert _text_violations("paper_strings", "ladder_line", ladder_full)
    deploy_full = paper_strings.FULL_DEPLOY_LINE_SENTENCE.full
    assert any(n not in LISTED_PROFILE_TOOLS for n in _KEEL_TOOL_NAME_RE.findall(deploy_full))


def test_paper_strings_are_sourced_from_the_module():
    """Sourcing arm (AC-06-21): over every module under keel/, no row's text
    is retyped as a literal, every declared paper render path imports the
    strings module and references its rows, and every importer is declared.
    Non-vacuity: the scan reads the whole package and a probe per row."""
    rows = paper_strings.ALL_PAPER_STRINGS
    violations, scanned = _sourcing_violations(KEEL_PKG_ROOT, PAPER_RENDER_PATHS, rows)
    assert scanned > 100, f"the sourcing scan read only {scanned} modules"
    assert set(_paper_string_probes(rows).values()) == {row.key for row in rows}
    assert not violations, "\n".join(violations)


def test_paper_sourcing_arm_control_arms(tmp_path):
    """The sourcing checker can fail, on a synthetic package: a retyped
    literal, an undeclared importer, a declared path that does not import the
    module, a declared row it never references and a declared path that does
    not exist each red; the honest twin stays green."""
    rows = paper_strings.ALL_PAPER_STRINGS
    pkg = tmp_path / "keel"
    outcomes = pkg / "tools" / "outcomes"
    outcomes.mkdir(parents=True)
    (outcomes / "_paper_strings.py").write_text(
        f"LADDER = {paper_strings.LADDER_LINE.full!r}\n", encoding="utf-8"
    )
    honest = "from ._paper_strings import LADDER_LINE\n\ndef line():\n    return LADDER_LINE.for_profile()\n"
    (outcomes / "honest.py").write_text(honest, encoding="utf-8")
    declared = {"tools/outcomes/honest.py": frozenset({"ladder_line"})}
    violations, scanned = _sourcing_violations(pkg, declared, rows)
    assert scanned == 1 and violations == []

    cases = {
        "retyped literal": (
            {
                "tools/outcomes/retyped.py": f"X = {paper_strings.MONITOR_PAPER_ID_REFUSAL.listed!r}\n"
            },
            declared,
            "retyped.py:1: retypes paper string 'monitor_paper_id_refusal'",
        ),
        "retyped template fragment": (
            {
                "tools/outcomes/carry.py": "X = f'your paper run proved commit {n}; the strategy has moved ahead'\n"
            },
            declared,
            "carry.py:1: retypes paper string 'graduation_carry_over_line'",
        ),
        "undeclared importer": (
            {"tools/outcomes/sneaky.py": "from ._paper_strings import LADDER_LINE\n"},
            declared,
            "sneaky.py: imports _paper_strings but is not a declared",
        ),
        "declared path without the import": (
            {"tools/outcomes/plain.py": "X = 1\n"},
            {**declared, "tools/outcomes/plain.py": frozenset({"ladder_line"})},
            "plain.py: declared paper render path does not import",
        ),
        "declared row never referenced": (
            {},
            {"tools/outcomes/honest.py": frozenset({"ladder_line", "monitor_paper_label"})},
            "honest.py: declared rows not referenced from the module: ['monitor_paper_label']",
        ),
        "declared path absent": (
            {},
            {**declared, "tools/outcomes/gone.py": frozenset({"ladder_line"})},
            "gone.py: declared paper render path does not exist",
        ),
    }
    for label, (extra_files, render_paths, expected) in cases.items():
        written = []
        for rel, text in extra_files.items():
            path = pkg / rel
            path.write_text(text, encoding="utf-8")
            written.append(path)
        try:
            violations, _ = _sourcing_violations(pkg, render_paths, rows)
            assert any(expected in v for v in violations), (label, violations)
        finally:
            for path in written:
                path.unlink()
    # And the honest package is green again once the seeds are gone.
    assert _sourcing_violations(pkg, declared, rows)[0] == []
