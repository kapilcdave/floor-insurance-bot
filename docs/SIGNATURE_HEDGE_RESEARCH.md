# SPY 0DTE Itô-signature replication research

Status: **protocol locked before any strategy-specific data is fetched. No
simulator exists yet and no result has been read.**

This tests one narrow claim from Guo, Wang, and Zhang, *Tradable Itô
Signatures: A Model-Free, Interpretable Framework for Dynamic Hedging*
(arXiv:2608.18120): that a linear combination of discretized Itô-signature
coordinates of the underlying path, fitted by Lasso and implemented as the
corresponding self-financing strategies, replicates an option payoff more
accurately out of sample than a Black–Scholes delta hedge on the same
rebalancing grid.

It is a replication-error study. It is not connected to the order engine, it
never prices or trades an option, and passing it would not establish a tradable
edge. See the limitations section before reading any number this harness emits.

## Why this version is the narrowest faithful test

The paper's Theorem 1 states that every nonconstant discretized Itô-signature
coordinate is the terminal gain of an adapted self-financing strategy in the
underlying and cash. Its Framework 1 fits payoff coefficients by linear
regression and then reuses those same coefficients as portfolio weights. Both
steps therefore require only the underlying price path.

That is the whole reason this project can test the paper at all. This repo has
one-minute SPY trade bars and no synchronized NBBO option quotes, so it cannot
evaluate a method that needs option marks at every rebalance. The signature
hedge does not need them: the fitted intercept is the initial cash position and
the target is a terminal payoff, which for a 0DTE contract is a deterministic
function of the settlement path. Option data enters nowhere on the signature
side, and the benchmark uses a public volatility index fixed before the session.

## Locked construction

- Underlying: SPY, one-minute bars, existing stock feed.
- One path per session, and exactly one observation per session. Windows are
  disjoint, so the sample carries no overlap. This is a deliberate departure
  from the paper, whose 146,223 exotic observations are roughly 3,000 sessions
  crossed with seven maturities and seven moneyness levels and therefore
  support no valid standard error.
- Session window: 10:00 through 15:45 America/New_York.
- Rebalancing grid: every 15 minutes from 10:00 to 15:45, giving 24 grid points
  and 23 intervals. A session missing any grid bar is excluded and recorded as
  excluded; it is never filled forward.
- Normalization: `X_k = S_k / S_10:00` and `t_k = (minutes since 10:00) / 345`,
  so the time-augmented path is `(X_k, t_k)` with `X_0 = 1` and `t ∈ [0, 1]`.
  Both coordinates are dimensionless, which makes signature coordinates
  comparable across sessions and across SPY price levels.
- Signature: discretized Itô signature of the time-augmented path, computed by
  the paper's recursion. Primary truncation order `m = 3`, giving 14
  coordinates. The three pure-time coordinates are deterministic on a fixed
  grid and are absorbed into the intercept, leaving 11 stochastic tradable
  coordinates. Order `m = 4` is a declared secondary and is reported alongside;
  it does not decide the outcome.

## Locked target payoffs

Strikes are the exact 10:00 spot, not a traded strike, because this measures
replication and never buys a contract. All payoffs are in units of `S_10:00`.

- Call: `max(X_23 − 1, 0)`.
- Put: `max(1 − X_23, 0)`.
- Geometric-average Asian call: `max(Ḡ − 1, 0)` with
  `Ḡ = exp(mean(log X_k))` over all 24 grid points, matching the paper's
  Appendix D.3 definition.

The Asian payoff is included because the paper's central claim is that gains
are largest for path-dependent contracts. It costs nothing extra here.

## Locked estimation

- Fit on training sessions only: regress the payoff on the stochastic
  signature coordinates by Lasso with a free intercept, features standardized
  by training-set mean and standard deviation.
- The penalty is chosen by five-fold cross-validation using contiguous
  chronological blocks of the training window. No randomized folds, no
  validation or holdout data, and no outcome-driven reselection.
- One coefficient vector per payoff per split boundary. The paper's
  signature-kernel path-similarity weighting is deliberately out of scope; see
  the prior stated below.
- No third-party numeric dependency. Coordinate-descent Lasso, the normal CDF
  via `math.erf`, and the signature recursion are implemented in-repo, matching
  this project's standard-library-only style.

## Locked hedge implementation

- Convert each fitted coefficient into positions by the Theorem 1 recursion and
  hold the summed position over each 15-minute interval. Terminal wealth is
  `p0 + Σ θ_j (X_{j+1} − X_j)`.
- Required identity check: implemented wealth must equal
  `β0 + Σ β_Γ · Sig_Γ` to within `1e-9` on every session, for both truncation
  orders. This is Theorem 1 evaluated numerically. A single failure invalidates
  the run and blocks any reported comparison.
- Benchmark: Black–Scholes delta hedge on the same grid with `r = q = 0`,
  `σ` = prior session's VIX1D close divided by 100, and time to expiry measured
  to **15:45 ET**. Initial cash is the Black–Scholes price at 10:00 under the
  same `σ`. The Asian benchmark uses the exact discrete-grid geometric-average
  price and delta under the same `σ`.
- Annualization is not uniquely determined for an intraday horizon, so both a
  trading-time convention (252 × 390 minutes per year) and a calendar
  convention (365 days) are evaluated. The benchmark is credited with whichever
  produces the *lower* error, so the comparison is conservative against the
  signature hedge.

- Costs: charge 1 basis point of traded notional on `|θ_{j+1} − θ_j|` at every
  rebalance, and on the initial and final position, for both methods
  identically. A 2 basis point stress is evaluated as a separate declared
  variant. The paper reports costs only as a 1 bp appendix robustness check,
  which understates the friction a high-turnover fitted hedge generates.
