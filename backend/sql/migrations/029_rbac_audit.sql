-- 029_rbac_audit.sql
-- P2 管理端 RBAC 版本谓词与角色/客服档案审计。
-- 只做加法，重复执行不得删除用户、会话或历史审计。

CREATE SCHEMA IF NOT EXISTS auth;

ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS auth.rbac_audits (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'default',
    actor_user_id   BIGINT,
    target_user_id  BIGINT NOT NULL,
    action          VARCHAR(64) NOT NULL,
    before_state    JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_state     JSONB NOT NULL DEFAULT '{}'::jsonb,
    result          VARCHAR(20) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rbac_audits_tenant_created
    ON auth.rbac_audits (tenant_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_rbac_audits_target_created
    ON auth.rbac_audits (tenant_id, target_user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_rbac_audits_actor_created
    ON auth.rbac_audits (tenant_id, actor_user_id, created_at DESC, id DESC);
