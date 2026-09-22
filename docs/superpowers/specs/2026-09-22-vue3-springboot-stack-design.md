# zerocode 设计文档 — 大型应用栈 vue3-springboot（以 base-pro 为基座的模板移植）

日期：2026-09-22　范围：`app/templates/vue3-springboot/` 模板交付 + 该栈的 dev 预览与 prod 发布全链路
参考工程：`D:\project\private\base-pro`（Spring Boot 3.2.5 + MyBatis-Plus + Spring Security/JWT + Redis + Vue3/TS/ant-design-vue 4 的 RBAC 管理后台基座）
关联规格：`docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/02-技术栈模板与生成规范.md`（本文对其 §4.2/§4.3 的 springboot 形态有两处背离，见 §2 决策 D-6/D-7）

## 1. 目标与非目标

### 目标

1. 用户在向导里选「大型」即可创建一个 **Vue3 + Spring Boot 3** 应用，铺底产物是 base-pro 的全套后台基座：登录、动态菜单侧栏、用户/角色/菜单/部门/字典/操作日志/登录日志七页可用。
2. 该应用**零修改可预览**：dev 起栈后能在浏览器登录并使用基座页面，AI 只在这个栈内增量开发业务模块。
3. 该应用**可发布**：prod build → migrate → up → 联合体检 → 版本晋升一条链跑通，与 vue3-fastapi 同权。
4. 移植后仍守平台既有铁律：数据库双轨（`schema/schema.sql` 幂等结构真相 + `sqlchange/D*.sql` 数据轨）、连接串与口令由平台注入、`deploy/` 与 `app.json` 对 AI 禁读禁写、compose 由平台渲染不进模板。

### 非目标（YAGNI）

- **不做 base-pro 上游同步机制**：模板是 fork 快照，上游后续改进不自动回流（已知取舍，见 §10）。
- **不引入平台级共享 Redis**：Redis 是应用私有依赖，随应用栈起停。
- **不做 .m2 全局共享/预热镜像**：本片用 per-app 私有卷，共享预热留后续优化。
- **不改 `vue3-node` / `static` 两栈**：它们仍按 R-C 以 400「尚未开放」拒绝。
- **不动平台数据库结构**：本设计不新增列/表，密钥走 `deploy/.env` 而非 `app_config`，因此**无 `changelog/` SQL 版本文件**。
- 不做接口级鉴权增强（base-pro 的 `sys_menu.permission` 目前只驱动侧栏可见性与路由守卫，不强制到方法级；原样继承，不改语义）。

## 2. 决策清单（含用户裁定与实测依据）

| # | 决策 | 裁定 | 依据 |
|---|---|---|---|
| D-1 | 模板形态 | **完整移植 base-pro + 按平台约定改造** | 用户选定；避免每个大型应用让 AI 重写登录/RBAC |
| D-2 | Redis 依赖 | **每应用一个 redis 容器**（dev/prod compose 各带） | 用户选定；Redis 在 base-pro 是硬依赖（9 文件 40 处引用，全部收敛在 `LoginUserCache` 门面） |
| D-3 | dev 后端跑法 | **容器内 `mvn -B spring-boot:run`**（源码挂载 + .m2 卷） | 用户选定；与 fastapi 栈「挂载 + 重启生效」同构，日志仍进 `compose logs` |
| D-4 | 交付范围 | **铺底 + dev 预览 + prod 发布全链路** | 用户选定，不留半成品 |
| D-5 | Java 包名 | **固定 `com.zerocode.app`**（放弃 `com.<slug>.app`） | 用户选定；`scaffold.render` 只替换文件内容不改路径名，固定包名即零改动、零非法包段风险 |
| D-6 | Spring Boot 版本 | **锁 3.2.5**，低于旧规格 02 §4.3 的 `^3.3` | spike 用 3.2.5 实测全绿；不拿未验证版本进模板 |
| D-7 | health 探针形态 | **`/api/health`**（actuator + `management.endpoints.web.base-path: /`），放弃旧规格 02 §4.2 的 `/actuator/health` | spike 实测 `/api/actuator/health` 被 Security 拦成 **401**；改 base-path 后与 `prod.wait_prod_health` 已硬编码的 `/api/health` 同形，探针零改动 |

