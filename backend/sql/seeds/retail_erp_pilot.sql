-- 零售 ERP 单店试点合成数据（仅用于开发/评测，不是真实企业数据）。
-- 前置：先在 agent_business 应用 150_retail_erp_pilot_schema.sql。
-- 固定业务日期：2026-11-01 至 2026-11-19；金额为 USD。
-- 唯一租户为 default；仅创建一间虚构门店，不写入现有业务表。
-- 可重复执行：仅按本文件固定主键插入/更新，不清空或删除其他数据。
-- 库存流水按完整数量变化记账；退款退回的商品在本样例中按可售退货入库。

BEGIN;

INSERT INTO retail.stores
    (tenant_id, store_id, store_name, timezone, currency_code, status)
VALUES
    ('default', 'STORE-PILOT-001', '总部试点门店 001（合成数据）',
     'America/Los_Angeles', 'USD', 'active')
ON CONFLICT (tenant_id, store_id) DO UPDATE SET
    store_name = EXCLUDED.store_name,
    timezone = EXCLUDED.timezone,
    currency_code = EXCLUDED.currency_code,
    status = EXCLUDED.status;

INSERT INTO retail.warehouses
    (tenant_id, warehouse_id, store_id, warehouse_name, warehouse_type, status)
VALUES
    ('default', 'WH-PILOT-BACKROOM', 'STORE-PILOT-001', '试点店后仓', 'backroom', 'active'),
    ('default', 'WH-PILOT-FLOOR', 'STORE-PILOT-001', '试点店卖场', 'store', 'active')
