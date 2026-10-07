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
from typing import Any

from fastmcp import FastMCP
from mcp.types import Icon

from keel.mcp._branding import (
    KEEL_ICON_256_DATA_URI,
    KEEL_ICON_256_SIZES,
    KEEL_ICON_256_URL,
    KEEL_ICON_DATA_URI,
    KEEL_ICON_MIME,
    KEEL_ICON_SIZES,
    KEEL_WEBSITE_URL,
)
from pipeline_engine.reference.system import assemble as _assemble


class KeelMCP(FastMCP):
    """FastMCP plus the deprecated-name layer (`_toolsets.TOOL_ALIASES`).

    `tools/call` with a renamed tool's OLD name runs the renamed tool: the
    name is canonicalised here, before FastMCP resolves it and before the
    middleware chain runs, so the hosted server's scope gate, audit row and
    metrics see the registered name. `tools/list` is untouched — it is the
    local provider's catalog, which carries only the new names — so a host
    that refreshes its catalog never learns an old spelling. Both the wire
    handler (`_call_tool_mcp`) and in-process callers go through
    `call_tool`, which is why the override lives on this one method.
    """

    async def call_tool(self, name: str, arguments: Any = None, **kwargs: Any) -> Any:
        from keel.tools.outcomes._toolsets import canonical_tool_name

        return await super().call_tool(canonical_tool_name(name), arguments, **kwargs)


