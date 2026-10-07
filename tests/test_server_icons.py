"""serverInfo.icons carries a 256 px Keel mark within host connector limits.

ChatGPT's connector form caps icons at 256 px and 10 KB; hosts that honour
``serverInfo.icons`` should find a mark inside those bounds, declared before the
512 px one. The embedded 256 px copy must decode to exactly the PNG shipped on
the site, so the https and data: sources are the same image.
"""

from __future__ import annotations

import base64
import pathlib
import struct

from keel.mcp import _branding


REPO = pathlib.Path(__file__).resolve().parents[4]
SITE_PNG = REPO / "services/keel-site/public/brand/keel-app-icon-256.png"


def _png_size(raw: bytes) -> tuple[int, int]:
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", raw[16:24])


def _embedded_256() -> bytes:
    prefix = "data:image/png;base64,"
    assert _branding.KEEL_ICON_256_DATA_URI.startswith(prefix)
    return base64.b64decode(_branding.KEEL_ICON_256_DATA_URI[len(prefix) :])


def test_embedded_256_icon_is_within_host_limits() -> None:
    raw = _embedded_256()
    assert _png_size(raw) == (256, 256)
    assert len(raw) <= 10 * 1024, len(raw)
    assert _branding.KEEL_ICON_256_SIZES == ["256x256"]


def test_embedded_256_icon_is_the_site_asset() -> None:
    assert SITE_PNG.exists()
    assert _embedded_256() == SITE_PNG.read_bytes()
    assert _branding.KEEL_ICON_256_URL.endswith("/brand/keel-app-icon-256.png")


def test_server_declares_256_first_then_512() -> None:
    from keel.mcp.server import create_server

    icons = create_server()._mcp_server.icons  # type: ignore[attr-defined]
    sizes = [tuple(i.sizes or []) for i in icons]
    # Non-vacuous: three icons, the 256 mark ahead of the 512 one.
    assert len(icons) == 3, sizes
    assert sizes[0] == ("256x256",) and sizes[-1] == ("512x512",)
    assert str(icons[0].src).startswith("https://")
    assert str(icons[1].src).startswith("data:image/png;base64,")
