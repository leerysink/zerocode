-- 日期：2026-11-09
-- 用途：微信小程序分发的凭据与任务状态（ADR-0043~0045 / 规格 2026-11-09）
-- 影响：新增 app_mobile_miniprogram（一应用一行）；无行等价 idle
-- 为什么单独建表：与 app_mobile_apk 同因——状态属于产物而非应用本体，删除应用靠 FK 级联收走；
-- 密钥原文不进表，只存 AppID、摘要与上传时间，原文落 data/miniprogram_keys/{slug}/private.key。
CREATE TABLE IF NOT EXISTS app_mobile_miniprogram (
    app_id          BIGINT PRIMARY KEY REFERENCES app_project(id) ON DELETE CASCADE,
    status          VARCHAR(16) NOT NULL DEFAULT 'idle',
    job_kind        VARCHAR(16),
    error           TEXT,
    appid           VARCHAR(64) UNIQUE,
    key_sha256      VARCHAR(64),
    key_uploaded_at TIMESTAMPTZ,
    version         VARCHAR(64),
    uploaded_at     TIMESTAMPTZ,
    preview_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
