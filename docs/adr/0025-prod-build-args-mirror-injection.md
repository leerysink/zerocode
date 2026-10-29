# 0025 依赖镜像源经 build.args 覆盖 prod 构建期

演示服务器上应用「发布」连续两次死在第 2 步：`docker compose -f prod-compose.yml build` 等满
`deploy.build_timeout` 的 600 秒被平台 kill，`deploy_record` 里只留下一句
`docker compose -f …/prod-compose.yml… 超时（600s）`。这台机器是境内云主机，而 ADR-0017 换源链只
贯通到**平台自己的镜像**与**应用 dev 栈**——prod 栈是 `build: ../backend`，`pip install` / `npm install`
跑在构建容器里，compose 的 `environment:` 对它无效，所以那一次安装照旧连 pypi.org 与
registry.npmjs.org。dev 预览能起来（运行期注入生效）而发布必挂，正是这条缺口的典型形态：同一份代码、
同一个变量、两条通道只通了一条。ADR-0017 把它写成「已知缺口而不是遗漏」并用一条 golden 逐字节断言钉住，
今天按它自己预留的路径（「将来若要覆盖 prod 构建期，走的是 build args」）补上。

我们决定让同一个打包参数喂两条通道：`app/deploy/compose.py` 的 prod 模板把 `build: ../backend` /
`build: ../frontend` 换成 `__BUILD_BACKEND__` / `__BUILD_FRONTEND__` 标记，渲染时按
`ZEROCODE_PYPI_MIRROR` / `ZEROCODE_NPM_REGISTRY` 展开成 `build: {context, args}`；应用模板的
Dockerfile 在**跑安装的那个 stage** 的 `FROM` 之后声明 `ARG <名>=<官方源>` + `ENV <名>=${<名>}`。
默认值填官方源而不是留空，于是「没配镜像源」这一支不需要任何条件分支就自然退回官方行为。存量应用由
发布 preflight 的 `ensure_prod_build_mirror` 幂等补这两行，与既有的 `_wire_dockerfile_proxy` 同一条
保守纪律：只认平台模板形状，形状不认识就原样放过。

被否决的方案：把超时调大（`deploy.build_timeout=1800` 只能当临时垫，它把 10 分钟的等待换成 30 分钟的
等待，装不上的依赖还是装不上，而且慢构建会一直占着该应用的发布互斥闸门）；把 `pip.conf` / `.npmrc`
写进应用的构建上下文（污染用户工程目录，且 `.npmrc` 常带 token——ADR-0017 已经为此否过同一形态）；
在服务器上手改应用 Dockerfile 的源（改的是应用工作区文件，下次 AI 迭代或重出模板即被覆盖，且仓库里
不留痕迹，正是 ADR-0017 要根治的那个「约定只活在打包机上」）；给 prod 也塞 `environment:`（对构建期
无效，是纯粹的假动作）。

## 后果

- **两条通道的形态不对称是刻意的**：dev 用 `environment:`（安装跑在运行期容器里），prod 用 `build.args`
  （安装跑在构建容器里）。同一个 `ZEROCODE_NPM_REGISTRY` 值、两种注入位，别去「统一」它们。
- 「没设变量时交付物逐字节不变」这条 ADR-0017 的承诺仍然成立，但实现约束换了形态：dev 的注入块会返回
  空串，所以标记不得独占一行；prod 的 `_prod_build_block` 永不返回空（未设时逐字回退成
  `    build: ../backend`），所以标记可以独占一行，行尾换行由模板自己提供。golden
  `tests/golden/prod-compose.fastapi.yml` 在未注入分支仍逐字节通过，不必重生。
- **ARG 必须落在消费它的那个 stage**：前端 Dockerfile 是 `node` 构建 stage + `nginx` 运行 stage 两段，
  插错 stage 等于没插。回填函数因此按「基座前缀 + 那条安装指令」双条件定位，而不是往文件尾追两行。
- **只对模板形状出手**：大型栈后端的基座是 `FROM maven:…`，`PIP_INDEX_URL` 对它没有消费方，所以
  `_PROD_COMPOSE_SB` 的 backend 干脆没有标记（不是漏配）。**Maven 仓库镜像仍是缺口**——它需要的是
  `settings.xml`，不是环境变量，超出这两个参数的形态；大型栈 prod 构建今天靠 `build_timeout_large`
  的 1800s 兜着，将来要补得另立一套注入位。
- 应用自带 Dockerfile 时，注入的 build args 会收到 BuildKit 一条 `[Warning] One or more build-args
  […] were not consumed`。这是警告不是失败，且比「静默用官方源等到超时」可观测——不去消音。
- 存量回填写的是**用户工程里的文件**，与应用自己的编辑存在竞争：幂等判据是「文件里出现过这个 ARG 名」，
  应用自己接过源就不再过手，不覆盖它的值。回填无条件执行、与变量是否配置无关，这样运维哪天设上镜像源，
  存量应用不必再等一次发布去补接线。
- 修完的是「装得慢」，不是「装不上」：如果服务器连镜像站都不通，构建仍会等到超时。超时从 600s 到判失败
  这段时间里发布互斥闸门占着该应用（`_spawn` 409），所以线上调大 `deploy.build_timeout` 之前先确认
  根因是慢还是不通——本次的现场证据是 pg 侧无锁、构建期无容器事件，指向网络侧。
- 生效需要**重出包**：compose 模板与 Dockerfile 模板都住在平台镜像里，`docker cp` 进容器只能临时救。
  服务器 `.env` 里这两个变量必须写裸值（compose 只插值一层，`${` 会原样进容器，同 ADR-0016）。
