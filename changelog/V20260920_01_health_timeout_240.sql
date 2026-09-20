-- 日期：2026-09-20
-- 用途：dev 预览健康等待默认值 60 秒调整为 240 秒（首次冷启动容器内 pip/npm 安装依赖常超 60 秒，误判成功预览为超时 error）
-- 影响：app_config（deploy.health_timeout）

UPDATE app_config SET value = '240' WHERE key = 'deploy.health_timeout';