### 2.1 spike 实测数据（本机 2026-09-22，一次性验证容器 `spike-sb`，已清理）

| 项 | 实测 |
|---|---|
| 冷启动（空 .m2 → 依赖下载 + 编译 + 启动） | ≈ **410s** |
| 热重启（.m2 已缓存 89MB，重编译 + 启动） | **77s** |
| `POST /api/auth/login`（admin/admin123） | 200 + token，DB 经 zcnet 内 `zerocode-pg16`、Redis 经 zcnet 内独立容器 |
| `GET /api/actuator/health` | **401**（actuator 依赖缺失，被 `SecurityConfig` 白名单外兜底拦截） |
| Docker Hub 直连 | 超时；`docker.m.daocloud.io/library/maven:3.9-eclipse-temurin-17` 可拉取 |
| Maven Central | 容器内可达，实测均速 ~100–400 kB/s（无镜像加速） |
| 渲染地雷 | `.vue` 内**无** `{{word}}` 形态（不撞 `PLACEHOLDER`）；`public/favicon.ico` 是二进制，`read_text` 会抛 |

## 3. 模板产物

```
app/templates/vue3-springboot/
├── README.md                 # 栈说明：初始账号、首次预览耗时、改数据找 AI 要 sqlchange
├── schema/schema.sql         # 11 张 sys_* 表的幂等结构真相（基线文件里的第 12 张 schema_history 不带，见 §4）
├── sqlchange/
│   ├── README.md             # 沿用平台数据轨说明 + 菜单种子示例
│   └── D20260101_01_rbac_seed.sql
├── backend/
│   ├── pom.xml               # com.zerocode / artifactId {{SLUG}} / Boot 3.2.5 / +actuator
│   ├── Dockerfile            # 多阶段：maven:3.9-eclipse-temurin-17 build → eclipse-temurin:17-jre-alpine run
│   └── src/main/{java/com/zerocode/app/...,resources/{application.yml,logback-spring.xml,mapper/*.xml}}
└── frontend/
    ├── package.json / package-lock.json / tsconfig*.json / components.d.ts
    ├── index.html / vite.config.ts / nginx.conf / Dockerfile
    └── src/{App.vue,main.ts,style.css,env.d.ts,api,config,layout,router,store,style,utils,views}
```

**剔除**（base-pro 有、模板不带）：`backend/.scratch`、`logs/`、`uploads/`、`target/`、`frontend/dist`、`*.tsbuildinfo`、`vite.config.js`、`vite.config.d.ts`（tsc 产物）、`database/`（被双轨取代）、`docs/superpowers/`、`start-*.bat`、`.gitignore`（换成平台模板版：`target/`、`uploads/`、`dist/`、`node_modules/`、`*.tsbuildinfo`）。

**改造清单**（逐条可验）

