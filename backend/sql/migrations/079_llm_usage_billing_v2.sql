-- 079_llm_usage_billing_v2.sql — llm_usage Billing V2（Model Billing Unified Closure 2026-10-07）
-- 目标：消灭 cost_usd 一列三态（USD / CNY / CNY 值标 USD）——建立唯一计费事实链：
--   Provider Usage → NormalizedUsage → price_usage() → BillingResult → llm_usage/Budget/Trace/Grafana
-- 不变量：同 Usage + 同价格版本 + 同 FX → 只有一个成本答案（billed_cost_cny）。
--
-- 列语义（V2 冻结契约）：
--   billing_schema_version  2 = V2 运行时写入或已按 §12 规则可信回填；1 = legacy（币种不可机判）
--   native_cost             供应商原始报价币种成本（审计用）
--   native_currency         原生币种（USD/CNY/...；legacy 失败行 = ''）
--   billed_cost_cny         平台记账本位币金额（恒 CNY）——预算/管理端/看板唯一权威；
--                           NULL = legacy 行未回填（读层按旧 currency 逻辑展示）
--   fx_rate                 该次调用实际使用的汇率快照；原生即 CNY 时为 NULL
--   price_version           本次调用使用的价格表版本（price_unknown/registry_fallback 为 ''）
--   pricing_source          approved_price_table / registry_fallback / unpriced
--   usage_source            provider / estimated / unavailable
--   *_cost_cny              分项成本（CNY）；*_unit_price 单价快照改原生币种语义
--
-- 历史数据处理（§12 禁暴力重算）：
--   currency='CNY' 行（2026-10-01 本位币切换后 observe/流式估算行）金额可信为 CNY
--   → billed_cost_cny = total_cost、版本升 2；currency='USD'/'' 行的金额语义不可机判
--   （真 USD、CNY 值错标 USD 两种混存）→ 不猜，保持版本 1、billed_cost_cny = NULL。
-- 与 llm_usage_store_pg._init_db 启动自愈段同口径（先迁移后启动皆可）。
-- 幂等：IF NOT EXISTS，重复执行无副作用；不修改任何历史 migration。

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS billing_schema_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS native_cost NUMERIC(18, 6) NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS native_currency TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS billed_cost_cny NUMERIC(18, 6);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS fx_rate NUMERIC(18, 6);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS price_version TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS pricing_source TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS usage_source TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS reasoning_cost_cny NUMERIC(18, 6) NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS cache_write_cost_cny NUMERIC(18, 6) NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS tool_call_cost_cny NUMERIC(18, 6) NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS reasoning_unit_price NUMERIC(18, 6);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS cache_write_unit_price NUMERIC(18, 6);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS tool_call_unit_price NUMERIC(18, 6);

-- 可信回填（只动 CNY 明确行；单价快照列不改——历史行是当时折算值，语义由版本区分）
UPDATE llm_usage
   SET billed_cost_cny = total_cost,
       billing_schema_version = 2
 WHERE billed_cost_cny IS NULL
   AND COALESCE(NULLIF(currency, ''), 'USD') = 'CNY';

CREATE INDEX IF NOT EXISTS idx_llm_usage_billing
    ON llm_usage(billing_schema_version, cost_status, ts);
