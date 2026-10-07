## Capability Boundaries — What Keel Can Test

### The authority for each claim

- **Component exists / does not exist** → the component registry. A
  component search with a typed filter (`input_type=..., output_type=...`)
  proves absence; a thin keyword result proves nothing. A component's
  detail gives its types, parameters and constraints.
- **Symbol tradeable** → the venue's instrument listing, which covers every
  Hyperliquid market, delisted ones included, with their status.
- **Timeframe** → the supported alphabet `5min, 15min, 30min, 1h, 2h, 3h,
4h, 6h, 8h, 12h, 1d` (validator/clock metadata). `30min` IS supported, and
  so is `5min` — the floor for every family but settled funding, whose floor
  is its own `1h`. `1min` is not in the alphabet.
- **Historical coverage** → the data-coverage floor in Platform Operations
  and the backtest run's own provenance; never a guess.
- **Live-deployability of a series** → the runtime freshness grade (below),
  which the deploy path enforces — not the registry and not the validator.
- **Fill semantics** → the documented execution contract: Keel simulates
  completed-bar target weights with close fills, fees and fixed slippage;
  live execution sends aggressive immediate-or-cancel orders by default (How
  execution actually works, below).

### Exact vs proxy — named, never silently swapped

Two measurements of the same concept coexist. The exact one is venue truth
from the 1m trade grid and is servable from the **flow floor, 2025-03-23**
(the archive's first instant is 2025-03-22 10:48 UTC; the first full day is
the floor every clock reads); the proxy is candle-derived and reaches the
full price depth, down into the retired 15m capture's frozen history
(from 2024-06-25 for BTC, ETH and SOL). A window that starts before the floor is not
refused: it begins at the floor and the response says so; an asset without
data on some bars is simply not traded there.

| Concept       | Exact (flow floor 2025-03-23→)                                                                | Proxy (full price depth)                   |
| ------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------ |
| Per-bar VWAP  | `VWAPLoader` — sum(price×size)/sum(size)                                                      | `VWAP` indicator — typical-price × volume  |
| Dollar volume | `DollarVolumeLoader` — sum(price×size); 15m candle volume × close spliced in before the floor | `NotionalVolumeExtractor` — volume × close |

The exact series applies inside the flow era; the proxy covers deeper
windows and is an approximation — the candle proxy `volume × close`, not
traded notional. A "price vs VWAP" threshold in the Pine sense (session or
anchored VWAP) is a SESSION/rolling anchor, and the per-bar `VWAPLoader` is
the wrong series for it. The series that match it are the rolling
ratio-of-sums recipe, the `VWAP` indicator (rolling proxy) and
`SessionVWAP` (session/day-anchored proxy; `anchor="day"` is Pine's daily
`vwap()`), each a proxy computed from typical-price × volume. `(close − VWAP) / ATR` is a `SignalRatio` over a
close-minus-`SessionVWAP` `Crossover` branch and an `ATR` branch.
Dollar volume is traded notional (sum of trade price × size) at every
depth: `DollarVolumeLoader` serves it from the floor and splices the 15m
candle `volume × close` in before it, naming the spliced bars in its
provenance — a window that reaches before 2025-03-23 is partly
candle-derived.
A trailing "$10M / 24h" floor is a THRESHOLD with a variable-size universe:
`Universe(min_trailing_dollar_volume=…)` at resolution and
`RollingDollarVolumeMask(dollar_volume_slot=…, window="24h", min_notional=…)`,
fed by `DollarVolumeLoader() -> Store(…)`, as the causal per-bar mask
applied BEFORE any rank. Both read traded dollar volume, the same number
at every clock (`min_trailing_notional_proxy` and the candle-proxy
`RollingNotionalProxyMask` are deprecated names for them), and top-N by
volume is NOT equivalent: it is a different rule. "Rebalanced
weekly" is `WeightCadence(duration="7d", anchor="MONDAY")`: INTENDED-weight
cadence at calendar boundaries, not a minimum holding period after partial
fills (execution-layer state).

### Notional vs base units — cross-asset comparison needs dollars

