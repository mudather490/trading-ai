"""The AI sidecar.

AI is a sidecar, never the chassis. This module is where that boundary is
ENFORCED rather than merely intended.

Three rules live here:

  1. Every call is LOGGED. Model, prompt hash, seed, temperature, token
     counts, cost and the validation verdict. If an AI output influenced
     anything, there is a row explaining exactly how.

  2. Every output is SCHEMA-VALIDATED. Invalid output is DROPPED and COUNTED.
     It is never coerced into shape. A model that returns prose where JSON was
     asked for produces a failed row, not a broken feature.

  3. Every number is GROUNDED. A number that appears in the output must
     already appear in the input payload. This is the mechanism that stops the
     model inventing a price, a stop, a size or a Sharpe ratio. Anything
     ungrounded is stripped and reported.

Privacy: free-tier providers may use content to improve their products. Only
PUBLIC MARKET NEWS may ever be sent to a free tier. `data_class` is checked
before the request goes out, not after.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .budget import Budget

log = logging.getLogger(__name__)

# Numbers we accept as "grounded" if they appear in the input.
# Stripping thousands separators and currency symbols first.
_NUMBER = re.compile(r"[-+]?\d[\d,]*\.?\d*")
# Numbers that are never a hallucination: years, small counts, obvious labels.
# We still require grounding for anything that looks like a price or a ratio.
_TRIVIAL = frozenset(
    {
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
        "100", "1000", "2020", "2021", "2022", "2023", "2024", "2025", "2026",
    }
)


class DataClassError(RuntimeError):
    """Raised when data that must not leave our infrastructure is about to."""


def hash_payload(payload: Any) -> str:
    """Stable hash of any JSON-serialisable payload."""
    if isinstance(payload, str):
        raw = payload
    else:
        raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_numbers(text: str) -> list[str]:
    """Every number-like token in a string, normalised."""
    found: list[str] = []
    for match in _NUMBER.finditer(text or ""):
        token = match.group(0).replace(",", "").rstrip(".")
        if token in ("", "-", "+", "."):
            continue
        found.append(token)
    return found


def grounded_numbers(
    output: str, input_payload: Any
) -> tuple[str, list[str]]:
    """Strip numbers from `output` that do not appear in `input_payload`.

    Returns (cleaned_output, stripped_numbers).

    The comparison is numeric when both sides parse as numbers, so "100.0"
    matches "100". String containment is the fallback.
    """
    input_text = (
        input_payload
        if isinstance(input_payload, str)
        else json.dumps(input_payload, default=str)
    )
    allowed: set[str] = set()
    allowed_numeric: set[float] = set()

    for token in extract_numbers(input_text):
        allowed.add(token)
        try:
            allowed_numeric.add(float(token))
        except ValueError:
            pass

    def is_allowed(token: str) -> bool:
        if token in allowed or token in _TRIVIAL:
            return True
        try:
            value = float(token)
        except ValueError:
            return False
        return any(abs(candidate - value) < 1e-9 for candidate in allowed_numeric)

    cleaned = output or ""
    stripped: list[str] = []

    # Replace from the end so earlier offsets stay valid.
    matches = list(_NUMBER.finditer(cleaned))
    for match in reversed(matches):
        raw = match.group(0)
        token = raw.replace(",", "").rstrip(".")
        if token in ("", "-", "+", "."):
            continue
        if is_allowed(token):
            continue
        stripped.append(token)
        cleaned = cleaned[: match.start()] + "[removed]" + cleaned[match.end() :]

    stripped.reverse()
    return cleaned, stripped


@dataclass
class ValidationResult:
    """The verdict on one AI output."""

    valid: bool
    data: Any = None
    reason: str | None = None
    stripped_numbers: list[str] = field(default_factory=list)


def validate_json(
    output: str, required_keys: tuple[str, ...], input_payload: Any = None
) -> ValidationResult:
    """Parse and validate a JSON output.

    Required keys must be present. Numbers are then grounded against the
    input payload, and ungrounded ones are stripped from string fields.
    """
    if not output or not output.strip():
        return ValidationResult(valid=False, reason="empty output")

    # Models sometimes wrap JSON in a fence or add prose around it.
    text = output.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find the outermost JSON object.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return ValidationResult(valid=False, reason="output is not JSON")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            return ValidationResult(valid=False, reason=f"invalid JSON: {exc}")

    if not isinstance(data, dict):
        return ValidationResult(valid=False, reason="output is not a JSON object")

    missing = [k for k in required_keys if k not in data]
    if missing:
        return ValidationResult(
            valid=False, reason=f"missing required keys: {', '.join(missing)}"
        )

    stripped: list[str] = []
    if input_payload is not None:
        input_text = (
            input_payload
            if isinstance(input_payload, str)
            else json.dumps(input_payload, default=str)
        )
        allowed: set[str] = set()
        allowed_numeric: set[float] = set()
        for token in extract_numbers(input_text):
            allowed.add(token)
            try:
                allowed_numeric.add(float(token))
            except ValueError:
                pass

        def _is_grounded(value: float) -> bool:
            token = repr(value)
            if token in allowed or token in _TRIVIAL:
                return True
            if value in allowed_numeric:
                return True
            # 0.93 vs 0.9300000001: compare to a sane precision.
            for candidate in allowed_numeric:
                if abs(candidate - value) < 1e-9:
                    return True
            return False

        for key, value in list(data.items()):
            if isinstance(value, str):
                cleaned, bad = grounded_numbers(value, input_payload)
                if bad:
                    data[key] = cleaned
                    stripped.extend(bad)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                # A numeric field the model invented is nulled, not trusted.
                # The key stays so the schema still validates.
                if not _is_grounded(float(value)):
                    stripped.append(str(value))
                    data[key] = None

    return ValidationResult(valid=True, data=data, stripped_numbers=stripped)


class AIProvider(Protocol):
    """What a provider must implement."""

    name: str
    free_tier: bool

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> tuple[str, int, int]:
        """Return (text, tokens_in, tokens_out)."""
        ...


class QuotaExceeded(RuntimeError):
    """The provider's free-tier quota is exhausted."""


