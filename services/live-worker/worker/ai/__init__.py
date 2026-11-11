"""The AI sidecar package.

AI is a sidecar. See `sidecar.py` for the rules and `providers.py` for the
free-tier providers.

The fallback here is the important part: when no provider is configured, or a
provider fails, every AI capability has a deterministic substitute. The app
keeps working. It simply says less.
"""

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
    "DataClassError",
    "GeminiProvider",
    "GroqProvider",
    "NullProvider",
    "ProviderError",
    "QuotaExceeded",
    "ValidationResult",
    "build_provider",
    "grounded_numbers",
    "hash_payload",
    "validate_json",
]
