# Android 远程 URL WebView 壳规格说明

日期：2026-11-05
上游决策：[ADR-0042](../adr/0042-android-webview-shell.md)
取代：[2026-11-03 移动端应用栈](2026-11-03-mobile-uniapp-stack.md) 中「APK 由用户上传、平台代管」的
US-16～US-18；H5 与账号部分继续有效。

## 1. 问题陈述

移动栈的 H5 已经能发布，但弹框里的 APK 仍是「用户在平台外打包后上传」。真实用户没有
HBuilderX / DCloud 账号与工具链，这个入口等于摆设；iOS 也没有可自动化的构建路径。结果是
「移动端」只在浏览器里成立，没有一个可安装到 Android 手机上的产物。

本规格要补的是：**生产发布成功后，平台自己构建一个可安装的 Android 壳，用户在现有移动端
弹框里直接下载/扫码安装**。壳加载应用自己的正式 H5 地址，不复制 H5 内容，也不引入
uni-app app-plus 编译链。

## 2. 方案概述

### 2.1 产物形态

- 壳是**远程 URL WebView 壳**：一个 Activity + WebView + 一组资源，启动即加载
  `nics.h5_url(app)`（SSL 开为 `https://{slug}.{GATEWAY_DOMAIN}/h5/`，SSL 关为
  `http://{宿主}:{prod_port}/h5/`）。
- H5 与接口同源，继续用相对 `/api`；JWT、`uni.*` 调用、路由都不变。
- 壳不打包 H5 静态文件；H5 更新只需重新发布应用，不需要重出 APK。
- 壳不含任何原生能力（相机、定位、推送、文件选择等）。

### 2.2 构建链

平台后端（容器内已有 `docker.sock`）在后台任务里：

1. 生成构建工作区：`AndroidManifest.xml`、`strings.xml`、`ShellConfig.java`、图标与
   `MainActivity.java`，落在 `data/apk_build/{slug}/`（与 `data/` 同路径挂载，Docker daemon 可见）。
2. 确保构建镜像 `zerocode-android-builder:1` 存在；缺失时从
   `app/templates/android-shell/Dockerfile` 构建（首次需要网络下载 Android SDK）。
3. `docker run --rm --network none` 执行 `build.sh`：`javac` → `d8` → `aapt2` →
   `zipalign` → `apksigner`，输出 `out/app.apk`。
4. 用现有 `mobile._save` 把 APK 原子写进 prod 的 `uploads` 卷（`.part` → `mv`）。
5. 更新 `app_mobile_apk` 状态为 `ready`；失败写 `failed` + 摘要，不影响发布结果。

不用 Gradle/AGP：壳工程没有第三方依赖，build-tools 直出更小、更快、构建期不联网。

### 2.3 数据与文件

新增平台表 `app_mobile_apk`（一个应用一行）：

| 列 | 含义 |
| --- | --- |
| `app_id` | 主键，FK `app_project(id) ON DELETE CASCADE` |
| `status` | `idle` / `building` / `ready` / `failed`（无行等价 `idle`） |
| `error` | 失败摘要（成功时清空） |
| `shell_version` | 已成功产出的壳版本（versionCode），初始 0 |
| `params_hash` | 上次成功构建的壳参数哈希 |
| `requested_at` / `built_at` | 最近一次请求 / 成功时间 |
| `apk_size` | 最近成功产物的字节数 |
| `updated_at` | 行更新时间 |

平台数据区：

- 自定义图标：`data/mobile_icons/{slug}.png` 或 `.jpg`（上传时按魔数判定）。
- 构建工作区：`data/apk_build/{slug}/`；每次构建前清空重建。
- 平台签名：`data/apk_keystore/zerocode.jks` + `pass.txt`（首次构建生成，**需备份**）。

### 2.4 壳参数与哈希

“壳参数”只包含会影响 APK 本体的东西：

- 包名（由网关域名反写 + slug 派生）
- 应用显示名（`app.name`）
- 启动地址（`nics.h5_url`）
- 图标（自定义文件字节哈希，或自动图标的确定性哈希）
- `SHELL_REVISION`（壳代码修订号，改 `MainActivity` 时手动 +1）

**不包含**应用发布版本、H5 内容、发布记录。参数哈希相同且状态 `ready` 且 APK 文件仍在 →
发布后不重建。

### 2.5 接缝

本规格在以下稳定接口上验证，测试尽量打在高接缝：

