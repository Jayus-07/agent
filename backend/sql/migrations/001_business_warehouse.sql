-- =====================================================
-- 001_business_warehouse.sql
-- 业务数据仓库初始化 — 按业务域拆分 7 个 schema
-- 适配 schema_config.py 的 19 张核心表
--
-- 执行：psql -U postgres -d demo -f 001_business_warehouse.sql
-- 幂等：CREATE 全部带 IF NOT EXISTS；DROP 用 IF EXISTS
-- =====================================================

-- ═══ 0. Schema 域（按业务域）═══
CREATE SCHEMA IF NOT EXISTS product;       -- 商品域
CREATE SCHEMA IF NOT EXISTS "order";       -- 订单域（避免与 ORDER 关键字冲突，加引号）
CREATE SCHEMA IF NOT EXISTS inventory;     -- 库存域
CREATE SCHEMA IF NOT EXISTS customer;      -- 客户域
CREATE SCHEMA IF NOT EXISTS crawler;       -- 爬虫竞品域
CREATE SCHEMA IF NOT EXISTS finance;       -- 财务域
CREATE SCHEMA IF NOT EXISTS ai;            -- Agent 运行数据

-- ═══ 1. 商品域 product ═══

CREATE TABLE IF NOT EXISTS product.categories (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    parent_id   INTEGER REFERENCES product.categories(id)
);

