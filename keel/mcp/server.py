"""MCP server — binds the outcome-tool surface to FastMCP.

The CLI and MCP share one outcome-tool inventory (`keel.tools.outcomes`).
This module wires that inventory to FastMCP and adds the `keel://`
resources that agents use for lazy context fetches.

`KEEL_TOOLSETS` env filters which tools register at startup — default
`read-only,backtest,share,live-read` includes live monitoring and excludes
live-trading mutations.
"""

from __future__ import annotations

import json

from fastmcp import FastMCP


def create_server() -> FastMCP:
    """Create and configure the MCP server.

    Tools come from `keel.tools.outcomes`. Resources are registered
    inline below.
    """

    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes import _bootstrap as _outcomes_bootstrap
    from keel.tools.outcomes._mcp_adapter import register_all as _outcomes_mcp_register
    from keel.tools.outcomes._toolsets import is_listed_profile, load_toolsets

    active_toolsets = load_toolsets()
    live_write_loaded = "live-write" in active_toolsets

    instructions = (
        LISTED_INSTRUCTIONS if is_listed_profile() else _full_instructions(live_write_loaded)
    )

    mcp = FastMCP(name="keel", instructions=instructions)

    _outcomes_bootstrap()
    _outcomes_mcp_register(mcp, OUTCOMES)

    # ── Resources (spec §4 — lazy on demand, no startup token cost) ─────
    # All resources prefer live API fetches; the components catalog +
    # DSL reference fall back to bundled data when the API isn't
    # reachable (offline / unauth). Phase 2C+ adds tearsheet PNG,
    # weights parquet, and `keel://context/*` (per spec §9).

    @mcp.resource("keel://components/catalog")
    def components_catalog() -> str:
        """Live component catalog — fetched from /v1/components, with
        bundled fallback when the API isn't reachable."""
        try:
            from keel.client import KeelClient

            client = KeelClient()
            try:
                live = client.get_public("/v1/components")
                return json.dumps(live, default=str)
            finally:
                client.close()
        except Exception:  # noqa: BLE001 — API unavailable → fall back to bundled local components dump
            from keel.tools.local import strategy_components_dump

            return json.dumps(strategy_components_dump(), default=str)

    @mcp.resource("keel://components/{name}/schema")
    def component_schema(name: str) -> str:
        """One component's full param schema + type graph + examples.

        Fetched live from /v1/components/{name}. Bundled fallback below
        when the API isn't reachable."""
        try:
            from keel.client import KeelClient

            client = KeelClient()
            try:
                return json.dumps(client.get_public(f"/v1/components/{name}"), default=str)
            finally:
                client.close()
        except Exception:  # noqa: BLE001 — API unavailable → fall back to bundled local component detail
            from keel.tools.local import strategy_component_detail

            return json.dumps(strategy_component_detail(name), default=str)

    @mcp.resource("keel://strategy/{strategy_id}/source")
    def strategy_source(strategy_id: str) -> str:
        """DSL source at a strategy's HEAD version."""
        from keel.client import KeelClient

        client = KeelClient()
        try:
            return json.dumps(
                client.get(f"/v1/strategies/{strategy_id}/versions/HEAD/source"),
                default=str,
            )
        finally:
            client.close()

    @mcp.resource("keel://strategy/{strategy_id}/lockfile")
    def strategy_lockfile(strategy_id: str) -> str:
        """Compiled lockfile (versioned components + params) for a strategy."""
        from keel.client import KeelClient

        client = KeelClient()
        try:
            return json.dumps(client.get(f"/v1/strategies/{strategy_id}/lock"), default=str)
        finally:
            client.close()

    @mcp.resource("keel://backtest/{backtest_id}/results")
    def backtest_results(backtest_id: str) -> str:
        """Backtest results envelope (metrics + time series + attribution)."""
        from keel.client import KeelClient

        client = KeelClient()
        try:
            return json.dumps(client.get(f"/v1/backtests/{backtest_id}/results"), default=str)
        finally:
            client.close()

    def _latest_backtest_payload(strategy_id: str | None = None) -> dict:
        """Return the latest backtest plus exact artifact pointers.

        This is a resource helper, not a tool: agents use it when they
        need context about the last run without starting work or building
        their own polling/list loop.
        """
        from keel.client import KeelClient
        from keel.errors import KeelError
        from keel.tools.outcomes._pagination import extract_paginated

        client = KeelClient()
        try:
            params: dict[str, object] = {"limit": 1}
            if strategy_id:
                params["strategy_id"] = strategy_id
            payload = client.get("/v1/backtests", **params)
            items, _next_cursor = extract_paginated(payload)
            if not items:
                return {
                    "found": False,
                    "strategy_id": strategy_id,
                    "latest": None,
                    "results": None,
                    "suggested_next_action": {
                        "tool": "keel_backtest_run",
                        "args": {"strategy_id": strategy_id} if strategy_id else {},
                        "reason": (
                            "No backtests were found for this scope. Run a "
                            "backtest first, then read this resource again."
                        ),
                    },
                }

            latest = items[0]
            backtest_id = latest.get("id") or latest.get("backtest_id")
            status = str(latest.get("status") or "").lower()
            result_resource_uri = f"keel://backtest/{backtest_id}/results" if backtest_id else None
            out = {
                "found": True,
                "strategy_id": strategy_id or latest.get("strategy_id"),
                "backtest_id": backtest_id,
                "status": status or None,
                "hero_url": (
                    f"https://app.usekeel.io/backtests/{backtest_id}?tab=tearsheet"
                    if backtest_id
                    else None
                ),
                "result_resource_uri": result_resource_uri,
                "latest": latest,
                "results": None,
                "results_available": False,
            }

            if backtest_id and status == "completed":
                try:
                    out["results"] = client.get(f"/v1/backtests/{backtest_id}/results")
                    out["results_available"] = True
                except KeelError as e:
                    out["results_error"] = e.to_dict()

            if not out["results_available"]:
                out["suggested_next_action"] = {
                    "tool": "keel_backtest_watch",
                    "args": {"backtest_id": backtest_id} if backtest_id else {},
                    "reason": (
                        "Latest backtest results are not available yet. Watch "
                        "the run until terminal, then read result_resource_uri."
                    ),
                }
            return out
        finally:
            client.close()

    @mcp.resource("keel://backtest/latest")
    def latest_backtest() -> str:
        """Latest backtest in the current org, with exact result URI if available."""
        return json.dumps(_latest_backtest_payload(), default=str)

    @mcp.resource("keel://strategy/{strategy_id}/backtest/latest")
    def latest_strategy_backtest(strategy_id: str) -> str:
        """Latest backtest for one strategy, with exact result URI if available."""
        return json.dumps(_latest_backtest_payload(strategy_id), default=str)

    @mcp.resource("keel://ownership/strategy/{strategy_id}")
    def strategy_ownership(strategy_id: str) -> str:
        """First-session ownership projection for one strategy."""
        from keel.tools.outcomes._base import ToolContext
        from keel.tools.outcomes._ownership import (
            fetch_ownership_projection,
            ownership_envelope_fields,
        )

        ctx = ToolContext()
        projection = fetch_ownership_projection(ctx, strategy_id)
        if projection:
            out = {"strategy_id": strategy_id, "projection": projection}
            out.update(ownership_envelope_fields(projection))
            return json.dumps(out, default=str)
        return json.dumps(
            {
                "strategy_id": strategy_id,
                "projection_available": False,
                "ownership_status": "not_started",
                "next_recommended_action": {
                    "kind": "write_strategy_brief",
                    "reason": "No first-session ownership projection is available yet.",
                },
                "missing_evidence": [
                    "strategy_brief",
                    "baseline_evidence",
                    "failure_modes",
                ],
                "live_readiness_blockers": [
                    "no_baseline",
                    "no_diagnosis",
                    "no_ownership_decision",
                    "no_readiness_review",
                ],
            },
            default=str,
        )

    @mcp.resource("keel://dsl/reference/{topic}")
    def dsl_reference_resource(topic: str) -> str:
        """DSL reference doc by topic (phases, types, slots, composition,
        normalization, best_practices). Bundled for 0.3.0 — API endpoint
        ships in Phase 2C."""
        from keel.tools.local import dsl_reference

        return json.dumps(dsl_reference(topic=topic), default=str)

    @mcp.resource("keel://knowledge/{section}")
    def knowledge_resource(section: str) -> str:
        """Bundled system-knowledge section (same files chat-api loads
        into its always-on system prompt). Use for direct fetch of one
        section without invoking a full skill. Sections include:
        ``reasoning_principles``, ``composition_mechanics``,
        ``dsl_syntax``, ``mistakes``, ``tool_usage``, ``trading_domain``,
        ``strategy_paths``, ``strategy_patterns``, ``universe_selection``,
        ``pipeline_system``, ``collaboration``, ``editor_ui``,
        ``component_versioning``. Section name matches the filename stem
        under ``keel/data/knowledge/``. Raises FileNotFoundError if the
        section doesn't exist — caller can use ``resources/list`` to
        enumerate available sections."""
        from keel.skills import load_section

        return load_section(section)

    @mcp.resource("keel://context/user")
    def user_context_resource() -> str:
        """Global user context (`~/.keel/context.md`) — preferences,
        default universe, custom prompt fragments. Read on session
        start by the agent (per spec §9.2)."""
        from keel.context import read_user_context

        entry = read_user_context()
        return json.dumps(
            {
                "layer": entry.layer,
                "source": str(entry.source) if entry.source else None,
                "exists": entry.exists,
                "body": entry.body,
            },
            default=str,
        )

    @mcp.resource("keel://context/project")
    def project_context_resource() -> str:
        """Project-level context (`<cwd>/keel.md` or the `## Keel` block
        in `CLAUDE.md`) — repo-specific preferences (per spec §9.1)."""
        from keel.context import read_project_context

        entry = read_project_context()
        return json.dumps(
            {
                "layer": entry.layer,
                "source": str(entry.source) if entry.source else None,
                "exists": entry.exists,
                "body": entry.body,
            },
            default=str,
        )

    @mcp.resource("keel://context/strategy/{strategy_id}")
    def strategy_context_resource(strategy_id: str) -> str:
        """Per-strategy context — wraps `keel_strategy_memory_read` so
        agents can browse memory as a resource. Returns the most recent
        notes."""
        from keel.client import KeelClient

        client = KeelClient()
        try:
            payload = client.get(f"/v1/strategies/{strategy_id}/memory", limit=10)
            return json.dumps(payload, default=str)
        except Exception as e:  # noqa: BLE001
            return json.dumps(
                {"strategy_id": strategy_id, "notes": [], "error": str(e)},
                default=str,
            )
        finally:
            client.close()

    # ── Skills (spec §11) — registered as MCP prompts ──────────────────
    # Hosts that support prompt-pickers render these natively as
    # `/skill <name>`-style commands. Hosts that don't can still
    # discover them via `prompts/list` and pull the body via
    # `prompts/get`. The body composes lazily from frontmatter +
    # bundled knowledge sections + per-skill workflow body.

    _register_skill_prompts(mcp)

    return mcp


