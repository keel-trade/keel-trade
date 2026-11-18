"""Keel Agent SDK — CLI and MCP server for AI-driven strategy development."""

# Kept equal to pyproject.toml's [project].version by
# tests/test_version.py — the fallback identity for contexts where the
# wheel metadata is absent (.mcpb bundle, source checkouts).
__version__ = "0.10.0"

# The first keel-trade release that carries screens (INV-71, spec 06 §D6.6):
# older wheels vendor a parser that refuses `Outputs`, so the screen reference
# states this version, and tests/test_screens_sdk_version.py binds the line,
# the CHANGELOG section and pyproject.toml to it. It never moves with later
# releases: it is the release screens arrived in.
SCREENS_MIN_SDK_VERSION = "0.10.0"
