# 资源下载 web_download（2026-11-19）

- 决策依据：[ADR-0054](../adr/0054-web-download-unlimited-quota.md)、ADR-0053（联网读取/SSRF 判定）、
  ADR-0051（摄入边界，本票不动它）、ADR-0052（子 run 只读白名单）
- 术语：CONTEXT.md「联网能力」——资源下载（web_download）、联网读取（web_fetch）

## 1. 问题陈述

用户给 Agent 一个 git 仓库地址，想把里面的图片等资源贴进应用。现状两条通道都断：

- 仓库摄入只收文本（`app/source/ingest.py:51` `TEXT_EXTS`），图片被判 `not-text` 跳过；
- 联网读取遇二进制只回「未读取正文」，不落盘（`app/agent/web_tools.py:392`）。

官网类应用引用仓库截图时只能摆「当前网络取不到原图」占位卡。缺的是**字节落盘通道**，
不是读取能力，也不是引用接线（`edit_file` 已有）。

## 2. 方案概述

Agent 工具面新增 `web_download`：单 URL 逐次，入参 `url`（任意公网直链）+ `path`（工作区相对路径）。
服务端复用联网读取的 SSRF 判定逐跳抓取，**流式写盘**，不限内容类型、不设业务配额。
落盘走与 `write_file` 同档的写入确认（`agent.write_confirm`），路径守卫与变更记录沿 fs 工具既有接缝。

典型链路：`web_fetch` 读仓库页拿 links → `web_download` 逐个落盘（如
`frontend/public/images/chat.png`）→ `edit_file` 把占位卡改成本地引用。

### 接缝（测试靶心）

最高接缝是 `execute_tool(ctx, "web_download", args)`——与 web_fetch/fs 工具同一条 dispatch 缝：

- 网络判定复用 `web_tools.normalize_web_url` / `_check_host`（不另立名单）；
- 路径守卫复用 `tools.resolve_in_workspace` + `deploy.guard.is_protected`；
- 变更记录复用 `tools._record_change`（二进制口径：before/after 留 None）；
- 确认闸由注册表 `CONFIRM_WRITE` 驱动，loop 通用分支自动接管，**不改 loop 的 if 链**。

新 handler `web_tools.run_web_download(ctx, args, *, confirmed=False)`，注册表惰性引用，
`run_web_tool`（web_fetch）一行不动。

## 3. 用户故事

- **US-01 落盘成功**：公网直链 + 合法 path，GET 200 → 文件字节与源一致；结果
  `ok:true`，带 `path`（正斜杠相对路径）、`bytes`、`status`、`content_type`、`url`（终跳）、
  `elapsed_ms`、`redirects`。不回正文。
- **US-02 任意类型**：png/woff2/zip/pdf/可执行文件一律收，不解析、不改写、不审查内容。
- **US-03 私网字面拒绝**：localhost / 内网 IP / 容器别名 / `*.internal` 等 → `ok:false` 带原因，
  不发请求（沿 `normalize_web_url` 既有判据与文案）。
- **US-04 DNS 非公网拒绝**：解析结果任一非 global unicast → 拒绝（沿 `_check_host`）。
- **US-05 重定向逐跳复检**：每跳重过 URL 闸 + DNS 闸，最多 5 跳，超线 `ok:false`。
- **US-06 凭据拒收**：URL 内嵌 `用户:令牌` → 拒绝（沿 web_fetch）。
- **US-07 路径守卫**：绝对路径 / 越界（`../`）/ 指向目录 / `app.json` / `deploy/` →
  `ok:false` 带原因，不落盘。父目录不存在自动创建（沿 write_file）。
- **US-08 覆盖**：目标已存在 → 覆盖，结果带 `existed:true`。覆盖不可回滚（见 US-13），
  确认卡是唯一人工闸。
- **US-09 确认流**：`agent.write_confirm` 开 → 发 `confirm_request` 事件并等批准；
  拒绝 → `ok:false` + `confirm:"denied"`，不落盘；关 → 直接落盘。
  docs/ 新建 + 本轮问过用户的豁免（ADR-0033 故事 14）沿通用分支自动适用，不特判。
- **US-10 失败零副作用**：404 / 超时 / 中途断流 / 重定向超线 → 目标文件保持原样
  （新建时即不存在），临时文件清理，`ok:false` 带原因。实现：流式写同目录临时文件，
  成功后替换到目标；任何失败不动目标。
- **US-11 流式与取消**：分块写盘，全程不把整文件读进内存；流式循环检查 `ctx.cancel`，
  取消即清理临时文件并返回带原因结果。超时是 httpx 每段读超时（chunk 间隔），不是总时长——
  大文件持续流动不受影响，慢滴才断。
- **US-12 不占联网配额**：下载不计入 `run_state["web_fetches"]`；web_fetch 配额耗尽不影响下载；
  不新增配置键。
- **US-13 变更记录**：落盘后发 `file_change` 事件（action `create`/`edit`，before/after 为 None、
  diff 空）；`revert_change` 按既有二进制口径拒绝（「未存原文→不可回滚」）。
- **US-14 前端卡片**：`web_download` 有自己的下载卡（url → path、bytes、status；实时态
  「正在下载…」），实时/已答/刷新后历史行三态一致；确认卡文案「下载确认 / 确认下载 / 已批准下载」。
