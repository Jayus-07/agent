-- 102_rbac_department_audits.sql
-- 部门主数据创建操作审计；部门创建和审计记录由 API 同事务写入。

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.department_audits (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        VARCHAR(64)  NOT NULL,
    actor_user_id    BIGINT,
    department_code  VARCHAR(50)  NOT NULL,
    department_name  VARCHAR(100) NOT NULL,
    action           VARCHAR(64)  NOT NULL,
    before_state     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    after_state      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    result           VARCHAR(20)  NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_department_audits_tenant_created
    ON auth.department_audits (tenant_id, created_at DESC, id DESC);
