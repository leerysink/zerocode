# Agent 预览页交互验证（browser_open / browser_step / browser_close）规格说明

**日期**：2026-11-02  **来源**：brainstorming 访谈  **ADR**：[0037 从一次性只读检查改为可交互会话](../adr/0037-agent-browser-interactive-session.md)（修订 [0031](../adr/0031-agent-browser-inspection-sidecar.md)）  **前序规格**：[2026-10-31 preview_page](2026-10-31-agent-browser-verification.md)

**实现状态**：已实施。七轮修复里有五条裁定修正了本文（截图自动附的范围、`page_errors_new`、失败步也带增量、清洗边界、参数侧遮断与位次从严），已就地追认在 §5.2 / §5.3 / §6 / §7 / §8 / §10；未收口的口子集中在 §14，真机走查记录在 §15。本文仍是绑定权威：再遇到分歧，要么改代码、要么改本文并留下裁定，不靠注释各说各话。

## 1. 问题陈述

`preview_page` 上线后有两类问题它结构上回答不了。

**等待策略是猜的。** 它的时序写死为「`domcontentloaded` → 等 `agent.browser_settle_ms`（默认 1200ms）→ 短 networkidle → 截图」。任何自带启动时序的应用都会把它打穿：一个 2.1 秒开机动画、且深链窗口在启动流程末尾才挂载的应用，Agent 连续 6 次检查全部停在「正在配置组件」，`ok:true`、console 干净、截图完整——唯独不是用户真正的首屏。Agent 据此得出「渲染没问题，是时序问题」的结论尚可，但它同样可能得出「页面坏了」。固定 sleep 与任意应用的启动耗时之间没有因果关系，这不是参数调得好的问题。

**只读检查看不到业务闭环。** 「帮我验证下单流程走不走得通」这个问题，`preview_page` 只能回答「下单页渲染出来了」。表单能不能填、点提交有没有真的落库、点删除那一行有没有消失、按钮点了有没有反应——全部需要操作。用户在聊天里说的「帮我看看这个功能对不对」，多数时候指的是行为对不对，不是长相对不对。

本规格把浏览器验证从「看一眼」升级为「走一遍」，同时保留 `preview_page` 作为便宜的快检。

## 2. 方案概述

- **三个交互工具**：`browser_open(path?, expect?)` / `browser_step(action, target, value?, expect?)` / `browser_close()`。与 `preview_page` 并存，后者继续作为「改完看一眼」的便宜选项。
- **sidecar 变成有状态服务**：新增进程内 session 注册表。活页面（Playwright context + page）留在 sidecar 内存，后端只持 `session_id ↔ chat_session` 绑定、步数额度与收口责任。**`session_id` 不进模型参数面**——沿用既有铁律「应用身份由服务端解析，schema 里没有 app_id」。
- **等待改为按条件等**：给了 `expect` 就轮询等到满足或超时（默认 3s、上限 8s）；没给只等一个短 networkidle 窗口。固定 sleep 不再是唯一的等待手段。
- **元素句柄用 ref，不用 CSS selector**：每次返回一份精简的**交互元素清单**（role + accessible name + ref + sensitive 标记），上限 40 条。模型下一步用 ref 指元素。理由是应用是 Vue SPA，scoped 样式类的哈希值不可预测，让模型写选择器等于让它瞎猜。ref 是**每次扫描重生成的随机短令牌**（形状 `r7kqmbt`），不是按采集顺序排的序号——序号会在每次重打时从头排，旧句柄因此在新清单里大概率仍然合法，拿它去点是**静默点到另一个元素**而不是响亮失败（真机复现，见 §15「ref 失效」与 §6.2）。
- **断言沿用 `db_query` 的 expect 口径**：给了就工具判 `passed`，不给则模型自己读结果。
- **截图默认不给，断言失败与动作失败自动给**：一张 1280×800 PNG 的 token 不便宜，而失败的那一刻正是唯一值得看现场的时刻。口径是「这一步没能往前推进」（裁定 R-26），不是只指断言。
- **动作全放开、不逐次确认**：dev 是沙箱，改坏了可重置。边界靠「只能开在当前应用 dev origin」+ 步数额度 + 收口报告守，不靠弹框。

## 3. 用户故事

### 跑通一条路径

- **US-01** 作为 Agent，我调 `browser_open({"path": "/orders"})`，得到 `session_id`（对我不可见）、页面标题、可见文本、交互元素清单、初始 console 与失败请求，以及 `steps_left`。
- **US-02** 作为 Agent，我用 `browser_step({"action": "click", "target": "r7kqmbt"})` 点清单里那个句柄为 r7kqmbt 的按钮，返回同一形状的新状态。
- **US-03** 作为 Agent，我在 step 里带 `expect: {"type": "text_contains", "value": "订单已创建"}`，工具在超时窗口内轮询判定，返回 `passed: true/false`；不带 expect 时不判定，我自己读。
- **US-04** 作为 Agent，每步返回的 `console` 与 `failed_requests` 只含**上一步以来新增**的条目，所以我能判断「这个错是不是我这一步搞出来的」，也不必重读同一批旧错误。（字段名是 `console_new` / `failed_requests_new`；裁定 R-27 之后未捕获异常也记同一本增量账，叫 `page_errors_new`。）
- **US-05** 作为 Agent，断言失败时结果自动附带一张截图；断言通过时没有截图，省 token。（裁定 R-26 把口径放宽成「这一步没能往前推进」：**动作层面的失败**——ref 失效、点不动——同样自动附图。）
- **US-06** 作为 Agent，目标应用有 2.1 秒开场动画时，我带 `expect` 打开首页，工具等到动画结束、目标文案出现后才返回——我不再需要让用户关掉动画才能看到真实首屏。（裁定 R-41 已接线：`browser_open` 的参数面里有 `expect`，后端 `_open` 把它透传给 `POST /sessions`，`_open_step` 的 `await_expect` 轮询与结论 `passed`/`note` 都回到模型手上；见 §6.3。）
- **US-07** 作为 Agent，我调 `browser_close()` 得到一份收口报告：走了几步、几步断言失败、失败在哪步。（裁定 R-31 之后报告分开两本账：`steps_failed` 只数断言未过、`steps_action_failed` 只数「点不动」，另报 `steps_used` / `sessions_opened` / `sessions_replaced`；一个 expect 都没声明时 `hint` 不说「所有已声明的断言均通过」，改说「本次走查未声明任何断言」。）
- **US-08** 作为 Agent，验证带登录的大型应用时，我从清单里找到账号与密码输入框，`fill` 两次再 `click` 登录，后续步骤里 session 保留着登录态（token 存在这个干净 context 的 localStorage 里）。

### 失败与边界

- **US-09** 作为 Agent，我用了上一次返回里的 ref 而页面已经重渲染，得到 `ok:false` +「页面已变化」，且结果**当场附带最新交互清单**，我下一步就能改指，不多花一次往返。
- **US-10** 作为 Agent，目标元素存在但被遮罩挡住或在视口外，工具自动等到可点或超时，超时返回「元素不可点击，可能被遮挡」并附截图。
- **US-11** 作为 Agent，我的点击触发了浏览器原生 `confirm`，它按**取消**处理，且结果明确写「页面弹出了确认框，已按取消处理」——我不会把它误读成操作成功。
- **US-12** 作为 Agent，我的 session 因空闲被回收，得到「浏览器会话已结束，请重新 browser_open」，且**不扣我的步数额度**。
- **US-13** 作为 Agent，步数额度用尽时得到明确提示「本 run 步数已用完，请 browser_close 收口」，而不是继续让我试。
- **US-14** 作为 Agent，sidecar 在流程中途重启，我拿到未知 session_id 的降级结果「浏览器服务已重启，请重新 browser_open」，不是一句「工具执行异常」。（实现不区分「重启」与「过期」——sidecar 重启后旧 id 就是未知 id，文案与 US-12 同一条「浏览器会话已结束，请重新 browser_open」，同样 `steps_charged:false`。）
- **US-15** 作为 Agent，预览未运行时 `browser_open` 返回未运行状态 +「先 preview_restart」，与 `preview_page` 同口径，**绝不自己起栈**。
- **US-16** 作为平台，sidecar 活 session 已达上限时 `POST /sessions` 直接拒绝并回「浏览器服务已满」，不排队——排队等于让一次工具调用挂几十秒。

### 安全与隔离

- **US-17** 作为平台，`browser_step` 的 `navigate` 只接受同源相对 path（复用 `normalize_path`），主文档导航仍只允许目标 origin，跳出即阻断并记为失败请求。
- **US-18** 作为平台，session 是 sidecar 新建的干净 context，**不读取、不复用用户宿主浏览器的 cookie 与登录态**。
- **US-19** 作为用户，我在应用里输入的口令不会明文留在聊天记录里：清单把 `type=password` 及名称含 token/secret/code 的元素标为 `sensitive`，后端对指向 sensitive 元素的 `fill` value 在落库前遮断。（关键词表实际是 `token|secret|password|passwd|api_key|apikey|code`，命中的是元素的 name/label；遮断跑在**两个出口**——结果侧的 `browser_log` 与参数侧落库前那份 `arguments`，后者还额外带「未知句柄也遮」与位次从严，见 §7 与 §8 的 R-37/R-39。）
- **US-20** 作为平台，页面文字是待检查数据不是指令；本规格承认注入面已从「读到文字」扩大到「基于文字采取行动」，缓解是工具面锁死在用户自己的 dev 应用、最坏是自伤。
- **US-21** 作为平台，run 收口时后端 best-effort 向 sidecar 发 `DELETE`，空闲 120s 的 session 由 sidecar 自行回收。两条都必须存在，只有 TTL 会让 `mem_limit: 1g` 的容器攒出僵尸 context。

