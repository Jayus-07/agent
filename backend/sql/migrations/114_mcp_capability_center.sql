-- 114_mcp_capability_center.sql — MCP 能力中心数据模型（B0）
-- 目标：外部 MCP Server 的连接配置、工具发现快照、审核状态、Agent 授权
--   全部以数据库为持久化事实源；进程内运行时快照（infra/mcp_registry/store.py）
--   每 15s 轮询本组表派生，写操作后本进程立即刷新，其余 worker 靠轮询。
-- 实体映射（ADR-0007）：
--   MCP Connection        → ai.mcp_connections（+ 分表 mcp_connection_credentials）
--   MCP Tool Snapshot     → ai.mcp_tools（当前态）+ ai.mcp_tool_snapshots（发现历史）
--   Tool Governance       → ai.mcp_tools.governance JSONB（operation/risk/确认/超时等）
--   Agent Tool Grant      → ai.mcp_tool_grants（产品级授权，product_key = 服务端产品身份）
--   Audit Event           → 复用 ai.security_events（ADMIN_ACTION + category='mcp_*'），
--                           不建平行审计体系
--   Runtime Snapshot      → 进程内存快照，不落库；刷新错误记录在 mcp_connections.status
-- 凭据：mcp_connection_credentials.secret_cipher = Fernet 密文（shared/crypto.py），
--   永不回显明文，查询面只暴露 fingerprint/last4。
-- 幂等：IF NOT EXISTS。回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.mcp_tool_grants;
--   DROP TABLE IF EXISTS ai.mcp_tool_snapshots;
--   DROP TABLE IF EXISTS ai.mcp_tools;
--   DROP TABLE IF EXISTS ai.mcp_connection_credentials;
--   DROP TABLE IF EXISTS ai.mcp_connections;

