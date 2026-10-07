## Pipeline Completeness

Every source has a pipeline stage — data, signal, forecast or sized — and is `backtest_ready` only when it ends in weights; a stage check names what is missing. A backtest of a pipeline that does not end in weights is refused before it runs (`PIPELINE_NOT_BACKTEST_READY`, with the missing step named).

## Backtest Mechanics — Single Continuous Simulation

The backtest is **one continuous simulation** over the requested window — capital evolves continuously, there is no daily reset, no withdrawal/redeposit, and no fresh-capital-per-day. The window is half-open `[start_date, end_date)` so `start_date` must be strictly before `end_date`; for a single trading day, set them to consecutive days. With both dates omitted the platform runs the default window for the strategy's timeframe; a named period is passed as exactly those dates, and a short window's indicators are computed over earlier history automatically, though long lookbacks can still leave some warm-up inside it.

Three separate layers:

- **Strategy** — signal logic and pipeline composition. The backtest replays it.
- **Execution** — how target weights translate to trades (`every_bar` / `on_change` / `buffered`). The backtest models it.
- **Simulation framing** — capital model, withdrawal cadence, multi-account aggregation, daily reset, "what if I added $X each week". **NOT in backtest scope today.**

A "simulation framing" question (fresh capital per day, daily P&L decomposition, profit withdrawal model, per-week comparisons) is answered from **one** backtest over the full window: the decomposition comes from its trades list and equity curve. N short backtests answer a different question and give wrong numbers — each short window gets its own warmup and capital base.
