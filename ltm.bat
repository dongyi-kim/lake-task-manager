@echo off
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%\.venv\Scripts\python.exe" (set "PY=%ROOT%\.venv\Scripts\python.exe") else if exist "%ROOT%..\.venv\Scripts\python.exe" (set "PY=%ROOT%..\.venv\Scripts\python.exe") else (set "PY=python")
"%PY%" "%ROOT%tools\ltm_cli.py" %*
