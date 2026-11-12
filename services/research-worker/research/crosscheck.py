"""The cross-check CLI.

It compares the daily bars the app already has against an INDEPENDENT
series from Twelve Data, and prints how far apart they are.

Why: Alpaca's free plan is IEX only. A bar built from IEX prints can
differ from the consolidated bar, and a strategy judged on a bar that is
not the bar you think it is will be judged on the wrong thing. The check
does not change anything. It reports.

Twelve Data's free plan may not be DISPLAYED, and this tool displays
nothing: it prints a report to a terminal, reads nothing from the app's
display surface, and writes nothing to the app's tables.

    python -m research.crosscheck --symbols AAPL,SPY,QQQ
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

# The strategy package is a sibling of `services`, not an installed package.
_CORE = Path(__file__).resolve().parents[3] / "packages" / "strategy-core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from .twelvedata import (  # noqa: E402
    CreditBudget,
    DailyBar,
    TwelveDataError,
    compare_series,
    fetch_daily_bars,
)

log = logging.getLogger(__name__)

# The instruments the app holds through Alpaca.
DEFAULT_SYMBOLS = ("AAPL", "GLD", "NVDA", "QQQ", "SPY", "TLT")


def _bars_from_rows(rows: Sequence[dict[str, Any]]) -> list[DailyBar]:
    bars: list[DailyBar] = []
    for row in rows:
        try:
            bars.append(
                DailyBar(
                    ts=datetime.fromisoformat(
                        str(row["ts"]).replace("Z", "+00:00")
                    ),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.ts)
    return bars


def run(
    symbols: Sequence[str],
    *,
    outputsize: int = 120,
    budget: CreditBudget | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Cross-check every symbol and return one row per symbol."""
    from .db import SupabaseRest

    db = SupabaseRest.from_env()
    budget = budget or CreditBudget()
    rows: list[dict[str, Any]] = []

    for symbol in symbols:
        instrument = db.instrument(symbol)
        if instrument is None:
            rows.append({"symbol": symbol, "status": "not_in_universe"})
            continue

        local = _bars_from_rows(
            db.closed_bars(instrument["id"], "1d", limit=outputsize * 2)
        )

        if not local:
            rows.append(
                {
                    "symbol": symbol,
                    "status": "no_local_bars",
                    "detail": "the venue has not produced daily bars for this one",
                }
            )
            continue

        try:
            external = fetch_daily_bars(
                symbol, api_key=api_key, outputsize=outputsize, budget=budget
            )
        except TwelveDataError as exc:
            rows.append({"symbol": symbol, "status": "refused", "detail": str(exc)})
            continue

        comparison = compare_series(local, external)
        rows.append(
            {
                "symbol": symbol,
                "status": "compared",
                "local_bars": len(local),
                "external_bars": len(external),
                **comparison,
            }
        )

    return rows


def render(rows: Sequence[dict[str, Any]], budget: CreditBudget) -> str:
    lines = [
        "symbol   days   median err%   max err%   within tolerance",
        "-------  -----  ------------  ---------  ---------------",
    ]
    for row in rows:
        if row.get("status") != "compared":
            lines.append(
                f"{row['symbol']:<8}  {row.get('status')}"
                + (f"  ({row.get('detail', '')})" if row.get("detail") else "")
            )
            continue
        lines.append(
            f"{row['symbol']:<8}  {row['days']:>5}  "
            f"{row['close_median_abs_error_pct']:>12.4f}  "
            f"{row['close_max_abs_error']:>9.4f}  "
            f"{'yes' if row['within_tolerance'] else 'NO'}"
        )
    state = budget.state()
    lines.append("")
    lines.append(
        f"credits used: {state['used']}/{state['limit']} "
        f"(the free plan allows {state['limit']} a day)"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(
        description="Compare the app's daily bars with an independent series."
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="comma-separated symbols to cross-check",
    )
    parser.add_argument("--outputsize", type=int, default=120)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    budget = CreditBudget()
    rows = run(symbols, outputsize=args.outputsize, budget=budget)
    print(render(rows, budget))

    failed = [
        row
        for row in rows
        if row.get("status") == "compared" and not row["within_tolerance"]
    ]
    if failed:
        print("")
        print(
            f"{len(failed)} instrument(s) disagree by more than 0.1%. The bars "
            "the app stores and the bars the provider reports are not the "
            "same bars. Judge strategies on that in mind."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
