# 控制台主题：全局浅/深（2026-11-19）

- 决策依据：[ADR-0055](../adr/0055-console-theme-unified-light-dark.md)（统一管辖，推翻工作台
  「默认深色、其余页面保持浅色」两条旧裁定）
- 术语：CONTEXT.md「控制台主题（console theme）」——浅色主题/深色主题、别名入口、统一管辖

## 1. 问题陈述

控制台只有浅色一种主题，夜间使用刺眼。唯一的深浅切换住在工作台
（`utils/theme.js:4`，键 `zc.workbench.theme.v1`，默认深色，`data-theme` + 局部 CSS 变量，
刻意不动全局 `:root`，见 `WorkspaceWorkbench.vue:43` 注释）；用户待得最久的聊天页、
应用列表反而没有。全局底色变量只有 6 个浅色值（`styles/theme.less:1-8`），
antd 侧只设了主色 token（`App.vue:13`），没有暗色算法。

缺的是**一份管辖所有控制台页面的全局浅/深偏好**，不是工作台能力的复制——
工作台已有机制保留，改的是取值来源。

## 2. 方案概述

一份浏览器本地偏好（localStorage 新键 `zc.console.theme.v1`）是唯一真源：
Pinia theme store 包裹 `utils/theme.js` 扩展出的纯函数，把主题投到三处——
`<html data-theme>` 驱动 `:root` CSS 变量换值、`App.vue` 的 `a-config-provider`
按主题切 `darkAlgorithm`、各编辑器/高亮组件按主题 prop 换肤。
规范入口在 AccountBar 顶栏「登出」旁；工作台状态栏按钮保留为**别名入口**，拨同一份偏好。

### 接缝（测试靶心）

- **逻辑 seam（最高）**：`utils/theme.js` 纯函数，storage 可注入——沿既有
  `readWorkbenchTheme/writeWorkbenchTheme` 的形态扩展 console 版（normalize/next/read/write/
  旧键继承），`theme.test.js` 的 fakeStorage 模式原样复用。全部判定逻辑只住这里。
- **状态 seam**：`stores/theme.js`（Pinia，与 auth/config 同族）—— reactive 状态 +
  storage 事件订阅 + 副作用（写 `<html data-theme>`）。组件只读 store，不各自读 localStorage。
- **应用 seam**：`styles/theme.less` 增 `:root[data-theme='dark']` 变量块；
  `App.vue` config-provider 的 theme 改为 computed。组件样式经变量或深色覆写换肤，
  不各自发明第二套开关。
- **组件 seam**：`AccountBar.test.js`（规范入口）、`WorkspaceWorkbench.test.js`（别名入口）
  均已存在，扩展而非新建。

## 3. 用户故事

- **US-01 默认浅色**：无偏好存储时所有页面呈浅色；现有用户升级后界面不变。
  normalize 只认精确 `'dark'`，其余（未设置、大写、带空格、脏值）一律浅色——
  与工作台旧规则「只认精确 light」互为镜像。
- **US-02 顶栏开关**：AccountBar「登出」旁新增浅/深切换按钮，一键翻转当前主题，
  即时生效不刷新页面；按钮带 aria-label 与悬停气泡（两名同源，沿常驻工具条口径）。
  登录页不挂 AccountBar（`App.vue:15` meta.public 判据不动），无入口但主题照常生效。
- **US-03 偏好持久化**：切换写入 `zc.console.theme.v1`；刷新、重开浏览器恢复同一主题。
- **US-04 存储不可用降级**：隐私模式/配额满时读写不抛错——切换仍在本页内存生效，
  只是记不住（沿 theme.js 既有静默降级形态）。
- **US-05 全局生效**：应用列表、聊天页、设置、技能、商店、DB 操作台、登录页、
  各详情整页（变更/记忆）与工作台全部跟随同一份偏好；页面间跳转无深浅混切。
