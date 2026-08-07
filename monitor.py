#!/usr/bin/env python3
"""Монитор свободных дат электронной очереди (паспортный сервис, Варшава).

Проверяет https://warszawa.pasport.org.ua/solutions/e-queue и шлёт уведомление
в Telegram, когда появляются свободные даты.

Запуск:
    python monitor.py                  # основной цикл
    python monitor.py --once           # одна проверка, вывести результат и выйти
    python monitor.py --test-telegram  # проверить, что уведомления доходят
    python monitor.py --chat-id        # подсказать свой TELEGRAM_CHAT_ID
    python monitor.py --simulate tests/fixtures/open.html   # прогон парсера на файле
    python monitor.py --simulate-open  # полный цикл на подменённом ответе «даты есть»
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import logging
import random
import signal
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import parser as queue_parser
import state as state_module
import sysinfo
from config import Config
from fetcher import (
    BlockedError,
    BrowserFetcher,
    CaptchaError,
    FetchError,
    VERDICT_CHALLENGE,
    VERDICT_OK,
    build_fetcher,
    check_proxies,
    usable_proxies,
)
from notifier import Notifier
from proxies import ProxyPool, load_proxies
from proxies import mask as proxy_mask

log = logging.getLogger("monitor")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    log.info("Получен сигнал %s — завершаемся после текущей проверки", signum)
    _stop = True


def setup_logging(level: str, log_file: str = "") -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        # Фоновый запуск (планировщик Windows) идёт без консоли — без файла
        # логов разбираться в проблемах будет не по чему.
        from logging.handlers import RotatingFileHandler

        try:
            handlers.append(
                RotatingFileHandler(
                    log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
                )
            )
        except OSError as exc:
            print(f"Не удалось открыть файл логов {log_file}: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    # httpx логирует каждый запрос на INFO — в нашем цикле это лишний шум.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def resolve_timezone(name: str):
    """Часовой пояс для сердцебиения, с откатом на UTC.

    В минимальных образах (в том числе в официальном образе Playwright) базы
    часовых поясов может не быть. Ронять из-за этого весь монитор нельзя:
    сердцебиение — вспомогательная функция, а слежение за датами — основная.
    """
    try:
        return ZoneInfo(name)
    except Exception as exc:
        log.warning(
            "Часовой пояс %s недоступен (%s) — считаю время по UTC. "
            "Чтобы починить, установи пакет tzdata.",
            name,
            exc,
        )
        return timezone.utc


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Monitor:
    def __init__(self, cfg: Config, notifier: Notifier) -> None:
        self.cfg = cfg
        self.notifier = notifier
        self.state = state_module.load(cfg.state_path)
        self.pool = ProxyPool(load_proxies(cfg.proxy_list, cfg.proxy_list_path))
        if self.pool.enabled:
            log.info(
                "Загружено прокси: %d, начинаю с %s",
                len(self.pool),
                proxy_mask(self.pool.current()),
            )
        self.fetcher = build_fetcher(cfg, self._current_proxy)
        self.tz = resolve_timezone(cfg.heartbeat_timezone)
        self.consecutive_errors = 0
        self.last_errors: list[Exception] = []
        self.switched_to_browser = False
        self.rotations_without_success = 0

    # ---------------------------------------------------------------- helpers

    def now(self) -> datetime:
        return datetime.now(timezone.utc).astimezone(self.tz)

    def _current_proxy(self) -> str | None:
        """Адрес прокси для очередного запроса: из пула, иначе из SCRAPE_PROXY."""
        if self.pool.enabled:
            return self.pool.current()
        return self.cfg.scrape_proxy or None

    def precheck_proxies(self) -> None:
        """Прогнать весь список прокси параллельно и оставить только рабочие.

        Список прокси — расходник: часть адресов мертва, часть забанена сайтом.
        Дешевле проверить их разом за пару минут на старте, чем ловить по одному
        в основном цикле по 40 секунд на таймаут.
        """
        if not self.pool.enabled or not self.cfg.proxy_precheck:
            return

        if self.use_cached_proxies():
            return

        log.info("Проверяю %d прокси (параллельно, это займёт минуту)...", len(self.pool))
        results = check_proxies(self.cfg, self.pool.proxies)

        browser_mode = self.cfg.fetch_mode in {"browser", "auto"}
        usable = usable_proxies(results, browser_mode=browser_mode)

        clean = sum(1 for _, verdict in results if verdict == VERDICT_OK)
        challenged = sum(1 for _, verdict in results if verdict == VERDICT_CHALLENGE)
        log.info(
            "Итог проверки: пропускают сразу — %d, отдают челлендж — %d, забанены — %d",
            clean,
            challenged,
            len(results) - clean - challenged,
        )

        if not usable:
            hint = ""
            if challenged and not browser_mode:
                hint = (
                    f" {challenged} прокси отдают челлендж — его проходит браузер, "
                    "поставь FETCH_MODE=browser."
                )
            log.error(
                "Ни один из %d прокси не годится для режима %s.%s",
                len(self.pool),
                self.cfg.fetch_mode,
                hint,
            )
            self.notifier.send(
                f"⚠️ Ни один из {len(self.pool)} прокси не подошёл. "
                "Монитор запущен, но достучаться до сайта не может." + hint
            )
            return

        if not clean and challenged:
            log.info(
                "Прокси без челленджа нет — работаем через те, что отдают челлендж: "
                "браузер пройдёт его сам, полученная кука живёт в контексте"
            )

        log.info("Годных прокси: %d. Использую %s", len(usable), proxy_mask(usable[0]))
        self.remember_proxies(usable)
        self.pool = ProxyPool(usable)

    def proxy_signature(self) -> str:
        """Отпечаток списка прокси — чтобы заметить, что список подменили."""
        joined = "\n".join(self.pool.proxies)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    def use_cached_proxies(self) -> bool:
        """Взять годные прокси из прошлой проверки, если она ещё свежая.

        Перезапуски на хостинге случаются часто, а полная проверка списка стоит
        полторы минуты и заметного расхода памяти. Кэш убирает эту работу.
        """
        if not self.state.proxy_usable_indices:
            return False
        if self.state.proxy_signature != self.proxy_signature():
            log.info("Список прокси изменился — проверяю заново")
            return False

        age_hours = (time.time() - self.state.proxy_checked_at) / 3600
        if age_hours > self.cfg.proxy_cache_hours:
            log.info("Прошлая проверка прокси устарела (%.1f ч) — проверяю заново", age_hours)
            return False

        usable = [
            self.pool.proxies[i]
            for i in self.state.proxy_usable_indices
            if 0 <= i < len(self.pool.proxies)
        ]
        if not usable:
            return False

        log.info(
            "Беру %d годных прокси из проверки %.1f ч назад. Использую %s",
            len(usable),
            age_hours,
            proxy_mask(usable[0]),
        )
        self.pool = ProxyPool(usable)
        return True

    def remember_proxies(self, usable: list[str]) -> None:
        """Запомнить результат проверки, чтобы не повторять его при перезапуске."""
        index_by_proxy = {proxy: i for i, proxy in enumerate(self.pool.proxies)}
        self.state.proxy_signature = self.proxy_signature()
        self.state.proxy_usable_indices = [
            index_by_proxy[p] for p in usable if p in index_by_proxy
        ]
        self.state.proxy_checked_at = time.time()
        self.save()

    def rotate_proxy(self) -> bool:
        """Взять следующий прокси из пула. False — если пул не задан."""
        if not self.pool.enabled:
            return False
        self.pool.rotate()
        self.consecutive_errors = 0
        self.last_errors.clear()
        return True

    def save(self) -> None:
        state_module.save(self.cfg.state_path, self.state)

    def sleep_interval(self) -> None:
        """Пауза между проверками с джиттером, чтобы не выглядеть роботом."""
        if isinstance(self.fetcher, BrowserFetcher):
            base, jitter = self.cfg.browser_interval_seconds, self.cfg.browser_jitter_seconds
        else:
            base, jitter = self.cfg.interval_seconds, self.cfg.jitter_seconds

        delay = base + random.uniform(0, jitter)
        log.debug("Спим %.1f сек", delay)
        self._interruptible_sleep(delay)

    @staticmethod
    def _interruptible_sleep(seconds: float) -> None:
        """Спит, но просыпается на SIGTERM — чтобы Render не убивал воркер силой."""
        deadline = time.monotonic() + seconds
        while not _stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))

    # ------------------------------------------------------------ уведомления

    def notify_slots(self, status: queue_parser.QueueStatus, new_dates: list[str]) -> None:
        if new_dates:
            dates = ", ".join(new_dates)
            text = (
                f"🟢 <b>Появились даты: {html_escape(dates)}</b>\n"
                f"Беги записываться: {self.cfg.url}"
            )
        else:
            text = (
                "🟢 <b>Появились свободные места!</b>\n"
                "Конкретные даты со страницы считать не удалось — "
                f"проверь сам: {self.cfg.url}"
            )
        self.notifier.send(text)

    def notify_closed(self) -> None:
        self.notifier.send("🔴 Свободные места закончились.", silent=True)

    def notify_outage(self, error: Exception) -> None:
        if isinstance(error, CaptchaError):
            text = (
                "⚠️ <b>Нужно вмешательство</b>\n"
                "Cloudflare показывает капчу/челлендж — автоматически это не решается.\n"
                f"Ошибка: {html_escape(str(error))}"
            )
        elif isinstance(error, BlockedError):
            text = (
                "⚠️ <b>Похоже, забанили</b>\n"
                "Сайт отдаёт блокировку. Скорее всего, дело в IP хостинга "
                "(см. README, раздел «Если Cloudflare блокирует»).\n"
                f"Ошибка: {html_escape(str(error))}"
            )
        else:
            text = (
                "⚠️ <b>Похоже, сайт лёг</b>\n"
                f"{self.cfg.error_threshold} ошибок подряд. "
                f"Последняя: {html_escape(str(error))}"
            )
        text += f"\n\nПауза {self.cfg.error_pause_minutes} мин, потом попробую снова."
        self.notifier.send(text)

    def notify_recovered(self) -> None:
        self.notifier.send("✅ Связь с сайтом восстановилась, продолжаю следить.", silent=True)

    def startup_message(self, status: queue_parser.QueueStatus | None) -> str:
        """Приветствие с текущим состоянием очереди.

        Отдельное сообщение «просто запустился» бесполезно, а вот «запустился,
        сейчас мест нет» сразу отвечает на вопрос, ради которого сюда смотрят.
        """
        if status is None:
            return (
                "🚀 <b>Монитор запущен</b>, но сайт сейчас не отвечает.\n"
                "Продолжаю пробовать."
            )
        if status.state == queue_parser.OPEN:
            dates = ", ".join(status.dates) if status.dates else "даты не распознаны"
            return f"🚀 <b>Монитор запущен.</b> Прямо сейчас места ЕСТЬ: {html_escape(dates)}"
        if status.state == queue_parser.CLOSED:
            return "🚀 <b>Монитор запущен.</b> Сейчас мест нет — слежу и сообщу, когда появятся."
        return (
            "🚀 <b>Монитор запущен</b>, но состояние очереди распознать не удалось. "
            "Слежу дальше."
        )

    def send_startup_notice(self, status: queue_parser.QueueStatus | None) -> None:
        """Отправить приветствие, если оно не превратится в спам.

        Приветствие полезно на машине, которая засыпает: после пробуждения
        видно, что слежка возобновилась. Но при перезапусках подряд — а на
        хостинге они идут пачками, если процессу не хватает памяти — те же
        сообщения превращаются в поток. Поэтому чаще раза в
        STARTUP_NOTICE_COOLDOWN_MINUTES приветствие не отправляется.
        """
        since = time.time() - self.state.last_startup_notified
        cooldown = self.cfg.startup_notice_cooldown_minutes * 60

        if self.state.last_startup_notified and since < cooldown:
            log.info(
                "Приветствие пропущено: прошлое было %.0f мин назад "
                "(частые перезапуски — не повод спамить)",
                since / 60,
            )
            return

        if not self.notifier.send(self.startup_message(status), silent=True):
            log.error(
                "!!! УВЕДОМЛЕНИЯ НЕ ДОХОДЯТ. Монитор продолжит следить за сайтом, "
                "но сообщить о датах не сможет, пока это не исправлено. !!!"
            )
            return

        log.info("Самопроверка Telegram пройдена — уведомления доходят")
        self.state.last_startup_notified = time.time()
        self.save()

    def maybe_heartbeat(self) -> None:
        """Раз в сутки в HEARTBEAT_HOUR сообщить, что монитор жив."""
        now = self.now()
        today = now.date().isoformat()

        if now.hour < self.cfg.heartbeat_hour or self.state.last_heartbeat_day == today:
            return

        backend = "браузер" if isinstance(self.fetcher, BrowserFetcher) else "прямой запрос"
        self.notifier.send(
            f"✅ Монитор жив. За сутки проверок: {self.state.checks}, "
            f"ошибок: {self.state.errors}.\n"
            f"Режим: {backend}. Текущее состояние очереди: {self.state.queue_state}.",
            silent=True,
        )
        self.state.last_heartbeat_day = today
        self.state.reset_counters(today)
        self.save()

    # ------------------------------------------------------------- одна итерация

    def handle_status(self, status: queue_parser.QueueStatus) -> None:
        """Сравнить свежее состояние с прошлым и уведомить, если нужно."""
        previous = self.state.queue_state

        if status.state == queue_parser.UNKNOWN:
            self.state.unknown_streak += 1
            log.warning(
                "Состояние не распознано (%s), подряд: %d",
                status.note,
                self.state.unknown_streak,
            )
            # Разметку могли поменять — предупредим один раз, примерно через час.
            if self.state.unknown_streak >= 60 and not self.state.unknown_notified:
                self.notifier.send(
                    "⚠️ Страница открывается, но состояние очереди распознать не получается — "
                    "возможно, изменилась вёрстка сайта. Стоит проверить парсер.\n"
                    f"{self.cfg.url}"
                )
                self.state.unknown_notified = True
            self.save()
            return

        self.state.unknown_streak = 0
        self.state.unknown_notified = False

        if status.state == queue_parser.OPEN:
            new_dates = [d for d in status.dates if d not in self.state.notified_dates]

            # Уведомляем, если появились новые даты либо очередь только что открылась.
            if new_dates or previous != queue_parser.OPEN:
                log.info("ЕСТЬ МЕСТА: %s (новые: %s)", status.dates, new_dates)
                self.notify_slots(status, new_dates)
                self.state.notified_dates = list(
                    dict.fromkeys(self.state.notified_dates + status.dates)
                )
            else:
                log.info("Места есть, но даты те же (%s) — не спамим", status.dates)
        else:
            if previous == queue_parser.OPEN:
                log.info("Места закончились")
                if self.cfg.notify_on_close:
                    self.notify_closed()
            # Забываем прошлые даты, чтобы их повторное появление снова стало новостью.
            self.state.notified_dates = []
            log.info("Мест нет (%s)", status.note)

        self.state.queue_state = status.state
        self.save()

    def maybe_switch_to_browser(self) -> None:
        """В режиме auto: после серии блокировок попробовать Playwright."""
        if self.cfg.fetch_mode != "auto" or self.switched_to_browser:
            return
        if isinstance(self.fetcher, BrowserFetcher):
            return
        if not all(isinstance(e, BlockedError) for e in self.last_errors):
            return

        if not importlib.util.find_spec("playwright"):
            log.warning(
                "Прямые запросы блокируются, но Playwright не установлен — "
                "переключиться на браузер не могу"
            )
            self.switched_to_browser = True  # больше не пробуем
            return

        log.warning("Прямые запросы блокируются — переключаюсь на Playwright")
        self.fetcher.close()
        self.fetcher = BrowserFetcher(self.cfg)
        self.switched_to_browser = True
        self.notifier.send(
            "ℹ️ Прямые запросы блокируются, переключился на headless-браузер.",
            silent=True,
        )

    def check_once(self, html: str | None = None) -> queue_parser.QueueStatus | None:
        """Одна проверка. Возвращает состояние либо None, если была ошибка."""
        today = self.now().date().isoformat()
        if self.state.counters_day != today:
            self.state.reset_counters(today)

        try:
            page = html if html is not None else self.fetcher.fetch()
        except FetchError as exc:
            self.consecutive_errors += 1
            self.state.errors += 1
            self.last_errors.append(exc)
            self.last_errors = self.last_errors[-self.cfg.error_threshold :]
            log.error("Ошибка проверки (%d подряд): %s", self.consecutive_errors, exc)
            self.save()
            return None

        self.state.checks += 1

        if self.consecutive_errors:
            log.info("Связь восстановилась после %d ошибок", self.consecutive_errors)
            if self.state.outage_notified:
                self.notify_recovered()
                self.state.outage_notified = False
        self.consecutive_errors = 0
        self.last_errors.clear()

        status = queue_parser.parse(page)
        log.info("Состояние очереди: %s", status)
        return status

    # ------------------------------------------------------------------ цикл

    def run(self) -> None:
        backend = "браузер" if isinstance(self.fetcher, BrowserFetcher) else "прямой запрос"
        log.info(
            "Старт. URL=%s, режим=%s (%s), интервал=%d±%d сек",
            self.cfg.url,
            self.cfg.fetch_mode,
            backend,
            self.cfg.interval_seconds,
            self.cfg.jitter_seconds,
        )

        sysinfo.log_memory("старт")
        self.precheck_proxies()
        sysinfo.log_memory("после проверки прокси")

        # Первая проверка идёт до приветственного сообщения, чтобы отправить
        # одно письмо вместо двух и сразу сказать, как дела на сайте. Это важно
        # для машины, которая засыпает: после каждого пробуждения видно, что
        # слежка возобновилась и что происходит с очередью прямо сейчас.
        first = self.check_once()
        sysinfo.log_memory("после первой проверки")
        if first is not None:
            self.handle_status(first)

        self.send_startup_notice(first)

        self.maybe_heartbeat()
        self.sleep_interval()

        while not _stop:
            status = self.check_once()

            if status is not None:
                self.handle_status(status)
                self.pool.mark_good()
                self.rotations_without_success = 0
            else:
                # Пока в пуле остались непроверенные прокси — меняем их
                # без долгой паузы: скорее всего, дело в конкретном адресе.
                if (
                    self.pool.enabled
                    and self.consecutive_errors >= self.cfg.proxy_rotate_after
                    and self.rotations_without_success < len(self.pool)
                ):
                    self.rotate_proxy()
                    self.rotations_without_success += 1
                    self.sleep_interval()
                    continue

                pool_exhausted = (
                    self.pool.enabled and self.rotations_without_success >= len(self.pool)
                )
                if self.consecutive_errors >= self.cfg.error_threshold or pool_exhausted:
                    if not self.state.outage_notified:
                        self.notify_outage(self.last_errors[-1])
                        self.state.outage_notified = True
                        self.save()
                    self.maybe_switch_to_browser()
                    log.warning("Пауза %d минут", self.cfg.error_pause_minutes)
                    self._interruptible_sleep(self.cfg.error_pause_minutes * 60)
                    self.consecutive_errors = 0
                    self.rotations_without_success = 0
                    continue

            self.maybe_heartbeat()
            self.sleep_interval()

        log.info("Остановлено")
        self.fetcher.close()


# ----------------------------------------------------------------------- CLI


def cmd_chat_id(cfg: Config) -> int:
    if not cfg.telegram_token:
        print("Задай TELEGRAM_BOT_TOKEN.")
        return 1

    notifier = Notifier(cfg.telegram_token, "")
    data = notifier.get_updates()
    results = data.get("result", [])

    if not results:
        print(
            "Апдейтов нет. Открой чат с ботом в Telegram, отправь ему /start "
            "и запусти команду ещё раз."
        )
        return 1

    seen = {}
    for update in results:
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is not None:
            seen[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")

    for chat_id, title in seen.items():
        print(f"TELEGRAM_CHAT_ID={chat_id}   ({title})")
    return 0


def cmd_probe(cfg: Config) -> int:
    """Проверка «пускает ли сайт отсюда».

    Способ проверки берётся из FETCH_MODE: прямой HTTP-клиент челлендж пройти
    не может в принципе, поэтому для браузерного режима проверять надо тоже
    браузером — иначе получим ложную тревогу там, где всё работает.
    """
    from fetcher import probe, probe_browser

    browser_mode = cfg.fetch_mode in {"browser", "auto"}
    result = probe_browser(cfg) if browser_mode else probe(cfg)

    print(f"URL:      {cfg.url}")
    print(f"Проверка: {result.get('backend', 'прямой запрос')}")
    print(f"Прокси:   {result['proxy']}")
    if "status" in result:
        print(f"HTTP:     {result.get('status', '—')}")
        print(f"cf-ray:   {result.get('cf_ray', '—')}")
    print(f"Вердикт:  {result['verdict']}")
    if result.get("detail"):
        print(f"Детали:   {result['detail']}")

    if result["verdict"].startswith("OK"):
        print("\nЭтот IP годится — монитор отсюда работать будет.")
        return 0

    if not browser_mode and result["verdict"] == "ЧЕЛЛЕНДЖ/КАПЧА":
        print(
            "\nСайт показывает проверку Cloudflare. Это не бан: её проходит\n"
            "настоящий браузер. Поставь FETCH_MODE=browser и проверь снова."
        )
        return 1

    print(
        "\nОтсюда монитор работать не сможет. Попробуй другой IP или задай "
        "SCRAPE_PROXY. См. README, раздел «Если Cloudflare блокирует»."
    )
    return 1


def cmd_probe_list(cfg: Config) -> int:
    """Проверить весь список прокси и напечатать рабочие."""
    proxies = load_proxies(cfg.proxy_list, cfg.proxy_list_path)
    if not proxies:
        print(
            "Список прокси пуст. Задай PROXY_LIST (строки через перевод строки) "
            "или PROXY_LIST_PATH (путь к файлу)."
        )
        return 1

    print(f"Проверяю {len(proxies)} прокси...\n")
    working = check_proxies(cfg, proxies)

    print(f"\nРабочих: {len(working)} из {len(proxies)}")
    if not working:
        print("Ни один прокси не пропускает к сайту.")
        return 1

    print("\nГодные адреса (можно вставить в PROXY_LIST):")
    for proxy in working:
        print(f"  {proxy}")
    return 0


def cmd_simulate(cfg: Config, path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    status = queue_parser.parse(html)
    print(f"Файл:      {path}")
    print(f"Состояние: {status.state}")
    print(f"Даты:      {status.dates or '—'}")
    print(f"Заметка:   {status.note}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Монитор очереди паспортного сервиса")
    ap.add_argument("--once", action="store_true", help="одна проверка и выход")
    ap.add_argument("--test-telegram", action="store_true", help="отправить тестовое сообщение")
    ap.add_argument("--chat-id", action="store_true", help="показать свой chat_id")
    ap.add_argument("--simulate", metavar="FILE", help="разобрать HTML из файла")
    ap.add_argument(
        "--probe",
        action="store_true",
        help="проверить, пускает ли сайт с этого IP (учитывает SCRAPE_PROXY)",
    )
    ap.add_argument(
        "--probe-list",
        action="store_true",
        help="проверить весь список прокси и показать рабочие",
    )
    ap.add_argument(
        "--simulate-open",
        action="store_true",
        help="полный цикл на подменённом ответе «даты есть» (проверка уведомления)",
    )
    args = ap.parse_args()

    cfg = Config.from_env()
    setup_logging(cfg.log_level, cfg.log_file)

    if args.simulate:
        return cmd_simulate(cfg, args.simulate)

    if args.probe:
        return cmd_probe(cfg)

    if args.probe_list:
        return cmd_probe_list(cfg)

    if args.chat_id:
        return cmd_chat_id(cfg)

    cfg.require_telegram()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id, source=cfg.instance_name)
    log.info("Подпись сообщений: %s", notifier.source)

    if args.test_telegram:
        ok = notifier.send(
            "🔔 Тестовое сообщение от монитора очереди.\n"
            "Если ты это видишь — уведомления настроены правильно."
        )
        return 0 if ok else 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    monitor = Monitor(cfg, notifier)

    if args.simulate_open:
        # Критерий готовности №3: подменяем ответ сайта и проверяем уведомление.
        import os.path

        fixture = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "open.html")
        with open(fixture, encoding="utf-8") as fh:
            html = fh.read()
        status = monitor.check_once(html=html)
        log.info("Симуляция: распознано %s", status)
        if status is not None:
            monitor.handle_status(status)
        return 0

    if args.once:
        status = monitor.check_once()
        if status is None:
            log.error("Проверка не удалась")
            monitor.fetcher.close()
            return 1
        monitor.handle_status(status)
        monitor.fetcher.close()
        return 0

    monitor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
