"""CS Phase 7 — handoffs table

Creates customer_service.handoffs for persistent handoff state storage.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-04
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
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
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS customer_service.handoffs CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
