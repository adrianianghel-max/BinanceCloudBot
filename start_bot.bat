@echo off
REM Launcher for BinanceCloudBot scanner.py
set TELEGRAM_TOKEN=6101964896:AAH8IYil0VDYS3mu-XX4xpbfGPAlni3OGCk
set TELEGRAM_CHAT_ID=1522064262
cd /d "%~dp0"
"C:\Users\adria\AppData\Local\Programs\Python\Python312\python.exe" scanner.py
pause