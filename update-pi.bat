@echo off
rem Windows helper: copies this project folder to the Pi and runs the installer there.
rem Double-click it, or run:  update-pi.bat pi@192.168.1.50
rem You will be asked for the Pi's password two or three times.
set "PI=pi@piradio.local"
if not "%~1"=="" set "PI=%~1"
set "SRC=%~dp0"
set "SRC=%SRC:~0,-1%"
for %%I in ("%SRC%") do set "DIR=%%~nxI"
for %%I in ("%SRC%\..") do set "PARENT=%%~fI"
echo Copying "%SRC%" to %PI% ...
rem tar (built into Windows 10 and 11) instead of scp: it leaves out Git's own
rem folder, whose read-only files make a second scp copy fail.
tar -C "%PARENT%" --exclude=.git --exclude=__pycache__ -cf - "%DIR%" | ssh %PI% "tar --warning=no-unknown-keyword -xf - -C ~"
if errorlevel 1 goto failed
echo.
echo Installing on the Pi ...
ssh -t %PI% "cd ~/'%DIR%' && sudo bash install.sh"
if errorlevel 1 goto failed
echo.
echo Finished. Reload the page on your phone.
pause
exit /b 0
:failed
echo.
echo Something went wrong. The messages above say what.
pause
exit /b 1
