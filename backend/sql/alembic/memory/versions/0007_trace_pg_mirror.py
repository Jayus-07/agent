"""Trace PG mirror — ai.trace_records table for structured trace storage.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-04
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
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
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS ai.trace_records;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
