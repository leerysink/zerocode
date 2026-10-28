# 聊天页应用操作条（chat app action bar）规格说明

**日期**：2026-10-28  **来源**：grill-with-docs 访谈  **术语**：见 [CONTEXT.md](../../CONTEXT.md)「聊天页应用操作条」「预览（一词三义）」「重建 dev 库」「打开预览」  **ADR**：[0019 按页挂载不抽公共层](../adr/0020-chat-app-actions-mount-per-page.md)、[0020 预览走新标签不内嵌](../adr/0021-chat-preview-new-tab-no-iframe.md)

## 1. 问题陈述

用户造应用的地方是聊天页，管应用的地方却是应用列表页。要让刚改完的代码在浏览器里跑起来，他得离开对话、
去 `/apps` 找到那张卡片、在 ☰ 菜单或卡面上找按钮，看完再切回来。聊天页底部 [ChatInput.vue 的 `.zc-input-buttons`](file:///d:/project/zerocode/frontend/src/components/ChatInput.vue#L150)
的工具条上现在只有「压缩上下文 / 发送 / 停止」三个**会话级**动作，一个**应用级**动作都没有。

结果是最高频的迭代回路（改代码 → 起预览 → 看效果）被一次跨页跳转切断，而这条回路上需要的能力
（预览启停、看日志、看监控、发布、动库）后端和前端**早已全部存在**，只是散在 `AppsView` 的卡片与弹框里。

本变更把这些出口按到聊天页上来，不新造任何能力。

## 2. 方案概述

- **纯前端变更，零后端改动、零数据库迁移**：需要的 9 个接口全部已存在（见 §5.1）。
- **落点**：`ChatInput.vue` 的 `.zc-input-buttons` 内、「压缩上下文」左侧，新增一个自持状态的组件
  `ChatAppActions.vue`。主区三个入口：`[⟳ 启动/重启预览]`（带文字）+ `[⧉]`（打开预览，图标）+ `[☰]`（更多，图标）。
- **更多 = 7 项、三段分隔线**：复制预览地址 ／ 数据库操作、重建 dev 库 ／ 发布新版本、发布记录、资源监控、预览日志。
- **四个查看类弹框按页挂载现成组件**（ADR-0020），壳子逐字照抄 `AppsView`：日志与发布记录用侧边 `a-drawer`、
  资源监控用居中 `a-modal`、发布是 `PublishModal` 自带壳。
- **应用身份靠反查**：聊天页只有 `currentWorkspaceId`，用一次 `GET /api/apps` 按 `workspace_id` 匹配出 app；
  反查不到（未绑定工作区 / 无应用 / 非本人应用）整组隐藏。
- **状态靠轮询单个应用详情**（不是 `devStatus`，理由见 §5.3），不订阅 deploy WS。

## 3. 用户故事

编号可独立验证。

### 存在性

- **US-01** 作为 owner，我打开一个属于自己应用的会话，底部工具条上能看到三个预览入口。
- **US-02** 作为用户，我打开的会话其 `workspace_id` 在 `GET /api/apps` 结果里匹配不到任何行（应用已被删、
  或属于别人），整组入口**不渲染**——不是置灰、不是报错，是这一格根本不存在。
- **US-03** 作为用户，我点「+ 新会话」但尚未选择工作区时（`currentWorkspaceId` 为 `null`），整组入口不渲染。
- **US-04** 作为用户，我在会话间切换时，入口组跟着换到**新会话所属的应用**，不出现「按钮还是上一个应用的」
  这一帧。

### 预览启停

- **US-05** 作为 owner，`status_dev` 为 `stopped` 或 `error` 时，主按钮文案是「启动预览」，点它调
  `POST /apps/{id}/dev/start`。
- **US-06** 作为 owner，`status_dev` 为 `running` 时，主按钮文案是「重启预览」，点它调
  `POST /apps/{id}/dev/restart`（后端语义 = stop + start）。
- **US-07** 作为 owner，我点击启动或重启后，按钮立刻进入 loading 且文案变「启动中…」并禁用，
  同时 `message.info` 给一句受理提示。
- **US-08** 作为 owner，预览进入 `starting` 后轮询加密到 2s 一次；轮询看到 `running` 时按钮文案转
  「重启预览」并 `message.success`；转为 `error` 时 `message.error` 且提示文案里带一个**可点击的「查看日志」**，
  点了直接打开预览日志抽屉。
- **US-09** 作为 owner，`starting` 持续超过 90 秒仍未见 `running`，我收到与 US-08 同样的失败提示
  （带「查看日志」入口），而不是无限转圈。
- **US-10** 作为 owner，接口本身返回错误（Docker 未启动的 503、非本人的 404）时，按钮**不**进入 loading
  残留态，错误原文（`detail`）作为 message 文案显示。

### 打开预览

- **US-11** 作为 owner，预览 `running` 时我点 `⧉`，新标签打开该应用后端返回的 `preview_url`；
  当前聊天页的会话、滚动、右栏状态一律不动。
- **US-12** 作为 owner，预览未 `running` 时 `⧉` 是禁用置灰的，hover tooltip 说「预览未启动」。
  点它**不会**代为启动。
- **US-13** 作为 owner，我点「复制预览地址」，`preview_url` 进剪贴板并 `message.success`；
  在非安全上下文（http + IP 访问控制台，`navigator.clipboard` 为 `undefined`）下 `message.error`
  提示手动选择，不静默失败。

### 更多菜单

- **US-14** 作为 owner，我点 `☰` 看到 7 项、按三段分隔线分组：预览（复制预览地址）／数据与运维
  （数据库操作、重建 dev 库）／发布与观测（发布新版本、发布记录、资源监控、预览日志），
  每项带前置图标且与 `AppsView` 同名项用同一图标。
- **US-15** 作为 owner，我点「数据库操作」，新标签打开 `/apps/{app.id}/db`（`<a target="_blank">`，
  与卡面同一形态）。
- **US-16** 作为 owner，我点「重建 dev 库」，弹出 `Modal.confirm`，标题「重建 dev 数据库」、
  `okType: 'danger'`、正文点明将删除并重建 `<slug>_dev`（从 sqlchange 文件全量重放）且不影响 `<slug>_prod`。
- **US-17** 作为 owner，我在该确认里点「重建」后，接口受理并**自动打开预览日志抽屉**——因为本页不订阅
  deploy WS，`dev_reset_result` 没有别的通道能看见。
- **US-18** 作为 owner，我点「预览日志」/「资源监控」/「发布记录」/「发布新版本」，分别看到
  `DeployLogPanel`（62% 抽屉）、`MetricsModal`（920 居中弹框）、`PublishHistory`（760px 抽屉）、
  `PublishModal`，内容与形态和列表页完全一致。
- **US-19** 作为 owner，我的应用 `stack` 不在可发布名单里时，「发布新版本」禁用并给说明性 tooltip。
  名单唯一真相在后端 `app/deploy/compose.py::PUBLISHABLE_STACKS`，前端镜像在 `utils/publishableStacks.js`，
  聊天页与列表页**共读这一份**——不许在任一处写死栈名。
- **US-20** 作为 owner，我在「发布记录」里点回滚，行为与列表页一致：关掉记录抽屉、打开 `PublishModal`
  的 rollback 模式并自动触发。

### 弹框共存

- **US-21** 作为用户，我开着预览日志抽屉时再点「资源监控」，日志抽屉被关掉、监控弹框打开——
  四个查看类壳子**互斥**，任何时刻至多一个开着。
- **US-22** 作为用户，我在弹框开着的时候切换会话，所有弹框自动关闭。

## 4. 已定决策

| 决策点 | 裁定 |
|---|---|
| 落点 | `ChatInput.vue` 的 `.zc-input-buttons` 内、「压缩上下文」左侧 |
| 预览载体 | 新标签打开 `preview_url`，不做内嵌 iframe（ADR-0021） |
| 启动态表达 | 一个按钮按 `status_dev` 变身（启动／启动中…／重启），文案即状态读数 |
| 状态新鲜度 | 轮询 `GET /api/apps/{id}`（纯 DB 读），不订阅 `/ws/deploy/{id}`；**不用 `devStatus`**，见 §5.3 |
| 未启动时「打开预览」 | 禁用置灰 + tooltip，不代为启动 |
| 显示条件 | 反查不到 app 就整组隐藏（`GET /api/apps` 已按 owner 过滤，一个判据管三件事） |
| 内容壳子 | 照搬 `AppsView` 各组件原壳（drawer / 居中 modal），四组件零改造 |
| 弹框共存 | 互斥，同时只开一个；切会话全关 |
| 重启反馈 | loading + 结果 message；失败时 message 带「查看日志」直达 |
| 重建 dev 库反馈 | 确认后自动弹开预览日志抽屉 |
| 重建 dev 库确认 | 复用卡面同款 `Modal.confirm`（卡面本来就有，不存在分叉） |
| 菜单文案分叉 | 聊天页叫「重建 dev 库」、卡面保持「重建 dev」，**有意为之**（ADR-0020） |
| 主区视觉 | 一个带文字 + 两个图标；图标定 `sync-outlined` / `select-outlined` / `menu-unfold-outlined` |
| 「预览」一词三义 | 不消歧、不改名，靠上下文（ADR 与术语表记录两个所指） |

**访谈中被推翻的两处前提**（写码前须知，别照旧口径实现）：

1. ~~「卡面的重建 dev 无确认」~~ → `AppsView.vue::resetDev` 一直有 `Modal.confirm`。不存在「要不要加确认」的取舍。
2. ~~`reset_dev` 重建的是容器栈~~ → 它 DROP 并重建 **dev 数据库**。这是「重建 dev 库」这个命名的全部依据。

## 5. 数据模型与接缝

### 5.1 接口清单：全部已存在，零后端改动

| 用途 | 接口 | 现有前端封装 |
|---|---|---|
| 反查 app（含 `preview_url`/`status_dev`/`slug`/`stack`） | `GET /api/apps` | `listApps()` `api/index.js::listApps` |
| 轮询单应用状态 | `GET /api/apps/{id}` | **缺**，需补一行 `getApp` |
| 启动／重启／重建库 | `POST /apps/{id}/dev/{start,restart,reset}` | `devStart/devRestart/devReset` `:71-75` |
| 预览地址 | 随 `listApps`/`getApp` 的 `preview_url` 返回 | — |
| 监控 | `GET /apps/{id}/metrics/{current,history}` | `MetricsModal` 内部自调 |
| 发布／记录 | `POST /apps/{id}/publish`、`GET /apps/{id}/records` | 组件内部自调 |
| 日志 | `GET /apps/{id}/dev/logs` + `deployWsUrl` | `DeployLogPanel` 内部自调 |

### 5.2 反查接缝（最高、最该测的一处）

`GET /api/apps` 的 owner 过滤在后端（`app_service.py::list_apps 的 owner 过滤` `WHERE a.owner_user_id = :u`），
所以**前端不再实现任何权限判定**。反查是一个纯函数，新文件
`frontend/src/utils/appLookup.js`：

```js
findAppByWorkspace(apps, workspaceId) -> app | null   // 两侧都按字符串比，不 Number()
```

id 按字符串比较是既有纪律（`api/index.js` 记忆条目：19 位大整数 `Number()` 会精度截断改错行）。
`workspaceId` 为 `null` 直接返回 `null`。

### 5.3 轮询接缝：读 `GET /api/apps/{id}`，**不是** `devStatus`

访谈里定的是「轮询 `devStatus`」，落笔时须更正：`GET /dev/status` 每次执行都 `docker compose ps`
起一个子进程（`engine.py::dev_status`），拿它当 5s 轮询目标是拿容器运行时接口做数据库读的事。

改用 `GET /api/apps/{app_id}` —— 两条 SELECT 的纯 DB 读，回体同样带 `status_dev` 与 `preview_url`
（`app_service.py::get_app`），非本人 404。需在 `api/index.js` 补 `export const getApp = (id) => http.get(\`/apps/${id}\`)`。

轮询节奏：页面可见时 5s；`status_dev === 'starting'` 时 2s；`document.hidden` 时停，`visibilitychange`
恢复即补一次。`starting` 超 90s 判定为超时（US-09）。

### 5.4 组件接缝

新增 `frontend/src/components/ChatAppActions.vue`，**自持全部状态**（app、status、轮询、四个壳子的开合），
对外只读 `chatStore.currentWorkspaceId`。`ChatInput.vue` 只加一行挂载，不往 `chat.js` 塞任何预览字段——
预览状态是视图关注点，不是会话状态。

```
ChatInput.vue 的 `.zc-input-buttons` 的 .zc-input-buttons 内、压缩上下文那个 a-button 之前
  <chat-app-actions />
```

（位置以用户红框为准 = 「压缩上下文」**左侧**。§2 与本节此前写的「159 之后」自相矛盾，
159 是压缩上下文按钮的收尾行，插在那儿会落到它右边，已按红框改正。）

四个壳子挂在 `ChatAppActions.vue` 的 template 尾部（`a-modal`/`a-drawer` 自带 teleport，嵌在布局里不影响定位）。
壳子的宽度、`destroy-on-close`、`:key` 逐字照抄 `AppsView.vue 的四个壳子挂载区`。

### 5.5 app 列表的来源

`apps` 由 `SideBar.vue::loadAll` 的 `loadAll()` 拉取，目前是该组件的局部 `ref`。两个选项：

- **采用**：`ChatAppActions` 自己 `listApps()` 一次并缓存于组件内（切会话时若已有缓存只重算反查，
  不重复请求）。
- 否决：把 `SideBar` 的 `apps` 提升到 store —— 侧栏与应用列表都会改它，提升后要背住三处失效时机。

代价是多一次请求（只在首次进入聊天页时），换来的是组件自洽、不跨组件耦合。

## 6. 测试决策

**顶层接缝是 `ChatAppActions.vue` 的行为**，但优先在**更低的纯函数接缝**上把逻辑吃干净，
组件测试只覆盖「接线正确」。

- **纯函数（vitest，新 `utils/appLookup.test.js`）**：命中／`workspaceId` 为 null／apps 为空／
  两侧类型不一致（`123` vs `'123'`）时的匹配结果；主按钮文案映射 `devActionLabel(status)` 四态全覆盖。
- **组件（新 `components/ChatAppActions.test.js`，仿 `components/SideBar.test.js` 的 mock 风格）**：
  - US-02／US-03：反查为 null 时组件根节点不渲染（断言容器不存在，而非 `disabled`）。
  - US-05／US-06：`status_dev` 两种取值下点击分别打到 `devStart`／`devRestart`（mock `api`，断言调用与参数）。
  - US-12：`stopped` 时「打开预览」按钮 `disabled`。
  - US-13：`preview_url` 逐字等于 mock 返回值（**断言里没有 `localhost` 拼接**，见下）。
  - US-16：确认框文案含 `<slug>_dev` 与 `<slug>_prod` 两个具体库名。
  - US-17：确认「重建」后日志抽屉的 open 翻转为真。
  - US-19：`stack: 'static'` 时「发布新版本」禁用。
  - US-21：开监控前日志抽屉的 open 变假。
  - US-22：watch 到 `currentWorkspaceId` 变化后四个 open 全假。
- **轮询（fake timers）**：5s／2s 两档节奏、`document.hidden` 停摆、90s 超时判定。仿
  `composables/useDeploy.test.js` 的定时器写法。
- **红线断言（防自拼地址）**：对 `frontend/src/components/ChatAppActions.vue` 源码做静态断言，
  不得出现 `localhost`、`://${`、`dev_port` 三者的字符串拼接形态——预览地址只能来自接口字段。
  这类「对生成/手写文本做断言」的测试要先收窄判定域，否则会命中自己的注释（既有踩坑记录）。
- **不测**：真实 Docker 环境下的启停时序（本地手测一次）；四个被复用组件的内部行为（它们各自已有测试）。

**「完成」的标志**：`cd frontend && npm run test -- --run` 全绿；`npm run build` 成功；
本地起平台后在聊天页手工走一遍——启动预览看到转圈到「重启预览」、`⧉` 开新标签、
重建 dev 库弹确认并自动开日志、四个壳子互斥、切会话后入口跟着换应用且弹框全关。

## 7. 明确不做

- **不动后端**：不加接口、不改 `app_service`、不新增 `ZEROCODE_*` 配置位、不落 changelog。
- **不抽跨页共享的 `<AppActions>` 组件或菜单注册表**（ADR-0020）；`AppsView` 的卡片与菜单不改。
  唯一例外：可发布栈名单从 `AppsView.vue` 提到 `utils/publishableStacks.js` 供两处共读——
  闸门行为必须同源，这不属于被否决的「共享 UI 层」。
- **不做 prod 运行日志**：后端没有这个数据源（只有 dev 一路日志接口），本期不补。
- **不做站内 iframe 预览**，也不给 `AppsView` 现有抽屉加/删任何东西（ADR-0021）。
- **不提供「停止预览」**：主按钮只在启动／重启之间变身，停栈仍留在列表卡面。
- **不放进行管理动作**：重命名、删除应用、发布到商店、打开工程目录（聊天页右栏 `FilePanel.vue 的「打开文件夹」按钮`
  已有同款）都不进这个菜单。
- **不改 `FilePanel.vue` 的「预览」文案**：一词三义靠上下文消歧，不做重命名。
- **不订阅 `/ws/deploy/{id}`** 作为常驻通道；组件内部自带的短连接不算。
- **不做 owner/founder 前端判定**：反查不到即隐藏，权限的唯一裁决处在后端。

## 8. 补充说明

- **`PublishModal` 的挂载前置**：它在 setup 期就读 `props.app.id`（`useDeploy(props.app.id)`），
  所以必须 `v-if="pub.open"` + `:key="pub.seq"` 才挂，不能靠传 `null` 隐藏。`AppsView.vue 的 `<PublishModal>` 挂载` 就是这个写法。
- **`DeployLogPanel` 的 `active` 语义**：`active` 驱动 WS 连与不连（`:active="logs.open"`），
  抽屉关了要把 `active` 落回 false，否则 WS 长挂。
- **`window.open` 的 transient activation**：Chrome 约 5 秒窗口内才算用户手势，返回 `null` 时
  必须给可操作提示，不许静默失败（ADR-0011 已为此立过规矩）。US-11 的实现要接住 `null`。
- **命名窗口**：预览标签建议用 `zerocode-preview-<app.id>` 之类的命名窗口，让同一应用重复点击复用标签
  而不是堆一排——与 `openFolder.js` 里 `zerocode-files-<id>` 的既有做法对齐。
- **`status_dev` 的取值域**：落库只有 `stopped` / `starting` / `running` / `error` 四态（`engine.py` 的
  `_set_status` 全部调用点；列定义无 CHECK，`VARCHAR(20) DEFAULT 'stopped'`）。`stopping` 只是 WS 事件名，
  `stop_dev` 在库里直接落 `stopped`，文案映射不必为它留分支。未识别取值按 `stopped` 处理，不给空文案。
- **`preview_url` 可能为空串**：`_with_preview_url` 在 `dev_port` 缺位时回 `""`（`app_service.py::_with_preview_url`）。
  此时 `⧉` 与「复制预览地址」都要禁用，不能开一个 `about:blank`。
- **与 `TodoBar` 的关系**：`ChatView.vue 的 `<TodoBar>`` 的 todo 条在输入框上方，本变更不碰它，二者无布局冲突。
- **下游**：本 spec 已就绪进入 [to-tickets](../specs/) 拆垂直切片。建议切法：
  ①反查 + 隐藏条件（含 `getApp` 封装与 `appLookup.js`）→ ②主按钮 + 轮询 + 反馈 →
  ③更多菜单的只读四项（日志/监控/记录/地址）→ ④写操作三项（打开预览/重建库/发布）→ ⑤互斥与切会话清理 + 红线断言。
