"""0010 — audit_logs.result 约束补 'pending'（P3.5 实测修复）

P1 落地时 audit_logs.result CHECK 只含 success/failure/denied/error，
而 action expert 建 proposal 后写审计 result='pending'（语义正确：
proposal built, awaiting confirmation）→ CheckViolation，退款确认卡
链路在 strict 审计路径上被打断。

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-18
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SQL = r"""
ALTER TABLE customer_service.audit_logs
    DROP CONSTRAINT IF EXISTS audit_logs_result_check;
ALTER TABLE customer_service.audit_logs
    ADD CONSTRAINT audit_logs_result_check
    CHECK (result::text = ANY (
        ARRAY['success', 'failure', 'denied', 'error', 'pending']::text[]
    ));
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE customer_service.audit_logs "
        "DROP CONSTRAINT IF EXISTS audit_logs_result_check;"
        "ALTER TABLE customer_service.audit_logs "
        "ADD CONSTRAINT audit_logs_result_check "
        "CHECK (result::text = ANY ("
        "ARRAY['success', 'failure', 'denied', 'error']::text[]));"
    )
