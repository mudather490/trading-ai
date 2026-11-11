"""Free-tier quota monitoring.

Free AI tiers change without notice: models are added and withdrawn, and
limits move. A provider that stops working must degrade the app, not break
it, and the operator must be told before the app goes quiet.

This module tracks daily usage per provider and warns at 80% of the known
cap. It also records the quota state on the worker's heartbeat so the
dashboard can show it.

The caps are the documented free-plan numbers, and they are deliberately
conservative: we would rather warn early than hit a wall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

log = logging.getLogger(__name__)

# Documented free-plan caps. Gemini does not publish its numbers, so we use a
# conservative figure and rely on the provider's own 429 to correct us.
KNOWN_DAILY_CAPS = {
    "groq": 1000,
    "gemini": 1500,
    "openrouter": 50,  # until $10 of lifetime credits are purchased
}

WARN_AT = 0.80


@dataclass
class ProviderQuota:
    provider: str
    cap: int
    used: int = 0
    day: date = field(default_factory=date.today)
    warned: bool = False

    def record(self, count: int = 1) -> None:
        today = date.today()
        if today != self.day:
            self.day = today
            self.used = 0
            self.warned = False
        self.used += count

    @property
    def fraction(self) -> float:
        return self.used / self.cap if self.cap else 0.0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.cap

    def state(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "used": self.used,
            "cap": self.cap,
            "fraction": round(self.fraction, 4),
            "exhausted": self.exhausted,
            "warned": self.warned,
        }


class QuotaMonitor:
    """Tracks usage across providers and warns before we hit a wall."""

    def __init__(self) -> None:
        self._quotas: dict[str, ProviderQuota] = {
            name: ProviderQuota(provider=name, cap=cap)
            for name, cap in KNOWN_DAILY_CAPS.items()
        }

    def record(self, provider: str, count: int = 1) -> ProviderQuota:
        quota = self._quotas.get(provider)
        if quota is None:
            quota = ProviderQuota(provider=provider, cap=KNOWN_DAILY_CAPS.get(provider, 100))
            self._quotas[provider] = quota

        quota.record(count)

        if quota.fraction >= WARN_AT and not quota.warned:
            quota.warned = True
            log.warning(
                "AI quota at %.0f%% for %s (%d/%d). Switch provider or reduce "
                "AI use; the app degrades to deterministic fallbacks when a "
                "quota runs out.",
                quota.fraction * 100,
                quota.provider,
                quota.used,
                quota.cap,
            )

        return quota

    def is_exhausted(self, provider: str) -> bool:
        quota = self._quotas.get(provider)
        return quota.exhausted if quota else False

    def state(self) -> dict[str, object]:
        return {name: q.state() for name, q in self._quotas.items()}

    def summary(self) -> str:
        parts = [
            f"{name} {q.used}/{q.cap}" for name, q in sorted(self._quotas.items())
        ]
        return ", ".join(parts)
