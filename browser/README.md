# 浏览器 sidecar（preview_page 与 browser_* 的隔离运行环境）

平台 Agent 的 `preview_page`（只读快检）与 `browser_open` / `browser_step` / `browser_close`
（交互走查）通过这个独立容器打开应用 dev 预览页。它没有 docker.sock、
没有平台库凭据、以非 root 运行；运行期只读根文件系统（见 compose）。

## 本地开发

平台后端跑在宿主（`dev.bat`）时，先启动 sidecar：

```bat
browser\dev-up.bat
```

默认监听 `http://127.0.0.1:8101`，与 `app/agent/browser_tools.py` 的
`ZEROCODE_BROWSER_URL` 默认值一致。停止：

```bat
browser\dev-down.bat
```

首次构建要下载 Chromium（默认走 npmmirror），需要数分钟、约 1GB 磁盘。

基础镜像按发行版代号钉成 `python:3.12-slim-trixie`，而不是浮动的 `python:3.12-slim`：
`playwright install --with-deps` 的 apt 包名表按代号查，代号一换（`ttf-unifont` →
`fonts-unifont`）就是构建期才炸的失败。升级 `requirements.txt` 里的 playwright 时，
要确认它认识基础镜像那个代号，两处一起动。

## 交付部署

`scripts/build_output.py` 会把 `browser/` 打进交付包，平台 compose 内服务名 `browser`，
后端通过 `http://browser:8101` 调用。sidecar 不发布宿主机端口。构建上下文是本目录，
所以交付包的 compose 写的是 `context: ./browser`。

网络受限的服务器上，browser 镜像的构建期换源沿用打包时的 `ZEROCODE_APT_MIRROR` /
`ZEROCODE_PYPI_MIRROR`，Chromium 下载源用 `.env` 里的 `PLAYWRIGHT_DOWNLOAD_HOST`
（打包时烘进 build args 的默认值是 npmmirror），改完 `docker compose build browser`。

## 会话生命周期（browser session）

ADR-0037 之后本服务不再只有「一次一 context」那条无状态路径。下面四条是读代码看不出来、
但改动这条路径前必须知道的事实。

### 端点面：三个会话端点 + 一个截图端点

- `POST /sessions` 开一个会话（干净 context + goto + 首轮采集）。
- `POST /sessions/{id}/step` 走一个动作并回新状态。
- `DELETE /sessions/{id}` 关闭，**幂等**：未知 id 也回 `ok`，因为 run 收口时它多半已被 TTL 回收过。
- `POST /sessions/{id}/screenshot` 只在断言未通过 / 动作失败之后由后端自己追一次，**刻意不进
  `ACTIONS`**（做成动作会把它出现在给模型的动作枚举里），也既不 `touch` 也不计步——截图既不该给
  空闲会话续命，也不该花掉模型的步数额度。
- 另有 `GET /sessions` 只作排障（内存里攒着哪些会话、各自闲了多久），不进后端调用面。

**超限是拒绝，不是排队。** `ZEROCODE_BROWSER_MAX_SESSIONS`（默认 2）用尽时 `POST /sessions` 直接
409 回「浏览器服务已满」。排队的后果是让一次工具调用挂几十秒，模型只会把它读成超时，从而丢掉
「服务已满」这个唯一可行动的原因；淘汰别人正在用的会话也不行，那会让另一个 run 莫名断线。
上限的二次判定与登记之间没有任何 await，所以这一对操作在单线程事件循环里是原子的。

会话端点**刻意不过 `/inspect` 那个全局信号量**（`MAX_CONCURRENCY = 2`）：一个走查会话能活几分钟，
占住全局槽位会把 `preview_page` 整条路径饿死；会话的并发上界由上面那个活会话数来管。

### TTL 巡检：打开中的会话不算空闲

`SWEEP_INTERVAL_SECONDS = 10`，每跳扫一次，`now - last_used > ZEROCODE_BROWSER_SESSION_TTL_SECONDS`
（默认 120s）就关 context 并移除。巡检任务自己炸了不退出循环——一次异常把循环摘掉，注册表里那些
context 就只能等进程退出才回收，而 `mem_limit: 1g` 会先一步到。

**`Session.opening` 标记不是装饰。** 一次 open 里最长的 await 就是 `goto` 本身（`timeout_ms` 上限
120000，外面还套 `wait_for` 的 +5s 宽限 = 125s > TTL 120s），逐 await 边界补 `touch` 修不到中间
那一百多秒，症状是在 `goto` 还没返回时把 context 关掉、最终报出去的「页面打开失败」成了失真文案
——真相是被自家巡检杀的。打开路径的每条出口（正常 / 超时 / 异常 / 被 cancel）都在 `finally` 里
清标记并把空闲时钟拨回现在：打开本身就能耗掉一整个 TTL，刚开好的会话不该在下一步就撞上「空闲 120s」。

