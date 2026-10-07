<!-- keywords: improve, better, next, iterate, upgrade, enhance, optimize, ladder, step, progression -->
<!-- pattern: improvement_ladders -->

## Improvement Ladder

### Level 1 → Level 2: Add a Second Signal

**When**: Pipeline has a single signal (1 indicator → forecast → ForecastWeightNormalizer).
**Action**: Add a complementary signal from a different family.
**Why**: Signals from different families have lower correlation, so combining them diversifies the forecast rather than refining one signal.

Best complementary pairs:

- Trend (EWMAC/ROC) + Carry (funding rate)
- Trend + Mean reversion (RSI)
- Momentum (ROC) + Breakout (BreakoutDistance)

### Level 2 → Level 3: Upgrade to Vol-Targeted Sizing

**When**: Pipeline combines signals with ForecastWeightNormalizer.
**Action**: Replace ForecastWeightNormalizer with ReturnVolatility + VolTargetWeightConverter chain.
**Why**: Volatility-targeted sizing scales each position to a volatility target, so risk per position is comparable across assets and over time.

### Level 3 → Level 4: Add Position Management

**When**: Pipeline has VolTargetWeightConverter but no IDM or turnover control.
**Action**: Add IDMPortfolioAggregator, and `Execution(rebalance="buffered", buffer_threshold=0.2, buffer_mode="relative", rebalance_method="to_edge")`.
**Why**: IDM captures diversification benefit; a buffer reduces unnecessary turnover.

### Level 4 → Level 5: Add Regime Conditioning

**When**: Pipeline has full position sizing and multiple signals.
**Action**: Add RegimeWeightedBlender with FundingLevelRegime.
**Why**: Adjusts signal weights based on market conditions.

### Level 5 → Level 6: Cost Optimization

**When**: Pipeline is feature-complete.
**Action**: Tune the buffer (`buffer_threshold`), slow the signal, or
adjust rebalance frequency.
**Why**: Fewer and smaller trades lower trading costs.

## Anti-Pattern: Skipping Levels

Jumping from a single-signal strategy to a full vol-targeted pipeline adds
every level's components and parameters at once, so a backtest cannot show
what any one addition changed. Testing each level before the next isolates
its effect. More than 25 free parameters almost certainly means overfitting.
