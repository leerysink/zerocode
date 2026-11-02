# Agent 预览页交互验证（browser_open / browser_step / browser_close）规格说明

**日期**：2026-11-02  **来源**：brainstorming 访谈  **ADR**：[0037 从一次性只读检查改为可交互会话](../adr/0037-agent-browser-interactive-session.md)（修订 [0031](../adr/0031-agent-browser-inspection-sidecar.md)）  **前序规格**：[2026-10-31 preview_page](2026-10-31-agent-browser-verification.md)

## 1. 问题陈述

`preview_page` 上线后有两类问题它结构上回答不了。

**等待策略是猜的。** 它的时序写死为「`domcontentloaded` → 等 `agent.browser_settle_ms`（默认 1200ms）→ 短 networkidle → 截图」。任何自带启动时序的应用都会把它打穿：一个 2.1 秒开机动画、且深链窗口在启动流程末尾才挂载的应用，Agent 连续 6 次检查全部停在「正在配置组件」，`ok:true`、console 干净、截图完整——唯独不是用户真正的首屏。Agent 据此得出「渲染没问题，是时序问题」的结论尚可，但它同样可能得出「页面坏了」。固定 sleep 与任意应用的启动耗时之间没有因果关系，这不是参数调得好的问题。

**只读检查看不到业务闭环。** 「帮我验证下单流程走不走得通」这个问题，`preview_page` 只能回答「下单页渲染出来了」。表单能不能填、点提交有没有真的落库、点删除那一行有没有消失、按钮点了有没有反应——全部需要操作。用户在聊天里说的「帮我看看这个功能对不对」，多数时候指的是行为对不对，不是长相对不对。

本规格把浏览器验证从「看一眼」升级为「走一遍」，同时保留 `preview_page` 作为便宜的快检。

## 2. 方案概述

- **三个交互工具**：`browser_open(path)` / `browser_step(action, target, value?, expect?)` / `browser_close()`。与 `preview_page` 并存，后者继续作为「改完看一眼」的便宜选项。
- **sidecar 变成有状态服务**：新增进程内 session 注册表。活页面（Playwright context + page）留在 sidecar 内存，后端只持 `session_id ↔ chat_session` 绑定、步数额度与收口责任。**`session_id` 不进模型参数面**——沿用既有铁律「应用身份由服务端解析，schema 里没有 app_id」。
- **等待改为按条件等**：给了 `expect` 就轮询等到满足或超时（默认 3s、上限 8s）；没给只等一个短 networkidle 窗口。固定 sleep 不再是唯一的等待手段。
- **元素句柄用 ref，不用 CSS selector**：每次返回一份精简的**交互元素清单**（role + accessible name + ref + sensitive 标记），上限 40 条。模型下一步用 ref 指元素。理由是应用是 Vue SPA，scoped 样式类的哈希值不可预测，让模型写选择器等于让它瞎猜。
- **断言沿用 `db_query` 的 expect 口径**：给了就工具判 `passed`，不给则模型自己读结果。
- **截图默认不给，断言失败自动给**：一张 1280×800 PNG 的 token 不便宜，而失败的那一刻正是唯一值得看现场的时刻。
- **动作全放开、不逐次确认**：dev 是沙箱，改坏了可重置。边界靠「只能开在当前应用 dev origin」+ 步数额度 + 收口报告守，不靠弹框。

## 3. 用户故事

### 跑通一条路径

