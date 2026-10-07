"""Convention gate: every outcome tool's description is knowledge-grounded.

The tool-surface audit (2026-07-20) leveled all outcome tool descriptions to
the corpus (`libs/pipeline_engine/reference/system/*.md`) standard, each
carrying an auditable ``# grounded-in: <file>:<lines>`` comment. This gate
keeps the surface from drifting back into ungrounded one-offs: a NEW outcome
tool (or one whose description is rewritten) must cite the corpus guidance it
distills, or add an explicit waiver here with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_OUTCOMES_DIR = Path(__file__).resolve().parent.parent / "keel" / "tools" / "outcomes"

# Modules that are not tools (helpers/shared infra) — not description-bearing.
_NON_TOOL = {"__init__"}

# Explicit, reasoned waivers (keep empty unless a tool genuinely has no
# behavioral discipline to cite — and say why).
_WAIVERS: dict[str, str] = {}


def _tool_modules() -> list[Path]:
    return [
        p
        for p in sorted(_OUTCOMES_DIR.glob("*.py"))
        if not p.stem.startswith("_") and p.stem not in _NON_TOOL
    ]


def test_every_tool_description_is_grounded_in_the_corpus():
    missing = [
        p.name
        for p in _tool_modules()
        if p.stem not in _WAIVERS and "grounded-in:" not in p.read_text()
    ]
    assert not missing, (
        "These outcome tools lack a `# grounded-in:` corpus citation — the "
        "tool-surface audit convention. Cite the "
        "libs/pipeline_engine/reference/system/*.md guidance the description "
        f"distills, or add a reasoned waiver: {sorted(missing)}"
    )


# ─── The citations resolve (mcp-conversion 05 §3, lane B2c) ─────────────
#
# The layer split moved corpus files under `system/chat/` (in-app only) and
# renamed `costs_and_fees` → `backtest_costs`, which left `# grounded-in:`
# comments citing files and line ranges that no longer existed. A citation
# names its path relative to `libs/pipeline_engine/reference/`; a bare corpus
# filename is ambiguous (the shared and chat trees share names) and a line
# past the end of the file cites nothing.

_CITATION_RE = re.compile(r"(?<![\w/.-])([\w/-]+\.md)(?::(\d+)(?:-(\d+))?)?")


def _grounding_blocks(text: str) -> list[str]:
    """Each `# grounded-in:` comment block, continuation comment lines included."""
    blocks: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "# grounded-in:" not in line:
            continue
        block = [line]
        for nxt in lines[i + 1 :]:
            if not nxt.strip().startswith("#"):
                break
            block.append(nxt)
        blocks.append("\n".join(block))
    return blocks


def _citation_violations(block: str, reference_root: Path, corpus_names: set[str]) -> list[str]:
    out: list[str] = []
    for m in _CITATION_RE.finditer(block):
        path, first, last = m.group(1), m.group(2), m.group(3)
        if "/" not in path:
            if path in corpus_names:
                out.append(f"{path}: cite the corpus file with its directory")
            continue
        if not path.startswith(("system/", "patterns/")):
            continue  # a spec or code path, not a corpus citation
        target = reference_root / path
        if not target.is_file():
            out.append(f"{path}: no such corpus file")
            continue
        n_lines = len(target.read_text(encoding="utf-8").splitlines())
        for bound in (first, last):
            if bound is not None and int(bound) > n_lines:
                out.append(f"{m.group(0)}: past the end of the file ({n_lines} lines)")
    return out


def test_grounding_citations_resolve_to_the_current_corpus():
    """# SEED (run 2026-09-28): set backtest_run.py's citation back to
    `costs_and_fees.md:53-61` — reds naming that file, while the control
    arms below stay green. Reverted by reversing the edit."""
    reference_root = Path(__file__).resolve().parents[4] / "libs" / "pipeline_engine" / "reference"
    if not (reference_root / "system").is_dir():
        pytest.skip("reads libs/pipeline_engine/reference (monorepo only)")
    corpus_names = {p.name for p in reference_root.rglob("*.md")}

    # Control arms: a stale citation of each shape reds; a current one does not.
    for stale in (
        "# grounded-in: costs_and_fees.md:53-61",
        "# grounded-in: system/costs_and_fees.md",
        "# grounded-in: system/backtest_costs.md:9-900",
    ):
        assert _citation_violations(stale, reference_root, corpus_names), stale
    current = "# grounded-in: system/backtest_costs.md:9-18 + specs/03-golive.md"
    assert not _citation_violations(current, reference_root, corpus_names)

    violations: list[str] = []
    cited = 0
    for p in _tool_modules():
        for block in _grounding_blocks(p.read_text(encoding="utf-8")):
            cited += len(_CITATION_RE.findall(block))
            violations += [
                f"{p.name}: {v}" for v in _citation_violations(block, reference_root, corpus_names)
            ]
    assert not violations, "stale `# grounded-in:` citations:\n" + "\n".join(violations)
    # Non-vacuity: the tool modules cite the corpus many times over.
    assert cited >= 30, cited
