"""Monorepo gate: the public-mirror G2 gitleaks allowlist stays exact (Q-0468).

Gate G2 in ``.github/workflows/sync-keel-trade-public.yml`` runs a pinned
gitleaks over the fully staged public tree. ``gitleaks dir`` auto-discovers
``.gitleaks.toml`` at the scanned-tree root, which is this package's file. Two
DSL ``StagedChange(key=...)`` identifiers in the bundled validator catalog clear
gitleaks' ``generic-api-key`` entropy threshold, so the config allowlists the
identifiers **by exact enumeration**.

That gate runs ONLY inside the sync workflow, which fires on an ``sdk-v*`` tag —
so a mismatch between the enumeration and the catalog is latent until release,
where a red G2 means a half-promoted release (the wheel publishes in the
parallel job). This test moves both failure modes to the introducing PR:

  * the enumeration must be exactly the catalog's live ``StagedChange`` key set
    — a new staged change without a same-PR ``.gitleaks.toml`` edit reds here;
  * the allowlist must stay a LINE regex with no ``paths`` entry. In gitleaks
    8.30.1 a ``paths`` entry excludes the WHOLE FILE from scanning and ignores
    ``condition``/``matchCondition`` entirely, so re-adding one would silently
    stop the scan from looking inside ``catalog.py`` at all — a planted
    ``sk_live_…`` there went undetected under the original path-scoped config.

Deliberately does NOT shell out to gitleaks: the binary is not present in the
test image. The behavioural proof (seeded credentials in and outside
``catalog.py``, scanned with the pinned 8.30.1) is recorded in the Q-0468
ledger entry. What this test owns is the config's SHAPE and its coupling to the
catalog.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "scripts" / "mirror_gates.py").is_file() and (
            parent / "packages" / "keel-trade" / "keel-sdk"
        ).is_dir():
            return parent
    raise RuntimeError("could not locate monorepo root from test file")


REPO_ROOT = _repo_root()
SDK_ROOT = REPO_ROOT / "packages" / "keel-trade" / "keel-sdk"
GITLEAKS_CONFIG = SDK_ROOT / ".gitleaks.toml"
BUNDLED_CATALOG = SDK_ROOT / "pipeline_engine" / "dsl" / "catalog.py"
ALLOWLIST_ID = "dsl-stagedchange-registry-keys"

# `StagedChange(` followed by its `key="..."` on the next line — the spelling
# the catalog is generated/written in.
STAGED_CHANGE_KEY_RE = re.compile(r'StagedChange\(\s*\n\s*key="([^"]+)"')


@pytest.fixture(scope="module")
def config() -> dict:
    return tomllib.loads(GITLEAKS_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def allowlist(config: dict) -> dict:
    entries = [a for a in config.get("allowlists", []) if a.get("id") == ALLOWLIST_ID]
    assert len(entries) == 1, (
        f"expected exactly one allowlist with id={ALLOWLIST_ID!r} in "
        f"{GITLEAKS_CONFIG.relative_to(REPO_ROOT)}; found {len(entries)}"
    )
    return entries[0]


@pytest.fixture(scope="module")
def catalog_keys() -> set[str]:
    keys = set(STAGED_CHANGE_KEY_RE.findall(BUNDLED_CATALOG.read_text(encoding="utf-8")))
    # Non-vacuity: this parse is the subject of the comparison below, so a
    # silent zero here would make the whole module pass while proving nothing.
    # The count is read from the catalog, which the allowlist edit does not
    # touch — so this assertion holds under either direction of the drift seed.
    assert len(keys) >= 10, (
        f"parsed only {len(keys)} StagedChange keys from "
        f"{BUNDLED_CATALOG.relative_to(REPO_ROOT)} — the catalog spelling changed "
        "and this test's parse no longer sees the registry it is meant to guard"
    )
    return keys


def test_default_ruleset_stays_on(config: dict) -> None:
    """Never swap the default rules out — the allowlist is the only exemption."""
    assert config.get("extend", {}).get("useDefault") is True


def test_allowlist_has_no_path_scope(allowlist: dict) -> None:
    """A ``paths`` entry would exclude whole files from the scan (Q-0468).

    Measured on the pinned gitleaks 8.30.1: with ``paths`` present, a planted
    ``sk_live_…`` inside ``catalog.py`` is NOT reported, and neither
    ``condition = "AND"`` nor ``matchCondition = "AND"`` restores it — both are
    accepted and ignored. The suppression must therefore be expressed only as a
    line regex.
    """
    assert "paths" not in allowlist, (
        "the G2 allowlist must not carry a `paths` entry: in gitleaks 8.30.1 that "
        "excludes the entire file from scanning and silently ignores "
        "condition/matchCondition, so real credentials in that file go "
        "unreported (Q-0468). Enumerate the exact lines instead."
    )
    assert allowlist.get("regexTarget") == "line"


def test_allowlist_enumerates_exactly_the_catalog_staged_change_keys(
    allowlist: dict, catalog_keys: set[str]
) -> None:
    """The enumeration is the catalog's key set — no more, no less.

    More would be an unexplained exemption; fewer means the next ``sdk-v*`` tag
    can red at G2 with the wheel already publishing.
    """
    regexes = allowlist.get("regexes", [])
    assert len(regexes) == 1, "expected a single enumerating regex"
    alternation = re.search(r"\(([^)]*)\)", regexes[0])
    assert alternation, f"the allowlist regex carries no alternation group: {regexes[0]!r}"
    listed = set(alternation.group(1).split("|"))

    assert listed == catalog_keys, (
        "the G2 gitleaks allowlist enumeration has drifted from the bundled "
        f"catalog's StagedChange keys.\n  only in .gitleaks.toml: {sorted(listed - catalog_keys)}"
        f"\n  only in catalog.py:     {sorted(catalog_keys - listed)}\n"
        "Edit packages/keel-trade/keel-sdk/.gitleaks.toml in this same PR — "
        "otherwise the next sdk-v* sync can fail Gate G2 after the wheel job "
        "has already published (Q-0468)."
    )


def test_the_two_known_offenders_are_covered(allowlist: dict) -> None:
    """Non-vacuity anchored on the finding itself.

    These are the exact two lines gitleaks 8.30.1 reported (catalog.py:3836 and
    :3929 at discovery). If the enumeration ever stops covering them the gate
    reds at tag time, whatever else the set comparison above says.
    """
    pattern = re.compile(allowlist["regexes"][0])
    for key in ("d2-refinement-unproven", "d4-slot-sib-narrowing"):
        assert pattern.search(f'        key="{key}",'), (
            f"{key!r} is a known generic-api-key false positive and is no longer "
            "covered by the G2 allowlist"
        )


def test_the_allowlist_regex_does_not_exempt_secret_shapes(allowlist: dict) -> None:
    """Control arm: the enumeration must not match anything but its own members.

    A shape-based regex (the original ``key="[a-z0-9-]*"``) would have swallowed
    a lowercase-hex token spelled on a ``key=`` line. Each probe below is a
    credential shape that MUST remain visible to the scanner.
    """
    pattern = re.compile(allowlist["regexes"][0])
    # Probe values are ASSEMBLED, never written as literals: this file is itself
    # inside the scanned public tree, and a literal credential-shaped token here
    # reds the very gate the test guards (observed while writing it — three
    # findings, which is the gate working).
    hex_token = "a3f81b6c" + "92d047e5" + "a8b31f4c" + "76e9d208"
    pat_token = "ghp-" + "a1b2c3d4e5f6g7h8" + "i9j0k1l2m3n4o5p6"
    stripe_token = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
    probes = [
        f'    key="{hex_token}",',  # hex token, kebab-compatible charset
        f'    key="{pat_token}",',
        '    key="d2-refinement-unproven-but-longer",',  # superstring of a listed key
        f'    STRIPE = "{stripe_token}"',
    ]
    for probe in probes:
        assert not pattern.search(probe), (
            f"the G2 allowlist regex exempts {probe!r} — it must match ONLY the "
            "enumerated StagedChange identifiers"
        )


def test_bundled_catalog_matches_the_monorepo_source() -> None:
    """The scanned copy is the vendored one; keep the two in step.

    If the vendored catalog were stale the enumeration above would be guarding
    a file the mirror does not actually ship.
    """
    source = REPO_ROOT / "libs" / "pipeline_engine" / "dsl" / "catalog.py"
    source_keys = set(STAGED_CHANGE_KEY_RE.findall(source.read_text(encoding="utf-8")))
    bundled_keys = set(STAGED_CHANGE_KEY_RE.findall(BUNDLED_CATALOG.read_text(encoding="utf-8")))
    assert source_keys, "no StagedChange keys parsed from libs/pipeline_engine/dsl/catalog.py"
    assert source_keys == bundled_keys, (
        "the bundled SDK catalog's StagedChange keys differ from "
        f"libs/pipeline_engine/dsl/catalog.py:\n  only in libs: "
        f"{sorted(source_keys - bundled_keys)}\n  only in SDK:  "
        f"{sorted(bundled_keys - source_keys)}"
    )
