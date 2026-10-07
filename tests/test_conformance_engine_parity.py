"""The SDK's local validator gives the SAME verdicts as the server's (Q-1870).

What an agent sees on a local dry-run — ``keel_strategy_compose(dry_run=true)``,
``keel strategy validate`` — is the VENDORED ``pipeline_engine`` validating
against the BUNDLED ``keel/data/registry.json`` (``keel/tools/local.py`` →
``keel.data.registry.load_registry()`` → ``load_registry_from_json``). What the
platform says on save is the LIVE ``libs/pipeline_engine`` validating against
the live ``COMPONENT_REGISTRY``. The two share their code (``build_data.py``
copies it verbatim) but not their registry: the SDK's is a JSON projection of
the live one, and a field the projection drops is a rule that silently never
fires on the local path. That is Q-1870 exactly — ``population_scope`` was not
emitted, so ``_is_mask_emitter`` never matched and pass 7b returned before
NONDENSE_TERMINAL_WEIGHTS, XS_BEFORE_UNIVERSE_MASK and MASK_SLOT_NOT_WIRED;
an agent building a rotation got a clean dry-run the server then warned on.
Same class as Q-1293 on the SDK's copy.

A per-field freshness test can only cover the fields someone thought of. This
guard compares OUTCOMES instead: every conformance fixture
(``libs/pipeline_engine/dsl/fixtures/conformance/**``, kinds ``dsl`` and
``graph``) through both engines, in two catalog configurations — shipped, and
every staged change at its terminal stage (so the DORMANT mask rules are
compared at the stage they will ship at too) — asserting identical issue codes
per severity lane, and the identical parse code for a parse-reject subject.

Two arms, because the full engine cannot load everywhere:

- ``declared`` — the SDK engine against each fixture's declared
  ``expected_*`` sets. libs' ``validator_parity_test.py`` holds the LIVE engine
  to exactly those sets, unfiltered, in ``test-python``; so equality here is
  equality with the live engine, transitively. Runs in every lane, including
  ``sdk-test`` (whose venv has no pandas/TA-Lib for the component registry).
- ``live`` — both engines, each in its own isolated subprocess (both packages
  are named ``pipeline_engine``; one process can hold only one), compared
  directly. Skips ONLY when the live engine's third-party dependencies are
  absent from this interpreter, and says which; any other failure is red.

Each engine subprocess proves which ``pipeline_engine`` it imported, so
neither side can silently validate with the other's engine (a
``PYTHONPATH=libs`` shell would otherwise make the "SDK" side the live one).

Divergences go in ``_KNOWN_DIVERGENCES`` with the reason, never silently, and
each row is asserted still-divergent so it cannot outlive its cause. No rule in
the corpus genuinely needs the server, and the table is EMPTY: the 14 rows this
guard found on its first run were three HYDRATION gaps in
``load_registry_from_json`` (libs/pipeline_engine/base/registry_types.py) —
``composer_inputs``, the structural param ``type_structure`` and
``expected_slot_domain`` were emitted into registry.json but never read back —
and the hydrator now reads each one back strictly (a malformed or
non-round-tripping field raises; nothing degrades).

SEED (run 2026-09-23): strip all 104 ``population_scope`` keys from the bundled
``keel/data/registry.json`` (latest + per-version) → 13 red, 915 green:
``NONDENSE_TERMINAL_WEIGHTS/reject_topn_without_converter`` in the declared
and live arms (the Q-1870 repro shape), the four ``XS_BEFORE_UNIVERSE_MASK/*``
fixtures and ``MASK_SLOT_NOT_WIRED/reject_unmasked_pooling_slot`` in the
terminal and live arms, and ``test_every_fired_code_fires_on_the_sdk_engine``
naming XS_BEFORE_UNIVERSE_MASK + MASK_SLOT_NOT_WIRED as dark. Control arm:
every ``TYPE_MISMATCH/*`` fixture and both corpus-size checks stay green
through the seed. Restoring the saved bytes (sha-checked) restores 928 green.

SEED (run 2026-09-23, after the hydrator fix emptied _KNOWN_DIVERGENCES; each
applied to the VENDORED pipeline_engine/base/registry_types.py by exact
string replace and reversed the same way, sha-checked back to the clean file;
942 green before and after):

- A: ``composer_inputs=None`` in ``load_registry_from_json`` (latest and per
  version) → 17 red, 925 green: the 8 composer fixtures
  (``CLOCK_MISMATCH/{accept_canonical_shape_b, reject_combiner_12h_x_1d,
  reject_regime_gate_1d_on_1h}``, ``COMPOSER_INPUT_TYPE_MISMATCH/{reject_basic,
  reject_nonadjacent_composer}``,
  ``COMPOSER_KEY_MISMATCH/reject_omitted_role_key_defaults_to_no_branch``,
  ``TERMINAL_CLOCK_MISMATCH/reject_crossover_omitted_keys_15min_under_1d``,
  ``VALUE_DOMAIN_MISMATCH/reject_hard_edge_direct``) in the declared and live
  arms, plus ``test_every_fired_code_fires_on_the_sdk_engine`` naming
  COMPOSER_INPUT_TYPE_MISMATCH dark.
- B: param ``type_`` rebuilt from the display ``type`` string again
  (``_resolve_param_type``) → 8 red, 934 green: the four
  ``PARAM_TYPE_MISMATCH/*`` rejects in the declared and live arms.
- C: ``expected_slot_domain=None`` → 4 red, 938 green:
  ``VALUE_DOMAIN_MISMATCH/reject_wrong_convention_mask_slot`` and
  ``VALUE_DOMAIN_UNPROVEN/reject_unproven_mask_slot``, declared and live.

Each seed is the other two's control: no seed reddened a fixture belonging to
another gap, and every ``TYPE_MISMATCH/*`` fixture and both corpus-size checks
stayed green through all three.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path

import pytest


SDK_ROOT = Path(__file__).resolve().parents[1]
MONO_ROOT = SDK_ROOT.parents[2]
LIBS_DIR = MONO_ROOT / "libs"
CONFORMANCE_DIR = LIBS_DIR / "pipeline_engine" / "dsl" / "fixtures" / "conformance"

_ENGINE_ROOTS = {"sdk": SDK_ROOT, "libs": LIBS_DIR}

#: Exit code a ``libs`` subprocess uses for "a THIRD-PARTY dependency of the
#: live engine is not installed in this interpreter" — the one skip reason.
_EXIT_MISSING_DEP = 3

#: Top-level packages that live in this repo. A missing one of these is a
#: broken path, never an absent dependency, so it can never become a skip.
_REPO_PACKAGES = (
    frozenset(p.name for p in LIBS_DIR.iterdir() if p.is_dir() and (p / "__init__.py").exists())
    if LIBS_DIR.is_dir()
    else frozenset()
)

#: fixture id → reason. A divergence listed here is asserted PRESENT (so the
#: row cannot outlive its cause) and excluded from equality. EMPTY: the 14
#: rows lane C parked on 2026-09-23 were three hydration gaps in
#: ``load_registry_from_json`` (``composer_inputs``, the structural param
#: ``type_structure``, ``expected_slot_domain`` — emitted, never read back),
#: closed the same day by reading each field back strictly. A new row needs a
#: reason no hydrator fix can close; none in the corpus has one.
_KNOWN_DIVERGENCES: dict[str, str] = {}

#: Codes the corpus declares that the SDK engine fires on NO fixture. Pinned
#: exactly (empty) so a code cannot go dark on the SDK without joining it.
_CODES_DARK_ON_SDK: frozenset[str] = frozenset()

_LANES = ("errors", "warnings", "info")
_SEVERITY_LANE = {"error": "errors", "warning": "warnings", "info": "info"}

pytestmark = pytest.mark.skipif(
    not CONFORMANCE_DIR.is_dir(),
    reason="monorepo-only: the conformance corpus lives in libs/ (absent in the public mirror)",
)


# ─── the corpus runner (executed in an isolated subprocess per engine) ─────


def _isolate(engine: str) -> None:
    """Make ``pipeline_engine`` resolve to exactly one engine's tree."""
    own = _ENGINE_ROOTS[engine].resolve()
    other = _ENGINE_ROOTS["libs" if engine == "sdk" else "sdk"].resolve()
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() not in (own, other)]
    sys.path.insert(0, str(own))
    if engine == "sdk":
        # The wheel does not carry pandas, so the SDK engine resolves a raw
        # ``DataFrame`` through its vendored pandas stub. A dev environment
        # that happens to have pandas installed took the other branch of
        # ``_resolve_type_name`` and hid a RelationError CI's clean venv hit
        # (2026-09-30: the stub's DataFrame was ``object``). Block pandas so
        # every run exercises the wheel's path.
        sys.modules["pandas"] = None  # type: ignore[assignment]


