# Surface-butterfly placebo diagnostics

Status: **the surface butterfly is rejected.** The entry-delay variant added
2026-08-25 takes the locked result from +$4.10 to -$3.79 per trade. The earlier
sections below are preserved as written, with the two conclusions they got wrong
marked; the correction is in [After the entry delay](#after-the-entry-delay).

Diagnostic only: changes no parameter and opens no holdout.

The surface butterfly is the only preregistered hypothesis in this project to
pass its development screen, and it passed on one-minute option *trade* bars
whose volume is frequently a single contract. Before spending more effort on the
surface family, this run asks whether the measured edge behaves like genuine
put/call parity convergence or like a stale-print artifact.

`scripts/surface_butterfly_placebo.py` reads only the existing
`state/surface-butterfly-cache`. It never constructs an Alpaca client and never
issues a network request, and it drops every session on or after the
2026-05-26 holdout boundary. The `control` variant reproduces the recorded
result exactly — 291 training trades at +$4.10 with profit factor 4.0101, and
106 validation trades at +$2.92 with profit factor 2.8507 — so the harness is a
faithful replication rather than a reimplementation.

## Variants

| Variant | Change from the locked rule |
|---|---|
| `control` | none |
| `inverted` | buy the **expensive** butterfly instead of the cheap one |
| `placebo_nogap` | require the parity gap to be **below** $0.08, then buy the cheapest fly |
| `liquid_min10` / `liquid_min25` | require every one of the six entry prints to have traded at least 10 / 25 contracts |
| `entry_shift_1min` | read entry marks at 11:01 instead of 11:00 -- signal **and** fill move together |
| `entry_delay_1min` / `entry_delay_2min` | choose the structure from the 11:00 cross-section, pay the 11:01 / 11:02 print |

## Result

| Variant | Train trades / avg / PF | Validation trades / avg / PF |
|---|---:|---:|
| `control` | 291 / +$4.10 / 4.0101 | 106 / +$2.92 / 2.8507 |
| `inverted` | 133 / -$2.54 / 0.2978 | 56 / -$3.90 / 0.0931 |
| `placebo_nogap` | 349 / +$0.88 / 1.4138 | 134 / +$1.20 / 1.6479 |
| `liquid_min10` | 187 / +$3.59 / 3.6693 | 96 / +$3.24 / 3.1980 |
| `liquid_min25` | 126 / +$3.73 / 3.9362 | 78 / +$4.85 / 5.6373 |
| `entry_shift_1min` | 265 / +$3.83 / 3.6712 | 103 / +$4.13 / 3.7987 |
| `entry_delay_1min` | 257 / **-$3.79** / 0.3710 | 102 / **-$3.80** / 0.3626 |
| `entry_delay_2min` | 255 / **-$4.26** / 0.3132 | 103 / **-$4.61** / 0.2617 |

The stale-print hypothesis predicted that the edge would concentrate in
illiquid legs and vanish once liquidity was required. It does not. Average P&L
by the thinnest leg in the structure is flat:

| Minimum leg volume at entry | Train trades / avg | Validation trades / avg |
|---|---:|---:|
| 1 contract | 44 / +$4.82 | 4 / +$0.05 |
| 2 to 9 | 105 / +$3.87 | 15 / +$2.67 |
| 10 to 49 | 102 / +$3.87 | 43 / +$3.10 |
| 50 or more | 40 / +$4.52 | 44 / +$3.10 |

Three findings survive:

**The sign flips when the trade is inverted.** Buying the expensive butterfly
loses -$2.54 and -$3.90 per trade at profit factors of 0.30 and 0.09, winning
only 22% and 14% of the time. A generic "cheap 0DTE butterflies are convex
lottery tickets" explanation predicts that *both* sides make money, because both
are long gamma into a pin. They do not. The cheap side converges up and the
expensive side converges down, which is what parity convergence predicts and
what a long-gamma artifact does not. The inverted sample is smaller because the
expensive fly more often breaches the $0.10 debit cap.

**The gap carries information beyond cheapness.** With no inconsistency required
at all, buying the cheapest available fly still earns +$0.88 and +$1.20 at
profit factors of 1.41 and 1.65. So roughly a dollar of the four is a generic
cheap-convexity drift. The remaining three dollars are attributable to the
parity gap itself.

**~~It is not a single-timestamp artifact.~~** ~~Requiring liquid prints on all six
legs and shifting the entry by a full minute both leave the result intact.~~
**This conclusion was wrong.** See below: `entry_shift_1min` moves the signal and
the fill together and therefore cannot detect a stale print at all.

## After the entry delay

Added 2026-08-25, after the
[smile residual screen](SMILE_RESIDUAL_RESEARCH.md) showed a cross-sectional
signal clearing every persistence and timing check while being made entirely of
stale prints. The same control applied here settles this line.

`entry_delay_1min` chooses the structure exactly as the locked rule does — same
11:00 cross-section, same $0.08 minimum gap, same cheap side, same $0.10 debit cap
— and then pays the 11:01 print instead of the 11:00 print. The fill is
unconditional; screening the delayed price against the debit cap would only fill
when it moved favourably.

| | control | delay 1 min | delay 2 min |
|---|---:|---:|---:|
| train avg P&L | +$4.10 | **-$3.79** | **-$4.26** |
| validation avg P&L | +$2.92 | **-$3.80** | **-$4.61** |
| train profit factor | 4.0101 | 0.3710 | 0.3132 |
| train win rate | 0.543 | 0.249 | 0.243 |
| avg debit at signal | $0.03 | $0.04 | $0.04 |
| avg debit **paid** | $0.03 | **$0.11** | **$0.11** |
| avg fill slippage | $0.00 | **+$0.07** | +$0.07 |
| share paid above the $0.10 cap | 0.000 | **0.436** | 0.423 |
| signals with no delayed fill | 0 | 34 (12%) | 36 (12%) |

**The mechanism is visible in one line.** The rule buys a butterfly whose 11:00
print says $0.03. One minute later the same butterfly prints at $0.11. The
average slippage of $0.07 is essentially the entire $0.08 gap the rule selects on,
which is the definition of the gap being the stale print rather than a real
inconsistency. On 44% of trades the price actually paid breaches the strategy's own
$0.10 debit cap — **the rule qualifies these trades only because the price it
inspects is not the price it would get.**

Note also where `entry_delay_1min` lands: -$3.79, close to `inverted`'s -$2.54.
That is coherent. If the cheap fly is not really cheap, then buying it at its true
price is simply a bad trade, and the two variants are approaching the same truth
from opposite directions.

### Why `entry_shift_1min` missed this

`entry_shift_1min` sets `entry_time = 11:01`, which moves the signal and the fill
together. It re-derives the gap from the 11:01 cross-section and transacts at
11:01 prints. So it tests whether the edge is specific to the 11:00 clock — it is
not — while leaving the signal and the transaction on the same prints, which is
exactly the condition a stale-print artifact needs to survive. Reading it as
evidence against staleness was the error, and it is an easy one: the variant name
and a one-minute shift both sound like the right control.

The liquidity filters missed it for a related reason. They require volume on the
entry prints, but volume does not make a print current. Six legs that each traded
25 contracts can still have last traded at six different moments inside the same
minute, and it is the *dispersion* of those moments, not the thinness of any one
leg, that manufactures the gap.

### The same result at every hour

The [intraday variant](INTRADAY_SURFACE_BUTTERFLY_RESEARCH.md) scans 10:00
through 14:00 and takes the first qualifier, and it passed its own development
screen at +$4.22 / +$3.99. Running this diagnostic against
`state/intraday-surface-butterfly-cache` at each locked slot shows the artifact is
not a property of 11:00.

Average P&L per trade, train / validation:

| slot | control | `entry_shift_1min` | `entry_delay_1min` | slippage | over cap |
|---|---:|---:|---:|---:|---:|
| 10:00 | +$4.32 / +$4.21 | +$4.88 / +$4.96 | **-$4.81 / -$1.93** | +$0.09 | 0.458 |
| 11:00 | +$4.10 / +$2.92 | +$3.38 / +$4.66 | **-$3.79 / -$3.80** | +$0.07 | 0.436 |
| 12:00 | +$4.25 / +$5.71 | +$3.99 / +$2.86 | **-$2.62 / -$2.91** | +$0.07 | 0.392 |
| 13:00 | +$4.45 / +$3.16 | +$3.86 / +$2.27 | **-$3.59 / -$4.40** | +$0.08 | 0.442 |
| 14:00 | +$3.65 / +$4.72 | +$3.39 / +$2.55 | **-$3.32 / -$2.83** | +$0.07 | 0.365 |

Ten out of ten cells flip sign. `entry_shift_1min` holds up in all ten, which is
the cleanest possible demonstration that it and `entry_delay_1min` are different
tests rather than two versions of the same one. Fill slippage is +$0.06 to +$0.09
throughout — always most of the $0.08 gap being selected on — and 37% to 47% of
delayed fills breach the $0.10 debit cap.

The whole surface family is closed.

### The paired six-leg trade dies with it

The section below proposes buying the cheap fly and selling the expensive one:
payoff-neutral by parity, holdable to expiration, so that the entry gap *is* the
P&L. That was the most attractive idea in the surface family, and this result
kills it directly rather than by analogy. Its entire P&L is the gap, and the gap
is gone $0.07 of $0.08 within sixty seconds. There is nothing to capture.

## What this does not establish

Nothing here touches the constraint that actually decides the strategy. Every
number above — the cheap side's gain and the expensive side's loss alike — is
measured against trade prints, not against the bid and ask that an order would
have to cross. The recorded three-cent-per-unit sensitivity already turns
validation negative, and the only live order in this project's history was a
$1-wide SPY spread submitted two cents *through* its displayed executable credit
that went unfilled for 62 seconds in Alpaca's paper simulator
(`state/spy_credit_probe_events.jsonl`, 2026-08-21).

A persistent $0.18 to $0.21 average parity violation in same-strike SPY
butterflies, present across 578 sessions in the most liquid option market in the
world, is not plausibly available to a retail account through a REST API. The
overwhelmingly likely reconciliation is that the gap is real in prints — one
butterfly last printed on the bid and the other on the ask — and absent in
executable quotes. This diagnostic cannot distinguish those two worlds, and no
amount of further backtesting on this data can.

## Consequence for the surface family (superseded)

Because the inverted leg loses roughly what the cheap leg gains, the two sides
are complements, which points at the six-leg paired trade that the original
research declined on complexity grounds. A call butterfly and a put butterfly on
the same three strikes have *identical* expiration payoffs: by put/call parity
the difference of the two structures is exactly zero, with the strike terms
cancelling. Buying the cheap fly and selling the expensive one is therefore
payoff-neutral, needs no convergence, and can be held to expiration rather than
marked out at noon — the entry gap *is* the P&L.

That removes market risk but not execution risk, and the arithmetic is tight.
One pair captures the gap on 100 shares, roughly $21 in training and $18 in
validation, against eight contract units of adverse fill. Break-even is about
$0.026 per unit in training and $0.023 in validation before the six-leg
commission. That is the same two-to-three-cent wall the naked butterfly hit,
reached from the other direction: the paired structure converts a directional
and gamma exposure into a pure bet on fill quality.

The binding unknown for the entire surface family is therefore a single
measurement nobody has taken: **what is the parity gap in executable quotes?**
The next step is a zero-order quote recorder, not another simulator.

**Superseded.** The entry delay answers the question well enough to act on
without waiting for quotes. A gap that has decayed by $0.07 of $0.08 one minute
later cannot be a standing inconsistency in executable prices; it is one
butterfly having last printed on the bid while the other last printed on the ask.
The [parity recorder](PARITY_RECORDER.md) is still worth running — it measures
the spread level directly, and it is already built — but it is no longer the
gate on a decision. The decision is made.

## Reproduce

```bash
.venv/bin/python scripts/surface_butterfly_placebo.py \
  --report-out state/surface-butterfly-placebo.json

# the intraday variant, one slot at a time
for t in 10:00 11:00 12:00 13:00 14:00; do
  .venv/bin/python scripts/surface_butterfly_placebo.py \
    --cache-dir state/intraday-surface-butterfly-cache --entry-time $t
done
```

The `entry_delay_*` numbers above are from
`state/surface-butterfly-placebo-delay.json`, produced by the same command.