| 接缝 | 位置 | 用途 |
| --- | --- | --- |
| 纯函数 | `app/deploy/shell.py` | 包名、版本名、参数哈希、自动图标 SVG、Manifest/资源生成 |
| 构建器 | `app/deploy/apkbuild.py` 的 `ApkBuilder` | 入队/去重/串行/状态/复用判定；`_docker` 可被测试替换 |
| 分发信息 | `mobile.info` | 弹框的唯一数据源，聚合 DB 状态、体积、图标与地址投影 |
| REST | `GET /api/apps/{id}/mobile`、`POST …/apk/rebuild`、`GET/PUT/DELETE …/icon` | 前端唯一入口 |
| 组件 | `frontend/src/components/MobileModal.vue` | 状态、重试、下载、图标覆盖 |
| 发布钩子 | `prod.publish(..., on_success=…)` | 发布成功后可注入回调，测试不用真构建 |

## 3. 用户故事

### 构建触发与复用

- **US-01** 移动栈应用生产发布成功（`status_prod='running'` 落库之后），平台在后台自动把 APK
  构建任务入队；发布响应不等待构建，构建失败不改变发布结果。
- **US-02** 自动入队时若库中状态为 `ready`、`params_hash` 与当前参数一致、且 prod 卷里的
  `{slug}.apk` 仍存在，则不构建，直接复用。
- **US-03** 壳参数任一变化（改名、换图标/恢复自动图标、网关域名或 SSL 开关导致启动地址变化、
  壳修订号提升）都使哈希失效：prod 正在运行时立即入队重建；prod 未运行时只留下 `params_stale`，
  下一次发布成功后自动重建。
- **US-04** 用户在弹框点“重新构建”，无论参数是否变化都构建，并使壳版本 +1。
- **US-05** 构建串行：同一时刻最多一个 APK 构建任务在跑；同一应用重复入队折叠为一次，构建期间
  又有新请求则在本轮结束后再跑一次。
- **US-06** 构建状态可查：`idle`（从未构建）、`building`、`ready`、`failed`；失败带可读摘要。
- **US-07** 构建失败不影响应用 H5、发布记录或 prod 容器；上一版 APK 保持可下载，弹框可重试。

### 壳本体

- **US-08** 包名由网关域名反写并追加 slug：`GATEWAY_DOMAIN=zerocode.com`、`slug=erp` →
  `com.zerocode.erp`；域名段按 Android 包名规则清洗（小写、去掉非法字符、数字开头补 `a`）。
- **US-09** 壳版本与应用发布版本独立：`versionCode = shell_version`，
  `versionName = 1.0.{shell_version}`；只有成功产出新 APK 时才递增并落库。
- **US-10** 应用显示名写入 APK 的 launcher 标签；名称含 `& < > " '` 等字符时转义正确。
- **US-11** 默认图标按应用名首字符与 slug 派生的确定性 SVG 生成，构建时栅格化为各密度
  mipmap；用户上传 PNG/JPEG（≤2MB）覆盖后使用其内容；删除覆盖后恢复自动图标。
- **US-12** SSL 开启时启动地址为 HTTPS；SSL 关闭时允许 cleartext HTTP（Manifest
  `usesCleartextTraffic=true`），使 IP 直连形态可用。
- **US-13** 壳内 WebView 启用 JavaScript 与 DOM storage；同 host 导航留在 WebView，外部
  `http(s)` 链接交给系统浏览器；返回键优先回退 WebView 历史。

### 分发与弹框

- **US-14** APK 下载地址保持 `{nics.app_url}/api/uploads/mobile/{slug}.apk`，构建成功后
  弹框显示体积、下载链接与下载二维码。
- **US-15** 弹框展示构建状态、失败摘要、壳版本与包名；“重新构建”在 `building` 时禁用。
  `building` 期间每 3 秒轮询 `GET /mobile`，离开弹框停止。
- **US-16** 弹框提供图标预览、上传覆盖与“恢复自动图标”；两个动作都会在 prod 运行时触发重建。
- **US-17** 移除弹框里的 APK 文件上传入口，同时移除 `POST /api/apps/{id}/mobile/apk` 端点。
- **US-18** 非移动栈应用调用 APK 相关端点返回 400；prod 未运行时手动重建返回 409，并说明先发布。

### 运维

- **US-19** 平台启动时把残留的 `building` 行改为 `failed`，摘要“平台重启导致构建中断，请重试”，
  避免弹框永久转圈。
- **US-20** 构建镜像缺失时按需构建；镜像构建失败或 APK 构建超时都只落 `failed`，可在弹框重试。
- **US-21** 删除应用记录时清理该应用的自定义图标与构建工作区（APK 已写进 prod 卷，随应用
  环境处理，不在本规格删除范围内）。

## 4. 已定决策