# ── Server instructions, per profile (spec 01 R3) ─────────────────────
#
# LISTED (directory registration): policy-vetted copy — no deploy/fund/
# trade verbs, no routing to tools absent from the listed surface
# (research/08 string rules; gate: tests/test_policy_scan.py).
LISTED_INSTRUCTIONS = (
    "Keel is a quantitative crypto research platform for Hyperliquid "
    "strategy development: compose strategies in the Keel DSL, run "
    "backtests on real market history, and review the results. The "
    "agent surface is a workflow-shaped set of outcome tools (call by "
    "canonical `keel_*` name; see `tools/list` for the active set). "
    "Start with `keel_status` to check auth + visible tools. "
    "\n\n"
    "WORKFLOW ROUTES — prefer these paths over ad-hoc tool picking. "
    "RESEARCH: decompose thesis → `keel_components_search` → "
    "`keel_components_detail_batch` for every planned component → "
    "`keel_strategy_compose(dry_run=true)` → save with "
    "`keel_strategy_compose` → `keel_backtest_run` → "
    "`keel_backtest_summarize`. "
    "EXISTING STRATEGY: `keel_strategy_search` or `keel_strategy_get` → "
    "`keel_strategy_fork` to iterate on a copy → backtest the fork. "
    "MONITORING: `keel_live_monitor` gives read-only state for "
    "strategies already running on the user's account. "
    "WEB APP: `keel_open_in_app` returns a link to view and manage a "
    "strategy in the Keel web app — offer it whenever the user wants "
    "to see or act on results outside this chat. "
    "DEBUG: read the structured error envelope first; use "
    "`recover-from-error` and `keel_doctor`. "
    "FEEDBACK: at the END of a session — and whenever the same friction "
    "repeats (a tool erroring twice, a confusing result, a missing "
    "capability) — file it with `keel_feedback` (kind: friction | praise "
    "| bug). It never fails and nothing waits on it. "
    "\n\n"
    "SKILLS — load deep workflow guidance BEFORE composing or "
    "iterating: see `prompts/list` (`strategy-creation`, "
    "`strategy-fork-and-iterate`, `backtest-and-analyze`, "
    "`component-discovery`, `portfolio-review`, `overfit-check`, "
    "`recover-from-error`). INVOKE `strategy-creation` BEFORE the "
    "first `keel_strategy_compose` in any session. "
    "\n\n"
    "KNOWLEDGE RESOURCES — individual sections available as MCP "
    "resources at `keel://knowledge/{section}`; latest backtest "
    "pointers at `keel://backtest/latest`. See `resources/list`."
    "\n\n"
    "STATE MODEL — strategy state lives on the Keel server: every tool "
    "call reads and writes the server's canonical version (one linear "
    "history), and `keel_strategy_log` shows which surface made each "
    "change."
)


