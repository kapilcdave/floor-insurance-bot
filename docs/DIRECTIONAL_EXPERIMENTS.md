# Directional experiment ledger

This ledger was declared and evaluated without fetching or reporting option
data from the explicit July 20 through August 18, 2026 holdout. All variants
used the same $5,000 balance, 2% maximum premium risk, $3 spread width, 2:1
minimum maximum-payoff ratio, five cents modeled slippage per side, and ten
cents modeled fees per spread.

Acceptance required all of the following before OOS could be considered:

- positive training P&L;
- positive validation P&L;
- profit factor above 1 in both splits;
- at least 20 validation trades.

| Experiment | Train trades | Train P&L | Train PF | Validation trades | Validation P&L | Validation PF | Accepted |
|---|---:|---:|---:|---:|---:|---:|---|
| Breakout, 15:00 close | 101 | $949.90 | 1.2213 | 30 | -$728.00 | 0.5476 | No |
| Volume breakout, 12:00 | 41 | -$439.10 | 0.7229 | 5 | -$185.50 | 0.1693 | No |
| VWAP momentum, 11:30 | 41 | -$306.10 | 0.8002 | 4 | -$193.40 | 0.0000 | No |
| Breakout, 10:30 close | 76 | -$369.60 | 0.8047 | 14 | -$221.40 | 0.4221 | No |
| Breakout, 12:00 close | 100 | $82.00 | 1.0249 | 27 | -$500.70 | 0.4864 | No |
| VWAP reversion, 11:30 | 19 | -$167.90 | 0.7867 | 8 | $173.20 | 2.1831 | No |
| Gap continuation, 12:00 | 21 | -$764.10 | 0.1798 | 10 | $92.00 | 1.3480 | No |
| Gap fade, 12:00 | 39 | $8.10 | 1.0063 | 7 | -$413.70 | 0.0000 | No |

Positive results that appear in only one split or a handful of trades are
regime dependence, not sufficient evidence of an edge. None passed, so the
held-out option data must remain sealed and none may be wired into the bot.

Reproduce the complete ledger:

```bash
floor-directional-experiments \
  --start 2025-08-18 \
  --end 2026-08-18 \
  --oos-start 2026-07-20
```

The next research cycle must introduce genuinely new information rather than
more thresholds on the same 09:45 SPY path. Examples include a licensed OPRA
quote history, volatility-regime inputs, market breadth, or reliable futures
history. Parameters should be declared before evaluation, and the existing OOS
period should not be used until a model passes the development rule.
