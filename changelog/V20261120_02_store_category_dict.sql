-- 商店分类字典（ADR-0061 §4）：分类的名称/顺序/启停归运营，条目上仍只存 key。
-- app_release.category 刻意不改列、不建外键：现存 5 个值的 key 就是中文 label，
-- 改 category_id 要回填存量并动商店筛选与打包链路，而引用完整性在写入处查一次即可。
CREATE TABLE IF NOT EXISTS store_category (
    key      VARCHAR(20) PRIMARY KEY,
    label    VARCHAR(40) NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0,
    enabled  BOOLEAN NOT NULL DEFAULT true
);

-- 种子 = 原平台固定枚举（app/store/release.py 的 STORE_CATEGORIES，规格 2026-11-19 §4.8）。
-- 「其他」是代码层兜底值：未设置或指向已停用分类时回落到它，服务层锁死不可停用与删除。
-- ON CONFLICT DO NOTHING：本文件在存量库上重放安全（init.sql 每次发布都要重跑）。
INSERT INTO store_category (key, label, sort) VALUES
 ('效率工具', '效率工具', 10),
 ('数据管理', '数据管理', 20),
 ('内容展示', '内容展示', 30),
 ('演示样例', '演示样例', 40),
 ('其他', '其他', 90)
ON CONFLICT (key) DO NOTHING;
