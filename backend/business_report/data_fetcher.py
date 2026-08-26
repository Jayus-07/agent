"""
data_fetcher.py — 数据获取层

从 SQL 或 API 拉取结构化数据，统一输出为 {data, metadata} JSON 格式。

报告类型注册表 REPORT_REGISTRY 是数据中心，定义每种报告的数据来源。
类似 schema_config.py 的设计：集中配置，运行时加载。
"""

import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod

import psycopg2
import psycopg2.extras
import requests

from backend.shared.logger import logger


# =====================================================
# 报告类型注册表
# =====================================================
# 每种报告类型定义：
#   name:      报告中文名
#   source:    数据来源 {"type": "sql"|"api", ...}
#   templates: 可选模板列表（第一个为默认）
#   charts:    自动图表配置 [{"type": "bar"|"pie"|"line", "x": ..., "y": ..., "title": ...}]

# fix f15：原 SQL 基于理想 schema（orders/channels/skus/campaigns…）编写，
# 与真实业务库（agent_business）不匹配 → "关系 orders 不存在"，日报生成
# 3 次重试全败。以下 SQL 全部对齐真实 schema：order.*/inventory.*/product.*/
# customer.*/finance.*/crawler.*（schema-qualified，列名保留模板期望的中文别名，
# 模板引擎缺列时自动降级 fallback 表格，不会报错）。

