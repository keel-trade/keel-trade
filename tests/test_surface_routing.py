"""CI drift gate for the canonical surface-routing table (spec 07 R7).

The table's single source of truth is ``shared/surface-routing.json``;
every rendered copy (site pages, AGENTS.md files, SDK README) must
match. See scripts/check_surface_routing.py for the full contract.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


CHECKER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_surface_routing.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_surface_routing", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_surface_routing_renders_match_canonical():
    checker = _load_checker()
    assert checker.check_surface_routing() == []


def test_canonical_table_matches_spec_07_rows():
    """The canonical table must stay the spec-07 R7 table verbatim.

    Guard against 'helpful' edits to the canonical source silently
    changing the reviewed routing story on every surface at once.
    """
    checker = _load_checker()
    data = checker.load_canonical()
    assert data["columns"] == ["You are…", "Default path (shown first)", "Also works"]
    rows = [(r["audience"], r["default_path"], r["also_works"]) for r in data["rows"]]
    # ONE hosted endpoint model (D1 2026-07-19): mcp.usekeel.io serves one
    # 23-tool surface, added via paste-URL now (directory one-click coming);
    # going live is a Keel web-app handoff on every surface, reads stay
    # everywhere. There is no separate "full"/"listed" split any more.
    assert rows == [
        (
            "Using Claude/ChatGPT on web or phone",
            "Hosted endpoint — paste the URL (directory one-click coming)",
            "CLI + local MCP",
        ),
        (
            "Working in Claude Code / Cursor / terminal",
            "`pipx install keel-trade` (CLI + local MCP)",
            "hosted endpoint",
        ),
        (
            "Going live with a strategy",
            "Keel web app (connect account, review sizing, go live)",
            "reads on every surface",
        ),
        ("Building your own agent/scripts", "SDK + API key", "CLI"),
        ("Just browsing/running strategies", "Web app + library", "hosted endpoint"),
    ]
