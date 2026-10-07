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

import asyncio
import re

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


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


# ─── Build the listed surface exactly as the hosted server would ────────


@pytest.fixture(scope="module")
def listed_surface():
    """(tools_json, instructions, prompt_names) from a real FastMCP
    server built under KEEL_SERVER_PROFILE=listed +
    KEEL_EXECUTION_MODE=hosted — the deployment configuration a
    directory registration actually runs."""
    import os

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
        return tools_json, server.instructions or "", {p.name for p in prompts}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _text_violations(owner: str, field: str, text: str) -> list[str]:
    hits = sorted({m.group(0).lower() for m in FORBIDDEN_TEXT_RE.finditer(text or "")})
    return [f"{owner}.{field}: forbidden term(s) {hits}"] if hits else []


# ─── The gate ───────────────────────────────────────────────────────────


def test_listed_surface_is_exactly_the_allow_list(listed_surface):
    tools_json, _, _ = listed_surface
    names = {t["name"] for t in tools_json}
    assert names == LISTED_PROFILE_TOOLS, (
        f"listed surface drifted — unexpected: {sorted(names - LISTED_PROFILE_TOOLS)}, "
        f"missing: {sorted(LISTED_PROFILE_TOOLS - names)}"
    )


def test_no_excluded_tool_present(listed_surface):
    tools_json, _, _ = listed_surface
    names = {t["name"] for t in tools_json}
    leaked = names & EXPLICITLY_EXCLUDED_TOOLS
    assert not leaked, f"excluded tools leaked into the listed profile: {sorted(leaked)}"
    # And the allow-list itself must never quietly admit one.
    assert not (LISTED_PROFILE_TOOLS & EXPLICITLY_EXCLUDED_TOOLS)


def test_no_forbidden_stems_in_tool_names(listed_surface):
    tools_json, _, _ = listed_surface
    violations = [
        f"{t['name']}: name contains forbidden stem {stem!r}"
        for t in tools_json
        for stem in FORBIDDEN_NAME_STEMS
        if stem in t["name"].lower()
    ]
    assert not violations, "\n".join(violations)


#: The no-trading boundary the listed instructions state (mcp-conversion 04
#: §5.3, founder-approved D-12). It NEGATES the forbidden families, so the
#: scan reads the instructions with this one clause removed; the clause is
#: asserted present exactly once, so the carve-out can never exempt nothing
#: and cannot quietly widen to another sentence.
BOUNDARY_CLAUSE = "It cannot place orders, move funds or connect wallets"


def test_no_forbidden_verbs_in_titles_descriptions_or_instructions(listed_surface):
    tools_json, instructions, _ = listed_surface
    assert instructions.count(BOUNDARY_CLAUSE) == 1, "the boundary clause moved"
    instructions = instructions.replace(BOUNDARY_CLAUSE, "")
    violations: list[str] = []
    for t in tools_json:
        violations += _text_violations(t["name"], "title", t["title"] or "")
        description = t["description"]
        if t["name"] == "keel_account_status":
            assert description.count(STATUS_BOUNDARY_CLAUSE) == 1, "the status boundary moved"
            description = description.replace(STATUS_BOUNDARY_CLAUSE, "")
        violations += _text_violations(t["name"], "description", description)
        for pname, pschema in (t["inputSchema"].get("properties") or {}).items():
            violations += _text_violations(
                t["name"], f"param[{pname}].description", pschema.get("description", "")
            )
    # The D-12 no-trading boundary NEGATES the money nouns ("cannot … move
    # funds or connect wallets"): exempt as that one exact sentence, never as
    # the field — any other use of those words in the instructions still reds.
    violations += _text_violations(
        "server", "instructions", instructions.replace(LISTED_BOUNDARY_SENTENCE, "")
    )
    assert not violations, "\n".join(violations)


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
            violations += _text_violations(t["name"], "outputSchema", text)
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
    known_tool_names = set(OUTCOMES)
    violations: list[str] = []

    def scan(owner: str, text: str) -> None:
        for ref in sorted(set(tool_name_re.findall(text or ""))):
            if ref in known_tool_names and ref not in LISTED_PROFILE_TOOLS:
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


@pytest.fixture(scope="module")
def listed_widget_surface():
    """(ui_resources, tool_wire_metas) from a real listed+hosted server.

    ``ui_resources`` maps each ``ui://`` URI to its served HTML text;
    ``tool_wire_metas`` maps tool name → the ``_meta`` dict published
    in tools/list.
    """
    import os

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
        resources = asyncio.run(server.list_resources())
        ui_resources = {
            str(r.uri): asyncio.run(r.read()).contents[0].content
            for r in resources
            if str(r.uri).startswith("ui://")
        }
        tools = asyncio.run(server.list_tools())
        tool_wire_metas = {t.name: (t.to_mcp_tool().meta or {}) for t in tools}
        return ui_resources, tool_wire_metas
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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
    violations: list[str] = []
    for uri, html in ui_resources.items():
        hits = sorted({m.group(0).lower() for m in FORBIDDEN_TEXT_RE.finditer(html or "")})
        if hits:
            violations.append(f"{uri}: forbidden term(s) {hits}")
    assert not violations, "\n".join(violations)


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
        violations += _text_violations(owner, "meta", text)
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
    assert len(descriptions) == len(LISTED_PROFILE_TOOLS)
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
    assert scanned >= 2 * len(LISTED_PROFILE_TOOLS)


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
    assert len(tools_json) == len(LISTED_PROFILE_TOOLS) == 29
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
    assert scanned >= 2 * len(LISTED_PROFILE_TOOLS)


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
    assert scanned >= 2 * len(LISTED_PROFILE_TOOLS)


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
    from keel.tools.outcomes._status_view import CAPABILITY_PHRASE

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
    text, envelope, _loaded = _served_status(monkeypatch, tmp_path, hosted=True)
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
    assert scanned >= 2 * len(LISTED_PROFILE_TOOLS) + 15
    # One phrase, two owners, byte for byte (agent-surface-cleanup fix-wave
    # resolution #10): the status view's line 3 OWNS it; the listed
    # description quotes it. It states the D-12 no-trading boundary in the
    # same words the instructions use (04 §5.3, the exact sentence below) —
    # the fact the refusal needs.
    (status,) = [t for t in tools_json if t["name"] == "keel_account_status"]
    assert f"This connector: {CAPABILITY_PHRASE}." in text
    assert f"what this connector can do: {CAPABILITY_PHRASE}." in status["description"]
    assert LISTED_BOUNDARY_LINE in instructions, (
        "the listed instructions must state the no-trading boundary verbatim (04 §5.3)"
    )
    assert STATUS_BOUNDARY_CLAUSE in CAPABILITY_PHRASE
    assert "managed in the Keel web app" in CAPABILITY_PHRASE
    # The status clause and the instructions' clause are one boundary: they
    # differ only in the sentence-initial capital.
    assert BOUNDARY_CLAUSE == "I" + STATUS_BOUNDARY_CLAUSE[1:]


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
    assert assemble.instruction_section_ids("listed") == (
        "head",
        "sequence",
        "quota",
        "surface-listed",
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
            violations += _text_violations(t["name"], "nested-param.description", text)
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


def _served_status(monkeypatch, tmp_path, *, hosted: bool):
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
    return sf.runtime_payloads()


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
    } <= set(g4_payloads)


#: Payloads about the caller's PLAN: no link at all (04 §4.1, §4.3 — the walls
#: lose every URL, keel_plan_usage loses manage_url/hero_url).
PLAN_PAYLOADS = (
    "free_wall",
    "paid_wall",
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
