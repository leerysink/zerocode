# 移动端应用栈（uni-app H5 + APK 代管产物）规格说明

**日期**：2026-11-03  **来源**：grill-with-docs 拷问访谈（8 条核心决策逐条拍板）  **ADR**：[0039 移动端与后端同应用、H5 走同域 `/h5/`，APK 由平台代管产物而不由平台构建](../adr/0039-mobile-uniapp-same-app-h5-path.md)  **术语**：[CONTEXT.md](../../CONTEXT.md) 的「后台」「移动栈」「H5 端」「APK 壳」「移动端用户角色」「真机预览码」「移动端弹框」七节

**实现状态**：已实施（2026-11-04，分支 `feature/mobile-uniapp-stack`）。代码、模板与自动化测试均已落地；**人工验收仍未完成**：真机扫码打开 `/h5/`、HBuilderX 云打包（R-1）、prod 构建时长与镜像体积实测（R-2）。R-3/R-4/R-5 的结论已回填 §7。

## 1. 问题陈述

平台今天只能生成「浏览器里的桌面尺寸网页」。用户说「我要一个能装在手机上、扫码就开的业务应用」时，三个地方同时没有出口：创建应用的技术栈列表里没有移动形态，Agent 的栈契约里没有移动端约定（rpx、`uni.*` 的条件编译、H5 路由 base），sidecar 验证只有桌面视口。结果是移动需求只能被降级成「把网页在手机上打开」。

更深的一层是接口。移动端的业务页必然要调后端，而平台**没有任何跨应用通路**：每个应用独享 `{slug}_dev` / `{slug}_prod` 库与角色，网关 `routes.json` 只登记 `status_prod='running'` 的应用，模板后端与前端 nginx 都没配 CORS，生成应用与平台之间更是零调用关系（小栈后端匿名，大栈的 JWT 只在应用内部）。于是「移动端怎么落地」这个问题，真实形态是「平台要不要为跨应用调用长出地址绑定、CORS、跨应用凭据、依赖级联启停、包依赖这一整套能力」。

**本规格的裁定是：不长。** 移动端与它的后端住进同一个应用，靠同域路径分流，不新增地址。

## 2. 方案概述

- **移动栈**：一个应用内容三件套——应用后端服务（`vue3-springboot` 底座）、后端管理系统（`admin web`，住根路径 `/`）、uni-app 工程（编译出 H5）。它是**一个** compose 项目、**一对**端口、**一套**库。
- **H5 端**挂同域 `/h5/`：manifest 的 `h5.router.base=/h5/` + `h5.router.mode=hash`，dev 期由一个**不发布端口**的 uni-app dev server 服务承载、管理端 vite 代理转发；prod 期多阶段构建后烘进前端镜像的 `html/h5`。接口一律同源相对 `/api`。
- **账号复用 `sys_user`**：移动端注册即建后台账号，挂一个受限的「移动端用户」角色；配套硬约束是后台用户列表默认过滤该角色。
- **APK 壳**平台不构建，改为**代管产物**：用户上传 → 该应用 prod 的 `uploads` 卷 → 走应用自己域名下载 + 二维码。
- **登记方式**：新的 stack key，但**不复制模板目录**——`scaffold` 先渲染 springboot 基底、再渲染 mobile 覆盖层。
- **上线即开放**，不挂平台级灰度开关。

### 接缝（本规格在哪些稳定接口上验证）

刻意把变更压在最少的接缝上。四个主接缝，全部**复用已有**靶心：

