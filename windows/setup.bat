@echo off
rem ---------------------------------------------------------------------------
rem  Launcher only. All real work happens in windows\setup.py.
rem
rem  IMPORTANT: keep this file pure ASCII and never call chcp here.
rem  cmd.exe reads .bat files by byte offset and re-decodes them after a codepage
rem  change, which corrupts the rest of the file - commands get split into
rem  garbage. Any text for the user belongs in setup.py, not here.
rem ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0.."

set "PY_CMD="

rem New Python install manager puts "py" on PATH; classic installer puts "python".
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"

if not defined PY_CMD (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo.
    echo   Python not found.
    echo.
    echo   If you have JUST installed Python: close this window and run
    echo   setup.bat again. PATH is only updated for newly opened windows.
    echo.
    echo   If Python is not installed yet: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

%PY_CMD% "windows\setup.py" %*
set "RESULT=%ERRORLEVEL%"

echo.
pause
exit /b %RESULT%
