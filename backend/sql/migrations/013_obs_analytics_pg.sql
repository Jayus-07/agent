-- 013_obs_analytics_pg.sql — 可观测层结构化分析（trace_summary + llm_usage）
-- 目标库：agent_memory（与 012 同层）
-- 执行方式：init-dbs.sh 空卷首启 / 存量库手工 psql 同 012
-- 说明：
--   - trace_summary：trace 完成时的结构化摘要（AnalyticsStore）
--   - llm_usage：每次 LLM/Embedding/Rerank 调用一行（LLMUsageStore），
--     SQLite 版 id 为 INTEGER AUTOINCREMENT，PG 用 BIGSERIAL（ORDER BY id DESC 语义不变）
--   - 应用侧 _init_db 幂等建表，语句与本文件一致

CREATE TABLE IF NOT EXISTS trace_summary (
    trace_id          TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,      -- trace 开始时间 (ISO8601 UTC)
    session_id        TEXT NOT NULL DEFAULT '',
    workflow_name     TEXT NOT NULL DEFAULT '',
    workflow_kind     TEXT NOT NULL DEFAULT 'other',
    status            TEXT NOT NULL DEFAULT 'success',
    question          TEXT NOT NULL DEFAULT '',
    answer_preview    TEXT NOT NULL DEFAULT '',
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    model             TEXT NOT NULL DEFAULT '',
    provider          TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
    kb_id             TEXT NOT NULL DEFAULT '',
    rejected          INTEGER NOT NULL DEFAULT 0,
    tags              TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_summary_session  ON trace_summary(session_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_trace_summary_ts       ON trace_summary(ts DESC);
CREATE INDEX IF NOT EXISTS idx_trace_summary_workflow ON trace_summary(workflow_name, ts DESC);

CREATE TABLE IF NOT EXISTS llm_usage (
    id                BIGSERIAL PRIMARY KEY,
    ts                TEXT NOT NULL,      -- ISO8601 UTC（与 trace ts 同格式，支持字典序过滤）
    trace_id          TEXT NOT NULL DEFAULT '',
    request_id        TEXT NOT NULL DEFAULT '',
    session_id        TEXT NOT NULL DEFAULT '',
    user_id           TEXT NOT NULL DEFAULT '',
    tenant_id         TEXT NOT NULL DEFAULT '',
    component         TEXT NOT NULL DEFAULT 'llm',  -- llm | embedding | rerank
    model             TEXT NOT NULL DEFAULT '',
    provider          TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cached_tokens     INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
    duration_ms       DOUBLE PRECISION NOT NULL DEFAULT 0,
    finish_reason     TEXT NOT NULL DEFAULT '',
    decision          TEXT NOT NULL DEFAULT 'primary',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts        ON llm_usage(ts);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model     ON llm_usage(model, ts);
CREATE INDEX IF NOT EXISTS idx_llm_usage_trace     ON llm_usage(trace_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_tenant_user_ts ON llm_usage(tenant_id, user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_component ON llm_usage(component, ts);
