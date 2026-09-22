-- 032: llm_usage 成本明细列（2026-09-22 Token 成本计量第一阶段）。
-- 复用现有 llm_usage 表（不新建系统）：标准化 token 三元组 + 分项成本 + 成本状态 + 货币。
-- 口径：
--   billable_input_tokens = prompt_tokens - cached_tokens（LangChain 统一口径 cached ⊆ input）
--   cost_status ∈ exact | estimated | unpriced（pricing.calculate_llm_cost_with_status 判定）
--   货币来自 model_price 行（页面录入可选 CNY/USD，默认 USD）
-- 幂等：全部 ADD COLUMN IF NOT EXISTS，可安全重放。llm_usage_store_pg._init_db
-- 启动时也会自动补列（双保险，兼容迁移未跑的环境）。

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS billable_input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS input_cost        DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS cached_input_cost DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS output_cost       DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS total_cost        DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS cost_status       TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS currency          TEXT NOT NULL DEFAULT '';

-- total_cost 与既有 cost_usd 同值（cost_usd 保留为兼容列，历史行 cost_status 为空串
-- 表示"迁移前的旧记录"，读取端按 estimated 对待即可，不必回填）。
