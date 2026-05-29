"""Strategy examples from bundled JSON data."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    """Load examples.json (raw dict with 'examples', 'count', 'showing')."""
    ref = resources.files("keel.data").joinpath("examples.json")
    return json.loads(ref.read_text())


def _load_examples() -> list[dict[str, Any]]:
    """Return the examples list from the bundled JSON."""
    raw = _load_raw()
    if isinstance(raw, dict) and "examples" in raw:
        return raw["examples"]
    # Fallback: raw is already a list
    return raw if isinstance(raw, list) else []


def strategy_examples(
    *,
    query: str | None = None,
    complexity: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Search or filter strategy examples.

    Args:
        query: Natural language search query.
        complexity: Filter by complexity level (simple, medium, complex).
        name: Get a specific example by name.

    Returns:
        Dict with 'examples' list, 'count' (total), and 'showing' (returned).
    """
    all_examples = _load_examples()
    total = len(all_examples)
    examples = list(all_examples)

    if name:
        for ex in examples:
            if ex.get("name") == name:
                return {"examples": [ex], "count": total, "showing": 1}
        return {"examples": [], "count": total, "showing": 0}

    if complexity:
        examples = [e for e in examples if e.get("complexity") == complexity]

    if query:
        query_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for ex in examples:
            score = 0.0
            name_tokens = set(re.findall(r"[a-z0-9]+", ex.get("name", "").lower()))
            desc_tokens = set(re.findall(r"[a-z0-9]+", ex.get("description", "").lower()))
            score += len(query_tokens & name_tokens) * 3.0
            score += len(query_tokens & desc_tokens) * 1.0
            if score > 0:
                scored.append((score, ex))
        scored.sort(key=lambda x: x[0], reverse=True)
        examples = [e for _, e in scored]

    return {"examples": examples, "count": total, "showing": len(examples)}
