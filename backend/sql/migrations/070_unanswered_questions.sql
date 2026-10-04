-- 070_unanswered_questions.sql — 业务拒答未答问题登记表（2026-10-03）
--
-- 为什么存在：主图 RAG 未命中 / SQL 空结果此前零留痕，知识缺口不可运营。
-- 写入方：backend/observability/unanswered.py（旁路软失败，照 M9 口径）。
-- 消费方：GET /api/admin/unanswered/*（知识运营聚类补录的输入）。

CREATE TABLE IF NOT EXISTS ai.unanswered_questions (
    id             BIGSERIAL PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id      VARCHAR(128) NOT NULL DEFAULT '',
    user_id        VARCHAR(128) NOT NULL DEFAULT '',
    session_id     VARCHAR(128) NOT NULL DEFAULT '',
    department     VARCHAR(128) NOT NULL DEFAULT '',
    kb_id          VARCHAR(128) NOT NULL DEFAULT '',
    trace_id       VARCHAR(64)  NOT NULL DEFAULT '',
    request_id     VARCHAR(64)  NOT NULL DEFAULT '',
    question       TEXT         NOT NULL,
    -- 归一化问题哈希（去空白+小写 sha1 前 32 位），供运营侧聚类去重
    question_hash  VARCHAR(32)  NOT NULL,
    source         VARCHAR(32)  NOT NULL,          -- rag_miss | sql_empty
    detail         JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_unanswered_created_at
    ON ai.unanswered_questions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_unanswered_hash
    ON ai.unanswered_questions (question_hash);
CREATE INDEX IF NOT EXISTS idx_unanswered_source_time
    ON ai.unanswered_questions (source, created_at DESC);
