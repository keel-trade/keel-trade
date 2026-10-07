## Key Mistakes to Avoid

Structural mistakes — a trade reader above `TradeManager`, an action fed something other than a 0/1 mask, a rule reading what a later stage sells, MaskAnd on directional signals, equal weights (EqualWeightAllocator, EqualWeightSizer) on a continuous forecast, an uncapped FixedWeightSizer — are reported by the validator with the fix in the message; a pipeline that does not end in WeightSeries, including one that ends at an unconsumed Parallel, is refused by the backtest before it runs. The ones below are judgment the validator cannot make.

**M-03: Normalizing binary signals.** CrossSectionalZScore on {-1,0,1} is meaningless. Binary signals follow Path 2 (entry/exit), not Path 1 (continuous forecast).

**M-16: Over-indexing on one pattern.** Not every strategy needs hierarchical multi-signal forecast-combine. A simple factor tilt or entry/exit strategy may be exactly what the user wants.

**M-21: Re-normalizing or re-thresholding already-binary signals.** MaskAnd, MaskOr, and the threshold filters (AboveThresholdFilter, BelowThresholdFilter) already output 0.0/1.0 binary values. Do NOT add MinMaxNormalize or ThresholdCross after them — this re-processes an already-binary signal and often squashes it to all zeros (producing 0 trades). Store the output directly: `MaskAnd() → Store('entries')`.

**M-29: Summing sleeves past the book.** Each branch sizes to WeightSeries before `WeightConcatenator`; the book's gross leverage is the sum of the branch targets — split the target across branches or cap after with `LeverageCap`. Two legs at `target_leverage=1.0` are a 2x book.

**M-35: Measuring a trade from its signal.** A stop, target, trail or time exit is measured from this trade's entry, so it is a rule on the Position: a reader (`TradeReturn()`, `BarsHeld()`, …) → a filter → `Exit()`, below `TradeManager`. A value computed from the entry signal (`Load('entries')`, a lag or a count since it) is anchored to the signal, which stays on after the trade closes or re-enters, so the next trade inherits a stale anchor. The reverse holds too: a stop or target never opens a trade. A dip to buy is a market fact in the signal layer, and an add is `ScaleIn`.
