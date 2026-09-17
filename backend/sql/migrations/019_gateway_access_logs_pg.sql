-- =====================================================
-- 019_gateway_access_logs_pg.sql — 网关访问审计日志表（迁移计划外缺口补齐）
-- 背景：2026-09-17 P3 清库重建暴露——ai.gateway_access_logs 此前仅由
--       Alembic 迁移 0008（backend/sql/alembic/memory/versions/）管理，
--       不在 init-dbs.sh 的 psql 首启链路中，清库后 GatewayLogIngest 报
--       `relation "ai.gateway_access_logs" does not exist`。
--       本迁移与 0008 内容对齐（幂等），保证纯 psql 首启也完整。
-- 数据链路：APISIX gateway-access-log.lua → Redis Stream agent:gw:access-log
--       → backend/observability/gateway_log_ingest.py 落库 →
--       /observability/gateway 管理端查询。
-- 目标库：agent_memory
-- 幂等：CREATE ... IF NOT EXISTS，可重复执行
-- =====================================================

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

-- agent_readonly 授权（与 004_readonly_role 口径一致；default privileges
-- 在 004 已配，此处显式补一遍以防只跑本文件的环境）
GRANT SELECT ON ALL TABLES IN SCHEMA ai TO agent_readonly;
