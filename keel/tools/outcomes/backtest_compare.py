"""`keel_backtest_compare` — compare two backtest runs side by side.

Q-0581 / register key `no-turnover-cost-feedback`: no surface reported
the turnover/fee consequence of an edit, so a user could make a strategy
strictly worse in exactly the dimensions the tool warns about. This tool
diffs BOTH the metric set (with a dedicated cost/turnover profile) AND
the pipeline/execution spec of the two runs, so a cost regression is one
command away instead of two eyeballed summaries.

Metric values are copied verbatim from each run's stored metrics — this
tool never recomputes a measurement (one-computation-owner rule); the
spec diff reuses the structural differ and adds the declaration diff
(Execution/Globals/Universe) from `_declarations`, because the
structural differ covers pipeline steps only. That module is the ONE
owner of the declaration diff — `keel_strategy_compose`'s change view
reads the same function (Q-1707).
"""

from __future__ import annotations

import re
from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._declarations import DECLARATION_SECTIONS, declaration_view, diff_declarations
from ._surface_hints import tool_ref, usage_hint
from .open_in_app import app_url_for


# Ordered comparison rows. Performance first, then the cost/turnover
# profile the tool exists to make legible. Copied verbatim per run.
_PERFORMANCE_KEYS = (
    "sharpe_ratio",
    "sharpe_ratio_active",
    "sortino_ratio",
    "calmar_ratio",
    "total_return",
    "max_drawdown_pct",
    "win_rate",
    "position_win_rate",
    "profit_factor",
    "position_profit_factor",
    "expectancy",
    "end_value",
    "funding_included",
)

_COST_PROFILE_KEYS = (
    "total_trades",
    "positions",
    "resizes",
    "total_orders",
    "turnover",
    "total_fees_paid",
    "fees_pct_of_initial",
    "fees_pct_of_gross_profit",
    "fees_pct_of_net_profit",
)


def _row(key: str, ma: dict, mb: dict) -> dict[str, Any]:
    va, vb = ma.get(key), mb.get(key)
    row: dict[str, Any] = {"a": va, "b": vb}
    if (
        isinstance(va, (int, float))
        and isinstance(vb, (int, float))
        and not isinstance(va, bool)
        and not isinstance(vb, bool)
    ):
        row["delta"] = vb - va
    return row


def _compare_metrics(ma: dict, mb: dict) -> tuple[dict, dict]:
    from ._backtest_view import era_metrics, signed_drawdown

    # One drawdown sign on every model-visible block (Q-1805); the trade
    # keys read by era (trade-metrics spec 01 §4.2), so a row compares like
    # with like and a delta is taken only between two recorded values.
    ma, mb = signed_drawdown(era_metrics(ma)), signed_drawdown(era_metrics(mb))
    perf = {k: _row(k, ma, mb) for k in _PERFORMANCE_KEYS if k in ma or k in mb}
    cost = {k: _row(k, ma, mb) for k in _COST_PROFILE_KEYS if k in ma or k in mb}
    return perf, cost


def _fetch_detail(client, backtest_id: str) -> dict:
    try:
        detail = client.get(f"/v1/backtests/{backtest_id}")
    except NotFoundError:
        raise NotFoundError(
            f"Backtest {backtest_id} not found.",
            suggestion=(
                "Verify both backtest_ids — each is the backtest_run_id returned "
                f"when the backtest was submitted ({tool_ref('keel_backtest_run')})."
            ),
        )
    if not isinstance(detail, dict) or not detail.get("metrics"):
        state = (detail.get("status") or "unknown").lower() if isinstance(detail, dict) else "?"
        raise KeelError(
            f"Backtest {backtest_id} has no metrics yet (status: {state}).",
            error_code="backtest_not_comparable",
            exit_code=3,
            suggestion=(
                f"Both runs must be complete. Watch {backtest_id} with "
                f"{tool_ref('keel_backtest_watch')} and retry once it reaches a "
                "terminal state."
            ),
        )
    return detail


#: The tool's arity. Two is an A/B; eight is the point past which a
#: comparison stops being readable on any host and starts being a sweep.
COMPARE_MIN_IDS = 2
COMPARE_MAX_IDS = 8

#: Curve resolution per run, as a BUCKET count — `GET /curve?points=N`
#: downsamples into N buckets and returns **N + 1** samples, because both
#: endpoints are inclusive. 120 buckets is 121 points on the wire, and
#: the budget arithmetic below counts the 121 (Q-1708: the contract said
#: "120 points", the wire carried 121, and nothing noticed).
COMPARE_CURVE_POINTS = 120

#: The per-ENVELOPE curve budget, across every run in the set. A compare
#: is the one tool whose size scales with its arity, and a per-run
#: constant does not bound a per-envelope cap: at eight runs, 121 points
#: × ~45 B × 8 is 44 KB of curve inside a 60 KB envelope, which is how a
#: real 8-run result reached 75 KB on staging while the guard was green
#: (Q-1708). Spent evenly, capped per run at the single-run resolution.
#:
#: 424 = 8 × 53, the resolution an 8-run compare already had (Q-2441). The
#: budget used to divide 480 by N + 1 for a BTC hold line that is only
#: present when `holds` asks; that phantom line absorbed the envelope's
#: fixed part, which grew after Q-1708. Counting the lines the envelope
#: really carries (below) at 424 keeps every default no larger than it was
#: (2-3 runs 120, 4 runs 106, 8 runs 53) and keeps 8 runs + 3 holds inside
#: the 60 KB host budget.
COMPARE_CURVE_TOTAL_POINTS = 424


