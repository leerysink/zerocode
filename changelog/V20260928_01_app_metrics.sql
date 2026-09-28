-- changelog/V20260928_01_app_metrics.sql
-- 日期：2026-09-28  用途：应用资源监控 Ticket 001 —— 指标表（实时+历史共用单表，kind 维度区分）  影响：app_metrics
-- 幂等：全部 IF [NOT] EXISTS 守卫，可在存量库安全重放。
-- 设计：一个应用一行一个时间点一个指标；kind 枚举四类（cpu/memory/db_size/workspace_size），
--       本期只写 workspace_size，其余由后续票接入。CPU 存百分比、内存/磁盘存字节。

CREATE TABLE IF NOT EXISTS app_metrics (
    id      BIGSERIAL PRIMARY KEY,
    app_id  BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind    VARCHAR(20) NOT NULL,
    value   NUMERIC NOT NULL,
    CONSTRAINT ck_app_metrics_kind CHECK (kind IN ('cpu', 'memory', 'db_size', 'workspace_size'))
);

-- 读侧主路径：按应用 + 时间倒序取最近点/窗口序列
CREATE INDEX IF NOT EXISTS ix_app_metrics_app_ts ON app_metrics (app_id, ts DESC);
-- 过期清理扫（Ticket 006）：按 ts 全表淘汰保留窗口外的行
CREATE INDEX IF NOT EXISTS ix_app_metrics_ts ON app_metrics (ts);
