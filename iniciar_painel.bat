@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Painel do raspador de vagas: http://127.0.0.1:8765  (feche esta janela para encerrar)
python -m raspador
pause
