-- Migration: 156_rag_vectors_tsv_gin.sql
-- 目标：为 rag_vectors 表增加 tsv_content tsvector 列并创建 GIN 倒排索引
-- 实现零额外内存、毫秒级原子行级增量全文检索，彻底解决单机 pickle BM25 全量重构 OOM 隐患。

DO $$
BEGIN
    -- 若 rag_vectors 表已存在（存量环境或主流程），增量添加 tsv_content 与 GIN 索引
    -- 若表尚未建立（如干净 CI 环境下的 init_db.py），由 pgvector_store._init_db 在首次初始化时幂等创建
    IF to_regclass('rag_vectors') IS NOT NULL THEN
        -- 1. 检查并添加 tsv_content 列
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'rag_vectors' AND column_name = 'tsv_content'
        ) THEN
            ALTER TABLE rag_vectors ADD COLUMN tsv_content tsvector;
        END IF;

        -- 2. 创建 GIN 倒排索引
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes 
            WHERE tablename = 'rag_vectors' AND indexname = 'idx_rag_vectors_tsv'
        ) THEN
            CREATE INDEX idx_rag_vectors_tsv ON rag_vectors USING gin(tsv_content);
        END IF;
    END IF;
END $$;

