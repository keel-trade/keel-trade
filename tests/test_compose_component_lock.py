"""`keel_strategy_compose` saves an update at the strategy's STORED pins.

dollar-volume spec 02 §1/§3: keel-api's PATCH now honours a `component_lock`.
Compose used to send the lock it generated from the BUNDLED registry — the
latest version of every component in this wheel's snapshot. Honoured, that
would silently move every pin on every save (or roll one back when the
snapshot lags the server). An update now sends the stored lock back with
keel-api's hash of it (`expected_lock_hash`): the server keeps those pins,
evolves them over the edit, and 409s if someone moved them since. Local
validation runs at the stored pins too. A create sends only the caller's
pins (Q-2270): keel-api now honours them, so the bundled lock never rides.

Seed (2026-10-01, reverted by reversing the edit): sending
`validation["lock"]` on create again reds both create arms.
"""

from __future__ import annotations

from typing import Any

import pytest
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()
    from keel.tools.outcomes import strategy_compose  # noqa: F401


SOURCE = """Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH"], market="perp")
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastWeightNormalizer(),
], name="lock_test")
"""

#: PriceDataLoader pinned one version behind the bundled latest (3).
STORED_LOCK = {
    "PriceDataLoader": 2,
    "TargetTimeframeResampler": 1,
    "ROC": 1,
    "ForecastScaler": 1,
    "ForecastWeightNormalizer": 1,
}
STORED_LOCK_HASH = "a" * 64


class _Client:
    """Records calls; canned GET payloads by path, `{}` otherwise."""

    def __init__(self, get_payloads: dict[str, Any] | None = None) -> None:
        self.get_payloads = get_payloads or {}
        self.calls: list[tuple[str, str, Any]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(("GET", path, params or None))
        return self.get_payloads.get(path, {})

    def post(self, path: str, json: dict | None = None, **params: Any) -> Any:
        self.calls.append(("POST", path, json))
        return {"strategy_id": "str_new", "current_sequence": 1}

    def patch(self, path: str, json: dict | None = None) -> Any:
        self.calls.append(("PATCH", path, json))
        return {"strategy_id": "str_abc", "current_sequence": 3}

    def bodies(self, verb: str) -> list[dict]:
        return [body for v, _p, body in self.calls if v == verb]


def _fresh_lock() -> dict[str, int]:
    from keel.tools.local import strategy_lock_generate

    return strategy_lock_generate(source=SOURCE)["component_lock"]


def test_an_update_sends_the_stored_pins_and_their_hash():
    fresh = _fresh_lock()
    # Non-vacuous: the bundled latest differs from the stored pins, so a
    # save that sent the generated lock would be a silent upgrade.
    assert fresh["PriceDataLoader"] == 3 and fresh != STORED_LOCK
    client = _Client(
        {
            "/v1/strategies/str_abc": {
                "strategy_id": "str_abc",
                "component_lock": STORED_LOCK,
                "lock_hash": STORED_LOCK_HASH,
            }
        }
    )
    OUTCOMES["keel_strategy_compose"].handler(
        {"strategy_id": "str_abc", "source": SOURCE}, ToolContext(api_client=client, is_tty=False)
    )
    (body,) = client.bodies("PATCH")
    assert body["component_lock"] == STORED_LOCK
    assert body["expected_lock_hash"] == STORED_LOCK_HASH


def test_an_update_without_a_readable_stored_lock_sends_none():
    """The stored-lock read failed: send no lock — keel-api then evolves the
    stored lock itself (the same pins) — never the bundled latest."""
    client = _Client()
    OUTCOMES["keel_strategy_compose"].handler(
        {"strategy_id": "str_abc", "source": SOURCE}, ToolContext(api_client=client, is_tty=False)
    )
    (body,) = client.bodies("PATCH")
    assert "component_lock" not in body
    assert "expected_lock_hash" not in body


def test_a_create_without_pins_sends_no_lock():
    """Control arm: a new strategy has no stored pins, and keel-api pins it at
    the SERVER's latest. Since keel-api honours `component_lock` on create
    (Q-2270), sending the bundled snapshot's lock would pin a create at
    whatever this wheel bundled — so nothing is sent unless asked."""
    client = _Client()
    OUTCOMES["keel_strategy_compose"].handler(
        {"name": "lock_test", "source": SOURCE}, ToolContext(api_client=client, is_tty=False)
    )
    (body,) = client.bodies("POST")
    assert "component_lock" not in body
    assert "expected_lock_hash" not in body


def test_a_create_sends_exactly_the_callers_pins_and_echoes_the_saved_lock():
    """Q-2270: the caller's `component_lock` on a CREATE was dropped (keel-api's
    create model had no field). It is sent as asked — not merged over the
    bundled lock — and the result carries the lock keel-api saved."""
    saved = {**_fresh_lock(), "PriceDataLoader": 2}

    class _Saving(_Client):
        def post(self, path, json=None, **params):
            self.calls.append(("POST", path, json))
            return {"strategy_id": "str_new", "current_sequence": 1, "component_lock": saved}

    client = _Saving()
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler(
            {"name": "lock_test", "source": SOURCE, "component_lock": {"PriceDataLoader": 2}},
            ToolContext(api_client=client, is_tty=False),
        )
        .to_envelope()
    )
    (body,) = client.bodies("POST")
    assert body["component_lock"] == {"PriceDataLoader": 2}
    assert env["component_lock"] == saved


