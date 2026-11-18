"""Tests for keel.errors."""

import json

import pytest
from keel.errors import (
    AuthError,
    ConflictError,
    EntitlementError,
    KeelError,
    NotFoundError,
    UsageError,
    ValidationError,
    translate_http_error,
)

from . import _plan_catalog as cat


# ── KeelError.to_dict() ─────────────────────────────────────────────────────


def test_keel_error_to_dict():
    e = KeelError("test message", suggestion="try this", docs_url="https://docs.example.com")
    d = e.to_dict()
    assert d["error"] == "error"
    assert d["message"] == "test message"
    assert d["exit_code"] == 1
    assert d["suggestion"] == "try this"
    assert d["docs_url"] == "https://docs.example.com"


def test_to_dict_without_optional_fields():
    e = KeelError("plain")
    d = e.to_dict()
    assert "suggestion" not in d
    assert "docs_url" not in d
    assert d["error"] == "error"
    assert d["message"] == "plain"
    assert d["exit_code"] == 1


def test_to_dict_with_only_suggestion():
    e = KeelError("msg", suggestion="hint")
    d = e.to_dict()
    assert d["suggestion"] == "hint"
    assert "docs_url" not in d


def test_to_dict_with_only_docs_url():
    e = KeelError("msg", docs_url="https://docs.example.com/foo")
    d = e.to_dict()
    assert "suggestion" not in d
    assert d["docs_url"] == "https://docs.example.com/foo"


def test_keel_error_is_exception():
    e = KeelError("test")
    assert isinstance(e, Exception)
    assert str(e) == "test"


def test_keel_error_custom_error_code():
    e = KeelError("msg", error_code="custom_code")
    assert e.error_code == "custom_code"
    assert e.to_dict()["error"] == "custom_code"


def test_keel_error_custom_exit_code():
    e = KeelError("msg", exit_code=99)
    assert e.exit_code == 99
    assert e.to_dict()["exit_code"] == 99


def test_keel_error_custom_both_codes():
    e = KeelError("msg", error_code="custom", exit_code=42)
    d = e.to_dict()
    assert d["error"] == "custom"
    assert d["exit_code"] == 42


# ── Subclass exit codes and error codes ──────────────────────────────────────


def test_subclass_exit_codes():
    assert NotFoundError("x").exit_code == 3
    assert AuthError("x").exit_code == 4
    assert ConflictError("x").exit_code == 5
    assert EntitlementError("x").exit_code == 6
    assert ValidationError("x").exit_code == 7
    assert UsageError("x").exit_code == 2


def test_subclass_error_codes():
    assert NotFoundError("x").error_code == "not_found"
    assert AuthError("x").error_code == "auth_failed"
    assert ConflictError("x").error_code == "conflict"
    assert EntitlementError("x").error_code == "insufficient_entitlements"
    assert ValidationError("x").error_code == "validation_failed"
    assert UsageError("x").error_code == "usage_error"


def test_subclass_to_dict_includes_subclass_fields():
    e = NotFoundError("widget not found", suggestion="Check the name")
    d = e.to_dict()
    assert d["error"] == "not_found"
    assert d["exit_code"] == 3
    assert d["message"] == "widget not found"
    assert d["suggestion"] == "Check the name"


def test_subclass_inherits_exception():
    for cls in (
        NotFoundError,
        AuthError,
        ConflictError,
        EntitlementError,
        ValidationError,
        UsageError,
    ):
        e = cls("test")
        assert isinstance(e, KeelError)
        assert isinstance(e, Exception)


def test_subclass_str():
    e = ValidationError("bad input")
    assert str(e) == "bad input"


# ── translate_http_error() ───────────────────────────────────────────────────


def test_translate_http_401():
    e = translate_http_error(401, "Unauthorized")
    assert isinstance(e, AuthError)
    assert e.exit_code == 4
    assert "keel auth login" in e.suggestion
    assert e.docs_url == "https://app.usekeel.io/settings?tab=api-keys"


def test_translate_http_403_scope_missing_keeps_keel_auth_login_recovery():
    """403 with no entitlement reasons in the body = scope-missing case.
    Recovery tool stays `keel_auth_login(scope='live')`."""
    e = translate_http_error(403, "Forbidden")
    assert isinstance(e, EntitlementError)
    assert e.exit_code == 6
    assert "keel_auth_login" in e.suggestion
    assert "scope='live'" in e.suggestion
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] == "keel_auth_login"
    assert env["suggested_next_action"]["args"] == {"scope": "live"}


def test_translate_http_403_plan_limit_is_facts_with_no_destination():
    """403 with a quota `code` = plan-limit case. Recovery tool MUST be
    None — re-auth doesn't add quota. The envelope `example` surfaces the
    SERVED numbers for the agent to relay; there is no `docs_url` and no
    URL anywhere (D-12: agent surfaces carry no plan destination)."""
    import json as _json

    body = _json.dumps(
        {
            "type": "https://errors.usekeel.io/forbidden",
            "title": "Forbidden",
            "status": 403,
            "detail": "You've used all 50 backtests this week.",
            "code": "quota_exhausted",
            "reasons": [
                "entitlement:insufficient:backtest_runs:limit=50:used=50:remaining=0"
                ":period=weekly:reset_epoch=1790035200"
            ],
            "quota": {
                "kind": "insufficient",
                "unit": "backtest_runs",
                "label": "backtests",
                "code": "quota_exhausted",
                "limit": 50,
                "used": 50,
                "remaining": 0,
                "period": "weekly",
                "reset_epoch": 1790035200,
            },
        }
    )
    e = translate_http_error(403, body)
    assert isinstance(e, EntitlementError)
    # The message states what happened with the served numbers and the
    # reset date. The server named no plan and no `higher_plans`, so the wall
    # says "this plan" and names no other (D-14: never guessed); no URL, and
    # — since Q-1806 — no directive to the model: every card renders
    # `message`. `retryable: false` carries the retry fact.
    assert str(e) == (
        "Backtests: 50 of 50 used on this plan; resets Tue 22 Sep. "
        "Composing, validating and reading existing results don't use this allowance."
    )
    for model_word in ("`example`", "docs_url", "do not retry", "Report"):
        assert model_word not in str(e), model_word
    assert e.to_envelope()["retryable"] is False
    assert e.docs_url is None

    serialized = __import__("json").dumps(e.to_envelope())
    assert "http" not in serialized, "no URL of any kind on a plan-limit 403 (D-12)"
    assert "billing_url" not in serialized

    env = e.to_envelope()
    # CRUCIAL: recovery_tool must be None for plan limits.
    assert env["suggested_next_action"]["tool"] is None, (
        "a plan-limit 403 must NOT point at keel_auth_login as recovery — "
        "re-auth doesn't increase quota"
    )
    # The SERVED block surfaces under `example` — label from the wire.
    assert env["example"]["unit"] == "backtest_runs"
    assert env["example"]["label"] == "backtests"
    assert env["example"]["limit"] == 50
    assert env["example"]["used"] == 50
    assert env["example"]["remaining"] == 0
    assert env["example"]["code"] == "quota_exhausted"
    # `current` stays as the legacy alias of `used` for installed clients.
    assert env["example"]["current"] == 50


