#!/usr/bin/env python3
"""Surface-routing table consistency gate (spec 07 R7, agent-first-build M6.4).

The canonical routing table lives in ``shared/surface-routing.json``.
Every rendered copy must match it exactly:

* Markdown surfaces carry a generated block between
  ``<!-- surface-routing:begin -->`` / ``<!-- surface-routing:end -->``
  markers — the block must byte-match the canonical render.
* The keel-site TSX surfaces (``/agents`` page + the keel-mcp page)
  must import the canonical JSON (``@shared/surface-routing.json``)
  instead of hand-copying rows — asserted by import grep.

Also enforced here (spec 07 R1/R2 — same one-source rule):

* ``public/.well-known/agents.md`` is byte-identical to
  ``public/AGENTS.md`` (single source, alternate discovery path).
* The generated raw-markdown docs variants (``public/docs/**.md`` +
  ``public/docs.md``) match the deterministic transform of
  ``content/docs/**.mdx`` (see ``generate-docs-md.mjs`` in keel-site).

The AGENT-SURFACE source (agent-surface spec 02 R1/R2) — how an agent
connects to Keel — lives in ``shared/agent-surface.json`` beside the
routing table. The hosted tool COUNT and tool LIST are never stored: they
are read from ``LISTED_PROFILE_TOOLS`` + the outcome registry at
generation time. Its consumers, all rendered by ``--write``:

* ``<!-- tool-inventory:begin -->`` / ``<!-- tool-inventory:end -->`` in
  ``services/keel-site/public/AGENTS.md`` (and its ``.well-known`` byte copy)
  — the WHOLE outcome registry by toolset, hand-maintained until Q-1703;
* ``<!-- agent-surface:begin -->`` / ``<!-- agent-surface:end -->`` marker
  blocks in the two ``AGENTS.md`` files (+ ``.well-known/agents.md``),
  ``public/llms.txt`` and the public-overlay README — byte-matched;
* ``public/.well-known/agent-card.json`` — the WHOLE file;
* ``shared/agent-surface.generated.ts`` and its byte-identical keel-app
  copy ``services/keel-app/src/lib/agent-surface.generated.ts`` — the
  typed module the site pages, the in-app Agents area and (M4) the
  connect box import, so no snippet is ever hand-typed in TSX again.

* ``services/keel-site/public/.well-known/skills/index.json`` — rendered by
  ``build_skills_manifest.render_manifest`` (keel.skills + this source) and
  held whole here: a ``hosted`` block (tool path, served / excluded names)
  and hosted-first install pointers (Q-1467, task 2.8);
* the two generated tool references (``packages/keel-trade/docs/
  tool-reference.md`` + ``content/docs/sdk/tool-reference.mdx``) — rendered
  by ``generate_agent_reference_docs.render_reference`` with a Hosted column
  from ``LISTED_PROFILE_TOOLS``, held whole here (task 2.8);
* ``services/keel-site/content/docs/agents/setup.mdx`` — the WHOLE docs
  setup page (the page ``MCP_DOCS_URL`` points at), rendered from the
  same source; its raw ``public/docs/agents/setup.md`` variant is held
  by the docs-md arm below.

* the agent briefing (``public/agent-briefing.md``, the file keel-site's
  middleware serves byte-identically as ``/agents.md`` and to
  ``Accept: text/markdown`` / pure-fetcher requests on ``/``, ``/agents``
  and ``/agents/*`` — spec 02 R4 as amended, task 2.4): second person,
  Railway's order (what Keel is → how to connect → what you can do, by
  tool group → the handoff → docs).

Run from the repo root:

    python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py
    python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write

``--write`` regenerates every marker block, the agent card and the TS
module in place (it never touches the TSX pages — they import the
generated module / the JSON directly).

CI: tests/test_surface_routing.py wraps this checker, so it runs in the
SDK test lane (sdk-test.yml + graph-selected test.yml targets). This is a
monorepo-only build/test utility — REPO_ROOT walks up from scripts/ into
the larger checkout, so its hardcoded paths only resolve inside the
monorepo. It is bundled into the public mirror as an inert file (matched
by the scripts/*.py allowlist entry, like its build-utility peers); it is
NOT on the sync rm list, so unlike check_agent_surface_docs.py it is not
stripped. Missing paths are reported as errors, not skipped — a renamed
render target must fail loudly.
"""

from __future__ import annotations

import ast
import base64
import importlib.util
import json
import re
import sys
import urllib.parse
from datetime import date, datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SDK_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SDK_ROOT.parent.parent.parent

CANONICAL_PATH = REPO_ROOT / "shared" / "surface-routing.json"

BEGIN_MARKER = "<!-- surface-routing:begin -->"
END_MARKER = "<!-- surface-routing:end -->"

# Markdown render targets — each must contain exactly one marker block.
MARKDOWN_TARGETS = [
    "services/keel-site/public/AGENTS.md",
    "services/keel-site/public/.well-known/agents.md",
    "packages/keel-trade/public-overlay/README.md",
    "packages/keel-trade/keel-sdk/AGENTS.md",
]

# TSX consumers of the surface source (numbers, tool names, copy law).
TSX_TARGETS = [
    "services/keel-site/src/app/agents/page.tsx",
    "services/keel-site/src/app/keel-mcp/page.tsx",
]
# No page renders the canonical routing table (AS-22 as amended 2026-09-17,
# Q-1487): the ConnectBox is every page's answer to spec 07 R7's "one
# recommended path", and the table stays in the agent-facing markdown targets
# above, where an agent reads it. TSX_TARGETS remain number consumers.

# keel-app cannot import @shared (shared/README.md), so its Agents area keeps
# a hand-copied ROUTING_TABLE; it is held to the canonical rows by value.
KEEL_APP_ROUTING_TABLE = "services/keel-app/src/lib/agent-surface.ts"
_KEEL_APP_ROW_RE = re.compile(
    r'audience:\s*"(?P<audience>[^"]*)",\s*defaultPath:\s*"(?P<default_path>[^"]*)",'
    r'\s*alsoWorks:\s*"(?P<also_works>[^"]*)",'
)

# Single-source rule for the agents instruction files (spec 07 R1).
AGENTS_MD = "services/keel-site/public/AGENTS.md"
WELL_KNOWN_AGENTS_MD = "services/keel-site/public/.well-known/agents.md"

# Raw-markdown docs variants (spec 07 R2). public/docs/**.md is emitted
# by services/keel-site/scripts/generate-docs-md.mjs; the transform is
# re-implemented here (deterministically) so drift fails CI.
DOCS_CONTENT_DIR = "services/keel-site/content/docs"
DOCS_MD_PUBLIC_DIR = "services/keel-site/public/docs"
DOCS_MD_ROOT_FILE = "services/keel-site/public/docs.md"


AGENT_SURFACE_PATH = REPO_ROOT / "shared" / "agent-surface.json"

AS_BEGIN_MARKER = "<!-- agent-surface:begin -->"
AS_END_MARKER = "<!-- agent-surface:end -->"

# Agent-surface marker-block targets: (repo-relative path, block flavour).
# ``full`` carries the per-client rows AND the hosted tool list; ``short``
# is the one-paragraph form for a README bullet; ``llms`` is one llms.txt
# resource line. .well-known/agents.md is a byte copy of AGENTS.md, so it
# is rendered ``full`` and additionally held identical below.
AGENT_SURFACE_MARKDOWN_TARGETS: list[tuple[str, str]] = [
    ("services/keel-site/public/AGENTS.md", "full"),
    ("services/keel-site/public/.well-known/agents.md", "full"),
    ("packages/keel-trade/keel-sdk/AGENTS.md", "short"),
    # The public front door carries the per-client quick starts: it is where
    # an AI assistant sends a reader who has no Keel account yet (Q-1649).
    ("packages/keel-trade/public-overlay/README.md", "clients"),
    ("services/keel-site/public/llms.txt", "llms"),
]

# ── The FULL local tool inventory (Q-1703) ─────────────────────────────
#
# `agent-surface` renders the HOSTED profile (LISTED_PROFILE_TOOLS). The
# public AGENTS.md also publishes the WHOLE registry — every tool the local
# package registers, by toolset, with the local-only ones marked — and until
# 2026-09-22 that section was typed by hand. It drifted: five tools
# (`keel_accounts_safety`, `keel_deployments_list`, `keel_live_quality`,
# `keel_live_receipt`, `keel_live_update`) had been registered for months and
# were named nowhere in it, and two write tools were filed under "read-only".
# The omission arm below could not see any of it — it only asks whether the
# HOSTED names are present. So the section is generated from the registry
# like every other published tool list.
TOOL_INVENTORY_BEGIN_MARKER = "<!-- tool-inventory:begin -->"
TOOL_INVENTORY_END_MARKER = "<!-- tool-inventory:end -->"

# Only the public AGENTS.md carries it. `.well-known/agents.md` is a byte
# copy of that file and is synced from it (check_agent_surface below), so it
# must not be patched independently.
TOOL_INVENTORY_TARGETS: list[str] = ["services/keel-site/public/AGENTS.md"]

# Registry order for the inventory: the load order an operator reasons about
# (`always` first, the opt-in `live-write` last). Every toolset a tool can
# carry must appear here — `registry_tools()` refuses to render one it does
# not know rather than dropping it silently.
_INVENTORY_TOOLSET_ORDER: tuple[str, ...] = (
    "always",
    "read-only",
    "backtest",
    "share",
    "live-read",
    "live-write",
    "live",
)

#: Heading + gate sentence per toolset, in `_INVENTORY_TOOLSET_ORDER`.
_INVENTORY_TOOLSET_HEADINGS: dict[str, str] = {
    "always": "Always loaded",
    "read-only": "Read-only and research (`read-only`)",
    "backtest": "Compose, backtest and iterate (`backtest`)",
    "share": "Share (`share`)",
    "live-read": "Live read (`live-read`, loaded by default)",
    "live-write": (
        "Live write (`live-write`, opt-in with "
        "`KEEL_TOOLSETS=always,read-only,backtest,share,live-read,live-write`)"
    ),
    "live": "Live (`live`)",
}

TOOL_INVENTORY_NOTE = (
    "GENERATED from the outcome registry (keel.tools.outcomes.OUTCOMES) — add a tool there,\n"
    "     then run python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write"
)

