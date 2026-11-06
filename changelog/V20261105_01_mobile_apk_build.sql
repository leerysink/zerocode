-- 日期：2026-11-05
-- 用途：Android 远程 URL WebView 壳的构建状态（ADR-0042 / 规格 2026-11-05）
-- 影响：新增 app_mobile_apk（一应用一行）；无行等价 idle
-- 为什么单独建表：状态属于「产物」而不是应用本体，删除应用靠 FK 级联收走；
-- 也避免把壳版本/参数哈希塞进 app_project 这种高频读取的宽表。
CREATE TABLE IF NOT EXISTS app_mobile_apk (
    app_id       BIGINT PRIMARY KEY REFERENCES app_project(id) ON DELETE CASCADE,
    status       VARCHAR(16) NOT NULL DEFAULT 'idle',
    error        TEXT,
    shell_version INTEGER NOT NULL DEFAULT 0,
    params_hash  VARCHAR(64),
    requested_at TIMESTAMPTZ,
    built_at     TIMESTAMPTZ,
    apk_size     BIGINT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
