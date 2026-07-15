-- memory/migrations/001_init.sql
-- Enterprise Memory System DDL
-- Run: psql -h localhost -U postgres -d demo -f memory/migrations/001_init.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Session Memory
CREATE TABLE IF NOT EXISTS chat_sessions (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL UNIQUE,
    user_id         VARCHAR(64)  NOT NULL DEFAULT 'default',
    summary         TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role            VARCHAR(16)  NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages (session_id, created_at);

-- LongTerm Memory
CREATE TABLE IF NOT EXISTS memory_records (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         VARCHAR(64)  NOT NULL DEFAULT 'default',
    session_id      VARCHAR(128) NOT NULL DEFAULT '',
    memory_type     VARCHAR(32)  NOT NULL CHECK (memory_type IN ('user_fact', 'preference', 'decision', 'knowledge')),
    content         TEXT         NOT NULL,
    embedding       vector(512),
    importance_score FLOAT       NOT NULL DEFAULT 0.5,
    confidence_score FLOAT       NOT NULL DEFAULT 1.0,
    access_count    INT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_access_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expire_at       TIMESTAMPTZ,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    superseded_by   UUID         REFERENCES memory_records(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_embedding ON memory_records
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_memory_user_active ON memory_records (user_id, is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records (memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory_records (importance_score DESC) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_last_access ON memory_records (last_access_at DESC);
