## Platform operations — data coverage and the backtest window

### Data coverage — per-asset and moving

Price history is **per-asset**: the earliest available bar depends on when
each instrument listed, and it **moves earlier over time** as the archive
backfills. So there is no single "history start" and no date to hardcode.
"Full history" is **data-driven** — the backtest tool and the submit
path resolve the real earliest coverage for the strategy's universe at run
time.

An ERA NOTE for orientation — never a value to pass. Measured on the
production 15-minute capture on 2026-09-26:

- **BTC, ETH, SOL** perpetuals: earliest 15-minute bar **2024-06-25**
  (continuous and dense from day one, ~672 bars/week).
- **HYPE**: from **2024-12-05**.
- Newer listings begin later, on their own listing dates.

The window a backtest actually uses when the caller names none is the
PLATFORM's, not a constant: the platform resolves
`max(universe_floor, series_era(timeframe))`. Every clock is served
from the 1-minute grid; 15-minute and coarser clocks also read the frozen
history of the retired venue 15m capture below each coin's first 1m bar, so
they reach back to the dates above, while a clock finer than 15 minutes has
no such history and can only start at the grid's era (**2025-03-22**), so
the same strategy on 5-minute bars
starts two quarters later than on 15-minute bars — by construction, not by
accident. The default SPAN per clock is 5min 60 days, 15min 90 days, and
5,000 bars for every coarser clock, era-capped. The run's own receipt echoes
the window it ran, and is the authority for it.

A backtest computes its signals over at least 500 bars of the timeframe
(at least 10 days, at most a year), so a short window uses the history
before it and trades from its first bar. That covers most strategies, not
all: long lookbacks or a start near the beginning of our data can still
leave warm-up inside the window. The run's `exposure` ("held positions on N
of M bars · first position …") shows how much of the window traded; a late
first position means start earlier. A finished run's summary takes `start`
/ `end` / `capital` to report part of it (last day, last week, on a given
capital).

"How far back can I test?" has a per-asset, data-driven answer: majors
reach back to mid-2024, newer coins to their listing.

### The backtest window rule

- **The platform's window is the default.** A backtest with no
  `start_date`/`end_date` runs the default window for the strategy's
  timeframe (above), never earlier than the universe's data, with the end
  clamped to today. A start date passed only to fill the field replaces
  that data-driven default.
- **A non-default window is a named period or a regime test** (e.g. "how
  did it do through the 2025 drawdown?").
- **A chosen window carries warm-up.** Most strategies
  need N indicator bars of warm-up before the first real signal. A short
  window gets history in front of it automatically (above); a lookback
  longer than that history, or a window longer than a year on a strategy
  with a long lookback, still warms up inside the window. A start set
  earlier makes the window of interest fully warm, and `exposure` shows how
  much of it traded.
- **A start near the data floor leaves less warm-up.** The earliest data
  for the universe bounds the history in front of the window, so a window
  starting close to the floor runs partly under-warmed.
