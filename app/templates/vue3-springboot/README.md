# {{APP_NAME}}（{{SLUG}}）

zerocode 大型应用（Vue3 + Spring Boot 3.2.5 管理后台），自带登录 / 部门 / 用户 / 角色 / 菜单 / 字典 /
操作日志 / 登录日志全套 RBAC 基座，业务页面在这套基座上往上加。

- 本机访问（dev 预览）：http://localhost:{{DEV_PORT}}
- 数据库：dev = `{{DEV_DB}}`，prod = `{{PROD_DB}}`（由平台自动供给）
- 初始账号 `admin` / `admin123`（登录页也有提示），**首次登录后请到「个人中心」改密**

## 开始之前

- **首次构建要等几分钟**：后端镜像第一次要把 Maven 依赖全部下载下来（后端 `Dockerfile` 里那句
  「冷启动 410s」就是这个），它挂了 BuildKit 缓存卷复用容器里的 `/root/.m2`，之后的构建不再重下
  （改了 `pom.xml` 的依赖版本或缓存被清掉时除外）。期间日志面板一直滚，**不要重复点「重启」**。
- **起好之后再重启约一分半**：依赖已进缓存卷、不再重下，实测「停止 → 再起 → 前端与后端 `/api/health`
  同时 200」就绪耗时约 **90s**（首次那几分钟的冷启动只发生一次）。
- **正式环境首次发布约 3 分 17 秒，二次发布约 1 分 23 秒**：本机实测（prod 端口 8216、lailiao 栈）——
  一次「一键发布」的七步（preflight → build → provision → migrate → deploy → health）冷缓存耗时
  **197s**（后端镜像第一次把整套 Maven 依赖下进 BuildKit `/root/.m2` 缓存卷），改一处前端文案再发布的
  第二次仅 **83s**（依赖与 `npm ci` 层全部命中缓存，只重跑前端 `npm run build`）。发布走 Task C3 的
  `deploy.build_timeout_large`（1800s）上限，冷构建远超旧的 600s 默认会被误杀成「镜像构建失败」。
  回滚只切程序（`PROD_VERSION` 回指旧 tag、容器起旧镜像），**数据库永不回滚**——发布期写入的数据仍在。
- 拉不到基础镜像（后端 `maven:3.9-eclipse-temurin-17` / `eclipse-temurin:17-jre-alpine`，前端
  `node:20-alpine` / `nginx:1.27-alpine`）时说明本机直连 Docker Hub 不通，先配镜像加速源再重试。

## 目录

- `frontend/` — Vue3 + TS + ant-design-vue；开发期由 vite 代理 `/api` → 后端容器
- `backend/` — Spring Boot 3.2.5，包名固定 `com.zerocode.app`
- `schema/schema.sql` — **结构真相**（声明式目标态，每次预览/发布前全量重放）→ 细则见 `schema/README.md`
- `sqlchange/` — **数据轨**（幂等种子与回填，一经应用不得回改）→ 细则见 `sqlchange/README.md`
- `deploy/` 与 `app.json` — 由 zerocode 平台生成与管理，请勿手改

## 三条最容易踩的规矩

1. **接口路径不要再写 `/api` 前缀**：`server.servlet.context-path` 已是 `/api`，你写 `/auth/login`，
   对外就是 `/api/auth/login`（上传件同理：`/uploads/**` 对外是 `/api/uploads/**`）。前端 axios 的
   `baseURL` 已是 `/api`，两边都别再加一层。
2. **加业务页是三件套，缺一件页面点不开**：
   ① `frontend/src/views/<模块>/index.vue` 并在 `frontend/src/router/index.ts` 注册路由；
   ② `backend` 的 `controller` / `service` / `mapper` / `entity`；
   ③ 往 `sqlchange/` 追加一个 `D<YYYYMMDD>_<NN>_menu_<模块>.sql` 补菜单并授权。
   路由是静态注册的、菜单只决定「侧边栏可见 + 守卫放行」，漏掉第③件能编译能启动，但点进去是 403。
3. **环境差异一律走环境变量，代码里不要写死**：数据源、Redis 地址、`JWT_SECRET`、上传目录都由平台注入。
   这些键刻意不留默认值——缺了就让启动直接失败，不留「静默连上本地库 / 用仓库公开密钥签 token」的降级路径。

## 常用改动位置

- 想看 SQL 日志：`backend/src/main/resources/application.yml` 里把 `mybatis-plus.configuration.log-impl`
  改成 `org.apache.ibatis.logging.stdout.StdOutImpl`（默认不打日志）
- 想开接口文档：改 `application.yml` 三处——`springdoc.api-docs.enabled`、`springdoc.swagger-ui.enabled` 改 true，
  并把 `knife4j.production` 改 false（**真正屏蔽文档的是它**，只翻 `knife4j.enable` 出不来）。
  `SecurityConfig` 已预先放开 `/doc.html`、`/swagger-ui/**`、`/v3/api-docs/**`、`/webjars/**`，无需再改；
  对外完整地址同样带 context 前缀：`http://localhost:{{DEV_PORT}}/api/doc.html`
- 想改结构：只编辑 `schema/schema.sql` 的目标态；要改数据或加菜单，才往 `sqlchange/` 追加 `D*.sql`
