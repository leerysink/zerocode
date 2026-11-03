# 浏览器验证改用服务端代登的「验证会话」，而不是引入 ego lite 或借宿主浏览器登录态

`preview_page` 打开大栈（vue3-springboot）应用的 dev 预览时拿到的是登录页：路由守卫读 localStorage 里裸 key `token`，没有就 `next('/login')`。于是「页面渲染对不对」这一层永远验证不到，Agent 会把登录页当业务界面读，误判成渲染故障。为拿到这份登录态，评估过把浏览器控制整体换成 ego lite（一个主打「继承你本机 Chrome 登录态」的 AI Agent 浏览器）。

ego lite 这条路否掉，依据是事实而非偏好：它只跑 macOS 与 Windows，Linux 在其 roadmap 上；浏览器本体是独立的桌面 App，仓库里的 `ego-browser` 只是把这台本机浏览器包成页内 JavaScript 函数交给跑在同一台机器上的 Agent CLI（Claude Code、Codex）调用。而本平台的 Agent 是服务端进程，sidecar 是 Linux 无头容器，用户的 Chrome 在用户自己的机器上——三者不在同一台机器。要用它，就得让平台凭空长出一个跑在用户机器上的执行器，而现状是全仓没有任何客户端执行器（无扩展、无 manifest.json、前端无 `eval`/`new Function`，两条 WS 通道都是服务端→前端的单向推送）。更直接的是它的立身卖点「首次启动迁移 Chrome 数据、Agent 直接继承登录态/cookie/扩展」正面撞 ADR-0031 与 ADR-0037 的支点：sidecar 无凭据、不接收任意 URL、不复用宿主浏览器状态。被换掉的动机最终确认只是「Agent 要进得去业务页」，而这个目标不需要用户的机器。

决策：为验证新建立一个**验证会话**——应用自己的登录身份，只活在 sidecar 那一个 context 里、随 context 销毁——由后端代登取得。大栈模板的 `/auth/login` 没有验证码（`/auth/captcha` 只是返回 `enabled: false` 的占位），凭据 `admin/admin123` 是模板种子写死的公开约定、且已陈述在 `app/deploy/contract.py` 的提示词里；后端复用 `preview_status` 那条既有 HTTP 通路（`settings.preview_probe_host`）调登录，只把返回的 JWT 交给 sidecar 注入。sidecar 仍无 docker.sock、无平台库凭据，也不需要知道用户名口令。自签 JWT 不走：`JwtAuthenticationFilter` 除验签外还要求 Redis 里有 login 时写入的 `LoginUser` 快照，只有真登录才建得出会话。

## 后果

- 修订 ADR-0037 里「session 是 sidecar 新建的干净 context」这句：context 仍是新建的、仍不含宿主浏览器的任何状态，但不再匿名——它带着服务端替它登录取得的应用身份。规格 2026-10-31 §12 的「不做登录态复用」自本 ADR 起收窄为「不复用宿主浏览器登录态」。
- 总是先登，失败即匿名：小栈模板（vue3-fastapi）根本没有 `/auth/login`，登录请求失败是正常路径，静默降级为匿名会话并在返回里标清身份，不报错、不重试、不加开关。
- 验证视角从「普通用户」变成「该应用的 admin」：菜单、按钮、可见数据范围都更宽。接受这个错配，用它换「至少能看到业务页」；缓解有两层——工具返回结构与聊天页预览卡片都标注本次身份，且用户手上就有提示词里那同一份账号口令，可自行复现同一视角。
- 反向盲区是既定取舍，不留专用路：常登之后 Agent 看不到「未登录访问应被拦住」这一面，但不给 `preview_page` 加 `anonymous` 参数（参数面扩大加双路径测试不值），需要验证拦截时由用户显式提出。
- 爆炸半径没有变大，但这点必须写清：dev 预览端口以 `0.0.0.0` 裸发布、网关按 slug 反代且 `access_by_lua` 不做鉴权，「预览页 + 默认口令」在面向互联网的演示机上本来就是任何访客可达的公开面。注入的 JWT 不是新秘密。代价是它会出现在后端→sidecar 的请求体里；sidecar 今天没有任何日志调用，uvicorn 访问日志也只打请求行，规格须钉住「不得把该 token 写入日志、聊天历史或落盘产物」。
- 落地位置：先只给已上线的 `/inspect` 与 `preview_page` 加，不排进 ADR-0037 那 8 个切片；`/sessions` 上线时同一份注入自然沿用，届时登录与会话生命周期再合并设计。
- 明确不做：prod 页面验证、外部站点登录态、以及「让用户在工作区设置里填验证账号」（抗口令修改，但凭据落库与加密的收益抵不过它新增的泄露面）。
