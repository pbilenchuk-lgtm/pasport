@echo off
rem Keep this file pure ASCII - see the comment in setup.bat for the reason.

echo.
echo Stopping the monitor and removing autostart...

schtasks /End    /TN "PassportQueueMonitor" >nul 2>&1
schtasks /Delete /TN "PassportQueueMonitor" /F >nul 2>&1

rem Kill the background process if it is still running.
taskkill /F /IM pythonw.exe >nul 2>&1

echo Done.
echo.
echo Your .env, state.json and monitor.log are left untouched.
echo Delete them by hand if you no longer need them.
echo.
pause