1. **`compose.render_dev_compose` / `render_prod_compose` 的输出形态**——纯函数、不碰 Docker。既有 `tests/test_compose_render.py::test_only_frontend_publishes_ports` 正是本方案的核心不变式守卫（「只有前端容器发布端口」），移动栈新增形状断言后它自动覆盖新栈。**端口模型因此不许改**：`ports.py::allocate_pair` 不动。
2. **`scaffold.render_layers` 的多源叠加**——本规格唯一新增的机制。`render` 保持不变（单目录），新增 `render_layers` 依次对若干模板目录调 `render`，测试接缝不变（tmp_path 真实渲染 + 残留自检）。
3. **`contract.py` 的栈文案**——已有 `test_contract.py` 的小栈「字节不变」回归保护，新增移动栈用例与它并列。
4. **应用自己的 `/api/uploads/**`**——APK 产物落点复用大栈既有的上传链路与 `uploads` 卷，**平台不新增对外端口、不新增存储位**。

反过来，以下模块**本规格明确不跨**：`gateway_routes.py`（routes.json 的 `{slug: prod_port}` 映射逐字不变）、`nics.py`（地址投影单点，H5 与 APK 都读它）、`prod.py::STEPLIST`（发布七步不增不减，只可能多探一条路径）、`ports.py`。

## 3. 用户故事

### 创建与脚手架

- **US-01** 作为用户，我在创建表单选「移动端应用」，得到 `backend/ frontend/ schema/ sqlchange/ deploy/` 五套目录，其中 `frontend/mobile/` 是 uni-app CLI 工程（见 D-12）；`app.json` 的 `stack` 是新 key，端口仍是**一对**（dev 奇 / prod 偶）。
- **US-02** 作为平台，脚手架渲染 mobile 覆盖层后，`frontend/mobile/src/manifest.json` 的 `h5.router.base` 已是 `/h5/`、`h5.router.mode` 是 `hash`，且全目录无 `{{KEY}}` 残留（覆盖层同受残留自检约束）。
- **US-03** 作为平台，覆盖层与基底在同一路径冲突时**覆盖层胜**；而 `backend/src/` 只有一份、来自基底模板——仓库里不许出现第二份 springboot 后端代码。
- **US-04** 作为 Agent，我读到的栈契约里写着移动端约定（`uni.*` 要条件编译、rpx、接口写相对 `/api`、H5 路由 base 是 `/h5/`）；我改一个小栈应用时读到的文案与今天**逐字节相同**。

### 预览（dev）

- **US-05** 作为用户，我启动预览后在手机浏览器打开 `http://{宿主 IP}:{dev_port}/h5/`，看到的是移动端页面，而对外**只有 `dev_port` 一个端口**（dev compose 里 mobile 服务没有 `ports:` 段）。
- **US-06** 作为开发者，我改 `frontend/mobile/src/pages/**` 的源码，手机上的页面热更新（代理带 `ws: true`）。
- **US-07** 作为移动端页面，我用相对地址 `/api/xxx` 调后端并拿到数据——dev 期这条代理链是「管理端 vite → `backend:8080`」，与后台管理系统共用一条。
- **US-08** 作为用户，当 `ZEROCODE_PREVIEW_HOST` 是 `localhost`（打包默认值）时，真机预览码**置灰**并写明「要真机调试请把它配成宿主 IP」，不给我一张扫不开的码。

### 发布与 prod

- **US-09** 作为用户，我点发布，前端镜像里烘进了 `html/h5`；发布成功后 `https://{slug}.zerocode.com/h5/` 在手机上直接打开。
- **US-10** 作为运维，`gateway/routes/routes.json` 的内容形态不变（仍是 `{slug: prod_port}`），网关与证书不需要任何改动。
- **US-11** 作为平台，H5 端与后台管理系统打的是**同一个后端、同一套库**（`{slug}_prod`），一次发布同时更新两端，不存在「移动端落后一版」这种状态。

### 账号与登录

- **US-12** 作为终端用户，我在 H5 里注册，`sys_user` 多一行且只挂「移动端用户」角色，没有任何后台菜单。
- **US-13** 作为管理员，我在后台用户列表里默认看不到这些 C 端用户；显式筛选该角色时才出现。
- **US-14** 作为终端用户，我登录走 `/api/auth/login`，没有验证码拦路（`/auth/captcha` 是恒返回 `enabled: false` 的占位），拿到的 JWT 与后台账号过同一个 `JwtAuthenticationFilter`、同一个 Redis `LoginUser` 快照。

