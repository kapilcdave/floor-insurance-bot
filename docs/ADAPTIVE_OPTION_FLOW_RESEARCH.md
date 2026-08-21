# Adaptive-width SPY 0DTE option-flow credit-spread research

Status: **rejected structurally; final holdout remains sealed**.

The locked $1 opening-flow spread showed a consistent but economically small
friction-free upper bound in training and validation, then lost after fixed
two-leg execution costs. This follow-up changes only the payoff scale: select a
wider spread when its live entry credit still holds one-contract defined risk
to $100. Signal timing, threshold, exit, costs, and chronology remain fixed.

## Locked strategy

- Reuse the opening-flow score exactly: three near-ATM call and put strikes,
  09:30–09:59 bar-signed volume, at least 1,000 contracts, and absolute score
  at least 0.20.
- Enter at 10:00 and hard-close at 15:00. Positive score sells a put credit
  spread; negative score sells a call credit spread.
- The short strike is unchanged: whole-dollar put at/below spot or call
  at/above spot.
- Candidate widths are $3, $2, then $1, evaluated widest first.
- For every width, model entry credit as the exact 10:00 short-minus-long
  trade-bar open less $0.02 per leg. Choose the widest positive credit below
  width whose `(width - credit) × 100 + $0.10 fees` is no more than $100.
- Width selection uses only entry-time data. It may fall back to a narrower
  candidate but never changes direction or signal qualification.
- Exactly one contract; no take profit, stop, re-entry, compounding, or other
  filter. Missing/stale hard-close data is charged conservatively as in the $1
  test.
- Stress independently selects width using $0.03 adverse fill per leg at entry
  and exit.

The candidate set is fixed before evaluation. Wider verticals still use only
two legs, so the modeled dollar friction remains approximately fixed while
directional exposure can increase. The $100 gate prevents this scaling from
silently increasing the proposed pilot loss.

## Chronology and promotion

February 1, 2024 through May 22, 2026 remains development data: 433 training
and 145 validation sessions under the existing chronological rule. The May 26
through August 19 final 60-session holdout stays sealed in a new cache.

Because the same development period motivated this structural follow-up,
training and validation are not represented as virgin evidence. They remain a
rejection gate. Only the final holdout is untouched for this specification,
and it will not be opened unless development passes unchanged.

Development requires at least 100 training and 30 validation trades, both
directions in both splits, positive average P&L, profit factor at least 1.25,
drawdown no worse than -$500 in each base split, and positive stress averages.
Passing permits only a paper mechanics probe before any separately approved
live pilot.

See [the original option-flow ledger](OPTION_FLOW_RESEARCH.md) for the signal
definition, sources, and free-data limitation.

## Result

The remedy could not activate. Across all 122 training and 53 validation
entries, neither the $2 nor $3 candidate ever passed the locked $100 maximum-
loss gate. Base and stress therefore selected $1 on every trade and reproduced
the original result exactly: -$4.03 per training trade and -$5.04 per
validation trade in base, with still worse stress performance.

This is a structural rejection rather than a new signal failure. An ATM-ish
$2 credit spread would need at least about $1.01 net credit to keep defined
risk plus fees within $100. None of the qualifying entries supplied it after
the locked adverse fill. Widening the hedge cannot scale the possible gross
edge without also exceeding the proposed pilot loss.

No option data from the final May 26 through August 19 holdout was fetched. The
strategy is not promoted and the risk cap is not raised after observing this
result.

## Reproduce the rejected run

```bash
floor-adaptive-option-flow-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/adaptive-option-flow-cache \
  --report-out state/adaptive-option-flow-report.json
```
