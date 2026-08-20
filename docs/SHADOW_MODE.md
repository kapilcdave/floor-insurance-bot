# Shadow mode

Shadow mode follows the complete live strategy lifecycle using observed market
data but never calls Alpaca's order-submission endpoint. It is intended for
forward testing with zero trading capital.

## Configuration

```text
ALPACA_PAPER=true
DRY_RUN=false
SHADOW_MODE=true

UNDERLYING=IWM
SIGNAL_SYMBOL=IWM
TREND_MODE=crossover
STRIKE_SELECTION=atm
SPREAD_WIDTH=1
STOP_DEBIT_MULTIPLE=1.5
MAX_TOTAL_LOSS_DOLLARS=100
TAKE_PROFIT_FRACTION=none

# Actual OPRA top-of-book data requires the corresponding Alpaca entitlement.
OPTIONS_FEED=opra
STOCK_FEED=sip

# A hypothetical portfolio used only for sizing.
SHADOW_EQUITY=100
RISK_BUDGET_DOLLARS=100
MAX_CONTRACTS=1
SHADOW_FEES_PER_SPREAD=0
SHADOW_MIN_CREDIT=0.01
SHADOW_LOG_PATH=state/shadow_events.jsonl
MAX_QUOTE_AGE_SECONDS=90
```

The absolute risk budget is still capped by `SHADOW_EQUITY` and
`MAX_TOTAL_LOSS_DOLLARS`. No fractional contract or simulated leverage is
created. A one-contract $1 spread can consume nearly all of a hypothetical
$100 balance. Shadow equity never changes an Alpaca balance.

`SHADOW_FEES_PER_SPREAD` is a configurable round-trip estimate. Keep it at zero
only if you deliberately want gross P&L.

`SHADOW_MIN_CREDIT` is separate from the live `MIN_CREDIT` guard. The default
allows shadow mode to exercise the lifecycle on a one-cent executable credit,
while real dry-run or order modes continue to require `MIN_CREDIT`. A zero or
negative executable credit is always rejected. The 90-second quote-age setting
matches the slower free indicative feed; use a tighter value such as 30 seconds
with a real-time OPRA entitlement.

## Run

```bash
set -a; source .env; set +a
floor-insurance doctor
floor-insurance run
```

Each successful entry is treated as immediately filled at the conservative
executable credit: short bid minus long ask. Every 15-second observation records
the underlying, both legs' bid/ask, the executable closing debit, spread-debit
stop, and optional profit target. Virtual exits use short ask minus long bid.

Watch raw events:

```bash
tail -f state/shadow_events.jsonl
```

Show accumulated completed-trade results without needing credentials:

```bash
floor-insurance shadow-report
```

The journal is append-only JSON Lines with permissions `0600`. It contains
`shadow_entry`, `shadow_observation`, `shadow_exit`, `shadow_skip`, and
`shadow_error` records. The daily state file remains separate and crash-safe.

## What this proves—and what it does not

With `OPTIONS_FEED=opra`, shadow mode observes real consolidated best bid/offer
quotes. It tests strike selection, timing, quote freshness, risk sizing, trigger
behavior, and conservative mark-to-market P&L. With `indicative`, it tests only
the software mechanics against Alpaca's modified indicative prices.

Shadow mode does not measure exchange routing, queue position, market impact,
partial fills, price improvement, or latency slippage. Its modeled fill is a
conservative quote-based assumption, not an execution guarantee.
