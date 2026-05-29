"""Parity tests — both backends of `keel.tools.local` must agree on shape.

The SDK has two implementations sitting behind the same `keel.tools.local`
surface:

1. **Rich** — `pipeline_engine.mcp.tools.*` (in-cluster, dev env with
   `PYTHONPATH=libs`). The same code chat-api imports.
2. **Bundled** — `keel.data.registry.*` reading the bundled JSON snapshot.
   Ships with the pipx-installed wheel.

`keel.tools.local._delegate_or_fallback()` picks rich when available
else bundled. To prevent the two implementations from drifting in
ways that break agents, these tests:

  * Call BOTH backends with the SAME canonical inputs.
  * Assert top-level keys / list-element keys match.
  * For lists, assert length is non-zero on both sides for a query we
    know both should answer (no exact ordering assertion — bundled
    and rich score differently by design).

If `pipeline_engine.mcp` isn't importable, the rich-path checks are
skipped — same env contract as the pipx-installed wheel.
"""

from __future__ import annotations

import importlib

import pytest


# ─── capability gates ──────────────────────────────────────────────────


def _rich_available():
    try:
        importlib.import_module("pipeline_engine.mcp.tools")
        return True
    except ImportError:
        return False


rich_only = pytest.mark.skipif(
    not _rich_available(),
    reason="pipeline_engine.mcp not available (pipx wheel env); rich-path parity not testable",
)


# ─── helpers ───────────────────────────────────────────────────────────


def _call_both(fn_name: str, **kwargs):
    """Invoke both backends; return (rich_result, bundled_result)."""
    import inspect

    rich_mod = importlib.import_module("pipeline_engine.mcp.tools")
    rich_fn = getattr(rich_mod, fn_name)
    sig_r = inspect.signature(rich_fn)
    rich_kwargs = {k: v for k, v in kwargs.items() if k in sig_r.parameters}
    rich_result = rich_fn(**rich_kwargs)

    # Bundled — use the same function names exposed by keel.data.registry.
    from keel.data import registry as _bundled

    bundled_dispatch = {
        "strategy_components_search": _bundled.search_components,
        "strategy_component_detail": _bundled.get_component_detail,
        "strategy_components_after": _bundled.get_components_after,
        "strategy_components_before": _bundled.get_components_before,
        "strategy_components_dump": _bundled.get_components_dump,
    }
    _bundled._ensure_loaded()
    bundled_fn = bundled_dispatch[fn_name]
    sig_b = inspect.signature(bundled_fn)
    bundled_kwargs = {k: v for k, v in kwargs.items() if k in sig_b.parameters}
    bundled_result = bundled_fn(**bundled_kwargs)
    return rich_result, bundled_result


def _entry_keys(entries):
    """Return the union of keys across a list of dict entries."""
    out = set()
    for e in entries:
        if isinstance(e, dict):
            out.update(e.keys())
    return out


# ─── parity assertions ────────────────────────────────────────────────


@rich_only
def test_parity_components_search_with_query():
    """`query=momentum`: both must return non-empty + same per-entry key set."""
    rich, bundled = _call_both("strategy_components_search", query="momentum", top_k=10)
    assert isinstance(rich, list) and isinstance(bundled, list)
    assert len(rich) > 0, "rich path returned 0 — registry hydration issue"
    assert len(bundled) > 0, "bundled path returned 0 — registry hydration issue"
    common_keys = _entry_keys(rich) & _entry_keys(bundled)
    # Both should at least carry these contract keys.
    must_have = {"name", "category", "description"}
    assert must_have.issubset(common_keys), (
        f"Missing contract keys; rich={_entry_keys(rich)}, bundled={_entry_keys(bundled)}"
    )


@rich_only
def test_parity_components_search_with_category_filter():
    rich, bundled = _call_both("strategy_components_search", category="indicator", top_k=5)
    assert isinstance(rich, list) and isinstance(bundled, list)
    # Both must filter to category='indicator' (case-insensitive).
    for r in rich:
        assert (r.get("category", "")).lower() == "indicator"
    for b in bundled:
        assert (b.get("category", "")).lower() == "indicator"