Exchange volume, taker flow, whale prints and open interest are in
**base-coin units** (BTC for BTC, DOGE for DOGE). Ranking or averaging them
across assets is refused, not approximated: "top N by volume" on base units
selects the highest-supply tokens. The fix is always offered with the
refusal:

- For volume: `DollarVolumeLoader` (traded notional; candle `volume × close`
  before 2025-03-23) or `NotionalVolumeExtractor` (the candle proxy) —
  cross-sectional volume is notional.
- For open interest: `OpenInterestLoader` serves BASE-COIN OI. Dollar OI is
  the product with a causally aligned price branch —
  `{"left": [OpenInterestLoader()], "right": [ExtractSeries(series_name="close")]} -> SignalProduct()` (the
  typed multiply composer; strict shape guard, NaN in ⇒ NaN out). Per-asset
  alternatives that need no price join: `Difference`/`SignalROC` (OI delta)
  or a per-asset `RollingZScoreTransform`. `OpenInterestRegime` /
  `PremiumRegime` (per-asset z FIRST, then the cross-sectional median) are
  the positioning regimes for `RegimeGate` / `RegimeScale` — state, never
  direction.
- OI, premium and predicted funding are per-MINUTE venue state
  (`market_data.ctx_1m`) served at every clock from `5min` up; a bar carries
  its LAST observed minute. So a 30-minute OI delta is
  `OpenInterestLoader() -> Difference()` — no projector, and `5min` is the
  floor. SETTLED funding is the one hourly series and stays hourly:
  `FundingDataLoader(timeframe="1h") -> TargetSignalProjector()` under a
  finer clock, the last COMPLETED hour projected forward.

### CVD anchor honesty

`TakerFlowLoader(side="net") -> CumulativeSum()` is CVD. Its LEVEL is
relative to the loaded window's start — CVD has no absolute level. Honest
uses: slopes, divergences, window-local z-scores. Comparing levels across
windows, or quoting "CVD is at X", is a category error.
OBV, MFI, or unsigned volume are NOT CVD and are never substituted for it.

### Per-series freshness — "backtestable now, live-deployable when the capture is live"

The flow family (`TakerFlowLoader`, `WhalePrintLoader`, `VWAPLoader`,
`DollarVolumeLoader`, `TwapVolumeLoader`) serves the `bars_1m` series. Its
grade is a property of (series × environment × time), decided at runtime by
the deploy path: a LIVE deploy of a
strategy consuming a backtest-grade series is refused with
`BACKTEST_GRADE_SERIES`, naming the loaders and what flips each. Backtests
are always allowed — that is what backtest-grade means. Per feature, the
status is "backtestable now; live deployment is refused until the live
trades capture is enabled in this environment" — neither "not supported"
nor "live-ready".
`TwapVolumeLoader` stays backtest-grade even after the capture is live until
its labelling verification lands.

The ctx family (`OpenInterestLoader` / `PremiumLoader` /
`PredictedFundingLoader` v2 — the version an unpinned strategy resolves) is
gated the same way on `ctx_1m/1m`; v1 serves only blobs pinned to it, reads
the old hourly partition and is not gated (the gate reads the blob's pinned
version, not the class name). Provisional (unsealed) minutes are SERVED,
never withheld — `provisional_from` in the run's provenance names, per coin,
the first bar above the seal watermark — so a recent window is available,
and a sealed re-run that differs there is the seal landing, not a bug.

### How execution actually works

The pipeline produces a target WEIGHT per asset per bar on the strategy's `target_timeframe`. At each bar the engine compares that target to the position it holds and sends orders for the difference — that is the whole execution model. By default a live deployment executes in the **market** style: one immediate-or-cancel (`Ioc`) order at an aggressive limit price — Hyperliquid's form of a market order — so every fill is a taker fill. A **maker** style also exists: it rests post-only (`Alo`) orders at the touch inside the bar's window and escalates to `Ioc` orders as the window closes, so a maker-styled bar can fill at a mix of maker and taker rates. The maker style is deployment configuration available only to an org holding the maker execution grant (deny-by-default); a strategy cannot declare it and a user cannot enable it. A strategy places no limit, stop, take-profit or bracket orders of its own: the DSL has no order type or limit price, and every exit is a close-triggered weight change. `Execution(rebalance=...)` changes only WHEN a difference is acted on — `every_bar` re-trues every held position each bar, `on_change` acts only when the target moves, `buffered` when it moves beyond a band — never what kind of order is sent.

