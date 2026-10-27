-- 117_governance_releases.sql — 治理配置发布/回滚（Batch 5-2，决策 #08/#14 收口）
-- 目标：治理配置（连接声明 + 能力包 + 授权关系）具备独立的「草稿 → 发布 →
-- 回滚」版本指针，与代码部署（发布记录页的系统 Gate）严格区分。
-- 语义（ADR-0011 扩展；V2 发布记录页第一档）：
--   - 快照 payload 与 /admin/mcp/export 同构（连接声明 + bundle + 授权），
--     **绝不含凭据**（D7 红线：凭据密文/fingerprint/last4/URL 内嵌形态）；
--   - 发布 = 确认某草稿为生效版本（发布记录可追溯，不改变配置本身）；
--   - 差异预览 = 任一版本快照 vs 当前声明的结构化 diff；
--   - 回滚 = 按快照恢复声明（连接声明字段/bundle/成员/授权行），危险写操作
--     走 run_governance_mutation 幂等 + 审计；不恢复凭据（目标环境重配）；
--   - 导入（POST /admin/mcp/import）= 从导出包重建声明，连接一律 disabled
--     且不带凭据、不建授权行——需目标环境重新配置并重新审核授权。
-- 回滚（按逆序 DROP）：
--   DROP TABLE IF EXISTS ai.governance_releases;

CREATE TABLE IF NOT EXISTS ai.governance_releases (
    id          TEXT PRIMARY KEY,                 -- gov_rel_<hex>
    version     INTEGER NOT NULL,                 -- 租户内递增
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    status      TEXT NOT NULL DEFAULT 'draft',
        -- draft（快照已建，未发布）| published（生效版本）| rolled_back（已被回滚操作消费）
    description TEXT NOT NULL DEFAULT '',
    payload     JSONB NOT NULL,                   -- 与 export 同构：connections/capability_bundles
    created_by  TEXT NOT NULL DEFAULT '',
    published_by TEXT NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_gov_rel_status CHECK (status IN ('draft', 'published', 'rolled_back')),
    CONSTRAINT uq_gov_rel_version UNIQUE (tenant_id, version)
);
CREATE INDEX IF NOT EXISTS idx_gov_rel_tenant
    ON ai.governance_releases(tenant_id, created_at DESC);
