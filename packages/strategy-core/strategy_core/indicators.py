"""Indicators.

Pure Python, no numpy dependency, so the live worker on a 1 GB VM and the
research worker on a laptop compute identical numbers. That matters: if the
live engine and the backtest disagree on an indicator by a rounding step, the
shadow-mode comparison is meaningless.

Every function returns a list the SAME LENGTH as its input, padded at the
front with `None` for the warm-up period. That alignment is what lets
`ctx.indicator("ema_50", -1)` mean "the value on the last bar" without any
index bookkeeping at the call site.
"""

from __future__ import annotations

from typing import Sequence

from .interface import Bar

__all__ = [
    "ema",
    "sma",
    "rsi",
    "atr",
    "adx",
    "donchian",
    "true_range",
    "highest",
    "lowest",
    "realized_volatility",
]


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Simple moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the first `period` SMA."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out

    multiplier = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = (values[i] - current) * multiplier + current
        out[i] = current
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI."""
    if len(values) < period + 1:
        return [None] * len(values)

    out: list[float | None] = [None] * len(values)

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def true_range(bars: Sequence[Bar]) -> list[float]:
    """True range for each bar. The first bar falls back to high - low."""
    if not bars:
        return []
    out = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        out.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - prev_close),
                abs(bars[i].low - prev_close),
            )
        )
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    """Average true range, Wilder-smoothed."""
    if len(bars) < period:
        return [None] * len(bars)
    tr = true_range(bars)
    out: list[float | None] = [None] * len(bars)

    current = sum(tr[:period]) / period
    out[period - 1] = current
    for i in range(period, len(bars)):
        current = (current * (period - 1) + tr[i]) / period
        out[i] = current
    return out


def adx(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    """Average Directional Index.

    Returns None during the warm-up period, which is twice `period` long.
    """
    n = len(bars)
    if n < period * 2:
        return [None] * n

    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    tr = true_range(bars)

    out: list[float | None] = [None] * n

    # Wilder smoothing over the first `period` window.
    smooth_tr = sum(tr[1 : period + 1])
    smooth_plus = sum(plus_dm[1 : period + 1])
    smooth_minus = sum(minus_dm[1 : period + 1])

    dx_values: list[float] = []
    dx_index: list[int] = []

    for i in range(period, n):
        if i > period:
            smooth_tr = smooth_tr - (smooth_tr / period) + tr[i]
            smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
            smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]

        if smooth_tr == 0:
            di_plus = di_minus = 0.0
        else:
            di_plus = 100.0 * smooth_plus / smooth_tr
            di_minus = 100.0 * smooth_minus / smooth_tr

        denom = di_plus + di_minus
        dx = 0.0 if denom == 0 else 100.0 * abs(di_plus - di_minus) / denom
        dx_values.append(dx)
        dx_index.append(i)

    if len(dx_values) < period:
        return out

    current = sum(dx_values[:period]) / period
    out[dx_index[period - 1]] = current
    for k in range(period, len(dx_values)):
        current = (current * (period - 1) + dx_values[k]) / period
        out[dx_index[k]] = current
    return out


def highest(values: Sequence[float], period: int) -> list[float | None]:
    """Rolling maximum."""
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1 : i + 1])
    return out


def lowest(values: Sequence[float], period: int) -> list[float | None]:
    """Rolling minimum."""
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = min(values[i - period + 1 : i + 1])
    return out


def donchian(
    bars: Sequence[Bar], period: int = 20
) -> tuple[list[float | None], list[float | None]]:
    """Donchian channels: (upper, lower).

    Note that the upper band EXCLUDES the current bar. A channel that
    includes the current bar can never be broken, because the close is by
    definition inside it.
    """
    highs = highest([b.high for b in bars], period)
    lows = lowest([b.low for b in bars], period)
    upper: list[float | None] = [None] * len(bars)
    lower: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        if i == 0:
            continue
        # Shift by one: use the PRIOR `period` bars, not including this one.
        window_high = highest([b.high for b in bars[:i]], period)
        window_low = lowest([b.low for b in bars[:i]], period)
        upper[i] = window_high[-1] if window_high else None
        lower[i] = window_low[-1] if window_low else None
    return upper, lower


def realized_volatility(closes: Sequence[float], period: int = 20) -> list[float | None]:
    """Annualised standard deviation of log returns over `period` bars."""
    if len(closes) < period + 1:
        return [None] * len(closes)

    import math

    out: list[float | None] = [None] * len(closes)
    for i in range(period, len(closes)):
        window = closes[i - period + 1 : i + 1]
        returns = [
            math.log(window[j] / window[j - 1]) for j in range(1, len(window)) if window[j - 1] > 0
        ]
        if len(returns) < 2:
            continue
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        # 252 annualisation for daily, scaled elsewhere for other timeframes.
        out[i] = math.sqrt(variance) * math.sqrt(252)
    return out
