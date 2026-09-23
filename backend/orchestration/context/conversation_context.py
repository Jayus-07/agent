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

# 漏斗候选跨轮保留上限（2026-09-23 E1）：控制上下文体积，Top-N 即止
FUNNEL_CANDIDATES_MAX = 5

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


def make_travel_run_id(conversation_id: str, seq: int) -> str:
    """travel_run_id 工厂：trv_{会话哈希8}_{seq:03d}。

    会话哈希保证不同 conversation 的 run_id 不碰撞；seq 保证同一会话内
    NEW_RUN 后新旧 run_id 可区分（旧 run 只留摘要，不参与归因）。
    """
    digest = hashlib.sha1((conversation_id or "").encode("utf-8")).hexdigest()[:8]
    return f"trv_{digest}_{int(seq or 0):03d}"


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

    # ── Selection Funnel 候选（2026-09-23 E1）：跨聊天轮的漏斗候选载体 ──
    # 主图 thread_id 每轮唯一（checkpoint 只服务单轮恢复/interrupt），
    # OrchestratorState.funnel_context 不是跨轮载体；漏斗 Top-N 改由
    # ConversationContext 承载（主键三元组 + TTL + 新 run 覆盖）。
    # 只存轻量结构化摘要（Top FUNNEL_CANDIDATES_MAX 条），不存整份 report。
    funnel_candidates: list[dict] = field(default_factory=list)
    funnel_run_id: str = ""
    funnel_candidates_at: float = 0.0

    # ── 路由上下文（2026-09-22 路由入口重构：Context Assembler 数据源）──
    # Router（ContinuationResolver / 粗分类）可读的跨轮任务状态。
    # active_domain 取路由层口径（travel/customer_service/selection_funnel/
    # data/knowledge/...），与粗分类 domain 命名一致。
    active_domain: str = ""
    last_intent: str = ""        # 上一轮路由意图（route_mode / domain_action）
    last_action: str = ""        # 上一轮动作（selected_tool / 域图名 / clarify）
    pending_question: str = ""   # 上一轮留给用户的待答问题（追问卡/澄清/待决项）

    # ── Travel Run（STOP F1，2026-09-23）──
    # 一次旅游规划任务的身份与结构化 pending。只存结构化事实与摘要，
    # 不存 itinerary 大对象（域图 checkpointer 才是权威执行状态——
    # 职责冻结见 docs/2026-09-23-TravelResume-STOPF0-审计与设计.md §三）。
    travel_run_seq: int = 0      # 会话内 run 序号（0 = 无活跃 run）
    travel_run_id: str = ""      # trv_{hash8}_{seq:03d}
    travel_stage: str = ""       # slot / planned / completed / cancelled
    travel_pending: dict | None = None   # TravelPendingQuestion 快照（见下）

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

    # ── 路由上下文维护（2026-09-22 路由入口重构）──

    def mark_turn(self, *, domain: str, intent: str = "",
                  action: str = "", pending_question: str | None = None) -> None:
        """记录一轮路由结果，供下一轮 Context Assembler / ContinuationResolver 消费。

        pending_question 传 None 表示「本轮没有产生新追问」，不清空旧值
        （追问往往跨轮存活，如旅游域待决项）；显式传空串表示清除。
        """
        if domain:
            self.active_domain = domain
        if intent:
            self.last_intent = intent
        if action:
            self.last_action = action
        if pending_question is not None:
            self.pending_question = pending_question
        self.updated_at = time.time()

    # ── Travel Run 维护（STOP F1）──

    def begin_travel_run(self) -> str:
        """开启新 run（seq+1）并返回 run_id；旧 pending 一并失效。

        仅在「首次规划」与「用户显式重新规划（NEW_RUN）」时调用——
        普通补槽/局部修改/约束变化（CONTINUE/PATCH/REPLAN）不得换 run。
        """
        self.travel_run_seq = int(self.travel_run_seq or 0) + 1
        self.travel_run_id = make_travel_run_id(self.conversation_id,
                                                self.travel_run_seq)
        self.travel_pending = None
        self.updated_at = time.time()
        return self.travel_run_id

    def set_travel_stage(self, stage: str) -> None:
        """更新 run 阶段（slot / planned / completed / cancelled）。"""
        if stage:
            self.travel_stage = stage
            self.updated_at = time.time()

    def set_travel_pending(self, pending: dict | None) -> None:
        """写入/清除结构化 pending question（None = 清除）。"""
        self.travel_pending = dict(pending) if pending else None
        self.updated_at = time.time()

    def clear_travel_run(self) -> None:
        """取消/收尾 run：清 run 身份与 pending，保留摘要槽位（任务书 §19）。

        槽位摘要（destination/days/…）是用户已确认的事实，保留供后续
        「再规划一个」时 slot_filler 预填参考；run 身份与 pending 必须
        清掉，否则 completed 后普通问题仍被 travel pending 拦截（T15）。
        """
        self.travel_run_seq = 0
        self.travel_run_id = ""
        self.travel_stage = ""
        self.travel_pending = None
        self.updated_at = time.time()

    # ── Selection Funnel 候选（2026-09-23 E1）──

    def set_funnel_candidates(self, candidates: list[dict], run_id: str) -> None:
        """记录一次成功漏斗运行的 Top-N 候选（新 run 覆盖旧候选）。

        只保留轻量结构化摘要（cap=FUNNEL_CANDIDATES_MAX），并为每条补充
        rank/funnel_run_id/generated_at 便于下一轮 selection_decision 归因。
        """
        kept = []
        for i, cand in enumerate((candidates or [])[:FUNNEL_CANDIDATES_MAX]):
            if not isinstance(cand, dict):
                continue
            entry = dict(cand)
            entry.setdefault("rank", i + 1)
            entry["funnel_run_id"] = run_id
            entry["generated_at"] = time.time()
            kept.append(entry)
        self.funnel_candidates = kept
        self.funnel_run_id = run_id if kept else ""
        if not kept:
            self.funnel_candidates_at = 0.0
        else:
            self.funnel_candidates_at = time.time()
        self.updated_at = time.time()

    def clear_funnel_candidates(self) -> None:
        """显式清除候选（会话 reset / 主题切换时由调用方触发）。"""
        self.funnel_candidates = []
        self.funnel_run_id = ""
        self.funnel_candidates_at = 0.0
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
            "active_domain": self.active_domain,
            "last_intent": self.last_intent,
            "last_action": self.last_action,
            "pending_question": self.pending_question,
            # Travel Run 摘要（STOP F1）：pending 结构化快照供路由层
            # TravelPendingResolver 与 trace 消费
            "travel_run_id": self.travel_run_id,
            "travel_stage": self.travel_stage,
            "travel_pending": dict(self.travel_pending) if self.travel_pending else None,
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


