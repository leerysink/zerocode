-- 账号治理（规格 2026-11-23 §2 / ADR-0067~0070）：封禁位 + 邀请码 + 配额平台默认。
-- 三件事一次落齐：迁移是线性历史，应用过的文件不再改（同 V20261116_01 的理由）——
-- 治理台与配额闸分属后续票，但表结构得在它们接线前就位，免得回头改已应用的迁移。
-- 全篇幂等（IF [NOT] EXISTS / ON CONFLICT），可在存量库上安全重放（ADR-0014 全量轨）。
-- 本文件禁 `$$` 块与含分号的字符串字面量：两条迁移轨都靠「剥 -- 后按 ; 切」解析语句。

-- 封禁是例外而非常态：banned_at 默认 NULL = 未封。用时间戳而非布尔位，顺手回答
-- 「什么时候封的」，本票因此不需要审计表（ADR-0067 后果：管理员唯一，留痕无意义）。
ALTER TABLE actor ADD COLUMN IF NOT EXISTS banned_at TIMESTAMPTZ;
ALTER TABLE actor ADD COLUMN IF NOT EXISTS banned_reason VARCHAR(200);

-- 邀请码：自助注册关门后平台唯一的入口门禁（ADR-0069）。一码一号，used_at/revoked_at
-- 两个时间戳就是它的全部状态机（未使用 / 已使用 / 已作废），不存枚举列以免三态互相打架。
-- used_by 可空：码用完后账号若被删（本票不做删号，留个安全形态）不该把码的历史一起带走。
CREATE TABLE IF NOT EXISTS invite_code (
    id         BIGSERIAL PRIMARY KEY,
    code       VARCHAR(64) NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES actor(id),
    used_by    BIGINT REFERENCES actor(id),
    used_at    TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_invite_code_created ON invite_code (created_at DESC);

-- 配额平台默认（ADR-0068）：owner NULL = 打底行，账号级覆盖行由管理员在治理台按人写。
-- 额度不住 actor 列——`quota.` 前缀归入 config_service.USER_PREFIXES，读侧走既有的
-- 「平台打底 + 用户覆盖」回落链（get_all），零新机制。
-- 仲裁目标刻意不写（`ON CONFLICT DO NOTHING` 无目标）：app_config 的唯一约束已在
-- V20260925_02 从 (key) 换成 (owner_user_id, key)，带列清单的形态在全量重放轨上会找不到约束。
INSERT INTO app_config (key, value) VALUES
 ('quota.max_apps', '10'),
 ('quota.max_running_stacks', '4')
ON CONFLICT DO NOTHING;
