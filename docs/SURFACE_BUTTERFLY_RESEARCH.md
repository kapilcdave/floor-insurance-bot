# SPY 0DTE local-surface butterfly research

Status: **passed the locked development screen; paper execution probe only;
final holdout remains sealed**.

This experiment targets cross-sectional relative value rather than the level or
direction of volatility. For equally spaced strikes, call and put prices must
be convex in strike. A $1-wide long butterfly is their discrete second
derivative and has a nonnegative expiration payoff. A call butterfly and put
butterfly at the same strikes have the same terminal payoff, so a large price
gap is a local surface inconsistency.

The fully paired trade would buy the cheap butterfly and sell the expensive
one, requiring six option legs and more execution complexity than this small
Alpaca account should accept. This test buys only the cheap three-leg
butterfly, caps its debit, and asks whether the local inconsistency mean reverts
over the following hour. It is relative-value exposure, not risk-free
arbitrage.

All rules below are frozen before strategy-specific option data is fetched.

## Locked signal and trade

- Underlying: SPY.
- Expiration: same session (0DTE).
- Entry: 11:00 America/New_York.
- Exit: 12:00 ET, using an exact synchronized mark or the last synchronized
  mark no more than five minutes old.
- Candidate centers: nearest whole-dollar strike to the 11:00 SPY open, with
  half dollars rounded upward, plus offsets -3 through +3.
- At each center, construct both the call and put $1 butterfly: buy one option
  one dollar below center, sell two at center, and buy one one dollar above.
- Raw butterfly debit: lower-strike opening trade price minus twice the center
  price plus the upper-strike price.
- Parity gap: absolute difference between raw call- and put-butterfly debits at
  the same center.
- Require exact 11:00 bars for all six contracts at a candidate center.
- Require parity gap of at least $0.08.
- Buy the cheaper call or put butterfly. Modeled entry debit is its raw debit
  plus $0.005 adverse fill for each of four contract units, rounded up to
  cents. Negative modeled debit is conservatively floored at zero rather than
  treated as free entry credit.
- Require modeled debit no greater than $0.10. Maximum one-contract loss is the
  debit times 100 plus $0.20 fees and must remain below $100.
- If multiple candidates qualify, choose the largest parity gap, then the
  lowest modeled debit, then the center nearest SPY. No outcome data enters
  selection.
- Exit credit is the synchronized raw butterfly value less $0.005 adverse fill
  per contract unit, bounded between zero and the $1 maximum payoff and rounded
  down to cents.
- Size: one butterfly. No take profit, stop, re-entry, direction, volatility,
  event, weekday, or post-result filter.

The stress model independently charges $0.01 adverse fill per contract unit at
entry and exit. It must satisfy the same $0.08 parity-gap and $0.10 debit gates.

## Chronology and promotion rule

Use February 1, 2024 through August 19, 2026. The final 60 sessions, May 26
through August 19, 2026, are sealed in a new strategy-specific cache. The first
75% of earlier sessions is training and the last 25% validation.

Development passes only if every condition holds without changing a parameter:

- at least 100 training trades and 30 validation trades;
- positive average net P&L and profit factor of at least 1.25 on both base
  splits;
- maximum drawdown no worse than -$500 on either base split;
- positive average P&L on both stress splits;
- at least ten purchased call butterflies and ten purchased put butterflies in
  both training and validation.

A deterministic moving-block bootstrap is diagnostic only. Passing would
authorize a paper multi-leg mechanics probe, not live money or holdout access.

## Research basis and limitations

Convexity in strike is a fundamental static no-arbitrage restriction and
corresponds to a nonnegative butterfly price. Research on observed option
surfaces stresses that bid-ask uncertainty and nonsynchronous observations can
create false apparent violations; recent 0DTE work therefore uses tick-level
NBBO data and explicitly constrained surface fits.

- <https://www.nber.org/papers/w8944>
- <https://doi.org/10.1080/14697688.2010.514005>
- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6457378>
- <https://docs.alpaca.markets/us/docs/historical-option-data>

Alpaca Basic one-minute option trade bars are not synchronized NBBO quotes.
This experiment can reject apparent relative value after conservative costs;
it cannot prove that a detected historical kink was executable.

## Result

This is the first preregistered options hypothesis in the project to pass every
locked development gate. It evaluated 433 training and 145 validation sessions
without reading any of the final 60 option dates from May 26 through August 19,
2026. The strategy-specific holdout cache audit was empty.

| Split | Trades | Calls / puts | Average gap | Average debit | Average P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training | 291 | 118 / 173 | $0.21 | $0.03 | +$4.10 | 4.0101 | -$25.00 |
| Validation | 106 | 48 / 58 | $0.18 | $0.03 | +$2.92 | 2.8507 | -$22.20 |
| Training stress | 275 | 120 / 155 | $0.21 | $0.03 | +$2.77 | 2.5121 | -$35.60 |
| Validation stress | 98 | 45 / 53 | $0.19 | $0.03 | +$1.97 | 2.0387 | -$27.00 |

The base trade risk averaged only $3.46 in training and $3.15 in validation.
Both option types and all sample-size gates were present. The deterministic
moving-block bootstrap estimated positive P&L in all 10,000 paths, with median
252-trade P&L of $946.60. Those figures describe this trade-bar model; they are
not a live forecast.

## Post-result execution sensitivity

Because the signal is derived from nonsynchronous one-minute prints, additional
friction levels were evaluated strictly as diagnostics. They do not alter or
rescue the locked pass.

| Adverse fill per contract unit | Training trades / avg / PF | Validation trades / avg / PF |
|---:|---:|---:|
| $0.02 | 230 / +$0.57 / 1.2628 | 85 / +$0.27 / 1.1339 |
| $0.03 | 172 / -$1.56 / 0.5440 | 74 / -$2.74 / 0.2146 |
| $0.05 | 77 / -$0.91 / 0.7369 | 31 / -$3.49 / 0.0073 |

The apparent edge therefore lives inside roughly two cents of adverse fill per
contract unit. That is too execution-sensitive for a live-money promotion.
The next authorized step is an unchanged atomic paper-order probe using current
quote sides, recording displayed debit, submitted limit, fill state, time to
fill, and one-hour markout. The sealed holdout remains closed until forward
execution evidence supports opening it.

## Reproduce the passing development screen

```bash
floor-surface-butterfly-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/surface-butterfly-cache \
  --report-out state/surface-butterfly-report.json
```
