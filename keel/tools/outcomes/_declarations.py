"""The declaration diff — ONE owner for Execution / Globals / Universe.

The structural differ (`pipeline_engine.dsl.differ`, reached through
`strategy_diff.diff_sources`) covers PIPELINE STEPS ONLY. Two surfaces
need the other half of a source pair:

* `keel_backtest_compare` — which declarations differ between two runs,
  so an execution-mode flip sits beside the fees it moved;
* `keel_strategy_compose` — what a SAVE changed, so an edit to
  `Globals`, `Universe` or `Execution` is not invisible in the change
  view (Q-1707: a `bar_offset` / `top_n` / buffer edit produced
  `view.change = null`, and a mixed save reported `"1 changed"` while
  two things had).

Compare had the only implementation; this module is it, and both call
it. One computation owner, one storage owner: the diff is computed
HERE and carried verbatim on both wires as
``{section: {key: {"a": before, "b": after}}}`` — compare under
``spec_diff.declarations``, compose under ``view.change.declarations``.
A renderer labels it; nobody recomputes it.

Pure: parsing is the caller's (or `declarations_between`'s) and there is
no network, no clock, no registry that can fail a save.
"""

from __future__ import annotations

from typing import Any


__all__ = [
    "DECLARATION_KEY_LABELS",
    "DECLARATION_SECTIONS",
    "SECTION_LABELS",
    "count_declarations",
    "declaration_rows",
    "declaration_view",
    "declarations_between",
    "diff_declarations",
]


#: The three declaration blocks, in the order a reader meets them in the
#: source. Both wires key on exactly these names.
DECLARATION_SECTIONS = ("execution", "globals", "universe")

#: What a section is CALLED in rendered copy — the DSL keyword, because
#: that is what the user typed.
SECTION_LABELS = {
    "execution": "Execution",
    "globals": "Globals",
    "universe": "Universe",
}

#: Words the change view says instead of the raw declaration key, where
#: the raw key is jargon rather than the thing the user was adjusting.
#: Same vocabulary as `backtest_compare._DECLARATION_LABELS` (`buffer`,
#: `method`, `tolerance`, `min order`). A key ABSENT from this map says
#: its own name — which is what the user wrote in the source, so
#: `bar_offset`, `top_n` and `rebalance` stay as they are.
DECLARATION_KEY_LABELS = {
    "buffer_threshold": "buffer",
    "buffer_mode": "buffer mode",
    "rebalance_method": "method",
    "on_change_tolerance": "tolerance",
    "min_trade_size": "min order",
}


def declaration_view(parsed) -> dict[str, dict]:
    """Effective Execution/Globals/Universe values for one parsed source.

    Effective (not just explicitly-set) values: a deleted `buffered`
    block and an inherited default `every_bar` must still diff — the
    behavior changed regardless of who typed it.
    """
    from pipeline_engine.dsl.spec import EXECUTION_PARAM_META, ExecutionSpec

    execution = parsed.execution or ExecutionSpec()
    exec_view = {name: getattr(execution, name) for name in EXECUTION_PARAM_META}

    globals_view: dict[str, Any] = {}
    if parsed.globals_ is not None:
        globals_view = {
            "target_timeframe": parsed.globals_.target_timeframe,
            "bar_offset": parsed.globals_.bar_offset,
        }

    universe_view: dict[str, Any] = {}
    if parsed.universe is not None:
        u = parsed.universe
        # The DECLARED universe only. `resolved` is the server's bake
        # (keel-api universe_bake, Q-1504), re-run on every save — not
        # something the user declared — so it is not part of the view
        # (Q-1747): a save whose only edit was an Execution buffer reported
        # `Universe · resolved [AAVE, ARB, …] → —`, the stored HEAD's baked
        # list against the unbaked source the agent submitted.
        universe_view = {
            "mode": u.mode,
            "market": u.market,
            "symbols": u.symbols,
            "categories": u.categories,
            "top_n": u.top_n,
            "exclusions": u.exclusions,
            "inclusions": u.inclusions,
        }

    return {"execution": exec_view, "globals": globals_view, "universe": universe_view}


def diff_declarations(view_a: dict, view_b: dict) -> dict[str, dict]:
    """`{section: {key: {"a": before, "b": after}}}` — only what moved."""
    out: dict[str, dict] = {}
    for section in DECLARATION_SECTIONS:
        sa, sb = view_a.get(section, {}), view_b.get(section, {})
        changed = {
            key: {"a": sa.get(key), "b": sb.get(key)}
            for key in sorted(set(sa) | set(sb))
            if sa.get(key) != sb.get(key)
        }
        if changed:
            out[section] = changed
    return out


def declarations_between(source_a: str, source_b: str) -> dict[str, dict]:
    """The declaration diff of two DSL SOURCES — parse, view, diff.

    Raises whatever the parser raises; callers that treat a change block
    as advisory catch it (a rendering must never fail a save).
    """
    from pipeline_engine.dsl import parse_strategy

    return diff_declarations(
        declaration_view(parse_strategy(source_a)),
        declaration_view(parse_strategy(source_b)),
    )


def count_declarations(declarations: Any) -> int:
    """How many declaration KEYS moved — the unit a change view counts."""
    if not isinstance(declarations, dict):
        return 0
    return sum(len(keys) for keys in declarations.values() if isinstance(keys, dict))


def declaration_rows(declarations: Any) -> list[dict]:
    """The renderable rows, in section order: section, key, label, a, b.

    The Python twin of `card-strategy.js`'s `declRows`. Both render
    `Execution · buffer  0.1 → 0.05` from the SAME wire block — neither
    recomputes the diff.
    """
    rows: list[dict] = []
    if not isinstance(declarations, dict):
        return rows
    for section in DECLARATION_SECTIONS:
        keys = declarations.get(section)
        if not isinstance(keys, dict):
            continue
        for key in sorted(keys):
            pair = keys[key]
            if not isinstance(pair, dict):
                continue
            rows.append(
                {
                    "section": section,
                    "section_label": SECTION_LABELS.get(section, section),
                    "key": key,
                    "label": DECLARATION_KEY_LABELS.get(key, key),
                    "a": pair.get("a"),
                    "b": pair.get("b"),
                }
            )
    return rows
