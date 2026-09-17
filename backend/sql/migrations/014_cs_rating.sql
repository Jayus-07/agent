-- 014_cs_rating.sql — 智能客服满意度评价（需求 ⑥：管理后台满意度统计）
-- 目标库：customer_service schema（conversations 表由 006 创建）
-- 执行方式：
--   1) docker/init-dbs.sh 空卷首启自动应用
--   2) 存量库手工：psql -U postgres -d agent_memory -f /docker-migrations/014_cs_rating.sql
-- 说明：
--   - 应用侧 ORM（customer_service/models/conversation.py CSConversation）同步加了同名列，
--     幂等列添加（duplicate_column 容忍），可重复执行
--   - rating 语义：1-5 星（SMALLINT），NULL=未评价；rated_at 评分时间

DO $$
BEGIN
    ALTER TABLE customer_service.conversations
        ADD COLUMN rating SMALLINT;
EXCEPTION
    WHEN duplicate_column THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE customer_service.conversations
        ADD COLUMN rating_comment TEXT;
EXCEPTION
    WHEN duplicate_column THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE customer_service.conversations
        ADD COLUMN rated_at TIMESTAMPTZ;
EXCEPTION
    WHEN duplicate_column THEN NULL;
END $$;

-- 统计聚合常用过滤：已评价会话
CREATE INDEX IF NOT EXISTS idx_cs_conv_rating
    ON customer_service.conversations(rating)
    WHERE rating IS NOT NULL;