### 展示与历史

- **US-22** 作为用户，聊天里每一步是一张带动作、目标元素名、断言结论的步骤卡，失败步骤带徽标与截图，我能一眼看出 Agent 走到哪、在哪崩。
- **US-23** 作为用户，刷新聊天后历史步骤卡仍然显示；截图沿用 `preview_page` 的会话目录保留与归属校验，不新增存储机制。

## 4. 已定决策

| 维度 | 决策 |
|---|---|
| 架构 | 单步 session 工具（模型逐步驱动），不是一次性交整段脚本。理由：模型看不到中间态就无法自适应，而它面对的正可能是自己刚写坏的页面 |
| 活页面位置 | sidecar 进程内存。Playwright 的 Page 对象不能跨进程，后端不可能持有它 |
| 与 preview_page 的关系 | 并存。快检便宜、无状态，继续用于「改完看一眼」；交互工具用于「走一遍」 |
| 触发时点 | 按需。用户明确要求验证、或 Agent 判断这是交付前最后一关时跑；不每次改完自动跑 |
| 动作确认 | 不逐次确认。dev 是沙箱，边界靠 origin 锁定 + 步数额度 + 收口报告 |
| 动作集 | 6 个：`click` `fill` `press` `select` `scroll_into_view` `navigate`。hover / drag / upload 第一版不做 |
| 元素定位 | 交互元素清单 + `data-zc-ref` 打标，ref 是每次扫描重生成的随机短令牌、只在最近一次返回内有效，每次 step 后全部重打 |
| 断言 | 4 种：`text_contains` `element_visible` `url_matches` `no_new_console_error`。可选，沿用 `db_query` 的 expect 口径 |
| 等待 | 给 expect 就轮询等到满足或超时（默认 3s、上限 8s）；没给只等短 networkidle |
| 截图 | step 默认不带；断言失败与动作失败各自动带一张（R-26），`screenshot: true` 永远优先 |
| 状态增量 | console / 失败请求 / 未捕获异常（`page_errors_new`，R-27）每步只回新增，不回全量；`preview_page` 那侧仍是全量字段名 |
| 原生弹窗 | 一律按取消处理并在结果里显式告知，第一版不提供 accept 动作 |
| session 归属 | 一个 run 一个 session；`session_id` 不进模型参数面，存在 `ctx.run_state` |
| 回收 | 显式 close + 空闲 TTL 120s + 活 session 上限（默认 2），三者并存 |
| 持久化 | 不做。sidecar 重启即全丢，后端按未知 id 降级 |
| 采集代码 | 不新写一套。把 `_inspect_in_context` 的采集与裁剪抽成可复用函数，`preview_page` 与 `browser_*` 共用 |

## 5. 工具契约

### 5.1 schema（`app/agent/llm.py` 新增 `BROWSER_SESSION_TOOLS`）

```json
{"name": "browser_open",
 "description": "在当前应用 dev 预览开一个可交互的浏览器会话，返回标题、可见文本与可交互元素清单；expect 给了就等首屏满足再返回",
 "parameters": {"type": "object", "properties": {
   "path": {"type": "string", "description": "同源相对路径，默认 /"},
   "expect": {"type": "object", "properties": {
     "type":  {"type": "string", "enum": ["text_contains","element_visible","url_matches","no_new_console_error"]},
     "value": {"type": "string", "description": "断言参数；no_new_console_error 不需要"}},
     "required": ["type"]}}}}

{"name": "browser_step",
 "description": "对当前浏览器会话执行一个动作并返回新状态；ref 只在上一次返回的清单内有效",
 "parameters": {"type": "object", "properties": {
   "action": {"type": "string", "enum": ["click","fill","press","select","scroll_into_view","navigate"]},
   "target": {"type": "string", "description": "上一次清单里的 ref（形如 r7kqmbt 的一次性令牌，整批换发）；navigate 时改填 path"},
   "value":  {"type": "string", "description": "fill 的文本 / press 的键名 / select 的选项值"},
   "expect": {"type": "object", "properties": {
     "type":  {"type": "string", "enum": ["text_contains","element_visible","url_matches","no_new_console_error"]},
     "value": {"type": "string", "description": "断言参数；no_new_console_error 不需要"}},
     "required": ["type"]},
   "screenshot": {"type": "boolean", "description": "是否附截图，默认 false；断言失败时无论如何都附"}},
 "required": ["action", "target"]}}

{"name": "browser_close",
 "description": "关闭当前浏览器会话，返回本次路径走查的收口报告",
 "parameters": {"type": "object", "properties": {}}}
```

挂载条件与 `preview_page` 完全一致：仅当 `resolve_session_app` 解析出唯一应用时挂载。

参数面（properties 与 `required`）与 `app/agent/llm.py::BROWSER_SESSION_TOOLS` 一致——实现里 `browser_open` / `browser_close` 还显式写了空数组 `"required": []`，与本文省略该键同义；`browser_step` 的五个属性名由 `tests/test_browser_session_tools.py` 逐字钉住。`description` 是实现文本的**摘要**不是发送原文——实现里三条描述刻意保持一句话，教程住在 `<browser_session>` 提示词块（`browser_session_tools.py::build_browser_session_prompt`），随每次请求发送的是短句。`browser_open` 的属性面是 `{path, expect}`（裁定 R-41 接线，见 §6.3）：那份 `expect` 与 `browser_step` 的是**同一个对象**（`llm.py::BROWSER_EXPECT_SCHEMA`），不是抄出来的第二份形状，四选一枚举因此不可能一侧加档、另一侧掉队。

### 5.2 step 成功结果

```json
{"ok": true,
 "action": "click", "target": "r7kqmbt", "target_name": "提交订单",
 "passed": true, "expect": {"type": "text_contains", "value": "订单已创建"}, "note": "",
 "session": "active", "status": "running",
 "url": "http://localhost:8210/orders", "title": "订单列表",
 "text": "……（截断后可见文本）",
 "interactive": [{"ref": "r4xwntk", "role": "button", "name": "新建", "disabled": false, "sensitive": false},
                 {"ref": "r9jzpsk", "role": "textbox", "name": "备注", "sensitive": false}],
 "interactive_truncated": false,
 "console_new": [], "failed_requests_new": [], "page_errors_new": [],
 "dialog": null,
 "steps_left": 9, "steps_charged": true, "checks_left": 5, "redacted": 0,
 "elapsed_ms": 1420}
```

- `interactive` 上限 40 条，超出时 `interactive_truncated: true`（**不是** `truncated`——那个键沿用
  `preview_page` 的既有语义，指文本/控制台/请求触顶；两个来源在 sidecar 的 `_step_state` 里取逻辑或，
  「本步 900 条错误只报了 40 条」不能被读成一份完整清单）。
  浏览器侧另有一道 200 个节点的遍历封顶，采到封顶也算截断：哪怕这一屏只有 3 个带名字的条目，
  也不能对模型说「清单是完整的」。没有可辨识名称的条目直接丢——Agent 拿到一个 name 为空的 ref 只会瞎点。
  `name` 的实际优先级是 `aria-label > label > placeholder > title > textContent`（都截 60 字）：
  `textContent` 排在四个属性之后，与本文早期草案的写法相反，以 `session_core.INTERACTIVE_JS` 为准。
- `target` 回显的是**模型给的那个字符串**（ref 或 navigate 的 path），不是元素对象；元素名另开
  `target_name` 一个键（裁定 R-32），且它取的是**动作之前**那份清单——新快照里的令牌是这一轮
  现生成的，旧句柄在里面根本查不到（§6.2），拿它标「这一步动的谁」只会画出一个空目标。
  查得到才写这个键，查不到就不写（卡片自行回落「句柄 r7kqmbt」那样的令牌回显）。
  回落文案**不按句柄长相分句柄与 path**：只有 `/` 开头的是 navigate 的路径，其余一律当句柄——
  令牌形状只在 sidecar 的纯函数层定义一处，前端或后端抄一份形状正则就成了第三处锁步同步点。
- `passed` 只在给了 `expect` 时出现；不给则 `passed` / `note` 两个字段一起缺席，模型自判。
  给了 `expect` 时 `note` 键**恒在**：`judge_expect` 通过时也返回一句失败措辞，所以通过那一步是空串。
  open 那一面是同一对字段、同一道闸（`_shape_state`，裁定 R-41）：`browser_open` 带 `expect` 时返回值里
  就有 `passed` / `note`，不带时一并缺席；但 open 的返回值**没有** `action` / `target` / `steps_charged`
  那三个回显键——它不是一步，§6.3 的四条口径（不扣步数、不自动附图）就是从这条形状差异上立住的。
- `page_errors_new` 是第三本增量账（裁定 R-27）。字段名与 `/inspect` 的全量 `page_errors` **有意
  不对称**，别「统一」掉它：快检是一次性全量检查，走查每步只报新增，改回全量等于每一步都重播
  首屏那次崩溃。
