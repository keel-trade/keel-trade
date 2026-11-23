"""Keel MCP connector branding: the server icon, as https URLs only.

The Keel app icon (the purple ``#635BFF`` rounded square with the white sail, rendered from the site/app favicon
``services/keel-site/src/app/icon.svg``) is served by the site at ``/brand/keel-app-icon-{256,512}.png``.
``serverInfo.icons`` names those URLs and never a ``data:`` URI: the MCP SDK stamps ``serverInfo`` on EVERY result's
``_meta["io.modelcontextprotocol/serverInfo"]`` (2026-07-28: "Servers SHOULD stamp it on every result"), so an
embedded PNG rode every tool call, about 22.4 KB of base64 a result (STG-SURFACES #5: ``keel_plan_usage`` was
23.5 KB whole with 0 B structured; a 40-step plan reached 56.8 KB against D-328's 60,000 B cap). The MCPB desktop
bundle ships its own ``scripts/icon.png`` file (``scripts/build_mcpb.py``, ``manifest.template.json``'s ``icon``),
which this module does not carry.

The 256 px mark is declared first: hosts that cap connector icons at 256 px / 10 KB (ChatGPT's form limit) find a
mark inside their bounds.
"""

from __future__ import annotations


KEEL_ICON_MIME = "image/png"
KEEL_WEBSITE_URL = "https://usekeel.io"

#: services/keel-site/public/brand/keel-app-icon-256.png (palette-quantised, ~3.6 KB)
KEEL_ICON_256_URL = "https://usekeel.io/brand/keel-app-icon-256.png"
KEEL_ICON_256_SIZES = ["256x256"]
#: services/keel-site/public/brand/keel-app-icon-512.png
KEEL_ICON_URL = "https://usekeel.io/brand/keel-app-icon-512.png"
KEEL_ICON_SIZES = ["512x512"]
