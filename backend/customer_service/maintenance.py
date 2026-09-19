"""customer_service/maintenance.py — CS 全局维护扫描（P2.4）

beat 周期任务的执行核心，与 Celery 完全解耦：
- celery 薄壳（tasks/cs_maintenance_tasks.py）仅 import 本模块调用
- 本地开发无 beat 时可经 API/脚本手动触发
- 全部动作走原子条件 UPDATE（幂等），与运行时恢复（supervisor
  _recover_handoff_timeout / confirmation_flow 过期分支）并发安全

设计依据：refactor-plan.md P2.4（复用 tasks 基建：tasks 表/退避/幂等键）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.shared.logger import logger


def scan_handoff_timeouts() -> dict[str, Any]:
    """全局扫描超时未接入的转接 → closed。

    覆盖 handoff_requested / waiting_human 两态（状态机二者均可合法
    转换到 CLOSED，与 supervisor._recover_handoff_timeout 同口径）。
    用户在图运行时由 supervisor 恢复；无人触发图时由本扫描兜底。
    """
    from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
    from backend.customer_service._db_loop import run_sync

    async def _scan() -> list[dict]:
        from backend.customer_service.repository import HandoffRepository
        from backend.memory.database import AsyncSessionLocal

        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=CS_HANDOFF_TIMEOUT_SECONDS,
        )
        async with AsyncSessionLocal() as db:
            repo = HandoffRepository(db)
            closed = await repo.close_stale(
                states=["handoff_requested", "waiting_human"],
                cutoff=cutoff,
            )
            await db.commit()
            return closed

    operation = _scan()
    try:
        closed = run_sync(operation)
        # 真实 bridge 会等待并接管协程；这里的防御性 close 也兼容
        # 同步测试桩直接返回结果而未消费 operation 的情况。
        if operation.cr_frame is not None:
            operation.close()
    except Exception as e:
        operation.close()
        logger.error("[CSMaintenance] handoff timeout scan failed: %s", e)
        return {"ok": False, "closed": [], "error": str(e)}

    for item in closed:
        logger.warning(
            "[CSMaintenance] handoff timed out → closed: user=%s conv=%s "
            "was=%s id=%s",
            item["user_id"], item["conversation_id"],
            item["handoff_state"], item["handoff_id"],
        )
    if closed:
        logger.info("[CSMaintenance] handoff scan closed %d stale", len(closed))
    return {"ok": True, "closed": closed, "count": len(closed)}


def scan_confirmation_expiries() -> dict[str, Any]:
    """全局扫描已过期的 pending 确认 → expired。

    与确认链路互斥：用户确认时 claim_pending 的 state='pending' 条件
    与本扫描的过期条件原子竞争，不会双写（审计口径：expired 独立终态）。
    """
    from backend.customer_service._db_loop import run_sync

    async def _scan() -> list[dict]:
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            expired = await repo.expire_stale()
            await db.commit()
            return expired

    operation = _scan()
    try:
        expired = run_sync(operation)
        if operation.cr_frame is not None:
            operation.close()
    except Exception as e:
        operation.close()
        logger.error("[CSMaintenance] confirmation expiry scan failed: %s", e)
        return {"ok": False, "expired": [], "error": str(e)}

    for item in expired:
        logger.info(
            "[CSMaintenance] confirmation expired: user=%s conv=%s id=%s",
            item["user_id"], item["conversation_id"], item["confirmation_id"],
        )
    if expired:
        logger.info(
            "[CSMaintenance] confirmation scan expired %d stale", len(expired),
        )
    return {"ok": True, "expired": expired, "count": len(expired)}
