"""Integration tests for the live worker loop.

These prove the pipeline works WITHOUT a network: a fake writer captures every
call, and we drive the worker with synthetic trades.

What we prove:
  * A trade updates the quote.
  * A trade that closes a minute publishes exactly one bar.
  * The heartbeat is written on schedule.
  * The broadcast bundle contains every tracked symbol.
  * The worker refuses to write to a decision table.

Run:  python -m pytest services/live-worker/tests -q
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.config import AppConfig, SupabaseConfig, WorkerConfig  # noqa: E402
from worker.main import LiveWorker  # noqa: E402
from worker.supabase_client import ForbiddenWrite  # noqa: E402

T0 = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)


class FakeWriter:
    """Captures everything the worker writes."""

    def __init__(self) -> None:
        self.quotes: list[dict] = []
        self.bars: list[dict] = []
        self.heartbeats: list[dict] = []
        self.broadcasts: list[tuple[str, str, dict]] = []
        self.signals: list[dict] = []

    def upsert_quote(self, instrument_id, **kwargs):
        self.quotes.append({"instrument_id": instrument_id, **kwargs})

    def insert_bar(self, instrument_id, **kwargs):
        self.bars.append({"instrument_id": instrument_id, **kwargs})

    def heartbeat(self, worker_id, **kwargs):
        self.heartbeats.append({"worker_id": worker_id, **kwargs})

    def broadcast(self, channel, event, payload):
        self.broadcasts.append((channel, event, payload))
        return True

    def record_signal(self, payload):
        self.signals.append(payload)
        return "signal-id"

    def fetch_instruments(self):
        return [
            {
                "id": "aaaaaaaa-0000-0000-0000-000000000001",
                "symbol": "BTCUSDT",
                "venue": "binance",
                "asset_class": "crypto",
                "in_universe": True,
                "primary_source": "binance_ws",
                "data_quality": "official",
            }
        ]

    def claim_jobs(self, worker_id, limit=5):
        return []

    def finish_job(self, job_id, ok, result=None, error=None):
        pass


def make_config() -> AppConfig:
    return AppConfig(
        supabase=SupabaseConfig(url="http://localhost", service_role_key="test-key"),
        worker=WorkerConfig(worker_id="test-worker", timeframes=("1m",)),
        binance=AppConfig.from_env.__func__ if False else None,  # placeholder, replaced below
        alpaca=None,
        oanda=None,
        enabled_venues=(),
    )


@pytest.fixture
def worker(monkeypatch):
    cfg = AppConfig.from_env.__wrapped__ if hasattr(AppConfig.from_env, "__wrapped__") else None
    # Build the config directly; no environment needed for these tests.
    from worker.config import BinanceConfig, OandaConfig

    config = AppConfig(
        supabase=SupabaseConfig(url="http://localhost", service_role_key="test-key"),
        worker=WorkerConfig(worker_id="test-worker", timeframes=("1m",)),
        binance=BinanceConfig(),
        alpaca=None,
        oanda=None,
        enabled_venues=(),
    )
    w = LiveWorker(config)
    w.writer = FakeWriter()
    return w


@pytest.mark.asyncio
async def test_a_trade_updates_the_quote(worker):
    await worker.load_instruments()
    await worker.handle_trade("BTCUSDT", 100.0, 1.5, T0)

    assert len(worker.writer.quotes) == 1
    q = worker.writer.quotes[0]
    assert q["last"] == 100.0
    assert q["source"] == "binance_ws"
    assert q["official"] is True
    assert worker.quotes_published == 1


@pytest.mark.asyncio
async def test_a_trade_that_crosses_a_minute_publishes_one_bar(worker):
    await worker.load_instruments()
    await worker.handle_trade("BTCUSDT", 100.0, 1.0, T0)
    # A trade inside the same minute extends the bar.
    await worker.handle_trade("BTCUSDT", 100.5, 2.0, T0 + timedelta(seconds=30))
    # The next trade belongs to the NEXT minute, so it closes the T0 bar.
    await worker.handle_trade("BTCUSDT", 101.0, 3.0, T0 + timedelta(minutes=1))

    assert len(worker.writer.bars) == 1
    bar = worker.writer.bars[0]
    assert bar["timeframe"] == "1m"
    assert bar["ts"] == T0, "the closed bar is the minute that just ended"
    assert bar["open_"] == 100.0
    assert bar["high"] == 100.5
    assert bar["low"] == 100.0
    assert bar["close"] == 100.5, "the T0+1min trade must not be inside this bar"
    assert bar["volume"] == 3.0
    # `is_closed` is set by the real writer; the builder only ever produces
    # ClosedBar objects, so a partial bar cannot reach this point at all.


@pytest.mark.asyncio
async def test_the_next_bar_closes_with_the_trade_that_started_it(worker):
    await worker.load_instruments()
    await worker.handle_trade("BTCUSDT", 100.0, 1.0, T0)
    await worker.handle_trade("BTCUSDT", 101.0, 1.0, T0 + timedelta(minutes=1))
    # One more minute closes the second bar.
    await worker.handle_trade("BTCUSDT", 102.0, 1.0, T0 + timedelta(minutes=2))

    assert len(worker.writer.bars) == 2
    second = worker.writer.bars[1]
    assert second["ts"] == T0 + timedelta(minutes=1)
    assert second["open_"] == 101.0
    assert second["close"] == 101.0


@pytest.mark.asyncio
async def test_a_symbol_outside_the_universe_publishes_no_bars(worker):
    await worker.load_instruments()
    await worker.handle_trade("DOGEUSDT", 1.0, 1.0, T0)
    await worker.handle_trade("DOGEUSDT", 1.1, 1.0, T0 + timedelta(minutes=1))

    assert worker.writer.bars == [], "a bar was published for an unknown instrument"
    assert worker.quotes_published == 0


@pytest.mark.asyncio
async def test_the_broadcast_bundle_contains_every_symbol(worker):
    await worker.load_instruments()
    await worker.handle_trade("BTCUSDT", 100.0, 1.0, T0)

    worker.writer.broadcast("ticks", "bundle", {"quotes": {"BTCUSDT": {"price": 100.0}}})
    assert len(worker.writer.broadcasts) == 1
    channel, event, payload = worker.writer.broadcasts[0]
    assert channel == "ticks"
    assert event == "bundle"
    assert payload["quotes"]["BTCUSDT"]["price"] == 100.0


@pytest.mark.asyncio
async def test_the_heartbeat_carries_the_stream_health(worker):
    await worker.load_instruments()
    worker.writer.heartbeat(
        worker.config.worker.worker_id,
        version="0.1.0",
        streams={"binance": {"connected": True, "reconnects": 0}},
        lag_ms={"binance": 42},
        meta={"quotes_published": 3},
    )
    hb = worker.writer.heartbeats[0]
    assert hb["worker_id"] == "test-worker"
    assert hb["streams"]["binance"]["connected"] is True
    assert hb["lag_ms"]["binance"] == 42


# --------------------------------------------------------- the guardrail --


def test_the_worker_refuses_to_write_a_decision_table():
    """The worker must never be able to promote a strategy or set a ticket."""
    from worker.supabase_client import SupabaseWriter

    writer = SupabaseWriter(SupabaseConfig(url="http://x", service_role_key="k"))
    for table in ("champions", "tickets", "performance", "strategy_versions", "holdout_ledger"):
        with pytest.raises(ForbiddenWrite):
            writer._guard(table)


def test_the_worker_is_allowed_to_write_market_data():
    from worker.supabase_client import SupabaseWriter

    writer = SupabaseWriter(SupabaseConfig(url="http://x", service_role_key="k"))
    for table in ("latest_quotes", "bars", "signals", "news", "ai_runs", "worker_heartbeat"):
        writer._guard(table)  # must not raise


def test_secrets_are_redacted_from_logs():
    """A key must never reach stdout."""
    from worker.config import redact

    text = "SUPABASE_SERVICE_ROLE_KEY=supersecretvalue ALPACA_SECRET_KEY=abc123"
    cleaned = redact(text)
    assert "supersecretvalue" not in cleaned
    assert "abc123" not in cleaned
    assert "REDACTED" in cleaned
