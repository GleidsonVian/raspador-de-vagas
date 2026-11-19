@echo off
rem Executa uma busca completa (todas as fontes ativas) e grava o progresso em log.txt.
rem Use "executar_busca.bat auto" na tarefa agendada: sem pausa no final.
chcp 65001 >nul
cd /d "%~dp0"
echo ===== %date% %time% ===== >> log.txt
echo Busca iniciada. Nao feche a janela do Edge. Leva ~10 min.
powershell -NoProfile -Command "& python -u cacador.py 2>&1 | Tee-Object -FilePath log.txt -Append"
echo ===== TERMINOU %time% ===== >> log.txt
echo ===== TERMINOU =====
if /i not "%1"=="auto" pause
