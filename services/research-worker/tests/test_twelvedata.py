"""The Twelve Data cross-check.

The rules this module obeys are the reason it is worth a test:

  * the free plan's data may not be DISPLAYED, so nothing here writes to a
    table the app shows,
  * the daily credit allowance is finite, so the budget refuses before the
    provider does,
  * the provider answers HTTP 200 with an explanation when it refuses, so
    a status check alone is not enough.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from research.twelvedata import (  # noqa: E402
    CreditBudget,
    TwelveDataError,
    compare_series,
    fetch_daily_bars,
)


class _Handler(BaseHTTPRequestHandler):
    payload: dict = {}
    status: int = 200

    def do_GET(self):  # noqa: N802
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        return


def _serve(payload: dict, status: int = 200):
    handler = type("Handler", (_Handler,), {"payload": payload, "status": status})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _day(offset: int) -> str:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return (base + timedelta(days=offset)).strftime("%Y-%m-%d")


def test_the_budget_refuses_when_the_allowance_is_spent():
    budget = CreditBudget(limit=3)
    assert budget.take() is True
    assert budget.take() is True
    assert budget.take() is True
    assert budget.take() is False, "the fourth call must be refused"
    assert budget.remaining() == 0


def test_the_budget_rolls_over_on_a_new_day():
    budget = CreditBudget(limit=1)
    assert budget.take() is True
    assert budget.take() is False
    budget.day = budget.day - timedelta(days=1)
    assert budget.remaining() == 1


def test_a_missing_key_is_refused_before_any_request():
    with pytest.raises(TwelveDataError):
        fetch_daily_bars("AAPL", api_key="")


def test_the_bars_are_parsed_and_sorted_oldest_first():
    payload = {
        "meta": {"symbol": "AAPL", "interval": "1day"},
        "values": [
            {"datetime": _day(2), "open": "2", "high": "3", "low": "1", "close": "2.5", "volume": "10"},
            {"datetime": _day(1), "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "5"},
        ],
    }
    server, base = _serve(payload)
    try:
        bars = fetch_daily_bars(
            "AAPL", api_key="test-key", base_url=base, outputsize=2
        )
    finally:
        server.shutdown()

    assert len(bars) == 2
    assert bars[0].ts < bars[1].ts, "bars must be oldest first"
    assert bars[0].close == 1.5
    assert bars[1].close == 2.5


def test_a_refusal_with_status_200_is_still_a_refusal():
    """The provider answers 200 with an explanation. A status check is not
    enough."""
    server, base = _serve({"code": 404, "message": "symbol not found"})
    try:
        with pytest.raises(TwelveDataError) as excinfo:
            fetch_daily_bars("NOPE", api_key="test-key", base_url=base)
    finally:
        server.shutdown()

    assert "symbol not found" in str(excinfo.value)


def test_a_rate_limit_is_reported_clearly():
    server, base = _serve({"message": "too many requests"}, status=429)
    try:
        with pytest.raises(TwelveDataError) as excinfo:
            fetch_daily_bars("AAPL", api_key="test-key", base_url=base)
    finally:
        server.shutdown()

    assert "429" in str(excinfo.value)


def test_one_malformed_row_does_not_lose_the_others():
    payload = {
        "meta": {"symbol": "AAPL"},
        "values": [
            {"datetime": _day(1), "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
            {"datetime": _day(2), "open": "nope", "high": "3", "low": "1", "close": "2.5"},
        ],
    }
    server, base = _serve(payload)
    try:
        bars = fetch_daily_bars("AAPL", api_key="test-key", base_url=base)
    finally:
        server.shutdown()

    assert len(bars) == 1


def test_two_series_that_agree_are_within_tolerance():
    from research.twelvedata import DailyBar

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    left = [
        DailyBar(base + timedelta(days=i), 100, 101, 99, 100.0, 10) for i in range(5)
    ]
    right = [
        DailyBar(base + timedelta(days=i), 100, 101, 99, 100.05, 10) for i in range(5)
    ]

    result = compare_series(left, right)
    assert result["days"] == 5
    assert result["within_tolerance"] is True


def test_two_series_that_disagree_are_flagged():
    from research.twelvedata import DailyBar

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    left = [
        DailyBar(base + timedelta(days=i), 100, 101, 99, 100.0, 10) for i in range(5)
    ]
    right = [
        DailyBar(base + timedelta(days=i), 100, 101, 99, 105.0, 10) for i in range(5)
    ]

    result = compare_series(left, right)
    assert result["within_tolerance"] is False
    assert result["close_median_abs_error_pct"] > 0.1


def test_two_series_with_no_shared_day_are_reported():
    from research.twelvedata import DailyBar

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    left = [DailyBar(base, 100, 101, 99, 100.0, 10)]
    right = [DailyBar(base + timedelta(days=400), 100, 101, 99, 100.0, 10)]

    result = compare_series(left, right)
    assert result["days"] == 0
    assert result["within_tolerance"] is False