def test_translate_http_403_ai_messages_quota_plan_limit_path():
    """Same plan-limit path for ai_messages exhaustion, and the LABEL
    comes from the wire — the SDK keeps no unit-label table (M0.6)."""
    import json as _json

    body = _json.dumps(
        {
            "detail": "You've reached your AI messages limit for this period.",
            "code": "quota_cap_reached",
            "quota": {
                "kind": "cap_exceeded",
                "unit": "ai_messages",
                "label": "AI messages",
                "code": "quota_cap_reached",
                "limit": 15,
                "used": 15,
                "remaining": 0,
            },
        }
    )
    e = translate_http_error(403, body)
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] is None
    assert env["example"]["unit"] == "ai_messages"
    assert env["example"]["label"] == "AI messages"


def test_the_served_label_wins_and_the_unit_table_is_the_fallback(monkeypatch):
    """M0.6, as D-14 amends it: the label is SERVED, so a label the SDK never
    heard of still renders. The one SDK table (`LIMIT_UNITS`, doc 07 §6.1)
    is the fallback for a server that sent none, pinned equal to
    `platform_auth.quota.UNIT_LABELS` (test_limit_units_*).

    Not vacuous: the unit is one no table in this repo contains, so only the
    served label could produce this string.
    """
    import json as _json

    from keel.errors import _label_for_unit

    body = _json.dumps(
        {
            "code": "quota_exhausted",
            "quota": {
                "kind": "insufficient",
                "unit": "quantum_runs",
                "label": "quantum runs",
                "code": "quota_exhausted",
                "limit": 3,
                "used": 3,
                "remaining": 0,
            },
        }
    )
    e = translate_http_error(403, body)
    assert "quantum runs" in str(e).lower()
    assert e.input["label"] == "quantum runs"
    # The mechanical fallback stays (keel_account_status reads it); plan-limit
    # copy reads the unit table's row first (`_unit_label`).
    from keel.errors import _unit_label

    assert _label_for_unit("backtest_runs") == "backtest runs"
    assert _unit_label({"unit": "backtest_runs"}) == "backtests"
    assert _label_for_unit("feature:priority_queue") == "priority queue"


def test_translate_http_403_feature_not_available_points_at_upgrade():
    """Plan doesn't include a feature → upgrade is the only path."""
    import json as _json

    body = _json.dumps(
        {
            "detail": "This feature is not available on your plan.",
            "reasons": ["entitlement:feature_not_available:feature:priority_queue"],
        }
    )
    e = translate_http_error(403, body)
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] is None
    assert env["example"]["unit"] == "feature:priority_queue"
    assert "priority queue" in env["example"]["unit_label"]


def test_translate_http_403_not_provisioned_points_at_onboarding_never_reauth():
    """keel-api Q-1519: a valid token for an account with no org is a 403
    `not_provisioned`. The fix is onboarding, so the envelope carries NO
    recovery tool — an agent must not loop on keel_auth_login."""
    import json

    body = json.dumps(
        {
            "type": "https://api.usekeel.io/errors/authorization_denied",
            "title": "Forbidden",
            "status": 403,
            "detail": "Your account is not set up yet. Complete onboarding to continue.",
            "code": "not_provisioned",
        }
    )
    e = translate_http_error(403, body)
    assert e.error_code == "not_provisioned"
    assert e.recovery_tool is None
    assert e.docs_url.endswith("/onboarding")
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] is None
    assert "onboarding" in e.suggestion


def test_translate_http_403_account_deactivated_has_no_recovery_tool():
    import json

    body = json.dumps({"status": 403, "detail": "deactivated", "code": "account_deactivated"})
    e = translate_http_error(403, body)
    assert e.error_code == "account_deactivated"
    assert e.recovery_tool is None
    assert e.to_envelope()["suggested_next_action"]["tool"] is None


def test_translate_http_403_unknown_body_falls_back_to_scope_default():
    """Garbage body that's not JSON = scope-missing fallback (preserves
    v0.4.2 behavior for callers that don't get the parseable shape)."""
    e = translate_http_error(403, "{not valid json")
    env = e.to_envelope()
    # No entitlement reasons → default scope-missing recovery.
    assert env["suggested_next_action"]["tool"] == "keel_auth_login"


# ── to_envelope() recovery-tool routing (v0.4.2) ────────────────────────────


def test_auth_error_envelope_routes_to_keel_auth_login():
    """AuthError's envelope must point agents at the MCP login tool."""
    e = AuthError("session expired")
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] == "keel_auth_login"
    assert env["suggested_next_action"]["args"] == {}


def test_entitlement_error_envelope_suggests_live_scope():
    """EntitlementError's envelope routes to keel_auth_login with scope='live'."""
    e = EntitlementError("need live scope")
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] == "keel_auth_login"
    assert env["suggested_next_action"]["args"] == {"scope": "live"}


def test_base_error_envelope_has_no_recovery_tool():
    """Plain KeelError leaves suggested_next_action.tool null (no auto-recovery)."""
    e = KeelError("some random failure")
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] is None
    assert env["suggested_next_action"]["args"] == {}


def test_envelope_with_docs_url_includes_it_in_next_action():
    e = AuthError("expired", docs_url="https://app.usekeel.io/help")
    env = e.to_envelope()
    assert env["suggested_next_action"]["docs_url"] == "https://app.usekeel.io/help"


def test_translate_http_404():
    e = translate_http_error(404, "Not found")
    assert isinstance(e, NotFoundError)
    assert e.exit_code == 3
    assert "Not found" in str(e)


def test_translate_http_404_empty_body():
    e = translate_http_error(404, "")
    assert isinstance(e, NotFoundError)
    assert "Resource not found" in str(e)


