-- 144: 预算治理第二批——待对账核销/归档闭环（resolution 轴 + action 审计表）
-- 日期：2026-11-10
--
-- 背景（待对账治理第二批）：
--   needs_review（待对账）只有进入、没有核销出口，存量持续堆积且占额
--   无法退出。本迁移为预占单增加独立的核销轴（resolution），status 枚举
--   保持不变（reserved/settled/released/needs_review），落实"needs_review
--   语义不写死，后续可扩展"的第一批前瞻约束。
--
-- 内容：
--   A. budget_reservations 增加 resolution 轴：resolution（''=未决，
--      settled_actual/released/written_off/archived=已关闭）、resolved_by、
--      resolved_at、actual_cny（settled_actual 时供应商实际账单金额）。
--   B. 新增 append-only 审计表 budget_reservation_resolutions：每次核销
--      动作一行，留痕可追溯，不物理删除任何预占/账本行。
--
-- 注意：
--   - 核销动作的账本释放语义在应用层实现（quota.resolve_needs_review），
--     释放仅在 status='needs_review' AND resolution='' 的开态执行，幂等。
--   - 编号说明：143_memory_product_scope.sql 由并行会话占用（未合入 main
--     时已应用共享库），本批从 144 起编避免冲突。

-- ── A. 预占单核销轴 ─────────────────────────────────────────────────
ALTER TABLE budget_reservations
    ADD COLUMN IF NOT EXISTS resolution text NOT NULL DEFAULT '';
ALTER TABLE budget_reservations
    ADD COLUMN IF NOT EXISTS resolved_by text NOT NULL DEFAULT '';
ALTER TABLE budget_reservations
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz;
ALTER TABLE budget_reservations
    ADD COLUMN IF NOT EXISTS actual_cny numeric;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'budget_reservations_resolution_check'
    ) THEN
        ALTER TABLE budget_reservations ADD CONSTRAINT budget_reservations_resolution_check
            CHECK (resolution = ANY (ARRAY[''::text, 'settled_actual'::text,
                                           'released'::text, 'written_off'::text,
                                           'archived'::text]));
    END IF;
END $$;

-- 关闭态判定高频使用（队列默认只看开态）
CREATE INDEX IF NOT EXISTS budget_reservations_open_review_idx
    ON budget_reservations (id)
    WHERE status = 'needs_review' AND resolution = '';

-- ── B. 核销动作审计表（append-only）────────────────────────────────
CREATE TABLE IF NOT EXISTS budget_reservation_resolutions (
    id bigserial PRIMARY KEY,
    reservation_id text NOT NULL,
    action text NOT NULL,
    reason text NOT NULL DEFAULT '',
    resolved_by text NOT NULL DEFAULT '',
    actual_cny numeric,
    released_cny numeric,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT budget_reservation_resolutions_action_check
        CHECK (action = ANY (ARRAY['settled_actual'::text, 'released'::text,
                                   'written_off'::text, 'archived'::text]))
);
CREATE INDEX IF NOT EXISTS budget_reservation_reservations_rid_idx
    ON budget_reservation_resolutions (reservation_id, created_at);
