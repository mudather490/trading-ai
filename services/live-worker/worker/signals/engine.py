"""The live signal engine.

This is the missing link. `signals` and `record_signal()` were built and
tested, but nothing ever called them: the worker streamed prices, built bars
and published a heartbeat, and `get_ticket()` therefore answered `no_match`
for every asset, forever.

What this module does, and refuses to do:

  * It evaluates a strategy ONLY on a CLOSED bar. The bar builder emits a
    bar after its window has ended; a forming bar is never visible, so a
    signal cannot repaint.
  * It uses the SAME strategy templates and the SAME indicator engine as the
    backtester (`packages/strategy-core`). A live run cannot disagree with
    the backtest about what a strategy sees.
  * It never sizes, never places an order and never promotes. Sizing lives in
    the Postgres RPC; promotion belongs to the owner.
  * It writes through the idempotent `record_signal()` RPC, keyed by
    (version, symbol, timeframe, rule, bar close). A reconnect that replays a
    bar cannot create a second signal.
  * A missing champion, a missing strategy template, or a strategy that
    raises is a normal outcome: log it and write nothing. Silence is honest.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# The strategy package is a sibling of `services`, not installed into the
# worker's virtualenv. Put it on the path once, explicitly.
_CORE = Path(__file__).resolve().parents[4] / "packages" / "strategy-core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from strategy_core.engine import Engine  # noqa: E402
from strategy_core.interface import Bar as CoreBar  # noqa: E402
from strategy_core.interface import Position  # noqa: E402
from strategy_core.strategies import TEMPLATES, build  # noqa: E402

from ..bars import signal_key  # noqa: E402

log = logging.getLogger(__name__)

# How many closed bars to load when an engine is first built. Rotation needs
# 200 for its moving average; 400 leaves headroom for every template.
LOOKBACK = 400

# A full reload is cheaper than pretending a hole is a trend. If the newest
# stored bar is further behind than this, reload from the database.
_MAX_GAP_BARS = 10


@dataclass(frozen=True)
class Binding:
    """The promoted strategy for one (asset class, timeframe)."""

    asset_class: str
    timeframe: str
    version_id: str
    version: int
    template_id: str
    strategy_name: str
    params: dict[str, Any]
    min_reward_risk: float


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def timeframe_delta(timeframe: str) -> timedelta:
    seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }.get(timeframe, 60)
    return timedelta(seconds=seconds)


def _core_bar(row: dict[str, Any]) -> CoreBar:
    return CoreBar(
        ts=_utc(datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume") or 0.0),
    )


class SignalEngine:
    """Champion registry + per-(instrument, timeframe) strategy engines."""

    def __init__(self, writer) -> None:
        self.writer = writer
        self.bindings: dict[tuple[str, str], Binding] = {}
        self._engines: dict[tuple[str, str], Engine] = {}
        self._strategies: dict[tuple[str, str], Any] = {}
        self._last_bar: dict[tuple[str, str], datetime] = {}
        self._last_side: dict[tuple[str, str], str] = {}
        self._owner_id: str | None = None

    # ------------------------------------------------------------ registry --

    def refresh_registry(self) -> None:
        """Reload the promoted champions. Failure keeps the previous set."""
        try:
            rows = self.writer.fetch_active_champions()
        except Exception as exc:  # noqa: BLE001
            log.warning("champion registry refresh failed: %s", exc)
            return

        previous = self.bindings
        bindings: dict[tuple[str, str], Binding] = {}
        for row in rows:
            template_id = str(row.get("template_id") or "")
            if template_id not in TEMPLATES:
                log.warning(
                    "champion %s/%s uses unknown template '%s'; skipping",
                    row.get("asset_class"),
                    row.get("timeframe"),
                    template_id,
                )
                continue
            risk_spec = row.get("risk_spec") or {}
            try:
                rr_floor = float(risk_spec.get("min_reward_risk", 1.0))
            except (TypeError, ValueError):
                rr_floor = 1.0
            key = (str(row["asset_class"]), str(row["timeframe"]))
            bindings[key] = Binding(
                asset_class=key[0],
                timeframe=key[1],
                version_id=str(row["version_id"]),
                version=int(row.get("version") or 1),
                template_id=template_id,
                strategy_name=str(row.get("strategy_name") or template_id),
                params=dict(row.get("params") or {}),
                min_reward_risk=rr_floor,
            )

        # If the promoted set changed, the cached engines may be running an
        # old template or old parameters. Rebuild them on next use.
        changed = bindings != previous
        self.bindings = bindings
        if changed:
            self._engines.clear()
            self._strategies.clear()
            self._last_bar.clear()
            self._last_side.clear()
        log.info(
            "champion registry: %s",
            ", ".join(f"{a}/{t}" for a, t in sorted(bindings)) or "(none)",
        )

    def binding_for(self, asset_class: str, timeframe: str) -> Binding | None:
        return self.bindings.get((asset_class, timeframe))

    # -------------------------------------------------------------- owner ---

    def owner_id(self) -> str | None:
        """The user who receives signal alerts. Resolved once and cached."""
        if self._owner_id is None:
            try:
                self._owner_id = self.writer.fetch_owner_id()
            except Exception as exc:  # noqa: BLE001
                log.warning("could not resolve the owner for alerts: %s", exc)
        return self._owner_id

    # ----------------------------------------------------------- evaluation --

    def evaluate_latest(
        self,
        *,
        instrument_id: str,
        symbol: str,
        asset_class: str,
        timeframe: str,
        data_quality: str | None = None,
    ) -> dict[str, Any] | None:
        """Evaluate the newest stored bar for one instrument/timeframe.

        Called after a backfill so the first ticket does not have to wait for
        the next bar close. It is the same code path as the live evaluation:
        the newest CLOSED bar in the database.
        """
        if self.binding_for(asset_class, timeframe) is None:
            return None
        rows = self.writer.fetch_bars(instrument_id, timeframe, 1)
        if not rows:
            return None
        row = rows[-1]

        from types import SimpleNamespace

        bar = SimpleNamespace(
            ts=datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00")),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0.0),
            timeframe=timeframe,
        )
        return self.evaluate(
            instrument_id=instrument_id,
            symbol=symbol,
            asset_class=asset_class,
            timeframe=timeframe,
            bar=bar,
            data_quality=data_quality,
        )

    def evaluate(
        self,
        *,
        instrument_id: str,
        symbol: str,
        asset_class: str,
        timeframe: str,
        bar,
        data_quality: str | None = None,
    ) -> dict[str, Any] | None:
        """Evaluate one CLOSED bar. Returns the recorded signal, or None.

        This is synchronous on purpose: it performs HTTP reads and writes, so
        the caller runs it in a thread instead of blocking the event loop.
        """
        binding = self.binding_for(asset_class, timeframe)
        if binding is None:
            return None

        key = (instrument_id, timeframe)
        bar_ts = _utc(bar.ts)
        close = float(bar.close)
        bar_close = bar_ts + timeframe_delta(timeframe)

        # The builder emits a closed bar once. A replay (shutdown flush after
        # a reconnect) must not re-evaluate the same bar.
        if self._last_bar.get(key) == bar_ts:
            return None

        core_bar = CoreBar(
            ts=bar_ts,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=close,
            volume=float(getattr(bar, "volume", 0.0) or 0.0),
        )

        engine = self._engines.get(key)
        if engine is None or self._is_gapped(engine, bar_ts, timeframe):
            engine = self._load_engine(
                instrument_id, symbol, timeframe, binding.params
            )
            if engine is None:
                return None
            self._engines[key] = engine
            self._strategies[key] = build(binding.template_id, binding.params)
            last_ts = _utc(engine.bars[-1].ts)
            if last_ts < bar_ts:
                engine.add_bar(core_bar)
            elif last_ts > bar_ts:
                # The stored history is ahead of the bar being evaluated.
                # That is a clock or ordering bug, not a signal.
                log.warning(
                    "signal engine: stored bars for %s %s are newer than the "
                    "closed bar being evaluated; skipping",
                    symbol,
                    timeframe,
                )
                return None
        else:
            engine.add_bar(core_bar)

        strategy = self._strategies[key]
        signal = engine.evaluate(
            strategy, position=Position(side="flat", entry_price=0.0, size=0.0)
        )
        self._last_bar[key] = bar_ts

        if signal is None:
            return None

        previous = self._last_side.get(key)
        self._last_side[key] = signal.side

        # A flat every bar is noise, not advice. Record the transition out of
        # a position once; a strategy with no opinion writes nothing at all.
        if signal.side == "flat" and previous != "buy" and previous != "sell":
            return None

        signal_key_value = signal_key(
            symbol, timeframe, signal.rule_id, bar_close, binding.version_id
        )
        expires_at = bar_close + timeframe_delta(timeframe) * 2

        payload = {
            "p_instrument_id": instrument_id,
            "p_timeframe": timeframe,
            "p_side": signal.side,
            "p_entry_price": close,
            "p_rule_id": signal.rule_id,
            "p_bar_close_time": bar_close.isoformat(),
            "p_data_as_of": bar_close.isoformat(),
            "p_signal_key": signal_key_value,
            "p_entry_low": signal.entry_low,
            "p_entry_high": signal.entry_high,
            "p_stop_price": signal.stop_price,
            "p_target_price": signal.target_price,
            "p_target_2_price": signal.target_2_price,
            "p_confidence": signal.confidence,
            "p_version_id": binding.version_id,
            "p_odds_band": {},
            "p_notes": {
                "template_id": binding.template_id,
                "strategy_name": binding.strategy_name,
                "bar_ts": bar_ts.isoformat(),
                "close": close,
                **dict(signal.notes or {}),
            },
            "p_expires_at": expires_at.isoformat(),
        }

        signal_id = self.writer.record_signal(payload)
        if signal_id is None:
            return None

        log.info(
            "signal %s %s %s %s @ %s (rule=%s)",
            signal.side.upper(),
            symbol,
            timeframe,
            binding.template_id,
            close,
            signal.rule_id,
        )

        self._file_alert(
            binding=binding,
            symbol=symbol,
            instrument_id=instrument_id,
            timeframe=timeframe,
            side=signal.side,
            rule_id=signal.rule_id,
            close=close,
            signal_id=signal_id,
            signal_key_value=signal_key_value,
            bar_close=bar_close,
            data_quality=data_quality,
        )

        return {
            "signal_id": signal_id,
            "side": signal.side,
            "rule_id": signal.rule_id,
            "signal_key": signal_key_value,
        }

    # ------------------------------------------------------------- helpers --

    def _is_gapped(self, engine: Engine, bar_ts: datetime, timeframe: str) -> bool:
        if not engine.bars:
            return True
        last = _utc(engine.bars[-1].ts)
        gap = bar_ts - last
        return gap > timeframe_delta(timeframe) * _MAX_GAP_BARS or gap < timedelta(0)

    def _load_engine(
        self,
        instrument_id: str,
        symbol: str,
        timeframe: str,
        params: dict[str, Any] | None = None,
    ) -> Engine | None:
        try:
            rows = self.writer.fetch_bars(instrument_id, timeframe, LOOKBACK)
        except Exception as exc:  # noqa: BLE001
            log.warning("bar load failed for %s %s: %s", symbol, timeframe, exc)
            return None
        if not rows:
            log.info(
                "signal engine: no closed bars for %s %s yet; run a backfill job",
                symbol,
                timeframe,
            )
            return None

        # The engine computes the indicator periods THIS candidate asks for.
        # A champion whose parameters differ from the defaults would
        # otherwise never see its own indicator and would never trade.
        engine = Engine.for_strategy(symbol, timeframe, params or {})
        engine.add_bars([_core_bar(row) for row in rows])
        return engine

    def _file_alert(
        self,
        *,
        binding: Binding,
        symbol: str,
        instrument_id: str,
        timeframe: str,
        side: str,
        rule_id: str,
        close: float,
        signal_id: str,
        signal_key_value: str,
        bar_close: datetime,
        data_quality: str | None,
    ) -> None:
        """Publish the alert row. Delivery is a separate concern.

        The bot and the dashboard both read `alerts`; the signal is already
        durable in `signals`, so a failed alert must never roll it back.
        """
        owner = self.owner_id()
        if owner is None:
            return

        # If the user has linked a Telegram chat, the alert is destined for
        # Telegram. The channel is fixed at filing time and never drifts.
        channel = "in_app"
        try:
            if self.writer.fetch_telegram_chat(owner) is not None:
                channel = "telegram"
        except Exception as exc:  # noqa: BLE001
            log.warning("could not resolve the Telegram chat for %s: %s", owner, exc)

        try:
            self.writer.fire_alert(
                user_id=owner,
                kind="signal",
                rule_id=rule_id,
                title=f"{side.upper()} {symbol} · {timeframe}",
                body=(
                    f"{binding.strategy_name} v{binding.version} fired "
                    f"{rule_id} at {close:g} on the closed {timeframe} bar."
                ),
                severity="info",
                payload={
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "side": side,
                    "signal_id": signal_id,
                    "signal_key": signal_key_value,
                    "bar_close_time": bar_close.isoformat(),
                    "price": close,
                    "data_quality": data_quality,
                },
                dedupe_key=signal_key_value,
                channel=channel,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("signal alert filing failed for %s: %s", symbol, exc)


__all__ = ["Binding", "SignalEngine", "timeframe_delta"]
