-- =====================================================
-- 020_alembic_gaps_pg.sql — Alembic 迁移缺口手写化（迁移计划外缺口补齐）
-- 背景：2026-09-17 P3 清库重建暴露——CS 域与 trace mirror 的部分 schema
--       演进仅存在于 Alembic（backend/sql/alembic/memory/versions/0003~0007），
--       不在 init-dbs.sh 的 psql 首启链路中。清库后 rag-service
--       MemoryService 报 `column chat_sessions.title does not exist`、
--       CS 会话表缺 0003/0005 扩展列。
-- 内容：将 0003/0004/0005/0007 的 UPGRADE_SQL 原样收录（全部幂等），
--       并补 ORM 漂移列 chat_sessions.title（模型有列、迁移无记录）。
-- 前置：006_customer_service.sql 需先执行（customer_service schema），
--       007_tool_approval.sql / 014_cs_rating.sql 由 init-dbs.sh 分别
--       应用到 agent_business / agent_memory。
-- 目标库：agent_memory
-- 幂等：可重复执行
-- =====================================================

-- ===== ORM 漂移补齐：chat_sessions.title =====
-- backend/memory/models/session.py 定义了 title 列（String(128) nullable），
-- 但 Alembic 0001~0008 与手写 001~019 均未创建该列；旧卷中为存量列。
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS title VARCHAR(128);

-- ===== from 0003_cs_phase1.py =====

-- =============================================
-- 1. Extend conversations table
-- =============================================

ALTER TABLE customer_service.conversations
    ADD COLUMN IF NOT EXISTS handling_mode     VARCHAR(20) NOT NULL DEFAULT 'ai',
    ADD COLUMN IF NOT EXISTS priority          VARCHAR(10) NOT NULL DEFAULT 'medium',
    ADD COLUMN IF NOT EXISTS labels            TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS context_summary   TEXT,
    ADD COLUMN IF NOT EXISTS inbox_id          VARCHAR(64),
    ADD COLUMN IF NOT EXISTS team_id           VARCHAR(64),
    ADD COLUMN IF NOT EXISTS first_reply_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_activity_at  TIMESTAMPTZ;

-- Rename old 'status' column to 'conversation_status' BEFORE adding constraints
-- Note: 006 migration created it as 'status', we need to rename first
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer_service'
          AND table_name = 'conversations'
          AND column_name = 'status'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer_service'
          AND table_name = 'conversations'
          AND column_name = 'conversation_status'
    ) THEN
        ALTER TABLE customer_service.conversations
            RENAME COLUMN status TO conversation_status;
    END IF;
END $$;

-- Backfill handling_mode from legacy status + ai_enabled
UPDATE customer_service.conversations
SET handling_mode = CASE
    WHEN conversation_status = 'waiting_human' THEN 'waiting_human'
    WHEN conversation_status = 'human_active'  THEN 'human'
    WHEN ai_enabled = false                   THEN 'human'
    ELSE 'ai'
END
WHERE handling_mode = 'ai'
  AND (conversation_status IN ('waiting_human', 'human_active') OR ai_enabled = false);

-- Backfill last_activity_at from updated_at where null
UPDATE customer_service.conversations
SET last_activity_at = updated_at
WHERE last_activity_at IS NULL;

-- Add new status values (keep old ones for backward compat during transition)
ALTER TABLE customer_service.conversations
    DROP CONSTRAINT IF EXISTS conversations_status_check;

ALTER TABLE customer_service.conversations
    ADD CONSTRAINT conversations_status_check
    CHECK (conversation_status IN ('open', 'pending', 'resolved', 'snoozed'));

-- Migrate old status values to new ones
UPDATE customer_service.conversations
SET conversation_status = CASE conversation_status
    WHEN 'active' THEN 'open'
    WHEN 'waiting_human' THEN 'open'
    WHEN 'human_active' THEN 'open'
    WHEN 'closed' THEN 'resolved'
    ELSE conversation_status
END;

-- =============================================
-- 2. Extend messages table
-- =============================================

ALTER TABLE customer_service.messages
    ADD COLUMN IF NOT EXISTS sender_type  VARCHAR(20) NOT NULL DEFAULT 'user',
    ADD COLUMN IF NOT EXISTS sender_id    VARCHAR(64),
    ADD COLUMN IF NOT EXISTS content_type VARCHAR(20) NOT NULL DEFAULT 'text',
    ADD COLUMN IF NOT EXISTS private      BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS attachments  JSONB;

-- Backfill sender_type from role
UPDATE customer_service.messages
SET sender_type = role
WHERE sender_type = 'user' AND role IN ('assistant', 'system', 'human_agent');

-- Add intent index
CREATE INDEX IF NOT EXISTS idx_cs_msg_intent
    ON customer_service.messages (intent_domain, intent_name)
    WHERE intent_domain IS NOT NULL;

-- =============================================
-- 3. New table: customers
-- =============================================

