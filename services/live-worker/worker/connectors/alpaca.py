"""Alpaca market-data connector (US equities, IEX feed).

Read-only. The Basic plan provides the IEX feed, which is a fraction of
consolidated volume; every event is labelled `alpaca_iex` and the instrument
registry marks the data quality as `partial`. The UI must never present an IEX
price as the consolidated price.

Facts this connector is built around:
  * The stream URL for the free plan is `/v2/iex`.
  * Authentication is a JSON message, then a subscription message.
  * A connection can be closed by the server at any time; reconnect with
    jittered backoff.
  * The Basic plan allows 30 websocket symbols. Our universe uses six.

No API key is logged. The key lives in the worker environment only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from .base import Trade, parse_rfc3339, reconnect_delay

log = logging.getLogger(__name__)

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
# Alpaca sends a heartbeat roughly every 10 seconds. A minute of silence means
# the socket is dead even if the TCP connection still looks open.
SILENCE_TIMEOUT = 70


class AlpacaError(RuntimeError):
    """The venue rejected authentication or a subscription."""


class AlpacaStream:
    """A resilient Alpaca trade stream.

    Usage:

        stream = AlpacaStream(("QQQ",), key_id, secret_key)
        async for trade in stream.trades():
            ...
    """

    name = "alpaca"

    def __init__(
        self,
        symbols: tuple[str, ...],
        key_id: str | None,
        secret_key: str | None,
        websocket_url: str = "wss://stream.data.alpaca.markets/v2/iex",
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.symbols = tuple(s.upper() for s in symbols)
        self.key_id = key_id
        self.secret_key = secret_key
        self.websocket_url = websocket_url
        self._on_event = on_event
        self._connected = False
        self._closed = False
        self.reconnects = 0
        self.last_message_at: datetime | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def lag_ms(self) -> int | None:
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
                delay = reconnect_delay(self.reconnects, RECONNECT_BASE, RECONNECT_MAX)
                log.warning(
                    "alpaca stream dropped (%s); reconnecting in %.1fs (attempt %d)",
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

        if not self.key_id or not self.secret_key:
            raise AlpacaError("ALPACA_KEY_ID and ALPACA_SECRET_KEY are required")

        async with websockets.connect(
            self.websocket_url,
            ping_interval=20,
            ping_timeout=20,
            max_size=2**20,
        ) as ws:
            await self._authenticate(ws)
            await ws.send(
                json.dumps({"action": "subscribe", "trades": list(self.symbols)})
            )
            self._connected = True
            self.reconnects = 0
            log.info("alpaca connected: %s", ", ".join(self.symbols))

            while not self._closed:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=SILENCE_TIMEOUT)
                except asyncio.TimeoutError:
                    log.warning("alpaca silent for %ds; recycling connection", SILENCE_TIMEOUT)
                    return

                self.last_message_at = datetime.now(timezone.utc)
                for trade in self.parse_messages(raw):
                    if self._on_event:
                        self._on_event(
                            "trade", {"symbol": trade.symbol, "price": trade.price}
                        )
                    yield trade

    async def _authenticate(self, ws) -> None:
        """Send the auth message and wait for the venue's verdict."""
        await ws.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": self.key_id,
                    "secret": self.secret_key,
                }
            )
        )
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            for message in _decode(raw):
                kind = message.get("T")
                if kind == "success" and message.get("msg") == "authenticated":
                    return
                if kind == "error":
                    raise AlpacaError(
                        f"alpaca auth failed: {message.get('code')} {message.get('msg')}"
                    )

    @staticmethod
    def parse_messages(raw: str | bytes) -> list[Trade]:
        """Parse one websocket frame into normalised trades.

        Alpaca sends an array of objects per frame. Control messages
        (`success`, `subscription`, `error`) are ignored here; errors during
        the live session are logged by the caller's reconnect path.
        """
        out: list[Trade] = []
        for message in _decode(raw):
            if message.get("T") != "t":
                continue
            try:
                out.append(
                    Trade(
                        symbol=str(message["S"]).upper(),
                        price=float(message["p"]),
                        size=float(message.get("s", 0.0)),
                        # Alpaca trade frames do not carry the taker side.
                        side=None,
                        venue="alpaca",
                        ts=parse_rfc3339(message.get("t")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def close(self) -> None:
        self._closed = True


# ------------------------------------------------------------- history ----

# Our timeframe names -> Alpaca's.
TIMEFRAMES = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}


def fetch_bars(
    symbol: str,
    timeframe: str = "1m",
    limit: int = 500,
    key_id: str | None = None,
    secret_key: str | None = None,
    data_url: str = "https://data.alpaca.markets",
) -> list[dict]:
    """Closed bars in the shape every connector shares.

    `ts` is the bar's OPEN time in UTC. Alpaca's feed is IEX on the free
    plan, so every one of these rows is `official = false`.
    """
    import urllib.parse
    import urllib.request

    if not key_id or not secret_key:
        raise AlpacaError("ALPACA_KEY_ID and ALPACA_SECRET_KEY are required")

    vendor_timeframe = TIMEFRAMES.get(timeframe, timeframe)
    query = urllib.parse.urlencode(
        {
            "timeframe": vendor_timeframe,
            "limit": min(int(limit), 1000),
            "feed": "iex",
            "adjustment": "raw",
            "sort": "asc",
        }
    )
    url = (
        f"{data_url.rstrip('/')}/v2/stocks/{urllib.parse.quote(symbol.upper())}/bars?{query}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "User-Agent": "trading-command-center/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_bars(payload)


def parse_bars(payload: dict) -> list[dict]:
    """Normalise an Alpaca bars payload into the shared bar shape."""
    rows = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    out: list[dict] = []
    for row in rows:
        try:
            out.append(
                {
                    "ts": parse_rfc3339(row["t"]),
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row.get("v", 0.0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda bar: bar["ts"])
    return out


def _decode(raw: str | bytes) -> list[dict]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []
