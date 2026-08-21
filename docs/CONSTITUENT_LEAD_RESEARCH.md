# SPY constituent lead-lag research

Status: **preregistered; not yet evaluated**.

This experiment tests a new input family before touching options. Published
research reports that lagged constituent returns contain short-horizon index
return information not already present in the index, with stronger
predictability during the middle of the day. We test a small, fixed,
resource-light proxy using prices available on Alpaca's free real-time IEX
stock feed.

If the underlying forecast fails, no option structure will be fitted around
it. If it passes, a separate options implementation must be preregistered and
charged its own execution costs.

## Locked universe and signal

- Target: SPY.
- Fixed constituent basket: AAPL, MSFT, NVDA, AMZN, META, GOOGL, AVGO, BRK.B,
  JPM, XOM, LLY, and WMT.
- The basket is equal-weighted. No weights, members, or sectors may change
  after results are observed.
- Observation window: 10:55 open through 10:59 close, America/New_York.
- For every session, calculate each constituent's five-minute return and the
  contemporaneous SPY return. All 12 constituents and SPY must have both exact
  bars; otherwise skip the session.
- Lead residual: arithmetic mean constituent return minus SPY return.
- Maintain the absolute lead residuals from the prior 60 complete sessions.
  The current day qualifies when its absolute residual is at least the nearest-
  rank 70th percentile of those prior values. The current session never enters
  its own threshold.
- Positive residual predicts SPY up; negative predicts SPY down.
- Entry price: SPY 11:00 open. Exit price: SPY 11:30 open, exactly 30 minutes
  later.
- Gross strategy return: SPY return multiplied by predicted direction.
- Base cost: one basis point round trip, deducted from every signal. Stress
  cost: two basis points.
- Constant one-unit exposure. No sizing, stop, option pricing, volatility,
  futures, weekday, event, or post-result filter.

The fixed basket is a proxy rather than the paper's full cross-section and has
survivorship and equal-weighting limitations. IEX is one exchange rather than
the consolidated SIP. The test requires exact liquid-name price bars and does
not use IEX volume, but a positive result would still require replication on a
fresh forward sample.

## Chronology and acceptance

Only February 1, 2024 through May 22, 2026 may be downloaded for this first
run. Those 578 development sessions split chronologically 75%/25% into 433
training and 145 validation sessions. Constituent cache filenames must end
before the final holdout.

May 26 through August 19, 2026 is the known 60-session final holdout. It is not
downloaded into the strategy-specific cache and is not simulated initially.

Development passes only if every condition holds unchanged:

- at least 80 training and 25 validation signals;
- bullish and bearish signals in both splits;
- positive average net return and win rate above 52% in both base splits;
- profit factor at least 1.15 in both base splits;
- cumulative maximum drawdown no worse than -1.00% in either base split;
- positive average net return in both two-basis-point stress splits.

Passing authorizes only preregistration of an options implementation. It does
not reveal the holdout or authorize paper/live orders.

Primary mechanism:
<https://academic.oup.com/jfec/article-abstract/21/2/485/6400345>.
