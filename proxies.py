"""Пул прокси: подобрать рабочий и переключиться, когда текущий отвалится.

Зачем: сайт закрыт для датацентровых IP, поэтому монитор ходит через прокси.
Прокси — расходник: они умирают, банятся и тормозят. Держать список и
переключаться автоматически надёжнее, чем вписывать один адрес руками.

Поддерживаемые форматы строки (по одной на строку, `#` — комментарий):

    host:port:user:pass          <- формат выгрузок большинства провайдеров
    host:port
    http://user:pass@host:port
    socks5://user:pass@host:port

Список берётся из PROXY_LIST (прямо в переменной окружения, через перевод
строки или запятую) либо из файла PROXY_LIST_PATH. В логи адреса пишутся
без пароля.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote, urlsplit

log = logging.getLogger(__name__)

_SCHEME_RE = re.compile(r"^[a-z0-9+.-]+://", re.IGNORECASE)


def parse_proxy(line: str) -> str | None:
    """Привести строку к URL вида scheme://user:pass@host:port."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    if _SCHEME_RE.match(line):
        return line

    parts = line.split(":")
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    if len(parts) == 4:
        host, port, user, password = parts
        # Логин и пароль могут содержать символы, ломающие URL.
        return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"

    log.warning("Не понял строку прокси, пропускаю: %r", line[:40])
    return None


def mask(proxy: str | None) -> str:
    """Адрес прокси без пароля — для логов и сообщений в Telegram."""
    if not proxy:
        return "нет (прямое подключение)"
    try:
        parts = urlsplit(proxy)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        user = f"{parts.username}@" if parts.username else ""
        return f"{parts.scheme}://{user}{host}{port}"
    except ValueError:
        return "<нераспознанный прокси>"


def load_proxies(inline: str = "", path: str = "") -> list[str]:
    """Собрать список прокси из переменной окружения и/или файла."""
    raw: list[str] = []

    if inline:
        raw.extend(re.split(r"[\n,]+", inline))

    if path:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                raw.extend(fh.readlines())
        else:
            log.warning("Файл со списком прокси не найден: %s", path)

    seen: list[str] = []
    for line in raw:
        proxy = parse_proxy(line)
        if proxy and proxy not in seen:
            seen.append(proxy)
    return seen


class ProxyPool:
    """Хранит список прокси и отдаёт текущий.

    Пустой пул — это нормальный режим «ходим напрямую»: current() вернёт None.
    """

    def __init__(self, proxies: list[str]) -> None:
        self.proxies = list(proxies)
        self.index = 0
        self.dead: set[str] = set()

    def __len__(self) -> int:
        return len(self.proxies)

    @property
    def enabled(self) -> bool:
        return bool(self.proxies)

    def current(self) -> str | None:
        if not self.proxies:
            return None
        return self.proxies[self.index % len(self.proxies)]

    def rotate(self, mark_dead: bool = True) -> str | None:
        """Перейти к следующему прокси. Возвращает новый адрес (или None)."""
        if not self.proxies:
            return None

        if mark_dead:
            self.dead.add(self.proxies[self.index % len(self.proxies)])

        # Прошли весь список — начинаем круг заново: прокси могли ожить.
        if len(self.dead) >= len(self.proxies):
            log.warning("Все %d прокси перебраны без успеха, начинаю круг заново", len(self.proxies))
            self.dead.clear()

        self.index = (self.index + 1) % len(self.proxies)
        while self.proxies[self.index] in self.dead:
            self.index = (self.index + 1) % len(self.proxies)

        log.info("Переключился на прокси %s", mask(self.current()))
        return self.current()

    def mark_good(self) -> None:
        """Текущий прокси ответил — снимаем с него метку «мёртвый»."""
        self.dead.discard(self.current() or "")