- `dialog` 非 null 时表示本步出现过原生弹窗：`{"type": "confirm", "message": "...", "handled": "dismissed"}`。
- `screenshot` 这个键**只在「该补一张现场图」时出现**（裁定 R-26）：断言未通过、动作层面的失败，
  或模型显式写了 `screenshot: true`。成功走查的响应里没有这个键（不是 `null`）——一次成功走查每步
  一张图会把 token 成本翻几倍。补图本身失败时是 `screenshot: null` 加一句 `screenshot_note`，
  已经拿到手的断言结论不跟着丢。
- `steps_charged` 在成功与失败结果里都出现：本步是否扣了步数额度。为 `false` 的是「什么都没做成」
  那几条出口——后端的四条本地拒绝（未 open、额度用尽、path 非法、缺 target）、传输/拒绝失败、
  sidecar 的「会话已没了」与「未知动作」；其余（含 ref 失效与点不动）为 `true`，Agent 确实发起了
  一次动作就该记账。
- 其余字段（`status` / `session` / `checks_left` / `redacted` / `text_truncated` / `truncated` /
  `timed_out`）沿用 `preview_page` 那一套闸口计数与截断位；`session` 只是 `"opened"|"active"`
  的标记，前端不依赖它。
- 截图仍走 `browser_shot_service`，URL 与鉴权口径不变。

### 5.3 失败结果（`ok:false`，不抛 `ToolError`；表里「expect 未满足」那一行是唯一故意留成 `ok:true` 的）

| 场景 | 返回 |
|---|---|
| 会话不存在/已回收 | `{"ok": false, "error": "浏览器会话已结束，请重新 browser_open", "session_gone": true, "steps_charged": false, "steps_left": n, "interactive": []}`（追认见下第 4 条） |
| ref 已失效 | `{"ok": false, "error": "ref 无效或页面已变化，请用最新清单里的 ref", "interactive": [...], "steps_charged": true, "screenshot": {...}}` |
| 元素不可点 / 动作失败 | `{"ok": false, "error": "元素不可点击或动作失败: <Playwright 原因>", "interactive": [...], "steps_charged": true, "screenshot": {...}}` |
| 未知动作 | `{"ok": false, "error": "未知动作: <原样>", "steps_charged": false}`（schema 有 enum，但后端不重校，模型写出越界值就走这条；sidecar 不重采清单，回给模型的 `interactive` 因此是空数组） |
| expect 未满足 | `{"ok": true, "passed": false, "expect": {...}, "note": "...", "screenshot": {...}, "console_new": [...], "page_errors_new": [...]}`（走通了但断言不过，不是工具失败） |
| 步数额度用尽 | `{"ok": false, "error": "本 run 步数已用完，请 browser_close 收口", "steps_left": 0, "steps_charged": false}` |
| 会话未开 | `{"ok": false, "error": "还没有浏览器会话，先 browser_open", "steps_charged": false}` |
| 预览未运行 | `{"ok": false, "error": "预览未运行", "status": "stopped", "url": "<display base>", "hint": "预览未运行：先用 preview_restart 启动"}`（`starting`/`stopping` 时 hint 是「预览正在启动，稍后再试」） |
| navigate 路径非法 | `{"ok": false, "error": "path 必须是同源相对路径（以 / 开头）", "steps_charged": false, "steps_left": n}` |
| sidecar 不可用/已满 | `{"ok": false, "error": "浏览器服务不可用" \| "浏览器服务已满（活会话达上限），请等前一个走查结束", "hint": "..."}` |
| Chromium 崩溃 | `{"ok": false, "error": "浏览器会话异常终止，请重新 browser_open", "session_gone": true, "steps_charged": false}` |

注意 expect 不过返回的是 `ok:true, passed:false`：动作执行成功与业务断言通过是两件事，混在一个字段里会让模型分不清「我点不动」和「点了但结果不对」。这条分形在失败结果里同样成立——`ok:false` 那几行**没有 `passed` 键**，`page_errors_new` 只是「这一步把页面点崩了」的旁证，不是断言结论（裁定 R-29）。

四处对规格原文的追认（都是七轮修复或最终修复波里定下来的，不是笔误）：

- **「ref 已失效」也自动附截图**。裁定 R-26 把补图口径定成「这一步没能往前推进」，ref 失效正落在这一条里；原文那一行漏写 `screenshot`，实现按同一口径带了。
- **失败的那一步也带三段增量**（`console_new` / `failed_requests_new` / `page_errors_new`，裁定 R-29）：「点下去把页面点崩了、但动作本身报失败」是真机最常见的一格，只给一张图和一句原因，模型只能靠图猜。
- **`error` 文案以实现为准**：「元素不可点击，可能被遮挡」（US-10 的描述性说法）落地成 `元素不可点击或动作失败: <原因>`，因为这一支同时覆盖 Playwright 的超时、严格模式命中多个元素、执行期抛错等好几种原文，替模型改成一句猜话反而丢信息。
- **`session_gone` 那一行的 `interactive` 是 `[]`，不是 `[...]`**（最终修复波第 2 批 5c 追认）：会话物理上已经没了，清单采不回来，实现回的是空数组；前端也不画空清单（`BrowserFlowCard.test.js` 的「失败结果不画空元素清单」钉的就是这一格）。原文写 `[...]` 是**表格比实现乐观**——按它读会以为还能拿一份新清单换个 ref 再试，而这里唯一可行动作是重新 `browser_open`。就地改成与实际一致，实现不动。

**交互清单为空时另带一句 `hint`**（最终修复波第 2 批 5a）：`browser_open` / `browser_step`（成功与失败两支）在裁给模型的清单为**空数组**时，结果里多一个 `hint` 键，点名「打标脚本只认按钮/链接/输入框，拖拽 / 文件上传 / hover 才出现的东西不在清单里，这三类第一版不做」，并明确「这不代表页面没有可操作部件」。
理由见 §8 那条改准的残余风险：真机探针（`probe/drag.html`，结果在 `probe/probe-results-7.json` 的 `drag_visible_elements: []`）里 `<div draggable="true">` 匹配不上任何选择器，模型手上的是一句空白的「清单为空」，它会据此下「这个应用没按钮 / 坏了」的结论——那是把工具边界读成应用属性。`hint` 这个键不是新增形状：`preview_page` 一侧本来就有它（§5.2 沿用同一套字段），前端 `BrowserFlowCard` 也早已渲染 `data-test="hint"`。

`steps_charged: false` 的那几行还有一条硬约束：**被拒绝的调用不许留下副作用**（裁定 R-24，仓库既有不变式，先例是 `engine.py::start_dev` 的 `preflight_docker` 与 `preview_tools::_restart` 的顺序）。五条本地拒绝全部排在打 sidecar 之前，且不写 `run_state`。

## 6. sidecar 契约

### 6.1 接口

- `POST /sessions` → `{"ok": true, "session_id": "...", "title", "text", "interactive", "console", "page_errors", "failed_requests", "dialog", "elapsed_ms"}`（首轮是全量账；给了 `expect` 时另带 `passed` / `note`）
- `POST /sessions/{id}/step` → 见 §5.2 形状（sidecar 返回原始版，后端做遮断与落盘）
- `POST /sessions/{id}/screenshot` → `{"ok": true, "screenshot_b64", "screenshot_width", "screenshot_height"}`（裁定 R-4：断言失败/动作失败时后端替模型补的那一次，**不进 `ACTIONS`**、既不 `touch` 也不计步）
- `DELETE /sessions/{id}` → `{"ok": true}`（幂等，未知 id 也回 ok）
- `GET /sessions` → `{"active": [{session_id, steps, idle_seconds}]}`：**只作排障**，不进后端调用面（`session_id` 一律不许出现在模型可见面上，见 §7）。
- `GET /healthz` 不变；`POST /inspect`（`preview_page` 用）不变。

**三种响应形状，按 status_code 分支读**（读串了会拿到空错误串或一句 Python repr）：400/409/503 是 `HTTPException` 的固定外壳 `{"detail": str}`；422 是 pydantic 的 `{"detail": [dict]}`，`detail` 是**列表**；常态结果一律 200，失败也留在 200 里带 `ok:false` 和原因。

会话端点**刻意不过 `/inspect` 那个全局信号量**（`MAX_CONCURRENCY = 2`）：一个走查会话能活几分钟，占住全局槽位会把 `preview_page` 整条路径饿死；会话的并发上界由活会话数上限来管。

### 6.2 session 注册表

进程内 `dict[session_id] = Session`，字段是 `session_id / context / page / origin / collectors / console_seen / failed_seen / page_errors_seen / last_used / steps / dialog / opening`（`browser/sessions.py` 的 dataclass）。两点和早期草案不同：

- **注册表里没有 `refs`**。给模型的清单存在**后端**的 `run_state["browser_refs"]`；sidecar 每次都现采现打，不留「上一次发出去的清单」，也就没有第二份会漂的副本。
- 三本增量账（console / 失败请求 / pageerror）跟着**会话**走，不跟着一次采集走：监听器收的是全量，每步要报的是「自上次以来新增」。

三条不变式：

