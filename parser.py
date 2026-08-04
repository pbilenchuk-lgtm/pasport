"""Разбор HTML страницы электронной очереди.

Что удалось выяснить из HAR (см. README, раздел «Разведка»):

Отдельного JSON-API со списком дат у сайта нет. Состояние очереди приходит
уже отрисованным в HTML самой страницы. Когда мест нет, в левой колонке лежит
красный блок:

    <div class="... border-red-500 bg-red-50 text-red-600" role="alert">
      ... <p><strong>Наразі всі місця зайняті.</strong></p>
          <p>Будь ласка, спробуйте в інший час або день.</p> ...
    </div>

Когда талоны появляются, этот блок исчезает, а в колонке отрисовывается виджет
записи (выбор услуги/даты). Точную разметку «открытого» состояния снять не
удалось — на момент экспорта HAR мест не было. Поэтому парсер построен
консервативно:

  * нашли маркер «мест нет»          -> CLOSED
  * маркера нет, но есть виджет/даты -> OPEN
  * ничего не опознали               -> UNKNOWN (не шлём «даты появились»,
                                        но сообщаем, если так подряд надолго)

Такой подход не даст ложной тревоги, если вёрстку поменяют.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# Состояния очереди.
OPEN = "open"
CLOSED = "closed"
UNKNOWN = "unknown"

# Фразы «мест нет» (в нижнем регистре, без учёта пунктуации).
CLOSED_MARKERS = (
    "всі місця зайняті",
    "все места заняты",
    "спробуйте в інший час",
    "наразі немає вільних",
    "немає вільних місць",
    "немає доступних дат",
    "запис тимчасово недоступний",
    "талони відсутні",
)

# Признаки того, что виджет записи реально отрисован.
OPEN_MARKERS = (
    "оберіть дату",
    "оберіть послугу",
    "виберіть дату",
    "виберіть послугу",
    "вільні дати",
    "доступні дати",
    "запис на",
    "обрати час",
    "оберіть час",
)

# Украинские месяцы -> номер, для дат вида «12 серпня».
UA_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4,
    "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
    "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

_RE_DMY = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b")
_RE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RE_UA_TEXT = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(UA_MONTHS) + r")\b", re.IGNORECASE
)
_RE_WS = re.compile(r"\s+")


@dataclass
class QueueStatus:
    """Результат разбора страницы."""

    state: str
    dates: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def has_slots(self) -> bool:
        return self.state == OPEN

    def __str__(self) -> str:
        if self.dates:
            return f"{self.state} ({', '.join(self.dates)})"
        return self.state


def _normalize(text: str) -> str:
    return _RE_WS.sub(" ", text).strip().lower()


def _queue_column(soup: BeautifulSoup):
    """Левая колонка страницы, где живёт либо алерт, либо виджет записи.

    Если структуру не узнали — возвращаем <main>, а если и его нет, то весь
    документ. Лучше проверить лишнего, чем не заметить появившиеся даты.
    """
    main = soup.find("main") or soup

    for div in main.find_all("div"):
        classes = div.get("class") or []
        if "lg:border-r" in classes and any(c.startswith("lg:w-1/2") for c in classes):
            return div

    return main


def _extract_dates(text: str) -> list[str]:
    """Вытащить даты в формате ДД.ММ в том порядке, в котором они идут на странице."""
    hits: list[tuple[int, str]] = []

    def add(position: int, day: int, month: int) -> None:
        if 1 <= day <= 31 and 1 <= month <= 12:
            hits.append((position, f"{day:02d}.{month:02d}"))

    for match in _RE_ISO.finditer(text):
        add(match.start(), int(match.group(3)), int(match.group(2)))

    for match in _RE_DMY.finditer(text):
        add(match.start(), int(match.group(1)), int(match.group(2)))

    for match in _RE_UA_TEXT.finditer(text):
        add(match.start(), int(match.group(1)), UA_MONTHS[match.group(2).lower()])

    hits.sort(key=lambda item: item[0])
    return list(dict.fromkeys(value for _, value in hits))


def parse(html: str) -> QueueStatus:
    """Разобрать HTML страницы очереди и вернуть её состояние."""
    if not html or len(html) < 500:
        return QueueStatus(UNKNOWN, note="пустой или слишком короткий ответ")

    soup = BeautifulSoup(html, "html.parser")
    column = _queue_column(soup)
    column_text = _normalize(column.get_text(" ", strip=True))

    # 1. Явный маркер «мест нет» — самый надёжный сигнал.
    for marker in CLOSED_MARKERS:
        if marker in column_text:
            return QueueStatus(CLOSED, note=marker)

    # Иногда алерт может оказаться вне опознанной колонки — проверим страницу
    # целиком, но только по самой однозначной фразе.
    page_text = _normalize(soup.get_text(" ", strip=True))
    if "всі місця зайняті" in page_text:
        return QueueStatus(CLOSED, note="всі місця зайняті (вне колонки)")

    # 2. Признаки отрисованного виджета записи.
    open_hits = [m for m in OPEN_MARKERS if m in column_text]

    # 3. Интерактивные элементы внутри колонки: форма, select, кнопки с датами.
    controls = column.find_all(["form", "select", "option", "button", "input"])
    dates = _extract_dates(column.get_text(" ", strip=True))

    if open_hits or (controls and dates):
        note = ", ".join(open_hits) if open_hits else "форма записи с датами"
        return QueueStatus(OPEN, dates=dates, note=note)

    # 4. Не опознали. Возможно, поменялась вёрстка — пусть решает monitor.py.
    return QueueStatus(
        UNKNOWN,
        dates=dates,
        note=f"нет ни маркера занятости, ни виджета записи "
        f"(в колонке {len(column_text)} символов, элементов управления: {len(controls)})",
    )
