"""Тесты подписи источника.

К одному боту может быть подключено несколько копий монитора — например,
домашний компьютер и сервер. Без подписи непонятно, кто прислал сообщение,
и какую копию чинить, если что-то пошло не так.

Запуск: python -m pytest tests/ -v (или python tests/test_notifier.py)
"""

from __future__ import annotations

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notifier as notifier_module  # noqa: E402

CLOUD_VARIABLES = (
    "RENDER",
    "RENDER_SERVICE_NAME",
    "DYNO",
    "KUBERNETES_SERVICE_HOST",
    "AWS_EXECUTION_ENV",
)


def _without_cloud_env():
    saved = {name: os.environ.pop(name, None) for name in CLOUD_VARIABLES}

    def restore() -> None:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    return restore


def test_local_run_is_signed_with_computer_name():
    restore = _without_cloud_env()
    try:
        source = notifier_module.detect_source()
    finally:
        restore()

    assert source.startswith("🖥")
    assert socket.gethostname() in source


def test_render_run_is_signed_as_cloud():
    restore = _without_cloud_env()
    os.environ["RENDER"] = "true"
    os.environ["RENDER_SERVICE_NAME"] = "passport-queue-monitor"
    try:
        source = notifier_module.detect_source()
    finally:
        restore()

    assert source.startswith("☁️")
    assert "passport-queue-monitor" in source


def test_cloud_without_service_name_still_marked():
    restore = _without_cloud_env()
    os.environ["KUBERNETES_SERVICE_HOST"] = "10.0.0.1"
    try:
        source = notifier_module.detect_source()
    finally:
        restore()

    assert source.startswith("☁️")


def test_signature_is_appended_on_its_own_line():
    notifier = notifier_module.Notifier("t", "1", source="🖥 PETRO-PC")
    signed = notifier.sign("🟢 Появились даты: 12.08")

    assert signed.startswith("🟢 Появились даты: 12.08")
    assert signed.endswith("<i>🖥 PETRO-PC</i>")
    assert "\n\n" in signed, "подпись должна стоять отдельной строкой"


def test_explicit_name_wins_over_detection():
    """Понятное имя из настроек важнее автоопределения."""
    notifier = notifier_module.Notifier("t", "1", source="домашний ноутбук")
    assert notifier.source == "домашний ноутбук"
    assert "домашний ноутбук" in notifier.sign("тест")


def test_empty_source_leaves_message_untouched():
    notifier = notifier_module.Notifier("t", "1")
    notifier.source = ""
    assert notifier.sign("текст") == "текст"


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