def _full_instructions(live_write_loaded: bool) -> str:
    """Instructions for the full (unlisted endpoint / local) profile."""
    return (
        "Keel is a quantitative crypto trading platform for Hyperliquid. "
        "The agent surface is a workflow-shaped set of outcome tools "
        "(call by canonical `keel_*` name; see `tools/list` for the "
        "active set under `KEEL_TOOLSETS`). "
        "Start with `keel_status` to check auth + visible tools. "
        "Auth: when `keel_status` returns `authenticated: false`, OR when "
        "any tool's error envelope sets `suggested_next_action.tool` to "
        "`keel_auth_login`, call `keel_auth_login` directly — it opens the "
        "user's browser, captures the OAuth redirect, and persists tokens. "
        "Optional `scope='live'` pre-checks live-trading consent. "
        "\n\n"
        "WORKFLOW ROUTES — prefer these paths over ad-hoc tool picking. "
        "FIRST SESSION: `keel_status` → `keel_auth_login` if needed → "
        "`prompts/list` and load `strategy-creation` before strategy work. "
        "RESEARCH: decompose thesis → `keel_components_search` → "
        "`keel_components_detail_batch` for every planned component → "
        "`keel_strategy_compose(dry_run=true)` → save with "
        "`keel_strategy_compose` → `keel_backtest_run` → "
        "`keel_backtest_summarize`. "
        "EXISTING STRATEGY: `strategy-fork-and-iterate` prompt → "
        "`keel_strategy_search` or `keel_strategy_get` → checkout/status/push "
        "when doing local file edits → backtest server HEAD. "
        "DEBUG: read the structured error envelope first; use "
        "`recover-from-error`, `keel_doctor`, and `keel_audit_list_last`. "
        "FEEDBACK: at the END of a session — and whenever the same friction "
        "repeats (a tool erroring twice, a confusing result, a missing "
        "capability) — file it with `keel_feedback` (kind: friction | praise "
        "| bug). It never fails and nothing waits on it. "
        "LIVE READ: `keel_live_monitor` is visible by default for existing "
        "deployments. Read `keel_live_monitor.freshness` before interpreting "
        "live data; positions are exchange snapshots, while portfolio/history "
        "views are recorded backend state. "
        "LIVE WRITE: deploy/control tools require explicit user request and "
        "`live-write` toolset opt-in. Load `deploy-and-monitor`, call "
        "`keel_accounts_list`, preview with `keel_live_deploy`, show the "
        "preview, then deploy only with the returned `confirmation_token` plus "
        "host/CLI confirmation and local arming. "
        "\n\n"
        "STATE MODEL — server HEAD is the single source of truth: "
        "backtests, deploys, and shares always resolve a server commit, "
        "never a local file. Local checkouts are working copies that WRITE "
        "THROUGH by default: `keel_backtest_run` and the `keel_live_deploy` "
        "preview push unpushed local edits automatically and pin to the new "
        "commit (`auto_push=false` opts out and raises `local_ahead`). "
        "Server-side edits (`keel_strategy_compose`) write back into a "
        "same-machine checkout; elsewhere `keel_strategy_status` detects "
        "staleness by hash and says to run `keel_strategy_pull`. A true "
        "conflict (local edited AND server moved) STOPS with a "
        "`sync_conflict` envelope carrying three-way hashes plus options "
        "`pull_force` | manual merge via `keel_strategy_diff` | pin "
        "`commit_id` — never auto-merged, never force-pushed. Commits carry "
        "surface attribution: `keel_strategy_log` shows 'modified via "
        "claude.ai, 2h ago'. "
        "\n\n"
        "SKILLS — load deep workflow guidance BEFORE composing or iterating. "
        "This server exposes 8 MCP prompts under the `keel-skill` tag (see "
        "`prompts/list`): `strategy-creation`, `strategy-fork-and-iterate`, "
        "`backtest-and-analyze`, `component-discovery`, `deploy-and-monitor`, "
        "`portfolio-review`, `overfit-check`, `recover-from-error`. Each "
        "auto-loads the matching knowledge sections inline (e.g. "
        "`strategy-creation` loads reasoning_principles + composition_mechanics "
        "+ dsl_syntax + mistakes + tool_usage + universe_selection + "
        "pipeline_system — the same knowledge chat-api keeps always-on). "
        "INVOKE `strategy-creation` BEFORE the first `keel_strategy_compose` "
        "in any session — without it you're composing blind. Same rule for "
        "`backtest-and-analyze` before `keel_backtest_run`, and "
        "`recover-from-error` when a tool keeps failing. The skill body "
        "tells you which other tools to call and in what order. "
        "\n\n"
        "KNOWLEDGE RESOURCES — individual sections also available as MCP "
        "resources at `keel://knowledge/{section}` (e.g. "
        "`keel://knowledge/tool_usage`, `keel://knowledge/mistakes`, "
        "`keel://knowledge/strategy_paths`) for direct fetch without "
        "invoking a full skill. Latest backtest resources "
        "(`keel://backtest/latest` and "
        "`keel://strategy/{strategy_id}/backtest/latest`) provide a compact "
        "last-run pointer without making agents build ad-hoc list loops. "
        "See `resources/list` for the full set."
        + (
            ""
            if live_write_loaded
            else "\n\n"
            "Live write tools (deploy/control) are NOT loaded under the default "
            "toolset. Set "
            "`KEEL_TOOLSETS=read-only,backtest,share,live-read,live-write` "
            "to opt in. `live` remains a deprecated alias for both live-read "
            "and live-write."
        )
    )


