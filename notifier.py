"""Отправка сообщений в Telegram. Только POST на api.telegram.org, без библиотек."""

from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT = 20

# Типовые ошибки Bot API и что с ними делать. Без подсказки они выглядят
# загадочно: самая частая — бот физически не может написать первым тому,
# кто не нажал у него /start.
ERROR_HINTS = (
    (
        "chat not found",
        "Telegram не знает такой чат. Две причины: либо TELEGRAM_CHAT_ID неверный, "
        "либо ты ещё не открывал чат с ботом. Бот НЕ МОЖЕТ написать первым — "
        "зайди в Telegram, найди своего бота и нажми /start, потом перезапусти сервис.",
    ),
    (
        "bot can't initiate conversation",
        "Бот не может написать первым. Открой чат с ботом в Telegram и нажми /start.",
    ),
    (
        "bot was blocked by the user",
        "Ты заблокировал бота в Telegram. Разблокируй его и нажми /start.",
    ),
    (
        "unauthorized",
        "TELEGRAM_BOT_TOKEN неверный или отозван. Возьми свежий токен у BotFather.",
    ),
    (
        "chat_id is empty",
        "TELEGRAM_CHAT_ID пустой. Узнать свой id: python monitor.py --chat-id",
    ),
)


def detect_source() -> str:
    """Понять, откуда работает монитор, чтобы подписывать сообщения.

    Один и тот же бот может обслуживать несколько копий — например, домашний
    компьютер и сервер. Без подписи непонятно, кто прислал сообщение и какую
    копию чинить, если что-то пошло не так.
    """
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_NAME"):
        return "☁️ " + (os.environ.get("RENDER_SERVICE_NAME") or "Render")

    for variable in ("DYNO", "KUBERNETES_SERVICE_HOST", "AWS_EXECUTION_ENV"):
        if os.environ.get(variable):
            return "☁️ сервер"

    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    return f"🖥 {host}" if host else "🖥 этот компьютер"


def explain(description: str) -> str:
    """Превратить ответ Telegram в понятную подсказку."""
    lowered = description.lower()
    for marker, hint in ERROR_HINTS:
        if marker in lowered:
            return hint
    return ""


class Notifier:
    """Тонкая обёртка над Bot API.

    Ошибки отправки не пробрасываются наружу: если Telegram недоступен, монитор
    должен продолжать проверять сайт, а не падать.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        disabled: bool = False,
        source: str = "",
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.disabled = disabled
        # Подпись источника: к одному боту может быть подключено несколько копий.
        self.source = source or detect_source()
        # Последняя подсказка по ошибке — её показывает самопроверка при старте.
        self.last_error = ""

    def sign(self, text: str) -> str:
        """Добавить подпись источника отдельной строкой в конце."""
        if not self.source:
            return text
        return f"{text}\n\n<i>{self.source}</i>"

    def send(self, text: str, silent: bool = False) -> bool:
        """Отправить сообщение. Возвращает True при успехе."""
        if self.disabled:
            log.info("[telegram отключён] %s", text)
            return True

        payload = {
            "chat_id": self.chat_id,
            "text": self.sign(text),
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
            try:
                description = json.loads(detail).get("description", detail)
            except ValueError:
                description = detail
            self._report_failure(f"HTTP {exc.code}: {description}", description)
            return False
        except Exception as exc:
            self.last_error = f"Telegram недоступен: {exc}"
            log.error("%s", self.last_error)
            return False

        if not body.get("ok"):
            description = str(body.get("description", body))
            self._report_failure(f"Telegram вернул ошибку: {description}", description)
            return False

        self.last_error = ""
        log.info("Telegram: сообщение отправлено")
        return True

    def _report_failure(self, summary: str, description: str) -> None:
        hint = explain(description)
        self.last_error = f"{summary}. {hint}".strip()
        log.error("%s", summary)
        if hint:
            log.error("ЧТО ДЕЛАТЬ: %s", hint)

    def get_updates(self) -> dict:
        """Вспомогательное: посмотреть входящие, чтобы узнать свой chat_id."""
        url = f"{API_BASE}/bot{self.token}/getUpdates"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
