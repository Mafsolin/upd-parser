@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not exist "input" mkdir "input"
if not exist "output" mkdir "output"

echo.
echo ===============================================
echo  Локальная обработка УПД
echo ===============================================
echo.
echo Фото нужно положить сюда:
echo %CD%\input
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установите Python и повторите запуск.
    echo.
    pause
    exit /b 1
)

python -c "import requests, openpyxl, dotenv, PIL" >nul 2>nul
if errorlevel 1 (
    echo Устанавливаю зависимости...
    python -m pip install -r "..\requirements.txt"
    if errorlevel 1 (
        echo.
        echo Не удалось установить зависимости.
        pause
        exit /b 1
    )
)

python "process_upd.py" --cli
set EXIT_CODE=%ERRORLEVEL%

echo.
if "%EXIT_CODE%"=="0" (
    echo Готово. Excel лежит в папке:
    echo %CD%\output
) else (
    echo Обработка завершилась с ошибкой. Код: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%
