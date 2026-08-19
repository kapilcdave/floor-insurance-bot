# Put credit spread structure

The floor-insurance credit spread is the strategy the bot actually submits
orders for, and until now it had never been measured. This document records
what the market pays for the spread the bot sells, what win rate that payment
requires, and what the structure earns on the capital it ties up.

The sealed July 20 through August 18, 2026 holdout was not fetched. All numbers
below come from 615 training and validation sessions, 2024-02-01 to 2026-07-17.

## Method

At 09:45 ET each session the harness sells the put spread the bot would select:
a short strike a fixed dollar buffer below spot, and a long strike one width
lower. It then manages the position exactly as the engine does.

- The emergency stop is evaluated against each minute bar's **low**, not its
  close, because a 15-second poll loop would see an intrabar breach.
- Any position still open at 15:00 ET is closed, paying the exit cost. The bot
  closes rather than letting a spread expire worthless, deliberately, to avoid
  pin and assignment risk. That costs roughly one exit spread per trade and the
  model charges it.
- Entry requires a credit of at least 5% of the width, so sessions with no
  premium are skipped rather than sold for nothing.
- Prices are one-minute option **trade** bars, not bid/ask. As in the
  directional research, these are research marks. Costs are therefore applied
  as an explicit parameter and every structure is reported twice.

Thirty-six structures were declared in code before evaluation: buffers of $5,
$10 and $15, widths of $1, $3 and $5, stops $1 and $3 above the short strike,
and either a 50%-of-credit profit target or holding to the close.

## Three structural facts

**The credit is 7% to 9% of the width, whatever the width.** At a $5 buffer the
median credit was $0.09 on a $1 spread, $0.26 on a $3 spread and $0.40 on a $5
spread. Widening the spread scales the reward and the risk together and buys no
better ratio.

**The configured default never trades.** At the shipped `BUFFER_DOLLARS=15` the
strategy met its own minimum credit on 7 of 615 sessions, an entry rate of 1.5%.
That matches the live shadow journal, where 24 of 31 skips read
`executable credit $0.00 is below MIN_CREDIT $0.05`. Strikes $15 below spot have
no bid worth collecting.

**Capital required rises with width while the credit ratio does not.** The risk
rule sizes from the maximum loss, which is nearly the full width, so a wider
spread demands a bigger account for the same one contract.

## Results

Gross is zero-cost, the structural upper bound. Net applies $0.02 per side of
slippage and $0.10 of fees per spread. Training split only; validation is in the
JSON report.

