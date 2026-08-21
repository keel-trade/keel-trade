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
    assert env["hero_url"].endswith("/strategies/str_new1")
    assert env["share_url"] is None

    # created_via is ALWAYS sent explicitly (server would default to "app")
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
    skill = (SDK_ROOT / "keel" / "skills" / "strategy-creation.md").read_text()
    assert "keel_library" not in skill


def test_fork_and_iterate_skill_gained_the_verbs():
    skill = (SDK_ROOT / "keel" / "skills" / "strategy-fork-and-iterate.md").read_text()
    assert "keel_library_fork" in skill
    assert "keel_library_list" in skill
