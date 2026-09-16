"""网关访问审计日志 — ai.gateway_access_logs

数据链路：APISIX gateway-access-log.lua（log 阶段）→ Redis Stream
agent:gw:access-log → backend/observability/gateway_log_ingest.py 同步消费落库 →
管理端 /observability/gateway「访问日志」区块查询（/api/observability/gateway-access-logs）。

entry_id 为 Redis Stream 条目 id（消费侧幂等键）：消费者 at-least-once 语义下
重启可能重放历史段，UNIQUE + ON CONFLICT DO NOTHING 保证不重复入库。

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-16
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ai schema 常规由 0007（trace mirror）创建；单独启用本迁移的环境可能没有
CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE IF NOT EXISTS ai.gateway_access_logs (
    id          BIGSERIAL PRIMARY KEY,
    entry_id    TEXT             NOT NULL UNIQUE,
    ts          TIMESTAMPTZ      NOT NULL,
    client_ip   TEXT             NOT NULL DEFAULT '',
    user_id     TEXT             NOT NULL DEFAULT '',
    auth_type   TEXT             NOT NULL DEFAULT '',
    trace_id    TEXT             NOT NULL DEFAULT '',
    method      TEXT             NOT NULL DEFAULT '',
    uri         TEXT             NOT NULL DEFAULT '',
    query       TEXT             NOT NULL DEFAULT '',
    status      INTEGER          NOT NULL DEFAULT 0,
    bytes       BIGINT           NOT NULL DEFAULT 0,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    ua          TEXT             NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gw_access_logs_ts
    ON ai.gateway_access_logs (ts DESC);

CREATE INDEX IF NOT EXISTS idx_gw_access_logs_user
    ON ai.gateway_access_logs (user_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_gw_access_logs_ip
    ON ai.gateway_access_logs (client_ip, ts DESC);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS ai.gateway_access_logs;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
