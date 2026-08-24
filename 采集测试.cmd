@echo off
REM ============================================================================
REM  Capture test launcher -- for the "pet window is frozen in the live feed" bug.
REM
REM  HOW TO USE: double-click, pick a mode, then set up the capture in Douyin
REM  Live Companion. While the capture preview is running, watch BOTH the real
REM  pet window on your desktop AND the preview inside the companion app.
REM
REM  The one observation that matters:
REM    - preview frozen BUT desktop pet still moving  -> capture side is at fault
REM    - both frozen at the same time                 -> our renderer stopped
REM  Everything else we do next depends on which of these two it is.
REM
REM  Mode 1 = transparent window (the normal one). This is the baseline. The
REM           anti-occlusion switches added on 2026-07-29 have never been tested
REM           against a real capture, so mode 1 may already work.
REM  Mode 2 = OPAQUE window on a solid green background. THIS IS THE INTENDED
REM           FIX, not just a diagnostic. Confirmed in the app on 2026-07-30:
REM             - window / process / game capture sources DO have chroma key
REM             - screen ("jie ping") capture does NOT
REM           Screen capture is the only source that renders the transparent pet
REM           as moving, but it cannot key the backdrop away -- and the sources
REM           that can key cannot capture a transparent window. Opaque + green
REM           + window capture + chroma key satisfies both sides at once.
REM           In the companion app: add this window as a WINDOW capture source,
REM           then turn on the green-screen (lv mu) key on that source.
REM  Mode 3 = transparent window + software rendering. Sometimes makes per-window
REM           capture work when GPU compositing is what blocks it.
REM  Mode 4 = OPAQUE + software rendering. TRY THIS ONE if mode 2 captures as a
REM           blank white rectangle. White usually means the capture could not
REM           read the window's GPU surface at all, so it fell back to a blank
REM           one. Software rendering makes the window paint into an ordinary
REM           bitmap that per-window capture can actually read, and opaque keeps
REM           the layered-window problem out of the way. Slightly more CPU.
REM
REM  Kept ASCII-only on purpose: the repo path contains Chinese characters and
REM  .cmd files break in confusing ways under different console codepages, so
REM  every path is resolved at runtime from this script's own location.
REM ============================================================================
setlocal EnableExtensions

set "APPDIR=%~dp0apps\character"
set "ELECTRON=%APPDIR%\node_modules\.bin\electron.cmd"

if not exist "%ELECTRON%" goto no_electron

echo.
echo   Capture test -- pick a window mode:
echo.
echo     1^) transparent            (current default; baseline)
echo     2^) opaque + green         (window capture + green-screen key^)
echo     3^) transparent, no GPU    (software rendering)
echo     4^) opaque + green, no GPU (try this if 2 captures as blank white^)
echo.
set "MODE="
set /p MODE=Enter 1, 2, 3 or 4 (then press Enter):

cd /d "%APPDIR%"

if "%MODE%"=="2" goto mode_opaque
if "%MODE%"=="3" goto mode_nogpu
if "%MODE%"=="4" goto mode_opaque_nogpu

echo.
echo [mode 1] transparent window, GPU on. Baseline run.
call "%ELECTRON%" .
goto done

:mode_opaque
echo.
echo [mode 2] OPAQUE window, solid green background.
echo          In the companion app: add this as a WINDOW capture source
echo          (not screen capture), then enable the green-screen key on it.
echo          If the window source still freezes, try process/game capture --
echo          those have the chroma key too.
echo          Another backdrop color can be set first, e.g.
echo            set PET_CHROMA=#FF00FF
set "PET_OPAQUE=1"
call "%ELECTRON%" .
goto done

:mode_nogpu
echo.
echo [mode 3] transparent window, software rendering (no GPU).
call "%ELECTRON%" . --no-gpu
goto done

:mode_opaque_nogpu
echo.
echo [mode 4] OPAQUE + green, software rendering (no GPU).
echo          Best bet when mode 2 shows up as a blank white rectangle.
echo          Add it as a WINDOW capture source, then enable the green key.
set "PET_OPAQUE=1"
call "%ELECTRON%" . --no-gpu
goto done

:no_electron
echo.
echo Electron not found: %ELECTRON%
echo Run "npm install" inside apps\character first.
pause
exit /b 1

:done
echo.
echo Pet exited. The log recorded which mode ran -- look for a line starting
echo with "pet:" in:
echo   apps\character\startup.log
pause
