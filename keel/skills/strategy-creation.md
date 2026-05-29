---
name: strategy-creation
description: |
  Author a new Keel strategy from a natural-language thesis. Reason
  multi-step: clarify intent, discover components, choose a composition
  path, draft the DSL, validate locally, then create on the platform.
  This is NOT template-matching — it's the same reasoning the chat-api
  uses to compose strategies from first principles.
trigger: |
  Use when the user says "create a strategy", "new strategy from",
  "build a strategy that ...", "make me a ... strategy", or describes
  a trading thesis and wants it implemented. Do NOT use to modify an
  existing strategy (use `strategy-fork-and-iterate`) or to discover
  components in isolation (use `component-discovery`).
knowledge:
  - reasoning_principles
  - composition_mechanics
  - dsl_syntax
  - mistakes
  - tool_usage
  - universe_selection
  - pipeline_system
tools:
  - keel_components_search
  - keel_components_compose_help
  - keel_components_detail_batch
  - keel_strategy_compose
  - keel_strategy_checkout
---

# Workflow

## Step 1: Read the user's thesis carefully

Identify the **three composition decisions** before touching tools:

1. **Path** — continuous forecast, discrete entry/exit, screen-select, or direct allocation? (See `pipeline_system` and `strategy_patterns`.)
2. **Universe** — what asset class, how many, filtered how? Default to `Universe(asset_class="hl_perp", max_assets=30)` if user gave no signal.
3. **Polarity** — trend-following or mean-reversion? Default to trend-following unless the user explicitly says "fade", "overbought/oversold", "reversal", or "contrarian" (mistake M-24).

Match complexity to specificity. Vague request → ask ONE clarifying question, then build the simplest viable version. Detailed request ("EWMAC 2/8 with FDM and vol sizing") → execute exactly as stated, no simplification.

## Step 2: Discover components by intent, not keyword

For each trading concept the user mentioned (e.g., "beta hedge", "vol targeting", "trailing stop", "regime gate"), call `keel_components_search` with the **concept** as the query — not just the literal word. Pattern docs are guides, not the catalog. A concept like "beta hedge" maps to a real component (`BetaHedgeAllocator`) that template-matching would miss (mistake M-28).

If the user named a specific indicator (RSI, MACD, Keltner), still search to find its current canonical name, the exact slot it produces, and any required adapter (e.g., `ThresholdCross` after a raw indicator value).

## Step 3: Mock the pipeline graph, THEN batch-verify with `keel_components_detail_batch`

**This is the most important step. Skip it and the dry-run will fail.**

First, sketch the intended graph as a flat list of component refs — no DSL yet, just names and the rough type chain you expect:

```
mock = [
    "PriceDataLoader",            # → OHLCVDict
    "TargetTimeframeResampler",   # OHLCVDict → OHLCVDict (resampled)
    "ROC",                        # OHLCVDict → SignalSeries
    "CrossSectionalZScore",       # SignalSeries → NormalizedSignal
    "ForecastScaler",             # NormalizedSignal → ForecastSeries
    "ForecastCapper",             # ForecastSeries → ForecastSeries
    "ForecastWeightNormalizer",   # ForecastSeries → WeightSeries
]
```

Then **call `keel_components_detail_batch(names=mock)` in a single round-trip**. The result tells you for every component: actual `input_type` / `output_type`, parameter names + types + defaults, slot reads/writes, examples. Walk it pair-wise and check:

- Does each output type match the next input type? (If not — search for a transform component that bridges, OR you picked the wrong one)
- Are required parameters covered, with sensible defaults?
- Do any components require `Store('slot_name')` from earlier in the pipeline? (Vol-targeted sizers + slot-reading exits typically do)
- Did `keel_components_search` surface candidates that DON'T appear in your mock? Often there's a better choice (e.g. `BetaHedgeAllocator` vs hand-rolling a hedge — mistake M-28)

Only AFTER the batch verify says everything fits should you write DSL. Catching a wrong-shape component here costs one batch call; catching it via `keel_strategy_compose(dry_run=True)` costs a full compile round-trip plus a re-search-and-redraft loop.