1. **上限即拒绝**：`len(SESSIONS) >= ZEROCODE_BROWSER_MAX_SESSIONS` 时 `POST /sessions` 直接 409，不排队、不淘汰别人正在用的 session（淘汰会让另一个 run 莫名断线）。
2. **空闲回收**：后台巡检每 10s 扫一次（`SWEEP_INTERVAL_SECONDS`），`now - last_used > ZEROCODE_BROWSER_SESSION_TTL_SECONDS` 的 session 关闭 context 并移除。**打开中的会话（`Session.opening`）不参与空闲判定**：一次 open 里最长的 await 就是 `goto` 本身（`timeout_ms` 上限 120000，外面还套 +5s 宽限 = 125s > TTL 120s），逐 await 边界补 `touch` 修不到中间那一百多秒——真修掉的后果是巡检在 `goto` 还没返回时关掉 context，报出去的「页面打开失败」成了失真文案。巡检循环自己炸了不退出：一次异常把任务摘掉，注册表里的 context 就只能等进程退出，而 `mem_limit: 1g` 会先一步到。
3. **origin 固化**：session 创建时记下目标 origin（折成 `scheme://host[:port]`，不带 path），`navigate` 只允许同 origin 的相对 path；主文档导航仍走 `allow_request`，跳出即阻断。

ref 实现：`browser/session_core.py::new_ref_tokens()` 每次扫描生成一批**互不相同的随机短令牌**（形状 `r` + 6 位，字母表 `abcdefghjkmnpqrstuvwxyz23456789`，去掉 `0 o 1 l i` 这类易混字符；池大小 = 遍历上限 200，两处同值有测试钉住），整份数组作为参数传给打标脚本（`page.evaluate(INTERACTIVE_JS, tokens)`），脚本按元素顺序取 `tokens[i]` 并 `setAttribute` 同一个值。**令牌在 Python 侧生成、JS 不摇随机数**是刻意的：唯一性与形状策略必须在纯函数层可单测，而且只能有一处。字符集刻意不含任何需要转义进 CSS 属性选择器的字符，所以 `selector_for` 拼 `[data-zc-ref="…"]` 之前只需判形状。

**每次 step 后重新打标，旧 ref 全部作废，而且「作废」是机制检测得到的事**——采集脚本一进页面就先清掉上一轮所有该属性，再领一批**新令牌**重打，所以上一轮发出去的句柄在新清单里几乎必然查不到，落回 `{"ok": false, "error": "ref 无效或页面已变化，请用最新清单里的 ref"}` 那条既带回新清单又自动附图的出口（§5.3）。这条曾经是假的：序号形状下旧序号在新清单里**大概率仍然合法**，机制只能报「成功」，于是静默点到另一个元素（真机复现，见 §15）。副作用（「ref 只在最近一次返回内有效」仍是物理事实）：Vue 重建 DOM 节点会连属性一起丢掉，而令牌每轮整批换发意味着后端两处 sensitive 判定与元素名回显**只能**认「动作之前」那份清单（§7、§8 R-37）——同一个令牌在新清单里重现是概率性的小数事件（量级见 §8 那条尾注，不是数学上的零），反查因此只会得到空名字。

### 6.3 等待策略

- 动作执行后：给了 `expect` → 在 `agent.browser_expect_timeout_ms`（默认 3000、上限 8000）内轮询该断言，命中即返回；未命中则等满超时并回 `passed:false`。同一个 `timeout_ms` 在 sidecar 侧**一次管两件事**：动作的可点等待上界与断言的轮询预算。
- 没给 `expect` → 只尝试短 `networkidle`（沿用现有 3s 上界），拿到当下状态。
- 固定 `settle_ms` 降级为 step 的**可选参数，默认 0**，只用于「动画结束后还想再看一眼」的场景，不再是默认路径。它**不复用** `agent.browser_settle_ms`（那个键属于 `preview_page` 的一次性检查，默认 1200 保持不变）——两条路径的等待语义从此分家，别再拿一个键去管两件事。
- **`no_new_console_error` 只看 `console`，不看 `page_errors`**（`session_core.judge_expect` 的入参只有 `console_new`）。这是 R-27 之前的既有判定面，**未随增量账扩展**：一个只抛未捕获异常（`pageerror`）、不打 `console.error` 的崩页——Vue 运行时的多数异常正是这个形状——照样能穿过这条断言拿到 `passed:true`。要看崩没崩得去读 `page_errors_new`，别把这条断言当「页面没炸」的证明。
- **`browser_open` 的 `expect` 已接线**（裁定 R-41，US-06 的落点）：`browser_open` 参数面里那份 `expect` 与 `browser_step` 的是**同一个对象**（`llm.py::BROWSER_EXPECT_SCHEMA`，§5.1），后端 `_open` 原样透传给 `POST /sessions`，由 sidecar 的 `_open_step` 走同一份 `await_expect` 轮询——首屏等到目标文案才返回，动画中段的中间态不再进模型上下文。四条口径要记住：① 它是 open 的一部分、**不是 step**，因此不占步数额度（`browser_steps_used` 不动，而 open 自己照旧花 `agent.browser_max_per_run` 那一本账）；额度判定 → path 校验 → 自动关旧三者的既有顺序一字未动（裁定 R-24）。② 结论只经 `_shape_state` 那一道唯一闸口回 `passed` / `note`（`passed` 只在给了 expect 时出现、`note` 只在 `passed` 为假时给，与 step 同形），不新开出口、不绕过 `scrub_known_host`。③ open 的 expect 失败**不自动附现场图**：R-26 那条「失败自动附图」的口径只覆盖 step——open 的失败形状里没有 `steps_charged`，步骤卡也不按 open 的返回值找图。④ 轮询预算与 step 那一档仍不同：`_open_step` 用写死的 `STEP_TIMEOUT_MS_DEFAULT`（3000ms），**不读** `agent.browser_expect_timeout_ms`（§10 同一行记着，接线没有改变它）。

### 6.4 运行约束

容器约束与 0031 完全一致，不新增豁免：`user 10001`、`read_only: true`、`tmpfs /tmp`、`cap_drop: [ALL]`、`no-new-privileges`、`mem_limit: 1g`、`cpus: 1.0`、`pids_limit: 256`、不发布宿主端口（这句指**交付包 compose**；本地 dev 的 `browser/docker-compose.yml` 发布 `127.0.0.1:8101` 是给宿主上的后端用的，不是服务器形态的豁免）。**新增一条断言到打包测试**：活 session 数上限与 TTL 必须在 compose env 里显式出现，不接受只靠代码默认值（默认值漂了没人发现）——落地形态是两条：交付包 compose 的键与 `.env.example` 的覆盖位，以及「代码默认 == 交付包烘的值 == 本地 dev compose」那条三源等式。

## 7. 后端接线与生命周期

`app/agent/browser_session_tools.py`（**新文件，不叫 `browser_tools.py`**——那份留给 `preview_page`，两者只共用 `_checks_left` / `_clean` / `_error` / `_int_cfg` / `_redact` / `normalize_path` / `browser_service_url` 这几个既有工具函数）。

`ctx.run_state` 里这一族键就是本功能的全部服务端状态（`run_state` 本身不落库，见 `tools.py` 的字段注释）：

| 键 | 语义 |
|---|---|
| `browser_session` | 当前绑定的 sidecar `session_id`；模型手上永远看不到它（ADR-0037）。一个 run 一个，`session_gone` 时置 `None` |
| `browser_steps_used` | **per-run** 步数账（不是 per-session）。写成赋值而不是 `setdefault` 就把口径改成了 per-session，「open→12 步→close→open→12 步」会合法地把模型上下文的成本上限悄悄翻倍 |
| `browser_refs` | **sidecar 返回的那一份清单**（未过后端的条数裁剪与 `name` 遮断，判定要的 `sensitive` 字段因此在），语义是「上一次返回给模型的清单」——对本步而言就是**动作之前**那份。§8 R-37 讲的就是它会过期 |
| `browser_log` | 逐步账本，`browser_close` 的报告由它算出来。只在内存里，`value` 键不进 report、也不单独落库（见 §8「明文落盘出口只有一处」） |
| `browser_checks` | open 与 `preview_page` **共用**的那个额度计数器 |
| `browser_opens` / `browser_sessions_replaced` | 本 run 真正建成过几个会话 / 其中几个是被重复 open 顶掉的。两个数分开记：`replaced + 1` 冒充「开过几个」会在报告里说一句低估的假话 |

- **value 遮断是两侧的事**：结果侧 `_step` 写 `browser_log` 时按 `browser_refs` 判 sensitive；参数侧由 `loop.py` 在**派发之前**调 `redact_sensitive_step_args` 改写落库/推送那一份 `arguments`。两处共用同一个判定函数，一处遮一处不遮就是漏。
- 参数侧天生只能拿到本轮开始时那份清单，所以在「清单说了也不算」之外多两条从严（Critical C-1 的裁定 R-34/R-35/R-36/R-38）：**未知句柄也遮**，以及**位次从严**——同一条消息里排在第 2 位起的浏览器调用（按 `function.name` 命中名单数，不看它的 `arguments` 能不能解析）一律按 sensitive 处理。代价是同轮多调用时非口令的 fill 也显示成占位符（每次走查 0–2 条的量级），而 sidecar 拿的仍是原值，功能不断。
- 工具执行收的是 `original_calls`（原值必须送达 sidecar，否则 US-08 的登录根本填不进去），当轮回注模型的 assistant tool_calls 也留原值，否则模型下一步重试时会把占位符当口令填进表单。
- 收口：`AgentRunner` 的 run 结束路径上 best-effort 发 `DELETE`（`close_browser_session`，硬超时 10s），失败只记日志（TTL 兜底）。这条与 `preview_page` 的「无副作用、无需收口」不同，是交互工具唯一引入的运维义务。
- 分派：`execute_tool` 新增 `BROWSER_SESSION_TOOL_NAMES` 分支，沿用惰性 import 与「失败一律是正常结果」的既有约定。
- 采集复用：`browser/app.py` 里 `_inspect_in_context` 的采集与裁剪部分抽成共享函数 `collect_page_state(page, collectors, *, started, timed_out, interactive_limit, include_screenshot)`，`/inspect` 与 session 两条路径都调它（sessions 侧再包一层 `snapshot_state`），截图策略只用 `include_screenshot` 表达；口径分岔只用参数，不靠复制函数。
- 地址口径：`collect_page_state` 回的 `url` 是 `_connect_url` 重写后的**容器可达 IP**，不是给用户看的地址。后端一律折成 `display_url`（`preview_url` + `path?query`，裁定 R-16），sidecar 自由文本里的内部 `host:port` 过 `scrub_known_host` 等价改写（裁定 R-22/R-28）。边界规则一条：**凡 sidecar 报告的 URL 一律清洗，凡页面自身的内容一律不清洗**。

