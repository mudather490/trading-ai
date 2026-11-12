"""Strategy templates.

Each template is a rule set with parameters. They are the candidates the
promotion gate judges, and the ones the AI is allowed to mutate (Tier A) or
rewrite entirely (Tier B, in a sandbox).

Every template here is DELIBERATELY SIMPLE. The evidence from our own
research is blunt about what does not work:

  * A Turtle 55/20 breakout claimed 3.29% max drawdown. On a mark-to-market
    equity curve it was -18.91%. The number was an artefact of how drawdown
    was measured.
  * A confirmed-level support/resistance rule scored Sharpe 0.68-0.74
    in-sample and -0.04 to 0.26 out of sample. The edge did not survive.
  * A set of narrative "predictions" scored 72% against an 88% naive
    baseline. Being worse than doing nothing clever is a real outcome.

So: no template invents a signal. Each one is a rule you can state in one
sentence, it must beat its benchmark on a cost-adjusted out-of-sample basis,
and if it does not, it is published as a failure rather than quietly dropped.
"""

from __future__ import annotations

from typing import Any, Mapping

from .indicators import adx, atr, donchian, ema, rsi
from .interface import Bar, Context, Signal, Strategy


class RotationStrategy(Strategy):
    """S18: hold the risk asset when it is above its long moving average,
    otherwise hold the defensive asset.

    "If QQQ is above its 200-day average, hold QQQ. If not, hold gold."

    This is the ONE strategy in this project with an independently verified
    result: +28.41% CAGR / Sharpe 1.77 / -18.10% max drawdown on 2022+, and
    +29.63% CAGR / -12.25% max drawdown / Sharpe 1.69 on 2025+.

    The strategy runs on the RISK symbol and decides which of the two legs to
    hold. It emits a signal for the risk symbol and for the defensive symbol
    separately, via `signals_for`.
    """

    min_bars = 210
    defaults = {
        "ma_period": 200,
        "risk_symbol": "QQQ",
        "defensive_symbol": "GLD",
        # The regime decides the leg. Above the average => risk; else defensive.
        "atr_period": 14,
        "atr_stop_mult": 2.0,
    }

    def regime(self, ctx: Context) -> str | None:
        """'risk' or 'defensive', or None if there is not enough history."""
        ma = ctx.indicator(f"ma_{self.params['ma_period']}")
        if ma is None:
            return None
        close = ctx.last_bar().close if ctx.last_bar() else None
        if close is None:
            return None
        return "risk" if close > ma else "defensive"

    def on_bar(self, ctx: Context) -> Signal | None:
        return self.signals_for(ctx).get(ctx.symbol)

    def signals_for(self, ctx: Context) -> dict[str, Signal]:
        """The signal for each leg.

        Returns a mapping of symbol -> Signal. The risk symbol gets a BUY
        when the regime is risk, the defensive symbol gets a BUY when the
        regime is defensive, and the other leg gets a FLAT.
        """
        risk = self.params["risk_symbol"]
        defensive = self.params["defensive_symbol"]

        regime = self.regime(ctx)
        if regime is None:
            return {}

        atr_value = ctx.indicator(f"atr_{self.params['atr_period']}")
        stop_mult = float(self.params["atr_stop_mult"])

        out: dict[str, Signal] = {}

        if regime == "risk":
            out[risk] = self._entry(ctx, risk, "buy", atr_value, stop_mult)
            out[defensive] = Signal(side="flat", rule_id="rotation_defensive")
        else:
            out[defensive] = self._entry(ctx, defensive, "buy", atr_value, stop_mult)
            out[risk] = Signal(side="flat", rule_id="rotation_risk")

        return out

    def _entry(
        self,
        ctx: Context,
        symbol: str,
        side: str,
        atr_value: float | None,
        stop_mult: float,
    ) -> Signal:
        bar = ctx.last_bar()
        if bar is None:
            return Signal(side="flat", rule_id="rotation")

        stop = None
        if atr_value:
            stop = bar.close - stop_mult * atr_value

        return Signal(
            side=side,
            stop_price=stop,
            target_price=None,  # a rotation has no target; it has a regime
            confidence=None,
            rule_id="rotation_ma",
            notes={
                "ma_period": self.params["ma_period"],
                "risk_symbol": self.params["risk_symbol"],
                "defensive_symbol": self.params["defensive_symbol"],
            },
        )


