"""A `keel_strategy_compose` dry run of a position-factory strategy compiles (Q-2563).

Since the position layer (Q-2448, release-v1.183) a registered factory such as
`StopLoss` compiles as its expansion (`TradeReturn`, `Exit`, …), and
`evolve_lock` pins those components too (spec 03-R50). The SDK's dry run built
its lock with a bootstrap-era walk of the DECLARED steps, so the lock it sent
to `/v1/strategies/compile` lacked the expansion, and the server's closed
resolver refused every such dry run "Unknown component 'TradeReturn'" — with
no issue code on the audit row — while the save (which evolves its own lock)
succeeded. 5 of 5 dry runs of one user, 6 of 6 codeless dry-run failures in
the 2026-10-08 window.

The fix: `strategy_lock_generate` IS `evolve_lock({}, parsed)` — one owner of
lock derivation — and a compile-stage refusal records a gate code.

The arms run the REAL bundled validator and lock derivation; only the network
is faked. `_closed_compile` emulates the server's contract for a supplied lock
(the resolver reads ONLY the lock, so an unpinned expansion step is refused)
using the engine's own closure (`evolve_lock`) as its oracle — the same oracle
keel-api's save uses — never the code under test.

Proof it can fail (run 2026-10-08, reverted by reversing the edit):
`strategy_lock_generate` restored to the declared-steps walk → 3 red:
`test_a_position_factory_dry_run_compiles` (`compile_error` names an
expansion component), `test_the_generated_lock_carries_the_expansion` and
the fake-server arm's acceptance half; the no-factory control and the four
issue-code arms stay green (5 passed). `_compile_issue_code` dropped from the
dry run's `record_outcome` → exactly the three refusal-code arms red, the
five others green.

Proof it is not vacuous: the expansion names are read from the registry's own
templates and asserted non-empty; the fake server is shown to REFUSE the
declared-only lock before any arm relies on it accepting the evolved one.
"""

from __future__ import annotations

from typing import Any

import pytest
from keel.hosting import close_request_outcome, current_request_outcome, open_request_outcome
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext

from .test_outcomes_strategy import _FakeClient


_bootstrap()

#: Lane 01's reproduced shape (subject 01, 2026-10-08): a trade-managed entry
#: with a StopLoss / TakeProfit branch and a risk sizer.
POSITION_FACTORY = """Globals(target_timeframe='4h')
Universe(mode='manual', symbols=['ETH'], market='perp')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    ATR(period=14),
    Scale(by=1.5),
    Store('stop_dist'),
    Load('ohlcv'),
    OrderBlock(direction='bullish', confirm='structure_break', mitigation='close_through'),
    RangeSelector(low_threshold=-10.0, high_threshold=0.0),
    Store('entries'),
    TradeManager(entries='entries', prices='ohlcv'),
    {"stop": [StopLoss(pct=0.05)], "target": [TakeProfit(pct=0.10)]},
    RiskSizer(risk=0.02, distance='stop_dist', max_weight=0.40),
], name='eth_ob_trade_test')
"""

#: Control: no registered factory anywhere, so its closure IS its declared steps.
NO_FACTORY = """Globals(target_timeframe='1d', bar_offset='12h')
Universe(mode='manual', symbols=['BTC', 'ETH'])
Execution(rebalance='buffered', buffer_threshold=0.2)
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name='no_factory_control')
"""


def _expansion_names(source: str) -> set[str]:
    """Every component the registry says the source's registered factories
    expand to — read from the templates, not from the lock generator."""
    from keel.tools.local import _ensure_registry

    _ensure_registry()
    from pipeline_engine.base.lock import walk_component_refs
    from pipeline_engine.base.registry import get_latest
    from pipeline_engine.base.registry_types import is_factory
    from pipeline_engine.binding import template_components
    from pipeline_engine.dsl import parse_strategy

    names: set[str] = set()
    for ref in walk_component_refs(parse_strategy(source)):
        sig = get_latest(ref.name)
        if sig is not None and is_factory(sig):
            names.update(template_components(sig.factory_expansion))
    return names


def _declared_only_lock(source: str) -> dict[str, int]:
    """The pre-fix walk, reproduced here as the seed shape: the declared
    ComponentRef steps at latest, no expansion."""
    from keel.tools.local import _ensure_registry

    _ensure_registry()
    from pipeline_engine.base.lock import walk_component_refs
    from pipeline_engine.base.registry import get_latest
    from pipeline_engine.dsl import parse_strategy

    lock: dict[str, int] = {}
    for ref in walk_component_refs(parse_strategy(source)):
        sig = get_latest(ref.name)
        if sig is not None:
            lock[ref.name] = sig.version
    return lock


def _closed_compile(source: str, component_lock: dict[str, int] | None = None) -> dict[str, Any]:
    """`/v1/strategies/compile`'s contract for a SUPPLIED lock, as the server
    enforced it: the resolver's registry is the lock and nothing else, so the
    first component the compile needs that the lock does not pin is refused
    with the resolver's message. No lock ⇒ the server derives its own."""
    from pipeline_engine.base.lock import evolve_lock
    from pipeline_engine.dsl import parse_strategy

    if component_lock is None:
        return {"compiled": True, "fingerprint": "f", "graph": {}}
    required = evolve_lock({}, parse_strategy(source))
    missing = sorted(set(required) - set(component_lock))
    if missing:
        return {
            "compiled": False,
            "errors": [
                f"Compile error: Unknown component '{missing[0]}'. "
                "Ensure the strategy passes validation before resolving."
            ],
        }
    return {"compiled": True, "fingerprint": "f", "graph": {}}