## 8. 安全边界与残余风险

**做得到**

- 模型没有 URL 参数，也没有 `session_id` 参数；目标由后端按应用 dev 端口 + 校验过的相对 path 构造。
- session 是 sidecar 新建的干净 context，不读取宿主浏览器状态。
- 主文档限目标 origin，子资源限同源 + 公网，私网与平台容器别名继续 `route.abort()`。
- 页面文本、console、请求 URL、断言 value 全部过 `redact_sensitive`；sensitive 字段的 `fill` value 在**落库那一份参数与结果账两处**都遮断（§7 的两侧口径）。
- 步数与 open 次数双额度，加空闲 TTL 与并发上限，防把 sidecar 拖垮。

**做不到 / 残余风险**

- **注入面扩大是实质性的**：0031 时代页面文字只能影响模型的判断，现在能影响它的动作。接受理由限定在「目标只能是用户自己的 dev 应用」，最坏是自伤。
- 截图与 `fill` value 的遮断都不是完备的：非 password 命名的敏感输入（身份证号、密钥明文框）不在 sensitive 判定内，只能靠规则表兜。
- 动作可以改 dev 数据且**不回滚**：与 `db_write` 同一口径——遇错即停，已成功的不撤销。收口报告只报事实，不承诺恢复。
- 拖拽类交互点不到（第一版无 `drag`），**而它的失效方式原先写错了**：原文承诺这类应用会得到诚实的「元素不可点击」。真机实测（`probe/probe-results-7.json` 的 `drag_visible_elements`，探针页是 `probe/drag.html`：一个 `<div id="piece" draggable="true">` 加一个 drop 区）——`<div draggable="true">` **匹配不上打标脚本的任何选择器**，于是模型看到的交互清单是**空的**——它读到的是「这个页面没有可操作元素」，不是「我不会拖拽」。这是最坏的一种「不行」：**看起来像应用的属性，其实是工具的边界**，症状从「一句诚实的失败」变成「一句错误的成功归因」。只有当页面上另有可点目标、模型把 drag 目标当 button 去打时才落到「元素不可点击」那一格。现已在清单为空时随结果带一句 `hint`（落点与理由见 §5.3 末），点名拖拽 / 上传 / hover 三类已知不做项。
- **清单为空那句 `hint` 不是穷举**（最终修复波第 2 批 5b）：它只点名三项已知不做项（拖拽 / 文件上传 / hover 才出现的部件，§11 那张非目标表里的头几项）。打标脚本还有别的不认的东西（自定义 `contenteditable` 部件、canvas 内绘制的控件、iframe 里的控件），将来加动作时这句话也会落后于实现——**它是当前不做项的清单，不是「页面为什么可能是空的」的完整答案**。
- **sensitive 输入框的明文图仍有一条自愿的出口**（最终修复波第 2 批 5b）：最终修复波第 1 批已把「sensitive `fill` 失败自动补图」这条明文泄漏堵掉（文本遮了、又被那张图原样放回聊天），但模型**显式**写 `screenshot: true` 时仍然给出一张可能含明文的现场图。这是它的主动选择而不是本功能替它做的决定，因此保留；prompt 里已写明「那些框里的字是明文渲染的」。要彻底封只能让 sensitive 目标无视显式请求，那会毁掉「模型确实需要看现场」那一格，本版本不接受。
- **旧句柄的失效是概率性的，不是数学上不可能撞上**：令牌每次扫描换发一批（约 200 个），令牌空间是 31^6 ≈ 8.9 亿，所以某个旧令牌在新扫描里**重现**的量级约 `200 / 8.9e8 ≈ 2e-7`（一次走查十几步、几步旧令牌，累计仍是 1e-6 量级以下）。撞上的后果退回序号时代那格「静默点到另一个元素」，但不再是**大概率**事件而是尾注事件；本版本接受它，不改成「扫描间不重用的持久映射表」（那会把「不留第二份会漂的副本」这条 §6.2 前提换掉，还会让 TTL/并发上限重新变成必须正确回收的状态）。要彻底封死只能给每次扫描加一个会话内自增前缀——那是又一次「稳定」，而稳定正是本条要修的病根。
- 活 session 期间页面 JS 可持续运行（定时器、WebSocket），最坏影响是 sidecar 自身资源占用，由 TTL 与上限约束。

**三条由七轮修复挣出来、本版本未修的风险（措辞按最终实现，不淡化）**

- **R-37：非浏览器调用触发 HMR 之后，「动作前那份清单」在参数侧与结果侧一起过期。**
  `fs_write` / `edit_file` 改源码、`preview_restart` 起新栈（以及外延点名的 `preview_page`——它自己不改页面，但同样不刷新 `browser_refs`）让 dev 预览重渲染之后，`run_state["browser_refs"]` **不会**更新，于是两侧读到的是同一份过期清单。此时**两侧同结论**，不违反 §7 的一致性口径，但**绝对判定是错的**：Vue 常常是就地打补丁而不是重建节点（`:type` 一翻，同一个 `<input>` 就从搜索框变成口令框，而 `data-zc-ref` 不是 Vue 管的属性，节点活着它就活着），于是真实口令框可以被填、值可以明文进 `message.tool_args`。堵它需要一条新裁定（「写源码 / 重启预览就作废轮初清单」），**且不得用「清空 `browser_refs`」实现**——参数侧对空清单判「未知」会遮，结果侧对空清单判 False 不遮，那会当场造出新的两侧分家。本版本未修，留待后续规格（裁定 R-40 登记）。
  ref 改成每次扫描换发的随机令牌（§6.2）之后，这一条的**范围收窄但一条没少**：「同一个旧句柄在新清单里指向另一个元素」那一半没有了（旧令牌在新清单里几乎必然查不到，因此落进参数侧的「未知即遮」）；剩下的正是这里写的这一半——**轮初清单里查得到的那个令牌**，它的节点被就地改了类型，两侧同判 False，绝对判定仍然是错的。
- **R-39：不可解析的 `arguments` 一律「不遮不改」，这是全工具共用的既有设计。**
  `app/agent/redact.py` 的 `rewrite_tool_calls_args` 对 JSON 解析失败的条目原样返回。可复现形状：同一条消息里两个调用**共用一个流式 `index`** 时，`app/agent/llm.py` 会把它们的 args 字符串拼接成一条（`slot["function"]["arguments"] += ...`），解析失败、但原文里的凭据照样进 `message.tool_args`（派发端把它换成 `{}`，动作实际没跑）。**它不属于 R-37 / C-1 那一族**——两侧对同一个 value 给的是同一个结论，不构成「一处遮一处不遮」。堵它要先决定「不可解析的 args 是否整串换成占位符」，影响面是全工具共用路径，**须另立规格**，不在本分支夹带。
- **明文落盘的出口只有一处，别把「两侧一致」读成「有一侧兜底」。**
  `fill` 的 `value` 只有参数侧（`message.tool_args`）会落库；结果侧的 `browser_log` 里那份 `value` **不进** `browser_close` 的 report，`run_state` 也不落库，逐步账本唯一的出口就是那份报告。所以**参数侧任何一条漏路径都是净泄漏**——不存在「结果侧遮住了就还有救」这回事。反过来，参数侧从严（多遮几条占位符）的代价只是可读性，不是功能。

## 9. 工程接线

