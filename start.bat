@echo off
setlocal

set "PYTHON=%~dp0.venv\Scripts\python.exe"

title MediaCollector

echo Starting MediaCollector...
echo.

if not exist "%PYTHON%" (
    echo Project Python not found: "%PYTHON%"
    echo Follow the setup steps in README.md, then run start.bat again.
    pause
    exit /b 1
)

cd /d "%~dp0backend" || goto :failed

echo Opening http://localhost:8000 after the server starts...
start "" /b powershell.exe -NoProfile -Command "for ($i = 0; $i -lt 20; $i++) { try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1 } catch { $health = $null }; if ($health.status -eq 'ok') { Start-Process 'http://localhost:8000'; break }; Start-Sleep -Milliseconds 500 }"
echo.
echo Keep this window open while using MediaCollector.
echo Press Ctrl+C to stop the server.
echo.

"%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo MediaCollector exited with an error.
pause
exit /b 1
