## Costs and fees

Facts to ground answers about execution costs. Backtest defaults are
realistic. The user (or you, when relevant) can override the `fees` /
`slippage` params on `run_backtest` to model something different.

### Hyperliquid base fees

- Maker: 1.5 bps (0.015%)
- Taker: 4.5 bps (0.045%) — raised from 3.5 bps in May 2025
- Volume-tier discounts exist for high-volume traders; the above is base.

### Keel live execution is taker-only

Live orders go out as `limit_ioc` (Immediate-Or-Cancel) or `market` —
both are aggressing orders that cross the spread, so live fills pay the
**4.5 bps taker** rate. Maker fills don't occur on Keel today.

### Backtest cost defaults

- `fees = 0.00045` (4.5 bps per trade — matches HL taker)
- `slippage = 0.00045` (4.5 bps execution stress)
- Combined per-trade round-trip cost ~9 bps
- Override via the `fees=` / `slippage=` params on `run_backtest`.

### Keel builder fees (live only)

On top of the HL taker fee, Keel charges a per-trade builder fee that
varies by plan:

| Plan    | Builder fee | All-in live taker cost |
| ------- | ----------- | ---------------------- |
| Free    | 5.0 bps     | 9.5 bps                |
| Starter | 3.0 bps     | 7.5 bps                |
| Trader  | 2.0 bps     | 6.5 bps                |
| Pro     | 1.0 bps     | 5.5 bps                |

Mention plan tiers when the user's question naturally invites it
(comparing backtest to live cost, asking about live PnL, asking about
high-turnover viability). For a high-turnover strategy on free, moving
to Trader or Pro materially reduces per-trade drag. Don't lead with
upsells — answer the user's question first, then surface the option if
it's actually relevant.
