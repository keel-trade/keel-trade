"""`keel_library_*` — the verified Library on the agent surface.

The contract under test (founder ruling 2026-08-21, additive-only):

* the three tools register and appear on BOTH the full and listed
  profiles (the hosted surface ships them immediately);
* envelopes follow the OutcomeResult conventions (hero_url, url_line,
  entry facts in extra);
* `keel_library_fork` sends an explicit `created_via` mapped from the
  SDK's self-declared surface — never lets the server default an agent
  fork to "app";
* fork → checkout integration: the returned strategy_id is directly
  consumable by `keel_strategy_checkout`'s input contract;
* the light-push boundary: the first_session status route carries the
  one neutral library line; the fork tool's description carries the
  new-user default (`ma-crossover-crypto`); `strategy-creation` (the
  from-thesis skill) stays library-free.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import respx
from httpx import Response
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


_bootstrap()

API = "https://api.test.keel"

SDK_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def _api_env(monkeypatch):
    monkeypatch.setenv("KEEL_API_KEY", "test-key")
    monkeypatch.setenv("KEEL_API_URL", API)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.setattr("keel.client.time.sleep", lambda *_: None)


# ─── Registration + profiles ────────────────────────────────────────────


def test_all_three_tools_register():
    for name in ("keel_library_list", "keel_library_get", "keel_library_fork"):
        assert name in OUTCOMES, f"{name} missing from the outcome registry"


def test_all_three_on_the_listed_profile():
    """Founder ruling: the hosted surface gets all three immediately."""
    for name in ("keel_library_list", "keel_library_get", "keel_library_fork"):
        assert name in LISTED_PROFILE_TOOLS


def test_toolset_classes():
    assert OUTCOMES["keel_library_list"].toolset == "read-only"
    assert OUTCOMES["keel_library_get"].toolset == "read-only"
    # fork creates a strategy — backtest class, NOT live-write
    assert OUTCOMES["keel_library_fork"].toolset == "backtest"
    assert OUTCOMES["keel_library_fork"].required_action == "strategy.create"


def test_cli_paths_are_the_library_group():
    assert OUTCOMES["keel_library_list"].cli_path == ("library", "list")
    assert OUTCOMES["keel_library_get"].cli_path == ("library", "get")
    assert OUTCOMES["keel_library_fork"].cli_path == ("library", "fork")


# ─── Handlers / envelopes ───────────────────────────────────────────────


@respx.mock
def test_list_envelope(_api_env):
    respx.get(f"{API}/v1/library").mock(
        return_value=Response(
            200,
            json={
                "import_id": "imp_1",
                "data_as_of": "2026-08-01",
                "count": 1,
                "entries": [
                    {
                        "slug": "ma-crossover-crypto",
                        "name": "MA Crossover",
                        "category": "trend",
                        "risk_band": "balanced",
                        "headline": {"sharpe": 1.1},
                        "default_variant_id": "v-default",
                        "public_path": "/strategies/ma-crossover-crypto",
                    }
                ],
            },
        )
    )
    env = OUTCOMES["keel_library_list"].handler({}, ToolContext()).to_envelope()
    assert env["hero_url"].endswith("/library")
    assert env["count"] == 1
    assert env["entries"][0]["slug"] == "ma-crossover-crypto"
    assert env["entries"][0]["headline"] == {"sharpe": 1.1}
    # projection is deliberate: internal/public-web fields stay out
    assert "public_path" not in env["entries"][0]


@respx.mock
def test_get_envelope(_api_env):
    respx.get(f"{API}/v1/library/funding-carry").mock(
        return_value=Response(
            200,
            json={
                "slug": "funding-carry",
                "name": "Funding Carry",
                "headline": {"sharpe": 1.146},
                "entry_version": "3",
                "data_as_of": "2026-08-01",
                "stale": False,
                "backtest_window": {"start": "2024-08-15", "end": "2026-07-05"},
                "variants": [{"variant_id": "v1", "is_default": True}],
            },
        )
    )
    env = (
        OUTCOMES["keel_library_get"].handler({"slug": "funding-carry"}, ToolContext()).to_envelope()
    )
    assert env["hero_url"].endswith("/library/funding-carry")
    assert env["backtest_window"]["start"] == "2024-08-15"
    assert env["variants"][0]["variant_id"] == "v1"
    # No graph published ⇒ no view, rather than an empty shell.
    assert "view" not in env


@respx.mock
def test_get_carries_the_entry_view_when_the_entry_publishes_a_graph(_api_env):
    """PLAN §4.2: "what IS this strategy" is answered before the fork.

    The library's graph is its own render-only shape (`{name, steps}`,
    the generated content package) — the view is built from it exactly
    as it is built from keel-api's GraphModel.
    """
    graph = json.loads(
        (
            SDK_ROOT.parents[2] / "libs/strategy_library/data/entries/funding-carry/graph.json"
        ).read_text()
    )
    respx.get(f"{API}/v1/library/funding-carry").mock(
        return_value=Response(
            200,
            json={
                "slug": "funding-carry",
                "name": "Funding Carry",
                "entry_version": "3",
                "graph": graph,
            },
        )
    )
    env = (
        OUTCOMES["keel_library_get"].handler({"slug": "funding-carry"}, ToolContext()).to_envelope()
    )
    view = env["view"]
    assert view["name"] == "Funding Carry"
    assert view["status"] == "PUBLISHED"
    assert view["size"] == "structure"
    assert view["url_line"].endswith("/library/funding-carry")
    # Every component the entry declares is named in the markdown.
    assert "FundingDataLoader" in view["markdown"]


@respx.mock
def test_fork_envelope_and_created_via(_api_env):
    route = respx.post(f"{API}/v1/library/ma-crossover-crypto/fork").mock(
        return_value=Response(
            201,
            json={
                "strategy_id": "str_new1",
                "name": "MA Crossover",
                "source_slug": "ma-crossover-crypto",
                "entry_version": "2",
                "variant_id": "v-default",
                "is_default_variant": True,
            },
        )
    )
    env = (
        OUTCOMES["keel_library_fork"]
        .handler({"slug": "ma-crossover-crypto"}, ToolContext())
        .to_envelope()
    )
    assert env["run_id"] == "str_new1"
    assert env["strategy_id"] == "str_new1"
    # V-6 (ratified 2026-09-19): every strategy link lands in the editor.
    assert env["hero_url"].endswith("/strategies/str_new1/edit")
    assert env["share_url"] is None

    # created_via is ALWAYS sent explicitly (server would default to "app").
    # `route` is the fork POST route, so `.last` is still that call — the
    # read-back this fork now attempts for its `view` (PLAN §4.2) is a
    # different route, and being unmocked here it is simply advisory.
    import json as _json

    body = _json.loads(route.calls.last.request.content)
    assert body["created_via"] in ("cli", "mcp-local", "mcp-remote", "sdk", "chat")


def test_created_via_surface_mapping(monkeypatch):
    from keel.tools.outcomes.library import _created_via

    monkeypatch.setattr("keel.surface.current_surface", lambda: "local-mcp")
    assert _created_via() == "mcp-local"
    monkeypatch.setattr("keel.surface.current_surface", lambda: "hosted-mcp")
    assert _created_via() == "mcp-remote"
    monkeypatch.setattr("keel.surface.current_surface", lambda: "cli")
    assert _created_via() == "cli"
    # unknown surfaces fail safe to plain sdk attribution
    monkeypatch.setattr("keel.surface.current_surface", lambda: "??")
    assert _created_via() == "sdk"


@respx.mock
def test_fork_then_checkout_integration(_api_env, tmp_path, monkeypatch):
    """The fork's strategy_id feeds keel_strategy_checkout directly."""
    respx.post(f"{API}/v1/library/ma-crossover-crypto/fork").mock(
        return_value=Response(
            201,
            json={
                "strategy_id": "str_forked",
                "name": "MA Crossover",
                "source_slug": "ma-crossover-crypto",
                "variant_id": "v-default",
                "is_default_variant": True,
            },
        )
    )
    fork_env = (
        OUTCOMES["keel_library_fork"]
        .handler({"slug": "ma-crossover-crypto"}, ToolContext())
        .to_envelope()
    )
    sid = fork_env["strategy_id"]
    checkout = OUTCOMES["keel_strategy_checkout"]
    assert "strategy_id" in checkout.input_schema["properties"]
    assert sid.startswith("str_")


