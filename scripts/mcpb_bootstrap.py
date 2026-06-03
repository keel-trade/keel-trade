"""MCPB bundle entry point — ensures runtime deps are installed, then runs keel.

Claude Desktop (and other MCPB hosts) launch this script via:

    python3 ${__dirname}/server/_bootstrap.py

The bundle only ships pure-Python keel + pipeline_engine. Runtime deps
(pydantic_core, cryptography, fastmcp, etc.) include Rust/C extensions
whose .so/.pyd files are Python-version + platform specific, so we
can't ship them inside a single cross-platform bundle.

Instead, on first launch we pip-install them into a per-Python-version
cache directory (~/.keel/mcpb-lib/py3.X/) and add it to sys.path. First
launch takes ~10-30 sec; subsequent launches are instant because the
cache is reused.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_DEPS = [
    "click>=8.0",
    "pyyaml>=6.0",
    "httpx>=0.24",
    "pydantic>=2.0",
    "fastmcp>=3.1.1",
]

SENTINEL_IMPORTS = ("fastmcp", "pydantic_core", "httpx", "yaml", "click")


def lib_dir() -> Path:
    home = Path(os.environ.get("KEEL_HOME") or Path.home() / ".keel")
    return home / "mcpb-lib" / f"py{sys.version_info.major}.{sys.version_info.minor}"


def try_import_sentinels() -> bool:
    for mod in SENTINEL_IMPORTS:
        try:
            __import__(mod)
        except ImportError:
            return False
    return True


def clear_cached_imports() -> None:
    """Drop sentinel modules + their submodules from sys.modules.

    Without this, a failed-then-installed dep gets stuck on its stale
    import state for the rest of the process.
    """
    roots = {m.split(".")[0] for m in SENTINEL_IMPORTS}
    for name in list(sys.modules):
        if name.split(".")[0] in roots:
            del sys.modules[name]


def install_deps(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    print(
        f"keel-trade: installing runtime deps to {target} (first launch only, takes ~10-30 sec)...",
        file=sys.stderr,
        flush=True,
    )
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(target),
        "--quiet",
        "--no-compile",
        "--disable-pip-version-check",
        *REQUIRED_DEPS,
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(
            f"keel-trade: pip install failed with exit code {exc.returncode}. "
            f"Ensure pip + internet are available, then restart Claude Desktop.",
            file=sys.stderr,
            flush=True,
        )
        raise


def main() -> None:
    target = lib_dir()
    # Always put the cache dir at the front of sys.path so it wins
    # against any system-installed copies of fastmcp/pydantic/etc.
    target_str = str(target)
    if target_str in sys.path:
        sys.path.remove(target_str)
    sys.path.insert(0, target_str)

    if not try_import_sentinels():
        clear_cached_imports()
        install_deps(target)
        # Python caches negative path lookups in sys.path_importer_cache;
        # after pip install creates new dirs under target, we have to
        # invalidate that cache or subsequent imports return the cached
        # "not found" result even though the files are on disk.
        importlib.invalidate_caches()
        clear_cached_imports()
        if not try_import_sentinels():
            print(
                "keel-trade: deps still not importable after install. "
                f"Remove {target} and retry to clear a corrupt cache.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

    from keel.cli.main import cli

    sys.argv = [sys.argv[0], "mcp", "serve"]
    cli(standalone_mode=False)


if __name__ == "__main__":
    main()