# Whole-file targets.
AGENT_CARD_TARGET = "services/keel-site/public/.well-known/agent-card.json"
DOCS_SETUP_PAGE_TARGET = "services/keel-site/content/docs/agents/setup.mdx"
DOCS_SETUP_PAGE_MD = "services/keel-site/public/docs/agents/setup.md"
SKILLS_INDEX_TARGET = "services/keel-site/public/.well-known/skills/index.json"
# The briefing file is named by the source (briefing.file) so the middleware,
# the generator and the checker agree on one path.
BRIEFING_TARGET_KEY = "file"
TOOL_REFERENCE_TARGETS: list[tuple[str, bool]] = [
    ("packages/keel-trade/docs/tool-reference.md", False),
    ("services/keel-site/content/docs/sdk/tool-reference.mdx", True),
]
# Site pages that state counts in prose; the numbers arm reads them so a
# typed integer cannot survive there either (Q-1468).
NUMBER_PAGE_CONSUMERS: list[str] = [
    "services/keel-site/src/app/mcp-hyperliquid/page.tsx",
    "services/keel-site/src/app/claude-hyperliquid/page.tsx",
    "services/keel-site/src/app/chatgpt-hyperliquid/page.tsx",
]
TS_MODULE_TARGETS = [
    "shared/agent-surface.generated.ts",
    # keel-app has no @shared alias (shared/README.md: different design
    # language, kept insulated), so it gets a byte-identical copy — the
    # same pattern as the pipeline_engine validator tables.
    "services/keel-app/src/lib/agent-surface.generated.ts",
]

# The copy law, exported for the two TS rendered-text scans (agent-surface
# task 2.6): keel-app's consent-route test and keel-site's agent-pages test
# import this JSON instead of re-typing FORBIDDEN_TEXT_RE. Rendered by
# ``--write`` from the regex lifted out of scan_listing_copy.py (never a
# second copy of the rule) and byte-checked like every other consumer, so a
# scanner change that is not regenerated reds CI here.
COPY_LAW_JSON_TARGET = "shared/copy-law.json"

# Order in which hosted tools are listed everywhere: toolset, then name.
_TOOLSET_ORDER = ("always", "read-only", "backtest", "share", "live-read")

# Client ids in the fixed AS-11 order — the source must list exactly these,
# in this order, so every consumer shows the same strip.
CLIENT_ORDER = (
    "claude",
    "chatgpt",
    "claude-code",
    "cursor",
    "codex",
    "windsurf",
    "vscode",
    "other",
)
# Optional clients (AS-20 #13): rendered on the guide and in the briefing, not
# the hero strip, until research/client-matrix.md has a verified row.
OPTIONAL_CLIENT_IDS = frozenset({"vscode"})

# Every hosted tool belongs to exactly one group (spec 02 R1 delta): the
# briefing and the /agents table render by group. A listed tool without a
# group is an error at generation — a new tool must be placed, never guessed.
TOOL_GROUP_ORDER = ("start", "discover", "build", "test", "read", "share", "help")
TOOL_GROUPS: dict[str, str] = {
    "keel_account_status": "start",
    "keel_connection_check": "start",
    "keel_components_search": "discover",
    "keel_components_get_many": "discover",
    "keel_components_get": "discover",
    "keel_library_list": "discover",
    "keel_library_get": "discover",
    "keel_strategy_compose": "build",
    "keel_strategy_fork": "build",
    # joined the listed profile 2026-09-23 (agent-surface-cleanup spec 03
    # §2.4): a forward commit that deletes nothing.
    "keel_strategy_restore": "build",
    "keel_library_fork": "build",
    "keel_strategy_notes_add": "build",
    "keel_backtest_run": "test",
    "keel_backtest_watch": "test",
    "keel_backtest_summarize": "test",
    # joined the listed profile 2026-09-22 (decision #19): several runs
    # render together through one comparison instead of N summaries.
    "keel_backtest_compare": "test",
    # joined the listed profile 2026-09-23 (Q-1893): one run's positions,
    # a summary by default and one page per asset/window scope.
    "keel_backtest_positions": "test",
    "keel_strategy_search": "read",
    "keel_strategy_get": "read",
    "keel_strategy_history": "read",
    "keel_strategy_diff": "read",
    "keel_strategy_notes_read": "read",
    "keel_strategy_readiness": "read",
    "keel_plan_usage": "read",
    "keel_live_monitor": "read",
    "keel_share_create": "share",
    "keel_app_link": "share",
    "keel_help": "help",
    "keel_feedback": "help",
}

# vendor_doc.checked older than this is a checker WARNING naming the URL —
# the vendor menu paths moved twice in 2026 (Q-1466).
VENDOR_DOC_MAX_AGE_DAYS = 90

# Library entries whose headline metrics are computed into NUMBERS.
LIBRARY_NUMBER_SLUGS = ("funding-carry",)
STRATEGY_LIBRARY_MANIFEST = "libs/strategy_library/data/manifest.json"
COMPONENT_REGISTRY = "packages/keel-trade/keel-sdk/keel/data/registry.json"

GENERATED_NOTE = (
    "GENERATED from shared/agent-surface.json + LISTED_PROFILE_TOOLS — edit there, then run\n"
    "     python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write"
)

# ── The drift gate (spec 02 R3; fixes Q-0997, closes Q-1451) ─────────────
#
# Three arms, all assertions (never written by --write):
#
# 1. COUNT — every consumer's hosted-count statements (generated OR
#    hand-typed) equal len(LISTED_PROFILE_TOOLS), and every consumer carries
#    at least one, so a file the patterns cannot see cannot pass by silence.
# 2. OMISSION — the consumers that ALSO keep a hand-maintained tool list
#    must name every hosted tool OUTSIDE the generated block: Q-0997's
#    defect was AGENTS.md losing five tools while the docs checker flagged
#    only unknown names. Byte-matching the block cannot see that.
# 3. COPY LAW — every published string in the source and every rendered
#    consumer passes the listing copy scan (research/08 rules, imported from
#    scan_listing_copy.py so listing copy, tool copy and agent-surface copy
#    are ONE law). The spec-07 routing TABLE (surface-routing.json) is under
#    the same arm: its rows render into the same files (Q-1463), so a row
#    saying "go live" would ship in AGENTS.md and the PyPI readme however
#    clean agent-surface.json is.
#
# Every phrasing the repo uses for the hosted count, normalised over
# collapsed whitespace with markdown emphasis stripped (Q-1451: a line wrap
# or **bold** hid a wrong number from a correct pattern).
HOSTED_COUNT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?P<count>\d+)-tool(?: listed)? research/backtest/read surface"),
    re.compile(r"research/backtest/read surface \((?P<count>\d+) tools\)"),
    re.compile(r"\b(?P<count>\d+) research/backtest/read tools\b"),
    re.compile(r"\bHosted tools \((?P<count>\d+)\)"),
    re.compile(r'"hosted_tool_count": (?P<count>\d+)\b'),
    re.compile(r"\bTOOL_COUNT = (?P<count>\d+)\b"),
    re.compile(r"\((?P<count>\d+) tools, fail-closed\)"),
]

# mcp-server's per-environment values: MCP_DOCS_URL must be the source's
# setup page (the URL the 401's resource_documentation points a client at)
# and MCP_RESOURCE_URL + the endpoint path must be the source's endpoint for
# that environment (settings.py appends MCP_ENDPOINT_PATH to it).
HELM_VALUES: list[str] = [
    "infrastructure/helm/mcp-server/values-dev.yaml",
    "infrastructure/helm/mcp-server/values-prod.yaml",
]
HELM_ENV_OF: dict[str, str] = {
    "infrastructure/helm/mcp-server/values-dev.yaml": "staging",
    "infrastructure/helm/mcp-server/values-prod.yaml": "hosted",
}
MCP_ENDPOINT_PATH = "/mcp"

# Consumers whose hosted-count statements the gate reads. Every agent-surface
# render target is here; a target with ZERO statements is an error (the gate
# refuses to be vacuous for it).
COUNT_CONSUMERS: list[str] = [
    "services/keel-site/public/AGENTS.md",
    "services/keel-site/public/.well-known/agents.md",
    "packages/keel-trade/keel-sdk/AGENTS.md",
    "packages/keel-trade/public-overlay/README.md",
    "services/keel-site/public/llms.txt",
    AGENT_CARD_TARGET,
    *TS_MODULE_TARGETS,
    DOCS_SETUP_PAGE_TARGET,
    # the briefing states the hosted count once (task 2.4)
    "services/keel-site/public/agent-briefing.md",
    # Q-1451's four: the consumers check_agent_surface_docs.py cannot scan
    # (its DOC_SUFFIXES and validators are for prose files). They state the
    # hosted count in a `$comment`, a docstring and two helm comments; the
    # count arm reads them here, so they are hand-maintained but no longer
    # ungated.
    "shared/surface-routing.json",
    "services/keel-app/src/lib/agent-surface.ts",
    *HELM_VALUES,
]

# Consumers that keep a HAND-MAINTAINED tool list beside the generated block
# (the "Current MCP Tool Names" sections). The omission arm reads them with
# the generated blocks removed.
#
# The two public AGENTS.md files left this list on 2026-09-22 (Q-1703): their
# inventory is now the generated `tool-inventory` block, held byte-exact by
# `_check_markdown_target`, which is a strictly stronger guard than "every
# hosted name appears somewhere" — it catches an EXTRA or MISPLACED tool too,
# and it covers the 19 non-hosted tools the omission arm never looked at. They
# are kept in COUNT_CONSUMERS: the count statements there are still prose.
HAND_TOOL_LIST_CONSUMERS: list[str] = [
    "packages/keel-trade/keel-sdk/AGENTS.md",
]

COPY_SCANNER_PATH = REPO_ROOT / "projects/fable/agent-first-build/submission/scan_listing_copy.py"


# ── The numbers arm (spec 02 R1 delta; Q-1468) ──────────────────────────
#
# Any integer printed before "tool", "skill", "component" or "topic" in a
# consumer must be one of the computed NUMBERS for that noun. A number the
# generator did not compute is a typed number, and typed numbers drift.
NUMBER_NOUNS: dict[str, tuple[str, ...]] = {
    "tool": ("tool_count", "tools_registry", "tools_local_default", "tools_live_write"),
    "skill": ("skills_hosted", "skills_bundled"),
    "component": ("component_count",),
    "topic": ("knowledge_topics", "help_topics"),
}
_NUMBER_BEFORE_NOUN_RE = re.compile(
    r"\b(?P<n>\d+)(?:-| )(?:[A-Za-z/-]+ ){0,3}?(?P<noun>tools?|skills?|components?|topics?)\b",
    re.IGNORECASE,
)


def number_errors(rel: str, text: str, numbers: dict) -> list[str]:
    """Integers before tool/skill/component/topic that are not computed numbers."""
    errors: list[str] = []
    normalized = _normalize_for_count_scan(text)
    for m in _NUMBER_BEFORE_NOUN_RE.finditer(normalized):
        noun = m.group("noun").lower().rstrip("s")
        allowed = {numbers[k] for k in NUMBER_NOUNS[noun]}
        value = int(m.group("n"))
        if value not in allowed:
            errors.append(
                f"{rel}: typed number {value!r} before {noun!r} ({m.group(0)!r}) is not a computed "
                f"NUMBERS value ({sorted(allowed)}); read it from NUMBERS / the generated module"
            )
    return errors


def number_consumers() -> list[str]:
    return [
        *COUNT_CONSUMERS,
        SKILLS_INDEX_TARGET,
        *(rel for rel, _ in TOOL_REFERENCE_TARGETS),
        *TSX_TARGETS,
        *NUMBER_PAGE_CONSUMERS,
    ]


