-- 133_cs_coupon.sql — 优惠券档案与核销记录（agent_business 库，cs-capability-gap B3）
--
-- 券口径与 coupon-policy.md / ai.cs_faq id=23 互为事实源：默认不可叠加
--（一单仅可用一张）、门槛按商品实付合计不含运费、过期不可恢复。
-- 演示种子数字与 mock 懒种子一致（满 200 减 30 / 5 元无门槛 / 9 折最高减 50 /
-- 满 100 减 10 已过期）。核销记录由下单链路写入，本迁移只建表不种子。

CREATE SCHEMA IF NOT EXISTS marketing;

CREATE TABLE IF NOT EXISTS marketing.coupons (
    id              BIGSERIAL PRIMARY KEY,
    customer_id     BIGINT NOT NULL,
    coupon_code     VARCHAR(64) NOT NULL,
    kind            VARCHAR(16) NOT NULL
                    CHECK (kind IN ('full_reduction', 'discount', 'no_threshold')),
    name            VARCHAR(64) NOT NULL DEFAULT '',
    threshold_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    discount_value  NUMERIC(14, 2) NOT NULL,
    max_discount    NUMERIC(14, 2),
    stackable       BOOLEAN NOT NULL DEFAULT FALSE,
    exclusions      VARCHAR(200) NOT NULL DEFAULT '',
    valid_from      TIMESTAMPTZ,
    valid_until     TIMESTAMPTZ,
    status          VARCHAR(16) NOT NULL DEFAULT 'available'
                    CHECK (status IN ('available', 'expired', 'used')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_id, coupon_code)
);
CREATE INDEX IF NOT EXISTS idx_coupons_customer
    ON marketing.coupons (customer_id, id);

CREATE TABLE IF NOT EXISTS marketing.coupon_redemptions (
    id             BIGSERIAL PRIMARY KEY,
    customer_id    BIGINT NOT NULL,
    coupon_code    VARCHAR(64) NOT NULL,
    order_no       VARCHAR(64) NOT NULL,
    amount_applied NUMERIC(14, 2) NOT NULL DEFAULT 0,
    redeemed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_order
    ON marketing.coupon_redemptions (customer_id, order_no);

-- 演示用户（872）券档案种子（幂等：customer_id+coupon_code 冲突则跳过）
INSERT INTO marketing.coupons
    (customer_id, coupon_code, kind, name, threshold_amount, discount_value,
     max_discount, stackable, exclusions, valid_from, valid_until, status)
VALUES
    (872, 'SAVE30', 'full_reduction', '满减券', 200.00, 30.00,
     NULL, FALSE, '秒杀/拼团价商品', now() - INTERVAL '60 days', now() + INTERVAL '30 days', 'available'),
    (872, 'NEW5', 'no_threshold', '无门槛券', 0.00, 5.00,
     NULL, FALSE, '', now() - INTERVAL '60 days', now() + INTERVAL '15 days', 'available'),
    (872, 'D90', 'discount', '折扣券', 100.00, 0.90,
     50.00, FALSE, '秒杀/拼团价商品', now() - INTERVAL '45 days', now() + INTERVAL '45 days', 'available'),
    (872, 'OLD10', 'full_reduction', '满减券', 100.00, 10.00,
     NULL, FALSE, '秒杀/拼团价商品', now() - INTERVAL '95 days', now() - INTERVAL '5 days', 'expired')
ON CONFLICT (customer_id, coupon_code) DO NOTHING;
