# Agent 浏览器验证的「验证会话」规格说明

**日期**：2026-11-03  **来源**：grill-with-docs 拷问  **ADR**：[0038 浏览器验证用服务端代登的验证会话](../adr/0038-agent-browser-verification-session.md)（修订 [0037](../adr/0037-agent-browser-interactive-session.md) 的「干净 context」表述）  **前序规格**：[2026-10-31 preview_page](2026-10-31-agent-browser-verification.md)

## 1. 问题陈述

`preview_page` 打开大栈（`vue3-springboot`）应用的 dev 预览页时，拿到的不是业务页，而是登录页。

原因不在浏览器，在身份：大栈前端的路由守卫读 `localStorage` 里裸 key `token`，没有就 `next('/login')`（`app/templates/vue3-springboot/frontend/src/router/index.ts`、`store/user.ts`）。sidecar 每次请求新建的 context 不含任何身份，于是首屏 200、JS 正常执行、控制台干净、截图完整——唯独内容是登录表单。Agent 读到的是「用户名 / 密码 / 登录」，很容易把它当成页面渲染异常，或者据此宣称业务页正常。规格 2026-10-31 已把「登录态、扩展、设备像素比不同」记为已知限制，但从未给出收口路径。

用户侧的认知里，这件事叫「不够真实」：人点开预览时浏览器里带着自己的登录态，Agent 却没有。拷问确认，真正需要的不是用户本机那份登录态，而是**Agent 能进到业务页**。本规格给验证一个新建立的**验证会话**，由服务端代登取得，只活在 sidecar 那一个 context 里。

## 2. 方案概述

- **一条新增的身份获取链路**：后端在调用 sidecar 之前，用模板公开的 `admin/admin123` 调应用自己的 `POST /api/auth/login`，拿到 JWT；只把 JWT 随 `/inspect` 请求体交给 sidecar，由 sidecar 在页面脚本执行前写入目标 origin 的 `localStorage.token`。
- **sidecar 仍然不知道口令**：它收到的只有一次性的 JWT；用户名口令只存在于后端进程与模板种子里。sidecar 的隔离前提（无 docker.sock、无平台库凭据、非 root、只读根文件系统）一条不变。
- **总是先登，失败即匿名**：登录是每次检查的正常第一步，不是可选动作。小栈（`vue3-fastapi`）没有 `/auth/login`，请求失败是预期路径——静默降级为匿名会话，并在结果里如实标注原因，既不报错也不重试。
- **结果里多一个身份字段**：工具返回与聊天页预览卡都标注本次是「admin」还是「匿名」，让模型和用户都能一眼看出「这次看到的是谁的视角」。
- **接缝仍是既有的那一个**：`preview_page` 工具契约与 sidecar `POST /inspect` 请求/响应体。不新增端点、不新增容器、不新增依赖、不改工具 schema。

## 3. 用户故事

### 拿到身份

- **US-01** 作为 Agent，当我用 `preview_page` 检查大栈应用时，打开的是业务页而不是登录页，返回里有 `identity.kind = "admin"` 与人类可读的 `identity.label`。
- **US-02** 作为 Agent，当我检查小栈应用时（它没有 `/auth/login`），页面照常打开，`identity.kind = "anonymous"`，`identity.note` 说明「该应用没有可用的登录接口」，不出现任何错误提示。
- **US-03** 作为 Agent，当登录失败（口令被用户改过、接口 500、返回体不合预期）时，我仍然拿到页面结果，`identity.kind = "anonymous"`，`identity.note` 写明失败原因（不含口令、不含 token）。
- **US-04** 作为 Agent，当登录请求挂住时，检查不会因此变慢到超时：登录有独立的短超时（默认 5s），超时按匿名降级，页面检查继续。
- **US-05** 作为 Agent，登录只在应用 dev 预览端口上发生；dev 端口没起或拒绝连接时按匿名降级，交给既有的「预览未运行」提示路径。

### 注入与隔离

