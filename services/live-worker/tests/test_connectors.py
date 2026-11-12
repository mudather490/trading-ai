"""Tests for the multi-venue connectors.

These are parser and wiring tests, not network tests. They prove:

  * Alpaca trade frames become normalised events; control frames are ignored.
  * OANDA price lines become a mid-price with a real bid/ask; heartbeats and
    non-tradeable prices are refused.
  * Nanosecond timestamps are parsed without crashing.
  * The worker maps OANDA's `EUR_USD` to the registry's `EURUSD`.
  * Every quote and bar carries the correct source and data-quality label.

Run:  python -m pytest services/live-worker/tests -q
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.config import (  # noqa: E402
    AlpacaConfig,
    AppConfig,
    BinanceConfig,
    OandaConfig,
    SupabaseConfig,
    WorkerConfig,
)
from worker.connectors import Trade  # noqa: E402
from worker.connectors.alpaca import AlpacaStream  # noqa: E402
from worker.connectors.base import parse_rfc3339  # noqa: E402
from worker.connectors.oanda import OandaStream  # noqa: E402
from worker.main import LiveWorker, canonical_symbol, source_label  # noqa: E402

T0 = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------- parsing --


def test_alpaca_trade_frame_becomes_a_trade():
    frame = json.dumps(
        [
            {
                "T": "t",
                "S": "QQQ",
                "p": 401.25,
                "s": 10,
                "t": "2026-01-05T14:30:00.123456789Z",
            }
        ]
    )
    trades = AlpacaStream.parse_messages(frame)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.symbol == "QQQ"
    assert trade.price == 401.25
    assert trade.size == 10.0
    assert trade.venue == "alpaca"
    assert trade.ts.microsecond == 123456


def test_alpaca_control_frames_are_ignored():
    frame = json.dumps(
        [
            {"T": "success", "msg": "authenticated"},
            {"T": "subscription", "trades": ["QQQ"]},
        ]
    )
    assert AlpacaStream.parse_messages(frame) == []


def test_oanda_price_line_becomes_a_mid_price():
    line = json.dumps(
        {
            "type": "PRICE",
            "instrument": "EUR_USD",
            "time": "2026-01-05T14:30:00.123456789Z",
            "bids": [{"price": "1.10000", "liquidity": 1000000}],
            "asks": [{"price": "1.10020", "liquidity": 1000000}],
            "tradeable": True,
        }
    )
    trade = OandaStream.parse_line(line)
    assert trade is not None
    assert trade.symbol == "EUR_USD"
    assert trade.bid == 1.10
    assert trade.ask == 1.1002
    assert trade.price == pytest.approx(1.1001)
    assert trade.venue == "oanda"


def test_oanda_heartbeat_is_not_a_trade():
    line = json.dumps({"type": "HEARTBEAT", "time": "2026-01-05T14:30:00Z"})
    assert OandaStream.parse_line(line) is None


def test_oanda_non_tradeable_price_is_refused():
    line = json.dumps(
        {
            "type": "PRICE",
            "instrument": "EUR_USD",
            "time": "2026-01-05T14:30:00Z",
            "bids": [{"price": "1.1000"}],
            "asks": [{"price": "1.1002"}],
            "tradeable": False,
        }
    )
    assert OandaStream.parse_line(line) is None


def test_oanda_malformed_line_is_refused():
    assert OandaStream.parse_line("not json") is None
    assert OandaStream.parse_line("") is None


# ---------------------------------------------------------------- history --


def test_alpaca_bars_are_normalised_and_sorted():
    from worker.connectors.alpaca import parse_bars

    payload = {
        "bars": [
            {"t": "2026-01-05T14:31:00Z", "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 10},
            {"t": "2026-01-05T14:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 5},
        ]
    }
    bars = parse_bars(payload)
    assert len(bars) == 2
    assert bars[0]["ts"].minute == 30, "bars must be oldest first"
    assert bars[0]["open"] == 1
    assert bars[1]["close"] == 2.5
    assert bars[0]["volume"] == 5


def test_the_binance_history_fetcher_returns_bars_not_a_coroutine():
    """The backfill must return a list.

    `fetch_klines` was `async def` while it used blocking urllib, and the
    synchronous wrapper called it without awaiting. The result was a
    coroutine, and every Binance backfill failed with
    "'coroutine' object is not iterable" — silently, for every symbol and
    every timeframe.
    """
    import asyncio

    from worker.connectors import binance as binance_connector

    assert not asyncio.iscoroutinefunction(
        binance_connector.fetch_klines
    ), "fetch_klines uses blocking urllib; it must not be a coroutine"

    original = binance_connector.fetch_klines
    binance_connector.fetch_klines = lambda *args, **kwargs: [
        {
            "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
        }
    ]
    try:
        bars = binance_connector.fetch_bars("btcusdt", timeframe="1h", limit=5)
    finally:
        binance_connector.fetch_klines = original

    assert isinstance(bars, list)
    assert len(bars) == 1
    assert bars[0]["close"] == 1.5


def test_oanda_candles_are_normalised_and_sorted():
    from worker.connectors.oanda import parse_candles

    payload = {
        "candles": [
            {
                "time": "2026-01-05T14:31:00Z",
                "o": "1.2", "h": "1.3", "l": "1.1", "c": "1.25", "volume": 9,
            },
            {
                "time": "2026-01-05T14:30:00Z",
                "o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15", "volume": 4,
            },
        ]
    }
    bars = parse_candles(payload)
    assert len(bars) == 2
    assert bars[0]["ts"].minute == 30, "bars must be oldest first"
    assert bars[0]["open"] == 1.1
    assert bars[1]["close"] == 1.25


def test_an_empty_history_payload_is_not_an_error():
    from worker.connectors.alpaca import parse_bars
    from worker.connectors.oanda import parse_candles

    assert parse_bars({}) == []
    assert parse_bars({"bars": None}) == []
    assert parse_candles({}) == []
    assert parse_candles({"candles": []}) == []


def test_a_backfilled_bar_is_dated_by_its_close_not_its_open():
    worker = make_worker()
    row = worker._bar_row(
        "id-btc",
        "BTCUSDT",
        {"primary_source": "binance_ws", "data_quality": "official"},
        "1m",
        {
            "ts": datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        },
    )
    assert row["ts"].startswith("2026-01-05T14:30:00")
    assert row["as_of"].startswith("2026-01-05T14:31:00"), "as_of is when the bar closed"
    assert row["is_closed"] is True
    assert row["source"] == "binance_ws"
    assert row["official"] is True


def test_nanosecond_timestamp_is_truncated_not_rejected():
    parsed = parse_rfc3339("2026-01-05T14:30:00.987654321Z")
    assert parsed.year == 2026
    assert parsed.microsecond == 987654


# --------------------------------------------------------------- wiring --


class FakeWriter:
    def __init__(self) -> None:
        self.quotes: list[dict] = []
        self.bars: list[dict] = []

    def fetch_instruments(self):
        return [
            {
                "id": "id-btc",
                "symbol": "BTCUSDT",
                "venue": "binance",
                "asset_class": "crypto",
                "in_universe": True,
                "primary_source": "binance_ws",
                "data_quality": "official",
            },
            {
                "id": "id-qqq",
                "symbol": "QQQ",
                "venue": "alpaca",
                "asset_class": "us_equity",
                "in_universe": True,
                "primary_source": "alpaca_iex",
                "data_quality": "partial",
            },
            {
                "id": "id-eurusd",
                "symbol": "EURUSD",
                "venue": "oanda",
                "asset_class": "forex",
                "in_universe": True,
                "primary_source": "oanda_practice",
                "data_quality": "indicative",
            },
        ]

    def upsert_quote(self, instrument_id, **kwargs):
        self.quotes.append({"instrument_id": instrument_id, **kwargs})

    def insert_bar(self, instrument_id, **kwargs):
        self.bars.append({"instrument_id": instrument_id, **kwargs})


def make_worker() -> LiveWorker:
    config = AppConfig(
        supabase=SupabaseConfig(url="http://localhost", service_role_key="test-key"),
        worker=WorkerConfig(worker_id="test-worker", timeframes=("1m",)),
        binance=BinanceConfig(),
        alpaca=AlpacaConfig(),
        oanda=OandaConfig(),
        enabled_venues=(),
    )
    worker = LiveWorker(config)
    worker.writer = FakeWriter()
    return worker


def test_the_registry_symbol_is_canonicalised():
    assert canonical_symbol("EUR_USD") == "EURUSD"
    assert canonical_symbol("btcusdt") == "BTCUSDT"
    assert canonical_symbol("QQQ") == "QQQ"


def test_source_labels_are_venue_specific():
    assert source_label("binance") == "binance_ws"
    assert source_label("alpaca") == "alpaca_ws"
    assert source_label("oanda") == "oanda_stream"


@pytest.mark.asyncio
async def test_an_alpaca_quote_carries_partial_quality_and_spread():
    worker = make_worker()
    await worker.load_instruments()
    await worker.handle_event(
        Trade(
            symbol="QQQ",
            price=401.25,
            size=10.0,
            ts=T0,
            venue="alpaca",
            bid=401.24,
            ask=401.26,
        )
    )

    assert len(worker.writer.quotes) == 1
    quote = worker.writer.quotes[0]
    assert quote["instrument_id"] == "id-qqq"
    assert quote["source"] == "alpaca_ws"
    assert quote["official"] is False, "IEX is partial, never official"
    assert quote["bid"] == 401.24
    assert quote["ask"] == 401.26


@pytest.mark.asyncio
async def test_an_oanda_quote_is_mapped_and_labelled():
    worker = make_worker()
    await worker.load_instruments()
    await worker.handle_event(
        Trade(
            symbol="EUR_USD",
            price=1.1001,
            size=0.0,
            ts=T0,
            venue="oanda",
            bid=1.10,
            ask=1.1002,
        )
    )

    assert len(worker.writer.quotes) == 1
    quote = worker.writer.quotes[0]
    assert quote["instrument_id"] == "id-eurusd"
    assert quote["source"] == "oanda_stream"
    assert quote["official"] is False, "practice prices are indicative"
    assert quote["last"] == 1.1001


@pytest.mark.asyncio
async def test_bars_keep_the_venue_of_the_event_that_built_them():
    worker = make_worker()
    await worker.load_instruments()
    await worker.handle_event(
        Trade(symbol="EUR_USD", price=1.1000, size=0.0, ts=T0, venue="oanda")
    )
    await worker.handle_event(
        Trade(
            symbol="EUR_USD",
            price=1.1005,
            size=0.0,
            ts=T0 + timedelta(minutes=1),
            venue="oanda",
        )
    )

    assert len(worker.writer.bars) == 1
    bar = worker.writer.bars[0]
    assert bar["instrument_id"] == "id-eurusd"
    assert bar["source"] == "oanda_stream"
    assert bar["open_"] == 1.1000
    assert bar["close"] == 1.1000, "the next minute's price must not be in this bar"
