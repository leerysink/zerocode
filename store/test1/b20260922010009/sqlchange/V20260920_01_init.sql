-- V20260920_01_init.sql
-- 日期：2026-09-20  用途：示例表 demo_notes（AI 可按需求替换或追加新文件）  影响：demo_notes

CREATE TABLE IF NOT EXISTS demo_notes (
    id         BIGSERIAL PRIMARY KEY,
    title      VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO demo_notes (title) VALUES ('欢迎使用 陌如玉') ON CONFLICT DO NOTHING;
