# 通配域名 HTTPS 网关（gateway）设计规格

## 1. 问题陈述

当前每个生成的应用通过各自的 prod 端口以 `http://localhost:{prod_port}` 直连访问（见 prod.py 的就绪提示），既没有域名形态，也没有 HTTPS。需求是给每个应用一个稳定的域名 `{slug}.zerocode.com`，并在 nginx 侧用**自建通配证书 `*.zerocode.com`** 提供 HTTPS 访问。

痛点：
- 无统一入口——用户要记住每个应用的随机端口号。
- 无 TLS——直连 http，无法演示真实域名访问。
- 应用侧不宜承载证书——通配证书若装进每个应用镜像，轮换要重建全部应用。

术语遵循 [CONTEXT.md](file:///d:/project/zerocode/CONTEXT.md)；关键决策见 ADR-0001、ADR-0002。

## 2. 方案概述

新增一个**平台级中央网关**（OpenResty 容器，仓库顶层 `gateway/` 目录独立编排、人工常驻），它是全站唯一持有 TLS 的地方：

- 监听宿主 `80`（301 跳 https）与 `443`（终止 TLS，用 `*.zerocode.com` 自签通配证书）。
- 收到 `{slug}.zerocode.com` 请求后，lua 取 Host 前缀为 slug，查一张**内存路由表**得到该应用的 prod 宿主端口，反代到 `http://127.0.0.1:{prod_port}`（转发给仍在跑 HTTP 的应用容器）。
- 路由表内容来自**平台导出的 `routes.json`**（`{slug: prod_port}`），以 volume 挂进网关容器；lua 按文件 mtime 判断是否重载。

应用侧模板与存量工程**零改动**：应用容器继续 `listen 80`，不感知 HTTPS。平台后端不管网关启停，只负责在应用 prod 状态变化时重写 routes.json。

## 3. 用户故事（每条可独立验证）

- US1：作为平台使用者，我第一次拉起跑 `gateway/` 里的 `docker compose up -d`，网关容器健康启动，443/80 端口在宿主可连。
- US2：作为平台使用者，我运行仓库内的 make-cert 脚本，`gateway/certs/` 下生成自签 CA 与 `*.zerocode.com` 证书（key + crt + CA），供网关挂载。
- US3：作为使用者，某应用发布并启动 prod 后，我在 hosts 里把 `{slug}.zerocode.com` 指向 127.0.0.1、浏览器信任 CA 后，访问 `https://{slug}.zerocode.com` 能看到该应用页面，`/api` 反代正常。
- US4：作为使用者，我对一个未运行 prod 的 slug 访问域名，网关返回 **404 提示页**（告知去控制台启动应用），而不是 502/空白。
- US5：作为使用者，我访问 `http://{slug}.zerocode.com`（80），被 **301** 跳到对应 `https://` 地址。
- US6：作为平台，某个应用 prod 启动成功（`status_prod → running`）后，routes.json 立即包含 `{slug: prod_port}` 条目。
- US7：作为平台，某应用 prod 停止（`→ stopped`）后被删除后，routes.json 立即**移除**对应条目。
- US8：作为平台，应用改名（rename_app）不影响 routes.json（slug/端口不变，无需重导）。
- US9：作为运维，我手动改 routes.json 里的端口值并更新 mtime，网关在下一个请求（或短缓存周期内）按新表转发，无需重启/reload 容器。
- US10：作为平台使用者，控制台本身仍以 `http://localhost:端口` 直连，不经网关、不要求证书。
- US11：作为代码维护者，我在 `master` 分支开发，本特性不引入数据库表结构变更。

## 4. 已定决策（避免翻案）

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | TLS 终止点 | 中央网关（OpenResty），应用容器不动（ADR-0001） |
| D2 | 运行环境 | 本机 Windows 先行，设计可移植上服务器，配置不写死本机假设 |
| D3 | 本机 DNS | 不自动化，手动改 hosts 验证；服务器阶段换真通配 A 记录 |
| D4 | 路由维护 | OpenResty lua 动态路由，Host 前缀即 slug，免 reload（ADR-0002） |
| D5 | 路由数据源 | 平台导出 `routes.json`（`{slug: prod_port}`），volume 挂载，lua 按 mtime 刷新 |
| D6 | 证书生成 | 仓库内 openssl make-cert 脚本，自签 CA + `*.zerocode.com`，落 `gateway/certs/`（gitignore） |
| D7 | 部署形态 | 独立顶层 `gateway/` 目录，人工 `docker compose up -d` 常驻，平台只喂路由 |
| D8 | 控制台入口 | 不走网关，保持 localhost 直连 |
| D9 | miss 行为 | slug 未命中 → 404 提示页 |
| D10 | 80 端口 | 兼听 80，301 跳 https |
| D11 | 纳入路由条件 | 仅 `status_prod == "running"` 的应用进 routes.json |

## 5. 接缝与测试决策

**最高接缝：routes.json 的导出。** 整个平台侧改动收敛为一个导出函数（拟置 `app/deploy/gateway_routes.py`）：

```
export_routes(session_factory, out_path) -> dict
  # SELECT slug, prod_port FROM app_project WHERE status_prod = 'running'
  # 渲染 {slug: prod_port, ...} 原子写盘（.tmp + replace），内容一致则不落盘（幂等，同 _write_if_changed）
```

现有生产链所有 prod 状态跃迁都经过 [set_status](file:///d:/project/zerocode/app/deploy/prod.py)（`STATUSES = stopped/starting/publishing/running/unhealthy/error`），删除走 [delete_app](file:///d:/project/zerocode/app/services/app_service.py)。导出钩子挂在这些点之后即可覆盖 D5 的四个时机（发布成功晋升 running、prod 启动、prod 停止、删除）。out_path 指向 `gateway/routes.json`（可配置，本机与仓库同目录，服务器可换）。

**测试接缝：**
- Python 单元测试（复用现有 `tests/` pytest 风格）：建 running / stopped 应用行，调 `export_routes`，断言 routes.json 内容随状态变化正确增删；幂等（同内容不重写）；slug 单段无点号约束。
- 网关配置为静态产物，用 `nginx -t`（OpenResty `openresty -t`）在 CI/本地校验语法；lua 路由查询逻辑以纯函数抽出便于单测（给定 Host + routes 表 → 目标端口 / miss）。
- 端到端手测（US3/US4/US5）依赖本机 docker + hosts + 信任 CA，记为验证清单，不进自动化。

**"完成"的样子：** `gateway/` 能起来、证书脚本能产出、routes.json 在四个时机正确增删、hosts+CA 就绪后浏览器能用 `https://{slug}.zerocode.com` 打开一个真实应用。

## 6. 明确不做（本次边界）

- 不做自动化 DNS（本机手动 hosts；服务器解析留待部署阶段）。
- 不做证书自动续期 / Let's Encrypt / ACME——只支持 openssl 自签。
- 不动应用侧模板与存量应用的 nginx 配置、不重建任何应用镜像。
- 不让控制台走网关、不给控制台配 HTTPS。
- 不管网关容器的生命周期自动化（不随平台自动拉起）。
- 不做 routes.json 的鉴权/多租户隔离（内网自签场景）。
- 不做数据库表结构变更（纯文件导出，无新表/新列，故本次无版本化 SQL）。
- 不引入 dev 预览环境的域名路由（只服务 prod）。

## 7. 补充说明

- **通配符一级约束**：`*.zerocode.com` 只匹配一级，slug 必须单段、不含点号；生成应用与商店实例的 slug 形态需保持此约束（现有 `insXXXX`/`lkXXXXXX` 形态满足）。
- **routes.json 契约**：格式即 `{slug: prod_port}` 扁平映射，改格式要 Python 导出侧与 lua 读取侧同步（ADR-0002 已记）。
- **上游地址**：本机 Docker Desktop 下容器访问宿主端口用 `host.docker.internal`；上服务器若同机部署可回退 `127.0.0.1`。此为可移植性唯一需参数化处，写进网关 env 而非硬编码。
- **证书不入库**：`gateway/certs/` 必须进 `.gitignore`，仓库只提交 make-cert 脚本。
- 构建链下一步：`to-tickets` 把本 spec 切成垂直切片（证书脚本片 / 网关容器片 / routes.json 导出片 / 接线片）。
