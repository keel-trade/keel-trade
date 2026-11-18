"""A deploy route's `quota` (doc 07 §5, D-14): keel-api sends ONE object when
the deploy took the last slot (paper create/restart, live deploy) and null
otherwise. The SDK renders the last-slot notice from it and projects it like
every quota block — names of the plans that run more, never their limits."""

from __future__ import annotations

import json

from keel.tools.outcomes.backtest_run import served_quota_fields

from . import _plan_catalog as cat


_LAST_SLOT = {
    "unit": "paper_strategies_max",
    "label": "paper strategies",
    "limit": 1,
    "used": 1,
    "remaining": 0,
    "unlimited": False,
    "tier": "exhausted",
    "plan": "free",
    "plan_name": "Free",
    "higher_plans": [
        {**h, "limit": int(cat._value(h["plan"], h["version"], "paper_strategies_max"))}
        for h in cat.higher_plans("free", "paper_strategies_max")
    ],
}


def test_the_last_slot_object_renders_the_notice_and_names_only():
    fields = served_quota_fields(_LAST_SLOT)
    more = cat.names(cat.higher_plans("free", "paper_strategies_max"))
    assert fields["quota_notice"] == (
        f"Last paper slot in use (1 of 1). {' and '.join(more)} run more."
    )
    assert fields["quota"] == [
        {"unit": "paper_strategies_max", "limit": 1, "used": 1, "remaining": 0, "more_plans": more}
    ]
    blob = json.dumps(fields)
    assert "higher_plans" not in blob and '"limit": 5' not in blob


def test_null_and_absent_quota_add_nothing():
    """Control: keel-api's null (no last slot) and an older server's absence."""
    assert served_quota_fields(None) == {}
    assert served_quota_fields([]) == {}
    assert served_quota_fields([_LAST_SLOT]) == served_quota_fields(_LAST_SLOT)


def test_live_deploy_relays_no_raw_quota_block():
    """keel_live_deploy used to relay the route's body whole as `deployment`;
    its `quota` now leaves it and rides projected beside it."""
    import inspect

    from keel.tools.outcomes import live_deploy

    src = inspect.getsource(live_deploy)
    assert 'if k != "quota"' in src and 'served_quota_fields(result.get("quota"))' in src
