"""The backtester.

Deliberately in the RESEARCH worker, not the live worker. A backtest can be
slow and memory-hungry; the live engine must never be blocked by one.

What makes this backtester trustworthy:

  * Mark-to-market equity. Drawdown is measured on the equity curve INCLUDING
    open positions, not on closed trades only. Our own research measured
    what that difference does: the Turtle 55/20 strategy claimed a 3.29% max
    drawdown and was really -18.91%. A backtester that hides open losses
    lies, and a gate that reads its output is worthless.
  * Costs are charged on entry AND exit: commission plus a spread/slippage
    allowance.
  * Signals are evaluated on CLOSED bars only. No lookahead, no repainting.
  * Every result carries its denominator: the number of trades, the period,
    the benchmark, and the number of trials that produced it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Sequence

from strategy_core.interface import Bar, Position, Signal, Strategy


@dataclass
class CostModel:
    """What it costs to trade."""

    # Commission per side, as a fraction (0.001 = 10 bps).
    commission: float = 0.0
    # Spread and slippage per side, as a fraction of the price.
    spread_slippage: float = 0.0
    # Annual financing/borrow cost for a long position (0.0 = none).
    annual_borrow: float = 0.0
    # Annual carry cost paid on the notional (funding, swap).
    annual_carry: float = 0.0

    def round_trip(self) -> float:
        return 2.0 * (self.commission + self.spread_slippage)

    def per_bar_carry(self, bars_per_year: int) -> float:
        if not bars_per_year:
            return 0.0
        return (self.annual_borrow + self.annual_carry) / bars_per_year


@dataclass
class Trade:
    entry_ts: datetime
    exit_ts: datetime
    side: str
    entry_price: float
    exit_price: float
    size: float
    gross_return: float
    net_return: float
    costs: float
    reason: str


@dataclass
class BacktestResult:
    """The result, WITH its denominator."""

    label: str
    equity: list[float]
    timestamps: list[datetime]
    trades: list[Trade]
    metrics: dict[str, Any] = field(default_factory=dict)
    benchmark: dict[str, Any] = field(default_factory=dict)

    @property
    def trade_count(self) -> int:
        return len(self.trades)


def bars_per_year(timeframe: str) -> int:
    """Approximate bars per year for annualising. Crypto trades continuously."""
    per_day = {
        "1m": 1440,
        "5m": 288,
        "15m": 96,
        "1h": 24,
        "4h": 6,
        "1d": 1,
    }.get(timeframe, 1)
    return int(per_day * 365)


def compute_metrics(
    equity: Sequence[float],
    timestamps: Sequence[datetime],
    trades: Sequence[Trade],
    timeframe: str,
    benchmark: Sequence[float] | None = None,
    n_trials: int = 1,
) -> dict[str, Any]:
    """The metrics, computed the honest way.

    Drawdown is measured on the mark-to-market equity curve, so an open
    losing position counts against us. That is the entire point.
    """
    if len(equity) < 2:
        return {"error": "insufficient equity data"}

    values = list(equity)
    # A curve that touches zero would divide by zero here. A blown-up
    # account is a result to REPORT, not an exception to raise.
    returns = [
        values[i] / values[i - 1] - 1
        for i in range(1, len(values))
        if values[i - 1]
    ]

    # Drawdown on the mark-to-market curve.
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, v / peak - 1)

    span_days = (timestamps[-1] - timestamps[0]).total_seconds() / 86400.0
    years = max(span_days / 365.25, 1e-9)

    total_return = values[-1] / values[0] - 1
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if values[-1] > 0 else -1.0

    scale = bars_per_year(timeframe)
    if returns:
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        vol = math.sqrt(var) * math.sqrt(scale)
        sharpe = (mean_r * scale) / vol if vol > 0 else 0.0

        downside = [r for r in returns if r < 0]
        if downside:
            d_var = sum(r * r for r in downside) / len(downside)
            d_vol = math.sqrt(d_var) * math.sqrt(scale)
            sortino = (mean_r * scale) / d_vol if d_vol > 0 else 0.0
        else:
            sortino = 0.0
    else:
        vol = 0.0
        sharpe = 0.0
        sortino = 0.0

    calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0

    wins = [t for t in trades if t.net_return > 0]
    losses = [t for t in trades if t.net_return <= 0]
    gross_win = sum(t.net_return for t in wins)
    gross_loss = abs(sum(t.net_return for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    expectancy = (
        sum(t.net_return for t in trades) / len(trades) if trades else 0.0
    )
    win_rate = (len(wins) / len(trades)) if trades else 0.0

    metrics: dict[str, Any] = {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "volatility": vol,
        "trades": len(trades),
        "win_rate": win_rate,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "avg_trade": expectancy,
        "years": years,
        "n_trials": n_trials,
        "start": timestamps[0].date().isoformat(),
        "end": timestamps[-1].date().isoformat(),
    }

    if benchmark is not None and len(benchmark) == len(values):
        b_returns = [
            benchmark[i] / benchmark[i - 1] - 1
            for i in range(1, len(benchmark))
            if benchmark[i - 1]
        ]
        if b_returns:
            b_total = benchmark[-1] / benchmark[0] - 1
            b_years = years
            b_cagr = (benchmark[-1] / benchmark[0]) ** (1 / b_years) - 1
            metrics["benchmark_total_return"] = b_total
            metrics["benchmark_cagr"] = b_cagr
            metrics["excess_cagr"] = cagr - b_cagr
            if b_returns:
                b_mean = sum(b_returns) / len(b_returns)
                b_var = sum((r - b_mean) ** 2 for r in b_returns) / (len(b_returns) - 1)
                b_vol = math.sqrt(b_var) * math.sqrt(scale)
                metrics["beta"] = (
                    sum(
                        (returns[i] - sum(returns) / len(returns))
                        * (b_returns[i] - b_mean)
                        for i in range(len(b_returns))
                    )
                    / (len(b_returns) - 1)
                    / b_var
                    if b_var > 0
                    else None
                )

    return metrics


class Backtester:
    """Runs a strategy over a bar series and produces an equity curve."""

    def __init__(
        self,
        bars: Sequence[Bar],
        *,
        timeframe: str = "1d",
        cost_model: CostModel | None = None,
        initial_equity: float = 1.0,
        risk_per_trade: float = 0.01,
        max_leverage: float = 1.0,
        label: str = "backtest",
        n_trials: int = 1,
    ) -> None:
        self.bars = list(bars)
        self.timeframe = timeframe
        self.cost_model = cost_model or CostModel()
        self.initial_equity = initial_equity
        # Fraction of equity risked per trade (0.01 = 1%).
        #
        # This used to default to 1.0, which means "risk the whole account on
        # every trade". A caller that forgot to override it got a strategy
        # with no downside control at all, and the numbers looked plausible
        # because a curve that goes to zero still has a Sharpe.
        self.risk_per_trade = risk_per_trade
        # The cap on exposure, as a multiple of equity.
        #
        # `risk_per_trade / stop_distance` is the textbook position size, and
        # it is only safe while the stop is not tiny. A Donchian stop that
        # sits 0.5% from entry implies 4x equity of notional at 2% risk, and
        # a 25% adverse move wipes the account. Without this cap the
        # backtester reported -1716% drawdowns and +2670% CAGR, which are not
        # results, they are arithmetic accidents.
        self.max_leverage = max_leverage
        self.label = label
        self.n_trials = n_trials

    def run(
        self,
        signal_fn: Callable[[list[Bar], int], Signal | None],
        exit_fn: Callable[[list[Bar], int, Position], Signal | None] | None = None,
        benchmark: Sequence[float] | None = None,
    ) -> BacktestResult:
        """Run the backtest.

        `signal_fn(bars_so_far, index)` returns a Signal or None. It is given
        only CLOSED bars, so it cannot see the future.

        `exit_fn` is optional: it decides when to close an open position. If
        it is absent, the position is closed on the opposite signal.
        """
        equity = self.initial_equity
        curve: list[float] = [equity]
        # The curve has one point per bar PLUS the starting point, so the
        # timestamps must have one per bar plus the first bar's time. They
        # were off by one before, which shifted the whole period by a bar.
        timestamps: list[datetime] = [self.bars[0].ts] if self.bars else []
        trades: list[Trade] = []

        position = Position(side="flat", entry_price=0.0, size=0.0)
        entry_equity = equity
        entry_price = 0.0
        entry_index = 0
        entry_cost = 0.0
        # The P&L already folded into the equity for the open position. The
        # mark-to-market below adds only the CHANGE since the last mark, or
        # the same P&L is counted once per bar and the equity explodes.
        marked_pnl = 0.0
        # Exposure bookkeeping, so a run can prove what it did.
        max_exposure = 0.0
        largest_positions: list[dict[str, Any]] = []
        # A run that loses the whole account is a FAILURE, not a result.
        # It stops trading and says so, instead of reporting a Sharpe
        # computed on a curve that went to zero.
        bankrupt = False
        bankrupt_at: str | None = None

        bars = self.bars
        for i in range(len(bars)):
            bar = bars[i]
            timestamps.append(bar.ts)

            if bankrupt:
                # The account is gone. Nothing more is traded, and the curve
                # stays flat at the level it died at.
                curve.append(equity)
                continue

            # ---- mark to market first: open positions count against us ----
            if position.side != "flat" and position.size:
                # P&L in CURRENCY: price change x units. This used to be
                # (price change / entry) x units, which mixes a relative
                # return with a unit count and understates every P&L by a
                # factor of the price. On a 70,000-dollar asset that is a
                # 70,000x error, and it silently turned every research
                # metric (Sharpe, CAGR, drawdown) into noise.
                unrealised = (
                    (bar.close - entry_price)
                    * position.size
                    * (1 if position.side == "buy" else -1)
                )
                equity += unrealised - marked_pnl
                marked_pnl = unrealised

            signal = signal_fn(bars[: i + 1], i)

            # ---- exit handling ----
            if position.side != "flat":
                should_exit = False
                exit_reason = "opposite"
                if exit_fn is not None:
                    exit_signal = exit_fn(bars[: i + 1], i, position)
                    if exit_signal is not None and exit_signal.side == "flat":
                        should_exit = True
                        exit_reason = exit_signal.rule_id
                elif signal is not None and signal.side != "flat" and signal.side != position.side:
                    should_exit = True
                    exit_reason = f"signal:{signal.rule_id}"

                if should_exit:
                    exit_cost = bar.close * position.size * self.cost_model.round_trip() / 2
                    equity -= exit_cost
                    gross = (
                        (bar.close - entry_price) / entry_price
                        * (1 if position.side == "buy" else -1)
                    )
                    net = gross - self.cost_model.round_trip()
                    trades.append(
                        Trade(
                            entry_ts=bars[entry_index].ts,
                            exit_ts=bar.ts,
                            side=position.side,
                            entry_price=entry_price,
                            exit_price=bar.close,
                            size=position.size,
                            gross_return=gross,
                            net_return=net,
                            costs=entry_cost + exit_cost,
                            reason=exit_reason,
                        )
                    )
                    position = Position(side="flat", entry_price=0.0, size=0.0)
                    entry_equity = equity
                    marked_pnl = 0.0

            # ---- entry handling ----
            if (
                signal is not None
                and signal.side in ("buy", "sell")
                and position.side == "flat"
            ):
                stop = signal.stop_price
                if stop and stop > 0:
                    stop_distance = abs(bar.close - stop)
                    if stop_distance > 0:
                        # Size so the stop costs risk_per_trade of equity.
                        size = (equity * self.risk_per_trade) / stop_distance
                        size = max(0.0, size)
                        # ...but never more notional than the leverage cap.
                        if bar.close > 0:
                            max_size = (equity * self.max_leverage) / bar.close
                            size = min(size, max_size)
                        if size > 0:
                            entry_price = bar.close
                            entry_index = i
                            entry_cost = (
                                bar.close * size * self.cost_model.round_trip() / 2
                            )
                            equity -= entry_cost
                            notional = bar.close * size
                            exposure = notional / equity if equity > 0 else 0.0
                            max_exposure = max(max_exposure, exposure)
                            largest_positions.append(
                                {
                                    "ts": bar.ts.isoformat(),
                                    "side": signal.side,
                                    "price": bar.close,
                                    "size": size,
                                    "notional": notional,
                                    "exposure": exposure,
                                    "stop_distance_pct": (
                                        stop_distance / bar.close if bar.close else 0.0
                                    ),
                                }
                            )
                            position = Position(
                                side=signal.side,
                                entry_price=bar.close,
                                size=size,
                                opened_at=bar.ts,
                            )
                            entry_equity = equity
                            marked_pnl = 0.0

            curve.append(equity)

            if equity <= 0:
                # The account is gone. Stop trading and record it.
                bankrupt = True
                bankrupt_at = bar.ts.isoformat()
                position = Position(side="flat", entry_price=0.0, size=0.0)
                marked_pnl = 0.0

        # Close any open position at the last bar, so the curve ends flat.
        if position.side != "flat" and position.size and self.bars:
            last = self.bars[-1]
            gross = (
                (last.close - entry_price) / entry_price
                * (1 if position.side == "buy" else -1)
            )
            net = gross - self.cost_model.round_trip()
            # The exit costs are real costs. They were charged on every
            # ordinary exit but NOT on the forced close at the end of the
            # data, so the last trade of every backtest was quietly cheaper
            # than it should have been.
            exit_cost = last.close * position.size * self.cost_model.round_trip() / 2
            equity -= exit_cost
            if curve:
                curve[-1] = equity
            trades.append(
                Trade(
                    entry_ts=bars[entry_index].ts,
                    exit_ts=last.ts,
                    side=position.side,
                    entry_price=entry_price,
                    exit_price=last.close,
                    size=position.size,
                    gross_return=gross,
                    net_return=net,
                    costs=entry_cost + exit_cost,
                    reason="end_of_data",
                )
            )

        metrics = compute_metrics(
            curve, timestamps, trades, self.timeframe, benchmark, self.n_trials
        )
        metrics["initial_equity"] = self.initial_equity
        metrics["final_equity"] = curve[-1] if curve else equity
        metrics["risk_per_trade"] = self.risk_per_trade
        metrics["max_leverage"] = self.max_leverage
        metrics["max_exposure_used"] = max_exposure
        metrics["bankrupt"] = bankrupt
        metrics["bankrupt_at"] = bankrupt_at
        # The five biggest positions, so a reader can see where the money
        # went without rerunning the backtest.
        metrics["largest_positions"] = sorted(
            largest_positions, key=lambda row: row["notional"], reverse=True
        )[:5]
        metrics["costs"] = {
            "commission": self.cost_model.commission,
            "spread_slippage": self.cost_model.spread_slippage,
            "round_trip": self.cost_model.round_trip(),
        }

        return BacktestResult(
            label=self.label,
            equity=curve,
            timestamps=timestamps,
            trades=trades,
            metrics=metrics,
        )


__all__ = ["Backtester", "BacktestResult", "CostModel", "Trade", "compute_metrics"]
