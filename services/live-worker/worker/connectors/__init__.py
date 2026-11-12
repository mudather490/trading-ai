"""Connector package.

Every connector here is READ-ONLY. The worker never places an order in v1:
the app is ticket-only, and broker write credentials do not exist yet.

A connector's job is to turn a venue's stream into normalised events:

    Trade(symbol, price, size, ts, side, venue, bid, ask)

and to expose its own health so the heartbeat can report lag.
"""

from .alpaca import AlpacaStream
from .base import Stream, Trade, canonical_symbol, reconnect_delay
from .binance import BinanceStream, fetch_klines
from .oanda import OandaStream

__all__ = [
    "AlpacaStream",
    "BinanceStream",
    "OandaStream",
    "Stream",
    "Trade",
    "canonical_symbol",
    "fetch_klines",
    "reconnect_delay",
]
