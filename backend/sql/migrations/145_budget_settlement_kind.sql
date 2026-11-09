-- 145: 预算治理——估算结算 DB 标记（settle_kind 轴），修复 billing_mismatch 误报
-- 日期：2026-11-10
--
-- 背景（待对账治理 1.3 后续）：1.3 引入的估算结算把预占上限计入
-- settled_cny（status='settled'），而对应 llm_usage 行没有同额成本行，
-- P1-09 的窗口对账（billing_mismatch，正常恒 0）会持续误报——真实结算
-- 与记账分叉的报警被合法的保守估算淹没。
--
-- 内容：
--   budget_reservations 增加 settle_kind 列（''=精确结算，'estimated'=
--   按预占上限保守估算）。status 枚举不动；settle_kind 是独立标记轴，
--   与 resolution（人工核销轴）正交。
--
-- 注意：
--   - billing_mismatch 对账 SQL 据此排除估算结算单（应用层 quota.py）。
--   - 历史数据 settle_kind=''（精确），与迁移前行为一致，无回填。

ALTER TABLE budget_reservations
    ADD COLUMN IF NOT EXISTS settle_kind text NOT NULL DEFAULT '';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'budget_reservations_settle_kind_check'
    ) THEN
        ALTER TABLE budget_reservations ADD CONSTRAINT budget_reservations_settle_kind_check
            CHECK (settle_kind = ANY (ARRAY[''::text, 'estimated'::text]));
    END IF;
END $$;
