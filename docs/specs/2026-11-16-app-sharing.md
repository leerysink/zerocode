# 应用共享规格说明

日期：2026-11-16
上游决策：[ADR-0049](../adr/0049-app-sharing-single-collaborator-readonly-watch.md)、
[ADR-0050](../adr/0050-publish-approval-owner-approves-and-executes.md)
术语：[CONTEXT.md](../../CONTEXT.md) 的「应用共享 / 共享给我 / 发布申请 / 旁观 / 共享管理」五节。

## 1. 问题陈述

平台的应用归属是单所有者模型：`app_project.owner_user_id` 是唯一归属，列表/详情/预览/文件/
数据库/聊天/发布全部按当前账号过滤，别人的应用一律 404（`app_service.list_apps/get_app`）；
聊天会话也按 `workspace.owner_user_id` 校验（`sessions._require_ws/_require_session`）。结果是
一个应用只能由创建者本人查看、预览、旁观和运维——想把应用交给同事试用、验证或值守，只能
共享账号口令，或由所有者逐次代操作。

本规格补的是「把应用的部分使用权授予其他账号」：所有者从账号列表选人共享；被共享者能看
应用信息、起停 dev 预览、旁观聊天、看监控与发布历史、只读浏览代码与数据库、启停生产栈、
重打 APK；但不能删除应用、不能改文件、不能写库、不能发言，**发布新版本必须经所有者审核**。

## 2. 方案概述

### 2.1 授权模型与数据

- **共享关系**：`app_share(app_id, user_id)` 一行 = 所有者把某个应用授予某个账号。一应用可
  共享给多人；一个账号可被共享多个应用；同一 (app_id, user_id) 唯一。共享**不可转授**。
- **角色只有两个**：`owner`（`app_project.owner_user_id`）与 `collaborator`（`app_share` 命中）。
  所有被共享者权限相同，不逐人配置。
- **选人范围**：全平台已注册账号昵称列表（新端点 `GET /api/users`），只返回 id / nickname /
  is_founder，不返回口令、token 与任何配置。
- **生命周期**：长期有效，无到期时间；所有者可随时撤销，被共享者可主动退出；撤销/退出立即
  生效（含正在进行的 WS 旁观）。撤销后会话与文件不删——重新共享即恢复可见。

新增迁移 `changelog/V20261116_01_app_sharing.sql`：

```sql
CREATE TABLE IF NOT EXISTS app_share (
    id         BIGSERIAL PRIMARY KEY,
    app_id     BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    created_by BIGINT NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (app_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_app_share_user ON app_share (user_id);

CREATE TABLE IF NOT EXISTS app_publish_request (
    id           BIGSERIAL PRIMARY KEY,
    app_id       BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    requester_id BIGINT NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|withdrawn|cancelled
    reason       TEXT,
    decided_by   BIGINT REFERENCES actor(id),
    decided_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_app_publish_request_pending
    ON app_publish_request (app_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS ix_app_publish_request_app
    ON app_publish_request (app_id, created_at DESC);
```

部分唯一索引是「同一应用同时只允许一条待审申请」的唯一兜底，不能只靠端点里的 SELECT 预检
（并发两条申请会同时通过预检）。

### 2.2 权限矩阵

| 能力 | 所有者 | 被共享者 |
| --- | --- | --- |
| 应用列表 / 详情 / 地址投影 | 自己的全部 | 共享给自己的（「共享给我」区块） |
| 删除应用 / 重命名 | ✓ | ✗（403） |
| 共享管理（成员、审批） | ✓ | ✗（入口不可见，端点 403） |
| dev 预览：启停 / 重启 / 状态 / 日志 | ✓ | ✓ |
| 重建 dev 库（`dev/reset`） | ✓ | ✗ |
| 聊天：新建会话 / 发言 / 停止 / 压缩 / 切模型 / 删会话 | ✓ | ✗（WS 上行被拒） |
| 聊天：读历史 / 实时旁观 | ✓ | ✓（只读） |
| 发布新版本 | 直接发布 | 发起申请，所有者批准后执行 |
| 回滚 / 上架商店 / 商店升级 | ✓ | ✗ |
| 发布历史查看 | ✓ | ✓ |
| 删除发布记录 | ✓ | ✗ |
| 资源监控（summary / current / history） | ✓ | ✓ |
| sqlchange 文件与迁移记录查看 | ✓ | ✓ |
| sqlchange apply / scaffold-backfill | ✓ | ✗ |
| 工作台浏览 / 读文件 / 下载 | ✓ | ✓ |
| 保存 / 新建 / 删除 / 重命名文件 | ✓ | ✗ |
| 打开工程目录（系统资源管理器） | ✓ | ✗（只给网页工作台） |
| DB 表结构 / 数据浏览 / 只读查询 | ✓ | ✓（仅 SELECT） |
| DB 写 / DDL / 批量写 | ✓ | ✗ |
| prod 启停 / 重启 / 状态 | ✓ | ✓ |
| 移动端弹框查看 / H5 二维码 / APK 重打 | ✓ | ✓ |
| 小程序预检 / 上传 / 预览码生成 / 凭据绑定 | ✓ | ✗ |
| 移动端图标覆盖 / 重置 | ✓ | ✗ |
| 工作区记忆面板与详情 | ✓ | ✗ |

