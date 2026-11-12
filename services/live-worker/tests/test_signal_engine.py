"""The live signal engine.

The property under test is not "a strategy makes money". It is that the
worker evaluates a CLOSED bar, once, through the same strategy package the
backtester uses, and writes through the idempotent RPC — or writes nothing.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.signals import SignalEngine  # noqa: E402


def _bars(closes: list[float], timeframe: str = "1d") -> list[dict]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    step = timedelta(days=1) if timeframe == "1d" else timedelta(hours=1)
    rows = []
    for i, close in enumerate(closes):
        rows.append(
            {
                "ts": (start + step * i).isoformat(),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000.0,
            }
        )
    return rows


def _bar(row: dict):
    from types import SimpleNamespace

    return SimpleNamespace(
        ts=datetime.fromisoformat(row["ts"]),
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        timeframe="1d",
    )


class FakeWriter:
    def __init__(self, bars, champions, owner="owner-1"):
        self._bars = bars
        self._champions = champions
        self.owner = owner
        self.signals: list[dict] = []
        self.alerts: list[dict] = []

    def fetch_bars(self, instrument_id, timeframe, limit=400):
        return self._bars[-limit:]

    def fetch_active_champions(self):
        return self._champions

    def fetch_owner_id(self):
        return self.owner

    def record_signal(self, payload):
        self.signals.append(payload)
        return f"signal-{len(self.signals)}"

    def fire_alert(self, **kwargs):
        self.alerts.append(kwargs)
        return f"alert-{len(self.alerts)}"


ROTATION = {
    "asset_class": "us_equity",
    "timeframe": "1d",
    "version_id": "version-1",
    "version": 1,
    "template_id": "rotation_ma",
    "strategy_name": "S18 Rotation",
    "params": {"ma_period": 200, "risk_symbol": "QQQ", "defensive_symbol": "GLD"},
    "risk_spec": {},
}


def _engine(bars, champions):
    writer = FakeWriter(bars, champions)
    engine = SignalEngine(writer)
    engine.refresh_registry()
    return engine, writer


def test_no_champion_writes_nothing():
    bars = _bars([100.0 + i for i in range(260)])
    engine, writer = _engine(bars, [])
    assert engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=_bar(bars[-1]),
    ) is None
    assert writer.signals == []


def test_unknown_template_is_skipped_not_crashed():
    bars = _bars([100.0 + i for i in range(260)])
    engine, writer = _engine(bars, [{**ROTATION, "template_id": "does_not_exist"}])
    assert engine.binding_for("us_equity", "1d") is None
    assert engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=_bar(bars[-1]),
    ) is None
    assert writer.signals == []


def test_rotation_buy_is_recorded_with_a_stop():
    bars = _bars([100.0 + i for i in range(260)])
    engine, writer = _engine(bars, [ROTATION])

    result = engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=_bar(bars[-1]),
        data_quality="partial",
    )

    assert result is not None
    assert result["side"] == "buy"
    assert len(writer.signals) == 1
    payload = writer.signals[0]
    assert payload["p_side"] == "buy"
    assert payload["p_stop_price"] is not None
    assert payload["p_signal_key"]
    assert payload["p_version_id"] == "version-1"
    assert payload["p_bar_close_time"].startswith("2024-09-")
    # The alert carries the data-quality label; a partial feed must not look
    # consolidated in the Telegram card either.
    assert writer.alerts[0]["payload"]["data_quality"] == "partial"


def test_the_same_closed_bar_is_evaluated_once():
    bars = _bars([100.0 + i for i in range(260)])
    engine, writer = _engine(bars, [ROTATION])
    bar = _bar(bars[-1])

    first = engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=bar,
    )
    second = engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=bar,
    )

    assert first is not None
    assert second is None
    assert len(writer.signals) == 1
    assert len(writer.alerts) == 1


def test_flat_first_reading_is_not_recorded_as_a_signal():
    # A downtrend puts QQQ below its 200-day average, so the rotation holds
    # the defensive leg and QQQ is flat. A flat with no prior position is not
    # news, and must not fill the signals table.
    bars = _bars([400.0 - i for i in range(260)])
    engine, writer = _engine(bars, [ROTATION])

    result = engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=_bar(bars[-1]),
    )

    assert result is None
    assert writer.signals == []
    assert writer.alerts == []


def test_signal_key_is_deterministic_across_restarts():
    bars = _bars([100.0 + i for i in range(260)])
    bar = _bar(bars[-1])

    engine_a, writer_a = _engine(bars, [ROTATION])
    engine_a.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=bar,
    )

    # A restart rebuilds the engine from the stored bars. The same closed bar
    # must produce the same key, so record_signal() can dedupe it.
    engine_b, writer_b = _engine(bars, [ROTATION])
    engine_b.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=bar,
    )

    assert writer_a.signals[0]["p_signal_key"] == writer_b.signals[0]["p_signal_key"]


def test_no_bars_writes_nothing():
    engine, writer = _engine([], [ROTATION])
    result = engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1d",
        bar=_bar(_bars([100.0])[-1]),
    )
    assert result is None
    assert writer.signals == []


def test_tactical_champion_on_a_missing_timeframe_does_nothing():
    bars = _bars([100.0 + i for i in range(260)])
    engine, writer = _engine(bars, [ROTATION])
    assert engine.evaluate(
        instrument_id="i-1",
        symbol="QQQ",
        asset_class="us_equity",
        timeframe="1h",
        bar=_bar(bars[-1]),
    ) is None
    assert writer.signals == []
