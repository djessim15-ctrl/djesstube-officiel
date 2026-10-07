@echo off
title DjessTube
cd /d "%~dp0"
start "" http://localhost:8765
python server.py
pause
