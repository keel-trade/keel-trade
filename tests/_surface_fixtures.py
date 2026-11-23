"""Shared helpers for the agent-surface layer guards (05 §6, G2–G7; closes Q-2081).

Not a conftest: each guard imports what it leans on by name, so a reader can
follow every helper a verdict depends on. Three kinds of thing live here:

* **The served surface** (:func:`listed_surface`) — the LISTED profile built
  in-process exactly as the hosted directory registration runs it
  (``KEEL_SERVER_PROFILE=listed``, ``KEEL_EXECUTION_MODE=hosted``,
  ``create_server()``): instructions, ``tools/list``, prompt descriptions and
  rendered bodies, every ``keel_help`` topic body, and every
  ``keel://knowledge`` / ``keel://dsl/reference`` read that resolves.
* **Runtime payload fixtures** (:func:`runtime_payloads`) — tool results the
  REAL SDK code paths produce through the real MCP adapter
  (``server.call_tool``), with only the HTTP layer to keel-api stubbed
  (``respx``). Walls on the free and a paid plan, ``keel_plan_usage`` on both,
  a critical-tier ``keel_backtest_run``, ``keel_account_status``, a sparse backtest
  result, the strategy log, and the 401/404 error envelopes.
* **The rule tables** — the agent-conduct register (G3), the commerce and
  live-money families (G4), pointer and tool-name extraction (G5, G6), and
  the explicit, sentence-exact exemption tables with a reason per entry.

The TARGET contracts are 04 §4 (runtime copy) and 05 §2–§3 (layers): these
helpers read whatever the tree serves, so the guards go red on today's
defects and green once the corpus, runtime and skills lanes land — never by
editing a list here.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[4]
REFERENCE_ROOT = REPO_ROOT / "libs" / "pipeline_engine" / "reference"
LAYERS_MANIFEST = REFERENCE_ROOT / "LAYERS.yaml"
SDK_ROOT = Path(__file__).resolve().parents[1]
VENDORED_DATA = SDK_ROOT / "keel" / "data"
HOST_ADAPTER_JS = SDK_ROOT / "keel" / "widgets" / "assets" / "host-adapter.js"
CHAT_EXECUTOR = REPO_ROOT / "services" / "chat-api" / "src" / "agent" / "executor.py"
CHAT_TOOLS = REPO_ROOT / "services" / "chat-api" / "src" / "agent" / "tools.py"


def in_monorepo() -> bool:
    """The guards that read the corpus source need the monorepo (the public
    SDK mirror ships tests without ``libs/``)."""
    return (REFERENCE_ROOT / "system").is_dir()


# ─── The pinned served topic set (05 §3, lane-C brief "Served (MCP) topics") ──
#
# Normalized with keel_help's own fold (lowercase, `-`/space → `_`), so
# `platform-operations.md` and `platform_operations` are one topic. Edit
# deliberately, with review: this list IS the Reference layer's public face.

SERVED_KNOWLEDGE_TOPICS: frozenset[str] = frozenset(
    {
        "backtest_costs",
        "capability_boundaries",
        "composition_mechanics",
        "dsl_syntax",
        "mistakes",
        "operating_core",
        "pipeline_system",
        "platform_operations",
        "reasoning_principles",
        "strategy_paths",
        "strategy_patterns",
        "tool_usage",
        "trading_domain",
        "universe_selection",
    }
)
SERVED_REFERENCE_TOPICS: frozenset[str] = frozenset(
    {
        "best_practices",
        "composition",
        "normalization",
        "phases",
        "slots",
        "types",
        "screener",
        "view_language",
    }
)
SERVED_PATTERN_TOPICS: frozenset[str] = frozenset(
    {
        "combining_signals",
        "common_mistakes",
        "data_loading",
        "entry_exit_patterns",
        "forecast_pipeline",
        "improvement_ladders",
        "position_sizing",
        "regime_conditioning",
        "risk_management",
        "rank_select_patterns",
        "screen_recipes",
        "session_and_structure_patterns",
    }
)
SERVED_DOC_TOPICS: frozenset[str] = (
    SERVED_KNOWLEDGE_TOPICS | SERVED_REFERENCE_TOPICS | SERVED_PATTERN_TOPICS
)
#: keel_help's own namespaces (listings, not documents).
HELP_NAMESPACES: frozenset[str] = frozenset({"skills", "rules"})
#: D-344 (1), CONFORM-MCP F2: the topics a listed caller is served only while Research is (07 §4.10, §5.3), and the
#: ``step:<Name>`` namespace beside them; a research-on surface pins them, a research-off one must not serve them.
RESEARCH_DOC_TOPICS: frozenset[str] = frozenset({"research", "research_steps"})
RESEARCH_TOPIC_PREFIX = "step:"
#: In-app only after the change (05 §3.1) — named so a failure reads plainly.
NOT_SERVED_TOPICS: frozenset[str] = frozenset(
    {"collaboration", "strategy_phases", "editor_ui", "component_versioning", "costs_and_fees"}
)


def normalize_topic(topic: str) -> str:
    """keel_help's fold (help.py `_normalize_topic`), restated so the guard
    does not borrow the code under test's normalizer."""
    return topic.strip().lower().replace("-", "_").replace(" ", "_")


# ─── Sentences ──────────────────────────────────────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'`(*_])")
_MD_LEAD_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)+")


def sentences(text: str) -> list[str]:
    """Markdown text → sentences, whitespace-normalized.

    Paragraphs are re-joined (a hard-wrapped sentence is one sentence),
    table rows split into cells, list/heading markers dropped, fenced code
    kept line by line. HTML comments (the pattern files' keyword headers)
    are not prose and are removed.
    """
    text = re.sub(r"<!--.*?-->", " ", text or "", flags=re.DOTALL)
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            joined = " ".join(para)
            out.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(joined) if s.strip())
            para.clear()

    in_fence = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            if line.strip():
                out.append(line.strip())
            continue
        if not line.strip():
            flush()
            continue
        if line.lstrip().startswith("|"):
            flush()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            out.extend(c for c in cells if c)
            continue
        if _MD_LEAD_RE.match(line):
            flush()
            line = _MD_LEAD_RE.sub("", line)
        para.append(line.strip())
    flush()
    return [re.sub(r"\s+", " ", s) for s in out if s]


# ─── G3: the agent-conduct register ─────────────────────────────────────
#
# spec 01's regexes (assemble.DIRECTIVE_PATTERNS / IMPERATIVE_RE /
# REGISTER_WORD_RE) were tuned for tool DESCRIPTIONS: reference prose says
# "must" about DSL rules and "never" about look-ahead, legitimately. This set
# reads something narrower — text that addresses the MODEL AS AN ASSISTANT
# about its conversation (what to say, ask, offer or suggest to the user,
# the user's mind, persona, its own turn). Calibrated 2026-09-28 against the
# 305 labelled sections of `layer-classification/sections.md` (the evidence
# folder): 40 / 40 OPINION-IN-APP sections hit; 9 / 228 REFERENCE sections
# hit, every one on a conduct TAIL inside a section the classifier itself
# marked "rewrite→base" (or chat-only) — see PENDING_REPHRASE below.

AGENT_CONDUCT_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "reply-script",
        r"\b(?:in|to|into) (?:your|the) (?:reply|response|answer)\b"
        r"|\byour (?:reply|response|answer)s?\b"
        r"|\breply (?:in|with)\b|\bphrase to use\b|\buse the phrase\b"
        r"|\bsay (?:so|both|that|it|what)\b|\blead with\b|\bquote (?:its|the|their|those) numbers\b",
    ),
    (
        "address-user",
        r"\b(?:tell|ask|remind|warn|inform|let) (?:the |a )?users?\b|\btell them\b"
        r"|\bask (?:one|a) (?:clarifying )?question\b|\bask for guidance\b"
        r"|\bsurface (?:it|this|that|the option) to the user\b|\bthe user's time\b"
        r"|\btalk (?:someone|them|the user) out of\b",
    ),
    (
        "offer",
        r"\boffer (?:to|the|an|a|one|those)\b|\bsuggest (?:1-2|one|that|improvements)\b"
        r"|\bwhen suggesting\b",
    ),
    ("posture-word", r"\bproactively\b|\bcelebrat\w*|\bnarrat\w*|\bencourag\w*|\bupsells?\b"),
    (
        "user-mind",
        r"\bthe user (?:actually wants|is (?:actually )?asking)\b"
        r"|\bwhat does the user\b|\bthe user's (?:question|intent|message)\b",
    ),
    (
        "persona",
        r"\byou are (?:embedded|a|an|the)\b|\bpersona\b|\bexplain (?:the why|why|key choices)\b"
        r"|\bground (?:your )?answers\b|\bor you, when relevant\b",
    ),
    (
        "assistant-self",
        r"\byour (?:toolset|turn|next turn)\b|\byou will not get\b|\bthinking block\b"
        r"|\bcall `think`|\blet you know\b|\bnever promise\b|\byou cannot (?:see|debug)\b"
        r"|\b(?:in )?(?:the same|this) turn\b|\bthe editor\b|\byour `[a-z_]+` call\b"
        r"|\bbefore (?:asserting|claiming|saying)\b",
    ),
)

