"""Тесты виртуального дисплея.

Дисплей отвечает за headful-браузер, а тот — за прохождение челленджа
Cloudflare. Прод уже падал с «Missing X server or $DISPLAY», потому что Xvfb
поднимался в entrypoint-скрипте и не доживал до запуска браузера.

Запуск: python -m pytest tests/ -v (или python tests/test_display.py)
"""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import display  # noqa: E402

HAS_XVFB = shutil.which("Xvfb") is not None


def test_external_display_is_respected():
    """Если дисплей уже дали снаружи, свой запускать не нужно."""
    display.stop()
    saved = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = ":0"
    try:
        assert display.ensure() is True
        assert display._process is None, "не должны были запускать свой Xvfb"
    finally:
        if saved is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = saved


def test_missing_xvfb_reports_failure_instead_of_raising():
    """Без Xvfb нужен честный False, а не исключение: монитор перейдёт в headless."""
    display.stop()
    saved_display = os.environ.pop("DISPLAY", None)
    original_which = display.shutil.which
    display.shutil.which = lambda name: None
    try:
        assert display.ensure() is False
    finally:
        display.shutil.which = original_which
        if saved_display is not None:
            os.environ["DISPLAY"] = saved_display


def test_display_starts_and_reports_alive():
    if not HAS_XVFB:
        print("    (Xvfb не установлен, проверка пропущена)")
        return

    display.stop()
    os.environ.pop("DISPLAY", None)
    try:
        assert display.ensure() is True
        assert display.is_alive() is True
        assert os.environ["DISPLAY"].startswith(":")
    finally:
        display.stop()


def test_repeated_ensure_does_not_spawn_second_server():
    if not HAS_XVFB:
        print("    (Xvfb не установлен, проверка пропущена)")
        return

    display.stop()
    os.environ.pop("DISPLAY", None)
    try:
        display.ensure()
        first = display._process
        display.ensure()
        assert display._process is first, "второй Xvfb запускать не нужно"
    finally:
        display.stop()


def test_dead_display_is_restarted():
    """Главный сценарий: Xvfb умер между стартом и запуском браузера."""
    if not HAS_XVFB:
        print("    (Xvfb не установлен, проверка пропущена)")
        return

    display.stop()
    os.environ.pop("DISPLAY", None)
    try:
        display.ensure()
        display._process.kill()
        display._process.wait()
        time.sleep(0.3)

        assert display.is_alive() is False
        assert display.ensure() is True, "монитор обязан поднять дисплей заново"
        assert display.is_alive() is True
    finally:
        display.stop()


def test_stop_is_idempotent():
    display.stop()
    display.stop()
    assert display.is_alive() is False


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
