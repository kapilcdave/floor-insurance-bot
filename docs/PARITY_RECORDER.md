# Executable-quote parity recorder

Status: **protocol locked before the first eligible session; zero orders; no
holdout access**.

Every hypothesis in this project has been decided by applying a modeled fill
cost to one-minute option *trade* prints. The two that survived their screens
survive only while adverse fill stays under roughly two to three cents per
contract unit, and no measurement of achievable fill has ever been taken. This
recorder takes it.

`floor-parity-recorder` submits nothing. The module contains no
order-construction path, does not import a submit helper, and a test asserts
that the strings `submit_spread`, `submit_multileg`, `cancel_order`, and
`/v2/orders` never appear in its source. It carries no market risk and cannot
be promoted into a trading configuration by changing an environment variable.

## Why these three structures

The recorder measures only structures whose payoff is bounded or deterministic,
because those are the ones where a positive number at displayed prices means
something unambiguous.

**Paired butterfly.** A call butterfly and a put butterfly on the same three
strikes have identical expiration payoffs — by put/call parity their difference
is exactly zero, with the strike terms cancelling. Buying the cheap one and
selling the expensive one is therefore payoff-neutral: it needs no convergence,
can be held to expiration, and the entry gap is the entire P&L. This is the
structure the [parity diagnostic](SURFACE_PARITY_DIAGNOSTIC.md) pointed at after
inverting the locked trade flipped its sign. `executable_edge` is that gap after
crossing all eight contract units.

**Vertical bounds.** A debit vertical must price inside `[0, width]`. Being paid
to own one, or selling one above its width, is riskless. Two legs instead of
six, so a third of the crossing cost.

**Boxes.** A box settles at exactly its width whatever the underlying does.
Paying below width or collecting above it is riskless.

All three come from one contiguous nine-strike ladder and therefore one quote
snapshot per observation.

## Why forward recording is the only option

Alpaca exposes **no historical option quote endpoint at any subscription tier**.
Probed directly on 2026-08-24 with paper credentials:

| Endpoint | Result |
|---|---|
| `/v1beta1/options/quotes` | `404 Not Found` on both `indicative` and `opra` |
| `/v1beta1/options/quotes/latest` | `200`, live only |
| `/v1beta1/options/snapshots` | `200`, live only |
| `/v1beta1/options/trades` | `200`, full history |
| `/v1beta1/options/bars` | `200`, full history |

Historical option data is trades and bars only. The one-minute trade prints every
screen in this project has used are therefore not a convenient approximation of
executable pricing — they are the *only* historical option data Alpaca sells, at
any price. Buying the OPRA entitlement does not unlock historical bid/ask, so no
strategy in this repository can ever be retroactively validated against
executable prices. There is no shortcut and no way to buy one.

That is what makes this recorder necessary rather than merely useful. Twenty
sessions of forward observation, roughly four calendar weeks, is the shortest
path to the measurement that decides the surface family.

## Locked protocol

- SPY same-session 0DTE options, `$1` wings.
- Observe once inside the first 60 seconds after 10:00, 11:00, 12:00, 13:00, and
  14:00 ET — the same slots as the intraday surface forward runner, so the two
  data sets are directly comparable.
- Ladder: the nearest whole strike to spot, offsets -3 through +3 as butterfly
  centers, plus one wing beyond each end. Nine strikes, both types, 18
  contracts.
- Record every leg's bid, ask, quote age, and a usability verdict. A stale,
  crossed, or too-wide leg is marked unusable and kept in the record; it is
  never silently dropped.
- Compute, for each structure, the cost to buy crossing every leg and the
  proceeds from selling crossing every leg. `executable_edge` is the best of the
  two riskless directions. Positive means displayed quotes permitted a riskless
  trade.
- Also record `mid_gap` for each butterfly center: the parity gap measured at
  the quote midpoint. Comparing its distribution against the $0.18–$0.26
  print-based gap in the historical result is a direct measurement of how much
  of that gap is a trade-print artifact.
- A failed or missed slot is journaled as missed and never reconstructed from
  later data.
- Append-only JSONL at mode `0600`. Structural parameters are code constants,
  not environment variables, because a forward measurement whose universe can
  drift between sessions is not a measurement.

## Decision rule, fixed in advance

Run unchanged for at least 20 sessions on the **OPRA** feed, then decide:

- **The surface family closes** if the median executable edge on usable
  butterfly pairs is at or below -$0.05 and riskless observations are
  approximately zero. That would establish that the historical print-based gaps
  are not available at displayed prices, and it retires the surface butterfly,
  the intraday variant, and the paired trade together.
- **A paper order probe is authorized** only if the median executable edge is
  positive at a frequency that matters. Nothing short of that justifies sending
  an order, and even then a paper fill is simulated and proves nothing about
  live fills.
- Either way the sealed historical holdout stays closed. This measurement is
  about execution, not about the signal.

The free `indicative` feed is not the consolidated book. `report` labels every
aggregate with the feed that produced it and refuses to describe indicative
observations as executable, so an accidental run on free data cannot be mistaken
for the real measurement.

## First smoke observation

A read-only `scan` on 2026-08-24 at 03:44 ET verified the plumbing end to end.
With the staleness guard at its normal setting the observation correctly skipped
with `underlying trade is stale` at a spot age of 214,441 seconds. With the
guard relaxed purely to exercise the ladder path, it read 18 legs, built seven
butterfly centers, 16 verticals, and eight boxes from one snapshot.

Those are Friday-close indicative quotes, not live OPRA quotes at a locked slot,
and one observation is not a data set. Nothing below is a result. It is,
however, the first look this project has ever had at the relevant numbers:

| Center | Mid parity gap | Executable edge |
|---:|---:|---:|
| 763 | $0.12 | -$0.18 |
| 764 | $0.12 | -$0.11 |
| 765 | $0.01 | -$0.11 |
| 766 | $0.06 | -$0.05 |
| 767 | $0.04 | -$0.13 |
| 768 | $0.03 | -$0.19 |
| 769 | $0.07 | -$0.22 |

Every box and every vertical was negative as well. Two things are worth noting
and neither is yet evidence. The mid parity gap averaged about $0.06, against
$0.18 to $0.26 measured from trade prints on the same structure — and five of
the seven centers fall below the strategy's own $0.08 entry gate. After crossing
the spread, every center is negative. That is the shape the print-artifact
hypothesis predicts, and the locked run on live OPRA data will settle it.

## Run

```bash
set -a; source .env; set +a

# Read-only single observation, prints and journals nothing.
.venv/bin/floor-parity-recorder scan

# Persistent recorder; records each locked slot once per session.
OPTIONS_FEED=opra .venv/bin/floor-parity-recorder record

# One tick, for a scheduler or systemd timer.
OPTIONS_FEED=opra .venv/bin/floor-parity-recorder record-once

.venv/bin/floor-parity-recorder report
```

`OPTIONS_FEED=opra` requires the Alpaca options data entitlement. Without it the
recorder still runs and still journals, but the report will state that the
aggregates are not evidence about executable pricing.
