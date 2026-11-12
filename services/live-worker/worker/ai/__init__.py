"""The AI sidecar package.

AI is a sidecar. See `sidecar.py` for the rules, `providers.py` for the
free-tier providers, `budget.py` for the ceilings that keep an agent loop
inside its quota, and `guardrails.py` for the checks that keep untrusted text
from giving orders.

The fallback here is the important part: when no provider is configured, or a
provider fails, every AI capability has a deterministic substitute. The app
keeps working. It simply says less.
"""

from .budget import Budget  # noqa: F401
from .guardrails import (  # noqa: F401
    InjectionVerdict,
    detect_prompt_injection,
    find_outcome_promises,
    sanitize_untrusted,
    wrap_untrusted,
)
from .providers import GeminiProvider, GroqProvider, NullProvider, build_provider  # noqa: F401
from .sidecar import (  # noqa: F401
    AISidecar,
    DataClassError,
    ProviderError,
    QuotaExceeded,
    ValidationResult,
    grounded_numbers,
    hash_payload,
    validate_json,
)

__all__ = [
    "AISidecar",
    "Budget",
    "DataClassError",
    "GeminiProvider",
    "GroqProvider",
    "InjectionVerdict",
    "NullProvider",
    "ProviderError",
    "QuotaExceeded",
    "ValidationResult",
    "build_provider",
    "detect_prompt_injection",
    "find_outcome_promises",
    "grounded_numbers",
    "hash_payload",
    "sanitize_untrusted",
    "validate_json",
    "wrap_untrusted",
]
