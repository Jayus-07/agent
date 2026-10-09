"""MemoryService — 统一记忆服务入口"""
import asyncio
import time

from langchain_core.messages import SystemMessage
from sqlalchemy.exc import IntegrityError

from backend.config import HISTORY_TOKEN_BUDGET, MEMORY_ORIGIN_INFERRED, PREVIOUS_OUTPUTS_MAX_TOKENS
from backend.context_budget import context_budget
from backend.memory.database import AsyncSessionLocal, get_session
from backend.memory.decay import MemoryDecayService
from backend.memory.importance import ImportanceScorer
from backend.memory.long_term import LongTermMemory, MemoryFact
from backend.memory.pii_filter import scan_and_sanitize
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.repository.session_repo import SessionOwnerMismatch, SessionRepository
from backend.memory.retriever import HybridRetriever
from backend.memory.session import SessionMemory
from backend.memory.short_term import ShortTermBuffer
from backend.memory.token_budget import (
    count_message_tokens,
    count_tokens,
    trim_messages_to_budget,
)
from backend.memory.trigger import MemoryWorthinessClassifier
from backend.observability.metrics import (
    degradation_alerts_total,
    memory_access_mark_failure_total,
    memory_access_mark_total,
    memory_conflict_total,
    memory_explicit_total,
    memory_extraction_candidate_total,
    memory_extraction_rejected_total,
    memory_inferred_total,
    memory_retrieval_failure_total,
    memory_retrieval_latency_seconds,
    memory_retrieval_total,
    memory_store_outcome_total,
    memory_supersede_total,
)
from backend.shared.logger import logger


def _metric_safe(fn, **labels) -> None:
    """metric 埋点兜底：观测面异常绝不反噬业务链路。"""
    try:
        fn(**labels)
    except Exception:  # pragma: no cover - 观测面异常不外泄
        pass


