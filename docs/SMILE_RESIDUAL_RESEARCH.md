# Cross-sectional smile residuals: rejected, and the reason generalises

Status: **rejected. The entire result was the entry print.** The more useful
finding is the second one below: the persistence filter this project has been
relying on does not detect stale prints, and an entry delay does.

Run with `floor-smile-residual-research`. Cache-only, no client, no socket, and
the sealed `2026-05-26` boundary is never read — a test asserts the absence of
`AlpacaClient`, `requests`, `urlopen`, and `/v1beta1` from the module source.

## The idea

For each minute, the butterfly at strike offset `i`

    B_i = P(K_{i-1}) - 2 P(K_i) + P(K_{i+1})

is a discrete second derivative of price in strike, so it is proportional to the
risk-neutral density there and must be positive under no-arbitrage. A Gaussian
density makes `log B_i` a parabola in `i`, so fitting

    log B_i ~ a + b i + c i^2

across one timestamp's strikes gives a residual `r_i = log B_i - fitted_i` that
is positive where the market prices more density than that strike's own
neighbours imply. Sell the rich butterfly, buy the cheap one, hold 30 minutes.

The fit uses only strikes present in that single minute, and only offsets whose
three legs all printed in that same minute. Nothing crosses sessions and no
parameter is learned from graded data, so there is no leakage to argue about.

## Preregistered acceptance rule

> At least 150 training and 40 validation trades; positive average P&L and
> profit factor at least 1.25 on both splits at the measured fill cost; positive
> average P&L on both splits at double that cost; the edge must not collapse when
> required persistence rises from one minute to three; **and it must survive
> filling one minute after the signal instead of at the signal minute's own
> print.** The sealed holdout is never read.

Fill cost is the `$0.006` per contract unit measured by the
[spread estimator](SPREAD_ESTIMATOR.md), charged on all four legs at entry and
exit — eight units plus $0.20 of fees, $0.25 a round trip. That measurement is
why cost is not available as an excuse here.

## Data

578 session files in `state/intraday-surface-butterfly-cache`, twelve strikes of
both types, split chronologically 75/25. 432 training and 145 validation panels
built, ending `2026-05-22`; the one session dropped (`2024-02-02`) has empty bar
arrays for all 24 of its option symbols in the cache. Zero sealed sessions are present — they were
never downloaded, which is a stronger guarantee than declining to read them.

2,057,290 butterflies examined across 224,062 fitted cross-sections.

## Residual dynamics

| quantity | value |
|---|---:|
| median absolute residual | 0.2093 |
| convexity violation rate | 0.1088 |
| lag-1 autocorrelation | 0.0984 |
| lag-3 | 0.0832 |
| lag-5 | 0.0764 |
| lag-15 | 0.0476 |
| median half-life | 0.34 minutes |

**One butterfly in nine is negative** — a flat no-arbitrage violation that cannot
exist in simultaneous quotes. That rate alone measures how badly nonsynchronous
prints distort this cross-section.

A lag-1 autocorrelation of 0.098 is a third of a minute of memory. The residual
is very nearly white noise; it has decayed before an order could reach the
exchange. There is no dislocation here that a trader could act on, so a
simulation reporting profit must be getting it from the prices it transacts at
rather than from the signal.

## The persistence ladder says yes

Training and validation, average P&L per trade in dollars on a one-butterfly
position, at the measured fill cost:

| required persistence | train n | train avg | train PF | val n | val avg | val PF | bootstrap P(profitable year) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 minute | 429 | **4.53** | 4.23 | 145 | **5.58** | 7.73 | 1.00 |
| 3 minutes | 429 | **3.57** | 3.74 | 145 | **3.30** | 3.50 | 1.00 |
| 5 minutes | 429 | **2.73** | 3.09 | 139 | **2.70** | 3.14 | 1.00 |

Every conventional check passes, and passes handsomely. Profit factor between 3
and 4. Win rate 69%. Validation agrees with training. A 10,000-path moving-block
bootstrap puts the probability of a profitable year at 1.00. The edge survives
double fill cost. It degrades gracefully rather than vanishing as the persistence
requirement tightens — exactly the shape a real effect is supposed to have.

