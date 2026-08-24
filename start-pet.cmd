@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found under this folder.
  echo Run:  .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo (create the venv first with: python -m venv .venv)
  pause
  exit /b 1
)

echo ============================================
echo   Live Desktop Pet - one-click start
echo ============================================
echo.

echo [1/3] starting bus broker...
start "Pet-Broker" "scripts\win\run-broker.cmd"
timeout /t 1 /nobreak >nul

echo [2/3] starting brain (decision engine)...
start "Pet-Brain" "scripts\win\run-brain.cmd"
timeout /t 1 /nobreak >nul

echo [3/3] starting character window...
start "Pet-Character" "scripts\win\run-character.cmd"

echo.
echo 3 windows started: broker / brain / character.
echo Close a window to stop that component.
echo.
echo Optional perception modules (run manually as needed, each needs its
echo own dependencies/calibration - see README.md and HANDOFF history):
echo   .venv\Scripts\python.exe services\perception-game\run.py
echo   .venv\Scripts\python.exe services\perception-face\run.py
echo   .venv\Scripts\python.exe services\perception-voice\run.py
echo   .venv\Scripts\python.exe services\perception-danmaku\run.py
echo.
echo Optional dialogue service (LLM chat, needs PET_CHAT_* in .env - see .env.example):
echo   .venv\Scripts\python.exe services\dialogue\run.py
echo.
echo Control panel (optional, another window):
echo   .venv\Scripts\python.exe apps\control-panel\panel.py --gui
echo   .venv\Scripts\python.exe apps\control-panel\panel.py --keywords
echo.
pause
