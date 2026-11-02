# 一键部署：Linux 脚本内核 + Windows WSL 皮

日期：2026-11-01　　前置共识：grill-with-docs 逐条拍板　　相关：ADR-0013 / ADR-0034 / ADR-0035、CONTEXT.md「部署根」「现场产物」「同路径挂载」「build-output」

## 1. 问题陈述

发布一次 zerocode 到演示机，今天要在两台机器上手敲七八条命令：开发机跑 `build-output.bat`，
`scp` 上传，再照 `README-deploy.md` §4 的**五步分目录 rsync**逐条敲，最后服务器上 `bash deploy.sh`
+ `bash apply-init.sh`。README 用一整张表 warn「一条 `rsync -a --delete` 会把现场产物全删掉」——
这张表本身就是痛点证据：最危险的一步（把新包合进已有部署根）完全靠人记住哪八项不能碰。

缺的三件事：

- **环境保障缺位**。`scripts/deploy.sh` 已经很完整（预检、腾端口、起栈、等 healthy、重放 init.sql、
  自检），但它**碰到没有 Docker 就直接 fail**，参数也全靠事前手填 `.env`，不会问。
- **升级没有护栏**。护栏的语义只写在文档里，不在代码里，所以每次发布都是一次人肉回归。
- **Windows 没有落点**。全仓库唯一的 PowerShell 是 `gateway/make-cert.ps1`（只签证书）。

## 2. 方案概述

一个部署内核，两个入口。

- `scripts/deploy.sh` 是唯一的部署内核：纯交互式收集参数 → 保障环境 → 按现场产物护栏把包合进
  部署根 → 起栈 → 重放 init.sql → 自检。**取消全部命令行参数**（`--check/--no-build/--gateway/
  --takeover/--fresh-db` 一并去掉），危险动作改由现场提问 + 手打 `yes` 承担。可桩测的判定逻辑
  抽进 `scripts/deploy_lib.sh`，由 `deploy.sh` source——这是本规格最重要的接缝。
- `scripts/deploy.ps1` 是 WSL 皮：只保证 WSL2 与发行版可用、把包推进发行版，然后在发行版里执行
  **同一份** `deploy.sh`。不装 Docker Desktop（ADR-0035）。
- 交付包里两份脚本都在，`build_output.py` 从 `scripts/` 原样搬运，缺任一文件不出包。

唯一的新运行期概念是**部署根变量化**（ADR-0034）：`.env` 重新提供 `DEPLOY_ROOT`，compose 挂载写
`${DEPLOY_ROOT}/…`，镜像 `WORKDIR` 改 `ARG DEPLOY_ROOT` 由 `build.args` 从同一份 `.env` 取值，
init.sql 里预置应用的 `root_path` 写成 `{{DEPLOY_ROOT}}/apps/<slug>` 由 `apply-init.sh` 执行前渲染。
同路径挂载的语义不变，变的只是取值时机。

## 3. 用户故事

### 入场与模式判定

- **US-01**　作为部署者，我在包目录下执行 `bash deploy.sh`，脚本立刻播报它的身份：包版本生成时间、
  它自己所在位置、当前是否已在部署根内。任何一步都能在参数提问之前失败，因为那时我还没输入过东西。
- **US-02**　作为部署者，脚本第一步只问一件事：这次是**新增部署**还是**升级部署**。我选错了不会触发
  额外拦截（已定决策 D-05），但见 US-14 的删卷提问。
- **US-03**　作为部署者，脚本对 `deploy.sh` 与 `deploy_lib.sh` 各做一次 CRLF 自检，命中就给出
  `sed -i 's/\r$//'` 的确切修法并退出（沿用现有行为）。

### 参数收集（纯交互，无命令行参数）

- **US-04**　脚本按顺序问：部署根路径（默认 `/opt/zerocode`）、`POSTGRES_USER`、`POSTGRES_PASSWORD`、
  控制台端口（默认 8100）、`PG_PORT`（默认 5433）、预览展示地址 `ZEROCODE_PREVIEW_HOST`（默认值从本机
  网卡自动探测一个 IP 供我直接回车）、三个依赖镜像源（apt/pip/npm，默认沿用包内烘进的值）、
  要不要起中央网关（要则追问 `ZEROCODE_GATEWAY_DOMAIN` 与 `ZEROCODE_SSL_ENABLE`）。
- **US-05**　脚本对口令做现场校验：**只允许字母、数字、`-` `_`**。含 `@ : / # % & ?` 会被原样拼进
  asyncpg 连接串并截断——这条约束今天只写在 `.env.example` 注释里，现在变成闸门。