def _codes(result) -> dict:
    return {lane: sorted({i.code for i in getattr(result, lane)}) for lane in _LANES}


def _run_corpus(engine: str) -> dict:
    """Validate every fixture in both configurations with ONE engine."""
    import pipeline_engine

    resolved = Path(pipeline_engine.__file__).resolve()
    if not resolved.is_relative_to(_ENGINE_ROOTS[engine].resolve()):
        raise SystemExit(f"{engine} runner imported the wrong pipeline_engine: {resolved}")

    if engine == "sdk":
        # Exactly the SDK's runtime hydration (keel/tools/local.py::_ensure_registry).
        from keel.data.registry import load_registry

        load_registry()
    else:
        from pipeline_engine.registry_loader import ensure_registry_loaded

        ensure_registry_loaded()

    from pipeline_engine.dsl.catalog import STAGED_CHANGES, Stage, staged_stage_override
    from pipeline_engine.dsl.emitter import GraphModel, graph_to_spec
    from pipeline_engine.dsl.parser import DSLParseError, parse_strategy
    from pipeline_engine.dsl.validator import validate_strategy

    def validate(fixture: dict) -> dict:
        if fixture.get("kind") == "dsl":
            try:
                strategy = parse_strategy(fixture["source"])
            except DSLParseError as exc:
                return {"parse": exc.code}
            return {"parse": None, **_codes(validate_strategy(strategy))}
        graph = GraphModel.from_dict(fixture["graph"])
        lock = (fixture.get("graph") or {}).get("componentLock")
        return {"parse": None, **_codes(validate_strategy(graph_to_spec(graph), lock=lock))}

    fixtures = {
        f"{p.parent.name}/{p.stem}": json.loads(p.read_text())
        for p in sorted(CONFORMANCE_DIR.glob("*/*.json"))
    }
    out: dict = {
        "engine_file": str(resolved),
        "fixtures": {},
        "signatures": _version_signatures(),
    }
    for fid, fx in fixtures.items():
        out["fixtures"][fid] = {"shipped": validate(fx)}
    with ExitStack() as stack:
        for key, change in STAGED_CHANGES.items():
            terminal = (
                change.terminal_stage
                if change.kind == "flow-shape"
                else Stage(change.terminal_stage)
            )
            stack.enter_context(staged_stage_override(key, terminal))
        for fid, fx in fixtures.items():
            out["fixtures"][fid]["terminal"] = validate(fx)
    return out


