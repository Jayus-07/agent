-- 147: 预算域额度轴（任务 9 执行化，2026-11-11）
--
-- 背景：执行面策略 scope 被 DB CHECK 硬限身份轴（user/tenant/
-- tenant_default/platform），观测面 llm_usage.agent_domain 已有归因但
-- 主链路覆盖率不足。用户拍板：每个用户按每个域独立额度，租户域池兜底；
-- 域清单固定 3 个（main/travel/customer_service），子域归并；
-- 总额度保留叠加 AND（4 道闸）。
--
-- 设计：三表各加 domain 列（''=总额度轴，非空=域额度轴），不建新表——
-- 现有 4 层继承链、月度对账、核销、月度对账全部兼容。
-- - budget_policies：PK 变 (scope_type, scope_id, domain)，domain='' 即
--   现有总额度策略，历史行无需回填；
-- - budget_ledger：PK 变 (scope_type, scope_id, period_type, period_start,
--   domain)，域窗口与总窗口并行记账；
-- - budget_reservations：PK 变 (id, scope_type, scope_id, period_type,
--   domain)——一笔域调用预占 8 行（user/tenant × 总/域 × 日/月），
--   settle/release/核销按 id 迭代释放，天然兼容。
--
-- 注意：幂等（IF NOT EXISTS / IF EXISTS）；CHECK 固定域枚举，新增域走迁移。

ALTER TABLE budget_policies
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT '';
ALTER TABLE budget_ledger
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT '';
ALTER TABLE budget_reservations
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT '';

ALTER TABLE budget_policies
    DROP CONSTRAINT IF EXISTS budget_policies_pkey;
ALTER TABLE budget_policies
    ADD CONSTRAINT budget_policies_pkey
    PRIMARY KEY (scope_type, scope_id, domain);

ALTER TABLE budget_ledger
    DROP CONSTRAINT IF EXISTS budget_ledger_pkey;
ALTER TABLE budget_ledger
    ADD CONSTRAINT budget_ledger_pkey
    PRIMARY KEY (scope_type, scope_id, period_type, period_start, domain);

ALTER TABLE budget_reservations
    DROP CONSTRAINT IF EXISTS budget_reservations_pkey;
ALTER TABLE budget_reservations
    ADD CONSTRAINT budget_reservations_pkey
    PRIMARY KEY (id, scope_type, scope_id, period_type, domain);

-- 域枚举：'' = 总额度轴；3 个顶级域 = 用户视角的产品入口。
-- 管理端工具（选品/知识库/数据查询）不按用户域额度管，不进枚举。
ALTER TABLE budget_policies
    DROP CONSTRAINT IF EXISTS budget_policies_domain_check;
ALTER TABLE budget_policies
    ADD CONSTRAINT budget_policies_domain_check
    CHECK (domain = ANY (ARRAY[''::text, 'main'::text, 'travel'::text,
                               'customer_service'::text]));

ALTER TABLE budget_ledger
    DROP CONSTRAINT IF EXISTS budget_ledger_domain_check;
ALTER TABLE budget_ledger
    ADD CONSTRAINT budget_ledger_domain_check
    CHECK (domain = ANY (ARRAY[''::text, 'main'::text, 'travel'::text,
                               'customer_service'::text]));

ALTER TABLE budget_reservations
    DROP CONSTRAINT IF EXISTS budget_reservations_domain_check;
ALTER TABLE budget_reservations
    ADD CONSTRAINT budget_reservations_domain_check
    CHECK (domain = ANY (ARRAY[''::text, 'main'::text, 'travel'::text,
                               'customer_service'::text]));
