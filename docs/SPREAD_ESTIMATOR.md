# Effective spread recovered from trade bars

Status: **result recorded; the modeled fill cost is vindicated and the binding
constraint moves to the signal**.

Run with `floor-spread-estimator calibrate` and `floor-spread-estimator estimate`.
Cache-only: the module holds no Alpaca client, opens no socket, and refuses every
session on or after the sealed `2026-05-26` boundary. A test asserts the absence
of `AlpacaClient`, `requests`, `urlopen`, and `/v1beta1` from its source.

## The question

Every screen in this project subtracts a *modeled* fill cost from a trade-print
P&L. The surviving [surface butterfly](SURFACE_BUTTERFLY_RESEARCH.md) uses half a
cent per contract unit and its validation turns negative somewhere between two
and three cents. That single unmeasured number therefore decides the only line
still alive, and it decides retroactively whether the eighteen rejected lines
were rejected fairly or merely graded harshly.

The [parity recorder](PARITY_RECORDER.md) will measure it forward over twenty
sessions, because no vendor sells historical option NBBO quotes at any price.
This module does not wait, and does not need quotes. Four microstructure
estimators recover an effective spread from transaction prices alone, and the
cache already holds 1-minute OHLC, volume, and VWAP for nine strikes of both
types across 575 pre-holdout sessions.

## Calibrate before measuring

Each estimator runs first on synthetic minute bars built from a multiplicatively
diffusing mid plus a pure bid/ask bounce at a *known* spread, so any error is the
estimator's bias rather than a property of the market. Deterministic LCG, fixed
seed, `--seed` to vary it.

| case | price | true | rel. vol/min | Roll | Corwin-Schultz | Abdi-Ranaldo |
|---|---:|---:|---:|---:|---:|---:|
| atm_tight | 1.00 | 0.010 | 1.0% | 0.012 (1.20x) | 0.009 (0.90x) | 0.010 (1.00x) |
| atm_typical | 1.00 | 0.020 | 2.0% | 0.023 (1.15x) | 0.017 (0.85x) | 0.020 (1.00x) |
| atm_wide | 1.00 | 0.050 | 2.0% | 0.054 (1.08x) | 0.047 (0.94x) | 0.051 (1.02x) |
| atm_violent | 1.00 | 0.020 | 6.0% | 0.037 (1.85x) | 0.017 (0.85x) | 0.028 (1.40x) |
| cheap_wing | 0.20 | 0.020 | 2.0% | 0.021 (1.05x) | 0.019 (0.95x) | 0.020 (1.00x) |
| cheap_wing_violent | 0.20 | 0.020 | 6.0% | 0.028 (1.40x) | 0.018 (0.90x) | 0.026 (1.30x) |
| rich_atm | 3.00 | 0.030 | 1.5% | 0.039 (1.30x) | 0.024 (0.80x) | 0.031 (1.03x) |

Median bias factors, divided out of every reported estimate: Roll `1.20`,
Corwin-Schultz `0.90`, Abdi-Ranaldo `1.02`.

One of these directions is the opposite of what the literature predicts.
Corwin-Schultz is documented as biased **upward** when volatility is large
relative to the spread, which is exactly the 0DTE regime, yet here it runs 10 to
20 percent **low** and is the most volatility-robust of the three. The published
bias comes from *daily* bars, whose high-low range spans a full session of drift.
A one-minute range on a liquid series is mostly bid/ask bounce, so the drift term
that inflates the daily estimator is nearly absent. The practical consequence is
that the three estimators bracket the answer from both sides instead of bounding
it from one.

An earlier version of this grid used an additive mid and produced a spurious
3.1x failure on the cheap wing. That was the simulator's own price floor acting
as a reflecting barrier, not an estimator defect. The mid is multiplicative now.

## Result

575 sessions, `2024-02-01` through `2026-05-22`, 50,554 series-windows at a
minimum of 20 bars and 50 contracts traded per window. Raw medians in dollars per
contract unit.

