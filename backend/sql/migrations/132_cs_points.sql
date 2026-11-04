-- 132_cs_points.sql — 会员积分账本与等级档案（agent_business 库，cs-capability-gap B2）
--
-- 积分账本式记账（余额=流水净和，单一事实源）；等级按近 90 天实付消费滚动
-- 评定（points-policy.md §3【业务确认】）。数字口径与 business-mock 懒种子及
-- ai.cs_faq id=8/16 互为事实源（消费 1 元=1 分、100 积分抵 1 元；
-- 白银≥500 / 黄金≥2000 / 铂金≥8000 元）。

CREATE TABLE IF NOT EXISTS finance.points_ledger (
    id            BIGSERIAL PRIMARY KEY,
    customer_id   BIGINT NOT NULL,
    direction     VARCHAR(4) NOT NULL CHECK (direction IN ('in', 'out')),
    points        BIGINT NOT NULL CHECK (points > 0),
    balance_after BIGINT NOT NULL,
    kind          VARCHAR(32) NOT NULL
                  CHECK (kind IN ('earn', 'redeem', 'rollback', 'expire')),
    ref_no        VARCHAR(64) NOT NULL DEFAULT '',
    memo          VARCHAR(200) NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_points_ledger_customer
    ON finance.points_ledger (customer_id, id DESC);

CREATE TABLE IF NOT EXISTS customer.tier_profiles (
    customer_id BIGINT PRIMARY KEY,
    tier        VARCHAR(16) NOT NULL DEFAULT 'normal'
                CHECK (tier IN ('normal', 'silver', 'gold', 'platinum')),
    spend_90d   NUMERIC(14, 2) NOT NULL DEFAULT 0,
    assessed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 演示用户（872）初始积分账本 + 黄金档档案（幂等：已存在则跳过）
INSERT INTO finance.points_ledger (customer_id, direction, points, balance_after, kind, ref_no, memo)
SELECT 872, 'in', 7800, 7800, 'earn', '', '消费获取'
WHERE NOT EXISTS (
    SELECT 1 FROM finance.points_ledger WHERE customer_id = 872
);

INSERT INTO finance.points_ledger (customer_id, direction, points, balance_after, kind, ref_no, memo)
SELECT 872, 'in', 200, 8000, 'earn', '', '订单完成奖励'
WHERE NOT EXISTS (
    SELECT 1 FROM finance.points_ledger
    WHERE customer_id = 872 AND memo = '订单完成奖励'
);

INSERT INTO customer.tier_profiles (customer_id, tier, spend_90d, assessed_at)
SELECT 872, 'gold', 3500.00, now()
ON CONFLICT (customer_id) DO NOTHING;