- **US-01** 作为 Agent，我调 `browser_open({"path": "/orders"})`，得到 `session_id`（对我不可见）、页面标题、可见文本、交互元素清单、初始 console 与失败请求，以及 `steps_left`。
- **US-02** 作为 Agent，我用 `browser_step({"action": "click", "target": "e7"})` 点清单里那个 ref 为 e7 的按钮，返回同一形状的新状态。
- **US-03** 作为 Agent，我在 step 里带 `expect: {"type": "text_contains", "value": "订单已创建"}`，工具在超时窗口内轮询判定，返回 `passed: true/false`；不带 expect 时不判定，我自己读。
- **US-04** 作为 Agent，每步返回的 `console` 与 `failed_requests` 只含**上一步以来新增**的条目，所以我能判断「这个错是不是我这一步搞出来的」，也不必重读同一批旧错误。
- **US-05** 作为 Agent，断言失败时结果自动附带一张截图；断言通过时没有截图，省 token。
- **US-06** 作为 Agent，目标应用有 2.1 秒开场动画时，我带 `expect` 打开首页，工具等到动画结束、目标文案出现后才返回——我不再需要让用户关掉动画才能看到真实首屏。
- **US-07** 作为 Agent，我调 `browser_close()` 得到一份收口报告：走了几步、几步断言失败、失败在哪步。
- **US-08** 作为 Agent，验证带登录的大型应用时，我从清单里找到账号与密码输入框，`fill` 两次再 `click` 登录，后续步骤里 session 保留着登录态（token 存在这个干净 context 的 localStorage 里）。

### 失败与边界

- **US-09** 作为 Agent，我用了上一次返回里的 ref 而页面已经重渲染，得到 `ok:false` +「页面已变化」，且结果**当场附带最新交互清单**，我下一步就能改指，不多花一次往返。
- **US-10** 作为 Agent，目标元素存在但被遮罩挡住或在视口外，工具自动等到可点或超时，超时返回「元素不可点击，可能被遮挡」并附截图。
- **US-11** 作为 Agent，我的点击触发了浏览器原生 `confirm`，它按**取消**处理，且结果明确写「页面弹出了确认框，已按取消处理」——我不会把它误读成操作成功。
- **US-12** 作为 Agent，我的 session 因空闲被回收，得到「浏览器会话已结束，请重新 browser_open」，且**不扣我的步数额度**。
- **US-13** 作为 Agent，步数额度用尽时得到明确提示「本 run 步数已用完，请 browser_close 收口」，而不是继续让我试。
- **US-14** 作为 Agent，sidecar 在流程中途重启，我拿到未知 session_id 的降级结果「浏览器服务已重启，请重新 browser_open」，不是一句「工具执行异常」。
- **US-15** 作为 Agent，预览未运行时 `browser_open` 返回未运行状态 +「先 preview_restart」，与 `preview_page` 同口径，**绝不自己起栈**。
- **US-16** 作为平台，sidecar 活 session 已达上限时 `POST /sessions` 直接拒绝并回「浏览器服务已满」，不排队——排队等于让一次工具调用挂几十秒。

### 安全与隔离

- **US-17** 作为平台，`browser_step` 的 `navigate` 只接受同源相对 path（复用 `normalize_path`），主文档导航仍只允许目标 origin，跳出即阻断并记为失败请求。
- **US-18** 作为平台，session 是 sidecar 新建的干净 context，**不读取、不复用用户宿主浏览器的 cookie 与登录态**。
- **US-19** 作为用户，我在应用里输入的口令不会明文留在聊天记录里：清单把 `type=password` 及名称含 token/secret/code 的元素标为 `sensitive`，后端对指向 sensitive 元素的 `fill` value 在落库前遮断。
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
| 元素定位 | 交互元素清单 + `data-zc-ref` 打标，ref 只在最近一次返回内有效，每次 step 后全部重打 |
| 断言 | 4 种：`text_contains` `element_visible` `url_matches` `no_new_console_error`。可选，沿用 `db_query` 的 expect 口径 |
| 等待 | 给 expect 就轮询等到满足或超时（默认 3s、上限 8s）；没给只等短 networkidle |
| 截图 | step 默认不带；断言失败自动带一张 |
| 状态增量 | console / 失败请求每步只回新增，不回全量 |
| 原生弹窗 | 一律按取消处理并在结果里显式告知，第一版不提供 accept 动作 |
| session 归属 | 一个 run 一个 session；`session_id` 不进模型参数面，存在 `ctx.run_state` |
| 回收 | 显式 close + 空闲 TTL 120s + 活 session 上限（默认 2），三者并存 |
| 持久化 | 不做。sidecar 重启即全丢，后端按未知 id 降级 |
| 采集代码 | 不新写一套。把 `_inspect_in_context` 的采集与裁剪抽成可复用函数，`preview_page` 与 `browser_*` 共用 |