def _version_signatures() -> dict:
    """Every (component, version) signature's version-scoped read surface.

    What pass 5 and pass 8 read per PINNED version: its parameter names, its
    slot reads and its cross-param constraints (Q-2202 — the JSON hydrator
    used to give every older version the latest's slot reads and
    constraints, so a strategy pinned at RollingUniverseMask v1 was checked
    for v2's ``dollar_volume_slot``).
    """
    from pipeline_engine.base.registry import COMPONENT_REGISTRY

    return {
        f"{name}@{ver}": {
            "params": sorted(sig.parameters),
            "slot_reads": sorted(sig.slot_reads),
            "constraints": [dict(c) for c in sig.param_constraints],
        }
        for name, versions in COMPONENT_REGISTRY.items()
        for ver, sig in versions.items()
    }


def _main(engine: str, out_path: str) -> int:
    _isolate(engine)
    try:
        result = _run_corpus(engine)
    except ImportError as exc:
        missing = _missing_third_party(exc) if engine == "libs" else None
        if missing:
            print(f"missing third-party dependency: {', '.join(missing)}", file=sys.stderr)
            return _EXIT_MISSING_DEP
        raise
    Path(out_path).write_text(json.dumps(result))
    return 0


def _missing_third_party(exc: ImportError) -> list[str] | None:
    """The absent third-party modules behind ``exc`` — or None if ANY part of
    it is something else (a repo module, a broken import, a non-import error).

    Two shapes reach here: a bare ``ModuleNotFoundError`` (``exc.name``), and
    ``registry_loader.ensure_registry_loaded``'s aggregate ``ImportError``
    ("Failed to import N component module(s):" + one line per module). The
    aggregate counts as a missing dependency only when EVERY one of its N
    lines is a ``No module named`` for a non-repo top-level package.
    """
    import re

    def third_party(name: str) -> str | None:
        top = name.split(".")[0]
        return top if top and top not in _REPO_PACKAGES and top != "pipeline_engine" else None

    if isinstance(exc, ModuleNotFoundError) and exc.name:
        top = third_party(exc.name)
        return [top] if top else None
    head = re.match(r"Failed to import (\d+) component module\(s\):", str(exc))
    if not head:
        return None
    names = re.findall(r"No module named '([\w.]+)'", str(exc))
    tops = [third_party(n) for n in names]
    if len(names) != int(head.group(1)) or not all(tops):
        return None
    return sorted(set(tops))


