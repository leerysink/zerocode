# 浏览器 sidecar（preview_page 的隔离运行环境）

平台 Agent 的 `preview_page` 通过这个独立容器打开应用 dev 预览页。它没有 docker.sock、
没有平台库凭据、以非 root 运行；运行期只读根文件系统（见 compose）。

## 本地开发

平台后端跑在宿主（`dev.bat`）时，先启动 sidecar：

```bat
browser\dev-up.bat
```

默认监听 `http://127.0.0.1:8101`，与 `app/agent/browser_tools.py` 的
`ZEROCODE_BROWSER_URL` 默认值一致。停止：

```bat
browser\dev-down.bat
```

首次构建要下载 Chromium（默认走 npmmirror），需要数分钟、约 1GB 磁盘。

基础镜像按发行版代号钉成 `python:3.12-slim-trixie`，而不是浮动的 `python:3.12-slim`：
`playwright install --with-deps` 的 apt 包名表按代号查，代号一换（`ttf-unifont` →
`fonts-unifont`）就是构建期才炸的失败。升级 `requirements.txt` 里的 playwright 时，
要确认它认识基础镜像那个代号，两处一起动。

## 交付部署

`scripts/build_output.py` 会把 `browser/` 打进交付包，平台 compose 内服务名 `browser`，
后端通过 `http://browser:8101` 调用。sidecar 不发布宿主机端口。构建上下文是本目录，
所以交付包的 compose 写的是 `context: ./browser`。

网络受限的服务器上，browser 镜像的构建期换源沿用打包时的 `ZEROCODE_APT_MIRROR` /
`ZEROCODE_PYPI_MIRROR`，Chromium 下载源用 `.env` 里的 `PLAYWRIGHT_DOWNLOAD_HOST`
（打包时烘进 build args 的默认值是 npmmirror），改完 `docker compose build browser`。

## 安全边界

- 只接受 host 命中 `BROWSER_ALLOWED_HOSTS`（默认 `host.docker.internal,127.0.0.1,localhost`）
  的 http(s) 地址；主文档只允许目标 origin，子资源阻断私网与容器地址。
- 每次检查新建 BrowserContext，不复用 cookie/登录态；不提供点击、输入、上传接口。
- 容器内 Chromium 关闭自带 sandbox（容器本身是隔离边界），这是 ADR-0031 记录过的取舍。
