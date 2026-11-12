"""Tests for the economic calendar.

What they prove:

  * A calendar payload becomes normalised events with a currency and a clock.
  * Events we cannot place on the clock, or whose country we do not know,
    are dropped rather than guessed at.
  * A USD release reaches every instrument that trades against the dollar,
    including USDT-quoted crypto and both sides of an FX pair.
  * A euro release reaches EURUSD and nothing else.
  * A release that has already happened produces no catalyst.
  * The worker REPLACES the forward window instead of appending to it.

Run:  python -m pytest services/live-worker/tests -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.calendar import build_catalysts, currencies_for, parse_events  # noqa: E402

T0 = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)

INSTRUMENTS = [
    {"id": "id-qqq", "symbol": "QQQ", "venue": "alpaca", "currency": "USD"},
    {"id": "id-btc", "symbol": "BTCUSDT", "venue": "binance", "currency": "USDT"},
    {"id": "id-eurusd", "symbol": "EURUSD", "venue": "oanda", "currency": "USD"},
    {"id": "id-gbpusd", "symbol": "GBPUSD", "venue": "oanda", "currency": "USD"},
]

OANDA_SYMBOLS = ("EUR_USD", "GBP_USD", "XAU_USD")


def payload(*events: dict) -> dict:
    return {"economicCalendar": list(events)}


def usd_event(**overrides) -> dict:
    event = {
        "country": "US",
        "event": "Non-Farm Payrolls",
        "impact": "high",
        "estimate": 180,
        "actual": 190,
        "prev": 175,
        "time": "2026-01-05T13:30:00",
    }
    event.update(overrides)
    return event


# ---------------------------------------------------------------- parsing --


def test_a_release_becomes_an_event_with_a_clock_and_a_currency():
    events = parse_events(payload(usd_event()))
    assert len(events) == 1
    event = events[0]
    assert event["currency"] == "USD"
    assert event["title"] == "Non-Farm Payrolls"
    assert event["severity"] == "high"
    assert event["forecast"] == 180.0
    assert event["previous"] == 175.0
    assert event["event_time"] == datetime(2026, 1, 5, 13, 30, tzinfo=timezone.utc)
    assert event["event_time"].tzinfo is not None, "a naive time must not stay naive"


def test_a_release_from_an_unknown_country_is_dropped():
    events = parse_events(payload(usd_event(country="ZZ")))
    assert events == []


def test_a_release_without_a_time_is_dropped():
    events = parse_events(payload(usd_event(time="")))
    assert events == []


def test_events_come_back_in_time_order():
    events = parse_events(
        payload(
            usd_event(event="Later", time="2026-01-07T13:30:00"),
            usd_event(event="Sooner", time="2026-01-05T13:30:00"),
        )
    )
    assert [e["title"] for e in events] == ["Sooner", "Later"]


def test_an_unexpected_payload_is_not_an_error():
    assert parse_events({}) == []
    assert parse_events({"economicCalendar": None}) == []
    assert parse_events({"economicCalendar": ["nonsense"]}) == []


# --------------------------------------------------------------- planning --


def test_the_currencies_that_move_an_instrument():
    assert currencies_for(INSTRUMENTS[0]) == {"USD"}
    assert currencies_for(INSTRUMENTS[1]) == {"USD"}, "USDT tracks the dollar"
    assert currencies_for(INSTRUMENTS[2], OANDA_SYMBOLS) == {"EUR", "USD"}
    assert currencies_for(INSTRUMENTS[3], OANDA_SYMBOLS) == {"GBP", "USD"}


def test_a_dollar_release_reaches_everything_that_trades_the_dollar():
    events = parse_events(payload(usd_event()))
    rows = build_catalysts(events, INSTRUMENTS, OANDA_SYMBOLS, now=T0)

    instrument_ids = {row["instrument_id"] for row in rows}
    assert instrument_ids == {"id-qqq", "id-btc", "id-eurusd", "id-gbpusd"}
    assert all(row["kind"] == "economic" for row in rows)
    assert all(row["severity"] == "high" for row in rows)


def test_a_euro_release_reaches_only_the_euro_pair():
    events = parse_events(payload(usd_event(country="DE", event="German ZEW")))
    rows = build_catalysts(events, INSTRUMENTS, OANDA_SYMBOLS, now=T0)
    assert {row["instrument_id"] for row in rows} == {"id-eurusd"}


def test_a_release_that_already_happened_produces_nothing():
    events = parse_events(payload(usd_event(time="2020-01-05T13:30:00")))
    rows = build_catalysts(events, INSTRUMENTS, OANDA_SYMBOLS, now=T0)
    assert rows == []


def test_a_catalyst_carries_the_numbers_it_was_built_from():
    events = parse_events(payload(usd_event()))
    rows = build_catalysts(events, INSTRUMENTS, OANDA_SYMBOLS, now=T0)
    metadata = rows[0]["metadata"]
    assert metadata["forecast"] == 180.0
    assert metadata["actual"] == 190.0
    assert metadata["currency"] == "USD"
    assert metadata["source_id"].startswith("US:")


# ------------------------------------------------------------ the worker --


class FakeWriter:
    def __init__(self) -> None:
        self.catalysts: list[dict] = []
        self.deleted: list[tuple[str, object]] = []

    def insert_catalysts(self, rows):
        self.catalysts.extend(rows)

    def delete_catalysts(self, kind, from_time):
        self.deleted.append((kind, from_time))

    def fetch_instruments(self):
        return [
            {
                "id": "id-eurusd",
                "symbol": "EURUSD",
                "venue": "oanda",
                "asset_class": "forex",
                "in_universe": True,
                "primary_source": "oanda_practice",
                "data_quality": "indicative",
                "currency": "USD",
            }
        ]


@pytest.mark.asyncio
async def test_the_worker_replaces_the_calendar_window(monkeypatch):
    import worker.main as main_module

    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    # A release tomorrow: the worker only publishes what is still ahead.
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    events = parse_events(payload(usd_event(time=tomorrow.strftime("%Y-%m-%dT13:30:00"))))
    monkeypatch.setattr(main_module, "fetch_events", lambda *a, **k: events)

    from worker.config import (
        AppConfig,
        OandaConfig,
        SupabaseConfig,
        WorkerConfig,
    )
    from worker.main import LiveWorker

    config = AppConfig(
        supabase=SupabaseConfig(url="http://localhost", service_role_key="k"),
        worker=WorkerConfig(worker_id="w"),
        binance=None,
        alpaca=None,
        oanda=OandaConfig(),
        enabled_venues=(),
    )
    worker = LiveWorker(config)
    worker.writer = FakeWriter()
    worker.instrument_ids = {"EURUSD": "id-eurusd"}
    worker.instrument_meta = {"EURUSD": INSTRUMENTS[2]}

    await worker._refresh_calendar()

    assert worker.writer.deleted, "the window must be replaced, not appended to"
    kind, _from = worker.writer.deleted[0]
    assert kind == "economic"
    assert len(worker.writer.catalysts) == 1
    assert worker.writer.catalysts[0]["instrument_id"] == "id-eurusd"


@pytest.mark.asyncio
async def test_no_api_key_means_no_catalysts_and_no_error(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)

    from worker.config import AppConfig, SupabaseConfig, WorkerConfig
    from worker.main import LiveWorker

    config = AppConfig(
        supabase=SupabaseConfig(url="http://localhost", service_role_key="k"),
        worker=WorkerConfig(worker_id="w"),
        binance=None,
        alpaca=None,
        oanda=None,
        enabled_venues=(),
    )
    worker = LiveWorker(config)
    worker.writer = FakeWriter()

    await worker._refresh_calendar()

    assert worker.writer.catalysts == []
    assert worker.writer.deleted == [], "nothing to replace means nothing to delete"
