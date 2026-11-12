"""Configuration for the live worker.

Every secret is read from the environment. Nothing is ever logged in full:
the loader redacts anything that looks like a credential before it reaches
stdout, because worker logs are the most likely place for a key to leak.

Copy `.env.example` to `.env` and fill it in. Never commit `.env`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Anything matching these patterns has its VALUE redacted.
_SECRET_NAME = re.compile(
    r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS?|PRIVATE)", re.IGNORECASE
)
# `NAME=value` form: replace the value.
_SECRET_ASSIGNMENT = re.compile(
    r"((?:[A-Za-z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS?)[A-Za-z0-9_]*)\s*[=:]\s*)"
    r"([^\s,;'\"}]+)",
    re.IGNORECASE,
)


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader. Does not overwrite an existing environment variable."""
    env_path = path or (Path(__file__).resolve().parents[1] / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def redact(text: str) -> str:
    """Replace anything that looks like a secret with a placeholder.

    Two forms are handled:
      * `SUPABASE_SERVICE_ROLE_KEY=supersecret` -> the VALUE is replaced
      * a bare mention of a credential name -> the name is replaced

    The value form is the one that matters: it is what actually leaks.
    """
    text = _SECRET_ASSIGNMENT.sub(r"\1***REDACTED***", text)
    return _SECRET_NAME.sub("***REDACTED***", text)


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    service_role_key: str

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required. "
                "See .env.example."
            )
        return cls(url=url, service_role_key=key)


@dataclass(frozen=True)
class BinanceConfig:
    """Public market data only. No API key is needed for market data streams.

    If the user later adds execution, the trading key belongs here and only
    here -- never in the Vercel environment.
    """

    websocket_base: str = "wss://stream.binance.com:9443"
    rest_base: str = "https://api.binance.com"
    # Symbols we subscribe to. Lowercase, USD-quoted.
    symbols: tuple[str, ...] = ("btcusdt", "ethusdt", "solusdt")

    @classmethod
    def from_env(cls) -> "BinanceConfig":
        raw = os.environ.get("BINANCE_SYMBOLS", "btcusdt,ethusdt,solusdt")
        symbols = tuple(s.strip().lower() for s in raw.split(",") if s.strip())
        return cls(
            websocket_base=os.environ.get(
                "BINANCE_WS_BASE", "wss://stream.binance.com:9443"
            ),
            rest_base=os.environ.get("BINANCE_REST_BASE", "https://api.binance.com"),
            symbols=symbols,
        )


@dataclass(frozen=True)
class AlpacaConfig:
    """US equities. Basic (free) plan: IEX only, 30 websocket symbols.

    IEX is a fraction of consolidated volume. Every US row carries this fact.
    """

    data_url: str = "https://data.alpaca.markets"
    websocket_url: str = "wss://stream.data.alpaca.markets/v2/iex"
    key_id: str | None = None
    secret_key: str | None = None
    symbols: tuple[str, ...] = ("QQQ", "SPY", "GLD", "AAPL", "NVDA", "TLT")

    @classmethod
    def from_env(cls) -> "AlpacaConfig":
        raw = os.environ.get("ALPACA_SYMBOLS", "QQQ,SPY,GLD,AAPL,NVDA,TLT")
        symbols = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
        return cls(
            key_id=os.environ.get("ALPACA_KEY_ID"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
            symbols=symbols,
            websocket_url=os.environ.get(
                "ALPACA_WS_BASE", "wss://stream.data.alpaca.markets/v2/iex"
            ),
        )

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret_key)


@dataclass(frozen=True)
class OandaConfig:
    """Forex and metals. Practice account only.

    OANDA practice quotes are indicative. Every FX row carries that fact.
    """

    practice_rest: str = "https://api-fxpractice.oanda.com"
    practice_stream: str = "https://stream-fxpractice.oanda.com"
    token: str | None = None
    account_id: str | None = None
    symbols: tuple[str, ...] = ("EUR_USD", "GBP_USD", "XAU_USD")

    @classmethod
    def from_env(cls) -> "OandaConfig":
        raw = os.environ.get("OANDA_SYMBOLS", "EUR_USD,GBP_USD,XAU_USD")
        symbols = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
        return cls(
            token=os.environ.get("OANDA_TOKEN"),
            account_id=os.environ.get("OANDA_ACCOUNT_ID"),
            symbols=symbols,
            practice_stream=os.environ.get(
                "OANDA_STREAM_BASE", "https://stream-fxpractice.oanda.com"
            ),
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.account_id)


@dataclass(frozen=True)
class WorkerConfig:
    """Top-level worker settings."""

    worker_id: str = "live-worker-1"
    # How often the heartbeat is written. The freshness gate treats a heartbeat
    # older than 2 minutes as stale.
    heartbeat_seconds: int = 10
    # The Realtime bundle throttle. 5s => ~518k messages/month against a 2M quota.
    realtime_bundle_seconds: int = 5
    # Which timeframes we build bars for.
    timeframes: tuple[str, ...] = ("1m", "5m", "1h", "1d")
    # How often the economic calendar is refreshed. The schedule changes on
    # its own, so a stale calendar is a calendar that lies.
    calendar_refresh_hours: int = 6
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            worker_id=os.environ.get("WORKER_ID", "live-worker-1"),
            heartbeat_seconds=int(os.environ.get("HEARTBEAT_SECONDS", "10")),
            realtime_bundle_seconds=int(os.environ.get("REALTIME_BUNDLE_SECONDS", "5")),
            timeframes=tuple(
                s.strip()
                for s in os.environ.get("TIMEFRAMES", "1m,5m,1h,1d").split(",")
                if s.strip()
            ),
            calendar_refresh_hours=int(
                os.environ.get("CALENDAR_REFRESH_HOURS", "6")
            ),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )


@dataclass(frozen=True)
class AppConfig:
    """Everything the worker needs, assembled once at startup."""

    supabase: SupabaseConfig
    worker: WorkerConfig
    binance: BinanceConfig
    alpaca: AlpacaConfig
    oanda: OandaConfig
    # Which venues to actually connect. A venue without credentials is skipped
    # rather than crashing the worker.
    enabled_venues: tuple[str, ...] = field(default=("binance",))

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        venues = os.environ.get("ENABLED_VENUES", "binance")
        return cls(
            supabase=SupabaseConfig.from_env(),
            worker=WorkerConfig.from_env(),
            binance=BinanceConfig.from_env(),
            alpaca=AlpacaConfig.from_env(),
            oanda=OandaConfig.from_env(),
            enabled_venues=tuple(v.strip() for v in venues.split(",") if v.strip()),
        )
