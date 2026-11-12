"""Shared contract for every market-data connector.

A connector is a read-only adapter. It turns a venue's stream into a sequence
of normalised `Trade` events and exposes enough health for the heartbeat to
tell the difference between "quiet market" and "dead socket".

Nothing in this module can place an order. There is no order method to call.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class Trade:
    """One normalised market event.

    `price` is the last traded price when the venue provides one, or the mid
    price when the venue only publishes a quote (OANDA). `bid`/`ask` are kept
    when available so the UI can show the spread; `size` is zero when the
    venue does not publish trade size.
    """

    symbol: str
    price: float
    size: float
    ts: datetime
    # 'buy' means the taker bought; 'sell' means the taker sold.
    side: str | None = None
    # The venue that produced this event. Set by the connector, never guessed
    # by the caller.
    venue: str = ""
    bid: float | None = None
    ask: float | None = None


@runtime_checkable
class Stream(Protocol):
    """What the worker needs from a venue stream."""

    @property
    def connected(self) -> bool:
        ...

    @property
    def reconnects(self) -> int:
        ...

    @property
    def lag_ms(self) -> int | None:
        ...

    def trades(self) -> AsyncIterator[Trade]:
        ...


_FRACTIONAL = re.compile(r"(\.\d{6})\d+")


def canonical_symbol(symbol: str) -> str:
    """Map a venue symbol to the instrument-registry symbol.

    OANDA speaks `EUR_USD`; the registry speaks `EURUSD`. Binance and Alpaca
    already use the registry form after uppercasing.
    """
    return symbol.replace("_", "").strip().upper()


def parse_rfc3339(value: str | None) -> datetime:
    """Parse an RFC3339 timestamp, including nanosecond fractions.

    Python's `fromisoformat` handles microseconds but not the nanosecond
    timestamps used by Alpaca and OANDA. We truncate to microseconds rather
    than rounding: the bar builder only needs millisecond accuracy, and a
    truncated timestamp can never move an event into a future bar.
    """
    if not value:
        return datetime.now(timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = _FRACTIONAL.sub(r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reconnect_delay(attempt: int, base: float = 1.0, maximum: float = 60.0) -> float:
    """Exponential backoff with jitter.

    Jitter matters: without it every worker that drops at the same moment
    reconnects at the same moment.
    """
    if attempt <= 0:
        attempt = 1
    delay = min(base * (2 ** (attempt - 1)), maximum)
    return delay + random.uniform(0, delay * 0.3)
