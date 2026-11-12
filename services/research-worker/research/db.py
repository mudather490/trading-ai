"""A small Supabase REST client for the research worker.

The research worker writes to the database with the service role, because a
candidate is not owned by anybody until a human promotes it. It writes ONLY
research artefacts:

  * `strategies` and `strategy_versions` (candidates),
  * `backtests` (the run that produced them),
  * `performance` (the metrics the gate reads),
  * `holdout_ledger` (how many times the holdout was read).

It never writes `champions`. Promotion is the owner's decision, made through
`promote_strategy()` in the app, and the gate decides whether it is allowed.

Everything is plain REST on purpose: this worker runs on the user's own
machine, it does not need a driver, and a dependency-free client is one less
thing to keep working.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

# The research worker holds the service-role key, because a candidate
# belongs to nobody until a human promotes it and so cannot be written
# under a user's RLS policy. Holding the key is not a blank cheque: these
# tables hold DECISIONS, and the runner may not write them.
#
# The live worker has the same rule for the same reason. Promotion happens
# through `promote_strategy()`, which only the owner may call.
_FORBIDDEN_TABLES = frozenset(
    {
        "champions",
        "gate_policy",
        "tickets",
        "user_profiles",
        "audit_log",
        "alerts",
        "jobs",
        "worker_heartbeat",
        "signals",
    }
)


class ForbiddenWrite(RuntimeError):
    """Raised when the research worker tries to write a decision table."""


class SupabaseRest:
    def __init__(self, url: str, service_role_key: str) -> None:
        self._url = url.rstrip("/")
        self._headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    @classmethod
    def from_env(cls) -> "SupabaseRest":
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required. "
                "See services/live-worker/.env.example."
            )
        return cls(url, key)

    # --------------------------------------------------------------- http --

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self._url}/rest/v1/{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer

        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            # A transport failure is a RuntimeError too, so a caller does
            # not have to know about urllib.
            raise RuntimeError(f"{method} {path} failed: {exc.reason}") from exc
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str, **query: Any) -> Any:
        return self._request("GET", path, query=query or None)

    def post(self, path: str, body: Any, *, prefer: str | None = None) -> Any:
        table = path.split("?")[0].split("/")[0]
        if table in _FORBIDDEN_TABLES:
            raise ForbiddenWrite(
                f"The research worker must not write to '{table}'. That table "
                "holds a decision, and the decision is the owner's."
            )
        return self._request("POST", path, body=body, prefer=prefer)

    def patch(self, path: str, body: Any, **query: Any) -> Any:
        return self._request("PATCH", path, query=query or None, body=body)

    # ------------------------------------------------------------- reads --

    def gate_policy(self) -> dict[str, Any]:
        """The ACTIVE gate policy: the thresholds the gate will enforce.

        The runner reads them so it can apply the same HARD filters the gate
        applies before it publishes anything. A candidate with four trades
        is not worth publishing: the gate will refuse it on the trade count
        and the refusal adds a version and a trial to the record for
        nothing.
        """
        rows = self.get(
            "gate_policy", select="version,thresholds", active="eq.true", limit=1
        )
        if not rows:
            raise RuntimeError("There is no active gate policy.")
        return {
            "version": rows[0]["version"],
            "thresholds": rows[0]["thresholds"] or {},
        }

    def trial_count(self) -> int:
        """How many candidates have ever been tried.

        Every published version, every AI proposal and every candidate in
        every research run is one trial. The deflated Sharpe counts them, so
        this number is what stops a search from looking better than it is.

        It counts the WHOLE history, not just the last grid. A search that
        ran three grids of ten candidates tried thirty things, and the
        holdout was read three times. Reporting "ten" would be a lie.
        """
        versions = self.get("strategy_versions", select="id", limit=1000) or []
        proposals = self.get("strategy_proposals", select="id", limit=1000) or []
        runs = self.get("research_runs", select="candidates", limit=1000) or []
        evaluated = sum(int(run.get("candidates") or 0) for run in runs)
        return len(versions) + len(proposals) + evaluated

    def holdout_reads(self, symbol: str, timeframe: str) -> int:
        """How many times the holdout tail of this dataset has been read.

        The tail of a dataset is ONE piece of information. Reading it once
        is a holdout. Reading it again makes it a validation set, and any
        candidate chosen with it is no longer out of sample.
        """
        rows = self.get(
            "research_runs",
            select="holdout_read",
            symbol=f"eq.{symbol.upper()}",
            timeframe=f"eq.{timeframe}",
            limit=500,
        ) or []
        return sum(1 for row in rows if row.get("holdout_read"))

    def record_research_run(self, row: dict[str, Any]) -> None:
        """Record one research run, dry ones included.

        A dry run is still a search. It evaluated candidates and it read the
        tail, and the trial counter has to know that.
        """
        self.post("research_runs", row)

    def instrument(self, symbol: str) -> dict[str, Any] | None:
        rows = self.get(
            "instruments",
            select="id,symbol,venue,asset_class,data_quality,in_universe",
            symbol=f"eq.{symbol.upper()}",
            limit=1,
        )
        return rows[0] if rows else None

    def closed_bars(
        self, instrument_id: str, timeframe: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        """The newest N closed bars, oldest first."""
        rows = self.get(
            "bars",
            select="ts,open,high,low,close,volume",
            instrument_id=f"eq.{instrument_id}",
            timeframe=f"eq.{timeframe}",
            is_closed="eq.true",
            order="ts.desc",
            limit=limit,
        )
        return list(reversed(rows or []))

    # ------------------------------------------------------------ writes --

    def upsert_strategy(self, name: str, family: str, asset_class: str, template_id: str):
        """Find the strategy by name, or create it once."""
        rows = self.get("strategies", select="id,name", name=f"eq.{name}", limit=1)
        if rows:
            return rows[0]["id"]
        created = self.post(
            "strategies",
            {
                "name": name,
                "family": family,
                "asset_class": asset_class,
                "template_id": template_id,
                "is_template": False,
            },
            prefer="return=representation",
        )
        if isinstance(created, list) and created:
            return created[0]["id"]
        if isinstance(created, dict) and "id" in created:
            return created["id"]
        rows = self.get("strategies", select="id,name", name=f"eq.{name}", limit=1)
        return rows[0]["id"] if rows else None

    def create_version(
        self,
        *,
        strategy_id: str,
        version: int,
        params: dict[str, Any],
        timeframes: list[str],
        risk_spec: dict[str, Any],
        code_ref: str,
        origin: str = "template",
    ) -> str:
        rows = self.get(
            "strategy_versions",
            select="id,version",
            strategy_id=f"eq.{strategy_id}",
            version=f"eq.{version}",
            limit=1,
        )
        if rows:
            return rows[0]["id"]
        created = self.post(
            "strategy_versions",
            {
                "strategy_id": strategy_id,
                "version": version,
                "params": params,
                "status": "challenger",
                "timeframes": timeframes,
                "risk_spec": risk_spec,
                "code_ref": code_ref,
                "origin": origin,
            },
            prefer="return=representation",
        )
        if isinstance(created, list) and created:
            return created[0]["id"]
        if isinstance(created, dict) and "id" in created:
            return created["id"]
        rows = self.get(
            "strategy_versions",
            select="id,version",
            strategy_id=f"eq.{strategy_id}",
            version=f"eq.{version}",
            limit=1,
        )
        return rows[0]["id"] if rows else None

    def insert_performance(self, row: dict[str, Any]) -> None:
        self.post("performance", row)

    def insert_backtest(self, row: dict[str, Any]) -> None:
        self.post("backtests", row)

    def record_holdout_read(self, version_id: str, purpose: str) -> None:
        """Count one read of the holdout.

        The gate refuses a candidate whose holdout was read more than once.
        The counter is incremented here, by the only component that reads the
        holdout.
        """
        rows = self.get(
            "holdout_ledger", select="version_id,reads", version_id=f"eq.{version_id}", limit=1
        )
        if rows:
            reads = int(rows[0].get("reads") or 0) + 1
            self.patch(
                "holdout_ledger",
                {
                    "reads": reads,
                    "last_read_at": _now(),
                    "purpose": purpose,
                },
                version_id=f"eq.{version_id}",
            )
        else:
            self.post(
                "holdout_ledger",
                {
                    "version_id": version_id,
                    "reads": 1,
                    "last_read_at": _now(),
                    "purpose": purpose,
                },
            )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["SupabaseRest"]