def sync_travel_run_to_context(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    *,
    brief: dict,
    missing_slots: list[str],
    new_run: bool = False,
) -> str | None:
    """旅游域图执行后的 run/pending/stage 结构化同步（STOP F1/F2）。

    与 sync_travel_brief_to_context 的分工：那个只同步槽位摘要；本函数
    维护 TravelRun 身份（首次/NEW_RUN 时 seq+1）、阶段标记与结构化
    pending（TravelPendingQuestion 快照）——后者是下一轮路由层
    TravelPendingResolver 的判定输入（G3/G4 的数据基础）。软失败。

    Returns:
        本次同步后的 run_id（异常/不可同步时 None）。
    """
    try:
        if not conversation_id:
            return None
        store = get_conversation_context_store()
        ctx = store.get(tenant_id or "", user_id or "", conversation_id)
        # 摘要槽位先同步（begin_travel_run 不依赖槽位，但 stage 判定依赖）
        sync_travel_brief_to_context(tenant_id, user_id, conversation_id,
                                     brief=brief)
        missing = [s for s in (missing_slots or []) if s]
        # run 身份：首次（无活跃 run）或显式 NEW_RUN 才换；普通补槽/
        # 改约束（CONTINUE/PATCH/REPLAN）保持同 run（任务书 §7）
        if new_run or not ctx.travel_run_id:
            run_id = ctx.begin_travel_run()
        else:
            run_id = ctx.travel_run_id
        # 阶段与 pending：必填槽缺失 = slot 阶段 + 结构化追问；齐备 = 规划
        # 中（completed 由 reporter 收尾轮的 finished 判定，见调用方）。
        if missing:
            ctx.set_travel_stage("slot")
            prev = ctx.travel_pending or {}
            if prev.get("requested_slots") == missing and prev.get("run_id") == run_id:
                prev["question_id"] = prev.get("question_id") or _new_question_id()
                ctx.set_travel_pending(prev)  # 同一轮追问：保留 question_id
            else:
                ctx.set_travel_pending({
                    "question_id": _new_question_id(),
                    "run_id": run_id,
                    "requested_slots": missing,
                    "reason": "missing_required",
                    "created_at": time.time(),
                })
        else:
            ctx.set_travel_pending(None)
            if ctx.travel_stage in ("", "slot"):
                ctx.set_travel_stage("planned")
        logger.debug(
            "[ConversationContext] travel run 同步: run=%s stage=%s missing=%s",
            run_id, ctx.travel_stage, missing,
        )
        return run_id
    except Exception as exc:  # noqa: BLE001 — 上下文同步失败绝不影响主链
        logger.warning("[ConversationContext] travel run 同步失败（软降级）: %s", exc)
        return None


