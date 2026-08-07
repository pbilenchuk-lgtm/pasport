"""Тесты логики уведомлений: переходы состояний, дедупликация, обработка ошибок.

Сеть и Telegram подменяются заглушками, реальных запросов не делается.
Запуск: python -m pytest tests/ -v (или python tests/test_monitor.py)
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor as monitor_module  # noqa: E402
import parser as queue_parser  # noqa: E402
from config import Config  # noqa: E402
from fetcher import BlockedError, FetchError  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


class FakeNotifier:
    """Собирает сообщения вместо отправки в Telegram."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.last_error = ""

    def send(self, text: str, silent: bool = False) -> bool:
        self.messages.append(text)
        return True


class FakeFetcher:
    """Отдаёт заранее заданную последовательность ответов или ошибок."""

    name = "fake"

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0

    def fetch(self) -> str:
        self.calls += 1
        item = self.script.pop(0) if self.script else ""
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        pass


def _monitor(script: list) -> tuple[monitor_module.Monitor, FakeNotifier]:
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    os.unlink(path)

    cfg = Config(telegram_token="x", telegram_chat_id="1", state_path=path)
    notifier = FakeNotifier()
    mon = monitor_module.Monitor(cfg, notifier)
    mon.fetcher = FakeFetcher(script)
    return mon, notifier


def _step(mon: monitor_module.Monitor) -> None:
    status = mon.check_once()
    if status is not None:
        mon.handle_status(status)


def test_closed_page_produces_no_messages():
    mon, notifier = _monitor([_fixture("closed.html")])
    _step(mon)
    assert mon.state.queue_state == queue_parser.CLOSED
    assert notifier.messages == []


def test_dates_appearing_sends_one_message():
    mon, notifier = _monitor([_fixture("closed.html"), _fixture("open.html")])
    _step(mon)
    _step(mon)
    assert len(notifier.messages) == 1
    assert "12.08, 13.08, 14.08" in notifier.messages[0]
    assert "🟢" in notifier.messages[0]


def test_same_dates_are_not_repeated():
    """Главное требование ТЗ: не спамить одним и тем же каждые 45 секунд."""
    mon, notifier = _monitor([_fixture("open.html")] * 5)
    for _ in range(5):
        _step(mon)
    assert len(notifier.messages) == 1


def test_dates_returning_after_close_notify_again():
    """Места пропали и появились снова — это снова новость."""
    pages = [_fixture("open.html"), _fixture("closed.html"), _fixture("open.html")]
    mon, notifier = _monitor(pages)
    for _ in pages:
        _step(mon)
    assert len(notifier.messages) == 2


def test_unknown_page_never_reports_slots():
    """Непонятная страница не должна выглядеть как «даты появились»."""
    mon, notifier = _monitor(["<html><body>" + "z" * 900 + "</body></html>"])
    _step(mon)
    assert mon.state.queue_state != queue_parser.OPEN
    assert notifier.messages == []


def test_outage_alert_after_threshold_and_only_once():
    mon, notifier = _monitor([FetchError("timeout")] * 12)

    for _ in range(12):
        status = mon.check_once()
        assert status is None
        if mon.consecutive_errors >= mon.cfg.error_threshold and not mon.state.outage_notified:
            mon.notify_outage(mon.last_errors[-1])
            mon.state.outage_notified = True

    assert len(notifier.messages) == 1
    assert "⚠️" in notifier.messages[0]
    assert mon.state.errors == 12


def test_blocked_error_mentions_ip_ban():
    mon, notifier = _monitor([])
    mon.notify_outage(BlockedError("HTTP 403: Blocked for security reasons"))
    assert "забанили" in notifier.messages[0]
    assert "IP" in notifier.messages[0]


def test_recovery_message_after_outage():
    mon, notifier = _monitor([FetchError("boom")] * 5 + [_fixture("closed.html")])
    for _ in range(5):
        mon.check_once()
    mon.state.outage_notified = True
    _step(mon)
    assert any("восстановилась" in m for m in notifier.messages)
    assert mon.consecutive_errors == 0


def test_heartbeat_fires_once_per_day():
    mon, notifier = _monitor([_fixture("closed.html")])
    _step(mon)
    mon.cfg.heartbeat_hour = 0  # чтобы условие «час настал» выполнялось всегда

    mon.maybe_heartbeat()
    mon.maybe_heartbeat()
    mon.maybe_heartbeat()

    heartbeats = [m for m in notifier.messages if "Монитор жив" in m]
    assert len(heartbeats) == 1
    assert mon.state.checks == 0  # счётчики обнулились после отправки


