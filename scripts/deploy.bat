@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  deploy.bat -- double-click entry for the Windows deploy shell.
REM
REM  It forwards and nothing else. deploy.ps1 right next to it is the
REM  WSL2 skin (ADR-0035): it owns the WSL2 / distro / systemd checks,
REM  pushes the bundle into the distro and runs the same deploy.sh that
REM  a Linux server runs. Nothing is decided in this file on purpose --
REM  a second copy of the logic is how two entry points start to drift.
REM
REM  Why a bat exists at all: Explorer does not run a .ps1 on double
REM  click (it opens it in an editor), and the people this entry is for
REM  are the ones who never open a terminal. Spec US-23 promises
REM  "double-click or PowerShell", and this file is the double-click.
REM
REM  Keep this file ASCII-only -- same rule as build-output.bat: under
REM  cp65001 cmd.exe loses byte-sync on multi-byte lines of the running
REM  batch file and turns echo text into bogus commands. Every Chinese
REM  message comes from deploy.ps1 instead.
REM
REM  Usage:
REM    deploy.bat              interactive; the window waits for a key
REM                            so a double-clicked run can be read
REM    deploy.bat --no-pause    for scripted runs: never wait for a key
REM    every other argument is forwarded to deploy.ps1, which refuses
REM    flags on the spot (D-11) and prints where each old flag went.
REM
REM  Line endings must stay CRLF: cmd.exe cannot be trusted to resolve
REM  the goto labels below from an LF-only batch file. The packager
REM  (scripts\build_output.py) re-emits CRLF when it ships this file,
REM  so the source's endings never reach the bundle.
REM ============================================================

chcp 65001 >nul

set "DO_PAUSE=1"
set "PASS_ARGS="
:parse_args
REM Test %1 (not "%~1") for end-of-args and forward %1 verbatim -- the two
REM rules build-output.bat already follows, so a quoted empty argument still
REM reaches the child instead of swallowing everything after it.
if "%1"=="" goto :args_done
if /i "%~1"=="--no-pause" (
    set "DO_PAUSE=0"
) else (
    set "PASS_ARGS=!PASS_ARGS! %1"
)
shift
goto :parse_args
:args_done

REM -NoProfile: a user profile must not redefine anything this run depends on.
REM -ExecutionPolicy Bypass: the default policy blocks unsigned scripts.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" !PASS_ARGS!
set "RC=!ERRORLEVEL!"

if "!DO_PAUSE!"=="1" pause
exit /b !RC!
