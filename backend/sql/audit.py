"""
audit.py — SQL 查询审计持久化（STOP C，best-effort）

统一写入点：backend/sql/sql_agent.py 的策略链/旧链出口与收口后的
execute_sql_tool——所有 NL2SQL 生产入口最终都汇聚到 agent 层，
因此审计逻辑只存在这一份，不在各入口复制（规格 §十）。

设计约束：
  - best-effort：审计写入失败只记日志 + 计数，绝不阻塞/失败正常查询
    （执行前的安全决策在内存中完成，不依赖审计成败，无 fail-open 面）
  - SQL 原文口径（2026-10-06 拍板变更）：存原文（sql_text，截断 8000 字符，
    migration 077）用于事故复盘——042 原「不存原文」的 PII 保守设计被推翻；
    生成 SQL 的 WHERE 可能内嵌用户 literal，以「审计表仅管理员可读 +
    保留期清理」缓解。query_hash = sha256(normalized_sql) 继续保留（聚合比对）。
  - 禁止落库：Authorization/JWT/password/凭据/堆栈 secret
  - 表：agent_memory.sql_query_audits（migration 042/077，登记 MIGRATION_TARGETS）
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from typing import Any, Iterable

from backend.shared.logger import logger

# 审计决策枚举（低基数；对外 metrics/trace 复用同一套语义）
DECISION_ALLOW = "ALLOW"
DECISION_DENY_PERMISSION = "DENY_PERMISSION"
DECISION_DENY_SCOPE = "DENY_SCOPE"
DECISION_DENY_TABLE = "DENY_TABLE"
DECISION_DENY_VALIDATOR = "DENY_VALIDATOR"
DECISION_EXECUTION_SUCCESS = "EXECUTION_SUCCESS"
DECISION_EXECUTION_FAILED = "EXECUTION_FAILED"
DECISION_TIMEOUT = "TIMEOUT"

# SQLSTATE 57014 → timeout（与 executor._classify_pg_error 同口径）
_TIMEOUT_STATUS = "timeout"

_NORMALIZE_WS = re.compile(r"\s+")

_write_failures_total = 0
_fail_lock = threading.Lock()


def normalize_sql_hash(sql: str) -> str:
    """sha256(去空白规范化 SQL)——只用于聚合比对，不含原文。"""
    normalized = _NORMALIZE_WS.sub(" ", (sql or "").strip()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _conn():
    """审计库连接（agent_memory，与 trace/observability 存储同库同口径）。"""
    import psycopg2

    from backend.config import MEMORY_DB_CONFIG

    return psycopg2.connect(
        host=MEMORY_DB_CONFIG["host"],
        port=MEMORY_DB_CONFIG["port"],
        dbname=MEMORY_DB_CONFIG["dbname"],
        user=MEMORY_DB_CONFIG["user"],
        password=MEMORY_DB_CONFIG["password"],
        connect_timeout=3,
        application_name="agent_sql_audit",
    )


_INSERT_SQL = """
INSERT INTO sql_query_audits (
    id, session_id, user_id, tenant_id, department, data_scope,
    source_channel, tool_name, query_hash, tables, decision, deny_code,
    duration_ms, row_count, status, error_type, sql_text, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
"""

# 原文截断上限：防行膨胀（正常 NL2SQL 语句远小于此）
_SQL_TEXT_MAX_LEN = 8000


def record_sql_audit(
    *,
    decision: str,
    session_id: str = "",
    user_id: str = "",
    tenant_id: str = "",
    department: str = "",
    data_scope: str = "",
    source_channel: str = "",
    tool_name: str = "sql.query",
    sql: str = "",
    tables: Iterable[str] = (),
    deny_code: str = "",
    duration_ms: int = 0,
    row_count: int | None = None,
    status: str = "",
    error_type: str = "",
) -> None:
    """异步 best-effort 写一条审计。任何异常都不向上传播。"""
    row = (
        str(uuid.uuid4()),
        (session_id or "")[:128],
        (user_id or "")[:128],
        (tenant_id or "")[:128],
        (department or "")[:128],
        (data_scope or "")[:32],
        (source_channel or "")[:16],
        (tool_name or "")[:64],
        normalize_sql_hash(sql) if sql else "",
        json.dumps(sorted({t for t in tables or ()}), ensure_ascii=False),
        (decision or "")[:32],
        (deny_code or "")[:64],
        int(duration_ms or 0),
        row_count,
        (status or "")[:32],
        (error_type or "")[:64],
        (sql or "")[:_SQL_TEXT_MAX_LEN],
    )
    thread = threading.Thread(
        target=_safe_insert, args=(row,), name="sql-audit", daemon=True)
    thread.start()


def _safe_insert(row: tuple) -> None:
    global _write_failures_total
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(_INSERT_SQL, row)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # best-effort：审计失败不影响主查询（规格 §九）
        with _fail_lock:
            _write_failures_total += 1
            failures = _write_failures_total
        logger.warning(f"[SQLAudit] 审计写入失败(累计 {failures}): {e}")


def decision_from_result(status: str) -> str:
    """执行结果 → 审计决策（执行阶段）。"""
    if status in ("success", "no_data"):
        return DECISION_EXECUTION_SUCCESS
    if status == _TIMEOUT_STATUS:
        return DECISION_TIMEOUT
    return DECISION_EXECUTION_FAILED


def write_failure_count() -> int:
    """测试/运维观测用：累计审计写失败次数。"""
    with _fail_lock:
        return _write_failures_total
