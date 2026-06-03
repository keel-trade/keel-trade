#!/usr/bin/env python3
"""Build a .mcpb bundle for keel-trade.

Bundle is a SINGLE cross-platform .mcpb. We ship only pure-Python
keel + pipeline_engine + a bootstrap entry point; runtime deps
(pydantic_core, cryptography, fastmcp, etc.) are pip-installed on
first launch into ~/.keel/mcpb-lib/py3.X/. This sidesteps the
Python-version ABI mismatch that bit v0.5.2 — its bundled
pydantic_core.cpython-311-darwin.so couldn't load under the
Python 3.12 that Claude Desktop launched.

First-launch cost: ~10-30 sec to pip install on the user's machine.
Subsequent launches reuse the cache and start instantly.

Layout produced:

    staging/
    ├── manifest.json
    ├── icon.png
    └── server/
        ├── _bootstrap.py       # entry point — installs deps, runs keel
        ├── keel/               # pure-Python copy of ../keel
        └── pipeline_engine/    # pure-Python copy of ../pipeline_engine

Usage (from the keel-sdk directory):

    python scripts/build_mcpb.py

Requirements:
    Python 3.11+
    Node 20+ (for npx-installed @anthropic-ai/mcpb CLI)

Spec: projects/strategyquant-wedge/23-mcpb-bundle-spec.md
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SDK_ROOT / "scripts"
DIST_DIR = SDK_ROOT / "dist"
STAGING_DIR = DIST_DIR / "mcpb-staging"
SERVER_DIR = STAGING_DIR / "server"

MANIFEST_TEMPLATE = SCRIPTS_DIR / "manifest.template.json"
ICON_SRC = SCRIPTS_DIR / "icon.png"
BOOTSTRAP_SRC = SCRIPTS_DIR / "mcpb_bootstrap.py"

PYPROJECT = SDK_ROOT / "pyproject.toml"


def get_pyproject() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def get_version(pyproject: dict) -> str:
    return pyproject["project"]["version"]


def clean_staging() -> None:
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True)
    SERVER_DIR.mkdir()


def copy_sources() -> None:
    for pkg in ("keel", "pipeline_engine"):
        src = SDK_ROOT / pkg
        dst = SERVER_DIR / pkg
        if not src.exists():
            raise RuntimeError(f"Source package missing: {src}")
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.pyo"),
        )


def copy_bootstrap() -> None:
    if not BOOTSTRAP_SRC.exists():
        raise RuntimeError(f"Bootstrap script missing at {BOOTSTRAP_SRC}")
    shutil.copy2(BOOTSTRAP_SRC, SERVER_DIR / "_bootstrap.py")


def copy_icon() -> None:
    if not ICON_SRC.exists():
        raise RuntimeError(
            f"Icon missing at {ICON_SRC}. "
            "Restore it from the brand assets: "
            "cp services/keel-site/public/brand/keel-mark-256.png "
            "packages/keel-trade/keel-sdk/scripts/icon.png"
        )
    shutil.copy2(ICON_SRC, STAGING_DIR / "icon.png")


def render_manifest(version: str) -> None:
    template = MANIFEST_TEMPLATE.read_text()
    rendered = template.replace("{{VERSION}}", version)
    parsed = json.loads(rendered)
    if parsed["version"] != version:
        raise RuntimeError("Version substitution failed")
    if not re.match(r"^\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Refusing non-semver version: {version!r}")
    (STAGING_DIR / "manifest.json").write_text(json.dumps(parsed, indent=2) + "\n")


def run_mcpb(args: list[str]) -> subprocess.CompletedProcess:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx is None:
        raise RuntimeError(
            "npx not found on PATH. Install Node.js 20+ "
            "(https://nodejs.org) to use the @anthropic-ai/mcpb CLI."
        )
    return subprocess.run(
        [npx, "--yes", "@anthropic-ai/mcpb@2", *args],
        check=True,
    )


def validate_manifest() -> None:
    run_mcpb(["validate", str(STAGING_DIR / "manifest.json")])


def run_mcpb_pack(version: str) -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    out_path = DIST_DIR / f"keel-trade-{version}.mcpb"
    if out_path.exists():
        out_path.unlink()
    run_mcpb(["pack", str(STAGING_DIR), str(out_path)])
    return out_path


def verify_bundle(bundle: Path) -> None:
    run_mcpb(["info", str(bundle)])


def main() -> None:
    pyproject = get_pyproject()
    version = get_version(pyproject)

    print(f"Building keel-trade {version} .mcpb (cross-platform)")
    print(f"  SDK root: {SDK_ROOT}")

    clean_staging()
    copy_sources()
    copy_bootstrap()
    copy_icon()
    render_manifest(version)
    validate_manifest()
    bundle = run_mcpb_pack(version)

    size_mb = bundle.stat().st_size / (1024 * 1024)
    print(f"\nBuilt {bundle.name} ({size_mb:.1f} MB)")
    print(f"  -> {bundle}")

    print("\nBundle info:")
    verify_bundle(bundle)


if __name__ == "__main__":
    main()
