-- 150_retail_erp_pilot_schema.sql
-- 单租户默认组织下的零售 ERP 试点数据模型。
--
-- 适用范围：agent_business。所有业务表带 tenant_id；门店业务事实另带
-- store_id。首批只建一间虚构试点门店，作为 default 租户的总部视图。
-- 当前 SQL 权限只允许 all scope 读取这些内部 ERP 表；增加真实门店前，
-- 必须先落地并验证用户到门店的服务端授权过滤。
--
-- 新 schema 不走 agent_readonly 的默认授权；仅向只读角色显式授予本迁移
-- 中登记的 21 张表，保持 SQL 白名单与数据库权限面一致。

BEGIN;

CREATE SCHEMA IF NOT EXISTS retail;

CREATE TABLE IF NOT EXISTS retail.stores (
    tenant_id      TEXT NOT NULL,
    store_id       TEXT NOT NULL,
    store_name     TEXT NOT NULL,
    timezone       TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    currency_code  CHAR(3) NOT NULL DEFAULT 'USD',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, store_id),
    CONSTRAINT ck_retail_stores_status CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS retail.warehouses (
    tenant_id       TEXT NOT NULL,
    warehouse_id    TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    warehouse_name  TEXT NOT NULL,
    warehouse_type  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, warehouse_id),
    UNIQUE (tenant_id, warehouse_id, store_id),
    FOREIGN KEY (tenant_id, store_id)
        REFERENCES retail.stores (tenant_id, store_id),
    CONSTRAINT ck_retail_warehouses_type
        CHECK (warehouse_type IN ('distribution_center', 'store', 'backroom')),
    CONSTRAINT ck_retail_warehouses_status CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS retail.products (
    tenant_id      TEXT NOT NULL,
    sku            TEXT NOT NULL,
    barcode        TEXT NOT NULL,
    product_name   TEXT NOT NULL,
    category       TEXT NOT NULL,
    brand          TEXT NOT NULL DEFAULT '',
    unit           TEXT NOT NULL DEFAULT 'each',
    sale_price     NUMERIC(12, 2) NOT NULL,
    unit_cost      NUMERIC(12, 2) NOT NULL,
    reorder_point  INTEGER NOT NULL DEFAULT 0,
    batch_tracked  BOOLEAN NOT NULL DEFAULT FALSE,
    status         TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, sku),
    UNIQUE (tenant_id, barcode),
    CONSTRAINT ck_retail_products_prices CHECK (sale_price >= 0 AND unit_cost >= 0),
    CONSTRAINT ck_retail_products_reorder CHECK (reorder_point >= 0),
    CONSTRAINT ck_retail_products_status CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS retail.suppliers (
    tenant_id       TEXT NOT NULL,
    supplier_id     TEXT NOT NULL,
    supplier_name   TEXT NOT NULL,
    country_code    CHAR(2) NOT NULL DEFAULT 'US',
    lead_time_days  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, supplier_id),
    CONSTRAINT ck_retail_suppliers_lead_time CHECK (lead_time_days >= 0),
    CONSTRAINT ck_retail_suppliers_status CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS retail.purchase_requests (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    requested_by    TEXT NOT NULL,
    status          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    decided_at      TIMESTAMPTZ,
    note            TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, request_id),
    UNIQUE (tenant_id, request_id, store_id),
    FOREIGN KEY (tenant_id, store_id)
        REFERENCES retail.stores (tenant_id, store_id),
    CONSTRAINT ck_retail_purchase_requests_status
        CHECK (status IN ('draft', 'submitted', 'approved', 'rejected', 'converted'))
);

