@echo off
title DjessTube
cd /d "%~dp0"

REM --- Quel Python utiliser ? ---
set PY=python
where python >nul 2>nul
if errorlevel 1 set PY=py -3
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython

REM --- Flask et yt-dlp sont-ils installes ? ---
%PY% -c "import flask, yt_dlp" >nul 2>nul
if errorlevel 1 (
  echo.
  echo Premiere utilisation : installation de Flask et yt-dlp...
  %PY% -m pip install --upgrade pip >nul 2>nul
  %PY% -m pip install -U -r requirements.txt
  if errorlevel 1 goto pasdinstall
  echo Installation terminee.
  echo.
)

REM --- yt-dlp a jour ? (sinon YouTube casse le lecteur) ---
%PY% -m pip install -U yt-dlp >nul 2>nul

start "" http://localhost:8765
%PY% server.py
pause
exit /b

:nopython
echo.
echo Python n'est pas installe sur cet ordinateur.
echo 1. Va sur https://www.python.org/downloads/
echo 2. Installe Python en cochant "Add Python to PATH"
echo 3. Relance ce fichier
echo.
pause
exit /b

:pasdinstall
echo.
echo L'installation de Flask / yt-dlp a echoue.
echo Verifie ta connexion Internet, puis relance ce fichier.
echo.
pause
exit /b
