"""Tests for keel.errors."""

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
    # The message states what happened with the served numbers, names the
    # reset, and stops. No plan named (the server sent none → the paid
    # shape), no URL, and — since Q-1806 — no directive to the model: every
    # card renders `message`. `retryable: false` carries the retry fact.
    assert str(e) == (
        "Weekly backtests used — 50 of 50 on your plan. They reset Tue 22 Sep 00:00 UTC. "
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


def test_sdk_keeps_no_unit_label_table(monkeypatch):
    """M0.6: the label is SERVED, so a label the SDK never heard of still
    renders. Two hand-synced tables existed before and had diverged.

    Not vacuous: the unit is one no table in this repo contains, so a
    surviving local table could not produce this string.
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
    # And the only local fallback is mechanical, not a table.
    assert _label_for_unit("backtest_runs") == "backtest runs"
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
    assert "Backtests used — 50 of 50" in str(e)


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
    assert "does not include" not in str(spent), (
        "someone who spent 50 of 50 must never be told the plan lacks the unit"
    )
    assert "Backtests used — 50 of 50" in str(spent)
    assert "Your plan does not include backtests." in str(never)


# ── D-12: the neutral wall (mcp-conversion 04 §4.1, exact copy) ─────────────

_WALL_QUOTA = {
    "kind": "insufficient",
    "unit": "backtest_runs",
    "label": "backtests",
    "code": "quota_exhausted",
    "period": "weekly",
    "reset_epoch": 1790640000,  # Tue 29 Sep 2026 00:00 UTC
    # keel-api still serves the other plans (D-10) — the SDK must drop them.
    "higher_plans": [
        {"plan": "starter", "limit": 500, "period": "weekly"},
        {"plan": "trader", "unlimited": True},
    ],
}


def _wall(plan: str, limit: int) -> str:
    return str(
        translate_http_error(
            403,
            _quota_403(**_WALL_QUOTA, plan=plan, limit=limit, used=limit, remaining=0),
        )
    )


def test_free_plan_wall_is_the_exact_d12_copy():
    """04 §4.1 free plan, quote-level. The ONE other-plans sentence rides
    here and only here.

    SEEDS (run 2026-09-28, each reverted by reversing the edit):
    * `quota_headline` back to the pre-D-12 `on_plan = f" on {plan}"` —
      this and the paid-wall test red;
    * the free gate removed from `quota_plans_line` — the paid-wall test
      reds (the D-12 scan refuses "Paid plans" off the free plan) while
      THIS test stays green: the control arm that proves the plan
      sentence reacts to the plan and nothing else."""
    from keel.errors import format_reset_instant, quota_reset_instant

    assert format_reset_instant(quota_reset_instant(1790640000)) == "Tue 29 Sep 00:00 UTC"
    assert _wall("free", 50) == (
        "Weekly backtests used — 50 of 50 on the Free plan. They reset Tue 29 Sep 00:00 UTC. "
        "Plans are changed in the Keel web app. "
        "Composing, validating and reading existing results don't use this allowance."
    )
    assert "Paid plans" not in _wall("free", 50)


def test_paid_plan_wall_states_the_limit_and_reset_only():
    """04 §4.1 any paid plan: first + third sentence, "on your plan". No
    plan sentence at all (Q2) — a paid user already knows plans exist."""
    for plan in ("starter", "trader", "pro"):
        text = _wall(plan, 500)
        assert text == (
            "Weekly backtests used — 500 of 500 on your plan. They reset Tue 29 Sep 00:00 UTC. "
            "Composing, validating and reading existing results don't use this allowance."
        ), plan
        assert "Paid plans" not in text and "Plans are changed" not in text
        assert "Starter" not in text


def test_the_wall_never_carries_the_served_higher_plans():
    """keel-api's `higher_plans` never reaches `example`, the message or
    any sentence — on any plan. Non-vacuous: the body DOES carry them.

    SEED (run 2026-09-28): re-project `higher_plans` in `_quota_context`
    — this reds; the paid-wall equality (control) stays green."""
    import json as _json

    assert _WALL_QUOTA["higher_plans"], "non-vacuity: the served block names other plans"
    for plan in ("free", "starter"):
        e = translate_http_error(
            403, _quota_403(**_WALL_QUOTA, plan=plan, limit=50, used=50, remaining=0)
        )
        blob = _json.dumps(e.to_envelope())
        assert "higher_plans" not in blob and "Starter" not in blob and "Trader" not in blob
        assert e.input["plan"] == plan, "the caller's OWN plan is a fact the wall keeps"


def test_neutral_wall_guard_rejects_every_d12_family():
    """The widened scan (D-12): each seeded sentence is one the wall HAS
    carried. Control: the exact free-wall sentences pass on the free plan,
    and the paid-plans sentence is refused off it."""
    from keel.errors import assert_neutral_wall_text

    for pitch in (
        "Higher plans include more backtests: Starter 500 a week, Trader unlimited.",
        "See plans in Keel.",
        "Plans are listed at https://app.usekeel.io/settings?tab=billing&from=agent.",
        "Compare the plan tiers.",
        "The builder fee is lower on Pro.",
        "Prices are at usekeel.io/pricing.",
        "`upgrade_options` lists them.",
        "Starter is $29/month.",
    ):
        with pytest.raises(ValueError):
            assert_neutral_wall_text(pitch, free_plan=True)
    from keel.errors import FREE_PLAN_SENTENCE

    assert FREE_PLAN_SENTENCE == "Plans are changed in the Keel web app."
    assert assert_neutral_wall_text(FREE_PLAN_SENTENCE, free_plan=True) == FREE_PLAN_SENTENCE
    # The pre-fallback sentence stays admissible on the free plan by the scan
    # (an entitlement fact, not a pitch) and refused off it — the guard is
    # unchanged; the wall simply no longer says it.
    allowed = "Paid plans include more backtests; plans are changed in the Keel web app."
    assert assert_neutral_wall_text(allowed, free_plan=True) == allowed
    with pytest.raises(ValueError, match="off the free plan"):
        assert_neutral_wall_text(allowed, free_plan=False)
    note = "Composing, validating and reading existing results don't use this allowance."
    assert assert_neutral_wall_text(note, free_plan=False) == note


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
    """M1.2: one factual line from the served block — facts and stop."""
    from keel.errors import render_quota_sentence

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
    assert line == "6 of 50 backtests left this week; they reset Tue 22 Sep 00:00 UTC."


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
