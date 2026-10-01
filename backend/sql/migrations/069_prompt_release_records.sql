-- 069_prompt_release_records.sql — Prompt 候选发布门禁记录
-- Prompt 版本表描述内容，发布记录描述一次带评测证据的环境切换。

CREATE TABLE IF NOT EXISTS ai.prompt_release_records (
    id                          BIGSERIAL PRIMARY KEY,
    release_id                  TEXT NOT NULL UNIQUE,
    prompt_key                  TEXT NOT NULL,
    version                     INTEGER NOT NULL CHECK (version > 0),
    target_env                 TEXT NOT NULL DEFAULT 'production',
    status                      TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN (
                                    'pending', 'running', 'failed', 'passed',
                                    'approved', 'published', 'rolled_back'
                                )),
    eval_suite                  TEXT NOT NULL,
    dataset_provenance          JSONB NOT NULL DEFAULT '{}',
    prompt_snapshot             JSONB NOT NULL DEFAULT '{}',
    tool_contract_fingerprint   TEXT NOT NULL DEFAULT '',
    model_binding_fingerprint   TEXT NOT NULL DEFAULT '',
    executor                    TEXT NOT NULL DEFAULT 'local'
                                CHECK (executor IN ('local', 'github')),
    eval_run_id                 TEXT,
    external_run_id            TEXT UNIQUE,
    metrics                     JSONB NOT NULL DEFAULT '{}',
    failure_reason              TEXT NOT NULL DEFAULT '',
    created_by                 TEXT NOT NULL DEFAULT '',
    approved_by                TEXT NOT NULL DEFAULT '',
    published_by               TEXT NOT NULL DEFAULT '',
    rollback_by                TEXT NOT NULL DEFAULT '',
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at               TIMESTAMPTZ,
    UNIQUE (prompt_key, version, target_env, eval_suite)
);

CREATE INDEX IF NOT EXISTS idx_prompt_release_records_prompt
    ON ai.prompt_release_records(prompt_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prompt_release_records_status
    ON ai.prompt_release_records(status, created_at DESC);
