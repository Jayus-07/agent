"""CS trace link — conversation ↔ trace association

Adds trace_id to messages and trace tracking columns to conversations.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
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
"""

DOWNGRADE_SQL = r"""
DROP INDEX IF EXISTS customer_service.idx_cs_conv_last_activity;
DROP INDEX IF EXISTS customer_service.idx_cs_msg_trace;

ALTER TABLE customer_service.conversations
    DROP COLUMN IF EXISTS last_trace_id,
    DROP COLUMN IF EXISTS trace_count;

ALTER TABLE customer_service.messages
    DROP COLUMN IF EXISTS trace_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