def mark_travel_run_completed(
    tenant_id: str, user_id: str, conversation_id: str,
) -> None:
    """行程成功出单后收尾：stage=completed + 清 pending（STOP F2，T15）。

    run_id 与摘要槽位保留（历史归因/后续参考）；pending 必须清掉，否则
    下一轮普通问题仍被 travel pending 拦截。软失败。
    """
    try:
        if not conversation_id:
            return
        ctx = get_conversation_context_store().get(
            tenant_id or "", user_id or "", conversation_id)
        ctx.set_travel_stage("completed")
        ctx.set_travel_pending(None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ConversationContext] travel 收尾失败（软降级）: %s", exc)


def _new_question_id() -> str:
    from uuid import uuid4

    return f"tq_{uuid4().hex[:8]}"


def sync_funnel_candidates_to_context(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    top: list[dict],
    run_id: str,
) -> None:
    """成功漏斗运行的 Top-N 候选 → ConversationContext（2026-09-23 E1）。

    写入时机契约：只允许在候选最终确定（漏斗成功产出）后调用——pool/
    screen/verify 中途状态不得写入，否则失败的漏斗 run 会污染下一轮。
    新 run 覆盖旧候选（覆盖即失效）。软失败，绝不影响主链。
    """
    try:
        if not conversation_id or not top:
            return
        store = get_conversation_context_store()
        ctx = store.get(tenant_id, user_id, conversation_id)
        ctx.set_funnel_candidates(top, run_id)
        logger.debug(
            "[ConversationContext] funnel 候选同步: run=%s n=%d conv=%s",
            run_id, len(ctx.funnel_candidates), conversation_id,
        )
    except Exception as exc:  # noqa: BLE001 — 上下文同步失败绝不影响主链
        logger.warning("[ConversationContext] funnel 候选同步失败（软降级）: %s", exc)


def read_funnel_candidates_from_context(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
) -> list[dict]:
    """读取上一轮漏斗候选（只读 peek；未命中/过期返回空 list）。

    读取优先级契约（E1）：显式请求候选 > 当前 graph state funnel_context >
    本函数（ConversationContext）> 无候选 → need_info。
    """
    try:
        if not conversation_id:
            return []
        ctx = get_conversation_context_store().peek(
            tenant_id or "", user_id or "", conversation_id)
        if ctx is None:
            return []
        return list(ctx.funnel_candidates or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ConversationContext] funnel 候选读取失败（软降级）: %s", exc)
        return []
