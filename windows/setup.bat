@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0.."

echo.
echo ==========================================================
echo   Монитор очереди паспортного сервиса — установка
echo ==========================================================
echo.

rem --- 1. Python --------------------------------------------------------
rem Ищем и py (Python install manager), и python: новый установщик может
rem положить в PATH только py, а старый — только python.
set "PY_CMD="

py -3 --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"

if not defined PY_CMD (
	python --version >nul 2>&1
	if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
	echo [!] Python не найден.
	echo.
	echo     Если ты только что его установил — ЗАКРОЙ ЭТО ОКНО и открой
	echo     файл заново. Установщик прописывает PATH, и старые окна об
	echo     этом не знают.
	echo.
	echo     Если Python ещё не стоит: https://www.python.org/downloads/
	echo.
	pause
	exit /b 1
)

for /f "tokens=*" %%v in ('!PY_CMD! --version 2^>^&1') do set "PY_VER=%%v"
echo [1/5] Нашёл !PY_VER! (команда: !PY_CMD!)

rem --- 2. Виртуальное окружение ----------------------------------------
if not exist ".venv\Scripts\python.exe" (
	echo [2/5] Создаю виртуальное окружение...
	!PY_CMD! -m venv .venv
	if errorlevel 1 (
		echo [!] Не удалось создать окружение.
		pause
		exit /b 1
	)
) else (
	echo [2/5] Виртуальное окружение уже есть.
)

set "VENV_PY=.venv\Scripts\python.exe"

rem --- 3. Зависимости ---------------------------------------------------
echo [3/5] Ставлю зависимости...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
	echo [!] Не удалось поставить зависимости.
	pause
	exit /b 1
)

rem Необязательное дополнение. Дома оно не нужно, а под свежие версии Python
rem сборки может не быть — поэтому неудача здесь не считается ошибкой.
"%VENV_PY%" -m pip install --quiet -r requirements-impersonate.txt >nul 2>&1

rem --- 4. Настройки -----------------------------------------------------
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

rem --- 5. Проверка и автозапуск ----------------------------------------
echo.
echo [5/5] Проверяю, доходят ли уведомления...
"%VENV_PY%" monitor.py --test-telegram
if errorlevel 1 (
	echo.
	echo [!] Уведомление не дошло. Самая частая причина: не нажат /start
	echo     в чате с ботом — Telegram не даёт боту написать первым.
	echo     Исправь и запусти этот файл заново.
	echo.
	pause
	exit /b 1
)

echo.
echo Проверяю доступ к сайту...
"%VENV_PY%" monitor.py --probe
if errorlevel 1 (
	echo.
	echo [!] С этого компьютера сайт не открывается. Автозапуск всё равно
	echo     настрою, но сначала проверь интернет и открой в браузере:
	echo     https://warszawa.pasport.org.ua/solutions/e-queue
	echo.
)

echo.
"%VENV_PY%" "windows\install_task.py" %*
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
