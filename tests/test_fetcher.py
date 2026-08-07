"""Тесты загрузчика страницы.

Отдельное внимание — порядку действий при запуске браузера. Прод уже ловил
ошибку, где дисплей поднимался ПОСЛЕ старта драйвера Playwright: драйвер
наследует окружение в момент запуска, поэтому Chromium не видел DISPLAY и
падал с «without having a XServer running». Откат на headless это скрывал, и
headful-режим не запускался ни разу.

Запуск: python -m pytest tests/ -v (или python tests/test_fetcher.py)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetcher  # noqa: E402
from config import Config  # noqa: E402


class _FakeChromium:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal
        self.launch_kwargs: dict = {}

    def launch(self, **kwargs):
        self.journal.append("launch")
        self.launch_kwargs = kwargs
        raise RuntimeError("дальше не идём, нас интересует только порядок вызовов")


class _FakePlaywright:
    def __init__(self, journal: list[str]) -> None:
        self.chromium = _FakeChromium(journal)


class _FakeStarter:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal

    def start(self):
        self.journal.append("playwright.start")
        return _FakePlaywright(self.journal)


def _run_ensure_page(headless: bool, display_ok: bool = True):
    """Прогнать _ensure_page с заглушками, вернуть журнал вызовов и kwargs."""
    import playwright.sync_api as pw_api

    journal: list[str] = []
    holder: dict = {}

    real_sync_playwright = pw_api.sync_playwright
    real_ensure = None

    import display as display_module

    real_ensure = display_module.ensure

    def fake_ensure(*args, **kwargs):
        journal.append("display.ensure")
        if display_ok:
            os.environ["DISPLAY"] = ":99"
        return display_ok

    def fake_sync_playwright():
        starter = _FakeStarter(journal)
        holder["starter"] = starter
        return starter

    pw_api.sync_playwright = fake_sync_playwright
    display_module.ensure = fake_ensure
    saved_display = os.environ.pop("DISPLAY", None)

    try:
        cfg = Config(browser_headless=headless)
        f = fetcher.BrowserFetcher(cfg)
        try:
            f._ensure_page()
        except RuntimeError:
            pass  # ожидаемо: заглушка launch прерывает выполнение
        return journal, f._pw.chromium.launch_kwargs if f._pw else {}
    finally:
        pw_api.sync_playwright = real_sync_playwright
        display_module.ensure = real_ensure
        os.environ.pop("DISPLAY", None)
        if saved_display is not None:
            os.environ["DISPLAY"] = saved_display


def test_display_is_ready_before_playwright_starts():
    """Главная регрессия: дисплей обязан подняться раньше драйвера Playwright."""
    journal, _ = _run_ensure_page(headless=False)

    assert "display.ensure" in journal, "дисплей вообще не поднимали"
    assert journal.index("display.ensure") < journal.index("playwright.start"), (
        f"неверный порядок: {journal}. Драйвер наследует окружение при старте, "
        "поэтому DISPLAY должен быть выставлен до него"
    )


def test_headless_mode_does_not_touch_display():
    journal, _ = _run_ensure_page(headless=True)
    assert "display.ensure" not in journal


def test_environment_is_passed_to_browser_explicitly():
    """DISPLAY передаём явно, а не надеемся на наследование драйвером."""
    _, kwargs = _run_ensure_page(headless=False)
    assert "env" in kwargs, "окружение должно передаваться в launch явно"
    assert kwargs["env"].get("DISPLAY") == ":99"


def test_falls_back_to_headless_without_display():
    """Без дисплея работаем headless, а не падаем."""
    journal, kwargs = _run_ensure_page(headless=False, display_ok=False)
    assert "display.ensure" in journal
    assert kwargs["headless"] is True


def test_challenge_page_from_production_is_recognised():
    """Настоящий текст челленджа, снятый с боевых логов."""
    html = (
        "<html><head><title>Трохи зачекайте…</title></head><body>"
        "Триває перевірка безпеки. Ця сторінка відображається, поки вебсайт "
        "перевіряє, що ви не бот. Ray ID: a272312f595caee1"
        "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script>"
        "</body></html>"
    )
    assert fetcher.looks_like_challenge(html) is True


def test_real_page_is_not_mistaken_for_challenge():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "closed.html")
    with open(path, encoding="utf-8") as fh:
        assert fetcher.looks_like_challenge(fh.read()) is False


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