_CONDUCT_RES = tuple((name, re.compile(pat, re.IGNORECASE)) for name, pat in AGENT_CONDUCT_PATTERNS)


def conduct_hits(text: str) -> list[tuple[str, str]]:
    """``[(rule, sentence)]`` for every sentence of `text` a conduct rule matches."""
    hits: list[tuple[str, str]] = []
    for sentence in sentences(text):
        for name, rx in _CONDUCT_RES:
            if rx.search(sentence):
                hits.append((name, sentence))
                break
    return hits


# ─── G4: the commerce and live-money families (04 §4, 05 §6 G4) ─────────
#
# Two scopes, one family. STRICT reads Keel's own agent-surface copy —
# runtime payloads, JSON keys, tools/list, instructions, prompt descriptions:
# there the bare word "upgrade" and any "$<digit>" are commerce. BODY reads
# served Reference and skill bodies, where "upgrade" is DSL vocabulary
# ("Upgrade to Vol-Targeted Sizing", a component-version upgrade) and "$10M"
# is a liquidity threshold — so the body scope names the plan-upgrade and
# price forms instead of the bare tokens. Everything else is identical.

_COMMERCE_SHARED = (
    r"\bhigher plans?\b|\bplan tiers?\b|\bbuilder fees?\b|\bsee plans\b|tab=billing|/pricing\b"
    r"|\bstripe\b|\bbilling\b|\bpricing\b"
    # A price is a dollar figure with a billing period; a bare "$3,000" is
    # backtest capital (a simulation fact) and "$10M/24h" a liquidity floor.
    r"|\$\s?\d[\d,.]*\s*(?:/\s*|per |a )(?:mo|month|year|yr)\b|\$\s?\d[\d,.]*\s*(?:monthly|annually)\b"
)
COMMERCE_STRICT_RE = re.compile(
    _COMMERCE_SHARED + r"|\bupgrad(?:e|es|ed|ing)\b|\bupgrade_\w+|\bcheckout\b",
    re.IGNORECASE,
)
COMMERCE_BODY_RE = re.compile(
    _COMMERCE_SHARED + r"|\bupgrade path\b|\bupgrad(?:e|es|ed|ing) (?:to )?(?:a |the |your )?"
    r"(?:paid|higher|pro|trader|starter|plan)\b|\bplan upgrades?\b"
    # "checkout" in a body is usually a local workspace checkout; the
    # commerce sense names the payment flow.
    r"|\bcheckout (?:page|session|flow)\b|checkout\.stripe",
    re.IGNORECASE,
)


