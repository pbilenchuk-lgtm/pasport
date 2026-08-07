@echo off
rem Keep this file pure ASCII - see the comment in setup.bat for the reason.
rem
rem Undoes keep-awake.bat and returns the usual Windows defaults.

echo.
echo Restoring normal sleep behaviour...

rem Sleep after 30 minutes idle, hibernate after 3 hours, screen off after 10.
powercfg /change standby-timeout-ac 30
powercfg /change hibernate-timeout-ac 180
powercfg /change monitor-timeout-ac 10

rem Closing the lid puts the laptop to sleep again.
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /setactive SCHEME_CURRENT

echo.
echo Done. The laptop will sleep as usual.
echo Remember: while it sleeps, the monitor does not check the site.
echo.
pause
