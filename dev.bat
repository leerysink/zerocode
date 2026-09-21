@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  zerocode dev startup   backend :8100  +  Vite dev :8082
echo ============================================================

REM ------------------------------------------------------------
REM 1. PostgreSQL container (host port 5433)
REM ------------------------------------------------------------
where docker >nul 2>nul
if errorlevel 1 goto :db_warn
docker start zerocode-pg16 >nul 2>nul
if not errorlevel 1 goto :db_ok
:db_warn
echo [warn] PostgreSQL container "zerocode-pg16" is not running.
echo        Start it yourself, or create it once with:
echo        docker run -d --name zerocode-pg16 -e POSTGRES_USER=zerocode -e POSTGRES_PASSWORD=zerocode123 -e POSTGRES_DB=zerocode -p 5433:5432 postgres:16-alpine
goto :db_done
:db_ok
echo [ok]   PostgreSQL container zerocode-pg16 is up
:db_done

REM ------------------------------------------------------------
REM 2. Backend environment - create .venv when missing, then
REM    install requirements (idempotent: no-op when satisfied)
REM ------------------------------------------------------------
if exist ".venv\Scripts\python.exe" goto :py_ready
where python >nul 2>nul
if errorlevel 1 goto :py_missing
echo [setup] creating .venv ...
python -m venv .venv
if errorlevel 1 goto :venv_missing
:py_ready
echo [setup] checking backend requirements ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q --disable-pip-version-check
if errorlevel 1 goto :pip_failed

REM ------------------------------------------------------------
REM 3. Frontend dependencies
REM ------------------------------------------------------------
if exist "frontend\node_modules\vite" goto :fe_ready
where npm >nul 2>nul
if errorlevel 1 goto :npm_missing
echo [setup] installing frontend dependencies (npm install) ...
call npm install --prefix frontend
if errorlevel 1 goto :npm_failed
:fe_ready

REM ------------------------------------------------------------
REM 4. Start backend in its own window.
REM    No --reload on purpose: the deploy suite writes generated
REM    app files under apps/, which would restart uvicorn constantly.
REM ------------------------------------------------------------
echo [run]   backend  http://127.0.0.1:8100   (window: zerocode-backend)
start "zerocode-backend" cmd /k .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100

REM ------------------------------------------------------------
REM 5. Start Vite dev server in this window; it proxies /api and
REM    /ws to :8100 (see frontend/vite.config.js).
REM ------------------------------------------------------------
echo [run]   frontend http://127.0.0.1:8082   (this window; Ctrl+C to stop)
echo         close the zerocode-backend window to stop the backend.
start /min "" cmd /c "timeout /t 5 >nul && start http://127.0.0.1:8082"
cd frontend
call npm run dev
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [error] Vite dev server exited with code %RC%
goto :end

REM ------------------------------------------------------------
REM failure paths
REM ------------------------------------------------------------
:py_missing
echo [error] python not found in PATH. Install Python 3.11+ and retry.
pause
exit /b 1

:venv_missing
echo [error] could not create .venv. Try manually: python -m venv .venv
pause
exit /b 1

:pip_failed
echo [error] pip install failed. Try manually:
echo         ".venv\Scripts\python.exe" -m pip install -r requirements.txt
pause
exit /b 1

:npm_missing
echo [error] npm not found in PATH. Install Node.js 18+ and retry.
pause
exit /b 1

:npm_failed
echo [error] npm install failed. Try manually: npm install --prefix frontend
pause
exit /b 1

:end
if not defined RC set "RC=0"
exit /b %RC%
