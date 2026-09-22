# zerocode 应用商店与跨实例数据一致性（设计）

日期：2026-09-22　状态：待评审
前置：M1 应用工作区 / Dev 预览、M2 Prod 发布套件（规格 01~08）、`sqlchange` 迁移规范（规格 07）

## 0. 决策记录（已与需求方确认）

| # | 议题 | 决策 |
| --- | --- | --- |
| D1 | 商店边界 | 同一 zerocode 平台内的市场，不做跨实例中心商店、不做离线包 |
| D2 | 申请后的使用形态 | **独立实例**：每个批准申请建一个新 `app_project`，独立代码目录、独立库、独立端口与容器 |
| D3 | 定制策略 | 允许 fork；检测到差异时**问人**（保留定制=断升级线 / 丢弃定制=回官方版） |
| D4 | 升级时机 | 使用者手动点升级，平台不自动推送迁移 |
| D5 | 新实例库初始化 | **只建结构、空库起步**（包内 sqlchange 有序全量重放），不带发布者任何数据 |
| D6 | 历史形状约束 | **线性历史（前缀关系）+ 破坏性变更两步走**；不兼容即 fork，不提供合并升级 |
| D7 | 影子库克隆手段 | `CREATE DATABASE ... TEMPLATE`（物理克隆，秒级，接受数秒断连窗口） |
| D8 | 身份体系 | 本期**不做账号体系**，用轻身份（昵称 + 设备 cookie）闭环；账号体系单列后续一期 |

## 1. 需求背景与目标

发布者（如「陌如玉」`test1` 的作者）希望把已开发完成的应用推到商店，别人新建应用时可以从商店申请，审批通过后立刻得到一份能用的实例。

难点不在分发，在于**同一份代码被复制成 N 份之后，功能还要继续演进，而每份实例的数据必须互不干扰、且不能被坏迁移打坏**。

目标：
1. 程序版本与数据版本解耦，升级是「换程序 + 前滚 schema」，数据留在各实例自己的库里。
2. 任何一次升级在执行前，必须先在真实数据的克隆上验证通过；失败必须能整体退回升级前状态。
3. 商店包不可变，实例是否可升级可机器判定，不靠人记。

非目标：跨平台同步、多租户行级隔离、自动全员升级、schema 差异自动生成/合并、prod 数据回流 dev。

## 2. 核心模型

### 2.1 两条轴

- **程序轴**：镜像 tag `zc-<slug>-{backend,frontend}:prod-<version>`，`version = b<YYYYMMDDHHmmss>`（M2 已有）。可任意切换、可回滚。
- **数据轴**：某个库内 `sqlchange_history` 中 `success=true` 的**有序文件序列**。只能前滚，永不回滚（`run.sh` 只有 up）。

「回滚一次升级」的完整性只来自程序轴的切换 + 一次数据克隆的丢弃重建，二者必须配对记账（§6.4）。

### 2.2 承重墙：线性历史

> **约束 L**：对任意两个商店版本 A、B（A 早于 B），A 的锚点序列必须是 B 锚点序列的前缀；对任意受管实例，其库内已应用序列必须是所跟版本锚点序列的前缀。

约束 L 成立时，升级退化为集合差：`需补跑 = 目标锚点 − 实例已应用`，无需 diff、无需合并、无需冲突解决。约束 L 被破坏（实例应用过包外文件、或包删改了已应用文件）即判 **fork / detached**，永久退出升级线。

由此派生的作者侧纪律（写进商店上架检查与模板 README，AI 提示词同步）：
1. 已应用的 `V*.sql` **永不修改**（`run.sh` 的 checksum 篡改闸门会拦，但闸门只能保护作者自己的库，保护不了已分发出去的包 —— 所以包必须物理快照，见 §4）。
2. 只做增量：加表、加列、建索引、数据回填。
3. 需要改列类型 / 拆表 / 删列时，**拆成两个版本**：版本 N 加新列 + 代码双写；版本 N+1 迁移数据 + 删旧列。同一版本内不得同时出现「读旧列的代码」与「删旧列的迁移」。

### 2.3 术语

| 术语 | 含义 |
| --- | --- |
| 锚点 anchor | 某商店版本的有序迁移清单 `[{filename, checksum}]`，取自**发布者 prod 库的 `sqlchange_history`** |
| 指纹 fingerprint | 锚点序列化后的 sha256，用于版本比对 |
| 包 release | 不可变目录 `store/<slug>/<version>/`：sqlchange 快照 + 源码快照 + manifest.json |
| 实例 instance | 由安装产生的 `app_project`，`origin = store:<release_id>` |
| 通道 channel | `managed`（跟随版本，可升级）/ `detached`（已 fork，不升级） |

