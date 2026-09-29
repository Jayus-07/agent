-- 055_llm_usage_attribution.sql — llm_usage 业务归因三列（M5 / 台账 D5）
-- 目标：llm_usage 已有 user/tenant/trace/run 归因，缺 skill/tool/域 维度，
--   成本六维聚合只能算三维，答不了「哪个 Skill/Tool/域在烧钱」。
-- 三列语义：
--   skill_id      发起本次模型调用的 Skill（skill base 执行栈绑定；空=非 Skill 链路）
--   tool_id       发起本次模型调用的 Tool（tool_runtime executor 绑定；空=非 Tool 链路）
--   agent_domain  业务域（domain_registry 注册键：customer_service/travel/
--                 travel_commerce/travel_booking/selection_funnel；空=主图链路）
-- 与 llm_usage_store_pg.ensure_schema 的启动自愈段同口径（先迁移后启动皆可）。
-- 幂等：IF NOT EXISTS，重复执行无副作用；向后兼容（默认空串，旧行语义=未归因）。

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS skill_id TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS tool_id TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS agent_domain TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_llm_usage_attribution
    ON llm_usage(skill_id, tool_id, agent_domain, ts);
