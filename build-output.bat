@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM  build-output.bat -- one-shot packaging of the demo deploy bundle
REM
REM  This bat is ONLY an entry point: it pins the console codepage,
REM  builds the frontend, then delegates. All real logic (init.sql
REM  synthesis, compose/Dockerfile rendering, preset app copying,
REM  replay-safety audit, port-conflict and credential checks) lives
REM  in scripts\build_output.py -- plain bat cannot handle UTF-8 JSON
REM  and Chinese text without mangling it.
REM
REM  NOTE: keep this file ASCII-only on purpose. Switching the console
REM  to cp65001 makes cmd.exe lose byte-sync on multi-byte lines in the
REM  running batch file, which splits echo text into bogus commands.
REM  Human-facing Chinese output comes from the Python script instead.
REM
REM  Usage: build-output.bat [args forwarded to scripts\build_output.py]
REM    build-output.bat                        platform only, no preset apps
REM    build-output.bat --slugs test1          preset a single app
REM    build-output.bat --slugs test1,sqlc002  ship test1 and sqlc002 as presets
REM    build-output.bat --deploy-root /srv/zc  different server deploy root
REM    build-output.bat --skip-frontend        reuse existing dist, skip npm run build
REM    build-output.bat --allow-stale-dist     accept a dist older than src
REM    build-output.bat --no-pause             for automation: never wait for a key
REM    build-output.bat --gateway-domain <domain>   gateway wildcard domain (env: ZEROCODE_GATEWAY_DOMAIN)
REM    build-output.bat --apt-mirror mirrors.aliyun.com   Debian + docker-ce mirror host (env: ZEROCODE_APT_MIRROR)
REM    build-output.bat --pypi-mirror https://mirrors.aliyun.com/pypi/simple   (env: ZEROCODE_PYPI_MIRROR)
REM    build-output.bat --npm-registry https://registry.npmmirror.com   (env: ZEROCODE_NPM_REGISTRY)
REM
REM  --no-pause matters more than it looks: every exit path ends in `pause`
REM  so a double-clicked window stays open long enough to read the Chinese
REM  summary. But `pause` blocks forever when the bat is driven by a script,
REM  a CI job or PowerShell (stdin is not a keyboard), which also hides the
REM  exit code. Anything non-interactive MUST pass --no-pause.
REM
REM  Redirecting this bat's output in PowerShell 5.1 shows mojibake: the
REM  child switches the console to cp65001 but PowerShell decodes the pipe
REM  with its own [Console]::OutputEncoding (cp936). Run it unredirected, or
REM  set [Console]::OutputEncoding = [Text.UTF8Encoding]::new() first.
REM ============================================================

REM The packager prints Chinese plus tick/cross marks. Under the default
REM cp936 console a redirected run dies with UnicodeEncodeError, so pin
REM both the console codepage and Python's stream encoding to UTF-8.
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "SKIP_FE=0"
set "DO_PAUSE=1"
set "PASS_ARGS="
:parse_args
REM Test %1 (not "%~1") for end-of-args: the second token of `--slugs ""`
REM is empty once quotes are stripped, and testing %~1 would swallow it
REM together with every remaining argument. Forwarding %1 verbatim is what
REM lets the empty string reach argparse.
if "%1"=="" goto :args_done
REM --skip-frontend and --no-pause are the bat's own switches: consuming them
REM here keeps argparse from seeing flags it does not know.
if /i "%~1"=="--skip-frontend" (
    set "SKIP_FE=1"
) else if /i "%~1"=="--no-pause" (
    set "DO_PAUSE=0"
) else (
    set "PASS_ARGS=!PASS_ARGS! %1"
)
shift
goto :parse_args
:args_done

REM ------------------------------------------------------------
REM 1. Python interpreter: prefer .venv, fall back to PATH.
REM    The packager uses stdlib only, so pip install is deliberately
REM    skipped here -- keeps the one-shot flow working offline.
REM ------------------------------------------------------------
set "PY=.venv\Scripts\python.exe"
if exist "!PY!" goto :py_ready
where python >nul 2>nul
if errorlevel 1 goto :py_missing
set "PY=python"
:py_ready