### ref 一次性是物理事实，不是约定

采集脚本一进页面就先把上一轮所有 `data-zc-ref` 属性 `removeAttribute` 清掉，再领**一整批新令牌**
重打（令牌由 `session_core.new_ref_tokens()` 在 Python 侧生成、数组当参数传给脚本，JS 不摇随机数：
唯一性与形状策略只能有一处，而且要能在没浏览器的机器上单测）。旧句柄作废不需要谁维护映射表、
也不需要谁判过期：上一轮那个 `r7kqmbt` 在这一轮的清单里几乎必然查不到，`step` 因此落到
「ref 无效或页面已变化，请用最新清单里的 ref」那条出口，当场带回新清单。
这条曾经检测不到：形状是按采集顺序排的序号，每次重打从头排，旧序号在新清单里大概率仍然合法，
于是拿旧句柄去点是**静默点到另一个元素**、回 `ok:true`（真机复现，规格 §15）。别把它改回稳定形状。
残余：清单里**查得到**的那个令牌，它的节点可能在两次采集之间被 Vue 就地改了类型（规格 §8 R-37），
所以后端那两处 sensitive 判定与元素名回显仍然只认「动作之前」那份清单。

### 本目录只放镜像 COPY 白名单里的源文件

`browser/Dockerfile` 按文件名逐个 COPY，`scripts/build_output.py` 的 `BROWSER_REQUIRED` 是同一份
白名单，`tests/test_build_output.py` 还有一条**遍历 `browser/*.py`** 的不变式测试：每个 `.py` 都
必须同时在白名单和 Dockerfile 里露面。所以**本地测试辅助文件请放别处（`tests/`）**——放进本目录
会被那条测试当成「该入包的源文件」而误红。

加真正的源文件时必须三处同步：`browser/Dockerfile` 的 COPY、`scripts/build_output.py` 的
`BROWSER_REQUIRED`、`browser/` 里的新文件本身。这条耦合踩过两次坑：Task 2 漏 `session_core.py`、
Task 3 漏 `sessions.py`，两次的症状都是**容器起来即崩**，`preview_page` 与 `browser_step` 恒回
「浏览器检查服务不可用」——离根因很远，所以打包期宁可拒包也不出包。

资源闸数字同理三处同源：代码默认（`browser/sessions.py`）、本地 dev compose（`browser/docker-compose.yml`）、
交付包烘进去的值（`scripts/build_output.py`），由 `tests/test_build_output.py` 钉成一条等式。
只靠代码默认值的话，默认一漂没人看得见，而这两个数正是运维要看得见的那两个数。

## 安全边界

- 只接受 host 命中 `BROWSER_ALLOWED_HOSTS`（默认 `host.docker.internal,127.0.0.1,localhost`）
  的 http(s) 地址；主文档只允许目标 origin，子资源阻断私网与容器地址。
- 一次性检查（`/inspect`）每次新建 BrowserContext 用完即关；会话路径（`/sessions`）把 context
  留在本进程内存里直到 close / TTL。两者都**不读取、不复用宿主浏览器的 cookie 与登录态**
  ——宿主登录态与验证会话是两件事，见 CONTEXT.md 的术语条目。
- 验证会话（ADR-0038）：后端代登后把 JWT 随请求体传来，本服务在页面脚本执行前写进该 context
  里目标 origin 的 `localStorage.token`（脚本自带 `location.origin` 自守，跨域 iframe 不写）；
  口令不下沉，token 不回传、不落盘、不写日志。**当前只接在 `/inspect` 上**，`/sessions` 的注入
  尚未接线（已知缺口，另票）。
- 会话的动作面只有六个：`click` `fill` `press` `select` `scroll_into_view` `navigate`。
  不提供文件上传、不提供脚本执行、不接收任意 URL（地址由后端按应用 dev 端口构造并校验）。
- 容器内 Chromium 关闭自带 sandbox（容器本身是隔离边界），这是 ADR-0031 记录过的取舍。

## 为什么导航前要把宿主别名换成 IP

Vite 5+ 的 dev 服务器有宿主白名单 `server.allowedHosts`，默认只放行 `localhost` 与 IP
字面量。sidecar 用 `host.docker.internal:8213` 访问应用 dev 端口时，`Host` 头就是那个别名，
会被 403 拦成一句 `Blocked request. This host ("host.docker.internal") is not allowed.`——
浏览器渲染的是拦截页，`preview_page` 于是「成功」返回一段与用户应用无关的文字，比失败更坏。

`app.py` 的 `_connect_url()` 在导航前把别名解析成直连地址（优先 IPv4：Docker Desktop 会给
`host.docker.internal` 同时返回 A/AAAA，容器里那条 IPv6 常不可路由），端口与路径不变。
目标仍是同一个宿主端口，只是不再触发对面的宿主检查；解析失败沿用原 URL。
