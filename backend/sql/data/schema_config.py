"""
schema_config.py — 数据库 schema 配置、白名单、敏感列、行级安全策略

这是整个 SQL Agent 安全体系的"唯一真相来源"（single source of truth）。
所有安全策略集中在此文件，由 schema_loader.py 加载。
修改安全策略只需改这一个文件，不需要改任何业务逻辑代码。

设计原则：安全规则由硬编码配置决定，不依赖 LLM 的 prompt 指令或"自觉"。
"""

from typing import Dict, Any

# =====================================================
# 数据库 Schema 定义 (PostgreSQL)
# =====================================================
# 以下配置被 schema_loader.py 在启动时一次性加载，预计算为快速查找结构：
#   - allowed_tables（表名集合）
#   - sensitive_columns（table.column 集合）
#   - masked_columns（{table.column: (prefix_len, suffix_len)} 字典）
#   - row_security（{table: {column, param}} 字典）
#   - banned_functions（大写函数名集合）
# 这些预计算结构在 validate、execute 等热路径上直接使用，O(1) 查找。

SCHEMA_CONFIG: Dict[str, Any] = {

    # ── 表定义 ─────────────────────────────────────────
    # 每个表包含：
    #   columns:     {列名: 描述}  — 描述会喂给 LLM 帮助它生成正确的 SQL
    #   description: str           — 表的业务说明，用于 router.py 选表时的提示词
    #
    # 注意：敏感列（如 email）虽然在 columns 中定义，但 get_table_info()
    # 生成 LLM 提示词时会自动排除这些列，从源头防止 LLM 知道它们的存在。
    #
    # ═══ 跨境电商核心表（对应 seed_data 9 领域数据模型）═══

    "tables": {
        # ── 商品域 ──
        "products": {
            "columns": {
                "product_id":      "SPU ID (SERIAL PRIMARY KEY)",
                "code":            "产品编码 (VARCHAR, 如 MK202)",
                "name":            "产品名称 (VARCHAR)",
                "brand_id":        "品牌ID (INTEGER, FK → brands.id)",
                "category_id":     "类目ID (INTEGER, FK → categories.id)",
                "lifecycle_stage": "生命周期 (VARCHAR, new/growth/mature/decline)",
                "target_market":   "目标市场 (VARCHAR)",
                "status":          "状态 (VARCHAR, active/inactive/discontinued)",
            },
            "description": "SPU 产品主表，一个产品有多个 SKU。注意列名是 products.name 不是 product_name。",
        },
        "skus": {
            "columns": {
                "sku_id":            "SKU ID (SERIAL PRIMARY KEY)",
                "product_id":        "所属 SPU ID (INTEGER, FK → products.product_id)",
                "variant_attrs":     "变体属性 (JSONB, 如 {color:Red, size:L})",
                "barcode":           "条形码 (VARCHAR)",
                "weight_g":          "重量克 (INTEGER)",
                "cost_price":        "成本价 (NUMERIC)",
                "selling_price":     "售价 (NUMERIC)",
                "hs_code":           "海关编码 (VARCHAR)",
                "country_of_origin": "原产国 (VARCHAR)",
                "status":            "状态 (VARCHAR, active/inactive)",
            },
            "description": "SKU 库存单位表，商品最小管理单元。通过 product_id 关联 products。",
        },
        "brands": {
            "columns": {
                "brand_id":     "品牌ID (SERIAL PRIMARY KEY)",
                "name":         "品牌名称 (VARCHAR, 注意列名是 name 不是 brand_name)",
                "trademark_no": "商标号 (VARCHAR)",
                "owner":        "品牌归属 (VARCHAR)",
            },
            "description": "品牌表，products.brand_id 外键关联此表。",
        },
        "categories": {
            "columns": {
                "category_id": "类目ID (SERIAL PRIMARY KEY)",
                "parent_id":   "父级类目ID (INTEGER, 自引用 FK)",
                "name":        "类目名称 (VARCHAR)",
            },
            "description": "商品类目表，自引用树形结构。",
        },

        # ── 渠道域 ──
        "channels": {
            "columns": {
                "channel_id":   "渠道ID (SERIAL PRIMARY KEY)",
                "code":         "平台代码 (VARCHAR, AMAZON_US/SHOPIFY/TIKTOK_SHOP/EBAY/WALMART)",
                "name":         "平台名称 (VARCHAR)",
                "country":      "国家/地区 (VARCHAR)",
                "currency":     "默认币种 (VARCHAR, USD/EUR/JPY)",
                "status":       "状态 (VARCHAR, active/inactive)",
            },
            "description": "销售平台/渠道表，一个渠道有多个订单和 Listing。",
        },

        # ── 订单域 ──
        "orders": {
            "columns": {
                "order_id":        "订单ID (SERIAL PRIMARY KEY)",
                "channel_id":      "渠道ID (INTEGER, FK → channels.channel_id)",
                "channel_order_id": "平台订单号 (VARCHAR)",
                "customer_id":     "客户ID (INTEGER, FK → customers.customer_id)",
                "status":          "订单状态 (VARCHAR, pending/paid/allocated/picking/shipped/delivered/cancelled/refunded)",
                "order_total":     "订单金额 (NUMERIC)",
                "currency":        "币种 (VARCHAR)",
                "placed_at":       "下单时间 (TIMESTAMP)",
                "paid_at":         "付款时间 (TIMESTAMP)",
                "shipped_at":      "发货时间 (TIMESTAMP)",
                "delivered_at":    "签收时间 (TIMESTAMP)",
            },
            "description": "统一订单表。列名 orders.status 表示订单状态，channel_order_id 保留平台原始订单号。",
        },
        "order_items": {
            "columns": {
                "order_id":   "订单ID (INTEGER, FK → orders.order_id)",
                "line_id":    "订单行号 (INTEGER)",
                "sku_id":     "SKU ID (INTEGER, FK → skus.sku_id)",
                "quantity":   "数量 (INTEGER)",
                "unit_price": "单价 (NUMERIC)",
                "line_total": "行总价 (NUMERIC)",
                "status":     "状态 (VARCHAR)",
            },
            "description": "订单明细表，一个订单可含多行商品。通过 sku_id 关联 skus。",
        },

        # ── 库存域 ──
        "warehouses": {
            "columns": {
                "warehouse_id": "仓库ID (SERIAL PRIMARY KEY)",
                "code":         "仓库编码 (VARCHAR, FBA_US/FBA_EU/3PL_USW/DOMESTIC_SZ)",
                "name":         "仓库名称 (VARCHAR)",
                "type":         "类型 (VARCHAR, DOMESTIC/FBA/3PL/TRANSIT)",
                "country":      "所在国家 (VARCHAR)",
                "region":       "区域 (VARCHAR)",
                "is_active":    "是否启用 (BOOLEAN)",
            },
            "description": "仓库表，包含国内仓/FBA/3PL/中转仓。注意列名是 warehouses.name 不是 warehouse_name。",
        },
        "inventory_levels": {
            "columns": {
                "warehouse_id":   "仓库ID (INTEGER, FK → warehouses.warehouse_id)",
                "sku_id":         "SKU ID (INTEGER, FK → skus.sku_id)",
                "qty_on_hand":    "现有库存 (INTEGER)",
                "qty_reserved":   "已预留库存 (INTEGER)",
                "qty_in_transit": "在途库存 (INTEGER)",
                "last_updated":   "最后更新时间 (TIMESTAMP)",
            },
            "description": "多仓库存快照表，复合主键 (warehouse_id, sku_id)。qty_available = qty_on_hand - qty_reserved。",
        },
        "inventory_transactions": {
            "columns": {
                "txn_id":       "事务ID (SERIAL PRIMARY KEY)",
                "warehouse_id": "仓库ID (INTEGER)",
                "sku_id":       "SKU ID (INTEGER)",
                "type":         "事务类型 (VARCHAR, INBOUND/OUTBOUND/TRANSFER/ADJUSTMENT/RESERVE/RELEASE)",
                "quantity":     "数量 (INTEGER)",
                "ref_type":     "关联单据类型 (VARCHAR, PO/SHIPMENT/ORDER)",
                "ref_id":       "关联单据ID (INTEGER)",
                "occurred_at":  "发生时间 (TIMESTAMP)",
            },
            "description": "库存流水表（append-only），不可变审计日志。",
        },

        # ── 物流域 ──
        "shipments": {
            "columns": {
                "shipment_id":   "运单ID (SERIAL PRIMARY KEY)",
                "order_id":      "订单ID (INTEGER, FK → orders.order_id)",
                "warehouse_id":  "发货仓库ID (INTEGER, FK → warehouses.warehouse_id)",
                "carrier":       "承运商 (VARCHAR, FedEx/UPS/DHL/USPS)",
                "tracking_no":   "追踪号 (VARCHAR)",
                "status":        "物流状态 (VARCHAR, label_created/in_transit/out_for_delivery/delivered)",
                "shipped_at":    "发运时间 (TIMESTAMP)",
                "delivered_at":  "签收时间 (TIMESTAMP)",
                "cost":          "运费 (NUMERIC)",
            },
            "description": "尾程物流运单表。一个订单可拆成多个 shipment。注意列名是 shipments.status。",
        },

        # ── 客户域 ──
        "customers": {
            "columns": {
                "customer_id":    "客户ID (SERIAL PRIMARY KEY)",
                "channel_id":     "渠道ID (INTEGER, FK → channels.channel_id)",
                "channel_user_id":"平台用户ID (VARCHAR)",
                "name":           "客户名 (VARCHAR)",
                "email":          "邮箱 (VARCHAR, 敏感信息)",
                "country":        "国家 (VARCHAR)",
                "segment":        "客户分层 (VARCHAR, vip/loyal/regular/new)",
                "lifetime_value": "LTV (NUMERIC)",
                "order_count":    "累计订单数 (INTEGER)",
                "first_order_at": "首单时间 (TIMESTAMP)",
                "last_order_at":  "末单时间 (TIMESTAMP)",
            },
            "description": "客户表（终端买家）。注意列名是 customers.name 不是 customer_name。",
        },

        # ── 广告域 ──
        "campaigns": {
            "columns": {
                "campaign_id":  "广告活动ID (SERIAL PRIMARY KEY)",
                "ad_account_id":"广告账户ID (INTEGER)",
                "channel":      "广告平台 (VARCHAR, AMAZON_ADS/GOOGLE_ADS/META_ADS/TIKTOK_ADS)",
                "name":         "活动名称 (VARCHAR)",
                "type":         "活动类型 (VARCHAR, SP/PRODUCT_DISPLAY/BRAND/VIDEO)",
                "status":       "状态 (VARCHAR, active/paused/ended)",
                "daily_budget": "日预算 (NUMERIC)",
                "total_budget": "总预算 (NUMERIC)",
                "start_date":   "开始日期 (DATE)",
                "end_date":     "结束日期 (DATE)",
            },
            "description": "跨平台广告活动表。注意列名是 campaigns.name 不是 campaign_name。",
        },
        "spend_records": {
            "columns": {
                "spend_id":    "花费记录ID (SERIAL PRIMARY KEY)",
                "ad_id":       "广告ID (INTEGER)",
                "campaign_id": "活动ID (INTEGER, FK → campaigns.campaign_id)",
                "date":        "日期 (DATE)",
                "spend":       "花费 (NUMERIC)",
                "impressions": "展示数 (INTEGER)",
                "clicks":      "点击数 (INTEGER)",
                "conversions": "转化数 (INTEGER)",
                "sales":       "广告销售额 (NUMERIC)",
            },
            "description": "广告花费日报表。可计算 ACoS = spend / sales, ROAS = sales / spend。",
        },

        # ── 供应商域 ──
        "suppliers": {
            "columns": {
                "supplier_id":  "供应商ID (SERIAL PRIMARY KEY)",
                "name":         "供应商名称 (VARCHAR, 注意列名是 name 不是 supplier_name)",
                "type":         "类型 (VARCHAR, MANUFACTURER/WHOLESALER)",
                "country":      "所在国家 (VARCHAR)",
                "contact_name": "联系人 (VARCHAR)",
                "payment_terms":"付款条款 (VARCHAR, NET30/NET60/TT)",
                "status":       "合作状态 (VARCHAR, active/inactive/blacklisted)",
                "rating":       "评分 (NUMERIC, 1-5)",
            },
            "description": "供应商表。注意列名是 suppliers.name 不是 supplier_name。",
        },

        # ── DCC 采集数据 (staging 层) ──
        "stg_products": {
            "columns": {
                "sku":         "SKU 编码 (VARCHAR, 主键)",
                "名称":        "商品名称 (TEXT)",
                "品类":        "品类 (VARCHAR, 电子产品/家居厨房/宠物用品/母婴/户外运动/配件)",
                "品牌":        "品牌名称 (VARCHAR, TechGleam/EcoLiving/PetPal/BabyJoy/OutdoorPro)",
                "售价":        "售价元 (FLOAT)",
                "成本":        "成本元 (FLOAT)",
                "平台":        "销售平台 (VARCHAR, Amazon/Shopify/eBay)",
                "状态":        "状态 (VARCHAR, 在售/停售)",
                "上架日期":    "上架日期 (VARCHAR)",
            },
            "description": "采集商品上架数据(staging层)。12条记录，6品类×5品牌，含售价成本毛利信息。",
        },
        "stg_orders": {
            "columns": {
                "订单号":      "订单编号 (VARCHAR, 主键)",
                "sku":         "SKU 编码 (VARCHAR, FK→stg_products.sku)",
                "数量":        "购买数量 (INTEGER)",
                "单价":        "单价元 (FLOAT)",
                "金额":        "订单金额元 (FLOAT)",
                "渠道":        "销售渠道 (VARCHAR, Amazon/Shopify/eBay)",
                "地区":        "客户地区 (VARCHAR, 美国/英国/德国/加拿大/日本)",
                "状态":        "订单状态 (VARCHAR, 已签收/已发货/处理中/已退货/已取消)",
                "下单日期":    "下单日期 (VARCHAR)",
                "签收日期":    "签收日期 (VARCHAR, 可空)",
            },
            "description": "采集订单数据(staging层)。15笔订单，3渠道×5地区，可通过sku关联stg_products。",
        },
        "stg_shops": {
            "columns": {
                "店铺id":      "店铺ID (VARCHAR, 主键)",
                "名称":        "店铺名称 (VARCHAR)",
                "平台":        "所属平台 (VARCHAR, Amazon/Shopify/eBay)",
                "地区":        "所在地区 (VARCHAR)",
                "状态":        "运营状态 (VARCHAR, 正常/已暂停)",
                "开店日期":    "开店日期 (VARCHAR)",
                "商品数":      "商品数 (INTEGER)",
                "评分":        "店铺评分 (FLOAT, 1-5)",
            },
            "description": "采集店铺数据(staging层)。8家店铺，Amazon×5/Shopify×2/eBay×1。",
        },
        "stg_inventory": {
            "columns": {
                "sku":         "SKU 编码 (VARCHAR, FK→stg_products.sku)",
                "仓库":        "仓库代码 (VARCHAR, US-EWR/UK-LHR/DE-FRA/US-LAX)",
                "库存量":      "当前库存 (INTEGER)",
                "安全库存":    "安全库存阈值 (INTEGER)",
                "预留量":      "已预留数量 (INTEGER)",
                "最后补货":    "最后补货日期 (VARCHAR)",
                "状态":        "库存状态 (VARCHAR, 充足/偏低/断货)",
                "预警":        "是否预警 (BOOLEAN)",
            },
            "description": "采集库存数据(staging层)。12条记录4个仓库。库存量<安全库存则状态=偏低/断货。",
        },
        "stg_suppliers": {
            "columns": {
                "供应商id":    "供应商ID (VARCHAR, 主键)",
                "名称":        "供应商名称 (VARCHAR)",
                "品类":        "供应品类 (VARCHAR)",
                "地区":        "所在地区 (VARCHAR, 中国/越南)",
                "交期天数":    "交货天数 (INTEGER)",
                "起订量":      "最小起订量 (INTEGER)",
                "不良率":      "不良率% (FLOAT)",
                "评分":        "综合评分 (FLOAT, 1-5)",
                "状态":        "合作状态 (VARCHAR, 合作中/评估中)",
            },
            "description": "采集供应商数据(staging层)。10家供应商，不良率<2%为优质，>3%需质量审查。",
        },
    },

    # ── 敏感列 ─────────────────────────────────────────
    # 定义格式: ["table.column", ...]
    #
    # 规则：任何 SQL 查询如果引用了此列表中的列，sql_validator.py 的 Layer 3
    # 会直接抛出 ValidationError，拒绝执行。这不是警告，是硬拦截。

    "sensitive_columns": [
        "customers.email",
    ],

    # ── 脱敏列 ─────────────────────────────────────────
    # 定义格式: {"table.column": (prefix_len, suffix_len)}
    #
    # 规则：这些列允许在 SQL 中查询，但 executor.py 返回结果前会对值做脱敏处理。
    # 脱敏算法：保留前 prefix_len 个字符 + "***" + 后 suffix_len 个字符

    "masked_columns": {
        "customers.email": (2, 1),
        "customers.name":  (1, 0),
    },

    # ── 行级安全策略 ────────────────────────────────────
    # 定义格式: {table: {column, param}}
    #
    # 跨境电商场景说明：
    # - 内部系统不做按角色的数据隔离（所有运营人员可查看所有渠道数据）
    # - 如需按渠道(channel)做数据隔离，可在此配置

    "row_security": {
        # 预留：按渠道隔离示例
        # "orders": {
        #     "column": "channel_id",
        #     "param":  "current_channel_id",
        # },
    },

    # ── 查询限制 ────────────────────────────────────────
    # max_limit: 如果 LLM 生成的 SQL 没有 LIMIT 子句，sql_validator.py 的
    #   Layer 5 会自动追加 LIMIT 100。如果显式写了更大的 LIMIT（如 LIMIT 200），
    #   则直接拒绝。这是防止全表扫描、消耗数据库资源的最后一道防线。
    # query_timeout: executor.py 在建立连接后设置 statement_timeout，超时自动中断。
    #   这是数据库层面的保护，即使应用层死循环也兜得住。

    "max_limit": 100,
    "query_timeout": 5.0,

    # ── 禁用函数黑名单 ──────────────────────────────────
    # sql_validator.py 的 Layer 4 检查：SQL 中任何函数调用如果在名单中（不区分大小写），
    # 直接拒绝。这些函数之所以被禁：
    #   sleep / pg_sleep / benchmark → 休眠攻击，耗尽连接池
    #   lo_import / lo_export       → 大对象操作，可能读写服务器文件系统
    #   pg_read_file / pg_read_binary_file   → 读取服务器文件
    #   pg_write_file / pg_write_binary_file → 写入服务器文件（更危险）
    #   dblink / dblink_exec        → 跨库访问，绕过本实例的权限控制

    "banned_functions": [
        "sleep", "pg_sleep", "benchmark",
        "lo_import", "lo_export",
        "pg_read_file", "pg_read_binary_file",
        "pg_write_file", "pg_write_binary_file",
        "dblink", "dblink_exec",
    ],
}