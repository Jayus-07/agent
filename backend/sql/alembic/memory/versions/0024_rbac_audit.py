"""0024 — RBAC 版本谓词、审计与会话撤销所需字段。"""

from pathlib import Path

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


SQL_PATH = Path(__file__).resolve().parents[3] / "migrations" / "029_rbac_audit.sql"
UPGRADE_SQL = SQL_PATH.read_text(encoding="utf-8")


def upgrade() -> None:
    """执行幂等原生迁移。"""
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    """保留用户版本和审计历史，禁止破坏性回滚。"""
    return None