# ── paper_not_enabled (paper-trading-v2 S06 §3.2.4 (c), AC-06-28, PT2-12) ──
#
# Seeds: delete the arm ⇒ the code arm reds (the paper 404 is `not_found`
# with the route's own sentence); match on the detail sentence instead of
# the code ⇒ the second code case (a different sentence) and the uncoded
# control red.

_PAPER_OFF_404 = '{"detail": "Paper trading is not available yet.", "code": "paper_not_enabled"}'


@pytest.mark.parametrize(
    "body",
    [
        _PAPER_OFF_404,
        '{"type": "about:blank", "title": "Not Found", "status": 404, '
        '"detail": "Some other sentence.", "code": "paper_not_enabled"}',
    ],
)
def test_translate_http_404_paper_not_enabled_is_typed_on_the_code(body):
    e = translate_http_error(404, body)
    assert isinstance(e, NotFoundError)
    assert e.error_code == "paper_not_enabled"
    assert str(e) == "Paper trading is not available on this Keel environment yet."
    assert e.suggestion is None
    assert e.exit_code == 3
    env = e.to_envelope()
    assert env["code"] == "paper_not_enabled"
    assert "suggested_next_action" not in env or not (env["suggested_next_action"] or {}).get(
        "reason"
    )


@pytest.mark.parametrize(
    "body",
    [
        '{"detail": "Paper trading is not available yet."}',  # same sentence, no code
        '{"detail": "Not found", "code": "deployment_not_found"}',  # another code
        "Not found",
    ],
)
def test_translate_http_404_without_the_code_keeps_not_found(body):
    e = translate_http_error(404, body)
    assert isinstance(e, NotFoundError)
    assert e.error_code == "not_found"
    assert e.suggestion  # today's "verify the id" pointer


def test_translate_http_409():
    e = translate_http_error(409, "Conflict")
    assert isinstance(e, ConflictError)
    assert e.exit_code == 5


def test_translate_http_409_empty_body():
    e = translate_http_error(409, "")
    assert isinstance(e, ConflictError)
    assert e.suggestion is not None  # Should suggest pull


def test_translate_http_422():
    e = translate_http_error(422, "Invalid field")
    assert isinstance(e, ValidationError)
    assert e.exit_code == 7
    assert "Invalid field" in str(e)


def _coded_422(code: str, message: str, **fields) -> str:
    import json

    return json.dumps({"detail": {"code": code, "message": message, **fields}})


def test_coded_422_keeps_the_code_out_of_the_prose():
    """Q-1742: keel-api's coded refusal reached the card as
    "... 2026-09-22. (code=WINDOW_EMPTY)". The code is the agent's, in
    `detail`; the message is the server's sentence, verbatim."""
    msg = "Cannot backtest — the requested window is empty after clamping."
    e = translate_http_error(422, _coded_422("WINDOW_EMPTY", msg))
    env = e.to_envelope()
    assert env["message"] == msg
    assert "code=" not in env["message"]
    assert env["detail"] == {"code": "WINDOW_EMPTY"}
    # Q-1751: keel-api's own code is the envelope code; the class stays.
    assert env["code"] == "WINDOW_EMPTY"
    assert isinstance(e, ValidationError) and env["exit_code"] == 7


def test_an_uncoded_422_keeps_the_class_code():
    """Control arm (Q-1751): a body with no machine code — the validation
    gate's `{"detail": ..., "issues": [...]}` — still reads validation_failed.

    # SEED: drop the `error_code=` argument in translate_http_error(422) —
    # test_coded_422_keeps_the_code_out_of_the_prose reds; this stays green.
    """
    import json

    body = json.dumps({"detail": {"detail": "Strategy has 1 validation error(s).", "issues": []}})
    assert translate_http_error(422, body).to_envelope()["code"] == "validation_failed"
    assert translate_http_error(422, "plain text").to_envelope()["code"] == "validation_failed"


def test_coded_422_carries_its_fields_structurally():
    e = translate_http_error(
        422,
        _coded_422(
            "WINDOW_INVERTED",
            "start_date 2025-06-01 is after end_date 2025-01-01.",
            requested_start="2025-06-01",
            requested_end="2025-01-01",
        ),
    )
    assert str(e) == "start_date 2025-06-01 is after end_date 2025-01-01."
    assert e.detail == {
        "code": "WINDOW_INVERTED",
        "requested_start": "2025-06-01",
        "requested_end": "2025-01-01",
    }


def test_422_expectation_is_specific_to_the_error_class():
    """A window refusal is about the dates — the DSL dry-run boilerplate sent
    agents to re-validate a strategy that was fine (Q-1742)."""
    window = translate_http_error(422, _coded_422("WINDOW_EMPTY", "empty")).to_envelope()
    assert "start_date before end_date" in window["what_was_expected"]
    assert "dry_run" not in window["what_was_expected"]

    other = translate_http_error(422, _coded_422("SOMETHING_ELSE", "no")).to_envelope()
    assert "SOMETHING_ELSE" in other["what_was_expected"]
    assert "dry_run" not in other["what_was_expected"]

    # Control arm: the UNCODED validation gate (issues list, no code) keeps
    # the DSL advice, and its hint text is unchanged.
    import json

    body = json.dumps({"detail": {"detail": "Strategy has 1 validation error(s).", "issues": []}})
    dsl = translate_http_error(422, body).to_envelope()
    assert "dry_run" in dsl["what_was_expected"]
    assert dsl["message"].startswith("Strategy has 1 validation error(s). (issues=")


def test_a_run_size_refusal_names_the_three_fits_not_the_dates_or_the_dsl():
    """Q-3316: keel-api refuses a backtest too large for one worker's memory.
    The expectation names the three ways to fit; it is neither the window
    family's "change the dates" nor the DSL dry-run advice.

    # SEED (2026-10-28, reverted by reversing the edit): drop the
    # RUN_SIZE_CODES branch in _expectation_for_422 — this test reds (the
    # generic coded sentence comes back); the window and control arms of
    # test_422_expectation_is_specific_to_the_error_class stay green.
    """
    msg = (
        "This backtest is too large to run. Try one of: up to 98 symbols at 5min "
        "over this window, 15min bars for all 197 symbols, or a shorter window of "
        "up to 292 days (from 2026-01-09)."
    )
    env = translate_http_error(
        422,
        _coded_422(
            "BACKTEST_TOO_LARGE",
            msg,
            remedy={"max_symbols": 98, "finest_fitting_timeframe": "15min"},
        ),
    ).to_envelope()
    assert env["code"] == "BACKTEST_TOO_LARGE"
    assert env["message"] == msg  # the server's sentence, verbatim
    expected = env["what_was_expected"]
    assert "fewer symbols" in expected and "coarser timeframe" in expected
    assert "shorter window" in expected
    assert "dry_run" not in expected and "start_date before end_date" not in expected
    # Founder 2026-10-28: no internal context in user copy (Q-3316).
    for internal in ("memory", "worker", "GiB", "cell"):
        assert internal.lower() not in expected.lower(), internal


