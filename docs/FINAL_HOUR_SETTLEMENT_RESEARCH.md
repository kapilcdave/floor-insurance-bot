# SPY 0DTE final-hour settlement research

Status: **protocol locked before any strategy P&L has been computed.** No P&L,
gross or net, has been read for any structure below. The sealed `2026-05-26`
boundary is never opened, and those sessions are not in the cache — they were
never downloaded.

Every hypothesis in this project so far has done two things: selected a trade by
comparing option prints, and closed the trade at an option mark. Both have now
been shown to be the failure. The [smile residual
screen](SMILE_RESIDUAL_RESEARCH.md) found one butterfly in nine priced negative —
a flat no-arbitrage violation that cannot exist in simultaneous quotes — and the
[surface butterfly](SURFACE_BUTTERFLY_RESEARCH.md) lost its entire edge to a
one-minute fill delay. Meanwhile the [spread estimator](SPREAD_ESTIMATOR.md)
measured the fill cost at `$0.006` per contract unit and removed cost as an
explanation. The binding constraint is the signal, and specifically the practice
of reading a signal off prints that cannot be transacted.

This experiment removes both mechanisms rather than trying to filter them.

## Why this is not one of the nineteen closed lines

Three structural firsts, each aimed at a specific documented failure:

1. **There is no selection rule.** Entry is unconditional on every eligible
   session. A stale print cannot be cherry-picked by a rule that does not
   choose. This is the direct answer to the finding that persistence filters
   select *for* chronic staleness rather than against it.
2. **The exit is settlement, not a mark.** The 0DTE payoff at 16:00 is a
   deterministic function of the settlement price, so no option print is read at
   exit. That eliminates half of every prior line's print exposure and halves
   the friction: four contract units are transacted, not eight.
3. **The entry is priced at the one-minute VWAP, not the opening print.** The
   opening print is the exact quantity that killed the last two lines. The cache
   carries `vw` on all four legs of all 552 eligible sessions and it has never
   been used.

The rejected [iron fly](IRON_FLY_RESEARCH.md) entered at 11:00, selected on a
straddle-richness gate, and hard-closed at 15:00 — its own specification states
that it never assumes expiration settlement or holds through the final hour.
This tests the window and the exit mechanism it excluded.

## Locked construction

- Underlying: SPY, 0DTE, one contract, no compounding.
- Entry: 15:00 America/New_York. Exactly one observation per session; windows
  are disjoint and the sample carries no overlap.
- Center strike `K`: nearest whole dollar to the 15:00 SPY bar open, exact half
  dollars rounded upward. Derived from the underlying only.
- Entry price of each leg: that leg's 15:00 one-minute VWAP. All legs of a
  structure must have a 15:00 bar; otherwise the session is skipped and the skip
  is counted.
- Settlement price `S_T`: close of the 15:59 SPY bar. Payoff is computed
  analytically from `S_T`; no option price is read after entry.
- Risk gate: one-contract maximum loss, `(width - credit) x 100` plus `$0.20`
  fees, must not exceed `$100`. A structure failing this on a session skips that
  session.
- No direction, trend, volatility, calendar, weekday, event, or post-result
  filter is permitted anywhere in this experiment.

### The frozen structure grid

Six structures, enumerated in full before any P&L is computed. Nothing may be
added later.

| ID | Structure | Short strikes | Long strikes | Strikes needed |
|---|---|---|---|---:|
| S1 | Short iron fly, $1 wings | `K` call, `K` put | `K+1`, `K-1` | ±1 |
| S2 | Short iron fly, $2 wings | `K` call, `K` put | `K+2`, `K-2` | ±2 |
| S3 | Short iron condor, $1 wings | `K+1`, `K-1` | `K+2`, `K-2` | ±2 |
| S4 | Short iron condor, $1 wings, wider shorts | `K+2`, `K-2` | `K+3`, `K-3` | ±3 |
| S5 | Long iron fly, $1 wings | inverse of S1 | inverse of S1 | ±1 |
| S6 | Long iron fly, $2 wings | inverse of S2 | inverse of S2 | ±2 |

S5 and S6 are the sign inversions of S1 and S2. **The inversion placebo is built
into the preregistered grid rather than run afterwards**, so a losing short
structure cannot be flipped into a winner by post-hoc reasoning: both sides were
declared together and both pay the multiplicity penalty below.

## Cost model

Measured, not assumed. `$0.006` per contract unit per share from the
[spread estimator](SPREAD_ESTIMATOR.md), charged on four legs at entry only,
plus `$0.20` of fees per structure:

    4 x $0.006 x 100 + $0.20 = $2.60 per structure, one way.

There is no exit charge because there is no exit transaction. Prior lines paid
this twice.

## Preregistered acceptance rule