class MemoryService:
    """统一记忆服务。Agent 只能通过此接口访问记忆。"""

    def __init__(self):
        self._sessions: dict[str, SessionMemory] = {}
        self._trigger = None  # lazy init with LLM model
        self._importance = None  # lazy init
        self._decay_service = None  # lazy init with repo

    def _get_trigger(self):
        if self._trigger is None:
            self._trigger = MemoryWorthinessClassifier()
        return self._trigger

    def _get_importance(self):
        if self._importance is None:
            self._importance = ImportanceScorer()
        return self._importance

    # ============================================================
    # Session lifecycle
    # ============================================================

    async def start_session(
        self, session_id: str, user_id: str = "default", query: str = "",
        tenant_id: str = "",
    ) -> ShortTermBuffer:
        from backend.memory.keying import normalize_tenant_id
        tenant_id = normalize_tenant_id(tenant_id)
        async with AsyncSessionLocal() as db_session:
            try:
                srepo = SessionRepository(db_session)

                # Ensure chat_sessions row exists (FK target for chat_messages)
                # STOP C（C4 属主隔离）：session 被其他用户占用时派生隔离
                # 存储键——该用户的所有读写落到自己的键下，互不可见
                try:
                    srow = await srepo.get_or_create(session_id, user_id)
                except SessionOwnerMismatch:
                    original = session_id
                    session_id = self._scoped_session_id(session_id, user_id)
                    logger.warning(
                        "[MemoryService] session %s 属主不匹配（跨用户收养拦截），"
                        "隔离至派生键", original)
                    srow = await srepo.get_or_create(session_id, user_id)

                # L2 → L1（只取最近 SHORT_TERM_MAX_MESSAGES*2 条：
                #  ① 查询层限量，避免长会话全量拉取；② 多取一倍给去重留余量）
                from backend.config import SHORT_TERM_MAX_MESSAGES
                l2 = await SessionMemory.create(session_id, srepo, user_id)
                self._sessions[session_id] = l2

                l1 = ShortTermBuffer()
                history = await l2.load_messages(limit=SHORT_TERM_MAX_MESSAGES * 2)
                # 历史双写修复：旧数据曾因前端+后端各持久化一次，产生连续重复消息。
                # 注入上下文前按 (role, content) 连续去重，避免重复历史挤占窗口、误导模型
                prev = None
                for msg in history:
                    if prev is not None and type(msg) is type(prev) and msg.content == prev.content:
                        continue
                    l1.add(msg)
                    prev = msg

                # L2 摘要注入：超过短消息窗口的更早上下文由会话摘要补足 ——
                # 否则摘要落库后从未参与 prompt，长会话的早期信息完全丢失。
                # 002 迁移后 title/summary 已分字段，summary 即纯 L2 摘要；
                # 保留短文本守卫仅为兼容未回填的旧库（summary 里可能残留旧重命名标题）
                # 角色安全（2026-09-23 P0-2）：摘要源自用户历史，属 untrusted
                # data——只进 AIMessage <historical_context> 数据块，不进
                # SystemMessage（SystemMessage 承载固定 policy 声明）。
                # import 必须无条件（L3 段也用 build_memory_context，
                # 放在 L2 条件分支内会在无摘要时 UnboundLocalError）
                from backend.context_budget.role_safety import (
                    build_historical_context,
                    build_memory_context,
                )
                # L2 数据块长度：L3 注入位置依赖它（顺序 §84：L2 摘要在前、
                # L3 记忆随后、再往后是最近对话）
                l2_block_len = 0
                if srow.summary and len(srow.summary) >= 30:
                    for _i, _m in enumerate(
                            build_historical_context(srow.summary)):
                        l1._messages.insert(_i, _m)
                    l2_block_len = 2
                    logger.info(f"[MemoryService] 注入 L2 会话摘要 (session={session_id}, {len(srow.summary)} 字)")

                # L3 → L1（独立数据库会话 + 显式降级：pgvector/检索异常
                # 只降级不阻断主聊天链 —— 主事务不被 L3 失败污染）
                # STOP D：candidate → SQL eligibility（scope/active/not expired）
                # → relevance gate → rank → merge → max-K → 安全数据上下文
                # （policy SystemMessage + <memory_context> AIMessage 数据块，
                # 记忆原文绝不进 SystemMessage）→ 仅注入条 mark_accessed。
                l3_started = time.perf_counter()
                # STOP G observability：memory.retrieve span 贯穿。无 active
                # trace 时 trace_collector 返回 noop span（软失败不阻断）；
                # span 属性只有 count/threshold 枚举——严禁写入记忆原文/
                # PII/租户标识（§七 trace 红线）。
                from backend.observability.tracer import trace_collector
                retrieve_span = trace_collector.start_span(
                    "memory.retrieve", name="长期记忆检索", type="retrieval")
                try:
                    async with AsyncSessionLocal() as l3_db:
                        l3_repo = MemoryRepository(l3_db)
                        retriever = HybridRetriever(l3_repo)
                        l3 = LongTermMemory(l3_repo)
                        # L3 语义 query：用当前用户问题检索长期记忆（此前误用 session_id，
                        # 召回与当前问题语义无关）；空 query 兜底回退 session_id 保持旧行为
                        l3_query = query or session_id
                        emb = l3.embedding.embed_query(l3_query)
                        retrieved = await retriever.retrieve(l3_query, emb, user_id,
                                                             tenant_id=tenant_id)
                    try:
                        trace_collector.end_span(
                            retrieve_span, metrics=dict(retriever.last_stage_counts))
                    except Exception:  # pragma: no cover - 观测面异常不外泄
                        pass
                    # 阶段计数随缓冲带回：span 由持有 ambient trace 的
                    # runner 侧创建（本协程跑在 memory 后台 loop，无 trace
                    # 上下文，此处创建的 span 是 noop）
                    l1.retrieval_stats = dict(retriever.last_stage_counts)
                    if retrieved:
                        records = [m.record for m in retrieved]
                        # 白名单字段（§34）：不带 tenant/user/UUID/embedding 分数/
                        # source_message_id——模型不需要内部标识
                        entries = [
                            {
                                "memory_type": r.memory_type,
                                "memory_key": r.memory_key or "",
                                "origin": r.origin,
                                "confidence": round(float(r.confidence_score), 2),
                                "content": r.content,
                            }
                            for r in records
                        ]
                        policy_msg, data_msg = build_memory_context(entries)[0:2]
                        # 顺序（§84）：紧跟 L2 摘要块（无摘要时在头部），
                        # 之后才是最近对话
                        _base = l2_block_len
                        l1._messages.insert(_base, policy_msg)
                        l1._messages.insert(_base + 1, data_msg)
                        # mark_accessed（§25/§26/§29）：只有真正注入的记忆才计访问，
                        # 独立短事务 + fail-open（失败不阻断主聊天，仅 metric+log）
                        try:
                            async with AsyncSessionLocal() as mark_db:
                                await MemoryRepository(mark_db).mark_accessed(
                                    [str(r.id) for r in records],
                                    tenant_id=tenant_id, user_id=user_id)
                                await mark_db.commit()
                            _metric_safe(memory_access_mark_total.inc)
                        except Exception as mark_exc:
                            _metric_safe(memory_access_mark_failure_total.inc)
                            logger.warning(
                                f"[MemoryService] mark_accessed 失败（fail-open）"
                                f"(session={session_id}): {mark_exc}")
                        logger.info(
                            f"[MemoryService] 注入 {len(records)} 条长期记忆 "
                            f"(session={session_id}, sources="
                            f"{[m.source for m in retrieved]})")
                    _metric_safe(
                        memory_retrieval_total.labels(
                            status="success", operation="retrieve").inc)
                except Exception as l3_exc:
                    # 降级：记录日志 + metric，主流程继续（不允许 L3 失败打挂聊天）
                    logger.error(
                        f"[MemoryService] L3 检索失败，降级继续 (session={session_id}): {l3_exc}")
                    try:
                        trace_collector.end_span(
                            retrieve_span, status="error",
                            metrics={"error": str(l3_exc)[:100]})
                    except Exception:  # pragma: no cover - 观测面异常不外泄
                        pass
                    _metric_safe(
                        memory_retrieval_total.labels(
                            status="degraded", operation="retrieve").inc)
                    _metric_safe(
                        memory_retrieval_failure_total.labels(
                            operation="retrieve").inc)
                    _metric_safe(
                        degradation_alerts_total.labels(
                            code="MEMORY_L3_RETRIEVAL_FAILED", level="warn").inc)
                finally:
                    try:
                        memory_retrieval_latency_seconds.labels(
                            operation="retrieve"
                        ).observe(time.perf_counter() - l3_started)
                    except Exception:  # pragma: no cover
                        pass

                # Token 预算裁剪（P3）：L1 条数上限（20 条）挡不住单条超长消息，
                # 注入摘要/长期记忆后按 token 整体裁剪，防止挤爆 LLM_CONTEXT_LENGTH
                # 动态预算（2026-09-22，ContextBudgetManager 统一管理）：
                # HISTORY_TOKEN_BUDGET 只是上限，实际预算 = input_budget
                # 减去 System 消息 / 当前问题 / previous_outputs 预留后按剩余
                # 空间动态收缩。只裁 active context，PG 原始消息不受影响。
                _system_tokens = sum(
                    count_message_tokens(m)
                    for m in l1._messages
                    if type(m).__name__ == "SystemMessage"
                )
                _history_budget = context_budget.history_budget(
                    system_tokens=_system_tokens,
                    current_query_tokens=count_tokens(query) if query else 0,
                    reserved_tokens=PREVIOUS_OUTPUTS_MAX_TOKENS,
                )
                _pre_trim = list(l1._messages)
                kept, dropped = trim_messages_to_budget(
                    l1._messages, _history_budget)
                if dropped:
                    l1._messages = kept
                    logger.info(
                        f"[MemoryService] 历史 token 预算裁剪: 丢弃 {dropped} 条旧消息 "
                        f"(budget={_history_budget}, 上限={HISTORY_TOKEN_BUDGET})")
                    # L2 观测闭环（2026-09-22 Phase 2）：metric + SSE context
                    # 事件。本协程跑在 MemoryManager 后台 loop 线程（无 sink），
                    # 事件经进程级缓冲由 runner flush 到 SSE。
                    try:
                        from backend.context_budget.metrics import (
                            emit_context_event,
                            record_compaction,
                        )
                        _before = sum(count_message_tokens(m) for m in _pre_trim)
                        _after = sum(count_message_tokens(m) for m in kept)
                        record_compaction(level="L2", action="history_trim",
                                          before_tokens=_before,
                                          after_tokens=_after)
                        emit_context_event(level="L2", action="history_trim",
                                           before_tokens=_before,
                                           after_tokens=_after,
                                           session_id=session_id)
                    except Exception:
                        logger.debug("L2 观测留痕失败", exc_info=True)

                await db_session.commit()
                return l1
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] start_session 失败: {e}")
                raise

    async def end_turn(self, session_id: str, question: str, answer: str,
                       user_id: str = "default", tenant_id: str = "") -> None:
        # save_turn 之前失败的路径同样要能进入后台 store（provenance 允许缺失，
        # 但不能因 UnboundLocalError 让 end_turn 抛异常）
        user_message_id: int | None = None
        async with AsyncSessionLocal() as db_session:
            try:
                srepo = SessionRepository(db_session)

                # Ensure chat_sessions row exists (may not if start_session was never called)
                # STOP C（C4 属主隔离）：与 start_session 同口径派生隔离键
                try:
                    await srepo.get_or_create(session_id, user_id)
                except SessionOwnerMismatch:
                    session_id = self._scoped_session_id(session_id, user_id)
                    await srepo.get_or_create(session_id, user_id)

                # L2 persistence —— save_turn 返回已落库的 (user_msg, assistant_msg)，
                # user message id 在此确定并作为不可变参数传入后台 store：
                # 禁止在后台协程里"查最新用户消息"取 provenance（并发 turn 会串轮）
                q_msg, _a_msg = await srepo.save_turn(session_id, question, answer)
                user_message_id = q_msg.id if q_msg is not None else None

                await db_session.commit()
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] end_turn 失败: {e}")

        # L2 摘要后台化（STOP E E2/E8）：摘要触发的 LLM 调用（秒级）不得在
        # 请求关闭路径同步等待——稳态长会话会把每次流关闭阻塞至多 5s
        # （MemoryManager._MEMORY_TIMEOUT），超时还会误报 memory_op_failed。
        # save_turn 已提交，摘要读独立会话；失败安全回退（旧摘要保留）。
        asyncio.ensure_future(self._summarize_if_needed(session_id))

        # L3: background write — caller's loop must keep running (Manager handles this)
        asyncio.ensure_future(self.store(question, answer, session_id, user_id,
                                         source_message_id=user_message_id,
                                         tenant_id=tenant_id))

    async def _summarize_if_needed(self, session_id: str) -> None:
        """L2 摘要后台任务（STOP E）。

        触发（既有口径不变）：message_count >= SESSION_MAX_MESSAGES（条数制）。
        滞后门（E2 新增）：水位线之后可摘要增量 >=
        CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES 才调 LLM——条数制触发在稳态
        （每轮 +2 条）下每轮都成立，增量又恰好达到 L5 的 MIN_DELTA=2，
        原实现稳态每轮一次摘要 LLM 调用（每轮重复 summary）。

        失败安全回退：任何异常仅 metric+log，旧摘要与水位线保留，
        绝不影响主聊天链（E-I8）。
        """
        try:
            async with AsyncSessionLocal() as db_session:
                try:
                    srepo = SessionRepository(db_session)
                    if not await srepo.needs_summarization(session_id):
                        return
                    from backend.config import (
                        CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES,
                        CONTEXT_L4_KEEP_RECENT_TURNS,
                    )
                    state = await srepo.get_summary_state(session_id)
                    through = int(state.get("through_id") or 0)
                    boundary = await srepo.summarizable_before_id(
                        session_id, CONTEXT_L4_KEEP_RECENT_TURNS)
                    if not boundary or boundary <= through:
                        return
                    rows = await srepo.load_messages_since(
                        session_id, through, boundary,
                        limit=CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES)
                    if len(rows) < CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES:
                        return  # 攒批：增量不足，等后续轮次凑批
                    l2 = await SessionMemory.create(session_id, srepo)
                    summary = await l2.summarize()
                    if summary is None:
                        # 摘要失败：保留旧摘要并留痕（静默丢失上下文最难排查）
                        try:
                            from backend.observability.metrics import degradation_alerts_total
                            degradation_alerts_total.labels(
                                code="memory_summary_failed", level="warn",
                            ).inc()
                        except Exception:
                            pass
                    elif not getattr(l2, "summary_persisted", False):
                        # 仅 fallback 全量路径需要二次落库（STOP B 2026-10-01）：
                        # 增量路径已随水位线 CAS 原子落库，这里的非 CAS
                        # update_summary 只写正文，并发下会让摘要正文与
                        # 水位线错配——禁止对已持久化结果重复写
                        await srepo.update_summary(session_id, summary)
                    await db_session.commit()
                except Exception as e:
                    await db_session.rollback()
                    logger.error(
                        f"[MemoryService] L2 摘要失败（后台，旧摘要保留）"
                        f"(session={session_id}): {e}")
                    _metric_safe(degradation_alerts_total.labels(
                        code="memory_summary_failed", level="warn").inc())
        except Exception as e:
            # 会话工厂/连接层失败：与 L3 同级降级，不允许反噬主链
            logger.error(
                f"[MemoryService] L2 摘要任务无法打开会话 (session={session_id}): {e}")

    # ============================================================
    # Retrieval
    # ============================================================

    async def search(self, query: str, session_id: str, user_id: str = "default",
                     top_k: int = 5, tenant_id: str = "") -> list[MemoryFact]:
        from backend.memory.keying import normalize_tenant_id
        tenant_id = normalize_tenant_id(tenant_id)
        async with AsyncSessionLocal() as db_session:
            try:
                mrepo = MemoryRepository(db_session)
                l3 = LongTermMemory(mrepo)
                retriever = HybridRetriever(mrepo)
                emb = l3.embedding.embed_query(query)
                # 工具显式搜索（§70/§71）：宽松 gate（enforce_gate=False），
                # 但 SQL eligibility（scope/active/not expired）同样生效；
                # 仅真正返回给 Agent 的记忆 mark_accessed（§31，带 scope）
                retrieved = await retriever.retrieve(query, emb, user_id, top_k=top_k,
                                                     tenant_id=tenant_id,
                                                     enforce_gate=False)
                records = [m.record for m in retrieved]

                if records:
                    await mrepo.mark_accessed([str(r.id) for r in records],
                                              tenant_id=tenant_id, user_id=user_id)
                    await db_session.commit()

                return [
                    MemoryFact(fact_type=r.memory_type, content=r.content, session_id=r.session_id,
                               created_at=str(r.created_at), importance_score=r.importance_score)
                    for r in records
                ]
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] search 失败: {e}")
                return []

    # ============================================================
    # Async store pipeline (background)
    # ============================================================

    async def store(self, question: str, answer: str, session_id: str, user_id: str = "default",
                    source_message_id: int | None = None, tenant_id: str = "") -> None:
        """后台管线: extract → evidence gate → PII → classify → score → conflict resolution → write

        provenance 契约（STOP B）：
          - origin 由本方法强制赋值 inferred（写入通道决定，不信任模型输出）
          - source_message_id 由 end_turn 在 save_turn 时确定后透传，
            本方法禁止回查"最新用户消息"（并发 turn 会串轮）
        版本管理契约（STOP C）：
          - keyed 事实走 conflict 裁决（同 key 同值 reaffirm / 新值 supersede
            或 explicit-blocked）；unkeyed 走语义去重（只判 duplicate）
          - 首建竞态由 partial unique index 兜底，IntegrityError rollback
            后独立事务重试一次（有界，§40）
        注意：本协程运行在 MemoryManager 的后台 event loop 上，
        LLM 同步调用（提取/分类）必须放到线程池，否则会阻塞整个 loop，
        导致同期其他记忆操作（会话持久化等）超时降级。
        """
        from backend.memory.keying import normalize_tenant_id
        tenant_id = normalize_tenant_id(tenant_id)
        async with AsyncSessionLocal() as db_session:
            write_started = time.perf_counter()
            try:
                mrepo = MemoryRepository(db_session)
                l3 = LongTermMemory(mrepo)

                # 1. Extract（LLM 同步调用 → 线程池）；rejections = 证据防线拒绝原因
                facts, rejections = await asyncio.to_thread(l3.extract_facts, question, answer)
                for reason in rejections:
                    _metric_safe(memory_extraction_rejected_total.labels(reason=reason).inc)
                if not facts:
                    return

                stored = 0
                for fact in facts:
                    # 2. Provenance 强制赋值（代码层，非模型层）
                    fact.origin = MEMORY_ORIGIN_INFERRED
                    fact.source_message_id = source_message_id
                    fact.session_id = session_id
                    _metric_safe(memory_extraction_candidate_total.inc)
                    # 3. Classify（可能触发 LLM 同步调用 → 线程池）
                    verdict = await asyncio.to_thread(
                        self._get_trigger().classify, fact.content, fact.fact_type
                    )
                    if verdict == "IGNORE":
                        _metric_safe(memory_extraction_rejected_total.labels(reason="not_worthy").inc)
                        continue
                    # 4. Score
                    fact.importance_score = self._get_importance().score(fact.fact_type, fact.content)
                    if not self._get_importance().should_store(fact.importance_score):
                        _metric_safe(memory_extraction_rejected_total.labels(reason="low_importance").inc)
                        continue
                    # 5. Conflict resolution + write（per-fact 事务：一个 fact 失败不丢同批其他）
                    try:
                        result = await l3.store_with_resolution(fact, user_id, session_id, tenant_id)
                        await db_session.commit()
                    except IntegrityError:
                        # 同 key 首建竞态：unique index 兜底 → rollback 后独立事务重试一次
                        await db_session.rollback()
                        logger.info("[MemoryService] keyed 首建竞态，重试一次 "
                                    f"(user={user_id}, key 已由并发事务创建)")
                        async with AsyncSessionLocal() as retry_db:
                            retry_l3 = LongTermMemory(MemoryRepository(retry_db))
                            result = await retry_l3.store_with_resolution(
                                fact, user_id, session_id, tenant_id)
                            await retry_db.commit()
                    _metric_safe(memory_store_outcome_total.labels(
                        outcome=result.outcome.value).inc)
                    if result.outcome.value == "SUPERSEDED":
                        _metric_safe(memory_supersede_total.inc)
                        _metric_safe(memory_conflict_total.inc)
                    elif result.outcome.value == "CONFLICT_BLOCKED_EXPLICIT":
                        _metric_safe(memory_conflict_total.inc)
                        logger.info(
                            "[MemoryService] inferred 与 active explicit 冲突被阻断 "
                            f"(user={user_id}, source_message_id={source_message_id})")
                    if result.stored:
                        stored += 1
                        _metric_safe(memory_inferred_total.inc)
                    else:
                        _metric_safe(memory_extraction_rejected_total.labels(
                            reason="duplicate").inc)

                if stored:
                    logger.info(
                        f"[MemoryService] 后台写入 {stored}/{len(facts)} 条记忆 "
                        f"(origin=inferred, source_message_id={source_message_id}, "
                        f"rejected={len(rejections)})")
                _metric_safe(
                    memory_retrieval_total.labels(
                        status="success", operation="write").inc)
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] store 失败: {e}")
                _metric_safe(
                    memory_retrieval_total.labels(
                        status="failure", operation="write").inc)
                _metric_safe(
                    memory_retrieval_failure_total.labels(
                        operation="write").inc)
                _metric_safe(
                    memory_extraction_rejected_total.labels(reason="error").inc)
            finally:
                try:
                    memory_retrieval_latency_seconds.labels(
                        operation="write"
                    ).observe(time.perf_counter() - write_started)
                except Exception:  # pragma: no cover
                    pass

    # ============================================================
    # Maintenance
    # ============================================================

    async def update(self, record_id: str, **fields) -> bool:
        async with AsyncSessionLocal() as db_session:
            try:
                mrepo = MemoryRepository(db_session)
                result = await mrepo.update_fields(record_id, **fields)
                await db_session.commit()
                return result
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] update 失败: {e}")
                return False

    async def archive(self, record_id: str) -> bool:
        return await self.update(record_id, is_active=False)

    async def save_messages(self, session_id: str, messages: list[dict], user_id: str = "default") -> dict:
        """批量保存会话消息（供 chat API 调用）。"""
        if not session_id or not messages:
            return {"saved": 0}
        saved = 0
        try:
            async with AsyncSessionLocal() as db:
                repo = SessionRepository(db)
                # STOP C（C4 属主隔离）：与 start/end_turn 同口径
                try:
                    await repo.get_or_create(session_id, user_id)
                except SessionOwnerMismatch:
                    session_id = self._scoped_session_id(session_id, user_id)
                    await repo.get_or_create(session_id, user_id)
                for m in messages:
                    await repo.save_message(session_id, m.get("role", "user"), m.get("content", ""))
                    saved += 1
                await db.commit()
        except Exception as e:
            logger.error(f"[MemoryService] save_messages 失败: {e}")
        return {"saved": saved}

    async def replace_session_messages(
        self, session_id: str, messages: list[dict[str, str]],
        user_id: str = "default",
    ) -> dict:
        """以单事务替换会话快照，供客户端按轮同步时保持幂等。"""
        if not session_id or len(session_id) > 128:
            return {"saved": 0, "error": "会话标识无效"}
        try:
            async with AsyncSessionLocal() as db_session:
                repo = SessionRepository(db_session)
                try:
                    await repo.get_or_create(session_id, user_id)
                except SessionOwnerMismatch:
                    await db_session.rollback()
                    return {"saved": 0, "error": "会话不存在"}
                saved = await repo.replace_messages(session_id, messages)
                await db_session.commit()
                return {"saved": len(saved)}
        except Exception as e:
            logger.error(f"[MemoryService] replace_session_messages 失败: {e}")
            return {"saved": 0, "error": str(e)}

    @staticmethod
    def _scoped_session_id(session_id: str, user_id: str) -> str:
        """跨用户收养拦截后的隔离存储键（确定性：同一用户恒映射同键）。"""
        import hashlib

        digest = hashlib.sha1(str(user_id).encode("utf-8")).hexdigest()[:8]
        return f"{session_id}::u:{digest}"

    async def run_decay(self) -> dict:
        async with AsyncSessionLocal() as db_session:
            mrepo = MemoryRepository(db_session)
            decay = MemoryDecayService(mrepo)
            result = await decay.run()
            await db_session.commit()
            return result

    # ============================================================
    # Session CRUD（供 API 路由使用）
    # ============================================================

    @staticmethod
    async def _check_owner(repo, session_id: str, user_id: str | None) -> str | None:
        """路由面属主校验：user_id 给定时，非属主/不存在都返回统一「会话不存在」。

        返回 None 表示校验通过（user_id=None 为内部调用方直通，不做校验）。
        404 而非 403：不向调用方泄露 session_id 是否真实存在。
        """
        if user_id is None:
            return None
        owner = await repo.get_owner(session_id)
        if owner != user_id:
            return "会话不存在"
        return None

    async def list_sessions(self, user_id: str = "default",
                            limit: int = 50, before: str | None = None) -> dict:
        """列出用户所有会话（支持分页）。

        Args:
            limit: 单次返回上限（50 / 100 / 200 等）
            before: 游标分页 — 仅返回 updated_at < before 的会话
        """
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                sessions = await repo.list_all(user_id=user_id, limit=limit, before=before)
                # 第一页（无 cursor）时返回 total；分页时 total 不变（cost）
                # 前端可据此判断 hasMore = (已显示 < total)
                total: int | None = None
                if before is None:
                    total = await repo.count_sessions(user_id=user_id)
                await db_session.commit()
                return {
                    "sessions": sessions,
                    "total": total if total is not None else len(sessions),
                    "has_more": total is not None and len(sessions) < (total or 0),
                }
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] list_sessions 失败: {e}")
                return {"sessions": [], "total": 0, "error": str(e)}

    async def get_profile(self, user_id: str, tenant_id: str = "",
                          limit: int = 50) -> dict:
        """用户画像：全部 eligible active 长期记忆（设置页只读展示）。

        与 Agent 检索（search_hybrid）走同一 eligibility 口径（is_active +
        (tenant, user) 双维度 + 未过期），但不做语义召回、不更新 access_count
        ——展示读不得污染召回排序的 recency 信号。
        """
        async with AsyncSessionLocal() as db_session:
            try:
                repo = MemoryRepository(db_session)
                records = await repo.list_active_profile(
                    user_id=user_id, tenant_id=tenant_id, limit=limit,
                )
                await db_session.commit()
                return {
                    "records": [
                        {
                            "id": str(r.id),
                            "memory_type": r.memory_type,
                            "content": r.content,
                            "memory_key": r.memory_key,
                            "structured_value": r.structured_value,
                            "origin": r.origin,
                            "importance_score": float(r.importance_score or 0),
                            "confidence_score": float(r.confidence_score or 0),
                            "created_at": r.created_at.isoformat() if r.created_at else None,
                            "last_access_at": (
                                r.last_access_at.isoformat() if r.last_access_at else None
                            ),
                        }
                        for r in records
                    ],
                    "total": len(records),
                }
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] get_profile 失败: {e}")
                return {"records": [], "total": 0, "error": str(e)}

    async def get_session_messages(self, session_id: str, user_id: str | None = None) -> dict:
        """获取会话消息列表。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                denied = await self._check_owner(repo, session_id, user_id)
                if denied:
                    return {"session_id": session_id, "messages": [], "error": denied}
                msgs = await repo.load_messages(session_id)
                await db_session.commit()
                return {
                    "session_id": session_id,
                    "messages": [
                        {"id": m.id, "role": m.role, "content": m.content,
                         "created_at": m.created_at.isoformat()}
                        for m in msgs
                    ],
                }
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] get_session_messages 失败: {e}")
                return {"session_id": session_id, "messages": [], "error": str(e)}

    async def list_profile_memories(self, user_id: str = "default",
                                    limit: int = 50, tenant_id: str = "") -> dict:
        """列出当前用户的画像记忆（memory_records active 行，设置页只读展示）。

        与 /memory/sessions（会话记忆）互补：这里只出长期记忆四类
        （user_fact / preference / decision / knowledge），按最近访问排序。
        只读，不触发 access_count 更新（避免展示行为污染记忆衰减信号）。
        """
        from sqlalchemy import select

        from backend.memory.keying import normalize_tenant_id
        from backend.memory.models.memory import MemoryRecord

        tenant_id = normalize_tenant_id(tenant_id)
        async with AsyncSessionLocal() as db_session:
            try:
                stmt = (
                    select(MemoryRecord)
                    .where(
                        MemoryRecord.user_id == user_id,
                        MemoryRecord.tenant_id == tenant_id,
                        MemoryRecord.is_active.is_(True),
                    )
                    .order_by(
                        MemoryRecord.last_access_at.desc(),
                        MemoryRecord.created_at.desc(),
                    )
                    .limit(max(1, min(limit, 200)))
                )
                rows = (await db_session.execute(stmt)).scalars().all()
                await db_session.commit()
                return {
                    "records": [
                        {
                            "memory_type": r.memory_type,
                            "content": r.content,
                            "created_at": r.created_at.isoformat() if r.created_at else None,
                        }
                        for r in rows
                    ],
                }
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] list_profile_memories 失败: {e}")
                return {"records": [], "error": str(e)}

    async def get_session_context(self, session_id: str, user_id: str | None = None) -> dict:
        """获取会话 Agent 工作上下文。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                denied = await self._check_owner(repo, session_id, user_id)
                if denied:
                    return {"session_id": session_id, "context": None, "error": denied}
                ctx = await repo.get_context(session_id)
                await db_session.commit()
                if ctx:
                    import json as _json
                    try:
                        return {"session_id": session_id, "context": _json.loads(ctx)}
                    except (_json.JSONDecodeError, TypeError, ValueError) as e:
                        logger.debug("会话上下文 JSON 解析失败，回退为 raw: %s", e)
                        return {"session_id": session_id, "context": {"raw": ctx}}
                return {"session_id": session_id, "context": None}
            except Exception as e:
                await db_session.rollback()
                return {"session_id": session_id, "context": None, "error": str(e)}

    async def delete_session(self, session_id: str, user_id: str | None = None) -> dict:
        """删除会话及消息。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                denied = await self._check_owner(repo, session_id, user_id)
                if denied:
                    return {"ok": False, "error": denied}
                ok = await repo.delete(session_id)
                await db_session.commit()
                if ok:
                    logger.info(f"[MemoryService] 已删除会话: {session_id}")
                    return {"ok": True, "session_id": session_id}
                return {"ok": False, "error": "会话不存在"}
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] delete_session 失败: {e}")
                return {"ok": False, "error": str(e)}

    async def rename_session(self, session_id: str, title: str, user_id: str | None = None) -> dict:
        """重命名会话。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                denied = await self._check_owner(repo, session_id, user_id)
                if denied:
                    return {"ok": False, "error": denied}
                ok = await repo.rename(session_id, title)
                await db_session.commit()
                if ok:
                    logger.info(f"[MemoryService] 已重命名: {session_id} → {title}")
                    return {"ok": True, "session_id": session_id, "title": title}
                return {"ok": False, "error": "会话不存在"}
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] rename_session 失败: {e}")
                return {"ok": False, "error": str(e)}