def _issues_refusal(text: str, *codes: str) -> str:
    """keel-api's validator-refusal body — `HTTPException(detail={"detail": …,
    "issues": [issue, …]})` (routers/strategies.py, Q-1695/Q-2207): no
    top-level code; each issue carries its catalog code."""
    import json

    return json.dumps(
        {
            "detail": {
                "detail": text,
                "issues": [
                    {"severity": "error", "code": c, "tier": "gate", "message": "m"} for c in codes
                ],
            }
        }
    )


def test_a_coded_400_carries_the_refusal_code_the_way_keel_api_reads_it():
    """Q-2564: a parse-refused save's envelope (and so the mcp-server audit
    row, which records the envelope `code`) names the rule — the FIRST
    issue's code, exactly what keel-api's own `refusal_code` stashes for the
    `api_request refused` line — instead of `usage_error`, the code of a
    wrong-ARGUMENT call. The class, exit code and message are unchanged; the
    issues ride `detail`, the 422/409 shape.

    Non-vacuity: two bodies, two different codes, each read back — the code
    comes from the body, not from a constant.

    # SEED (run 2026-10-08): `error_code=code` → `error_code=None` in
    # translate_http_error(400) — this arm reds on `'usage_error' ==
    # 'PARAM_EXPRESSION_NOT_ALLOWED'`; the uncoded control below stays green.
    """
    for code in ("PARAM_EXPRESSION_NOT_ALLOWED", "VARIABLE_ASSIGN_SHAPE"):
        e = translate_http_error(400, _issues_refusal("Invalid strategy input: x", code))
        env = e.to_envelope()
        assert env["code"] == code
        assert isinstance(e, UsageError) and env["exit_code"] == 2
        assert env["message"].startswith("Invalid strategy input: x")
        assert env["detail"]["issues"][0]["code"] == code
    # The first issue is the refusal's code — keel-api's reader, mirrored.
    both = translate_http_error(400, _issues_refusal("x", "VARIABLE_ASSIGN_SHAPE", "TYPE_MISMATCH"))
    assert both.to_envelope()["code"] == "VARIABLE_ASSIGN_SHAPE"


def test_control_an_uncoded_400_is_still_a_usage_error():
    """Every 400 that names no code keeps today's envelope: the deploy-link
    token verdict (plain text), the `{"error": …}` base_ref shape, an empty
    issues list, a non-JSON body."""
    import json

    bodies = [
        '{"detail": "The deploy link failed verification."}',
        json.dumps({"detail": {"error": "base_ref_unresolved", "detail": "No HEAD."}}),
        _issues_refusal("Strategy has 1 validation error(s).", *()),
        "<html>bad gateway</html>",
        "",
    ]
    for body in bodies:
        e = translate_http_error(400, body)
        env = e.to_envelope()
        assert isinstance(e, UsageError)
        assert env["code"] == "usage_error", body
        assert env["exit_code"] == 2
        assert "detail" not in env, body


def test_an_issues_only_422_names_its_gate_issue():
    """The same reader on the 422 arm (Q-2564): keel-api's lock-gate refusal
    (`{"detail": …, "issues": [LOCK_ERROR]}`, no top-level code) audits
    LOCK_ERROR — the code the dry run records for the same failure (Q-2563)
    — not `validation_failed`. The hint keys on `detail.code`, which this
    shape still lacks, so the DSL advice is unchanged."""
    e = translate_http_error(
        422, _issues_refusal("Cannot create strategy: component version lock failed", "LOCK_ERROR")
    )
    env = e.to_envelope()
    assert isinstance(e, ValidationError) and env["exit_code"] == 7
    assert env["code"] == "LOCK_ERROR"
    assert env["detail"]["issues"][0]["code"] == "LOCK_ERROR"
    assert "dry_run" in env["what_was_expected"]


def test_uncoded_409_keeps_its_recovery_hint_in_the_text():
    import json

    body = json.dumps({"detail": {"detail": "Source hash mismatch", "current_source_hash": "abc"}})
    e = translate_http_error(409, body)
    assert str(e) == "Source hash mismatch (current_source_hash=abc)"


def test_translate_http_422_empty_body():
    e = translate_http_error(422, "")
    assert isinstance(e, ValidationError)
    assert "Validation failed" in str(e)


def test_translate_http_500():
    e = translate_http_error(500, "Server error")
    assert isinstance(e, KeelError)
    assert e.exit_code == 1
    assert e.retryable is True


def test_translate_http_502():
    e = translate_http_error(502, "Bad gateway")
    assert isinstance(e, KeelError)
    assert e.retryable is True


def test_translate_http_429():
    e = translate_http_error(429, "Rate limited")
    assert isinstance(e, KeelError)
    assert e.retryable is True
    assert e.suggestion is not None


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_empty_message():
    e = KeelError("")
    assert str(e) == ""
    d = e.to_dict()
    assert d["message"] == ""


def test_raise_and_catch_keel_error():
    with pytest.raises(KeelError) as exc_info:
        raise KeelError("boom")
    assert str(exc_info.value) == "boom"


def test_raise_and_catch_subclass_as_keel_error():
    with pytest.raises(KeelError):
        raise NotFoundError("missing")


def test_raise_and_catch_subclass_directly():
    with pytest.raises(NotFoundError):
        raise NotFoundError("missing")


# ── The quota vocabulary (mcp-conversion M0.5/M0.6/M1.2) ────────────────


def _quota_403(**quota) -> str:
    """A 403 body exactly as the current keel-api emits it."""
    import json as _json

    return _json.dumps(
        {
            "title": "Forbidden",
            "status": 403,
            "detail": "Insufficient entitlements",
            "code": quota.get("code"),
            "quota": quota,
        }
    )


