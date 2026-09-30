"""context_repository.py — ConversationContext 分布式仓库（STOP G1/G2）。

职责冻结（docs/2026-09-24-TravelDistributedContext-STOPG0-审计与设计.md §六）：

    ConversationContextRepository (Protocol)
        ├── MemoryConversationContextRepository   单测 / 本地开发 / 显式降级
        └── RedisConversationContextRepository    生产（shared hot state）

设计要点：
- 业务层只依赖统一接口与 ContextMutation，不感知 Redis key / JSON /
  WATCH/TTL —— 「读-改-写整对象覆盖」禁止作为并发写路径。
- mutation 应用逻辑 = ``apply_mutation`` 纯函数（单一事实源），Memory 在
  进程锁内执行，Redis 在 WATCH/MULTI/EXEC 乐观锁事务内执行——两个
  backend 行为完全一致（T7 并发用例互相印证）。
- 并发语义：``version`` 单调递增；RESOLVE_TRAVEL_PENDING 以
  expected_question_id 做 CAS（stale 不得清新 pending）；COMPLETED /
  CANCEL / CLEAR 以 expected_run_id 做 CAS（stale run 不得覆盖新 run）。
- TTL 复用 CONVERSATION_CONTEXT_TTL_SECONDS；仅成功 mutation 刷新
  （Redis SETEX 与写同事务；失败 / stale / missing 不刷新）。
- shared ≠ durable：Redis 键在 allkeys-lru 下可被驱逐，丢失路径 =
  checkpoint resume / reconstruct / fresh（STOP F 语义，不 500）。
"""
from __future__ import annotations

import copy
import json
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Protocol

from backend.shared.logger import logger
from backend.orchestration.context import context_metrics as _m
from backend.orchestration.context.conversation_context import (
    CONTEXT_SLOT_FIELDS,
    ConversationContext,
    _new_question_id,
)


def _safe(metric_fn) -> None:
    """指标埋点软失败：观测绝不影响主链。"""
    try:
        metric_fn()
    except Exception:  # noqa: BLE001
        pass


def _stale_kind(mtype: str) -> str:
    """stale 拒绝的分类（低基数：pending | run）。"""
    return ("pending" if mtype in (MutationType.RESOLVE_TRAVEL_PENDING,
                                   MutationType.RESOLVE_BOOKING_INTENT)
            else "run")

# ── Mutation 类型（任务书 §十一：mutation 表达业务意图，非整对象覆盖）──


class MutationType:
    MARK_TURN = "mark_turn"
    MERGE_TRAVEL_SUMMARY = "merge_travel_summary"
    PATCH_TRAVEL_SUMMARY = "patch_travel_summary"
    START_TRAVEL_RUN = "start_travel_run"
    SET_TRAVEL_STAGE = "set_travel_stage"
    SET_TRAVEL_PENDING = "set_travel_pending"
    RESOLVE_TRAVEL_PENDING = "resolve_travel_pending"
    # 交易挂起（Phase 5 / D2 两跳断修复）：预订/比价子图澄清期的参数挂起。
    # 与 travel pending 并列但独立——两个子图无 checkpointer，澄清期参数
    # 不在 PG，必须由会话上下文承载，否则用户答槽位值时掉域。
    SET_BOOKING_INTENT = "set_booking_intent"
    RESOLVE_BOOKING_INTENT = "resolve_booking_intent"
    MARK_TRAVEL_COMPLETED = "mark_travel_completed"
    CANCEL_TRAVEL_RUN = "cancel_travel_run"
    CLEAR_TRAVEL_RUN = "clear_travel_run"
    CLEAR_EVIDENCE = "clear_evidence"
    SET_TOPIC = "set_topic"
    OVERWRITE_DESTINATION = "overwrite_destination"
    SET_FUNNEL_CANDIDATES = "set_funnel_candidates"
    CLEAR_FUNNEL_CANDIDATES = "clear_funnel_candidates"
    APPLY_FOLLOWUP_RESOLUTION = "apply_followup_resolution"


# 允许在 context 不存在时惰性创建的 mutation（对齐原 get() 惰性创建语义；
# CAS 类 mutation 在无 context 时一律 missing，不得凭空创建再改）
_CREATING_MUTATIONS = frozenset({
    MutationType.MARK_TURN,
    MutationType.MERGE_TRAVEL_SUMMARY,
    MutationType.PATCH_TRAVEL_SUMMARY,
    MutationType.START_TRAVEL_RUN,
    # 交易挂起允许惰性创建：用户第一条消息就是「订酒店」，此前无上下文
    MutationType.SET_BOOKING_INTENT,
    MutationType.SET_TOPIC,
    MutationType.OVERWRITE_DESTINATION,
    MutationType.SET_FUNNEL_CANDIDATES,
    MutationType.CLEAR_FUNNEL_CANDIDATES,
    MutationType.APPLY_FOLLOWUP_RESOLUTION,
    MutationType.CLEAR_EVIDENCE,
})


