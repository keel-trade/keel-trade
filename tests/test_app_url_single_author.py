"""The four app targets that have a canonical builder have exactly one author.

Why this guard exists: `open_in_app.app_url_for` was introduced as "the
canonical web-app URL for one target", but only `keel_app_link` called it.
Eighteen other sites across fourteen files built the same URL with an
f-string, because copying the neighbouring line is easier than importing.
That was invisible until V-6 (2026-09-19) moved the strategy target from
`/strategies/{id}` to the editor: the one call site followed, the eighteen did
not, and every checkout, push, pull, memory, restore, search row, ownership
message and deploy handoff kept pointing at a page that had become a redirect.

Nothing failed. The links still resolved — which is what makes this class of
drift expensive: a stale URL and a correct one are indistinguishable from the
tool's side, and only a reader clicking through would land somewhere other
than where the product decided they should.

**Scope.** Only the four kinds `app_url_for` knows how to build: a strategy, a
backtest, a live deployment, a share page. Routes with no canonical builder
(`/accounts/{id}`, `/audit`, `/deploy/{id}`, the bare `/strategies` list) are
composed in place and are NOT flagged — a guard that fires on things its fix
cannot express is a guard people switch off.

The same blindness shipped two parameters no page has ever read
(`?tab=history` on the commit log, `?compare=a..b` on the diff), so
`_with_query` builds parameterised links in the same one place.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest


OUTCOMES = Path(__file__).resolve().parents[1] / "keel" / "tools" / "outcomes"

# The one file allowed to compose these URLs from their parts.
AUTHOR = "open_in_app.py"

# The four kinds `app_url_for` owns. A trailing `/{` or `/"` distinguishes a
# TARGET (`/strategies/{id}`) from the collection route (`/strategies`), which
# has no canonical builder.
OWNED = re.compile(
    r"\{\s*ctx\.app_url\s*\}/(?:strategies|backtests|live)/\{"
    r"|\{\s*ctx\.share_url_root\s*\}/\{"
)

DEAD_PARAMS = ("?tab=history", "?compare=")


def _code_only(path: Path) -> list[tuple[int, str]]:
    """Source lines with comments removed.

    Comments are not code: this file's own explanation of `?compare=` is not a
    tool shipping `?compare=`. The design-token gate had exactly this bug —
    it read `// React error #418` as a hex colour and held main red for a day
    (Q-1639) — so this guard strips them rather than repeat it.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    drop: dict[int, list[tuple[int, int]]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                drop.setdefault(tok.start[0], []).append((tok.start[1], tok.end[1]))
    except tokenize.TokenError:  # pragma: no cover - a syntax error fails elsewhere
        pass
    out = []
    for i, line in enumerate(lines, 1):
        for start, _end in sorted(drop.get(i, []), reverse=True):
            line = line[:start]
        out.append((i, line))
    return out


def _sources() -> list[Path]:
    return sorted(p for p in OUTCOMES.glob("*.py") if p.name != "__init__.py")


def test_only_app_url_for_composes_the_owned_targets() -> None:
    """No tool builds a strategy/backtest/live/share URL by hand.

    SEED: put `hero_url=f"{ctx.app_url}/strategies/{strategy_id}"` back into
    any tool (strategy_checkout.py, say) — this test names that file and line.
    """
    offenders: list[str] = []
    for path in _sources():
        if path.name == AUTHOR:
            continue
        for lineno, line in _code_only(path):
            if OWNED.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "These build a web-app URL by hand instead of calling "
        "`app_url_for(kind, id, ctx)`, so when the product moves a route they "
        "silently keep pointing at the old one:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_is_not_vacuous() -> None:
    """The population is real, and the pattern matches the shape it targets.

    Both quantities are read from the tree, not from the guard's own output, so
    a seed in any tool cannot move them.
    """
    sources = _sources()
    assert len(sources) > 30, f"only {len(sources)} outcome modules scanned"

    author_code = "\n".join(line for _, line in _code_only(OUTCOMES / AUTHOR))
    assert OWNED.search(author_code), (
        f"the pattern no longer matches {AUTHOR}, which definitely composes "
        "these URLs — it would match nothing anywhere and the guard above "
        "would pass vacuously"
    )

    # And comment-stripping works, or the dead-param arms below are theatre.
    stripped = _code_only(OUTCOMES / "strategy_diff.py")
    assert any(
        "?compare=" in line for line in (OUTCOMES / "strategy_diff.py").read_text().splitlines()
    ), (
        "strategy_diff.py no longer mentions ?compare= even in a comment — "
        "the comment-stripping arm below no longer proves anything"
    )
    assert not any("?compare=" in line for _, line in stripped), "comments were not stripped"


@pytest.mark.parametrize("dead", DEAD_PARAMS)
def test_no_tool_ships_a_query_no_page_reads(dead: str) -> None:
    """Two parameters that were emitted for months and read by nothing.

    `services/keel-app`'s editor route honours `version`, `run`, `mode`,
    `style`, `split`, `validate`, `onboarding`, `component-highlight` and
    `continue`; the overview route it replaced read no query at all. (The
    LIVE route's `?tab=` is real — `live/[id]/__tests__/live-detail-tabs`
    pins it — which is why only these two strings are listed.)

    Reads STRING LITERALS through the AST, not raw text: a docstring
    explaining the defect is not a tool shipping it, and this file, the
    helper's docstring and the two fixed tools all say the words. Text
    scanning is what made the design-token gate red for a day (Q-1639).
    """
    offenders: list[str] = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if id(node) in docstrings:
                continue
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(
                    v.value
                    for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
            if text and dead in text:
                offenders.append(f"{path.name}:{getattr(node, 'lineno', '?')}")
    assert not offenders, f"`{dead}` is read by no app route: " + ", ".join(sorted(set(offenders)))
