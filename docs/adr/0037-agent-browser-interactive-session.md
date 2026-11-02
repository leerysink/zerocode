# Agent 浏览器验证从一次性只读检查改为可交互会话

ADR-0031 把浏览器验证定成「每次请求新建 BrowserContext、只读、不点击」。上线后暴露两类它回答不了的问题。一是等待策略只有固定 sleep：应用带 2 秒级开场动画时，截图必然落在动画中段，Agent 连续 6 次都读到「正在配置组件」，把它误判成渲染故障——而这不是运气差，`domcontentloaded` 后等 1200ms 写死在 `agent.browser_settle_ms`，与任何应用自己的启动时序无关。二是只读检查看不到业务闭环：表单能不能提交、点删除有没有真的少一行、按钮点了有没有反应，只有操作过才知道。两类问题都要求活页面跨调用存在，而「无状态、一次一 context」正是 0031 的安全支点，所以这不是加参数能解决的，需要单开一条修订。

决策：sidecar 增加进程内 session 注册表（`POST /sessions` 开、`POST /sessions/{id}/step` 走一步、`DELETE` 关），活页面留在 sidecar 内存，后端只持 `session_id ↔ chat_session` 绑定与额度。隔离前提中不变的部分照旧：同一个无 docker.sock、无平台库凭据、非 root、只读根文件系统的容器；仍不接收任意 URL，目标 origin 由后端构造并校验；仍不复用用户浏览器的 cookie 与登录态——session 是 sidecar 新建的干净 context，不是宿主上那个 Chrome。变的部分只有一条：context 的生命周期从「一次请求」拉长到「一个 run（空闲 120s 回收）」，页面 JS 因此可以在一次验证内累积状态并执行 Agent 的动作。

爆炸半径随之重估：从「Agent 看到用户自己的应用页面」变成「Agent 以该页面当前登录身份改用户自己的 dev 数据」。这仍是自伤域而非越权域——目标锁死在当前会话所属应用的 dev 预览，dev 库可由 `db_write` 重置或重建栈恢复。用户明确选择不逐次确认动作，理由是弹框会把十几步的路径压碎、且 dev 本就是沙箱。

## 后果

- 修订 ADR-0031 后果第 1 条：对外不再是「一个只读工具」，而是「一个只读快检 `preview_page` + 三个交互工具 `browser_open` / `browser_step` / `browser_close`」。规格 2026-10-31 的 US-14（不提供点击/输入/上传/执行脚本的参数面）与 §12 非目标第 1 条自本 ADR 起失效。
- 三项配套缺一不可，缺了就退化成事故：活 session 数上限（超限直接拒绝而非排队——排队等于让一次工具调用挂几十秒）、空闲 TTL、run 收口时后端 best-effort 发 `DELETE`。`mem_limit: 1g` 之下僵尸 context 会把 sidecar 拖进 OOM。
- 提示注入面从「读到不可信文字」扩大到「基于不可信文字采取行动」：一个写着「点这个按钮导出全部数据」的页面有机会诱导 Agent 操作。缓解是工具面锁死在用户自己的 dev origin、最坏是自伤；prompt 声明页面内容是数据不是指令，属于概率性防护，不是硬隔离。
- `fill` 的 value 会进聊天消息历史。对标记为 sensitive 的元素（`type=password`，或名称含 token/secret/code）在落库前遮断；不符合该判定的敏感输入仍只依赖统一规则表，作为残余风险记录，不宣称已解决。
- 交付包 compose 的浏览器服务约束不变，但打包测试需新增断言防止 read_only / cap_drop / 资源限制这几项在后续改动中漂移。