#: Every catalog plan display name (plans.yaml, D-14 §6.1), case-sensitive
#: so "a trader" and "pro-rata" stay ordinary words. Static copy (tools/list,
#: instructions, prompts, served bodies) names no plan at all; a runtime plan
#: payload names one only inside the renderer's own-plan or more clause, and
#: only for a served higher plan (`plan_name_violations`).
def _plan_names_re() -> re.Pattern[str]:
    from . import _plan_catalog as cat

    names = sorted(cat.all_plan_names(), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")


PLAN_NAME_RE = _plan_names_re()


def _static_plan_name_re() -> re.Pattern[str]:
    """A paid plan named AS a plan in static copy: in a comparison or a move
    ("Trader or Pro", "to Trader"), or with a plan word ("Pro includes",
    "the Trader plan"). "Free" and a bare "Trader" are English words in
    static copy ("a free-text note", "Trader phrase → Keel mapping")."""
    from . import _plan_catalog as cat

    n = "(?:" + "|".join(re.escape(x) for x in cat.all_plan_names() if x != "Free") + ")"
    return re.compile(
        rf"\b{n} (?:or|and) {n}\b|\b(?:to|into|on) (?:the )?{n}\b"
        rf"|\b{n} (?:plans?|includes?|runs?)\b|\bthe {n} plan\b"
    )


PAID_PLAN_NAME_RE = _static_plan_name_re()
LIVE_MONEY_RE = re.compile(
    r"\breal capital\b|\bfunded wallets?\b|\bconnect(?:ing)? (?:a|your) wallet\b"
    r"|\bgo(?:ing)? live\b|\bdeploy(?:ing)? (?:it )?live\b|\bcanary\b|\brun (?:it )?forward\b",
    re.IGNORECASE,
)


#: Expiry urgency (connect-onboarding spec 01 §1.9) — the SAME pattern the
#: runtime guard `keel.errors.assert_neutral_wall_text` refuses, read from it
#: rather than copied. Strict scope only: in a served Reference body "the
#: window running out" is DSL vocabulary (a component's breakout window), not
#: a push at the user.
def _expiry_urgency_re() -> re.Pattern[str]:
    from keel.errors import EXPIRY_URGENCY_PATTERN

    return re.compile(EXPIRY_URGENCY_PATTERN, re.IGNORECASE)


EXPIRY_URGENCY_RE = _expiry_urgency_re()

#: Any Keel web URL inside a WALL payload (04 §4.1: "every URL" removed).
KEEL_URL_RE = re.compile(r"https?://[^\s\"')]*usekeel\.io[^\s\"')]*", re.IGNORECASE)


def plan_name_violations(
    text: str, *, own: str | None, more: tuple[str, ...] | list[str]
) -> list[str]:
    """D-14 §7: plan names in a runtime plan payload ``text``, outside the
    generated clauses, or naming a plan the server did not serve.

    Allowed, and removed before the scan: the caller's own plan in the
    renderer's own-plan positions (``on <Own>``, ``Plan: <Own>.``,
    ``<Own> doesn't include``, ``<Own> screens cover``, ``<Own> runs the Check``), and the more clause (or a
    feature wall's "are included on <names>.") itself — but only when
    its names are EXACTLY the served ``more``, in order. A clause naming any
    other set, a clause on a payload whose server named none (the top plan),
    or a plan name anywhere else is a violation.
    """
    from keel.errors import join_plan_names

    violations: list[str] = []
    text = text or ""
    names_re = r"(?P<names>(?:[A-Z][\w-]*)(?:(?:, | and )[A-Z][\w-]*)*)"
    # The more clause by kind and unit ("include more", "run more", "run faster", "include it", "screen every
    # coin"), and the v3 feature wall's inclusion head ("Signal studies are included on Quant and Pro.").
    clause = re.compile(
        rf"(?:{names_re} (?:(?:includes?|runs?) (?:more|faster|it)|screens? every coin)\b)"
        rf"|(?:\b(?:is|are) included on {names_re.replace('names', 'incl')}\.)"
    )
    for m in clause.finditer(text):
        names = m.group("names") or m.group("incl")
        if not PLAN_NAME_RE.search(names):
            continue
        if not more or names != join_plan_names(list(more)):
            violations.append(f"more clause {m.group(0)!r} (served {list(more)!r})")
    text = clause.sub(" ", text)
    # A JSON text block carries the fields themselves: `"plan_name": "Free"`
    # and `"more_plans": ["Trader", "Pro"]` — exact served values, not copy.
    for name in (own, *more):
        if name:
            text = text.replace(f'"{name}"', " ")
    if own:
        for pattern in (
            rf"\bon {re.escape(own)}\b",
            rf"\bPlan: {re.escape(own)}\.",
            rf"\b{re.escape(own)} doesn't include\b",
            # Plans v3 (spec 02 §D): the coin cap's own-plan position, and a study type's included alternative.
            rf"\b{re.escape(own)} screens cover\b",
            rf"\b{re.escape(own)} runs the Check\b",
        ):
            text = re.sub(pattern, " ", text)
    violations += [f"plan name {m.group(0)!r}" for m in PLAN_NAME_RE.finditer(text)]
    return violations


def family_hits(text: str, *, scope: str) -> list[tuple[str, str]]:
    """``[(family, match)]`` for the commerce + live-money families, and
    (strict scope) expiry urgency.

    ``scope`` is ``"strict"`` (Keel's own copy and payloads) or ``"body"``
    (served Reference and skill bodies).
    """
    if scope not in ("strict", "body"):
        raise ValueError(f"unknown scope {scope!r}")
    commerce = COMMERCE_STRICT_RE if scope == "strict" else COMMERCE_BODY_RE
    out = [("commerce", m.group(0)) for m in commerce.finditer(text or "")]
    out += [("plan-name", m.group(0)) for m in PAID_PLAN_NAME_RE.finditer(text or "")]
    out += [("live-money", m.group(0)) for m in LIVE_MONEY_RE.finditer(text or "")]
    if scope == "strict":
        out += [("urgency", m.group(0)) for m in EXPIRY_URGENCY_RE.finditer(text or "")]
    return out


def family_sentence_hits(text: str, *, scope: str) -> list[tuple[str, str, str]]:
    """``[(family, match, sentence)]`` — family hits located by sentence."""
    out: list[tuple[str, str, str]] = []
    for sentence in sentences(text):
        for fam, match in family_hits(sentence, scope=scope):
            out.append((fam, match, sentence))
    return out


# ─── G5: pointers ───────────────────────────────────────────────────────

_TOPIC_ARG_RE = re.compile(r"""\btopic\s*=\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z_][\w:\-]*))""")
_URI_RE = re.compile(r"keel://(knowledge|dsl/reference)/([A-Za-z0-9_\-{}<>]+)")
_SKILL_PTR_RE = re.compile(r"\bskill:(<[a-z_]+>|[a-z][a-z0-9\-]*)")
_CATALOGUE_HEAD_RE = re.compile(r"keel_help\b[^\n]*\btopic=<")


@dataclass(frozen=True)
class Pointer:
    kind: str  # "topic" | "uri" | "catalogue" | "skill"
    target: str
    context: str


def _is_placeholder(target: str) -> bool:
    return "<" in target or "{" in target


def _catalogue_targets(line: str) -> list[str]:
    """Topic names a catalogue line points at ("what next → strategy_phases").

    A catalogue line is one introduced by ``keel_help … topic=<name>`` or a
    list item under such a line. Clauses split on ``;``; a clause with an
    arrow points at what follows it, and a clause without one continues the
    previous arrow's list. Each item's first token is the candidate.
    """
    out: list[str] = []
    seen_arrow = False
    for clause in line.split(";"):
        if "→" in clause:
            seen_arrow = True
            rhs = clause.split("→")[-1]
        elif seen_arrow:
            rhs = clause
        else:
            continue
        for item in rhs.split(","):
            m = re.match(r"\s*`?([a-z][a-z0-9_\-]*(?::[A-Za-z<>_\-]+)?)", item)
            if m:
                out.append(m.group(1))
    return out


def extract_pointers(text: str) -> list[Pointer]:
    """Every model-directed pointer in `text` (05 R-L3)."""
    pointers: list[Pointer] = []
    for m in _TOPIC_ARG_RE.finditer(text or ""):
        target = next(g for g in m.groups() if g)
        pointers.append(Pointer("topic", target, text[max(0, m.start() - 60) : m.end() + 20]))
    for m in _URI_RE.finditer(text or ""):
        pointers.append(Pointer("uri", f"{m.group(1)}:{m.group(2)}", m.group(0)))
    for m in _SKILL_PTR_RE.finditer(text or ""):
        pointers.append(Pointer("skill", m.group(1), text[max(0, m.start() - 60) : m.end() + 20]))
    in_catalogue = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if _CATALOGUE_HEAD_RE.search(line):
            in_catalogue = True
            targets = _catalogue_targets(line.split(":", 1)[-1] if ":" in line else line)
        elif in_catalogue and stripped.startswith("- "):
            targets = _catalogue_targets(stripped[2:])
        else:
            if in_catalogue and stripped:
                in_catalogue = False
            continue
        for target in targets:
            pointers.append(Pointer("catalogue", target, stripped[:160]))
    return pointers


def pointer_violation(
    p: Pointer, *, corpus_stems: frozenset[str], research: bool = False
) -> str | None:
    """Why `p` is not allowed, or None.

    * ``skill:`` pointers are the caller's business (the guard decides where
      they may appear); here every concrete skill name is merely well-formed.
    * a topic / URI pointer must resolve to a SERVED topic (or a keel_help
      namespace, or a ``rule:`` code) — never to an in-app-only file.
    * a catalogue item is checked only when it names something the corpus
      knows (any layer) or is snake_case: English label words are not topics.
    """
    target = p.target
    if p.kind == "skill" or _is_placeholder(target):
        return None
    if p.kind == "uri":
        space, name = target.split(":", 1)
        topic = normalize_topic(name)
        allowed = SERVED_KNOWLEDGE_TOPICS if space == "knowledge" else SERVED_REFERENCE_TOPICS
        return None if topic in allowed else f"keel://{space}/{name} is not a served topic"
    low = target.lower()
    if low.startswith("rule:") or low.startswith("skill:"):
        return None
    if research and (
        low.startswith(RESEARCH_TOPIC_PREFIX) or normalize_topic(target) in RESEARCH_DOC_TOPICS
    ):
        return None  # served while Research is (``research`` surfaces only)
    topic = normalize_topic(target)
    if topic in SERVED_DOC_TOPICS or topic in HELP_NAMESPACES:
        return None
    if p.kind == "catalogue" and topic not in corpus_stems and "_" not in topic:
        return None  # an English label word, not a topic
    return f"{target!r} is not a served topic"


@cache
def corpus_stems() -> frozenset[str]:
    """Every corpus file stem, any layer, normalized (both trees)."""
    stems = {normalize_topic(p.stem) for p in REFERENCE_ROOT.rglob("*.md")}
    stems |= {normalize_topic(p.stem) for p in VENDORED_DATA.rglob("*.md")}
    return frozenset(stems | NOT_SERVED_TOPICS | SERVED_DOC_TOPICS)


# ─── G6: tool names ─────────────────────────────────────────────────────

KEEL_TOOL_RE = re.compile(r"\bkeel_[a-z][a-z0-9_]*\b")

#: `keel_`-prefixed identifiers that are not tool names. Exact tokens only.
KEEL_NON_TOOL_IDENTIFIERS: frozenset[str] = frozenset()


def _dict_literal_keys(path: Path, names: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & names:
                keys |= {
                    k.value
                    for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
    return keys


def _name_values(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "name"
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    out.add(v.value)
    return out


PE_MCP_SCHEMAS = REPO_ROOT / "libs" / "pipeline_engine" / "mcp" / "schemas.py"
PE_MCP_TOOLS = REPO_ROOT / "libs" / "pipeline_engine" / "mcp" / "tools.py"


@cache
def chat_tool_names() -> frozenset[str]:
    """Keel-chat tool names, read FROM CODE (never a literal list).

    Sources, unioned, all PARSED (neither chat-api nor ``pipeline_engine.mcp``
    is importable here — the SDK vendors a pipeline_engine subset that
    shadows libs/): chat-api's dispatch tables (``TOOL_DISPATCH``,
    ``ASYNC_TOOL_DISPATCH`` in executor.py), chat-api's own tool definitions
    (tools.py), pipeline_engine's MCP tool schemas and definitions, and the
    corpus builder's ``CHAT_VOCABULARY`` (the chat spellings of the head's
    tool names). Names without an underscore (``think``) are dropped: an
    English word is not evidence of a tool name.
    """
    from pipeline_engine.reference.system.assemble import CHAT_VOCABULARY

    names: set[str] = set()
    names |= _dict_literal_keys(CHAT_EXECUTOR, {"TOOL_DISPATCH", "ASYNC_TOOL_DISPATCH"})
    names |= _name_values(CHAT_TOOLS)
    names |= _name_values(PE_MCP_SCHEMAS)
    names |= _name_values(PE_MCP_TOOLS)
    names |= set(CHAT_VOCABULARY.values())
    return frozenset(n for n in names if "_" in n and not n.startswith("keel_"))


def tool_name_hits(text: str, listed_tools: frozenset[str]) -> list[str]:
    """Tool-like names in `text` that are not on the listed tools/list."""
    hits = [
        t
        for t in KEEL_TOOL_RE.findall(text or "")
        if t not in listed_tools and t not in KEEL_NON_TOOL_IDENTIFIERS
    ]
    for name in sorted(chat_tool_names()):
        if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text or ""):
            hits.append(name)
    return hits


# ─── The served surface ─────────────────────────────────────────────────

_PROFILE_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_LISTED_CLIENT",
    "KEEL_LISTED_SCREENS_HOSTS",
)


@contextmanager
def listed_env() -> Iterator[None]:
    """The directory registration's environment, restored on exit.

    With the listed screens flag ON for every host (screeners L13): the layer
    guards read the widest listed surface, so the screen tools, prompt and
    topics are held to the same rules as the rest. The flag-off surface is
    the base's, pinned by the freeze fixtures (test_listed_outputs_head,
    test_listed_screens)."""
    saved = {k: os.environ.get(k) for k in _PROFILE_ENV}
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    os.environ["KEEL_EXECUTION_MODE"] = "hosted"
    os.environ["KEEL_LISTED_SCREENS_HOSTS"] = "all"
    os.environ.pop("KEEL_TOOLSETS", None)
    os.environ.pop("KEEL_LISTED_CLIENT", None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def _quiet_fastmcp() -> Iterator[None]:
    """A refused resource read logs a full traceback; the probe expects refusals."""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


@dataclass
class ServedSurface:
    instructions: str
    tools: list[dict[str, Any]]
    prompts: dict[str, dict[str, str]]
    help_listing: dict[str, Any]
    help_docs: dict[str, str]  # normalized topic → served body
    help_info: dict[str, str]  # normalized topic → that doc result's `info` line
    help_skills: dict[str, Any]  # keel_help(topic="skills") — the skills catalogue
    resources: dict[str, str]  # uri → served text (reads that resolved)
    probed: tuple[str, ...] = field(default=())
    research: bool = False  # built as a caller with Research served

    def listed_tool_names(self) -> frozenset[str]:
        return frozenset(t["name"] for t in self.tools)

    def help_topics(self) -> frozenset[str]:
        return frozenset(normalize_topic(t) for t in self.help_listing.get("topics", []))

    def resource_topics(self, space: str) -> frozenset[str]:
        prefix = f"keel://{space}/"
        return frozenset(
            normalize_topic(u[len(prefix) :]) for u in self.resources if u.startswith(prefix)
        )

    def served_bodies(self) -> dict[str, str]:
        """owner → text for every Reference body a client can fetch."""
        out = {f"keel_help:{t}": body for t, body in self.help_docs.items()}
        out.update({f"resource:{u}": body for u, body in self.resources.items()})
        return out


def _probe_stems() -> set[str]:
    stems = set(SERVED_DOC_TOPICS | NOT_SERVED_TOPICS)
    if REFERENCE_ROOT.is_dir():
        stems |= {p.stem for p in REFERENCE_ROOT.rglob("*.md")}
    stems |= {p.stem for p in VENDORED_DATA.rglob("*.md")}
    variants: set[str] = set()
    for s in stems:
        variants |= {s, s.replace("-", "_"), s.replace("_", "-")}
    return variants


def _schema_strings(node: Any) -> list[str]:
    if isinstance(node, dict):
        out = [node["description"]] if isinstance(node.get("description"), str) else []
        for value in node.values():
            out += _schema_strings(value)
        return out
    if isinstance(node, list):
        return [s for item in node for s in _schema_strings(item)]
    return []


def tool_copy(tool: dict[str, Any]) -> list[tuple[str, str]]:
    """``[(owner, text)]`` for a tool's description and every schema string."""
    out = [(f"{tool['name']}.description", tool["description"] or "")]
    if tool.get("title"):
        out.append((f"{tool['name']}.title", tool["title"]))
    out += [(f"{tool['name']}.inputSchema", s) for s in _schema_strings(tool["inputSchema"])]
    out += [(f"{tool['name']}.outputSchema", s) for s in _schema_strings(tool["outputSchema"])]
    return out


def _read_or_refused(server: Any, uri: str) -> Any | None:
    """The read's result, or None when the server REFUSES the resource.

    Only fastmcp's ResourceError (the template handler raised — unknown
    section / topic) means "not served"; anything else is a harness fault and
    propagates rather than reading as a refusal.
    """
    from fastmcp.exceptions import ResourceError

    try:
        return asyncio.run(server.read_resource(uri))
    except ResourceError:
        return None


@cache
def listed_surface(research: bool = False) -> ServedSurface:
    """Build the listed server once per process and read everything it serves.

    ``research`` builds it as a caller with Research served reads it (doc 07
    §7: research is in the listed scans)."""
    from contextlib import nullcontext

    from keel.mcp.server import create_server
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._toolsets import research_served_context

    with listed_env(), research_served_context(True) if research else nullcontext():
        _bootstrap()
        server = create_server()
        tools = asyncio.run(server.list_tools())
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
        prompts: dict[str, dict[str, str]] = {}
        for p in asyncio.run(server.list_prompts()):
            rendered = asyncio.run(server.render_prompt(p.name, {}))
            body = "\n".join(m.content.text for m in rendered.messages)
            prompts[p.name] = {"description": p.description or "", "body": body}

        help_tool = OUTCOMES["keel_help"]
        listing = help_tool.handler({}, ToolContext()).extra
        skills_listing = help_tool.handler({"topic": "skills"}, ToolContext()).extra
        docs: dict[str, str] = {}
        info: dict[str, str] = {}
        for topic in listing.get("topics", []):
            if normalize_topic(topic) in HELP_NAMESPACES:
                continue
            extra = help_tool.handler({"topic": topic}, ToolContext()).extra
            docs[normalize_topic(topic)] = extra.get("body") or ""
            info[normalize_topic(topic)] = extra.get("info") or ""

        resources: dict[str, str] = {}
        probed: list[str] = []
        with _quiet_fastmcp():
            for stem in sorted(_probe_stems()):
                for space in ("knowledge", "dsl/reference"):
                    uri = f"keel://{space}/{stem}"
                    probed.append(uri)
                    res = _read_or_refused(server, uri)
                    if res is None:
                        continue
                    text = "\n".join(str(c.content) for c in res.contents)
                    if space == "dsl/reference":
                        text = json.loads(text).get("content", text)
                    resources[uri] = text
        return ServedSurface(
            instructions=server.instructions or "",
            tools=tools_json,
            prompts=prompts,
            help_listing=listing,
            help_docs=docs,
            help_info=info,
            help_skills=skills_listing,
            resources=resources,
            probed=tuple(probed),
            research=research,
        )


# ─── Runtime payload fixtures (real code paths, HTTP stubbed) ────────────

API = "https://api.surface-guards.test"

#: One reset every fixture shares, so a copy rule never keys on a date.
RESET_EPOCH = 1790553600  # Mon 2026-09-28 00:00 UTC


def served_higher(plan: str, unit: str) -> list[dict]:
    """keel-api's `higher_plans` for a v2 `plan` on `unit`: the catalog's
    names AND their limits (D-10), so "names, never numbers" is non-vacuous."""
    from . import _plan_catalog as cat

    out = cat.higher_plans(plan, unit)
    for entry in out:
        value = cat._value(entry["plan"], entry["version"], unit)
        entry["limit"] = int(value) if value != float("inf") else "unlimited"
    return out


def served_faster(plan: str) -> list[dict]:
    """keel-api's `higher_plans` on the paper/live floor (with their floors)."""
    from . import _plan_catalog as cat

    out = cat.faster_plans(plan)
    for entry in out:
        entry["floor"] = cat.floor_of(entry["plan"], entry["version"])
    return out


def more_names(plan: str, unit: str) -> tuple[str, ...]:
    from . import _plan_catalog as cat

    if unit == "paper_live_clock_floor":
        return tuple(cat.names(cat.faster_plans(plan)))
    return tuple(cat.names(cat.higher_plans(plan, unit)))


def own_name(plan: str) -> str:
    from . import _plan_catalog as cat

    return cat.display_name(plan)


def _limit(plan: str, unit: str) -> int:
    from . import _plan_catalog as cat

    return int(cat._value(plan, "v2", unit))


def wall_403_body(plan: str, unit: str = "backtest_runs") -> dict:
    """A plan-limit 403 exactly as keel-api serves it (numbers included — D-10)."""
    limit = _limit(plan, unit)
    return {
        "title": "Forbidden",
        "status": 403,
        "detail": f"You've used all {limit} backtests included on this plan.",
        "code": "quota_exhausted",
        "quota": {
            "kind": "insufficient",
            "unit": unit,
            "label": "backtests",
            "code": "quota_exhausted",
            "limit": limit,
            "used": limit,
            "remaining": 0,
            "period": "30d",
            "reset_epoch": RESET_EPOCH,
            "plan": plan,
            "plan_name": own_name(plan),
            "higher_plans": served_higher(plan, unit),
        },
    }


def _plan_status_block(plan: str) -> dict:
    """keel-api's /v1/me `plan_status` block (numbers + options), as served."""
    options = (
        [
            {
                "plan": "starter",
                "price": {"usd_per_month": 29, "usd_per_month_billed_annually": 23},
                "what_changes": {
                    "backtest_runs": {"from": 50, "to": 500},
                    "builder_fee_bps": {"from": 5, "to": 3},
                },
            },
            {"plan": "trader", "price": {"usd_per_month": 79}, "what_changes": {}},
        ]
        if plan == "free"
        else [{"plan": "pro", "price": {"usd_per_month": 199}, "what_changes": {}}]
    )
    limit = 50 if plan == "free" else 500
    return {
        "plan": plan,
        "builder_fee_bps": 5 if plan == "free" else 2,
        "limits": {"backtest_runs": limit, "compute_seconds": 1500, "live_slots": 1},
        "remaining": {"backtest_runs": 46, "compute_seconds": 1380, "live_slots": 1},
        "upgrade_options": options,
        "manage_url": "https://app.usekeel.io/settings?tab=billing",
    }


#: A served first-week block per unit (connect-onboarding spec 01 §1.8) —
#: the one time-limited allowance an agent surface states (§1.9).
FIRST_WEEK = {
    "backtest_runs": {"granted": 200, "remaining": 154, "ends_at": "2026-10-13T15:02:11Z"},
    "backtest_compute_seconds": {
        "granted": 6000,
        "remaining": 4620,
        "ends_at": "2026-10-13T15:02:11Z",
    },
}

#: The clause `FIRST_WEEK["backtest_runs"]` folds into a backtests line
#: (Q-7601 fix round), exactly.
FIRST_WEEK_CLAUSE = "(154 first-week extras until Oct 13)"
#: An approved backtests line carrying it: the control every urgency seed in
#: test_policy_scan.py is built from.
FIRST_WEEK_SENTENCE = f"Backtests: 246 of 250 left {FIRST_WEEK_CLAUSE}; resets Sep 22."


def _me(plan: str, *, first_week: bool = False) -> dict:
    body = _me_body(plan)
    if first_week:
        for balance in body["entitlements"]:
            if balance["unit"] in FIRST_WEEK:
                balance["first_week"] = dict(FIRST_WEEK[balance["unit"]])
                # The served totals include the grant (entitlements.py
                # `get_consumable_balance`), so what is left includes it too:
                # a first-week part never exceeds `available`.
                balance["granted"] += FIRST_WEEK[balance["unit"]]["granted"]
                balance["available"] += FIRST_WEEK[balance["unit"]]["granted"]
    return body


def _me_body(plan: str) -> dict:
    limit = 50 if plan == "free" else 500
    caps = [
        {
            "unit": unit,
            "type": "cap",
            "granted": _limit(plan, unit),
            "cap_current": 0,
            "available": _limit(plan, unit),
            "higher_plans": served_higher(plan, unit),
        }
        for unit in (
            "paper_strategies_max",
            "screeners_live_max",
            "screeners_published_max",
            "screeners_private_max",
        )
    ]
    return {
        "plan_name": own_name(plan),
        "serving_floor": {"floor": _floor(plan), "higher_plans": served_faster(plan)},
        "principal": {"id": "prn_guard", "type": "user"},
        "org": {"id": "org_guard", "name": "Surface Guard", "plan": plan, "status": "active"},
        "credential_scopes": ["strategy.*"],
        "entitlements": [
            {
                "unit": "backtest_runs",
                "type": "consumable",
                "granted": limit,
                "spent": limit - 46,
                "reserved": 0,
                "available": 46,
                "higher_plans": served_higher(plan, "backtest_runs"),
            },
            {
                "unit": "backtest_compute_seconds",
                "type": "consumable",
                "granted": 1500,
                "spent": 120,
                "reserved": 0,
                "available": 1380,
                "higher_plans": served_higher(plan, "backtest_compute_seconds"),
            },
            *caps,
        ],
        "plan_status": _plan_status_block(plan),
    }


def _floor(plan: str) -> str:
    from . import _plan_catalog as cat

    return cat.floor_of(plan)


def _names_only(entries: list[dict]) -> list[dict]:
    return [{k: e[k] for k in ("plan", "version", "name")} for e in entries]


def coins_422_body(plan: str = "free", resolved: int = 180) -> dict:
    """keel-api's coin-cap refusal (V3-API shape 1) on a run, save or copy: a 422 with the four-part wall, the
    remedy whose `apply` the same call takes, and the agent's suggestion."""
    from . import _plan_catalog as cat

    limit = int(cat._value(plan, "v3", "screen_assets_max"))
    names = cat.names(cat.higher_plans(plan, "screen_assets_max", "v3"))
    more = f" {', '.join(names[:-1])} and {names[-1]} include more." if len(names) > 1 else ""
    return {
        "detail": {
            "code": "SCREEN_COINS_OVER_PLAN",
            "unit": "screen_assets_max",
            "kind": "setting",
            "message": (
                f"{own_name(plan)} screens cover up to {limit} coins; this one covers {resolved}."
                f"{more} Plans are changed in the Keel web app."
            ),
            "limit": limit,
            "resolved": resolved,
            "plan": plan,
            "plan_name": own_name(plan),
            "higher_plans": served_higher(plan, "screen_assets_max"),
            "remedy": {
                "kind": "narrow_universe",
                "max_coins": limit,
                "resolved_coins": resolved,
                "apply": {"narrow_to_plan": True},
                "words": f"Keeps the {limit} most traded of the {resolved}.",
                "options": [
                    {"mode": "top_volume", "top_n": limit},
                    {"mode": "manual", "max": limit},
                ],
            },
            "suggestion": (
                f"Plan limit. Tell the user the coin limit and which plans include more; use the top {limit} or a "
                f"{limit}-coin pick. Don't split the screen into several runs to get past it."
            ),
        }
    }


def _me_v3(plan: str = "free") -> dict:
    """/v1/me on a plans v3 org: the screens block (saved, coins per screen, refresh) and the split floor (spec 02
    §L3), as platform_auth.pricing serves them (names only on every new block)."""
    from . import _plan_catalog as cat

    body = _me_body(plan)
    floor_paper = cat.floor_of(plan, "v3", "paper")
    floor_live = cat.floor_of(plan, "v3")

    def side(applies: str, floor: str) -> dict:
        return {
            "applies": applies,
            "floor": floor,
            "plan": plan,
            "plan_name": own_name(plan),
            "higher_plans": _names_only(cat.faster_plans(plan, "v3", applies)),
        }

    body["serving_floor"] = {
        "floor": floor_paper,
        "plan": plan,
        "plan_name": own_name(plan),
        "higher_plans": _names_only(cat.faster_plans(plan, "v3", "paper")),
        "paper": side("paper", floor_paper),
        "live": side("live", floor_live),
    }
    body["plan_status"]["screens"] = {
        "saved": {
            "unit": "screeners_max",
            "limit": int(cat._value(plan, "v3", "screeners_max")),
            "used": 1,
            "remaining": int(cat._value(plan, "v3", "screeners_max")) - 1,
            "higher_plans": _names_only(cat.higher_plans(plan, "screeners_max", "v3")),
        },
        "coins_per_screen": {
            "limit": int(cat._value(plan, "v3", "screen_assets_max")),
            "higher_plans": _names_only(cat.higher_plans(plan, "screen_assets_max", "v3")),
        },
        "refresh_min_minutes": {
            "limit": int(cat._value(plan, "v3", "screener_refresh_min_minutes")),
            "higher_plans": _names_only(
                cat.higher_plans(plan, "screener_refresh_min_minutes", "v3")
            ),
        },
    }
    return body


_ENTITLEMENTS = {
    "balances": [{"unit": "backtests", "granted": 50, "spent": 4, "reserved": 0, "available": 46}]
}

_DETAIL = {
    "id": "btr_guard",
    "status": "COMPLETED",
    "strategy_id": "str_guard",
    "strategy_name": "Guard Momentum",
    "sequence_number": 3,
    "commit_id": "c_1a2b3c4d",
    "engine": "native",
    "start_date": "2024-08-15",
    "end_date": "2026-09-22",
    "completed_at": "2026-09-22T11:04:00Z",
    "metrics": {
        "sharpe": 0.67,
        "total_return_pct": 60.5,
        "max_drawdown": 43.4,
        "total_trades": 3,
        "win_rate_pct": 33.3,
        "turnover": 20.0,
    },
}


def _critical_block() -> dict:
    """A critical-tier quota block with keel-api's plan data attached."""
    return {
        "unit": "backtest_runs",
        "label": "backtests",
        "limit": 50,
        "used": 46,
        "remaining": 4,
        "unlimited": False,
        "period": "weekly",
        "resets_at": "2026-09-28T00:00:00Z",
        "seconds_to_reset": 24300,
        "tier": "critical",
        "plan": "free",
        "plan_name": "Free",
        "higher_plans": served_higher("free", "backtest_runs"),
    }


@dataclass
class Payload:
    """One tool result as a host receives it."""

    name: str
    tool: str
    text: str  # every text content block, joined
    structured: Any  # structuredContent (parsed when it is a JSON string)
    is_error: bool
    #: The caller's own plan name and the names of the plans the server said
    #: include more (D-14): what `plan_name_violations` allows, per payload.
    own_plan: str | None = None
    more_plans: tuple[str, ...] = ()

    def strings(self) -> list[str]:
        """The text block plus every string VALUE in the structured content."""
        return [self.text, *_string_values(self.structured)]

    def keys(self) -> list[str]:
        return _json_keys(self.structured)


def _string_values(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [s for v in node.values() for s in _string_values(v)]
    if isinstance(node, list):
        return [s for item in node for s in _string_values(item)]
    return [node] if isinstance(node, str) else []


def _json_keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [*node.keys(), *(k for v in node.values() for k in _json_keys(v))]
    if isinstance(node, list):
        return [k for item in node for k in _json_keys(item)]
    return []


def _route(table: dict[tuple[str, str], Any]):
    """A respx side effect: (METHOD, path) → JSON | (status, JSON); else 404."""
    import httpx

    def side_effect(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        hit = table.get(key)
        if hit is None:
            for (method, prefix), value in table.items():
                if (
                    method == request.method
                    and prefix.endswith("*")
                    and request.url.path.startswith(prefix[:-1])
                ):
                    hit = value
                    break
        if hit is None:
            return httpx.Response(
                404, json={"title": "Not Found", "status": 404, "detail": "no route"}
            )
        status, body = hit if isinstance(hit, tuple) else (200, hit)
        return httpx.Response(status, json=body)

    return side_effect


@contextmanager
def full_hosted_env() -> Iterator[None]:
    """A full-profile hosted server's environment (default toolsets), restored on exit."""
    saved = {k: os.environ.get(k) for k in _PROFILE_ENV}
    os.environ["KEEL_SERVER_PROFILE"] = "full"
    os.environ["KEEL_EXECUTION_MODE"] = "hosted"
    os.environ.pop("KEEL_TOOLSETS", None)
    os.environ.pop("KEEL_LISTED_CLIENT", None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _call(tool: str, args: dict, table: dict[tuple[str, str], Any], *, env=listed_env) -> Payload:
    """``tools/call`` on a real hosted server with a bound caller (listed by default)."""
    import keel.client
    import keel.tools.outcomes._handoff as handoff
    import respx
    from keel.hosting import bind_request_credentials, clear_request_credentials
    from keel.mcp.server import create_server

    saved_sleep = keel.client.time.sleep
    saved_anon = handoff._is_anon_session
    keel.client.time.sleep = lambda *_a, **_k: None
    handoff._is_anon_session = lambda: False  # the hosted surface is never anonymous
    try:
        with env(), respx.mock(assert_all_called=False) as router:
            router.route(host="api.surface-guards.test").mock(side_effect=_route(table))
            server = create_server()
            reset = bind_request_credentials(token="guard-token", api_url=API)
            try:
                result = asyncio.run(server.call_tool(tool, args))
            finally:
                clear_request_credentials(reset)
    finally:
        keel.client.time.sleep = saved_sleep
        handoff._is_anon_session = saved_anon
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    structured = result.structured_content
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        try:
            structured = json.loads(structured["result"])
        except ValueError:
            pass
    is_error = bool(getattr(result, "is_error", False))
    return Payload(name="", tool=tool, text=text, structured=structured, is_error=is_error)


@cache
def runtime_payloads() -> dict[str, Payload]:
    """Every runtime fixture, built through the real SDK paths (HTTP stubbed)."""
    import keel.tools.outcomes.backtest_run as br

    saved = (br._POLL_INTERVAL_S, br._POLL_MAX_S)
    br._POLL_INTERVAL_S, br._POLL_MAX_S = 0.0, 0.05
    try:
        out = {
            "free_wall": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard"},
                {("POST", "/v1/backtests"): (403, wall_403_body("free"))},
            ),
            "paid_wall": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard"},
                {("POST", "/v1/backtests"): (403, wall_403_body("trader"))},
            ),
            # The top plan: no plan includes more, so no clause (founder Q2).
            "top_wall": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard"},
                {("POST", "/v1/backtests"): (403, wall_403_body("pro"))},
            ),
            "plan_status_free": _call("keel_plan_usage", {}, {("GET", "/v1/me"): _me("free")}),
            # Plans v3 (spec 02 §C4.1): the screens block and the split floor, on the listed profile.
            "plan_status_v3": _call("keel_plan_usage", {}, {("GET", "/v1/me"): _me_v3("free")}),
            # The coin cap (spec 02 §C4.3): an unsaved screen run over Free's coins per screen.
            "screen_coins_wall": _call(
                "keel_screener_run",
                {"source": 'Globals(target_timeframe="1d")'},
                {("POST", "/v1/result-runs"): (422, coins_422_body("free"))},
            ),
            "plan_status_paid": _call("keel_plan_usage", {}, {("GET", "/v1/me"): _me("trader")}),
            # A first-week account (spec 01 §1.9): the one allowed time-limited
            # sentence must pass every family — the control arm for urgency.
            "plan_status_first_week": _call(
                "keel_plan_usage", {}, {("GET", "/v1/me"): _me("free", first_week=True)}
            ),
            "critical_run": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard", "wait": False},
                {
                    ("POST", "/v1/backtests"): {
                        "id": "btr_guard",
                        "status": "queued",
                        "strategy_id": "str_guard",
                        "quota": [_critical_block()],
                    },
                    ("GET", "/v1/backtests/*"): {**_DETAIL, "status": "RUNNING"},
                },
            ),
            "critical_run_first_week": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard", "wait": False},
                {
                    ("POST", "/v1/backtests"): {
                        "id": "btr_guard",
                        "status": "queued",
                        "strategy_id": "str_guard",
                        "quota": [
                            {
                                **_critical_block(),
                                # Capped by what is left at all, as keel-api serves it.
                                "first_week": {**FIRST_WEEK["backtest_runs"], "remaining": 4},
                            }
                        ],
                    },
                    ("GET", "/v1/backtests/*"): {**_DETAIL, "status": "RUNNING"},
                },
            ),
            # The paper/live timeframe heads-up on a run (doc 07 §5, Q4).
            "floor_run": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard", "wait": False},
                {
                    ("POST", "/v1/backtests"): {
                        "id": "btr_guard",
                        "status": "queued",
                        "strategy_id": "str_guard",
                        "serving_floor": {
                            "floor": "1h",
                            "requested": "15min",
                            "plan": "free",
                            "plan_name": "Free",
                            "higher_plans": served_faster("free"),
                        },
                    },
                    ("GET", "/v1/backtests/*"): {**_DETAIL, "status": "RUNNING"},
                },
            ),
            "status": _call(
                "keel_account_status",
                {},
                {("GET", "/v1/me"): _me("free"), ("GET", "/v1/entitlements"): _ENTITLEMENTS},
            ),
            "sparse_run": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard", "wait": True},
                {
                    ("POST", "/v1/backtests"): {**_DETAIL, "status": "queued"},
                    ("GET", "/v1/backtests"): {"data": [], "pagination": {}},
                    ("GET", "/v1/backtests/*"): _DETAIL,
                },
            ),
            "strategy_log_empty": _call(
                "keel_strategy_history",
                {"strategy_id": "str_guard"},
                {("GET", "/v1/strategies/str_guard/versions"): []},
            ),
            "auth_401": _call(
                "keel_strategy_search",
                {},
                {("GET", "/v1/strategies"): (401, {"title": "Unauthorized", "status": 401})},
            ),
            # Every route answers 404: the generic translate_http_error arm.
            "not_found_404": _call("keel_strategy_history", {"strategy_id": "str_missing"}, {}),
        }
    finally:
        br._POLL_INTERVAL_S, br._POLL_MAX_S = saved
    out.update(period_30d_payloads())
    for name, payload in out.items():
        payload.name = name
    _attach_plans(out)
    return out


