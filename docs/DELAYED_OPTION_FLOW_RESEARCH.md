# Deployable delayed option-flow research

Status: **preregistered; not yet evaluated**.

The original opening-flow signal is modestly positive only under extremely
good modeled execution, and Alpaca documents that free indicative option
trades are delayed 15 minutes. A 10:00 live entry cannot observe the complete
09:30–09:59 signal window without paid data. This experiment tests the exact
deployment-compatible remedy: wait until 10:15, when the unchanged flow window
is available, then place the same $1 credit spread.

## Locked strategy

- Signal window, contracts, volume signs, 1,000-contract minimum, and absolute
  0.20 score threshold are unchanged from the original flow experiment.
- Signal bars: 09:30 through 09:59 America/New_York only. No 10:00–10:14 bar
  may enter the score.
- Signal strikes: nearest whole-dollar strike to the 10:15 SPY entry open plus
  one above and below. Selecting these strikes and then reading their completed
  earlier bars uses only information available at entry.
- Entry: exactly 10:15. Positive flow sells the whole-dollar put at/below SPY
  and buys $1 lower; negative flow sells the call at/above SPY and buys $1
  higher.
- Base execution remains deliberately unchanged: $0.02 adverse fill per leg at
  entry and exit plus $0.10 fees. Stress uses $0.03.
- Require positive credit below width and maximum loss no more than $100.
- One contract, no stop, target, re-entry, compounding, or added market filter;
  hard close at 15:00 with the same conservative missing-mark rule.

The execution-sensitivity result does not authorize replacing the base model
with midpoint fills. This candidate must first retain information after the
mandatory delay under the same conservative costs.

## Chronology and promotion

The first 433 pre-holdout sessions are training and the following 145 are
validation. May 26 through August 19, 2026 remains the 60-session final holdout
in a new cache and may not be fetched initially. The development dates are not
virgin because related flow results are known; they are a rejection gate for
this deployment change.

Promotion requires the unchanged option-flow gates: at least 100/30 trades,
both directions in both splits, positive average P&L, profit factor at least
1.25, drawdown no worse than -$500, and positive stress averages. Only then may
the untouched holdout be considered. Passing still authorizes paper limit-fill
measurement, not live money.

See [the original flow ledger](OPTION_FLOW_RESEARCH.md) for sources and the
bar-sign limitation. Alpaca's free-trade delay is documented at
<https://docs.alpaca.markets/us/docs/historical-option-data>.
