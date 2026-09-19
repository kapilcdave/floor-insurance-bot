# Wheel tenor measurement

Measured 2026-09-16 to answer whether shortening the wheel's tenor to weekly
expirations raises income. It does not, on a low-priced underlying. The result
is recorded here because the naive argument for weeklies is correct as far as it
goes, and the reason it fails is not obvious from the premium quote.

Method: the exact `wheel.py` rule set replayed on real Alpaca historical option
daily closes, January 2024 through September 2026, one contract, hold to
expiration, no rolling or buy-to-close. Delta comes from inverting
Black-Scholes on the observed close.

## Net return by tenor

Net dollars per contract as a percentage of average collateral, annualized:

| underlying | weekly (~5 DTE) | bi-weekly | 3-week | monthly (~34 DTE) |
| ---------- | --------------: | --------: | -----: | ----------------: |
| SOFI       |             6.1 |       9.9 |   17.7 |              20.3 |
| NVDA       |            27.1 |      30.1 |   36.8 |              32.6 |
| AAPL       |            25.1 |      23.6 |   33.0 |              20.2 |

Three-week (17-25 DTE) was the best tenor in all three names. Weekly was the
worst in all three.

## The premium gain is real; it is spent on downtime

Weeklies do collect more premium per unit of time exposed. On SOFI:

| tenor   | premium per day in position | uptime | premium per calendar day |
| ------- | --------------------------: | -----: | -----------------------: |
| weekly  |                       $3.02 |    38% |                    $1.16 |
| 3-week  |                       $1.48 |    69% |                    $1.02 |
| monthly |                       $1.06 |    82% |                    $0.87 |

The 2.85x per-day-held advantage matches the 1/sqrt(T) prediction of 2.55x.
It does not reach the account because uptime falls from 82% to 38%: a 5-DTE
25-delta put on a $17 stock is worth about $0.16, which fails the $0.10 premium
floor most weeks. SOFI sat idle 278 days of 694.

## Friction, not tenor, is the mechanism

Cost of selling one cent below the printed trade, with contract selection held
fixed so the haircut cannot relocate the trade:

| run           | one cent as a share of net |
| ------------- | -------------------------: |
| SOFI weekly   |                        21% |
| SOFI monthly  |                         3% |
| NVDA weekly   |                         1% |
| AAPL weekly   |                         1% |

You pay the tick twice as often on credits 2.25x smaller. On SOFI weeklies a
one-cent tick is about 6% of the credit; on a $180 underlying it is about 1%.
This is why weeklies work on NVDA and AAPL and fail on SOFI. **The binding
constraint is the underlying's share price, not the tenor.**

## Relaxing the premium floor makes it worse

The obvious cure for 38% uptime is to lower `WHEEL_MIN_PREMIUM`. Tested:

| floor | cycles | idle | gross | net  | net %/yr |
| ----- | -----: | ---: | ----: | ---: | -------: |
| $0.10 |     49 |  180 |  $806 | $231 |      6.1 |
| $0.05 |     62 |  148 |  $801 | $180 |      4.8 |
| $0.02 |     88 |   87 |  $842 |  -$29 |     -0.9 |

Gross premium is flat across all three. The floor was not costing income; it was
excluding negative-expectancy trades.

## Limits of this measurement

- **Path dependence dominates single runs.** Changing only how long the
  simulation waits after a skipped cycle moved monthly net from $383 to $750.
  Read the levels to about a factor of two.
- Across 8 staggered launch dates, SOFI weekly net took only **two** distinct
  values ($231 or $840). The launches are not independent; effective n is about
  2, so "3-week beat weekly in 6 of 8 launches" is one or two independent
  comparisons, not a significant result.
- The per-day premium, uptime, and per-cent friction figures are arithmetic on
  the trade log and do not carry that noise.
- Credits are daily close trade prices, not the bid the bot actually sells at.
- Deltas invert Black-Scholes on American options.
- 22 to 88 cycles per configuration. January 2024 to September 2026 was a +131%
  run in SOFI; a covered strategy structurally lags that.
