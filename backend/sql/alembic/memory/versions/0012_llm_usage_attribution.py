"""0012 — LLM/Embedding/Rerank 用量的请求归属字段。"""
from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE IF EXISTS llm_usage "
        "ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE IF EXISTS llm_usage "
        "ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE IF EXISTS llm_usage "
        "ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_tenant_user_ts "
        "ON llm_usage(tenant_id, user_id, ts DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_usage_tenant_user_ts")
    op.execute("ALTER TABLE IF EXISTS llm_usage DROP COLUMN IF EXISTS tenant_id")
    op.execute("ALTER TABLE IF EXISTS llm_usage DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE IF EXISTS llm_usage DROP COLUMN IF EXISTS request_id")
