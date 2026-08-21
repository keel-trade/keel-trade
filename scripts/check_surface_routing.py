#!/usr/bin/env python3
"""Surface-routing table consistency gate (spec 07 R7, agent-first-build M6.4).

The canonical routing table lives in ``shared/surface-routing.json``.
Every rendered copy must match it exactly:

* Markdown surfaces carry a generated block between
  ``<!-- surface-routing:begin -->`` / ``<!-- surface-routing:end -->``
  markers — the block must byte-match the canonical render.
* The keel-site TSX surfaces (``/agents`` page + the keel-mcp page)
  must import the canonical JSON (``@shared/surface-routing.json``)
  instead of hand-copying rows — asserted by import grep.

Also enforced here (spec 07 R1/R2 — same one-source rule):

* ``public/.well-known/agents.md`` is byte-identical to
  ``public/AGENTS.md`` (single source, alternate discovery path).
* The generated raw-markdown docs variants (``public/docs/**.md`` +
  ``public/docs.md``) match the deterministic transform of
  ``content/docs/**.mdx`` (see ``generate-docs-md.mjs`` in keel-site).

Run from the repo root:

    python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py
    python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write

``--write`` regenerates the markdown marker blocks in place (it never
touches the TSX surfaces — they import the JSON directly).

CI: tests/test_surface_routing.py wraps this checker, so it runs in the
SDK test lane (sdk-test.yml + graph-selected test.yml targets). This is a
monorepo-only build/test utility — REPO_ROOT walks up from scripts/ into
the larger checkout, so its hardcoded paths only resolve inside the
monorepo. It is bundled into the public mirror as an inert file (matched
by the scripts/*.py allowlist entry, like its build-utility peers); it is
NOT on the sync rm list, so unlike check_agent_surface_docs.py it is not
stripped. Missing paths are reported as errors, not skipped — a renamed
render target must fail loudly.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SDK_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SDK_ROOT.parent.parent.parent

CANONICAL_PATH = REPO_ROOT / "shared" / "surface-routing.json"

BEGIN_MARKER = "<!-- surface-routing:begin -->"
END_MARKER = "<!-- surface-routing:end -->"

# Markdown render targets — each must contain exactly one marker block.
MARKDOWN_TARGETS = [
    "services/keel-site/public/AGENTS.md",
    "services/keel-site/public/.well-known/agents.md",
    "packages/keel-trade/public-overlay/README.md",
    "packages/keel-trade/keel-sdk/AGENTS.md",
]

# TSX render targets — each must import the canonical JSON.
TSX_TARGETS = [
    "services/keel-site/src/app/agents/page.tsx",
    "services/keel-site/src/app/keel-mcp/page.tsx",
]
TSX_IMPORT_RE = re.compile(r"""from\s+["']@shared/surface-routing\.json["']""")

# Single-source rule for the agents instruction files (spec 07 R1).
AGENTS_MD = "services/keel-site/public/AGENTS.md"
WELL_KNOWN_AGENTS_MD = "services/keel-site/public/.well-known/agents.md"

# Raw-markdown docs variants (spec 07 R2). public/docs/**.md is emitted
# by services/keel-site/scripts/generate-docs-md.mjs; the transform is
# re-implemented here (deterministically) so drift fails CI.
DOCS_CONTENT_DIR = "services/keel-site/content/docs"
DOCS_MD_PUBLIC_DIR = "services/keel-site/public/docs"
DOCS_MD_ROOT_FILE = "services/keel-site/public/docs.md"


def load_canonical() -> dict:
    return json.loads(CANONICAL_PATH.read_text())


def render_markdown_table(data: dict) -> str:
    """Deterministic markdown render of the canonical table."""
    cols = data["columns"]
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in data["rows"]:
        lines.append(f"| {row['audience']} | {row['default_path']} | {row['also_works']} |")
    return "\n".join(lines)


def render_block(data: dict) -> str:
    """The full generated block, markers included."""
    return (
        f"{BEGIN_MARKER}\n"
        "<!-- GENERATED from shared/surface-routing.json — edit there, then run\n"
        "     python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write -->\n"
        f"{render_markdown_table(data)}\n"
        f"{END_MARKER}"
    )


_BLOCK_RE = re.compile(
    re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER),
    flags=re.DOTALL,
)


def _check_markdown_target(rel: str, expected_block: str, write: bool) -> list[str]:
    path = REPO_ROOT / rel
    if not path.exists():
        return [f"{rel}: missing render target (routing table must render here)"]
    text = path.read_text()
    blocks = _BLOCK_RE.findall(text)
    if len(blocks) != 1:
        return [
            f"{rel}: expected exactly one surface-routing marker block "
            f"({BEGIN_MARKER} … {END_MARKER}), found {len(blocks)}"
        ]
    if blocks[0] == expected_block:
        return []
    if write:
        path.write_text(_BLOCK_RE.sub(lambda _: expected_block, text, count=1))
        return []
    return [
        f"{rel}: surface-routing block drifted from shared/surface-routing.json "
        "(run the checker with --write to regenerate)"
    ]


