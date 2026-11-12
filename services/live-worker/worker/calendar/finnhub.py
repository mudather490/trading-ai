"""Finnhub economic calendar (free tier).

Facts this connector is built around:
  * One call covers a date range: `/calendar/economic?from=&to=`.
  * The free tier allows 60 calls a minute. One refresh a day is nothing.
  * The `time` field is a naive ISO timestamp in UTC. We attach UTC
    explicitly rather than inheriting the machine's timezone, because a
    timestamp that silently changes with the host's locale is a timestamp
    that will eventually be wrong.
  * `impact` is one of high / medium / low, which is exactly the severity
    scale the `catalysts` table uses.

No key is ever logged.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Country code -> the currency whose market the release moves.
COUNTRY_CURRENCY = {
    "US": "USD",
    "EU": "EUR",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "GB": "GBP",
    "JP": "JPY",
    "CN": "CNY",
    "CA": "CAD",
    "CH": "CHF",
    "AU": "AUD",
    "NZ": "NZD",
    "SE": "SEK",
    "NO": "NOK",
    "DK": "DKK",
    "MX": "MXN",
    "BR": "BRL",
    "IN": "INR",
    "SG": "SGD",
    "HK": "HKD",
    "ZA": "ZAR",
    "RU": "RUB",
    "TR": "TRY",
    "PL": "PLN",
}

# The impact label the venue gives us -> the severity the schema stores.
IMPACT_TO_SEVERITY = {
    "high": "high",
    "medium": "medium",
    "low": "low",
}


class CalendarError(RuntimeError):
    """The calendar could not be read."""


def fetch_events(
    api_key: str,
    from_date: datetime,
    to_date: datetime,
    base_url: str = "https://finnhub.io/api/v1",
) -> list[dict]:
    """Read the economic calendar between two dates.

    Returns the normalised event list. Raises CalendarError when the venue
    refuses or answers with something unexpected; the caller falls back to
    "no events known" rather than inventing a calm market.
    """
    import urllib.parse
    import urllib.request

    if not api_key:
        raise CalendarError("FINNHUB_API_KEY is required")

    query = urllib.parse.urlencode(
        {
            "from": from_date.strftime("%Y-%m-%d"),
            "to": to_date.strftime("%Y-%m-%d"),
            "token": api_key,
        }
    )
    url = f"{base_url.rstrip('/')}/calendar/economic?{query}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "trading-command-center/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CalendarError(f"economic calendar request failed: {exc}") from exc

    return parse_events(payload)


def parse_events(payload: dict) -> list[dict]:
    """Normalise a calendar payload into event dicts.

    Events without a known currency, without a title, or without a time are
    dropped: a catalyst we cannot place on the clock cannot warn anyone.
    """
    rows = payload.get("economicCalendar") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        country = str(row.get("country") or "").upper()
        currency = COUNTRY_CURRENCY.get(country)
        title = str(row.get("event") or "").strip()
        if not currency or not title:
            continue

        event_time = _parse_event_time(row.get("time"))
        if event_time is None:
            continue

        impact = str(row.get("impact") or "").lower()
        severity = IMPACT_TO_SEVERITY.get(impact, "info")

        out.append(
            {
                "source_id": f"{country}:{row.get('time')}:{title}",
                "title": title,
                "country": country,
                "currency": currency,
                "event_time": event_time,
                "impact": impact or "info",
                "severity": severity,
                "actual": _as_float(row.get("actual")),
                "forecast": _as_float(row.get("estimate")),
                "previous": _as_float(row.get("prev")),
                "source": "finnhub_calendar",
            }
        )

    out.sort(key=lambda event: event["event_time"])
    return out


def _parse_event_time(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def week_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The window the worker refreshes: today plus the next seven days."""
    start = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return start, start + timedelta(days=7)
