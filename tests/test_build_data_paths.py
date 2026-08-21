"""Regression tests for `scripts/build_data.py`'s sys.path ordering.

The SDK ships a VENDORED `pipeline_engine/` that this very script generates.
If the SDK root precedes `libs/` on `sys.path`, a build imports its own
previous OUTPUT instead of the live engine — whose `registry_loader` is a
no-op stub — and dies with "COMPONENT_REGISTRY is empty in SDK env".

The original guard (`if sdk_str not in sys.path: insert(0, ...)`) got this
wrong in three configurations, and only worked locally by accident: an
editable install of `keel-trade` puts the SDK root on `sys.path` via a
`.pth`, so the insert was skipped and `libs/` (inserted first) happened to
win. Take that `.pth` away — or run with `PYTHONPATH=<sdk>:libs` — and the
vendored tree shadowed the live engine.

These tests pin the invariant directly: after `ensure_imports()`, `libs/`
precedes the SDK root, from ANY starting configuration.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_data.py"


@pytest.fixture(scope="module")
def build_data():
    """Import build_data.py by path (it is a script, not an installed module)."""
    spec = importlib.util.spec_from_file_location("_build_data_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _order(build_data) -> tuple[int, int]:
    return sys.path.index(str(build_data.LIBS_DIR)), sys.path.index(str(build_data.SDK_ROOT))


@pytest.mark.parametrize(
    "starting_path",
    [
        pytest.param([], id="neither-present"),
        pytest.param(["LIBS"], id="libs-only"),
        pytest.param(["SDK"], id="sdk-only-editable-install"),
        pytest.param(["SDK", "LIBS"], id="sdk-ahead-of-libs"),
        pytest.param(["LIBS", "SDK"], id="already-correct"),
    ],
)
def test_libs_always_precedes_sdk_root(build_data, monkeypatch, starting_path):
    """`libs/` must win for `pipeline_engine` no matter how we start."""
    subs = {"LIBS": str(build_data.LIBS_DIR), "SDK": str(build_data.SDK_ROOT)}
    monkeypatch.setattr(sys, "path", [subs[k] for k in starting_path] + ["/some/other/entry"])

    build_data.ensure_imports()

    libs_at, sdk_at = _order(build_data)
    assert libs_at < sdk_at, f"libs must precede the SDK root; got {sys.path[:3]}"


def test_no_duplicate_entries(build_data, monkeypatch):
    """Re-seeding must not leave the entries duplicated on repeated calls."""
    libs_str, sdk_str = str(build_data.LIBS_DIR), str(build_data.SDK_ROOT)
    monkeypatch.setattr(sys, "path", [sdk_str, libs_str, sdk_str, "/other"])

    build_data.ensure_imports()
    build_data.ensure_imports()

    assert sys.path.count(libs_str) == 1
    assert sys.path.count(sdk_str) == 1


def test_both_roots_remain_importable(build_data, monkeypatch):
    """The SDK root stays on the path — the build imports `keel.data.registry`."""
    monkeypatch.setattr(sys, "path", ["/other"])

    build_data.ensure_imports()

    assert str(build_data.LIBS_DIR) in sys.path
    assert str(build_data.SDK_ROOT) in sys.path
    assert "/other" in sys.path


def test_assert_live_engine_rejects_the_vendored_tree(build_data, monkeypatch):
    """The build must loud-fail if `pipeline_engine` resolved to its own output."""

    class _Fake:
        __file__ = str(build_data.SDK_ROOT / "pipeline_engine" / "__init__.py")

    monkeypatch.setitem(sys.modules, "pipeline_engine", _Fake())

    with pytest.raises(RuntimeError, match="WRONG pipeline_engine"):
        build_data.assert_live_engine()
