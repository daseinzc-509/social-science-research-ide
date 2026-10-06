@echo off
setlocal
cd /d "%~dp0"

if exist ".conda-env\Scripts\sra.exe" (
  ".conda-env\Scripts\sra.exe" ui
  goto :end
)

if exist ".conda-env\python.exe" (
  ".conda-env\python.exe" -m sociology_research.cli ui
  goto :end
)

echo.
echo Could not find .conda-env in this project folder.
echo Expected one of:
echo   .conda-env\Scripts\sra.exe
echo   .conda-env\python.exe
echo.
pause

:end
endlocal
