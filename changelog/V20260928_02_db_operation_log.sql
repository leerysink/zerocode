-- changelog/V20260928_02_db_operation_log.sql
-- 日期：2026-09-28  用途：应用 DB 操作台审计日志（读+写全量留痕）  影响：db_operation_log
-- 幂等：全部 IF [NOT] EXISTS 守卫，可在存量库安全重放。

CREATE TABLE IF NOT EXISTS db_operation_log (
    id             BIGSERIAL PRIMARY KEY,
    app_id         BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    actor_user_id  BIGINT,
    env            VARCHAR(10) NOT NULL,
    op_type        VARCHAR(20) NOT NULL,
    sql_text       TEXT,
    target_table   VARCHAR(255),
    status         VARCHAR(10) NOT NULL,
    affected       INTEGER,
    error          TEXT,
    duration_ms    INTEGER,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_db_oplog_status CHECK (status IN ('success', 'error'))
);

CREATE INDEX IF NOT EXISTS ix_db_oplog_app_ts ON db_operation_log (app_id, ts DESC);
