# SPY surface-butterfly paper probe

Status: **protocol locked before the first forward quote scan**.

This experiment asks whether the local-surface candidate in
[`SURFACE_BUTTERFLY_RESEARCH.md`](SURFACE_BUTTERFLY_RESEARCH.md) survives
current quote sides and Alpaca's atomic paper-order simulator. It cannot prove
that a live order would fill, and it is not authorized to use live money.

## Locked protocol

- Underlying and expiration: SPY, same-session 0DTE.
- Entry observation: once per session from 11:00 through 11:05 ET.
- Centers: the nearest whole-dollar strike to SPY plus offsets -3 through +3.
- At every center, construct matching $1-wide call and put butterflies with
  ratios `+1/-2/+1`.
- Executable buy debit: lower ask minus two times the center bid plus the upper
  ask. Reject a candidate when any leg is missing, crossed, older than the
  configured 90-second ceiling, or wider than $0.10.
- Parity gap: absolute difference between the executable call and put buy
  debits. Require at least $0.08 and buy the cheaper structure.
- Round the displayed debit upward to a one-cent limit. Require a signed limit
  from -$0.10 through +$0.10; a negative price demands a net credit.
- Select the largest gap, then the lowest limit debit, then the center nearest
  SPY. Submit exactly one butterfly as one atomic Alpaca `mleg` paper order.
- Never reprice or chase. Cancel an unfilled entry after 60 seconds and stop for
  the session.
- If filled, hold until 12:00 ET. Record a current executable close mark, then
  submit one atomic market exit. Reconcile every uncertain submission by its
  client order ID.
- Record candidate quotes, submitted limit, broker fill price, time to fill,
  noon markout, exit fill, and gross P&L in an append-only local journal.
- Send Telegram notifications when credentials are configured.
- Run the unchanged protocol for at least 20 eligible sessions. Do not open the
  sealed historical holdout or authorize live money based on fewer sessions.

`scan` is read-only and may be used outside the entry window for plumbing
checks, but its result is labeled ineligible and does not count toward the 20
sessions. `run` and `once` must refuse any non-paper endpoint and require the
explicit `SURFACE_BUTTERFLY_PAPER_PROBE=true` opt-in.

## Why atomic orders

Alpaca defines the parent quantity as the number of strategy units and each
leg's `ratio_qty` relative to that parent. The `1:2:1` ratio is already in the
required simplest form. A single `mleg` avoids deliberately legging into
temporary delta and gamma exposure.

- <https://docs.alpaca.markets/us/docs/options-level-3-trading>
- <https://docs.alpaca.markets/us/reference/postorder>

Paper fills remain simulated and the Basic indicative feed is not a substitute
for live OPRA execution evidence.

## Configure and run

The scanner is read-only and does not require the order opt-in:

```bash
set -a; source .env; set +a
.venv/bin/floor-surface-butterfly-probe doctor
.venv/bin/floor-surface-butterfly-probe scan
```

After inspecting `doctor`, enable only the paper runner:

```text
ALPACA_PAPER=true
LIVE_TRADING_CONFIRMED=false
ALPACA_TRADING_URL=https://paper-api.alpaca.markets
SURFACE_BUTTERFLY_PAPER_PROBE=true

SURFACE_PROBE_STATE_PATH=state/surface_butterfly_probe_state.json
SURFACE_PROBE_LOG_PATH=state/surface_butterfly_probe_events.jsonl
SURFACE_PROBE_POLL_SECONDS=5
```

Then start the persistent process before 11:00 ET:

```bash
set -a; source .env; set +a
.venv/bin/floor-surface-butterfly-probe run
```

Inspection commands never submit orders:

```bash
.venv/bin/floor-surface-butterfly-probe state
.venv/bin/floor-surface-butterfly-probe report
tail -f state/surface_butterfly_probe_events.jsonl
```

The process resets only after a completed prior session. If a prior-day entry
or exit remains pending/open, it stops and requires manual reconciliation
instead of assuming the paper position disappeared.

## Initial plumbing check

At 14:29 ET on August 21, 2026, the read-only scanner connected to an active,
unblocked Alpaca Level 3 paper account and evaluated five centers with complete,
fresh, tight six-leg quote sets. It selected the 762/763/764 put butterfly at a
$0.06 executable debit; the matching call butterfly cost $0.22, producing a
$0.16 gap. The scan submitted no order because it was outside 11:00–11:05 ET,
and it does not count toward the 20-session forward test.

This plumbing observation demonstrates current contract discovery, quote-side
math, and candidate ranking. It is not fill evidence or a profitable trade.