> **Stage 0, upper bound.** For each of the six structures, compute the
> per-trade average with zero friction on the training split only. A structure
> advances only if that average is at least `$7.80`, three times the one-way
> cost. If no structure clears it, the line closes and nothing further is built.
> This gate exists because the [ATM trend research](ATM_TREND_RESEARCH.md) was
> explained entirely by a zero-cost upper bound smaller than its own costs, and
> that was discovered only after a full simulator was written.
>
> **Stage 1, single candidate.** The advancing structure with the highest
> training zero-friction average is graded at measured cost. Because six
> structures were examined, it passes only if *every* condition holds with no
> parameter changed:
>
> - at least 150 training and 40 validation trades;
> - positive average net P&L and profit factor at least 1.25 on both splits at
>   measured cost;
> - positive average net P&L on both splits at **double** measured cost;
> - positive average net P&L on both splits when the entry fills at the **15:01
>   VWAP** instead of 15:00 — the strict entry delay, a fill in a minute strictly
>   after the one the structure was chosen from;
> - the sign of the average is unchanged on both splits when `S_T` is perturbed
>   by `+$0.05` and by `-$0.05` — the pin-sensitivity control;
> - the average materially exceeds a **randomized-center baseline**: the same
>   structure centered on a strike drawn deterministically from that session's
>   cached strike set rather than on the 15:00 ATM strike.
>
> The sealed holdout is never read. A moving-block bootstrap is diagnostic only
> and cannot rescue a failed condition.

Note what the entry-delay gate means *here*. With unconditional entry there is
no selection to bias, so the delay should be close to neutral. If it is not
neutral, the result is an artifact of the 15:00 print and the line closes — the
control is doing the same job as before, but a near-null result is now the
expected outcome rather than the hoped-for one.

## Data and chronology

- 578 cached option session files in `state/intraday-surface-butterfly-cache`,
  twelve strikes of both types, ending `2026-05-22`. Zero sealed sessions
  present.
- Underlying: `spy-2024-02-01-2026-08-19-iex.json` in the same cache.
- Coverage inspected before locking this protocol, bar existence and strike
  containment only, no prices and no P&L: 565 sessions have both a 15:00 and a
  15:59 stock bar; of those, **552 have a complete four-leg 15:00 snapshot with
  VWAP on every leg** for the ±1 structures; 9 sessions have the ATM strike
  outside the cached range and 4 are missing a leg.
- Split the 552 chronologically 75/25, giving roughly 414 training and 138
  validation observations. Both Stage 0 and Stage 1 read training only until
  Stage 1's validation gate.
- A new strategy-specific sealed holdout is the sessions from `2026-05-26`
  through `2026-08-19`. It is not downloaded, which is a stronger guarantee than
  declining to read it.

## Assignment risk and the promotion path

Passing authorizes **no live SPY trading**, and this is a risk limit rather than
a caution. SPY options are American-style and physically settled. Holding a
short structure into 16:00 exposes a $5,000 account to assignment on the short
legs with exercise decisions running past the close, which can deliver roughly
$60,000 of stock notional overnight before the long legs offset it. The research
payoff below is the *economic* equivalent of cash settlement, not the
operational one.

The authorized promotion is therefore a forward paper mechanics probe on **XSP**,
which is European-style and cash-settled, and which this repository already
restricts to paper mode (`UNDERLYING=XSP` is blocked whenever
`ALPACA_PAPER=false`). A live SPY version would require a separate pre-close
flatten variant that reintroduces an exit mark and must pass every gate above on
its own. It is not preregistered here.

## What this cannot establish

- **Not that fills are achievable.** A one-minute VWAP is still trade-derived.
  It is materially harder for a single stale print to move than an opening
  print is, but only the [parity recorder](PARITY_RECORDER.md) can measure an
  executable price.
- **Not an exact settlement.** The stock bars are IEX, not consolidated, so the
  15:59 close differs from the OCC closing price used for expiration. The
  payoff is kinked at the strikes, so a few cents matters — that is what the
  pin-sensitivity gate is for, and a failure there means the design needs a
  consolidated close before it means anything about the market.
- **Not a general claim about the final hour.** One entry time, one underlying,
  one holding period.

## Research basis

The economic claim is that the final hour of a 0DTE contract carries a variance
and jump premium that settlement does not collect back: see Bakshi and Kapadia
on delta-hedged gains and the volatility risk premium, Bollerslev, Tauchen and
Zhou on the variance risk premium, Boyer and Vorkink on lottery-like demand in
options, and the 0DTE retail-flow literature on concentrated late-session
demand for cheap short-dated options. Citation identifiers were not verified in
the session that wrote this document and should be checked before this section
is read as a literature review; no gate above depends on any of them.

## Stage 0 result

Two structures cleared the `$7.80` zero-friction gate. Training only, boundary
`2025-10-23`, 421 training sessions, no friction charged anywhere.

