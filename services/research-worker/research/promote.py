"""The promotion runner.

It turns closed bars into a CANDIDATE, and a candidate into a version the
owner may promote. It does not promote anything itself.

Why not? Because the whole point of the gate is that a human with authority
presses the button and the gate answers. A script that promoted its own
candidate would make the gate decoration.

What this runner computes, and why each of those numbers is hard:

  DSR (deflated Sharpe)
      The Sharpe ratio you measured is inflated by the number of things you
      tried. If you test 40 parameter sets and keep the best, the best Sharpe
      is partly luck. The deflated Sharpe answers: given that I ran N trials,
      what is the probability that the best one still has an edge? It uses
      the expected maximum Sharpe under the null (Bailey & Lopez de Prado)
      and the real skew and kurtosis of the returns, because a fat-tailed
      return series makes a Sharpe ratio less trustworthy than it looks.

  PBO (probability of backtest overfitting)
      Computed with CSCV: the history is cut into S slices, every way of
      taking half of them as in-sample and half as out-of-sample is tried,
      and we count how often the best in-sample candidate turns out to be in
      the bottom half out-of-sample. An isolated peak scores near 1.0.

  Neighbour stability
      The candidate's parameter neighbours must also work. A strategy that
      only works at 200 and nowhere near 200 is a curve fit with extra
      steps, and the gate rejects it.

The holdout is read ONCE, by this module, and the read is counted in
`holdout_ledger`. A second read and the candidate is no longer promotable.

Run it:
    python -m research.promote --symbol BTCUSDT --timeframe 1d \
        --template trend_ema_adx
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Sequence

# The strategy package is a sibling of `services`, not an installed package.
_CORE = Path(__file__).resolve().parents[3] / "packages" / "strategy-core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from strategy_core.engine import Engine  # noqa: E402
from strategy_core.interface import Bar, Position  # noqa: E402
from strategy_core.strategies import TEMPLATES, build  # noqa: E402

from .backtest import CostModel, compute_metrics  # noqa: E402

log = logging.getLogger(__name__)

# The templates this runner can evaluate. `rotation_ma` needs a second
# series (the defensive leg), so it is not here: a runner that quietly
# evaluated a rotation strategy on one instrument would be measuring
# something else entirely.
SINGLE_INSTRUMENT_TEMPLATES = ("trend_ema_adx", "donchian_breakout", "rsi2_mean_reversion")

EULER_MASCHERONI = 0.5772156649015329


# ----------------------------------------------------------------- models --


@dataclass(frozen=True)
class Candidate:
    """One point in the parameter grid."""

    template_id: str
    params: dict[str, Any]
    label: str
    neighbours: tuple[int, ...] = ()

    def key(self) -> str:
        return json.dumps(self.params, sort_keys=True)


@dataclass
class CandidateResult:
    candidate: Candidate
    metrics: dict[str, Any]
    returns: list[float] = field(default_factory=list)
    bars: int = 0
    trades: int = 0


@dataclass
class Report:
    symbol: str
    timeframe: str
    template_id: str
    results: list[CandidateResult]
    best: CandidateResult | None
    dsr: float
    pbo: float
    neighbour_pass_rate: float
    holdout: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    # How many candidates have EVER been tried, across every grid. The
    # deflated Sharpe counts them, so this is the number that keeps the
    # search honest.
    n_trials: int = 0
    # Why the selected candidate was selected, in words. The rule is applied
    # to the IN-SAMPLE segment only; the holdout is never part of the
    # selection.
    selection_rule: str = ""


# ------------------------------------------------------------- statistics --


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def sharpe_per_observation(returns: Sequence[float]) -> float:
    """Sharpe on the per-observation scale (no annualisation)."""
    if len(returns) < 2:
        return 0.0
    var = _variance(returns)
    if var <= 0:
        return 0.0
    return _mean(returns) / math.sqrt(var)


def skewness(values: Sequence[float]) -> float:
    if len(values) < 3:
        return 0.0
    sd = math.sqrt(_variance(values))
    if sd <= 0:
        return 0.0
    m = _mean(values)
    return _mean([((v - m) / sd) ** 3 for v in values])


def kurtosis(values: Sequence[float]) -> float:
    """NON-excess kurtosis. A normal distribution has 3."""
    if len(values) < 4:
        return 3.0
    sd = math.sqrt(_variance(values))
    if sd <= 0:
        return 3.0
    m = _mean(values)
    return _mean([((v - m) / sd) ** 4 for v in values])


def probabilistic_sharpe_ratio(
    sharpe: float, sr0: float, n_obs: int, skew: float, kurt: float
) -> float:
    """PSR: the probability the true Sharpe exceeds `sr0`.

    Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio".
    """
    if n_obs < 3:
        return 0.0
    variance_term = 1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * sharpe * sharpe
    if variance_term <= 0:
        return 0.0
    z = (sharpe - sr0) * math.sqrt(n_obs - 1) / math.sqrt(variance_term)
    return NormalDist().cdf(z)


def expected_max_sharpe(n_trials: int, trial_variance: float) -> float:
    """The Sharpe you would expect from the luckiest of N random trials."""
    if n_trials < 2 or trial_variance <= 0:
        return 0.0
    normal = NormalDist()
    z_one = normal.inv_cdf(1.0 - 1.0 / n_trials)
    z_two = normal.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(trial_variance) * (
        (1.0 - EULER_MASCHERONI) * z_one + EULER_MASCHERONI * z_two
    )


def deflated_sharpe(
    sharpe: float,
    n_obs: int,
    n_trials: int,
    trial_sharpes: Sequence[float],
    skew: float,
    kurt: float,
) -> float:
    """The deflated Sharpe ratio: P(true edge survives the search)."""
    sr_star = expected_max_sharpe(n_trials, _variance(list(trial_sharpes)))
    return probabilistic_sharpe_ratio(sharpe, sr_star, n_obs, skew, kurt)


def pbo_cscv(
    per_candidate_returns: Sequence[Sequence[float]],
    n_slices: int = 8,
) -> tuple[float, float]:
    """PBO by Combinatorially Symmetric Cross-Validation.

    Returns (pbo, mean_relative_rank).

    The history is cut into `n_slices` contiguous slices. Every way of
    choosing half of them as in-sample is tried. For each split we take the
    candidate with the best in-sample Sharpe and ask where it RANKS
    out-of-sample. If the in-sample winner is usually in the bottom half
    out-of-sample, the selection procedure is overfitting, and the PBO is
    the fraction of splits where that happens.
    """
    candidates = [list(r) for r in per_candidate_returns if r]
    n_candidates = len(candidates)
    n_obs = min(len(r) for r in candidates) if candidates else 0
    if n_candidates < 2 or n_obs < n_slices * 2:
        return 1.0, 0.0

    # Per candidate, per slice: the summary statistics of that slice's
    # returns. Pooling slices is then exact and cheap.
    slices: list[list[dict[str, float]]] = []
    for candidate in candidates:
        per_slice: list[dict[str, float]] = []
        size = n_obs // n_slices
        for s in range(n_slices):
            chunk = candidate[s * size : (s + 1) * size] if s < n_slices - 1 else candidate[(n_slices - 1) * size :]
            values = [v for v in chunk if v is not None]
            n = len(values)
            if n < 2:
                per_slice.append({"n": 0.0, "mean": 0.0, "sumsq": 0.0})
                continue
            m = _mean(values)
            ss = sum((v - m) ** 2 for v in values)
            per_slice.append({"n": float(n), "mean": m, "sumsq": ss})
        slices.append(per_slice)

    def pooled(candidate_index: int, slice_ids: Sequence[int]) -> float:
        n_total = 0.0
        weighted = 0.0
        sumsq = 0.0
        for s in slice_ids:
            row = slices[candidate_index][s]
            n_total += row["n"]
            weighted += row["n"] * row["mean"]
            sumsq += row["sumsq"]
        if n_total < 2:
            return 0.0
        mean = weighted / n_total
        variance = (sumsq + sum(row["n"] * (row["mean"] - mean) ** 2 for row in
                               (slices[candidate_index][s] for s in slice_ids))) / (n_total - 1)
        if variance <= 0:
            return 0.0
        return mean / math.sqrt(variance)

    from itertools import combinations

    half = n_slices // 2
    overfit = 0
    total = 0
    rank_sum = 0.0

    for in_sample in combinations(range(n_slices), half):
        out_sample = [s for s in range(n_slices) if s not in in_sample]
        if not out_sample:
            continue

        is_scores = [pooled(c, in_sample) for c in range(n_candidates)]
        oos_scores = [pooled(c, out_sample) for c in range(n_candidates)]

        best = max(range(n_candidates), key=lambda c: is_scores[c])
        best_oos = oos_scores[best]
        # Relative rank: 0 = the worst candidate out-of-sample.
        rank = sum(1 for score in oos_scores if score < best_oos)
        relative = rank / n_candidates
        rank_sum += relative
        total += 1
        if relative < 0.5:
            overfit += 1

    if total == 0:
        return 1.0, 0.0
    return overfit / total, rank_sum / total


# ------------------------------------------------------------ the grid ----


def build_grid(template_id: str, overrides: dict[str, Any] | None = None) -> list[Candidate]:
    """The candidate and its parameter neighbours.

    One parameter moves at a time, by about 20%. A grid where two parameters
    move together cannot tell you which one mattered, and a candidate whose
    neighbours all fail is a curve fit.
    """
    if template_id not in TEMPLATES:
        raise SystemExit(
            f"Unknown template '{template_id}'. Available: {', '.join(sorted(TEMPLATES))}"
        )
    if template_id not in SINGLE_INSTRUMENT_TEMPLATES:
        raise SystemExit(
            f"'{template_id}' needs a second price series; this runner only "
            f"evaluates {', '.join(SINGLE_INSTRUMENT_TEMPLATES)}."
        )

    defaults = dict(TEMPLATES[template_id].defaults)
    defaults.update(overrides or {})

    candidates: list[Candidate] = [
        Candidate(template_id, dict(defaults), "base")
    ]

    for name, value in defaults.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        for factor in (0.8, 1.2):
            moved = value * factor
            if isinstance(value, int):
                moved = max(2, int(round(moved)))
            else:
                moved = round(moved, 4)
            if moved == value:
                continue
            params = dict(defaults)
            params[name] = moved
            candidates.append(
                Candidate(template_id, params, f"{name}={moved}")
            )

    return candidates


def _param_distance(a: dict[str, Any], b: dict[str, Any]) -> int:
    """How many parameters differ between two candidates."""
    keys = set(a) | set(b)
    return sum(1 for key in keys if a.get(key) != b.get(key))


def neighbour_pass_rate(
    results: Sequence[CandidateResult], best_index: int
) -> float:
    """The share of the best candidate's neighbours that also work.

    The neighbours are the candidates that differ in exactly ONE parameter:
    the immediate neighbours in parameter space. "Also works" means: positive
    net expectancy, a positive Sharpe, and a drawdown inside the limit the
    gate applies to the candidate itself.
    """
    if not results:
        return 0.0
    best = results[best_index]
    neighbours = [
        result
        for index, result in enumerate(results)
        if index != best_index
        and _param_distance(result.candidate.params, best.candidate.params) == 1
    ]
    if not neighbours:
        return 0.0

    def ok(result: CandidateResult) -> bool:
        metrics = result.metrics
        expectancy = metrics.get("expectancy") or 0.0
        sharpe = metrics.get("sharpe") or 0.0
        drawdown = metrics.get("max_drawdown") or 0.0
        return expectancy > 0 and sharpe > 0 and drawdown > -0.30

    return sum(1 for result in neighbours if ok(result)) / len(neighbours)


# ---------------------------------------------------------- evaluation ----


def _to_bars(rows: Sequence[dict[str, Any]]) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        ts = row.get("ts")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        bars.append(
            Bar(
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            )
        )
    return bars


def evaluate_candidate(
    candidate: Candidate,
    bars: Sequence[Bar],
    timeframe: str,
    symbol: str,
    cost_model: CostModel,
    *,
    max_leverage: float = 1.0,
    risk_per_trade: float = 0.02,
) -> CandidateResult:
    """Run one candidate over the bars and collect its metrics."""
    from .backtest import Backtester

    engine = Engine.for_strategy(symbol, timeframe, candidate.params)
    engine.add_bars(list(bars))
    strategy = build(candidate.template_id, candidate.params)

    def signal_fn(_bars_so_far, index: int):
        ctx = engine.context_at(
            index, strategy.params, position=Position(side="flat", entry_price=0.0, size=0.0)
        )
        try:
            return strategy.on_bar(ctx)
        except Exception as exc:  # noqa: BLE001
            log.warning("strategy raised on %s: %s", symbol, exc)
            return None

    backtester = Backtester(
        bars,
        timeframe=timeframe,
        cost_model=cost_model,
        risk_per_trade=risk_per_trade,
        max_leverage=max_leverage,
        label=candidate.label,
    )
    result = backtester.run(signal_fn)

    # The backtester already computed the metrics, with the same numbers the
    # curve was built from. Recomputing them here was a second source of
    # truth, and it silently dropped the trial count and the exposure
    # bookkeeping.
    metrics = dict(result.metrics)

    equity = result.equity
    returns = [
        equity[i] / equity[i - 1] - 1.0
        for i in range(1, len(equity))
        if equity[i - 1]
    ]

    return CandidateResult(
        candidate=candidate,
        metrics=metrics,
        returns=returns,
        bars=len(bars),
        trades=len(result.trades),
    )


def run_research(
    rows: Sequence[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    template_id: str,
    cost_model: CostModel,
    is_fraction: float = 0.7,
    n_slices: int = 8,
    overrides: dict[str, Any] | None = None,
    max_leverage: float = 1.0,
    risk_per_trade: float = 0.02,
    prior_trials: int = 0,
    min_trades: int = 30,
    max_drawdown: float = 0.30,
) -> Report:
    """Evaluate the grid, select a candidate, and measure the three hard things."""
    bars = _to_bars(rows)
    notes: list[str] = []

    if len(bars) < 200:
        raise SystemExit(
            f"Only {len(bars)} closed bars for {symbol} {timeframe}. "
            "Run a backfill job first; a research result on 50 bars is a guess."
        )

    # The holdout is the tail. It is read ONCE, after the candidate is chosen.
    split = int(len(bars) * is_fraction)
    is_bars = bars[:split]
    holdout_bars = bars[split:]
    if len(is_bars) < 150 or len(holdout_bars) < 30:
        raise SystemExit(
            f"{len(bars)} bars is not enough to split into "
            f"{len(is_bars)} in-sample and {len(holdout_bars)} holdout."
        )

    grid = build_grid(template_id, overrides)
    results: list[CandidateResult] = []
    for candidate in grid:
        results.append(
            evaluate_candidate(
                candidate,
                is_bars,
                timeframe,
                symbol,
                cost_model,
                max_leverage=max_leverage,
                risk_per_trade=risk_per_trade,
            )
        )

    # A candidate that lost the whole account is not a candidate. It is a
    # failure, and selecting it because some ratio looked good on the way
    # down is exactly how a broken backtester promotes a broken strategy.
    alive = [
        (index, result)
        for index, result in enumerate(results)
        if not result.metrics.get("bankrupt")
    ]
    if not alive:
        raise SystemExit(
            "Every candidate in the grid lost the entire account on the "
            "in-sample segment. Nothing is publishable: the sizing or the "
            "strategy is broken, and the gate has nothing honest to judge."
        )
    if len(alive) < len(results):
        notes.append(
            f"{len(results) - len(alive)} of {len(results)} candidates went "
            "bankrupt on the in-sample segment and were excluded."
        )

    # The hard filters the gate itself enforces, applied BEFORE the
    # selection. A candidate with four trades is refused by the gate on the
    # trade count, so publishing it adds a version and a trial to the record
    # for nothing. These are all IN-SAMPLE numbers: the holdout takes no
    # part in the choice.
    publishable = [
        (index, result)
        for index, result in alive
        if result.trades >= min_trades
        and abs(result.metrics.get("max_drawdown") or 0.0) <= max_drawdown
    ]
    if not publishable:
        raise SystemExit(
            f"No candidate cleared the pre-gate filters: at least {min_trades} "
            f"in-sample trades and a drawdown inside {max_drawdown:.0%}. "
            "The gate would refuse every one of them, so none is published."
        )
    if len(publishable) < len(alive):
        notes.append(
            f"{len(alive) - len(publishable)} candidate(s) were excluded for "
            f"fewer than {min_trades} in-sample trades or a drawdown outside "
            f"{max_drawdown:.0%}. The gate would refuse them."
        )

    # The best candidate is chosen on the in-sample segment, by Sharpe.
    scored = [
        (index, (result.metrics.get("sharpe") or 0.0))
        for index, result in publishable
    ]
    best_index = max(scored, key=lambda pair: pair[1])[0]
    best = results[best_index]

    # A run that blew up must never be selected, whatever it scored.
    if best.metrics.get("bankrupt"):
        raise SystemExit("The selected candidate went bankrupt; refusing to publish.")

    # The trial count is the WHOLE history, not this grid. A search that ran
    # three grids of ten candidates tried thirty things; the deflated Sharpe
    # has to know that, or the number means nothing.
    total_trials = prior_trials + len(results)

    trial_sharpes = [
        (result.metrics.get("sharpe") or 0.0)
        for result in results
    ]
    # Convert the annualised Sharpe back to the per-observation scale used by
    # the statistics, so the deflated Sharpe is computed on one scale.
    scale = {
        "1m": 1440 * 365,
        "5m": 288 * 365,
        "15m": 96 * 365,
        "1h": 24 * 365,
        "4h": 6 * 365,
        "1d": 365,
    }.get(timeframe, 365)
    per_obs_sharpes = [
        (result.metrics.get("sharpe") or 0.0) / math.sqrt(scale) for result in results
    ]
    best_per_obs = per_obs_sharpes[best_index]

    dsr = deflated_sharpe(
        sharpe=best_per_obs,
        n_obs=len(best.returns),
        n_trials=total_trials,
        trial_sharpes=per_obs_sharpes,
        skew=skewness(best.returns),
        kurt=kurtosis(best.returns),
    )

    pbo, mean_rank = pbo_cscv([result.returns for result in results], n_slices=n_slices)

    neighbour_rate = neighbour_pass_rate(results, best_index)

    # The holdout: read once, and only for the chosen candidate.
    holdout_result = evaluate_candidate(
        best.candidate,
        holdout_bars,
        timeframe,
        symbol,
        cost_model,
        max_leverage=max_leverage,
        risk_per_trade=risk_per_trade,
    )
    holdout = {
        "bars": len(holdout_bars),
        "start": holdout_bars[0].ts.date().isoformat(),
        "end": holdout_bars[-1].ts.date().isoformat(),
        "trades": holdout_result.trades,
        "sharpe": holdout_result.metrics.get("sharpe"),
        "cagr": holdout_result.metrics.get("cagr"),
        "max_drawdown": holdout_result.metrics.get("max_drawdown"),
        "expectancy": holdout_result.metrics.get("expectancy"),
        "bankrupt": holdout_result.metrics.get("bankrupt"),
        "reads": 1,
    }
    if (holdout_result.metrics.get("sharpe") or 0.0) <= 0:
        notes.append(
            "The holdout Sharpe is not positive. The candidate may not survive "
            "the gate, and that would be the honest outcome."
        )

    return Report(
        symbol=symbol,
        timeframe=timeframe,
        template_id=template_id,
        results=results,
        best=best,
        dsr=dsr,
        pbo=pbo,
        neighbour_pass_rate=neighbour_rate,
        holdout=holdout,
        notes=notes,
        n_trials=total_trials,
        selection_rule=(
            f"in-sample only: at least {min_trades} trades, drawdown inside "
            f"{max_drawdown:.0%}, not bankrupt; then the highest in-sample "
            "Sharpe"
        ),
    )


# ------------------------------------------------------------- publishing --


def publish(
    report: Report,
    db,
    *,
    dry_run: bool = False,
    holdout_contaminated: bool = False,
    holdout_reads_before: int = 0,
) -> dict[str, Any]:
    """Write the candidate, its backtest and its performance record.

    It writes NO champion. Promotion is the owner's decision.
    """
    best = report.best
    assert best is not None

    instrument = db.instrument(report.symbol)
    if instrument is None:
        raise SystemExit(f"{report.symbol} is not in the universe.")

    version_number = 1
    strategy_id = db.upsert_strategy(
        name=f"{report.template_id} · {report.symbol} · {report.timeframe}",
        family=report.template_id.split("_")[0],
        asset_class=instrument["asset_class"],
        template_id=report.template_id,
    )
    version_id = db.create_version(
        strategy_id=strategy_id,
        version=version_number,
        params=best.candidate.params,
        timeframes=[report.timeframe],
        risk_spec={"min_reward_risk": 1.0},
        code_ref=f"packages/strategy-core/strategy_core/strategies.py#{report.template_id}",
    )

    metrics = best.metrics
    period_start = metrics.get("start") or report.holdout["start"]
    period_end = metrics.get("end") or report.holdout["end"]

    performance_row = {
        "version_id": version_id,
        "asset_class": instrument["asset_class"],
        "timeframe": report.timeframe,
        "period_start": period_start,
        "period_end": period_end,
        "trades": best.trades,
        "win_rate": metrics.get("win_rate"),
        "expectancy": metrics.get("expectancy"),
        "profit_factor": metrics.get("profit_factor"),
        "cagr": metrics.get("cagr"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
        "max_drawdown": metrics.get("max_drawdown"),
        "exposure_pct": None,
        "dsr": report.dsr,
        "pbo": report.pbo,
        "n_trials": report.n_trials,
        "neighbour_pass_rate": report.neighbour_pass_rate,
        # A template candidate has no AI feature, so the AI feature gate does
        # not apply to it. Zero is the honest value, not a missing one.
        "ai_feature_gain_sharpe": 0.0,
        "benchmark": None,
        "benchmark_ret": None,
        "data_source": instrument.get("primary_source") or "bars",
        "data_official": instrument.get("data_quality") == "official",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "notes": {
            "template_id": report.template_id,
            "candidate": best.candidate.label,
            "holdout": report.holdout,
            "notes": report.notes,
            "selection_rule": report.selection_rule,
            "n_trials": report.n_trials,
            "max_leverage": best.metrics.get("max_leverage"),
            "max_exposure_used": best.metrics.get("max_exposure_used"),
            # Whether the tail of this dataset had already been read by an
            # earlier run. The gate refuses a contaminated candidate, and it
            # says why.
            "holdout_contaminated": holdout_contaminated,
            "holdout_reads_before": holdout_reads_before,
        },
    }

    backtest_row = {
        "version_id": version_id,
        "label": best.candidate.label,
        "period_start": period_start,
        "period_end": period_end,
        "timeframe": report.timeframe,
        "metrics": {
            "in_sample": metrics,
            "holdout": report.holdout,
            "candidates": [
                {
                    "label": result.candidate.label,
                    "params": result.candidate.params,
                    "sharpe": result.metrics.get("sharpe"),
                    "cagr": result.metrics.get("cagr"),
                    "max_drawdown": result.metrics.get("max_drawdown"),
                    "trades": result.trades,
                }
                for result in report.results
            ],
            "dsr": report.dsr,
            "pbo": report.pbo,
            "neighbour_pass_rate": report.neighbour_pass_rate,
        },
        "trades": best.trades,
        "data_source": instrument.get("primary_source") or "bars",
        "data_official": instrument.get("data_quality") == "official",
        "data_as_of": datetime.now(timezone.utc).isoformat(),
        "n_trials": report.n_trials,
        "holdout_reads": 1,
        "engine": "strategy-core",
    }

    if dry_run:
        return {
            "strategy_id": strategy_id,
            "version_id": version_id,
            "performance": performance_row,
            "backtest": backtest_row,
            "published": False,
        }

    db.insert_performance(performance_row)
    db.insert_backtest(backtest_row)
    db.record_holdout_read(version_id, "promotion runner: one read, then never again")

    return {
        "strategy_id": strategy_id,
        "version_id": version_id,
        "published": True,
    }


# ----------------------------------------------------------------- report --


def render(report: Report) -> str:
    best = report.best
    assert best is not None
    lines = [
        f"{report.symbol} {report.timeframe} · {report.template_id}",
        "",
        f"candidates tested : {len(report.results)}",
        f"trials ever       : {report.n_trials} (the search penalty)",
        f"selected          : {best.candidate.label} {best.candidate.params}",
        f"selection rule    : {report.selection_rule}",
        f"trades (IS)       : {best.trades}",
        f"sharpe            : {(best.metrics.get('sharpe') or 0):.3f}",
        f"cagr              : {(best.metrics.get('cagr') or 0) * 100:.2f}%",
        f"max drawdown      : {(best.metrics.get('max_drawdown') or 0) * 100:.2f}%",
        f"expectancy/trade  : {(best.metrics.get('expectancy') or 0):.6f}",
        f"risk per trade    : {(best.metrics.get('risk_per_trade') or 0) * 100:.2f}%",
        f"max exposure      : {(best.metrics.get('max_exposure_used') or 0):.2f}x equity"
        f" (cap {best.metrics.get('max_leverage')}x)",
        f"went bankrupt     : {bool(best.metrics.get('bankrupt'))}",
        "",
        f"DSR               : {report.dsr:.4f}   (gate wants >= 0.95)",
        f"PBO               : {report.pbo:.4f}   (gate wants <= 0.50)",
        f"neighbour pass    : {report.neighbour_pass_rate:.2%}   (gate wants >= 60%)",
        "",
        "holdout (read once):",
        f"  {report.holdout['start']} -> {report.holdout['end']}",
        f"  trades {report.holdout['trades']}, sharpe {report.holdout['sharpe']}",
    ]
    for note in report.notes:
        lines.append("")
        lines.append(f"note: {note}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    # Windows consoles default to cp1252 and refuse to print a box-drawing
    # character. A report that cannot be printed is a report nobody reads.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(description="Evaluate candidates and publish one.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--template", default="trend_ema_adx")
    parser.add_argument(
        "--commission",
        type=float,
        default=0.0005,
        help="commission per side as a fraction (0.0005 = 5 bps)",
    )
    parser.add_argument(
        "--spread",
        type=float,
        default=0.0005,
        help="spread + slippage per side as a fraction of the price",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3000,
        help="how many closed bars to read (the default uses the whole history)",
    )
    parser.add_argument(
        "--risk",
        type=float,
        default=0.02,
        help="fraction of equity risked per trade (0.02 = 2%%)",
    )
    parser.add_argument(
        "--max-leverage",
        type=float,
        default=1.0,
        help="cap on position notional, as a multiple of equity",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    from .db import SupabaseRest

    db = SupabaseRest.from_env()
    instrument = db.instrument(args.symbol)
    if instrument is None:
        raise SystemExit(f"{args.symbol} is not in the universe.")

    rows = db.closed_bars(instrument["id"], args.timeframe, limit=args.limit)
    if not rows:
        raise SystemExit(
            f"No closed {args.timeframe} bars for {args.symbol}. Run a backfill job first."
        )

    costs = CostModel(commission=args.commission, spread_slippage=args.spread)

    # The runner applies the SAME hard filters the gate applies, so it does
    # not publish candidates the gate is certain to refuse. The thresholds
    # are read from the ACTIVE policy: a policy change moves both sides at
    # once, and neither side keeps its own copy.
    policy = db.gate_policy()
    thresholds = policy["thresholds"]
    min_trades = int(
        thresholds.get("min_trades_1d")
        if args.timeframe == "1d"
        else thresholds.get("min_trades_1h")
        or thresholds.get("min_trades_1d")
        or 30
    )
    max_drawdown = float(thresholds.get("max_drawdown") or 0.30)

    # Every candidate ever tried, across every grid, ever. The deflated
    # Sharpe counts them.
    prior_trials = db.trial_count()

    # Has the tail of THIS dataset been read before? If it has, it is a
    # validation set now, not a holdout, and the candidate is marked so the
    # gate refuses it. The mark is published, not hidden.
    holdout_reads_before = db.holdout_reads(args.symbol, args.timeframe)
    holdout_contaminated = holdout_reads_before > 0

    report = run_research(
        rows,
        symbol=args.symbol,
        timeframe=args.timeframe,
        template_id=args.template,
        cost_model=costs,
        max_leverage=args.max_leverage,
        risk_per_trade=args.risk,
        prior_trials=prior_trials,
        min_trades=min_trades,
        max_drawdown=max_drawdown,
    )

    print(render(report))

    # The run is recorded whatever happens next. The holdout has already
    # been read by the time the report is printed, so a failure to publish
    # does NOT make the read any less of a read. An earlier version
    # recorded the run AFTER publishing, so a publish failure lost the
    # record of a holdout read that had already happened.
    try:
        result = publish(
            report,
            db,
            dry_run=args.dry_run,
            holdout_contaminated=holdout_contaminated,
            holdout_reads_before=holdout_reads_before,
        )
    finally:
        try:
            db.record_research_run(
                {
                    "symbol": args.symbol.upper(),
                    "timeframe": args.timeframe,
                    "template_id": args.template,
                    "candidates": len(report.results),
                    "trades": report.best.trades if report.best else None,
                    "sharpe": (
                        report.best.metrics.get("sharpe") if report.best else None
                    ),
                    "dsr": report.dsr,
                    "pbo": report.pbo,
                    "holdout_read": True,
                    "dry_run": bool(args.dry_run),
                    "note": report.selection_rule,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not record the research run: %s", exc)

    if holdout_contaminated:
        print("")
        print(
            "WARNING: the holdout of this dataset was read by "
            f"{holdout_reads_before} earlier run(s). The candidate is marked "
            "contaminated and the gate will refuse it. Use a dataset whose "
            "tail has never been read."
        )

    if result.get("published"):
        print("")
        print(f"published candidate version {result['version_id']}")
        print("It is NOT promoted. Open the app → Strategies → Promote to ask the gate.")
    else:
        print("")
        print("dry run: nothing was written")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
