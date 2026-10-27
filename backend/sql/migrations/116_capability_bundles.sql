-- 116_capability_bundles.sql — 能力包与静态能力产品授权（Batch 2，ADR-0011 D5 扩展）
-- 目标：静态 Capability 与动态 mcp.* 工具用同一套"能力包 → 产品授权"模型：
--   - ai.capability_bundles      能力包（成员 = 静态 capability 和/或动态 mcp.*）
--   - ai.capability_bundle_items 包成员（capability 名，两类统一）
--   - ai.product_bundle_grants   产品 → 能力包授权（agent / customer_service）
-- 执行语义：
--   - 动态 mcp.* 仍以 ai.mcp_tool_grants 为执行契约（快照内复核，不变）；
--     bundle 授权是管理面聚合视图，授权 bundle = 批量维护其成员的 grants。
--   - 静态能力的产品上限升级为：ai.product_bundle_grants 有任一行（含默认
--     种子）→ 按授权白名单判定；表为空 → 回退既有硬编码域黑名单（兼容）。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.product_bundle_grants;
--   DROP TABLE IF EXISTS ai.capability_bundle_items;
--   DROP TABLE IF EXISTS ai.capability_bundles;

CREATE TABLE IF NOT EXISTS ai.capability_bundles (
    id          TEXT PRIMARY KEY,              -- cap_bundle_<hex>
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    domain      TEXT NOT NULL DEFAULT 'general', -- general | cs | selection | travel | data | ...
    enabled     BOOLEAN NOT NULL DEFAULT true,
    version     INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL DEFAULT '',
    updated_by  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cap_bundle_name UNIQUE (name)
);

-- 成员：静态 capability 名（capabilities.yaml）与动态 mcp.<slug> 统一存名。
-- 成员存在性由一致性检查对账（不 FK——动态成员随注册表增删，删工具不删历史包）。
CREATE TABLE IF NOT EXISTS ai.capability_bundle_items (
    bundle_id  TEXT NOT NULL REFERENCES ai.capability_bundles(id) ON DELETE CASCADE,
    capability TEXT NOT NULL,
    added_by   TEXT NOT NULL DEFAULT '',
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bundle_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_cap_bundle_items_cap
    ON ai.capability_bundle_items(capability);

-- 产品 → 能力包授权（product_key 语义与 mcp_tool_grants 一致）
CREATE TABLE IF NOT EXISTS ai.product_bundle_grants (
    id          BIGSERIAL PRIMARY KEY,
    bundle_id   TEXT NOT NULL REFERENCES ai.capability_bundles(id) ON DELETE CASCADE,
    product_key TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    granted_by  TEXT NOT NULL DEFAULT '',
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT NOT NULL DEFAULT '',
    revoked_at  TIMESTAMPTZ,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    CONSTRAINT ck_bundle_grant_product CHECK (product_key IN ('agent', 'customer_service')),
    CONSTRAINT uq_bundle_grant UNIQUE (bundle_id, product_key, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_bundle_grants_product
    ON ai.product_bundle_grants(product_key, tenant_id);