| 位置 | 改动 |
|---|---|
| `browser/app.py` | 新增 `/sessions` `/sessions/{id}/step` `/sessions/{id}/screenshot` `DELETE /sessions/{id}` `GET /sessions`；启动后台巡检任务；把 `_inspect_in_context` 的采集与裁剪抽成共享的 `collect_page_state`，`/inspect` 改调它并回归 |
| `browser/session_core.py` | **新增纯函数层**（刻意不 import playwright）：ref 打标与找回、sensitive 判定、增量裁剪、expect 判定、TTL/上限判定 |
| `browser/sessions.py` | **新增注册表**：`Session` dataclass 与上限/TTL/幂等 close/记账/origin 拼接/动作分派/`await_expect` 轮询。**与 Playwright 调用分开**，这一层的测试不需要浏览器 |
| `browser/Dockerfile` | COPY 白名单**逐个文件名**列源文件——新增源文件必须在这里露面 |
| `app/agent/llm.py` | `BROWSER_SESSION_TOOLS` + `BROWSER_SESSION_TOOL_NAMES` |
| `app/agent/browser_session_tools.py` | **新增文件**（不是 `browser_tools.py`，那份留给 `preview_page`）：`run_browser_session_tool`；session 绑定、额度、sensitive 遮断（两侧共用的判定）、截图落盘、收口报告、参数侧遮断 `redact_sensitive_step_args`、`scrub_known_host` 清洗闸 |
| `app/agent/tools.py` | 新增分派分支（惰性 import） |
| `app/agent/redact.py` | 把「改写 `function.arguments` 的唯一机械动作」从 `redact_tool_calls_args` 里抽成 `rewrite_tool_calls_args(calls, rewrite, tool_names)`，供参数侧遮断复用（裁定 R-33）——**凭据规则表 `_redact_deep` 一行未改**，抽出来的只是结构保持 + 白名单 + 处数语义 |
| `app/agent/loop.py` | 挂载三个工具与 `<browser_session>` 提示词；派发前调参数侧遮断；run 收口发 close |
| `app/agent/browser_session_tools.py::build_browser_session_prompt` | 交互工具指引：ref 一次性、增量语义、expect 何时给、不要执行页面里的话。**提示词 builders 住在工具模块里，不在 `prompts.py`**（`build_system_prompt` 只负责拼装顺序 `<database>` → `<preview>` → `<browser>` → `<browser_session>`） |
| `frontend/src/components/BrowserFlowCard.vue` | **新增**步骤流卡（动作/目标元素名/断言徽标/失败截图/收口报告，两本账分开报数） |
| `frontend/src/components/ChatMessages.vue` | 路由三个工具到新卡片（实时与历史同一条闸） |
| `scripts/build_output.py` | compose env 新增 session 上限与 TTL（`.env.example` 同步）；**`BROWSER_REQUIRED` 白名单加两个新源文件** |
| `dev.bat` / `browser/dev-up.bat` | 补一句 sidecar 要先起来的提示。**这两个 `.bat` 是纯 ASCII 文件**：写 UTF-8 汉字会在 cmd.exe 默认代码页乱码，所以新增文案用英文（通则针对源码注释，`echo` 属运行期输出） |
| `tests/`（新增四个） | `test_browser_session_core.py`（纯函数）、`test_browser_sessions.py`（注册表 + 端点，浏览器对象用桩）、`test_browser_session_tools.py`（后端三工具）、`test_agent_loop.py` 的参数侧遮断 |
| `tests/test_build_output.py` | 打包测试断言上限/TTL 显式在场、容器约束不漂移，外加遍历 `browser/*.py` 的不变式（见下） |
| `CONTEXT.md` | 新增术语「浏览器会话（browser session）」「交互元素句柄（ref）」，并修订「预览页检查」条目 |
| `browser/README.md` | 新增「会话生命周期」小节：端点面、超限拒绝的理由、TTL 巡检与 `opening`、ref 的物理时效、目录只放白名单源文件 |

**这条耦合是本次踩了两次坑的地方，单独写出来**：往 `browser/` 加一个 `.py` 源文件，必须**三处同步**——`browser/Dockerfile` 的 COPY 名单、`scripts/build_output.py` 的 `BROWSER_REQUIRED`、以及 `browser/*.py` 那个文件本身。漏了哪一处都不会让包坏掉（`copytree` 整目录搬运，文件照样进包），但症状是**容器起来即崩、`preview_page` 与 `browser_step` 恒回「浏览器检查服务不可用」**，离根因很远。Task 2 漏了 `session_core.py`、Task 3 漏了 `sessions.py`，两次都是这一条。封它的是 `tests/test_build_output.py::test_browser_required_covers_every_sidecar_source`：遍历目录断言每个 `.py` 都在白名单和 Dockerfile 里露面（不写第二份名单，第二份名单自己会漂）。反过来说，**本地测试辅助文件别放 `browser/`**，放进去就会被这条测试当成「该入包的源文件」而误红——请放 `tests/`（`browser/README.md` 里也写了这一条）。

## 10. 配置项

每个键都核过「真的有人读」。**第 4 轮之前 `agent.browser_interactive_max` 是个死键**（sidecar 侧支持、后端不传），按裁定 R-23 补接线而不是从本表删掉——删规格条目等于悄悄降低交付面。

| 键 / 环境变量 | 默认 | 谁读它 | 漂了会怎样 |
|---|---|---|---|
| `agent.browser_max_per_run` | 6 | `browser_tools.py::_checks_left`（夹 0..100），计数器 `run_state["browser_checks"]` 由 `preview_page` 与 `browser_open` **共用** | 调小把两条路径一起收紧（走查里做一次快检也占 open 额度）；`<=0` 不限；>100 夹到 100 |
| `agent.browser_max_steps_per_run` | 12 | `browser_session_tools.py::_steps_left`（夹 0..200），账记在 `run_state["browser_steps_used"]`，**per-run** | 调小更早撞 US-13「步数已用完」；调大就是放宽模型上下文的成本上限（这个键的作用就是它）；`<=0` 不限，sidecar 的活会话上限与 TTL 仍兜资源 |
| `agent.browser_expect_timeout_ms` | 3000 | `_step` 的请求体 `timeout_ms`（后端夹 500..8000，sidecar 再夹一次同区间）；一个数**同时**管动作可点等待上界与断言轮询预算 | 调大→最坏单步更久（客户端 `STEP_TIMEOUT_SECONDS = 90` 兜底）；**对 open 那一档轮询无效**——`_open_step` 用的是写死的 3000 常量（§6.3） |
| `agent.browser_interactive_max` | 40 | `_interactive_max`（夹 1..200）两处：透传给 sidecar 的 `interactive_max`，以及后端裁剪 `interactive` 清单 | **生效区间实际只有 1..40**：sidecar 的 `_interactive_limit` 只允许往下调（放大等于让调用方自己决定泄漏多少），写 200 也只是把 ≤40 条原样切回来。调小确实省 token；`name` 恒截 60 字、无名条目恒被丢掉，与本键无关 |
| `ZEROCODE_BROWSER_MAX_SESSIONS` | 2 | `browser/sessions.py::max_sessions()`，每次 `POST /sessions` 读进程 env | 调大＝更多活 context 挤在 `mem_limit: 1g` 里；**写 0 是「不限」而不是「禁用」**（`can_open` 的 `limit <= 0` 分支），这行填 0 等于拆掉这道闸。三处同源：代码默认 / 本地 dev compose / 交付包烘的值 |
| `ZEROCODE_BROWSER_SESSION_TTL_SECONDS` | 120 | `sessions.session_ttl()`，巡检每 10s 读一次 | 调短→走查中途被回收，US-12 那句「会话已结束」会频繁出现（不扣步数）；调长→僵尸 context 占内存。**同样 0 = 永不回收**（`expired()` 对 `ttl <= 0` 恒 False），只剩显式 close 兜。另：它必须大于一次 open 的预算（125s）才不会出现「打开中被杀」，这正是 `Session.opening` 存在的原因 |
| `agent.browser_timeout_seconds` | 45 | 既有键沿用：`_inspect`（`preview_page`）与 `_open`（导航超时；HTTP 客户端超时 = 它 + 15s）各读一次 | 调小→慢首屏的应用 open 直接吃「页面打开超时」；sidecar 侧夹 10000..120000 |
| `agent.browser_settle_ms` | 1200 | 既有键，**只有 `preview_page` 读**（`_inspect` 的固定 sleep） | step 不读它（§6.3 的两条路径分家）；漂了只影响快检的等待，不影响走查 |
| `ZEROCODE_BROWSER_URL` / `ZEROCODE_BROWSER_TARGET_HOST` / `BROWSER_ALLOWED_HOSTS` | `http://127.0.0.1:8101` / `host.docker.internal` / `host.docker.internal,127.0.0.1,localhost` | 既有三件套：后端连 sidecar、sidecar 连应用 dev 端口、sidecar 的 host 白名单 | 后两者必须同步改：只改目标宿主不改白名单，`preview_page` 与 `browser_step` 都恒回「浏览器检查服务不可用」——症状与「sidecar 没起来」「缺源文件」完全一样，排障时先分清这三种 |

`agent.*` 这些键是会话/账户配置里的自由键（`ctx.cfg`），没有默认值登记表：读不到就用代码里的默认，写错类型也回落到默认（`_int_cfg` 吞 `ValueError`）——所以**键名漂了的表现是静默用默认值**，`tests/test_browser_session_tools.py` 用 `INTERACTIVE_CFG_KEY` / `EXPECT_TIMEOUT_CFG_KEY` 两个常量把键名逐字钉住，就是为了给这条留一只闸。

## 11. 非目标

- 不做批量动作脚本（`steps: [...]` 一次交整段）。单步契约跑顺后若往返成本确实过高，再挂批量入口，复用同一 step 契约。**实际结论**：本版本按单步契约交付，批量入口没做，也未触发「必须做」的证据——那个「往返成本过高」的判据要靠 §15 走查记录里的实际步数/往返数说话，而它**尚未采集**（走查由主会话跑真机回填）。所以这条非目标仍然成立，但它的重启条件目前是空的，别把它读成「已验证单步够用」。
- 不做 hover、drag、upload、文件下载、剪贴板、iframe 内定位。
- 不做视觉回归基线与截图对比；不长期保存中间态。
- 不做多标签页/多窗口；一个 session 一个 page。
- 不复用用户宿主浏览器的登录态，不连真实 Chrome（见 ADR-0037 被否决项）。
- 不读 prod 预览、不接外部 URL、不做跨应用验证。
- 不新增数据库表；截图沿用 `preview_page` 的会话目录机制。

