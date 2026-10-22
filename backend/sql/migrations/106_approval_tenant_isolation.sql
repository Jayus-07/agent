-- 106_approval_tenant_isolation.sql — 审批单只对所属租户管理员可见和处置。
ALTER TABLE ai.tool_approval_requests
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
UPDATE ai.tool_approval_requests SET tenant_id = 'default'
WHERE tenant_id IS NULL OR tenant_id = '';
CREATE INDEX IF NOT EXISTS idx_tool_approval_tenant_status
    ON ai.tool_approval_requests(tenant_id, status, created_at DESC);
