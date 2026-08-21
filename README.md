# Floor Insurance Bot

A small, auditable 0DTE credit-spread research bot for Alpaca. It includes an
IWM zero-order shadow experiment and a strictly paper-only SPY limit probe that
tests unchanged atomic order fills against Alpaca's simulator. SPY, QQQ, IWM,
and paper-only XSP remain supported by the broader research engine. It is
designed for a 1 GB RAM / 30 GB Linux VPS and includes Telegram notifications,
persistent daily state, paper-trading defaults, zero-order shadow execution,
circuit breakers, and a systemd service.

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
- Options are not fractional. A one-contract, $1-wide XSP spread entered for a
  $0.05 credit has a $95 maximum loss. On a $100 account that is 95% of the
  account, not conservative position sizing.
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
- An ATM spread cannot use the legacy `short strike + $3.00` stop: it would
  trigger immediately. The active stop watches the executable spread debit and
  exits when it reaches a configured multiple of filled entry credit.
- **The far-OTM credit originally assumed does not exist.** Measured over 615
  sessions, a put spread $15 below SPY paid a median credit of $0.00 and met the
  $0.05 minimum on 7 of them. The legacy buffered configuration was a no-op,
  and the structures that do trade return a few percent a year at best. See
  [the credit structure measurement](docs/CREDIT_STRUCTURE.md) before funding
  anything.

## ATM 20-day-trend verdict

The requested ATM/20-SMA hypothesis was tested on SPY, QQQ, and IWM using a
12-variant grid committed before fetching results. No configuration passed the
predeclared, cost-adjusted train/validation rule, so the live ATM strategy is
research-blocked and the final July 20 through August 18, 2026 holdout remains
sealed.

Every daily `close > SMA20` variant lost after modeled costs. QQQ failed every
variant. IWM crossover entries without a profit target were positive on both
splits, but there were only 23 training and 11 validation trades. The local
configuration therefore observes the more conservative 1.5x-stop IWM version
in shadow mode; it is not an approved trading strategy. See
[the ATM trend research ledger](docs/ATM_TREND_RESEARCH.md).

```bash
floor-atm-trend-research \
  --symbol IWM \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20
```

## Legacy buffered-spread viability

The credit strategy was finally measured rather than assumed. Across 36
declared structures and 615 training and validation sessions, the market pays
7% to 9% of the spread width, which requires roughly a nine-in-ten win rate to
break even. Three structures survive realistic costs; the best returns about
3.9% per year while risking 5.4% of the account on a single session.

`floor-insurance doctor` now reports `minimum_viable_equity` and fails when the
balance cannot fund one contract. With the shipped defaults that floor is
$9,500, so the $5,000 example account would silently skip every tick.

```bash
floor-credit-structure \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20 \
  --slippage-per-side 0.02 \
  --fees-per-spread 0.10
```

Read [docs/CREDIT_STRUCTURE.md](docs/CREDIT_STRUCTURE.md) for the full table and
the reasoning. The conclusion is that this strategy is not viable as designed.

## Implied-move SPY experiment

A separate research-only harness tests six predeclared SPY put-spread variants
whose strikes are one or 1.25 ATM-straddle-implied moves below spot. It charges
adverse fill on every leg, performs an additional cost stress, and keeps the
final chronological holdout sealed. It is not connected to order submission.

The locked run rejected all six. At one implied move, only 15 of 461 training
sessions and six of 154 validation sessions paid the required $0.15 credit, and
every exit style lost on both splits. At 1.25 moves there were two training
trades and zero validation trades. See
[the implied-move research ledger](docs/IMPLIED_MOVE_RESEARCH.md).

## Strategy defaults

At 09:45 America/New_York on a regular trading day, the bot first fetches
adjusted daily bars ending strictly before that session. `TREND_MODE=above`
allows every previous close above its contemporaneous SMA;
`TREND_MODE=crossover` requires a fresh move from at-or-below to above. An
ineligible or incomplete signal ends the session without an order.

The default ATM selector sells the nearest put at or below spot and buys the
put exactly `SPREAD_WIDTH` lower. The exact credit and actual strike width
determine risk. `MAX_TOTAL_LOSS_DOLLARS=100`, `MAX_CONTRACTS=1`, and the normal
percentage or absolute risk budget all apply; the strictest limit wins. For
SPY, QQQ, and IWM the bot reads the latest stock trade. For XSP it derives a
reference from same-expiration call/put midpoint parity because this paper
account lacks Alpaca's separate index-value data grant. Missing or stale quotes
cause a safe skip. It then manages the filled spread until one of these events:

- executable spread debit reaches `STOP_DEBIT_MULTIPLE` times filled credit:
  close and count a loss;
