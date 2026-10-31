-- 127_cs_canned_responses.sql — 坐席快捷回复话术，个人/团队两级
-- （cs-workstation-v2 P4，agent_memory 库）

-- 个人话术（scope=personal）仅 owner 读写；团队话术（scope=team）全员可读、
-- supervisor 可写。「存为话术」默认落个人，面板两级同列展示。
CREATE TABLE IF NOT EXISTS customer_service.cs_canned_responses (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    scope VARCHAR(16) NOT NULL,
    owner_agent_id VARCHAR(64),
    title VARCHAR(80) NOT NULL,
    content TEXT NOT NULL,
    category VARCHAR(32) NOT NULL DEFAULT '通用',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_canned_scope_enum CHECK (scope IN ('personal', 'team')),
    CONSTRAINT ck_canned_personal_owner CHECK (
        scope <> 'personal' OR owner_agent_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_canned_tenant_scope_owner
    ON customer_service.cs_canned_responses (tenant_id, scope, owner_agent_id);
