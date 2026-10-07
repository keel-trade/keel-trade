"""The derived strategy `view` — one owner, four consumers (PLAN §4.2).

`build_view()` turns the graph keel-api already returns into the block the
strategy-shaped tools carry: a deterministic header, the structure, the
change, whatever evidence exists, the size the event deserves, and the
markdown text hosts render. Four consumers read it — the MCP text block,
the CLI human renderer, the card (`keel/widgets/assets/card-strategy.js`),
and the app canvas through the same graph.

Three rules this module lives by:

* **Registry nouns only.** Every label is a mechanical projection of the
  graph: a timeframe, a universe mode, a rebalance setting, a component
  name, a count. No adjectives, no verbs, no claims, no dates, no ids
  anywhere in `header` (V-2: the server writes structure, the agent
  writes meaning, the user writes the thesis).
* **Pure.** No network, no clock, no registry import that can fail the
  caller. Categories come from the SDK's bundled registry; when it cannot
  be read the map is simply empty and the card falls back to no badge.
* **One grammar with the card.** The label helpers here are the Python
  twin of the card's `clockLabel` / `universeFromGraph` /
  `executionFromGraph` / `countBlocks` / `chooseSize` fallbacks, so a
  legacy envelope (no `view`) and a modern one read identically.

Two graph shapes reach this module and both are supported:

* the emitter's GraphModel (`{blocks, factories, globals, universe,
  execution}`, blocks keyed `component` / `branches` / `factoryName` /
  `slotName` / `variableName`) — what keel-api serves;
* the library's render-only summary (`{name, steps}`, steps keyed `name`
  with a nested `pipeline` type and `unknown` reprs) — what
  `libs/strategy_library/data/entries/*/graph.json` holds, which is the
  M0 guard's corpus.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ._base import election_key
from ._declarations import count_declarations, declaration_rows


__all__ = [
    "KIND_STRATEGY",
    "VIEW_TOOLS",
    "build_parse_error_view",
    "build_view",
    "change_from_diff",
    "count_blocks",
    "choose_size",
    "resize_view",
]


#: The tools whose envelope can carry a `view`. The MCP adapter reads this
#: to decide whether a tool's text block may become `view.markdown` (and
#: therefore whether its structured output must stay schema-free).
#:
#: The four backtest-shaped tools joined at the render-cadence build
#: (RENDER-CADENCE-BUILD §2.5, Q-1688): their text block becomes the
#: receipt / evidence / comparison markdown, with the operational fields
#: appended by `_mcp_adapter.view_tool_result` (§2.8) so nothing the
#: model must read is lost to `structuredContent`.
VIEW_TOOLS = frozenset(
    {
        "keel_strategy_get",
        "keel_strategy_compose",
        "keel_strategy_fork",
        "keel_strategy_diff",
        "keel_library_get",
        "keel_library_fork",
        "keel_backtest_run",
        "keel_backtest_watch",
        "keel_backtest_summarize",
        "keel_backtest_compare",
        # Agent-surface-cleanup spec 02 §2.2/§2.5: restore is the strategy
        # kind (R-15, the same rule as compose) and `keel_account_status` is a view
        # tool with no card — 12 markdown view tools in all.
        "keel_strategy_restore",
        "keel_account_status",
    }
)

#: `view.kind` — what a renderer dispatches on. Strategy views carry
#: `"strategy"`; a missing `kind` reads as strategy, so an envelope built
#: before this build renders identically (BUILD §2.3).
KIND_STRATEGY = "strategy"

#: Inline block budget, spent in reading order over EVERY block (not
#: top-level rows — the HRP measured 1,770 px tall under a top-level
#: limit; decisions.md "The extremes come first").
BLOCK_BUDGET = 12
BLOCK_BUDGET_MOBILE = 8

#: Beyond this many touched blocks the change stops being a list of hunks
#: and becomes the full form with marks (PLAN §4.3). Mirrors the card's
#: MAX_HUNKS. Counted over BLOCKS only: marks are a block concept, and a
#: declaration has no block to mark (Q-1707).
MAX_HUNKS = 8

#: How many declaration deltas the one-line summary names before it says
#: how many more there are. Mirrors the card's MAX_DECL_PHRASES.
MAX_DECL_PHRASES = 3

#: The card's Code tab carries the strategy's own DSL source. A source longer
#: than this rides as a head plus a marker rather than in full: the card is a
#: preview and the editor is where a long strategy is actually read, and the
#: source travels inside every envelope that carries a view.
MAX_SOURCE_CHARS = 20000

#: A run whose window covers less than this share of the longest window any
#: completed run of the strategy covered is a SUB-window (a half-period
#: split, a stress window): never preferred as "the" evidence while a
#: full-window run exists, and labelled when it is all there is (Q-1879).
FULL_WINDOW_SHARE = 0.9


# ── Graph walking (both shapes) ───────────────────────────────────────


def _blocks(graph: Any) -> list[dict]:
    """The top-level block list of either graph shape."""
    if not isinstance(graph, dict):
        return []
    for key in ("blocks", "steps"):
        value = graph.get(key)
        if isinstance(value, list):
            return [b for b in value if isinstance(b, dict)]
    return []


def _groups(block: dict) -> list[tuple[str, list[dict]]]:
    """The named child sequences of a block, in declaration order.

    A `parallel` yields one entry per branch; the library shape's
    `pipeline` yields its single named sub-sequence. A leaf yields none —
    which is what makes it a leaf everywhere below.
    """
    if block.get("type") == "parallel":
        branches = block.get("branches")
        if isinstance(branches, dict):
            return [
                (str(name), [b for b in blocks if isinstance(b, dict)])
                for name, blocks in branches.items()
                if isinstance(blocks, list)
            ]
        return []
    if block.get("type") == "pipeline":
        steps = block.get("steps")
        if isinstance(steps, list):
            return [
                (
                    str(block.get("name") or "pipeline"),
                    [b for b in steps if isinstance(b, dict)],
                )
            ]
    return []


def count_blocks(blocks: list[dict]) -> int:
    """Leaf blocks in a list, descending into branches.

    The Python twin of the card's `countBlocks`: a parallel contributes
    its branches' blocks, never itself.
    """
    total = 0
    for block in blocks:
        groups = _groups(block)
        if groups:
            for _, children in groups:
                total += count_blocks(children)
        else:
            total += 1
    return total


_REPR_KIND_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(")
_REPR_LABEL_RE = re.compile(r"\b(?:slot_name|name|key)='([^']*)'")

#: Library-shape `unknown` reprs → the verb the card's badges use.
_REPR_VERBS = {
    "SlotStoreSpec": "store",
    "SlotStoreValueSpec": "store",
    "SlotLoadSpec": "load",
    "SlotExtractSpec": "extract",
}


def _label_from_repr(text: str) -> str:
    kind_m = _REPR_KIND_RE.match(text or "")
    kind = kind_m.group(1) if kind_m else ""
    label_m = _REPR_LABEL_RE.search(text or "")
    label = label_m.group(1) if label_m else ""
    verb = _REPR_VERBS.get(kind)
    if verb and label:
        return f"{verb} {label}"
    if label:
        return label
    return kind or "unknown"


def block_name(block: dict) -> str:
    """The name a reader would say out loud for one block.

    The Python twin of the card's `blockName`, widened for the library
    shape's `name` key and its `unknown` reprs.
    """
    kind = block.get("type")
    if kind == "component":
        return str(block.get("component") or block.get("name") or "")
    if kind == "factory_call":
        return str(block.get("factoryName") or block.get("factory") or block.get("name") or "")
    if kind in ("slot_store", "slot_store_value"):
        return f"store {block.get('slotName') or ''}".strip()
    if kind == "slot_load":
        return f"load {block.get('slotName') or ''}".strip()
    if kind == "slot_extract":
        return f"extract {block.get('extractKey') or ''}".strip()
    if kind == "variable_ref":
        return str(block.get("variableName") or block.get("name") or "")
    if kind == "unknown":
        return _label_from_repr(str(block.get("repr") or ""))
    return str(block.get("component") or block.get("name") or kind or "")


def component_name(block: dict) -> str | None:
    """The registry component this block names, or None."""
    if block.get("type") != "component":
        return None
    name = block.get("component") or block.get("name")
    return str(name) if name else None


def walk_components(graph: Any) -> list[str]:
    """Every component occurrence in reading order: pipeline then factories.

    Occurrences, not a set — a component used twice appears twice, which
    is what the M0 guard counts.
    """
    out: list[str] = []

    def walk(blocks: list[dict]) -> None:
        for block in blocks:
            name = component_name(block)
            if name:
                out.append(name)
            for _, children in _groups(block):
                walk(children)

    walk(_blocks(graph))
    for factory in _factory_defs(graph):
        walk(_factory_body(factory))
    return out


def _factory_defs(graph: Any) -> list[dict]:
    if not isinstance(graph, dict):
        return []
    factories = graph.get("factories")
    if not isinstance(factories, list):
        return []
    return [f for f in factories if isinstance(f, dict)]


def _factory_body(factory: dict) -> list[dict]:
    for key in ("body", "steps"):
        value = factory.get(key)
        if isinstance(value, list):
            return [b for b in value if isinstance(b, dict)]
    return []


# ── Header labels (the card's twin) ───────────────────────────────────


def clock_label(globals_: Any) -> str | None:
    """`4h`, or `1d @ 12h` when a bar offset is declared."""
    if not isinstance(globals_, dict):
        return None
    timeframe = globals_.get("target_timeframe")
    if not timeframe:
        return None
    offset = globals_.get("bar_offset")
    return f"{timeframe} @ {offset}" if offset else str(timeframe)


def top_n_label(top_n: Any, mode: Any = "top_volume") -> str:
    """`Top 10 by volume` — the universe chip's words for a `top_n`, the ONE
    owner (the chip and compare's diff row both say it this way, Q-1876).
    `by volume` only under the mode that ranks by volume."""
    label = f"Top {top_n if top_n is not None else 'N'}"
    return f"{label} by volume" if mode in (None, "top_volume") else label


def universe_block(universe: Any) -> dict | None:
    """`{label, total, symbols}` — the mode said in registry nouns."""
    if not isinstance(universe, dict):
        return None
    mode = str(universe.get("mode") or "manual")
    resolved = universe.get("resolved")
    symbols = universe.get("symbols")
    listed = resolved if isinstance(resolved, list) and resolved else symbols
    listed = list(listed) if isinstance(listed, list) else []
    if universe.get("resolved_total") is not None:
        total = universe["resolved_total"]
    elif universe.get("top_n") is not None and not listed:
        total = universe["top_n"]
    else:
        total = len(listed)
    if mode == "manual":
        n = len(listed)
        label = f"Manual basket · {n} asset{'' if n == 1 else 's'}" if listed else "Manual basket"
    elif mode == "top_volume":
        label = top_n_label(universe.get("top_n"), mode)
    elif mode == "category":
        cats = universe.get("categories")
        label = "Categories" + (
            ": " + ", ".join(str(c) for c in cats) if isinstance(cats, list) and cats else ""
        )
    else:
        label = mode.replace("_", " ")
    market = universe.get("market")
    if market == "perp":
        label += " · HL perps"
    elif market:
        label += f" · {market}"
    return {"label": label, "total": total, "symbols": listed}


def execution_block(execution: Any) -> dict | None:
    """`{label, params}` — `Buffered 0.4`, `On change`, else `Execution`."""
    if not isinstance(execution, dict):
        return None
    rebalance = execution.get("rebalance")
    if rebalance == "buffered":
        threshold = execution.get("buffer_threshold")
        label = "Buffered" + (f" {_num(threshold)}" if threshold is not None else "")
    elif rebalance:
        text = str(rebalance).replace("_", " ")
        label = text[:1].upper() + text[1:]
    else:
        label = "Execution"
    return {"label": label, "params": dict(execution)}


def execution_dsl(execution: Any) -> str | None:
    """`Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', …)`.

    The execution declaration as DSL a model can copy (Q-1846). The card's
    label ("Buffered 0.2") is for a person; an agent that read it guessed
    `Execution(buffer=0.2)` — a parse error — because the label names no
    parameter. This names every parameter that is IN EFFECT for the mode:
    `rebalance` always, each param whose registry `modes` include the mode
    (at its value, else the registry default the runtime back-fills — so a
    template's `to_edge` and the default `to_center` read differently), and
    a mode-free param (`min_trade_size`) only when it differs from its
    default. Order and defaults are `EXECUTION_PARAM_META`'s, the one
    registry the parser and both validators read. Accepts the graph's sparse
    dict or the declaration view's full one. None when there is nothing to
    say or the registry cannot be read.
    """
    if not isinstance(execution, dict):
        return None
    try:
        from pipeline_engine.dsl.spec import EXECUTION_PARAM_META
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    rebalance = execution.get("rebalance") or EXECUTION_PARAM_META["rebalance"]["default"]
    args: list[str] = []
    for name, meta in EXECUTION_PARAM_META.items():
        default = meta.get("default")
        value = rebalance if name == "rebalance" else execution.get(name)
        modes = meta.get("modes")
        if name == "rebalance":
            pass
        elif modes:
            if rebalance not in modes:
                continue
            if value is None:
                value = default
        elif value is None or value == default:
            continue
        if value is None:
            continue
        args.append(f"{name}={value!r}")
    return f"Execution({', '.join(args)})" if args else None


def factory_rows(graph: Any) -> list[dict]:
    """`[{name, steps, params}]` — the name, its block count, its params."""
    rows: list[dict] = []
    for factory in _factory_defs(graph):
        params = factory.get("params")
        names: list[str] = []
        if isinstance(params, list):
            for param in params:
                if isinstance(param, str):
                    names.append(param)
                elif isinstance(param, dict) and param.get("name") is not None:
                    names.append(str(param["name"]))
        rows.append(
            {
                "name": str(factory.get("name") or ""),
                "steps": count_blocks(_factory_body(factory)),
                "params": names,
            }
        )
    return rows


def categories_for(graph: Any) -> dict[str, str]:
    """`{component: registry category}` for every component in the graph.

    Read from the SDK's bundled registry (`keel/data/registry.json`) —
    the same generated projection every other SDK surface reads. A
    registry that cannot be loaded yields an empty map rather than a
    fabricated category: the card simply draws no badge.
    """
    try:
        from keel.data.registry import _load_json

        components = _load_json("registry.json").get("components")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return {}
    if not isinstance(components, list):
        return {}
    by_name = {
        str(c.get("name")): c.get("category")
        for c in components
        if isinstance(c, dict) and c.get("name")
    }
    out: dict[str, str] = {}
    for name in walk_components(graph):
        if name in out:
            continue
        category = by_name.get(name)
        if category:
            out[name] = str(category)
    return out


# ── Validation, status, evidence ──────────────────────────────────────


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return 0


def validation_block(metadata: Any) -> dict:
    """`{ok, errors, warnings}` from whatever the caller already knows.

    keel-api stamps `validation: {valid, errors, warnings}` on a save; a
    compose passes its own local verdict the same way. Absent both, a
    strategy that compiled is reported valid — which is the state the
    server itself is in when it serves the row.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    raw = meta.get("validation")
    if isinstance(raw, dict):
        errors = _count(raw.get("errors"))
        warnings = _count(raw.get("warnings"))
        ok = raw.get("ok")
        if ok is None:
            ok = raw.get("valid")
        if ok is None:
            ok = errors == 0
        block = {"ok": bool(ok), "errors": errors, "warnings": warnings}
    elif meta.get("compilation_error"):
        block = {"ok": False, "errors": 1, "warnings": 0}
    else:
        block = {"ok": True, "errors": 0, "warnings": 0}
    deprecations = deprecations_block(meta.get("deprecations"))
    if deprecations:
        # The pinned deprecated components (position-layer spec 04-R24/R25):
        # keel-api derives them from the HEAD lock. Present only when there
        # are any, so every other strategy's block is byte-identical.
        block["deprecations"] = deprecations
    return block


def deprecations_block(raw: Any) -> list[dict]:
    """keel-api's ``deprecations`` rows, normalised to the four keys the card
    and the ``upgrade:`` line read: ``component``, ``version``,
    ``known_issue`` (``{id, summary}`` or ``None``) and ``replacement_text``."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict) or not row.get("component"):
            continue
        issue = row.get("known_issue")
        out.append(
            {
                "component": str(row["component"]),
                "version": row.get("version"),
                "known_issue": (
                    {"id": issue.get("id"), "summary": issue.get("summary")}
                    if isinstance(issue, dict) and issue.get("id")
                    else None
                ),
                "replacement_text": row.get("replacement_text"),
            }
        )
    return out


_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _short_date(value: Any) -> str | None:
    """`Sep 20` — the card's `fmtDate(v, false)`, never a raw stamp."""
    text = str(value or "")
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{_MONTHS[month - 1]} {int(match.group(3))}"


def status_text_for(metadata: Any) -> str | None:
    """`Draft · updated Sep 20` — the card's `statusText`, in Python."""
    meta = metadata if isinstance(metadata, dict) else {}
    status = meta.get("status")
    word = str(status).lower().capitalize() if status else None
    when = _short_date(meta.get("updated_at"))
    if word and when:
        return f"{word} · updated {when}"
    if word:
        return word
    return f"Updated {when}" if when else None


def evidence_from_metadata(metadata: Any) -> dict | None:
    """The latest completed backtest, or None ⇒ "No backtest yet".

    Every field is a tool result copied across; nothing is recomputed
    here (one computation owner per measurement).
    """
    meta = metadata if isinstance(metadata, dict) else {}
    return _evidence(
        meta.get("latest_backtest_metrics"),
        version=meta.get("latest_backtest_sequence"),
        start=meta.get("latest_backtest_start_date"),
        end=meta.get("latest_backtest_end_date"),
    )


def evidence_from_run(row: Any, *, sub_window: bool = False) -> dict | None:
    """One `/v1/backtests` listing row as `view.evidence` (Q-1879) — the
    same shape and the same owners as `evidence_from_metadata`, plus
    `sub_window` when the run covers only part of the strategy's longest
    window."""
    if not isinstance(row, dict):
        return None
    evidence = _evidence(
        row.get("metrics"),
        version=row.get("sequence_number"),
        start=row.get("start_date"),
        end=row.get("end_date"),
        served=row.get("window"),
    )
    if evidence is not None and sub_window:
        evidence["sub_window"] = True
    return evidence


def _evidence(
    metrics: Any, *, version: Any, start: Any, end: Any, served: Any = None
) -> dict | None:
    if not isinstance(metrics, dict) or not metrics:
        return None
    # The same window shape AND the same metric normalisation the backtest
    # view puts on the wire, from the same owner (`view_metrics`): this
    # used to re-pick the keys and re-sign the drawdown itself, and quoted
    # the full-window Sharpe where the backtest card and the app quote the
    # active-period one (Q-1709, Q-1746).
    from ._backtest_view import count_label, net_of_for, view_metrics, window_block

    normalised = view_metrics(metrics)
    # The one count the line names (spec 01 §6.2): the run's trades, or its
    # positions when that is all it recorded (Era B). A run that recorded
    # neither (an unknown era) names trades with an em dash.
    count_key = (
        "positions" if "trades" not in normalised and "positions" in normalised else "trades"
    )
    return {
        "version": version,
        "window": window_block(start, end, served),
        "sharpe": normalised.get("sharpe"),
        "total_return_pct": normalised.get("total_return_pct"),
        "max_drawdown_pct": normalised.get("max_drawdown_pct"),
        "win_rate_pct": normalised.get("win_rate_pct"),
        # `total_trades` is the card's existing key for this count: the run's
        # trades (`view.metrics.trades`), or its positions on an Era B run.
        "total_trades": normalised.get(count_key),
        # The count's word, served (Q-1906): the backtest view's one label,
        # lower-cased — "trades" or "positions", by era. The card draws it;
        # its static HTML may not carry the word (listed static-copy scan,
        # tests/test_policy_scan.py).
        "count_label": count_label(count_key),
        # What THIS run is net of, from the backtest view's one owner — the
        # strategy view kept its own ("fees, slippage, funding") beside the
        # results' "fees, slippage and carry": two words for one claim, and
        # "funding" is off the listed surface's word list (ChatGPT R4 #11).
        "net_of": net_of_for(metrics),
    }


def choose_evidence(rows: Any, *, version: Any = None) -> tuple[dict | None, bool]:
    """`(evidence, decided)` from a `/v1/backtests` listing (Q-1879).

    "Latest" is not "representative": a half-period split run last would
    otherwise stand for the strategy. Among COMPLETED runs, prefer the
    requested `version`'s runs (else every version's — the evidence line then
    says whose run it is), and among those the newest run whose window
    covers ≥ `FULL_WINDOW_SHARE` of the longest window any run covered; only
    when every candidate is shorter, the newest candidate, marked
    `sub_window`. `decided` is False when the listing holds no completed run
    with metrics — the caller keeps the metadata's answer then.
    """
    from ._backtest_view import _completed_at, is_success, window_block

    done = [
        r
        for r in (rows or [])
        if isinstance(r, dict)
        and is_success(r)
        and isinstance(r.get("metrics"), dict)
        and r["metrics"]
    ]
    if not done:
        return None, False

    def days(row: dict) -> int:
        value = window_block(row.get("start_date"), row.get("end_date"), row.get("window")).get(
            "days"
        )
        return value if isinstance(value, int) else 0

    def when(row: dict) -> Any:
        from datetime import datetime

        stamp = _completed_at(row)
        return stamp if stamp is not None else datetime.min.replace(tzinfo=None)

    longest = max(days(r) for r in done)
    floor = FULL_WINDOW_SHARE * longest
    same = [
        r
        for r in done
        if version is not None
        and _version_number(r.get("sequence_number")) == _version_number(version)
    ]
    pool = same or done
    full = [r for r in pool if days(r) >= floor]
    pick = max(full or pool, key=lambda r: when(r).replace(tzinfo=None))
    return evidence_from_run(pick, sub_window=days(pick) < floor), True


# ── The change block ──────────────────────────────────────────────────

_BRANCH_TOKEN_RE = re.compile(r"^step\[\d+\]$")


def _path_segments(path: Any) -> list[str]:
    """Branch names out of a differ path (`pipeline.step[2].trend_up.step[0]`)."""
    if isinstance(path, list):
        return [str(p) for p in path]
    segments = str(path or "").split(".")
    out: list[str] = []
    for index, segment in enumerate(segments):
        if index == 0 and segment == "pipeline":
            continue
        if _BRANCH_TOKEN_RE.match(segment):
            continue
        if segment:
            out.append(segment)
    return out


def _differ_name(component: Any, params: Any) -> str:
    """The differ's component token, said the way the graph says it."""
    text = str(component or "")
    if text.startswith("factory:") or text.startswith("var:"):
        return text.split(":", 1)[1]
    params = params if isinstance(params, dict) else {}
    if text in ("Store", "StoreValue"):
        return f"store {params.get('slot_name') or ''}".strip()
    if text == "Load":
        return f"load {params.get('slot_name') or ''}".strip()
    if text == "Extract":
        return f"extract {params.get('key') or ''}".strip()
    return text


def _block_index(graph: Any) -> dict[tuple[tuple[str, ...], str], list[str]]:
    """`{(branch path, block name): [ids in order]}` for the new graph.

    The differ works on sources and knows no block ids; the card marks
    blocks BY id. This is the join: walk the graph the differ walked and
    key each block the way a differ entry reads.
    """
    index: dict[tuple[tuple[str, ...], str], list[str]] = {}

    def walk(blocks: list[dict], path: tuple[str, ...]) -> None:
        for block in blocks:
            groups = _groups(block)
            if groups:
                for name, children in groups:
                    walk(children, path + (name,))
                continue
            key = (path, block_name(block))
            index.setdefault(key, []).append(str(block.get("id") or ""))

    walk(_blocks(graph), ())
    return index


def change_from_diff(
    diff: Any,
    graph: Any,
    *,
    from_version: Any = None,
    to_version: Any = None,
) -> dict | None:
    """The card-contract `change` from `strategy_diff`'s diff output.

    `diff` is exactly what `strategy_diff.diff_sources()` returns (the
    same structure the diff tool puts on the wire). Nothing about WHAT
    changed is decided here — only how it is keyed for a renderer:
    entries gain the graph block id they name, so a card can mark the
    block, and the path becomes the branch names a reader sees.
    """
    if not isinstance(diff, dict) or diff.get("error"):
        return None
    index = _block_index(graph)
    taken: dict[tuple[tuple[str, ...], str], int] = {}

    def resolve(entry: dict) -> str:
        path = tuple(_path_segments(entry.get("path")))
        name = _differ_name(entry.get("component"), entry.get("params"))
        key = (path, name)
        ids = index.get(key) or []
        cursor = taken.get(key, 0)
        taken[key] = cursor + 1
        if cursor < len(ids) and ids[cursor]:
            return ids[cursor]
        # No block answers to this entry (a removal, or a shape the
        # emitter renders differently) — the differ's own path is a
        # stable key, and a card simply draws no mark for it.
        return str(entry.get("path") or f"{'.'.join(path)}:{name}")

    def entries(raw: Any, *, with_params: bool) -> list[dict]:
        out: list[dict] = []
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            row: dict[str, Any] = {
                "id": resolve(entry),
                "component": _differ_name(entry.get("component"), entry.get("params")),
                "path": _path_segments(entry.get("path")),
            }
            if with_params and isinstance(entry.get("params"), dict):
                row["params"] = dict(entry["params"])
            if not with_params:
                changes = entry.get("param_changes")
                params: dict[str, Any] = {}
                if isinstance(changes, list):
                    for change in changes:
                        if isinstance(change, dict) and change.get("param") is not None:
                            params[str(change["param"])] = {
                                "old": change.get("old"),
                                "new": change.get("new"),
                            }
                elif isinstance(changes, dict):
                    for key, pair in changes.items():
                        if isinstance(pair, (list, tuple)) and len(pair) == 2:
                            params[str(key)] = {"old": pair[0], "new": pair[1]}
                row["params"] = params
            out.append(row)
        return out

    added = entries(diff.get("added"), with_params=True)
    removed = entries(diff.get("removed"), with_params=True)
    changed = entries(diff.get("changed") or diff.get("modified"), with_params=False)
    reordered = entries(diff.get("reordered"), with_params=True)
    # The declarations ride verbatim from `_declarations` (Q-1707) — the
    # block keys say what moved in the PIPELINE, and until this key
    # existed a `Globals` / `Universe` / `Execution` edit moved nothing
    # a reader could see. `touched` counts BOTH, because it is the number
    # the summary and the size are read as.
    declarations = diff.get("declarations")
    declarations = declarations if isinstance(declarations, dict) else {}
    blocks_touched = len(added) + len(removed) + len(changed)
    touched = blocks_touched + count_declarations(declarations)
    if touched == 0 and not reordered:
        return None
    block = {
        "added": added,
        "removed": removed,
        "changed": changed,
        "reordered": reordered,
        "summary_text": _change_summary(diff, declarations, blocks_touched),
        "touched": touched,
        "total": count_blocks(_blocks(graph)),
    }
    # Present exactly when a declaration moved, so a pipeline-only change
    # is byte-identical to what every existing reader already consumes and
    # the key's presence is itself the news.
    if declarations:
        block["declarations"] = declarations
    if from_version is not None:
        block["from_version"] = from_version
    if to_version is not None:
        block["to_version"] = to_version
    return block


def _change_summary(diff: dict, declarations: dict, blocks_touched: int) -> str | None:
    """The one line a host reads — blocks AND declarations (Q-1707).

    The structural differ's own summary is a BLOCK tally (`1 changed`,
    `1 added | 1 changed`) and it is `identical` when only a declaration
    moved. Naming each declaration delta after it, in the differ's own
    pipe-separated grammar, is what stops `"1 changed"` from being read
    as the whole truth about a save that moved two things.
    """
    base = diff.get("summary_text") or diff.get("summary")
    rows = declaration_rows(declarations)
    if not rows:
        return base
    phrases = [
        f"{row['section_label']} · {row['label']} {_value(row['a'])} → {_value(row['b'])}"
        for row in rows[:MAX_DECL_PHRASES]
    ]
    if len(rows) > MAX_DECL_PHRASES:
        phrases.append(f"+{len(rows) - MAX_DECL_PHRASES} more")
    if blocks_touched and base:
        return " | ".join([base, *phrases])
    return " | ".join(phrases)


#: A commit message is one line in the version history; past this the
#: remaining changes are counted, not listed.
MAX_COMMIT_MESSAGE_CHARS = 160


def commit_message_from_diff(diff: Any) -> str | None:
    """One line naming what a save changed — the version history's label.

    `Execution · buffer 0.1 → 0.2`, `ROC · period 8 → 42`, `+ LeverageCap`,
    `− Momentum` — the declaration rows and the block entries of the SAME
    diff the change view renders (Q-1752: every MCP save landed with an
    empty `message`, so v2/v3/v4 of one strategy could not be told apart and
    the next session backtested v4 thinking it was the base). None when the
    diff names nothing (an identical source, or a failed differ).
    """
    if not isinstance(diff, dict) or diff.get("error"):
        return None
    phrases: list[str] = []
    for row in declaration_rows(diff.get("declarations")):
        phrases.append(
            f"{row['section_label']} · {row['label']} {_value(row['a'])} → {_value(row['b'])}"
        )
    for entry in diff.get("changed") or diff.get("modified") or []:
        if not isinstance(entry, dict):
            continue
        name = _differ_name(entry.get("component"), entry.get("params"))
        changes = entry.get("param_changes")
        pairs: list[str] = []
        if isinstance(changes, list):
            pairs = [
                f"{c.get('param')} {_value(c.get('old'))} → {_value(c.get('new'))}"
                for c in changes
                if isinstance(c, dict) and c.get("param") is not None
            ]
        phrases.append(f"{name} · {', '.join(pairs)}" if pairs else f"~ {name}")
    for sign, key in (("+", "added"), ("−", "removed")):
        for entry in diff.get(key) or []:
            if isinstance(entry, dict):
                phrases.append(
                    f"{sign} {_differ_name(entry.get('component'), entry.get('params'))}"
                )
    if not phrases and diff.get("reordered"):
        phrases.append("reordered blocks")
    if not phrases:
        return None
    line = ""
    for i, phrase in enumerate(phrases):
        candidate = phrase if not line else f"{line}; {phrase}"
        if len(candidate) > MAX_COMMIT_MESSAGE_CHARS:
            return (
                f"{line}; +{len(phrases) - i} more" if line else phrase[:MAX_COMMIT_MESSAGE_CHARS]
            )
        line = candidate
    return line


def _blocks_touched(change: dict) -> int:
    """How many PIPELINE blocks a change touches — never the declarations.

    The marks arm and the `N blocks unchanged` line are both block
    arithmetic; counting a declaration in either one makes the card mark
    nothing and the markdown understate what is unchanged.
    """
    return (
        len(change.get("added") or [])
        + len(change.get("removed") or [])
        + len(change.get("changed") or [])
    )


def _normalize_change(change: Any, graph: Any) -> dict | None:
    """Fill the counts a renderer needs when a caller sent a bare diff."""
    if not isinstance(change, dict):
        return None
    out = dict(change)
    for key in ("added", "removed", "changed", "reordered"):
        if not isinstance(out.get(key), list):
            out[key] = []
    if out.get("declarations") is not None and not isinstance(out.get("declarations"), dict):
        out.pop("declarations")
    if out.get("touched") is None:
        out["touched"] = _blocks_touched(out) + count_declarations(out.get("declarations"))
    if out.get("total") is None:
        out["total"] = count_blocks(_blocks(graph))
    return out


def choose_size(change: Any, total: int) -> tuple[str, bool]:
    """`(size, marks)` — the event picks, never the model (V-13).

    The Python twin of the card's `chooseSize`: nothing moved ⇒
    structure; ONE thing moved and nothing was added or removed ⇒
    receipt; more than eight BLOCK hunks or more than half the blocks ⇒
    the full form with marks; otherwise the change.

    "One thing" counts a declaration key exactly as it counts a block's
    params (Q-1707): a lone `Execution(buffer_threshold=…)` edit is the
    same size of event as a lone `ROC(period=…)` edit, and reads as the
    same receipt. The marks arm stays BLOCK-only — a declaration has no
    block to mark, so counting it there would ask the card to draw the
    full form and mark nothing in it.
    """
    if not isinstance(change, dict):
        return "structure", False
    n_added = len(change.get("added") or [])
    n_removed = len(change.get("removed") or [])
    blocks_touched = n_added + n_removed + len(change.get("changed") or [])
    touched = change.get("touched")
    if not isinstance(touched, int):
        touched = blocks_touched + count_declarations(change.get("declarations"))
    if touched == 0:
        return "structure", False
    if touched == 1 and n_added == 0 and n_removed == 0:
        return "receipt", False
    declared_total = change.get("total")
    if not isinstance(declared_total, int):
        declared_total = total
    if blocks_touched > MAX_HUNKS or blocks_touched > declared_total / 2:
        return "structure", True
    return "change", False


# ── Values ────────────────────────────────────────────────────────────


def _num(value: Any) -> str:
    """A number the way a person says it: `25`, not `25.0`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _value(value: Any, *, limit: int = 30) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _num(value)
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict) and list(value) == ["$ref"] and isinstance(value["$ref"], str):
        # A factory-parameter reference is said by its name, not by its
        # wire form (the card's `fmtVal` does the same).
        text = value["$ref"]
    else:
        try:
            text = json.dumps(value, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _params_of(block: dict) -> dict:
    for key in ("params", "factoryArgs"):
        value = block.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _leaf_line(block: dict, *, max_params: int = 4) -> str:
    """`ADX(14)` · `EqualWeightSizer(max_weight=0.2, target_leverage=1)`.

    One param collapses to its value the way people say it; more than one
    keeps the key so nothing is guessed. Four, then `+N`.
    """
    name = block_name(block)
    params = _params_of(block)
    if not params:
        return name
    items = list(params.items())
    if len(items) == 1 and not isinstance(items[0][1], bool):
        # `ADX(14)` reads the way people say it — but a bare `true` says
        # nothing, so a lone flag keeps its key.
        return f"{name}({_value(items[0][1])})"
    shown = items[:max_params]
    rendered = ", ".join(f"{key}={_value(value)}" for key, value in shown)
    extra = len(items) - len(shown)
    if extra > 0:
        rendered += f", +{extra}"
    return f"{name}({rendered})"


# ── Markdown (PLAN §4.6) ──────────────────────────────────────────────


def _validity_word(validation: dict) -> str:
    errors = validation.get("errors") or 0
    warnings = validation.get("warnings") or 0
    if errors:
        return f"{errors} error" if errors == 1 else f"{errors} errors"
    if warnings:
        return f"{warnings} warning" if warnings == 1 else f"{warnings} warnings"
    return "valid" if validation.get("ok", True) else "invalid"


def _header_lines(view: dict) -> list[str]:
    header = view["header"]
    bits = [f"**{view['name'] or 'Untitled'}**"]
    version = view.get("version")
    previous = view.get("previous_version")
    if version is not None:
        if previous is not None and str(previous) != str(version):
            bits.append(f"v{previous} → v{version}")
        else:
            bits.append(f"v{version}")
    if view.get("head_version") is not None:
        bits.append(f"HEAD is v{view['head_version']}")
    bits.append(_validity_word(view["validation"]))
    if header.get("clock"):
        bits.append(str(header["clock"]))
    lines = [" · ".join(bits)]
    universe = header.get("universe")
    if universe and universe.get("label"):
        lines.append(f"universe  {universe['label']}")
    execution = header.get("execution")
    if execution and execution.get("label"):
        lines.append(f"execution {execution['label']}")
    factories = header.get("factories") or []
    if factories:
        shown = factories[:3]
        rendered = ", ".join(f"{f['name']} ({f['steps']} steps)" for f in shown)
        extra = len(factories) - len(shown)
        if extra > 0:
            rendered += f", +{extra} more"
        lines.append(f"factories {rendered}")
    return lines


class _Budget:
    """Blocks left to draw, and what was left undrawn."""

    def __init__(self, limit: int | None) -> None:
        self.left = limit
        self.dropped = 0

    def take(self, n: int) -> bool:
        if self.left is None:
            return True
        if self.left >= n:
            self.left -= n
            return True
        return False

    def drop(self, n: int) -> None:
        self.dropped += n


def _mark_for(block: dict, marks: dict[str, str] | None) -> str:
    if not marks:
        return ""
    return marks.get(str(block.get("id") or ""), "  ")


def _render_list(
    blocks: list[dict],
    indent: str,
    budget: _Budget,
    marks: dict[str, str] | None,
) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        if block.get("type") == "pipeline":
            # A named sub-pipeline is a sequence, not a choice: render it
            # inline exactly as the emitter flattens it, so one DSL reads
            # the same whichever graph shape carried it here.
            for _, children in _groups(block):
                lines.extend(_render_list(children, indent, budget, marks))
            continue
        groups = _groups(block)
        if not groups:
            if budget.take(1):
                lines.append(f"{indent}{_mark_for(block, marks)}{_leaf_line(block)}")
            else:
                budget.drop(1)
            continue
        size = sum(count_blocks(children) for _, children in groups)
        if budget.left is not None and budget.left < size:
            # It does not fit: one row that names its branches with
            # counts, so nothing is hidden without a number (V-14).
            if budget.take(1):
                counts = ", ".join(f"{name} {count_blocks(kids)}" for name, kids in groups)
                kind = "Parallel" if block.get("type") == "parallel" else "Pipeline"
                lines.append(
                    f"{indent}… {kind} · {len(groups)} branches · {size} blocks ({counts})"
                )
            else:
                budget.drop(size)
            continue
        width = max(len(name) for name, _ in groups)
        for index, (name, children) in enumerate(groups):
            tee = "└" if index == len(groups) - 1 else "├"
            if any(_groups(child) for child in children):
                lines.append(f"{indent}{tee} {name}")
                nested = "  " if index == len(groups) - 1 else "│ "
                lines.extend(_render_list(children, indent + nested, budget, marks))
                continue
            chain = " → ".join(
                f"{_mark_for(child, marks)}{_leaf_line(child)}" for child in children
            )
            budget.take(len(children))
            lines.append(f"{indent}{tee} {name.ljust(width)}  {chain}")
    return lines


def _structure_markdown(
    graph: Any,
    *,
    mobile: bool,
    full: bool,
    marks: dict[str, str] | None,
) -> list[str]:
    limit = None if full else (BLOCK_BUDGET_MOBILE if mobile else BLOCK_BUDGET)
    budget = _Budget(limit)
    lines = _render_list(_blocks(graph), "", budget, marks)
    if budget.dropped:
        lines.append(f"… {budget.dropped} more blocks")
    return lines


def _path_prefix(entry: dict, label: str) -> str:
    """`trend_strong › AboveThresholdFilter` — branch names, then the block."""
    path = entry.get("path")
    parts = [str(p) for p in path] if isinstance(path, list) else []
    return " › ".join(parts + [label]) if parts else label


def _entry_label(entry: dict, *, with_params: bool) -> str:
    label = str(entry.get("component") or "")
    if not with_params:
        return label
    params = entry.get("params")
    block = {
        "type": "component",
        "component": label,
        "params": params if isinstance(params, dict) else {},
    }
    return _leaf_line(block)


def _change_markdown(change: dict) -> list[str]:
    lines: list[str] = []
    # Declarations first: a clock, a universe or an execution-mode edit is
    # the thing a user asked for by name, and before Q-1707 it was the one
    # thing this rendering never said.
    for row in declaration_rows(change.get("declarations")):
        lines.append(
            f"~ {row['section_label']} · {row['label']}   {_value(row['a'])} → {_value(row['b'])}"
        )
    for entry in change.get("changed") or []:
        params = entry.get("params")
        deltas = []
        if isinstance(params, dict):
            for key, pair in params.items():
                if isinstance(pair, dict) and ("old" in pair or "new" in pair):
                    deltas.append(f"{key} {_value(pair.get('old'))} → {_value(pair.get('new'))}")
        detail = ", ".join(deltas)
        head = _path_prefix(entry, _entry_label(entry, with_params=False))
        lines.append(f"~ {head}" + (f"   {detail}" if detail else ""))
    for entry in change.get("added") or []:
        lines.append(f"+ {_path_prefix(entry, _entry_label(entry, with_params=True))}")
    for entry in change.get("removed") or []:
        lines.append(f"- {_path_prefix(entry, _entry_label(entry, with_params=False))}")
    total = change.get("total")
    if isinstance(total, int):
        # BLOCKS unchanged, over blocks touched — `touched` also counts
        # declaration keys now, and a declaration is not a block.
        unchanged = max(total - _blocks_touched(change), 0)
        lines.append(f"  {unchanged} blocks unchanged")
    return lines


def _marks_map(change: dict | None) -> dict[str, str] | None:
    if not change:
        return None
    marks: dict[str, str] = {}
    for entry in change.get("changed") or []:
        marks[str(entry.get("id"))] = "~ "
    for entry in change.get("added") or []:
        marks[str(entry.get("id"))] = "+ "
    return marks


def _factories_markdown(graph: Any) -> list[str]:
    """Each factory's steps, drawn in full — the `--full` half of §4.6.

    The inline rendering carries a factory as one chip (name + step
    count); everything is only ever hidden behind a number, and this is
    where the number is spent.
    """
    factories = _factory_defs(graph)
    if not factories:
        return []
    lines = ["", "factories"]
    for factory, row in zip(factories, factory_rows(graph), strict=False):
        params = ", ".join(row["params"])
        lines.append(f"{row['name']}({params})" if params else str(row["name"]))
        lines.extend(_render_list(_factory_body(factory), "  ", _Budget(None), None))
    return lines


def _preview_receipt_line(view: dict, graph: Any) -> str:
    """`Preview · 5 blocks · valid` (BUILD §2.3).

    `_validity_word` returns exactly ONE word, so a preview never reads
    both `valid` and a warning count; with errors the block count is
    dropped — the errors are the news, and they follow in `validation`.
    """
    validation = view.get("validation") or {}
    word = _validity_word(validation)
    if validation.get("errors"):
        return f"Preview · {word}"
    total = count_blocks(_blocks(graph))
    return f"Preview · {total} block{'' if total == 1 else 's'} · {word}"


def _receipt_markdown(view: dict, graph: Any) -> str:
    """A strategy receipt: ONE line, plus the link line when there IS a
    target (BUILD §2.3 / §2.4).

    Reached only when the view has no `change` to draw — a dry run, or a
    create / fork / save the caller asked to present as a receipt. A
    receipt WITH a change keeps the existing change rendering, which is
    already one line per hunk.
    """
    if str(view.get("status") or "").upper() == "PREVIEW":
        lines = [_preview_receipt_line(view, graph)]
    else:
        lines = _header_lines(view)[:1]
    if view.get("url_line"):
        lines.append(str(view["url_line"]))
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(
    view: dict,
    graph: Any,
    *,
    mobile: bool = False,
    full: bool = False,
) -> str:
    """The text rendering every text host receives (PLAN §4.6).

    Tree characters, never tables: a table wraps badly in a terminal and
    a card is not what a terminal has.
    """
    change = view.get("change")
    size = view.get("size")
    if size == "receipt" and not change:
        return _receipt_markdown(view, graph)
    lines = _header_lines(view)
    lines.append("")
    if change and size in ("change", "receipt"):
        lines.extend(_change_markdown(change))
    else:
        marks = _marks_map(change) if view.get("marks") else None
        lines.extend(_structure_markdown(graph, mobile=mobile, full=full, marks=marks))
        if change and view.get("marks"):
            for entry in change.get("removed") or []:
                lines.append(f"- {_path_prefix(entry, _entry_label(entry, with_params=False))}")
    if full:
        lines.extend(_factories_markdown(graph))
    evidence = view.get("evidence")
    if evidence:
        lines.append("")
        lines.append(_evidence_line(evidence, head_version=view.get("version")))
    if view.get("url_line"):
        lines.append("")
        lines.append(str(view["url_line"]))
    return "\n".join(lines).rstrip() + "\n"


def _version_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("#").isdigit():
        return int(value.strip().lstrip("#"))
    return None


def evidence_version_note(evidence: Any, head_version: Any) -> str | None:
    """`latest evidence is from v2; v4 not backtested` — or None.

    The strategy view shows the LATEST completed run (Q-1790), which after a
    save is a run of an older version. The numbers were printed unlabelled,
    so a v4 strategy's text reported v2's Sharpe as its own (review 06 §4,
    the ChatGPT agent's priority 5). Silent when the run IS the head
    version, or when either version is unknown — never a guessed label.
    """
    if not isinstance(evidence, dict):
        return None
    ran = _version_number(evidence.get("version"))
    head = _version_number(head_version)
    if ran is None or head is None or ran == head:
        return None
    return f"latest evidence is from v{ran}; v{head} not backtested"


#: One minus, everywhere (U+2212) — the card system's number rule.
MINUS = "−"


def _signed(value: Any, suffix: str = "") -> str:
    """A signed number whose sign is a glyph, never only a colour."""
    if value is None:
        return "—"
    text = _num(value)
    if text.startswith("-"):
        return MINUS + text[1:] + suffix
    return "+" + text + suffix


def _evidence_line(evidence: dict, *, head_version: Any = None) -> str:
    # Every number through the backtest view's ONE display owner
    # (`_tile_display`, Q-1710) at the card's precision: `_num` stringified a
    # float verbatim and printed "Sharpe 2.7447805047596745" (Q-1877).
    from ._backtest_view import _tile_display

    def shown(key: str, value: Any) -> str:
        return _tile_display(key, {key: value}) if value is not None else "—"

    window = evidence.get("window") or {}
    bits = [
        f"Sharpe {shown('sharpe', evidence.get('sharpe'))}",
        f"return {shown('total_return_pct', evidence.get('total_return_pct'))}",
        f"max DD {shown('max_drawdown_pct', evidence.get('max_drawdown_pct'))}",
        # The count and its word, as the evidence carries them (by era).
        f"{shown('trades', evidence.get('total_trades'))} "
        f"{evidence.get('count_label') or 'trades'}",
    ]
    # The run's version rides the line (review 06 §4): the numbers belong to
    # the version that ran, which is not always the version on screen.
    ran = _version_number(evidence.get("version"))
    line = ("backtest  " if ran is None else f"backtest v{ran}  ") + " · ".join(bits)
    # The one display rule (spec 03 §2.5): a range ends on its LAST COVERED
    # day, never the exclusive end.
    start = window.get("start")
    last = window.get("last_bar")
    if not last and window.get("end"):
        from ._backtest_view import window_block

        last = window_block(start, window.get("end")).get("last_bar")
    if start and last:
        line += f" · {start} → {last}"
        if evidence.get("sub_window"):
            # Q-1879: a split / stress window stands in only when no
            # full-window run exists, and it says so.
            line += " (sub-window)"
    net_of = evidence.get("net_of")
    if net_of:
        line += " · net of " + ", ".join(str(n) for n in net_of)
    note = evidence_version_note(evidence, head_version)
    if note:
        line += f" — {note}"
    return line


# ── The view ──────────────────────────────────────────────────────────


def source_block(source: Any) -> dict | None:
    """The `view.source` block — the DSL text the Code tab renders, or None.

    Capped, because this rides inside the envelope. A truncated block says so
    and says how much is missing, so the card can be honest rather than
    silently showing a partial strategy as if it were whole.
    """
    if not isinstance(source, str):
        return None
    text = source.strip("\n")
    if not text.strip():
        return None
    total = len(text)
    lines = text.count("\n") + 1
    if total <= MAX_SOURCE_CHARS:
        return {"text": text, "lines": lines, "truncated": False}
    head = text[:MAX_SOURCE_CHARS]
    # Cut on a line boundary so the tab never ends mid-token.
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    return {
        "text": head,
        "lines": lines,
        "truncated": True,
        "shown_lines": head.count("\n") + 1,
    }


def requested_source_text(source: Any, *, version: Any = None) -> str | None:
    """The text block's `source:` line — the DSL `include_source=true` fetched.

    `source` is the envelope's top-level `source` (keel-api's
    `{"source": "<dsl>", ...}` read, or a bare string); `view.source` — the
    Code tab's copy, present on every read — never reaches here, so the line
    appears only when the caller asked. Capped by the Code tab's own owner
    (`source_block`), and a capped source says so: a model must never plan an
    edit from a partial strategy it took as whole.
    """
    text = source.get("source") if isinstance(source, dict) else source
    block = source_block(text)
    if block is None:
        return None
    ref = str(version).strip() if version not in (None, "") else "HEAD"
    count = block["lines"]
    head = f"the DSL at {ref}, {count} line{'' if count == 1 else 's'}"
    if block.get("truncated"):
        head += (
            f"; the first {block['shown_lines']} are shown, the rest is in structuredContent.source"
        )
    return f"{head}\n```python\n{block['text']}\n```"


def resize_view(view: dict, size: str) -> dict:
    """Re-render an already-built strategy view at a different size.

    The `present` half that `build_view(size_override=)` cannot serve:
    `keel_strategy_fork` and a persisted `keel_strategy_compose` read
    their view back through `strategy_get.view_for_strategy`, which owns
    the graph fetch and not the sizing. Rather than thread an override
    through that seam, they hand the finished view here — the structure
    is in `view["structure"]`, so the markdown is re-rendered from the
    SAME graph the view was built from rather than a second read.

    Marks never survive a shrink: a receipt has nothing to mark.
    """
    if not isinstance(view, dict) or view.get("size") == size:
        return view
    out = dict(view)
    out["size"] = size
    if size != "structure":
        out.pop("marks", None)
    out["markdown"] = render_markdown(out, out.get("structure"))
    return out


def build_parse_error_view(
    issue: dict,
    *,
    name: str | None = None,
    source: Any = None,
    url: str | None = None,
    object_id: str | None = None,
) -> dict:
    """The `view` for a dry run whose source did not PARSE (Q-1840).

    `build_view` needs a graph, and a source the parser rejects has none —
    so before this a parse failure carried NO view at all: the text block
    on claude.ai fell back to the raw JSON envelope, and the card, given
    nothing, drew "Untitled · 0 blocks · No backtest yet". The same event
    as a validation error, so the same shape as one: a PREVIEW receipt
    named as the caller named it, one error, the parser's own message (it
    carries the line and column), and the `rule:<CODE>` pointer every
    other issue line carries. `structure` is an empty graph and
    `parse_error` says so, so a renderer draws the error instead of an
    empty pipeline.
    """
    code = str(issue.get("code") or "PARSE_ERROR")
    message = str(issue.get("message") or "The source did not parse.")
    view: dict[str, Any] = {
        "kind": KIND_STRATEGY,
        "name": name,
        "version": None,
        "status": "PREVIEW",
        "validation": {"ok": False, "errors": 1, "warnings": 0},
        "header": {"clock": None, "universe": None, "execution": None, "factories": []},
        "structure": {"blocks": []},
        "change": None,
        "evidence": None,
        "categories": {},
        "size": "receipt",
        "status_text": None,
        "error": message,
        "parse_error": True,
    }
    if url:
        view["url_line"] = f"View in Keel: {url}"
    view.update(election_key(object_id))
    lines = [" · ".join([f"**{name or 'Untitled'}**", "Preview", "1 error — did not parse"])]
    lines.append(message)
    suggestion = issue.get("suggestion")
    if isinstance(suggestion, str) and suggestion.strip():
        lines.append(f"fix: {suggestion.strip()}")
    # The rule pointer rides the text block's validation line
    # (`_mcp_adapter._issue_line`), which every host appends; the card and
    # a terminal need the staging fact, because it explains why a second
    # dry run can report errors this one did not (Q-1841).
    lines.append(
        f"Checked as far as the parse ({code}); component checks run once the source parses."
    )
    if view.get("url_line"):
        lines.append(str(view["url_line"]))
    view["markdown"] = "\n".join(lines) + "\n"
    src = source_block(source)
    if src is not None:
        view["source"] = src
    return view


def build_view(
    graph: Any,
    metadata: Any,
    *,
    change: Any = None,
    evidence: Any = None,
    mobile: bool = False,
    full: bool = False,
    url: str | None = None,
    previous_version: Any = None,
    source: Any = None,
    size_override: str | None = None,
    object_id: str | None = None,
    head_version: Any = None,
) -> dict | None:
    """The `view` block for one strategy, or None without a graph.

    `head_version` is set when the view describes a version that is NOT
    HEAD (`keel_strategy_get(version=1)`, ChatGPT R4 #1): every field then
    describes that version, and HEAD rides only as `view.head_version` and
    the header's `HEAD is v4` — never mixed into the structure or evidence.

    Pure except for the election key's clock: `graph` is whatever the
    server derived, `metadata` is the row it came with, and everything
    else is a projection of those two.

    `size_override` is what `present` resolves to (BUILD §2.4) — the
    caller says how much to show, `choose_size` says what the event
    deserves, and the override wins. Marks (the full form with change
    marks) only survive an override to `structure`; a receipt never
    carries them.

    `object_id` is the supersession election key's subject when the
    caller knows it and the metadata row does not (a dry run against an
    existing strategy is the case that matters).
    """
    if not isinstance(graph, dict) or not _blocks(graph):
        return None
    meta = metadata if isinstance(metadata, dict) else {}

    header = {
        "clock": clock_label(graph.get("globals") or graph.get("globals_")),
        "universe": universe_block(graph.get("universe")),
        "execution": execution_block(graph.get("execution")),
        "factories": factory_rows(graph),
    }
    normalized_change = _normalize_change(change, graph)
    total = count_blocks(_blocks(graph))
    size, marks = choose_size(normalized_change, total)
    if size_override:
        size = size_override
        marks = marks and size == "structure"
    if evidence is None:
        evidence = evidence_from_metadata(meta)

    version = meta.get("current_sequence")
    if version is None:
        version = meta.get("version")
    if previous_version is None and normalized_change is not None:
        previous_version = normalized_change.get("from_version")
    ran = _version_number(evidence.get("version")) if isinstance(evidence, dict) else None
    shown = _version_number(version)
    if ran is not None and shown is not None and ran != shown:
        # A machine-readable twin of the "latest evidence is from v2" note
        # (ChatGPT R4 #5): the card demotes the numbers, and the text
        # block's `evidence_matches_version: false` line states it. Absent
        # when the run IS the version shown — no key, no claim.
        evidence = {**evidence, "matches_version": False}

    view: dict[str, Any] = {
        "kind": KIND_STRATEGY,
        "name": meta.get("name") or meta.get("strategy_name") or graph.get("name"),
        "version": version,
        "status": meta.get("status"),
        "validation": validation_block(meta),
        "header": header,
        "structure": graph,
        "change": normalized_change,
        "evidence": evidence,
        "categories": categories_for(graph),
        "size": size,
        "status_text": status_text_for(meta),
    }
    if previous_version is not None:
        view["previous_version"] = previous_version
    if head_version is not None and _version_number(head_version) != shown:
        view["head_version"] = head_version
    if marks:
        view["marks"] = True
    if url:
        view["url_line"] = f"View in Keel: {url}"
    # The election key (BUILD §2.9): server-minted, on every view.
    view.update(election_key(object_id or meta.get("strategy_id") or meta.get("id")))
    view["markdown"] = render_markdown(view, graph, mobile=mobile, full=full)
    # After the markdown deliberately: the source is for the card's Code tab,
    # and every text host already relays the structure in prose.
    src = source_block(source)
    if src is not None:
        view["source"] = src
    return view