### 2.3 发布审批

- 被共享者点「申请发布」→ 写入 `app_publish_request(status='pending')`；同一应用已有待审申请
  或 `status_prod='publishing'` 时 409。
- 所有者批准 → **先跑完整发布闸门**（任务互斥 → 状态并发闸 → chat_active 确认 → Docker 预检），
  通过后把申请置 `approved` 并异步执行发布；任一闸门失败则申请保持 `pending`，可重试。
- 批准即执行；驳回必须填理由（`reason` 非空），申请人可撤回。
- 撤销共享 / 主动退出时，**该用户发起的 pending 申请**置 `cancelled`；其他成员的申请不受影响。
  删除应用由外键级联清除共享与申请。
- 批准时按**当时最新代码**发布，不冻结申请时刻快照；批准时若有活跃会话，仍走既有
  `chat_active` 强制确认分支。
- 发布成功后申请保持 `approved`；发布异步失败不回退申请状态，失败事实由发布记录与应用状态呈现。

### 2.4 只读旁观（聊天）

- 会话读取：被共享者可列会话、读详情/消息/变更记录；新建、改名、切模型、删除一律 403。
- WS `/ws/chat/{session_id}`：所有者连接维持现状；被共享者连接进入**旁观模式**，只接收下行事件，
  所有上行消息（发言、答题、确认、停止、压缩）一律拒绝并回 `error`（不落库、不触业务）。
- **旁观连接不得夺走所有者的 emit 槽**：`hub.set_emit` 现在按会话单槽覆盖，旁观必须注册到独立的
  watcher 集合，所有者与旁观者互不影响；旁观者断连只摘自己的登记。
- 待答的提问/确认卡在旁观侧渲染为只读（选项不可点、无输入框、无「自行决定」），显示「等待所有者操作」；
  所有者作答后旁观侧随消息流同步转为已答态。
- 会话状态角标（`GET /sessions/live-status`）的归属集合扩为「自有工作区 + 被共享工作区」，
  被共享者在左栏也能看到运行中/等待操作/完成/失败的状态。
- 撤销共享 / 主动退出时，该应用下该用户的旁观连接被立即关闭（4404），前端提示后回应用页。

### 2.5 接缝（seams）

**判权只留一个入口**，不在每个端点重写 SQL：

- `app_service.get_app_for_user(sf, app_id, user_id) -> (app, role)`：`role ∈ {owner, collaborator}`；
  两者都不是 → 404（延续「不暴露存在性」口径）。所有按 app_id 取应用的 HTTP 端点改用它。
- `app_service.workspace_role(sf, workspace_id, user_id) -> role | None`：文件、会话、附件等按
  workspace 判权的端点用它；`None` → 404。
- 端点级增权：`require_owner(role)` → 403「仅所有者可操作」。被共享者已知应用存在，403 是诚实口径。
- `list_apps` 保持 owner 过滤不变；新增 `list_shared_apps(sf, user_id)`。
- 发布闸门从 `apps.publish` 提取为共用函数，由「所有者直接发布」与「批准发布申请」两处调用——
  两处各写一份必然漂移（同一类问题已有 ADR-0046 的判据单源先例）。
- 创始人既有的「指标可看全部」例外保持原样（`metrics._load_app` 的 founder 分支不动），
  不扩散到其他端点。

### 2.6 只读访问的边界

- **DB 只读**：复用 `db_admin_service.classify_op`，只有 `select` 放行；`SELECT ... INTO` 与
  函数式写（归入 `other`）一并拒绝；批量语句中任一条不是 select → 整批 403。
- **文件只读**：`/fs/tree`、`/fs/ls`、`/fs/download`、`/files/read` 放行；`/files/save` 与
  `/fs/open-folder` 仅所有者。`deploy/` 的既有服务端围栏不变（`app/deploy/guard.is_secret_read`）。
