"""CS Phase 1 — dual-dimension state + new entity tables

Extends customer_service schema (created by 006 raw SQL migration):

1. conversations: add handling_mode, priority, labels, context_summary,
   inbox_id, team_id, first_reply_at, last_activity_at; migrate old status
   values to new dual-dimension model.

2. messages: add sender_type, sender_id, content_type, private, attachments;
   backfill sender_type from role.

3. New tables: customers, cs_agents, assignments.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL = r"""
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

-- Backfill handling_mode from legacy status + ai_enabled
UPDATE customer_service.conversations
SET handling_mode = CASE
    WHEN status = 'waiting_human' THEN 'waiting_human'
    WHEN status = 'human_active'  THEN 'human'
    WHEN ai_enabled = false       THEN 'human'
    ELSE 'ai'
END
WHERE handling_mode = 'ai' AND status IN ('waiting_human', 'human_active');

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

-- Rename old 'status' column to 'conversation_status' if it still exists as 'status'
-- Note: 006 migration created it as 'status', we need to rename
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
-- 7. Readonly grants
-- =============================================
GRANT SELECT ON customer_service.customers TO agent_readonly;
GRANT SELECT ON customer_service.cs_agents TO agent_readonly;
GRANT SELECT ON customer_service.assignments TO agent_readonly;
"""

UPGRADE_SQL = SQL

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS customer_service.assignments CASCADE;
DROP TABLE IF EXISTS customer_service.cs_agents CASCADE;
DROP TABLE IF EXISTS customer_service.customers CASCADE;

ALTER TABLE customer_service.messages DROP COLUMN IF EXISTS sender_type;
ALTER TABLE customer_service.messages DROP COLUMN IF EXISTS sender_id;
ALTER TABLE customer_service.messages DROP COLUMN IF EXISTS content_type;
ALTER TABLE customer_service.messages DROP COLUMN IF EXISTS private;
ALTER TABLE customer_service.messages DROP COLUMN IF EXISTS attachments;
DROP INDEX IF EXISTS customer_service.idx_cs_msg_intent;

ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS handling_mode;
ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS priority;
ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS labels;
ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS context_summary;
ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS inbox_id;
ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS team_id;
ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS first_reply_at;
ALTER TABLE customer_service.conversations DROP COLUMN IF EXISTS last_activity_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
