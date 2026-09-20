-- 027_rag_processing_lineage.sql — RAG 上传/重索引模型血缘
-- 一次文件处理一行 run；每个逻辑阶段和重试尝试一行 step。

CREATE TABLE IF NOT EXISTS rag_processing_runs (
    run_id                TEXT PRIMARY KEY,
    doc_id                TEXT NOT NULL,
    file_hash             TEXT NOT NULL DEFAULT '',
    operation             TEXT NOT NULL DEFAULT 'upload',
    status                TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'failed', 'duplicate', 'cancelled')),
    pipeline_version      TEXT NOT NULL DEFAULT '',
    git_sha               TEXT,
    config_snapshot_hash  TEXT NOT NULL DEFAULT '',
    config_snapshot       JSONB NOT NULL DEFAULT '{}'::jsonb,
    task_id               TEXT,
    batch_id              TEXT,
    trace_id              TEXT,
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    error_message         TEXT,
    model_summary         JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_rag_processing_runs_doc_finished
    ON rag_processing_runs(doc_id, finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_processing_runs_status_started
    ON rag_processing_runs(status, started_at);
CREATE INDEX IF NOT EXISTS idx_rag_processing_runs_task
    ON rag_processing_runs(task_id);

CREATE TABLE IF NOT EXISTS rag_processing_steps (
    step_id               TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES rag_processing_runs(run_id)
        ON DELETE CASCADE,
    stage                 TEXT NOT NULL,
    ordinal               INTEGER NOT NULL DEFAULT 0,
    attempt_no            INTEGER NOT NULL DEFAULT 1,
    status                TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'skipped', 'cached', 'fallback', 'failed')),
    role                  TEXT,
    engine_type           TEXT NOT NULL DEFAULT '',
    provider              TEXT,
    model_name            TEXT,
    model_revision        TEXT,
    config_source         TEXT,
    config_revision       TEXT,
    artifact_fingerprint  TEXT,
    prompt_key            TEXT,
    prompt_version        TEXT,
    prompt_hash           TEXT,
    taxonomy_version      TEXT,
    rules_version         TEXT,
    schema_fingerprint    TEXT,
    cache_status          TEXT NOT NULL DEFAULT 'miss',
    input_count           INTEGER NOT NULL DEFAULT 0,
    output_count          INTEGER NOT NULL DEFAULT 0,
    prompt_tokens         INTEGER NOT NULL DEFAULT 0,
    completion_tokens     INTEGER NOT NULL DEFAULT 0,
    total_tokens          INTEGER NOT NULL DEFAULT 0,
    cached_tokens         INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd              DOUBLE PRECISION NOT NULL DEFAULT 0,
    duration_ms           DOUBLE PRECISION NOT NULL DEFAULT 0,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    fallback_reason       TEXT,
    skip_reason           TEXT,
    error_message         TEXT,
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (run_id, stage, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_rag_processing_steps_run_ordinal
    ON rag_processing_steps(run_id, ordinal, attempt_no);
CREATE INDEX IF NOT EXISTS idx_rag_processing_steps_model
    ON rag_processing_steps(model_name, provider, started_at);

ALTER TABLE doc_registry
    ADD COLUMN IF NOT EXISTS last_processing_run_id TEXT,
    ADD COLUMN IF NOT EXISTS pipeline_version TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS metadata_route TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS ocr_used BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ocr_model TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS metadata_model TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS model_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS processing_status TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS processing_finished_at TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_doc_registry_processing_run
    ON doc_registry(last_processing_run_id);

ALTER TABLE llm_usage
    ADD COLUMN IF NOT EXISTS run_id TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS step_id TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS role TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS stage TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_llm_usage_processing_run
    ON llm_usage(run_id, step_id, ts);
