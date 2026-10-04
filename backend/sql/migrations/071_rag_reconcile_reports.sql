-- 每次对账一条轻量报告；不一致明细保存为 JSONB，不复制索引内容。
CREATE SCHEMA IF NOT EXISTS ai;
CREATE TABLE IF NOT EXISTS ai.rag_reconcile_reports (
    id BIGSERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consistent BOOLEAN NOT NULL,
    issue_count INTEGER NOT NULL CHECK (issue_count >= 0),
    bm25_status TEXT NOT NULL CHECK (bm25_status IN ('available', 'unavailable')),
    detail JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rag_reconcile_reports_run_at
    ON ai.rag_reconcile_reports (run_at DESC);
