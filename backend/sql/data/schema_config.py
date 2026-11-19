"""
schema_config.py — 业务数据仓库 Schema 配置（按业务域拆分）

设计原则：
  - 不建"大而全"电商库，而是企业级**业务数据仓库 Demo**
  - PostgreSQL 一库多 schema（含 retail 零售 ERP 试点域）
  - 每个 schema 对应一个业务域，便于权限隔离 + 物理逻辑分离
  - 表名用 schema-qualified 形式（`product.products`），避免跨域重名

支持的 Agent 场景（业务侧驱动 schema 规模）：
  - 销售日报 Agent    → order.orders + order.order_items
  - 库存预警 Agent    → inventory.inventory + inventory.warehouses
  - 商品分析 Agent    → product.products + product.categories + order.order_items
  - 财务分析 Agent    → finance.expenses + finance.daily_profit
  - NL2SQL 评测       → 核心表规模可控；按业务域选表，减少无关上下文

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
        "finance",      # 财务域
        "ai",           # Agent 运行数据域
        "retail",       # 零售 ERP 试点域
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
            "description": "订单明细表 — 一个订单多个商品，一对多；明细销售额按数量×单价计算。",
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
            "description": "退款记录表。商品级退款率按退款金额/商品销售额计算；两侧分别聚合后再关联。",
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
    # 敏感列：对 LLM 完全隐藏（不出现在表结构描述中），且 SELECT 引用直接拒绝。
    # 注意：当前 customer.customers 表无 phone/email 列，故为空；
    # 接入真实用户画像数据（手机/邮箱/身份证）时加入这里。
    "sensitive_columns": [
        # "customer.customers.phone",
        # "customer.customers.email",
    ],

    # ── 脱敏列（P1-11 已启用）─────────────────────────
    # 值格式: {列名: (保留前缀长度, 保留后缀长度)}
    # 查询仍可返回该列，但结果打码：客户姓名 → "张***"
    "masked_columns": {
        "customer.customers.name": (1, 0),
    },

    # ── 表数据域策略（2026-09-23 SQL 收口 STOP B，决策 D1-D4 定版）──────
    # 消费方：backend/sql/policy.py::SQLPolicyGuard（三维 scope 注入引擎）。
    # data_domain：
    #   shared    — 平台运营数据，无个人/部门归属 → 持 sql.read 者皆可读
    #               （department/self 用户同样放行，不虚构归属条件）
    #   personal  — 存在真实个人归属的表：all 全量；self 按 self_column
    #               参数化注入；department 无部门列可表达 → fail-closed 拒绝
    #   internal  — 财务/Agent 运行内部数据（含他人会话内容/商业成本）
    #               → 仅 data_scope=all 可读（决策 D4）
    # tenant_column：现有 18 张旧表无租户列；retail 试点表真实带 tenant_id，
    #   并由 Guard 参数化注入（all 也不例外，tenant 永远优先）。
    #   retail 暂归 internal，未实现门店 ACL 前仅全域权限账号可查询。
    # department_column：当前无表声明；声明后 department scope 自动注入
    #   （principal.department 为空 → fail-closed，决策 D2）。
    # self_column：与当前用户关联的列。order_items/refunds/customer_behavior
    #   个人归属需跨表 join 才能表达 → 本阶段不登记 self_column，
    #   self scope 用户查询直接拒绝（不做自动 JOIN 改写，安全优先）。
    "table_policies": {
        # ═══ shared（11 表）═══
        "product.products":               {"data_domain": "shared"},
        "product.categories":             {"data_domain": "shared"},
        "product.product_tags":           {"data_domain": "shared"},
        "inventory.inventory":            {"data_domain": "shared"},
        "inventory.warehouses":           {"data_domain": "shared"},
        "inventory.purchase_orders":      {"data_domain": "shared"},
        "customer.customers":             {"data_domain": "shared"},
        "customer.customer_behavior":     {"data_domain": "shared"},
        # ═══ personal（3 表）═══
        "order.orders": {
            "data_domain": "personal",
            "self_column": "customer_id",   # 与旧 row_security 同列；STOP C 收口后 row_security 段退役
        },
        "order.order_items":              {"data_domain": "personal"},
        "order.refunds":                  {"data_domain": "personal"},
        # ═══ internal（4 表，仅 all）═══
        "finance.expenses":               {"data_domain": "internal"},
        "finance.daily_profit":           {"data_domain": "internal"},
        "ai.agent_tasks":                 {"data_domain": "internal"},
        "ai.agent_trace":                 {"data_domain": "internal"},
    },

    # ── 行级安全（P1-11 已配置，默认由 SQL_ROW_SECURITY_ENABLED 控制）──
    # ⚠️ legacy（STOP C 收口对象）：仅服务未接 SQLPolicyContext 的存量调用
    # （评测 runner / 脚本）。走 SQLPolicyGuard 的新链路按 table_policies
    # 注入 self 条件，不再读本段——避免 order.orders.customer_id 双重注入。
    # 生产部署设置 SQL_ROW_SECURITY_ENABLED=true 启用；
    # 启用后查询 order.orders 将强制注入 customer_id = current_user_id
    # （参数化，值来自服务端推导的可信用户头 X-User-Id，客户端不可伪造），
    # 缺少用户上下文时按严格模式直接拒绝（RowSecurityError）。
    "row_security": {
        "order.orders": {
            "column": "customer_id",
            "param":  "current_user_id",
        },
    },

    # ── 查询限制 ────────────────────────────────────────
    "max_limit": 100,
    "query_timeout": 5.0,

    # ── 禁用函数黑名单 ──
    # 2026-10-03 只读渗透（材料#14）确认 7 个非查询系统函数穿透旧名单，
    # 全部补入（第⑥层 agent_readonly 只兜得住其中一部分，第④层纵深防御）：
    #   - set_config 可篡改会话级行级安全上下文，行级隔离开启后 = 提权路径
    #   - setval/nextval 篡改序列；pg_terminate_backend/pg_cancel_backend 干预连接
    #   - lo_get 读大对象绕过敏感列防线
    "banned_functions": [
        "sleep", "pg_sleep", "benchmark",
        "lo_import", "lo_export", "lo_get",
        "pg_read_file", "pg_read_binary_file",
        "pg_write_file", "pg_write_binary_file",
        "dblink", "dblink_exec",
        "setval", "nextval",
        "set_config", "current_setting",
        "pg_terminate_backend", "pg_cancel_backend",
        # 信息探测类（非 pg_ 前缀，进显式黑名单）：权限/事务信息泄露
        "has_table_privilege", "has_column_privilege", "txid_current",
    ],
    # ── pg_* 系统函数族放行白名单（G-17 fail-closed，默认空）──
    # 未列入 banned_functions 的 pg_* 函数一律拒绝；确有业务需要的系统函数
    # 在此显式批准后放行，不给「黑名单外 = 安全」的默认。
    "allowed_functions": [],
}


def _retail_table(description: str, **columns: str) -> dict[str, Any]:
    """声明零售 ERP 表结构，供 SQL 白名单、提示和列校验共用。"""
    return {"columns": columns, "description": description}


_RETAIL_TABLES = {
    "retail.stores": _retail_table(
        "门店主数据；当前只有 default 租户的虚构试点门店。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="门店编码，租户内唯一", store_name="门店名称",
        timezone="门店时区", currency_code="门店币种",
        status="门店状态：active/inactive", created_at="创建时间",
    ),
    "retail.warehouses": _retail_table(
        "门店下属仓库、后仓和卖场区域；试点用仓库间调拨模拟补货。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        warehouse_id="仓库编码", store_id="所属门店编码",
        warehouse_name="仓库名称",
        warehouse_type="类型：distribution_center/store/backroom",
        status="仓库状态",
    ),
    "retail.products": _retail_table(
        "SKU 商品主数据，含条码、售价、成本和补货阈值。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        sku="库存单位 SKU，租户内唯一", barcode="商品条码，租户内唯一",
        product_name="商品名称", category="商品类别", brand="品牌",
        unit="计量单位", sale_price="标价（USD）", unit_cost="单位成本（USD）",
        reorder_point="补货点", batch_tracked="是否按批次追踪",
        status="商品状态：active/inactive",
    ),
    "retail.suppliers": _retail_table(
        "供应商主数据及国家、预计交期。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        supplier_id="供应商编码", supplier_name="供应商名称",
        country_code="国家代码", lead_time_days="预计交货天数",
        status="供应商状态",
    ),
    "retail.purchase_requests": _retail_table(
        "门店采购申请及审批状态；converted 表示已转采购订单。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="申请所属门店", request_id="采购申请单号",
        requested_by="申请人（合成数据）",
        status="draft/submitted/approved/rejected/converted",
        created_at="申请时间", decided_at="审批时间", note="申请备注",
    ),
    "retail.purchase_request_lines": _retail_table(
        "采购申请商品行及申请、批准数量。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="申请所属门店", request_id="采购申请单号", line_no="行号",
        sku="商品 SKU", requested_qty="申请数量",
        approved_qty="批准数量；未审批时为空",
    ),
    "retail.purchase_orders": _retail_table(
        "采购订单主表；取消订单不计入有效采购金额。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="采购所属门店", po_id="采购订单号", supplier_id="供应商编码",
        warehouse_id="计划收货仓库", source_request_id="来源采购申请号",
        status=(
            "draft/pending_approval/approved/sent/"
            "partially_received/received/cancelled"
        ),
        ordered_at="下单时间", expected_at="预计到货时间", currency_code="订单币种",
    ),
    "retail.purchase_order_lines": _retail_table(
        "采购订单商品行；金额按 ordered_qty × unit_cost 计算。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="采购所属门店", po_id="采购订单号", line_no="行号",
        sku="商品 SKU", ordered_qty="订购数量", unit_cost="订单单位成本（USD）",
    ),
    "retail.receipts": _retail_table(
        "到货/收货单；只将 posted/partial 的验收数量计为有效收货。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="收货所属门店", receipt_id="收货单号", po_id="采购订单号",
        warehouse_id="实际收货仓库", received_at="收货时间",
        status="posted/partial/void", reference_no="供应商送货单号",
    ),
    "retail.receipt_lines": _retail_table(
        "收货明细；received_qty = accepted_qty + rejected_qty，拒收不入库存。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="收货所属门店", receipt_id="收货单号", line_no="行号",
        po_id="采购订单号", po_line_no="采购订单行号", sku="商品 SKU",
        received_qty="实收数量（含拒收）", accepted_qty="验收合格数量",
        rejected_qty="拒收数量",
    ),
    "retail.inventory_balances": _retail_table(
        "仓库库存快照；缺货按 on_hand_qty < safety_stock，等于阈值不算缺货。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="库存所属门店", warehouse_id="仓库编码", sku="商品 SKU",
        on_hand_qty="当前实物库存", reserved_qty="预留数量",
        safety_stock="安全库存线", updated_at="快照更新时间",
    ),
    "retail.inventory_movements": _retail_table(
        "库存流水；quantity_delta 有正负，opening_balance 为期初合成流水。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="库存所属门店", movement_id="库存流水号", warehouse_id="仓库编码",
        sku="商品 SKU",
        movement_type=(
            "receipt/sale/transfer_in/transfer_out/stock_count/return"
        ),
        quantity_delta="库存变化量：入库为正、出库为负",
        source_type="来源类型；opening_balance 为期初库存",
        source_id="来源单据号", occurred_at="流水发生时间",
    ),
    "retail.product_batches": _retail_table(
        "批次库存与到期日期；临期查询需过滤 quantity_on_hand > 0。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="批次所属门店", batch_id="批次号", warehouse_id="所在仓库",
        sku="商品 SKU", received_at="入库日期", expiry_date="到期日期",
        quantity_on_hand="该批次现存数量",
        status="available/quarantined/expired",
    ),
    "retail.stock_transfers": _retail_table(
        "试点店内仓库调拨单；当前数据只覆盖后仓与卖场，不代表跨店权限。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="调拨所属门店", transfer_id="调拨单号",
        from_warehouse_id="调出仓库", to_warehouse_id="调入仓库",
        status="draft/approved/in_transit/received/cancelled",
        created_at="创建时间", shipped_at="发出时间", received_at="签收时间",
    ),
    "retail.stock_transfer_lines": _retail_table(
        "调拨商品行；完成差异按 sent_qty 与 received_qty 比较。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="调拨所属门店", transfer_id="调拨单号", line_no="行号",
        sku="商品 SKU", sent_qty="发出数量", received_qty="实收数量；未签收时为空",
    ),
    "retail.stock_counts": _retail_table(
        "门店盘点单；只有 posted 状态的差异计入已过账盘点。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="盘点所属门店", count_id="盘点单号", warehouse_id="盘点仓库",
        status="draft/counting/submitted/approved/posted/rejected",
        planned_at="计划时间", counted_at="实盘完成时间", posted_at="差异过账时间",
    ),
    "retail.stock_count_lines": _retail_table(
        "盘点明细；差异 = counted_qty - system_qty，金额差异按 unit_cost 计算。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="盘点所属门店", count_id="盘点单号", line_no="行号",
        sku="商品 SKU", system_qty="盘点前系统数量", counted_qty="实盘数量",
        unit_cost="盘点成本价（USD）",
    ),
    "retail.pos_sales": _retail_table(
        "POS 销售单；净销售排除 cancelled，含税总额减已退款金额。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="销售所属门店", sale_id="销售单号", receipt_no="POS 小票号",
        sold_at="销售时间", status="completed/cancelled", currency_code="销售币种",
        subtotal="商品净销售额，不含税", tax_amount="税额",
        total_amount="含税成交总额；subtotal + tax_amount",
    ),
    "retail.pos_sale_lines": _retail_table(
        "POS 销售明细；line_net_amount = quantity × unit_price - discount_amount，不含税。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="销售所属门店", sale_id="销售单号", line_no="行号",
        sku="商品 SKU", quantity="销售数量", unit_price="成交单价（USD）",
        unit_cost="销售时成本（USD）", discount_amount="行折扣金额",
        tax_amount="该销售行税额", line_net_amount="该行不含税净额",
    ),
    "retail.pos_returns": _retail_table(
        "POS 退货退款单；只将 refunded 状态视为已退款，requested 不计退款。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="退货所属门店", return_id="退货单号", sale_id="原销售单号",
        status="requested/approved/refunded/rejected", requested_at="申请时间",
        refunded_at="退款完成时间", reason="退货原因",
    ),
    "retail.pos_return_lines": _retail_table(
        "POS 退货商品行；refund_amount 是实际退款金额，含本样例退款税额。",
        tenant_id="租户 ID，由服务端 SQL 策略自动过滤",
        store_id="退货所属门店", return_id="退货单号", line_no="行号",
        sale_id="原销售单号", sale_line_no="原销售行号", sku="商品 SKU",
        return_qty="退货数量", refund_amount="实际退款金额（USD）",
    ),
}

SCHEMA_CONFIG["schemas"].add("retail")
SCHEMA_CONFIG["tables"].update(_RETAIL_TABLES)
SCHEMA_CONFIG["table_policies"].update({
    table_name: {"data_domain": "internal", "tenant_column": "tenant_id"}
    for table_name in _RETAIL_TABLES
})
