# 0035 Windows 部署落点 = PowerShell 皮 + WSL 发行版内跑同一份 Linux 脚本

平台要在 Windows 上也能一键部署。可选形态有三种：PowerShell 直接驱动 Docker Desktop 起 Linux 容器、
PowerShell 只是 WSL 的皮、平台裸跑 Windows（venv + uvicorn + 静态 dist，只有 pg/browser 进容器）。
我们决定第二种：`scripts/deploy.ps1` 只负责保证 WSL2 与一个发行版可用，然后把交付包推进发行版、
在里面执行与 Linux 服务器完全相同的 `deploy.sh`。Docker Engine 由 `deploy.sh` 自己 apt 安装，
**不装 Docker Desktop**。

## 理由与备选

- 一条部署逻辑，两个入口。同路径挂载、`docker.sock`、`host-gateway`、`read_only` sidecar 这些
  形状本来就是 Linux 的；换成「PowerShell 直接跑 compose」要么放弃同路径挂载，要么为 Windows
  单独渲染一套 compose，两套行为从此分叉。
- 裸跑 Windows 与 `dev.bat` 的形状一致，但平台所有部署自检（容器内 `printenv`、sidecar `/healthz`
  探活）都要重写，且与 Linux 版不再是同一件事，每次改动维护两份。
- Docker Desktop 免掉了 systemd 与发行版内装 engine 的接线，代价是企业规模使用的授权条款、首次
  GUI 接受条款，以及它会接管 WSL 里的 docker CLI——与 apt 装的 engine 冲突，脚本得二选一。

## 后果

- 「安装地址」在 Windows 上是 **WSL 发行版内部的 Linux 路径**，不是 `D:\`。放 `D:\` 等于把
  `pgdata` 与应用工程放进跨系统 9P 慢盘，`node_modules` 类目录会慢到不能用。
- WSL 发行版要开 systemd（`/etc/wsl.conf`），脚本负责写入并提示 `wsl --shutdown` 后续跑。
- Docker 引擎随发行版生命周期：Windows 重启后需要再跑一次脚本（或另配开机任务）把栈拉起。
- 中央网关在 Windows 上默认不建：宿主 80/443 常被占用，通配 DNS 也指不到本机；
  需要 HTTPS 域名时仍按 README 手工起。
- 双击入口是 `scripts/deploy.bat`，**不算第三个入口**：它只做 `chcp 65001` → 拉起同目录
  `deploy.ps1` → 回传退出码 → `pause`，一条判定都没有（理由见本 ADR「一条部署逻辑，两个入口」）。
  加它的原因是 Explorer 双击 `.ps1` 只会用编辑器打开，而规格 US-23 承诺的是「双击或 PowerShell」。
  它与 `build-output.bat` 同一条约定：纯 ASCII 源码 + CRLF 行尾，自动化驱动时用 `--no-pause`。
