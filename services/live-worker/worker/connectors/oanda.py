"""OANDA v20 pricing-stream connector (forex and metals).

Read-only. This connector uses a PRACTICE account's pricing stream. Practice
prices are indicative, not an executable feed; the instrument registry marks
the data quality as `indicative`, and every quote carries that label through
to the UI.

OANDA's stream is HTTP chunked, not a websocket: one JSON object per line.
A `PRICE` message carries a bid/ask ladder; we normalise it to a mid-price
trade so the rest of the worker has one event shape. `HEARTBEAT` messages
only prove the connection is alive.

No token is logged. The token lives in the worker environment only.
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


class OandaError(RuntimeError):
    """The venue rejected authentication or the pricing stream."""


class OandaStream:
    """A resilient OANDA pricing stream.

    Usage:

        stream = OandaStream(("EUR_USD",), token, account_id)
        async for trade in stream.trades():
            ...
    """

    name = "oanda"

    def __init__(
        self,
        instruments: tuple[str, ...],
        token: str | None,
        account_id: str | None,
        stream_url: str = "https://stream-fxpractice.oanda.com",
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.instruments = tuple(s.upper() for s in instruments)
        self.token = token
        self.account_id = account_id
        self.stream_url = stream_url.rstrip("/")
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
        """Yield prices forever, reconnecting as needed."""
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
                    "oanda stream dropped (%s); reconnecting in %.1fs (attempt %d)",
                    exc,
                    delay,
                    self.reconnects,
                )
                await asyncio.sleep(delay)

    async def _run_session(self) -> AsyncIterator[Trade]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'httpx' package is required. pip install httpx"
            ) from exc

        if not self.token or not self.account_id:
            raise OandaError("OANDA_TOKEN and OANDA_ACCOUNT_ID are required")

        url = (
            f"{self.stream_url}/v3/accounts/{self.account_id}/pricing/stream"
        )
        params = {"instruments": ",".join(self.instruments)}
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Datetime-Format": "RFC3339",
        }
        # A streaming response has no read timeout: the server is allowed to
        # stay quiet between prices. Connect/write timeouts still apply.
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "GET", url, params=params, headers=headers
            ) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")[:300]
                    raise OandaError(
                        f"oanda pricing stream returned {response.status_code}: {detail}"
                    )

                self._connected = True
                self.reconnects = 0
                log.info("oanda connected: %s", ", ".join(self.instruments))

                async for line in response.aiter_lines():
                    if self._closed:
                        return
                    self.last_message_at = datetime.now(timezone.utc)
                    trade = self.parse_line(line)
                    if trade is None:
                        continue
                    if self._on_event:
                        self._on_event(
                            "price", {"symbol": trade.symbol, "price": trade.price}
                        )
                    yield trade

    @staticmethod
    def parse_line(line: str) -> Trade | None:
        """Parse one newline-delimited OANDA message.

        `HEARTBEAT` and unknown message types return None. A `PRICE` that is
        not tradeable also returns None: publishing a non-tradeable price
        would keep the freshness gate green while the market is actually
        closed, which is exactly the sort of silent lie this system refuses.
        """
        if not line or not line.strip():
            return None
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(message, dict) or message.get("type") != "PRICE":
            return None
        if message.get("tradeable") is False:
            return None

        instrument = message.get("instrument")
        if not instrument:
            return None

        bid = _best_price(message.get("bids"), side="bid")
        ask = _best_price(message.get("asks"), side="ask")
        if bid is None:
            bid = _as_float(message.get("closeoutBid"))
        if ask is None:
            ask = _as_float(message.get("closeoutAsk"))
        if bid is None or ask is None:
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            return None

        return Trade(
            symbol=str(instrument).upper(),
            price=(bid + ask) / 2.0,
            # OANDA's pricing stream has no trade size.
            size=0.0,
            ts=parse_rfc3339(message.get("time")),
            side=None,
            venue="oanda",
            bid=bid,
            ask=ask,
        )

    def close(self) -> None:
        self._closed = True


# ------------------------------------------------------------- history ----

# Our timeframe names -> OANDA granularities.
TIMEFRAMES = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
}


def fetch_bars(
    instrument: str,
    timeframe: str = "1m",
    count: int = 500,
    token: str | None = None,
    rest_url: str = "https://api-fxpractice.oanda.com",
) -> list[dict]:
    """Closed bars in the shape every connector shares.

    `ts` is the bar's OPEN time in UTC. OANDA practice candles are
    indicative, so every one of these rows is `official = false`.
    """
    import urllib.parse
    import urllib.request

    if not token:
        raise OandaError("OANDA_TOKEN is required")

    granularity = TIMEFRAMES.get(timeframe, timeframe)
    query = urllib.parse.urlencode(
        {"granularity": granularity, "count": min(int(count), 5000)}
    )
    url = (
        f"{rest_url.rstrip('/')}/v3/instruments/{urllib.parse.quote(instrument.upper())}"
        f"/candles?{query}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Datetime-Format": "RFC3339",
            "User-Agent": "trading-command-center/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_candles(payload)


def parse_candles(payload: dict) -> list[dict]:
    """Normalise an OANDA candles payload into the shared bar shape.

    OANDA returns candles newest first and only sends complete candles once
    they close. We sort ascending so every connector hands the worker the
    same ordering.
    """
    rows = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                {
                    "ts": parse_rfc3339(row["time"]),
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row.get("volume", 0.0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda bar: bar["ts"])
    return out


def _best_price(levels, *, side: str) -> float | None:
    """Return the best bid (highest) or best ask (lowest) from a ladder."""
    if not isinstance(levels, list):
        return None
    prices: list[float] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _as_float(level.get("price"))
        if price is not None and price > 0:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if side == "bid" else min(prices)


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
