#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen_sql_agent_demo_seed.py — SQL Agent 演示数据种子生成器（确定性）

用途
    为 SQL Agent / NL2SQL 演示生成一批**可信、确定性、可重复应用**的电商模拟数据，
    覆盖 `backend/sql/data/schema_config.py::SCHEMA_CONFIG` 纳管的全部 7 schema / 18 张表
    （product / order / inventory / customer / crawler / finance / ai）。

    本脚本只生成 SQL 文本，不连数据库、不执行 SQL；落盘产物：
        backend/sql/seeds/sql_agent_demo_business.sql
    作用域固定为本地 PostgreSQL（docker 容器 agent-postgres-1，宿主机端口 5433，
    库 agent_business，用户 postgres）。

怎么跑
    # 生成并落盘（默认输出到 backend/sql/seeds/sql_agent_demo_business.sql）
    .venv\\Scripts\\python.exe backend/scripts/gen_sql_agent_demo_seed.py

    # 只打印到 stdout，不落盘
    .venv\\Scripts\\python.exe backend/scripts/gen_sql_agent_demo_seed.py --stdout

    # 自定义输出路径
    .venv\\Scripts\\python.exe backend/scripts/gen_sql_agent_demo_seed.py --out <path>

    # 应用到本地库（PowerShell here-string/管道，避免 `"order"` 引号被吞）
    Get-Content backend/sql/seeds/sql_agent_demo_business.sql -Raw |
        docker exec -i agent-postgres-1 psql -U postgres -d agent_business -v ON_ERROR_STOP=1 -q -f -

确定性
    唯一随机源是 `random.Random(SEED)`（SEED 写死在文件顶部，不读环境变量、不读系统时间）；
    所有时间列一律写成相对量（`NOW() - INTERVAL 'n days'`、`CURRENT_DATE - n`），
    因此**任何时刻**生成/应用，数据都落在「最近 180 天」窗口内，且脚本输出逐字节可复现。

幂等语义
    产物 SQL 整体包在**单个 BEGIN/COMMIT** 内（任一语句失败即整体回滚，不会留下
    「删了没插回来」的中间态），执行序为「序列对齐 → 子表/父表清理 → 父表到子表重插」：
      * 序列对齐（第 0 节）：本种子写入的 17 张 SERIAL 表逐个 `setval` 到 `MAX(id)`。
        历史种子（`demo_showcase_*.sql` 等）用**显式 id** 插入、从不推进序列，导致
        `MAX(id) > last_value`——不对齐则任何走 SERIAL 默认值的插入都会 nextval 出已占用
        的 id 并撞 PK（服务器环境实测 `customers_pkey (id)=(8)`）。本段不写数据、不改结构，
        幂等可重复执行。
      * 普通表：delete-then-insert，重复应用行数不增长、内容不变（SERIAL id 会重新分配）。
      * finance.expenses：不参与删除，逐行 `WHERE NOT EXISTS (date,type,amount 三元组)` 去重。
      * finance.daily_profit：不参与删除，从真实订单派生后 `ON CONFLICT (date) DO UPDATE`。
      * 插入一律只用 SERIAL 默认值，不显式指定 id；setval 只出现在第 0 节序列对齐，
      * 全脚本无 TRUNCATE、无无条件 DELETE、无 DDL、不碰角色与授权。
    因此可安全重复执行；`DEMO-SHOW-*` 客服演示单、`演示·%` 客户、`演示品牌` 商品等既有数据零影响。

标记约定（删除范围的唯一依据）
    父锚点（`LIKE` 前缀即本种子的所有权标记）：
        product.categories.name            LIKE '演示分类·%'
        product.products.sku               LIKE 'SQDEMO-%'
        customer.customers.name            LIKE '演示客户SQ%'
        inventory.warehouses.name          LIKE '演示仓·%'
        crawler.competitor_products.brand  LIKE '演示竞品%'
        "order".orders.order_no            LIKE 'SQDEMO-%'
        ai.agent_tasks.session_id          LIKE 'SQDEMO-%'
    子表按「引用了上述父锚点」删除：
        product_tags / inventory / purchase_orders / customer_behavior / product_reviews（按 product_id）
        order_items / refunds（按 order_id）、competitor_price（按竞品 id）、agent_trace（按 task_id）

数据规模（内置自检，不达标直接报错退出）
    categories 8｜products 40｜product_tags 50｜customers 50｜orders 400｜order_items ~850
    refunds 40｜warehouses 3｜inventory 120（其中 12 行低于安全库存）｜purchase_orders 24
    competitor_products 12｜competitor_price 240｜product_reviews 60｜customer_behavior 600
    expenses 90｜agent_tasks 30｜agent_trace 120
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

# ── 固定种子（写死；改它等于换一批数据）────────────────────────────
SEED = 20261008

_BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUT = _BACKEND_DIR / "sql" / "seeds" / "sql_agent_demo_business.sql"

# ── 标记前缀（与模块 docstring 的「标记约定」一处定义，勿散落）──────
SKU_PREFIX = "SQDEMO-P"
ORDER_PREFIX = "SQDEMO-O"
TASK_PREFIX = "SQDEMO-TS"
CATEGORY_PREFIX = "演示分类·"
CATEGORY_LIKE = "演示分类·%"
CUSTOMER_PREFIX = "演示客户SQ"
CUSTOMER_LIKE = "演示客户SQ%"
WAREHOUSE_PREFIX = "演示仓·"
WAREHOUSE_LIKE = "演示仓·%"
COMPETITOR_BRAND_PREFIX = "演示竞品"
COMPETITOR_BRAND_LIKE = "演示竞品%"
SKU_LIKE = "SQDEMO-%"
ORDER_LIKE = "SQDEMO-%"
TASK_LIKE = "SQDEMO-%"
COMPETITOR_URL_PREFIX = "https://demo.example.com/"

# ── 目标规模 ────────────────────────────────────────────────────────
N_CATEGORIES = 8
N_PRODUCTS = 40
N_TAGS = 50
N_CUSTOMERS = 50
N_ORDERS = 400
N_REFUNDS = 40
N_WAREHOUSES = 3
N_INVENTORY = 120
N_LOW_STOCK = 12
N_PURCHASE_ORDERS = 24
N_COMPETITOR_PRODUCTS = 12
N_COMPETITOR_PRICE_POINTS = 20
N_REVIEWS = 60
N_BEHAVIOR = 600
N_EXPENSES = 90
N_TASKS = 30
N_TRACES = 120

# 本种子会写入、且 id 为 SERIAL 的表（序列对齐段与结尾守卫共用此清单）。
# 写法同时充当 SQL 标识符与 pg_get_serial_sequence 的文本实参：保留字 schema
# `order` 在文本实参里必须带双引号（'"order".orders'），故此处一并写死引号。
SERIAL_TABLES: List[str] = [
    "product.categories",
    "product.products",
    "product.product_tags",
    '"order".orders',
    '"order".order_items',
    '"order".refunds',
    "inventory.warehouses",
    "inventory.inventory",
    "inventory.purchase_orders",
    "customer.customers",
    "customer.customer_behavior",
    "crawler.competitor_products",
    "crawler.competitor_price",
    "crawler.product_reviews",
    "finance.expenses",
    "ai.agent_tasks",
    "ai.agent_trace",
]

# ── 静态素材 ────────────────────────────────────────────────────────
CATEGORIES: List[Tuple[str, str | None]] = [
    ("演示分类·数码影音", None),
    ("演示分类·家居生活", None),
    ("演示分类·服饰运动", None),
    ("演示分类·个护美妆", None),
    ("演示分类·无线耳机", "演示分类·数码影音"),
    ("演示分类·厨房电器", "演示分类·家居生活"),
    ("演示分类·运动户外", "演示分类·服饰运动"),
    ("演示分类·护肤彩妆", "演示分类·个护美妆"),
]

