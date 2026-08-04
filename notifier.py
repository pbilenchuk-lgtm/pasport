"""Отправка сообщений в Telegram. Только POST на api.telegram.org, без библиотек."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT = 20


class Notifier:
    """Тонкая обёртка над Bot API.

    Ошибки отправки не пробрасываются наружу: если Telegram недоступен, монитор
    должен продолжать проверять сайт, а не падать.
    """

    def __init__(self, token: str, chat_id: str, disabled: bool = False) -> None:
        self.token = token
        self.chat_id = chat_id
        self.disabled = disabled

    def send(self, text: str, silent: bool = False) -> bool:
        """Отправить сообщение. Возвращает True при успехе."""
        if self.disabled:
            log.info("[telegram отключён] %s", text)
            return True

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            # Уведомления о датах приходят со звуком — это главный смысл сервиса.
            "disable_notification": silent,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{API_BASE}/bot{self.token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            log.error("Telegram HTTP %s: %s", exc.code, detail)
            return False
        except Exception as exc:
            log.error("Telegram недоступен: %s", exc)
            return False

        if not body.get("ok"):
            log.error("Telegram вернул ошибку: %s", body)
            return False

        log.info("Telegram: сообщение отправлено")
        return True

    def get_updates(self) -> dict:
        """Вспомогательное: посмотреть входящие, чтобы узнать свой chat_id."""
        url = f"{API_BASE}/bot{self.token}/getUpdates"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
