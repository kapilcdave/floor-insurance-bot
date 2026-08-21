# SPY 0DTE variance-premium iron-fly research

Status: **preregistered; not yet evaluated**.

This experiment tests a specific economic hypothesis: same-day SPY options can
price intraday jump and variance risk above subsequently realized movement. It
does not assume that option selling is automatically profitable. The four-leg
structure is accepted only when current option-implied movement is materially
richer than a trailing, observable realized-movement estimate and when one
contract's expiration-defined loss is no more than $100.

The specification, chronology, costs, and pass/fail gate below are frozen before
the simulator is run. Results will be recorded whether they pass or fail.

## Locked hypothesis

- Underlying: SPY.
- Expiration: same session (0DTE).
- Entry: 11:00 America/New_York, after the opening period in which published
  0DTE research finds concentrated jump activity.
- Center strike: nearest whole-dollar strike to the 11:00 SPY open, with exact
  half dollars rounded upward.
- Structure: sell the center put and center call, buy the put exactly $2 below
  and the call exactly $2 above (one $2-wide iron fly).
- Realized-move reference: the arithmetic mean of the absolute SPY move from
  the 11:00 open to the 16:00 close over the prior 20 completed sessions. Only
  information available before the current entry is used.
- Implied-move proxy: the raw 11:00 opening trade prices of the center put plus
  center call (the ATM straddle).
- Richness gate: enter only if the implied-move proxy is at least 1.25 times the
  trailing realized-move reference.
- Entry credit: raw four-leg credit less $0.02 adverse fill on each leg, rounded
  down to cents. Do not enter if credit is non-positive or at least the wing
  width.
- Risk gate: one-contract maximum loss, `(wing width - entry credit) × 100`
  plus $0.20 round-trip fees, must be no more than $100.
- Size: exactly one contract, with no compounding.
- Take profit: none.
- Stop loss: none. The wings are the stop; this avoids assuming that a
  four-leg stop order fills during the jump for which the seller is being paid.
- Hard close: 15:00 ET. The simulation never assumes expiration settlement or
  holds through the final hour.

No direction, futures, trend, volatility-index, economic-calendar, weekday, or
post-result parameter filter is permitted in this experiment.

## Historical execution model

Alpaca historical option bars are one-minute trade aggregates rather than
synchronized NBBO quotes. This is therefore a rejection screen, not evidence
that a live four-leg limit order would fill.

The base model applies $0.02 adverse fill to each of four legs at entry and
again at exit, plus $0.20 per completed iron fly. All four legs must have a
trade bar at exactly 11:00. The 15:00 exit uses synchronized close marks and
adds four-leg adverse fill; if 15:00 is missing, the last synchronized mark no
more than five minutes old may be used. Otherwise the full $2 wing width is
charged.

The stress model raises adverse fill to $0.03 per leg at entry and exit. It
does not reuse a base-model entry: its own worse credit must independently pass
the $100 risk gate.

## Chronology

Data begins February 1, 2024, Alpaca's documented start of historical options
coverage, and ends August 19, 2026. The final 60 sessions, May 26 through
August 19, 2026, are sealed. Of the preceding sessions, the first 75% are
training and the final 25% are validation.

The initial command may fetch and evaluate only training and validation option
dates. It must report the sealed boundary and audit whether strategy-specific
holdout cache files already existed without loading them. Other experiments
have used overlapping SPY history, so only this specification—not the market
period—is new.

## Promotion rule

Development passes only if every condition holds without changing a parameter:

- at least 100 training trades and 30 validation trades;
- positive average P&L and profit factor of at least 1.25 on both base splits;
- maximum drawdown no worse than five $100 risk units (-$500) on either base
  split;
- positive average P&L on both stress splits.

A deterministic moving-block bootstrap is diagnostic and cannot rescue a
failed chronological split. Passing development would authorize only a paper
four-leg fill/mechanics probe. It would not authorize live money or reveal the
sealed holdout.

## Evidence and limitations

The mechanism is grounded in published work finding meaningful 0DTE jump-risk
premia, including concentrated open/close jump activity. Separate research
also warns that transaction costs and estimation error can make apparent 0DTE
volatility arbitrage economically insignificant. This experiment explicitly
tests whether enough premium survives conservative costs in this small-account
structure.

- <https://papers.ssrn.com/sol3/Delivery.cfm/5223127.pdf?abstractid=5223127&mirid=1>
- <https://www.federalreserve.gov/econres/ifdp/the-price-of-macroeconomic-uncertainty-evidence-from-daily-options.htm>
- <https://www.sciencedirect.com/science/article/pii/S0304407624000782>
- <https://bmvh162.ust.hk/bizinsight/2026/04/are-0dte-options-mispriced>
- <https://docs.alpaca.markets/us/docs/historical-option-data>
