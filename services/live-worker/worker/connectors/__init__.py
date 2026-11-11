"""Connector package.

Every connector here is READ-ONLY. The worker never places an order in v1:
the app is ticket-only, and broker write credentials do not exist yet.

A connector's job is to turn a venue's stream into normalised events:

    Trade(symbol, price, size, ts, side)

and to expose its own health so the heartbeat can report lag.
"""

from .binance import BinanceStream, Trade, fetch_klines  # noqa: F401

__all__ = ["BinanceStream", "Trade", "fetch_klines"]
