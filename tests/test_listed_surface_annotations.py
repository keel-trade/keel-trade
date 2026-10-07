"""Listed-surface annotation gate — the exact tool surface a directory
reviewer sees, annotated correctly, proven in one place.

`test_tool_annotations.py` proves every tool in ``OUTCOMES`` carries a
correct ``title``/``readOnlyHint``/``destructiveHint``.
`test_server_profiles.py` proves ``effective_annotations()`` swaps in
``listed_title`` under the listed profile. Neither asserts the
CONJUNCTION: that every tool ON THE LISTED SURFACE, rendered through its
listed copy, still carries a complete and correct annotation set.

That conjunction is what the Anthropic reviewer actually inspects
(annotations are a top-two rejection cause), and it was previously true
only by composition of two files. A `listed_title` override that dropped
or contradicted a hint would pass both existing suites.

The listed profile is also where the safety stakes are highest: the copy
is deliberately reworded to read as read-only/research, so a hint that
silently disagreed with that copy is exactly the drift worth failing on.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._mcp_adapter import effective_annotations
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS, SERVER_PROFILE_ENV


_bootstrap()

# The safety keys a listed_title override must never touch. `title` is
# deliberately excluded — rewriting it is the whole point of the listed
# profile. Anything else differing means policy copy changed behavior.
SAFETY_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


@pytest.fixture
def listed_profile():
    """Run the body under KEEL_SERVER_PROFILE=listed.

    `effective_annotations()` reads the profile from the environment via
    `is_listed_profile()`, so the profile must be active while the
    annotations are rendered — not merely at import time.
    """
    with mock.patch.dict(os.environ, {SERVER_PROFILE_ENV: "listed"}):
        yield


def test_listed_surface_is_non_empty_and_a_subset_of_the_catalog():
    """Guards the whole file: a vacuous or drifted allow-list would make
    every parametrized case below pass by doing nothing."""
    assert LISTED_PROFILE_TOOLS, "listed surface is empty — the sweep would be vacuous"
    unknown = set(LISTED_PROFILE_TOOLS) - set(OUTCOMES)
    assert not unknown, f"listed allow-list names tools absent from the catalog: {sorted(unknown)}"


@pytest.mark.parametrize("name", sorted(LISTED_PROFILE_TOOLS))
def test_listed_annotations_complete_and_unchanged_in_safety(name, listed_profile):
    """Listed copy may reword the title; it must never alter the hints."""
    tool = OUTCOMES[name]
    annotations = effective_annotations(tool)

    title = annotations.get("title")
    assert isinstance(title, str) and title.strip(), f"{name}: missing title on listed surface"
    assert len(title) <= 60, f"{name}: listed title too long for host UIs: {title!r}"
    assert title != name, f"{name}: listed title must be human-readable, not the tool name"

    for hint in ("readOnlyHint", "destructiveHint"):
        assert isinstance(annotations.get(hint), bool), (
            f"{name}: {hint} missing or non-bool on the listed surface"
        )
    assert not (annotations["readOnlyHint"] and annotations["destructiveHint"]), (
        f"{name}: readOnlyHint and destructiveHint are mutually exclusive"
    )

    # THE INVARIANT: policy-vetted listed copy may rewrite the title and
    # nothing else. The absolute classification is gated once, at the
    # catalog level, by test_tool_annotations.py; asserting equality here
    # keeps that single source of truth instead of copying the table.
    base = tool.annotations
    for key in SAFETY_KEYS:
        assert annotations.get(key) == base.get(key), (
            f"{name}: listed copy changed {key} "
            f"({base.get(key)!r} → {annotations.get(key)!r}) — "
            "listed_title may rewrite the title only, never the safety class"
        )
    assert set(annotations) == set(base), (
        f"{name}: listed annotations changed the key set ({sorted(set(base) ^ set(annotations))})"
    )


def test_listed_surface_carries_no_live_write_tools(listed_profile):
    """The directory listing attests that the connector cannot move money
    or place orders (packet §3.3). live-write is what would falsify that.
    """
    forbidden = {"keel_live_deploy", "keel_live_control"}
    present = forbidden & set(LISTED_PROFILE_TOOLS)
    assert not present, f"live-write tools must never reach the listed surface: {sorted(present)}"


# Destructive tools deliberately permitted on the listed surface — the
# IRREVERSIBLE ones under the append-only rule (the policy in
# keel/tools/outcomes/_annotation_justifications.py, Q-2080, 2026-10-01):
# a public disclosure cannot be retracted from recipients, and a note sent
# to the Keel team cannot be recalled. Neither moves money — the packet's
# §3.3 attestation (no asset transfers, no financial transactions) is a
# separate fact. Q-1506 had emptied this set so a share would not prompt on
# every call; OpenAI's review holds destructiveHint to irreversible OUTCOMES,
# so the prompt is the price (reversed 2026-10-01).
#
# Anything else appearing on the listed surface with destructiveHint=True is
# a review event — see the test below.
ALLOWED_DESTRUCTIVE_ON_LISTED: frozenset[str] = frozenset({"keel_share_create", "keel_feedback"})


def test_listed_destructive_tools_are_the_acknowledged_set(listed_profile):
    """The listed surface is research/build/read only. Exactly two tools
    are destructive-by-annotation and both are deliberate; a NEW destructive
    tool arriving on the reviewed surface must show up in this file's
    diff rather than in a rejection email.
    """
    from keel.tools.outcomes._annotation_justifications import LISTED_DESTRUCTIVE_TOOLS

    assert LISTED_DESTRUCTIVE_TOOLS == ALLOWED_DESTRUCTIVE_ON_LISTED  # one truth
    destructive = {
        name
        for name in LISTED_PROFILE_TOOLS
        if effective_annotations(OUTCOMES[name])["destructiveHint"]
    }
    unexpected = destructive - ALLOWED_DESTRUCTIVE_ON_LISTED
    assert not unexpected, (
        "new destructive tool(s) on the directory-reviewed listed surface — "
        "confirm they move no money and acknowledge them in "
        f"ALLOWED_DESTRUCTIVE_ON_LISTED: {sorted(unexpected)}"
    )

    stale = ALLOWED_DESTRUCTIVE_ON_LISTED - destructive
    assert not stale, (
        f"ALLOWED_DESTRUCTIVE_ON_LISTED lists tools that are no longer "
        f"destructive-on-listed — remove them: {sorted(stale)}"
    )


def test_listed_titles_are_unique(listed_profile):
    """Duplicate titles confuse host tool pickers — and the listed
    profile is precisely where titles get rewritten, so collisions are
    likelier here than on the full surface."""
    titles = [effective_annotations(OUTCOMES[n])["title"] for n in LISTED_PROFILE_TOOLS]
    dupes = sorted({t for t in titles if titles.count(t) > 1})
    assert not dupes, f"duplicate listed titles: {dupes}"


def test_listed_annotations_construct_valid_mcp_objects(listed_profile):
    """The effective annotations must build the exact object the MCP
    adapter publishes to tools/list — not merely a well-shaped dict."""
    from mcp.types import ToolAnnotations

    for name in sorted(LISTED_PROFILE_TOOLS):
        ToolAnnotations(**effective_annotations(OUTCOMES[name]))


# ── openWorldHint + written grounds for every hint (OpenAI O-3) ─────────
# The rule (founder-approved 2026-09-25, widened 2026-10-01 under Q-2080):
# openWorldHint is True only for a tool that reaches the public internet, an
# independently controlled external system or an open-ended set of entities,
# or changes publicly visible state; a tool bounded to the caller's own Keel
# account/workspace is False. The reasons live in ONE place in code —
# keel/tools/outcomes/_annotation_justifications.py — and never go over
# the wire.
#
# SEED A: flip keel_strategy_get's openWorldHint back to True in
#   strategy_get.py — test_listed_open_world_set_is_exact reds naming it.
# SEED B: blank one field of any LISTED_ANNOTATION_JUSTIFICATIONS row —
#   test_every_listed_hint_carries_a_written_justification reds naming it.
# SEED C (run 2026-10-01): flip keel_live_monitor's openWorldHint back to
#   False in live_monitor.py — test_listed_open_world_set_is_exact reds
#   naming it as missing.

EXPECTED_LISTED_COUNT = 29
EXPECTED_OPEN_WORLD_ON_LISTED: frozenset[str] = frozenset(
    {"keel_share_create", "keel_feedback", "keel_live_monitor", "keel_strategy_fork"}
)


def test_listed_surface_count_is_pinned():
    """Not vacuous: the sweeps below cover exactly the 29 listed tools."""
    assert len(LISTED_PROFILE_TOOLS) == EXPECTED_LISTED_COUNT


def test_listed_open_world_set_is_exact(listed_profile):
    """Every listed tool states openWorldHint explicitly, and exactly the
    expected set is True — a public-state tool must be deliberate, and a
    workspace-bounded one must not claim the open world."""
    from keel.tools.outcomes._annotation_justifications import LISTED_OPEN_WORLD_TOOLS

    missing = sorted(
        n
        for n in LISTED_PROFILE_TOOLS
        if not isinstance(effective_annotations(OUTCOMES[n]).get("openWorldHint"), bool)
    )
    assert not missing, f"listed tools without an explicit openWorldHint: {missing}"

    open_world = {
        n for n in LISTED_PROFILE_TOOLS if effective_annotations(OUTCOMES[n])["openWorldHint"]
    }
    assert open_world == EXPECTED_OPEN_WORLD_ON_LISTED, (
        f"openWorldHint=True on the listed surface drifted: "
        f"unexpected {sorted(open_world - EXPECTED_OPEN_WORLD_ON_LISTED)}, "
        f"missing {sorted(EXPECTED_OPEN_WORLD_ON_LISTED - open_world)}"
    )
    # The code-side declaration agrees with this pin (one truth).
    assert LISTED_OPEN_WORLD_TOOLS == EXPECTED_OPEN_WORLD_ON_LISTED


def test_every_listed_hint_carries_a_written_justification():
    """OpenAI's submission asks for a justification for each value: every
    listed tool has a non-empty reason for readOnly, destructive and
    openWorld, and the justification table covers exactly the listed set."""
    from keel.tools.outcomes._annotation_justifications import (
        LISTED_ANNOTATION_JUSTIFICATIONS,
    )

    assert set(LISTED_ANNOTATION_JUSTIFICATIONS) == set(LISTED_PROFILE_TOOLS), (
        f"justification table drifted from the listed surface: "
        f"unjustified {sorted(set(LISTED_PROFILE_TOOLS) - set(LISTED_ANNOTATION_JUSTIFICATIONS))}, "
        f"stale {sorted(set(LISTED_ANNOTATION_JUSTIFICATIONS) - set(LISTED_PROFILE_TOOLS))}"
    )
    blank = sorted(
        f"{name}.{field}"
        for name, row in LISTED_ANNOTATION_JUSTIFICATIONS.items()
        for field, text in row._asdict().items()
        if not (isinstance(text, str) and text.strip())
    )
    assert not blank, f"listed hints with no written justification: {blank}"
    assert len(LISTED_ANNOTATION_JUSTIFICATIONS) == EXPECTED_LISTED_COUNT


def test_justifications_never_reach_the_wire(listed_profile):
    """The grounds are review material only: no annotation dict carries
    them, and no listed description quotes one."""
    from keel.tools.outcomes._annotation_justifications import (
        LISTED_ANNOTATION_JUSTIFICATIONS,
    )

    for name in sorted(LISTED_PROFILE_TOOLS):
        annotations = effective_annotations(OUTCOMES[name])
        assert set(annotations) <= {"title", *SAFETY_KEYS}, (
            f"{name}: unexpected annotation keys {sorted(set(annotations) - {'title', *SAFETY_KEYS})}"
        )
        tool = OUTCOMES[name]
        description = tool.listed_description or tool.description
        # Blank rows are the justification test's finding, not a leak.
        for text in filter(str.strip, LISTED_ANNOTATION_JUSTIFICATIONS[name]):
            assert text not in description, f"{name}: a justification leaked into the description"


# ── The append-only rule (Q-2080, 2026-10-01) ────────────────────────────
# A listed write tool is destructiveHint=False ONLY if it is append-only
# (every call adds a record/version, nothing edited in place or deleted),
# and every such tool is named in `APPEND_ONLY` with its reason. The guard
# holds the set and the hints in step both ways.
#
# SEED D (run 2026-10-01): in strategy_restore.py set destructiveHint back to
#   True — test_append_only_set_is_exactly_the_non_destructive_writes reds
#   naming keel_strategy_restore as "in APPEND_ONLY but destructive".
# SEED E (run 2026-10-01): delete the keel_backtest_run row from APPEND_ONLY
#   — the same test reds naming it as "non-destructive write not in
#   APPEND_ONLY", while the control arm (every read tool absent) stays green.


def _listed_write_tools() -> set[str]:
    return {n for n in LISTED_PROFILE_TOOLS if not OUTCOMES[n].annotations["readOnlyHint"]}


def test_append_only_set_is_exactly_the_non_destructive_writes(listed_profile):
    from keel.tools.outcomes._annotation_justifications import (
        APPEND_ONLY,
        LISTED_DESTRUCTIVE_TOOLS,
    )

    writes = _listed_write_tools()
    non_destructive = {
        n for n in writes if not effective_annotations(OUTCOMES[n])["destructiveHint"]
    }
    destructive = writes - non_destructive
    assert set(APPEND_ONLY) == non_destructive, (
        f"non-destructive write not in APPEND_ONLY: {sorted(non_destructive - set(APPEND_ONLY))}; "
        f"in APPEND_ONLY but destructive or not a write: {sorted(set(APPEND_ONLY) - non_destructive)}"
    )
    assert destructive == LISTED_DESTRUCTIVE_TOOLS, sorted(destructive ^ LISTED_DESTRUCTIVE_TOOLS)
    # Control arm: no read-only tool is in the set (a read writes nothing to
    # append), and the set reasons are non-empty sentences.
    assert not (set(APPEND_ONLY) & {n for n in LISTED_PROFILE_TOOLS if n not in writes})
    blank = [n for n, why in APPEND_ONLY.items() if not why.strip().endswith(".")]
    assert not blank, f"append-only entries without a one-line reason: {blank}"
    # Non-vacuity, on quantities the seeds cannot move: the listed surface
    # carries writes of both classes.
    assert len(writes) == 8, sorted(writes)
    assert len(APPEND_ONLY) >= 5 and len(LISTED_DESTRUCTIVE_TOOLS) == 2


def test_append_only_reasons_describe_adding_not_replacing():
    """Each APPEND_ONLY reason states what is ADDED and never claims an undo:
    "it can be undone" is not a reason under OpenAI's rule."""
    from keel.tools.outcomes._annotation_justifications import APPEND_ONLY

    for name, why in APPEND_ONLY.items():
        low = why.lower()
        assert any(w in low for w in ("new", "append", "adds", "creates")), (name, why)
        assert "undo" not in low and "revers" not in low, (name, why)
