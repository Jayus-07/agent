-- 企业扫码登录：外部身份只作登录凭据，本地 auth.users 仍是权限事实源。
BEGIN;

CREATE TABLE IF NOT EXISTS auth.sso_provider_configs (
    tenant_id            VARCHAR(64)  NOT NULL,
    provider              VARCHAR(16)  NOT NULL
        CHECK (provider IN ('feishu', 'wecom', 'dingtalk')),
    client_id             VARCHAR(255) NOT NULL,
    client_secret_cipher  TEXT         NOT NULL,
    external_org_id       VARCHAR(255) NOT NULL,
    agent_id              VARCHAR(128),
    redirect_uri          VARCHAR(2048) NOT NULL,
    enabled               BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider),
    CHECK (length(client_id) > 0),
    CHECK (length(client_secret_cipher) > 0),
    CHECK (length(external_org_id) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_users_id_tenant
    ON auth.users (id, tenant_id);

CREATE TABLE IF NOT EXISTS auth.external_identities (
    id                   BIGSERIAL PRIMARY KEY,
    tenant_id            VARCHAR(64)  NOT NULL,
    provider              VARCHAR(16)  NOT NULL
        CHECK (provider IN ('feishu', 'wecom', 'dingtalk')),
    external_org_id       VARCHAR(255) NOT NULL,
    external_subject_id   VARCHAR(255) NOT NULL,
    local_user_id         BIGINT       NOT NULL,
    created_by            VARCHAR(128) NOT NULL DEFAULT '',
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (length(external_org_id) > 0),
    CHECK (length(external_subject_id) > 0),
    FOREIGN KEY (local_user_id, tenant_id)
        REFERENCES auth.users(id, tenant_id) ON DELETE CASCADE,
    UNIQUE (tenant_id, provider, external_org_id, external_subject_id),
    UNIQUE (tenant_id, provider, local_user_id)
);

CREATE INDEX IF NOT EXISTS idx_auth_external_identities_user
    ON auth.external_identities (tenant_id, local_user_id);

COMMENT ON TABLE auth.sso_provider_configs IS
    '按本地租户配置的扫码登录客户端；client_secret_cipher 必须由 SECRETS_ENCRYPTION_KEY 加密';
COMMENT ON TABLE auth.external_identities IS
    '外部企业身份到本地 auth.users 的显式绑定；登录不按手机号自动关联或创建账号';

COMMIT;