For multi-signal joins: directional signals combine via `ApplyMask` (one signal directional, the other a filter), NOT `MaskAnd`/`MaskOr` which lose direction (mistake M-23). For exit logic on binary signals, do NOT re-normalize the output (mistake M-21). Branches that share data but stand alone → `Parallel { branch_a: [...], branch_b: [...] }` not serial Store → Load (mistake M-19). Branches that drop assets need `AssetAligner` in all sibling branches (mistake M-18).

## Step 4: Draft the DSL inline

Build the strategy as a single `Strategy(...)` block. Keep it minimal — every component must have a purpose the user can defend. Add what the user mentioned, plus the *required* glue (data loaders, weight normalizer, sizer) and explain each addition in one sentence.

## Step 5: Validate via dry-run compose

Call `keel_strategy_compose(dry_run=True, source=<draft>)`. If validation fails:

- Read the error code, NOT just the message
- Common fixes: missing `Universe`, wrong sizer for signal shape, `target_timeframe` mismatch
- Re-draft and re-validate. Do NOT proceed to creation until dry-run passes.

## Step 6: Create on platform

Call `keel_strategy_compose(dry_run=False, source=<validated>, name=<descriptive_slug>)`. Return:

- The `strategy_id`
- The `hero_url` (authenticated link to the strategy in the web app)
- One paragraph explaining the composition choices
- Suggested next step: backtest via `backtest-and-analyze`

## Step 7: Check out for iteration (recommended)

After the first create, immediately `keel_strategy_checkout <strategy_id>` so the file lives in the user's editor (project-local if cwd has `.keel/workspace.yaml`). Subsequent edits then go through the lightweight-git flow — edit → `keel_strategy_push` — which preserves history and is the right path for `strategy-fork-and-iterate`. Direct `keel_strategy_compose(strategy_id=..., source=...)` still works but bypasses the local file the user can see, so prefer the checkout flow for anything beyond the initial creation.

# Common mistakes

- **Defaulting to mean reversion when the user didn't ask** (M-24). Crypto has structural momentum bias. Without `NegateTransform`, high RSI = long. Only invert on explicit "fade"/"overbought" language.
- **Pattern-matching trading concepts instead of searching** (M-28). When the user says "beta hedge", search for the concept — don't synthesize a `ConstantForecast(-10)` and call it done.
- **EqualWeightAllocator on continuous forecasts** (M-01). Continuous signals carry conviction. Use `ForecastWeightNormalizer` or `VolTargetWeightConverter`. `EqualWeightAllocator` is correct only for Path 3 (screen-and-select).
- **Skipping the dry-run validate before create** (mistake #4 in §11.5 catalog). The platform parser is cryptic; the local validator names the missing component.
- **TopN without exit logic** (M-11). `TopNAssetSelector` picks entries but doesn't manage exits. Always follow with `SelectionToSignalConverter(hold_periods=...)`.

# Expected output shape

1. One-sentence summary of the thesis ("Trend-following on top-30 HL perps with regime-gated vol sizing").
2. The DSL source (fenced code block).
3. Bulleted explanation of the 3-5 key composition choices and *why*.
4. The `strategy_id` and `hero_url`.
5. Suggested next step (run a backtest).

# When NOT to use this skill

- **Modifying an existing strategy** → use `strategy-fork-and-iterate`. Even small edits should fork+update, not re-create.
- **Just looking up a component** → use `component-discovery`. Don't drag the user through a full creation flow if they only want to know what `BetaHedgeAllocator` does.
- **Recovering from a tool failure** → use `recover-from-error`. If `keel_strategy_compose` keeps erroring out and the user is frustrated, hand off.
- **The user wants to test something already authored** → use `backtest-and-analyze`.

# Test prompts

1. "Build me a momentum strategy on the top 20 HL perps with 1h bars."
2. "Create a new strategy that does EWMAC 8/32 trend following with vol-targeted sizing and a beta hedge to BTC."
3. "Make a mean-reversion strategy on ETH using RSI overbought/oversold."
