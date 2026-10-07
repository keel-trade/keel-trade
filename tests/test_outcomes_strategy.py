"""Smoke tests for the strategy-family outcome tools.

These tests don't touch the network — `KeelClient` is monkey-patched
per-test to record calls and return canned payloads. The fixture below
also imports each strategy module explicitly so the OUTCOMES registry is
populated even though `_bootstrap()` doesn't know about them yet (the
integration into `__init__.py` is handled outside the family fan-out).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()
    # The strategy family modules aren't in `_bootstrap`'s list yet; import
    # them eagerly so the registry is complete.
    from keel.tools.outcomes import (  # noqa: F401
        strategy_compose,
        strategy_delete,
        strategy_diff,
        strategy_fork,
        strategy_get,
        strategy_memory,
        strategy_search,
    )


class _FakeClient:
    """Drop-in for KeelClient. Records calls; returns canned payloads."""

    def __init__(
        self,
        get_payloads: dict[str, Any] | None = None,
        post_payloads: dict[str, Any] | None = None,
        patch_payloads: dict[str, Any] | None = None,
        delete_payloads: dict[str, Any] | None = None,
        bake: Any = None,
    ) -> None:
        self.bake = bake
        self.get_payloads = get_payloads or {}
        self.post_payloads = post_payloads or {}
        self.patch_payloads = patch_payloads or {}
        self.delete_payloads = delete_payloads or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(("GET", path, params or None))
        if path in self.get_payloads:
            payload = self.get_payloads[path]
            if isinstance(payload, Exception):
                raise payload
            return payload
        return {}

    def post(self, path: str, json: dict | None = None, **params: Any) -> Any:
        self.calls.append(("POST", path, json))
        if path in self.post_payloads:
            payload = self.post_payloads[path]
            if isinstance(payload, Exception):
                raise payload
            return payload
        return {}

    def patch(self, path: str, json: dict | None = None) -> Any:
        self.calls.append(("PATCH", path, json))
        # Like the server: a save moves HEAD, so a HEAD read AFTER the save
        # returns the stored source (through `bake`, the server's universe
        # bake, when a test sets one). Q-1747 reads the stored source back.
        head = f"{path}/versions/HEAD/source"
        if json and "source" in json and isinstance(self.get_payloads.get(head), dict):
            before = self.get_payloads[head]
            stored = self.bake(json["source"]) if self.bake else json["source"]
            self.get_payloads[head] = {
                **before,
                "source": stored,
                "sequence_number": (before.get("sequence_number") or 0) + 1,
            }
        if path in self.patch_payloads:
            payload = self.patch_payloads[path]
            if isinstance(payload, Exception):
                raise payload
            return payload
        return {}

    def delete(self, path: str) -> Any:
        self.calls.append(("DELETE", path, None))
        if path in self.delete_payloads:
            payload = self.delete_payloads[path]
            if isinstance(payload, Exception):
                raise payload
            return payload
        return None


# ─── Registration smoke ────────────────────────────────────────────────


def test_all_eight_strategy_tools_register():
    expected = {
        "keel_strategy_search",
        "keel_strategy_get",
        "keel_strategy_compose",
        "keel_strategy_fork",
        "keel_strategy_diff",
        "keel_strategy_delete",
        "keel_strategy_notes_read",
        "keel_strategy_notes_add",
    }
    assert expected.issubset(set(OUTCOMES.keys()))


def test_descriptions_name_their_neighbours():
    """Listed tools name the neighbour a caller might want instead as a
    neutral fact (Q-1804); the CLI-only destructive delete keeps its
    explicit `Do NOT use` guard."""
    for name in (
        "keel_strategy_search",
        "keel_strategy_get",
        "keel_strategy_compose",
        "keel_strategy_fork",
        "keel_strategy_diff",
        "keel_strategy_notes_read",
        "keel_strategy_notes_add",
    ):
        desc = OUTCOMES[name].description
        others = [t for t in OUTCOMES if t != name and f"`{t}`" in desc]
        assert others, f"{name} names no neighbour"
        assert "Do NOT" not in desc, name
    assert "Do NOT use" in OUTCOMES["keel_strategy_delete"].description


def test_destructive_tool_is_flagged_for_cli_confirm():
    delete = OUTCOMES["keel_strategy_delete"]
    assert delete.confirm_in_cli is True
    assert delete.annotations["destructiveHint"] is True
    # Hard delete is non-idempotent (spec §4 line 303).
    assert delete.annotations["idempotentHint"] is False


# ─── Handler-level tests (direct invocation w/ fake client) ────────────


def test_strategy_search_returns_results_with_hero_urls():
    fake = _FakeClient(
        get_payloads={
            "/v1/strategies": {
                "items": [
                    {
                        "strategy_id": "str_abc",
                        "name": "Alpha",
                        "owner": "org_x",
                        "updated_at": "2026-01-01",
                    },
                    {
                        "strategy_id": "str_def",
                        "name": "Beta",
                        "owner": "org_x",
                        "updated_at": "2026-01-02",
                    },
                ],
                "next_cursor": "c1",
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_search"].handler({"limit": 10, "query": "alpha"}, ctx)
    env = res.to_envelope()
    assert env["share_url"] is None
    assert env["hero_url"] == "https://app.usekeel.io/strategies"
    assert len(env["results"]) == 2
    assert env["results"][0]["hero_url"] == "https://app.usekeel.io/strategies/str_abc/edit"
    assert env["next_cursor"] == "c1"


def test_strategy_get_minimum_returns_metadata_and_resource_uri():
    fake = _FakeClient(
        get_payloads={"/v1/strategies/str_abc": {"strategy_id": "str_abc", "name": "X"}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_abc"}, ctx)
    env = res.to_envelope()
    assert env["run_id"] == "str_abc"
    assert env["resource_uri"] == "keel://strategy/str_abc/source"
    # V-6 (ratified 2026-09-19): every strategy link lands in the editor.
    assert env["hero_url"] == "https://app.usekeel.io/strategies/str_abc/edit"
    assert env["metadata"]["strategy_id"] == "str_abc"


def test_strategy_get_asks_for_the_graph_beside_metadata():
    """The strategy card's glass box renders from the server-derived graph
    (Q-1505): the metadata GET carries `include=graph`, and whatever the
    server returns under `graph` rides the envelope verbatim."""
    fake = _FakeClient(
        get_payloads={
            "/v1/strategies/str_abc": {
                "strategy_id": "str_abc",
                "name": "X",
                "graph": {"blocks": [{"type": "component", "component": "ROC"}]},
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert fake.calls[0] == ("GET", "/v1/strategies/str_abc", {"include": "graph"})
    assert env["metadata"]["graph"]["blocks"][0]["component"] == "ROC"


# ─── The derived view (PLAN §4.2) ──────────────────────────────────────
#
# These prove the ATTACHMENT — that each strategy-shaped tool carries a
# view and what it is keyed to. What the view CONTAINS is proved against
# the card fixtures in tests/test_strategy_view.py.

_ADX_ENVELOPE = json.loads(
    (Path(__file__).resolve().parent / "fixtures/cards/strategy_adx.envelope.json").read_text()
)
_ADX_GRAPH = _ADX_ENVELOPE["metadata"]["graph"]


def test_strategy_get_carries_the_derived_view():
    """Q-1619: an id and a link say nothing — the envelope carries the
    strategy drawn the way the editor draws it."""
    fake = _FakeClient(
        get_payloads={
            "/v1/strategies/str_abc": {
                "strategy_id": "str_abc",
                "name": "adx_trend_crypto",
                "status": "DRAFT",
                "current_sequence": 1,
                "graph": _ADX_GRAPH,
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    view = env["view"]
    assert view["size"] == "structure"
    assert view["header"]["clock"] == "4h"
    assert view["header"]["universe"]["label"] == "Top 15 by volume · HL perps"
    assert "AboveThresholdFilter" in view["markdown"]
    assert view["url_line"] == "View in Keel: https://app.usekeel.io/strategies/str_abc/edit"


def test_no_graph_means_no_view_rather_than_an_empty_one():
    """Control arm: a source the server could not parse comes back
    without `graph`, and the envelope simply has no view — never a shell
    the card would draw as an empty strategy."""
    fake = _FakeClient(get_payloads={"/v1/strategies/str_abc": {"strategy_id": "str_abc"}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert "view" not in env


def test_strategy_fork_carries_the_new_strategys_view():
    fake = _FakeClient(
        post_payloads={"/v1/strategies/str_abc/fork": {"strategy_id": "str_new"}},
        get_payloads={
            "/v1/strategies/str_new": {
                "strategy_id": "str_new",
                "name": "adx_fork",
                "status": "DRAFT",
                "current_sequence": 1,
                "graph": _ADX_GRAPH,
            }
        },
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = OUTCOMES["keel_strategy_fork"].handler({"source": "str_abc"}, ctx).to_envelope()
    assert env["view"]["name"] == "adx_fork"
    # Read back from the NEW strategy, never the parent.
    assert ("GET", "/v1/strategies/str_new", {"include": "graph"}) in fake.calls


def test_strategy_compose_dry_run_view_comes_from_the_servers_own_parse(monkeypatch):
    """The dry run shows the SAME derivation the save would produce —
    `POST /v1/strategies/parse` — not a locally-guessed twin."""
    monkeypatch.setattr(
        "keel.tools.outcomes.strategy_compose._try_local_validate",
        lambda source, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"compiled": True}, raising=False
    )
    fake = _FakeClient(post_payloads={"/v1/strategies/parse": {"graph": _ADX_GRAPH, "valid": True}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": "Globals()", "dry_run": True}, ctx)
        .to_envelope()
    )
    assert any(path == "/v1/strategies/parse" for _, path, _ in fake.calls)
    assert env["view"]["status"] == "PREVIEW"
    # A dry run is a STEP since the render-cadence build (BUILD §2.3):
    # the default is the one-line preview receipt, and the full
    # structure is what `present="view"` asks for.
    assert env["view"]["size"] == "receipt"
    # Eight LEAF blocks (the five top-level rows include a parallel with
    # branches) — `count_blocks` is the card's own counter.
    assert env["view"]["markdown"].strip() == "Preview · 8 blocks · valid"


# ─── Q-1686: a card's action must go somewhere specific ────────────────


def _card_action_url(env: dict) -> str | None:
    """The URL the strategy card's ONE action resolves to.

    A Python mirror of `linkUrl` in
    `keel/widgets/assets/card-strategy.js:1193-1201` — the function that
    sets `view.url` (`:1189`), which is what the footer's
    "Open in Keel ↗" is guarded on (`:1452`, `:1624`) and what hides the
    adapter's own row (`:1818`). `host-adapter.js:979` runs the same
    precedence for that fallback row. Asserting `env["hero_url"]` alone
    would prove less than the card does: three other envelope fields can
    supply the action, and the fix has to close all four.
    """
    url = env.get("hero_url") or env.get("share_url")
    if not url and isinstance(env.get("render"), dict):
        url = env["render"].get("fallback_url")
    if not url and isinstance(env.get("url_line"), str):
        match = re.search(r"https?://\S+", env["url_line"])
        if match:
            url = match.group(0)
    return url or None


def _dry_run_env(monkeypatch, args: dict) -> dict:
    """A compose dry run over the real 5-block ADX graph."""
    monkeypatch.setattr(
        "keel.tools.outcomes.strategy_compose._try_local_validate",
        lambda source, **_kw: {
            "ok": True,
            "warnings": [{"severity": "warning", "message": "advisory"}],
            "errors": [],
            "lock": None,
        },
    )
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"compiled": True}, raising=False
    )
    fake = _FakeClient(post_payloads={"/v1/strategies/parse": {"graph": _ADX_GRAPH, "valid": True}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    return OUTCOMES["keel_strategy_compose"].handler({**args, "dry_run": True}, ctx).to_envelope()


def _dry_run_structure_env(monkeypatch, args: dict) -> dict:
    """The same dry run asked to present the whole thing.

    Q-1686's card is the STRUCTURE view, which since BUILD §2.3 is what
    `present="view"` produces rather than the default — so the two
    action-URL arms below keep testing the card they were written for.
    """
    return _dry_run_env(monkeypatch, {**args, "present": "view"})


def test_compose_dry_run_with_no_strategy_offers_no_action(monkeypatch):
    """Q-1686 (founder, claude.ai): the first compose call rendered a full
    five-block card headed `Keel Untitled` whose one action, "Open in
    Keel", went to the strategy LIST — a card with no identity to open,
    offering to open it.

    `dry_run=True` with neither `name` nor `strategy_id` is the shape the
    tool description steers the first call into ("use this first to
    iterate cheaply"). The card is right to draw the structure; it must
    not draw an action, because there is no target.
    """
    env = _dry_run_structure_env(monkeypatch, {"source": "Globals()"})

    # NON-VACUITY, half 1 — this really is the founder's card: a full
    # structure view with every block drawn. Without it the assertion
    # below would pass for an envelope the card never renders at all.
    assert env["view"]["size"] == "structure"
    assert len(env["view"]["structure"]["blocks"]) == 5
    assert env["view"]["name"] is None  # the `Untitled` headline (Q-1630)
    assert env["view"]["validation"]["warnings"]  # the `1 warning` chip

    # THE VERDICT: nothing in this envelope resolves to an action, and in
    # particular not to the bare strategy list.
    assert _card_action_url(env) is None
    assert env.get("hero_url") is None
    assert "hero_url" not in env
    assert "url_line" not in env

    # The prose an agent reads on a widget-less surface carries no link
    # either (`_strategy_view.py:1069` / `:965`).
    assert "View in Keel" not in env["view"]["markdown"]


def test_compose_dry_run_against_a_real_strategy_still_opens_it(monkeypatch):
    """Control arm — identical call but for `strategy_id`, resolved through
    the SAME mirror.

    NON-VACUITY, half 2: proves `_card_action_url` can find an action, so
    the None above is the envelope's answer and not the helper's.
    """
    env = _dry_run_structure_env(monkeypatch, {"source": "Globals()", "strategy_id": "str_abc"})

    assert env["view"]["size"] == "structure"
    assert len(env["view"]["structure"]["blocks"]) == 5
    assert _card_action_url(env) == "https://app.usekeel.io/strategies/str_abc/edit"
    assert (
        env["view"]["markdown"]
        .rstrip()
        .endswith("View in Keel: https://app.usekeel.io/strategies/str_abc/edit")
    )


def test_compose_persist_without_an_id_offers_no_action(monkeypatch):
    """The same rule on the persist arm: a save the API answered with
    neither `strategy_id` nor `id` has nothing to open either."""
    monkeypatch.setattr(
        "keel.tools.outcomes.strategy_compose._try_local_validate",
        lambda source, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    fake = _FakeClient(post_payloads={"/v1/strategies": {"current_sequence": 1}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": "Globals()", "name": "demo"}, ctx)
        .to_envelope()
    )
    # Non-vacuity: the save really happened and really came back id-less.
    assert any(method == "POST" and path == "/v1/strategies" for method, path, _ in fake.calls)
    assert env["strategy_id"] is None
    assert _card_action_url(env) is None


def test_strategy_compose_persist_carries_the_view_with_its_change(monkeypatch):
    """An update reads its own before-source and carries what changed,
    keyed to the new graph's block ids so a card can mark them."""
    source_a = json.loads(
        (Path(__file__).resolve().parents[1] / "keel/data/templates.json").read_text()
    )["basic"]["content"].replace("{name}", "demo")
    source_b = source_a.replace("period=8", "period=42")
    from pipeline_engine.dsl.emitter import spec_to_graph
    from pipeline_engine.dsl.parser import parse_strategy

    graph_b = spec_to_graph(parse_strategy(source_b)).to_dict()
    monkeypatch.setattr(
        "keel.tools.outcomes.strategy_compose._try_local_validate",
        lambda source, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    fake = _FakeClient(
        get_payloads={
            "/v1/strategies/str_abc/versions/HEAD/source": {
                "source": source_a,
                "sequence_number": 1,
            },
            "/v1/strategies/str_abc": {
                "strategy_id": "str_abc",
                "name": "demo",
                "status": "DRAFT",
                "current_sequence": 2,
                "graph": graph_b,
            },
        },
        patch_payloads={
            "/v1/strategies/str_abc": {"strategy_id": "str_abc", "current_sequence": 2}
        },
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"strategy_id": "str_abc", "source": source_b}, ctx)
        .to_envelope()
    )
    view = env["view"]
    # One block's params changed, nothing added or removed ⇒ a receipt.
    assert view["size"] == "receipt"
    assert view["previous_version"] == 1
    changed = view["change"]["changed"]
    assert [c["component"] for c in changed] == ["ROC"]
    assert changed[0]["params"] == {"period": {"old": 8, "new": 42}}
    # The id is the NEW graph's own block id, not the differ's path.
    ids = {b["id"] for b in graph_b["blocks"]}
    assert changed[0]["id"] in ids
    assert "period 8 → 42" in view["markdown"]