class ProviderError(RuntimeError):
    """The provider failed in a way we did not expect."""


@dataclass
class AISidecar:
    """The single entry point for every AI call in the system.

    Nothing else in the codebase calls a model directly. That is what makes
    the logging, the validation and the privacy check unavoidable.
    """

    provider: AIProvider
    capability: str
    prompt_version: str = "v1"
    prompt_template: str = ""
    temperature: float = 0.0
    seed: int = 19
    # 'public' may go to a free tier. 'private' may NOT.
    data_class: str = "public"
    required_keys: tuple[str, ...] = ()
    # Optional sink: a callable that records the run.
    log_sink: Any = None
    # Optional quota tracker. When the provider's free tier is exhausted the
    # sidecar refuses rather than hammering it.
    quota: Any = None
    # Optional per-task budget. When a ceiling breaks, the call is refused
    # BEFORE the request leaves the machine. This is what makes an agent loop
    # safe to run unattended.
    budget: Budget | None = None

    def run(
        self,
        prompt: str,
        *,
        input_payload: Any = None,
        expected_keys: tuple[str, ...] | None = None,
        user_id: str | None = None,
    ) -> ValidationResult:
        """Make one AI call, validate it, and log it.

        On ANY failure the caller gets a ValidationResult with valid=False.
        There is no path where a failure produces a usable-looking result.
        """
        # Privacy check BEFORE the request leaves the machine.
        if self.provider.free_tier and self.data_class != "public":
            raise DataClassError(
                f"Refusing to send '{self.data_class}' data to the free-tier "
                f"provider '{self.provider.name}'. Only public market news may "
                f"go to a free tier (I-19)."
            )

        # Quota check BEFORE the request leaves the machine. A 429 from the
        # provider is slower and noisier than not making the call.
        if self.quota is not None and getattr(self.provider, "free_tier", False):
            if self.quota.is_exhausted(self.provider.name):
                result = ValidationResult(
                    valid=False,
                    reason=(
                        f"The free-tier quota for '{self.provider.name}' is "
                        f"exhausted. Using the deterministic fallback."
                    ),
                )
                self._log(
                    {
                        "capability": self.capability,
                        "provider": self.provider.name,
                        "model_id": getattr(self.provider, "model_id", "unknown"),
                        "prompt_hash": hash_payload(self.prompt_template or prompt),
                        "prompt_version": self.prompt_version,
                        "seed": self.seed,
                        "temperature": self.temperature,
                        "input_hash": hash_payload(
                            input_payload if input_payload is not None else prompt
                        ),
                        "input_preview": _preview(input_payload),
                        "output_json": None,
                        "valid": False,
                        "invalid_reason": result.reason,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "latency_ms": 0,
                        "cost_usd": 0,
                        "data_class": self.data_class,
                        "user_id": user_id,
                    }
                )
                return result

        prompt_hash = hash_payload(self.prompt_template or prompt)
        input_hash = hash_payload(input_payload if input_payload is not None else prompt)

        # Budget check BEFORE the request leaves the machine. A loop that has
        # spent its allowance stops, it does not keep spending.
        if self.budget is not None:
            breach = self.budget.breach()
            if breach is not None:
                reason = (
                    f"The task budget is exhausted ({breach}). "
                    "No further model calls will be made."
                )
                self._log(
                    {
                        "capability": self.capability,
                        "provider": self.provider.name,
                        "model_id": getattr(self.provider, "model_id", "unknown"),
                        "prompt_hash": prompt_hash,
                        "prompt_version": self.prompt_version,
                        "seed": self.seed,
                        "temperature": self.temperature,
                        "input_hash": input_hash,
                        "input_preview": _preview(input_payload),
                        "output_json": None,
                        "valid": False,
                        "invalid_reason": reason,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "latency_ms": 0,
                        "cost_usd": 0,
                        "data_class": self.data_class,
                        "user_id": user_id,
                    }
                )
                return ValidationResult(valid=False, reason=reason)

        started = time.monotonic()
        text = ""
        tokens_in = 0
        tokens_out = 0
        error: str | None = None
        result = ValidationResult(valid=False, reason="call did not run")

        try:
            text, tokens_in, tokens_out = self.provider.complete(
                prompt, max_tokens=1024
            )
            if self.quota is not None:
                self.quota.record(self.provider.name)
            if self.budget is not None:
                self.budget.record(tokens_in=tokens_in, tokens_out=tokens_out)
            result = validate_json(
                text,
                expected_keys if expected_keys is not None else self.required_keys,
                input_payload,
            )
            if not result.valid:
                error = result.reason
        except QuotaExceeded as exc:
            error = f"quota exceeded: {exc}"
            result = ValidationResult(valid=False, reason=error)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            result = ValidationResult(valid=False, reason=error)

        latency_ms = int((time.monotonic() - started) * 1000)

        self._log(
            {
                "capability": self.capability,
                "provider": self.provider.name,
                "model_id": getattr(self.provider, "model_id", "unknown"),
                "prompt_hash": prompt_hash,
                "prompt_version": self.prompt_version,
                "seed": self.seed,
                "temperature": self.temperature,
                "input_hash": input_hash,
                "input_preview": _preview(input_payload),
                "output_json": result.data if result.valid else None,
                "valid": result.valid,
                "invalid_reason": result.reason,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
                "cost_usd": 0,
                "data_class": self.data_class,
                "user_id": user_id,
            }
        )

        if not result.valid:
            log.warning(
                "AI call failed capability=%s provider=%s reason=%s",
                self.capability,
                self.provider.name,
                result.reason,
            )
        elif result.stripped_numbers:
            log.warning(
                "AI output contained %d ungrounded number(s); they were stripped: %s",
                len(result.stripped_numbers),
                ", ".join(result.stripped_numbers[:5]),
            )

        return result

    def _log(self, row: dict[str, Any]) -> None:
        if self.log_sink is None:
            return
        try:
            self.log_sink(row)
        except Exception as exc:  # noqa: BLE001
            log.warning("AI run logging failed: %s", exc)


def _preview(payload: Any, limit: int = 400) -> str | None:
    """A short, safe preview of the input for the audit trail."""
    if payload is None:
        return None
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, default=str, separators=(",", ":"))
    )
    return text[:limit]
