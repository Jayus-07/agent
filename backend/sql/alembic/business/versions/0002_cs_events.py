"""0002 — customer_service.events 实时事件持久化表（P3.2）

断线补发 + event_id 幂等去重的事件源：
- id 全局自增，兼作会话内游标 seq（单调即可）
- event_id UNIQUE 客户端去重键
- payload JSONB 保存原平铺字段

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-17
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = r"""
CREATE TABLE IF NOT EXISTS customer_service.events (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL,
    event_id        VARCHAR(64) UNIQUE NOT NULL,
    type            VARCHAR(64) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cs_event_conv_id
    ON customer_service.events (conversation_id, id);
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customer_service.events")