REPORT_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ═══════ 销售日报 ═══════
    "daily_sales": {
        "name": "销售日报",
        "source": {
            "type": "sql",
            # 窗口基于最新订单时间滚动（而非 CURRENT_DATE）：
            # demo 数据时间戳固定，固定窗口必空；生产环境效果等同近7日。
            "sql": """
                WITH bounds AS (
                    SELECT MAX(created_at) AS latest FROM "order"."orders"
                )
                SELECT
                    DATE(o.created_at) AS 日期,
                    '线上' AS 渠道,
                    COUNT(DISTINCT o.id) AS 订单数,
                    SUM(o.total_amount) AS 销售额,
                    COUNT(DISTINCT o.customer_id) AS 下单客户数,
                    ROUND(AVG(o.total_amount), 2) AS 客单价
                FROM "order"."orders" o, bounds b
                WHERE o.created_at >= b.latest - INTERVAL '7 days'
                  AND COALESCE(o.status, '') <> 'cancelled'
                GROUP BY DATE(o.created_at)
                ORDER BY DATE(o.created_at) DESC
            """,
        },
        "templates": ["daily_sales.j2"],
        "charts": [
            {"type": "line", "x": "日期", "y": "销售额", "title": "近7日销售额趋势"},
        ],
    },
    # ═══════ 商品动销分析 ═══════
    "product_performance": {
        "name": "商品动销分析报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    p.product_name AS 产品名称,
                    p.brand AS 品牌,
                    COUNT(DISTINCT oi.order_id) AS 销售订单数,
                    SUM(oi.quantity) AS 销售数量,
                    SUM(oi.price) AS 销售额,
                    ROUND(SUM(oi.price - oi.cost * oi.quantity), 2) AS 毛利,
                    ROUND(
                        SUM(oi.price - oi.cost * oi.quantity)
                        / NULLIF(SUM(oi.price), 0) * 100, 1
                    ) AS 毛利率
                FROM "order"."order_items" oi
                JOIN product.products p ON p.id = oi.product_id
                GROUP BY p.product_name, p.brand
                ORDER BY 销售额 DESC
                LIMIT 50
            """,
        },
        "templates": ["product_performance.j2"],
        "charts": [
            {"type": "bar", "x": "产品名称", "y": "销售额", "title": "Top 50 产品销售额"},
            {"type": "bar", "x": "产品名称", "y": "毛利率", "title": "毛利率对比"},
        ],
    },
    # ═══════ 库存健康报告 ═══════
    "inventory_health": {
        "name": "库存健康报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    w.name AS 仓库,
                    p.product_name AS 产品名称,
                    p.sku AS SKU编码,
                    i.stock_quantity AS 现有库存,
                    i.safety_stock AS 安全库存,
                    CASE
                        WHEN i.stock_quantity <= 0 THEN '缺货'
                        WHEN i.stock_quantity < i.safety_stock THEN '低库存'
                        WHEN i.stock_quantity > i.safety_stock * 3 THEN '积压'
                        ELSE '正常'
                    END AS 库存状态
                FROM inventory.inventory i
                JOIN inventory.warehouses w ON w.id = i.warehouse_id
                JOIN product.products p ON p.id = i.product_id
                ORDER BY 现有库存 ASC
                LIMIT 100
            """,
        },
        "templates": ["inventory_health.j2"],
        "charts": [
            {"type": "pie", "x": "库存状态", "y": None, "title": "库存状态分布"},
            {"type": "bar", "x": "仓库", "y": "现有库存", "title": "各仓库库存量"},
        ],
    },
    # ═══════ 竞品价格监控（demo 库无广告投放数据，改用 crawler 域） ═══════
    "ad_performance": {
        "name": "竞品价格监控报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    cp.platform AS 平台,
                    cp.brand AS 品牌,
                    cp.product_name AS 商品名称,
                    cp.category AS 品类,
                    MIN(price.price) AS 最低价,
                    ROUND(AVG(price.price), 2) AS 均价,
                    MAX(price.crawl_time) AS 最近抓取时间
                FROM crawler.competitor_products cp
                JOIN crawler.competitor_price price ON price.product_id = cp.id
                WHERE price.crawl_time >= CURRENT_TIMESTAMP - INTERVAL '30 days'
                GROUP BY cp.platform, cp.brand, cp.product_name, cp.category
                ORDER BY 均价 DESC
                LIMIT 50
            """,
        },
        "templates": ["ad_performance.j2"],
        "charts": [
            {"type": "bar", "x": "商品名称", "y": "均价", "title": "竞品均价对比"},
        ],
    },
    # ═══════ 订单履约报告 ═══════
    "order_fulfillment": {
        "name": "订单履约报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    '线上' AS 渠道,
                    o.status AS 订单状态,
                    COUNT(DISTINCT o.id) AS 订单数,
                    SUM(o.total_amount) AS 金额合计,
                    COUNT(DISTINCT r.order_id) AS 退款订单数,
                    ROUND(
                        COUNT(DISTINCT r.order_id)::NUMERIC
                        / NULLIF(COUNT(DISTINCT o.id), 0) * 100, 2
                    ) AS 退款率
                FROM "order"."orders" o
                LEFT JOIN "order"."refunds" r ON r.order_id = o.id
                WHERE o.created_at >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY o.status
                ORDER BY 订单数 DESC
            """,
        },
        "templates": ["order_fulfillment.j2"],
        "charts": [
            {"type": "pie", "x": "订单状态", "y": "订单数", "title": "订单状态分布"},
        ],
    },
    # ═══════ 客户分析 ═══════
    "customer_analysis": {
        "name": "客户分析报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    cus.level AS 客户分层,
                    cus.gender AS 性别,
                    COUNT(*) AS 客户数,
                    COUNT(CASE WHEN cus.register_time >= CURRENT_DATE - INTERVAL '30 days'
                          THEN 1 END) AS 近30天新增
                FROM customer.customers cus
                GROUP BY cus.level, cus.gender
                ORDER BY 客户数 DESC
            """,
        },
        "templates": ["customer_analysis.j2"],
        "charts": [
            {"type": "pie", "x": "客户分层", "y": "客户数", "title": "客户分层占比"},
        ],
    },
}


def register_report_type(report_type: str, config: dict):
    """动态注册新的报告类型"""
    REPORT_REGISTRY[report_type] = config
    logger.info(f"[DataFetcher] 注册报告类型: {report_type}")


# =====================================================
# 抽象基类
# =====================================================

class DataFetcher(ABC):
    """数据获取器基类"""

    @abstractmethod
    def fetch(self, config: dict, filters: dict = None) -> Dict[str, Any]:
        """
        获取结构化数据。

        返回:
            {"data": [...], "metadata": {...}}
        """
        pass


# =====================================================
# SQL 数据获取器
# =====================================================

class SQLFetcher(DataFetcher):
    """从 PostgreSQL 获取数据"""

    def __init__(self, db_config: dict = None):
        from backend.config import BUSINESS_DB_CONFIG
        self.db_config = db_config or dict(BUSINESS_DB_CONFIG)

    def fetch(self, config: dict, filters: dict = None) -> Dict[str, Any]:
        """
        执行 SQL 查询，返回结构化 JSON。

        参数:
            config:  源配置，含 "sql" 字段
            filters: 筛选条件，映射为 SQL 占位符 %(key)s 的值

        返回:
            {"data": [...], "metadata": {...}}
        """
        filters = filters or {}
        sql = config["sql"]
        start_time = time.time()

        conn = None
        try:
            conn = psycopg2.connect(**self.db_config)
            conn.set_session(readonly=True, autocommit=True)

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 只注入 SQL 中实际用到的占位符，避免 psycopg2 报参数多余
                import re
                used_params = set(re.findall(r"%\((\w+)\)s", sql))
                params = {k: v for k, v in filters.items() if k in used_params}

                cur.execute(sql, params)
                rows = cur.fetchall()
                data = [dict(r) for r in rows]

            elapsed = time.time() - start_time
            logger.info(f"[SQLFetcher] 查询完成: {len(data)} 行, 耗时 {elapsed:.2f}s")

            return {
                "data": data,
                "metadata": {
                    "source": "SQL",
                    "row_count": len(data),
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_ms": int(elapsed * 1000),
                    "filters": filters,
                },
            }

        except psycopg2.OperationalError as e:
            logger.error(f"[SQLFetcher] 数据库连接失败: {e}")
            raise
        except Exception as e:
            logger.error(f"[SQLFetcher] 查询失败: {e}")
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    logger.debug("[P1-10] 连接关闭失败", exc_info=True)


# =====================================================
# API 数据获取器
# =====================================================

class APIFetcher(DataFetcher):
    """从 HTTP API 获取数据"""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def fetch(self, config: dict, filters: dict = None) -> Dict[str, Any]:
        """
        调用 HTTP API，返回结构化 JSON。

        参数:
            config:  源配置，含 "url" 字段，可选 "headers", "method"
            filters: 查询参数，拼接到 query string

        返回:
            {"data": [...], "metadata": {...}}
        """
        filters = filters or {}
        url = config["url"]
        headers = config.get("headers", {})
        method = config.get("method", "GET").upper()
        start_time = time.time()

        try:
            if method == "GET":
                resp = requests.get(url, params=filters, headers=headers,
                                    timeout=self.timeout)
            elif method == "POST":
                resp = requests.post(url, json=filters, headers=headers,
                                     timeout=self.timeout)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")

            resp.raise_for_status()
            body = resp.json()

            elapsed = time.time() - start_time

            # 兼容两种返回格式：{"data": [...]} 或裸数组 [...]
            if isinstance(body, list):
                data = body
            elif isinstance(body, dict) and "data" in body:
                data = body["data"]
            else:
                data = [body]

            logger.info(f"[APIFetcher] API 调用完成: {len(data)} 条, 耗时 {elapsed:.2f}s")

            return {
                "data": data,
                "metadata": {
                    "source": f"API:{url}",
                    "row_count": len(data),
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_ms": int(elapsed * 1000),
                    "filters": filters,
                },
            }

        except requests.Timeout:
            logger.error(f"[APIFetcher] API 超时 ({self.timeout}s): {url}")
            raise
        except requests.RequestException as e:
            logger.error(f"[APIFetcher] API 请求失败: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"[APIFetcher] JSON 解析失败: {e}")
            raise


# =====================================================
# 工厂函数
# =====================================================

def get_fetcher(source_config: dict, db_config: dict = None) -> DataFetcher:
    """根据源配置创建合适的 DataFetcher"""
    source_type = source_config.get("type", "sql")
    if source_type == "sql":
        return SQLFetcher(db_config=db_config)
    elif source_type == "api":
        return APIFetcher()
    else:
        raise ValueError(f"不支持的数据源类型: {source_type}")
