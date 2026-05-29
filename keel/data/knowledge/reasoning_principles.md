## Reasoning Principles

- **Every pipeline must reach WeightSeries.** Track the output type as you compose. If it's not WeightSeries, the pipeline is incomplete.
- **Types flow forward through phases.** DATA → SIGNAL → FORECAST → POSITION. You can't put an indicator after a position sizer.
- **Parallel produces dict, which must be consumed.** Always follow a Parallel block with a Composer, Extract, or Load.
- **Normalize only when needed.** ForecastScaler handles signal scaling internally — a single signal can go directly to ForecastScaler without prior normalization. CrossSectionalZScore is needed when **combining multiple signals on different scales** (e.g., ROC + RSI through ForecastCombiner). For single-signal pipelines, suggest cross-sectional normalization as a follow-up improvement, not the default. For small universes (<5 assets), use RollingZScoreTransform or ForecastScaler(pool="instrument") instead — cross-sectional z-scores are degenerate with few assets.
- **Independent computations belong in Parallel.** If two paths share the same input but don't depend on each other's output (e.g., a signal and a filter), use Parallel branches — not serial Store/Load chains. Ask: "does B need A's output?" If no, they're parallel. Branches receive `current` automatically — don't Load what's already flowing.
- **Slot scoping: branches can't see each other.** Parallel branches get context snapshots. Only Store before Parallel when a branch needs data from a *different* pipeline point via Load. Branches already receive `current` (the post-resampler data) automatically.
- **All data uses the target timeframe.** Store('ohlcv') goes AFTER TargetTimeframeResampler, never after PriceDataLoader. Every component operates on the resampled target timeframe. No component needs raw 15min data.
- **Two mask types: use the right one.** Universe masks (1.0/NaN from RollingVolumeUniverseMask) go through ApplyUniverseMask via slots. Signal filter masks (True/False from threshold filters) go through ApplyMask (a Composer that consumes the Parallel dict directly).
- **Data shape is (time × assets).** Every operation applies to all assets simultaneously. There is no single-asset mode.
- **Signal values carry information.** Each step should preserve or deliberately transform it — never silently discard.
- **Each transformation runs once.** Double normalization or double scaling destroys signal quality.
- **Match the path to the signal type.** Continuous signals (ROC, EWMA) follow Path 1. Discrete signals (threshold, binary) follow Path 2.
- **Exit logic is required for discrete strategies.** TopN and threshold entries need corresponding exit mechanisms.