class TrendEmaAdxStrategy(Strategy):
    """Trend following: a fast/slow EMA cross, filtered by ADX.

    The ADX filter matters. A crossover in a ranging market is noise, and
    the filter is what turns a crossover into a trend system.
    """

    min_bars = 60
    defaults = {
        "fast": 20,
        "slow": 50,
        "adx_period": 14,
        "min_adx": 20.0,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
    }

    def on_bar(self, ctx: Context) -> Signal | None:
        if not ctx.ready(self.min_bars):
            return None

        fast = ctx.indicator(f"ema_{self.params['fast']}")
        slow = ctx.indicator(f"ema_{self.params['slow']}")
        prev_fast = ctx.indicator(f"ema_{self.params['fast']}", -2)
        prev_slow = ctx.indicator(f"ema_{self.params['slow']}", -2)
        adx_value = ctx.indicator(f"adx_{self.params['adx_period']}")
        atr_value = ctx.indicator(f"atr_{self.params['atr_period']}")
        bar = ctx.last_bar()

        if None in (fast, slow, prev_fast, prev_slow, adx_value, atr_value) or bar is None:
            return None

        if adx_value < float(self.params["min_adx"]):
            return None

        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow

        if crossed_up:
            return Signal(
                side="buy",
                stop_price=bar.close - float(self.params["atr_stop_mult"]) * atr_value,
                confidence=min(1.0, adx_value / 50.0),
                rule_id="ema_adx_up",
            )
        if crossed_down:
            return Signal(
                side="sell",
                stop_price=bar.close + float(self.params["atr_stop_mult"]) * atr_value,
                confidence=min(1.0, adx_value / 50.0),
                rule_id="ema_adx_down",
            )
        return None


class DonchianBreakoutStrategy(Strategy):
    """Donchian channel breakout.

    The Turtle rules. Kept because our own research measured what happens:
    the claim of a 3.29% max drawdown was a measurement artefact, and the
    honest mark-to-market figure was -18.91%. A strategy in this project that
    hides its drawdown is rejected by the gate.
    """

    min_bars = 60
    defaults = {
        "entry_period": 20,
        "exit_period": 10,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
    }

    def on_bar(self, ctx: Context) -> Signal | None:
        if not ctx.ready(self.min_bars):
            return None

        upper = ctx.indicator("donchian_upper")
        lower = ctx.indicator("donchian_lower")
        atr_value = ctx.indicator(f"atr_{self.params['atr_period']}")
        bar = ctx.last_bar()

        if upper is None or lower is None or atr_value is None or bar is None:
            return None

        # The channel EXCLUDES the current bar, so a close can break it.
        if bar.close > upper:
            return Signal(
                side="buy",
                stop_price=bar.close - float(self.params["atr_stop_mult"]) * atr_value,
                target_price=None,
                rule_id="donchian_breakout_up",
            )
        if bar.close < lower:
            return Signal(
                side="sell",
                stop_price=bar.close + float(self.params["atr_stop_mult"]) * atr_value,
                target_price=None,
                rule_id="donchian_breakout_down",
            )
        return None


class Rsi2MeanReversionStrategy(Strategy):
    """RSI(2) mean reversion, long only.

    Connors' RSI-2 system. It only works in a ranging market, so it is
    filtered: below a long moving average it is disabled entirely.
    """

    min_bars = 60
    defaults = {
        "rsi_period": 2,
        "rsi_oversold": 5.0,
        "rsi_overbought": 60.0,
        "trend_filter_ma": 200,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
    }

    def on_bar(self, ctx: Context) -> Signal | None:
        if not ctx.ready(self.min_bars):
            return None

        rsi_value = ctx.indicator(f"rsi_{self.params['rsi_period']}")
        trend_ma = ctx.indicator(f"ma_{self.params['trend_filter_ma']}")
        atr_value = ctx.indicator(f"atr_{self.params['atr_period']}")
        bar = ctx.last_bar()

        if rsi_value is None or trend_ma is None or atr_value is None or bar is None:
            return None

        # Only above the long-term trend. Below it, we do not buy the dip.
        if bar.close < trend_ma:
            return None

        if rsi_value < float(self.params["rsi_oversold"]):
            return Signal(
                side="buy",
                stop_price=bar.close - float(self.params["atr_stop_mult"]) * atr_value,
                rule_id="rsi2_oversold",
            )
        return None


# The registry. The live worker and the research worker both load strategies
# from here, so a template cannot exist in one place and not the other.
TEMPLATES: dict[str, type[Strategy]] = {
    "rotation_ma": RotationStrategy,
    "trend_ema_adx": TrendEmaAdxStrategy,
    "donchian_breakout": DonchianBreakoutStrategy,
    "rsi2_mean_reversion": Rsi2MeanReversionStrategy,
}


def build(template_id: str, params: Mapping[str, Any] | None = None) -> Strategy:
    cls = TEMPLATES.get(template_id)
    if cls is None:
        raise KeyError(
            f"Unknown strategy template '{template_id}'. "
            f"Available: {', '.join(sorted(TEMPLATES))}"
        )
    return cls(params)


__all__ = [
    "TEMPLATES",
    "RotationStrategy",
    "TrendEmaAdxStrategy",
    "DonchianBreakoutStrategy",
    "Rsi2MeanReversionStrategy",
    "build",
]
