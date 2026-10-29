# 工作区工作台（workbench）规格说明

**日期**：2026-10-29  **来源**：grill-with-docs 访谈 + to-spec  **术语**：见 [CONTEXT.md](../../CONTEXT.md)「工作区工作台」「页签」「渲染视图」「冲突」  **ADR**：[0022 工作台取代只读 explorer](../adr/0022-workbench-replaces-readonly-explorer.md)、[0023 Monaco 与 CodeMirror 并存](../adr/0023-workbench-editor-monaco.md)、[0024 保存语义](../adr/0024-workbench-save-conflict-tabs.md)

## 1. 问题陈述

「打开工程目录 / 打开文件夹」的默认落地页是只读的内置文件管理器（`/workspaces/:id/files`）：左树右预览，
文件内容用 `<pre>` 原样展示，唯一的编辑入口是聊天页右栏 300px 宽的 textarea。用户点进默认入口后，
看到的是目录而不是能改代码的地方——想编辑还得退回聊天页、在窄栏里无高亮地改，动线是断的。

本变更把它升级为**工作区工作台**：像 VSCode 一样打开文件夹、开页签、改代码、保存；Markdown 能识别并
渲染；文件类型有颜色区分。不引入插件安装机制，语言支持、主题与渲染全部内置。

## 2. 方案概述

```
/workspaces/:id/files  (WorkspaceFilesView，宿主不变)
└── WorkspaceWorkbench（新增，整页骨架）
    ├── 左侧：工作区名 + 刷新 + 懒加载文件树（沿用 /fs/ls，彩色类型图标）
    ├── 顶部：文件页签（脏标记、关闭、横向滚动）
    ├── 右侧：WorkbenchEditor（Monaco / Markdown 渲染视图 / 读取错误态）
    ├── 底部：状态栏（语言、Ln/Col、选区、UTF-8、EOL、主题切换、保存态）
    └── 冲突弹框（覆盖 / 重新加载 / 取消）
```

- **前端为主，后端三处小改**：`/api/files/read` 回带 `mtime_ns`；`/api/files/save` 接收
  `base_mtime_ns` 并在不匹配时返回 409；`/api/fs/download` 增加 `inline=1`（Markdown 图片内联用）。
- **Monaco 懒加载**：只有工作台路由的 chunk 背 Monaco，控制台主包不变。只配核心 `editor.worker`，
  不启 TypeScript/JSON 语言服务。
- **编辑收口**：聊天页右栏 `FilePanel` 删掉 textarea 编辑与保存，改为只读预览 + 「在编辑器中打开」。
- **逻辑尽量下沉纯函数**：页签状态、文件类型映射、Markdown 相对路径解析与渲染都可脱离 DOM 测试；
  组件测试只钉接线。

## 3. 用户故事

### 3.1 打开与文件树

- **US-01** 作为 owner，我用合法 id 打开页面，根层由 `GET /api/fs/ls?path=` 拉取；点目录行展开该层
  （懒加载，收起再展开不重拉），点文件行在工作台打开页签，不把文件路径发给 `/fs/ls`。
- **US-02** 作为用户，我再次点击已打开的文件，聚焦已有页签；不新建重复页签、不重复请求内容。
- **US-03** 作为用户，我点「刷新」只重置并重拉文件树，已打开的页签与其未保存内容不受影响。
- **US-04** 作为用户，工作区 id 变化时（非常规路径），树、页签、编辑器全部重置，并按新工作区加载
  它自己的恢复页签列表。
- **US-05** 作为用户，我用 `?file=<相对路径>` 打开工作台（右栏跳转 / Markdown 相对链接），目标文件
  自动开页签并被激活；文件树逐级懒加载并展开到它所在目录。文件不存在时页签内显示读取错误，树不报错。
- **US-06** 作为用户，地址里的 id 非法（非纯数字 / 空 / 小数 / 负数 / 带路径）时，页面给出错误说明，
  一个目录请求都不发（沿用现有行为）。
- **US-07** 作为用户，工作区为空时树区显示空态；没有打开页签时编辑区显示「从左侧选择文件开始编辑」。
- **US-08** 作为用户，文件树按扩展名显示彩色类型图标（Vue 绿、TS 蓝、JS 黄、Python 蓝、Markdown 蓝灰、
  JSON 黄、图片紫等），目录用文件夹图标；无法识别的类型用中性色兜底。
