# Online conformal SPY 0DTE iron-condor research

Status: **rejected on training and validation; final holdout remains sealed**.

This experiment tests a distributional pricing hypothesis rather than a fixed
technical rule. A lightweight HAR-style model forecasts SPY's absolute move
from 11:00 to 15:00. Rolling conformal residuals turn that point forecast into
an empirical 90% upper bound. The strategy sells a defined-risk iron condor
only when the option market pays enough credit outside that predicted range.

The model, chronology, execution assumptions, and rejection rule below are
frozen before any strategy-specific option legs are fetched or scored.

## Locked forecasting model

- Underlying: SPY one-minute bars, sampled without using data after entry.
- Entry: 11:00 America/New_York.
- Target: absolute dollar move from the 11:00 SPY open to the 15:00 SPY close.
- Morning realized scale: square root of the sum of squared log returns from
  09:30 through 11:00, multiplied by the 11:00 price.
- Predictors: intercept, log morning realized scale, log mean target over the
  prior five completed sessions, and log mean target over the prior 20
  completed sessions. Every positive quantity has a $0.01 floor before logs.
- Estimator: rolling ridge regression on the prior 120 completed sessions with
  lambda 0.001 applied to non-intercept coefficients.
- Conformal calibration: for each of the latest 40 prior sessions, create a
  genuine one-step-ahead forecast from its preceding 120 observations. Take
  the nearest-rank 90th percentile of those 40 absolute log residuals.
- Current upper move bound: exponentiate the current log forecast plus the
  conformal residual quantile.
- Warmup: skip until both the 120-session forecast window and 40 one-step
  conformal residuals exist. No missing value is carried or replaced with zero.

This is an online calculation. A session's target may enter the model only
after that session has completed. There is no random train/test shuffle and no
future normalization.

## Locked option structure

- Expiration: same session (SPY 0DTE).
- Short put: floor of `11:00 spot - conformal upper move`.
- Short call: ceiling of `11:00 spot + conformal upper move`.
- Long wings: exactly $1 farther out on each side.
- Size: one four-leg iron condor, no compounding.
- Base entry credit: exact 11:00 trade-bar credit less $0.005 adverse fill per
  leg, rounded down to cents.
- Minimum post-cost credit: $0.10. Credit must remain below the $1 wing width.
- One-contract defined loss including $0.20 fees must not exceed $100.
- Exit: no take profit and no stop; close at 15:00 using an exact synchronized
  mark or the last synchronized mark no more than five minutes old. Missing
  close data is charged the full $1 width.
- Base exit debit adds $0.005 adverse fill per leg and rounds up to cents.
- Stress independently charges $0.01 adverse fill per leg at entry and exit.
- No direction, VIX, economic event, weekday, option-flow, trend, or post-result
  filter is permitted.

The model is deliberately symmetric and direction-neutral. Its proposed edge
is that the physical 90% interval may occasionally be narrower than the range
for which option buyers pay, not that SPY will rise or fall.

## Chronology and holdout

Use February 1, 2024 through August 19, 2026. The final 60 sessions, May 26
through August 19, 2026, are sealed. Only the earlier development dates may be
fetched. The first 75% of development is training and the last 25% validation.

Use a new strategy-specific cache. The report must list any pre-existing option
file inside the holdout and must neither read nor create such a file.

## Promotion rule

Development passes only if every condition holds without changing a parameter:

- at least 100 training trades and 30 validation trades;
- positive average net P&L and profit factor of at least 1.25 on both base
  splits;
- maximum drawdown no worse than -$500 on either base split;
- positive average P&L on both stress splits;
- realized containment by the predicted symmetric range between 85% and 95%
  on both chronological base splits.

Coverage is a model-calibration gate, not an excuse to promote an unprofitable
strategy. A moving-block bootstrap is diagnostic only. Passing would authorize
an atomic paper mechanics probe, not live money or holdout access.

## Research basis and limitations

The physical distribution and the option-implied risk-neutral distribution can
differ because investors pay premia for volatility, skewness, and kurtosis
risk. Conformal methods provide distribution-free calibration tools, including
variants designed for changing time series. Recent 0DTE factor research also
finds that apparent residual alpha becomes infeasible under small transaction
costs, so this test prices every leg and requires execution stress.

- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2902209>
- <https://proceedings.mlr.press/v204/canete23a.html>
- <https://papers.neurips.cc/paper/2021/hash/0d441de75945e5acbc865406fc9a2559-Abstract.html>
- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7149778>
- <https://docs.alpaca.markets/us/docs/historical-option-data>

Alpaca Basic history is not a synchronized OPRA quote history. The bar-based
fill model can reject a strategy but cannot prove that a live atomic order was
executable. No amount of conformal calibration removes that limitation.

## Result

The forecast passed its calibration gate but the trade failed its availability,
sample-size, and economic gates. The run evaluated 433 training and 145
validation sessions while leaving all 60 sessions from May 26 through August
19, 2026 sealed. The strategy-specific holdout cache audit was empty.

| Split | Forecasts | Containment | Trades | Average credit | Average P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training | 245 | 93.88% | 11 | $0.17 | -$12.75 | 0.2728 | -$173.40 |
| Validation | 145 | 94.48% | 5 | $0.28 | -$16.80 | 0.1876 | -$90.80 |
| Training stress | 245 | 93.88% | 8 | $0.17 | -$12.08 | 0.1869 | -$116.00 |
| Validation stress | 145 | 94.48% | 5 | $0.26 | -$20.40 | 0.0778 | -$104.80 |

The model's realized containment sits inside the preregistered 85% through 95%
band on both chronological splits. The average upper move was $8.17 in training
and $8.96 in validation. Those honest tail bounds pushed the short strikes so
far from SPY that most $1 wings either lacked an exact trade at 11:00 or paid
less than the $0.10 minimum credit. Only 16 base trades survived, far below the
required 100 and 30.

The few surviving trades were not promising. Seven of 11 training trades and
three of five validation trades won, yet one tail loss overwhelmed several
small credits. Refunding the entire $4.20 base round-trip friction assumption
would still leave average P&L near -$8.55 and -$12.60. The negative result is
therefore not explained by the half-cent per-leg fill model.

This experiment separates forecasting from trading: the conformal model
delivered the requested coverage, but correctly calibrated insurance was too
far out of the money to monetize with a small defined-risk spread. Narrowing
the coverage after seeing this result would simply understate tail risk and is
not permitted. No bootstrap is reported because 16 trades cannot support one.

The strategy is not connected to paper or live orders, and the holdout remains
sealed.

## Reproduce the rejected run

```bash
floor-conformal-condor-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/conformal-condor-cache \
  --report-out state/conformal-condor-report.json
```