def test_state_survives_restart():
    """После рестарта монитор помнит, о чём уже уведомлял."""
    mon, notifier = _monitor([_fixture("open.html")])
    _step(mon)
    assert len(notifier.messages) == 1

    restarted, notifier2 = _monitor([_fixture("open.html")])
    restarted.cfg.state_path = mon.cfg.state_path
    restarted.state = monitor_module.state_module.load(mon.cfg.state_path)
    _step(restarted)
    assert notifier2.messages == []


def test_telegram_errors_get_actionable_hints():
    """Самая частая ошибка — бот не может написать первым. Подсказка обязана быть."""
    import notifier as notifier_module

    assert "/start" in notifier_module.explain("Bad Request: chat not found")
    assert "/start" in notifier_module.explain(
        "Forbidden: bot can't initiate conversation with a user"
    )
    assert "/start" in notifier_module.explain("Forbidden: bot was blocked by the user")
    assert "BotFather" in notifier_module.explain("Unauthorized")
    assert notifier_module.explain("что-то совсем неизвестное") == ""


def test_repeated_restarts_do_not_spam_startup_messages():
    """Перезапуски идут пачками — одинаковые приветствия читать невозможно."""
    closed = queue_parser.parse(_fixture("closed.html"))

    mon, notifier = _monitor([])
    mon.send_startup_notice(closed)
    assert len(notifier.messages) == 1

    # Пять перезапусков подряд с общим файлом состояния.
    for _ in range(5):
        restarted, again = _monitor([])
        restarted.cfg.state_path = mon.cfg.state_path
        restarted.state = monitor_module.state_module.load(mon.cfg.state_path)
        restarted.send_startup_notice(closed)
        assert again.messages == [], "приветствие должно быть подавлено"


def test_startup_message_returns_after_cooldown():
    closed = queue_parser.parse(_fixture("closed.html"))

    mon, notifier = _monitor([])
    mon.send_startup_notice(closed)
    assert len(notifier.messages) == 1

    # Прошло больше часа — снова уместно.
    mon.state.last_startup_notified -= mon.cfg.startup_notice_cooldown_minutes * 60 + 1
    mon.send_startup_notice(closed)
    assert len(notifier.messages) == 2


def test_dates_still_notify_during_startup_cooldown():
    """Подавление приветствий не должно глушить главное — сообщение о датах."""
    mon, notifier = _monitor([_fixture("open.html")])
    mon.state.last_startup_notified = time.time()

    status = mon.check_once()
    mon.handle_status(status)
    mon.send_startup_notice(status)

    assert any("Появились даты" in m for m in notifier.messages)
    assert not any("Монитор запущен" in m for m in notifier.messages)


def test_startup_message_reports_current_state():
    """После каждого пробуждения машины видно, что происходит с очередью."""
    mon, _ = _monitor([])

    closed = queue_parser.parse(_fixture("closed.html"))
    assert "мест нет" in mon.startup_message(closed)

    opened = queue_parser.parse(_fixture("open.html"))
    message = mon.startup_message(opened)
    assert "ЕСТЬ" in message and "12.08" in message

    assert "не отвечает" in mon.startup_message(None)


def test_env_file_does_not_override_real_environment():
    """Переменные Render и systemd должны быть главнее файла .env."""
    import config as config_module

    handle, path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write("# комментарий\n")
        fh.write("PASPORT_TEST_NEW=из_файла\n")
        fh.write('PASPORT_TEST_EXISTING="из_файла"\n')
        fh.write("мусор без равно\n")

    os.environ["PASPORT_TEST_EXISTING"] = "из_окружения"
    os.environ.pop("PASPORT_TEST_NEW", None)
    try:
        applied = config_module.load_env_file(path)
        assert applied == 1
        assert os.environ["PASPORT_TEST_NEW"] == "из_файла"
        assert os.environ["PASPORT_TEST_EXISTING"] == "из_окружения"
    finally:
        os.unlink(path)
        os.environ.pop("PASPORT_TEST_NEW", None)
        os.environ.pop("PASPORT_TEST_EXISTING", None)


def test_missing_env_file_is_not_an_error():
    import config as config_module

    assert config_module.load_env_file("/nonexistent/.env") == 0


def _patch_check_proxies(verdicts):
    """Подменить check_proxies, вернув функцию восстановления.

    Атрибут именно подменяется, а не создаётся: если импорт в monitor.py
    пропадёт, тест упадёт на AttributeError, а не замаскирует ошибку.
    """
    original = monitor_module.check_proxies
    monitor_module.check_proxies = lambda cfg, proxies, **kw: [
        (proxy, verdicts[proxy]) for proxy in proxies
    ]

    def restore() -> None:
        monitor_module.check_proxies = original

    return restore