## 3. 四类库与数据归属

| 库 | 数据归属 | 与商店的关系 |
| --- | --- | --- |
| `<slug>_dev` | 发布者试验数据，允许脏 | **无关，永不进包**（锚点不取目录文件列表，正是为了避开这里的未发布迁移） |
| `<slug>_prod` | 发布者正式数据 | 锚点唯一来源 |
| 商店包 | **零业务数据**，只有源码 + 迁移序列 | 不可变 |
| `<instance>_dev` / `<instance>_prod` | 申请人自己 | 从锚点空库重放起步；此后与其他实例、与发布者库无任何耦合 |

数据一致性在这里的定义就是：**代码共享、结构序列共享、数据零共享**。跨实例不存在任何写入路径，因此不需要分布式事务、不需要数据同步链路。

## 4. 商店包布局

```
store/
  test1/
    b20260922010009/
      manifest.json          # version / 来源 app_id / prev_release / fingerprint / anchor / 源码排除表 / 健康检查约定
      sqlchange/             # 锚点文件物理 copy（只含锚点序列内的文件，多余未应用文件不进包）
      src/
        backend/  frontend/  # 源码快照（排除 deploy/、node_modules/、.venv、dist、__pycache__）
```

要点：
- **物理 copy，不引用 `apps/<slug>/`**。作者后续改代码不能波及已上架版本。
- 快照含源码，因为申请后是普通应用，AI 必须能继续改（D3 的 fork 前提）。
- **镜像不共享**：实例首启按自己的 slug 从 `src/` 构建 `zc-<instance>-*:prod-<version>`。共享源镜像 tag 会被 keep-5 清理（`records.versions_for_cleanup` + 按 slug 前缀删镜像）和实例删除误伤，属于跨实例相互破坏的路径，明确禁止。
- 包目录写入后置只读；同 `slug+version` 重复上架直接拒绝（`UNIQUE(slug, version)`）。

## 5. 数据模型（SQL 版本管理）

新增 `changelog/V20260922_01_marketplace.sql`（不改已有版本文件，由 `db.run_migrations` 按序执行）：

```sql
-- actor：轻身份，首次访问留名字，token 走 cookie（本期不鉴权，仅用于标识申请人与实例归属）
CREATE TABLE IF NOT EXISTS actor (
    id          BIGSERIAL PRIMARY KEY,
    nickname    VARCHAR(50)  NOT NULL,
    token       VARCHAR(64)  NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- app_release：商店版本（不可变包）
CREATE TABLE IF NOT EXISTS app_release (
    id              BIGSERIAL PRIMARY KEY,
    app_id          BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    slug            VARCHAR(40) NOT NULL,          -- 来源 slug（展示用冗余）
    version         VARCHAR(40) NOT NULL,          -- 绑定的 prod 程序版本
    title           VARCHAR(100) NOT NULL,
    summary         TEXT,
    anchor          JSONB NOT NULL,                -- [{filename, checksum}] 有序
    fingerprint     VARCHAR(64) NOT NULL,
    prev_release_id BIGINT REFERENCES app_release(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'published',  -- published|withdrawn
    created_by      BIGINT REFERENCES actor(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (slug, version)
);

-- release_application：申请-审批单
CREATE TABLE IF NOT EXISTS release_application (
    id             BIGSERIAL PRIMARY KEY,
    release_id     BIGINT NOT NULL REFERENCES app_release(id),
    applicant_id   BIGINT NOT NULL REFERENCES actor(id),
    want_name      VARCHAR(100),                    -- 申请人填的应用名
    target_app_id  BIGINT REFERENCES app_project(id),   -- 批准后建出的实例
    status         VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|cancelled|failed
    reason         TEXT,                            -- 驳回原因 / 安装失败原因
    decided_by     BIGINT REFERENCES actor(id),
    decided_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- app_instance_link：受管实例与包的绑定关系（fork 判定的基线）
CREATE TABLE IF NOT EXISTS app_instance_link (
    app_id            BIGINT PRIMARY KEY REFERENCES app_project(id) ON DELETE CASCADE,
    release_id        BIGINT NOT NULL REFERENCES app_release(id),
    channel           VARCHAR(20) NOT NULL DEFAULT 'managed',   -- managed|detached
    detach_reason     VARCHAR(40),                              -- code_diverged|sql_diverged|manual_fork|choice_keep_custom
    baseline_manifest JSONB NOT NULL,                           -- 安装/末次升级时的锚点快照
    baseline_hash     VARCHAR(64) NOT NULL,                     -- 源码目录 hash 基线（排除平台生成物）
    applied_fingerprint VARCHAR(64),                            -- 当前所跟版本指纹
    owner_actor       BIGINT REFERENCES actor(id),
    installed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_upgraded_at  TIMESTAMPTZ
);

ALTER TABLE app_project ADD COLUMN IF NOT EXISTS origin VARCHAR(60);   -- 安装来源 store:<release_id>（不记当前所跟版本，那在 link 表）
```

