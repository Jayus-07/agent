-- 010_doc_registry_pg.sql — RAG 文档注册表（R1/C19：registry SQLite→PostgreSQL）
-- 目标库：agent_memory（Agent 自身元数据库，与 chat/memory 表同层）
-- 执行方式：
--   1) 容器内：psql -U postgres -d agent_memory -f /docker-migrations/010_doc_registry_pg.sql
--   2) 或依赖应用侧幂等建表（PostgresDocumentRegistry._init_db，语句与本文件一致）
-- 表名可经 DOC_REGISTRY_PG_TABLE 覆盖（默认 doc_registry；本脚本按默认名书写）。
-- 说明：
--   - 时间列保持 TEXT，写入 to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')，
--     与 SQLite datetime('now') 的文本格式及排序语义一致，迁移后无需类型转换。
--   - expire_at 直接内置（SQLite 侧由 ensure_expire_at_column 惰性补列）。
--   - R4 版本治理字段（version_id/effective_from/supersedes_version_id/source_priority/
--     quality_status）在本表基础上增量迁移，本文件不预建（任务书 §12.6：不提前换库）。

CREATE TABLE IF NOT EXISTS doc_registry (
    file_path    TEXT PRIMARY KEY,
    file_name    TEXT NOT NULL,
    kb_id        TEXT NOT NULL,
    doc_id       TEXT NOT NULL,
    file_hash    TEXT NOT NULL,
    file_size    BIGINT NOT NULL DEFAULT 0,
    file_mtime   DOUBLE PRECISION NOT NULL DEFAULT 0,
    chunk_count  INTEGER DEFAULT 0,
    chunk_ids    TEXT DEFAULT '[]',
    doc_db_id    TEXT,
    doc_type     TEXT DEFAULT 'general',
    confidence   DOUBLE PRECISION DEFAULT 0,
    llm_used     INTEGER DEFAULT 0,
    quality_score DOUBLE PRECISION DEFAULT 0,
    quality_issues TEXT DEFAULT '',
    embedding_model TEXT DEFAULT '',
    minhash_sig  TEXT DEFAULT '',
    near_dup_id  TEXT DEFAULT '',
    status       TEXT DEFAULT 'active',
    last_indexed TEXT,
    created_at   TEXT DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at   TEXT DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    metadata_fingerprint TEXT DEFAULT '',
    doc_version  INTEGER DEFAULT 1,
    kb_version   TEXT DEFAULT 'v1',
    department   TEXT DEFAULT '',
    summary      TEXT DEFAULT '',
    keywords     TEXT DEFAULT '',
    time_refs    TEXT DEFAULT '',
    business_domain TEXT DEFAULT '',
    complexity   TEXT DEFAULT '',
    permission_scope TEXT DEFAULT 'general',
    expire_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_doc_registry_doc_id ON doc_registry(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_registry_kb_id ON doc_registry(kb_id);
CREATE INDEX IF NOT EXISTS idx_doc_registry_status ON doc_registry(status);
