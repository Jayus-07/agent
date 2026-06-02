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

from utils.logger import logger


# =====================================================
# 报告类型注册表
# =====================================================
# 每种报告类型定义：
#   name:      报告中文名
#   source:    数据来源 {"type": "sql"|"api", ...}
#   templates: 可选模板列表（第一个为默认）
#   charts:    自动图表配置 [{"type": "bar"|"pie"|"line", "x": ..., "y": ..., "title": ...}]

REPORT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "monthly_sales": {
        "name": "月度销售报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    d.name AS dept_name,
                    COUNT(p.id) AS project_count,
                    COALESCE(SUM(p.budget), 0) AS total_budget,
                    COUNT(CASE WHEN p.status = 'active' THEN 1 END) AS active_count,
                    COUNT(CASE WHEN p.status = 'completed' THEN 1 END) AS completed_count
                FROM departments d
                LEFT JOIN users u ON u.dept_id = d.id
                LEFT JOIN projects p ON p.owner_id = u.id
                WHERE 1=1
                GROUP BY d.name
                ORDER BY total_budget DESC
            """,
        },
        "templates": ["sales_summary.j2", "sales_detail.j2"],
        "charts": [
            {"type": "bar", "x": "dept_name", "y": "total_budget", "title": "各部门预算分布"},
            {"type": "pie", "x": "dept_name", "y": "project_count", "title": "各部门项目数占比"},
        ],
    },
    "project_progress": {
        "name": "项目进度报告",
        "source": {
            "type": "sql",
            "sql": """
                SELECT
                    p.name AS project_name,
                    d.name AS owner_dept,
                    p.status,
                    p.budget,
                    p.start_date,
                    p.end_date,
                    COUNT(pm.user_id) AS member_count
                FROM projects p
                LEFT JOIN users u ON u.id = p.owner_id
                LEFT JOIN departments d ON d.id = u.dept_id
                LEFT JOIN project_members pm ON pm.project_id = p.id
                WHERE 1=1
                GROUP BY p.id, p.name, d.name, p.status, p.budget, p.start_date, p.end_date
                ORDER BY p.start_date DESC
            """,
        },
        "templates": ["project_progress.j2"],
        "charts": [
            {"type": "bar", "x": "project_name", "y": "budget", "title": "项目预算对比"},
        ],
    },
    "dept_summary": {
        "name": "部门概览报告",
        "source": {
            "type": "api",
            "url": "http://localhost:8080/api/dept/summary",
        },
        "templates": ["dept_overview.j2"],
        "charts": [],
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
        from config import DB_CONFIG
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
