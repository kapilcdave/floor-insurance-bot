# Adaptive-width SPY 0DTE iron-fly research

Status: **rejected on training and validation; final holdout remains sealed**.

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

## Result

The locked run rejected the adaptive structure without opening the 60-session
holdout. The strategy-specific cache audit found no option data from May 26
through August 19, 2026.

| Split | Trades | Wins | Avg width | Average P&L | Total P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training | 155 | 50 | $2.41 | -$18.48 | -$2,865.00 | 0.2914 | -$2,865.00 |
| Validation | 59 | 26 | $2.78 | -$14.66 | -$864.80 | 0.4716 | -$927.00 |
| Training stress | 153 | 41 | $2.38 | -$26.13 | -$3,998.60 | 0.1607 | -$3,998.60 |
| Validation stress | 59 | 22 | $2.66 | -$22.08 | -$1,302.80 | 0.2902 | -$1,323.60 |

The base selector chose $2 wings on 101 training trades, $3 on 47, $4 on four,
and $5 on three. Validation shifted toward $3 wings (44 of 59 trades), which
raised the win rate from the narrow fly's 33.9% to 44.1%. It did not produce
positive expectancy. Losses were larger when the wider structure failed, and
both chronological splits remained decisively negative.

The $100 cap is binding in the way the hypothesis anticipated: broad wings
retain more premium, but most $4 and $5 structures require more than $100 of
defined risk and are rejected. The widths the account can afford still buy
back too much tail exposure to overcome four-leg friction. Changing the width
set, risk cap, or richness multiple after this result would be another strategy
and would require a new preregistration; none is promoted here.

The 10,000-path diagnostic bootstrap estimated zero positive 252-trade years
for this historical model, with median P&L of -$4,390.40. It is descriptive,
not a forecast.

## Reproduce the rejected run

```bash
floor-adaptive-iron-fly-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/adaptive-iron-fly-cache \
  --report-out state/adaptive-iron-fly-report.json
```
