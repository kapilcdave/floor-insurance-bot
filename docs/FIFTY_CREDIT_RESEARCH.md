# Fixed $0.50 SPY credit research

Status: **preregistered; results not evaluated**.

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

## Data limitation

Alpaca documents historical options coverage only from February 2024 and says
its free indicative feed contains modified rather than actual OPRA quotes:

- <https://docs.alpaca.markets/us/docs/historical-option-data>
- <https://docs.alpaca.markets/us/docs/about-market-data-api>