@pytest.fixture
def closed_server(monkeypatch):
    sent: list[dict[str, int] | None] = []

    def _compile(source, component_lock=None):
        sent.append(component_lock)
        return _closed_compile(source, component_lock)

    monkeypatch.setattr("keel.tools.remote.strategy_compile", _compile, raising=False)
    return sent


def _dry_run(source: str) -> tuple[dict, dict]:
    ctx = ToolContext(api_client=_FakeClient(), is_tty=False)
    token = open_request_outcome()
    try:
        env = (
            OUTCOMES["keel_strategy_compose"]
            .handler({"source": source, "name": "x", "dry_run": True}, ctx)
            .to_envelope()
        )
        return env, dict(current_request_outcome())
    finally:
        close_request_outcome(token)


# ─── the lock ────────────────────────────────────────────────────────────────


def test_the_generated_lock_carries_the_expansion():
    """Non-vacuity first: the registry's templates name expansion components
    for this source, the declared-only walk misses every one of them, and the
    generator pins every one of them — because it IS `evolve_lock`."""
    from keel.tools.local import strategy_lock_generate

    from pipeline_engine.base.lock import evolve_lock
    from pipeline_engine.dsl import parse_strategy

    expansion = _expansion_names(POSITION_FACTORY)
    assert {"TradeReturn", "Exit"} <= expansion, expansion
    declared = _declared_only_lock(POSITION_FACTORY)
    assert "StopLoss" in declared and not (expansion & set(declared))

    lock = strategy_lock_generate(source=POSITION_FACTORY)["component_lock"]
    assert expansion <= set(lock), sorted(expansion - set(lock))
    assert lock == evolve_lock({}, parse_strategy(POSITION_FACTORY))


def test_the_fake_server_refuses_the_declared_only_lock_and_accepts_the_evolved_one():
    """The fake can fail: the seed shape is refused the way the server refused
    it, and the control's declared steps ARE its closure."""
    refused = _closed_compile(POSITION_FACTORY, _declared_only_lock(POSITION_FACTORY))
    assert refused["compiled"] is False
    assert "Unknown component '" in refused["errors"][0]
    assert refused["errors"][0].split("'")[1] in _expansion_names(POSITION_FACTORY)

    from keel.tools.local import strategy_lock_generate

    assert _closed_compile(
        POSITION_FACTORY, strategy_lock_generate(source=POSITION_FACTORY)["component_lock"]
    )["compiled"]
    assert _expansion_names(NO_FACTORY) == set()
    assert _closed_compile(NO_FACTORY, _declared_only_lock(NO_FACTORY))["compiled"]


# ─── the dry run ─────────────────────────────────────────────────────────────


def test_a_position_factory_dry_run_compiles(closed_server):
    env, slot = _dry_run(POSITION_FACTORY)
    assert env["compile_error"] is None, env["compile_error"]
    assert env["compiled"] is True
    assert env["validation"]["errors"] == []
    assert slot == {"validation_ok": True}, slot
    (sent,) = closed_server
    assert sent is not None and _expansion_names(POSITION_FACTORY) <= set(sent)


def test_control_a_no_factory_dry_run_compiles_through_the_same_server(closed_server):
    env, slot = _dry_run(NO_FACTORY)
    assert env["compiled"] is True and env["compile_error"] is None
    assert slot == {"validation_ok": True}
    (sent,) = closed_server
    assert sent is not None and set(sent) == set(_declared_only_lock(NO_FACTORY))


# ─── the audit row's code for a compile-stage refusal ────────────────────────


def _refusing(monkeypatch, message: str):
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile",
        lambda source, component_lock=None: {"compiled": False, "errors": [message]},
        raising=False,
    )


def test_a_compile_refusal_after_clean_validation_records_compile_failed(monkeypatch):
    _refusing(monkeypatch, "Compile error: Unknown component 'TradeReturn'. Ensure …")
    env, slot = _dry_run(NO_FACTORY)
    assert env["validation"]["errors"] == [] and env["compiled"] is False
    assert slot == {"validation_ok": False, "issue_codes": ["COMPILE_FAILED"]}, slot


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Lock error: Locked version 9 for component 'ROC' no longer exists", "LOCK_ERROR"),
        ("Parse error: unexpected token", "PARSE_ERROR"),
    ],
)
def test_the_routes_own_prefixes_map_to_their_gate_codes(monkeypatch, message, code):
    _refusing(monkeypatch, message)
    _env, slot = _dry_run(NO_FACTORY)
    assert slot == {"validation_ok": False, "issue_codes": [code]}, slot


def test_control_a_compiled_dry_run_records_no_code(closed_server):
    _env, slot = _dry_run(NO_FACTORY)
    assert slot == {"validation_ok": True}
