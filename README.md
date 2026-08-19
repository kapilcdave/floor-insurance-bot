# Floor Insurance Bot

A small, auditable 0DTE SPY bull-put-spread bot for Alpaca. It is designed for
a 1 GB RAM / 30 GB Linux VPS and includes Telegram notifications, persistent
daily state, paper-trading defaults, zero-order shadow execution, circuit
breakers, and a systemd service.

> [!WARNING]
> This is experimental software, not investment advice. 0DTE options can lose
> their full defined risk quickly. Start with Alpaca paper trading, supervise
> it, and verify every assumption against your account permissions and market
> data plan before considering live use.

## Important corrections to the original rulebook

- A $1-wide spread has a **gross** risk of $100, not $50. Exact maximum loss is
  `(spread width - entry credit) * 100 * contracts`. A $5,000 account with a 1%
  risk budget can trade one spread only when its filled credit is at least
  $0.50. This bot never rounds a zero-contract result up to one.
- A $0.50 credit on a put spread $15 below SPY is an example, not a reasonable
  guaranteed fill. Expected $50-$80 daily profits and an 85% win rate are not
  assumed or advertised.
- A defined-risk spread cannot normally lose $300 per contract at expiration;
  its economic maximum loss is bounded by the width less credit. Bad legging,
  failed close orders, pin/assignment risk, fees, and post-expiration handling
  can still create losses or stock exposure. Orders are therefore submitted as
  atomic Alpaca multi-leg orders.
- Polling every five minutes is too slow for the emergency rule. The default is
  15 seconds while a position is open, with a configurable stale-quote guard.
- The default stop is the safer `short strike + $3.00`, as requested in the
  warning section. This is an underlying-price trigger, not a guaranteed exit
  price.

## Strategy defaults

At 09:45 America/New_York on a regular trading day, the bot reads SPY's latest
trade, selects today's put strikes at least $15 below SPY, and constructs a
$1-wide bull put spread. It sizes from equity and the actual proposed credit.
It then manages the filled spread until one of these events:

- spread debit reaches 50% of the filled entry credit: close and finish;
- SPY reaches `short strike + $3`: close, count a loss, and optionally re-enter;
- three emergency-stop losses: finish for the day;
- 15:00 ET: cancel working orders, flatten the bot-owned spread, and finish.

The bot submits every spread as one atomic Alpaca multi-leg order and uses a
`floor-insurance-*` client-order ID for crash reconciliation. It never calls an
account-wide flatten endpoint.

The default `MAX_DAILY_ENTRIES=1` intentionally disables same-day re-entry
after a stop. You can raise it to three to reproduce the draft circuit breaker,
but do that only after separately validating re-entry behavior.

## Requirements

- Python 3.11+
- an Alpaca account with options trading level 3 (spreads)
- paper keys for initial operation
- OPRA options + SIP stock data before any live consideration
- optional Telegram bot token and chat ID

Alpaca's free indicative options feed does not contain actual OPRA quotes. It
is allowed only in paper mode here. Alpaca also does not return Greeks for 0DTE
contracts, so this strategy does not depend on delta.

## Install and paper-test

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
set -a; source .env; set +a

# Verify account permissions and feeds.
.venv/bin/floor-insurance doctor

# Evaluate one tick without submitting an order (DRY_RUN=true).
.venv/bin/floor-insurance once

# Run continuously. Set DRY_RUN=false only with paper credentials first.
.venv/bin/floor-insurance run
```

The app does not automatically load `.env`; systemd loads its environment file,
and an interactive shell must export it as shown above. State is written
atomically with mode `0600`.

Useful commands:

```bash
.venv/bin/floor-insurance state
.venv/bin/floor-insurance shadow-report
.venv/bin/pytest --cov=floor_insurance
```

## Directional debit-spread research

The repository also contains a research-only 0DTE call/put debit-spread
backtester. It tests a no-lookahead opening-range/VWAP signal and accepts only
modeled spreads with at least 2:1 maximum reward/risk. It is intentionally not
wired into the trading bot.

```bash
floor-directional-backtest \
  --start 2025-08-18 \
  --end 2026-08-18 \
  --oos-start 2026-07-20
