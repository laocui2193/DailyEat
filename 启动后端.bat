@echo off
chcp 65001 >nul
cd /d "%~dp0server"
C:\Users\asus\anaconda3\python.exe app.py
pause