- **US-09** 作为用户，当前激活页签对应的文件在树里高亮；切换页签时树跟着选中并展开到该文件。
- **US-10** 作为用户，展开某层失败时收到 `detail` 提示，该层不进入展开态也不记进已加载集合，
  再次展开可重试；面包屑/当前层退回仍可见的一层（沿用现有行为）。
- **US-11** 作为用户，中文、空格、深层路径原样展示与请求，不被改写。

### 3.2 页签

- **US-12** 作为用户，同一路径只有一个页签；页签标题是文件名，hover 显示完整相对路径；页签栏
  横向滚动，不换行。
- **US-13** 作为用户，我关闭激活页签后，右侧相邻页签激活；没有右侧则激活左侧；关闭最后一个页签后
  编辑区回到空态。
- **US-14** 作为用户，有未保存改动的页签显示脏圆点；关闭脏页签时弹出确认（「关闭并丢弃」/「取消」），
  取消则页签与内容原样保留。
- **US-15** 作为用户，刷新或重开页面后，工作台按 workspace 恢复页签列表与激活项；恢复的页签一律是
  干净态，激活页签立即从磁盘重读，其余页签在首次激活时才读。
- **US-16** 作为用户，localStorage 里的页签数据损坏、类型不对或含空路径时，按空列表处理并移除坏值，
  不抛错、不阻断页面；页签列表写盘失败时编辑不受影响，只提示一次「页签无法记忆」。
- **US-17** 作为用户，存在脏页签时关闭浏览器标签/刷新，浏览器给出离开确认（`beforeunload`）。
- **US-18** 作为用户，恢复列表里的重复路径去重；页签数量不设产品上限，但只接受字符串路径。

### 3.3 编辑器与保存

- **US-19** 作为用户，打开文件后 Monaco 按扩展名选语言：`.vue` 走 HTML 模式；js/ts/json/md/html/css/
  scss/less/py/java/sql/yaml/sh/bat/dockerfile/xml/ini 等取 Monaco 内置语言；未知类型为纯文本。
  状态栏显示当前语言名。
- **US-20** 作为用户，在编辑器里输入后页签出现脏圆点；用撤销回到保存时的内容，脏圆点消失
  （以 Monaco 的 alternativeVersionId 判定，不做全文比较）。
- **US-21** 作为用户，按 Ctrl+S 保存激活页签：请求带打开/上次保存时拿到的 `base_mtime_ns`；
  成功后更新页签的 mtime、清除脏态（若保存期间没有继续输入），状态栏短暂显示「已保存」，不弹 toast。
- **US-22** 作为用户，保存失败（400/403/404/500）时看到后端 `detail`，页签保持脏态。
- **US-23** 作为用户，保存时磁盘已被 Agent/他人改动，后端回 409；工作台弹出三按钮弹框
  「覆盖 / 重新加载 / 取消」：
  - 覆盖：用 409 回带的当前 `mtime_ns` 再保存一次；
  - 重新加载：丢弃本地改动、重读磁盘并清脏；
  - 取消：关闭弹框，内容与脏态不动。
- **US-24** 作为用户，二进制或超过 200KB 的文件被后端拒读时，编辑区显示原因与下载链接，不挂编辑器；
  页签仍可关闭。
- **US-25** 作为用户，根目录 `app.json` 以只读打开（Monaco readOnly，状态栏标「只读」），Ctrl+S 不发请求；
  `deploy/**` 不进树、读取返回 403（沿用服务端保护）。
- **US-26** 作为用户，状态栏实时显示语言、`Ln x, Col y`、有选区时的选中字符数、UTF-8、LF/CRLF
  （按内容判断）、保存态与主题切换入口。
- **US-27** 作为用户，工作台默认深色；点状态栏主题按钮在深/浅之间切换并记住选择；只有工作台与 Monaco
  变色，控制台其他页面保持原浅色主题。
