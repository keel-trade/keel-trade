"""serverInfo.icons: the Keel mark as https URLs, 256 px first, and never a ``data:`` URI.

ChatGPT's connector form caps icons at 256 px and 10 KB; hosts that honour ``serverInfo.icons`` find a mark inside
those bounds, declared before the 512 px one. Each URL names a PNG the site serves (``services/keel-site/public``).

STG-SURFACES #5 (every MCP tool, not research only): the MCP SDK stamps ``serverInfo`` on every result's
``_meta["io.modelcontextprotocol/serverInfo"]``, so the two embedded PNGs rode every tool call (22.4 KB of a 23.5 KB
``keel_plan_usage`` answer; a 40-step plan at 56.8 KB against D-328's 60,000 B cap). The bound below holds a core
tool's whole ``_meta`` small. SEED (reversed by reversing the edit): a ``data:`` icon back in ``create_server``'s
``icons`` -> both the URL-only arm and the ``_meta`` bound red. Control: the MCPB bundle's own icon file arm is green
under the seed (it never read the embedded copy). Non-vacuity: the stamp is present, so the bound measures a real
serverInfo.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import struct


REPO = pathlib.Path(__file__).resolve().parents[4]
SITE = REPO / "services/keel-site/public"
SDK = pathlib.Path(__file__).resolve().parents[1]
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
#: A core tool result's whole ``_meta``: the SDK's stamp (name, version, website, two icon URLs) and FastMCP's own
#: block, with room to spare; an embedded PNG is ~22,000 B.
META_BOUND = 2_000


def _png_size(raw: bytes) -> tuple[int, int]:
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", raw[16:24])


def test_the_server_declares_url_icons_only_256_first() -> None:
    from keel.mcp.server import create_server

    icons = create_server()._mcp_server.icons  # type: ignore[attr-defined]
    sizes = [tuple(i.sizes or []) for i in icons]
    assert len(icons) == 2, sizes  # non-vacuous: the 256 and the 512 mark
    assert sizes == [("256x256",), ("512x512",)]
    assert all(str(i.src).startswith("https://usekeel.io/brand/") for i in icons), [i.src for i in icons]
    assert not any(str(i.src).startswith("data:") for i in icons)


def test_each_icon_url_names_a_png_the_site_serves() -> None:
    from keel.mcp import _branding

    for url, size in ((_branding.KEEL_ICON_256_URL, 256), (_branding.KEEL_ICON_URL, 512)):
        path = SITE / url.removeprefix("https://usekeel.io/")
        assert path.exists(), path
        assert _png_size(path.read_bytes()) == (size, size)
    assert len((SITE / "brand/keel-app-icon-256.png").read_bytes()) <= 10 * 1024


def test_the_mcpb_bundle_still_ships_its_own_icon_file() -> None:
    """Control: the desktop bundle copies ``scripts/icon.png`` and names it in its manifest; the server's icons
    never fed it."""
    icon = SDK / "scripts" / "icon.png"
    assert icon.exists() and _png_size(icon.read_bytes())[0] >= 256
    manifest = json.loads((SDK / "scripts" / "manifest.template.json").read_text())
    assert manifest["icon"] == "icon.png"
    assert 'ICON_SRC = SCRIPTS_DIR / "icon.png"' in (SDK / "scripts" / "build_mcpb.py").read_text()


def test_a_core_tool_results_meta_stays_small(monkeypatch) -> None:
    """Every tool result carries the serverInfo stamp; a core tool's whole ``_meta`` stays under ``META_BOUND``."""
    from fastmcp import Client

    from keel.mcp.server import create_server

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")

    async def meta_of(tool: str) -> dict:
        async with Client(create_server()) as client:
            result = await client.call_tool_mcp(tool, {})
            return dict(result.meta or {})

    meta = asyncio.run(meta_of("keel_plan_usage"))
    assert SERVER_INFO_META_KEY in meta  # non-vacuity: the stamp is there to measure
    size = len(json.dumps(meta, separators=(",", ":")))
    assert size < META_BOUND, size