### Bar-close consent (sessions, SMC, Pine-style risk)

Every exit in Keel is a close-triggered weight change. Price can trade
through a stop or target intrabar; the exit fires at the close of the bar
that triggers it, and Keel places no resting stop/TP orders on the exchange.
Execution framed as orders — "stop just below the range low", "resting TP at
SMA24", "1R target", "bracket", "OCO" — is supported under bar-close
semantics; the exact resting/intrabar claim is a different execution engine.
A moving-level exit ("exit when price returns to SMA24") IS expressible
today at bar close: store the market mask and close the trade with
`[Load(...), Exit()]`, a rule below `TradeManager`. The session family IS
registered, all "supported under bar-close semantics": `SessionMask` (IANA
zone, DST by the local clock, `anchor="day"` for a 00:00 day),
`SessionRangeHigh` / `SessionRangeLow(range_duration="30min")` (NaN until
the window's last bar closes, then frozen — never partial),
`SessionCloseExit` (the flat-by-EOD pulse), `SessionVWAP` and
`SessionRelativeVolume`. The NY-open ORB composes exactly as asked (recipe
in the entry_exit and session_and_structure patterns): range levels →
`{"fast": [ExtractSeries(series_name="close")], "slow": [SessionRangeHigh(...)]} -> Crossover() -> ThresholdCross(upper=0, mode="long_only")`
entries; `stop_dist` = close − the range low; `TradeManager`; R =
`TradePnL()` ÷ `AtEntry(slot="stop_dist")`; rules `{stop, target, bell}`;
`RiskSizer`. A market exit's bar also refuses a new entry, so "flat by the
bell" is one rule.
Declare `Execution(rebalance="on_change")` (an entry/exit strategy; `every_bar`
re-trues every held position each bar). Under bar-close semantics the stop
and the 1R target fire at the close of the bar that reaches them, and a
30-minute range on the 15min clock is the two completed 09:30–10:00 bars;
the pair is not a bracket order. A level scaled just below the range low
(`Scale(by=0.999)`) expresses "just under"; a fixed-percentage stop is a
DIFFERENT rule. A position that depends on its own past trades can differ
between live and the backtest until the strategy is next flat (the
`entry_exit_patterns` topic, "Live and backtest positions").

### No repaint, by contract

Keel's structure components confirm with lag and never rewrite the past:
recomputing later never changes an earlier value. Chart tools such as
TradingView repaint (a swing high appears before it is confirmed; a zone
moves as candles form), so a level there can show earlier than in Keel.
That lag is the no-repaint contract, which is what makes the backtest
meaningful — not a bug to tune away.

### Bar-fill honesty for flow/microstructure signals

A signal built from taker flow, whale prints, VWAP deviation, or
top-of-book aggregates is TIMING-sensitive, and the simulator fills at bar
close with fixed slippage. A backtest of one tests whether the flow signal
has bar-scale predictive value; it does not simulate maker, queue, or
intrabar execution. Depth ratios from bar aggregates are a proxy
for "resting liquidity"; order age and queue priority are not recoverable
and are never claimed. Full multi-level L2 and liquidation-price heatmaps
are a different execution engine with no authoritative feed. Realized
liquidation flow (no supported feed today) is distinct from modeled
liquidation-price maps (which need account/margin reconstruction), and
funding or OI is not a liquidation heatmap.

### R-vocabulary mapping

Trader phrase → Keel mapping today:

- "risk 1% per trade" (stop-distance sizing) → `RiskSizer(risk=0.01, distance=<stop distance slot>, max_weight=...)` — sized ONCE at the trade's entry bar and held; the cap is load-bearing. `RiskBudgetSizer` sizes to a VOLATILITY budget, a different rule.
- "1R / 2R target", "1.5R stop" → R = `TradePnL()` ÷ `AtEntry(slot=<stop distance>)` (`SignalRatio`), thresholds −1 / +2 → `Exit()`. "TP at the SMA24, stop at 1.5RR": the unit is the distance to the target at entry, so the target rule is R ≥ 1 and the stop rule is R ≤ −1/1.5 (= −0.6667). Bar-close. `TradeReturn()` thresholds are fixed percentages, not R.
- "daily loss limit", "pause after −3%" → **Per asset, expressible:** `RealizedPnL(window="1d") → AboveThresholdFilter(threshold=-0.03) → AllowEntry()` — no new entry on that asset for the rest of the UTC day once its realized loss reaches 3% (it does not close the open trade; `RealizedPnL` is a fraction). **Across the whole book at once: not expressible yet** — a cross-asset rule, a later portfolio-layer project. An execution-layer halt trigger (pause / flatten to cash) beside the existing halt machinery is not yet built. It is not a pipeline component; do not fake it with a signal.
- "max N open positions", "one position at a time across BTC and ETH" → **Not expressible yet: cross-asset** concurrency is a later portfolio-layer project. Per asset, `EntryCount(window=...)` caps entries. Neither a sizer nor `TopNAssetSelector` (which caps entries only) expresses the cross-asset limit.
- "trailing stop" → `GiveBack()` against a `Scale`d ATR, or `DrawdownFromPeak()` for a % trail, → `Exit()`; or `TrailingStop(...)` — supported under bar-close semantics.
- "flat by end of day" → `SessionCloseExit` stored → `[Load("bell"), Exit()]` — closes the trade at the final bar AND refuses an entry on it; `BarsHeld()` (N bars) is a different rule.
- "move stop to breakeven" → a later-stage `SoldFraction()` rule, or `SinceEntry(agg="max")` on R.
- "take half off" → `Reduce(fraction=0.5)` (of the initial size).
- "DCA", "safety orders", "pyramid" → `ScaleIn(units=..., times=...)`.
- "max N trades a day" → `EntryCount(window="1d")` with `AllowEntry()`.
- "cooldown" → `BarsSinceExit()` or `Cooldown(...)` with `AllowEntry()`; `reentry="any_bar"` for a still-on signal.

### Interpretation as configuration (SMC and price-action terms)

"Order block", "fair value gap", "break of structure", "liquidity sweep",
"premium/discount" each have several sourced definitions (LuxAlgo, smc.py,
ICT teaching). Keel does not adjudicate which is correct: each contested
clause is a parameter with sourced options and a documented default. The SMC family IS
registered — `SwingPivot`, `BreakOfStructure`, `ChangeOfCharacter`,
`LiquiditySweep`, `PremiumDiscount`, `FairValueGap`, `OrderBlock`,
`BreakerBlock`, `Displacement` — every contested clause a parameter with a
sourced default (`OrderBlock(candle_select, confirm, zone, mitigation)`,
`FairValueGap(fill)`, `LiquiditySweep(target, reclaim_bars)`,
`ChangeOfCharacter(which_pivot)`). Zones emit the signed ATR distance to
the nearest ACTIVE zone (≤ 0 = inside; the bar that reaches a zone reports
it, then it is gone under `touch`) or `output="edge"`, the far-edge PRICE
for an `AtEntry(slot=...)` structure stop and `RiskSizer(distance=...)`.
All of it confirms with lag (no repaint), is bounded by `expiry_bars` (a
Keel convention) and executes at bar close. "Enter the FVG / OB" is the
zone → `BelowThresholdFilter(threshold=0.0, inclusive=True)` mask, gated
by the flow context the user names via `MaskAnd`.

### Grid / DCA bots and other different-engine asks

A grid bot is a resting-order ladder with fill-anchored state — event-time
execution the completed-bar simulator does not model. Verdict: "a different
execution engine". What Keel can test honestly: a DCA-in schedule or a
mean-reversion entry/exit at bar close under `Execution(rebalance=...)`,
stated as a different strategy. Martingale-class money management is
refused outright.
