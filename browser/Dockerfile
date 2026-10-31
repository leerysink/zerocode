# zerocode 浏览器 sidecar 镜像 —— 独立容器承载 headless Chromium（ADR-0031）。
# 无 docker.sock、无平台库凭据；非 root；运行期只读根文件系统（compose 侧约束）。
# 基础镜像按发行版代号钉死：playwright 的 `install --with-deps` 用 apt 包名表按代号查表，
# `python:3.12-slim` 是浮动 tag（今天=trixie），代号一变就出现「ttf-unifont 无候选包」这种
# 构建期才报错的失败。requirements.txt 里的 playwright 版本与这里的代号要一起升级。
FROM python:3.12-slim-trixie

# 变量名与打包脚本/compose 的 ZEROCODE_* 保持同一套：三级取值（打包参数 > 打包机 env >
# 默认）烘成 ARG 默认值之后，服务器上还能用同名 .env 变量覆盖再 build。
ARG ZEROCODE_APT_MIRROR=
ARG ZEROCODE_PYPI_MIRROR=
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST} \
    HOME=/tmp \
    TMPDIR=/tmp

RUN set -eux; \
    if [ -n "$ZEROCODE_APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        [ -e "$f" ] || continue; \
        sed -i -e "s|deb.debian.org|$ZEROCODE_APT_MIRROR|g" -e "s|security.debian.org|$ZEROCODE_APT_MIRROR|g" "$f"; \
      done; \
    fi

# 构建上下文是 browser/ 目录本身：本地 compose 与交付包 compose 都按这个口径传 context，
# 不用把整个仓库（含 apps/、data/）送进 daemon。
COPY requirements.txt /tmp/requirements.txt
RUN set -eux; \
    if [ -n "$ZEROCODE_PYPI_MIRROR" ]; then pip config set global.index-url "$ZEROCODE_PYPI_MIRROR"; fi; \
    pip install --no-cache-dir -r /tmp/requirements.txt

RUN set -eux; \
    playwright install --with-deps chromium; \
    rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin browser
USER 10001

COPY app.py inspect_core.py __init__.py /service/browser/
WORKDIR /service
EXPOSE 8101
CMD ["uvicorn", "browser.app:app", "--host", "0.0.0.0", "--port", "8101"]