ON CONFLICT (tenant_id, warehouse_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    warehouse_name = EXCLUDED.warehouse_name,
    warehouse_type = EXCLUDED.warehouse_type,
    status = EXCLUDED.status;

INSERT INTO retail.products
    (tenant_id, sku, barcode, product_name, category, brand, unit,
     sale_price, unit_cost, reorder_point, batch_tracked, status)
VALUES
    ('default', 'COF-001', '810000000001', '中烘焙咖啡豆 340g', '食品', 'Harbor Roast', '袋', 12.00, 6.00, 20, FALSE, 'active'),
    ('default', 'OAT-001', '810000000002', '原味燕麦奶 1L', '食品', 'Daily Oat', '盒', 4.50, 2.00, 24, TRUE, 'active'),
    ('default', 'MIX-001', '810000000003', '坚果混合包 200g', '食品', 'Trail Day', '袋', 6.99, 3.00, 16, TRUE, 'active'),
    ('default', 'SOAP-001', '810000000004', '植物香皂 3块装', '日化', 'Clear Field', '盒', 5.99, 2.50, 12, FALSE, 'active'),
    ('default', 'SHAM-001', '810000000005', '日常洗发水 400ml', '日化', 'Clear Field', '瓶', 8.99, 4.00, 10, TRUE, 'active'),
    ('default', 'PAPER-001', '810000000006', '厨房纸巾 6卷装', '家居', 'Home Harbor', '包', 9.99, 5.00, 8, FALSE, 'active'),
    ('default', 'VIT-001', '810000000007', '维生素软糖 60粒', '食品', 'Good Day', '瓶', 14.99, 7.00, 6, TRUE, 'active'),
    ('default', 'RICE-001', '810000000008', '茉莉香米 2kg', '食品', 'Golden Field', '袋', 11.99, 6.00, 8, FALSE, 'active')
ON CONFLICT (tenant_id, sku) DO UPDATE SET
    barcode = EXCLUDED.barcode,
    product_name = EXCLUDED.product_name,
    category = EXCLUDED.category,
    brand = EXCLUDED.brand,
    unit = EXCLUDED.unit,
    sale_price = EXCLUDED.sale_price,
    unit_cost = EXCLUDED.unit_cost,
    reorder_point = EXCLUDED.reorder_point,
    batch_tracked = EXCLUDED.batch_tracked,
    status = EXCLUDED.status;

INSERT INTO retail.suppliers
    (tenant_id, supplier_id, supplier_name, country_code, lead_time_days, status)
VALUES
    ('default', 'SUP-FOOD-001', 'NorthStar Foods（合成供应商）', 'US', 5, 'active'),
    ('default', 'SUP-CARE-001', 'Evergreen Personal Care（合成供应商）', 'US', 7, 'active'),
    ('default', 'SUP-HOME-001', 'Cedar Home Goods（合成供应商）', 'US', 4, 'active')
ON CONFLICT (tenant_id, supplier_id) DO UPDATE SET
    supplier_name = EXCLUDED.supplier_name,
    country_code = EXCLUDED.country_code,
    lead_time_days = EXCLUDED.lead_time_days,
    status = EXCLUDED.status;

INSERT INTO retail.purchase_requests
    (tenant_id, store_id, request_id, requested_by, status, created_at, decided_at, note)
VALUES
    ('default', 'STORE-PILOT-001', 'PR-2026-001', '店长-合成账号', 'submitted', '2026-11-17 09:15-08', NULL, '周末食品补货'),
    ('default', 'STORE-PILOT-001', 'PR-2026-002', '总部采购-合成账号', 'converted', '2026-11-10 10:00-08', '2026-11-11 14:00-08', '转为 PO-2026-004'),
    ('default', 'STORE-PILOT-001', 'PR-2026-003', '店长-合成账号', 'rejected', '2026-11-06 11:20-08', '2026-11-07 09:00-08', '纸巾申请超出预算'),
    ('default', 'STORE-PILOT-001', 'PR-2026-004', '店员-合成账号', 'draft', '2026-11-18 16:30-08', NULL, '待提交的日化补货申请')
ON CONFLICT (tenant_id, request_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    requested_by = EXCLUDED.requested_by,
    status = EXCLUDED.status,
    created_at = EXCLUDED.created_at,
    decided_at = EXCLUDED.decided_at,
    note = EXCLUDED.note;

INSERT INTO retail.purchase_request_lines
    (tenant_id, store_id, request_id, line_no, sku, requested_qty, approved_qty)
VALUES
    ('default', 'STORE-PILOT-001', 'PR-2026-001', 1, 'OAT-001', 20, NULL),
    ('default', 'STORE-PILOT-001', 'PR-2026-001', 2, 'VIT-001', 10, NULL),
    ('default', 'STORE-PILOT-001', 'PR-2026-002', 1, 'COF-001', 30, 30),
    ('default', 'STORE-PILOT-001', 'PR-2026-002', 2, 'MIX-001', 20, 20),
    ('default', 'STORE-PILOT-001', 'PR-2026-003', 1, 'PAPER-001', 50, 0),
    ('default', 'STORE-PILOT-001', 'PR-2026-004', 1, 'SOAP-001', 12, NULL)
ON CONFLICT (tenant_id, request_id, line_no) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    sku = EXCLUDED.sku,
    requested_qty = EXCLUDED.requested_qty,
    approved_qty = EXCLUDED.approved_qty;

INSERT INTO retail.purchase_orders
    (tenant_id, store_id, po_id, supplier_id, warehouse_id, source_request_id,
     status, ordered_at, expected_at, currency_code)
VALUES
    ('default', 'STORE-PILOT-001', 'PO-2026-001', 'SUP-FOOD-001', 'WH-PILOT-BACKROOM', NULL, 'partially_received', '2026-11-01 09:00-08', '2026-11-04 17:00-08', 'USD'),
    ('default', 'STORE-PILOT-001', 'PO-2026-002', 'SUP-CARE-001', 'WH-PILOT-BACKROOM', NULL, 'sent', '2026-11-14 10:00-08', '2026-11-22 17:00-08', 'USD'),
    ('default', 'STORE-PILOT-001', 'PO-2026-003', 'SUP-HOME-001', 'WH-PILOT-BACKROOM', NULL, 'partially_received', '2026-11-05 08:30-08', '2026-11-09 17:00-08', 'USD'),
    ('default', 'STORE-PILOT-001', 'PO-2026-004', 'SUP-FOOD-001', 'WH-PILOT-BACKROOM', 'PR-2026-002', 'sent', '2026-11-17 13:00-08', '2026-11-24 17:00-08', 'USD'),
    ('default', 'STORE-PILOT-001', 'PO-2026-005', 'SUP-HOME-001', 'WH-PILOT-BACKROOM', NULL, 'cancelled', '2026-11-02 09:00-08', '2026-11-07 17:00-08', 'USD')
ON CONFLICT (tenant_id, po_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    supplier_id = EXCLUDED.supplier_id,
    warehouse_id = EXCLUDED.warehouse_id,
    source_request_id = EXCLUDED.source_request_id,
    status = EXCLUDED.status,
    ordered_at = EXCLUDED.ordered_at,
    expected_at = EXCLUDED.expected_at,
    currency_code = EXCLUDED.currency_code;

INSERT INTO retail.purchase_order_lines
    (tenant_id, store_id, po_id, line_no, sku, ordered_qty, unit_cost)
VALUES
    ('default', 'STORE-PILOT-001', 'PO-2026-001', 1, 'OAT-001', 100, 2.00),
    ('default', 'STORE-PILOT-001', 'PO-2026-002', 1, 'SOAP-001', 50, 2.50),
    ('default', 'STORE-PILOT-001', 'PO-2026-002', 2, 'SHAM-001', 30, 4.00),
    ('default', 'STORE-PILOT-001', 'PO-2026-003', 1, 'PAPER-001', 40, 5.00),
    ('default', 'STORE-PILOT-001', 'PO-2026-003', 2, 'RICE-001', 30, 6.00),
    ('default', 'STORE-PILOT-001', 'PO-2026-004', 1, 'COF-001', 30, 6.00),
    ('default', 'STORE-PILOT-001', 'PO-2026-004', 2, 'MIX-001', 20, 3.00),
    ('default', 'STORE-PILOT-001', 'PO-2026-005', 1, 'PAPER-001', 100, 5.00)
ON CONFLICT (tenant_id, po_id, line_no) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    sku = EXCLUDED.sku,
    ordered_qty = EXCLUDED.ordered_qty,
    unit_cost = EXCLUDED.unit_cost;

INSERT INTO retail.receipts
    (tenant_id, store_id, receipt_id, po_id, warehouse_id, received_at, status, reference_no)
VALUES
    ('default', 'STORE-PILOT-001', 'RCV-2026-001', 'PO-2026-001', 'WH-PILOT-BACKROOM', '2026-11-03 10:15-08', 'partial', 'ASN-SYN-001'),
    ('default', 'STORE-PILOT-001', 'RCV-2026-002', 'PO-2026-003', 'WH-PILOT-BACKROOM', '2026-11-09 14:40-08', 'partial', 'ASN-SYN-002')
ON CONFLICT (tenant_id, receipt_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    po_id = EXCLUDED.po_id,
    warehouse_id = EXCLUDED.warehouse_id,
    received_at = EXCLUDED.received_at,
    status = EXCLUDED.status,
    reference_no = EXCLUDED.reference_no;

INSERT INTO retail.receipt_lines
    (tenant_id, store_id, receipt_id, line_no, po_id, po_line_no, sku,
     received_qty, accepted_qty, rejected_qty)
VALUES
    ('default', 'STORE-PILOT-001', 'RCV-2026-001', 1, 'PO-2026-001', 1, 'OAT-001', 92, 88, 4),
    ('default', 'STORE-PILOT-001', 'RCV-2026-002', 1, 'PO-2026-003', 1, 'PAPER-001', 40, 39, 1),
    ('default', 'STORE-PILOT-001', 'RCV-2026-002', 2, 'PO-2026-003', 2, 'RICE-001', 28, 28, 0)
ON CONFLICT (tenant_id, receipt_id, line_no) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    po_id = EXCLUDED.po_id,
    po_line_no = EXCLUDED.po_line_no,
    sku = EXCLUDED.sku,
    received_qty = EXCLUDED.received_qty,
    accepted_qty = EXCLUDED.accepted_qty,
    rejected_qty = EXCLUDED.rejected_qty;

INSERT INTO retail.stock_transfers
    (tenant_id, store_id, transfer_id, from_warehouse_id, to_warehouse_id,
     status, created_at, shipped_at, received_at)
VALUES
    ('default', 'STORE-PILOT-001', 'TRF-2026-001', 'WH-PILOT-BACKROOM', 'WH-PILOT-FLOOR', 'received', '2026-11-10 07:30-08', '2026-11-10 08:00-08', '2026-11-10 08:35-08'),
    ('default', 'STORE-PILOT-001', 'TRF-2026-002', 'WH-PILOT-BACKROOM', 'WH-PILOT-FLOOR', 'in_transit', '2026-11-17 16:00-08', '2026-11-17 16:30-08', NULL)
ON CONFLICT (tenant_id, transfer_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    from_warehouse_id = EXCLUDED.from_warehouse_id,
    to_warehouse_id = EXCLUDED.to_warehouse_id,
    status = EXCLUDED.status,
    created_at = EXCLUDED.created_at,
    shipped_at = EXCLUDED.shipped_at,
    received_at = EXCLUDED.received_at;

INSERT INTO retail.stock_transfer_lines
    (tenant_id, store_id, transfer_id, line_no, sku, sent_qty, received_qty)
VALUES
    ('default', 'STORE-PILOT-001', 'TRF-2026-001', 1, 'OAT-001', 30, 28),
    ('default', 'STORE-PILOT-001', 'TRF-2026-001', 2, 'SOAP-001', 12, 12),
    ('default', 'STORE-PILOT-001', 'TRF-2026-002', 1, 'COF-001', 20, NULL),
    ('default', 'STORE-PILOT-001', 'TRF-2026-002', 2, 'PAPER-001', 15, NULL)
ON CONFLICT (tenant_id, transfer_id, line_no) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    sku = EXCLUDED.sku,
    sent_qty = EXCLUDED.sent_qty,
    received_qty = EXCLUDED.received_qty;

INSERT INTO retail.stock_counts
    (tenant_id, store_id, count_id, warehouse_id, status, planned_at, counted_at, posted_at)
VALUES
    ('default', 'STORE-PILOT-001', 'CNT-2026-001', 'WH-PILOT-FLOOR', 'posted', '2026-11-17 14:00-08', '2026-11-17 16:30-08', '2026-11-17 17:00-08'),
    ('default', 'STORE-PILOT-001', 'CNT-2026-002', 'WH-PILOT-BACKROOM', 'draft', '2026-11-19 09:00-08', NULL, NULL)
ON CONFLICT (tenant_id, count_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    warehouse_id = EXCLUDED.warehouse_id,
    status = EXCLUDED.status,
    planned_at = EXCLUDED.planned_at,
    counted_at = EXCLUDED.counted_at,
    posted_at = EXCLUDED.posted_at;

INSERT INTO retail.stock_count_lines
    (tenant_id, store_id, count_id, line_no, sku, system_qty, counted_qty, unit_cost)
VALUES
    ('default', 'STORE-PILOT-001', 'CNT-2026-001', 1, 'OAT-001', 52, 51, 2.00),
    ('default', 'STORE-PILOT-001', 'CNT-2026-001', 2, 'SOAP-001', 29, 30, 2.50),
    ('default', 'STORE-PILOT-001', 'CNT-2026-001', 3, 'PAPER-001', 2, 1, 5.00),
    ('default', 'STORE-PILOT-001', 'CNT-2026-001', 4, 'VIT-001', 4, 4, 7.00)
ON CONFLICT (tenant_id, count_id, line_no) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    sku = EXCLUDED.sku,
    system_qty = EXCLUDED.system_qty,
    counted_qty = EXCLUDED.counted_qty,
    unit_cost = EXCLUDED.unit_cost;

INSERT INTO retail.pos_sales
    (tenant_id, store_id, sale_id, receipt_no, sold_at, status, currency_code,
     subtotal, tax_amount, total_amount)
VALUES
    ('default', 'STORE-PILOT-001', 'SALE-2026-001', 'R-0001', '2026-11-18 12:10-08', 'completed', 'USD', 33.00, 2.64, 35.64),
    ('default', 'STORE-PILOT-001', 'SALE-2026-002', 'R-0002', '2026-11-17 15:10-08', 'completed', 'USD', 30.96, 2.48, 33.44),
    ('default', 'STORE-PILOT-001', 'SALE-2026-003', 'R-0003', '2026-11-16 13:25-08', 'completed', 'USD', 20.97, 1.68, 22.65),
    ('default', 'STORE-PILOT-001', 'SALE-2026-004', 'R-0004', '2026-11-16 10:30-08', 'cancelled', 'USD', 60.00, 4.80, 64.80),
    ('default', 'STORE-PILOT-001', 'SALE-2026-005', 'R-0005', '2026-11-15 11:40-08', 'completed', 'USD', 38.97, 3.12, 42.09),
    ('default', 'STORE-PILOT-001', 'SALE-2026-006', 'R-0006', '2026-11-14 16:05-08', 'completed', 'USD', 19.49, 1.56, 21.05),
    ('default', 'STORE-PILOT-001', 'SALE-2026-007', 'R-0007', '2026-11-13 14:50-08', 'completed', 'USD', 31.98, 2.56, 34.54),
    ('default', 'STORE-PILOT-001', 'SALE-2026-008', 'R-0008', '2026-11-12 12:30-08', 'completed', 'USD', 31.96, 2.56, 34.52),
    ('default', 'STORE-PILOT-001', 'SALE-2026-009', 'R-0009', '2026-11-11 13:00-08', 'completed', 'USD', 16.49, 1.32, 17.81),
    ('default', 'STORE-PILOT-001', 'SALE-2026-010', 'R-0010', '2026-11-10 12:00-08', 'completed', 'USD', 20.98, 1.68, 22.66),
    ('default', 'STORE-PILOT-001', 'SALE-2026-011', 'R-0011', '2026-11-08 16:10-08', 'completed', 'USD', 27.99, 2.24, 30.23),
    ('default', 'STORE-PILOT-001', 'SALE-2026-012', 'R-0012', '2026-11-05 10:10-08', 'cancelled', 'USD', 29.97, 2.40, 32.37),
    ('default', 'STORE-PILOT-001', 'SALE-2026-013', 'R-0013', '2026-11-03 09:30-08', 'completed', 'USD', 11.99, 0.96, 12.95)
ON CONFLICT (tenant_id, sale_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    receipt_no = EXCLUDED.receipt_no,
    sold_at = EXCLUDED.sold_at,
    status = EXCLUDED.status,
    currency_code = EXCLUDED.currency_code,
    subtotal = EXCLUDED.subtotal,
    tax_amount = EXCLUDED.tax_amount,
    total_amount = EXCLUDED.total_amount;

INSERT INTO retail.pos_sale_lines
    (tenant_id, store_id, sale_id, line_no, sku, quantity, unit_price,
     unit_cost, discount_amount, tax_amount, line_net_amount)
VALUES
    ('default', 'STORE-PILOT-001', 'SALE-2026-001', 1, 'COF-001', 2, 12.00, 6.00, 0.00, 1.92, 24.00),
    ('default', 'STORE-PILOT-001', 'SALE-2026-001', 2, 'OAT-001', 2, 4.50, 2.00, 0.00, 0.72, 9.00),
    ('default', 'STORE-PILOT-001', 'SALE-2026-002', 1, 'MIX-001', 3, 6.99, 3.00, 0.00, 1.68, 20.97),
    ('default', 'STORE-PILOT-001', 'SALE-2026-002', 2, 'PAPER-001', 1, 9.99, 5.00, 0.00, 0.80, 9.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-003', 1, 'SOAP-001', 2, 5.99, 2.50, 0.00, 0.96, 11.98),
    ('default', 'STORE-PILOT-001', 'SALE-2026-003', 2, 'SHAM-001', 1, 8.99, 4.00, 0.00, 0.72, 8.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-004', 1, 'COF-001', 5, 12.00, 6.00, 0.00, 4.80, 60.00),
    ('default', 'STORE-PILOT-001', 'SALE-2026-005', 1, 'VIT-001', 1, 14.99, 7.00, 0.00, 1.20, 14.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-005', 2, 'RICE-001', 2, 11.99, 6.00, 0.00, 1.92, 23.98),
    ('default', 'STORE-PILOT-001', 'SALE-2026-006', 1, 'OAT-001', 3, 4.50, 2.00, 0.00, 1.08, 13.50),
    ('default', 'STORE-PILOT-001', 'SALE-2026-006', 2, 'SOAP-001', 1, 5.99, 2.50, 0.00, 0.48, 5.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-007', 1, 'PAPER-001', 2, 9.99, 5.00, 0.00, 1.60, 19.98),
    ('default', 'STORE-PILOT-001', 'SALE-2026-007', 2, 'COF-001', 1, 12.00, 6.00, 0.00, 0.96, 12.00),
    ('default', 'STORE-PILOT-001', 'SALE-2026-008', 1, 'SHAM-001', 2, 8.99, 4.00, 0.00, 1.44, 17.98),
    ('default', 'STORE-PILOT-001', 'SALE-2026-008', 2, 'MIX-001', 2, 6.99, 3.00, 0.00, 1.12, 13.98),
    ('default', 'STORE-PILOT-001', 'SALE-2026-009', 1, 'OAT-001', 1, 4.50, 2.00, 0.00, 0.36, 4.50),
    ('default', 'STORE-PILOT-001', 'SALE-2026-009', 2, 'RICE-001', 1, 11.99, 6.00, 0.00, 0.96, 11.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-010', 1, 'SOAP-001', 1, 5.99, 2.50, 0.00, 0.48, 5.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-010', 2, 'VIT-001', 1, 14.99, 7.00, 0.00, 1.20, 14.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-011', 1, 'COF-001', 1, 12.00, 6.00, 0.00, 0.96, 12.00),
    ('default', 'STORE-PILOT-001', 'SALE-2026-011', 2, 'MIX-001', 1, 6.99, 3.00, 0.00, 0.56, 6.99),
    ('default', 'STORE-PILOT-001', 'SALE-2026-011', 3, 'OAT-001', 2, 4.50, 2.00, 0.00, 0.72, 9.00),
    ('default', 'STORE-PILOT-001', 'SALE-2026-012', 1, 'PAPER-001', 3, 9.99, 5.00, 0.00, 2.40, 29.97),
    ('default', 'STORE-PILOT-001', 'SALE-2026-013', 1, 'RICE-001', 1, 11.99, 6.00, 0.00, 0.96, 11.99)
ON CONFLICT (tenant_id, sale_id, line_no) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    sku = EXCLUDED.sku,
    quantity = EXCLUDED.quantity,
    unit_price = EXCLUDED.unit_price,
    unit_cost = EXCLUDED.unit_cost,
    discount_amount = EXCLUDED.discount_amount,
    tax_amount = EXCLUDED.tax_amount,
    line_net_amount = EXCLUDED.line_net_amount;

INSERT INTO retail.pos_returns
    (tenant_id, store_id, return_id, sale_id, status, requested_at, refunded_at, reason)
VALUES
    ('default', 'STORE-PILOT-001', 'RET-2026-001', 'SALE-2026-005', 'refunded', '2026-11-16 09:15-08', '2026-11-16 09:45-08', '包装破损'),
    ('default', 'STORE-PILOT-001', 'RET-2026-002', 'SALE-2026-010', 'refunded', '2026-11-11 10:00-08', '2026-11-11 10:20-08', '顾客改变主意'),
    ('default', 'STORE-PILOT-001', 'RET-2026-003', 'SALE-2026-001', 'requested', '2026-11-18 15:00-08', NULL, '顾客申请换货，尚未退款')
ON CONFLICT (tenant_id, return_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    sale_id = EXCLUDED.sale_id,
    status = EXCLUDED.status,
    requested_at = EXCLUDED.requested_at,
    refunded_at = EXCLUDED.refunded_at,
    reason = EXCLUDED.reason;

INSERT INTO retail.pos_return_lines
    (tenant_id, store_id, return_id, line_no, sale_id, sale_line_no, sku,
     return_qty, refund_amount)
VALUES
    ('default', 'STORE-PILOT-001', 'RET-2026-001', 1, 'SALE-2026-005', 1, 'VIT-001', 1, 16.19),
    ('default', 'STORE-PILOT-001', 'RET-2026-002', 1, 'SALE-2026-010', 1, 'SOAP-001', 1, 6.47),
    ('default', 'STORE-PILOT-001', 'RET-2026-003', 1, 'SALE-2026-001', 2, 'OAT-001', 1, 0.00)
ON CONFLICT (tenant_id, return_id, line_no) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    sale_id = EXCLUDED.sale_id,
    sale_line_no = EXCLUDED.sale_line_no,
    sku = EXCLUDED.sku,
    return_qty = EXCLUDED.return_qty,
    refund_amount = EXCLUDED.refund_amount;

INSERT INTO retail.product_batches
    (tenant_id, store_id, batch_id, warehouse_id, sku, received_at, expiry_date,
     quantity_on_hand, status)
VALUES
    ('default', 'STORE-PILOT-001', 'BAT-OAT-20261103-A', 'WH-PILOT-BACKROOM', 'OAT-001', '2026-11-03', '2026-12-05', 35, 'available'),
    ('default', 'STORE-PILOT-001', 'BAT-OAT-20261103-B', 'WH-PILOT-BACKROOM', 'OAT-001', '2026-11-03', '2027-01-15', 30, 'available'),
    ('default', 'STORE-PILOT-001', 'BAT-MIX-20261101-A', 'WH-PILOT-FLOOR', 'MIX-001', '2026-11-01', '2026-11-25', 10, 'available'),
    ('default', 'STORE-PILOT-001', 'BAT-MIX-20261101-B', 'WH-PILOT-FLOOR', 'MIX-001', '2026-11-01', '2026-12-20', 8, 'available'),
    ('default', 'STORE-PILOT-001', 'BAT-SHAM-20261101-A', 'WH-PILOT-FLOOR', 'SHAM-001', '2026-11-01', '2027-04-30', 11, 'available'),
    ('default', 'STORE-PILOT-001', 'BAT-VIT-20261101-A', 'WH-PILOT-FLOOR', 'VIT-001', '2026-11-01', '2026-12-01', 4, 'available')
ON CONFLICT (tenant_id, batch_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    warehouse_id = EXCLUDED.warehouse_id,
    sku = EXCLUDED.sku,
    received_at = EXCLUDED.received_at,
    expiry_date = EXCLUDED.expiry_date,
    quantity_on_hand = EXCLUDED.quantity_on_hand,
    status = EXCLUDED.status;

INSERT INTO retail.inventory_balances
    (tenant_id, store_id, warehouse_id, sku, on_hand_qty, reserved_qty, safety_stock, updated_at)
VALUES
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'COF-001', 60, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'OAT-001', 178, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'MIX-001', 70, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'SOAP-001', 56, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'SHAM-001', 60, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'PAPER-001', 78, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'VIT-001', 20, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-BACKROOM', 'RICE-001', 96, 0, 0, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'COF-001', 11, 0, 20, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'OAT-001', 49, 0, 24, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'MIX-001', 18, 0, 16, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'SOAP-001', 30, 0, 12, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'SHAM-001', 11, 0, 10, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'PAPER-001', 1, 0, 8, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'VIT-001', 4, 0, 6, '2026-11-19 00:00-08'),
    ('default', 'STORE-PILOT-001', 'WH-PILOT-FLOOR', 'RICE-001', 8, 0, 8, '2026-11-19 00:00-08')
ON CONFLICT (tenant_id, store_id, warehouse_id, sku) DO UPDATE SET
    on_hand_qty = EXCLUDED.on_hand_qty,
    reserved_qty = EXCLUDED.reserved_qty,
    safety_stock = EXCLUDED.safety_stock,
    updated_at = EXCLUDED.updated_at;

INSERT INTO retail.inventory_movements
    (tenant_id, store_id, movement_id, warehouse_id, sku, movement_type,
     quantity_delta, source_type, source_id, occurred_at)
VALUES
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-COF', 'WH-PILOT-BACKROOM', 'COF-001', 'receipt', 80, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-OAT', 'WH-PILOT-BACKROOM', 'OAT-001', 'receipt', 120, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-MIX', 'WH-PILOT-BACKROOM', 'MIX-001', 'receipt', 70, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-SOAP', 'WH-PILOT-BACKROOM', 'SOAP-001', 'receipt', 68, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-SHAM', 'WH-PILOT-BACKROOM', 'SHAM-001', 'receipt', 60, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-PAPER', 'WH-PILOT-BACKROOM', 'PAPER-001', 'receipt', 54, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-VIT', 'WH-PILOT-BACKROOM', 'VIT-001', 'receipt', 20, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-B-RICE', 'WH-PILOT-BACKROOM', 'RICE-001', 'receipt', 68, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-COF', 'WH-PILOT-FLOOR', 'COF-001', 'receipt', 15, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-OAT', 'WH-PILOT-FLOOR', 'OAT-001', 'receipt', 30, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-MIX', 'WH-PILOT-FLOOR', 'MIX-001', 'receipt', 24, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-SOAP', 'WH-PILOT-FLOOR', 'SOAP-001', 'receipt', 20, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-SHAM', 'WH-PILOT-FLOOR', 'SHAM-001', 'receipt', 14, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-PAPER', 'WH-PILOT-FLOOR', 'PAPER-001', 'receipt', 5, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-VIT', 'WH-PILOT-FLOOR', 'VIT-001', 'receipt', 5, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-OPEN-F-RICE', 'WH-PILOT-FLOOR', 'RICE-001', 'receipt', 12, 'opening_balance', 'OPEN-2026-11-01', '2026-11-01 00:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-RCV-OAT-88', 'WH-PILOT-BACKROOM', 'OAT-001', 'receipt', 88, 'receipt', 'RCV-2026-001', '2026-11-03 10:15-08'),
    ('default', 'STORE-PILOT-001', 'MOV-RCV-PAPER-39', 'WH-PILOT-BACKROOM', 'PAPER-001', 'receipt', 39, 'receipt', 'RCV-2026-002', '2026-11-09 14:40-08'),
    ('default', 'STORE-PILOT-001', 'MOV-RCV-RICE-28', 'WH-PILOT-BACKROOM', 'RICE-001', 'receipt', 28, 'receipt', 'RCV-2026-002', '2026-11-09 14:40-08'),
    ('default', 'STORE-PILOT-001', 'MOV-TRF1-OAT-OUT', 'WH-PILOT-BACKROOM', 'OAT-001', 'transfer_out', -30, 'transfer', 'TRF-2026-001', '2026-11-10 08:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-TRF1-OAT-IN', 'WH-PILOT-FLOOR', 'OAT-001', 'transfer_in', 28, 'transfer', 'TRF-2026-001', '2026-11-10 08:35-08'),
    ('default', 'STORE-PILOT-001', 'MOV-TRF1-SOAP-OUT', 'WH-PILOT-BACKROOM', 'SOAP-001', 'transfer_out', -12, 'transfer', 'TRF-2026-001', '2026-11-10 08:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-TRF1-SOAP-IN', 'WH-PILOT-FLOOR', 'SOAP-001', 'transfer_in', 12, 'transfer', 'TRF-2026-001', '2026-11-10 08:35-08'),
    ('default', 'STORE-PILOT-001', 'MOV-TRF2-COF-OUT', 'WH-PILOT-BACKROOM', 'COF-001', 'transfer_out', -20, 'transfer', 'TRF-2026-002', '2026-11-17 16:30-08'),
    ('default', 'STORE-PILOT-001', 'MOV-TRF2-PAPER-OUT', 'WH-PILOT-BACKROOM', 'PAPER-001', 'transfer_out', -15, 'transfer', 'TRF-2026-002', '2026-11-17 16:30-08'),
    ('default', 'STORE-PILOT-001', 'MOV-COUNT-OAT', 'WH-PILOT-FLOOR', 'OAT-001', 'stock_count', -1, 'stock_count', 'CNT-2026-001', '2026-11-17 17:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-COUNT-SOAP', 'WH-PILOT-FLOOR', 'SOAP-001', 'stock_count', 1, 'stock_count', 'CNT-2026-001', '2026-11-17 17:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-COUNT-PAPER', 'WH-PILOT-FLOOR', 'PAPER-001', 'stock_count', -1, 'stock_count', 'CNT-2026-001', '2026-11-17 17:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-001-COF', 'WH-PILOT-FLOOR', 'COF-001', 'sale', -2, 'sale', 'SALE-2026-001', '2026-11-18 12:10-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-001-OAT', 'WH-PILOT-FLOOR', 'OAT-001', 'sale', -2, 'sale', 'SALE-2026-001', '2026-11-18 12:10-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-002-MIX', 'WH-PILOT-FLOOR', 'MIX-001', 'sale', -3, 'sale', 'SALE-2026-002', '2026-11-17 15:10-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-002-PAPER', 'WH-PILOT-FLOOR', 'PAPER-001', 'sale', -1, 'sale', 'SALE-2026-002', '2026-11-17 15:10-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-003-SOAP', 'WH-PILOT-FLOOR', 'SOAP-001', 'sale', -2, 'sale', 'SALE-2026-003', '2026-11-16 13:25-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-003-SHAM', 'WH-PILOT-FLOOR', 'SHAM-001', 'sale', -1, 'sale', 'SALE-2026-003', '2026-11-16 13:25-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-005-VIT', 'WH-PILOT-FLOOR', 'VIT-001', 'sale', -1, 'sale', 'SALE-2026-005', '2026-11-15 11:40-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-005-RICE', 'WH-PILOT-FLOOR', 'RICE-001', 'sale', -2, 'sale', 'SALE-2026-005', '2026-11-15 11:40-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-006-OAT', 'WH-PILOT-FLOOR', 'OAT-001', 'sale', -3, 'sale', 'SALE-2026-006', '2026-11-14 16:05-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-006-SOAP', 'WH-PILOT-FLOOR', 'SOAP-001', 'sale', -1, 'sale', 'SALE-2026-006', '2026-11-14 16:05-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-007-PAPER', 'WH-PILOT-FLOOR', 'PAPER-001', 'sale', -2, 'sale', 'SALE-2026-007', '2026-11-13 14:50-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-007-COF', 'WH-PILOT-FLOOR', 'COF-001', 'sale', -1, 'sale', 'SALE-2026-007', '2026-11-13 14:50-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-008-SHAM', 'WH-PILOT-FLOOR', 'SHAM-001', 'sale', -2, 'sale', 'SALE-2026-008', '2026-11-12 12:30-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-008-MIX', 'WH-PILOT-FLOOR', 'MIX-001', 'sale', -2, 'sale', 'SALE-2026-008', '2026-11-12 12:30-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-009-OAT', 'WH-PILOT-FLOOR', 'OAT-001', 'sale', -1, 'sale', 'SALE-2026-009', '2026-11-11 13:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-009-RICE', 'WH-PILOT-FLOOR', 'RICE-001', 'sale', -1, 'sale', 'SALE-2026-009', '2026-11-11 13:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-010-SOAP', 'WH-PILOT-FLOOR', 'SOAP-001', 'sale', -1, 'sale', 'SALE-2026-010', '2026-11-10 12:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-010-VIT', 'WH-PILOT-FLOOR', 'VIT-001', 'sale', -1, 'sale', 'SALE-2026-010', '2026-11-10 12:00-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-011-COF', 'WH-PILOT-FLOOR', 'COF-001', 'sale', -1, 'sale', 'SALE-2026-011', '2026-11-08 16:10-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-011-MIX', 'WH-PILOT-FLOOR', 'MIX-001', 'sale', -1, 'sale', 'SALE-2026-011', '2026-11-08 16:10-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-011-OAT', 'WH-PILOT-FLOOR', 'OAT-001', 'sale', -2, 'sale', 'SALE-2026-011', '2026-11-08 16:10-08'),
    ('default', 'STORE-PILOT-001', 'MOV-SALE-013-RICE', 'WH-PILOT-FLOOR', 'RICE-001', 'sale', -1, 'sale', 'SALE-2026-013', '2026-11-03 09:30-08'),
    ('default', 'STORE-PILOT-001', 'MOV-RET-001-VIT', 'WH-PILOT-FLOOR', 'VIT-001', 'return', 1, 'return', 'RET-2026-001', '2026-11-16 09:45-08'),
    ('default', 'STORE-PILOT-001', 'MOV-RET-002-SOAP', 'WH-PILOT-FLOOR', 'SOAP-001', 'return', 1, 'return', 'RET-2026-002', '2026-11-11 10:20-08')
ON CONFLICT (tenant_id, movement_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    warehouse_id = EXCLUDED.warehouse_id,
    sku = EXCLUDED.sku,
    movement_type = EXCLUDED.movement_type,
    quantity_delta = EXCLUDED.quantity_delta,
    source_type = EXCLUDED.source_type,
    source_id = EXCLUDED.source_id,
    occurred_at = EXCLUDED.occurred_at;

COMMIT;