### APK 壳

- **US-15** 作为开发者，我在平台外把工程打包成 APK（本期不由平台构建），在移动端弹框里上传，产物落进该应用 prod 的 `uploads` 卷。
- **US-16** 作为用户，我拿到形如 `https://{slug}.zerocode.com/api/uploads/mobile/{slug}.apk` 的下载地址与二维码；**重建 prod 镜像后它仍然能下载**（卷不随镜像销毁）。
- **US-17** 作为平台，应用还没发布过（`status_prod` 非 `running`）时，上传入口给出明确阻断理由，而不是隐式替我起栈或发布。
- **US-18** 作为开发者，我拿到的打包指引里两种地址形态都写清了：SSL 开时 `https://{slug}.{GATEWAY_DOMAIN}`、SSL 关时 `http://{ZEROCODE_PREVIEW_HOST}:{prod_port}`——APK 不在同源环境里，必须填绝对域名。

### 入口与验证

- **US-19** 作为用户，应用卡面 ☰ 菜单与聊天页「更多」的发布与观测段各有一个移动端入口，点开是**同一个**居中弹框组件（dev/prod 切换 + H5 地址与真机预览码 + APK 上传与下载行）。
- **US-20** 作为 Agent，我用 `preview_page` / `browser_open` 带视口参数（如 `device: "iPhone 14"`）验证 `/h5/`，拿到的是窄屏渲染结果；不带参数时与今天一致是桌面视口。
- **US-21** 作为 Agent，两条路径（只读快检与交互走查）仍然共用**同一个采集与裁剪函数**，视口只影响 context 的建立，不影响采集逻辑。

## 4. 已定决策

| # | 决策 | 被否的备选与否决依据 |
|---|---|---|
| D-1 | 交付形态 = H5 + 原生 APK 分发 | 只做 H5（丢掉真机原生需求）；小程序为主（要 appid 与 HTTPS 合法域名备案，正面撞本机自签 `zerocode.com` 证书） |
| D-2 | 移动端与后端**同一个应用** | 拆两个应用——要求平台先长出应用间地址绑定/CORS/跨应用凭据/依赖级联启停；只做移动端复用平台后端——要求先做「平台开放 API + 应用身份 + 配额」，那是独立里程碑 |
| D-3 | H5 挂 `/h5/`，hash 路由 | `/m/`（国内语境指独立移动站域名，与网关一级子域混淆）；根路径 `/` 让管理端挪 `/admin/`（要动模板 publicPath 与既有约定，影响面更大）；子域名 `{slug}-h5.*`（`router.lua` 取一级子域当 slug，直接路由未命中） |
| D-4 | 底座 = 大型档 `vue3-springboot` | 小型档 `vue3-fastapi`——其模板后端匿名、无鉴权代码，移动端一登录就要重造整套安全体系 |
| D-5 | 新 stack key + **模板叠加层** | 复制整份模板（仓库里两份 springboot 后端代码，必然漂移）；改成 `mobile: true` 标志位（形态真源从「栈」变成「栈 + 标志」两个，且要处理存量应用补铺目录） |
| D-6 | 账号**复用 `sys_user`** + 受限角色 | 独立 `app_user` 体系（要改 `JwtAuthenticationFilter` 认两种身份，动大栈安全底座）；本期不做登录（生成的应用只能内部人用，后续补注册要返工） |
| D-7 | APK 平台**代管产物**（上传 + 下载页 + 码） | 只交源码与指引（用户拿不到分发）；只做下载页不做上传（表现为「码指向 404」）；本期不碰 APK（与 D-1 冲突） |
| D-8 | dev 多一个**不发布端口**的服务 + vite 代理 | watch 构建写进静态目录（没 HMR，改一行等一次完整构建）；给移动端多开 dev 端口（一应用两对端口，端口池 8210-8399 腰斩，且推翻 D-2 的拓扑约束） |
| D-9 | sidecar **加视口参数** + 契约提醒 | 按栈自动切手机视口（移动栈同样住着管理端 `/`，默认切窄屏会误伤后台页验证）；本期不做视口验证（移动端的主要 bug 类型恰好是工具看不见的） |
| D-10 | 移动端弹框**两处入口共用一个组件** | 只放卡面（真机调试要退回列表再点两下）；内嵌进已有弹框（把「运维观测」与「交付地址」挤进同一容器）；行内独立按钮（行内已有分段约定，再加会挤满右上角） |
| D-11 | **直接开放**，不挂灰度开关 | 平台级开关默认开/默认关——代价已接受：出问题只能回退代码重发版。也因此本规格**不新增任何 `app_config` 平台键**，实现时不得顺手加（加了就必须同步 conftest 的种子，见 §7） |
| D-12 | uni-app 工程住在 `frontend/mobile/`，**不占顶层目录** | 顶层 `mobile/` 目录——prod 前端镜像的构建上下文是 `frontend/`，H5 要进同一镜像就得再挂一个构建上下文或改 build 参数；且 `.dockerignore`、文件树与「前端构建产物都在 frontend」的既有口径会分叉。住 `frontend/mobile/` 让 `COPY mobile/` 与 `COPY --from=mobile-build` 都落在同一个上下文里，代价只是给管理端 vite 锁死 `optimizeDeps.entries`（否则它会扫 `mobile/index.html` 当第二入口） |

