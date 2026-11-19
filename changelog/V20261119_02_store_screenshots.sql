-- 商店截图（规格 2026-11-19 §4.3）：DB 只存相对路径列表，文件落 store/releases/<id>/。
ALTER TABLE app_release ADD COLUMN IF NOT EXISTS screenshots JSONB NOT NULL DEFAULT '[]'::jsonb;
