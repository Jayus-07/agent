-- 107_auth_knowledge_bases.sql
-- 知识库目录从静态配置迁移到 PostgreSQL，变更保留版本号和审计记录。
-- 知识库目录沿用现有平台级共享语义；owner_depts 支持多个部门，"all" 单独表示全部部门。

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.knowledge_bases (
    kb_id       VARCHAR(64) PRIMARY KEY,
    name        VARCHAR(120) NOT NULL,
    domain      VARCHAR(64) NOT NULL,
    owner_depts JSONB NOT NULL,
    audience    VARCHAR(16) NOT NULL DEFAULT 'internal',
    deprecated  BOOLEAN NOT NULL DEFAULT FALSE,
    read_only   BOOLEAN NOT NULL DEFAULT FALSE,
    alias_for   VARCHAR(64),
    status      VARCHAR(12) NOT NULL DEFAULT 'active',
    version     INTEGER NOT NULL DEFAULT 0,
    created_by  VARCHAR(128) NOT NULL DEFAULT '',
    updated_by  VARCHAR(128) NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_knowledge_bases_audience
        CHECK (audience IN ('internal', 'customer', 'test')),
    CONSTRAINT ck_knowledge_bases_status
        CHECK (status IN ('active', 'inactive')),
    CONSTRAINT ck_knowledge_bases_owner_depts_array
        CHECK (jsonb_typeof(owner_depts) = 'array' AND jsonb_array_length(owner_depts) > 0)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_status_name
    ON auth.knowledge_bases (status, name, kb_id);

CREATE TABLE IF NOT EXISTS auth.knowledge_base_audits (
    id            BIGSERIAL PRIMARY KEY,
    kb_id         VARCHAR(64) NOT NULL,
    tenant_id     VARCHAR(128) NOT NULL,
    actor_user_id VARCHAR(128) NOT NULL,
    action        VARCHAR(16) NOT NULL,
    before_state  JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_state   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_knowledge_base_audits_action
        CHECK (action IN ('create', 'update', 'deactivate', 'activate'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_base_audits_kb_created
    ON auth.knowledge_base_audits (kb_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_base_audits_tenant_created
    ON auth.knowledge_base_audits (tenant_id, created_at DESC, id DESC);

-- 仅作为一次性初始目录基线；迁移后运行时目录以 auth.knowledge_bases 为准。
INSERT INTO auth.knowledge_bases
    (kb_id, name, domain, owner_depts, audience, deprecated, read_only, alias_for)
VALUES
    ('biz_inventory', '库存业务知识库', 'inventory', '["warehouse", "supply_chain"]', 'internal', FALSE, FALSE, NULL),
    ('biz_order', '订单业务知识库', 'order', '["order_dept", "customer"]', 'internal', FALSE, FALSE, NULL),
    ('biz_product', '商品业务知识库', 'product', '["product_dept"]', 'internal', FALSE, FALSE, NULL),
    ('policy_hr', '人事制度知识库', 'hr', '["hr"]', 'internal', FALSE, FALSE, NULL),
    ('policy_finance', '财务制度知识库', 'finance', '["finance"]', 'internal', FALSE, FALSE, NULL),
    ('policy_general', '企业公共制度知识库', 'general', '["all"]', 'internal', FALSE, FALSE, NULL),
    ('rag_eval_kb', '统一 RAG 评测知识库', 'general', '["all"]', 'test', FALSE, FALSE, NULL),
    ('cs_eval_kb', '客服评测知识库', 'customer_service', '["all"]', 'test', FALSE, FALSE, NULL),
    ('travel_eval_kb', '旅游评测知识库', 'travel', '["all"]', 'test', FALSE, FALSE, NULL),
    ('rag_test_kb', 'RAG 小型评测知识库（兼容）', 'general', '["all"]', 'test', TRUE, TRUE, 'rag_eval_kb'),
    ('rag_100_docs', 'RAG 100 文档评测知识库（兼容）', 'general', '["all"]', 'test', TRUE, TRUE, 'rag_eval_kb'),
    ('cs_faq', '客服FAQ', 'customer_service', '["customer"]', 'customer', FALSE, FALSE, NULL),
    ('cs_product', '产品知识库', 'customer_service', '["customer", "product_dept"]', 'customer', FALSE, FALSE, NULL),
    ('cs_policy', '政策知识库', 'customer_service', '["customer"]', 'customer', FALSE, FALSE, NULL),
    ('cs_aftersales', '售后知识库', 'customer_service', '["customer"]', 'customer', FALSE, FALSE, NULL),
    ('cs_complaint', '投诉处理知识库', 'customer_service', '["customer"]', 'customer', FALSE, FALSE, NULL),
    ('cs_scripts', '话术知识库', 'customer_service', '["customer"]', 'customer', FALSE, FALSE, NULL),
    ('travel', '旅游知识库', 'travel', '["all"]', 'customer', FALSE, FALSE, NULL)
ON CONFLICT (kb_id) DO NOTHING;

COMMENT ON TABLE auth.knowledge_bases IS
    '平台级 RAG 知识库目录；owner_depts 保存允许上传/管理的部门集合，all 表示所有部门';
COMMENT ON TABLE auth.knowledge_base_audits IS
    '知识库目录增改停用审计；与目录变更在同一事务内写入';