- **记忆**：不进入共享范围，`/api/memories` 仍按 `workspace.owner_user_id` 校验。
- **附件**：读取附件按「附件所属会话 → 工作区」判权，被共享者可读；上传附件仍仅所有者。

## 3. 用户故事

**共享管理（所有者）**

- **S1 共享应用**：所有者打开应用卡片 ☰ →「共享管理」，从账号列表选择一个用户确认共享；成员列表
  立即出现该昵称。重复共享同一用户 → 409；共享给自己 → 400；用户不存在 → 404。
- **S2 撤销共享**：所有者在成员列表点移除；该用户立即从「共享给我」消失，正在进行的旁观连接
  被关闭，其 pending 发布申请变为 `cancelled`。
- **S3 待审角标**：存在 pending 发布申请时，所有者应用卡片右上角显示角标；角标随 5 秒轮询消失于
  批准/驳回/撤回之后。
- **S4 审批发布**：所有者在共享管理弹框看到申请人昵称与申请时间，点「批准」→ 应用进入
  `publishing` 并最终产出新版本；点「驳回」必须填写理由，理由落库并在申请人侧可见。
- **S5 共享不可转授**：被共享者调用任何共享管理端点 → 403，UI 无共享管理入口。

**被共享者**

- **S6 共享给我**：「我的应用」页在自有应用下方出现独立的「共享给我」区块，卡片标注所有者昵称；
  区块为空时整块不渲染；该区块的应用不计入页头统计与「我的应用」徽标。
- **S7 预览**：被共享者可启动/停止/重启 dev 预览、查看预览状态与预览日志、打开/复制预览地址；
  调用重建 dev 库 → 403，卡片与聊天页均无该入口。
- **S8 旁观聊天**：左栏出现「共享给我」分组，列出该应用的全部会话；点开为只读聊天——历史与实时
  运行可见，输入框、发送、停止、压缩、切模型、附件上传均不可用；待答卡片显示「等待所有者操作」。
- **S9 观测与只读工程**：可看资源监控、发布历史、sqlchange 迁移记录；工作台只读（可看可下载，
  Ctrl+S 无效并提示只读）；无「打开工程目录」入口。
- **S10 只读数据库**：DB 操作台可看表结构、表数据、审计记录并执行 SELECT；任何写/DDL/含写语句的
  批量批次被 403 拒绝并给出只读提示。
- **S11 生产与移动端**：可启停/重启 prod、看 prod 状态；可打开移动端弹框查看 H5 地址/二维码/APK
  状态并重打 APK；小程序上传、预览码生成、图标覆盖、凭据绑定不可见且端点 403。
- **S12 申请发布**：共享卡片上的「发布」位置是「申请发布」；点击确认后变为「待审批」，重复申请
  → 409；可撤回。被驳回后卡片显示驳回理由；批准后应用状态随轮询变为 publishing/running。
- **S13 主动退出**：被共享者可在共享给我区块的卡片菜单选择「退出共享」，确认后该应用立即消失。
- **S14 撤销后失效**：共享被撤销后，任何应用/文件/数据库/会话端点对该用户恢复 404；正在打开的
  聊天页提示「共享已撤销」并引导回应用页。

**越权与边界**

- **S15 非共享账号**：未登录 401；登录但未被共享的账号访问该应用任何端点 → 404（不暴露存在性）。
- **S16 所有者动作隔离**：被共享者调用删除、重命名、回滚、上架商店、删除发布记录、sqlchange
  apply、保存文件、写库 → 403；所有者本人不受影响。
- **S17 所有者直接发布**：所有者发布新版本不产生申请记录、不需要审批，行为与现状一致。

**系统一致性**

- **S18 单待审**：同一应用同时只允许一条 pending 申请；并发两条时一条成功、一条 409。
- **S19 审批与发布闸门同源**：批准时若有活跃会话，返回 409 `chat_active`（与直接发布同形状），
  所有者确认后带 `force=true` 重试；Docker 预检失败时申请保持 pending。
- **S20 删除应用级联**：删除应用时共享关系与发布申请一并消失；被共享者列表下一次轮询即不再出现该应用。

## 4. 已定决策

