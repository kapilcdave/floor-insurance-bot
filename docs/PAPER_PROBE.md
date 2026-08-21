# SPY paper limit probe

This mode measures one narrow question: when Alpaca's free indicative feed shows
a qualifying SPY 0DTE put credit spread, does an unchanged atomic limit order
receive a simulated paper fill? It does **not** establish that selling the
spread has positive expectancy, and a paper fill does not establish that a live
order would fill.

## Locked experiment

At 10:00 ET, once per session, the bot:

1. reads the latest SPY trade and today's put contracts;
2. builds every exact $1-wide put spread from spot through $10 OTM;
3. discards stale quotes and any candidate whose displayed bid/ask width on
   either leg exceeds $0.10;
4. scans from farthest OTM inward for the first candidate with at least $0.30
   conservative executable credit (`short bid - long ask`);
5. submits one atomic Alpaca paper multi-leg order at exactly $0.30 credit;
6. cancels after 60 seconds if it has not filled, without repricing or chasing;
7. after a fill, submits an atomic market exit if the executable close debit
   reaches two times the actual filled credit, or at 15:00 ET.

The directional moving-average gate is deliberately bypassed. This is an
execution probe, not a directional strategy. One contract and one submission
per day are hard configuration requirements. With a $0.30 fill, a $1-wide
spread's expiration-defined maximum loss is $70 before fees and exceptional
assignment or operational complications.

Use these settings with **paper credentials only**:

```text
ALPACA_PAPER=true
LIVE_TRADING_CONFIRMED=false
PAPER_PROBE_MODE=true
DRY_RUN=false
SHADOW_MODE=false

UNDERLYING=SPY
SIGNAL_SYMBOL=SPY
STRIKE_SELECTION=credit_target
SPREAD_WIDTH=1
MIN_CREDIT=0.30
PROBE_MAX_OTM_DOLLARS=10
MAX_LEG_QUOTE_WIDTH=0.10

RISK_BUDGET_DOLLARS=100
MAX_TOTAL_LOSS_DOLLARS=100
MAX_CONTRACTS=1
MAX_DAILY_ENTRIES=1
TAKE_PROFIT_FRACTION=none
STOP_DEBIT_MULTIPLE=2

ENTRY_TIME_ET=10:00
ENTRY_CUTOFF_TIME_ET=10:05
ENTRY_FILL_TIMEOUT_SECONDS=60
HARD_CLOSE_TIME_ET=15:00
POLL_SECONDS_OPEN=15

STATE_PATH=state/spy_credit_probe_daily.json
PAPER_PROBE_LOG_PATH=state/spy_credit_probe_events.jsonl
```

`PAPER_PROBE_MODE` refuses live endpoints, XSP/IWM/QQQ, shadow or dry-run mode,
spreads wider than $1, more than one contract or daily entry, risk caps above
$100, and an enabled take-profit rule. The `credit_target` selector itself is
also blocked in live mode.

## Run and inspect

```bash
set -a; source .env; set +a
.venv/bin/floor-insurance doctor
.venv/bin/floor-insurance run

tail -f state/spy_credit_probe_events.jsonl
.venv/bin/floor-insurance probe-report
```

The append-only ledger distinguishes `probe_submitted`,
`probe_cancel_requested`, `probe_unfilled`, `probe_filled`, and
`probe_exit_filled`. A fill records Alpaca's actual simulated fill credit and
time-to-fill; the exit record includes gross spread P&L. Skips and API failures
are recorded separately.

## What the free feed can and cannot tell us

Alpaca Basic provides indicative options data rather than OPRA. Alpaca staff
have also explained that the paper simulator can evaluate a limit against
actual market data that differs from the indicative quote exposed to a Basic
subscriber. That makes the accept/cancel result useful as a broker-mechanics
probe, but it is not a reconstruction of the live order book.

- [Alpaca market-data plan documentation](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api)
- [Alpaca discussion of indicative quotes and paper limit fills](https://forum.alpaca.markets/t/which-logs-can-i-look-at-to-figure-out-why-my-limit-orders-arent-filling-paper-trading/18379)

WebSockets would reduce quote and order-status polling delay, but they would not
turn indicative data into OPRA or make simulated fills live fills. For this
once-daily 60-second experiment, 15-second order polling is adequate and easier
to audit on a 1 GB VPS. Revisit streaming only if the experiment produces a
reason to test faster management.

## Forward-test rule

Run the exact configuration for at least 20 eligible market sessions without
changing the target, quote-width cap, scan range, or timeout. Then report:

- sessions with a qualifying displayed spread;
- submitted, filled, and canceled counts;
- time-to-fill and improvement over the $0.30 limit;
- stop and hard-close counts;
- gross P&L, worst trade, and maximum drawdown.

Do not call this an edge based only on fill rate or win rate. The economic test
is net expectancy after realistic fees, live slippage, and tail losses. An
honest historical execution backtest requires contemporaneous OPRA bid/ask
data; stock candles or end-of-day option marks cannot supply that evidence.
