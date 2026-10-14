"""The CLI and MCP reference rows for paper ship with the wheel (paper-trading-v2
S07 AC-07-11; S07 §3.16.3, §3.16.4, §3.21; S06 §3.9 row 10; PT2-28, PT2-91).

The wheel's follow-up released the paper SDK (``PAPER_SDK_RELEASED = True``)
and wrote the docs that describe it. This file holds them to the registry:

* the generated ``tool-reference.md`` and ``sdk/tool-reference.mdx`` carry one
  row per paper tool, named exactly as the registry names it, with ``—`` in
  the Hosted column (the default listed surface is HEAD's; the Claude host
  switch is the connector's own page, PT2-90) and ``simulated`` in its
  description;
* ``sdk/cli-reference.mdx`` carries every command line of ``cli-reference.md``'s
  ``## Paper`` block verbatim, ``keel deployments list --capital-class
  practice`` included;
* no file carries a ``paper list`` / ``paper monitor`` row (reads go through
  ``keel live monitor`` and ``keel deployments list``);
* the three follow-up pages of §3.16.4 and the two package docs of §3.16.3
  each carry their paper addition.

Seed (AC-07-11): rename ``keel_paper_control`` in the public mirror
(``sdk/tool-reference.mdx``) ⇒ ``test_tool_reference_rows_match_the_registry``
reds on that file. Count: each of the four §3.16.3 reference files and the
three §3.16.4 pages exists and is non-empty, and the registry list compared
against holds both paper tool names. Monorepo only, like
``tests/test_surface_routing.py``: the site files live outside the SDK.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap


SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parents[2]
PKG_DOCS = REPO_ROOT / "packages" / "keel-trade" / "docs"
SITE_DOCS = REPO_ROOT / "services" / "keel-site" / "content" / "docs"

TOOL_REFERENCES = (
    PKG_DOCS / "tool-reference.md",
    SITE_DOCS / "sdk" / "tool-reference.mdx",
)
CLI_REFERENCE = PKG_DOCS / "cli-reference.md"
CLI_MIRROR = SITE_DOCS / "sdk" / "cli-reference.mdx"
#: §3.16.3's four reference files.
REFERENCE_FILES = (*TOOL_REFERENCES, CLI_REFERENCE, CLI_MIRROR)

#: §3.16.4's follow-up pages and §3.16.3's package docs, each with a phrase
#: only its paper addition carries.
PAPER_ADDITIONS = {
    SITE_DOCS / "getting-started" / "index.mdx": (
        "## Paper first",
        "Paper trading needs no live-trading scope",
    ),
    SITE_DOCS / "getting-started" / "first-backtest-cli.mdx": ('keel paper deploy "$STRATEGY_ID"',),
    SITE_DOCS / "sdk" / "index.mdx": ("## Paper trade", "keel paper deploy str_abc123"),
    PKG_DOCS / "agent-setup.md": (
        "Paper runs complete in band; live deploys hand off to the web app.",
    ),
    PKG_DOCS / "getting-started.md": (
        "Paper runs complete in band; live deploys hand off to the web app.",
    ),
    SITE_DOCS / "paper-trading" / "index.mdx": (
        "## Through agents and the CLI",
        "/docs/sdk/cli-reference",
        "/docs/sdk/tool-reference",
    ),
}

if not SITE_DOCS.is_dir():  # pragma: no cover - the SDK published alone
    pytest.skip("monorepo only: the keel-site docs are not present", allow_module_level=True)


def _paper_tools() -> list[str]:
    _bootstrap()
    names = sorted(name for name in OUTCOMES if name.startswith("keel_paper_"))
    assert names == ["keel_paper_control", "keel_paper_deploy"], names
    return names


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    marker = "\n" + heading.split(" ", 1)[0] + " "
    nxt = text.find(marker, start + len(heading))
    return text[start : nxt if nxt != -1 else len(text)]


def _command_lines(block: str) -> list[str]:
    lines: list[str] = []
    for fence in re.findall(r"```bash\n(.*?)```", block, flags=re.DOTALL):
        lines.extend(line.split("#", 1)[0].strip() for line in fence.splitlines())
    return [line for line in lines if line]


def test_every_reference_file_exists_and_is_non_empty():
    for path in (*REFERENCE_FILES, *PAPER_ADDITIONS):
        assert path.is_file(), path
        assert path.read_text().strip(), path


@pytest.mark.parametrize("path", TOOL_REFERENCES, ids=lambda p: p.name)
def test_tool_reference_rows_match_the_registry(path):
    text = path.read_text()
    for name in _paper_tools():
        rows = [line for line in text.splitlines() if line.startswith(f"| `{name}` |")]
        assert len(rows) == 1, f"{path.name}: expected one table row for {name}, got {rows}"
        cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
        assert cells[1] == "—", f"{path.name}: {name}'s Hosted column reads {cells[1]!r}"
        lines = [
            line for line in _section(text, "### `paper`").splitlines() if f"`{name}`:" in line
        ]
        assert len(lines) == 1, f"{path.name}: no `paper` toolset description for {name}"
        assert "simulated" in lines[0].lower(), f"{path.name}: {name}'s description: {lines[0]}"


def test_cli_mirror_carries_the_paper_commands_verbatim():
    commands = _command_lines(_section(CLI_REFERENCE.read_text(), "## Paper"))
    assert "keel deployments list --capital-class practice" in commands
    assert any(c.startswith("keel paper deploy ") for c in commands)
    assert any(c.startswith("keel paper control ") for c in commands)
    mirror = set(_command_lines(CLI_MIRROR.read_text()))
    missing = [c for c in commands if c not in mirror]
    assert not missing, f"sdk/cli-reference.mdx lacks {missing}"


def test_no_paper_list_or_monitor_command():
    pattern = re.compile(r"keel[ _]paper[ _](?:list|monitor)\b")
    for path in (*REFERENCE_FILES, *PAPER_ADDITIONS):
        assert not pattern.search(path.read_text()), path


@pytest.mark.parametrize("path", sorted(PAPER_ADDITIONS), ids=lambda p: p.name)
def test_each_page_carries_its_paper_addition(path):
    text = path.read_text()
    for phrase in PAPER_ADDITIONS[path]:
        assert phrase in text, f"{path}: missing {phrase!r}"