def compare_curve_points(n_runs: int) -> int:
    """Buckets to request per run so the whole envelope stays in budget."""
    if n_runs <= 0:
        return COMPARE_CURVE_POINTS
    return min(COMPARE_CURVE_POINTS, COMPARE_CURVE_TOTAL_POINTS // n_runs)


#: Declaration keys the run LABELS may name, and the listing-safe word
#: each reads as. `None` means the value alone (`v1 · every_bar`).
#: A key absent from this map is a term that cannot be NAMED (the set
#: falls back to commit messages, then bare `v3`) rather than emitting an
#: unvetted identifier into copy the listed surface shows — `min_trade_size`
#: is the live example: its own name carries two forbidden tokens.
_DECLARATION_LABELS: dict[str, str | None] = {
    "rebalance": None,
    "buffer_threshold": "buffer",
    "buffer_mode": "buffer mode",
    "rebalance_method": "method",
    "on_change_tolerance": "tolerance",
    "min_trade_size": "min order",
    "target_timeframe": None,
    "bar_offset": "offset",
}

#: Declaration sections whose keys label through the map above. Universe
#: is handled apart (`_declaration_terms`): only `top_n` reads as a label
#: (`top 10`); any other universe difference blocks the structural label.
_LABEL_SECTIONS = ("execution", "globals")


def _grouped(labels: list[str], values: list[Any]) -> list[tuple[Any, list[str]]]:
    """Runs grouped by a value, first-seen order (the baseline's group first)."""
    groups: list[tuple[Any, list[str]]] = []
    for label, value in zip(labels, values):
        for known, names in groups:
            if known == value:
                names.append(label)
                break
        else:
            groups.append((value, [label]))
    return groups


def _carry_words(value: Any, n: int) -> str:
    if value is True:
        return "includes carry" if n == 1 else "include carry"
    if value is False:
        return "excludes carry" if n == 1 else "exclude carry"
    return "does not record carry" if n == 1 else "do not record carry"


def _prose_names(details: list[dict], labels: list[str], shared_strategy: Any) -> list[str]:
    """How a comparability sentence names each run: `v2` within one
    strategy, the run label otherwise — and the label whenever the short
    names would not tell the runs apart."""
    if shared_strategy is not None:
        stems = [
            f"v{d.get('sequence_number')}" if d.get("sequence_number") is not None else None
            for d in details
        ]
        if all(stems) and len(set(stems)) == len(stems):
            return [str(stem) for stem in stems]
    return labels


def _comparability(
    details: list[dict], labels: list[str]
) -> tuple[list[str], list[str], dict | None]:
    """`(warnings, notes, overlap)` for the whole set (Q-1802).

    Set-level, naming the runs by their labels: the pairwise A/B version
    said "A: … B: …" for two runs and repeated itself per pair for eight.
    The window sentence and its severity come from `compare_windows`, the
    one owner; engines and carry inclusion are warnings whenever they
    differ, because either changes what a number means.
    """
    from ._backtest_view import MIXED_ERA_LINE, _names, compare_windows, trade_era, window_block

    warnings: list[str] = []
    notes: list[str] = []
    windows = [window_block(d.get("start_date"), d.get("end_date")) for d in details]
    verdict = compare_windows(windows, labels)
    overlap: dict | None = None
    if verdict is not None:
        (notes if verdict["severity"] == "note" else warnings).append(verdict["sentence"])
        if verdict["overlap"]:
            overlap = {**verdict["overlap"], "severity": verdict["severity"]}

    engines = _grouped(labels, [d.get("engine") for d in details])
    if len(engines) > 1:
        runs = "; ".join(
            f"{_names(names)} {'uses' if len(names) == 1 else 'use'} "
            f"{engine or 'an unrecorded engine'}"
            for engine, names in engines
        )
        warnings.append(f"Engines differ — {runs}. Cost modelling may differ between engines.")

    carry = _grouped(labels, [(d.get("metrics") or {}).get("funding_included") for d in details])
    if len(carry) > 1:
        runs = "; ".join(
            f"{_names(names)} {_carry_words(value, len(names))}" for value, names in carry
        )
        warnings.append(f"Carry inclusion differs — {runs}. PnL bases differ.")
    # Runs whose counts were recorded under different eras (trade-metrics
    # spec 01 §6.2): each row is like with like, and one line says why a
    # cell reads "—".
    if len({trade_era(d.get("metrics")) for d in details}) > 1:
        notes.append(MIXED_ERA_LINE)
    return warnings, notes, overlap


# ── Settings that change results, in words (Q-1802) ──────────────────
#
# A claude.ai compare of a library fork against the agent's own baseline
# said only "Different strategies — structure and universe may differ."
# while the two ran different EXECUTION (buffered 0.2 to the band edge vs
# every bar to the centre): template defaults differ, and nothing named
# it. These sentences name each setting that differs, per run, in words —
# never a raw key or a raw value like `to_edge`.

#: Execution keys whose value only matters when the run rebalances in a band.
_BAND_KEYS = ("buffer_threshold", "buffer_mode", "rebalance_method")

#: Every execution key a comparison names, in the order a sentence says them.
_EXECUTION_KEYS = ("rebalance", *_BAND_KEYS, "min_trade_size", "on_change_tolerance")

_REBALANCE_WORDS = {
    "every_bar": "rebalances every bar",
    "on_change": "rebalances when its target changes",
    "buffered": "rebalances only outside a band",
}
#: What a band is a fraction OF, per `buffer_mode` (spec.py's own meaning).
_BAND_BASE = {
    "relative": "of target",
    "absolute": "of portfolio value",
    "reference": "of recent mean weight",
}
#: Where a band breach trades to, per `rebalance_method`.
_TRADES_TO = {"to_edge": "only to the band edge", "to_center": "back to target"}


def _pct_words(value: Any) -> str | None:
    """`20%` from `0.2` — a fraction said as a percentage, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"{value * 100:g}%"


def _execution_phrase(view: dict, shown: list[str]) -> str:
    """How ONE run executes, in words, saying only the `shown` keys.

    When how a run rebalances is itself what differs, a banded run is
    described whole — `rebalances only outside a 20% band of target,
    trading only to the band edge` — because its band settings are the
    difference. Band settings of a run that never rebalances in a band
    are inert and never named.
    """
    rebalance = view.get("rebalance")
    banded = rebalance == "buffered"
    width = _pct_words(view.get("buffer_threshold"))
    base = _BAND_BASE.get(view.get("buffer_mode"))
    trades_to = _TRADES_TO.get(view.get("rebalance_method"))
    bits: list[str] = []
    if "rebalance" in shown or not banded:
        if banded and width:
            text = f"rebalances only outside a {width} band" + (f" {base}" if base else "")
            bits.append(text + (f", trading {trades_to}" if trades_to else ""))
        else:
            bits.append(_REBALANCE_WORDS.get(rebalance) or "uses another rebalance rule")
    else:
        if "buffer_threshold" in shown and width:
            bits.append(f"uses a {width} band")
        if "buffer_mode" in shown and base:
            bits.append(f"measures its band {base}")
        if "rebalance_method" in shown and trades_to:
            bits.append(f"trades {trades_to}")
    minimum = _pct_words(view.get("min_trade_size"))
    if "min_trade_size" in shown and minimum is not None:
        bits.append(f"skips orders under {minimum} of the portfolio")
    tolerance = view.get("on_change_tolerance")
    if "on_change_tolerance" in shown and rebalance == "on_change" and tolerance is not None:
        bits.append(f"ignores target changes under {tolerance:g}")
    return ", ".join(bits) if bits else "executes the same way"


def _describe_groups(labels: list[str], phrases: list[str]) -> str:
    """`v1 and v2 <phrase>; v3 <phrase>` — runs grouped by what they do."""
    groups = _grouped(labels, phrases)
    from ._backtest_view import _names

    return "; ".join(f"{_names(names)} {phrase}" for phrase, names in groups)


def _execution_difference(declarations: list[dict], labels: list[str]) -> str | None:
    """`Execution differs — v1 rebalances only outside a 0.2 relative band,
    trading to its edge; v2 rebalances every bar.`"""
    views = [d.get("execution") or {} for d in declarations]
    shown = [k for k in _EXECUTION_KEYS if len({repr(v.get(k)) for v in views}) > 1]
    if not shown:
        return None
    phrases = [_execution_phrase(view, shown) for view in views]
    if len(set(phrases)) <= 1:
        return None
    return f"Execution differs — {_describe_groups(labels, phrases)}."


_OFFSET_RE = re.compile(r"^\s*(\d+)\s*(h|min|m)\s*$")


def _daily_close(tf: Any, offset: Any) -> str | None:
    """`12:00` for a `1d` clock offset `12h`; `00:00` with no offset; None
    for any other clock or an offset this cannot read (never a guess)."""
    if str(tf or "").strip() != "1d":
        return None
    if not offset:
        return "00:00"
    match = _OFFSET_RE.match(str(offset))
    if match is None:
        return None
    minutes = int(match.group(1)) * (60 if match.group(2) == "h" else 1)
    if minutes >= 24 * 60:
        return None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clock_difference(declarations: list[dict], labels: list[str]) -> str | None:
    views = [d.get("globals") or {} for d in declarations]
    phrases = []
    for view in views:
        tf, offset = view.get("target_timeframe"), view.get("bar_offset")
        close = _daily_close(tf, offset)
        if close is not None:
            # ChatGPT R4 #6: "offset 12h" vs nothing read as noise; the fact
            # is when the day the signal sees ends.
            phrases.append(f"closes its daily bar at {close} UTC")
            continue
        bits = [f"runs on {tf} bars" if tf else "runs on its loader's bars"]
        if offset:
            bits.append(f"offset {offset}")
        phrases.append(", ".join(bits))
    if len(set(phrases)) <= 1:
        return None
    return f"Clocks differ — {_describe_groups(labels, phrases)}."


def _universe_difference(declarations: list[dict], labels: list[str]) -> str | None:
    views = [d.get("universe") or {} for d in declarations]
    if len({repr(sorted(v.items())) for v in views}) <= 1:
        return None
    others = [{k: val for k, val in v.items() if k != "top_n"} for v in views]
    if len({repr(sorted(o.items())) for o in others}) <= 1:
        phrases = [
            f"takes the top {v.get('top_n')}" if v.get("top_n") else "takes no top-N cut"
            for v in views
        ]
        return f"Universes differ — {_describe_groups(labels, phrases)}."
    return "Universes differ — the runs declare different asset universes."


def _settings_words(detail: dict) -> dict[str, str]:
    """A run's effective capital / cost / exposure settings, in words.

    `backtest_config` on the wire is SPARSE — only what the caller set
    (`BacktestConfig.as_overrides`) — so an absent key IS the default,
    resolved by `_backtest_view.effective_backtest_config` — the one owner
    the per-run `cost_model` reads too (Q-2270).
    """
    from ._backtest_view import effective_backtest_config

    value = effective_backtest_config(detail)
    if value is None:
        raise ValueError("backtest config model unavailable")
    return {
        "init_cash": f"${value['init_cash']:,.0f} capital",
        "fees": f"{value['fees'] * 10_000:g} bps fees",
        "slippage": f"{value['slippage'] * 10_000:g} bps slippage",
        "leverage": f"a {value['leverage']:g}× margin cap",
    }


def _settings_difference(details: list[dict], labels: list[str]) -> str | None:
    """`Backtest settings differ — v1: $10,000 capital; v2: $50,000 capital.`"""
    try:
        words = [_settings_words(d) for d in details]
    except Exception:  # noqa: BLE001 — an unreadable config is not a verdict
        return None
    keys = [
        k for k in ("init_cash", "fees", "slippage", "leverage") if len({w[k] for w in words}) > 1
    ]
    if not keys:
        return None
    phrases = ["uses " + ", ".join(w[k] for k in keys) for w in words]
    return f"Backtest settings differ — {_describe_groups(labels, phrases)}. Every net figure moves with them."


def _pipeline_matches(spec_diff: Any) -> bool | None:
    """Whether a spec diff found NO pipeline change; None when it cannot say."""
    if not isinstance(spec_diff, dict):
        return None
    pipeline = spec_diff.get("pipeline")
    if not isinstance(pipeline, dict) or pipeline.get("error"):
        return None
    return not any(
        value for key, value in pipeline.items() if isinstance(value, list) and key != "path"
    )


def _cross_strategy_sentence(spec_diffs: list[Any], declarations: list[dict]) -> str:
    """What the runs of DIFFERENT strategies share, from the spec diff itself
    (ChatGPT R4 #6). "structure and universe may differ" was said even when
    Keel's own diff showed the same pipeline and universe with one clock
    difference — a hedge the tool had the facts to replace. The clock,
    execution and universe specifics ride their own sentences
    (`_declaration_differences`); this one says what is the SAME. Without a
    diff for every run, the hedge stands."""
    fallback = "Different strategies — structure and universe may differ."
    verdicts = [_pipeline_matches(d) for d in spec_diffs[1:]]
    if not verdicts or any(v is None for v in verdicts) or len(declarations) != len(spec_diffs):
        return fallback
    same_pipeline = all(verdicts)
    universes = {repr(sorted((d.get("universe") or {}).items())) for d in declarations}
    same_universe = len(universes) == 1
    if same_pipeline and same_universe:
        return "Different strategies with the same pipeline and universe."
    if same_pipeline:
        return "Different strategies with the same pipeline; their universes differ."
    if same_universe:
        return "Different strategies with the same universe; their pipelines differ."
    return "Different strategies — their pipelines and universes differ."


def _distinct_labels(labels: list[str], details: list[dict]) -> list[str]:
    """Column labels a reader can tell apart (ChatGPT R4: two columns both
    read "HYPE Long/Cash MACD Trend"). A repeated name gains its run's
    version; still repeated, its order among the same-named runs by when
    they ran — "earlier"/"later" for two, "#1".."#N" beyond."""
    from collections import Counter

    counts = Counter(labels)
    if all(n == 1 for n in counts.values()):
        return labels
    out = list(labels)
    for i, (label, detail) in enumerate(zip(labels, details)):
        seq = detail.get("sequence_number")
        if counts[label] > 1 and seq is not None:
            out[i] = f"{label} · v{seq}"
    counts = Counter(out)
    for label in [label for label, n in counts.items() if n > 1]:
        group = [i for i, value in enumerate(out) if value == label]
        order = sorted(group, key=lambda i: str(details[i].get("queued_at") or ""))
        words = (
            ["earlier", "later"] if len(order) == 2 else [f"#{n + 1}" for n in range(len(order))]
        )
        for word, i in zip(words, order):
            out[i] = f"{label} ({word})"
    return out


def _declaration_differences(
    details: list[dict], declarations: list[dict], labels: list[str]
) -> list[str]:
    """Execution / clock / universe declarations that differ, in words."""
    if not declarations or len(declarations) != len(details):
        return []
    lines = (
        _execution_difference(declarations, labels),
        _clock_difference(declarations, labels),
        _universe_difference(declarations, labels),
    )
    return [line for line in lines if line]


# ── Spec diff (pipeline steps + declarations) ─────────────────────────


def _fetch_source(client, detail: dict) -> str | None:
    strategy_id = detail.get("strategy_id")
    commit_id = detail.get("commit_id")
    if not strategy_id or not commit_id:
        return None
    resp = client.get(f"/v1/strategies/{strategy_id}/versions/{commit_id}/source")
    if isinstance(resp, dict):
        return resp.get("source")
    return None


#: The declaration diff moved to `_declarations` at Q-1707: the compose
#: change view needs the SAME measurement, and two implementations of one
#: measurement is a defect. These names stay so this module's readers (and
#: `_spec_diff` below) are unchanged; the computation has one owner.
_declaration_view = declaration_view
_diff_declarations = diff_declarations


def _spec_diff(source_a: str, source_b: str) -> dict[str, Any]:
    """Structural pipeline diff + declaration diff. Never raises upward —
    callers wrap; a spec-diff failure degrades to a note and must not
    kill the metrics comparison."""
    from keel.tools.local import strategy_diff as local_diff
    from pipeline_engine.dsl import parse_strategy

    pipeline = local_diff(source_a=source_a, source_b=source_b)
    parsed_a = parse_strategy(source_a)
    parsed_b = parse_strategy(source_b)
    declarations = _diff_declarations(_declaration_view(parsed_a), _declaration_view(parsed_b))
    return {"pipeline": pipeline, "declarations": declarations}


def _summarize(perf: dict, cost: dict, spec_diff: dict | None) -> str:
    parts: list[str] = []
    sharpe = perf.get("sharpe_ratio", {})
    if isinstance(sharpe.get("a"), (int, float)) and isinstance(sharpe.get("b"), (int, float)):
        parts.append(f"Sharpe {sharpe['a']:.2f}→{sharpe['b']:.2f}")
    fpi = cost.get("fees_pct_of_initial", {})
    if isinstance(fpi.get("a"), (int, float)) and isinstance(fpi.get("b"), (int, float)):
        parts.append(f"fees {fpi['a']:.2f}%→{fpi['b']:.2f}% of capital")
    gross = cost.get("fees_pct_of_gross_profit", {})
    if isinstance(gross.get("a"), (int, float)) and isinstance(gross.get("b"), (int, float)):
        parts.append(f"fee drag {gross['a']:.1f}%→{gross['b']:.1f}% of gross PnL")
    turnover = cost.get("turnover", {})
    if isinstance(turnover.get("a"), (int, float)) and isinstance(turnover.get("b"), (int, float)):
        parts.append(f"turnover {turnover['a']:.1f}x→{turnover['b']:.1f}x")
    if spec_diff:
        decl = spec_diff.get("declarations") or {}
        for section, changes in decl.items():
            for key, change in changes.items():
                parts.append(f"{section}.{key} {change['a']}→{change['b']}")
        pipeline = spec_diff.get("pipeline") or {}
        added = pipeline.get("added") or []
        removed = pipeline.get("removed") or []
        if added:
            parts.append(f"+{', '.join(s.get('component', '?') for s in added[:3])}")
        if removed:
            parts.append(f"-{', '.join(s.get('component', '?') for s in removed[:3])}")
    return "; ".join(parts) if parts else "No comparable metrics found."


# ── Run labels: what each version changed (Q-1688, Q-1792) ───────────
#
# A version compare's columns used to read "v1 · v2 · v3" whenever the
# set differed anywhere but ONE Execution/Globals key — and a pipeline
# block's param (the founder's `lookback 20 → 10 → 30`) was not a key the
# labeller could see at all, so the commonest iteration labelled bare.
# The label now names the set-wide CHANGE TERMS: every declaration key
# and every block param whose value is not the same in every run. When
# one or two terms vary and each can be named, every run (the baseline
# included) carries its own values (`v2 · lookback 10`). Otherwise the
# commit message each save now writes (Q-1752) is the fallback.

#: At most this many terms name a run; three is a sweep, not a label.
_LABEL_MAX_TERMS = 2

#: A term value longer than this is not shown (a symbol list, a dict).
_LABEL_VALUE_CHARS = 14

#: The commit-message fallback is cut to this many characters.
_LABEL_MESSAGE_CHARS = 32

#: Name segments a block param may not put into a label. A param name is
#: a component author's identifier, not vetted copy; the listed surface
#: rejects these tokens (tests/test_policy_scan.py), so a param carrying
#: one is a term that cannot be NAMED — the set falls back to messages.
_UNSAFE_PARAM_SEGMENTS = frozenset(
    {
        "amount",
        "buy",
        "collateral",
        "deploy",
        "fund",
        "funding",
        "leverage",
        "margin",
        "notional",
        "qty",
        "quantity",
        "sell",
        "size",
        "trade",
        "trades",
        "trading",
        "upgrade",
        "usd",
        "wallet",
    }
)


#: How many differences one `Differs from baseline` cell names before it
#: says how many more there are. A table cell is one line wide.
MAX_DIFF_PARTS = 3

#: What that cell says when a run's spec and window match the baseline's.
#: An em dash would be indistinguishable from "the envelope carried no
#: diff", which is what the row said on every real result until Q-1714 —
#: so a reader concluded the varied parameter had no effect on the spec.
DIFF_IDENTICAL = "identical"


def _declaration_part(section: str, key: str, value: Any, *, universe_mode: Any = None) -> str:
    """`buffer 0.05` · `offset 6h` · `Top 20 by volume` — this module's own
    vocabulary (`_DECLARATION_LABELS`), the same words `_run_label`
    puts in a run's label, so the label and the diff cell agree.

    A Universe `top_n` says what the card's universe chip says (Q-1876):
    the chip's owner (`_strategy_view.top_n_label`) words it, never the raw
    DSL key."""
    if section == "universe" and key == "top_n":
        from ._strategy_view import top_n_label

        return top_n_label(value, universe_mode)
    word = _DECLARATION_LABELS.get(key, key) if key in _DECLARATION_LABELS else key
    return f"{value}" if word is None else f"{word} {value}"


def _run_diff(spec_diff: Any, *, same_window: bool, universe_mode: Any = None) -> str:
    """`buffer 0.05 · window` — what THIS run differs from the baseline in.

    `card-compare.js` reads this off `view.runs[i].diff` and the wire
    never carried it (Q-1714): the information sat one level up in
    `spec_diffs[i]`, in a shape the card does not read, so the row whose
    whole job is to say what varied read `—` in every column on every
    real result. Projected here rather than in the card because the
    envelope is where a measurement is decided; the card renders.
    """
    parts: list[str] = []
    if isinstance(spec_diff, dict):
        declarations = spec_diff.get("declarations")
        if isinstance(declarations, dict):
            for section in DECLARATION_SECTIONS:
                keys = declarations.get(section)
                if not isinstance(keys, dict):
                    continue
                for key in sorted(keys):
                    pair = keys[key]
                    if isinstance(pair, dict):
                        parts.append(
                            _declaration_part(
                                section, key, pair.get("b"), universe_mode=universe_mode
                            )
                        )
        pipeline = spec_diff.get("pipeline")
        if isinstance(pipeline, dict):
            added = len(pipeline.get("added") or [])
            removed = len(pipeline.get("removed") or [])
            changed = len(pipeline.get("changed") or pipeline.get("modified") or [])
            for count, word in ((added, "added"), (removed, "removed"), (changed, "changed")):
                if count:
                    parts.append(f"{count} {word}")
    if not same_window:
        parts.append("window")
    if not parts:
        # Only sayable when the spec diff RAN: without it this run's
        # spec is unknown, not identical.
        return DIFF_IDENTICAL if isinstance(spec_diff, dict) else ""
    if len(parts) > MAX_DIFF_PARTS:
        extra = len(parts) - MAX_DIFF_PARTS
        parts = [*parts[:MAX_DIFF_PARTS], f"+{extra} more"]
    return " · ".join(parts)


def _label_value(value: Any) -> str | None:
    """A term value as a label shows it, or None when it cannot be shown."""
    if isinstance(value, bool):
        text = str(value).lower()
    elif isinstance(value, (int, float, str)):
        text = str(value)
    else:
        return None
    text = text.strip()
    if not text or "\n" in text or len(text) > _LABEL_VALUE_CHARS:
        return None
    return text


def _param_word(name: str) -> str | None:
    """A block param's name as a label word, or None when it is unsafe."""
    segments = [s for s in name.lower().split("_") if s]
    if not segments or any(s in _UNSAFE_PARAM_SEGMENTS for s in segments):
        return None
    return " ".join(segments)


def _term(word: str | None, values: list[Any], nameable: bool) -> dict[str, Any]:
    shown = [_label_value(v) for v in values]
    return {
        "word": word,
        "values": shown,
        "nameable": nameable and all(v is not None for v in shown),
    }


def _declaration_terms(declarations: list[dict]) -> list[dict]:
    """Execution/Globals keys (named through the vetted map) and Universe
    keys (only `top_n` is nameable) whose value varies across the set."""
    terms: list[dict] = []
    for section in (*_LABEL_SECTIONS, "universe"):
        keys: set[str] = set()
        for view in declarations:
            keys |= set((view.get(section) or {}).keys())
        for key in sorted(keys):
            values = [(view.get(section) or {}).get(key) for view in declarations]
            if len({repr(v) for v in values}) <= 1:
                continue
            if section == "universe":
                terms.append(_term("top", values, key == "top_n"))
            else:
                terms.append(
                    _term(_DECLARATION_LABELS.get(key), values, key in _DECLARATION_LABELS)
                )
    return terms


def _block_terms(parsed: list[Any]) -> list[dict]:
    """Pipeline block params whose value varies across the set.

    Blocks are matched by component name and occurrence, ignoring
    position — the structural differ's rule (`diff_strategies`), through
    its own flattener so the two cannot disagree about what a step is. A
    block present in some runs and not others is a term that cannot be
    named (it is a structure change, not a value).
    """
    from pipeline_engine.dsl.differ import _flatten_steps

    keyed: list[dict[tuple[str, int], dict]] = []
    for spec in parsed:
        seen: dict[str, int] = {}
        steps: dict[tuple[str, int], dict] = {}
        for step in _flatten_steps(spec.pipeline.steps, "pipeline"):
            n = seen.get(step["component"], 0)
            seen[step["component"]] = n + 1
            steps[(step["component"], n)] = step["params"]
        keyed.append(steps)
    terms: list[dict] = []
    for key in sorted(set().union(*keyed)):
        if not all(key in steps for steps in keyed):
            terms.append(_term(None, [None] * len(keyed), False))
            continue
        params = [steps[key] for steps in keyed]
        for name in sorted(set().union(*params)):
            values = [p.get(name) for p in params]
            if len({repr(v) for v in values}) <= 1:
                continue
            present = all(name in p for p in params)
            word = _param_word(name)
            terms.append(_term(word, values, present and word is not None))
    return terms


def _change_labels(declarations: list[dict], parsed: list[Any]) -> list[str] | None:
    """Each run's change part (`lookback 10`), or None when the set's
    difference cannot be named in at most two terms."""
    terms = _declaration_terms(declarations) + _block_terms(parsed)
    if not 1 <= len(terms) <= _LABEL_MAX_TERMS:
        return None
    if not all(t["nameable"] for t in terms):
        return None
    words = [t["word"] for t in terms if t["word"]]
    if len(words) != len(set(words)):
        return None  # two terms under one word would read as one change
    labels: list[str] = []
    for index in range(len(parsed)):
        bits = [
            t["values"][index] if t["word"] is None else f"{t['word']} {t['values'][index]}"
            for t in terms
        ]
        labels.append(", ".join(bits))
    return labels


def _message_label(message: Any) -> str | None:
    """A commit message cut to a label: first line, whole words."""
    if not isinstance(message, str) or not message.strip():
        return None
    text = " ".join(message.strip().splitlines()[0].split())
    if len(text) > _LABEL_MESSAGE_CHARS and " · " in text:
        # Q-1752's derived messages read `<Block> · <param> <old> → <new>`;
        # when the whole will not fit, the change is the part to keep.
        text = text.rsplit(" · ", 1)[1]
    if len(text) > _LABEL_MESSAGE_CHARS:
        cut = text[: _LABEL_MESSAGE_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,·:;-→")
        text = (cut or text[: _LABEL_MESSAGE_CHARS - 1]) + "…"
    return text


def _fetch_message(client, detail: dict) -> str | None:
    """The version's commit message (Q-1752), best-effort."""
    strategy_id = detail.get("strategy_id")
    commit_id = detail.get("commit_id")
    if not strategy_id or not commit_id:
        return None
    try:
        resp = client.get(f"/v1/strategies/{strategy_id}/versions/{commit_id}")
    except KeelError:
        return None
    return resp.get("message") if isinstance(resp, dict) else None


#: What the version that CREATED a strategy is labelled, instead of its
#: `Create <name>` message (Q-1752's create default): the name is already
#: the card's header, so "v1 · Create HYPE Long/Cash MACD Trend" beside
#: "v2 · MACD 12/26/9 → 8/21/5" said nothing about v1 (ChatGPT, 2026-09-23).
ORIGINAL_LABEL = "original"


def _version_message(client, detail: dict) -> str | None:
    """The version's message as a label source — its create default read
    as `original`, never as the strategy's name again."""
    message = _fetch_message(client, detail)
    if not isinstance(message, str) or not message.startswith("Create "):
        return message
    name = detail.get("strategy_name")
    if message.strip() == f"Create {name}" or detail.get("sequence_number") == 1:
        return ORIGINAL_LABEL
    return message


def _version_labels(
    client, details: list[dict], declarations: list[dict], parsed: list[Any]
) -> list[str]:
    """`v2 · lookback 10` per run of ONE strategy.

    Structural first — the declaration + block-param terms that vary
    across the whole set, so the baseline is labelled with its own value
    (a pairwise rule has nothing to label it by). The commit message is
    the fallback, used only when it tells the runs apart; otherwise the
    bare `v{n}` stands.
    """
    stems = [
        f"v{d.get('sequence_number')}" if d.get("sequence_number") is not None else f"run {i + 1}"
        for i, d in enumerate(details)
    ]
    changes = _change_labels(declarations, parsed) if parsed else None
    if changes is None:
        from ._backtest_view import parallel_map

        messages = [
            _message_label(m) for m in parallel_map(lambda d: _version_message(client, d), details)
        ]
        if len({m for m in messages if m}) > 1:
            changes = [m or "" for m in messages]
    if changes is None:
        return stems
    return [f"{stem} · {change}" if change else stem for stem, change in zip(stems, changes)]


def _run_curve(client, backtest_id: str, points: int) -> dict | None:
    """One run's compact curve at the comparison budget, or None."""
    return _run_curve_and_reference(client, backtest_id, points)[0]


def _run_curve_and_reference(
    client,
    backtest_id: str,
    points: int,
    reference_span: tuple[str, str] | None = None,
    holds: tuple[str, ...] = (),
) -> tuple[Any, list[dict]]:
    """One run's compact curve and, when `holds` asks, its hold lines — one
    `/curve` read."""
    from ._backtest_view import fetch_curve_and_holds

    return fetch_curve_and_holds(
        client, backtest_id, points=points, holds=holds, reference_span=reference_span
    )


def _hold_span_fallback(references: list[dict], common: Any) -> str | None:
    """Mark hold lines that do NOT cover the common span, and say why.

    `holds` promises each line "over the span every run covers"; when the
    runs share no span (`common_reference_span` → None) or keel-api did not
    echo the span asked for, the line covers run 1's own span instead. That
    used to be silent in the structured result (`common_span` absent rather
    than false — Q-2270); each such line now carries `common_span: false`
    and `span_note`, and the comparison notes carry the sentence once.
    """
    if not references:
        return None
    reason = (
        "the runs share no common span (or a run lacks its chart dates)"
        if common is None
        else "keel-api did not confirm the common span"
    )
    note = f"Hold lines cover run 1's span: {reason}."
    marked = False
    for reference in references:
        if not reference.get("common_span"):
            reference["common_span"] = False
            reference["span_note"] = note
            marked = True
    return note if marked else None


def _holds_arg(value: Any) -> tuple[str, ...]:
    """`holds` as the ordered, de-duplicated symbols asked for, or a refusal."""
    from ._backtest_view import HOLD_SYMBOLS

    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise KeelError(
            "`holds` is a list of symbols.",
            error_code="bad_compare_args",
            exit_code=2,
            suggestion=f"1–3 of {', '.join(HOLD_SYMBOLS)}.",
        )
    symbols = tuple(dict.fromkeys(v.strip().upper() for v in value))
    unknown = [s for s in symbols if s not in HOLD_SYMBOLS]
    if unknown or len(symbols) > len(HOLD_SYMBOLS):
        raise KeelError(
            f"`holds` takes 1–3 of {', '.join(HOLD_SYMBOLS)} (got {', '.join(value)}).",
            error_code="bad_compare_args",
            exit_code=2,
            suggestion=f"1–3 of {', '.join(HOLD_SYMBOLS)}.",
        )
    return symbols


def _head_drift_next(client, strategy_id: Any, details: list[dict]) -> str | None:
    """Review 06 §4 — after a set of variants, HEAD sits on whichever
    version was saved LAST, which need not be the one the user picks. The
    one `next` states that fact and the call that moves HEAD (R-23).

    Neutral by construction (Q-1875): it names HEAD and the versions the
    set ran, never a winner. It used to name the highest-Sharpe version as
    the one to restore — a ranking the user never chose, which pointed the
    R4 agent at the worst option for the user's own criterion.

    Silent unless every run is one strategy's, the set spans ≥ 2 versions
    and HEAD is known. Advisory: one read of the strategy row; a failure
    costs the line.
    """
    if not strategy_id:
        return None
    from ._backtest_view import is_success

    versions = sorted(
        {
            detail["sequence_number"]
            for detail in details
            if is_success(detail)
            and isinstance(detail.get("sequence_number"), int)
            and not isinstance(detail.get("sequence_number"), bool)
        }
    )
    if len(versions) < 2:
        return None
    try:
        meta = client.get(f"/v1/strategies/{strategy_id}")
    except Exception:  # noqa: BLE001 — a hint never fails the compare
        return None
    head = meta.get("current_sequence") if isinstance(meta, dict) else None
    if not isinstance(head, int) or isinstance(head, bool):
        return None
    ran = ", ".join(f"v{v}" for v in versions)
    return (
        f"HEAD is v{head}; this set ran {ran}: "
        f'keel_strategy_restore(strategy_id="{strategy_id}", ref="<version>") makes any '
        "of them the next version."
    )


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from ._backtest_view import (
        COST_MODEL_KEYS,
        build_comparison_view,
        common_reference_span,
        cost_model_block,
        normalized_status,
        parallel_map,
        realism_from_metrics,
        view_metrics,
        window_block,
    )

    ids = [str(i) for i in (args.get("backtest_ids") or [])]
    if not COMPARE_MIN_IDS <= len(ids) <= COMPARE_MAX_IDS:
        raise KeelError(
            f"Between {COMPARE_MIN_IDS} and {COMPARE_MAX_IDS} backtest_ids are "
            f"required (got {len(ids)}).",
            error_code="bad_compare_args",
            exit_code=2,
            suggestion=usage_hint(
                "Usage: `keel backtest compare <backtest_id> <backtest_id> "
                "[... up to 8]`, the baseline first.",
                "Pass 2 to 8 backtest ids in `backtest_ids`, the baseline first.",
            ),
        )

    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        # A run compared with itself is one column twice (Q-2273): refused,
        # so the agent asks for the comparison it meant.
        raise KeelError(
            f"Each backtest may appear once in `backtest_ids` (repeated: {', '.join(repeated)}).",
            error_code="bad_compare_args",
            exit_code=2,
            suggestion="Pass 2 to 8 different backtest ids, the baseline first.",
        )

    holds = _holds_arg(args.get("holds"))
    client = ctx.get_client()
    details = parallel_map(lambda run_id: _fetch_detail(client, run_id), ids)
    metrics_raw = [d.get("metrics") or {} for d in details]
    baseline = details[0]

    strategy_ids = {d.get("strategy_id") for d in details if d.get("strategy_id")}
    shared_strategy = strategy_ids.pop() if len(strategy_ids) == 1 else None

    # Sources + spec diffs, best-effort: N completed runs must compare
    # even when a source is unfetchable (pruned commit, old fork) or the
    # local pipeline_engine bundle cannot parse an older dialect.
    sources: list[str | None] = []
    spec_diff_error: str | None = None
    try:
        sources = parallel_map(lambda detail: _fetch_source(client, detail), details)
    except Exception as e:  # noqa: BLE001 — degrade, never kill the compare
        sources = [None] * len(details)
        spec_diff_error = f"Spec diff unavailable: {e}"

    spec_diffs: list[Any] = [None]
    declarations: list[dict] = []
    parsed: list[Any] = []
    if spec_diff_error is None and all(sources):
        try:
            from pipeline_engine.dsl import parse_strategy

            parsed = [parse_strategy(src) for src in sources]
            declarations = [_declaration_view(spec) for spec in parsed]
            spec_diffs += [_spec_diff(sources[0], src) for src in sources[1:]]
        except Exception as e:  # noqa: BLE001 — degrade, never kill the compare
            declarations = []
            parsed = []
            spec_diffs = [None] * len(details)
            spec_diff_error = f"Spec diff unavailable: {e}"
    else:
        spec_diffs = [None] * len(details)
        if spec_diff_error is None:
            spec_diff_error = "Source unavailable for one or more runs."

    labels = (
        _distinct_labels(
            [str(d.get("strategy_name") or f"run {i + 1}") for i, d in enumerate(details)],
            details,
        )
        if shared_strategy is None
        else _version_labels(client, details, declarations, parsed)
    )

    # Comparability over the whole set (Q-1802), naming each run the way
    # the reader tells them apart: `v2` within one strategy (the label's
    # own stem — its change part is what the sentence is about to say),
    # the strategy name across strategies.
    names = _prose_names(details, labels, shared_strategy)
    warnings, notes, overlap = _comparability(details, names)
    if shared_strategy is None:
        warnings.append(_cross_strategy_sentence(spec_diffs, declarations))
    # The settings that differ, named (Q-1802). Run settings (capital,
    # costs, exposure cap) are a warning always: they are never the
    # strategy edit under test, and they move every net figure. The
    # DECLARATIONS are a warning across strategies — nobody chose them as
    # the thing under test (a library fork's template defaults are not
    # the agent's) — and within one strategy they ARE the edit being
    # compared: a note, and not even that when the run labels already
    # name every change.
    settings = _settings_difference(details, names)
    if settings:
        warnings.append(settings)
    declared = _declaration_differences(details, declarations, names)
    if shared_strategy is None:
        warnings.extend(declared)
    elif _change_labels(declarations, parsed) is None:
        notes.extend(declared)

    runs: list[dict[str, Any]] = []
    # Every line the envelope carries shares the budget (spec 03 §2.2 point
    # 5, Q-2441): N run curves, one series per asked hold (keel-api
    # downsamples each to run 1's bucket count), and `reference.series`, the
    # legacy one-line duplicate of the first hold. No holds, no hold lines.
    curve_points = compare_curve_points(len(details) + len(holds) + (1 if holds else 0))
    references: list[dict] = []
    run_ids = [str(detail.get("id") or ids[index]) for index, detail in enumerate(details)]
    # Hold lines only when `holds` asks (Q-1886: no price read by default),
    # each over the span every run's chart covers — asked of run 1's read.
    common = common_reference_span(details) if holds else None
    # Every run's `/curve` read at once — each is independent, and in
    # sequence they were most of a compare's wall clock (Q-1886).
    curves = parallel_map(
        lambda index: _run_curve_and_reference(
            client,
            run_ids[index],
            curve_points,
            common if index == 0 else None,
            holds if index == 0 else (),
        ),
        range(len(run_ids)),
    )
    for index, detail in enumerate(details):
        run_id = run_ids[index]
        run: dict[str, Any] = {
            "label": labels[index],
            "version": detail.get("sequence_number"),
            "status": normalized_status(detail),
            "metrics": view_metrics(detail.get("metrics")),
            # The window THIS run's numbers cover (Q-1802): metrics are
            # copied per run, never recomputed on a common window.
            "window": window_block(
                detail.get("start_date"), detail.get("end_date"), detail.get("window")
            ),
            "url": app_url_for("backtest", str(run_id), ctx),
        }
        # The run's recorded order realism (Q-1876), copied as the
        # single-run tools copy it — the small-order share is often the
        # deciding difference between variants on a small account.
        realism = realism_from_metrics(detail.get("metrics"))
        if realism is not None:
            run["realism"] = realism
        if index:
            # The baseline differs from nothing; every other run says
            # what it differs from it IN (Q-1714).
            diff = _run_diff(
                spec_diffs[index] if index < len(spec_diffs) else None,
                same_window=(detail.get("start_date"), detail.get("end_date"))
                == (baseline.get("start_date"), baseline.get("end_date")),
                universe_mode=(
                    (declarations[index].get("universe") or {}).get("mode")
                    if index < len(declarations)
                    else None
                ),
            )
            if diff:
                run["diff"] = diff
        curve, run_references = curves[index]
        if curve:
            run["curve"] = curve
        cost_model = cost_model_block(detail)
        if cost_model is not None:
            run["cost_model"] = cost_model
        if index == 0:
            # The span every run covers when the server served it, else run
            # 1's — named either way, never "the same span" (R-28, Q-1886).
            references = run_references
            fallback = _hold_span_fallback(references, common)
            if fallback is not None:
                notes.append(fallback)
        runs.append(run)

    window = window_block(
        baseline.get("start_date"), baseline.get("end_date"), baseline.get("window")
    )
    name = (
        baseline.get("strategy_name")
        if shared_strategy is not None
        else f"{len(details)} strategies"
    )
    # V-6: ONE destination, and a specific one. Every run shares a
    # strategy ⇒ its editor, where the next iteration happens. Runs that
    # span strategies have no single target, so the envelope says so
    # rather than inventing one (Q-1686's rule).
    hero_url = app_url_for("strategy", shared_strategy, ctx) if shared_strategy else None

    extra: dict[str, Any] = {}
    if len(details) == 2:
        # The two-id key set stays byte-identical (§2.2): every existing
        # caller, fixture and golden reads these exact keys.
        perf, cost = _compare_metrics(metrics_raw[0], metrics_raw[1])
        extra.update(
            {
                "run_a": _run_block(details[0]),
                "run_b": _run_block(details[1]),
                "performance": perf,
                "cost_profile": cost,
                "comparability_warnings": warnings,
                "metrics_raw_a": metrics_raw[0],
                "metrics_raw_b": metrics_raw[1],
                "summary_text": _summarize(perf, cost, spec_diffs[1]),
            }
        )
        if spec_diffs[1] is not None:
            extra["spec_diff"] = spec_diffs[1]
        if spec_diff_error is not None:
            extra["spec_diff_error"] = spec_diff_error
    else:
        # Beyond two ids the pair keys are omitted rather than
        # reinterpreted: one key never carries two shapes.
        perf_by_run, cost_by_run = _metrics_by_run(metrics_raw)
        extra.update(
            {
                "runs": [_run_block(d) for d in details],
                "performance_by_run": perf_by_run,
                "cost_profile_by_run": cost_by_run,
                "comparability_warnings": warnings,
                "metrics_raw_by_run": metrics_raw,
                "summary_text": _summarize_set(runs),
            }
        )
        if spec_diff_error is not None:
            extra["spec_diff_error"] = spec_diff_error
    # `spec_diffs` is the N-shape, baseline-vs-each by index — present on
    # both arms so a two-id caller can move to it without a shape test.
    extra["spec_diffs"] = spec_diffs
    if shared_strategy:
        # The audit row's join key (D.1's third ratio has none without
        # it): `outcome_ref_from_result` reads `extra.strategy_id`.
        extra["strategy_id"] = shared_strategy
        drift = _head_drift_next(client, shared_strategy, details)
        if drift:
            extra["next"] = drift
    # One cost model for the set when every run shares it (Q-2270); when they
    # differ, each run's rides `view.runs[i].cost_model` and the settings
    # warning names the difference.
    models = [run.get("cost_model") for run in runs]
    if models and all(models) and len({tuple(m[k] for k in COST_MODEL_KEYS) for m in models}) == 1:
        extra["cost_model"] = models[0]
    if references:
        # Spec 03 §2.2 / R-28: the numbers ride `structuredContent`, the
        # series `_meta` (where the host takes it). `reference` keeps its
        # one-line shape (the first asked); `references` carries every one.
        # A COPY, not the same dict: the channel partition pops
        # `reference.series` and `references[].series` in place, and an
        # aliased first entry handed its series to `reference` alone (Q-2223).
        extra["reference"] = dict(references[0])
        extra["references"] = references

    view = build_comparison_view(
        runs=runs,
        name=name,
        window=window,
        warnings=warnings,
        notes=notes,
        overlap=overlap,
        url=hero_url,
        object_id=f"compare:{shared_strategy}" if shared_strategy else None,
    )
    if view is not None:
        extra["view"] = view

    from ._render import card_render_block

    extra["render"] = card_render_block("backtest", fallback_url=hero_url, ctx=ctx)

    return OutcomeResult(
        run_id=None,
        hero_url=hero_url,
        share_url=None,
        summary_metrics=None,
        resource_uri=None,
        extra=extra,
    )


def _run_block(detail: dict) -> dict:
    return {
        "backtest_id": detail.get("id"),
        "strategy_id": detail.get("strategy_id"),
        "strategy_name": detail.get("strategy_name"),
        "commit_id": detail.get("commit_id"),
        "engine": detail.get("engine"),
        "period": {
            "start_date": detail.get("start_date"),
            "end_date": detail.get("end_date"),
        },
    }


def _metrics_by_run(metrics_raw: list[dict]) -> tuple[dict, dict]:
    """`{key: [v0, v1, ...]}` aligned with `runs[]` — the N-shape of the
    two-id `performance` / `cost_profile` blocks. Values verbatim, except
    the drawdown, stated once as `max_drawdown_pct` ≤ 0 (Q-1805)."""
    from ._backtest_view import era_metrics, signed_drawdown

    metrics_raw = [signed_drawdown(era_metrics(m)) for m in metrics_raw]

    def columns(keys):
        out: dict[str, list[Any]] = {}
        for key in keys:
            if any(key in m for m in metrics_raw):
                out[key] = [m.get(key) for m in metrics_raw]
        return out

    return columns(_PERFORMANCE_KEYS), columns(_COST_PROFILE_KEYS)


def _summarize_set(runs: list[dict]) -> str:
    """One line naming each run's headline — the >2 twin of `_summarize`."""
    parts = []
    for run in runs:
        metrics = run.get("metrics") or {}
        sharpe = metrics.get("sharpe")
        if isinstance(sharpe, (int, float)) and not isinstance(sharpe, bool):
            parts.append(f"{run['label']} Sharpe {sharpe:.2f}")
        else:
            parts.append(str(run["label"]))
    return "; ".join(parts) if parts else "No comparable metrics found."


BACKTEST_COMPARE = register(
    OutcomeTool(
        name="keel_backtest_compare",
        required_action="backtest.read",
        cli_path=("backtest", "compare"),
        toolset="backtest",
        # grounded-in: system/backtest_costs.md:9-18 ("Backtest cost
        # defaults" — fees/slippage are modelled, so cost deltas between
        # runs are real signal); system/trading_domain.md:25 (too many
        # trades / high turnover is a named failure mode — costs dominate
        # short timeframes); system/chat/collaboration.md:52-69 (§4 iterate
        # one change at a time and confirm what actually changed between
        # two versions).
        description=(
            "Compare 2–8 completed backtests side by side — one run alone is "
            "`keel_backtest_summarize`. One table with deltas against the first id and "
            "each run's cost profile (resizes, turnover, fees), "
            "overlaid equity curves, notes when windows, engines, carry or strategies "
            "differ, and — when the sources resolve — a declaration and step diff. It "
            "takes every id in one call; saved versions of one strategy compare the same "
            "way, and `keel_strategy_restore` makes a chosen version the latest."
        ),
        input_schema={
            "type": "object",
            "required": ["backtest_ids"],
            "properties": {
                "backtest_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": COMPARE_MIN_IDS,
                    "maxItems": COMPARE_MAX_IDS,
                    "description": (
                        "Two to eight backtest_ids, the baseline first. "
                        "Deltas are each run minus the first."
                    ),
                    "x-cli-positional": True,
                },
                "holds": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["BTC", "ETH", "SOL"]},
                    "minItems": 1,
                    "maxItems": 3,
                    "uniqueItems": True,
                    "description": (
                        "Optional price-only hold lines for comparison with holding "
                        "the asset itself: 1–3 of BTC, ETH, SOL, each over the span "
                        "every run covers; when the runs share none, over run 1's span, "
                        "marked `common_span: false`. Omitted, no price data is read."
                    ),
                },
            },
        },
        annotations={
            "title": "Compare Backtests",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
        # No listed override (agent-surface-cleanup spec 01 §2.5, R-4): the
        # base text is the same on every profile, so it is written in the
        # listed word rules' terms — "carry", and no count word: the count's
        # label is "Trades" (Q-1906) and "trades" is a listed-banned token.
    )
)
