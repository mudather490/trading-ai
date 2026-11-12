"""Per-task budget for AI work.

A budget is four independent ceilings. The first one that breaks stops the
work; it never silently truncates it.

    max_tokens   cumulative prompt + completion tokens
    max_cost_usd dollar ceiling (free tiers are 0.00, so this only bites
                 once a paid provider is added)
    max_seconds  wall-clock seconds for the whole task
    max_calls    number of model calls

Why this exists: a free tier is a shared, fragile resource, and an agent
loop that re-plans can turn one question into two hundred calls. The cap is
the thing that makes the loop safe to run unattended overnight.

Deliberately free of any provider-specific pricing table. Pricing rots; a
conservative ceiling does not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Budget:
    """Four ceilings and the counters that measure against them."""

    max_tokens: int = 200_000
    max_cost_usd: float = 1.0
    max_seconds: float = 120.0
    max_calls: int = 8

    tokens_used: int = 0
    cost_used: float = 0.0
    calls_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------ accounting --

    def record(
        self,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Account one completed model call."""
        self.tokens_used += max(0, int(tokens_in)) + max(0, int(tokens_out))
        self.cost_used += max(0.0, float(cost_usd))
        self.calls_used += 1

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    # --------------------------------------------------------------- checks --

    def breach(self) -> str | None:
        """The name of the first broken ceiling, or None."""
        if self.tokens_used >= self.max_tokens:
            return "tokens"
        if self.cost_used >= self.max_cost_usd:
            return "cost"
        if self.elapsed_seconds() >= self.max_seconds:
            return "wall_clock"
        if self.calls_used >= self.max_calls:
            return "calls"
        return None

    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def burn_fraction(self) -> float:
        """How close the task is to any ceiling, as a fraction in [0, 1+]."""
        parts = [
            self.tokens_used / max(self.max_tokens, 1),
            self.cost_used / max(self.max_cost_usd, 1e-9),
            self.elapsed_seconds() / max(self.max_seconds, 1e-9),
            self.calls_used / max(self.max_calls, 1),
        ]
        return max(parts)

    # ------------------------------------------------------------- reporting --

    def snapshot(self) -> dict:
        return {
            "tokens_used": self.tokens_used,
            "max_tokens": self.max_tokens,
            "cost_used": round(self.cost_used, 6),
            "max_cost_usd": self.max_cost_usd,
            "calls_used": self.calls_used,
            "max_calls": self.max_calls,
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
            "max_seconds": self.max_seconds,
        }