| ID | Structure | Trades | Avg gross | Win rate | Avg credit | Avg max loss | Gate |
|---|---|---:|---:|---:|---:|---:|---|
| S1 | Short iron fly, $1 wings | 414 | +$5.14 | 0.452 | $0.71 | $29.26 | fails |
| S2 | Short iron fly, $2 wings | 182 | **+$13.40** | 0.566 | $1.26 | $73.78 | **advances** |
| S3 | Short iron condor, shorts ±$1 | 404 | **+$8.07** | 0.733 | $0.31 | $69.47 | **advances** |
| S4 | Short iron condor, shorts ±$2 | 377 | +$5.81 | 0.898 | $0.13 | $86.78 | fails |
| S5 | Long iron fly, $1 wings | 411 | -$5.00 | 0.552 | -$0.71 | $70.74 | fails |
| S6 | Long iron fly, $2 wings | 220 | -$13.48 | 0.355 | -$0.81 | $81.01 | fails |

All four short structures are positive and both long inversions are the mirror
image, so the sign is consistent across the grid rather than resting on one
structure. Under the locked selection rule the Stage 1 candidate is **S2**.

## The entry delay does not touch it

Run early, on training only, because a control can only ever kill a result — it
cannot manufacture a pass, so running it before it is due costs nothing. The
center strike is chosen from the 15:00 underlying bar and the legs are paid for
at the **15:01** VWAP, a minute that cannot be the one that chose the structure.

| ID | Fill at 15:00 | Fill at 15:01 | Change |
|---|---:|---:|---:|
| S1 | +$5.14 | +$5.04 | -$0.10 |
| S2 | +$13.40 | +$14.72 | +$1.32 |
| S3 | +$8.07 | +$8.12 | +$0.05 |
| S4 | +$5.81 | +$5.76 | -$0.05 |

**This is the first line in the project the entry delay has not destroyed.** The
[smile residual screen](SMILE_RESIDUAL_RESEARCH.md) went from +$3.57 to -$0.20 on
the same control and the [surface butterfly](SURFACE_BUTTERFLY_RESEARCH.md) from
+$4.10 to -$3.79. Both had a selection rule reading option prints; this one has
none, which is exactly the reason it was built this way. The result is evidence
that the design premise holds, not evidence that the trade works.

Note also what the delay control is worth here compared with those two lines.
There it was the discriminating test. Here, with no selection on prints, it can
only detect a one-minute drift in the price level, so passing it is a weaker
statement than failing it would have been.

## Amendment recorded after Stage 0: the risk gate is a selection rule

The protocol above claims that entry is unconditional and that no rule selects on
option prices. **For S2 that claim is wrong, and the error is mine.** The `$100`
maximum-loss ceiling on a $2-wide fly requires `(2 - credit) x 100 + 0.20 <= 100`,
so a credit of at least `$1.00`. S2 therefore trades only on sessions where the
fly is already rich, which is 182 of 421 sessions, and its average credit of
`$1.26` confirms it. The qualifying set also moves when the fill minute moves —
182 sessions at 15:00 against 187 at 15:01 — which is the signature of an entry
condition read off prices.

So S2's `+$13.40` is not an unconditional premium. It is a premium conditional on
richness, selected by the risk gate, using the prints this project has learned to
distrust. The gate binds hardly at all on S3 (404 of 421 sessions, average credit
`$0.31`), which is the clean unconditional structure of the two.

This amendment does not relax a gate or add a structure. It adds a second
candidate: **Stage 1 grades S3 alongside S2**, with S3 designated the primary
because its entry is genuinely unconditional. Both were in the declared grid and
both pay the same multiplicity penalty, so grading two is more conservative than
grading the one the ranking rule happened to favour. Future protocols in this
project must state whether a risk gate is price-dependent before it is locked.

## What Stage 0 does not establish

- **Not a passing result.** No friction of any kind was charged. S3 clears the
  gate by 3.5%, and `$2.60` of one-way cost against `+$8.07` leaves a margin that
  Stage 1's double-cost gate will test directly.
- **Not immunity to the settlement reference.** Every number above uses the IEX
  15:59 close. The payoffs are kinked at the strikes and the pin-sensitivity gate
  has not been run.
- **Not a real premium rather than an entry-price bias.** A systematic bias in
  using a VWAP as a selling price would produce this same uniform positive sign
  across all four short structures. Delay-neutrality argues against that bias
  being a one-minute artifact; it does not rule out a persistent one, and only
  the [parity recorder](PARITY_RECORDER.md) can.

## Reproduce

```bash
floor-final-hour-research --stage 0 \
  --cache-dir state/intraday-surface-butterfly-cache \
  --report-out state/final-hour-report.json

# The strict entry delay: choose from 15:00, pay at 15:01.
floor-final-hour-research --stage 0 --fill-time 15:01 \
  --report-out state/final-hour-report-delay.json
```
