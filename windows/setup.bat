@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0.."

echo.
echo ==========================================================
echo   Монитор очереди паспортного сервиса — установка
echo ==========================================================
echo.

rem --- 1. Python ---------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
	echo [!] Python не найден.
	echo     Установи Python 3.10 или новее с https://www.python.org/downloads/
	echo     ВАЖНО: при установке поставь галочку "Add Python to PATH".
	echo.
	pause
	exit /b 1
)
echo [1/5] Python найден.

rem --- 2. Виртуальное окружение -----------------------------------------
if not exist ".venv\Scripts\python.exe" (
	echo [2/5] Создаю виртуальное окружение...
	python -m venv .venv
	if errorlevel 1 (
		echo [!] Не удалось создать окружение.
		pause
		exit /b 1
	)
) else (
	echo [2/5] Виртуальное окружение уже есть.
)

rem --- 3. Зависимости ----------------------------------------------------
echo [3/5] Ставлю зависимости...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
	echo [!] Не удалось поставить зависимости.
	pause
	exit /b 1
)

rem --- 4. Настройки ------------------------------------------------------
if not exist ".env" (
	echo.
	echo [4/5] Настройка Telegram.
	echo.
	echo     Токен берётся у @BotFather.
	echo     ВАЖНО: перед первым запуском открой чат со своим ботом
	echo     и нажми /start, иначе Telegram не даст боту написать тебе.
	echo.
	set /p BOT_TOKEN=Вставь TELEGRAM_BOT_TOKEN:
	set /p CHAT_ID=Вставь TELEGRAM_CHAT_ID:

	>  ".env" echo TELEGRAM_BOT_TOKEN=!BOT_TOKEN!
	>> ".env" echo TELEGRAM_CHAT_ID=!CHAT_ID!
	>> ".env" echo FETCH_MODE=direct
	>> ".env" echo LOG_FILE=monitor.log
	echo.
	echo     Настройки сохранены в файл .env — там же их можно поменять.
) else (
	echo [4/5] Файл .env уже есть, оставляю как есть.
)

rem --- 5. Проверка и автозапуск -----------------------------------------
echo.
echo [5/5] Проверяю, доходят ли уведомления...
".venv\Scripts\python.exe" monitor.py --test-telegram
if errorlevel 1 (
	echo.
	echo [!] Уведомление не дошло. Самая частая причина: ты ещё не нажал
	echo     /start в чате с ботом — Telegram не даёт боту написать первым.
	echo     Исправь и запусти setup.bat заново.
	echo.
	pause
	exit /b 1
)

echo.
echo Проверяю доступ к сайту...
".venv\Scripts\python.exe" monitor.py --probe
if errorlevel 1 (
	echo.
	echo [!] С этого компьютера сайт не открывается. Автозапуск всё равно
	echo     настрою, но сначала проверь интернет и открой в браузере:
	echo     https://warszawa.pasport.org.ua/solutions/e-queue
	echo.
)

echo.
".venv\Scripts\python.exe" "windows\install_task.py" %*
if errorlevel 1 (
	echo [!] Не удалось настроить автозапуск.
	pause
	exit /b 1
)

echo.
echo ==========================================================
echo   Готово. Монитор работает в фоне и стартует сам
echo   при входе в систему.
echo.
echo   Логи:      monitor.log
echo   Настройки: .env
echo   Убрать:    windows\uninstall.bat
echo ==========================================================
echo.
pause
