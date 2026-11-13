# vue3-node 与 static 栈交付（小栈补全）

- 日期：2026-11-13
- 上游：`docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/02-技术栈模板与生成规范.md`（FR2-06 的欠账条）
- 决策：[ADR-0047 vue3-node 栈选型](../adr/0047-vue3-node-stack-selection.md)
- 术语：`CONTEXT.md` 的「小栈」「vue3-node 栈（Node 小栈）」「static 栈（纯前端静态页）」
  「铺数据轨 / 不铺数据轨」「栈交付闸门（裁定 R-C）」
- 先例：`docs/specs/2026-11-03-mobile-uniapp-stack.md`（加栈的登记面清单）、ADR-0039（不复制底座的叠加层）

## 1. 问题陈述

向导给用户的三个小型档选项里，只有一个是真的。`vue3-node` 与 `static` 的 stack key 早已登记进枚举、
契约文案、健康路径与前端选项，但 `app/templates/` 下没有对应的模板目录，于是这两个选项在创建应用时被
**栈交付闸门**（`app/services/app_service.py:87-88`，裁定 R-C）以 400「该技术栈尚未开放（即将上线）」拦下。
「界面能点、创建必挂」本身就是坏产物：用户无法从文案里判断这栈到底有没有。

`vue3-node` 还是全仓登记得最不完整的一个栈——它被**半登记**成了 Python 栈的样子：

- `app/agent/check_tools.py:55-58` 把它与 `vue3-fastapi` 并列，要求 `backend/app` 目录并跑
  `python -m compileall` —— Node 工程里两者都不存在，`run_check` 对这个栈永远只能报「无法执行」。
- `app/agent/preview_tools.py:485` 给模型的提示是「**无后端**，前端刷新即可」——那是 `static` 的语义，
  等于告诉模型这个栈没有后端可改。

而 `static` 缺得更深一层：脚手架 `app/deploy/scaffold.py:57-66` 的判据是「产物 `sqlchange/` 里没有任何
`V*`/`D*.sql` 就补一份 `demo_notes` 种子」——一个**不铺数据轨**的栈铺完底必然落进这条分支，
产物里会多出一张永远不会被建的表和一次必红的迁移。

## 2. 方案概述

把两个栈交付到**已交付栈**状态，交付物分两类，不引入新抽象：

1. **模板目录**（新增两份，随平台镜像发布）：`app/templates/vue3-node/`、`app/templates/static/`。
   形态与 `vue3-fastapi` **小栈同形**：同一套 `/api` 前缀与 `/api/health`、同一对端口口径、
   后端容器内同样监听 8000、同一份 `frontend/nginx.conf` 反代形态。
2. **平台侧的分派补齐**：所有「按 stack 分流」的既有分支点上补齐这两个栈——compose 的 dev/prod 文本、
   可发布名单（两处）、`run_check` 预设、预览提示词、契约文案、脚手架的数据轨判据。

整份变更只跨一个接缝：**`stack` → 平台产物**。除 `scaffold` 的数据轨判据外，不新增模块、不新增配置键、
不新增 stack key。

## 3. 用户故事

### 创建与铺底

- **US-01** 用户在向导里选「Vue3 + Node（Express）」创建应用，得到一套**零修改可预览**的 Node 工程，
  不再收到 400。
- **US-02** 用户选「纯前端静态页」创建应用，同样创建成功，产物里**没有** `backend/`、`schema/`、`sqlchange/`
  三个目录，也**没有**平台补的 `demo_notes` 种子文件。
- **US-03** 铺底产物中不残留任何平台占位符（`{{SLUG}}`/`{{APP_NAME}}`/`{{DEV_PORT}}`/`{{PROD_PORT}}`/
  `{{DEV_DB}}`/`{{PROD_DB}}`/`{{TODAY}}` 全部被替换），与现有栈共用同一条残留自检。
- **US-04** 新栈应用的 `app.json` 里 `stack`、`health.backend`（`/api/health`；`static` 为空串）、
  端口对与库名，与另两个小栈同形同规则；应用卡片与聊天页操作条不需要为新栈新增任何分支。
- **US-05** 平台仍然为 `static` 应用建库建角色、仍然写 `deploy/.env` 的凭据位（`provision` 保持栈无关）。
  代价是每个静态应用浪费一对没人用的库——换取的是创建链路不出现栈分支。