CREATE TABLE IF NOT EXISTS product.products (
    id            SERIAL PRIMARY KEY,
    sku           VARCHAR(64) UNIQUE NOT NULL,
    product_name  VARCHAR(256) NOT NULL,
    category_id   INTEGER REFERENCES product.categories(id),
    brand         VARCHAR(128),
    cost_price    NUMERIC(10,2),
    sale_price    NUMERIC(10,2),
    status        VARCHAR(32) DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_products_category ON product.products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_created  ON product.products(created_at);

CREATE TABLE IF NOT EXISTS product.product_tags (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER REFERENCES product.products(id) ON DELETE CASCADE,
    tag         VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_tags_product ON product.product_tags(product_id);

-- ═══ 2. 订单域 order（带引号）═══

CREATE TABLE IF NOT EXISTS "order".orders (
    id              SERIAL PRIMARY KEY,
    order_no        VARCHAR(64) UNIQUE NOT NULL,
    customer_id     INTEGER,
    total_amount    NUMERIC(12,2),
    status          VARCHAR(32),            -- pending/paid/shipped/completed/cancelled
    payment_status  VARCHAR(32),
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_orders_created ON "order".orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON "order".orders(status);

CREATE TABLE IF NOT EXISTS "order".order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER REFERENCES "order".orders(id) ON DELETE CASCADE,
    product_id  INTEGER,
    quantity    INTEGER,
    price       NUMERIC(10,2),
    cost        NUMERIC(10,2)               -- 用于利润分析
);
CREATE INDEX IF NOT EXISTS idx_oi_product ON "order".order_items(product_id);

CREATE TABLE IF NOT EXISTS "order".refunds (
    id            SERIAL PRIMARY KEY,
    order_id      INTEGER REFERENCES "order".orders(id),
    product_id    INTEGER,
    refund_amount NUMERIC(10,2),
    reason        VARCHAR(256),
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_refunds_product ON "order".refunds(product_id);

-- ═══ 3. 库存域 inventory ═══

CREATE TABLE IF NOT EXISTS inventory.warehouses (
    id        SERIAL PRIMARY KEY,
    name      VARCHAR(128),
    location  VARCHAR(128)
);

CREATE TABLE IF NOT EXISTS inventory.inventory (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER,
    warehouse_id    INTEGER REFERENCES inventory.warehouses(id),
    stock_quantity  INTEGER DEFAULT 0,
    safety_stock    INTEGER DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_inv_product ON inventory.inventory(product_id);

CREATE TABLE IF NOT EXISTS inventory.purchase_orders (
    id          SERIAL PRIMARY KEY,
    supplier_id INTEGER,
    product_id  INTEGER,
    quantity    INTEGER,
    status      VARCHAR(32),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ═══ 4. 客户域 customer ═══

CREATE TABLE IF NOT EXISTS customer.customers (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(128),
    gender        VARCHAR(8),
    level         VARCHAR(32),              -- 普通/银卡/金卡/VIP
    register_time TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS customer.customer_behavior (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customer.customers(id),
    event_type  VARCHAR(32),                -- view/click/add_cart/favorite
    product_id  INTEGER,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_behavior_customer ON customer.customer_behavior(customer_id);

-- ═══ 5. 爬虫竞品域 crawler ═══

CREATE TABLE IF NOT EXISTS crawler.competitor_products (
    id            SERIAL PRIMARY KEY,
    platform      VARCHAR(64),              -- Amazon/TikTok Shop/淘宝
    brand         VARCHAR(128),
    product_name  VARCHAR(256),
    category      VARCHAR(128),
    url           VARCHAR(512)
);

CREATE TABLE IF NOT EXISTS crawler.competitor_price (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER REFERENCES crawler.competitor_products(id) ON DELETE CASCADE,
    price       NUMERIC(10,2),
    discount    NUMERIC(4,2),
    crawl_time  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comp_price_time ON crawler.competitor_price(crawl_time);

CREATE TABLE IF NOT EXISTS crawler.product_reviews (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER,                    -- 可以指向 product.products.id 或 crawler.competitor_products.id
    rating      INTEGER,
    review_text TEXT,
    sentiment   VARCHAR(16),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ═══ 6. 财务域 finance ═══

CREATE TABLE IF NOT EXISTS finance.expenses (
    id     SERIAL PRIMARY KEY,
    type   VARCHAR(64),                     -- 广告费/物流费/人工
    amount NUMERIC(12,2),
    date   DATE DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS finance.daily_profit (
    date    DATE PRIMARY KEY,
    revenue NUMERIC(12,2),
    cost    NUMERIC(12,2),
    profit  NUMERIC(12,2)
);

-- ═══ 7. Agent 运行数据 ai ═══

CREATE TABLE IF NOT EXISTS ai.agent_tasks (
    id         SERIAL PRIMARY KEY,
    session_id VARCHAR(128),
    user_query TEXT,
    task_type  VARCHAR(64),
    status     VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai.agent_trace (
    id         SERIAL PRIMARY KEY,
    task_id    INTEGER REFERENCES ai.agent_tasks(id) ON DELETE CASCADE,
    node       VARCHAR(64),
    input      JSONB,
    output     JSONB,
    duration   NUMERIC(8,3),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trace_task ON ai.agent_trace(task_id);

-- ═══ 8. Seed 数据 — 让 SQL Agent 立刻能跑通 ═══

-- 商品分类
INSERT INTO product.categories (id, name, parent_id) VALUES
    (1, '服装', NULL),
    (2, '外套', 1),
    (3, '裙子', 1),
    (4, '饰品', NULL),
    (5, '项链', 4),
    (6, '戒指', 4)
ON CONFLICT (id) DO NOTHING;

-- 商品（5-10 条覆盖 10 张核心表可分析场景）
INSERT INTO product.products (id, sku, product_name, category_id, brand, cost_price, sale_price, status, created_at) VALUES
    (1, 'A001', '珍珠项链',    5, 'PearlCo', 50.00,  199.00, 'active', NOW() - INTERVAL '5 days'),
    (2, 'A002', '羊毛外套',    2, 'Warmth',  200.00, 599.00, 'active', NOW() - INTERVAL '20 days'),
    (3, 'A003', '丝绸连衣裙',  3, 'SilkLine', 180.00, 599.00, 'active', NOW() - INTERVAL '25 days'),
    (4, 'A004', '银戒指',      6, 'SilverArt', 30.00, 99.00,  'active', NOW() - INTERVAL '2 days'),
    (5, 'A005', '针织毛衣',    2, 'Warmth',  120.00, 299.00, 'active', NOW() - INTERVAL '15 days')
ON CONFLICT (id) DO NOTHING;

-- 商品标签
INSERT INTO product.product_tags (product_id, tag) VALUES
    (1, '爆款'),
    (3, '新品'),
    (2, '高利润'),
    (1, '清仓')
ON CONFLICT DO NOTHING;

-- 仓库
INSERT INTO inventory.warehouses (id, name, location) VALUES
    (1, '主仓-深圳', '深圳'),
    (2, '海外仓-洛杉矶', 'Los Angeles')
ON CONFLICT (id) DO NOTHING;

-- 库存（含 1 条触发预警：珍珠项链库存 < 安全库存）
INSERT INTO inventory.inventory (product_id, warehouse_id, stock_quantity, safety_stock, updated_at) VALUES
    (1, 1, 20,  100, NOW()),    -- 触发预警
    (2, 1, 300, 50,  NOW()),
    (3, 1, 250, 100, NOW()),
    (4, 1, 80,  100, NOW()),    -- 触发预警
    (5, 2, 200, 50,  NOW()),
    (1, 2, 50,  100, NOW())
ON CONFLICT DO NOTHING;

-- 客户
INSERT INTO customer.customers (id, name, gender, level, register_time) VALUES
    (1, '张小明', 'M', 'VIP', NOW() - INTERVAL '180 days'),
    (2, '李小红', 'F', '金卡', NOW() - INTERVAL '90 days'),
    (3, '王大锤', 'M', '普通', NOW() - INTERVAL '10 days')
ON CONFLICT (id) DO NOTHING;

-- 客户行为
INSERT INTO customer.customer_behavior (customer_id, event_type, product_id, created_at) VALUES
    (1, 'view',     1, NOW() - INTERVAL '3 days'),
    (1, 'add_cart', 1, NOW() - INTERVAL '3 days'),
    (2, 'view',     3, NOW() - INTERVAL '1 day'),
    (2, 'favorite', 3, NOW() - INTERVAL '1 day'),
    (3, 'click',    4, NOW())
ON CONFLICT DO NOTHING;

-- 订单（最近一个月，含 status 多样性）
INSERT INTO "order".orders (id, order_no, customer_id, total_amount, status, payment_status, created_at) VALUES
    (1, 'ORD20250805001', 1, 199.00, 'completed', 'paid', NOW() - INTERVAL '5 days'),
    (2, 'ORD20250810002', 2, 599.00, 'completed', 'paid', NOW() - INTERVAL '25 days'),
    (3, 'ORD20250815003', 3, 99.00,  'shipped',   'paid', NOW() - INTERVAL '20 days'),
    (4, 'ORD20250820004', 1, 599.00, 'completed', 'paid', NOW() - INTERVAL '15 days')
ON CONFLICT (id) DO NOTHING;

-- 订单明细
INSERT INTO "order".order_items (order_id, product_id, quantity, price, cost) VALUES
    (1, 1, 1, 199.00, 50.00),
    (2, 3, 1, 599.00, 180.00),
    (3, 4, 1, 99.00,  30.00),
    (4, 2, 1, 599.00, 200.00)
ON CONFLICT DO NOTHING;

-- 退款（让 Agent 能分析"高退款率商品"）
INSERT INTO "order".refunds (order_id, product_id, refund_amount, reason, created_at) VALUES
    (1, 1, 199.00, '商品描述不符', NOW() - INTERVAL '4 days'),
    (2, 3, 599.00, '质量问题',     NOW() - INTERVAL '24 days')
ON CONFLICT DO NOTHING;

-- 竞品（爬虫域）
INSERT INTO crawler.competitor_products (id, platform, brand, product_name, category, url) VALUES
    (1, 'Amazon', 'CompetitorX', 'Stylish Necklace', 'Jewelry', 'https://amazon.com/dp/B001'),
    (2, 'TikTok Shop', 'TrendBrand', 'Hot Dress',      'Apparel', 'https://tiktok.com/p/B002')
ON CONFLICT (id) DO NOTHING;

INSERT INTO crawler.competitor_price (product_id, price, discount, crawl_time) VALUES
    (1, 149.00, 0.25, NOW()),
    (1, 139.00, 0.30, NOW() - INTERVAL '1 day'),
    (2, 549.00, 0.10, NOW())
ON CONFLICT DO NOTHING;

INSERT INTO crawler.product_reviews (product_id, rating, review_text, sentiment, created_at) VALUES
    (1, 4, 'Quality is good but shipping slow', 'neutral', NOW() - INTERVAL '2 days'),
    (3, 2, 'Color is different from picture', 'negative', NOW() - INTERVAL '1 day')
ON CONFLICT DO NOTHING;

-- 财务
INSERT INTO finance.expenses (type, amount, date) VALUES
    ('广告费', 5000.00, CURRENT_DATE - INTERVAL '5 days'),
    ('物流费', 1200.00, CURRENT_DATE - INTERVAL '5 days'),
    ('人工',   8000.00, CURRENT_DATE - INTERVAL '5 days')
ON CONFLICT DO NOTHING;

INSERT INTO finance.daily_profit (date, revenue, cost, profit) VALUES
    (CURRENT_DATE - INTERVAL '5 days', 1496.00, 8500.00, -7004.00)
ON CONFLICT (date) DO NOTHING;

-- Agent 任务（自描述，让 Agent 能"分析自己的历史"）
INSERT INTO ai.agent_tasks (id, session_id, user_query, task_type, status, created_at) VALUES
    (1, 'demo-1', '查询最近一个月内价格最高的商品信息', 'sql.query', 'success', NOW() - INTERVAL '1 day')
ON CONFLICT (id) DO NOTHING;

-- 修序列（确保后续插入不冲突）
SELECT setval(pg_get_serial_sequence('product.products', 'id'), COALESCE((SELECT MAX(id) FROM product.products), 1));
SELECT setval(pg_get_serial_sequence('"order".orders',    'id'), COALESCE((SELECT MAX(id) FROM "order".orders),    1));
SELECT setval(pg_get_serial_sequence('"order".order_items','id'), COALESCE((SELECT MAX(id) FROM "order".order_items),1));
SELECT setval(pg_get_serial_sequence('"order".refunds',   'id'), COALESCE((SELECT MAX(id) FROM "order".refunds),   1));
SELECT setval(pg_get_serial_sequence('product.categories','id'), COALESCE((SELECT MAX(id) FROM product.categories),1));
SELECT setval(pg_get_serial_sequence('inventory.warehouses','id'), COALESCE((SELECT MAX(id) FROM inventory.warehouses),1));
SELECT setval(pg_get_serial_sequence('customer.customers','id'), COALESCE((SELECT MAX(id) FROM customer.customers),1));
SELECT setval(pg_get_serial_sequence('crawler.competitor_products','id'), COALESCE((SELECT MAX(id) FROM crawler.competitor_products),1));

-- ═══ 9. 验证查询（可选，跑一下确认数据落地）═══
-- SELECT 'product.products' AS t, COUNT(*) FROM product.products
-- UNION ALL SELECT 'order.orders', COUNT(*) FROM "order".orders
-- UNION ALL SELECT 'inventory.inventory', COUNT(*) FROM inventory.inventory;