# Skills excluded from the listed-profile prompt surface — their bodies
# guide workflows whose tools are not registered on that profile.
LISTED_EXCLUDED_SKILLS: frozenset[str] = frozenset({"deploy-and-monitor"})


def _register_skill_prompts(mcp: "FastMCP") -> None:
    """Register each bundled skill as an MCP prompt.

    Per spec §11 (last paragraph): "Hosts that support prompt-pickers
    render it free; everyone else gets NL matching". We use FastMCP's
    `add_prompt` to register one prompt per skill. The prompt name is
    the skill's canonical name; the description is the skill's
    short description; the body lazy-loads via `compose_skill()`.
    """
    from keel.skills import BUNDLED_SKILLS, compose_skill, list_skills
    from keel.tools.outcomes._toolsets import is_listed_profile

    try:
        skills_map = list_skills()
    except Exception:  # noqa: BLE001 — skill parse failure at startup → serve no prompts, tools still work
        # If skill parsing fails at server startup, fall back to no
        # prompts rather than crashing the server. Tools still work.
        return

    for name in BUNDLED_SKILLS:
        sk = skills_map.get(name)
        if sk is None:
            continue
        if is_listed_profile() and name in LISTED_EXCLUDED_SKILLS:
            # The listed registration exposes no deploy workflow —
            # its guidance would route to tools absent from the
            # surface (spec 01 R3, research/08).
            continue

        # Closure capture: bind `name` per iteration so each prompt
        # composes its own body.
        def _make_handler(skill_name: str):
            def _handler() -> str:
                return compose_skill(skill_name)

            _handler.__name__ = f"skill_{skill_name.replace('-', '_')}"
            _handler.__doc__ = (
                f"Keel agent skill: {skill_name}. "
                f"Composes frontmatter + knowledge sections + workflow body. "
                f"Trigger: {' '.join(sk.trigger.split())[:200]}"
            )
            return _handler

        mcp.prompt(
            name=name,
            description=_one_line(sk.description),
            tags={"keel-skill"},
        )(_make_handler(name))


def _one_line(text: str) -> str:
    return " ".join(text.split())
