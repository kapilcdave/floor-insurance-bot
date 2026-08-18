from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient, AlpacaError
from .config import Config, ConfigError
from .engine import TradingEngine
from .notify import Notifier
from .state import StateStore
from .strategy import StrategySkip

LOG = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="0DTE SPY floor-insurance bot")
    parser.add_argument("command", nargs="?", choices=("run", "once", "doctor", "state"), default="run")
    return parser


def _logging() -> None:
    logging.basicConfig(
        level=getattr(logging, __import__("os").getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _components(config: Config) -> tuple[TradingEngine, AlpacaClient]:
    alpaca = AlpacaClient(config)
    store = StateStore(config.state_path)
    notifier = Notifier(
        config.telegram_token,
        config.telegram_chat_id,
        config.request_timeout_seconds,
    )
    return TradingEngine(config, alpaca, store, notifier), alpaca


def doctor(config: Config, alpaca: AlpacaClient) -> int:
    account = alpaca.account()
    clock = alpaca.clock()
    report = {
        "paper": config.paper,
        "dry_run": config.dry_run,
        "account_status": account.get("status"),
        "trading_blocked": account.get("trading_blocked"),
        "options_trading_level": account.get("options_trading_level"),
        "market_open": clock.get("is_open"),
        "next_open": clock.get("next_open"),
        "next_close": clock.get("next_close"),
        "stock_feed": config.stock_feed,
        "options_feed": config.options_feed,
        "telegram_configured": bool(config.telegram_token and config.telegram_chat_id),
    }
    print(json.dumps(report, indent=2))
    return 0 if not account.get("trading_blocked") and int(account.get("options_trading_level") or 0) >= 3 else 1


def main() -> int:
    _logging()
    args = _parser().parse_args()
    try:
        config = Config.from_env()
        engine, alpaca = _components(config)
        if args.command == "doctor":
            return doctor(config, alpaca)
        if args.command == "state":
            state = engine.store.load(datetime.now(ZoneInfo(config.timezone)).date().isoformat())
            print(json.dumps(state.to_dict(), indent=2))
            return 0
        if args.command == "once":
            engine.tick()
            return 0

        stopping = False

        def stop(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        LOG.info("bot started (paper=%s dry_run=%s)", config.paper, config.dry_run)
        while not stopping:
            try:
                delay = engine.tick()
            except StrategySkip as exc:
                LOG.info("strategy skipped this tick: %s", exc)
                delay = config.poll_seconds_idle
            except (AlpacaError, OSError, RuntimeError):
                LOG.exception("tick failed; state preserved and retrying")
                delay = config.poll_seconds_open
            for _ in range(delay):
                if stopping:
                    break
                time.sleep(1)
        LOG.info("bot stopped")
        return 0
    except (ConfigError, ValueError) as exc:
        LOG.error("configuration error: %s", exc)
        return 2
    except AlpacaError as exc:
        LOG.error("Alpaca error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