- **D1** 角色与权限：单角色 `collaborator`，所有被共享者权限相同（grill 第 3 轮）。
- **D2** 聊天形态：只读旁观，历史 + 实时（ADR-0049）。
- **D3** 审批流向：被共享者发起、**所有者**批准；所有者自己发布免审（grill 第 1 轮）。
- **D4** 审批执行：批准即执行（grill 第 2 轮）。
- **D5** 审批范围：仅「发布新版本」；回滚、上架商店、小程序上传不在此列（grill 第 4 轮）。
- **D6** 预览权限：启停/重启 + 日志；重建 dev 库仅所有者（grill 第 2 问）。
- **D7** 用户列表：全量昵称列表，只暴露 id / nickname / is_founder（grill 第 5 轮）。
- **D8** 呈现：「我的应用」页内独立「共享给我」区块，不混排（grill 第 6 轮）。
- **D9** 期限：长期有效，所有者可撤销，被共享者可退出（grill 第 7 轮）。
- **D10** 移动端：可看 + APK 重打；小程序上传与图标覆盖仅所有者（grill 第 8 轮）。
- **D11** 入口：卡片 ☰「共享管理」+ 待审角标（grill 第 9 轮）。
- **D12** 附加权限：观测只读、工程文件只读、DB 只读、prod 启停（grill 第 4 问）。
- **D13** 不做通知中心、不做到期时间、不做逐人权限勾选（终审清单默认项）。
- **D14** 同一应用同时只允许一条待审申请（ADR-0050）。
- **D15** 批准按最新代码发布；活跃会话闸门照旧（ADR-0050）。
- **D16** 共享不可转授（ADR-0049）。

## 5. 接口契约

### 5.1 账号与共享

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/users` | 登录用户 | `[{id, nickname, is_founder}]`，按昵称排序；不含口令/token |
| GET | `/api/apps/{app_id}/share` | owner | `{members:[{user_id,nickname,created_at}], pending_request:{id,requester_id,requester_nickname,created_at}\|null}` |
| POST | `/api/apps/{app_id}/share` | owner | `{user_id}` → 201；自共享 400、用户不存在 404、已共享 409 |
| DELETE | `/api/apps/{app_id}/share/{user_id}` | owner | 200；未共享 404；用户不存在 404 |
| POST | `/api/apps/{app_id}/share/leave` | collaborator | 200；owner 调用 404 |

### 5.2 发布申请

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/apps/{app_id}/publish-requests` | collaborator | 201 `{id,status:'pending',created_at}`；已有 pending 或 `publishing` → 409；owner → 403 |
| POST | `/api/apps/{app_id}/publish-requests/{rid}/approve` | owner | `{force?, confirmDestructive?}` → 202；非 pending → 409；闸门失败 → 4xx 且申请保持 pending |
| POST | `/api/apps/{app_id}/publish-requests/{rid}/reject` | owner | `{reason}` 非空 → 200；空理由 400；非 pending 409 |
| DELETE | `/api/apps/{app_id}/publish-requests/{rid}` | requester | 200 撤回；非 pending 409；非本人 403 |

### 5.3 列表与角色字段

- `GET /api/apps`（不变）：每项新增 `role:"owner"` 与 `pending_publish: bool`。
- `GET /api/apps/shared`（新增）：每项与 `list_apps` 同形状，另加 `role:"collaborator"`、
  `owner_nickname`、`pending_publish: bool`、`publish_request:{status,reason,created_at}|null`
  （当前用户最近一条申请，用于展示驳回理由）。
- `GET /api/apps/{app_id}`：新增 `role` 字段；无权限仍 404。
- `GET /api/workspaces`：返回自有 + 被共享的工作区，每项带 `role`；`DELETE /workspaces/{id}` 仍仅 owner。
- `GET /api/apps/metrics/summary`：非创始人 = 自有 + 被共享应用；创始人维持「全部」现状。

### 5.4 只读访问扩展

- 允许被共享者：`GET /apps/{id}/urls`、`dev/{start,stop,restart,status}`、`GET /dev/logs`、
  `GET /apps/{id}/records`、`GET /apps/{id}/sqlchange/{files,records}`、`GET /metrics/*`、
  `prod/{start,stop,restart,status}`、`GET /mobile`、`POST /mobile/apk/rebuild`、`GET /mobile/miniprogram/preview`、
  `GET /fs/{tree,ls,download}`、`GET /files/read`、`GET /sessions*` 只读族、`GET /api/attachments/{id}`、
  `GET /apps/{id}/db/audit`、`GET /apps/{id}/db/{env}/tables`、
  `GET /apps/{id}/db/{env}/tables/{table}/{schema,data}`、
  `POST /apps/{id}/db/{env}/sql{,/batch}`（仅 select）。
- 仅 owner：`DELETE/PATCH /apps/{id}`、`dev/reset`、`sqlchange/apply|scaffold-backfill`、
  `records/{rid}` 删除、`rollback`、`publish`、store 全部写端点、`PUT /files/save`、
  `POST /fs/open-folder`、`PUT/DELETE /mobile/icon`、miniprogram 写端点、memories 全部端点、
  `POST /sessions`、`PATCH/DELETE /sessions/{id}`、`POST /sessions/{id}/attachments`。