## 12. 测试计划

**sidecar 纯函数单测（无浏览器依赖，沿用 `inspect_core` 的做法）**：ref 打标与找回、失效判定、sensitive 标记（password / 名称命中 / 不命中）、增量裁剪（去重集合与上限）、4 种 expect 的判定矩阵、TTL 与上限的边界（恰好满、恰好过期、并发下不淘汰在用）。

**后端单测**：三工具挂载条件；`session_id` 不出现在模型可见 schema；未 open 就 step；session 失效不扣步数；步数额度用尽；run 收口发 DELETE（含失败只记日志）；sensitive `fill` value 落库前被遮断；结果各字段过 `redact_sensitive`；sidecar 未知 id 降级；预览未运行不调 sidecar；expect 不过回 `ok:true, passed:false` 而非失败。open 的 `expect` 通道另四条（裁定 R-41）：载荷里真带着它（钉到发给 sidecar 的请求体，不替身 `_sidecar_open`）、不给时载荷里是 `null` 且返回值没有 `passed` 键、`passed:false` 时 `note` 已过遮断与 host 清洗而 `passed:true` 时是空串、带 expect 的 open 不扣步数额度且失败不自动附图；另有 `llm.py` 那份共享形状断 `is`（两侧不是抄出来的两份）。

**sidecar 集成（真 Playwright，标记为需要容器的慢测）**：open→click→expect 通过；重渲染后旧 ref 失效且返回新清单；被遮挡元素超时并附截图；原生 confirm 被 dismiss 且 `dialog` 字段有值；navigate 跳出 origin 被阻断；TTL 到点回收。
**实际交付形态与本计划不同（追认）**：这一组**没有**独立成真慢测套件。注册表与端点段用桩浏览器跑（`tests/test_browser_sessions.py`，没装 playwright 的机器临时补模块桩，裁定 R-18），上面六条里能被语义层覆盖的都在单测里，**真 Chromium 的「点得到点不到、框弹没弹、TTL 真回收」全部移交 §15 手工矩阵**（按 R-8 由控制器/主会话在真容器上跑）。所以 §15 那张表不是走流程，它替这一段测试兜底——空着就是这一段没有测试。

**前端 vitest**：步骤卡正常/断言失败/无截图/图裂降级；`ChatMessages` 对三个工具名的实时与历史路由；收口报告汇总数正确。

**打包测试**：compose 含 session 上限与 TTL 两项 env；容器约束不漂移（`read_only`、`cap_drop`、`pids_limit`、无 `ports`）。

**手工矩阵**：正常路径跑通 / 2 秒级开场动画应用（正是 US-06 的回归目标，拿 chudadi 验证不再需要手动关动画）/ 大型栈登录（admin123 走 fill，检查历史记录里是否被遮断）/ 带原生 confirm 的删除 / 断言故意写错看失败卡 / 步数耗尽 / sidecar 中途重启 / 拖拽类应用（预期得到空清单加那句盲区 `hint`，而不是「点不到」——见 §5.3 末与 §8）/ 刷新后步骤卡仍在 / 删除会话后截图清理。**逐行的走法与判定条件以 §15 那张表为准**：它把上面这些项铺开成可勾的行走法，在此之上另加「重复 open」「拓扑不泄漏（Task 4 登记）」「动作失败也带文字信号」「同一条消息里两个 step」「navigate path 非法」五条，并把开场动画那条按接线后的走法写回「`browser_open` 带 expect」（裁定 R-41）。

## 13. 任务拆分（垂直切片）

1. **T1 sidecar session 层**：`session_core.py` 纯函数 + 单测；`/sessions` 三接口；共享采集函数抽取；`/inspect` 改调共享函数并回归。
2. **T2 后端工具**：三工具 schema 与分派；session 绑定、额度、sensitive 遮断、收口 close；单测。
3. **T3 前端步骤卡**：`BrowserFlowCard.vue` + `ChatMessages.vue` 路由 + vitest。**注意：`ChatMessages.vue` 与 `ChatMessages.test.js` 当前工作区已脏，动工前先确认归属。**
4. **T4 打包与配置**：compose env 两项、`build_output.py`、打包测试断言、`dev.bat` 提示。
5. **T5 文档**：`CONTEXT.md` 术语、规格与 ADR 交叉链接、`browser/README.md` 补 session 生命周期。
6. **T6 验证**：后端全量 pytest（串行）、前端 vitest `--maxWorkers=4`、sidecar 集成慢测、手工矩阵，UI 由用户预览放行。

**实际执行按 8 张票切**（`.superpowers/sdd/2026-11-02-agent-browser-interaction/task-1..8-brief.md`，每片单独审查、逐片提交）：本表的 T1 拆成三片（`session_core.py` 纯函数 / 共享采集函数抽取与 `/inspect` 回归 / `sessions.py` 注册表与端点），T2 拆成两片（open+close / step+失败截图与增量账），T3 前端与 T4 打包对得上，T5 文档 + T6 验证收在最后一张票。**判定面一条没变**，只是切片更细——副作用是七轮修复里冒出了本规格写作时看不到的裁定（R-24~R-40），已按 §5.2/§5.3/§6/§7/§8/§14 追认。

## 14. 延后项登记（本分支未做，谁登记的、为什么没做）

七轮修复里点过但**没有**在本分支收口的口子，集中登记在这里，免得下一个人以为它们是疏漏。

- **R-37 / R-39**：见 §8 那三条的全文。两条都需要新裁定（R-37 要定「什么动作作废轮初清单」，R-39 要定「不可解析的 args 是否整串换成占位符」），且 R-39 的影响面是全工具共用路径，**须另立规格**。
- **open 的 `expect` 接线剩下的一条尾巴**（裁定 R-41 已把主口收掉：schema、透传、`passed`/`note` 结论与测试面见 §5.1 与 §6.3）：open 那一档的轮询预算仍是 sidecar 里写死的 `STEP_TIMEOUT_MS_DEFAULT`（3000ms），不读 `agent.browser_expect_timeout_ms`（§10）——要改得动 `browser/`，另立裁定。曾经并列的另一条尾巴（open 上声明的断言不进收口两本账、`hint` 因此会说「本次走查未声明任何断言」）已由**裁定 R-42 收口**：`browser_log` 的 open 那条在**声明过 expect 且拿到结论**时记真结论（未声明时仍为 `null`，不假装判过），`browser_open_declared_expect` 另记「声明过」这个事实本身，`hint` 因此在「只有 open 带 expect」的 run 上说「均通过」/「声明过但没拿到判定结论」而不是那句假话。report 的**形状**未动（键集与 `ok` 语义照旧），前端 `BrowserFlowCard.vue` 的 open 行按 `ok`/`action`/`target` 渲染、`passed` 走三档通则，不依赖它恒 `null`。
- **`preview_page` 那侧的 host 清洗未收口**：`browser_tools.py::_inspect` 的 `url` / `error` / `console` / `failed_requests` 仍可能带 `_connect_url` 重写后的容器内地址。裁定 R-22 / R-28 明确「同病但属既有行为、有自己的测试面，本轮不动」并登记给 Task 8；本轮是文档收口，**代码未动**，这条继续挂着。
- **工具名单还剩第三处字面量**：`run_browser_session_tool` 里的 `if name == "browser_open" / "browser_step" / "browser_close"`（Task 7 → T8 登记）。分派闸与位次口径已收成同一个 tuple 对象，这一处漏改的方向是**安全向**（名单有、handler 不认 → 回「未知工具」→ 不执行、不写清单 → 只是多遮），但别把它读成「三份名单已统一」。
- **两条 sidecar 形状的已知不一致**（Task 5 minor deferred）：① `_do_navigate` 的 path 非法出口不接 `_step_state`，那一步会落 log 但三段增量字段缺席；② 成功支的 `_append_log` 不受 `steps_charged` 门控（当前无害——sidecar 的成功响应把 `steps_charged` 写死 `true`）。镜像重建后在 §15 里 live 复核 ①。
- **一条测试缺口**（Task 6 minor deferred）：没有断言直接钉 `bo.BROWSER_MAX_SESSIONS_ENV == "ZEROCODE_BROWSER_MAX_SESSIONS"`，即「键名漂了但默认值没漂」这条第二漂移路径未封。
- **`no_new_console_error` 的判定面**：只看 console、不看 `page_errors`（§6.3）。要让它成为「崩页也抓得到」的证据，得扩判定面——那会改变一条既有断言的语义，同样需要裁定。
- **一步内连弹两个原生 dialog 只报最后一条**（最终修复波审查构造，判「可以留」）：`dialog` 是单值字段（`sessions.take_dialog` 读一次即清），同一步里先后弹出两个 `confirm` 时只有最后一个进结果。**丢的是「弹了几次」这个数量**，而 US-11 的核心信号（「有个原生框被自动取消了，你以为的删除可能没删」）照样出声——第一个框仍被按取消处理、页面状态仍是那次处理后的样子。要收就得把 `dialog` 改成列表并同步前端与 §5.2 的形状，代价是一次契约变更，本版本不做。
- **`browser/app.py` 已到 758 行**（五个端点 + 采集 + 巡检同居一处）：**下一次往它加端点或参数，就先拆出 `browser/sessions_api.py`（一个 `APIRouter`），不要继续往这个文件里追加、也别只把这句留在注释里**。本轮已经因为「出口散在同一文件的不同高度」漏掉过一条账（裁定 R-45 的第四条出口 F-11 就在 `step` 的最外层 `except` 里，离另外三条二十行开外），拆文件不能让这类问题消失，但能让「这个端点有几条不回状态的出口」变成一次能一眼数完的事。
- **`screenshot_width` / `screenshot_height` 是死载荷**：sidecar 的截图响应带这两个键（`VIEWPORT_WIDTH/HEIGHT` 两个常量），而后端 `browser_shot_service` 落盘后从 PNG 字节自己算宽高，模型与前端看到的都是算出来的那一份——这两个键从来没被读过。连带的一个信息损失：补图失败时后端只回一句 `screenshot_note`（「现场截图未取到（服务不可用或会话已结束）」），把 sidecar 那边「是 viewport 不对、是页面正在导航、还是进程没了」的原因折叠成了一句话。收口要同时动 sidecar 响应形状与 `browser_shot_service`，本轮判「可以留」。

