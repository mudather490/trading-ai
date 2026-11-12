"""The backtester's arithmetic.

The mark-to-market on the equity curve is the number every gate rule reads:
Sharpe, CAGR and the drawdown all come from it. It used to mix units — a
relative return multiplied by a unit count — which understated every P&L by
a factor of the price. On a 70,000-dollar asset that is a 70,000x error,
and it was invisible because the curve still moved in the right direction.

These tests pin the arithmetic down with numbers a human can check.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "strategy-core"))

from research.backtest import Backtester, CostModel  # noqa: E402
from strategy_core.interface import Bar, Signal  # noqa: E402


def _bars(prices: list[float]) -> list[Bar]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            ts=base + timedelta(days=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1.0,
        )
        for index, price in enumerate(prices)
    ]


def test_the_equity_curve_counts_the_profit_in_currency():
    """Buy at 100, the price doubles, the equity doubles.

    The position is sized so that a stop 50 below costs `risk_per_trade` of
    the equity: size = equity * risk / stop_distance = 0.5 / 50 = 0.01 units.
    At 200, that position is worth (200 - 100) * 0.01 = 1.0 on an equity of
    1.0.
    """
    bars = _bars([100.0, 200.0])

    def buy_on_the_first_bar(bars_so_far, index):
        if index == 0:
            return Signal(side="buy", stop_price=50.0)
        return None

    backtester = Backtester(
        bars,
        timeframe="1d",
        cost_model=CostModel(),
        risk_per_trade=0.5,
        label="unit-test",
    )
    result = backtester.run(buy_on_the_first_bar)

    # equity[0] is the starting equity, then one point per bar.
    assert len(result.equity) == 3
    assert abs(result.equity[1] - 1.0) < 1e-9, result.equity
    # The mark-to-market at 200: (200 - 100) * 0.01 units = +1.0.
    assert abs(result.equity[2] - 2.0) < 1e-9, result.equity
    assert abs(result.equity[-1] - 2.0) < 1e-9, result.equity


def test_a_position_that_loses_money_marks_against_the_equity():
    bars = _bars([100.0, 90.0])

    def buy_on_the_first_bar(bars_so_far, index):
        if index == 0:
            return Signal(side="buy", stop_price=50.0)
        return None

    backtester = Backtester(
        bars,
        timeframe="1d",
        cost_model=CostModel(),
        risk_per_trade=0.5,
        label="unit-test",
    )
    result = backtester.run(buy_on_the_first_bar)
    # A 10-point fall with 0.01 units is -0.1 on an equity of 1.0.
    assert abs(result.equity[2] - 0.9) < 1e-9, result.equity


def test_the_costs_are_charged_in_currency():
    bars = _bars([100.0, 100.0])

    def buy_on_the_first_bar(bars_so_far, index):
        if index == 0:
            return Signal(side="buy", stop_price=50.0)
        return None

    backtester = Backtester(
        bars,
        timeframe="1d",
        cost_model=CostModel(commission=0.001, spread_slippage=0.0005),
        risk_per_trade=0.5,
        label="unit-test",
    )
    result = backtester.run(buy_on_the_first_bar)

    # 0.01 units at 100 is a 1.0 notional; 0.15% per side is 0.0015, twice.
    assert result.equity[-1] < 1.0, result.equity
    assert abs(result.equity[-1] - (1.0 - 0.003)) < 1e-9, result.equity


def test_a_short_gains_when_the_price_falls():
    bars = _bars([100.0, 80.0])

    def short_on_the_first_bar(bars_so_far, index):
        if index == 0:
            return Signal(side="sell", stop_price=150.0)
        return None

    backtester = Backtester(
        bars,
        timeframe="1d",
        cost_model=CostModel(),
        risk_per_trade=0.5,
        label="unit-test",
    )
    result = backtester.run(short_on_the_first_bar)
    # size = 0.5 / 50 = 0.01 units; a 20-point fall is +0.2 on an equity of 1.
    assert abs(result.equity[2] - 1.2) < 1e-9, result.equity


def test_the_default_risk_is_one_percent():
    """Not one hundred percent.

    The default used to be 1.0, which means "risk the whole account on every
    trade". A caller that forgot to override it got no downside control.
    """
    backtester = Backtester(_bars([100.0, 101.0]), timeframe="1d")
    assert backtester.risk_per_trade == 0.01
    assert backtester.max_leverage == 1.0


def test_a_tight_stop_cannot_leverage_the_account():
    """The stop distance must not decide how big the position is.

    `risk / stop_distance` is the textbook size. With a stop 0.5% away and
    50% risk, it asks for 100x the equity in notional. The leverage cap is
    what stops that.
    """
    bars = _bars([100.0, 100.0])

    def buy_with_a_tight_stop(bars_so_far, index):
        if index == 0:
            return Signal(side="buy", stop_price=99.5)
        return None

    backtester = Backtester(
        bars,
        timeframe="1d",
        cost_model=CostModel(),
        risk_per_trade=0.5,
        max_leverage=1.0,
        label="unit-test",
    )
    result = backtester.run(buy_with_a_tight_stop)

    largest = result.metrics["largest_positions"][0]
    assert largest["notional"] <= 1.0 + 1e-9, largest
    assert result.metrics["max_exposure_used"] <= 1.0 + 1e-9


def test_a_run_that_loses_the_account_is_flagged_and_stops():
    """A blown-up account is a failure, not a result.

    The curve must stop where it died, not keep going into negative
    territory: a -1716% drawdown is an arithmetic accident, not a risk
    measurement.
    """
    bars = _bars([100.0, 0.0])

    def buy_on_the_first_bar(bars_so_far, index):
        if index == 0:
            return Signal(side="buy", stop_price=50.0)
        return None

    backtester = Backtester(
        bars,
        timeframe="1d",
        cost_model=CostModel(),
        risk_per_trade=0.5,
        max_leverage=1.0,
        label="unit-test",
    )
    result = backtester.run(buy_on_the_first_bar)

    assert result.metrics["bankrupt"] is True
    assert result.metrics["bankrupt_at"] is not None
    # The equity never goes below zero, so the drawdown cannot exceed 100%.
    assert min(result.equity) >= -1e-9, result.equity
    assert result.metrics["max_drawdown"] >= -1.0


def test_a_healthy_run_is_not_flagged_as_bankrupt():
    bars = _bars([100.0, 110.0])

    def buy_on_the_first_bar(bars_so_far, index):
        if index == 0:
            return Signal(side="buy", stop_price=50.0)
        return None

    backtester = Backtester(
        bars,
        timeframe="1d",
        cost_model=CostModel(),
        risk_per_trade=0.02,
        max_leverage=1.0,
        label="unit-test",
    )
    result = backtester.run(buy_on_the_first_bar)

    assert result.metrics["bankrupt"] is False
    assert result.metrics["max_drawdown"] > -1.0
    assert result.metrics["max_exposure_used"] <= 1.0 + 1e-9
