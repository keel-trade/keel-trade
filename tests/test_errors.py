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
    for cls in (NotFoundError, AuthError, ConflictError, EntitlementError, ValidationError, UsageError):
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


def test_translate_http_403_billing_quota_points_at_billing_url():
    """403 with entitlement reasons (e.g. backtest_runs exhausted) = billing
    case. Recovery tool MUST be None — re-auth doesn't add quota. The
    docs_url + envelope `example` must surface plan/unit/limit/current
    for the agent to relay to the user."""
    import json as _json

    body = _json.dumps({
        "type": "https://errors.usekeel.io/forbidden",
        "title": "Forbidden",
        "status": 403,
        "detail": "You've reached your backtest runs limit for this period. Upgrade for more.",
        "reasons": ["entitlement:cap_exceeded:backtest_runs:limit=30:current=30"],
    })
    e = translate_http_error(403, body)
    assert isinstance(e, EntitlementError)
    # Message names the unit + directs at the upgrade URL.
    assert "backtest" in e.args[0].lower() or "backtest" in str(e).lower()
    assert "usekeel.io/settings?tab=billing" in str(e)
    # Suggestion explicitly says NO mcp tool fixes this.
    assert "no MCP tool" in e.suggestion or "no MCP tool" in str(e)
    # docs_url is billing.
    assert e.docs_url == "https://app.usekeel.io/settings?tab=billing"

    env = e.to_envelope()
    # CRUCIAL: recovery_tool must be None for billing limits.
    assert env["suggested_next_action"]["tool"] is None, (
        "billing-quota 403 must NOT point at keel_auth_login as recovery — "
        "re-auth doesn't increase quota, only billing upgrade does"
    )
    # Plan / unit / limit context surfaces under `example`.
    assert env["example"]["unit"] == "backtest_runs"
    assert env["example"]["limit"] == 30
    assert env["example"]["current"] == 30
    assert env["example"]["billing_url"].endswith("/settings?tab=billing")


def test_translate_http_403_ai_messages_quota_billing_path():
    """Same billing-quota path for ai_messages exhaustion."""
    import json as _json

    body = _json.dumps({
        "detail": "You've reached your AI chat messages limit for this period.",
        "reasons": ["entitlement:cap_exceeded:ai_messages:limit=15:current=15"],
    })
    e = translate_http_error(403, body)
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] is None
    assert env["example"]["unit"] == "ai_messages"
    assert "AI chat messages" in env["example"]["unit_label"]


def test_translate_http_403_feature_not_available_points_at_upgrade():
    """Plan doesn't include a feature → upgrade is the only path."""
    import json as _json

    body = _json.dumps({
        "detail": "This feature is not available on your plan.",
        "reasons": ["entitlement:feature_not_available:feature:priority_queue"],
    })
    e = translate_http_error(403, body)
    env = e.to_envelope()
    assert env["suggested_next_action"]["tool"] is None
    assert env["example"]["unit"] == "feature:priority_queue"
    assert "priority queue" in env["example"]["unit_label"]


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
