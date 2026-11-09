# 微信小程序分发规格说明

日期：2026-11-09
上游决策：[ADR-0043](../adr/0043-miniprogram-third-distribution-target.md)、
[ADR-0044](../adr/0044-miniprogram-build-overlay-in-builder.md)、
[ADR-0045](../adr/0045-miniprogram-publish-boundary-and-credentials.md)
术语：[CONTEXT.md](../../CONTEXT.md) 的「小程序端 / 小程序凭据 / 小程序版本档位 / 小程序预览码 /
小程序构建任务」五节。

## 1. 问题陈述

移动栈应用已经能发布 H5、并由平台代出 Android 壳（ADR-0042），但微信里没有产物：用户要在
微信内使用，只能分享 H5 链接，无法获得小程序的会话入口、也进不了微信的审核与分发体系。

本规格补的是**同一移动栈应用的第三个分发目标**：应用绑定自己的小程序账号（AppID + 代码上传
密钥）后，平台把 `frontend/mobile/` 的 uni-app 源码编译成 mp-weixin 包，自动上传到该账号产生
**开发版**，并能按需生成**小程序预览码**；体验版、提审、发布仍由用户在微信公众平台亲手操作
（ADR-0045：平台不越过分发边界）。

## 2. 方案概述

### 2.1 产物形态

- 小程序端是与 H5 **共源码、共后端**的独立编译产物：页面与逻辑编进小程序包，接口请求打到本应用
  自己的后端 `https://{slug}.{GATEWAY_DOMAIN}/api`（`nics.app_url` 投影，ADR-0039/0043）。
- 不新增 stack key、不新增端口、不新增数据库；app 记录仍是那一条，H5 的 `/h5/` 与小程序并存。
- 不是 web-view 壳：不复用 H5 页面，也不依赖 H5 是否可达（只依赖接口域名可达且可被小程序请求）。
- 一期不含任何微信原生能力（登录、支付、订阅消息、分享回调），见 §6。

### 2.2 构建链

独立 builder 容器 `zerocode-miniprogram-builder:1`（Node + uni-app mp-weixin 工具链 +
`miniprogram-ci`），由平台后端以 `docker run --rm` 串行驱动：

1. 复制应用的 `frontend/mobile/` 到工作区 `data/mp_build/{slug}/`（**副本**，不回写应用目录）。
2. 平台侧叠加构建配置（ADR-0044）：
   - `package.json` 补 `@dcloudio/uni-mp-weixin`（版本与 H5 侧 uni-app 包保持同一版本号）与
     `build:mp-weixin` 脚本；
   - `src/manifest.json` 补 `mp-weixin.appid`（取自已绑定凭据）与 `mp-weixin.setting.urlCheck`；
   - 生成 `project.config.json`（appid、`miniprogramRoot: dist/build/mp-weixin/`）。
3. 对副本的 `src/utils/request.js` 打接口基址补丁：文件头注入
   `const API_BASE = 'https://{slug}.{GATEWAY_DOMAIN}/api'`，并把
   `url: '/api' + path` 精确替换为 `url: API_BASE + path`。文件缺失或该字面量不是恰好一处 →
   任务失败（「移动端请求封装结构已变化，需平台适配」），**不出半成品包**。
4. `uni build -p mp-weixin` 编译。
5. `miniprogram-ci` 上传（`upload`）产生开发版，版本号取应用当前发布版本、备注含应用名与时间；
   或预览（`preview`）产生预览码图片。
6. 任务状态落库；失败保留日志尾部与分类原因，旧开发版不受影响。

构建容器**需要出网**（npm 镜像 + `api.weixin.qq.com:443`），与 APK builder 的 `--network none`
不同；凭据目录只读挂载。

### 2.3 数据与文件

新增平台表 `app_mobile_miniprogram`（一个应用一行）：

| 列 | 含义 |
| --- | --- |
| `app_id` | 主键，FK `app_project(id) ON DELETE CASCADE` |
| `status` | `idle` / `building` / `uploading` / `ready` / `failed`（无行等价 `idle`） |
| `job_kind` | 当前/最近任务：`precheck` / `upload` / `preview` |
| `error` | 失败摘要（含分类与日志尾部，成功清空） |
| `appid` | 已绑定的小程序 AppID（展示时脱敏） |
| `key_sha256` | 上传密钥内容摘要：仅用于「密钥是否换过」与不落原文 |
| `key_uploaded_at` | 最近一次密钥上传时间 |
| `version` | 最近一次成功上传的版本号（`b<14位时间戳>`） |
| `uploaded_at` / `preview_at` | 最近成功上传 / 生成预览码时间 |
| `updated_at` | 行更新时间 |

平台数据区：

