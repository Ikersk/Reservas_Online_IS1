@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo  Configuracion inicial - Barberia (Windows)
echo ============================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=python"
    ) else (
        echo [ERROR] No se encontro Python en el PATH.
        echo Instala Python 3.10+ y marca "Add Python to PATH".
        pause
        exit /b 1
    )
)

echo [1/4] Verificando entorno virtual...
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo Entorno virtual ya existe.
)

echo.
echo [2/4] Instalando dependencias...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Fallo al instalar dependencias.
    pause
    exit /b 1
)

echo.
echo [3/4] Revisando archivo .env...
if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo Se creo .env desde .env.example
    ) else (
        echo SECRET_KEY=cambia-esta-clave-secreta> .env
        echo ADMIN_PASSWORD=barberia123>> .env
        echo SMTP_HOST=>> .env
        echo SMTP_PORT=587>> .env
        echo SMTP_USER=>> .env
        echo SMTP_PASSWORD=>> .env
        echo SMTP_USE_TLS=true>> .env
        echo SMTP_FROM=>> .env
        echo PAYMENT_RECEIVER_BANK=Banco de Venezuela>> .env
        echo PAYMENT_RECEIVER_PHONE=0412-0000000>> .env
        echo PAYMENT_RECEIVER_ID=V-00000000>> .env
        echo PAYMENT_RECEIVER_NAME=Barberia>> .env
        echo PAYMENT_REFERENCE_PREFIX=CITA>> .env
        echo PAYMENT_PENDING_MINUTES=10>> .env
        echo BCV_USD_RATE=36.50>> .env
        echo Se creo .env con valores base.
    )
    echo IMPORTANTE: abre .env y configura tus credenciales antes de usar en produccion.
) else (
    echo .env ya existe.
)

echo.
echo [4/4] Configuracion completada.
echo Usa run_windows.bat para iniciar el proyecto.
echo.
pause
exit /b 0
