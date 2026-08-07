@echo off
rem Keep this file pure ASCII - see the comment in setup.bat for the reason.
rem
rem Stops the laptop from sleeping WHILE PLUGGED IN, so the monitor keeps
rem checking the site. Battery behaviour is left untouched on purpose:
rem nobody wants a laptop draining itself in a bag.

echo.
echo ==========================================================
echo   Keep the laptop awake while plugged in
echo ==========================================================
echo.

rem Never sleep or hibernate on AC power.
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

rem Screen may still turn off after 10 minutes - that saves power
rem and does not stop the monitor.
powercfg /change monitor-timeout-ac 10

rem Closing the lid should not put it to sleep on AC power.
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
if errorlevel 1 (
    echo.
    echo   Note: could not change the lid action.
    echo   Right-click this file and pick "Run as administrator" to allow it.
)
powercfg /setactive SCHEME_CURRENT

echo.
echo   Done. While plugged in the laptop will stay awake,
echo   even with the lid closed. The screen still turns off.
echo.
echo   On battery nothing changed - it will sleep as before.
echo   To undo: restore-sleep.bat
echo.
pause