@lru_cache(maxsize=None)
def _engine_results(engine: str, tmp: str) -> dict | str:
    """Run the corpus under ``engine``; a str return is a skip reason."""
    out = Path(tmp) / f"{engine}.json"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["METRICS_ENABLED"] = "false"
    proc = subprocess.run(
        [sys.executable, "-E", str(Path(__file__).resolve()), engine, str(out)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(tmp)),
        timeout=900,
    )
    if engine == "libs" and proc.returncode == _EXIT_MISSING_DEP:
        return proc.stderr.strip()
    if proc.returncode != 0:
        raise AssertionError(
            f"{engine} corpus runner failed (rc={proc.returncode}):\n"
            f"{proc.stderr[-4000:] or proc.stdout[-4000:]}"
        )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def _tmp(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("conformance-engine-parity"))


@pytest.fixture(scope="module")
def sdk_results(_tmp) -> dict:
    res = _engine_results("sdk", _tmp)
    assert isinstance(res, dict)
    return res


@pytest.fixture(scope="module")
def libs_results(_tmp) -> dict:
    res = _engine_results("libs", _tmp)
    if isinstance(res, str):
        pytest.skip(
            f"live engine not loadable in this interpreter ({res}); the `declared` arm "
            f"still holds the SDK to the verdicts libs' validator_parity_test pins"
        )
    return res


# ─── fixture expectations (what libs' validator_parity_test pins) ──────────


@lru_cache(maxsize=1)
def _fixtures() -> dict[str, dict]:
    return (
        {
            f"{p.parent.name}/{p.stem}": json.loads(p.read_text())
            for p in sorted(CONFORMANCE_DIR.glob("*/*.json"))
        }
        if CONFORMANCE_DIR.is_dir()
        else {}
    )


_FIXTURE_IDS = sorted(_fixtures())


def _declared_shipped(fx: dict) -> dict:
    if fx.get("expected_parse_error"):
        return {"parse": fx["expected_parse_error"]["code"]}
    return {
        "parse": None,
        "errors": sorted({e["code"] for e in fx.get("expected_errors", [])}),
        "warnings": sorted({e["code"] for e in fx.get("expected_warnings", [])}),
        "info": sorted({e["code"] for e in fx.get("expected_info", [])}),
    }