def test_precheck_keeps_only_working_proxies():
    mon, _ = _monitor([])
    mon.pool = monitor_module.ProxyPool(["http://a:1", "http://b:2"])
    mon.cfg.proxy_precheck = True
    mon.cfg.fetch_mode = "direct"

    restore = _patch_check_proxies(
        {"http://a:1": monitor_module.VERDICT_OK, "http://b:2": "IP ЗАБЛОКИРОВАН"}
    )
    try:
        mon.precheck_proxies()
    finally:
        restore()

    assert len(mon.pool) == 1
    assert mon.pool.current() == "http://a:1"


def test_challenge_proxies_are_kept_for_browser_but_not_for_direct():
    """Челлендж проходит браузер, поэтому такие прокси годятся только ему.

    Именно этот случай встретился в проде: жёстко забанены не все прокси,
    часть отдаёт решаемый челлендж.
    """
    verdicts = {
        "http://a:1": monitor_module.VERDICT_CHALLENGE,
        "http://b:2": "IP ЗАБЛОКИРОВАН",
    }

    mon, notifier = _monitor([])
    mon.pool = monitor_module.ProxyPool(list(verdicts))
    mon.cfg.proxy_precheck = True
    mon.cfg.fetch_mode = "browser"
    restore = _patch_check_proxies(verdicts)
    try:
        mon.precheck_proxies()
    finally:
        restore()
    assert mon.pool.proxies == ["http://a:1"], "браузер должен взять челлендж-прокси"

    mon2, notifier2 = _monitor([])
    mon2.pool = monitor_module.ProxyPool(list(verdicts))
    mon2.cfg.proxy_precheck = True
    mon2.cfg.fetch_mode = "direct"
    restore = _patch_check_proxies(verdicts)
    try:
        mon2.precheck_proxies()
    finally:
        restore()
    assert len(mon2.pool) == 2, "пул не должен меняться, если годных нет"
    assert any("FETCH_MODE=browser" in m for m in notifier2.messages), (
        "нужно подсказать, что челлендж решается браузером"
    )


def test_clean_proxies_come_before_challenged_ones():
    from fetcher import VERDICT_CHALLENGE, VERDICT_OK, usable_proxies

    results = [
        ("http://challenged:1", VERDICT_CHALLENGE),
        ("http://clean:2", VERDICT_OK),
        ("http://banned:3", "IP ЗАБЛОКИРОВАН"),
    ]
    assert usable_proxies(results, browser_mode=True) == [
        "http://clean:2",
        "http://challenged:1",
    ]
    assert usable_proxies(results, browser_mode=False) == ["http://clean:2"]


def test_proxy_check_result_is_reused_after_restart():
    """Перезапуск не должен заново гонять весь список: это полторы минуты."""
    verdicts = {
        "http://a:1": monitor_module.VERDICT_OK,
        "http://b:2": "IP ЗАБЛОКИРОВАН",
        "http://c:3": monitor_module.VERDICT_OK,
    }

    mon, _ = _monitor([])
    mon.pool = monitor_module.ProxyPool(list(verdicts))
    mon.cfg.proxy_precheck = True
    mon.cfg.fetch_mode = "direct"

    restore = _patch_check_proxies(verdicts)
    try:
        mon.precheck_proxies()
    finally:
        restore()
    assert mon.pool.proxies == ["http://a:1", "http://c:3"]

    # Перезапуск: то же состояние, но проверка не должна выполняться вовсе.
    restarted, _ = _monitor([])
    restarted.cfg.state_path = mon.cfg.state_path
    restarted.state = monitor_module.state_module.load(mon.cfg.state_path)
    restarted.pool = monitor_module.ProxyPool(list(verdicts))
    restarted.cfg.proxy_precheck = True

    def explode(*args, **kwargs):
        raise AssertionError("проверка не должна была запускаться — есть свежий кэш")

    original = monitor_module.check_proxies
    monitor_module.check_proxies = explode
    try:
        restarted.precheck_proxies()
    finally:
        monitor_module.check_proxies = original

    assert restarted.pool.proxies == ["http://a:1", "http://c:3"]


def test_changed_proxy_list_invalidates_cache():
    """Подменили список — старый результат больше не годится."""
    verdicts = {"http://a:1": monitor_module.VERDICT_OK, "http://b:2": "IP ЗАБЛОКИРОВАН"}

    mon, _ = _monitor([])
    mon.pool = monitor_module.ProxyPool(list(verdicts))
    mon.cfg.proxy_precheck = True
    mon.cfg.fetch_mode = "direct"
    restore = _patch_check_proxies(verdicts)
    try:
        mon.precheck_proxies()
    finally:
        restore()

    other, _ = _monitor([])
    other.cfg.state_path = mon.cfg.state_path
    other.state = monitor_module.state_module.load(mon.cfg.state_path)
    other.pool = monitor_module.ProxyPool(["http://x:9", "http://y:8"])
    assert other.use_cached_proxies() is False