- **US-06 antd 组件换肤**：深色时 config-provider 启用 `theme.darkAlgorithm`，
  主色保持 `#5b7a9d`；浅色保持现状 token。弹框、抽屉、消息提示等全局挂载组件同样跟随。
- **US-07 自定义样式换肤**：硬编码颜色变量化或加深色覆写（ hotspot：`db-admin.less`、
  `AppsView.vue` hero 渐变与仪表带、`ChangeDiffPane.vue`、`SideBar.vue` 等，见 §7）；
  验收口径是两种主题下每个页面文字/背景/边框对比可读，不出现深底浅组件或浅底白字。
- **US-08 工作台统一管辖**：工作台读全局偏好，`zc.workbench.theme.v1` 不再被读取；
  状态栏按钮保留为别名入口——点击拨全局偏好，标签语义不变（显示目标主题名，
  `WorkbenchStatusBar.vue:25`）；`.zc-wb` 的 `data-theme` 与局部 CSS 变量机制保留，
  取值来源换成全局（`WorkspaceWorkbench.vue:629/636` 两块变量及其 :deep 覆写不重写）。
- **US-09 状态栏蓝底保留**：工作台状态栏两种主题下都是 VSCode 蓝底白字
  （`WorkbenchStatusBar.vue:4` 注释的既有刻意设计），本次不改。
- **US-10 Monaco 跟随**：工作台编辑器按全局主题切 `vs`/`vs-dark`
  （`MonacoEditor.vue:76,147` 既有 prop + watch 机制保留，只换取值来源）。
- **US-11 SqlEditor 跟随**：DB 操作台 CodeMirror 增深色 theme 变体
  （`SqlEditor.vue:43` consoleTheme 现状浅色写死），经 compartment 热切换，
  切换不重建编辑器实例、不丢光标/选区/未执行 SQL。
- **US-12 代码高亮跟随**：Markdown 渲染视图与代码块高亮在深色下用 github-dark 系配色
  （现状 `MarkdownView.vue:7` 全局引入 github.css），浅色保持 github；
  渲染视图的正文/链接/引用块底色同样随主题。
- **US-13 监控曲线跟随**：MetricsModal 内联 SVG 的曲线、网格、坐标轴文字色
  两种主题下均可读（现状 7 处硬编码浅色）。
- **US-14 旧偏好一次性继承**：新键缺席且旧键 `zc.workbench.theme.v1` 有合法显式值
  （`'light'`/`'dark'`）时，继承为全局偏好初值并写入新键；旧键缺席或脏值 → 浅色。
  继承只发生一次（新键写入后旧键永不再读，也不主动删除）。
- **US-15 跨标签页同步**：一个标签页切换主题，其余同源标签页经 storage 事件实时跟随
  （工作台新标签与原标签互相同步）；事件只读新键，旧键变更不响应。
- **US-16 刷新无闪帧**：主题在应用挂载前落到 `<html data-theme>`（main.js 侧先读偏好），
  深色用户刷新不会先闪一帧浅色。
- **US-17 旧测试改写**：`theme.test.js` 中「缺省深色」系列断言随统一管辖改写为
  console 口径（默认浅色、新键、继承逻辑）；工作台组件测试里独立主题读写断言
  改为全局偏好断言。不是删测试，是换口径。

## 4. 已定决策（拷问共识，不翻案）

| 决策点 | 结论 |
|---|---|
| 范围 | 整个控制台所有页面（含登录页、工作台） |
| 深度 | 完整浅/深主题（底色/文字/边框/antd/编辑器/曲线），不是只换底色 |
| 切换方式 | 手动开关；跟随系统/三态/按钟点均被否 |
| 管辖 | 统一管辖，一份全局偏好；工作台独立主题退役，各管各的/可覆盖均被否 |
| 默认 | 浅色；默认深色被否（现有用户体验不变） |
| 入口 | AccountBar 顶栏「登出」旁；设置页不放 |
| 工作台旧按钮 | 保留为别名入口，拨同一份偏好；直接拆除被否 |
| 旧偏好 | 有显式值一次性继承；跨标签页 storage 事件同步 |
| 存储 | 浏览器本地 localStorage，不落后端、不跨设备（与侧栏宽度同策略） |
| 状态栏蓝底 | 两种主题下保留 VSCode 蓝，刻意设计不动 |

