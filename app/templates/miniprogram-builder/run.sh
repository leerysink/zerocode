#!/bin/sh
# 小程序任务入口（ADR-0044）：应用副本已由平台叠加好，这里只负责装依赖 + 编译 + 上传/预览。
# 容器需要出网：npm 镜像源与 api.weixin.qq.com:443。
set -e

cd /workspace

npm install --no-audit --no-fund
npm run build:mp-weixin

if [ "$MP_MODE" = "upload" ]; then
  # 上传开发版：每次覆盖「版本管理」里的最新一份
  npx miniprogram-ci upload --pp . --pkp "$MP_KEY" --appid "$MP_APPID" \
    --uv "$MP_VERSION" --ud "$MP_DESC" --enable-es6 true
else
  # 预检/按需预览：产出二维码图片，平台负责搬运到不公开的数据区
  npx miniprogram-ci preview --pp . --pkp "$MP_KEY" --appid "$MP_APPID" \
    --uv "$MP_VERSION" --ud "$MP_DESC" --enable-es6 true \
    --qrcode-format image --qrcode-output-dest /workspace/preview.jpg
fi
