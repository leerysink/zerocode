-- changelog/V20261003_01_user_skill_ai.sql
-- 日期：2026-10-03  用途：技能 AI 生成通道——user_skill 增列 generation_prompt
--                    （spec docs/specs/2026-10-03-skill-ai-generation.md §5.2）
-- 影响：user_skill 增一个可空列；老行恒 NULL 即「非 AI 生成」，无数据回填。
-- 幂等：IF NOT EXISTS 守卫，可在存量库安全重放（对齐 V20260926_01_user_skill.sql 写法）。
-- source_type 增第三个取值 'ai'（列是 VARCHAR(20)，取值变化不需要 DDL）；
-- source_url 对生成型技能填占位哨兵 'ai://generated'，仅满足 NOT NULL，永不发起请求。

ALTER TABLE user_skill ADD COLUMN IF NOT EXISTS generation_prompt TEXT;
