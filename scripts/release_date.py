"""The release date of the keel-trade version this checkout documents.

The generated docs pages (``content/docs/sdk/tool-reference.mdx`` and
``content/docs/agents/setup.mdx`` in keel-site) are stamped with it as their
``updated`` frontmatter, so their date is the release they describe rather
than the day someone last regenerated them (seo-growth-engine research/11
#11). The version is ``pyproject.toml``'s; its date is the matching
``## [x.y.z] — YYYY-MM-DD`` heading in ``CHANGELOG.md``. A version with no
dated heading is an error, never a guess.
"""

from __future__ import annotations

import re
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parent.parent


def keel_trade_version(sdk_root: Path = SDK_ROOT) -> str:
    text = (sdk_root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"$', text, re.M)
    if not m:
        raise RuntimeError(f"{sdk_root / 'pyproject.toml'}: no version line")
    return m.group(1)


def keel_trade_release_date(sdk_root: Path = SDK_ROOT) -> str:
    version = keel_trade_version(sdk_root)
    changelog = (sdk_root / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(version)}\] [—-] (\d{{4}}-\d{{2}}-\d{{2}})", changelog, re.M)
    if not m:
        raise RuntimeError(
            f"CHANGELOG.md has no dated '## [{version}]' heading; date the release "
            "before regenerating the docs"
        )
    return m.group(1)
