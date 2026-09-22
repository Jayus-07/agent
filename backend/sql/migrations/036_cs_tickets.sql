-- 036_cs_tickets.sql
-- 批次C：统一工单表。收编投诉工单（此前仅内存模拟）与转人工单（handoffs
-- 表 ticket_id 此前是游离字符串），提供落库、用户查询与状态流转的数据契约。
-- 本迁移只做加法：幂等创建，不依赖 Redis 或应用代码。

CREATE SCHEMA IF NOT EXISTS customer_service;

CREATE TABLE IF NOT EXISTS customer_service.tickets (
    id                  BIGSERIAL PRIMARY KEY,
    ticket_id           VARCHAR(64) NOT NULL,
    tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',
    -- complaint | handoff | inquiry | repair
    type                VARCHAR(30) NOT NULL DEFAULT 'inquiry',
    -- open | processing | pending_user | resolved | closed
    status              VARCHAR(30) NOT NULL DEFAULT 'open',
    -- ai | agent | user（谁建的单）
    source              VARCHAR(30) NOT NULL DEFAULT 'ai',
    conversation_id     VARCHAR(64) NOT NULL,
    user_id             VARCHAR(64) NOT NULL,
    -- type=handoff 时关联 handoffs.handoff_id
    handoff_id          VARCHAR(64),
    assigned_agent_id   VARCHAR(64),
    priority            VARCHAR(10) NOT NULL DEFAULT 'medium',
    title               VARCHAR(200) NOT NULL DEFAULT '',
    description         TEXT,
    resolution          TEXT,
    severity            VARCHAR(20),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_tickets_tenant_ticket_id
    ON customer_service.tickets (tenant_id, ticket_id);

-- 用户侧"我的工单"：按用户 + 状态过滤，时间倒序
CREATE INDEX IF NOT EXISTS idx_cs_tickets_user_status
    ON customer_service.tickets (tenant_id, user_id, status, created_at DESC);

-- 管理侧列表：按状态 + 优先级筛选
CREATE INDEX IF NOT EXISTS idx_cs_tickets_tenant_status
    ON customer_service.tickets (tenant_id, status, priority, created_at DESC);

-- 会话维度：会话关闭时联动查关联工单
CREATE INDEX IF NOT EXISTS idx_cs_tickets_conversation
    ON customer_service.tickets (conversation_id);

-- 派单联动：handoff_id 反查工单
CREATE INDEX IF NOT EXISTS idx_cs_tickets_handoff
    ON customer_service.tickets (handoff_id)
    WHERE handoff_id IS NOT NULL;

-- ── 收编回填：存量 handoffs 的 ticket_id（HANDOFF-xxx）补录为工单 ──
-- 幂等：ON CONFLICT DO NOTHING（uq_cs_tickets_tenant_ticket_id 去重）
INSERT INTO customer_service.tickets
    (ticket_id, tenant_id, type, status, source, conversation_id,
     user_id, handoff_id, priority, title, description, created_at, updated_at)
SELECT h.ticket_id, h.tenant_id, 'handoff',
       CASE WHEN h.handoff_state = 'closed' THEN 'closed' ELSE 'processing' END,
       'ai', h.conversation_id, h.user_id, h.handoff_id,
       CASE WHEN h.priority >= 70 THEN 'high'
            WHEN h.priority <= 30 THEN 'low' ELSE 'medium' END,
       COALESCE(LEFT(h.trigger_reason, 200), '人工转接工单'),
       h.trigger_reason,
       h.created_at, h.updated_at
FROM customer_service.handoffs h
WHERE h.ticket_id IS NOT NULL AND h.ticket_id <> ''
ON CONFLICT (tenant_id, ticket_id) DO NOTHING;
