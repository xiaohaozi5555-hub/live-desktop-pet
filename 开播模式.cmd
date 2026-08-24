@echo off
REM ============================================================================
REM  Live-stream mode launcher.
REM
REM  Right-click this file -> "Run as administrator", or just double-click it
REM  (it will ask for elevation by itself). It starts, in order:
REM     1) the barrage grabber (DouyinBarrageGrab / WssBarrageServer.exe)
REM     2) the desktop pet, with PET_GRAB_RECORD=1 so that the recorder service
REM        runs and the env-guard snapshots/restores the root cert + system proxy
REM
REM  Administrator rights are REQUIRED for both: installing the machine-level
REM  root certificate needs them, and so does DELETING it afterwards. Start the
REM  pet without elevation and the certificate will silently stay in your system.
REM
REM  Everyday use (no streaming) should keep using the normal shortcut instead;
REM  this launcher is only for when you actually go live.
REM
REM  Kept ASCII-only on purpose: the repo path contains Chinese characters and
REM  .cmd files break in confusing ways under different console codepages, so
REM  every path is resolved at runtime from this script's own location.
REM ============================================================================
setlocal EnableExtensions

if not defined BARRAGE_GRAB_DIR set "BARRAGE_GRAB_DIR=%~dp0tools\BarrageGrab"
set "GRAB_DIR=%BARRAGE_GRAB_DIR%"
set "GRAB_EXE=%GRAB_DIR%\WssBarrageServer.exe"
set "ROOT=%~dp0"
set "APPDIR=%ROOT%apps\character"
set "ELECTRON=%APPDIR%\node_modules\.bin\electron.cmd"

REM ---- prerequisite check (also runnable standalone: pass "check" as argument) ----
set "PROBLEM="
if not exist "%GRAB_EXE%" set "PROBLEM=%PROBLEM% [missing] %GRAB_EXE%"
if not exist "%ELECTRON%" set "PROBLEM=%PROBLEM% [missing] %ELECTRON%"
if not exist "%ROOT%services\env-guard\guard.py" set "PROBLEM=%PROBLEM% [missing] env-guard"
if not exist "%ROOT%services\perception-danmaku\record_grab.py" set "PROBLEM=%PROBLEM% [missing] recorder"

if /i "%~1"=="check" (
  if defined PROBLEM (
    echo CHECK FAILED:%PROBLEM%
    exit /b 1
  )
  echo CHECK OK - grabber, electron, env-guard and recorder are all in place.
  exit /b 0
)

if defined PROBLEM (
  echo.
  echo Cannot start live mode, something is missing:
  echo    %PROBLEM%
  echo.
  pause
  exit /b 1
)

REM ---- elevate if we are not administrator ----
net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

echo ============================================================
echo  Live mode: starting barrage grabber + desktop pet
echo ============================================================
echo.

REM ---- 1) barrage grabber (skip if already running) ----
tasklist /fi "imagename eq WssBarrageServer.exe" 2>nul | find /i "WssBarrageServer.exe" >nul
if errorlevel 1 (
  echo [1/2] starting barrage grabber ...
  start "BarrageGrab" /d "%GRAB_DIR%" "%GRAB_EXE%"
) else (
  echo [1/2] barrage grabber already running, reusing it
  echo       NOTE: a running grabber keeps the config it started with. If you just
  echo             edited WssBarrageServer.exe.config, close that BarrageGrab window
  echo             and run this launcher again, or the change will NOT take effect.
)

REM ---- wait for its websocket port, up to ~20s ----
echo       waiting for port 8888 ...
set "READY="
for /l %%i in (1,1,20) do (
  if not defined READY (
    netstat -ano | find ":8888" | find "LISTENING" >nul && set "READY=1"
    if not defined READY ping -n 2 127.0.0.1 >nul
  )
)
if defined READY (
  echo       port 8888 is up
) else (
  echo       WARNING: port 8888 did not come up. The pet will still start, but it
  echo                will not receive any barrage until the grabber is running.
)

REM ---- 2) the pet, with the barrage feed + verifier + env-guard enabled ----
REM   PET_GRAB=1        -> the danmaku service: grabber -> bus (this is what makes the pet react)
REM   PET_GRAB_VERIFY=1 -> the verifier: records raw data and judges the pet's reactions.
REM   The verifier is temporary; once the open questions are answered you can drop it and
REM   keep only PET_GRAB=1. They are separate on purpose - the judge must not be the answerer.
REM
REM   PET_OPAQUE=1 + --no-gpu  -> GREEN SCREEN MODE. This is what makes the pet capturable.
REM   Verified 2026-07-30 end to end: opaque green window + "window capture" source +
REM   the companion app's green-screen key = the pet floats over the stream with a clean
REM   background. Both switches are ONE unit, do not keep just one:
REM     - transparent window   -> window capture freezes (layered window)
REM     - GPU compositing on   -> window capture reads a blank white rectangle
REM   Screen ("jie ping") capture can see a transparent pet, but that source type has no
REM   chroma key, so the backdrop can never be removed there. Hence this combination.
echo [2/2] starting desktop pet (green screen mode) with barrage feed + verifier ...
set "PET_GRAB=1"
set "PET_GRAB_VERIFY=1"
set "PET_OPAQUE=1"
cd /d "%APPDIR%"
start "Mowan" /min "%ELECTRON%" . --no-gpu

echo.
echo Done. Two things are running now:
echo   - a console window titled "BarrageGrab"  (do NOT close it while streaming)
echo   - the pet window + its control console
echo.
echo ============================================================
echo  GOING LIVE - do these IN THIS ORDER
echo ============================================================
echo.
echo  1. Wait until you actually see all three: the BarrageGrab console, the pet
echo     window, and the pet's control console.
echo.
echo  2. NOW open Douyin Live Companion (zhi bo ban lv). ORDER MATTERS: the grabber
echo     has to own the system proxy BEFORE the companion opens its barrage
echo     connection. Open the companion first and that connection goes out direct,
echo     where the grabber can never see it.
echo     (You no longer need to fully restart the companion -- the startup-argument
echo      injection is switched off on purpose, see liveCompanHookSwitch below.)
echo.
echo  3. In the companion, add the pet as a WINDOW capture source -- NOT screen
echo     capture -- then turn ON the green-screen key for that source. The pet sits
echo     on a green background on purpose and the key removes it. Screen capture
echo     has no green-screen key at all, so the backdrop can never come off there.
echo.
echo  4. Only if you want to talk to her: click "start voice recognition" in the
echo     control console.
echo.
echo  5. Go live.
echo.
echo  6. For the first few minutes, confirm barrage is really arriving: the pet
echo     reacts, or viewers appear in the console's viewer list. If nothing comes
echo     in, the console raises a stall banner by itself after 5 minutes.
echo.
echo  DO NOT use "move out of view" while streaming -- it freezes the captured
echo  image (root cause still unknown). Just leave the pet sitting on the desktop;
echo  that does not affect what viewers see.
echo.
echo ============================================================
echo  FINISHING UP - also in this order
echo ============================================================
echo.
echo  1. Click "manual stream end" in the control console and wait a few seconds.
echo     This is the ONLY thing that triggers the end-of-stream profile summary.
echo     Just closing the window skips it silently -- that is why the streamer
echo     profile stayed empty for the first several real streams.
echo.
echo  2. Close the pet's control console. THIS is what deletes the root
echo     certificate and restores your system proxy, so do not skip it.
echo.
echo  3. Close the "BarrageGrab" window LAST.
echo.
echo Recordings and the health report land in: .cache\grab\
echo.
pause
