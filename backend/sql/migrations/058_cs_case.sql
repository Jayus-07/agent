-- 058_cs_case.sql — 客服统一案件表（迁移 B12，设计方案 §10.1 cs_case 核心字段）
-- 目标：聊天 ≠ 问题——工单/投诉/售后收敛为统一案件实体（cs_case），承接
--   「创建 → 分配 → 处理 → 关闭」核心生命周期；SLA 按 P0/P1/P2 由服务层
--   计算落 sla_deadline_at（5min/30min/24h，设计方案 §4.6）。
-- 口径：
--   status      open | waiting_user | processing | resolved | closed
--   priority    P0 | P1 | P2
--   case_type   refund | return | exchange | complaint | inquiry
-- 防重入：同会话同类型只允许一个活跃案件（部分唯一索引，并发由 DB 决胜，
--   对齐 051 确认链守卫模式）；closed/resolved 后允许同会话重新进线建新案。
-- 过渡策略（设计方案 §14.2）：与 tickets 表并存，complaint 源先并行写入，
--   ticket/after_sales 全面并表走后续批次。

CREATE TABLE IF NOT EXISTS customer_service.cs_case (
    -- id 自增主键与 ORM（models/case.py，跟随 tickets/confirmations 表形态）；
    -- case_id UUID 为对外唯一标识。两处 DDL↔ORM 必须成对改。
    id                      BIGSERIAL PRIMARY KEY,
    case_id                 UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id               TEXT NOT NULL DEFAULT 'default',
    conversation_id         TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    case_type               TEXT NOT NULL,
    priority                TEXT NOT NULL DEFAULT 'P2',
    status                  TEXT NOT NULL DEFAULT 'open',
    title                   TEXT NOT NULL DEFAULT '',
    context                 JSONB NOT NULL DEFAULT '{}',
    owner_agent_id          TEXT,
    related_confirmation_id TEXT,
    resolution              TEXT,
    sla_deadline_at         TIMESTAMPTZ,
    first_responded_at      TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cs_case_conversation
    ON customer_service.cs_case (conversation_id);
CREATE INDEX IF NOT EXISTS idx_cs_case_status
    ON customer_service.cs_case (tenant_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_cs_case_user
    ON customer_service.cs_case (user_id, created_at DESC);

-- 同会话同类型活跃案件唯一（并发决胜在 DB，应用层不做先查后插）
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_case_active
    ON customer_service.cs_case (conversation_id, case_type)
    WHERE status IN ('open', 'waiting_user', 'processing');