| # | 位置 | 动作 |
|---|---|---|
| C-1 | `pom.xml` | `groupId com.zerocode`、`artifactId {{SLUG}}`、`name {{APP_NAME}}`；新增 `spring-boot-starter-actuator`；版本维持 spike 实测基线 |
| C-2 | 全仓 | 包名 `com.leerysink.base` → `com.zerocode.app`，目录树同步改名；`application.yml` 的 `type-aliases-package`、dev 的 `packages-to-scan`、`logging.level` 一并替换 |
| C-3 | 配置 | `application-dev.yml` + `application-prod.yml` + `application.yml` **合一为单一 `application.yml`**，全部外置为环境变量且**不给默认值**：`SERVER_PORT:8080`、`SPRING_DATASOURCE_URL/USERNAME/PASSWORD`、`SPRING_DATA_REDIS_HOST/PORT/PASSWORD`、`JWT_SECRET`、`UPLOAD_DIR`。base-pro dev 里的绝对路径 `D:/project/private/base-pro/uploads`、固定端口 8082、`password: ${PG_PASSWORD:postgres}` 全部消失 |
| C-4 | actuator | `management.endpoints.web.base-path: /`、`exposure.include: health`；配合 `server.servlet.context-path: /api` → 探针 URL 恰为 `/api/health` |
| C-5 | `SecurityConfig` | 白名单追加 `/health`（Spring Security 的 pattern 相对 context-path） |
| C-6 | 文案 | 启动横幅 `base-pro 基础管理后台 启动成功` → `{{APP_NAME}} 启动成功`，并**删掉横幅里的「接口文档」那一行**（knife4j 默认关，见 §7）；`index.html` title、登录页标题、`frontend/src/config/index.ts` 的 `appTitle` 吃 `{{APP_NAME}}` |
| C-7 | 前端 dev 接线 | `vite.config.ts`：`port 5173`、`host '0.0.0.0'`、proxy `/api → http://backend:8080`（compose 服务名），删掉本机 3001/localhost:8082 的说明注释 |
| C-8 | prod 接线 | 新增 `backend/Dockerfile`（`# syntax=docker/dockerfile:1` + `RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests package`）、`frontend/Dockerfile`（node build → nginx:1.27-alpine + `COPY nginx.conf`）、`frontend/nginx.conf`（`/api/ → http://backend:8080`、`client_max_body_size 50m` 对齐 `spring.servlet.multipart.max-request-size`、`try_files ... /index.html`） |
| C-9 | 二进制资源 | `favicon.ico` 原样保留，依赖 §5.1 的渲染器改造才能安全入仓 |

## 4. 数据库双轨

base-pro 的 `database/init-postgres.sql`（297 行、12 表 + 索引 + COMMENT + 种子 + setval）拆成平台两轨：

