# 0016 网关域名走环境变量、证书用与域名无关的固定文件名

ADR-0001 结尾写的是「本机先行，后续上服务器时改的是 DNS 解析与证书签发，网关形态不变」。真到换域名
这一步才发现那句话只对了一半：形态确实没变，但域名当时被写死在**五处**——`lua/router.lua` 的 slug
匹配模式、`nginx.conf` 两个 `server_name`、`nginx.conf` 的证书文件名、`404.html` 的文案、
`make-cert.{sh,ps1}` 的签发目标。其中前两处的失效方式是**静默**的：换域名后全站 404，日志里没有一行
指向真因；404 页还会用旧域名告诉访问者「这个应用不存在」，把人引向完全错误的排查方向。

我们决定两件事：**域名一律不在 gateway/ 的任何文件里出现**（只保留一处标注为「env 未设时的回退」的
默认值），由 `GATEWAY_DOMAIN` 环境变量经 compose 透传给 lua；**证书固定叫 `gateway.crt` /
`gateway.key`**，与域名彻底解耦，让本机自签（`make-cert`）和服务器上的 Let's Encrypt
（`acme.sh --install-cert`）写同一对文件名。

被否决的方案：`envsubst` 渲染 nginx.conf 模板（要多一个启动前的渲染步骤和一个模板文件，而 nginx 本来
就能用 `env` 指令把变量递给 lua，server_name 更是根本不需要知道域名）；保留域名化的证书文件名、换域名
时重新生成 nginx.conf（把「换 CA」和「换域名」耦合成一次改配置，而这两件事实际上彼此无关）；在前端也
放一份域名常量（那就是第二个事实来源，换域名时必然漂移）。

## 后果

- `nginx.conf` 的两个 `server_name` 都是 `_`（catch-all）。这是安全的：443/80 上只有本网关，而
  「这个主机名归哪个应用」本来就由 lua 按 routes.json 判定，`server_name` 再写一遍域名只是多一份
  需要跟着改的硬编码。代价是 nginx 不再对陌生域名提前拒答，全部落到 lua——而 lua 对匹配不上的
  host 走 `ngx.exit(404)`，语义等价。
- `env GATEWAY_DOMAIN;` **必须**写在 nginx 主上下文。nginx 默认清空继承来的环境变量，漏写这一行时
  lua 里 `os.getenv` 拿到 nil，不报错、不告警，只是静默退回默认域名。已用源码守卫测试钉住。
- 换域名要改**两处 env**：平台侧的 `ZEROCODE_GATEWAY_DOMAIN`（`app/deploy/nics.py` 用它拼控制台展示
  的地址）与网关侧 `gateway/.env` 的同名变量。gateway 的 compose 是一个独立项目，它的 `.env` 在
  `gateway/` 下而不是仓库根，这一点很容易漏。两边对不上的症状是「地址显示正常，点进去全 404」。
- lua 侧的 slug 字符集（`[a-z0-9]+`）**刻意比平台侧宽**（`slugs.SLUG_RE` 是 `^[a-z][a-z0-9]{1,39}$`）。
  宽的一侧只会去 routes.json 查不到、然后给出 404 提示页，不会误拒合法 slug；反过来收紧则会在两侧
  规则不同步时把合法应用挡在门外。不对称是有意为之，测试里钉住了现状，将来要收紧是个自觉决定。
- 域名进 Lua 模式前必须转义：`.` 在模式里是「任意字符」，`-` 是惰性量词。不转义的话 `my-demo.com`
  根本匹配不上，而 `leerysink.com` 会连 `leerysinkXcom` 一起匹配。
- `gateway/certs/` 里旧的 `wildcard.<域名>.crt/key` 从此不再被任何配置引用；`make-cert.ps1` 会把它们
  列为孤儿文件提示，但**不删**——证书目录属于用户数据。
- 本机自签的 CA 每次跑 `make-cert` 都会重建，所以先前导入过 `ca.crt` 的客户端需要重新导入一次。
  服务器上走 acme.sh 之后这个问题不存在（公信 CA，无需导入）。
- 前端不持有域名常量，这条已由 `tests/test_gateway_domain_param.py` 的两条守卫钉住：一条扫
  `frontend/src` 里不得出现网关域名，一条扫不得用模板字符串把 slug 拼成 http(s) 地址（换成别的
  域名也一样错，所以盯的是动作而不只是那个字符串）。原先 `PublishModal.vue` 的 `mainUrl` 与
  `AppsView.vue` 的 `copyProdUrl` 各有一份 `zerocode.com` 兜底，现已去掉：拿不到地址就不显示、
  如实说明，而不是编一个。这个兜底比「换域名会漂移」更坏——`nics.app_urls` 在 slug 缺位时
  **故意**回空串（不谎拼一个必然无效的地址），`ensureUrls` 又把请求异常静默吞掉，所以兜底恰好
  在「后端说没有」和「接口挂了」两种情况下触发，把死链塞进剪贴板，而它在界面上和真地址无法区分。
- 顺带修掉的同源缺陷：`AppsView.copyUrl` 是全前端唯一没有 catch 的剪贴板调用。`navigator.clipboard`
  只在安全上下文（https 或 localhost）存在，而演示很可能走 `http://<服务器IP>:8100`——那里它是
  `undefined`，点击「正式地址」和预览抽屉的「复制地址」会静默无反应。这也是本 ADR 主张
  「IP+端口直连」与「通配域名 HTTPS」两条访问路径并存时，前端必须同时兼容的后果。
- 打包期到运行期的这条 env 通道（`ZEROCODE_GATEWAY_DOMAIN` 的「显式参数 > 打包机环境变量 > 默认」三级
  取值，以及 compose 写 `${VAR:-打包值}`、`.env.example` 写裸值的两副面孔）后来被推广到依赖镜像源；
  那条链和「哪些值配得上运行期覆盖」的判据见 ADR-0017。
