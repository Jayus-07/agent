-- 154_sys_config_ddl_and_price_seed.sql
-- 1. O2：sys_config 与 sys_config_history 建表入正式迁移，终结运行期反复执行 CREATE TABLE DDL（消灭 1381ms 慢 SQL）
-- 2. O1：补齐模型权威价格行（glm-5.3-flash / bge-m3 / bge-reranker-v2-m3 等），消除 [Budget][price_unknown] 计费告警

-- ── 1. sys_config DDL 收敛入迁移 ──────────────────────────────
CREATE TABLE IF NOT EXISTS sys_config (
    key VARCHAR(64) PRIMARY KEY,
    value VARCHAR(128) NOT NULL,
    updated_by VARCHAR(64),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sys_config_history (
    id BIGSERIAL PRIMARY KEY,
    key VARCHAR(64) NOT NULL,
    old_value VARCHAR(128),
    new_value VARCHAR(128),
    operator VARCHAR(64) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sys_config_history_key
    ON sys_config_history (key, changed_at DESC);

-- ── 2. 价格治理版本与生效价格补齐（O1 闭环） ─────────────────────
INSERT INTO model_price_versions (
    version, source, imported_by, status, effective_from,
    reviewer_1, reviewer_2, coverage_ratio, missing_price_count, price_calculation_error_count
) VALUES (
    'v1.0-seed', 'provider-page', 'system', 'active', now(),
    'admin', 'finance', 1.0, 0, 0
) ON CONFLICT (version) DO NOTHING;

-- glm-5.3-flash (智谱主力模型：输入 0.8 CNY/1M，输出 2.8 CNY/1M，缓存命中 0.23 CNY/1M)
INSERT INTO model_price (
    model_name, component, dimension, price_per_unit, unit, currency,
    price_table_version, source, effective_from, approval_status, reviewer_1, reviewer_2
) VALUES
    ('glm-5.3-flash', 'llm', 'input', 0.800000, 'per_1m_tokens', 'CNY', 'v1.0-seed', 'provider-page', now(), 'approved', 'admin', 'finance'),
    ('glm-5.3-flash', 'llm', 'output', 2.800000, 'per_1m_tokens', 'CNY', 'v1.0-seed', 'provider-page', now(), 'approved', 'admin', 'finance'),
    ('glm-5.3-flash', 'llm', 'cache_read', 0.230000, 'per_1m_tokens', 'CNY', 'v1.0-seed', 'provider-page', now(), 'approved', 'admin', 'finance')
ON CONFLICT DO NOTHING;

-- bge-m3 本地专项嵌入模型（0.000001 CNY / 1M 记账底数）
INSERT INTO model_price (
    model_name, component, dimension, price_per_unit, unit, currency,
    price_table_version, source, effective_from, approval_status, reviewer_1, reviewer_2
) VALUES
    ('bge-m3', 'embedding', 'input', 0.000001, 'per_1m_tokens', 'CNY', 'v1.0-seed', 'provider-page', now(), 'approved', 'admin', 'finance')
ON CONFLICT DO NOTHING;

-- bge-reranker-v2-m3 本地专项重排模型
INSERT INTO model_price (
    model_name, component, dimension, price_per_unit, unit, currency,
    price_table_version, source, effective_from, approval_status, reviewer_1, reviewer_2
) VALUES
    ('bge-reranker-v2-m3', 'rerank', 'input', 0.000001, 'per_1m_tokens', 'CNY', 'v1.0-seed', 'provider-page', now(), 'approved', 'admin', 'finance')
ON CONFLICT DO NOTHING;