def create_server() -> FastMCP:
    """Create and configure the MCP server.

    Tools come from `keel.tools.outcomes`. Resources are registered
    inline below.
    """

    from keel.hosting import is_hosted
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes import _bootstrap as _outcomes_bootstrap
    from keel.tools.outcomes._mcp_adapter import register_all as _outcomes_mcp_register
    from keel.tools.outcomes._toolsets import is_listed_profile, load_toolsets

    active_toolsets = load_toolsets()
    live_write_loaded = "live-write" in active_toolsets

    instructions = (
        LISTED_INSTRUCTIONS
        if is_listed_profile()
        else _full_instructions(live_write_loaded, hosted=is_hosted())
    )

    # serverInfo.version = the published keel-trade wheel version (not the
    # FastMCP framework version FastMCP would otherwise report) — this is the
    # product version connectors/directories show.
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        _keel_version = _pkg_version("keel-trade")
    except PackageNotFoundError:
        # Not pip-installed (the .mcpb bundle runs from an unpacked tree):
        # fall back to the package's own version constant so directories
        # never display 0.0.0.
        from keel import __version__ as _keel_version

    mcp = KeelMCP(
        name="keel",
        version=_keel_version,
        instructions=instructions,
        website_url=KEEL_WEBSITE_URL,
        # serverInfo.icons — the Keel mark, embedded so it ships in the wheel.
        # Surfaced by Cursor / Claude Desktop connector UIs (claude.ai custom
        # connectors do not render it yet, but the field is correct).
        icons=[
            Icon(
                src=KEEL_ICON_256_URL,
                mimeType=KEEL_ICON_MIME,
                sizes=KEEL_ICON_256_SIZES,
            ),
            Icon(
                src=KEEL_ICON_256_DATA_URI,
                mimeType=KEEL_ICON_MIME,
                sizes=KEEL_ICON_256_SIZES,
            ),
            Icon(
                src=KEEL_ICON_DATA_URI,
                mimeType=KEEL_ICON_MIME,
                sizes=KEEL_ICON_SIZES,
            ),
        ],
    )

    _outcomes_bootstrap()
    _outcomes_mcp_register(mcp, OUTCOMES)

    # ── Widget cards (spec 06 R2) ───────────────────────────────────
    # One bundle, four cards (backtest / strategy glass-box / live /
    # deploy-preflight), registered as `ui://keel/cards/*` resources in
    # both MCP Apps and ChatGPT Apps SDK dialects. Gated by the same
    # profile/toolset machinery as the owning tools — the preflight
    # card exists only where keel_live_deploy does (never listed).
    from keel.widgets import register_card_resources

    register_card_resources(mcp)

    # ── Resources (spec §4 — lazy on demand, no startup token cost) ─────
    # All resources prefer live API fetches; the components catalog +
    # DSL reference fall back to bundled data when the API isn't
    # reachable (offline / unauth).

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
                    # A fact, not a call to spend quota (spec 05 R-L4).
                    "suggested_next_action": {
                        "tool": None,
                        "args": {},
                        "reason": "No backtests have been run for this scope.",
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
        return ownership_resource_payload(strategy_id)

    @mcp.resource("keel://dsl/reference/{topic}")
    def dsl_reference_resource(topic: str) -> str:
        """DSL reference doc by topic (phases, types, slots, composition,
        normalization, best_practices). Served from the bundled data: the
        planned `/v1/reference/{topic}` endpoint never shipped, and the
        bundle is the path (same note as `help.py`)."""
        from keel.tools.local import dsl_reference

        return json.dumps(dsl_reference(topic=topic), default=str)

    @mcp.resource("keel://knowledge/{section}")
    def knowledge_resource(section: str) -> str:
        """Bundled system-knowledge section — a direct fetch of one
        section without invoking a full skill. Raises FileNotFoundError
        if the section doesn't exist; ``resources/list`` enumerates
        them."""
        # Served form, not the raw file: `operating_core` is the base
        # document (no chat opinion layer), exactly as `keel_help` serves it.
        from keel.data.knowledge import served_section

        return served_section(section)

    # The section list is GENERATED from the bundled directory, never
    # hand-typed (W2 §5 S4): a list in prose is a list that goes stale
    # the first time a file is added. `Section name matches the filename
    # stem under keel/data/knowledge/.`
    knowledge_resource.__doc__ = (knowledge_resource.__doc__ or "") + (
        " Sections: " + ", ".join(f"``{name}``" for name in _bundled_knowledge_sections()) + "."
    )

    if serves_local_filesystem_context():
        _register_local_context_resources(mcp)

    @mcp.resource("keel://context/strategy/{strategy_id}")
    def strategy_context_resource(strategy_id: str) -> str:
        """Per-strategy context — wraps `keel_strategy_notes_read` so
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


# ── Server instructions, per profile (spec 01 R3; guidance spec §3 L2) ─
#
# NOTHING is hand-written here any more. Every sentence of every profile
# comes from the corpus source (`operating_core.md`) through the builder
# (`pipeline_engine.reference.system.assemble`, vendored into this wheel
# by scripts/build_data.py exactly as `dsl/catalog.py` is). The builder
# emits, per profile, the 482-character head plus the `layer: body`
# sections whose `profiles:` admit that profile — so a copy change is a
# corpus edit, never a Python edit, and the three profiles cannot drift
# from one another or from the chat's static prompt.
#
# Host facts the assembly is written against (guidance spec §2):
#
# * claude.ai DROPS server instructions entirely (claude-ai-mcp#93) — so
#   nothing lives only here; every sentence has an L1/L3 twin, checked by
#   the sole-carrier guard in tests/test_guidance_guards.py.
# * ChatGPT and Codex read the FIRST 512 CHARACTERS as the essentials —
#   the head is self-contained inside them (six facts, guard-pinned).
# * Claude Code truncates at 2 KB (BYTES) and uses the string as its
#   tool-search discovery signal — hence fact 1's task category.
#
# LISTED (directory registration): policy-vetted copy — no deploy/fund/
# trade verbs, no routing to tools absent from the listed surface
# (research/08 string rules; gate: tests/test_policy_scan.py). The
# full-only blocks (auth, write-through state, live) carry
# `profiles: [full]` in the corpus and never reach the listed string.


def _listed_instructions() -> str:
    """The listed profile's instructions, assembled from the corpus."""
    return _assemble.instructions("listed")


LISTED_INSTRUCTIONS = _listed_instructions()


def _full_instructions(live_write_loaded: bool, *, hosted: bool = False) -> str:
    """Instructions for the full (local MCP / CLI) profile.

    ``hosted`` selects the hosted-server SURFACE section (spec 07 R7):
    the hosted endpoint is file-free, so file/workspace asks route to the
    CLI; local servers get the charts → web-app hint. The live-write
    notice is emitted only when that toolset is absent, exactly as
    before — the condition is the corpus section's
    ``when: live_write_absent`` key.
    """
    profile = "full-hosted" if hosted else "full-local"
    return _assemble.instructions(profile, live_write_loaded=live_write_loaded)


def _bundled_knowledge_sections() -> tuple[str, ...]:
    """Every bundled knowledge section stem, read from the directory.

    W2 §5 S4: the resource docstring used to carry a hand-typed list of
    13 names while 18 files shipped. Reading the directory is the fix —
    the enumeration cannot disagree with what is served.
    """
    from importlib import resources

    return tuple(
        sorted(
            f.name.removesuffix(".md")
            for f in resources.files("keel.data").joinpath("knowledge").iterdir()
            if f.name.endswith(".md")
        )
    )


# URIs of the resources that read the SERVER MACHINE's filesystem. They
# exist only where the server machine is the user's machine.
LOCAL_FILESYSTEM_CONTEXT_URIS: tuple[str, ...] = (
    "keel://context/user",
    "keel://context/project",
)


def serves_local_filesystem_context() -> bool:
    """Whether this server registers the local-filesystem context resources.

    ``keel://context/user`` reads ``~/.keel/context.md`` and
    ``keel://context/project`` reads ``keel.md`` / ``CLAUDE.md`` from the
    process cwd. On the user's own machine (CLI, stdio MCP, the .mcpb
    bundle) that is the user's context. On a hosted server
    (``KEEL_EXECUTION_MODE=hosted`` — services/mcp-server always sets it)
    or the directory-listed profile (``KEEL_SERVER_PROFILE=listed``) it
    would be the POD's home directory and working directory: always absent
    today, but a server-filesystem read on a shared connector serves
    whatever file an image ever ships there to every caller, and reads as a
    data-exposure path to a directory reviewer. So neither is registered
    there (S-5). Per-strategy context (``keel://context/strategy/{id}``)
    reads through the Keel API as the caller and stays on every profile.
    """
    from keel.hosting import is_hosted
    from keel.tools.outcomes._toolsets import is_listed_profile

    return not (is_hosted() or is_listed_profile())


def _local_context_payload(entry) -> str:
    return json.dumps(
        {
            "layer": entry.layer,
            "source": str(entry.source) if entry.source else None,
            "exists": entry.exists,
            "body": entry.body,
        },
        default=str,
    )


def _register_local_context_resources(mcp: "FastMCP") -> None:
    """Register the two local-filesystem context layers (local mode only —
    see :func:`serves_local_filesystem_context`)."""

    @mcp.resource("keel://context/user")
    def user_context_resource() -> str:
        """Global user context (`~/.keel/context.md`) — preferences,
        default universe, custom prompt fragments. Read on session
        start by the agent (per spec §9.2)."""
        from keel.context import read_user_context

        return _local_context_payload(read_user_context())

    @mcp.resource("keel://context/project")
    def project_context_resource() -> str:
        """Project-level context (`<cwd>/keel.md` or the `## Keel` block
        in `CLAUDE.md`) — repo-specific preferences (per spec §9.1)."""
        from keel.context import read_project_context

        return _local_context_payload(read_project_context())


def ownership_resource_payload(strategy_id: str) -> str:
    """Body of the ``keel://ownership/strategy/{id}`` resource.

    Module-level (not a closure inside ``create_server``) so it can be driven
    directly by tests — the resource's registration is trivial, its behaviour
    is not.
    """
    import os

    from keel.tools.outcomes import _ownership
    from keel.tools.outcomes._base import ToolContext

    # Honor KEEL_APP_URL like _cli_adapter and _mcp_adapter do; a bare
    # ToolContext() would point every staging reader at prod URLs.
    app_url = os.environ.get("KEEL_APP_URL")
    ctx = ToolContext(app_url=app_url) if app_url else ToolContext()

    fetch = _ownership.fetch_projection(ctx, strategy_id)
    out: dict = {"strategy_id": strategy_id}
    if fetch.projection is not None:
        out["projection"] = fetch.projection
        out.update(_ownership.ownership_envelope_fields(fetch.projection))
        return json.dumps(out, default=str)
    # Honest unavailability with its reason — never a fabricated
    # 'not_started' projection (Q-0500). A strategy with no ownership work at
    # all is NOT this branch: keel-api answers that with a real projection
    # whose session_id is null (spec 20 §2.4 N1).
    out.update(_ownership.projection_unavailable_fields(fetch))
    return json.dumps(out, default=str)


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

    A skill that fails to parse fails server startup, loudly, naming the
    skill (`keel.skills._parse` raises ``ValueError``). It used to be caught
    here and the server came up serving NO prompts — a silent fallback
    (`.claude/rules/lessons.md`) that hid a packaging defect behind a
    healthy-looking server whose method layer had vanished (05 R-L2). Every
    bundled skill ships in the wheel and is parsed by the test suite, so a
    parse failure is a build error, never a runtime condition.
    """
    from keel.skills import BUNDLED_SKILLS, compose_skill, list_skills
    from keel.tools.outcomes._toolsets import is_listed_profile

    skills_map = list_skills()

    for name in BUNDLED_SKILLS:
        sk = skills_map[name]
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
                f"Composes frontmatter + reference index + workflow body. "
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
