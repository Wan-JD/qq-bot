@echo off
chcp 65001

echo ==========================================
echo    QQ Bot Launcher (WorkBuddy Edition)
echo ==========================================
echo.

set NAPCAT_DIR=%USERPROFILE%\NapCat\NapCat.44498.Shell
set ASTRBOT_DIR=%USERPROFILE%\AstrBot

echo [1/3] Stopping existing services...
taskkill /f /im python.exe 2>nul
taskkill /f /im QQ.exe 2>nul
timeout /t 2 /nobreak >nul
echo    Done
echo.

echo [2/3] Starting NapCat with QQ 3796981649...
cd /d "%NAPCAT_DIR%"
start "NapCat" cmd /k napcat.auto.bat
timeout /t 8
echo    NapCat started
echo.

echo [3/3] Starting AstrBot...
cd /d "%ASTRBOT_DIR%"
start "AstrBot" cmd /k astrbot run
timeout /t 5
echo    AstrBot started
echo.

echo ==========================================
echo    All services started
echo ==========================================
echo.
echo Usage:
echo   Group chat: just send any message directly
echo   Private chat: send "wolgedou + message"
echo   Test account 1281375417 works without trigger
echo.
echo Note:
echo   NapCat auto-login QQ 3796981649
echo   Wait 15 seconds before testing!
echo   Minimize windows, do not close
echo.
pause
