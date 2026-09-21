"""dispatch/presence.py — 坐席在线状态与 dispatcher 心跳（Redis）。

设计边界（方案 §五「不用 Redis 分布式锁」）：

- Redis **只回答「这个坐席最近 45 秒有没有心跳」**，不承担绑定/容量判定；
  最终绑定与容量一律由 PostgreSQL 事务决定（见 ``repository``/``service``）。
- 所有查询 **fail-closed**：Redis 不可用返回 ``None``，调用方必须放弃本轮
  派单；绝不允许把"查不到"当作"没人在线"以外的任何成功语义。
- key 复用 ``AgentHub.presence_key``，保证与 P5 的 WS 心跳写侧完全一致
  （tenant/agent 分段 URL 编码，避免拼接碰撞）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

from backend.config.cs_dispatch import (
    CS_DISPATCHER_HEARTBEAT_TTL_SECONDS,
)
from backend.customer_service.realtime import AgentHub
from backend.shared.logger import logger

_HEARTBEAT_KEY_PREFIX = "cs:dispatcher:heartbeat:"


def _redis_client() -> Any | None:
    """间接层：测试替换此函数即可模拟 Redis 可用/不可用/报错。"""
    try:
        from backend.infra.redis.client import get_redis

        return get_redis()
    except Exception:
        logger.warning("[cs-dispatch] redis client unavailable", exc_info=True)
        return None


def dispatcher_heartbeat_key(instance_id: str) -> str:
    return f"{_HEARTBEAT_KEY_PREFIX}{instance_id}"


def _decode(raw: Any) -> bool:
    if raw is None:
        return False
    if isinstance(raw, bytes):
        return bool(raw.strip())
    return bool(str(raw).strip())


def _lookup(client: Any, keys: list[str]) -> Any:
    """同步 Redis 往返；由 ``asyncio.to_thread`` 调用，不阻塞事件循环。"""
    if hasattr(client, "mget"):
        return client.mget(keys)
    return [client.exists(key) for key in keys]  # pragma: no cover - 极简客户端


async def online_agent_ids(
    *, tenant_id: str, agent_ids: Iterable[str]
) -> set[str] | None:
    """返回 45 秒内有心跳的 agent_id 子集；Redis 不可用返回 ``None``。

    使用 ``MGET`` 单次往返（``presence_key`` 有 TTL，存在即在线，值不参与
    判定）。无候选时不访问 Redis —— 但仍返回空集合而不是 ``None``，
    因为"确实没有候选"与"Redis 故障"必须区分：前者是正常 no-op，
    后者必须 fail-closed。

    Redis 客户端是同步实现（与 P5 的 ``AgentHub`` 同一份 ``get_redis()``），
    因此丢线程池执行，避免在派单事务持锁期间阻塞事件循环。
    """
    ids = [str(agent_id) for agent_id in agent_ids]
    if not ids:
        return set()

    client = _redis_client()
    if client is None:
        return None

    keys = [AgentHub.presence_key(tenant_id, agent_id) for agent_id in ids]
    try:
        values = await asyncio.to_thread(_lookup, client, keys)
    except Exception:
        logger.warning("[cs-dispatch] presence lookup failed", exc_info=True)
        return None

    if values is None or len(values) != len(ids):
        logger.warning("[cs-dispatch] presence lookup returned malformed result")
        return None
    return {agent_id for agent_id, value in zip(ids, values) if _decode(value)}


def _heartbeat_write(client: Any, key: str) -> Any:
    return client.setex(key, CS_DISPATCHER_HEARTBEAT_TTL_SECONDS, "1")


async def write_dispatcher_heartbeat(instance_id: str) -> bool:
    """写入 dispatcher 心跳（TTL 30 秒）；失败返回 ``False``，不抛异常。"""
    instance_id = str(instance_id or "").strip()
    if not instance_id:
        return False
    client = _redis_client()
    if client is None:
        return False
    try:
        stored = await asyncio.to_thread(
            _heartbeat_write, client, dispatcher_heartbeat_key(instance_id)
        )
    except Exception:
        logger.warning("[cs-dispatch] heartbeat write failed", exc_info=True)
        return False
    return stored is not False
