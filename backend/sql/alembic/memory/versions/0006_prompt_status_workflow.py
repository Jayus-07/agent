"""Prompt status workflow — updated_at column + status CHECK constraint

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE prompt_versions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_prompt_version_status'
    ) THEN
        ALTER TABLE prompt_versions
            ADD CONSTRAINT ck_prompt_version_status
            CHECK (status IN ('draft','testing','evaluation','passed','published','archived'));
    END IF;
END $$;
"""

DOWNGRADE_SQL = r"""
ALTER TABLE prompt_versions DROP CONSTRAINT IF EXISTS ck_prompt_version_status;
ALTER TABLE prompt_versions DROP COLUMN IF EXISTS updated_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
