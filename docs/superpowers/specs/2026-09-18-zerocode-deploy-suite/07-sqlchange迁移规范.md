# zerocode 部署发布套件（v2）— 07 sqlchange 迁移规范

- 日期：2026-09-18
- 关联：00 总览（D6）、02 生成规范（契约第 3 条）、03 Dev 预览、04 Prod 发布、06 数据库供给

## 1. 需求背景

D6 已定：应用的一切数据变更（DDL/DML）收口在应用内 `sqlchange/` 目录，文件命名沿用全局规则 `V<YYYYMMDD>_<两位序号>_<说明>.sql`；**部署到生产时自动执行**。本档定义目录规范、执行器（sqlrunner）、执行时机与失败处理，以及它与平台自身 `changelog/` 体系的严格分离。

## 2. 用户故事

- S-07-1 AI 加"班组字段"：自动产出 `sqlchange/V20260925_01_add_banzu_to_laoliao.sql`；王厂长点发布，SQL 自动在正式库执行，字段就位，旧数据完好。
- S-07-2 某条 SQL 写错了：发布在"执行 SQL"一步停下，明确告诉他哪个文件、哪句错了；修好后重新发布只补跑没执行过的。
- S-07-3 他在预览环境让 AI 反复改表，dev 库怎么折腾都不影响正式库。

## 3. 功能需求

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR7-01 | 目录与命名：应用根 `sqlchange/`，文件 `V<YYYYMMDD>_<两位序号>_<中文或英文说明>.sql`；序号当天内递增，跨天重置 | P0 |
| FR7-02 | 执行器 `zerocode-sqlrunner:1`（平台提供的镜像）：psql 逐文件增量执行，按文件名字典序；每库一张 `sqlchange_history` 记录执行结果；已应用文件跳过 | P0 |
| FR7-03 | dev 时机：dev 栈每次启动先执行迁移（compose migrate 服务）；会话中可用「立即执行」按钮补跑（不重启栈） | P0 |
| FR7-04 | prod 时机：发布流水线步骤 4（04 §4.1），backend/frontend `depends_on migrate: service_completed_successfully`——迁移不过，应用不启动 | P0 |
| FR7-05 | 失败呈现：失败文件名、PG 错误消息、文件内容定位；UI 三处可见（预览空态/发布进度/应用详情迁移记录） | P0 |
| FR7-06 | 篡改检测：`sqlchange_history` 存 checksum（md5）；已应用文件被修改时，执行器告警并拒绝继续（防止历史不可复现），UI 给出恢复指引 | P1 |
| FR7-07 | 迁移记录页：应用详情展示两库各自的执行清单（文件/时间/耗时/结果/错误） | P0 |
| FR7-08 | 「立即执行」补跑入口：dev 环境下对 `<slug>_dev` 执行一次 sqlrunner（one-shot 容器），结果同 FR7-05 呈现 | P0 |
| FR7-09 | 回滚约定：不自动回滚。文件可选包含 `-- ROLLBACK:` 注释段；UI 展示历史时提供"复制回滚语句"按钮（有人工确认后由 AI 或用户在 psql 执行——平台不代执行回滚） | P1 |

## 4. 详细设计

### 4.1 文件规范（对 AI 的硬约束，契约第 3 条）

```sql
-- V20260925_01_add_banzu_to_laoliao.sql
-- 日期：2026-09-25  用途：来料表增加班组字段  影响：lailiao
ALTER TABLE lailiao ADD COLUMN IF NOT EXISTS banzu VARCHAR(50);

-- ROLLBACK:
-- ALTER TABLE lailiao DROP COLUMN IF EXISTS banzu;
```

- 一个文件一个逻辑变更；完整可执行（禁止片段）；禁止依赖执行顺序之外的会话状态。
- AI 写新文件前必须 `list` 目录取下一序号；**禁止修改已存在文件**（文件工具对 sqlchange 已有文件提供整写保护由契约约束 + checksum 检测兜底）。
- 与平台 `changelog/` 的关系：命名规则一致、执行器不同（平台是 Python 分号切分执行器，应用是 psql 文件级执行器）；目录/库/记录表完全独立，互不扫描。

### 4.2 sqlchange_history（每个应用库内的表，sqlrunner 自动建）

