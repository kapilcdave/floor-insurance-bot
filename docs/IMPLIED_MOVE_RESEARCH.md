# SPY implied-move credit-spread research

## Pre-registration

This experiment was committed before historical option results were fetched.
It asks whether a volatility-normalized downside strike improves on the rejected
fixed-dollar buffer without adding a directional signal.

Six variants are fixed in advance:

- SPY 0DTE, one contract, $1 spread width;
- entry at 10:00 ET;
- the implied move is the 10:00 price of the ATM call plus ATM put;
- the short put is one or 1.25 implied moves below SPY, rounded down;
- minimum modeled executable credit of $0.15;
- a spread-debit stop at twice entry credit;
- 50%, 75%, or no profit target;
- hard close at 15:00 ET;
- $0.02 adverse fill per leg at entry and exit plus $0.10 round-trip fees;
- an additional $0.03-per-leg stress run;
- chronological training and validation only; the final holdout is sealed.

A candidate needs at least 100 training and 30 validation trades, positive
average P&L and profit factor above 1.15 on both splits, drawdown no worse than
five $100 risk units, and positive average P&L on both stress splits. Validation
is a pass/fail gate, not another surface to optimize.

## Data limitation

Alpaca provides historical option trades and one-minute trade bars, but not a
historical option NBBO endpoint. A call trade, put trade, short-put trade, and
long-put trade within the same minute were not necessarily executable together.
Charging every leg helps reject weak structures but cannot turn trade bars into
quotes. A positive result would therefore remain research-blocked until it
survived forward OPRA bid/ask collection.

## Result

Reject every variant. No configuration passed the predeclared rule, and the
July 20 through August 19, 2026 holdout remains sealed with no option cache
files created for it.

The inspected sample contained 461 training and 154 validation sessions. At
one implied move, only 15 training sessions and six validation sessions met the
$0.15 cost-adjusted credit floor. All three exits lost on both splits:

| Variant | Train trades | Train avg | Train PF | Validation trades | Validation avg | Validation PF |
|---|---:|---:|---:|---:|---:|---:|
| 50% target | 15 | -$2.30 | 0.7016 | 6 | -$1.43 | 0.7485 |
| 75% target | 15 | -$2.30 | 0.6102 | 6 | -$3.43 | 0.3977 |
| Hold to stop/close | 15 | -$1.77 | 0.8047 | 6 | -$0.60 | 0.8947 |

At 1.25 implied moves, there were only two training trades and no validation
trades. One two-trade row was positive, but it is not evidence and failed the
minimum sample gate by almost two orders of magnitude. Raising adverse fill to
$0.03 per leg reduced the one-move training sample to seven trades; every
training average remained negative.

The structural failure is the same as the fixed-distance strategy: once the
short strike is far enough away to feel safe, a $1-wide spread rarely pays 15
cents after conservative costs. The few qualifying sessions did not compensate
for their losing exits.

At 14:44 ET on August 20, 2026, a read-only live mechanics check using Alpaca's
free indicative feed observed SPY at 763.55 and a 1.38-point ATM-straddle move.
The one-move 762/761 put spread showed a $0.10 executable credit; the 1.25-move
761/760 spread showed $0.02. Both failed the locked minimum. This late-day,
non-OPRA snapshot is not part of the backtest and is not evidence about a 10:00
fill.

The result does not authorize an implied-move selector in the trading engine.
Changing the move multiple, minimum credit, time, or stop after this result
would be a new hypothesis requiring a new pre-registration and untouched data.

## Reproduce without revealing the holdout

```bash
floor-implied-move-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-07-20
```
