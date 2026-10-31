@echo off
REM 启动本地开发用浏览器 sidecar（首次会构建镜像，需要下载 Chromium，数分钟）。
docker compose -f "%~dp0docker-compose.yml" up -d --build
if errorlevel 1 (
  echo [browser] 启动失败：确认 Docker Desktop 已运行。
  exit /b 1
)
echo [browser] sidecar 已启动：http://127.0.0.1:8101/healthz
