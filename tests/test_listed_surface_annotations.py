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


# Destructive tools deliberately permitted on the listed surface.
# "Destructive" in MCP means irreversible, NOT money-moving — the packet's
# §3.3 attestation is specifically that the connector cannot transfer
# assets or execute financial transactions, which none of these do.
#
# keel_share_create: publishes a public share URL. Irreversible in the
#   sense that the content was exposed, so destructiveHint=True is the
#   honest annotation (it earns a host confirmation prompt, which is the
#   desired behavior). It moves no money and places no orders.
#
# Anything NOT in this set is a review event — see the test below.
ALLOWED_DESTRUCTIVE_ON_LISTED = frozenset({"keel_share_create"})


def test_listed_destructive_tools_are_the_acknowledged_set(listed_profile):
    """The listed surface is research/build/read only. Exactly one tool
    is destructive-by-annotation and it is deliberate; a NEW destructive
    tool arriving on the reviewed surface must show up in this file's
    diff rather than in a rejection email.
    """
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