def test_code_branches_without_reading_reason_strings():
    """D-8: the client branches on `code`, not on `reasons[]` prose.

    Proved by a body whose reasons are DELIBERATELY unparseable garbage:
    the old string-sniffing path can extract nothing from it, so a
    correctly-branching client can only be reading `code` + `quota`.
    """
    import json as _json

    body = _json.dumps(
        {
            "detail": "Insufficient entitlements",
            "code": "quota_exhausted",
            "reasons": ["entitlement:MOVED:the:producer:changed:vocabulary:again"],
            "quota": {
                "kind": "insufficient",
                "unit": "backtest_runs",
                "label": "backtests",
                "code": "quota_exhausted",
                "limit": 50,
                "used": 50,
                "remaining": 0,
            },
        }
    )
    e = translate_http_error(403, body)
    assert e.recovery_tool is None, "a quota code must never route to re-auth"
    assert e.input["limit"] == 50 and e.input["used"] == 50
    assert "Backtests: 50 of 50 used" in str(e)


def test_non_quota_403_code_never_becomes_a_plan_limit():
    """A machine code that is NOT a quota refusal keeps the scope shape —
    the client never guesses a plan limit from an unknown code."""
    import json as _json

    body = _json.dumps({"detail": "Forbidden", "code": "scope_missing", "reasons": []})
    e = translate_http_error(403, body)
    assert e.recovery_tool == "keel_auth_login"
    assert e.input is None


def test_old_server_without_code_still_parses_reasons():
    """The reason-sniffing path survives ONLY as the labelled fallback for
    a keel-api older than the quota contract — and it still produces the
    same normalized shape (kind, unit, limit, used, derived code)."""
    import json as _json

    body = _json.dumps(
        {
            "detail": "Insufficient entitlements",
            "reasons": ["entitlement:insufficient:backtest_runs:limit=30:current=30"],
        }
    )
    e = translate_http_error(403, body)
    assert e.recovery_tool is None
    assert e.input["kind"] == "insufficient"
    assert e.input["limit"] == 30
    assert e.input["used"] == 30, "the legacy `current=` must normalize to `used`"
    assert e.input["code"] == "quota_exhausted", "the code is derived when the server sent none"


def test_only_no_grant_kinds_may_say_the_plan_does_not_include_it():
    """Q-1590's user-visible half, at the message layer.

    A spent grant and a grant that never existed are different sentences.
    Control arm: the same unit, same numbers, only `kind` differs.
    """
    spent = translate_http_error(
        403,
        _quota_403(
            kind="insufficient",
            unit="backtest_runs",
            label="backtests",
            code="quota_exhausted",
            limit=50,
            used=50,
            remaining=0,
        ),
    )
    never = translate_http_error(
        403,
        _quota_403(
            kind="no_grants",
            unit="backtest_runs",
            label="backtests",
            code="plan_feature_unavailable",
        ),
    )
    assert "doesn't include" not in str(spent), (
        "someone who spent 50 of 50 must never be told the plan lacks the unit"
    )
    assert "Backtests: 50 of 50 used" in str(spent)
    assert "This plan doesn't include backtests." in str(never)


# ── D-14: the wall names the plans that include more (doc 07 §4/§5) ─────────

_WALL_QUOTA = {
    "kind": "insufficient",
    "unit": "backtest_runs",
    "label": "backtests",
    "code": "quota_exhausted",
    "period": "30d",
    "reset_epoch": 1796428800,  # Sat 5 Dec 2026 00:00 UTC
}


def _served_wall(plan: str, limit: int, *, higher: list | None = None) -> dict:
    """The quota block keel-api serves on a v2 plan's backtest wall: the
    catalog's plans that include more, with their limits (D-10) — the SDK
    must project the names and drop the numbers."""
    served = cat.higher_plans(plan, "backtest_runs") if higher is None else higher
    for entry in served:
        entry["limit"] = int(cat._value(entry["plan"], entry["version"], "backtest_runs"))
        entry["period"] = "30d"
    return {
        **_WALL_QUOTA,
        "plan": plan,
        "plan_name": cat.display_name(plan),
        "higher_plans": served,
        "limit": limit,
        "used": limit,
        "remaining": 0,
    }


def _wall(plan: str, limit: int) -> str:
    return str(translate_http_error(403, _quota_403(**_served_wall(plan, limit))))


def test_free_plan_wall_is_the_exact_d14_copy():
    """THE golden (doc 07 §5, Free v2): own numbers and plan, the reset date,
    the plans that include more, where plans change, what keeps working.

    SEEDS (run 2026-11-16, each reverted by reversing the edit):
    * `quota_plans_line` returning None — this reds; the Pro control below
      stays green (it never had a clause);
    * `more_plan_names` reading every served entry's `plan` id instead of
      its `name` — this reds on "trader and pro"."""
    assert _wall("free", 25) == (
        "Backtests: 25 of 25 used on Free; resets Sat 5 Dec. "
        "Trader and Pro include more. Plans are changed in the Keel web app. "
        "Composing, validating and reading existing results don't use this allowance."
    )


@pytest.mark.parametrize("plan", ["free", "trader", "pro"])
def test_every_v2_wall_names_exactly_the_catalog_plans_that_include_more(plan):
    """Generated from plans.yaml (doc 07 §6.1): the clause lists exactly the
    catalog's higher plans for this plan and unit; the top plan gets none,
    and then no "Plans are changed" line either."""
    from keel.errors import more_clause

    limit = int(cat._value(plan, "v2", "backtest_runs"))
    text = _wall(plan, limit)
    expected = more_clause("consumable", cat.names(cat.higher_plans(plan, "backtest_runs")))
    assert text.startswith(f"Backtests: {limit:,} of {limit:,} used on {cat.display_name(plan)};")
    if expected is None:
        assert "include" not in text and "Plans are changed" not in text, text
    else:
        assert f" {expected} Plans are changed in the Keel web app. " in text, text
    others = [n for n in cat.all_plan_names() if n != cat.display_name(plan)]
    for name in others:
        if name not in cat.names(cat.higher_plans(plan, "backtest_runs")):
            assert name not in text, (plan, name, text)


def test_the_wall_carries_the_higher_plans_names_never_their_numbers():
    """keel-api's `higher_plans` (with each plan's limit) never reaches
    `example`, the message or any sentence; only the NAMES do, as
    `more_plans` (founder Q1). Non-vacuous: the body DOES carry the limits.

    SEED (run 2026-11-16): project `higher_plans` whole in `_quota_context`
    — this reds on the key and on "1000"; the golden stays green."""
    import json as _json

    served = _served_wall("free", 25)
    assert [h["limit"] for h in served["higher_plans"]] == [1000, 4000], "non-vacuity"
    e = translate_http_error(403, _quota_403(**served))
    blob = _json.dumps(e.to_envelope())
    assert "higher_plans" not in blob and "1000" not in blob and "4000" not in blob
    assert e.input["more_plans"] == ["Trader", "Pro"]
    assert e.input["plan"] == "free" and e.input["plan_name"] == "Free"