- Record maximum absolute position and mean per-rebalance turnover for both
  methods. A hedge that replicates well by demanding large leverage is not
  usable in this account regardless of its error.

### Corrections made before the first run

Recorded rather than silently applied. No data had been fetched.

1. Time to expiry was originally written as 16:00 ET while the payoff is
   defined at 15:45. The target contract expires at the end of the hedging
   window, so 15:45 is correct and 16:00 was an error.
2. The Asian benchmark originally cited the paper's Appendix C.2 continuous-
   averaging formula. Because the payoff here is a discrete grid average, the
   exact discrete-grid lognormal price and delta are derived and used instead.
   This makes the benchmark stronger, not weaker.
3. The two annualization conventions above were unspecified. Both are now
   evaluated, with the better one credited to the benchmark.

### Declared post-lock diagnostic

Reported alongside the gated numbers and clearly labelled as a diagnostic, not
a gate. The benchmark's initial cash is replaced by the training-mean residual
`p0 = mean(payoff − hedge gain)`, which is the best constant cash the benchmark
could have been given. This isolates whether any signature advantage is a
better hedge or merely a better initial cash level — a control the paper omits.
It can only make the signature comparison look worse, never better.

## Metric

Terminal hedging error `e = payoff − terminal wealth`, in units of `S_10:00`,
reported as mean absolute error ×10⁻³ to match the paper's tables. Mean squared
error is secondary. Per-session paired differences against the benchmark are
summarized by a paired sign test and a deterministic moving-block bootstrap.

Win rate is reported but is explicitly **not** a gate. A high win rate with a
small mean improvement is the signature of frequent small wins against rare
large losses, and the paper's headline win rates carry no valid standard error.

## Chronology and sealing

Development window February 1, 2024 through May 22, 2026. The 60 sessions from
May 26 through August 19, 2026 are sealed. The first 75% of development
sessions is training and the last 25% validation.

Sealing here works differently from the option-cache experiments in this repo,
because the underlying bars *are* the strategy data. The development run must
request SPY bars only through May 22, 2026 and store them in a range-named
cache file. The holdout range is never requested. The report records the cache
filenames actually present so that any premature fetch is visible.

## Promotion gates

Development passes only if every condition holds with no parameter changed:

- at least 300 training and 100 validation sessions with complete grids;
- the Theorem 1 identity holds to `1e-9` on every session;
- lower mean absolute terminal error than the benchmark on both training and
  validation, for all three payoffs, after 1 bp costs;
- validation mean absolute error reduced by at least 10% for the Asian payoff
  and not worsened for the call or the put;
- still better than the benchmark on validation under the 2 bp stress;
- validation maximum absolute position no greater than 3 units of underlying
  per unit option, and mean per-rebalance turnover no greater than 0.5.

Passing would authorize exactly one follow-up: testing whether the paper's
signature-kernel weighting adds anything on top of a base version that already
works. It would not authorize opening the holdout, connecting the order engine,
or risking money.

## Stated prior: this is expected to fail

The paper's own Appendix D.6 reports that the unweighted Itô-signature hedge is
substantially *worse* than the benchmark on the exotic contracts. For Asian
calls its OlsSig mean error is 22.67 against 6.435 for Monte Carlo, and
LassoSig is 10.55, still well behind. Nearly all reported improvement arrives
only with signature-kernel path-similarity weighting whose bandwidth was tuned
on a development period. The honest reading of Table D.6 is that the headline
result belongs to a kernel-weighted local Lasso, not to the Itô-signature basis
itself.

This test evaluates the base version first anyway, because the alternative is
to start from a tuned localization and never learn whether the basis does any
work. A negative result is the expected outcome and is recorded as such.

## Limitations

- This measures replication error, not profit. No option price enters the
  signature side, so a favorable result means the payoff is easier to
  synthesize, not that money can be made.
- A 0DTE at-the-money option's gamma diverges into the close. A 15-minute grid
  is coarse for both methods, so this compares two coarse hedges rather than
  approaching continuous replication.
- VIX1D is an SPX one-day implied volatility used as a preregistered SPY proxy.
  The benchmark is therefore a plain delta hedge, not the paper's practitioner
  delta backed out of each contract's own observed price. It is a weaker
  benchmark in one direction and a cleaner one in another, since it cannot peek
  at the contract being replicated.
- Roughly 470 training and 155 validation sessions, against the paper's 32,768
  simulated training paths. This is the small-sample regime in which the paper
  claims an advantage, which is the point of testing it, but power is limited.
- A single coefficient vector spans a two-year training window that includes
  several volatility regimes. Non-stationarity is not addressed in this
  version, by design.
- One-minute SPY trade bars are not the consolidated tape at those timestamps.
  Rebalances are modeled at bar opens with the declared cost charge.

## References

- <https://arxiv.org/abs/2608.18120>
- <https://doi.org/10.1080/14697688.2019.1571683>
- <https://doi.org/10.1287/opre.49.3.372.11221>
- <https://doi.org/10.1080/1350486X.2020.1846573>
- <https://www.cboe.com/tradable_products/vix/vix_historical_data/>
- <https://docs.alpaca.markets/us/docs/historical-stock-data>

## Planned interface

Not implemented yet. Reserved names, so the eventual simulator cannot quietly
reuse another experiment's cache:

```bash
floor-signature-hedge-research \
  --start 2024-02-01 \
  --end 2026-05-22 \
  --oos-start 2026-05-26 \
  --cache-dir state/signature-hedge-cache \
  --report-out state/signature-hedge-report.json
```

## Result

Not run.
