@echo off
setlocal
title SRA Local Workspace

cd /d "%~dp0"

set "SRA_EXE=%~dp0.conda-env\Scripts\sra.exe"
set "PYTHON_EXE=%~dp0.conda-env\python.exe"

if exist "%SRA_EXE%" (
    echo Starting SRA local workspace...
    "%SRA_EXE%" ui
    goto :done
)

if exist "%PYTHON_EXE%" (
    echo SRA console launcher was not found.
    echo Trying the Python module directly...
    "%PYTHON_EXE%" -m sociology_research.cli ui
    goto :done
)

echo ERROR: Cannot find the local environment:
echo   "%~dp0.conda-env"
echo.
echo Make sure this BAT file is in the project root.
pause
exit /b 1

:done
if errorlevel 1 (
    echo.
    echo SRA exited with an error.
    pause
)
exit /b %errorlevel%
