"""ConversationContext — 会话级跨轮结构化上下文（P2.1/D1）。

职责边界（P2.2 原则，勿颠倒）：
    TravelBrief            = Travel 域详细状态源（权威，含 checkpointer）
    ConversationContext    = 主 Router 可读取的跨域摘要上下文（本轮新增）

设计要点：
- 主键必须是 ``(tenant_id, user_id, conversation_id)`` 三元组，
  禁止只用 session_id —— 防多用户/多租户/同用户不同会话串上下文。
- 日期模型沿用 TravelBrief 事实源：``start_date + days``，
  **不**新增 date_range 持久化字段（end_date 运行时计算），避免双写冲突。
- 第一版为进程内存储（TTL + LRU 上限）；不引入新持久化。
- ``last_verified_source_ids`` 只存证据 ID，不存模型生成 answer（P2.10）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field, replace

from backend.config.conversation_context import (
    CONVERSATION_CONTEXT_MAX_ENTRIES,
    CONVERSATION_CONTEXT_TTL_SECONDS,
)
from backend.shared.logger import logger

# 可跨轮携带的结构化槽位（P2.1 推荐字段子集；日期沿用 start_date+days）
CONTEXT_SLOT_FIELDS = (
    "destination",
    "cities",
    "origin",
    "start_date",
    "days",
    "party_size",
    "budget_cny",
    "preferences",
    "must_go",
    "avoid",
    "current_topic",
)


@dataclass
class ConversationContext:
    """单个 (tenant, user, conversation) 的跨轮摘要状态。"""

    tenant_id: str
    user_id: str
    conversation_id: str

    # ── 结构化槽位（Travel 摘要为主，跨域可读）──
    destination: str = ""
    cities: list[str] = field(default_factory=list)
    origin: str = ""
    start_date: str = ""
    days: int | None = None
    party_size: int | None = None
    budget_cny: float | None = None
    preferences: list[str] = field(default_factory=list)
    must_go: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    current_topic: str = ""

    # ── Evidence 复用（P2.10）：只存 ID，不存生成内容 ──
    last_verified_source_ids: list[str] = field(default_factory=list)
    source_context_fingerprint: str = ""

    updated_at: float = field(default_factory=time.time)

    # ── 槽位合并 ──

    def merge_slots(self, slots: dict) -> None:
        """把上游（如 TravelBrief 摘要）的槽位合并进上下文。

        只接受 CONTEXT_SLOT_FIELDS 内的键；None 值跳过（不清空既有值）。
        """
        for key in CONTEXT_SLOT_FIELDS:
            value = slots.get(key)
            if value is None:
                continue
            setattr(self, key, value)
        self.updated_at = time.time()

    def set_topic(self, topic: str) -> None:
        if topic:
            self.current_topic = topic
            self.updated_at = time.time()

    # ── P2.5 显式切换：overwrite 清理 ──

    def overwrite_destination(self, new_destination: str, *, keep_days: bool = True) -> None:
        """显式切换目的地：替换 destination，清除与旧目的地强绑定的状态。

        清理项（P2.5）：cities / last_verified_source_ids /
        source_context_fingerprint。days 等通用槽位按语义保留
        （「5 天游云南，改成成都」→ days=5 保留）。
        """
        self.destination = new_destination
        self.cities = []
        self.last_verified_source_ids = []
        self.source_context_fingerprint = ""
        if not keep_days:
            self.days = None
        self.updated_at = time.time()

    def clear_evidence(self) -> None:
        """destination changed / topic changed / explicit reset 时清旧证据。"""
        self.last_verified_source_ids = []
        self.source_context_fingerprint = ""
        self.updated_at = time.time()

    # ── fingerprint（P2.10）──

    def fingerprint(self) -> str:
        """上下文指纹：反映 destination/cities/current_topic。

        用于判定上一轮 evidence 是否与当前上下文兼容。
        """
        raw = json.dumps(
            {
                "destination": self.destination,
                "cities": sorted(self.cities),
                "topic": self.current_topic,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def touch_evidence(self, source_ids: list[str]) -> None:
        """记录本轮通过校验的证据 ID，并同步刷新指纹。"""
        self.last_verified_source_ids = list(dict.fromkeys(source_ids))[:20]
        self.source_context_fingerprint = self.fingerprint()
        self.updated_at = time.time()

    def evidence_compatible(self) -> bool:
        """历史证据是否仍与当前上下文兼容（指纹一致）。"""
        return bool(
            self.last_verified_source_ids
            and self.source_context_fingerprint == self.fingerprint()
        )

    def snapshot(self) -> dict:
        """供 Resolver / Trace 消费的只读快照（不含敏感明细）。"""
        return {
            "destination": self.destination,
            "cities": list(self.cities),
            "origin": self.origin,
            "start_date": self.start_date,
            "days": self.days,
            "party_size": self.party_size,
            "budget_cny": self.budget_cny,
            "preferences": list(self.preferences),
            "must_go": list(self.must_go),
            "avoid": list(self.avoid),
            "current_topic": self.current_topic,
        }


class ConversationContextStore:
    """进程内上下文存储：三元组主键 + TTL + LRU 上限。

    线程安全（主图与域图节点在不同线程/loop 执行）。查询未命中返回
    空上下文（惰性创建），不抛异常。
    """

    def __init__(self, *, ttl_seconds: int = CONVERSATION_CONTEXT_TTL_SECONDS,
                 max_entries: int = CONVERSATION_CONTEXT_MAX_ENTRIES):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: dict[tuple[str, str, str], ConversationContext] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(tenant_id: str, user_id: str, conversation_id: str) -> tuple[str, str, str]:
        return (tenant_id or "", user_id or "", conversation_id or "")

    def get(self, tenant_id: str, user_id: str, conversation_id: str) -> ConversationContext:
        """取上下文；不存在/过期则创建新的（调用方可继续合并槽位）。"""
        key = self._key(tenant_id, user_id, conversation_id)
        now = time.time()
        with self._lock:
            ctx = self._data.get(key)
            if ctx is not None and now - ctx.updated_at > self._ttl:
                # 过期即弃：防陈旧目的地污染新一轮对话
                self._data.pop(key, None)
                ctx = None
            if ctx is None:
                ctx = ConversationContext(
                    tenant_id=key[0], user_id=key[1], conversation_id=key[2]
                )
            self._data.pop(key, None)  # 重新插入以维持 LRU 顺序
            self._data[key] = ctx
            self._evict_locked(now)
            return ctx

    def peek(self, tenant_id: str, user_id: str, conversation_id: str) -> ConversationContext | None:
        """只读探测（不创建）。"""
        key = self._key(tenant_id, user_id, conversation_id)
        with self._lock:
            ctx = self._data.get(key)
            if ctx is None or time.time() - ctx.updated_at > self._ttl:
                return None
            return ctx

    def reset(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        key = self._key(tenant_id, user_id, conversation_id)
        with self._lock:
            self._data.pop(key, None)

    def _evict_locked(self, now: float) -> None:
        # 先清过期
        expired = [k for k, v in self._data.items() if now - v.updated_at > self._ttl]
        for k in expired:
            self._data.pop(k, None)
        # 再按 LRU 淘汰
        while len(self._data) > self._max:
            oldest_key = next(iter(self._data))
            self._data.pop(oldest_key, None)


# 进程级单例（图节点与 runner 共享同一份上下文）
_store: ConversationContextStore | None = None
_store_lock = threading.Lock()


def get_conversation_context_store() -> ConversationContextStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ConversationContextStore()
    return _store


def sync_travel_brief_to_context(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    brief: dict,
) -> None:
    """TravelBrief 摘要槽位 → ConversationContext（P2.2 单向同步）。

    TravelBrief 是 Travel 域权威状态；本函数只把跨轮 follow-up 需要的
    槽位**单向**同步到摘要上下文，绝不反向覆盖。同步软失败（不抛）。
    """
    try:
        if not conversation_id:
            return
        slots = {
            key: brief.get(key)
            for key in CONTEXT_SLOT_FIELDS
            if brief.get(key) is not None
        }
        if not slots:
            return
        store = get_conversation_context_store()
        ctx = store.get(tenant_id, user_id, conversation_id)
        old_dest = ctx.destination
        ctx.merge_slots(slots)
        # 目的地变化 = 上下文不兼容，旧 evidence 失效（P2.10）
        if old_dest and slots.get("destination") and slots["destination"] != old_dest:
            ctx.clear_evidence()
        logger.debug(
            "[ConversationContext] travel brief 同步: dest=%s cities=%s conv=%s",
            ctx.destination, ctx.cities, conversation_id,
        )
    except Exception as exc:  # noqa: BLE001 — 上下文同步失败绝不影响主链
        logger.warning("[ConversationContext] travel brief 同步失败（软降级）: %s", exc)
