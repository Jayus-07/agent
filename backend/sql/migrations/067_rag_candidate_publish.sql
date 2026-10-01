-- 066: RAG 候选版本模型（2026-10-01 上传并发/幂等/失败回滚收口 B 阶段）
--
-- 1) doc_registry.active_generation：逻辑文档的「已发布代次」指针。
--    候选索引全部落在 generation 隔离层（向量候选 collection / BM25 staging
--    目录），发布时以 register_published 的条件 upsert（CAS）一次性切换——
--    并发候选后到者 CAS 失败被拒（superseded），过期 Worker 无法覆盖新发布。
--    应用侧 PostgresDocumentRegistry._ensure_columns 亦会惰性补列（双保险）。
-- 2) rag_index_runs：一次上传 = 一条运行记录（候选状态权威，与 doc_registry
--    同库）。status 状态机 claimed→indexing→publishing→published；终态一次写
--    （failed/superseded/published 之后的回写被仓储层拒绝）——重试不得重复
--    发布，幂等重放不能把终态洗回中间态。

ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS active_generation TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS rag_index_runs (
    upload_id    TEXT PRIMARY KEY,
    generation   TEXT NOT NULL DEFAULT '',
    file_path    TEXT NOT NULL DEFAULT '',
    doc_id       TEXT NOT NULL DEFAULT '',
    kb_id        TEXT NOT NULL DEFAULT '',
    department   TEXT NOT NULL DEFAULT '',
    file_hash    TEXT NOT NULL DEFAULT '',
    staging_path TEXT NOT NULL DEFAULT '',
    tenant_id    TEXT NOT NULL DEFAULT '',
    actor_id     TEXT NOT NULL DEFAULT '',
    base_generation TEXT NOT NULL DEFAULT '',
    celery_task_id  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'claimed',
    stage        TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    created_at   TEXT DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at   TEXT DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_rag_index_runs_file_path ON rag_index_runs(file_path);
CREATE INDEX IF NOT EXISTS idx_rag_index_runs_status ON rag_index_runs(status);