@dataclass
class ContextMutation:
    """一次业务意图的原子变更。

    payload 可带并发控制键：
    - ``expected_run_id``：CAS，当前 run 不匹配 → stale（run 类写保护）
    - ``expected_question_id``：CAS，当前 pending 不匹配 → stale
    - ``expected_version``：CAS，上下文版本不匹配 → stale
    """

    type: str
    payload: dict = field(default_factory=dict)


@dataclass
class MutationResult:
    """mutate 结果。status 语义：

    - applied：已应用并落库（version+1、TTL 刷新）
    - noop：合法但无变化（如 RESOLVE 时本就无 pending），不落库不刷 TTL
    - stale：CAS 不匹配，**拒绝应用**（stale pending / stale run 保护）
    - missing：目标上下文不存在且该 mutation 不允许创建
    - conflict：乐观锁重试耗尽（Redis 并发极端场景）
    """

    status: str
    context: ConversationContext | None = None
    run_id: str = ""
    question_id: str = ""
    version: int = 0
    detail: str = ""


# ── mutation 应用（纯函数，Memory/Redis 共用，单一事实源）──


def _cas_check(ctx: ConversationContext, payload: dict) -> str:
    """统一 CAS 校验。返回空串=通过，否则返回 stale 原因。

    expected_run_id 在「当前无 run」时不前置拦截（交给分支内 noop 判定：
    取消/收尾一个已不存在的 run 是合法终态，而非并发冲突）。
    """
    expected_run = payload.get("expected_run_id")
    if (expected_run is not None and (ctx.travel_run_id or "")
            and ctx.travel_run_id != expected_run):
        return f"run {ctx.travel_run_id} != expected {expected_run}"
    expected_q = payload.get("expected_question_id")
    if expected_q is not None:
        current_q = (ctx.travel_pending or {}).get("question_id") or ""
        # 仅「有 pending 且 id 不匹配」才是 stale（新 pending 保护）；
        # 无 pending 时 resolve 是幂等终态，交分支内 noop
        if current_q and current_q != expected_q:
            return f"pending {current_q} != expected {expected_q}"
    expected_ver = payload.get("expected_version")
    if expected_ver is not None and int(ctx.version) != int(expected_ver):
        return f"version {ctx.version} != expected {expected_ver}"
    return ""