1. **远程 URL 壳**，不打包离线 H5；壳加载 prod H5 地址。
2. **发布成功后异步构建**；参数未变且产物在则复用。
3. **只保留自动构建的 APK**，移除人工上传入口与后端上传端点。
4. **包名由网关域名反写 + slug 派生**；壳版本独立于应用发布版本。
5. **图标自动生成，弹框可上传覆盖**；上传/恢复默认都触发重建。
6. 失败不影响发布，弹框报错并可重试。
7. **平台统一 keystore**，不按应用生成签名。
8. **只出正式包**（prod 地址），不做 dev 包。
9. 构建使用 **build-tools 直出**（aapt2/d8/apksigner），不用 Gradle/AGP。
10. 构建**串行**，一次一个。
11. prod 未运行时的参数变化**留到下一次发布**再构建。
12. 弹框展示**状态 + 失败原因摘要**。
13. 应用改名、图标变化、启动地址变化都视为壳参数变化，prod 运行时立即重建。
14. APK 继续落 prod 的 `uploads` 卷，下载地址不变。

## 5. 测试决策

### 后端

- `tests/test_shell_params.py`：包名派生（普通域名、连字符、数字开头、空域名）、名称/图标/
  地址/修订号变化会改哈希、发布版本不影响哈希、自动图标 SVG 确定且 XML 转义、Manifest
  cleartext 开关与标签转义。
- `tests/test_apk_build.py`：用假 docker 驱动 `ApkBuilder`：
  - 参数未变 + ready + 文件存在 → 不构建；
  - 参数变化 → 构建并版本 +1，状态 `ready`；
  - 构建失败 → `failed` + 摘要、旧版本保留；
  - 串行与重复入队折叠；
  - 启动恢复把 `building` 改 `failed`。
- `tests/test_mobile_api.py`：扩展 `GET /mobile` 的 `apk`/`icon` 形状；重建端点的 202/400/409；
  图标上传（PNG/JPEG 魔数、超限、非法类型）、读取（自定义优先，否则 SVG）、删除；
  确认旧上传端点已移除。
- 迁移：`tests/test_migrations.py` 验证新迁移可重放。

### 前端

- `frontend/src/components/MobileModal.test.js`：
  - 状态角标 `idle/building/ready/failed` 与失败摘要；
  - `building` 时重建按钮禁用并启动轮询，离开/结束后停止；
  - 下载链接与二维码只在 `ready` 且体积存在时出现；
  - 图标预览、上传、恢复默认调用对应 API；
  - 不再存在 APK 文件上传控件。

### 手工验收

1. 在 `apps/test2`（移动栈）发布 prod，等待构建结束；
2. `docker compose exec backend ls -l /uploads/mobile/test2.apk` 确认产物；
3. `apksigner verify --print-certs`、`aapt2 dump badging` 确认包名/版本/标签/图标；
4. 浏览器打开移动端弹框，确认状态、下载、二维码与图标覆盖；
5. 停 prod 后点重建，确认 409 与提示；再发布，确认自动重建。

不跑全量 pytest（会拆掉宿主上正在跑的 dev 预览栈），只跑上述定向文件；前端定向
`--maxWorkers=4`。

## 6. 明确不做

- iOS / TestFlight / IPA；Apple Developer 账号与 macOS 构建环境不在本期。
- uni-app app-plus 编译、离线包、原生插件、相机/定位/推送/文件选择等原生能力。
- 按应用独立 keystore、证书托管与轮换。
- 人工上传 APK 的能力（前端入口与后端端点都删除）。
- 应用商店/Google Play 上架、自动更新、安装引导。
- 多 ABI / 原生库裁剪（壳是纯 Java + WebView）。
- PWA 安装路径。
- 删除 prod 卷里的 APK（应用删除时只清平台侧临时文件）。

## 7. 补充说明

- **首次构建慢**：构建镜像要下载 Android SDK（platform 34 + build-tools 34.0.0），首次可能
  十几分钟；镜像构建超时 3600s，单次 APK 构建超时 1800s。失败摘要会带 docker 日志尾部。
- **域名是包名的一部分**：换 `ZEROCODE_GATEWAY_DOMAIN` 会换包名，已安装用户无法覆盖升级。
  这是远程壳 + 反写包名的固有代价，运维文档要写明。
- **keystore 必须备份**：`data/apk_keystore/` 丢失后，同包名的新 APK 与已安装版本签名不同，
  只能卸载重装；平台不提供换签名迁移。
- **WebView 证书**：HTTPS 地址证书无效时 WebView 默认报错页；本规格不实现“忽略证书错误”。
- **cleartext**：SSL 关时 Manifest 打开 `usesCleartextTraffic`，仅覆盖 IP 直连形态；SSL 开时
  仍为纯 HTTPS。
- **H5 登录态**：WebView 与应用同域，`/api` 相对路径、Cookie/localStorage 与浏览器一致；
  域登录要求网关与证书正常。
- **APK 体积**：壳约几 MB，仍受 `APK_MAX_BYTES = 200MB` 保护，构建产物超限按失败处理。
