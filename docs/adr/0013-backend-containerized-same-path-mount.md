# 0013 平台后端容器化 + 宿主同路径挂载，部署根被钉死

演示服务器要一条 `docker compose up -d` 拉起平台。难点是后端并非纯 Web 服务：它用
`asyncio.create_subprocess_exec("docker", ...)` 在宿主执行 `docker compose -f <root_path>/deploy/*-compose.yml`
起应用的 dev/prod 栈，而这些 compose 里的挂载是**相对 compose 文件目录**的路径（如 `../backend`），
`root_path` 又是建应用时写死的宿主绝对路径。我们决定：后端进容器、挂 `/var/run/docker.sock`、
镜像内装 docker CLI + compose plugin，并把部署根下的 `apps/`、`data/`、`store/`（约定
`/opt/zerocode/apps`、`/opt/zerocode/data`、`/opt/zerocode/store`）**挂载到容器内的同一条绝对路径**。
平台代码本身烘进镜像、不做同路径绑定——
否则 bind mount 会把镜像里的 `app/`、`frontend/dist` 遮住，版本就锁在了宿主目录上。

被否决的方案：后端不进容器、用 venv + uvicorn 跑在宿主（Docker 路径天然正确、改动最小，但失去
"一条命令拉起全部"，演示机还得手工装 Python 与 systemd 单元）；全容器化 + named volume 存应用源码
（必须改 `compose.py` 的渲染逻辑，把相对绑定换成卷挂载，动到平台核心发布路径，风险最高）。

## 后果

- 部署根不可自由摆放：`apps/` 的容器内路径与宿主路径逐字相同，`root_path` 才在两个视角下
  都成立（`data/`、`store/` 也同样被烘进 compose 与打包脚本的部署根，换目录同样要重打包）。
  换目录等于要重算全部 `workspace.root_path`，这是本决策最贵的一面。
- 三条挂载的理由分两类，别混为一谈：`apps/` 是**必须**同路径——运行期要被宿主 daemon 按宿主
  路径解析（应用工程）；`data/` 与 `store/` 只需**挂出来**——它们是运行期落盘（平台数据区、
  技能库、商店包），不挂就落进容器可写层，`up -d --build` 一重建就没，路径是否与宿主相同并不
  影响正确性。平台代码升级走镜像重建，不靠宿主目录同步。
- 后端容器持有宿主 docker.sock，等价于持有宿主 root 权限。这是"全流程可玩"的既定代价，
  演示机不得跑不可信工作区。
- 预置应用的 `root_path` 一律重写为 `<部署根>/apps/<slug>`；打包脚本是唯一做这次重写的地方，
  本地库里的 Windows 路径不上服务器。
- 镜像内必须自带 docker CLI 与 compose plugin，否则平台所有部署能力静默失效——启动时的
  `_deploy_startup` 各步都是"非致命 + 打日志"，Docker 缺失不会让平台崩，只会让预览/发布点不动。
