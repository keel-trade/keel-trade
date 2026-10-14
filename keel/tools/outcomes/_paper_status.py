"""A paper run's status in keel-app's words (S05 §3.4.3's badge table; QA-W2).

keel-api serves a paper run's ``status`` in the deployment vocabulary, so a
running paper run reads ``"LIVE"``, which a reader takes for real capital.
keel-app never shows it: ``paperHealthBadge``
(``services/keel-app/src/lib/status-badge.ts``, the single edit point) maps the
row's ``status`` (refined by ``health`` on ``LIVE`` and ``pause_reason`` on
``PAUSED``) to Deploying / Paper / Paused / Stopped. Every paper output the SDK
renders carries the same word in place of the raw value, through
:func:`paper_status_word`; ``tests/test_paper_agent_outputs.py`` reads the app's
function so the two cannot drift.

One-word data labels, not prose, so they live here rather than in
``_paper_strings`` (whose rows are sentences: its sourcing probes need 12
characters; ``deployments._ROW_LABELS`` is the precedent). A listed form
differs only where the app's word is not listed-safe: "Deploying" carries a
banned listed stem, so the listed connector says "Starting".

An unknown status is never echoed: keel-app renders "Unknown" so a server
value cannot put an unvetted word (or "Live") on a paper surface.
"""

from __future__ import annotations

from typing import Final

from ._toolsets import is_listed_profile


__all__ = ["PAPER_STATUS_LISTED", "PAPER_STATUS_WORDS", "paper_status_word"]


#: keel-app's ``paperHealthBadge`` branches → its label. ``STATUS`` is the
#: plain branch, ``STATUS:refinement`` a ``health`` (LIVE) or ``pause_reason``
#: (PAUSED) branch, ``default`` a status the app does not know.
PAPER_STATUS_WORDS: Final[dict[str, str]] = {
    "DEPLOYING": "Deploying",
    "LIVE": "Paper",
    "LIVE:degraded": "Paper · degraded",
    "LIVE:stalled": "Paper · stalled",
    "PAUSED": "Paused",
    "PAUSED:over_cap": "Paused · plan limit",
    "PAUSED:idle": "Paused · idle",
    "STOPPED": "Stopped",
    "default": "Unknown",
}

#: The listed connector's word where the app's is not listed-safe.
PAPER_STATUS_LISTED: Final[dict[str, str]] = {"DEPLOYING": "Starting"}


def paper_status_word(status: object, *, health: object = None, pause_reason: object = None) -> str:
    """A paper run's status in keel-app's words (``paperHealthBadge``'s label).

    ``status`` drives (any case, as the app normalises it); ``health`` refines
    ``LIVE`` only; ``pause_reason`` refines ``PAUSED`` only. A status the map
    lacks reads "Unknown", never the server's value. The profile is read at
    call time.
    """
    key = str(status or "").upper()
    refinement = health if key == "LIVE" else pause_reason if key == "PAUSED" else None
    branch = f"{key}:{refinement}" if f"{key}:{refinement}" in PAPER_STATUS_WORDS else key
    if branch not in PAPER_STATUS_WORDS:
        branch = "default"
    if is_listed_profile() and branch in PAPER_STATUS_LISTED:
        return PAPER_STATUS_LISTED[branch]
    return PAPER_STATUS_WORDS[branch]
