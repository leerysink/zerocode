# zerocode 部署发布套件（v2）— 04 Prod 打包发布

- 日期：2026-09-18
- 关联：00 总览（D2/D8）、01 应用模型、03 Dev 预览、05 URL、06 数据库、07 sqlchange

## 1. 需求背景

"一键打包成 docker 发布出去，生成一个访问 URL"是本套件的最终交付动作。发布流水线把"目录里的代码"变成"局域网同事随时可用的正式应用"，且发布失败不影响正在运行的旧版本。

## 2. 用户故事

- S-04-1 王厂长点「发布」，看到进度条一步步走（构建镜像 → 准备数据库 → 执行 SQL → 启动 → 体检），1-3 分钟后界面出现大号网址按钮，一键复制发给同事。
- S-04-2 发布到一半失败了：老版本还在线上跑着，同事无感；他点"查看日志"看到是哪一步、什么原因。
- S-04-3 新版本有 bug，他在发布历史里点"回滚到昨天"，1 分钟内回到旧版（数据库数据不动）。
- S-04-4 电脑重启后打开 zerocode，应用页点「启动」，昨天的正式应用恢复访问。

## 3. 功能需求

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR4-01 | 「发布」一键流水线（幂等可重入）：前置检查 → 构建 prod 镜像 → 供给 prod 库（06）→ 执行 sqlchange（07）→ 启动 prod 栈 → 健康检查 → 记录版本并展示 URL | P0 |
| FR4-02 | 发布进度实时可见：WS 推每一步开始/完成/失败与构建日志尾部；UI 进度条 + 分步清单（成功✓/进行中/失败✗） | P0 |
| FR4-03 | 版本管理：版本号 `b<YYYYMMDDHHmmss>`，镜像 tag `zc-<slug>-frontend/backend:prod-<version>`；保留最近 5 版（`deploy.keep_versions` 可配），更早的自动清理 | P0 |
| FR4-04 | 一键回滚：选历史版本 → 重启 prod 栈到旧 tag；**数据库不回滚**，UI 明确警示"仅回退程序，数据保持当前状态" | P0 |
| FR4-05 | 发布历史列表：时间/版本/状态/耗时/执行的 SQL 文件清单/操作（回滚、查看日志） | P0 |
| FR4-06 | prod 栈日常管理：启动/停止/重启（停止 ≠ 删除，数据在平台 PG 库中不受影响）；重启机器后一键恢复 | P0 |
| FR4-07 | 健康检查：前端 `GET /` 200 + 后端 health 接口 200（static 栈仅前者），最长等待 60s（`deploy.health_timeout` 可配）；失败自动回退上一版本 | P0 |
| FR4-08 | 卸载清理：停止并删除 prod 容器与镜像；删库为 01 FR1-07 的独立确认项 | P1 |
| FR4-09 | 发布时应用被 AI 占用编辑（会话 running）：弹出确认"当前 AI 正在修改代码，发布的可能是不完整版本，是否继续？" | P0 |

## 4. 详细设计

### 4.1 发布流水线（状态机 steps）

```
publish(app) = [
  1 preflight    Docker 可用/端口未占用/app.json 合法/迁移文件名规范扫描（07）
  2 build        docker compose -f deploy/prod-compose.yml build
                 （frontend: node build→nginx；backend: 按栈；tag=prod-<version>）
  3 provision    prod 库/角色存在性检查与供给（06，幂等）
  4 migrate      sqlrunner 跑 sqlchange/*.sql → <slug>_prod（07；失败即中止，旧版本不受影响）
  5 deploy       docker compose up -d（frontend/backend 引用新 tag）
  6 health       前后端健康探测（4.3）
  7 record       deploy_record 落库 + 旧版本镜像清理（保留 5）+ status_prod=running
]
```

- `status_prod`：`stopped | starting | publishing | running | unhealthy | error`（`starting` 为 prod 栈启动/重启的进行态，M2 实现时补入；发布用 `publishing` 另有并发闸语义）。
- 步骤 2-5 期间旧容器继续运行（构建新镜像不 touch 旧容器；up 在 5 才切换）——**发布失败旧版本零感知**（FR4-01/S-04-2）。
- 幂等重入：任意步骤失败后重新点发布，从 preflight 重跑全部（build 有层缓存，代价小）；`publishing` 状态下禁止并发第二次发布。

### 4.2 prod-compose.yml（与 dev 的差异）

