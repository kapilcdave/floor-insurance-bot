# ATM 20-day-trend credit-spread research

## Decision

Do not promote this strategy to live trading. No configuration passed the
predeclared acceptance rule. The final July 20 through August 18, 2026 holdout
remains sealed for SPY, QQQ, and IWM.

The only result worth collecting forward is an **IWM crossover** entry with no
profit target and a spread-debit stop. It was positive after modeled costs on
both inspected splits, but the sample was only 23 training trades and 11
validation trades. That is a shadow candidate, not evidence of a dependable
edge.

## Fixed experiment

The research implementation and acceptance rule were committed before option
history was fetched. Every variant used:

- a completed-session 20-day simple moving average;
- either every close above the average or only a fresh crossover;
- a 09:45 ET entry on the following session;
- one $1-wide put spread with the short strike at or immediately below spot;
- spread-debit stops at 1.5 or 2 times entry credit, or no stop;
- a 50% credit profit target or no profit target;
- a 15:00 ET hard close;
- $0.02 slippage on each spread transaction and $0.10 fees per round trip;
- chronological training and validation ending July 17, 2026.

A candidate needed positive average net P&L and profit factor above one on both
splits, with at least 100 training trades and 30 validation trades. Candidates
would have been ranked by training average only, with validation used as a
pass/fail gate.

Historical option prices are one-minute trade bars, not contemporaneous NBBO
quotes. Explicit costs make the result more conservative, but only forward
OPRA shadow quotes can measure executable fills.

## Results after modeled costs

| Underlying and variant | Train trades | Train avg | Validation trades | Validation avg | Verdict |
|---|---:|---:|---:|---:|---|
| SPY above, 1.5x stop, no target | 321 | -$3.55 | 99 | -$2.06 | Reject |
| SPY crossover, 2x stop, no target | 20 | +$2.75 | 9 | +$1.57 | Too small |
| QQQ above, hold, 50% target | 299 | -$3.30 | 87 | -$0.33 | Reject |
| QQQ crossover, 1.5x stop, no target | 25 | -$2.42 | 13 | -$12.33 | Reject |
| IWM above, 2x stop, no target | 256 | -$3.63 | 108 | -$3.19 | Reject |
| IWM crossover, 1.5x stop, no target | 23 | +$1.64 | 11 | +$1.45 | Shadow only |
| IWM crossover, 2x stop, no target | 23 | +$1.42 | 11 | +$4.63 | Shadow only |

All other declared variants were negative on at least one split or failed the
sample threshold. QQQ was uniformly unattractive. Every daily-regime variant
lost after costs.

The SPY zero-cost upper bound explains the failure. Its best daily-regime
variant averaged only $1.34 in training and $0.80 in validation before any
friction. The modeled round trip costs more than that small raw edge.

## Reproduce without revealing the holdout

```bash
floor-atm-trend-research \
  --symbol SPY \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20

floor-atm-trend-research \
  --symbol IWM \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20
```

Do not add a holdout-reveal switch until a candidate has accumulated enough
untouched forward observations and is frozen in a separate commit.
