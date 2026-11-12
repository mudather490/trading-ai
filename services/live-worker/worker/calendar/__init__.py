"""Economic calendar.

The point of a calendar is the clock. A setup taken twenty minutes before a
high-impact release is a different trade from the same setup taken at any
other time, and no amount of chart reading tells you which one you are
looking at.

The calendar turns "the market might move" into "the market moves at 13:30
UTC, and the consensus expects 180k".

Events are published as **catalysts**, one row per affected instrument. The
ticket function already turns a catalyst into a warning, so this feeds the
existing decision path without changing it — and, as with all news, it can
never change a size (I-6).
"""

from __future__ import annotations

from .finnhub import (
    COUNTRY_CURRENCY,
    IMPACT_TO_SEVERITY,
    fetch_events,
    parse_events,
)
from .planner import build_catalysts, currencies_for

__all__ = [
    "COUNTRY_CURRENCY",
    "IMPACT_TO_SEVERITY",
    "build_catalysts",
    "currencies_for",
    "fetch_events",
    "parse_events",
]