def apply_mutation(
    ctx: ConversationContext,
    mutation: ContextMutation,
    *,
    existed: bool = True,
) -> MutationResult:
    """把 mutation 应用到 ctx（就地改），返回结果。

    纯逻辑无 IO；调用方负责并发控制（Memory 锁 / Redis WATCH）。
    """
    mtype = mutation.type
    payload = mutation.payload or {}

    if mtype not in _CREATING_MUTATIONS and not existed:
        return MutationResult(status="missing", detail=mtype)

    stale_reason = _cas_check(ctx, payload)
    if stale_reason:
        return MutationResult(status="stale", context=ctx.copy(),
                              detail=f"{mtype}: {stale_reason}")

    if mtype == MutationType.MARK_TURN:
        ctx.mark_turn(
            domain=payload.get("domain") or "",
            intent=payload.get("intent") or "",
            action=payload.get("action") or "",
            pending_question=payload.get("pending_question"),
        )

    elif mtype == MutationType.MERGE_TRAVEL_SUMMARY:
        slots = {k: v for k, v in (payload.get("slots") or {}).items()
                 if k in CONTEXT_SLOT_FIELDS and v is not None}
        if not slots:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="empty slots")
        old_dest = ctx.destination
        ctx.merge_slots(slots)
        # 目的地变化 = 上下文不兼容，旧 evidence 失效（P2.10，对齐原同步）
        if old_dest and slots.get("destination") and slots["destination"] != old_dest:
            ctx.clear_evidence()

    elif mtype == MutationType.PATCH_TRAVEL_SUMMARY:
        slots = {k: v for k, v in (payload.get("slots") or {}).items()
                 if k in CONTEXT_SLOT_FIELDS and v is not None}
        if not slots:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="empty slots")
        ctx.merge_slots(slots)

    elif mtype == MutationType.START_TRAVEL_RUN:
        conv_hash8 = payload.get("conv_hash8") or ""
        ctx.travel_run_seq = int(ctx.travel_run_seq or 0) + 1
        ctx.travel_run_id = f"trv_{conv_hash8}_{ctx.travel_run_seq:03d}"
        ctx.travel_pending = None
        ctx.updated_at = time.time()

    elif mtype == MutationType.SET_TRAVEL_STAGE:
        stage = payload.get("stage") or ""
        if not stage:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="empty stage")
        only_from = payload.get("if_stage_in")
        if only_from is not None and ctx.travel_stage not in only_from:
            # 条件推进（如 completed 不回退 planned）：条件不符 = 合法 no-op
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="stage not in "
                                  f"{only_from} (current={ctx.travel_stage})")
        ctx.set_travel_stage(stage)

    elif mtype == MutationType.SET_TRAVEL_PENDING:
        run_id = payload.get("run_id") or ""
        requested = [s for s in (payload.get("requested_slots") or []) if s]
        # 写 pending 必须落在当前 run 上（run CAS：竞态下 NEW_RUN 已换 run
        # → stale，下一轮重进图会重新同步，观测可见）
        if (ctx.travel_run_id or "") != run_id:
            return MutationResult(
                status="stale", context=ctx.copy(),
                detail=f"set_pending: run {ctx.travel_run_id or '-'} "
                       f"!= target {run_id}")
        if not requested:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="empty slots")
        prev = ctx.travel_pending or {}
        if (prev.get("requested_slots") == requested
                and prev.get("run_id") == run_id):
            # 同一轮追问重发：保留 question_id（对齐 STOP F 同步逻辑）
            prev["question_id"] = prev.get("question_id") or _new_question_id()
            ctx.set_travel_pending(prev)
        else:
            ctx.set_travel_pending({
                "question_id": _new_question_id(),
                "run_id": run_id,
                "requested_slots": requested,
                "reason": payload.get("reason") or "missing_required",
                "created_at": time.time(),
            })

    elif mtype == MutationType.RESOLVE_TRAVEL_PENDING:
        expected_q = payload.get("expected_question_id")
        current = ctx.travel_pending or {}
        current_q = current.get("question_id") or ""
        if not current_q:
            # 本就无 pending：合法 no-op（对齐原「set(None)」终态，但不清
            # 别人刚写入的新 pending——CAS 保证）
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version)
        if expected_q is not None and current_q != expected_q:
            return MutationResult(
                status="stale", context=ctx.copy(),
                detail=f"resolve: pending {current_q} != expected {expected_q}")
        ctx.set_travel_pending(None)
        if ctx.travel_stage in ("", "slot"):
            ctx.set_travel_stage("planned")

    elif mtype == MutationType.SET_BOOKING_INTENT:
        route_mode = payload.get("route_mode") or ""
        kind = payload.get("kind") or ""
        missing = [s for s in (payload.get("missing_slots") or []) if s]
        if not route_mode or not kind or not missing:
            # 参数齐备时不该写挂起（调用方应发 RESOLVE）——缺失即无意义挂起
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version,
                                  detail="incomplete booking intent")
        prev = ctx.booking_intent or {}
        # 同一挂起（同域同类型）持续更新 → 保留 question_id：续填是同一件
        # 事的推进，不是新挂起；换域/换类型才算新挂起（旧 question_id 作废）
        if (prev.get("route_mode") == route_mode
                and prev.get("kind") == kind
                and prev.get("question_id")):
            question_id = prev["question_id"]
        else:
            question_id = _new_question_id()
        ctx.set_booking_intent({
            "question_id": question_id,
            "route_mode": route_mode,
            "kind": kind,
            "missing_slots": missing,
            "collected": dict(payload.get("collected") or {}),
            "reason": payload.get("reason") or "missing_required",
            "created_at": prev.get("created_at") or time.time(),
            "updated_at": time.time(),
        })

    elif mtype == MutationType.RESOLVE_BOOKING_INTENT:
        current = ctx.booking_intent or {}
        if not current:
            # 无挂起：合法终态（如用户直接给出完整信息，从未产生澄清）
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="no pending intent")
        expected_q = payload.get("expected_question_id")
        current_q = current.get("question_id") or ""
        if expected_q is not None and current_q and current_q != expected_q:
            return MutationResult(
                status="stale", context=ctx.copy(),
                detail=f"resolve intent: {current_q} != expected {expected_q}")
        ctx.set_booking_intent(None)

    elif mtype == MutationType.MARK_TRAVEL_COMPLETED:
        expected_run = payload.get("expected_run_id")
        if not (ctx.travel_run_id or ""):
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="no active run")
        if expected_run is not None and ctx.travel_run_id != expected_run:
            return MutationResult(
                status="stale", context=ctx.copy(),
                detail=f"completed: run {ctx.travel_run_id} "
                       f"!= expected {expected_run}")
        ctx.set_travel_stage("completed")
        ctx.set_travel_pending(None)

    elif mtype == MutationType.CANCEL_TRAVEL_RUN:
        # 取消当前 run：stage=cancelled + pending 清 + run 身份清；
        # travel_run_seq 保留（单调递增 → 下一 run 编号不撞号，任务书 §18）；
        # 摘要槽位保留（STOP F clear 契约：清 run + pending，保留摘要）。
        expected_run = payload.get("expected_run_id")
        if not (ctx.travel_run_id or ""):
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="no active run")
        if expected_run is not None and ctx.travel_run_id != expected_run:
            return MutationResult(
                status="stale", context=ctx.copy(),
                detail=f"cancel: run {ctx.travel_run_id} "
                       f"!= expected {expected_run}")
        cancelled_run = ctx.travel_run_id
        ctx.travel_stage = "cancelled"
        ctx.travel_pending = None
        ctx.travel_run_id = ""
        ctx.updated_at = time.time()
        return MutationResult(status="applied", context=ctx.copy(),
                              run_id=cancelled_run,
                              version=ctx.version + 1,
                              detail="cancelled")

    elif mtype == MutationType.CLEAR_TRAVEL_RUN:
        ctx.clear_travel_run()  # STOP F 冻结契约：seq=0，保留摘要槽位

    elif mtype == MutationType.CLEAR_EVIDENCE:
        ctx.clear_evidence()

    elif mtype == MutationType.SET_TOPIC:
        topic = payload.get("topic") or ""
        if not topic:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="empty topic")
        ctx.set_topic(topic)

    elif mtype == MutationType.OVERWRITE_DESTINATION:
        new_dest = payload.get("destination") or ""
        if not new_dest:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="empty dest")
        ctx.overwrite_destination(new_dest,
                                  keep_days=payload.get("keep_days", True))

    elif mtype == MutationType.SET_FUNNEL_CANDIDATES:
        candidates = payload.get("candidates") or []
        run_id = payload.get("run_id") or ""
        if not candidates:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version, detail="empty top")
        ctx.set_funnel_candidates(candidates, run_id)

    elif mtype == MutationType.CLEAR_FUNNEL_CANDIDATES:
        ctx.clear_funnel_candidates()

    elif mtype == MutationType.APPLY_FOLLOWUP_RESOLUTION:
        # 对齐 follow_up_resolver.apply_resolution_to_context 语义：
        # overwrite 优先，否则 resolved 时记 topic
        new_dest = payload.get("overwrite_destination")
        if new_dest:
            ctx.overwrite_destination(new_dest)
        elif payload.get("resolved") and payload.get("topic"):
            ctx.set_topic(payload["topic"])
        else:
            return MutationResult(status="noop", context=ctx.copy(),
                                  version=ctx.version)

    else:
        return MutationResult(status="stale", detail=f"unknown type {mtype}")

    return MutationResult(status="applied", context=ctx.copy(),
                          run_id=getattr(ctx, "travel_run_id", ""),
                          version=ctx.version + 1)


