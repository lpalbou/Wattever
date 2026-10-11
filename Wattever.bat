@echo off
setlocal
cd /d "%~dp0"
title LLM GPU Benchmark

set "PY="
where py >nul 2>&1
if %errorlevel%==0 set "PY=py -3"
if not defined PY (
  where python >nul 2>&1
  if %errorlevel%==0 set "PY=python"
)
if not defined PY (
  echo.
  echo   Python 3 was not found on this machine.
  echo   Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
  echo   and run this file again. No other packages are needed.
  echo.
  pause
  exit /b 1
)

if not exist "corpora\agentic-ai-oct2026.full.md" (
  echo Building the benchmark corpus, one moment...
  %PY% tools\build_corpus.py
)

echo.
echo   Starting the benchmark server. Your browser will open shortly.
echo   Keep this window open while you benchmark; close it or press Ctrl+C to stop.
echo.
%PY% -m wattever.server
if %errorlevel% neq 0 (
  echo.
  echo   The server exited with an error. The message above says why.
  pause
)
endlocal
