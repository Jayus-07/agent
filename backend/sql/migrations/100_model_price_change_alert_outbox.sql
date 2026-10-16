-- 100_model_price_change_alert_outbox.sql
-- 官方价格变化告警的持久化、幂等待处理队列。

CREATE TABLE IF NOT EXISTS model_price_change_alert_events (
    event_id UUID PRIMARY KEY,
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
    observed_on DATE NOT NULL,
    previous_price JSONB NOT NULL,
    official_price JSONB NOT NULL,
    source_url TEXT NOT NULL,
    alert_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (alert_status IN ('pending', 'triggering', 'triggered')),
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    alert_triggered_at TIMESTAMPTZ,
    delivery_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (delivery_status = 'unknown'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, model_name, component, dimension, observed_on),
    CHECK (
        (alert_status = 'pending' AND alert_triggered_at IS NULL
            AND lease_expires_at IS NULL)
        OR (alert_status = 'triggering' AND alert_triggered_at IS NULL
            AND lease_expires_at IS NOT NULL)
        OR (alert_status = 'triggered' AND alert_triggered_at IS NOT NULL
            AND lease_expires_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_model_price_change_alert_events_pending
    ON model_price_change_alert_events (created_at, event_id)
    WHERE alert_status = 'pending';

ALTER TABLE model_price_monitor_runs
    DROP CONSTRAINT IF EXISTS model_price_monitor_runs_status_check;
ALTER TABLE model_price_monitor_runs
    ADD CONSTRAINT model_price_monitor_runs_status_check
    CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'cancelled'));
