@echo off
setlocal
cd /d "%~dp0\.."
echo [INFO] Iniciando Creo Elements/Direct Modeling MCP Server...
call .\.venv\Scripts\activate.bat
set PYTHONPATH=src
python -m creo_mcp
pause
