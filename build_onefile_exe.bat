@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Для сборки exe на этом компьютере нужен Python.
    echo На компьютере пользователя Python уже не понадобится.
    pause
    exit /b 1
)

python build_onefile_exe.py
set EXIT_CODE=%ERRORLEVEL%
echo.
pause
exit /b %EXIT_CODE%