# 每池 5 个商品 = 8 池 × 5 = 40；cost 为成本价区间，markup 为售价倍率区间
PRODUCT_POOLS: List[Dict[str, Any]] = [
    {
        "category": "演示分类·无线耳机",
        "names": [
            "主动降噪蓝牙耳机 Pro",
            "骨传导运动耳机",
            "真无线耳机 Lite",
            "头戴式监听耳机",
            "开放式蓝牙耳机",
        ],
        "cost": (90.0, 420.0),
        "markup": (2.0, 2.6),
    },
    {
        "category": "演示分类·数码影音",
        "names": [
            "便携投影仪 mini",
            "智能运动手环",
            "蓝牙音箱 mini",
            "高清网络摄像头",
            "无线磁吸充电宝",
        ],
        "cost": (85.0, 620.0),
        "markup": (1.9, 2.5),
    },
    {
        "category": "演示分类·厨房电器",
        "names": [
            "便携榨汁杯 400ml",
            "空气炸锅 5L",
            "智能电饭煲 4L",
            "桌面胶囊咖啡机",
            "多功能破壁料理机",
        ],
        "cost": (65.0, 520.0),
        "markup": (1.9, 2.4),
    },
    {
        "category": "演示分类·家居生活",
        "names": [
            "石墨烯暖手宝",
            "桌面静音加湿器",
            "记忆棉护腰靠垫",
            "智能香薰机",
            "折叠布艺收纳箱",
        ],
        "cost": (14.0, 160.0),
        "markup": (2.5, 3.4),
    },
    {
        "category": "演示分类·运动户外",
        "names": [
            "轻量防风冲锋衣",
            "户外防水音箱",
            "折叠碳纤维登山杖",
            "速干透气运动T恤",
            "便携露营氛围灯",
        ],
        "cost": (42.0, 310.0),
        "markup": (2.1, 2.8),
    },
    {
        "category": "演示分类·服饰运动",
        "names": [
            "羊毛混纺针织衫",
            "宽松工装休闲裤",
            "轻薄鹅绒羽绒服",
            "纯棉基础款卫衣",
            "户外防风马甲",
        ],
        "cost": (35.0, 270.0),
        "markup": (2.2, 3.0),
    },
    {
        "category": "演示分类·护肤彩妆",
        "names": [
            "氨基酸温和洁面乳",
            "烟酰胺提亮精华液",
            "持久持妆粉底液",
            "玻尿酸补水保湿面霜",
            "清爽防晒喷雾 SPF50",
        ],
        "cost": (16.0, 130.0),
        "markup": (2.6, 3.6),
    },
    {
        "category": "演示分类·个护美妆",
        "names": [
            "声波电动牙刷",
            "硅胶声波洁面仪",
            "便携电动剃须刀",
            "免洗护发精油",
            "恒温蒸汽眼罩",
        ],
        "cost": (22.0, 190.0),
        "markup": (2.5, 3.3),
    },
]

BRANDS = ["声阔云", "沁禾", "驰野", "云栖", "极米造物", "素研", "牧野工坊"]

TAG_PLAN: List[Tuple[str, int]] = [("爆款", 14), ("新品", 12), ("清仓", 10), ("高利润", 14)]

SURNAMES = [
    "陈", "林", "黄", "张", "李", "王", "吴", "刘", "郑", "谢",
    "许", "何", "罗", "高", "马", "梁", "宋", "唐", "冯", "韩",
]
CUSTOMER_LEVELS = ["普通"] * 23 + ["银卡"] * 13 + ["金卡"] * 9 + ["VIP"] * 5
CUSTOMER_GENDERS = ["M"] * 26 + ["F"] * 24

WAREHOUSES = [
    ("演示仓·杭州", "杭州"),
    ("演示仓·广州", "广州"),
    ("演示仓·成都", "成都"),
]

ORDER_STATUS_PLAN = (
    ["completed"] * 220  # 55%
    + ["shipped"] * 60
    + ["paid"] * 48
    + ["pending"] * 32
    + ["cancelled"] * 40
)

REFUND_REASONS = [
    "商品质量问题",
    "与描述不符",
    "尺寸不合适",
    "物流破损",
    "七天无理由退货",
    "发错商品",
    "拍错/多拍",
    "效果不满意",
]

REVIEW_TEXT = {
    "positive": [
        "用了两周，续航和降噪都比预期好，会回购。",
        "做工扎实，物流很快，外包装也没有磕碰。",
        "性价比很高，同价位里算很能打的。",
        "第二次购买了，这次是送朋友的，反馈很好。",
        "客服响应及时，问题当天就解决了。",
    ],
    "neutral": [
        "整体还行，没有特别惊艳的地方。",
        "功能符合描述，就是说明书太简陋了。",
        "价格波动有点大，买完就降价了。",
        "外观和图片基本一致，容量比想象中小。",
        "用了一周，暂时没发现什么问题。",
    ],
    "negative": [
        "收到就是坏的，退货流程还很麻烦。",
        "和详情页描述差距明显，材质很一般。",
        "用了三天就出现异响，质量堪忧。",
        "物流太慢，足足等了十天才到。",
        "尺寸和标注不符，只能申请退款。",
    ],
}

PURCHASE_STATUS_PLAN = ["pending"] * 10 + ["arrived"] * 9 + ["cancelled"] * 5

COMPETITOR_PRODUCTS = [
    ("Amazon", "演示竞品01", "无线降噪耳机 X2", "数码影音"),
    ("Amazon", "演示竞品02", "智能体脂秤 S5", "数码影音"),
    ("Amazon", "演示竞品03", "便携咖啡机 Mini", "厨房电器"),
    ("Amazon", "演示竞品04", "户外防水背包 30L", "运动户外"),
    ("TikTok Shop", "演示竞品05", "持妆粉底液 30ml", "护肤彩妆"),
    ("TikTok Shop", "演示竞品06", "折叠露营桌", "运动户外"),
    ("TikTok Shop", "演示竞品07", "石墨烯暖手宝 2代", "家居生活"),
    ("TikTok Shop", "演示竞品08", "氨基酸洁面慕斯", "护肤彩妆"),
    ("淘宝", "演示竞品09", "主动降噪头戴耳机", "无线耳机"),
    ("淘宝", "演示竞品10", "空气炸锅 6L", "厨房电器"),
    ("淘宝", "演示竞品11", "羊毛针织开衫", "服饰运动"),
    ("淘宝", "演示竞品12", "声波电动牙刷 Pro", "个护美妆"),
]

EXPENSE_TYPES = ["广告费", "物流费", "人工"]

(TASK_QUERY, TASK_TYPE) = (
    [
        "近30天销量最高的10个商品是哪些",
        "查一下库存低于安全库存阈值的商品",
        "上个月各品牌的销售额占比",
        "退款率最高的5个商品",
        "最近7天的每日利润趋势",
        "华东区域的订单量同比变化",
        "竞品价格最近一个月降幅最大的商品",
        "加购但未下单的用户有多少",
        "按类目统计毛利率排名",
        "广告费投入产出比最高的渠道",
        "客单价最高的会员等级",
        "各仓库的库存周转情况",
        "本月新客注册与首单转化率",
        "滞销商品（近60天无销量）清单",
        "评分为1到2星的商品主要集中在哪些类目",
        "复购次数超过3次的客户名单",
        "各支付状态的订单金额分布",
        "采购在途订单的预计到货情况",
        "售价低于成本价1.5倍的商品",
        "最近90天单量最高的星期一",
        "各平台的竞品平均折扣力度",
        "取消率最高的商品类目",
        "金卡及以上客户的流失风险分布",
        "上午下单和晚上下单的客单价差异",
        "单品贡献利润前10名",
        "退款原因分布及占比",
        "各品牌在售SKU数量",
        "行为漏斗从浏览到下单的转化率",
        "近30天物流费用与订单量的关系",
        "高利润标签商品的实际利润率",
    ],
    [
        "sql.query", "inventory.alert", "business.analyze", "business.analyze",
        "finance.report", "sql.query", "competitor.monitor", "customer.insight",
        "business.analyze", "finance.report", "customer.insight", "inventory.alert",
        "customer.insight", "business.analyze", "crawler.review", "customer.insight",
        "sql.query", "inventory.alert", "business.analyze", "sql.query",
        "competitor.monitor", "business.analyze", "customer.insight", "sql.query",
        "finance.report", "business.analyze", "business.analyze", "customer.insight",
        "finance.report", "finance.report",
    ],
)

