# Adaptive-width SPY 0DTE iron-fly research

Status: **preregistered; not yet evaluated**.

The locked $2-wing iron fly was approximately flat before an optimistic removal
of friction and deeply negative after four-leg costs. Its narrow wings bought
back most of the tail-risk premium. This follow-up tests one structural remedy:
retain more tail exposure when the live entry credit still caps one-contract
loss at $100. It does not change the timing, volatility signal, costs, or
chronology after seeing the first result.

## Locked strategy

- SPY 0DTE, exactly one contract, entered at 11:00 America/New_York and closed
  at 15:00.
- Center: nearest whole-dollar strike to the 11:00 SPY open, with half dollars
  rounded upward.
- Candidate symmetric wing widths: $5, $4, $3, then $2, in that order.
- Each candidate sells the center put and call and buys the put/call exactly
  the candidate width away.
- Entry credit: raw four-leg 11:00 opening trade-bar credit less $0.02 adverse
  fill per leg, rounded down to cents.
- Selection: choose the widest candidate with all four exact entry bars,
  positive credit below its width, and maximum loss of no more than $100 after
  $0.20 fees. Do not use exit data in selection and do not fall back to any
  width below $2.
- Volatility filter: unchanged from the first test. The ATM straddle opening
  price must be at least 1.25 times the mean absolute 11:00-to-16:00 SPY move
  over the prior 20 completed sessions.
- No take profit or stop loss; the selected wings define risk. Close at 15:00,
  using an exact synchronized mark or the last synchronized mark no more than
  five minutes old. Missing close data is charged at the selected wing width.
- No direction, futures, trend, volatility-index, event, weekday, or other
  filter.

The base model charges $0.02 adverse fill on each of four legs at entry and
exit plus $0.20 fees. The stress model charges $0.03 per leg in both
directions. Stress selection is independent: a width passing the base fill may
fail or select differently under stress.

## Chronology and promotion rule

The dataset and split remain fixed: February 1, 2024 through August 19, 2026;
433 training sessions; 145 validation sessions; and the final 60 sessions from
May 26 through August 19, 2026 sealed in a new strategy-specific cache. Only
training and validation options may be fetched on the initial run.

Development passes only with at least 100 training and 30 validation trades,
positive average P&L and profit factor of at least 1.25 on both base splits,
drawdown no worse than -$500 on either base split, and positive average P&L on
both stress splits. A bootstrap is diagnostic only. Passing permits a paper
four-leg mechanics probe, not live trading or holdout reveal.

Alpaca option bars are trade aggregates rather than synchronized NBBO quotes.
As with the first test, conservative modeled fills can reject this structure
but cannot prove live executability. See
[the narrow-fly ledger](IRON_FLY_RESEARCH.md) for the mechanism, primary
research, and full data limitations.