#: Each plan payload's own plan and the unit whose served names it may say.
_PAYLOAD_PLANS: dict[str, tuple[str, str]] = {
    "free_wall": ("free", "backtest_runs"),
    "paid_wall": ("trader", "backtest_runs"),
    "top_wall": ("pro", "backtest_runs"),
    "critical_run": ("free", "backtest_runs"),
    "critical_run_first_week": ("free", "backtest_runs"),
    "critical_run_30d": ("free", "backtest_runs"),
    "plan_status_free": ("free", "backtest_runs"),
    "plan_status_paid": ("trader", "backtest_runs"),
    "plan_status_first_week": ("free", "backtest_runs"),
    "plan_status_30d": ("free", "backtest_runs"),
    "plan_status_v3": ("free", "backtest_runs"),
    "screen_coins_wall": ("free", "screen_assets_max"),
    "paid_wall_30d": ("trader", "backtest_runs"),
    "floor_run": ("free", "paper_live_clock_floor"),
    "paper_wall": ("free", "paper_strategies_max"),
    "paper_last_slot": ("free", "paper_strategies_max"),
    "paper_floor_free": ("free", "paper_live_clock_floor"),
    "paper_floor_paid": ("trader", "paper_live_clock_floor"),
    "screen_private_wall": ("free", "screeners_private_max"),
    "plan_status_full": ("free", "backtest_runs"),
}