不新建 schema_version 表：**每个库的 `sqlchange_history` 就是它自己的 schema 真相**，跨机器不需要额外对账表。

## 6. 关键流程

### 6.1 上架（发布者）

入口：应用页「发布到商店」，对已有 prod 版本的应用执行；新增 `app/store/release.py`。

闸门（任一不过即拒绝，不产生半成品目录）：
1. 该应用有成功的 prod 发布记录，且 `zc-<slug>-{backend,frontend}:prod-<version>` 镜像在位。
2. 读 `<slug>_prod` 库 `sqlchange_history where success=true order by filename` → 得锚点序列。
3. 逐个文件按 `md5sum` 比对 `apps/<slug>/sqlchange/<f>` 与 history 中 checksum：不一致 → 拒（作者改了历史文件，违反纪律 1）。
4. 锚点非空（至少一个 `V*_init.sql`）；`apps/<slug>/sqlchange/` 里未应用的文件不进包，UI 明示「N 个文件未被 prod 应用，已排除」。
5. 线性校验：新包 `prev_release_id` 指向该 slug 最新已发布版本，且其 anchor 必须是新 anchor 的前缀；否则提示「版本序列分叉，不能上架，请改为发布新应用」。
6. 源码快照排除表命中即跳过（`deploy/`、`node_modules/`、`.venv`、`dist`、`__pycache__`），**`deploy/.env` 及任何凭据必须排除**。

落库：写 `store/...`（临时目录 → 原子 rename）+ `app_release` 行 + `deploy_record(kind='release')` 记账。

### 6.2 申请与审批（轻身份）

- 首次使用商店的用户，前端弹「留下你的名字」→ `POST /api/store/identify`，建 `actor` 行并下发 `zc_actor` cookie（httpOnly、SameSite=Lax）。设备标识即 cookie 本身，不采集任何硬件信息。
- 商店列表 → 点「申请使用」→ 填应用名 → `release_application(pending)`。
- 发布者在「我的发布」审批：通过 → 触发安装（202 + WS 进度）；驳回 → 填原因。
- 本期无鉴权：任何人可审批自己发布的版本，他人实例只读不可动（跨实例写操作按 `owner_actor` 与 `app_project.origin` 收口，越权返 403）。这是 D8 的边界，账号体系一期再补。

### 6.3 安装（审批通过后自动执行，`app/store/install.py`）

流水线六步（复用 M1/M2 现有函数，逐步 `_step` 事件 + `deploy_record(kind='install')`）：
1. 建 `app_project`：slug 由 `slugs.suggest_slug(f"{原slug}短前缀", taken)` 生成、`origin`、端口 `ports.allocate_pair`、写 `apps/<newslug>/`（从包 `src/` copy + 平台重生成 `app.json`，新 slug / 新端口 / 新库名 / 新健康检查）。
2. 建工作区关联 + 端口登记（现有 `app_service.create` 路径）。
3. `provision_env(sf, newslug, "dev")` + `provision_env(sf, newslug, "prod")` —— 一应用两库两角色，与现有规格 06 完全一致。
4. **基线重放**：把包内 `sqlchange/` 作为迁移源挂进 `sqlrun.run_sqlchange`，空库全量按序执行（`run.sh` 语义天然是「跳过已应用」，空库即全跑），dev、prod 各一次，凭据各自传入。零新增迁移代码。
5. `docker compose build` + `up -d`（实例自己的镜像 tag）→ `wait_prod_health`。
6. 写 `app_instance_link(channel='managed', baseline_manifest=anchor, applied_fingerprint=fingerprint, baseline_hash=当前源码 hash)`；回填 `release_application.target_app_id/status=approved`；返回独立访问 URL。

