-- 日期：2026-11-03
-- 用途：移动栈 vue3-springboot-mobile 落地（ADR-0039 / 规格 2026-11-03）
-- 影响：app_project.stack, app_release.stack
-- 为什么必须改：栈键 21 字符，原 VARCHAR(20) 在 INSERT 那一刻就 StringDataRightTruncation
-- （创建应用直接 500），不是显示层截断。宽度取 40，与 slug 列同档，给后续栈键留头。
ALTER TABLE app_project ALTER COLUMN stack TYPE VARCHAR(40);
ALTER TABLE app_release ALTER COLUMN stack TYPE VARCHAR(40);
