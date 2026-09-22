-- =====================================================
-- 042_sql_query_audits.sql
-- SQL Agent 生产收口（STOP C 2026-09-23）：查询审计持久化
--
-- 目标库：agent_memory（与 trace/observability 存储同库同口径）
-- 写入方：backend/sql/audit.py（best-effort，异步，失败不阻塞主查询）
-- 隐私口径：不存 SQL 原文（含用户 literal/订单号等 PII 风险），
--   只存 query_hash = sha256(规范化 SQL) + 表名集合；
--   禁止落库 Authorization/JWT/password/凭据/堆栈 secret。
-- =====================================================

CREATE TABLE IF NOT EXISTS sql_query_audits (
    id             UUID PRIMARY KEY,
    session_id     VARCHAR(128) NOT NULL DEFAULT '',
    user_id        VARCHAR(128) NOT NULL DEFAULT '',
    tenant_id      VARCHAR(128) NOT NULL DEFAULT '',
    department     VARCHAR(128) NOT NULL DEFAULT '',
    data_scope     VARCHAR(32)  NOT NULL DEFAULT '',
    source_channel VARCHAR(16)  NOT NULL DEFAULT '',   -- http|graph|tool|mcp|unknown
    tool_name      VARCHAR(64)  NOT NULL DEFAULT '',
    query_hash     VARCHAR(64)  NOT NULL DEFAULT '',   -- sha256(normalized_sql)
    tables         TEXT         NOT NULL DEFAULT '[]', -- JSON 数组（白名单表）
    decision       VARCHAR(32)  NOT NULL,              -- ALLOW|DENY_*|EXECUTION_*|TIMEOUT
    deny_code      VARCHAR(64)  NOT NULL DEFAULT '',   -- SQL_PERMISSION_DENIED 等
    duration_ms    INTEGER      NOT NULL DEFAULT 0,
    row_count      INTEGER,
    status         VARCHAR(32)  NOT NULL DEFAULT '',   -- executor status
    error_type     VARCHAR(64)  NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 聚合查询索引（按通道/决策/时间排查旁路）
CREATE INDEX IF NOT EXISTS idx_sql_query_audits_created_at
    ON sql_query_audits (created_at);
CREATE INDEX IF NOT EXISTS idx_sql_query_audits_decision_channel
    ON sql_query_audits (decision, source_channel, created_at);
CREATE INDEX IF NOT EXISTS idx_sql_query_audits_user
    ON sql_query_audits (user_id, created_at);
