from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


class ConfigError(ValueError):
    """Raised when configuration is absent, inconsistent, or unsafe."""


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _decimal(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.getenv(name, default))
    except Exception as exc:
        raise ConfigError(f"{name} must be a decimal number") from exc


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    trading_base_url: str
    data_base_url: str
    paper: bool
    live_confirmed: bool
    options_feed: str
    symbol: str
    buffer_dollars: Decimal
    spread_width: Decimal
    stop_buffer: Decimal
    risk_fraction: Decimal
    take_profit_fraction: Decimal
    min_credit: Decimal
    max_contracts: int
    max_daily_losses: int
    poll_seconds_idle: int
    poll_seconds_open: int
    request_timeout_seconds: int
    max_quote_age_seconds: int
    entry_time: str
    entry_cutoff_time: str
    take_profit_cutoff_time: str
    hard_close_time: str
    timezone: str
    state_path: Path
    telegram_token: str | None
    telegram_chat_id: str | None
    dry_run: bool

    @classmethod
    def from_env(cls, *, require_credentials: bool = True) -> "Config":
        paper = _bool("ALPACA_PAPER", True)
        live_confirmed = _bool("LIVE_TRADING_CONFIRMED", False)
        api_key = os.getenv("ALPACA_API_KEY", "").strip()
        api_secret = os.getenv("ALPACA_API_SECRET", "").strip()
        trading_default = (
            "https://paper-api.alpaca.markets"
            if paper
            else "https://api.alpaca.markets"
        )
        cfg = cls(
            api_key=api_key,
            api_secret=api_secret,
            trading_base_url=os.getenv("ALPACA_TRADING_URL", trading_default).rstrip("/"),
            data_base_url=os.getenv(
                "ALPACA_DATA_URL", "https://data.alpaca.markets"
            ).rstrip("/"),
            paper=paper,
            live_confirmed=live_confirmed,
            options_feed=os.getenv("OPTIONS_FEED", "indicative").strip().lower(),
            symbol=os.getenv("UNDERLYING", "SPY").strip().upper(),
            buffer_dollars=_decimal("BUFFER_DOLLARS", "15"),
            spread_width=_decimal("SPREAD_WIDTH", "1"),
            stop_buffer=_decimal("STOP_BUFFER", "3"),
            risk_fraction=_decimal("RISK_FRACTION", "0.01"),
            take_profit_fraction=_decimal("TAKE_PROFIT_FRACTION", "0.50"),
            min_credit=_decimal("MIN_CREDIT", "0.05"),
            max_contracts=_int("MAX_CONTRACTS", 10),
            max_daily_losses=_int("MAX_DAILY_LOSSES", 3),
            poll_seconds_idle=_int("POLL_SECONDS_IDLE", 60),
            poll_seconds_open=_int("POLL_SECONDS_OPEN", 15),
            request_timeout_seconds=_int("REQUEST_TIMEOUT_SECONDS", 10),
            max_quote_age_seconds=_int("MAX_QUOTE_AGE_SECONDS", 30),
            entry_time=os.getenv("ENTRY_TIME_ET", "09:45"),
            entry_cutoff_time=os.getenv("ENTRY_CUTOFF_TIME_ET", "14:00"),
            take_profit_cutoff_time=os.getenv("TAKE_PROFIT_CUTOFF_TIME_ET", "14:00"),
            hard_close_time=os.getenv("HARD_CLOSE_TIME_ET", "15:00"),
            timezone="America/New_York",
            state_path=Path(os.getenv("STATE_PATH", "state/daily.json")),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            dry_run=_bool("DRY_RUN", True),
        )
        cfg.validate(require_credentials=require_credentials)
        return cfg

    def validate(self, *, require_credentials: bool = True) -> None:
        if require_credentials and (not self.api_key or not self.api_secret):
            raise ConfigError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
        if self.symbol != "SPY":
            raise ConfigError("UNDERLYING must be SPY for this bot")
        if not self.paper and not self.live_confirmed:
            raise ConfigError(
                "live endpoint blocked: set LIVE_TRADING_CONFIRMED=true as a second opt-in"
            )
        if not self.paper and self.options_feed != "opra":
            raise ConfigError("live trading requires OPTIONS_FEED=opra")
        if self.options_feed not in {"indicative", "opra"}:
            raise ConfigError("OPTIONS_FEED must be indicative or opra")
        if self.spread_width <= 0 or self.buffer_dollars <= 0:
            raise ConfigError("spread width and buffer must be positive")
        if not (Decimal("0") < self.risk_fraction <= Decimal("0.05")):
            raise ConfigError("RISK_FRACTION must be greater than 0 and at most 0.05")
        if not (Decimal("0") < self.take_profit_fraction < Decimal("1")):
            raise ConfigError("TAKE_PROFIT_FRACTION must be between 0 and 1")
        if self.max_contracts < 1 or self.max_daily_losses < 1:
            raise ConfigError("contract and loss caps must be positive")
        if min(self.poll_seconds_idle, self.poll_seconds_open) < 1:
            raise ConfigError("poll intervals must be positive")

