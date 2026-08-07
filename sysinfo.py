"""Замер памяти контейнера.

Нужен для диагностики: на Render монитор перезапускался посреди ожидания
челленджа без единого исключения в логах — так выглядит убийство процесса по
нехватке памяти. Без цифр это остаётся догадкой, поэтому расход печатается на
ключевых шагах.

Читаем cgroup: обычный /proc/meminfo показывает память хоста, а не лимит
контейнера, и на Render вводит в заблуждение.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# cgroup v2 (современные хосты) и v1 (старые).
_USAGE_PATHS = (
    "/sys/fs/cgroup/memory.current",
    "/sys/fs/cgroup/memory/memory.usage_in_bytes",
)
_LIMIT_PATHS = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)


# «Лимита нет» cgroup обозначает не только словом max, но и огромным числом
# вроде 2^63. Всё, что больше терабайта, лимитом считать бессмысленно.
_NO_LIMIT_ABOVE = 1 << 40


def _read_first(paths: tuple[str, ...]) -> int | None:
    for path in paths:
        try:
            with open(path) as fh:
                value = fh.read().strip()
        except OSError:
            continue
        if value == "max":
            return None
        try:
            return int(value)
        except ValueError:
            continue
    return None


def memory() -> tuple[int | None, int | None]:
    """Текущий расход и лимит памяти в байтах (или None, если не определить)."""
    limit = _read_first(_LIMIT_PATHS)
    if limit is not None and limit > _NO_LIMIT_ABOVE:
        limit = None
    return _read_first(_USAGE_PATHS), limit


def log_memory(tag: str) -> None:
    """Записать расход памяти. Молчит там, где cgroup недоступен."""
    used, limit = memory()
    if used is None:
        return

    mb = used / 1024 / 1024
    if limit:
        limit_mb = limit / 1024 / 1024
        percent = used / limit * 100
        log.info("Память (%s): %.0f МБ из %.0f МБ (%.0f%%)", tag, mb, limit_mb, percent)
        if percent > 85:
            log.warning(
                "Памяти почти не осталось — контейнер может убить процесс. "
                "Помогут более лёгкий режим или тариф с большим объёмом."
            )
    else:
        log.info("Память (%s): %.0f МБ", tag, mb)


def describe() -> str:
    """Короткая строка о памяти — для сообщений в Telegram."""
    used, limit = memory()
    if used is None:
        return ""
    if limit:
        return f"{used / 1024 / 1024:.0f}/{limit / 1024 / 1024:.0f} МБ"
    return f"{used / 1024 / 1024:.0f} МБ"


def cpu_count() -> int:
    return os.cpu_count() or 1
