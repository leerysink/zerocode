# 0072 部署包内置镜像快照，安装期免拉取

- 状态：Accepted
- 日期：2026-11-26
- 关联：CONTEXT「镜像包」、ADR-0013（容器化交付）、ADR-0066（沙箱镜像显式 build）

## 背景

境内演示机与客户机上 `deploy.sh` 的安装耗时大头是镜像拉取：第 7 步预热 4 个 Dockerfile 的
FROM（`python:3.12-slim`、`python:3.12-slim-trixie`）+ `compose pull`（`pgvector/pgvector:pg16`），
拉不动再配加速站重拉；网关的 `openresty/openresty:alpine` 甚至不在预热名单，第 9 步起网关时才拉；
平台首启还要用 `postgres:16-alpine` 构建 `zerocode-sqlrunner`。Docker Hub 直连不可达、加速站
时好时坏，安装常常卡在这一段。

目标机**能联网但慢/不稳**，不是断网交付：构建期 pip/npm/apt/Playwright 仍可在线装，本次只需要
把「安装期镜像拉取」这一步从部署机挪到打包机。

## 决策

1. **镜像范围固定五个**（平台安装全路径）：`pgvector/pgvector:pg16`、`python:3.12-slim`、
   `python:3.12-slim-trixie`、`openresty/openresty:alpine`、`postgres:16-alpine`。
   应用模板构建镜像（node/maven/jdk/redis）**不进包**。
2. **打包默认产出** `images/`（gzip tar + manifest）；`--no-images` 出 3MB 小包；
   `--refresh-images` 先把五个镜像拉新再存；缺镜像时自动经 `docker.m.daocloud.io` 拉取并回
   官方 tag；有本地快照时默认不再联网（可复现优先）。
3. **部署端先 load 后免拉**：`deploy.sh` 在第 7 步前 `docker load`；manifest 里镜像 ID 与本地
   全一致才跳过 load；load 失败 fail-fast；load 成功后彻底跳过联网预热与 compose pull。
4. **tag 与 compose/Dockerfile 字面一致**：重复 load 覆盖同 tag 旧快照，升级包即更新镜像；
   镜像新鲜度责任在打包端。
5. **manifest 由打包器生成、部署端只读**：避免 save 名单与 load 校验名单两处硬编码漂移。
6. `images/` 排除出 docker build 上下文（.dockerignore），否则每次 build 上传约 1.6GB 上下文。

## 备选方案

- **只包 pg**：省约 250MB，但首启 sqlrunner 与网关仍在线拉，安装期仍有卡点。被否。
- **连应用模板镜像一起包**：包近 1.2GB，且应用构建仍要联网装 npm/maven 依赖，收益小。
  被否；翻案条件：客户要求断网建应用。
- **内网 registry 分发**：目标机无现成 registry，多一层运维。被否。
- **load 后仍 pull 校验更新**：慢网下回到卡顿体验。被否；新鲜度用打包端 `--refresh-images` 解决。
- **完全离线（含构建期依赖）**：超出本次范围；翻案条件：出现无外网交付需求。

## 后果

- 默认包从 3MB 涨到约 450MB（gzip）；`--no-images` 保留小包路径。
- 服务器镜像内容等于打包机快照，不再由部署机从 registry 取；`pg16` 浮动 tag 的新鲜度靠
  `--refresh-images` 或打包前手工 pull 保证。
- 镜像不经 registry digest 校验（docker save/load 保真层内容），信任边界是发布包本身。
- `README-deploy` 与 CONTEXT 中「build-output 不含镜像 tar」的旧口径已同步改为「默认含镜像包」。
