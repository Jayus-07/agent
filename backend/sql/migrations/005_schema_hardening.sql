-- =====================================================
-- 005_schema_hardening.sql
-- 生产就绪 P1: 补外键 + CHECK 约束 + 索引
--
-- 设计动机:
--   - 001 建了 50% 的 FK（跨域 join 的关键字段缺失）
--   - 0 个 CHECK 约束（status/gender/rating 等枚举字段无约束）
--   - 高频 join 的 product_id / customer_id 无索引
--
-- 执行: psql -U postgres -d agent_business -f 005_schema_hardening.sql
-- 幂等: 所有 ALTER TABLE 前检查约束是否已存在
-- =====================================================

-- ═══ 1. 补缺失外键 ═══

-- order.orders.customer_id → customer.customers.id
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_orders_customer') THEN
        ALTER TABLE "order".orders
            ADD CONSTRAINT fk_orders_customer
            FOREIGN KEY (customer_id) REFERENCES customer.customers(id);
    END IF;
END $$;

-- order.order_items.product_id → product.products.id
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_order_items_product') THEN
        ALTER TABLE "order".order_items
            ADD CONSTRAINT fk_order_items_product
            FOREIGN KEY (product_id) REFERENCES product.products(id);
    END IF;
END $$;

-- order.refunds.product_id → product.products.id
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_refunds_product') THEN
        ALTER TABLE "order".refunds
            ADD CONSTRAINT fk_refunds_product
            FOREIGN KEY (product_id) REFERENCES product.products(id);
    END IF;
END $$;

-- inventory.inventory.product_id → product.products.id
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_inventory_product') THEN
        ALTER TABLE inventory.inventory
            ADD CONSTRAINT fk_inventory_product
            FOREIGN KEY (product_id) REFERENCES product.products(id);
    END IF;
END $$;

-- inventory.purchase_orders.product_id → product.products.id
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_purchase_orders_product') THEN
        ALTER TABLE inventory.purchase_orders
            ADD CONSTRAINT fk_purchase_orders_product
            FOREIGN KEY (product_id) REFERENCES product.products(id);
    END IF;
END $$;

-- customer.customer_behavior.product_id → product.products.id
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_behavior_product') THEN
        ALTER TABLE customer.customer_behavior
            ADD CONSTRAINT fk_behavior_product
            FOREIGN KEY (product_id) REFERENCES product.products(id);
    END IF;
END $$;

-- ═══ 2. 补 CHECK 约束 ═══

-- product.products.status ∈ (active, inactive)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_product_status') THEN
        ALTER TABLE product.products
            ADD CONSTRAINT chk_product_status
            CHECK (status IN ('active', 'inactive'));
    END IF;
END $$;

-- product.products: cost_price > 0, sale_price > 0
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_product_prices') THEN
        ALTER TABLE product.products
            ADD CONSTRAINT chk_product_prices
            CHECK (cost_price IS NULL OR cost_price > 0);
        ALTER TABLE product.products
            ADD CONSTRAINT chk_product_sale_price
            CHECK (sale_price IS NULL OR sale_price > 0);
    END IF;
END $$;

-- order.orders.status
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_order_status') THEN
        ALTER TABLE "order".orders
            ADD CONSTRAINT chk_order_status
            CHECK (status IN ('pending', 'paid', 'shipped', 'completed', 'cancelled'));
    END IF;
END $$;

-- order.order_items: quantity > 0, price > 0
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_oi_quantity') THEN
        ALTER TABLE "order".order_items
            ADD CONSTRAINT chk_oi_quantity
            CHECK (quantity IS NULL OR quantity > 0);
        ALTER TABLE "order".order_items
            ADD CONSTRAINT chk_oi_price
            CHECK (price IS NULL OR price > 0);
    END IF;
END $$;

