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

REPORT_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ═══════ 销售日报 ═══════
    "daily_sales": {
        "name": "销售日报",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    DATE(o.placed_at) AS 日期,
                    c.code AS 渠道,
                    COUNT(DISTINCT o.order_id) AS 订单数,
                    SUM(o.order_total) AS 销售额,
                    COUNT(DISTINCT o.customer_id) AS 下单客户数,
                    ROUND(AVG(o.order_total), 2) AS 客单价
                FROM orders o
                LEFT JOIN channels c ON c.channel_id = o.channel_id
                WHERE o.placed_at >= CURRENT_DATE - INTERVAL '7 days'
                  AND o.status NOT IN ('cancelled')
                GROUP BY DATE(o.placed_at), c.code
                ORDER BY DATE(o.placed_at) DESC, 销售额 DESC
            """,
        },
        "templates": ["daily_sales.j2"],
        "charts": [
            {"type": "line", "x": "日期", "y": "销售额", "title": "近7日销售额趋势"},
            {"type": "bar", "x": "渠道", "y": "订单数", "title": "各渠道订单数对比"},
        ],
    },
    # ═══════ 商品动销分析 ═══════
    "product_performance": {
        "name": "商品动销分析报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    p.name AS 产品名称,
                    b.name AS 品牌,
                    COUNT(DISTINCT oi.order_id) AS 销售订单数,
                    SUM(oi.quantity) AS 销售数量,
                    SUM(oi.line_total) AS 销售额,
                    ROUND(AVG(s.cost_price * oi.quantity), 2) AS 成本合计,
                    ROUND(SUM(oi.line_total) - SUM(s.cost_price * oi.quantity), 2) AS 毛利,
                    ROUND(
                        (SUM(oi.line_total) - SUM(s.cost_price * oi.quantity))
                        / NULLIF(SUM(oi.line_total), 0) * 100, 1
                    ) AS 毛利率
                FROM order_items oi
                JOIN skus s ON s.sku_id = oi.sku_id
                JOIN products p ON p.product_id = s.product_id
                LEFT JOIN brands b ON b.brand_id = p.brand_id
                JOIN orders o ON o.order_id = oi.order_id
                WHERE o.placed_at >= CURRENT_DATE - INTERVAL '30 days'
                  AND o.status NOT IN ('cancelled', 'refunded')
                GROUP BY p.name, b.name
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
                    w.type AS 仓库类型,
                    p.name AS 产品名称,
                    s.sku_id AS SKU编码,
                    il.qty_on_hand AS 现有库存,
                    il.qty_reserved AS 已预留,
                    il.qty_in_transit AS 在途库存,
                    (il.qty_on_hand - il.qty_reserved) AS 可用库存,
                    CASE
                        WHEN (il.qty_on_hand - il.qty_reserved) <= 0 THEN '缺货'
                        WHEN (il.qty_on_hand - il.qty_reserved) < 10 THEN '低库存'
                        WHEN (il.qty_on_hand - il.qty_reserved) > 100 THEN '积压'
                        ELSE '正常'
                    END AS 库存状态
                FROM inventory_levels il
                JOIN skus s ON s.sku_id = il.sku_id
                JOIN products p ON p.product_id = s.product_id
                JOIN warehouses w ON w.warehouse_id = il.warehouse_id
                WHERE w.is_active = TRUE
                ORDER BY 可用库存 ASC
                LIMIT 100
            """,
        },
        "templates": ["inventory_health.j2"],
        "charts": [
            {"type": "pie", "x": "库存状态", "y": None, "title": "库存状态分布"},
            {"type": "bar", "x": "仓库", "y": "可用库存", "title": "各仓库可用库存"},
        ],
    },
    # ═══════ 广告效果分析 ═══════
    "ad_performance": {
        "name": "广告效果分析报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    c.channel AS 广告平台,
                    c.name AS 活动名称,
                    c.type AS 活动类型,
                    c.status AS 状态,
                    SUM(sr.spend) AS 总花费,
                    SUM(sr.impressions) AS 总展示,
                    SUM(sr.clicks) AS 总点击,
                    SUM(sr.conversions) AS 总转化,
                    SUM(sr.sales) AS 广告销售额,
                    ROUND(SUM(sr.clicks)::NUMERIC / NULLIF(SUM(sr.impressions), 0) * 100, 2) AS CTR,
                    ROUND(SUM(sr.spend)::NUMERIC / NULLIF(SUM(sr.clicks), 0), 2) AS CPC,
                    ROUND(SUM(sr.spend)::NUMERIC / NULLIF(SUM(sr.sales), 0) * 100, 2) AS ACoS,
                    ROUND(SUM(sr.sales)::NUMERIC / NULLIF(SUM(sr.spend), 0), 2) AS ROAS
                FROM campaigns c
                JOIN spend_records sr ON sr.campaign_id = c.campaign_id
                WHERE sr.date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY c.channel, c.name, c.type, c.status
                ORDER BY 总花费 DESC
            """,
        },
        "templates": ["ad_performance.j2"],
        "charts": [
            {"type": "bar", "x": "活动名称", "y": "ROAS", "title": "各活动 ROAS 对比"},
            {"type": "bar", "x": "活动名称", "y": "ACoS", "title": "各活动 ACoS 对比"},
        ],
    },
    # ═══════ 订单履约报告 ═══════
    "order_fulfillment": {
        "name": "订单履约报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    c.code AS 渠道,
                    o.status AS 订单状态,
                    COUNT(*) AS 订单数,
                    SUM(o.order_total) AS 金额合计,
                    ROUND(AVG(
                        EXTRACT(EPOCH FROM (o.shipped_at - o.placed_at)) / 3600
                    ), 1) AS 平均发货耗时小时,
                    ROUND(AVG(
                        EXTRACT(EPOCH FROM (o.delivered_at - o.placed_at)) / 3600
                    ), 1) AS 平均签收耗时小时,
                    COUNT(CASE WHEN o.status = 'refunded' THEN 1 END) AS 退款订单数,
                    ROUND(
                        COUNT(CASE WHEN o.status = 'refunded' THEN 1 END)::NUMERIC
                        / NULLIF(COUNT(*), 0) * 100, 2
                    ) AS 退款率
                FROM orders o
                LEFT JOIN channels c ON c.channel_id = o.channel_id
                WHERE o.placed_at >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY c.code, o.status
                ORDER BY c.code, 订单数 DESC
            """,
        },
        "templates": ["order_fulfillment.j2"],
        "charts": [
            {"type": "pie", "x": "订单状态", "y": "订单数", "title": "订单状态分布"},
            {"type": "bar", "x": "渠道", "y": "平均签收耗时小时", "title": "各渠道平均签收耗时"},
        ],
    },
    # ═══════ 客户分析 ═══════
    "customer_analysis": {
        "name": "客户分析报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    cus.country AS 国家,
                    cus.segment AS 客户分层,
                    COUNT(DISTINCT cus.customer_id) AS 客户数,
                    ROUND(AVG(cus.lifetime_value), 2) AS 平均LTV,
                    ROUND(AVG(cus.order_count), 1) AS 平均订单数,
                    COUNT(CASE WHEN cus.last_order_at >= CURRENT_DATE - INTERVAL '30 days'
                          THEN 1 END) AS 近30天活跃,
                    ROUND(
                        COUNT(CASE WHEN cus.last_order_at >= CURRENT_DATE - INTERVAL '30 days'
                          THEN 1 END)::NUMERIC / NULLIF(COUNT(*), 0) * 100, 2
                    ) AS 活跃率,
                    SUM(cus.order_count) AS 累计订单总数
                FROM customers cus
                GROUP BY cus.country, cus.segment
                ORDER BY 客户数 DESC
            """,
        },
        "templates": ["customer_analysis.j2"],
        "charts": [
            {"type": "pie", "x": "客户分层", "y": "客户数", "title": "客户分层占比"},
            {"type": "bar", "x": "国家", "y": "平均LTV", "title": "各国平均 LTV"},
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
        from backend.config import DB_CONFIG
        self.db_config = db_config or dict(DB_CONFIG)

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
                    pass


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
