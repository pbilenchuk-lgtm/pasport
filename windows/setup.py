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
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        # Вывод pip при неудаче огромный, а суть — в паре строк. Если это
        # попытка собрать пакет из исходников, скажем прямо, что делать.
        if "Microsoft Visual C++" in output or "failed building wheel" in output.lower():
            say()
            say("[!] Pip попытался собрать пакет из исходников, а компилятора нет.")
            say("    Это значит, что под твою версию Python готовой сборки пока")
            say("    не выпустили. Самое простое — поставить версию постарше:")
            say()
            say("        py install 3.13")
            say()
            say("    затем удалить папку .venv и запустить setup.bat заново.")
        else:
            say(output[-2000:])
    return result.returncode


def step_venv() -> bool:
    if os.path.exists(VENV_PY):
        say("[1/6] Виртуальное окружение уже есть.")
        return True

    say("[1/6] Создаю виртуальное окружение...")
    if run([sys.executable, "-m", "venv", VENV_DIR]) != 0:
        return False
    return os.path.exists(VENV_PY)


def step_dependencies() -> bool:
    say("[2/6] Ставлю зависимости (это займёт минуту)...")
    run([VENV_PY, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])

    if run([VENV_PY, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"]) != 0:
        return False

    # Браузер нужен по делу: сайт закрыт проверкой Cloudflare, и обычный
    # HTTP-клиент её не проходит — только настоящий Chromium.
    say("      Ставлю браузер (около 150 МБ, это дольше)...")
    if run([VENV_PY, "-m", "pip", "install", "--quiet", "-r", "requirements-browser.txt"]) != 0:
        say("      Не удалось поставить playwright.")
        return False

    if run([VENV_PY, "-m", "playwright", "install", "chromium"]) != 0:
        say("      Не удалось скачать Chromium.")
        return False

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
        say("[3/6] Настройки уже заданы в файле .env.")
        return True

    say()
    say("[3/6] Настройка Telegram.")
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

    existing.update({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})
    write_env(existing)

    say()
    say("    Сохранил в файл .env — там же всё можно поменять.")
    return True


def write_env(values: dict[str, str]) -> None:
    values.setdefault("FETCH_MODE", "browser")
    values.setdefault("LOG_FILE", "monitor.log")
    with open(ENV_FILE, "w", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def step_pick_browser_mode() -> bool:
    """Подобрать режим браузера, который проходит проверку Cloudflare.

    Сначала пробуем скрытый браузер: он не мозолит глаза. Если Cloudflare его
    не пропускает — переходим на обычный, с окном за пределами экрана.
    Результат записываем в .env, чтобы монитор не подбирал это каждый раз.
    """
    say()
    say("[5/6] Подбираю режим браузера (проверка Cloudflare)...")

    for headless, label in ((True, "скрытый"), (False, "обычный")):
        say(f"      Пробую {label} браузер...")
        env = dict(os.environ)
        env["FETCH_MODE"] = "browser"
        env["BROWSER_HEADLESS"] = "true" if headless else "false"

        result = subprocess.run(
            [VENV_PY, "monitor.py", "--probe"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            say(f"      Подходит: {label} браузер.")
            values = read_env()
            values["FETCH_MODE"] = "browser"
            values["BROWSER_HEADLESS"] = "true" if headless else "false"
            write_env(values)
            return True

        for line in (result.stdout or "").splitlines():
            if line.startswith(("Вердикт", "Детали")):
                say("        " + line.strip())

    say()
    say("[!] Сайт не открылся ни скрытым, ни обычным браузером.")
    say("    Автозапуск всё равно настрою — возможно, это временно.")
    say("    Проверить вручную:  .venv\\Scripts\\python.exe monitor.py --probe")
    return False


def step_telegram_check() -> bool:
    say()
    say("[4/6] Проверяю, доходят ли уведомления...")
    if run([VENV_PY, "monitor.py", "--test-telegram"], quiet=False) != 0:
        say()
        say("[!] Уведомление не дошло.")
        say("    Самая частая причина: не нажат /start в чате с ботом —")
        say("    Telegram не даёт боту написать первым.")
        say("    Исправь и запусти setup.bat заново.")
        return False
    return True


def step_autostart(extra_args: list[str]) -> bool:
    say()
    say("[6/6] Настраиваю автозапуск...")
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

    if not step_telegram_check():
        return 1

    site_ok = step_pick_browser_mode()

    if not step_autostart(sys.argv[1:]):
        return fail("Не удалось настроить автозапуск.")

    say()
    say("==========================================================")
    if site_ok:
        say("  Готово. Монитор работает в фоне и стартует сам")
        say("  при входе в систему.")
        say()
        say("  В Telegram придёт сводка о текущем состоянии очереди.")
    else:
        say("  Автозапуск настроен, но сайт сейчас не открывается.")
        say("  Монитор будет пробовать дальше и сообщит, когда получится.")
    say()
    say("  Логи:      monitor.log")
    say("  Настройки: .env")
    say("  Убрать:    windows\\uninstall.bat")
    say("==========================================================")
    say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