## 15. 手工矩阵走查记录（2026-11-02）

判定条件已按最终实现写定。跑之前注意：需要重建 sidecar 镜像（`docker compose build browser`），且后端全量 pytest 会 down 掉本机 dev 栈。

**本轮走查的覆盖面必须先说清，否则这张表会被读成「全绿」。** 已实测的部分是**直打 sidecar HTTP 端点** + **以替身 ctx 直调后端工具层**（`run_browser_session_tool`），页面是一个本地静态探针页（2.5s 开场动画、口令框、原生 confirm、抛未捕获异常的按钮、1.6s 后延迟出现的元素），结果存 `.superpowers/sdd/2026-11-02-agent-browser-interaction/probe/probe-results{,-2}.json`。**没有**跑真实平台后端 + 真实生成应用 + 真实聊天会话，所以「需要人眼看 UI」与「需要真实应用数据面」的行**仍是未验证**，不是通过。

| 项 | 实测 |
|---|---|
| 开场动画应用 | **已验（替身页，非 chudadi）**。对照组同时成立：**不带 expect 打开 → 拿到的正是「正在配置组件… 请勿关闭电脑」**，旧病如实复现；**带 expect 打开 → 等 2.61s 越过 2.5s 动画、拿到「牌桌就绪」、`passed:true` 且无 note**。后端工具链同形状 `steps_left` 仍为 12（不扣步数）。chudadi 本尊未跑 |
| 大型栈登录 | **部分已验**。遮断机制在真 HTTP 往返下成立：sensitive 框 fill → `value_redacted:true` 且 `browser_log` 里查不到明文；非 sensitive 框 fill → 值 `第一单` 照旧保留（**没有一刀切**）。**登录态跨步保持、真实 `admin123`、`message.tool_args` 落库面**三项未跑（需真实大型栈应用；前两项由 `tests/test_agent_loop.py` 的真 loop 测试覆盖，非真机） |
| 带原生 confirm 的删除 | **已验（sidecar 层）**。`dialog:{type:confirm, message:"确认删除这一行？", handled:"dismissed"}`，且页面文本里**没有**「已删除」。前端那句提示的渲染由 `BrowserFlowCard.test.js` 覆盖，未人眼看 |
| 断言故意写错 | **未跑真机**。由 `tests/test_browser_session_tools.py` 覆盖 |
| ref 失效 | **已修（原为「未修，需裁定」）**：序号形状下拿旧 ref 再点回的是 `ok:true`——旧序号在新清单里**大概率仍然合法**（实测 `stale_ref_still_exists_in_new_list:true`），本次侥幸仍指向同一元素，顺序一变就静默点到另一个元素。现在 ref 是每次扫描重生成的随机短令牌（§6.2），旧令牌在新扫描里几乎必然查不到，落回 `ok:false` +「ref 无效或页面已变化，请用最新清单里的 ref」，照旧带回新清单并自动附图（§5.3 那条早就写好、生产里几乎不可达的路径）。**本轮的验证面**：`tests/test_browser_session_core.py`（令牌唯一性/形状/字符集、`selector_for` 只认新形状）+ `tests/test_browser_sessions.py`（sidecar 层替身 page 钉「旧令牌 → 查不到 → 带回新清单」）。**真机那一跑仍待补**：见本节末「Deferred live steps」，验收点就是「拿旧令牌去点必须回 `ok:false` + 那句中文 + 带回新清单」。 |
| 步数耗尽 | **未跑真机**。由单测覆盖。`steps_used` / `steps_total` 两本账的口径差异已在真机侧观察到并确认（`steps_total:3` vs `steps_used:2`，差的就是 open 那一行） |
| sidecar 中途重启 | **等价路径已验**（用 `DELETE` 制造会话消失，未真重启容器）：`{ok:false, session_gone:true, steps_charged:false}` + 那句中文。真重启未跑 |
| 拖拽类应用 | **未跑真机**（无拖拽类样本应用）。**这一行的失效方式原先写错了**：原表与本节上一版都写「预期得到诚实的『元素不可点击』」，而 `probe/probe-results-7.json` 实测的是 `<div draggable="true">` 匹配不上打标脚本的任何选择器，**交互清单直接为空**——agent 看到的是「这页没有可操作元素」。改准后现在走的是 §5.3 末那条空清单 `hint`（拖拽 / 上传 / hover），代码与单测在场（`tests/test_browser_session_tools.py` 那四条），真机那一跑仍待补 |
| 重复 open | **未跑真机**。但容量闸在真机侧验过：已有 1 个活会话时第 2 个 open 成功、**第 3 个回 409** 且 detail 是「浏览器服务已满（活会话达上限），请等前一个走查结束」 |
| 刷新后步骤卡仍在 | **未验——需要人眼**。本轮没有起前端 dev server、没有真实聊天会话。这是合回前**必须补**的一格 |
| 删除会话后截图清理 | **未跑真机**。沿用 `preview_page` 既有机制，未新增代码路径 |
| 拓扑不泄漏 | **部分已验**：工具返回值里 `url == http://localhost:8642/index.html`（展示地址，非容器 IP）、返回值 JSON 里不含 `session_id`。**`failed_requests_new` 里的子资源 URL 是否带容器地址未跑**（探针页无失败子资源）；`preview_page` 那侧的同类暴露面按裁定未修 |
| 动作失败也带文字信号（R-29） | **部分已验**：`page_errors_new` 在真机里确实拿到了「牌局状态机崩了」（`console_new` 为空，符合预期——`setTimeout` 里抛的是 pageerror 不是 console.error）。但这一条是在 **`ok:true` 的步**上观察到的；**`ok:false` 那一步带增量**这一支（sidecar 层 R-29 的改动）未单独跑 |
| 同一条消息里两个 step | **未跑真机**。由 `tests/test_agent_loop.py` 的**真 loop 双调用**测试覆盖（含「args 为空/畸形的 open 也占位次」那一支）。注意真机探针是逐个调用发的，不构成这一行的证据 |
| `_do_navigate` 的 path 非法 | **已验（sidecar 层）**：绝对 URL 与 `//example.com/x` 都回 `ok:false` +「path 必须是同源相对路径（以 / 开头）」，无 500、无空错误串。**另发现一层记账不一致**：直打 sidecar 时该步 `steps_charged:true`，而后端 `_step` 会先本地校验并回 `false`——生产路径只走后端，故不影响交付，但两层的口径不同值得记一笔 |

**本轮真机另外复现的一条**（原表没有，属 §14 的 R-42）：只在 `browser_open` 上声明 `expect` 且 `passed:true` 的 run，`browser_close` 的 `hint` 曾输出「本次走查未声明任何断言」——**实测复现的假话**，已在 `32c012d` 修掉（open 的断言结论进那本账，`steps_failed` 口径随之含 open）。

**尚未验证、合回前必须补的三格**：刷新后步骤卡仍在（人眼看 UI）、拖拽类应用、断言故意写错的自动附图。**原列的第四项「ref 序号复用导致的静默错点」已按 §6.2 修掉**（随机令牌），代码与单测/替身层测试在场，**只剩真机那一跑**（下表末 Deferred live steps）。

### Deferred live steps（本轮按 R-8 不重建镜像、不起容器、不打真 sidecar HTTP）

1. **验收点**：探针页 open → 记下清单里的某个令牌 → 让页面重渲染（改变可交互集合的**顺序**，例如插入/删除一个按钮）→ 拿**旧令牌**去 `click`。必须回 `ok:false` +「ref 无效或页面已变化，请用最新清单里的 ref」，且带回**新清单**（令牌与旧的那一份整批不同）并自动附一张现场图；`steps_charged:true`。旧实现在这格回 `ok:true` 并静默点到另一个元素。
2. 同一跑里对照确认：新清单里查不到旧令牌的**属性也确实被清掉了**（`document.querySelectorAll('[data-zc-ref]')` 的取值集合与上一轮无交集）。
3. 大 DOM 页（>200 个可交互节点）跑一次 open，确认 `interactive_truncated:true` 照旧出声——令牌池与遍历上限同值这条关系在真机上不漂。
4. 登录页跑一次「fill 口令 → 下一步 fill 备注」，确认只有口令那一步 `value_redacted:true`，备注照旧明文（令牌换发没有把「已知非 sensitive 放行」变成一刀切）。