def test_local_validation_runs_at_the_stored_pins(monkeypatch):
    seen: list[dict | None] = []

    def _validate(source, component_lock=None, pre_save=False):
        seen.append(component_lock)
        return {"valid": True, "warnings": [], "errors": []}

    compiled_at: list[dict | None] = []

    def _compile(source, component_lock=None):
        compiled_at.append(component_lock)
        return {"compiled": True}

    monkeypatch.setattr("keel.tools.local.strategy_validate", _validate)
    monkeypatch.setattr("keel.tools.remote.strategy_compile", _compile)
    client = _Client({"/v1/strategies/str_abc": {"component_lock": STORED_LOCK}})
    OUTCOMES["keel_strategy_compose"].handler(
        {"strategy_id": "str_abc", "source": SOURCE, "dry_run": True},
        ToolContext(api_client=client, is_tty=False),
    )
    # Validated, and the dry run's compile asked for, at the stored pins.
    assert seen and seen[0] == STORED_LOCK
    assert compiled_at == [STORED_LOCK]


# ─── the caller's component_lock (DV13: an external agent applies an upgrade) ──

#: A stored lock one pin behind; the caller moves exactly that pin.
REQUESTED_LOCK = {"PriceDataLoader": 3}


def _stored_client(cls=None):
    return (cls or _Client)(
        {
            "/v1/strategies/str_abc": {
                "strategy_id": "str_abc",
                "component_lock": STORED_LOCK,
                "lock_hash": STORED_LOCK_HASH,
            }
        }
    )


def test_an_update_with_a_component_lock_saves_the_bumped_pins(monkeypatch):
    seen: list[dict | None] = []

    def _validate(source, component_lock=None, pre_save=False):
        seen.append(component_lock)
        return {"valid": True, "warnings": [], "errors": []}

    monkeypatch.setattr("keel.tools.local.strategy_validate", _validate)
    client = _stored_client()
    OUTCOMES["keel_strategy_compose"].handler(
        {"strategy_id": "str_abc", "source": SOURCE, "component_lock": REQUESTED_LOCK},
        ToolContext(api_client=client, is_tty=False),
    )
    (body,) = client.bodies("PATCH")
    # Non-vacuous: the requested pin differs from the stored one.
    assert STORED_LOCK["PriceDataLoader"] == 2
    assert body["component_lock"] == REQUESTED_LOCK
    # Guarded by the hash of the lock the pins were read against.
    assert body["expected_lock_hash"] == STORED_LOCK_HASH
    # Validated locally at the stored pins with the requested one moved —
    # the same union keel-api evolves.
    assert seen[0] == {**STORED_LOCK, **REQUESTED_LOCK}


class _Conflicting(_Client):
    """keel-api's uncoded lock-hash 409, through the real HTTP translation."""

    def patch(self, path: str, json: dict | None = None) -> Any:
        import json as _json

        from keel.errors import translate_http_error

        self.calls.append(("PATCH", path, json))
        raise translate_http_error(
            409,
            _json.dumps(
                {
                    "detail": {
                        "detail": "Component lock hash mismatch",
                        "current_lock_hash": "b" * 64,
                    }
                }
            ),
        )


def test_a_stale_lock_hash_is_surfaced_as_a_conflict():
    from keel.errors import ConflictError

    client = _stored_client(_Conflicting)
    with pytest.raises(ConflictError) as exc:
        OUTCOMES["keel_strategy_compose"].handler(
            {"strategy_id": "str_abc", "source": SOURCE, "component_lock": REQUESTED_LOCK},
            ToolContext(api_client=client, is_tty=False),
        )
    assert len(client.bodies("PATCH")) == 1  # the save was attempted once
    assert "component pins changed" in str(exc.value)
    assert "keel_strategy_compose" in (exc.value.suggestion or "")


@pytest.mark.parametrize("bad", [{"PriceDataLoader": "3"}, {"PriceDataLoader": True}, [1]])
def test_a_malformed_component_lock_is_a_usage_error(bad):
    from keel.errors import ValidationError

    client = _stored_client()
    with pytest.raises(ValidationError):
        OUTCOMES["keel_strategy_compose"].handler(
            {"strategy_id": "str_abc", "source": SOURCE, "component_lock": bad},
            ToolContext(api_client=client, is_tty=False),
        )
    assert client.bodies("PATCH") == []
