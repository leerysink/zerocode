-- 商店运营编辑（ADR-0061 §2）：版本条目的描述性元数据可被创始人修改，留痕走行上两列。
-- 刻意不建编辑历史表：当前诉求只是「作者能看出条目被平台改过」，不是逐字段审计。
-- updated_by 无默认值：只有真被编辑过的行才非空，前端据此决定要不要渲染留痕副行。
ALTER TABLE app_release ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE app_release ADD COLUMN IF NOT EXISTS updated_by BIGINT REFERENCES actor(id);
