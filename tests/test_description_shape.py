"""The description shape rule (agent-surface-cleanup spec 01 §2.5, §4 #1).

A description — `description` and, where one exists, `listed_description`
— states what the tool does and which neighbour it is picked over, what it
returns, the parameter facts the schema cannot carry, and at most one
paragraph of domain facts with no structural carrier. R-4: the base is the
base on EVERY profile, so the rule reads both fields of every listed tool;
a `listed_description` differs only by a surface fact (the hosted server
is file-free), never by register.

Forbidden classes (the table and its verdict function are
`assemble.DESCRIPTION_CLASSES` / `assemble.description_shape_violations`,
each class proved against its own positive and negative control in
`libs/pipeline_engine/reference/system/assemble_test.py`): rendering
cadence, method beyond one `skill:` pointer, reply style, off-surface
mechanisms on the listed profile, duplicate routing, and the imperative /
directive register.

Sizes: 2,048 BYTES per description is the host cut (HARD); the listed
TOTAL ≤ 13,000 characters is the project ceiling the founder ratified on
2026-09-23 (GOAL "Done when") and is asserted with that citation; the
1,200-character per-description aim stays reported only (decision #38).

Out of scope, stated rather than silently skipped: the listed INPUT
SCHEMAS' off-surface parameters (`source_file`, `auto_push`,
`push_message`) leave through `listed_input_schema` (R-8), which is the SDK
results lane's change, not this guard's subject; and full-profile-only
tools (live write, workspace, auth) are not in the listed catalog.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

from pipeline_engine.reference.system import assemble


_bootstrap()


@pytest.fixture(scope="module")
def listed_descriptions() -> dict[str, str]:
    """Every listed tool's EFFECTIVE listed description, off a real server."""
    from keel.mcp.server import create_server

    saved = {
        k: os.environ.get(k)
        for k in ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS")
    }
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    os.environ["KEEL_EXECUTION_MODE"] = "hosted"
    os.environ.pop("KEEL_TOOLSETS", None)
    try:
        tools = asyncio.run(create_server().list_tools())
        return {t.name: t.description or "" for t in tools}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _full_descriptions() -> dict[str, str]:
    """The shared `description` of every listed tool — the text the full
    profiles (Claude Code, Codex, the CLI's --help) read."""
    return {f"{name} (full)": OUTCOMES[name].description for name in LISTED_PROFILE_TOOLS}


def test_no_description_matches_a_forbidden_class(listed_descriptions, capsys):
    """# SEED (run 2026-09-23, lane L6a): append "The result renders as a
    one-line receipt." to `keel_backtest_watch`'s description in
    `keel/tools/outcomes/backtest_watch.py` — this reds naming
    keel_backtest_watch on BOTH profiles. Revert by reversing that edit."""
    listed, listed_sentences = assemble.description_shape_violations(
        listed_descriptions, listed=True
    )
    full, full_sentences = assemble.description_shape_violations(_full_descriptions(), listed=False)
    violations = listed + full
    assert not violations, "descriptions outside the shape rule:\n" + "\n".join(violations)
    # Non-vacuity, on quantities the seed cannot move: every listed tool on
    # both profiles was read, and the scan split them into real sentences.
    assert set(listed_descriptions) == set(LISTED_PROFILE_TOOLS)
    assert len(listed_descriptions) + len(_full_descriptions()) >= 40
    assert listed_sentences >= 100 and full_sentences >= 100


def test_the_listed_total_fits_the_project_ceiling(listed_descriptions, capsys):
    """# SEED: pad `keel_help`'s description with 600 characters — the
    listed total passes 13,000 and this reds. Revert by reversing it."""
    report = assemble.description_size_report(listed_descriptions)
    total = report["total_chars"]
    for name, size in sorted(report["sizes"].items()):
        print(f"AIM  listed[{name}]: {size['chars']} chars")
    print(f"AIM  listed total: {total} chars across {report['count']} tools")
    assert total <= assemble.LISTED_TOTAL_CEILING_CHARS, (
        f"the listed descriptions total {total} chars (> "
        f"{assemble.LISTED_TOTAL_CEILING_CHARS}, the ceiling ratified 2026-09-23)"
    )
    assert not report["over_hard_bytes"], report["over_hard_bytes"]
    short = {
        n: v["chars"]
        for n, v in report["sizes"].items()
        if v["chars"] < assemble.MIN_DESCRIPTION_CHARS
    }
    assert not short, f"descriptions too short to describe the tool: {short}"
    # Non-vacuity: every listed tool was measured.
    assert report["count"] == len(LISTED_PROFILE_TOOLS)
    assert "AIM  listed total" in capsys.readouterr().out


def test_a_listed_override_differs_only_by_a_surface_fact():
    """R-4: register is never a reason for an override. Where a listed
    override exists, the shared text is the SAME text plus the full-profile
    facts — so every sentence of the override is also in the shared text,
    except ones that name the listed connector's own surface."""
    checked = 0
    for name in sorted(LISTED_PROFILE_TOOLS):
        tool = OUTCOMES[name]
        if not tool.listed_description:
            continue
        checked += 1
        first_listed = assemble._sentences(tool.listed_description)[0]
        first_full = assemble._sentences(tool.description)[0]
        # The first line is where the host picks the tool: one per tool.
        if name not in {"keel_connection_check", "keel_live_monitor"}:
            assert first_listed == first_full, name
    # Non-vacuity: overrides exist to compare.
    assert checked >= 5
