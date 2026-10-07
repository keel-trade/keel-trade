---
name: overfit-check
description: >
  Probe whether a backtest result is robust or curve-fit: re-run the strategy
  on disjoint windows and regimes the user agrees to, optionally perturb the
  sensitive parameters, compare the runs once, and give one verdict (ROBUST /
  MIXED / OVERFIT). Use when the user asks "is this real?", "is this
  overfit?", "out-of-sample", "walk-forward", or asks for a robustness check;
  the check is most informative on an unusually strong in-sample result
  (Sharpe above 3.0, return above 500%). Not a substitute for the first
  backtest (backtest-and-analyze).
tools:
  - keel_backtest_run
  - keel_backtest_watch
  - keel_backtest_compare
references:
  - step: 2
    topic: platform_operations
    why: "the backtest window rule: warm-up and the data floor for each probe window"
  - step: 3
    topic: trading_domain
    why: "complexity ladder (>25 free parameters is overfitting), which parameters are the sensitive ones"
  - step: 4
    topic: reasoning_principles
    why: "each transform once; signal diversity over parameter variants when reading a fragile result"
---

# Method

## Step 1 — Confirm the baseline

The `strategy_id`, the baseline `run_id` (or run one on the default window),
and its window. State the baseline Sharpe / return / max DD before probing.

## Step 2 — Agree the out-of-sample windows

The probe takes several runs — one backtest of the plan's weekly allowance
each, plus wall-clock time. Before starting, say how many runs it needs and
which windows, and start once the user agrees; they may drop or pick
windows. Windows the user named are used as given. When the user asks for a
default split, this one is relative to the baseline window:

- W1 in-sample — its first half;
- W2 out-of-sample — its second half;
- W3 held-out tail — the most recent 3 months;
- W4 / W5 regimes — one high-vol stretch, one low-vol / chop stretch.

Every window must cover the strategy's warm-up. For each agreed window:
`keel_backtest_run(strategy_id=<id>, start_date=<start>, end_date=<end>)`; if still active,
`keel_backtest_watch(backtest_id=<run_id>)`. When all are terminal,
`keel_backtest_compare` once with every window's id — the table is the
comparison view.

## Step 3 — Parameter perturbation (only when the user asks for depth)

Pick the 1–2 parameters that matter (a lookback, a threshold, a leverage
cap — not a seed). Perturb ±20% and ±50% — four runs per parameter, stated
before starting — and compare once. A Sharpe that collapses under ±20% points
to a fitted parameter rather than an edge.

## Step 4 — Read stability

- Sharpe spread across windows: max − min > 2.0 with any window negative is
  fragile. Real edges degrade smoothly.
- Sign of returns: one negative disjoint window is a yellow flag; two are red.
- Parameter sensitivity: robust to ±20% or it is not an edge.

## Step 5 — Verdict

- **ROBUST** — stable Sharpe, positive across regimes, parameters not
  razor-thin.
- **MIXED** — some windows weak, thesis holds in most; a candidate for
  further out-of-sample testing.
- **OVERFIT** — variance too high, negative OOS, or razor-thin parameters.

One sentence, then the windows / perturbations that drove it.

# Decision points

- Disjoint is not cherry-picked: never place the OOS window on a known good
  period.
- An average across windows hides a −3.0 window; read every row.
- A ROBUST verdict is necessary, not sufficient — it describes historical
  windows only.
- A fragile result is answered with robustness, not with less exposure: a
  trend filter, sizing, or a regime gate, rather than removing exposure to
  dodge one bad window — that is curve-fitting.

# Output shape

1. The verdict sentence.
2. The comparison `view` (all windows) — explained, not repeated, where the
   host draws it.
3. The perturbation comparison, if run.
4. Two bullets of interpretation.
5. What the verdict implies: further out-of-sample testing / iterate further
   / discard.

# When NOT to use

- First-pass backtest → `backtest-and-analyze`; a strategy one run shows is
  broken gains nothing from five more.
- Authoring or editing → the creation or fork skills.

# Test prompts

1. "Is this 3.5 Sharpe real, or am I overfitting?"
2. "Run an out-of-sample check on str_K9p2Lz."
3. "Walk-forward this strategy across the last year."
