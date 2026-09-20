"""cs_dispatcher.py — 客服自动派单 worker（P6）。

独立容器运行（compose 服务 ``cs-dispatcher``，声明 2 副本），与 API/HTTP
链路解耦：HTTP 只写 ``waiting_human``，本进程负责把它变成 ``agent_offered``。

设计要点：

- 每次 ``run_once`` **最多绑定一张工单**；多租户按全局队列顺序（优先级
  DESC、created_at ASC、id ASC）依次尝试，第一个成功即返回。
- 进程内不持有任何派单权威：绑定、容量与幂等都靠 PostgreSQL 事务与
  部分唯一索引，多副本互相之间不通信、不抢 Redis 锁。
- Redis 不可用 → 该轮 fail-closed（不产生新绑定），工单留在 PG，恢复后
  下一轮继续。
- 异常只记日志，循环不退出；每轮写一次 30 秒 TTL 心跳，供告警判定
  "dispatcher 心跳中断"。

手动运行：

    python -m backend.workers.cs_dispatcher
"""
from __future__ import annotations

import asyncio
import os
import socket

from backend.config import cs_dispatch as config
from backend.customer_service.dispatch import presence, repository, service
from backend.customer_service.dispatch.service import DispatchResult
from backend.customer_service.realtime import get_agent_hub
from backend.memory.database import AsyncSessionLocal
from backend.shared.logger import logger

_sleep = asyncio.sleep


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


async def run_forever(iterations: int | None = None) -> list[DispatchResult]:
    """主循环；``iterations`` 仅测试用（None = 永不退出）。"""
    instance_id = resolve_instance_id()
    mode = dispatch_mode()
    logger.info(
        "[cs-dispatcher] start instance=%s mode=%s interval=%ss",
        instance_id,
        mode,
        config.CS_DISPATCH_INTERVAL_SECONDS,
    )
    bind_hub_loop()

    results: list[DispatchResult] = []
    index = 0
    while iterations is None or index < iterations:
        try:
            result = await run_once()
        except Exception:
            logger.warning("[cs-dispatcher] dispatch iteration failed", exc_info=True)
        else:
            results.append(result)
            if result.status == "dispatched":
                logger.info(
                    "[cs-dispatcher] offered handoff=%s agent=%s attempt=%s",
                    result.handoff_id,
                    result.agent_id,
                    result.assignment_version,
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
