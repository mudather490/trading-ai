"""Tests for the AI quota monitor.

The monitor is what stops a free tier from silently killing the news
pipeline. It must warn before the wall, and the sidecar must refuse rather
than hammer a provider that has said no.

Run:  python -m pytest services/live-worker/tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.ai.quota import KNOWN_DAILY_CAPS, QuotaMonitor  # noqa: E402
from worker.ai.sidecar import AISidecar  # noqa: E402


class FakeProvider:
    name = "groq"
    free_tier = True
    model_id = "fake-model"

    def __init__(self, response: str = '{"ok": true}') -> None:
        self.response = response
        self.calls = 0

    def complete(self, prompt: str, *, max_tokens: int = 1024):
        self.calls += 1
        return self.response, 10, 5


def test_usage_accumulates():
    q = QuotaMonitor()
    q.record("groq")
    q.record("groq")
    assert q.state()["groq"]["used"] == 2


def test_an_unknown_provider_gets_a_default_cap():
    q = QuotaMonitor()
    q.record("brand-new-provider")
    assert "brand-new-provider" in q.state()


def test_the_quota_is_not_exhausted_at_the_start():
    q = QuotaMonitor()
    assert not q.is_exhausted("groq")


def test_the_quota_is_exhausted_at_the_cap():
    q = QuotaMonitor()
    for _ in range(KNOWN_DAILY_CAPS["groq"]):
        q.record("groq")
    assert q.is_exhausted("groq")


def test_the_state_reports_the_fraction():
    q = QuotaMonitor()
    for _ in range(KNOWN_DAILY_CAPS["groq"] // 2):
        q.record("groq")
    state = q.state()["groq"]
    assert state["fraction"] == 0.5
    assert state["exhausted"] is False


def test_the_sidecar_refuses_once_the_quota_is_exhausted():
    """The provider must not be called at all when the quota is gone."""
    q = QuotaMonitor()
    provider = FakeProvider()
    sidecar = AISidecar(
        provider=provider,
        capability="news_extract",
        required_keys=("ok",),
        quota=q,
    )

    # Exhaust the quota.
    for _ in range(KNOWN_DAILY_CAPS["groq"]):
        q.record("groq")

    result = sidecar.run("extract", input_payload={})
    assert not result.valid
    assert "quota" in (result.reason or "").lower()
    assert provider.calls == 0, "the provider was called despite an exhausted quota"


def test_the_sidecar_still_works_with_quota_left():
    q = QuotaMonitor()
    provider = FakeProvider()
    sidecar = AISidecar(
        provider=provider,
        capability="news_extract",
        required_keys=("ok",),
        quota=q,
    )
    result = sidecar.run("extract", input_payload={})
    assert result.valid
    assert provider.calls == 1
    assert q.state()["groq"]["used"] == 1


def test_a_paid_provider_is_not_quota_limited():
    """A paid provider must not be blocked by the free-tier tracker."""

    class PaidProvider(FakeProvider):
        name = "paid"
        free_tier = False

    q = QuotaMonitor()
    provider = PaidProvider()
    sidecar = AISidecar(
        provider=provider,
        capability="news_extract",
        required_keys=("ok",),
        quota=q,
    )
    result = sidecar.run("extract", input_payload={})
    assert result.valid
