-- changelog/V20261101_01_clarify_mode.sql
-- 日期：2026-11-01  用途：需求澄清模式默认值（ADR-0032 / ADR-0033，规格 docs/specs/2026-11-01-agent-clarify-mode.md）
-- 影响：app_config 新增两个平台级键 clarify.enabled / clarify.ask_remind_at
-- 幂等：ON CONFLICT DO NOTHING，可在存量库安全重放。
--
-- 默认值在代码里也有一处兜底（app/agent/clarify.py 的 DEFAULT_ENABLED / DEFAULT_REMIND_AT）：
-- 缺行即开，所以这两行的作用是**让平台侧显式可见、可在设置页被账户覆盖**，
-- 而不是开关成立的前提。
-- 播种同样用 DO NOTHING 而不是 DO UPDATE：一旦被保存过就无法区分「管理员主动下调」与
-- 「保存设置时顺带写入」，强覆盖等于替存量部署改主意。
-- 前缀 clarify.* 已进 USER_PREFIXES（用户级），这里的 owner NULL 行是打底值，用户行覆盖它。
-- 仲裁目标跟 ux_app_config_owner_key（Ticket 003）：owner 缺省 NULL = 平台级键。

INSERT INTO app_config (key, value) VALUES
    ('clarify.enabled', 'true'),
    ('clarify.ask_remind_at', '6')
ON CONFLICT (owner_user_id, key) DO NOTHING;
