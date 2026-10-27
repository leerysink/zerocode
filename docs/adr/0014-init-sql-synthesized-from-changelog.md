# 0014 init.sql 由 changelog 全量合成，重放只补不覆盖

用户要求"部署后第一次执行 init.sql，以后每次发布都把内容全部打包进 init.sql"。我们决定：
`changelog/` 仍是唯一的 SQL 真相源，`init.sql` 是**发布时由脚本按日期顺序合成**的全量产物
（结构段 + 数据段），不手写、不入库、每次发布重新生成；重放语义定为「只补不覆盖」——结构段
沿用既有幂等 DDL（`IF [NOT] EXISTS`），数据段一律 `ON CONFLICT DO NOTHING`。

被否决的方案：手工维护一份全量快照（与 changelog 双轨并存，下次发布忘导出就丢新表）；
数据段 `DO UPDATE`（本地永远为准，演示机上现场产生的对话与应用会被下一次发布冲掉）；
数据段只对空库执行（多一套 `seed_marker` 记账，且以后无法再通过 init.sql 下发新默认配置）。

## 后果

- init.sql 与后端启动时的 `run_migrations` **同源**（都由 `changelog/` 驱动），不是第二份真相。
  但两者在交付包里的命运不同：平台镜像（Dockerfile 由打包生成在交付包的 `backend/Dockerfile`）只
  COPY 了 `backend/requirements.txt`、`backend/app` 与 `frontend/dist`，**不含 `changelog/`**，
  所以 backend 首启时 `run_migrations` 面对不存在的目录会拿空迭代器静默空转
  （不报错也不建表）。因此 `bash apply-init.sh` 是每次发布不可省的**唯一 schema 通道**，
  `schema_migration` 表在演示部署上永远是空的。刻意不把 changelog 烘进镜像：原始迁移
  没有幂等守卫（守卫是合成期补的），在 init.sql 已建好的库上重放会因 `CREATE TABLE` 撞名
  而让 backend 启动失败。代价：忘了跑 apply-init.sh 时，现象是「新表没出现、但日志里一切正常」。
- 现有 changelog 是按「每个文件只执行一次」写的，全量重放会把三类隐患暴露出来，所以合成必须是
  「改写」而不是「拼接」。在真 pgvector 库上连跑两遍实测到前两个缺陷，对应四处规范化：
  1. **非幂等 DDL**（集中在 `V20260917_02_interaction.sql`，如裸 `CREATE TABLE model_config`；
     具体条数随 changelog 增长而变，以产物 footer 的合计为准）
     → 能补守卫的自动补 `IF NOT EXISTS`（一句 `ALTER` 里的多个 `ADD COLUMN` 逐个补，
     省掉 `COLUMN` 关键字的 `ADD foo int` 同样补）。
  2. **无法补守卫的 DDL**（`ADD CONSTRAINT`、`CREATE TYPE`、`RENAME`、`ALTER COLUMN … TYPE`、
     不带 `IF EXISTS` 的 `DROP INDEX/TYPE/VIEW/…`）
     → PG 根本没有 `IF NOT EXISTS` 形态，**fail-closed 拒包**并给出处置建议（包 DO 块先查
     `pg_constraint` / `pg_type`），不留到演示现场才爆。
  3. **仲裁目标失效**：`V20260925_02` 把 `app_config` 的主键 `(key)` 换成
     `(owner_user_id, key) NULLS NOT DISTINCT` 联合唯一索引，于是重放到第二遍时，前面那句
     `ON CONFLICT (key)` 已找不到匹配约束，直接报 `there is no unique or exclusion constraint
     matching the ON CONFLICT specification` → 归一为无目标的 `ON CONFLICT DO NOTHING`（对「任意
     唯一冲突」都跳过，正好就是只补不覆盖）。`ON CONFLICT ON CONSTRAINT <名>` 是同一个坑但
     剔不掉（它必须指名约束），故一律拒包。
  4. **裸 UPDATE 覆盖服务器既有值**：changelog 里的 `UPDATE … SET 默认值` 在增量轨上只生效一次，
     在重放轨上会**每次发布都执行一遍**，把运维在服务器上改过的值静默拍回本地值。这类回退
     没有任何报错，只会在下一次现场预览超时时才暴露（`deploy.*` 不在 `config_service.USER_PREFIXES`
     里，任何登录账号改的都是 owner NULL 的平台行，而 `deploy_cfg` 读的正是这一行）。
     → 按 `_UPDATE_DISPOSITIONS` **显式处置表**逐条登记：能加值守卫的改写成「仅当仍是旧
     默认值时才生效」（抬过一次后 WHERE 再不命中，人工改过的值也不命中）；`WHERE` 本身就自限的
     （如昵称去重：跑过一遍后不再有重名行）保留原句。**表外的一律 fail-closed 拒包**，不做
     猜测式改写；已登记的语句一旦改了形（哪怕只是把 240 改成 300）也重新拒包，逼它重过人工评审。
     判定必须按**语句前缀**而不是子串：列名 `updated_at` 含子串 `UPDATE`，子串匹配会把所有建表语句误杀。
  另外 `V20261008_02` 的 `ALTER COLUMN embedding TYPE vector(1024)` 包上类型判断，避开无谓的
  全表重写与持锁。changelog 原文一律不动（后端启动迁移靠它），规范化只发生在合成产物上。