失败处理：第 1~4 步失败 → 建出的库与目录回滚（`provision.drop_env` 已有、目录删除、`app_project` 行删），申请单标 `failed` + 原因，日志入口在 `deploy_record`；第 5 步失败 → 保留库与代码（标 `error`，允许「重试启动」），**不删数据**。

### 6.4 升级（使用者手动点，`app/store/upgrade.py`）

```
① 前缀校验（纯 SQL，不起容器）
   实例库 history(success) 序列  vs  目标 anchor 序列
   · 实例存在包外文件               → detached 判定，走 §6.5 问人
   · 同 filename 但 checksum 不同   → 同上（违反纪律 1）
   · 非前缀（目标缺实例已有文件）   → 同上
   · diff = anchor − history        → 空则「已是最新」
② 加锁：pg_advisory_lock(hashtext('zc-mig:' || slug))，防重复请求把同一文件跑两遍
③ 影子克隆（D7）：
   a. pg_terminate_backend(源 prod 库活动连接)          ← 数秒断连窗口，prod 会 502/自动重连
   b. CREATE DATABASE <slug>_shadow TEMPLATE <slug>_prod
   c. REVOKE ALL ON DATABASE <slug>_shadow FROM PUBLIC；随后只把 CONNECT/TEMP 授给
      实例自己的 prod 角色（见下），其余角色一律连不上
④ 影子预演：run_sqlchange(app_dir=store/<slug>/<version>, cred=<实例 prod 凭据>)
   **必须以实例 prod 角色的同一凭据执行，不得用平台超级用户**：TEMPLATE 克隆只换库的
   owner，库内对象 owner 仍是 `<slug>_prod` 角色（迁移本就是它跑的），故给它 CONNECT/TEMP 后，
   预演的权限路径与⑥的真实迁移**逐字节一致**；用超级用户会假绿（缺 schema 权限、缺扩展创建权、
   行级权限差异这类失败在预演里看不见）。`run_sqlchange` 现签名已支持（app_dir 即迁移源）。
   断言：退出码 0 + 迁移前后每张 public 表 count(*) 不下降（迁移文件内显式声明允许的删除，
   走注释标记 `-- zc:allow-row-deletion <table>` 白名单）+ history 成功集 == 目标 anchor。
   **不在影子库上另起临时容器**：目标镜像与目标 schema 的组合已被「该版本在发布者 prod 上
   成功发布 + 体检通过」真实验证（§6.1 闸门 1），预演专责的是另一半风险——别人的数据形态
   会不会让同一条迁移失败。临时容器要抢端口、要造 env，收益重复，砍掉。
⑤ 影子改名留作回滚点：<slug>_shadow → <slug>_prev_<target_version>（不 DROP）
⑥ 真实例 prod 库跑同一批 diff（同包、同顺序、同代码镜像；prod 容器存活期间迁移，与 M2 现有 publish 第 4 步同形态）
⑦ 程序切换：ensure_deploy_files(version=<目标版本>) → compose up -d → wait_prod_health
⑧ 成功：更新 link.applied_fingerprint/baseline_manifest/baseline_hash/last_upgraded_at；
   deploy_record(kind='upgrade', detail={diff, 断言结果, prev_db, image})；
   keep-1 清理旧 <slug>_prev_*（与镜像 keep-5 策略对齐，不一次留多份库占盘）
⑨ 失败分两类，处置完全不同：
   ⑥ 真实迁移失败 → 单文件 --single-transaction 原子，库停在最后一个成功文件，程序仍在跑旧
      版本 → **不动库**：中止、prev 保留、记录 failed。
   ⑦ 体检失败 → 沿用 M2 现路：程序回退旧 tag，数据库保持已前进（不回滚）——约束 L + 两步走
      纪律保证新 schema 向后兼容，旧代码可继续在前进后的库上服务。
      prev 库此时不丢弃，作为「人工数据复原」入口挂在升级记录上：仅当确认旧代码与新 schema
      不兼容时出人手，动作 = terminate → DROP <slug>_prod → ALTER DATABASE <prev> RENAME TO
      <slug>_prod → ALTER DATABASE ... OWNER TO <slug>_prod → 重授 CONNECT/TEMP → 旧 tag up -d。
      **不做自动换名回滚**：跟 owner 相关的库替换一旦做错就是真数据事故，宁可要人确认。
```

一次克隆同时买到「预演」和「回滚点」，这是不做常驻灰度环境的理由：不新增 env（`ENVS = ("dev","prod")` 不改）、不新增端口、不新增 compose 栈、不新增角色，磁盘代价 = 一个库体积。