- when enabled, spread debit reaches `TAKE_PROFIT_FRACTION` of filled credit:
  close and finish;
- three emergency-stop losses: finish for the day;
- 15:00 ET: cancel working orders, flatten the bot-owned spread, and finish.

The bot submits every spread as one atomic Alpaca multi-leg order and uses a
`floor-insurance-*` client-order ID for crash reconciliation. It never calls an
account-wide flatten endpoint.

## SPY paper limit probe

The broker-mechanics experiment scans fresh, tight $1-wide SPY put spreads from
farthest OTM inward, requires a fixed $0.30 displayed executable credit,
submits one atomic paper limit at exactly $0.30, and cancels it after 60 seconds
without repricing. It bypasses the directional filter and writes every broker
outcome to a separate append-only ledger.

This is deliberately not available on Alpaca's live endpoint. Free indicative
quotes are not the live OPRA order book, and Alpaca paper fills are simulated;
the experiment measures paper execution mechanics, not a proven trading edge.
See [the SPY paper limit probe guide](docs/PAPER_PROBE.md) for the locked setup,
safety interlocks, commands, and 20-session evaluation rule.

The separately preregistered `$0.50` version has already failed its historical
rejection screen. It traded only five of 433 training sessions, lost $109.50,
and produced zero trades across 145 validation sessions. Four of the five
training entries hit the spread stop. Its final 60-session holdout remains
sealed because the development result misses every promotion gate. See
[the fixed-credit research ledger](docs/FIFTY_CREDIT_RESEARCH.md).

A direction-neutral variance-premium test also failed. The locked $2-wing SPY
iron fly produced 155 training and 59 validation trades, but averaged -$16.63
and -$13.98 per trade after conservative four-leg costs. The narrow wings buy
back most of the tail premium, leaving too little gross edge to survive
execution. Its final 60 sessions remain sealed, and it is not connected to the
order engine. See [the iron-fly research ledger](docs/IRON_FLY_RESEARCH.md).

The preregistered follow-up allowed $2 through $5 wings but selected the widest
one whose modeled loss stayed below $100. It also failed: -$18.48 per training
trade and -$14.66 per validation trade. Most entries could afford only $2 or $3
wings; the broader structures that retain more tail premium generally violate
the small-account risk cap. See
[the adaptive-width ledger](docs/ADAPTIVE_IRON_FLY_RESEARCH.md).

An opening option-flow signal was balanced across bullish and bearish entries
and reached 122 training and 53 validation trades. Its $1 credit spreads still
lost -$4.03 and -$5.04 per trade after two-leg costs. An optimistic removal of
all modeled friction leaves only a few dollars per trade, suggesting a small
gross signal that the narrow structure cannot monetize rather than deployable
alpha. See [the option-flow ledger](docs/OPTION_FLOW_RESEARCH.md).

Allowing $2 and $3 option-flow spreads did not change a single trade: every
wider candidate exceeded the locked $100 maximum-loss cap, so all 175
development entries remained $1 wide. The cap is doing its job, but it prevents
scaling the possible gross signal. See
[the adaptive option-flow ledger](docs/ADAPTIVE_OPTION_FLOW_RESEARCH.md).

The deployment-compatible flow test waited until 10:15 because free Alpaca
option trades are delayed. The information decayed: training fell to a 49.2%
win rate and -$8.47 per trade; validation averaged -$4.57. Even an optimistic
removal of all modeled friction leaves training slightly negative. See
[the delayed-flow ledger](docs/DELAYED_OPTION_FLOW_RESEARCH.md).

A stock-only constituent lead-lag gate was also attempted before pricing any
options. Requiring exact five-minute endpoint bars for all 12 names yielded
zero training signals because the free IEX feed is too sparse as a complete
cross-section. Its cache stops before the final holdout. See
[the constituent lead ledger](docs/CONSTITUENT_LEAD_RESEARCH.md).

The default `TAKE_PROFIT_FRACTION=none` holds until the spread stop or hard
close. `MAX_DAILY_ENTRIES=1` intentionally disables same-day re-entry
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

XSP is European-style and cash-settled, which avoids early assignment and stock
delivery. Alpaca currently exposes XSP to Trading API retail users only in paper
trading, so the configuration rejects live XSP mode. Paper fills are simulated;
they do not prove that the same order would fill in the live order book.

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
.venv/bin/floor-insurance probe-report
.venv/bin/pytest --cov=floor_insurance
```

## Archived directional research

The repository retains an earlier research-only 0DTE call/put debit-spread
backtester. It tests a no-lookahead opening-range/VWAP signal and accepts only
modeled spreads with at least 2:1 maximum reward/risk. It is intentionally not
wired into the trading bot and is not part of the current ATM strategy.

```bash
floor-directional-backtest \
  --start 2024-02-01 \
  --end 2026-08-18 \
  --oos-start 2026-07-20 \
  --constant-sizing