- **US-06** 作为平台，token 在页面任何脚本执行前写入（`context.add_init_script`），因此 Vue 应用初始化时路由守卫能从 `localStorage.token` 读到它；写入与请求的 `path` 无关，深链接同样生效。
- **US-07** 作为平台，token 只写进本次请求目标 origin 的 `localStorage`，不写 cookie、不写其他 origin、不跨请求复用；context 关闭即消失。
- **US-08** 作为平台，每次检查独立登录、不缓存 token：`/inspect` 保持无状态契约，不引入跨请求会话表。
- **US-09** 作为平台，JWT 不进入 sidecar 的响应体、不进入工具返回、不写日志、不落盘；后端与 sidecar 均不得新增打印请求体或 token 的日志。
- **US-10** 作为平台，用户名口令不传给 sidecar，sidecar 侧找不到任何口令字段。
- **US-11** 作为平台，登录请求走应用自己的 dev 端口（后端已通过 `settings.preview_probe_host()` 打这个口，`preview_status` 用的是同一条通路），sidecar 的 host 允许清单不因此放宽。

### 展示与提示

- **US-12** 作为用户，聊天页的预览卡上有一个身份徽标：`admin` 显示为「以 admin 身份验证」；匿名显示为「匿名（未登录）」，并可在备注里看到原因。
- **US-13** 作为 Agent，我的系统提示词里写明：`preview_page` 默认以 admin 身份打开，看到的是管理员视角；「未登录应被拦截」这类行为它验证不了，需要时请用户提出。
- **US-14** 作为 Agent，`identity` 字段在失败结果里也存在（例如 `ok:false` 的预览未就绪），不让模型误以为「没标身份就是匿名」。

## 4. 已定决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 换不换浏览器实现 | **不换**。继续 Playwright + headless Chromium sidecar | ego lite 只跑 macOS/Windows，Linux 在 roadmap；它是桌面浏览器，Agent 在本机 CLI 里调用。本平台 Agent 在服务端，要用它就得凭空造一个客户端执行器 |
| 登录态来源 | 服务端代登：`admin/admin123` + 应用 `/api/auth/login` | 口令是模板种子写死的公开约定，已陈述在 `app/deploy/contract.py`；`/auth/captcha` 只返回 `enabled:false` 占位，程序化登录本就通 |
| 自签 JWT | **不做** | `JwtAuthenticationFilter` 除验签外还要求 Redis 里有 login 时写入的 `LoginUser` 快照，只有真登录建得出来 |
| 谁调 login | 后端调，只把 JWT 递给 sidecar | sidecar 维持「无凭据」的隔离叙述；口令不下沉 |
| 登录地址 | `http://{preview_probe_host()}:{dev_port}/api/auth/login` | 后端容器化与否由 `preview_probe_host()` 处理，与 `preview_status` 同一条既有通路 |
| 触发时机 | 总是先登；失败即匿名 | 单一路径，无双分支判定；小栈应用失败是正常路径 |
| 登录超时 | 独立短超时（默认 5s），超时降级不拖垮检查 | 登录是辅助步骤，不能把 45s 的检查预算吃掉 |
| token 缓存 | 不缓存，每次检查重新登录 | 与 `/inspect` 无状态契约一致；24h 有效期内重复登录成本极低 |
| 注入机制 | `add_init_script` 写入 `localStorage.setItem('token', <json 字符串>)` | `storage_state` 需精确 origin 且整包替换状态；init script 与导航解耦、与 path 无关 |
| 身份错配 | 接受 admin 视角更宽，但工具返回 + 卡片 + 提示词三处标注 | 用户手上就有同一份口令，可自行复现同一视角 |
| 反向盲区 | 不加 `anonymous` 参数，不提供匿名专用路径 | 参数面扩大 + 双路径测试不值；需要时由用户显式提出 |
| 落地位置 | 只落 `/inspect` + `preview_page`，不进 ADR-0037 的 8 个切片 | 盲区今天就能解；`/sessions` 上线时同一份注入自然沿用 |
| 工具 schema | **不变**，仍然只有 `path` | 身份由服务端决定，模型无从选择 |

## 5. 测试决策

**接缝一：`preview_page` 工具契约（`tests/test_browser_tools.py`，sidecar 与登录接口都用替身）**

