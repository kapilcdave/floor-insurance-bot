# Fixed $0.50 SPY credit research

Status: **rejected on training and validation; final holdout remains sealed**.

This experiment asks whether selling a much closer SPY 0DTE put spread for a
fixed $0.50 credit improves the economics enough to justify a one-contract live
pilot. It is a different structure from the rejected far-OTM rule: on a
$1-wide spread, a $0.50 fill offers $50 maximum gross profit against $50
expiration-defined maximum loss.

This file fixes the hypothesis, execution model, chronology, and pass/fail rule
before the simulator is run. Results will be added whether the hypothesis
passes or fails.

## Locked strategy

- Underlying: SPY.
- Expiration: same session (0DTE).
- Entry: 10:00 America/New_York.
- Structure: one $1-wide bull-put spread.
- Candidate shorts: every whole-dollar strike from $10 below the 10:00 SPY
  open through the highest whole-dollar strike at or below it. The hedge is
  exactly $1 lower.
- Selection: scan from farthest OTM inward and select the first candidate whose
  modeled executable credit reaches the fixed target.
- Submitted and filled credit: exactly $0.50. Improvement above the limit is
  not credited to the simulation.
- Take profit: none.
- Emergency stop: close when modeled executable spread debit reaches $0.75.
- Hard close: 15:00 ET; never model expiration settlement.
- Size: exactly one contract, with no compounding.

Although the user described this as non-directional insurance selling, a
bull-put spread is still directionally bullish/neutral. No trend, futures,
volatility, or day-of-week filter is permitted in this experiment.

## Historical execution model

Alpaca historical option bars are one-minute trade aggregates, not
contemporaneous NBBO quotes. The result can reject the hypothesis but cannot
prove that a live $0.50 multi-leg limit would fill.

The base model applies $0.02 adverse fill per leg on both entry qualification
and exit, plus $0.10 fees per completed spread:

- a candidate must show at least `$0.50 + 2 × $0.02 = $0.54` raw credit at the
  exact entry minute;
- a qualifying entry is booked at exactly $0.50 credit;
- every exit debit adds `2 × $0.02 = $0.04`;
- stop detection uses the adverse intraminute combination of the short leg's
  high and long leg's low;
- a stop exit pays at least $0.75 and may pay as much as the $1 spread width;
- a missing or stale hard-close mark never receives a zero-debit assumption.

The stress model raises adverse fill to $0.03 per leg. It therefore requires a
$0.56 raw entry mark, adds $0.06 to exit debit, and delays a triggered stop by
one synchronized option-bar minute. If no delayed mark exists, it charges the
full spread width.

## Chronology

Data begins on February 1, 2024, the start of Alpaca's historical options
coverage, and ends August 19, 2026. The 638 SPY sessions are divided before
option results are evaluated:

- training: first 75% of the 578 pre-holdout sessions (433 sessions);
- validation: remaining 25% of the pre-holdout sessions (145 sessions);
- strategy-specific final holdout: May 26 through August 19, 2026 (60
  sessions).

The first run may access only training and validation dates. The command must
report the holdout boundary and whether matching cache files already existed,
but must not load or simulate those files. Related SPY experiments have used
overlapping market history, so this is not a virgin dataset; it is a sealed
holdout only for this newly fixed specification.

## Promotion rule

The strategy passes development only if all of the following hold without a
parameter change:

- at least 100 training trades and 30 validation trades;
- positive average P&L per contract on both base splits;
- profit factor at least 1.25 on both base splits;
- maximum drawdown no worse than five $50 risk units (-$250) on either base
  split;
- positive average P&L on both stress splits.

A deterministic 10,000-path, five-trade moving-block bootstrap will also report
one-contract annual P&L and drawdown distributions. It is diagnostic and cannot
override a failed chronological gate.

Passing does not authorize live trading. It authorizes a short paper mechanics
check, implementation of a persistent $100 lifetime-loss breaker, and then a
separately confirmed one-contract pilot in the funded $3,000 account. A failed
development result leaves the live interlock in place and the final holdout
sealed.

## Result

The locked run rejected the strategy before the final holdout. It evaluated
433 training and 145 validation sessions. The May 26 through August 19, 2026
holdout was neither loaded nor simulated, and its cache-file audit was empty.

| Split | Trades | Wins | Stops | Average P&L | Total P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training | 5 | 1 | 4 | -$21.90 | -$109.50 | 0.2954 | -$155.40 |
| Validation | 0 | 0 | 0 | — | $0.00 | — | $0.00 |
| Training stress | 5 | 1 | 4 | -$23.10 | -$115.50 | 0.2754 | -$159.40 |
| Validation stress | 0 | 0 | 0 | — | $0.00 | — | $0.00 |

The base model found a qualifying raw credit on only five training sessions,
an entry rate of 1.2%. It skipped 421 training sessions because no candidate
reached $0.54, four because both exact entry marks were unavailable, and three
because the underlying entry minute was absent. Every validation session
failed the raw-credit threshold.

The five accepted training spreads showed $0.64 average raw credit and placed
the short strike an average $4.40 below SPY. That apparent premium was not a
free improvement in reward/risk: four of the five rich-credit sessions hit the
$0.75 spread-debit stop, including one full $50.10 modeled loss. The premium
appeared when the insured risk was unusually high.

The deterministic bootstrap was correctly suppressed. Repeating five trades
into 10,000 synthetic 252-trade years would manufacture precision rather than
measure uncertainty.

This misses every meaningful promotion gate: sample size, validation trades,
average P&L, profit factor, and cost stress. No parameter was changed, the
holdout remains sealed, and this result does not authorize the proposed live
pilot. The existing `$0.30` paper order process remains an execution-mechanics
probe only.

## Reproduce the rejected run

```bash
floor-fifty-credit-research \
  --start 2024-02-01 \
  --end 2026-08-19 \
  --oos-start 2026-05-26 \
  --cache-dir state/fifty-credit-cache \
  --report-out state/fifty-credit-report.json
```

## Data limitation

Alpaca documents historical options coverage only from February 2024 and says
its free indicative feed contains modified rather than actual OPRA quotes:

- <https://docs.alpaca.markets/us/docs/historical-option-data>
- <https://docs.alpaca.markets/us/docs/about-market-data-api>