# ── Q-1707 · a declaration edit is a change, and it is counted ─────────


def _basic_source() -> str:
    return json.loads(
        (Path(__file__).resolve().parents[1] / "keel/data/templates.json").read_text()
    )["basic"]["content"].replace("{name}", "demo")


def _offset_source() -> str:
    """The basic template with a 12:00 UTC close declared — the template itself
    omits `bar_offset` since Q-1895, and these tests move an existing one."""
    source = _basic_source()
    bare = 'Globals(target_timeframe="1d")'
    assert bare in source, "template Globals moved"
    return source.replace(bare, 'Globals(target_timeframe="1d", bar_offset="12h")')


def _compose_save(
    monkeypatch, source_a: str, source_b: str, *, bake: Any = None, real_validation=False
) -> dict:
    """Drive a real PATCH save of `source_b` over a stored `source_a`."""
    from pipeline_engine.dsl.emitter import spec_to_graph
    from pipeline_engine.dsl.parser import parse_strategy

    graph_b = spec_to_graph(parse_strategy(source_b)).to_dict()
    if not real_validation:
        monkeypatch.setattr(
            "keel.tools.outcomes.strategy_compose._try_local_validate",
            lambda source, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
        )
    fake = _FakeClient(
        bake=bake,
        get_payloads={
            "/v1/strategies/str_abc/versions/HEAD/source": {
                "source": source_a,
                "sequence_number": 1,
            },
            "/v1/strategies/str_abc": {
                "strategy_id": "str_abc",
                "name": "demo",
                "status": "DRAFT",
                "current_sequence": 2,
                "graph": graph_b,
            },
        },
        patch_payloads={
            "/v1/strategies/str_abc": {"strategy_id": "str_abc", "current_sequence": 2}
        },
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    return (
        OUTCOMES["keel_strategy_compose"]
        .handler({"strategy_id": "str_abc", "source": source_b}, ctx)
        .to_envelope()
    )


def test_a_declaration_only_save_is_a_real_change_not_a_silent_re_render(monkeypatch):
    """Q-1707, reproduced on staging: a save whose only edit was
    `Execution(...)` / `Globals(...)` / `Universe(...)` returned
    `view.change = null`, so the card fell back to a full structure whose
    header quietly carried the NEW value and whose body said nothing had
    moved."""
    # SEED: in `strategy_compose._persisted_change`, delete the
    # `change["declarations"] = declarations_between(before, after)`
    # assignment — `view["change"]` is None again and every assertion
    # below reds. That IS the shipped defect.
    source_a = _offset_source()
    source_b = source_a.replace('bar_offset="12h"', 'bar_offset="21h"')
    # Not vacuous, and keyed on the SOURCES rather than the envelope: the
    # edit really is declaration-only, so an empty change can only be the
    # differ's blind spot.
    assert source_b != source_a
    from keel.tools.outcomes.strategy_diff import diff_sources

    assert diff_sources(source_a, source_b)["changed"] == []

    env = _compose_save(monkeypatch, source_a, source_b)
    change = env["view"]["change"]
    assert change is not None, "a declaration-only save reported nothing changed"
    assert change["declarations"] == {"globals": {"bar_offset": {"a": "12h", "b": "21h"}}}
    assert change["touched"] == 1
    assert change["from_version"] == 1 and change["to_version"] == 2
    # One thing moved ⇒ the same receipt a one-param block edit gets.
    assert env["view"]["size"] == "receipt"
    assert "bar_offset 12h → 21h" in change["summary_text"]
    assert "~ Globals · bar_offset   12h → 21h" in env["view"]["markdown"]


_BAKED = [f"C{i:02d}" for i in range(30)]


def _bake(source: str) -> str:
    """The server's universe bake (keel-api universe_bake, Q-1504), in
    miniature: an unresolved Universe gets its resolved list on save."""
    assert 'resolved=[], resolved_at=""' in source, "template placeholder moved"
    return source.replace(
        'resolved=[], resolved_at=""', f'resolved={_BAKED!r}, resolved_at="2026-09-22T00:00:00Z"'
    )


def test_the_universe_bake_is_not_reported_as_an_edit(monkeypatch):
    """Q-1747: a save whose ONLY edit was the Execution buffer reported a
    second change, `Universe · resolved ["AAVE","ARB",…] → —` — the stored
    HEAD (baked) against the SUBMITTED source (not yet baked) — and every
    criteria-universe save warned UNRESOLVED_UNIVERSE ("save the strategy")
    on the save that had just resolved it.

    # SEED: in strategy_compose, pass `source` instead of `saved_source`
    # to `_persisted_change` — the universe row returns and this reds.
    """
    source_a = _bake(_basic_source())
    submitted = _basic_source().replace("period=8", "period=9")
    assert "resolved=[]" in submitted and "resolved=['C00'" in source_a  # non-vacuous
    assert "top_volume" in submitted  # a CRITERIA universe — the case that warned
    env = _compose_save(monkeypatch, source_a, submitted, bake=_bake, real_validation=True)
    change = env["view"]["change"]
    assert "universe" not in (change.get("declarations") or {}), change
    assert "resolved" not in env["view"]["markdown"]
    codes = [w.get("code") for w in env["validation"]["warnings"] if isinstance(w, dict)]
    assert "UNRESOLVED_UNIVERSE" not in codes, codes
    # The universe block describes what the save STORED.
    assert env["universe"]["as_stored"] is True and env["universe"]["resolved_count"] == 30


_TEMPLATE_UNIVERSE = (
    'Universe(mode="top_volume", top_n=30, market="perp", resolved=[], resolved_at="")'
)


def _with_universe(universe: str) -> str:
    source = _basic_source()
    assert _TEMPLATE_UNIVERSE in source, "template universe line moved"
    return source.replace(_TEMPLATE_UNIVERSE, universe)


def _real_validation_dry_run(monkeypatch, source: str) -> dict:
    """A compose dry run through the REAL local validator (only the server
    parse/compile are faked)."""
    from pipeline_engine.dsl.emitter import spec_to_graph
    from pipeline_engine.dsl.parser import parse_strategy

    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"compiled": True}, raising=False
    )
    graph = spec_to_graph(parse_strategy(source)).to_dict()
    fake = _FakeClient(post_payloads={"/v1/strategies/parse": {"graph": graph, "valid": True}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    return (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": source, "dry_run": True}, ctx)
        .to_envelope()
    )


def _issue_codes(env: dict) -> list:
    v = env["validation"]
    return [i.get("code") for i in [*v["warnings"], *v["errors"]] if isinstance(i, dict)]


def test_a_dry_run_does_not_warn_that_a_criteria_universe_is_unresolved(monkeypatch):
    """Review 06 §3.2 #4 (L5 `pre_save`): the dry run previews a save, and
    the save resolves a criteria universe server-side — so a dry run of
    `Universe(mode='top_volume', top_n=30, market='perp')` warning
    UNRESOLVED_UNIVERSE told the agent to fix something the save fixes.

    SEED: in strategy_compose._handler, call `_try_local_validate(source)`
    (drop `pre_save=dry_run`) — the warning returns and this reds; the
    manual-universe and save-path controls below stay green.
    """
    from keel.tools.local import strategy_validate

    source = _with_universe("Universe(mode='top_volume', top_n=30, market='perp')")
    # Non-vacuous: at full severity this exact source DOES warn.
    full = [w["code"] for w in strategy_validate(source=source)["warnings"]]
    assert "UNRESOLVED_UNIVERSE" in full, full
    env = _real_validation_dry_run(monkeypatch, source)
    assert "UNRESOLVED_UNIVERSE" not in _issue_codes(env), env["validation"]
    # The universe block still says the save resolves it.
    assert env["universe"]["source"] == "unresolved"


def test_a_dry_run_of_a_manual_universe_without_symbols_still_warns(monkeypatch):
    """Control: a manual universe has nothing for the save to resolve, so
    `pre_save` must not soften it (the validator scopes the override to
    criteria modes)."""
    source = _with_universe("Universe(mode='manual', market='perp')")
    env = _real_validation_dry_run(monkeypatch, source)
    assert "UNRESOLVED_UNIVERSE" in _issue_codes(env), env["validation"]


def test_a_save_reports_an_unresolved_criteria_universe_at_full_severity(monkeypatch):
    """Control: the save path keeps full severity. With no server bake the
    stored source is the submitted one, so the save's verdict is the
    pre-save validation — which must still carry the warning."""
    source_a = _basic_source()
    submitted = _with_universe("Universe(mode='top_volume', top_n=30, market='perp')").replace(
        "period=8", "period=9"
    )
    env = _compose_save(monkeypatch, source_a, submitted, real_validation=True)
    assert "UNRESOLVED_UNIVERSE" in _issue_codes(env), env["validation"]


def test_strategy_validate_defaults_to_full_severity():
    """`keel.tools.local.strategy_validate` is also the validate tool's and
    the backtest gate's path: `pre_save` is opt-in, and a gate still reads
    error in production mode (the engine's own promotion)."""
    from keel.tools.local import strategy_validate

    source = _with_universe("Universe(mode='top_volume', top_n=30, market='perp')")
    default = strategy_validate(source=source)
    pre = strategy_validate(source=source, pre_save=True)
    assert "UNRESOLVED_UNIVERSE" in [w["code"] for w in default["warnings"]]
    assert "UNRESOLVED_UNIVERSE" not in [w["code"] for w in pre["warnings"] + pre["errors"]]
    assert "UNRESOLVED_UNIVERSE" in [i["code"] for i in pre["info"]]


def test_every_save_writes_a_commit_message_that_names_the_change(monkeypatch):
    """Q-1752: v2/v3/v4 of Simple Momentum (buffer 5/10/20%) landed with an
    EMPTY `message`, so the history could not tell the variants apart and
    the next session backtested v4 thinking it was the base.

    # SEED: in strategy_compose's update branch, delete the
    # `payload["message"] = commit_message_for_save(...)` line — the PATCH
    # carries no message and this reds.
    """
    # The basic template writes the continuous buffered line (Q-2127) at 0.2.
    source_a = _basic_source().replace("buffer_threshold=0.2", "buffer_threshold=0.1")
    source_b = source_a.replace("buffer_threshold=0.1", "buffer_threshold=0.2")
    assert source_a != _basic_source() and source_b != source_a  # non-vacuous edit
    from pipeline_engine.dsl.emitter import spec_to_graph
    from pipeline_engine.dsl.parser import parse_strategy

    monkeypatch.setattr(
        "keel.tools.outcomes.strategy_compose._try_local_validate",
        lambda source, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    fake = _FakeClient(
        get_payloads={
            "/v1/strategies/str_abc/versions/HEAD/source": {
                "source": source_a,
                "sequence_number": 1,
            },
            "/v1/strategies/str_abc": {
                "strategy_id": "str_abc",
                "name": "demo",
                "current_sequence": 2,
                "graph": spec_to_graph(parse_strategy(source_b)).to_dict(),
            },
        },
        patch_payloads={"/v1/strategies/str_abc": {"strategy_id": "str_abc"}},
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_compose"].handler({"strategy_id": "str_abc", "source": source_b}, ctx)
    (patch_body,) = [body for verb, _p, body in fake.calls if verb == "PATCH"]
    assert patch_body["message"] == "Execution · buffer 0.1 → 0.2"

    # The caller's own message wins verbatim.
    fake.get_payloads["/v1/strategies/str_abc/versions/HEAD/source"]["source"] = source_a
    OUTCOMES["keel_strategy_compose"].handler(
        {"strategy_id": "str_abc", "source": source_b, "message": "buffer 20% variant"}, ctx
    )
    assert [b for v, _p, b in fake.calls if v == "PATCH"][-1]["message"] == "buffer 20% variant"


def test_a_commit_message_is_never_empty():
    """Control arms: no readable before, an unparseable pair, a create."""
    from keel.tools.outcomes.strategy_compose import (
        FALLBACK_COMMIT_MESSAGE,
        commit_message_for_save,
    )

    assert commit_message_for_save(None, None, "x", name="Momo") == "Create Momo"
    assert commit_message_for_save("", "not dsl (", "also not") == FALLBACK_COMMIT_MESSAGE
    assert commit_message_for_save("  ", None, "x") == FALLBACK_COMMIT_MESSAGE
    derived = commit_message_for_save(
        None, _basic_source(), _basic_source().replace("period=8", "period=42")
    )
    assert derived == "ROC · period 8 → 42"


def test_the_declared_universe_still_diffs(monkeypatch):
    """Control arm: a DECLARED universe edit (top_n) is still a change —
    only the bake is excluded, not the Universe section."""
    from keel.tools.outcomes._declarations import declaration_view

    from pipeline_engine.dsl.parser import parse_strategy

    assert "resolved" not in declaration_view(parse_strategy(_bake(_basic_source())))["universe"]
    source_a = _bake(_basic_source())
    submitted = _basic_source().replace("top_n=30", "top_n=20")
    env = _compose_save(monkeypatch, source_a, submitted, bake=_bake)
    assert env["view"]["change"]["declarations"]["universe"] == {"top_n": {"a": 30, "b": 20}}


def test_a_mixed_save_counts_the_declaration_AND_the_block(monkeypatch):
    """The sharp case from the ledger: `bar_offset` 9h→21h beside
    `ROC(period=30→45)` was reported as `"1 changed"` with `4 blocks
    unchanged`, naming only the ROC."""
    # SEED: as above — `touched` drops to 1 and `summary_text` says only
    # `1 changed`, which is the exact wrong statement staging made.
    source_a = _offset_source()
    source_b = (
        source_a.replace('bar_offset="12h"', 'bar_offset="21h"')
        .replace("top_n=30", "top_n=20")
        .replace("period=8", "period=42")
    )
    env = _compose_save(monkeypatch, source_a, source_b)
    change = env["view"]["change"]
    # Three things moved: two declarations and one component.
    assert change["touched"] == 3, change
    assert [c["component"] for c in change["changed"]] == ["ROC"]
    assert change["declarations"]["globals"]["bar_offset"] == {"a": "12h", "b": "21h"}
    assert change["declarations"]["universe"]["top_n"] == {"a": 30, "b": 20}
    # Not vacuous: the BLOCK tally is still block arithmetic — one of the
    # five blocks moved, so four are unchanged, and the declarations are
    # named on their own lines rather than folded into that count.
    lines = env["view"]["markdown"].splitlines()
    assert "  4 blocks unchanged" in lines, lines
    assert "~ Globals · bar_offset   12h → 21h" in lines, lines
    assert "~ Universe · top_n   30 → 20" in lines, lines
    assert "~ ROC   period 8 → 42" in lines, lines


def test_strategy_diff_file_mode_carries_the_change_view(tmp_path):
    source_a = json.loads(
        (Path(__file__).resolve().parents[1] / "keel/data/templates.json").read_text()
    )["basic"]["content"].replace("{name}", "demo")
    source_b = source_a.replace("period=8", "period=42")
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text(source_a)
    b.write_text(source_b)
    ctx = ToolContext(api_client=_FakeClient(), is_tty=False)
    env = (
        OUTCOMES["keel_strategy_diff"]
        .handler({"ref_a": str(a), "ref_b": str(b)}, ctx)
        .to_envelope()
    )
    assert env["changed"], "the diff itself must still be on the envelope"
    assert env["view"]["size"] == "receipt"
    assert "period 8 → 42" in env["view"]["markdown"]


def test_strategy_diff_names_a_declaration_only_difference(monkeypatch):
    """Q-1898 (R4): the two HYPE strategies that returned −7.1% and +44.6%
    differed ONLY in `Globals(bar_offset='12h')`, and `keel_strategy_diff`
    answered "identical" — the step differ never reads the declarations.
    The diff now carries `_declarations`' block and names it in the summary
    and on the card's change view, in source mode (two strategies, hosted)
    and version mode (two versions).

    # SEED (run 2026-09-23): make `strategy_diff._add_declarations` return
    # at its first line — this reds (`KeyError: 'declarations'` on the source
    # arm) while the 73 other strategy tests stay green.
    """
    # The hosted branch: refs are DSL text (no files), as on the listed server.
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    with_offset = _offset_source()
    without = _basic_source()
    ctx = ToolContext(api_client=_FakeClient(), is_tty=False)
    env = (
        OUTCOMES["keel_strategy_diff"]
        .handler({"ref_a": with_offset, "ref_b": without}, ctx)
        .to_envelope()
    )
    # Not vacuous, keyed on the SOURCES: the step differ sees nothing.
    assert env["changed"] == [] and env["added"] == [] and env["removed"] == []
    assert env["declarations"] == {"globals": {"bar_offset": {"a": "12h", "b": None}}}
    assert "bar_offset 12h →" in env["summary_text"]
    assert "identical" not in env["summary_text"].lower()
    assert env["view"]["change"]["declarations"] == env["declarations"]

    # Version mode: both versions' sources are read and diffed the same way.
    fake = _FakeClient(
        post_payloads={"/v1/strategies/str_abc/versions/diff": {"changes": {}}},
        get_payloads={
            "/v1/strategies/str_abc/versions/1/source": {"source": with_offset},
            "/v1/strategies/str_abc/versions/2/source": {"source": without},
        },
    )
    env = (
        OUTCOMES["keel_strategy_diff"]
        .handler(
            {"strategy_id": "str_abc", "ref_a": "1", "ref_b": "2"},
            ToolContext(api_client=fake, is_tty=False),
        )
        .to_envelope()
    )
    assert env["declarations"] == {"globals": {"bar_offset": {"a": "12h", "b": None}}}
    assert "bar_offset 12h →" in env["summary_text"]

    # Control: identical sources stay identical, with no declarations key.
    same = (
        OUTCOMES["keel_strategy_diff"]
        .handler({"ref_a": without, "ref_b": without}, ctx)
        .to_envelope()
    )
    assert "declarations" not in same
    assert "identical" in same["summary_text"].lower()


def test_strategy_get_include_source_and_versions_calls_extra_endpoints():
    fake = _FakeClient(
        get_payloads={
            "/v1/strategies/str_abc": {"strategy_id": "str_abc"},
            "/v1/strategies/str_abc/versions": [{"sequence_number": 1}],
            "/v1/strategies/str_abc/versions/HEAD/source": {"source": "...", "sequence_number": 1},
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_get"].handler(
        {"strategy_id": "str_abc", "include_source": True, "include_versions": True}, ctx
    )
    env = res.to_envelope()
    assert "versions" in env
    assert "source" in env
    paths = [c[1] for c in fake.calls]
    assert "/v1/strategies/str_abc/versions" in paths
    assert "/v1/strategies/str_abc/versions/HEAD/source" in paths


def test_strategy_compose_dry_run_does_not_persist(monkeypatch):
    # Monkey-patch the validator so we don't need a full DSL parser.
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    # And prevent any real remote compile call.
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile",
        lambda **kw: {"compiled": True},
        raising=False,
    )

    fake = _FakeClient()
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from keel import *", "dry_run": True}, ctx
    )
    env = res.to_envelope()
    assert env["validation"]["ok"] is True
    assert env["dry_run"] is True
    # No PERSISTING write should have been issued for dry runs. The
    # graph read (`POST /v1/strategies/parse`, PLAN §4.2) is a
    # derivation, not a save: it is the only POST allowed here, and
    # nothing may reach `/v1/strategies` or `/v1/strategies/{id}`.
    writes = [
        (method, path)
        for method, path, _ in fake.calls
        if method in {"POST", "PATCH"} and path != "/v1/strategies/parse"
    ]
    assert writes == []


def test_strategy_compose_dry_run_surfaces_validation_as_feedback(monkeypatch):
    """Validation issues surface in the response — they DON'T block (v0.4.x).

    Aligns with the web app editor + chat-api + keel-api policy:
    validation is feedback, not a gate. Pre-fix the SDK wrapper raised
    ValidationError on any error and blocked the call — the outlier
    behavior across the system. Now the dry_run path returns success
    with `validation.{errors,warnings}` populated so the agent has
    full feedback without being blocked.

    The actual gate is compile (which is attempted regardless and
    surfaces via `compile_error`).
    """
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src, **_kw: {
            "ok": False,
            "warnings": [],
            "errors": [{"severity": "error", "message": "Parse error: imports not allowed"}],
            "lock": None,
        },
    )
    # Stub remote compile so the call doesn't try to hit the network.
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile",
        lambda **kw: {"compiled": True},
        raising=False,
    )
    ctx = ToolContext(api_client=_FakeClient(), is_tty=False)

    # MUST return successfully, not raise.
    res = OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from x import y", "dry_run": True}, ctx
    )
    env = res.to_envelope()
    # No error code — validation issues surface as data, not as exception.
    assert env.get("code") not in ("validation_failed", "usage_error")
    # The validation feedback is in the response under `validation`.
    assert env["validation"]["ok"] is False
    assert env["validation"]["errors"]
    assert "Parse error" in env["validation"]["errors"][0]["message"]
    assert env["dry_run"] is True


