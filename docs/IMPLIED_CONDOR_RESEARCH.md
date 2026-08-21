# Implied-move SPY 0DTE iron-condor research

Status: **rejected on training and validation; final holdout remains sealed**.

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

## Result

The locked experiment failed every economic promotion gate. It evaluated 433
training and 145 validation sessions while leaving all 60 sessions from May 26
through August 19, 2026 sealed. The strategy-specific audit found no option
cache file in that holdout.

| Split | Trades | Wins | Average credit | Average P&L | Total P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training | 173 | 88 | $0.29 | -$8.11 | -$1,403.60 | 0.4142 | -$1,403.60 |
| Validation | 62 | 32 | $0.33 | -$10.22 | -$633.40 | 0.4263 | -$668.60 |
| Training stress | 158 | 55 | $0.27 | -$16.06 | -$2,537.60 | 0.1347 | -$2,537.60 |
| Validation stress | 62 | 23 | $0.29 | -$17.96 | -$1,113.40 | 0.1764 | -$1,129.20 |

The sample-size gates were satisfied, so this is not a sparse-result problem.
The base condor won only 50.9% of training trades and 51.6% of validation
trades while its average one-contract defined risk was $70.79 and $67.18. The
OTM short strikes improved the win rate relative to the rejected ATM iron fly,
but not enough to compensate for the losing tail.

Base friction is $8.20 per round trip: one cent on each of four legs at entry
and exit, plus $0.20 fees. Adding all of it back is an intentionally optimistic
friction-free upper bound. Training would average approximately +$0.09, while
validation would still average -$2.02. Thus execution consumes a nearly flat
training payoff, but it does not explain the negative validation payoff. At two
cents per leg both splits deteriorate sharply.

The deterministic 10,000-path moving-block bootstrap estimated zero probability
of a positive 252-trade year under this historical model. Median annual P&L was
-$2,174.40. This is descriptive of the rejected specification, not a forecast.

No parameter is reversed or retuned after seeing the result. The condor is not
connected to paper or live order submission, and the live fill probe is not
authorized by this result.

## Reproduce the rejected run

```bash
floor-implied-condor-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/implied-condor-cache \
  --report-out state/implied-condor-report.json
```