### 5.5 WS

- `/ws/chat/{session_id}`：owner → 现状；collaborator → 旁观；其他 → 4404。
- 旁观下行：沿用现有事件类型；`ask_user` / `confirm_request` 渲染为只读卡。
- 旁观上行：一律 `{type:"error", message:"共享旁观为只读"}`，不写库、不改 hub 状态。
- 不变量：旁观连接的建立与断开不得改变 owner 连接的 emit 槽；同会话 owner 仍收到全量事件。

### 5.6 错误口径

- 未登录：401。应用/工作区/会话不可见：404。已可见但无权执行的动作：403「仅所有者可操作」。
- 共享相关冲突：自共享 400、重复共享/重复申请 409、未知用户 404、空驳回理由 400。

## 6. 测试决策

**接缝选择**：判权统一走 `get_app_for_user` / `workspace_role`，测试打在 HTTP 层（REST 是稳定
接口），不逐个 mock service；发布闸门单源意味着批准路径的测试可以替换 `prod.publish` 的副作用，
但闸门判定必须与直接发布共用同一函数。

**后端（pytest）**

- 新增 `tests/test_app_sharing.py`：
  - S1/S2/S13/S15/S18：共享 CRUD、撤销、自退、非成员 404、待审唯一冲突。
  - 权限矩阵参数化：一张 `(method, path, owner_expected, collaborator_expected)` 表覆盖
    S7/S9/S10/S11/S16，防止新端点漏判。
  - S4/S12/S17/S19：申请 → 驳回（理由必填）→ 重新申请 → 批准（stub 发布）→ 状态迁移；
    批准遇 `chat_active` 返回 409 且申请仍 pending；并发两条申请只成一条。
  - S20：删除应用后共享与申请消失。
- `tests/test_ws.py` 扩展：旁观连接收到下行事件、上行被拒、owner 槽不被夺走、撤销后连接关闭。
- `tests/test_db_admin.py` 扩展：被共享者 SELECT 放行；INSERT/UPDATE/DDL/SELECT INTO/混合批次 403。
- `tests/conftest.py`：TRUNCATE 名单追加 `app_share, app_publish_request`（虽有 CASCADE 兜底，
  显式列名与既有风格一致）。

**前端（vitest）**

- `AppsView.test.js`：「共享给我」区块渲染与隐藏、卡片 owner 昵称、共享卡片动作裁剪、
  待审角标、共享管理弹框（成员增删/批准/驳回）、申请发布与驳回理由展示、页头统计不含共享应用。
- `SideBar.test.js`：共享分组只读（无「+」、无重命名/删除菜单），会话可点。
- `ChatView.test.js`：只读模式隐藏输入区/停止/压缩，旁观卡不可交互，撤销提示。
- `WorkspaceFilesView.test.js`：只读模式 Ctrl+S 不发保存请求。
- `DbAdminView.test.js`：只读横幅与非 SELECT 请求的 403 提示。

**完成标准**：上述故事全部可复现通过；`pytest` 全量、`vitest --maxWorkers=4` 全量绿；
权限矩阵表与后端 403/404 口径逐条对上。

## 7. 明确不做

- 多人共聊 / 被共享者发言 / 共享会话归属（ADR-0049）。
- 逐人权限勾选、两级角色、到期时间、通知中心。
- 回滚、上架商店、小程序上传的审批（仍仅所有者）。
- 共享转授、组织/团队成员管理、所有权转移。
- 被共享者使用 Agent 修改代码、上传附件、编辑工作区记忆。
- 移动端图标覆盖、小程序凭据与上传。
- 改变创始人现有的跨账号指标可见例外。

## 8. 补充说明

- **敏感面**：`deploy/` 明文口令由既有服务端围栏挡住；模型配置与密钥按 owner 过滤，旁观不触发
  模型调用，因此不涉及「用谁的额度」；DB 只读查询会看到真实业务数据（含 prod），已由 D12 显式接受。
- **并发**：同一应用同时只允许一条 pending；批准与直接发布共用「任务互斥 + publishing 闸」。
  已批准的发布即使随后共享被撤销也继续执行——它已越过审批节点，撤销只回收访问权。
- **数据保留**：撤销共享只删 `app_share` 行，不删会话、文件或发布记录；重新共享恢复可见。
- **init.sql**：新表随 `changelog/` 自动合成进 `init.sql`（ADR-0014），不手写第二份 schema。
- **术语**：实现与文档一律用「所有者 / 被共享者 / 共享给我 / 发布申请 / 旁观 / 共享管理」，
  「共享人」禁用（CONTEXT.md）。
