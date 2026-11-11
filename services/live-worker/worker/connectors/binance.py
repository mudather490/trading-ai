"""Binance public market data connector.

Keyless, free, full-market. This is the one venue where the free tier gives
us real-time data for the whole market, which is why crypto is the app's
real-time showcase.

Facts this connector is built around (from Binance's documentation):
  * A connection lives at most 24 hours. Reconnecting is routine, not an edge
    case, so the reconnect path is a first-class code path.
  * 1,024 streams per connection. Our 12 symbols use a handful.
  * 5 incoming messages per second. We send one SUBSCRIBE and then listen.
  * The server pings every 20 seconds; a connection without a pong within a
    minute is closed. We answer pings.
  * 300 connection attempts per 5 minutes per IP. A reconnect loop must
    back off or we will ban ourselves.

The connector emits normalised trade events. It never places an order.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Awaitable, Callable

log = logging.getLogger(__name__)

# Binance closes a connection after this long. We reconnect before that.
# 23 hours leaves an hour of slack for the swap.
CONNECTION_MAX_LIFETIME = 23 * 3600
# Backoff between reconnect attempts. Jitter matters: without it, every
# worker that drops at the same time reconnects at the same time.
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0


@dataclass(frozen=True)
class Trade:
    """One normalised trade."""

    symbol: str
    price: float
    size: float
    ts: datetime
    # 'buy' means the taker bought; 'sell' means the taker sold.
    side: str | None = None


def _parse_ts(ms: int | None) -> datetime:
    if ms is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class BinanceStream:
    """A resilient Binance combined-stream websocket client.

    Usage:

        stream = BinanceStream(symbols=("btcusdt",))
        async for trade in stream.trades():
            ...
    """

    def __init__(
        self,
        symbols: tuple[str, ...],
        base_url: str = "wss://stream.binance.com:9443",
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.symbols = tuple(s.lower() for s in symbols)
        self.base_url = base_url.rstrip("/")
        self._on_event = on_event
        self._queue: asyncio.Queue[Trade] = asyncio.Queue(maxsize=10_000)
        self._connected = False
        self._closed = False
        self.reconnects = 0
        self.last_message_at: datetime | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def lag_ms(self) -> int | None:
        """How long ago the last message arrived. The heartbeat reports this."""
        if self.last_message_at is None:
            return None
        delta = datetime.now(timezone.utc) - self.last_message_at
        return int(delta.total_seconds() * 1000)

    async def trades(self) -> AsyncIterator[Trade]:
        """Yield trades forever, reconnecting as needed."""
        while not self._closed:
            try:
                async for trade in self._run_session():
                    yield trade
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._connected = False
                self.reconnects += 1
                delay = min(RECONNECT_BASE * (2 ** (self.reconnects - 1)), RECONNECT_MAX)
                delay += random.uniform(0, delay * 0.3)  # jitter
                log.warning(
                    "binance stream dropped (%s); reconnecting in %.1fs (attempt %d)",
                    exc,
                    delay,
                    self.reconnects,
                )
                await asyncio.sleep(delay)

    async def _run_session(self) -> AsyncIterator[Trade]:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'websockets' package is required. pip install websockets"
            ) from exc

        streams = "/".join(f"{s}@trade" for s in self.symbols)
        url = f"{self.base_url}/stream?streams={streams}"

        started = time.monotonic()
        async with websockets.connect(url, ping_interval=None, max_size=2**20) as ws:
            self._connected = True
            self.reconnects = 0
            log.info("binance connected: %s", streams)

            while not self._closed:
                if time.monotonic() - started > CONNECTION_MAX_LIFETIME:
                    log.info("binance session lifetime reached; recycling")
                    return

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=70)
                except asyncio.TimeoutError:
                    # Binance pings every 20s. 70s of silence means the
                    # connection is dead even though the socket looks open.
                    log.warning("binance silent for 70s; recycling connection")
                    return

                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                self.last_message_at = datetime.now(timezone.utc)

                trade = self._parse(message)
                if trade is not None:
                    if self._on_event:
                        self._on_event("trade", {"symbol": trade.symbol, "price": trade.price})
                    try:
                        self._queue.put_nowait(trade)
                    except asyncio.QueueFull:
                        # The consumer is behind. Drop the oldest, not the
                        # newest: we care about the current price.
                        try:
                            self._queue.get_nowait()
                            self._queue.put_nowait(trade)
                        except (asyncio.QueueEmpty, asyncio.QueueFull):
                            pass
                    yield trade

    @staticmethod
    def _parse(message: dict) -> Trade | None:
        """Extract a trade from a combined-stream message."""
        data = message.get("data", message)
        if data.get("e") != "trade":
            return None
        try:
            return Trade(
                symbol=str(data["s"]).upper(),
                price=float(data["p"]),
                size=float(data.get("q", 0.0)),
                ts=_parse_ts(data.get("T")),
                # 'm' is "is the buyer the market maker". If true, the taker SOLD.
                side="sell" if data.get("m") else "buy",
            )
        except (KeyError, TypeError, ValueError):
            return None

    def close(self) -> None:
        self._closed = True


async def fetch_klines(
    symbol: str,
    interval: str = "1m",
    limit: int = 500,
    base_url: str = "https://api.binance.com",
) -> list[dict]:
    """Backfill closed bars over REST. Used to seed a fresh install.

    Returns the raw kline rows. The key is Binance's own, so the bars carry
    their own provenance.
    """
    import urllib.parse
    import urllib.request

    url = (
        f"{base_url.rstrip('/')}/api/v3/klines"
        f"?symbol={urllib.parse.quote(symbol.upper())}"
        f"&interval={interval}&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "trading-command-center/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = json.loads(resp.read().decode("utf-8"))

    out: list[dict] = []
    for row in rows:
        # Binance kline: [openTime, open, high, low, close, volume, closeTime, ...]
        out.append(
            {
                "ts": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "closed_at": datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc),
            }
        )
    return out
