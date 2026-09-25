-- V20260925_01_actor_auth.sql
-- 日期：2026-09-25  用途：登录体系第一步——actor 升级为可认证用户（ADR-0003 / spec §5）
-- 影响：actor（加 password_hash / is_founder；nickname 唯一化）
-- 两步走：本文件只加列与建唯一索引（先去重），语义收缩（identify 收编等）在后续版本文件。

ALTER TABLE actor ADD COLUMN IF NOT EXISTS password_hash VARCHAR(100);
ALTER TABLE actor ADD COLUMN IF NOT EXISTS is_founder BOOLEAN NOT NULL DEFAULT FALSE;

-- 存量轻身份可能重名（昵称无约束历史）。老 actor 无口令、永远无法登录，仅为让位
-- UNIQUE 索引做无损去重：保留最早行的昵称，其余追加 #id 后缀。
UPDATE actor
   SET nickname = nickname || '#' || id
 WHERE id NOT IN (SELECT MIN(id) FROM actor GROUP BY nickname);

CREATE UNIQUE INDEX IF NOT EXISTS ux_actor_nickname ON actor(nickname);
