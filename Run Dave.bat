@echo off
title Dave - Quiz Suggest Assistant
cd /d "%~dp0"
setlocal EnableDelayedExpansion

echo.
echo  ========================================
echo    Dave - Quiz Suggest Assistant
echo  ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [!] Virtual environment belum ada. Menginstall...
    python -m venv .venv
    if errorlevel 1 (
        echo [!] Gagal buat venv. Pastikan Python terinstall.
        goto :pause_exit
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] Gagal install dependencies.
        goto :pause_exit
    )
)

echo [i] Menjalankan Dave - mode LIVE ^(auto-scan layar, tanpa kalibrasi^).
echo     Buka Quiz.com, lalu biarkan Dave mengawasi layar.
echo     Tutup jendela overlay biru untuk berhenti.
echo.
".venv\Scripts\python.exe" run_dave.py
set ERR=!errorlevel!

echo.
if !ERR! neq 0 (
    echo [!] Dave berhenti dengan error ^(kode !ERR!^).
) else (
    echo [i] Dave selesai. Ringkasan sesi ada di atas.
)
goto :pause_exit

:pause_exit
echo.
pause
endlocal