**升级只作用于 `<slug>_prod`**，实例的 dev 库不动（开发者可继续在 dev 试）；下次他启动预览时 diff 会一并补跑 dev，与 M1 现有语义一致。

### 6.5 fork 判定与「问人」（D3）

判定信号，两个都算：
- **代码分叉**：实例源码目录 hash（同一排除表，`app/store/baseline.py`）≠ `baseline_hash`。
- **SQL 分叉**：升级前的前缀校验不通过。

受管实例在应用启动/卡片刷新时做代码 hash 比对，不一致即标 `channel='detached', detach_reason='code_diverged'` 并在 UI 弹选择：
- **保留我的定制** → 永久 `detached`，升级按钮置灰，商店里标「已定制」；
- **丢弃定制，回到官方版 vN** → 用包 `src/` 覆盖代码目录（保留库），重置 `baseline_*`，回 `managed`。

不提供第三种「把我的迁移并进官方序列」——约束 L 的简单性正是这套方案能落地的原因。

### 6.6 下架与删除

- 下架（`status='withdrawn'`）：商店列表不再出现、不再接受新申请；**已安装实例不受任何影响**（包目录仍在，升级线保留）。
- 删除实例：走现有应用删除路径（`app_project` 级联 + `provision.drop_env` 两库 + 按**实例自己的** slug 前缀删镜像），顺带清理该实例的 `<slug>_prev_*`。

## 7. 一致性闸门一览与失败矩阵

| 风险 | 拦在哪 |
| --- | --- |
| 作者改了历史迁移文件 | 上架比对 history checksum 拒（§6.1-3）；实例侧升级前缀校验拒（§6.4-①）；`run.sh` TAMPER exit 3 兜底 |
| dev 未发布迁移混进包 | 锚点取自 prod history，不取目录列表 |
| 上架版本从未真实跑过 | 闸门要求 prod 有该版本成功发布记录 + 镜像在服 |
| 升级把库弄坏 | 影子预演（同凭据同权限路径）挡住坏迁移；⑥失败库未动；⑦失败程序可退、prev 可人工整体复原 |
| 重复点击并发迁移 | `pg_advisory_lock` per slug |
| 版本序列分叉还想合并 | 约束 L 判定 → detached，不自动合 |
| 包被后续发布覆盖 | 物理快照 + `UNIQUE(slug, version)` + 目录只读 |
| keep-5 删掉正在用的镜像 | 实例镜像 tag 各自独立，不共享（§4） |
| 凭据随包外泄 | 源码快照强制排除 `deploy/`（含 `.env`），上架扫描到即拒 |
| 平台崩溃留半程 | 启动 `reconcile` 扩展：`running` 的 install/upgrade 记录标 `interrupted`（复用 `records.mark_interrupted` 的 kind 白名单扩列）；影子库/prev 库残留按 link 对账清理 |
| 实例 schema 与所跟版本对不上但没人动过 | 启动对账比对 history 与 `applied_fingerprint` → 标 `schema_drift`，UI 亮黄条 |

## 8. API（前缀 `/api`，WS 复用 `/ws/deploy/{app_id}`）

```
POST /api/store/identify                 {nickname} -> {actor_id, nickname}（Set-Cookie: zc_actor）
GET  /api/store/releases                 商店列表（published，含 latest 版本与可升级标记）
GET  /api/store/releases/{id}            详情（版本历史、锚点文件数、说明）
POST /api/store/applications             {release_id, want_name} -> 201 申请单
GET  /api/store/applications?box=mine|review    我的申请 / 待我审批
POST /api/store/applications/{id}/approve   -> 202 安装中（进度走 deploy WS）
POST /api/store/applications/{id}/reject     {reason}
POST /api/apps/{id}/release              {title, summary} -> 201 上架新版本
GET  /api/apps/{id}/store-link           通道/所跟版本/可升级版本/分叉原因
POST /api/apps/{id}/store/upgrade        {to_release_id} -> 202（进度走 deploy WS）
POST /api/apps/{id}/store/fork           {choice: keep_custom|reset_to_official}
```

写操作全部经 `_reject_if_task_running` 与既有 chat 闸门（发布/安装/升级期间 AI 不得改该应用代码），沿用 409 语义。

## 9. 前端

