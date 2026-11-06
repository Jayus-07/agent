-- 138_cs_invoice_business.sql — 客服发票抬头与模拟开票记录（agent_business，B8）
-- 本迁移不创建真实税务发票；模拟记录的 real_invoice_issued 被约束为 false。

CREATE SCHEMA IF NOT EXISTS customer;
CREATE SCHEMA IF NOT EXISTS finance;

CREATE TABLE IF NOT EXISTS customer.invoice_titles (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    tenant_id   VARCHAR(128) NOT NULL DEFAULT 'default',
    title_type  VARCHAR(16) NOT NULL
                CHECK (title_type IN ('individual', 'company')),
    title_name  VARCHAR(200) NOT NULL,
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    status      VARCHAR(16) NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'inactive')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_id, tenant_id, title_name)
);
CREATE INDEX IF NOT EXISTS idx_invoice_titles_owner
    ON customer.invoice_titles (customer_id, tenant_id, status, is_default DESC);

CREATE TABLE IF NOT EXISTS finance.invoices (
    id                  BIGSERIAL PRIMARY KEY,
    customer_id         BIGINT NOT NULL,
    tenant_id           VARCHAR(128) NOT NULL DEFAULT 'default',
    invoice_title_id    BIGINT NOT NULL
                        REFERENCES customer.invoice_titles(id),
    order_no            VARCHAR(64) NOT NULL,
    invoice_no          VARCHAR(64) NOT NULL,
    invoice_type        VARCHAR(16) NOT NULL DEFAULT 'electronic'
                        CHECK (invoice_type = 'electronic'),
    status              VARCHAR(16) NOT NULL DEFAULT 'simulated'
                        CHECK (status = 'simulated'),
    real_invoice_issued BOOLEAN NOT NULL DEFAULT FALSE
                        CHECK (real_invoice_issued IS FALSE),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, customer_id, invoice_no),
    UNIQUE (tenant_id, customer_id, order_no, invoice_title_id)
);
CREATE INDEX IF NOT EXISTS idx_invoices_owner_order
    ON finance.invoices (customer_id, tenant_id, order_no, id DESC);

INSERT INTO customer.invoice_titles
    (customer_id, tenant_id, title_type, title_name, is_default, status)
VALUES
    (872, 'default', 'individual', '个人抬头（模拟）', TRUE, 'active'),
    (872, 'default', 'company', '企业抬头（模拟）', FALSE, 'active')
ON CONFLICT (customer_id, tenant_id, title_name) DO NOTHING;
