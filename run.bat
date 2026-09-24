@echo off
REM Million Piksel — lokal ishga tushirish (Windows).
REM manage.py backend/ ichida, shuning uchun bu fayl o'zi kerakli papkaga o'tadi.

cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Virtual muhit yaratilmoqda...
    python -m venv .venv
    if errorlevel 1 goto :err
    echo [2/3] Kutubxonalar o'rnatilmoqda...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto :err
)

if not exist "dev.sqlite3" (
    echo [3/3] Ma'lumotlar bazasi tayyorlanmoqda...
    .venv\Scripts\python.exe manage.py migrate
    if errorlevel 1 goto :err
    echo.
    echo Admin hisobi yarating ^(parol kiritishda harflar ko'rinmaydi^):
    .venv\Scripts\python.exe manage.py createsuperuser
)

echo.
echo ================================================
echo   Sayt   : http://127.0.0.1:8000
echo   Panel  : http://127.0.0.1:8000/admin/panel/  ^(superuser login/paroli bilan^)
echo   To'xtatish: Ctrl+C
echo ================================================
echo.

.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --ws-ping-interval 20
goto :eof

:err
echo.
echo XATO yuz berdi. Yuqoridagi xabarni o'qing.
pause
