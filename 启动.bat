@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:ELECTRON_RUN_AS_NODE=$null; Start-Process -FilePath '.\node_modules\electron\dist\electron.exe' -ArgumentList '.'"
