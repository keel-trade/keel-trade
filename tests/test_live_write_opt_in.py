"""The live-write opt-in hint keeps the default set (Q-3095).

``KEEL_TOOLSETS`` replaces the default toolsets, so a hint that types the set
drops whatever later joins the default: once PT2-91 put ``paper`` in the
default set, every hint that typed the pre-paper set unloaded the paper tools
for whoever followed it. ``_toolsets.live_write_opt_in()`` is the one owner;
this file proves it keeps every default toolset, that the served status hint
reads it, and that each hand-written or generated copy matches it.

Seed: drop ``PAPER_TOOLSET`` from ``TOOLSET_ORDER`` ⇒ ``live_write_opt_in``
raises and every arm here reds; type the old set back into
``docs/agent-setup.md`` ⇒ only ``test_every_copy_matches_the_owner`` reds
on that file. Count: the copies list is non-empty and each file exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from keel.tools.outcomes._toolsets import (
    default_toolsets,
    live_write_opt_in,
    load_toolsets,
)


SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parents[2]
#: Every non-corpus copy of the opt-in value (the operating core's corpus line
#: changes with the next chat bundle cut, Q-3095).
COPIES = (
    REPO_ROOT / "packages/keel-trade/docs/agent-setup.md",
    REPO_ROOT / "packages/keel-trade/docs/tool-reference.md",
    REPO_ROOT / "services/keel-site/content/docs/sdk/cli-reference.mdx",
    REPO_ROOT / "services/keel-site/content/docs/sdk/tool-reference.mdx",
    SDK_ROOT / "scripts/check_surface_routing.py",
)


def test_the_opt_in_keeps_every_default_toolset_and_adds_live_write(monkeypatch):
    value = live_write_opt_in()
    assert value.startswith("KEEL_TOOLSETS=")
    named = value.removeprefix("KEEL_TOOLSETS=").split(",")
    assert set(named) == set(default_toolsets()) | {"live-write"}
    assert "paper" in named
    # Following the hint loads a superset of the default set.
    monkeypatch.setenv("KEEL_TOOLSETS", value.removeprefix("KEEL_TOOLSETS="))
    assert default_toolsets() | {"live-write"} <= load_toolsets()


def test_the_status_live_route_prints_the_owner():
    from keel.tools.outcomes import status

    assert "live_write_opt_in()" in Path(status.__file__).read_text()
    assert "share,live-read,live-write" not in Path(status.__file__).read_text()


@pytest.mark.parametrize("path", COPIES, ids=lambda p: p.name)
def test_every_copy_matches_the_owner(path):
    if not path.exists():  # pragma: no cover - the SDK published alone
        pytest.skip("monorepo only")
    text = path.read_text()
    value = live_write_opt_in()
    assert value in text, f"{path.name} lacks {value}"
    # The typed pre-paper set ("…,share,live-read,live-write"); the `live`
    # alias sentence ("an alias for `live-read,live-write`") is not a set.
    assert "share,live-read,live-write" not in text.replace(value, ""), (
        f"{path.name} still types the pre-paper set"
    )
