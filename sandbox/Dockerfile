# zerocode 沙箱镜像 —— run_python / run_shell 的一次性执行环境（ADR-0056 / ADR-0066）。
# 无 docker.sock、无库凭据；非 root。工作区只读挂载、deploy 遮蔽、写只进 /scratch
# 这些围栏由平台侧 docker run 施加（app/agent/python_tools.py 的 _sandbox_argv），不在本文件。
#
# 基础镜像按发行版代号钉死（同 browser/Dockerfile 的理由）：apt 包名表按代号查表，
# 浮动 `python:3.12-slim` 换代就是构建期才炸的失败。
FROM python:3.12-slim-trixie

# 变量名与打包脚本/compose 的 ZEROCODE_* 同一套：打包时烘成 ARG 默认值，
# 服务器上还能用同名 .env 变量覆盖再 build。
ARG ZEROCODE_APT_MIRROR=
ARG ZEROCODE_PYPI_MIRROR=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    TMPDIR=/tmp

RUN set -eux; \
    if [ -n "$ZEROCODE_APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        [ -e "$f" ] || continue; \
        sed -i -e "s|deb.debian.org|$ZEROCODE_APT_MIRROR|g" -e "s|security.debian.org|$ZEROCODE_APT_MIRROR|g" "$f"; \
      done; \
    fi

# 常用 CLI 只能在这里进镜像：运行期以非 root（1000）跑，apt 拿不到 dpkg lock，
# 脚本里 `apt-get install` 必然失败（ADR-0066 决策 6）。新增系统工具要改本文件并重建。
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git curl zip unzip ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# 构建上下文是 sandbox/ 目录本身（同 browser/）：不把整个仓库（含 apps/、data/）送进 daemon。
COPY requirements.txt /tmp/requirements.txt
RUN set -eux; \
    if [ -n "$ZEROCODE_PYPI_MIRROR" ]; then pip config set global.index-url "$ZEROCODE_PYPI_MIRROR"; fi; \
    pip install --no-cache-dir -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt

RUN set -eux; \
    useradd --create-home --uid 1000 --shell /bin/sh sandbox; \
    mkdir -p /workspace /scratch
USER 1000
WORKDIR /workspace

# 平台每次都显式传 `python -c …` / `sh -c …`；这个 CMD 只服务手工探镜像。
CMD ["python3"]