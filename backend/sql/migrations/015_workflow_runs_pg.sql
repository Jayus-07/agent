-- =====================================================
-- 015_workflow_runs_pg.sql — workflow_runs（迁移计划 Batch C）
-- 目标库：agent_memory（Agent 自身运行状态，不暴露给 NL2SQL）
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行
-- 对应实现：backend/orchestration/workflow/persistence_pg.py
-- =====================================================

CREATE TABLE IF NOT EXISTS workflow_runs (
    id            TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    duration_ms   BIGINT,
    inputs_json   TEXT,
    outputs_json  TEXT,
    error         TEXT,
    trace_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_workflow ON workflow_runs(workflow_name, started_at DESC);
