"""A guessed component name gets the real one (Q-2449).

Driven through the real `keel_components_get_many` handler with keel-api
answering 404 (the network is mocked, never the resolution), so the
swallow-then-bundled-fallback path the hosted, stdio and CLI surfaces share
is the one exercised. Before the fix every miss carried one generic line
("Component names are case-sensitive (e.g. `RSI`, not `rsi`)").

Controls: a real name resolves with no error; a nonsense name gets the
honest no-match, not a guess. Non-vacuity: every asked name produced an
entry.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from keel.errors import NotFoundError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


@pytest.fixture(autouse=True)
def _keel_api_404():
    _bootstrap()
    with patch(
        "keel.client.KeelClient._request",
        side_effect=NotFoundError("GET /v1/components/{name}: 404"),
    ):
        yield


def _get_many(names: list[str]) -> dict:
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    env = OUTCOMES["keel_components_get_many"].handler({"names": names}, ctx).to_envelope()
    return env["components"]


def test_a_guessed_name_is_answered_with_the_real_one():
    asked = ["EWMAC", "MACrossover", "rsi", "StochasticOscillator", "RateOfChange", "Zzqx"]
    out = _get_many(asked)
    assert set(out) == set(asked)  # non-vacuity: one entry per asked name
    for name in ("EWMAC", "MACrossover"):
        assert out[name]["suggestion"].startswith(f"'{name}' is a pattern, not a component")
        assert "EWMA(window=8)" in out[name]["suggestion"]
        assert "Crossover()" in out[name]["suggestion"]
    assert out["rsi"]["suggestion"].startswith("Did you mean: RSI?")
    assert out["StochasticOscillator"]["suggestion"].startswith("Did you mean: Stochastic?")
    assert out["RateOfChange"]["suggestion"].startswith("Did you mean: ROC?")
    assert out["Zzqx"]["suggestion"].startswith("No close match for 'Zzqx'.")
    for name in asked:
        assert out[name]["error_code"] == "not_found"


def test_control_real_names_resolve_unchanged():
    out = _get_many(["EWMA", "ROC"])
    assert out["EWMA"]["name"] == "EWMA" and "error" not in out["EWMA"]
    assert out["ROC"]["name"] == "ROC" and "error" not in out["ROC"]
