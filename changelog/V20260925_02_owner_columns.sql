-- changelog/V20260925_02_owner_columns.sql
-- 日期：2026-09-25  用途：Ticket 003 宽重构·扩展——全资源表加 owner_user_id 归属维度（列只写不读，查询行为零变化）
-- 影响：workspace, app_project, model_config, app_config
-- 幂等：全部 IF [NOT] EXISTS 守卫，可在存量库安全重放。

-- 归属列：可空，NULL = 存量无主（004 创始人承接后收敛）；app_config 的 NULL = 平台级键
ALTER TABLE workspace    ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES actor(id);
ALTER TABLE app_project  ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES actor(id);
ALTER TABLE model_config ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES actor(id);
ALTER TABLE app_config   ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES actor(id);

-- workspace：root_path 全局唯一 → (owner_user_id, root_path) 联合唯一（两步走：先建新、后删旧）。
-- NULLS NOT DISTINCT：无主（owner IS NULL）存量行之间仍保持路径唯一，跨用户同路径放行。
CREATE UNIQUE INDEX IF NOT EXISTS ux_workspace_owner_root
    ON workspace (owner_user_id, root_path) NULLS NOT DISTINCT;
ALTER TABLE workspace DROP CONSTRAINT IF EXISTS workspace_root_path_key;

-- 模型档案：全局"仅一个默认" → 每用户至多一个默认（部分唯一，只约束 is_default 行）
CREATE UNIQUE INDEX IF NOT EXISTS ux_model_default_per_owner
    ON model_config (owner_user_id) WHERE is_default;
DROP INDEX IF EXISTS uk_model_default;

-- app_config：主键 (key) → (owner_user_id, key)，owner NULL = 平台级（dbcred.%、agent.* 等留 NULL）。
-- 复合主键不能含 NULL，故以 UNIQUE NULLS NOT DISTINCT 索引承载同义（含 NULL 组合亦唯一），
-- 写侧 ON CONFLICT (owner_user_id, key) 以此索引为仲裁目标。
CREATE UNIQUE INDEX IF NOT EXISTS ux_app_config_owner_key
    ON app_config (owner_user_id, key) NULLS NOT DISTINCT;
ALTER TABLE app_config DROP CONSTRAINT IF EXISTS app_config_pkey;
