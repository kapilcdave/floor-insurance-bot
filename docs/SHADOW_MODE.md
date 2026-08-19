# Shadow mode

Shadow mode follows the complete live strategy lifecycle using observed market
data but never calls Alpaca's order-submission endpoint. It is intended for
forward testing with zero trading capital.

## Configuration

```text
ALPACA_PAPER=true
DRY_RUN=false
SHADOW_MODE=true

# Actual OPRA top-of-book data requires the corresponding Alpaca entitlement.
OPTIONS_FEED=opra
STOCK_FEED=sip

# A hypothetical portfolio used only for sizing.
SHADOW_EQUITY=10000
SHADOW_FEES_PER_SPREAD=0
SHADOW_LOG_PATH=state/shadow_events.jsonl
```

`SHADOW_EQUITY=10000` normally permits one $1-wide contract under the 1% rule
even when credit is small. To test the intended $5,000 deployment exactly, set
it to `5000`; expect the strategy to skip whenever maximum spread loss exceeds
$50. Shadow equity never changes an Alpaca balance.

`SHADOW_FEES_PER_SPREAD` is a configurable round-trip estimate. Keep it at zero
only if you deliberately want gross P&L.

## Run

```bash
set -a; source .env; set +a
floor-insurance doctor
floor-insurance run
```

Each successful entry is treated as immediately filled at the conservative
executable credit: short bid minus long ask. Every 15-second observation records
SPY, both legs' bid/ask, the executable closing debit, stop level, and profit
target. Virtual exits use short ask minus long bid.

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
