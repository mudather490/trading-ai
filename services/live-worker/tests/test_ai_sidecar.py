"""Tests for the AI sidecar.

These prove the boundary holds:

  * An ungrounded number is stripped from the output.
  * A grounded number survives.
  * Invalid JSON produces a failed result, not a coerced one.
  * Private data is REFUSED before it reaches a free-tier provider.
  * A provider failure produces a failed result, never a usable-looking one.
  * Every call is logged.

Run:  python -m pytest services/live-worker/tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.ai import (  # noqa: E402
    AISidecar,
    DataClassError,
    ProviderError,
    ValidationResult,
    grounded_numbers,
    validate_json,
)


class FakeProvider:
    name = "fake"
    free_tier = True
    model_id = "fake-model-1"

    def __init__(self, response: str = "", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 1024):
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return self.response, 100, 50


# ---------------------------------------------------- grounded numbers ----


def test_a_grounded_number_survives():
    text = "The stop is at 67200 and the target is 70400."
    payload = {"stop": 67200, "target": 70400}
    cleaned, stripped = grounded_numbers(text, payload)
    assert stripped == []
    assert "67200" in cleaned
    assert "70400" in cleaned


def test_an_ungrounded_number_is_stripped():
    text = "The stop is at 67200 and the target is 99999."
    payload = {"stop": 67200}
    cleaned, stripped = grounded_numbers(text, payload)
    assert "99999" in stripped
    assert "99999" not in cleaned
    assert "67200" in cleaned, "a grounded number must not be removed"


def test_numeric_matching_ignores_formatting():
    text = "Size is 0.42 and price is 68000.00"
    payload = {"size": 0.42, "price": 68000}
    cleaned, stripped = grounded_numbers(text, payload)
    assert stripped == [], f"unexpectedly stripped {stripped}"


def test_thousands_separators_are_ignored():
    text = "Notional is 1,234.50"
    payload = {"notional": 1234.5}
    cleaned, stripped = grounded_numbers(text, payload)
    assert stripped == []


def test_a_completely_invented_number_is_caught():
    text = "Expectancy is 0.87 per trade."
    payload = {"trades": 214, "win_rate": 0.55}
    cleaned, stripped = grounded_numbers(text, payload)
    assert "0.87" in stripped


def test_trivial_numbers_are_allowed():
    # Years and small counts are not hallucinations.
    text = "Based on 2024 data across 5 trades."
    payload = {}
    cleaned, stripped = grounded_numbers(text, payload)
    assert stripped == []


# ------------------------------------------------------- json validation ----


def test_valid_json_passes():
    out = '{"event_type": "earnings", "impact_subject": "company"}'
    result = validate_json(out, ("event_type", "impact_subject"))
    assert result.valid
    assert result.data["event_type"] == "earnings"


def test_json_in_a_code_fence_is_accepted():
    out = '```json\n{"event_type": "earnings"}\n```'
    result = validate_json(out, ("event_type",))
    assert result.valid


def test_json_with_surrounding_prose_is_accepted():
    out = 'Here is the analysis: {"event_type": "earnings"} Hope that helps.'
    result = validate_json(out, ("event_type",))
    assert result.valid
    assert result.data["event_type"] == "earnings"


def test_missing_required_keys_fail():
    out = '{"event_type": "earnings"}'
    result = validate_json(out, ("event_type", "impact_subject"))
    assert not result.valid
    assert "impact_subject" in result.reason


def test_non_json_fails_rather_than_being_coerced():
    out = "I think this is probably bullish because of the earnings report."
    result = validate_json(out, ("event_type",))
    assert not result.valid
    assert result.data is None


def test_empty_output_fails():
    result = validate_json("", ("event_type",))
    assert not result.valid
    assert result.reason == "empty output"


def test_validation_grounds_numbers_against_the_input():
    out = '{"event_type": "earnings", "impact_subject": "company", "confidence": 0.93}'
    payload = {"event_type": "earnings", "impact_subject": "company"}
    result = validate_json(out, ("event_type", "impact_subject"), payload)
    assert result.valid
    assert "0.93" in result.stripped_numbers
    assert result.data["confidence"] is None, "an invented number must be nulled"


# ---------------------------------------------------------- the sidecar ----


def test_the_sidecar_logs_every_call():
    rows = []
    provider = FakeProvider('{"event_type": "earnings"}')
    sidecar = AISidecar(
        provider=provider,
        capability="news_extract",
        required_keys=("event_type",),
        log_sink=rows.append,
    )
    sidecar.run("extract this", input_payload={"title": "Tesla earnings"})

    assert len(rows) == 1
    row = rows[0]
    assert row["capability"] == "news_extract"
    assert row["valid"] is True
    assert row["provider"] == "fake"
    assert row["seed"] == 19
    assert row["temperature"] == 0
    assert row["prompt_hash"]
    assert row["input_hash"]


def test_the_sidecar_refuses_private_data_on_a_free_tier():
    provider = FakeProvider('{"ok": true}')
    sidecar = AISidecar(
        provider=provider,
        capability="narrate",
        required_keys=("ok",),
        data_class="private",
    )
    with pytest.raises(DataClassError):
        sidecar.run("summarise this user's journal", input_payload={"note": "private"})

    assert provider.calls == [], "the request was sent despite the privacy refusal"


def test_public_data_is_allowed_on_a_free_tier():
    provider = FakeProvider('{"ok": true}')
    sidecar = AISidecar(
        provider=provider,
        capability="news_extract",
        required_keys=("ok",),
        data_class="public",
    )
    result = sidecar.run("extract", input_payload={"headline": "public news"})
    assert result.valid
    assert len(provider.calls) == 1


def test_a_provider_failure_produces_a_failed_result_not_a_crash():
    rows = []
    provider = FakeProvider(error=ProviderError("boom"))
    sidecar = AISidecar(
        provider=provider,
        capability="news_extract",
        required_keys=("event_type",),
        log_sink=rows.append,
    )
    result = sidecar.run("extract", input_payload={})
    assert not result.valid
    assert result.data is None
    assert rows[0]["valid"] is False


def test_an_invalid_response_is_logged_as_invalid():
    rows = []
    provider = FakeProvider("this is not json at all")
    sidecar = AISidecar(
        provider=provider,
        capability="news_extract",
        required_keys=("event_type",),
        log_sink=rows.append,
    )
    result = sidecar.run("extract", input_payload={})
    assert not result.valid
    assert rows[0]["valid"] is False
    assert rows[0]["invalid_reason"]


def test_the_same_input_hashes_the_same_way():
    from worker.ai import hash_payload

    a = hash_payload({"b": 2, "a": 1})
    b = hash_payload({"a": 1, "b": 2})
    assert a == b, "hash must not depend on key order"