| Structure | Entry rate | Median credit | Credit/width | Trades | Win rate | Avg win | Avg loss | Gross avg | Net avg |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `buf5_w1_stop1_tp0.5` | 58.4% | $0.09 | 9.0% | 269 | 84.0% | $5.15 | $21.28 | $0.92 | -$2.07 |
| `buf5_w1_stop1_hold` | 58.4% | $0.09 | 9.0% | 269 | 71.4% | $8.99 | $20.92 | $0.58 | -$2.64 |
| `buf5_w1_stop3_tp0.5` | 58.4% | $0.09 | 9.0% | 269 | 74.7% | $4.99 | $9.85 | $1.23 | -$2.58 |
| `buf5_w1_stop3_hold` | 58.4% | $0.09 | 9.0% | 269 | 51.7% | $8.91 | $8.43 | $0.62 | -$3.24 |
| `buf5_w3_stop1_tp0.5` | 41.2% | $0.26 | 8.7% | 190 | 82.1% | $15.04 | $50.59 | $3.29 | -$2.22 |
| `buf5_w3_stop1_hold` | 41.2% | $0.26 | 8.7% | 190 | 71.0% | $25.82 | $47.55 | $4.58 | $0.14 |
| `buf5_w3_stop3_tp0.5` | 41.2% | $0.26 | 8.7% | 190 | 66.8% | $14.68 | $23.37 | $2.06 | -$3.09 |
| `buf5_w3_stop3_hold` | 41.2% | $0.26 | 8.7% | 190 | 49.5% | $25.80 | $20.27 | $2.63 | -$1.71 |
| `buf5_w5_stop1_tp0.5` | 30.8% | $0.40 | 8.0% | 142 | 80.3% | $24.25 | $70.89 | $5.49 | -$0.09 |
| `buf5_w5_stop1_hold` | 30.8% | $0.40 | 8.0% | 142 | 67.6% | $41.67 | $64.57 | $7.25 | $3.48 |
| `buf5_w5_stop3_tp0.5` | 30.8% | $0.40 | 8.0% | 142 | 62.7% | $23.94 | $32.28 | $2.96 | -$1.97 |
| `buf5_w5_stop3_hold` | 30.8% | $0.40 | 8.0% | 142 | 47.9% | $40.62 | $28.76 | $4.46 | $0.07 |
| `buf10_w1_stop1_tp0.5` | 11.1% | $0.07 | 7.0% | 51 | 94.1% | $4.44 | $27.33 | $2.57 | $0.73 |
| `buf10_w1_stop1_hold` | 11.1% | $0.07 | 7.0% | 51 | 88.2% | $7.84 | $22.50 | $4.27 | $1.11 |
| `buf10_w1_stop3_tp0.5` | 11.1% | $0.07 | 7.0% | 51 | 92.2% | $4.49 | $18.75 | $2.67 | -$1.10 |
| `buf10_w1_stop3_hold` | 11.1% | $0.07 | 7.0% | 51 | 78.4% | $7.92 | $16.45 | $2.67 | -$1.20 |
| `buf10_w3_stop1_tp0.5` | 7.2% | $0.20 | 6.7% | 33 | 93.9% | $13.58 | $60.00 | $9.12 | $11.18 |
| `buf10_w3_stop1_hold` | 7.2% | $0.20 | 6.7% | 33 | 87.9% | $24.28 | $61.00 | $13.94 | $12.54 |
| `buf10_w3_stop3_tp0.5` | 7.2% | $0.20 | 6.7% | 33 | 90.9% | $13.63 | $44.00 | $8.39 | $7.34 |
| `buf10_w3_stop3_hold` | 7.2% | $0.20 | 6.7% | 33 | 75.8% | $24.44 | $38.50 | $9.18 | $5.50 |
| `buf10_w5_stop1_tp0.5` | 5.2% | $0.33 | 6.6% | 24 | 95.8% | $23.61 | $96.00 | $18.62 | $10.69 |
| `buf10_w5_stop1_hold` | 5.2% | $0.33 | 6.6% | 24 | 83.3% | $41.55 | $92.00 | $19.29 | $18.79 |
| `buf10_w5_stop3_tp0.5` | 5.2% | $0.33 | 6.6% | 24 | 87.5% | $24.33 | $58.00 | $14.04 | $6.79 |
| `buf10_w5_stop3_hold` | 5.2% | $0.33 | 6.6% | 24 | 66.7% | $43.31 | $54.38 | $10.75 | $6.43 |
| `buf15_w1_stop1_tp0.5` | 1.5% | $0.11 | 11.0% | 7 | 100.0% | $6.57 | - | $6.57 | $0.10 |
| `buf15_w1_stop1_hold` | 1.5% | $0.11 | 11.0% | 7 | 71.4% | $12.60 | $30.50 | $0.29 | -$5.50 |
| `buf15_w1_stop3_tp0.5` | 1.5% | $0.11 | 11.0% | 7 | 100.0% | $6.57 | - | $6.57 | $1.50 |
| `buf15_w1_stop3_hold` | 1.5% | $0.11 | 11.0% | 7 | 71.4% | $12.60 | $23.50 | $2.29 | -$2.70 |
| `buf15_w3_stop1_tp0.5` | 1.7% | $0.33 | 11.0% | 8 | 87.5% | $16.71 | $82.00 | $4.38 | $3.23 |
| `buf15_w3_stop1_hold` | 1.7% | $0.33 | 11.0% | 8 | 75.0% | $33.00 | $81.00 | $4.50 | -$2.43 |
| `buf15_w3_stop3_tp0.5` | 1.7% | $0.33 | 11.0% | 8 | 87.5% | $16.71 | $61.00 | $7.00 | $6.73 |
| `buf15_w3_stop3_hold` | 1.7% | $0.33 | 11.0% | 8 | 75.0% | $33.00 | $65.00 | $8.50 | $2.90 |
| `buf15_w5_stop1_tp0.5` | 1.5% | $0.48 | 9.6% | 7 | 85.7% | $29.17 | $126.00 | $7.00 | $5.04 |
| `buf15_w5_stop1_hold` | 1.5% | $0.48 | 9.6% | 7 | 57.1% | $62.50 | $112.67 | -$12.57 | -$16.67 |
| `buf15_w5_stop3_tp0.5` | 1.5% | $0.48 | 9.6% | 7 | 85.7% | $29.17 | $88.00 | $12.43 | $10.47 |
| `buf15_w5_stop3_hold` | 1.5% | $0.48 | 9.6% | 7 | 57.1% | $62.50 | $71.33 | $5.14 | $1.04 |

