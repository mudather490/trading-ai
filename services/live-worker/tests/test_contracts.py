"""The provider contract check.

The check exists to notice that a provider changed what it returns. These
tests are that notice: a changed shape must be DETECTED, and a healthy
provider must not cry wolf.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.contracts import (  # noqa: E402
    ContractCheck,
    compare_with_previous,
    key_set_hash,
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_the_hash_ignores_the_order_of_the_keys():
    assert key_set_hash(["b", "a"]) == key_set_hash(["a", "b"])


def test_the_hash_changes_when_a_key_changes():
    assert key_set_hash(["symbol", "price"]) != key_set_hash(["symbol", "last"])


def test_a_healthy_provider_passes():
    from worker.contracts import _check

    server, base = _serve({"symbol": "BTCUSDT", "price": "73420.10"})
    try:
        check = _check(
            "binance", "GET /ticker", f"{base}/ticker", required_keys=("symbol", "price")
        )
    finally:
        server.shutdown()

    assert check.ok is True
    assert check.keys == ["price", "symbol"]
    assert check.schema_hash == key_set_hash(["price", "symbol"])
    assert check.sample["price"] == "73420.10"
    assert check.note is None


def test_a_provider_that_returns_an_error_fails():
    from worker.contracts import _check

    server, base = _serve({"error": "nope"}, status=503)
    try:
        check = _check("binance", "GET /ticker", f"{base}/ticker")
    finally:
        server.shutdown()

    assert check.ok is False
    assert "503" in (check.note or "")


def test_a_missing_required_key_fails():
    from worker.contracts import _check

    server, base = _serve({"symbol": "BTCUSDT"})
    try:
        check = _check(
            "binance",
            "GET /ticker",
            f"{base}/ticker",
            required_keys=("symbol", "price"),
        )
    finally:
        server.shutdown()

    assert check.ok is False
    assert "price" in (check.note or "")


def test_a_changed_shape_is_detected():
    """The whole point of the check."""
    previous = {
        "schema_hash": key_set_hash(["symbol", "price"]),
        "keys": ["symbol", "price"],
    }
    check = ContractCheck(
        provider="binance",
        endpoint="GET /ticker",
        schema_hash=key_set_hash(["symbol", "lastPrice"]),
        keys=["symbol", "lastPrice"],
        ok=True,
    )

    compared = compare_with_previous(check, previous)

    assert compared.ok is False
    assert "changed" in (compared.note or "")
    assert "lastPrice" in (compared.note or "")


def test_an_unchanged_shape_stays_ok():
    previous = {
        "schema_hash": key_set_hash(["symbol", "price"]),
        "keys": ["symbol", "price"],
    }
    check = ContractCheck(
        provider="binance",
        endpoint="GET /ticker",
        schema_hash=key_set_hash(["price", "symbol"]),
        keys=["price", "symbol"],
        ok=True,
    )

    assert compare_with_previous(check, previous).ok is True


def test_the_first_check_has_nothing_to_compare_against():
    check = ContractCheck(
        provider="binance",
        endpoint="GET /ticker",
        schema_hash=key_set_hash(["symbol"]),
        keys=["symbol"],
        ok=True,
    )
    assert compare_with_previous(check, None).ok is True


def test_an_unreachable_provider_is_a_failure_not_a_crash():
    from worker.contracts import check_binance

    # Port 1 is not going to answer.
    check = check_binance("http://127.0.0.1:1")
    assert check.ok is False
    assert check.keys == []
