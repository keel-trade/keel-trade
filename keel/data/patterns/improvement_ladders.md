<!-- keywords: improve, better, next, iterate, upgrade, enhance, optimize, ladder, step, progression -->
<!-- pattern: improvement_ladders -->

# Context-Driven Improvement Sequence

When a user says "make it better" or wants to improve an existing strategy,
suggest ONE specific improvement at a time. Follow this ladder based on what
the pipeline currently has.

## Improvement Ladder

### Level 1 → Level 2: Add a Second Signal

**When**: Pipeline has a single signal (1 indicator → forecast → ForecastWeightNormalizer).
**Action**: Add a complementary signal from a different family.
**Why**: Uncorrelated signals improve Sharpe ratio more than optimizing one signal.

Best complementary pairs:
- Trend (EWMAC/ROC) + Carry (funding rate)
- Trend + Mean reversion (RSI)
- Momentum (ROC) + Breakout (BreakoutDistance)

### Level 2 → Level 3: Upgrade to Vol-Targeted Sizing

**When**: Pipeline combines signals with ForecastWeightNormalizer.
**Action**: Replace ForecastWeightNormalizer with ReturnVolatility + VolTargetWeightConverter chain.
**Why**: Volatility-targeted sizing reduces drawdowns without sacrificing returns.

### Level 3 → Level 4: Add Position Management

**When**: Pipeline has VolTargetWeightConverter but no inertia or IDM.
**Action**: Add IDMPortfolioAggregator → PositionInertia.
**Why**: IDM captures diversification benefit; inertia reduces unnecessary turnover.

### Level 4 → Level 5: Add Regime Conditioning

**When**: Pipeline has full position sizing and multiple signals.
**Action**: Add RegimeWeightedBlender with FundingLevelRegime.
**Why**: Adjusts signal weights based on market conditions.

### Level 5 → Level 6: Cost Optimization

**When**: Pipeline is feature-complete.
**Action**: Add PositionInertia tuning, DenseToSparseConverter, or
adjust rebalance frequency.
**Why**: Reduces trading costs without changing signal quality.

## Anti-Pattern: Skipping Levels

Don't jump from a single-signal strategy to a full production pipeline.
Each level should be tested and understood before adding complexity.
More than 25 free parameters almost certainly means overfitting.

## Asking the Right Question

When the user says "make it better", ask yourself:
1. What level is the current pipeline?
2. What's the ONE most impactful improvement?
3. Suggest that ONE change, explain why, and let the user test it.
