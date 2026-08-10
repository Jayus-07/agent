# DATABASE — 数据库设计

> PostgreSQL 双库 + 14 个 SQLITE 散落 + Migration 治理。
> 配套阅读：[PRD.md](PRD.md) / [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 1. 双库架构

```
┌────────────────────────────────────────────────────────┐
│                  PostgreSQL 18 (本地)                   │
│  安装路径：D:/Program Files/PostgreSQL/18              │
│  认证：scram-sha-256（[~/.claude/CLAUDE.md 全局配置]）  │
└──────────────┬──────────────────────────┬───────────────┘
               │                          │
    ┌──────────▼──────────┐    ┌───────────▼─────────┐
    │  agent_business     │    │   agent_memory      │
    │  (业务仓库)          │    │   (元数据仓库)       │
    │                     │    │                     │
    │  7 schema           │    │   chat_sessions      │
    │  ~18 表             │    │   chat_messages      │
    │  业务核心            │    │   memory_records     │
    │                     │    │   (含 pgvector)      │
    └─────────────────────┘    └─────────────────────┘
```

启动时 `data/` 目录里还有 14 个 **SQLite 单文件**（详见 §5），承担 Trace / 文档 / 告警 / 报告 / 工作流等次要数据 —— **必须治理**。

---

## 2. 7 schema × 18 表（agent_business）

### 2.1 总览

| # | Schema | 表数 | 定位 |
|---|---|---|---|
| 1 | `product` | 3 | 商品域 |
| 2 | `order`（带引号） | 3 | 订单域 |
| 3 | `inventory` | 3 | 库存域 |
| 4 | `customer` | 2 | 客户域 |
| 5 | `crawler` | 3 | 爬虫竞品域 |
| 6 | `finance` | 2 | 财务域 |
| 7 | `ai` | 2 | Agent 运行数据 |
| **合计** | — | **18** | — |

CLAUDE.md 摘要里说的"19 表"沿用旧口径，**实际 001 migration 建 18 表**（3+3+3+2+3+2+2）。

---

### 2.2 product 域（3 表）

#### `product.categories`

```sql
CREATE TABLE product.categories (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    parent_id   INTEGER REFERENCES product.categories(id)   -- 自引用
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| id | SERIAL PK | |
| name | VARCHAR(128) NOT NULL | 类目名 |
| parent_id | INTEGER FK | 自引用（如 服装→外套） |

#### `product.products`（核心表）

```sql
CREATE TABLE product.products (
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
```

| 字段 | 类型 | 说明 |
|---|---|---|
| id | SERIAL PK | |
| sku | VARCHAR(64) UNIQUE | 唯一 SKU |
| product_name | VARCHAR(256) NOT NULL | 商品名 |
| category_id | INTEGER FK | → categories.id |
| brand | VARCHAR(128) | 品牌 |
| cost_price | NUMERIC(10,2) | 成本价 |
| sale_price | NUMERIC(10,2) | 售价 |
| status | VARCHAR(32) | active / inactive（CHECK 约束） |
| created_at | TIMESTAMP | 创建时间 |

**索引**：`idx_products_category` (category_id) / `idx_products_created` (created_at)

#### `product.product_tags`

```sql
CREATE TABLE product.product_tags (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER REFERENCES product.products(id) ON DELETE CASCADE,
    tag         VARCHAR(64)
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| id | SERIAL PK | |
| product_id | INTEGER FK | → products.id（级联删除） |
| tag | VARCHAR(64) | 标签（爆款/新品/高利润/清仓） |

---

### 2.3 order 域（3 表，含引号 schema）

```sql
CREATE SCHEMA "order";   -- 避免与 ORDER 关键字冲突
```

#### `order.orders`

```sql
CREATE TABLE "order".orders (
    id              SERIAL PRIMARY KEY,
    order_no        VARCHAR(64) UNIQUE NOT NULL,
    customer_id     INTEGER,
    total_amount    NUMERIC(12,2),
    status          VARCHAR(32),
    payment_status  VARCHAR(32),
    created_at      TIMESTAMP DEFAULT NOW()
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| order_no | VARCHAR(64) UNIQUE | 订单号 |
| customer_id | INTEGER FK | → customer.customers.id（005 加 FK） |
| status | VARCHAR(32) | pending / paid / shipped / completed / cancelled（CHECK） |
| payment_status | VARCHAR(32) | 支付状态 |
| total_amount | NUMERIC(12,2) | 订单总额 |

**索引**：`idx_orders_created` / `idx_orders_status` / `idx_orders_customer` (005)

#### `order.order_items`

```sql
CREATE TABLE "order".order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER REFERENCES "order".orders(id) ON DELETE CASCADE,
    product_id  INTEGER,
    quantity    INTEGER,
    price       NUMERIC(10,2),
    cost        NUMERIC(10,2)               -- 利润分析用
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| order_id | INTEGER FK | → orders.id（级联删除） |
| product_id | INTEGER FK | → products.id（005 加 FK） |
| cost | NUMERIC(10,2) | 进货成本（用于利润分析） |

**索引**：`idx_oi_product` / `idx_oi_order` (005)

#### `order.refunds`

```sql
CREATE TABLE "order".refunds (
    id            SERIAL PRIMARY KEY,
    order_id      INTEGER REFERENCES "order".orders(id),
    product_id    INTEGER,
    refund_amount NUMERIC(10,2),
    reason        VARCHAR(256),
    created_at    TIMESTAMP DEFAULT NOW()
);
```

**索引**：`idx_refunds_product` / `idx_refunds_order` (005)

---

### 2.4 inventory 域（3 表）

#### `inventory.warehouses`

```sql
CREATE TABLE inventory.warehouses (
    id        SERIAL PRIMARY KEY,
    name      VARCHAR(128),
    location  VARCHAR(128)
);
```

#### `inventory.inventory`（核心表）

```sql
CREATE TABLE inventory.inventory (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER,
    warehouse_id    INTEGER REFERENCES inventory.warehouses(id),
    stock_quantity  INTEGER DEFAULT 0,
    safety_stock    INTEGER DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT NOW()
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| stock_quantity | INTEGER DEFAULT 0 | 当前库存（CHECK ≥ 0） |
| safety_stock | INTEGER DEFAULT 0 | 安全库存（CHECK ≥ 0） |
| updated_at | TIMESTAMP | |

**索引**：`idx_inv_product` / `idx_inv_warehouse` / `idx_inv_product_warehouse` (005)

#### `inventory.purchase_orders`

```sql
CREATE TABLE inventory.purchase_orders (
    id          SERIAL PRIMARY KEY,
    supplier_id INTEGER,
    product_id  INTEGER,
    quantity    INTEGER,
    status      VARCHAR(32),
    created_at  TIMESTAMP DEFAULT NOW()
);
```

**索引**：`idx_po_product` / `idx_po_status` (005)

---

### 2.5 customer 域（2 表）

#### `customer.customers`

```sql
CREATE TABLE customer.customers (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(128),
    gender        VARCHAR(8),
    level         VARCHAR(32),              -- 普通/银卡/金卡/VIP
    register_time TIMESTAMP DEFAULT NOW()
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| gender | VARCHAR(8) | M / F（CHECK 005） |
| level | VARCHAR(32) | 普通 / 银卡 / 金卡 / VIP |

#### `customer.customer_behavior`

```sql
CREATE TABLE customer.customer_behavior (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customer.customers(id),
    event_type  VARCHAR(32),                -- view/click/add_cart/favorite
    product_id  INTEGER,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| event_type | VARCHAR(32) | view / click / add_cart / favorite（CHECK 005） |

**索引**：`idx_behavior_customer` / `idx_behavior_product` / `idx_behavior_time` (005)

---

### 2.6 crawler 域（3 表）

#### `crawler.competitor_products`

```sql
CREATE TABLE crawler.competitor_products (
    id            SERIAL PRIMARY KEY,
    platform      VARCHAR(64),              -- Amazon/TikTok Shop/淘宝
    brand         VARCHAR(128),
    product_name  VARCHAR(256),
    category      VARCHAR(128),
    url           VARCHAR(512)
);
```

#### `crawler.competitor_price`

```sql
CREATE TABLE crawler.competitor_price (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER REFERENCES crawler.competitor_products(id) ON DELETE CASCADE,
    price       NUMERIC(10,2),
    discount    NUMERIC(4,2),
    crawl_time  TIMESTAMP DEFAULT NOW()
);
```

**索引**：`idx_comp_price_time` (crawl_time)

#### `crawler.product_reviews`

```sql
CREATE TABLE crawler.product_reviews (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER,                    -- 可指向 product.products.id 或 crawler.competitor_products.id
    rating      INTEGER,
    review_text TEXT,
    sentiment   VARCHAR(16),
    created_at  TIMESTAMP DEFAULT NOW()
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| rating | INTEGER | 1-5（CHECK 005） |
| sentiment | VARCHAR(16) | positive / negative / neutral（CHECK 005） |

**索引**：`idx_reviews_product` / `idx_reviews_sentiment` (005)

---

### 2.7 finance 域（2 表）

#### `finance.expenses`

```sql
CREATE TABLE finance.expenses (
    id     SERIAL PRIMARY KEY,
    type   VARCHAR(64),                     -- 广告费/物流费/人工
    amount NUMERIC(12,2),
    date   DATE DEFAULT CURRENT_DATE
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| type | VARCHAR(64) | 广告费 / 物流费 / 人工 |
| amount | NUMERIC(12,2) | > 0（CHECK 005） |

#### `finance.daily_profit`

```sql
CREATE TABLE finance.daily_profit (
    date    DATE PRIMARY KEY,
    revenue NUMERIC(12,2),
    cost    NUMERIC(12,2),
    profit  NUMERIC(12,2)
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| date | DATE PK | 业务日 |

---

### 2.8 ai 域（2 表）

#### `ai.agent_tasks`

```sql
CREATE TABLE ai.agent_tasks (
    id         SERIAL PRIMARY KEY,
    session_id VARCHAR(128),
    user_query TEXT,
    task_type  VARCHAR(64),
    status     VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW()
);
```

**索引**：`idx_tasks_session` / `idx_tasks_status` (005)

#### `ai.agent_trace`

```sql
CREATE TABLE ai.agent_trace (
    id         SERIAL PRIMARY KEY,
    task_id    INTEGER REFERENCES ai.agent_tasks(id) ON DELETE CASCADE,
    node       VARCHAR(64),
    input      JSONB,
    output     JSONB,
    duration   NUMERIC(8,3),
    created_at TIMESTAMP DEFAULT NOW()
);
```

| 字段 | 类型 | 说明 |
|---|---|---|
| input / output | JSONB | 节点输入输出 |
| duration | NUMERIC(8,3) | 毫秒 |

**索引**：`idx_trace_task` (task_id)

---

### 2.9 关系图（核心）

```
product.categories ─┐
                    │
        ┌───────────▼────────────┐
        │   product.products     │ ◄────┐
        └───────────┬────────────┘      │ FK
                    │                   │
        ┌───────────▼─────────┐  ┌──────┴──────────┐
        │ product.product_tags │  │ inventory.inv   │
        └─────────────────────┘  └─────────────────┘
                                    │
        ┌───────────┐              │
        │ customer. │──────────────┘
        │ customers │
        └─────┬─────┘
              │
              ▼
        ┌───────────┐         ┌──────────────┐
        │ order.    │────────►│ order.       │
        │ orders    │ 1:N     │ order_items  │
        └─────┬─────┘         └──────────────┘
              │
              ▼
        ┌───────────┐
        │ order.    │
        │ refunds   │
        └───────────┘
```

---

## 3. agent_memory 库（元数据）

### 3.1 表结构

| 表 | 字段 | 用途 |
|---|---|---|
| `chat_sessions` | id / user_id / title / created_at / updated_at | 会话元数据 |
| `chat_messages` | session_id / role / content / created_at | 消息持久化 |
| `memory_records` | id / user_id / type / content / embedding (BYTEA) / importance_score / confidence_score / supersede / superseded_by | 长期记忆 + pgvector |
| `ai.*` | 镜像 | AI schema 镜像 |

### 3.2 pgvector 索引

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE INDEX memory_embedding_idx ON memory_records USING ivfflat (embedding vector_cosine_ops);
```

### 3.3 关键说明

- ⚠️ `chat_sessions.user_id VARCHAR NOT NULL`（与 `auth-decision.md` 描述不一致，后者说"设为可空"）
- ⚠️ 跨用户 / 跨租户的 memory 隔离**未实现**（仅靠 user_id 字符串，建议作为 Phase 3 鉴权接入首要价值）

---

## 4. 连接池与只读账号

### 4.1 连接池（[backend/sql/executor.py](../backend/sql/executor.py)）

```python
ThreadedConnectionPool(
    minconn=2,
    maxconn=10,
    host=...,
    connect_timeout=5,
    keepalives_idle=30,
    keepalives_interval=10,
    keepalives_count=3,
    application_name="agent_sql_executor",
)
```

**双重检查锁单例**（PgConnectionPool 线程安全）。

### 4.2 只读账号四层防线

| 层 | 配置 | 作用 |
|---|---|---|
| **1. DB 角色** | `agent_readonly` NOSUPERUSER | 仅 GRANT SELECT + ALTER DEFAULT PRIVILEGES |
| **2. 角色级** | `statement_timeout=30s` / `idle_in_transaction_session_timeout=60s` / `CONNECTION LIMIT 20` | 超时 + 空闲清理 + 连接上限 |
| **3. 连接级** | `conn.set_session(readonly=True)` | Python 侧额外保护 |
| **4. 事务级** | `BEGIN + SET TRANSACTION READ ONLY + SET LOCAL statement_timeout` | 事务级只读 + TIMEOUT |

### 4.3 004 migration 创建只读账号

```sql
CREATE ROLE agent_readonly WITH LOGIN PASSWORD 'agent_readonly_dev'
    NOSUPERUSER NOCREATEDB NOCREATEROLE;

GRANT CONNECT ON DATABASE agent_business TO agent_readonly;
GRANT USAGE ON SCHEMA product, "order", inventory, customer, crawler, finance, ai TO agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA <各 schema> TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA <各 schema> GRANT SELECT ON TABLES TO agent_readonly;
```

⚠️ **默认密码硬编码**（`agent_readonly_dev`），生产环境必须通过 env `PG_READONLY_PASSWORD` 注入。

### 4.4 行级安全（[backend/sql/row_security.py](../backend/sql/row_security.py)）

- sqlglot 重写 AST，为受保护表注入 `AND table.col = %(param)s` 参数化占位符
- 引用受保护表却缺参数 → 抛 `RowSecurityError`
- **当前缺陷**：`current_user_id` 来自 HTTP 请求体（非可信鉴权源）→ 攻击者可越权
- **修复路径**：接上 JWT 后立刻生效（机制已就绪）

---

## 5. 14 个 SQLite 散落（架构债务）

`data/` 目录下的 SQLite 单文件：

| 数据库 | 模块 | 用途 | 前端页面 |
|---|---|---|---|
| `trace_store.db` | `observability/trace_store.py` | Trace 持久化（< 5000 行） | `/observability/traces` |
| `doc_registry.db` | `rag/indexing/doc_registry.py` | 文档注册表 | `/knowledge/documents` |
| `doc_operation_log.db` | `rag/indexing/operation_log.py` | 文档操作日志 | `/knowledge/operations` |
| `chunk_store.db` | `rag/indexing/chunk_store.py` | Chunk 存储 | — |
| `keyword_rules.db` | `rag/preprocessing/keyword_store.py` | 关键词规则 | `/knowledge/keywords` |
| `inventory_alerts.db` | `rag/../inventory_alerts.py` | 告警中心 | `/alerts` |
| `daily_reports.db` | `business_report/` | 报告中心 | `/reports` |
| `workflow_runs.db` | `orchestration/workflow/persistence.py` | 工作流运行 | `/schedules` |
| `chat_history.db` | — | 历史对话 | — |
| `demo_sales.db` | — | 演示数据 | — |
| `test_x.db` | — | 测试 | — |
| `trace_cleanup_audit.db` | — | 清理审计 | — |
| `chroma/` | — | 向量库（文件） | — |
| `bm25/` | — | BM25 索引（文件） | — |

**架构债务**：

- ❌ 不能水平扩展
- ❌ 无并发写保护
- ❌ 无备份策略
- ❌ 无跨实例共享
- ✅ 但：单机性能足够 + 与业务 PG 解耦

**P0 治理目标**（[ROADMAP.md §1](ROADMAP.md)）：核心数据（Trace / 告警 / 报告 / Workflow）迁 PostgreSQL。

---

## 6. Migration 治理

### 6.1 当前 5 个 migration

| 文件 | 内容 | 行数 |
|---|---|---|
| `001_business_warehouse.sql` | 7 schema + 18 表 + seed | 324 |
| `002_agent_memory_schema.sql` | memory 库 schema dump | 75 |
| `003_agent_business_schema.sql` | 业务库 schema dump | 190 |
| `003_agent_memory_seed.sql` | memory 库 seed | 122 ⚠️ 编号重复 |
| `004_readonly_role.sql` | 只读角色 + 权限 | 71 |
| `005_schema_hardening.sql` | 补 FK + CHECK + 索引 | 208 |

**问题**：

- ❌ 003 编号重复（两个文件），破坏有序性
- ❌ 裸 SQL + 手工 `psql -f` 执行
- ❌ 无版本表（不知道库当前跑到哪个 migration）
- ❌ 无回滚脚本（出错只能手动反写）
- ❌ 不在 CI 流程内（schema 漂移无人察觉）

### 6.2 P1 治理目标

- 引入 **Alembic**（Python ORM migration 工具）
- 重新打 baseline
- CI 校验：每个 PR 检查迁移到目标版本是否成功
- 强制回滚脚本

详细：[ROADMAP.md §1 P1](ROADMAP.md)

---

## 7. 关键文件索引

| 文件 | 职责 |
|---|---|
| `backend/sql/migrations/001_business_warehouse.sql` | 业务库 DDL + seed |
| `backend/sql/migrations/002_agent_memory_schema.sql` | memory 库 schema |
| `backend/sql/migrations/003_agent_business_schema.sql` | 业务库 schema dump |
| `backend/sql/migrations/003_agent_memory_seed.sql` | memory 库 seed |
| `backend/sql/migrations/004_readonly_role.sql` | 只读账号 |
| `backend/sql/migrations/005_schema_hardening.sql` | 补 FK + CHECK + 索引 |
| `backend/sql/executor.py` | ThreadedConnectionPool + 4 层防线 |
| `backend/sql/row_security.py` | 行级安全（sqlglot） |
| `backend/sql/schemas_config.py` | schema 白名单 + 敏感列 |
| `backend/sql/schema_loader.py` | 动态 schema 加载 |
| `backend/memory/migrations/001_init.sql` | memory 库 DDL（pgvector） |
| `backend/config/database.py` | DB 连接配置 |

---

## 验证

最后验证：2026-08-10 · 与代码一致（18 业务表 + 5 migration + 4 层只读防线 + 14 SQLite 散落）。
