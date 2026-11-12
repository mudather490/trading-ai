"""Outbound Telegram delivery.

The split of labour between the two tiers is deliberate:

  * INBOUND commands live on the web tier (Vercel). Telegram pushes the
    update to the webhook, the webhook answers. Vercel may be cold, but a
    command arrives because a human asked.
  * OUTBOUND alerts live here, on the always-on worker. A signal that fires
    at 3am must reach the phone, and Vercel Hobby sleeps.

The worker holds the bot token. The web tier holds it too (it must, to
answer a command), but only the worker sends unsolicited messages.

Delivery rules:

  * Only alerts whose channel is 'telegram' are sent. The channel is fixed
    when the alert is filed.
  * An alert with no linked chat is left pending, not dropped. It will be
    delivered the moment the user links a chat. Silently discarding a signal
    because the phone was not set up yet would be the worst possible
    behaviour.
  * The message repeats the data-quality label. A ticket built on IEX-only
    data must not look consolidated in a chat message.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class TelegramSender:
    """A very small Bot API client.

    Deliberately not a dependency: this is four HTTP calls and one JSON body.
    """

    def __init__(self, token: str | None) -> None:
        self._token = (token or "").strip()

    @classmethod
    def from_env(cls) -> "TelegramSender":
        return cls(os.environ.get("TELEGRAM_BOT_TOKEN"))

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def send(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        if not self._token:
            return False

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        request = urllib.request.Request(
            f"{API_BASE}/bot{self._token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            if not body.get("ok"):
                log.warning("telegram refused the message: %s", body.get("description"))
                return False
            return True
        except urllib.error.HTTPError as exc:
            log.warning("telegram HTTP %s", exc.code)
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram send failed: %s", exc)
        return False


def format_alert(alert: dict[str, Any]) -> str:
    """Render an alert for a phone.

    Plain text only. The body of an alert may quote a news headline, and
    Telegram's parsers would turn untrusted text into a broken message.
    """
    payload = alert.get("payload") or {}
    lines = [str(alert.get("title") or "Signal")]

    body = alert.get("body")
    if body:
        lines.append("")
        lines.append(str(body))

    symbol = payload.get("symbol")
    timeframe = payload.get("timeframe")
    if symbol and timeframe:
        lines.append("")
        lines.append(f"{symbol} · {timeframe}")

    quality = payload.get("data_quality")
    if quality:
        lines.append(f"Data quality: {quality}")

    close_time = payload.get("bar_close_time")
    if close_time:
        lines.append(f"Bar close: {close_time}")

    lines.append("")
    lines.append("The app advises. You place the trade.")
    return "\n".join(lines)


class TelegramDelivery:
    """Drains the pending Telegram alerts."""

    def __init__(self, writer: Any, sender: TelegramSender) -> None:
        self.writer = writer
        self.sender = sender
        # Alerts we have already found undeliverable. Logged once, not every
        # cycle.
        self._orphaned: set[str] = set()

    @property
    def configured(self) -> bool:
        return self.sender.configured

    def deliver_once(self) -> int:
        """Send what can be sent. Returns the number of messages sent."""
        if not self.sender.configured:
            return 0

        alerts = self.writer.fetch_pending_alerts(limit=20, channel="telegram")
        sent = 0
        for alert in alerts:
            alert_id = str(alert.get("id"))
            user_id = alert.get("user_id")
            if not user_id:
                continue

            chat_id = self.writer.fetch_telegram_chat(str(user_id))
            if chat_id is None:
                if alert_id not in self._orphaned:
                    self._orphaned.add(alert_id)
                    log.info(
                        "alert %s has no linked Telegram chat; leaving it pending",
                        alert_id,
                    )
                continue

            if not self.sender.send(chat_id, format_alert(alert)):
                # Leave it pending: a failed send is not a delivered message.
                continue

            self.writer.mark_alert_sent(alert_id)
            self._orphaned.discard(alert_id)
            sent += 1
            log.info("telegram: delivered alert %s to chat %s", alert_id, chat_id)

        return sent


__all__ = ["TelegramDelivery", "TelegramSender", "format_alert"]
