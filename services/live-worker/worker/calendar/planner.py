"""Turn calendar events into catalysts.

A catalyst is instrument-scoped: it says "this instrument has something
happening at this time". The economic calendar is currency-scoped: it says
"USD has something happening at this time". The planner is the translation
between the two, and it is deliberately boring — a currency match, nothing
clever.

Two details that matter:

  * A crypto instrument is quoted in USDT but reacts to USD releases, so
    USDT is treated as USD for event matching.
  * An FX pair reacts to BOTH of its currencies. EURUSD cares about the euro
    AND the dollar.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..connectors.base import canonical_symbol


def currencies_for(
    instrument: dict, oanda_symbols: tuple[str, ...] = ()
) -> set[str]:
    """Which currencies move this instrument."""
    symbol = str(instrument.get("symbol") or "").upper()
    venue = str(instrument.get("venue") or "").lower()

    if venue == "oanda":
        for candidate in oanda_symbols:
            if canonical_symbol(candidate) == symbol:
                base, _, quote = candidate.partition("_")
                codes = {code for code in (base, quote) if code}
                if codes:
                    return codes

    currency = str(instrument.get("currency") or "").upper()
    if currency == "USDT":
        currency = "USD"
    return {currency} if currency else set()


def build_catalysts(
    events: list[dict],
    instruments: list[dict],
    oanda_symbols: tuple[str, ...] = (),
    now: datetime | None = None,
) -> list[dict]:
    """Build one catalyst row per (event, affected instrument).

    Events in the past are dropped: a warning about a release that already
    happened is noise, not risk.
    """
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    currency_map: list[tuple[dict, set[str]]] = [
        (instrument, currencies_for(instrument, oanda_symbols))
        for instrument in instruments
    ]

    rows: list[dict] = []
    for event in events:
        event_time = event["event_time"]
        if event_time < cutoff:
            continue
        for instrument, codes in currency_map:
            if event["currency"] not in codes:
                continue
            rows.append(
                {
                    "instrument_id": instrument["id"],
                    "kind": "economic",
                    "title": event["title"],
                    "occurs_at": _iso(event_time),
                    "severity": event["severity"],
                    "source": event["source"],
                    "url": None,
                    "confirmed": True,
                    "metadata": {
                        "source_id": event["source_id"],
                        "country": event["country"],
                        "currency": event["currency"],
                        "impact": event["impact"],
                        "actual": event["actual"],
                        "forecast": event["forecast"],
                        "previous": event["previous"],
                    },
                }
            )
    return rows


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
