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
        "keel_strategy_delete",
        "keel_accounts_list",
        "keel_audit_list_last",
        "keel_strategy_diff",
        "keel_strategy_restore",
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


def test_no_forbidden_verbs_in_titles_descriptions_or_instructions(listed_surface):
    tools_json, instructions, _ = listed_surface
    violations: list[str] = []
    for t in tools_json:
        violations += _text_violations(t["name"], "title", t["title"] or "")
        violations += _text_violations(t["name"], "description", t["description"])
        for pname, pschema in (t["inputSchema"].get("properties") or {}).items():
            violations += _text_violations(
                t["name"], f"param[{pname}].description", pschema.get("description", "")
            )
    violations += _text_violations("server", "instructions", instructions)
    assert not violations, "\n".join(violations)


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