Costs decide almost everything. Twelve of the twelve $5-buffer structures are
profitable gross; nine of them are losing once two cents per side is charged.
Two cents on a nine-cent credit is not a harsh assumption, it is roughly the
quoted spread on an option that cheap.

The `buf10` rows with large net averages trade 24 to 51 times in 461 sessions.
They only find premium on the rare high-volatility days, and they fail the
declared 100-trade minimum. Their apparent edge is a handful of observations.

Taking profit at half the credit loses to holding once costs are charged. Paying
an exit spread to capture four and a half cents does not pay for itself.

## The three survivors and what they cost to run

Viability was declared as positive average P&L per contract on both splits, at
least 100 training trades, and survival under costs. Three structures qualify.

| Structure | Trades/yr | Expectancy | Worst loss | Min equity at 1% risk | Return on that capital |
|---|---:|---:|---:|---:|---:|
| `buf5_w3_stop1_hold` | ~104 | $0.16 | -$78.10 | $27,400 | 0.06%/yr |
| `buf5_w5_stop1_hold` | ~82 | $4.36 | -$110.10 | $46,000 | 0.78%/yr |
| `buf5_w5_stop3_hold` | ~82 | $1.49 | -$70.10 | $46,000 | 0.27%/yr |

At the configuration's maximum 5% risk fraction the capital requirement falls by
a factor of five, and the best structure returns about **3.9% per year** on
$9,200 while a single maximum loss is 5.4% of the account.

Two of the three are indistinguishable from zero: $0.16 and $1.49 per contract.
The one with real magnitude, `buf5_w5_stop1_hold`, is also the best of 36
structures measured on the same sessions, so part of that $4.36 is selection.

## Verdict

The strategy is not viable as designed. Best case, after choosing the winning
structure with hindsight, it returns a few percent a year while risking about 5%
of the account on any single 0DTE session, trading 82 times a year, and needing
either a $46,000 account or the risk cap turned to its maximum. Treasury bills
pay more with none of the tail.

The structural reason is simple and does not depend on the fill model: the
market pays 7% to 9% of the width for this spread. To break even the position
must win roughly nine times out of ten, and the observed win rates at a $5
buffer are 48% to 84% depending on the stop. Moving strikes further out raises
the win rate but collapses the credit to nothing, which is exactly what the
shipped $15 buffer does.

Nothing here is fixable by tuning these three parameters. A viable version would
have to sell meaningfully closer to the money, which raises the credit ratio and
converts the strategy into a different one with a much higher loss frequency, and
that hypothesis has not been tested.

## The silent no-op

`Config.minimum_viable_equity()` now reports the smallest balance that can fund
one contract, and `floor-insurance doctor` fails when the account cannot. With
the shipped defaults of `SPREAD_WIDTH=1`, `MIN_CREDIT=0.05` and
`RISK_FRACTION=0.01`, one contract risks $95 and the floor is **$9,500**. On the
$5,000 balance the documentation suggests, every tick skipped and nothing said
so.

## Reproduce

```bash
floor-credit-structure \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20

floor-credit-structure \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20 \
  --slippage-per-side 0.02 \
  --fees-per-spread 0.10
```

The report lists any pre-existing option cache file inside the holdout; that
list is empty.
