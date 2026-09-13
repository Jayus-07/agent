-- 007_tool_approval.sql — 写操作工具审批单（human-in-the-loop）
-- 库: agent_business；schema: ai
-- 对应模块: backend/security/tool_approval.py
-- 状态机: pending → approved → executed
--                 ↘ rejected
-- approved 单在 TOOL_APPROVAL_TTL_SECONDS 内可被同指纹重试消费（→ executed）

CREATE TABLE IF NOT EXISTS ai.tool_approval_requests (
    id UUID PRIMARY KEY,
    tool_name TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    user_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'executed')),
    reviewer TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ
);

-- 同指纹同时最多一张 pending 单（幂等建单，仅约束 pending 态；
-- executed/rejected 可有多条历史记录，不能用全列 UNIQUE）
CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_approval_fingerprint_pending
    ON ai.tool_approval_requests(fingerprint) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_tool_approval_status
    ON ai.tool_approval_requests(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tool_approval_fingerprint
    ON ai.tool_approval_requests(fingerprint);
