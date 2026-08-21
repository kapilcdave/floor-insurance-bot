# Sparse-IEX constituent lead-lag research

Status: **rejected; final holdout remains undownloaded**.

The strict 12-of-12 exact-bar test produced no training signals because IEX is
one exchange and individual minute bars can be absent. This follow-up changes
only the data-completeness rule. It is designed to match what a live collector
could calculate from the latest observed liquid-name prices without inventing
prices for missing symbols.

## Locked data fallback

- Universe, 10:55–10:59 observation window, equal weighting, residual formula,
  trailing 60-session 70th-percentile threshold, 11:00 entry, 11:30 exit, and
  one/two-basis-point costs remain unchanged.
- For each constituent, use the first available bar's open and last available
  bar's close inside 10:55–10:59.
- A constituent qualifies only if its first bar is no later than 10:56 and its
  last bar is no earlier than 10:58. A single bar cannot satisfy both bounds.
- Require at least 8 of the fixed 12 constituents. Equal-weight only the names
  that independently pass the coverage rule that session.
- SPY must retain exact 10:55, 10:59, 11:00, and 11:30 bars. No fallback is
  allowed for the traded instrument.
- Missing constituents cannot be carried forward from before 10:55, assigned a
  zero return, or replaced with another symbol.

This permits the basket composition to vary and therefore adds measurement
noise and composition bias. Metrics must report the average and minimum member
count on signals. Passing would require a fresh forward collector to confirm
the residual with consolidated or quote-based prices.

## Chronology and promotion

The already-cached February 1, 2024 through May 22, 2026 development data may
be replayed; all cache files end before the holdout. The chronological 75/25
split and known May 26–August 19 final holdout remain unchanged.

The original acceptance gate remains fixed: at least 80/25 signals, both
directions, positive base average, win rate above 52%, profit factor at least
1.15, drawdown no worse than -1.00%, and positive two-basis-point stress
averages in both splits. Passing authorizes an options preregistration only,
not holdout access or orders.

See [the strict data rejection](CONSTITUENT_LEAD_RESEARCH.md) for the mechanism,
fixed universe, and primary source.

## Result

The sparse rule solved data availability but rejected the economic hypothesis.
It produced 105 training and 44 validation signals, with both directions and an
average 10.68/11.75 members. No constituent or SPY file extends beyond May 25,
so the May 26–August 19 holdout remains undownloaded.

| Split | Signals | Bull / bear | Win rate | Gross avg | Net avg | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training | 105 | 45 / 60 | 49.5% | +0.52 bp | -0.48 bp | 0.9453 | -2.46% |
| Validation | 44 | 22 / 22 | 47.7% | -4.18 bp | -5.18 bp | 0.4003 | -2.53% |
| Training stress | 105 | 45 / 60 | 46.7% | +0.52 bp | -1.48 bp | 0.8399 | -2.82% |
| Validation stress | 44 | 22 / 22 | 40.9% | -4.18 bp | -6.18 bp | 0.3274 | -2.85% |

Training was indistinguishable from noise before costs, and validation moved
opposite the proposed catch-up direction. Reversing the signal after observing
that result would be data mining rather than a new independent mechanism.

The 10,000-path block bootstrap assigned only a 15.59% probability to positive
mean net return over a 149-signal sample; its median average was -1.92 basis
points. The input does not pass the stock-only gate, so no options data,
strategy, or order path is added.

## Reproduce the rejected run

```bash
floor-sparse-constituent-lead-research \
  --start 2024-02-01 \
  --oos-start 2026-05-26 \
  --oos-end 2026-08-19 \
  --cache-dir state/constituent-lead-cache \
  --report-out state/sparse-constituent-lead-report.json
```
