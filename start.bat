@echo off
REM ============================================================
REM  BinanceCloudBot - Script de pornire
REM  Inlocuieste valorile de mai jos cu credentialele tale:
REM    TELEGRAM_TOKEN  = tokenul de la @BotFather
REM    TELEGRAM_CHAT_ID = ID-ul tau de chat Telegram
REM ============================================================

set TELEGRAM_TOKEN=PUNE_TOKENUL_TAU_AICI
set TELEGRAM_CHAT_ID=PUNE_CHAT_ID_UL_TAU_AICI

cd /d "%~dp0"
python scanner.py
pause
