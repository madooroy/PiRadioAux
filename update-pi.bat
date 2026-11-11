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
rem folder, whose read-only files make a second scp copy fail. The copy is
rem unpacked into a new folder on the Pi and then swapped in, so nothing left
rem over in the old one (folders that arrived read-only, say) can get in the
rem way. Windows marks the folders read-only and lists them before their files,
rem so their permissions are set only at the end (--delay-directory-restore),
rem then made writable. The old copy is kept as ~/%DIR%.previous.
tar -C "%PARENT%" --exclude=.git --exclude=__pycache__ -cf - "%DIR%" | ssh %PI% "set -e; cd ~; chmod -R u+rwX .piradio-new 2>/dev/null || true; rm -rf .piradio-new; mkdir .piradio-new; tar --warning=no-unknown-keyword --no-same-permissions --delay-directory-restore -xf - -C .piradio-new; chmod -R u+rwX,go+rX .piradio-new; if [ -e '%DIR%' ]; then chmod -R u+rwX '%DIR%' '%DIR%.previous' 2>/dev/null || true; rm -rf '%DIR%.previous'; mv '%DIR%' '%DIR%.previous'; fi; mv '.piradio-new/%DIR%' '%DIR%'; rmdir .piradio-new"
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