### dev 预览

- **US-06** 启动 Node 应用的 dev 预览：`migrate` 闸门照旧先跑完（结构轨 + 数据轨），
  随后 `backend` 容器装依赖并以 watch 模式起在容器内 8000，`frontend` 起 vite 并代理 `/api → backend:8000`。
- **US-07** 启动 `static` 应用的 dev 预览：只有一个 vite 服务，无 backend、无 migrate；
  `dev-compose.yml` 与既有 golden `tests/golden/dev-compose.static.yml` 逐字节一致。
- **US-08** Node 后端改一个 `.ts` 文件后无需重启即生效；改 `package.json` 或 `tsconfig.json` 后
  必须显式重启预览才生效，且这条口径写进了 `<preview>` 提示词（不再是「无后端，刷新即可」）。
- **US-09** Node 应用的后端容器有**自己**的 `node_modules` 具名卷，与前端那个互不遮挡；
  停预览再起，依赖不必重新联网全量下载。
- **US-10** 首次预览时后端启动 banner 打到 **stdout**（应用、环境、端口、脱敏后的数据库、连通性、就绪耗时），
  与 `vue3-fastapi` 的日志面同构；预览日志抽屉里能直接看到它。

### 发布 prod

- **US-11** Node 应用可发布：`render_prod_compose` 产出 backend（`node:20-alpine` 多阶段：`tsc` 编译后
  只带生产依赖运行）+ frontend（nginx）+ migrate 三个服务，端口与库的接线与 fastapi 栈同形。
- **US-12** `static` 应用可发布：prod 只有 frontend 一个服务（纯 nginx 静态站点），无 backend、无 migrate；
  发布体检不再探 `/api/health`（`app/deploy/prod.py:86` 已对 `static` 短路，本规格只补 compose 与名单）。
- **US-13** 平台设了 npm 换源（`ZEROCODE_NPM_REGISTRY`）时，Node 栈的 backend 与 frontend 在 prod
  **构建期**都收到 `NPM_CONFIG_REGISTRY` build arg；没设时产物逐字节回退到旧的标量 `build:` 形态，
  老应用 golden 不变。fastapi 栈的 backend 仍收 `PIP_INDEX_URL`，两条不互串。
- **US-14** 存量应用的生产反代补齐（`_ensure_prod_proxy_conf`）按栈取模板：`static` 应用补到的
  `nginx.conf` 里**不得**出现 `backend` upstream（nginx 解析不到该 hostname 会在启动期直接失败），
  Node 应用补到的是指向 `backend:8000` 的那份。
- **US-15** 发布按钮的可用性对新栈正确：两个新栈进入后端 `PUBLISHABLE_STACKS` 与前端
  `utils/publishableStacks.js` 镜像名单，漂移由既有测试 `test_frontend_publish_gate_matches_backend_whitelist`
  钉住。
- **US-16** 新栈应用的资源监控、打开生产、启停生产、库结构查询与查询控制台全部开箱可用，
  无需任何栈分支（这些面按容器与凭据工作，与语言无关）。

### AI 契约与自检

- **US-17** 激活 Node 应用工作区时，注入的应用契约写明本栈事实：Express 5 + TypeScript + pg-promise、
  `DATABASE_URL` 是**纯 `postgresql://` 连接串**（不是 SQLAlchemy 的 `+asyncpg` 形态）、`APP_PORT` 为容器内端口、
  依赖只能在模板清单上追加。
- **US-18** 契约对 Node 栈给出两条栈专属红线：SQL 一律走 pg-promise 的参数化形式（不得字符串拼接），
  Express 5 的路由通配写 `/{*path}` 而非 `'*'`（旧写法在 5.x 会在启动期抛错）。
- **US-19** `run_check` 对 Node 栈的后端预设是在一次性容器里跑 `tsc --noEmit`，工程没有 `tsconfig.json` 时报
  「无法执行」而不是「检查未通过」；对 `static` 栈请求后端预设时明确回答「该栈没有后端」，
  前端预设（类型检查或降级 `vite build`）照旧可用。
- **US-20** `static` 应用的契约不再声明后端环境变量与 health 接口义务（`ENV_HINT` 已是「（无后端）」），
  并说明数据落 localStorage 与导出文件、跨设备同步不在本栈能力内。