- **US-06**　脚本对部署根做规范化校验（沿用 `normalize_deploy_root` 的口径：必须 Linux 绝对路径、
  收敛重复斜杠、不许是 `/`），输入 `D:\zerocode` 时明确告诉我：Windows 侧要填的是 WSL 发行版内部的
  Linux 路径，填 `D:\` 等价于放进跨系统 9P 慢盘。
- **US-07**　我回答过的非敏感项落盘为部署根下的 `.deploy-answers`（`KEY=VALUE`，**不含口令**）；
  下次运行先读它当默认值。升级部署时我应当一路回车就能复用上次的配置。
- **US-08**　脚本把参数写进部署根的 `.env`：**动笔前先把旧文件拷成 `.env.bak-<UTC 时间戳>`**
  （只保留最近 5 份），完成时打印一行备份位置。写的是合并结果，不是覆盖——`.env` 里我没被问到的
  既有键必须原样保留（已定决策 D-06）。
- **US-09**　`.deploy-answers` 与 `.env` 同性质，属现场产物：升级合并永不删除或覆盖它（见 US-16）。

### 环境保障

- **US-10**　脚本检测 `docker` CLI、`docker compose` v2 插件、daemon 可达、`/var/run/docker.sock`
  存在。缺任一项不再直接 fail，而是进入安装分支：按 `apt`/`dnf`/`yum` 分派装 docker-ce 与
  docker-compose-plugin（含 `ca-certificates curl gnupg` 前置），`systemctl enable --now docker`，
  并把当前用户加进 `docker` 组（提示：组变更要重新登录或 `sg docker` 才生效）。
- **US-11**　安装分支开始前，脚本问一次是否配 registry mirror；要就写 `/etc/docker/daemon.json`
  （**已存在的 JSON 合并而不是覆盖**）并重启 docker。
- **US-12**　`up` 之前先 `docker compose pull` 预热基础镜像（`pgvector/pgvector:pg16` 等），
  哪个拉不动就当场指名镜像名与该镜像的可达性排查建议，而不是让 `build` 卡在半路没动静。
- **US-13**　需要 root 而当前不是 root 时，脚本自动 `sudo` 重跑自己；遇到必须重启才生效的环节
  （WSL2 组件刚启用、虚拟化未开）时**不自动 reboot**，而是打印「重启后把同一条命令再跑一次」，
  并保证重跑时已答项走默认值（幂等续跑）。

### 落盘与升级护栏

- **US-14**　选「新增」且检测到 `pgdata` 卷已存在时，脚本删卷前要求现场手打 `yes`；输入不是 `yes`
  就中止且此刻尚未对系统做任何改动。这取代原来的 `--fresh-db`。
- **US-15**　选「新增」时，脚本 `mkdir -p` 出 `apps/ data/ store/ gateway/certs/ gateway/routes/`，
  把包内容铺进部署根。若我输入的部署根不等于包所在目录，脚本负责整目录搬运（`rsync -a` 不带
  `--delete`），并要求重跑 `up -d --build`。
- **US-16**　选「升级」时，脚本按现场产物清单执行分目录合并，**八项一律不碰**：`.env`、`apps/`、
  `data/`、`store/`、`gateway/certs/`、`gateway/routes/`、`gateway/.env`、`pgdata` 卷
  （外加 `.deploy-answers`）。只有 `backend/`、`browser/`、`frontend/` 三个纯源码目录带 `--delete`
  镜像；顶层 `docker-compose.yml`、`init.sql`、`apply-init.sh`、`deploy.sh`、`deploy_lib.sh`、
  `deploy.ps1`、`.dockerignore` 只覆盖不删。合并前把这套动作打印成一张「会动什么 / 不会动什么」的表。
- **US-17**　选「升级」时脚本不猜 `gateway/docker-compose.yml` 的结构，只覆盖配置文件；
  网关容器要不要重建由我现场回答。

### 起栈与验收

- **US-18**　脚本比较本次输入的部署根与 `.env` 里既有 `DEPLOY_ROOT`：**不一致时强制 `--build`**，
  并在提问里预告这一点。只 `up -d` 不重建会让镜像 `WORKDIR` 停在旧根，症状正是 ADR-0013 警告的
  「新建应用落进容器可写层」。
- **US-19**　`up -d --build` 后，脚本等 `zerocode-pg16` healthy、等控制台回 200，超时就把
  `docker compose logs <服务> --tail 100` 的确切命令摆在我面前。
- **US-20**　`apply-init.sh` 在执行前把 init.sql 里的 `{{DEPLOY_ROOT}}` 渲染成 `.env` 的值，
  渲染结果**不落地成包内文件**（管道进 psql），保持「包内文件与源一致」可判定。
- **US-21**　自检沿用并补齐现有六道：`ZEROCODE_PREVIEW_PROBE_HOST` 在容器里存在且
  `host.docker.internal` 可解析、sidecar 的 `ZEROCODE_BROWSER_URL` 已注入且从后端容器实探 `/healthz`
  通过、compose 声明了 `browser` 服务、四个运行参数回显、`.env` 与容器内值一致告警。
- **US-22**　结束时打印：控制台地址、预览地址基座与端口段（8210-8399，含「宿主防火墙/安全组要放行」
  一句）、起预览的首次耗时预期、「第一个注册的账号即创始人」的提醒、以及本次 `.env` 备份位置。

### Windows 皮

- **US-23**　作为 Windows 部署者，我双击或在 PowerShell 里跑 `deploy.ps1`，它依次保证：WSL 已启用
  且为 v2、有可用发行版（没有就 `wsl --install -d Ubuntu-24.04` 并提示重启后续跑）、发行版内
  `/etc/wsl.conf` 开了 systemd（写过后提示 `wsl --shutdown` 续跑）。
- **US-24**　`deploy.ps1` 问我把包放在哪（zip 或目录，默认脚本同目录的包），把它推进发行版的
  目标路径后，在发行版内执行 `bash deploy.sh`，并把子进程输出原样透传（含提问）。
- **US-25**　Windows 侧默认建议**不起中央网关**并解释原因（宿主 80/443 常被占、通配 DNS 指不到本机），
  但我坚持要时不硬拦（ADR-0035 后果条）。
- **US-26**　`deploy.ps1` 以 UTF-8 **带 BOM** 落盘且行尾为 LF：Windows PowerShell 5.1 在 GBK 代码页下
  读无 BOM 的中文脚本会解析失败。搬运规则与 `deploy.sh` 同口径（剥 CRLF、缺文件不出包）。

## 4. 已定决策

| 编号 | 决策 | 出处 |
|---|---|---|
| D-01 | Windows 落点 = PowerShell 皮 + WSL 发行版内跑同一份 Linux 脚本，不装 Docker Desktop | ADR-0035 |
| D-02 | 一键的边界只到目标机，不含打包与跨机传输 | 拷问 Q2 |
| D-03 | 部署根改为部署期输入，compose `${DEPLOY_ROOT}` + Dockerfile `ARG` + init.sql 占位符 | ADR-0034 |
| D-04 | 预置应用 `root_path` 由打包器写占位符、部署时渲染（否决「直接改写 init.sql」与「换根禁用预置」） | ADR-0034 |
| D-05 | 新增/升级由**开局直接问用户**判定，不做自动探测，**不加误选拦截** | 拷问 Q5 / Q7 |
| D-06 | 升级护栏 = 八项现场产物全保 + 仅 `backend/browser/frontend` 带 `--delete` | 拷问 Q6，语义源自 README §4 |
| D-07 | 唯一的后路是写 `.env` 前静默备份 `.env.bak-<时间戳>`，不提问、不阻断 | 拷问 Q8 |
| D-08 | 参数分层提问（必问 7 组 + 回车取默认），回答落盘 `.deploy-answers` 供下次复用 | 拷问 Q9 |
| D-09 | Linux 侧装到「Docker Engine + 前置包 + 可选 registry mirror + 预热 pull」三层 | 拷问 Q10 |
| D-10 | 能抬权就抬，要重启就停下提示续跑，不自动 reboot | 拷问 Q12 |
| D-11 | 纯交互，取消所有命令行参数 | 拷问 Q14 |
| D-12 | 脚本原件住 `scripts/`，由 `build_output.py` 搬进 `build-output/`；不新建仓库根 `output/` | 拷问 Q11 |

术语按 CONTEXT.md 走：部署根、现场产物、同路径挂载、预置应用、build-output。

## 5. 测试决策

**接缝 A（最高接缝，覆盖本规格一半以上的改动）：离线打包断言**。沿用 `tests/test_build_output.py`
的 `fake_repo` + `_build()` 夹具——它不连数据库、不碰 docker、不跑 npm。新增用例：

- compose 里三处挂载与 `ZEROCODE_DATA_DIR`/`ZEROCODE_GATEWAY_ROUTES` 都取 `${DEPLOY_ROOT:-/opt/zerocode}`；
- `backend/Dockerfile` 含 `ARG DEPLOY_ROOT` 且 `WORKDIR ${DEPLOY_ROOT}`，compose 的 `build.args` 把
  同一个值喂进去（两边取自同一变量是 ADR-0034 的唯一安全前提）；
- init.sql 里预置应用 `root_path` 是 `{{DEPLOY_ROOT}}/apps/<slug>` 字面量，**绝不出现真实宿主路径**；
  重放安全审计（`audit_replay_safety`）不许把占位符判成残留标记；
- `deploy.ps1` 与 `deploy_lib.sh` 的搬运：缺任一文件抛 `BuildError`；`.ps1` 带 UTF-8 BOM 且无 CRLF；
  沿用 `test_generated_text_files_use_lf` 的清单式断言把两个新文件加进去。

**接缝 B：`deploy_lib.sh` 的桩测矩阵**。用 `bash` 起子进程、PATH 前置桩目录（`docker`/`ss`/`apt-get`/
`systemctl`/`curl`/`wsl`），覆盖场景：缺 Docker / 缺 compose 插件 / daemon 不可达 / 端口被占（占用者是
容器 vs 宿主进程）/ 口令含非法字符 / 部署根是相对路径 / 升级合并的「会动什么」表 / `.env` 合并保留既有键
/ `DEPLOY_ROOT` 变化触发强制重建 / 应答文件读回当默认值。**没有 `bash` 时整组 skip 并在交付说明里写明未验证**。

**接缝 C：现有「回归钉」文本断言继续留**。`busy()` 不许挂 `|| true`、sidecar 两道闸门关键字
（`grep -qx browser`、`ZEROCODE_BROWSER_URL`、`/healthz`、`docker exec -e ZCB_URL`）——它们钉的是
**已实撞过的事故**，重构脚本时最容易顺手删掉。

**接缝 D：PowerShell 静态校验**。有 `pwsh` 就跑 AST 解析（`[System.Management.Automation.Language.Parser]`）
+ 关键判定断言；没有就只做编码/行尾/关键字断言，并在交付说明里写明未验证。

**不在本次的测试**：真容器实跑。桩测跑尽逻辑后，实跑由部署者放行，放行前先确认本机 dev 预览栈
（占着 8100/5433）能否停。

**完成长这样**：接缝 A/B/C 全绿；`README-deploy.md` §2 与 §4 改写为「`bash deploy.sh` 一条命令」；
`scripts/deploy.ps1` 存在于包内；桩测矩阵的场景清单逐条有对应用例名可指。

## 6. 明确不做

- 不做跨机传输与端到端远程部署（无 SSH/scp/凭据处理，D-02）。
- 不做 Windows 原生容器、不做宿主裸跑平台形态（D-01）。
- 不装 Docker Desktop；不引入 WSL 之外的 Windows 依赖。
- 不产出离线镜像 tar；`docker compose pull` 只做预热，不做 air-gap 交付。
- 不改 `init.sql` 的「只补不覆盖」重放语义，也不把 `changelog/` 烘进镜像（ADR-0014 不变）。
- 不动平台业务代码、不动网关 nginx 逻辑与 `routes.json` 契约。
- 不引入任何命令行参数（D-11）。
- 不做自动 reboot、不自动 kill 宿主进程、不自动 stop 非 zerocode 的容器（现场提问后才动）。

## 7. 补充说明

- **实测到的一处既存缺陷**：`build-output/deploy.sh` 比 `scripts/deploy.sh` 落后——包里那份缺
  browser sidecar 的两道闸门（2026-11-01 演示机实撞过的那件事，见 `test_repo_deploy_script_gates_the_browser_sidecar`
  的注释）。包是 2026-10-31T16:31Z 那次打包的拷贝。本次改动落地后重跑打包即消除，但它证明
  「包里脚本 = 仓库脚本」只是约定，接缝 A 的搬运断言要守住。
- **取消参数的连带账**：README 排障表里「`deploy.sh --takeover` 会 stop 掉占端口的旧容器」这类文案
  要同步改写；D-11 的代价是批量部署三台机器要答三遍题（可用 `.deploy-answers` 缓解，但那是默认值
  复用、不是无人值守）。`build-output.bat` 的 `--no-pause` 教训说明自动化口子一旦没有就会被绕过——
  将来真需要非交互时，别在 `deploy.sh` 里加 flag，给它一个 `--answers` 文件重放入口。
- **两处未验证的技术赌注**（实跑前要当假设对待）：WSL 发行版内 `systemd` 拉起 docker daemon 的
  可用性；`/var/run/docker.sock` 与 external 网络 `zcnet` 在 WSL2 里的行为。桩测只能证明逻辑分支，
  不能证明这两条。
- **换部署根的真实代价**：不是重跑打包，而是 `up -d --build` 重建平台镜像（`WORKDIR` 变了）；
  服务器上跑过之后换根还需要人工确认 `apps/` 里既有工程的 `root_path` 与新根一致。
- **境内网络的既有事实**：基础镜像与应用构建期依赖都可能被 Docker Hub / PyPI 直连拖死，US-11/US-12
  是为这一条服务；域名未备案时 `ZEROCODE_SSL_ENABLE=false` 走 IP 直连的机制已存在，本规格不动它。
- 交付包目录是生成物（每次原子重建），任何"把脚本手放进 `build-output/`"的做法下次打包即丢——
  这就是 D-12 的由来。
