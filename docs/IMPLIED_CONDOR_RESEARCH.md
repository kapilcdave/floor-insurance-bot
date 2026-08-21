# Implied-move SPY 0DTE iron-condor research

Status: **preregistered; results not yet evaluated**.

This experiment tests a direction-neutral volatility-risk-premium hypothesis:
when the same-day ATM straddle prices substantially more movement than SPY has
recently realized, an out-of-the-money iron condor may retain enough premium to
survive its four option legs. It differs structurally from the rejected iron
fly: the short strikes sit away from spot instead of at the center strike.

The rules, chronology, costs, and rejection gate below are frozen before any
strategy-specific option legs are fetched or results are calculated.

## Locked strategy

- Underlying: SPY.
- Expiration: same session (0DTE).
- Entry: 11:00 America/New_York.
- Hard close: 15:00 ET; never hold through the final hour or expiration.
- ATM implied-move proxy: the 11:00 opening trade prices of the put and call at
  the whole-dollar strike rounded down from the 11:00 SPY open.
- Realized-move reference: the arithmetic mean absolute SPY move from the 11:00
  open to 16:00 close over the prior 20 completed sessions.
- Richness gate: the ATM straddle proxy must be at least 1.25 times the trailing
  realized-move reference.
- Short put: floor of `spot - 0.75 × implied move`.
- Short call: ceiling of `spot + 0.75 × implied move`.
- Wings: buy the put exactly $1 below the short put and the call exactly $1
  above the short call.
- Size: one four-leg condor, no compounding.
- Entry credit: raw four-leg opening-bar credit less $0.01 adverse fill per leg,
  rounded down to cents. Net entry credit must be at least $0.15 and below $1.
- Maximum loss: `($1 - entry credit) × 100 + $0.20 fees`; it must not exceed
  $100.
- Exit: no take profit and no stop. Use the exact synchronized 15:00 close mark,
  or the last synchronized mark no more than five minutes old. Missing exit data
  is charged the full $1 width.
- No trend, direction, futures, economic-calendar, weekday, or post-result
  parameter filter is permitted.

The base model charges $0.01 adverse fill on each of four legs at entry and
again at exit, plus $0.20 per completed condor. The stress model independently
charges $0.02 per leg in each direction. Stress entries must pass the same
credit and risk gates under their own worse fill.

## Chronology and sealed holdout

The dataset runs from February 1, 2024 through August 19, 2026. The final 60
sessions, May 26 through August 19, 2026, are reserved out of sample. Only the
earlier development sessions may be fetched. The first 75% of development is
training and the final 25% validation.

The strategy uses a new cache. The initial report must list any pre-existing
strategy-specific option file in the holdout and must not load or create one.
Overlapping dates used by other experiments do not unseal this independently
preregistered strategy.

## Promotion rule

Development passes only if every condition holds without changing a parameter:

- at least 100 training trades and 30 validation trades;
- positive average net P&L and profit factor of at least 1.25 on both base
  splits;
- maximum drawdown no worse than -$500 on either base split;
- positive average P&L on both stress splits.

A deterministic moving-block bootstrap is diagnostic and cannot rescue a
failed chronological split. Passing development would authorize only an
atomic paper fill probe. It would not authorize live orders or reveal the
sealed holdout.

## Why this mechanism is worth falsifying

Federal Reserve research reports that insurance against price, variance, and
downside risk is more expensive around several major macroeconomic releases,
but event-risk work also finds that a priced risk does not automatically create
an expected-return premium. This experiment therefore measures the full spread
payoff rather than assuming that a high implied move is an edge.

Alpaca historical option bars are one-minute trade aggregates from the free
indicative dataset, not synchronized executable OPRA quotes. Conservative
per-leg costs make this a rejection screen; even a passing result would require
forward testing against actual fills.

- <https://www.federalreserve.gov/econres/ifdp/the-price-of-macroeconomic-uncertainty-evidence-from-daily-options.htm>
- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4484011>
- <https://docs.alpaca.markets/us/docs/historical-option-data>

