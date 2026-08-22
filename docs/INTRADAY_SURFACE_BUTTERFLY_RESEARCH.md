# SPY 0DTE intraday surface-butterfly research

Status: **passed the locked development screen; execution-sensitive paper
candidate only; final holdout remains sealed**.

This experiment tests whether the 11:00 local-surface result is present across
the regular session. It is a new hypothesis, not a reinterpretation of the
locked 11:00 result.

## Locked rules

- SPY same-session 0DTE options only.
- Scan at exactly 10:00, 11:00, 12:00, 13:00, and 14:00 ET.
- At each scan, use the nearest whole-dollar strike plus offsets -3 through +3.
- Construct matching $1-wide call and put butterflies at each center.
- Require a call/put butterfly trade-price gap of at least $0.08.
- Buy the cheaper butterfly for a modeled debit no greater than $0.10 after
  $0.005 adverse fill per contract unit; charge $0.20 per round trip.
- Take only the first qualifying scan each session. Never re-enter that day.
- Exit exactly one hour after entry using a synchronized mark no more than five
  minutes old. No stop, take profit, direction, regime, or event filter.
- Size one butterfly. The stress run charges $0.01 adverse fill per contract
  unit at entry and exit.

## Data and chronology

Use February 1, 2024 through August 19, 2026. Keep the final 60 sessions, May 26
through August 19, 2026, sealed and absent from the strategy-specific option
cache. Split earlier sessions chronologically 75% training and 25% validation.

The development screen passes only if both base splits have at least 100/30
trades, positive average P&L, profit factor at least 1.25, maximum drawdown no
worse than -$500, and at least ten call and put butterflies. Both stress splits
must have positive average P&L. Report selected trades by entry hour, but do not
choose or suppress an hour after seeing the result.

## Limitation

Alpaca Basic historical one-minute option trade bars are not synchronized
executable quotes. This remains a rejection screen: apparent intraday parity
gaps may be stale-print artifacts, and passing cannot authorize live money.

## Result

All 578 development sessions were evaluated. The strategy-specific cache ends
on May 22, 2026; its audit found no option file from the final 60-session May 26
through August 19 holdout.

| Split | Trades | Calls / puts | Average P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|
| Training | 427 | 182 / 245 | +$4.22 | 4.5521 | -$41.00 |
| Validation | 145 | 58 / 87 | +$3.99 | 3.4744 | -$33.80 |
| Training stress | 427 | 180 / 247 | +$2.58 | 2.4207 | -$48.00 |
| Validation stress | 145 | 53 / 92 | +$2.51 | 2.2491 | -$49.40 |

The locked development screen passed. The validation win rate was 55.17%, so
the modeled result came from payoff size rather than an implausibly high win
rate. The deterministic 10,000-path moving-block bootstrap estimated median
252-trade P&L of $1,044.60 and a 5th–95th percentile range of $813.60 to
$1,284.60. These are trade-bar model outputs, not live forecasts.

The first qualifying scan dominated selection:

| Entry | Training | Validation |
|---|---:|---:|
| 10:00 | 353 | 114 |
| 11:00 | 50 | 24 |
| 12:00 | 16 | 4 |
| 13:00 | 3 | 2 |
| 14:00 | 5 | 1 |

This is therefore mostly a 10:00 strategy, not evidence that every hour is
equally attractive.

## Post-result fill sensitivity

Additional adverse-fill levels were evaluated without changing the locked
pass. Cost is per contract unit at both entry and exit.

| Cost | Training trades / avg / PF | Validation trades / avg / PF |
|---:|---:|---:|
| $0.02 | 425 / +$0.97 / 1.4797 | 144 / +$0.90 / 1.4289 |
| $0.03 | 398 / -$0.65 / 0.7528 | 140 / -$1.01 / 0.6590 |
| $0.05 | 291 / -$1.95 / 0.3717 | 112 / -$1.91 / 0.3618 |

The apparent edge disappears between two and three cents of adverse fill per
contract unit. Combined with nonsynchronous trade bars, that prevents a live
promotion. Current executable quotes and atomic paper fills remain the next
gate; the historical holdout stays closed.

## Reproduce

```bash
floor-intraday-surface-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/intraday-surface-butterfly-cache \
  --report-out state/intraday-surface-butterfly-report.json
```
