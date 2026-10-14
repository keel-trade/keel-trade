## Backtest costs

### Hyperliquid base fees

- Maker: 1.5 bps (0.015%)
- Taker: 4.5 bps (0.045%) — raised from 3.5 bps in May 2025
- Volume-tier discounts exist for high-volume traders; the above is base.

### Backtest cost defaults

- `fees = 0.00045` (4.5 bps per trade — matches HL taker)
- `slippage = 0.00045` (4.5 bps execution stress)
- Combined ~9 bps per order (fee + slippage), charged on every order — a
  trade that opens and later closes a position pays it on both orders
  (~18 bps)
- A backtest's `fees=` / `slippage=` parameters override both defaults.
- Backtest costs model the exchange (**venue**) fee and slippage only; no
  other fee is in any backtest cost path.

### A maker-fee backtest is a scenario, not the live default

- Live execution fills at the taker rate unless the account's org holds the
  maker execution grant, which users cannot turn on themselves. A backtest
  run at the maker fee is a what-if, not what a live deployment gets by
  default: present the taker-fee result as the achievable number.