def test_missing_slug_is_a_typed_error():
    from keel.errors import KeelError

    for name in ("keel_library_get", "keel_library_fork"):
        with pytest.raises(KeelError):
            OUTCOMES[name].handler({}, ToolContext())


# ─── The light-push boundary ────────────────────────────────────────────


def test_first_session_route_carries_the_one_library_line():
    from keel.tools.outcomes.status import _workflow_routes

    routes = _workflow_routes(live_read_loaded=False, live_write_loaded=False)
    first = next(r for r in routes if r["name"] == "first_session")
    assert any("keel_library_list" in line for line in first["next"])


def test_fork_description_carries_the_new_user_default():
    desc = OUTCOMES["keel_library_fork"].description
    assert "ma-crossover-crypto" in desc


def test_strategy_creation_skill_stays_library_free():
    """The from-thesis path is untouched — the founder's over-indexing
    boundary. A library mention appearing here is a ruling violation,
    not a feature."""
    skill = (SDK_ROOT / "keel" / "skills" / "strategy-creation" / "SKILL.md").read_text()
    assert "keel_library" not in skill


def test_fork_and_iterate_skill_gained_the_verbs():
    skill = (SDK_ROOT / "keel" / "skills" / "strategy-fork-and-iterate" / "SKILL.md").read_text()
    assert "keel_library_fork" in skill
    assert "keel_library_list" in skill


