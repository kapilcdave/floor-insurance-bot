# Floor Insurance Bot

A small, auditable 0DTE SPY bull-put-spread bot for Alpaca. It is designed for
a 1 GB RAM / 30 GB Linux VPS and includes Telegram notifications, persistent
daily state, paper-trading defaults, circuit breakers, and a systemd service.

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

The bot only manages orders and positions bearing its `floor-insurance-*`
client-order ID prefix. It does not flatten unrelated account positions.

## Status

The implementation is being built commit-by-commit. Paper trading remains the
default and live trading requires an explicit two-part opt-in.

## License

MIT

