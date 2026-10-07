"""The listed-surface contract fixes of 2026-10-01 (Q-2080; Q-2263, Q-2264,
Q-2266; the Lane A mirrors Q-2255 and Q-2258).

Each arm drives the REAL handler (HTTP faked at `KeelClient`) and reads what
the tool returns or sends — never the copy. Seeds (run 2026-10-01, each
reverted by reversing the edit):

* `share_create.permission_for`: return `"view"` for both values — the
  derived-permission arm reds; the contradiction arm reds.
* `strategy_memory.served_note`: return the row unchanged — the notes arm
  reds naming `source_conversation_id`.
* `live_monitor.canonical_view`: return `view` unchanged — the alias arm
  reds with an "Unknown view" refusal.
* `_ownership.ownership_envelope_fields`: drop the `is_listed_profile`
  branch — the readiness arm reds naming `live_readiness_blockers`.
* `strategy_compose`: delete the `COMPILE_FAILED` branch — the refusal arm
  reds (no recovery tool, no saved version in the suggestion); the compiling
  control stays green (Q-2258, run 2026-10-05).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from unittest import mock
from unittest.mock import patch

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()


@pytest.fixture
def listed_hosted(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")


# ── keel_share_create: the strict routes (Q-2255) ───────────────────────


def _share_calls(args: dict) -> tuple[list, dict]:
    posts: list[tuple[str, dict]] = []

    def fake_post(self, path, json=None, **_):
        posts.append((path, json))
        return {"share_id": "gDXjURKqWPs8CZ4eXdqAI", "include_source": json.get("include_source")}

    with (
        patch("keel.client.KeelClient.post", fake_post),
        patch("keel.client.KeelClient.get", lambda self, path, **_: {}),
    ):
        env = OUTCOMES["keel_share_create"].handler(args, ToolContext()).to_envelope()
    return posts, env


def test_permission_is_derived_from_include_source_and_never_sent():
    posts, env = _share_calls({"target_id": "str_x", "include_source": True})
    assert posts == [
        ("/v1/strategies/str_x/share-links", {"include_source": True, "pin_latest_backtest": True})
    ]
    assert env["permission"] == "fork" and env["include_source"] is True
    posts, env = _share_calls({"target_id": "str_x"})
    assert posts[0][1] == {"include_source": False, "pin_latest_backtest": True}
    assert env["permission"] == "view"


def test_backtest_shares_send_include_source_and_expiry_only():
    future = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0).isoformat()
    posts, env = _share_calls({"target_id": "btr_x", "include_source": True, "expires_at": future})
    assert posts == [
        ("/v1/backtests/btr_x/share-link", {"include_source": True, "expires_at": future})
    ]
    assert env["share_url"].startswith("https://usekeel.io/share/gDXjURKqWPs8CZ4eXdqAI")


def test_a_contradictory_permission_is_refused_before_anything_publishes():
    for args in (
        {"target_id": "str_x", "include_source": False, "permission": "fork"},
        {"target_id": "str_x", "include_source": True, "permission": "view"},
    ):
        with pytest.raises(KeelError) as exc:
            _share_calls(args)
        assert exc.value.error_code == "invalid_permission"
    # Control: a permission that AGREES with include_source is accepted.
    posts, _ = _share_calls({"target_id": "str_x", "include_source": True, "permission": "fork"})
    assert "permission" not in posts[0][1]


@pytest.mark.parametrize(
    "value",
    ["2026-09-30", "2026-12-31T00:00:00", "2020-01-01T00:00:00Z", "yesterday", 5],
)
def test_expires_at_must_be_a_future_timezone_aware_instant(value):
    with pytest.raises(KeelError) as exc:
        _share_calls({"target_id": "str_x", "expires_at": value})
    assert exc.value.error_code == "invalid_expires_at"


def test_listed_share_schema_carries_permission(listed_hosted):
    """Reversed 2026-10-01 (Anthropic directory review): `permission` is applied
    exactly as requested, so the listed schema serves it again."""
    from keel.tools.outcomes._mcp_adapter import effective_input_schema

    props = effective_input_schema(OUTCOMES["keel_share_create"])["properties"]
    assert props["permission"]["enum"] == ["view", "fork"]
    assert set(props) == {"target_id", "target_type", "include_source", "permission", "expires_at"}
    assert "backtest share alike" in props["include_source"]["description"]


# ── keel_strategy_notes_read: no conversation ids (Q-2266) ──────────────


def test_notes_never_carry_source_conversation_id():
    rows = {
        "data": [
            {
                "id": "m1",
                "note": "baseline 1.2",
                "role": "agent",
                "source_conversation_id": "cnv_9",
            },
            {"id": "m2", "note": "risk: HYPE", "role": "user", "source_conversation_id": None},
        ]
    }
    with patch("keel.client.KeelClient.get", lambda self, path, **_: rows):
        env = OUTCOMES["keel_strategy_notes_read"].handler({"strategy_id": "str_x"}, ToolContext())
    notes = env.to_envelope()["notes"]
    assert [n["id"] for n in notes] == ["m1", "m2"]  # non-vacuous: both rows served
    assert not any("source_conversation_id" in n for n in notes)
    assert notes[0]["note"] == "baseline 1.2" and notes[0]["role"] == "agent"


def test_listed_notes_add_omits_role_and_drops_it_when_sent(listed_hosted):
    from keel.tools.outcomes._mcp_adapter import _make_handler, effective_input_schema

    assert "role" not in effective_input_schema(OUTCOMES["keel_strategy_notes_add"])["properties"]
    sent: list[dict] = []

    def fake_post(self, path, json=None, **_):
        sent.append(json)
        return {"id": "m3", "created_at": "2026-10-01T00:00:00Z"}

    from keel.hosting import bind_request_credentials, clear_request_credentials

    handler = _make_handler(OUTCOMES["keel_strategy_notes_add"], frozenset())
    reset = bind_request_credentials(token="t", api_url="https://api.test")
    try:
        with patch("keel.client.KeelClient.post", fake_post):
            out = json.loads(handler(strategy_id="str_x", note="hello", role="user"))
    finally:
        clear_request_credentials(reset)
    assert "code" not in out, out
    assert sent == [{"note": "hello", "role": "agent"}]


# ── keel_live_monitor: the view rule and the listed spellings ───────────


def _monitor(args: dict) -> tuple[list[str], dict]:
    paths: list[str] = []

    def fake_get(self, path, **_):
        paths.append(path)
        return {"ok": True}

    with patch("keel.client.KeelClient.get", fake_get):
        env = OUTCOMES["keel_live_monitor"].handler(args, ToolContext()).to_envelope()
    return paths, env


def test_omitted_view_is_portfolio_without_a_deployment_and_overview_with_one():
    paths, env = _monitor({})
    assert env["view"] == "portfolio" and paths[0] == "/v1/deployments/portfolio/summary"
    paths, env = _monitor({"deployment_id": "dep_1"})
    assert env["view"] == "overview" and paths[0] == "/v1/deployments/dep_1"


@pytest.mark.parametrize(
    "listed, shared",
    [("trade_history", "trades"), ("order_history", "orders"), ("funding_payments", "funding")],
)
def test_listed_view_spellings_reach_the_same_endpoint(listed, shared):
    paths_listed, env_listed = _monitor({"deployment_id": "dep_1", "view": listed})
    paths_shared, env_shared = _monitor({"deployment_id": "dep_1", "view": shared})
    assert paths_listed == paths_shared == [f"/v1/deployments/dep_1/{shared}"]
    assert env_listed["view"] == env_shared["view"] == shared


def test_view_declares_no_default_on_either_profile(listed_hosted):
    from keel.tools.outcomes._mcp_adapter import effective_input_schema

    tool = OUTCOMES["keel_live_monitor"]
    assert "default" not in tool.input_schema["properties"]["view"]
    assert "default" not in effective_input_schema(tool)["properties"]["view"]
    assert "trade_history" in effective_input_schema(tool)["properties"]["view"]["enum"]
    assert "trades" not in effective_input_schema(tool)["properties"]["view"]["enum"]


# ── the readiness fields: no live column on the listed surface ──────────


_PROJECTION = {
    "overall_status": "validating",
    "next_recommended_action": {"kind": "backtest"},
    "missing_evidence": ["baseline"],
    "live_readiness_blockers": ["no_live_account"],
}


def test_listed_readiness_fields_drop_the_live_column(listed_hosted):
    from keel.tools.outcomes._ownership import ownership_envelope_fields

    fields = ownership_envelope_fields(dict(_PROJECTION))
    assert "live_readiness_blockers" not in fields
    assert fields["missing_evidence"] == ["baseline"]  # the research half is kept


def test_full_profile_readiness_fields_keep_the_live_column(monkeypatch):
    """Control arm: the local/CLI profile is unchanged."""
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    from keel.tools.outcomes._ownership import ownership_envelope_fields

    assert ownership_envelope_fields(dict(_PROJECTION))["live_readiness_blockers"] == [
        "no_live_account"
    ]


# ── keel_connection_check: hosted output names no plumbing ──────────────


def test_hosted_connection_check_names_no_url_exception_or_local_remedy(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("HOME", str(tmp_path))
    from keel.hosting import bind_request_credentials, clear_request_credentials

    def boom(self, path, **_):
        raise RuntimeError("http://keel-api.prod.svc.cluster.local:8080 refused")

    reset = bind_request_credentials(
        token="t", api_url="http://keel-api.prod.svc.cluster.local:8080"
    )
    try:
        with (
            patch("keel.auth.get_identity", side_effect=RuntimeError("secret token junk")),
            patch("keel.client.KeelClient.get", boom),
            mock.patch.dict(os.environ, {"KEEL_API_KEY": "k"}),
        ):
            with pytest.raises(KeelError) as exc:
                OUTCOMES["keel_connection_check"].handler({}, ToolContext())
    finally:
        clear_request_credentials(reset)
    text = json.dumps(exc.value.to_envelope())
    for forbidden in ("cluster.local", "secret token junk", "keel auth login", "KEEL_API_KEY"):
        assert forbidden not in text, forbidden
    assert "checks" in text and "Reconnect this connector" in text


# ── keel_strategy_compose: a save that does not compile is refused (Q-2258) ──


def test_a_non_compiling_save_is_the_error_envelope_naming_the_saved_version():
    """keel-api refuses a source that does not compile with 422 COMPILE_FAILED
    and writes nothing; the tool raises that envelope — never a save, never a
    "draft" — and names the version the strategy still is."""
    from keel.errors import translate_http_error

    source = "Globals(target_timeframe='1d')\nUniverse(mode='static', symbols=['BTC'])\nPipeline([PriceDataLoader()], name='x')\n"
    refusal = translate_http_error(
        422,
        json.dumps(
            {
                "detail": {
                    "code": "COMPILE_FAILED",
                    "detail": (
                        "This source does not compile, so it was not saved and nothing "
                        "changed: unknown component Frobnicate. The strategy is still v3."
                    ),
                    "compilation_error": "unknown component Frobnicate",
                    "current_head": {"sequence_number": 3, "commit_id": "cmt_3"},
                    "issues": [{"code": "UNKNOWN_COMPONENT", "severity": "error"}],
                }
            }
        ),
    )
    patches: list[str] = []

    def fake_patch(self, path, json=None, **_):
        patches.append(path)
        raise refusal

    def fake_get(self, path, **_):
        if path.endswith("/source"):
            return {"source": source, "sequence_number": 3}
        if path.startswith("/v1/strategies/str_x/versions"):
            return []
        return {"strategy_id": "str_x", "current_sequence": 3, "name": "x"}

    with (
        patch("keel.client.KeelClient.patch", fake_patch),
        patch("keel.client.KeelClient.get", fake_get),
        patch(
            "keel.tools.outcomes.strategy_compose._try_local_validate",
            lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "lock": {}},
        ),
        pytest.raises(KeelError) as exc,
    ):
        OUTCOMES["keel_strategy_compose"].handler(
            {"strategy_id": "str_x", "source": source}, ToolContext()
        )
    assert patches == ["/v1/strategies/str_x"]  # non-vacuous: the save was sent
    env = exc.value.to_envelope()
    assert env["code"] == "COMPILE_FAILED"
    assert env["detail"]["compilation_error"] == "unknown component Frobnicate"
    assert env["suggested_next_action"]["tool"] == "keel_strategy_get"
    assert env["suggested_next_action"]["args"]["strategy_id"] == "str_x"
    assert "nothing was saved" in env["what_was_expected"]
    assert "still v3" in env["what_was_expected"]


def test_a_compiling_save_carries_no_draft_marker():
    """Control arm: the ordinary save is unchanged."""
    source = "Globals(target_timeframe='1d')\nUniverse(mode='static', symbols=['BTC'])\nPipeline([PriceDataLoader()], name='x')\n"
    patched = {"strategy_id": "str_x", "current_sequence": 4}

    def fake_get(self, path, **_):
        if path.endswith("/source"):
            return {"source": source, "sequence_number": 4}
        if path.startswith("/v1/strategies/str_x/versions"):
            return []
        if path == "/v1/backtests":
            return {"data": []}
        return {"strategy_id": "str_x", "current_sequence": 4, "name": "x"}

    with (
        patch("keel.client.KeelClient.patch", lambda self, path, json=None, **_: patched),
        patch("keel.client.KeelClient.get", fake_get),
        patch(
            "keel.tools.outcomes.strategy_compose._try_local_validate",
            lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "lock": {}},
        ),
    ):
        env = (
            OUTCOMES["keel_strategy_compose"]
            .handler({"strategy_id": "str_x", "source": source}, ToolContext())
            .to_envelope()
        )
    assert "draft" not in env and "compilation_error" not in env
    assert env["version"] == 4
    assert "error" not in env


# ── keel_strategy_compose: name/thesis are create-only on the listed surface ──
# (Q-2265, founder 2026-10-01). Seed: delete the `is_listed_profile()` arm in
# `strategy_compose._handler` — the listed arm reds (name/description reach
# the PATCH body, no note) while the full-profile control stays green.

_CREATE_ONLY_SOURCE = "Globals(target_timeframe='1d')\nUniverse(mode='static', symbols=['BTC'])\nPipeline([PriceDataLoader()], name='x')\n"


def _compose_update(args: dict) -> tuple[list[dict], dict]:
    bodies: list[dict] = []

    def fake_patch(self, path, json=None, **_):
        bodies.append(json)
        return {"strategy_id": "str_x", "current_sequence": 5}

    def fake_get(self, path, **_):
        if path.endswith("/source"):
            return {"source": _CREATE_ONLY_SOURCE, "sequence_number": 5}
        if path.startswith("/v1/strategies/str_x/versions"):
            return []
        if path == "/v1/backtests":
            return {"data": []}
        return {"strategy_id": "str_x", "current_sequence": 5, "name": "x"}

    with (
        patch("keel.client.KeelClient.patch", fake_patch),
        patch("keel.client.KeelClient.get", fake_get),
        patch(
            "keel.tools.outcomes.strategy_compose._try_local_validate",
            lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "lock": {}},
        ),
    ):
        env = OUTCOMES["keel_strategy_compose"].handler(args, ToolContext()).to_envelope()
    return bodies, env


@pytest.fixture
def listed_profile(monkeypatch):
    # The listed schema/semantics without hosted credential binding: the
    # handler is driven directly with a faked client.
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")


def test_listed_update_never_sends_name_or_thesis_and_says_so(listed_profile):
    bodies, env = _compose_update(
        {
            "strategy_id": "str_x",
            "source": _CREATE_ONLY_SOURCE,
            "name": "renamed",
            "description": "new thesis",
        }
    )
    assert len(bodies) == 1  # non-vacuous: the save happened
    assert "source" in bodies[0]
    assert "name" not in bodies[0] and "description" not in bodies[0]
    assert "`name` and `description` not applied" in env["note"]


def test_listed_update_without_name_or_thesis_carries_no_note(listed_profile):
    bodies, env = _compose_update({"strategy_id": "str_x", "source": _CREATE_ONLY_SOURCE})
    assert len(bodies) == 1 and "note" not in env


def test_full_profile_update_still_renames(monkeypatch):
    """Control arm: the CLI / local profile keeps update semantics."""
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    bodies, env = _compose_update(
        {
            "strategy_id": "str_x",
            "source": _CREATE_ONLY_SOURCE,
            "name": "renamed",
            "description": "new thesis",
        }
    )
    assert bodies[0]["name"] == "renamed" and bodies[0]["description"] == "new thesis"
    assert "note" not in env


def test_listed_schema_says_name_and_thesis_are_create_only(listed_hosted):
    from keel.tools.outcomes._mcp_adapter import effective_input_schema

    props = effective_input_schema(OUTCOMES["keel_strategy_compose"])["properties"]
    for key in ("name", "description"):
        text = props[key]["description"]
        assert "only on create" in text.lower() or "used only on create" in text.lower(), key
        assert "whenever the thesis changes" not in text and "it sets the name" not in text