```

The initial baseline lost $728 on validation after modeled slippage and was
rejected. Its final 22-session OOS period remains sealed. See
[the directional research guide](docs/DIRECTIONAL_RESEARCH.md) for the exact
assumptions, results, and historical-fill limitation.

Fourteen predeclared variants have now failed the development acceptance rule,
including six that split the breakout by the prior close of the Cboe volatility
complex over a window extended back to February 2024. One of them appeared to
pass until a sizing control showed the result came from compounding a different
balance rather than from the filter. Reproduce the fixed comparison with
`floor-directional-experiments` and see the
[experiment ledger](docs/DIRECTIONAL_EXPERIMENTS.md). None of these models is
available to the order-submission state machine.

## Zero-capital shadow mode

Shadow mode runs the complete strategy against each observed underlying and
options quote without submitting an order. It records conservative virtual
entries, every 15-second quote observation, triggers, exits, and modeled P&L in
an append-only JSONL journal.

```text
ALPACA_PAPER=true
DRY_RUN=false
SHADOW_MODE=true
UNDERLYING=IWM
SIGNAL_SYMBOL=IWM
TREND_MODE=crossover
STRIKE_SELECTION=atm
STOP_DEBIT_MULTIPLE=1.5
MAX_TOTAL_LOSS_DOLLARS=100
TAKE_PROFIT_FRACTION=none
SHADOW_EQUITY=100
RISK_BUDGET_DOLLARS=100
MAX_CONTRACTS=1
SHADOW_LOG_PATH=state/iwm_atm_crossover_shadow_events.jsonl
```

Use `OPTIONS_FEED=opra` and `STOCK_FEED=sip` only when the Alpaca credentials
have those data entitlements. The free `indicative`/`iex` combination still
tests software mechanics but is not actual consolidated options pricing.

No fractional option is created: `SHADOW_EQUITY` is only a hypothetical sizing
balance. The absolute budget is capped at the sizing balance, and the bot still
requires the spread's exact maximum loss to fit. With the settings above, one
$1-wide spread can consume almost all $100.

XSP can still be selected for separate paper mechanics, with
`SIGNAL_SYMBOL=SPY`. After enough shadow observations, Alpaca **paper** order
submission can be enabled with `SHADOW_MODE=false` and `DRY_RUN=false`. Keep
`ALPACA_PAPER=true`. That exercises Alpaca's simulated multi-leg fills without
risking cash. There is currently no supported route in this bot to obtain live
retail XSP fills through Alpaca.

See [the shadow-mode guide](docs/SHADOW_MODE.md) for setup, event format,
reporting, and the execution limitations this cannot measure.

## Telegram

Create a bot with BotFather, send that bot a message, determine the chat ID, and
set `TELEGRAM_BOT_TOKEN` plus `TELEGRAM_CHAT_ID`. Telegram failure never blocks
order management; the error is sent to the local log.

## Backtesting without fooling ourselves

The backtester requires contemporaneous bid/ask quotes for both option legs and
the underlying reference. Stock-only or option-close-only data cannot honestly
test the entry credit, 50% take profit, or exit slippage.

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

The prepared-quote harness uses conservative executable prices: short bid
minus long ask on entry, then short ask minus long bid on exit. It currently
tests one entry per day. Synthetic tests verify the math and event ordering;
they are not evidence of profitability. The bundled ATM research uses
historical one-minute option
trade bars plus explicit costs, not a licensed historical NBBO feed. Its
rejection is documented rather than presented as executable performance.

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

Live mode is deliberately awkward. For supported stock ETF underlyings, the
base interlock requires:

```text
ALPACA_PAPER=false
LIVE_TRADING_CONFIRMED=true
STOCK_FEED=sip
OPTIONS_FEED=opra
```

ATM mode has an additional `ATM_LIVE_CONFIRMED=true` interlock. No current ATM
candidate passed research, so leave it false. The setting prevents an older
live configuration from silently running the newly added strategy; it is not a
recommendation to override the failed research gate.

Even then, paper-soak the exact deployed commit first. Supervise the bot and
keep broker access available for manual flattening. Assignment, pin risk,
market halts, rejected exits, bad data, and exchange/broker outages cannot be
eliminated in software.

`UNDERLYING=XSP` is additionally blocked whenever `ALPACA_PAPER=false` because
Alpaca retail live index-options trading is not currently available.

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
