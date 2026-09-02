-- =============================================
-- 006_customer_service.sql
-- 客服系统 schema + 核心表
-- 依赖: 004_readonly_role.sql (agent_readonly 角色)
-- =============================================

-- 客服 schema
CREATE SCHEMA IF NOT EXISTS customer_service;

-- =============================================
-- 客服会话表
-- =============================================
CREATE TABLE IF NOT EXISTS customer_service.conversations (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL UNIQUE,
    user_id         VARCHAR(64) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'waiting_human', 'human_active', 'closed')),
    channel         VARCHAR(20) NOT NULL DEFAULT 'web'
                    CHECK (channel IN ('web', 'app', 'phone', 'wechat')),
    assigned_agent  VARCHAR(64),
    ai_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_conv_user
    ON customer_service.conversations (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_conv_status
    ON customer_service.conversations (status) WHERE status != 'closed';
CREATE INDEX IF NOT EXISTS idx_cs_conv_agent
    ON customer_service.conversations (assigned_agent)
    WHERE assigned_agent IS NOT NULL;

-- =============================================
-- 客服消息表
-- =============================================
CREATE TABLE IF NOT EXISTS customer_service.messages (
    id              BIGSERIAL PRIMARY KEY,
    message_id      VARCHAR(64) NOT NULL UNIQUE,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id)
                    ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system', 'human_agent')),
    content         TEXT NOT NULL,
    intent_domain   VARCHAR(30),
    intent_name     VARCHAR(50),
    confidence      FLOAT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cs_msg_conv
    ON customer_service.messages (conversation_id, created_at);

-- =============================================
-- 客服操作记录表
-- =============================================
CREATE TABLE IF NOT EXISTS customer_service.agent_actions (
    id              BIGSERIAL PRIMARY KEY,
    action_id       VARCHAR(64) NOT NULL UNIQUE,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id),
    user_id         VARCHAR(64) NOT NULL,
    action_type     VARCHAR(30) NOT NULL
                    CHECK (action_type IN (
                        'refund_request', 'refund_execute',
                        'return_request', 'return_execute',
                        'address_update', 'order_cancel',
                        'complaint_create', 'handoff_request',
                        'handoff_accept', 'handoff_close'
                    )),
    target_type     VARCHAR(30),
    target_id       VARCHAR(64),
    before_state    JSONB,
    after_state     JSONB,
    confirmation_state VARCHAR(20) NOT NULL
                    CHECK (confirmation_state IN (
                        'not_required', 'pending', 'confirmed',
                        'cancelled', 'executing', 'success', 'failed', 'expired'
                    )),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'executing', 'success', 'failed', 'cancelled')),
    executed_by     VARCHAR(20) NOT NULL DEFAULT 'ai'
                    CHECK (executed_by IN ('ai', 'human', 'system')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_action_conv
    ON customer_service.agent_actions (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cs_action_user
    ON customer_service.agent_actions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_action_pending
    ON customer_service.agent_actions (status)
    WHERE status IN ('pending', 'executing');

-- =============================================
-- 审计日志表
-- =============================================
CREATE TABLE IF NOT EXISTS customer_service.audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    log_id          VARCHAR(64) NOT NULL UNIQUE,
    user_id         VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64),
    action_id       VARCHAR(64),
    actor_type      VARCHAR(20) NOT NULL
                    CHECK (actor_type IN ('ai', 'human', 'system')),
    actor_id        VARCHAR(64),
    action          VARCHAR(50) NOT NULL,
    resource_type   VARCHAR(30),
    resource_id     VARCHAR(64),
    before_state    JSONB,
    after_state     JSONB,
    result          VARCHAR(20) NOT NULL
                    CHECK (result IN ('success', 'failure', 'denied', 'error')),
    error_detail    TEXT,
    ip_address      VARCHAR(45),
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cs_audit_user
    ON customer_service.audit_logs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_audit_action
    ON customer_service.audit_logs (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_audit_resource
    ON customer_service.audit_logs (resource_type, resource_id)
    WHERE resource_type IS NOT NULL;

-- =============================================
-- 投诉工单表
-- =============================================
CREATE TABLE IF NOT EXISTS customer_service.complaints (
    id              BIGSERIAL PRIMARY KEY,
    complaint_id    VARCHAR(64) NOT NULL UNIQUE,
    user_id         VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id),
    category        VARCHAR(30) NOT NULL
                    CHECK (category IN (
                        'service_quality', 'product_quality',
                        'logistics', 'refund_dispute', 'other'
                    )),
    severity        VARCHAR(10) NOT NULL DEFAULT 'medium'
                    CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    description     TEXT NOT NULL,
    context_summary TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'investigating', 'resolved', 'escalated', 'closed')),
    assigned_to     VARCHAR(64),
    resolution      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_complaint_user
    ON customer_service.complaints (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_complaint_status
    ON customer_service.complaints (status) WHERE status NOT IN ('closed', 'resolved');

-- =============================================
-- 确认状态表（持久化确认状态机）
-- =============================================
CREATE TABLE IF NOT EXISTS customer_service.confirmations (
    id              BIGSERIAL PRIMARY KEY,
    confirmation_id VARCHAR(64) NOT NULL UNIQUE,
    conversation_id VARCHAR(64) NOT NULL
                    REFERENCES customer_service.conversations(conversation_id),
    user_id         VARCHAR(64) NOT NULL,
    action_type     VARCHAR(30) NOT NULL,
    target_type     VARCHAR(30) NOT NULL,
    target_id       VARCHAR(64) NOT NULL,
    proposal        JSONB NOT NULL,
    state           VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (state IN (
                        'pending', 'confirmed', 'cancelled',
                        'executing', 'success', 'failed', 'expired'
                    )),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    confirmed_at    TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cs_confirm_pending
    ON customer_service.confirmations (user_id, state)
    WHERE state = 'pending';

-- =============================================
-- 权限: agent_readonly 只读访问客服 schema
-- =============================================
GRANT USAGE ON SCHEMA customer_service TO agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA customer_service TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA customer_service
    GRANT SELECT ON TABLES TO agent_readonly;
