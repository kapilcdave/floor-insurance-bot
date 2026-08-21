# SPY 0DTE intraday surface-butterfly research

Status: **preregistered before strategy-specific intraday option data is fetched**.

This experiment tests whether the 11:00 local-surface result is present across
the regular session. It is a new hypothesis, not a reinterpretation of the
locked 11:00 result.

## Locked rules

- SPY same-session 0DTE options only.
- Scan at exactly 10:00, 11:00, 12:00, 13:00, and 14:00 ET.
- At each scan, use the nearest whole-dollar strike plus offsets -3 through +3.
- Construct matching $1-wide call and put butterflies at each center.
- Require a call/put butterfly trade-price gap of at least $0.08.
- Buy the cheaper butterfly for a modeled debit no greater than $0.10 after
  $0.005 adverse fill per contract unit; charge $0.20 per round trip.
- Take only the first qualifying scan each session. Never re-enter that day.
- Exit exactly one hour after entry using a synchronized mark no more than five
  minutes old. No stop, take profit, direction, regime, or event filter.
- Size one butterfly. The stress run charges $0.01 adverse fill per contract
  unit at entry and exit.

## Data and chronology

Use February 1, 2024 through August 19, 2026. Keep the final 60 sessions, May 26
through August 19, 2026, sealed and absent from the strategy-specific option
cache. Split earlier sessions chronologically 75% training and 25% validation.

The development screen passes only if both base splits have at least 100/30
trades, positive average P&L, profit factor at least 1.25, maximum drawdown no
worse than -$500, and at least ten call and put butterflies. Both stress splits
must have positive average P&L. Report selected trades by entry hour, but do not
choose or suppress an hour after seeing the result.

## Limitation

Alpaca Basic historical one-minute option trade bars are not synchronized
executable quotes. This remains a rejection screen: apparent intraday parity
gaps may be stale-print artifacts, and passing cannot authorize live money.