TASK_STATUS_PLAN = ["success"] * 24 + ["failed"] * 4 + ["running"] * 2

TRACE_NODE_PLANS = [
    ["router", "planner", "supervisor", "reporter"],
    ["router", "skill_executor", "validator", "reporter"],
    ["router", "planner", "sql_generator", "reporter"],
]

BEHAVIOR_PLAN = ["view"] * 270 + ["click"] * 150 + ["add_cart"] * 120 + ["favorite"] * 60


# ═══════════════════════════════════════════════════════════════════
# SQL 文本辅助
# ═══════════════════════════════════════════════════════════════════

def sql_str(value: str) -> str:
    """SQL 字符串字面量（单引号转义）。"""
    return "'" + str(value).replace("'", "''") + "'"


def sql_jsonb(obj: Any) -> str:
    """紧凑 JSONB 字面量（键值一律双引号，避免与外层单引号冲突）。"""
    payload = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
    return sql_str(payload) + "::jsonb"


def cents(value: int) -> str:
    """分 → NUMERIC 字面量（整数运算，杜绝浮点误差）。"""
    return f"{value // 100}.{value % 100:02d}"


def ts(days: int, minutes: int = 0) -> str:
    """相对时间戳表达式：NOW() - INTERVAL '<days> days' [- '<minutes> minutes']。"""
    expr = f"NOW() - INTERVAL '{days} days'"
    if minutes:
        expr += f" - INTERVAL '{minutes} minutes'"
    return expr


def date_expr(days: int) -> str:
    """相对日期表达式（DATE 列用）。"""
    return f"CURRENT_DATE - {days}"


def values_rows(rows: Sequence[Sequence[str]]) -> str:
    return ",\n".join("  (" + ", ".join(cells) + ")" for cells in rows)


# ═══════════════════════════════════════════════════════════════════
# 数据构造（全部走同一个 rng，保证可复现）
# ═══════════════════════════════════════════════════════════════════

def build_categories() -> List[Tuple[str, str | None]]:
    assert len(CATEGORIES) == N_CATEGORIES
    return list(CATEGORIES)


def build_products(rng: random.Random) -> List[Dict[str, Any]]:
    raw: List[Tuple[str, str, Tuple[float, float], Tuple[float, float]]] = []
    for pool in PRODUCT_POOLS:
        for name in pool["names"]:
            raw.append((pool["category"], name, pool["cost"], pool["markup"]))
    assert len(raw) == N_PRODUCTS, f"商品池数量 {len(raw)} != {N_PRODUCTS}"
    rng.shuffle(raw)

    inactive_idx = set(rng.sample(range(N_PRODUCTS), 2))
    products: List[Dict[str, Any]] = []
    for idx, (category, name, cost_range, markup_range) in enumerate(raw):
        cost_c = int(round(rng.uniform(*cost_range) * 100))
        markup = rng.uniform(*markup_range)
        sale_c = int(math.ceil(cost_c * markup / 1000.0) * 1000) - 10  # 向上取整到整元再减 0.1
        if sale_c <= cost_c:
            sale_c = int(round(cost_c * 1.9))
        assert sale_c > cost_c > 0, f"{name} 售价未高于成本价"
        products.append(
            {
                "sku": f"{SKU_PREFIX}{idx + 1:03d}",
                "name": name,
                "category": category,
                "brand": BRANDS[idx % len(BRANDS)],
                "cost_c": cost_c,
                "sale_c": sale_c,
                "status": "inactive" if idx in inactive_idx else "active",
                "created_days": rng.randint(200, 600),
                "created_minutes": rng.randint(0, 1439),
            }
        )
    return products


