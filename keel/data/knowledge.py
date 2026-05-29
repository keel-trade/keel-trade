"""System knowledge from bundled markdown files."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources


# Files loaded in order (matches pipeline_engine.reference.system)
_KNOWLEDGE_FILES = [
    "pipeline_system.md",
    "strategy_paths.md",
    "strategy_patterns.md",
    "collaboration.md",
    "mistakes.md",
    "trading_domain.md",
    "composition_mechanics.md",
    "universe_selection.md",
    "reasoning_principles.md",
    "tool_usage.md",
    "dsl_syntax.md",
    "component_versioning.md",
]


@lru_cache(maxsize=1)
def load_system_knowledge() -> str:
    """Load all knowledge sections concatenated."""
    knowledge_dir = resources.files("keel.data").joinpath("knowledge")
    parts = []
    for fname in _KNOWLEDGE_FILES:
        ref = knowledge_dir.joinpath(fname)
        try:
            parts.append(ref.read_text())
        except FileNotFoundError:
            continue
    return "".join(parts)


def load_section(name: str) -> str:
    """Load a single knowledge section by filename (without .md extension)."""
    ref = resources.files("keel.data").joinpath("knowledge", f"{name}.md")
    try:
        return ref.read_text()
    except FileNotFoundError:
        available = [
            f.name.removesuffix(".md")
            for f in resources.files("keel.data").joinpath("knowledge").iterdir()
            if f.name.endswith(".md")
        ]
        raise FileNotFoundError(f"Unknown section '{name}'. Available: {available}")