```

The initial baseline lost $728 on validation after modeled slippage and was
rejected. Its final 22-session OOS period remains sealed. See
[the directional research guide](docs/DIRECTIONAL_RESEARCH.md) for the exact
assumptions, results, and historical-fill limitation.

Eight predeclared follow-up variants also failed the development acceptance
rule. Reproduce the fixed comparison with `floor-directional-experiments` and
see the [experiment ledger](docs/DIRECTIONAL_EXPERIMENTS.md). None of these
models is available to the order-submission state machine.

## Zero-capital shadow mode

Shadow mode runs the complete strategy against each observed SPY and options
quote without submitting an order. It records conservative virtual entries,
every 15-second quote observation, triggers, exits, and modeled P&L in an
append-only JSONL journal.

```text
ALPACA_PAPER=true
DRY_RUN=false
SHADOW_MODE=true
SHADOW_EQUITY=10000
SHADOW_LOG_PATH=state/shadow_events.jsonl
```

Use `OPTIONS_FEED=opra` and `STOCK_FEED=sip` only when the Alpaca credentials
have those data entitlements. The free `indicative`/`iex` combination still
tests software mechanics but is not actual consolidated options pricing.

No fractional option is created: `SHADOW_EQUITY` is only a hypothetical sizing
balance. Set it to `5000` to reproduce the intended account, which will skip
one contract unless its true maximum loss fits inside $50. The default $10,000
balance makes it easier to collect one-contract forward-test observations.

See [the shadow-mode guide](docs/SHADOW_MODE.md) for setup, event format,
reporting, and the execution limitations this cannot measure.

## Telegram

Create a bot with BotFather, send that bot a message, determine the chat ID, and
set `TELEGRAM_BOT_TOKEN` plus `TELEGRAM_CHAT_ID`. Telegram failure never blocks
order management; the error is sent to the local log.

## Backtesting without fooling ourselves

The backtester requires contemporaneous bid/ask quotes for both option legs and
the SPY price. Stock-only or option-close-only data cannot honestly test the
entry credit, 50% take profit, or exit slippage.

Prepared input is a CSV with one row per observation and these columns:

```text
timestamp,underlying,short_strike,long_strike,short_bid,short_ask,long_bid,long_ask
```

Timestamps must include an offset. Each day's file rows must represent the
spread selected from information available at entry—do not select strikes using
the day's eventual low or close. Alpaca's historical options data starts in
February 2024; use OPRA rather than the free indicative feed for research meant
to resemble execution.

```bash
floor-insurance-backtest data/prepared_spreads.csv \
  --starting-equity 5000 \
  --fees-per-spread 0.06
```

The default report reveals only the first 60% training period and next 20%
validation period. Tune on those, write down and freeze the configuration, then
run exactly once with `--reveal-oos` to see the final chronological 20%.

This harness uses conservative executable prices: short bid minus long ask on
entry, then short ask minus long bid on exit. It currently tests one entry per
day. Synthetic tests verify the math and event ordering; they are not evidence
of profitability. No real backtest result is bundled because this repository
does not have access to a licensed historical OPRA dataset.

Do not add a futures/momentum regime switch until it improves validation data
after fees and slippage, and do not repeatedly inspect the held-out segment.
Overnight futures direction alone is not a demonstrated edge.

## Existing VPS deployment (no provisioning)

The repository does not create cloud resources. On an existing Debian/Ubuntu
VPS, place the checkout at `/opt/floor-insurance-bot`, create a non-login
`floorbot` user, install its virtual environment, copy `.env.example` to
`/etc/floor-insurance-bot.env`, and put persistent state under
`/var/lib/floor-insurance-bot`:

```bash
sudo useradd --system --home /opt/floor-insurance-bot --shell /usr/sbin/nologin floorbot
sudo mkdir -p /var/lib/floor-insurance-bot
sudo chown floorbot:floorbot /var/lib/floor-insurance-bot
sudo chmod 700 /var/lib/floor-insurance-bot

sudo cp deploy/floor-insurance.service /etc/systemd/system/
sudo chmod 600 /etc/floor-insurance-bot.env
sudo systemctl daemon-reload
sudo systemctl enable --now floor-insurance
sudo journalctl -u floor-insurance -f
```

Set `STATE_PATH=/var/lib/floor-insurance-bot/daily.json` in the environment
file. The systemd unit caps memory at 256 MB and grants write access only to the
state directory. The runtime itself normally uses far less than the available
1 GB; no pandas, ML framework, database, or charting stack is installed.

## REST polling versus WebSockets

Open positions poll every 15 seconds by default, not every five minutes. REST is
kept as the initial implementation because retry, freshness, and recovery are
easy to audit. Alpaca's options WebSocket uses MessagePack and requires robust
reconnect/resubscribe plus a stale-stream watchdog. Streaming is a sensible
second phase after paper soak testing, with REST retained as the fallback.

## Failure behavior

- GET requests retry transient errors with bounded backoff.
- POST requests are never blindly retried. On an ambiguous response the bot
  reconciles by deterministic client-order ID; if still unknown it alerts and
  refuses to duplicate the order.
- Quotes older than `MAX_QUOTE_AGE_SECONDS` block a decision.
- Working take-profit entries are canceled and replaced by a market multi-leg
  exit at the hard close.
- Early-close sessions move the hard close to one hour before the exchange's
  reported close.
- Daily state resets by New York trading date, not by the VPS's Iowa timezone.

## Live-trading interlock

Live mode is deliberately awkward. All four values are required:

```text
ALPACA_PAPER=false
LIVE_TRADING_CONFIRMED=true
STOCK_FEED=sip
OPTIONS_FEED=opra
```

Even then, paper-soak the exact deployed commit first. Supervise the bot and
keep broker access available for manual flattening. Assignment, pin risk,
market halts, rejected exits, bad data, and exchange/broker outages cannot be
eliminated in software.

## Project layout

```text
src/floor_insurance/  REST client, strategy, state machine, CLI, backtester
tests/                deterministic unit and lifecycle tests
docs/                 shadow execution and research guides
deploy/               hardened systemd unit for an existing VPS
.github/workflows/    Python 3.11-3.13 CI
```

## Status and non-goals

Paper trading remains the default. This project does not promise a return,
provision infrastructure, scrape unlicensed options history, or infer an edge
from an AI-generated win-rate claim.

## License

MIT