def build_tags(rng: random.Random, products: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    idx_pool = list(range(len(products)))
    assigned: Dict[int, List[str]] = {}
    for tag, count in TAG_PLAN:
        for i in rng.sample(idx_pool, count):
            assigned.setdefault(i, []).append(tag)
    rows: List[Tuple[str, str]] = []
    for i in sorted(assigned):
        for tag in assigned[i]:
            rows.append((products[i]["sku"], tag))
    assert len(rows) == N_TAGS, f"标签行数 {len(rows)} != {N_TAGS}"
    return rows


def build_customers(rng: random.Random) -> List[Dict[str, Any]]:
    levels = list(CUSTOMER_LEVELS)
    genders = list(CUSTOMER_GENDERS)
    rng.shuffle(levels)
    rng.shuffle(genders)
    customers: List[Dict[str, Any]] = []
    for i in range(N_CUSTOMERS):
        customers.append(
            {
                "name": f"{CUSTOMER_PREFIX}{i + 1:03d}·{rng.choice(SURNAMES)}",
                "gender": genders[i],
                "level": levels[i],
                "reg_days": rng.randint(30, 547),  # 最近 18 个月
                "reg_minutes": rng.randint(0, 1439),
            }
        )
    return customers


def build_orders(
    rng: random.Random,
    products: List[Dict[str, Any]],
    customers: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    sellable = [p for p in products if p["status"] == "active"]
    statuses = list(ORDER_STATUS_PLAN)
    rng.shuffle(statuses)

    orders: List[Dict[str, Any]] = []
    for i in range(N_ORDERS):
        days = rng.randint(1, 179)
        minutes = rng.randint(0, 1439)
        status = statuses[i]
        eligible = [c for c in customers if c["reg_days"] > days + 3] or customers
        customer = rng.choice(eligible)

        n_items = rng.choices([1, 2, 3, 4], weights=[30, 38, 22, 10])[0]
        picked = rng.sample(sellable, n_items)
        items = []
        total_c = 0
        for product in picked:
            qty = rng.choices([1, 2, 3], weights=[70, 22, 8])[0]
            line_c = product["sale_c"] * qty
            total_c += line_c
            items.append(
                {
                    "sku": product["sku"],
                    "quantity": qty,
                    "price_c": product["sale_c"],
                    "cost_c": product["cost_c"],
                }
            )

        if status == "pending":
            payment_status = "unpaid"
        elif status == "cancelled":
            payment_status = "unpaid"  # 拿到退款的取消单在下面改写为 refunded
        else:
            payment_status = "paid"

        orders.append(
            {
                "order_no": "",  # 排序后统一编号
                "customer_name": customer["name"],
                "status": status,
                "payment_status": payment_status,
                "days": days,
                "minutes": minutes,
                "items": items,
                "total_c": total_c,
            }
        )

    # 下单时间升序 = 订单号升序（老的先编号）
    orders.sort(key=lambda o: (-o["days"], -o["minutes"], o["customer_name"]))
    for i, order in enumerate(orders, start=1):
        order["order_no"] = f"{ORDER_PREFIX}{i:04d}"

    # 退款：24 单 completed + 16 单 cancelled（只挂这两类状态）
    completed_idx = [i for i, o in enumerate(orders) if o["status"] == "completed"]
    cancelled_idx = [i for i, o in enumerate(orders) if o["status"] == "cancelled"]
    refund_idx = rng.sample(completed_idx, 24) + rng.sample(cancelled_idx, 16)

    refunds: List[Dict[str, Any]] = []
    for i in refund_idx:
        order = orders[i]
        line = rng.choice(order["items"])  # 整行退款，金额天然 <= 订单金额
        amount_c = line["price_c"] * line["quantity"]
        assert amount_c <= order["total_c"]
        delta = rng.randint(1, min(10, order["days"]))
        refunds.append(
            {
                "order_no": order["order_no"],
                "sku": line["sku"],
                "refund_c": amount_c,
                "reason": rng.choice(REFUND_REASONS),
                "days": max(0, order["days"] - delta),
                "minutes": rng.randint(0, 1439),
            }
        )
        if order["status"] == "cancelled":
            order["payment_status"] = "refunded"

    refunds.sort(key=lambda r: r["order_no"])
    return orders, refunds


def build_inventory(
    rng: random.Random, products: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    pairs = [(p["sku"], w[0]) for p in products for w in WAREHOUSES]
    assert len(pairs) == N_INVENTORY
    low_pairs = set(rng.sample(pairs, N_LOW_STOCK))

    rows: List[Dict[str, Any]] = []
    for sku, warehouse in pairs:
        safety = rng.randint(20, 120)
        if (sku, warehouse) in low_pairs:
            stock = rng.randint(0, safety - 1)
        else:
            stock = rng.randint(safety, safety * 4)
        assert stock >= 0 and safety >= 0
        rows.append(
            {
                "sku": sku,
                "warehouse": warehouse,
                "stock": stock,
                "safety": safety,
                "updated_days": rng.randint(1, 14),
                "updated_minutes": rng.randint(0, 1439),
            }
        )
    assert sum(1 for r in rows if r["stock"] < r["safety"]) == N_LOW_STOCK
    return rows


def build_purchase_orders(
    rng: random.Random, products: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    statuses = list(PURCHASE_STATUS_PLAN)
    rng.shuffle(statuses)
    rows: List[Dict[str, Any]] = []
    for i in range(N_PURCHASE_ORDERS):
        product = rng.choice(products)
        rows.append(
            {
                "supplier_id": rng.randint(1, 20),
                "sku": product["sku"],
                "quantity": rng.randint(20, 500),
                "status": statuses[i],
                "days": rng.randint(1, 90),
                "minutes": rng.randint(0, 1439),
            }
        )
    return rows


def build_competitor_products() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for i, (platform, brand, product_name, category) in enumerate(COMPETITOR_PRODUCTS, start=1):
        slug = {"Amazon": "amazon", "TikTok Shop": "tiktok", "淘宝": "taobao"}[platform]
        rows.append(
            {
                "platform": platform,
                "brand": brand,
                "product_name": product_name,
                "category": category,
                "url": f"{COMPETITOR_URL_PREFIX}{slug}/cp-{i:02d}",
            }
        )
    assert len(rows) == N_COMPETITOR_PRODUCTS
    return rows


def build_competitor_price(
    rng: random.Random, competitor_products: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for product in competitor_products:
        price = rng.uniform(50.0, 800.0)
        for k in range(N_COMPETITOR_PRICE_POINTS):
            price = max(5.0, price * (1 + rng.uniform(-0.06, 0.06)))
            days = 57 - k * 3
            discount = rng.choice(
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
            )
            assert 0.0 <= discount <= 0.5
            rows.append(
                {
                    "url": product["url"],
                    "price_c": int(round(price * 100)),
                    "discount": discount,
                    "days": max(0, days),
                    "minutes": rng.randint(0, 1439),
                }
            )
    assert len(rows) == N_COMPETITOR_PRODUCTS * N_COMPETITOR_PRICE_POINTS
    return rows


def build_reviews(rng: random.Random, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rating_plan = [5] * 18 + [4] * 18 + [3] * 9 + [2] * 9 + [1] * 6  # = 60
    rng.shuffle(rating_plan)
    rows: List[Dict[str, Any]] = []
    for i in range(N_REVIEWS):
        product = rng.choice(products)
        rating = rating_plan[i]
        sentiment = "positive" if rating >= 4 else ("neutral" if rating == 3 else "negative")
        rows.append(
            {
                "sku": product["sku"],
                "rating": rating,
                "review_text": rng.choice(REVIEW_TEXT[sentiment]),
                "sentiment": sentiment,
                "days": rng.randint(0, 119),
                "minutes": rng.randint(0, 1439),
            }
        )
    return rows


def build_behavior(
    rng: random.Random, products: List[Dict[str, Any]], customers: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    sellable = [p for p in products if p["status"] == "active"]
    elig_customers = [c for c in customers if c["reg_days"] >= 60] or customers
    events = list(BEHAVIOR_PLAN)
    rng.shuffle(events)
    rows: List[Dict[str, Any]] = []
    for i in range(N_BEHAVIOR):
        rows.append(
            {
                "customer_name": rng.choice(elig_customers)["name"],
                "event_type": events[i],
                "sku": rng.choice(sellable)["sku"],
                "days": rng.randint(0, 59),  # 均晚于注册时间（注册 >= 60 天前）
                "minutes": rng.randint(0, 1439),
            }
        )
    return rows


def build_expenses(rng: random.Random) -> List[Dict[str, Any]]:
    days = list(range(N_EXPENSES))  # 90 行覆盖最近 90 天，每天一行
    rng.shuffle(days)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for i, day in enumerate(days):
        etype = EXPENSE_TYPES[i % len(EXPENSE_TYPES)]
        amount_c = rng.randint(5000, 300000)  # 50.00 ~ 3000.00
        key = (day, etype, amount_c)
        while key in seen:  # 理论上不会命中（日期互异），保留防御
            amount_c = rng.randint(5000, 300000)
            key = (day, etype, amount_c)
        seen.add(key)
        assert amount_c > 0
        rows.append({"type": etype, "amount_c": amount_c, "days": day})
    assert len({(r["days"], r["type"], r["amount_c"]) for r in rows}) == N_EXPENSES
    return rows


def build_tasks_and_traces(
    rng: random.Random,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    statuses = list(TASK_STATUS_PLAN)
    rng.shuffle(statuses)
    tasks: List[Dict[str, Any]] = []
    for i in range(N_TASKS):
        days = rng.randint(1, 30)
        minutes = rng.randint(0, 1439)
        tasks.append(
            {
                "session_id": f"{TASK_PREFIX}{i + 1:03d}",
                "user_query": TASK_QUERY[i],
                "task_type": TASK_TYPE[i],
                "status": statuses[i],
                "days": days,
                "minutes": minutes,
            }
        )

    traces: List[Dict[str, Any]] = []
    for i, task in enumerate(tasks):
        plan = TRACE_NODE_PLANS[i % len(TRACE_NODE_PLANS)]
        offset = 0
        for node in plan:
            offset += rng.randint(1, 9)
            payload_in: Dict[str, Any]
            payload_out: Dict[str, Any]
            if node == "router":
                payload_in = {"question": task["user_query"]}
                payload_out = {"domain": "sql", "intent": task["task_type"]}
            elif node == "planner":
                payload_in = {"question": task["user_query"]}
                payload_out = {"steps": rng.randint(1, 3)}
            elif node == "sql_generator":
                payload_in = {"tables": ["order.orders", "order.order_items"]}
                payload_out = {"sql_chars": rng.randint(120, 400)}
            elif node == "skill_executor":
                payload_in = {"skill": "sql_analysis"}
                payload_out = {"rows": rng.randint(1, 100)}
            elif node == "validator":
                payload_in = {"layer_count": 6}
                payload_out = {"passed": True}
            elif node == "supervisor":
                payload_in = {"task_id": task["session_id"]}
                payload_out = {"dispatched": rng.randint(1, 3)}
            else:  # reporter
                payload_in = {"steps": rng.randint(1, 3)}
                payload_out = {"answer_chars": rng.randint(80, 600)}
            traces.append(
                {
                    "session_id": task["session_id"],
                    "node": node,
                    "input": payload_in,
                    "output": payload_out,
                    "duration": round(rng.uniform(0.05, 3.5), 3),
                    "days": task["days"],
                    "minutes": max(0, task["minutes"] - offset),
                }
            )
    assert len(traces) == N_TRACES
    return tasks, traces


# ═══════════════════════════════════════════════════════════════════
# SQL 渲染
# ═══════════════════════════════════════════════════════════════════

HEADER = f"""-- ═══════════════════════════════════════════════════════════════════
-- sql_agent_demo_business.sql — SQL Agent 演示业务数据种子（自动生成，勿手改）
--
-- 生成器：backend/scripts/gen_sql_agent_demo_seed.py（固定种子 {SEED}）
-- 目标库：docker 容器 agent-postgres-1 / 宿主端口 5433 / 库 agent_business / 用户 postgres
-- 覆盖  ：schema_config.SCHEMA_CONFIG 纳管的 7 schema × 18 表
--
-- 应用（PowerShell，注意 `"order"` 引号，走 here-string 管道最稳）：
--   Get-Content backend/sql/seeds/sql_agent_demo_business.sql -Raw |
--     docker exec -i agent-postgres-1 psql -U postgres -d agent_business -v ON_ERROR_STOP=1 -q -f -
--
-- 幂等：BEGIN/COMMIT 内按「序列对齐 → 子表删 → 父表删 → 父表到子表重插」执行。
--       第 0 节序列对齐：本种子写入的 17 张 SERIAL 表逐个 setval 到 MAX(id)——
--       历史种子用显式 id 插入、从不推进序列，不对齐则走默认值的插入必然撞 PK。
--       该段不写数据、不改结构，幂等可重复执行。
--       finance.expenses 不删除，走逐行 WHERE NOT EXISTS(date,type,amount)；
--       finance.daily_profit 不删除，从真实订单派生后 ON CONFLICT (date) DO UPDATE。
--       重复应用行数不增长（SERIAL id 会重新分配）。
--
-- 标记约定（删除范围唯一依据）：
--   父锚点  product.categories.name            LIKE '演示分类·%'
--           product.products.sku               LIKE 'SQDEMO-%'
--           customer.customers.name            LIKE '演示客户SQ%'
--           inventory.warehouses.name          LIKE '演示仓·%'
--           crawler.competitor_products.brand  LIKE '演示竞品%'
--           "order".orders.order_no            LIKE 'SQDEMO-%'
--           ai.agent_tasks.session_id          LIKE 'SQDEMO-%'
--   子表    按「引用了上述父锚点」删除（product_tags/inventory/purchase_orders/
--           customer_behavior/product_reviews 按 product_id；order_items/refunds 按
--           order_id；competitor_price 按竞品 id；agent_trace 按 task_id）。
--
-- 安全边界：无 TRUNCATE、无无条件 DELETE、无 DDL、不碰角色与授权；
--           不影响既有 DEMO-SHOW-* 订单 / 演示·% 客户 / 演示品牌 商品。
--
-- 时间口径：全部为相对量（NOW() - INTERVAL 'n days' / CURRENT_DATE - n），
--           任何时刻应用都落在「最近 180 天」窗口内。
-- ═══════════════════════════════════════════════════════════════════

BEGIN;
"""


def render_sequence_alignment() -> List[str]:
    """序列对齐段：把本种子会写入的 17 张 SERIAL 表的序列修正到与表内数据一致。

    为什么必须做：`demo_showcase_business.sql` / `demo_showcase_users.sql` 用**显式 id**
    （= auth uid）插入客户，从不推进序列；其它历史种子同样如此。于是 `MAX(id)` 远大于
    序列的 `last_value`，任何走 SERIAL 默认值的插入都会 nextval 出一个已被占用的 id 并撞 PK
    （服务器环境实测：`duplicate key value violates unique constraint "customers_pkey"
    Key (id)=(8)`）。本段不写数据、不改结构，只把序列与数据对齐；幂等，可重复执行。

    setval 的第二个参数用**标量子查询**而不是 `FROM <table>`：`SELECT setval(...) FROM t`
    在 t 为空时返回 0 行、setval 根本不会被调用（已实测），空表就退化不成 1；标量子查询
    保证每张表恰好调用一次 setval。setval(seq, n) 默认 is_called=true → 下一次 nextval = n+1。
    """
    lines = [
        "",
        "-- ═══ 0. 序列对齐（先于清理；同一事务内，失败则整体回滚）═══",
        "-- 显式 id 种子会滞后序列 → 不对齐则任何走默认值的插入撞 PK；本段幂等、可重复执行。",
        "-- 不是 DDL、不动任何数据，只把已损坏的序列修正到与数据一致。",
    ]
    for table in SERIAL_TABLES:
        lines += [
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),",
            f"              GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 0), 1));",
        ]
    return lines


def render_cleanup() -> List[str]:
    return [
        "",
        "-- ═══ 1. 幂等清理（子表 → 父表；只删本种子标记行）═══",
        # 整脚本单一事务：清理与插入同生共死，任一步失败都不会留下「删了没插回来」的中间态。
        "-- 子表：按引用了本种子父锚点删除",
        f"DELETE FROM product.product_tags\n"
        f" WHERE product_id IN (SELECT id FROM product.products WHERE sku LIKE '{SKU_LIKE}');",
        f"DELETE FROM crawler.product_reviews\n"
        f" WHERE product_id IN (SELECT id FROM product.products WHERE sku LIKE '{SKU_LIKE}');",
        f"DELETE FROM inventory.inventory\n"
        f" WHERE product_id IN (SELECT id FROM product.products WHERE sku LIKE '{SKU_LIKE}')\n"
        f"    OR warehouse_id IN (SELECT id FROM inventory.warehouses WHERE name LIKE '{WAREHOUSE_LIKE}');",
        f"DELETE FROM inventory.purchase_orders\n"
        f" WHERE product_id IN (SELECT id FROM product.products WHERE sku LIKE '{SKU_LIKE}');",
        f"DELETE FROM customer.customer_behavior\n"
        f" WHERE customer_id IN (SELECT id FROM customer.customers WHERE name LIKE '{CUSTOMER_LIKE}')\n"
        f"    OR product_id IN (SELECT id FROM product.products WHERE sku LIKE '{SKU_LIKE}');",
        f"DELETE FROM \"order\".order_items\n"
        f" WHERE order_id IN (SELECT id FROM \"order\".orders WHERE order_no LIKE '{ORDER_LIKE}');",
        f"DELETE FROM \"order\".refunds\n"
        f" WHERE order_id IN (SELECT id FROM \"order\".orders WHERE order_no LIKE '{ORDER_LIKE}')\n"
        f"    OR product_id IN (SELECT id FROM product.products WHERE sku LIKE '{SKU_LIKE}');",
        f"DELETE FROM crawler.competitor_price\n"
        f" WHERE product_id IN (SELECT id FROM crawler.competitor_products"
        f" WHERE brand LIKE '{COMPETITOR_BRAND_LIKE}');",
        f"DELETE FROM ai.agent_trace\n"
        f" WHERE task_id IN (SELECT id FROM ai.agent_tasks WHERE session_id LIKE '{TASK_LIKE}');",
        "",
        "-- 父表：先删引用方，再删锚点自身",
        f"DELETE FROM \"order\".orders WHERE order_no LIKE '{ORDER_LIKE}';",
        f"DELETE FROM customer.customers WHERE name LIKE '{CUSTOMER_LIKE}';",
        f"DELETE FROM ai.agent_tasks WHERE session_id LIKE '{TASK_LIKE}';",
        f"DELETE FROM product.products WHERE sku LIKE '{SKU_LIKE}';",
        f"DELETE FROM crawler.competitor_products WHERE brand LIKE '{COMPETITOR_BRAND_LIKE}';",
        "-- 分类是自引用树：先删二级（parent_id 非空）再删一级",
        f"DELETE FROM product.categories WHERE name LIKE '{CATEGORY_LIKE}' AND parent_id IS NOT NULL;",
        f"DELETE FROM product.categories WHERE name LIKE '{CATEGORY_LIKE}';",
        f"DELETE FROM inventory.warehouses WHERE name LIKE '{WAREHOUSE_LIKE}';",
        "",
        "-- ═══ 2. 插入（父表 → 子表；不显式指定 id，父子引用一律自然键 join）═══",
    ]


def render_categories(categories: Sequence[Tuple[str, str | None]]) -> List[str]:
    top = [c for c in categories if c[1] is None]
    sub = [c for c in categories if c[1] is not None]
    top_rows = [[sql_str(name)] for name, _ in top]
    sub_rows = [[sql_str(name), sql_str(parent)] for name, parent in sub]
    return [
        "",
        f"-- ── 2.1 商品分类（{len(categories)} 条 = 4 一级 + 4 二级，parent_id 组树）──",
        "INSERT INTO product.categories (name, parent_id)",
        "SELECT v.name, NULL",
        "FROM (VALUES",
        values_rows(top_rows),
        ") AS v(name);",
        "",
        "INSERT INTO product.categories (name, parent_id)",
        "SELECT v.name, p.id",
        "FROM (VALUES",
        values_rows(sub_rows),
        f") AS v(name, parent_name)",
        f"JOIN product.categories p ON p.name = v.parent_name AND p.name LIKE '{CATEGORY_LIKE}';",
    ]


def render_products(products: Sequence[Dict[str, Any]]) -> List[str]:
    rows = [
        [
            sql_str(p["sku"]),
            sql_str(p["name"]),
            sql_str(p["category"]),
            sql_str(p["brand"]),
            cents(p["cost_c"]),
            cents(p["sale_c"]),
            sql_str(p["status"]),
            ts(p["created_days"], p["created_minutes"]),
        ]
        for p in products
    ]
    return [
        "",
        f"-- ── 2.2 商品 SPU（{len(products)} 条；6~8 品牌、8 类目全覆盖、2 条 inactive）──",
        "INSERT INTO product.products",
        "  (sku, product_name, category_id, brand, cost_price, sale_price, status, created_at)",
        "SELECT v.sku, v.product_name, c.id, v.brand, v.cost_price, v.sale_price, v.status, v.created_at",
        "FROM (VALUES",
        values_rows(rows),
        ") AS v(sku, product_name, category_name, brand, cost_price, sale_price, status, created_at)",
        f"JOIN product.categories c ON c.name = v.category_name AND c.name LIKE '{CATEGORY_LIKE}';",
    ]


def render_tags(tags: Sequence[Tuple[str, str]]) -> List[str]:
    rows = [[sql_str(sku), sql_str(tag)] for sku, tag in tags]
    return [
        "",
        f"-- ── 2.3 商品标签（{len(tags)} 条：爆款/新品/清仓/高利润 混合）──",
        "INSERT INTO product.product_tags (product_id, tag)",
        "SELECT p.id, v.tag",
        "FROM (VALUES",
        values_rows(rows),
        ") AS v(sku, tag)",
        f"JOIN product.products p ON p.sku = v.sku AND p.sku LIKE '{SKU_LIKE}';",
    ]


def render_customers(customers: Sequence[Dict[str, Any]]) -> List[str]:
    rows = [
        [sql_str(c["name"]), sql_str(c["gender"]), sql_str(c["level"]), ts(c["reg_days"], c["reg_minutes"])]
        for c in customers
    ]
    return [
        "",
        f"-- ── 2.4 客户（{len(customers)} 条；M/F、普通/银卡/金卡/VIP 齐全）──",
        "INSERT INTO customer.customers (name, gender, level, register_time)",
        "SELECT v.name, v.gender, v.level, v.register_time",
        "FROM (VALUES",
        values_rows(rows),
        ") AS v(name, gender, level, register_time);",
    ]


def render_warehouses() -> List[str]:
    rows = [[sql_str(name), sql_str(location)] for name, location in WAREHOUSES]
    return [
        "",
        f"-- ── 2.5 仓库（{len(WAREHOUSES)} 条：杭州/广州/成都）──",
        "INSERT INTO inventory.warehouses (name, location)",
        "SELECT v.name, v.location",
        "FROM (VALUES",
        values_rows(rows),
        ") AS v(name, location);",
    ]


def render_orders(orders: Sequence[Dict[str, Any]]) -> List[str]:
    rows = [
        [
            sql_str(o["order_no"]),
            sql_str(o["customer_name"]),
            cents(o["total_c"]),
            sql_str(o["status"]),
            sql_str(o["payment_status"]),
            ts(o["days"], o["minutes"]),
        ]
        for o in orders
    ]
    return [
        "",
        f"-- ── 2.6 订单（{len(orders)} 条；最近 180 天；status 五种齐全、completed 约 55%）──",
        "-- total_amount 由生成器按 order_items 的 price*quantity 精确求和写死",
        "INSERT INTO \"order\".orders",
        "  (order_no, customer_id, total_amount, status, payment_status, created_at)",
        "SELECT v.order_no, c.id, v.total_amount, v.status, v.payment_status, v.created_at",
        "FROM (VALUES",
        values_rows(rows),
        ") AS v(order_no, customer_name, total_amount, status, payment_status, created_at)",
        f"JOIN customer.customers c ON c.name = v.customer_name AND c.name LIKE '{CUSTOMER_LIKE}';",
    ]


def render_order_items(orders: Sequence[Dict[str, Any]]) -> List[str]:
    rows: List[List[str]] = []
    for order in orders:
        for item in order["items"]:
            rows.append(
                [
                    sql_str(order["order_no"]),
                    sql_str(item["sku"]),
                    str(item["quantity"]),
                    cents(item["price_c"]),
                    cents(item["cost_c"]),
                ]
            )
    return [
        "",
        f"-- ── 2.7 订单明细（{len(rows)} 行；每单 1~4 个商品）──",
        "INSERT INTO \"order\".order_items (order_id, product_id, quantity, price, cost)",
        "SELECT o.id, p.id, v.quantity, v.price, v.cost",
        "FROM (VALUES",
        values_rows(rows),
        ") AS v(order_no, sku, quantity, price, cost)",
        f"JOIN \"order\".orders o ON o.order_no = v.order_no AND o.order_no LIKE '{ORDER_LIKE}'",
        f"JOIN product.products p ON p.sku = v.sku AND p.sku LIKE '{SKU_LIKE}';",
    ]


def render_refunds(refunds: Sequence[Dict[str, Any]]) -> List[str]:
    rows = [
        [
            sql_str(r["order_no"]),
            sql_str(r["sku"]),
            cents(r["refund_c"]),
            sql_str(r["reason"]),
            ts(r["days"], r["minutes"]),
        ]
        for r in refunds
    ]
    return [
        "",
        f"-- ── 2.8 退款（{len(refunds)} 条；只挂 completed/cancelled，金额 <= 订单金额）──",
        "INSERT INTO \"order\".refunds (order_id, product_id, refund_amount, reason, created_at)",
        "SELECT o.id, p.id, v.refund_amount, v.reason, v.created_at",
        "FROM (VALUES",
        values_rows(rows),
        ") AS v(order_no, sku, refund_amount, reason, created_at)",
        f"JOIN \"order\".orders o ON o.order_no = v.order_no AND o.order_no LIKE '{ORDER_LIKE}'",
        f"JOIN product.products p ON p.sku = v.sku AND p.sku LIKE '{SKU_LIKE}';",
    ]


def render_inventory(rows: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [
            sql_str(r["sku"]),
            sql_str(r["warehouse"]),
            str(r["stock"]),
            str(r["safety"]),
            ts(r["updated_days"], r["updated_minutes"]),
        ]
        for r in rows
    ]
    return [
        "",
        f"-- ── 2.9 多仓库存（{len(rows)} 行 = 40 商品 × 3 仓；其中 {N_LOW_STOCK} 行低于安全库存）──",
        "INSERT INTO inventory.inventory",
        "  (product_id, warehouse_id, stock_quantity, safety_stock, updated_at)",
        "SELECT p.id, w.id, v.stock_quantity, v.safety_stock, v.updated_at",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(sku, warehouse_name, stock_quantity, safety_stock, updated_at)",
        f"JOIN product.products p ON p.sku = v.sku AND p.sku LIKE '{SKU_LIKE}'",
        f"JOIN inventory.warehouses w ON w.name = v.warehouse_name AND w.name LIKE '{WAREHOUSE_LIKE}';",
    ]


def render_purchase_orders(rows: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [
            str(r["supplier_id"]),
            sql_str(r["sku"]),
            str(r["quantity"]),
            sql_str(r["status"]),
            ts(r["days"], r["minutes"]),
        ]
        for r in rows
    ]
    return [
        "",
        f"-- ── 2.10 采购订单（{len(rows)} 条；pending/arrived/cancelled 全覆盖）──",
        "INSERT INTO inventory.purchase_orders",
        "  (supplier_id, product_id, quantity, status, created_at)",
        "SELECT v.supplier_id, p.id, v.quantity, v.status, v.created_at",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(supplier_id, sku, quantity, status, created_at)",
        f"JOIN product.products p ON p.sku = v.sku AND p.sku LIKE '{SKU_LIKE}';",
    ]


def render_competitor_products(rows: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [
            sql_str(r["platform"]),
            sql_str(r["brand"]),
            sql_str(r["product_name"]),
            sql_str(r["category"]),
            sql_str(r["url"]),
        ]
        for r in rows
    ]
    return [
        "",
        f"-- ── 2.11 竞品商品（{len(rows)} 条；Amazon/TikTok Shop/淘宝）──",
        "INSERT INTO crawler.competitor_products (platform, brand, product_name, category, url)",
        "SELECT v.platform, v.brand, v.product_name, v.category, v.url",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(platform, brand, product_name, category, url);",
    ]


def render_competitor_price(rows: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [sql_str(r["url"]), cents(r["price_c"]), f"{r['discount']:.2f}", ts(r["days"], r["minutes"])]
        for r in rows
    ]
    return [
        "",
        f"-- ── 2.12 竞品价格时序（{len(rows)} 行 = {N_COMPETITOR_PRODUCTS} 商品 × "
        f"{N_COMPETITOR_PRICE_POINTS} 个时间点；discount 0~0.5）──",
        "INSERT INTO crawler.competitor_price (product_id, price, discount, crawl_time)",
        "SELECT cp.id, v.price, v.discount, v.crawl_time",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(url, price, discount, crawl_time)",
        "JOIN crawler.competitor_products cp ON cp.url = v.url"
        f" AND cp.brand LIKE '{COMPETITOR_BRAND_LIKE}';",
    ]


def render_reviews(rows: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [
            sql_str(r["sku"]),
            str(r["rating"]),
            sql_str(r["review_text"]),
            sql_str(r["sentiment"]),
            ts(r["days"], r["minutes"]),
        ]
        for r in rows
    ]
    return [
        "",
        f"-- ── 2.13 商品评论（{len(rows)} 条；rating 1~5，sentiment 与 rating 自洽）──",
        "INSERT INTO crawler.product_reviews (product_id, rating, review_text, sentiment, created_at)",
        "SELECT p.id, v.rating, v.review_text, v.sentiment, v.created_at",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(sku, rating, review_text, sentiment, created_at)",
        f"JOIN product.products p ON p.sku = v.sku AND p.sku LIKE '{SKU_LIKE}';",
    ]


def render_behavior(rows: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [
            sql_str(r["customer_name"]),
            sql_str(r["event_type"]),
            sql_str(r["sku"]),
            ts(r["days"], r["minutes"]),
        ]
        for r in rows
    ]
    return [
        "",
        f"-- ── 2.14 用户行为流水（{len(rows)} 条；四种 event_type 齐全，客户/商品均指向本种子）──",
        "INSERT INTO customer.customer_behavior (customer_id, event_type, product_id, created_at)",
        "SELECT c.id, v.event_type, p.id, v.created_at",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(customer_name, event_type, sku, created_at)",
        f"JOIN customer.customers c ON c.name = v.customer_name AND c.name LIKE '{CUSTOMER_LIKE}'",
        f"JOIN product.products p ON p.sku = v.sku AND p.sku LIKE '{SKU_LIKE}';",
    ]


def render_expenses(rows: Sequence[Dict[str, Any]]) -> List[str]:
    values = [[sql_str(r["type"]), cents(r["amount_c"]), date_expr(r["days"])] for r in rows]
    return [
        "",
        f"-- ── 2.15 运营支出（{len(rows)} 条；广告费/物流费/人工，最近 90 天）──",
        "-- 不参与删除：逐行 WHERE NOT EXISTS(date,type,amount) 保证可重跑不重复",
        "INSERT INTO finance.expenses (type, amount, date)",
        "SELECT v.type, v.amount, v.spend_date",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(type, amount, spend_date)",
        "WHERE NOT EXISTS (",
        "  SELECT 1 FROM finance.expenses e",
        "   WHERE e.date = v.spend_date AND e.type = v.type AND e.amount = v.amount",
        ");",
    ]


def render_daily_profit() -> List[str]:
    return [
        "",
        "-- ── 2.16 每日利润（不参与删除；从真实订单派生，永远与订单一致）──",
        "-- revenue = 当日 completed 订单 total_amount 之和",
        "-- cost    = 当日 completed 订单明细 cost*quantity 之和",
        "INSERT INTO finance.daily_profit (date, revenue, cost, profit)",
        "SELECT r.dt, r.revenue, COALESCE(c.cost, 0), r.revenue - COALESCE(c.cost, 0)",
        "FROM (",
        "  SELECT o.created_at::date AS dt, SUM(o.total_amount) AS revenue",
        "    FROM \"order\".orders o",
        "   WHERE o.status = 'completed'",
        "   GROUP BY 1",
        ") r",
        "LEFT JOIN (",
        "  SELECT o.created_at::date AS dt, SUM(oi.cost * oi.quantity) AS cost",
        "    FROM \"order\".orders o",
        "    JOIN \"order\".order_items oi ON oi.order_id = o.id",
        "   WHERE o.status = 'completed'",
        "   GROUP BY 1",
        ") c ON c.dt = r.dt",
        "ON CONFLICT (date) DO UPDATE",
        "SET revenue = EXCLUDED.revenue, cost = EXCLUDED.cost, profit = EXCLUDED.profit;",
    ]


def render_tasks(tasks: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [
            sql_str(t["session_id"]),
            sql_str(t["user_query"]),
            sql_str(t["task_type"]),
            sql_str(t["status"]),
            ts(t["days"], t["minutes"]),
        ]
        for t in tasks
    ]
    return [
        "",
        f"-- ── 2.17 Agent 任务（{len(tasks)} 条；session_id 前缀 {TASK_PREFIX} 为本种子标记）──",
        "-- 本表历史上曾因序列滞后需要 ON CONFLICT(id) 重试；第 0 节序列对齐后已无此需要，",
        "-- 与其它表完全一致：只用 SERIAL 默认值，不显式指定 id。",
        "INSERT INTO ai.agent_tasks (session_id, user_query, task_type, status, created_at)",
        "SELECT v.session_id, v.user_query, v.task_type, v.status, v.created_at",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(session_id, user_query, task_type, status, created_at);",
    ]


def render_traces(traces: Sequence[Dict[str, Any]]) -> List[str]:
    values = [
        [
            sql_str(t["session_id"]),
            sql_str(t["node"]),
            sql_jsonb(t["input"]),
            sql_jsonb(t["output"]),
            f"{t['duration']:.3f}",
            ts(t["days"], t["minutes"]),
        ]
        for t in traces
    ]
    return [
        "",
        f"-- ── 2.18 Agent Trace（{len(traces)} 条 = {N_TASKS} 任务 × 4 节点）──",
        "INSERT INTO ai.agent_trace (task_id, node, input, output, duration, created_at)",
        "SELECT t.id, v.node, v.input, v.output, v.duration, v.created_at",
        "FROM (VALUES",
        values_rows(values),
        ") AS v(session_id, node, input, output, duration, created_at)",
        f"JOIN ai.agent_tasks t ON t.session_id = v.session_id AND t.session_id LIKE '{TASK_LIKE}';",
    ]


def render_footer() -> List[str]:
    return [
        "",
        "-- ═══ 3. 收尾 ═══",
        "COMMIT;",
        "",
        "-- 应用完成后的抽查（可选）：",
        "--   select count(*) from \"order\".orders where order_no like 'SQDEMO-%';",
        "--   select count(*) from inventory.inventory where stock_quantity < safety_stock;",
    ]


# ═══════════════════════════════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════════════════════════════

def self_check(facts: Dict[str, Any]) -> None:
    expected = {
        "categories": N_CATEGORIES,
        "products": N_PRODUCTS,
        "tags": N_TAGS,
        "customers": N_CUSTOMERS,
        "orders": N_ORDERS,
        "refunds": N_REFUNDS,
        "warehouses": N_WAREHOUSES,
        "inventory": N_INVENTORY,
        "low_stock": N_LOW_STOCK,
        "purchase_orders": N_PURCHASE_ORDERS,
        "competitor_products": N_COMPETITOR_PRODUCTS,
        "competitor_price": N_COMPETITOR_PRODUCTS * N_COMPETITOR_PRICE_POINTS,
        "reviews": N_REVIEWS,
        "behavior": N_BEHAVIOR,
        "expenses": N_EXPENSES,
        "tasks": N_TASKS,
        "traces": N_TRACES,
    }
    for key, want in expected.items():
        got = facts[key]
        if got != want:
            raise SystemExit(f"[self-check] {key}: 期望 {want}，实际 {got}")
    if not 800 <= facts["order_items"] <= 900:
        raise SystemExit(f"[self-check] order_items={facts['order_items']} 不在 800~900")

    orders = facts["orders_data"]
    for order in orders:
        line_sum = sum(i["price_c"] * i["quantity"] for i in order["items"])
        if line_sum != order["total_c"]:
            raise SystemExit(f"[self-check] {order['order_no']} 金额 != 明细之和")
    total_by_no = {o["order_no"]: o["total_c"] for o in orders}
    for refund in facts["refunds_data"]:
        if refund["refund_c"] > total_by_no[refund["order_no"]]:
            raise SystemExit(f"[self-check] {refund['order_no']} 退款金额超过订单金额")
        if refund["days"] > next(o["days"] for o in orders if o["order_no"] == refund["order_no"]):
            raise SystemExit(f"[self-check] {refund['order_no']} 退款时间早于下单时间")
    for product in facts["products_data"]:
        if product["sale_c"] <= product["cost_c"]:
            raise SystemExit(f"[self-check] {product['sku']} sale_price <= cost_price")
    statuses = {o["status"] for o in orders}
    if statuses != {"pending", "paid", "shipped", "completed", "cancelled"}:
        raise SystemExit(f"[self-check] 订单状态覆盖不全: {sorted(statuses)}")
    completed = sum(1 for o in orders if o["status"] == "completed")
    if completed != 220:
        raise SystemExit(f"[self-check] completed={completed} != 220")


REQUIRED_DELETE_STATEMENTS = (
    "DELETE FROM product.product_tags",
    "DELETE FROM crawler.product_reviews",
    "DELETE FROM inventory.inventory",
    "DELETE FROM inventory.purchase_orders",
    "DELETE FROM customer.customer_behavior",
    'DELETE FROM "order".order_items',
    'DELETE FROM "order".refunds',
    "DELETE FROM crawler.competitor_price",
    "DELETE FROM ai.agent_trace",
    'DELETE FROM "order".orders',
    "DELETE FROM customer.customers",
    "DELETE FROM ai.agent_tasks",
    "DELETE FROM product.products",
    "DELETE FROM crawler.competitor_products",
    "DELETE FROM product.categories",   # 自引用树：二级、一级各一条
    "DELETE FROM product.categories",
    "DELETE FROM inventory.warehouses",
)


def self_check_cleanup(sql: str) -> None:
    """守卫：7 个父锚点 + 9 张子表的清理语句一个都不能漏（漏了必然破坏幂等）。"""
    for statement in set(REQUIRED_DELETE_STATEMENTS):
        if statement not in sql:
            raise SystemExit(f"[self-check] 缺少清理语句: {statement}")
    if sql.count("DELETE FROM") != len(REQUIRED_DELETE_STATEMENTS):
        raise SystemExit(
            f"[self-check] DELETE 语句数 {sql.count('DELETE FROM')} "
            f"!= 期望 {len(REQUIRED_DELETE_STATEMENTS)}"
        )
    if re.search(r"(?im)^\s*TRUNCATE\b", sql):
        raise SystemExit("[self-check] 产物出现 TRUNCATE（禁止）")
    # 序列对齐段必须覆盖全部 SERIAL 表，且 setval 只允许出现在该段
    if sql.count("pg_get_serial_sequence") != len(SERIAL_TABLES):
        raise SystemExit(
            f"[self-check] 序列对齐段覆盖 {sql.count('pg_get_serial_sequence')} 张表 "
            f"!= 期望 {len(SERIAL_TABLES)}"
        )
    if sql.count("setval(") != len(SERIAL_TABLES):
        raise SystemExit(
            f"[self-check] setval( 出现 {sql.count('setval(')} 次 != 期望 {len(SERIAL_TABLES)}"
        )
    if "DO $seed$" in sql or "ON CONFLICT (id)" in sql:
        raise SystemExit("[self-check] 产物仍残留 ai.agent_tasks 的 id 冲突重试兜底")


# ═══════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════

def build_sql() -> str:
    rng = random.Random(SEED)

    categories = build_categories()
    products = build_products(rng)
    tags = build_tags(rng, products)
    customers = build_customers(rng)
    orders, refunds = build_orders(rng, products, customers)
    inventory = build_inventory(rng, products)
    purchase_orders = build_purchase_orders(rng, products)
    competitor_products = build_competitor_products()
    competitor_price = build_competitor_price(rng, competitor_products)
    reviews = build_reviews(rng, products)
    behavior = build_behavior(rng, products, customers)
    expenses = build_expenses(rng)
    tasks, traces = build_tasks_and_traces(rng)

    self_check(
        {
            "categories": len(categories),
            "products": len(products),
            "tags": len(tags),
            "customers": len(customers),
            "orders": len(orders),
            "order_items": sum(len(o["items"]) for o in orders),
            "refunds": len(refunds),
            "warehouses": len(WAREHOUSES),
            "inventory": len(inventory),
            "low_stock": sum(1 for r in inventory if r["stock"] < r["safety"]),
            "purchase_orders": len(purchase_orders),
            "competitor_products": len(competitor_products),
            "competitor_price": len(competitor_price),
            "reviews": len(reviews),
            "behavior": len(behavior),
            "expenses": len(expenses),
            "tasks": len(tasks),
            "traces": len(traces),
            "orders_data": orders,
            "refunds_data": refunds,
            "products_data": products,
        }
    )

    lines: List[str] = [HEADER.rstrip("\n")]
    lines += render_sequence_alignment()
    lines += render_cleanup()
    lines += render_categories(categories)
    lines += render_products(products)
    lines += render_tags(tags)
    lines += render_customers(customers)
    lines += render_warehouses()
    lines += render_orders(orders)
    lines += render_order_items(orders)
    lines += render_refunds(refunds)
    lines += render_inventory(inventory)
    lines += render_purchase_orders(purchase_orders)
    lines += render_competitor_products(competitor_products)
    lines += render_competitor_price(competitor_price)
    lines += render_reviews(reviews)
    lines += render_behavior(behavior)
    lines += render_expenses(expenses)
    lines += render_daily_profit()
    lines += render_tasks(tasks)
    lines += render_traces(traces)
    lines += render_footer()
    sql = "\n".join(lines) + "\n"
    self_check_cleanup(sql)
    return sql


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 SQL Agent 演示数据种子 SQL（确定性）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出文件路径")
    parser.add_argument("--stdout", action="store_true", help="只打印到 stdout，不落盘")
    args = parser.parse_args(argv)

    sql = build_sql()
    if args.stdout:
        sys.stdout.write(sql)
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql, encoding="utf-8", newline="\n")
    size = out.stat().st_size
    print(
        f"[gen_sql_agent_demo_seed] seed={SEED} -> {out} "
        f"({sql.count(chr(10))} 行 / {size} 字节)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