| moneyness | Corwin-Schultz | Abdi-Ranaldo | range decomp. | Roll | implied \|delta\| |
|---|---:|---:|---:|---:|---:|
| ATM (0-1) | 0.008 | 0.012 | 0.040 | 0.046 | 0.384 |
| 1-2 away | 0.006 | 0.012 | 0.032 | 0.046 | 0.369 |
| 2-3 away | 0.004 | 0.012 | 0.020 | 0.042 | 0.317 |
| wing 3+ | 0.000 | 0.010 | 0.011 | 0.032 | 0.215 |

| time of day | Corwin-Schultz | Abdi-Ranaldo | range decomp. | Roll |
|---|---:|---:|---:|---:|
| 09:30-10:00 | 0.006 | 0.011 | 0.029 | 0.067 |
| 10:00-11:00 | 0.006 | 0.011 | 0.029 | 0.054 |
| 11:00-12:00 | 0.005 | 0.011 | 0.026 | 0.042 |
| 12:00-14:00 | 0.003 | 0.012 | 0.020 | 0.029 |
| 14:00-15:00 | 0.003 | 0.011 | 0.013 | 0.027 |

**The range decomposition passes its own falsification test.** Its slope is an
implied `|delta|`, and it decays monotonically from 0.384 at the money to 0.215
in the wings across 50,000 independent regressions. Calls and puts are pooled, so
the ATM value sits below the 0.5 a single type would give. A decomposition
returning noise could not produce that profile, so its intercept is measuring
bounce rather than absorbing whatever is left over.

Bias-corrected half-spread per contract unit — what a trader filling at the mid
actually pays on each leg:

| estimator | all strikes | ATM only |
|---|---:|---:|
| Corwin-Schultz | 0.003 | 0.004 |
| Abdi-Ranaldo (primary) | 0.006 | 0.006 |
| range decomposition | 0.012 | 0.020 |
| Roll | 0.018 | 0.019 |

## Verdict

**The modeled half-cent per contract unit is not optimistic.** The two
well-calibrated estimators put the ATM half-spread at `0.004` and `0.006` around
a modeled `0.005`. The level that kills the surviving line is `0.03`, which
implies a six-cent round-trip spread on a one-dollar option — five times anything
measured here, across 575 sessions.

Cost is therefore not what limits these strategies, and **no rejected line can be
revived by assuming cheaper fills.** That closes off an entire category of
"maybe it works if execution is better" and is worth more than a new signal.

Read alongside the [parity diagnostic](SURFACE_PARITY_DIAGNOSTIC.md), it also
relocates the surface butterfly's problem. The print-based parity gap that
generates its edge is $0.18 to $0.26; the first quote-based look put the same gap
near $0.06. The edge is far more likely to be a nonsynchronous-print artifact in
the **signal** than a fill-cost problem, which sharpens what the parity recorder
has to settle: not whether the gap is affordable to cross, but whether it exists
at all.

## What this does not establish

- **Conditioning on having traded.** An effective spread recovered from prints
  describes the minutes in which somebody chose to trade. The market at an
  arbitrary moment is wider, which is exactly the selection the forward recorder
  avoids by sampling on a clock instead of on activity.
- **The top of the bracket is unresolved.** Roll reads 0.046 at the money, nearly
  four times Abdi-Ranaldo, while its measured synthetic bias is only 1.20x. Its
  real-data reading exceeds its own calibrated bias, which means the grid does not
  reach the real per-minute volatility regime. Treat the low end as the
  measurement and the high end as an open bound.
- **Cheap wings defeat the range estimators.** Corwin-Schultz reads exactly
  `0.000` beyond three dollars out because every print in a minute lands on one
  tick, so high equals low. That zero is an artifact of penny quoting, reported as
  a hard zero rather than dressed up as a tight market.
- **Roll is undefined 30% of the time** (15,115 of 50,554 windows) because the
  serial covariance came out positive. That is reported as undefined rather than
  floored at zero, since positive covariance falsifies Roll's model instead of
  indicating a tight market.
