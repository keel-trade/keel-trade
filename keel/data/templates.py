"""Strategy templates from bundled JSON data."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any


DEFAULT_STRATEGY_DIR = Path.home() / ".keel" / "strategies"


@lru_cache(maxsize=1)
def _load_templates() -> dict[str, dict[str, Any]]:
    """Load templates.json."""
    ref = resources.files("keel.data").joinpath("templates.json")
    return json.loads(ref.read_text())


def list_templates() -> list[str]:
    """Return available template names."""
    return list(_load_templates().keys())


def get_template(name: str) -> dict[str, Any]:
    """Get a template by name.

    Raises:
        KeyError: If template not found.
    """
    templates = _load_templates()
    if name not in templates:
        raise KeyError(f"Template '{name}' not found. Available: {list(templates.keys())}")
    return templates[name]


def create_from_template(
    name: str,
    template: str = "basic",
    strategy_dir: str | None = None,
) -> dict[str, Any]:
    """Create a new strategy file from a template.

    Args:
        name: Strategy name.
        template: Template key (basic, momentum, multi_factor, carry).
        strategy_dir: Directory to write to (default: ~/.keel/strategies/).

    Returns:
        Dict with path and status.
    """
    tmpl = get_template(template)
    content = tmpl["content"].replace("my_strategy", name)

    out_dir = Path(strategy_dir) if strategy_dir else DEFAULT_STRATEGY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.py"

    if out_path.exists():
        return {
            "path": str(out_path),
            "template": template,
            "name": name,
            "status": "exists",
            "error": f"Strategy '{name}' already exists at {out_path}",
        }

    out_path.write_text(content)

    return {
        "path": str(out_path),
        "template": template,
        "name": name,
        "status": "created",
    }