def test_an_old_server_without_higher_plans_gets_no_clause_and_no_guess():
    """Absent `higher_plans` renders NO more clause (never guessed), and an
    absent `plan_name` falls back to the served plan id capitalised — the
    same identity, not a lookup."""
    quota = {**_WALL_QUOTA, "plan": "free", "limit": 25, "used": 25, "remaining": 0}
    text = str(translate_http_error(403, _quota_403(**quota)))
    assert text.startswith("Backtests: 25 of 25 used on Free; resets Sat 5 Dec. ")
    assert "include" not in text and "Plans are changed" not in text


def test_plan_copy_guard_refuses_prices_links_and_pitch_words():
    """D-14 §7: what stays banned. Each seed is copy Keel HAS shipped or a
    pitch the spec names; the control arm is the D-14 wall's own lines and
    plain-words references, which pass."""
    from keel.errors import assert_plan_copy

    for pitch in (
        "Trader is $49 a month.",
        "Starter is $29/month.",
        "Plans are listed at https://app.usekeel.io/settings?tab=billing&from=agent.",
        "Prices are at usekeel.io/pricing.",
        "Pay through Stripe checkout.",
        "Upgrade now to run more.",
        "Unlock more backtests.",
        "A limited offer.",
        "Save with an annual plan.",
        "A better deal on Pro.",
        "`upgrade_options` lists them.",
        "Higher plans include more backtests: Starter 500 a week, Trader unlimited.",
    ):
        with pytest.raises(ValueError):
            assert_plan_copy(pitch)
    for kept in (
        "Backtests: 25 of 25 used on Free; resets Sat 5 Dec.",
        "Trader and Pro include more. Plans are changed in the Keel web app.",
        "Pro includes more. Plans are changed in the Keel web app.",
        "Keel's pricing page lists the plans.",
        "Composing, validating and reading existing results don't use this allowance.",
    ):
        assert assert_plan_copy(kept) == kept


def test_more_clause_matches_the_shared_parity_cases():
    """doc 07 §6.2: the SDK renderer and libs/platform_auth's render every row
    of the shared table identically — the clause, the wall's plan line, and
    each unit's kind (a cap the plan does not grant at all is a feature).
    Non-vacuous: every kind, unit rows and name-count rows, the empty case."""
    from keel.errors import (
        LIMIT_KINDS,
        WEB_APP_SENTENCE,
        limit_kind,
        more_clause,
        quota_plans_line,
    )

    table = cat.limit_copy_cases()
    assert table["plans_changed"] == WEB_APP_SENTENCE
    assert tuple(table["kinds"]) == LIMIT_KINDS
    cases = table["cases"]
    assert {c["kind"] for c in cases} == set(LIMIT_KINDS)
    assert any(c["expected_more_clause"] is None for c in cases)
    unit_rows = 0
    for case in cases:
        assert more_clause(case["kind"], case["names"]) == case["expected_more_clause"], case
        if case["unit"] is None:
            continue
        unit_rows += 1
        info = {"unit": case["unit"], "more_plans": case["names"]}
        if case["kind"] == "feature":
            info.update(kind="no_cap_grants", code="plan_feature_unavailable")
        assert limit_kind(info) == case["kind"], case
        assert quota_plans_line(info) == case["expected_wall_more"], case
    assert unit_rows >= 30, unit_rows


def test_errors_name_no_tool_absent_on_the_listed_profile(monkeypatch):
    """Spec 05 §4 item 7: on the listed (hosted) profile `keel_auth_login`
    and `keel_audit_list_last` are not on tools/list, so the 401, the 404
    and the non-quota 403 name neither. Control: the full profile keeps the
    local recovery (the tests above)."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    auth = translate_http_error(401, "Unauthorized")
    missing = translate_http_error(404, "Not found")
    scope = translate_http_error(403, "Forbidden")
    for e in (auth, missing, scope):
        env = e.to_envelope()
        blob = str(env)
        assert "keel_auth_login" not in blob and "keel_audit_list_last" not in blob, blob
        assert env["suggested_next_action"]["tool"] is None
    assert "Reconnect" in auth.suggestion

    monkeypatch.delenv("KEEL_SERVER_PROFILE")
    assert translate_http_error(401, "Unauthorized").recovery_tool == "keel_auth_login"
    assert "keel_audit_list_last" in translate_http_error(404, "x").suggestion


def test_uncoded_409_names_no_cli_step_where_there_is_no_checkout(monkeypatch):
    """Spec 05 R-L4: the uncoded 409's 'keel strategy pull' is a CLI step
    against a local checkout; a hosted or listed server has neither, so its
    suggestion states the conflict instead. Control: the full local profile
    keeps the pull step."""
    local = translate_http_error(409, "")
    assert "keel strategy pull" in local.suggestion
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    listed = translate_http_error(409, "")
    assert listed.suggestion, "the listed 409 still carries a suggestion"
    blob = str(listed.to_envelope())
    assert "keel strategy" not in blob and "pull" not in blob, blob
    monkeypatch.delenv("KEEL_SERVER_PROFILE")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    assert "keel strategy" not in translate_http_error(409, "").suggestion


def test_401_api_keys_url_also_resolves_from_the_environment(monkeypatch):
    """Same defect class, same file: the 401's token page was hardcoded."""
    monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.tailf4d598.ts.net")
    e = translate_http_error(401, "Unauthorized")
    assert e.docs_url == "https://staging-app.tailf4d598.ts.net/settings?tab=api-keys"


def test_render_quota_sentence_states_both_halves_and_the_reset():
    """M1.2 / doc 07 §5: one count line from the served block — the date,
    no window adjective; the notice adds the more clause from 75%."""
    from keel.errors import render_quota_notice, render_quota_sentence

    line = render_quota_sentence(
        {
            "unit": "backtest_runs",
            "label": "backtests",
            "limit": 50,
            "used": 44,
            "remaining": 6,
            "period": "weekly",
            "resets_at": "2026-09-22T00:00:00Z",
            "tier": "warn",
        }
    )
    assert line == "6 of 50 backtests left; resets Tue 22 Sep."
    block = {
        "unit": "backtest_runs",
        "label": "backtests",
        "limit": 25,
        "used": 21,
        "remaining": 4,
        "resets_at": "2026-12-05T00:00:00Z",
        "higher_plans": cat.higher_plans("free", "backtest_runs"),
    }
    for tier in ("warn", "critical", "exhausted"):
        assert render_quota_notice({**block, "tier": tier}) == (
            "4 of 25 backtests left; resets Sat 5 Dec. Trader and Pro include more."
        ), tier
    # Control: the 50% notice stays a bare count (founder Q3).
    assert render_quota_notice({**block, "tier": "notice"}) == (
        "4 of 25 backtests left; resets Sat 5 Dec."
    )