## 5. 测试决策

主接缝是「纯函数渲染 compose」与「脚手架真实渲染到 tmp_path」——它们能在不启动 Docker、不做云打包的前提下覆盖本规格绝大部分架构断言。

- **compose 形状**（`tests/test_compose_render.py`）：沿用 **springboot 的 inline 形状断言风格，不新建 golden 文件**——`tests/golden/` 目前只有 fastapi 与 static 两份完整 yml，再加第三份就多一处要人肉同步的真相。必测四条：mobile 服务无 `ports:` 段；`test_only_frontend_publishes_ports` 把移动栈纳入参数化后仍通过；prod 前端镜像构建上下文含 mobile；`ensure_deploy_files` 对移动栈写 prod compose（`PUBLISHABLE_STACKS` 登记后的门）。
- **不变式的反向断言**（最值钱的一条）：`routes.json` 导出对移动栈应用的输出**只有一条 `{slug: prod_port}`**。这条测试存在的意义是防止将来有人给移动端加第二个发布端口而悄悄破坏网关契约。
- **脚手架叠加**（`tests/test_scaffold.py`）：基底与覆盖层的覆盖顺序；无残留；`backend/src` 只有一份；覆盖层缺文件时不报错（叠加不是合并）。
- **模板体检**（`tests/test_springboot_template.py` 风格）：`frontend/mobile/` 必备文件齐、`manifest.json` 可解析且 `h5.router.base` 为 `/h5/`、`nginx.conf` 含 `location /h5/`、管理端 vite 配置含 `/h5` 代理且带 `ws`。**`nginx.conf` 必须是 LF 换行**（本仓库既有踩坑：CRLF 会让容器启动失败），判定要用按字节读、别用 Git Bash 的 `grep`/`od`。
- **契约**（`tests/test_contract.py`）：移动栈文案含约定；小栈与大栈文案**逐字节不变**（已有同类保护，直接扩展参数）。
- **市场打包**（`app/store/packages.py`）：`unpackage/` 进排除名单；`EXCLUDE_*` 四份名单会被写进包 manifest，改动要同步断言。
- **APK 上传端点**（pytest API 层）：体积上限判定、`status_prod` 非 running 时阻断、落点确实是 `uploads` 卷、下载 URL 形态。**不测真实云打包**，那是 R-1 的事。
- **前端**（vitest）：`appForm.test.js` 栈登记；`AppsView.test.js` ☰ 菜单新项；`ChatAppActions.test.js` 更多段新项；新组件测试沿用 `MetricsModal.test.js` 的居中弹框风格。本仓库 VTU 有四条已知坑（二次 mount、共享监听器 autoUnmount、桩按 id、`trigger` 改不了 PointerEvent 只读属性），桩要照现有约定写。
- **全量命令**：后端 pytest **必须串行**（共享 `zerocode_test` 库，并发 TRUNCATE 会死锁）；前端全量 vitest 用 `--maxWorkers=4`（默认 60 worker 会偶发超时假失败）。
- **人工验收（唯一不可替代的一步）**：真机扫码打开 `/h5/`；HBuilderX 云打包跑通一次并回写 R-1 结论；`uni build` 与 `mvn package` 同时进 prod 构建后的**实测时长与镜像体积**（见 R-2，规格不承诺数字，只承诺「量出来并写在这里」）。