-- inventory.inventory: stock_quantity >= 0, safety_stock >= 0
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_inv_quantity') THEN
        ALTER TABLE inventory.inventory
            ADD CONSTRAINT chk_inv_quantity
            CHECK (stock_quantity >= 0);
        ALTER TABLE inventory.inventory
            ADD CONSTRAINT chk_inv_safety
            CHECK (safety_stock >= 0);
    END IF;
END $$;

-- customer.customers.gender ∈ (M, F)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_customer_gender') THEN
        ALTER TABLE customer.customers
            ADD CONSTRAINT chk_customer_gender
            CHECK (gender IS NULL OR gender IN ('M', 'F'));
    END IF;
END $$;

-- customer.customer_behavior.event_type
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_behavior_type') THEN
        ALTER TABLE customer.customer_behavior
            ADD CONSTRAINT chk_behavior_type
            CHECK (event_type IN ('view', 'click', 'add_cart', 'favorite'));
    END IF;
END $$;

-- crawler.product_reviews: rating BETWEEN 1 AND 5, sentiment
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_review_rating') THEN
        ALTER TABLE crawler.product_reviews
            ADD CONSTRAINT chk_review_rating
            CHECK (rating IS NULL OR (rating BETWEEN 1 AND 5));
    END IF;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_review_sentiment') THEN
        ALTER TABLE crawler.product_reviews
            ADD CONSTRAINT chk_review_sentiment
            CHECK (sentiment IS NULL OR sentiment IN ('positive', 'negative', 'neutral'));
    END IF;
END $$;

-- finance: amount > 0
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_expense_amount') THEN
        ALTER TABLE finance.expenses
            ADD CONSTRAINT chk_expense_amount
            CHECK (amount > 0);
    END IF;
END $$;

-- ═══ 3. 补缺失索引（高频 join + 筛选列）═══

-- order.orders(customer_id) — 跨域 join 到 customer
CREATE INDEX IF NOT EXISTS idx_orders_customer ON "order".orders(customer_id);

-- order.order_items(order_id) — 订单→明细 1:N join
CREATE INDEX IF NOT EXISTS idx_oi_order ON "order".order_items(order_id);

-- order.refunds(order_id) — 订单→退款 1:N join
CREATE INDEX IF NOT EXISTS idx_refunds_order ON "order".refunds(order_id);

-- inventory.inventory(warehouse_id) — 多仓查询
CREATE INDEX IF NOT EXISTS idx_inv_warehouse ON inventory.inventory(warehouse_id);

-- inventory.inventory(product_id, warehouse_id) — 复合索引（商品+仓库维度）
CREATE INDEX IF NOT EXISTS idx_inv_product_warehouse ON inventory.inventory(product_id, warehouse_id);

-- inventory.purchase_orders(product_id) — 商品采购历史
CREATE INDEX IF NOT EXISTS idx_po_product ON inventory.purchase_orders(product_id);

-- inventory.purchase_orders(status) — 采购状态筛选
CREATE INDEX IF NOT EXISTS idx_po_status ON inventory.purchase_orders(status);

-- customer.customer_behavior(product_id) — 商品漏斗分析
CREATE INDEX IF NOT EXISTS idx_behavior_product ON customer.customer_behavior(product_id);

-- customer.customer_behavior(created_at) — 时间范围筛选
CREATE INDEX IF NOT EXISTS idx_behavior_time ON customer.customer_behavior(created_at);

-- ai.agent_tasks(session_id) — 会话维度查询
CREATE INDEX IF NOT EXISTS idx_tasks_session ON ai.agent_tasks(session_id);

-- ai.agent_tasks(status) — 状态筛选
CREATE INDEX IF NOT EXISTS idx_tasks_status ON ai.agent_tasks(status);

-- crawler.product_reviews(product_id) — 商品评价关联
CREATE INDEX IF NOT EXISTS idx_reviews_product ON crawler.product_reviews(product_id);

-- crawler.product_reviews(sentiment) — 情感筛选
CREATE INDEX IF NOT EXISTS idx_reviews_sentiment ON crawler.product_reviews(sentiment);
