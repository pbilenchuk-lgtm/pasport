"""Тесты логики уведомлений: переходы состояний, дедупликация, обработка ошибок.

Сеть и Telegram подменяются заглушками, реальных запросов не делается.
Запуск: python -m pytest tests/ -v (или python tests/test_monitor.py)
"""

from __future__ import annotations

import os
import sys
import tempfile

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

    def check(self) -> bool:
        return self.send("🚀 Монитор очереди запущен", silent=True)


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


def test_startup_check_sends_a_message():
    mon, notifier = _monitor([])
    assert mon.notifier.check() is True
    assert "запущен" in notifier.messages[0]


def _patch_check_proxies(result):
    """Подменить check_proxies, вернув функцию восстановления.

    Атрибут именно подменяется, а не создаётся: если импорт в monitor.py
    пропадёт, тест упадёт на AttributeError, а не замаскирует ошибку.
    """
    original = monitor_module.check_proxies
    monitor_module.check_proxies = lambda cfg, proxies, **kw: result(proxies)

    def restore() -> None:
        monitor_module.check_proxies = original

    return restore


def test_precheck_keeps_only_working_proxies():
    mon, _ = _monitor([])
    mon.pool = monitor_module.ProxyPool(["http://a:1", "http://b:2"])
    mon.cfg.proxy_precheck = True

    restore = _patch_check_proxies(lambda proxies: proxies[:1])
    try:
        mon.precheck_proxies()
    finally:
        restore()

    assert len(mon.pool) == 1
    assert mon.pool.current() == "http://a:1"


def test_precheck_warns_when_no_proxy_works():
    mon, notifier = _monitor([])
    mon.pool = monitor_module.ProxyPool(["http://a:1"])
    mon.cfg.proxy_precheck = True

    restore = _patch_check_proxies(lambda proxies: [])
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