@rich_only
def test_parity_component_detail_for_known_name():
    rich, bundled = _call_both("strategy_component_detail", name="AD")
    assert isinstance(rich, dict) and isinstance(bundled, dict)
    # Both must surface these documented contract fields.
    contract_keys = {"name", "category", "input_type", "output_type", "parameters"}
    assert contract_keys.issubset(set(rich.keys())), f"rich missing keys: {contract_keys - set(rich.keys())}"
    assert contract_keys.issubset(set(bundled.keys())), f"bundled missing keys: {contract_keys - set(bundled.keys())}"
    assert rich["name"] == "AD"
    assert bundled["name"] == "AD"
    assert rich["category"] == bundled["category"]


@rich_only
def test_parity_components_after_returns_compatible_set():
    rich, bundled = _call_both("strategy_components_after", name="AD")
    assert isinstance(rich, list) and isinstance(bundled, list)
    rich_names = {r["name"] for r in rich if isinstance(r, dict)}
    bundled_names = {b["name"] for b in bundled if isinstance(b, dict)}
    # Both must exclude the seed component itself.
    assert "AD" not in rich_names
    assert "AD" not in bundled_names
    # Should heavily overlap — both walk the same type graph. Don't
    # require strict equality (bundled may have a stale registry
    # snapshot if a new component shipped without regen), but enforce
    # ≥80% jaccard similarity so silent drift is caught.
    intersection = len(rich_names & bundled_names)
    union = len(rich_names | bundled_names)
    similarity = intersection / union if union else 1.0
    assert similarity >= 0.8, (
        f"rich vs bundled components_after('AD') diverged "
        f"(similarity={similarity:.2f}); regenerate keel/data/registry.json"
    )


@rich_only
def test_parity_components_dump_total_count_close():
    rich, bundled = _call_both("strategy_components_dump")
    # Should be within 5% — drift suggests the bundled JSON snapshot is stale.
    delta = abs(len(rich) - len(bundled))
    threshold = max(5, int(0.05 * max(len(rich), len(bundled))))
    assert delta <= threshold, (
        f"rich dump = {len(rich)}, bundled dump = {len(bundled)}; "
        f"delta {delta} exceeds threshold {threshold}. "
        f"Regenerate keel/data/registry.json via build_data.py."
    )


# ─── plumbing: capability detection ────────────────────────────────────


def test_delegate_helper_returns_rich_when_available():
    """When pipeline_engine.mcp is importable, _delegate_or_fallback uses it."""
    from keel.tools.local import _delegate_or_fallback, _rich_module

    if _rich_module() is None:
        pytest.skip("pipeline_engine.mcp not available in this env")
    # Use a sentinel fallback to verify it was NOT called.
    sentinel_calls = []

    def _sentinel_fallback(**kwargs):
        sentinel_calls.append(kwargs)
        return "FALLBACK_RAN"

    result = _delegate_or_fallback("strategy_components_dump", _sentinel_fallback)
    assert sentinel_calls == [], "Fallback was called even though rich path is available"
    assert isinstance(result, list)
    assert len(result) > 0


def test_delegate_helper_uses_fallback_when_function_missing():
    """When the requested name isn't on pipeline_engine.mcp.tools, fallback runs."""
    from keel.tools.local import _delegate_or_fallback

    def _fallback(**kwargs):
        return {"fell_back": True, "kwargs": kwargs}

    result = _delegate_or_fallback(
        "this_function_definitely_does_not_exist", _fallback, foo="bar"
    )
    assert result == {"fell_back": True, "kwargs": {"foo": "bar"}}


def test_delegate_helper_filters_unknown_kwargs():
    """Stray kwargs that the target doesn't accept must be dropped, not raise."""
    from keel.tools.local import _delegate_or_fallback

    def _fallback_with_strict_sig(query=None, top_k=10):
        return {"query": query, "top_k": top_k}

    # Pass extra kwarg that fallback doesn't take.
    result = _delegate_or_fallback(
        "nonexistent_rich_fn",
        _fallback_with_strict_sig,
        query="momentum",
        top_k=5,
        unknown_extra="should_be_dropped",
    )
    assert result == {"query": "momentum", "top_k": 5}
