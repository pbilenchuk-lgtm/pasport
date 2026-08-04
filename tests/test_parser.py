"""Тесты парсера. Запуск: python -m pytest tests/ -v (или python tests/test_parser.py)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as queue_parser  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def test_closed_page_is_detected():
    """Реальная страница, снятая из HAR: мест нет."""
    status = queue_parser.parse(_fixture("closed.html"))
    assert status.state == queue_parser.CLOSED
    assert not status.has_slots


def test_open_page_is_detected_with_dates():
    """Страница с отрисованным виджетом записи: места есть, даты извлечены."""
    status = queue_parser.parse(_fixture("open.html"))
    assert status.state == queue_parser.OPEN
    assert status.has_slots
    assert status.dates == ["12.08", "13.08", "14.08"]


def test_garbage_is_unknown_not_open():
    """Мусор вместо страницы не должен выглядеть как «даты появились»."""
    for html in ("", "<html><body>hello</body></html>", "x" * 600):
        status = queue_parser.parse(html)
        assert status.state != queue_parser.OPEN, html[:40]


def test_cloudflare_block_is_not_open():
    """Страница блокировки не должна распознаваться как открытая очередь."""
    status = queue_parser.parse("<html><body>Blocked for security reasons</body></html>")
    assert status.state != queue_parser.OPEN


def test_date_extraction_formats():
    """Даты в разных форматах приводятся к ДД.ММ без дублей."""
    html = """
    <main><div class="lg:w-1/2 lg:border-r">
      <form><label>Оберіть дату</label>
      <button data-date="2026-09-01">01.09.2026</button>
      <button>2 жовтня</button>
      <button>03.11</button>
      <button>01.09.2026</button>
      </form>
    </div></main>
    """
    status = queue_parser.parse(html.replace("\n", "") + " " * 600)
    assert status.state == queue_parser.OPEN
    assert status.dates == ["01.09", "02.10", "03.11"]


def test_impossible_dates_are_ignored():
    """32.13 — не дата, в выдачу попасть не должна."""
    html = """
    <main><div class="lg:w-1/2 lg:border-r">
      <form><label>Оберіть дату</label><button>32.13</button><button>05.05</button></form>
    </div></main>
    """
    status = queue_parser.parse(html + " " * 600)
    assert status.dates == ["05.05"]


if __name__ == "__main__":
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
