"""SQL 查询阶段事件构建器。

事件复用 SSE v2 已有的 status/log 类型；该模块只负责生成安全、低基数
的阶段帧，不携带数据库异常堆栈或结果明细。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def emit_sql_stage(
    sink: Callable[[dict], None] | None,
    *,
    node: str,
    phase: str,
    message: str,
    status: str = "running",
    tool: str = "sql.query",
    **payload: Any,
) -> None:
    """向可选事件接收器发出一个真实 SQL 阶段。"""
    if sink is None:
        return
    now = time.time()
    base = {
        "phase": phase,
        "tool": tool,
        "status": status,
        **payload,
    }
    try:
        sink({"event": "status", "data": {"node": node, "ts": now}})
        sink({
            "event": "log",
            "data": {
                "level": "error" if status == "failed" else "info",
                "node": node,
                "step_id": phase,
                "message": message,
                "payload": base,
                "ts": now,
            },
        })
    except Exception:
        # 观测/展示链路不能阻断 SQL 查询。
        return
