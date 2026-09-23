-- 046_llm_usage_identity_billing.sql — usage 身份链 + Billing Snapshot（STOP C）
-- 目标（Model Governance STOP C / C2+C6）：
--   1. llm_usage 补齐身份链列：此前 model 列可能是上游回传名（豆包
--      doubao-seed-2-0-mini-260428 被记成 provider=ollama 的 1174 行错位），
--      且 upstream 原值 / 请求覆盖名 / 绑定来源只进进程内 ContextVar，
--      落库后不可追溯。四列语义（C2）：
--       - requested_model   请求显式覆盖指定的登记名（无覆盖为空）
--       - upstream_model_id provider response 回传的原始模型名（观测值）
--       - binding_source    解析来源（request_override/db_binding/.../fallback）
--       - model 列维持 canonical（登记名）口径，聚合维度不变
--   2. Billing Snapshot（C6）：调用时单价随行落库，模型改价不污染历史
--      对账（此前只存计算结果 total，无法审计用的是哪一版价格）。
--      单价 per_1m_tokens，币种用既有 currency 列；price_unknown（硬门缺价
--      按注册表估价）时单价为 NULL —— 语义=当时无生效价格行。
--   3. cost_status 枚举收口（C10）：实际值为 exact|estimated|unpriced|
--      price_unknown 四值（price_unknown 此前未在 035 注释声明，本次补记；
--      列无 CHECK 约束，以 pricing.py COST_STATUS_* 常量为唯一口径）。
-- 幂等：IF NOT EXISTS，重复执行无副作用；向后兼容（全部可空或带默认）。

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS requested_model TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS upstream_model_id TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS binding_source TEXT NOT NULL DEFAULT '';

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS input_unit_price NUMERIC(18, 6);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS output_unit_price NUMERIC(18, 6);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS cache_input_unit_price NUMERIC(18, 6);
