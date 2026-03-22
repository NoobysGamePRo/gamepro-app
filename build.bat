@echo off
echo ============================================
echo  Noobys GamePRo - Build Script
echo ============================================
echo.

REM Check that pyinstaller is installed
where pyinstaller >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: PyInstaller not found.
    echo Install it with:  pip install pyinstaller
    pause
    exit /b 1
)

REM Build to TEMP to avoid OneDrive file-lock on dist\GamePRo.exe
set TMPWORK=%LOCALAPPDATA%\Temp\gamepro_work
set TMPDIST=%LOCALAPPDATA%\Temp\gamepro_dist

echo Building GamePRo.exe ...
echo (building outside OneDrive to avoid file-lock issues)
echo.
pyinstaller gamepro.spec --clean --workpath "%TMPWORK%" --distpath "%TMPDIST%"

if %ERRORLEVEL% equ 0 (
    echo.
    echo Copying GamePRo.exe to dist\ ...
    if not exist dist mkdir dist
    copy /y "%TMPDIST%\GamePRo.exe" dist\GamePRo.exe
    echo.
    echo Copying scripts folder to dist\ ...
    if exist dist\scripts rmdir /s /q dist\scripts
    xcopy /e /i /q scripts dist\scripts
    echo.
    echo ============================================
    echo  Build successful!
    echo  Output: dist\GamePRo.exe
    echo           dist\scripts\  (ship alongside the .exe)
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  Build FAILED. Check output above.
    echo ============================================
)

pause
