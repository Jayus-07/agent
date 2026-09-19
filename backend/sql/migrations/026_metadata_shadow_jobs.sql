-- 026_metadata_shadow_jobs.sql — 元数据影子评估任务表
-- 影子任务只在独立队列执行；正文存缓存，Celery payload 仅携带 job id。

CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE IF NOT EXISTS ai.metadata_shadow_jobs (
    id                 UUID PRIMARY KEY,
    upload_id          TEXT NOT NULL DEFAULT '',
    doc_id             TEXT NOT NULL DEFAULT '',
    text_hash          TEXT NOT NULL,
    filename           TEXT NOT NULL DEFAULT '',
    file_path          TEXT NOT NULL DEFAULT '',
    main_envelope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_cache_key    TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
    attempts           INTEGER NOT NULL DEFAULT 0,
    error              TEXT NOT NULL DEFAULT '',
    taxonomy_version   TEXT NOT NULL DEFAULT '',
    rules_version      TEXT NOT NULL DEFAULT '',
    model_version      TEXT NOT NULL DEFAULT '',
    shadow_doc_type    TEXT NOT NULL DEFAULT '',
    shadow_level       TEXT NOT NULL DEFAULT '',
    main_doc_type      TEXT NOT NULL DEFAULT '',
    agreement          BOOLEAN,
    latency_ms         DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (text_hash, taxonomy_version, rules_version, model_version)
);

CREATE INDEX IF NOT EXISTS idx_metadata_shadow_jobs_status
    ON ai.metadata_shadow_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_metadata_shadow_jobs_doc
    ON ai.metadata_shadow_jobs(doc_id, created_at DESC);
