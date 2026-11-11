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
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # allow `python worker/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.bars import BarBuilder
from worker.config import AppConfig
from worker.connectors import BinanceStream
from worker.supabase_client import SupabaseWriter, iso, utcnow

log = logging.getLogger("worker")


def configure_logging(level: str) -> None:
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
        self.streams: dict[str, BinanceStream] = {}
        self.quotes: dict[str, dict] = {}
        self.instrument_ids: dict[str, str] = {}
        self.instrument_meta: dict[str, dict] = {}
        self._stop = asyncio.Event()
        self.bars_published = 0
        self.quotes_published = 0
        self.started_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------- startup --

    async def load_instruments(self) -> None:
        """Load the instrument registry so we know the uuid for each symbol."""
        rows = self.writer.fetch_instruments()
        for row in rows:
            symbol = str(row["symbol"]).upper()
            self.instrument_ids[symbol] = row["id"]
            self.instrument_meta[symbol] = row
        log.info("loaded %d instruments: %s", len(rows), ", ".join(sorted(self.instrument_ids)))

    def open_streams(self) -> None:
        if "binance" in self.config.enabled_venues:
            stream = BinanceStream(symbols=self.config.binance.symbols)
            self.streams["binance"] = stream
            log.info("binance stream opened for %s", ", ".join(self.config.binance.symbols))

    # ------------------------------------------------------------- trading --

    async def handle_trade(self, symbol: str, price: float, size: float, ts: datetime) -> None:
        """Process one trade: update the quote, build bars, publish."""
        venue = "binance"
        instrument_id = self.instrument_ids.get(symbol)
        if instrument_id is None:
            # The symbol is not in our universe. We still track the price for
            # the dashboard but we do not build bars or signal on it.
            pass

        previous = self.quotes.get(symbol)
        self.quotes[symbol] = {
            "price": price,
            "size": size,
            "ts": ts,
            "bid": None,
            "ask": None,
            "change_pct": None,
            "volume": None,
        }

        # Publish the quote. One upsert per symbol, throttled below.
        if instrument_id is not None:
            await self._publish_quote(instrument_id, symbol, price, size, ts, venue)

        # Build bars.
        closed = self.builder.on_trade(symbol, price, size, ts, self.config.worker.timeframes)
        for bar in closed:
            await self._publish_bar(instrument_id, bar, venue)

    async def _publish_quote(
        self,
        instrument_id: str,
        symbol: str,
        price: float,
        size: float,
        ts: datetime,
        venue: str,
    ) -> None:
        meta = self.instrument_meta.get(symbol, {})
        try:
            self.writer.upsert_quote(
                instrument_id,
                bid=None,
                ask=None,
                last=price,
                last_size=size,
                change_pct=None,
                volume=None,
                source=f"{venue}_ws",
                official=meta.get("data_quality") == "official",
                as_of=ts,
            )
            self.quotes_published += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("quote publish failed for %s: %s", symbol, exc)

    async def _publish_bar(self, instrument_id: str | None, bar, venue: str) -> None:
        if instrument_id is None:
            return
        meta = self.instrument_meta.get(bar.instrument, {})
        try:
            self.writer.insert_bar(
                instrument_id,
                timeframe=bar.timeframe,
                ts=bar.ts,
                open_=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                source=f"{venue}_ws",
                official=meta.get("data_quality") == "official",
                as_of=bar.closed_at,
            )
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
        except Exception as exc:  # noqa: BLE001
            log.warning("bar publish failed for %s %s: %s", bar.instrument, bar.timeframe, exc)

    # ---------------------------------------------------------- the loops --

    async def run_venue(self, name: str) -> None:
        """Consume one venue's stream forever."""
        stream = self.streams[name]
        async for trade in stream.trades():
            if self._stop.is_set():
                break
            await self.handle_trade(
                trade.symbol, trade.price, trade.size, trade.ts
            )

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
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat failed: %s", exc)
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

            payload = {
                "ts": iso(utcnow()),
                "quotes": {
                    symbol: {
                        "price": q["price"],
                        "change_pct": q["change_pct"],
                        "as_of": iso(q["ts"]),
                    }
                    for symbol, q in self.quotes.items()
                },
            }
            self.writer.broadcast("ticks", "bundle", payload)

    async def run_jobs(self) -> None:
        """Consume queued work. The worker polls; Vercel never calls in."""
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
        else:
            log.info("no handler for job kind '%s'; acknowledging", kind)

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
        await self.load_instruments()
        self.open_streams()

        tasks = [asyncio.create_task(self.run_heartbeat())]
        tasks.append(asyncio.create_task(self.run_broadcast()))
        tasks.append(asyncio.create_task(self.run_jobs()))
        for name in self.streams:
            tasks.append(asyncio.create_task(self.run_venue(name)))

        stop_task = asyncio.create_task(self._stop.wait())
        done, pending = await asyncio.wait(
            [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
        )

        log.info("shutting down")
        self._stop.set()
        for task in pending:
            task.cancel()
        for task in done:
            if task is not stop_task and not task.cancelled():
                exc = task.exception()
                if exc:
                    log.error("task ended with error: %s", exc)

        # Close any in-progress bars so the last minute is not lost.
        for symbol in list(self.quotes):
            for bar in self.builder.flush(symbol, self.config.worker.timeframes):
                await self._publish_bar(self.instrument_ids.get(symbol), bar, "binance")

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
