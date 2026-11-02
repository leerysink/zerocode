# 0034 部署根改为部署期输入，同路径挂载改由 build arg 取值

ADR-0013 把部署根（约定 `/opt/zerocode`）在**打包期**烘死到三处——compose 的同路径挂载、镜像
`WORKDIR`、init.sql 里预置应用的 `root_path`——后果是「换目录要重打包」，与一键部署要求的
「安装地址由部署现场输入」正面冲突。我们决定：部署根改在**部署时输入**，compose 挂载写成
`${DEPLOY_ROOT}/...`、镜像 `WORKDIR` 改为 `ARG DEPLOY_ROOT` 并由 `build.args` 从同一个 `.env`
取同一份值，init.sql 里预置应用的 `root_path` 写成占位符 `{{DEPLOY_ROOT}}/apps/<slug>`，
由 `apply-init.sh` 执行前渲染。**同路径挂载本身不变**，变的只是取值时机。

## 备选方案

- 部署脚本把包放到任意目录、再建软链回 `/opt/zerocode`：不动打包器，但软链一被删或搬家就全站
  失联，症状与「目录放错」同形、极难排；WSL 里把根放到 `/mnt/d` 还会落进跨系统 9P 慢盘。
- 部署期用文本替换改写 init.sql：改动最小，但对生成物动手，替换后的文件与包内不再一致，
  下次升级的「包 vs 现场」判定被污染。
- 换根即禁用预置应用：把「自由选路径」与「带预置应用」变成二选一，用户侧不可接受。
- 保持打包期烘死、脚本只问其它参数：零风险，但不满足需求本身。

## 后果

- `.env` 重新提供 `DEPLOY_ROOT`。ADR-0013 与打包脚本注释里「刻意不提供 DEPLOY_ROOT」那句作废：
  它当初的危险在于「运行期改 .env 而 WORKDIR 不变」，现在两者由同一条 build arg 同源取值，
  不再有分家的可能——代价是**改部署根必须重跑 `up -d --build`**，只 `up -d` 不生效。
- Windows 落点走 PowerShell 皮 + WSL2 内跑现有 Linux 部署脚本（不是 Windows 原生容器，也不是
  宿主裸跑平台）。因此 Windows 上的「安装地址」是 WSL 发行版内部的 Linux 路径。
- `build_output.py` 的 compose/Dockerfile/init.sql 三处渲染与重放安全审计都要跟着改；包里那份
  `deploy.sh` 从此落后于 `scripts/` 的窗口期风险，由每次打包重新拷贝来消除。