def _attach_plans(payloads: dict[str, Payload]) -> None:
    for name, payload in payloads.items():
        if name in _PAYLOAD_PLANS:
            plan, unit = _PAYLOAD_PLANS[name]
            payload.own_plan = own_name(plan)
            payload.more_plans = more_names(plan, unit)


# ─── The 30-day cycle (plan change spec 01 §C5/§C11) ────────────────────
#
# The current plans meter backtests per account on a 30-day cycle
# (`period: "30d"`). The agent surfaces gain exactly one wording for it —
# "this 30-day period" — and it must pass every D-12 family like "this week"
# does. These fixtures render that wording through the real SDK paths: the
# plan-usage sentence, a critical-tier run's quota notice, and a paid wall
# (whose headline carries no period adjective).

#: The 30-day cycle's next reset, an arbitrary instant (not a Monday).
RESET_30D_AT = "2026-11-29T14:00:00Z"  # Sun 29 Nov 2026
RESET_30D_EPOCH = 1795960800


def _me_30d() -> dict:
    """A current-plans Free org mid-cycle: both backtest units on `30d`."""
    body = _me_body("free")
    body["entitlements"] = [
        {
            "unit": "backtest_runs",
            "type": "consumable",
            "granted": 25,
            "spent": 4,
            "reserved": 0,
            "available": 21,
            "period": "30d",
            "resets_at": RESET_30D_AT,
        },
        {
            "unit": "backtest_compute_seconds",
            "type": "consumable",
            "granted": 750,
            "spent": 120,
            "reserved": 0,
            "available": 630,
            "period": "30d",
            "resets_at": RESET_30D_AT,
        },
    ]
    return body