CREATE TABLE IF NOT EXISTS retail.purchase_request_lines (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    line_no         INTEGER NOT NULL,
    sku             TEXT NOT NULL,
    requested_qty   INTEGER NOT NULL,
    approved_qty    INTEGER,
    PRIMARY KEY (tenant_id, request_id, line_no),
    FOREIGN KEY (tenant_id, request_id, store_id)
        REFERENCES retail.purchase_requests (tenant_id, request_id, store_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_purchase_request_qty
        CHECK (requested_qty > 0 AND (approved_qty IS NULL OR approved_qty >= 0))
);

CREATE TABLE IF NOT EXISTS retail.purchase_orders (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    po_id           TEXT NOT NULL,
    supplier_id     TEXT NOT NULL,
    warehouse_id    TEXT NOT NULL,
    source_request_id TEXT,
    status          TEXT NOT NULL,
    ordered_at      TIMESTAMPTZ NOT NULL,
    expected_at     TIMESTAMPTZ,
    currency_code   CHAR(3) NOT NULL DEFAULT 'USD',
    PRIMARY KEY (tenant_id, po_id),
    UNIQUE (tenant_id, po_id, store_id),
    FOREIGN KEY (tenant_id, store_id)
        REFERENCES retail.stores (tenant_id, store_id),
    FOREIGN KEY (tenant_id, supplier_id)
        REFERENCES retail.suppliers (tenant_id, supplier_id),
    FOREIGN KEY (tenant_id, warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    FOREIGN KEY (tenant_id, source_request_id, store_id)
        REFERENCES retail.purchase_requests (tenant_id, request_id, store_id),
    CONSTRAINT ck_retail_purchase_orders_status
        CHECK (status IN ('draft', 'pending_approval', 'approved', 'sent',
                          'partially_received', 'received', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS retail.purchase_order_lines (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    po_id           TEXT NOT NULL,
    line_no         INTEGER NOT NULL,
    sku             TEXT NOT NULL,
    ordered_qty     INTEGER NOT NULL,
    unit_cost       NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (tenant_id, po_id, line_no),
    UNIQUE (tenant_id, store_id, po_id, line_no),
    FOREIGN KEY (tenant_id, po_id, store_id)
        REFERENCES retail.purchase_orders (tenant_id, po_id, store_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_purchase_order_line_values
        CHECK (ordered_qty > 0 AND unit_cost >= 0)
);

CREATE TABLE IF NOT EXISTS retail.receipts (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    receipt_id      TEXT NOT NULL,
    po_id           TEXT NOT NULL,
    warehouse_id    TEXT NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL,
    reference_no    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, receipt_id),
    UNIQUE (tenant_id, receipt_id, store_id),
    UNIQUE (tenant_id, receipt_id, store_id, po_id),
    FOREIGN KEY (tenant_id, po_id, store_id)
        REFERENCES retail.purchase_orders (tenant_id, po_id, store_id),
    FOREIGN KEY (tenant_id, warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    CONSTRAINT ck_retail_receipts_status CHECK (status IN ('posted', 'partial', 'void'))
);

CREATE TABLE IF NOT EXISTS retail.receipt_lines (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    receipt_id      TEXT NOT NULL,
    line_no         INTEGER NOT NULL,
    po_id           TEXT NOT NULL,
    po_line_no      INTEGER NOT NULL,
    sku             TEXT NOT NULL,
    received_qty    INTEGER NOT NULL,
    accepted_qty    INTEGER NOT NULL,
    rejected_qty    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, receipt_id, line_no),
    FOREIGN KEY (tenant_id, receipt_id, store_id)
        REFERENCES retail.receipts (tenant_id, receipt_id, store_id),
    FOREIGN KEY (tenant_id, receipt_id, store_id, po_id)
        REFERENCES retail.receipts (tenant_id, receipt_id, store_id, po_id),
    FOREIGN KEY (tenant_id, store_id, po_id, po_line_no)
        REFERENCES retail.purchase_order_lines (tenant_id, store_id, po_id, line_no),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_receipt_line_qty
        CHECK (received_qty >= 0 AND accepted_qty >= 0 AND rejected_qty >= 0
               AND received_qty = accepted_qty + rejected_qty)
);

CREATE TABLE IF NOT EXISTS retail.inventory_balances (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    warehouse_id    TEXT NOT NULL,
    sku             TEXT NOT NULL,
    on_hand_qty     INTEGER NOT NULL,
    reserved_qty    INTEGER NOT NULL DEFAULT 0,
    safety_stock    INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, store_id, warehouse_id, sku),
    FOREIGN KEY (tenant_id, warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_inventory_qty
        CHECK (on_hand_qty >= 0 AND reserved_qty >= 0 AND safety_stock >= 0)
);

CREATE TABLE IF NOT EXISTS retail.inventory_movements (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    movement_id     TEXT NOT NULL,
    warehouse_id    TEXT NOT NULL,
    sku             TEXT NOT NULL,
    movement_type   TEXT NOT NULL,
    quantity_delta  INTEGER NOT NULL,
    source_type     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, movement_id),
    FOREIGN KEY (tenant_id, warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_inventory_movement_type
        CHECK (movement_type IN ('receipt', 'sale', 'transfer_in', 'transfer_out',
                                 'stock_count', 'return')),
    CONSTRAINT ck_retail_inventory_movement_delta CHECK (quantity_delta <> 0)
);

CREATE TABLE IF NOT EXISTS retail.product_batches (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    batch_id        TEXT NOT NULL,
    warehouse_id    TEXT NOT NULL,
    sku             TEXT NOT NULL,
    received_at     DATE NOT NULL,
    expiry_date     DATE NOT NULL,
    quantity_on_hand INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'available',
    PRIMARY KEY (tenant_id, batch_id),
    FOREIGN KEY (tenant_id, warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_batch_dates CHECK (expiry_date >= received_at),
    CONSTRAINT ck_retail_batch_qty CHECK (quantity_on_hand >= 0),
    CONSTRAINT ck_retail_batch_status CHECK (status IN ('available', 'quarantined', 'expired'))
);

CREATE TABLE IF NOT EXISTS retail.stock_transfers (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    transfer_id     TEXT NOT NULL,
    from_warehouse_id TEXT NOT NULL,
    to_warehouse_id TEXT NOT NULL,
    status          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    shipped_at      TIMESTAMPTZ,
    received_at     TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, transfer_id),
    UNIQUE (tenant_id, transfer_id, store_id),
    FOREIGN KEY (tenant_id, from_warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    FOREIGN KEY (tenant_id, to_warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    CONSTRAINT ck_retail_stock_transfers_status
        CHECK (status IN ('draft', 'approved', 'in_transit', 'received', 'cancelled')),
    CONSTRAINT ck_retail_stock_transfers_warehouses
        CHECK (from_warehouse_id <> to_warehouse_id)
);

CREATE TABLE IF NOT EXISTS retail.stock_transfer_lines (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    transfer_id     TEXT NOT NULL,
    line_no         INTEGER NOT NULL,
    sku             TEXT NOT NULL,
    sent_qty        INTEGER NOT NULL,
    received_qty    INTEGER,
    PRIMARY KEY (tenant_id, transfer_id, line_no),
    FOREIGN KEY (tenant_id, transfer_id, store_id)
        REFERENCES retail.stock_transfers (tenant_id, transfer_id, store_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_transfer_line_qty
        CHECK (sent_qty > 0 AND (received_qty IS NULL OR received_qty >= 0))
);

CREATE TABLE IF NOT EXISTS retail.stock_counts (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    count_id        TEXT NOT NULL,
    warehouse_id    TEXT NOT NULL,
    status          TEXT NOT NULL,
    planned_at      TIMESTAMPTZ NOT NULL,
    counted_at      TIMESTAMPTZ,
    posted_at       TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, count_id),
    UNIQUE (tenant_id, count_id, store_id),
    FOREIGN KEY (tenant_id, warehouse_id, store_id)
        REFERENCES retail.warehouses (tenant_id, warehouse_id, store_id),
    CONSTRAINT ck_retail_stock_counts_status
        CHECK (status IN ('draft', 'counting', 'submitted', 'approved', 'posted', 'rejected'))
);

CREATE TABLE IF NOT EXISTS retail.stock_count_lines (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    count_id        TEXT NOT NULL,
    line_no         INTEGER NOT NULL,
    sku             TEXT NOT NULL,
    system_qty      INTEGER NOT NULL,
    counted_qty     INTEGER NOT NULL,
    unit_cost       NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (tenant_id, count_id, line_no),
    FOREIGN KEY (tenant_id, count_id, store_id)
        REFERENCES retail.stock_counts (tenant_id, count_id, store_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_stock_count_line_values
        CHECK (system_qty >= 0 AND counted_qty >= 0 AND unit_cost >= 0)
);

CREATE TABLE IF NOT EXISTS retail.pos_sales (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    sale_id         TEXT NOT NULL,
    receipt_no      TEXT NOT NULL,
    sold_at         TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL,
    currency_code   CHAR(3) NOT NULL DEFAULT 'USD',
    subtotal        NUMERIC(12, 2) NOT NULL,
    tax_amount      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (tenant_id, sale_id),
    UNIQUE (tenant_id, store_id, sale_id),
    UNIQUE (tenant_id, store_id, receipt_no),
    FOREIGN KEY (tenant_id, store_id)
        REFERENCES retail.stores (tenant_id, store_id),
    CONSTRAINT ck_retail_pos_sales_status CHECK (status IN ('completed', 'cancelled')),
    CONSTRAINT ck_retail_pos_sales_amounts
        CHECK (subtotal >= 0 AND tax_amount >= 0 AND total_amount = subtotal + tax_amount)
);

CREATE TABLE IF NOT EXISTS retail.pos_sale_lines (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    sale_id         TEXT NOT NULL,
    line_no         INTEGER NOT NULL,
    sku             TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      NUMERIC(12, 2) NOT NULL,
    unit_cost       NUMERIC(12, 2) NOT NULL,
    discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    tax_amount      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    line_net_amount NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (tenant_id, sale_id, line_no),
    UNIQUE (tenant_id, store_id, sale_id, line_no),
    FOREIGN KEY (tenant_id, store_id, sale_id)
        REFERENCES retail.pos_sales (tenant_id, store_id, sale_id),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_pos_sale_line_values
        CHECK (quantity > 0 AND unit_price >= 0 AND unit_cost >= 0
               AND discount_amount >= 0 AND tax_amount >= 0
               AND line_net_amount >= 0)
);

CREATE TABLE IF NOT EXISTS retail.pos_returns (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    return_id       TEXT NOT NULL,
    sale_id         TEXT NOT NULL,
    status          TEXT NOT NULL,
    requested_at    TIMESTAMPTZ NOT NULL,
    refunded_at     TIMESTAMPTZ,
    reason          TEXT NOT NULL,
    PRIMARY KEY (tenant_id, return_id),
    UNIQUE (tenant_id, return_id, store_id),
    FOREIGN KEY (tenant_id, store_id, sale_id)
        REFERENCES retail.pos_sales (tenant_id, store_id, sale_id),
    CONSTRAINT ck_retail_pos_returns_status
        CHECK (status IN ('requested', 'approved', 'refunded', 'rejected'))
);

CREATE TABLE IF NOT EXISTS retail.pos_return_lines (
    tenant_id       TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    return_id       TEXT NOT NULL,
    line_no         INTEGER NOT NULL,
    sale_id         TEXT NOT NULL,
    sale_line_no    INTEGER NOT NULL,
    sku             TEXT NOT NULL,
    return_qty      INTEGER NOT NULL,
    refund_amount   NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (tenant_id, return_id, line_no),
    FOREIGN KEY (tenant_id, return_id, store_id)
        REFERENCES retail.pos_returns (tenant_id, return_id, store_id),
    FOREIGN KEY (tenant_id, store_id, sale_id, sale_line_no)
        REFERENCES retail.pos_sale_lines (tenant_id, store_id, sale_id, line_no),
    FOREIGN KEY (tenant_id, sku) REFERENCES retail.products (tenant_id, sku),
    CONSTRAINT ck_retail_pos_return_line_values
        CHECK (return_qty > 0 AND refund_amount >= 0)
);

CREATE INDEX IF NOT EXISTS idx_retail_purchase_requests_tenant_store_status
    ON retail.purchase_requests (tenant_id, store_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_retail_purchase_orders_tenant_store_status
    ON retail.purchase_orders (tenant_id, store_id, status, ordered_at DESC);
CREATE INDEX IF NOT EXISTS idx_retail_receipts_tenant_store_received
    ON retail.receipts (tenant_id, store_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_retail_inventory_tenant_store_sku
    ON retail.inventory_balances (tenant_id, store_id, sku);
CREATE INDEX IF NOT EXISTS idx_retail_movements_tenant_store_time
    ON retail.inventory_movements (tenant_id, store_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_retail_batches_tenant_store_expiry
    ON retail.product_batches (tenant_id, store_id, expiry_date);
CREATE INDEX IF NOT EXISTS idx_retail_sales_tenant_store_time
    ON retail.pos_sales (tenant_id, store_id, sold_at DESC);
CREATE INDEX IF NOT EXISTS idx_retail_returns_tenant_store_status
    ON retail.pos_returns (tenant_id, store_id, status, refunded_at DESC);

-- 新 schema 默认不授权；只开放 SQL schema_config 同步登记的 21 张表。
GRANT USAGE ON SCHEMA retail TO agent_readonly;
GRANT SELECT ON retail.stores,
    retail.warehouses,
    retail.products,
    retail.suppliers,
    retail.purchase_requests,
    retail.purchase_request_lines,
    retail.purchase_orders,
    retail.purchase_order_lines,
    retail.receipts,
    retail.receipt_lines,
    retail.inventory_balances,
    retail.inventory_movements,
    retail.product_batches,
    retail.stock_transfers,
    retail.stock_transfer_lines,
    retail.stock_counts,
    retail.stock_count_lines,
    retail.pos_sales,
    retail.pos_sale_lines,
    retail.pos_returns,
    retail.pos_return_lines
TO agent_readonly;

COMMIT;
