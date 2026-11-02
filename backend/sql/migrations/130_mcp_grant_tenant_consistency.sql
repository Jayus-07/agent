-- 130_mcp_grant_tenant_consistency.sql — MCP grant 租户一致性 DB 级防线（S1 审计修复）
-- 历史：114 的 grants(tool_id)→tools(id) 外键不含 tenant 一致性，capability_slug
-- 全局唯一，B 租户可经 bundle 把执行授权绑到 A 租户工具（跨租户 runtime/
-- connection/凭据使用）。应用层已收紧（grant 写入租户过滤 + 读快照
-- t.tenant_id = g.tenant_id 默认拒绝），本迁移补 DB 级约束并收口存量脏行。

-- 1) 组合外键的被引用侧唯一索引（tools.id 是主键，加 (id, tenant_id) 组合唯一）
CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_tools_id_tenant
    ON ai.mcp_tools(id, tenant_id);

-- 2) 存量错配 grant 一律失效：保留行作审计痕迹（append 痕迹可比删除可追），
--    enabled=false 后读快照与写复核均不再放行（幂等：重跑影响 0 行）。
UPDATE ai.mcp_tool_grants g
SET enabled = false, revoked_at = COALESCE(revoked_at, now())
FROM ai.mcp_tools t
WHERE g.tool_id = t.id AND g.tenant_id <> t.tenant_id AND g.enabled;

-- 3) 组合外键（NOT VALID：不锁表校验存量——步骤 2 已收口，但 NOT VALID
--    保证即使存在其他形态历史行迁移也不失败；约束对新增/更新立即生效）。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_mcp_grants_tool_tenant'
          AND conrelid = 'ai.mcp_tool_grants'::regclass
    ) THEN
        ALTER TABLE ai.mcp_tool_grants
            ADD CONSTRAINT fk_mcp_grants_tool_tenant
            FOREIGN KEY (tool_id, tenant_id)
            REFERENCES ai.mcp_tools(id, tenant_id)
            ON DELETE CASCADE NOT VALID;
    END IF;
END $$;
