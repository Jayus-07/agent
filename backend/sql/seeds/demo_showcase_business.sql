-- ═══════════════════════════════════════════════════════════════════
-- demo_showcase_business.sql — 演示业务数据（agent_business 库）
--
-- 前置：先执行 demo_showcase_users.sql（agent_memory 库）并拿到各
--       demo 账号的 uid，代入下方 psql 变量。
--
-- 执行：
--   docker exec -i agent-postgres-1 psql -U postgres -d agent_business \
--     -v demo_fuzhou_uid=<uid1> -v demo_li_uid=<uid2> -v demo_wang_uid=<uid3> \
--     < demo_showcase_business.sql
--
-- 幂等：order_no 前缀 DEMO-SHOW- 唯一约束 + WHERE NOT EXISTS。
-- 注意：order.refunds 无 customer_id（归属经 order_id→orders）。
-- ═══════════════════════════════════════════════════════════════════

-- 1. 客户档案（id 显式 = auth uid，客服域按 customer_id::text = user_id 映射）
INSERT INTO customer.customers (id, name, gender, level, register_time)
VALUES
  (:demo_fuzhou_uid, '演示·福州行', 'M', 'VIP',  NOW() - INTERVAL '90 days'),
  (:demo_li_uid,     '演示·李经理', 'F', '金卡', NOW() - INTERVAL '60 days'),
  (:demo_wang_uid,   '演示·王同学', 'M', '普通', NOW() - INTERVAL '30 days')
ON CONFLICT (id) DO NOTHING;

-- 2. 订单（状态全覆盖；demo_fuzhou 两条含可退款单支撑点选演示）
INSERT INTO "order".orders (order_no, customer_id, total_amount, status, payment_status, created_at)
SELECT v.order_no, v.uid, v.amount, v.status, v.pay_status, v.at
FROM (VALUES
  ('DEMO-SHOW-O-001', :demo_fuzhou_uid,  599.00, 'completed', 'paid',     NOW() - INTERVAL '5 days'),
  ('DEMO-SHOW-O-002', :demo_fuzhou_uid, 1299.00, 'shipped',   'paid',     NOW() - INTERVAL '2 days'),
  ('DEMO-SHOW-O-003', :demo_fuzhou_uid,  259.00, 'cancelled', 'refunded', NOW() - INTERVAL '40 days'),
  ('DEMO-SHOW-O-004', :demo_fuzhou_uid,   89.00, 'pending',   'unpaid',   NOW() - INTERVAL '1 days'),
  ('DEMO-SHOW-O-005', :demo_li_uid,      459.00, 'paid',      'paid',     NOW() - INTERVAL '3 days'),
  ('DEMO-SHOW-O-006', :demo_li_uid,     2399.00, 'completed', 'paid',     NOW() - INTERVAL '50 days'),
  ('DEMO-SHOW-O-007', :demo_wang_uid,    199.00, 'completed', 'paid',     NOW() - INTERVAL '20 days'),
  ('DEMO-SHOW-O-008', :demo_wang_uid,    329.00, 'shipped',   'paid',     NOW() - INTERVAL '6 days')
) AS v(order_no, uid, amount, status, pay_status, at)
WHERE NOT EXISTS (
  SELECT 1 FROM "order".orders o WHERE o.order_no = v.order_no
);

-- 3. 演示商品（SKU 前缀 DEMO-SHOW，与种子库互不影响）
INSERT INTO product.products (sku, product_name, brand, sale_price, cost_price, status)
SELECT v.sku, v.name, '演示品牌', v.price, v.cost, 'active'
FROM (VALUES
  ('DEMO-SHOW-SKU-1', '智能降噪耳机', 599.00, 380.00),
  ('DEMO-SHOW-SKU-2', '机械键盘 PRO', 1299.00, 900.00),
  ('DEMO-SHOW-SKU-3', '便携咖啡机',   459.00, 300.00),
  ('DEMO-SHOW-SKU-4', '人体工学椅',   2399.00, 1700.00),
  ('DEMO-SHOW-SKU-5', '桌面加湿器',   199.00, 120.00),
  ('DEMO-SHOW-SKU-6', '运动水壶',     329.00, 200.00)
) AS v(sku, name, price, cost)
WHERE NOT EXISTS (
  SELECT 1 FROM product.products p WHERE p.sku = v.sku
);

-- 4. 订单明细（退款候选的商品名数据源）
INSERT INTO "order".order_items (order_id, product_id, quantity, price, cost)
SELECT o.id, p.id, 1, v.price, v.cost
FROM (VALUES
  ('DEMO-SHOW-O-001', 'DEMO-SHOW-SKU-1', 599.00, 380.00),
  ('DEMO-SHOW-O-002', 'DEMO-SHOW-SKU-2', 1299.00, 900.00),
  ('DEMO-SHOW-O-003', 'DEMO-SHOW-SKU-6', 259.00, 200.00),
  ('DEMO-SHOW-O-005', 'DEMO-SHOW-SKU-3', 459.00, 300.00),
  ('DEMO-SHOW-O-006', 'DEMO-SHOW-SKU-4', 2399.00, 1700.00),
  ('DEMO-SHOW-O-007', 'DEMO-SHOW-SKU-5', 199.00, 120.00),
  ('DEMO-SHOW-O-008', 'DEMO-SHOW-SKU-6', 329.00, 200.00)
) AS v(order_no, sku, price, cost)
JOIN "order".orders o ON o.order_no = v.order_no
JOIN product.products p ON p.sku = v.sku
WHERE NOT EXISTS (
  SELECT 1 FROM "order".order_items oi WHERE oi.order_id = o.id
);

-- 4. 退款记录（挂 O-003；O-001/O-002 保持可退状态供点选演示）
INSERT INTO "order".refunds (order_id, product_id, refund_amount, reason, created_at)
SELECT o.id, NULL, o.total_amount, '七天无理由退货', o.created_at + INTERVAL '1 day'
FROM "order".orders o
WHERE o.order_no = 'DEMO-SHOW-O-003'
  AND NOT EXISTS (SELECT 1 FROM "order".refunds r WHERE r.order_id = o.id);

-- 5. 核对输出
SELECT o.order_no, o.customer_id, c.name, o.status, o.payment_status, o.total_amount
FROM "order".orders o
JOIN customer.customers c ON c.id = o.customer_id
WHERE o.order_no LIKE 'DEMO-SHOW-%'
ORDER BY o.order_no;