def _critical_block_30d() -> dict:
    return {
        **_critical_block(),
        "limit": 25,
        "used": 21,
        "remaining": 4,
        "period": "30d",
        "resets_at": RESET_30D_AT,
    }


def _paid_wall_30d_body() -> dict:
    body = wall_403_body("trader")
    body["quota"].update(period="30d", reset_epoch=RESET_30D_EPOCH)
    return body


def period_30d_payloads() -> dict[str, Payload]:
    """The three 30-day payloads, built fresh on every call (uncached), so a
    seeded-failure arm can rebuild them under a patched phrase table."""
    import keel.tools.outcomes.backtest_run as br

    saved = (br._POLL_INTERVAL_S, br._POLL_MAX_S)
    br._POLL_INTERVAL_S, br._POLL_MAX_S = 0.0, 0.05
    try:
        out = {
            "plan_status_30d": _call("keel_plan_usage", {}, {("GET", "/v1/me"): _me_30d()}),
            "critical_run_30d": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard", "wait": False},
                {
                    ("POST", "/v1/backtests"): {
                        "id": "btr_guard",
                        "status": "queued",
                        "strategy_id": "str_guard",
                        "quota": [_critical_block_30d()],
                    },
                    ("GET", "/v1/backtests/*"): {**_DETAIL, "status": "RUNNING"},
                },
            ),
            "paid_wall_30d": _call(
                "keel_backtest_run",
                {"strategy_id": "str_guard"},
                {("POST", "/v1/backtests"): (403, _paid_wall_30d_body())},
            ),
        }
    finally:
        br._POLL_INTERVAL_S, br._POLL_MAX_S = saved
    for name, payload in out.items():
        payload.name = name
    _attach_plans(out)
    return out


