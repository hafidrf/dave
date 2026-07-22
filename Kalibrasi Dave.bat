@echo off
title Dave - Kalibrasi Layar
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] Virtual environment belum dibuat. Jalankan instalasi dulu.
    pause
    exit /b 1
)

echo.
echo  Buka Quiz.com di browser DULU (ukuran window final).
echo  Lalu tekan tombol apa saja untuk mulai kalibrasi...
pause >nul

".venv\Scripts\python.exe" calibrate.py
echo.
pause
