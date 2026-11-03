# 移动端与后端同应用、H5 走同域 `/h5/`，APK 由平台代管产物而不由平台构建

uni-app 进入平台后有三种可选边界：① 一个应用内容 backend + 管理端 + 移动端；② 拆成移动端应用与后端应用两个 compose 项目；③ 移动端不自带后端、直连平台服务。选 ①，且新增一个大型档移动栈（`vue3-springboot` 底座 + uni-app 工程），H5 挂在应用同域的 `/h5/` 路径下。

否决 ② 的理由是平台今天**没有**跨应用通路，而不是它不好写：每个应用独享 `{slug}_dev` / `{slug}_prod` 库与角色，网关 `routes.json` 只登记 `status_prod='running'` 的应用，模板后端与管理端 nginx 都没配 CORS。拆两个应用等于先为平台造「应用间地址绑定 + 跨应用数据通路 + 依赖级联启停 + 包依赖」这一整套能力，再谈移动端。否决 ③ 同理——生成应用与平台之间没有任何调用关系（小栈后端匿名，大栈的 JWT 只在应用内部），它是「平台开放 API」那个独立里程碑的前置工程。

关键点是**「多个服务」不等于「多个地址」**：`ports.py::allocate_pair` 每应用只分配一对端口，prod 栈里只有 frontend 容器发布 `prod_port:80`，backend（8080）从不 publish，靠前端 nginx 的 `/api/` 反代（ADR-0001/0002 的既有形态）。移动端加进来就是同一个 nginx 上多一条 `location /h5/`，对外地址、网关路由、资源监控、发布流程逐字不变。真正会带来两对端口两个域名的症状是方案 ②，不是「代码放一起」。

## 后果

- **APK 不在平台内构建**：HBuilderX 云打包依赖 DCloud 账号与其工具链，容器里跑不起来。平台的角色改成代管产物——用户上传 APK，落进该应用 prod 已有的 `uploads` 卷，下载走应用自己的域名（`/api/uploads/**` 已 permitAll），控制台出二维码。规格里必须把「uni-app **CLI 工程**（Docker 构建的前提）能否直接进 HBuilderX 云打包」列为待实测的前置事实，不得当既成结论。
- 因此 APK 与 H5 有一道无法抹平的差异：H5 用同源相对 `/api`，APK 必须填绝对域名（`https://{slug}.{GATEWAY_DOMAIN}`，SSL 关闭时为 `http://{host}:{prod_port}` 形态），打包指引要把两种形态都写清。
- **账号体系复用 `sys_user`**：移动端注册即建后台账号 + 一个受限的「移动端用户」角色，`/auth/login` 直接可用（`/auth/captcha` 是恒返回 `enabled: false` 的占位，不校验验证码，见 ADR-0038）。代价是后台用户列表必须默认过滤掉该角色，否则 C 端用户淹掉管理页。不自建 `app_user`、不改 `JwtAuthenticationFilter` 认两种身份——那要动大栈的安全底座，而 ADR-0038 已钉死「自签 JWT 不走：过滤器还要求 Redis 里有 login 时写入的 `LoginUser` 快照」。
- dev 栈多一个**不发布端口**的 uni-app dev server 服务，管理端 vite 的 proxy 加一条 `/h5 → mobile:8080`（带 `ws: true` 保 HMR）。H5 用 hash 路由 + `publicPath: /h5/`，因此不需要 history 回落规则。
- prod 把 H5 烘进前端镜像（多阶段构建后 COPY 进 `html/h5`），于是构建期同时存在 npm 与 Maven 两套依赖。npm 侧沿用 ADR-0025 的 `build.args` 换源通路；**Maven 换源仍是缺口**（CONTEXT 已记），移动栈把这条缺口的暴露面放大了。
- 移动栈用新的 stack key 登记，但**不复制模板目录**：`scaffold.py::render` 先渲染 `vue3-springboot` 基底、再渲染一份 mobile 覆盖层。这是为了避免仓库里出现两份 springboot 后端代码——那份漂移后来必然修一处忘一处。代价是 scaffold 要长出「多源目录依次 render」这个小能力。
- 创建表单直接出现移动栈，不挂平台级灰度开关。回滚只能靠代码回退重发版，这是本期明确接受的代价。