- **结构轨 `schema/schema.sql`**：`sys_dept`/`sys_user`/`sys_role`/`sys_user_role`/`sys_role_dept`/`sys_menu`/`sys_role_menu`/`sys_dict_type`/`sys_dict_data`/`sys_oper_log`/`sys_login_log` 全部转成幂等形态（`CREATE TABLE IF NOT EXISTS` + 逐列 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` + `COMMENT ON`），**不含任何数据、不含 DROP**。sqlrunner 每次预览/发布先 fold 一遍，故结构永远收敛。
- **数据轨 `sqlchange/D20260101_01_rbac_seed.sql`**：部门三层、`admin`（BCrypt of `admin123`）、角色 admin/common、`sys_menu` 目录+菜单+按钮 F 类、`sys_role_menu`（admin 全量、common 限部门/字典）、样例字典，以及 6 条 `setval` 序列前移。全部 `ON CONFLICT ... DO NOTHING`，幂等可重放。文件名日期**固定为 20260101**（不经占位替换——渲染器不改路径名），保证永远排在应用期新增文件之前，且账本只跑一次。
- **删除 `schema_history` 表及其种子登记**：base-pro 自带的 SQL 账本与平台账本 `sqlchange_history` 语义重复，两套并存会双记账。平台侧不认这张表，模板里不留。

## 5. 平台侧改动

### 5.1 `app/deploy/scaffold.py`（两处，非做不可）

- **demo 种子按栈让位**：现无条件写 `sqlchange/D{TODAY}_01_seed.sql`（`INSERT INTO demo_notes`），springboot 栈无 `demo_notes` → 首预览迁移必红。改为**渲染前探测模板 `sqlchange/` 是否已自带 `*.sql`**，自带即不追加 demo 种子（栈无关规则，vue3-fastapi 行为不变）。
- **二进制文件按字节 copy**：现对每个文件 `read_text(encoding="utf-8")`，`favicon.ico` 会抛 `UnicodeDecodeError` → 创建失败回滚。改为 UTF-8 解不开即 `read_bytes/write_bytes` 原样 copy；占位符残留自检只对文本文件执行。

### 5.2 `app/services/app_service.py`

`_check_scale_stack` 的 R-C 闸门随模板落地自然放行（无需删码）；`HEALTH_BY_STACK["vue3-springboot"]` 由 `/actuator/health` 改为 `/api/health`（D-7）。当前无任何已创建的 springboot 应用，故 `app.json` 无历史值需迁移。

### 5.3 `app/deploy/compose.py`

- **dev**：新增 springboot 分支（`_DEV_COMPOSE_SB` + `_REDIS_SVC_SB` + `_BACKEND_SVC_SB`）。migrate 片段完全复用 `_MIGRATE_SVC`；`frontend` 与 fastapi 栈同形；`backend` 用 `maven:3.9-eclipse-temurin-17`、`../backend:/src`、per-app 命名卷 `m2:/root/.m2`（compose 自动加项目前缀 → 天然隔离，不与其他应用共享，避免 maven 跨容器无锁并发写坏缓存）、`command: mvn -B spring-boot:run`，`depends_on: {migrate: service_completed_successfully, redis: service_started}`；jdbc URL 在 compose 内拼：`jdbc:postgresql://zerocode-pg16:5432/${DEV_DB}?currentSchema=public&stringtype=unspecified`。
- **prod**：`render_prod_compose` 栈闸门放行 `vue3-springboot`（新增 `_PROD_COMPOSE_SB`，含 redis + uploads 命名卷 `/uploads` + 两个 build），`ensure_deploy_files` 落 `prod-compose.yml` 的条件同步由「== fastapi」改为「∈ 已支持发布栈」。
- **`.env` 密钥注入**：`_ENV_TMPL` 增 `JWT_SECRET`、`REDIS_PASSWORD` 两键，语义为**缺则生成、有则保留**（`secrets.token_urlsafe(32)`；复用 `_env_get/_env_set` 的单向推进纪律，并把两键加入 `_CRED_KEYS` 的保留集合，否则发布重渲染会冲空）。口令不进数据库、不进仓库、AI 禁读（`deploy/` 已是禁区）。
- redis 服务：`redis:7-alpine`，`command: ["redis-server","--requirepass","${REDIS_PASSWORD}"]`，**不映射宿主端口**，仅走 compose 内部网络。

### 5.4 `app/deploy/engine.py` / `prod.py` / `settings.py`

- dev 健康：large 栈从「只看前端 `/`」升级为「`/` 与 `/api/health`（经 vite 代理打到 backend）」联合判定，杜绝「预览已就绪、后端还在编译」的假绿；fastapi 栈探针不变。
- `settings.DEFAULTS` 新增 `deploy.health_timeout_large = 900`（冷启动实测 410s，留一倍余量）；起栈日志明示「首次启动需下载 Maven 依赖，可能数分钟，请勿重复点重启」。
- `prod._ensure_prod_proxy_conf`：nginx.conf 与 Dockerfile 接线源改为**按 `meta["stack"]` 取模板**（fastapi 存量行为与产物 diff 为零）。

### 5.5 `app/deploy/contract.py`（按栈裁剪，不动 fastapi 文案）

给 springboot 栈追加四条：

1. **新增业务页三件套**：`frontend/src/views/<模块>/index.vue` + `router/index.ts` 注册路由 + `sqlchange/D*.sql` 补 `sys_menu`（含按钮 F 类）并 `sys_role_menu` 授权给角色 1。缺第三件会被路由守卫打成 403（路由静态注册、菜单控可见性——这是 spike 读码确认的事实）。
2. Controller **不要再写 `/api` 前缀**（`server.servlet.context-path` 已是 `/api`）；统一返回 `Result`，分页 `PageResult`，实体走 MyBatis-Plus 约定（snake_case 表/列、驼峰实体、逻辑删除 `deleted`）。
3. 基座类只可追加不可重写：`LoginUserCache`、`JwtUtils`、`SecurityConfig`、`GlobalExceptionHandler`、`Result`、`PageResult`。
4. `sys_*` 基表不得改名/删列（会连带打断登录、侧栏与操作日志）；新表新列仍走 `schema/schema.sql` 目标态。

