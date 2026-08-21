"""Convention gate: every outcome tool's description is knowledge-grounded.

The tool-surface audit (2026-07-20) leveled all outcome tool descriptions to
the corpus (`libs/pipeline_engine/reference/system/*.md`) standard, each
carrying an auditable ``# grounded-in: <file>:<lines>`` comment. This gate
keeps the surface from drifting back into ungrounded one-offs: a NEW outcome
tool (or one whose description is rewritten) must cite the corpus guidance it
distills, or add an explicit waiver here with a reason.
"""

from __future__ import annotations

from pathlib import Path


_OUTCOMES_DIR = (
    Path(__file__).resolve().parent.parent / "keel" / "tools" / "outcomes"
)

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
