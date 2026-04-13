@echo off
REM ── MT5 EA Optimizer Launcher ──────────────────────────────────────────────
REM Double-click this file to start the optimizer app.
REM Browser will open automatically.

title MT5 EA Optimizer

SET PY=C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe
SET APP=%~dp0app.py

echo.
echo  =====================================================
echo    MT5 EA Optimizer — Starting...
echo    Your browser will open in a few seconds.
echo    Do NOT close this window while optimizer is running.
echo  =====================================================
echo.

"%PY%" "%APP%"

echo.
echo  Optimizer stopped. Press any key to close.
pause > nul