`ENV_HINT` 对应改为模板真实 env 名（`SPRING_DATASOURCE_URL/USERNAME/PASSWORD`、`SERVER_PORT`、`SPRING_DATA_REDIS_HOST/PASSWORD`、`JWT_SECRET`、`UPLOAD_DIR`）。

### 5.6 前端

向导已允许「大型 + vue3-springboot」（`AppsView.vue:81/428`），无功能改动；仅在规模卡片文案补「首次预览需数分钟（下载依赖）」。

## 6. dev / prod 编排形状（最终产物）

dev（`deploy/dev-compose.yml`，平台渲染，应用不可写）：

```
name: zc-<slug>-dev
services:
  redis    : redis:7-alpine                     --requirepass ${REDIS_PASSWORD}
  migrate  : zerocode-sqlrunner:1               ../schema:/schema:ro  ../sqlchange:/sql:ro
  backend  : maven:3.9-eclipse-temurin-17       ../backend:/src + m2:/root/.m2  mvn -B spring-boot:run
               SPRING_DATASOURCE_URL=jdbc:postgresql://zerocode-pg16:5432/${DEV_DB}?...  USERNAME=${DEV_USER}  PASSWORD=${DEV_PASSWORD}
               SPRING_DATA_REDIS_HOST=redis  PASSWORD=${REDIS_PASSWORD}  SERVER_PORT=8080  JWT_SECRET=${JWT_SECRET}  UPLOAD_DIR=/src/uploads
               networks [default, zcnet]  depends_on {migrate: completedSuccessfully, redis: started}
  frontend : node:20-alpine                     ../frontend:/src + fe_node_modules  5173→__DEV_PORT__
volumes: m2, fe_node_modules
```

prod 与之同构，差异照旧：镜像即产物（`zc-<slug>-backend:prod-${PROD_VERSION}` / `-frontend`）、不挂源码、uploads 与 redis 用命名卷、仅前端 `__PROD_PORT__:80` 对外、migrate 闸门指 `PROD_*`。

## 7. 安全与默认值

- 初始账号 `admin` / `admin123`（沿用 spike/基座既有 BCrypt 种子，dev+prod 同）。登录页常驻一行提示「初始账号 admin / 初始密码 admin123，请登录后在个人中心修改」，模板 README 同记一次。
- actuator 仅 `exposure.include=health`，不暴露 env/beans/metrics 等。
- `JWT_SECRET`、`REDIS_PASSWORD`、数据源口令**三处无默认值**：漏配即启动失败，不留静默降级路径；密钥由平台生成于 `deploy/.env`，仓库与 AI 都看不到。
- **接口文档与 SQL 日志默认关闭**：三份 yml 合一后不再有 profile 分叉，`knife4j.enable: false`、`springdoc.api-docs.enabled: false`、MyBatis 走 `NoLoggingImpl` 即模板默认态；预览期想看 SQL 或想开 doc.html，由用户让 AI 就地改 `backend/src/main/resources/application.yml`（应用工程内的配置文件 AI 可编辑，不属 `deploy/` 禁区）。不给这类开关预留环境变量——需要时改文件即可。
- 文件上传目录：dev 落宿主 `backend/uploads`（源码挂载，重启不丢），prod 落 named volume `/uploads`（跨版本持久）。

## 8. 测试与验收

**单测（不留纸面通过）**

- `test_scaffold`：springboot 模板渲染零 `{{` 残留、不生成 `demo_notes` 种子、`favicon.ico` 字节一致（md5 比对）、`D20260101_01_rbac_seed.sql` 落在渲染产物内。
- `test_app_service`：`scale=large + stack=vue3-springboot` 放行并写出 `health.backend=/api/health`。
- `test_compose_render`：springboot dev/prod 产物含 redis 服务与 `m2` 卷、jdbc URL 拼装正确、渲染标记零残留；fastapi 存量产物 diff 为零（回归钉）。
- `test_contract`：springboot 栈契约文本含三件套条款与「不要重复 /api 前缀」，fastapi 文本不变。
- `test_engine`（docker 可用时）：large 栈 dev 联合健康判定生效。

