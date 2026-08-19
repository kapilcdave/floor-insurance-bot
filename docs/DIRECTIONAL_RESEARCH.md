# Directional 0DTE debit-spread research

This module tests a separate strategy from the floor-insurance credit bot. It
does not submit orders and is not wired into the live state machine.

## Baseline hypothesis

At 09:45 ET, the research harness examines only SPY bars timestamped before the
entry minute. It buys a call spread when the 09:44 close is above both the
first five-minute opening range and the 15-minute VWAP. It buys a put spread
when the inverse conditions hold. Otherwise it skips the day.

It considers integer-strike, $3-wide 0DTE spreads near SPY and accepts only a
spread whose modeled maximum reward is at least twice its debit. The default
$5,000 portfolio risks at most 2% per day, including modeled fees. A $1 debit
therefore sits on the boundary of what one contract can safely fit.

The profit order targets two times the initial risk. Any position still open is
marked closed at 15:00 ET, or one hour before an inferred early close.

## Data and fill limitation

Alpaca exposes historical one-minute option trade bars to this account, but its
historical option quote endpoint is not available. The harness synchronizes
option-bar prices and subtracts configurable slippage on both entry and exit.
Those are research marks, not executable bid/ask fills.

Use this backtest to reject weak signals. Do not use it as proof that a quoted
P&L could have been filled. Live OPRA bid/ask shadow observations and Alpaca
paper MLEG orders remain required before any live consideration.

## Run without opening the holdout

```bash
set -a
source .env
set +a

floor-directional-backtest \
  --start 2025-08-18 \
  --end 2026-08-18 \
  --oos-start 2026-07-20 \
  --trades-output state/directional-trades.csv
```

Raw bars are cached one session at a time under `state/backtest-cache/`. The
cache is ignored by Git and was about 45 MB for the initial year, remaining
well within the target VPS storage and memory limits.

The explicit OOS period is not processed unless `--reveal-oos` is supplied.
The report also lists any pre-existing option cache files inside the holdout;
that list must be empty for a clean final test.

## Initial baseline result

The first run on August 19, 2026 used a $5,000 starting balance, 2% risk, $3
width, 2:1 minimum reward/risk, five cents of modeled slippage per side, and
ten cents of modeled round-trip fees per spread.

| Split | Sessions | Trades | Win rate | P&L | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Training | 172 | 101 | 42.57% | $949.90 | 1.2213 | -$803.60 |
| Validation | 58 | 30 | 26.67% | -$728.00 | 0.5476 | -$798.00 |

Only two validation trades reached the 2R target. The baseline therefore
failed validation and must not be promoted to forward or paper trading. The 22
sessions from July 20 through August 18 remain sealed. Do not reveal them to
rescue this parameter set.

Any next hypothesis should be written down before it is tested, compared on
both training and validation after the same slippage model, and rejected if it
depends on repeatedly tuning the held-out period.

Eight fixed price, volume, VWAP, timing, and overnight-gap hypotheses were
subsequently evaluated. None passed the development acceptance rule. See the
[experiment ledger](DIRECTIONAL_EXPERIMENTS.md); the OOS period remains sealed.
