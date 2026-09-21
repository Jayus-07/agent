"""
executor.py — 安全执行 SQL 并返回结构化结果（生产就绪 P0 加固版）

功能:
  1. 连接池：ThreadedConnectionPool 替代每次新建连接
  2. 只读账号：agent_readonly 角色替代 postgres superuser（数据库层强制只读）
  3. 连接参数：connect_timeout / keepalives / application_name
  4. 严格只读事务：先 BEGIN + SET TRANSACTION READ ONLY（双重防线）
  5. 查询超时保护：SET LOCAL statement_timeout（事务级别，干净）
  6. 参数化查询：row_security 注入的 user_id 等敏感值走 params 通道，不进 SQL 文本
  7. 列级脱敏：标记为 masked_columns 的列
  8. 结果返回 SQLResult（结构化）— 失败细分 timeout / syntax_error / permission_denied / connection

向后兼容：
  - 旧的 `execute_sql()` 保留（Markdown 字符串出口），供 `execute_sql_tool` 路径
  - 新的 `execute_sql_struct()` 返回 SQLResult，供 SQLSkill 走结构化错误语义
"""
import threading
import time
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extras
import sqlglot
from sqlglot import exp
from psycopg2 import OperationalError, ProgrammingError
from psycopg2.pool import ThreadedConnectionPool, PoolError

from backend.sql.schema_loader import schema_loader
from backend.sql.sql_result import SQLResult
from backend.shared.logger import logger

# =================================================
# 连接池（线程安全单例）
# =================================================

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ThreadedConnectionPool:
    """惰性初始化连接池单例（线程安全）。

    首次调用时从 BUSINESS_DB_READONLY_CONFIG 构建池，
    后续调用复用同一池。
    """
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool

        from backend.config import (
            BUSINESS_DB_READONLY_CONFIG as db_config,
            DB_POOL_MIN_CONN,
            DB_POOL_MAX_CONN,
            DB_CONNECT_TIMEOUT,
            DB_KEEPALIVES_IDLE,
        )

        _pool = ThreadedConnectionPool(
            minconn=DB_POOL_MIN_CONN,
            maxconn=DB_POOL_MAX_CONN,
            host=db_config["host"],
            port=db_config["port"],
            dbname=db_config["dbname"],
            user=db_config["user"],
            password=db_config["password"],
            connect_timeout=DB_CONNECT_TIMEOUT,
            keepalives=1,
            keepalives_idle=DB_KEEPALIVES_IDLE,
            keepalives_interval=10,
            keepalives_count=3,
            application_name="agent_sql_executor",
        )
        logger.info(
            f"[Executor] 连接池初始化: "
            f"min={DB_POOL_MIN_CONN} max={DB_POOL_MAX_CONN} "
            f"user={db_config['user']} db={db_config['dbname']}"
        )
        return _pool