**「完成」的判据**：一个移动栈应用能创建 → 手机上扫码看到 H5 → 注册与登录跑通 → 一次发布同时更新两端且 `routes.json` 只有一条 → 上传的 APK 在镜像重建后仍可下载 → 全量 pytest 与 vitest 绿。任何一条不成立就不算完成，不接受「代码写完了但没真机验过」。

## 6. 明确不做

- **微信小程序 / 快应用**：需要 appid、开发者工具人工上传审核，且要求 HTTPS 合法域名并在微信后台备案——与本机自签证书正面冲突。
- **平台内构建 APK**：HBuilderX 云打包依赖 DCloud 账号与其工具链，容器里跑不起来。
- **`uniCloud`**：接口与数据会搬到平台管不到的第三方，与「一应用一库、凭据由平台供给」冲突。已在 CONTEXT 里禁止文档出现「uniCloud 作为可选后端」。
- **跨应用调用能力**（应用间地址绑定、CORS、跨应用数据通路、依赖级联启停、包依赖）：这是被 D-2 否掉之后本不该出现的整块能力，本规格不为其开任何口子。
- **平台开放 API 给生成应用调用**：独立里程碑。
- **App 原生能力**：推送、蓝牙、离线包、深度链接——这些正是 APK 相对 H5 的收益，本期一个都不做。
- **存量应用补开移动端**：只有新创建的移动栈应用带 `frontend/mobile/`。
- **灰度开关 / 平台配置键**：见 D-11。
- **小型档移动栈**（fastapi 底座）：见 D-4。
- **prod 页面验证**、**独立子域名 H5**、**端口模型改动**。

## 7. 补充说明：五条事实核对（R-1/R-2 仍待人工验收）与三条易踩的坑

**R-1（能动摇 D-7）uni-app CLI 工程能否进 HBuilderX 云打包。** Docker 构建要求工程是 **CLI 工程**（有 `package.json`、`npx uni build -p h5`），而云打包习惯的是 **HBuilderX 工程**。这是两套目录结构，冲突点就在我们要同时满足「容器里能构建」和「用户能云打包」。排为**第一颗示踪弹**：先手工建一份 CLI 工程，走一遍「CLI 构建 H5 成功」+「HBuilderX 打开该目录并云打包成功」。失败则 D-7 的实现退化为「交付源码 + 手动导入指引」，拓扑决策（D-2/D-3/D-5）不受影响。**结论：待人工验收**（CLI 构建 H5 已在 prod 镜像里跑通，HBuilderX 云打包未实测）。

**R-2（成本可能超预期）prod 构建期同时存在 npm 与 Maven。** npm 侧沿用 ADR-0025 的 `build.args` 换源通路；**Maven 仓库镜像至今是缺口**（CONTEXT「依赖镜像源」一节明记）。uni-app 的 `node_modules` 体积与 springboot 的 maven 依赖叠加后，构建时长与镜像体积都会涨——先量出真实数字再决定是否要给 Maven 补换源位，别在没数字时就改端口或公开地址来绕。**结论：待人工实测**，数字回填本节。

