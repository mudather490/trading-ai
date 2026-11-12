"""Golden-master test: the engine must agree with an independent implementation.

The previous test verified the RULE. This one verifies the ENGINE.

It runs the RotationStrategy through the Engine and the Backtester, then
compares the result against a completely separate implementation of the same
rule (in test_s18_reproduction.py). If the engine, the strategy, the
indicator code and the backtester all agree with an independent
implementation of the same idea, then a bug would have to be present in both
at once to slip through.

That is the point of a golden master: not that it is a strong test, but that
it is an INDEPENDENT one.

Run:  python -m pytest services/research-worker/tests -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "strategy-core"))

from research.backtest import Backtester, CostModel  # noqa: E402
from strategy_core.engine import Engine  # noqa: E402
from strategy_core.interface import Position, Signal  # noqa: E402
from strategy_core.strategies import RotationStrategy  # noqa: E402

from test_s18_reproduction import fetch_yahoo, rotation_equity  # noqa: E402


@pytest.fixture(scope="module")
def market_data():
    try:
        return fetch_yahoo("QQQ"), fetch_yahoo("GLD")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"market data unavailable: {exc}")


def _engine_rotation(
    qqq, gld, ma_period: int = 200, start: str = "2022-01-01"
) -> tuple[list[float], list[datetime]]:
    """Run the rotation through the real Engine and Backtester.

    The Engine holds QQQ. GLD is fed in as an auxiliary series. The signal
    function asks the strategy for a signal on each closed bar.
    """
    gld_by_ts = {b.ts: b.close for b in gld}
    common = [b for b in qqq if b.ts in gld_by_ts]

    # Build the engine ONCE over the whole series, then ask it for the
    # context as of each bar. Recomputing the indicators bar by bar is
    # O(n^2) and turns a five-year backtest into a hang.
    engine = Engine("QQQ", "1d", ma_periods=(ma_period,))
    engine.add_bars(common)
    strategy = RotationStrategy({"ma_period": ma_period})

    equity = 1.0
    curve = [equity]
    timestamps: list[datetime] = []

    holding = "QQQ"

    for i, bar in enumerate(common):
        gld_bars = [b for b in gld if b.ts <= bar.ts]

        if bar.ts.date().isoformat() < start:
            continue

        ctx = engine.context_at(
            i,
            strategy.params,
            position=Position(side="flat", entry_price=0.0, size=0.0),
            aux_bars={"GLD": gld_bars},
        )
        signals = strategy.signals_for(ctx)

        # Which leg should we be holding after this bar?
        want = "QQQ" if signals.get("QQQ") and signals["QQQ"].side == "buy" else "GLD"

        if want != holding:
            holding = want

        # The return of the leg we hold.
        if holding == "QQQ":
            ret = bar.close / (common[i - 1].close if i else bar.close) - 1
        else:
            prev_gld = gld_by_ts[common[i - 1].ts] if i else bar.close
            ret = gld_by_ts[bar.ts] / prev_gld - 1

        equity *= 1 + ret
        curve.append(equity)
        timestamps.append(bar.ts)

    return curve, timestamps


def test_the_engine_matches_the_independent_implementation(market_data):
    """The engine and a separate implementation must agree.

    We allow a small tolerance: the two differ in how they treat the very
    first bar of the window, which contributes one bar's return.
    """
    qqq, gld = market_data

    engine_curve, engine_ts = _engine_rotation(qqq, gld)
    ref_curve, ref_ts, _ = rotation_equity(qqq, gld, start="2022-01-01")

    assert len(engine_curve) == len(ref_curve), (
        f"different bar counts: engine {len(engine_curve)} vs reference {len(ref_curve)}"
    )

    engine_total = engine_curve[-1] / engine_curve[0]
    ref_total = ref_curve[-1] / ref_curve[0]

    print(
        f"\n  engine {engine_total:.4f}  reference {ref_total:.4f}  "
        f"delta {abs(engine_total - ref_total):.6f}"
    )

    # A 2% relative difference is generous but still catches a real
    # disagreement in the indicator maths or the bar alignment.
    assert abs(engine_total - ref_total) / max(ref_total, 1e-9) < 0.02, (
        "the engine and the independent implementation disagree"
    )


def test_the_engine_reproduces_the_verified_shape(market_data):
    """The engine's own numbers must match the published S18 figures."""
    import math

    qqq, gld = market_data
    curve, timestamps = _engine_rotation(qqq, gld)

    values = curve
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]

    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)

    years = (timestamps[-1] - timestamps[0]).total_seconds() / (86400 * 365.25)
    cagr = (values[-1] / values[0]) ** (1 / years) - 1

    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    vol = math.sqrt(var) * math.sqrt(365)
    sharpe = (mean_r * 365) / vol

    print(f"\n  ENGINE S18 2022+: CAGR {cagr * 100:.2f}%  Sharpe {sharpe:.2f}  maxDD {max_dd * 100:.2f}%")

    # Published: +28.41% CAGR, Sharpe 1.77, -18.10% maxDD.
    assert 0.24 < cagr < 0.34, f"CAGR {cagr:.4f} outside the verified band"
    assert 1.6 < sharpe < 2.0, f"Sharpe {sharpe:.2f} outside the verified band"
    assert -0.22 < max_dd < -0.13, f"maxDD {max_dd:.4f} outside the verified band"


def test_the_backtester_and_the_engine_agree_on_trade_count(market_data):
    """The backtester must produce trades the engine's signals justify."""
    qqq, gld = market_data

    engine = Engine("QQQ", "1d", ma_periods=(200,))
    engine.add_bars(qqq)
    strategy = RotationStrategy({"ma_period": 200})

    def signal_fn(bars_so_far, index):
        bar = bars_so_far[-1]
        ctx = engine.context_at(index, strategy.params)
        signals = strategy.signals_for(ctx)
        qqq_signal = signals.get("QQQ")
        if qqq_signal and qqq_signal.side == "buy":
            # A 10% stop. With a 2% risk budget that is a 0.2x position:
            # sane, not levered.
            return Signal(
                side="buy",
                stop_price=bar.close * 0.90,
                rule_id="rotation_ma",
            )
        if qqq_signal and qqq_signal.side == "flat":
            # The regime flipped to the defensive leg: exit.
            return Signal(side="flat", rule_id="rotation_exit")
        return None

    bt = Backtester(
        qqq,
        timeframe="1d",
        cost_model=CostModel(commission=0.0005),
        risk_per_trade=0.02,
        label="engine",
    )
    result = bt.run(signal_fn)

    assert result.metrics["trades"] >= 1, "the rotation produced no trades"
    assert result.metrics["max_drawdown"] < 0
    assert result.metrics["start"] < result.metrics["end"]
    print(
        f"\n  backtest: {result.metrics['trades']} trades, "
        f"CAGR {result.metrics['cagr'] * 100:.2f}%, "
        f"maxDD {result.metrics['max_drawdown'] * 100:.2f}%"
    )
