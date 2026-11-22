"""shared/sql_obs.py — SQLAlchemy 语句级观测埋点（慢查询 + 执行异常）。

上线后故障定位的两条硬需求（2026-11-22 实机验证会话补齐）：
  1. 慢查询可见：超过 SQL_SLOW_LOG_MS（默认 300ms）的语句记 WARNING，
     含耗时与语句文本——DB 慢导致的接口慢不再只能靠猜。
     SQL_SLOW_LOG_MS=0 = 记录**所有**语句（排查模式；日常别开，日志量大）。
  2. SQL 异常可见：任何在引擎层抛出的执行错误记 ERROR，含语句文本、
     参数个数与底层异常原因。之前只能依赖调用方各自的 logger.warning
     （如 post_my_message persist failed），未接全局异常日志的路径会漏。

为什么用引擎事件而不是 echo=True：echo 全量打印会淹没日志且无耗时字段；
事件方案只记「慢 + 错」，正常路径零开销（一次 perf_counter 取差值）。

脱敏边界：只记语句文本（占位符形式，不含值）与参数个数，**不记参数值**
——参数可能含用户消息/凭据，进日志违反敏感数据红线。

trace 关联：事件在请求协程/工作线程内同步触发，全局 logger 自动注入
trace_id / user_id（contextvar），SQL 日志可与 [Access] 行直接对上。

用法（两个生产引擎都已挂接；幂等，重复 install 无副作用）：
    install_sql_observation(engine, engine_name="memory-async")
"""
from __future__ import annotations

import os
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

from backend.shared.logger import logger

# 慢查询阈值（毫秒）：SQL_SLOW_LOG_MS=0 关闭慢查询记录；上限 60s 防呆
_SLOW_MS = max(0, min(int(os.getenv("SQL_SLOW_LOG_MS", "300")), 60000))

# 语句文本截断：慢查询日志里通常前 300 字符足以识别语句来源
_STMT_MAX = 300

# conn.info 的计时栈 key（连接级复用；嵌套/并发下逐条 pop）
_STARTS_KEY = "_sql_obs_starts"

_INSTALLED: set[int] = set()


def install_sql_observation(engine: Engine, engine_name: str = "engine") -> None:
    """注册语句级事件监听。幂等：同引擎实例重复调用只挂一次。"""
    if id(engine) in _INSTALLED:
        return
    _INSTALLED.add(id(engine))

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info.setdefault(_STARTS_KEY, []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        starts = conn.info.get(_STARTS_KEY)
        start = starts.pop() if starts else None
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms >= _SLOW_MS:  # 阈值 0 = 全记（排查模式）
            logger.warning(
                "[SQL] engine=%s slow=%.1fms rows=%s stmt=%s",
                engine_name,
                elapsed_ms,
                (cursor.rowcount if cursor.rowcount is not None else -1),
                _clip(statement),
            )

    @event.listens_for(engine, "handle_error")
    def _on_error(exception_context):  # noqa: ANN001
        statement = getattr(exception_context, "statement", None) or ""
        parameters = getattr(exception_context, "parameters", None)
        n_params = len(parameters) if isinstance(parameters, (list, tuple)) else -1
        orig = getattr(exception_context, "original_exception", None) or (
            getattr(exception_context, "exception", None)
        )
        logger.error(
            "[SQL] engine=%s error=%s:%s stmt=%s params_n=%s",
            engine_name,
            type(orig).__name__ if orig is not None else "SQLAlchemyError",
            (str(orig)[:300] if orig is not None else str(exception_context)),
            _clip(statement),
            n_params,
        )

    logger.info("[SQL] observation installed engine=%s slow_ms=%s", engine_name, _SLOW_MS)


def _clip(statement: str) -> str:
    text = " ".join((statement or "").split())
    return text[:_STMT_MAX]
