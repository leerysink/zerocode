-- V20260920_03_desktop_memos.sql
-- 日期：2026-09-20
-- 用途：为桌面「记事本」应用新增便签数据表 desktop_memos（标题 + 正文 + 置顶标记），
--       并把示例表 demo_notes 中的历史数据一次性迁移进来，保证旧数据不丢。
-- 影响表：desktop_memos（新建）、demo_notes（只读取，不修改结构）

CREATE TABLE IF NOT EXISTS desktop_memos (
    id         BIGSERIAL PRIMARY KEY,
    title      VARCHAR(200) NOT NULL DEFAULT '未命名便签',
    content    TEXT         NOT NULL DEFAULT '',
    pinned     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_desktop_memos_pinned ON desktop_memos (pinned DESC, updated_at DESC);

-- 目标列必须与 SELECT 表达式一一对应：FALSE 对应 pinned。缺 pinned 列会报
-- 「INSERT has more expressions than target columns」，sqlrunner 退出码 2，dev 栈迁移闸门失败不启动。
INSERT INTO desktop_memos (title, content, pinned, created_at, updated_at)
SELECT d.title, '', FALSE, d.created_at, d.created_at
FROM demo_notes d
WHERE NOT EXISTS (SELECT 1 FROM desktop_memos m WHERE m.title = d.title);
