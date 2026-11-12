"""Tier A mutations.

The AI may change ONE parameter, to ONE of the values it was offered. Every
other answer is refused. These tests are that refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.ai.mutation import (  # noqa: E402
    allowed_values,
    build_payload,
    propose_mutation,
)
from worker.ai.sidecar import ValidationResult  # noqa: E402


class FakeSidecar:
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def run(self, prompt, *, input_payload=None, expected_keys=None, user_id=None):
        self.calls.append(
            {
                "prompt": prompt,
                "input_payload": input_payload,
                "expected_keys": expected_keys,
            }
        )
        return self.result


PARAMS = {"fast": 20, "slow": 50, "min_adx": 20.0, "atr_stop_mult": 2.0}


def test_allowed_values_never_include_the_current_value():
    values = allowed_values(20)
    assert 20 not in values
    assert all(0.5 * 20 <= v <= 2.0 * 20 for v in values)


def test_integer_parameters_stay_integers():
    values = allowed_values(20, integer=True)
    assert all(float(v).is_integer() for v in values)


def test_the_payload_carries_the_allowed_values():
    # The payload is the grounding reference: a number the model invents is
    # not in here, so the sidecar strips it.
    payload = build_payload("trend_ema_adx", PARAMS)
    assert payload["template"] == "trend_ema_adx"
    assert payload["parameters"]["fast"]["current"] == 20
    assert payload["parameters"]["fast"]["allowed"] == allowed_values(20)


def test_a_valid_proposal_is_accepted():
    allowed = allowed_values(20)
    sidecar = FakeSidecar(
        ValidationResult(
            valid=True,
            data={
                "parameter": "fast",
                "value": allowed[0],
                "reason": "A slower cross should survive a chop.",
            },
        )
    )
    mutation = propose_mutation(sidecar, "trend_ema_adx", PARAMS)
    assert mutation is not None
    assert mutation.parameter == "fast"
    assert mutation.value == allowed[0]
    assert mutation.params["fast"] == allowed[0]
    # Only one parameter changed.
    assert mutation.params["slow"] == 50
    assert mutation.params["min_adx"] == 20.0


def test_an_unknown_parameter_is_refused():
    sidecar = FakeSidecar(
        ValidationResult(
            valid=True,
            data={"parameter": "leverage", "value": 10, "reason": "more"},
        )
    )
    assert propose_mutation(sidecar, "trend_ema_adx", PARAMS) is None


def test_an_invented_value_is_refused():
    sidecar = FakeSidecar(
        ValidationResult(
            valid=True,
            data={"parameter": "fast", "value": 999, "reason": "much faster"},
        )
    )
    assert propose_mutation(sidecar, "trend_ema_adx", PARAMS) is None


def test_a_nulled_value_is_refused():
    # This is what the sidecar does to a number that is not in the payload.
    sidecar = FakeSidecar(
        ValidationResult(
            valid=True,
            data={"parameter": "fast", "value": None, "reason": "faster"},
        )
    )
    assert propose_mutation(sidecar, "trend_ema_adx", PARAMS) is None


def test_an_invalid_output_is_refused():
    sidecar = FakeSidecar(ValidationResult(valid=False, reason="output is not JSON"))
    assert propose_mutation(sidecar, "trend_ema_adx", PARAMS) is None


def test_a_template_without_numeric_parameters_is_refused():
    sidecar = FakeSidecar(ValidationResult(valid=True, data={}))
    assert propose_mutation(sidecar, "trend_ema_adx", {"label": "x"}) is None
    assert sidecar.calls == []
