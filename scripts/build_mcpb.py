#!/usr/bin/env python3
"""Build a .mcpb bundle for keel-trade.

Reads version from pyproject.toml, copies keel/ + pipeline_engine/ into a
staging directory alongside vendored runtime deps (auto-detected current
platform), renders the manifest from a template, and runs `mcpb pack` to
produce ``dist/keel-trade-<version>-<platform>.mcpb``.

Cross-platform model: one .mcpb per OS, built natively on its target
runner. Runtime deps include Rust/C extensions (pydantic_core, cryptography,
cffi, rpds, watchfiles, caio) that have no pure-Python fallback, so each
platform needs its own wheels. CI matrix builds all three; macOS is the
primary submission target since most Claude Desktop users are on macOS.

Layout produced (single PYTHONPATH dir avoids cross-platform pathsep issues):

    staging/
    ├── manifest.json
    ├── icon.png
    └── server/
        ├── keel/               # copied from ../keel
        ├── pipeline_engine/    # copied from ../pipeline_engine
        ├── click/              # vendored
        ├── pydantic/           # vendored
        └── ...

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
import sys
import tomllib
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SDK_ROOT / "scripts"
DIST_DIR = SDK_ROOT / "dist"
STAGING_DIR = DIST_DIR / "mcpb-staging"
SERVER_DIR = STAGING_DIR / "server"

MANIFEST_TEMPLATE = SCRIPTS_DIR / "manifest.template.json"
ICON_SRC = SCRIPTS_DIR / "icon.png"

PYPROJECT = SDK_ROOT / "pyproject.toml"


def get_pyproject() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def get_version(pyproject: dict) -> str:
    return pyproject["project"]["version"]


def get_runtime_deps(pyproject: dict) -> list[str]:
    return list(pyproject["project"]["dependencies"])


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
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache", "*.pyo"
            ),
        )


def install_runtime_deps(deps: list[str]) -> None:
    if not deps:
        raise RuntimeError("No runtime deps found in pyproject.toml")
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", str(SERVER_DIR),
        "--quiet",
        "--no-compile",
        "--disable-pip-version-check",
        *deps,
    ]
    print(f"Installing runtime deps to {SERVER_DIR.relative_to(SDK_ROOT)}: {' '.join(deps)}")
    subprocess.run(cmd, check=True)
    # Strip __pycache__ that occasionally appear despite --no-compile
    for pyc_dir in SERVER_DIR.rglob("__pycache__"):
        shutil.rmtree(pyc_dir, ignore_errors=True)


def copy_icon() -> None:
    if not ICON_SRC.exists():
        raise RuntimeError(
            f"Icon missing at {ICON_SRC}. "
            "Restore it from the brand assets: "
            "cp services/keel-site/public/brand/keel-mark-256.png "
            "packages/keel-trade/keel-sdk/scripts/icon.png"
        )
    shutil.copy2(ICON_SRC, STAGING_DIR / "icon.png")


PLATFORM_MAP = {
    "darwin": "darwin",
    "win32": "win32",
    "linux": "linux",
}


def current_platform() -> str:
    plat = PLATFORM_MAP.get(sys.platform)
    if plat is None:
        raise RuntimeError(
            f"Unsupported sys.platform={sys.platform!r}. "
            f"Run on darwin / win32 / linux."
        )
    return plat


def render_manifest(version: str, platform: str) -> None:
    template = MANIFEST_TEMPLATE.read_text()
    rendered = (
        template
        .replace("{{VERSION}}", version)
        .replace("{{PLATFORMS_JSON}}", json.dumps([platform]))
    )
    parsed = json.loads(rendered)
    if parsed["version"] != version:
        raise RuntimeError("Version substitution failed")
    if not re.match(r"^\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Refusing non-semver version: {version!r}")
    if parsed["compatibility"]["platforms"] != [platform]:
        raise RuntimeError("Platform substitution failed")
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


def run_mcpb_pack(version: str, platform: str) -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    out_path = DIST_DIR / f"keel-trade-{version}-{platform}.mcpb"
    if out_path.exists():
        out_path.unlink()
    run_mcpb(["pack", str(STAGING_DIR), str(out_path)])
    return out_path


def verify_bundle(bundle: Path) -> None:
    run_mcpb(["info", str(bundle)])


def main() -> None:
    pyproject = get_pyproject()
    version = get_version(pyproject)
    deps = get_runtime_deps(pyproject)
    platform = current_platform()

    print(f"Building keel-trade {version} .mcpb for {platform}")
    print(f"  SDK root: {SDK_ROOT}")

    clean_staging()
    copy_sources()
    install_runtime_deps(deps)
    copy_icon()
    render_manifest(version, platform)
    validate_manifest()
    bundle = run_mcpb_pack(version, platform)

    size_mb = bundle.stat().st_size / (1024 * 1024)
    print(f"\nBuilt {bundle.name} ({size_mb:.1f} MB)")
    print(f"  -> {bundle}")

    print("\nBundle info:")
    verify_bundle(bundle)


if __name__ == "__main__":
    main()
