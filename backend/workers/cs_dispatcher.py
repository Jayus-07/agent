"""cs_dispatcher.py — 客服自动派单 worker（P6 派单 / P7 回收 / P8 投递）。

独立容器运行（compose 服务 ``cs-dispatcher``，声明 2 副本），与 API/HTTP
链路解耦：HTTP 只写 ``waiting_human``，本进程负责把它变成 ``agent_offered``，
并把已经终局的 offer 回收、把落库事件投出去。

一个 tick 的三段（``run_tick``）：

1. ``reap_stage``  —— 回收过期 offer / 关闭超期工单（P7，``CS_REAPER_ENABLED``）
2. ``run_once``    —— 派单（P6，``CS_DISPATCH_MODE`` 三态）
3. ``relay_stage`` —— 投递 pending 事件（P8，``CS_OUTBOX_RELAY_ENABLED``）

顺序是刻意的：**先把过期 offer 放回队列，同一 tick 就能重新派出去**
（方案 §六 P7「过期后 2 秒内释放并重派」，tick=1s）；最后投递事件，
让本 tick 产生的状态变更尽量同 tick 送达浏览器。

``CS_DISPATCH_MODE=off`` **只关闭第 2 段**。第 1、3 段是恢复/投递路径，
不是被灰度门控的算法：把派单关掉却停掉回收，会把用户永久卡在
``agent_offered``；停掉投递则会让已提交的通知永远到不了坐席。
需要彻底停机时用「``off`` + 停容器」组合（方案 §四 停止发布）。

其余设计要点：

- 进程内不持有任何派单权威：绑定、容量与幂等都靠 PostgreSQL 事务与
  部分唯一索引，多副本互相之间不通信、不抢 Redis 锁。
- Redis 不可用 → 派单该轮 fail-closed（不产生新绑定）；relay 该轮投递失败
  的事件保持 ``pending``，下一轮继续重投（不丢）。
- 异常只记日志，循环不退出；每轮写一次 30 秒 TTL 心跳，供告警判定
  "dispatcher 心跳中断"。

手动运行：

    python -m backend.workers.cs_dispatcher
"""
from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass

from backend.config import cs_dispatch as config
from backend.customer_service.dispatch import (
    outbox,
    presence,
    reaper,
    repository,
    service,
)
from backend.customer_service.dispatch.outbox import RelayResult
from backend.customer_service.dispatch.reaper import ReapResult
from backend.customer_service.dispatch.service import DispatchResult
from backend.customer_service.realtime import get_agent_hub
from backend.memory.database import AsyncSessionLocal
from backend.shared.logger import logger

_sleep = asyncio.sleep


@dataclass(frozen=True)
class TickResult:
    """一个 tick 的三段结果。``status`` 代理派单段，保持 P6 观测口径。"""

    dispatch: DispatchResult
    reaped: ReapResult | None = None
    relayed: RelayResult | None = None

    @property
    def status(self) -> str:
        return self.dispatch.status


def resolve_instance_id() -> str:
    """实例名：显式配置优先，否则取容器 hostname（多副本天然唯一）。"""
    configured = os.getenv(config.CS_DISPATCHER_INSTANCE_ID_ENV, "").strip()
    if configured:
        return configured
    return socket.gethostname()


async def write_heartbeat(instance_id: str) -> bool:
    try:
        return bool(await presence.write_dispatcher_heartbeat(instance_id))
    except Exception:
        logger.warning("[cs-dispatcher] heartbeat failed", exc_info=True)
        return False


def bind_hub_loop() -> None:
    """把当前 loop 交给 AgentHub，使提交后的广播能真正发出。

    不启动订阅线程：dispatcher 只发布，API 实例负责订阅并推给浏览器。
    """
    try:
        get_agent_hub().bind_loop(asyncio.get_running_loop(), start_subscriber=False)
    except Exception:
        logger.warning("[cs-dispatcher] agent hub loop bind failed", exc_info=True)


def dispatch_mode() -> str:
    return str(getattr(config, "CS_DISPATCH_MODE", "off")).strip().lower()


async def run_once(now=None) -> DispatchResult:
    """派一轮：最多绑定一张工单，返回结构化结果（不抛业务异常）。"""
    mode = dispatch_mode()
    if mode == "off":
        return DispatchResult(status="disabled", detail="CS_DISPATCH_MODE=off")

    dry_run = mode != "enforce"
    async with AsyncSessionLocal() as session:
        async with session.begin():
            tenant_ids = await repository.waiting_tenant_heads(
                session, limit=config.CS_DISPATCH_TENANT_SCAN_LIMIT
            )

        if not tenant_ids:
            return DispatchResult(status="no_handoff")

        last = DispatchResult(status="no_handoff")
        for tenant_id in tenant_ids[: config.CS_DISPATCH_MAX_TENANTS_PER_TICK]:
            last = await service.dispatch_once(
                session, tenant_id=tenant_id, now=now, dry_run=dry_run
            )
            if last.status in {"dispatched", "shadow"}:
                return last
        return last


async def reap_stage(now=None) -> ReapResult | None:
    """回收过期 offer / 关闭超期工单；开关关闭时返回 ``None``。"""
    if not getattr(config, "CS_REAPER_ENABLED", True):
        return None
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await reaper.reap_once(session, now=now)
    if result.released or result.closed:
        logger.info(
            "[cs-dispatcher] reaped released=%s closed=%s scanned=%s",
            result.released,
            result.closed,
            result.scanned,
        )
    return result


async def relay_stage(now=None) -> RelayResult | None:
    """投递 pending 事件；开关关闭时返回 ``None``。"""
    if not getattr(config, "CS_OUTBOX_RELAY_ENABLED", True):
        return None
    async with AsyncSessionLocal() as session:
        return await outbox.relay_pending_events(session, now=now)


async def run_tick(now=None) -> TickResult:
    """一个完整 tick：reaper → dispatch → outbox relay。"""
    reaped = await reap_stage(now)
    dispatch = await run_once(now)
    relayed = await relay_stage(now)
    return TickResult(dispatch=dispatch, reaped=reaped, relayed=relayed)


async def run_forever(iterations: int | None = None) -> list[TickResult]:
    """主循环；``iterations`` 仅测试用（None = 永不退出）。"""
    instance_id = resolve_instance_id()
    mode = dispatch_mode()
    logger.info(
        "[cs-dispatcher] start instance=%s mode=%s reaper=%s relay=%s interval=%ss",
        instance_id,
        mode,
        getattr(config, "CS_REAPER_ENABLED", True),
        getattr(config, "CS_OUTBOX_RELAY_ENABLED", True),
        config.CS_DISPATCH_INTERVAL_SECONDS,
    )
    bind_hub_loop()

    results: list[TickResult] = []
    index = 0
    while iterations is None or index < iterations:
        try:
            result = await run_tick()
        except Exception:
            logger.warning("[cs-dispatcher] dispatch iteration failed", exc_info=True)
        else:
            results.append(result)
            if result.dispatch.status == "dispatched":
                logger.info(
                    "[cs-dispatcher] offered handoff=%s agent=%s attempt=%s",
                    result.dispatch.handoff_id,
                    result.dispatch.agent_id,
                    result.dispatch.assignment_version,
                )
        await write_heartbeat(instance_id)
        index += 1
        if iterations is None or index < iterations:
            await _sleep(config.CS_DISPATCH_INTERVAL_SECONDS)
    return results


async def _amain() -> None:
    await run_forever()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:  # pragma: no cover - 容器停止
        logger.info("[cs-dispatcher] stopped by signal")


if __name__ == "__main__":  # pragma: no cover - 进程入口
    main()
