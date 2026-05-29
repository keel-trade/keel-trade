"""Tests for the shared paginated-response extractor.

Covers the canonical keel-api shape `{data:[...], pagination:{cursor, has_more}}`
plus the legacy `items` / `events` / `accounts` / `notes` fallbacks, plus
bare-list edge cases. Also includes per-tool regression tests against the
canonical shape — pre-v0.4.2 these returned empty lists for every paginated
endpoint because the handlers only looked at the legacy keys.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from keel.tools.outcomes._pagination import extract_paginated


# ── Pure helper ──────────────────────────────────────────────────────────


def test_canonical_shape_extracts_data_and_cursor():
    """The keel-api canonical {data, pagination} shape must be the priority."""
    payload = {
        "data": [{"id": "a"}, {"id": "b"}],
        "pagination": {"cursor": "cur_abc", "has_more": True},
    }
    items, cursor = extract_paginated(payload)
    assert items == [{"id": "a"}, {"id": "b"}]
    assert cursor == "cur_abc"


def test_canonical_shape_no_cursor_when_last_page():
    payload = {
        "data": [{"id": "x"}],
        "pagination": {"cursor": None, "has_more": False},
    }
    items, cursor = extract_paginated(payload)
    assert items == [{"id": "x"}]
    assert cursor is None


def test_legacy_items_fallback():
    payload = {"items": [{"id": "a"}], "next_cursor": "next123"}
    items, cursor = extract_paginated(payload)
    assert items == [{"id": "a"}]
    assert cursor == "next123"


def test_legacy_events_fallback():
    payload = {"events": [{"action": "x"}]}
    items, cursor = extract_paginated(payload)
    assert items == [{"action": "x"}]
    assert cursor is None


def test_bare_list_accepted():
    items, cursor = extract_paginated([{"id": "a"}, {"id": "b"}])
    assert items == [{"id": "a"}, {"id": "b"}]
    assert cursor is None


def test_unknown_shape_returns_empty():
    items, cursor = extract_paginated({"some_other_key": "x"})
    assert items == []
    assert cursor is None


def test_canonical_wins_over_legacy_when_both_present():
    """Defensive: if a response somehow carries both, prefer canonical."""
    payload = {
        "data": [{"id": "real"}],
        "pagination": {"cursor": "ok"},
        "items": [{"id": "stale"}],
        "next_cursor": "stale_cur",
    }
    items, cursor = extract_paginated(payload)
    assert items == [{"id": "real"}]
    assert cursor == "ok"


def test_none_payload_returns_empty():
    items, cursor = extract_paginated(None)
    assert items == []
    assert cursor is None


# ── Per-handler regression tests against the canonical shape ──────────────


def _canonical(items, cursor=None):
    """Build a {data, pagination} response like keel-api emits."""
    return {
        "data": items,
        "pagination": {"cursor": cursor, "has_more": cursor is not None},
    }


def test_audit_list_last_reads_canonical_shape():
    """Regression — pre-v0.4.2 the handler returned 0 events against the
    live API because it only looked for `items` / `events`."""
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes import audit as _audit  # noqa: F401

    canonical_audit_response = _canonical([
        {"id": "evt_1", "action": "backtest.create", "decision": "permit",
         "metadata": {}, "created_at": "2026-05-20T10:00:00Z"},
        {"id": "evt_2", "action": "strategy.update", "decision": "permit",
         "metadata": {}, "created_at": "2026-05-20T09:00:00Z"},
    ], cursor="cur_xyz")

    with patch("keel.client.KeelClient.get", return_value=canonical_audit_response):
        tool = OUTCOMES["keel_audit_list_last"]
        result = tool.handler({"n": 5}, ToolContext(is_tty=False))

    env = result.to_envelope()
    assert len(env["events"]) == 2
    assert env["events"][0]["tool"] == "backtest.create"
    assert env["events"][1]["tool"] == "strategy.update"
    assert env["next_cursor"] == "cur_xyz"


def test_accounts_list_reads_canonical_shape():
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes import accounts as _accounts  # noqa: F401

    canonical_accounts = _canonical([
        {"account_id": "acc_a", "wallet_address": "0xaaa", "status": "active",
         "account_mode": "unified"},
        {"account_id": "acc_b", "wallet_address": "0xbbb", "status": "pending",
         "account_mode": "cross"},
    ])

    with patch("keel.client.KeelClient.get", return_value=canonical_accounts):
        tool = OUTCOMES["keel_accounts_list"]
        result = tool.handler({}, ToolContext(is_tty=False))

    env = result.to_envelope()
    assert len(env["accounts"]) == 2
    assert env["accounts"][0]["account_id"] == "acc_a"


def test_strategy_search_reads_canonical_shape():
    """THE highest-impact regression — strategy_search is the discovery
    tool every authed agent calls. Pre-fix it always returned 0 strategies
    regardless of how many the user actually had."""
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes import strategy_search as _ss  # noqa: F401

    canonical_strategies = _canonical([
        {"strategy_id": "str_1", "name": "Carry", "tags": ["momentum"],
         "current_sequence": 3},
        {"strategy_id": "str_2", "name": "Vol",  "tags": ["vol"],
         "current_sequence": 1},
    ])

    with patch("keel.client.KeelClient.get", return_value=canonical_strategies):
        tool = OUTCOMES["keel_strategy_search"]
        result = tool.handler({}, ToolContext(is_tty=False))

    env = result.to_envelope()
    results = env.get("strategies") or env.get("results") or []
    assert len(results) == 2, env
    names = {r.get("name") for r in results}
    assert names == {"Carry", "Vol"}


def test_strategy_memory_read_reads_canonical_shape():
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes import strategy_memory as _sm  # noqa: F401

    canonical_memory = {
        "data": [
            {"note": "Tried period=20, sharpe 1.4"},
            {"note": "period=14 improved to 1.8"},
        ],
        "pagination": {"cursor": None, "has_more": False},
        "last_updated": "2026-05-20T12:00:00Z",
        "summary": "iterating ROC period",
    }

    with patch("keel.client.KeelClient.get", return_value=canonical_memory):
        tool = OUTCOMES["keel_strategy_memory_read"]
        result = tool.handler({"strategy_id": "str_abc"}, ToolContext(is_tty=False))

    env = result.to_envelope()
    assert len(env["notes"]) == 2
    assert env["notes"][0]["note"].startswith("Tried period=20")
    assert env.get("last_updated") == "2026-05-20T12:00:00Z"
    assert env.get("summary") == "iterating ROC period"