### 防漂移与文档

- **US-21** 两份新模板各有**模板体检测试**（照 `tests/test_springboot_template.py` 的先例）：关键文件在位、
  占位符齐全且形态合法、非 UTF-8 文件不带占位字节、依赖清单按 ADR-0047 锁版本、Dockerfile 声明换源 ARG、
  `static` 模板里不存在 `backend/`、`schema/`、`sqlchange/`。
- **US-22** 上游规格 02 的 §1 表格、§4.2、§4.3、FR2-06 按 ADR-0047 勘误（`express ^4.19` + 裸 `pg`
  → Express 5 + TypeScript + pg-promise；「模板仍待补」→ 已交付），并留下勘误标记而不是静默改写。
- **US-23** `README.md` 的技术栈表补上新栈，`CONTEXT.md` 五条术语在位且实现使用这些规范词。

## 4. 已定决策

| # | 决策 | 出处 |
|---|---|---|
| D-1 | 两个栈同期交付，`static` 不做「只到预览」的半套 | 拷问共识 |
| D-2 | Node 栈选型 Express 5 + TypeScript + pg-promise，**不用 ORM** | ADR-0047 |
| D-3 | 后端容器内监听 **8000**，不用 Node 惯例 3000 | ADR-0047 |
| D-4 | Node 模板与 `vue3-fastapi` **同形最小骨架**：health + `demo_notes` 列表/新增 + 启动 banner + 双轨目录；不铺登录/RBAC，不加 `{code,msg,data}` 响应包 | 拷问共识 |
| D-5 | `static` **不铺数据轨**，但平台照旧建库建角色（`provision` 不加栈分支） | 拷问共识 |
| D-6 | `vue3-node` 与 `static` 都**不进**联合健康探针（`JOINT_HEALTH_STACKS` 只增不改，本期不增） | 拷问共识 |
| D-7 | 完成度为端到端（C 档）：模板 + dev + prod + 可发布名单 + `run_check` + 提示词 + 契约 + 测试 + 文档 | 拷问共识 |
| D-8 | 只开一纸 ADR（0047）；`static` 的数据轨判据属可低成本翻案的实现规则，记入 `CONTEXT.md` 术语与本规格，不单开 ADR | 拷问共识 |
| D-9 | 栈交付闸门的**判据本身保留**，只是现在没有小栈被它拦下；「向导里有 = 必须过闸门」成为规范 | 本规格 |

## 5. 测试决策

接缝从低到高五处，优先打高的；除模板体检测试外全部复用既有接缝，不新建测试基座。

1. **`compose.render_dev_compose` / `render_prod_compose`**（主接缝，文本进 golden）
   - 新 golden：`tests/golden/dev-compose.node.yml`、`prod-compose.node.yml`、`prod-compose.static.yml`。
   - 既有断言翻转：`test_compose_render.py:498-501`（node dev 曾抛 500）改为渲染成功；
     `:306-308`（`static`/`vue3-node` 不可发布）改为二者成功、名单外的栈仍 400。
2. **`app_service.create_app_project`**（创建链路）
   - `test_app_service.py:83-88` 的 R-C 400 断言改为「可创建」；闸门本身另用「不存在的模板层」钉住，
     防止判据被整段删掉（变异检查：把 `app_service.py:87` 的存在性判据注释掉，该用例必须红）。
3. **`scaffold.render`**（数据轨判据）
   - `static` 产物不落 `demo_notes` 种子；带数据轨的栈行为逐字节不变（`test_scaffold.py` 既有断言不许动）。
4. **`contract.build_app_contract`**（契约文本，读 `app.json`）
   - 断言按栈取到纯 `postgresql://` 提示、pg-promise 参数化红线、`/{*path}` 红线；
     `test_contract.py` 里「三处 stack key 同值」那条锁不动。
5. **`check_tools._backend_check` + `preview_tools.build_preview_prompt`**
   - 预设分派按栈正确；`test_preview_tools.py:1115` 的「刷新即可」断言按新文案改写。
6. **模板体检测试**（新增两份，扫模板目录本体）——`static` 与 `vue3-node` 各一份。

