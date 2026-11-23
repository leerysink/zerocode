# 沙箱镜像（run_python 与 run_shell 的执行环境）

平台 Agent 的 `run_python`（任意 Python 脚本 / 技能母本脚本）与 `run_shell`（任意终端命令）
都在这个镜像起的**一次性容器**里跑（`docker run --rm`，跑完即销毁）。

**围栏不在本目录。** 工作区只读挂载、`deploy/` 遮蔽、无库凭据、无 docker.sock、非 root、
限资源、写只进 `/scratch` —— 这些由平台侧 `docker run` 参数施加，唯一出处是
`app/agent/python_tools.py` 的 `_sandbox_argv()`。本目录只负责「容器里有什么」。

## 本地开发

```bat
sandbox\build.bat
```

产出 `zerocode-sandbox:dev`，与 `python_tools.DEFAULT_SANDBOX_IMAGE` 一致。
换 tag 设环境变量 `ZEROCODE_SANDBOX_IMAGE`（交付包烘 `zerocode-sandbox:demo`）。

**镜像没构建时的症状**：`docker run` 回退出码 125 且输出含 `Unable to find image`。
平台把它识别成平台故障，回一句「沙箱镜像 … 未构建」的引导，而不是报成「命令非零退出」——
后者会让模型去改命令，离根因很远。

## 里面装了什么

- **Python 库**（`requirements.txt`）：python-docx / openpyxl / python-pptx / pandas / numpy /
  requests / python-dateutil。办公文档只支持 OOXML 三件（`.docx`/`.xlsx`/`.pptx`）。
- **CLI**（Dockerfile 的 apt 层）：git / curl / zip / unzip / ca-certificates。

**运行期装不了系统包**：容器以非 root（1000）跑，`apt-get install` 拿不到 dpkg lock
（`Unable to acquire the dpkg frontend lock, are you root?`）。所以要新增系统工具
**必须改 Dockerfile 并重建镜像**，不能由脚本或配置扩展——这是 ADR-0046「新增能力必须改平台代码」
那条刻意代价的延续。

老 `.doc`/`.xls` 不在支持范围，刻意不装 LibreOffice/antiword 这类转换依赖（ADR-0066）。

## 两条构建期约束

**基础镜像按发行版代号钉死**（`python:3.12-slim-trixie`，不是浮动的 `python:3.12-slim`）：
apt 包名表按代号查，代号一换就是构建期才炸的失败。同 `browser/Dockerfile` 踩过的坑。

**构建上下文是本目录**，不是整仓——不把 `apps/`、`data/` 送进 daemon。
交付包 compose 因此写 `context: ./sandbox`。

## 出口 router（`egress/`）

沙箱的「仅公网」由一个常驻小容器成立：`egress/` 镜像持 `NET_ADMIN`，把 nft 规则装在**自己的
netns** 里；沙箱以 `--network container:zerocode-sandbox-egress` 共享该 netns，规则因此对沙箱生效，
而沙箱自身没有 `NET_ADMIN`、改不了规则。**宿主防火墙一行不动。**

规则文本由平台生成（`app/agent/sandbox_egress.py`），网段表过 `guard.is_internal_ip` 筛，
不另抄一份判定。平台启动时写 `<data>/sandbox_egress/ruleset.nft` 并应用。

三条读代码看不出来的事实：

- **挂目录不挂文件**。compose 先起 router、平台后写规则；单文件 bind mount 在文件还不存在时
  会被建成**目录**，且原子替换后钉在旧 inode 上（同 `gateway/routes` 的教训）。所以挂
  `<data>/sandbox_egress/` 整个目录，entrypoint 等文件出现（最多 60s），等不到就非零退出交给
  restart 重试——**绝不「没规则也照常跑」**，那等于静默失去过滤。
- **DNS 例外不能写死**。传统 Linux 上容器解析器是 `127.0.0.11`，Docker Desktop 用的是宿主网关上的
  代理（实测 `192.168.65.7`，落在 `192.168/16` 里）。所以 entrypoint 按容器**实际**
  `/etc/resolv.conf` 放行，且只放 53 端口——实测同一 IP 的 8100 端口仍被拒。
- **router 没起时沙箱是 fail-closed**。`docker run --network container:` 会失败，平台把它识别成
  平台故障回「出口 router 未在运行」，不是降级成无过滤。

## 交付部署

`scripts/build_output.py` 把 `sandbox/` 打进包，交付 compose 声明两个构建目标：

```bash
docker compose build sandbox-image sandbox-egress
```

**这一步不能省**：`sandbox-image` 带 `profiles: [build-only]`（它不是常驻服务，平台按需
`docker run` 起一次性容器），`up -d --build` **不会**构建它。漏掉的表现是 `run_python`/`run_shell`
恒回「沙箱镜像未构建」，而平台其余功能一切正常。`deploy.sh` 第 7 步已代做。

## 本目录只放镜像 COPY 白名单里的源文件

`Dockerfile` 按文件名逐个 COPY，`scripts/build_output.py` 的白名单是同一份，
`tests/test_build_output.py` 有一条遍历本目录的不变式测试。所以**本地测试辅助脚本请放别处**
（`tests/` 或 `.scratch/`）——放进本目录会被那条测试当成「该入包的源文件」而误红。

网络受限的服务器上，构建期换源沿用 `ZEROCODE_APT_MIRROR` / `ZEROCODE_PYPI_MIRROR`
（与 browser 镜像同一套变量名），改完 `docker compose build sandbox`。