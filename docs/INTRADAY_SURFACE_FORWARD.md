# Intraday surface-butterfly paper forward test

Status: **protocol locked before the first eligible forward session**.

This runner tests whether the historical intraday surface result survives
current quote sides and Alpaca's atomic paper simulator. It is paper-only and
does not authorize the sealed holdout or live funds.

## Locked daily policy

- SPY same-session 0DTE options.
- Observe once during the first minute after 10:00, 11:00, 12:00, 13:00, and
  14:00 ET.
- At each observation, apply the existing current-quote gates: seven nearby
  centers, matching $1-wide call and put butterflies, at least $0.08 executable
  price gap, fresh/tight legs, and signed entry limit no greater than $0.10.
- If no candidate qualifies, wait for the next locked hour.
- The first qualifying candidate ends signal discovery for the day whether its
  order fills or not. Never substitute a later signal after an unfilled order.
- Submit one complete `1:2:1` butterfly as one atomic Alpaca paper order. Never
  reprice or chase; cancel after 60 seconds.
- Exit a fill atomically one hour after its actual fill or at 15:00 ET,
  whichever comes first.
- Use separate intraday-forward state and append-only journal files. Label all
  events `cohort=intraday_forward`.
- A missed hourly window is recorded and never reconstructed from later data.
- Prior-day pending/open state requires manual reconciliation.

The first-minute operational window allows a five-second polling process to
observe the intended hour. Its few-second timing difference from a historical
one-minute bar is execution reality, not a tunable signal parameter.

## Forward gate

Run the unchanged rule until it records at least 20 eligible candidates and 15
atomic paper fills. Before opening the sealed historical holdout, require:

- positive net average P&L after recorded fees;
- profit factor at least 1.25;
- average adverse fill no greater than $0.02 per contract unit;
- no stranded position, duplicate submission, or unresolved reconciliation;
  and
- complete quote, order, markout, and exit records for every submission.

Paper fills are simulated and cannot establish live fill quality. Passing this
gate would authorize opening the historical holdout once, not live trading.

## Run

Start the persistent process before 10:00 ET using paper credentials:

```bash
set -a; source .env; set +a
SURFACE_BUTTERFLY_PAPER_PROBE=true \
  .venv/bin/floor-surface-butterfly-probe intraday-run
```

For a scheduler or service that invokes one tick at a time, use
`intraday-once`. Read-only inspection commands are:

```bash
.venv/bin/floor-surface-butterfly-probe intraday-state
.venv/bin/floor-surface-butterfly-probe intraday-report
tail -f state/intraday_surface_forward_events.jsonl
```

Do not run the older locked-11:00 or mechanics cohorts simultaneously. Each
cohort nevertheless has a distinct broker client-order-ID namespace, state
file, and journal to prevent accidental reconciliation collisions.
