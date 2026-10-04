-- 076_cs_faq_tables.sql — FAQ 精准匹配层与缺口台账建表迁移化（E5，2026-10-05）
--
-- 口径：
-- * ai.cs_faq / ai.cs_faq_query_log 此前由 faq.py 运行时惰性建表
--   （CREATE TABLE IF NOT EXISTS），游离在迁移体系外——E5（惰性建表
--   迁移化）把 DDL 收编为正式迁移；本文件为唯一权威，faq.py 的惰性
--   建表保留为存量环境兜底（幂等 IF NOT EXISTS，语句逐字一致防漂移）。
-- * cs_faq_query_log 是缺口周检（cs_faq_gap_review / C6/A8）的数据源，
--   matched=false 行是缺口闭环的输入，删表即断缺口台账。
-- * 索引：缺口周检按 created_at 回看窗口 + matched 过滤；
--   cs_faq 按 status 过滤（published 计数）与 faq_key 精确匹配（UNIQUE）。
--
-- 回滚（down）：
--   DROP TABLE IF EXISTS ai.cs_faq_query_log;
--   DROP TABLE IF EXISTS ai.cs_faq;
--   （cs_faq_query_log 依赖 faq_id 逻辑关联 cs_faq，先删子表）

CREATE TABLE IF NOT EXISTS ai.cs_faq (
    id          BIGSERIAL PRIMARY KEY,
    faq_key     TEXT NOT NULL UNIQUE,
    question    TEXT NOT NULL,
    variants    TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    kb_refs     TEXT NOT NULL DEFAULT '',
    valid_until TEXT,
    hit_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai.cs_faq_query_log (
    id           BIGSERIAL PRIMARY KEY,
    question     TEXT NOT NULL,
    matched      BOOLEAN NOT NULL,
    faq_id       BIGINT,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cs_faq_query_log_created
    ON ai.cs_faq_query_log(created_at);

CREATE INDEX IF NOT EXISTS idx_cs_faq_query_log_matched
    ON ai.cs_faq_query_log(matched, created_at);

CREATE INDEX IF NOT EXISTS idx_cs_faq_status
    ON ai.cs_faq(status);