def _check_tsx_target(rel: str) -> list[str]:
    path = REPO_ROOT / rel
    if not path.exists():
        return [f"{rel}: missing render target (routing table must render here)"]
    if not TSX_IMPORT_RE.search(path.read_text()):
        return [
            f"{rel}: must import the canonical table from "
            "'@shared/surface-routing.json' (no hand-copied rows)"
        ]
    return []


def _check_agents_md_single_source() -> list[str]:
    a = REPO_ROOT / AGENTS_MD
    b = REPO_ROOT / WELL_KNOWN_AGENTS_MD
    missing = [str(p.relative_to(REPO_ROOT)) for p in (a, b) if not p.exists()]
    if missing:
        return [f"{m}: missing agents instruction file" for m in missing]
    if a.read_bytes() != b.read_bytes():
        return [
            f"{WELL_KNOWN_AGENTS_MD}: must be byte-identical to {AGENTS_MD} "
            "(single source; copy AGENTS.md over it)"
        ]
    return []


def transform_mdx_to_md(text: str) -> str:
    """Deterministic .mdx → raw .md transform.

    Mirror of services/keel-site/scripts/generate-docs-md.mjs: strip the
    frontmatter fence and emit ``# title`` + ``> description`` when the
    body doesn't already start with an H1. Any change here must be made
    in both implementations.
    """
    title = ""
    description = ""
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            frontmatter = text[4:end]
            body = text[end + 5 :]
            for line in frontmatter.splitlines():
                m = re.match(r"^(title|description):\s*(.*)$", line)
                if m:
                    value = m.group(2).strip()
                    if (value.startswith('"') and value.endswith('"')) or (
                        value.startswith("'") and value.endswith("'")
                    ):
                        value = value[1:-1]
                    if m.group(1) == "title":
                        title = value
                    else:
                        description = value
    body = body.lstrip("\n")
    header = ""
    if title and not body.startswith("# "):
        header = f"# {title}\n\n"
        if description:
            header += f"> {description}\n\n"
    return header + body


def _docs_md_expected() -> dict[str, str]:
    """Map of repo-relative output path → expected raw markdown."""
    content_dir = REPO_ROOT / DOCS_CONTENT_DIR
    out: dict[str, str] = {}
    for mdx in sorted(content_dir.rglob("*.mdx")):
        rel = mdx.relative_to(content_dir)
        if rel.name == "index.mdx":
            if rel.parent == Path("."):
                target = Path(DOCS_MD_ROOT_FILE)
            else:
                target = Path(DOCS_MD_PUBLIC_DIR) / rel.parent.with_suffix(".md")
        else:
            target = Path(DOCS_MD_PUBLIC_DIR) / rel.with_suffix(".md")
        out[str(target)] = transform_mdx_to_md(mdx.read_text())
    return out


def _check_docs_md() -> list[str]:
    errors: list[str] = []
    expected = _docs_md_expected()
    for rel, want in expected.items():
        path = REPO_ROOT / rel
        if not path.exists():
            errors.append(
                f"{rel}: missing raw-markdown docs variant "
                "(run `node services/keel-site/scripts/generate-docs-md.mjs`)"
            )
            continue
        if path.read_text() != want:
            errors.append(
                f"{rel}: stale raw-markdown docs variant "
                "(run `node services/keel-site/scripts/generate-docs-md.mjs`)"
            )
    # No orphans: every published .md must correspond to a docs page.
    public_docs = REPO_ROOT / DOCS_MD_PUBLIC_DIR
    if public_docs.exists():
        for md in sorted(public_docs.rglob("*.md")):
            rel = str(md.relative_to(REPO_ROOT))
            if rel not in expected:
                errors.append(f"{rel}: orphan docs .md variant (no matching content/docs page)")
    return errors


def check_surface_routing(*, write: bool = False) -> list[str]:
    """Return human-readable drift errors (empty list = consistent)."""
    errors: list[str] = []
    data = load_canonical()
    expected_block = render_block(data)
    for rel in MARKDOWN_TARGETS:
        errors.extend(_check_markdown_target(rel, expected_block, write))
    for rel in TSX_TARGETS:
        errors.extend(_check_tsx_target(rel))
    errors.extend(_check_agents_md_single_source())
    errors.extend(_check_docs_md())
    return errors


def main() -> int:
    write = "--write" in sys.argv[1:]
    errors = check_surface_routing(write=write)
    if not errors:
        print("Surface-routing renders match shared/surface-routing.json.")
        return 0
    print("Surface-routing drift detected:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
