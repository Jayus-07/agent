-- 128_cs_qa_scores.sql — 质检中心抽检评分表（cs-workstation-v2 P6，agent_memory 库）

-- 每日抽样任务一行 = 一次会话抽检：抽样时以 AI 预填建议创建（source=ai），
-- 人工评分/提交后 source=human。UNIQUE(tenant_id, conversation_id) 保证同一
-- 会话全生命周期至多被抽检一次（抽样去重的唯一事实源，重跑幂等）。
-- 申诉状态机不可回退：none → appealing → upheld|overturned（终态锁定）；
-- 改判写 final_score/final_verdict，原始 score_* 列保留作审计对照。
CREATE TABLE IF NOT EXISTS customer_service.cs_qa_scores (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    batch_date DATE NOT NULL,
    source VARCHAR(16) NOT NULL DEFAULT 'ai',
    score_service SMALLINT,
    score_accuracy SMALLINT,
    score_compliance SMALLINT,
    score_timeliness SMALLINT,
    total_score SMALLINT,
    verdict VARCHAR(16),
    note TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    scored_by VARCHAR(64),
    scored_at TIMESTAMPTZ,
    ai_prefill JSONB,
    appeal_status VARCHAR(16) NOT NULL DEFAULT 'none',
    appeal_reason TEXT,
    appeal_requested_by VARCHAR(64),
    appeal_requested_at TIMESTAMPTZ,
    appeal_decision VARCHAR(16),
    appeal_opinion TEXT,
    appeal_decided_by VARCHAR(64),
    appeal_decided_at TIMESTAMPTZ,
    final_score SMALLINT,
    final_verdict VARCHAR(16),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_qa_score_status CHECK (status IN ('pending', 'draft', 'scored')),
    CONSTRAINT ck_qa_score_source CHECK (source IN ('ai', 'human')),
    CONSTRAINT ck_qa_score_verdict CHECK (verdict IN ('pass', 'fail')),
    CONSTRAINT ck_qa_appeal_status CHECK (
        appeal_status IN ('none', 'appealing', 'upheld', 'overturned')
    ),
    CONSTRAINT ck_qa_appeal_decision CHECK (
        appeal_decision IS NULL OR appeal_decision IN ('upheld', 'overturned')
    ),
    CONSTRAINT ck_qa_final_verdict CHECK (
        final_verdict IS NULL OR final_verdict IN ('pass', 'fail')
    ),
    CONSTRAINT uq_qa_score_conversation UNIQUE (tenant_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_qa_score_tenant_status
    ON customer_service.cs_qa_scores (tenant_id, status, batch_date);
CREATE INDEX IF NOT EXISTS idx_qa_score_agent
    ON customer_service.cs_qa_scores (tenant_id, agent_id, batch_date);
