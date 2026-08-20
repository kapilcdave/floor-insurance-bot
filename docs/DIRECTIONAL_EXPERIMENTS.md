# Directional experiment ledger

Fourteen fixed hypotheses have now been declared and evaluated without fetching
or reporting option data from the sealed July 20 through August 18, 2026
holdout. Round three extended the research window, introduced the first input
that is not derived from the SPY price path, and uncovered a comparability
defect that had made one variant look like a winner.

Nothing passed. No model is wired into the trading bot.

## Window

Alpaca serves one-minute option bars well before the previous start date, so the
window was extended from one year to two and a half years before any new
hypothesis was tested. Statistical power, not another threshold, was the
binding constraint: the loosest signal previously produced only 30 validation
trades, so any regime filter layered on top would have been rejected on trade
count rather than on evidence.

| Split | Sessions | Range |
|---|---:|---|
| Train | 461 | 2024-02-01 to 2025-12-03 |
| Validation | 154 | 2025-12-04 to 2026-07-17 |
| Out of sample | 22 | 2026-07-20 to 2026-08-18 (sealed) |

Sixteen sessions lack a complete 09:30 to 09:45 record on the free `iex` feed
and are therefore skipped as unsignalled rather than partially evaluated.

## New information source

The 09:45 SPY path had been exhausted. The only genuinely new input available
without a data licence is the published Cboe volatility complex, cached under
`state/backtest-cache/cboe-*.csv`.

| Series | Sessions | First | Last |
|---|---:|---|---|
| VIX | 9,253 | 1990-01-02 | 2026-08-18 |
| VIX1D | 1,069 | 2022-05-13 | 2026-08-18 |
| VIX9D | 3,928 | 2011-01-04 | 2026-08-18 |
| VIX3M | 4,254 | 2009-09-18 | 2026-08-18 |
| VVIX | 5,085 | 2006-03-06 | 2026-08-18 |

Every snapshot is taken from the most recent close *strictly before* the trading
date, so a signal cannot see the session it trades. Intraday volatility proxies
were rejected instead of used: over 41 sample sessions the `iex` feed delivered
a complete 09:30 to 09:45 record for VIXY on 1 session and for UUP on none, so
minute-level volatility ETF data is not usable on this entitlement.

`VIX_History.csv` publishes rows on market holidays while VIX9D and VIX3M do
not, so the raw VIX dates are not a trading calendar. The calendar is the
intersection of the long-history series, 3,928 sessions from 2011-01-04. Without
that correction, 20 holiday rows in the window became snapshot dates on which
the term-structure series were absent, and the volatility filters silently
abstained on the following session.

## Sizing comparability

The first path-dependent result was a false positive, and it is worth recording
in full. Position size is proportional to the running balance, so a filter that
avoids losses keeps a larger balance and can afford days the unfiltered path had
to skip. Under that sizing the three volatility families did not reconcile
against the unfiltered breakout:

| Family | Split | Partition trades | Unfiltered trades | Unexplained P&L |
|---|---|---:|---:|---:|
| low/high VIX | train | 170 | 131 | $877.90 |
| low/high VIX | validation | 53 | 90 | $805.30 |
| contango/backwardation | train | 176 | 131 | -$72.50 |
| contango/backwardation | validation | 84 | 90 | $44.50 |
| cheap/rich 1D | train | 144 | 131 | $403.30 |
| cheap/rich 1D | validation | 78 | 90 | -$271.20 |

Each pair splits the same sessions on one threshold, so these residuals should
be zero. They are not, which means a filtered result cannot be compared with the
unfiltered one under proportional sizing at all.

Two path-independent controls were therefore added:

- `--constant-sizing` applies the same 2% risk rule to the starting balance. The
  day set and the contract count become identical across variants while the risk
  cap stays intact. This is the faithful control and the basis for every
  decision below.
- `--fixed-contracts` additionally ignores the risk budget. It takes premium the
  2% cap would forbid, so it answers a different question and is a diagnostic
  only.

Under constant sizing all three families reconcile exactly: 238 train trades and
88 validation trades in every family, with zero unexplained P&L.

## Rules

Acceptance, declared before round one:

- positive training P&L;
- positive validation P&L;
- profit factor above 1 in both splits;
- at least 20 validation trades.

Promotion, declared before any path-independent ledger was run:

