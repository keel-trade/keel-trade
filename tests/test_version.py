"""The fallback version constant must track pyproject.toml.

`keel.__version__` is the serverInfo/CLI version identity whenever the
wheel metadata is absent — the unpacked .mcpb bundle and source
checkouts. It sat at a stale 0.1.0 for a year because nothing checked
it; the .mcpb then reported 0.0.0 to every connector. This pins the
two to each other so a release bump cannot ship without it.
"""

import tomllib
from pathlib import Path

import keel


def test_dunder_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as f:
        declared = tomllib.load(f)["project"]["version"]
    assert keel.__version__ == declared, (
        f"keel.__version__ ({keel.__version__}) != pyproject.toml "
        f"version ({declared}) — bump keel/__init__.py with the release"
    )
