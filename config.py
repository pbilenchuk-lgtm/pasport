"""Конфигурация монитора. Всё читается из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

TARGET_URL = "https://warszawa.pasport.org.ua/solutions/e-queue"

# Ниже 30 секунд опускаться нельзя — риск бана IP.
MIN_INTERVAL_SECONDS = 30


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} должен быть целым числом, получено: {raw!r}")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "y"}


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    # --- цель ---
    url: str = TARGET_URL

    # --- Telegram ---
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # --- как ходим на сайт ---
    # direct  — обычный HTTP-клиент (Вариант А из ТЗ)
    # browser — Playwright/Chromium (Вариант Б)
    # auto    — direct, а при блокировке автоматически переключиться на browser
    fetch_mode: str = "auto"
    # Один прокси. Если задан список ниже, он имеет приоритет.
    scrape_proxy: str = ""
    # Список прокси: прямо в переменной (через перевод строки/запятую) или файлом.
    proxy_list: str = ""
    proxy_list_path: str = ""
    # Сколько ошибок подряд терпеть, прежде чем взять следующий прокси.
    proxy_rotate_after: int = 3
    # Проверять ли весь список прокси при старте, оставляя только рабочие.
    proxy_precheck: bool = True
    proxy_check_timeout: int = 20

    # --- интервалы ---
    # Для direct: 45 ± 0..15 сек. Для browser: 60 + 0..30 = 60..90 сек.
    interval_seconds: int = 45
    jitter_seconds: int = 15
    browser_interval_seconds: int = 60
    browser_jitter_seconds: int = 30

    request_timeout: int = 40

    # --- обработка ошибок ---
    error_threshold: int = 5
    error_pause_minutes: int = 15

    # --- сердцебиение ---
    heartbeat_hour: int = 10
    heartbeat_timezone: str = "Europe/Warsaw"

    # --- состояние ---
    state_path: str = "state.json"

    # --- прочее ---
    notify_on_close: bool = False
    log_level: str = "INFO"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    )

    # Заголовки скопированы из HAR реального браузера (см. README, раздел «Разведка»).
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls(
            url=_env_str("TARGET_URL", TARGET_URL),
            telegram_token=_env_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env_str("TELEGRAM_CHAT_ID"),
            fetch_mode=_env_str("FETCH_MODE", "auto").lower(),
            scrape_proxy=_env_str("SCRAPE_PROXY"),
            proxy_list=os.environ.get("PROXY_LIST", ""),
            proxy_list_path=_env_str("PROXY_LIST_PATH"),
            proxy_rotate_after=_env_int("PROXY_ROTATE_AFTER", 3),
            proxy_precheck=_env_bool("PROXY_PRECHECK", True),
            proxy_check_timeout=_env_int("PROXY_CHECK_TIMEOUT", 20),
            interval_seconds=_env_int("INTERVAL_SECONDS", 45),
            jitter_seconds=_env_int("JITTER_SECONDS", 15),
            browser_interval_seconds=_env_int("BROWSER_INTERVAL_SECONDS", 60),
            browser_jitter_seconds=_env_int("BROWSER_JITTER_SECONDS", 30),
            request_timeout=_env_int("REQUEST_TIMEOUT", 40),
            error_threshold=_env_int("ERROR_THRESHOLD", 5),
            error_pause_minutes=_env_int("ERROR_PAUSE_MINUTES", 15),
            heartbeat_hour=_env_int("HEARTBEAT_HOUR", 10),
            heartbeat_timezone=_env_str("HEARTBEAT_TIMEZONE", "Europe/Warsaw"),
            state_path=_env_str("STATE_PATH", "state.json"),
            notify_on_close=_env_bool("NOTIFY_ON_CLOSE", False),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        )

        if cfg.fetch_mode not in {"auto", "direct", "browser"}:
            raise SystemExit(
                f"FETCH_MODE должен быть auto|direct|browser, получено: {cfg.fetch_mode!r}"
            )

        # Защита от слишком частых проверок — даже если кто-то поставит INTERVAL_SECONDS=5.
        if cfg.interval_seconds < MIN_INTERVAL_SECONDS:
            cfg.interval_seconds = MIN_INTERVAL_SECONDS
        if cfg.browser_interval_seconds < MIN_INTERVAL_SECONDS:
            cfg.browser_interval_seconds = MIN_INTERVAL_SECONDS
        cfg.jitter_seconds = max(0, cfg.jitter_seconds)
        cfg.browser_jitter_seconds = max(0, cfg.browser_jitter_seconds)

        return cfg

    def require_telegram(self) -> None:
        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", self.telegram_token),
                ("TELEGRAM_CHAT_ID", self.telegram_chat_id),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "Не заданы переменные окружения: "
                + ", ".join(missing)
                + ". См. README, раздел «Переменные окружения»."
            )