**R-3（APK 落点口径）——已裁定并实现。** dev 侧没有 `uploads` 卷（`UPLOAD_DIR: /src/uploads` 落在 bind mount 的 `backend/` 里），下载地址却只走 prod 域名，所以平台**不往 dev 落 APK**：`app/deploy/mobile.py` 一律经 `docker compose exec -T backend` 把流灌进 prod 的 `uploads` 命名卷（`/uploads/mobile/{slug}.apk`）。应用从未发布过（`status_prod != 'running'`）时，上传端点在**任何写入之前**返回 409 并给出「请先发布并启动正式环境」，不隐式起栈、不隐式发布（US-17）。写入分两步 exec：先 `cat > .part`，退出码为 0 后再单独 `mv` 转正——中断最多留下隐藏的 `.part`，半个文件不会被当成正式 APK。

**R-4（复用 `sys_user` 的连带面）——已逐点确认并实现。** ① 用户列表：`SysUserMapper.xml::selectUserPage` 在 `roleKey` 为空时用 `NOT EXISTS (… r.role_key = 'mobile')` 默认排除 C 端账号，显式传 `roleKey=mobile` 才只看它们（US-13）。② 数据范围：移动端角色 `data_scope=2`（本部门），注册账号 `dept_id` 为空 → `selectVisibleDeptIds` 返回空集 → `@DataScope` 的可见数据为空（fail-closed）。③ 后台菜单：角色**不写 `sys_role_menu`**，`getRouters` 无从取出任何菜单（US-12）。④ 登录日志：`SysLoginLog` 按 userName 记录、不按角色过滤，移动端用户的登录会出现在后台登录日志里——这是运维观测面，不是越权，明确接受。

**R-5（会先 413 的体积闸门）——结论已更正。** 原假设是 APK 走应用前端 nginx 的 `/api/` 上传链路，因此 `client_max_body_size 50m` 与 `spring.servlet.multipart.max-request-size` 必须一起调。实际实现**不走那条链**：平台端点经 `docker compose exec -T backend` 的 stdin 直接写卷，应用 nginx 与 Spring multipart 都不在路径上。真正的体积闸门是 `mobile.APK_MAX_BYTES = 200MB`（快路径读 `UploadFile.size`，慢路径由 `_CappedReader` 在流式写入时兜底，超限 413），上限值由 `info` 接口的 `apk_max_bytes` 回传给前端做同值提示。**不要为了 APK 去改这两个 nginx/Spring 上限。**

另外三条：

- **地址投影不许自己算。** APK 的 baseUrl 与打包指引里的地址，来源是 `nics.py::app_url` 的单点投影（与「打开生产」同一口径，受 `ZEROCODE_SSL_ENABLE` 影响）。前端另算一套会让两种形态在某一层悄悄漂移。
- **本规格不新增平台配置键**，因此**不需要动 `tests/conftest.py` 的种子**。这条写出来是为了对抗「顺手加个开关更稳」的冲动——一旦加了，就必须同步补进 `app_config` 的硬编码种子，否则「打底行」类断言不成立。应用的「移动端用户」角色种子落在**覆盖层的 `sqlchange/D20260101_02_mobile_role.sql`**（`app/templates/uniapp-overlay/sqlchange/`，随应用分库执行），不是平台 `changelog/`，也不是模板 `schema.sql`。
- **移动栈是第 5 个 stack key，登记面横跨五处**（`compose.py` 的 `PUBLISHABLE_STACKS`/`STACK_SECRETS`、`contract.py`、`app_service.py` 的 `STACKS`/`LARGE_STACKS`/`TEMPLATE_LAYERS`、`appForm.js` 的 `LARGE_STACKS`、`publishableStacks.js`）。漏一侧的症状是创建时能选、发布时被闸门拦下，或预览起栈时缺密钥。`_check_scale_stack` 现在两个方向都守：large 只收 springboot 系，small 只收小栈名单。
