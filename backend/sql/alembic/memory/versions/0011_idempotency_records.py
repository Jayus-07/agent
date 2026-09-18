"""0011 — 全局幂等终态记录。

Redis 负责短期原子 claim/lease，本表负责 PG 权威终态与历史结果重放。
"""
from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SQL = r"""
CREATE TABLE IF NOT EXISTS ai.idempotency_records (
    tenant_id        TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    operation        TEXT NOT NULL,
    client_key       TEXT NOT NULL,
    request_hash     CHAR(64) NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running',
    result           JSONB,
    error_code       TEXT,
    attempt          INTEGER NOT NULL DEFAULT 0,
    lease_id         UUID,
    lease_expires_at TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id, operation, client_key),
    CONSTRAINT idempotency_records_status_check
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT idempotency_records_attempt_check
        CHECK (attempt >= 0)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_records_expiry
    ON ai.idempotency_records (expires_at)
    WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_idempotency_records_status_updated
    ON ai.idempotency_records (status, updated_at DESC);
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai.idempotency_records")