def test_render_quota_sentence_omits_what_the_server_did_not_send():
    """No period → no window word. No reset → no reset clause. Unlimited
    or number-less → no sentence at all, rather than a null-number one
    ("you have  left" is the Q-1590 shape)."""
    from keel.errors import render_quota_sentence

    assert (
        render_quota_sentence({"label": "live strategies", "limit": 1, "used": 1, "remaining": 0})
        == "0 of 1 live strategies left."
    )
    assert render_quota_sentence({"unit": "backtest_runs", "unlimited": True}) is None
    assert render_quota_sentence({"unit": "backtest_runs", "label": "backtests"}) is None
    assert render_quota_sentence("not a block") is None


def test_reset_instant_never_fabricates_one():
    """A malformed or absent instant reads as 'not told', never as now."""
    from keel.errors import quota_reset_instant

    assert quota_reset_instant({"resets_at": "tomorrow-ish"}) is None
    assert quota_reset_instant({}) is None
    assert quota_reset_instant(None) is None
    assert quota_reset_instant(True) is None, "a bool is not an epoch"
    assert quota_reset_instant(1790035200).year == 2026
    assert quota_reset_instant("2026-09-22T00:00:00Z").day == 22


# ── The plan change: the 30-day cycle and the timeframe floor (spec 01 §C11) ─


def test_the_30_day_cycle_reads_this_30_day_period_and_never_monthly():
    """§C11: `30d` is "this 30-day period" in the sentence. The run notice and
    the reset clause both come from the server; "monthly" (a billing cadence
    the D-12 scan refuses) appears nowhere."""
    from keel.errors import quota_period_phrase, render_quota_sentence

    block = {
        "unit": "backtest_runs",
        "label": "backtests",
        "limit": 25,
        "used": 21,
        "remaining": 4,
        "period": "30d",
        "resets_at": "2026-11-29T14:00:00Z",
    }
    assert quota_period_phrase(block) == " this 30-day period"
    # D-14 rule 8: plan-limit copy names no window; the date carries it.
    assert render_quota_sentence(block) == "4 of 25 backtests left; resets Sun 29 Nov."
    # Control: the weekly unit keeps its phrase.
    assert quota_period_phrase({**block, "period": "weekly"}) == " this week"


def test_a_30_day_wall_headline_has_no_period_adjective():
    """§C11 (no adjective entry): a `30d` wall reads "Backtests used — …",
    never "Monthly backtests" (which the neutral-wall guard would refuse) and
    never "Weekly backtests" (which would be false)."""
    quota = {**_WALL_QUOTA, "period": "30d", "reset_epoch": 1795960800}
    free = str(
        translate_http_error(403, _quota_403(**quota, plan="free", limit=25, used=25, remaining=0))
    )
    assert free == (
        "Backtests: 25 of 25 used on Free; resets Sun 29 Nov. "
        "Composing, validating and reading existing results don't use this allowance."
    )
    paid = str(
        translate_http_error(
            403, _quota_403(**quota, plan="trader", limit=1000, used=1000, remaining=0)
        )
    )
    assert paid.startswith("Backtests: 1,000 of 1,000 used on Trader;")
    assert "Weekly" not in free + paid and "onthly" not in free + paid


#: The §L7 sentence for the current Free plan's 1h floor, exactly.
_FLOOR_1H = (
    "This plan runs paper and live strategies at a 1-hour timeframe or longer; "
    "this limit does not reset on a clock."
)


def test_the_timeframe_floor_refusal_is_the_floor_wall_with_structured_facts():
    """§C10/§L7 + doc 07 §5: keel-api's 422 SERVING_CLOCK_BELOW_PLAN_FLOOR
    keeps its code and `floor`/`requested` on `detail`; the message is the
    floor wall naming the plans that run faster, generated from the catalog,
    and `detail` carries them as names only (their floors never)."""
    from keel.errors import PLAN_FLOOR_SUGGESTION, assert_plan_copy

    faster = cat.faster_plans("free")
    for entry in faster:
        entry["floor"] = cat.floor_of(entry["plan"])
    e = translate_http_error(
        422,
        _coded_422(
            "SERVING_CLOCK_BELOW_PLAN_FLOOR",
            _FLOOR_1H,
            floor=cat.floor_of("free"),
            requested="15min",
            plan="free",
            plan_name="Free",
            higher_plans=faster,
        ),
    )
    env = e.to_envelope()
    assert isinstance(e, ValidationError)
    assert env["code"] == "SERVING_CLOCK_BELOW_PLAN_FLOOR"
    assert env["message"] == (
        "Paper and live on Free need 1h or slower; this is 15min. "
        "Change `target_timeframe` to 1h or slower. "
        "Trader and Pro run faster. Plans are changed in the Keel web app."
    )
    assert env["detail"] == {
        "code": "SERVING_CLOCK_BELOW_PLAN_FLOOR",
        "floor": "1h",
        "requested": "15min",
        "plan": "free",
        "plan_name": "Free",
        "more_plans": cat.names(faster),
    }
    assert env["what_was_expected"] == PLAN_FLOOR_SUGGESTION
    assert env["retryable"] is False
    for text in (env["message"], env["what_was_expected"]):
        assert assert_plan_copy(text) == text
    # An old server (no plan, no higher plans): the floor wall, no clause.
    old = translate_http_error(
        422,
        _coded_422("SERVING_CLOCK_BELOW_PLAN_FLOOR", _FLOOR_1H, floor="1h", requested="15min"),
    )
    assert str(old) == (
        "Paper and live on this plan need 1h or slower; this is 15min. "
        "Change `target_timeframe` to 1h or slower."
    )
    # Control: any other coded 422 keeps the generic coded advice.
    other = translate_http_error(422, _coded_422("WINDOW_EMPTY", "empty")).to_envelope()
    assert other["what_was_expected"] != PLAN_FLOOR_SUGGESTION


# ── Both pots on the wall (plan change spec 01 §C6) ──────────────────────