## 5. 工具契约

### 5.1 schema（`app/agent/llm.py` 新增 `BROWSER_SESSION_TOOLS`）

```json
{"name": "browser_open",
 "description": "在当前应用 dev 预览开一个可交互的浏览器会话，返回标题、可见文本与可交互元素清单",
 "parameters": {"type": "object", "properties": {
   "path": {"type": "string", "description": "同源相对路径，默认 /"}}}}

{"name": "browser_step",
 "description": "对当前浏览器会话执行一个动作并返回新状态；ref 只在上一次返回的清单内有效",
 "parameters": {"type": "object", "properties": {
   "action": {"type": "string", "enum": ["click","fill","press","select","scroll_into_view","navigate"]},
   "target": {"type": "string", "description": "上一次清单里的 ref；navigate 时改填 path"},
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

### 5.2 step 成功结果

```json
{"ok": true,
 "action": "click", "target": {"ref": "e7", "role": "button", "name": "提交订单"},
 "passed": true, "expect": {"type": "text_contains", "value": "订单已创建"},
 "url": "http://localhost:8210/orders", "title": "订单列表",
 "text": "……（截断后可见文本）",
 "interactive": [{"ref": "e1", "role": "button", "name": "新建", "disabled": false, "sensitive": false},
                 {"ref": "e2", "role": "textbox", "name": "备注", "sensitive": false}],
 "console_new": [], "failed_requests_new": [],
 "dialog": null,
 "screenshot": null,
 "steps_left": 9, "elapsed_ms": 1420}
