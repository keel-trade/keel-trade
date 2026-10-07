## Reasoning Principles

- **Every pipeline ends in WeightSeries.** Track the output type as you compose — data → signal → forecast → weights — and plan the sizer from the start.
- **Normalize only when needed.** ForecastScaler handles signal scaling internally — a single signal can go directly to ForecastScaler without prior normalization. CrossSectionalZScore is needed when **combining multiple signals on different scales** (e.g., ROC + RSI through ForecastCombiner). For single-signal pipelines, cross-sectional normalization is a follow-up improvement, not the default. For small universes (<5 assets), use RollingZScoreTransform or `ForecastScaler(pool="by_asset")` instead — cross-sectional z-scores are degenerate with few assets.
- **Data shape is (time × assets).** Every operation applies to all assets simultaneously. There is no single-asset mode.
- **Signal values carry information.** Each step should preserve or deliberately transform it — never silently discard.
- **Each transformation runs once.** Double normalization or double scaling destroys signal quality.
- **Match the path to the signal type.** Continuous signals (ROC, EWMA) follow Path 1. Discrete signals (threshold, binary) follow Path 2.
- **A discrete strategy needs a way out.** A threshold entry exits when it re-evaluates flat, or through rules on its Position (`TradeManager`); a top-N selection holds for a calendar cadence (WeightCadence).
- **Name the universe before the signal.** `Universe(...)` decides which assets a signal can ever see; a signal that filters assets on its own is a second, hidden universe that the loader, the backtest and live will each resolve differently.