**端到端（人工 + docker，逐条给证据）**

1. 向导创建大型应用 → 起预览 → 冷启动 ≤900s 内登录成功 → 七张基座页面全部可打开。
2. 让 AI 加一个「带建表的 CRUD 页面」：新表进 `schema/schema.sql`、菜单进 `sqlchange/D*`、重启后侧栏可见、增删改查可用。
3. 点「发布」：build → prod migrate → up → `/` 与 `/api/health` 双 200 → 版本晋升；第二次发布显著快于第一次（BuildKit/.m2 缓存生效）。
4. `pytest tests/ -q` 全绿；`frontend` 相关测试与构建不破（`npm run test` / `npm run build`）。

## 9. 与旧规格 02 的差异回执

| 旧规格文本 | 本文 | 原因 |
|---|---|---|
| §4.2 「`/actuator/health`」 | `/api/health`（actuator base-path `/`） | D-7，spike 实测 401；与 prod 探针硬编码同形 |
| §4.3 「spring-boot ^3.3」 | 3.2.5 | D-6，实测基线 |
| §4.2 「包名 `com.<slug>.app`」 | `com.zerocode.app` | D-5，渲染器不改路径 |
| FR2-06 「M3 补齐三栈」 | 本片只交付 vue3-springboot，`vue3-node`/`static` 继续 400 | 范围收敛 |

规格 02 的这四处在实现片里同步改注（不改需求编号，只改文本与状态）。

## 10. 风险与已知取舍

| 风险 | 影响 | 处置 |
|---|---|---|
| 冷启动 410s 系本机单次实测，无 Maven 镜像加速 | 首预览等待长，用户可能重复点重启 | `health_timeout_large=900` + 日志明示 + 前端文案；后续可做 go-offline 预热镜像 |
| `maven`/`eclipse-temurin` 镜像 Docker Hub 直连不可达 | 用户机拉不到 → 起栈失败 | README 前置条件写清加速源取法（与 `zerocode-pg16` 同节）；失败日志走既有 compose 错误面 |
| 模板 ≈140 文件入仓，base-pro 上游改进不回流 | 双份维护 | 明确按 fork 快照管理，模板自身的 bug 在本仓修 |
| 每个大型应用一份 .m2（实测 89MB+，全量约 300–500MB） | 磁盘占用 | 刻意的隔离代价；共享预热留后续 |
| prod build 首次也要下依赖 | 首次发布慢 | BuildKit cache mount 同机复用；验收项 3 明确「第二次显著快于第一次」 |
| 初始口令 admin/admin123 且 prod 同值 | 发布到共享网络后可被猜 | 登录页常驻改密提示；真正的收口（首登强制改密）若要做，另立需求，不在本片偷偷加 |
| AI 未来在 `.vue` 里写 `{{count}}`（无空格） | 只影响创建期渲染，存量应用不受影响 | 模板基线已验证零命中；契约不额外展开（渲染是一次性的） |

## 11. 实施切片预告（供 to-tickets / writing-plans 展开）

1. 模板产物落仓（含双轨 SQL、Dockerfile 三件、包名与配置合一改造）+ `scaffold.render` 两处改造。
2. dev 编排：`_DEV_COMPOSE_SB`/redis/m2 卷 + `.env` 双密钥 + large 栈联合健康与超时 + 端到端能登录。
3. prod 编排与发布链：`_PROD_COMPOSE_SB` + 闸门放行 + `_ensure_prod_proxy_conf` 按栈 + 端到端能发布。
4. 契约与文案：`contract.py` 栈裁剪 + `HEALTH_BY_STACK` + 前端向导提示 + README/规格 02 差异改注。

每片都必须带自己的验收证据（compose 产物断言 + 真跑记录），不允许「前三片纸面绿、第四片才第一次启动」。
