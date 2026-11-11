"""Supabase access for the live worker.

The worker uses the service-role key, which bypasses RLS. That makes it the
single most privileged component in the system, so this module is deliberately
narrow: it exposes ONLY the writes the worker is allowed to make, and refuses
anything that touches sizing, promotion or execution.

The allowed write set:
    latest_quotes, bars, signals, news, news_features, positioning,
    worker_heartbeat, ai_runs, alerts, jobs

Everything else is off-limits. `_FORBIDDEN_TABLES` enforces that in code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .config import SupabaseConfig

log = logging.getLogger(__name__)

# The worker must never write to these. They hold the decisions.
_FORBIDDEN_TABLES = frozenset(
    {
        "champions",
        "gate_policy",
        "tickets",
        "user_profiles",
        "holdout_ledger",
        "performance",
        "backtests",
        "strategy_versions",
        "strategies",
        "audit_log",
    }
)


class ForbiddenWrite(RuntimeError):
    """Raised when the worker tries to write a table it must not touch."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


class SupabaseWriter:
    """Thin, guarded wrapper over the Supabase REST API."""

    def __init__(self, config: SupabaseConfig) -> None:
        self._url = config.url.rstrip("/")
        self._key = config.service_role_key
        self._headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    # ------------------------------------------------------------- guards --

    @staticmethod
    def _guard(table: str) -> None:
        if table in _FORBIDDEN_TABLES:
            raise ForbiddenWrite(
                f"The live worker must not write to '{table}'. "
                "That table holds a decision that only the owner or the "
                "research worker may make."
            )

    # -------------------------------------------------------------- posts --

    def _post(self, path: str, payload: dict[str, Any]) -> None:
        import urllib.error
        import urllib.request

        body = _json_bytes(payload)
        req = urllib.request.Request(
            f"{self._url}/rest/v1/{path}", data=body, headers=self._headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Supabase write failed ({path}): {exc.code} {detail}") from exc

    def _patch(self, path: str, payload: dict[str, Any]) -> None:
        import urllib.error
        import urllib.request

        body = _json_bytes(payload)
        req = urllib.request.Request(
            f"{self._url}/rest/v1/{path}", data=body, headers=self._headers, method="PATCH"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Supabase write failed ({path}): {exc.code} {detail}") from exc

    # ---------------------------------------------------------- the writes --

    def upsert_quote(
        self,
        instrument_id: str,
        *,
        bid: float | None,
        ask: float | None,
        last: float | None,
        last_size: float | None,
        change_pct: float | None,
        volume: float | None,
        source: str,
        official: bool,
        as_of: datetime,
    ) -> None:
        """Upsert the single latest quote row for an instrument."""
        self._guard("latest_quotes")
        self._post(
            "latest_quotes?on_conflict=instrument_id",
            {
                "instrument_id": instrument_id,
                "bid": bid,
                "ask": ask,
                "last": last,
                "last_size": last_size,
                "change_pct": change_pct,
                "volume": volume,
                "source": source,
                "official": official,
                "as_of": iso(as_of),
            },
        )

    def insert_bar(
        self,
        instrument_id: str,
        *,
        timeframe: str,
        ts: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float | None,
        source: str,
        official: bool,
        as_of: datetime,
    ) -> None:
        """Append one CLOSED bar. Idempotent via the primary key."""
        self._guard("bars")
        self._post(
            "bars",
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "ts": iso(ts),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "is_closed": True,
                "source": source,
                "official": official,
                "as_of": iso(as_of),
            },
        )

    def heartbeat(
        self,
        worker_id: str,
        *,
        version: str,
        streams: dict[str, Any],
        lag_ms: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Write the heartbeat the freshness gate depends on."""
        self._guard("worker_heartbeat")
        self._post(
            "worker_heartbeat?on_conflict=worker_id",
            {
                "worker_id": worker_id,
                "version": version,
                "last_seen_at": iso(utcnow()),
                "streams": streams,
                "lag_ms": lag_ms,
                "meta": meta or {},
            },
        )

    def record_signal(self, payload: dict[str, Any]) -> str | None:
        """Record a signal through the idempotent RPC.

        The signal_key makes a duplicate bar close a no-op rather than a
        second signal. Returns the signal id.
        """
        self._guard("signals")
        import urllib.request

        body = _json_bytes(payload)
        req = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/record_signal",
            data=body,
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
            return raw.strip('"') or None
        except Exception as exc:  # noqa: BLE001
            log.warning("record_signal failed: %s", exc)
            return None

    def log_ai_run(self, payload: dict[str, Any]) -> None:
        """Record every AI call. Nothing AI does happens without a row here."""
        self._guard("ai_runs")
        self._post("ai_runs", payload)

    def insert_news(self, rows: Sequence[dict[str, Any]]) -> None:
        self._guard("news")
        if rows:
            self._post("news", list(rows))

    def insert_news_features(self, rows: Sequence[dict[str, Any]]) -> None:
        self._guard("news_features")
        if rows:
            self._post("news_features", list(rows))

    def insert_positioning(self, rows: Sequence[dict[str, Any]]) -> None:
        self._guard("positioning")
        if rows:
            self._post("positioning", list(rows))

    def claim_jobs(self, worker_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Claim queued work. Uses FOR UPDATE SKIP LOCKED inside the RPC."""
        import urllib.request

        body = _json_bytes({"p_worker_id": worker_id, "p_limit": limit})
        req = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/claim_jobs",
            data=body,
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _json_loads(resp.read().decode("utf-8", "replace")) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("claim_jobs failed: %s", exc)
            return []

    def finish_job(
        self, job_id: str, ok: bool, result: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        body = _json_bytes(
            {"p_job_id": job_id, "p_ok": ok, "p_result": result or {}, "p_error": error}
        )
        import urllib.request

        req = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/finish_job",
            data=body,
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except Exception as exc:  # noqa: BLE001
            log.warning("finish_job failed: %s", exc)

    def broadcast(self, channel: str, event: str, payload: dict[str, Any]) -> bool:
        """Send a Realtime Broadcast message.

        Used for the throttled tick bundle. Returns False on failure: a failed
        broadcast must never take down the engine.
        """
        import urllib.error
        import urllib.request

        body = _json_bytes(
            {
                "messages": [
                    {
                        "topic": "realtime",
                        "event": event,
                        "payload": payload,
                        "private": False,
                    }
                ]
            }
        )
        req = urllib.request.Request(
            f"{self._url}/api/v1/realtime/broadcast",
            data=body,
            headers={**self._headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("broadcast failed: %s", exc)
            return False

    def fetch_instruments(self) -> list[dict[str, Any]]:
        """The instrument registry, so the worker knows the uuid per symbol."""
        import urllib.request

        req = urllib.request.Request(
            f"{self._url}/rest/v1/instruments?select=id,symbol,venue,asset_class,"
            f"in_universe,primary_source,data_quality&in_universe=eq.true",
            headers=self._headers,
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _json_loads(resp.read().decode("utf-8", "replace")) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch_instruments failed: %s", exc)
            return []


def _json_bytes(payload: Any) -> bytes:
    import json

    return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")


def _json_loads(raw: str) -> Any:
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
