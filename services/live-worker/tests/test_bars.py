"""Tests for the bar builder and the signal idempotency key.

These are the properties the rest of the system trusts blindly:

  * A bar closes exactly once.
  * A duplicate trade never produces a second bar.
  * A signal key is stable for the same (instrument, timeframe, rule, bar).
  * Different inputs produce different keys.

Run:  python -m pytest services/live-worker/tests -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.bars import BarBuilder, bucket_start, signal_key  # noqa: E402

T0 = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)


def test_bucket_start_truncates_to_the_minute():
    ts = T0 + timedelta(seconds=37, milliseconds=500)
    assert bucket_start(ts, "1m") == T0


def test_bucket_start_truncates_to_the_hour():
    ts = T0 + timedelta(minutes=42)
    assert bucket_start(ts, "1h") == T0.replace(minute=0, second=0)


def test_bucket_start_truncates_to_the_day():
    ts = T0 + timedelta(hours=5)
    assert bucket_start(ts, "1d") == T0.replace(hour=0, minute=0, second=0)


def test_a_bar_closes_when_the_next_bucket_starts():
    b = BarBuilder()
    timeframes = ("1m",)

    b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    b.on_trade("BTCUSDT", 101.0, 2.0, T0 + timedelta(seconds=30), timeframes)
    closed = b.on_trade("BTCUSDT", 102.0, 1.0, T0 + timedelta(minutes=1), timeframes)

    assert len(closed) == 1
    bar = closed[0]
    assert bar.ts == T0
    assert bar.open == 100.0
    assert bar.high == 101.0
    assert bar.low == 100.0
    assert bar.close == 101.0
    assert bar.volume == 3.0


def test_a_bar_closes_only_once_even_with_duplicate_trades():
    b = BarBuilder()
    timeframes = ("1m",)

    # The same trade arrives twice (a reconnect replay), then the next minute's
    # trade also arrives twice. Across the whole sequence the bar must close
    # exactly once.
    closed = []
    closed += b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    closed += b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    closed += b.on_trade("BTCUSDT", 101.0, 1.0, T0 + timedelta(minutes=1), timeframes)
    closed += b.on_trade("BTCUSDT", 101.0, 1.0, T0 + timedelta(minutes=1), timeframes)

    assert len(closed) == 1, f"expected exactly one close, got {len(closed)}"
    assert closed[0].ts == T0
    assert closed[0].volume == 2.0, "the duplicate trade inflated the volume"


def test_a_late_trade_for_a_closed_bucket_is_ignored():
    b = BarBuilder()
    timeframes = ("1m",)

    b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    b.on_trade("BTCUSDT", 105.0, 1.0, T0 + timedelta(minutes=2), timeframes)

    # A straggler from the first minute arrives late.
    closed = b.on_trade("BTCUSDT", 99.0, 1.0, T0 + timedelta(seconds=5), timeframes)

    assert closed == [], "a late trade reopened a closed bar"


def test_multiple_timeframes_close_independently():
    b = BarBuilder()
    timeframes = ("1m", "5m")

    b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    closed = b.on_trade("BTCUSDT", 101.0, 1.0, T0 + timedelta(minutes=1), timeframes)

    frames = {c.timeframe for c in closed}
    assert frames == {"1m"}, "the 5m bar must not close after one minute"


def test_the_five_minute_bar_closes_at_the_five_minute_boundary():
    b = BarBuilder()
    timeframes = ("1m", "5m")

    b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    # The trade at 12:05 belongs to the NEXT 5m bucket, so it closes the
    # 12:00 one.
    closed = b.on_trade("BTCUSDT", 101.0, 1.0, T0 + timedelta(minutes=5), timeframes)

    frames = {c.timeframe for c in closed}
    assert "5m" in frames, f"the 5m bar did not close: {frames}"
    five = next(c for c in closed if c.timeframe == "5m")
    assert five.ts == T0
    assert five.close == 100.0, "the 5m bar must not include the 12:05 trade"


def test_instruments_do_not_share_bars():
    b = BarBuilder()
    timeframes = ("1m",)

    b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    b.on_trade("ETHUSDT", 50.0, 1.0, T0, timeframes)
    closed = b.on_trade("BTCUSDT", 101.0, 1.0, T0 + timedelta(minutes=1), timeframes)

    assert len(closed) == 1
    assert closed[0].instrument == "BTCUSDT"
    assert closed[0].open == 100.0


def test_flush_closes_an_in_progress_bar():
    b = BarBuilder()
    timeframes = ("1m",)

    b.on_trade("BTCUSDT", 100.0, 1.0, T0, timeframes)
    closed = b.flush("BTCUSDT", timeframes)

    assert len(closed) == 1
    assert closed[0].close == 100.0


def test_flush_with_no_trades_closes_nothing():
    b = BarBuilder()
    assert b.flush("BTCUSDT", ("1m",)) == []


# --------------------------------------------------------- signal keys ----


def test_signal_key_is_stable_for_the_same_inputs():
    a = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v3")
    b = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v3")
    assert a == b


def test_signal_key_changes_with_the_bar():
    a = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v3")
    b = signal_key("BTCUSDT", "1h", "ema_cross", T0 + timedelta(hours=1), "v3")
    assert a != b


def test_signal_key_changes_with_the_rule():
    a = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v3")
    b = signal_key("BTCUSDT", "1h", "donchian", T0, "v3")
    assert a != b


def test_signal_key_changes_with_the_strategy_version():
    a = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v3")
    b = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v4")
    assert a != b


def test_signal_key_normalises_the_symbol_case():
    a = signal_key("btcusdt", "1h", "ema_cross", T0, "v3")
    b = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v3")
    assert a == b


def test_signal_key_is_deterministic_across_processes():
    # The key must not depend on PYTHONHASHSEED or dict ordering.
    a = signal_key("BTCUSDT", "1h", "ema_cross", T0, "v3")
    assert len(a) == 32
    assert all(c in "0123456789abcdef" for c in a)
