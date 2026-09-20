"""dispatch/outbox.py — 客服生命周期事件的持久 outbox（P7 写入 / P8 投递）。

分工：

- ``append_event``：**在业务事务内**写一条 ``customer_service.events``
  （``outbox_status='pending'``）。事件与状态变更同事务提交，因此
  「工单已绑定但进程崩溃」不会丢通知 —— 提交即存在。
- ``relay_pending_events``：**事务提交后**由 worker 扫描 ``pending`` 行，
  按 ``id`` 升序发布到 Redis（同会话事件顺序 = seq 顺序），发布成功才把该行
  标成 ``published`` 并写 ``published_at``。

为什么不用 P6 的 ``event_relay.publish_persisted_event`` 就够了：那只做了一次
fire-and-forget 广播，进程在「提交成功」与「广播成功」之间被杀就会永久丢通知。
P8 的 relay 把广播变成可重试的持久动作（方案 §六 P8 完成标准「杀死 dispatcher
后事件不丢」）。

去重语义：``event_id`` 全局唯一，客户端按 ``event_id`` 去重；relay 自身用
``FOR UPDATE SKIP LOCKED`` 分片，绝不会有两个副本同时发布同一行。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.cs_dispatch import CS_OUTBOX_RELAY_BATCH_LIMIT
from backend.customer_service.dispatch import repository
from backend.customer_service.models.event import CSEvent
from backend.shared.logger import logger


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def append_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    conversation_id: str,
    type: str,
    payload: dict,
    handoff_id: str | None = None,
    target_agent_id: str | None = None,
    actor_user_id: str | None = None,
    now: datetime,
) -> CSEvent:
    """把一条生命周期事件写入 outbox（``pending``）；返回 ORM 行。

    只 ``session.add``，不 flush、不 commit —— 由调用方所在事务决定提交时机，
    保证「状态变更与事件同事务」。
    """
    event = CSEvent(
        conversation_id=conversation_id,
        event_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        handoff_id=handoff_id,
        target_agent_id=target_agent_id,
        actor_user_id=actor_user_id,
        event_seq=None,
        type=type,
        payload=payload,
        outbox_status="pending",
        created_at=now,
    )
    session.add(event)
    return event


def envelope_for(event: CSEvent, *, extra: dict | None = None) -> dict:
    """把 outbox 行还原成 WS 帧封套（与 P6 广播格式一致）。"""
    payload = dict(event.payload or {})
    envelope = {
        "type": event.type,
        "event_id": event.event_id,
        "seq": event.id,
        "ts": _iso(event.created_at),
        "target_agent_id": event.target_agent_id,
        "tenant_id": event.tenant_id,
        **payload,
    }
    if extra:
        envelope.update(extra)
    return envelope


@dataclass(frozen=True)
class RelayResult:
    """一轮 relay 的结果（worker 观测与测试共用）。"""

    scanned: int = 0
    published: int = 0
    failed: int = 0
    oldest_pending_lag_seconds: float | None = None

    @property
    def status(self) -> str:
        if self.scanned == 0:
            return "empty"
        return "published" if self.failed == 0 else "partial"


async def _publish(event: CSEvent) -> bool:
    """把单条持久事件投递到实时通道；未送达返回 ``False``（下一轮重试）。

    走 ``AgentHub.publish_envelope``（PUBLISH 回执语义），而不是 P6 的
    fire-and-forget：只有拿到订阅者数才知道到底投出去没有，否则
    「标 published 但其实没人收到」会让 P8 的可靠性承诺变成空话。
    """
    try:
        from backend.customer_service.realtime import AgentHub
    except Exception:
        logger.warning("[cs-outbox] realtime hub unavailable", exc_info=True)
        return False
    return await AgentHub.publish_envelope(envelope_for(event))


async def relay_pending_events(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int | None = None,
) -> RelayResult:
    """扫描并投递待发布事件，成功者标 ``published``。

    事务边界刻意**每批一次提交**：投递失败的行保持 ``pending``，下一轮
    （1 秒后）自动重试；不会因为一条坏事件阻塞后面的事件。
    发布发生在持有行锁期间：多副本下另一副本被 ``SKIP LOCKED`` 跳过，
    因此不存在「同一行被两个副本同时发布」。
    """
    batch = limit if limit is not None else CS_OUTBOX_RELAY_BATCH_LIMIT
    published = 0
    failed = 0

    async with session.begin():
        events = await repository.lock_pending_outbox_events(session, limit=batch)
        scanned = len(events)
        for event in events:
            if await _publish(event):
                event.outbox_status = "published"
                event.published_at = now
                published += 1
            else:
                failed += 1
        if scanned:
            await session.flush()

    lag: float | None = None
    if failed or published:
        async with session.begin():
            oldest = await repository.oldest_pending_outbox_created_at(session)
        if oldest is not None:
            lag = max(0.0, (now - oldest).total_seconds())

    if failed:
        logger.warning(
            "[cs-outbox] %s/%s events deferred to the next relay tick",
            failed,
            scanned,
        )
    return RelayResult(
        scanned=scanned,
        published=published,
        failed=failed,
        oldest_pending_lag_seconds=lag,
    )
