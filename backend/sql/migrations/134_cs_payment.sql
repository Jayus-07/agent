-- 134_cs_payment.sql — 客服支付流水查询（agent_business，cs-capability-gap B4）
--
-- 本迁移只登记支付尝试与成功流水，不执行支付、退款或资金状态变更。
-- 退款提案继续走既有 CS_REFUND_REQUEST_MODE 确认与人工审核链路。

CREATE SCHEMA IF NOT EXISTS payment;

CREATE TABLE IF NOT EXISTS payment.payment_records (
    id             BIGSERIAL PRIMARY KEY,
    customer_id    BIGINT NOT NULL,
    tenant_id      VARCHAR(128) NOT NULL DEFAULT 'default',
    order_no       VARCHAR(64) NOT NULL,
    transaction_no VARCHAR(64) NOT NULL,
    status         VARCHAR(16) NOT NULL
                   CHECK (status IN ('success', 'failed')),
    amount         NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    paid_at        TIMESTAMPTZ,
    kind           VARCHAR(24) NOT NULL
                   CHECK (kind IN ('alipay', 'wechat_pay', 'bank_card')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_id, tenant_id, transaction_no)
);

CREATE INDEX IF NOT EXISTS idx_payment_records_order_no
    ON payment.payment_records (order_no);
CREATE INDEX IF NOT EXISTS idx_payment_records_owner_order
    ON payment.payment_records (customer_id, tenant_id, order_no, id DESC);

-- 演示用户（872）三态订单种子，与 business-mock 懒种子金额/状态对齐。
-- 冲突时不覆盖已有订单或支付记录，迁移可安全重放。
INSERT INTO "order".orders
    (order_no, customer_id, total_amount, status, payment_status, created_at)
VALUES
    ('CSB4-PAY-SINGLE-872', 872, 1299.00, 'paid', 'paid', now() - INTERVAL '7 days'),
    ('CSB4-PAY-FAILED-872', 872, 358.00, 'pending', 'unpaid', now() - INTERVAL '1 day'),
    ('CSB4-PAY-DUP-872', 872, 699.00, 'shipped', 'paid', now() - INTERVAL '3 days')
ON CONFLICT (order_no) DO NOTHING;

INSERT INTO payment.payment_records
    (customer_id, tenant_id, order_no, transaction_no, status, amount, paid_at, kind)
SELECT seed.customer_id, seed.tenant_id, seed.order_no, seed.transaction_no,
       seed.status, seed.amount, seed.paid_at, seed.kind
FROM (VALUES
    (872::BIGINT, 'default'::VARCHAR, 'CSB4-PAY-SINGLE-872'::VARCHAR,
     'PAY-872-SINGLE-001'::VARCHAR, 'success'::VARCHAR, 1299.00::NUMERIC,
     now() - INTERVAL '7 days', 'alipay'::VARCHAR),
    (872::BIGINT, 'default'::VARCHAR, 'CSB4-PAY-FAILED-872'::VARCHAR,
     'PAY-872-FAILED-001'::VARCHAR, 'failed'::VARCHAR, 358.00::NUMERIC,
     NULL::TIMESTAMPTZ, 'bank_card'::VARCHAR),
    (872::BIGINT, 'default'::VARCHAR, 'CSB4-PAY-DUP-872'::VARCHAR,
     'PAY-872-DUP-001'::VARCHAR, 'success'::VARCHAR, 699.00::NUMERIC,
     now() - INTERVAL '3 days', 'alipay'::VARCHAR),
    (872::BIGINT, 'default'::VARCHAR, 'CSB4-PAY-DUP-872'::VARCHAR,
     'PAY-872-DUP-002'::VARCHAR, 'success'::VARCHAR, 699.00::NUMERIC,
     now() - INTERVAL '3 days' + INTERVAL '1 minute', 'wechat_pay'::VARCHAR)
) AS seed(
    customer_id, tenant_id, order_no, transaction_no, status, amount, paid_at, kind
)
JOIN "order".orders AS o
  ON o.order_no = seed.order_no
 AND o.customer_id = seed.customer_id
ON CONFLICT (customer_id, tenant_id, transaction_no) DO NOTHING;