#: A week-1 compute wall on the current Free plan, exactly as keel-api serves
#: it (test_plan_both_pots.py pins the server side): the first-week part is
#: spent first, so it is empty, and the plan's own pot holds 19 s -- short
#: of one submit's 20 s reservation.
_BOTH_POTS_QUOTA = {
    "kind": "insufficient",
    "unit": "backtest_compute_seconds",
    "label": "backtest compute time",
    "code": "quota_exhausted",
    "limit": 2250,
    "used": 2231,
    "remaining": 19,
    "period": "30d",
    "reset_epoch": 1795960800,  # Sun 29 Nov 2026 14:00 UTC
    "first_week_remaining": 0,
    "first_week_ends_epoch": 1793973600,  # Fri 6 Nov 2026 14:00 UTC
    "first_week_granted": 1500,
    "first_week_then_remaining": 19,
    "first_week_then_limit": 750,
    "plan": "free",
}


def test_both_pots_on_the_30_day_wall():
    """`example` carries both pots as a `first_week` block (allow-listed
    keys); the wall's words carry no first-week line (Q-7601 fix round: a
    clause rides a backtests line only, and only while the first week has
    some left; this compute wall's part is spent). The exact words are pinned
    once, in test_plan_limits_first_week_golden.py.

    SEED (run 2026-10-30): `first_week_from_reason` returning None reds this
    test; the v1 control below stays green. Reverted by reversing the edit."""
    e = translate_http_error(403, _quota_403(**_BOTH_POTS_QUOTA))
    text = str(e)
    assert "first-week" not in text and "period" not in text, text
    assert text.startswith(
        f"Backtest compute time: {_BOTH_POTS_QUOTA['remaining']} s of 2,250 s left on Free"
    ), text
    assert e.input["first_week"] == {
        "granted": 1500,
        "remaining": 0,
        "ends_at": "2026-11-06T14:00:00Z",
        "then_remaining": 19,
        "then_limit": 750,
    }


def test_a_v1_first_week_wall_is_unchanged():
    """Control: a v1 plan-first wall carries only the first-week pair (no
    plan pot is served for it), so its lines and `example` are today's."""
    quota = {
        k: v
        for k, v in _BOTH_POTS_QUOTA.items()
        if k not in ("first_week_granted", "first_week_then_remaining", "first_week_then_limit")
    }
    e = translate_http_error(403, _quota_403(**quota))
    assert "first-week" not in str(e)
    assert "first_week" not in e.input


# ─── RI-237: a research route's nested 403, and every other 403 unchanged ────
# SEED (run 2026-11-14, reversed): drop the `_research_refusal(payload)` call in
# `_translate_403` -> the two research arms red (the message is the raw body,
# the code is None) while the byte-identical control stays green.

#: Non-research 403 bodies: plain text, unparseable, the platform quota block,
#: keel-api's nested non-research refusals (live sharing, a screen flag, the
#: anon tier), provisioning, a nested quota code WITHOUT words, a string detail.
_NON_RESEARCH_403 = [
    "Forbidden",
    "{not valid json",
    json.dumps(
        {"type": "about:blank", "title": "Forbidden", "status": 403, "detail": "Monthly backtests used.",
         "code": "quota_exhausted", "unit": "backtests_per_month", "used": 50, "limit": 50,
         "resets_at": "2026-12-01T00:00:00Z", "plan": "free"}
    ),
    json.dumps({"detail": {"code": "LIVE_SHARE_CREATION_DISABLED", "message": "Live Share creation and editing are not enabled."}}),
    json.dumps({"detail": {"code": "LIVE_SHARE_CAPABILITY_DISABLED", "message": "Live Share capability is not enabled: x.", "capabilities": ["x"]}}),
    json.dumps({"detail": {"code": "SCREENER_PUBLISH_DISABLED"}}),
    json.dumps({"detail": {"error": "anon_tier_disabled", "message": "Anonymous access is not enabled on this environment."}}),
    json.dumps({"status": 403, "detail": "no org", "code": "not_provisioned"}),
    json.dumps({"detail": {"code": "quota_exhausted"}}),
    json.dumps({"detail": "Organisation not found."}),
]  # fmt: skip

#: sha256 of every envelope above on both server kinds, computed by the code
#: BEFORE RI-237 (the same on 3.13 and 3.11): the research arm moves none.
_NON_RESEARCH_403_DIGEST = "5f66f5a18cae2fcdbbd4305faa143a8333039010ef2746532b6a37504aae30e4"


def _envelopes_403(monkeypatch, bodies: list[str]) -> list[dict]:
    import keel.errors as errors

    out = []
    for local in (True, False):
        monkeypatch.setattr(errors, "_local_tools_registered", lambda v=local: v)
        for body in bodies:
            e = translate_http_error(403, body)
            out.append(
                {
                    "local": local,
                    "class": type(e).__name__,
                    "recovery": [e.recovery_tool, e.recovery_tool_args],
                    "env": e.to_envelope(),
                }
            )
    return out


def test_every_non_research_403_is_byte_identical_to_before_ri_237(monkeypatch):
    import hashlib

    envelopes = _envelopes_403(monkeypatch, _NON_RESEARCH_403)
    assert len(envelopes) == 20  # non-vacuity: ten bodies on both server kinds
    digest = hashlib.sha256(json.dumps(envelopes, sort_keys=True, default=str).encode()).hexdigest()
    assert digest == _NON_RESEARCH_403_DIGEST


@pytest.mark.parametrize(
    "code,unit",
    [("quota_exhausted", "research_studies"), ("quota_cap_reached", "research_backtests_max"),
     ("plan_feature_unavailable", "feature:research_template_map")],
)  # fmt: skip
def test_a_research_403_serves_its_own_words_never_the_body_or_a_login(monkeypatch, code, unit):
    words = "This study was not run and nothing was charged."
    body = json.dumps({"detail": {"code": code, "message": words, "unit": unit}})
    for item in _envelopes_403(monkeypatch, [body]):  # both server kinds alike
        env = item["env"]
        assert item["class"] == "EntitlementError" and item["recovery"] == [None, None]
        assert env["message"] == words and env["code"] == "insufficient_entitlements"
        assert env["detail"] == {"code": code, "unit": unit}
        assert "live" not in env["what_was_expected"] and "login" not in env["what_was_expected"]


def test_a_research_coded_403_keeps_its_own_code(monkeypatch):
    words = "Sign in to keep studies."
    body = json.dumps({"detail": {"code": "RESEARCH_SIGN_IN_REQUIRED", "message": words}})
    for item in _envelopes_403(monkeypatch, [body]):
        assert (
            item["env"]["code"] == "RESEARCH_SIGN_IN_REQUIRED" and item["env"]["message"] == words
        )
        assert item["env"]["detail"] == {"code": "RESEARCH_SIGN_IN_REQUIRED"}