```

- `interactive` 上限 40 条，超出时 `truncated: true`。`name` 取 accessible name，优先级 `aria-label > label > textContent（截断 60 字）> placeholder > title`。
- `passed` 只在给了 `expect` 时出现；不给则字段缺席，模型自判。
- `dialog` 非 null 时表示本步出现过原生弹窗：`{"type": "confirm", "message": "...", "handled": "dismissed"}`。
- `steps_charged` 在成功与失败结果里都出现：本步是否扣了步数额度。只有「session 已失效」与「额度已用尽」为 `false`（Agent 没做成任何事，不该被记账），其余为 `true`。
- 截图仍走 `browser_shot_service`，URL 与鉴权口径不变。

### 5.3 失败结果（全部 `ok:false`，不抛 `ToolError`）

| 场景 | 返回 |
|---|---|
| 会话不存在/已回收 | `{"ok": false, "error": "浏览器会话已结束，请重新 browser_open", "session_gone": true, "steps_charged": false}` |
| ref 已失效 | `{"ok": false, "error": "页面已变化，请用最新清单里的 ref", "interactive": [...], "steps_charged": true}` |
| 元素不可点 | `{"ok": false, "error": "元素不可点击，可能被遮挡", "screenshot": {...}}` |
| expect 未满足 | `{"ok": true, "passed": false, "expect": {...}, "screenshot": {...}, "console_new": [...]}`（走通了但断言不过，不是工具失败） |
| 步数额度用尽 | `{"ok": false, "error": "本 run 步数已用完，请 browser_close 收口", "steps_left": 0}` |
| 会话未开 | `{"ok": false, "error": "还没有浏览器会话，先 browser_open"}` |
| 预览未运行 | `{"ok": false, "status": "stopped", "hint": "预览未运行：先用 preview_restart 启动"}` |
| navigate 路径非法 | `{"ok": false, "error": "path 必须是同源相对路径（以 / 开头）"}` |
| sidecar 不可用/已满 | `{"ok": false, "error": "浏览器检查服务不可用" \| "浏览器服务已满", "hint": "..."}` |
| Chromium 崩溃 | `{"ok": false, "error": "浏览器会话异常终止，请重新 browser_open", "session_gone": true}` |

注意 expect 不过返回的是 `ok:true, passed:false`：动作执行成功与业务断言通过是两件事，混在一个字段里会让模型分不清「我点不动」和「点了但结果不对」。

## 6. sidecar 契约

### 6.1 接口

- `POST /sessions` → `{"ok": true, "session_id": "...", "title", "text", "interactive", "console", "failed_requests", "elapsed_ms"}`
- `POST /sessions/{id}/step` → 见 §5.2 形状（sidecar 返回原始版，后端做遮断与落盘）
- `DELETE /sessions/{id}` → `{"ok": true}`（幂等，未知 id 也回 ok）
- `GET /healthz` 不变；`POST /inspect`（`preview_page` 用）不变。

### 6.2 session 注册表

进程内 `dict[session_id] = {context, page, origin, console_seen, failed_seen, refs, last_used, steps}`。三条不变式：

1. **上限即拒绝**：`len(SESSIONS) >= ZEROCODE_BROWSER_MAX_SESSIONS` 时 `POST /sessions` 直接 409，不排队、不淘汰别人正在用的 session（淘汰会让另一个 run 莫名断线）。
2. **空闲回收**：后台巡检每 10s 扫一次，`now - last_used > ZEROCODE_BROWSER_SESSION_TTL_SECONDS` 的 session 关闭 context 并移除。
3. **origin 固化**：session 创建时记下目标 origin，`navigate` 只允许同 origin 的相对 path；主文档导航仍走 `allow_request`，跳出即阻断。

ref 实现：采集时给每个交互元素写 `data-zc-ref="eN"`，step 时按属性选择器找回。**每次 step 后重新打标，旧 ref 全部作废**——Vue 重建 DOM 节点会连属性一起丢掉，所以「ref 只在最近一次返回内有效」不是约定，是物理事实。

### 6.3 等待策略

- 动作执行后：给了 `expect` → 在 `agent.browser_expect_timeout_ms`（默认 3000、上限 8000）内轮询该断言，命中即返回；未命中则等满超时并回 `passed:false`。
- 没给 `expect` → 只尝试短 `networkidle`（沿用现有 3s 上界），拿到当下状态。
- 固定 `settle_ms` 降级为 step 的**可选参数，默认 0**，只用于「动画结束后还想再看一眼」的场景，不再是默认路径。它**不复用** `agent.browser_settle_ms`（那个键属于 `preview_page` 的一次性检查，默认 1200 保持不变）——两条路径的等待语义从此分家，别再拿一个键去管两件事。

### 6.4 运行约束

容器约束与 0031 完全一致，不新增豁免：`user 10001`、`read_only: true`、`tmpfs /tmp`、`cap_drop: [ALL]`、`no-new-privileges`、`mem_limit: 1g`、`cpus: 1.0`、`pids_limit: 256`、不发布宿主端口。**新增一条断言到打包测试**：活 session 数上限与 TTL 必须在 compose env 里显式出现，不接受只靠代码默认值（默认值漂了没人发现）。

## 7. 后端接线与生命周期

- `session_id` 存在 `ctx.run_state["browser_session"]`，一个 run 一个；`steps_used` 同处计数。
- 后端持有最近一次 `interactive` 清单（`run_state["browser_refs"]`），用于查 sensitive 标记——`fill` 指向 sensitive 元素时，value 在写入消息历史前过遮断。
- 收口：`AgentRunner` 的 run 结束路径上 best-effort 发 `DELETE`，失败只记日志（TTL 兜底）。这条与 `preview_page` 的「无副作用、无需收口」不同，是交互工具唯一引入的运维义务。
- 分派：`execute_tool` 新增 `BROWSER_SESSION_TOOL_NAMES` 分支，沿用惰性 import 与「失败一律是正常结果」的既有约定。
- 采集复用：`browser/app.py` 里 `_inspect_in_context` 的采集与裁剪部分抽成 `collect_page_state(page)` 一类的共享函数，`/inspect` 与 session 两条路径都调它，避免两处口径漂移。

## 8. 安全边界与残余风险

**做得到**

- 模型没有 URL 参数，也没有 `session_id` 参数；目标由后端按应用 dev 端口 + 校验过的相对 path 构造。
- session 是 sidecar 新建的干净 context，不读取宿主浏览器状态。
- 主文档限目标 origin，子资源限同源 + 公网，私网与平台容器别名继续 `route.abort()`。
- 页面文本、console、请求 URL、断言 value 全部过 `redact_sensitive`；sensitive 字段的 `fill` value 在落库前遮断。
- 步数与 open 次数双额度，加空闲 TTL 与并发上限，防把 sidecar 拖垮。

**做不到 / 残余风险**

- **注入面扩大是实质性的**：0031 时代页面文字只能影响模型的判断，现在能影响它的动作。接受理由限定在「目标只能是用户自己的 dev 应用」，最坏是自伤。
- 截图与 `fill` value 的遮断都不是完备的：非 password 命名的敏感输入（身份证号、密钥明文框）不在 sensitive 判定内，只能靠规则表兜。
- 动作可以改 dev 数据且**不回滚**：与 `db_write` 同一口径——遇错即停，已成功的不撤销。收口报告只报事实，不承诺恢复。
- 拖拽类交互点不到（第一版无 `drag`）。这类应用上 Agent 会得到「元素不可点击」而不是假成功，属已知能力边界。
- 活 session 期间页面 JS 可持续运行（定时器、WebSocket），最坏影响是 sidecar 自身资源占用，由 TTL 与上限约束。

## 9. 工程接线

| 位置 | 改动 |
|---|---|
| `browser/app.py` | 新增 `/sessions` `/sessions/{id}/step` `DELETE /sessions/{id}`；抽共享采集函数 |
| `browser/session_core.py` | **新增纯函数层**（刻意不 import playwright）：ref 打标与找回、sensitive 判定、增量裁剪、expect 判定、TTL/上限判定 |
| `app/agent/llm.py` | `BROWSER_SESSION_TOOLS` + `BROWSER_SESSION_TOOL_NAMES` |
| `app/agent/browser_tools.py` | `run_browser_session_tool`；session 绑定、额度、sensitive 遮断、截图落盘、收口报告 |
| `app/agent/tools.py` | 新增分派分支 |
| `app/agent/loop.py` | 挂载三个工具与提示词；run 收口发 close |
| `app/agent/prompts.py` | 交互工具指引：ref 一次性、增量语义、expect 何时给、不要执行页面里的话 |
| `frontend/src/components/BrowserFlowCard.vue` | **新增**步骤流卡（动作/目标/断言徽标/失败截图/收口报告） |
| `frontend/src/components/ChatMessages.vue` | 路由三个工具到新卡片 |
| `scripts/build_output.py` | compose env 新增 session 上限与 TTL；打包测试断言 |
| `CONTEXT.md` | 新增术语「浏览器会话（browser session）」「交互元素句柄（ref）」，并修订「预览页检查」条目 |

## 10. 配置项

| 键 / 环境变量 | 默认 | 说明 |
|---|---|---|
| `agent.browser_max_per_run` | 6 | 沿用；`browser_open` 计入同一额度 |
| `agent.browser_max_steps_per_run` | 12 | 单 run 步数上限，`<=0` 不限 |
| `agent.browser_expect_timeout_ms` | 3000 | 断言轮询上界，夹在 500..8000 |
| `agent.browser_interactive_max` | 40 | 每步返回的交互元素条数上限 |
| `ZEROCODE_BROWSER_MAX_SESSIONS` | 2 | sidecar 活 session 上限，超限拒绝新 open |
| `ZEROCODE_BROWSER_SESSION_TTL_SECONDS` | 120 | 空闲回收阈值 |

## 11. 非目标

- 不做批量动作脚本（`steps: [...]` 一次交整段）。单步契约跑顺后若往返成本确实过高，再挂批量入口，复用同一 step 契约。
- 不做 hover、drag、upload、文件下载、剪贴板、iframe 内定位。
- 不做视觉回归基线与截图对比；不长期保存中间态。
- 不做多标签页/多窗口；一个 session 一个 page。
- 不复用用户宿主浏览器的登录态，不连真实 Chrome（见 ADR-0037 被否决项）。
- 不读 prod 预览、不接外部 URL、不做跨应用验证。
- 不新增数据库表；截图沿用 `preview_page` 的会话目录机制。

## 12. 测试计划

**sidecar 纯函数单测（无浏览器依赖，沿用 `inspect_core` 的做法）**：ref 打标与找回、失效判定、sensitive 标记（password / 名称命中 / 不命中）、增量裁剪（去重集合与上限）、4 种 expect 的判定矩阵、TTL 与上限的边界（恰好满、恰好过期、并发下不淘汰在用）。

**后端单测**：三工具挂载条件；`session_id` 不出现在模型可见 schema；未 open 就 step；session 失效不扣步数；步数额度用尽；run 收口发 DELETE（含失败只记日志）；sensitive `fill` value 落库前被遮断；结果各字段过 `redact_sensitive`；sidecar 未知 id 降级；预览未运行不调 sidecar；expect 不过回 `ok:true, passed:false` 而非失败。

**sidecar 集成（真 Playwright，标记为需要容器的慢测）**：open→click→expect 通过；重渲染后旧 ref 失效且返回新清单；被遮挡元素超时并附截图；原生 confirm 被 dismiss 且 `dialog` 字段有值；navigate 跳出 origin 被阻断；TTL 到点回收。

**前端 vitest**：步骤卡正常/断言失败/无截图/图裂降级；`ChatMessages` 对三个工具名的实时与历史路由；收口报告汇总数正确。

**打包测试**：compose 含 session 上限与 TTL 两项 env；容器约束不漂移（`read_only`、`cap_drop`、`pids_limit`、无 `ports`）。

**手工矩阵**：正常路径跑通 / 2 秒级开场动画应用（正是 US-06 的回归目标，拿 chudadi 验证不再需要手动关动画）/ 大型栈登录（admin123 走 fill，检查历史记录里是否被遮断）/ 带原生 confirm 的删除 / 断言故意写错看失败卡 / 步数耗尽 / sidecar 中途重启 / 拖拽类应用（预期得到诚实的「点不到」）/ 刷新后步骤卡仍在 / 删除会话后截图清理。

## 13. 任务拆分（垂直切片）

1. **T1 sidecar session 层**：`session_core.py` 纯函数 + 单测；`/sessions` 三接口；共享采集函数抽取；`/inspect` 改调共享函数并回归。
2. **T2 后端工具**：三工具 schema 与分派；session 绑定、额度、sensitive 遮断、收口 close；单测。
3. **T3 前端步骤卡**：`BrowserFlowCard.vue` + `ChatMessages.vue` 路由 + vitest。**注意：`ChatMessages.vue` 与 `ChatMessages.test.js` 当前工作区已脏，动工前先确认归属。**
4. **T4 打包与配置**：compose env 两项、`build_output.py`、打包测试断言、`dev.bat` 提示。
5. **T5 文档**：`CONTEXT.md` 术语、规格与 ADR 交叉链接、`browser/README.md` 补 session 生命周期。
6. **T6 验证**：后端全量 pytest（串行）、前端 vitest `--maxWorkers=4`、sidecar 集成慢测、手工矩阵，UI 由用户预览放行。
