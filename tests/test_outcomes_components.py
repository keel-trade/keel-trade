"""Smoke tests for the components outcome tools.

Per Phase 2A `components_search` + `components_compose_help` consolidate
six legacy primitives into two outcome tools. These tests run against
the bundled registry — no API or mocking required.
"""

from __future__ import annotations

import pytest

from keel.errors import NotFoundError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx() -> ToolContext:
    """Build a ToolContext with no API client — handlers will fall back
    to bundled because `_search_via_api` swallows client errors."""
    return ToolContext(api_client=None)


# ─── components_search ───────────────────────────────────────────────────


def test_components_search_with_keyword_returns_results():
    tool = OUTCOMES["keel_components_search"]
    out = tool.handler({"keyword": "momentum"}, _ctx())
    env = out.to_envelope()
    assert env["share_url"] is None
    assert env["hero_url"].endswith("/components")
    assert env["resource_uri"] == "keel://components/catalog"
    results = env["results"]
    assert isinstance(results, list)
    assert len(results) > 0
    # At least one result should mention momentum in name or description.
    assert any(
        "momentum" in (r.get("name", "") + " " + r.get("description", "")).lower()
        for r in results
    )
    assert env["total"] == len(results)
    assert env["limit"] == 20


def test_components_search_with_category_filter():
    tool = OUTCOMES["keel_components_search"]
    out = tool.handler({"category": "indicator", "limit": 5}, _ctx())
    env = out.to_envelope()
    results = env["results"]
    assert len(results) > 0
    assert len(results) <= 5
    for r in results:
        assert r["category"] == "indicator"


def test_components_search_falls_back_to_bundled_when_api_lacks_filter(monkeypatch):
    """Regression — v0.4.2 live smoke caught the search returning all 182
    components for ANY query. Root cause: `/v1/components` API endpoint
    only honors `category`; for `query` / `keyword` / `input_type` /
    `output_type` / `after` / `before` the API silently returned the
    full catalog. The handler trusted that, never filtered locally.

    Now: when ANY of those filters is requested, the API path is
    skipped (returns None) and bundled search — which implements every
    filter correctly — runs instead.
    """
    from keel.tools.outcomes._base import ToolContext
    from unittest.mock import MagicMock

    tool = OUTCOMES["keel_components_search"]
    fake_client = MagicMock()
    # Simulate the API returning ALL 182 (no filter support); if our
    # guard works, this should never be called.
    fake_client.get.return_value = [{"name": f"FakeComp{i}", "description": "n/a",
                                      "category": "cat", "input_type": "Any",
                                      "output_type": "Any"} for i in range(182)]
    ctx = ToolContext(api_client=fake_client, is_tty=False)

    out = tool.handler({"query": "momentum"}, ctx)
    env = out.to_envelope()
    # Must NOT have hit the API for an unsupported-filter query.
    fake_client.get.assert_not_called()
    # Must have run bundled search and gotten real momentum components,
    # not the 182 fake ones.
    assert all(not r["name"].startswith("FakeComp") for r in env["results"])
    assert len(env["results"]) > 0


def test_components_search_uses_api_when_only_category(monkeypatch):
    """The category-only path IS supported by the API — verify the handler
    uses it (avoids the bundled search round-trip when not needed)."""
    from keel.tools.outcomes._base import ToolContext
    from unittest.mock import MagicMock

    tool = OUTCOMES["keel_components_search"]
    fake_client = MagicMock()
    # Match the API's PaginatedResponse-less shape for the components
    # endpoint (list_components returns a bare list, not paginated).
    fake_client.get.return_value = [
        {"name": "ROC", "description": "rate of change", "category": "indicator",
         "input_type": "OHLCVDict", "output_type": "SignalSeries"},
    ]
    ctx = ToolContext(api_client=fake_client, is_tty=False)

    out = tool.handler({"category": "indicator", "limit": 5}, ctx)
    env = out.to_envelope()
    fake_client.get.assert_called_once()
    # Used API, got the fake component.
    assert env["results"][0]["name"] == "ROC"


def test_components_search_with_after_filter():
    """`after=AD` returns components whose input type accepts AD's output.

    AD outputs SignalSeries (per the bundled registry), so the candidate
    pool is non-empty and excludes AD itself.
    """
    tool = OUTCOMES["keel_components_search"]
    out = tool.handler({"after": "AD", "limit": 10}, _ctx())
    env = out.to_envelope()
    results = env["results"]
    assert len(results) > 0
    names = {r["name"] for r in results}
    assert "AD" not in names


# ─── components_compose_help ─────────────────────────────────────────────


def test_components_compose_help_returns_schema():
    tool = OUTCOMES["keel_components_compose_help"]
    out = tool.handler({"name": "AD"}, _ctx())
    env = out.to_envelope()
    assert env["share_url"] is None
    assert env["hero_url"].endswith("/components/AD")
    assert env["resource_uri"] == "keel://components/AD/schema"
    assert env["name"] == "AD"
    assert env["category"] == "indicator"
    assert env["input_type"] == "OHLCVDict"
    assert env["output_type"] == "SignalSeries"
    assert isinstance(env["parameters"], list)
    # AD has an "Example:" block in its description; pull that through.
    assert isinstance(env["examples"], list)


def test_components_compose_help_description_explains_component_detail_role():
    desc = OUTCOMES["keel_components_compose_help"].description
    assert "schema/detail contract for ONE known" in desc
    assert "keel_components_detail_batch" in desc
    assert "ComponentRef" in desc
    assert "Do NOT use to discover components" in desc


def test_components_compose_help_unknown_name_raises_NotFoundError():
    tool = OUTCOMES["keel_components_compose_help"]
    with pytest.raises(NotFoundError):
        tool.handler({"name": "DefinitelyNotAComponent_XYZ"}, _ctx())