**「完成」长什么样**：上述定向 pytest 全绿 + `dev-compose`/`prod-compose` golden 与渲染产物逐字节一致 +
两个新栈各**真起一次 dev 预览**并浏览器验收到首页与 `/api/health`（US-01/02/06/07/10 的真机证据）。
全量测试按仓库纪律串行跑，且会拆掉本机正在跑的预览栈——手工验收与全量测试不排在一起。

## 6. 明确不做

- **不做联合健康探针扩容**：小栈的 dev 就绪仍只探前端 `/`。「预览报就绪而后端半死」这个已知局限继续由
  模板首页 banner 与 AI 走查承担（D-6）。
- **不做 static 上架商店**：`app/store/release.py:59-73` 的闸门要求 `zc-{slug}-backend:prod-{version}` 镜像
  在位、且 prod 库有成功的迁移历史——纯前端且**不铺数据轨**的应用两条都必然落空。本期只在闸门链最前面
  给出一句诚实拒绝（「纯前端静态页应用暂不支持上架商店」），不改商店拓扑。
- **不改 `provision_env` 的建库逻辑**（D-5），不动端口池、`.env` 形态与凭据生成。
- **不新增平台配置键**——因此不需要动 `tests/conftest.py` 的 `app_config` 种子。
- **不改统一响应包、不做登录/RBAC 基线**（D-4）。
- **不做平台出包与演示服务器部署**：模板住在平台仓库里随镜像发布，本规格的验收在本地工作区完成；
  线上生效是另一层动作，另行下令。
- **不动 node 栈之外的任何栈**：`vue3-fastapi`、`vue3-springboot`、移动栈的产物与 golden 必须逐字节不变。

## 7. 补充说明

- **最容易踩空的一条**：`DATABASE_URL` 的形态。fastapi 栈的 dev/prod compose 注入的是
  `postgresql+asyncpg://…`（SQLAlchemy 方言前缀），pg-promise 解析不了它。Node 栈的两份 compose 文本必须
  注入纯 `postgresql://…`，且契约、模板 `.env` 假设与 README 三处口径一致。这条错的表征是
  「预览起来了、首页显示后端未就绪、日志里一句连接串非法」，不是创建期报错。
- **prod 构建参数名按栈分叉**：`compose.py:427` 现在无条件给 backend 传 `PIP_INDEX_URL`。Node 栈的 backend
  构建上下文要的是 `NPM_CONFIG_REGISTRY`（值取 `ZEROCODE_NPM_REGISTRY`），需要一个按栈的映射而不是再加一个
  if；未设镜像源时两者都必须逐字节回退到标量 `build:` 形态（ADR-0025）。
- **版本锁基线**（模板内写死，AI 只能追加）：`node:20-alpine`、`express ^5`、`pg-promise ^11`、
  `typescript ^5`、`tsx ^4`、`@types/node ^20`、`vue ^3.4`、`vite ^5`、`ant-design-vue ^4`、`pinia ^2`、
  `axios ^1`。前端沿用通用前端锁定行，与 `vue3-fastapi` 同一份 package.json 形态。
- **`node_modules` 具名卷**：`compose.py` 的卷名都是同源派生的（`VOLUME_NAME`/`M2_VOLUME_NAME`/
  `PIP_VOLUME_NAME`/`MOBILE_VOLUME_NAME`），新增 backend 侧 Node 卷要照同一个 `_x_volume()` 同构写法补，
  且顶层 `volumes:` 声明与挂载条目必须同源——理由是 `MOBILE_VOLUME_NAME` 那条注释写的同一个：
  同挂载点共用卷会把两套依赖树盖在一起。
- **市场打包不需要改**：`app/store/packages.py:21` 的 `EXCLUDE_DIRS` 已全局排除 `node_modules` 与 `dist`，
  Node 工作区的编译产物天然不进包、不算源码分叉。
- **出包与指纹之外的复用面**（资源监控、库控制台、网关、APK/小程序构建）都不按栈分支；其中 APK 与小程序
  构建硬绑移动栈（`apkbuild.py`/`mpbuild.py`），新栈不涉及。
- 本规格是 `to-tickets` 的输入；示踪弹顺序建议：**先跑通 Node 的 dev 全链（模板 + dev compose + 闸门放行 +
  一次真预览）**，再补 prod 与自检，最后做 `static`——`static` 单独一片是因为它的数据轨判据一动就牵连
  现有栈的 golden。