def test_stale_proxy_cache_is_rechecked():
    verdicts = {"http://a:1": monitor_module.VERDICT_OK}

    mon, _ = _monitor([])
    mon.pool = monitor_module.ProxyPool(list(verdicts))
    mon.cfg.proxy_precheck = True
    mon.cfg.fetch_mode = "direct"
    restore = _patch_check_proxies(verdicts)
    try:
        mon.precheck_proxies()
    finally:
        restore()

    # Отматываем время проверки на сутки назад.
    mon.state.proxy_checked_at -= 24 * 3600
    assert mon.use_cached_proxies() is False


def test_proxy_cache_stores_indices_not_credentials():
    """В файле состояния не должно быть логинов и паролей от прокси."""
    proxies = ["http://user:secret@1.2.3.4:8000"]
    verdicts = {proxies[0]: monitor_module.VERDICT_OK}

    mon, _ = _monitor([])
    mon.pool = monitor_module.ProxyPool(proxies)
    mon.cfg.proxy_precheck = True
    mon.cfg.fetch_mode = "direct"
    restore = _patch_check_proxies(verdicts)
    try:
        mon.precheck_proxies()
    finally:
        restore()

    with open(mon.cfg.state_path, encoding="utf-8") as fh:
        saved = fh.read()
    assert "secret" not in saved
    assert "1.2.3.4" not in saved
    assert mon.state.proxy_usable_indices == [0]


def test_precheck_warns_when_no_proxy_works():
    mon, notifier = _monitor([])
    mon.pool = monitor_module.ProxyPool(["http://a:1"])
    mon.cfg.proxy_precheck = True
    mon.cfg.fetch_mode = "direct"

    restore = _patch_check_proxies({"http://a:1": "IP ЗАБЛОКИРОВАН"})
    try:
        mon.precheck_proxies()
    finally:
        restore()

    assert any("Ни один" in m for m in notifier.messages)


def test_precheck_skipped_without_proxies():
    """Без списка прокси стартовая проверка не должна ничего делать."""
    mon, notifier = _monitor([])
    mon.precheck_proxies()
    assert notifier.messages == []


def test_no_undefined_names_anywhere():
    """Статический анализ всего проекта.

    Продакшен уже падал с NameError: функция вызывалась, но импорт стоял
    только внутри другой ветки. Обычные тесты такое пропускают, если ветка
    не выполняется, — поэтому проверяем исходники целиком.
    """
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = [
        os.path.join(root, name)
        for name in sorted(os.listdir(root))
        if name.endswith(".py")
    ]
    files += [os.path.join(FIXTURES, "..", f) for f in ("test_monitor.py",)]

    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", *files],
        capture_output=True,
        text=True,
    )

    if "No module named" in result.stderr:
        print("    (pyflakes не установлен, проверка пропущена)")
        return

    assert not result.stdout.strip(), f"pyflakes нашёл проблемы:\n{result.stdout}"


def test_unknown_timezone_falls_back_to_utc():
    """Отсутствие базы часовых поясов роняло весь сервис в Docker-образе.

    Сердцебиение — вспомогательная функция, из-за неё монитор падать не должен.
    """
    tz = monitor_module.resolve_timezone("Europe/Nonexistent")
    assert tz is not None

    from datetime import datetime, timezone as dt_timezone

    assert datetime.now(dt_timezone.utc).astimezone(tz) is not None


def test_known_timezone_still_resolves():
    tz = monitor_module.resolve_timezone("Europe/Warsaw")
    assert str(tz) in {"Europe/Warsaw", "UTC"}


def test_monitor_starts_with_broken_timezone():
    """Монитор должен создаваться даже при неверном HEARTBEAT_TIMEZONE."""
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    os.unlink(path)

    cfg = Config(
        telegram_token="x",
        telegram_chat_id="1",
        state_path=path,
        heartbeat_timezone="Не/Существует",
    )
    mon = monitor_module.Monitor(cfg, FakeNotifier())
    assert mon.now() is not None


def test_interval_floor_is_enforced():
    """Ниже 30 секунд опускаться нельзя, даже если попросили."""
    os.environ["INTERVAL_SECONDS"] = "5"
    try:
        cfg = Config.from_env()
        assert cfg.interval_seconds == 30
    finally:
        del os.environ["INTERVAL_SECONDS"]


if __name__ == "__main__":
    import logging

    logging.disable(logging.CRITICAL)

    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  OK   {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
    print("\nПровалено тестов:", failures)
    sys.exit(1 if failures else 0)