- **US-28** 作为用户，切换页签保留各文件的编辑内容、光标与滚动位置；关闭页签释放对应编辑器模型。
- **US-29** 作为用户，读取在途时关闭页签或切换工作区，迟到的响应不得写进新状态（沿用世代号纪律）。
- **US-30** 作为用户，Ctrl+S 在无激活页签、读取错误页、只读文件或干净页签上不产生写请求。

### 3.4 Markdown

- **US-31** 作为用户，打开 `.md` 默认落在「渲染视图」；页签右上有「源码 / 渲染 / 分屏」三态切换；
  恢复的 md 页签也默认渲染视图。
- **US-32** 作为用户，渲染视图展示当前缓冲区内容（含未保存改动）；分屏时左侧输入右侧实时更新；
  切回源码后可继续编辑与保存。
- **US-33** 作为用户，渲染视图里的相对图片（`./a.png`、`../img/x.jpg`）解析为相对当前 md 文件的路径，
  经 `/api/fs/download?...&inline=1` 内联显示；越出工作区根的路径不改写（保持裂图）。
- **US-34** 作为用户，渲染视图里的相对链接若指向工作区内文件，点击在工作台开对应页签；`http(s)` 等
  外链新标签打开（`rel="noopener noreferrer"`）；`javascript:`、`data:` 与越界相对路径渲染为普通文本，
  不产生可点击链接。
- **US-35** 作为用户，Markdown 原始 HTML 被转义（`html:false`）；表格正常渲染；代码块有语法高亮，
  且深/浅主题下配色都清晰可读。
- **US-36** 作为用户，纯锚点链接（`#section`）保持原样但不保证滚动定位（本期不加标题 id）。

### 3.5 聊天页右栏与入口

- **US-37** 作为用户，聊天页右栏「文件」tab 只读预览文件内容；不再出现「编辑 / 保存 / 取消」，
  二进制/超限的提示与现状一致。
- **US-38** 作为用户，右栏选中可读文件后点「在编辑器中打开」，新标签打开该工作区工作台并带上
  `?name=<工作区名>&file=<相对路径>`；弹窗被拦截时给出可操作提示。
- **US-39** 作为用户，右栏「打开文件夹」按钮的三态分流（web/auto/system）与现状完全一致，
  只是目标页从只读管理器变成工作台。

## 4. 已定决策

| 决策点 | 裁定 |
|---|---|
| 宿主 | 升级 `/workspaces/:id/files` 整页，入口文案与地址不变（ADR-0022） |
| 骨架 | 左树 + 页签 + 编辑区 + 状态栏；不做活动栏（无插件安装、无搜索） |
| 编辑引擎 | Monaco；SQL 控制台继续用 CodeMirror，不迁移（ADR-0023） |
| 插件含义 | 内置语言/主题/Markdown 能力，不做安装机制 |
| Vue 高亮 | `.vue` 映射到 Monaco HTML 模式，不写 Monarch、不引社区语言包 |
| Markdown 默认 | 渲染视图；可切源码/分屏；术语叫「渲染视图」不叫预览 |
| Markdown 资源 | 相对图片内联显示；相对文件链接站内开页签；外链新标签 |
| 保存 | 手动 Ctrl+S + 脏标记；不自动保存（ADR-0024） |
| 冲突 | 保存前 mtime 检测；覆盖/重新加载/取消，不静默覆盖（ADR-0024） |
| 页签恢复 | 只恢复列表与激活项，不持久化草稿（ADR-0024） |
| 文件操作 | 只编辑保存；不新建/重命名/删除/移动 |
| 体积限制 | 二进制与 200KB 上限不变；超限只给下载 |
| 主题 | 工作台默认深色、可切浅色；控制台其余页面不变 |
| 颜色区分 | 树彩色类型图标 + 编辑器语法高亮 |
| 右栏 | 只读预览 + 「在编辑器中打开」，编辑收口到工作台 |
| 图标 | 自绘 SVG 类型图标映射，不引图标包 |
| 外部改动 | 只在保存时检测冲突，不做轮询/推送自动重载 |
| 树宽/分屏 | 固定宽度（树 280px、分屏 50/50），不做拖拽 |

## 5. 数据模型与接缝

### 5.1 后端契约（三处改动）

