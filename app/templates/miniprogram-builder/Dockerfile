# ZeroCode 微信小程序构建器（ADR-0044 / 规格 2026-11-09）
#
# 只装「与具体应用无关」的工具链：Node、npm 镜像源、miniprogram-ci。
# uni-app 相关依赖在每个任务的工作区里按应用 package.json 的版本安装——
# 这样升级应用侧的 @dcloudio/uni-app 不需要重建本镜像，版本也不会悄悄分叉。
FROM node:20-slim

ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g miniprogram-ci@2 \
    && npm cache clean --force

COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

WORKDIR /workspace
ENTRYPOINT ["/bin/sh", "/app/run.sh"]
