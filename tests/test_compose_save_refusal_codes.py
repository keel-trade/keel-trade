"""A save keel-api REFUSES records the refusal's rule codes on its audit row (Q-2564).

Wave 2026-10-08, subject 01: two saves refused 400 for `PARAM_EXPRESSION_NOT_ALLOWED`
and `VARIABLE_ASSIGN_SHAPE` audited `error_code: usage_error`, `outcome:
{is_error: true}` and no rule code — the row of an agent passing a bad
ARGUMENT — while the 422 `COMPILE_FAILED` save carried its gate code but not
the validator's findings in the same body. `keel_strategy_compose` recorded
`validation_ok` + `issue_codes` only after a successful write (Q-2368); its
refusal arms re-raised and recorded nothing, and the 400 arm of
`translate_http_error` never read the body's `issues`.

Now a refused save records `validation_ok: false` with the codes the refusal
names (`validation.errors`, else error-severity `issues`, else the body's own
gate code when the body is a validation refusal — Q-2563's dry-run rule), and
the re-raised error's envelope `code` is the refusal's first code, which is
what mcp-server audits as `error_code`.

The arms stub ONLY the network (the fake client raises exactly what
`KeelClient` raises: `translate_http_error(status, body)` over keel-api's own
body shapes) and the local pre-save validator — stubbed to report the source
VALID, so a recorded code can only have come from the server's refusal body.

Proof it can fail (run 2026-10-08, reverted by reversing each edit):

* `_record_refused_save(e)` removed from the update arm → the update arms red
  (slot `{}`); the create arms, the controls and the envelope arm green.
* `error_code=code` → `error_code=None` in `translate_http_error(400)` → the
  four parse arms red on their ENVELOPE line (`'usage_error' ==
  'PARAM_EXPRESSION_NOT_ALLOWED'`) with the slot assertion above it passing,
  plus `test_errors.py`'s coded-400 arm; the compile arms and the controls
  green — the two seams are independent, which is why both are asserted.

Proof it is not vacuous: the fake bodies are built from a parameter and the
recorded code is asserted equal to THAT parameter across two different codes
(a constant could satisfy only one), and the stubbed local validator is
asserted to have reported no error for the same source.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from keel.errors import KeelError, translate_http_error
from keel.hosting import close_request_outcome, current_request_outcome, open_request_outcome
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext

from .test_outcomes_strategy import _FakeClient


_bootstrap()

SID = "str_01k5g8w2y3z4a5b6c7d8e9f0g1"
SOURCE = "from keel import *"


def _parse_refusal(code: str) -> KeelError:
    """keel-api's parse refusal: `HTTPException(400, detail={"detail": …,
    "issues": [DSLParseError.to_issue(tier="gate")]})` (routers/strategies.py)."""
    body = {
        "detail": {
            "detail": "Invalid strategy input: Function calls not allowed in parameter values",
            "issues": [{"severity": "error", "code": code, "tier": "gate", "message": "m"}],
        }
    }
    return translate_http_error(400, json.dumps(body))


def _compile_refusal(error_codes: list[str]) -> KeelError:
    """keel-api's 422 `_compile_failed_detail` + `_unsaved_compile_detail`
    (Q-2258): the gate code, and the validator's findings on the same input —
    `issues` is EVERY issue (a warning included), `validation.errors` only
    the errors."""
    body = {
        "detail": {
            "code": "COMPILE_FAILED",
            "detail": "This source does not compile, so it was not saved: boom.",
            "compilation_error": "boom",
            "issues": [{"severity": "warning", "code": "UNRESOLVED_UNIVERSE"}]
            + [{"severity": "error", "code": c} for c in error_codes],
            "validation": {
                "valid": not error_codes,
                "errors": [{"severity": "error", "code": c} for c in error_codes],
                "warnings": [{"severity": "warning", "code": "UNRESOLVED_UNIVERSE"}],
            },
            "current_head": {"sequence_number": 3},
        }
    }
    return translate_http_error(422, json.dumps(body))


@pytest.fixture
def local_validator_says_valid(monkeypatch):
    import keel.tools.outcomes.strategy_compose as mod

    seen: list[str] = []

    def fake(src, **_kw):
        seen.append(src)
        return {"ok": True, "warnings": [], "errors": [], "lock": None}

    monkeypatch.setattr(mod, "_try_local_validate", fake)
    return seen


def _refused(args: dict, client: Any) -> tuple[dict, KeelError]:
    ctx = ToolContext(api_client=client, is_tty=False)
    token = open_request_outcome()
    try:
        with pytest.raises(KeelError) as info:
            OUTCOMES["keel_strategy_compose"].handler(args, ctx)
        return dict(current_request_outcome()), info.value
    finally:
        close_request_outcome(token)


@pytest.mark.parametrize("code", ["PARAM_EXPRESSION_NOT_ALLOWED", "VARIABLE_ASSIGN_SHAPE"])
def test_a_parse_refused_update_records_the_rule_code(local_validator_says_valid, code):
    fake = _FakeClient(patch_payloads={f"/v1/strategies/{SID}": _parse_refusal(code)})
    slot, err = _refused({"source": SOURCE, "strategy_id": SID}, fake)
    assert local_validator_says_valid == [
        SOURCE
    ]  # the code is the SERVER's, not the local validator's
    assert slot == {"validation_ok": False, "issue_codes": [code]}
    assert err.to_envelope()["code"] == code


@pytest.mark.parametrize("code", ["PARAM_EXPRESSION_NOT_ALLOWED", "VARIABLE_ASSIGN_SHAPE"])
def test_a_parse_refused_create_records_the_rule_code(local_validator_says_valid, code):
    fake = _FakeClient(post_payloads={"/v1/strategies": _parse_refusal(code)})
    slot, err = _refused({"source": SOURCE, "name": "MyStrat"}, fake)
    assert slot == {"validation_ok": False, "issue_codes": [code]}
    assert err.to_envelope()["code"] == code


def test_a_compile_refused_save_records_the_validators_error_codes_once(local_validator_says_valid):
    """The validator's errors are the failure (never its warnings, never the
    gate code beside them) — one failure, recorded once."""
    fake = _FakeClient(
        patch_payloads={
            f"/v1/strategies/{SID}": _compile_refusal(["UNKNOWN_PARAM", "TYPE_MISMATCH"])
        }
    )
    slot, err = _refused({"source": SOURCE, "strategy_id": SID}, fake)
    assert slot == {"validation_ok": False, "issue_codes": ["UNKNOWN_PARAM", "TYPE_MISMATCH"]}
    assert err.to_envelope()["code"] == "COMPILE_FAILED"  # Q-1751, unchanged
    assert err.recovery_tool == "keel_strategy_get"  # the Q-2258 recovery hint still rides


def test_a_compile_refused_save_the_validator_could_not_name_records_the_gate_code(
    local_validator_says_valid,
):
    """Q-2563's rule on the save: a COMPILE_FAILED with no validator error is
    never a codeless validation failure."""
    fake = _FakeClient(patch_payloads={f"/v1/strategies/{SID}": _compile_refusal([])})
    slot, _ = _refused({"source": SOURCE, "strategy_id": SID}, fake)
    assert slot == {"validation_ok": False, "issue_codes": ["COMPILE_FAILED"]}


def test_control_a_refusal_that_judged_no_source_records_no_verdict(local_validator_says_valid):
    """An uncoded 409 (the lock-hash conflict), a coded 409 that is not a
    validation refusal and a 404: nothing judged the source, so the slot stays
    empty here (the adapter alone stamps `is_error`)."""
    refusals = [
        translate_http_error(409, json.dumps({"detail": {"detail": "Source hash mismatch"}})),
        translate_http_error(
            409,
            json.dumps(
                {"code": "HEAD_MOVED", "detail": "HEAD moved", "status": 409, "title": "Conflict"}
            ),
        ),
        translate_http_error(404, "Not found"),
    ]
    for refusal in refusals:
        fake = _FakeClient(patch_payloads={f"/v1/strategies/{SID}": refusal})
        slot, _ = _refused({"source": SOURCE, "strategy_id": SID}, fake)
        assert slot == {}, type(refusal).__name__


def test_control_a_successful_save_records_as_before(local_validator_says_valid):
    commit = "cmt_01k5g8w2y3z4a5b6c7d8e9f0g1"
    fake = _FakeClient(
        patch_payloads={
            f"/v1/strategies/{SID}": {
                "strategy_id": SID,
                "current_sequence": 2,
                "head_commit_id": commit,
            }
        }
    )
    ctx = ToolContext(api_client=fake, is_tty=False)
    token = open_request_outcome()
    try:
        OUTCOMES["keel_strategy_compose"].handler({"source": SOURCE, "strategy_id": SID}, ctx)
        assert dict(current_request_outcome()) == {"commit_id": commit, "validation_ok": True}
    finally:
        close_request_outcome(token)


def test_refusal_issue_codes_reads_the_body_in_authority_order():
    """The reader itself, over the shapes keel-api sends: errors win over
    issues, error-severity issues over warnings, the gate code last and only
    for a validation refusal."""
    from keel.tools.outcomes.strategy_compose import _refusal_issue_codes

    assert _refusal_issue_codes(None) == []
    assert _refusal_issue_codes({"code": "HEAD_MOVED"}) == []  # not a validation refusal
    assert _refusal_issue_codes(
        {"code": "COMPILE_FAILED", "issues": [], "validation": {"errors": []}}
    ) == ["COMPILE_FAILED"]
    assert _refusal_issue_codes(
        {"issues": [{"severity": "warning", "code": "W"}, {"severity": "error", "code": "E"}]}
    ) == ["E"]
    assert _refusal_issue_codes(
        {
            "code": "COMPILE_FAILED",
            "issues": [{"code": "I"}],
            "validation": {"errors": [{"code": "V"}]},
        }
    ) == ["V"]
