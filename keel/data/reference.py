"""DSL reference documentation from bundled markdown files."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any


TOPICS = ("phases", "types", "slots", "composition", "normalization", "best_practices")

_DESCRIPTIONS = {
    "phases": "Pipeline phase ordering — 14 categories in 6 groups",
    "types": "Type flow system — pipeline data types and transitions",
    "slots": "Slot system — Store, Load, StoreValue semantics",
    "composition": "Composition patterns — parallel, factories, variables, nesting",
    "normalization": "Signal normalization — when and how to normalize signals",
    "best_practices": "Strategy quality — overfitting, signal quality, sizing, common mistakes",
}


@lru_cache(maxsize=8)
def _load_reference_file(name: str) -> str:
    """Load a single reference markdown file."""
    ref = resources.files("keel.data").joinpath("reference", f"{name}.md")
    return ref.read_text()


def load_reference(topic: str | None = None) -> dict[str, Any]:
    """Load reference content for a topic or table of contents.

    Args:
        topic: Specific topic name, or None for listing.

    Returns:
        Dict with topic name(s) and content.
    """
    if topic is None:
        return {
            "topics": list(TOPICS),
            "descriptions": _DESCRIPTIONS,
        }

    if topic not in TOPICS:
        raise ValueError(f"Unknown topic '{topic}'. Available: {list(TOPICS)}")

    return {
        "topic": topic,
        "content": _load_reference_file(topic),
    }
