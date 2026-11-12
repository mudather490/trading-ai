"""Tier A: the AI may change a strategy's PARAMETERS. Nothing else.

This is the whole of the AI's authority over a strategy, and the limit is
deliberate:

  * ONE parameter per proposal,
  * only parameters the template already declares,
  * only values from a discrete allowed set,
  * and every proposal CONSUMES A TRIAL, the moment it is made.

That last point is the important one. A parameter search that tries forty
things and keeps the best has inflated its Sharpe by luck, and the gate has
to know about it. The deflated Sharpe ratio counts the trials; if the AI
keeps proposing, the bar rises, and eventually nothing passes. That is the
system working, not the system failing.

The allowed values are put IN THE PROMPT, and the prompt payload is also
the grounding reference. So a number the model invents is stripped by the
sidecar before anyone sees it, and the proposal is refused. The model cannot
invent a parameter value; it can only pick one of the ones it was offered.

What this module does NOT do: promote anything, size anything, write
strategy code, or touch a live signal. A proposal is a proposal. The
research worker evaluates it, the gate judges it, and the owner decides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

log = logging.getLogger(__name__)

# How far one mutation may move a parameter. A mutation that changes a value
# by ten times is not a mutation, it is a different strategy wearing the
# old strategy's name.
MOVE_FACTORS: tuple[float, ...] = (0.75, 0.9, 1.1, 1.25)

# Integer parameters must stay integers.
INTEGER_PARAMETERS = frozenset({"fast", "slow", "ma_period", "adx_period", "atr_period", "entry_period", "exit_period"})


@dataclass(frozen=True)
class Mutation:
    """One proposed parameter change."""

    parameter: str
    value: float
    reason: str
    params: dict[str, Any]

    def as_row(self) -> dict[str, Any]:
        return {
            "tier": "mutation",
            "proposed_params": self.params,
            "sandbox_stage": "proposed",
        }


def allowed_values(current: float, *, integer: bool = False) -> list[float]:
    """The only values a mutation may choose for this parameter."""
    values: list[float] = []
    for factor in MOVE_FACTORS:
        moved = current * factor
        if integer:
            moved = float(max(2, int(round(moved))))
        else:
            moved = round(moved, 4)
        if moved != current and moved not in values:
            values.append(moved)
    return values


def build_payload(
    template_id: str, params: Mapping[str, Any]
) -> dict[str, Any]:
    """The prompt payload.

    It doubles as the grounding reference: every number the model is allowed
    to say appears here, so the sidecar's grounded-number check accepts
    exactly the allowed values and rejects everything else.
    """
    parameters: dict[str, Any] = {}
    for name, value in params.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        parameters[name] = {
            "current": value,
            "allowed": allowed_values(
                float(value), integer=name in INTEGER_PARAMETERS
            ),
        }
    return {
        "template": template_id,
        "parameters": parameters,
        "instruction": "change exactly one parameter to one of its allowed values",
    }


PROMPT = """You are tuning a trading strategy template for {template}.

The parameters, and the ONLY values you may choose for each:

{payload}

Change EXACTLY ONE parameter to ONE of its allowed values. Choose the value
you expect to hold up OUT OF SAMPLE, not the one that flatters the recent
past. A change that is too small to matter is as bad as one that is too
large.

Answer with JSON only, in exactly this shape:
{{"parameter": "<parameter name>", "value": <number>, "reason": "<one sentence>"}}
"""


def propose_mutation(
    sidecar: Any,
    template_id: str,
    params: Mapping[str, Any],
) -> Mutation | None:
    """Ask the model for one parameter change, and refuse anything invalid.

    Returns None when there is no usable proposal. None is a normal answer:
    no key, quota exhausted, invalid JSON, an invented number, an unknown
    parameter, a value that is not on the list, or a change that does
    nothing.
    """
    payload = build_payload(template_id, params)
    if not payload["parameters"]:
        log.info("mutation: template %s has no numeric parameters", template_id)
        return None

    prompt = PROMPT.format(template=template_id, payload=payload)
    result = sidecar.run(
        prompt,
        input_payload=payload,
        expected_keys=("parameter", "value", "reason"),
    )

    if not result.valid:
        log.info(
            "mutation: the model produced nothing usable (%s)", result.reason
        )
        return None

    data = result.data or {}
    parameter = data.get("parameter")
    value = data.get("value")
    reason = data.get("reason")

    if not isinstance(parameter, str) or parameter not in payload["parameters"]:
        log.info("mutation: '%s' is not a parameter of %s", parameter, template_id)
        return None

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        log.info("mutation: no usable value for %s", parameter)
        return None

    allowed = payload["parameters"][parameter]["allowed"]
    if not any(abs(float(value) - float(candidate)) < 1e-9 for candidate in allowed):
        log.info(
            "mutation: %s = %s is not one of the allowed values",
            parameter,
            value,
        )
        return None

    if not isinstance(reason, str) or not reason.strip():
        log.info("mutation: %s has no reason", parameter)
        return None

    new_params = dict(params)
    new_params[parameter] = value

    return Mutation(
        parameter=parameter,
        value=float(value),
        reason=reason.strip(),
        params=new_params,
    )


__all__ = ["Mutation", "allowed_values", "build_payload", "propose_mutation"]
