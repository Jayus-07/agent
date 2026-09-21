"""dispatch/agent_busy.py — 坐席自动置忙（2026-09-21 派单治理）。

语义：

- 坐席在滑动窗口（``CS_AGENT_AUTO_BUSY_WINDOW_SECONDS``）内拒单/超时次数
  达到 ``CS_AGENT_AUTO_BUSY_THRESHOLD`` → 写入置忙 key（TTL =
  ``CS_AGENT_AUTO_BUSY_SECONDS``），期间 dispatcher 不派单给他。
- 计数与置忙全部走 Redis（INCR + EXPIRE / SETEX），多副本 dispatcher/
  offers API 天然共享状态；**fail-open**：Redis 不可用时视为无人置忙，
  派单照常——在线判定（``presence``）已经是 fail-closed，置忙只是
  体验优化层，不应放大故障面。
- key 空间与 presence/心跳同域前缀 ``cs:``，便于运维排查。

为什么不用 DB：置忙是短时限流（分钟级），PG 事务写它会把每次拒单变成
跨表事务；Redis TTL 天然过期，无需清理任务。
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

from backend.config.cs_dispatch import (
    CS_AGENT_AUTO_BUSY_SECONDS,
    CS_AGENT_AUTO_BUSY_THRESHOLD,
    CS_AGENT_AUTO_BUSY_WINDOW_SECONDS,
)
from backend.customer_service.dispatch.presence import _redis_client

REJECT_COUNT_KEY_PREFIX = "cs:agent:reject:"
BUSY_KEY_PREFIX = "cs:agent:busy:"


def reject_count_key(tenant_id: str, agent_id: str) -> str:
    return f"{REJECT_COUNT_KEY_PREFIX}{tenant_id}:{agent_id}"


def busy_key(tenant_id: str, agent_id: str) -> str:
    return f"{BUSY_KEY_PREFIX}{tenant_id}:{agent_id}"


def _record_reject(client: Any, tenant_id: str, agent_id: str) -> bool:
    """INCR 拒单计数（窗口 TTL），达阈值写置忙 key。返回是否被置忙。"""
    count_key = reject_count_key(tenant_id, agent_id)
    count = client.incr(count_key)
    if count == 1:
        client.expire(count_key, CS_AGENT_AUTO_BUSY_WINDOW_SECONDS)
    if int(count or 0) >= CS_AGENT_AUTO_BUSY_THRESHOLD:
        client.setex(busy_key(tenant_id, agent_id), CS_AGENT_AUTO_BUSY_SECONDS, "1")
        client.delete(count_key)
        return True
    return False


def _lookup_busy(client: Any, keys: list[str]) -> list[Any]:
    if hasattr(client, "mget"):
        return client.mget(keys)
    return [client.exists(key) for key in keys]  # pragma: no cover


async def record_agent_reject(tenant_id: str, agent_id: str) -> bool:
    """登记一次拒单/超时；达到阈值自动置忙。Redis 不可用返回 False（fail-open）。

    返回 ``True`` 表示本次触发了置忙（调用方可用于日志/指标）。
    """
    tenant_id = str(tenant_id or "").strip()
    agent_id = str(agent_id or "").strip()
    if not tenant_id or not agent_id:
        return False
    client = _redis_client()
    if client is None:
        return False
    try:
        return bool(
            await asyncio.to_thread(_record_reject, client, tenant_id, agent_id)
        )
    except Exception:
        return False


async def busy_agent_ids(
    *, tenant_id: str, agent_ids: Iterable[str]
) -> set[str]:
    """返回处于置忙期的 agent_id 子集；Redis 不可用返回空集（fail-open）。"""
    ids = [str(agent_id) for agent_id in agent_ids]
    if not ids:
        return set()
    client = _redis_client()
    if client is None:
        return set()
    keys = [busy_key(tenant_id, agent_id) for agent_id in ids]
    try:
        values = await asyncio.to_thread(_lookup_busy, client, keys)
    except Exception:
        return set()
    if values is None or len(values) != len(ids):
        return set()
    return {
        agent_id
        for agent_id, value in zip(ids, values)
        if value is not None and (not isinstance(value, int) or value)
    }