def _cap_403_body(plan: str, unit: str, *, kind: str, code: str, used: int) -> dict:
    """A cap or no-grant refusal exactly as keel-api serves it (D-14)."""
    limit = _limit(plan, unit)
    return {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
        "detail": "Insufficient entitlements",
        "code": code,
        "reasons": [f"entitlement:{kind}:{unit}:limit={limit}:current={used}"],
        "quota": {
            "kind": kind,
            "unit": unit,
            "code": code,
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "plan": plan,
            "plan_name": own_name(plan),
            "higher_plans": served_higher(plan, unit),
        },
    }


#: The paper quota wall as keel-api denies a create (S01 IC-01-10).
PAPER_CAP_403_BODY = _cap_403_body(
    "free", "paper_strategies_max", kind="cap_exceeded", code="quota_cap_reached", used=1
)
#: Free has no private auto-refreshing screens (plans.yaml: undeclared = 0).
SCREEN_PRIVATE_403_BODY = _cap_403_body(
    "free",
    "screeners_private_max",
    kind="no_cap_grants",
    code="plan_feature_unavailable",
    used=0,
)


#: The paper/live timeframe-floor refusal's words, by floor, exactly as the
#: plan change contract fixes them (spec 01 §L7): the server's message IS the
#: neutral sentence, with `floor`/`requested` beside it.
FLOOR_WORDS = {"1h": "1-hour", "15min": "15-minute", "5min": "5-minute"}


def floor_refusal_message(floor: str) -> str:
    return (
        f"This plan runs paper and live strategies at a {FLOOR_WORDS[floor]} timeframe "
        "or longer; this limit does not reset on a clock."
    )