- 凭据：`data/miniprogram_keys/{slug}/private.key`（`0600`），与 `appid` 一起绑定；删除应用时整目录清理。
- 构建工作区：`data/mp_build/{slug}/`；每次任务前清空重建，成功或失败后只留日志尾部，不长期保留包体。
- 预览码：最近一次生成写入 `data/miniprogram_preview/{slug}.jpg`（独立于构建工作区，工作区清空
  不会抹掉上一张码），只经鉴权接口读取，**不进**任何 `uploads` 公开目录。

### 2.4 地址与前置条件

- 接口基址与 H5 同源，单点走 `nics.app_url(app)`；小程序 request 合法域名需要**逐个**添加
  `{slug}.{GATEWAY_DOMAIN}`，微信不支持通配符。
- 小程序真机请求只接受备案域名 + 有效 HTTPS + 443 默认端口：`ZEROCODE_SSL_ENABLE` 关闭、
  使用自签证书、或域名不可达时，预检直接失败并给出前置条件提示，不做「跳过校验」分支
  （该开关在预览/体验/正式环境不生效，只会制造假绿）。
- 上传密钥需在微信后台把**平台服务器出网 IP** 加进白名单；IP 探取失败仅提示，不阻断绑定。

### 2.5 接缝

| 接缝 | 位置 | 用途 |
| --- | --- | --- |
| 分发信息 | `app/deploy/mobile.py::info` | 弹框唯一数据源；新增 `miniprogram` 字段与顶层凭据/域名/IP 引导信息 |
| 发布钩子 | `prod.publish(..., on_success=…)`（[app/api/apps.py:534](../../app/api/apps.py#L534)） | 发布成功后追加小程序上传入队，不改发布语义 |
| 任务器 | `app/deploy/mpbuild.py` 的 `MiniprogramBuilder` | 入队/去重/串行/状态/复用判定；`_docker` 可被测试替换 |
| 纯函数 | `app/deploy/mpoverlay.py` | 叠加配置生成、`request.js` 补丁、版本号与备注、错误分类 |
| REST | `GET /api/apps/{id}/mobile` 及 `…/miniprogram/*` 子资源 | 前端唯一入口，凭据只进不出 |
| 组件 | `frontend/src/components/MobileModal.vue` | 卡片 ☰ 与聊天页「更多」共用的同一弹框 |
| 状态表 | `changelog/V20261109_xx_miniprogram.sql` | 镜像 `app_mobile_apk` 的建表/恢复约定 |

## 3. 用户故事

### 凭据与绑定

- **US-01** 移动栈应用在弹框「小程序」区块填写小程序 AppID 并上传代码上传密钥文件；保存后密钥落到
  `data/miniprogram_keys/{slug}/private.key`（`0600`），DB 只存 AppID 与密钥摘要。
- **US-02** 凭据不回显：任何读取接口都不返回密钥内容，AppID 只显示脱敏形态（保留 `wx` 前缀与末四位）；
  密钥只显示「已上传 / 上传时间」。
- **US-03** 一应用一账号、AppID 全局唯一：绑定已被其他应用占用的 AppID 返回 409；换绑必须先解绑。
- **US-04** 解绑清理 DB 行与密钥文件，状态回 `idle`；正在跑的任务按当前轮失败收口，不再补跑。
- **US-05** 非移动栈应用调用小程序端点返回 400；未绑定凭据时的上传/预览返回 409 并提示先绑定。
- **US-06** 绑定成功后异步预检一次：构建 + `preview`，**不产生开发版**；结果 `ready` 或
  `failed` + 分类原因。
- **US-07** 预检错误分类：密钥无效 / IP 未加白 / 编译失败（带日志尾部）/ 出网失败 / 域名或证书
  不可用（预检额外做 HTTPS 可达性检查）；分类与原文都进 `error`，弹框可读。

### 构建与上传

- **US-08** 生产发布成功（`status_prod='running'` 落库后）自动入队一次上传任务；发布响应不等待任务，
  任务失败不改变发布结果、发布记录与 prod 容器。
- **US-09** 入队闸门：`status_prod='running'` 且存在当前发布版本；不满足时 `deferred`，不构建。
- **US-10** 上传版本号 = 当前发布版本 `b<14位时间戳>`，上传备注含应用名与时间；每次上传覆盖微信
  「开发版」的最新一份，不保留历史开发版。
- **US-11** 弹框「重新上传」无论版本与状态都执行一次上传；`uploading`/`building` 时按钮禁用；
  prod 未运行返回 409 并说明先发布。
- **US-12** 任务串行：同一时刻最多一个任务；同应用重复入队折叠；任务运行中又有新请求，在本轮结束后
  补跑一次（与 `ApkBuilder` 的入队语义一致）。
- **US-13** 构建配置只在副本上叠加，应用 `frontend/mobile/` 内文件字节不变；存量移动栈应用绑定后
  立即可用，无需新建应用或改造模板。
- **US-14** `request.js` 补丁必须精确命中：文件缺失、找不到唯一目标字面量、或副本 patch 后仍含
  相对 `/api` 请求 → 失败并提示平台适配，不产出包。
- **US-15** 失败时保留日志尾部（≤4KB）与分类原因；成功或失败都不长期保留编译产物与源码副本。
- **US-16** 预览码按需生成：用户点「生成预览码」才跑一次 `preview` 任务；图片经鉴权接口返回，
  短时有效（以微信返回为准），过期后需重新生成；同一应用只保留最新一张。
- **US-17** 开发版与预览码互不替代：自动/手动上传只产生开发版，预览只产生码；两个动作可分别执行。
- **US-18** 平台启动时把残留的 `building`/`uploading` 行改为 `failed`，摘要「平台重启导致任务中断，
  请重试」；旧开发版仍可用。
- **US-19** 删除应用时随 FK 级联删除状态行，并清理 `data/miniprogram_keys/{slug}/`、
  `data/mp_build/{slug}/` 与 `data/miniprogram_preview/{slug}.jpg`。

### 弹框与引导

- **US-20** 现有移动端弹框新增「小程序」区块；卡片 ☰ 与聊天页「更多」共用同一组件，非移动栈应用
  不显示该区块。
- **US-21** 区块内容：绑定/解绑、AppID 脱敏、任务状态与最近上传版本、失败摘要、「重新上传」、
  「生成预览码」（展示二维码）、「去微信公众平台」跳转。
- **US-22** 区块展示需要人工加白的条目：request 合法域名 `https://{slug}.{GATEWAY_DOMAIN}`
  （说明需在该小程序后台逐个添加、不支持通配符）与平台上传出网 IP，均可一键复制。
- **US-23** 区块给出三步指引：微信后台选开发版 → 设为体验版 → 提审并发布；文案明确「平台不会提审、
  不会发布」。
- **US-24** 任务进行中复用现有轮询节奏（3 秒）刷新 `GET /mobile`，离开弹框停止；`building`/`uploading`
  期间上传与预览按钮禁用。
- **US-25** SSL 关闭、域名不可用或证书不可信时，绑定区块展示前置条件提示并禁用上传；不提供跳过后门。

## 4. 已定决策

1. 小程序端是同一移动栈应用的**第三分发目标**，不新增 stack key、端口与数据库。
2. 接口走平台统一网关域名（`nics.app_url`），沿用绝对基址；域名逐个加白，不用通配符。
3. 平台只产出**开发版 + 预览码**；体验版、提审、发布由人在微信官网完成。
4. 凭据 = AppID + 代码上传密钥，落 `data/miniprogram_keys/{slug}/`（`0600`），不回显、不进模型
   上下文、不进应用源码与市场包。
5. 一应用一账号、AppID 全局唯一；换绑先解绑。
6. 绑定后异步预检一次；预检只 build + preview，不产生开发版。
7. 发布成功后自动上传 + 弹框手动重传；未绑凭据时跳过。
8. 闸门为 `status_prod='running'`；不满足则 `deferred`。
9. 版本号 = 应用当前发布版本；每次上传覆盖开发版最新一份。
10. mp-weixin 配置**构建时平台侧叠加**，应用源码不动，存量移动栈应用立即可用。
11. 接口基址**构建期对副本打补丁**，结构不符即失败。
12. 独立 builder 容器、串行执行、独立状态表；失败与发布解耦，旧开发版保留，可重试。
13. 预览码按需生成、鉴权接口返回、不落公开目录。
14. 一期**不接**微信登录/支付/订阅消息/分享回调；二期再上静默登录（AppSecret 平台存 + env 注入）。
15. 后台-only 的发布也触发一次上传（内容可能没变），一期接受；二期可加内容哈希跳过。

## 5. 测试决策

### 后端

- `tests/test_mp_overlay.py`（纯函数，最高接缝）：
  - overlay 生成的 `package.json`/`manifest.json`/`project.config.json` 含预期 appid 与脚本，
    且不触碰原副本之外的路径；
  - `request.js` 正常补丁（唯一命中）；文件缺失、零命中、多命中三种失败分支；
  - 版本号取发布版本、备注含应用名；错误分类映射（密钥/IP/编译/网络/证书）。
- `tests/test_mp_build.py`（假 docker + 假 `miniprogram-ci` 输出，镜像 `tests/test_apk_build.py`）：
  - 未绑凭据 → `skipped`；prod 未运行 → `deferred`；运行中重复入队 → `building`；同轮结束后补跑；
  - 成功上传 → `ready` + `version`/`uploaded_at`，且不产生预览码；
  - 预检成功不写版本、不产生开发版；
  - 上传失败 → `failed` + 分类摘要，旧 `version` 保留；
  - 启动恢复把 `building`/`uploading` → `failed`（固定文案）；表缺失（42P01）容忍。
- `tests/test_mobile_api.py`（扩展现有文件）：
  - `GET /mobile` 新增 `miniprogram` 形状（状态/appid 脱敏/version/失败摘要/引导域名与 IP）；
  - 绑定（合法/非法 AppID/密钥非私钥/重复 AppID 409）、解绑、上传与预览的 202/400/409/403；
  - 预览图接口需鉴权，未生成时 404；响应不出现密钥原文。
- `tests/test_migrations.py`：新迁移可重放，级联删除生效。

### 前端

`frontend/src/components/MobileModal.test.js`（沿用现有弹框测试风格）：

- 非移动栈不渲染小程序区块；移动栈渲染绑定表单与三步指引；
- 绑定后 AppID 脱敏展示、密钥不回显；
- `building`/`uploading` 时上传与预览禁用并轮询，结束后停止；
- 失败摘要与分类可见；预览码仅在生成成功后显示；
- 复制域名 / IP 的行为可用（mock clipboard）。

### 手工验收

1. 给 `apps/test2`（移动栈）绑定测试小程序号与上传密钥，确认预检通过、未产生开发版；
2. 发布 prod，等待自动上传结束，在微信后台「版本管理」看到开发版且版本号 = 发布版本；
3. 生成预览码，手机微信扫码，确认页面能调通 `https://{slug}.{域名}/api`（真机、非开发工具）；
4. 故意去掉 IP 白名单/换错密钥，确认失败分类可读、旧开发版仍在、弹框可重试；
5. 停止 prod 后点重传，确认 409；重启平台，确认残留任务转 `failed` 而非永久转圈。

后端定向 pytest 串行执行（共享 `zerocode_test` 库）；前端定向 `--maxWorkers=4`；不跑全量 pytest。

## 6. 明确不做

- 微信登录、支付、订阅消息、分享回调与任何微信原生能力（一期）；登录排二期（静默登录 + 自动建号，
  AppSecret 平台存并经 env 注入，应用源码不落密钥）。
- 体验版设置、提审、发布、第三方平台授权（无公开 API，且属于 ADR-0045 划定的边界外）。
- 独立小程序栈、web-view 壳、导出源码让用户自行用微信开发者工具上传。
- 按环境多 AppID（测试号/正式号并存）、一应用多账号。
- 分包/主包体积优化、代码包压缩、按需注入等小程序工程化调优。
- 预览码长期保存、公开分享链接、非成员扫码。
- 跨端账号合并（H5 与小程序同一自然人视为两个账号，二期也不合并）。
- 非移动栈应用的小程序入口。

## 7. 补充说明

- **外部前置**（全在平台之外，未满足则预检必失败）：ICP 备案通过；网关切 acme.sh 真证书
  （`CERT_MODE=acme`，DNSPod DNS-01，`--fullchain-file`）；平台服务器出网 IP 进上传密钥白名单；
  每个 `{slug}.{GATEWAY_DOMAIN}` 逐个加入该小程序的 request 合法域名；小程序主体认证、服务类目、
  体验成员由用户自行完成。
- **待实测风险**：小程序主体与平台域名跨主体时能否添加 request 合法域名；个人主体类目对业务接口的
  限制；主包 2MB 上限（uni-app 默认产物可能逼近）；极老 Android 对 Let's Encrypt 根链的兼容性。
- **uni-app 版本耦合**：overlay 里的 `@dcloudio/uni-mp-weixin` 必须与 H5 侧依赖同版本号，升级模板时
  要同步改 overlay，否则编译期报错——这是 ADR-0044 选「构建时叠加」的已知代价。
- **出网差异**：小程序 builder 不能 `--network none`；若部署环境限制出网，失败分类会落到「网络」，
  运维文档要写明需要放通 `api.weixin.qq.com:443` 与 npm 镜像。
- **日志**：失败摘要包含 docker 输出尾部与 `miniprogram-ci` 错误码，便于区分「密钥错」与「IP 未加白」。
- **凭据可重新下载**：上传密钥丢失可在微信后台重置并重新下载，平台侧不承诺备份；换密钥后
  `key_sha256` 变化会触发一次预检。
- **覆盖式开发版**：每次发布都覆盖开发版最新一份，若用户在微信后台已基于旧开发版设为体验版，
  体验版仍是旧快照，不受影响。
