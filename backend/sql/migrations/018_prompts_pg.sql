-- =====================================================
-- 018_prompts_pg.sql — Prompt 管理三表（迁移计划外缺口补齐）
-- 背景：2026-09-17 P3 清库重建暴露——prompts 表此前不在任何迁移中
--       （仅存在于旧 PG 卷的存量数据），清库后 PromptService 报
--       `relation "prompts" does not exist`。本迁移按 ORM 模型
--       backend/memory/models/prompt.py 补齐权威 schema。
-- 目标库：agent_memory
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行
-- =====================================================

CREATE TABLE IF NOT EXISTS prompts (
    id                SERIAL PRIMARY KEY,
    key               VARCHAR(128) NOT NULL UNIQUE,
    name              VARCHAR(256) NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    category          VARCHAR(64) NOT NULL DEFAULT '',
    risk_level        VARCHAR(16) NOT NULL DEFAULT 'low',
    template_engine   VARCHAR(32) NOT NULL DEFAULT 'str_format',
    variables         JSONB NOT NULL DEFAULT '[]'::jsonb,
    active_version    INTEGER,
    is_code_controlled BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at        TIMESTAMPTZ DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS ix_prompts_key ON prompts(key);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id          SERIAL PRIMARY KEY,
    prompt_id   INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    template    TEXT NOT NULL,
    variables   JSONB NOT NULL DEFAULT '[]'::jsonb,
    status      VARCHAR(16) NOT NULL DEFAULT 'draft',
    change_note TEXT NOT NULL DEFAULT '',
    created_by  VARCHAR(128) NOT NULL DEFAULT 'system',
    created_at  TIMESTAMPTZ DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at  TIMESTAMPTZ DEFAULT (now() AT TIME ZONE 'utc'),
    CONSTRAINT uq_prompt_version UNIQUE (prompt_id, version),
    CONSTRAINT ck_prompt_version_status
        CHECK (status IN ('draft','testing','evaluation','passed','published','archived'))
);
CREATE INDEX IF NOT EXISTS ix_prompt_versions_prompt_id ON prompt_versions(prompt_id);

CREATE TABLE IF NOT EXISTS prompt_audit_log (
    id           SERIAL PRIMARY KEY,
    prompt_key   VARCHAR(128) NOT NULL,
    action       VARCHAR(32) NOT NULL,
    from_version INTEGER,
    to_version   INTEGER,
    actor        VARCHAR(128) NOT NULL DEFAULT 'system',
    role         VARCHAR(32) NOT NULL DEFAULT '',
    detail       JSONB,
    created_at   TIMESTAMPTZ DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS ix_prompt_audit_log_prompt_key ON prompt_audit_log(prompt_key);
