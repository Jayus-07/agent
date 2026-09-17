"""customer_service/typing_state.py — 「输入中」瞬态状态（双向）

转人工会话里坐席/用户正在输入的短时信号：写方 set_typing，读方
is_typing。生命周期由 TTL 兜底（持续输入即续期，停止后 5s 自然消失，
无需显式清除），瞬态数据不落库、不入事件流。

存储选型：Redis SETEX 优先（多 worker 读写一致 + TTL 原生过期）；
Redis 不可用降级进程内 dict + monotonic 过期（单 worker 语义等价）。
允许极端情况下信号丢失（最坏表现为提示不显示），不做重试/补偿。
"""
from __future__ import annotations

import time

TYPING_TTL_SECONDS = 5

# 降级存储：key -> 过期时刻（monotonic）
_local_state: dict[str, float] = {}


def _key(side: str, conversation_id: str) -> str:
    return f"cs:typing:{side}:{conversation_id}"


def set_typing(side: str, conversation_id: str) -> None:
    """标记 side（agent / user）正在输入；TTL 内可被 is_typing 读到。"""
    key = _key(side, conversation_id)
    try:
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is not None:
            r.setex(key, TYPING_TTL_SECONDS, "1")
            return
    except Exception:
        pass  # Redis 故障降级进程内存，瞬态信号失败不影响业务链路
    _local_state[key] = time.monotonic() + TYPING_TTL_SECONDS


def is_typing(side: str, conversation_id: str) -> bool:
    """查询 side 是否仍在输入 TTL 窗口内。"""
    key = _key(side, conversation_id)
    try:
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is not None:
            return bool(r.exists(key))
    except Exception:
        pass
    expiry = _local_state.get(key)
    if expiry is None:
        return False
    if expiry <= time.monotonic():
        _local_state.pop(key, None)
        return False
    return True
