-- changelog/V20261001_01_open_folder_web.sql
-- 日期：2026-10-01  用途：「打开工程目录」默认方式改为内置文件管理器（ADR-0009）
-- 影响：app_config 新增平台级键 fs.open_folder_mode = "web"
-- 幂等：ON CONFLICT DO NOTHING，可在存量库安全重放。
--
-- 为什么是 DO NOTHING 而不是 DO UPDATE：这个键一旦被保存过就无法区分
-- 「用户主动选的 auto/system」与「保存设置时顺带写入的 auto」，
-- 播种强行覆盖等于替存量部署改主意 —— 要换行为请去设置页切一次。
-- 仲裁目标跟 ux_app_config_owner_key（Ticket 003）：owner 缺省 NULL = 平台级键。

INSERT INTO app_config (key, value) VALUES ('fs.open_folder_mode', '"web"')
ON CONFLICT (owner_user_id, key) DO NOTHING;