| 接口 | 变更 |
|---|---|
| `GET /api/files/read` | 回体增加 `mtime_ns`（十进制字符串，`st_mtime_ns` 的精确值；约 1.79e18 超出 JS Number 的 2^53，走数字会被浏览器舍入）；其余校验与错误不变 |
| `PUT /api/files/save` | body 增加可选 `base_mtime_ns`（同一枚十进制字符串，后端 Pydantic 转 int）；不匹配回 409；成功回体增加 `mtime_ns` |
| `GET /api/fs/download` | 增加可选 `inline: bool = False`；为 true 时 `FileResponse(f)` 不带 `filename`，不产生 `Content-Disposition: attachment` |

**save 语义细节**：

- `base_mtime_ns` 缺省 = 不做冲突检测，保持现有「写盘并按需建父目录」行为（后端测试与其他内部调用可用）。
- `base_mtime_ns` 提供时：
  - 目标不是已存在文件 → 409，body `{"detail": "文件已被删除或移动", "mtime_ns": null}`；
  - `st_mtime_ns` 与 `base_mtime_ns` 不相等 → 409，body `{"detail": "文件已被其他改动，请重新加载后再保存", "mtime_ns": "<当前值>"}`；
  - 相等 → 写入（`newline=""` 字节保真不变），回 `{"ok": true, "mtime_ns": "<写后 st_mtime_ns>"}`。
- 409 用 `JSONResponse(status_code=409, content=...)` 顶格返回 `detail` 字符串 + `mtime_ns`，
  前端从 `e.response.data.detail` / `.mtime_ns` 取，两个方向都只透传字符串，禁止转 Number。
- 写保护（`app.json`、`deploy/**`）、路径越界、二进制、200KB 上限全部保持现有行为。

### 5.2 纯函数接缝（优先在此测试）

1. `frontend/src/utils/fileTypes.js`
   - `fileKind(path) -> string`：扩展名/文件名 → 图标种类（vue/js/ts/json/md/html/css/py/java/sql/yaml/shell/docker/image/xml/folder/file）。
   - `languageOf(path) -> string`：Monaco language id（`.vue` → `html`，未知 → `plaintext`）。
   - `isMarkdown(path)`、`isReadOnlyPath(path)`（仅根 `app.json`）。
   - `iconColor(kind)`：固定调色板。
2. `frontend/src/utils/workbenchTabs.js`
   - 页签列表的纯操作：`openTab(tabs, path)`（已存在则只激活）、`closeTab(tabs, path)`（返回新列表与应激活项）、
     `setDirty`、`activeTab`、`hasDirty`、`neighborAfterClose`。
   - 持久化编解码：`encodeTabs(tabs, active)` / `decodeTabs(raw)`（坏值、去重、非字符串、空串收口）。
   - 存储键：`zc.workbench.tabs.v1.<workspaceId>`。
3. `frontend/src/utils/markdownDoc.js`
   - `renderDocMarkdown(content, { workspaceId, filePath }) -> html`：独立 markdown-it 实例
     （`html:false`、`linkify:true`、`breaks:false`），hljs 高亮；相对图片改写成
     `/api/fs/download?...&inline=1`；相对文件链接加 `data-file-path`；外链 `target="_blank"
     rel="noopener noreferrer"`；不安全/越界链接降级为纯文本。
   - `resolveWorkspaceRel(filePath, href) -> string | null`：解析 `./`、`../`、query/hash，越界返回 null。
   聊天页 `MarkdownView.vue` 不改造、不复用这个实例（它保留 `breaks:true` 的聊天气泡语义）。
4. `frontend/src/utils/theme.js`（或等价位置）
   - `readWorkbenchTheme()` / `writeWorkbenchTheme(value)`：键 `zc.workbench.theme.v1`，
     合法值 `dark` / `light`，缺省与坏值一律 `dark`。

### 5.3 组件接缝