Every number in that table is an artifact.

## The entry delay says no

Same rule, same trades, same 3-minute persistence. The only change is which
minute the order fills in.

| entry delay | train n | train avg | train PF | train win rate | val n | val avg | val PF | bootstrap P(profitable year) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 minutes | 429 | **+3.57** | 3.74 | 0.688 | 145 | **+3.30** | 3.50 | 1.00 |
| 1 minute | 429 | **−0.20** | 0.93 | 0.497 | 145 | **−0.11** | 0.95 | 0.36 |
| 2 minutes | 429 | **−0.32** | 0.89 | 0.464 | 145 | **−0.59** | 0.76 | 0.29 |

One minute of delay takes the average from +3.57 to −0.20 and the win rate from
0.688 to 0.497 — a coin flip, to three decimal places. Nothing survives. The
entire result was the ability to transact at the price that generated the signal.

A one-minute delay is a trivial handicap for any dislocation that takes minutes
to close, and the strategy holds for thirty. An edge that cannot absorb it was
never reading the market.

**Rejected.**

## The finding worth keeping: persistence is not a staleness control

This is the part that generalises past this strategy.

The persistence filter was supposed to be the defence against nonsynchronous
prints, on the reasoning that a bad print cannot repeat. That reasoning is wrong,
and this run is a clean counterexample. A print is not stale at random — an
illiquid strike lags *consistently*, so its butterfly stays dislocated minute
after minute while never being available at the lagging price. Such a series
sails through a persistence filter of any length. Tightening from one minute to
five only cut the apparent edge from 4.53 to 2.73, because the filter was
selecting for chronic staleness rather than against it.

The entry delay works because it attacks the mechanism directly instead of a
proxy for it. It does not ask whether the signal is stable; it refuses to let the
simulation trade at the print that produced the signal, which is the only thing a
real trader also cannot do.

That the two controls disagree so completely — 3.74 profit factor versus 0.93 on
identical trades — means **any result in this repository validated by persistence
or timing robustness alone should be treated as unproven until it has been re-run
under a true entry delay.**

That includes the one surviving line. The
[surface butterfly diagnostic](SURFACE_PARITY_DIAGNOSTIC.md) has an
`entry_shift_1min` variant which held up (+$3.83 train, +$4.13 validation against
+$4.10 / +$2.92 control), and it is easy to read that as the same control run
already. It is not. `entry_shift_1min` sets `entry_time = 11:01`, which moves the
signal *and* the fill together — it establishes that the edge is not an artifact
of the 11:00 clock specifically, a different and weaker claim. The signal is still
computed from, and transacted at, the same minute's prints. **No line in this
repository has yet been graded under a fill that happens strictly after the
minute the signal was read.** Adding that variant to
`scripts/surface_butterfly_placebo.py` is the highest-value next test in the
project, and this screen is the reason to expect it to matter.

A test now pins the reporting side: a residual with 0.1 lag-1 autocorrelation is
reported as negligible rather than as "consistent with a dislocation that
closes," which is the generous wording that let the first version of this screen
report `passes: true`.

## What this does not establish

- **Not that the smile has no cross-sectional structure.** It establishes that
  the structure visible in 1-minute trade bars cannot be transacted against, and
  says nothing about what synchronized quotes would show. Only the
  [parity recorder](PARITY_RECORDER.md) can settle that.
- **Not that a faster reaction would rescue it.** Halving the delay is not
  available in this data — one minute is the bar resolution. But the direction is
  unambiguous: the edge is monotone decreasing in delay, is already negative at
  the smallest measurable delay, and the residual's own half-life is 0.34
  minutes, so a sub-minute reaction is racing a signal that decays inside the
  time it takes to be observed.
- **Not a cost problem.** At the measured fill cost the zero-delay version earns
  fourteen times its round-trip cost. Making fills free would not change the
  delay result at all, since the delayed version loses on gross P&L before cost.