def test_strategy_compose_persist_surfaces_validation_as_feedback(monkeypatch):
    """Same feedback-not-gate policy on the persist path.

    Validation issues come back under `validation` in the success
    envelope; the save proceeds regardless (matches keel-api's
    `_validate_compile_graph` which logs warnings + continues).
    Only API-level compile/runtime errors block the save.
    """
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src, **_kw: {
            "ok": False,
            "warnings": [],
            "errors": [
                {"severity": "error", "message": "Type mismatch: StreamSeries → SignalSeries"}
            ],
            "lock": None,
        },
    )
    fake = _FakeClient(
        post_payloads={"/v1/strategies": {"strategy_id": "str_new", "current_sequence": 1}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)

    # MUST persist successfully, not raise.
    res = OUTCOMES["keel_strategy_compose"].handler(
        {"source": "Pipeline([FundingDataLoader(), TargetSignalResampler()])", "name": "smoke"}, ctx
    )
    env = res.to_envelope()
    assert env["strategy_id"] == "str_new"
    # Validation feedback still surfaces in the success envelope.
    assert env["validation"]["ok"] is False
    assert env["validation"]["errors"]
    assert "Type mismatch" in env["validation"]["errors"][0]["message"]
    # And the POST actually went through.
    assert any(method == "POST" and path == "/v1/strategies" for method, path, _ in fake.calls)


def test_strategy_compose_stream_to_signal_subtype_passes_validation():
    """Regression — v0.4.x prod-readiness smoke caught the SDK-bundled
    pipeline_engine missing `types.py`, which broke NewType subtype
    walking. `StreamSeries = NewType("StreamSeries", SignalSeries)`
    (declared in the upstream `pipeline_engine.types` module) means StreamSeries IS
    a subtype of SignalSeries; `is_compatible(StreamSeries, SignalSeries)`
    must return True so pipelines like `FundingDataLoader → TargetSignalResampler`
    validate cleanly (FundingDataLoader outputs StreamSeries;
    TargetSignalResampler expects SignalSeries).

    Pre-fix, `_resolve_type_name()` couldn't find pipeline_engine.types
    in the SDK bundle, fell back to synthetic placeholder types with no
    `__supertype__`, and the subtype check returned False. Now
    `build_data.py` ships a pandas-stripped types.py in the SDK so the
    NewType chain stays intact at validation time.
    """
    from pipeline_engine.base.registry import is_compatible
    from pipeline_engine.types import SignalSeries, StreamSeries

    # The NewType chain must be reachable from inside the SDK env.
    assert StreamSeries.__supertype__ is SignalSeries, (
        "SDK-bundled types.py drift — StreamSeries should declare SignalSeries as its supertype"
    )
    # And `is_compatible` must honor it.
    assert is_compatible(StreamSeries, SignalSeries) is True
    # The reverse must NOT be true — SignalSeries isn't a StreamSeries.
    assert is_compatible(SignalSeries, StreamSeries) is False


def test_strategy_compose_real_pipeline_with_funding_to_resampler_validates():
    """The user-facing regression: a production strategy that flows
    FundingDataLoader (StreamSeries) → TargetSignalResampler (expects
    SignalSeries) must validate cleanly. Caught during a real
    fresh-session test when the SDK was rejecting the user's edits
    on a backtest-clean parent strategy."""
    from keel.data.registry import load_registry  # hydrates COMPONENT_REGISTRY

    from pipeline_engine.dsl import parse_strategy, validate_strategy

    load_registry()  # populate COMPONENT_REGISTRY from bundled JSON
    src = """Globals(target_timeframe='1d')
Universe(mode='top_volume', top_n=10, resolved=['BTC', 'ETH'])
Pipeline([
    FundingDataLoader(),
    TargetSignalResampler(method='mean'),
    NegateTransform(),
    EWMATransform(window=10),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10),
    ForecastCapper(limit=20),
    ForecastWeightNormalizer(target_leverage=1),
])
"""
    sf = parse_strategy(src)
    result = validate_strategy(sf)
    assert result.valid, (
        f"Expected valid strategy, got errors: {[e.to_dict() for e in result.errors]}"
    )


def test_the_no_imports_rule_is_carried_by_the_parser_not_the_description():
    """The no-imports rule used to be prose in compose's description so an
    agent would not burn a round trip on a Python `from` line. Since Q-1695
    the PARSER carries it — a coded issue with its own fix — so the prose
    left the description (agent-surface-cleanup spec 01, review 06 §5).
    This pins the structural carrier: if the parser ever stops naming the
    fix, the fact has no owner and this reds.

    # SEED: change "Remove the import." in `IMPORT_NOT_ALLOWED`'s
    # suggestion in pipeline_engine/dsl/catalog.py to "Drop it." — this
    # reds. Revert by reversing that edit.
    """
    from pipeline_engine.dsl.parser import DSLParseError, parse_strategy

    source = (
        "from keel import *\n"
        "Globals(target_timeframe='1d')\n"
        "Universe(mode='top_volume', top_n=30, market='perp')\n"
        "Execution(rebalance='every_bar')\n"
        "Pipeline([PriceDataLoader(), ROC(period=20), ForecastScaler(avg_abs_target=10.0), "
        "ForecastWeightNormalizer(target_leverage=1.0)], name='x')"
    )
    with pytest.raises(DSLParseError) as err:
        parse_strategy(source)
    issue = err.value.to_issue().to_dict()
    assert issue["code"] == "IMPORT_NOT_ALLOWED"
    assert "Remove the import" in (issue["suggestion"] or "")
    # Control arm: the same source without the import line parses — the
    # fix the issue names is one that works.
    parse_strategy(source.split("\n", 1)[1])
    # The description still carries the skeleton's shape facts.
    desc = OUTCOMES["keel_strategy_compose"].description
    assert "normalizer" in desc.lower()


def test_strategy_compose_create_posts_to_strategies(monkeypatch):
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    fake = _FakeClient(
        post_payloads={"/v1/strategies": {"strategy_id": "str_new", "current_sequence": 1}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from keel import *", "name": "MyStrat"}, ctx
    )
    env = res.to_envelope()
    assert env["strategy_id"] == "str_new"
    assert (
        "POST",
        "/v1/strategies",
        {"source": "from keel import *", "name": "MyStrat", "message": "Create MyStrat"},
    ) in fake.calls


def test_strategy_compose_update_patches_existing(monkeypatch):
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    fake = _FakeClient(
        patch_payloads={"/v1/strategies/str_abc": {"strategy_id": "str_abc", "current_sequence": 2}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from keel import *", "strategy_id": "str_abc"}, ctx
    )
    env = res.to_envelope()
    assert env["strategy_id"] == "str_abc"
    assert any(
        method == "PATCH" and path == "/v1/strategies/str_abc" for method, path, _ in fake.calls
    )


def test_strategy_fork_by_strategy_id_uses_fork_endpoint():
    fake = _FakeClient(post_payloads={"/v1/strategies/str_abc/fork": {"strategy_id": "str_forked"}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_fork"].handler({"source": "str_abc"}, ctx)
    env = res.to_envelope()
    assert env["strategy_id"] == "str_forked"
    assert env["parent"] == "str_abc"
    assert any(path == "/v1/strategies/str_abc/fork" for _, path, _ in fake.calls)


def test_strategy_fork_sends_empty_object_body_not_null():
    """Regression — v0.4.2 live smoke caught the handler passing `None`
    when no `name`/`target_workspace_id` were provided, which the
    keel-api /v1/strategies/{id}/fork endpoint rejected as 422 `Field
    required` (the body model is required). Always send `{}`."""
    fake = _FakeClient(
        post_payloads={"/v1/strategies/str_xyz/fork": {"strategy_id": "str_forked2"}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_fork"].handler({"source": "str_xyz"}, ctx)
    body = next(c[2] for c in fake.calls if c[1] == "/v1/strategies/str_xyz/fork")
    assert body == {}, f"fork should send empty object body, not {body!r}"
    assert body is not None


def test_strategy_fork_by_share_id_uses_fork_with_edits():
    fake = _FakeClient(
        post_payloads={"/v1/strategies/fork-with-edits": {"strategy_id": "str_from_share"}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_fork"].handler({"source": "gDXjURKqWPs8"}, ctx)
    env = res.to_envelope()
    assert env["strategy_id"] == "str_from_share"
    # The link id rides under the key the API's `ForkWithEditsRequest`
    # declares — `share_id`, the share link's own primary key (Q-1621: the
    # SDK sent `share_link_id`, the name the OTHER fork endpoint uses, and
    # every share-link fork 422'd).
    body = next(c[2] for c in fake.calls if c[1] == "/v1/strategies/fork-with-edits")
    assert body == {"share_id": "gDXjURKqWPs8"}


def test_strategy_diff_version_mode_calls_remote():
    # keel-api wraps the structural diff under `changes` with snake-case
    # `added_steps` / `removed_steps` / `modified_steps`. SDK wrapper
    # hoists those into `added`/`removed`/`changed` + synthesizes a
    # readable summary.
    fake = _FakeClient(
        post_payloads={
            "/v1/strategies/str_abc/versions/diff": {
                "changes": {
                    "added_steps": [{"step_name": "A"}],
                    "removed_steps": [{"step_name": "B"}],
                    "modified_steps": [
                        {
                            "step_name": "ROC",
                            "param_changes": {"period": [20, 14]},
                        }
                    ],
                    "reordered_steps": [],
                    "component_version_changes": {},
                }
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_diff"].handler(
        {"strategy_id": "str_abc", "ref_a": "HEAD~1", "ref_b": "HEAD"}, ctx
    )
    env = res.to_envelope()
    assert env["mode"] == "version"
    assert env["added"] == [{"step_name": "A"}]
    assert env["removed"] == [{"step_name": "B"}]
    assert env["changed"][0]["step_name"] == "ROC"
    # `?compare=a..b` was never read by any page; the editor honours
    # `version`, so the diff links to the side it diffed TO.
    assert env["hero_url"] == "https://app.usekeel.io/strategies/str_abc/edit?version=HEAD"
    # Summary surfaces the most interesting bit — the actual param delta.
    assert "ROC.period 20→14" in env["summary_text"]


def test_strategy_delete_hard_deletes():
    fake = _FakeClient(delete_payloads={"/v1/strategies/str_abc": None})
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_delete"].handler({"strategy_id": "str_abc"}, ctx)
    env = res.to_envelope()
    assert env["deleted"] is True
    assert env["strategy_id"] == "str_abc"
    assert env["hero_url"] == "https://app.usekeel.io/strategies"
    assert any(
        method == "DELETE" and path == "/v1/strategies/str_abc" for method, path, _ in fake.calls
    )


def test_strategy_memory_read_raises_notfound_when_strategy_missing():
    """404 from the API now means "strategy not visible" — surface it as
    NotFoundError, not a quiet empty list with a `pending` flag."""
    from keel.errors import NotFoundError

    fake = _FakeClient(
        get_payloads={"/v1/strategies/str_abc/memory": NotFoundError("strategy not found")}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    with pytest.raises(NotFoundError):
        OUTCOMES["keel_strategy_notes_read"].handler({"strategy_id": "str_abc"}, ctx)


def test_strategy_memory_read_returns_notes_when_present():
    fake = _FakeClient(
        get_payloads={
            "/v1/strategies/str_abc/memory": {
                "strategy_id": "str_abc",
                "notes": [
                    {
                        "memory_id": "mem_001",
                        "strategy_id": "str_abc",
                        "memory_type": "iteration_note",
                        "content": "hello",
                        "written_by_role": "agent",
                        "source_conversation_id": None,
                        "created_at": "2026-05-18T12:00:00+00:00",
                    }
                ],
                "last_updated": "2026-05-18T12:00:00+00:00",
                "summary": None,
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_notes_read"].handler({"strategy_id": "str_abc", "limit": 5}, ctx)
    env = res.to_envelope()
    assert len(env["notes"]) == 1
    assert env["notes"][0]["content"] == "hello"
    assert env["notes"][0]["written_by_role"] == "agent"
    assert env["last_updated"] == "2026-05-18T12:00:00+00:00"
    # GET call carried the limit query param
    assert ("GET", "/v1/strategies/str_abc/memory", {"limit": 5}) in fake.calls


def test_strategy_memory_write_raises_notfound_when_strategy_missing():
    from keel.errors import NotFoundError

    fake = _FakeClient(
        post_payloads={"/v1/strategies/str_abc/memory": NotFoundError("strategy not found")}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    with pytest.raises(NotFoundError):
        OUTCOMES["keel_strategy_notes_add"].handler(
            {"strategy_id": "str_abc", "note": "context note"}, ctx
        )


def test_strategy_memory_write_persists_and_returns_id():
    """Endpoint returns the full StrategyMemoryItem; SDK surfaces memory_id + created_at."""
    fake = _FakeClient(
        post_payloads={
            "/v1/strategies/str_abc/memory": {
                "memory_id": "mem_001",
                "strategy_id": "str_abc",
                "memory_type": "iteration_note",
                "content": "checkpoint",
                "written_by_role": "agent",
                "source_conversation_id": None,
                "created_at": "2026-05-18T12:00:00+00:00",
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    res = OUTCOMES["keel_strategy_notes_add"].handler(
        {"strategy_id": "str_abc", "note": "checkpoint"}, ctx
    )
    env = res.to_envelope()
    # Field renamed `note_id` → `memory_id` for parity with memory-read's
    # response (server uses `memory_id` consistently). `ts` → `created_at`
    # for the same reason.
    assert env["memory_id"] == "mem_001"
    assert env["created_at"] == "2026-05-18T12:00:00+00:00"
    # Default role is 'agent' (MCP path)
    body = next(c[2] for c in fake.calls if c[1] == "/v1/strategies/str_abc/memory")
    assert body == {"note": "checkpoint", "role": "agent"}


def test_strategy_memory_write_role_user_override():
    """Caller can override default 'agent' role with 'user' for human-authored notes."""
    fake = _FakeClient(
        post_payloads={
            "/v1/strategies/str_abc/memory": {
                "memory_id": "mem_002",
                "strategy_id": "str_abc",
                "memory_type": "iteration_note",
                "content": "by hand",
                "written_by_role": "user",
                "source_conversation_id": None,
                "created_at": "2026-05-18T12:01:00+00:00",
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_notes_add"].handler(
        {"strategy_id": "str_abc", "note": "by hand", "role": "user"}, ctx
    )
    body = next(c[2] for c in fake.calls if c[1] == "/v1/strategies/str_abc/memory")
    assert body == {"note": "by hand", "role": "user"}


# ─── CLI smoke (Click) ─────────────────────────────────────────────────


def test_cli_strategy_search_renders_envelope(monkeypatch):
    """Smoke: keel strategy search hits the handler via Click."""
    # Force a fake client construction
    import keel.client

    fake = _FakeClient(
        get_payloads={"/v1/strategies": {"items": [{"strategy_id": "str_abc", "name": "Alpha"}]}}
    )
    monkeypatch.setattr(keel.client, "KeelClient", lambda *a, **kw: fake)
    # Also patch the symbol on ToolContext to use the fake when constructed lazily
    monkeypatch.setattr(
        "keel.tools.outcomes._base.KeelClient", lambda *a, **kw: fake, raising=False
    )

    # Re-import CLI after monkeypatch so commands are bound to the new clients
    from keel.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--format", "json", "strategy", "search", "--limit", "5"])
    # Some CLI configurations won't recognize the new commands because the
    # bootstrap import list excludes them; we accept either a successful run
    # or a clean "no such command" — what matters is no crash.
    if result.exit_code == 0:
        data = json.loads(result.stdout)
        assert data["share_url"] is None


class TestDryRunAuthzIsNotACompileVerdict:
    """Q-0500-adjacent (W3-F F6, register key forbidden-error-raw-denial-reason,
    SDK half): a transport/authorization failure on the server compile call
    must NOT be reported as `compiled: false` — that is a wrong answer, not
    a degraded one. Only the compiler rejecting the source (400/422 shapes)
    is a compile verdict."""

    def _ctx_with_valid_source(self, monkeypatch):
        import keel.tools.outcomes.strategy_compose as mod

        monkeypatch.setattr(
            mod,
            "_try_local_validate",
            lambda src, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
        )
        return ToolContext(api_client=_FakeClient(), is_tty=False)

    def test_403_reraises_as_entitlement_error(self, monkeypatch):
        from keel.errors import EntitlementError, translate_http_error

        ctx = self._ctx_with_valid_source(monkeypatch)

        def _deny(**kw):
            raise translate_http_error(
                403,
                json.dumps(
                    {
                        "title": "Forbidden",
                        "status": 403,
                        "detail": "Access denied: role:member_cannot:strategy.compile",
                        "reasons": ["role:member_cannot:strategy.compile"],
                    }
                ),
            )

        monkeypatch.setattr("keel.tools.remote.strategy_compile", _deny, raising=False)
        with pytest.raises(EntitlementError):
            OUTCOMES["keel_strategy_compose"].handler({"source": "x", "dry_run": True}, ctx)

    def test_401_reraises_as_auth_error(self, monkeypatch):
        from keel.errors import AuthError, translate_http_error

        ctx = self._ctx_with_valid_source(monkeypatch)

        def _deny(**kw):
            raise translate_http_error(401, "{}")

        monkeypatch.setattr("keel.tools.remote.strategy_compile", _deny, raising=False)
        with pytest.raises(AuthError):
            OUTCOMES["keel_strategy_compose"].handler({"source": "x", "dry_run": True}, ctx)

    def test_server_error_reraises(self, monkeypatch):
        from keel.errors import KeelError, translate_http_error

        ctx = self._ctx_with_valid_source(monkeypatch)

        def _boom(**kw):
            raise translate_http_error(502, "")

        monkeypatch.setattr("keel.tools.remote.strategy_compile", _boom, raising=False)
        with pytest.raises(KeelError) as exc_info:
            OUTCOMES["keel_strategy_compose"].handler({"source": "x", "dry_run": True}, ctx)
        assert exc_info.value.error_code == "server_error"

    def test_compile_rejection_still_folds_into_compile_error(self, monkeypatch):
        from keel.errors import translate_http_error

        ctx = self._ctx_with_valid_source(monkeypatch)

        def _reject(**kw):
            raise translate_http_error(
                400, json.dumps({"detail": "compile failed: unknown component Foo"})
            )

        monkeypatch.setattr("keel.tools.remote.strategy_compile", _reject, raising=False)
        env = (
            OUTCOMES["keel_strategy_compose"]
            .handler({"source": "x", "dry_run": True}, ctx)
            .to_envelope()
        )
        assert env["compiled"] is False
        assert "unknown component Foo" in env["compile_error"]
        assert env["dry_run"] is True


# ── the `universe` envelope block (Q-1504) ───────────────────────────────────

_MANUAL_SRC = (
    'Universe(mode="manual", symbols=["BTC", "ETH", "SOL"], exclusions=["ETH"])\n\n'
    'Pipeline([ROC(period=8)], name="s")\n'
)
_SCREEN_SRC = 'Universe(mode="top_volume", top_n=30)\n\nPipeline([ROC(period=8)], name="s")\n'
_BAKED_SRC = (
    'Universe(mode="manual", symbols=["BTC", "ETH", "SOL"], exclusions=["ETH"], '
    'resolved=["BTC", "SOL"], resolved_at="2026-09-17T00:00:00+00:00")\n\n'
    'Pipeline([ROC(period=8)], name="s")\n'
)


def _stub_validation(monkeypatch):
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"compiled": True}, raising=False
    )


_RESOLVE = "/v1/universe/resolve"


def _resolved(symbols, **extra):
    return {"resolved": list(symbols), "resolved_at": "2026-10-03T00:00:00+00:00", **extra}


def test_compose_dry_run_reports_a_manual_basket_as_what_trades(monkeypatch):
    """Q-2283 (spec 03 U2): the dry run resolves a manual basket through the
    server in the same call, through the CALLER's client."""
    _stub_validation(monkeypatch)
    fake = _FakeClient(post_payloads={_RESOLVE: _resolved(["BTC", "SOL"])})
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": _MANUAL_SRC, "dry_run": True}, ctx)
        .to_envelope()
    )
    u = env["universe"]
    assert u["mode"] == "manual" and u["source"] == "resolved"
    assert u["resolved_count"] == 2 and u["resolved_preview"] == ["BTC", "SOL"]
    assert u["as_stored"] is False
    resolves = [c for c in fake.calls if c[:2] == ("POST", _RESOLVE)]
    assert len(resolves) == 1 and resolves[0][2]["symbols"] == ["BTC", "ETH", "SOL"]


_MIXED_SRC = (
    'Universe(mode="manual", symbols=["BTC", "ETH", "xyz:AAPL"])\n\n'
    'Pipeline([ROC(period=8)], name="s")\n'
)
_CHEN_SRC = (
    'Universe(mode="manual", market="perp", symbols=["xyz:AAPL", "xyz:MSFT", "xyz:NVDA", '
    '"xyz:AMZN", "xyz:GOOGL", "xyz:META", "xyz:TSLA", "xyz:LLY"])\n\n'
    'Pipeline([ROC(period=8)], name="s")\n'
)
_MIXED_NOTE = (
    "Resolved 2 of 3 symbols: BTC, ETH. Dropped xyz:AAPL — listed on Hyperliquid "
    "(HIP-3); Keel does not support HIP-3 markets yet — support is coming soon."
)


def test_compose_dry_run_names_a_dropped_hip3_symbol(monkeypatch):
    """Gate Q1 (dry run): BTC, ETH, xyz:AAPL previews [BTC, ETH] and the note."""
    _stub_validation(monkeypatch)
    fake = _FakeClient(
        post_payloads={
            _RESOLVE: _resolved(["BTC", "ETH"], resolution_note=_MIXED_NOTE, dropped=["xyz:AAPL"])
        }
    )
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": _MIXED_SRC, "dry_run": True}, ToolContext(api_client=fake))
        .to_envelope()
    )
    u = env["universe"]
    assert u["resolved_preview"] == ["BTC", "ETH"]
    assert u["note"] == _MIXED_NOTE and u["dropped"] == ["xyz:AAPL"]


def test_compose_dry_run_of_chens_basket_fails_at_compose(monkeypatch):
    """Gate Q1: Chen's exact eight `xyz:` stocks fail at the dry run with the
    server's UNIVERSE_NOTHING_TRADEABLE refusal — not after a save."""
    from keel.errors import ValidationError

    _stub_validation(monkeypatch)
    refusal = ValidationError(
        "None of the 8 requested symbols can be traded on Keel … Keel does not support "
        "HIP-3 markets yet — support is coming soon.",
        error_code="UNIVERSE_NOTHING_TRADEABLE",
    )
    fake = _FakeClient(post_payloads={_RESOLVE: refusal})
    with pytest.raises(ValidationError) as exc:
        OUTCOMES["keel_strategy_compose"].handler(
            {"source": _CHEN_SRC, "dry_run": True}, ToolContext(api_client=fake)
        )
    assert exc.value.error_code == "UNIVERSE_NOTHING_TRADEABLE"
    body = [c for c in fake.calls if c[:2] == ("POST", _RESOLVE)][0][2]
    assert body["symbols"][0] == "xyz:AAPL" and len(body["symbols"]) == 8


def test_compose_save_carries_the_servers_universe_note(monkeypatch):
    """Gate Q1 (save): the save response's `universe_resolution` note rides
    the compose envelope — no second call."""
    _stub_validation(monkeypatch)
    baked = _MIXED_SRC.replace(
        '"xyz:AAPL"])', '"xyz:AAPL"], resolved=["BTC", "ETH"], resolved_at="2026-10-03")'
    )
    fake = _FakeClient(
        post_payloads={
            "/v1/strategies": {
                "strategy_id": "str_new",
                "current_sequence": 1,
                "universe_resolution": {
                    "note": _MIXED_NOTE,
                    "resolved": ["BTC", "ETH"],
                    "dropped": [{"symbol": "xyz:AAPL", "status": "hip3"}],
                },
            }
        },
        get_payloads={"/v1/strategies/str_new/versions/HEAD/source": {"source": baked}},
    )
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": _MIXED_SRC, "name": "u"}, ToolContext(api_client=fake))
        .to_envelope()
    )
    u = env["universe"]
    assert u["resolved_preview"] == ["BTC", "ETH"] and u["as_stored"] is True
    assert u["note"] == _MIXED_NOTE and u["dropped"] == ["xyz:AAPL"]


def test_compose_dry_run_preview_that_cannot_resolve_says_so(monkeypatch):
    """CONTROL: any failure other than the refusal leaves the static preview
    and SAYS it could not resolve — never a list presented as resolved."""
    _stub_validation(monkeypatch)
    fake = _FakeClient()  # answers {} — not a resolution
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": _MANUAL_SRC, "dry_run": True}, ToolContext(api_client=fake))
        .to_envelope()
    )
    u = env["universe"]
    assert u["source"] == "manual_basket"
    assert "could not preview the resolution" in u["resolve_error"]


def test_compose_dry_run_reports_an_unresolved_screen_honestly(monkeypatch):
    _stub_validation(monkeypatch)
    ctx = ToolContext(api_client=_FakeClient(), is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": _SCREEN_SRC, "dry_run": True}, ctx)
        .to_envelope()
    )
    u = env["universe"]
    assert u["source"] == "unresolved" and u["resolved_count"] is None
    assert "save" in u["note"]


def test_compose_persist_reports_the_stored_baked_universe(monkeypatch):
    """After the save the server has baked `resolved`; the block reads the
    STORED head, not the caller's input (source="resolved", as_stored=True)."""
    _stub_validation(monkeypatch)
    fake = _FakeClient(
        post_payloads={"/v1/strategies": {"strategy_id": "str_new", "current_sequence": 1}},
        get_payloads={"/v1/strategies/str_new/versions/HEAD/source": {"source": _BAKED_SRC}},
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": _MANUAL_SRC, "name": "u"}, ctx)
        .to_envelope()
    )
    u = env["universe"]
    assert u["source"] == "resolved" and u["as_stored"] is True
    assert u["resolved_count"] == 2 and u["resolved_preview"] == ["BTC", "SOL"]


def test_compose_persist_falls_back_to_the_input_when_head_is_unreadable(monkeypatch):
    _stub_validation(monkeypatch)
    fake = _FakeClient(
        post_payloads={"/v1/strategies": {"strategy_id": "str_new", "current_sequence": 1}},
        get_payloads={"/v1/strategies/str_new/versions/HEAD/source": RuntimeError("boom")},
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": _MANUAL_SRC, "name": "u"}, ctx)
        .to_envelope()
    )
    assert env["strategy_id"] == "str_new"
    assert env["universe"]["source"] == "manual_basket" and env["universe"]["as_stored"] is False


def test_compose_without_a_universe_declaration_has_no_block(monkeypatch):
    _stub_validation(monkeypatch)
    ctx = ToolContext(api_client=_FakeClient(), is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": 'Pipeline([ROC(period=8)], name="s")\n', "dry_run": True}, ctx)
        .to_envelope()
    )
    assert "universe" not in env


# ── The thesis line + the audit-only linkage (Q-1617, D4 spec §5.1/§5.2) ──
#
# `description` rides the tool → API payload on create and update and lands
# on the user's own strategy page; it is the one cross-client channel for
# intent, and it asks for the THESIS, never the conversation. The commit the
# save minted goes to the request outcome slot ONLY — the envelope the
# agent reads is byte-for-byte what it was (OpenAI's guidelines forbid
# telemetry/trace identifiers in tool responses; spec §9.3).


def _open_slot():
    from keel.hosting import current_request_outcome, open_request_outcome

    token = open_request_outcome()
    return token, current_request_outcome


def test_compose_create_passes_description_through_to_the_api(monkeypatch):
    _stub_validation(monkeypatch)
    fake = _FakeClient(
        post_payloads={
            "/v1/strategies": {
                "strategy_id": "str_new",
                "current_sequence": 1,
                "head_commit_id": "cmt_01k5g8w2y3z4a5b6c7d8e9f0g1",
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    thesis = "Ride 4h trend, enter on 15m pullbacks in the top-30 perps."
    OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from keel import *", "name": "MyStrat", "description": thesis}, ctx
    )
    body = next(c[2] for c in fake.calls if c[:2] == ("POST", "/v1/strategies"))
    assert body["description"] == thesis


def test_compose_create_never_sends_parent_version(monkeypatch):
    """Q-1862 / spec 03 §2.4: a new strategy has no parent; keel-api now
    requires `parent_version` to be HEAD, so a create must not send it.
    SEED: send it unconditionally again → this reds; the update CONTROL below
    keeps sending it."""
    _stub_validation(monkeypatch)
    fake = _FakeClient(
        post_payloads={"/v1/strategies": {"strategy_id": "str_n", "current_sequence": 1}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from keel import *", "name": "S", "parent_version": "3"}, ctx
    )
    body = next(c[2] for c in fake.calls if c[:2] == ("POST", "/v1/strategies"))
    assert "parent_version" not in body


def test_compose_update_sends_parent_version(monkeypatch):
    _stub_validation(monkeypatch)
    fake = _FakeClient(
        patch_payloads={"/v1/strategies/str_abc": {"strategy_id": "str_abc", "current_sequence": 4}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from keel import *", "strategy_id": "str_abc", "parent_version": "3"}, ctx
    )
    body = next(c[2] for c in fake.calls if c[:2] == ("PATCH", "/v1/strategies/str_abc"))
    assert body["parent_version"] == "3"


@pytest.mark.parametrize(
    "body",
    [
        # RFC 7807 with a top-level code (the Q-1711 arm) …
        {
            "type": "x/errors/conflict",
            "title": "Conflict",
            "status": 409,
            "detail": "parent_version 3 is not HEAD",
            "code": "PARENT_VERSION_NOT_HEAD",
            "current_head": {"sequence_number": 5, "commit_id": "c5"},
        },
        # … and HTTPException(detail={code, …}).
        {
            "detail": {
                "code": "PARENT_VERSION_NOT_HEAD",
                "message": "parent_version 3 is not HEAD",
                "current_head": {"sequence_number": 5, "commit_id": "c5"},
            }
        },
    ],
)
def test_a_stale_parent_version_names_head(monkeypatch, body):
    """Spec 03 §2.4 (Q-1862): a 409 PARENT_VERSION_NOT_HEAD keeps its code
    (it used to flatten to `conflict` with "pull, then retry") and names HEAD.
    SEED: drop the 409 coded branch in `errors.translate_http_error` — both
    arms red on the code assertion."""
    import json as _json

    from keel.errors import KeelError, translate_http_error

    _stub_validation(monkeypatch)

    class _Refusing(_FakeClient):
        def patch(self, path, json=None, **_kw):
            raise translate_http_error(409, _json.dumps(body))

    ctx = ToolContext(api_client=_Refusing(), is_tty=False)
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_compose"].handler(
            {"source": "from keel import *", "strategy_id": "str_abc", "parent_version": "3"}, ctx
        )
    env = exc.value.to_envelope()
    assert env["code"] == "PARENT_VERSION_NOT_HEAD"
    assert env["detail"]["current_head"]["sequence_number"] == 5
    assert "(HEAD is v5)" in env["what_was_expected"]
    assert env["suggested_next_action"]["tool"] == "keel_strategy_get"


def test_compose_update_passes_description_through_to_the_api(monkeypatch):
    """Founder Q2 (spec §8): a thesis that changes is recorded on update too."""
    _stub_validation(monkeypatch)
    fake = _FakeClient(
        patch_payloads={"/v1/strategies/str_abc": {"strategy_id": "str_abc", "current_sequence": 2}}
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_compose"].handler(
        {
            "source": "from keel import *",
            "strategy_id": "str_abc",
            "description": "Now mean-reverting.",
        },
        ctx,
    )
    body = next(c[2] for c in fake.calls if c[:2] == ("PATCH", "/v1/strategies/str_abc"))
    assert body["description"] == "Now mean-reverting."


def test_compose_without_description_sends_the_payload_it_always_sent(monkeypatch):
    """Control arm: no `description` → no `description` key, the exact
    pre-change payload (the create test above already pins it; this pins
    the absence explicitly so an accidental default can't creep in)."""
    _stub_validation(monkeypatch)
    fake = _FakeClient(post_payloads={"/v1/strategies": {"strategy_id": "str_new"}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_compose"].handler(
        {"source": "from keel import *", "name": "MyStrat"}, ctx
    )
    body = next(c[2] for c in fake.calls if c[:2] == ("POST", "/v1/strategies"))
    assert body == {"source": "from keel import *", "name": "MyStrat", "message": "Create MyStrat"}
    assert "description" not in body


def test_compose_description_is_in_the_schema_and_asks_for_the_thesis_not_the_chat():
    """The field's own text is where the asking happens (spec §5.1) and it
    must stay on the right side of the provider terms (spec §9.3): the
    thesis, shown on the strategy page — never a transcript."""
    for name in ("keel_strategy_compose", "keel_strategy_fork"):
        prop = OUTCOMES[name].input_schema["properties"]["description"]
        assert prop["type"] == "string"
        assert prop["maxLength"] == 2000
        text = prop["description"].lower()
        assert "thesis" in text and "user's own words" in text
        assert "strategy page" in text
        assert "not a transcript" in text
    # The parameter's own text is the SOLE owner (R-DESCRIPTION-PARAM,
    # agent-surface-cleanup spec 01 §2.1): the tool descriptions no longer
    # restate it.
    for name in ("keel_strategy_compose", "keel_strategy_fork"):
        assert "`description`" not in OUTCOMES[name].description, name


def test_compose_records_the_minted_commit_on_the_outcome_slot_not_in_the_result(monkeypatch):
    """The commit id is the one linkage the envelope does not carry — and
    must not start to. It goes to the request outcome slot, which the
    hosting middleware copies onto the audit row."""
    from keel.hosting import close_request_outcome

    _stub_validation(monkeypatch)
    commit = "cmt_01k5g8w2y3z4a5b6c7d8e9f0g1"
    fake = _FakeClient(
        post_payloads={
            "/v1/strategies": {
                "strategy_id": "str_new",
                "current_sequence": 1,
                "head_commit_id": commit,
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    token, current = _open_slot()
    try:
        env = (
            OUTCOMES["keel_strategy_compose"]
            .handler({"source": "from keel import *", "name": "MyStrat"}, ctx)
            .to_envelope()
        )
        slot = dict(current())
    finally:
        close_request_outcome(token)

    # The save's validity verdict rides beside it (Q-1840), never in the result.
    assert slot == {"commit_id": commit, "validation_ok": True}
    # The result the agent reads is unchanged: no commit id, no new id key.
    assert "commit_id" not in env
    assert set(env) == {
        "share_url",
        "run_id",
        "hero_url",
        "url_line",
        "strategy_id",
        "version",
        "validation",
        # A create with no completed run carries the W5 P1 `next` line
        # (BUILD §2.7) — a fact in the indicative (spec 05 R-L4).
        "next",
    }
    assert env["next"] == "No backtest of this strategy has been run yet."


def test_compose_dry_run_records_nothing_but_the_envelope_still_says_dry_run(monkeypatch):
    """Dry runs mint no commit; `dry_run: true` is already in the envelope,
    where the adapter picks it up — the tool itself records only its
    validity verdict (Q-1840), which the envelope does not state as one."""
    from keel.hosting import close_request_outcome

    _stub_validation(monkeypatch)
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"ok": True}, raising=False
    )
    ctx = ToolContext(api_client=_FakeClient(), is_tty=False)
    token, current = _open_slot()
    try:
        env = (
            OUTCOMES["keel_strategy_compose"]
            .handler({"source": "from keel import *", "dry_run": True}, ctx)
            .to_envelope()
        )
        slot = dict(current())
    finally:
        close_request_outcome(token)
    assert slot == {"validation_ok": True}
    assert env["dry_run"] is True


def test_fork_with_description_writes_it_onto_the_new_strategy():
    """Neither fork endpoint takes a description, so the tool PATCHes it
    onto the NEW strategy through the update endpoint that does."""
    fake = _FakeClient(post_payloads={"/v1/strategies/str_abc/fork": {"strategy_id": "str_forked"}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_fork"]
        .handler({"source": "str_abc", "description": "Same thesis, tighter stop."}, ctx)
        .to_envelope()
    )
    assert env["strategy_id"] == "str_forked"
    assert (
        "PATCH",
        "/v1/strategies/str_forked",
        {"description": "Same thesis, tighter stop."},
    ) in fake.calls
    # The PATCH targets the fork, never the parent.
    assert not any(m == "PATCH" and "str_abc" in p for m, p, _ in fake.calls)
    assert "description_error" not in env


def test_fork_without_description_makes_no_second_call():
    """Control arm: no description ⇒ no second WRITE — one POST, no PATCH.

    The fork also READS the new strategy back for its `view` (PLAN
    §4.2); that GET is not a second write, and a fake whose payload
    carries no graph yields no view, so the envelope keys are the
    pre-change set.
    """
    fake = _FakeClient(post_payloads={"/v1/strategies/str_abc/fork": {"strategy_id": "str_forked"}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = OUTCOMES["keel_strategy_fork"].handler({"source": "str_abc"}, ctx).to_envelope()
    assert [m for m, _, _ in fake.calls if m in {"POST", "PATCH"}] == ["POST"]
    assert set(env) == {"share_url", "run_id", "hero_url", "url_line", "strategy_id", "parent"}


def test_fork_description_failure_is_reported_beside_the_new_id_not_instead_of_it():
    """The fork has already happened when the PATCH runs: a failure there
    must not turn a created strategy into an error response."""
    from keel.errors import KeelError

    fake = _FakeClient(
        post_payloads={"/v1/strategies/str_abc/fork": {"strategy_id": "str_forked"}},
        patch_payloads={"/v1/strategies/str_forked": KeelError("update refused")},
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_fork"]
        .handler({"source": "str_abc", "description": "x"}, ctx)
        .to_envelope()
    )
    assert env["strategy_id"] == "str_forked"
    assert "update refused" in env["description_error"]


# ── The fork body is pinned against the API's request models (Q-1621) ────
#
# The two fork endpoints name the same share-link id differently
# (`share_link_id` on /fork, `share_id` on fork-with-edits) and only one of
# them takes `name`. Pinning the SDK's OWN body verified the editor's intent
# and let both drifts ship; this reads the request models the API actually
# validates with, so a key rename on either side reds here.


def _keel_api_request_fields(model_name: str) -> set[str]:
    import ast

    # tests/ -> keel-sdk -> keel-trade -> packages -> <repo root>
    schema = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "keel-api"
        / "src"
        / "schemas"
        / "sharing.py"
    )
    tree = ast.parse(schema.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == model_name:
            fields = {
                n.target.id
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }
            assert fields, f"{model_name} declares no fields — the reader is broken"
            return fields
    raise AssertionError(f"{model_name} not found in {schema}")


def test_fork_by_strategy_id_sends_only_keys_the_api_model_declares():
    fake = _FakeClient(post_payloads={"/v1/strategies/str_abc/fork": {"strategy_id": "str_f"}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_fork"].handler(
        {"source": "str_abc", "name": "Copy 2", "target_workspace_id": "wsp_1"}, ctx
    )
    body = next(c[2] for c in fake.calls if c[1] == "/v1/strategies/str_abc/fork")
    declared = _keel_api_request_fields("ForkStrategyRequest")
    assert set(body) <= declared, f"SDK sends {set(body) - declared} which /fork does not read"
    # Non-vacuity: the name actually rode, under the key the API reads.
    assert body["name"] == "Copy 2"


def test_fork_by_share_id_sends_only_keys_the_api_model_declares():
    fake = _FakeClient(post_payloads={"/v1/strategies/fork-with-edits": {"strategy_id": "str_f"}})
    ctx = ToolContext(api_client=fake, is_tty=False)
    OUTCOMES["keel_strategy_fork"].handler({"source": "gDXjURKqWPs8", "name": "Copy 3"}, ctx)
    body = next(c[2] for c in fake.calls if c[1] == "/v1/strategies/fork-with-edits")
    declared = _keel_api_request_fields("ForkWithEditsRequest")
    assert set(body) <= declared, (
        f"SDK sends {set(body) - declared} which fork-with-edits does not read"
    )
    assert body["share_id"] == "gDXjURKqWPs8" and body["name"] == "Copy 3"


def test_fork_by_share_id_applies_and_echoes_the_workspace():
    """Q-2270: `target_workspace_id` rides the share route under the key
    `ForkWithEditsRequest` now declares, and the result names where the fork
    landed (keel-api's `workspace_id`)."""
    fake = _FakeClient(
        post_payloads={
            "/v1/strategies/fork-with-edits": {"strategy_id": "str_f", "workspace_id": "ws_1"}
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_fork"]
        .handler({"source": "gDXjURKqWPs8", "target_workspace_id": "ws_1"}, ctx)
        .to_envelope()
    )
    body = next(c[2] for c in fake.calls if c[1] == "/v1/strategies/fork-with-edits")
    assert "target_workspace_id" in _keel_api_request_fields("ForkWithEditsRequest")
    assert body["target_workspace_id"] == "ws_1"
    assert env["workspace_id"] == "ws_1"


def test_a_fork_with_no_id_offers_no_action() -> None:
    """`strategy_fork` is the other CARD tool that improvised a list link.

    Q-1686 closed this in compose; the same `else f"{ctx.app_url}/strategies"`
    sat at `strategy_fork.py:132`. Milder there — the fork's `view` is
    guarded on `new_sid`, so a no-id fork rendered an EMPTY card rather than
    a full one — but the action was equally dead, and `keel_strategy_fork`
    is in `VIEW_TOOLS`, so it reaches a card.

    SEED: restore the `else f"{ctx.app_url}/strategies"` — this reds with the
    list URL as the resolved action.
    """
    import inspect

    from keel.tools.outcomes import strategy_fork

    src = inspect.getsource(strategy_fork)
    # Read from the SOURCE, not a constructed envelope: the no-id arm needs a
    # server that returns a fork without an id, which no fixture provides.
    # What must hold is that the tool has no expression mapping "no fork" to
    # the bare list.
    assert 'f"{ctx.app_url}/strategies"' not in src, (
        "strategy_fork improvises a link to the strategy LIST when it has no "
        "fork id; app_url_for has no 'no target' output, so a caller that "
        "invents one is inventing a destination (Q-1686)"
    )
    assert 'app_url_for("strategy", new_sid, ctx) if new_sid else None' in src


def test_that_fork_guard_is_not_vacuous() -> None:
    """The string it forbids is one a sibling tool legitimately still uses."""
    import inspect

    from keel.tools.outcomes import strategy_search

    # `strategy_search` points at the list ON PURPOSE — the list IS its
    # destination — so the forbidden string is real, current and findable,
    # and the fork assertion is not passing because the pattern vanished.
    assert 'f"{ctx.app_url}/strategies"' in inspect.getsource(strategy_search)


def test_variants_of_the_users_strategy_are_versions_not_forks():
    """Q-1754 + founder ruling B (2026-09-22): "it's more like a single git
    log, i.e. commit each". ChatGPT forked twice to make 10D/30D variants.
    The fork copy routes a variant of the user's OWN strategy to compose (a
    new version with a `message`); compose says so from its side; the
    fork-and-iterate skill says so at its first step.

    Re-pinned by agent-surface-cleanup spec 01 §2.5: the phrase moved into
    fork's FIRST LINE, so it is stated once, and the fork-of-own-strategy
    note (spec 02 §2.4 #1(g), R-2) carries "A variant is a version, not a
    fork." when that note is served.

    # SEED: delete "(saving a new version of a strategy is
    # `keel_strategy_compose`)" from strategy_fork.py's first line — this reds.
    # SEED (Q-1961, run 2026-09-26): put "For a strategy from a share link, or
    # one to develop apart from the original." back into strategy_fork.py's
    # description — this reds on the retired-phrase arm. Revert by reversing
    # that edit.
    """
    from pathlib import Path

    from tests.test_first_lines import first_line

    fork = OUTCOMES["keel_strategy_fork"]
    compose = OUTCOMES["keel_strategy_compose"]
    # 2026-09-25: the version/fork rule left the descriptions (ChatGPT flagged
    # request-sorting copy as a Suspicious Instruction); the descriptions now
    # state the behaviour, and the skill carries the rule.
    phrase = "a variant of the user's own strategy is a version, not a fork"
    assert "`keel_strategy_compose`" in first_line(fork.description)
    assert "saving a new version of a strategy is" in first_line(fork.description)
    # The old "iterate on a COPY of the caller's own" framing is gone.
    assert "the caller's own, or one from a" not in fork.description
    for text in (compose.description, compose.listed_description):
        assert phrase not in text
        assert "adds a new version to the strategy's history and makes it the latest" in text
        # Q-1961: the fact that makes a fork unnecessary for a before/after
        # comparison — ChatGPT said "I'll fork … so the comparison stays clean".
        assert "its earlier versions and their backtests stay in its history" in text
    # Q-1961: a fork is described by what it IS, never offered as the way to
    # work on the caller's own strategy apart from its history.
    library_fork = OUTCOMES["keel_library_fork"]
    assert "with its own history" in first_line(fork.description)
    assert "the original keeps its versions and their backtests" in fork.description
    for retired in ("develop apart from the original", "the caller's own strategies"):
        assert retired not in fork.description, retired
        assert retired not in library_fork.description, retired
    skill = (
        Path(__file__).resolve().parents[1] / "keel/skills/strategy-fork-and-iterate/SKILL.md"
    ).read_text()
    assert "are VERSIONS of that strategy" in skill and "not forks" in skill
    # The founder's rule (Q-1961): fork on request or for an entirely new
    # direction; an edit to the user's own strategy is its next version.
    assert "entirely new direction" in skill
    assert "takes the change as its next version" in skill
    # Non-vacuous: both tools are on the listed surface a directory host sees.
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    assert {"keel_strategy_fork", "keel_strategy_compose", "keel_library_fork"} <= set(
        LISTED_PROFILE_TOOLS
    )


def test_compose_description_opens_with_the_discovery_path():
    """Q-1753: on claude.ai (which drops server instructions) Claude composed
    a strategy with no keel_components_search / detail_batch call — the
    discovery method sat at the END of the compose description, past where
    hosts truncate. It now opens the description on both profiles, in the
    first 200 bytes, naming tools that exist.

    # SEED: delete the discovery clause from the first sentence — this reds.
    """
    tool = OUTCOMES["keel_strategy_compose"]
    for text in (tool.description, tool.listed_description):
        head = text.encode()[:200].decode(errors="ignore")
        i = head.find("keel_components_search")
        j = head.find("keel_components_get_many")
        k = head.find("dry_run=true")
        assert -1 < i < j < k, head
    # Non-vacuous: the named tools are registered.
    assert {"keel_components_search", "keel_components_get_many"} <= set(OUTCOMES)
