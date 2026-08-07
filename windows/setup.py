"""Установка монитора на Windows.

Вся логика живёт здесь, а не в .bat, по прозаичной причине: cmd.exe не умеет
надёжно исполнять батник с кириллицей. Он читает файл по байтовым смещениям, а
после chcp декодирует их иначе — и остаток файла разбирается вперемешку, вплоть
до того, что «install_task.py» превращается в «all_task.py». Поэтому setup.bat
оставлен пустой ASCII-обёрткой, а весь текст и все шаги — тут.

Запускается через windows\\setup.bat, вручную вызывать не нужно.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(ROOT, ".venv")
VENV_PY = os.path.join(VENV_DIR, "Scripts", "python.exe")
ENV_FILE = os.path.join(ROOT, ".env")

TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
CHAT_ID_RE = re.compile(r"^-?\d{5,}$")


def say(text: str = "") -> None:
    print(text, flush=True)


def fail(text: str) -> int:
    say()
    say("[!] " + text)
    say()
    return 1


def run(args: list[str], quiet: bool = True) -> int:
    """Запустить команду, вернуть код возврата."""
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=quiet,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if quiet and result.returncode != 0:
        # Показываем вывод только когда что-то пошло не так.
        say((result.stdout or "").strip()[-2000:])
        say((result.stderr or "").strip()[-2000:])
    return result.returncode


def step_venv() -> bool:
    if os.path.exists(VENV_PY):
        say("[1/4] Виртуальное окружение уже есть.")
        return True

    say("[1/4] Создаю виртуальное окружение...")
    if run([sys.executable, "-m", "venv", VENV_DIR]) != 0:
        return False
    return os.path.exists(VENV_PY)


def step_dependencies() -> bool:
    say("[2/4] Ставлю зависимости (это займёт минуту)...")
    run([VENV_PY, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])

    code = run([VENV_PY, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"])
    if code != 0:
        return False

    # Необязательное дополнение: нужно только там, где Cloudflare придирается к
    # прямым запросам. Дома не требуется, поэтому неудача здесь не критична.
    run([VENV_PY, "-m", "pip", "install", "--quiet", "-r", "requirements-impersonate.txt"])
    return True


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(ENV_FILE):
        return values
    with open(ENV_FILE, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def ask(prompt: str, pattern: re.Pattern, hint: str) -> str:
    while True:
        value = input(prompt).strip().strip('"').strip("'")
        if pattern.match(value):
            return value
        say("    Не похоже на правду. " + hint)


def step_settings() -> bool:
    existing = read_env()
    token = existing.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = existing.get("TELEGRAM_CHAT_ID", "")

    if TOKEN_RE.match(token) and CHAT_ID_RE.match(chat_id):
        say("[3/4] Настройки уже заданы в файле .env.")
        return True

    say()
    say("[3/4] Настройка Telegram.")
    say()
    say("    Токен берётся у @BotFather и выглядит так: 1234567890:AAxxxxxxxx")
    say("    Свой chat_id можно узнать у бота @userinfobot — это число.")
    say()
    say("    ВАЖНО: открой чат со своим ботом и нажми /start,")
    say("    иначе Telegram не даст ему написать тебе первым.")
    say()
    say("    Вставлять в консоль — правой кнопкой мыши.")
    say()

    if not TOKEN_RE.match(token):
        token = ask(
            "TELEGRAM_BOT_TOKEN: ", TOKEN_RE, "Токен вида 1234567890:AAxxxxxxxxxx"
        )
    if not CHAT_ID_RE.match(chat_id):
        chat_id = ask("TELEGRAM_CHAT_ID:  ", CHAT_ID_RE, "Это должно быть число.")

    existing.update(
        {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": chat_id,
            "FETCH_MODE": existing.get("FETCH_MODE", "direct"),
            "LOG_FILE": existing.get("LOG_FILE", "monitor.log"),
        }
    )

    with open(ENV_FILE, "w", encoding="utf-8") as fh:
        for key, value in existing.items():
            fh.write(f"{key}={value}\n")

    say()
    say("    Сохранил в файл .env — там же всё можно поменять.")
    return True


def step_checks_and_autostart(extra_args: list[str]) -> bool:
    say()
    say("[4/4] Проверяю, доходят ли уведомления...")
    if run([VENV_PY, "monitor.py", "--test-telegram"], quiet=False) != 0:
        say()
        say("[!] Уведомление не дошло.")
        say("    Самая частая причина: не нажат /start в чате с ботом —")
        say("    Telegram не даёт боту написать первым.")
        say("    Исправь и запусти setup.bat заново.")
        return False

    say()
    say("Проверяю доступ к сайту...")
    if run([VENV_PY, "monitor.py", "--probe"], quiet=False) != 0:
        say()
        say("[!] С этого компьютера сайт не открылся. Автозапуск всё равно настрою,")
        say("    но проверь интернет и открой ссылку в браузере:")
        say("    https://warszawa.pasport.org.ua/solutions/e-queue")

    say()
    task_script = os.path.join(ROOT, "windows", "install_task.py")
    return run([VENV_PY, task_script] + extra_args, quiet=False) == 0


def main() -> int:
    say()
    say("==========================================================")
    say("  Монитор очереди паспортного сервиса — установка")
    say("==========================================================")
    say()
    say(f"Python: {sys.version.split()[0]}")
    say(f"Папка:  {ROOT}")
    say()

    if not step_venv():
        return fail("Не удалось создать виртуальное окружение.")

    if not step_dependencies():
        return fail("Не удалось поставить зависимости. Проверь интернет.")

    if not step_settings():
        return fail("Настройки не заданы.")

    if not step_checks_and_autostart(sys.argv[1:]):
        return 1

    say()
    say("==========================================================")
    say("  Готово. Монитор работает в фоне и стартует сам")
    say("  при входе в систему.")
    say()
    say("  Логи:      monitor.log")
    say("  Настройки: .env")
    say("  Убрать:    windows\\uninstall.bat")
    say("==========================================================")
    say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
