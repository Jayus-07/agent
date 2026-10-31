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
    """全局扫描转接等待进度 → SLA warning 提前告警 + closed 兜底。

    P3 分级职责（与 reaper 配合）：
    - warning（elapsed ≥ 50% 窗口）：本扫描发出（Redis SETNX 幂等），
      给主管通知中心留出接单窗口；
    - breach（≥ 100% = total_deadline 到点）：**由 reaper 关闭路径发出**
      （_close_handoff 置 sla_level=breach + sla.breach 事件）——dispatcher
      worker 每秒扫描必先于本 beat 扫描关闭工单，maintenance 发 breach
      在现网是死代码；本扫描对超时行仅保留 close_stale 兜底（reaper
      不可用时）。
    DB sla_level 只升不降；覆盖 handoff_requested / waiting_human 两态。
    """
    from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
    from backend.customer_service._db_loop import run_sync

    async def _scan() -> tuple[list[dict], list[dict]]:
        from sqlalchemy import select

        from backend.customer_service.models.handoff import CSHandoff
        from backend.customer_service.repository import HandoffRepository
        from backend.memory.database import AsyncSessionLocal

        now = datetime.now(timezone.utc)
        timeout = CS_HANDOFF_TIMEOUT_SECONDS
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(CSHandoff).where(
                        CSHandoff.handoff_state.in_(
                            ["handoff_requested", "waiting_human"],
                        ),
                    )
                )
            ).scalars().all()
            alerts: list[dict] = []
            for row in rows:
                if row.created_at is None:
                    continue
                elapsed = (now - row.created_at).total_seconds()
                # 只做提前告警；≥timeout 的行交给 close_stale/reaper
                if elapsed < timeout / 2 or elapsed >= timeout:
                    continue
                if row.sla_level == "warning":
                    continue
                # Redis SETNX 幂等：同一 handoff 一个窗口只发一次
                fired = True
                try:
                    from backend.customer_service.realtime import get_agent_hub

                    fired = get_agent_hub().mark_sla_alert_once(
                        handoff_id=row.handoff_id, level="warning",
                        ttl_seconds=max(30, timeout),
                    )
                except Exception as exc:  # noqa: BLE001 — Redis 故障不阻断扫描
                    logger.warning(
                        "[CSMaintenance] sla dedup mark failed: %s", exc,
                    )
                if not fired:
                    continue
                row.sla_level = "warning"
                alerts.append({
                    "handoff_id": row.handoff_id,
                    "conversation_id": row.conversation_id,
                    "tenant_id": row.tenant_id,
                    "level": "warning",
                    "elapsed_seconds": int(elapsed),
                })
            await db.commit()
            repo = HandoffRepository(db)
            cutoff = now - timedelta(seconds=timeout)
            closed = await repo.close_stale(
                states=["handoff_requested", "waiting_human"],
                cutoff=cutoff,
            )
            await db.commit()
            return alerts, closed

    operation = _scan()
    try:
        alerts, closed = run_sync(operation)
        # 真实 bridge 会等待并接管协程；这里的防御性 close 也兼容
        # 同步测试桩直接返回结果而未消费 operation 的情况。
        if operation.cr_frame is not None:
            operation.close()
    except Exception as e:
        operation.close()
        logger.error("[CSMaintenance] handoff timeout scan failed: %s", e)
        return {"ok": False, "closed": [], "alerts": [], "error": str(e)}

    # 分级告警广播（persist=True：进事件流可补发，主管通知中心消费）
    # maintenance worker 进程没有 WS 主 loop（bind_loop 只在 app/dispatcher
    # 调用），不绑定则 publish 会静默丢弃——借用 _db_loop 常驻后台 loop 作
    # 广播跳板（与 cs_dispatcher.bind_hub_loop 同模式，只发布不订阅；
    # bind 为幂等赋值，重复调用无副作用）。
    try:
        from backend.customer_service import _db_loop
        from backend.customer_service.realtime import get_agent_hub

        get_agent_hub().bind_loop(_db_loop.get_loop(), start_subscriber=False)
    except Exception as exc:  # noqa: BLE001 — 绑定失败仅丢广播，不阻断扫描
        logger.warning("[CSMaintenance] agent hub loop bind failed: %s", exc)
    for item in alerts:
        try:
            from backend.customer_service.realtime import get_agent_hub

            get_agent_hub().publish(
                f"sla.{item['level']}",
                persist=True,
                handoff_id=item["handoff_id"],
                conversation_id=item["conversation_id"],
                level=item["level"],
                elapsed_seconds=item["elapsed_seconds"],
            )
            logger.warning(
                "[CSMaintenance] handoff SLA %s: conv=%s elapsed=%ss",
                item["level"], item["conversation_id"],
                item["elapsed_seconds"],
            )
        except Exception as exc:  # noqa: BLE001 — 广播失败不阻断扫描
            logger.warning("[CSMaintenance] sla alert publish failed: %s", exc)

    for item in closed:
        logger.warning(
            "[CSMaintenance] handoff timed out → closed: user=%s conv=%s "
            "was=%s id=%s",
            item["user_id"], item["conversation_id"],
            item["handoff_state"], item["handoff_id"],
        )
    if closed:
        logger.info("[CSMaintenance] handoff scan closed %d stale", len(closed))
    return {
        "ok": True, "closed": closed, "alerts": alerts,
        "count": len(closed),
    }


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
