# 0018 网关随包交付，平台与网关共享 routes 目录

交付包（`build-output/`）此前**不含 `gateway/`**：README 让运维「另行部署」网关与泛解析证书
（ADR-0016 只把域名参数化，范围没扩到打包）。真到一台新服务器上，这条「另行部署」实际上有两处
断链，第二处此前没有任何文档提到：

1. 包里没有网关配置，照 README 只跑 `docker compose up -d` 的话，控制台的
   `https://<slug>.<域名>` 永远 404。
2. 即使手工把仓库的 `gateway/` 传上去并起了容器，后端写路由表的落点是
   `<WORKDIR>/gateway/routes/routes.json`（`app/deploy/gateway_routes.py`），而平台 compose
   只挂了 `apps`、`data`、`store` 三个目录。于是写入落在 **backend 容器的可写层**：`mkdir -p`
   成功、文件写成功、日志干净，宿主上的网关却读不到；`docker compose up --build` 之后还会丢。

## 决定

1. **打包器复制网关配置**：`gateway/docker-compose.yml`、`nginx.conf`、`lua/`、`404.html`、
   `make-cert.{sh,ps1}` 进包；`gateway` compose 里
   `${ZEROCODE_GATEWAY_DOMAIN:-…}` 的 fallback 烘成本次打包域名，与平台 compose 的
   `${VAR:-打包值}` 同一副面孔。源文件里这个 token 必须恰好出现一次，否则拒包。
2. **私钥与运行期状态不入包**：`certs/`（TLS 私钥）与 `routes/routes.json`（运行期投影）
   一律剔除，但目录要建出来（放 `.gitkeep`），避免 Docker 以 root 属主自动创建挂载点。
   包内 `routes.json` 的初值是空对象 `{}`；证书在服务器现场签发。
3. **平台与网关共享 routes 目录**：平台 compose 把 `<部署根>/gateway/routes`
   **同路径挂进 backend**（并设 `ZEROCODE_GATEWAY_ROUTES`），网关继续作为独立 compose 项目，
   以只读方式挂同一个宿主目录（`./routes`）。发布/停机后路由自动生效，网关不必重启。
4. **README 补全部署链**：证书签发（生产 acme.sh / 内网 make-cert）、`docker compose -f
   gateway/docker-compose.yml up -d`、80/443 前置条件，以及每次发布时 gateway 的 rsync
   必须排除 `certs/`、`routes/`、`.env`。

## 被否决的方案

- **把 gateway 并进平台 compose**：两者生命周期不同——网关占用宿主 80/443、证书要独立续期，
  平台重建不该牵连网关；反过来网关重启也不该被平台 `up` 顺带做掉。名字、网络与上游
  （`host.docker.internal`）都与平台 compose 无关，合并只换来一个更脆的单文件。
- **把 `certs/` 打进包**：私钥随包流传等于把「冒充整个 `*.<域名>`」的能力发出去。服务器证书
  必须现场签发（acme.sh 或 make-cert），本机自签 CA 也不该被服务器沿用。
- **打包时带开发机的 `routes.json`**：开发机上 running 的应用端口不是服务器事实。带上它会把
  「404」换成「反代到错误端口」——后者在网关上看起来是通的，更难查。故初值固定为 `{}`。
- **只挂单个 `routes.json` 文件**：后端用 `.tmp + os.replace` 原子替换文件，单文件 bind mount
  在替换后仍钉着旧 inode，网关会一直读到第一次的内容。必须挂目录。
- **继续维持「另行部署」**：两处断链都是静默的（404 与日志无异常），而演示现场最贵的成本
  就是排查这种「看着都对」的故障。

## 后果

- 部署根的影响面多一处：`ZEROCODE_GATEWAY_ROUTES` 与 routes 挂载都按部署根烘死，换目录要重打包。
- 首次部署多两步：放证书、单独起网关；宿主 80/443 必须空闲，DNS 要有 `*.<域名>` 解析。
- 每次发布多一步 gateway 同步；`rsync --delete` 必须排除 `certs/`、`routes/`、`.env`，
  否则会删掉服务器私钥、运行期路由与现场域名覆盖。README 的「不能一条 rsync 搞定」表已补上这三行。
- 域名仍是两处 env：平台 `.env` 与 `gateway/.env`（可选，不建就用烘进 compose 的默认值）。
  两边不一致的症状仍是 ADR-0016 记过的「地址正常、点进去全 404」，现已在排障表里逐项列出。
- 出包终检「交付目录里不得有 `.env`」不变：`gateway/.env.example` 是模板，`gateway/.env`
  与开发机的本地覆盖都不进包。
