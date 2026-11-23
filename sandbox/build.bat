@echo off
REM 构建本地开发用沙箱镜像（run_python / run_shell 的执行环境）。
REM 首次构建要装 pandas/numpy 等轮子，数分钟；基础镜像本地已有则不拉取。
docker build -t zerocode-sandbox:dev "%~dp0."
if errorlevel 1 (
  echo [sandbox] 构建失败：确认 Docker Desktop 已运行；网络受限时设 ZEROCODE_APT_MIRROR / ZEROCODE_PYPI_MIRROR 后重试。
  exit /b 1
)
echo [sandbox] 镜像已就绪：zerocode-sandbox:dev
echo [sandbox] 平台默认就用这个 tag；换 tag 设环境变量 ZEROCODE_SANDBOX_IMAGE。