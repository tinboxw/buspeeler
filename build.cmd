@echo off
setlocal
if defined BUSPEELER_PYTHON (
  "%BUSPEELER_PYTHON%" "%~dp0scripts\build.py" %*
) else if exist "D:\dev-cache\buspeeler\shared\python312\Scripts\python.exe" (
  "D:\dev-cache\buspeeler\shared\python312\Scripts\python.exe" "%~dp0scripts\build.py" %*
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 "%~dp0scripts\build.py" %*
  ) else (
    python "%~dp0scripts\build.py" %*
  )
)
exit /b %errorlevel%
