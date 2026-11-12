"""AI providers.

Free tiers only for now. Two of them, for two different reasons:

  * Gemini  -- the most generous free quota for BULK work (news extraction).
               Free-tier content may be used to improve Google's products, so
               only PUBLIC MARKET NEWS goes here.
  * Groq    -- fast and small models, good for narration and chat. 30 RPM,
               1,000 requests/day on the free plan.

OpenRouter's free models are capped at 50 requests/day until $10 of lifetime
credits are purchased, which is too few for a news pipeline. We keep the
provider interface open so it can be added later without touching callers.

Both providers implement the same interface, so the sidecar is provider-agnostic
and the quota can move from one to the other by changing config.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from .sidecar import ProviderError, QuotaExceeded

log = logging.getLogger(__name__)

# Free-tier daily request caps we design against. The sidecar's quota monitor
# warns at 80% of these.
GROQ_FREE_DAILY = 1000
GEMINI_FREE_DAILY = 1500  # conservative: Google does not publish the number


class BaseProvider:
    """Shared HTTP plumbing. Uses urllib so the worker needs no extra deps."""

    name = "base"
    free_tier = True
    model_id = "unknown"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.requests_today = 0
        self._day = time.strftime("%Y-%m-%d")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _count_request(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self.requests_today = 0
        self.requests_today += 1

    def _post_json(self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: int = 60) -> dict:
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 429:
                raise QuotaExceeded(f"{self.name}: {exc.code} {detail}") from exc
            raise ProviderError(f"{self.name}: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self.name}: {exc.reason}") from exc


class GroqProvider(BaseProvider):
    """Groq free plan. Fast, small models, 30 RPM / 1,000 RPD."""

    name = "groq"
    free_tier = True

    def __init__(self, api_key: str | None = None, model_id: str | None = None) -> None:
        super().__init__(api_key or os.environ.get("GROQ_API_KEY"))
        self.model_id = model_id or os.environ.get(
            "GROQ_MODEL", "openai/gpt-oss-120b"
        )
        self.base_url = "https://api.groq.com/openai/v1"

    @property
    def daily_cap(self) -> int:
        return GROQ_FREE_DAILY

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> tuple[str, int, int]:
        if not self.configured:
            raise ProviderError("GROQ_API_KEY is not set")

        self._count_request()
        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "seed": 19,
            "max_tokens": max_tokens,
        }
        result = self._post_json(
            f"{self.base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )
        try:
            text = result["choices"][0]["message"]["content"]
            usage = result.get("usage", {})
            return (
                text,
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"groq: unexpected response shape: {exc}") from exc


class GeminiProvider(BaseProvider):
    """Google Gemini free tier. Best for bulk news extraction.

    Free-tier content may be used to improve Google's products. Only public
    market news is ever sent here (I-19).
    """

    name = "gemini"
    free_tier = True

    def __init__(self, api_key: str | None = None, model_id: str | None = None) -> None:
        super().__init__(api_key or os.environ.get("GEMINI_API_KEY"))
        self.model_id = model_id or os.environ.get(
            "GEMINI_MODEL", "gemini-2.5-flash"
        )
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    @property
    def daily_cap(self) -> int:
        return GEMINI_FREE_DAILY

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> tuple[str, int, int]:
        if not self.configured:
            raise ProviderError("GEMINI_API_KEY is not set")

        self._count_request()
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        url = (
            f"{self.base_url}/models/{self.model_id}:generateContent"
            f"?key={self.api_key}"
        )
        result = self._post_json(url, {"Content-Type": "application/json"}, payload)
        try:
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            usage = result.get("usageMetadata", {})
            return (
                text,
                int(usage.get("promptTokenCount", 0)),
                int(usage.get("candidatesTokenCount", 0)),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"gemini: unexpected response shape: {exc}") from exc


class NullProvider(BaseProvider):
    """A provider that always fails.

    Used when no API key is configured, so the app degrades to templates
    instead of raising (I-20: deterministic fallback for every AI path).
    """

    name = "null"
    free_tier = False

    @property
    def configured(self) -> bool:
        return True

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> tuple[str, int, int]:
        raise ProviderError("No AI provider is configured. Using the deterministic fallback.")


def build_provider(preferred: str | None = None) -> BaseProvider:
    """Pick a provider from the environment.

    Preference order: an explicitly named one, then Groq, then Gemini.
    Falls back to NullProvider so the worker never dies from a missing key.
    """
    name = (preferred or os.environ.get("AI_PROVIDER", "")).strip().lower()

    candidates = {
        "groq": lambda: GroqProvider(),
        "gemini": lambda: GeminiProvider(),
    }

    if name and name in candidates:
        provider = candidates[name]()
        if provider.configured:
            return provider
        log.warning("AI_PROVIDER=%s but no key is set; falling back.", name)

    for factory in candidates.values():
        provider = factory()
        if provider.configured:
            return provider

    log.warning("No AI provider configured. The app will use deterministic fallbacks.")
    return NullProvider()


# Which provider does which job.
#
# Two free tiers, two jobs. Groq is fast and its quota is the smallest, so it
# does the interactive work (explanations, the evidence-bound thesis, the
# ranking, and the Tier A parameter proposals). Gemini has the generous bulk
# quota, so it does the news extraction and clustering.
JOB_ROUTES: dict[str, tuple[str, ...]] = {
    "chat": ("groq", "gemini"),
    "thesis": ("groq", "gemini"),
    "ranking": ("groq", "gemini"),
    "mutation": ("groq", "gemini"),
    "news": ("gemini", "groq"),
}


class ProviderRouter:
    """Picks the provider by the JOB, not by a single global preference.

    One key for everything wastes the shape of each provider: Groq's quota is
    small but its latency is tiny, and Gemini's quota is large but slower.
    The router sends the work to the right one and falls back to whichever
    is configured, so a missing key never breaks a feature — it only changes
    which model answers.
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {
            "groq": GroqProvider(),
            "gemini": GeminiProvider(),
        }

    def for_job(self, job: str) -> BaseProvider:
        for name in JOB_ROUTES.get(job, ("groq", "gemini")):
            provider = self._providers[name]
            if provider.configured:
                return provider
        for provider in self._providers.values():
            if provider.configured:
                return provider
        return NullProvider()

    def state(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "configured": provider.configured,
                "model_id": getattr(provider, "model_id", "unknown"),
                "daily_cap": getattr(provider, "daily_cap", 0),
            }
            for name, provider in self._providers.items()
        }