def _declared_at_terminal(fx: dict) -> dict[str, str]:
    block = fx.get("expected_at_terminal") or {}
    return {
        e["code"]: _SEVERITY_LANE[e["severity"]]
        for k in ("expected_errors", "expected_warnings", "expected_info")
        for e in block.get(k, [])
    }


def _all_codes(res: dict) -> set[str]:
    if res.get("parse"):
        return {res["parse"]}
    return {c for lane in _LANES for c in res[lane]}


# ─── the declared arm (runs everywhere) ─────────────────────────────────────


@pytest.mark.parametrize("fixture_id", _FIXTURE_IDS)
def test_sdk_engine_matches_declared_verdict(fixture_id: str, sdk_results) -> None:
    """Shipped configuration: the SDK engine produces exactly the verdict the
    live engine is held to (codes per severity lane, or the parse code)."""
    fx = _fixtures()[fixture_id]
    got = sdk_results["fixtures"][fixture_id]["shipped"]
    if fixture_id in _KNOWN_DIVERGENCES:
        assert got != _declared_shipped(fx), (
            f"{fixture_id} no longer diverges — the hydrator gap is closed; delete "
            f"its _KNOWN_DIVERGENCES row ({_KNOWN_DIVERGENCES[fixture_id]})"
        )
        return
    assert got == _declared_shipped(fx), (
        f"{fixture_id}: the SDK's local validator diverges from the server's verdict.\n"
        f"  server (declared): {_declared_shipped(fx)}\n  sdk (bundled):     {got}\n"
        f"A registry field the validator reads is probably missing from "
        f"keel/data/registry.json — emit it in scripts/build_data.py and regenerate."
    )


@pytest.mark.parametrize("fixture_id", _FIXTURE_IDS)
def test_sdk_engine_matches_declared_terminal_verdict(fixture_id: str, sdk_results) -> None:
    """Full terminal configuration: every ``expected_at_terminal`` fire occurs
    on the SDK engine in its declared lane, and nothing undeclared appears —
    libs' Stage-B contract, so a DORMANT rule is compared at the stage it will
    ship at, not only at its current silence."""
    if fixture_id in _KNOWN_DIVERGENCES:
        pytest.skip(
            f"known divergence (asserted present in the shipped arm): {_KNOWN_DIVERGENCES[fixture_id]}"
        )
    fx = _fixtures()[fixture_id]
    shipped = sdk_results["fixtures"][fixture_id]["shipped"]
    terminal = sdk_results["fixtures"][fixture_id]["terminal"]
    if fx.get("expected_parse_error"):
        assert terminal == shipped
        return
    declared = _declared_at_terminal(fx)
    s_codes, t_codes = _all_codes(shipped), _all_codes(terminal)
    undeclared = (t_codes - s_codes) - set(declared)
    missing = (set(declared) - s_codes) - t_codes
    assert not undeclared, (
        f"{fixture_id}: undeclared terminal fires on the SDK engine: {sorted(undeclared)}"
    )
    assert not missing, (
        f"{fixture_id}: expected_at_terminal declares {sorted(missing)} but the SDK "
        f"engine did not fire them at the terminal configuration"
    )
    for code, lane in declared.items():
        if code in t_codes:
            assert code in terminal[lane], f"{fixture_id}: {code} fired outside lane {lane!r}"


# ─── the live arm (both engines, compared directly) ─────────────────────────


@pytest.mark.parametrize("fixture_id", _FIXTURE_IDS)
def test_sdk_engine_matches_live_engine(fixture_id: str, sdk_results, libs_results) -> None:
    """Both engines, both configurations: identical verdicts per fixture."""
    sdk = sdk_results["fixtures"][fixture_id]
    live = libs_results["fixtures"][fixture_id]
    if fixture_id in _KNOWN_DIVERGENCES:
        assert sdk != live, f"{fixture_id} no longer diverges — delete its _KNOWN_DIVERGENCES row"
        return
    assert sdk == live, (
        f"{fixture_id}: local dry-run and server disagree.\n  live: {live}\n  sdk:  {sdk}"
    )


