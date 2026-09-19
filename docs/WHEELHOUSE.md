# Wheelhouse

Wheelhouse automates one conservative version of the options wheel without
pretending that selling premium is automatically profitable. It uses the
broker's live positions as its state, so it does not need a second portfolio
database that can drift away from Alpaca.

## Decision rule

On each tick Wheelhouse:

1. Requires an open market, an unblocked account, and options level 1 or above.
2. Stops if the symbol already has an option position or open option order.
3. Sells a covered call for each complete 100-share lot, up to the configured
   cap, or considers a cash-secured put only when the stock position is zero.
4. Chooses the expiration nearest the target DTE inside the allowed window.
5. Estimates annualized realized volatility from completed daily closes and
   compares it with the selected expiration's nearest-to-spot implied
   volatility.
6. Uses the cautious delta if IV/RV is elevated or a known event falls between
   today and expiration. Otherwise it uses the normal delta.
7. Rejects stale or wide quotes, bids below the premium floor, yields below the
   annualized floor, unaffordable puts, and calls below both spot and stock cost
   basis.
8. Sends a sell limit at the displayed bid. It never sends a naked option or a
   market order.

The default normal and cautious targets are 0.25 and 0.15 absolute delta. A
lower delta is farther out of the money, but it is not safer in every sense and
does not eliminate gap, assignment, or opportunity-cost risk.

## Event handling

Set `WHEEL_EVENT_DATES` to comma-separated ISO dates, for example:

```text
WHEEL_EVENT_DATES=2026-10-29,2027-01-28
```

Use it for confirmed earnings dates, ex-dividend dates, product decisions, or
other binary events you want treated cautiously. Wheelhouse deliberately does
not scrape or guess an earnings calendar. Keep this list current; the IV/RV
test is a second line of defense, not a replacement for event data.

## Configuration

The DTE window accepts a minimum as low as 3, which selects the nearest weekly
Friday. Shortening the tenor is not free premium: see
[the tenor measurement](WHEEL_TENOR.md) before changing it. Below 3 DTE a
25-delta option is mostly gamma and the listed strike grid cannot express the
target delta, so the validator refuses it.

```text
WHEEL_SYMBOL=AAPL
WHEEL_MIN_DTE=28
WHEEL_TARGET_DTE=35
WHEEL_MAX_DTE=45
WHEEL_TARGET_DELTA=0.25
WHEEL_CAUTIOUS_DELTA=0.15
WHEEL_IV_RV_RATIO=1.5
WHEEL_REALIZED_VOL_WINDOW=20
WHEEL_MIN_PREMIUM=0.10
WHEEL_MIN_ANNUALIZED_YIELD=0.08
WHEEL_MAX_QUOTE_WIDTH_FRACTION=0.25
WHEEL_MAX_CONTRACTS=1
WHEEL_POLL_SECONDS=900
```

`WHEEL_MIN_ANNUALIZED_YIELD` is premium divided by secured capital, annualized
without compounding. For puts, secured capital is strike times 100; for calls,
the denominator is spot times 100. It is only a quote filter and is not an
expected-return estimate.

## Running safely

Install the project and start with paper credentials and dry-run mode:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
set -a; source .env; set +a
.venv/bin/wheelhouse doctor
.venv/bin/wheelhouse once
```

`once` is suitable for a supervised daily run or cron. `run` polls every 15
minutes by default. Deterministic daily client-order IDs, current positions,
and open orders prevent a normal retry from stacking duplicate contracts.

Paper mode and `DRY_RUN=true` are defaults. Live mode additionally requires:

```text
ALPACA_PAPER=false
WHEEL_LIVE_CONFIRMED=true
STOCK_FEED=sip
OPTIONS_FEED=opra
```

Alpaca's free indicative options feed is useful for software plumbing, not for
judging executable prices. Assignment can create or remove 100 shares per
contract, and early assignment is possible on American-style options. Monitor
the account around expiration and ex-dividend dates; this bot does not roll,
buy back, exercise, or override broker expiration handling.

The `floor-insurance` executable remains available only for the repository's
legacy 0DTE spread engine and research workflows.
