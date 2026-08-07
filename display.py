"""Виртуальный X-дисплей для headful-браузера.

Cloudflare часто не пропускает headless-Chromium, поэтому браузер должен быть
настоящим, а значит ему нужен дисплей. В контейнере его даёт Xvfb.

Дисплей поднимается отсюда, а не из entrypoint-скрипта, по двум причинам:

* между стартом контейнера и запуском браузера проходит около минуты (проверка
  прокси), и за это время Xvfb может умереть — тогда браузер падает с
  «Missing X server or $DISPLAY». Здесь дисплей проверяется прямо перед
  запуском браузера и при необходимости поднимается заново;
* вывод Xvfb попадает в лог, а не в /dev/null, иначе причину сбоя не узнать.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import time

log = logging.getLogger(__name__)

_process: subprocess.Popen | None = None
_display: str | None = None

# Номера дисплеев, которые пробуем занять.
_CANDIDATES = (99, 98, 97, 96)


def _socket_path(number: int) -> str:
    return f"/tmp/.X11-unix/X{number}"


def is_alive() -> bool:
    """Дисплей поднят и процесс Xvfb ещё жив."""
    if _display is None:
        return False
    if _process is not None and _process.poll() is not None:
        return False
    return os.path.exists(_socket_path(int(_display.lstrip(":"))))


def stop() -> None:
    global _process, _display
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None
    _display = None


def ensure(width: int = 1280, height: int = 800) -> bool:
    """Гарантировать наличие рабочего X-дисплея.

    Возвращает True, если headful-браузер запускать можно.
    """
    global _process, _display

    # Дисплей уже дал кто-то снаружи (например, xvfb-run или рабочая машина).
    external = os.environ.get("DISPLAY")
    if external and _process is None:
        log.info("Использую уже заданный DISPLAY=%s", external)
        return True

    if is_alive():
        return True

    if _process is not None:
        code = _process.poll()
        log.warning("Xvfb умер (код %s), поднимаю заново", code)
        stop()

    if not shutil.which("Xvfb"):
        log.warning("Xvfb не найден — headful-браузер запустить нельзя")
        return False

    os.makedirs("/tmp/.X11-unix", exist_ok=True)

    for number in _CANDIDATES:
        # Чужой или осиротевший сокет — этот номер занят, пробуем следующий.
        lock = f"/tmp/.X{number}-lock"
        if os.path.exists(_socket_path(number)) or os.path.exists(lock):
            continue

        try:
            process = subprocess.Popen(
                [
                    "Xvfb",
                    f":{number}",
                    "-screen",
                    "0",
                    # 16 бит вместо 24: кадровый буфер вдвое меньше, а для
                    # прохождения челленджа глубина цвета роли не играет.
                    f"{width}x{height}x16",
                    "-nolisten",
                    "tcp",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log.warning("Не удалось запустить Xvfb: %s", exc)
            return False

        # Ждём появления сокета — Chromium стартует быстрее, чем поднимается X.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.warning(
                    "Xvfb на :%d завершился сразу (код %s), пробую другой номер",
                    number,
                    process.returncode,
                )
                break
            if os.path.exists(_socket_path(number)):
                _process = process
                _display = f":{number}"
                os.environ["DISPLAY"] = _display
                atexit.register(stop)
                log.info("Виртуальный дисплей %s запущен", _display)
                return True
            time.sleep(0.2)
        else:
            log.warning("Xvfb на :%d не поднялся за 10 сек", number)
            process.terminate()

    log.warning("Свободный номер дисплея найти не удалось")
    return False
