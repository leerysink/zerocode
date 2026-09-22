-- V20260920_02_desktop_settings.sql
-- 日期：2026-09-20
-- 用途：为「陌如玉」类 Win11 桌面应用新增桌面配置持久化表 desktop_settings（主题、壁纸、强调色、桌面图标位置、开始菜单固定项等），
--       并写入一份默认配置，保证首次进入即可读取。
-- 影响表：desktop_settings（新建）

CREATE TABLE IF NOT EXISTS desktop_settings (
    key        VARCHAR(64) PRIMARY KEY,
    value      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO desktop_settings (key, value)
VALUES (
    'desktop',
    '{
        "theme": "light",
        "accent": "#0078d4",
        "wallpaper": "bloom",
        "taskbarAlign": "center",
        "iconLayout": {},
        "showDesktopIcons": true
    }'::jsonb
)
ON CONFLICT (key) DO NOTHING;
