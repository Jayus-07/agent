"""0023 — 客服自动派单、租户隔离与 outbox 数据契约。"""

from pathlib import Path

from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


SQL_PATH = Path(__file__).resolve().parents[3] / "migrations" / "028_cs_dispatch.sql"
UPGRADE_SQL = SQL_PATH.read_text(encoding="utf-8")


def upgrade() -> None:
    """执行与原生迁移相同的幂等 SQL。"""
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    """本迁移仅添加兼容字段，禁止破坏性回滚。"""
    return None
