-- 126_cs_agent_status_sla.sql — 坐席工作状态 + handoff SLA 分级标记
-- （cs-workstation-v2 P3，agent_memory 库）

-- 坐席工作状态：在线/忙碌/小休/离线。派单过滤排除 away/offline（不派新单）；
-- 「忙碌」为人工标记，仍参与派单（与 Redis 置忙的自动满载语义区分）。
ALTER TABLE customer_service.cs_agents
    ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'online';
ALTER TABLE customer_service.cs_agents
    DROP CONSTRAINT IF EXISTS ck_cs_agent_status_enum;
ALTER TABLE customer_service.cs_agents
    ADD CONSTRAINT ck_cs_agent_status_enum
    CHECK (status IN ('online', 'busy', 'away', 'offline'));

-- SLA 分级标记（warning/breach）：由 handoff_timeout_scan 幂等写入，
-- 主管通知中心据此展示升级告警；关闭兜底仍由 reaper 负责。
ALTER TABLE customer_service.handoffs
    ADD COLUMN IF NOT EXISTS sla_level VARCHAR(16);
ALTER TABLE customer_service.handoffs
    DROP CONSTRAINT IF EXISTS ck_cs_handoff_sla_level;
ALTER TABLE customer_service.handoffs
    ADD CONSTRAINT ck_cs_handoff_sla_level
    CHECK (sla_level IN ('warning', 'breach'));
