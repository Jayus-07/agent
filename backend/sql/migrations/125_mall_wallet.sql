-- 100_mall_wallet.sql — 商城演示钱包账本（agent_business 库，cs-workstation-v2 P2）
--
-- 演示资金底座：余额与流水（支付=扣款、退款/取消=回款、充值=入账）。
-- 金额一律 NUMERIC(14,2)（DECIMAL 语义），禁止浮点累加展示。
-- 生产级支付对接属商城域另立项（见 docs/tasks/cs-workstation-v2/TASK.md §2.4）。

CREATE TABLE IF NOT EXISTS finance.wallet_balances (
    customer_id  BIGINT PRIMARY KEY,
    balance      NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
    currency     VARCHAR(8) NOT NULL DEFAULT 'CNY',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS finance.wallet_ledger (
    id             BIGSERIAL PRIMARY KEY,
    customer_id    BIGINT NOT NULL,
    direction      VARCHAR(4) NOT NULL CHECK (direction IN ('in', 'out')),
    amount         NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    balance_after  NUMERIC(14, 2) NOT NULL,
    kind           VARCHAR(32) NOT NULL
                   CHECK (kind IN ('topup', 'pay', 'cancel_refund', 'refund')),
    ref_no         VARCHAR(64) NOT NULL DEFAULT '',
    memo           VARCHAR(200) NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_customer
    ON finance.wallet_ledger (customer_id, id DESC);

-- 演示用户（99001）初始资金 ¥5,000 + 初始入账流水（幂等：已存在则跳过）
INSERT INTO finance.wallet_balances (customer_id, balance, currency)
VALUES (99001, 5000.00, 'CNY')
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO finance.wallet_ledger (customer_id, direction, amount, balance_after, kind, ref_no, memo)
SELECT 99001, 'in', 5000.00, 5000.00, 'topup', '', '演示初始资金'
WHERE NOT EXISTS (
    SELECT 1 FROM finance.wallet_ledger WHERE customer_id = 99001 AND kind = 'topup'
);
