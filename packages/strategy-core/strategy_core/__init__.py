"""Trading Command Center — shared strategy core.

Used by BOTH the live worker and the research worker. One code path means a
backtest and a live run cannot disagree about what a strategy does.

Import as `strategy_core`.
"""

from .interface import Bar, Context, Position, Signal, Strategy  # noqa: F401

__version__ = "0.1.0"

__all__ = ["Bar", "Context", "Position", "Signal", "Strategy"]
