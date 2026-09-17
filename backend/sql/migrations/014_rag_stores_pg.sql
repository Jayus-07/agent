-- 014_rag_stores_pg.sql — RAG 索引族存储（迁移计划 2026-09-17 Batch B）
-- 目标库：agent_memory（与 doc_registry/010 同层）
-- 执行方式：init-dbs.sh 空卷首启 / 存量库手工 psql 同 012
-- 说明：
--   - chunk_store：Chunk 文本持久化（Trace 详情页展示用，不参与检索链路）
--   - keyword_rules：关键词规则动态管理（首启为空表时由应用侧导入种子数据）
--   - doc_operation_log：文档管理操作审计日志
--   - 应用侧各 PG 实现 _init_db 幂等建表，语句与本文件一致
--   - created_at/updated_at 由应用侧生成 UTC 文本（等价 SQLite datetime('now')）

-- ── 1/3 chunk_store ──
CREATE TABLE IF NOT EXISTS chunk_store (
    id                   BIGSERIAL PRIMARY KEY,
    doc_id               TEXT NOT NULL,
    chunk_index          INTEGER NOT NULL DEFAULT 0,
    content              TEXT NOT NULL DEFAULT '',
    char_count           INTEGER NOT NULL DEFAULT 0,
    keywords             TEXT NOT NULL DEFAULT '',
    llm_keywords         TEXT NOT NULL DEFAULT '',
    llm_model            TEXT NOT NULL DEFAULT '',
    section_title        TEXT NOT NULL DEFAULT '',
    doc_type             TEXT NOT NULL DEFAULT '',
    kb_id                TEXT NOT NULL DEFAULT '',
    department           TEXT NOT NULL DEFAULT '',
    simulated_questions  TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chunk_store_doc_id ON chunk_store(doc_id);

-- ── 2/3 keyword_rules ──
CREATE TABLE IF NOT EXISTS keyword_rules (
    id         BIGSERIAL PRIMARY KEY,
    keyword    TEXT NOT NULL,
    doc_type   TEXT NOT NULL DEFAULT 'general',
    category   TEXT NOT NULL DEFAULT '',
    weight     INTEGER NOT NULL DEFAULT 1,
    enabled    INTEGER NOT NULL DEFAULT 1,
    source     TEXT NOT NULL DEFAULT 'seed',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_keyword_rules_enabled  ON keyword_rules(enabled);
CREATE INDEX IF NOT EXISTS idx_keyword_rules_doc_type ON keyword_rules(doc_type);
CREATE INDEX IF NOT EXISTS idx_keyword_rules_category ON keyword_rules(category);

-- ── 3/3 doc_operation_log ──
CREATE TABLE IF NOT EXISTS doc_operation_log (
    id           BIGSERIAL PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    doc_name     TEXT NOT NULL,
    operation    TEXT NOT NULL,
    user_id      TEXT DEFAULT 'anonymous',
    source       TEXT,
    trace_id     TEXT,
    batch_id     TEXT,
    result       TEXT DEFAULT 'success',
    detail       TEXT,
    duration_ms  INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dop_created  ON doc_operation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dop_doc      ON doc_operation_log(doc_id);
CREATE INDEX IF NOT EXISTS idx_dop_operation ON doc_operation_log(operation);
CREATE INDEX IF NOT EXISTS idx_dop_batch    ON doc_operation_log(batch_id);
