"""DSL reference documentation from bundled markdown files."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from keel.data._bundled import MARKDOWN_FILES, read_bundled
from keel.errors import NotFoundError


TOPICS = (
    "phases",
    "types",
    "slots",
    "composition",
    "normalization",
    "best_practices",
    "screener",
    "view_language",
)

_DESCRIPTIONS = {
    "screener": "Screeners — stored outputs, evidence, reuse and automation",
    "view_language": "View language — tables, charts and presentation-only expressions",
    "phases": "Pipeline phase ordering — 14 categories in 6 groups",
    "types": "Type flow system — pipeline data types and transitions",
    "slots": "Slot system — Store, Load, StoreValue semantics",
    "composition": "Composition patterns — parallel, factories, variables, nesting",
    "normalization": "Signal normalization — when and how to normalize signals",
    "best_practices": "Strategy quality — overfitting, signal quality, sizing, common mistakes",
}


@lru_cache(maxsize=8)
def _load_reference_file(name: str) -> str:
    """Load a single reference markdown file (a key into the bundled
    listing, never a path — Q-3352)."""
    return read_bundled(
        "keel.data", "reference", name, layout=MARKDOWN_FILES, kind="DSL reference topic"
    )


def load_reference(topic: str | None = None) -> dict[str, Any]:
    """Load reference content for a topic or table of contents.

    Args:
        topic: Specific topic name, or None for listing.

    Returns:
        Dict with topic name(s) and content.
    """
    from keel.tools.outcomes._toolsets import screen_topic_withheld

    topics = [t for t in TOPICS if not screen_topic_withheld(t)]
    if topic is None:
        return {
            "topics": topics,
            "descriptions": {t: _DESCRIPTIONS[t] for t in topics},
        }

    if topic not in topics:
        # The public topic list, never the input (Q-3352): this text reaches
        # an MCP client through the `keel://dsl/reference/{topic}` resource.
        raise NotFoundError(
            f"Unknown DSL reference topic. Available: {', '.join(topics)}.",
            suggestion="Ask for one of the listed topics by its exact name.",
        )

    return {
        "topic": topic,
        "content": _load_reference_file(topic),
    }
