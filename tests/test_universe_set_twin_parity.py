"""The two `universe_set` implementations build their spec the SAME way.

`keel.tools.local.universe_set` (bundled, ships in the wheel) and
`pipeline_engine.mcp.tools.universe_set` (rich, in-cluster) are the documented
DUPLICATION BOUNDARY: the wheel cannot import the rich module, because the
build deliberately excludes `mcp/` (it drags in pandas/numpy/ta-lib). They
drifted exactly as that arrangement invites — audit 04 U-18 found the rich twin
dropping the floor and `max_leverages` on every criteria edit, and the SDK twin
dropping those PLUS `resolved`, `resolved_at` and `groups`, so
`keel universe set` silently un-resolved a resolved strategy.

Both now build the spec with ONE constructor,
`pipeline_engine.dsl.spec.universe_spec_with_declared_state`, which lives in the
DSL subset the SDK vendors — shared for real, with no import the packaging
forbids.

This file guards that at the SOURCE level, deliberately. A runtime parity test
cannot work here: `test_implementations_parity.py`'s rich path is unconditionally
skipped under every pytest entry point that has ever run it (Q-0707, see its
docstring), so a runtime comparison of these two twins would be vacuous in every
lane. Reading the two sources works in exactly the lane that ships the wheel.

Proof it can fail (recorded 2026-09-16): restoring either twin's inline
`UniverseSpec(...)` construction reds `test_neither_twin_builds_the_spec_by_hand`
naming that twin; dropping `min_trailing_notional_proxy` (since DV6b the floor is
`min_trailing_dollar_volume`) from either signature
reds `test_both_twins_accept_the_same_selectors`.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from keel.tools import local as sdk_tools


#: The rich twin is not importable in the wheel's env (no `mcp/` in the
#: bundle), so it is read as TEXT from the repo checkout. This test file ships
#: only in the repo — `tests/` is not part of the wheel — so an absent path is
#: a real failure, never a reason to skip.
REPO_ROOT = Path(__file__).resolve().parents[4]
RICH_TOOLS = REPO_ROOT / "libs" / "pipeline_engine" / "mcp" / "tools.py"

SHARED_CONSTRUCTOR = "universe_spec_with_declared_state"


def _function_node(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"no def {name} in the parsed source")


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _arg_names(node: ast.FunctionDef) -> list[str]:
    return [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]


@pytest.fixture(scope="module")
def twins() -> dict[str, ast.FunctionDef]:
    assert RICH_TOOLS.is_file(), (
        f"the rich twin is not at {RICH_TOOLS} — this test reads the repo "
        "checkout, and `tests/` does not ship in the wheel, so this is a real "
        "failure (a moved file), not an environment to skip"
    )
    rich = _function_node(RICH_TOOLS.read_text(), "universe_set")
    sdk = _function_node(inspect.getsource(sdk_tools), "universe_set")
    # Non-vacuity: both bodies are real functions, not stubs the walk would
    # trivially pass.
    assert len(rich.body) > 5 and len(sdk.body) > 5
    return {"rich pipeline_engine/mcp/tools.py": rich, "bundled keel/tools/local.py": sdk}


def test_both_twins_build_the_spec_with_the_shared_constructor(twins):
    for name, node in twins.items():
        assert SHARED_CONSTRUCTOR in _called_names(node), (
            f"{name}: universe_set no longer builds its spec with "
            f"{SHARED_CONSTRUCTOR}() — the two twins drift the moment either "
            "one constructs UniverseSpec itself (audit 04 U-18)"
        )


def test_neither_twin_builds_the_spec_by_hand(twins):
    for name, node in twins.items():
        assert "UniverseSpec" not in _called_names(node), (
            f"{name}: universe_set constructs UniverseSpec directly. Every "
            "declaration the signature has no argument for is then dropped — "
            "that is exactly U-18. Use "
            f"{SHARED_CONSTRUCTOR}(existing, …), which carries them."
        )


def test_both_twins_accept_the_same_selectors(twins):
    """A selector one surface accepts and the other does not is the same
    divergence one level up: the agent's edit means something different
    depending on which surface it reached."""
    rich, sdk = twins["rich pipeline_engine/mcp/tools.py"], twins["bundled keel/tools/local.py"]
    # `component_lock` is not a selector: it is the pins the rich twin
    # VALIDATES the result at (chat-api injects its working lock; dollar-volume
    # spec 02 §3). The SDK twin returns the rewritten source unvalidated.
    assert [a for a in _arg_names(rich) if a != "component_lock"] == [
        a for a in _arg_names(sdk) if a != "resolve"
    ]
    assert "component_lock" in _arg_names(rich)
    # `resolve` is not a selector either: the SDK twin resolves in the same
    # call by default (Q-2283, spec 03 U2) through the API; the rich twin's
    # writes are resolved by the save it feeds (chat-api persists through
    # keel-api, which resolves).
    assert "resolve" in _arg_names(sdk)
    # …and the set is the real one, not two empty lists agreeing.
    # The floor under its current name (DV6b) — both twins are writers.
    assert "min_trailing_dollar_volume" in _arg_names(sdk)
    assert {"source", "mode", "market", "top_n", "volume_quartiles"} <= set(_arg_names(sdk))


def test_the_shared_constructor_is_in_the_vendored_bundle():
    """The sharing only holds while the constructor's module ships in the
    wheel: it lives in `pipeline_engine.dsl.spec`, which the build copies, and
    the SDK imports it from the vendored tree at runtime."""
    from pipeline_engine.dsl import spec as vendored_spec

    assert hasattr(vendored_spec, SHARED_CONSTRUCTOR)
    # The import must resolve to the VENDORED tree — the wheel's contract —
    # not to `libs/`, which is absent from a pipx install.
    vendored = Path(vendored_spec.__file__).resolve()
    assert vendored.parts[-4:] == ("keel-sdk", "pipeline_engine", "dsl", "spec.py"), vendored
