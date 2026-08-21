# SPY 0DTE opening option-flow credit-spread research

Status: **rejected after costs; final holdout remains sealed**.

This experiment tests whether unusually one-sided activity in near-the-money
SPY 0DTE options during the first 30 minutes contains directional information
that can be monetized with a two-leg credit spread. Published research finds
that signed option activity after the opening bell predicts the underlying's
remaining intraday return. Our free Alpaca history does not identify buyer and
seller initiation, so the locked signal below uses a deliberately simple,
auditable bar-direction proxy. That limitation makes the experiment a
rejection screen, not a replication of the paper.

The specification and pass/fail rule are committed before option results are
evaluated.

## Locked signal and trade

- Underlying: SPY; expiration: same session (0DTE).
- Signal window: 09:30 through 09:59 America/New_York.
- Entry: exactly 10:00; hard close: exactly 15:00.
- Signal strikes: the nearest whole-dollar strike to the 10:00 SPY open plus
  one strike above and below it. Calls and puts at all three strikes are used.
- Each one-minute option bar contributes its volume with a sign: call volume is
  positive when that bar closes above its open and negative when it closes
  below; put volume has the opposite sign. Flat bars contribute zero signal
  but still count toward total observed volume.
- Flow score: summed signed volume divided by total call-plus-put volume. It is
  bounded from -1 to +1.
- Data gate: require at least 1,000 contracts of total signal-window volume and
  an absolute flow score of at least 0.20. Positive is bullish; negative is
  bearish. Equality qualifies.
- Bullish trade: sell the whole-dollar put at or immediately below 10:00 SPY
  and buy the put exactly $1 lower.
- Bearish trade: sell the whole-dollar call at or immediately above SPY and buy
  the call exactly $1 higher.
- Entry credit: raw short-minus-long 10:00 trade-bar open less $0.02 adverse
  fill per leg, rounded down to cents.
- Enter only with positive credit below $1 and maximum loss `(1 - credit) ×
  100 + $0.10 fees` no greater than $100.
- Size: one contract. No take profit, stop, re-entry, compounding, trend,
  futures, volatility, event, weekday, or post-result filter.
- Exit debit: synchronized 15:00 close marks plus $0.02 adverse fill per leg.
  A synchronized mark no more than five minutes old is allowed; otherwise the
  full $1 width is charged.

The stress model uses $0.03 adverse fill per leg at entry and exit. It must
independently pass the entry and risk rules.

The bar sign is fixed without examining its results. It is not described as
true signed order flow: a price-up bar can contain both buyer- and seller-
initiated trades, and call/put price changes also reflect SPY movement and IV.

## Chronology and promotion

Data covers February 1, 2024 through August 19, 2026. The first 75% of sessions
before May 26, 2026 are training, the remaining 25% are validation, and the
final 60 sessions from May 26 through August 19 are sealed in a new cache. The
initial run may fetch only training and validation option dates.

Development passes only if all conditions hold unchanged:

- at least 100 training and 30 validation trades;
- positive average P&L and profit factor of at least 1.25 on both base splits;
- maximum drawdown no worse than -$500 on either base split;
- positive average P&L on both stress splits;
- both bullish and bearish trades appear in training and validation.

A bootstrap is diagnostic and cannot override a failed chronological gate.
Passing would authorize only a paper order-mechanics probe, not live money or a
holdout reveal.

## Sources and data limitation

- Opening option activity and intraday return predictability:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3095239>
- Evidence that residual 0DTE alpha can become infeasible under small
  transaction costs:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7149778>
- Alpaca historical option trades are delayed, modified derivatives on the
  free indicative feed rather than actual OPRA data:
  <https://docs.alpaca.markets/us/docs/historical-option-data>

## Result

The locked run rejected the $1 spread after costs. It evaluated 433 training
and 145 validation sessions while leaving May 26 through August 19, 2026
sealed; the strategy-specific holdout cache audit was empty.

| Split | Trades | Bull / bear | Wins | Average P&L | Total P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training | 122 | 61 / 61 | 71 | -$4.03 | -$492.20 | 0.7673 | -$587.50 |
| Validation | 53 | 28 / 25 | 29 | -$5.04 | -$267.30 | 0.7225 | -$267.30 |
| Training stress | 122 | 61 / 61 | 66 | -$7.87 | -$960.20 | 0.5839 | -$1,033.70 |
| Validation stress | 53 | 28 / 25 | 29 | -$8.89 | -$471.30 | 0.5517 | -$471.30 |

The signal cleared both sample-size and two-direction gates, but missed every
economic gate. Its 58.2% training and 54.7% validation win rates were
insufficient for average entry credits of only $0.34 and $0.35.

There is one useful diagnostic, not a promotion: base friction is at most $8.10
per completed trade ($0.02 on two entry and two exit legs plus fees). Adding
all of that back gives an optimistic friction-free upper bound of about +$4.07
per training trade and +$3.06 per validation trade. Bounding at the spread
width means actual removable friction can be smaller, so these are upper
bounds, not a claim of gross profitability. The direction signal may contain a
small amount of information, but a $1 spread cannot monetize it after costs.

The deterministic bootstrap estimated only a 1.84% chance of a positive
252-trade year for the rejected net model. Median annual P&L was -$1,080.20.
The strategy is not promoted to paper orders and its threshold is not tuned.

### Post-result execution sensitivity

The fixed signal was replayed without changing entries across a per-leg
adverse-fill curve. The $0.10 fee assumption remained in every row. This is a
diagnostic of the possible gross effect, not a new acceptance test.

| Adverse fill per leg, each side | Train avg / PF | Validation avg / PF |
|---:|---:|---:|
| $0.000 | +$3.72 / 1.2598 | +$2.81 / 1.1898 |
| $0.005 | +$1.79 / 1.1186 | +$0.82 / 1.0528 |
| $0.010 | -$0.17 / 0.9895 | -$1.16 / 0.9298 |
| $0.015 | -$2.10 / 0.8732 | -$3.12 / 0.8202 |
| $0.020 | -$4.03 / 0.7673 | -$5.04 / 0.7225 |
| $0.030 | -$7.87 / 0.5839 | -$8.89 / 0.5517 |

The sign is consistent before meaningful friction, but even perfect modeled
fills miss the 1.25 validation profit-factor gate. One cent per leg makes both
splits negative. Any forward investigation must therefore measure unchanged
atomic limit fills; assuming midpoint execution would manufacture the desired
result.

## Reproduce the rejected run

```bash
floor-option-flow-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/option-flow-cache \
  --report-out state/option-flow-report.json
```
