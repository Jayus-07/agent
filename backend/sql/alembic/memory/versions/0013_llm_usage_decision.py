"""0013 — 记录每次模型调用的 primary/retry/fallback 决策。"""
from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE IF EXISTS llm_usage "
        "ADD COLUMN IF NOT EXISTS decision TEXT NOT NULL DEFAULT 'primary'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS llm_usage DROP COLUMN IF EXISTS decision")
