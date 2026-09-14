"""customer_service/checkpointer_cleanup.py — 向后兼容薄壳

实现已迁移到 `backend/orchestration/graph/checkpointer_cleanup.py`：
该模块操作的是 LangGraph 在 agent_memory 里的**共享** checkpoint 三张表，
主图与旅游域同样在用，不属于客服域私有设施。

本文件只保留原有调用签名，避免破坏既有 import / 测试；
新代码请直接 import 编排层的共享实现。
"""
from __future__ import annotations

from backend.orchestration.graph.checkpointer_cleanup import (  # noqa: F401
    CLEANUP_INTERVAL_SECONDS,
    FIRST_RUN_DELAY_SECONDS,
    cleanup_stale_checkpoints,
    daemon_state,
)
from backend.orchestration.graph.checkpointer_cleanup import (
    start_cleanup_daemon as _start_shared,
)


def start_cleanup_daemon(max_age_days: int | None = None) -> bool:
    """启动 TTL 清理守护（兼容旧签名；内部转调共享实现）。

    Args:
        max_age_days: 过期天数；None 时取 CS_CHECKPOINT_TTL_DAYS
    """
    from backend.config.customer_service import CS_CHECKPOINT_TTL_DAYS

    days = max_age_days if max_age_days is not None else CS_CHECKPOINT_TTL_DAYS
    return _start_shared(days, owner="cs")