| 项 | dev（03） | prod（本档） |
|---|---|---|
| frontend | node dev server + bind-mount | 多阶段构建的 nginx 镜像，静态产物 |
| backend | 语言官方镜像 + 热重载命令 | 应用镜像（依赖已装齐），`restart: unless-stopped` |
| 迁移 | sqlrunner 指向 dev 库 | sqlrunner 指向 prod 库，backend/frontend `depends_on: migrate: service_completed_successfully` |
| 端口 | dev_port（奇） | prod_port（偶），仅 frontend 对外 |
| 环境变量 | .env 的 DEV_* | .env 的 PROD_*（库名/账号/密码由 06 供给时生成并写入） |

### 4.3 健康检查与自动回退

- 探测：平台后端直接 `GET http://localhost:<prod_port>/`；backend health 通过 `docker compose exec backend curl/wget` 或容器内 healthcheck 指令（模板内置 HEALTHCHECK，`docker inspect` 读结果），取最简可靠路径。
- 落地口径（M2 实现）：判定以单 URL 为准——prod_port 只有 frontend nginx 对外，`GET /` 通即联合健康通过；backend 存活经 nginx `/api` 反代间接覆盖，`docker inspect` 的 health 结果另存入 `records.detail.health` 仅供排查不参与判定。
- 60s 内不通过：`up -d` 保持新容器但标记发布失败 → 自动 `up -d` 回退上一版本 tag → UI 显示"新版本体检未通过，已保持上一版本"。
- 首次发布（无上一版本）：保持新容器运行但标 `unhealthy`，URL 仍展示（便于排查）。

### 4.4 回滚（FR4-04）

```
rollback(app, version) = preflight → deploy(tag=prod-<version>) → health → record(kind=rollback)
```

- 仅切换镜像 tag 重启容器；`<slug>_prod` 库与 sqlchange 不动。
- 警示文案固定："回滚只恢复程序版本，数据库结构与数据保持当前状态；若新版本改过表结构，旧程序可能读写异常，请联系 AI 生成兼容处理。"

### 4.5 数据模型 `deploy_record`（DDL 走平台 changelog/）

```sql
CREATE TABLE IF NOT EXISTS deploy_record (
    id          BIGSERIAL PRIMARY KEY,
    app_id      BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    kind        VARCHAR(10) NOT NULL,          -- publish | rollback | start | stop
    version     VARCHAR(40),                   -- b<YYYYMMDDHHmmss>；rollback 时=目标旧版本
    status      VARCHAR(20) NOT NULL,          -- running | success | failed
    step        VARCHAR(20),                   -- 失败/进行中的步骤名
    detail      JSONB,                         -- {images:[], sqlApplied:[], health:{}, durationMs}
    error       TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_deploy_record_app ON deploy_record(app_id, started_at DESC);
```

## 5. 接口

| 方法与路径 | 说明 |
|---|---|
| `POST /apps/{id}/publish` | 触发发布；202 + record_id；进度走 WS |
| `POST /apps/{id}/rollback` | body `{version}`；202 |
| `POST /apps/{id}/prod/start|stop|restart` | 日常管理（幂等） |
| `GET /apps/{id}/prod/status` | 状态/当前版本/URL/容器态 |
| `GET /apps/{id}/records?limit=` | 发布历史 |
| `DELETE /apps/{id}/records/{rid}` | 删除单条历史与其镜像（可选清理项） |

WS `/ws/deploy/{app_id}` 下行（本域）：`publish_step`（{step, state, detail}）、`build_log`（尾随行）、`prod_status`、`publish_done`（{ok, url, version, error}）。

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| Docker 不可用 / 磁盘不足预检 | 中止于 preflight，中文引导（D9） |
| 构建失败（依赖/语法） | 记录 failed + step=build；UI 展示错误尾部 100 行 + "把日志发给 AI 修复"按钮（一键把错误摘要注入聊天输入框） |
| 迁移失败 | failed + step=migrate；07 的失败呈现；旧版本继续服务 |
| 健康检查超时 | 自动回退（4.3）；若回退也失败 → error，展示两个版本的日志入口 |
| 发布中平台崩溃 | 重启后 `publishing` 记录标 failed（step 未知），可重新发布；容器态以 docker 为准核对 |
| 磁盘清理后历史镜像缺失 | 回滚前校验镜像存在，缺失则提示"该版本镜像已清理，无法回滚" |

## 7. 验收标准

1. 小型栈应用从点发布到 URL 可访问 ≤3 分钟（首次，含镜像构建；二次发布 ≤90s）。
2. 发布过程中制造构建失败：旧版本全程可访问，历史里 failed 记录含 step 与日志。
3. 连续发布 7 次：镜像仅保留最近 5 版，回滚到第 6 版可用。
4. 修改代码 + 新增迁移文件后发布：新功能生效、prod 库旧数据保留、`sqlchange_history` 增量记录正确。
5. AI 会话 running 时点发布：出现 FR4-09 确认框。
6. 重启宿主机 → 启动 zerocode → 应用页「启动」→ URL 恢复，数据完整。
