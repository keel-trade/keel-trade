"""The paper page's Claude section (S10 §9.4, PT2-90), masked for the docs guards.

When this landed, the paper tools (``keel_paper_deploy``, ``keel_paper_control``)
were held from the SDK wheel and served only by the hosted connector to the
hosts ``KEEL_LISTED_PAPER_HOSTS`` names, so the docs could name them in exactly
one section, "## Paper trading in Claude", of the paper page and its
raw-markdown mirror. PT2-91 (2026-10-14) released the paper SDK: both tools are
in the registry, so ``check_agent_surface_docs.py`` admits them, and the CLI
forms (``keel paper deploy|control``), on every page. The mask stays as the
section's reader (``mask_claude_section`` blanks the two names inside it only),
which no longer changes a verdict.
"""

from __future__ import annotations

import re


#: Repo-relative paths of the two files that carry the section.
CLAUDE_SECTION_FILES: tuple[str, ...] = (
    "services/keel-site/content/docs/paper-trading/index.mdx",
    "services/keel-site/public/docs/paper-trading.md",
)
CLAUDE_SECTION_HEADING = "## Paper trading in Claude"
#: The names admitted inside the section: the two MCP tool names, exactly.
CLAUDE_SECTION_ALLOWED_RE = re.compile(r"\bkeel_paper_(?:deploy|control)\b")


def claude_section_lines(rel: str, text: str) -> set[int]:
    """1-based line numbers of ``text`` that sit inside the Claude section
    (heading line included), or an empty set for any other file."""
    if rel not in CLAUDE_SECTION_FILES:
        return set()
    inside, lines = False, set()
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            inside = line.strip() == CLAUDE_SECTION_HEADING
        if inside:
            lines.add(n)
    return lines


def mask_claude_section(rel: str, text: str) -> str:
    """``text`` with the two MCP tool names blanked inside the Claude section
    only (line count preserved); every other byte is unchanged."""
    section = claude_section_lines(rel, text)
    if not section:
        return text
    out = []
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        out.append(CLAUDE_SECTION_ALLOWED_RE.sub("paper-tool", line) if n in section else line)
    return "".join(out)
