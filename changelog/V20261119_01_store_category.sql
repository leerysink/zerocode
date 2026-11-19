-- 商店分类（规格 2026-11-19 §4.8）：平台固定枚举，上架选填，代码层缺省「其他」。
ALTER TABLE app_release ADD COLUMN IF NOT EXISTS category VARCHAR(20);
