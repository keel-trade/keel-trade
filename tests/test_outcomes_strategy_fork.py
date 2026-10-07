"""The fork-of-own-strategy note (spec 03 §2.9, R-2 — guard 17).

`fork_note` and its `next` line are present EXACTLY when the caller's org
owns the source strategy (`source_org_id == org_id` on keel-api's fork
response). Another org's PUBLIC strategy forked by `str_` id and a
share-link fork carry neither — the id shape proves nothing about ownership.

SEED (run 2026-09-23, reverted by reversing the edit): in
`strategy_fork.fork_note`, return a note whenever `source` starts with
`str_` (drop the org comparison) — `test_another_orgs_public_strategy_carries_no_note`
reds while the own-org arm stays green.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import strategy_fork as _fork_mod  # noqa: F401 — registers
from keel.tools.outcomes._base import ToolContext


def _fork(source: str, response: dict) -> dict:
    client = MagicMock()
    client.post.return_value = response
    client.get.side_effect = Exception("no read-back in this test")
    ctx = ToolContext(api_client=client, is_tty=False)
    return OUTCOMES["keel_strategy_fork"].handler({"source": source}, ctx).to_envelope()


ARMS = [
    # (source, response, expects a note)
    ("str_mine", {"strategy_id": "str_new1", "org_id": "org_a", "source_org_id": "org_a"}, True),
    ("str_theirs", {"strategy_id": "str_new2", "org_id": "org_a", "source_org_id": "org_b"}, False),
    ("shr_link", {"strategy_id": "str_new3", "org_id": "org_a", "source_org_id": "org_a"}, False),
]


def test_the_arms_name_exactly_two_orgs():
    """Non-vacuity on the INPUTS: the three fixtures name two distinct
    source orgs, so the arms really differ in ownership."""
    assert len({resp["source_org_id"] for _, resp, _ in ARMS}) == 2


@pytest.mark.parametrize("source,response,expects", ARMS)
def test_the_note_follows_ownership(source, response, expects):
    env = _fork(source, response)
    assert ("fork_note" in env) is expects
    assert ("next" in env) is expects


def test_an_own_org_fork_names_the_version_path():
    env = _fork(ARMS[0][0], ARMS[0][1])
    # No always-null `parent_name`: keel-api's fork response carries no
    # source name (review 2 #4).
    assert env["fork_note"] == {"parent_strategy_id": "str_mine"}
    assert env["next"] == (
        'This is your own strategy — a new version is keel_strategy_compose(strategy_id="str_mine"); '
        "this fork is a separate copy. A variant is a version, not a fork."
    )


def test_another_orgs_public_strategy_carries_no_note():
    env = _fork(ARMS[1][0], ARMS[1][1])
    assert "fork_note" not in env


def test_an_older_keel_api_without_the_field_carries_no_note():
    env = _fork("str_mine", {"strategy_id": "str_new", "org_id": "org_a"})
    assert "fork_note" not in env
