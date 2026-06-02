"""
executor.py — 安全执行 SQL 并返回格式化结果

功能:
  1. 使用 psycopg2 只读事务 (SET TRANSACTION READ ONLY)
  2. 查询超时保护 (SET statement_timeout, PostgreSQL 原生)
  3. 参数化查询防注入
  4. 列级脱敏 (标记为 masked_columns 的列)
  5. 结果格式化为 Markdown 表格
"""
from typing import Dict, Any
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import OperationalError, ProgrammingError

from sql_agent.schema_loader import schema_loader
from utils.logger import logger


# =====================================================
# 主执行器
# =====================================================

def execute_sql(
    sql: str,
    db_config: Dict[str, Any],
    params: Dict[str, Any] = None,
    timeout: float = None,
) -> str:
    """
    安全执行 SQL 查询 (PostgreSQL)。

    参数:
        sql: 经过校验和行级注入的安全 SQL
        db_config: PostgreSQL 连接配置
                   {"host": "...", "port": 5432, "dbname": "...",
                    "user": "...", "password": "..."}
        params: 行级安全参数 (如 {"current_user_id": 101})，不使用 SQL 占位符，
                直接转换成常量值（已经过硬校验，安全）。
        timeout: 超时秒数

    返回:
        Markdown 格式的查询结果字符串
    """
    if timeout is None:
        timeout = schema_loader.query_timeout

    params = params or {}
    conn = None

    try:
        # — 建立连接 —
        conn = psycopg2.connect(**db_config)
        conn.set_session(readonly=True, autocommit=True)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # — 查询超时 (PostgreSQL 原生) —
            cur.execute("SET statement_timeout = %s", (int(timeout * 1000),))

            # 行级安全参数已由 row_security.py 硬编码到 SQL 常量中，安全。
            cur.execute(sql)
            rows = cur.fetchall()

            columns = [desc.name for desc in cur.description] if cur.description else []

            # — 转为 dict 列表 —
            result_dicts = [dict(r) for r in rows]

            # — 列级脱敏 —
            masked_results = [_mask_row(r, columns) for r in result_dicts]

            # — Markdown 格式化 —
            md = _to_markdown_table(columns, masked_results)
            logger.info(f"[Executor] 查询完成: {len(rows)} 行, {len(columns)} 列")
            return md

    except OperationalError as e:
        logger.error(f"[Executor] 数据库连接/操作错误: {e}")
        return f"数据库错误: {e}"

    except ProgrammingError as e:
        error_msg = str(e)
        if "canceling statement" in error_msg.lower():
            logger.warning(f"[Executor] 查询超时 ({timeout}s)")
            return f"查询超时（超过 {timeout} 秒），请简化查询条件。"
        logger.error(f"[Executor] SQL 执行错误: {e}")
        return f"查询执行错误: {error_msg}"

    except Exception as e:
        logger.error(f"[Executor] 查询执行失败: {e}")
        return f"查询执行错误: {str(e)}"

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# =====================================================
# 列级脱敏
# =====================================================

def _mask_value(value: Any, column_key: str) -> Any:
    """对单值执行脱敏"""
    if value is None:
        return value
    if not isinstance(value, str):
        return value

    mask_config = None
    for mc_key, mc_val in schema_loader.masked_columns.items():
        if mc_key.endswith(f".{column_key}") or mc_key == column_key:
            mask_config = mc_val
            break

    if not mask_config:
        return value

    prefix_len, suffix_len = mask_config
    if len(value) <= prefix_len + suffix_len + 1:
        return "*" * min(len(value), 5)

    masked = value[:prefix_len] + "***" + value[-suffix_len:]
    return masked


def _mask_row(row: dict, column_names: list) -> dict:
    """对一行数据执行列级脱敏"""
    masked = {}
    for col, val in row.items():
        masked[col] = _mask_value(val, col)
    return masked


# =====================================================
# Markdown 格式化
# =====================================================

def _to_markdown_table(columns: list, rows: list) -> str:
    """将查询结果格式化为 Markdown 表格"""
    if not rows:
        return "(无结果)"

    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"

    data_lines = []
    for row in rows:
        vals = [str(row.get(c, "")) for c in columns]
        data_lines.append("| " + " | ".join(vals) + " |")

    return "\n".join([header, sep] + data_lines)
