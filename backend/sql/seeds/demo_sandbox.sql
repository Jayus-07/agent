-- ============================================================
-- demo_sandbox.sql — 客服演示业务沙盒播种数据
-- 方案: docs/customer-service/演示沙盒方案-2026-09-17.md (SB-1)
--
-- ⚠️ 全部为「模拟数据」，仅供演示沙盒使用：
--   - 归属演示客户 customer_id = 99001（对应配置 CS_DEMO_CUSTOMER_ID）
--   - 订单号统一 DEMO- 前缀；SKU 统一 DEMO-SKU- 前缀
--   - 不会触碰任何非 DEMO 前缀的真实数据（删除/插入均带前缀守卫）
--
-- 目标库: agent_business（业务库，含 "order" / product / customer schema）
-- 执行方式（幂等，可重复执行）:
--   docker compose exec -T postgres psql -U postgres -d agent_business \
--     < backend/sql/seeds/demo_sandbox.sql
--
-- 剧本覆盖（与演示场景对应）:
--   DEMO-1001  已签收 1 天     → 订单查询 / 已签收详情
--   DEMO-1002  已签收 3 天     → 退款资格 ✅（场景 A 主角，商品损坏退款）
--   DEMO-1003  已发货, 轨迹停滞 5 天 → 物流延误 / 催件（场景 B 主角，
--                                停滞天数由 MockTraceProvider 内置轨迹控制）
--   DEMO-1006  处理中(pending) → 未发货订单查询
--   DEMO-1009  已取消          → 退款资格 ❌（不可退分支演示）
--   DEMO-1012  处理中(paid)    → 待发货 + 退款资格 ✅ 分支
--   其余        常规覆盖：多渠道/多状态，凑足 15 条演示订单
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 0. 演示客户（与 AccountService 查询的 customer.customers 对应）
-- ------------------------------------------------------------
DELETE FROM customer.customers WHERE id = 99001;
INSERT INTO customer.customers (id, name, gender, level, register_time)
VALUES (99001, '演示用户（模拟数据）', NULL, 'gold', now() - INTERVAL '90 days');
SELECT setval(pg_get_serial_sequence('customer.customers', 'id'),
              GREATEST((SELECT MAX(id) FROM customer.customers), 1));

-- ------------------------------------------------------------
-- 1. 演示商品（商品咨询 / SQL 导购用；规格材质详见 demo-kb 商品目录）
-- ------------------------------------------------------------
DELETE FROM product.products WHERE sku LIKE 'DEMO-SKU-%';
INSERT INTO product.products (id, sku, product_name, category_id, brand, cost_price, sale_price, status) VALUES
  (99101, 'DEMO-SKU-001', '蓝牙降噪耳机 Pro（演示）', 1, 'DemoBrand', 180.00, 299.00, 'active'),
  (99102, 'DEMO-SKU-002', '便携榨汁杯 400ml（演示）', 2, 'DemoBrand', 60.00, 129.00, 'active'),
  (99103, 'DEMO-SKU-003', '智能体脂秤 S2（演示）',   2, 'DemoHome',  55.00,  99.00, 'active'),
  (99104, 'DEMO-SKU-004', '户外防水音箱 mini（演示）', 1, 'DemoBrand', 90.00, 199.00, 'active'),
  (99105, 'DEMO-SKU-005', '石墨烯暖手宝（演示）',     2, 'DemoHome',  40.00,  79.00, 'active');
SELECT setval(pg_get_serial_sequence('product.products', 'id'),
              GREATEST((SELECT MAX(id) FROM product.products), 1));

-- ------------------------------------------------------------
-- 2. 演示订单 ×15（customer_id = 99001）
--    status 枚举与客服服务层一致: pending/paid/shipped/completed/cancelled
-- ------------------------------------------------------------
DELETE FROM "order".order_items WHERE order_id BETWEEN 99001 AND 99015;
DELETE FROM "order".orders WHERE order_no LIKE 'DEMO-%';
INSERT INTO "order".orders (id, order_no, customer_id, total_amount, status, payment_status, created_at) VALUES
  (99001, 'DEMO-1001', 99001, 598.00, 'completed', 'paid',     now() - INTERVAL '6 days'),
  (99002, 'DEMO-1002', 99001, 129.00, 'completed', 'paid',     now() - INTERVAL '8 days'),
  (99003, 'DEMO-1003', 99001, 897.00, 'shipped',   'paid',     now() - INTERVAL '7 days'),
  (99004, 'DEMO-1004', 99001, 259.00, 'completed', 'paid',     now() - INTERVAL '10 days'),
  (99005, 'DEMO-1005', 99001, 199.50, 'completed', 'paid',     now() - INTERVAL '12 days'),
  (99006, 'DEMO-1006', 99001, 358.00, 'pending',   'unpaid',   now() - INTERVAL '1 days'),
  (99007, 'DEMO-1007', 99001, 459.00, 'completed', 'paid',     now() - INTERVAL '14 days'),
  (99008, 'DEMO-1008', 99001, 199.00, 'completed', 'paid',     now() - INTERVAL '16 days'),
  (99009, 'DEMO-1009', 99001, 299.00, 'cancelled', 'refunded', now() - INTERVAL '18 days'),
  (99010, 'DEMO-1010', 99001, 378.00, 'shipped',   'paid',     now() - INTERVAL '4 days'),
  (99011, 'DEMO-1011', 99001,  99.00, 'completed', 'paid',     now() - INTERVAL '20 days'),
  (99012, 'DEMO-1012', 99001, 299.00, 'paid',      'paid',     now() - INTERVAL '2 days'),
  (99013, 'DEMO-1013', 99001, 387.00, 'completed', 'paid',     now() - INTERVAL '22 days'),
  (99014, 'DEMO-1014', 99001, 316.00, 'completed', 'paid',     now() - INTERVAL '24 days'),
  (99015, 'DEMO-1015', 99001,  79.80, 'cancelled', 'refunded', now() - INTERVAL '26 days');
SELECT setval(pg_get_serial_sequence('"order".orders', 'id'),
              GREATEST((SELECT MAX(id) FROM "order".orders), 1));

-- ------------------------------------------------------------
-- 3. 订单明细（product_id 指向演示商品）
-- ------------------------------------------------------------
INSERT INTO "order".order_items (order_id, product_id, quantity, price, cost) VALUES
  (99001, 99101, 2, 299.00, 360.00),
  (99002, 99102, 1, 129.00,  60.00),
  (99003, 99101, 3, 299.00, 540.00),
  (99004, 99103, 1, 259.00, 120.00),
  (99005, 99105, 5,  39.90, 200.00),
  (99006, 99102, 2, 179.00, 120.00),
  (99007, 99104, 1, 459.00, 230.00),
  (99008, 99103, 1, 199.00, 100.00),
  (99009, 99101, 1, 299.00, 180.00),
  (99010, 99105, 2, 189.00,  80.00),
  (99011, 99105, 1,  99.00,  55.00),
  (99012, 99101, 1, 299.00, 180.00),
  (99013, 99102, 3, 129.00, 180.00),
  (99014, 99104, 4,  79.00, 360.00),
  (99015, 99105, 2,  39.90,  80.00);

COMMIT;

-- ------------------------------------------------------------
-- 验证（执行后人工抽查）:
--   SELECT order_no, status, payment_status, created_at
--     FROM "order".orders WHERE customer_id = 99001 ORDER BY order_no;
--   SELECT count(*) FROM "order".order_items WHERE order_id BETWEEN 99001 AND 99015; -- 15
-- ------------------------------------------------------------
