"""0015 — 预算策略变更追加式审计记录。"""

from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_policy_audit (
            id BIGSERIAL PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            before_value JSONB,
            after_value JSONB NOT NULL,
            reason TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_budget_policy_audit_scope
        ON budget_policy_audit(scope_type, scope_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS budget_policy_audit")