- `frontend/src/views/StoreView.vue`（路由 `/store`，SideBar 加「商店」入口）：应用卡片流（名称/作者/简介/版本/已安装标记）+「申请使用」。
- `MyReleases` 抽屉（`components/ReleaseDrawer.vue`）：上架表单、申请审批列表、通过时展示安装进度日志（复用 `DeployLogPanel`）。
- `AppsView.vue` 卡片：商店实例角标（「来自商店 vN」/「已定制」）、有新版时显示「升级」按钮 → 复用 `PublishModal` 的进度/失败弹层形态；断连窗口与预演耗时写进提示文案（「升级中会有几秒不可访问」）。
- 轻身份：首次进入商店的昵称弹窗 `ActorNameModal.vue`；不引入登录页。

## 10. 与现有代码的接缝

复用，不改语义：`provision.provision_env/drop_env/read_cred`、`sqlrun.run_sqlchange`（迁移源目录参数化已是现成的）、`compose.ensure_deploy_files`、`ports.allocate_pair`、`records.begin/set_step/finish/versions_for_cleanup`、`prod.wait_prod_health`、`hub` 事件流、`slugs`、`guard`（`store/` 加入平台保留区，AI 不可写）。

新增：`app/store/{release,install,upgrade,baseline,identity}.py`、`app/api/store.py`、`app/deploy/shadow.py`（克隆/改名/丢弃三个动作）、`changelog/V20260922_01_marketplace.sql`、模板 `README.md` 与 AI 提示词补「迁移只追加、破坏性变更两步走」纪律。

不改动：`ENVS`、`run.sh` 执行算法、`sqlchange_history` 结构、M2 的七步发布语义。

## 11. 资源预算与预期耗时

- 端口：池 8210-8399 共约 95 对，商店实例与自建应用同池；设置页 `deploy.port_base/port_max` 可扩。默认提示：实例数 > 40 时建议扩池。
- 磁盘：每实例一个 prod 容器 + 一个 prev 回滚库（keep-1）。粗估 10 个实例、单库 100MB → 约 2GB 级，本地盘可忽略。
- 耗时（单实例升级，百 MB 库）：前缀校验 <100ms → 克隆 1~3s（**prod 断连窗口**）→ 预演迁移 数秒~十几秒 → 真实迁移 数秒 → 程序切换+体检 10~60s。UI 按此给进度与预期。

## 12. 测试策略（TDD）

`tests/test_store_release.py`：锚点取自 prod history（dev 有额外文件时不进包）；改历史文件拒上架；未成功发布过拒上架；前缀分叉拒上架；包排除 `deploy/.env`。
`tests/test_store_install.py`：空库重放后 `sqlchange_history` == 锚点；两库两角色权限隔离（实例角色连不上他人库）；第 4 步失败回滚无残留；`app.json` 重生成字段正确。
`tests/test_store_upgrade.py`：diff 计算五种输入（新增 / 已是最新 / 包外文件 / 同文件名 checksum 不同 / 非前缀）；
预演保真：同一份迁移在影子库以实例 prod 凭据能过、把 public 权限回收后真能被挡住（证明预演不是走过场）；
⑥失败时真实库未被动过；⑦失败时程序回退且 prev 库在位；人工复原走通 DROP→RENAME→OWNER→GRANT 全链路；
并发第二次请求被 advisory lock 挡住。
`tests/test_store_fork.py`：代码 hash 变更 → `detached`；`reset_to_official` 回 `managed` 且库保留。
Docker/PG 相关用例沿用现有 `pytest.mark.skipif(sqlrun.docker_available())` 纪律与清理约定。
前端：`StoreView.test.js`、`AppsView.test.js` 增商店角标与升级入口分支。

## 13. 分期

- **M3-a（商店闭环）**：轻身份 + 上架 + 申请审批 + 安装（§6.1~6.3）。交付后可申请到能用的实例，尚不含升级。
- **M3-b（一致性与升级）**：影子克隆/预演/回滚点 + 手动升级 + fork 检测 + 启动对账扩展（§6.4~6.6、§7）。
- **M4（账号体系）**：`actor` → `user`（登录、密码、权限），商店与实例列表按账号隔离；本期的越权 403 收口届时替换为真鉴权。

M3-a 与 M3-b 各出一份实施计划（`docs/superpowers/plans/`），不合成一份：M3-a 交付即可用（申请→装好→访问），
M3-b 的一致性闸门需要独立评审与独立回归（它动的是真库）。

## 14. 明确不做

跨平台/跨机器的中心商店与包下载协议、行级多租户共享库、自动强制全员升级、Alembic 式 schema 自动 diff、双写/数据同步链路、prod 数据导入 dev（脱敏另议）、常驻 staging 第四套环境。
