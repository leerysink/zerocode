# 0072 部署包内置镜像快照，安装期免拉取

- 状态：Accepted（2026-11-27 修订：默认小包，`--images` 显式内置快照）
- 日期：2026-11-26
- 关联：CONTEXT「镜像包」、ADR-0013（容器化交付）、ADR-0034（部署根运行期输入）、ADR-0066（沙箱镜像显式 build）

## 背景

境内演示机与客户机上 `deploy.sh` 的安装耗时大头是镜像拉取：第 7 步预热 4 个 Dockerfile 的
FROM（`python:3.12-slim`、`python:3.12-slim-trixie`）+ `compose pull`（`pgvector/pgvector:pg16`），
拉不动再配加速站重拉；网关的 `openresty/openresty:alpine` 甚至不在预热名单，第 9 步起网关时才拉；
平台首启还要用 `postgres:16-alpine` 构建 `zerocode-sqlrunner`。Docker Hub 直连不可达、加速站
时好时坏，安装常常卡在这一段。

目标机**能联网但慢/不稳**，不是断网交付：构建期 pip/npm/apt/Playwright 仍可在线装，本次只需要
把「安装期镜像拉取」这一步从部署机挪到打包机。

**修订背景（2026-11-26 实撞）**：初版快照只含五个基础镜像，免「拉」不免「建」——起栈一律
`up -d --build`，新机器 buildkit 缓存为空，pip/apt/Playwright 全量构建仍落在部署机。2 vCPU/3.3G
的 VMware 演示机上实测约 2 小时（playwright 步 33 分钟、两个镜像层导出各 20+ 分钟；vmstat
运行队列 25+、iowait≈0，纯 CPU 饱和）。构建成本必须和拉取一样挪到打包机。

## 决策

1. **镜像范围＝五个基础＋四个构建目标**：基础名单不变（`pgvector/pgvector:pg16`、
   `python:3.12-slim`、`python:3.12-slim-trixie`、`openresty/openresty:alpine`、
   `postgres:16-alpine`）；构建目标 `zerocode-platform:demo`、`zerocode-browser:demo`、
   `zerocode-sandbox:demo`、`zerocode-sandbox-egress:demo` 由**打包机先 `compose build` 再
   save**（`build_service_images`），tag 与 compose 逐字同源。应用模板构建镜像
   （node/maven/jdk/redis）**不进包**。构建目标不在任何 registry，save 时只 inspect 不 pull，
   缺了当场拒包。
2. **打包默认产出约 3MB 小包**，`build-output.bat --images` 才产出 `images/`（gzip tar + manifest）；
   `--refresh-images` 须与 `--images` 同用，先把五个基础镜像拉新再存；缺基础镜像时自动经
   `docker.m.daocloud.io` 拉取并回官方 tag；有本地快照时默认不再联网（可复现优先）。服务镜像每次
   显式打包都 build（缓存命中时快），升级包的新代码由此进快照。
3. **部署端先 load 后免拉**：`deploy.sh` 在第 7 步前 `docker load`；manifest 里镜像 ID 与本地
   全一致才跳过 load；load 失败 fail-fast；load 成功后彻底跳过联网预热与 compose pull。
4. **同根免构建**（修订）：`zc_build_plan` 判定——快照就绪且部署根＝打包烘的根时 `up -d` 不带
   `--build`（升级的新代码经新快照 load 进来，compose 见镜像 ID 变化重建容器）；无快照或换根仍
   `up -d --build`（backend 镜像 WORKDIR 是构建期值，ADR-0034）。沙箱两镜像已含在快照，就绪即
   跳过显式 build；无快照时保留 ADR-0066 的显式 build。
5. **tag 与 compose/Dockerfile 字面一致**：重复 load 覆盖同 tag 旧快照，升级包即更新镜像；
   镜像新鲜度责任在打包端。
6. **manifest 由打包器生成、部署端只读**：避免 save 名单与 load 校验名单两处硬编码漂移。
7. `images/` 排除出 docker build 上下文（.dockerignore），否则每次 build 上传约 1.6GB 上下文。

## 备选方案

- **只包 pg**：省约 250MB，但首启 sqlrunner 与网关仍在线拉，安装期仍有卡点。被否。
- **连应用模板镜像一起包**：包近 1.2GB，且应用构建仍要联网装 npm/maven 依赖，收益小。
  被否；翻案条件：客户要求断网建应用。
- **内网 registry 分发**：目标机无现成 registry，多一层运维。被否。
- **load 后仍 pull 校验更新**：慢网下回到卡顿体验。被否；新鲜度用打包端 `--refresh-images` 解决。
- **完全离线（含构建期依赖）**：超出本次范围；翻案条件：出现无外网交付需求。
- **只扩 VM 配置不改交付链路**（修订时备选）：4C8G 能把全量 build 压到十几分钟，但每台弱机
  都要重复付构建成本，且客户机配置不受我们控制。被否；需要免拉取/免构建时显式用 `--images`。

## 后果

- 默认包约 3MB，不含镜像；显式 `--images` 才增至约 1GB+（gzip，含 Chromium 层）。
- 显式打包时构建成本只在打包机付一次；部署机安装＝load＋up，分钟级。打包机需有 docker 与前端 dist。
- 换部署根仍触发部署机全量重建（WORKDIR 构建期值）；弱机换根前应先扩配置或改走打包根。
- 内置快照时服务器镜像内容等于打包机快照，不再由部署机从 registry 取；`pg16` 浮动 tag 的新鲜度靠
  `--refresh-images` 或打包前手工 pull 保证。
- 镜像不经 registry digest 校验（docker save/load 保真层内容），信任边界是发布包本身。
- `README-deploy` 与 CONTEXT 中镜像包口径同步为「默认不含，`--images` 显式开启」。
