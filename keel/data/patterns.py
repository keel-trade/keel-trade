"""Composition pattern search from bundled markdown files."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Any


@lru_cache(maxsize=1)
def _load_patterns() -> dict[str, dict[str, Any]]:
    """Load and parse all pattern files."""
    patterns: dict[str, dict[str, Any]] = {}
    pattern_dir = resources.files("keel.data").joinpath("patterns")

    for item in sorted(pattern_dir.iterdir()):
        if not item.name.endswith(".md"):
            continue
        text = item.read_text()

        # Extract pattern name from comment
        name_match = re.search(r"<!--\s*pattern:\s*(\S+)\s*-->", text)
        if not name_match:
            continue
        name = name_match.group(1)

        # Extract keywords
        kw_match = re.search(r"<!--\s*keywords:\s*(.+?)\s*-->", text)
        keywords = []
        if kw_match:
            keywords = [k.strip().lower() for k in kw_match.group(1).split(",")]

        # Extract title
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else name

        content = re.sub(r"<!--.*?-->\n?", "", text).strip()

        patterns[name] = {
            "name": name,
            "title": title,
            "keywords": keywords,
            "content": content,
        }

    return patterns


def search_patterns(query: str, max_results: int = 2) -> list[dict[str, Any]]:
    """Search patterns by keyword overlap scoring."""
    patterns = _load_patterns()
    query_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))

    scored: list[tuple[float, dict[str, Any]]] = []
    for pattern in patterns.values():
        score = 0.0
        for kw in pattern["keywords"]:
            kw_words = set(kw.split())
            if kw_words & query_tokens:
                score += 1.0
            elif any(qt in kw or kw in qt for qt in query_tokens for kw in kw_words):
                score += 0.5

        title_words = set(re.findall(r"[a-z0-9]+", pattern["title"].lower()))
        score += len(title_words & query_tokens) * 0.3

        if score > 0:
            scored.append((score, pattern))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:max_results]]


def list_patterns() -> list[dict[str, Any]]:
    """Return metadata for all patterns."""
    patterns = _load_patterns()
    return [
        {"name": p["name"], "title": p["title"], "keywords": p["keywords"]}
        for p in sorted(patterns.values(), key=lambda p: p["name"])
    ]