| 组件 | 职责 | 测试方式 |
|---|---|---|
| `components/WorkspaceWorkbench.vue` | 持有树、页签、主题、保存与冲突状态；串起读取/保存/恢复/揭示文件 | 桩掉 `a-tree`、`WorkbenchEditor`、`FileIcon`；mock api |
| `components/workbench/WorkbenchEditor.vue` | Monaco 容器、Markdown 三态、读取错误态、只读态、光标信息上报 | 桩掉 Monaco 封装；测模式切换与事件 |
| `components/workbench/MonacoEditor.vue` | 唯一直接 import `monaco-editor` 的模块；模型/视图状态/主题/只读 | 组件测试不挂；浏览器手测 |
| `components/workbench/FileIcon.vue` | 自绘 SVG 类型图标（按 `fileKind` 取图形与颜色） | 纯映射由 `fileTypes.test.js` 覆盖 |
| `components/workbench/WorkbenchStatusBar.vue` | 状态栏展示与主题切换 | 随宿主断言文本/类名 |
| `views/WorkspaceFilesView.vue` | 地址 → id/name/file 三参，非法 id 拦截；页头去掉只读提示 | 既有测试扩展 `?file=` 用例 |

**文件树**沿用现有 `WorkspaceExplorer.vue` 的懒加载实现（世代号、`loadedKeys` 回写、展开/选中边界），
迁移进工作台侧栏；`utils/fsNav.js` 的 `toTreeNodes/attachChildren/findNode/ancestorsOf/formatBytes/
formatMtime/downloadUrl` 继续使用。`downloadUrl` 增加第三参 `inline`（默认 false），改动向后兼容。

**揭示文件**：`revealFile(path)` 从根开始逐级 `listDir`，把祖先目录逐层 `attachChildren` + 展开，
最后选中文件并激活页签；任一层缺失就停止，不弹错（读取错误由页签自身展示）。激活页签时复用同一条路径。

**Monaco 集成**：`monaco-editor` 作为新依赖；`MonacoEditor.vue` 内配置
`self.MonacoEnvironment = { getWorker: () => new editorWorker() }`（`monaco-editor/esm/vs/editor/editor.worker?worker`），
不配 ts/json/css/html worker。每个打开文件一个 model（URI 按 workspace + 路径唯一），切页签时
`saveViewState` / `restoreViewState`；关闭页签 dispose model。主题 `vs-dark` / `vs`。

### 5.4 文件类型映射（图标与语言）

| 类型 | 扩展/文件名 | 图标色 | Monaco |
|---|---|---|---|
| vue | `.vue` | `#42b883` | `html` |
| js | `.js .mjs .cjs .jsx` | `#f1e05a` | `javascript` |
| ts | `.ts .tsx` | `#3178c6` | `typescript` |
| json | `.json` | `#cbcb41` | `json` |
| md | `.md .markdown` | `#519aba` | `markdown` |
| html | `.html .htm` | `#e34c26` | `html` |
| css | `.css .scss .less` | `#42a5f5` | `css` / `scss` / `less` |
| py | `.py` | `#3572a5` | `python` |
| java | `.java` | `#b07219` | `java` |
| sql | `.sql` | `#e38c00` | `sql` |
| yaml | `.yml .yaml` | `#cb171e` | `yaml` |
| shell | `.sh .bash` | `#89e051` | `shell` |
| bat | `.bat .cmd` | `#c1f12e` | `bat` |
| docker | `Dockerfile`、`*.dockerfile` | `#0db7ed` | `dockerfile` |
| xml | `.xml .svg` | `#ff6600` | `xml` |
| image | `.png .jpg .jpeg .gif .webp .ico .bmp` | `#a074c4` | 不可读（下载） |
| ini | `.ini .conf .env`、`.env*` | `#6b7280` | `ini` |
| 兜底 | 其余 | `#8c8c8c` | `plaintext` |

图标为自绘 SVG，形状按类型区分（Vue 三角、TS/JS 方块、md 文档、图片山形等），颜色取上表。

### 5.5 存储与主题

| 键 | 内容 | 失败策略 |
|---|---|---|
| `zc.workbench.tabs.v1.<workspaceId>` | `{"tabs":["a.md","src/x.vue"],"active":"a.md"}` | 解析失败按空；写失败提示一次，不阻断编辑 |
| `zc.workbench.theme.v1` | `"dark"` / `"light"` | 坏值回 `dark` |