REM ------------------------------------------------------------
REM 2. Frontend build: dist must come from the current src.
REM    Shipping a stale dist is the number one cause of "features
REM    mysteriously missing" during the demo, so rebuild by default.
REM ------------------------------------------------------------
if "!SKIP_FE!"=="1" (
    echo [skip] --skip-frontend: reusing existing frontend\dist
    goto :fe_done
)
if exist "frontend\node_modules\vite" goto :fe_build
where npm >nul 2>nul
if errorlevel 1 goto :npm_missing
echo [setup] installing frontend dependencies: npm install ...
call npm install --prefix frontend
if errorlevel 1 goto :npm_failed
:fe_build
echo [build] building frontend: npm run build ...
pushd frontend
call npm run build
set "FE_RC=!ERRORLEVEL!"
popd
if not "!FE_RC!"=="0" goto :fe_failed
:fe_done

REM ------------------------------------------------------------
REM 3. Package: assemble the bundle behind fail-closed gates --
REM    replay-safety audit, idempotence audit, the bare-UPDATE
REM    disposition table, preset port conflicts, stale-dist check and
REM    the final credential scan. Any gate failure aborts with a
REM    Chinese reason and exit code 1.
REM
REM    A failure cannot damage an existing bundle: everything is first
REM    assembled in build-output.staging and only renamed into place
REM    once every gate has passed. On failure the staging dir is dropped
REM    and the previous build-output stays byte-identical -- which is
REM    what the promise below is allowed to say.
REM
REM    The rename itself is retried a few times first: Windows hands out
REM    transient ERROR_ACCESS_DENIED on directory renames while antivirus,
REM    the search indexer or an editor holding a file in build-output has a
REM    handle open. Failing the whole package over a millisecond-long lock
REM    would mean re-running it by hand right before a demo (seen in the
REM    wild). Only sharing/permission errors are retried; a full disk is
REM    reported immediately.
REM ------------------------------------------------------------
echo [pack]  generating build-output ...
"!PY!" scripts\build_output.py !PASS_ARGS!
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" goto :pack_failed

echo.
echo ============================================================
echo  Done. Server-side steps are printed above and documented in
echo  build-output\README-deploy.md
echo ============================================================
call :hold
exit /b 0

REM ------------------------------------------------------------
REM failure paths
REM ------------------------------------------------------------
:py_missing
echo [error] python not found in PATH. Install Python 3.11+ and retry,
echo         or run dev.bat once to create .venv.
call :hold
exit /b 1

:npm_missing
echo [error] npm not found in PATH. Install Node.js 18+ and retry;
echo         with a usable dist already present you can pass --skip-frontend.
call :hold
exit /b 1

:npm_failed
echo [error] npm install failed. Try manually: npm install --prefix frontend
call :hold
exit /b 1

:fe_failed
echo [error] frontend build failed with code !FE_RC!.
echo         Try manually: cd frontend ^&^& npm run build
call :hold
exit /b 1

:pack_failed
echo [error] packaging failed with code !RC!. The reason is printed above.
echo         Packaging assembles build-output.staging first and only renames it
echo         into place once every gate passes, so a gate failure leaves the
echo         previous build-output untouched. If -- and only if -- the message
echo         above says the rollback itself failed, the previous bundle is
echo         sitting in build-output.old and build-output does not exist.
call :hold
exit /b 1

REM ------------------------------------------------------------
REM :hold -- keep a double-clicked window open, but never block automation.
REM A bare `pause` waits on stdin forever when the bat is piped or driven by
REM a script, and the caller never sees the exit code. --no-pause opts out.
REM ------------------------------------------------------------
:hold
if "!DO_PAUSE!"=="1" pause
exit /b 0
