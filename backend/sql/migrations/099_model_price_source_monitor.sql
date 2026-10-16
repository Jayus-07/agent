-- 099_model_price_source_monitor.sql
-- 每日保存供应商公开模型价格观测；该表不参与计费，也不改动 model_price。

CREATE TABLE IF NOT EXISTS model_price_source_observations (
    source_id TEXT NOT NULL CHECK (
        source_id IN (
            'volcengine_ark_doubao_seed_2_0_mini',
            'aliyun_qwen3_7_text_embedding',
            'aliyun_qwen3_7_text_rerank'
        )
    ),
    model_name TEXT NOT NULL,
    component TEXT NOT NULL CHECK (component IN ('llm', 'embedding', 'rerank')),
    dimension TEXT NOT NULL CHECK (
        dimension IN ('input', 'output', 'cache_read')
    ),
    price_per_unit NUMERIC(18, 9) NOT NULL CHECK (price_per_unit >= 0),
    unit TEXT NOT NULL CHECK (unit = 'per_1m_tokens'),
    currency TEXT NOT NULL CHECK (currency = 'CNY'),
    source_url TEXT NOT NULL,
    source_updated_at TEXT NOT NULL DEFAULT '',
    content_sha256 CHAR(64) NOT NULL CHECK (length(content_sha256) = 64),
    observed_on DATE NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (
        source_id, model_name, component, dimension, observed_on
    )
);

CREATE INDEX IF NOT EXISTS idx_model_price_source_observations_latest
    ON model_price_source_observations (
        source_id, model_name, component, dimension, observed_on DESC
    );

-- 每次任务运行的状态与结果，便于管理端追踪失败、部分成功及告警记录。
CREATE TABLE IF NOT EXISTS model_price_monitor_runs (
    run_id UUID PRIMARY KEY,
    observed_on DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    sources_total INTEGER NOT NULL DEFAULT 3 CHECK (sources_total >= 0),
    sources_failed INTEGER NOT NULL DEFAULT 0 CHECK (sources_failed >= 0),
    quotes_recorded INTEGER NOT NULL DEFAULT 0 CHECK (quotes_recorded >= 0),
    source_changes INTEGER NOT NULL DEFAULT 0 CHECK (source_changes >= 0),
    system_mismatches INTEGER NOT NULL DEFAULT 0 CHECK (system_mismatches >= 0),
    source_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    comparison_results JSONB NOT NULL DEFAULT '{}'::jsonb,
    alert_events JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_price_monitor_runs_started
    ON model_price_monitor_runs (started_at DESC);
