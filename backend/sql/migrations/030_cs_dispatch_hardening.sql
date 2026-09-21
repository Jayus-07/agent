-- 030_cs_dispatch_hardening.sql
-- 派单治理（2026-09-21）：技能匹配 + 拒单原因。
-- 本迁移只做加法，全部幂等（ADD COLUMN IF NOT EXISTS），可安全重放。

-- 1) 坐席技能组标签：派单时与 handoffs.required_skill 精确匹配。
--    存量行由 DEFAULT 统一回填 'general'，行为与迁移前一致。
ALTER TABLE customer_service.cs_agents
    ADD COLUMN IF NOT EXISTS skill VARCHAR(32) NOT NULL DEFAULT 'general';

-- 2) 工单所需技能组：入池默认 'general'（不指定技能 = 任意坐席可接）。
ALTER TABLE customer_service.handoffs
    ADD COLUMN IF NOT EXISTS required_skill VARCHAR(32) NOT NULL DEFAULT 'general';

-- 3) 拒单原因：decline 接口可选提交，仅新增列，不改状态机。
ALTER TABLE customer_service.assignments
    ADD COLUMN IF NOT EXISTS decline_reason VARCHAR(255);

-- 技能匹配查询的选择性索引：dispatcher 每秒按 tenant + skill 过滤候选。
CREATE INDEX IF NOT EXISTS idx_cs_agent_tenant_skill
    ON customer_service.cs_agents (tenant_id, skill);