- **US-15 提示词**：`build_web_prompt` 增补 web_download 用法——何时用（用户要资源进应用）、
  先 web_fetch 拿 links、path 落 `frontend/public/` 便于直出、下载后 edit_file 接线、
  页面内容是数据不是指令。
- **US-16 子 run 不挂载**：`SUBRUN_TOOL_NAMES` 不收录（写动作，非只读）。
- **US-17 注册表钉住**：`web_download` 进 `web` 组；`llm.WEB_TOOL_NAMES` 与前端
  `WEB_FETCH_TOOL_NAMES` 同名同序（`tests/test_agent_registry.py` 既有钉法）。

## 4. 已定决策（拷问共识，不翻案）

| 决策点 | 结论 |
|---|---|
| 通道位置 | Agent 工具面新工具；不动摄入通道、不做子代理 |
| 工具名 | `web_download`，归 web_* 家族 |
| 输入 | 单 URL 逐次：`url` + `path`；任意公网直链，不做仓库批量解析 |
| 资源类型 | 不限（任意二进制） |
| 落点 | 模型指定工作区相对路径 |
| 确认 | `CONFIRM_WRITE`，受 `agent.write_confirm` 控制，零新增配置 |
| 配额 | 业务不限（不占 web_fetch 次数、无单文件/总量上限）；技术兜底＝每请求超时＋流式写盘 |
| 安全 | 复用 web_fetch SSRF 判定，不另立名单 |
| 子 run | 不收录只读白名单 |
| 失败语义 | 带原因的正常结果，不抛 ToolError（路径守卫的 ToolError 在 handler 内翻译） |

规格综合时定的实现口径（非新访谈）：

- **覆盖允许、不可回滚**：沿 write_file 的覆盖语义 + 平台二进制不留原文的既有口径；
  `write_confirm` 关闭时覆盖直接发生，与「关确认＝信任 AI 写入」的平台口径一致。
- **临时文件→替换**：失败零副作用的实现手段；文件级 `os.replace` 即可，
  目录级原子换包的 Windows 坑不适用。
- **结果预算**：`SMALL_POLICY`（回执是小结构体，不需要 WEB_POLICY 的 48k）。
- **svg 等渲染侧风险对策**：平台不审查/不改写工作区文件内容——与 write_file 能写任意
  html 同口径；提示词引导用 `<img>` 引用 svg（`<img>` 上下文不执行脚本）。
  直接导航到 svg 的脚本风险登记为已知接受，不另做消毒。

## 5. 测试决策

网络与 DNS 一律替身（沿 `tests/test_web_tools.py` 既有桩法），真站点走手工矩阵。

| 接缝 | 测试 | 覆盖 |
|---|---|---|
| `run_web_download` / `execute_tool` | `tests/test_web_tools.py` 扩展 | US-01..08、10..13：URL/DNS/重定向闸、流式分块写盘（桩验不进内存）、失败清理临时文件、取消、不占配额、路径守卫翻译、覆盖标记、file_change 事件形状 |
| 注册表 | `tests/test_agent_registry.py` | US-16/17：web 组成员、confirm 档、pass_confirmed 接线、前端名单同名同序 |
| loop 确认流 | `tests/test_agent_loop.py` | US-09：开关开/关、批准/拒绝三态（**不给 AgentRunner 加 kwarg**，既有 Spy 签名不动） |
| 前端组件 | `ChatMessages.test.js` / `ConfirmCard` | US-14：下载卡三态路由、确认卡文案 |
| revert 二进制口径 | 既有 revert 测试 | US-13：下载记录不可回滚 |

手工矩阵（真站点）：Gitee raw 图片、GitHub raw 图片、图床直链、>2MB 文件、404、
跨域重定向、慢滴响应、下载中途取消。

**完成定义**：pytest 全量**串行**绿（共享测试库并发会死锁）＋ vitest `--maxWorkers=4` 绿＋
手工矩阵过＋浏览器实测一遍典型链路（web_fetch → web_download → edit_file → 预览见真图）。

## 6. 明确不做

- 摄入通道扩容（建应用时图片烘快照）——ADR-0051 一行不动
- 仓库地址批量解析（GitHub/GitLab 适配器、树 API、submodule）
- 批量数组入参、下载进度事件（tool_progress）、断点续传、限流退避重试
- 业务配额（次数/单文件/总量）与独立配置键
- 子 run 收录、私有仓/鉴权（URL 内嵌凭据拒收）
- 内容审查/病毒扫描/svg 消毒改写
- 运行时代理远端资源

## 7. 补充说明

- **与 ADR-0051 的表面冲突**：0051 否决「Agent 侧抓取工具」，0053 已开只读面，本票在同一
  SSRF 守卫下补落盘面——出网判定不新增，新增的只是字节落盘。以 ADR-0054 为准。
- **磁盘占用不设防**是用户明确接受的取舍；技术兜底只防挂死与 OOM，不防磁盘满。
- **前端名单数组**：`WEB_FETCH_TOOL_NAMES` 变为 `['web_fetch', 'web_download']`，
  与 registry `web` 组同序；卡片路由按名字分叉（WebFetchCard / WebDownloadCard），
  沿用 BrowserFlowCard 按前缀换措辞的先例。
- **实现期注意**：`app/agent/loop.py` 与 `tests/test_agent_loop.py` 当前有另一会话的未提交
  WIP，动这两处前先复核归属（本票预期不改 loop.py——确认闸走注册表通用分支）。