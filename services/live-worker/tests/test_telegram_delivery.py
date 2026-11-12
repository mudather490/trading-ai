"""Telegram delivery.

The property under test: an alert is delivered ONLY when a chat is linked,
and it is marked sent ONLY when Telegram accepted it. A failed send must not
look like a delivered message.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.telegram import TelegramDelivery, TelegramSender, format_alert  # noqa: E402


class FakeWriter:
    def __init__(self, alerts, chats):
        self._alerts = alerts
        self._chats = chats
        self.sent: list[str] = []
        self.fetched_channel: str | None = None

    def fetch_pending_alerts(self, limit=20, channel=None):
        self.fetched_channel = channel
        return self._alerts[:limit]

    def fetch_telegram_chat(self, user_id):
        return self._chats.get(user_id)

    def mark_alert_sent(self, alert_id):
        self.sent.append(alert_id)


class FakeSender(TelegramSender):
    def __init__(self, accept=True):
        super().__init__("test-token")
        self.accept = accept
        self.messages: list[tuple[int, str]] = []

    def send(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text))
        return self.accept


ALERT = {
    "id": "alert-1",
    "user_id": "user-1",
    "kind": "signal",
    "title": "BUY BTCUSDT · 1h",
    "body": "S18 Rotation v1 fired rotation_ma at 100.0 on the closed 1h bar.",
    "payload": {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "side": "buy",
        "price": 100.0,
        "data_quality": "official",
        "bar_close_time": "2026-01-01T00:00:00+00:00",
    },
}


def test_format_keeps_the_data_quality_label():
    text = format_alert(ALERT)
    assert "BUY BTCUSDT" in text
    assert "BTCUSDT · 1h" in text
    assert "Data quality: official" in text
    assert "Bar close: 2026-01-01T00:00:00+00:00" in text


def test_a_linked_chat_receives_the_alert_and_it_is_marked_sent():
    writer = FakeWriter([ALERT], {"user-1": 555})
    sender = FakeSender()
    delivery = TelegramDelivery(writer, sender)

    assert delivery.deliver_once() == 1
    assert len(sender.messages) == 1
    assert sender.messages[0][0] == 555
    assert writer.sent == ["alert-1"]
    # Only Telegram-destined alerts are drained.
    assert writer.fetched_channel == "telegram"


def test_an_alert_without_a_chat_stays_pending():
    writer = FakeWriter([ALERT], {})
    sender = FakeSender()
    delivery = TelegramDelivery(writer, sender)

    assert delivery.deliver_once() == 0
    assert sender.messages == []
    assert writer.sent == []


def test_a_refused_send_is_not_marked_sent():
    writer = FakeWriter([ALERT], {"user-1": 555})
    sender = FakeSender(accept=False)
    delivery = TelegramDelivery(writer, sender)

    assert delivery.deliver_once() == 0
    assert writer.sent == []


def test_no_token_means_no_delivery():
    writer = FakeWriter([ALERT], {"user-1": 555})
    delivery = TelegramDelivery(writer, TelegramSender(None))

    assert delivery.configured is False
    assert delivery.deliver_once() == 0
    assert writer.sent == []


def test_the_delivery_task_never_returns_early():
    """A background task that returns takes the whole worker down.

    The main loop used to wait for FIRST_COMPLETED, so the Telegram task
    returning early (because no bot token was configured) killed the worker
    one second after it started. The task must block until the stop event.
    """
    import asyncio

    from worker.main import LiveWorker

    class Harness:
        run_telegram = LiveWorker.run_telegram

        def __init__(self) -> None:
            self._stop = asyncio.Event()
            self.telegram = TelegramDelivery(
                FakeWriter([], {}), TelegramSender(None)
            )

    async def scenario() -> bool:
        worker = Harness()
        task = asyncio.create_task(worker.run_telegram())
        await asyncio.sleep(0.05)
        still_running = not task.done()
        worker._stop.set()
        await asyncio.wait_for(task, timeout=2)
        return still_running

    assert asyncio.run(scenario()) is True
