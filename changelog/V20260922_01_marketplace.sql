-- changelog/V20260922_01_marketplace.sql
-- 日期：2026-09-22  用途：应用商店 M3-a（轻身份 / 版本包 / 申请审批 / 实例绑定）
-- 影响：actor, app_release, release_application, app_instance_link, app_project

-- actor：轻身份。本期不做账号体系（规格 D8），token 走 cookie，仅用于标识申请人与实例归属，不鉴权。
CREATE TABLE IF NOT EXISTS actor (
    id           BIGSERIAL PRIMARY KEY,
    nickname     VARCHAR(50) NOT NULL,
    token        VARCHAR(64) NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- app_release：不可变版本包。anchor 是有序 [{filename, checksum}]，取自来源应用的 prod 库。
-- app_id 刻意不加 FK：来源应用被删时包必须留在（已装实例的升级线、审批历史都要引用它）。
CREATE TABLE IF NOT EXISTS app_release (
    id              BIGSERIAL PRIMARY KEY,
    app_id          BIGINT NOT NULL,
    slug            VARCHAR(40) NOT NULL,
    version         VARCHAR(40) NOT NULL,
    title           VARCHAR(100) NOT NULL,
    summary         TEXT,
    stack           VARCHAR(20) NOT NULL DEFAULT 'vue3-fastapi',
    anchor          JSONB NOT NULL,
    fingerprint     VARCHAR(64) NOT NULL,
    prev_release_id BIGINT REFERENCES app_release(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'published',
    created_by      BIGINT REFERENCES actor(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (slug, version)
);
CREATE INDEX IF NOT EXISTS ix_app_release_slug ON app_release(slug, id DESC);

CREATE TABLE IF NOT EXISTS release_application (
    id            BIGSERIAL PRIMARY KEY,
    release_id    BIGINT NOT NULL REFERENCES app_release(id),
    applicant_id  BIGINT NOT NULL REFERENCES actor(id),
    want_name     VARCHAR(100),
    target_app_id BIGINT REFERENCES app_project(id),
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    reason        TEXT,
    decided_by    BIGINT REFERENCES actor(id),
    decided_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_application_status ON release_application(status, id DESC);

-- app_instance_link：受管实例 ↔ 包。baseline_* 是 M3-b 做 fork 判定的基线，本一期就要写对。
CREATE TABLE IF NOT EXISTS app_instance_link (
    app_id            BIGINT PRIMARY KEY REFERENCES app_project(id) ON DELETE CASCADE,
    release_id        BIGINT NOT NULL REFERENCES app_release(id),
    channel           VARCHAR(20) NOT NULL DEFAULT 'managed',
    detach_reason     VARCHAR(40),
    baseline_manifest JSONB NOT NULL,
    baseline_hash     VARCHAR(64) NOT NULL,
    applied_fingerprint VARCHAR(64),
    owner_actor       BIGINT REFERENCES actor(id),
    installed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_upgraded_at  TIMESTAMPTZ
);

ALTER TABLE app_project ADD COLUMN IF NOT EXISTS origin VARCHAR(60);

INSERT INTO app_config (key, value) VALUES
    ('store.enabled', 'true'),
    ('store.keep_prev', '1')
ON CONFLICT (key) DO NOTHING;
