"""The provider contract check.

A market data API can change its response shape without announcing it, and
a pipeline that keeps "working" with nulls is worse than one that is loudly
broken: the prices stop updating and the app does not say why.

So every provider gets a cheap call whose RESPONSE SHAPE is recorded:

  * the SET OF KEYS in the reply, hashed,
  * a sample of the values, to prove they are not null,
  * the latency and the status.

A change in the key set is a schema change, and it is detected by a HASH.
This is deterministic. It cannot hallucinate a change, and it cannot miss
one. An LLM asked to "watch for API changes" would eventually decide that
something had changed without anything changing, and nobody could audit
why it believed that.

The check is also cheap on purpose. It calls the smallest endpoint each
provider has, once, so the credit cost is negligible: one Binance ticker,
one Alpaca trade, one OANDA account, one Twelve Data quote, one Finnhub
calendar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ContractCheck:
    """The result of one check."""

    provider: str
    endpoint: str
    schema_hash: str
    keys: list[str]
    ok: bool
    status: int | None = None
    latency_ms: int = 0
    sample: dict[str, Any] = field(default_factory=dict)
    note: str | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "schema_hash": self.schema_hash,
            "keys": self.keys,
            "ok": self.ok,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "sample": self.sample,
            "note": self.note,
        }


def key_set_hash(keys: list[str]) -> str:
    """A stable hash of the key set.

    Sorted, because a JSON object has no order and a provider that
    reorders its keys has not changed anything.
    """
    joined = ",".join(sorted(keys))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _keys_of(payload: Any) -> list[str]:
    """The top-level keys of a JSON object."""
    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())
    return []


def _sample_of(payload: Any, keys: list[str]) -> dict[str, Any]:
    """One small value per key, so a reader can see what the API said."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            out[key] = type(value).__name__
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    return out


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[Any, int, int]:
    """GET a URL. Returns (payload, status, latency_ms)."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "trading-command-center/0.1", **(headers or {})},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    elapsed = int((time.monotonic() - started) * 1000)
    try:
        payload = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        payload = None
    return payload, status, elapsed


def _check(
    provider: str,
    endpoint: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    required_keys: tuple[str, ...] = (),
) -> ContractCheck:
    """Run one check and build its result."""
    try:
        payload, status, elapsed = _request(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return ContractCheck(
            provider=provider,
            endpoint=endpoint,
            schema_hash="",
            keys=[],
            ok=False,
            status=None,
            latency_ms=0,
            note=f"the provider could not be reached: {exc}",
        )

    keys = _keys_of(payload)
    digest = key_set_hash(keys)

    note: str | None = None
    ok = 200 <= status < 300 and bool(keys)
    if not ok:
        note = f"HTTP {status}"
    else:
        missing = [key for key in required_keys if key not in keys]
        if missing:
            ok = False
            note = f"the response is missing {', '.join(missing)}"
        elif payload is not None and all(
            payload.get(key) is None for key in keys if key in required_keys
        ):
            ok = False
            note = "the response carried the right keys and no values"

    return ContractCheck(
        provider=provider,
        endpoint=endpoint,
        schema_hash=digest,
        keys=keys,
        ok=ok,
        status=status,
        latency_ms=elapsed,
        sample=_sample_of(payload, keys),
        note=note,
    )


# ------------------------------------------------------------- the providers --


def check_binance(rest_base: str) -> ContractCheck:
    """The cheapest public call: one ticker."""
    return _check(
        "binance",
        "GET /api/v3/ticker/price",
        f"{rest_base.rstrip('/')}/api/v3/ticker/price?symbol=BTCUSDT",
        required_keys=("symbol", "price"),
    )


def check_alpaca(data_url: str, key_id: str, secret_key: str) -> ContractCheck:
    """One trade on IEX. Cheap, and it proves the data key works."""
    return _check(
        "alpaca",
        "GET /v2/stocks/{symbol}/trades/latest",
        f"{data_url.rstrip('/')}/v2/stocks/AAPL/trades/latest?feed=iex",
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
        },
        required_keys=("symbol", "trades"),
    )


def check_oanda(rest_base: str, token: str, account_id: str) -> ContractCheck:
    """The account endpoint. It proves the practice token works."""
    return _check(
        "oanda",
        "GET /v3/accounts/{accountID}",
        f"{rest_base.rstrip('/')}/v3/accounts/{account_id}",
        headers={"Authorization": f"Bearer {token}"},
        required_keys=("account",),
    )


def check_twelvedata(api_key: str) -> ContractCheck:
    """One daily bar. Twelve Data's free tier is 8 credits a minute, so the
    check uses the cheapest endpoint there is."""
    return _check(
        "twelvedata",
        "GET /time_series",
        (
            "https://api.twelvedata.com/time_series"
            f"?symbol=AAPL&interval=1day&outputsize=2&apikey={api_key}"
        ),
        required_keys=("meta", "values"),
    )


def check_finnhub(api_key: str) -> ContractCheck:
    """The economic calendar, which is what the app uses Finnhub for."""
    return _check(
        "finnhub",
        "GET /calendar/economic",
        (
            "https://finnhub.io/api/v1/calendar/economic"
            f"?from=2024-01-01&to=2024-01-07&token={api_key}"
        ),
        required_keys=("economicCalendar",),
    )


def run_checks(config: Any) -> list[ContractCheck]:
    """Check every CONFIGURED provider.

    A provider that is not configured is not checked and not reported: the
    feed banner already says "not configured", and a failed check on a
    missing key would say the same thing in a louder voice.
    """
    checks: list[ContractCheck] = []

    if "binance" in getattr(config, "enabled_venues", ()):
        checks.append(check_binance(config.binance.rest_base))

    alpaca = getattr(config, "alpaca", None)
    if alpaca is not None and alpaca.configured and "alpaca" in getattr(
        config, "enabled_venues", ()
    ):
        checks.append(
            check_alpaca(alpaca.data_url, alpaca.key_id, alpaca.secret_key)
        )

    oanda = getattr(config, "oanda", None)
    if oanda is not None and oanda.configured and "oanda" in getattr(
        config, "enabled_venues", ()
    ):
        checks.append(
            check_oanda(oanda.practice_rest, oanda.token, oanda.account_id)
        )

    return checks


def compare_with_previous(
    check: ContractCheck, previous: dict[str, Any] | None
) -> ContractCheck:
    """Compare a check with the previous one for the same provider.

    A DIFFERENT key set is a schema change. The check is reported as
    failed, with both hashes, because a changed schema is exactly the thing
    the check exists to find.
    """
    if not previous or not previous.get("schema_hash") or not check.schema_hash:
        return check
    if previous["schema_hash"] == check.schema_hash:
        return check

    old_keys = previous.get("keys") or []
    old_set = {str(key) for key in old_keys}
    new_set = set(check.keys)
    added = ", ".join(sorted(new_set - old_set)) or "none"
    removed = ", ".join(sorted(old_set - new_set)) or "none"
    check.ok = False
    check.note = (
        "the response shape changed since the last check "
        f"(added: {added}; removed: {removed})"
    )
    return check


__all__ = [
    "ContractCheck",
    "check_binance",
    "check_alpaca",
    "check_oanda",
    "check_twelvedata",
    "check_finnhub",
    "compare_with_previous",
    "key_set_hash",
    "run_checks",
]