def _close_pool() -> None:
    """关闭连接池（测试/进程退出时调用）。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
            logger.info("[Executor] 连接池已关闭")


class PoolExhaustedError(Exception):
    """连接池打满（限流语义，不再裸 500）——建议项 2026-09-21。"""


@contextmanager
def _get_conn(timeout: float = None):
    """从连接池获取一个连接，用 with 自动归还。

    使用 psycopg2.pool.ThreadedConnectionPool.getconn() / putconn()。
    归还前执行 rollback() 清理未完成的事务。
    """
    pool = _get_pool()
    conn = None
    try:
        try:
            conn = pool.getconn()
        except PoolError as e:
            # 连接池打满：转限流语义（建议项 2026-09-21），不再裸 500
            raise PoolExhaustedError(
                f"连接池已满（maxconn={pool.maxconn}）"
            ) from e
        conn.autocommit = False
        conn.set_session(readonly=True)
        yield conn
    finally:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[Executor] 连接 rollback 失败（可能已关闭）", exc_info=True)
            pool.putconn(conn)


def _classify_pg_error(exc: Exception) -> tuple[str, str | None]:
    """把 psycopg2 异常映射为 (SQLStatus, error_type)。

    优先级：先按 psycopg2 异常类（SQLSTATE 对应），后按消息 substring。
    这条规则避免 PG 区域设置（如中文错误信息）影响分类。

    返回:
      (status, error_type)
        status: 与 SQLResult SQLStatus 取值之一
        error_type: 子分类字符串（与 StepResult.error_type 对齐）
    """
    # SQLSTATE 由 .pgcode 暴露，对应标准 5 位 SQLSTATE（不依赖 locale）
    sqlstate = getattr(exc, "pgcode", None)

    # 1. 超时（最常见）
    if sqlstate == "57014":  # query_canceled
        return "timeout", None

    # 2. 权限拒绝
    if sqlstate == "42501":  # insufficient_privilege
        return "permission_denied", "privilege"

    # 3. 资源不足（连接池满 / OOM）
    if sqlstate in ("53000", "53100", "53200", "53300"):
        return "failed", "resources"

    # 4. 语法错（解析失败 / 关键字拼错）
    if sqlstate in ("42601", "42602", "42622"):
        return "syntax_error", "syntax"

    # 5. 关系 / 列 / 函数不存在 → schema 不匹配（业务侧问题）
    if sqlstate == "42P01":  # undefined_table
        return "syntax_error", "schema_mismatch"
    if sqlstate == "42703":  # undefined_column
        return "syntax_error", "column_not_found"
    if sqlstate in ("42883", "42884"):  # undefined_function / ambiguous_function
        return "syntax_error", "function_not_found"

    # 6. SQLSTATE 不可用或非标准 → 按异常类 + substring 双兜底
    try:
        import psycopg2.errors as pgerr  # noqa: WPS433
        if isinstance(exc, pgerr.QueryCanceled):
            return "timeout", None
        if isinstance(exc, pgerr.InsufficientPrivilege):
            return "permission_denied", "privilege"
        if isinstance(exc, (pgerr.SyntaxError, pgerr.UndefinedTable, pgerr.UndefinedColumn)):
            return "syntax_error", "schema_mismatch"
    except ImportError:
        pass

    msg = str(exc).lower()
    if sqlstate == "57014" or "canceling statement due to statement timeout" in msg:
        return "timeout", None
    if "permission denied" in msg or "权限不足" in msg:
        return "permission_denied", "privilege"
    if "read-only transaction" in msg:
        return "permission_denied", "readonly_violation"
    if "does not exist" in msg or "不存在" in msg:
        return "syntax_error", "schema_mismatch"
    if "syntax error" in msg or "语法错误" in msg:
        return "syntax_error", "syntax"

    return "failed", "unknown"


# =================================================
# 主执行器
# =================================================

def _public_error_text(status: str, timeout: float) -> str:
    """把 PG 异常转成对外安全的分类文案。

    安全修复（审查 #11）：PG 报错原文可能携带 SQL 片段/字面量数据，
    只允许进日志，不得回显给调用方。
    """
    if status == "timeout":
        return f"查询超时（超过 {timeout} 秒），请简化查询条件。"
    if status == "permission_denied":
        return "安全错误：数据库权限拒绝，请检查只读角色配置。"
    if status == "syntax_error":
        return "查询执行错误：SQL 语法或对象名不正确，请调整后重试。"
    return "数据库错误：执行失败，请稍后重试或联系管理员。"


def execute_sql(
    sql: str,
    db_config: dict[str, Any] | None = None,
    params: dict[str, Any] = None,
    timeout: float = None,
) -> str:
    """
    安全执行 SQL 查询 (PostgreSQL) — 使用连接池。

    参数:
        sql:      已经过校验和行级注入的安全 SQL（含 %(name)s 占位符）
        db_config: 已废弃（v2 使用连接池，忽略此参数）
        params:   psycopg2 named params（如 {"users_id": 101}）
        timeout:  超时秒数

    返回:
        Markdown 格式的查询结果字符串
    """
    if timeout is None:
        timeout = schema_loader.query_timeout

    params = params or {}

    try:
        with _get_conn(timeout) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 显式只读事务 + 超时（数据库层双重防线）
                cur.execute("BEGIN")
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = %s", (int(timeout * 1000),))

                # 参数化执行
                cur.execute(sql, params)
                rows = cur.fetchall()

                columns = [desc.name for desc in cur.description] if cur.description else []
                result_dicts = [dict(r) for r in rows]
                lineage = _output_lineage(sql)
                masked_results = [_mask_row(r, columns, lineage) for r in result_dicts]

                md = _to_markdown_table(columns, masked_results)
                logger.info(f"[Executor] 查询完成: {len(rows)} 行, {len(columns)} 列")
                conn.commit()
                return md

    except PoolExhaustedError as e:
        logger.warning(f"[Executor] 连接池打满: {e}")
        return "**查询被限流**\n\n当前数据库并发过高（连接池已满），请稍后重试。"

    except OperationalError as e:
        status, _ = _classify_pg_error(e)
        # 安全修复：原文只进日志（可能含 SQL 片段/字面量），对外只回分类语义
        logger.error(f"[Executor] 数据库执行错误({status}): {e}")
        if timeout is None:
            timeout = schema_loader.query_timeout
        return _public_error_text(status, timeout)

    except ProgrammingError as e:
        status, _ = _classify_pg_error(e)
        logger.error(f"[Executor] SQL 执行错误({status}): {e}")
        if timeout is None:
            timeout = schema_loader.query_timeout
        return _public_error_text(status, timeout)

    except Exception as e:
        logger.error(f"[Executor] SQL 执行失败: {e}")
        if timeout is None:
            timeout = schema_loader.query_timeout
        return _public_error_text("failed", timeout)


# =================================================
# 结构化执行（A 段新增：让 SQLSkill 拿到精确错误语义）
# =================================================

def execute_sql_struct(
    sql: str,
    db_config: dict[str, Any] | None = None,
    params: dict[str, Any] = None,
    timeout: float = None,
) -> SQLResult:
    """执行 SQL 并返回 SQLResult（v2：使用连接池）。

    与 execute_sql 的差异：
      - 返回 SQLResult 而非 Markdown 字符串
      - status 精确分类: success / no_data / timeout / syntax_error / permission_denied / failed
    """
    if timeout is None:
        timeout = schema_loader.query_timeout
    params = params or {}
    t0 = time.monotonic()

    try:
        with _get_conn(timeout) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("BEGIN")
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = %s", (int(timeout * 1000),))

                cur.execute(sql, params)
                rows = cur.fetchall()
                columns = [desc.name for desc in cur.description] if cur.description else []
                result_dicts = [dict(r) for r in rows]
                lineage = _output_lineage(sql)
                masked = [_mask_row(r, columns, lineage) for r in result_dicts]

                conn.commit()
                elapsed = time.monotonic() - t0
                logger.info(f"[Executor:struct] 查询完成: {len(rows)} 行, {len(columns)} 列")
                return SQLResult.success(masked, columns, sql=sql, elapsed=elapsed)

    except PoolExhaustedError as e:
        elapsed = time.monotonic() - t0
        logger.warning(f"[Executor:struct] 连接池打满: {e}")
        # status 沿用既有 Literal（"failed"），限流语义由 error_type + 文案表达
        return SQLResult.failed(
            status="failed",
            error="当前数据库并发过高（连接池已满），请稍后重试",
            error_type="rate_limited",
            sql=sql,
            elapsed=elapsed,
        )

    except (OperationalError, ProgrammingError) as e:
        status, error_type = _classify_pg_error(e)
        elapsed = time.monotonic() - t0
        if isinstance(e, OperationalError) and status == "failed":
            error_type = "connection"
        # 安全修复：PG 原文只进日志；SQLResult.error 会经 step_results 透出
        # 到 Reporter/前端，只回分类语义
        logger.warning(f"[Executor:struct] 失败 status={status} type={error_type}: {e}")
        return SQLResult.failed(
            status=status,
            error=_struct_error_text(status, timeout),
            error_type=error_type,
            sql=sql,
            elapsed=elapsed,
        )

    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"[Executor:struct] 兜底异常: {e}")
        return SQLResult.failed(
            status="failed",
            error=_struct_error_text("failed", timeout),
            error_type="unknown",
            sql=sql,
            elapsed=elapsed,
        )


# =================================================
# 列级脱敏
# =================================================

def _struct_error_text(status: str, timeout: float) -> str:
    """execute_sql_struct 的对外安全文案（原文只进日志）。"""
    if timeout is None:
        timeout = schema_loader.query_timeout
    return _public_error_text(status, timeout)


def _output_lineage(sql: str) -> dict[str, set[str]]:
    """输出列名 → 源列名集合 的映射（用于别名脱敏，审查 #10）。

    `SELECT name AS n` 的结果列名是 n，按结果列名匹配 masked_columns
    会漏脱敏。这里解析 AST 把 n → {name} 映射出来，_mask_row 据此
    回溯源列名匹配。解析失败返回空 dict（退化为按结果列名匹配，
    不阻塞查询）。
    """
    try:
        stmt = sqlglot.parse_one(sql, read="postgres")
        mapping: dict[str, set[str]] = {}
        for proj in stmt.expressions:
            out_name = (getattr(proj, "alias", "") or proj.output_name or "")
            if not out_name:
                continue
            cols = {c.name.lower() for c in proj.find_all(exp.Column)}
            if cols:
                mapping[out_name.lower()] = cols
        return mapping
    except Exception:
        return {}


def _apply_mask(value: str, prefix_len: int, suffix_len: int) -> str:
    """按 (prefix, suffix) 配置对字符串打码。

    P1-11 修复：suffix_len=0 时 value[-0:] 会返回整个字符串，
    导致脱敏结果泄露原文（"张***张三丰"），必须显式跳过后缀拼接
    """
    if len(value) <= prefix_len + suffix_len + 1:
        return "*" * min(len(value), 5)
    masked = value[:prefix_len] + "***"
    if suffix_len > 0:
        masked += value[-suffix_len:]
    return masked


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

    return _apply_mask(value, mask_config[0], mask_config[1])


def _mask_row(row: dict, column_names: list, lineage: dict | None = None) -> dict:
    """对一行数据执行列级脱敏。

    结果列名与源列名（lineage 解析出的别名映射）都参与匹配：
    `SELECT name AS n` 时结果列名 n 不在 masked_columns 里，
    但源列名 name 在 → 仍需脱敏。多个键命中时取第一个匹配配置，
    只打码一次。
    """
    masked = {}
    for col, val in row.items():
        keys = [col.lower()] + sorted((lineage or {}).get(col.lower(), set()))
        config = next(
            (
                cfg
                for key in keys
                for mk, cfg in schema_loader.masked_columns.items()
                if mk.endswith(f".{key}") or mk == key
            ),
            None,
        )
        if config and isinstance(val, str):
            masked[col] = _apply_mask(val, config[0], config[1])
        else:
            masked[col] = val
    return masked


# =================================================
# Markdown 格式化
# =================================================

def _to_markdown_table(columns: list, rows: list) -> str:
    """将查询结果格式化为 Markdown 表格"""
    if not rows:
        return "(无结果)"

    header = "| " + " | " .join(columns) + " |"
    sep = "| " + " | " .join("---" for _ in columns) + " |"

    data_lines = []
    for row in rows:
        vals = [str(row.get(c, "")) for c in columns]
        data_lines.append("| " + " | " .join(vals) + " |")

    return "\n".join([header, sep] + data_lines)
