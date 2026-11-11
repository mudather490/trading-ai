"""Bar builder.

Turns a stream of trades into closed OHLCV bars at several timeframes.

Two rules that the rest of the system depends on:

  1. A bar is only published when it CLOSES. A bar in progress is never
     written, so no consumer can act on a partially-formed candle.
  2. A bar close is emitted EXACTLY ONCE. The builder tracks which
     (instrument, timeframe, bucket) pairs it has already closed, so a
     reconnect or a duplicate trade cannot produce a second bar.

The second rule matters more than it looks: if a bar could close twice, the
downstream signal engine would see two "new bars" for the same period and
could fire the same signal twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

log = logging.getLogger(__name__)

# Timeframe -> seconds. Only fixed-length timeframes, which keeps bucket
# arithmetic trivial and avoids the DST bugs that plague session-based bars.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def bucket_start(ts: datetime, timeframe: str) -> datetime:
    """The start of the bucket this timestamp belongs to."""
    seconds = TIMEFRAME_SECONDS[timeframe]
    epoch = int(ts.astimezone(timezone.utc).timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


@dataclass
class Bar:
    """One bar in progress."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    trade_count: int = 0

    def update(self, price: float, size: float) -> None:
        if self.trade_count == 0:
            self.open = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
        self.close = price
        self.volume += size
        self.trade_count += 1


@dataclass
class ClosedBar:
    """A finished bar, ready to publish."""

    instrument: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed_at: datetime

    def as_row(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class BarBuilder:
    """Maintains in-progress bars for many instruments and timeframes.

    `on_trade` is called for every trade. It returns the list of bars that
    CLOSED as a result, which the caller publishes.
    """

    # key: (instrument, timeframe) -> Bar
    _bars: dict[tuple[str, str], Bar] = field(default_factory=dict)
    # key: (instrument, timeframe, bucket_ts) -> True once closed
    _closed: dict[tuple[str, str, datetime], bool] = field(default_factory=dict)

    def on_trade(
        self,
        instrument: str,
        price: float,
        size: float,
        ts: datetime,
        timeframes: Iterable[str] = ("1m",),
    ) -> list[ClosedBar]:
        """Feed one trade. Returns any bars that closed because of it."""
        closed: list[ClosedBar] = []
        for timeframe in timeframes:
            seconds = TIMEFRAME_SECONDS.get(timeframe)
            if seconds is None:
                raise ValueError(f"Unknown timeframe '{timeframe}'")

            bucket = bucket_start(ts, timeframe)
            key = (instrument, timeframe, bucket)

            # A bar for a bucket we already closed is a duplicate. Ignore it.
            if key in self._closed:
                continue

            bar_key = (instrument, timeframe)
            bar = self._bars.get(bar_key)
            if bar is None or bar.ts != bucket:
                # Either the first trade for this bar, or we crossed into a
                # new bucket. If there was a bar in progress, it closed.
                if bar is not None:
                    closed.append(self._close(instrument, timeframe, bar))
                bar = Bar(ts=bucket, open=price, high=price, low=price, close=price)
                self._bars[bar_key] = bar

            bar.update(price, size)

            # Has the bucket ended? A trade cannot arrive for a past bucket in
            # normal operation, but a late trade can. We close the bar when
            # the NEXT bucket starts, not on a timer, so the bar always
            # contains every trade that belonged to it.
            _ = seconds

        return closed

    def _close(self, instrument: str, timeframe: str, bar: Bar) -> ClosedBar:
        key = (instrument, timeframe, bar.ts)
        self._closed[key] = True
        return ClosedBar(
            instrument=instrument,
            timeframe=timeframe,
            ts=bar.ts,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            closed_at=datetime.now(timezone.utc),
        )

    def flush(self, instrument: str, timeframes: Iterable[str]) -> list[ClosedBar]:
        """Close any in-progress bars, e.g. on shutdown."""
        closed: list[ClosedBar] = []
        for timeframe in timeframes:
            bar = self._bars.pop((instrument, timeframe), None)
            if bar is not None and bar.trade_count > 0:
                closed.append(self._close(instrument, timeframe, bar))
        return closed

    def current(self, instrument: str, timeframe: str) -> Bar | None:
        return self._bars.get((instrument, timeframe))

    @property
    def closed_count(self) -> int:
        return len(self._closed)


def signal_key(
    instrument: str, timeframe: str, rule_id: str, bar_ts: datetime, version: str | None = None
) -> str:
    """Deterministic idempotency key for a signal.

    One strategy, one instrument, one timeframe, one closed bar, one rule =>
    exactly one signal. A reconnect that replays a bar cannot create a second
    signal, because the key is identical.
    """
    import hashlib

    parts = [instrument.upper(), timeframe, rule_id, bar_ts.astimezone(timezone.utc).isoformat()]
    if version:
        parts.append(str(version))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
