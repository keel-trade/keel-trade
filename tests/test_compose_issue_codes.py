"""An invalid compose records WHICH rules fired on its audit row (Q-2368).

Before this, `keel_strategy_compose` recorded only `validation_ok: false`: the
audit could say an agent's first draft was invalid but never which rule it
broke, so "which rule trips agents first" was unanswerable. The tool now also
records the validator's rule codes — Keel's own catalog vocabulary — through
the one outcome channel (`keel.hosting.record_outcome`), on the dry run and on
the save. Never the source, a message or an argument: only `code`, and
`record_outcome` drops anything that is not rule-code shaped.

The dry-run arms run the REAL local validator (nothing about validation is
stubbed) and fake only the network: the compile endpoint answers compiled,
because an INVALID_UNIVERSE source compiles — validation is feedback, not a
gate.

Proof it can fail (run 2026-10-04, reverted by reversing the edit): both
`issue_codes=_error_codes(...)` arguments removed from strategy_compose.py →
the invalid dry-run arm and the save arm red (the slot holds only
`validation_ok` / `commit_id`); the valid controls stay green.

Proof it is not vacuous: the fixture's own validator output is asserted first
to hold exactly one ERROR whose code is INVALID_UNIVERSE, and the control
source is asserted valid through the same validator.
"""

from __future__ import annotations

from typing import Any

import pytest
from keel.hosting import close_request_outcome, current_request_outcome, open_request_outcome
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext

from .test_outcomes_strategy import _FakeClient


_bootstrap()

VALID = """Globals(target_timeframe='1d', bar_offset='12h')
Universe(mode='manual', symbols=['BTC', 'ETH'])
Execution(rebalance='buffered', buffer_threshold=0.2)
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name='issue_codes_fixture')
"""

#: A manual universe with nothing selected: a genuine work-in-progress state
#: the validator reports as INVALID_UNIVERSE (and keel-api still saves).
INVALID = VALID.replace(
    "Universe(mode='manual', symbols=['BTC', 'ETH'])", "Universe(mode='manual')"
)
assert INVALID != VALID


@pytest.fixture
def compile_ok(monkeypatch):
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile",
        lambda source, component_lock=None: {"compiled": True, "fingerprint": "f", "graph": {}},
        raising=False,
    )


def _recorded(args: dict, client: Any = None) -> dict:
    ctx = ToolContext(api_client=client or _FakeClient(), is_tty=False)
    token = open_request_outcome()
    try:
        OUTCOMES["keel_strategy_compose"].handler(args, ctx)
        return dict(current_request_outcome())
    finally:
        close_request_outcome(token)


def test_the_fixture_really_trips_one_rule_and_the_control_none():
    """Non-vacuity, from the validator itself."""
    from keel.tools.outcomes.strategy_compose import _try_local_validate

    invalid = _try_local_validate(INVALID, pre_save=True)
    assert [e["code"] for e in invalid["errors"]] == ["INVALID_UNIVERSE"], invalid["errors"]
    valid = _try_local_validate(VALID, pre_save=True)
    assert valid["errors"] == [], valid["errors"]


def test_an_invalid_dry_run_records_its_rule_codes(compile_ok):
    slot = _recorded({"source": INVALID, "name": "x", "dry_run": True})
    assert slot == {"validation_ok": False, "issue_codes": ["INVALID_UNIVERSE"]}


def test_control_a_valid_dry_run_records_no_issue_codes(compile_ok):
    slot = _recorded({"source": VALID, "name": "x", "dry_run": True})
    assert slot == {"validation_ok": True}


def test_a_save_with_validation_errors_records_their_codes_only(monkeypatch):
    """The save path: errors' codes ride beside the commit; warnings, a
    bare-string error and every message stay out."""
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src, **_kw: {
            "ok": False,
            "warnings": [{"code": "UNRESOLVED_UNIVERSE", "message": "w"}],
            "errors": [
                {"code": "INVALID_UNIVERSE", "message": "Buy BTC when RSI < 30"},
                "validator-unavailable: boom",
                {"code": "TYPE_MISMATCH", "message": "m"},
            ],
            "lock": None,
        },
    )
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
    slot = _recorded({"source": "from keel import *", "name": "MyStrat"}, fake)
    assert slot == {
        "commit_id": commit,
        "validation_ok": False,
        "issue_codes": ["INVALID_UNIVERSE", "TYPE_MISMATCH"],
    }
