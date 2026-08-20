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

Pending the first locked run.

## Reproduce without revealing the holdout

```bash
floor-implied-move-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-07-20
```