def compute_numbers() -> dict:
    """Every number a consumer may print, computed from the live sources."""
    if str(SDK_ROOT) not in sys.path:
        sys.path.insert(0, str(SDK_ROOT))
    from keel.mcp.server import LISTED_EXCLUDED_SKILLS
    from keel.skills import BUNDLED_SKILLS
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes import help as help_tool
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS, is_tool_loaded

    _bootstrap()
    local_default = frozenset({"always", "read-only", "backtest", "share", "live-read"})
    components = json.loads((REPO_ROOT / COMPONENT_REGISTRY).read_text())["components"]
    if isinstance(components, dict):
        components = list(components.values())
    manifest = json.loads((REPO_ROOT / STRATEGY_LIBRARY_MANIFEST).read_text())
    library: dict[str, dict] = {}
    for entry in manifest["entries"]:
        if entry["slug"] in LIBRARY_NUMBER_SLUGS:
            h = entry["headline"]
            library[entry["slug"]] = {
                "sharpe": round(float(h["sharpe"]), 2),
                "return_pct": round(float(h["total_return_pct"]), 1),
                "max_dd_pct": round(float(h["max_drawdown_pct"]), 1),
                "trade_count": int(h["trades"]),
            }
    missing = sorted(set(LIBRARY_NUMBER_SLUGS) - set(library))
    if missing:
        raise RuntimeError(f"strategy-library manifest has no entry for {missing}")
    knowledge = sorted(p.stem for p in (SDK_ROOT / "keel" / "data" / "knowledge").glob("*.md"))
    if not knowledge:
        raise RuntimeError("keel/data/knowledge is empty — run build_data.py")
    return {
        "tool_count": len(LISTED_PROFILE_TOOLS),
        "tools_registry": len(OUTCOMES),
        "tools_local_default": sum(
            1 for t in OUTCOMES.values() if is_tool_loaded(t.toolset, local_default)
        ),
        "tools_live_write": sum(1 for t in OUTCOMES.values() if t.toolset == "live-write"),
        "skills_hosted": len(set(BUNDLED_SKILLS) - set(LISTED_EXCLUDED_SKILLS)),
        "skills_bundled": len(BUNDLED_SKILLS),
        "knowledge_topics": len(knowledge),
        "help_topics": len(help_tool._list_bundled_topics()),
        "component_count": sum(1 for c in components if c.get("status") == "active"),
        "library": library,
    }


def tool_group(name: str) -> str:
    try:
        return TOOL_GROUPS[name]
    except KeyError:
        raise RuntimeError(
            f"hosted tool {name!r} has no entry in TOOL_GROUPS — place it in one of "
            f"{TOOL_GROUP_ORDER} (check_surface_routing.py)"
        ) from None


def vendor_doc_errors(data: dict) -> list[str]:
    """Every client but `other` must say where its instructions were verified."""
    errors: list[str] = []
    for client in data["clients"]:
        doc = client.get("vendor_doc")
        if client["id"] == "other":
            continue
        if not doc or not doc.get("url") or not doc.get("checked"):
            errors.append(f"shared/agent-surface.json: clients[{client['id']}] has no vendor_doc")
            continue
        try:
            datetime.strptime(doc["checked"], "%Y-%m-%d")
        except ValueError:
            errors.append(
                f"shared/agent-surface.json: clients[{client['id']}] vendor_doc.checked "
                f"{doc['checked']!r} is not YYYY-MM-DD"
            )
    return errors


def vendor_doc_warnings(data: dict, today: date | None = None) -> list[str]:
    """Clients whose vendor_doc.checked is older than VENDOR_DOC_MAX_AGE_DAYS."""
    today = today or date.today()
    warnings: list[str] = []
    for client in data["clients"]:
        for doc in (client.get("vendor_doc"), (client.get("deep_link") or {}).get("vendor_doc")):
            if not doc or not doc.get("checked"):
                continue
            try:
                checked = datetime.strptime(doc["checked"], "%Y-%m-%d").date()
            except ValueError:
                continue
            age = (today - checked).days
            if age > VENDOR_DOC_MAX_AGE_DAYS:
                warnings.append(
                    f"shared/agent-surface.json: clients[{client['id']}] instructions were last "
                    f"checked {age} days ago ({doc['checked']}) against {doc['url']} — re-verify "
                    f"and bump vendor_doc.checked"
                )
    return warnings


def render_text(template: str, data: dict, endpoint: str) -> str:
    """`{{endpoint}}` and `{{handoff}}` substitution (mirror of TS renderSnippet)."""
    return template.replace("{{endpoint}}", endpoint).replace("{{handoff}}", data["handoff"])


def render_deep_link(client: dict, endpoint: str) -> dict | None:
    """The vendor-documented install link for one client, or None.

    Config JSON is compact (no spaces) — `JSON.stringify` on the TS side
    produces the same bytes, so the base64 / URL-encoded forms agree.
    """
    dl = client.get("deep_link")
    if not dl:
        return None
    config = json.loads(json.dumps(dl["config"]).replace("{{endpoint}}", endpoint))
    compact = json.dumps(config, separators=(",", ":"))
    fills = {
        "{{config_b64}}": base64.b64encode(compact.encode()).decode(),
        "{{config_urlencoded}}": urllib.parse.quote(compact, safe=""),
    }

    def fill(template: str) -> str:
        for key, value in fills.items():
            template = template.replace(key, value)
        return template

    return {"url": fill(dl["template"]), "web": fill(dl["web"]) if dl.get("web") else None}


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_skills_index() -> str:
    """The whole skills manifest — keel.skills + this source (one renderer)."""
    return _load_sibling("build_skills_manifest").render_manifest()


def render_tool_reference(*, mdx: bool) -> str:
    return _load_sibling("generate_agent_reference_docs").render_reference(mdx=mdx)


def _normalize_for_count_scan(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("*", ""))


def count_statements(text: str) -> list[int]:
    """Every hosted-count number stated in ``text`` (any phrasing)."""
    normalized = _normalize_for_count_scan(text)
    found: list[int] = []
    for pattern in HOSTED_COUNT_PATTERNS:
        found.extend(int(m.group("count")) for m in pattern.finditer(normalized))
    return found


def count_errors(rel: str, text: str, expected: int) -> list[str]:
    counts = count_statements(text)
    if not counts:
        return [
            f"{rel}: no hosted tool-count statement found — the count gate cannot see this "
            "consumer (add the phrasing to HOSTED_COUNT_PATTERNS or the count to the file)"
        ]
    return [
        f"{rel}: stale hosted tool count {actual}; expected {expected}"
        for actual in counts
        if actual != expected
    ]


def omission_errors(rel: str, text: str, tool_names: list[str]) -> list[str]:
    """Hosted tools a hand-maintained list has lost (generated blocks excluded)."""
    hand_text = _TOOL_INVENTORY_BLOCK_RE.sub("", _AS_BLOCK_RE.sub("", text))
    present = set(re.findall(r"\bkeel_[a-z0-9_]+\b", hand_text))
    missing = sorted(set(tool_names) - present)
    if not missing:
        return []
    return [
        f"{rel}: hand-maintained tool list omits hosted tool(s) outside the generated block: "
        + ", ".join(missing)
    ]


def load_forbidden_text_re() -> re.Pattern[str]:
    """The listing copy law, read from scan_listing_copy.py itself.

    The scanner imports PyYAML at module import (it scans YAML packets),
    which the SDK test environment does not carry, so the ONE regex is
    lifted from its source by AST rather than by import — no second copy of
    the rule, and a renamed or missing scanner fails loudly.
    """
    if not COPY_SCANNER_PATH.exists():
        raise FileNotFoundError(
            f"listing copy scanner missing at {COPY_SCANNER_PATH.relative_to(REPO_ROOT)}"
        )
    tree = ast.parse(COPY_SCANNER_PATH.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FORBIDDEN_TEXT_RE" for t in node.targets
        ):
            compiled = eval(  # noqa: S307 — a re.compile(...) literal from a repo file
                compile(ast.Expression(node.value), str(COPY_SCANNER_PATH), "eval"),
                {"re": re},
            )
            if not isinstance(compiled, re.Pattern):
                raise TypeError("FORBIDDEN_TEXT_RE in scan_listing_copy.py is not a compiled regex")
            return compiled
    raise LookupError("FORBIDDEN_TEXT_RE not found in scan_listing_copy.py")