```sql
CREATE TABLE IF NOT EXISTS sqlchange_history (
    filename    VARCHAR(200) PRIMARY KEY,
    checksum    VARCHAR(32),
    success     BOOLEAN NOT NULL,
    error       TEXT,
    duration_ms INT,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.3 sqlrunner 执行算法（镜像内 /run.sh）

```
输入：env TARGET_DB/TARGET_USER/TARGET_PASSWORD + /sql 挂载（sqlchange/ 目录）
1 psql 连接 TARGET_DB → 建表 sqlchange_history（IF NOT EXISTS）
2 ls /sql/V*.sql 字典序排序
3 对每个文件：
    history 有记录且 success → 跳过
    history 有记录且 !success → 重新执行（修复后重跑）
    checksum 与已记录不符 → 整体失败退出（exit 3，FR7-06）
    psql -v ON_ERROR_STOP=1 --single-transaction -f <file>   -- 文件级事务
    成功 → INSERT history(success=true, duration)；失败 → INSERT history(success=false, error) 后 exit 2
4 全部完成 exit 0
```

- `--single-transaction` 限制：文件内不可用 `CREATE INDEX CONCURRENTLY`（契约注明"并发建索引需写普通 CREATE INDEX"，小厂场景足够）。
- 文件内容全量交给 psql 解析——**注释/字符串中含 `;` 均无限制**（与平台 changelog 执行器的分号切分限制不同，此处更宽松）。
- 镜像构成：`postgres:16-alpine` + 一个 run.sh（平台构建脚本 `app/deploy/sqlrunner/` 提供，构建进平台安装流程；镜像缺失时自动本地构建）。

### 4.4 执行时机汇总

| 时机 | 触发方 | 目标库 | 失败后果 |
|---|---|---|---|
| dev 栈启动 | dev-compose migrate 服务 | `<slug>_dev` | dev 栈不启动，预览空态显示失败详情（03） |
| 会话中「立即执行」 | UI/API one-shot 容器 | `<slug>_dev` | toast + 详情，不影响运行中 dev 栈 |
| 发布流水线 | prod-compose migrate 服务（04 步骤 4） | `<slug>_prod` | 发布中止于 step=migrate，旧版本继续服务 |
| static 栈 | 无数据库 | — | migrate 服务整体省略 |

### 4.5 历史查询

平台后端用管理员连接读各库 `sqlchange_history` 展示（FR7-07）；不缓存，实时查询（单机规模查询成本可忽略）。

## 5. 接口

| 方法与路径 | 说明 |
|---|---|
| `POST /apps/{id}/sqlchange/apply?env=dev` | FR7-08 立即执行（202，结果走 WS `migrate_result`） |
| `GET /apps/{id}/sqlchange/records?env=` | 两库执行历史 |
| `GET /apps/{id}/sqlchange/files` | 目录文件列表 + 各文件是否已应用 + checksum 状态 |
| `GET /apps/{id}/sqlchange/rollback?file=` | 返回文件内 `-- ROLLBACK:` 段文本（FR7-09） |

WS `/ws/deploy/{app_id}`：`migrate_result`（{env, ok, applied[], failed:{file,error}}）。

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| 语法/权限错误 | exit 2 + history 记录 error；UI 定位到文件与 PG 消息；"发给 AI 修复"一键注入聊天 |
| 已应用文件被改动 | exit 3 篡改告警；指引：用 git/文件历史恢复原内容，或（明确放弃历史一致性时）手动删该库 history 记录（UI 不提供按钮，指引 psql 命令） |
| 文件命名不规范（正则外） | preflight 扫描告警不阻断（psql 仍按字典序执行），UI 标黄提示 AI 修正 |
| 空目录/无新文件 | exit 0，记录 applied=[] |
| sqlrunner 镜像缺失 | 平台启动体检时自动构建；构建失败提示重装 |

## 7. 验收标准

1. AI 产出合法迁移文件 → dev 重启后表结构生效、`sqlchange_history` 有成功记录；重复重启不重复执行。
2. 发布链路：prod 迁移在 backend 启动前完成；故意写坏 SQL → 发布停在 step=migrate、旧版本无感、修复后重新发布仅补跑坏文件。
3. 修改已应用文件 → 执行器 exit 3，UI 显示篡改告警与恢复指引。
4. 「立即执行」在 dev 栈运行中补跑新文件成功，预览内新表立即可用。
5. 平台 changelog/ 与应用 sqlchange/ 互不干扰（各自历史表、执行器、时机独立验证）。
