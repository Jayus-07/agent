-- 012_obs_trace_store_pg.sql — 可观测层 trace_store（迁移计划 2026-09-17 Batch A）
-- 目标库：agent_memory（Agent 自身元数据，与 doc_registry/010 同层）
-- 执行方式：
--   1) docker/init-dbs.sh 空卷首启自动应用
--   2) 存量库手工：psql -U postgres -d agent_memory -f /docker-migrations/012_obs_trace_store_pg.sql
-- 说明：
--   - 应用侧 PostgresTraceStore._init_db 幂等建表，语句与本文件一致（权威 schema 双写对齐）
--   - created_at 为应用侧生成的本地时间文本（与 SQLite 版语义一致），不用服务器时钟
--   - rejected 从 JSON metadata.rejection.rejected 同源提取为列（写入时计算，无需 JSON1）

CREATE TABLE IF NOT EXISTS trace_store (
    trace_id      TEXT PRIMARY KEY,
    data          TEXT NOT NULL,      -- TraceRecord 序列化 JSON
    created_at    TEXT NOT NULL,
    session_id    TEXT DEFAULT '',
    status        TEXT DEFAULT '',
    workflow_name TEXT DEFAULT '',
    rejected      INTEGER DEFAULT 0,  -- 0/1（Evidence Gate 拒答）
    duration_ms   INTEGER DEFAULT 0,
    parent_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_trace_store_created ON trace_store(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_store_session ON trace_store(session_id);
CREATE INDEX IF NOT EXISTS idx_trace_store_status  ON trace_store(status);
CREATE INDEX IF NOT EXISTS idx_trace_store_rejected ON trace_store(rejected);
