"""
schema_config.py — 业务数据仓库 Schema 配置（按业务域拆分）

设计原则：
  - 不建"大而全"电商库，而是企业级**业务数据仓库 Demo**
  - PostgreSQL 一库多 schema（product/order/inventory/customer/crawler/finance/ai）
  - 每个 schema 对应一个业务域，便于权限隔离 + 物理逻辑分离
  - 表名用 schema-qualified 形式（`product.products`），避免跨域重名

支持的 Agent 场景（业务侧驱动 schema 规模）：
  - 销售日报 Agent    → order.orders + order.order_items
  - 库存预警 Agent    → inventory.inventory + inventory.warehouses
  - 商品分析 Agent    → product.products + product.categories + order.order_items
  - 竞品分析 Agent    → crawler.competitor_products + crawler.competitor_price + crawler.product_reviews
  - 财务分析 Agent    → finance.expenses + finance.daily_profit
  - NL2SQL 评测       → 10 张核心表规模匹配 DeepSeek/Qwen 上下文窗口

修改时机：新业务表接入 / 敏感列调整 / 行级安全策略变更
"""

from typing import Dict, Any

# =====================================================
# PostgreSQL Schema 配置
# =====================================================

SCHEMA_CONFIG: Dict[str, Any] = {

    # ── Schema 域白名单（防止 LLM 引用不存在的 schema）──
    "schemas": {
        "product",      # 商品域
        "order",        # 订单域
        "inventory",    # 库存域
        "customer",     # 用户域
        "crawler",      # 爬虫竞品域（⭐ Agent 区别于普通 SQL Agent 的核心数据）
        "finance",      # 财务域
        "ai",           # Agent 运行数据域
    },

    # ── 表定义 ─────────────────────────────────────────
    # 键格式：`<schema>.<table>` 全限定名（避免跨域重名）
    # 每张表：
    #   columns:     {列名: 描述}  — 喂给 LLM 帮助生成 SQL
    #   description: str           — router 选表时的业务说明
    "tables": {
        # ═══ 商品域 product ═══
        "product.products": {
            "columns": {
                "id":          "PK (SERIAL)",
                "sku":         "商品编码 (VARCHAR, 唯一)",
                "product_name":"商品名称 (VARCHAR)",
                "category_id": "分类ID (INTEGER, FK → product.categories.id)",
                "brand":       "品牌 (VARCHAR)",
                "cost_price":  "成本价 (NUMERIC)",
                "sale_price":  "售价 (NUMERIC)",
                "status":      "上架状态 (VARCHAR, active/inactive)",
                "created_at":  "创建时间 (TIMESTAMP)",
            },
            "description": "SPU 商品主表。Agent: 商品分析/销量/利润/滞销。",
        },
        "product.categories": {
            "columns": {
                "id":        "PK",
                "name":      "分类名称 (VARCHAR)",
                "parent_id": "父分类ID (INTEGER, 自引用 FK)",
            },
            "description": "商品类目树。Agent: 分类聚合分析。",
        },
        "product.product_tags": {
            "columns": {
                "id":         "PK",
                "product_id": "商品ID (FK → product.products.id)",
                "tag":        "标签 (爆款/新品/清仓/高利润)",
            },
            "description": "商品标签表。Agent: 多维筛选。",
        },

        # ═══ 订单域 order ═══
        "order.orders": {
            "columns": {
                "id":             "PK",
                "order_no":       "订单号 (VARCHAR, 唯一)",
                "customer_id":    "买家ID (FK → customer.customers.id)",
                "total_amount":   "订单总金额 (NUMERIC)",
                "status":         "订单状态 (pending/paid/shipped/completed/cancelled)",
                "payment_status": "支付状态 (VARCHAR)",
                "created_at":     "下单时间 (TIMESTAMP)",
            },
            "description": "订单主表。Agent: 销售/订单趋势/退款分析。",
        },
        "order.order_items": {
            "columns": {
                "id":         "PK",
                "order_id":   "订单ID (FK → order.orders.id)",
                "product_id": "商品ID (FK → product.products.id)",
                "quantity":   "数量 (INTEGER)",
                "price":      "单价 (NUMERIC)",
                "cost":       "成本 (NUMERIC, 用于利润分析)",
            },
            "description": "订单明细表 — 一个订单多个商品，一对多。",
        },
        "order.refunds": {
            "columns": {
                "id":            "PK",
                "order_id":      "订单ID (FK → order.orders.id)",
                "product_id":    "商品ID (FK → product.products.id)",
                "refund_amount": "退款金额 (NUMERIC)",
                "reason":        "退款原因 (VARCHAR)",
                "created_at":    "退款时间 (TIMESTAMP)",
            },
            "description": "退款表。Agent: 高退款率商品分析。",
        },

        # ═══ 库存域 inventory ═══
        "inventory.inventory": {
            "columns": {
                "id":             "PK",
                "product_id":     "商品ID (FK → product.products.id)",
                "warehouse_id":   "仓库ID (FK → inventory.warehouses.id)",
                "stock_quantity": "当前库存 (INTEGER)",
                "safety_stock":   "安全库存阈值 (INTEGER)",
                "updated_at":     "更新时间 (TIMESTAMP)",
            },
            "description": "多仓库存快照。Agent: 库存预警/采购建议。触发预警: stock_quantity < safety_stock。",
        },
        "inventory.warehouses": {
            "columns": {
                "id":       "PK",
                "name":     "仓库名称 (VARCHAR)",
                "location": "所在地 (VARCHAR)",
            },
            "description": "仓库字典。Agent: 多仓调度/配送分析。",
        },
        "inventory.purchase_orders": {
            "columns": {
                "id":         "PK",
                "supplier_id": "供应商ID",
                "product_id": "商品ID (FK → product.products.id)",
                "quantity":   "采购数量 (INTEGER)",
                "status":     "采购状态 (VARCHAR, pending/arrived/cancelled)",
                "created_at": "创建时间 (TIMESTAMP)",
            },
            "description": "采购订单。Agent: 采购节奏/补货建议。",
        },

        # ═══ 客户域 customer ═══
        "customer.customers": {
            "columns": {
                "id":           "PK",
                "name":         "客户姓名 (VARCHAR)",
                "gender":       "性别 (M/F)",
                "level":        "会员等级 (普通/银卡/金卡/VIP)",
                "register_time":"注册时间 (TIMESTAMP)",
            },
            "description": "客户主表。Agent: 用户画像/复购/流失分析。",
        },
        "customer.customer_behavior": {
            "columns": {
                "id":          "PK",
                "customer_id": "客户ID (FK → customer.customers.id)",
                "event_type":  "事件类型 (view/click/add_cart/favorite)",
                "product_id":  "商品ID (FK → product.products.id)",
                "created_at":  "事件时间 (TIMESTAMP)",
            },
            "description": "用户行为流水。Agent: 漏斗转化/意向商品。",
        },

        # ═══ 爬虫竞品域 crawler（⭐差异化数据）═══
        "crawler.competitor_products": {
            "columns": {
                "id":          "PK",
                "platform":    "电商平台 (Amazon/TikTok Shop/淘宝)",
                "brand":       "竞品品牌 (VARCHAR)",
                "product_name":"竞品商品名称 (VARCHAR)",
                "category":    "类目 (VARCHAR)",
                "url":         "商品链接 (VARCHAR)",
            },
            "description": "竞品商品字典。Agent: 竞品监控/市场份额分析。",
        },
        "crawler.competitor_price": {
            "columns": {
                "id":         "PK",
                "product_id": "竞品商品ID (FK → crawler.competitor_products.id)",
                "price":      "当前价格 (NUMERIC)",
                "discount":   "折扣 (NUMERIC, 0-1)",
                "crawl_time": "抓取时间 (TIMESTAMP)",
            },
            "description": "竞品价格时序。Agent: 价格走势/降价预警。",
        },
        "crawler.product_reviews": {
            "columns": {
                "id":          "PK",
                "product_id":  "商品ID (FK → product.products.id 或 crawler.competitor_products.id)",
                "rating":      "评分 (1-5 INTEGER)",
                "review_text": "评论正文 (TEXT)",
                "sentiment":   "情感 (positive/negative/neutral)",
                "created_at":  "评论时间 (TIMESTAMP)",
            },
            "description": "商品评论（含竞品）。Agent: 口碑/痛点分析。",
        },

        # ═══ 财务域 finance ═══
        "finance.expenses": {
            "columns": {
                "id":     "PK",
                "type":   "费用类型 (广告费/物流费/人工)",
                "amount": "金额 (NUMERIC)",
                "date":   "日期 (DATE)",
            },
            "description": "运营支出流水。Agent: 成本结构分析。",
        },
        "finance.daily_profit": {
            "columns": {
                "date":    "日期 (DATE, 主键)",
                "revenue": "营业收入 (NUMERIC)",
                "cost":    "总成本 (NUMERIC)",
                "profit":  "净利润 (NUMERIC)",
            },
            "description": "每日利润汇总。Agent: 利润趋势/盈亏分析。",
        },

        # ═══ Agent 运行域 ai ═══
        "ai.agent_tasks": {
            "columns": {
                "id":         "PK",
                "session_id": "会话ID (VARCHAR)",
                "user_query": "用户问题 (TEXT)",
                "task_type":  "任务类型 (VARCHAR)",
                "status":     "状态 (running/success/failed)",
                "created_at": "创建时间 (TIMESTAMP)",
            },
            "description": "Agent 任务记录。Agent: 自我统计/历史检索。",
        },
        "ai.agent_trace": {
            "columns": {
                "id":        "PK",
                "task_id":   "任务ID (FK → ai.agent_tasks.id)",
                "node":      "节点名 (planner/supervisor/...)",
                "input":     "节点输入 (JSONB)",
                "output":    "节点输出 (JSONB)",
                "duration":  "耗时 (NUMERIC, 秒)",
                "created_at":"时间 (TIMESTAMP)",
            },
            "description": "Agent Trace 持久化（对应 LangGraph Trace 页面）。Agent: 自我调试。",
        },
    },

    # ── 敏感列 ─────────────────────────────────────────
    # 注：客户姓名/手机/邮箱等隐私字段未来加入时进这里
    # 当前 Demo 规模暂未启用，保留路径
    "sensitive_columns": [
        # "customer.customers.phone",
        # "customer.customers.email",
    ],

    # ── 脱敏列 ─────────────────────────────────────────
    # 当前空 — schema 演示阶段不引入脱敏；接生产时按业务域填充
    "masked_columns": {},

    # ── 行级安全 ───────────────────────────────────────
    # Demo 阶段所有表对运营账号开放；按需收紧
    "row_security": {
        # 示例：客户数据按账号隔离
        # "order.orders": {
        #     "column": "customer_id",
        #     "param":  "current_customer_id",
        # },
    },

    # ── 查询限制 ────────────────────────────────────────
    "max_limit": 100,
    "query_timeout": 5.0,

    # ── 禁用函数黑名单（同上一版） ──
    "banned_functions": [
        "sleep", "pg_sleep", "benchmark",
        "lo_import", "lo_export",
        "pg_read_file", "pg_read_binary_file",
        "pg_write_file", "pg_write_binary_file",
        "dblink", "dblink_exec",
    ],
}