-- ── MCP 连接注册表 ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai.mcp_connections (
    id                 TEXT PRIMARY KEY,          -- 稳定 ID（mcp_conn_<hex>）
    name               TEXT NOT NULL,
    base_url           TEXT NOT NULL,
    transport          TEXT NOT NULL DEFAULT 'streamable_http',
    auth_type          TEXT NOT NULL DEFAULT 'none',   -- none | static_bearer
    network_scope      TEXT NOT NULL DEFAULT 'public', -- public | private（private 才放行内网 URL）
    enabled            BOOLEAN NOT NULL DEFAULT true,
    status             TEXT NOT NULL DEFAULT 'never_connected',
        -- never_connected | connected | error | disabled
    last_error         TEXT NOT NULL DEFAULT '',
    last_synced_at     TIMESTAMPTZ,
    discovery_version  INTEGER NOT NULL DEFAULT 0,     -- 每次成功发现 +1
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    created_by         TEXT NOT NULL DEFAULT '',
    updated_by         TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_mcp_conn_transport CHECK (transport IN ('streamable_http')),
    CONSTRAINT ck_mcp_conn_auth CHECK (auth_type IN ('none', 'static_bearer')),
    CONSTRAINT ck_mcp_conn_scope CHECK (network_scope IN ('public', 'private')),
    CONSTRAINT ck_mcp_conn_status CHECK (
        status IN ('never_connected', 'connected', 'error', 'disabled')),
    CONSTRAINT uq_mcp_conn_name UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS idx_mcp_connections_tenant
    ON ai.mcp_connections(tenant_id);

-- ── 连接凭据（分表，密文存储；照 llm_provider_credentials 范式）────
CREATE TABLE IF NOT EXISTS ai.mcp_connection_credentials (
    connection_id      TEXT PRIMARY KEY
        REFERENCES ai.mcp_connections(id) ON DELETE CASCADE,
    secret_cipher      TEXT NOT NULL,                 -- enc:<fernet>
    secret_fingerprint TEXT NOT NULL DEFAULT '',
    secret_last4       TEXT NOT NULL DEFAULT '',
    secret_version     INTEGER NOT NULL DEFAULT 1,
    updated_by         TEXT NOT NULL DEFAULT '',
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── MCP 工具当前态（发现 + 审核）────────────────────────────
CREATE TABLE IF NOT EXISTS ai.mcp_tools (
    id                BIGSERIAL PRIMARY KEY,
    connection_id     TEXT NOT NULL
        REFERENCES ai.mcp_connections(id) ON DELETE CASCADE,
    tool_name         TEXT NOT NULL,        -- 上游声明的工具名（不可信输入）
    capability_slug   TEXT NOT NULL,        -- 平台 capability 名后缀（mcp.<slug>，全局唯一）
    description       TEXT NOT NULL DEFAULT '',   -- 上游描述（不可信，仅展示；审核者填写可信摘要后生效）
    status            TEXT NOT NULL DEFAULT 'pending',
        -- pending（新发现，默认拒绝执行）| approved | rejected | disabled
        -- （管理员停用，曾是 approved）| retired（上游已消失）
    content_hash      TEXT NOT NULL DEFAULT '',   -- 当前发现内容摘要（desc+inputSchema+outputSchema）
    approved_hash     TEXT NOT NULL DEFAULT '',   -- 审核通过时的摘要；与 content_hash 不一致 = 待重审
    schema_changed    BOOLEAN NOT NULL DEFAULT false,
    governance        JSONB NOT NULL DEFAULT '{}',
        -- 审核时落库的治理配置：operation / risk_level / confirmation_policy /
        -- timeout_ms / max_calls_per_request / retry / dedupe / rate_limit /
        -- circuit_breaker / idempotency_required / required_roles / required_scopes
    examples          JSONB NOT NULL DEFAULT '[]', -- 审核者填写的路由示例（≥2 条才可 routed）
    review_notes      TEXT NOT NULL DEFAULT '',
    reviewed_by       TEXT NOT NULL DEFAULT '',
    reviewed_at       TIMESTAMPTZ,
    discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_version INTEGER NOT NULL DEFAULT 0,
    retired_reason    TEXT NOT NULL DEFAULT '',
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_mcp_tools_status CHECK (
        status IN ('pending', 'approved', 'rejected', 'disabled', 'retired')),
    CONSTRAINT uq_mcp_tools_conn_tool UNIQUE (connection_id, tool_name)
);

-- capability 名是全局命名空间（ToolSpec/manifest 合并），slug 必须全局唯一
CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_tools_slug
    ON ai.mcp_tools(capability_slug);

CREATE INDEX IF NOT EXISTS idx_mcp_tools_tenant
    ON ai.mcp_tools(tenant_id);
CREATE INDEX IF NOT EXISTS idx_mcp_tools_status
    ON ai.mcp_tools(status);

-- ── 工具发现快照历史（版本对比 / 回溯）──────────────────────
CREATE TABLE IF NOT EXISTS ai.mcp_tool_snapshots (
    id                BIGSERIAL PRIMARY KEY,
    tool_id           BIGINT NOT NULL
        REFERENCES ai.mcp_tools(id) ON DELETE CASCADE,
    discovery_version INTEGER NOT NULL,
    content_hash      TEXT NOT NULL DEFAULT '',
    input_schema      JSONB NOT NULL DEFAULT '{}',
    output_schema     JSONB,
    description       TEXT NOT NULL DEFAULT '',
    discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mcp_tool_snapshots_tool
    ON ai.mcp_tool_snapshots(tool_id, discovery_version DESC);

-- ── Agent（产品入口）工具授权 ──────────────────────────────
-- product_key = 服务端可信产品身份（ADR-0002）；domain_hint/工具名不是授权依据。
CREATE TABLE IF NOT EXISTS ai.mcp_tool_grants (
    id          BIGSERIAL PRIMARY KEY,
    tool_id     BIGINT NOT NULL
        REFERENCES ai.mcp_tools(id) ON DELETE CASCADE,
    product_key TEXT NOT NULL,      -- agent | customer_service
    enabled     BOOLEAN NOT NULL DEFAULT true,
    granted_by  TEXT NOT NULL DEFAULT '',
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    CONSTRAINT ck_mcp_grant_product CHECK (
        product_key IN ('agent', 'customer_service')),
    CONSTRAINT uq_mcp_tool_grants UNIQUE (tool_id, product_key, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_mcp_tool_grants_tenant
    ON ai.mcp_tool_grants(tenant_id);
