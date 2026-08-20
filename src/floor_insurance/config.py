from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
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


def _optional_decimal(name: str) -> Decimal | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception as exc:
        raise ConfigError(f"{name} must be a decimal number") from exc


def _nullable_decimal(name: str, default: str) -> Decimal | None:
    raw = os.getenv(name, default).strip().lower()
    if raw in {"", "none", "off", "disabled"}:
        return None
    try:
        return Decimal(raw)
    except Exception as exc:
        raise ConfigError(f"{name} must be a decimal number or none") from exc


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    trading_base_url: str
    data_base_url: str
    paper: bool
    live_confirmed: bool
    atm_live_confirmed: bool
    stock_feed: str
    options_feed: str
    symbol: str
    signal_symbol: str
    trend_window: int
    trend_mode: str
    strike_selection: str
    buffer_dollars: Decimal
    spread_width: Decimal
    stop_buffer: Decimal
    stop_debit_multiple: Decimal
    max_total_loss_dollars: Decimal
    risk_fraction: Decimal
    risk_budget_dollars: Decimal | None
    take_profit_fraction: Decimal | None
    min_credit: Decimal
    shadow_min_credit: Decimal
    max_contracts: int
    max_daily_entries: int
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
    shadow_mode: bool
    shadow_equity: Decimal
    shadow_fees_per_spread: Decimal
    shadow_log_path: Path

    @classmethod
    def from_env(cls, *, require_credentials: bool = True) -> "Config":
        paper = _bool("ALPACA_PAPER", True)
        live_confirmed = _bool("LIVE_TRADING_CONFIRMED", False)
        atm_live_confirmed = _bool("ATM_LIVE_CONFIRMED", False)
        options_feed = os.getenv("OPTIONS_FEED", "indicative").strip().lower()
        api_key = os.getenv("ALPACA_API_KEY", "").strip()
        api_secret = os.getenv("ALPACA_API_SECRET", "").strip()
        trading_default = (
            "https://paper-api.alpaca.markets"
            if paper
            else "https://api.alpaca.markets"
        )
        symbol = os.getenv("UNDERLYING", "SPY").strip().upper()
        signal_default = "SPY" if symbol == "XSP" else symbol
        cfg = cls(
            api_key=api_key,
            api_secret=api_secret,
            trading_base_url=os.getenv("ALPACA_TRADING_URL", trading_default).rstrip("/"),
            data_base_url=os.getenv(
                "ALPACA_DATA_URL", "https://data.alpaca.markets"
            ).rstrip("/"),
            paper=paper,
            live_confirmed=live_confirmed,
            atm_live_confirmed=atm_live_confirmed,
            stock_feed=os.getenv(
                "STOCK_FEED", "iex" if paper else "sip"
            ).strip().lower(),
            options_feed=options_feed,
            symbol=symbol,
            signal_symbol=os.getenv("SIGNAL_SYMBOL", signal_default).strip().upper(),
            trend_window=_int("TREND_WINDOW", 20),
            trend_mode=os.getenv("TREND_MODE", "above").strip().lower(),
            strike_selection=os.getenv("STRIKE_SELECTION", "atm").strip().lower(),
            buffer_dollars=_decimal("BUFFER_DOLLARS", "15"),
            spread_width=_decimal("SPREAD_WIDTH", "1"),
            stop_buffer=_decimal("STOP_BUFFER", "3"),
            stop_debit_multiple=_decimal("STOP_DEBIT_MULTIPLE", "2"),
            max_total_loss_dollars=_decimal("MAX_TOTAL_LOSS_DOLLARS", "100"),
            risk_fraction=_decimal("RISK_FRACTION", "0.01"),
            risk_budget_dollars=_optional_decimal("RISK_BUDGET_DOLLARS"),
            take_profit_fraction=_nullable_decimal("TAKE_PROFIT_FRACTION", "0.50"),
            min_credit=_decimal("MIN_CREDIT", "0.05"),
            shadow_min_credit=_decimal("SHADOW_MIN_CREDIT", "0.01"),
            max_contracts=_int("MAX_CONTRACTS", 1),
            max_daily_entries=_int("MAX_DAILY_ENTRIES", 1),
            max_daily_losses=_int("MAX_DAILY_LOSSES", 3),
            poll_seconds_idle=_int("POLL_SECONDS_IDLE", 60),
            poll_seconds_open=_int("POLL_SECONDS_OPEN", 15),
            request_timeout_seconds=_int("REQUEST_TIMEOUT_SECONDS", 10),
            max_quote_age_seconds=_int(
                "MAX_QUOTE_AGE_SECONDS", 90 if options_feed == "indicative" else 30
            ),
            entry_time=os.getenv("ENTRY_TIME_ET", "09:45"),
            entry_cutoff_time=os.getenv("ENTRY_CUTOFF_TIME_ET", "14:00"),
            take_profit_cutoff_time=os.getenv("TAKE_PROFIT_CUTOFF_TIME_ET", "14:00"),
            hard_close_time=os.getenv("HARD_CLOSE_TIME_ET", "15:00"),
            timezone="America/New_York",
            state_path=Path(os.getenv("STATE_PATH", "state/daily.json")),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            dry_run=_bool("DRY_RUN", True),
            shadow_mode=_bool("SHADOW_MODE", False),
            shadow_equity=_decimal("SHADOW_EQUITY", "10000"),
            shadow_fees_per_spread=_decimal("SHADOW_FEES_PER_SPREAD", "0"),
            shadow_log_path=Path(
                os.getenv("SHADOW_LOG_PATH", "state/shadow_events.jsonl")
            ),
        )
        cfg.validate(require_credentials=require_credentials)
        return cfg

    def minimum_viable_equity(self) -> Decimal:
        """Smallest balance that can fund one spread under the risk rule.

        The risk rule sizes from the maximum loss, which is nearly the full
        spread width. A $1-wide spread sold for the $0.05 minimum therefore
        risks $95, so a 1% risk fraction cannot fund a single contract below
        $9,500. With an absolute risk budget, the minimum is instead the
        spread's maximum loss. Below this balance every entry safely skips.
        """

        credit_floor = self.shadow_min_credit if self.shadow_mode else self.min_credit
        worst_case = (self.spread_width - credit_floor) * Decimal("100")
        if self.risk_budget_dollars is not None:
            return worst_case.quantize(Decimal("0.01"))
        return (worst_case / self.risk_fraction).quantize(Decimal("0.01"))

    def validate(self, *, require_credentials: bool = True) -> None:

        if require_credentials and (not self.api_key or not self.api_secret):
            raise ConfigError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
        if self.dry_run and self.shadow_mode:
            raise ConfigError("DRY_RUN and SHADOW_MODE cannot both be true")
        if self.shadow_equity <= 0 or self.shadow_fees_per_spread < 0:
            raise ConfigError(
                "shadow equity must be positive and shadow fees cannot be negative"
            )
        if self.shadow_log_path == self.state_path:
            raise ConfigError("SHADOW_LOG_PATH and STATE_PATH must be different files")
        if self.symbol not in {"SPY", "QQQ", "IWM", "XSP"}:
            raise ConfigError("UNDERLYING must be SPY, QQQ, IWM, or XSP")
        if not self.signal_symbol:
            raise ConfigError("SIGNAL_SYMBOL is required")
        if self.trend_window < 2:
            raise ConfigError("TREND_WINDOW must be at least 2")
        if self.trend_mode not in {"above", "crossover"}:
            raise ConfigError("TREND_MODE must be above or crossover")
        if self.strike_selection not in {"atm", "buffered"}:
            raise ConfigError("STRIKE_SELECTION must be atm or buffered")
        if self.symbol == "XSP" and not self.paper:
            raise ConfigError(
                "live XSP trading is blocked: Alpaca retail currently supports "
                "XSP only in paper trading"
            )
        if not self.paper and not self.live_confirmed:
            raise ConfigError(
                "live endpoint blocked: set LIVE_TRADING_CONFIRMED=true as a second opt-in"
            )
        if not self.paper and self.options_feed != "opra":
            raise ConfigError("live trading requires OPTIONS_FEED=opra")
        if not self.paper and self.stock_feed != "sip":
            raise ConfigError("live trading requires STOCK_FEED=sip")
        if (
            not self.paper
            and self.strike_selection == "atm"
            and not self.atm_live_confirmed
        ):
            raise ConfigError(
                "live ATM strategy is research-blocked: set ATM_LIVE_CONFIRMED=true "
                "only after independent forward validation"
            )
        if self.stock_feed not in {"iex", "sip"}:
            raise ConfigError("STOCK_FEED must be iex or sip")
        if self.options_feed not in {"indicative", "opra"}:
            raise ConfigError("OPTIONS_FEED must be indicative or opra")
        if min(self.spread_width, self.buffer_dollars, self.stop_buffer) <= 0:
            raise ConfigError("spread width, strike buffer, and stop buffer must be positive")
        if self.stop_debit_multiple <= 1:
            raise ConfigError("STOP_DEBIT_MULTIPLE must be greater than one")
        if self.max_total_loss_dollars <= 0:
            raise ConfigError("MAX_TOTAL_LOSS_DOLLARS must be positive")
        if not (Decimal("0") < self.min_credit < self.spread_width):
            raise ConfigError("MIN_CREDIT must be positive and below SPREAD_WIDTH")
        if not (Decimal("0") < self.shadow_min_credit < self.spread_width):
            raise ConfigError(
                "SHADOW_MIN_CREDIT must be positive and below SPREAD_WIDTH"
            )
        if not (Decimal("0") < self.risk_fraction <= Decimal("0.05")):
            raise ConfigError("RISK_FRACTION must be greater than 0 and at most 0.05")
        if self.risk_budget_dollars is not None and self.risk_budget_dollars <= 0:
            raise ConfigError("RISK_BUDGET_DOLLARS must be positive when configured")
        if self.take_profit_fraction is not None and not (
            Decimal("0") < self.take_profit_fraction < Decimal("1")
        ):
            raise ConfigError("TAKE_PROFIT_FRACTION must be between 0 and 1")
        if min(self.max_contracts, self.max_daily_entries, self.max_daily_losses) < 1:
            raise ConfigError("contract and loss caps must be positive")
        if min(self.poll_seconds_idle, self.poll_seconds_open) < 1:
            raise ConfigError("poll intervals must be positive")
        if min(self.request_timeout_seconds, self.max_quote_age_seconds) < 1:
            raise ConfigError("request timeout and quote age must be positive")
        try:
            entry = time.fromisoformat(self.entry_time)
            entry_cutoff = time.fromisoformat(self.entry_cutoff_time)
            profit_cutoff = time.fromisoformat(self.take_profit_cutoff_time)
            hard_close = time.fromisoformat(self.hard_close_time)
        except ValueError as exc:
            raise ConfigError("schedule values must use HH:MM or HH:MM:SS") from exc
        if not (entry < entry_cutoff <= hard_close and profit_cutoff <= hard_close):
            raise ConfigError("schedule must order entry < entry cutoff <= hard close")