## 5. 测试决策

- **纯函数层（主靶心）**：`theme.test.js` 扩展——normalize 镜像规则、next 翻转、
  新键读写、坏值/缺省浅色、存储抛错降级、旧键继承四态（新键在/旧键合法/旧键脏/双缺席）。
  fakeStorage 注入，不挂真 localStorage。
- **store 层**：`stores/theme.test.js` 新建——初始值取纯函数结果、toggle 写存储 +
  改状态、storage 事件触发状态更新、事件只认新键。存储与事件均可注入。
- **组件层**：`AccountBar.test.js` 扩展（按钮存在、点击调 store toggle、aria-label）；
  `WorkspaceWorkbench.test.js` 改写（别名入口拨全局、`data-theme` 随全局）。
  断言落在属性/类名/store 状态上——happy-dom 不规范化 CSS，**不算计算颜色**。
- **视觉验收（人工）**：两种主题各走一遍全部页面（应用列表/聊天/设置/技能/商店/
  DB 操作台/登录/工作台/变更详情/记忆详情/监控弹框），口径是 US-07 的对比可读；
  深色下重点看 hero 渐变、仪表带、diff 行底色、SQL 结果表格。
- **回归**：全量 vitest 用 `--maxWorkers=4`（默认并发会偶发超时假失败）；
  既有 SqlEditor/MetricsModal/MarkdownView 相关测试不因换肤破口。
- 「完成」＝上述测试全绿 + 人工双主题走查无不可读区域 + 刷新无闪帧。

## 6. 明确不做

- **生成的应用与预览 iframe**：应用自己的界面，控制台主题不穿透。
- **跟随系统 / 三态 / 按钟点自动切换**：已否，不加「自动」态。
- **工作台独立覆盖**：统一管辖后不存在第二份偏好。
- **账户级同步 / 后端存储**：偏好只活在浏览器本地。
- **用户自定义配色 / 主题市场**：只有浅/深两档，主色不换。
- **工作台状态栏蓝底换色**：刻意保留。
- **旧键主动清理**：继承后不读不删，无害残留。

## 7. 补充说明

- **工作量热点**：40 个文件共 407 处硬编码颜色（grep 口径）。重灾区：
  `styles/db-admin.less`（43 行）、`views/AppsView.vue`（20 行，hero 渐变/仪表带/环境胶囊）、
  `components/change/ChangeDiffPane.vue`（20 行，diff 增删底色）、
  `components/MetricsModal.vue`（7 行，SVG 曲线）、`components/SideBar.vue`（5 行）。
  策略：能进全局变量的进变量（`theme.less` 现有 6 个变量可扩展），
  语义色（成功/警告/危险/diff 增删）建深色对应值，不逐处写双份样式。
- **antd 4.2.6**：`theme.darkAlgorithm` 开箱可用；主色 `#5b7a9d` 深色下若对比不足
  允许微调明度，验收以可读为准，不新设主色。
- **highlight.js**：github.css 是全局副作用引入，深色方案需按 `data-theme` 作用域
  覆写或动态切换样式，避免两套 CSS 打架；`utils/markdownDoc.js` 的 hljs 实例同口径。
- **CodeMirror**：consoleTheme 是 JS 对象不是 CSS，深色变体走
  `EditorView.theme` 第二份 + compartment.reconfigure，不重建实例。
- **闪帧**：main.js 在 `mount` 前读偏好写 `<html data-theme>`；store 初始化复用同一读数，
  不二次读存储造成首帧不一致。
- **后续约束**：此后新增页面/样式必须同时考虑两种主题（ADR-0055 后果），
  浅色不再是唯一默认假设。