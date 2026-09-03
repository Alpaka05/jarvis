@echo off
title Jarvis
echo %date% %time% gestartet von "%~f0" >> "%~dp0last_start.log"
cd /d "%~dp0.."
set "UV=uv"
where uv >nul 2>&1 || set "UV=%USERPROFILE%\.local\bin\uv.exe"
"%UV%" run python main.py
if errorlevel 1 pause
