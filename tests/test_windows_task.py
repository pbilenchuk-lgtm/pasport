"""Тесты задачи планировщика Windows.

Проверять на самой Windows нечем, но XML можно разобрать где угодно, а именно
в нём живут настройки, без которых на засыпающем ноутбуке монитор работать не
будет.

Запуск: python -m pytest tests/ -v (или python tests/test_windows_task.py)
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "windows"))

from install_task import build_xml  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _xml(wake: bool = False) -> ET.Element:
    text = build_xml(
        python=r"C:\monitor\.venv\Scripts\pythonw.exe",
        script=r"C:\monitor\monitor.py",
        workdir=r"C:\monitor",
        user="HOME\\user",
        wake=wake,
    )
    return ET.fromstring(text)


def test_xml_is_well_formed():
    assert _xml() is not None


def test_runs_without_console_window():
    """pythonw.exe вместо python.exe — иначе при каждом входе будет чёрное окно."""
    command = _xml().find(".//t:Exec/t:Command", NS).text
    assert command.endswith("pythonw.exe"), command


def test_starts_on_logon():
    assert _xml().find(".//t:LogonTrigger", NS) is not None


def test_periodic_trigger_revives_a_dead_monitor():
    """Ноутбук просыпается — задача должна подняться сама, а не ждать входа."""
    repetition = _xml().find(".//t:CalendarTrigger/t:Repetition/t:Interval", NS)
    assert repetition is not None and repetition.text == "PT15M"


def test_second_instance_is_not_started():
    """Повторный триггер не должен плодить копии монитора."""
    policy = _xml().find(".//t:MultipleInstancesPolicy", NS)
    assert policy.text == "IgnoreNew"


def test_battery_does_not_stop_the_monitor():
    """На ноутбуке настройки по умолчанию убили бы задачу при отключении зарядки."""
    root = _xml()
    assert root.find(".//t:DisallowStartIfOnBatteries", NS).text == "false"
    assert root.find(".//t:StopIfGoingOnBatteries", NS).text == "false"


def test_restarts_after_failure():
    root = _xml()
    assert root.find(".//t:RestartOnFailure/t:Interval", NS).text == "PT1M"
    assert int(root.find(".//t:RestartOnFailure/t:Count", NS).text) > 100


def test_no_execution_time_limit():
    """PT0S — без ограничения: монитор должен работать сутками."""
    assert _xml().find(".//t:ExecutionTimeLimit", NS).text == "PT0S"


def test_wake_flag_is_off_by_default_and_can_be_enabled():
    assert _xml(wake=False).find(".//t:WakeToRun", NS).text == "false"
    assert _xml(wake=True).find(".//t:WakeToRun", NS).text == "true"


def test_paths_with_spaces_stay_quoted():
    text = build_xml(
        python=r"C:\Program Files\monitor\.venv\Scripts\pythonw.exe",
        script=r"C:\Program Files\monitor\monitor.py",
        workdir=r"C:\Program Files\monitor",
        user="HOME\\user",
        wake=False,
    )
    root = ET.fromstring(text)
    assert root.find(".//t:Exec/t:Arguments", NS).text.startswith('"')
    assert root.find(".//t:Exec/t:Arguments", NS).text.endswith('"')


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