工作台主题通过根节点 `[data-theme="dark|light"]` 上的 CSS 变量切换，不污染全局 `:root`：
深色 `背景 #1e1e1e / 侧栏 #252526 / 边框 #3c3c3c / 主文字 #cccccc / 次文字 #8c8c8c`；
浅色 `背景 #ffffff / 侧栏 #f3f3f3 / 边框 #e0e0e0 / 主文字 #333333 / 次文字 #6b6b6b`；
状态栏两套主题都用 VSCode 蓝 `#007acc` + 白字。Markdown 代码块的高亮 token 颜色按主题覆盖，
不依赖 highlight.js 主题 CSS 的全局 `.hljs` 规则。

## 6. 测试决策

**接缝优先级**：纯函数 > 后端端点 > 组件接线；Monaco 本体只做浏览器手测。

### 6.1 后端（`tests/test_files_api.py`）

- `read` 回体含十进制字符串 `mtime_ns`，与文件 `st_mtime_ns` 精确一致。
- `save` 带匹配 `base_mtime_ns` → 200，内容落盘，回体 `mtime_ns` 等于写后磁盘值的字符串。
- 外部改文件后用旧 `base_mtime_ns` 保存 → 409，响应含 `detail` 与当前 `mtime_ns` 字符串，磁盘内容未被覆盖。
- 把字符串 mtime 转成 JS 等价舍入值（`int(float(base))`）后保存 → 409，锁住「数字精度不可用于冲突判定」。
- 文件被外部删除后再用旧 mtime 保存 → 409，`mtime_ns` 为 null。
- 不带 `base_mtime_ns` 的既有保存用例继续通过（含新建 `sub/b.txt`、LF 字节保真）。
- `app.json` 保存仍 403；二进制/200KB 读取仍 400；`deploy/**` 仍 403。
- `download?inline=1` 回 200、`Content-Type` 按后缀推断、无 `attachment`；默认下载仍带
  `Content-Disposition: attachment`。

### 6.2 前端纯函数（vitest）

- `fileTypes.test.js`：上表逐行映射；大小写；无扩展 `Dockerfile`；`.env`；未知类型兜底；
  `isReadOnlyPath('app.json')` 为真、`src/app.json` 为假。
- `workbenchTabs.test.js`：打开/重复打开/关闭激活项/关闭非激活项/最后一个/邻居选择/脏态；
  编解码去重、坏 JSON、非数组、非字符串、空串；`hasDirty`。
- `markdownDoc.test.js`：相对图片改写成 `/api/fs/download` 且带 `inline=1`；`../` 归一化；
  越根不改写；相对链接带 `data-file-path`；外链 `target="_blank" rel="noopener noreferrer"`；
  `javascript:`/`data:` 不产链接；原始 HTML 被转义；表格与代码块高亮类存在；纯锚点不被拦截。
- `openFolder.test.js`：`workspaceFilesPath(id, file?)`、`openWorkspaceFilesPage(id, name, file)`
  的 URL 组合（仅 name、仅 file、两者、非法 id 返回空/false）；既有断言不改语义。

### 6.3 前端组件（happy-dom）

- `WorkspaceWorkbench.test.js`（桩 `a-tree`、`WorkbenchEditor`、`FileIcon`；mock api）：
  US-02/03/04/05/09/12/13/14/15/16/17/21/22/23/24/25/29 的接线；
  冲突弹框三按钮分别触发重发保存/重读/关闭；恢复页签只有激活项发 `readFile`。
- `WorkbenchEditor.test.js`（桩 Monaco 封装）：语言与只读透传；md 默认 render、三态切换；
  读取错误态给下载链接；光标事件上报状态栏字段。
- `WorkspaceFilesView.test.js`：`?file` 单值/数组取首个、传给工作台；id 非法仍零请求；
  既有 name 兜底与转义用例保留。
- `FilePanel.test.js`：不再有「编辑/保存/取消」；选中可读文件出现「在编辑器中打开」并打开带
  `file=` 的地址；弹窗拦截提示；既有三态分流用例保留。

### 6.4 完成标准