CREATE TABLE IF NOT EXISTS customer_service.customers (
    id               BIGSERIAL PRIMARY KEY,
    customer_id      VARCHAR(64) NOT NULL UNIQUE,
    display_name     VARCHAR(128) NOT NULL DEFAULT '',
    email            VARCHAR(128),
    phone            VARCHAR(20),
    avatar_url       TEXT,
    auth_provider    VARCHAR(20),
    auth_external_id VARCHAR(128),
    custom_fields    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cs_customer_external
    ON customer_service.customers (auth_provider, auth_external_id)
    WHERE auth_provider IS NOT NULL AND auth_external_id IS NOT NULL;

-- =============================================
-- 4. New table: cs_agents (human support agents)
-- =============================================

CREATE TABLE IF NOT EXISTS customer_service.cs_agents (
    id               BIGSERIAL PRIMARY KEY,
    agent_id         VARCHAR(64) NOT NULL UNIQUE,
    display_name     VARCHAR(128) NOT NULL,
    email            VARCHAR(128),
    role             VARCHAR(20) NOT NULL DEFAULT 'agent',
    available        BOOLEAN NOT NULL DEFAULT TRUE,
    max_conversations BIGINT NOT NULL DEFAULT 10,
    custom_fields    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cs_agent_email
    ON customer_service.cs_agents (email) WHERE email IS NOT NULL;

-- =============================================
-- 5. New table: assignments
-- =============================================

CREATE TABLE IF NOT EXISTS customer_service.assignments (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id)
                    ON DELETE CASCADE,
    agent_id        VARCHAR(64)
                    REFERENCES customer_service.cs_agents(agent_id)
                    ON DELETE SET NULL,
    assigned_by     VARCHAR(64),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unassigned_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_assign_conv
    ON customer_service.assignments (conversation_id, assigned_at);
CREATE INDEX IF NOT EXISTS idx_cs_assign_agent
    ON customer_service.assignments (agent_id, assigned_at);

-- =============================================
-- 6. FK: conversations.assigned_agent_id → cs_agents
-- =============================================

-- Add the FK column if it doesn't exist (006 used assigned_agent VARCHAR)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer_service'
          AND table_name = 'conversations'
          AND column_name = 'assigned_agent'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer_service'
          AND table_name = 'conversations'
          AND column_name = 'assigned_agent_id'
    ) THEN
        ALTER TABLE customer_service.conversations
            RENAME COLUMN assigned_agent TO assigned_agent_id;
    END IF;
END $$;

-- =============================================
-- 7. Readonly grants (skip if role doesn't exist)
-- =============================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly') THEN
        GRANT SELECT ON customer_service.customers TO agent_readonly;
        GRANT SELECT ON customer_service.cs_agents TO agent_readonly;
        GRANT SELECT ON customer_service.assignments TO agent_readonly;
    END IF;
END $$;


-- ===== from 0004_cs_phase7_handoffs.py =====

CREATE TABLE IF NOT EXISTS customer_service.handoffs (
    id              BIGSERIAL PRIMARY KEY,
    handoff_id      VARCHAR(64) NOT NULL UNIQUE,
    conversation_id VARCHAR(64) NOT NULL,
    user_id         VARCHAR(64) NOT NULL,
    handoff_state   VARCHAR(20) NOT NULL DEFAULT 'initiated',
    trigger_type    VARCHAR(30),
    trigger_reason  TEXT,
    ticket_id       VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_handoff_user
    ON customer_service.handoffs (user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_cs_handoff_active
    ON customer_service.handoffs (user_id, handoff_state)
    WHERE handoff_state != 'closed';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly') THEN
        GRANT SELECT ON customer_service.handoffs TO agent_readonly;
    END IF;
END $$;


-- ===== from 0005_cs_trace_link.py =====

ALTER TABLE customer_service.messages
    ADD COLUMN IF NOT EXISTS trace_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_cs_msg_trace
    ON customer_service.messages (trace_id) WHERE trace_id IS NOT NULL;

ALTER TABLE customer_service.conversations
    ADD COLUMN IF NOT EXISTS last_trace_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS trace_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_cs_conv_last_activity
    ON customer_service.conversations (last_activity_at DESC);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly') THEN
        GRANT SELECT ON customer_service.messages TO agent_readonly;
        GRANT SELECT ON customer_service.conversations TO agent_readonly;
    END IF;
END $$;


-- ===== from 0007_trace_pg_mirror.py =====

CREATE TABLE IF NOT EXISTS ai.trace_records (
    trace_id        TEXT PRIMARY KEY,
    session_id      TEXT,
    conversation_id TEXT,
    workflow_name   TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_trace_records_session
    ON ai.trace_records (session_id);

CREATE INDEX IF NOT EXISTS idx_trace_records_workflow
    ON ai.trace_records (workflow_name);

CREATE INDEX IF NOT EXISTS idx_trace_records_created
    ON ai.trace_records (created_at DESC);