def _iter_published_strings(node, path=""):
    """Every string leaf of the source except ``$``-prefixed comment keys."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if str(k).startswith("$"):
                continue
            yield from _iter_published_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_published_strings(v, f"{path}[{i}]")


# Product identifiers the copy law must not read as prose. `keel-trade` is
# the PyPI/GitHub/MCP-registry name and appears in every install command;
# its hyphen is a word boundary, so `\btrade\b` matches it. The policy scan's
# own scope note exempts identifiers because underscores are word
# characters — the hyphenated name gets the same treatment, explicitly and
# only for these exact tokens. Prose "trade" still reds.
# `funding-carry` is a library slug (NUMBERS.library key) and `deploy-and-monitor`
# a bundled skill NAME (skills/index.json hosted.excluded) — identifiers, not prose,
# and the exemption is exactly these tokens.
COPY_IDENTIFIER_TOKENS: tuple[str, ...] = ("keel-trade", "funding-carry", "deploy-and-monitor")


def _mask_identifiers(text: str) -> str:
    for token in COPY_IDENTIFIER_TOKENS:
        text = text.replace(token, token.replace("-", "_"))
    return text


# Python-only regex syntax that would compile to something else (or not at
# all) as a JS RegExp. The rule is written in the ASCII subset both engines
# share; the renderer refuses rather than exporting a pattern that means a
# different thing in the browser test.
_PY_ONLY_REGEX_SYNTAX = ("(?P<", "(?P=", "(?i)", "(?#", "\\A", "\\Z", "(?x)", "(?s)", "(?m)")


def render_copy_law_json(forbidden: re.Pattern[str] | None = None) -> str:
    """``shared/copy-law.json`` — the one copy-law regex, in a form a JS
    ``new RegExp(pattern, js_flags)`` reads identically to Python's ``re``."""
    forbidden = load_forbidden_text_re() if forbidden is None else forbidden
    if forbidden.flags & ~re.UNICODE != re.IGNORECASE:
        raise ValueError(
            f"FORBIDDEN_TEXT_RE flags {forbidden.flags!r} are not exactly IGNORECASE; "
            "the JS flag mapping below only knows IGNORECASE -> 'i'"
        )
    bad = [tok for tok in _PY_ONLY_REGEX_SYNTAX if tok in forbidden.pattern]
    if bad:
        raise ValueError(f"FORBIDDEN_TEXT_RE uses Python-only regex syntax {bad}; not exportable")
    doc = {
        "$comment": (
            "GENERATED by packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write "
            "from FORBIDDEN_TEXT_RE in projects/fable/agent-first-build/submission/"
            "scan_listing_copy.py (the listing copy law, research/08). DO NOT EDIT - edit the "
            "scanner and regenerate; tests/test_surface_routing.py fails CI when this drifts. "
            "Consumers: keel-app consent-copy-law.unit.test.tsx, keel-site "
            "agent-pages-copy-law.unit.test.tsx."
        ),
        "source": str(COPY_SCANNER_PATH.relative_to(REPO_ROOT)),
        "name": "FORBIDDEN_TEXT_RE",
        "pattern": forbidden.pattern,
        "python_flags": ["IGNORECASE"],
        "js_flags": "i",
        "identifier_tokens": list(COPY_IDENTIFIER_TOKENS),
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def copy_errors(label: str, texts: list[tuple[str, str]], forbidden: re.Pattern[str]) -> list[str]:
    errors: list[str] = []
    for field, text in texts:
        hits = sorted({m.group(0).lower() for m in forbidden.finditer(_mask_identifiers(text))})
        if hits:
            errors.append(f"{label}: {field}: forbidden term(s) {hits} (listing copy law)")
    return errors


def routing_table_copy_errors(routing: dict, forbidden: re.Pattern[str]) -> list[str]:
    """The copy law over the spec-07 routing table: its published strings
    (the rows; ``$``-comments excluded) and the markdown block they render to.
    Q-1463: the rows were written before the copy law existed and carried
    "Going live" / "go live" into every agent file while the gate scanned
    only agent-surface.json."""
    errors = copy_errors(
        "shared/surface-routing.json", list(_iter_published_strings(routing)), forbidden
    )
    errors.extend(copy_errors("rendered", [("block:routing", render_block(routing))], forbidden))
    return errors


def gated_paths() -> list[str]:
    """Every repo-relative path this checker reads — the set a CI trigger
    must cover for the gate to run when a consumer changes (Q-1457)."""
    paths = [
        str(CANONICAL_PATH.relative_to(REPO_ROOT)),
        str(AGENT_SURFACE_PATH.relative_to(REPO_ROOT)),
        str(COPY_SCANNER_PATH.relative_to(REPO_ROOT)),
        *MARKDOWN_TARGETS,
        *TSX_TARGETS,
        KEEL_APP_ROUTING_TABLE,
        *(rel for rel, _ in AGENT_SURFACE_MARKDOWN_TARGETS),
        AGENT_CARD_TARGET,
        *TS_MODULE_TARGETS,
        COPY_LAW_JSON_TARGET,
        *COUNT_CONSUMERS,
        *HAND_TOOL_LIST_CONSUMERS,
        # the docs .md variants are derived from content/docs/**.mdx
        DOCS_SETUP_PAGE_TARGET,
        DOCS_SETUP_PAGE_MD,
        SKILLS_INDEX_TARGET,
        *(rel for rel, _ in TOOL_REFERENCE_TARGETS),
        *NUMBER_PAGE_CONSUMERS,
        briefing_target(load_agent_surface()),
        STRATEGY_LIBRARY_MANIFEST,
        COMPONENT_REGISTRY,
        DOCS_MD_ROOT_FILE,
    ]
    return sorted(set(paths))


def workflow_glob_matches(pattern: str, path: str) -> bool:
    """GitHub Actions path-filter semantics: ``**`` spans segments, ``*`` and
    ``?`` stay inside one segment; a pattern with no ``/`` matches any depth."""
    regex = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            regex += ".*"
            i += 2
            continue
        if c == "*":
            regex += "[^/]*"
        elif c == "?":
            regex += "[^/]"
        else:
            regex += re.escape(c)
        i += 1
    return re.fullmatch(regex, path) is not None


def paths_uncovered(patterns: list[str], paths: list[str]) -> list[str]:
    return [p for p in paths if not any(workflow_glob_matches(pat, p) for pat in patterns)]


_HELM_ENV_RE = re.compile(
    r"-\s*name:\s*(?P<name>MCP_DOCS_URL|MCP_RESOURCE_URL)\n(?:[ \t]*#[^\n]*\n)*[ \t]*value:\s*\"(?P<value>[^\"]+)\""
)


def helm_values_env(text: str) -> dict[str, str]:
    """``{MCP_DOCS_URL: …, MCP_RESOURCE_URL: …}`` read from a values file (no YAML dep)."""
    return {m.group("name"): m.group("value") for m in _HELM_ENV_RE.finditer(text)}


def helm_errors(rel: str, text: str, data: dict) -> list[str]:
    env = helm_values_env(text)
    errors: list[str] = []
    for key in ("MCP_DOCS_URL", "MCP_RESOURCE_URL"):
        if key not in env:
            errors.append(f"{rel}: {key} not found — the helm gate cannot see this file")
    if errors:
        return errors
    if env["MCP_DOCS_URL"] != data["setup_page_url"]:
        errors.append(
            f"{rel}: MCP_DOCS_URL is {env['MCP_DOCS_URL']!r}; "
            f"shared/agent-surface.json setup_page_url is {data['setup_page_url']!r}"
        )
    which = HELM_ENV_OF[rel]
    want = data["endpoint"][which]
    if env["MCP_RESOURCE_URL"] + MCP_ENDPOINT_PATH != want:
        errors.append(
            f"{rel}: MCP_RESOURCE_URL {env['MCP_RESOURCE_URL']!r} + {MCP_ENDPOINT_PATH!r} != "
            f"shared/agent-surface.json endpoint.{which} {want!r}"
        )
    return errors


def check_agent_surface_gate() -> list[str]:
    """The three assertion arms over every consumer; empty list = clean."""
    errors: list[str] = []
    data = load_agent_surface()
    tools = listed_tools()
    expected = len(tools)
    names = [t["name"] for t in tools]
    numbers = compute_numbers()

    for rel in number_consumers():
        path = REPO_ROOT / rel
        if not path.exists():
            errors.append(f"{rel}: missing numbers consumer")
            continue
        errors.extend(number_errors(rel, path.read_text(), numbers))

    errors.extend(vendor_doc_errors(data))
    for client in data["clients"]:
        if bool(client.get("optional")) != (client["id"] in OPTIONAL_CLIENT_IDS):
            errors.append(
                f"shared/agent-surface.json: clients[{client['id']}].optional must be "
                f"{client['id'] in OPTIONAL_CLIENT_IDS} (OPTIONAL_CLIENT_IDS)"
            )

    for rel in COUNT_CONSUMERS:
        path = REPO_ROOT / rel
        if not path.exists():
            errors.append(f"{rel}: missing count consumer")
            continue
        errors.extend(count_errors(rel, path.read_text(), expected))

    for rel in HAND_TOOL_LIST_CONSUMERS:
        path = REPO_ROOT / rel
        if not path.exists():
            continue  # already reported above
        errors.extend(omission_errors(rel, path.read_text(), names))

    for rel in HELM_VALUES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue  # already reported above
        errors.extend(helm_errors(rel, path.read_text(), data))

    forbidden = load_forbidden_text_re()
    errors.extend(
        copy_errors("shared/agent-surface.json", list(_iter_published_strings(data)), forbidden)
    )
    errors.extend(routing_table_copy_errors(load_canonical(), forbidden))
    rendered: list[tuple[str, str]] = [
        (f"block:{kind}", render_agent_surface_block(data, kind, tools))
        for kind in ("full", "clients", "short", "llms")
    ]
    rendered.append(("agent-card.json", render_agent_card(data, tools)))
    rendered.append(("agent-surface.generated.ts", render_ts_module(data, tools)))
    rendered.append(("docs/agents/setup.mdx", render_docs_setup_page(data, tools)))
    rendered.append(("agent-briefing.md", render_briefing(data, tools)))
    skills_index = json.loads(render_skills_index())
    rendered.extend(
        (f"skills/index.json:{field}", text)
        for field, text in _iter_published_strings(
            {k: skills_index[k] for k in ("description", "install", "hosted")}
        )
    )
    errors.extend(copy_errors("rendered", rendered, forbidden))
    return errors


def load_canonical() -> dict:
    return json.loads(CANONICAL_PATH.read_text())


def _approvals_line(data: dict, tools: list[dict]) -> str:
    """The approvals sentence (Q-1506): the source's host how-to with the
    write-tool list COMPUTED from the registry's readOnlyHint, so the
    sentence names exactly the tools a host prompts for and cannot drift
    from the annotations the hosts read."""
    writes = ", ".join(f"`{t['name']}`" for t in tools if not t["read_only"])
    template = data.get("approvals")
    if not template:
        raise ValueError("shared/agent-surface.json has no `approvals` sentence (Q-1506)")
    return _mdx_text(template.replace("{{write_tools}}", writes))


def load_agent_surface() -> dict:
    data = json.loads(AGENT_SURFACE_PATH.read_text())
    if "briefing" not in data:
        raise ValueError("shared/agent-surface.json has no `briefing` block (spec 02 R4)")
    ids = tuple(c["id"] for c in data["clients"])
    if ids != CLIENT_ORDER:
        raise ValueError(
            f"shared/agent-surface.json clients must be exactly {CLIENT_ORDER} in that "
            f"order (AS-11); found {ids}"
        )
    return data


def _one_line(text: str) -> str:
    """First sentence of a tool description (mirror of generate_agent_reference_docs)."""
    compact = " ".join(text.split())
    first = compact.split(". Do NOT use", 1)[0]
    first = first.split(". ", 1)[0]
    return first.rstrip(".")


def listed_tools() -> list[dict]:
    """The hosted surface, read from the registry — never typed.

    Returns ``[{name, toolset, description}]`` in toolset-then-name order.
    The description is the listed-profile copy when a tool carries one
    (the policy-scanned text a directory reviewer sees), else the shared
    description; first sentence only.
    """
    if str(SDK_ROOT) not in sys.path:
        sys.path.insert(0, str(SDK_ROOT))
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    _bootstrap()
    missing = sorted(LISTED_PROFILE_TOOLS - set(OUTCOMES))
    if missing:
        raise RuntimeError(f"LISTED_PROFILE_TOOLS names tools absent from the registry: {missing}")
    tools = []
    for name in LISTED_PROFILE_TOOLS:
        tool = OUTCOMES[name]
        title = tool.listed_title or tool.annotations.get("title")
        if not title:
            raise RuntimeError(f"hosted tool {name!r} carries no title annotation")
        tools.append(
            {
                "name": name,
                "title": title,
                "toolset": tool.toolset,
                "group": tool_group(name),
                "read_only": bool(tool.annotations.get("readOnlyHint", False)),
                "description": _one_line(tool.listed_description or tool.description),
            }
        )
    tools.sort(key=lambda t: (_TOOLSET_ORDER.index(t["toolset"]), t["name"]))
    return tools


def registry_tools() -> list[dict]:
    """EVERY registered outcome tool, read from the registry — never typed.

    Returns ``[{name, toolset, local_only, hosted}]`` in
    ``_INVENTORY_TOOLSET_ORDER`` then name order. ``hosted`` is membership of
    ``LISTED_PROFILE_TOOLS`` (the hosted endpoint's profile IS that set), and
    ``local_only`` is the tool's own flag — the one `is_tool_loaded` consults
    to keep filesystem/browser-bound tools off every hosted endpoint.
    """
    if str(SDK_ROOT) not in sys.path:
        sys.path.insert(0, str(SDK_ROOT))
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    _bootstrap()
    if not OUTCOMES:
        raise RuntimeError("the outcome registry is empty — _bootstrap() registered nothing")
    unknown = sorted({t.toolset for t in OUTCOMES.values()} - set(_INVENTORY_TOOLSET_ORDER))
    if unknown:
        raise RuntimeError(
            f"toolset(s) {unknown} have no place in _INVENTORY_TOOLSET_ORDER — "
            "a new toolset must be placed in check_surface_routing.py, never dropped"
        )
    tools = [
        {
            "name": name,
            "toolset": tool.toolset,
            "local_only": bool(tool.local_only),
            "hosted": name in LISTED_PROFILE_TOOLS,
        }
        for name, tool in OUTCOMES.items()
    ]
    tools.sort(key=lambda t: (_INVENTORY_TOOLSET_ORDER.index(t["toolset"]), t["name"]))
    return tools


def render_tool_inventory_block(tools: list[dict] | None = None) -> str:
    """The generated full-registry inventory block, markers included.

    Non-vacuity, asserted at render: the block emits exactly one bullet per
    registered tool. A renderer that silently dropped a toolset would still
    produce well-formed markdown, and the byte-check downstream would happily
    pin the short version — which is the failure this block exists to end.
    """
    tools = registry_tools() if tools is None else tools
    lines = [
        "Every `keel_*` MCP tool the `keel-trade` package registers, by toolset. "
        "`hosted` marks the tools the hosted endpoint also exposes; `local only` "
        "marks the ones bound to the user's filesystem or browser, which no hosted "
        "endpoint registers. `KEEL_TOOLSETS` selects which toolsets load locally.",
    ]
    emitted = 0
    for toolset in _INVENTORY_TOOLSET_ORDER:
        group = [t for t in tools if t["toolset"] == toolset]
        if not group:
            continue
        lines += ["", f"{_INVENTORY_TOOLSET_HEADINGS[toolset]}:", ""]
        for tool in group:
            if tool["local_only"]:
                mark = " (local only)"
            elif tool["hosted"]:
                mark = " (hosted)"
            else:
                mark = ""
            lines.append(f"- `{tool['name']}`{mark}")
            emitted += 1
    if emitted != len(tools):
        raise RuntimeError(
            f"tool inventory rendered {emitted} of {len(tools)} registered tools — "
            "a toolset was dropped between registry_tools() and the render"
        )
    lines += [
        "",
        "Do not invent wrapper or meta-tool names: call the `keel_*` tools your "
        "active `tools/list` returns.",
    ]
    head = f"{TOOL_INVENTORY_BEGIN_MARKER}\n<!-- {TOOL_INVENTORY_NOTE} -->\n"
    return f"{head}{chr(10).join(lines)}\n{TOOL_INVENTORY_END_MARKER}"


def render_snippet(template: str, endpoint: str) -> str:
    return template.replace("{{endpoint}}", endpoint)


def _compact_json(text: str) -> str:
    return json.dumps(json.loads(text), separators=(", ", ": "))


def _client_one_liner(client: dict, endpoint: str) -> str:
    """One markdown bullet per client: the exact thing to do."""
    snippet = render_snippet(client["snippet"], endpoint)
    steps = [render_snippet(step, endpoint) for step in client["steps"]]
    kind = client["kind"]
    if kind == "command":
        cmds = " then ".join(f"`{line}`" for line in snippet.splitlines())
        return f"- **{client['name']}**: {cmds}. {steps[1]}."
    if kind == "config_file":
        return f"- **{client['name']}**: {steps[0]}: `{_compact_json(snippet)}`. {steps[1]}."
    if kind == "directory":
        return f"- **{client['name']}**: Connect from the directory: {client['directory_url']}."
    # paste_url
    return f"- **{client['name']}**: {steps[0]}. {steps[1]}."


def render_agent_surface_block(data: dict, kind: str, tools: list[dict] | None = None) -> str:
    """The generated agent-surface block for one markdown flavour, markers included."""
    tools = listed_tools() if tools is None else tools
    endpoint = data["endpoint"]["hosted"]
    count = len(tools)
    claude_code = next(c for c in data["clients"] if c["id"] == "claude-code")
    codex = next(c for c in data["clients"] if c["id"] == "codex")
    cc_cmd = render_snippet(claude_code["snippet"], endpoint)
    codex_cmd = render_snippet(codex["snippet"], endpoint).splitlines()[0]
    paste_clients = [
        c["name"] for c in data["clients"] if c["kind"] == "paste_url" and c["id"] != "other"
    ]
    handoff = data["handoff"]
    head = f"{AS_BEGIN_MARKER}\n<!-- {GENERATED_NOTE} -->\n"
    if kind == "full":
        lines = [
            f"Hosted endpoint URL: `{endpoint}` — one endpoint, a {count}-tool "
            "research/backtest/read surface, nothing to install. Sign in or sign up "
            "on the Keel page that opens (no account needed beforehand); "
            "authentication is your MCP client's OAuth flow, never a tool call.",
            "",
        ]
        lines.extend(_client_one_liner(c, endpoint) for c in data["clients"])
        lines += [
            "",
            handoff,
            "",
            f"Hosted tools ({count}):",
            "",
        ]
        lines.extend(f"- `{t['name']}` — {t['description']}." for t in tools)
        lines += [
            "",
            f"Setup guide: {data['setup_page_url']} · Per-client runbook: {data['agents_page_url']}",
        ]
        body = "\n".join(lines)
    elif kind == "clients":
        # `full` minus the tool dump: the per-client quick starts belong on a
        # front door, a 26-line tool list does not (the README links the tool
        # reference instead). Q-1649: 7 of 35 signups in the 30 days to
        # 2026-09-20 arrived from an AI assistant or this repo's README, and
        # the one measured arrival never saw a connect surface.
        lines = [
            f"Hosted endpoint URL: `{endpoint}` — one endpoint, a {count}-tool "
            "research/backtest/read surface, nothing to install. Sign in or sign up "
            "on the Keel page that opens (no account needed beforehand); "
            "authentication is your MCP client's OAuth flow, never a tool call.",
            "",
        ]
        lines.extend(_client_one_liner(c, endpoint) for c in data["clients"])
        lines += [
            "",
            handoff,
            "",
            f"Setup guide: {data['setup_page_url']} · Per-client runbook: {data['agents_page_url']}",
        ]
        body = "\n".join(lines)
    elif kind == "short":
        body = (
            f"- **Hosted endpoint (default)** — `{endpoint}`: one endpoint, a {count}-tool "
            "research/backtest/read surface, nothing to install. Paste the URL into "
            f"{', '.join(paste_clients)} or any remote-MCP client and sign in or sign up when prompted; "
            f"Claude Code: `{cc_cmd}`; Codex: `{codex_cmd}`. {handoff} "
            f"Per-client steps: {data['agents_page_url']}"
        )
    elif kind == "llms":
        body = (
            f"- [Hosted MCP endpoint]({endpoint}): The one hosted Keel MCP endpoint and the "
            "default way to connect an agent — paste the URL into "
            f"{', '.join(paste_clients)}, Cursor, Windsurf or any remote-MCP client and sign in or "
            f"sign up when prompted (Claude Code: `{cc_cmd}`; Codex: `{codex_cmd}`). "
            f"{count} research/backtest/read tools, nothing to install. {handoff}"
        )
    else:
        raise ValueError(f"unknown agent-surface block kind {kind!r}")
    return f"{head}{body}\n{AS_END_MARKER}"


def render_agent_card(data: dict, tools: list[dict] | None = None) -> str:
    """The whole ``.well-known/agent-card.json``, from the source + registry."""
    tools = listed_tools() if tools is None else tools
    card = data["agent_card"]
    other = data["other_ways"]
    doc = {
        "name": card["name"],
        "product": card["product"],
        "url": card["url"],
        "description": card["description"],
        "envelope": card["envelope"],
        "endpoints": {
            "website": card["endpoints"]["website"],
            "docs": data["docs_url"],
            "agent_instructions": data["briefing_url"],
            "agents_page": data["agents_page_url"],
            "llms_txt": card["endpoints"]["llms_txt"],
            "pricing": card["endpoints"]["pricing"],
            "skills": card["endpoints"]["skills"],
            "mcp": {
                "hosted_url": data["endpoint"]["hosted"],
                "hosted_transport": "streamable-http",
                "hosted_auth": "oauth2",
                "hosted_setup": data["setup_page_url"],
                "hosted_tool_count": len(tools),
                "hosted_tools": [t["name"] for t in tools],
                "handoff": data["handoff"],
                "transport": "stdio",
                "install": other["cli"]["install"],
                "command": other["local_mcp"]["command"],
                "registry": card["provenance"]["mcp_registry"],
                "claude_desktop_bundle": other["mcpb"]["url"],
            },
        },
        "provenance": card["provenance"],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _published(node):
    """A copy of a source node with `$`-prefixed comment keys removed."""
    if isinstance(node, dict):
        return {k: _published(v) for k, v in node.items() if not str(k).startswith("$")}
    if isinstance(node, list):
        return [_published(v) for v in node]
    return node


def _ts(value) -> str:
    """A JSON literal is a valid TypeScript literal."""
    return json.dumps(value, indent=2, ensure_ascii=False)


def render_ts_module(data: dict, tools: list[dict] | None = None) -> str:
    """``shared/agent-surface.generated.ts`` — typed, importable, never hand-edited."""
    tools = listed_tools() if tools is None else tools
    ask = data["ask_prompts"]
    numbers = compute_numbers()
    hosted_deep_links = {
        c["id"]: render_deep_link(c, data["endpoint"]["hosted"])
        for c in data["clients"]
        if c.get("deep_link")
    }
    lines = [
        "// GENERATED by packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write",
        "// from shared/agent-surface.json + LISTED_PROFILE_TOOLS (the outcome registry).",
        "// DO NOT EDIT — edit the source and regenerate. Byte-identical copies:",
        "//   shared/agent-surface.generated.ts",
        "//   services/keel-app/src/lib/agent-surface.generated.ts",
        "// The drift gate (tests/test_surface_routing.py) fails CI when either drifts.",
        "",
        'export type AgentClientKind = "directory" | "paste_url" | "command" | "config_file";',
        'export type SnippetLanguage = "text" | "bash" | "json";',
        "",
        "export interface AgentClientSupports {",
        "  /** null = not yet verified against the client (research/client-matrix.md). */",
        "  readonly tools: boolean | null;",
        "  readonly resources: boolean | null;",
        "  readonly prompts: boolean | null;",
        "  readonly instructions: boolean | null;",
        "}",
        "",
        "export interface AgentClientVerified {",
        "  readonly version: string;",
        "  readonly date: string;",
        "}",
        "",
        "/** Where the client's menu path / command form was verified, and when (Q-1466). */",
        "export interface AgentClientVendorDoc {",
        "  readonly url: string;",
        "  readonly checked: string;",
        "}",
        "",
        "export interface AgentClient {",
        "  readonly id: string;",
        "  readonly name: string;",
        "  readonly vendor: string | null;",
        "  readonly kind: AgentClientKind;",
        "  /** Template: substitute `{{endpoint}}` with renderSnippet()/connectSnippet(). */",
        "  readonly snippet: string;",
        "  readonly snippet_language: SnippetLanguage;",
        "  /** Short ordered steps; may also carry `{{endpoint}}`. */",
        "  readonly steps: readonly string[];",
        "  readonly deep_link: AgentClientDeepLink | null;",
        "  /** Set once the client's directory listing is approved (task 6.4). */",
        "  readonly directory_url: string | null;",
        "  readonly supports: AgentClientSupports;",
        "  readonly verified: AgentClientVerified | null;",
        "  readonly vendor_doc: AgentClientVendorDoc | null;",
        "  /** Where the client runs: web | desktop | mobile | terminal | ide. */",
        "  readonly surfaces: readonly string[];",
        "  /** The vendor's menu path, verbatim; null for config-file clients. */",
        "  readonly ui_path: string | null;",
        "  /** What the client shows when it wants a sign-in. */",
        "  readonly login: string;",
        "  readonly config_path: string | null;",
        "  readonly config_alt: string | null;",
        "  readonly first_prompt: string;",
        "  readonly starter_prompts: readonly string[];",
        "  /** The third pair is the AS-14 handoff (`{{handoff}}`, filled by renderSnippet). */",
        "  readonly troubleshooting: readonly AgentClientTroubleshooting[];",
        "  /** Guide + briefing only, not the hero strip (AS-20 #13). */",
        "  readonly optional: boolean;",
        "}",
        "",
        "export interface AgentBriefing {",
        "  /** The public markdown twin every agent route links as rel=alternate. */",
        "  readonly path: string;",
        "  readonly file: string;",
        "  readonly routes: readonly string[];",
        "  /** Matched FIRST (Accept: text/markdown). */",
        "  readonly accept: readonly string[];",
        "  /** Secondary match: pure fetchers only, never Claude-User / ChatGPT-User. */",
        "  readonly user_agents: readonly string[];",
        "}",
        "",
        "export interface LibraryNumbers {",
        "  readonly sharpe: number;",
        "  readonly return_pct: number;",
        "  readonly max_dd_pct: number;",
        "  readonly trade_count: number;",
        "}",
        "",
        "/** The ONLY integers a consumer may print before tool / skill / component / topic (Q-1468). */",
        "export interface AgentSurfaceNumbers {",
        "  readonly tool_count: number;",
        "  readonly tools_registry: number;",
        "  readonly tools_local_default: number;",
        "  readonly tools_live_write: number;",
        "  readonly skills_hosted: number;",
        "  readonly skills_bundled: number;",
        "  readonly knowledge_topics: number;",
        "  readonly help_topics: number;",
        "  readonly component_count: number;",
        "  readonly library: Readonly<Record<string, LibraryNumbers>>;",
        "}",
        "",
        "export type ToolGroup = " + " | ".join(_ts(g) for g in TOOL_GROUP_ORDER) + ";",
        "",
        "export interface AgentSurfaceTool {",
        "  readonly name: string;",
        "  readonly title: string;",
        "  readonly toolset: string;",
        "  readonly group: ToolGroup;",
        "  readonly read_only: boolean;",
        "  readonly description: string;",
        "}",
        "",
        "export interface AgentClientDeepLink {",
        "  /** Vendor-documented install link; `{{config_b64}}` / `{{config_urlencoded}}` are filled by deepLink(). */",
        "  readonly template: string;",
        "  readonly web: string | null;",
        "  readonly config: Readonly<Record<string, string>>;",
        "  readonly vendor_doc: AgentClientVendorDoc;",
        "}",
        "",
        "export interface AgentClientTroubleshooting {",
        "  readonly issue: string;",
        "  readonly solution: string;",
        "}",
        "",
        f"export const HOSTED_ENDPOINT = {_ts(data['endpoint']['hosted'])};",
        f"export const STAGING_ENDPOINT = {_ts(data['endpoint']['staging'])};",
        f"export const DOCS_URL = {_ts(data['docs_url'])};",
        f"export const SETUP_PAGE_URL = {_ts(data['setup_page_url'])};",
        f"export const AGENTS_PAGE_URL = {_ts(data['agents_page_url'])};",
        f"export const BRIEFING_URL = {_ts(data['briefing_url'])};",
        "",
        "/** The one sentence about live, stated positively (AS-14). */",
        f"export const HANDOFF = {_ts(data['handoff'])};",
        "",
        "/** len(LISTED_PROFILE_TOOLS) at generation time — never type this number. */",
        f"export const TOOL_COUNT = {len(tools)};",
        "",
        "/** Computed at generation from the registry, the skills, the knowledge dir and the library manifest. */",
        f"export const NUMBERS: AgentSurfaceNumbers = {_ts(numbers)};",
        f"export const COMPONENT_COUNT = {numbers['component_count']};",
        "",
        "/** Render order of the tool groups everywhere a consumer groups tools. */",
        f"export const TOOL_GROUP_ORDER: readonly ToolGroup[] = {_ts(list(TOOL_GROUP_ORDER))};",
        "",
        "/** Spec 02 R4 (amended): what the site's middleware keys the markdown briefing on. */",
        f"export const BRIEFING: AgentBriefing = {_ts(_published(data['briefing']))};",
        "",
        f"export const TOOLS: readonly AgentSurfaceTool[] = {_ts(tools)};",
        "",
        "/** Fixed client order (AS-11): Claude, ChatGPT, Claude Code, Cursor, Codex, Windsurf, Other. */",
        f"export const CLIENTS: readonly AgentClient[] = {_ts(_published(data['clients']))};",
        "",
        f"export const OTHER_WAYS = {_ts(data['other_ways'])} as const;",
        "",
        f"export const ASK_PROMPT = {_ts(ask['prompt'])};",
        "",
        "/** The prompt /agents leads with (AS-24): connect here, then a first backtest — no summary, the reader is on the page. */",
        f"export const CONNECT_PROMPT = {_ts(ask['connect_prompt'])};",
        "",
        # Every `<target>_url` key of ask_prompts becomes a template, in the
        # source's order (AS-23: Claude, ChatGPT, Perplexity, Google AI Mode
        # today); the TS union below is derived, never typed.
        "const ASK_URL_TEMPLATES = {",
        *(
            f"  {key[: -len('_url')]}: {_ts(value)},"
            for key, value in ask.items()
            if key.endswith("_url")
        ),
        "} as const;",
        "",
        "/** The ask targets, in the source's order (AS-23). */",
        "export type AskTarget = keyof typeof ASK_URL_TEMPLATES;",
        "export const ASK_TARGETS = Object.keys(ASK_URL_TEMPLATES) as readonly AskTarget[];",
        "",
        "/** Substitute `{{endpoint}}` and `{{handoff}}` in a snippet, step or troubleshooting line. */",
        "export function renderSnippet(template: string, endpoint: string = HOSTED_ENDPOINT): string {",
        '  return template.split("{{endpoint}}").join(endpoint).split("{{handoff}}").join(HANDOFF);',
        "}",
        "",
        "/** Pre-rendered install links for the hosted endpoint (byte-identical to the Python render). */",
        f"export const HOSTED_DEEP_LINKS: Readonly<Record<string, {{ readonly url: string; readonly web: string | null }}>> = {_ts(hosted_deep_links)};",
        "",
        "/** The vendor-documented install link for one client against one endpoint, or null. */",
        "export function deepLink(id: string, endpoint: string = HOSTED_ENDPOINT): { url: string; web: string | null } | null {",
        "  const dl = clientById(id).deep_link;",
        "  if (!dl) return null;",
        '  const compact = JSON.stringify(JSON.parse(JSON.stringify(dl.config).split("{{endpoint}}").join(endpoint)));',
        "  const b64 = btoa(compact);",
        "  const enc = encodeURIComponent(compact);",
        '  const fill = (t: string) => t.split("{{config_b64}}").join(b64).split("{{config_urlencoded}}").join(enc);',
        "  return { url: fill(dl.template), web: dl.web ? fill(dl.web) : null };",
        "}",
        "",
        "/** Clients shown in the hero strip: every non-optional client in AS-11 order. */",
        "export const PRIMARY_CLIENTS: readonly AgentClient[] = CLIENTS.filter((c) => !c.optional);",
        "",
        "export function clientById(id: string): AgentClient {",
        "  const client = CLIENTS.find((c) => c.id === id);",
        "  if (!client) throw new Error(`unknown agent client: ${id}`);",
        "  return client;",
        "}",
        "",
        "/** The rendered connect snippet for one client against one endpoint. */",
        "export function connectSnippet(id: string, endpoint: string = HOSTED_ENDPOINT): string {",
        "  return renderSnippet(clientById(id).snippet, endpoint);",
        "}",
        "",
        '/** "Ask Claude / ChatGPT about Keel" link (spec 03 §5). */',
        "/** Open an assistant with any prompt (the /agents prompt box passes CONNECT_PROMPT). */",
        "export function askUrlWith(which: AskTarget, prompt: string): string {",
        '  return ASK_URL_TEMPLATES[which].split("{{prompt}}").join(encodeURIComponent(prompt));',
        "}",
        "",
        "export function askUrl(which: AskTarget): string {",
        "  return askUrlWith(which, ASK_PROMPT);",
        "}",
        "",
    ]
    return "\n".join(lines)


def _fence(language: str, body: str) -> str:
    return f"```{language}\n{body}\n```"


def _host_link(url: str) -> str:
    """A long URL as a short markdown link labelled by host + first path segment:
    a bare 60-character URL is one unbreakable run of text and widens a 390px
    page (AS-21); the raw .md twin keeps the full URL in the link target."""
    parts = urllib.parse.urlsplit(url)
    first = parts.path.strip("/").split("/")[0]
    label = f"{parts.netloc}/{first}" if first else parts.netloc
    return f"[{label}]({url})"


def _mdx_text(text: str) -> str:
    """Prose-safe for MDX: `<id>` reads as a JSX tag and `{x}` as an expression
    (a registry description carries `usekeel.io/share/<id>`), so both are
    entity-escaped outside code spans. Backtick spans are left alone — MDX
    treats their contents as code."""
    out: list[str] = []
    for i, part in enumerate(text.split("`")):
        if i % 2 == 0:
            part = (
                part.replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("{", "&#123;")
                .replace("}", "&#125;")
            )
        out.append(part)
    return "`".join(out)


# The verify half, shared in WORDING with keel-site/src/lib/agent-verify.ts
# so a user reading the docs page and a user reading the site are told the
# same thing (Q-1570, Q-1574).
VERIFY_TITLE = "Check it worked"
STATUS_TOOL = "keel_account_status"
VERIFY_PROMPT = f"Use {STATUS_TOOL} and tell me which Keel account this session is signed in as."


def _verify_lines(client: dict) -> list[str]:
    """The "did it work?" half of a client's setup block (Q-1570, Q-1574).

    Ported verbatim in behaviour from ``keel-site/src/lib/agent-verify.ts``
    so the docs page and the site cannot disagree about how a connection is
    checked. Both derive from this same source file; neither types a
    per-client string.

    Honesty rules, identical to the site's:

    * the CLI check is DERIVED from the connect snippet and guarded on its
      SHAPE — a `command` client whose snippet manages MCP through an ``mcp``
      subcommand is checked with that CLI's ``mcp list``; any other shape
      yields no command, because inventing one sends a connected user to
      debug something that was never going to work;
    * a client that lists connectors in a menu is pointed at the menu the
      source already names (``ui_path``), never a guessed path;
    * a client with neither says so rather than being handed a command;
    * every client gets the agent check, which is the one that proves the
      connection end to end.
    """
    # The vendorless row is named "Other", which is a label rather than
    # something you can put in a sentence. keel-site's guides call it "your
    # MCP client"; say the same thing here so the two surfaces read alike.
    name = "your MCP client" if client["id"] == "other" else client["name"]
    subject = name[0].upper() + name[1:]
    out: list[str] = ["", f"**{VERIFY_TITLE}**", ""]

    snippet = (client.get("snippet") or "").strip().split()
    if client["kind"] == "command" and len(snippet) >= 2 and snippet[1] == "mcp":
        out.append(f"- In {name}:")
        out.append("")
        out.append(_fence("bash", f"{snippet[0]} mcp list"))
        out.append("")
        out.append(
            "  Keel should be listed there with its sign-in recorded. If it is, you are "
            "connected — signing in again changes nothing."
        )
    elif client.get("ui_path"):
        out.append(
            f"- In {name}: Keel is listed where you added it "
            f"({_mdx_text(client['ui_path'])}) once the sign-in has finished."
        )
    else:
        out.append(
            f"- {subject} does not report the connection separately, so the agent's own "
            "answer is the check."
        )

    out.append(f"- Ask the agent: {_mdx_text(VERIFY_PROMPT)}")
    out.append("")
    out.append(
        f"  `{STATUS_TOOL}` answers with the Keel account the session is signed in as, "
        "which nothing but a live connection can return. If the agent says it has no "
        "Keel tools, the sign-in did not finish."
    )
    return out


def _verified_line(client: dict) -> str:
    """Stated BOTH ways (Q-1570).

    Every client shipped ``verified: null`` and the page rendered nothing for
    it, so "checked against 0.155.1" and "nobody has ever run this" looked
    identical — which is how an unverified snippet reads as verified.
    """
    v = client.get("verified")
    label = "your MCP client" if client["id"] == "other" else client["name"]
    if v:
        return (
            f"Verified against {label} {v['version']} on {v['date']}. "
            "An older version may not complete the sign-in."
        )
    return (
        f"Not verified yet against a {label} version. The steps follow the "
        "vendor's documentation; if the sign-in does not finish, tell us."
    )


def render_docs_setup_page(data: dict, tools: list[dict] | None = None) -> str:
    """The WHOLE docs setup page (``content/docs/agents/setup.mdx``).

    What an agent gets, the endpoint, the per-client steps in AS-11 order,
    the hosted tool list, the app handoff sentence once (AS-14), the other
    ways in. MDX: no bare ``{``/``<`` outside code — every snippet is fenced.
    """
    tools = listed_tools() if tools is None else tools
    endpoint = data["endpoint"]["hosted"]
    count = len(tools)
    other = data["other_ways"]
    paste_clients = [
        c["name"] for c in data["clients"] if c["kind"] == "paste_url" and c["id"] != "other"
    ]
    lines = [
        "---",
        "title: Agent Setup",
        "description: Connect an AI agent to Keel. The hosted MCP endpoint is the default — paste one URL and sign in; the keel-trade package is the other way in.",
        "---",
        "",
        f"{{/* {GENERATED_NOTE.replace(chr(10) + '     ', ' ')} */}}",
        "",
        "Keel runs **one hosted MCP endpoint**:",
        "",
        _fence("text", endpoint),
        "",
        f"Paste it into {', '.join(paste_clients)}, Cursor, Windsurf or any client that takes a "
        "remote MCP server, sign in or sign up on the Keel page that opens (no account "
        "needed beforehand), and "
        f"the agent has Keel: a {count}-tool research/backtest/read surface, nothing to install, "
        "no API key to create. Authentication is your MCP client's OAuth flow, never a tool call.",
        "",
        data["handoff"],
        "",
        "## Connect your client",
        "",
    ]
    for client in data["clients"]:
        lines.append(f"### {client['name']}")
        lines.append("")
        snippet = render_snippet(client["snippet"], endpoint)
        if client["kind"] in ("command", "config_file"):
            lines.append(_fence(client["snippet_language"], snippet))
            lines.append("")
        for i, step in enumerate(client["steps"], 1):
            lines.append(f"{i}. {_mdx_text(render_snippet(step, endpoint))}")
        link = render_deep_link(client, endpoint)
        if link:
            # fenced, not inline: a base64 / URL-encoded link is one unbreakable
            # token, and a <pre> scrolls inside its own box at 390px (AS-21)
            # while an inline code span widens the page
            lines.append("")
            lines.append("Install link (opens the client and registers Keel):")
            lines.append("")
            lines.append(_fence("text", link["url"]))
            if link["web"]:
                lines.append("")
                lines.append("Or in the browser:")
                lines.append("")
                lines.append(_fence("text", link["web"]))
        lines.extend(_verify_lines(client))
        lines.append("")
        lines.append(f"First prompt: {_mdx_text(client['first_prompt'])}")
        lines.append("")
        lines.append(_verified_line(client))
        doc = client.get("vendor_doc")
        if doc:
            lines.append("")
            lines.append(f"Vendor docs: {_host_link(doc['url'])} (checked {doc['checked']})")
        lines.append("")
    lines += [
        "## What the agent gets",
        "",
        f"Hosted tools ({count}):",
        "",
    ]
    # a bullet list, not a table: prettier pads table columns, and this file
    # must stay byte-identical to the render without a .prettierignore entry
    lines.extend(f"- `{t['name']}` — {_mdx_text(t['description'])}." for t in tools)
    lines += [
        "",
        "`tools/list` is authoritative; use the live schemas for exact argument names. "
        "The file-based workspace tools and `keel_auth_login` / `keel_auth_logout` are not "
        "registered on the hosted endpoint — they belong to the package below.",
        "",
        _approvals_line(data, tools),
        "",
        "## Other ways to connect",
        "",
        "### The keel CLI",
        "",
        _fence("bash", f"{other['cli']['install']}\n{other['cli']['login']}"),
        "",
        other["cli"]["note"],
        "",
        "### Local MCP over stdio",
        "",
        other["local_mcp"]["note"],
        "",
        _fence("bash", f"{other['local_mcp']['claude_code']}\n{other['local_mcp']['codex']}"),
        "",
        "Any client that launches MCP servers from a config file:",
        "",
        _fence("json", other["local_mcp"]["config"]),
        "",
        "### Claude Desktop bundle",
        "",
        f"{other['mcpb']['note']} Download: [{other['mcpb']['url'].rsplit('/', 1)[-1]}]({other['mcpb']['url']})",
        "",
        "### API key",
        "",
        f"{other['api_key']['note']} Create a key at {_host_link(other['api_key']['create_url'])}; "
        f"reference: [API reference]({other['api_key']['docs_url']})",
        "",
        "## More",
        "",
        f"- Per-client runbook with expected outputs: {data['agents_page_url']}",
        f"- Machine-readable instructions for agents: {data['briefing_url']}",
        "- [CLI reference](/docs/sdk/cli-reference) · [MCP tool reference](/docs/sdk/tool-reference)",
        "",
    ]
    return "\n".join(lines)


def briefing_target(data: dict) -> str:
    """Repo-relative path of the generated briefing, from the source."""
    rel = data["briefing"][BRIEFING_TARGET_KEY]
    if not rel.startswith("services/keel-site/public/"):
        raise ValueError(f"briefing.file must live under services/keel-site/public/: {rel!r}")
    return rel


def render_briefing(data: dict, tools: list[dict] | None = None) -> str:
    """The markdown briefing an agent gets instead of the HTML (spec 02 R4).

    Second person, Railway's order: what Keel is → how to connect (every
    client, optional ones included — an agent reading this may be running
    in any of them) → what you can do, by tool group → the AS-14 handoff
    sentence once → where the docs are. Plain markdown, no MDX escaping:
    this file is served raw.
    """
    tools = listed_tools() if tools is None else tools
    endpoint = data["endpoint"]["hosted"]
    count = len(tools)
    card = data["agent_card"]
    other = data["other_ways"]
    lines = [
        "# Keel for agents",
        "",
        f"<!-- {GENERATED_NOTE.replace(chr(10) + '     ', ' ')} -->",
        "",
        f"You are an AI agent reading usekeel.io. {card['description']}",
        "",
        "## How to connect",
        "",
        f"The hosted MCP endpoint is `{endpoint}` — one endpoint, a {count}-tool "
        "research/backtest/read surface, nothing to install. Sign in or sign up on the Keel "
        "page that opens (no account needed beforehand); authentication is your MCP client's "
        "OAuth flow, never a tool call.",
        "",
    ]
    lines.extend(_client_one_liner(c, endpoint) for c in data["clients"])
    lines += [
        "",
        f"Other ways in: the `keel` CLI (`{other['cli']['install']}`, then "
        f"`{other['cli']['login']}`), the same package as a local stdio MCP server "
        f"(`{other['local_mcp']['command']}`), the Claude Desktop bundle "
        f"({other['mcpb']['url']}), or the REST API with an API key "
        f"({other['api_key']['docs_url']}).",
        "",
        "## What you can do",
        "",
    ]
    by_group: dict[str, list[dict]] = {g: [] for g in TOOL_GROUP_ORDER}
    for tool in tools:
        by_group[tool["group"]].append(tool)
    for group in TOOL_GROUP_ORDER:
        members = by_group[group]
        if not members:
            continue
        lines.append(f"### {group.capitalize()}")
        lines.append("")
        lines.extend(
            f"- `{t['name']}` — {t['title']}: {t['description']}."
            + (" Read-only." if t["read_only"] else "")
            for t in members
        )
        lines.append("")
    lines += [
        data["handoff"],
        "",
        "## Docs",
        "",
        f"- Setup guide: {data['setup_page_url']}",
        f"- Per-client runbook: {data['agents_page_url']}",
        f"- Agent instructions: {data['briefing_url']}",
        f"- Docs index for agents: {card['endpoints']['llms_txt']}",
        f"- Skills manifest: {card['endpoints']['skills']}",
        f"- Product docs: {data['docs_url']}",
        "",
    ]
    return "\n".join(lines)


def render_markdown_table(data: dict) -> str:
    """Deterministic markdown render of the canonical table."""
    cols = data["columns"]
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in data["rows"]:
        lines.append(f"| {row['audience']} | {row['default_path']} | {row['also_works']} |")
    return "\n".join(lines)


def render_block(data: dict) -> str:
    """The full generated block, markers included."""
    return (
        f"{BEGIN_MARKER}\n"
        "<!-- GENERATED from shared/surface-routing.json — edit there, then run\n"
        "     python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write -->\n"
        f"{render_markdown_table(data)}\n"
        f"{END_MARKER}"
    )


_BLOCK_RE = re.compile(
    re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER),
    flags=re.DOTALL,
)


_AS_BLOCK_RE = re.compile(
    re.escape(AS_BEGIN_MARKER) + r".*?" + re.escape(AS_END_MARKER),
    flags=re.DOTALL,
)


_TOOL_INVENTORY_BLOCK_RE = re.compile(
    re.escape(TOOL_INVENTORY_BEGIN_MARKER) + r".*?" + re.escape(TOOL_INVENTORY_END_MARKER),
    flags=re.DOTALL,
)


def _check_markdown_target(
    rel: str,
    expected_block: str,
    write: bool,
    *,
    block_re: re.Pattern[str] = _BLOCK_RE,
    markers: tuple[str, str] = (BEGIN_MARKER, END_MARKER),
    label: str = "surface-routing",
    source: str = "shared/surface-routing.json",
) -> list[str]:
    path = REPO_ROOT / rel
    if not path.exists():
        return [f"{rel}: missing render target ({label} must render here)"]
    text = path.read_text()
    blocks = block_re.findall(text)
    if len(blocks) != 1:
        return [
            f"{rel}: expected exactly one {label} marker block "
            f"({markers[0]} … {markers[1]}), found {len(blocks)}"
        ]
    if blocks[0] == expected_block:
        return []
    if write:
        path.write_text(block_re.sub(lambda _: expected_block, text, count=1))
        return []
    return [
        f"{rel}: {label} block drifted from {source} (run the checker with --write to regenerate)"
    ]


def _check_whole_file_target(rel: str, expected: str, write: bool, *, source: str) -> list[str]:
    path = REPO_ROOT / rel
    if not path.exists():
        if write:
            path.write_text(expected)
            return []
        return [f"{rel}: missing generated file (run the checker with --write)"]
    if path.read_text() == expected:
        return []
    if write:
        path.write_text(expected)
        return []
    return [f"{rel}: drifted from {source} (run the checker with --write to regenerate)"]


def keel_app_routing_table_errors(text: str, routing: dict) -> list[str]:
    """keel-app's hand-copied ROUTING_TABLE must equal the canonical rows.

    Markdown backticks are stripped on the canonical side (the TS rows are
    plain text). Zero parsed rows is an error: a file the regex cannot read
    must never pass by silence."""
    rows = [m.groupdict() for m in _KEEL_APP_ROW_RE.finditer(text)]
    if not rows:
        return [f"{KEEL_APP_ROUTING_TABLE}: no ROUTING_TABLE rows parsed — the gate cannot see it"]
    want = [
        {k: r[k].replace("`", "") for k in ("audience", "default_path", "also_works")}
        for r in routing["rows"]
    ]
    if rows == want:
        return []
    return [
        f"{KEEL_APP_ROUTING_TABLE}: ROUTING_TABLE drifted from shared/surface-routing.json "
        f"(rows {rows} != {want}); copy the canonical rows over it"
    ]


def _check_agents_md_single_source() -> list[str]:
    a = REPO_ROOT / AGENTS_MD
    b = REPO_ROOT / WELL_KNOWN_AGENTS_MD
    missing = [str(p.relative_to(REPO_ROOT)) for p in (a, b) if not p.exists()]
    if missing:
        return [f"{m}: missing agents instruction file" for m in missing]
    if a.read_bytes() != b.read_bytes():
        return [
            f"{WELL_KNOWN_AGENTS_MD}: must be byte-identical to {AGENTS_MD} "
            "(single source; copy AGENTS.md over it)"
        ]
    return []


def transform_mdx_to_md(text: str) -> str:
    """Deterministic .mdx → raw .md transform.

    Mirror of services/keel-site/scripts/generate-docs-md.mjs: strip the
    frontmatter fence and emit ``# title`` + ``> description`` when the
    body doesn't already start with an H1. Any change here must be made
    in both implementations.
    """
    title = ""
    description = ""
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            frontmatter = text[4:end]
            body = text[end + 5 :]
            for line in frontmatter.splitlines():
                m = re.match(r"^(title|description):\s*(.*)$", line)
                if m:
                    value = m.group(2).strip()
                    if (value.startswith('"') and value.endswith('"')) or (
                        value.startswith("'") and value.endswith("'")
                    ):
                        value = value[1:-1]
                    if m.group(1) == "title":
                        title = value
                    else:
                        description = value
    body = body.lstrip("\n")
    header = ""
    if title and not body.startswith("# "):
        header = f"# {title}\n\n"
        if description:
            header += f"> {description}\n\n"
    return header + body


def _docs_md_expected() -> dict[str, str]:
    """Map of repo-relative output path → expected raw markdown."""
    content_dir = REPO_ROOT / DOCS_CONTENT_DIR
    out: dict[str, str] = {}
    for mdx in sorted(content_dir.rglob("*.mdx")):
        rel = mdx.relative_to(content_dir)
        if rel.name == "index.mdx":
            if rel.parent == Path("."):
                target = Path(DOCS_MD_ROOT_FILE)
            else:
                target = Path(DOCS_MD_PUBLIC_DIR) / rel.parent.with_suffix(".md")
        else:
            target = Path(DOCS_MD_PUBLIC_DIR) / rel.with_suffix(".md")
        out[str(target)] = transform_mdx_to_md(mdx.read_text())
    return out


def _check_docs_md() -> list[str]:
    errors: list[str] = []
    expected = _docs_md_expected()
    for rel, want in expected.items():
        path = REPO_ROOT / rel
        if not path.exists():
            errors.append(
                f"{rel}: missing raw-markdown docs variant "
                "(run `node services/keel-site/scripts/generate-docs-md.mjs`)"
            )
            continue
        if path.read_text() != want:
            errors.append(
                f"{rel}: stale raw-markdown docs variant "
                "(run `node services/keel-site/scripts/generate-docs-md.mjs`)"
            )
    # No orphans: every published .md must correspond to a docs page.
    public_docs = REPO_ROOT / DOCS_MD_PUBLIC_DIR
    if public_docs.exists():
        for md in sorted(public_docs.rglob("*.md")):
            rel = str(md.relative_to(REPO_ROOT))
            if rel not in expected:
                errors.append(f"{rel}: orphan docs .md variant (no matching content/docs page)")
    return errors


def check_agent_surface(*, write: bool = False) -> list[str]:
    """Agent-surface source → every consumer (spec 02 R2). Empty list = consistent."""
    errors: list[str] = []
    data = load_agent_surface()
    tools = listed_tools()
    # Rendered FIRST: WELL_KNOWN_AGENTS_MD is byte-copied from AGENTS.md in
    # the loop below, so the inventory has to be current in the source file
    # before that copy is taken. One --write run must settle both.
    inventory = render_tool_inventory_block()
    for rel in TOOL_INVENTORY_TARGETS:
        errors.extend(
            _check_markdown_target(
                rel,
                inventory,
                write,
                block_re=_TOOL_INVENTORY_BLOCK_RE,
                markers=(TOOL_INVENTORY_BEGIN_MARKER, TOOL_INVENTORY_END_MARKER),
                label="tool-inventory",
                source="the outcome registry (keel.tools.outcomes.OUTCOMES)",
            )
        )
    for rel, kind in AGENT_SURFACE_MARKDOWN_TARGETS:
        if write and rel == WELL_KNOWN_AGENTS_MD:
            # The byte copy of AGENTS.md (rendered just above): sync it
            # rather than patch its block, so one --write run is enough.
            (REPO_ROOT / rel).write_bytes((REPO_ROOT / AGENTS_MD).read_bytes())
            continue
        errors.extend(
            _check_markdown_target(
                rel,
                render_agent_surface_block(data, kind, tools),
                write,
                block_re=_AS_BLOCK_RE,
                markers=(AS_BEGIN_MARKER, AS_END_MARKER),
                label="agent-surface",
                source="shared/agent-surface.json",
            )
        )
    errors.extend(
        _check_whole_file_target(
            AGENT_CARD_TARGET,
            render_agent_card(data, tools),
            write,
            source="shared/agent-surface.json",
        )
    )
    module = render_ts_module(data, tools)
    for rel in TS_MODULE_TARGETS:
        errors.extend(
            _check_whole_file_target(rel, module, write, source="shared/agent-surface.json")
        )
    errors.extend(
        _check_whole_file_target(
            COPY_LAW_JSON_TARGET,
            render_copy_law_json(),
            write,
            source="scan_listing_copy.py FORBIDDEN_TEXT_RE",
        )
    )
    errors.extend(
        _check_whole_file_target(
            SKILLS_INDEX_TARGET,
            render_skills_index(),
            write,
            source="keel.skills + shared/agent-surface.json (build_skills_manifest.py)",
        )
    )
    for rel, mdx in TOOL_REFERENCE_TARGETS:
        errors.extend(
            _check_whole_file_target(
                rel,
                render_tool_reference(mdx=mdx),
                write,
                source="the outcome registry (generate_agent_reference_docs.py)",
            )
        )
    errors.extend(
        _check_whole_file_target(
            briefing_target(data),
            render_briefing(data, tools),
            write,
            source="shared/agent-surface.json",
        )
    )
    (REPO_ROOT / DOCS_SETUP_PAGE_TARGET).parent.mkdir(parents=True, exist_ok=True)
    errors.extend(
        _check_whole_file_target(
            DOCS_SETUP_PAGE_TARGET,
            render_docs_setup_page(data, tools),
            write,
            source="shared/agent-surface.json",
        )
    )
    return errors


def check_surface_routing(*, write: bool = False) -> list[str]:
    """Return human-readable drift errors (empty list = consistent)."""
    errors: list[str] = []
    data = load_canonical()
    expected_block = render_block(data)
    for rel in MARKDOWN_TARGETS:
        errors.extend(_check_markdown_target(rel, expected_block, write))
    app_table = REPO_ROOT / KEEL_APP_ROUTING_TABLE
    if not app_table.exists():
        errors.append(
            f"{KEEL_APP_ROUTING_TABLE}: missing render target (routing table must render here)"
        )
    else:
        errors.extend(keel_app_routing_table_errors(app_table.read_text(), data))
    errors.extend(check_agent_surface(write=write))
    errors.extend(check_agent_surface_gate())
    errors.extend(_check_agents_md_single_source())
    errors.extend(_check_docs_md())
    return errors


def main() -> int:
    write = "--write" in sys.argv[1:]
    errors = check_surface_routing(write=write)
    for warning in vendor_doc_warnings(load_agent_surface()):
        print(f"warning: {warning}", file=sys.stderr)
    if not errors:
        print("Surface-routing + agent-surface renders match shared/*.json.")
        return 0
    print("Surface-routing drift detected:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
