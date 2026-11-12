"""The live worker main loop.

Responsibilities, in priority order:

  1. Stay alive and report health. The freshness gate refuses every ticket
     when the heartbeat goes stale, so a silent worker is worse than a
     crashed one: it must either beat or say it is down.
  2. Maintain the latest quote per instrument.
  3. Build closed bars and publish them.
  4. Push a throttled Realtime bundle so the dashboard updates.
  5. Consume queued jobs (news refresh, backfill, retention).

It does NOT:
  * place orders (no write credentials exist)
  * size positions (the Postgres RPC does that)
  * promote strategies (only the owner can)

Run it with:  python -m worker.main
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):  # allow `python worker/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.bars import BarBuilder
from worker.calendar import build_catalysts, fetch_events
from worker.calendar.finnhub import week_window
from worker.config import AppConfig
from worker.connectors import (
    AlpacaStream,
    BinanceStream,
    OandaStream,
    Trade,
    canonical_symbol as _canonical_symbol,
)
from worker.connectors.alpaca import fetch_bars as fetch_alpaca_bars
from worker.connectors.binance import fetch_bars as fetch_binance_bars
from worker.connectors.oanda import fetch_bars as fetch_oanda_bars
from worker.ai.mutation import propose_mutation
from worker.ai.providers import ProviderRouter
from worker.ai.quota import QuotaMonitor
from worker.ai.sidecar import AISidecar
from worker.contracts import compare_with_previous, run_checks
from worker.signals import SignalEngine
from worker.supabase_client import SupabaseWriter, iso, utcnow
from worker.telegram import TelegramDelivery, TelegramSender

log = logging.getLogger("worker")

# How often the provider contract check runs. Six hours is often enough to
# notice a change and rare enough to cost nothing: one ticker, one trade,
# one account.
CONTRACT_CHECK_INTERVAL_S = 6 * 60 * 60


def configure_logging(level: str) -> None:
    # Logs are stamped in UTC. The default formatter uses LOCAL time while
    # printing a "Z", which is a lie the reader cannot see.
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # The websockets library is chatty at INFO.
    logging.getLogger("websockets").setLevel(logging.WARNING)


class LiveWorker:
    """The engine."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.writer = SupabaseWriter(config.supabase)
        self.builder = BarBuilder()
        self.streams: dict[str, object] = {}
        self.quotes: dict[str, dict] = {}
        # The first price seen this run, per symbol. The change percentage is
        # measured against it.
        self.session_open: dict[str, float] = {}
        self.instrument_ids: dict[str, str] = {}
        self.instrument_meta: dict[str, dict] = {}
        # Which venue produced the latest event for a symbol. Used when a bar
        # is flushed at shutdown, so its source label is still correct.
        self.venue_by_symbol: dict[str, str] = {}
        self.quota = QuotaMonitor()
        self.signals = SignalEngine(self.writer)
        self.telegram = TelegramDelivery(self.writer, TelegramSender.from_env())
        self._stop = asyncio.Event()
        self.bars_published = 0
        self.quotes_published = 0
        self.started_at = datetime.now(timezone.utc)
        # When the instrument registry was last read. A registry can go stale
        # if the database is rebuilt underneath us.
        self.registry_loaded_at = datetime.now(timezone.utc)
        self.signals_loaded_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------- startup --

    async def load_instruments(self) -> None:
        """Load the instrument registry so we know the uuid for each symbol."""
        rows = self.writer.fetch_instruments()
        for row in rows:
            symbol = str(row["symbol"]).upper()
            self.instrument_ids[symbol] = row["id"]
            self.instrument_meta[symbol] = row
        self.registry_loaded_at = utcnow()
        log.info("loaded %d instruments: %s", len(rows), ", ".join(sorted(self.instrument_ids)))

    async def wait_for_database(self, timeout_s: float = 90.0) -> bool:
        """Wait for the database before the first query.

        The worker and the database start together in the local stack, and
        the worker won that race often enough to matter: it asked for the
        instrument registry before the database was listening, got a
        connection refused, and started with ZERO instruments. It then
        streamed prices it could not name, and every screen showed blanks.

        A worker that starts before its database is a worker with no
        universe. Waiting is the difference between "the feed is down" and
        "the app does not know what a symbol is".
        """
        deadline = time.monotonic() + timeout_s
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                rows = await asyncio.to_thread(self.writer.fetch_instruments)
                if rows:
                    log.info("database is ready (%d instruments)", len(rows))
                    return True
                log.warning("the database answered but the universe is empty")
            except Exception as exc:  # noqa: BLE001
                if attempt == 1 or attempt % 5 == 0:
                    log.info(
                        "waiting for the database (attempt %d): %s", attempt, exc
                    )
            await asyncio.sleep(2.0)
        log.error(
            "the database was still not reachable after %.0fs; starting anyway",
            timeout_s,
        )
        return False

    def open_streams(self) -> None:
        """Open every configured venue. A missing credential skips the venue."""
        if "binance" in self.config.enabled_venues:
            stream = BinanceStream(
                symbols=self.config.binance.symbols,
                base_url=self.config.binance.websocket_base,
            )
            self.streams["binance"] = stream
            log.info("binance stream opened for %s", ", ".join(self.config.binance.symbols))

        if "alpaca" in self.config.enabled_venues:
            if not self.config.alpaca.configured:
                log.warning(
                    "alpaca is enabled but ALPACA_KEY_ID/ALPACA_SECRET_KEY are "
                    "not set; skipping the venue"
                )
            else:
                stream = AlpacaStream(
                    symbols=self.config.alpaca.symbols,
                    key_id=self.config.alpaca.key_id,
                    secret_key=self.config.alpaca.secret_key,
                    websocket_url=self.config.alpaca.websocket_url,
                )
                self.streams["alpaca"] = stream
                log.info(
                    "alpaca stream opened for %s (IEX, partial)",
                    ", ".join(self.config.alpaca.symbols),
                )

        if "oanda" in self.config.enabled_venues:
            if not self.config.oanda.configured:
                log.warning(
                    "oanda is enabled but OANDA_TOKEN/OANDA_ACCOUNT_ID are not "
                    "set; skipping the venue"
                )
            else:
                stream = OandaStream(
                    instruments=self.config.oanda.symbols,
                    token=self.config.oanda.token,
                    account_id=self.config.oanda.account_id,
                    stream_url=self.config.oanda.practice_stream,
                )
                self.streams["oanda"] = stream
                log.info(
                    "oanda pricing stream opened for %s (practice, indicative)",
                    ", ".join(self.config.oanda.symbols),
                )

    # ------------------------------------------------------------- trading --

    async def handle_trade(
        self,
        symbol: str,
        price: float,
        size: float,
        ts: datetime,
        *,
        venue: str = "binance",
        bid: float | None = None,
        ask: float | None = None,
    ) -> None:
        """Process one trade: update the quote, build bars, publish."""
        symbol = canonical_symbol(symbol)
        instrument_id = self.instrument_ids.get(symbol)
        if instrument_id is None:
            # The symbol is not in our universe. We still track the price for
            # the dashboard but we do not build bars or signal on it.
            pass

        self.venue_by_symbol[symbol] = venue
        # The first price we see for a symbol in this run is the reference
        # for the change percentage. It is a session change, not a 24h
        # change, and the label in the UI says so.
        session_open = self.session_open.get(symbol)
        if session_open is None:
            session_open = price
            self.session_open[symbol] = price
        change_pct = (
            (price - session_open) / session_open if session_open else None
        )

        self.quotes[symbol] = {
            "price": price,
            "size": size,
            "ts": ts,
            "bid": bid,
            "ask": ask,
            "change_pct": change_pct,
            "volume": None,
        }

        # Publish the quote. One upsert per symbol, throttled below.
        if instrument_id is not None:
            await self._publish_quote(
                instrument_id,
                symbol,
                price,
                size,
                ts,
                venue,
                bid=bid,
                ask=ask,
                change_pct=change_pct,
            )

        # Build bars.
        closed = self.builder.on_trade(symbol, price, size, ts, self.config.worker.timeframes)
        for bar in closed:
            await self._publish_bar(instrument_id, bar, venue)

    async def handle_event(self, trade: Trade) -> None:
        """Process one normalised connector event."""
        await self.handle_trade(
            trade.symbol,
            trade.price,
            trade.size,
            trade.ts,
            venue=trade.venue or "unknown",
            bid=trade.bid,
            ask=trade.ask,
        )

    async def _publish_quote(
        self,
        instrument_id: str,
        symbol: str,
        price: float,
        size: float,
        ts: datetime,
        venue: str,
        *,
        bid: float | None = None,
        ask: float | None = None,
        change_pct: float | None = None,
    ) -> None:
        meta = self.instrument_meta.get(symbol, {})

        def publish(target_id: str) -> None:
            self.writer.upsert_quote(
                target_id,
                bid=bid,
                ask=ask,
                last=price,
                last_size=size,
                change_pct=change_pct,
                volume=None,
                source=source_label(venue),
                official=meta.get("data_quality") == "official",
                as_of=ts,
            )

        if await self._write_with_reload(symbol, publish, instrument_id, "quote"):
            self.quotes_published += 1

    async def _publish_bar(self, instrument_id: str | None, bar, venue: str) -> None:
        if instrument_id is None:
            return
        symbol = bar.instrument
        meta = self.instrument_meta.get(symbol, {})

        def publish(target_id: str) -> None:
            self.writer.insert_bar(
                target_id,
                timeframe=bar.timeframe,
                ts=bar.ts,
                open_=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                source=source_label(venue),
                official=meta.get("data_quality") == "official",
                as_of=bar.closed_at,
            )

        if await self._write_with_reload(symbol, publish, instrument_id, "bar"):
            self.bars_published += 1
            log.info(
                "bar closed %s %s o=%s h=%s l=%s c=%s v=%s",
                bar.instrument,
                bar.timeframe,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            )
            await self._evaluate_bar(instrument_id, symbol, bar)

    async def _evaluate_bar(self, instrument_id: str | None, symbol: str, bar) -> None:
        """Run the promoted champion on the bar that just closed.

        Nothing happens when no champion exists for the asset class and
        timeframe. That is the correct answer, not a failure.
        """
        if instrument_id is None:
            return
        meta = self.instrument_meta.get(symbol, {})
        asset_class = str(meta.get("asset_class") or "")
        if not asset_class or self.signals.binding_for(asset_class, bar.timeframe) is None:
            return
        try:
            await asyncio.to_thread(
                self.signals.evaluate,
                instrument_id=instrument_id,
                symbol=symbol,
                asset_class=asset_class,
                timeframe=bar.timeframe,
                bar=bar,
                data_quality=meta.get("data_quality"),
            )
        except Exception as exc:  # noqa: BLE001
            # A broken strategy must never take down the price stream.
            log.warning("signal evaluation failed for %s %s: %s", symbol, bar.timeframe, exc)

    def venue_meta(self) -> dict:
        """What the feed-status banner needs to tell the truth.

        `configured=false` means the venue was never connected: that is a
        setup state, not an outage, and the UI must not call it "stale".
        """
        quality = {"binance": "official", "alpaca": "partial", "oanda": "indicative"}
        specs = {
            "binance": (self.config.binance, True),
            "alpaca": (self.config.alpaca, self.config.alpaca.configured),
            "oanda": (self.config.oanda, self.config.oanda.configured),
        }
        meta: dict[str, dict] = {}
        for name, (cfg, configured) in specs.items():
            stream = self.streams.get(name)
            meta[name] = {
                "enabled": name in self.config.enabled_venues,
                "configured": bool(configured),
                "connected": bool(getattr(stream, "connected", False)),
                "symbols": len(getattr(cfg, "symbols", ()) or ()),
                "data_quality": quality[name],
            }
        return meta

    async def _write_with_reload(
        self,
        symbol: str,
        publish,
        instrument_id: str,
        what: str,
    ) -> bool:
        """Write once. If the instrument registry has gone stale, reload and retry.

        The registry is read at startup and cached. If the database is ever
        rebuilt underneath us, those ids are stale and every write fails on a
        foreign key. Reloading the registry and trying once turns a dead
        engine into a recovered one.
        """
        try:
            publish(instrument_id)
            return True
        except Exception as exc:  # noqa: BLE001
            if "foreign key" not in str(exc).lower():
                log.warning("%s publish failed for %s: %s", what, symbol, exc)
                return False

            log.warning(
                "%s publish failed for %s: the instrument registry is stale; reloading",
                what,
                symbol,
            )
            try:
                await self.load_instruments()
            except Exception as reload_exc:  # noqa: BLE001
                log.warning("instrument reload failed: %s", reload_exc)
                return False

            fresh = self.instrument_ids.get(symbol)
            if fresh is None:
                log.warning("%s publish skipped: %s is no longer in the registry", what, symbol)
                return False
            try:
                publish(fresh)
                return True
            except Exception as retry_exc:  # noqa: BLE001
                log.warning("%s publish failed for %s: %s", what, symbol, retry_exc)
                return False

    # ---------------------------------------------------------- the loops --

    async def run_venue(self, name: str) -> None:
        """Consume one venue's stream forever."""
        stream = self.streams[name]
        async for trade in stream.trades():
            if self._stop.is_set():
                break
            await self.handle_event(trade)

    async def run_heartbeat(self) -> None:
        """Write the heartbeat. The freshness gate depends on this row."""
        while not self._stop.is_set():
            try:
                self.writer.heartbeat(
                    self.config.worker.worker_id,
                    version=_version(),
                    streams={
                        name: {
                            "connected": stream.connected,
                            "reconnects": stream.reconnects,
                        }
                        for name, stream in self.streams.items()
                    },
                    lag_ms={
                        name: stream.lag_ms for name, stream in self.streams.items()
                    },
                    meta={
                        "quotes_published": self.quotes_published,
                        "bars_published": self.bars_published,
                        "uptime_seconds": int(
                            (utcnow() - self.started_at).total_seconds()
                        ),
                        "ai_quota": self.quota.state(),
                        "venues": self.venue_meta(),
                        "telegram": {"configured": self.telegram.configured},
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat failed: %s", exc)

            # Keep the instrument registry fresh. The universe can change
            # while the worker is running, and a stale id means a failed
            # write rather than a wrong price.
            if (utcnow() - self.registry_loaded_at).total_seconds() > 300:
                try:
                    await self.load_instruments()
                except Exception as exc:  # noqa: BLE001
                    log.warning("instrument refresh failed: %s", exc)

            # The champion can change while the worker runs. Refresh it on the
            # same cadence as the instrument registry; the old binding keeps
            # working until the new one is read.
            if (utcnow() - self.signals_loaded_at).total_seconds() > 300:
                try:
                    await asyncio.to_thread(self.signals.refresh_registry)
                    self.signals_loaded_at = utcnow()
                except Exception as exc:  # noqa: BLE001
                    log.warning("champion refresh failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.config.worker.heartbeat_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def run_broadcast(self) -> None:
        """Push a throttled Realtime bundle.

        One bundle every 5 seconds containing every symbol. That is roughly
        518k messages a month against a 2M free-tier quota.
        """
        interval = self.config.worker.realtime_bundle_seconds
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break  # stop was set
            except asyncio.TimeoutError:
                pass

            payload = self.build_broadcast_payload()
            self.writer.broadcast("ticks", "bundle", payload)

    def build_broadcast_payload(self) -> dict:
        """The tick bundle.

        Keyed by INSTRUMENT ID, not by symbol. The dashboard keys its quote
        map by instrument id, so a bundle keyed by symbol would look alive
        and never move.
        """
        return {
            "ts": iso(utcnow()),
            "quotes": {
                instrument_id: {
                    "symbol": symbol,
                    "price": q["price"],
                    "change_pct": q["change_pct"],
                    "as_of": iso(q["ts"]),
                }
                for symbol, q in self.quotes.items()
                if (instrument_id := self.instrument_ids.get(symbol)) is not None
            },
        }

    async def run_calendar(self) -> None:
        """Keep the economic calendar fresh.

        The calendar is the clock the ticket function uses to warn about
        event risk. It changes on its own schedule, so it is refreshed on a
        timer rather than once at startup.
        """
        interval = self.config.worker.calendar_refresh_hours * 3600
        while not self._stop.is_set():
            try:
                await self._refresh_calendar()
            except Exception as exc:  # noqa: BLE001
                log.warning("calendar refresh failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def _refresh_calendar(self) -> None:
        """Replace the forward calendar window with what is actually true."""
        api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
        if not api_key:
            log.info("no FINNHUB_API_KEY; the economic calendar stays empty")
            return

        start, end = week_window()
        events = await asyncio.to_thread(fetch_events, api_key, start, end)
        instruments = list(self.instrument_meta.values())
        oanda_symbols = self.config.oanda.symbols if self.config.oanda else ()
        rows = build_catalysts(events, instruments, oanda_symbols)

        # Replace, never append: a cancelled release must stop warning.
        self.writer.delete_catalysts("economic", utcnow())
        self.writer.insert_catalysts(rows)
        log.info(
            "calendar refreshed: %d events -> %d catalysts", len(events), len(rows)
        )

    async def run_telegram(self) -> None:
        """Deliver the alerts that were filed for Telegram.

        The worker is the always-on tier, so it owns outbound delivery: a
        signal that fires at 3am must reach the phone even if the web tier is
        asleep.

        NOTE: this task must NEVER return early. The main loop waits for
        FIRST_COMPLETED, so a background task that returns takes the whole
        worker down with it. That is exactly what happened when this task
        returned early because no bot token was configured: the worker lived
        for one second and then stopped, and every screen said the engine
        was down.
        """
        if not self.telegram.configured:
            log.info("no TELEGRAM_BOT_TOKEN; outbound alerts stay in the app")
            await self._stop.wait()
            return
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.telegram.deliver_once)
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram delivery failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                continue

    async def run_jobs(self) -> None:
        while not self._stop.is_set():
            jobs = self.writer.claim_jobs(self.config.worker.worker_id, limit=3)
            for job in jobs:
                try:
                    await self._run_job(job)
                    self.writer.finish_job(job["id"], True, {"ok": True})
                except Exception as exc:  # noqa: BLE001
                    log.warning("job %s failed: %s", job.get("kind"), exc)
                    self.writer.finish_job(job["id"], False, {}, str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                continue

    async def _run_job(self, job: dict) -> None:
        kind = job.get("kind")
        if kind == "retention":
            await self._run_retention(job)
        elif kind == "backup":
            log.info("backup job acknowledged: %s", job.get("payload"))
        elif kind == "backfill":
            await self._run_backfill(job)
        elif kind == "calendar_refresh":
            await self._refresh_calendar()
        elif kind == "ai_mutation":
            await self._run_ai_mutation(job)
        elif kind == "provider_contract_check":
            await self._check_provider_contracts()
        else:
            log.info("no handler for job kind '%s'; acknowledging", kind)

    async def seed_history_if_empty(self) -> None:
        """Fetch history for an instrument that has none.

        A fresh install has an empty database, so the chart is blank, the
        indicators are cold, and the research runner has nothing to read.
        The user should not have to know that a job has to be queued to fix
        it.

        The seed is skipped when the daily bars are already there, so this
        costs nothing on a normal start.
        """
        missing: list[str] = []
        for symbol, instrument_id in self.instrument_ids.items():
            try:
                rows = await asyncio.to_thread(
                    self.writer.fetch_bars, instrument_id, "1d", 2
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("could not read the bars for %s: %s", symbol, exc)
                continue
            if not rows:
                missing.append(symbol)

        if not missing:
            return

        log.info(
            "seeding history for %d instrument(s) with no bars: %s",
            len(missing),
            ", ".join(sorted(missing)),
        )
        await self._run_backfill({"payload": {"symbols": missing}})

    async def run_contract_checks(self) -> None:
        """Check every provider's response shape, every few hours.

        A market data API can change what it returns without saying so,
        and the symptom is a screen that stops updating and never explains
        itself. The key set is hashed, so a change is DETECTED, not
        guessed at.

        This task must never return early. The main loop treats a task
        that returns as a reason to stop the worker.
        """
        while True:
            try:
                await self._check_provider_contracts()
            except Exception as exc:  # noqa: BLE001
                log.warning("contract check failed: %s", exc)
            await asyncio.sleep(CONTRACT_CHECK_INTERVAL_S)

    async def _check_provider_contracts(self) -> None:
        """Run one round of contract checks and record the results."""
        checks = await asyncio.to_thread(run_checks, self.config)
        for check in checks:
            previous = await asyncio.to_thread(
                self.writer.fetch_last_contract, check.provider
            )
            check = compare_with_previous(check, previous)
            await asyncio.to_thread(
                self.writer.record_provider_contract, check.as_row()
            )
            log.info(
                "contract %s %s ok=%s latency=%dms %s",
                check.provider,
                check.endpoint,
                check.ok,
                check.latency_ms,
                check.note or "",
            )

    async def _run_ai_mutation(self, job: dict) -> None:
        """Tier A: the AI proposes ONE parameter change per promoted strategy.

        It proposes. It does not decide. Every proposal consumes a trial the
        moment it is made, so the deflated Sharpe ratio counts the search and
        the gate can see how much of the edge was luck.
        """
        champions = await asyncio.to_thread(self.writer.fetch_champion_params)
        if not champions:
            log.info("ai_mutation: there is no promoted strategy to mutate")
            return

        router = ProviderRouter()
        for champion in champions:
            provider = router.for_job("mutation")
            if not provider.configured:
                log.info("ai_mutation: no AI provider is configured")
                return

            sidecar = AISidecar(
                provider=provider,
                capability="propose_mutation",
                quota=self.quota,
                log_sink=self.writer.insert_ai_run,
                data_class="public",
            )
            mutation = await asyncio.to_thread(
                propose_mutation,
                sidecar,
                str(champion["template_id"]),
                champion["params"],
            )
            if mutation is None:
                continue

            trials = self.writer.count_trials() + 1
            row = {
                "tier": "mutation",
                "parent_version_id": champion["version_id"],
                "proposed_params": mutation.params,
                "model_id": getattr(provider, "model_id", "unknown"),
                "seed": sidecar.seed,
                "n_trials_after": trials,
                "status": "proposed",
                "sandbox_stage": "proposed",
                "sandbox_log": (
                    f"{mutation.parameter} -> {mutation.value}: {mutation.reason}"
                ),
            }
            proposal_id = await asyncio.to_thread(
                self.writer.insert_strategy_proposal, row
            )
            log.info(
                "ai_mutation: proposed %s=%s for %s (%s), trial %d",
                mutation.parameter,
                mutation.value,
                champion.get("strategy_name"),
                proposal_id,
                trials,
            )

    async def _run_backfill(self, job: dict) -> None:
        """Seed history so the indicators are warm on the first day.

        The bars come from the same venue that will stream them live, and
        carry that venue's data-quality label. This is not a backtest: it is
        closed bars, fetched once, so nothing has to guess what yesterday
        looked like.
        """
        payload = job.get("payload") or {}
        timeframes = list(payload.get("timeframes") or self.config.worker.timeframes)
        only = set(payload.get("symbols") or [])
        limits = {"1m": 500, "5m": 500, "1h": 1000, "1d": 2500}

        total = 0
        for symbol, instrument_id in self.instrument_ids.items():
            if only and symbol not in only:
                continue
            meta = self.instrument_meta.get(symbol, {})
            venue = str(meta.get("venue") or "")
            fetcher = self._history_fetcher(venue, symbol)
            if fetcher is None:
                log.info(
                    "backfill: skipping %s (no credentials for venue '%s')", symbol, venue
                )
                continue

            for timeframe in timeframes:
                try:
                    bars = await asyncio.to_thread(
                        fetcher, timeframe, limits.get(timeframe, 500)
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("backfill failed for %s %s: %s", symbol, timeframe, exc)
                    continue
                if not bars:
                    continue
                rows = [
                    self._bar_row(instrument_id, symbol, meta, timeframe, bar)
                    for bar in bars
                ]
                try:
                    self.writer.insert_bars(rows)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "backfill insert failed for %s %s: %s", symbol, timeframe, exc
                    )
                    continue
                total += len(rows)
                log.info(
                    "backfill: %s %s %d bars", symbol, timeframe, len(rows)
                )
                # Warm the signal engine immediately. Without this a fresh
                # install would answer "no signal" until the next bar close,
                # which for a 1d champion is tomorrow.
                await asyncio.to_thread(
                    self.signals.evaluate_latest,
                    instrument_id=instrument_id,
                    symbol=symbol,
                    asset_class=str(meta.get("asset_class") or ""),
                    timeframe=timeframe,
                    data_quality=meta.get("data_quality"),
                )

        log.info("backfill complete: %d bars", total)

    def _history_fetcher(self, venue: str, symbol: str):
        """A callable (timeframe, limit) -> bars for this venue, or None."""
        if venue == "binance":
            base = self.config.binance.rest_base

            def fetch_binance(timeframe: str, limit: int) -> list[dict]:
                return fetch_binance_bars(
                    symbol, timeframe=timeframe, limit=limit, base_url=base
                )

            return fetch_binance

        if venue == "alpaca":
            if not self.config.alpaca or not self.config.alpaca.configured:
                return None
            key_id = self.config.alpaca.key_id
            secret_key = self.config.alpaca.secret_key
            base = self.config.alpaca.data_url

            def fetch_alpaca(timeframe: str, limit: int) -> list[dict]:
                return fetch_alpaca_bars(
                    symbol,
                    timeframe=timeframe,
                    limit=limit,
                    key_id=key_id,
                    secret_key=secret_key,
                    data_url=base,
                )

            return fetch_alpaca

        if venue == "oanda":
            if not self.config.oanda or not self.config.oanda.configured:
                return None
            token = self.config.oanda.token
            base = self.config.oanda.practice_rest
            instrument = self._venue_symbol(symbol, venue)

            def fetch_oanda(timeframe: str, limit: int) -> list[dict]:
                return fetch_oanda_bars(
                    instrument,
                    timeframe=timeframe,
                    count=limit,
                    token=token,
                    rest_url=base,
                )

            return fetch_oanda

        return None

    def _venue_symbol(self, symbol: str, venue: str) -> str:
        """The symbol in the venue's own spelling."""
        if venue == "oanda" and self.config.oanda:
            for candidate in self.config.oanda.symbols:
                if canonical_symbol(candidate) == symbol:
                    return candidate
        return symbol

    def _bar_row(
        self,
        instrument_id: str,
        symbol: str,
        meta: dict,
        timeframe: str,
        bar: dict,
    ) -> dict:
        """One bar in the shape the `bars` table stores."""
        closed_at = bar["ts"] + timedelta(seconds=_timeframe_seconds(timeframe))
        return {
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "ts": iso(bar["ts"]),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar.get("volume"),
            "is_closed": True,
            "source": meta.get("primary_source") or "unknown",
            "official": meta.get("data_quality") == "official",
            "as_of": iso(closed_at),
        }

    async def _run_retention(self, job: dict) -> None:
        """Delete bars past their retention window.

        Free tier gives us 500MB. Retention is enforced, not hoped for.
        """
        import urllib.request

        rules = {
            "1m": "14 days",
            "5m": "90 days",
            "1h": "2 years",
        }
        url = self.config.supabase.url.rstrip("/")
        headers = {
            "apikey": self.config.supabase.service_role_key,
            "Authorization": f"Bearer {self.config.supabase.service_role_key}",
            "Content-Type": "application/json",
        }
        for timeframe, window in rules.items():
            body = (
                f'{{"timeframe":"{timeframe}","ts":"lt.now() - interval \'{window}\'"}}'
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{url}/rest/v1/bars?timeframe=eq.{timeframe}", data=body, headers=headers
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    resp.read()
                log.info("retention: deleted %s bars older than %s", timeframe, window)
            except Exception as exc:  # noqa: BLE001
                log.warning("retention failed for %s: %s", timeframe, exc)

    # ------------------------------------------------------------- the run --

    async def run(self) -> None:
        log.info("worker %s starting", self.config.worker.worker_id)
        await self.wait_for_database()
        await self.load_instruments()
        await asyncio.to_thread(self.signals.refresh_registry)
        self.signals_loaded_at = utcnow()
        self.open_streams()
        await self.seed_history_if_empty()

        tasks = [asyncio.create_task(self.run_heartbeat())]
        tasks.append(asyncio.create_task(self.run_broadcast()))
        tasks.append(asyncio.create_task(self.run_jobs()))
        tasks.append(asyncio.create_task(self.run_calendar()))
        tasks.append(asyncio.create_task(self.run_telegram()))
        tasks.append(asyncio.create_task(self.run_contract_checks()))
        for name in self.streams:
            tasks.append(asyncio.create_task(self.run_venue(name)))

        stop_task = asyncio.create_task(self._stop.wait())

        # A background task that RETURNS must not take the worker down. The
        # first version of this loop waited for FIRST_COMPLETED, so any task
        # that finished early ended the whole worker: the Telegram task
        # returned immediately when no bot token was configured, and the
        # worker died one second after starting. Every screen then said the
        # engine was down, which was true, and completely misleading.
        #
        # A task that RAISES is a different matter: that is a broken
        # component, and the worker stops rather than pretending.
        alive = set(tasks)
        while not self._stop.is_set():
            done, alive = await asyncio.wait(
                [stop_task, *alive], return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done:
                break
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None:
                    log.error("task ended with an error: %s", exc)
                    self._stop.set()
                    break
                log.warning(
                    "a background task ended; the worker keeps running without it"
                )

        log.info("shutting down")
        self._stop.set()
        for task in alive:
            task.cancel()
        for task in done:
            if task is not stop_task and not task.cancelled():
                exc = task.exception()
                if exc:
                    log.error("task ended with error: %s", exc)

        # Close any in-progress bars so the last minute is not lost.
        for symbol in list(self.quotes):
            venue = self.venue_by_symbol.get(symbol, "binance")
            for bar in self.builder.flush(symbol, self.config.worker.timeframes):
                await self._publish_bar(self.instrument_ids.get(symbol), bar, venue)

        log.info(
            "worker stopped. quotes=%d bars=%d uptime=%ds",
            self.quotes_published,
            self.bars_published,
            int((utcnow() - self.started_at).total_seconds()),
        )

    def request_stop(self) -> None:
        self._stop.set()


def _version() -> str:
    from worker import __version__

    return __version__


def canonical_symbol(symbol: str) -> str:
    """Map a venue symbol to the instrument-registry symbol.

    Re-exported from the connector contract so that every module agrees on
    one spelling.
    """
    return _canonical_symbol(symbol)


def source_label(venue: str) -> str:
    """The provenance label stored on every quote and bar."""
    if venue in ("binance", "alpaca"):
        return f"{venue}_ws"
    if venue == "oanda":
        return "oanda_stream"
    return f"{venue}_stream"


def _timeframe_seconds(timeframe: str) -> int:
    """How long one bar lasts, in seconds."""
    return {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }.get(timeframe, 60)


async def main() -> None:
    config = AppConfig.from_env()
    configure_logging(config.worker.log_level)
    worker = LiveWorker(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
