"""dispatch/event_relay.py — 派单事件的提交后广播。

分工（方案 §六 P6 第 6 步 / P8 扩展点）：

- 事件**在同一派单事务内落库**（``CSEvent``，``outbox_status='pending'``），
  保证"工单已绑定但进程崩溃"也能从持久事件恢复通知。
- 事务提交后由本模块做一次 fire-and-forget 广播（``persist=False``，不二次
  落库、不占用新的 ``event_id``）。
- 完整 outbox relay（按 ``outbox_status`` 扫描重发、标记 ``published``、
  outbox lag 指标）属于 P8；P6 只提供这一次广播，且广播失败**绝不**影响
  已提交的绑定。
"""
from __future__ import annotations

from backend.shared.logger import logger


def publish_persisted_event(envelope: dict) -> bool:
    """广播一条已经落库的事件；失败只记日志并返回 ``False``。

    envelope 必须带 ``type`` 与 ``event_id``：``event_id`` 与持久行一致，
    客户端据此去重（方案 §五 第 8 步）。
    """
    event_type = str(envelope.get("type") or "").strip()
    if not event_type or not envelope.get("event_id"):
        logger.warning("[cs-dispatch] refuse to publish malformed event envelope")
        return False

    try:
        from backend.customer_service.realtime import get_agent_hub
    except Exception:
        logger.warning("[cs-dispatch] agent hub unavailable", exc_info=True)
        return False

    payload = {key: value for key, value in envelope.items() if key != "type"}
    try:
        get_agent_hub().publish(event_type, persist=False, **payload)
    except Exception:
        logger.warning("[cs-dispatch] event broadcast failed", exc_info=True)
        return False
    return True