def test_sdk_hydrates_every_version_signature_like_the_live_registry(
    sdk_results, libs_results
) -> None:
    """Every version the SDK ships reads the slots, params and constraints the
    live registry gives THAT version (Q-2202) — outcome fixtures only cover
    the pins someone wrote a fixture for; this covers every pin."""
    sdk, live = sdk_results["signatures"], libs_results["signatures"]
    assert set(sdk) <= set(live), sorted(set(sdk) - set(live))
    # Non-vacuity: the population holds older versions whose read surface
    # differs from their latest's — exactly the shape the hydrator got wrong.
    latest = {}
    for key in live:
        name, ver = key.rsplit("@", 1)
        latest[name] = max(latest.get(name, 0), int(ver))
    shapes = [
        k
        for k in sdk
        if int(k.rsplit("@", 1)[1]) < latest[k.rsplit("@", 1)[0]]
        and live[k] != live[f"{k.rsplit('@', 1)[0]}@{latest[k.rsplit('@', 1)[0]]}"]
    ]
    assert len(sdk) >= 200 and len(shapes) >= 5, (len(sdk), shapes)
    diverged = {k: {"sdk": sdk[k], "live": live[k]} for k in sdk if sdk[k] != live[k]}
    assert not diverged, (
        "the SDK hydrates these versions with another version's read surface — "
        "emit the field per version in scripts/build_data.py and read it per "
        f"version in load_registry_from_json: {json.dumps(diverged, indent=1)[:4000]}"
    )


# ─── non-vacuity ────────────────────────────────────────────────────────────


def test_each_engine_ran_its_own_tree_over_the_whole_corpus(sdk_results, libs_results) -> None:
    assert Path(sdk_results["engine_file"]).is_relative_to(SDK_ROOT)
    assert Path(libs_results["engine_file"]).is_relative_to(LIBS_DIR)
    for res in (sdk_results, libs_results):
        assert sorted(res["fixtures"]) == _FIXTURE_IDS


def test_the_corpus_is_the_whole_corpus(sdk_results) -> None:
    """The comparison is not over a truncated population: every fixture file
    was validated, in both configurations, and the corpus is its known size."""
    assert len(_FIXTURE_IDS) >= 300, len(_FIXTURE_IDS)
    assert sorted(sdk_results["fixtures"]) == _FIXTURE_IDS
    for fid in _FIXTURE_IDS:
        assert set(sdk_results["fixtures"][fid]) == {"shipped", "terminal"}, fid
    kinds = {_fixtures()[f].get("kind", "graph") for f in _FIXTURE_IDS}
    assert kinds == {"graph", "dsl"}, kinds


def test_every_fired_code_fires_on_the_sdk_engine(sdk_results) -> None:
    """Every code the corpus declares — shipped lanes, parse codes, and
    ``expected_at_terminal`` — fires on the SDK engine on at least one
    fixture. Pinned by name for Q-1870's three universe-mask rules."""
    declared: set[str] = set()
    for fx in _fixtures().values():
        declared |= _all_codes(_declared_shipped(fx))
        declared |= set(_declared_at_terminal(fx))
    fired: set[str] = set()
    for res in sdk_results["fixtures"].values():
        fired |= _all_codes(res["shipped"]) | _all_codes(res["terminal"])
    assert len(declared) >= 80, len(declared)
    assert declared - fired == set(_CODES_DARK_ON_SDK), (
        f"codes the corpus declares that never fire on the SDK engine: "
        f"{sorted(declared - fired)} (pinned: {sorted(_CODES_DARK_ON_SDK)})"
    )
    assert {
        "NONDENSE_TERMINAL_WEIGHTS",
        "XS_BEFORE_UNIVERSE_MASK",
        "MASK_SLOT_NOT_WIRED",
    } <= fired


if __name__ == "__main__":  # the per-engine subprocess entry point
    sys.exit(_main(sys.argv[1], sys.argv[2]))
