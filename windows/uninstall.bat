@echo off
chcp 65001 >nul

echo.
echo Останавливаю монитор и убираю автозапуск...

schtasks /End    /TN "PassportQueueMonitor" >nul 2>&1
schtasks /Delete /TN "PassportQueueMonitor" /F >nul 2>&1

rem На всякий случай добиваем процесс, если он ещё висит.
taskkill /F /IM pythonw.exe >nul 2>&1

echo Готово. Файлы .env, state.json и monitor.log остались на месте —
echo если они больше не нужны, удали их вручную.
echo.
pause
