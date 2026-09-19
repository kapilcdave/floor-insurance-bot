from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time

from .alpaca import AlpacaClient, AlpacaError
from .wheel import WheelConfig, WheelEngine, WheelSkip

LOG = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wheelhouse",
        description="Disciplined covered-call and cash-secured-put wheel",
    )
    parser.add_argument("command", nargs="?", choices=("once", "run", "doctor"), default="once")
    return parser


def _logging() -> None:
    logging.basicConfig(
        level=getattr(logging, __import__("os").getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> int:
    _logging()
    args = _parser().parse_args()
    try:
        config = WheelConfig.from_env()
        engine = WheelEngine(config, AlpacaClient(config))
        if args.command == "doctor":
            report = engine.doctor()
            print(json.dumps(report, indent=2))
            healthy = (
                report["asset_tradable"]
                and report["asset_has_options"]
                and not report["trading_blocked"]
                and report["options_trading_level"] >= 1
            )
            return 0 if healthy else 1
        if args.command == "once":
            try:
                print(json.dumps(engine.tick().to_dict(), indent=2))
            except WheelSkip as exc:
                LOG.info("wheel skipped: %s", exc)
            return 0

        stopping = False

        def stop(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        LOG.info(
            "Wheelhouse started (symbol=%s paper=%s dry_run=%s)",
            config.symbol,
            config.paper,
            config.dry_run,
        )
        while not stopping:
            try:
                plan = engine.tick()
                LOG.info("wheel plan: %s", json.dumps(plan.to_dict(), sort_keys=True))
            except WheelSkip as exc:
                LOG.info("wheel skipped: %s", exc)
            except (AlpacaError, OSError, RuntimeError):
                LOG.exception("wheel tick failed; broker state will be reconciled on retry")
            for _ in range(config.poll_seconds):
                if stopping:
                    break
                time.sleep(1)
        LOG.info("Wheelhouse stopped")
        return 0
    except (ValueError, AlpacaError) as exc:
        LOG.error("Wheelhouse error: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
