"""The SDK release that ships screens is bound in one place (INV-71, spec 06 §D6.6).

`keel.SCREENS_MIN_SDK_VERSION` is the first keel-trade release with the screen
tools. The served screen reference names it ("Screens need keel-trade X or
later"), the CHANGELOG has its section and names the screen tools, and the
package version is at least it. A version bump that forgets the reference, or a
reference that names a version the SDK has not reached, reds here.
"""

from __future__ import annotations

import re
from pathlib import Path

import keel


SDK_ROOT = Path(__file__).resolve().parents[1]
_LINE = re.compile(r"Screens need keel-trade\s+(\d+\.\d+\.\d+)\s+or later\.")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def _served_screener_reference() -> str:
    from keel.data.reference import load_reference

    return load_reference("screener")["content"]


def test_the_screen_reference_names_the_release_screens_arrived_in():
    """# SEED (L13): change the line in libs/pipeline_engine/reference/screener.md
    to "0.9.0 or later" and re-run build_data.py — this reds. Reverse both."""
    found = _LINE.findall(_served_screener_reference())
    assert found == [keel.SCREENS_MIN_SDK_VERSION], found


def test_the_package_has_reached_it_and_the_changelog_says_so():
    pyproject = (SDK_ROOT / "pyproject.toml").read_text()
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert version == keel.__version__
    assert _version(version) >= _version(keel.SCREENS_MIN_SDK_VERSION)
    changelog = (SDK_ROOT / "CHANGELOG.md").read_text()
    section = changelog.split(f"## [{keel.SCREENS_MIN_SDK_VERSION}]", 1)
    assert len(section) == 2, "the CHANGELOG has no section for the screens release"
    body = section[1].split("\n## ", 1)[0]
    for name in ("keel_screener_run", "keel_screener_view", "keel_screener_view_set"):
        assert name in body, name