「完成」的样子：
- 大栈应用 + 登录成功 → sidecar 收到的请求体里带 `session_token`；工具返回 `identity.kind == "admin"`。
- 小栈应用 + 登录 404 → 请求体不带 `session_token`；`identity.kind == "anonymous"` 且 `note` 提到没有登录接口。
- 登录 401 / 500 / 超时 / 返回体缺 `data.token` → 全部落匿名分支，页面结果照常返回。
- 安全断言：把整个工具返回 `json.dumps` 后，**不含** JWT 原文，也不含口令。
- 既有用例（次数上限、遮断、截图落盘、失败提示）全部保持不变。

**接缝二：sidecar 注入纯函数（`tests/test_browser_sidecar_core.py`）**

`inspect_core.py` 保持不 import playwright。新增 `build_session_script(token)` 为纯函数：
- 生成的 JS 把 token 写进 `localStorage` 的 `token` key，值为原始字符串（不是 JSON 包装）；
- token 含引号、反斜杠、换行、`</script>` 之类时仍生成合法且不可越界的脚本（用 `json.dumps` 转义后断言）；
- 空 token / None 不生成脚本。

**接缝三：前端预览卡（`ChatMessages.test.js` 既有 38 例的同等模式）**

- admin 结果渲染「以 admin 身份验证」徽标；
- 匿名结果渲染「匿名（未登录）」并在备注展示原因；
- 旧数据（没有 `identity` 字段）不崩、不显示徽标。

**回归面**：`browser/app.py` 的 `/inspect` 请求体新增字段为可选，旧调用不传 `session_token` 时行为与现在一致（纯匿名），因此侧车可先发、后端后发。

**不需要改的测试**：`tests/test_build_output.py` 的镜像与 Playwright 版本断言——本规格不新增依赖、不新增镜像。

## 6. 明确不做

- 不引入 ego lite、任何桌面浏览器、任何跑在用户机器上的执行器；平台不新增客户端形态。
- 不读取、不复用宿主浏览器的 cookie / localStorage / 扩展。
- 不做「用户在工作区设置里填验证账号」——凭据落库与加密的收益抵不过新增的泄露面。
- 不给 `preview_page` 加 `anonymous` 或其他身份参数；工具 schema 保持只有 `path`。
- 不做 prod 页面验证，不验证外部站点，不支持验证码。
- 不做登录态持久化与跨请求缓存。
- 不改 ADR-0037 的交互三件套（`browser_open` / `browser_step` / `browser_close`）——本规格只覆盖已上线的只读快检。
- 不把「以 admin 验证」升级为多角色/多身份验证。

## 7. 补充说明

**为什么不是 ego lite。** 它的浏览器本体是 macOS/Windows 桌面 App，`ego-browser` 只是把本机浏览器包成页内 JS 给本地 Agent CLI 用；Linux 在 roadmap。本平台 Agent 在服务端、sidecar 在 Linux 容器、用户的 Chrome 在用户机器，三者不同机。它的核心卖点「首次启动迁移 Chrome 数据、Agent 直接继承登录态/cookie/扩展」正面撞 ADR-0031/0037 的两条支点。完整否决依据见 [ADR-0038](../adr/0038-agent-browser-verification-session.md)。

**这不算新增秘密。** dev 预览端口以 `0.0.0.0` 裸发布、网关按 slug 反代且不做鉴权，「预览页 + 默认口令」在面向互联网的演示机上本来就是任何访客可达的公开面。注入的 JWT 不是新秘密；真正的代价是它会经过后端→sidecar 的请求体，因此 US-09 的「不进日志」是硬要求。

**口径收窄。** 规格 2026-10-31 §12 的「不做登录态复用」自 ADR-0038 起收窄为「不复用宿主浏览器登录态」；2026-11-02 规格 US-18（session 不复用宿主浏览器 cookie）**继续有效**，与之不冲突：验证会话是服务端为这个 context 新建立的，不是从宿主浏览器搬来的。

**已知残余风险**：admin 视角比真实用户宽，可能掩盖权限相关的界面差异；这一点由标注与提示词兑，不宣称已解决。
