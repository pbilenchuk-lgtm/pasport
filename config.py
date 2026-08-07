"""Конфигурация монитора. Всё читается из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

TARGET_URL = "https://warszawa.pasport.org.ua/solutions/e-queue"

# Файл с настройками рядом с кодом. Нужен в первую очередь для Windows: там
# монитор запускает планировщик задач, и обычные переменные среды задавать
# неудобно. Настоящие переменные окружения всегда важнее файла.
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# Ниже 30 секунд опускаться нельзя — риск бана IP.
MIN_INTERVAL_SECONDS = 30


def load_env_file(path: str = ENV_FILE) -> int:
    """Подгрузить настройки из .env. Возвращает число применённых строк.

    Формат простой: KEY=VALUE, строки с # игнорируются. Значение, уже заданное
    в окружении, не перезаписывается — так переменные Render/systemd остаются
    главнее файла.
    """
    if not os.path.exists(path):
        return 0

    applied = 0
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
                    applied += 1
    except OSError:
        return applied

    return applied


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
    # Подпись под сообщениями. Пусто — определится само (имя компьютера или
    # название сервиса на хостинге).
    instance_name: str = ""

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
    # Сколько часов доверять прошлой проверке прокси, не повторяя её.
    proxy_cache_hours: int = 6

    # --- интервалы ---
    # Для direct: 45 ± 0..15 сек. Для browser: 60 + 0..30 = 60..90 сек.
    interval_seconds: int = 45
    jitter_seconds: int = 15
    browser_interval_seconds: int = 60
    browser_jitter_seconds: int = 30

    request_timeout: int = 40
    # Сколько ждать, пока браузер пройдёт челлендж Cloudflare.
    challenge_wait_seconds: int = 60
    # Headless-браузер Cloudflare часто не пропускает. В Docker-образе мы
    # запускаем настоящий (headful) Chromium под виртуальным дисплеем Xvfb.
    browser_headless: bool = True
    # Размер окна. Меньше окно — меньше памяти под кадровый буфер, а на дешёвом
    # тарифе контейнер убивали именно за перерасход.
    browser_width: int = 1280
    browser_height: int = 800

    # --- обработка ошибок ---
    error_threshold: int = 5
    error_pause_minutes: int = 15

    # --- сердцебиение ---
    heartbeat_hour: int = 10
    heartbeat_timezone: str = "Europe/Warsaw"
    # Не слать приветствие чаще, чем раз в столько минут: перезапуски идут
    # пачками, а одинаковые сообщения подряд читать невозможно.
    startup_notice_cooldown_minutes: int = 60

    # --- состояние ---
    state_path: str = "state.json"

    # --- прочее ---
    notify_on_close: bool = False
    log_level: str = "INFO"
    # Файл для логов. Нужен, когда монитор работает фоном без консоли (Windows).
    log_file: str = ""
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    )

    # Заголовки скопированы из HAR реального браузера (см. README, раздел «Разведка»).
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        load_env_file()

        cfg = cls(
            url=_env_str("TARGET_URL", TARGET_URL),
            telegram_token=_env_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env_str("TELEGRAM_CHAT_ID"),
            instance_name=_env_str("INSTANCE_NAME"),
            fetch_mode=_env_str("FETCH_MODE", "auto").lower(),
            scrape_proxy=_env_str("SCRAPE_PROXY"),
            proxy_list=os.environ.get("PROXY_LIST", ""),
            proxy_list_path=_env_str("PROXY_LIST_PATH"),
            proxy_rotate_after=_env_int("PROXY_ROTATE_AFTER", 3),
            proxy_precheck=_env_bool("PROXY_PRECHECK", True),
            proxy_check_timeout=_env_int("PROXY_CHECK_TIMEOUT", 20),
            proxy_cache_hours=_env_int("PROXY_CACHE_HOURS", 6),
            interval_seconds=_env_int("INTERVAL_SECONDS", 45),
            jitter_seconds=_env_int("JITTER_SECONDS", 15),
            browser_interval_seconds=_env_int("BROWSER_INTERVAL_SECONDS", 60),
            browser_jitter_seconds=_env_int("BROWSER_JITTER_SECONDS", 30),
            request_timeout=_env_int("REQUEST_TIMEOUT", 40),
            challenge_wait_seconds=_env_int("CHALLENGE_WAIT_SECONDS", 60),
            browser_headless=_env_bool("BROWSER_HEADLESS", True),
            browser_width=_env_int("BROWSER_WIDTH", 1280),
            browser_height=_env_int("BROWSER_HEIGHT", 800),
            error_threshold=_env_int("ERROR_THRESHOLD", 5),
            error_pause_minutes=_env_int("ERROR_PAUSE_MINUTES", 15),
            heartbeat_hour=_env_int("HEARTBEAT_HOUR", 10),
            heartbeat_timezone=_env_str("HEARTBEAT_TIMEZONE", "Europe/Warsaw"),
            startup_notice_cooldown_minutes=_env_int("STARTUP_NOTICE_COOLDOWN_MINUTES", 60),
            state_path=_env_str("STATE_PATH", "state.json"),
            notify_on_close=_env_bool("NOTIFY_ON_CLOSE", False),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            log_file=_env_str("LOG_FILE"),
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