- `cd frontend && npm run test -- --run` 全绿；`npm run build` 成功，Monaco 不在主入口 chunk。
- `python -m pytest tests/test_files_api.py`（或统一入口）全绿；后端全量测试不因端点签名变化回归。
- 本地起后端 + 前端，在浏览器手工走一遍：打开目录 → 展开 → 开多页签 → 编辑 → Ctrl+S →
  外部改文件后再保存见冲突三选一 → md 渲染/图片/相对链接/分屏 → 切主题 → 刷新恢复页签 →
  右栏「在编辑器中打开」定位到文件 → 只读 `app.json`。
- Monaco 的模型切换、光标/滚动保持、Markdown 分屏实时刷新属于**必须手测**的项（happy-dom 跑不了）。

## 7. 明确不做

- 不做全局搜索、Ctrl+P 快速打开、命令面板、问题面板。
- 不做新建/重命名/删除/移动/拖拽文件，也不改后端的建文件语义（保存仍可按需建父目录，这是既有行为）。
- 不做插件/扩展安装、插件市场或主题包；`.vue` 不写自定义语法。
- 不做自动保存、保存并关闭、草稿持久化（hot exit）。
- 不做外部改动的轮询或推送自动重载；只有保存时的 mtime 冲突检测。
- 不做 Markdown 标题锚点 id、任务列表复选框、Mermaid、数学公式。
- 不做二进制/图片预览，超限与二进制只给下载。
- 不做树宽拖拽、分屏比例拖拽、分屏滚动同步。
- 不改造聊天页 `MarkdownView.vue` 的渲染配置，也不把右栏预览改成渲染视图。
- 不动 `/fs/ls`、`/fs/tree` 的忽略名单与懒加载口径；不动网关与应用运行时。

## 8. 补充说明

- **mtime 精度**：冲突判定用 `st_mtime_ns` 的精确值，但 JSON 里以**十进制字符串**承载——
  纳秒戳约 1.79e18，超过 JS Number 的 2^53 安全整数范围，走数字会在浏览器端被舍入
  （Windows 上约 256ns 一格），回传的 `base_mtime_ns` 永远对不上当前值。前端只透传字符串。
  `/fs/ls` 继续只回浮点 `mtime` 给展示，不参与冲突判定。
- **脏态判定**：按「缓冲区内容是否等于该文件已写盘内容」比较，而不是 Monaco 的
  `alternativeVersionId`——撤销时 Monaco 在内容变更事件之后才恢复该 id，事件里读到的是新号，
  撤销回保存点会漏报 clean。
- **保存期间继续输入**：保存请求以发出时的内容为准；响应回来时若编辑器版本已变，只更新
  `base_mtime_ns`，脏态保持，下一次保存再提交新内容（US-21 的「若保存期间没有继续输入」）。
- **409 后覆盖**：用 409 回带的 `mtime_ns` 作新的 `base_mtime_ns` 再保存；若期间又被改，会再次 409，
  不做无校验强写。
- **只读判定**：前端只把根路径 `app.json` 标只读；`deploy/**` 在树与读取层就被后端挡住，前端无需另判。
- **页签恢复与 `?file=` 的优先级**：`?file=` 指定的文件插入列表并激活；若它已在恢复列表中，只激活不重复。
- **右栏跳转地址**：`openWorkspaceFilesPage(id, name, file)` 的窗口名仍是 `zerocode-files-<id>`，
  同一工作区复用标签；浏览器对同名窗口执行导航，`file` 参数随地址生效。
- **路由不变**：`/workspaces/:id/files` 字面量与 `utils/openFolder.js` 的对拍断言（`router/index.test.js`）
  不改；只扩展 query 参数。
- **既有测试迁移**：`WorkspaceExplorer.test.js` 的懒加载/竞态用例迁移为工作台树侧栏用例；
  `FilePanel.test.js` 的编辑相关断言删除，保留三态分流与新增跳转用例。
- **包体**：Monaco 只被工作台路由引用，`vite build` 应把它分进该路由 chunk；若主入口 chunk 出现
  Monaco，视为回归。
- **下游**：本 spec 进入 to-tickets 拆垂直切片，建议顺序：
  ①后端契约（mtime/inline + 测试）→ ②纯函数（fileTypes/tabs/markdownDoc/openFolder）→
  ③工作台骨架与页签/保存链路 → ④Monaco 与 Markdown 三态 → ⑤右栏收口与入口参数 → ⑥视觉（图标/主题/状态栏）与手工验收。