# ── Repository Protocol ──


class ConversationContextRepository(Protocol):
    """会话上下文仓库统一接口（业务层唯一可见面）。"""

    def get(self, tenant_id: str, user_id: str,
            conversation_id: str) -> ConversationContext | None:
        """读取快照副本；不存在返回 None（不创建）。"""
        ...

    def peek(self, tenant_id: str, user_id: str,
             conversation_id: str) -> ConversationContext | None:
        """与 get 等价的只读探测（保留名以贴调用方语义）。"""
        ...

    def mutate(self, tenant_id: str, user_id: str, conversation_id: str,
               mutation: ContextMutation) -> MutationResult:
        """原子应用一次业务意图 mutation。"""
        ...

    def save(self, context: ConversationContext, *,
             expected_version: int | None = None) -> ConversationContext:
        """CAS 全量提交（低频兜底；核心写路径必须走 mutate）。"""
        ...

    def delete(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        """显式删除（会话 reset）。"""
        ...

    @property
    def status(self) -> dict:
        """backend 观测态：{"backend": str, "status": healthy|degraded|disabled}。"""
        ...


# ── Memory 实现（单测 / 本地开发 / 显式降级）──


class MemoryConversationContextRepository:
    """进程内实现：三元组 key + TTL + LRU + 锁内原子 mutation。"""

    backend_name = "memory"

    def __init__(self, *, ttl_seconds: int | None = None,
                 max_entries: int | None = None):
        from backend.config.conversation_context import (
            CONVERSATION_CONTEXT_MAX_ENTRIES,
            CONVERSATION_CONTEXT_TTL_SECONDS,
        )

        self._ttl = (CONVERSATION_CONTEXT_TTL_SECONDS if ttl_seconds is None
                     else ttl_seconds)
        self._max = (CONVERSATION_CONTEXT_MAX_ENTRIES if max_entries is None
                     else max_entries)
        self._data: dict[tuple[str, str, str], ConversationContext] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(tenant_id: str, user_id: str, conversation_id: str):
        return (tenant_id or "", user_id or "", conversation_id or "")

    def get(self, tenant_id: str, user_id: str,
            conversation_id: str) -> ConversationContext | None:
        key = self._key(tenant_id, user_id, conversation_id)
        now = time.time()
        t0 = time.monotonic()
        with self._lock:
            ctx = self._data.get(key)
            if ctx is None or now - ctx.updated_at > self._ttl:
                if ctx is not None:
                    self._data.pop(key, None)
                _safe(lambda: _m.conversation_context_read_total.labels(
                    "memory", "miss").inc())
                _safe(lambda: _m.conversation_context_operation_seconds.labels(
                    "get").observe(time.monotonic() - t0))
                return None
            _safe(lambda: _m.conversation_context_read_total.labels(
                "memory", "hit").inc())
            _safe(lambda: _m.conversation_context_operation_seconds.labels(
                "get").observe(time.monotonic() - t0))
            return ctx.copy()

    peek = get

    def mutate(self, tenant_id: str, user_id: str, conversation_id: str,
               mutation: ContextMutation) -> MutationResult:
        key = self._key(tenant_id, user_id, conversation_id)
        now = time.time()
        t0 = time.monotonic()
        with self._lock:
            existed_ctx = self._data.get(key)
            existed = existed_ctx is not None and now - existed_ctx.updated_at <= self._ttl
            if existed_ctx is not None and not existed:
                self._data.pop(key, None)
                existed_ctx = None
            if existed_ctx is None:
                if mutation.type not in _CREATING_MUTATIONS:
                    _safe(lambda: _m.conversation_context_mutation_total.labels(
                        mutation.type, "missing").inc())
                    return MutationResult(status="missing", detail=mutation.type)
                ctx = ConversationContext(tenant_id=key[0], user_id=key[1],
                                          conversation_id=key[2])
            else:
                ctx = existed_ctx
            result = apply_mutation(ctx, mutation, existed=existed_ctx is not None)
            if result.status == "applied":
                ctx.version += 1
                ctx.updated_at = time.time()
                self._data.pop(key, None)  # 重新插入维持 LRU 序
                self._data[key] = ctx
                self._evict_locked(now)
                result.version = ctx.version
                result.run_id = ctx.travel_run_id
                result.context = ctx.copy()
                _safe(lambda: _m.conversation_context_write_total.labels(
                    "memory").inc())
            elif result.context is None:
                result.context = ctx.copy()
                result.version = ctx.version
            if result.status == "stale":
                _safe(lambda: _m.record_stale(_stale_kind(mutation.type)))
            _safe(lambda: _m.conversation_context_mutation_total.labels(
                mutation.type, result.status).inc())
            _safe(lambda: _m.conversation_context_operation_seconds.labels(
                "mutate").observe(time.monotonic() - t0))
            return result

    def save(self, context: ConversationContext, *,
             expected_version: int | None = None) -> ConversationContext:
        key = self._key(context.tenant_id, context.user_id,
                        context.conversation_id)
        now = time.time()
        with self._lock:
            current = self._data.get(key)
            if expected_version is not None:
                if current is None or current.version != expected_version:
                    raise ContextConflictError(
                        f"save: version mismatch on {key}")
            stored = context.copy()
            stored.version = (current.version if current else 0) + 1
            stored.updated_at = now
            self._data.pop(key, None)
            self._data[key] = stored
            self._evict_locked(now)
            return stored.copy()

    def delete(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        with self._lock:
            self._data.pop(self._key(tenant_id, user_id, conversation_id), None)

    @property
    def status(self) -> dict:
        return {"backend": self.backend_name, "status": "healthy"}

    def _evict_locked(self, now: float) -> None:
        expired = [k for k, v in self._data.items()
                   if now - v.updated_at > self._ttl]
        for k in expired:
            self._data.pop(k, None)
        while len(self._data) > self._max:
            self._data.pop(next(iter(self._data)), None)


class ContextConflictError(RuntimeError):
    """save CAS 失败（乐观锁冲突）。"""


class ContextBackendUnavailable(RuntimeError):
    """REQUIRE_SHARED=true 时 Redis 不可用（fail-closed 信号）。"""


# ── Redis 实现（生产；WATCH/MULTI/EXEC 乐观锁 + Lua-free 单键 CAS）──

import random

_RETRY_ON_WATCH_ERROR = 8
_WATCH_RETRY_BACKOFF = (0.001, 0.006)  # 随机退避区间（秒），错开竞争窗口


class RedisConversationContextRepository:
    """Redis shared 实现。业务模块禁止绕过本类直接 redis.Redis(...)。

    - 复用 infra.redis.client.get_redis() 进程单例（连接池/超时/cooldown）
    - key：{prefix}conversation_context:v1:{t}:{u}:{c}（quote 编码防注入）
    - value：JSON（禁 pickle），schema_version + version
    - 写：WATCH 读 → apply_mutation（纯函数）→ MULTI SETEX 提交；
      WatchError 重试；仅 applied 落库（= 刷 TTL）
    - 降级策略由 CONVERSATION_CONTEXT_REQUIRE_SHARED 决定（见 _policy）
    """

    backend_name = "redis"

    def __init__(self, *, fallback: MemoryConversationContextRepository | None = None,
                 require_shared: bool | None = None, ttl_seconds: int | None = None):
        from backend.config.conversation_context import (
            CONVERSATION_CONTEXT_REQUIRE_SHARED,
            CONVERSATION_CONTEXT_TTL_SECONDS,
        )
        from backend.config.redis import REDIS_KEY_PREFIX

        self._fallback = fallback or MemoryConversationContextRepository()
        self._require_shared = (CONVERSATION_CONTEXT_REQUIRE_SHARED
                                if require_shared is None else require_shared)
        self._ttl = (CONVERSATION_CONTEXT_TTL_SECONDS if ttl_seconds is None
                     else ttl_seconds)
        self._prefix = REDIS_KEY_PREFIX
        self._degraded = False

    # ── key / 序列化 ──

    def _key(self, tenant_id: str, user_id: str, conversation_id: str) -> str:
        enc = lambda s: urllib.parse.quote(s or "", safe="")  # noqa: E731
        return (f"{self._prefix}conversation_context:v1:"
                f"{enc(tenant_id)}:{enc(user_id)}:{enc(conversation_id)}")

    @staticmethod
    def _decode(raw) -> ConversationContext | None:
        if not raw:
            return None
        try:
            return ConversationContext.from_dict(json.loads(raw))
        except (ValueError, TypeError):
            # schema 不兼容 / 半写损坏：视为 miss（reconstruct/fresh 兜底），
            # 绝不让单个坏键毒化会话
            logger.warning("[ConversationContext] redis value 解码失败，按 miss 处理")
            return None

    # ── 降级策略（任务书 §八）──

    def _policy(self, operation: str, exc: Exception):
        """Redis 操作失败时的统一策略：

        - REQUIRE_SHARED=true → fail-closed：抛 ContextBackendUnavailable
          （调用方软失败路径吞掉 → 确定性 miss/不落库，绝不假写 memory）
        - REQUIRE_SHARED=false → 降级进程内 memory（warning + degraded 标记）
        """
        self._degraded = True
        _safe(lambda: _m.conversation_context_backend_error_total.labels(
            "redis", operation).inc())
        if self._require_shared:
            logger.error("[ConversationContext] event=conversation_context."
                         "backend_error op=%s require_shared=true fail-closed: %s",
                         operation, exc)
            raise ContextBackendUnavailable(str(exc)) from exc
        logger.warning(
            "[ConversationContext] event=conversation_context.backend_degraded "
            "op=%s → fallback memory: %s", operation, exc)
        _safe(lambda: _m.conversation_context_fallback_total.labels(
            "redis_error").inc())
        return None

    @property
    def status(self) -> dict:
        """backend 观测态。STOP H-D3 修复：healthy 必须以「当前可达」为准，
        而非「尚未观察到失败」——client 不可得（连不上/cooldown 中）时
        如实报 degraded，禁止误报 healthy。"""
        from backend.config.redis import REDIS_ENABLED
        if not REDIS_ENABLED:
            return {"backend": self.backend_name, "status": "disabled"}
        if self._client() is None or self._degraded:
            return {"backend": self.backend_name, "status": "degraded"}
        return {"backend": self.backend_name, "status": "healthy"}

    def _client(self):
        from backend.infra.redis.client import get_redis
        return get_redis()

    # ── 读 ──

    def get(self, tenant_id: str, user_id: str,
            conversation_id: str) -> ConversationContext | None:
        r = self._client()
        t0 = time.monotonic()
        if r is not None:
            try:
                ctx = self._decode(r.get(self._key(tenant_id, user_id,
                                                   conversation_id)))
                _safe(lambda: _m.conversation_context_read_total.labels(
                    "redis", "hit" if ctx is not None else "miss").inc())
                _safe(lambda: _m.conversation_context_operation_seconds.labels(
                    "get").observe(time.monotonic() - t0))
                return ctx
            except Exception as exc:  # noqa: BLE001 — redis 异常统一策略
                # require_shared=true → _policy raise（fail-closed，调用方
                # 软失败路径吞掉 = 确定性 miss）；false → 落 fallback
                self._policy("get", exc)
        return self._fallback.get(tenant_id, user_id, conversation_id)

    peek = get

    # ── 写 ──

    def mutate(self, tenant_id: str, user_id: str, conversation_id: str,
               mutation: ContextMutation) -> MutationResult:
        from redis.exceptions import WatchError

        r = self._client()
        if r is None:
            if self._require_shared:
                logger.error("[ConversationContext] event=conversation_context."
                             "backend_error op=mutate require_shared=true "
                             "fail-closed: redis unavailable")
                _safe(lambda: _m.conversation_context_backend_error_total.labels(
                    "redis", "mutate").inc())
                raise ContextBackendUnavailable("redis unavailable")
            logger.warning("[ConversationContext] event=conversation_context."
                           "backend_degraded op=mutate → fallback memory")
            self._degraded = True
            _safe(lambda: _m.conversation_context_backend_error_total.labels(
                "redis", "mutate").inc())
            _safe(lambda: _m.conversation_context_fallback_total.labels(
                "redis_unavailable").inc())
            return self._fallback.mutate(tenant_id, user_id, conversation_id,
                                         mutation)
        key = self._key(tenant_id, user_id, conversation_id)
        t0 = time.monotonic()
        try:
            for _ in range(_RETRY_ON_WATCH_ERROR):
                with r.pipeline() as pipe:
                    try:
                        pipe.watch(key)
                        raw = pipe.get(key)
                        existed = raw is not None
                        ctx = self._decode(raw)
                        if ctx is None:
                            if not existed:
                                if mutation.type not in _CREATING_MUTATIONS:
                                    return MutationResult(status="missing",
                                                          detail=mutation.type)
                                ctx = ConversationContext(
                                    tenant_id=tenant_id or "",
                                    user_id=user_id or "",
                                    conversation_id=conversation_id or "")
                            else:
                                # 坏键：重置为空上下文再应用（重建而非毒化）
                                ctx = ConversationContext(
                                    tenant_id=tenant_id or "",
                                    user_id=user_id or "",
                                    conversation_id=conversation_id or "")
                        result = apply_mutation(ctx, mutation, existed=existed)
                        if result.status != "applied":
                            if result.status == "stale":
                                _safe(lambda: _m.record_stale(
                                    _stale_kind(mutation.type)))
                            _safe(lambda: _m.conversation_context_mutation_total.labels(
                                mutation.type, result.status).inc())
                            return result
                        ctx.version += 1
                        ctx.updated_at = time.time()
                        pipe.multi()
                        pipe.setex(key, self._ttl,
                                   json.dumps(ctx.to_dict(),
                                              ensure_ascii=False))
                        pipe.execute()
                        result.version = ctx.version
                        result.run_id = ctx.travel_run_id
                        result.context = ctx.copy()
                        _safe(lambda: _m.conversation_context_mutation_total.labels(
                            mutation.type, "applied").inc())
                        _safe(lambda: _m.conversation_context_write_total.labels(
                            "redis").inc())
                        _safe(lambda: _m.conversation_context_operation_seconds.labels(
                            "mutate").observe(time.monotonic() - t0))
                        return result
                    except WatchError:
                        # 并发写冲突：随机退避后重读重试（version 单调保证）
                        time.sleep(random.uniform(*_WATCH_RETRY_BACKOFF))
                        continue
        except ContextBackendUnavailable:
            raise
        except ContextConflictError:
            raise
        except (ConnectionError, TimeoutError) as exc:  # noqa: BLE001
            # redis 层连接/超时异常统一策略（apply_mutation 的程序性 bug
            # 不在此列，直接抛出防掩盖）
            self._policy("mutate", exc)
            if self._require_shared:
                raise
            return self._fallback.mutate(tenant_id, user_id, conversation_id,
                                         mutation)
        # 重试耗尽：并发冲突，拒绝应用（下一轮请求会带最新快照重做）
        logger.warning("[ConversationContext] event=conversation_context.conflict "
                       "op=mutate retries_exhausted type=%s", mutation.type)
        _safe(lambda: _m.conversation_context_conflict_total.labels(
            "mutate").inc())
        _safe(lambda: _m.conversation_context_mutation_total.labels(
            mutation.type, "conflict").inc())
        return MutationResult(status="conflict", detail="retries exhausted")

    def save(self, context: ConversationContext, *,
             expected_version: int | None = None) -> ConversationContext:
        from redis.exceptions import WatchError

        r = self._client()
        if r is None:
            if self._require_shared:
                raise ContextBackendUnavailable("redis unavailable")
            return self._fallback.save(context, expected_version=expected_version)
        key = self._key(context.tenant_id, context.user_id,
                        context.conversation_id)
        try:
            for _ in range(_RETRY_ON_WATCH_ERROR):
                with r.pipeline() as pipe:
                    try:
                        pipe.watch(key)
                        raw = pipe.get(key)
                        current = self._decode(raw)
                        current_version = current.version if current else 0
                        if (expected_version is not None
                                and current_version != expected_version):
                            raise ContextConflictError(
                                f"save: version {current_version} "
                                f"!= expected {expected_version}")
                        stored = context.copy()
                        stored.version = current_version + 1
                        pipe.multi()
                        pipe.setex(key, self._ttl,
                                   json.dumps(stored.to_dict(),
                                              ensure_ascii=False))
                        pipe.execute()
                        return stored.copy()
                    except WatchError:
                        continue
        except ContextConflictError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._policy("save", exc)
            if self._require_shared:
                raise
            return self._fallback.save(context, expected_version=expected_version)
        raise ContextConflictError("save: retries exhausted")

    def delete(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        r = self._client()
        if r is not None:
            try:
                r.delete(self._key(tenant_id, user_id, conversation_id))
                return
            except Exception as exc:  # noqa: BLE001
                self._policy("delete", exc)
                if self._require_shared:
                    raise
        self._fallback.delete(tenant_id, user_id, conversation_id)


# ── 进程单例工厂 ──

_repo: ConversationContextRepository | None = None
_repo_lock = threading.Lock()


def _build_repository() -> ConversationContextRepository:
    from backend.config.conversation_context import (
        CONVERSATION_CONTEXT_BACKEND,
        CONVERSATION_CONTEXT_REQUIRE_SHARED,
    )
    from backend.config.redis import REDIS_ENABLED

    if CONVERSATION_CONTEXT_BACKEND == "memory":
        return MemoryConversationContextRepository()
    if CONVERSATION_CONTEXT_BACKEND == "redis":
        if CONVERSATION_CONTEXT_REQUIRE_SHARED and not REDIS_ENABLED:
            # startup fail-fast（任务书 §八）：生产声明必须 shared 但
            # Redis 未启用 = 配置性缺失，拒绝带病启动（瞬态连不上由
            # 运行时 _policy fail-closed 兜底，不在此列）
            raise RuntimeError(
                "CONVERSATION_CONTEXT_REQUIRE_SHARED=true 但 REDIS_ENABLED=false"
                "——拒绝以进程内 memory 启动（跨 worker 假一致风险）")
        return RedisConversationContextRepository()
    raise ValueError(
        f"CONVERSATION_CONTEXT_BACKEND 非法取值: {CONVERSATION_CONTEXT_BACKEND!r}"
        "（允许 redis | memory）")


def get_conversation_context_repository() -> ConversationContextRepository:
    """进程单例。业务层唯一入口；测试用 reset_* / monkeypatch 替换。"""
    global _repo
    if _repo is None:
        with _repo_lock:
            if _repo is None:
                _repo = _build_repository()
    return _repo


def reset_conversation_context_repository() -> None:
    """测试专用：丢弃单例（下一次 get 重建）。生产禁用。"""
    global _repo
    with _repo_lock:
        _repo = None
