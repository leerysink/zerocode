-- changelog/V20260918_01_deploy_suite.sql
-- 日期：2026-09-18  用途：部署发布套件 M1 —— 应用工程表与部署配置默认值  影响：app_project, app_config

CREATE TABLE IF NOT EXISTS app_project (
    id              BIGSERIAL PRIMARY KEY,
    workspace_id    BIGINT NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    slug            VARCHAR(40)  NOT NULL UNIQUE,
    scale           VARCHAR(10)  NOT NULL,
    stack           VARCHAR(20)  NOT NULL,
    prod_port       INT NOT NULL UNIQUE,
    dev_port        INT NOT NULL UNIQUE,
    status_dev      VARCHAR(20) NOT NULL DEFAULT 'stopped',
    status_prod     VARCHAR(20) NOT NULL DEFAULT 'stopped',
    current_version VARCHAR(40),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO app_config (key, value) VALUES
    ('deploy.apps_root', '""'),
    ('deploy.port_base', '8210'),
    ('deploy.port_max', '8399'),
    ('deploy.keep_versions', '5'),
    ('deploy.health_timeout', '60'),
    ('deploy.auto_stop_dev_on_exit', 'true')
ON CONFLICT (key) DO NOTHING;
