"""Хранение состояния между проверками (и между рестартами сервиса).

Файл нужен, чтобы не слать одно и то же уведомление каждые 45 секунд.
На Render у Background Worker диск эфемерный: после передеплоя файл пропадёт
и первое найденное состояние будет считаться новым. Для монитора это
безопасно — максимум придёт одно повторное уведомление.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)


@dataclass
class State:
    # Последнее распознанное состояние очереди: open/closed/unknown.
    queue_state: str = "unknown"
    # Даты, о которых пользователь уже уведомлён.
    notified_dates: list[str] = field(default_factory=list)

    # Счётчики для суточного «сердцебиения».
    counters_day: str = ""
    checks: int = 0
    errors: int = 0
    last_heartbeat_day: str = ""

    # Флаги, чтобы не спамить одинаковыми предупреждениями.
    outage_notified: bool = False
    unknown_streak: int = 0
    unknown_notified: bool = False

    def reset_counters(self, day: str) -> None:
        self.counters_day = day
        self.checks = 0
        self.errors = 0


def load(path: str) -> State:
    if not os.path.exists(path):
        log.info("Файл состояния %s не найден — начинаем с чистого листа", path)
        return State()

    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("Не удалось прочитать %s (%s) — начинаем заново", path, exc)
        return State()

    known = {f.name for f in State.__dataclass_fields__.values()}
    return State(**{k: v for k, v in raw.items() if k in known})


def save(path: str, state: State) -> None:
    """Атомарная запись: сначала во временный файл, потом переименование."""
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(state), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        # Потеря состояния не повод ронять монитор.
        log.error("Не удалось сохранить состояние в %s: %s", path, exc)
