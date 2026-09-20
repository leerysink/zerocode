-- changelog/V20260920_02_deploy_record.sql
-- 日期：2026-09-20  用途：M2 发布历史表与默认网卡配置  影响：deploy_record, app_config
-- kind：publish|rollback|start|stop  status：running|success|failed
-- version：b<YYYYMMDDHHmmss> 回滚记录 version=目标旧版  detail 含镜像与迁移清单

CREATE TABLE IF NOT EXISTS deploy_record (
    id          BIGSERIAL PRIMARY KEY,
    app_id      BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    kind        VARCHAR(10) NOT NULL,
    version     VARCHAR(40),
    status      VARCHAR(20) NOT NULL,
    step        VARCHAR(20),
    detail      JSONB,
    error       TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_deploy_record_app ON deploy_record(app_id, started_at DESC);

INSERT INTO app_config (key, value) VALUES ('deploy.default_nic', '""')
ON CONFLICT (key) DO NOTHING;
