@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\Jercy\AppData\Local\Programs\Python\Python311\python.exe"

if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" app.py
    goto finished
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 app.py
    goto finished
)

where python >nul 2>nul
if not errorlevel 1 (
    python app.py
    goto finished
)

echo Python was not found. Please install Python 3.11 or add python to PATH.
pause
exit /b 1

:finished
if errorlevel 1 pause
