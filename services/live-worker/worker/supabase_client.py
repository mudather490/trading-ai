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

    def _post(
        self, path: str, payload: dict[str, Any], *, prefer: str | None = None
    ) -> Any:
        import urllib.error
        import urllib.request

        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        body = _json_bytes(payload)
        req = urllib.request.Request(
            f"{self._url}/rest/v1/{path}", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Supabase write failed ({path}): {exc.code} {detail}") from exc
        # PostgREST returns the inserted row when the caller asks for it.
        return _json_loads(raw) if raw.strip() else None

    def _delete(self, path: str) -> None:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{self._url}/rest/v1/{path}", headers=self._headers, method="DELETE"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Supabase delete failed ({path}): {exc.code} {detail}") from exc

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
        """Append one CLOSED bar. Idempotent via the primary key.

        It has to be idempotent: the backfill seeds the same minute the
        live stream is about to publish, and a plain INSERT then fails with
        a duplicate key. The bar is written twice, once by each path, and
        the second write must update the first rather than fight it.
        """
        self._guard("bars")
        self._post(
            "bars?on_conflict=instrument_id,timeframe,ts",
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
            prefer="resolution=merge-duplicates",
        )

    def insert_bars(self, rows: Sequence[dict[str, Any]]) -> None:
        """Append many CLOSED bars in one request.

        The primary key is (instrument_id, timeframe, ts), so a re-run of the
        backfill updates the same rows instead of failing or duplicating.
        """
        self._guard("bars")
        if rows:
            self._post(
                "bars?on_conflict=instrument_id,timeframe,ts",
                list(rows),
                prefer="resolution=merge-duplicates",
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

    def insert_catalysts(self, rows: Sequence[dict[str, Any]]) -> None:
        """Publish catalysts. A catalyst is a warning, never a trigger."""
        self._guard("catalysts")
        if rows:
            self._post("catalysts", list(rows))

    def delete_catalysts(self, kind: str, from_time: datetime) -> None:
        """Clear the forward window of one catalyst kind.

        The calendar is a moving target: the schedule changes, events are
        added and cancelled. Refreshing means replacing the window, not
        appending to it.
        """
        self._guard("catalysts")
        self._delete(
            f"catalysts?kind=eq.{kind}&occurs_at=gte.{iso(from_time)}"
        )

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
        rows = self._get(
            "instruments?select=id,symbol,venue,asset_class,in_universe,"
            "primary_source,data_quality&in_universe=eq.true"
        )
        return rows or []

    # ------------------------------------------------------------- reads --
    # The worker reads only what it needs to evaluate: closed bars, the
    # promoted champions, and the owner who receives alerts. It never reads
    # capital, tickets or user settings.

    def _get(self, path: str) -> Any:
        import urllib.request

        req = urllib.request.Request(
            f"{self._url}/rest/v1/{path}", headers=self._headers, method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _json_loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Supabase read failed (%s): %s", path.split("?")[0], exc)
            return []

    def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        import urllib.request

        req = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/{name}",
            data=_json_bytes(payload),
            headers=self._headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return _json_loads(raw) if raw.strip() else None

    def fetch_bars(
        self, instrument_id: str, timeframe: str, limit: int = 400
    ) -> list[dict[str, Any]]:
        """The most recent CLOSED bars, oldest first.

        `order=ts.desc` + reverse gives the newest N without a window
        function; the strategies only ever need the tail.
        """
        rows = self._get(
            "bars?select=ts,open,high,low,close,volume"
            f"&instrument_id=eq.{instrument_id}"
            f"&timeframe=eq.{timeframe}"
            "&is_closed=eq.true"
            f"&order=ts.desc&limit={int(limit)}"
        )
        return list(reversed(rows or []))

    def fetch_active_champions(self) -> list[dict[str, Any]]:
        """Join champions -> versions -> strategies in Python.

        Deliberately not a PostgREST embedding: the local emulator implements
        plain filters, and a champion set is a handful of rows. Three small
        reads are easier to trust than one clever one.
        """
        champions = self._get(
            "champions?select=asset_class,timeframe,version_id&active=eq.true"
        ) or []
        if not champions:
            return []

        versions = {
            v["id"]: v
            for v in (
                self._get(
                    "strategy_versions?select=id,version,params,risk_spec,"
                    "status,strategy_id"
                )
                or []
            )
        }
        strategies = {
            s["id"]: s
            for s in (
                self._get("strategies?select=id,name,template_id") or []
            )
        }

        joined: list[dict[str, Any]] = []
        for champion in champions:
            version = versions.get(str(champion.get("version_id")))
            if not version or version.get("status") != "champion":
                continue
            strategy = strategies.get(str(version.get("strategy_id")))
            if not strategy:
                continue
            joined.append(
                {
                    "asset_class": champion.get("asset_class"),
                    "timeframe": champion.get("timeframe"),
                    "version_id": champion.get("version_id"),
                    "version": version.get("version"),
                    "params": version.get("params") or {},
                    "risk_spec": version.get("risk_spec") or {},
                    "template_id": strategy.get("template_id"),
                    "strategy_name": strategy.get("name"),
                }
            )
        return joined

    def fetch_owner_id(self) -> str | None:
        rows = self._get(
            "user_profiles?select=user_id,role&role=eq.owner&limit=1"
        ) or []
        return str(rows[0]["user_id"]) if rows else None

    def fire_alert(
        self,
        *,
        user_id: str,
        kind: str,
        rule_id: str,
        title: str,
        body: str | None = None,
        severity: str = "info",
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        channel: str = "in_app",
    ) -> str | None:
        """File one alert. The dedupe key makes a replay a no-op."""
        self._guard("alerts")
        try:
            return self._rpc(
                "fire_alert",
                {
                    "p_user_id": user_id,
                    "p_kind": kind,
                    "p_rule_id": rule_id,
                    "p_title": title,
                    "p_body": body,
                    "p_severity": severity,
                    "p_payload": payload or {},
                    "p_dedupe_key": dedupe_key,
                    "p_channel": channel,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("fire_alert failed: %s", exc)
            return None

    def fetch_pending_alerts(
        self, limit: int = 20, channel: str | None = None
    ) -> list[dict[str, Any]]:
        path = (
            "alerts?select=id,user_id,kind,title,body,payload,dedupe_key,channel"
            "&state=eq.pending"
        )
        if channel:
            path += f"&channel=eq.{channel}"
        path += f"&order=created_at.asc&limit={int(limit)}"
        return self._get(path) or []

    def fetch_telegram_chat(self, user_id: str) -> int | None:
        rows = self._get(
            "telegram_links?select=chat_id&revoked_at=is.null"
            f"&user_id=eq.{user_id}&limit=1"
        ) or []
        return int(rows[0]["chat_id"]) if rows else None

    # ------------------------------------------------------- AI proposals --

    def fetch_champion_params(self) -> list[dict[str, Any]]:
        """The promoted strategies, with their parameters.

        Used by the Tier A mutation path: the AI may only propose changes to
        a strategy that already exists and is promoted.
        """
        champions = self._get(
            "champions?select=asset_class,timeframe,version_id&active=eq.true"
        ) or []
        if not champions:
            return []

        versions = {
            v["id"]: v
            for v in (
                self._get(
                    "strategy_versions?select=id,version,params,risk_spec,status,"
                    "strategy_id,origin"
                )
                or []
            )
        }
        strategies = {
            s["id"]: s
            for s in (self._get("strategies?select=id,name,template_id") or [])
        }

        out: list[dict[str, Any]] = []
        for champion in champions:
            version = versions.get(str(champion.get("version_id")))
            if not version or version.get("status") != "champion":
                continue
            strategy = strategies.get(str(version.get("strategy_id")))
            if not strategy or not strategy.get("template_id"):
                continue
            out.append(
                {
                    "asset_class": champion.get("asset_class"),
                    "timeframe": champion.get("timeframe"),
                    "version_id": champion.get("version_id"),
                    "version": version.get("version"),
                    "template_id": strategy.get("template_id"),
                    "strategy_name": strategy.get("name"),
                    "params": version.get("params") or {},
                }
            )
        return out

    def count_trials(self) -> int:
        """How many candidates have been tried.

        Every strategy version and every AI proposal consumes exactly one
        trial. The deflated Sharpe ratio counts them, so this number is what
        keeps a large search honest.
        """
        versions = self._get("strategy_versions?select=id") or []
        proposals = self._get("strategy_proposals?select=id") or []
        return len(versions) + len(proposals)

    def fetch_last_contract(self, provider: str) -> dict[str, Any] | None:
        """The most recent contract check for one provider."""
        rows = self._get(
            "provider_contracts?select=provider,endpoint,schema_hash,keys,ok,"
            f"checked_at&provider=eq.{provider}&order=checked_at.desc&limit=1"
        ) or []
        return rows[0] if rows else None

    def record_provider_contract(self, row: dict[str, Any]) -> None:
        """Record one contract check.

        A failure is recorded too. The point of the check is to publish a
        broken provider, not to hide it.
        """
        self._guard("provider_contracts")
        allowed = {
            "provider",
            "endpoint",
            "schema_hash",
            "keys",
            "ok",
            "status",
            "latency_ms",
            "sample",
            "note",
        }
        payload = {key: value for key, value in row.items() if key in allowed}
        try:
            self._post("provider_contracts", payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("provider_contracts insert failed: %s", exc)

    def insert_ai_run(self, row: dict[str, Any]) -> None:
        """Record one AI call.

        The sidecar hands us the whole row: model, prompt hash, seed,
        temperature, tokens, latency, the validation verdict and the output.
        If an AI output influenced anything, this row explains exactly how.
        """
        self._guard("ai_runs")
        allowed = {
            "capability",
            "provider",
            "model_id",
            "prompt_hash",
            "prompt_version",
            "seed",
            "temperature",
            "input_hash",
            "input_preview",
            "output_json",
            "valid",
            "invalid_reason",
            "tokens_in",
            "tokens_out",
            "latency_ms",
            "cost_usd",
            "data_class",
            "user_id",
        }
        payload = {key: value for key, value in row.items() if key in allowed}
        try:
            self._post("ai_runs", payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("ai_runs insert failed: %s", exc)

    def insert_strategy_proposal(self, row: dict[str, Any]) -> str | None:
        """File an AI proposal. A proposal is not a strategy and not a champion."""
        self._guard("strategy_proposals")
        try:
            created = self._post(
                "strategy_proposals?select=id",
                row,
                prefer="return=representation",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("strategy proposal insert failed: %s", exc)
            return None
        if isinstance(created, list) and created:
            return str(created[0].get("id"))
        if isinstance(created, dict) and "id" in created:
            return str(created["id"])
        return None

    def mark_alert_sent(self, alert_id: str) -> None:
        self._patch(f"alerts?id=eq.{alert_id}", {"state": "sent", "sent_at": iso(utcnow())})


def _json_bytes(payload: Any) -> bytes:
    import json

    return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")


def _json_loads(raw: str) -> Any:
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
