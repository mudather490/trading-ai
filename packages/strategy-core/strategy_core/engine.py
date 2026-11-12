"""The strategy engine.

Computes the indicators a strategy needs, builds a Context, and asks the
strategy for a Signal. Shared by the live worker and the research worker, so
a backtest and a live run cannot disagree about what a strategy sees.

The critical property: the engine only ever sees CLOSED bars. A bar in
progress is never passed to a strategy, because a strategy that can see a
forming bar will produce a signal that changes when the bar closes. That is
repainting, and it is the single most common way a backtest lies.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Sequence

from .indicators import adx, atr, donchian, ema, rsi, sma
from .interface import Bar, Context, Position, Signal, Strategy

log = logging.getLogger(__name__)


class Engine:
    """Holds the computed indicator series for one symbol and timeframe."""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        *,
        ma_periods: Sequence[int] = (50, 100, 200),
        ema_periods: Sequence[int] = (20, 50),
        rsi_periods: Sequence[int] = (2, 14),
        atr_periods: Sequence[int] = (14,),
        adx_periods: Sequence[int] = (14,),
        donchian_periods: Sequence[int] = (20, 55),
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.bars: list[Bar] = []
        self._indicators: dict[str, list[float | None]] = {}
        self._ma_periods = tuple(ma_periods)
        self._ema_periods = tuple(ema_periods)
        self._rsi_periods = tuple(rsi_periods)
        self._atr_periods = tuple(atr_periods)
        self._adx_periods = tuple(adx_periods)
        self._donchian_periods = tuple(donchian_periods)

    def add_bar(self, bar: Bar) -> None:
        """Append a CLOSED bar and recompute the indicators."""
        self.bars.append(bar)
        self._recompute()

    def add_bars(self, bars: Sequence[Bar]) -> None:
        """Append many bars and recompute ONCE.

        A backtest feeds bars one at a time, and recomputing every indicator
        on every bar is O(n) each time, which makes a five-year daily
        backtest take minutes instead of seconds. When the bars arrive in a
        batch, compute once at the end.
        """
        self.bars.extend(bars)
        self._recompute()

    def _recompute(self) -> None:
        closes = [b.close for b in self.bars]
        self._indicators = {}

        for period in self._ma_periods:
            self._indicators[f"ma_{period}"] = sma(closes, period)
        for period in self._ema_periods:
            self._indicators[f"ema_{period}"] = ema(closes, period)
        for period in self._rsi_periods:
            self._indicators[f"rsi_{period}"] = rsi(closes, period)
        for period in self._atr_periods:
            self._indicators[f"atr_{period}"] = atr(self.bars, period)
        for period in self._adx_periods:
            self._indicators[f"adx_{period}"] = adx(self.bars, period)

        if self._donchian_periods:
            # The engine keeps the LONGEST channel under a single key pair so
            # a strategy asking for a specific period gets the right one.
            for period in self._donchian_periods:
                upper, lower = donchian(self.bars, period)
                self._indicators[f"donchian_upper_{period}"] = upper
                self._indicators[f"donchian_lower_{period}"] = lower

            longest = max(self._donchian_periods)
            upper, lower = donchian(self.bars, longest)
            self._indicators["donchian_upper"] = upper
            self._indicators["donchian_lower"] = lower
            # NOTE: the period is stored separately, NOT in _indicators.
            # _indicators holds only sequences, because context_at() slices
            # every one of them.

    def context(
        self,
        params: Mapping[str, Any],
        position: Position | None = None,
        aux_bars: Mapping[str, Sequence[Bar]] | None = None,
        aux_indicators: Mapping[str, Sequence[float]] | None = None,
    ) -> Context:
        """Build a Context from the current state.

        `aux_indicators` keys are "<symbol>:<name>". For convenience the
        plain "<name>" of this engine's own indicators is also exposed.
        """
        indicators = dict(self._indicators)

        # A strategy asking for `donchian_upper` gets the channel for the
        # period it configured, not always the longest one.
        period = params.get("entry_period")
        if period and f"donchian_upper_{period}" in indicators:
            indicators["donchian_upper"] = indicators[f"donchian_upper_{period}"]
            indicators["donchian_lower"] = indicators[f"donchian_lower_{period}"]

        return Context(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bars=tuple(self.bars),
            position=position or Position(side="flat", entry_price=0.0, size=0.0),
            params=params,
            now=self.bars[-1].ts if self.bars else datetime.now(),
            indicators=indicators,
            aux_bars=dict(aux_bars or {}),
            aux_indicators=dict(aux_indicators or {}),
        )

    def context_at(
        self,
        index: int,
        params: Mapping[str, Any],
        position: Position | None = None,
        aux_bars: Mapping[str, Sequence[Bar]] | None = None,
        aux_indicators: Mapping[str, Sequence[float]] | None = None,
    ) -> Context:
        """Build the Context as of bar `index`.

        A backtest needs the context as it stood at each bar in the past.
        Slicing the already-computed indicator series is far cheaper than
        rebuilding the engine bar by bar.
        """
        cut = index + 1
        indicators = {
            name: series[:cut] for name, series in self._indicators.items()
        }

        period = params.get("entry_period")
        if period and f"donchian_upper_{period}" in indicators:
            indicators["donchian_upper"] = indicators[f"donchian_upper_{period}"]
            indicators["donchian_lower"] = indicators[f"donchian_lower_{period}"]

        aux_cut: dict[str, Sequence[Bar]] = {}
        for symbol, series in (aux_bars or {}).items():
            aux_cut[symbol] = series[:cut]

        aux_ind_cut: dict[str, Sequence[float]] = {}
        for key, series in (aux_indicators or {}).items():
            aux_ind_cut[key] = series[:cut]

        return Context(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bars=tuple(self.bars[:cut]),
            position=position or Position(side="flat", entry_price=0.0, size=0.0),
            params=params,
            now=self.bars[index].ts if 0 <= index < len(self.bars) else datetime.now(),
            indicators=indicators,
            aux_bars=aux_cut,
            aux_indicators=aux_ind_cut,
        )

    def evaluate(
        self,
        strategy: Strategy,
        position: Position | None = None,
        aux: Mapping[str, Any] | None = None,
    ) -> Signal | None:
        """Ask the strategy for a signal on the latest closed bar."""
        ctx = self.context(
            strategy.params,
            position=position,
            aux_bars=(aux or {}).get("bars"),
            aux_indicators=(aux or {}).get("indicators"),
        )
        try:
            return strategy.on_bar(ctx)
        except Exception as exc:  # noqa: BLE001
            # A broken strategy must not take down the worker. Log it and
            # treat it as no opinion.
            log.warning(
                "strategy %s raised on %s %s: %s",
                type(strategy).__name__,
                self.symbol,
                self.timeframe,
                exc,
            )
            return None


__all__ = ["Engine"]