- 三类缺陷都属于「增量轨永远藏得住、全量重放轨必然暴露」的类型，纯静态审查不足以发现（静态审计
  漏掉了非幂等 DDL，直到真库跑出 `relation "model_config" already exists` 才补上；裸 UPDATE 更是
  连真库连跑两遍也发现不了——它不报错，只是默默覆盖）。因此定下两条规矩：
  1. **打包脚本的改动必须在真 pgvector 库上连跑两遍验证**，不能只靠单测与静态断言。
  2. 重放后的存活性断言必须**含可被覆盖的配置值**（如先把 `deploy.health_timeout` 手工改成
     非默认值，再重放，断言它没被拍回去），不能只数表数与行数。
- 新增表/列只要写进 changelog 就会自动进下一次 init.sql；反之，**绕过 changelog 直接改
  init.sql 的改动会在下次发布丢失**。
- 只覆盖平台库。应用分库与凭据不进 init.sql：口令属 `deploy/.env` 与 `app_config.dbcred.*`
  的既有归属，写进 SQL 文件就撞上"凭据不入仓库"红线。连带后果：预置应用被种子到新机器后
  只有元数据行、没有凭据，而凭据原先只能由 `create_app` 生成——所以 dev 凭据改成首次预览时
  惰性供给（ADR-0015）。「仍走平台既有机制」指的是凭据始终由平台运行期生成并存在平台库里，
  不是指供给入口只有 `create_app` 那一个。
- 剔仲裁目标的代价：被创始人事务「划归」过的 `llm.*` 行（owner 由 NULL 变成账号 id）在重放时会补回
  一条 NULL 属主的平台默认行。实测不会覆盖任何人：`config_service.get_all` 是 `{**glob, **mine}`，
  用户自己的行永远胜出，且创始人事务是 UPDATE 划归而非复制（不会产生重复行）。
- 预置应用的种子行 `owner_user_id` 留 NULL，靠 `register` 的创始人事务被首个注册账号承接；
  所以演示账号不需要写进 SQL，服务器现场注册即可。种子里的 `workspace.id` 靠 `RETURNING … INTO`
  拿，不靠 `root_path` 反查：`V20260925_02` 已把 `root_path` 的全局 UNIQUE 换成
  `(owner_user_id, root_path)` 联合唯一，同路径不同属主的两行是合法的，反查会命中多行。
- 部署根在打包时被**烘死到三处**（compose 的同路径挂载、镜像的 `WORKDIR`、init.sql 里预置应用的
  `root_path`），且 compose 刻意不走 `${DEPLOY_ROOT:-…}` 运行期覆盖：`WORKDIR` 是构建期定下的，
  而 `settings.py` 的 `REPO_ROOT` 由它推出，运行期改 `.env` 只会让挂载点与 `REPO_ROOT` 分家
  （新建应用落进容器可写层，宿主 daemon 找不到那个路径），而且全程不报错。换部署根的唯一
  正确做法是重跑打包（`build-output.bat --deploy-root …`）。