# ─── Q-1843: the facts the description promises reach the model ────────


def _real_entry_payload(slug: str) -> dict:
    """`GET /v1/library/{slug}` for a REAL entry of the generated package,
    shaped as `routers/library.py::get_library_entry` shapes it (the fields
    this tool reads), so the facts line is proven against real data."""
    root = SDK_ROOT.parents[2] / "libs/strategy_library/data/entries" / slug
    entry = json.loads((root / "entry.json").read_text())
    grid = json.loads((root / "config_grid.json").read_text())
    return {
        "slug": slug,
        "name": entry.get("name") or slug,
        "headline": entry["headline"],
        "entry_version": str(entry.get("artifact_version")),
        "data_as_of": entry["data_as_of"],
        "stale": True,
        "backtest_window": entry["backtest_window"],
        "variants": [
            {
                "variant_id": v["variant_id"],
                "label": v.get("label"),
                "metrics": v.get("metrics"),
                "is_default": bool(v.get("is_default")),
                "publishable": bool(v.get("publishable")),
                "forkable": bool(v.get("forkable")),
            }
            for v in grid["variants"]
        ],
        "graph": json.loads((root / "graph.json").read_text()),
    }


@respx.mock
def test_get_puts_the_verified_facts_in_the_text_the_model_reads(_api_env):
    """R3 probe: "keel_library_get promises headline metrics, window,
    freshness, variants — it returned only the pipeline structure". The
    envelope carried them; the text block (the model's only channel on
    claude.ai) was the view's markdown, which draws only the structure.

    SEED (2026-09-23, reverted): `library_facts_line` returning None — this
    arm reds; the control below stays green."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    payload = _real_entry_payload("adx-trend-crypto")
    # Non-vacuity: the real entry carries every fact the line must say.
    assert payload["headline"]["trades"] > 0 and len(payload["variants"]) > 3
    respx.get(f"{API}/v1/library/adx-trend-crypto").mock(return_value=Response(200, json=payload))
    env = (
        OUTCOMES["keel_library_get"]
        .handler({"slug": "adx-trend-crypto"}, ToolContext())
        .to_envelope()
    )
    text = view_tool_result(json.dumps(env), "keel_library_get").content[0].text
    (facts,) = [line for line in text.splitlines() if line.startswith("facts: ")]

    # Expected text is derived from the entry on disk (a library refresh moves
    # these numbers), with the formatting written out independently here.
    def day(iso: str) -> str:
        d = date.fromisoformat(iso[:10])
        return f"{d.strftime('%b')} {d.day}, {d.year}"

    w, h = payload["backtest_window"], payload["headline"]
    assert f"verified run {day(w['start'])} – {day(w['end'])}" in facts
    assert (
        f"Sharpe {h['sharpe']:.2f} · return +{h['total_return_pct']:.1f}% · "
        f"max drawdown −{h['max_drawdown_pct']:.1f}% · {h['trades']:,} trades"
    ) in facts
    assert f"data as of {day(payload['data_as_of'])} (stale)" in facts
    assert f"{len(payload['variants'])} variants — " in facts
    assert "(default)" in facts or "; default " in facts
    # The structure is still the view — the facts ride beside it.
    assert env["view"]["size"] == "structure"


def test_facts_line_names_only_variants_a_fork_can_take():
    """Q-2498: the facts line steered agents to its best-Sharpe variants, and
    keel-api refused 14 of the 54 it named. A variant marked `forkable: false`
    is counted but never named."""
    from keel.tools.outcomes.library import MAX_FACT_VARIANTS, library_facts_line

    payload = _real_entry_payload("momentum-funding-hyperliquid")
    ranked = sorted(
        (v for v in payload["variants"] if not v["is_default"]),
        key=lambda v: -v["metrics"]["sharpe_ratio"],
    )
    best = ranked[0]
    # Non-vacuity: with every variant forkable, the best one is named.
    assert all(v["forkable"] for v in payload["variants"])
    assert (best["label"] or best["variant_id"]) + ":" in library_facts_line(payload)
    best["forkable"] = False
    facts = library_facts_line(payload)
    assert (best["label"] or best["variant_id"]) + ":" not in facts
    assert f"{len(payload['variants'])} variants — " in facts
    assert facts.count(": Sharpe") == MAX_FACT_VARIANTS


def test_listed_variant_projection_carries_forkable():
    from keel.tools.outcomes._listed_projection import LISTED_LIBRARY_VARIANT_FIELDS

    assert "forkable" in LISTED_LIBRARY_VARIANT_FIELDS


def test_control_an_entry_with_no_facts_carries_no_facts_line():
    from keel.tools.outcomes.library import library_facts_line

    assert library_facts_line({"slug": "x", "name": "X"}) is None
    assert library_facts_line(None) is None


# ─── Q-1901: a default library_get makes zero price reads ───────────────


@respx.mock
def test_get_is_one_package_read_with_no_hold_line(_api_env):
    """Founder, 2026-09-23: no BTC/ETH/SOL hold read on every call. The tool
    makes ONE request — the entry — without `references`, and projects no
    hold line even when an older keel-api sends one unasked.

    SEED (2026-09-23, reverted): the handler re-projecting
    `result["reference"]` into `extra["reference"]` → this arm reds on the
    envelope assertion while the request-count assertions above it hold
    (the SDK never asks for the read; keel-api's default owns it, guarded in
    services/keel-api/tests/test_library_reference.py)."""
    payload = _real_entry_payload("adx-trend-crypto")
    # An older keel-api computed and sent this unasked.
    payload["reference"] = {"label": "BTC hold", "basis": "price only", "ret_pct": 12.3}
    route = respx.route(host="api.test.keel").mock(return_value=Response(200, json=payload))
    env = (
        OUTCOMES["keel_library_get"]
        .handler({"slug": "adx-trend-crypto"}, ToolContext())
        .to_envelope()
    )
    # ONE read, the entry itself, never asking for references.
    assert route.call_count == 1
    (call,) = route.calls
    assert call.request.url.path == "/v1/library/adx-trend-crypto"
    assert "references" not in call.request.url.params
    assert "reference" not in env
    # Non-vacuity: the facts line is present (the real entry carries facts),
    # it simply names no hold.
    assert env["library_facts"] and "Sharpe" in env["library_facts"]
    assert "BTC" not in env["library_facts"]
