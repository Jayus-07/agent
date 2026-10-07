-- 080_question_ledger.sql — 线上问题台账（2026-10-08 #13，候选评测集供给侧）
--
-- 口径：
-- * 目标：线上各域用户问题统一落一张台账（data-explorer「问题收集」tab 的
--   数据源），按域（travel/sql/planner/cs/rag/ai_assistant）积累候选评测集
--   原料；写入走进程内旁路软失败（observability/question_ledger.py），
--   不新建 Celery 队列（写入量=每请求 1 行，进程内足够）。
-- * question 存掩码后原文（shared/pii_mask 统一出口，写入前处理）；
--   question_hash = sha256(规范化问题)[:16]，供写入侧 24h 同域去重。
-- * status 是运营状态机：pending→accepted（转候选评测集）/dismissed（忽略），
--   转候选走 evaluation_datasets.register_dataset_candidate（source_type=
--   "online_ledger"），approve 后进不可变版本目录。
-- * 索引：管理端列表按 (domain, status, created_at DESC) 筛选分页；
--   去重检查按 (question_hash, created_at DESC)。
--
-- 回滚（down）：
--   DROP TABLE IF EXISTS ai.question_ledger;

CREATE TABLE IF NOT EXISTS ai.question_ledger (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      TEXT        NOT NULL DEFAULT 'default',
    user_id        TEXT        NOT NULL DEFAULT '',
    session_id     TEXT        NOT NULL DEFAULT '',
    domain         TEXT        NOT NULL,
    question       TEXT        NOT NULL,
    question_hash  TEXT        NOT NULL,
    answer_summary TEXT        NOT NULL DEFAULT '',
    trace_id       TEXT        NOT NULL DEFAULT '',
    source         TEXT        NOT NULL DEFAULT 'chat',
    status         TEXT        NOT NULL DEFAULT 'pending',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_question_ledger_domain CHECK (
        domain IN ('travel', 'sql', 'planner', 'cs', 'rag', 'ai_assistant')),
    CONSTRAINT ck_question_ledger_status CHECK (
        status IN ('pending', 'accepted', 'dismissed'))
);

CREATE INDEX IF NOT EXISTS idx_question_ledger_domain_status
    ON ai.question_ledger (domain, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_question_ledger_hash
    ON ai.question_ledger (question_hash, created_at DESC);