- pass acceptance under equity-proportional sizing; **and**
- pass acceptance again under constant reference equity; **and**
- reconcile exactly against the unfiltered breakout under constant sizing.

## Results under constant sizing

| Variant | Train trades | Train P&L | Train PF | Validation trades | Validation P&L | Validation PF | Accepted |
|---|---:|---:|---:|---:|---:|---:|---|
| `breakout_1500` | 238 | -$1,760.90 | 0.8311 | 88 | $400.20 | 1.1059 | No |
| `volume_breakout_1200` | 110 | -$1,025.10 | 0.7256 | 40 | -$330.00 | 0.7712 | No |
| `vwap_momentum_1130` | 76 | -$1,151.60 | 0.5641 | 39 | -$718.90 | 0.5283 | No |
| `breakout_1030` | 238 | -$1,894.90 | 0.6734 | 88 | -$863.80 | 0.6120 | No |
| `breakout_1200` | 238 | -$2,320.90 | 0.7123 | 88 | -$284.80 | 0.9044 | No |
| `vwap_reversion_1130` | 41 | -$283.10 | 0.7973 | 25 | -$240.50 | 0.7481 | No |
| `gap_continuation_1200` | 88 | -$749.80 | 0.7574 | 44 | -$448.40 | 0.7345 | No |
| `gap_fade_1200` | 118 | -$1,027.80 | 0.7306 | 32 | -$204.20 | 0.8294 | No |
| `breakout_1500_low_vix` | 87 | -$948.80 | 0.7435 | 36 | $497.40 | 1.3832 | No |
| `breakout_1500_high_vix` | 151 | -$812.10 | 0.8793 | 52 | -$97.20 | 0.9608 | No |
| `breakout_1500_contango` | 220 | -$1,014.10 | 0.8905 | 80 | $570.00 | 1.1702 | No |
| `breakout_1500_backwardation` | 18 | -$746.80 | 0.3586 | 8 | -$169.80 | 0.6037 | No |
| `breakout_1500_cheap_1d` | 192 | -$1,583.30 | 0.8091 | 70 | $684.00 | 1.2465 | No |
| `breakout_1500_rich_1d` | 46 | -$177.60 | 0.9167 | 18 | -$283.80 | 0.7169 | No |

Accepted models: 0. Every variant loses money in training. The four with
positive validation P&L share the same explanation: the December 2025 to July
2026 stretch was kind to bought premium. That is a property of the period, not
of any model.

The equity-proportional ledger reports `breakout_1500_contango` as accepted,
with training P&L of $199.20 and a profit factor of 1.0301. Under constant
sizing the same variant loses $1,014.10 in training at a profit factor of
0.8905. Roughly $200 of the apparent training edge came from compounding a
different balance, not from the filter. The promotion rule rejects it.

## What the volatility complex did explain

The new input is not worthless; it ranks regimes consistently. Under constant
sizing, backwardation sessions returned a 0.3586 training profit factor against
0.8905 for contango, and sessions where 1-day implied volatility was rich
relative to 9-day were the worst small subset. Buying 0DTE debit spreads is
clearly more expensive in stressed, inverted volatility. But the favourable half
of every partition still loses in training, so the information reduces damage
without producing an edge that survives modeled slippage and fees.

## Reproduce

```bash
floor-directional-experiments \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20 \
  --constant-sizing
```

Drop `--constant-sizing` for the equity-proportional ledger, or pass
`--fixed-contracts 1` for the unbudgeted diagnostic. Cboe history is downloaded
once and reused from the cache afterwards, so reruns are offline and
deterministic. The report lists any pre-existing option cache file inside the
holdout; that list is empty.

## Next

Two and a half years of SPY minute bars, the published volatility complex, and
fourteen declared hypotheses produce no candidate. Further variants on this
signal family would be noise fitting. A next cycle needs a different kind of
input, and the cost of each is now known:

- licensed OPRA historical quotes, which would also replace the research fill
  model with executable bid/ask;
- reliable intraday breadth or cross-asset data, which the free `iex` feed
  cannot supply for the symbols that would carry the signal;
- index futures history for the overnight session.

Until then the sealed 22 sessions stay sealed, and the order-submission state
machine keeps running the unrelated floor-insurance credit strategy in
zero-capital shadow mode.
