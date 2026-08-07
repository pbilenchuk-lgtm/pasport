"""Проверки .bat-файлов.

Прошлая версия setup.bat содержала кириллицу и вызывала chcp. cmd.exe читает
батник по байтовым смещениям, а после смены кодовой страницы декодирует их
иначе — остаток файла разбирается вперемешку. На практике это выглядело так:
«install_task.py» превратился в «all_task.py», «exit /b» — в «/b», а установка
молча сломалась.

Отсюда правило: в .bat только ASCII и никакого chcp, весь текст — в .py.

Запуск: python -m pytest tests/ -v (или python tests/test_windows_scripts.py)
"""

from __future__ import annotations

import os
import sys

WINDOWS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "windows"
)


def _bat_files() -> list[str]:
    return [
        os.path.join(WINDOWS_DIR, name)
        for name in sorted(os.listdir(WINDOWS_DIR))
        if name.lower().endswith(".bat")
    ]


def test_bat_files_exist():
    names = {os.path.basename(p) for p in _bat_files()}
    assert "setup.bat" in names
    assert "uninstall.bat" in names


def test_bat_files_are_pure_ascii():
    """Кириллица в батнике ломает разбор файла целиком."""
    for path in _bat_files():
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            data.decode("ascii")
        except UnicodeDecodeError as exc:
            line = data[: exc.start].count(b"\n") + 1
            bad = data[exc.start : exc.start + 20]
            raise AssertionError(
                f"{os.path.basename(path)}, строка {line}: не-ASCII байты {bad!r}. "
                "Текст для пользователя должен жить в .py, а не в .bat"
            )


def test_bat_files_do_not_change_codepage():
    """Именно chcp внутри батника сбивал cmd.exe с позиции в файле."""
    for path in _bat_files():
        with open(path, encoding="ascii") as fh:
            for number, line in enumerate(fh, 1):
                stripped = line.strip().lower()
                if stripped.startswith("rem"):
                    continue
                assert "chcp" not in stripped, (
                    f"{os.path.basename(path)}, строка {number}: вызов chcp. "
                    "Смена кодовой страницы портит разбор остатка файла"
                )


def test_bat_files_have_no_bom():
    """BOM в начале батника cmd.exe выводит как мусор и ломает первую команду."""
    for path in _bat_files():
        with open(path, "rb") as fh:
            assert not fh.read(3).startswith(b"\xef\xbb\xbf"), os.path.basename(path)


def test_setup_bat_delegates_to_python():
    with open(os.path.join(WINDOWS_DIR, "setup.bat"), encoding="ascii") as fh:
        content = fh.read()
    assert "setup.py" in content, "вся логика должна быть в setup.py"
    assert "pause" in content, "окно не должно закрываться, не показав результат"


def test_setup_bat_supports_both_python_launchers():
    """Новый установщик кладёт в PATH py, старый — python."""
    with open(os.path.join(WINDOWS_DIR, "setup.bat"), encoding="ascii") as fh:
        content = fh.read()
    assert "py -3" in content
    assert "python --version" in content


def test_setup_py_is_valid_python():
    import ast

    for name in ("setup.py", "install_task.py"):
        path = os.path.join(WINDOWS_DIR, name)
        with open(path, encoding="utf-8") as fh:
            ast.parse(fh.read(), filename=name)


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
