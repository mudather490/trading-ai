"""Twelve Data, as a RESEARCH-ONLY cross-check.

Why this module is not a venue connector
========================================

Twelve Data's free plan is "internal non-display usage only". Display access
starts on the Grow plan ($29/month). So on $0 this provider may be FETCHED
and USED INTERNALLY, and it may not be shown in the app: not on a chart,
not on a watchlist, not in a ticket.

That is the entire reason this module lives in the research worker and not
in `worker/connectors/`:

  * it never writes `latest_quotes`,
  * it never writes `bars` for display,
  * its key is not in the web tier's environment,
  * it only CROSS-CHECKS what another venue already produced.

Why cross-check at all?
=======================

Alpaca's free plan is IEX only: a fraction of consolidated US volume. A
bar built from IEX prints can differ from the consolidated bar in the
volume, and sometimes in the close. A second, independent daily series for
the same instrument tells the research worker whether the bars it is about
to judge a strategy on are the bars it thinks they are.

The budget
==========

The free plan allows 8 API credits a minute and 800 a day, and the quota
resets at midnight UTC. One daily bar request for one symbol costs one
credit. The whole cross-check for the six US instruments costs six credits
a day, which is under 1% of the daily allowance.

The counter below is not decoration: the module refuses to spend more than
the allowance, because a 429 means every OTHER call in the day fails too.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

log = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com"

# The free plan's daily allowance.
DAILY_CREDIT_LIMIT = 800

# The interval names Twelve Data uses.
INTERVALS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


@dataclass
class DailyBar:
    """One daily bar, in the shape the research worker uses."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class CreditBudget:
    """The daily credit counter.

    The free plan allows 800 credits a day and resets at midnight UTC. The
    counter refuses to spend past that, because the 429 that follows would
    break every other call for the rest of the day, including the ones the
    app depends on.
    """

    def __init__(self, limit: int = DAILY_CREDIT_LIMIT) -> None:
        self.limit = limit
        self.used = 0
        self.day = datetime.now(timezone.utc).date()

    def _roll(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day = today
            self.used = 0

    def remaining(self) -> int:
        self._roll()
        return max(0, self.limit - self.used)

    def take(self, credits: int = 1) -> bool:
        """Take the credits, or refuse."""
        self._roll()
        if self.used + credits > self.limit:
            log.warning(
                "twelvedata: the daily allowance is spent (%d/%d); refusing",
                self.used,
                self.limit,
            )
            return False
        self.used += credits
        return True

    def state(self) -> dict[str, Any]:
        return {"used": self.used, "limit": self.limit, "remaining": self.remaining()}


class TwelveDataError(RuntimeError):
    """The provider refused, or answered something unexpected."""


def _api_key(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("TWELVE_DATA_API_KEY")


def fetch_daily_bars(
    symbol: str,
    *,
    api_key: str | None = None,
    outputsize: int = 120,
    budget: CreditBudget | None = None,
    base_url: str = BASE_URL,
) -> list[DailyBar]:
    """The daily bars for one instrument.

    One call, one credit. The provider answers with `meta` and `values`,
    newest first.
    """
    key = _api_key(api_key)
    if not key:
        raise TwelveDataError("TWELVE_DATA_API_KEY is not set.")

    if budget is not None and not budget.take(1):
        raise TwelveDataError("the daily credit allowance is spent.")

    query = urllib.parse.urlencode(
        {
            "symbol": symbol.upper(),
            "interval": "1day",
            "outputsize": max(1, min(int(outputsize), 5000)),
            "apikey": key,
        }
    )
    url = f"{base_url.rstrip('/')}/time_series?{query}"

    request = urllib.request.Request(
        url, headers={"User-Agent": "trading-command-center/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        if exc.code == 429:
            raise TwelveDataError(
                "the provider's rate limit was reached (429): " + detail
            ) from exc
        raise TwelveDataError(f"HTTP {exc.code}: {detail}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TwelveDataError(f"the response was not JSON: {raw[:120]}") from exc

    if not isinstance(payload, dict):
        raise TwelveDataError("the response was not an object.")

    if "values" not in payload:
        # The provider answers 200 with an explanation when it cannot serve
        # the request, which is a trap for a caller that only checks the
        # status code.
        raise TwelveDataError(
            f"the provider refused: {payload.get('message', payload)}"
        )

    bars: list[DailyBar] = []
    for row in payload.get("values") or []:
        try:
            bars.append(
                DailyBar(
                    ts=datetime.fromisoformat(
                        str(row["datetime"]).replace("Z", "+00:00")
                    ),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                )
            )
        except (KeyError, TypeError, ValueError):
            # One malformed row is not a reason to lose the other 119.
            continue

    bars.sort(key=lambda bar: bar.ts)
    return bars


def compare_series(
    primary: Sequence[DailyBar], secondary: Sequence[DailyBar]
) -> dict[str, Any]:
    """Compare two daily series for the same instrument.

    The question this answers is narrow: does the SECOND venue agree with
    the first about the same day? A disagreement on the close is worth
    knowing about before a strategy is judged on it.
    """
    left = {bar.ts.date(): bar for bar in primary}
    right = {bar.ts.date(): bar for bar in secondary}
    shared = sorted(set(left) & set(right))

    if not shared:
        return {
            "days": 0,
            "close_median_abs_error": None,
            "close_max_abs_error": None,
            "close_median_abs_error_pct": None,
            "within_tolerance": False,
            "note": "the two series share no day",
        }

    errors = [abs(left[day].close - right[day].close) for day in shared]
    errors_pct = [
        abs(left[day].close - right[day].close) / left[day].close * 100
        for day in shared
        if left[day].close
    ]
    median = sorted(errors)[len(errors) // 2]
    median_pct = (
        sorted(errors_pct)[len(errors_pct) // 2] if errors_pct else None
    )
    return {
        "days": len(shared),
        "close_median_abs_error": round(median, 6),
        "close_max_abs_error": round(max(errors), 6),
        "close_median_abs_error_pct": (
            round(median_pct, 4) if median_pct is not None else None
        ),
        # A tenth of a percent is tight enough to catch a broken feed and
        # loose enough to survive a different consolidation.
        "within_tolerance": median_pct is not None and median_pct <= 0.1,
    }


__all__ = [
    "CreditBudget",
    "DailyBar",
    "TwelveDataError",
    "compare_series",
    "fetch_daily_bars",
]
