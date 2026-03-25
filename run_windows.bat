@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] No existe entorno virtual.
    echo Ejecuta primero setup_windows.bat
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] No existe archivo .env
    echo Ejecuta setup_windows.bat para generarlo.
    pause
    exit /b 1
)

echo ===============================
echo  Iniciando Barberia App...
echo ===============================
echo.
echo URL Reservas: http://127.0.0.1:5000/
echo URL Admin:    http://127.0.0.1:5000/admin/login
echo.

call ".venv\Scripts\python.exe" app.py

echo.
echo La aplicacion se detuvo.
pause
exit /b 0
