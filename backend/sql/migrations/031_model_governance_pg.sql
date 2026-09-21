-- ============================================================
-- 031_model_governance_pg.sql — 模型治理族 + 幂等终态表（alembic 手写化）
--
-- 背景：Alembic 双轨退役（2026-09-21）。alembic memory 线的以下版本
-- 在 backend/sql/migrations/*.sql 中没有对应物，废弃 alembic 前必须
-- 把它们收编进来，否则全新环境缺 15 张表和 3 处种子：
--   0011 idempotency_records  0014~0016 budget/price 族
--   0017~0022 llm 治理族      0025 内置模型种子
-- （0002 prompts→018、0003~0010→020、0012/0013→013、0023/0024→028/029
--   已有对应，不重复收录）
--
-- 内容 = 原版本文件的 SQL 原文按序拼接，未改语义；
-- 全部幂等（CREATE IF NOT EXISTS / ON CONFLICT DO NOTHING），可重复执行。
-- 目标库：agent_memory
-- ============================================================


-- ═══════════ 源自 alembic 0011_idempotency_records.py ═══════════

CREATE TABLE IF NOT EXISTS ai.idempotency_records (
    tenant_id        TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    operation        TEXT NOT NULL,
    client_key       TEXT NOT NULL,
    request_hash     CHAR(64) NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running',
    result           JSONB,
    error_code       TEXT,
    attempt          INTEGER NOT NULL DEFAULT 0,
    lease_id         UUID,
    lease_expires_at TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id, operation, client_key),
    CONSTRAINT idempotency_records_status_check
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT idempotency_records_attempt_check
        CHECK (attempt >= 0)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_records_expiry
    ON ai.idempotency_records (expires_at)
    WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_idempotency_records_status_updated
    ON ai.idempotency_records (status, updated_at DESC);


-- ═══════════ 源自 alembic 0014_budget_quota_price_tables.py ═══════════

CREATE TABLE IF NOT EXISTS model_price (
            id BIGSERIAL PRIMARY KEY,
            model_name TEXT NOT NULL,
            component TEXT NOT NULL CHECK (component IN ('llm', 'embedding', 'rerank')),
            dimension TEXT NOT NULL CHECK (
                dimension IN ('input', 'output', 'cache_read', 'cache_write',
                              'reasoning', 'tool_call')
            ),
            price_per_unit NUMERIC(18, 6) NOT NULL CHECK (price_per_unit >= 0),
            unit TEXT NOT NULL DEFAULT 'per_1m_tokens'
                CHECK (unit IN ('per_1m_tokens', 'per_call')),
            currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
            price_table_version TEXT NOT NULL,
            source TEXT NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            effective_to TIMESTAMPTZ,
            approval_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (approval_status IN ('pending', 'approved', 'rejected')),
            reviewer_1 TEXT,
            reviewer_2 TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                approval_status <> 'approved'
                OR (
                    reviewer_1 IS NOT NULL AND reviewer_1 <> ''
                    AND reviewer_2 IS NOT NULL AND reviewer_2 <> ''
                    AND reviewer_1 <> reviewer_2
                )
            )
        );

CREATE INDEX IF NOT EXISTS idx_model_price_effective
        ON model_price(model_name, component, dimension, effective_from DESC);

CREATE OR REPLACE FUNCTION reject_model_price_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'model_price is append-only';
        END;
        $$;

CREATE TABLE IF NOT EXISTS budget_policies (
            scope_type TEXT NOT NULL CHECK (
                scope_type IN ('user', 'tenant', 'tenant_default', 'platform')
            ),
            scope_id TEXT NOT NULL,
            daily_limit_usd NUMERIC(18, 6) NOT NULL CHECK (daily_limit_usd > 0),
            monthly_limit_usd NUMERIC(18, 6) NOT NULL CHECK (monthly_limit_usd > 0),
            enforcement TEXT NOT NULL DEFAULT 'hard'
                CHECK (enforcement IN ('hard', 'soft', 'audit')),
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            audit_exempt BOOLEAN NOT NULL DEFAULT false,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (scope_type, scope_id),
            CHECK (
                NOT audit_exempt
                OR (
                    scope_type = 'tenant'
                    AND scope_id LIKE 'test%'
                    AND enforcement = 'audit'
                )
            )
        );

INSERT INTO budget_policies
            (scope_type, scope_id, daily_limit_usd, monthly_limit_usd,
             enforcement, timezone, updated_by)
        VALUES
            ('platform', 'default', 20.000000, 300.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant_default', 'default', 60.000000, 1000.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant', 'platform-default', 20.000000, 300.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant', 'internal', 100.000000, 2000.000000,
             'hard', 'Asia/Shanghai', 'migration-0014'),
            ('tenant', 'test', 10.000000, 100.000000,
             'soft', 'Asia/Shanghai', 'migration-0014')
        ON CONFLICT (scope_type, scope_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS budget_ledger (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK (period_type IN ('day', 'month')),
            period_start TIMESTAMPTZ NOT NULL,
            limit_usd NUMERIC(18, 6) NOT NULL CHECK (limit_usd > 0),
            enforcement TEXT NOT NULL CHECK (enforcement IN ('hard', 'soft', 'audit')),
            used_usd NUMERIC(18, 6) NOT NULL DEFAULT 0 CHECK (used_usd >= 0),
            reserved_usd NUMERIC(18, 6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (scope_type, scope_id, period_type, period_start)
        );

CREATE TABLE IF NOT EXISTS budget_reservations (
            id UUID NOT NULL,
            request_id TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK (period_type IN ('day', 'month')),
            period_start TIMESTAMPTZ NOT NULL,
            reserved_usd NUMERIC(18, 6) NOT NULL CHECK (reserved_usd >= 0),
            settled_usd NUMERIC(18, 6) NOT NULL DEFAULT 0 CHECK (settled_usd >= 0),
            status TEXT NOT NULL DEFAULT 'reserved'
                CHECK (status IN ('reserved', 'settled', 'released')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            settled_at TIMESTAMPTZ,
            PRIMARY KEY (id, scope_type, scope_id, period_type)
        );

CREATE INDEX IF NOT EXISTS idx_budget_reservations_request
        ON budget_reservations(request_id, created_at DESC);

CREATE TABLE IF NOT EXISTS budget_events (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK (period_type IN ('day', 'month')),
            period_start TIMESTAMPTZ NOT NULL,
            threshold NUMERIC(5, 4) NOT NULL CHECK (threshold IN (0.8000, 1.0000)),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (scope_type, scope_id, period_type, period_start, threshold)
        );


-- ═══════════ 源自 alembic 0015_budget_policy_audit.py ═══════════

CREATE TABLE IF NOT EXISTS budget_policy_audit (
            id BIGSERIAL PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            before_value JSONB,
            after_value JSONB NOT NULL,
            reason TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_budget_policy_audit_scope
        ON budget_policy_audit(scope_type, scope_id, created_at DESC);


-- ═══════════ 源自 alembic 0016_price_governance.py ═══════════

CREATE TABLE IF NOT EXISTS model_price_versions (
            version TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            imported_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'reviewed_1', 'scheduled',
                                  'canary', 'active', 'rejected', 'expired')),
            effective_from TIMESTAMPTZ NOT NULL,
            reviewer_1 TEXT,
            reviewer_2 TEXT,
            canary_started_at TIMESTAMPTZ,
            canary_completed_at TIMESTAMPTZ,
            coverage_ratio NUMERIC(6, 5) NOT NULL DEFAULT 0,
            missing_price_count INTEGER NOT NULL DEFAULT 0
                CHECK (missing_price_count >= 0),
            price_calculation_error_count INTEGER NOT NULL DEFAULT 0
                CHECK (price_calculation_error_count >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (reviewer_1 IS NULL OR reviewer_1 <> imported_by),
            CHECK (reviewer_2 IS NULL OR reviewer_2 <> imported_by),
            CHECK (reviewer_1 IS NULL OR reviewer_2 IS NULL OR reviewer_1 <> reviewer_2)
        );

CREATE TABLE IF NOT EXISTS model_price_reviews (
            id BIGSERIAL PRIMARY KEY,
            version TEXT NOT NULL REFERENCES model_price_versions(version),
            reviewer TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
            reason TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (version, reviewer)
        );

CREATE INDEX IF NOT EXISTS idx_model_price_versions_status
        ON model_price_versions(status, updated_at DESC);


-- ═══════════ 源自 alembic 0017_llm_providers.py ═══════════

CREATE TABLE IF NOT EXISTS llm_providers (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            -- 协议适配族：代码白名单，用户不可改（配错即不可用，见设计 §3.2）
            driver TEXT NOT NULL
                CHECK (driver IN ('openai', 'anthropic', 'ollama')),
            base_url TEXT NOT NULL DEFAULT '',
            -- public 走 url_guard 全量检查；private 需管理员**显式勾选**才跳过
            -- IP 段检查（不得由「解析出来是私网」自动放行，否则 DNS rebinding 绕过）
            network_scope TEXT NOT NULL DEFAULT 'public'
                CHECK (network_scope IN ('public', 'private')),
            extra_headers JSONB NOT NULL DEFAULT '{}'::jsonb,
            -- 计费口径：metered 按价格表 / subscription 恒 0 但显示「订阅制」/
            -- local 自托管恒 0。三者语义不可合并（见设计 B.3）。
            billing TEXT NOT NULL DEFAULT 'metered'
                CHECK (billing IN ('metered', 'subscription', 'local')),
            is_builtin BOOLEAN NOT NULL DEFAULT false,
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE TABLE IF NOT EXISTS llm_provider_credentials (
            provider_id TEXT PRIMARY KEY
                REFERENCES llm_providers(id) ON DELETE CASCADE,
            -- Fernet 密文（TEXT：VARCHAR(128) 装不下）
            key_cipher TEXT NOT NULL,
            -- 脱敏展示 + 审计用；明文与密文都不得进审计表
            key_fingerprint TEXT NOT NULL DEFAULT '',
            key_last4 TEXT NOT NULL DEFAULT '',
            -- 轮换计数：与缓存实例比对可发现「密钥已换但仍用旧实例」
            key_version INTEGER NOT NULL DEFAULT 1 CHECK (key_version >= 0),
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE TABLE IF NOT EXISTS llm_models (
            -- 大小写敏感：MiniMax-M3 / Qwen/Qwen3-32B / BAAI/bge-m3 不得被 lower 破坏
            name TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL
                REFERENCES llm_providers(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            context_length INTEGER,
            pricing JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT true,
            source TEXT NOT NULL DEFAULT 'user'
                CHECK (source IN ('builtin', 'user')),
            created_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_llm_providers_enabled
        ON llm_providers(enabled, is_builtin);

CREATE INDEX IF NOT EXISTS idx_llm_models_provider
        ON llm_models(provider_id, enabled);


-- ═══════════ 源自 alembic 0018_model_config_governance.py ═══════════

INSERT INTO llm_providers
            (id, display_name, driver, base_url, billing, is_builtin, enabled)
        VALUES
            ('ollama', 'Ollama', 'ollama', '', 'local', true, true),
            ('deepseek', 'DeepSeek', 'openai', '', 'metered', true, true),
            ('minimax', 'MiniMax', 'anthropic', '', 'metered', true, true),
            ('qwen', '通义千问', 'openai', '', 'metered', true, true),
            ('qwen_tp', '通义千问 Token Plan', 'openai', '', 'subscription', true, true),
            ('vllm', '自托管 vLLM', 'openai', '', 'local', true, true),
            ('siliconflow', '硅基流动', 'openai', '', 'metered', true, true)
        ON CONFLICT (id) DO NOTHING;

ALTER TABLE llm_providers
            ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS last_probe_ok BOOLEAN,
            ADD COLUMN IF NOT EXISTS last_probe_worst_grade TEXT;

CREATE TABLE IF NOT EXISTS llm_model_role_bindings (
            role TEXT PRIMARY KEY,
            model_name TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE TABLE IF NOT EXISTS llm_config_history (
            id BIGSERIAL PRIMARY KEY,
            object_type TEXT NOT NULL
                CHECK (object_type IN (
                    'role', 'provider', 'provider_credential',
                    'provider_network_scope'
                )),
            object_key TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            secret_fingerprint TEXT,
            operator TEXT NOT NULL DEFAULT '',
            rollbackable BOOLEAN NOT NULL DEFAULT true,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_llm_config_history_changed_at
        ON llm_config_history(changed_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_llm_config_history_object
        ON llm_config_history(object_type, object_key, changed_at DESC);


-- ═══════════ 源自 alembic 0019_specialized_model_bindings.py ═══════════

ALTER TABLE llm_providers
            DROP CONSTRAINT IF EXISTS llm_providers_driver_check;

ALTER TABLE llm_providers
            ADD CONSTRAINT llm_providers_driver_check
            CHECK (driver IN ('openai', 'anthropic', 'ollama', 'specialized'));

CREATE TABLE IF NOT EXISTS llm_specialized_model_bindings (
            role TEXT PRIMARY KEY
                CHECK (role IN ('embedding', 'rerank')),
            provider_id TEXT NOT NULL
                REFERENCES llm_providers(id) ON DELETE RESTRICT,
            -- 适配器是后端白名单值；不把每个新协议都固化成数据库 CHECK。
            adapter TEXT NOT NULL,
            model_name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            options JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT true,
            last_probe_at TIMESTAMPTZ,
            last_probe_ok BOOLEAN,
            last_probe_summary TEXT,
            last_probe_elapsed_ms INTEGER,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_llm_specialized_bindings_provider
        ON llm_specialized_model_bindings(provider_id, enabled);


-- ═══════════ 源自 alembic 0020_model_kind_catalog.py ═══════════

ALTER TABLE llm_models
            ADD COLUMN IF NOT EXISTS model_kind TEXT NOT NULL DEFAULT 'chat';

UPDATE llm_models
        SET model_kind = 'chat'
        WHERE model_kind IS NULL
           OR model_kind NOT IN ('chat', 'embedding', 'rerank');

ALTER TABLE llm_models
            DROP CONSTRAINT IF EXISTS llm_models_model_kind_check;

ALTER TABLE llm_models
            ADD CONSTRAINT llm_models_model_kind_check
            CHECK (model_kind IN ('chat', 'embedding', 'rerank'));

CREATE INDEX IF NOT EXISTS idx_llm_models_provider_kind
        ON llm_models(provider_id, model_kind, enabled);


-- ═══════════ 源自 alembic 0021_catalog_specialized_models.py ═══════════

INSERT INTO llm_models
            (name, provider_id, display_name, model_kind, source, created_by, updated_at)
        SELECT model_name, provider_id, model_name, role, 'user', 'system', now()
        FROM llm_specialized_model_bindings
        WHERE enabled = true
        ON CONFLICT (name) DO UPDATE SET
            model_kind = CASE
                WHEN llm_models.provider_id = EXCLUDED.provider_id
                THEN EXCLUDED.model_kind
                ELSE llm_models.model_kind
            END,
            updated_at = CASE
                WHEN llm_models.provider_id = EXCLUDED.provider_id
                THEN now()
                ELSE llm_models.updated_at
            END;


-- ═══════════ 源自 alembic 0022_extend_model_kinds.py ═══════════

ALTER TABLE llm_models
            DROP CONSTRAINT IF EXISTS llm_models_model_kind_check;

ALTER TABLE llm_models
            ADD CONSTRAINT llm_models_model_kind_check
            CHECK (model_kind IN ('chat', 'embedding', 'rerank', 'vision', 'speech'));


-- ═══════════ 源自 alembic 0025_seed_builtin_models_to_db.py ═══════════
-- 内置模型种入 DB（B15「DB 统一控制」收尾）；pricing 为 JSONB 字面量。
INSERT INTO llm_models
    (name, provider_id, display_name, description, pricing, enabled, source, created_by, model_kind)
VALUES
    ('qwen3.7-plus', 'qwen', 'Qwen 3.7 Plus - 在线',
     '阿里云百炼 Qwen3.7-Plus，OpenAI 兼容协议，需要 API Key',
     '{"input_price_per_1m": 0.4, "output_price_per_1m": 1.2}', true, 'builtin', 'migration-0023', 'chat'),
    ('qwen3.7-plus@tp', 'qwen_tp', 'Qwen 3.7 Plus - Token Plan',
     '阿里云百炼模型包端点（sk-sp- Key），需配置 QWEN_TP_API_KEY',
     '{"input_price_per_1m": 0.0, "output_price_per_1m": 0.0}', true, 'builtin', 'migration-0023', 'chat'),
    ('qwen2.5:3b', 'ollama', 'Qwen 2.5 (3B) - 本地',
     '本地 Ollama，免费，无需 API Key',
     '{"input_price_per_1m": 0.0, "output_price_per_1m": 0.0}', true, 'builtin', 'migration-0023', 'chat'),
    ('deepseek-v4-flash', 'deepseek', 'DeepSeek V4-Flash - 云端',
     'DeepSeek V4-Flash，高并发低延迟，需要 API Key',
     '{"input_price_per_1m": 0.14, "output_price_per_1m": 0.28}', true, 'builtin', 'migration-0023', 'chat'),
    ('MiniMax-M3', 'minimax', 'MiniMax M3 - 云端',
     'MiniMax-M3，OpenAI 兼容协议，需要 API Key',
     '{"input_price_per_1m": 3.0, "output_price_per_1m": 15.0}', true, 'builtin', 'migration-0023', 'chat'),
    ('Qwen/Qwen3-32B-AWQ', 'vllm', 'Qwen3 32B (AWQ) - 自托管',
     '自托管 vLLM（OpenAI 兼容），需配置 VLLM_API_BASE/VLLM_API_KEY',
     '{"input_price_per_1m": 0.0, "output_price_per_1m": 0.0}', true, 'builtin', 'migration-0023', 'chat'),
    ('Qwen/Qwen3-32B', 'siliconflow', 'Qwen3 32B - 硅基流动',
     '硅基流动 Qwen3-32B，OpenAI 兼容协议，需在供应商页配置 API Key',
     '{"input_price_per_1m": 0.0, "output_price_per_1m": 0.0}', true, 'builtin', 'migration-0023', 'chat'),
    ('Qwen/Qwen3-8B', 'siliconflow', 'Qwen3 8B - 硅基流动',
     '硅基流动 Qwen3-8B，OpenAI 兼容协议，需在供应商页配置 API Key',
     '{"input_price_per_1m": 0.0, "output_price_per_1m": 0.0}', true, 'builtin', 'migration-0023', 'chat')
ON CONFLICT (name) DO NOTHING;
