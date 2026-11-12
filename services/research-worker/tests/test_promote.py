"""The promotion runner's statistics.

These are the three numbers that are easy to fake and hard to compute, so
they get their own tests:

  * the deflated Sharpe ratio (the search penalty),
  * the probability of backtest overfitting (the CSCV),
  * the neighbourhood pass rate (the isolated-peak detector).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "strategy-core"))

from research.promote import (  # noqa: E402
    Candidate,
    CandidateResult,
    build_grid,
    deflated_sharpe,
    expected_max_sharpe,
    neighbour_pass_rate,
    pbo_cscv,
    probabilistic_sharpe_ratio,
    skewness,
    kurtosis,
)


def _series(seed: int, n: int, drift: float = 0.0) -> list[float]:
    """A deterministic pseudo-random return series with a drift."""
    state = seed
    out: list[float] = []
    for _ in range(n):
        state = (1103515245 * state + 12345) % (2**31)
        noise = (state / 2**31 - 0.5) * 0.02
        out.append(drift + noise)
    return out


def test_the_sharpe_ratio_of_a_flat_series_is_zero():
    from research.promote import sharpe_per_observation

    assert sharpe_per_observation([0.001] * 50) == 0.0
    assert sharpe_per_observation([]) == 0.0


def test_the_expected_maximum_sharpe_grows_with_the_number_of_trials():
    one = expected_max_sharpe(2, 0.01)
    many = expected_max_sharpe(100, 0.01)
    assert many > one > 0


def test_the_deflated_sharpe_falls_as_the_search_grows():
    """Ten trials for the same edge is a worse bet than two."""
    trial_sharpes = [0.05, 0.06, 0.04, 0.07, 0.03, 0.05, 0.06, 0.04, 0.05, 0.07]
    returns = _series(7, 400, drift=0.0008)
    skew = skewness(returns)
    kurt = kurtosis(returns)

    few = deflated_sharpe(
        sharpe=0.05,
        n_obs=len(returns),
        n_trials=2,
        trial_sharpes=[0.05, 0.06],
        skew=skew,
        kurt=kurt,
    )
    many = deflated_sharpe(
        sharpe=0.05,
        n_obs=len(returns),
        n_trials=len(trial_sharpes),
        trial_sharpes=trial_sharpes,
        skew=skew,
        kurt=kurt,
    )
    assert 0 <= many <= few <= 1


def test_a_negative_edge_has_a_deflated_sharpe_near_zero():
    value = deflated_sharpe(
        sharpe=-0.05,
        n_obs=400,
        n_trials=5,
        trial_sharpes=[-0.05, -0.04, -0.06, -0.05, -0.03],
        skew=0.0,
        kurt=3.0,
    )
    # Nowhere near the 0.95 the gate demands.
    assert value < 0.2


def test_the_probabilistic_sharpe_ratio_is_a_probability():
    value = probabilistic_sharpe_ratio(
        sharpe=0.1, sr0=0.0, n_obs=500, skew=0.0, kurt=3.0
    )
    assert 0.0 <= value <= 1.0
    # A bigger edge is more certain.
    better = probabilistic_sharpe_ratio(
        sharpe=0.3, sr0=0.0, n_obs=500, skew=0.0, kurt=3.0
    )
    assert better > value


def test_the_pbo_of_candidates_with_no_skill_is_high():
    """Two coin flips: the in-sample winner is usually the out-sample loser."""
    candidates = [
        _series(11, 320),
        _series(29, 320),
        _series(47, 320),
    ]
    pbo, rank = pbo_cscv(candidates, n_slices=8)
    assert 0.0 <= pbo <= 1.0
    assert 0.0 <= rank <= 1.0
    # With no skill, the selection is close to a coin flip, so PBO is not low.
    assert pbo > 0.3


def test_the_pbo_of_a_candidate_with_persistent_skill_is_lower():
    skilled = _series(101, 320, drift=0.004)
    noise = [_series(202, 320)[i] for i in range(320)]
    pbo, _rank = pbo_cscv([skilled, noise, noise], n_slices=8)
    assert pbo < 0.5


def test_the_pbo_needs_enough_data():
    pbo, _ = pbo_cscv([[0.001, -0.001]], n_slices=8)
    assert pbo == 1.0


def _result(params: dict, sharpe: float, expectancy: float, drawdown: float):
    return CandidateResult(
        candidate=Candidate("trend_ema_adx", params, "test"),
        metrics={
            "sharpe": sharpe,
            "expectancy": expectancy,
            "max_drawdown": drawdown,
        },
    )


def test_the_neighbour_pass_rate_counts_the_one_parameter_neighbours():
    base = {"fast": 20, "slow": 50}
    results = [
        _result(base, 1.2, 0.001, -0.10),          # the candidate
        _result({"fast": 22, "slow": 50}, 1.1, 0.001, -0.10),  # a neighbour
        _result({"fast": 16, "slow": 50}, 1.0, 0.001, -0.12),  # a neighbour
        _result({"fast": 20, "slow": 55}, -0.4, -0.002, -0.40),  # a bad neighbour
        _result({"fast": 22, "slow": 55}, 1.0, 0.001, -0.10),  # NOT a neighbour
    ]
    rate = neighbour_pass_rate(results, 0)
    # Two of the three neighbours work.
    assert abs(rate - 2 / 3) < 1e-9


def test_the_grid_moves_one_parameter_at_a_time():
    grid = build_grid("trend_ema_adx")
    base = grid[0]
    assert base.label == "base"
    for candidate in grid[1:]:
        differing = [
            key
            for key in set(candidate.params) | set(base.params)
            if candidate.params.get(key) != base.params.get(key)
        ]
        assert len(differing) == 1


def test_the_grid_refuses_a_template_that_needs_two_series():
    import pytest

    with pytest.raises(SystemExit):
        build_grid("rotation_ma")


def test_the_grid_refuses_an_unknown_template():
    import pytest

    with pytest.raises(SystemExit):
        build_grid("does_not_exist")


def test_the_sharpe_ratio_maths_is_sane():
    from research.promote import sharpe_per_observation

    returns = [0.01] * 10 + [-0.01] * 10
    assert sharpe_per_observation(returns) == 0.0
    rising = [0.01 * i for i in range(20)]
    assert sharpe_per_observation(rising) > 1.0
    assert not math.isnan(sharpe_per_observation(rising))
