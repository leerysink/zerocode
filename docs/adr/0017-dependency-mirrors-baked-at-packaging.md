# 0017 依赖镜像源在打包期烘进交付物，运行期用同名 env 覆盖

演示服务器上 `docker compose up -d --build` 死在 `[backend 2/7]`——那一步是装 docker CLI 的
`apt-get install ca-certificates curl gnupg`，容器内连 `deb.debian.org:80` 超时后 apt 报
`E: Unable to locate package curl`，exit 100。同一台机器上 `ping www.baidu.com` 是通的，所以现场
第一判断成了「网络没问题」；境内云主机的典型形态恰恰是 DNS 解得出、到官方源那几条 TCP 连接超时，
而 apt 把超时报成「找不到包」，极易误读成 Dockerfile 里包名写错。

更要紧的是这类故障的**结构性成因**：换源的约定一直活在打包机的用户级配置里——
`frontend/package-lock.json` 的 `resolved` 全指向镜像站、本机有 `pip.conf` 与 `.npmrc`、apt 也早换过
——而容器一个都继承不到。于是「本地装得上、服务器上装不上」，且仓库里没有任何一处留下痕迹，换一台
服务器还得从头再踩。

我们决定把它变成打包参数：`scripts/build_output.py` 新增 `--apt-mirror` / `--pypi-mirror` /
`--npm-registry` 三项，与既有的 `--gateway-domain` 共用同一条三级取值链（显式参数 > 打包机环境变量 >
默认值），**默认全部留空，不给时产物逐字节不变**。校验与部署根、网关域名同级，落在暂存目录创建之前。
其中 PyPI 与 npm 两个值除了烘进镜像，还以同名变量走通三段：compose 写成 `${ZEROCODE_PYPI_MIRROR:-打包值}`
烘成默认，运维在服务器 `.env` 里改一次即长期生效，平台容器里的 `app/deploy/compose.py` 再把同一个值
转注进它起的每一个应用 dev 栈。

被否决的方案：Dockerfile 里硬编码镜像站（换一次站要改源码重出包，还把境内优化强加给境外部署，官方源
可达时反而退化）；把 `pip.conf` / `.npmrc` 当文件挂进容器（多两个交付件与两处挂载，而 pip/npm 本来就认
环境变量，何况 `.npmrc` 常带 token——那等于把凭据分发到服务器）；只走 `build args`（覆盖不到运行期应用
dev 栈里那一次 `npm install` / `pip install`，而「点预览装依赖」才是这条链的主路径，两套机制并存更容易
漏配一处）；在服务器上手改 `/etc/hosts` 或走 host 网络（把病因从容器挪到宿主、照样不可复现，容器一重建
又没了）。

## 后果

- 「默认留空」意味着**已经在服务器上炸过的旧包不会自己变好**：必须重跑打包，参数才会落进
  `backend/Dockerfile` 与 compose。这是个动作，不是个开关。
- 平台镜像的 Dockerfile **不在仓库根**，而是打包时生成到交付包的 `backend/Dockerfile`，由 compose 的
  `build.context: .` + `build.dockerfile: backend/Dockerfile` 引用（上下文取整包，才能同时 COPY
  `backend/` 与 `frontend/dist`）。所以 apt/pip 的换源块只能靠重跑打包更新，手改服务器上的那份等于改
  产物，下次发布即被覆盖。镜像实际 COPY 的只有 `backend/requirements.txt`、`backend/app`、
  `frontend/dist` 三项，其余（`apps/` `data/` `store/` `init.sql` `apply-init.sh` `README-deploy.md`
  `.env` `.env.example`）由 `.dockerignore` 挡在构建上下文之外——想「挂个配置文件进构建」的那条路
  在这里也是堵着的。
- 两个方向的归一化**故意不对称**，别把它当不一致去「修正」：`--apt-mirror` 宽容——剥协议、剥尾斜杠、
  转小写，只收裸主机名（带路径 / 端口 / 空白才拒），因为换源靠 sed 替换主机名段、目录必须沿用官方布局；
  `--pypi-mirror` / `--npm-registry` 严格——必须是 `http(s)://` 开头的完整地址并保留端口与路径，因为
  这两个值原样写进容器 ENV 由 pip/npm 自己解析。**查询串与锚点一律拒**：带 `?token=` 的地址会被原样
  烘进交付包，那玩意儿是凭据。
- apt **不进容器 env、不进 `.env.example`**：它只作用于镜像构建期，容器里没有任何一方读它。「变量名
  贯通三段」这件事只有 PyPI 与 npm 配得上，别顺手把 `ZEROCODE_APT_MIRROR` 也塞进 environment——那只是
  多一个没人读的键。
- prod 栈**一律不注入**，这是已知缺口而不是遗漏：prod 的 compose 用 `build: ../backend`，
  `pip install` / `npm install` 跑在构建容器里，而 compose 的 `environment:` 对 `build:` 无效。现状用
  一条「设了变量也必须与 golden 文件逐字节一致」的测试钉住，免得后来人以为 prod 也跟着走镜像源。大型栈
  （vue3-springboot）后端的 Maven 仓库镜像同理不在三个参数范围内，它的 dev 侧只注入 npm。
  **本条的 prod 半边已被 ADR-0025 推翻**（境内服务器上 prod 构建等满 600s 超时）：改走 `build.args`，
  不再依赖 `environment:`。Maven 半边仍然成立。
- 「不给 = 逐字不变」有实现代价：注入块要么返回空串、要么返回自带换行的整块，模板里的占位标记**不得
  独占一行**。变异验证实测过——把空替换从 `""` 改成 `"\n"`，golden 逐字节比对与两条「空值视同未设」的
  用例同时变红。
- 三级取值的空值处置同样不对称：显式传空必拒（`--pypi-mirror` 后面漏值是人的动作，退回官方源等于这次
  发布继续炸而打包报成功）；打包机 env 里的空视同未设（shell / CI 里 `set FOO=` 与没设通行等价，为一个
  无关的空变量拦下一次正常打包是纯粹的伤害）。所以取值链**不能**写成 `value or DEFAULT`。
- 测试必须清掉宿主机变量：打包机的标准操作就是 `setx ZEROCODE_PYPI_MIRROR …`，而 pytest 进程会继承那份
  用户级变量——不隔离的话 golden 逐字节比对会在配过镜像源的机器上莫名变红、换台机器又是绿的。
  `tests/test_compose_render.py` 用模块级 autouse fixture 处理，与 conftest 里网关路由的同构先例一致。
- 服务器 `.env` 里这几个变量必须写**裸值**、不能写 `${…}`：compose 读 `.env` 只做一层插值，`${` 会原样
  进容器被当字面值（网关域名同理，见 ADR-0016）。
- 与 ADR-0016 的关系：那里定的是「域名」这一个值的 env 通道，本 ADR 把同一套取值链与运行期覆盖形态推广
  到依赖镜像源，并把「哪些值配得上运行期覆盖」这条判据写明白——**有运行期消费方才进 `.env`**。部署根是
  反例：compose 挂载、镜像 `WORKDIR`、init.sql 的 `root_path` 三处同源，运行期改它只会让三者对不上且
  全程不报错，所以钉死不给覆盖位。