#: The plan whose floor each fixture floor is (v2).
FLOOR_PLAN = {"1h": "free", "15min": "trader", "5min": "pro"}


def floor_refusal_422_body(floor: str, requested: str, *, message: str | None = None) -> dict:
    """keel-api's §L7 refusal as FastAPI renders a dict HTTPException detail,
    with the D-14 fields: ``{"detail": {code, message, floor, requested,
    plan, plan_name, higher_plans}}``. ``message`` overrides the server's
    sentence (the seeded-failure arm)."""
    plan = FLOOR_PLAN[floor]
    return {
        "detail": {
            "code": "SERVING_CLOCK_BELOW_PLAN_FLOOR",
            "message": message if message is not None else floor_refusal_message(floor),
            "floor": floor,
            "requested": requested,
            "plan": plan,
            "plan_name": own_name(plan),
            "higher_plans": served_faster(plan),
        }
    }


def paper_floor_payload(floor: str, requested: str, *, message: str | None = None) -> Payload:
    """``keel_paper_deploy`` refused by the timeframe floor, through the real
    MCP adapter (keel-api stubbed with the §L7 422). Built where the tool
    runs, exactly like the paper wall."""
    from tests._paper_sdk import paper_sdk_release

    with paper_sdk_release():
        payload = _call(
            "keel_paper_deploy",
            {"strategy_id": "str_guard"},
            {
                ("POST", "/v1/paper"): (
                    422,
                    floor_refusal_422_body(floor, requested, message=message),
                )
            },
            env=full_hosted_env,
        )
    payload.name = f"paper_floor_{floor}"
    return payload


@cache
def paper_runtime_payloads() -> dict[str, Payload]:
    """The paper runtime fixtures (paper-trading-v2 S06 §3.4.6), built through
    the real MCP adapter on a full-profile hosted server while the held paper
    SDK is released for the build only; the held state is restored after.

    The listed connector cannot serve keel_paper_deploy while the SDK is held
    (the listed flag is refused), so the wall is built where the tool runs.

    The two timeframe-floor refusals (plan change spec 01 §C10/§C11): the
    current Free plan's 1h floor refusing a 15min strategy, and a paid plan's
    15min floor refusing a 5min one — both the server's neutral sentence.
    """
    from tests._paper_sdk import paper_sdk_release

    with paper_sdk_release():
        out = {
            "paper_wall": _call(
                "keel_paper_deploy",
                {"strategy_id": "str_guard"},
                {("POST", "/v1/paper"): (403, PAPER_CAP_403_BODY)},
                env=full_hosted_env,
            ),
            # Taking the last slot (doc 07 §5): keel-api's exhausted-tier block.
            "paper_last_slot": _call(
                "keel_paper_deploy",
                {"strategy_id": "str_guard"},
                {
                    ("POST", "/v1/paper"): {
                        "deployment_id": "dep_guard",
                        "status": "DEPLOYING",
                        "strategy_id": "str_guard",
                        # One object, as keel-api serves the last slot taken.
                        "quota": {
                            "unit": "paper_strategies_max",
                            "label": "paper strategies",
                            "limit": 1,
                            "used": 1,
                            "remaining": 0,
                            "tier": "exhausted",
                            "plan": "free",
                            "plan_name": "Free",
                            "higher_plans": served_higher("free", "paper_strategies_max"),
                        },
                    }
                },
                env=full_hosted_env,
            ),
        }
    out["plan_status_full"] = _call(
        "keel_plan_usage", {}, {("GET", "/v1/me"): _me("free")}, env=full_hosted_env
    )
    out["screen_private_wall"] = _call(
        "keel_screener_refresh",
        {"strategy_id": "str_guard"},
        {
            ("GET", "/v1/screener-shares"): {"items": []},
            ("GET", "/v1/strategies/str_guard"): {"head_commit_id": "c_1"},
            ("POST", "/v1/screeners/str_guard/refresh"): (403, SCREEN_PRIVATE_403_BODY),
        },
        env=full_hosted_env,
    )
    out["paper_floor_free"] = paper_floor_payload("1h", "15min")
    out["paper_floor_paid"] = paper_floor_payload("15min", "5min")
    for name, payload in out.items():
        payload.name = name
    _attach_plans(out)
    return out


#: The fixtures a plan-limit wall produces.
WALL_FIXTURES = ("free_wall", "paid_wall", "top_wall")

#: Every v2 limit kind's fixtures at the wall, near it, and in plan usage
#: (doc 07 §7: "every limit kind ... has a wall, notice and plan-usage
#: fixture"), each with the sentence that proves it is that kind's. A
#: feature has no near-limit notice (doc 07 §5: "—").
KIND_FIXTURES: dict[str, dict[str, tuple[str, str] | None]] = {
    "consumable": {
        "wall": ("free_wall", "Backtests: 25 of 25 used on Free"),
        "notice": ("critical_run", "4 of 50 backtests left"),
        "plan_usage": ("plan_status_free", "Backtests: 46 of 50 left"),
    },
    "cap": {
        "wall": ("paper_wall", "Paper strategies: 1 of 1 running on Free"),
        "notice": ("paper_last_slot", "Last paper slot in use (1 of 1)."),
        "plan_usage": ("plan_status_full", "Paper strategies: 0 of 1 running on Free."),
    },
    "floor": {
        "wall": ("paper_floor_free", "Paper and live on Free run at 1 hour or slower"),
        "notice": ("floor_run", "Paper and live on Free run at 1 hour or slower"),
        "plan_usage": ("plan_status_v3", "Paper on Free runs daily."),
    },
    # Plans v3 (spec 02 §C2): a size bound per action; no near notice (§D "—").
    "setting": {
        "wall": (
            "screen_coins_wall",
            "Free screens cover up to 30 coins; this one covers 180.",
        ),
        "notice": None,
        "plan_usage": ("plan_status_v3", "Coins per screen: up to 30 on Free."),
    },
    "feature": {
        # The v3 feature wall, inclusion first (spec 02 §D; a cap of 0 reads as a feature, PLANS-REVIEW R30).
        "wall": (
            "screen_private_wall",
            "Private auto-refreshing screens are included on Trader, Quant and Pro.",
        ),
        "notice": None,
        "plan_usage": ("plan_status_full", "Private auto-refreshing screens: not on Free."),
    },
}


# ─── The plan-limit card (widgets/assets/host-adapter.js renderPlanLimit) ──


def render_plan_limit_source() -> str:
    """The `renderPlanLimit` function's source, brace-matched."""
    src = HOST_ADAPTER_JS.read_text(encoding="utf-8")
    start = src.index("function renderPlanLimit(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError("renderPlanLimit is not brace-balanced")


def js_string_literals(src: str) -> list[str]:
    """String literals ('…', "…", `…`) in a JS snippet, comments excluded."""
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return [m.group(2) for m in re.finditer(r"(['\"`])((?:\\.|(?!\1).)*)\1", src)]


# ─── Sentence-exact exemptions: the lane-B2 rephrase residue ────────────
#
# 05 §3.1 keeps these fact sections SHARED and has lane B2 rephrase them
# descriptively; lane B1 moves text verbatim and never rephrases. So after
# the reorganisation each sentence below is still served, still carries the
# pattern its guard reads, and is the ONE place that guard is told to look
# away — per sentence, per topic, with the reason. Derived 2026-09-28 by
# running every body guard over the base corpus minus the sections 05 §3.1
# moves to chat/ (lane-G scratch `predict_post_b1.py`). The liveness arm
# (`test_pending_rephrase_exemptions_are_live`) fails the moment B2 rewrites
# one — the entry is then deleted, never re-pointed.


@dataclass(frozen=True)
class Exemption:
    guard: str  # "G3" | "G4" | "G6"
    topic: str  # the served topic (normalized) the sentence is exempt IN
    sentence: str  # exactly as `sentences()` yields it
    reason: str


PENDING_REPHRASE: tuple[Exemption, ...] = ()


def is_exempt(guard: str, topic: str, sentence: str) -> bool:
    return any(
        e.guard == guard and e.topic == topic and e.sentence == sentence for e in PENDING_REPHRASE
    )
