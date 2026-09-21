-- ============================================================
-- 032_memory_embedding_vector.sql — Memory L3 embedding 列修复
--
-- 背景：memory_records.embedding 历史上是 bytea（早期建表类型错误），
-- 与 pgvector cosine 检索（<=>）不兼容，L3 检索始终报错
-- 「bytea <=> unknown」→ start_session 整体失败。
--
-- 事实（2026-09-21 核对）：存量 3 行、非空 embedding = 0 行，
-- 无需迁移旧数据，USING NULL::vector(1024) 直接换型。
-- 目标维度 = text-embedding-v3 的 1024 维，与 ORM（pgvector.sqlalchemy
-- Vector(1024)）及 RAG 向量库 rag_vectors vector(1024) 一致。
--
-- 目标库：agent_memory
-- 幂等：DO 块判断列实际类型，仅 bytea 时才 ALTER，可安全重放。
-- ============================================================

-- 全新环境兜底：扩展此前为手工安装，迁移体系无对应物
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'memory_records'
          AND column_name = 'embedding'
          AND udt_name = 'bytea'
    ) THEN
        ALTER TABLE memory_records
            ALTER COLUMN embedding TYPE vector(1024)
            USING NULL::vector(1024);
    END IF;
END
$$;
