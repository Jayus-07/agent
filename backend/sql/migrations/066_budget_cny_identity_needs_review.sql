-- 066: 预算治理 P0 修复——记账本位币统一 CNY + 预占待对账状态 + 滞留预占处置
-- 日期：2026-10-01
--
-- 背景（P0 审查结论）：
--   1) 无显式策略的租户/用户共用 tenant_default 账本键，额度隔离失效
--      （代码修复：账本键一律为真实身份 user/tenant，本迁移只做数据口径搬迁）；
--   2) 预算层金额以 USD 命名/记账，而项目拍板以人民币为主，且 llm_usage
--      已混入 CNY 报价行，跨行加总失真——统一折算为 CNY；
--   3) 预占可能永久滞留（结算异常被吞 / 流式缺 usage）且无法表达
--      "已计费但用量未知"——新增 needs_review（待对账）状态。
--
-- 内容：
--   A. budget_reservations 增加 review_reason 列，status 约束加入 needs_review；
--   B. 预算三表列改名 _usd → _cny（budget_policies/budget_ledger/budget_reservations）；
--   C. 存量金额按 BUDGET_FX_USD_CNY=7.20 折算为 CNY（策略/账本/预占三表，
--      以及 llm_usage 中 currency='USD' 的历史成本行）；
--   D. 滞留预占处置：周期已结束仍 reserved 的预占转入 needs_review
--      （review_reason='legacy_stale_sweep'），账本占额同步释放。
--      当前周期仍打开的滞留预占不动（保守保留占额，由 sweep 惰性回收）。
--
-- 注意：
--   - 重跑防护由 public.schema_migrations 记录承担；本文件 C 段金额折算
--     不可重复执行（再跑会二次 ×7.20）。
--   - 折算不可逆：回滚需按 1/7.20 反向折算，无自动 down。
--   - 迁移前基线：budget_reservations 共 1182 行，其中 12 行 reserved
--     （合计 $6.00，user_id=8，2026-09-18/19，对应账本 day+month 各 $3.00）。

-- ── A. 预占状态机扩展 ─────────────────────────────────────────────
ALTER TABLE budget_reservations
    ADD COLUMN IF NOT EXISTS review_reason text NOT NULL DEFAULT '';

ALTER TABLE budget_reservations
    DROP CONSTRAINT IF EXISTS budget_reservations_status_check;
ALTER TABLE budget_reservations
    ADD CONSTRAINT budget_reservations_status_check
    CHECK (status = ANY (ARRAY['reserved'::text, 'settled'::text,
                               'released'::text, 'needs_review'::text]));

-- ── B. 列改名（幂等；改名本身可重复执行）───────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'budget_policies'
                 AND column_name = 'daily_limit_usd') THEN
        ALTER TABLE budget_policies RENAME COLUMN daily_limit_usd TO daily_limit_cny;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'budget_policies'
                 AND column_name = 'monthly_limit_usd') THEN
        ALTER TABLE budget_policies RENAME COLUMN monthly_limit_usd TO monthly_limit_cny;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'budget_ledger'
                 AND column_name = 'used_usd') THEN
        ALTER TABLE budget_ledger RENAME COLUMN used_usd TO used_cny;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'budget_ledger'
                 AND column_name = 'reserved_usd') THEN
        ALTER TABLE budget_ledger RENAME COLUMN reserved_usd TO reserved_cny;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'budget_ledger'
                 AND column_name = 'limit_usd') THEN
        ALTER TABLE budget_ledger RENAME COLUMN limit_usd TO limit_cny;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'budget_reservations'
                 AND column_name = 'reserved_usd') THEN
        ALTER TABLE budget_reservations RENAME COLUMN reserved_usd TO reserved_cny;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'budget_reservations'
                 AND column_name = 'settled_usd') THEN
        ALTER TABLE budget_reservations RENAME COLUMN settled_usd TO settled_cny;
    END IF;
END $$;

-- ── C. 存量金额 USD → CNY（×7.20，只允许执行一次）─────────────────
UPDATE budget_policies
    SET daily_limit_cny = daily_limit_cny * 7.20,
        monthly_limit_cny = monthly_limit_cny * 7.20;

UPDATE budget_ledger
    SET used_cny = used_cny * 7.20,
        reserved_cny = reserved_cny * 7.20,
        limit_cny = limit_cny * 7.20;

UPDATE budget_reservations
    SET reserved_cny = reserved_cny * 7.20,
        settled_cny = settled_cny * 7.20;

-- llm_usage 历史成本行：USD 报价行折算为 CNY；空币种行（成本恒 0）统一补 CNY。
-- 定价出口（pricing.calculate_llm_cost_with_status）自本版本起恒返 CNY。
UPDATE llm_usage
    SET total_cost = total_cost * 7.20,
        cost_usd = cost_usd * 7.20,
        input_cost = input_cost * 7.20,
        cached_input_cost = cached_input_cost * 7.20,
        output_cost = output_cost * 7.20,
        input_unit_price = input_unit_price * 7.20,
        output_unit_price = output_unit_price * 7.20,
        cache_input_unit_price = cache_input_unit_price * 7.20,
        currency = 'CNY'
    WHERE currency = 'USD';

UPDATE llm_usage SET currency = 'CNY' WHERE COALESCE(currency, '') = '';

-- ── D. 滞留预占处置（周期已结束的 reserved → needs_review + 释放占额）──
UPDATE budget_reservations r
SET status = 'needs_review',
    review_reason = 'legacy_stale_sweep',
    settled_at = now()
WHERE r.status = 'reserved'
  AND NOT EXISTS (
      SELECT 1
      FROM budget_reservations r2
      WHERE r2.id = r.id
        AND r2.status = 'reserved'
        AND r2.period_start IN (
            -- 当前日/月周期的起点（Asia/Shanghai 本地零点，与
            -- budget_periods() 口径一致）
            (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')
                AT TIME ZONE 'Asia/Shanghai'),
            (date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')
                AT TIME ZONE 'Asia/Shanghai')
        )
  );

UPDATE budget_ledger l
SET reserved_cny = GREATEST(l.reserved_cny - agg.hold, 0),
    updated_at = now()
FROM (
    SELECT scope_type, scope_id, period_type, period_start,
           SUM(reserved_cny) AS hold
    FROM budget_reservations
    WHERE status = 'needs_review' AND review_reason = 'legacy_stale_sweep'
    GROUP BY scope_type, scope_id, period_type, period_start
) agg
WHERE l.scope_type = agg.scope_type
  AND l.scope_id = agg.scope_id
  AND l.period_type = agg.period_type
  AND l.period_start = agg.period_start;
