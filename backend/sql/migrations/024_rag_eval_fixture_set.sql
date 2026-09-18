-- 024_rag_eval_fixture_set.sql — 评测语料范围元数据
-- （编号 2026-09-19 由 022 改为 024：main 已占用 022_feedback_review_candidates 与 023_auth_sessions，避免撞号）
-- fixture_set 仅用于 RAG 评测范围与诊断；生产文档默认空值，不能替代权限字段。

ALTER TABLE doc_registry
    ADD COLUMN IF NOT EXISTS fixture_set TEXT DEFAULT '';

ALTER TABLE chunk_store
    ADD COLUMN IF NOT EXISTS fixture_set TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_doc_registry_fixture_set
    ON doc_registry(fixture_set);
